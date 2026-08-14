"""Story 3.2 AC 1 — the payment-schedule projection, without a database.

The indemnity half of "projected remaining exposure" is
`total_scheduled − paid_so_far`, so the reserve verdict is only as trustworthy
as this projection. `test_reserve_check.py` proves the banding; this proves the
number it bands.

**It is also Story 3.3's generator.** That story persists `payment_schedule_week`
rows through `project_payments` rather than beside it (AD-2), so the assertions
below are about a contract two stories share: how many weeks, starting when, and
which of them count as paid.

**The reference date is always named.** `project_payments` takes `as_of` rather
than reading a clock, and every test here passes a date — a suite that let the
calendar decide would pass today and fail in three weeks, on the same code.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from data.models.enums import RecoveryWindow, ScheduleWeekStatus, Stage
from rules.parameters import DerivationThresholds
from services import derivations
from services.financials import (
    MAX_WEEKS,
    MIN_WEEKS,
    SCHEDULE_WEEKS,
    PaymentProjection,
    project_payments,
    scheduled_weeks,
)

WEEKLY = 954_71
WAITING_DAYS = 7
DOI = date(2026, 3, 22)
FIRST_START = DOI + timedelta(days=WAITING_DAYS)  # 2026-03-29


@dataclass(frozen=True)
class FakeClaim:
    """The three columns `ScheduleClaim` names, and nothing else.

    A stand-in rather than a `Claim`, for `test_benefit_calculation.FakeClaim`'s
    reason: constructing the ORM row means supplying forty unrelated NOT NULL
    columns to ask about three. It is a *typed* stand-in — mypy checks it
    against the protocol at every call below.
    """

    doi: date = DOI
    recovery: RecoveryWindow = RecoveryWindow.weeks_6_8
    stage: Stage = Stage.treatment


def project(
    *,
    recovery: RecoveryWindow = RecoveryWindow.weeks_6_8,
    stage: Stage = Stage.treatment,
    doi: date = DOI,
    as_of: date = date(2026, 4, 26),
    weekly: int = WEEKLY,
) -> PaymentProjection:
    return project_payments(
        FakeClaim(doi=doi, recovery=recovery, stage=stage),
        weekly_cents=weekly,
        waiting_days=WAITING_DAYS,
        as_of=as_of,
    )


# --- how many weeks -------------------------------------------------------


@pytest.mark.parametrize(
    ("recovery", "expected"),
    [
        # The clamp bites at the bottom: a 0-2 week window is still projected
        # over the four-week minimum.
        (RecoveryWindow.weeks_0_2, 4),
        (RecoveryWindow.weeks_2_4, 4),
        (RecoveryWindow.weeks_4_6, 6),
        (RecoveryWindow.weeks_6_8, 8),
        # And at the top: 26 weeks of entitlement, 20 weeks shown.
        (RecoveryWindow.over_1_year, 20),
    ],
)
def test_the_week_count_is_the_window_upper_bound_clamped(
    recovery: RecoveryWindow, expected: int
) -> None:
    """The prototype's `Math.max(4, Math.min(weeks, 20))` over its own numbers."""
    assert scheduled_weeks(recovery) == expected
    assert project(recovery=recovery).week_count == expected


def test_every_recovery_window_has_a_week_count() -> None:
    """A sixth member must not be a `KeyError` in a request path.

    The lookup is exhaustive by construction rather than by a default, which is
    the arrangement `EXPECTED_WEEKS` uses one module over — with the difference
    that this one has no fallback at all, so the coverage is the guarantee.
    """
    assert set(SCHEDULE_WEEKS) == set(RecoveryWindow)


@pytest.mark.parametrize("recovery", list(RecoveryWindow))
def test_the_week_count_is_always_inside_the_clamp(recovery: RecoveryWindow) -> None:
    assert MIN_WEEKS <= scheduled_weeks(recovery) <= MAX_WEEKS


def test_the_schedule_is_not_the_treatment_phases_window() -> None:
    """Two tables over one column, answering two questions — on purpose.

    `SCHEDULE_WEEKS` says how many weekly indemnity payments a claim has
    scheduled; `treatment_progress.EXPECTED_WEEKS` says how long the claim was
    expected to take. They are pinned apart here because they *look* like
    duplication — this is the assertion that keeps the entry
    `test_case_file_derivations.PHASE_HOME` carries for `schedule.py` honest,
    and that would fail if a later story folded one into the other.

    A 0-2 week claim is the clearest case: 14 expected days, and four weeks of
    payments, because a schedule shorter than the minimum is not worth a table.
    """
    phase = derivations.treatment_phase.for_thresholds(THRESHOLDS)

    assert phase.expected_days_for(RecoveryWindow.weeks_0_2) == 14
    assert scheduled_weeks(RecoveryWindow.weeks_0_2) == 4


THRESHOLDS = DerivationThresholds(
    version=4,
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
)


# --- when the weeks start -------------------------------------------------


def test_the_first_week_starts_after_the_statutory_waiting_period() -> None:
    """Story 3.1 displayed the waiting period; this is where it is applied."""
    assert project().weeks[0].start == FIRST_START


def test_weeks_are_seven_days_and_run_back_to_back() -> None:
    """Inclusive ends, so week N+1 starts the day after week N finishes."""
    weeks = project().weeks

    for week in weeks:
        assert (week.end - week.start).days == 6
    for earlier, later in zip(weeks, weeks[1:], strict=False):
        assert later.start == earlier.end + timedelta(days=1)


def test_the_weeks_are_numbered_from_one() -> None:
    """A label a handler reads ("Wk 3"), not an index."""
    assert [week.week for week in project().weeks] == list(range(1, 9))


# --- which weeks count as paid -------------------------------------------


def test_a_week_that_ended_before_today_is_paid() -> None:
    """Four weeks fully elapsed by 26 April: 29 Mar, 5, 12 and 19 April."""
    projection = project(as_of=date(2026, 4, 26))
    statuses = [week.status for week in projection.weeks]

    assert statuses[:4] == [ScheduleWeekStatus.paid] * 4
    assert projection.paid_so_far_cents == WEEKLY * 4


def test_the_week_containing_today_is_due_this_week() -> None:
    """26 April falls inside 26 April – 2 May, which is week 5."""
    weeks = project(as_of=date(2026, 4, 26)).weeks

    assert weeks[4].start == date(2026, 4, 26)
    assert weeks[4].status is ScheduleWeekStatus.due_this_week


def test_a_week_still_ahead_is_upcoming() -> None:
    statuses = [week.status for week in project(as_of=date(2026, 4, 26)).weeks]

    assert statuses[5:] == [ScheduleWeekStatus.upcoming] * 3


def test_the_boundary_days_of_the_current_week_are_both_due() -> None:
    """The edge a `<` slipped into a `<=` would move by one day.

    The first and last day of a week both fall inside it, so a claim read on
    either reports the same week as due — and the week before it as paid only
    once it has actually finished.
    """
    first_day = project(as_of=date(2026, 4, 26)).weeks[4]
    last_day = project(as_of=date(2026, 5, 2)).weeks[4]

    assert first_day.status is ScheduleWeekStatus.due_this_week
    assert last_day.status is ScheduleWeekStatus.due_this_week
    assert project(as_of=date(2026, 5, 3)).weeks[4].status is ScheduleWeekStatus.paid


@pytest.mark.parametrize("stage", [Stage.intake, Stage.investigation])
def test_an_unapproved_stage_pays_nothing_however_old_the_claim(stage: Stage) -> None:
    """Stage before calendar. The prototype's first branch.

    Read three years after the injury, so a projection that let the dates
    decide would report every week paid — which for a claim nobody has approved
    a payment on would be the console inventing disbursements.
    """
    projection = project(stage=stage, as_of=date(2029, 1, 1))

    assert {week.status for week in projection.weeks} == {ScheduleWeekStatus.pending_approval}
    assert projection.paid_so_far_cents == 0
    assert projection.remaining_indemnity_cents == projection.total_scheduled_cents


def test_a_settled_claim_is_fully_paid_however_recent() -> None:
    """The other stage short-circuit, read the day after the injury."""
    projection = project(stage=Stage.settled, as_of=DOI + timedelta(days=1))

    assert {week.status for week in projection.weeks} == {ScheduleWeekStatus.paid}
    assert projection.paid_so_far_cents == projection.total_scheduled_cents
    assert projection.remaining_indemnity_cents == 0


def test_a_claim_whose_schedule_has_not_begun_owes_all_of_it() -> None:
    """A future-dated injury — the seeded portfolio runs to September."""
    projection = project(doi=date(2026, 9, 22), as_of=date(2026, 8, 14))

    assert {week.status for week in projection.weeks} == {ScheduleWeekStatus.upcoming}
    assert projection.remaining_indemnity_cents == WEEKLY * 8


# --- the totals -----------------------------------------------------------


def test_the_totals_are_the_weekly_figure_times_the_weeks() -> None:
    projection = project()

    assert projection.weekly_cents == WEEKLY
    assert projection.total_scheduled_cents == WEEKLY * projection.week_count
    assert all(week.amount_cents == WEEKLY for week in projection.weeks)


def test_remaining_indemnity_is_what_is_scheduled_less_what_is_paid() -> None:
    projection = project(as_of=date(2026, 4, 26))

    assert projection.remaining_indemnity_cents == (
        projection.total_scheduled_cents - projection.paid_so_far_cents
    )
    assert projection.remaining_indemnity_cents == WEEKLY * 4


def test_remaining_indemnity_is_floored_at_zero() -> None:
    """A fully-elapsed schedule owes nothing rather than a negative sum.

    Unreachable today — paid weeks are a subset of scheduled ones — and kept
    because Story 3.3 persists these rows and 3.4 moves their statuses. A
    negative exposure would classify as *heavy* with total confidence, which is
    the worst available way to be wrong about a reserve.
    """
    projection = project(as_of=date(2029, 1, 1))

    assert projection.paid_so_far_cents == projection.total_scheduled_cents
    assert projection.remaining_indemnity_cents == 0


# --- the next payment due -------------------------------------------------


def test_next_due_is_the_first_week_that_is_not_yet_paid() -> None:
    assert project(as_of=date(2026, 4, 26)).next_due == date(2026, 4, 26)


def test_next_due_is_none_when_every_week_is_paid() -> None:
    assert project(as_of=date(2029, 1, 1)).next_due is None


def test_next_due_is_none_while_the_schedule_awaits_approval() -> None:
    """The prototype's exclusion, ported rather than tidied.

    A claim whose whole schedule is pending approval has no payment *due*;
    answering with its first projected week would put a date beside the word
    "due" for money nobody has authorised.
    """
    assert project(stage=Stage.intake, as_of=date(2026, 4, 26)).next_due is None
