"""The indemnity payment-schedule projection — one generator (Story 3.2).

The prototype's `buildPaymentSchedule` (line 740), ported. It answers a
question Story 3.2 needs and Story 3.3 renders: given a claim's weekly
benefit, how many weeks is it scheduled for, which of those weeks have been
paid, and therefore how much indemnity exposure is still ahead of the reserve.

**Why it lands in 3.2 rather than 3.3, and why that is not scope creep.**
`reserve_check` compares *projected remaining exposure* to the reserve, and
the indemnity half of that number is `total_scheduled − paid_so_far`. There
is no honest way to compute it without the projection. AD-2 then forbids the
obvious alternative — a small "just the totals" calculation here and the real
generator in 3.3 — because that is two functions deciding how long a claim's
schedule is. So the generator is here, complete, and Story 3.3 persists
`payment_schedule_week` rows *through this function* rather than beside it.
The story's Dev Notes call this seam deliberate; this module is where it sits.

**Pure, and deliberately so.** No database, no clock: the reference date is an
argument. `services/financials/benefit.py` splits the same way and for the
same reason — it is what lets a property test run this ten thousand times, and
what stops a request that straddles UTC midnight from projecting two claims
against two different days.

## Where the reference date comes from, and the one divergence

The prototype computes its reference date as `DOI + c.daysOpen`, which reads
oddly until you notice that `daysOpen` is a *static dataset column*: adding a
claim's age to its date of injury is how a file with no clock says "now". This
console has a clock, so the reference date is simply `as_of` — the same day
the rest of the case file is resolved against.

The two are not identical, and the difference is worth writing down: this
build's `days_open` counts from `froi_date` rather than `doi` (see
`services/derivations/open_duration.py` for why), so the prototype's
expression would land a few days *behind* today by exactly the reporting lag.
Porting the intent rather than the arithmetic is what keeps a schedule moving
with the calendar instead of with a column nobody maintains.

## What stays in Python, and what 3.3 may want to move

`SCHEDULE_WEEKS`, `MIN_WEEKS` and `MAX_WEEKS` are the prototype's, and they
are ordinary constants rather than JDM parameters. This story's rule document
(`reserve_bands`) carries the two band thresholds and nothing else, which is
what the story scopes it to; the schedule's own tunables belong to the story
that owns the schedule surface. Recorded in `deferred-work.md` so that 3.3
promotes them deliberately rather than inheriting them by accident.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final, Protocol

from data.models.enums import RecoveryWindow, ScheduleWeekStatus, Stage

DAYS_PER_WEEK: Final[int] = 7
"""Not a parameter, for `treatment_progress.DAYS_PER_WEEK`'s reason: a week is
seven days whatever the business rules say."""

#: Weeks the schedule runs for, per recovery window — the prototype's regex
#: result as a lookup over tokens, exactly as `EXPECTED_WEEKS` replaced the
#: same regex in `services/derivations/treatment_progress.py`.
#:
#: The **upper** bound of each window, matching the prototype's
#: `parseInt(m[2])`, and 26 for the year-scale member (its `/Year/` branch).
#: Note that these are the values *before* the clamp below, which is why
#: `weeks_0_2`'s 2 and `over_1_year`'s 26 are stated rather than pre-clamped:
#: a reader comparing this table with the prototype should find the prototype's
#: numbers, and the clamp should be visible as the separate rule it is.
SCHEDULE_WEEKS: Final[dict[RecoveryWindow, int]] = {
    RecoveryWindow.weeks_0_2: 2,
    RecoveryWindow.weeks_2_4: 4,
    RecoveryWindow.weeks_4_6: 6,
    RecoveryWindow.weeks_6_8: 8,
    RecoveryWindow.over_1_year: 26,
}

#: The prototype's `Math.max(4, Math.min(weeks, 20))`. A schedule of fewer than
#: four weeks is not worth a week-by-week table, and twenty is as far ahead as
#: this console projects — a year-scale claim's schedule is *shown* twenty
#: weeks deep rather than claimed to be twenty weeks long, which is why the
#: Bills tab's heading in the prototype says "20-week schedule shown".
MIN_WEEKS: Final[int] = 4
MAX_WEEKS: Final[int] = 20

#: The stages at which no week has been approved for payment yet. The
#: prototype's first branch: an intake or investigation claim's whole schedule
#: is a projection awaiting approval, however old the claim is.
UNAPPROVED_STAGES: Final[frozenset[Stage]] = frozenset({Stage.intake, Stage.investigation})


class ScheduleClaim(Protocol):
    """The three claim columns the projection reads.

    Structural rather than `Claim`, for `BenefitClaim`'s reason: a service may
    pass a row projection and a test a small stand-in, and the read surface is
    documented by being written down.
    """

    @property
    def doi(self) -> date: ...
    @property
    def recovery(self) -> RecoveryWindow: ...
    @property
    def stage(self) -> Stage: ...


@dataclass(frozen=True)
class ScheduleWeek:
    """One week of the projected indemnity schedule.

    `week` is 1-based because it is a label a handler reads ("Wk 3"), not an
    index. `start` and `end` are inclusive, so a week is seven days and
    `end = start + 6` — the prototype's arithmetic, kept because an exclusive
    end would render as the next week's start date in the Bills table.
    """

    week: int
    start: date
    end: date
    amount_cents: int
    status: ScheduleWeekStatus


@dataclass(frozen=True)
class PaymentProjection:
    """A claim's whole projected schedule, and the three totals it implies.

    `remaining_indemnity_cents` is floored at zero rather than allowed to go
    negative. It cannot today — `paid_so_far_cents` is a subset of the same
    weeks that make up `total_scheduled_cents` — but Story 3.3 persists these
    rows and Story 3.4 moves their statuses, and a schedule that shortened
    under a corrected recovery window after weeks had been paid would otherwise
    hand the reserve check a *negative* exposure, which classifies as heavy
    with total confidence. The floor is the prototype's own `Math.max(0, …)`
    at the point it reads this number.
    """

    weeks: tuple[ScheduleWeek, ...]
    weekly_cents: int
    week_count: int
    total_scheduled_cents: int
    paid_so_far_cents: int
    remaining_indemnity_cents: int
    #: The start of the first week that is due or upcoming, or `None`.
    #:
    #: The prototype's `nextDue`, which its Bills header renders, and its
    #: exclusion of `pending_approval` is ported rather than tidied: a claim
    #: whose whole schedule is awaiting approval has no payment *due*, and
    #: answering with the first projected week would put a date beside the word
    #: "due" for money nobody has authorised. Carried here so Story 3.3 does
    #: not scan the rows for it a second time.
    next_due: date | None


def scheduled_weeks(recovery: RecoveryWindow) -> int:
    """How many weeks the schedule runs for, clamped.

    Separate from `project_payments` because it is the one number a caller
    might legitimately want without building a schedule (Story 3.3's Bills
    heading states it), and because stating the clamp once keeps it from being
    re-applied at a call site.
    """
    return max(MIN_WEEKS, min(MAX_WEEKS, SCHEDULE_WEEKS[recovery]))


def _status(*, stage: Stage, week_start: date, week_end: date, as_of: date) -> ScheduleWeekStatus:
    """The prototype's status ladder, in its order.

    Stage first, then the calendar. The order matters: a settled claim's weeks
    are all paid whatever the dates say, and an intake claim's are all pending
    approval however far in the past they fall.
    """
    if stage in UNAPPROVED_STAGES:
        return ScheduleWeekStatus.pending_approval
    if stage is Stage.settled:
        return ScheduleWeekStatus.paid
    if week_end < as_of:
        return ScheduleWeekStatus.paid
    if week_start <= as_of <= week_end:
        return ScheduleWeekStatus.due_this_week
    return ScheduleWeekStatus.upcoming


def project_payments(
    claim: ScheduleClaim,
    *,
    weekly_cents: int,
    waiting_days: int,
    as_of: date,
) -> PaymentProjection:
    """The claim's projected week-by-week indemnity schedule.

    `weekly_cents` and `waiting_days` are `services/financials.compute_benefit`'s
    answers, passed in rather than recomputed: the weekly figure exists in
    exactly one place (AD-2) and the waiting period is a `benefit_params`
    parameter that Story 3.1 displays and this function is the first to
    *apply* — the first week starts at DOI plus the wait, which is what that
    document's description said 3.3 would do and what 3.2 needed first.

    Pure. `as_of` is required rather than defaulted for
    `OpenDurationDerivation`'s reason: two claims in one request must be
    projected against one day.
    """
    week_count = scheduled_weeks(claim.recovery)
    first_start = claim.doi + timedelta(days=waiting_days)

    weeks: list[ScheduleWeek] = []
    paid_so_far_cents = 0
    next_due: date | None = None
    for index in range(week_count):
        start = first_start + timedelta(days=index * DAYS_PER_WEEK)
        end = start + timedelta(days=DAYS_PER_WEEK - 1)
        status = _status(stage=claim.stage, week_start=start, week_end=end, as_of=as_of)
        if status is ScheduleWeekStatus.paid:
            paid_so_far_cents += weekly_cents
        elif next_due is None and status is not ScheduleWeekStatus.pending_approval:
            next_due = start
        weeks.append(
            ScheduleWeek(
                week=index + 1,
                start=start,
                end=end,
                amount_cents=weekly_cents,
                status=status,
            )
        )

    total_scheduled_cents = weekly_cents * week_count
    return PaymentProjection(
        weeks=tuple(weeks),
        weekly_cents=weekly_cents,
        week_count=week_count,
        total_scheduled_cents=total_scheduled_cents,
        paid_so_far_cents=paid_so_far_cents,
        remaining_indemnity_cents=max(0, total_scheduled_cents - paid_so_far_cents),
        next_due=next_due,
    )
