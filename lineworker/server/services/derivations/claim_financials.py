"""The Bills tab's summary figures — five registered derivations (Story 3.3).

AD-10: every figure a handler reads off the financial summary has exactly one
computing function, and it is here. The treatment Overview's paid-vs-reserve
card and the Bills & Payments tab show the same numbers because they call the
same computers over the same rows, which is what makes AC 4's "both surfaces
show identical figures" structural rather than a convention two components
happen to keep.

**Registered although only one of the five is parameterised — none of them
are, in fact.** `days_open` set that precedent and `claim_money` restated it:
the registry answers "who computes this?", not "what is configurable?". Three
of these are close to one-liners (`installments_paid` is a count), and that is
exactly when a second copy appears somewhere else and drifts.

## The effective breakdown, and why it is not simply the paid columns

`claim.paid_indemnity/paid_medical/paid_expense` are a **snapshot**, and on
every open claim in the seeded portfolio all three are zero — while the
schedule shows elapsed weeks and the bills show payments. The prototype hit
this and says so in a comment beside its own fix (`billsHTML`, line 1429): the
static columns are "often 0 even though the schedule already show[s] real
disbursements", so it derives the breakdown from the live figures instead.

`PaidToDate` ports that rule exactly, including its all-or-nothing shape: the
fallback is decided **once, on the sum**, not per component. A claim whose
columns say `(0, 45000, 0)` reports medical $450 and nothing else, rather than
mixing a real medical column with a live indemnity figure — two sources in one
total, and no way to say which of them a number came from.

This is the same defect Story 3.2's code review found on the treatment card,
where "Indemnity paid $0.00" was rendered above a verdict computed from the
projection. The fix there was to make one card use one notion of paid; this is
that notion, named and computed once, for every surface that shows it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from data.models.enums import LineItemStatus, ScheduleWeekStatus
from services.derivations.claim_money import CostSplit, PaidColumns, TotalPaidDerivation, split_of
from services.derivations.registry import Derivation, register


class ScheduleRow(Protocol):
    """One persisted `payment_schedule_week`, structurally.

    Structural rather than the ORM class so these derivations stay pure and
    testable against small stand-ins — `PaidColumns`' rule, and the reason a
    property test can range over schedules without a database.
    """

    @property
    def week_no(self) -> int: ...
    @property
    def period_start(self) -> date: ...
    @property
    def amount_cents(self) -> int: ...
    @property
    def status(self) -> ScheduleWeekStatus: ...


class LineItem(Protocol):
    """One persisted `bill` or `expense` — the two columns these figures read.

    One protocol for both tables because these derivations do not care which
    they are looking at: a paid line item is a paid line item, and the amount
    is an amount. `LineItemStatus` is shared for the same reason.
    """

    @property
    def amount_cents(self) -> int: ...
    @property
    def status(self) -> LineItemStatus: ...


def paid_total(items: Sequence[LineItem]) -> int:
    """The paid subset's amount, in cents.

    A module function because three of the derivations below want it and so
    does `services/financials/summary.py` — one definition of "paid" over a
    line-item list, rather than a `sum(... if ... == paid)` comprehension
    repeated at four call sites where the fourth eventually says `!= paid`.
    """
    return sum(item.amount_cents for item in items if item.status is LineItemStatus.paid)


def unpaid_total(items: Sequence[LineItem]) -> int:
    """The complement — everything not yet paid, in cents.

    The reserve check's medical exposure term. Its own function rather than
    `total - paid_total(...)` so that a status added to `LineItemStatus` lands
    in exactly one of the two by definition, instead of silently counting as
    unpaid in one place and as neither in another.
    """
    return sum(item.amount_cents for item in items if item.status is not LineItemStatus.paid)


@dataclass(frozen=True)
class PaidToDate:
    """What has actually been disbursed on a claim, and where it went.

    `total_cents` is the figure the summary's first paycard shows; the three
    components are the cost bar's segments and are published separately
    because "why is this claim's paid-to-date $38,000" is answered by the
    split, not by the total.

    `from_paid_columns` says which source answered. It exists because the two
    are genuinely different statements about a claim — "the carrier's ledger
    says this" against "the schedule and the bills add up to this" — and a
    reader debugging a figure that looks wrong needs to know which one they
    are looking at before anything else. Nothing in the UI renders it today.
    """

    total_cents: int
    indemnity_cents: int
    medical_cents: int
    expense_cents: int
    from_paid_columns: bool


@dataclass(frozen=True)
class PaidToDateDerivation:
    def of(
        self,
        claim: PaidColumns,
        *,
        disbursed_indemnity_cents: int,
        bills: Sequence[LineItem],
        expenses: Sequence[LineItem],
    ) -> PaidToDate:
        """The prototype's effective breakdown (`billsHTML`, lines 1428-1436).

        `disbursed_indemnity_cents` is the schedule's paid-so-far — passed in
        rather than summed here, because "which weeks count as disbursed" is
        `installments_paid`'s question one line down and the two must not
        answer it differently.

        Keyword-only past the claim: four of the five inputs are money or
        collections of it, and positionally, swapping bills for expenses would
        produce a perfectly plausible total from the wrong halves.
        """
        static_total = TotalPaidDerivation().of(claim)
        if static_total > 0:
            return PaidToDate(
                total_cents=static_total,
                indemnity_cents=claim.paid_indemnity,
                medical_cents=claim.paid_medical,
                expense_cents=claim.paid_expense,
                from_paid_columns=True,
            )
        medical = paid_total(bills)
        expense = paid_total(expenses)
        return PaidToDate(
            total_cents=disbursed_indemnity_cents + medical + expense,
            indemnity_cents=disbursed_indemnity_cents,
            medical_cents=medical,
            expense_cents=expense,
            from_paid_columns=False,
        )

    def split(self, paid: PaidToDate) -> CostSplit | None:
        """The cost bar over this breakdown — `claim_money.split_of`.

        A method here rather than a sixth registered derivation: it is the
        *same* derived value `cost_split` already names, computed over the
        effective triple instead of the static one. Registering a second
        `cost_split_effective` would be two names for one question, which is
        what AD-10's one-computer rule exists to stop; delegating to `split_of`
        means the rounding convention that makes three shares sum to exactly
        100 lives in one place whichever triple it is given.
        """
        return split_of(paid.indemnity_cents, paid.medical_cents, paid.expense_cents)


@dataclass(frozen=True)
class TotalClaimProjectedDerivation:
    def of(self, paid_to_date_cents: int, reserve_cents: int) -> int:
        """Paid to date plus the reserve still held — the prototype's
        `totalClaimAmount`.

        **Not "incurred", and the distinction is the one `claim_money` already
        records in reverse.** That module notes the prototype labelling
        `paid_indemnity + paid_medical + paid_expense` "Total incurred" when
        insurance practice reserves the word for paid *plus* reserve. This
        figure is that quantity — and the prototype calls it "Total Claim
        (Projected)", which is both accurate and what the card says. Two
        figures, two names, and the naming discrepancy stays where it was
        rather than spreading.
        """
        return paid_to_date_cents + reserve_cents


@dataclass(frozen=True)
class InstallmentsPaidDerivation:
    def of(self, weeks: Sequence[ScheduleRow]) -> int:
        """How many scheduled weeks have been disbursed.

        A count of rows rather than `disbursed / weekly`: a division would be
        wrong the moment two weeks carry different amounts, which they can as
        soon as a comp-rate override lands between one materialization and the
        next (paid weeks keep the amount they were paid at —
        `services/financials/materialize.py`).
        """
        return sum(1 for week in weeks if week.status is ScheduleWeekStatus.paid)


@dataclass(frozen=True)
class NextPaymentDueDerivation:
    def of(self, weeks: Sequence[ScheduleRow]) -> date | None:
        """When the next payment falls due, or `None`.

        The prototype's rule (`billsHTML`, `nextRow`): the first week that is
        due this week, and otherwise the first upcoming one. Both exclusions
        are deliberate and both are the prototype's —

        - **`pending_approval` weeks are skipped.** A claim whose whole
          schedule awaits approval has no payment *due*; answering with its
          first projected week would put a date beside the word "due" for
          money nobody has authorised.
        - **`payment_scheduled` weeks are skipped too**, which the prototype
          has no equivalent of. A week already approved into a batch is not
          waiting to fall due — it is waiting to be paid — so naming it here
          would tell a handler to expect an action that has been taken.

        **This is the same rule `PaymentProjection.next_due` applies inside the
        generator, and this is the one that is published.** The difference is
        the source: that one reads the projection, this one reads the rows the
        projection was materialized into, and once a week has been decided the
        rows are the truth. `tests/test_claim_financials.py` pins the two
        agreeing on a freshly materialized claim, which is what keeps a second
        expression of one rule safe.
        """
        upcoming: date | None = None
        for week in weeks:
            if week.status is ScheduleWeekStatus.due_this_week:
                return week.period_start
            if week.status is ScheduleWeekStatus.upcoming and upcoming is None:
                upcoming = week.period_start
        return upcoming


@dataclass(frozen=True)
class BillsOnFileDerivation:
    def of(self, bills: Sequence[LineItem]) -> int:
        """How many medical bills the claim has — paid or not.

        *On file*, so every row counts: the card's heading reads "n on file
        ($x of $y paid)", and a count that quietly excluded unpaid bills would
        contradict the two figures beside it.
        """
        return len(bills)


paid_to_date = register(
    Derivation(
        name="paid_to_date",
        describes=(
            "what has actually been disbursed on a claim, in cents, with its "
            "indemnity/medical/expense split — the paid columns when they are "
            "populated, and the live schedule and line items when they are not"
        ),
        build=lambda _thresholds: PaidToDateDerivation(),
    )
)

total_claim_projected = register(
    Derivation(
        name="total_claim_projected",
        describes="paid to date plus the reserve still held, in cents",
        build=lambda _thresholds: TotalClaimProjectedDerivation(),
    )
)

installments_paid = register(
    Derivation(
        name="installments_paid",
        describes="how many weeks of the indemnity schedule have been disbursed",
        build=lambda _thresholds: InstallmentsPaidDerivation(),
    )
)

next_payment_due = register(
    Derivation(
        name="next_payment_due",
        describes=(
            "the start date of the first schedule week that is due or upcoming, "
            "skipping weeks awaiting approval or already scheduled into a batch"
        ),
        build=lambda _thresholds: NextPaymentDueDerivation(),
    )
)

bills_on_file = register(
    Derivation(
        name="bills_on_file",
        describes="how many medical bills a claim has, paid or not",
        build=lambda _thresholds: BillsOnFileDerivation(),
    )
)
