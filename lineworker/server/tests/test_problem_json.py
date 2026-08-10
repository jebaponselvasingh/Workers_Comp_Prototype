"""Story 1.3 AC 4: every request without a valid session gets 401 problem+json.

These tests need no database on purpose. A request with no cookie is
rejected before any SQL runs, and that is exactly the property worth
pinning: the unauthenticated path must not depend on the database being
reachable, or a DB outage turns 401s into 500s.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import APIRouter

import api.deps as deps
from api import create_app
from api.deps import PUBLIC_PATHS, enforce_authenticated
from api.errors import PROBLEM_CONTENT_TYPE
from config import Settings

UNREACHABLE_DB = "postgresql://x:x@db-not-real.invalid:5432/x"


@asynccontextmanager
async def make_client(
    app_extra: APIRouter | None = None, *, raise_app_exceptions: bool = True
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(database_url=UNREACHABLE_DB))
    if app_extra is not None:
        app.include_router(app_extra)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_protected_endpoint_without_cookie_is_401_problem_json() -> None:
    async with make_client() as client:
        resp = await client.get("/me")

    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    body = resp.json()
    assert body["status"] == 401
    assert body["title"] == "Unauthenticated"
    assert body["type"] == "/problems/unauthenticated"
    assert body["detail"]


async def test_unknown_cookie_is_401_with_a_different_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token that resolves to no session fails closed, and says so.

    The lookup is stubbed rather than run against a database: this asserts
    the dependency's branch, and the real lookup is exercised end to end in
    test_auth.py's logout-replay test.
    """

    async def no_session(db: object, token: str) -> None:
        return None

    monkeypatch.setattr(deps, "resolve_session", no_session)

    async with make_client() as client:
        client.cookies.set("lw_session", "not-a-real-session-token")
        resp = await client.get("/me")

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Your session has ended. Sign in again."


async def test_a_router_added_later_inherits_the_401_gate() -> None:
    """The regression this guards: a Story 6 router that forgets auth.

    `enforce_authenticated` lives on the app, so a brand-new router is
    protected the moment it is included — nobody has to remember.
    """
    router = APIRouter()

    @router.get("/some-future-endpoint")
    async def future() -> dict[str, str]:  # pragma: no cover - never reached
        return {"leaked": "data"}

    async with make_client(router) as client:
        resp = await client.get("/some-future-endpoint")

    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)


async def test_public_paths_answer_without_a_session() -> None:
    async with make_client() as client:
        resp = await client.get("/healthz")
    # healthz is public: it answers (degraded here — no database), not 401.
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"


async def test_public_path_set_is_the_documented_minimum() -> None:
    """Widening the unauthenticated surface should be a visible diff."""
    documented = {"/healthz", "/personas", "/auth/login", "/auth/logout"}
    assert set(PUBLIC_PATHS) == documented


async def test_public_paths_still_match_when_the_proxy_forwards_the_prefix() -> None:
    """The allowlist must key on the same path routing keys on.

    `request.url.path` keeps the mount prefix; routing strips it. Behind an
    ingress that forwards `/api` intact — an ALB or k8s Ingress with no
    rewrite, `uvicorn --root-path`, or the Vite dev proxy pointed at a
    host-run uvicorn — comparing the raw path meant `/personas` and
    `/auth/login` both 401'd and nobody could sign in at all.
    """
    # Passing the gate means reaching the (deliberately unreachable)
    # database, i.e. a 500 rather than a 401 — that difference is the whole
    # assertion.
    async with make_client(raise_app_exceptions=False) as client:
        resp = await client.post("/api/auth/login", json={"personaId": 1})
        assert resp.status_code == 500

    async with make_client(raise_app_exceptions=False) as client:
        resp = await client.get("/api/me")
        assert resp.status_code == 401  # still protected under the prefix


async def test_logout_never_requires_a_valid_session() -> None:
    """Logout is idempotent: an expired cookie still gets cleared."""
    async with make_client() as client:
        resp = await client.post("/auth/logout")

    assert resp.status_code == 204
    # The clearing Set-Cookie is the whole point — a 401 here would strand
    # a dead cookie in the browser until it aged out.
    assert "lw_session=" in resp.headers.get("set-cookie", "")


async def test_openapi_and_docs_are_not_governed_by_the_auth_dependency() -> None:
    """These are plain Starlette routes, so the dependency never sees them.

    Pinning the real behaviour: they answer without a session, and the only
    control over them is whether they are served at all. Listing them in
    PUBLIC_PATHS would imply a switch that does not exist.
    """
    async with make_client() as client:
        for path in ("/openapi.json", "/docs"):
            resp = await client.get(path)
            assert resp.status_code == 200, path
            assert path.lstrip("/").split("/")[0] not in {p.lstrip("/") for p in PUBLIC_PATHS}


async def test_prod_is_refused_while_persona_login_is_the_auth_mechanism() -> None:
    """The Deferred IdP decision is a boot blocker, not just an advisory."""
    with pytest.raises(RuntimeError, match="ENV=prod is refused"):
        create_app(Settings(database_url=UNREACHABLE_DB, env="prod"))  # type: ignore[arg-type]


async def test_unhandled_exception_becomes_problem_json_without_leaking_detail() -> None:
    """AD-11: the 500 body says nothing about what actually blew up.

    Needs raise_app_exceptions=False — Starlette re-raises after its error
    handler runs, so the default transport would surface the exception
    instead of the response the client would really receive.
    """
    router = APIRouter()

    @router.get("/boom")
    async def boom() -> None:
        raise ValueError("claimant SSN 123-45-6789")

    app = create_app(Settings(database_url=UNREACHABLE_DB))
    app.include_router(router)
    app.dependency_overrides[enforce_authenticated] = lambda: None

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/boom")

    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    body = resp.text
    assert "SSN" not in body and "123-45-6789" not in body and "ValueError" not in body


async def test_unknown_route_is_problem_json_too() -> None:
    """No route matched, so no dependency ran — the envelope still holds.

    Router-level 404s never reach `enforce_authenticated` (there is no
    route to attach it to), which is why the app-level exception handlers
    and not the auth dependency are what make problem+json universal.
    """
    async with make_client() as client:
        resp = await client.get("/no-such-thing")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    assert resp.json()["title"] == "Not Found"


@pytest.mark.parametrize("secure_env,expected", [("prod", True), ("dev", False), ("e2e", False)])
def test_cookie_secure_flag_follows_environment(secure_env: str, expected: bool) -> None:
    settings = Settings(database_url=UNREACHABLE_DB, env=secure_env)  # type: ignore[arg-type]
    assert settings.cookie_secure is expected


def test_cookie_secure_flag_is_overridable() -> None:
    settings = Settings(database_url=UNREACHABLE_DB, env="dev", session_cookie_secure=True)  # type: ignore[arg-type]
    assert settings.cookie_secure is True
