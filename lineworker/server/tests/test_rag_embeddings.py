"""Story 6.1 — the composer, the refresh command and the dimension guard.

Four separable claims, and only the middle two need a database:

1. **The composer is deterministic** — same claim state, byte-identical text
   and hash — and **detects real change**, field by field. Hypothesis over
   permutations for the first half, because the failure it guards against is
   the one no example test finds: a set or dict iterated in an order that
   happens to be stable for the values somebody typed into a test.
2. **The refresh does what it says**: stale rows before never-embedded ones, at
   most `limit` per run, `embedded_at` on every result, idempotent on a second
   call, and survivable when the model server is unreachable.
3. **`mark_claim_stale` is an idempotent upsert that does not commit** — the
   contract the four wired commands depend on.
4. **A wrong-width vector is refused by name**, in the client where the check
   lives, and takes the run down rather than writing anything.

The `EmbeddingClient` protocol is what makes 2 and 3 runnable at all; see
`tests/embedding_fixture.py`.
"""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, date, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AdditionalInjury, AppUser, Claim, Employer
from data.models.enums import UserRole
from data.repositories import embeddings as embedding_repo
from data.repositories.identity import employer_ids_for
from services.rag import (
    EMBEDDING_DIMENSIONS,
    EmbeddingDimensionMismatch,
    compose_and_hash,
    compose_claim_text,
    embed_claims,
    mark_claim_stale,
    refresh_stale_embeddings,
    search_knowledge,
    similar_claims,
    text_hash,
)
from services.rag.client import OllamaEmbeddingClient
from tests.conftest import requires_db
from tests.embedding_fixture import FakeEmbeddingClient, UnreachableEmbeddingClient

SEEDED_CLAIMS = 100
SEEDED_CHUNKS = 15


async def first_claim_pk(db: "AsyncSession") -> int:
    """The lowest claim surrogate key, narrowed to `int` for mypy.

    `db.scalars(...).first()` is `int | None` by signature and never `None`
    against a seeded database; asserting it here rather than at six call
    sites keeps the tests reading as tests.
    """
    pk = (await db.execute(sa.select(Claim.id).order_by(Claim.id))).scalars().first()
    assert pk is not None
    return pk


# --- 1. the composer, with no database -----------------------------------


#: The claim columns the composer reads, as a Hypothesis strategy per field.
#: Text values include the empty string and whitespace on purpose: those are
#: values `services/claims/edit.py` refuses at the API boundary but that the
#: composer must still handle deterministically, because it is also reached
#: from the refresh job over whatever is already in the database.
CLAIM_TEXT_FIELDS = (
    "injury_type",
    "cause",
    "body_part",
    "icd",
    "icd_desc",
    "disability",
    "recovery",
    "stage",
    "status",
    "return_status",
)


@st.composite
def claim_states(draw: st.DrawFn) -> dict[str, Any]:
    """One claim's composer-visible state, as a plain dict."""
    state: dict[str, Any] = {field: draw(st.text(max_size=40)) for field in CLAIM_TEXT_FIELDS}
    state["severity_score"] = draw(st.integers(min_value=0, max_value=100))
    state["surgery_required"] = draw(st.booleans())
    state["rtw_rec"] = draw(st.none() | st.dates())
    state["actual_rtw"] = draw(st.none() | st.dates())
    state["sector"] = draw(st.text(max_size=30))
    return state


def build(state: dict[str, Any]) -> tuple[Claim, Employer]:
    """Transient ORM objects — no session, no identity map, no database.

    The composer takes its three inputs as arguments precisely so this is
    possible: a function that had to load a rule document or a child row could
    not be a Hypothesis property.
    """
    claim = Claim(
        injury_type=state["injury_type"],
        cause=state["cause"],
        body_part=state["body_part"],
        icd=state["icd"],
        icd_desc=state["icd_desc"],
        severity_score=state["severity_score"],
        disability=state["disability"],
        recovery=state["recovery"],
        surgery_required=state["surgery_required"],
        stage=state["stage"],
        status=state["status"],
        return_status=state["return_status"],
        rtw_rec=state["rtw_rec"],
        actual_rtw=state["actual_rtw"],
    )
    return claim, Employer(sector=state["sector"])


@settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
@given(state=claim_states())
def test_the_composer_is_deterministic(state: dict[str, Any]) -> None:
    """Same claim state ⇒ byte-identical text and hash.

    The property that makes `source_text_hash` mean "the source changed"
    rather than "something was iterated in a different order". Without it every
    refresh run finds every hash different, re-embeds the whole portfolio, and
    the `stale` column stops distinguishing anything.
    """
    first_claim, first_employer = build(state)
    second_claim, second_employer = build(state)

    first_text, first_hash = compose_and_hash(first_claim, first_employer, [])
    second_text, second_hash = compose_and_hash(second_claim, second_employer, [])

    assert first_text == second_text
    assert first_hash == second_hash
    assert first_hash == text_hash(first_text)


@settings(max_examples=40, suppress_health_check=[HealthCheck.too_slow])
@given(state=claim_states(), data=st.data())
def test_the_composer_does_not_depend_on_injury_row_order(
    state: dict[str, Any], data: st.DataObject
) -> None:
    """Secondary injuries are sorted by id inside the composer.

    The repository also orders them, deliberately — but a composer that trusted
    its caller for the one property it exists to guarantee would turn a change
    to a query plan into a portfolio-wide re-embed that nothing explains.
    """
    injuries = [
        AdditionalInjury(
            id=index,
            body_part=data.draw(st.text(max_size=20)),
            injury_type=data.draw(st.text(max_size=20)),
            severity_score=data.draw(st.integers(min_value=0, max_value=100)),
        )
        for index in (3, 1, 2)
    ]
    claim, employer = build(state)

    forwards = compose_claim_text(claim, employer, injuries)
    backwards = compose_claim_text(claim, employer, list(reversed(injuries)))

    assert forwards == backwards
    assert forwards.count("Additional injury:") == 3


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("injury_type", "Fracture"),
        ("cause", "Slip on wet floor"),
        ("body_part", "Lower Back"),
        ("icd", "S43.401A"),
        ("icd_desc", "Unspecified sprain of right shoulder joint"),
        ("severity_score", 7),
        ("disability", "temporary_total"),
        ("recovery", "6_12_weeks"),
        ("surgery_required", True),
        ("stage", "treatment"),
        ("status", "open"),
        ("return_status", "modified_duty"),
        ("rtw_rec", date(2026, 5, 4)),
        ("actual_rtw", date(2026, 6, 1)),
        ("sector", "Aerospace"),
    ],
)
def test_the_composer_notices_every_field_it_reads(field: str, changed: object) -> None:
    """Change one composer input ⇒ different text and a different hash.

    The other half of the contract, and the one that fails silently: a field
    left out of the composition means a claim whose vector describes its
    pre-edit self, with a fresh `embedded_at` beside it to say otherwise.

    Enumerated rather than reflective, because "which fields should be in the
    summary?" is a design decision and this list is where it is recorded. A
    story that adds a field to the composer adds a row here; a story that
    removes one has to delete a row and explain why.
    """
    base = {
        **{name: "baseline" for name in CLAIM_TEXT_FIELDS},
        "severity_score": 4,
        "surgery_required": False,
        "rtw_rec": None,
        "actual_rtw": None,
        "sector": "Automotive",
    }
    before_text, before_hash = compose_and_hash(*build(base), [])
    after_text, after_hash = compose_and_hash(*build({**base, field: changed}), [])

    assert before_text != after_text, field
    assert before_hash != after_hash, field


def test_a_secondary_injury_changes_the_summary() -> None:
    """`add_/remove_additional_injury` widen what "the claim changed" means.

    Neither command writes a `claim` column, so nothing else in the codebase
    would call the claim modified. This is the assertion that says why they are
    wired to `mark_claim_stale` anyway.
    """
    base = {
        **{name: "baseline" for name in CLAIM_TEXT_FIELDS},
        "severity_score": 4,
        "surgery_required": False,
        "rtw_rec": None,
        "actual_rtw": None,
        "sector": "Automotive",
    }
    claim, employer = build(base)
    injury = AdditionalInjury(id=1, body_part="Lumbar", injury_type="Strain", severity_score=30)

    assert compose_claim_text(claim, employer, []) != compose_claim_text(claim, employer, [injury])


def test_the_summary_carries_no_money_and_no_names() -> None:
    """What the composer deliberately leaves out (AD-11, and the comp-rate call).

    Two claims are not similar because they cost the same, which is the reason
    `update_comp_rate_override` is the one AD-4 command not wired to
    mark-stale. Asserted rather than left to the docstring, so that adding a
    financial column to the composer is a decision somebody takes on purpose.
    """
    claim = Claim(
        injury_type="Strain",
        cause="Lifting",
        body_part="Shoulder",
        icd="S43.4",
        icd_desc="Sprain",
        severity_score=42,
        disability="temporary_partial",
        recovery="2_6_weeks",
        surgery_required=False,
        stage="treatment",
        status="open",
        return_status="not_returned",
        reserve=1_234_500,
        paid_indemnity=99_900,
        comp_rate_override_bp=6667,
        aww=120_000,
    )
    text = compose_claim_text(claim, Employer(sector="Automotive", name="Toyota"), [])

    for absent in ("1234500", "99900", "6667", "120000", "Toyota"):
        assert absent not in text


# --- 4. the dimension guard, with no database ----------------------------


class _StubResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _StubAsyncClient:
    """Enough of `httpx.AsyncClient` for one POST. Records the request body.

    `last_json` is per instance. As a class attribute it outlived the test that
    set it, so a later test asserting on it could pass against a request an
    earlier one had made — the failure mode where deleting a test breaks a
    different one.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.last_json: dict[str, Any] | None = None

    def __call__(self, **_: Any) -> "_StubAsyncClient":
        return self

    async def __aenter__(self) -> "_StubAsyncClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, Any]) -> _StubResponse:
        self.last_json = json
        self.url = url
        return _StubResponse(self._payload)


async def test_a_wrong_width_vector_is_refused_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """`EMBEDDING_MODEL=nomic-embed-text` (768) against `vector(1024)`.

    The mistake somebody will make deliberately, believing it is the supported
    constrained-hardware configuration — the architecture's CPU-dev pairing
    names that model. Padding, truncating or re-scaling would all "work" and
    would produce a table of vectors that retrieve noise with nothing on any
    screen to explain it.

    The exception has to name all three numbers, which is what this asserts: the
    model, what it returned, and what the column wants.
    """
    stub = _StubAsyncClient({"embeddings": [[0.0] * 768]})
    monkeypatch.setattr("services.rag.client.httpx.AsyncClient", stub)

    client = OllamaEmbeddingClient(
        base_url="http://ollama:11434/", model="nomic-embed-text", timeout_seconds=5
    )
    with pytest.raises(EmbeddingDimensionMismatch) as raised:
        await client.embed(["anything"])

    assert raised.value.model == "nomic-embed-text"
    assert raised.value.returned == 768
    assert raised.value.expected == EMBEDDING_DIMENSIONS == 1024
    message = str(raised.value)
    assert "nomic-embed-text" in message and "768" in message and "1024" in message


async def test_the_client_posts_the_batch_as_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One POST for the whole batch, and the model name travels with it.

    Also pins the trailing-slash normalisation: the base URL comes from an
    operator's env file, and a URL that works depending on how it was typed is
    a support question waiting to happen.
    """
    stub = _StubAsyncClient({"embeddings": [[0.5] * EMBEDDING_DIMENSIONS] * 3})
    monkeypatch.setattr("services.rag.client.httpx.AsyncClient", stub)

    client = OllamaEmbeddingClient(
        base_url="http://ollama:11434/", model="bge-m3", timeout_seconds=5
    )
    vectors = await client.embed(["a", "b", "c"])

    assert len(vectors) == 3
    assert stub.last_json == {"model": "bge-m3", "input": ["a", "b", "c"]}
    assert stub.url == "http://ollama:11434/api/embed"


async def test_an_empty_batch_is_not_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refresh tick with nothing pending is the steady state, not an error."""

    def explode(**_: Any) -> None:  # pragma: no cover - must not be called
        raise AssertionError("an empty batch must not reach the network")

    monkeypatch.setattr("services.rag.client.httpx.AsyncClient", explode)
    client = OllamaEmbeddingClient(
        base_url="http://ollama:11434", model="bge-m3", timeout_seconds=5
    )
    assert await client.embed([]) == []


# --- 2 and 3. the refresh command, against a database --------------------


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def everything(db: AsyncSession) -> CallerContext:
    """An unbounded context, built from the seeded system actor (AD-7).

    The refresh job's own context. Its `scope_all` makes `employer_scope` a
    tautology, which is what the two scoped *write* paths compile to under the
    scheduler.
    """
    user = (await db.scalars(sa.select(AppUser).where(AppUser.role == UserRole.system))).one()
    return CallerContext(user_id=user.id, role=user.role, employer_ids=ALL_EMPLOYERS)


async def context_for_handler(db: AsyncSession, name: str) -> CallerContext:
    """A scoped handler's context, built the way the API dependency builds one."""
    user = (
        await db.scalars(
            sa.select(AppUser).where(AppUser.name == name, AppUser.role == UserRole.handler)
        )
    ).one()
    return CallerContext(
        user_id=user.id, role=user.role, employer_ids=await employer_ids_for(db, user.id)
    )


async def reset_embeddings(db: AsyncSession) -> None:
    """Put the tables back to the state migration 0041 leaves them in.

    Called at the top of every DB-backed test here so each one is independent
    of the order the others ran in — `seeded_db_url` is module-scoped, and
    `refresh_stale_embeddings` commits.
    """
    await db.execute(
        sa.text(
            "UPDATE claim_embedding SET embedding = NULL, stale = true, "
            "stale_at = NULL, source_text_hash = NULL, model = NULL, embedded_at = NULL"
        )
    )
    await db.execute(sa.text("UPDATE knowledge_embedding SET embedding = NULL, embedded_at = NULL"))
    await db.commit()


@requires_db
async def test_a_first_run_embeds_the_whole_portfolio_and_the_corpus(
    db: AsyncSession, everything: CallerContext
) -> None:
    """Migration 0041 creates the work; this is the only thing that does it.

    A migration cannot reach Ollama, so it inserts one pending row per claim
    and per chunk. `refresh_stale_embeddings` is the single code path that ever
    writes a vector, and this is that statement asserted end to end.
    """
    await reset_embeddings(db)
    client = FakeEmbeddingClient()

    run = await refresh_stale_embeddings(db, everything, client=client, limit=None)

    assert run.rows_embedded == SEEDED_CLAIMS
    assert run.chunks_embedded == SEEDED_CHUNKS
    assert run.rows_failed == 0

    remaining = await db.scalar(
        sa.text("SELECT count(*) FROM claim_embedding WHERE stale OR embedding IS NULL")
    )
    assert remaining == 0
    provenance = (
        (
            await db.execute(
                sa.text("SELECT DISTINCT model FROM claim_embedding WHERE embedding IS NOT NULL")
            )
        )
        .scalars()
        .all()
    )
    assert provenance == ["fake-embed"], "the client's model name is the row's provenance"


@requires_db
async def test_a_second_run_does_nothing(db: AsyncSession, everything: CallerContext) -> None:
    """Idempotent by construction, not by bookkeeping (`run_payment_batch`'s shape).

    The pending query is "stale or never embedded", so a second call selects
    nothing. There is no run ledger to consult and therefore nothing that can
    get out of step with the rows themselves.
    """
    await reset_embeddings(db)
    await refresh_stale_embeddings(db, everything, client=FakeEmbeddingClient(), limit=None)

    second = FakeEmbeddingClient()
    run = await refresh_stale_embeddings(db, everything, client=second, limit=None)

    assert (run.rows_embedded, run.chunks_embedded, run.rows_failed) == (0, 0, 0)
    assert second.calls == [], "nothing pending must mean nothing reaches the model"


@requires_db
async def test_stale_rows_are_embedded_before_never_embedded_ones(
    db: AsyncSession, everything: CallerContext
) -> None:
    """AC 4's ordering, with the batch bound set to exactly one row.

    Three states are constructed deliberately: one row stale with a vector, one
    row never embedded, and the rest fresh. With `limit=1` the run must pick the
    stale one — a wrong answer is worse than no answer, so a claim whose stored
    summary is known to be out of date outranks one that has none.
    """
    await reset_embeddings(db)
    await refresh_stale_embeddings(db, everything, client=FakeEmbeddingClient(), limit=None)

    claim_ids = (await db.execute(sa.select(Claim.id).order_by(Claim.id).limit(2))).scalars().all()
    stale_pk, null_pk = claim_ids
    await db.execute(
        sa.text("UPDATE claim_embedding SET stale = true WHERE claim_id = :pk"),
        {"pk": stale_pk},
    )
    await db.execute(
        sa.text(
            "UPDATE claim_embedding SET embedding = NULL, embedded_at = NULL WHERE claim_id = :pk"
        ),
        {"pk": null_pk},
    )
    await db.commit()

    client = FakeEmbeddingClient()
    run = await refresh_stale_embeddings(db, everything, client=client, limit=1)

    assert run.rows_embedded == 1
    still_pending = (
        (
            await db.execute(
                sa.text("SELECT claim_id FROM claim_embedding WHERE stale OR embedding IS NULL")
            )
        )
        .scalars()
        .all()
    )
    assert still_pending == [null_pk], "the stale row went first; the NULL row is next"


@requires_db
async def test_the_batch_bound_is_exact(db: AsyncSession, everything: CallerContext) -> None:
    """100 pending claims and 15 pending chunks, `limit=25` ⇒ 25 texts, total.

    Asserted on what the *client* was asked for as well as on rows written: a
    command that composed a hundred summaries and stored twenty-five would
    satisfy a row count while doing four times the work the knob exists to
    bound.

    **The bound is the run's, not each half's**, which is what
    `EMBEDDING_REFRESH_BATCH_SIZE`'s "how many rows one run embeds" says it is.
    Forwarding the limit to both halves unchanged would let a tick configured
    for twenty-five send fifty texts. The claims spend the budget first, so on
    a fresh deployment the corpus waits for a later tick — and from the second
    tick onwards it is already embedded and asks for nothing, leaving the whole
    budget to the claims for ever after.
    """
    await reset_embeddings(db)
    client = FakeEmbeddingClient()

    run = await refresh_stale_embeddings(db, everything, client=client, limit=25)

    assert run.rows_embedded == 25
    assert run.chunks_embedded == 0, "the claim half spent the whole budget"
    assert sum(len(batch) for batch in client.calls) == 25
    assert len(client.calls) == 1, "the corpus half had no budget, so it made no call"


@requires_db
async def test_retrieval_carries_embedded_at(db: AsyncSession, everything: CallerContext) -> None:
    """AD-12: freshness travels with every retrieval result, from day one."""
    await reset_embeddings(db)
    client = FakeEmbeddingClient()
    await refresh_stale_embeddings(db, everything, client=client, limit=None)

    target = (await db.execute(sa.select(Claim.claim_id).order_by(Claim.id))).scalars().first()
    items = await similar_claims(db, everything, claim_business_id=target, k=5, client=client)

    assert items, "an embedded portfolio should have neighbours"
    assert all(item.embedded_at is not None for item in items)
    assert all(item.stale is False for item in items)
    assert all(item.claim_id != target for item in items), "the target is not its own neighbour"
    # Cosine distances, ascending — the repository's ordering, published as-is.
    assert [item.distance for item in items] == sorted(item.distance for item in items)


@requires_db
async def test_an_unreachable_model_server_leaves_the_rows_pending(
    db: AsyncSession, everything: CallerContext
) -> None:
    """Story 6.6's honest degradation, in the shape this story can already keep.

    The run survives, counts the failure, writes nothing, and does not retry
    inside the tick — the next tick is the retry. Nothing falls back to a cloud
    provider, because there is nothing to fall back to (AD-5).

    **Both halves degrade, and this is the state that proves it matters**: a
    freshly migrated deployment has claims *and* chunks pending, so a corpus
    half that raised instead of counting would turn the whole run into an
    exception — and `POST /admin/embedding-refresh`, whose response model
    promises "200 with zeroes and non-zero failure counts", into a 500 on
    exactly the deployment most likely to be looking at it.

    `limit=None` because that is the shape of the run being described — the
    admin route drains everything pending, and it is also the only way both
    halves are attempted in one call: under a budget the claims exhaust, the
    corpus is not reached at all (`test_the_batch_bound_is_exact` covers that
    side).
    """
    await reset_embeddings(db)
    client = UnreachableEmbeddingClient()

    run = await refresh_stale_embeddings(db, everything, client=client, limit=None)

    assert run.rows_embedded == 0
    assert run.chunks_embedded == 0
    assert run.rows_failed == SEEDED_CLAIMS, "every row the claim half was carrying"
    assert run.chunks_failed == 15, "and every chunk the corpus half was carrying"

    pending = await db.scalar(
        sa.text("SELECT count(*) FROM claim_embedding WHERE stale OR embedding IS NULL")
    )
    assert pending == SEEDED_CLAIMS, "nothing was written, so everything is still pending"
    unembedded_chunks = await db.scalar(
        sa.text("SELECT count(*) FROM knowledge_embedding WHERE embedding IS NULL")
    )
    assert unembedded_chunks == 15


class EditDuringEmbedClient:
    """Commits a handler's edit *while* the refresh is waiting on the model.

    The whole point of `stale_at`, expressed as the only kind of test that can
    catch its absence. A refresh selects a pending row, composes it, and then
    spends up to `EMBEDDING_REQUEST_TIMEOUT_SECONDS` inside one HTTP call; this
    client is that call, and it uses a *separate session* to do what a handler
    on another connection would do — mark the claim stale and commit — before
    returning the vectors the refresh then writes.

    A second session rather than the refresh's own, because the race is between
    two transactions and a mark-stale on the same session would be the same
    transaction, which is exactly the situation that is already safe.
    """

    model = "fake-embed"

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], claim_pk: int) -> None:
        self._sessionmaker = sessionmaker
        self._claim_pk = claim_pk
        self._inner = FakeEmbeddingClient()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        async with self._sessionmaker() as other:
            user = (
                await other.scalars(sa.select(AppUser).where(AppUser.role == UserRole.system))
            ).one()
            ctx = CallerContext(user_id=user.id, role=user.role, employer_ids=ALL_EMPLOYERS)
            await mark_claim_stale(other, ctx, claim_pk=self._claim_pk)
            await other.commit()
        return await self._inner.embed(texts)


@requires_db
async def test_an_edit_during_the_embed_window_is_not_lost(
    db: AsyncSession, everything: CallerContext, seeded_db_url: str
) -> None:
    """AD-12's staleness contract, at the one moment it can silently break.

    Retrieval may lag an edit — the contract says so, and `embedded_at` is how
    a reader sees it. What it may never do is lag an edit *invisibly*. Before
    `stale_at`, `write_claim_embedding` cleared `stale` unconditionally, so an
    edit committing inside the embed window was overwritten by the vector of
    the text as it read *before* that edit, with a fresh timestamp beside it.
    The row was then wrong, advertised as current, and stayed that way until
    some unrelated edit happened to touch the claim again.

    So the assertion is not "the vector is right" — the in-flight one never
    could be — but "the row still says it needs doing".
    """
    await reset_embeddings(db)
    claim_pk = await first_claim_pk(db)

    # Everything embedded and clean, so the only pending row is the one the
    # edit below creates; otherwise `stale` proves nothing.
    await refresh_stale_embeddings(db, everything, client=FakeEmbeddingClient(), limit=None)
    assert await db.scalar(sa.text("SELECT count(*) FROM claim_embedding WHERE stale")) == 0

    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        other_sessions = async_sessionmaker(engine, expire_on_commit=False)
        await db.execute(
            sa.text("UPDATE claim_embedding SET stale = true WHERE claim_id = :pk"),
            {"pk": claim_pk},
        )
        await db.commit()

        run = await refresh_stale_embeddings(
            db,
            everything,
            client=EditDuringEmbedClient(other_sessions, claim_pk),
            limit=None,
        )
    finally:
        await engine.dispose()

    assert run.rows_embedded == 1, "the in-flight vector is still written"
    row = (
        await db.execute(
            sa.text(
                "SELECT stale, embedded_at IS NOT NULL FROM claim_embedding WHERE claim_id = :pk"
            ),
            {"pk": claim_pk},
        )
    ).one()
    assert row.stale is True, (
        "an edit landed inside the embed window, so the row this run wrote is "
        "already out of date and the next tick has to redo it"
    )


@requires_db
async def test_a_row_untouched_during_the_embed_window_is_cleared(
    db: AsyncSession, everything: CallerContext
) -> None:
    """The other half of `stale_at`: the ordinary path still clears the flag.

    Without this, a guard that simply never cleared `stale` would pass the test
    above and re-embed the whole portfolio on every tick for ever.
    """
    await reset_embeddings(db)

    run = await refresh_stale_embeddings(db, everything, client=FakeEmbeddingClient(), limit=None)

    assert run.rows_embedded == SEEDED_CLAIMS
    assert await db.scalar(sa.text("SELECT count(*) FROM claim_embedding WHERE stale")) == 0


@requires_db
async def test_search_knowledge_returns_labelled_chunks_nearest_first(
    db: AsyncSession, everything: CallerContext
) -> None:
    """The corpus half of RAG, exercised through the service rather than the repo.

    Story 6.4's labour-law action is `search_knowledge`'s first real caller, so
    until then this is the only thing standing between "the corpus is seeded"
    and "the corpus can be retrieved". It asserts the two properties that
    function actually promises: the query text is embedded *here* (the
    repository is handed a vector), and every hit carries the provenance that
    keeps a paraphrase from reading as settled law.
    """
    await reset_embeddings(db)
    client = FakeEmbeddingClient()
    await refresh_stale_embeddings(db, everything, client=client, limit=None)
    before = len(client.calls)

    hits = await search_knowledge(db, everything, query_text="waiting period", k=3, client=client)

    assert 0 < len(hits) <= 3
    assert len(client.calls) == before + 1, "the query text was embedded, once"
    assert client.calls[-1] == ["waiting period"]
    assert all(hit.source.startswith("synthetic-demo:") for hit in hits)
    assert all(hit.chunk_text for hit in hits)
    assert [hit.distance for hit in hits] == sorted(hit.distance for hit in hits)


@requires_db
async def test_search_knowledge_is_not_narrowed_by_the_caller_s_book(
    db: AsyncSession, everything: CallerContext
) -> None:
    """Reference data, not claim data — the carve-out, asserted.

    The labour-law corpus is the one thing `services/rag` retrieves that is not
    scoped: a statute does not belong to an employer. It still takes a caller
    context (AD-7's "no code path may query claim data without one" is a
    discipline about shape, not an exemption to argue per module), and this is
    the test that says the context does not silently filter it — otherwise a
    scoped handler would get a quieter corpus than a supervisor and nobody
    would notice until an answer went missing.
    """
    await reset_embeddings(db)
    client = FakeEmbeddingClient()
    await refresh_stale_embeddings(db, everything, client=client, limit=None)

    scoped = await context_for_handler(db, "Sarah Williams")
    mine = await search_knowledge(db, scoped, query_text="waiting period", k=5, client=client)
    everyones = await search_knowledge(
        db, everything, query_text="waiting period", k=5, client=client
    )

    assert [hit.chunk_text for hit in mine] == [hit.chunk_text for hit in everyones]


@requires_db
async def test_similar_claims_accepts_free_text_as_well_as_a_claim(
    db: AsyncSession, everything: CallerContext
) -> None:
    """The `query_text` branch — the one a tool will use for "cases like this".

    Both branches embed in `services/rag` and hand the repository a vector; the
    difference is only where the text comes from. Asserted together with the
    argument guard, because "exactly one of the two" is the contract that keeps
    a caller from silently getting the claim branch when it meant the text one.
    """
    await reset_embeddings(db)
    client = FakeEmbeddingClient()
    await refresh_stale_embeddings(db, everything, client=client, limit=None)

    hits = await similar_claims(
        db, everything, query_text="lower back strain lifting", k=3, client=client
    )
    assert 0 < len(hits) <= 3
    assert all(hit.embedded_at is not None for hit in hits)

    with pytest.raises(ValueError):
        await similar_claims(db, everything, k=3, client=client)
    with pytest.raises(ValueError):
        await similar_claims(
            db, everything, claim_business_id="WC-20017", query_text="both", k=3, client=client
        )


@requires_db
async def test_embed_claims_recomputes_exactly_the_named_claims(
    db: AsyncSession, everything: CallerContext
) -> None:
    """The targeted command, which the refresh does not use and a caller might.

    It exists for the on-demand case — a handler asking for similar cases on a
    claim they have just edited — and its docstring says it is what a test
    asserts against, so here is that test. The property worth pinning is that
    it embeds the claims it was *given* rather than whatever happens to be
    pending: it is the one entry point that does not consult the queue.
    """
    await reset_embeddings(db)
    setup = FakeEmbeddingClient()
    await refresh_stale_embeddings(db, everything, client=setup, limit=None)

    pks = (await db.execute(sa.select(Claim.id).order_by(Claim.id).limit(2))).scalars().all()
    client = FakeEmbeddingClient()

    written = await embed_claims(db, everything, claim_pks=list(pks), client=client)
    await db.commit()

    assert written == 2
    assert len(client.calls) == 1
    assert len(client.calls[0]) == 2, "two claims composed, two texts embedded"
    models = (
        (
            await db.execute(
                sa.text("SELECT DISTINCT model FROM claim_embedding WHERE claim_id = ANY(:pks)"),
                {"pks": list(pks)},
            )
        )
        .scalars()
        .all()
    )
    assert models == ["fake-embed"]


@requires_db
async def test_a_failing_claim_batch_is_counted_and_the_run_survives(
    db: AsyncSession, everything: CallerContext
) -> None:
    """The claim half is caught and counted; the rows stay stale.

    Distinguished from the test above by embedding the corpus first, so the only
    work left is claims — which is the state a live deployment is in for all but
    its first tick.
    """
    await reset_embeddings(db)
    await refresh_stale_embeddings(db, everything, client=FakeEmbeddingClient(), limit=None)
    await db.execute(sa.text("UPDATE claim_embedding SET stale = true"))
    await db.commit()

    run = await refresh_stale_embeddings(
        db, everything, client=UnreachableEmbeddingClient(), limit=10
    )

    assert run.rows_embedded == 0
    assert run.rows_failed == 10
    assert run.chunks_embedded == 0
    still_stale = await db.scalar(sa.text("SELECT count(*) FROM claim_embedding WHERE stale"))
    assert still_stale == SEEDED_CLAIMS


@requires_db
async def test_a_dimension_mismatch_takes_the_run_down_and_writes_nothing(
    db: AsyncSession, everything: CallerContext
) -> None:
    """Not caught, on purpose. See `services/rag/embeddings.py`'s docstring.

    A wrong-width vector fails identically on every future tick, so a run that
    logged it and carried on would fill the log with one line while the
    portfolio stayed unembedded and nothing said why.
    """
    await reset_embeddings(db)

    class WrongWidth:
        model = "nomic-embed-text"

        async def embed(self, texts: Sequence[str]) -> list[list[float]]:
            raise EmbeddingDimensionMismatch(
                model=self.model, returned=768, expected=EMBEDDING_DIMENSIONS
            )

    with pytest.raises(EmbeddingDimensionMismatch):
        await refresh_stale_embeddings(db, everything, client=WrongWidth(), limit=5)

    written = await db.scalar(
        sa.text("SELECT count(*) FROM claim_embedding WHERE embedding IS NOT NULL")
    )
    assert written == 0


@requires_db
async def test_mark_claim_stale_is_an_idempotent_upsert(
    db: AsyncSession, everything: CallerContext
) -> None:
    """Called twice ⇒ one row, still stale. Called for a claim with no row ⇒ one row.

    The second half is the case migration 0041 makes rare rather than
    impossible: a claim inserted by a later story has no pending row, and an
    edit to it must still mark something.
    """
    await reset_embeddings(db)
    await refresh_stale_embeddings(db, everything, client=FakeEmbeddingClient(), limit=None)
    claim_pk = await first_claim_pk(db)

    await mark_claim_stale(db, everything, claim_pk=claim_pk)
    await mark_claim_stale(db, everything, claim_pk=claim_pk)
    await db.commit()

    rows = (
        (
            await db.execute(
                sa.text("SELECT stale FROM claim_embedding WHERE claim_id = :pk"), {"pk": claim_pk}
            )
        )
        .scalars()
        .all()
    )
    assert rows == [True]

    await db.execute(sa.text("DELETE FROM claim_embedding WHERE claim_id = :pk"), {"pk": claim_pk})
    await db.commit()
    await mark_claim_stale(db, everything, claim_pk=claim_pk)
    await db.commit()

    recreated = (
        await db.execute(
            sa.text("SELECT stale, embedding IS NULL FROM claim_embedding WHERE claim_id = :pk"),
            {"pk": claim_pk},
        )
    ).one()
    assert tuple(recreated) == (True, True)


@requires_db
async def test_mark_claim_stale_does_not_commit(
    db: AsyncSession, everything: CallerContext
) -> None:
    """The contract the four wired commands depend on (AD-12).

    `audit.record` and `timeline.append` keep the same one. A version that
    committed would flag a claim stale for an edit that then lost its
    compare-and-swap and never happened.
    """
    await reset_embeddings(db)
    await refresh_stale_embeddings(db, everything, client=FakeEmbeddingClient(), limit=None)
    claim_pk = await first_claim_pk(db)

    await mark_claim_stale(db, everything, claim_pk=claim_pk)
    await db.rollback()

    stale = await db.scalar(
        sa.text("SELECT stale FROM claim_embedding WHERE claim_id = :pk"), {"pk": claim_pk}
    )
    assert stale is False


@requires_db
async def test_the_seeded_corpus_is_labelled_synthetic(db: AsyncSession) -> None:
    """Every row says it is demonstration text, in its source and in its body.

    Knowledge-corpus sourcing is a Deferred architecture decision and this story
    is forbidden from building an ingestion pipeline; what it must not do is
    present paraphrase as authoritative law. The label travels *inside* the
    chunk text so a model quoting a passage quotes the disclaimer with it.
    """
    rows = (
        await db.execute(sa.text("SELECT source, state_code, chunk_text FROM knowledge_chunk"))
    ).all()

    assert len(rows) == SEEDED_CHUNKS
    assert {row.state_code for row in rows} == {"OH", "MN", "IL", "WA", "TX"}
    for row in rows:
        assert row.source.startswith("synthetic-demo:"), row.source
        assert "SYNTHETIC DEMONSTRATION TEXT" in row.chunk_text
        assert "not statutory law" in row.chunk_text


@requires_db
async def test_an_out_of_scope_claim_cannot_be_marked_stale(db: AsyncSession) -> None:
    """AD-7 applies to the writes, not only to the reads.

    A handler's edit command cannot, even by passing another book's primary
    key, touch a row outside their scope — and it fails silently, which is the
    same answer a scoped read gives and for the same reason (an exception would
    be an existence oracle).
    """
    await reset_embeddings(db)
    empty = CallerContext(user_id=1, role=UserRole.handler, employer_ids=frozenset())
    claim_pk = await first_claim_pk(db)

    await db.execute(sa.text("UPDATE claim_embedding SET stale = false"))
    await db.commit()

    affected = await embedding_repo.upsert_claim_embedding_stale(db, empty, claim_pk=claim_pk)
    await db.commit()

    assert affected == 0
    stale = await db.scalar(
        sa.text("SELECT stale FROM claim_embedding WHERE claim_id = :pk"), {"pk": claim_pk}
    )
    assert stale is False


@requires_db
async def test_write_claim_embedding_clears_stale_in_the_same_statement(
    db: AsyncSession, everything: CallerContext
) -> None:
    """One fact, one write.

    Clearing the flag separately would open a window in which a vector is
    stored and still advertised as stale, and the refresh would re-embed it on
    every tick for ever.
    """
    await reset_embeddings(db)
    claim_pk = await first_claim_pk(db)
    at = datetime.now(UTC)

    await embedding_repo.write_claim_embedding(
        db,
        everything,
        claim_pk=claim_pk,
        embedding=[0.1] * EMBEDDING_DIMENSIONS,
        source_text_hash="deadbeef",
        model="bge-m3",
        embedded_at=at,
        stale_at=None,
    )
    await db.commit()

    row = (
        await db.execute(
            sa.text(
                "SELECT stale, source_text_hash, model, embedded_at IS NOT NULL "
                "FROM claim_embedding WHERE claim_id = :pk"
            ),
            {"pk": claim_pk},
        )
    ).one()
    assert tuple(row) == (False, "deadbeef", "bge-m3", True)


# --- the scheduler registration ------------------------------------------


def test_the_refresh_is_registered_as_a_second_job() -> None:
    """`build_job_runner` holds this refresh, and the runner is still generic.

    Story 3.4 wrote the scheduler with no mention of payments precisely so that
    this story could add a job without touching `services/jobs.py` beyond a
    second predicate. Asserted because "the slot was reserved" is only a claim
    about the code until something registers in it — and because the scheduler
    is off under `ENV=e2e` by design, so no e2e spec can see this.

    **Membership and ordering rather than an exact list**, amended by Story
    6.2 when it registered a third job. An equality against two names was the
    right assertion while this story was the last one; keeping it would have
    made every later job a failure here, in a test about *this* job's
    registration. What still matters is that the embedding refresh is
    registered, that the payment batch is registered before it (registration
    order is the tick's evaluation order), and that neither has been renamed.
    """
    from api.app import EMBEDDING_REFRESH_JOB, PAYMENT_BATCH_JOB, build_job_runner
    from config import Settings

    runner = build_job_runner(Settings(), sessionmaker=None)  # type: ignore[arg-type]

    names = [job.name for job in runner.jobs]
    assert EMBEDDING_REFRESH_JOB in names
    assert names.index(PAYMENT_BATCH_JOB) < names.index(EMBEDDING_REFRESH_JOB)
    assert len(names) == len(set(names)), f"a job name is registered twice: {names}"


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [(0.0, False), (899.0, False), (900.0, True), (5_000.0, True)],
)
def test_every_seconds_is_due_on_elapsed_time(elapsed: float, expected: bool) -> None:
    """The predicate's whole behaviour, including the boundary.

    `>=` rather than `>`: the runner's tick is the granularity, and with an
    interval set to an exact multiple of it a strict comparison would defer
    every run by one whole tick, for ever. The first tick after a restart is
    due unconditionally, which is asserted separately below.
    """
    from datetime import timedelta

    from services.jobs import every_seconds

    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    due = every_seconds(900.0)

    assert due(now, now - timedelta(seconds=elapsed)) is expected


def test_every_seconds_fires_on_the_first_tick_after_a_restart() -> None:
    """`last_run is None` ⇒ due, which is safe because the refresh is idempotent.

    The command's query is "what is pending", so a restart loop costs one query
    per boot rather than one duplicated write — the same argument
    `JobRunner.tick` makes for the payment batch.
    """
    from services.jobs import every_seconds

    assert every_seconds(900.0)(datetime(2026, 8, 19, tzinfo=UTC), None) is True
