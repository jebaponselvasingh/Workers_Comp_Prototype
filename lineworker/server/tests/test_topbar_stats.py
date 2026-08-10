"""Story 1.4 AC 1/2/4 — `GET /stats/topbar` over a real seeded database.

The named proof the epics mandate: Jennifer Park's tiles cover only her
three employers while David Bline's cover the whole portfolio, with both
numbers derived from the seed file rather than typed in. A handler runs the
same assertions so all three roles are shown to take the identical scoped
path — the point of AD-7 is that role does not appear in it at all.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from api import create_app
from config import Settings
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

TOPBAR = "/stats/topbar"


@asynccontextmanager
async def make_client(db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(database_url=db_url, env="e2e"))  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def login_as(client: httpx.AsyncClient, name: str, role: str) -> None:
    personas = (await client.get("/personas")).json()["items"]
    match = [p for p in personas if p["name"] == name and p["role"] == role]
    assert len(match) == 1, f"expected exactly one {name}/{role} persona"
    resp = await client.post("/auth/login", json={"personaId": match[0]["id"]})
    assert resp.status_code == 200


async def topbar_for(db_url: str, name: str, role: str, **kwargs: Any) -> dict[str, int]:
    async with make_client(db_url) as client:
        await login_as(client, name, role)
        resp = await client.get(TOPBAR, **kwargs)
        assert resp.status_code == 200, resp.text
        stats: dict[str, int] = resp.json()
        return stats


# --- per-persona numbers (AC 4) ----------------------------------------


@pytest.mark.parametrize(
    ("name", "role"),
    [
        ("Jennifer Park", "supervisor"),  # scoped: Toyota / GM / 3M
        ("David Bline", "supervisor"),  # scope_all: the whole portfolio
        ("Sarah Williams", "handler"),  # scoped: 3M only
        ("David Bline", "analyst"),  # the third role, same path
    ],
)
async def test_tiles_cover_exactly_the_personas_book(
    seeded_db_url: str, name: str, role: str
) -> None:
    assert await topbar_for(seeded_db_url, name, role) == seed_fixture.expected_topbar_stats(
        name, role
    )


async def test_the_full_portfolio_supervisor_sees_all_one_hundred(seeded_db_url: str) -> None:
    """Named in the story text, so asserted literally as well as by derivation."""
    stats = await topbar_for(seeded_db_url, "David Bline", "supervisor")
    assert stats["caseload"] == 100


async def test_a_scoped_supervisor_sees_strictly_less_than_the_portfolio(
    seeded_db_url: str,
) -> None:
    """The pair is the point: equal numbers would pass every test above.

    If scoping silently stopped working, each persona's expectation would
    still be "whatever the seed says" — but Park's book would become the
    whole portfolio, and this is the assertion that notices.
    """
    park = await topbar_for(seeded_db_url, "Jennifer Park", "supervisor")
    bline = await topbar_for(seeded_db_url, "David Bline", "supervisor")

    assert park["caseload"] < bline["caseload"]
    assert park["activeTx"] < bline["activeTx"]
    assert park["highRisk"] < bline["highRisk"]


async def test_the_response_is_camel_case_and_carries_nothing_else(seeded_db_url: str) -> None:
    stats = await topbar_for(seeded_db_url, "Jennifer Park", "supervisor")
    assert set(stats) == {"caseload", "activeTx", "highRisk"}


# --- AC 2: no endpoint accepts caller-supplied scope --------------------


async def test_query_parameters_cannot_widen_or_change_the_scope(seeded_db_url: str) -> None:
    """The smuggling attempt: ask as Park, name Bline's portfolio anyway.

    Unknown parameters are ignored rather than rejected (FastAPI's default,
    and the safer direction — a 422 here would tell an attacker which
    parameter names exist). What matters is that the answer is byte-identical.
    """
    honest = await topbar_for(seeded_db_url, "Jennifer Park", "supervisor")

    for smuggled in (
        {"employerId": "1"},
        {"employer_id": "1"},
        {"employerIds": "1,2,3"},
        {"scopeAll": "true"},
        {"scope_all": "true"},
        {"userId": "7"},
        {"role": "supervisor"},
    ):
        attempt = await topbar_for(seeded_db_url, "Jennifer Park", "supervisor", params=smuggled)
        assert attempt == honest, f"{smuggled} changed the answer"


async def test_the_route_declares_no_parameters_at_all(seeded_db_url: str) -> None:
    """AC 2 structurally: the contract itself offers nowhere to put a scope.

    Asserted against the published OpenAPI document rather than the
    function signature, because the contract is what a client (and a
    reviewer) reads.
    """
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"][TOPBAR]["get"]

    assert operation.get("parameters", []) == []
    assert "requestBody" not in operation


async def test_the_endpoint_requires_a_session(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.get(TOPBAR)
    assert resp.status_code == 401


async def test_a_persona_switch_re_resolves_the_scope(seeded_db_url: str) -> None:
    """Two personas, one client: the second login must not see the first's book.

    Scope rides the caller context, rebuilt per request from `app_user` —
    never cached in the session row (AD-7). A cached scope would show up
    exactly here.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, "David Bline", "supervisor")
        assert (await client.get(TOPBAR)).json()["caseload"] == 100

        await client.post("/auth/logout")
        await login_as(client, "Sarah Williams", "handler")
        assert (await client.get(TOPBAR)).json() == seed_fixture.expected_topbar_stats(
            "Sarah Williams", "handler"
        )
