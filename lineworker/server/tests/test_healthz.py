"""App factory builds; /healthz answers, with the DB check monkeypatched."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import api.app as app_module
from api import create_app
from api.app import check_db
from config import Settings
from logging_config import configure_logging


@asynccontextmanager
async def make_client(
    monkeypatch: pytest.MonkeyPatch, db_ok: bool
) -> AsyncIterator[httpx.AsyncClient]:
    async def fake_check(engine: AsyncEngine) -> bool:
        return db_ok

    monkeypatch.setattr(app_module, "check_db", fake_check)
    app = create_app(Settings(database_url="postgresql://x:x@db-not-real:5432/x"))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_healthz_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async with make_client(monkeypatch, db_ok=True) as client:
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "db": "ok"}


async def test_healthz_degraded_when_db_down(monkeypatch: pytest.MonkeyPatch) -> None:
    async with make_client(monkeypatch, db_ok=False) as client:
        resp = await client.get("/healthz")
        assert resp.status_code == 503
        assert resp.json()["db"] == "unreachable"


async def test_db_unreachable_is_logged_with_its_error_class(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Without the class name a bad password, a missing DB and a TLS failure
    all emit the same opaque line — the container just sits unhealthy."""
    configure_logging("INFO")
    engine = create_async_engine("postgresql+asyncpg://x:x@db-not-real.invalid:5432/x")
    try:
        assert await check_db(engine) is False
    finally:
        await engine.dispose()

    records: list[dict[str, Any]] = [
        json.loads(line)
        for line in capfd.readouterr().err.strip().splitlines()
        if line.startswith("{")
    ]
    warning = next(r for r in records if r["event"] == "healthz.db_unreachable")
    assert warning["error"]  # the exception class, e.g. OperationalError
    assert "x:x@" not in json.dumps(warning)  # no credentials/PHI in the log
