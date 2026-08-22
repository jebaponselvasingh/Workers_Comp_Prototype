"""The audit log's schema owner (AD-4, AD-11) — first written by Story 2.3.

AD-4 splits audit into two responsibilities and this module is the first
half: `services/audit` **owns the shape** of an audit event and, later, its
retention and redaction (AD-11's purge cascade, Story 8.1); the *writing*
happens inside whichever command performed the mutation, in that command's
own transaction. Hence `record` takes a session it does not own and never
commits — an audit row that could be committed separately from the change it
describes is exactly the failure "in the same transaction" is about.

**Why a helper at all, rather than `db.add(AuditEvent(...))` at each call
site.** The schema is fixed by AD-4 — `(id, at, actor_id, actor_role,
action, entity, entity_id, before, after)` — and "fixed" only survives
contact with twenty commands if there is one place that spells it. The
actor is taken from the caller context rather than passed, so a command
cannot name a different actor from the one whose scope it just ran under.

**AD-11: diffs are PHI.** `before`/`after` carry only the fields the command
actually wrote — never a whole row, and never a value that was not part of
the change. The structlog line this module emits carries the action, the
entity id and *which keys* changed; it never carries a value.
"""

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final, Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models import AuditEvent

log = structlog.get_logger()

#: What a human decided about an AI-proposed write (Story 6.5, AD-6).
#:
#: The three decisions `HumanInTheLoopMiddleware` is configured with. The
#: vendor's enum has a fourth, `respond`, which AD-6 names as unused in v1 and
#: which is therefore deliberately absent here: a member with no producer would
#: be a vocabulary for an outcome this build cannot reach.
ApprovalOutcome = Literal["approved", "edited", "rejected"]

#: The `action` prefix every copilot-approval row carries.
#:
#: **The outcome is encoded in `action`, never in `after`**, and that is the one
#: decision in this function worth arguing. `before`/`after` are the PHI-bearing
#: diff columns, and AD-11's purge cascade grants the `audit_redactor` role
#: UPDATE on exactly those two — so a purge preserves the who/what/when skeleton
#: and overwrites the diffs. An outcome stored in `after` would therefore be
#: *erasable by design*: after a purge the row would say a handler decided
#: something about a claim, and no longer say what. `action` is part of the
#: skeleton, so `copilot_approval.rejected` survives redaction, which is what
#: "the reject branch's audit row is the only thing it persists" has to mean if
#: it is to mean anything seven years later.
#:
#: `ai_insight.generated` is the precedent for a dotted `action` naming a family
#: and a member rather than a family plus a payload.
COPILOT_APPROVAL_PREFIX: Final[str] = "copilot_approval"

#: The entity a copilot approval is recorded against.
#:
#: The **claim**, not the write's target row, and not a synthetic
#: "copilot_approval" entity kind. Two reasons, and both are about what a reader
#: of the log can do with the row. The claim is the thing a compliance question
#: is ever asked about ("who let the AI touch this file?"), and it is what Story
#: 8.1's purge cascade finds rows by — a content-free row has no diff to carry a
#: `claim_id` in the way `injuries._diff` does, so the claim has to *be* the
#: entity or the row is unreachable from the claim it concerns. A rejected
#: proposal, moreover, has no target row: nothing was written, so there is no id
#: to name.
COPILOT_APPROVAL_ENTITY: Final[str] = "claim"


async def record(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    action: str,
    entity: str,
    entity_id: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    at: datetime | None = None,
) -> AuditEvent:
    """Add one AD-4 audit row to the caller's transaction. Does not commit.

    `at` is a parameter with a UTC default rather than a database default,
    for the reason the column has no `server_default`: the event happened
    when the command decided it happened, and a command that emits two rows
    for one mutation should be able to stamp them identically.
    """
    event = AuditEvent(
        at=at or datetime.now(UTC),
        actor_id=ctx.user_id,
        actor_role=ctx.role,
        action=action,
        entity=entity,
        entity_id=entity_id,
        before=dict(before) if before is not None else None,
        after=dict(after) if after is not None else None,
    )
    db.add(event)
    # Keys, never values (AD-11). "Which fields did this actor change on
    # which claim" is an operational question; what they changed them *to*
    # is claim data and lives only in the row above.
    log.info(
        "audit.recorded",
        action=action,
        entity=entity,
        entity_id=entity_id,
        actor_id=ctx.user_id,
        fields=sorted(after) if after else [],
    )
    return event


async def record_copilot_approval(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    outcome: ApprovalOutcome,
    claim_business_id: str,
    at: datetime | None = None,
) -> AuditEvent:
    """Record that a human decided about an AI-proposed write — **content-free**.

    AD-6's `record_copilot_approval`, and the only persistence permitted on the
    reject branch. All three decisions produce exactly one of these rows, so
    "the copilot proposed something and a person answered" is a fact with a
    seven-year retention floor behind it whether or not anything was written.

    **Nothing about the proposal is in the row.** No tool name, no drafted
    field, no letter, no `before`/`after` at all — the diff columns are `None`
    rather than empty objects, which is the honest shape for an event whose
    whole content is that a decision happened. What the row names is the actor
    (from `ctx`, like every other audit row), the claim, the timestamp and which
    of the three decisions it was; see `COPILOT_APPROVAL_PREFIX` on why the last
    of those lives in `action`.

    Note what this row is *not*: it is not the audit of the write. An approved
    write goes on to run its own AD-4 command, which emits its own event in its
    own transaction with its own diff. Two rows, because two things happened — a
    person approved, and then a claim changed — and the second may still fail
    stale after the first is durable. That is the correct order: a decision that
    was made is a fact even when the write it authorized lost a race.

    **This commits, unlike `record` above**, and the asymmetry is deliberate.
    Every other caller of `record` is a command that owns its transaction and
    emits the audit row inside it; this one has no such command to ride along
    with. `update_claim_fields` owns its transaction and its docstring forbids a
    composite wrapping it — that would hand the composite authority to discard
    the caller's pending writes — so the approval row cannot share the write's
    transaction and must be its own. It is written *before* the write runs, so a
    write that then fails still leaves the decision recorded.
    """
    event = await record(
        db,
        ctx,
        action=f"{COPILOT_APPROVAL_PREFIX}.{outcome}",
        entity=COPILOT_APPROVAL_ENTITY,
        entity_id=claim_business_id,
        before=None,
        after=None,
        at=at,
    )
    await db.commit()
    return event


#: The `action` prefix every export row carries (Story 7.5, AD-4, NFR-5).
#:
#: `COPILOT_APPROVAL_PREFIX`' arrangement, and its argument applies unchanged one
#: family over: the **format is encoded in `action`** as well as in `after`, and
#: that deliberate duplication is the one decision here worth arguing. `before`
#: and `after` are the PHI-bearing diff columns, and AD-11's purge cascade grants
#: the `audit_redactor` role UPDATE on exactly those two — so a purge preserves
#: the who/what/when skeleton and overwrites the diffs. A format stored only in
#: `after` would therefore be *erasable by design*: after a purge the row would
#: say an analyst exported something, and no longer say whether a spreadsheet or
#: a CSV of it exists on somebody's laptop. `action` is part of the skeleton, so
#: `export.xlsx` survives redaction — which is what "the audit trail is the
#: record" has to mean if it is to mean anything seven years later.
#:
#: `copilot_approval.rejected` and `ai_insight.generated` are the precedent for a
#: dotted `action` naming a family and a member rather than a family plus a
#: payload.
EXPORT_PREFIX: Final[str] = "export"


#: How many hex characters of the filter digest reach `entity_id` — see above.
_DIGEST_LENGTH: Final[int] = 16


def _filter_digest(filters: Mapping[str, str]) -> str:
    """A stable 16-hex fingerprint of one filter set. Content-free by construction.

    **Why a digest rather than the filter set alone.** The set itself is in
    `after`, which AD-11's purge cascade overwrites; `entity_id` is part of the
    skeleton and survives. "The same slice was exported three times last week" is
    a question a compliance reader asks about *redacted* rows, and without a
    stable id in the skeleton there is nothing left to group them by — every row
    would read as an export of something, of nothing in particular.

    Canonical by sorted key, so two requests that set the same facets in a
    different order produce the same id: a query string is unordered and an
    `entity_id` that disagreed about that would defeat the only question it
    exists to answer.

    **The canonical form is JSON, and the separators it replaced are the reason.**
    The first version joined `key\\x1f value` pairs with `\\x1e`, on the argument
    that `=`/`&` could not be used because a facet value may contain either. That
    argument was right and the fix was not: it only moved the collision to two
    rarer characters. Both are reachable — `filter[injuryType]=A%1Fb` decodes to a
    value holding `\\x1f`, and the free-text facets (`injuryType`, `state`,
    `sector`, `region`, `icd10`) have no vocabulary anything validates — so two
    genuinely different filter sets could share one digest and a compliance
    reader grouping redacted rows by it would be told two slices were one.
    `json.dumps` over a sorted dict has no such character: every delimiter it
    emits is one the encoder escapes inside a value, so the encoding is
    *injective* by construction rather than by hoping the separator is exotic
    enough. `ensure_ascii=True` pins the bytes a non-ASCII value hashes to, so a
    digest does not move if the interpreter's default ever does; `separators`
    drops the whitespace, which is not about size but about there being exactly
    one canonical form.

    Truncated to sixteen hex characters: `entity_id` is a display-length column
    shared with claim business ids, and 64 bits is far beyond what
    "did these two rows describe the same slice" needs. This is a grouping key
    and never a security token — nothing is authenticated by it, so collision
    resistance rather than preimage resistance is the whole requirement.

    **The values that go in are query terms, not claim values.** An employer id,
    an ICD-10 code, a severity band, a date bound. `record_export` says so at
    length and `tests/test_dataset_export.py` proves it by scanning the
    serialised payload for a seeded worker's name rather than by checking key
    names.
    """
    canonical = json.dumps(
        {key: filters[key] for key in sorted(filters)},
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]


async def record_export(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    target: str,
    fmt: str,
    rows: int,
    filters: Mapping[str, str],
    at: datetime | None = None,
) -> AuditEvent:
    """Record that a table left the system — **content-free** (AC 2, AD-4, AD-11).

    An export is the one read on this console that moves PHI out of it, which is
    why it is the one read that writes an audit row at all. Every other dashboard
    surface is a query, and `test_reading_the_financial_section_writes_no_audit_
    event` holds that line one story over; this function is the exception that
    story's docstring predicted.

    **Nothing about the file is in the row.** No claim id, no worker name, no
    employer label, no cell, no byte count — the row names the actor (from `ctx`,
    like every other audit row), what was exported, in what shape, how big it
    was, and under which filter set. `before` is `None` rather than an empty
    object, which is the honest shape for an event that changed nothing:
    `record_copilot_approval` sets both to `None` for the same reason, and this
    one carries an `after` only because "how big" and "of what" are facts about
    the event rather than about a claim. `services/rag/insights.py` writes the
    identical kind of `after` and calls it "provenance, not content".

    **`filters` is every parameter that changed what was exported** — the active
    `filter[…]` facets *and* the surface's own controls (`groupBy`, `grain`,
    `anchor`, `cohort`, `from`, `to`, `sort[…]`) — keyed by the name the wire
    uses and valued as the wire carried it, with the unset omitted. The wire
    spelling rather than the Python field name, for `WIRE_KEYS`' reason: the row
    has to be readable beside the request that produced it, and a reader
    reconstructing "what did they actually ask for" should be able to paste it
    into a URL. That is a claim about the **whole** key and not about its stem,
    so a facet is `filter[severityBand]` rather than `severityBand` — the bare
    form is a chip's name and no route accepts it, and it also read as a
    different convention from the controls sitting beside it in the same mapping.
    `export._facet_terms` composes the brackets and `tests/test_dataset_export.py`
    pins the spelling. The controls are in there beside the facets because a
    breakdown grouped by ICD-10 and one grouped by employer are two different
    files from one filter set, and a row that recorded only the facets would say
    they were the same export.

    **What is deliberately not in there is the table.** `entity` already carries
    the `ExportTarget`, so a `table=breakdown` term would be the same fact spelled
    twice — and spelled *worse* the second time, since `entity` is skeleton and
    survives AD-11's redaction while `after` does not.

    **They are query terms and never claim values.** An employer id, an ICD-10
    code, a severity band, a date bound — the vocabulary of a URL, all of it
    already in the caller's address bar and none of it read out of a claim row.
    The distinction is what makes a content-free event possible at all, and it is
    checked by scanning the serialised payload for a seeded worker's name rather
    than by inspecting key names.

    **`entity` is the target and not a claim, which departs from
    `COPILOT_APPROVAL_ENTITY` — deliberately.** That constant argues at length
    that a content-free row must name the *claim*, because a row with no diff has
    nowhere else to carry a `claim_id` and would otherwise be "unreachable from
    the claim it concerns"; it names Story 8.1's purge cascade, which finds rows
    by the claim they belong to. The argument does not transfer, and the reason is
    arithmetic rather than taste: a copilot approval is about exactly one claim,
    and an export is about **a set** — a hundred claims under a segmentation, or
    a distribution over a whole book, or (for an emptied segment) none at all.
    There is no single claim to name, and naming one would be worse than naming
    none: it would assert a relationship the event does not have, and a purge
    keyed on it would erase an egress record because one of the hundred claims it
    counted was deleted. So `entity` carries what the row is actually about —
    which table left — and `entity_id` carries the slice digest, and neither is a
    claim id.

    **The consequence is real and is recorded rather than argued away.** An
    export row is not reachable from the claims it exported: a Story 8.1 purge of
    one claim will not find, redact or cascade to the export rows whose files
    contained it, because nothing on this row says it did. That is unavoidable
    without putting a claim id list in `after`, which is exactly the content the
    AC and AD-11 forbid — a hundred claim ids *is* the file's index. What a purge
    can still do is what the skeleton supports: find every export by actor, by
    target, by day and by slice digest. The gap is entered in
    `deferred-work.md` against Story 8.1 so that the retention story decides it
    rather than discovers it.

    **This commits, like `record_copilot_approval` and unlike `record`.** The
    asymmetry is that function's, restated: every other caller of `record` is a
    command that owns its transaction and emits the row inside it; an export has
    no mutating command to ride along with. It is the router-write guard
    (`test_no_router_writes_through_a_session`) that decides *where* the commit
    goes — a route may not commit — and this module that decides it may commit at
    all.

    **It is called after the table is materialised and before the first byte
    leaves.** That ordering is the whole of AC 3 and is enforced by
    `services/worklist/export.py` rather than here: `rows` has to be a real count
    rather than an estimate, which means the table must already exist; and the
    row has to be durable before the response starts, because a `StreamingResponse`
    body iterator runs after the request-scoped session has closed and would have
    no transaction to write in. The residual is that an export failing *during*
    transmission is over-recorded rather than unrecorded, which is the direction
    an egress log should err in.
    """
    event = await record(
        db,
        ctx,
        action=f"{EXPORT_PREFIX}.{fmt}",
        entity=target,
        entity_id=_filter_digest(filters),
        before=None,
        after={
            "target": target,
            "format": fmt,
            "rows": rows,
            # A copy rather than the caller's mapping: `record` stores what it is
            # handed, and a route's parameter dict outliving the request is one
            # mutation away from an audit row that no longer says what was asked.
            "filters": dict(filters),
        },
        at=at,
    )
    await db.commit()
    return event
