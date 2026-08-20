"""FastAPI app factory. Routers stay thin (AD-1); auth is default-on.

`enforce_authenticated` is attached to the app, not to individual routers,
so every future router inherits both the 401 behaviour and the problem+json
envelope without opting in. See `api/deps.py` for why that direction
matters.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import Depends, FastAPI, Response
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agents import InsightGenerationDeps, chat_client, refresh_pending_insights
from agents.chat_model import copilot_chat_model
from agents.graph import build_graph
from api.deps import enforce_authenticated
from api.errors import register_error_handlers
from api.routers import (
    admin_router,
    auth_router,
    claims_router,
    copilot_router,
    dashboard_router,
    diary_router,
    glossary_router,
    stats_router,
)
from config import Env, Settings, get_settings
from logging_config import configure_logging
from services.financials.batch import run_payment_batch, system_context
from services.jobs import JobRunner, ScheduledJob, every_seconds, weekly_on
from services.rag import embedding_client, refresh_stale_embeddings

log = structlog.get_logger()

#: How many psycopg connections the checkpoint saver's own pool may open.
#:
#: A module constant rather than a sixth `Settings` field, deliberately. The
#: five copilot knobs in `config.py` are all things an operator has a reason to
#: change — a retention period, two bounds on a model, a keepalive cadence. This
#: is a consequence of a design decision (one pool for the saver, separate from
#: SQLAlchemy's) rather than a decision of its own, and a knob nobody should
#: turn is a knob somebody eventually turns.
#:
#: Four, because a run holds one connection for the checkpoint writes of one
#: turn and a single-site deployment does not have four handlers streaming at
#: once against a model server that answers one request at a time.
COPILOT_POOL_MAX_SIZE = 4

PAYMENT_BATCH_JOB = "payment_batch"
EMBEDDING_REFRESH_JOB = "embedding_refresh"
INSIGHT_REFRESH_JOB = "insight_refresh"


def build_job_runner(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> JobRunner:
    """The api process's scheduled jobs (spine: Structural Seed).

    Three jobs: Story 3.4's payment batch, Story 6.1's embedding refresh and
    Story 6.2's insight refresh. The second is why the runner was written
    generic in the first place — the comment that stood here reserved the slot,
    and filling it required no change to `services/jobs.py` beyond a second
    due-predicate beside `weekly_on`. The third cost nothing at all: same
    predicate, same session-per-run shape, same system actor, which is the
    outcome that made "a small generic hook, not a payments-specific one-off"
    worth insisting on.

    **The two cadences are different kinds of thing**, and the predicates say
    so. `weekly_on` is a claim about the calendar (a bank's cut-off days);
    `every_seconds` is a claim about elapsed time (how long a stale embedding
    may stay stale). Neither is expressed as a cron string, so neither can be
    misread as the other.

    **The job opens and closes its own session.** A long-lived session held
    across ticks would hold a pooled connection for the process's lifetime and
    would carry a transaction snapshot between runs; the batch is a command
    like any other and gets a session with the same lifetime as its work.

    **The system actor is resolved per run**, inside the job, not captured
    here — AD-7's rule that a caller context is a reference re-resolved rather
    than a materialized scope, and here it also means a boot that precedes
    migration 0029 fails at the first tick with a sentence rather than at
    import time with the whole process.
    """
    runner = JobRunner(tick_seconds=settings.scheduler_tick_seconds)

    async def payment_batch() -> None:
        async with sessionmaker() as session:
            await run_payment_batch(session, await system_context(session))

    runner.register(
        ScheduledJob(
            name=PAYMENT_BATCH_JOB,
            due=weekly_on(settings.payment_batch_weekday_numbers),
            run=payment_batch,
        )
    )

    async def embedding_refresh() -> None:
        # Same shape as the batch above, deliberately: its own session, the
        # system actor resolved per run. The actor is the one migration 0029
        # seeded for the payment batch rather than a second machine identity —
        # `data/models/enums.py::UserRole` says in as many words that Epic 6's
        # refresh "inherits this one rather than minting a second convention",
        # and its `scope_all` makes the AD-7 predicate a tautology because the
        # refresh's scope genuinely is the whole portfolio.
        #
        # The client is built per run for the same reason the session is: it
        # holds a URL and a model name read off `Settings`, and constructing it
        # here rather than in the closure means an operator's restart after
        # changing `EMBEDDING_MODEL` is the whole of the deployment procedure.
        async with sessionmaker() as session:
            await refresh_stale_embeddings(
                session,
                await system_context(session),
                client=embedding_client(settings),
                limit=settings.embedding_refresh_batch_size,
            )

    runner.register(
        ScheduledJob(
            name=EMBEDDING_REFRESH_JOB,
            due=every_seconds(settings.embedding_refresh_interval_seconds),
            run=embedding_refresh,
        )
    )

    async def insight_refresh() -> None:
        # The embedding refresh's shape one story later, and deliberately not a
        # variation on it: its own session, the system actor resolved per run,
        # the clients built per run so an operator's restart after changing
        # `CHAT_MODEL` is the whole of the deployment procedure.
        #
        # **Two clients, because generation speaks to two endpoints.** The chat
        # client narrates; the embedding client is what the similar-case gather
        # needs to turn the query claim into a vector. Both are built here at
        # the composition root and handed down, which is the arrangement that
        # keeps `services/rag` free of chat code (AD-5) — see
        # `services/rag/insights.py` on the injected generator.
        #
        # The bound is a bound on *claims*, not rows: each claim costs up to
        # four completions against a model server that answers one request at a
        # time, which is why `INSIGHT_REFRESH_BATCH_SIZE` is much smaller than
        # the embedding batch. Whatever is left is still pending on the next
        # tick.
        async with sessionmaker() as session:
            await refresh_pending_insights(
                session,
                await system_context(session),
                limit=settings.insight_refresh_batch_size,
                deps=InsightGenerationDeps(
                    chat=chat_client(settings),
                    embed=embedding_client(settings),
                    staleness_days=settings.insight_staleness_disclosure_days,
                ),
            )

    runner.register(
        ScheduledJob(
            name=INSIGHT_REFRESH_JOB,
            due=every_seconds(settings.insight_refresh_interval_seconds),
            run=insight_refresh,
            # **The one background job**, and the only one that has any business
            # being one. `tick` awaits its jobs in order, so before this flag a
            # multi-minute insight run sat in front of the embedding refresh and
            # the payment batch and delayed both — a job that spends up to
            # `CHAT_REQUEST_TIMEOUT_SECONDS` per kind, four kinds per claim,
            # holding a scheduler whose other two jobs are measured in
            # milliseconds (review of Story 6.2, M9).
            #
            # Safe to run alongside them because of what it touches: it writes
            # `ai_insight` and nothing else, through the one command that owns
            # that table (AD-12), and the payment batch and embedding refresh
            # write neither. `JobRunner.tick`'s in-flight guard is what stops a
            # second copy of *this* job starting on the next tick.
            background=True,
        )
    )
    return runner


@dataclass(frozen=True)
class CopilotRuntime:
    """Everything a copilot run needs, assembled once at startup (AD-6).

    Stored on `app.state.copilot` and read by `api/routers/copilot.py`. One
    object rather than five attributes on `app.state`, because they are
    constructed together, torn down together, and a route that had to reach for
    four of them separately would be four chances to read one that had not been
    built.

    **The graph is compiled once**, which is AD-6 stated operationally: a graph
    rebuilt per request would re-bind its tools and re-read its prompt file on
    every message, for a structure that cannot vary between runs.

    **The chat model is built once too**, and `lifespan` is the one place in the
    process where that is safe. `agents/client.py` builds a client per refresh
    run and argues that a long-lived one on a module global would outlive the
    event loop it was created on; `lifespan` has a live loop for the process's
    whole life, which is exactly the shape that inverts the argument. See
    `agents/chat_model.py` on the pooled-client deferral this discharges, and on
    the half of it that stays deferred.

    `sessionmaker` is here rather than reached through `app.state` inside a
    tool, so that `agents/` never imports `api/` — the dependency direction the
    whole server is arranged around.

    The three bounds are values rather than a `Settings` reference for the same
    reason `CallerContext` is a frozen dataclass: a run must not be able to
    widen its own limits, and a route reading `Settings` at stream time would be
    a second configuration surface at the one moment nobody is watching.
    """

    graph: CompiledStateGraph[Any, Any, Any, Any]
    #: The saver the graph is compiled with, published beside it.
    #:
    #: Nothing in this story reaches for it — the history route asks the *graph*,
    #: which asks its checkpointer, because AD-3's exception is about who reads
    #: the tables and the answer must stay "the saver". It is here because
    #: `agents/threads.discard_thread` takes a checkpointer and Epic 8's PHI
    #: purge is the caller that will need one, and because a graph test that
    #: wants to swap the model has to rebuild against *this* saver rather than
    #: construct a second one over the same tables.
    checkpointer: BaseCheckpointSaver[Any]
    sessionmaker: async_sessionmaker[AsyncSession]
    max_tool_calls: int
    run_timeout_seconds: float
    sse_keepalive_seconds: float


async def build_copilot(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[CopilotRuntime, AsyncConnectionPool[Any]]:
    """Pool → saver → chat model → compiled graph. The copilot's composition root.

    Returns the pool beside the runtime because `lifespan` has to close it, and
    a runtime that owned its own teardown would be a second shutdown path
    running in an order nobody chose.

    **A psycopg pool of its own, not SQLAlchemy's engine.** `AsyncPostgresSaver`
    is written against psycopg's async connection API — it uses server-side
    binary parameters and its own pipeline mode — so it cannot be handed an
    asyncpg connection from `app.state.engine`. Two pools against one database
    is the honest cost of AD-3's exception: the saver owns its tables and it
    reaches them its own way.

    `row_factory=dict_row` is required by the saver rather than chosen here; it
    reads its own rows by column name.

    **`saver.setup()` is never called** — not here, not anywhere. Migration 0043
    vendored its DDL, the application role has no schema rights, and a `setup()`
    on every boot would be a second owner of the same tables. This is the code
    that would have called it, and the absence is the point.

    `min_size=1`: the copilot is one panel, not a hot path, and a pool that
    opened four connections at boot in a deployment where nobody uses the
    copilot would be four idle backends for nothing.
    """
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=1,
        max_size=COPILOT_POOL_MAX_SIZE,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    # **Opened without waiting for a connection**, and that is not a
    # micro-optimisation. `wait=True` makes the pool's first connection a
    # precondition of `lifespan`, which makes the *whole api* refuse to start
    # when the copilot's pool cannot reach the database — including `/healthz`,
    # whose entire job is to answer while the database is down. It also broke
    # every test that builds an app against an unreachable database to assert a
    # 401 or a degraded health check, which is the same failure wearing a
    # different hat.
    #
    # AD-14's posture says it plainly: claim operations never depend on the
    # agent runtime. A copilot that cannot reach its checkpoint store is a
    # copilot that answers `error` on a run (Story 6.6 owns how that reads); it
    # is not a console that will not boot.
    await pool.open()
    saver = AsyncPostgresSaver(pool)
    graph = build_graph(
        model=copilot_chat_model(settings),
        checkpointer=saver,
        max_tool_calls=settings.copilot_max_tool_calls_per_run,
    )
    runtime = CopilotRuntime(
        graph=graph,
        checkpointer=saver,
        sessionmaker=sessionmaker,
        max_tool_calls=settings.copilot_max_tool_calls_per_run,
        run_timeout_seconds=settings.copilot_run_timeout_seconds,
        sse_keepalive_seconds=settings.copilot_sse_keepalive_seconds,
    )
    return runtime, pool


async def check_db(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        # The class name distinguishes auth failure from a missing database
        # from a TLS error, and carries no claim data (AD-11 unaffected).
        log.warning("healthz.db_unreachable", error=type(exc).__name__)
        return False
    return True


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    is_prod = settings.env is Env.prod
    if is_prod:
        # Story 1.3 ships persona selection *as* authentication: anyone who
        # can reach the API can POST a persona id and receive a full
        # session. That is the sanctioned interim while the IdP choice is
        # Deferred — but "do not deploy this to prod" belongs in the code,
        # not only in a readiness advisory. Refusing to boot makes the
        # Deferred decision a blocker rather than a note somebody misses.
        raise RuntimeError(
            "ENV=prod is refused while persona login is the authentication "
            "mechanism: POST /auth/login mints a session from an unauthenticated "
            "persona id. Resolve the Deferred IdP decision and replace "
            "_resolve_user in api/deps.py before enabling prod."
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # pool_pre_ping: connections killed out-of-band (e.g. the e2e reset
        # terminating backends after a schema drop) are detected and
        # replaced transparently instead of failing the next request.
        engine = create_async_engine(settings.async_database_url, pool_pre_ping=True)
        app.state.engine = engine
        # expire_on_commit=False: a committed ORM object stays readable
        # while the response is being serialized.
        app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

        # The scheduled-jobs hook (spine: "the payment batch and embedding
        # refresh run as scheduled jobs inside the api process"). Built
        # unconditionally so `/healthz` and a test can inspect the registry,
        # started only where a wall clock is wanted — under `e2e` it is off, so
        # a suite's figures cannot move because a Tuesday arrived mid-run.
        # The copilot spine (Story 6.3): its own psycopg pool, the
        # `AsyncPostgresSaver` over it, the streaming chat model and the one
        # compiled graph. Built here rather than lazily on first use because
        # `lifespan` is the only place in the process with a live event loop for
        # the process's whole life — `agents/client.py` records the failure mode
        # a chat client acquires when it outlives one — and because a graph
        # compiled on the first request would put a cold start in front of a
        # handler's first message.
        copilot, copilot_pool = await build_copilot(settings, app.state.sessionmaker)
        app.state.copilot = copilot

        runner = build_job_runner(settings, app.state.sessionmaker)
        app.state.jobs = runner
        task: asyncio.Task[None] | None = None
        if settings.scheduler_runs:
            task = asyncio.create_task(runner.run_forever(lambda: datetime.now(UTC)))

        log.info("app.start", env=settings.env, scheduler=settings.scheduler_runs)
        yield

        if task is not None:
            # Cancel and *await* it. Dropping the reference would leave the
            # task running against an engine this line is about to dispose,
            # which surfaces as an asyncpg error at shutdown with no obvious
            # cause. `run_forever` re-raises `CancelledError`, so this returns
            # as soon as the current sleep is interrupted.
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        # …and the same again for the jobs that outlive a tick. `run_forever`
        # cancels them, but cancelling only schedules the interrupt — without
        # waiting, a background insight run's session cleanup lands *after* the
        # dispose below (follow-up review of Story 6.2, B8). Unconditional,
        # because `tick` can be driven without the loop.
        await runner.shutdown()

        # **The copilot pool closes before the engine disposes**, in the order
        # the job task is drained above and for the same reason: a pool closed
        # after its database handles were torn down surfaces as a psycopg error
        # at shutdown with no obvious cause. The two pools are independent, but
        # the ordering rule is one rule and it is easier to keep than to
        # remember which of two it applies to.
        await copilot_pool.close()

        await engine.dispose()
        log.info("app.stop")

    # `is_prod` is unreachable-true while the tripwire above stands; the
    # branches below are kept so that lifting it (once a real IdP lands)
    # restores the intended prod posture rather than silently publishing the
    # schema and the docs UIs.
    app = FastAPI(
        title="LINEWORKER API",
        root_path="/api",
        lifespan=lifespan,
        # These are plain Starlette routes, so `enforce_authenticated` never
        # runs for them — not being served is the only way to close them.
        # The generated TS client reads scripts/dump_openapi.py, not a
        # running server, so nothing depends on the schema being published.
        openapi_url=None if is_prod else "/openapi.json",
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        dependencies=[Depends(enforce_authenticated)],
    )
    app.state.settings = settings
    register_error_handlers(app)
    app.include_router(auth_router)
    app.include_router(stats_router)
    app.include_router(glossary_router)
    app.include_router(claims_router)
    app.include_router(diary_router)
    app.include_router(copilot_router)
    app.include_router(dashboard_router)
    if settings.env is Env.e2e:
        # AD-15's deterministic batch trigger, and it is *not served* anywhere
        # else — a 404 from the router rather than a 403 from a guard. See
        # `api/routers/admin.py` on why the difference matters.
        app.include_router(admin_router)

    @app.get("/healthz")
    async def healthz(response: Response) -> dict[str, str]:
        db_ok = await check_db(app.state.engine)
        if not db_ok:
            response.status_code = 503
        return {"status": "ok" if db_ok else "degraded", "db": "ok" if db_ok else "unreachable"}

    return app
