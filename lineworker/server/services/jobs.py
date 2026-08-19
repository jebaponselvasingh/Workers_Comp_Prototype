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
    """One periodic job. Three fields, and none of them is a schedule string.

    `run` takes no arguments and returns whatever it likes: a job that needs a
    database session opens one itself (the api process's sessionmaker is
    captured in the closure at registration), because a runner that handed out
    sessions would own their lifetime and would be a transaction manager rather
    than a scheduler.
    """

    name: str
    due: DuePredicate
    run: Callable[[], Awaitable[object]]


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
        """
        ran: list[str] = []
        for job in self.jobs:
            if not job.due(now, self.last_run.get(job.name)):
                continue
            # Recorded *before* awaiting, so a job that raises does not retry
            # on the very next tick — see the module docstring.
            self.last_run[job.name] = now
            ran.append(job.name)
            try:
                await job.run()
            except Exception as exc:
                # The class name and the job name; never the exception's
                # message, which for a database error can carry column values
                # (AD-11). The traceback goes nowhere on purpose: this is an
                # operational log line, and the failure is visible as a job
                # that stopped having an effect.
                log.error("scheduler.job_failed", job=job.name, error=type(exc).__name__)
        return tuple(ran)

    async def run_forever(self, clock: Callable[[], datetime]) -> None:
        """Tick until cancelled. The only part of this module with a `sleep`.

        Cancellation is re-raised rather than swallowed, so the lifespan's
        `task.cancel()` actually ends the task instead of looping through a
        `CancelledError` caught by a bare `except`.
        """
        log.info("scheduler.started", jobs=[job.name for job in self.jobs])
        try:
            while True:
                await asyncio.sleep(self.tick_seconds)
                await self.tick(clock())
        except asyncio.CancelledError:
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
