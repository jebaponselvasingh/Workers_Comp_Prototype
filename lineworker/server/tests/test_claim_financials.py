"""Story 3.3 AC 1 — the five summary derivations, and what each refuses to do.

Pure tests over the registered computers in
`services/derivations/claim_financials.py`. The figures they produce are what a
handler reads off the top of the Bills tab and off the treatment Overview's
paid-vs-reserve card, so the thing worth pinning is not the arithmetic (it is
addition) but the *choices*: which source answers when the paid columns are
empty, which statuses count as disbursed, and which weeks a "next due" date is
allowed to name.
"""

from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from data.models.enums import LineItemStatus, RecoveryWindow, ScheduleWeekStatus, Stage
from rules.parameters import DerivationThresholds
from services import derivations
from services.financials import project_payments

# --- stand-ins -----------------------------------------------------------


class Paid:
    """The three `paid_*` columns."""

    def __init__(self, indemnity: int = 0, medical: int = 0, expense: int = 0) -> None:
        self.paid_indemnity = indemnity
        self.paid_medical = medical
        self.paid_expense = expense


class Week:
    def __init__(self, week_no: int, status: ScheduleWeekStatus, *, day: int = 1) -> None:
        self.week_no = week_no
        self.period_start = date(2026, 4, day)
        self.amount_cents = 100_000
        self.status = status


class Item:
    def __init__(self, amount: int, status: LineItemStatus) -> None:
        self.amount_cents = amount
        self.status = status


#: The registry is built from thresholds; none of these five read one, so any
#: block will do. Taking it from the same place the app does keeps the call
#: shape honest rather than reaching past `for_thresholds`.
THRESHOLDS = DerivationThresholds(
    version=6,
    risk_high_min=65,
    risk_med_min=35,
    siu_fraud_score_min=60,
    rtw_blocked_hash_modulus=5,
    payment_due_hash_modulus=3,
    treatment_early_max_ratio=0.3,
    treatment_active_max_ratio=0.7,
    recovery_year_expected_days=180,
    recovery_default_expected_days=42,
    path_minor_severity_max=35,
    path_minor_recovery_windows=frozenset({RecoveryWindow.weeks_0_2}),
    path_fatality_severity_min=100,
    ptd_severity_threshold=85,
    # Story 5.1's, added in version 5 — the dashboard's fraud REVIEW cut-off,
    # which is deliberately not `siu_fraud_score_min` (the referral one).
    fraud_flag_score_min=55,
    # Story 7.1's two, added in version 6 — the edges of the fraud-score BAND the
    # analyst workspace distributes on. A third rule over the fraud columns, not a
    # re-spelling of either above it: both of those are conjoined with
    # `fraud_flag`, and this pair bands the score alone. `fraud_band_high_min`
    # carries the same integer as `fraud_flag_score_min` today, which is exactly
    # why the oracle restates it separately rather than reusing the name.
    fraud_band_high_min=55,
    fraud_band_med_min=35,
)


def paid_to_date() -> derivations.PaidToDateDerivation:
    computer: derivations.PaidToDateDerivation = derivations.paid_to_date.for_thresholds(THRESHOLDS)
    return computer


# --- paid to date: which source answers ------------------------------------


def test_the_paid_columns_answer_when_they_are_populated() -> None:
    """A settled claim: the carrier's ledger is complete, so it is the truth."""
    result = paid_to_date().of(
        Paid(indemnity=4_000_000, medical=1_200_000, expense=50_000),
        # Deliberately contradicted by the live figures, so a computer that
        # preferred them has something to be wrong about.
        disbursed_indemnity_cents=999,
        bills=[Item(777, LineItemStatus.paid)],
        expenses=[Item(555, LineItemStatus.paid)],
    )

    assert result.total_cents == 5_250_000
    assert (result.indemnity_cents, result.medical_cents, result.expense_cents) == (
        4_000_000,
        1_200_000,
        50_000,
    )
    assert result.from_paid_columns is True


def test_the_live_figures_answer_when_the_columns_are_all_zero() -> None:
    """Every open claim in the seeded portfolio, and the prototype's own rule.

    `billsHTML` says why in a comment: the static columns are "often 0 even
    though the schedule already show[s] real disbursements".
    """
    result = paid_to_date().of(
        Paid(),
        disbursed_indemnity_cents=1_135_870,
        bills=[Item(127_500, LineItemStatus.paid), Item(174_000, LineItemStatus.under_review)],
        expenses=[Item(10_450, LineItemStatus.paid)],
    )

    assert result.from_paid_columns is False
    assert result.indemnity_cents == 1_135_870
    # The unpaid bill is excluded — this is what has been *paid*, not billed.
    assert result.medical_cents == 127_500
    assert result.expense_cents == 10_450
    assert result.total_cents == 1_273_820


def test_the_fallback_is_decided_on_the_sum_not_per_component() -> None:
    """The prototype's all-or-nothing shape, and the reason it is right.

    A claim whose columns say `(0, 45000, 0)` reports medical $450 and nothing
    else. Mixing a real medical column with a live indemnity figure would put
    two sources in one total with no way to say which a number came from — and
    it is the shape a reader is most likely to "fix" into a per-field
    fallback, so it gets a test that names the choice.
    """
    result = paid_to_date().of(
        Paid(medical=45_000),
        disbursed_indemnity_cents=1_135_870,
        bills=[Item(127_500, LineItemStatus.paid)],
        expenses=[Item(10_450, LineItemStatus.paid)],
    )

    assert result.from_paid_columns is True
    assert result.total_cents == 45_000
    assert result.indemnity_cents == 0
    assert result.expense_cents == 0


@settings(max_examples=200, deadline=None)
@given(
    indemnity=st.integers(min_value=0, max_value=10_000_000),
    medical=st.integers(min_value=0, max_value=10_000_000),
    expense=st.integers(min_value=0, max_value=10_000_000),
    disbursed=st.integers(min_value=0, max_value=10_000_000),
    bill_amounts=st.lists(st.integers(min_value=0, max_value=1_000_000), max_size=8),
)
def test_the_three_components_always_add_to_the_total(
    indemnity: int, medical: int, expense: int, disbursed: int, bill_amounts: list[int]
) -> None:
    """Whichever source answered, the parts are the whole.

    The cost bar is drawn from the three components and labelled with the
    total, so a claim where they disagree renders a bar whose segments do not
    describe the number above them.
    """
    result = paid_to_date().of(
        Paid(indemnity, medical, expense),
        disbursed_indemnity_cents=disbursed,
        bills=[Item(amount, LineItemStatus.paid) for amount in bill_amounts],
        expenses=[],
    )

    assert (
        result.indemnity_cents + result.medical_cents + result.expense_cents == result.total_cents
    )


def test_the_cost_split_is_none_when_nothing_has_been_paid() -> None:
    """NFR-3's empty state, decided on the server rather than in the card."""
    computer = paid_to_date()
    result = computer.of(Paid(), disbursed_indemnity_cents=0, bills=[], expenses=[])

    assert result.total_cents == 0
    assert computer.split(result) is None


def test_the_cost_split_shares_sum_to_exactly_one_hundred() -> None:
    """The prototype's bug, not inherited: three independently rounded shares
    can total 99 or 101, and a bar drawn as three widths then misfits its
    track."""
    computer = paid_to_date()
    result = computer.of(
        Paid(),
        disbursed_indemnity_cents=1_000_000,
        bills=[Item(333_333, LineItemStatus.paid)],
        expenses=[Item(333_333, LineItemStatus.paid)],
    )
    split = computer.split(result)

    assert split is not None
    assert split.indemnity_pct + split.medical_pct + split.expense_pct == 100


# --- the schedule-derived figures ------------------------------------------


def test_installments_paid_counts_paid_weeks_only() -> None:
    """`payment_scheduled` is approved, not disbursed.

    The metric sits beside "Indemnity paid", so counting an approved week here
    would make the console claim money had moved because a handler pressed a
    button.
    """
    weeks = [
        Week(1, ScheduleWeekStatus.paid),
        Week(2, ScheduleWeekStatus.paid),
        Week(3, ScheduleWeekStatus.payment_scheduled),
        Week(4, ScheduleWeekStatus.due_this_week),
        Week(5, ScheduleWeekStatus.upcoming),
        Week(6, ScheduleWeekStatus.pending_approval),
    ]

    assert derivations.installments_paid.for_thresholds(THRESHOLDS).of(weeks) == 2


@pytest.mark.parametrize(
    ("statuses", "expected_week"),
    [
        # Due beats upcoming, wherever it sits in the list.
        ([ScheduleWeekStatus.upcoming, ScheduleWeekStatus.due_this_week], 2),
        # The *first* upcoming when nothing is due.
        ([ScheduleWeekStatus.paid, ScheduleWeekStatus.upcoming, ScheduleWeekStatus.upcoming], 2),
        # Nothing to be due: an unapproved schedule has no payment due.
        ([ScheduleWeekStatus.pending_approval, ScheduleWeekStatus.pending_approval], None),
        # An approved week is waiting to be *paid*, not to fall due.
        ([ScheduleWeekStatus.payment_scheduled, ScheduleWeekStatus.paid], None),
        # A fully paid schedule has no next payment.
        ([ScheduleWeekStatus.paid, ScheduleWeekStatus.paid], None),
    ],
)
def test_next_payment_due_skips_the_weeks_it_should(
    statuses: list[ScheduleWeekStatus], expected_week: int | None
) -> None:
    weeks = [Week(i + 1, status, day=i + 1) for i, status in enumerate(statuses)]

    result = derivations.next_payment_due.for_thresholds(THRESHOLDS).of(weeks)

    assert result == (None if expected_week is None else date(2026, 4, expected_week))


def test_the_published_next_due_agrees_with_the_projections_own() -> None:
    """Two expressions of one rule, pinned to agree (AD-10).

    `PaymentProjection.next_due` computes this inside the generator and the
    derivation computes it from the rows the generator was materialized into.
    Once a week has been *decided* the two can legitimately differ — that is
    what makes the rows the truth — but on a freshly materialized claim, where
    no decision has been made, they must be the same date. Without this, "the
    derivation is the published one" would be a comment rather than a checked
    fact.
    """

    class Claim:
        doi = date(2026, 3, 1)
        recovery = RecoveryWindow.weeks_6_8
        stage = Stage.treatment

    for offset in range(0, 90, 7):
        reference = date(2026, 3, 1) + timedelta(days=offset)
        projection = project_payments(
            Claim(), weekly_cents=100_000, waiting_days=7, as_of=reference
        )
        rows = [Week(week.week, week.status) for week in projection.weeks]
        # `Week` fixes its own start date, so compare against the projection's
        # rows rather than the stand-in's calendar.
        for row, week in zip(rows, projection.weeks, strict=True):
            row.period_start = week.start

        assert (
            derivations.next_payment_due.for_thresholds(THRESHOLDS).of(rows) == projection.next_due
        ), reference


def test_bills_on_file_counts_every_bill_not_just_the_unpaid() -> None:
    """The heading reads "n on file ($x of $y paid)" — a count that excluded
    paid bills would contradict the two figures beside it."""
    bills = [
        Item(100, LineItemStatus.paid),
        Item(200, LineItemStatus.under_review),
        Item(300, LineItemStatus.pending_submission),
    ]

    assert derivations.bills_on_file.for_thresholds(THRESHOLDS).of(bills) == 3


def test_total_claim_projected_is_paid_plus_reserve() -> None:
    assert (
        derivations.total_claim_projected.for_thresholds(THRESHOLDS).of(1_273_820, 4_500_000)
        == 5_773_820
    )


# --- the two line-item sums ------------------------------------------------


def test_paid_and_unpaid_partition_the_line_items() -> None:
    """Every status falls in exactly one of the two, by construction.

    The reason `unpaid_total` is its own function rather than
    `total - paid_total`: a status added to `LineItemStatus` lands in one of
    them by definition instead of silently counting as unpaid in one place and
    as neither in another.
    """
    items = [Item(1_000, status) for status in LineItemStatus]

    assert derivations.paid_total(items) + derivations.unpaid_total(items) == 1_000 * len(
        LineItemStatus
    )
    assert derivations.paid_total(items) == 1_000
