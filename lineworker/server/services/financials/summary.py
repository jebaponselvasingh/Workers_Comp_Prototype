"""The claim-financials read model — one surface, one assembler (Story 3.3).

Everything the Bills & Payments tab renders: the four-figure summary, the cost
bar, the week-by-week schedule, the medical bills, the expenses and the reserve
verdict. Assembled here and nowhere else.

**One read model rather than four endpoints**, and the story asks for it in
those terms. The Bills tab shows a summary whose figures are sums of the three
lists below it; served separately, a client could hold a summary fetched before
a refresh beside a schedule fetched after one, and the totals row would
disagree with the rows it totals. One payload under one query key makes that
unrepresentable.

It is also the surface every later financial story consumes: Story 3.4's
approval batch refetches it, 3.5's bill-review actions act on its line items,
Epic 5's dashboard aggregates the same derivations and Epic 6's copilot tools
read it. Keeping it one endpoint is what stops each of those growing a
financial read of its own.

## What this assembles, and what it computes (nothing)

Every figure below comes from a registered derivation
(`services/derivations/claim_financials.py`) or from `classify_reserve`. This
module orders the work and shapes the result; it decides no money. That
division is AD-10's, and it is why the summary the case file embeds and the
summary this returns cannot differ — `services/claims/detail.py` calls the same
assembler and takes the reserve verdict out of the same object.

## The schedule is refreshed before anything reads it

`materialize_schedule` runs first, and the rest of the function reads the rows
it returned rather than re-querying. Three of the five week statuses move with
the calendar, so an unrefreshed read is a stale read; doing it once here and
handing the rows down is what keeps the summary's `installments_paid`, the
schedule table's chips and the reserve check's disbursed figure the same
answer to the same question. The command is a no-op when nothing has moved.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models import Bill, Expense
from data.models.enums import BillCategory, ExpenseCategory, LineItemStatus, ScheduleWeekStatus
from data.repositories import claims as claim_repo
from rules.parameters import DerivationThresholds, reserve_bands_for
from services import derivations
from services.derivations.claim_money import CostSplit, PaidColumns
from services.financials.approval import line_item_is_approvable, week_is_approvable
from services.financials.benefit import Benefit
from services.financials.materialize import materialize_schedule
from services.financials.reserve import ReserveCheck, ReserveClaim, reserve_check_from_rows


class FinancialsClaim(ReserveClaim, PaidColumns, Protocol):
    """Every claim column this read model reads, in one protocol.

    The union of two narrower ones rather than a restatement of their members,
    `ReserveClaim`'s own arrangement over `ScheduleClaim`: the reserve half
    projects and judges a schedule, the `PaidColumns` half is the snapshot
    `paid_to_date` falls back *from*. Extending both keeps the lists from
    drifting and says what each is for — and it is what makes the difference
    between the two visible at the type level, which is the distinction this
    whole module is careful about.
    """


@dataclass(frozen=True)
class ScheduleWeekView:
    """One row of the week-by-week table.

    A view type rather than the ORM row for the reason every other block on the
    case file has one: the wire shape is a decision this layer makes.

    **`version` arrives with Story 3.4, which is the story that reads it** —
    3.3 published neither it nor a surrogate id and said this would add them
    "deliberately". The approval compare-and-swaps on this number, so a client
    that renders the ✓ button must be holding the version it will send. The
    surrogate `id` is still absent: a week is identified by `(claim, week_no)`,
    the table's own unique constraint, and publishing a second identity for one
    row would let two callers address it two ways.

    **`approvable` is the server's answer, not a client-side status check.**
    Which statuses can be approved is the same rule
    `services/financials/approval.py` refuses on, and a browser deciding it
    independently would offer a button the server rejects (AD-1).
    """

    week_no: int
    period_start: date
    period_end: date
    amount_cents: int
    status: ScheduleWeekStatus
    version: int
    approvable: bool


@dataclass(frozen=True)
class LineItemView:
    """One medical bill or one expense.

    **`id` is published here, unlike on the schedule week.** A line item has no
    business identifier and no natural key — two "Follow-Up Physician Visit"
    rows on one claim are possible and the label is content a later story may
    reword — so the surrogate is the only stable thing a React list key or a
    3.4 approval can address it by. `document` publishes its id for exactly
    this reason; a schedule week does not need one because `(claim, week_no)`
    already identifies it.
    """

    id: int
    category: BillCategory | ExpenseCategory
    label: str
    amount_cents: int
    status: LineItemStatus
    #: `ScheduleWeekView`'s two Story 3.4 fields, for the same reasons — the
    #: version the approval compare-and-swaps on, and the server's own answer
    #: to whether the ✓ button belongs on this row.
    version: int
    approvable: bool


@dataclass(frozen=True)
class LineItemGroup:
    """A claim's bills or expenses, with the two figures their card heading states.

    The totals are here rather than left to the client because the heading
    reads "6 on file ($4,120 of $23,400 paid)" — three numbers about one list,
    and a browser that added two of them itself would be the AD-1 violation
    that turns into a rounding disagreement the moment anything is filtered.
    """

    items: tuple[LineItemView, ...]
    count: int
    total_cents: int
    paid_cents: int


@dataclass(frozen=True)
class FinancialSummary:
    """The four paycards, the cost bar and the metrics row (AC 1).

    `cost_split` is `None` when nothing has been disbursed, which is the state
    the card renders as "No payments disbursed yet — reserve of $X held against
    projected exposure" rather than as a bar of three zero-width segments. That
    `None` is `split_of`'s decision, not this dataclass's.

    `paid_from_columns` says whether the paid figures came from the claim's
    `paid_*` snapshot or from the live schedule and line items. Nothing renders
    it; it is here because the two are different statements about a claim and a
    reader debugging a figure needs to know which one they are looking at
    before anything else.
    """

    paid_to_date_cents: int
    total_claim_projected_cents: int
    reserve_cents: int
    cost_split: CostSplit | None
    paid_indemnity_cents: int
    paid_medical_cents: int
    paid_expense_cents: int
    paid_from_columns: bool

    # The metrics row.
    weekly_indemnity_cents: int
    installments_paid: int
    week_count: int
    next_payment_due: date | None
    bills_on_file: int

    # The schedule header's two figures — "$21,793 disbursed to date of
    # $22,940 scheduled". Published rather than summed in the browser for
    # `LineItemGroup`'s reason, and they are the same two `ReserveCheck`
    # carries, from the same rows.
    scheduled_indemnity_cents: int
    disbursed_indemnity_cents: int

    #: When the next payment batch runs (Story 3.4). The prototype's
    #: `nextBatchDate`, and the date its Bills summary and both approval sheets
    #: render — "Approved payments are disbursed in the next scheduled batch
    #: run. Next batch: Friday, Aug 15."
    #:
    #: **Not claim-specific**, which is the one odd thing about it living on a
    #: per-claim payload. It rides here because it is only ever shown beside
    #: these figures and because a second endpoint for one date would be a
    #: second cache entry the Bills tab has to keep in step with this one; a
    #: dashboard that needs it later should take it from the registered
    #: derivation, not from a claim's summary.
    next_batch_date: date


@dataclass(frozen=True)
class ClaimFinancials:
    """The whole Bills & Payments payload, and the case file's reserve verdict.

    `reserve_check` travels here as well as on the case file, and it is the
    *same object* on both — `services/claims/detail.py` takes it out of this
    result rather than computing a second one. AC 4 asks that the treatment
    Overview card and this tab show identical figures; one computation reached
    through one assembler is what makes that true regardless of when either
    payload was fetched.
    """

    summary: FinancialSummary
    schedule: tuple[ScheduleWeekView, ...]
    bills: LineItemGroup
    expenses: LineItemGroup
    reserve_check: ReserveCheck


def _group(rows: Sequence[Bill] | Sequence[Expense]) -> LineItemGroup:
    items = tuple(
        LineItemView(
            id=row.id,
            category=row.category,
            label=row.label,
            amount_cents=row.amount_cents,
            status=row.status,
            version=row.version,
            approvable=line_item_is_approvable(row.status),
        )
        for row in rows
    )
    return LineItemGroup(
        items=items,
        count=len(items),
        total_cents=sum(item.amount_cents for item in items),
        paid_cents=derivations.paid_total(items),
    )


async def claim_financials(
    db: AsyncSession,
    ctx: CallerContext,
    claim: FinancialsClaim,
    *,
    claim_pk: int,
    claim_ref: str,
    benefit: Benefit,
    thresholds: DerivationThresholds,
    as_of: date,
    batch_weekdays: frozenset[int],
) -> ClaimFinancials:
    """Everything the Bills & Payments tab shows, for one claim.

    `benefit` and `thresholds` are handed in rather than fetched for
    `reserve_check_for_claim`'s reason: both callers have already loaded them,
    and a second `state_rate_schedule` lookup and rule-document load to reach
    the identical values is two round trips for nothing.

    `batch_weekdays` arrives the same way and from a different tier —
    `Settings.payment_batch_weekday_numbers`, a deployment knob rather than a
    JDM parameter, threaded from the route exactly as `services/worklist/sla.py`
    threads its four targets. A service that read `get_settings()` itself would
    be a second place configuration is consumed, and the SLA strip already set
    the precedent against that.

    Four queries: the schedule refresh's SELECT, the bills, the expenses and
    the reserve bands' document. AD-7 is enforced on the two line-item reads by
    the repositories, and on the schedule a step earlier — the claim reached
    this service through `select_claim_detail`, which is scoped.
    """
    weeks = await materialize_schedule(
        db,
        claim,
        claim_pk=claim_pk,
        claim_ref=claim_ref,
        benefit=benefit,
        as_of=as_of,
        ctx=ctx,
    )
    bills = await claim_repo.select_bills(db, ctx, claim_ref)
    expenses = await claim_repo.select_expenses(db, ctx, claim_ref)

    bill_group = _group(bills)
    expense_group = _group(expenses)

    reserve_check = reserve_check_from_rows(
        claim,
        weeks=weeks,
        bills=bill_group.items,
        bands=await reserve_bands_for(db, as_of),
    )

    paid_to_date_computer = derivations.paid_to_date.for_thresholds(thresholds)
    paid = paid_to_date_computer.of(
        claim,
        # The verdict's own disbursed figure, so the summary's indemnity share
        # and the reserve card's "Indemnity paid" row are the same number
        # rather than two sums of the same rows.
        disbursed_indemnity_cents=reserve_check.disbursed_indemnity_cents,
        bills=bill_group.items,
        expenses=expense_group.items,
    )

    summary = FinancialSummary(
        paid_to_date_cents=paid.total_cents,
        total_claim_projected_cents=derivations.total_claim_projected.for_thresholds(thresholds).of(
            paid.total_cents, claim.reserve
        ),
        reserve_cents=claim.reserve,
        cost_split=paid_to_date_computer.split(paid),
        paid_indemnity_cents=paid.indemnity_cents,
        paid_medical_cents=paid.medical_cents,
        paid_expense_cents=paid.expense_cents,
        paid_from_columns=paid.from_paid_columns,
        weekly_indemnity_cents=benefit.weekly_cents,
        installments_paid=derivations.installments_paid.for_thresholds(thresholds).of(weeks),
        # The number of rows, not the projection's week count: a schedule that
        # shortened keeps any decided week past its new end
        # (`materialize.py`), and the table below shows them, so a count from
        # the projection would disagree with what is on screen.
        week_count=len(weeks),
        next_payment_due=derivations.next_payment_due.for_thresholds(thresholds).of(weeks),
        bills_on_file=derivations.bills_on_file.for_thresholds(thresholds).of(bill_group.items),
        scheduled_indemnity_cents=reserve_check.scheduled_indemnity_cents,
        disbursed_indemnity_cents=reserve_check.disbursed_indemnity_cents,
        next_batch_date=derivations.next_batch_date.for_thresholds(thresholds).of(
            as_of, batch_weekdays
        ),
    )

    return ClaimFinancials(
        summary=summary,
        schedule=tuple(
            ScheduleWeekView(
                week_no=week.week_no,
                period_start=week.period_start,
                period_end=week.period_end,
                amount_cents=week.amount_cents,
                status=week.status,
                version=week.version,
                approvable=week_is_approvable(week.status),
            )
            for week in weeks
        ),
        bills=bill_group,
        expenses=expense_group,
        reserve_check=reserve_check,
    )


__all__ = [
    "ClaimFinancials",
    "FinancialsClaim",
    "FinancialSummary",
    "LineItemGroup",
    "LineItemView",
    "ScheduleWeekView",
    "claim_financials",
]
