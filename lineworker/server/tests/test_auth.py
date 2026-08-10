"""Story 1.3 AC 2-4 against a real migrated + seeded database.

Covered here: the session lifecycle (login -> me -> logout -> replay 401),
the AD-7 promise that a session references a user and never a scope, the
context builder's single `scope_all` -> ALL resolution, and the persona
endpoint's grouping of the Story 1.2 seed.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi import APIRouter

from api import create_app
from api.deps import CallerContextDep
from api.errors import PROBLEM_CONTENT_TYPE
from config import Settings
from data.context import AllEmployers
from data.models import Session
from data.repositories.identity import hash_token
from tests.conftest import APP_PASSWORD, requires_db

pytestmark = requires_db

# A route that echoes what the AD-7 context builder resolved. Nothing in
# the product exposes the caller context, but the story's whole point is
# that it is built correctly, so the test needs a window onto it.
probe = APIRouter()


@probe.get("/_test/context")
async def read_context(ctx: CallerContextDep) -> dict[str, Any]:
    scope = ctx.employer_ids
    return {
        "userId": ctx.user_id,
        "role": ctx.role.value,
        "scopesAll": ctx.scopes_all_employers,
        "employerIds": None if isinstance(scope, AllEmployers) else sorted(scope),
    }


@asynccontextmanager
async def make_client(db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(database_url=db_url, env="e2e"))  # type: ignore[arg-type]
    app.include_router(probe)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def persona_named(client: httpx.AsyncClient, name: str, role: str) -> dict[str, Any]:
    resp = await client.get("/personas")
    assert resp.status_code == 200
    items: list[dict[str, Any]] = resp.json()["items"]
    match = [p for p in items if p["name"] == name and p["role"] == role]
    assert len(match) == 1, f"expected exactly one {name}/{role} persona, got {len(match)}"
    return match[0]


def sync_engine(db_url: str) -> sa.Engine:
    return sa.create_engine(db_url.replace("postgresql://", "postgresql+psycopg://", 1))


@pytest.fixture(autouse=True)
def empty_session_table(seeded_db_url: str) -> None:
    """Start every test with no sessions.

    The seeded database is module-scoped (migrating per test would dominate
    the runtime), so without this the session-count assertions below would
    really be asserting on test execution order.
    """
    engine = sync_engine(seeded_db_url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM session"))
    finally:
        engine.dispose()


# --- personas ----------------------------------------------------------


async def test_personas_groups_the_seed_by_role(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.get("/personas")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 10
    assert body["nextCursor"] is None  # camelCase boundary is live

    by_role: dict[str, list[str]] = {}
    for persona in body["items"]:
        by_role.setdefault(persona["role"], []).append(persona["name"])
    assert len(by_role["supervisor"]) == 3
    assert len(by_role["analyst"]) == 1
    assert len(by_role["handler"]) == 6
    # David Bline is seeded twice (supervisor + analyst) per Story 1.2's
    # documented ruling, so he appears under both roles exactly as the
    # prototype's dropdowns do.
    assert "David Bline" in by_role["supervisor"]
    assert by_role["analyst"] == ["David Bline"]


async def test_personas_labels_are_derived_from_real_scope(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        park = await persona_named(client, "Jennifer Park", "supervisor")
        bline = await persona_named(client, "David Bline", "supervisor")

    assert park["label"] == "Jennifer Park — WC Supervisor (3M/GM/Toyota)"
    assert bline["label"] == "David Bline — WC Supervisor (Full portfolio)"


async def test_personas_endpoint_exposes_no_scope_data(seeded_db_url: str) -> None:
    """Pre-auth by necessity — so it must leak nothing machine-usable."""
    async with make_client(seeded_db_url) as client:
        resp = await client.get("/personas")

    for persona in resp.json()["items"]:
        assert set(persona) == {"id", "name", "role", "label"}


async def test_personas_is_reachable_without_a_session(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        assert (await client.get("/personas")).status_code == 200


# --- session lifecycle -------------------------------------------------


async def test_login_mints_a_session_and_me_returns_the_persona(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        persona = await persona_named(client, "Kaya Johnson", "handler")
        login = await client.post("/auth/login", json={"personaId": persona["id"]})

        assert login.status_code == 200
        assert login.json() == {
            "id": persona["id"],
            "name": "Kaya Johnson",
            "role": "handler",
            "initials": "KJ",
        }
        set_cookie = login.headers["set-cookie"]
        assert "HttpOnly" in set_cookie
        assert "SameSite=lax" in set_cookie.replace("samesite", "SameSite")

        me = await client.get("/me")
        assert me.status_code == 200
        # Role comes from app_user, not from anything the client sent.
        assert me.json()["role"] == "handler"


async def test_session_cookie_is_opaque_and_stored_hashed(seeded_db_url: str) -> None:
    """A database dump must not be a pile of usable cookies."""
    async with make_client(seeded_db_url) as client:
        persona = await persona_named(client, "David Bline", "supervisor")
        await client.post("/auth/login", json={"personaId": persona["id"]})
        token = client.cookies["lw_session"]

    engine = sync_engine(seeded_db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(sa.text("SELECT token_hash FROM session")).scalars().all()
    finally:
        engine.dispose()

    assert len(rows) == 1
    assert rows[0] != token
    assert rows[0] == hash_token(token)


async def test_session_row_carries_no_scope(seeded_db_url: str) -> None:
    """AC 2 / AD-7: the session references a user and nothing else.

    Asserted against the table definition rather than one row, so a later
    story that "helpfully" caches employer ids on the session trips this.
    """
    assert set(Session.__table__.columns.keys()) == {
        "id",
        "token_hash",
        "user_id",
        "created_at",
        "expires_at",
    }

    async with make_client(seeded_db_url) as client:
        persona = await persona_named(client, "Jennifer Park", "supervisor")
        await client.post("/auth/login", json={"personaId": persona["id"]})

    engine = sync_engine(seeded_db_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(sa.text("SELECT * FROM session")).mappings().one()
    finally:
        engine.dispose()

    serialized = repr(dict(row))
    for employer_id in range(1, 11):
        assert f"employer_id={employer_id}" not in serialized
    assert "employer" not in serialized


async def test_logout_ends_the_session_server_side(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        persona = await persona_named(client, "Marcus Chen", "handler")
        await client.post("/auth/login", json={"personaId": persona["id"]})
        stolen_token = client.cookies["lw_session"]

        logout = await client.post("/auth/logout")
        assert logout.status_code == 204

        after = await client.get("/me")
        assert after.status_code == 401
        assert after.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)

        # The cookie is not merely cleared in the browser — replaying the
        # exact token a thief could have copied is dead too.
        client.cookies.set("lw_session", stolen_token)
        replay = await client.get("/me")
        assert replay.status_code == 401

    engine = sync_engine(seeded_db_url)
    try:
        with engine.connect() as conn:
            remaining = conn.execute(sa.text("SELECT count(*) FROM session")).scalar_one()
    finally:
        engine.dispose()
    assert remaining == 0


async def test_the_whole_flow_works_as_the_runtime_app_role(seeded_db_url: str) -> None:
    """The other tests connect as the owner; production does not.

    Story 1.2 stripped the app role down to a CRUD-on-domain-tables grant
    surface, so a new table needs its grants or every login 500s on the
    real stack while every owner-connected test stays green.
    """
    # render_as_string(hide_password=False): str(URL) masks the password as
    # "***", which would reach the driver verbatim and fail to authenticate.
    app_url = (
        sa.make_url(seeded_db_url)
        .set(username="lineworker_app", password=APP_PASSWORD)
        .render_as_string(hide_password=False)
    )
    async with make_client(app_url) as client:
        persona = await persona_named(client, "Dante Reyes", "handler")
        assert (
            await client.post("/auth/login", json={"personaId": persona["id"]})
        ).status_code == 200
        assert (await client.get("/me")).status_code == 200
        assert (await client.post("/auth/logout")).status_code == 204
        assert (await client.get("/me")).status_code == 401


async def test_login_with_an_unknown_persona_is_401(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.post("/auth/login", json={"personaId": 999_999})

    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    assert resp.json()["title"] == "Unauthenticated"


async def test_login_rejects_a_malformed_body_as_problem_json(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.post("/auth/login", json={"persona": "David Bline"})

    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    assert resp.json()["type"] == "/problems/validation-error"


# --- AD-7 context builder ----------------------------------------------


@pytest.mark.parametrize(
    "name,role,expect_all",
    [
        ("David Bline", "supervisor", True),
        ("David Bline", "analyst", True),
        ("Jennifer Park", "supervisor", False),
        ("Sarah Williams", "handler", False),
    ],
)
async def test_context_builder_resolves_scope_from_app_user(
    seeded_db_url: str, name: str, role: str, expect_all: bool
) -> None:
    async with make_client(seeded_db_url) as client:
        persona = await persona_named(client, name, role)
        await client.post("/auth/login", json={"personaId": persona["id"]})
        ctx = (await client.get("/_test/context")).json()

    assert ctx["userId"] == persona["id"]
    assert ctx["role"] == role
    assert ctx["scopesAll"] is expect_all
    if expect_all:
        assert ctx["employerIds"] is None
    else:
        assert ctx["employerIds"]


async def test_scoped_context_matches_the_assignment_table(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        persona = await persona_named(client, "Jennifer Park", "supervisor")
        await client.post("/auth/login", json={"personaId": persona["id"]})
        ctx = (await client.get("/_test/context")).json()

    engine = sync_engine(seeded_db_url)
    try:
        with engine.connect() as conn:
            expected = sorted(
                conn.execute(
                    sa.text("SELECT employer_id FROM user_employer_assignment WHERE user_id = :u"),
                    {"u": persona["id"]},
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()

    assert ctx["employerIds"] == expected
    assert len(expected) == 3  # Toyota, GM, 3M


async def test_context_is_re_resolved_per_request_not_cached_in_the_session(
    seeded_db_url: str,
) -> None:
    """AD-7's "re-resolve every request" is only real if a scope change
    lands without a new login.

    The added assignment row is removed again: `seeded_db_url` is
    module-scoped and the autouse fixture only truncates `session`, so
    leaving it behind would make every later assertion about this persona's
    book depend on test declaration order.
    """
    engine = sync_engine(seeded_db_url)
    added_employer_id: int | None = None
    try:
        async with make_client(seeded_db_url) as client:
            persona = await persona_named(client, "Sarah Williams", "handler")
            await client.post("/auth/login", json={"personaId": persona["id"]})
            before = (await client.get("/_test/context")).json()["employerIds"]

            with engine.begin() as conn:
                added_employer_id = conn.execute(
                    sa.text(
                        "INSERT INTO user_employer_assignment (user_id, employer_id) "
                        "SELECT :u, id FROM employer WHERE id NOT IN "
                        "(SELECT employer_id FROM user_employer_assignment WHERE user_id = :u) "
                        "ORDER BY id LIMIT 1 RETURNING employer_id"
                    ),
                    {"u": persona["id"]},
                ).scalar_one()
            after = (await client.get("/_test/context")).json()["employerIds"]

        assert len(after) == len(before) + 1
        assert added_employer_id in after
    finally:
        if added_employer_id is not None:
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "DELETE FROM user_employer_assignment "
                        "WHERE user_id = :u AND employer_id = :e"
                    ),
                    {"u": persona["id"], "e": added_employer_id},
                )
        engine.dispose()


async def test_logout_clears_the_cookie_even_when_the_session_is_already_dead(
    seeded_db_url: str,
) -> None:
    """Logout must not 401 the one caller who most needs the cookie gone."""
    async with make_client(seeded_db_url) as client:
        client.cookies.set("lw_session", "a-token-that-was-revoked-long-ago")
        resp = await client.post("/auth/logout")

    assert resp.status_code == 204
    assert "lw_session=" in resp.headers.get("set-cookie", "")


async def test_an_out_of_range_persona_id_is_rejected_not_a_500(seeded_db_url: str) -> None:
    """int4 overflow used to reach the driver and answer 500, which both
    crashed and told the caller their id was out of range rather than
    unknown."""
    async with make_client(seeded_db_url) as client:
        resp = await client.post("/auth/login", json={"personaId": 2_147_483_648})

    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)


async def test_login_clears_the_callers_expired_sessions(seeded_db_url: str) -> None:
    """Expired rows are reaped where a caller owns the commit, not in the
    read path of the auth dependency."""
    engine = sync_engine(seeded_db_url)
    async with make_client(seeded_db_url) as client:
        persona = await persona_named(client, "Dante Reyes", "handler")

        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO session (token_hash, user_id, created_at, expires_at) "
                    "VALUES ('stale-hash', :u, now() - interval '2 days', "
                    "now() - interval '1 day')"
                ),
                {"u": persona["id"]},
            )
            assert conn.execute(sa.text("SELECT count(*) FROM session")).scalar_one() == 1

        await client.post("/auth/login", json={"personaId": persona["id"]})

    try:
        with engine.connect() as conn:
            hashes = conn.execute(sa.text("SELECT token_hash FROM session")).scalars().all()
    finally:
        engine.dispose()

    assert "stale-hash" not in hashes
    assert len(hashes) == 1


async def test_an_expired_session_is_rejected_without_committing(seeded_db_url: str) -> None:
    """resolve_session is a pure read: it must not own a transaction
    boundary on the session Story 1.4's repositories will share."""
    engine = sync_engine(seeded_db_url)
    async with make_client(seeded_db_url) as client:
        persona = await persona_named(client, "Marcus Chen", "handler")
        await client.post("/auth/login", json={"personaId": persona["id"]})

        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE session SET expires_at = now() - interval '1 hour'"))

        resp = await client.get("/me")
        assert resp.status_code == 401

    try:
        with engine.connect() as conn:
            # Still present: rejecting an expired cookie is a read, and the
            # row is cleaned up on the next login instead.
            remaining = conn.execute(sa.text("SELECT count(*) FROM session")).scalar_one()
    finally:
        engine.dispose()
    assert remaining == 1
