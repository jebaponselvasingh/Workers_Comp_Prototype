"""Story 6.2 — the insight cache, and the one property everything else rests on.

The AD-2 equality test below is this module's reason to exist and the story's
own Dev Notes call it mandatory: **every figure in a persisted insight is
byte-equal to the deterministic service output it came from.** It is written by
re-calling the four services and comparing values, rather than by asserting that
some number is present, because the failure it exists to catch is a *plausible*
number — a settlement estimate, a reserve recommendation, a rounded ratio — and
a plausible number is exactly what a card full of assertions about shape would
sail past.

Everything else here is the machinery that makes that property survive contact
with a model server:

- The two *schemas* — one per kind for what may be written, one for what is
  stored — and a structural guard that no narrative schema carries a field a
  figure could go in.
- The failure paths, which are two and are different: an answer that fails its
  schema leaves the other three kinds written, and a model server that is down
  leaves the previous generation standing with its previous timestamp.
- The cache semantics: a re-refresh replaces four rows and never produces eight.
- AD-4 and AD-11: one content-free audit event per card, and not a single log
  line carrying a prompt or a completion.

Every test runs against a fake `ChatClient` and a fake `EmbeddingClient`
(`tests/insight_fixture.py`, `tests/embedding_fixture.py`), which is what the
two Protocols are for — a suite that needed a GPU to say whether a re-refresh
duplicates rows is a suite that gets skipped.
"""

import asyncio
import io
import json
import tokenize
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agents import insights as agent_insights
from agents.client import ChatSchemaRejected, ChatUnavailable
from agents.envelope import ToolResult
from agents.insights import InsightGenerationDeps, refresh_claim_insights, refresh_pending_insights
from agents.prompts import PROMPTS_DIR, load
from agents.schemas import (
    NARRATIVE_SCHEMAS,
    FraudLowRiskNarrative,
    FraudRedFlagsNarrative,
    MoneyFigure,
    ReserveAdequacyInsight,
    ReserveAdequacyNarrative,
)
from agents.tools import fraud_signals, next_actions, reserve_check, similar_cases
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AiInsight, AiInsightAttempt, AppUser, AuditEvent, Claim
from data.models.enums import InsightKind, UserRole
from services import rag
from services.claims.detail import ClaimNotVisible
from services.financials import ReserveVerdict, format_dollars
from services.rag.client import EmbeddingDimensionMismatch
from tests.conftest import requires_db
from tests.embedding_fixture import FakeEmbeddingClient
from tests.insight_fixture import (
    FakeChatClient,
    MalformedChatClient,
    UnavailableChatClient,
    fill,
)

SERVER_ROOT = Path(__file__).resolve().parents[1]

#: How stale a neighbour's vector may be before the similar-case card discloses
#: it. The `Settings` default; stated here rather than imported so a test that
#: happens to exercise the disclosure is not silently retuned by a config change.
STALENESS_DAYS = 7


# --- structural: the schemas cannot hold a figure -------------------------
#
# No database. These run on a laptop with nothing installed, which is the
# half that catches the tempting one-line change — "let the model fill in the
# reserve figure, it has it in the prompt anyway".


@pytest.mark.parametrize("schema", NARRATIVE_SCHEMAS, ids=lambda s: s.__name__)
def test_a_narrative_schema_carries_only_prose(schema: type[Any]) -> None:
    """AD-2, asserted at the one place a model could originate a figure.

    Every field of every schema the model is asked to fill is a string or a list
    of strings. That is the mechanical form of "the LLM supplies only the
    sentences around figures it cannot originate": a model that wanted to invent
    a dollar amount has nowhere in the grammar to put one, because
    `with_structured_output(method="json_schema")` constrains decoding to
    exactly this shape.

    A guard rather than a convention, because the change it refuses is one field
    declaration, reads as a simplification, and would defeat the whole epic's
    headline guarantee without breaking a single other test.
    """
    for name, field in schema.model_fields.items():
        rendered = str(field.annotation)
        assert rendered in ("<class 'str'>", "list[str]"), (
            f"{schema.__name__}.{name} is annotated {rendered} — a narrative schema may only "
            "carry prose, or a model can originate a figure (AD-2)"
        )


def test_the_reserve_schema_forbids_a_ratio_when_the_bills_are_not_on_file() -> None:
    """The I/O matrix's "reserve indeterminate" row, at the schema.

    `remaining_medical_cents is None` does not mean zero: it means the bills are
    not on file, which forces `ReserveVerdict.indeterminate` and leaves
    `ratio_bp` as `None`. A card quoting "115%" in that state would be quoting a
    comparison nobody made, from half the inputs — the failure `ReserveCheck`'s
    own docstring records this console having shipped once.

    Asserted by asking for the refusal, three times over: the ratio, the medical
    exposure and the projected total are each independently impossible when the
    bills are absent, and a guard that only refused the first would let a
    "projected exposure" through that is a lower bound wearing a total's name.
    """
    base: dict[str, Any] = {
        "verdict": ReserveVerdict.indeterminate,
        "verdict_rationale": "The bills are not on file, so exposure cannot be judged.",
        "ratio_bp": None,
        "reserve": MoneyFigure(cents=4_800_000, display="$48,000"),
        "remaining_indemnity": MoneyFigure(cents=1_900_000, display="$19,000"),
        "remaining_medical": None,
        "projected_remaining": None,
        "bills_on_file": False,
        "bands_version": 1,
        "narrative": _narrative(ReserveAdequacyNarrative),
        "prompt_version": 1,
    }
    # The honest combination validates.
    ReserveAdequacyInsight.model_validate(base)

    for field, offending in (
        ("ratio_bp", 11_500),
        ("remaining_medical", MoneyFigure(cents=1, display="$0")),
        ("projected_remaining", MoneyFigure(cents=1, display="$0")),
    ):
        with pytest.raises(ValueError, match="indeterminate"):
            ReserveAdequacyInsight.model_validate({**base, field: offending})


def test_the_prompt_files_are_versioned_and_declare_their_own_key() -> None:
    """Prompts are versioned files loaded by key — the spine's convention.

    The version is written into every `ai_insight.content` the prompt produced,
    so "which instructions produced this card?" is answerable from the row. A
    file whose header is missing, or which declares a key it is not filed under,
    is refused — the second case being the copy-paste that would otherwise serve
    one kind's instructions under another's name.
    """
    for kind in InsightKind:
        prompt = load(kind.value)
        assert prompt.key == kind.value
        assert prompt.version >= 1
        assert prompt.text

    system = load("system")
    assert system.version >= 1

    # And every `.md` beside the loader is a prompt somebody can reach, so a
    # file added without being wired to a kind fails here rather than sitting
    # unloaded.
    #
    # **Two copilot files joined the set in Story 6.3** (AD-15: amended, never
    # deleted). `copilot_system` is the chat surface's own preamble — a sibling
    # of `system` rather than a kind composed on top of it, because `system.md`
    # ends by instructing the model to answer only with JSON, which is right for
    # four constrained completions and exactly wrong for a streamed
    # conversation. `copilot_greeting` is not an instruction at all: it is the
    # deterministic case-summary line (AD-2), filed here because user-visible
    # text belongs in a versioned file a reviewer reads as prose.
    #
    # Both are loaded by key like everything else, so both are covered by the
    # header validation above rather than exempted from it.
    copilot = {"copilot_system", "copilot_greeting"}
    # **Six quick-action files joined the set in Story 6.4** (AD-15 again). The
    # spine's Prompts convention says QAS keys map 1:1 to prompt files where a
    # prompt is needed, so the stems here are the keys in
    # `agents/graph.py::QUICK_ACTIONS` — minus `data_alignment`, which makes no
    # model call and therefore has no instructions to version. That one absence
    # is the whole of the `requires_llm: False` path, and it is asserted from
    # the map's side in `tests/test_copilot_qas.py` rather than restated here.
    quick_actions = {"laborlaw", "similar", "rtw", "reserve", "fraud", "nextactions"}
    expected = {kind.value for kind in InsightKind} | {"system"} | copilot | quick_actions
    assert {path.stem for path in PROMPTS_DIR.glob("*.md")} == expected
    for key in sorted(copilot | quick_actions):
        assert load(key).key == key
        assert load(key).version >= 1
        assert load(key).text


#: Every module that plausibly *would* write `ai_insight` if AD-12 were not
#: enforced by where the code lives: the neighbouring `services/rag` command,
#: the three services whose output the cards narrate, and the two routers that
#: serve them. `services/rag/insights.py` is here too — as the owner, with one
#: assertion instead of two; see the test.
INSIGHT_ADJACENT_MODULES = [
    "services/rag/insights.py",
    "services/rag/embeddings.py",
    "services/claims/edit.py",
    "services/worklist/actions.py",
    "services/financials/reserve.py",
    "api/routers/claims.py",
    "api/routers/admin.py",
    "agents/insights.py",
]


@pytest.mark.parametrize("module", INSIGHT_ADJACENT_MODULES)
def test_no_module_outside_the_repository_reaches_the_insight_orm_class(module: str) -> None:
    """AD-12 and AD-7 together: every `ai_insight` write goes through one query.

    `services/rag/insights.py` is in the list on purpose although it *is* the
    owner. What the scan asserts about it is that even the owner does not reach
    the ORM class directly: it goes through `data/repositories/insights.py`,
    which is where the `employer_scope(ctx)` predicate lives. A command that
    constructed an `AiInsight` and added it to a session would be a write with
    no employer filter on it — and it would look exactly like the ordinary
    SQLAlchemy this codebase is full of.

    `agents/insights.py` is here for the layering half: generation composes the
    content and hands it to the command, and an `agents/` module that touched
    the table would be inference writing to the database directly.

    Comments and docstrings are stripped first,
    `test_no_command_writes_the_embedding_tables_itself`'s rule and for its
    reason: this codebase's prose names the table constantly and a guard that
    made the correct comment unwritable would be training the code. String
    literals are deliberately kept, so a raw-SQL second writer is still caught.
    """
    source = _code_only((SERVER_ROOT / module).read_text(encoding="utf-8"))
    assert "AiInsight" not in source, f"{module} reaches the ORM class directly"


def test_the_table_name_appears_in_no_raw_sql_outside_the_repository() -> None:
    """The other half of the guard above, over the literal rather than the class.

    Separate from it because two modules legitimately spell `ai_insight` in a
    *string*: the owner writes it as the AD-4 audit entity and action name, and
    the router as the entity of a problem document. Neither is SQL. So this
    scans the modules where the string could only be a query, which is what a
    second writer smuggled past the ORM check would look like.
    """
    for module in INSIGHT_ADJACENT_MODULES:
        if module in ("services/rag/insights.py", "api/routers/claims.py"):
            continue
        source = _code_only((SERVER_ROOT / module).read_text(encoding="utf-8"))
        assert "ai_insight" not in source, f"{module} names the table directly"


def _code_only(source: str) -> str:
    """`source` with its comments and docstrings removed.

    Tokenize rather than regex, `test_embedding_staleness.py`'s helper and its
    argument: a `#` inside a string literal is not a comment, and this guard
    asserts on the presence of *identifiers*.
    """
    out: list[str] = []
    prev = tokenize.INDENT
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and prev in (
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
        ):
            continue
        if token.type not in (tokenize.NL, tokenize.COMMENT):
            prev = token.type
        out.append(token.string)
    return "\n".join(out)


def test_exactly_the_declared_modules_read_the_ollama_base_url() -> None:
    """AD-5 stays a ten-second grep, and this is the ten seconds.

    `services/rag/client.py` argues the property at length: "no PHI leaves the
    network" is a claim about every outbound call in the process, and it is
    checkable only while the base URL has a countable set of readers. Story 6.1
    had one; Story 6.2 added the chat client; Story 6.3 adds
    `agents/chat_model.py`, the streaming model the copilot graph runs on.

    **Amended rather than deleted, and renamed with it** (AD-15). The test was
    `test_exactly_two_modules_read_the_ollama_base_url` and the number was in
    its name, so a fourth reader made the name a lie before it made the
    assertion one. The name no longer counts, because the property was never
    about the count: it is that the set is *enumerable and declared*, and that
    every member of it lives in `agents/` or `services/rag/`.

    `agents/chat_model.py` is a correct addition rather than a violation, and
    `agents/client.py` predicted it in as many words: its `ChatClient` Protocol
    is structured-only by design and reserves the streaming surface for Story
    6.3, which "will own its own surface rather than widening this one".
    Widening the Protocol would have put a streaming path inside the module
    whose whole claim is that it makes one kind of request.

    The equality stays an equality. A fifth reader should be a diff somebody
    argues for here, which is the entire mechanism.

    ## The fifth reader: `agents/degradation.py` (Story 6.6)

    Here is that diff, and here is the argument for it. AD-14 requires the SPA
    to know whether the model server is reachable *before* a handler discovers
    it by sending a message that fails, which means something in this process
    has to ask the model server whether it is answering — a `GET /api/version`,
    the same path `deploy/compose.e2e.yaml`'s stub healthcheck already targets.
    Asking requires the URL.

    It could not have been folded into an existing reader without making one of
    them worse. `services/rag/client.py` is the embeddings client and its whole
    claim is that it makes one kind of request; `agents/client.py` is
    structured-only by design and has said so since 6.2; `agents/chat_model.py`
    builds a `BaseChatModel` and a liveness probe is not one. So the probe lives
    beside the wrapper that translates the outage it reports on, in the module
    that owns the degradation seam.

    **The property this guard actually protects is untouched by it.** Every
    member of the set still lives in `agents/` or `services/rag/`, still talks
    to the internal Ollama and nothing else, and there is still no cloud host
    anywhere in the tree. A reader that appeared in `api/`, `services/` outside
    `rag`, or `data/` would be the failure this test exists to catch, and this
    is not one.
    """
    readers = {
        str(path.relative_to(SERVER_ROOT))
        for path in SERVER_ROOT.rglob("*.py")
        if ".venv" not in path.parts
        # `tests/` is excluded for the reason it is excluded from every other
        # structural guard in this suite: the tests are the independent oracle
        # and are allowed to name what they are asserting about — this very
        # function contains the string it is scanning for.
        and "tests" not in path.parts
        and "ollama_base_url" in path.read_text(encoding="utf-8")
    }
    assert readers == {
        "config.py",
        "services/rag/client.py",
        "agents/client.py",
        "agents/chat_model.py",
        "agents/degradation.py",
    }


def _clear_env_cache(ls_utils: Any) -> None:
    """Drop langsmith's memoised environment reads.

    `get_env_var` is `@overload`ed *and* `lru_cache`d, so mypy resolves the name
    to the overload signatures rather than to the wrapper and cannot see
    `cache_clear`. Reached through an untyped alias here rather than with an
    inline `type: ignore`, because the ignore would sit on three lines and would
    silence the next real error on any of them.

    The cache is what makes `config.py` write these variables at *import*: the
    first read wins for the life of the process. Only a test needs to look
    twice.
    """
    ls_utils.get_env_var.cache_clear()


def test_langsmith_tracing_is_forced_off_and_cannot_be_switched_back_on() -> None:
    """AD-5/AD-16: the vendor's cloud exporter, closed — and closed *provably*.

    The grep above cannot see this one, and that is the point of writing it
    separately. `langchain-ollama` depends on `langchain-core`, which
    hard-depends on `langsmith` (a required distribution in `uv.lock`, not an
    extra), and that package's tracer needs no code at all to activate: it reads
    `LANGSMITH_TRACING` / `LANGCHAIN_TRACING_V2` / their siblings off the
    environment at the first completion and POSTs the whole prompt body and the
    whole completion to `api.smith.langchain.com`. For this build that is a
    claim's clinical narrative leaving the network through a code path nobody
    wrote — and `test_exactly_the_declared_modules_read_the_ollama_base_url`
    would still pass, because no file in the tree mentions a URL.

    Asserted through the **vendor's own predicate** rather than by reading the
    environment back, so a rename of the variables upstream fails here instead
    of leaving a guard that checks a name nothing reads any more. And asserted
    *after* poisoning the environment the way a shared base image or an
    inherited `.env` would, because the useful claim is "an operator cannot turn
    this on", not "nobody happens to have turned it on".

    `get_env_var` is `lru_cache`d, which is exactly why `config.py` writes these
    at import: the first read wins for the life of the process. The cache is
    cleared here so this test can observe its own poisoning.
    """
    import os

    from langsmith import utils as ls_utils

    from config import _TRACING_ENV_OFF, force_local_only_tracing

    assert ls_utils.tracing_is_enabled() is False

    original = {name: os.environ.get(name) for name in _TRACING_ENV_OFF}
    try:
        # An unprotected process, then an operator switching tracing on: every
        # variable `config.py` writes is removed first, because leaving one in
        # place would make the "before" state indistinguishable from the state
        # under test and the assertion below vacuous.
        for name in _TRACING_ENV_OFF:
            os.environ.pop(name, None)
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        _clear_env_cache(ls_utils)
        assert ls_utils.tracing_is_enabled() is True, (
            "the fixture no longer models an operator turning tracing on — this guard "
            "would pass against a build with no protection at all"
        )

        force_local_only_tracing()
        _clear_env_cache(ls_utils)
        assert ls_utils.tracing_is_enabled() is False
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        force_local_only_tracing()
        _clear_env_cache(ls_utils)


@pytest.mark.parametrize("profile", ["compose.yaml", "compose.e2e.yaml"])
def test_every_compose_profile_closes_all_six_tracing_doors(profile: str) -> None:
    """C5: the container-level half of the egress guard, matched to the code's.

    `config.py` overwrites six variables at import, and the compose files set
    three — while their own comment claimed the door was "closed even before
    Python starts". It was not: `langsmith.utils.get_env_var` reads the
    `LANGSMITH_*` spelling and falls back to `LANGCHAIN_*`, and `*_OTEL_ENABLED`
    is a second exporter, so half the names an operator could set were never
    answered at this layer at all (follow-up review of Story 6.2, C5).

    The import-time overwrite covers them regardless, which is why this is a
    `low` — but two statements of one containment rule that disagree is how the
    weaker one ends up being the one somebody relies on. Driven off
    `_TRACING_ENV_OFF` rather than a copied list, so a seventh door added to the
    code is a failure here rather than a silent gap.

    Matched as text rather than parsed, because the file is YAML with anchors
    and a parser would be a dependency this suite does not declare.
    """
    from config import _TRACING_ENV_OFF

    text = (SERVER_ROOT.parent / "deploy" / profile).read_text()

    for name, value in _TRACING_ENV_OFF.items():
        assert f'{name}: "{value}"' in text, (
            f"{profile} does not close {name}, which config.py forces off — the two "
            "statements of one containment rule disagree"
        )


def test_the_insight_refresh_is_registered_as_a_third_job() -> None:
    """`build_job_runner` holds this refresh, after the two Story 6.1 left.

    The scheduler is off under `ENV=e2e` by design, so no e2e spec can see this
    — which is why the registration is asserted here rather than left to the
    stack. Membership and ordering rather than an exact list, the amendment
    Story 6.2 made to `test_the_refresh_is_registered_as_a_second_job`: an
    equality would make every later job a failure in a test about this one.
    """
    from api.app import INSIGHT_REFRESH_JOB, build_job_runner
    from config import Settings

    runner = build_job_runner(Settings(), sessionmaker=None)  # type: ignore[arg-type]

    names = [job.name for job in runner.jobs]
    assert INSIGHT_REFRESH_JOB in names
    assert len(names) == len(set(names)), f"a job name is registered twice: {names}"


# --- the behavioural half -------------------------------------------------


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def system(db: AsyncSession) -> CallerContext:
    """The unbounded context the scheduled refresh runs under.

    The seeded system actor, resolved the way `services/financials/batch.py`
    resolves it — so every assertion below is made through the same AD-7
    predicate the job uses, rendered as the tautology `scope_all` produces
    rather than skipped.
    """
    user = (await db.scalars(sa.select(AppUser).where(AppUser.role == UserRole.system))).one()
    return CallerContext(user_id=user.id, role=user.role, employer_ids=ALL_EMPLOYERS)


@pytest.fixture
async def claim_id(db: AsyncSession, system: CallerContext) -> str:
    """A claim to narrate, chosen by id so two runs pick the same one."""
    pending = await rag.claims_needing_insights(db, system, limit=1)
    assert pending, "the seeded portfolio has no claims"
    return pending[0]


def deps(chat: Any) -> InsightGenerationDeps:
    """Generation wired to a fake model server and a fake embedder."""
    return InsightGenerationDeps(
        chat=chat, embed=FakeEmbeddingClient(), staleness_days=STALENESS_DAYS
    )


def _claim_pk(claim_business_id: str) -> Any:
    """The claim's surrogate key as a subquery — for the raw deletes below.

    Raw rather than through the repository on purpose: two tests here have to
    reach past `services/rag` to put the database in a state no command
    produces (a claim missing one kind), and doing that through the owner would
    be inventing a second writer to test that there is only one.
    """
    return sa.select(Claim.id).where(Claim.claim_id == claim_business_id).scalar_subquery()


def _narrative(schema: type[Any]) -> Any:
    from tests.insight_fixture import fill

    return fill(schema)


@requires_db
async def test_a_refresh_writes_one_row_per_kind_with_its_model_and_timestamp(
    db: AsyncSession, system: CallerContext, claim_id: str
) -> None:
    """AC 1's first half, and the shape every other test here builds on."""
    before = datetime.now(UTC)
    run = await refresh_claim_insights(
        db, system, claim_business_id=claim_id, deps=deps(FakeChatClient())
    )

    assert run.written == len(InsightKind)
    assert run.failed == 0
    assert run.model_unavailable is False

    cached = await rag.claim_insights(db, system, claim_business_id=claim_id)
    assert {insight.kind for insight in cached} == set(InsightKind)
    for insight in cached:
        assert insight.model == FakeChatClient.model
        assert insight.generated_at >= before
        assert insight.content


@requires_db
async def test_every_figure_in_the_content_is_the_service_s_own_output(
    db: AsyncSession, system: CallerContext, claim_id: str
) -> None:
    """**AD-2's enforcement mechanism.** Treat this test as mandatory.

    Each kind's figures are compared against a *second, independent* call to the
    service they came from, made after the insight was persisted. That is what
    makes it non-vacuous: an implementation that let the model supply a number
    would produce a card whose every field is present and well-typed, and only
    an equality against the deterministic source can tell that apart from a
    correct one.

    The comparison is by value and it is exhaustive per kind — every money
    amount, every count, every score, every verdict, every threshold and every
    rule-document version on the stored card. A test that checked one field
    would pass against a card whose other six were invented.
    """
    await refresh_claim_insights(
        db, system, claim_business_id=claim_id, deps=deps(FakeChatClient())
    )
    cached = {
        insight.kind: insight.content
        for insight in await rag.claim_insights(db, system, claim_business_id=claim_id)
    }

    # --- reserve adequacy: services/financials -----------------------------
    check = (await reserve_check(db, system, claim_business_id=claim_id)).require()
    reserve = cached[InsightKind.reserve_adequacy_review]
    assert reserve["verdict"] == check.verdict.value
    assert reserve["verdict_rationale"] == check.rationale
    assert reserve["ratio_bp"] == check.ratio_bp
    assert reserve["bands_version"] == check.bands_version
    assert reserve["reserve"] == {
        "cents": check.reserve_cents,
        "display": format_dollars(check.reserve_cents),
    }
    assert reserve["remaining_indemnity"] == {
        "cents": check.remaining_indemnity_cents,
        "display": format_dollars(check.remaining_indemnity_cents),
    }
    assert reserve["bills_on_file"] is (check.remaining_medical_cents is not None)
    if check.remaining_medical_cents is not None:
        assert reserve["remaining_medical"] == {
            "cents": check.remaining_medical_cents,
            "display": format_dollars(check.remaining_medical_cents),
        }

    # --- next best actions: services/worklist -------------------------------
    actions = (await next_actions(db, system, claim_business_id=claim_id)).require()
    stored = cached[InsightKind.next_best_actions]
    assert stored["cap"] == actions.cap
    assert stored["rules_version"] == actions.rules_version
    assert stored["action_count"] == len(actions.items)
    assert [row["id"] for row in stored["actions"]] == [item.id for item in actions.items]
    assert [row["urgency"] for row in stored["actions"]] == [
        item.urgency.value for item in actions.items
    ]
    assert [row["label"] for row in stored["actions"]] == [item.label for item in actions.items]

    # --- fraud risk: services/derivations ------------------------------------
    signals = (await fraud_signals(db, system, claim_business_id=claim_id)).require()
    fraud = cached[InsightKind.fraud_risk_indicators]
    assert fraud["signals"] == {
        "fraud_score": signals.fraud_score,
        "fraud_flag": signals.fraud_flag,
        "siu_review": signals.siu_review,
        "fraud_flagged": signals.fraud_flagged,
        "siu_fraud_score_min": signals.siu_fraud_score_min,
        "fraud_flag_score_min": signals.fraud_flag_score_min,
        "thresholds_version": signals.thresholds_version,
    }

    # --- similar cases: services/rag ----------------------------------------
    found = (
        await similar_cases(
            db,
            system,
            claim_business_id=claim_id,
            client=FakeEmbeddingClient(),
            staleness_days=STALENESS_DAYS,
        )
    ).require()
    similar = cached[InsightKind.similar_case_outcomes]
    assert similar["neighbour_count"] == len(found.items)
    assert similar["stale_count"] == found.stale_count
    assert similar["staleness_disclosure"] == found.disclosure
    assert similar["book_is_empty"] is (not found.items)
    assert [row["claim_id"] for row in similar["neighbours"]] == [
        item.claim_id for item in found.items
    ]
    assert [row["distance"] for row in similar["neighbours"]] == [
        item.distance for item in found.items
    ]
    assert [row["severity_score"] for row in similar["neighbours"]] == [
        item.severity_score for item in found.items
    ]


@requires_db
async def test_a_second_refresh_replaces_the_rows_rather_than_duplicating_them(
    db: AsyncSession, system: CallerContext, claim_id: str
) -> None:
    """AC's re-refresh row: unique `(claim_id, kind)` is the cache's whole shape.

    The row count is asserted *and* the timestamps are asserted to have moved,
    because a version of this that only counted rows would pass against an
    upsert that silently did nothing on conflict — four rows, unchanged, and a
    Refresh button that appears to work.
    """
    await refresh_claim_insights(
        db, system, claim_business_id=claim_id, deps=deps(FakeChatClient())
    )
    first = {
        insight.kind: insight.generated_at
        for insight in await rag.claim_insights(db, system, claim_business_id=claim_id)
    }

    await refresh_claim_insights(
        db, system, claim_business_id=claim_id, deps=deps(FakeChatClient())
    )
    second = await rag.claim_insights(db, system, claim_business_id=claim_id)

    assert len(second) == len(InsightKind)
    assert {insight.kind for insight in second} == set(InsightKind)
    for insight in second:
        assert insight.generated_at > first[insight.kind]


@requires_db
async def test_a_kind_whose_answer_fails_its_schema_writes_nothing_and_leaves_the_rest(
    db: AsyncSession, system: CallerContext, claim_id: str
) -> None:
    """The I/O matrix's "malformed model output" row.

    The fraud narratives are the ones refused, so the assertion is about a card
    that is *absent* rather than half-written — there is no such thing as a
    partial insight, because the content model is validated before the upsert.
    The other three persist, which is the per-kind savepoint doing its job.
    """
    chat = MalformedChatClient(FraudRedFlagsNarrative, FraudLowRiskNarrative)
    run = await refresh_claim_insights(db, system, claim_business_id=claim_id, deps=deps(chat))

    assert run.written == len(InsightKind) - 1
    assert run.failed == 1
    # A bad completion is not an outage: the server answered, it just answered
    # wrongly, and the interactive route must not turn that into a 503.
    assert run.model_unavailable is False

    kinds = {
        insight.kind for insight in await rag.claim_insights(db, system, claim_business_id=claim_id)
    }
    assert InsightKind.fraud_risk_indicators not in kinds
    assert kinds == set(InsightKind) - {InsightKind.fraud_risk_indicators}


class _SessionPoisoningChatClient:
    """A client whose first kind leaves the session in a failed transaction.

    A gather issues four scoped queries per kind, and any of them can raise —
    a statement timeout, a lost connection, a bug in a future kind's tool. What
    matters is not the query but the *state it leaves behind*: PostgreSQL
    refuses every subsequent statement on that connection until the transaction
    is rolled back, so a session left failed takes down every later kind of this
    claim and every claim after it in the batch.

    Modelled by executing a statement that cannot succeed. That is a real
    `SQLAlchemyError` out of a real failed transaction rather than a raised
    sentinel, which is the difference between asserting the rollback and
    asserting that a fake was constructed.
    """

    model = "poisoning-chat"

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self.calls = 0

    async def structured[T: Any](self, *, system: str, user: str, schema: type[T]) -> T:
        self.calls += 1
        if self.calls == 1:
            await self._db.execute(sa.text("SELECT 1 FROM lineworker_no_such_table"))
        return fill(schema)


@requires_db
async def test_a_database_error_in_one_gather_does_not_poison_the_rest_of_the_run(
    db: AsyncSession, system: CallerContext, claim_id: str
) -> None:
    """A2: the failure path that had no `await db.rollback()`.

    `_attempt`'s write-stage handler rolled back and its generation-stage
    handler did not, so a database error during a kind's gather returned "one
    failed kind" while leaving the session unusable. Every later kind then
    raised `PendingRollbackError` from a line with nothing to do with the
    original fault, was caught by the same handler, and was counted as another
    failed kind — and in a batch run the *next claim* failed the same way, so
    one transient error took out the whole tick.

    Asserted as the three survivors rather than as the absence of an exception:
    a version that swallowed the poisoning without rolling back would also not
    raise, and would report four failures instead of one.
    """
    chat = _SessionPoisoningChatClient(db)

    run = await refresh_claim_insights(db, system, claim_business_id=claim_id, deps=deps(chat))

    assert run.failed == 1, "a database error in one gather was allowed to fail the other kinds"
    assert run.written == len(InsightKind) - 1
    assert run.model_unavailable is False, "a database error is not a model outage"
    assert run.failed_kinds == (rag.ALL_KINDS[0],)

    kinds = {
        insight.kind for insight in await rag.claim_insights(db, system, claim_business_id=claim_id)
    }
    assert kinds == set(InsightKind) - {rag.ALL_KINDS[0]}

    # …and the session is still usable afterwards, which is what the next claim
    # in a batch depends on.
    assert await db.scalar(sa.select(sa.func.count()).select_from(Claim))


@requires_db
async def test_an_unreachable_model_writes_nothing_and_leaves_the_previous_cards_standing(
    db: AsyncSession, system: CallerContext, claim_id: str
) -> None:
    """The I/O matrix's "model server down" row, which is a cache's honest state.

    Generate, then take the model away and refresh again: no row moves, every
    `generated_at` is the one it already had, and the run reports the outage as
    a flag rather than as an exception. That last part is what
    `POST …/insights/refresh` turns into a 503 and what the scheduled job
    survives.

    **It gives up after the first kind**, and the count is asserted as one
    rather than four deliberately (review of Story 6.2, M6). A container that
    did not answer for the similar-case narrative will not answer for the next
    three, and pressing on would spend `CHAT_REQUEST_TIMEOUT_SECONDS` per kind —
    four two-minute timeouts on the route a handler is sitting in front of, to
    learn what the first one already said. The client's own call counter is what
    makes that assertable: a version that ploughed on would show four.

    The three kinds never attempted are counted as neither written nor failed —
    `failed` is rows this run tried — but they are **named** in `failed_kinds`,
    which is the question a caller asks (follow-up review of Story 6.2, B7).
    Publishing only the one that failed left the other three looking never
    generated with nothing to say they had not been tried; that matters most on
    the run that *did* write a card first, because that one answers 200 and the
    tab renders the list.
    """
    await refresh_claim_insights(
        db, system, claim_business_id=claim_id, deps=deps(FakeChatClient())
    )
    before = {
        insight.kind: (insight.generated_at, insight.model)
        for insight in await rag.claim_insights(db, system, claim_business_id=claim_id)
    }

    chat = UnavailableChatClient()
    run = await refresh_claim_insights(db, system, claim_business_id=claim_id, deps=deps(chat))

    assert run.written == 0
    assert run.failed == 1
    assert chat.calls == 1, "the run kept asking a model server that had already not answered"
    assert run.model_unavailable is True
    assert run.failed_kinds == rag.ALL_KINDS, (
        "the kinds after the outage were left out of the report, so a caller "
        "could not tell an un-attempted card from a never-generated one"
    )

    after = {
        insight.kind: (insight.generated_at, insight.model)
        for insight in await rag.claim_insights(db, system, claim_business_id=claim_id)
    }
    assert after == before


@requires_db
async def test_the_fraud_variant_is_the_derivation_s_answer_and_not_the_model_s(
    db: AsyncSession, system: CallerContext
) -> None:
    """AC 3's mechanism: `outcome` is decided before any prompt is composed.

    Two claims, chosen from the seed by what `services/derivations` says about
    them rather than by what a card would like to show: one that clears a fraud
    threshold and one that clears neither. The stored `outcome` must follow the
    derivations in both directions — a test that only checked the elevated case
    would pass against an implementation that always wrote red flags.

    The `red_flags` variant additionally must carry a non-empty list, which is
    the other half of AC 3: the low-risk case is a *variant*, not this one with
    nothing in it.
    """
    elevated, quiet = await _one_claim_of_each_fraud_outcome(db, system)

    for claim_business_id, expected in ((elevated, "red_flags"), (quiet, "low_risk")):
        await refresh_claim_insights(
            db,
            system,
            claim_business_id=claim_business_id,
            deps=deps(FakeChatClient()),
        )
        cached = {
            insight.kind: insight.content
            for insight in await rag.claim_insights(db, system, claim_business_id=claim_business_id)
        }
        fraud = cached[InsightKind.fraud_risk_indicators]
        assert fraud["outcome"] == expected, claim_business_id
        if expected == "red_flags":
            assert fraud["narrative"]["red_flags"], "the red-flag variant may not be an empty list"
        else:
            assert fraud["narrative"]["confirmation"]
            assert "red_flags" not in fraud["narrative"]


async def _one_claim_of_each_fraud_outcome(
    db: AsyncSession, system: CallerContext
) -> tuple[str, str]:
    """A seeded claim above a fraud threshold and one below both.

    Chosen by asking the tool — which asks the registered derivations — rather
    than by hardcoding two claim ids: a reseed that moved the scores would
    otherwise turn this test into an assertion about the fixture.
    """
    candidates = await rag.claims_needing_insights(db, system, limit=None)
    elevated: str | None = None
    quiet: str | None = None
    for claim_business_id in candidates:
        signals = (await fraud_signals(db, system, claim_business_id=claim_business_id)).require()
        if signals.siu_review or signals.fraud_flagged:
            elevated = elevated or claim_business_id
        else:
            quiet = quiet or claim_business_id
        if elevated and quiet:
            return elevated, quiet
    raise AssertionError("the seed no longer holds one claim of each fraud outcome")


@requires_db
async def test_an_indeterminate_reserve_produces_a_card_with_no_ratio(
    db: AsyncSession,
    system: CallerContext,
    claim_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reserve state the seed cannot currently reach, reached deliberately.

    `reserve_check_for_claim` always has the bill rows in hand, so
    `remaining_medical_cents` is never `None` against today's data and the
    `indeterminate` verdict is unreachable end to end. That is a fact about the
    seed rather than about the rule — `classify_reserve` implements the rule and
    `ReserveCheck` documents it — so the tool's envelope is substituted here to
    put the generator in that state and assert what it writes.

    Substituting the *tool* rather than the service is deliberate: it is the
    smallest seam that produces the state, it leaves the content assembly and
    the schema validation under test, and it is exactly the seam Story 6.3's
    registry will formalise.
    """
    check = (await reserve_check(db, system, claim_business_id=claim_id)).require()
    indeterminate = type(check)(
        verdict=ReserveVerdict.indeterminate,
        ratio_bp=None,
        projected_remaining_cents=None,
        remaining_indemnity_cents=check.remaining_indemnity_cents,
        remaining_medical_cents=None,
        scheduled_indemnity_cents=check.scheduled_indemnity_cents,
        disbursed_indemnity_cents=check.disbursed_indemnity_cents,
        disbursed_medical_cents=check.disbursed_medical_cents,
        reserve_cents=check.reserve_cents,
        rationale="Medical bills are not on file, so exposure cannot be judged.",
        bands_version=check.bands_version,
    )

    async def stubbed(*_args: Any, **_kwargs: Any) -> ToolResult[Any]:
        return ToolResult.succeeded(
            indeterminate,
            display={
                "reserveCents": format_dollars(indeterminate.reserve_cents),
                "remainingIndemnityCents": format_dollars(indeterminate.remaining_indemnity_cents),
                "disbursedIndemnityCents": format_dollars(indeterminate.disbursed_indemnity_cents),
                "disbursedMedicalCents": format_dollars(indeterminate.disbursed_medical_cents),
            },
        )

    monkeypatch.setattr(agent_insights, "reserve_check", stubbed)
    await refresh_claim_insights(
        db, system, claim_business_id=claim_id, deps=deps(FakeChatClient())
    )

    stored = {
        insight.kind: insight.content
        for insight in await rag.claim_insights(db, system, claim_business_id=claim_id)
    }[InsightKind.reserve_adequacy_review]

    assert stored["bills_on_file"] is False
    assert stored["ratio_bp"] is None
    assert stored["remaining_medical"] is None
    assert stored["projected_remaining"] is None
    assert stored["verdict"] == ReserveVerdict.indeterminate.value


@requires_db
async def test_every_written_card_records_one_content_free_audit_event(
    db: AsyncSession, system: CallerContext, claim_id: str
) -> None:
    """AD-4: insight writes are exempt from the approval gate, never from audit.

    One event per card, in the same transaction as the row, carrying the claim,
    the kind and the model — and **not one word of the narrative** (AD-11). The
    assertion scans the whole event for any sentence the fake produced, which is
    what makes it a check on the *content* rather than on the key names.
    """
    await refresh_claim_insights(
        db, system, claim_business_id=claim_id, deps=deps(FakeChatClient())
    )

    events = list(
        (
            await db.scalars(
                sa.select(AuditEvent)
                .where(AuditEvent.action == rag.INSIGHT_GENERATED_ACTION)
                .where(AuditEvent.entity_id.like(f"{claim_id}:%"))
                .order_by(AuditEvent.id)
            )
        ).all()
    )

    assert len(events) == len(InsightKind)
    assert {event.entity_id for event in events} == {
        f"{claim_id}:{kind.value}" for kind in InsightKind
    }
    for event in events:
        assert event.entity == "ai_insight"
        assert event.before is None
        assert event.after is not None
        assert set(event.after) == {"kind", "model"}
        assert "Deterministic test" not in json.dumps(event.after)


@requires_db
async def test_the_attempt_cursor_is_deliberately_unaudited(
    db: AsyncSession, system: CallerContext, claim_id: str
) -> None:
    """The other half of AD-4 on this story: an argued exemption, asserted.

    `store_insights` writes and commits an `ai_insight_attempt` row before it
    generates anything, and emits no audit event for it. AD-4 has no carve-out,
    so that is either a decision or a miss — and the difference between the two
    is whether anything says so. `AiInsightAttempt`'s class docstring and
    migration 0042's both carry the argument (the row is a scheduler's cursor,
    not a change to a claim; the audited fact is what the refresh produced, and
    `ai_insight.generated` carries it), and this asserts the outcome so that a
    later story adding an event has to come back and delete a test rather than
    quietly changing the volume Epic 8's review reads (follow-up review of Story
    6.2, B5).

    Asserted over every event the whole refresh wrote, not merely over a
    filtered subset: the property is that `ai_insight.generated` is the *only*
    action this path emits, which a query for that action could not tell from a
    second action nobody thought to look for.
    """
    await refresh_claim_insights(
        db, system, claim_business_id=claim_id, deps=deps(FakeChatClient())
    )

    attempted = await db.scalar(
        sa.select(AiInsightAttempt.attempted_at)
        .join(Claim, AiInsightAttempt.claim_id == Claim.id)
        .where(Claim.claim_id == claim_id)
    )
    assert attempted is not None, "the attempt cursor was not written — the fixture is inert"

    actions = set(
        (
            await db.scalars(
                sa.select(AuditEvent.action).where(AuditEvent.entity_id.like(f"{claim_id}:%"))
            )
        ).all()
    )
    assert actions == {rag.INSIGHT_GENERATED_ACTION}
    assert not (
        await db.scalars(sa.select(AuditEvent).where(AuditEvent.entity == "ai_insight_attempt"))
    ).all()


@requires_db
async def test_no_log_line_carries_a_prompt_or_the_model_s_answer(
    db: AsyncSession,
    system: CallerContext,
    claim_id: str,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """AD-11, asserted against the bytes the process actually emits.

    The fake's sentences are distinctive on purpose, so this is a real search
    rather than a check that some field is absent: if any part of a prompt or a
    completion reached structlog, the string would be in stderr.

    The prompts are checked too, and they are the larger risk. A completion is a
    paragraph; a *prompt* contains the claim's cause, its ICD description and up
    to three retrieved passages, which is PHI-adjacent claim text — and the
    tempting debug line ("what did we send?") is exactly the one that would put
    it in an operator's log aggregator for ever.
    """
    from logging_config import configure_logging

    configure_logging("INFO")
    chat = FakeChatClient()
    await refresh_claim_insights(db, system, claim_business_id=claim_id, deps=deps(chat))

    emitted = capfd.readouterr().err
    assert chat.calls, "the fake was never asked for a completion"
    for prompt in chat.prompts:
        for line in prompt.splitlines():
            probe = line.strip()
            if len(probe) < 40:
                continue
            assert probe not in emitted, "a prompt body reached the log (AD-11)"
    assert "Deterministic test narrative" not in emitted
    # …and the events that *are* emitted carry the ids and counts they should.
    assert "rag.insights_stored" in emitted


class _DyingChatClient:
    """Answers `healthy` completions, then the model server goes away.

    The case a client that is always up or always down cannot produce, and the
    one that matters for B7: a run that wrote a card and *then* lost the model
    answers 200 with a fresh payload, so its report is the only thing telling a
    handler that two of the four cards on screen were never attempted.
    """

    model = "dying-chat"

    def __init__(self, healthy: int) -> None:
        self._healthy = healthy
        self.calls = 0

    async def structured[T: Any](self, *, system: str, user: str, schema: type[T]) -> T:
        self.calls += 1
        if self.calls > self._healthy:
            raise ChatUnavailable("no model server")
        return fill(schema)


@requires_db
async def test_the_kinds_after_an_outage_are_named_rather_than_left_silent(
    db: AsyncSession, system: CallerContext, claim_id: str
) -> None:
    """B7: a partial run's report covers what it never attempted, not only what failed.

    One card is written, the model dies, and the run stops — correctly, because
    a container that did not answer for the second kind will not answer for the
    third or the fourth, and pressing on would spend two more chat timeouts on
    the route a handler is sitting in front of. But `written == 1` means this
    answers 200 with a fresh payload, and the two kinds nobody ever asked for
    render as "not generated" — the same state as a claim the scheduler has not
    reached, with nothing to distinguish them.

    So `failed_kinds` names all three: the one that failed and the two that were
    skipped. `failed` still counts one, because it counts attempts, and the two
    numbers disagreeing is the point rather than a defect.
    """
    chat = _DyingChatClient(healthy=1)

    run = await refresh_claim_insights(db, system, claim_business_id=claim_id, deps=deps(chat))

    assert run.written == 1
    assert run.failed == 1, "an un-attempted kind was counted as a failed row"
    assert chat.calls == 2, "the run kept asking after the model stopped answering"
    assert run.model_unavailable is True
    assert run.failed_kinds == rag.ALL_KINDS[1:]

    # …and the cache agrees with the report: one card, three slots empty.
    cached = await rag.claim_insights(db, system, claim_business_id=claim_id)
    assert {insight.kind for insight in cached} == {rag.ALL_KINDS[0]}


@requires_db
async def test_a_claim_that_vanishes_mid_batch_is_skipped_rather_than_killing_the_run(
    db: AsyncSession,
    system: CallerContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B6: the queue is a snapshot, and a stale entry in it is a race.

    `claims_needing_insights` reads the queue, and the claims in it are then
    generated one at a time over minutes of model time. A claim deleted by
    Story 8.1's purge, or reassigned out of the actor's partition, raises
    `ClaimNotVisible` from `store_insights`' own scope resolution when its turn
    comes — and that used to come out of the loop, ending the batch with every
    remaining claim unprocessed and turning `POST /admin/insight-refresh` into a
    500 for a run that was otherwise going perfectly well.

    Driven by making the *command* refuse one claim rather than by deleting a
    row, because a claim in this seed is referenced by a dozen tables and the
    property under test is the loop's handling, not the cascade's.
    """
    pending = await rag.claims_needing_insights(db, system, limit=3)
    assert len(pending) == 3, "the seed did not offer three pending claims"
    vanished = pending[0]
    real = rag.store_insights

    async def refusing(
        session: AsyncSession,
        ctx: CallerContext,
        *,
        claim_business_id: str,
        generator: Any,
        kinds: Any = rag.ALL_KINDS,
    ) -> Any:
        if claim_business_id == vanished:
            raise ClaimNotVisible(claim_business_id)
        return await real(
            session, ctx, claim_business_id=claim_business_id, generator=generator, kinds=kinds
        )

    monkeypatch.setattr(rag, "store_insights", refusing)

    run = await refresh_pending_insights(db, system, limit=3, deps=deps(FakeChatClient()))

    assert run.claims == 2, "the vanished claim was counted as visited"
    assert run.written == 2 * len(InsightKind)
    assert run.model_unavailable is False
    # The other two really generated, and the skipped one is untouched — still
    # pending, which is the right state for a claim nobody could resolve.
    for claim_business_id in pending[1:]:
        cached = await rag.claim_insights(db, system, claim_business_id=claim_business_id)
        assert {insight.kind for insight in cached} == set(InsightKind)
    assert vanished in await rag.claims_needing_insights(db, system, limit=None)


@requires_db
async def test_two_concurrent_refreshes_of_one_claim_do_not_interleave(
    db: AsyncSession, system: CallerContext, claim_id: str, seeded_db_url: str
) -> None:
    """B10: the scheduled job and the Refresh button are two callers of one function.

    With a commit per kind, two runs on one claim leave its four rows carrying
    two different `generated_at` values — half a card set from each — and the
    tab renders two timestamps for what a handler saw as one refresh. Nothing is
    corrupt; the timestamp is simply not the thing it claims to be.

    Driven as a real race: a second session on a second engine, so the two runs
    are two database connections exactly as the job and the route are, and a
    gate that holds the first run inside the kind loop until the second has had
    its chance to start. A build without the lock writes eight rows' worth of
    two generations here; with it, the second run finds the claim held and
    yields an empty result.

    The assertion is on the *timestamps* rather than on which run won, because
    which one wins is a race and the property is not.
    """
    released = asyncio.Event()
    entered = asyncio.Event()

    class _GatedChatClient:
        model = "gated-chat"

        async def structured[T: Any](self, *, system: str, user: str, schema: type[T]) -> T:
            entered.set()
            await released.wait()
            return fill(schema)

    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as other:
            first = asyncio.create_task(
                refresh_claim_insights(
                    db, system, claim_business_id=claim_id, deps=deps(_GatedChatClient())
                )
            )
            async with asyncio.timeout(10):
                await entered.wait()

                # The first run is inside its kind loop, holding the claim.
                second = await refresh_claim_insights(
                    other, system, claim_business_id=claim_id, deps=deps(FakeChatClient())
                )
                assert second.written == 0, "a second run generated the same claim concurrently"
                assert second.failed == 0, "yielding is not failing"

                released.set()
                winner = await first

    finally:
        released.set()
        await engine.dispose()

    assert winner.written == len(InsightKind)
    cached = await rag.claim_insights(db, system, claim_business_id=claim_id)
    assert len({insight.generated_at for insight in cached}) == 1, (
        "one claim's cards carry two generation timestamps — two runs interleaved"
    )
    assert {insight.model for insight in cached} == {_GatedChatClient.model}


@requires_db
async def test_the_scheduled_refresh_is_bounded_by_its_claim_budget(
    db: AsyncSession, system: CallerContext
) -> None:
    """`insight_refresh_batch_size` is a bound on claims, not on rows.

    Each claim costs up to four completions, so the knob counts claims and the
    run writes `limit * len(InsightKind)` rows at most. Asserted with a limit of
    two against a portfolio of a hundred, so a run that ignored the bound would
    be visible immediately rather than only on a large seed.

    The pending set shrinks by exactly the claims visited, which is the other
    half: a bounded tick that did not make progress would loop for ever on the
    head of the queue.
    """
    pending_before = await rag.claims_needing_insights(db, system, limit=None)
    assert len(pending_before) > 2

    run = await refresh_pending_insights(db, system, limit=2, deps=deps(FakeChatClient()))

    assert run.claims == 2
    assert run.written == 2 * len(InsightKind)
    assert run.failed == 0

    pending_after = await rag.claims_needing_insights(db, system, limit=None)
    assert len(pending_after) == len(pending_before) - 2
    assert set(pending_after) == set(pending_before) - set(pending_before[:2])


@requires_db
async def test_the_scheduled_refresh_stops_at_the_first_outage(
    db: AsyncSession, system: CallerContext
) -> None:
    """No retry storm: one unreachable claim is enough to end the tick.

    A container that did not answer for one claim will not answer for the next
    ninety-nine, and a run that ploughed on would spend a timeout per claim per
    kind to learn what the first one already said. The remaining claims are
    still pending, which is what makes stopping safe — the next tick is the
    retry, which is a bounded retry by construction.
    """
    run = await refresh_pending_insights(db, system, limit=5, deps=deps(UnavailableChatClient()))

    assert run.claims == 1
    assert run.written == 0
    assert run.model_unavailable is True


@requires_db
async def test_a_claim_stops_being_pending_once_every_kind_exists(
    db: AsyncSession, system: CallerContext, claim_id: str
) -> None:
    """The queue's whole definition, and the reason an *old* card is not pending.

    A claim is pending while it is missing a kind. Once all four exist it drops
    out and the scheduled job leaves it alone for ever — re-generation is the
    on-demand path's job, which is what stops the portfolio being re-narrated
    hourly against a model server that answers one request at a time. Both
    halves are asserted, because "it drops out" and "it comes back when a kind
    is deleted" are the two directions a wrong predicate gets wrong.
    """
    assert claim_id in await rag.claims_needing_insights(db, system, limit=None)

    await refresh_claim_insights(
        db, system, claim_business_id=claim_id, deps=deps(FakeChatClient())
    )
    assert claim_id not in await rag.claims_needing_insights(db, system, limit=None)

    await db.execute(
        sa.delete(AiInsight)
        .where(AiInsight.claim_id == _claim_pk(claim_id))
        .where(AiInsight.kind == InsightKind.fraud_risk_indicators)
    )
    await db.commit()
    assert claim_id in await rag.claims_needing_insights(db, system, limit=None)


# --- the review's four: transactions, starvation, silent writes, config errors


class _TransactionWatchingChatClient:
    """A `ChatClient` that records whether a transaction was open when it was asked.

    The only way to observe H2's property from the outside. `db.in_transaction()`
    is true from the first statement of a SQLAlchemy session until its next
    commit or rollback, so a completion issued while it is true is a completion
    holding a pooled connection idle-in-transaction for as long as the model
    takes — up to `CHAT_REQUEST_TIMEOUT_SECONDS`, four times per claim, across a
    whole book on the admin trigger.
    """

    model = "transaction-watching-chat"

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self.in_transaction: list[bool] = []

    async def structured[T: Any](self, *, system: str, user: str, schema: type[T]) -> T:
        self.in_transaction.append(self._db.in_transaction())
        return cast(T, _narrative(schema))


class _ClaimPickyChatClient:
    """A `ChatClient` that refuses one claim's completions and answers everyone else's.

    The claim id appears in the user message, fenced and tagged, so a fake can
    tell *which* claim it is being asked about without being told — which is
    what makes "one claim the model deterministically refuses" expressible at
    all. `ChatSchemaRejected` rather than `ChatUnavailable`, because the
    starvation this models is the permanent kind: a claim whose narrative the
    model reliably cannot fit into the schema, with a healthy server.
    """

    model = "picky-chat"

    def __init__(self, refuse_claim_id: str) -> None:
        self._refuse = refuse_claim_id
        self.seen: list[str] = []

    async def structured[T: Any](self, *, system: str, user: str, schema: type[T]) -> T:
        if self._refuse in user:
            self.seen.append(self._refuse)
            raise ChatSchemaRejected(f"bad answer for {schema.__name__}")
        return cast(T, _narrative(schema))


@requires_db
async def test_no_transaction_is_open_while_the_model_is_being_asked(
    db: AsyncSession, system: CallerContext, claim_id: str
) -> None:
    """H2: the completion happens outside the transaction, and the write inside it.

    This shipped the other way round — `_generate_and_write` ran entirely inside
    a `begin_nested()` savepoint with one commit at the end of the claim — so a
    single claim held a connection idle-in-transaction for up to four chat
    timeouts, eight minutes at the shipped `CHAT_REQUEST_TIMEOUT_SECONDS`, while
    `POST /admin/insight-refresh` did that for the whole book.

    Asserted from the client's own vantage point rather than by reading the
    restructured code, and asserted for **every** completion rather than the
    first: the failure the ordering fixes is the second, third and fourth kind
    of a claim, which run after the first kind's write has already opened one.

    The other half — that the write *is* transactional — is
    `test_a_schema_rejection_writes_nothing_for_that_kind_and_leaves_the_rest`
    and the audit assertions, which would all fail against a version that had
    simply stopped using transactions.
    """
    chat = _TransactionWatchingChatClient(db)

    run = await refresh_claim_insights(db, system, claim_business_id=claim_id, deps=deps(chat))

    assert run.written == len(InsightKind)
    assert len(chat.in_transaction) == len(InsightKind)
    assert chat.in_transaction == [False] * len(InsightKind), (
        "a chat completion was issued with a transaction open — a connection held "
        "idle-in-transaction for as long as the model takes to answer"
    )


@requires_db
async def test_a_permanently_failing_claim_does_not_block_the_rest(
    db: AsyncSession, system: CallerContext
) -> None:
    """H4: the head of the queue advances even when the head never succeeds.

    The scheduled run is bounded and its membership test is "missing at least
    one kind" — and a kind that fails writes no row, so a claim the model
    reliably refuses is pending again the moment the run ends. Ordered by
    `claim.id` alone (which is how this shipped) that claim sat at position one
    of every batch for ever, re-spending its completions each tick; and because
    `refresh_pending_insights` stops at the first claim whose failure was an
    unavailable model server, one such claim could stop the portfolio outright.

    Modelled at `limit=1`, which is the sharpest form of the question: with a
    batch of one, either the queue rotates or nothing else in the book is ever
    generated. Three ticks, and the assertion is that they visited three
    *different* claims and left two of them with a full set of cards.
    """
    pending = await rag.claims_needing_insights(db, system, limit=None)
    assert len(pending) >= 3
    poisoned = pending[0]
    chat = _ClaimPickyChatClient(poisoned)

    visited: list[str] = []
    for _ in range(3):
        before = set(await rag.claims_needing_insights(db, system, limit=1))
        await refresh_pending_insights(db, system, limit=1, deps=deps(chat))
        visited.extend(before)

    assert visited[0] == poisoned, "the fixture no longer starts at the failing claim"
    assert len(set(visited)) == 3, (
        f"three bounded ticks visited {sorted(set(visited))} — a claim that always fails "
        "is holding the head of the queue"
    )
    # …and the two claims behind it really were generated, so the rotation is
    # progress rather than a queue that merely reorders.
    for claim_business_id in visited[1:]:
        cached = await rag.claim_insights(db, system, claim_business_id=claim_business_id)
        assert {insight.kind for insight in cached} == set(InsightKind)
    assert not await rag.claim_insights(db, system, claim_business_id=poisoned)


@requires_db
async def test_a_zero_row_upsert_is_counted_as_a_failure(
    db: AsyncSession, system: CallerContext, claim_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M10: a write that affected nothing is a failure, not a silent success.

    `upsert_insight` reports zero rows when its scoped `SELECT` produced no
    source row — the claim moved out from under a run that had already resolved
    it. Before the review the branch guarding the audit event simply skipped it
    and returned, so the kind was counted as neither written nor failed, emitted
    no AD-4 event, and stayed pending for ever while every future refresh
    re-spent its completion for nothing.

    Forced by patching the repository, which is the only way to reach a race
    that needs two sessions and a `DELETE` between two statements — and which
    keeps the assertion on the behaviour rather than on the race.
    """
    from data.repositories import insights as repo

    async def writes_nothing(*args: Any, **kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(repo, "upsert_insight", writes_nothing)
    run = await refresh_claim_insights(
        db, system, claim_business_id=claim_id, deps=deps(FakeChatClient())
    )

    assert run.written == 0
    assert run.failed == len(InsightKind)
    assert run.model_unavailable is False
    assert run.failed_kinds == tuple(InsightKind)

    monkeypatch.undo()
    assert not await rag.claim_insights(db, system, claim_business_id=claim_id)
    assert claim_id in await rag.claims_needing_insights(db, system, limit=None)
    events = await db.scalars(
        sa.select(AuditEvent).where(AuditEvent.entity_id.like(f"{claim_id}:%"))
    )
    assert events.all() == []


@requires_db
@pytest.mark.parametrize(
    "kind",
    [InsightKind.similar_case_outcomes, InsightKind.next_best_actions],
    ids=lambda k: k.value,
)
async def test_a_dimension_mismatch_is_a_configuration_error_and_not_an_outage(
    db: AsyncSession, system: CallerContext, claim_id: str, kind: InsightKind
) -> None:
    """M2: the one embedding failure that must not wear the 503.

    `services/rag/client.py` raises `EmbeddingDimensionMismatch` deliberately,
    naming the model, the width it returned and the width the column holds,
    because pointing `EMBEDDING_MODEL` at a model of a different dimensionality
    is a migration rather than a configuration flip. `services/rag/embeddings.py`
    re-raises it for that reason and this path now does too — a bare `except
    Exception` in the similar-case wrapper used to swallow it into
    `unavailable=True`, so the route answered "Model server unavailable" and
    told an operator to wait for a container that was answering perfectly well.

    Asserted as a propagation rather than as a count, which is the whole
    distinction: a counted failure is a card that will regenerate, and this one
    will fail identically on every future tick until somebody changes a setting.

    **Parametrized by kind, and each is run on its own** (follow-up review of
    Story 6.2, B4). Two kinds embed: the similar-case gather through
    `agents/tools/similar.py`, and the next-actions gather through the knowledge
    retrieval in `agents/insights.py::_knowledge`. Only the first was narrowed,
    and this test passed anyway because `similar_case_outcomes` happens to be
    `ALL_KINDS[0]` — so the run raised before the second path was ever reached,
    and reordering the enum would have hidden the gap without touching a line of
    this file. Driving each kind alone is what makes the two call sites answer
    for themselves.
    """

    class MismatchedEmbeddingClient:
        model = "wrong-width"

        async def embed(self, texts: Any) -> Any:
            raise EmbeddingDimensionMismatch(model="nomic-embed-text", returned=768, expected=1024)

    generator = agent_insights.ToolBackedGenerator(
        db,
        system,
        deps=InsightGenerationDeps(
            chat=FakeChatClient(),
            embed=MismatchedEmbeddingClient(),
            staleness_days=STALENESS_DAYS,
        ),
    )

    with pytest.raises(EmbeddingDimensionMismatch):
        await rag.store_insights(
            db, system, claim_business_id=claim_id, generator=generator, kinds=(kind,)
        )
