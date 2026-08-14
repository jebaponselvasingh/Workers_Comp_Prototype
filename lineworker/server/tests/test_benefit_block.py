"""Story 3.1 AC 1 and 2 — the benefit on the case file, over a real database.

`test_benefit_calculation.py` proves the arithmetic. This is about the
*contract*: that every claim carries a benefit block whatever stage it is in,
that every figure on it crosses as integer cents, that the one formatted amount
is prose rather than a figure, and that a jurisdiction with no schedule is a
visible refusal rather than a quiet default (NFR-4).

Driven through the app, because what the story promises is a payload.
"""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.models import Claim
from services.derivations import IndemnityType
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

KAYA = ("Kaya Johnson", "handler")
#: The full-portfolio supervisor, so "the same claim" needs no book
#: intersection to be computed — Jennifer Park's three employers do not
#: include Kaya's, which is what the first draft of this file got wrong.
DAVID = ("David Bline", "supervisor")

SERVER_ROOT = Path(__file__).resolve().parents[1]
FINANCIALS = SERVER_ROOT / "services" / "financials"

#: Every field the benefit block publishes. Written out so that a field added
#: without a story behind it fails here, and so that a field *removed* fails in
#: the same place rather than as a `KeyError` in a component.
BENEFIT_FIELDS = {
    "weeklyCents",
    "compRateBp",
    "defaultCompRateBp",
    "isOverridden",
    "indemnityType",
    "stateCode",
    "stateName",
    "stateMinCents",
    "stateMaxCents",
    "scheduleEffectiveDate",
    "waitingDays",
    "reserveRationale",
    "compRateMinBp",
    "compRateMaxBp",
    "paramsVersion",
}


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


async def detail_for(db_url: str, persona: tuple[str, str], claim_id: str) -> dict[str, Any]:
    async with make_client(db_url) as client:
        await login_as(client, *persona)
        resp = await client.get(f"/claims/{claim_id}")
        assert resp.status_code == 200, resp.text
        payload: dict[str, Any] = resp.json()
        return payload


def a_claim_in_stage(persona: tuple[str, str], stage: str) -> str:
    claims = sorted(
        str(claim["claim_id"])
        for claim in seed_fixture.claims_for(*persona)
        if claim["stage"] == stage
    )
    assert claims, f"no seeded {stage} claim for {persona}"
    return claims[0]


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


# --- the block, at every stage ------------------------------------------


@pytest.mark.parametrize("stage", ["intake", "investigation", "treatment", "settled"])
async def test_every_stage_carries_a_complete_benefit_block(seeded_db_url: str, stage: str) -> None:
    """Outside the stage-variant union, and at every stage.

    The *card* renders on two variants — the two the prototype puts it on —
    but the figure is a fact about the claim throughout: a settled claim was
    paid a weekly indemnity, and an intake claim already has a wage, a state
    and a disability. Story 3.3's payment schedule is a Bills-tab surface and
    needs the same figure whatever stage the claim is in.
    """
    payload = await detail_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, stage))

    assert set(payload["benefit"]) == BENEFIT_FIELDS
    benefit = payload["benefit"]
    assert benefit["stateCode"] == payload["header"]["state"]
    assert benefit["indemnityType"] in {member.value for member in IndemnityType}
    assert benefit["stateMinCents"] <= benefit["weeklyCents"] <= benefit["stateMaxCents"]


async def test_a_supervisor_reading_a_case_file_gets_the_same_benefit(
    seeded_db_url: str,
) -> None:
    """The block is not handler-only: role gates *capability* (the override
    command), scope gates visibility. A supervisor reviewing a reserve has to
    see the figure the reserve is set against."""
    claim_id = a_claim_in_stage(KAYA, "treatment")
    handler_view = await detail_for(seeded_db_url, KAYA, claim_id)
    supervisor_view = await detail_for(seeded_db_url, DAVID, claim_id)

    assert supervisor_view["benefit"] == handler_view["benefit"]


# --- AC 2: money is cents on the wire, formatted only in prose ----------


async def test_every_money_field_crosses_as_an_integer(seeded_db_url: str) -> None:
    payload = await detail_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, "investigation"))
    benefit = payload["benefit"]

    for field in ("weeklyCents", "stateMinCents", "stateMaxCents"):
        value = benefit[field]
        assert isinstance(value, int) and not isinstance(value, bool), field


async def test_the_only_formatted_money_on_the_block_is_the_rationale(
    seeded_db_url: str,
) -> None:
    """AC 2, stated as the thing that would break it.

    A currency symbol anywhere but the paragraph means a figure was formatted
    server-side, which is the convention this contract exists to keep — and it
    is an easy mistake to make on a card whose whole subject is money. The
    paragraph is exempt because it is a *sentence*: see
    `services/financials/rationale.py` on why a template with holes in it for
    the browser to fill would be worse.
    """
    payload = await detail_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, "treatment"))
    benefit = dict(payload["benefit"])
    rationale = benefit.pop("reserveRationale")

    assert "$" in rationale, "the paragraph quotes the reserve and the statutory bounds"
    assert "$" not in repr(benefit)


async def test_no_indemnity_label_crosses_the_wire(seeded_db_url: str) -> None:
    """The Enums convention: the token travels, the UI owns the label.

    The prototype's value is the label and the abbreviation glued together
    (`"TTD — Temporary Total Disability"`), and it recovers the short form with
    a `.split(" — ")`. A payload carrying that string would put display text in
    the contract and make the browser parse it back apart.
    """
    payload = await detail_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, "settled"))

    assert "Disability" not in repr(payload["benefit"])


# --- NFR-4: a jurisdiction with no schedule -----------------------------


async def test_a_claim_in_an_uncovered_state_refuses_rather_than_defaulting(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """The prototype's `|| {max:1200, min:250}`, deliberately not reproduced.

    The state is moved to one nobody has rates for, which is the only way to
    reach this against a correctly migrated database (0023 refuses to complete
    while a seeded claim is uncovered). The whole case file must fail — the
    benefit is part of the payload, and blanking one card while serving the
    rest would be the silent answer in a different shape.

    **The refusal says nothing about the claim** (AD-11): a two-letter state
    code is claim data, so it goes to the structured log and the body carries
    a sentence the caller can act on instead.
    """
    claim_id = a_claim_in_stage(KAYA, "settled")
    original = (
        await db.execute(sa.select(Claim.state).where(Claim.claim_id == claim_id))
    ).scalar_one()
    await db.execute(sa.update(Claim).where(Claim.claim_id == claim_id).values(state="ZZ"))
    await db.commit()

    try:
        async with make_client(seeded_db_url) as client:
            await login_as(client, *KAYA)
            resp = await client.get(f"/claims/{claim_id}")
    finally:
        await db.execute(sa.update(Claim).where(Claim.claim_id == claim_id).values(state=original))
        await db.commit()

    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"] == "/problems/missing-state-rate"
    assert "ZZ" not in body["detail"]
    assert claim_id not in body["detail"]


# --- AD-2 / AD-10: one reader of the PTD condition ----------------------


def test_the_financial_engine_never_compares_a_severity_score() -> None:
    """The placement argument, as a source-level guard.

    `compute_benefit` asks the registered `indemnity_type` derivation whether a
    claim is permanently *totally* disabled and pays accordingly; it must not
    band `severity_score` itself. A second comparison here would be a second
    reader of `ptdSeverityThreshold` — one of them in a document, one of them
    in Python — which is exactly what AD-8's "each rule element in exactly one
    tier" and AD-10's "exactly one computing function" both forbid.

    Blunt on purpose, in the manner of
    `test_derivations.py::test_no_module_outside_the_registry_hardcodes_the_band`:
    it reads the sources as text, so the fix is to delete the comparison rather
    than to argue with the test.
    """
    comparison = re.compile(r"severity_score\s*(?:<=|>=|<|>|==|!=)")
    offenders = [
        path.name
        for path in FINANCIALS.glob("*.py")
        # Docstrings and comments are the *reason* none of this is here, and
        # they name the field freely; only code is scanned.
        if comparison.search(re.sub(r'"""(?:.|\n)*?"""|#[^\n]*', "", path.read_text()))
    ]

    assert offenders == [], (
        "these modules band a severity score inside the financial engine; the "
        f"registered indemnity_type derivation is the one place that decides: {offenders}"
    )
