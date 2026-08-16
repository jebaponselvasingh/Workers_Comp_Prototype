"""The payment batch (Story 3.4, AC 2) — the only `payment_scheduled → paid`.

Approval queues money; this disburses it. One command, three statements, one
transaction, and a per-row audit event for each transition (AC 3 asks for
"each transition", so the events are not collapsed into one per run: a batch
that paid 40 rows across 12 claims leaves 40 rows in the log, because the
question an auditor asks is "when was *this* payment made", not "how big was
Tuesday's file").

## Idempotence is structural, not bookkept

Each statement is `UPDATE … SET status = 'paid' WHERE status =
'payment_scheduled'`. A second run selects nothing, because the first run's
rows are no longer `payment_scheduled` — there is no "already processed" flag
to check, no run ledger to consult, and therefore nothing that can be out of
step with the rows themselves. Running with nothing eligible is a clean no-op
that writes no audit rows at all.

That is the same shape `materialize_schedule` uses for the same reason, and it
is what AD-4 means by refusing unguarded read-modify-writes: a SELECT-then-
UPDATE batch would double-pay under two schedulers, and "there is only ever one
scheduler" is exactly the assumption that stops being true first.

## What the batch may set `paid` on, and the one honest caveat

The story's invariant is "nothing else in the codebase ever sets `paid`". For
`bill` and `expense` that is exactly, checkably true, and
`tests/test_payment_ownership.py` greps the tree to keep it true.

For `payment_schedule_week` it needs one qualification, and it is better stated
than quietly relied on. The *projection* also produces `paid`: a treatment
claim's elapsed weeks and a settled claim's whole schedule come back `paid`
from `services/financials/schedule.py`, which is the prototype's own rule and
the reason the seeded portfolio has any disbursement history at all. So a week
can reach `paid` two ways — by the calendar moving past it, or by this batch.

What is true without qualification, and what the story's invariant is actually
about, is the transition that matters:

> **An *approved* week is paid only here.**

`materialize_schedule` never rewrites a week whose status is
`payment_scheduled` or `paid` (`DECIDED_STATUSES`), so once a handler has
approved a week, the calendar cannot touch it and the batch is its only exit.
The Excel's row-29-versus-row-64 conflict is about approval, and that half is
absolute: no approval command anywhere writes `paid`.

The residual — an unapproved week going `upcoming → paid` because seven days
passed — is demo-data behaviour inherited from the prototype, recorded in
`deferred-work.md` rather than silently changed here. Changing it would mean
every open claim's schedule showing nothing disbursed until somebody clicked
twenty weeks of approvals, which is a decision about what the seeded portfolio
*means* and belongs to whoever owns that data.

## The actor

The batch has no request behind it, so it runs as the seeded system
`app_user` — `data/models/enums.py::UserRole` argues why that exists and why
the alternatives were worse. `system_context` builds its `CallerContext` from
the row, and the row's `scope_all` makes the AD-7 predicate `TRUE` because the
batch's scope genuinely is the whole portfolio.

`run_payment_batch` still takes a context rather than resolving one, so that a
caller *can* narrow it. That is not hypothetical: the e2e harness triggers the
batch through an admin route under the requesting persona, which pays that
handler's book and nothing else — a smaller, more deterministic run than a
portfolio-wide one, using the same command.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, Bill, Expense, PaymentScheduleWeek
from data.models.enums import SYSTEM_ACTOR_NAME, LineItemStatus, ScheduleWeekStatus, UserRole
from data.repositories import claims as claim_repo
from services import audit

log = structlog.get_logger()

#: The audit `action` every row transition records. One name, so "when was
#: this claim's indemnity actually disbursed" is one filter on the log —
#: and deliberately *not* the three approval actions' shape (one per entity),
#: because the answer to "what paid this" is the same event whichever table
#: the row was in.
BATCH_ACTION: Final[str] = "payment_batch.pay"


@dataclass(frozen=True)
class _Target:
    """One table the batch transitions, with its two status values.

    A table rather than three near-identical blocks, for
    `transition_payment_row_cas`'s reason: the statements differ only by a
    class name and a vocabulary, and the first copy to drift would be the one
    that forgot a guard.
    """

    entity: str
    table: type[PaymentScheduleWeek] | type[Bill] | type[Expense]
    scheduled: Any
    paid: Any


TARGETS: Final[tuple[_Target, ...]] = (
    _Target(
        entity="payment_schedule_week",
        table=PaymentScheduleWeek,
        scheduled=ScheduleWeekStatus.payment_scheduled,
        paid=ScheduleWeekStatus.paid,
    ),
    _Target(
        entity="bill",
        table=Bill,
        scheduled=LineItemStatus.payment_scheduled,
        paid=LineItemStatus.paid,
    ),
    _Target(
        entity="expense",
        table=Expense,
        scheduled=LineItemStatus.payment_scheduled,
        paid=LineItemStatus.paid,
    ),
)


@dataclass(frozen=True)
class PaymentBatchRun:
    """What one run did.

    Counts and claim ids, never amounts. The batch's job is to move statuses;
    what those rows are worth is `services/financials/summary.py`'s question,
    answered from the rows themselves — and a total computed here would be a
    second answer to it (AD-10). `claims_touched` is a sorted tuple of business
    ids so a caller can invalidate exactly the right cache entries and a test
    can assert on it without ordering flakiness.
    """

    rows_paid_by_entity: dict[str, int]
    claims_touched: tuple[str, ...]

    @property
    def rows_paid(self) -> int:
        return sum(self.rows_paid_by_entity.values())

    @property
    def is_empty(self) -> bool:
        return self.rows_paid == 0


async def system_context(db: AsyncSession) -> CallerContext:
    """The batch's caller context, resolved from the seeded system actor.

    Resolved per run rather than cached, which is AD-7's rule for *every*
    context and applies here for a concrete reason as well as a formal one:
    the api process is long-lived, and a context built once at startup would
    survive a change to the row it was built from.

    Raises rather than inventing an actor if the row is missing. A batch that
    silently ran unaudited because a migration had not been applied is the
    exact failure AD-4 exists to prevent, and "no actor" is a deployment error
    with a one-line fix, not a state worth degrading into.
    """
    user = (
        await db.scalars(
            sa.select(AppUser).where(
                AppUser.name == SYSTEM_ACTOR_NAME, AppUser.role == UserRole.system
            )
        )
    ).one_or_none()
    if user is None:
        raise RuntimeError(
            f"no system actor {SYSTEM_ACTOR_NAME!r} in app_user — migration "
            "0029_system_actor has not been applied, and the payment batch "
            "cannot write an audit event without one (AD-4)"
        )
    return CallerContext(
        user_id=user.id,
        # `ALL_EMPLOYERS` from the column, through the same `scope_all` reading
        # `api/deps.get_caller_context` applies — not hardcoded here, so a row
        # edited to be scoped would produce a scoped batch rather than a
        # portfolio-wide one that ignores its own configuration.
        role=user.role,
        employer_ids=ALL_EMPLOYERS if user.scope_all else frozenset(),
    )


async def run_payment_batch(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    now: datetime | None = None,
) -> PaymentBatchRun:
    """Disburse every approved payment in the caller's scope. Idempotent.

    Three UPDATE … WHERE status statements and one audit event per row moved,
    all in one transaction: either the whole file went out and the whole log
    records it, or neither did.

    **Directly invocable, and that is a requirement rather than a convenience**
    (the story asks for it in as many words). The scheduler is one caller; the
    e2e harness's admin route is another; a unit test is a third. Nothing about
    this function knows a scheduler exists, which is what keeps the deferred
    "APScheduler versus worker container" decision from reaching the command.

    **Nothing is committed when nothing moved.** An empty run issues three
    UPDATEs that match no rows, writes no audit events, and commits an empty
    transaction — cheap, and more importantly silent in the audit log. A batch
    that logged "ran, paid nothing" twice a week for a year would bury the runs
    that did something.
    """
    at = now or datetime.now(UTC)
    paid_rows: dict[str, list[tuple[int, int]]] = {}

    for target in TARGETS:
        moved = await claim_repo.pay_scheduled_rows(
            db,
            ctx,
            target.table,
            scheduled_status=target.scheduled,
            paid_status=target.paid,
        )
        paid_rows[target.entity] = [(int(row_id), int(claim_pk)) for row_id, claim_pk in moved]

    claim_pks = {claim_pk for rows in paid_rows.values() for _, claim_pk in rows}
    refs = await claim_repo.select_claim_refs(db, ctx, sorted(claim_pks))

    for target in TARGETS:
        for row_id, claim_pk in paid_rows[target.entity]:
            await audit.record(
                db,
                ctx,
                action=BATCH_ACTION,
                entity=target.entity,
                # The claim's business id, matching the approval commands, so
                # the approval and the disbursement of one payment are two
                # rows a reader can put side by side with one filter.
                entity_id=refs[claim_pk],
                before={"status": target.scheduled.value, "row_id": row_id},
                after={"status": target.paid.value, "row_id": row_id},
                at=at,
            )

    run = PaymentBatchRun(
        rows_paid_by_entity={entity: len(rows) for entity, rows in paid_rows.items()},
        claims_touched=tuple(sorted(refs.values())),
    )

    await db.commit()

    if not run.is_empty:
        # Counts and ids only — never an amount, never a claimant (AD-11).
        log.info(
            "payment_batch.completed",
            rows_paid=run.rows_paid,
            by_entity=run.rows_paid_by_entity,
            claims=len(run.claims_touched),
        )
    return run


def entity_names() -> Sequence[str]:
    """The three tables the batch transitions, for tests and for the ownership
    guard — read off `TARGETS` rather than restated, so a fourth table added
    above is covered without anybody remembering to widen a list."""
    return tuple(target.entity for target in TARGETS)


__all__ = [
    "BATCH_ACTION",
    "TARGETS",
    "PaymentBatchRun",
    "entity_names",
    "run_payment_batch",
    "system_context",
]
