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
