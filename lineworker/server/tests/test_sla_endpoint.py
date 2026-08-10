"""Story 1.5 AC 1 — `GET /stats/sla` over a real seeded database.

The half of the story that only a database can prove: that the strip is
computed over *the caller's* book, for *every* role, through the same
scoped path as every other read (AD-7). The prototype's gap was precisely
here — `recalcSLA` ran on handler flows only, so a supervisor's strip
showed header defaults nobody had computed.

Expectations come from `seed_fixture.expected_sla_strip`, which re-states
the metric definitions over the seed file rather than importing them from
the service.
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

SLA = "/stats/sla"
METRICS = ("pick", "approve", "settle", "rtwRate")

# Every seeded persona, so "for all three roles" is asserted over the whole
# cast rather than a chosen sample.
PERSONAS = [
    ("Jennifer Park", "supervisor"),
    ("David Bline", "supervisor"),
    ("Ken Stoker", "supervisor"),
    ("Sarah Williams", "handler"),
    ("Kaya Johnson", "handler"),
    ("David Bline", "analyst"),
]


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
    assert (await client.post("/auth/login", json={"personaId": match[0]["id"]})).status_code == 200


async def sla_for(db_url: str, name: str, role: str, **kwargs: Any) -> dict[str, Any]:
    async with make_client(db_url) as client:
        await login_as(client, name, role)
        resp = await client.get(SLA, **kwargs)
        assert resp.status_code == 200, resp.text
        strip: dict[str, Any] = resp.json()
        return strip


# --- AC 1: computed per persona, for every role -------------------------


@pytest.mark.parametrize(("name", "role"), PERSONAS)
async def test_the_strip_covers_exactly_the_personas_book(
    seeded_db_url: str, name: str, role: str
) -> None:
    strip = await sla_for(seeded_db_url, name, role)
    expected = seed_fixture.expected_sla_strip(name, role)

    for metric in METRICS:
        assert strip[metric]["value"] == expected[metric]["value"], metric
        assert strip[metric]["status"] == expected[metric]["status"], metric


@pytest.mark.parametrize(("name", "role"), PERSONAS)
async def test_every_role_gets_four_computed_tiles(
    seeded_db_url: str, name: str, role: str
) -> None:
    """FR-SLA-1 in one assertion: nobody reads a default.

    The prototype's supervisors and analysts saw the markup's placeholder
    values because nothing recomputed the strip for them. Here the same
    endpoint answers for all three roles, and every tile has a number the
    seeded book actually produced.
    """
    strip = await sla_for(seeded_db_url, name, role)

    assert set(strip) == set(METRICS)
    for metric in METRICS:
        assert strip[metric]["value"] is not None, f"{metric} is empty for {name}/{role}"
        assert strip[metric]["status"] in {"pass", "warn"}


async def test_a_scoped_supervisor_and_the_portfolio_disagree(seeded_db_url: str) -> None:
    """The pair that proves scoping, as in Story 1.4.

    Every per-persona expectation above would still pass if scoping broke,
    because each would become "whatever the whole portfolio says". Two
    personas whose books differ must produce different strips.
    """
    stoker = await sla_for(seeded_db_url, "Ken Stoker", "supervisor")
    bline = await sla_for(seeded_db_url, "David Bline", "supervisor")

    assert [stoker[m]["value"] for m in METRICS] != [bline[m]["value"] for m in METRICS]
    # Stoker's book is the seed's one passing RTW rate; the portfolio's misses.
    assert stoker["rtwRate"]["status"] == "pass"
    assert bline["rtwRate"]["status"] == "warn"


# --- AC 2/3: targets and verdicts are the server's ----------------------


async def test_each_metric_carries_its_configured_target_and_direction(
    seeded_db_url: str,
) -> None:
    strip = await sla_for(seeded_db_url, "Jennifer Park", "supervisor")

    for metric in METRICS:
        assert strip[metric]["target"] == seed_fixture.SLA_TARGETS[metric], metric
    assert strip["rtwRate"]["direction"] == "above"
    for metric in ("pick", "approve", "settle"):
        assert strip[metric]["direction"] == "below"

    # The precision the service rounded to, so the client cannot format to
    # a different one and display a figure the server never computed.
    assert [strip[metric]["decimals"] for metric in METRICS] == [1, 1, 0, 0]


async def test_a_configured_target_changes_the_verdict_and_not_the_number(
    seeded_db_url: str,
) -> None:
    """AC 3 end to end: the target reaches the wire from configuration.

    A structural test in `test_sla_aggregation.py` proves the aggregation
    names no literal; this proves the value it reads is the deployment's,
    all the way to the client — with the measurement itself unchanged,
    which is what separates a target from a formula.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, "Jennifer Park", "supervisor")
        strict = (await client.get(SLA)).json()

    lenient_settings = Settings(
        database_url=seeded_db_url,
        env="e2e",  # type: ignore[arg-type]
        sla_pick_target_days=10.0,
        sla_approve_target_days=10.0,
        sla_settle_target_days=100.0,
    )
    app = create_app(lenient_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await login_as(client, "Jennifer Park", "supervisor")
            lenient = (await client.get(SLA)).json()

    for metric in ("pick", "approve", "settle"):
        assert strict[metric]["status"] == "warn", metric
        assert lenient[metric]["status"] == "pass", metric
        assert lenient[metric]["value"] == strict[metric]["value"], metric
        assert lenient[metric]["target"] != strict[metric]["target"], metric


# --- the 1.4 rule, restated for this route ------------------------------


async def test_the_route_declares_no_parameters_at_all(seeded_db_url: str) -> None:
    """No caller-supplied scope, asserted against the published contract."""
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"][SLA]["get"]

    assert operation.get("parameters", []) == []
    assert "requestBody" not in operation


async def test_query_parameters_cannot_widen_or_change_the_scope(seeded_db_url: str) -> None:
    honest = await sla_for(seeded_db_url, "Ken Stoker", "supervisor")

    for smuggled in (
        {"employerId": "1"},
        {"employerIds": "1,2,3"},
        {"scopeAll": "true"},
        {"userId": "7"},
        {"role": "supervisor"},
    ):
        attempt = await sla_for(seeded_db_url, "Ken Stoker", "supervisor", params=smuggled)
        assert attempt == honest, f"{smuggled} changed the answer"


async def test_the_endpoint_requires_a_session(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.get(SLA)
    assert resp.status_code == 401


async def test_the_response_is_camel_case_and_carries_nothing_else(seeded_db_url: str) -> None:
    strip = await sla_for(seeded_db_url, "Jennifer Park", "supervisor")

    assert set(strip) == set(METRICS)
    for metric in METRICS:
        assert set(strip[metric]) == {"value", "target", "direction", "decimals", "status"}
