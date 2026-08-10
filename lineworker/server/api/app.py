"""FastAPI app factory. Routers stay thin (AD-1); auth deps arrive in Story 1.3."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from config import Settings, get_settings
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

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # pool_pre_ping: connections killed out-of-band (e.g. the e2e reset
        # terminating backends after a schema drop) are detected and
        # replaced transparently instead of failing the next request.
        app.state.engine = create_async_engine(settings.async_database_url, pool_pre_ping=True)
        log.info("app.start", env=settings.env)
        yield
        await app.state.engine.dispose()
        log.info("app.stop")

    app = FastAPI(title="LINEWORKER API", root_path="/api", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz(response: Response) -> dict[str, str]:
        db_ok = await check_db(app.state.engine)
        if not db_ok:
            response.status_code = 503
        return {"status": "ok" if db_ok else "degraded", "db": "ok" if db_ok else "unreachable"}

    return app
