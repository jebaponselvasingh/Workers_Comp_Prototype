"""End-of-retention housekeeping — the two floors AD-11 states, enforced.

`purge.py` deletes on demand, for a named subject. This module deletes on a
clock, for everything past its retention floor, and the two floors are
deliberately different numbers because they are different kinds of obligation:

- **`audit_retention_years` (7).** How long an audit row must be *kept*. It is a
  floor rather than a lifetime — the row is evidence that somebody did
  something, and destroying it early is the failure. Enforcement runs under
  `audit_redactor`, whose `DELETE` is bounded by the `audit_redactor_delete` RLS
  policy to rows past the floor, so the database refuses a younger row even if
  this code asks for one.
- **`copilot_checkpoint_retention_days` (90).** How long a conversation may be
  *kept*. A checkpoint quotes a diagnosis, a wage and a fraud indicator back
  verbatim (`config.py` says so at the knob), so here the floor is a ceiling on
  exposure and keeping a transcript longer is the failure.

Both commands live here rather than in the services that own the tables,
because AD-12 registers `services/audit` as the sole PHI deleter and a retention
sweep is a deletion. Both are registered as scheduled jobs by `api/app.py` on
their own interval knobs, and both are directly invocable — the payment batch's
rule, and it is what lets `tests/test_purge_cascade.py` drive a whole retention
window without a clock.

## Why the audit sweep counts before it deletes

The floor lives in *two* places and they can disagree. Migration 0003 bakes the
configured value into the RLS policy at migration time; `Settings
.audit_retention_years` is read at run time. That duplication is deliberate and
`20260822_0049_purge_grants.py` argues it at length — driving the policy from a
`current_setting()` GUC would let the session the policy guards choose its own
floor, which is not a safety net.

The residual is a silent one: set `AUDIT_RETENTION_YEARS=5` without a migration
and this job would compute a five-year cutoff, issue a `DELETE`, and have RLS
quietly refuse every row between five and seven years old. Nothing would raise.
The counts would look like "nothing was due", which is exactly what a job that
had stopped working also looks like.

So the delete is compared against a count taken in the same transaction, and a
shortfall raises `RetentionFloorMismatch` naming both numbers. A loud failure
on a daily job is an operator's morning; a quiet one is an audit log that was
never actually being swept.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast

import sqlalchemy as sa
import structlog
from sqlalchemy import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings
from data.context import CallerContext
from data.models import AuditEvent, CopilotThread
from services import audit
from services.audit._redactor import redactor_connection
from services.audit.purge import CHECKPOINT_STORE, THREAD_ENTITY, ThreadDeleter, in_batches

log = structlog.get_logger()

#: The audit log as a plain Core table — `purge.py`'s `_AUDIT`, restated
#: rather than imported across a module boundary for one alias. Core rather
#: than the ORM entity because the sweep runs on a bare `AsyncConnection`
#: under `SET ROLE audit_redactor`, where there is no session for an
#: ORM-enabled statement to reach for; the `cast` narrows `FromClause` to the
#: `Table` a declarative model's `__table__` always is.
_AUDIT: Final[sa.Table] = cast(sa.Table, AuditEvent.__table__)

#: The `action` the audit sweep's summary row carries.
#:
#: A dotted family (`retention.<what>`) rather than `purge.py`'s flat `purge`,
#: and the difference is what the two rows are *about*. A purge event names the
#: store it emptied in `entity`, so the family is already spelled there. A
#: retention event's `entity` is the table it swept and its family is the
#: *policy* that swept it — "which floor produced this row" is not derivable
#: from `entity` alone, because the audit sweep and the checkpoint sweep would
#: otherwise be indistinguishable from an on-demand purge of the same table.
AUDIT_RETENTION_ACTION: Final[str] = "retention.audit"

#: The `action` the checkpoint sweep's summary row carries.
CHECKPOINT_RETENTION_ACTION: Final[str] = "retention.checkpoint"

#: The `entity` the audit sweep's summary row names.
AUDIT_RETENTION_ENTITY: Final[str] = "audit_event"


class RetentionFloorMismatch(RuntimeError):
    """The RLS policy refused rows this job's configured cutoff had selected.

    The one failure this module exists to make loud. It means `Settings
    .audit_retention_years` and the floor baked into `audit_redactor_delete`
    disagree — almost certainly because the environment variable was lowered
    without the migration that would move the policy with it.

    Raised rather than logged-and-continued because the two outcomes of
    continuing are both bad: the job reports a smaller number than it deleted
    (so the log is wrong), or it reports the count it intended (so the log is a
    lie). A daily job that raises is visible; one that under-deletes for a year
    is discovered at an audit.
    """


@dataclass(frozen=True)
class AuditRetentionRun:
    """What one audit sweep did. A count and the instant it swept up to.

    `cutoff` is on the summary rather than only in the log line because it is
    the one fact that makes the count checkable after the event: "deleted 412
    rows" says nothing without "everything older than this", and the instant is
    the database's `now()` rather than this process's clock, so it is the same
    instant the RLS policy evaluated against.
    """

    rows_deleted: int
    cutoff: datetime

    @property
    def is_empty(self) -> bool:
        return self.rows_deleted == 0


@dataclass(frozen=True)
class CheckpointRetentionRun:
    """What one checkpoint sweep did. Threads, not checkpoint rows.

    `purge.py::_discard_threads` explains why the number cannot be rows: the
    saver's `adelete_thread` returns nothing and this package may not count
    rows in a table AD-3's exception says only the saver reads. A thread is
    also the honest unit — the knob bounds a *conversation's* age.
    """

    threads_deleted: int
    cutoff: datetime

    @property
    def is_empty(self) -> bool:
        return self.threads_deleted == 0


async def purge_expired_audit_events(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    settings: Settings,
) -> AuditRetentionRun:
    """Delete every `audit_event` past the configured floor. Under the redactor role.

    AC 5's second half. The `DELETE` runs as `audit_redactor` because that is
    the only role in the cluster with `DELETE` on this table at all — the app
    role is INSERT-only by design (Story 1.2) — and the RLS policy attached to
    that grant is the safety net: a bug in the arithmetic below cannot delete a
    row younger than the floor, because the database will not let it.

    **The cutoff is computed by the database, once, and then bound as a
    parameter to both statements.** `now()` is transaction-start time in
    PostgreSQL, so the count, the delete and the policy's own `now() - interval
    '7 years'` all see the same instant — which is what makes the shortfall
    comparison below a statement about the *floor* rather than about the
    microseconds that passed between two queries.

    **`<`, never `<=`.** A row at exactly the floor survives, which is what a
    retention floor means: the obligation is to keep it *for* seven years, and
    the last instant of the seventh year is still inside it. The boundary is
    asserted directly in `tests/test_purge_cascade.py` rather than left to be
    inferred from this sentence.

    **Nothing is committed and no event is written when nothing was due.** A
    daily job that recorded "swept nothing" every day would put 365 rows a year
    into the very table it is sweeping, and would bury the runs that did
    something — `run_payment_batch` makes the identical argument about the
    identical hazard.
    """
    years = settings.audit_retention_years
    async with redactor_connection(settings) as redactor:
        cutoff = await redactor.scalar(
            sa.text("SELECT now() - make_interval(years => :years)"), {"years": years}
        )
        expired = await redactor.scalar(
            sa.select(sa.func.count()).select_from(_AUDIT).where(_AUDIT.c.at < cutoff)
        )
        pre_count = int(expired or 0)
        result = await redactor.execute(sa.delete(_AUDIT).where(_AUDIT.c.at < cutoff))
        deleted = int(result.rowcount)
        if deleted < pre_count:
            # Logged *and* raised: the log line is what an operator greps for
            # and the exception is what stops the job pretending it succeeded.
            # Both numbers, because the fix is to reconcile them.
            log.error(
                "retention.floor_mismatch",
                configured_years=years,
                rows_past_configured_floor=pre_count,
                rows_deleted=deleted,
            )
            await redactor.rollback()
            raise RetentionFloorMismatch(
                f"AUDIT_RETENTION_YEARS={years} selected {pre_count} row(s) but the "
                f"audit_redactor_delete row-level-security policy allowed only {deleted}. "
                "The policy's floor is baked in by migration 0003 and is changed by a "
                "migration, never by configuration; the two now disagree and this job "
                "would otherwise under-delete in silence."
            )
        await redactor.commit()

    run = AuditRetentionRun(rows_deleted=deleted, cutoff=_as_utc(cutoff))
    if run.is_empty:
        return run

    await audit.record(
        db,
        ctx,
        action=AUDIT_RETENTION_ACTION,
        entity=AUDIT_RETENTION_ENTITY,
        entity_id=run.cutoff.isoformat(),
        before=None,
        after=None,
    )
    await db.commit()
    log.info("retention.audit_swept", rows_deleted=run.rows_deleted, configured_years=years)
    return run


async def purge_expired_checkpoints(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    settings: Settings,
    delete_thread: ThreadDeleter,
) -> CheckpointRetentionRun:
    """Discard every conversation older than the checkpoint floor.

    AC 5's first half, and the knob it enforces has been sitting in `config.py`
    unread since Story 6.3 — that story created the data and stated how long it
    lives; this is the job that was named there as Epic 8's.

    **Keyed on `copilot_thread.created_at`, which bounds a conversation's age
    rather than its idleness.** The checkpoint tables carry no timestamp column
    this codebase may read (AD-3's exception, and `tests/test_layering.py`
    enforces it), so the only clock available is this project's own row — minted
    by the command that starts the conversation. A thread created ninety-one
    days ago is therefore swept even if it was used yesterday. That is a real
    limitation rather than an approximation of one, and it is recorded in
    `deferred-work.md` rather than worked around with a `last_used_at` column
    nobody writes.

    **Checkpoints first, rows second**, `purge.py::_purge_stores`' rule and its
    reason: the row is the only index of the string the saver files transcripts
    under, so deleting it first would leave PHI nothing in the system can name.

    **One failing thread must not stall the sweep for ever, which is what an
    unguarded loop over a daily job amounts to.** This runs on a clock and its
    input is "every thread past the cutoff", so a single thread the saver cannot
    discard — a checkpoint row corrupted by a half-written write, a thread id
    the vendored tables no longer recognise — is re-selected tomorrow, raises
    again, and blocks *every other* transcript behind it. Not one conversation
    would ever be deleted again, and the only symptom would be a daily
    exception nobody connects to a retention floor. So each thread is guarded,
    the sweep continues, and only the threads whose transcripts actually went
    have their `copilot_thread` rows deleted — a row kept beside a transcript
    that is still there is the honest state, and it is what makes the next run
    retry exactly the threads that failed.

    The failure line carries a **count and the exception type names** (AD-11),
    never `str(exc)`: a saver error message can quote a thread id, and a thread
    id is `<claim business id>:<user>:<seq>`.

    **`<`, never `<=`**, and no event or log line at all when nothing was due —
    both for `purge_expired_audit_events`' stated reasons.
    """
    cutoff = datetime.now(UTC) - timedelta(days=settings.copilot_checkpoint_retention_days)
    expired: Sequence[str] = (
        await db.scalars(
            sa.select(CopilotThread.thread_id).where(CopilotThread.created_at < cutoff)
        )
    ).all()
    if not expired:
        return CheckpointRetentionRun(threads_deleted=0, cutoff=cutoff)

    # The loop is spelled here rather than shared with `purge.py`'s: the two
    # differ in exactly the way that matters — an on-demand purge of one subject
    # *should* propagate and be re-run by its operator, and a daily sweep over
    # everybody's threads should not let one bad row take the other thousand
    # with it.
    discarded: list[str] = []
    failures: list[str] = []
    for thread_id in expired:
        try:
            await delete_thread(thread_id)
        # Deliberately broad: the deleter is an injected callable over a
        # vendored saver, so the exception surface is the vendor's and narrowing
        # it here would be this module claiming to know what a future
        # checkpointer raises.
        except Exception as exc:
            failures.append(type(exc).__name__)
            continue
        discarded.append(thread_id)
    if failures:
        log.warning(
            "retention.checkpoint_discard_failed",
            threads_failed=len(failures),
            threads_discarded=len(discarded),
            errors=sorted(set(failures)),
        )
    if not discarded:
        return CheckpointRetentionRun(threads_deleted=0, cutoff=cutoff)

    # Batched for `purge.py::in_batches`' reason: one bind parameter per thread
    # id, and a retention window that has been left unswept can hold more of
    # them than one statement may carry.
    deleted = 0
    for batch in in_batches(discarded):
        result = await db.execute(
            sa.delete(CopilotThread)
            .where(CopilotThread.thread_id.in_(batch))
            .execution_options(synchronize_session=False)
        )
        deleted += int(cast(CursorResult[Any], result).rowcount)

    await audit.record(
        db,
        ctx,
        action=CHECKPOINT_RETENTION_ACTION,
        entity=THREAD_ENTITY,
        entity_id=cutoff.isoformat(),
        before=None,
        after=None,
    )
    await db.commit()
    log.info(
        "retention.checkpoints_swept",
        store=CHECKPOINT_STORE,
        threads_deleted=deleted,
        retention_days=settings.copilot_checkpoint_retention_days,
    )
    return CheckpointRetentionRun(threads_deleted=deleted, cutoff=cutoff)


def _as_utc(value: Any) -> datetime:
    """The database's `timestamptz` as an aware UTC `datetime`.

    asyncpg returns an aware value already; the normalisation is here so that
    the summary object and the `entity_id` it renders carry one representation
    whatever the driver hands back, rather than an ISO string whose offset
    depends on the server's `TimeZone` setting.
    """
    stamp = cast(datetime, value)
    return stamp.astimezone(UTC)


__all__ = [
    "AUDIT_RETENTION_ACTION",
    "AUDIT_RETENTION_ENTITY",
    "CHECKPOINT_RETENTION_ACTION",
    "AuditRetentionRun",
    "CheckpointRetentionRun",
    "RetentionFloorMismatch",
    "purge_expired_audit_events",
    "purge_expired_checkpoints",
]
