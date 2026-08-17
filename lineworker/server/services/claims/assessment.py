"""The checklist's three writes (Story 3.5, AC 4 and AC 5) — AD-12's owner.

A handler working the "⏰ Upcoming actions required" card can do three things
without leaving it: approve the claim's assessment, mark a document read and
then accept it, and record that a recordable injury has been entered on the
OSHA 300 log. All three land here, and they land here rather than in
`services/worklist` because of who owns the rows: `claim` and `document` are
`services/claims`' tables (AD-12), and the worklist generates the *list* while
the owning service performs the *write*. The fourth thing that card can do —
confirm a payment — is Story 3.4's command, reached through its own surface,
and this module deliberately has no member for it.

## Three commands, one ladder — `services/claims/edit.py`'s

Role, then scope, then the request's own validity, then the version, then the
write. Role is checked **before the claim is looked up**, which is a security
property rather than a style choice: if scope came first, a supervisor would
get 404 for a claim outside their book and 403 for one inside it, and the
difference would answer "does this claim exist?" for a caller who may not ask.

Each of the three adds AD-4's extra rung for a lifecycle update — a guard on
the *expected current state*, in the same statement as the version — and each
guard is a different pair of columns:

- **`approve_assessment`** guards `status IN (initial, ch_assessment_process)`.
- **`set_document_review`** guards the review pair. Marking reviewed asks for
  `(false, false)`; confirming asks for `(true, false)`, which is where the
  prototype's own sequencing (`confirmDocument` returns early when
  `!st.reviewed`) becomes a WHERE clause instead of a disabled button.
- **`mark_osha_logged`** guards `osha_recordable AND NOT osha_logged`.

The guard is not redundant with the version in any of the three, and the reason
is the same each time: a version says nobody has touched the row, a state guard
says the move is available from where the row is. They fail on different
histories, and without the second, an audit log would gain a second event for a
decision made once.

## Re-doing a *completion* is a no-op; re-doing the *transition* is not

Confirming an already-confirmed document and logging an already-logged injury
each answer 200 with the current case file and write nothing. That is
`update_comp_rate_override`'s rule — "the log is a record of changes, and
re-submitting the value already stored is not one" — and it matters more here
than it did there, because these controls sit on a card that re-renders after
every write and a handler double-clicking one should not be told that somebody
else changed their claim.

**`approve_assessment` is deliberately outside that rule**, and an earlier
version of this section wrongly listed it inside (code review, 2026-08-17). The
two completions record a fact; the approval performs a status transition, and a
transition whose precondition has already been consumed is a 409 with the fresh
entity — see that function's own docstring for the argument.

**A stale version is still a 409 even when the value already matches**, which
is the pre-check's order below: the claim moved under the caller, and the fresh
entity is what they need. The no-op path is only reached once the version is
known to be current.

## What these three do *not* do

They do not toggle. A completion here is one-way: reviewed, confirmed and
logged are set and never cleared. The prototype's `confirmDocument` toggles
(line 881), and it is the one part of `docReviewState` not ported — an audited
completion a second click silently withdraws is a worse record than no
completion at all, and "I confirmed the wrong document" is a correction that
wants an author and a reason rather than the same button pressed twice. Named
here because it is a deliberate deviation, and recorded in `deferred-work.md`.
"""

from datetime import UTC, date, datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models.core import Claim
from data.models.enums import ActionCommand, ClaimStatus, TimelineTag, UserRole
from data.repositories import claims as claim_repo
from services import audit
from services.claims import timeline
from services.claims.detail import ClaimDetail, ClaimNotVisible, claim_detail
from services.claims.edit import EditNotPermitted, InvalidPatch, conflict

#: The statuses a claim's assessment may be approved *from*, and the status it
#: is approved *to* (the prototype's `approveClaim`, lines 925-930).
#:
#: Exported because `services/worklist/actions.py` fires its
#: `assessment_approval` trigger on exactly this set. One list rather than two
#: agreeing ones: a checklist that offered the button from a status this command
#: refuses would produce a 409 the handler can do nothing about, for a control
#: that should not have been on screen (`week_is_approvable`'s argument, one
#: entity over).
APPROVABLE_ASSESSMENT_STATUSES: Final[frozenset[ClaimStatus]] = frozenset(
    {ClaimStatus.initial, ClaimStatus.ch_assessment_process}
)
APPROVED_STATUS: Final[ClaimStatus] = ClaimStatus.ch_approved

#: One `action` per command — the function's own name, so an audit row read
#: months later names what wrote it (`services/claims/edit.py`'s convention).
APPROVE_ASSESSMENT_ACTION: Final[str] = "approve_assessment"
SET_DOCUMENT_REVIEW_ACTION: Final[str] = "set_document_review"
MARK_OSHA_LOGGED_ACTION: Final[str] = "mark_osha_logged"

CLAIM_ENTITY: Final[str] = "claim"
DOCUMENT_ENTITY: Final[str] = "document"

#: The two document states a caller may ask for, and what each requires the row
#: to be first. The mapping *is* the sequencing rule — see the module docstring
#: on why it is a WHERE clause rather than a button's `disabled` attribute.
_DOCUMENT_STEPS: Final[dict[ActionCommand, tuple[bool, bool, str]]] = {
    # command -> (expected reviewed, expected confirmed, column to set)
    ActionCommand.mark_document_reviewed: (False, False, "reviewed"),
    ActionCommand.confirm_document: (True, False, "confirmed"),
}


async def approve_assessment(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    *,
    expected_version: int,
    as_of: date | None = None,
    now: datetime | None = None,
) -> ClaimDetail:
    """Move a claim from assessment to `ch_approved`, audited (AC 4).

    Returns the freshly assembled case file — the header's status chip, the
    stepper and the timeline with this approval's event already in it — for
    `update_claim_fields`' reason: an acknowledgement would leave the SPA
    deciding what the approval invalidated, which is what AD-9 forbids the
    browser from doing.

    Raises `EditNotPermitted` (403), `ClaimNotVisible` (404) or `StaleClaim`
    (409). A claim already in `ch_approved` — or in `denied`, `settled`,
    `settled_closed` — is a 409 rather than a silent success, and the two cases
    are deliberately one answer: both mean "this claim is not where you thought
    it was", the fresh entity is attached, and telling them apart would publish
    which predicate failed for no benefit a handler can use.

    **This command has no no-op branch, unlike the other two**, and the review
    pass sharpened why (2026-08-17). The original argument — that a second
    click always carries a stale version — is not quite true: this command
    answers with the whole case file, so the SPA installs the new version at
    once while the checklist row awaits its own refetch, and a second click can
    therefore arrive with a *current* version against an already-approved
    claim. It is still refused, deliberately. A document review and an OSHA
    entry are records of a fact — asserting them twice asserts the same fact,
    so 200 is honest. An approval is a **transition**, and "approve this claim"
    against a claim that is already approved is not the same request twice: it
    is a request whose precondition no longer holds, which is what 409-with-
    the-fresh-entity means everywhere else in this build. The card renders that
    entity, so the handler sees `ch_approved` either way; what differs is
    whether the log and the API agree about what happened.

    **This command owns the transaction on `db`**, as every AD-4 command in this
    build does, with the same warning: a later composite must call the pieces
    rather than wrapping this.
    """
    if ctx.role is not UserRole.handler:
        raise EditNotPermitted("Only a claims handler can approve a claim assessment.")

    row = await claim_repo.select_claim_detail(db, ctx, claim_business_id)
    if row is None:
        raise ClaimNotVisible(claim_business_id)
    claim: Claim = row.Claim

    # **Read before anything writes** (Story 3.4's live bug, restated). The
    # conflict path rolls back, which expires every object in the session, and
    # implicit IO on an `AsyncSession` outside an awaited call raises
    # `MissingGreenlet` — so the value the audit diff needs is held in a local
    # while it is certainly readable.
    before = claim.status
    claim_pk = claim.id
    claim_ref = claim.claim_id

    changed = await claim_repo.transition_claim_status_cas(
        db,
        ctx,
        claim_business_id,
        expected_version=expected_version,
        expected_statuses=APPROVABLE_ASSESSMENT_STATUSES,
        new_status=APPROVED_STATUS,
    )
    if changed == 0:
        # Either the claim moved between the SELECT above and this statement,
        # or it was never in an approvable status. Roll back before re-reading
        # or the "fresh" entity would be this transaction's own snapshot —
        # `services/claims/edit.py::conflict` learned that against a real
        # database.
        await db.rollback()
        return await conflict(db, ctx, claim_business_id, as_of)

    at = now or datetime.now(UTC)
    await audit.record(
        db,
        ctx,
        action=APPROVE_ASSESSMENT_ACTION,
        entity=CLAIM_ENTITY,
        entity_id=claim_ref,
        before={"status": before.value},
        after={"status": APPROVED_STATUS.value},
        at=at,
    )
    await timeline.append(
        db,
        claim_pk=claim_pk,
        description="Claim assessment approved by the handler",
        # `approval`, the tag the seed already uses for a claim's own lifecycle
        # approval — which is precisely what this is. `TimelineTag`'s docstring
        # keeps that tag for one event per claim, in its lifecycle, and a
        # handler filtering a timeline for "when was this claim approved" should
        # find this row and not twenty payment lines.
        tag=TimelineTag.approval,
        # `at.date()`, not `as_of` — Story 2.3's "one event, one clock".
        event_date=at.date(),
    )
    await db.commit()

    # `expire_on_commit=False` keeps committed objects readable, so the identity
    # map still holds the pre-approval `Claim`. Expire it, or the response could
    # carry the previous status.
    db.expire_all()
    return await claim_detail(db, ctx, claim_business_id, as_of=as_of)


async def set_document_review(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    document_id: int,
    *,
    step: ActionCommand,
    expected_version: int,
    as_of: date | None = None,
    now: datetime | None = None,
) -> ClaimDetail:
    """Mark one document reviewed, or confirm one already reviewed (AC 5).

    `step` is the completion being recorded, and it is one of the two document
    members of `ActionCommand` — the same value the checklist published on the
    action the handler clicked, so the control and the command name one thing
    rather than two that have to agree. Anything else is an `InvalidPatch`
    (422): a caller asking to "approve an assessment" through the document route
    has sent a request this command cannot apply, and refusing it here also
    covers the AD-13 agent tools, which never pass through the API layer's
    Pydantic model.

    `expected_version` is the **document's**, not the claim's — the write
    compare-and-swaps on the row it changes, so accepting a medical
    authorization is not refused because somebody corrected an unrelated field
    on the same claim. `useRemoveInjury` publishes the row version for exactly
    this reason.

    Raises `EditNotPermitted` (403), `ClaimNotVisible` (404 — including for a
    document that is not on this claim, which is the same answer for
    `select_document`'s reason), `InvalidPatch` (422) or `StaleClaim` (409). A
    document already in the requested state answers 200 with the current case
    file and writes nothing; **confirming a document nobody has reviewed is a
    409**, not a 422, because it is a statement about where the row is rather
    than about the request — and the fresh entity attached is what tells the
    handler to press Review first.
    """
    if ctx.role is not UserRole.handler:
        raise EditNotPermitted("Only a claims handler can record a document review.")

    row = await claim_repo.select_claim_detail(db, ctx, claim_business_id)
    if row is None:
        raise ClaimNotVisible(claim_business_id)
    claim_pk = row.Claim.id
    claim_ref = row.Claim.claim_id

    if step not in _DOCUMENT_STEPS:
        raise InvalidPatch("a document review step must be either review or confirmation")
    expected_reviewed, expected_confirmed, column = _DOCUMENT_STEPS[step]

    document = await claim_repo.select_document(db, ctx, claim_business_id, document_id)
    if document is None:
        raise ClaimNotVisible(claim_business_id)

    # Held before any write, `approve_assessment`'s rule.
    was_reviewed = document.reviewed
    was_confirmed = document.confirmed
    document_name = document.name

    if document.version != expected_version:
        # The pre-check exists so that a *no-op* request on a stale version
        # still conflicts — there would be no UPDATE for the compare-and-swap to
        # fail on. `services/claims/edit.py` argues the same ordering.
        return await conflict(db, ctx, claim_business_id, as_of)

    if getattr(document, column):
        # Already done. The log is a record of changes; a second click on a
        # control that has not re-rendered yet is not one.
        return await claim_detail(db, ctx, claim_business_id, as_of=as_of)

    changed = await claim_repo.update_document_review_cas(
        db,
        ctx,
        claim_business_id,
        document_id,
        expected_version=expected_version,
        expected_reviewed=expected_reviewed,
        expected_confirmed=expected_confirmed,
        values={column: True},
    )
    if changed == 0:
        # Confirming an unreviewed document lands here, as does a row that moved
        # between the SELECT and the UPDATE. One answer, and the fresh case file
        # says which it was.
        await db.rollback()
        return await conflict(db, ctx, claim_business_id, as_of)

    at = now or datetime.now(UTC)
    await audit.record(
        db,
        ctx,
        action=SET_DOCUMENT_REVIEW_ACTION,
        entity=DOCUMENT_ENTITY,
        # The **claim's** business id, not the document's surrogate, so the
        # audit log answers "what happened to WC-20017" without a join —
        # `services/financials/approval.py`'s convention. Which document it was
        # is in the diff, where an id sits beside the two flags it moved.
        entity_id=claim_ref,
        before={"document": document_id, "reviewed": was_reviewed, "confirmed": was_confirmed},
        after={
            "document": document_id,
            "reviewed": was_reviewed or column == "reviewed",
            "confirmed": was_confirmed or column == "confirmed",
        },
        at=at,
    )
    await timeline.append(
        db,
        claim_pk=claim_pk,
        # **Names the document, and nothing about its contents** — Story 2.4's
        # rule for the severity score. A document's name is already on the case
        # file's Documents tab; what it says about a worker is not, and a
        # timeline is the wrong place for it to arrive (AD-11).
        description=(
            f"{document_name} confirmed" if column == "confirmed" else f"{document_name} reviewed"
        ),
        tag=TimelineTag.document,
        event_date=at.date(),
    )
    await db.commit()

    db.expire_all()
    return await claim_detail(db, ctx, claim_business_id, as_of=as_of)


async def mark_osha_logged(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    *,
    expected_version: int,
    as_of: date | None = None,
    now: datetime | None = None,
) -> ClaimDetail:
    """Record that a recordable injury has been entered on the OSHA 300 log (AC 5).

    Raises `EditNotPermitted` (403), `ClaimNotVisible` (404), `InvalidPatch`
    (422) or `StaleClaim` (409).

    **A claim that is not `osha_recordable` is a 422, not a 409**, and that is
    the one place these three commands' refusals differ in kind. A 409 says "the
    row moved, re-read and try again"; there is nothing to re-read here and
    trying again will never work, because whether an injury is recordable is a
    property of the injury rather than a state a handler passes through. The
    sentence names the rule and no claim data (AD-11).

    A claim already logged answers 200 with the current case file and writes
    nothing — `set_document_review`'s rule, and `update_comp_rate_override`'s
    before it.
    """
    if ctx.role is not UserRole.handler:
        raise EditNotPermitted("Only a claims handler can record an OSHA 300 entry.")

    row = await claim_repo.select_claim_detail(db, ctx, claim_business_id)
    if row is None:
        raise ClaimNotVisible(claim_business_id)
    claim: Claim = row.Claim
    claim_pk = claim.id
    claim_ref = claim.claim_id
    recordable = claim.osha_recordable
    already_logged = claim.osha_logged

    if not recordable:
        raise InvalidPatch(
            "this claim's injury is not OSHA recordable, so it has no OSHA 300 entry to file"
        )

    if claim.version != expected_version:
        return await conflict(db, ctx, claim_business_id, as_of)

    if already_logged:
        return await claim_detail(db, ctx, claim_business_id, as_of=as_of)

    changed = await claim_repo.mark_osha_logged_cas(
        db, ctx, claim_business_id, expected_version=expected_version
    )
    if changed == 0:
        await db.rollback()
        return await conflict(db, ctx, claim_business_id, as_of)

    at = now or datetime.now(UTC)
    await audit.record(
        db,
        ctx,
        action=MARK_OSHA_LOGGED_ACTION,
        entity=CLAIM_ENTITY,
        entity_id=claim_ref,
        before={"osha_logged": False},
        after={"osha_logged": True},
        at=at,
    )
    await timeline.append(
        db,
        claim_pk=claim_pk,
        description="Injury recorded on the OSHA 300 log",
        tag=TimelineTag.compliance,
        event_date=at.date(),
    )
    await db.commit()

    db.expire_all()
    return await claim_detail(db, ctx, claim_business_id, as_of=as_of)


def document_step_of(*, reviewed: bool, confirmed: bool) -> ActionCommand | None:
    """Which completion a document is next eligible for — the *server's* answer.

    Published on the checklist action (`Action.command`) rather than inferred in
    the browser, for `week_is_approvable`'s reason: "reviewed but not confirmed"
    looks like the whole rule and is not — a confirmed document is eligible for
    nothing, and a client that read only `reviewed` would offer Confirm on a row
    the command refuses.

    **Two booleans rather than a `Document`**, so that both readers can call it:
    `services/worklist/actions.py` holds documents behind a structural protocol
    (which keeps its generator pure and database-free), and this package must
    not import that protocol back. The alternative is the answer written twice,
    which is exactly the disagreement between the offered control and the
    accepted transition that publishing it exists to prevent.
    """
    if confirmed:
        return None
    if reviewed:
        return ActionCommand.confirm_document
    return ActionCommand.mark_document_reviewed


__all__ = [
    "APPROVABLE_ASSESSMENT_STATUSES",
    "APPROVED_STATUS",
    "APPROVE_ASSESSMENT_ACTION",
    "MARK_OSHA_LOGGED_ACTION",
    "SET_DOCUMENT_REVIEW_ACTION",
    "approve_assessment",
    "document_step_of",
    "mark_osha_logged",
    "set_document_review",
]
