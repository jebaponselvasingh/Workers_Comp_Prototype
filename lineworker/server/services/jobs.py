"""In-process scheduled jobs — the registration point, kept deliberately small.

The spine's Structural Seed says "the payment batch and embedding refresh run
as scheduled jobs inside the `api` process (mechanism deferred)", and the
Deferred list keeps "APScheduler versus worker container" open. This module is
the smallest thing that satisfies the binding half without pre-empting the
open one: an asyncio task, a list of jobs, and a predicate per job.

**Generic on purpose, and this is the story's instruction rather than
speculation.** Story 3.4's Dev Notes ask for "a small generic scheduled-jobs
hook in the api process, not a payments-specific one-off", because Epic 6's
embedding refresh lands on it next. So nothing here mentions payments: a job is
a name, a predicate over the clock, and something to await.

## What a job is, and what the runner refuses to know

    ScheduledJob(name=…, due=lambda now, last_run: …, run=…)

The runner ticks on a fixed interval, asks each job whether it is due, and
awaits the ones that say yes. It does not know what any of them do, it does not
retry, and it does not care what they return. Two consequences worth stating:

- **`due` owns the cadence.** The tick interval is a polling rate, not a
  schedule — conflating the two is how a twice-weekly job ends up running every
  five minutes. `Settings.scheduler_tick_seconds` and
  `Settings.payment_batch_weekdays` are separate knobs for this reason.
- **A job that raises is logged and the loop survives.** A scheduler that dies
  on one job's exception takes every other job with it, silently, until
  somebody notices a thing did not happen. `last_run` is still advanced on
  failure, so a job that fails every tick logs once per due window rather than
  once per tick.

## Why `tick` is separate from `run_forever`

`tick(now)` is the whole of the behaviour and takes its clock as an argument,
so a test can drive a week through the scheduler in microseconds and assert
exactly which jobs fired on which day. `run_forever` is a `while True` with a
`sleep` in it and has nothing else in it to get wrong — which is the point:
everything worth testing is on the side that does not need a clock to pass.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

import structlog

log = structlog.get_logger()

#: `(now, last_run) -> should it run?`. `last_run` is `None` until the job has
#: fired once **in this process** — see `JobRunner.tick` on what that means
#: after a restart.
DuePredicate = Callable[[datetime, datetime | None], bool]


@dataclass(frozen=True)
class ScheduledJob:
    """One periodic job. Four fields, and none of them is a schedule string.

    `run` takes no arguments and returns whatever it likes: a job that needs a
    database session opens one itself (the api process's sessionmaker is
    captured in the closure at registration), because a runner that handed out
    sessions would own their lifetime and would be a transaction manager rather
    than a scheduler.

    **`background` says this job may not hold the tick.** The runner awaits its
    jobs in order, which is right for work measured in milliseconds and wrong
    for work measured in model completions: Story 6.2's insight refresh can
    spend `CHAT_REQUEST_TIMEOUT_SECONDS` per kind, four kinds per claim, five
    claims per run — so a single slow run delayed the embedding refresh and the
    payment batch behind it, silently, for minutes at a time (review of Story
    6.2, M9). A background job is started as its own task and the tick moves on.

    The flag is opt-in and stays that way. Serial is the safer default: it means
    two jobs never write the same rows at once, and it makes the tick's
    behaviour something a test can assert without a clock. A job that opts out
    of it is making a claim about itself — that it holds no lock the other jobs
    want and that a run overlapping the next tick is survivable — and
    `JobRunner.tick`'s in-flight guard is what makes the second half true.
    """

    name: str
    due: DuePredicate
    run: Callable[[], Awaitable[object]]
    background: bool = False


@dataclass
class JobRunner:
    """The registry and the loop. One instance per api process.

    Held on `app.state` rather than as a module global so a test can build one
    without touching the running app's, and so two apps in one interpreter
    (which is exactly what the test suite is) do not share a schedule.
    """

    tick_seconds: float
    jobs: list[ScheduledJob] = field(default_factory=list)
    last_run: dict[str, datetime] = field(default_factory=dict)
    #: The still-running task of each `background=True` job, so a second tick
    #: cannot start a second copy of one. Keyed by job name and never removed:
    #: a finished task is cheap to hold and `done()` is the whole of the check.
    in_flight: dict[str, asyncio.Task[object]] = field(default_factory=dict)

    def register(self, job: ScheduledJob) -> None:
        """Add a job. A duplicate name is an error, not a second entry.

        The registry's rule is the derivations registry's: two jobs under one
        name would be two schedules for one thing, and the second would look
        like a no-op in every log line that names it.
        """
        if any(existing.name == job.name for existing in self.jobs):
            raise ValueError(f"a job named {job.name!r} is already registered")
        self.jobs.append(job)

    async def tick(self, now: datetime) -> tuple[str, ...]:
        """Run every job that says it is due, and report which ran.

        **`last_run` is per process and starts empty**, so a job whose
        predicate reads "a batch day, and not yet today" fires once on the
        first tick after a restart on a batch day. That is deliberate for the
        payment batch: the job is idempotent by construction, so a restart at
        noon re-running Tuesday's batch pays only what has been approved since,
        and the alternative — persisting run history so a restart can decline —
        would be a state store to keep correct in order to skip work that costs
        nothing. A job for which re-running is *not* free must say so in its
        own `due`, and this sentence is where it should find out.

        Failures are contained per job, not per tick: the second job still runs
        when the first raises.

        **A `background=True` job is started rather than awaited**, and a due
        one that is still running from a previous tick is skipped without
        advancing `last_run` — so it is retried on the next tick rather than
        having its due window consumed by a run it never got. That distinction
        is the reason the skip is not simply "report it as run": a job whose
        interval is shorter than its runtime would otherwise fire once and then
        look like it had fired every time.

        The returned names are the jobs this tick *started*, which for a serial
        job is also the jobs it finished and for a background one is not.
        """
        ran: list[str] = []
        for job in self.jobs:
            if not job.due(now, self.last_run.get(job.name)):
                continue
            if job.background:
                running = self.in_flight.get(job.name)
                if running is not None and not running.done():
                    log.info("scheduler.job_still_running", job=job.name)
                    continue
                self.last_run[job.name] = now
                ran.append(job.name)
                self.in_flight[job.name] = asyncio.create_task(self._guarded(job))
                continue
            # Recorded *before* awaiting, so a job that raises does not retry
            # on the very next tick — see the module docstring.
            self.last_run[job.name] = now
            ran.append(job.name)
            await self._guarded(job)
        return tuple(ran)

    async def shutdown(self) -> None:
        """Cancel every in-flight background job and **wait for it to end**.

        The waiting is the whole of it (follow-up review of Story 6.2, B8).
        `run_forever` already asked its background tasks to stop when it was
        cancelled, but `Task.cancel()` only *schedules* the cancellation: the
        task does not see it until the loop next runs it, and the lifespan's
        very next statement is `engine.dispose()`. So a background insight run
        — the one job in this build that can be mid-completion for minutes —
        got as far as its session's `__aexit__` after the engine underneath it
        had been torn down, which surfaces as an asyncpg error at shutdown with
        no obvious cause. That is precisely the failure the lifespan's own
        `task.cancel(); await task` comment records for the scheduler task
        itself; the background tasks are a second copy of it, and were left out.

        `return_exceptions=True` because a cancelled task raises
        `CancelledError` and a failing one has already been logged by
        `_guarded` — neither is something a shutdown path should re-raise, and
        an exception here would skip the `engine.dispose()` it is protecting.

        Idempotent and safe on an empty registry, so the lifespan calls it
        unconditionally rather than mirroring the scheduler's own start
        condition.
        """
        pending = [task for task in self.in_flight.values() if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            log.info("scheduler.background_jobs_stopped", jobs=len(pending))

    async def _guarded(self, job: ScheduledJob) -> object:
        """Run one job, turning any exception into one operational log line.

        Shared by both paths so a background job's failure is reported exactly
        as a serial one's is — an unhandled exception inside a bare
        `create_task` is worse than useless, because asyncio surfaces it as a
        "Task exception was never retrieved" warning at garbage-collection time,
        detached from the job that caused it.

        The class name and the job name; never the exception's message, which
        for a database error can carry column values (AD-11). The traceback goes
        nowhere on purpose: this is an operational log line, and the failure is
        visible as a job that stopped having an effect.
        """
        try:
            return await job.run()
        except asyncio.CancelledError:
            # Shutdown, not failure. Re-raised so the task actually ends.
            raise
        except Exception as exc:
            log.error("scheduler.job_failed", job=job.name, error=type(exc).__name__)
            return None

    async def run_forever(self, clock: Callable[[], datetime]) -> None:
        """Tick until cancelled. The only part of this module with a `sleep`.

        Cancellation is re-raised rather than swallowed, so the lifespan's
        `task.cancel()` actually ends the task instead of looping through a
        `CancelledError` caught by a bare `except`.

        **Background jobs are cancelled with it** — but cancelled is not
        finished, and this method cannot wait for them: it is itself being
        cancelled, so an `await` here has no promise of returning. Asking them
        to stop is all that belongs on this path; `shutdown()` is the half that
        waits, and the lifespan calls it after this task has ended (follow-up
        review of Story 6.2, B8). Background jobs are the one thing in this
        module that outlives a tick, so they are also the one thing that could
        outlive the engine the lifespan is about to dispose — which surfaces as
        an asyncpg error at shutdown with no obvious cause, precisely the
        failure the lifespan's own `task.cancel(); await task` comment records
        one level down.
        """
        log.info("scheduler.started", jobs=[job.name for job in self.jobs])
        try:
            while True:
                await asyncio.sleep(self.tick_seconds)
                await self.tick(clock())
        except asyncio.CancelledError:
            for task in self.in_flight.values():
                task.cancel()
            log.info("scheduler.stopped")
            raise


def weekly_on(weekdays: frozenset[int]) -> DuePredicate:
    """Due once per calendar day, on the named weekdays (`date.weekday()`).

    The predicate the payment batch is registered with, written here rather
    than in `api/app.py` so that the "once per day" half is expressed once. The
    comparison is on `.date()` rather than on an elapsed interval: "Tuesdays
    and Fridays" is a statement about the calendar, and 24-hour arithmetic
    would drift across a daylight-saving boundary into running twice or not at
    all on the day it moved.
    """

    def due(now: datetime, last_run: datetime | None) -> bool:
        if now.weekday() not in weekdays:
            return False
        return last_run is None or last_run.date() < now.date()

    return due


def every_seconds(interval: float) -> DuePredicate:
    """Due once per `interval` seconds of elapsed wall clock (Story 6.1).

    The second predicate this module holds, and it answers a different kind of
    question from `weekly_on`. That one is a statement about the *calendar* —
    "money goes out on Tuesdays and Fridays" — and compares `.date()` precisely
    so a daylight-saving shift cannot make a batch run twice or not at all.
    This one is a statement about *elapsed time*: the embedding refresh has no
    opinion about which day it is, only about how long a stale row should be
    allowed to stay stale. Interval arithmetic is the right shape for that, and
    the daylight-saving hazard does not apply because nothing here reads a
    local date.

    Written here rather than as a lambda in `api/app.py` for `weekly_on`'s
    reason: the runner's cadences live in predicates, so that the tick stays a
    polling rate and a job's schedule stays one testable function.

    **Due immediately on the first tick after a restart** (`last_run is None`),
    which is the module docstring's stated default and is correct for this job
    for the same reason it is for the batch: `refresh_stale_embeddings` is
    idempotent by construction — its query is "what is pending", so a run with
    nothing pending embeds nothing and commits an empty transaction. A restart
    loop would cost one query per boot, not one duplicated write.

    The comparison is `>=` rather than `>`: the runner's own tick interval is
    the granularity, and with `interval` set to an exact multiple of it, a
    strict comparison would defer every run by one whole tick for ever.
    """

    def due(now: datetime, last_run: datetime | None) -> bool:
        if last_run is None:
            return True
        elapsed = (now - last_run).total_seconds()
        # A *negative* elapsed time means the wall clock stepped backwards
        # between two ticks — an NTP correction, most plainly — leaving
        # `last_run` in the future. Treated as due rather than waited out: the
        # alternative is a refresh that stops running until the clock catches
        # up, which for an hour's correction is an hour of edits that stay
        # stale for no reason anybody could find in a log. Re-running early
        # costs one query, because the job is idempotent by construction.
        return elapsed >= interval or elapsed < 0

    return due


__all__ = ["DuePredicate", "JobRunner", "ScheduledJob", "every_seconds", "weekly_on"]
