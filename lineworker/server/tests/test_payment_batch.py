"""Story 3.4 AC 2 and AC 3 — the payment batch, against a real database.

Four claims are made here and each has a test that could fail:

1. The batch moves `payment_scheduled` rows to `paid`, on all three tables.
2. It moves **nothing else** — the four other statuses are untouched, which is
   what makes "status-guarded" a property rather than a comment.
3. A second run is a clean no-op, and that is idempotence *by construction*
   rather than by a run ledger: the first run's rows are no longer eligible.
4. The figures a handler reads recompute afterwards, through 3.3's registered
   derivations and nothing else.

Plus the two things about the batch that are not about payments at all: the
system actor it audits under, and the scheduler that decides when it runs.

**These tests mutate the seeded portfolio.** `seeded_db_url` is module-scoped,
so mutations persist between tests in this file — every test below sets up the
state it needs by SQL rather than assuming what the last one left.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, AuditEvent, Bill, Claim, Expense, PaymentScheduleWeek
from data.models.enums import (
    SYSTEM_ACTOR_NAME,
    LineItemStatus,
    ScheduleWeekStatus,
    UserRole,
)
from data.repositories.identity import employer_ids_for
from services.financials.batch import BATCH_ACTION, run_payment_batch, system_context
from services.jobs import JobRunner, ScheduledJob, weekly_on
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

KAYA = ("Kaya Johnson", "handler")
SARAH = ("Sarah Williams", "handler")

#: Every status that is *not* eligible for the batch, per table. Written out
#: rather than derived as "the enum minus one member", because the assertion
#: they support is that the batch touches none of them — and a set computed
#: from the same enum the code reads would agree with a bug that widened it.
UNTOUCHED_WEEK_STATUSES = (
    ScheduleWeekStatus.pending_approval,
    ScheduleWeekStatus.due_this_week,
    ScheduleWeekStatus.upcoming,
    ScheduleWeekStatus.paid,
)
UNTOUCHED_LINE_ITEM_STATUSES = (
    LineItemStatus.pending_submission,
    LineItemStatus.under_review,
    LineItemStatus.paid,
)


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def context_for(db: AsyncSession, name: str, role: str) -> CallerContext:
    user = (
        await db.scalars(sa.select(AppUser).where(AppUser.name == name, AppUser.role == role))
    ).one()
    return CallerContext(
        user_id=user.id,
        role=user.role,
        employer_ids=ALL_EMPLOYERS if user.scope_all else await employer_ids_for(db, user.id),
    )


async def claim_pk_of(db: AsyncSession, claim_business_id: str) -> int:
    return (await db.scalars(sa.select(Claim.id).where(Claim.claim_id == claim_business_id))).one()


def a_claim_of(persona: tuple[str, str], index: int = 0) -> str:
    claims = sorted(str(claim["claim_id"]) for claim in seed_fixture.claims_for(*persona))
    return claims[index]


async def set_status(db: AsyncSession, table: Any, row_id: int, status: Any) -> None:
    await db.execute(sa.update(table).where(table.id == row_id).values(status=status))
    await db.commit()
    db.expire_all()


async def status_of(db: AsyncSession, table: Any, row_id: int) -> Any:
    db.expire_all()
    return (await db.scalars(sa.select(table.status).where(table.id == row_id))).one()


async def first_row_id(db: AsyncSession, table: Any, claim_pk: int, offset: int = 0) -> int:
    ids = (
        await db.scalars(sa.select(table.id).where(table.claim_id == claim_pk).order_by(table.id))
    ).all()
    assert len(ids) > offset, f"claim {claim_pk} has no {table.__name__} at offset {offset}"
    return int(ids[offset])


# --- AC 2: what the batch moves ------------------------------------------


async def test_the_batch_pays_every_approved_row_on_all_three_tables(
    db: AsyncSession, seeded_db_url: str
) -> None:
    claim_id = a_claim_of(KAYA, 0)
    claim_pk = await claim_pk_of(db, claim_id)

    week_id = await first_row_id(db, PaymentScheduleWeek, claim_pk)
    bill_id = await first_row_id(db, Bill, claim_pk)
    expense_id = await first_row_id(db, Expense, claim_pk)
    await set_status(db, PaymentScheduleWeek, week_id, ScheduleWeekStatus.payment_scheduled)
    await set_status(db, Bill, bill_id, LineItemStatus.payment_scheduled)
    await set_status(db, Expense, expense_id, LineItemStatus.payment_scheduled)

    run = await run_payment_batch(db, await context_for(db, *KAYA))

    assert run.rows_paid_by_entity["payment_schedule_week"] >= 1
    assert run.rows_paid_by_entity["bill"] >= 1
    assert run.rows_paid_by_entity["expense"] >= 1
    assert claim_id in run.claims_touched
    assert await status_of(db, PaymentScheduleWeek, week_id) is ScheduleWeekStatus.paid
    assert await status_of(db, Bill, bill_id) is LineItemStatus.paid
    assert await status_of(db, Expense, expense_id) is LineItemStatus.paid


@pytest.mark.parametrize("status", UNTOUCHED_WEEK_STATUSES)
async def test_the_batch_leaves_every_other_week_status_alone(
    db: AsyncSession, seeded_db_url: str, status: ScheduleWeekStatus
) -> None:
    """The status guard, walked over the whole vocabulary.

    `paid` is in this list too, and deliberately: a batch that re-selected
    already-paid rows would emit a second disbursement audit event for money
    that moved once. That the row's *value* would not change is not the point —
    the log would be wrong.
    """
    claim_pk = await claim_pk_of(db, a_claim_of(KAYA, 1))
    week_id = await first_row_id(db, PaymentScheduleWeek, claim_pk)
    await set_status(db, PaymentScheduleWeek, week_id, status)

    before = await audit_count(db)
    await run_payment_batch(db, await context_for(db, *KAYA))

    assert await status_of(db, PaymentScheduleWeek, week_id) is status
    if status is not ScheduleWeekStatus.paid:
        assert await audit_count(db) == before, "the batch audited a row it did not move"


@pytest.mark.parametrize("status", UNTOUCHED_LINE_ITEM_STATUSES)
async def test_the_batch_leaves_every_other_line_item_status_alone(
    db: AsyncSession, seeded_db_url: str, status: LineItemStatus
) -> None:
    claim_pk = await claim_pk_of(db, a_claim_of(KAYA, 2))
    bill_id = await first_row_id(db, Bill, claim_pk)
    await set_status(db, Bill, bill_id, status)

    await run_payment_batch(db, await context_for(db, *KAYA))

    assert await status_of(db, Bill, bill_id) is status


async def test_a_second_run_pays_nothing(db: AsyncSession, seeded_db_url: str) -> None:
    """Idempotence by construction — the acceptance criterion, exactly.

    Nothing is remembered between runs. The second run selects nothing because
    the first run's rows are no longer `payment_scheduled`, which is a property
    of the statement rather than of any bookkeeping that could drift from it.
    """
    claim_pk = await claim_pk_of(db, a_claim_of(KAYA, 3))
    week_id = await first_row_id(db, PaymentScheduleWeek, claim_pk)
    await set_status(db, PaymentScheduleWeek, week_id, ScheduleWeekStatus.payment_scheduled)
    ctx = await context_for(db, *KAYA)

    first = await run_payment_batch(db, ctx)
    audited = await audit_count(db)
    second = await run_payment_batch(db, ctx)

    assert first.rows_paid >= 1
    assert second.rows_paid == 0
    assert second.is_empty
    assert second.claims_touched == ()
    # And the no-op is silent: an empty run writes no audit rows at all, or the
    # log would fill with "paid nothing" twice a week for ever.
    assert await audit_count(db) == audited


async def test_a_run_with_nothing_eligible_is_a_clean_no_op(
    db: AsyncSession, seeded_db_url: str
) -> None:
    ctx = await context_for(db, *KAYA)
    await run_payment_batch(db, ctx)  # drain anything a previous test scheduled

    run = await run_payment_batch(db, ctx)

    assert run.is_empty
    assert run.rows_paid_by_entity == {"payment_schedule_week": 0, "bill": 0, "expense": 0}


# --- AC 3: the audit trail ------------------------------------------------


async def audit_count(db: AsyncSession) -> int:
    db.expire_all()
    return (await db.scalars(sa.select(sa.func.count()).select_from(AuditEvent))).one()


async def test_every_transition_carries_its_own_audit_event(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """AC 3 says "each transition", so the events are not collapsed per run.

    Three rows are scheduled and the batch is run once; three events appear.
    The question an auditor asks is "when was *this* payment made", and one
    row per run could not answer it.
    """
    claim_id = a_claim_of(KAYA, 4)
    claim_pk = await claim_pk_of(db, claim_id)
    week_ids = [
        await first_row_id(db, PaymentScheduleWeek, claim_pk, offset) for offset in range(3)
    ]
    for week_id in week_ids:
        await set_status(db, PaymentScheduleWeek, week_id, ScheduleWeekStatus.payment_scheduled)

    before = await batch_events(db, claim_id)
    await run_payment_batch(db, await context_for(db, *KAYA))
    after = await batch_events(db, claim_id)

    assert len(after) - len(before) == 3
    for event in after[len(before) :]:
        assert event.entity == "payment_schedule_week"
        # Both sides of the transition, and the row it was — never an amount,
        # never a claimant (AD-11).
        assert event.before is not None and event.before["status"] == "payment_scheduled"
        assert event.after is not None and event.after["status"] == "paid"
        assert set(event.after) == {"status", "row_id"}
    assert {event.after["row_id"] for event in after[len(before) :]} == set(week_ids)  # type: ignore[index]


async def batch_events(db: AsyncSession, claim_id: str) -> list[AuditEvent]:
    db.expire_all()
    return list(
        (
            await db.scalars(
                sa.select(AuditEvent)
                .where(AuditEvent.action == BATCH_ACTION)
                .where(AuditEvent.entity_id == claim_id)
                .order_by(AuditEvent.id)
            )
        ).all()
    )


async def test_the_batch_audits_under_the_system_actor(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """The actor is honest: a machine, not whichever handler happened to be on.

    Run under `system_context` rather than a persona's, which is what the
    scheduler does — and the audit row names the seeded system `app_user` with
    `actor_role: system`, so a reader can filter the log for "what did a human
    do" and get an answer.
    """
    claim_pk = await claim_pk_of(db, a_claim_of(KAYA, 5))
    week_id = await first_row_id(db, PaymentScheduleWeek, claim_pk)
    await set_status(db, PaymentScheduleWeek, week_id, ScheduleWeekStatus.payment_scheduled)

    ctx = await system_context(db)
    assert ctx.role is UserRole.system
    # `scope_all` on the row, so the predicate is AD-7's tautology rather than
    # a skipped filter — the batch's scope genuinely is the whole portfolio.
    assert ctx.scopes_all_employers

    await run_payment_batch(db, ctx)

    event = (
        await db.scalars(
            sa.select(AuditEvent)
            .where(AuditEvent.action == BATCH_ACTION)
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
    ).one()
    actor = (await db.scalars(sa.select(AppUser).where(AppUser.id == event.actor_id))).one()
    assert actor.name == SYSTEM_ACTOR_NAME
    assert event.actor_role is UserRole.system


# --- AD-7: the batch is scoped, and its scope is real --------------------


async def test_a_scoped_run_pays_only_that_book(db: AsyncSession, seeded_db_url: str) -> None:
    """The employer predicate is not decoration.

    Sarah's claim is approved and *Kaya's* context runs the batch. Nothing
    moves — which is what makes the scope on this statement a fact rather than
    a comment, and is exactly what the e2e's admin trigger relies on when it
    runs the batch as the requesting handler.
    """
    sarah_only = next(
        claim
        for claim in sorted(str(c["claim_id"]) for c in seed_fixture.claims_for(*SARAH))
        if claim not in {str(c["claim_id"]) for c in seed_fixture.claims_for(*KAYA)}
    )
    claim_pk = await claim_pk_of(db, sarah_only)
    week_id = await first_row_id(db, PaymentScheduleWeek, claim_pk)
    await set_status(db, PaymentScheduleWeek, week_id, ScheduleWeekStatus.payment_scheduled)

    run = await run_payment_batch(db, await context_for(db, *KAYA))
    assert sarah_only not in run.claims_touched
    assert await status_of(db, PaymentScheduleWeek, week_id) is ScheduleWeekStatus.payment_scheduled

    run = await run_payment_batch(db, await context_for(db, *SARAH))
    assert sarah_only in run.claims_touched
    assert await status_of(db, PaymentScheduleWeek, week_id) is ScheduleWeekStatus.paid


# --- AC 3: the figures recompute -----------------------------------------


async def test_the_figures_move_with_the_rows(db: AsyncSession, seeded_db_url: str) -> None:
    """Paid To Date, Installments Paid and Next Payment Due, after a batch.

    Read through the assembler rather than the API so the assertion is about
    the *derivations* (AD-10) rather than about a route: nothing new computes
    these figures, so what the batch changes is the rows and the existing
    computers answer differently.
    """
    from rules.parameters import thresholds_for
    from services.claims.detail import claim_financial_detail

    ctx = await context_for(db, *KAYA)
    weekdays = frozenset({1, 4})
    await thresholds_for(db, datetime.now(UTC).date())

    # **Searched rather than picked by index**, for the reason
    # `tests/test_payment_approval.py::claim_with_approvable` records at
    # length: a claim old enough has every week `paid` by the calendar, so
    # "a claim" and "a claim with a week left to decide" are different sets.
    # Skipping would have made this acceptance criterion silently uncovered.
    claim_id = ""
    before = None
    week_no = 0
    for candidate_id in sorted(str(c["claim_id"]) for c in seed_fixture.claims_for(*KAYA)):
        payload = await claim_financial_detail(db, ctx, candidate_id, batch_weekdays=weekdays)
        undecided = [
            week
            for week in payload.schedule
            if week.status
            in (
                ScheduleWeekStatus.pending_approval,
                ScheduleWeekStatus.due_this_week,
                ScheduleWeekStatus.upcoming,
            )
        ]
        if undecided:
            claim_id, before, week_no = candidate_id, payload, undecided[0].week_no
            break
    assert before is not None, "no claim in Kaya's book has an undecided week"
    claim_pk = await claim_pk_of(db, claim_id)
    week_id = (
        await db.scalars(
            sa.select(PaymentScheduleWeek.id)
            .where(PaymentScheduleWeek.claim_id == claim_pk)
            .where(PaymentScheduleWeek.week_no == week_no)
        )
    ).one()
    await set_status(db, PaymentScheduleWeek, week_id, ScheduleWeekStatus.payment_scheduled)

    await run_payment_batch(db, ctx)
    db.expire_all()
    after = await claim_financial_detail(db, ctx, claim_id, batch_weekdays=weekdays)

    assert after.summary.installments_paid == before.summary.installments_paid + 1
    assert after.summary.disbursed_indemnity_cents > before.summary.disbursed_indemnity_cents
    if not before.summary.paid_from_columns:
        # The effective breakdown reads the live rows, so a newly paid week
        # moves the headline figure. On a claim answering from its `paid_*`
        # columns it would not, which is `paid_to_date`'s all-or-nothing rule
        # rather than a failure here.
        assert after.summary.paid_to_date_cents > before.summary.paid_to_date_cents


# --- the scheduler ------------------------------------------------------


def at(day: str, hour: int = 9) -> datetime:
    """A datetime on a named 2026 weekday. 2026-08-10 is a Monday."""
    offsets = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    return datetime(2026, 8, 10, hour, tzinfo=UTC) + timedelta(days=offsets[day])


async def test_the_batch_job_fires_only_on_its_configured_days() -> None:
    """`weekly_on` over a whole week, without a clock.

    The reason `tick` takes its `now` as an argument: a week runs through the
    scheduler in microseconds and the assertion is exact, rather than a test
    that sleeps and hopes.
    """
    runner = JobRunner(tick_seconds=1)
    runner.register(ScheduledJob(name="batch", due=weekly_on(frozenset({1, 4})), run=_noop))

    fired = [
        day
        for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        if await runner.tick(at(day))
    ]
    assert fired == ["tue", "fri"]


async def test_the_batch_job_fires_once_per_day_however_often_it_is_ticked() -> None:
    """A tick is a poll, not a schedule.

    Five ticks on one Tuesday run the batch once. Conflating the poll interval
    with the cadence is how a twice-weekly job ends up running every five
    minutes.
    """
    runner = JobRunner(tick_seconds=1)
    runner.register(ScheduledJob(name="batch", due=weekly_on(frozenset({1})), run=_noop))

    fired = [await runner.tick(at("tue", hour)) for hour in (8, 9, 12, 17, 23)]
    assert [bool(f) for f in fired] == [True, False, False, False, False]
    assert await runner.tick(at("tue", 9) + timedelta(days=7)), "the next Tuesday runs again"


async def test_a_failing_job_does_not_stop_the_next_one() -> None:
    """A scheduler that dies on one job's exception takes every other job with
    it, silently, until somebody notices a thing did not happen."""
    ran: list[str] = []
    runner = JobRunner(tick_seconds=1)
    runner.register(ScheduledJob(name="boom", due=lambda *_: True, run=_raise))
    runner.register(
        ScheduledJob(name="second", due=lambda *_: True, run=lambda: _record(ran, "second"))
    )

    assert await runner.tick(at("mon")) == ("boom", "second")
    assert ran, "the second job did not run"


def test_a_duplicate_job_name_is_refused() -> None:
    runner = JobRunner(tick_seconds=1)
    runner.register(ScheduledJob(name="batch", due=lambda *_: True, run=_noop))
    with pytest.raises(ValueError, match="already registered"):
        runner.register(ScheduledJob(name="batch", due=lambda *_: True, run=_noop))


async def _noop() -> None:
    return None


async def _raise() -> None:
    raise RuntimeError("the job failed")


async def _record(sink: list[str], name: str) -> None:
    sink.append(name)
