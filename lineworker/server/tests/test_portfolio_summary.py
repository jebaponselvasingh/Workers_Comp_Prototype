"""Story 5.1 — the portfolio KPI cards, over a real seeded database.

Every figure is checked against `seed_fixture.expected_portfolio_summary`,
which re-counts `seed_data.json` independently: the expectations here share no
code with the aggregate under test, so a fold that was wrong in the same way as
its oracle is not a state these tests can reach.

Three things beyond "the numbers are right" are what this file is actually
about, because they are the properties the story is exposed on:

- **Scope, not role.** Jennifer Park's ten figures are strictly smaller than
  David Bline's and every claim behind them belongs to one of her three
  employers. Equal numbers would pass every "expected == seed" assertion above
  while scoping had silently stopped working — `test_topbar_stats.py`'s pairing,
  applied to ten figures instead of three.
- **The thresholds are the document's.** Two card captions quote numbers the
  response carries, so the response is compared against `thresholds_for(db)`
  rather than against literals, and a v6 inserted effective today moves the
  count *and* the published cut-off with nothing deployed.
- **The endpoint has nowhere to put a scope.** Asserted against the published
  OpenAPI contract, which is what a client and a reviewer read.

The DB-backed tests **mutate** the seeded portfolio (the supersession test
inserts and removes a rule document). The module-scoped `seeded_db_url` fixture
rebuilds the schema for this file, so the mutation is contained here and is
rolled back within its own test regardless.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.context import CallerContext
from data.models import AppUser, AuditEvent, Claim, Employer
from data.models.enums import UserRole
from data.repositories.identity import employer_ids_for
from rules.engine import utc_today
from rules.parameters import DERIVATION_THRESHOLDS_KEY, thresholds_for
from services.worklist import portfolio_summary
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

SUMMARY = "/dashboard/summary"

BLINE = ("David Bline", "supervisor")
PARK = ("Jennifer Park", "supervisor")
ANALYST = ("David Bline", "analyst")

#: The ten cards plus the chip's two counts — every key the oracle predicts.
#: Named rather than derived from a response, so a field that silently
#: disappeared from the payload would fail rather than shrink the comparison.
FIGURE_KEYS = (
    "totalClaims",
    "underTreatment",
    "settledClosed",
    "highRisk",
    "totalPaidCents",
    "totalReserveCents",
    "fraudFlagged",
    "oshaRecordable",
    "litigation",
    "surgeryRequired",
    "employerCount",
    "plantCount",
)


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


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


async def summary_for(db_url: str, name: str, role: str, **kwargs: Any) -> dict[str, int]:
    async with make_client(db_url) as client:
        await login_as(client, name, role)
        resp = await client.get(SUMMARY, **kwargs)
        assert resp.status_code == 200, resp.text
        payload: dict[str, int] = resp.json()
        return payload


def figures(payload: dict[str, int]) -> dict[str, int]:
    """The twelve counted values, without the three rule-provenance fields."""
    return {key: payload[key] for key in FIGURE_KEYS}


# --- the ten figures, per persona (AC 1, 2) -----------------------------


@pytest.mark.parametrize(
    ("name", "role"),
    [
        BLINE,  # scope_all: the whole portfolio
        PARK,  # scoped: Toyota / GM / 3M
        ANALYST,  # the second role that lands on this dashboard
        ("Sarah Williams", "handler"),  # a third role, taking the identical path
    ],
)
async def test_the_cards_cover_exactly_the_personas_book(
    seeded_db_url: str, name: str, role: str
) -> None:
    payload = await summary_for(seeded_db_url, name, role)
    assert figures(payload) == seed_fixture.expected_portfolio_summary(name, role)


async def test_the_full_portfolio_supervisor_sees_all_one_hundred(seeded_db_url: str) -> None:
    """Named in the story text, so asserted literally as well as by derivation."""
    payload = await summary_for(seeded_db_url, *BLINE)

    assert payload["totalClaims"] == 100
    assert payload["employerCount"] == 10


async def test_the_dataset_chips_plant_count_is_counted_not_the_prototypes_literal(
    seeded_db_url: str,
) -> None:
    """A recorded departure from the prototype (Design Note 5).

    `renderSV` writes "15 US plants" into the chip while its own array holds 29
    distinct plants, so the literal is wrong for the one portfolio it was
    written for and wrong differently for every scoped persona. Counting it is
    the fix, and this asserts the fix rather than the literal — pinned against
    the seed so it stays a statement about the data.
    """
    payload = await summary_for(seeded_db_url, *BLINE)
    distinct = len({claim["plant"] for claim in seed_fixture.seed()["claims"]})

    assert payload["plantCount"] == distinct
    assert payload["plantCount"] != 15


async def test_settled_and_closed_counts_the_stage_not_the_status(seeded_db_url: str) -> None:
    """The other recorded departure (Design Note 3).

    `renderSV` filters `status === "Settled & Closed"`; this counts
    `stage = settled`, which is what the queue's stage groups, the SLA strip's
    settled segment and 5.3's donut all group by. The two disagree on eight
    seeded claims, and a card that disagreed with the donut beneath it is the
    failure AD-10 exists to prevent — so the delta is asserted rather than left
    to be rediscovered as a defect.
    """
    payload = await summary_for(seeded_db_url, *BLINE)
    claims = seed_fixture.seed()["claims"]
    by_stage = sum(1 for claim in claims if claim["stage"] == "settled")
    by_status = sum(1 for claim in claims if claim["status"] == "settled_closed")

    assert payload["settledClosed"] == by_stage
    assert by_status < by_stage, "the two rules no longer differ — this test has stopped asserting"


# --- scope, not role (AC 3, 4) ------------------------------------------


async def test_a_scoped_supervisor_sees_strictly_less_than_the_portfolio(
    seeded_db_url: str,
) -> None:
    """The pair is the point: equal numbers would pass every test above.

    If scoping silently stopped working, each persona's expectation would still
    be "whatever the seed says" — but Park's book would become the whole
    portfolio, and this is the assertion that notices.

    All twelve, including the two the chip carries: a supervisor told she is
    looking at ten employers when she can see three is the same defect as a
    count that did not narrow, one line further down the screen.

    **`<=` per key, and a strict `<` on the two that cannot tie.** Asserting
    strict inequality on every key would state the same property while quietly
    depending on the seed's *contents*: Park's `litigation` is 0 against
    Bline's 3 and would tie the moment anyone moved or removed those three
    litigated claims, turning a scoping assertion into a red test about data.
    Any card can legitimately read zero for both personas — an empty band is
    not a broken predicate. What cannot happen under working scoping is a
    proper subset with as many claims, or as many employers, as the whole
    portfolio: those two are strictly smaller for structural reasons (Park
    holds three of the ten employers, and every employer here has claims), and
    together with `<=` everywhere they say "narrowed, and narrowed on every
    figure" without leaning on which claims happen to be litigated.
    """
    park = await summary_for(seeded_db_url, *PARK)
    bline = await summary_for(seeded_db_url, *BLINE)

    for key in FIGURE_KEYS:
        assert park[key] <= bline[key], f"{key} grew under a narrower scope"

    assert park["totalClaims"] < bline["totalClaims"]
    assert park["employerCount"] < bline["employerCount"]


async def test_every_claim_behind_a_scoped_personas_cards_is_inside_her_book(
    db: AsyncSession,
) -> None:
    """AC 3's named proof, taken at the aggregate's own read path.

    The counts agreeing with an oracle shows the arithmetic; this shows the
    *set*. Her context is built the way `api/deps.py` builds it, the aggregate
    is called directly, and the employers behind the rows it folded are read
    back from the database — so a widened predicate would show up as an
    employer she is not assigned to rather than as a number that happened to
    match.
    """
    park = (
        await db.scalars(sa.select(AppUser).where(AppUser.name == PARK[0], AppUser.role == PARK[1]))
    ).one()
    # Read as a bare frozenset rather than off `CallerContext.employer_ids`,
    # which is the `ALL_EMPLOYERS | frozenset` union — a scoped persona is
    # precisely the case where the union has one inhabitant, and narrowing it
    # here keeps the SQL below honest about what it is filtering on.
    employer_ids = await employer_ids_for(db, park.id)
    ctx = CallerContext(user_id=park.id, role=park.role, employer_ids=employer_ids)

    assigned = set(
        (await db.scalars(sa.select(Employer.name).where(Employer.id.in_(employer_ids)))).all()
    )
    assert assigned == seed_fixture.employers_of(*PARK)

    visible = await db.scalars(
        sa.select(Employer.name)
        .join(Claim, Claim.employer_id == Employer.id)
        .where(Claim.employer_id.in_(employer_ids))
        .distinct()
    )
    assert set(visible.all()) == assigned

    result = await portfolio_summary(db, ctx, await thresholds_for(db))
    assert result.employer_count == len(assigned)
    assert result.total_claims == len(seed_fixture.claims_for(*PARK))


async def test_an_empty_book_reports_zeros_rather_than_failing(db: AsyncSession) -> None:
    """The NFR-3 zero state, at the service rather than through a session.

    A persona assigned to no employers is a legitimate state (a supervisor
    between assignments), and `employer_scope` reads it as a predicate matching
    nothing rather than as "no filter". Ten zeros is the correct answer; the
    two thresholds and the chip's counts still arrive, because "nothing in the
    book" says nothing about which rules were in force.
    """
    thresholds = await thresholds_for(db)
    result = await portfolio_summary(
        db,
        CallerContext(user_id=0, role=UserRole.supervisor, employer_ids=frozenset()),
        thresholds,
    )

    assert result.total_claims == 0
    assert result.under_treatment == 0
    assert result.settled_closed == 0
    assert result.high_risk == 0
    assert result.total_paid_cents == 0
    assert result.total_reserve_cents == 0
    assert result.fraud_flagged == 0
    assert result.osha_recordable == 0
    assert result.litigation == 0
    assert result.surgery_required == 0
    assert result.employer_count == 0
    assert result.plant_count == 0
    assert result.high_risk_severity_min == thresholds.risk_high_min
    assert result.fraud_score_min == thresholds.fraud_flag_score_min


# --- the thresholds are the document's (AC 2, AD-8) ---------------------


async def test_the_published_thresholds_come_from_the_rule_document(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """Compared against the JDM resolution path, never against a literal.

    A test asserting `highRiskSeverityMin == 65` would keep passing if the
    endpoint had hardcoded it, which is the one thing worth checking here.
    """
    thresholds = await thresholds_for(db)
    payload = await summary_for(seeded_db_url, *BLINE)

    assert payload["highRiskSeverityMin"] == thresholds.risk_high_min
    assert payload["fraudScoreMin"] == thresholds.fraud_flag_score_min
    assert payload["rulesVersion"] == thresholds.version
    # And the dashboard's cut-off is not the queue's — the whole reason this
    # story added a parameter rather than reusing one.
    assert payload["fraudScoreMin"] != thresholds.siu_fraud_score_min


async def test_the_fraud_card_counts_the_review_rule_not_the_siu_referral_rule(
    seeded_db_url: str,
) -> None:
    """13, not 9 (Design Note 2), both restated from the seed.

    The two rules read one column pair and differ only in a threshold, so the
    wrong one produces a number that looks entirely reasonable. Asserting both
    populations is what makes the difference visible in a failure message.
    """
    payload = await summary_for(seeded_db_url, *BLINE)
    claims = seed_fixture.seed()["claims"]
    reviewed = sum(
        1
        for claim in claims
        if claim["fraud_flag"] and claim["fraud_score"] >= seed_fixture.FRAUD_FLAG_SCORE_MIN
    )
    referred = sum(
        1
        for claim in claims
        if claim["fraud_flag"] and claim["fraud_score"] >= seed_fixture.SIU_FRAUD_SCORE_MIN
    )

    assert payload["fraudFlagged"] == reviewed
    assert payload["fraudFlagged"] != referred


async def test_a_superseded_rule_document_moves_the_count_and_the_caption(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """AD-8 end to end, and the only demonstration of it worth anything.

    A v6 of `derivation_thresholds` raising `fraudFlagScoreMin` is inserted
    effective today; the same request comes back with fewer flagged claims
    *and* a higher published cut-off, so the card's caption follows the
    document. Nothing is deployed, nothing is restarted and no Python changes.

    Rolled back afterwards so the rest of the module sees the seeded document,
    and read back before the rollback because the assertion is about what a
    request answered while the version was live.
    """
    raised = 70
    before = await summary_for(seeded_db_url, *BLINE)

    content = (
        await db.execute(
            sa.text(
                "SELECT content FROM rule_document WHERE key = :key ORDER BY version DESC LIMIT 1"
            ),
            {"key": DERIVATION_THRESHOLDS_KEY},
        )
    ).scalar_one()
    await db.execute(
        sa.text(
            "INSERT INTO rule_document (key, version, effective_from, content, created_at) "
            "VALUES (:key, 6, :today, CAST(:content AS jsonb), now())"
        ),
        {
            "key": DERIVATION_THRESHOLDS_KEY,
            # `utc_today()`, not `date.today()`: `rules/engine.py` filters
            # `effective_from <= as_of` against a UTC-derived today, so a local
            # date east of UTC near midnight runs a day ahead, the document is
            # not yet effective, and this fails as if the loader were broken.
            "today": utc_today(),
            "content": json.dumps(_retuned(content, fraudFlagScoreMin=raised)),
        },
    )
    await db.commit()

    try:
        after = await summary_for(seeded_db_url, *BLINE)
        expected = sum(
            1
            for claim in seed_fixture.seed()["claims"]
            if claim["fraud_flag"] and claim["fraud_score"] >= raised
        )

        assert expected < before["fraudFlagged"], "the retune must actually narrow the population"
        assert after["fraudFlagged"] == expected
        # The caption's number followed the count, which is the half a client
        # holding its own constant would have got wrong.
        assert after["fraudScoreMin"] == raised
        assert after["rulesVersion"] == 6
        # Nothing else moved: one parameter changed, one card changed.
        assert after["totalClaims"] == before["totalClaims"]
        assert after["highRisk"] == before["highRisk"]
    finally:
        await db.execute(
            sa.text("DELETE FROM rule_document WHERE key = :key AND version = 6"),
            {"key": DERIVATION_THRESHOLDS_KEY},
        )
        await db.commit()


def _retuned(content: dict[str, Any], **overrides: int) -> dict[str, Any]:
    """A copy of a thresholds document with named expressions given new values.

    The document's expression values are *strings* of ZEN expressions, which is
    why the override is stringified rather than assigned: a JSON number in that
    slot is a different thing from the expression `"70"`, and only one of them
    evaluates.
    """
    retuned: dict[str, Any] = json.loads(json.dumps(content))
    seen: set[str] = set()
    for node in retuned["nodes"]:
        for expression in node.get("content", {}).get("expressions", []):
            if expression["key"] in overrides:
                expression["value"] = str(overrides[expression["key"]])
                seen.add(expression["key"])
    assert seen == set(overrides), f"no such expression: {sorted(set(overrides) - seen)}"
    return retuned


# --- the contract (AC 3) ------------------------------------------------


async def test_the_route_declares_no_parameters_at_all(seeded_db_url: str) -> None:
    """AD-7 structurally: the contract itself offers nowhere to put a scope.

    Asserted against the published OpenAPI document rather than the function
    signature, because the contract is what a client (and a reviewer) reads.
    """
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"][SUMMARY]["get"]

    assert operation.get("parameters", []) == []
    assert "requestBody" not in operation


async def test_query_parameters_cannot_widen_or_change_the_scope(seeded_db_url: str) -> None:
    """The smuggling attempt: ask as Park, name Bline's portfolio anyway.

    Unknown parameters are ignored rather than rejected (FastAPI's default, and
    the safer direction — a 422 here would tell an attacker which parameter
    names exist). What matters is that the answer is byte-identical.
    """
    honest = await summary_for(seeded_db_url, *PARK)

    for smuggled in (
        {"employerId": "1"},
        {"employerIds": "1,2,3"},
        {"scopeAll": "true"},
        {"userId": "7"},
        {"role": "supervisor"},
    ):
        attempt = await summary_for(seeded_db_url, *PARK, params=smuggled)
        assert attempt == honest, f"{smuggled} changed the answer"


async def test_the_response_is_camel_case_and_carries_nothing_else(seeded_db_url: str) -> None:
    payload = await summary_for(seeded_db_url, *PARK)
    assert set(payload) == {
        *FIGURE_KEYS,
        "highRiskSeverityMin",
        "fraudScoreMin",
        "rulesVersion",
    }


async def test_the_response_is_never_cached(seeded_db_url: str) -> None:
    """One persona's portfolio must not be served to another from upstream."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *PARK)
        resp = await client.get(SUMMARY)
    assert resp.headers["cache-control"] == "no-store"


async def test_the_endpoint_requires_a_session(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.get(SUMMARY)

    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


async def test_reading_the_dashboard_writes_no_audit_event(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """A query is not a mutation (Epic 5 preamble, story Task 3).

    Counted before and after rather than asserted absent, because other rows
    exist: what must not happen is this request adding one.
    """
    count = sa.select(sa.func.count()).select_from(AuditEvent)
    before = (await db.execute(count)).scalar_one()

    await summary_for(seeded_db_url, *BLINE)

    await db.commit()  # a new snapshot, or the count would be the old one
    assert (await db.execute(count)).scalar_one() == before
