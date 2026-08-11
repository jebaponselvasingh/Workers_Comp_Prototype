"""FastAPI app factory. Routers stay thin (AD-1); auth is default-on.

`enforce_authenticated` is attached to the app, not to individual routers,
so every future router inherits both the 401 behaviour and the problem+json
envelope without opting in. See `api/deps.py` for why that direction
matters.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from api.deps import enforce_authenticated
from api.errors import register_error_handlers
from api.routers import auth_router, claims_router, glossary_router, stats_router
from config import Env, Settings, get_settings
from logging_config import configure_logging

log = structlog.get_logger()


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
        log.info("app.start", env=settings.env)
        yield
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

    @app.get("/healthz")
    async def healthz(response: Response) -> dict[str, str]:
        db_ok = await check_db(app.state.engine)
        if not db_ok:
            response.status_code = 503
        return {"status": "ok" if db_ok else "degraded", "db": "ok" if db_ok else "unreachable"}

    return app
