"""Story 6.2 — an AI narrative is claim data, and AD-7 applies to it as such.

The acceptance criterion names this test, and the reason it is named rather than
left to the general repository guard is what an insight *is*: a paragraph in a
JSONB column, generated from a claim's clinical narrative, its reserve, its
bills and its fraud score. Nothing about the row looks like it belongs to
anybody — there is no employer id on it, no plant, no handler — so it is the one
table in this build where a missing scope predicate would be invisible to
inspection and would leak the whole of a case file in prose.

So the assertions are at **both** levels the story asks for.

At the repository, because that is where the guarantee lives and where a
refactor would remove it, and because both the read *and the write* are scoped
there. The write matters more than it looks: a handler's Refresh button reaches
`upsert_insight` with a claim primary key, and a version of that statement
without the predicate would let a crafted request write a narrative onto another
employer's case file.

Through the service and the API, because a repository-only test would leave the
layers above free to widen the context on the way down — and because the
*single-answer rule* is an API-level property: an out-of-scope claim must 404
with a body indistinguishable from an unknown claim's, or the route becomes an
oracle for enumerating a portfolio the caller cannot read.

Three personas, chosen from the seeded nine for what they prove, the trio
`test_rag_scope.py` already uses:

- **Kaya Johnson**, a handler scoped to four employers with 45 claims.
- **Sarah Williams**, a handler scoped to 3M alone, whose book is disjoint from
  Kaya's — so "Kaya never reads a 3M claim's insights" has content.
- The seeded **system actor**, unbounded, which is the other half of AD-7's rule
  that `ALL` widens the predicate rather than removing it. A test that only
  proved narrowing would pass against a repository that returned nothing at all.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agents.insights import InsightGenerationDeps, refresh_claim_insights
from api import create_app
from config import Settings
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AiInsight, AppUser, Claim
from data.models.enums import InsightKind, UserRole
from data.repositories import insights as insight_repo
from data.repositories.identity import employer_ids_for
from services import rag
from services.claims.detail import ClaimNotVisible
from tests import seed_fixture
from tests.conftest import requires_db
from tests.embedding_fixture import FakeEmbeddingClient
from tests.insight_fixture import FakeChatClient

pytestmark = requires_db

KAYA = ("Kaya Johnson", "handler")
SARAH = ("Sarah Williams", "handler")

#: The instant the fixture rows carry. Fixed rather than `now()` so a test that
#: writes over one can tell its own write apart from the fixture's.
FIXTURE_AT = datetime(2026, 8, 19, 9, 30, tzinfo=UTC)


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def context_for(db: AsyncSession, name: str, role: str) -> CallerContext:
    """Build a caller context the way the API dependency does (AD-7)."""
    user = (
        await db.scalars(sa.select(AppUser).where(AppUser.name == name, AppUser.role == role))
    ).one()
    return CallerContext(
        user_id=user.id,
        role=user.role,
        employer_ids=ALL_EMPLOYERS if user.scope_all else await employer_ids_for(db, user.id),
    )


async def system_for(db: AsyncSession) -> CallerContext:
    user = (await db.scalars(sa.select(AppUser).where(AppUser.role == UserRole.system))).one()
    return CallerContext(user_id=user.id, role=user.role, employer_ids=ALL_EMPLOYERS)


@pytest.fixture
async def sarahs_claim(db: AsyncSession) -> str:
    """A claim in Sarah's book with all four narratives cached.

    Generated through the **shipped command**, under the system actor's
    unbounded context and against a fake chat client — so the rows are real
    content the API can re-validate, and a scope leak in a *read* below cannot
    be masked by the write having been narrow. There is no model server
    involved: that is what the two Protocols are for.

    A hand-written `{"marker": …}` would have been simpler and was the first
    version of this fixture; the route refused it, correctly, because it
    re-validates stored content against the kind's schema on the way out. That
    refusal is a feature this module gets to lean on rather than work around.
    """
    system = await system_for(db)
    claim_business_id = sorted(seed_fixture.expected_claim_ids(*SARAH))[0]
    await refresh_claim_insights(
        db,
        system,
        claim_business_id=claim_business_id,
        deps=InsightGenerationDeps(
            chat=FakeChatClient(), embed=FakeEmbeddingClient(), staleness_days=7
        ),
    )
    return claim_business_id


# --- the repository ------------------------------------------------------


async def test_a_stranger_reads_no_rows_and_resolves_no_claim(
    db: AsyncSession, sarahs_claim: str
) -> None:
    """Both scoped reads refuse, and they refuse in the two different ways they
    have to.

    `select_claim_insights` answers an empty list — which is deliberately the
    same answer a claim with no insights gives, and is why the service resolves
    the claim first. `select_insight_claim_pk` answers `None`, which is what
    turns into the 404, and it answers `None` for an unknown claim too.
    """
    owner = await context_for(db, *SARAH)
    stranger = await context_for(db, *KAYA)

    assert await insight_repo.select_claim_insights(db, owner, claim_business_id=sarahs_claim)
    assert (
        await insight_repo.select_claim_insights(db, stranger, claim_business_id=sarahs_claim) == []
    )

    assert (
        await insight_repo.select_insight_claim_pk(db, owner, claim_business_id=sarahs_claim)
        is not None
    )
    assert (
        await insight_repo.select_insight_claim_pk(db, stranger, claim_business_id=sarahs_claim)
        is None
    )
    assert (
        await insight_repo.select_insight_claim_pk(db, owner, claim_business_id="WC-99999") is None
    )


async def test_the_write_is_scoped_too_and_a_stranger_writes_nothing(
    db: AsyncSession, sarahs_claim: str
) -> None:
    """The half nobody would think to scope, and the one a Refresh button reaches.

    `upsert_insight` takes a claim **primary key**, so a caller who obtained one
    by any means could otherwise write a narrative onto a case file outside their
    book. The statement's `SELECT … WHERE employer_scope(ctx)` produces no source
    row for a stranger, so there is no insert and no conflict either — the call
    reports zero rows and the owner's content is untouched.

    Asserted in both directions: the same call under the owner's context writes,
    which is what stops a version of this passing because the statement was
    broken for everybody.
    """
    owner = await context_for(db, *SARAH)
    stranger = await context_for(db, *KAYA)
    claim_pk = await insight_repo.select_insight_claim_pk(db, owner, claim_business_id=sarahs_claim)
    assert claim_pk is not None
    at = datetime(2026, 8, 20, 9, 30, tzinfo=UTC)

    written = await insight_repo.upsert_insight(
        db,
        stranger,
        claim_pk=claim_pk,
        kind=InsightKind.fraud_risk_indicators,
        content={"marker": "intruder"},
        model="intruder",
        generated_at=at,
    )
    assert written == 0

    written = await insight_repo.upsert_insight(
        db,
        owner,
        claim_pk=claim_pk,
        kind=InsightKind.fraud_risk_indicators,
        content={"marker": "owner"},
        model="owner",
        generated_at=at,
    )
    assert written == 1
    # Rolled back so the intruder's *and* the owner's writes are discarded and
    # the module's shared database keeps the fixture's generated content — the
    # assertion above is about the two row counts, not about what is left.
    await db.rollback()

    stored = (
        await db.scalars(
            sa.select(AiInsight)
            .where(AiInsight.claim_id == claim_pk)
            .where(AiInsight.kind == InsightKind.fraud_risk_indicators)
        )
    ).one()
    assert stored.model == FakeChatClient.model


async def test_the_pending_queue_is_scoped_to_the_callers_book(db: AsyncSession) -> None:
    """`claims_needing_insights` is a read of claim rows and is filtered like one.

    The scheduled job runs it under the system actor, where the predicate is
    AD-7's tautology and the answer is the whole portfolio. The e2e admin
    trigger runs it under the requesting persona, where it must be their book
    and nothing else — which is what makes that route a live assertion rather
    than a convenience.
    """
    system = await system_for(db)
    scoped = await context_for(db, *SARAH)

    everything = set(await rag.claims_needing_insights(db, system, limit=None))
    hers = set(await rag.claims_needing_insights(db, scoped, limit=None))

    # An intersection rather than an equality against her whole book: this
    # module's other tests generate for one of her claims, and the seeded
    # database is shared across the module. What is being asserted is that the
    # scoped queue is exactly the unbounded one narrowed to her employers —
    # which is a stronger statement than "it is a subset" and does not depend on
    # which tests have run.
    assert hers
    assert hers == everything & seed_fixture.expected_claim_ids(*SARAH)
    assert hers < everything


# --- the service ---------------------------------------------------------


async def test_the_service_refuses_a_foreign_claim_exactly_as_it_refuses_an_unknown_one(
    db: AsyncSession, sarahs_claim: str
) -> None:
    """`ClaimNotVisible` for both, which the route turns into one 404.

    The conflation is the security property: two different exceptions would
    become two different responses, and a caller comparing them could walk
    `WC-20000`…`WC-20999` to learn which claims exist.
    """
    stranger = await context_for(db, *KAYA)

    with pytest.raises(ClaimNotVisible):
        await rag.claim_insights(db, stranger, claim_business_id=sarahs_claim)
    with pytest.raises(ClaimNotVisible):
        await rag.claim_insights(db, stranger, claim_business_id="WC-99999")

    owner = await context_for(db, *SARAH)
    assert len(await rag.claim_insights(db, owner, claim_business_id=sarahs_claim)) == len(
        InsightKind
    )


async def test_generation_under_the_system_actor_narrates_no_other_employers_claim(
    db: AsyncSession,
) -> None:
    """The leak the scheduled job had, and the one that scoping a *read* misses.

    Every other assertion in this module is about who may read a row. This one
    is about what goes *into* it, and it is the one AD-7 obligation the
    repository guards cannot express: the scheduled refresh runs as the system
    actor, whose `scope_all` makes `employer_scope` the tautology it is supposed
    to be — so the similar-case gather, which is the one gather that reads
    *other claims*, drew its neighbours from the entire portfolio. Their claim
    ids and their employers' short names were then persisted into
    `ai_insight.content` and served, correctly scoped, to a handler who could
    see the subject claim and nothing else (review of Story 6.2, H1).

    A cached narrative is composed once and read by many, so the scope that
    composed it has to be one every reader is entitled to. The narrowest such
    scope is the subject claim's own employer partition, and
    `services/rag.subject_scoped_context` is where that is applied.

    Asserted on the persisted card rather than on the tool's return value,
    because the leak is only a leak once it is stored: the employer of every
    neighbour named in `content` is looked up in the database and compared
    against the subject's. And it is generated under the **system** actor
    specifically — under a scoped handler the old code would have passed for the
    wrong reason.
    """
    system = await system_for(db)
    subject = sorted(seed_fixture.expected_claim_ids(*SARAH))[0]
    subject_employer = await db.scalar(
        sa.select(Claim.employer_id).where(Claim.claim_id == subject)
    )

    # The whole portfolio, embedded, because `select_similar_claims` excludes
    # rows with a NULL vector — and a database in which only Sarah's claims had
    # vectors would make the assertion below true without the fix. Sarah's
    # employer is the smallest book in the seed, so the neighbours a leaking
    # build would find are overwhelmingly other employers'.
    await rag.refresh_stale_embeddings(db, system, client=FakeEmbeddingClient(), limit=1_000)

    await refresh_claim_insights(
        db,
        system,
        claim_business_id=subject,
        deps=InsightGenerationDeps(
            chat=FakeChatClient(), embed=FakeEmbeddingClient(), staleness_days=7
        ),
    )

    cached = await rag.claim_insights(db, system, claim_business_id=subject)
    similar = next(
        insight for insight in cached if insight.kind is InsightKind.similar_case_outcomes
    )
    neighbours = similar.content["neighbours"]
    assert neighbours, (
        "the similar-case card named no neighbours — this assertion would hold "
        "vacuously against a build that leaked every one of them"
    )

    named = [neighbour["claim_id"] for neighbour in neighbours]
    employers = set(
        (await db.scalars(sa.select(Claim.employer_id).where(Claim.claim_id.in_(named)))).all()
    )
    assert employers == {subject_employer}
    # …and the same fact stated the way a reader would notice it, because the
    # employer's *name* is on the card and a claim id is not obviously anybody's.
    short_names = {neighbour["employer_short_name"] for neighbour in neighbours}
    assert len(short_names) == 1


async def test_the_system_context_sees_every_claims_insights(
    db: AsyncSession, sarahs_claim: str
) -> None:
    """AD-7's other end: `ALL` widens the predicate, it does not remove it.

    The scheduled job's context is the seeded system actor, and it must be able
    to read and generate for the whole portfolio — including a claim no handler
    in this test can see. A version of the predicate that narrowed for everybody
    would pass every assertion above and leave the job generating nothing.
    """
    system = await system_for(db)

    cached = await rag.claim_insights(db, system, claim_business_id=sarahs_claim)
    assert len(cached) == len(InsightKind)


# --- the API -------------------------------------------------------------


@asynccontextmanager
async def make_client(db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """A client against the real app, logged in through the real login route.

    `test_action_checklist.py`'s helper, verbatim, and for its reason: the
    assertions below are about what a *caller* is told, so overriding the auth
    dependency would replace the very thing under test — `get_caller_context`
    is where a request's scope is resolved, and a test that supplied one
    directly would prove nothing about how the route gets it.
    """
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


async def test_the_route_answers_one_404_for_a_foreign_claim_and_an_unknown_one(
    seeded_db_url: str, sarahs_claim: str
) -> None:
    """The single-answer rule, asserted on the two bodies rather than the codes.

    Two responses that differed only in status would already be an oracle; two
    that differ in `type`, `title` or wording are the same oracle with more
    steps. So the bodies are compared whole, modulo the `detail` that echoes
    back the id the caller sent — comparing them *including* `detail` would only
    assert that the route ignores its own parameter.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        foreign = await client.get(f"/claims/{sarahs_claim}/insights")
        unknown = await client.get("/claims/WC-99999/insights")

    assert foreign.status_code == 404
    assert unknown.status_code == 404
    assert {**foreign.json(), "detail": None} == {**unknown.json(), "detail": None}
    assert foreign.json()["detail"] == f"No claim {sarahs_claim} in your caseload."


async def test_the_route_answers_the_owner_with_four_cards(
    seeded_db_url: str, sarahs_claim: str
) -> None:
    """The other half, so the refusal above is not a route that refuses everyone.

    Also the AC 2 shape at the API: four keyed slots, every one of them present,
    each carrying its generation timestamp and the model that wrote it — and
    `Cache-Control: no-store`, because this payload is model output about one
    persona's claim and must never be served to another from an intermediary.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        response = await client.get(f"/claims/{sarahs_claim}/insights")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "similarCaseOutcomes",
        "reserveAdequacyReview",
        "nextBestActions",
        "fraudRiskIndicators",
    }
    for card in payload.values():
        assert card["status"] == "ready"
        assert card["model"] == FakeChatClient.model
        assert card["generatedAt"] is not None
        assert card["content"] is not None
    assert response.headers["cache-control"] == "no-store"


async def test_a_claim_with_no_insights_is_four_empty_cards_and_not_a_404(
    seeded_db_url: str,
) -> None:
    """NFR-3 and AC 2: "nobody has generated this yet" is a state, not an error.

    It is also the state *every* claim is in on a fresh deployment, so answering
    it as a missing resource would send the tab down its error branch for the
    normal case — and would be indistinguishable, from the browser, from the
    404 the test above requires for a claim the caller may not see.
    """
    claim_business_id = sorted(seed_fixture.expected_claim_ids(*KAYA))[0]

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.get(f"/claims/{claim_business_id}/insights")

    assert response.status_code == 200
    for card in response.json().values():
        assert card["status"] == "not_generated"
        assert card["content"] is None
        assert card["model"] is None
        assert card["generatedAt"] is None


async def test_the_refresh_route_answers_one_404_for_a_foreign_claim_and_an_unknown_one(
    seeded_db_url: str, sarahs_claim: str
) -> None:
    """The write route's 404, which had no test at all until the review (M15).

    `POST /claims/{id}/insights/refresh` is the more dangerous of the two to
    leave unasserted: it is the route that *generates*, so a version that
    resolved the claim after building its clients — or that let a `ClaimNotVisible`
    escape as a 500 — would spend four completions on a stranger's claim before
    refusing, and would refuse in a shape a caller can tell apart from an
    unknown claim's. The single-answer rule (AD-7) is asserted on the two bodies
    for `GET`'s reason: two responses differing in `type`, `title` or wording are
    the same enumeration oracle with more steps.

    No model server is involved, which is the point of asserting it here: the
    claim is resolved by `store_insights` **before** any generation, so this
    passes with `OLLAMA_BASE_URL` pointing at nothing.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        foreign = await client.post(f"/claims/{sarahs_claim}/insights/refresh")
        unknown = await client.post("/claims/WC-99999/insights/refresh")

    assert foreign.status_code == 404
    assert unknown.status_code == 404
    assert {**foreign.json(), "detail": None} == {**unknown.json(), "detail": None}
    assert foreign.json()["detail"] == f"No claim {sarahs_claim} in your caseload."


async def test_the_refresh_route_answers_503_when_the_model_server_does_not_answer(
    seeded_db_url: str,
) -> None:
    """The other branch nothing exercised: `MODEL_UNAVAILABLE_RESPONSE` (M15).

    Reached the honest way — the app is built with `OLLAMA_BASE_URL` pointing at
    a port nothing listens on and a one-second chat timeout, so the *shipped*
    `OllamaChatClient` fails its transport, raises `ChatUnavailable`, and the
    route reads `InsightRun.model_unavailable`. Nothing is swapped in Python;
    what is asserted is the composed path from a dead socket to a problem
    document.

    **200-with-counts and 503 are different answers to the same run**, which is
    why this is worth a test rather than an argument: the admin trigger answers
    200 so a degradation spec can read the counts, and this route answers 503
    because a handler is waiting on it. A single status code would have been
    wrong for one of them.

    The 503 body is checked for what it must *not* contain as well: no vendor
    message, no prompt, no claim narrative (AD-11).
    """
    claim_business_id = sorted(seed_fixture.expected_claim_ids(*KAYA))[0]
    settings = Settings(
        database_url=seeded_db_url,
        env="e2e",  # type: ignore[arg-type]
        # A port nothing is bound to, and a timeout short enough that the test
        # costs a connection refusal rather than a wait.
        ollama_base_url="http://127.0.0.1:1",
        chat_request_timeout_seconds=1.0,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await login_as(client, *KAYA)
            response = await client.post(f"/claims/{claim_business_id}/insights/refresh")

    assert response.status_code == 503
    problem = response.json()
    assert problem["title"] == "Model server unavailable"
    assert "127.0.0.1" not in problem["detail"]
    assert claim_business_id not in problem["detail"]

    # And nothing was written: the cards a handler can see through an outage are
    # exactly the cards they could see before it.
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        cards = (await client.get(f"/claims/{claim_business_id}/insights")).json()
    assert all(card["status"] == "not_generated" for card in cards.values())


async def test_a_stored_row_the_schema_no_longer_accepts_is_an_empty_card_not_a_500(
    db: AsyncSession, seeded_db_url: str, sarahs_claim: str
) -> None:
    """M3: one unreadable row is one empty card, and its content never reaches a log.

    The route re-validates stored `content` on the way out, which is right — a
    row written by an older prompt version whose schema has since changed would
    otherwise reach the browser as a shape no component handles. What was wrong
    was the consequence: the `ValidationError` propagated, so **all four** cards
    became a 500, and Pydantic v2 embeds the offending input in its message, so
    the unhandled error was logged with a traceback carrying model prose about a
    claim — the one place in the build where a narrative reached the operational
    log (AD-11).

    Now it is `not_generated`, which is the state the row is really in: no
    content the browser can show, a Refresh button attached, and pressing it
    overwrites the row. The other three cards are unaffected, which is the half
    that matters most to a reader.

    The corrupt row is written with raw SQL for `test_ai_insights.py::_claim_pk`'s
    reason: putting the database into a state no command produces is exactly what
    the owner-only write rule prevents, so it is done past the owner rather than
    by inventing a second writer.
    """
    await db.execute(
        sa.text(
            'UPDATE ai_insight SET content = \'{"outcome": "impossible"}\'::jsonb '
            "WHERE kind = 'fraud_risk_indicators' AND claim_id = "
            "(SELECT id FROM claim WHERE claim_id = :cid)"
        ),
        {"cid": sarahs_claim},
    )
    await db.commit()

    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        response = await client.get(f"/claims/{sarahs_claim}/insights")

    assert response.status_code == 200
    payload = response.json()
    assert payload["fraudRiskIndicators"]["status"] == "not_generated"
    assert payload["fraudRiskIndicators"]["content"] is None
    for key in ("similarCaseOutcomes", "reserveAdequacyReview", "nextBestActions"):
        assert payload[key]["status"] == "ready", f"{key} was taken down by another kind's bad row"
