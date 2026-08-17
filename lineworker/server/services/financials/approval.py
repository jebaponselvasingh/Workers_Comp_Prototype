"""Approving a payment into the next batch (Story 3.4, AC 1) — AD-12's write.

`services/financials` is the sole writer of `payment_schedule_week`, `bill`
and `expense` (AD-12), and this module holds the three commands that move a
row from "somebody should look at this" to "queued for disbursement". The
worklist's approval surface and the Bills tab's ✓ buttons both arrive here;
neither touches a table.

## The one-sentence invariant

**Approval never marks `paid`, and only the batch ever does.**

The source spreadsheet contradicts itself about this — row 29 says approving a
payment marks it Paid, rows 64–66 say approval sets Payment Scheduled — and
the spine's conventions resolve it in favour of 64–66. So these commands write
exactly one status, `payment_scheduled`, and `services/financials/batch.py` is
the only place `payment_scheduled → paid` happens. The two halves of that
sentence are enforced separately and by different means:

- *Approval never marks paid* is structural here: `paid` appears in this
  module only as a status these commands **refuse to start from**.
- *Only the batch marks paid* is held by `tests/test_payment_ownership.py`,
  which greps the tree — with one honest exception for schedule weeks that the
  batch module documents at length.

## The refusal ladder, and why it is 2.3's ladder plus one rung

`services/claims/edit.py` set the order every AD-4 command follows: role,
then scope, then the request's own validity, then the version, then the write.
A lifecycle command adds a rung, and AD-4 names it in as many words —
"lifecycle/status updates additionally guard on expected current status".

That guard is not redundant with the version, which is the thing worth being
clear about. A version says "the row has not moved since you read it". A
status guard says "the move you are asking for is available from where the row
is now". They fail on different histories: approving a week that somebody else
approved a second ago fails the *version* check; approving a week whose sheet
the handler left open across a batch run — same version if nothing else
touched it, but now `paid` — fails the *status* check. Without the second, the
console would emit a second approval audit event for a payment that has
already gone out.

Both are checked in the same UPDATE (`transition_payment_row_cas`), so neither
is a read-then-write race. The pre-checks below exist only to produce the
right *answer* — a 404 for a row that is not there, a 409 with fresh state for
one that has moved — and they run after the statement has already failed, so
the happy path pays for neither.

## What "approvable" means, per table, and where it comes from

The prototype decides this twice and differently, and both are ported:

- **A schedule week** is approvable from any status that is not already
  decided — `pending_approval`, `due_this_week` or `upcoming`
  (`reviewScheduleWeek`: `canApprove = status !== "Paid" && status !==
  "PaymentScheduled"`). The AC's canonical case is `pending_approval`; the
  other two are included because a handler *can* approve next week's payment
  early, and refusing that would be a rule the prototype does not have.
- **A bill or an expense** is approvable only from `under_review`
  (`reviewLineItem`: `canApprove = it.status === "UnderReview"`). A
  `pending_submission` item is one the *provider* has not filed — there is
  nothing to approve, and approving it would queue money against a bill nobody
  has sent.

Stated as frozensets rather than as `not in DECIDED` so that the answer is
readable in one place and so the UI's "can this be approved?" and the
command's refusal are the same list crossing the wire (`approvable` on the
week and line-item payloads).

## The import direction, since it looks backwards

This module imports `services.claims.timeline`, while `services/claims/detail`
imports `services.financials`. That is not a cycle: `services/claims/__init__`
is empty, so reaching `services.claims.timeline` pulls in nothing else, and
the timeline writer imports only `data/`. It is deliberate rather than
tolerated — AD-12 makes `services/claims/timeline.py` the *sole* constructor
of a `TimelineEvent` (asserted in `tests/test_claim_edit_validation.py`) while
making the owning command responsible for emitting one. A financials command
that wrote its own timeline row would break the first rule to satisfy the
second; calling the one writer satisfies both.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models import Bill, Expense, PaymentScheduleWeek
from data.models.enums import LineItemStatus, ScheduleWeekStatus, TimelineTag
from data.repositories import claims as claim_repo
from services import audit
from services.claims import timeline

#: A schedule week may be approved from any status a decision has not already
#: put there. See the module docstring for the prototype's own predicate.
APPROVABLE_WEEK_STATUSES: Final[frozenset[ScheduleWeekStatus]] = frozenset(
    {
        ScheduleWeekStatus.pending_approval,
        ScheduleWeekStatus.due_this_week,
        ScheduleWeekStatus.upcoming,
    }
)

#: A bill or expense may be approved only once it has been submitted and
#: reviewed. `pending_submission` is the *provider's* state, not the handler's.
APPROVABLE_LINE_ITEM_STATUSES: Final[frozenset[LineItemStatus]] = frozenset(
    {LineItemStatus.under_review}
)

#: One action name per command — the function's own name, so an audit row read
#: months later names what wrote it (`services/claims/edit.py`'s convention).
APPROVE_WEEK_ACTION: Final[str] = "approve_schedule_week"
APPROVE_BILL_ACTION: Final[str] = "approve_bill_payment"
APPROVE_EXPENSE_ACTION: Final[str] = "approve_expense_payment"

#: How `_conflict` re-reads a row after the compare-and-swap refused it —
#: always the same scoped repository call the command opened with, closed over
#: by the command so this module has exactly one read path per table.
type Reread = Callable[[], Awaitable[PaymentRow | None]]

WEEK_ENTITY: Final[str] = "payment_schedule_week"
BILL_ENTITY: Final[str] = "bill"
EXPENSE_ENTITY: Final[str] = "expense"


class PaymentNotVisible(LookupError):
    """No such payment row on this claim, for this caller — a 404.

    One exception for "no such week", "no such bill", "another claim's row" and
    "a claim outside your book", because the repository answers `None` to all
    four and a route that told them apart would be an enumeration oracle
    (AD-7). `ClaimNotVisible` is raised a layer up, by whoever resolved the
    claim; this is the row-level counterpart.
    """


class StalePaymentRow(Exception):
    """The row moved before the approval landed — a 409.

    Carries the row's **fresh status and version** rather than the whole
    entity, unlike `StaleClaim`. The caller that turns this into a problem
    document (`services/worklist/approvals.py`) re-reads the entire financials
    payload anyway — the summary's figures move with an approval — so
    attaching a second copy of one row here would put two answers about it in
    one response. What the fields are for is the *reason*: "somebody else
    approved this" and "this was paid while your sheet was open" are different
    sentences, and only the status can tell them apart.
    """

    def __init__(self, *, entity: str, status: Any, version: int) -> None:
        super().__init__(f"{entity} has moved on from the version you were approving")
        self.entity = entity
        self.status = status
        self.version = version


@dataclass(frozen=True)
class Approval:
    """What one approval did — the audit trail's own summary of it.

    Returned rather than `None` so a caller can say what happened without
    re-reading, and so the worklist's delegating command has something to log
    that is not a claim field (AD-11).
    """

    entity: str
    entity_id: str
    label: str
    before: Any
    after: Any


class PaymentRow(Protocol):
    """The columns every approvable row has, structurally.

    Three ORM classes with three different category vocabularies and one
    shared shape: an id, a version, a status. A protocol rather than a union
    keeps `_approve` readable and keeps a test able to hand it a stand-in.
    """

    @property
    def id(self) -> int: ...
    @property
    def version(self) -> int: ...
    @property
    def status(self) -> Any: ...


def _timeline_sentence(label: str) -> str:
    """The timeline row's prose.

    **Names what was approved, never the amount**, which is Story 2.4's rule
    for the severity score restated: the timeline is a log of what moved, the
    audit event holds the values, and a money figure quoted in a sentence gets
    read as the claim's total rather than as one line of it.
    """
    return f"{label} approved for payment"


async def _approve(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    table: type[PaymentScheduleWeek] | type[Bill] | type[Expense],
    row: PaymentRow,
    reread: Reread,
    expected_version: int,
    approvable: frozenset[Any],
    scheduled: Any,
    claim_pk: int,
    claim_ref: str,
    entity: str,
    entity_id: str,
    action: str,
    label: str,
    at: datetime,
) -> Approval:
    """The write the three public commands share, once.

    Every one of them does the same five things in the same order, and the
    only differences are which table, which vocabulary and what the timeline
    sentence says. Written once because the *sameness is the contract*: three
    copies would be three chances for one of them to skip the status guard or
    to commit before the audit row.
    """
    # **Read off the row before anything writes, and this is load-bearing**
    # (found by `test_a_stale_version_is_refused_with_the_fresh_payload`
    # against a real database). `db.rollback()` on the conflict path expires
    # every object in the session, so `row.id` *after* it is an implicit
    # refresh — and implicit IO on an `AsyncSession` outside an awaited call
    # raises `MissingGreenlet`, which surfaced as a 500 on the one path that
    # exists to answer 409. Two integers held in locals cost nothing and cannot
    # go stale: they are what the row said when the caller's version was read.
    row_id = row.id
    before = row.status

    changed = await claim_repo.transition_payment_row_cas(
        db,
        ctx,
        table,
        row_id=row_id,
        expected_version=expected_version,
        expected_statuses=approvable,
        new_status=scheduled,
    )
    if changed == 0:
        # The row moved between the read above and this statement, or it was
        # never in an approvable state to begin with. Roll back before
        # re-reading, or the "fresh" row would be the snapshot this
        # transaction already took — `services/claims/edit.py::conflict`
        # learned this against a real database.
        await db.rollback()
        raise await _conflict(db, reread, entity=entity, row_id=row_id)

    await audit.record(
        db,
        ctx,
        action=action,
        entity=entity,
        # The **claim's** business id, not the row's surrogate, so the audit
        # log answers "what happened to WC-20017" without a join. Which row it
        # was is in the diff, where a week number and a bill label mean
        # something to a reader; a bare `id` would not.
        entity_id=claim_ref,
        before={"status": before.value, "item": entity_id},
        after={"status": scheduled.value, "item": entity_id},
        at=at,
    )
    await timeline.append(
        db,
        claim_pk=claim_pk,
        description=_timeline_sentence(label),
        # `benefit`, the Epic 3 tag Story 3.1 introduced for "a financial
        # decision on the case file", rather than `approval`. The seeded
        # `approval` rows are the *claim's* approval at intake — one event per
        # claim, in its lifecycle — and a handler filtering a timeline for
        # "when was this claim approved" should not get twenty payment lines
        # back. Story 3.1's docstring names 3.4's approvals as this tag's next
        # user, so this is the plan rather than a reading of it.
        tag=TimelineTag.benefit,
        # `at.date()`, not a rules-effective date — 2.3's "one event, one
        # clock" correction.
        event_date=at.date(),
    )
    await db.commit()
    return Approval(
        entity=entity,
        # The **row's** identity here, not `claim_ref` — the opposite call from
        # the audit row above, and for the same reason. The audit log is asked
        # "what happened to WC-20017"; a caller holding this object already
        # knows which claim it asked about and is missing only which line of it
        # moved. Story 3.5's checklist acknowledgement is the caller the class
        # docstring names, and "week 3" is what it has to render.
        entity_id=entity_id,
        label=label,
        before=before,
        after=scheduled,
    )


async def _conflict(
    db: AsyncSession,
    reread: Reread,
    *,
    entity: str,
    row_id: int,
) -> Exception:
    """Why the compare-and-swap matched nothing — always an exception to raise.

    Returns rather than raises so the call site reads `raise await
    _conflict(...)`, which keeps mypy's flow analysis intact at each branch
    (`services/claims/edit.py::conflict` returns a value for the same reason,
    from the other direction).

    The row is re-read to tell the two failures apart: gone (or now out of
    scope) is a 404, still there is a 409 carrying what it says now. "Still
    there" covers both a version mismatch and a status that was never
    approvable, and they are deliberately one answer — the fresh status is
    attached, so a client can render "already paid" without the server having
    to publish which predicate failed.

    **`reread` is the caller's own scoped repository call**, closed over, not
    a `db.get(table, id)` here. That was the first version and it was wrong in
    a way worth recording: `Session.get` goes to the identity map and the
    primary key, so it re-reads *without* the employer predicate — and a
    function holding a `CallerContext` it never used is precisely what
    `tests/test_scoped_repository.py::test_no_service_function_accepts_a_caller_context_it_never_reads`
    exists to catch. It caught it. Nothing was reachable that should not have
    been (the row was resolved in scope moments earlier), but the shape read as
    scoping applied where none was, which is how the next unscoped read gets
    written.
    """
    db.expire_all()
    fresh = await reread()
    if fresh is None:
        return PaymentNotVisible(f"no {entity} {row_id} in your caseload")
    return StalePaymentRow(entity=entity, status=fresh.status, version=fresh.version)


async def approve_schedule_week(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_pk: int,
    claim_ref: str,
    week_no: int,
    expected_version: int,
    now: datetime | None = None,
) -> Approval:
    """Approve one week of a claim's indemnity schedule into the next batch.

    Sets `payment_scheduled` and nothing else — see the module docstring on why
    that is the whole of the story's invariant.

    **The claim is resolved by the caller**, which is the arrangement
    `materialize_schedule` and `reserve_check_for_claim` already use and the
    one AD-12 requires: `services/claims` owns the `claim` row and does the
    scoped resolve, `services/financials` is handed the identifiers and owns
    the money. `claim_pk` addresses the rows, `claim_ref` is what the audit
    event and the timeline name.

    **No role gate here.** Capability is checked once, by the worklist command
    that fronts this (AD-7: role gates capability), and checking it twice would
    mean two places to keep in step — with the failure mode that the *inner*
    one is the one nobody reaches from a test. What this command does enforce
    unconditionally is scope: the UPDATE carries `employer_scope`, so a caller
    that skipped the resolve still cannot write another book's row.

    Raises `PaymentNotVisible` (404) or `StalePaymentRow` (409).
    """

    async def reread() -> PaymentScheduleWeek | None:
        return await claim_repo.select_schedule_week(db, ctx, claim_ref, week_no)

    row = await reread()
    if row is None:
        raise PaymentNotVisible(f"no week {week_no} on {claim_ref} in your caseload")
    return await _approve(
        db,
        ctx,
        table=PaymentScheduleWeek,
        row=row,
        reread=reread,
        expected_version=expected_version,
        approvable=APPROVABLE_WEEK_STATUSES,
        scheduled=ScheduleWeekStatus.payment_scheduled,
        claim_pk=claim_pk,
        claim_ref=claim_ref,
        entity=WEEK_ENTITY,
        entity_id=f"week {week_no}",
        action=APPROVE_WEEK_ACTION,
        label=f"Indemnity payment week {week_no}",
        at=now or datetime.now(UTC),
    )


async def approve_bill_payment(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_pk: int,
    claim_ref: str,
    bill_id: int,
    expected_version: int,
    now: datetime | None = None,
) -> Approval:
    """Approve one medical bill into the next batch (`under_review` → scheduled).

    `approve_schedule_week`'s contract over a different table and a narrower
    approvable set. The Excel's rows 64–66 write-back, and the prototype's
    `approvePayment` with the bug removed: the prototype writes the status
    token `PendingSubmission` and *labels* it "Payment Scheduled", so its
    approved bills become indistinguishable from bills nobody has filed. Here
    they are two members and this is the transition between them.
    """

    async def reread() -> Bill | None:
        return await claim_repo.select_bill(db, ctx, claim_ref, bill_id)

    row = await reread()
    if row is None:
        raise PaymentNotVisible(f"no bill {bill_id} on {claim_ref} in your caseload")
    return await _approve(
        db,
        ctx,
        table=Bill,
        row=row,
        reread=reread,
        expected_version=expected_version,
        approvable=APPROVABLE_LINE_ITEM_STATUSES,
        scheduled=LineItemStatus.payment_scheduled,
        claim_pk=claim_pk,
        claim_ref=claim_ref,
        entity=BILL_ENTITY,
        entity_id=f"bill {bill_id}",
        action=APPROVE_BILL_ACTION,
        label=row.label,
        at=now or datetime.now(UTC),
    )


async def approve_expense_payment(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_pk: int,
    claim_ref: str,
    expense_id: int,
    expected_version: int,
    now: datetime | None = None,
) -> Approval:
    """Approve one claim expense into the next batch — `approve_bill_payment`'s rule."""

    async def reread() -> Expense | None:
        return await claim_repo.select_expense(db, ctx, claim_ref, expense_id)

    row = await reread()
    if row is None:
        raise PaymentNotVisible(f"no expense {expense_id} on {claim_ref} in your caseload")
    return await _approve(
        db,
        ctx,
        table=Expense,
        row=row,
        reread=reread,
        expected_version=expected_version,
        approvable=APPROVABLE_LINE_ITEM_STATUSES,
        scheduled=LineItemStatus.payment_scheduled,
        claim_pk=claim_pk,
        claim_ref=claim_ref,
        entity=EXPENSE_ENTITY,
        entity_id=f"expense {expense_id}",
        action=APPROVE_EXPENSE_ACTION,
        label=row.label,
        at=now or datetime.now(UTC),
    )


def week_is_approvable(status: ScheduleWeekStatus) -> bool:
    """Whether the ✓ button is offered for a week — the *server's* answer.

    Published on the payload (`ScheduleWeekResponse.approvable`) rather than
    left to the browser, and this is AD-1 rather than pedantry: "which statuses
    can be approved" is the same rule the command refuses on, and a client that
    decided it independently would offer a button for a state the server
    rejects — a 409 the handler cannot act on, for a control that should not
    have been there.
    """
    return status in APPROVABLE_WEEK_STATUSES


def line_item_is_approvable(status: LineItemStatus) -> bool:
    """`week_is_approvable` over the line-item vocabulary."""
    return status in APPROVABLE_LINE_ITEM_STATUSES


def approvable_statuses(entity: str) -> Sequence[Any]:
    """The approvable set for one entity name, sorted — for tests and messages.

    A lookup rather than three exported constants at the call site, so a test
    that ranges over the three commands can range over their vocabularies too.
    """
    if entity == WEEK_ENTITY:
        return sorted(APPROVABLE_WEEK_STATUSES)
    if entity in (BILL_ENTITY, EXPENSE_ENTITY):
        return sorted(APPROVABLE_LINE_ITEM_STATUSES)
    raise KeyError(f"{entity!r} is not an approvable payment entity")


__all__ = [
    "APPROVABLE_LINE_ITEM_STATUSES",
    "APPROVABLE_WEEK_STATUSES",
    "APPROVE_BILL_ACTION",
    "APPROVE_EXPENSE_ACTION",
    "APPROVE_WEEK_ACTION",
    "BILL_ENTITY",
    "EXPENSE_ENTITY",
    "WEEK_ENTITY",
    "Approval",
    "PaymentNotVisible",
    "StalePaymentRow",
    "approvable_statuses",
    "approve_bill_payment",
    "approve_expense_payment",
    "approve_schedule_week",
    "line_item_is_approvable",
    "week_is_approvable",
]
