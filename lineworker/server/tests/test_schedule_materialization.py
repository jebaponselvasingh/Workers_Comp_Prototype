"""Story 3.3 AC 2 — persisting the schedule without clobbering decisions.

`test_payment_projection.py` proves what a claim's schedule *is*; this is about
what happens when it meets rows that already exist. That is the failure the
architecture spine names by name (AD-12): a regeneration that overwrites a
status a human decision put there silently revokes approvals, and it does so on
a code path nobody thinks of as a write — opening the Bills tab.

Most of this file drives the **pure planner**, which is what lets a property
range over schedules and stored states without a database. The three tests at
the end drive the command itself, because "writes only when something changed"
is a claim about a session and an audit log, not about a plan.
"""

from collections.abc import AsyncIterator
from datetime import date, timedelta

import pytest
import sqlalchemy as sa
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AuditEvent, Claim, PaymentScheduleWeek
from data.models.enums import RecoveryWindow, ScheduleWeekStatus, Stage, UserRole
from rules.engine import utc_today
from rules.parameters import thresholds_for
from services.financials import (
    DECIDED_STATUSES,
    MATERIALIZE_ACTION,
    benefit_for_claim,
    materialize_schedule,
    plan_materialization,
    project_payments,
)
from services.financials.schedule import ScheduleWeek
from tests.conftest import requires_db


def as_of() -> date:
    """The reference date the DB-backed tests project against.

    **`utc_today()` rather than a fixed date, which is the opposite of the rule
    the pure tests follow** — and the exception is the point. Every test above
    names its date because the date is an *input* there, and a test whose
    result depends on when it runs is a test that will one day fail for no
    reason. Here the date is not an input: these three assert agreement with
    what migration 0028 wrote, and 0028 materialized against `utc_today()`. A
    literal pinned to the day this file was written would agree with the seed
    exactly once and then quietly start measuring drift instead of behaviour.

    The same function the migration and the service use, so all three read one
    clock (`rules/engine.py` explains why it is UTC rather than the host's).
    """
    return utc_today()


# --- stand-ins -----------------------------------------------------------


class FakeClaim:
    """The three columns `project_payments` reads."""

    def __init__(self, *, doi: date, recovery: RecoveryWindow, stage: Stage) -> None:
        self.doi = doi
        self.recovery = recovery
        self.stage = stage


class StoredRow:
    """A `payment_schedule_week` row, structurally — see `StoredWeek`."""

    def __init__(
        self,
        week_no: int,
        *,
        status: ScheduleWeekStatus,
        start: date = date(2026, 4, 5),
        end: date = date(2026, 4, 11),
        amount_cents: int = 100_000,
    ) -> None:
        self.week_no = week_no
        self.period_start = start
        self.period_end = end
        self.amount_cents = amount_cents
        self.status = status


def projection(
    *,
    stage: Stage = Stage.treatment,
    recovery: RecoveryWindow = RecoveryWindow.weeks_4_6,
    doi: date = date(2026, 3, 1),
    weekly_cents: int = 100_000,
    as_of: date = date(2026, 4, 20),
) -> tuple[ScheduleWeek, ...]:
    return project_payments(
        FakeClaim(doi=doi, recovery=recovery, stage=stage),
        weekly_cents=weekly_cents,
        waiting_days=7,
        as_of=as_of,
    ).weeks


def stored_from(weeks: tuple[ScheduleWeek, ...]) -> list[StoredRow]:
    """The rows a materialization of `weeks` would have left behind."""
    return [
        StoredRow(
            week.week,
            status=week.status,
            start=week.start,
            end=week.end,
            amount_cents=week.amount_cents,
        )
        for week in weeks
    ]


# --- the empty and the unchanged cases ------------------------------------


def test_an_unmaterialized_claim_is_all_inserts() -> None:
    weeks = projection()
    plan = plan_materialization(weeks, [])

    assert len(plan.inserts) == len(weeks)
    assert plan.updates == ()
    assert plan.deletes == ()
    assert not plan.is_empty()


def test_regeneration_on_unchanged_inputs_is_a_no_op() -> None:
    """The property the read path's affordability rests on.

    Both financial surfaces call the command before reading, so this being a
    no-op is what keeps a GET from writing on every request. Asserted rather
    than assumed, because "this is usually free" is exactly the kind of claim
    that quietly stops being true — a stray `version` bump or a date compared
    across types would make every read a write and nothing else would say so.
    """
    weeks = projection()
    plan = plan_materialization(weeks, stored_from(weeks))

    assert plan.is_empty()


# --- the AD-12 clobbering scenario ----------------------------------------


@pytest.mark.parametrize("decided", sorted(DECIDED_STATUSES))
def test_a_decided_week_is_never_updated(decided: ScheduleWeekStatus) -> None:
    """The failure this module exists to prevent.

    A week the calendar now calls `due_this_week`, carrying a status a decision
    put there. The projection disagrees with the row on *every* field — status,
    dates and amount — and the plan still leaves it alone, which is the
    "frozen, not merely status-preserved" rule.
    """
    weeks = projection()
    target = weeks[2]
    stored = stored_from(weeks)
    stored[2] = StoredRow(
        target.week,
        status=decided,
        start=target.start - timedelta(days=70),
        end=target.end - timedelta(days=70),
        amount_cents=target.amount_cents + 12_345,
    )

    plan = plan_materialization(weeks, stored)

    assert plan.is_empty(), f"a {decided} week was rewritten by a regeneration"


def test_an_undecided_week_whose_status_moved_is_updated() -> None:
    """The other half: the calendar's own statuses *are* refreshed.

    Without this, "preserve decided statuses" would be indistinguishable from
    "never update anything", and the schedule would freeze on the day it was
    seeded.
    """
    weeks = projection()
    stored = stored_from(weeks)
    stored[3] = StoredRow(
        weeks[3].week,
        status=ScheduleWeekStatus.upcoming,
        start=weeks[3].start,
        end=weeks[3].end,
        amount_cents=weeks[3].amount_cents,
    )

    plan = plan_materialization(weeks, stored)

    assert [week.week for week in plan.updates] == [weeks[3].week]
    assert plan.updates[0].status is weeks[3].status


def test_a_shortened_schedule_deletes_undecided_weeks_and_keeps_decided_ones() -> None:
    """A recovery window corrected downward, with one week already approved.

    Undecided weeks past the new end are projections of payments that will now
    never happen and go. A decided one is a payment that was approved or made:
    deleting it would erase the record, so it survives its own schedule — a
    state that wants a human rather than a tidy-up.
    """
    long_weeks = projection(recovery=RecoveryWindow.over_1_year)
    stored = stored_from(long_weeks)
    # Week 19 was approved into a batch; week 18 is an ordinary projection.
    stored[18] = StoredRow(19, status=ScheduleWeekStatus.payment_scheduled)

    short_weeks = projection(recovery=RecoveryWindow.weeks_4_6)
    plan = plan_materialization(short_weeks, stored)

    assert len(short_weeks) == 6
    # Everything past week 6 except the approved week 19.
    assert plan.deletes == tuple(n for n in range(7, 21) if n != 19)
    assert 19 not in plan.deletes


def test_a_lengthened_schedule_inserts_the_new_weeks_only() -> None:
    short_weeks = projection(recovery=RecoveryWindow.weeks_4_6)
    long_weeks = projection(recovery=RecoveryWindow.over_1_year)

    plan = plan_materialization(long_weeks, stored_from(short_weeks))

    assert [week.week for week in plan.inserts] == list(range(7, 21))
    assert plan.deletes == ()


def test_a_changed_weekly_amount_rewrites_only_the_undecided_weeks() -> None:
    """A comp-rate override between two materializations.

    The paid weeks keep the amount they were paid at, which is why
    `installments_paid` counts rows rather than dividing a total by a weekly
    figure — see `services/derivations/claim_financials.py`.
    """
    # Mid-schedule deliberately: the module's default reference date is past
    # the end of a six-week window, which would make *every* week paid and
    # leave this test asserting over an empty "rewritten" set — passing while
    # checking nothing.
    part_way = date(2026, 3, 29)
    weeks = projection(weekly_cents=100_000, as_of=part_way)
    stored = stored_from(weeks)
    repriced = projection(weekly_cents=125_000, as_of=part_way)

    plan = plan_materialization(repriced, stored)

    rewritten = {week.week for week in plan.updates}
    frozen = {row.week_no for row in stored if row.status in DECIDED_STATUSES}
    assert rewritten and frozen
    assert rewritten.isdisjoint(frozen)
    assert all(week.amount_cents == 125_000 for week in plan.updates)


# --- properties ------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(
    stage=st.sampled_from(sorted(Stage, key=lambda s: s.value)),
    recovery=st.sampled_from(sorted(RecoveryWindow, key=lambda r: r.value)),
    weekly_cents=st.integers(min_value=0, max_value=5_000_00),
    offset_days=st.integers(min_value=-400, max_value=400),
)
def test_materializing_twice_changes_nothing_the_second_time(
    stage: Stage, recovery: RecoveryWindow, weekly_cents: int, offset_days: int
) -> None:
    """Idempotence, over the whole input space (the story's property test).

    Applying a plan and re-planning must produce an empty plan for *every*
    claim shape, not for the four the examples above happen to use. This is the
    property the read path depends on, so it is the one worth ranging over.
    """
    weeks = projection(
        stage=stage,
        recovery=recovery,
        weekly_cents=weekly_cents,
        as_of=date(2026, 3, 1) + timedelta(days=offset_days),
    )
    first = plan_materialization(weeks, [])
    # Applying the plan is exactly "the rows become the projection".
    second = plan_materialization(weeks, stored_from(weeks))

    assert len(first.inserts) == len(weeks)
    assert second.is_empty()


@settings(max_examples=200, deadline=None)
@given(
    recovery=st.sampled_from(sorted(RecoveryWindow, key=lambda r: r.value)),
    weekly_cents=st.integers(min_value=0, max_value=5_000_00),
)
def test_the_weeks_sum_to_the_weekly_figure_times_the_week_count(
    recovery: RecoveryWindow, weekly_cents: int
) -> None:
    """∀ claims: `sum(week amounts) == weekly × weeks` (the story's property).

    True of a freshly generated schedule, and deliberately *not* asserted of a
    stored one: `test_a_changed_weekly_amount_rewrites_only_the_undecided_weeks`
    is the case where it stops holding, because a paid week keeps its own
    amount. Stating which of the two this covers is the point.
    """
    weeks = projection(recovery=recovery, weekly_cents=weekly_cents)

    assert sum(week.amount_cents for week in weeks) == weekly_cents * len(weeks)


@settings(max_examples=200, deadline=None)
@given(
    statuses=st.lists(
        st.sampled_from(sorted(ScheduleWeekStatus, key=lambda s: s.value)),
        min_size=6,
        max_size=6,
    )
)
def test_no_plan_ever_touches_a_decided_week(statuses: list[ScheduleWeekStatus]) -> None:
    """The AD-12 guarantee as a property, over every arrangement of statuses.

    Whatever the stored rows say, no week whose status is decided appears in
    `updates` or in `deletes`. The parametrized test above proves it for one
    week in one position; this proves there is no arrangement that slips one
    through.
    """
    weeks = projection(recovery=RecoveryWindow.weeks_4_6)
    stored = [StoredRow(index + 1, status=status) for index, status in enumerate(statuses)]
    decided = {row.week_no for row in stored if row.status in DECIDED_STATUSES}

    plan = plan_materialization(weeks, stored)

    assert decided.isdisjoint({week.week for week in plan.updates})
    assert decided.isdisjoint(set(plan.deletes))


# --- the command, against a database --------------------------------------
#
# Everything above is pure. These three are about the *session*: that an
# unchanged claim costs no write and no audit row, that a changed one is
# audited under the AD-4 action name, and that a decided week survives a real
# round trip through the table rather than only through the planner.


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def _a_treatment_claim(db: AsyncSession) -> Claim:
    claim = (
        await db.execute(
            sa.select(Claim).where(Claim.stage == Stage.treatment).order_by(Claim.id).limit(1)
        )
    ).scalar_one()
    return claim


async def _audit_count(db: AsyncSession, claim_ref: str) -> int:
    return (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == MATERIALIZE_ACTION, AuditEvent.entity_id == claim_ref)
        )
    ).scalar_one()


async def _rows(db: AsyncSession, claim_pk: int) -> list[PaymentScheduleWeek]:
    result = await db.execute(
        sa.select(PaymentScheduleWeek)
        .where(PaymentScheduleWeek.claim_id == claim_pk)
        .order_by(PaymentScheduleWeek.week_no)
    )
    return list(result.scalars())


async def _materialize(db: AsyncSession, claim: Claim) -> tuple[PaymentScheduleWeek, ...]:
    today = as_of()
    thresholds = await thresholds_for(db, today)
    benefit = await benefit_for_claim(db, claim, thresholds, today)
    ctx = CallerContext(user_id=claim.handler_id, role=UserRole.handler, employer_ids=ALL_EMPLOYERS)
    return await materialize_schedule(
        db,
        claim,
        claim_pk=claim.id,
        claim_ref=claim.claim_id,
        benefit=benefit,
        as_of=today,
        ctx=ctx,
    )


@requires_db
async def test_the_seeded_schedule_needs_no_write_and_logs_nothing(db: AsyncSession) -> None:
    """The affordability claim, end to end.

    Migration 0028 materialized every claim against `utc_today()`, and these
    tests run the same day, so the first call has nothing to do. If this ever
    fails on a clock boundary it will fail loudly rather than by writing —
    which is the correct direction, and why the assertion is on the audit
    count rather than on wall time.
    """
    claim = await _a_treatment_claim(db)
    before = await _audit_count(db, claim.claim_id)

    await _materialize(db, claim)

    assert await _audit_count(db, claim.claim_id) == before


@requires_db
async def test_a_decided_week_survives_a_real_regeneration(db: AsyncSession) -> None:
    """AD-12's scenario against the table, not the planner.

    A handler approves week 3 into a batch (Story 3.4's transition, written
    here by hand because that command does not exist yet), the calendar is
    wound forward so the projection would call that week `paid`, and the
    regeneration must leave it `payment_scheduled`.
    """
    claim = await _a_treatment_claim(db)
    rows = await _rows(db, claim.id)
    assert rows, "migration 0028 should have materialized this claim"

    target = rows[2]
    target.status = ScheduleWeekStatus.payment_scheduled
    await db.commit()

    today = as_of()
    thresholds = await thresholds_for(db, today)
    benefit = await benefit_for_claim(db, claim, thresholds, today)
    ctx = CallerContext(user_id=claim.handler_id, role=UserRole.handler, employer_ids=ALL_EMPLOYERS)
    # A year on: every week of every schedule has elapsed, so the projection
    # calls all of them `paid`.
    refreshed = await materialize_schedule(
        db,
        claim,
        claim_pk=claim.id,
        claim_ref=claim.claim_id,
        benefit=benefit,
        as_of=today + timedelta(days=365),
        ctx=ctx,
    )

    kept = {row.week_no: row.status for row in refreshed}
    assert kept[target.week_no] is ScheduleWeekStatus.payment_scheduled
    # And the rest did move, so this is not passing because nothing happened.
    assert any(status is ScheduleWeekStatus.paid for status in kept.values())


@requires_db
async def test_a_real_change_is_audited_once_under_the_ad4_action(db: AsyncSession) -> None:
    """AD-4: the write is a command, and it says so in the log.

    One event for a refresh that moved many weeks, not one per week: the audit
    row's `after` names each week and its landing status, which is what an
    auditor asking "which weeks moved, and to what" needs. Twenty rows saying
    the same thing would be the same answer, harder to read.
    """
    claim = await _a_treatment_claim(db)
    for row in await _rows(db, claim.id):
        row.status = ScheduleWeekStatus.upcoming
    await db.commit()
    before = await _audit_count(db, claim.claim_id)

    await _materialize(db, claim)

    assert await _audit_count(db, claim.claim_id) == before + 1
    event = (
        await db.execute(
            sa.select(AuditEvent)
            .where(AuditEvent.action == MATERIALIZE_ACTION, AuditEvent.entity_id == claim.claim_id)
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
    ).scalar_one()
    assert event.entity == "payment_schedule_week"
    assert event.after is not None
    assert event.after["updated"], "the diff should name the weeks that moved"
    # AD-11: week numbers and statuses only — no amounts, no dates.
    assert set(event.after) == {"trigger", "inserted", "updated", "deleted", "weeks_before"}
    # **The trigger, and why it is worth an assertion** (code review). This
    # command runs on a read path, so `actor_role` names whoever opened the
    # claim — including a supervisor or analyst, neither of whom can write
    # anything through any command here. Without this key the log would read as
    # "a supervisor rewrote a payment schedule"; with it, it says what happened.
    assert event.after["trigger"] == "calendar_refresh"
