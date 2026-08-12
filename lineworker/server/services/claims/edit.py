"""The audited inline edit — this build's first AD-4 write path (Story 2.3).

Every later mutation copies the shape of this module: a PATCH-shaped command
carrying only the fields that changed, compare-and-swapped on the caller's
`expected_version`, emitting an audit event and a timeline event **in the
same transaction** as the change itself. Epic 3's financial commands, Epic
4's diary and meeting writes and Epic 6's approved copilot writes all land on
this pattern, so the decisions below are made once here rather than
re-litigated per story.

## The five refusals, and the order they are checked in

1. **Role.** Handlers edit; supervisors and analysts read (BR-ROLE). Checked
   **first, before the claim is even looked up**, and that order is a
   security property rather than a style choice: if scope were checked first,
   a supervisor would get 404 for a claim outside their book and 403 for one
   inside it, which is precisely the existence oracle
   `select_claim_detail`'s single-answer rule exists to close. Role first
   means every non-handler gets 403 for every claim id, and learns nothing.
2. **Scope.** `select_claim_detail` answers `None` for "not yours" and for
   "does not exist" alike; both become `ClaimNotVisible`, hence one 404.
3. **The patch itself** — unknown key, wrong type, empty string, an
   unparseable ICD-10, a body key or recovery window outside the vocabulary.
   Checked before the version, because a 422 tells the caller something
   actionable about the request they wrote, while a 409 tells them to
   re-read and try again; when both are true the first is the more useful
   answer and the second will still be there afterwards.
4. **The version.** A pre-check so a *no-op* patch on a stale version still
   conflicts (there is no UPDATE for the compare-and-swap to fail on), and
   the compare-and-swap in the UPDATE itself as the authoritative guard
   against the read→write race the pre-check cannot see.
5. **The write.** `WHERE id = ? AND version = ?`, increment on success. Zero
   rows changed means somebody else got there first: the command re-reads
   and raises `StaleClaim` carrying the **fresh entity**, which is what lets
   the SPA render the current value inline without a second round trip
   (AD-9).

## What it writes, and what it refuses to write

The whitelist is the six fields on the prototype's investigation injury card
plus `icd_desc`, and it is a fixed tuple rather than "any column on the
model". `severity_score` is not on it — Story 2.4 owns the score and writes
it through `update_claim_severity` further down this module, because the
whitelist machinery below is built for text and an integer threaded through
it would make five things conditional to save twenty lines. Nor is any
financial column (Epic 3), nor `stage` (Story 3.5's approvals), nor
`version` itself.

`body_part` is the one column written that a caller cannot name. Picking a
body **key** rewrites the label from `BODY_PART_LABELS`, exactly as the
prototype's `updateBodyPart` does — see `reference.py` for why the two
vocabularies differ. It rides in the audit diff, because the diff describes
what the command *wrote* and a diff that omitted it would be an incomplete
record of the change.

**Unchanged fields are dropped.** A patch whose values all equal the stored
ones writes nothing, bumps no version, and emits neither an audit nor a
timeline row — the log is a record of changes, and "a handler tabbed through
the card without typing" is not one. The response is still the fresh entity,
so the client cannot tell the difference and does not need to.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models.core import Claim
from data.models.enums import Disability, RecoveryWindow, TimelineTag, UserRole
from data.repositories import claims as claim_repo
from services import audit
from services.claims import timeline
from services.claims.detail import ClaimDetail, ClaimNotVisible, claim_detail
from services.claims.reference import (
    BODY_PART_LABELS,
    FIELD_LABELS,
    ICD_PATTERN,
    SEVERITY_MAX,
    SEVERITY_MIN,
)

#: The patch keys this command accepts, in the order the injury card renders
#: them — which is also the order the timeline description lists them in, so
#: two handlers editing the same two fields produce the same sentence.
EDITABLE_FIELDS: Final[tuple[str, ...]] = (
    "injury_type",
    "cause",
    "body_key",
    "icd",
    "icd_desc",
    "disability",
    "recovery",
)

#: Caps on the free-text fields. Generous against the seeded data (the
#: longest values are 29, 37 and 68 characters) and present so that a paste
#: accident cannot put a kilobyte of prose in a column the case header
#: renders on one line.
MAX_LENGTHS: Final[Mapping[str, int]] = {
    "injury_type": 80,
    "cause": 120,
    "icd": 12,
    "icd_desc": 200,
}

#: The AD-4 `action` — the command's own name, so an audit row read months
#: later names the function that wrote it.
ACTION: Final[str] = "update_claim_fields"
ENTITY: Final[str] = "claim"


class EditNotPermitted(PermissionError):
    """The caller's role does not carry the edit capability (AD-7).

    Role gates capability, scope gates visibility. This is the capability
    half, and it is raised without reference to any claim — see the module
    docstring on why that ordering matters.
    """


class InvalidPatch(ValueError):
    """The patch is not something this command can apply — a 422.

    **The message names fields, never values** (AD-11). "icd is not a valid
    ICD-10 code" is safe to put in a problem document and useful to a
    handler; echoing what they typed would put claim data in an error body,
    a log line and, eventually, a browser console.
    """


class StaleClaim(Exception):
    """Somebody else changed the claim first — a 409 carrying fresh state.

    The fresh entity travels *with* the exception rather than being fetched
    again by the router, so the 409 body can carry it (the Write-concurrency
    convention) and the SPA can render the current value at the edited field
    without a second request.
    """

    def __init__(self, fresh: ClaimDetail) -> None:
        super().__init__(f"{fresh.claim_id} has moved on from the version you were editing")
        self.fresh = fresh


@dataclass(frozen=True)
class _Change:
    """One column the command will write, with the value it replaces."""

    column: str
    before: str
    after: str


class EditableClaim(Protocol):
    """The columns the diff reads — the whole of what `_changes` needs.

    A `Protocol` rather than `Claim` itself because that is what the function
    actually depends on: eight strings. It keeps the diff testable without
    constructing a row that has forty unrelated NOT NULL columns, and it
    documents the read surface at the same time — a new editable field has
    to be added here, which is one more place the whitelist is stated.
    """

    injury_type: str
    cause: str
    body_key: str
    body_part: str
    icd: str
    icd_desc: str
    disability: Disability
    recovery: RecoveryWindow


def require_text(field: str, raw: object) -> str:
    """A trimmed, non-empty, control-character-free, length-capped string.

    **Public since Story 2.4**, which validates a secondary injury's type the
    same way (`services/claims/injuries.py`). `field` indexes `FIELD_LABELS`
    and `MAX_LENGTHS`, so a caller adds its field to both rather than passing
    a label and a limit — which is what keeps "one field, one wording, one
    cap" true across two commands.
    """
    if not isinstance(raw, str):
        raise InvalidPatch(f"{FIELD_LABELS[field]} must be text")
    value = raw.strip()
    if not value:
        raise InvalidPatch(f"{FIELD_LABELS[field]} cannot be empty")
    # PostgreSQL `text` cannot hold a NUL, and asyncpg raises rather than
    # truncating — which reached the request as an unhandled 500 rather than
    # a 422 (code review, verified against a real database). The rest of the
    # C0 range and DEL are refused with it: none of them means anything in a
    # clinical field, and all of them corrupt a log line or a CSV export
    # downstream.
    if any(character < " " or character == "\x7f" for character in value):
        raise InvalidPatch(f"{FIELD_LABELS[field]} cannot contain control characters")
    limit = MAX_LENGTHS.get(field)
    if limit is not None and len(value) > limit:
        raise InvalidPatch(f"{FIELD_LABELS[field]} must be {limit} characters or fewer")
    return value


def normalise(patch: Mapping[str, Any]) -> dict[str, str]:
    """Whitelist, type-check and canonicalise a patch, or raise `InvalidPatch`.

    Separate from the command and exported, because the API layer's Pydantic
    model is a *convenience* — it produces a friendlier 422 for a browser —
    and not the enforcement. An agent tool (AD-13) calls the command
    directly, so the command has to be safe on its own.
    """
    if not patch:
        raise InvalidPatch("the patch names no fields")
    if not set(patch) <= set(EDITABLE_FIELDS):
        # **The rejected keys are deliberately not named** (AD-11, code
        # review). They are caller-supplied strings, and this command is
        # reachable directly from an AD-13 agent tool whose arguments are
        # untrusted content (AD-16) — echoing them puts attacker-chosen text
        # into a problem document and a log line. The *allowed* set is a
        # constant, so naming that instead is both safe and more useful.
        raise InvalidPatch(f"editable fields are: {', '.join(EDITABLE_FIELDS)}")

    # **An explicit `null` is refused here rather than at the API layer**
    # (code review, 2026-08-12). It used to be refused in
    # `ClaimFieldPatch.edited_fields()`, which runs while the router is
    # *building* the call — so it fired before the command's role check and a
    # supervisor sending `{"cause": null}` learned their patch was malformed
    # instead of being told their role cannot edit. The module docstring
    # makes "role first, before anything else is examined" an ordering rule,
    # and a rule enforced everywhere except one path is not one.
    #
    # Naming the keys is safe *here* and would not have been three lines
    # earlier: the whitelist check above has already run, so every key left
    # is one of `EDITABLE_FIELDS` — a constant, not caller-supplied text
    # (AD-11).
    blanked = sorted(field for field, value in patch.items() if value is None)
    if blanked:
        raise InvalidPatch(f"these fields cannot be null: {', '.join(blanked)}")

    # **An ICD-10 code and its description are one fact in two columns.**
    # Moving the code alone leaves the description describing the previous
    # diagnosis, with an audit diff naming only the code — a silently wrong
    # claim file, and the exact shape the whitelist keeps `icd_desc` for.
    # Requiring the pair is what makes that impossible rather than merely
    # discouraged; the injury card sends both from one commit.
    icd_fields = {"icd", "icd_desc"} & set(patch)
    if len(icd_fields) == 1:
        raise InvalidPatch("ICD-10 code and description must be edited together")

    normalised: dict[str, str] = {}
    for field in EDITABLE_FIELDS:
        if field not in patch:
            continue
        value = require_text(field, patch[field])
        if field == "icd":
            value = value.upper()
            if not ICD_PATTERN.match(value):
                raise InvalidPatch("ICD-10 must look like `S61.412A`")
        elif field == "body_key" and value not in BODY_PART_LABELS:
            raise InvalidPatch("body part is not one of the diagram's regions")
        elif field == "recovery" and value not in set(RecoveryWindow):
            raise InvalidPatch("recovery window is not one of the five windows")
        elif field == "disability" and value not in set(Disability):
            raise InvalidPatch("disability must be temporary or permanent")
        normalised[field] = value
    return normalised


def _changes(claim: EditableClaim, normalised: Mapping[str, str]) -> tuple[_Change, ...]:
    """The columns that actually differ, plus the body-part label that rides
    with a body-key change."""
    changes = [
        _Change(column=field, before=str(getattr(claim, field)), after=value)
        for field, value in normalised.items()
        if str(getattr(claim, field)) != value
    ]
    key_change = next((change for change in changes if change.column == "body_key"), None)
    if key_change is not None:
        label = BODY_PART_LABELS[key_change.after]
        if claim.body_part != label:
            changes.append(_Change(column="body_part", before=claim.body_part, after=label))
    return tuple(changes)


def describe(changes: tuple[_Change, ...]) -> str:
    """The timeline row's prose — "Injury details updated (injury type, ICD-10)".

    Built from the *patch* labels rather than the columns, so the derived
    `body_part` write does not show up as a second field a handler did not
    touch. Ordered by `EDITABLE_FIELDS` so the sentence is stable.
    """
    touched = {change.column for change in changes}
    named = [FIELD_LABELS[field] for field in EDITABLE_FIELDS if field in touched]
    return f"Injury details updated ({', '.join(named)})"


async def update_claim_fields(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    *,
    expected_version: int,
    patch: Mapping[str, Any],
    as_of: date | None = None,
    now: datetime | None = None,
) -> ClaimDetail:
    """Apply a whitelisted patch under compare-and-swap, audited (AD-4).

    Returns the freshly assembled case file — the same shape `GET
    /claims/{id}` answers, with the new `version` and every derived value
    recomputed through the registry (AD-10). Returning the whole entity
    rather than an acknowledgement is what keeps the SPA from having to
    decide which of its cached fields the edit invalidated.

    Raises `EditNotPermitted` (403), `ClaimNotVisible` (404), `InvalidPatch`
    (422) or `StaleClaim` (409) — see the module docstring for the order and
    the reasons.

    **This command owns the transaction on `db`**: it commits on success and
    rolls back on a lost race. That is correct while a router is the caller —
    the request session has no other work in it — and it is the thing a later
    *composite* command must not inherit blindly: wrapping this function
    would hand it the authority to discard the caller's pending writes. A
    composite that needs several AD-4 writes in one transaction should call
    the pieces this function is built from, not this function (code review).

    **A failure in the re-read below happens after the commit.** The write is
    durable at that point, so the caller sees an error for an edit that
    landed; the SPA's next fetch shows it. Narrow, and deliberately not
    papered over with a partial response — an entity assembled from something
    other than the persisted row is the failure this command's whole shape is
    designed to avoid.
    """
    if ctx.role is not UserRole.handler:
        raise EditNotPermitted("Only a claims handler can edit a case file.")

    row = await claim_repo.select_claim_detail(db, ctx, claim_business_id)
    if row is None:
        raise ClaimNotVisible(claim_business_id)
    claim: Claim = row.Claim

    normalised = normalise(patch)

    if claim.version != expected_version:
        return await conflict(db, ctx, claim_business_id, as_of)

    changes = _changes(claim, normalised)
    if not changes:
        # Nothing to write, so nothing to audit and nothing to log. The
        # caller still gets the current entity; a client that sent a no-op
        # cannot tell, and should not have to.
        return await claim_detail(db, ctx, claim_business_id, as_of=as_of)

    values = {change.column: change.after for change in changes}
    changed = await claim_repo.update_claim_fields_cas(
        db, ctx, claim_business_id, expected_version, values
    )
    if changed == 0:
        # The row moved between the SELECT above and the UPDATE. Nothing was
        # written (the predicate matched no rows), but the transaction has
        # taken a snapshot, so roll it back before re-reading or the "fresh"
        # entity would be the stale one we already have.
        await db.rollback()
        return await conflict(db, ctx, claim_business_id, as_of)

    at = now or datetime.now(UTC)
    await audit.record(
        db,
        ctx,
        action=ACTION,
        entity=ENTITY,
        entity_id=claim.claim_id,
        # Only the fields written, never the whole row (AD-11). The two
        # mappings have identical keys by construction, which is the
        # property `tests/test_claim_edit_validation.py` pins with Hypothesis.
        before={change.column: change.before for change in changes},
        after={change.column: change.after for change in changes},
        at=at,
    )
    await timeline.append(
        db,
        claim_pk=claim.id,
        description=describe(changes),
        tag=TimelineTag.edit,
        # `at.date()`, **not** `as_of` (code review). `as_of` is a
        # rules-effective date — it selects which threshold version answers —
        # and letting it date the timeline meant a caller evaluating against
        # historical thresholds recorded the edit as having happened then,
        # while its audit row was stamped today. One event, one clock.
        event_date=at.date(),
    )
    await db.commit()

    # `expire_on_commit=False` (see `api/app.py`) keeps committed objects
    # readable, so the identity map still holds the `Claim` this request
    # loaded. Expire it, or the response could carry pre-edit values.
    #
    # **Not because "the ORM never saw the UPDATE"** — an earlier version of
    # this comment said so and was wrong (code review, verified against a
    # real database). `AsyncSession.execute` on a `sa.update()` is an
    # ORM-enabled UPDATE with `synchronize_session="auto"` → `"evaluate"`, so
    # SQLAlchemy re-runs the WHERE clause *in Python* against the in-session
    # object and mutates it to match. It is therefore synchronised, but on
    # the basis of what Python thinks rather than what the database did — see
    # `conflict`, where that distinction has teeth.
    db.expire_all()
    return await claim_detail(db, ctx, claim_business_id, as_of=as_of)


SEVERITY_ACTION: Final[str] = "update_claim_severity"


def normalise_severity(raw: object) -> int:
    """A whole severity score inside `0..100`, or `InvalidPatch`.

    Exported for `normalise`'s reason: an AD-13 agent tool calls the command
    directly and never passes through the API layer's Pydantic model, so the
    command has to be safe on its own.

    **`bool` is refused before `int`.** `True` is an `int` in Python, so a
    caller sending `true` would otherwise record a severity score of 1 — a
    number no one typed, in a column that decides the claim's risk band.

    **Nothing is clamped.** The prototype's `updateSevScore` does
    `Math.max(0, Math.min(100, n))`, silently turning a typo of `780` into a
    maximum-severity claim. A refusal the handler can see is the honest
    answer; clamping is a value the server invented (NFR-3, and the reason
    AC 4 asks for validation rather than correction).
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise InvalidPatch("severity score must be a whole number")
    if not SEVERITY_MIN <= raw <= SEVERITY_MAX:
        raise InvalidPatch(f"severity score must be between {SEVERITY_MIN} and {SEVERITY_MAX}")
    return raw


async def update_claim_severity(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    *,
    expected_version: int,
    severity_score: object,
    as_of: date | None = None,
    now: datetime | None = None,
) -> ClaimDetail:
    """Set a claim's severity score under compare-and-swap, audited (Story 2.4).

    The same five refusals in the same order as `update_claim_fields`, and
    the same compare-and-swap through the same repository function — this is
    a *second command*, not a second write idiom.

    **A command of its own rather than an eighth entry on the patch
    whitelist.** The story permits either. The whitelist is a machine for
    *text*: `require_text` trims and length-caps, `_Change` carries `str`
    before/after, the timeline sentence is built from `FIELD_LABELS`, and a
    Hypothesis property in `tests/test_claim_edit_validation.py` ranges over
    every member of it. Threading one integer through all of that would have
    made each of those five things conditional in order to avoid writing
    twenty lines here — and the audit diff would have recorded `"78"` for a
    numeric column. The refusal ladder, the CAS and the conflict response are
    shared; only the value's shape is not.

    **What changes downstream, and why nothing here recomputes it.** The
    severity score is what `risk` bands (AD-10), so this write moves the case
    header's gauge, the queue card's dot and the top bar's High Risk count —
    every one of them by re-reading the same registered derivation, not by
    anything this function tells them. That is exactly the property AC 3 is
    asking for, and it is a consequence of the score being a column and the
    band never being one.
    """
    if ctx.role is not UserRole.handler:
        raise EditNotPermitted("Only a claims handler can edit a case file.")

    row = await claim_repo.select_claim_detail(db, ctx, claim_business_id)
    if row is None:
        raise ClaimNotVisible(claim_business_id)
    claim: Claim = row.Claim

    score = normalise_severity(severity_score)

    if claim.version != expected_version:
        return await conflict(db, ctx, claim_business_id, as_of)

    before = claim.severity_score
    if score == before:
        # 2.3's rule: the log is a record of changes, and re-submitting the
        # score that is already stored is not one.
        return await claim_detail(db, ctx, claim_business_id, as_of=as_of)

    changed = await claim_repo.update_claim_fields_cas(
        db, ctx, claim_business_id, expected_version, {"severity_score": score}
    )
    if changed == 0:
        await db.rollback()
        return await conflict(db, ctx, claim_business_id, as_of)

    at = now or datetime.now(UTC)
    await audit.record(
        db,
        ctx,
        action=SEVERITY_ACTION,
        entity=ENTITY,
        entity_id=claim.claim_id,
        # Integers, not their string forms: the column is numeric, and an
        # audit log that has to be parsed to be compared is a log that will
        # be compared wrongly.
        before={"severity_score": before},
        after={"severity_score": score},
        at=at,
    )
    await timeline.append(
        db,
        claim_pk=claim.id,
        # **The sentence names the field, not the number.** Story 2.3 set the
        # rule for the timeline: it says what was touched. Here there is a
        # second reason to keep it — the score is the input to the risk band,
        # and a log line quoting one number out of a sequence of edits reads
        # as the claim's severity rather than as one step of it. The audit
        # row holds both values.
        description="Severity score updated",
        tag=TimelineTag.edit,
        event_date=at.date(),
    )
    await db.commit()

    db.expire_all()
    return await claim_detail(db, ctx, claim_business_id, as_of=as_of)


async def conflict(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    as_of: date | None,
) -> ClaimDetail:
    """Always raises — re-reads the claim and raises `StaleClaim` with it.

    **Public since Story 2.4**, which added three more commands that lose the
    same race in the same way (`update_claim_severity` below, and the two in
    `services/claims/injuries.py`). It was `_conflict` while there was one
    caller; a second module importing a private name is how a convention
    quietly becomes four slightly different conflict responses.

    Typed as returning `ClaimDetail` so call sites read as `return await
    conflict(...)`, which keeps mypy's exhaustiveness intact at each branch.
    A claim that vanished from scope between the read and here raises
    `ClaimNotVisible` instead, and that is the right answer: the caller
    cannot be told the fresh state of a claim they can no longer see.

    **`expire_all` first, and what it is actually defending against.** This
    function is reached precisely when somebody else's write won, so the
    `Claim` in the session's identity map is a losing snapshot — and worse
    than merely stale. A lost compare-and-swap matches zero rows in the
    database, but `synchronize_session="evaluate"` re-runs the predicate in
    Python against the in-session object, which *does* still satisfy it, and
    applies the change there: probed against a real database, the object was
    left holding the winner's `version` with the loser's value, a state that
    exists nowhere. Expiring discards it.

    On the CAS path `db.rollback()` runs first and already expires
    everything, so this line is defence in depth rather than the only guard —
    an earlier version of this docstring claimed otherwise, and
    `tests/test_claim_edit.py::test_the_conflict_response_survives_without_the_rollback`
    now pins the behaviour with the rollback removed, so neither line can be
    deleted silently (code review).
    """
    db.expire_all()
    raise StaleClaim(await claim_detail(db, ctx, claim_business_id, as_of=as_of))
