"""Persisting the payment-schedule projection — the AD-12 write path (3.3).

`services/financials` is the only writer of `payment_schedule_week` (AD-12),
and this module is the only part of it that writes. Story 3.4's worklist
approval calls in here rather than reaching the table, and so does the seed.

## What this is, and the failure it exists to prevent

`schedule.py::project_payments` decides what a claim's schedule *is* — one
generator, AD-2, extended by Story 3.3 rather than forked. This module decides
what to do with rows that already exist, which is a different question and the
one the architecture spine names by name: **regeneration must never clobber a
status that a human decision put there.**

The five statuses split in two, and the partition is the whole design:

- **Calendar-derived** — `pending_approval`, `due_this_week`, `upcoming`. These
  are the projection's answer to "where does this week sit relative to today",
  and they are *worthless* the moment the calendar moves past them. They are
  refreshed on every materialization.
- **Decided** — `payment_scheduled` (a handler approved this week into the next
  batch, Story 3.4) and `paid` (it has been disbursed). These record something
  that happened. Nothing recomputes them.

A **decided week is frozen entirely** — status, dates and amount — not merely
status-preserved. That is stricter than it first needs to be and it is the
right strictness: the amount on a paid week is what was actually disbursed, so
rewriting it from a freshly computed weekly figure (after, say, a comp-rate
override) would quietly restate history. An undecided week, by contrast, is
pure projection and takes whatever the generator now says.

**One consequence worth stating because it looks like a bug.** The projection
also produces `paid`, for weeks that have simply elapsed. So a week can become
decided without anybody deciding anything, and once it has, a later correction
that moves the schedule *forward* (a corrected date of injury, a lengthened
recovery window) leaves those weeks paid although the new projection puts them
in the future. That is deliberate, and it is the safe direction to be wrong in:
the alternative un-pays weeks, and this table's job is to be trustworthy about
money that has moved. A claim whose date of injury was wrong enough to matter
needs its schedule corrected by a command that says so, not by a side effect of
somebody opening the Bills tab.

## Idempotence is a load-bearing property, not an optimisation

Both read surfaces that show financial figures call this before reading — the
case file (for the reserve check's indemnity terms) and the Bills tab's read
model — because AC 4 requires that the two cannot disagree, and a schedule
whose statuses move with the clock can only satisfy that if the refresh happens
ahead of *both* rather than ahead of one. That makes this a write on a read
path, which is worth paying for only because it is a no-op in the overwhelming
majority of cases: a claim's statuses change at most once a week, so
`plan_materialization` returns an empty plan and nothing is written, committed
or audited.

`tests/test_schedule_materialization.py` holds that as a property rather than
as a claim — regeneration on unchanged inputs produces an empty plan — because
"this is usually a no-op" is exactly the kind of statement that silently stops
being true.

## Why the planning half is pure and separate

`plan_materialization` takes a projection and the rows that exist and returns
what to do about it, with no session anywhere near it. Two reasons, and the
second is the practical one:

1. It is what lets a Hypothesis property range over schedules and stored states
   without a database — `compute_benefit`/`benefit_for_claim` split the same
   way for the same reason.
2. **The seed migration cannot call an async command.** Alembic runs
   synchronously and has no `CallerContext` to audit under, so migration 0028
   applies this planner's output with its own connection. That keeps the seed
   going through the one set of *decisions* even though it cannot go through
   the one set of writes — which is the part AD-2 is actually about.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final, Protocol

import sqlalchemy as sa
import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from data.context import CallerContext
from data.models import PaymentScheduleWeek
from data.models.enums import ScheduleWeekStatus
from data.repositories import claims as claim_repo
from services import audit
from services.financials.benefit import Benefit
from services.financials.schedule import ScheduleClaim, ScheduleWeek, project_payments

log = structlog.get_logger()

#: The statuses a regeneration must not overwrite — see the module docstring.
#: `paid` is here although the projection also produces it: once a week is
#: paid, what makes it paid is that money moved, not that a date passed.
DECIDED_STATUSES: Final[frozenset[ScheduleWeekStatus]] = frozenset(
    {ScheduleWeekStatus.payment_scheduled, ScheduleWeekStatus.paid}
)

#: The audit action this command records. One name, so a reader filtering the
#: log for "what has ever written a payment schedule" gets a complete answer.
MATERIALIZE_ACTION: Final[str] = "payment_schedule.materialize"


class StoredWeek(Protocol):
    """One `payment_schedule_week` row, structurally.

    A protocol rather than the ORM class for `ScheduleClaim`'s reason: the
    planner is pure, and a test should be able to hand it five small stand-ins
    without constructing mapped instances or a metadata registry.
    """

    @property
    def week_no(self) -> int: ...
    @property
    def period_start(self) -> date: ...
    @property
    def period_end(self) -> date: ...
    @property
    def amount_cents(self) -> int: ...
    @property
    def status(self) -> ScheduleWeekStatus: ...


@dataclass(frozen=True)
class MaterializationPlan:
    """What a materialization would do to the rows that exist.

    Three disjoint sets keyed by week number. `updates` carries only weeks
    whose stored values actually differ from the projection's, which is what
    makes `is_empty` mean "nothing to write" rather than "nothing to compute" —
    and therefore what makes the no-op case free.
    """

    inserts: tuple[ScheduleWeek, ...]
    updates: tuple[ScheduleWeek, ...]
    #: Week numbers to remove: rows past the end of a schedule that has been
    #: shortened, and only the undecided ones. See `plan_materialization`.
    deletes: tuple[int, ...]

    def is_empty(self) -> bool:
        return not (self.inserts or self.updates or self.deletes)


def _differs(stored: StoredWeek, projected: ScheduleWeek) -> bool:
    return (
        stored.period_start != projected.start
        or stored.period_end != projected.end
        or stored.amount_cents != projected.amount_cents
        or stored.status is not projected.status
    )


def plan_materialization(
    projected: Sequence[ScheduleWeek],
    existing: Iterable[StoredWeek],
) -> MaterializationPlan:
    """What to insert, update and delete to make the stored rows the projection.

    Pure. `projected` is `PaymentProjection.weeks`; `existing` is whatever is
    in the table for this claim, in any order.

    **Decided weeks are skipped wholesale**, on both sides of the comparison:
    a stored week whose status is `payment_scheduled` or `paid` is never
    updated and never deleted, whatever the projection now says about it. The
    module docstring argues why that is stricter than status-preservation and
    why the strictness is the point.

    **Surplus weeks are deleted only when undecided.** A schedule that shortens
    — a handler correcting a recovery window from "over 1 year" down to "2-4
    weeks" — leaves rows past the new end. Undecided ones are projections of
    payments that will now never happen and go; a decided one is a payment that
    was approved or made, and deleting it would erase the record of it. Such a
    week outliving its schedule is a real state that wants a human, which is why
    it is kept and visible rather than tidied away.
    """
    stored = {week.week_no: week for week in existing}

    inserts: list[ScheduleWeek] = []
    updates: list[ScheduleWeek] = []
    for week in projected:
        current = stored.get(week.week)
        if current is None:
            inserts.append(week)
        elif current.status not in DECIDED_STATUSES and _differs(current, week):
            updates.append(week)

    projected_weeks = {week.week for week in projected}
    deletes = tuple(
        sorted(
            week_no
            for week_no, week in stored.items()
            if week_no not in projected_weeks and week.status not in DECIDED_STATUSES
        )
    )

    return MaterializationPlan(inserts=tuple(inserts), updates=tuple(updates), deletes=deletes)


def _diff(plan: MaterializationPlan, existing: Sequence[StoredWeek]) -> dict[str, object]:
    """The audit `after` payload: what this write changed, by week number.

    Week numbers and statuses only — no amounts, no dates, no claim data beyond
    the entity id the event already carries (AD-11). The question an auditor
    asks of this log is "which weeks moved, and to what", and that is answerable
    from exactly this.
    """
    return {
        # **The trigger, and it is not decoration** (code review, 2026-08-14).
        # This command runs on a *read* path, so its `actor_id` and `actor_role`
        # name whoever happened to open the claim first after a week boundary —
        # including a supervisor or analyst, neither of whom may write anything
        # through any command in this system. Under AD-4 the log answers "who
        # changed what", and without this key it would answer that a supervisor
        # rewrote a payment schedule. With it, the row says what it is: a
        # calendar refresh, observed while that user was reading. The actor is
        # still recorded because "who was the request under" is a real question
        # an auditor asks, and `AuditEvent.actor_id` is not nullable.
        "trigger": "calendar_refresh",
        "inserted": {str(week.week): week.status.value for week in plan.inserts},
        "updated": {str(week.week): week.status.value for week in plan.updates},
        "deleted": sorted(plan.deletes),
        "weeks_before": len(existing),
    }


async def _existing(
    db: AsyncSession, ctx: CallerContext, claim_ref: str
) -> list[PaymentScheduleWeek]:
    """This claim's rows, through the scoped repository (AD-7).

    **Was an unscoped query built here** (code review, 2026-08-14). It read the
    same rows — the claim had already been resolved through
    `select_claim_detail` — so nothing was reachable that should not have been,
    but it left `select_payment_schedule` written, documented as this table's
    AD-7 entry point, and called by nothing. A repository function that reads
    as the enforcement point while the service quietly bypasses it is worse
    than no function at all: the next author adds a second bypass on the
    strength of the first.
    """
    return list(await claim_repo.select_payment_schedule(db, ctx, claim_ref))


async def materialize_schedule(
    db: AsyncSession,
    claim: ScheduleClaim,
    *,
    claim_pk: int,
    claim_ref: str,
    benefit: Benefit,
    as_of: date,
    ctx: CallerContext,
) -> tuple[PaymentScheduleWeek, ...]:
    """Refresh this claim's schedule rows and return them, week order.

    `benefit` is handed in rather than recomputed, `reserve_check_for_claim`'s
    rule and AD-2's: `compute_benefit` is the one place a weekly indemnity
    figure exists, and the case file has already asked it.

    **Writes and audits only when something changed.** An unchanged claim costs
    one indexed SELECT and nothing else — no UPDATE, no audit row, no commit —
    which is what makes calling this ahead of every financial read affordable.
    See the module docstring on why both surfaces call it rather than one.

    **`claim_pk` and `claim_ref` are passed rather than read off `claim`**
    because `ScheduleClaim` is deliberately three columns wide (`doi`,
    `recovery`, `stage`) and widening it to carry an id would make every caller
    of the pure projection hold one. The surrogate addresses the rows; the
    business id is what the audit event names, for `AuditEvent`'s convention.

    AD-7 is satisfied a step earlier and in the usual place: the claim reaches
    this service from `select_claim_detail`, which is scoped, so the rows a
    caller can refresh are exactly the ones they can already read.
    `unpaid_medical_cents` and `benefit_for_claim` take their claims on the
    same terms.
    """
    existing = await _existing(db, ctx, claim_ref)
    projection = project_payments(
        claim,
        weekly_cents=benefit.weekly_cents,
        waiting_days=benefit.waiting_days,
        as_of=as_of,
    )
    plan = plan_materialization(projection.weeks, existing)

    if plan.is_empty():
        return tuple(existing)

    for week in plan.inserts:
        db.add(
            PaymentScheduleWeek(
                claim_id=claim_pk,
                week_no=week.week,
                period_start=week.start,
                period_end=week.end,
                amount_cents=week.amount_cents,
                status=week.status,
            )
        )
    if plan.updates:
        by_week = {row.week_no: row for row in existing}
        for week in plan.updates:
            row = by_week[week.week]
            row.period_start = week.start
            row.period_end = week.end
            row.amount_cents = week.amount_cents
            row.status = week.status
            # The row moved, so its compare-and-swap column moves with it —
            # AD-4's convention, and what stops Story 3.4's approval from
            # committing against a week this refresh has already rewritten.
            row.version += 1
    if plan.deletes:
        await db.execute(
            sa.delete(PaymentScheduleWeek).where(
                PaymentScheduleWeek.claim_id == claim_pk,
                PaymentScheduleWeek.week_no.in_(plan.deletes),
            )
        )

    await audit.record(
        db,
        ctx,
        action=MATERIALIZE_ACTION,
        entity="payment_schedule_week",
        entity_id=claim_ref,
        # No `before` document: this is not a field edit with two values, it is
        # a set of rows moving, and `after` already names each week's landing
        # status. A `before` restating the same weeks' old statuses would double
        # the payload to say the same thing twice.
        before=None,
        after=_diff(plan, existing),
    )
    try:
        await db.commit()
    except (IntegrityError, StaleDataError):
        # **Two readers can race here** (code review, 2026-08-14), because this
        # is a write on a read path and the two financial surfaces share a key
        # prefix — one `invalidateQueries` can fire both refetches at once. The
        # loser of a concurrent insert violates `uq_payment_schedule_week`, and
        # the loser of a concurrent update-versus-delete matches zero rows and
        # raises `StaleDataError`. Both were an unhandled 500 on a GET.
        #
        # Losing is harmless and needs no retry: the winner computed the *same*
        # plan from the same inputs — that is what `plan_materialization`'s
        # idempotence property means — so rolling back and re-reading returns
        # exactly the rows this call would have written. What must not happen
        # is a handler seeing a 500 because somebody else opened the claim.
        await db.rollback()
        log.info("payment_schedule.materialize_raced", claim_id=claim_ref)
        return tuple(await _existing(db, ctx, claim_ref))

    return tuple(await _existing(db, ctx, claim_ref))


__all__ = [
    "DECIDED_STATUSES",
    "MATERIALIZE_ACTION",
    "MaterializationPlan",
    "StoredWeek",
    "materialize_schedule",
    "plan_materialization",
]
