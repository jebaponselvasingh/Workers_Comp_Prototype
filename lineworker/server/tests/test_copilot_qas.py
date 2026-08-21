"""The seven deterministic quick actions — routing, provenance, variants, failure.

Story 6.4's whole subject, asserted at the level the guarantees are actually
made at. Seven properties, and each is a specific way this could be wrong:

1. **Every key reaches exactly its own node, and no model routed it.** AC 1 asks
   for this key by key, and the half that a passing route test asserts vacuously
   is "no LLM router was involved" — so every routing assertion below also
   counts how many times the scripted model was called. One call is the
   narration; two would be a router.
2. **Every entry declares `requires_llm`**, and exactly one entry declares
   `False`. Story 6.6 gates degradation on that flag, so it is a contract rather
   than a convenience, and `data_alignment` is the false path it needs.
3. **Figures come from tools.** Every money string a node puts in front of the
   model is one `reserve_check` produced, quoted from the envelope's `display`
   map; and no prompt file states a figure at all. Those are two independent
   mechanisms and neither is the prompt alone.
4. **The verdict and the variant are chosen in Python.** The fraud low-risk and
   red-flag variants are picked from the derivations' envelope before a prompt
   is composed, exactly as the Insights card picks its schema; the reserve
   `indeterminate` state renders as "not on file" and never as `$0`.
5. **Untrusted text is fenced and never reaches a transcript.** Retrieved
   passages and claim narrative enter the *user* message as fenced items;
   `data_alignment`, the one node that formats tool output for a human, carries
   no delimiter markup at all.
6. **A failed tool is a plain sentence and one terminal event**, never a stack
   trace and never a silent empty answer. A narration that decoded nothing is
   the same obligation reached the other way: the node succeeded, so the run
   must still say something rather than checkpoint a blank turn and terminate
   `error`.
7. **What the prompt refers to is either present or declared absent.** Every
   prompt file names `MATERIAL TO ANALYSE` and `rtw.md` tells the model to quote
   from it; the section can be missing, so the figures say so. And the
   labour-law disclaimer is appended in Python rather than requested in a
   prompt, because a scripted model cannot be made to write one and a
   requirement no test can assert is not one.

**Two levers, used deliberately.** Most tests here run against the real seeded
database, the real registry and the real services, because "the figure in the
prompt is the one the service computed" is not a statement a stub can make. The
state-dependent cases — a reserve with no bills, a stale neighbour, a tool that
refuses — replace one registry entry with a stub returning a crafted envelope,
because seeding a claim into each of those states would be seeding the assertion.
The stub is installed on `agents.qas.REGISTRY` rather than on
`agents.registry.REGISTRY`, since the node module binds the name at import.

Scripted models are declared as local classes above the tests that need them —
`test_ai_insights.py`'s pattern, so a fake cannot quietly grow behaviour for a
different test's benefit.
"""

import dataclasses
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from typing import Any

import pytest
import sqlalchemy as sa
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agents import prompts
from agents import qas as qas_module
from agents.context import CopilotContext
from agents.envelope import ToolResult
from agents.fencing import FIGURES_HEADING, ITEM_CLOSE, ITEM_OPEN, MATERIAL_HEADING
from agents.graph import CHAT_NODE, ENTRY_NODE, QUICK_ACTIONS, build_graph, route_entry, run_inputs
from agents.registry import REGISTRY, RegisteredTool, ToolKind
from agents.state import CopilotState
from agents.threads import saver_config
from agents.tools.fraud import FraudSignals
from api.routers.copilot import RunRequest, _validate_run_body
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, Claim
from data.models.enums import UserRole
from services import rag
from services.financials import ReserveCheck, ReserveVerdict
from tests.conftest import requires_db
from tests.embedding_fixture import FakeEmbeddingClient

#: What the scripted model answers. Long and unmistakable, so "the narration
#: reached the wire" is a real search rather than a check that some field name
#: is present.
REPLY = "Deterministic quick-action narration from a scripted chat model."

#: The staleness window every context below is built with. A test's own number,
#: not `Settings`': `insight_staleness_disclosure_days` is a deployment decision
#: and a test that read it would change meaning when an operator changed a knob.
STALENESS_DAYS = 7


# --- the scripted model --------------------------------------------------


class _RecordingChatModel(BaseChatModel):
    """Streams `REPLY` in three chunks and records every prompt it was given.

    Three chunks rather than one because the nodes call `astream`, and a fake
    whose `_stream` is the base class's fallback would yield the whole answer as
    a single chunk — which is indistinguishable from a node that awaited a
    complete completion, the exact failure "a quick action streams" is about.

    `calls` is what makes the AD-14 half of the routing assertions real. A
    quick-action run may call a model **once**, to narrate; a run that called it
    twice went through a router first, and a run that called it at all when
    `requires_llm` is `False` is not the action it declared itself to be.
    """

    calls: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted-quick-action"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=REPLY))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        self.calls.append(list(messages))
        for part in (REPLY[:20], REPLY[20:40], REPLY[40:]):
            yield ChatGenerationChunk(message=AIMessageChunk(content=part))


def _model() -> _RecordingChatModel:
    """A fresh recorder. `calls` is a class attribute on a pydantic model, so a
    per-instance list has to be assigned rather than defaulted — a shared list
    would make every test in this module see every other test's prompts."""
    model = _RecordingChatModel()
    model.calls = []
    return model


def _system_of(model: _RecordingChatModel) -> str:
    """The system message of the one completion this run made."""
    assert len(model.calls) == 1, f"expected exactly one model call, got {len(model.calls)}"
    system = model.calls[0][0]
    return str(system.content)


def _user_of(model: _RecordingChatModel) -> str:
    """The user message of the one completion this run made."""
    assert len(model.calls) == 1, f"expected exactly one model call, got {len(model.calls)}"
    return str(model.calls[0][1].content)


# --- the harness ---------------------------------------------------------


@pytest.fixture
async def engine(seeded_db_url: str) -> AsyncIterator[Any]:
    created = create_async_engine(
        seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    )
    try:
        yield created
    finally:
        await created.dispose()


@pytest.fixture
async def db(engine: Any) -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session


@pytest.fixture
async def system(db: AsyncSession) -> CallerContext:
    """The unbounded context, resolved the way the scheduled jobs resolve it."""
    user = (await db.scalars(sa.select(AppUser).where(AppUser.role == UserRole.system))).one()
    return CallerContext(user_id=user.id, role=user.role, employer_ids=ALL_EMPLOYERS)


@pytest.fixture
async def claim_id(db: AsyncSession) -> str:
    """A claim to ask about, chosen by id so two runs pick the same one."""
    claim = (await db.scalars(sa.select(Claim).order_by(Claim.id).limit(1))).one()
    return str(claim.claim_id)


@pytest.fixture
async def indexed(db: AsyncSession, system: CallerContext) -> None:
    """Vectors for the claims and the corpus — what retrieval needs to return anything.

    Requested per test rather than left to run order (6.1's `embedEverything`
    lesson, which cost that spec two tests). It is idempotent: a second call
    finds nothing stale and embeds nothing.
    """
    await rag.refresh_stale_embeddings(db, system, client=FakeEmbeddingClient(), limit=None)
    await rag.seed_knowledge_corpus(db, system, client=FakeEmbeddingClient())
    await db.commit()


def _context(engine: Any, claim: str | None, ctx: CallerContext) -> CopilotContext:
    return CopilotContext(
        caller=ctx,
        sessionmaker=async_sessionmaker(engine, expire_on_commit=False),
        claim_business_id=claim,
        embedding_client=FakeEmbeddingClient(),
        embedding_staleness_days=STALENESS_DAYS,
    )


async def _run_key(
    *,
    # `BaseChatModel` rather than the recorder, so the silent model below can be
    # driven through the same real graph. Every caller that then reads `.calls`
    # passes a `_RecordingChatModel` and the reader helpers narrow to it.
    model: BaseChatModel,
    engine: Any,
    ctx: CallerContext,
    claim: str,
    key: str | None,
    thread: str = "claim.WC-00000.u1.s1",
) -> list[Any]:
    """One whole turn through the real compiled graph. Returns every stream part.

    `tools=[]` on the harness: the grounded-chat node is not on any quick
    action's path, and a run that reached it would be a routing failure this
    module exists to catch rather than a tool call to exercise. The quick-action
    nodes get their tools through `agents/registry.invoke`, not through the
    agent harness, so nothing is stubbed out by that argument.
    """
    graph = build_graph(model=model, checkpointer=InMemorySaver(), max_tool_calls=3, tools=[])
    return [
        part
        async for part in graph.astream(
            run_inputs(
                caller=ctx,
                claim_business_id=claim,
                message="the button's label",
                quick_action=key,
            ),
            config=saver_config(thread),
            context=_context(engine, claim, ctx),
            stream_mode=["messages", "updates", "custom"],
        )
    ]


def _nodes(parts: Sequence[Any]) -> list[str]:
    """Which nodes finished, in order — the `updates` frames' whole content."""
    return [node for mode, chunk in parts if mode == "updates" for node in dict(chunk)]


def _streamed(parts: Sequence[Any]) -> str:
    """Everything that reached the wire as assistant content, reassembled.

    Both channels, because both become `messages` frames at the endpoint: model
    tokens through `messages`, and a model-free node's own note through
    `custom`. A test that read only one would pass for six of the seven actions
    and say nothing at all about the seventh.
    """
    out: list[str] = []
    for mode, chunk in parts:
        if mode == "messages":
            message, _metadata = chunk
            content = getattr(message, "content", "")
            if isinstance(content, str):
                out.append(content)
        elif mode == "custom" and isinstance(chunk, Mapping):
            note = chunk.get(qas_module.QAS_NOTE)
            if isinstance(note, str):
                out.append(note)
    return "".join(out)


def _stub(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    result: ToolResult[Any],
) -> None:
    """Replace one registry entry's implementation with a crafted envelope.

    Installed on `agents.qas.REGISTRY` because that module binds the name at
    import — patching `agents.registry.REGISTRY` would leave the nodes reading
    the original dict and every assertion below would be about the seeded claim
    rather than about the state being tested.

    The *entry* is replaced rather than the map's shape, so the schema, the kind
    and the declared injections are still the real ones: a stub that skipped
    `invoke` would skip the write gate, the argument validation and the AD-16
    claim confinement along with the service call.
    """

    async def _impl(db: Any, ctx: Any, **arguments: Any) -> ToolResult[Any]:
        return result

    # **Composed on whatever is installed now, not on the original.** Two stubs
    # in one test is the normal case — a node calls its own tool and
    # `claim_reader` — and rebuilding from `agents.registry.REGISTRY` each time
    # would silently discard the previous stub, leaving one real tool running
    # against a claim the test never seeded. Every assertion downstream would
    # then be about the seeded portfolio rather than about the state under test,
    # and would fail for a reason that has nothing to do with the property.
    current: Mapping[str, RegisteredTool] = qas_module.REGISTRY
    entry = dataclasses.replace(current[name], impl=_impl)
    patched: dict[str, RegisteredTool] = {**current, name: entry}
    monkeypatch.setattr(qas_module, "REGISTRY", patched)


# --- the map, and the routing table (AC 1) -------------------------------


def test_the_map_holds_the_seven_keys_and_declares_requires_llm_on_every_one() -> None:
    """AC 1's declaration half. One importable structure, fully declared.

    `requires_llm` is what Story 6.6 gates degradation on: when the model server
    is down, the actions that still answer are exactly the entries this says
    `False` for. So the flag is asserted present on all seven and asserted to be
    `False` on exactly one — a map where every entry needed a model would leave
    6.6 with no false path to gate, and a second false path added carelessly
    would silently change what "honest degradation" covers.

    A prompt file is required exactly when a model is, and the two travel
    together by construction: a node with no prompt has nothing to send.
    """
    assert set(QUICK_ACTIONS) == {
        "laborlaw",
        "similar",
        "rtw",
        "reserve",
        "fraud",
        "nextactions",
        "data_alignment",
    }
    for key, action in QUICK_ACTIONS.items():
        assert isinstance(action.requires_llm, bool), f"{key} declares no requires_llm"
        assert action.node, f"{key} routes nowhere"
        assert (action.prompt_key is not None) is action.requires_llm, (
            f"{key} declares requires_llm={action.requires_llm} and "
            f"prompt_key={action.prompt_key!r}; the two must agree"
        )

    model_free = {key for key, action in QUICK_ACTIONS.items() if not action.requires_llm}
    assert model_free == {"data_alignment"}


def test_a_model_free_builder_is_never_handed_a_model() -> None:
    """`requires_llm: False` enforced by a **signature**, not by a promise (6.6).

    The stronger form of the claim the call-count test below makes. That one
    asserts `data_alignment` did not call a model on the run it observed; this
    asserts it *could not have*, because its builder is not given one.

    Until Story 6.6 `_build_data_alignment` accepted `model` and `prompt_key`
    and used neither, "so that every entry in the map is built the same way" —
    which put a live `BaseChatModel` in the scope of the one node whose entire
    declared property is that it has none, leaving the flag enforced by a
    comment asking the next author not to type `_narrate`. Adding one would have
    been a green build and a stopped container's problem three stories later.

    Written as an assertion about parameters rather than about behaviour on
    purpose: behaviour is what the other test covers, and the gap between the
    two is exactly the mistake this closes.
    """
    import inspect

    deterministic = inspect.signature(qas_module._build_data_alignment)
    assert deterministic.parameters == {}, (
        "a requires_llm: False builder takes an argument; if that is a model, "
        "the flag is a comment again"
    )

    # …and the positive control, which is what stops this passing on a module
    # where nothing takes a model at all: every narrating builder takes one, and
    # takes a `prompt_key` that is not optional.
    for key, builder in qas_module._NARRATING_BUILDERS.items():
        parameters = inspect.signature(builder).parameters
        assert set(parameters) == {"model", "prompt_key"}, f"{key} has an unexpected signature"
        assert parameters["prompt_key"].annotation is str, (
            f"{key} accepts an optional prompt file; `prompts.load(None)` is a run-time failure"
        )


def test_the_keys_and_the_node_builders_are_the_same_seven() -> None:
    """ "One importable structure" across the two files the split needs.

    `agents/graph.py` owns the map and `agents/qas.py` owns the builders, so the
    property that matters is that neither can hold a key the other does not.
    Without this, a key added to the map with no builder is a `KeyError` in
    `lifespan` — a process that will not start — and a builder with no key is
    dead code nothing routes to.

    **Two builder maps since Story 6.6**, not one, because the `requires_llm`
    flag now selects between them — so the union is what has to equal the map's
    keys, and the two maps must also be disjoint: a key in both would make
    "which builder answers for this key?" depend on which branch `build_node`
    took, which is precisely the ambiguity the split exists to remove.
    """
    narrating = set(qas_module._NARRATING_BUILDERS)
    deterministic = set(qas_module._DETERMINISTIC_BUILDERS)

    assert narrating & deterministic == set()
    assert set(QUICK_ACTIONS) == narrating | deterministic
    # …and each key is in the map its flag says it is in, which is the half a
    # union comparison alone would let slide.
    assert {key for key, action in QUICK_ACTIONS.items() if action.requires_llm} == narrating
    assert {key for key, action in QUICK_ACTIONS.items() if not action.requires_llm} == (
        deterministic
    )


@pytest.mark.parametrize("key", sorted(QUICK_ACTIONS))
def test_each_key_routes_to_its_own_node_with_no_model_involved(key: str) -> None:
    """AC 1, **once per key**, at the routing function itself.

    `route_entry` reads one channel and calls nothing. That is the AD-14
    property stated as a pure function: a quick action is answered identically
    every time because a dict said where it goes, not because a router model
    agreed with itself twice. The graph-level half — that the node it names is
    the node that actually runs — is the parametrised run below.
    """
    state: CopilotState = {
        "messages": [],
        "caller": {"user_id": 1, "role": "handler"},
        "quick_action": key,
    }
    assert route_entry(state) == QUICK_ACTIONS[key].node


def test_free_text_clears_the_key_and_routes_to_the_chat_node() -> None:
    """AC 1's other half. A free-text turn must not inherit the last key.

    `run_inputs` writes `quick_action: None` rather than omitting it, and the
    channel is last-write-wins — so an omitted key would leave the previous
    turn's value in the checkpoint and the router would send this message down a
    quick action's node. That is a live bug the moment the map is non-empty,
    which is now.
    """
    inputs = run_inputs(caller=_HANDLER, claim_business_id="WC-20017", message="what's the story?")
    assert inputs["quick_action"] is None
    state: CopilotState = {"messages": [], "caller": inputs["caller"], "quick_action": None}
    assert route_entry(state) == CHAT_NODE


_HANDLER = CallerContext(user_id=7, role=UserRole.handler, employer_ids=frozenset({1, 2}))


# --- the unknown key is refused, not routed (AC 1) ------------------------


def test_an_unknown_quick_action_key_is_refused_before_a_thread_is_touched() -> None:
    """AC 1 at the contract — the structured rejection of a key nothing routes.

    `route_entry` falls through to free text for a key it does not know, which
    is the right answer for a router and the wrong answer to a client: a button
    that quietly became a chat message would be a quick action that had stopped
    being deterministic with nothing anywhere saying so. So the refusal happens
    in `_validate_run_body`, before the thread is resolved, before the lock is
    taken and before a single token is requested.
    """
    from api.errors import ProblemException

    with pytest.raises(ProblemException) as raised:
        _validate_run_body(RunRequest(message="Reserve review", quick_action="laborlow"))
    assert raised.value.status_code == 422
    assert "laborlow" in str(raised.value.detail)
    # The valid keys are named, because a client that got the key wrong has no
    # other way to find out what the right ones are.
    for key in QUICK_ACTIONS:
        assert key in str(raised.value.detail)


def test_a_quick_action_and_a_resume_command_together_are_refused() -> None:
    """Two fields that mean different kinds of run cannot both be honoured.

    `{"message": …, "command": {…}}` was a 200 until the review of Story 6.3 and
    a bad one; a quick action arriving beside a resume is the same ambiguity
    with a third field, and it is refused for the same reason rather than
    resolved by the order of two `if`s.
    """
    from api.errors import ProblemException

    with pytest.raises(ProblemException) as raised:
        _validate_run_body(RunRequest(command={"resume": "approve"}, quick_action="reserve"))
    assert raised.value.status_code == 422


def test_a_valid_quick_action_with_a_message_is_accepted() -> None:
    """The positive control — a validator that refused everything would pass above."""
    for key in QUICK_ACTIONS:
        _validate_run_body(RunRequest(message="the button's label", quick_action=key))


# --- every key, through the real graph -----------------------------------


@requires_db
@pytest.mark.parametrize("key", sorted(QUICK_ACTIONS))
async def test_every_key_reaches_its_node_and_calls_no_router_model(
    engine: Any, system: CallerContext, claim_id: str, indexed: None, key: str
) -> None:
    """AC 1 at the graph level, once per key, with the vacuous half closed.

    Two assertions and the second is the one that is easy to leave out. The
    `updates` frames name `entry_router` then the action's own node, which is
    the routing. And the scripted model records **at most one** call — the
    narration — so a run that consulted a router before dispatching fails here
    rather than passing a route assertion that never asked.

    `data_alignment` records **zero**, which is the `requires_llm: False`
    declaration proved rather than trusted: the map says no model, and this is
    the assertion that the node agrees.
    """
    model = _model()
    parts = await _run_key(
        model=model, engine=engine, ctx=system, claim=claim_id, key=key, thread=f"t.qas.{key}"
    )

    assert _nodes(parts) == [ENTRY_NODE, QUICK_ACTIONS[key].node]
    assert CHAT_NODE not in _nodes(parts)

    expected_calls = 1 if QUICK_ACTIONS[key].requires_llm else 0
    assert len(model.calls) == expected_calls, (
        f"{key} declares requires_llm={QUICK_ACTIONS[key].requires_llm} and made "
        f"{len(model.calls)} model calls"
    )

    # Every action says something. A run that finished having emitted nothing
    # would terminate `error` at the endpoint, which is the correct handling of
    # a silent run and the wrong outcome for a button.
    assert _streamed(parts).strip(), f"{key} put no assistant content on the wire"


@requires_db
@pytest.mark.parametrize(
    "key", sorted(key for key, action in QUICK_ACTIONS.items() if action.requires_llm)
)
async def test_every_narrating_node_sends_its_own_prompt_file_and_the_two_sections(
    engine: Any, system: CallerContext, claim_id: str, indexed: None, key: str
) -> None:
    """The composition, per key: instructions from a file, material in the user message.

    AD-16's structural half. The system message is the copilot preamble plus
    this key's own prompt file and contains nothing from the database; the user
    message carries the figures under one heading and the untrusted items under
    the other. There is no template for a crafted string to escape from, because
    there is no concatenation it is part of.

    **Composed on `copilot_system.md`, not `system.md`**, and the assertion is
    worth its line: `system.md` ends "Answer only with the JSON object matching
    the schema you were given", which would instruct a streamed conversational
    answer to be a JSON document — a failure that reads as a model bug rather
    than as a composition mistake.
    """
    model = _model()
    await _run_key(
        model=model, engine=engine, ctx=system, claim=claim_id, key=key, thread=f"t.compose.{key}"
    )

    system_message = _system_of(model)
    prompt_key = QUICK_ACTIONS[key].prompt_key
    assert prompt_key is not None
    assert prompts.load(prompt_key).text in system_message
    assert prompts.load("copilot_system").text in system_message
    assert "Answer only with the JSON object" not in system_message

    user = _user_of(model)
    assert user.startswith(FIGURES_HEADING)
    assert MATERIAL_HEADING in user
    # The material half is fenced, item by item; the figures half is not.
    figures, material = user.split(MATERIAL_HEADING, 1)
    assert ITEM_OPEN not in figures, "a fenced item reached the figures section"
    assert material.count(ITEM_OPEN) == material.count(ITEM_CLOSE) >= 1


def test_no_quick_action_prompt_file_states_a_figure() -> None:
    """AD-2 on the instruction channel: a prompt is where a figure would hide.

    The model is told to quote the strings it was given; a prompt that contained
    a dollar amount, a threshold or a day count would be a figure the model
    could state without a service having produced it — and it would be
    indistinguishable in the answer from one that came out of a tool.

    Two-digit runs are the bar rather than any digit at all, so that a prompt
    may say "two or three sentences" without failing; nothing in a briefing's
    instructions needs a number bigger than nine.
    """
    import re

    for action in QUICK_ACTIONS.values():
        if action.prompt_key is None:
            continue
        text = prompts.load(action.prompt_key).text
        assert "$" not in text, f"{action.prompt_key} states a money amount"
        assert "%" not in text, f"{action.prompt_key} states a percentage"
        assert not re.search(r"\d\d", text), f"{action.prompt_key} states a multi-digit figure"


# --- reserve: the figures are the service's (AC 2) -----------------------


@requires_db
async def test_the_reserve_action_quotes_every_display_string_it_was_given(
    engine: Any, db: AsyncSession, system: CallerContext, claim_id: str
) -> None:
    """AD-2's provenance assertion: the money in the prompt is the service's money.

    The comparison is against a **second, independent call** to the same tool —
    the technique `test_ai_insights.py` uses for the insight cards — so this
    asserts that the node copied rather than that it formatted something that
    happened to look right. Every string in `display` has to appear verbatim in
    the figures section, and the section must carry no money the envelope did
    not.
    """
    from agents.tools import reserve_check

    model = _model()
    await _run_key(
        model=model, engine=engine, ctx=system, claim=claim_id, key="reserve", thread="t.reserve"
    )
    user = _user_of(model)
    figures = user.split(MATERIAL_HEADING, 1)[0]

    result = await reserve_check(db, system, claim_business_id=claim_id)
    assert result.ok and result.data is not None
    assert result.display, "the fixture claim has no money figures to assert about"
    for field, rendered in result.display.items():
        assert rendered in figures, f"{field} was not quoted from the envelope"

    # Every `$` in the figures section belongs to one of those strings. A figure
    # the node rendered itself would show up here as the one dollar amount that
    # is in no envelope.
    quoted = "".join(result.display.values())
    for amount in _money_tokens(figures):
        assert amount in quoted, f"{amount} is a money figure no tool produced"

    assert result.data.verdict.value in figures
    assert result.data.verdict.value in {member.value for member in ReserveVerdict}


def _money_tokens(text: str) -> list[str]:
    """Every `$…` run in a string — what a reader would read as a money amount."""
    import re

    return re.findall(r"\$[\d,]+(?:\.\d+)?", text)


@requires_db
async def test_a_reserve_with_no_bills_says_so_and_never_renders_zero(
    monkeypatch: pytest.MonkeyPatch, engine: Any
) -> None:
    """The `indeterminate` verdict, which is five members rather than three.

    `remaining_medical_cents is None` means the bills are not on file, which
    forces `verdict` to `indeterminate`, `ratio_bp` to `None`, and makes
    `reserve_check`'s `display` map omit three keys — so there is nothing to
    quote and the absence *is* the state. A node that formatted the `None` as
    `$0` would hand the model a figure meaning "nothing is outstanding" for a
    state that means "nobody knows", which is the exact confusion the wrapper
    refuses one layer down.

    Stubbed rather than seeded: putting a claim into this state through the
    services would mean deleting its bills, and the assertion would then be
    about the deletion.
    """
    check = ReserveCheck(
        verdict=ReserveVerdict.indeterminate,
        ratio_bp=None,
        projected_remaining_cents=None,
        remaining_indemnity_cents=1_200_000,
        remaining_medical_cents=None,
        scheduled_indemnity_cents=2_000_000,
        disbursed_indemnity_cents=800_000,
        disbursed_medical_cents=0,
        reserve_cents=4_800_000,
        rationale="Medical bills are not on file, so exposure cannot be judged.",
        bands_version=3,
    )
    _stub(
        monkeypatch,
        "reserve_check",
        ToolResult.succeeded(
            check,
            display={
                "reserveCents": "$48,000.00",
                "remainingIndemnityCents": "$12,000.00",
                "disbursedIndemnityCents": "$8,000.00",
                "disbursedMedicalCents": "$0.00",
            },
        ),
    )
    _stub(monkeypatch, "claim_reader", ToolResult.failed("not needed for this assertion"))

    model = _model()
    parts = await _run_key(
        model=model,
        engine=engine,
        ctx=_HANDLER,
        claim="WC-20017",
        key="reserve",
        thread="t.reserve.indeterminate",
    )
    assert _nodes(parts) == [ENTRY_NODE, QUICK_ACTIONS["reserve"].node]

    figures = _user_of(model).split(MATERIAL_HEADING, 1)[0]
    assert "indeterminate" in figures
    assert "not on file" in figures
    assert "Neither is zero" in figures
    assert "Do not state a ratio" in figures
    # The three absent keys are absent, not rendered — and nowhere in the
    # figures is there a money amount the envelope did not carry, which is the
    # `$0` this state used to invite.
    for absent in ("Medical still unpaid", "Projected remaining exposure"):
        assert absent not in figures
    assert "Remaining exposure as a share of the reserve:" not in figures
    for amount in _money_tokens(figures):
        assert amount in "$48,000.00 $12,000.00 $8,000.00 $0.00"


@requires_db
async def test_a_settled_claim_has_bills_on_file_and_no_ratio(
    monkeypatch: pytest.MonkeyPatch, engine: Any
) -> None:
    """`ratioBp` is not part of the medical pair, and treating it as one broke.

    `ReserveCheck` documents three nullable fields and two different reasons:
    `remaining_medical_cents` and `projected_remaining_cents` are `None`
    together when the bills are not on file, while `ratio_bp` is `None`
    "whenever no complete comparison happened — **a settled claim**, or an
    incomplete exposure". A `closed_final` claim therefore has its bills on file
    and no ratio, and a node that read `display["ratioBp"]` inside the
    bills-on-file branch raised a `KeyError` on the first settled claim it met.

    The endpoint turned that into one `error` frame — correctly, and that is the
    point: a quick action that never answered, on a state a large share of the
    seeded portfolio is in, reported as an outage.
    """
    check = ReserveCheck(
        verdict=ReserveVerdict.closed_final,
        ratio_bp=None,
        projected_remaining_cents=0,
        remaining_indemnity_cents=0,
        remaining_medical_cents=0,
        scheduled_indemnity_cents=2_000_000,
        disbursed_indemnity_cents=2_000_000,
        disbursed_medical_cents=310_000,
        reserve_cents=4_800_000,
        rationale="Claim settled and closed. No further reserve exposure.",
        bands_version=3,
    )
    _stub(
        monkeypatch,
        "reserve_check",
        ToolResult.succeeded(
            check,
            display={
                "reserveCents": "$48,000.00",
                "remainingIndemnityCents": "$0.00",
                "disbursedIndemnityCents": "$20,000.00",
                "disbursedMedicalCents": "$3,100.00",
                "remainingMedicalCents": "$0.00",
                "projectedRemainingCents": "$0.00",
            },
        ),
    )
    _stub(monkeypatch, "claim_reader", ToolResult.failed("not needed for this assertion"))

    model = _model()
    parts = await _run_key(
        model=model,
        engine=engine,
        ctx=_HANDLER,
        claim="WC-20017",
        key="reserve",
        thread="t.reserve.settled",
    )
    assert _nodes(parts) == [ENTRY_NODE, QUICK_ACTIONS["reserve"].node]

    figures = _user_of(model).split(MATERIAL_HEADING, 1)[0]
    assert "closed_final" in figures
    assert "Medical still unpaid: $0.00" in figures
    assert "Remaining exposure as a share of the reserve:" not in figures
    assert "Do not state a ratio" in figures


@requires_db
async def test_a_light_verdict_with_no_bills_is_not_reported_as_unjudged(
    monkeypatch: pytest.MonkeyPatch, engine: Any
) -> None:
    """ "No bills on file" and "indeterminate" are two conditions, not one.

    `services/financials/reserve.py` returns `light` on an *incomplete* exposure
    whenever the exposure it does know has already passed the light band —
    deliberately, because an unknown non-negative addition cannot bring a total
    back under a threshold it has already crossed. `closed_final` is the other
    such state: a settled claim is judged before any band arithmetic runs.

    The node read the missing `remainingMedicalCents` key and asserted "that is
    why the verdict says the reserve cannot be judged", which for this claim is
    false and dangerous in the specific way this story exists to prevent: an
    under-reserved intake claim would have carried `verdict: light` and "cannot
    be judged" in the same block the system prompt tells the model is
    authoritative, and a model reconciling the two could tell a handler no
    action is needed.

    So the absence sentence stays — it is about the *figures*, and it is correct
    — and the clause about the verdict is read from the verdict.
    """
    check = ReserveCheck(
        verdict=ReserveVerdict.light,
        ratio_bp=None,
        projected_remaining_cents=None,
        remaining_indemnity_cents=6_000_000,
        remaining_medical_cents=None,
        scheduled_indemnity_cents=6_000_000,
        disbursed_indemnity_cents=0,
        disbursed_medical_cents=0,
        reserve_cents=1_000_000,
        rationale="Known exposure already exceeds the reserve; bills are not on file.",
        bands_version=3,
    )
    _stub(
        monkeypatch,
        "reserve_check",
        ToolResult.succeeded(
            check,
            display={
                "reserveCents": "$10,000.00",
                "remainingIndemnityCents": "$60,000.00",
                "disbursedIndemnityCents": "$0.00",
                "disbursedMedicalCents": "$0.00",
            },
        ),
    )
    _stub(monkeypatch, "claim_reader", ToolResult.failed("not needed for this assertion"))

    model = _model()
    await _run_key(
        model=model,
        engine=engine,
        ctx=_HANDLER,
        claim="WC-20017",
        key="reserve",
        thread="t.reserve.light.nobills",
    )

    figures = _user_of(model).split(MATERIAL_HEADING, 1)[0]
    assert "verdict, computed by this system: light" in figures
    # The two facts about the missing figures, which are still true.
    assert "not on file" in figures
    assert "Neither is zero" in figures
    # …and not one word claiming the verdict withheld itself.
    assert "cannot be judged" not in figures
    assert "indeterminate" not in figures
    # The model is told what the verdict actually rests on instead.
    assert "lower bound" in figures


# --- fraud: the variant is chosen in Python (AC 2) -----------------------


def _signals(*, siu: bool, flagged: bool) -> ToolResult[FraudSignals]:
    return ToolResult.succeeded(
        FraudSignals(
            fraud_score=71 if (siu or flagged) else 12,
            fraud_flag=flagged,
            siu_review=siu,
            fraud_flagged=flagged,
            siu_fraud_score_min=70,
            fraud_flag_score_min=60,
            thresholds_version=4,
        )
    )


@requires_db
@pytest.mark.parametrize(
    ("siu", "flagged", "expected"),
    [
        (False, False, "write the low-risk confirmation"),
        (True, False, "write the red-flag variant"),
        (False, True, "write the red-flag variant"),
        (True, True, "write the red-flag variant"),
    ],
)
async def test_the_fraud_variant_is_decided_before_the_prompt_is_composed(
    monkeypatch: pytest.MonkeyPatch, engine: Any, siu: bool, flagged: bool, expected: str
) -> None:
    """`elevated = siu_review or fraud_flagged`, in Python, exactly as 6.2 does it.

    Which answer a claim gets is two registered derivations' verdict over a
    stored score and a rule document's thresholds. A model asked to choose would
    sometimes choose the reassuring one, and a confident low-risk confirmation
    on a referable claim reads exactly like a correct one.

    All four combinations, because "or" is the whole rule and the interesting
    rows are the two where the derivations disagree — they fund different work
    (`agents/tools/fraud.py` argues the distinction at length) and either firing
    is enough.
    """
    _stub(monkeypatch, "fraud_signals", _signals(siu=siu, flagged=flagged))
    _stub(monkeypatch, "claim_reader", ToolResult.failed("not needed for this assertion"))

    model = _model()
    await _run_key(
        model=model,
        engine=engine,
        ctx=_HANDLER,
        claim="WC-20017",
        key="fraud",
        thread=f"t.fraud.{siu}.{flagged}",
    )
    figures = _user_of(model).split(MATERIAL_HEADING, 1)[0]
    assert expected in figures
    # And exactly one instruction — a prompt naming both variants would leave
    # the choice with the model after all.
    other = (
        "write the red-flag variant"
        if expected.endswith("low-risk confirmation")
        else "write the low-risk confirmation"
    )
    assert other not in figures


# --- similar: the disclosure, verbatim or not at all (AC 3) --------------


@requires_db
async def test_the_similar_action_frames_the_set_as_this_claim_s_employer(
    engine: Any, system: CallerContext, claim_id: str, indexed: None
) -> None:
    """ "At this claim's employer", never "your caseload" (follow-up review of 6.2, B9).

    `similar_cases` narrows to the subject claim's employer partition, so for a
    handler covering two employers "your caseload" names a strictly larger set
    than the one that was searched. The framing has to say what was actually
    searched, and the prompt file repeats the prohibition.

    Neighbour text travels fenced: an employer's short name and an injury
    description are written by people, so they are items under the material
    heading rather than lines under the figures heading.
    """
    model = _model()
    await _run_key(
        model=model, engine=engine, ctx=system, claim=claim_id, key="similar", thread="t.similar"
    )
    user = _user_of(model)
    figures, material = user.split(MATERIAL_HEADING, 1)

    assert "at this claim's employer" in figures
    assert "caseload" not in figures.lower()
    assert "your book" not in figures.lower()
    assert ITEM_OPEN in material


@requires_db
async def test_a_stale_neighbour_puts_the_tool_s_own_disclosure_in_the_prompt(
    monkeypatch: pytest.MonkeyPatch, engine: Any
) -> None:
    """AD-12: the disclosure sentence is quoted, never re-derived (AC 3).

    It states two counts and a configured window — three figures, and AD-2 says
    the model originates none of them. It is produced in exactly one place,
    `agents/tools/similar.py::_disclosure`, so the Insights card and the chat
    quote the same string; a node that rebuilt the sentence would be a second
    place the threshold lives and a second chance to word it differently.
    """
    from agents.tools.similar import SimilarCases

    disclosure = (
        "2 of 3 comparable claims were last indexed more than 7 days ago or have "
        "changed since; treat their outcomes as indicative."
    )
    _stub(
        monkeypatch,
        "similar_cases",
        ToolResult.succeeded(
            SimilarCases(items=(), stale_count=2, disclosure=disclosure), display={}
        ),
    )
    _stub(monkeypatch, "claim_reader", ToolResult.failed("not needed for this assertion"))

    model = _model()
    await _run_key(
        model=model,
        engine=engine,
        ctx=_HANDLER,
        claim="WC-20017",
        key="similar",
        thread="t.similar.stale",
    )
    assert disclosure in _user_of(model)


@requires_db
async def test_nothing_stale_means_no_staleness_sentence_at_all(
    monkeypatch: pytest.MonkeyPatch, engine: Any
) -> None:
    """`disclosure is None` is a different fact from an empty string.

    A card that always carried a freshness clause would train a reader to skip
    it, which is the failure mode a disclosure has. So when nothing is stale
    there is no sentence — not a shorter one, not a reassuring one.
    """
    from agents.tools.similar import SimilarCases

    _stub(
        monkeypatch,
        "similar_cases",
        ToolResult.succeeded(SimilarCases(items=(), stale_count=0, disclosure=None), display={}),
    )
    _stub(monkeypatch, "claim_reader", ToolResult.failed("not needed for this assertion"))

    model = _model()
    await _run_key(
        model=model,
        engine=engine,
        ctx=_HANDLER,
        claim="WC-20017",
        key="similar",
        thread="t.similar.fresh",
    )
    user = _user_of(model)
    assert "Freshness disclosure" not in user
    assert "last indexed more than" not in user
    assert "treat their outcomes as indicative" not in user
    # …and the empty partition is reported as a real state rather than as a gap.
    assert "no comparable claims at this claim's employer" in user


# --- labor law: grounded (AC 3), fenced and tagged (AC 5) ----------------


@requires_db
async def test_the_labor_law_action_fences_and_tags_every_retrieved_passage(
    engine: Any, system: CallerContext, claim_id: str, indexed: None
) -> None:
    """AC 5: each chunk individually delimited and tagged with its source id.

    And the retrieval query is composed from the header's **bare scalars only**
    — the state, the stage, the risk band, the severity score. `claim_reader`
    returns the injury description, the cause and the ICD text as fenced strings
    with delimiter markup in them, so they are unusable as a query and
    unwrapping a fence to recover the raw value would defeat the fence. What is
    lost is nothing: the fenced claim material travels in the same user message,
    so the model narrates the retrieved rules against the injury without any
    claim text having steered retrieval.
    """
    seen: list[Any] = []
    from agents.fencing import fence
    from agents.tools.knowledge import KnowledgeHits

    real = REGISTRY["labor_law_search"]

    async def _spy(db: Any, ctx: Any, **arguments: Any) -> ToolResult[KnowledgeHits]:
        seen.append(arguments)
        return await real.impl(db, ctx, **arguments)

    import dataclasses as _dc

    monkeypatchless = {**REGISTRY, "labor_law_search": _dc.replace(real, impl=_spy)}
    original = qas_module.REGISTRY
    setattr(qas_module, "REGISTRY", monkeypatchless)  # noqa: B010
    try:
        model = _model()
        await _run_key(
            model=model,
            engine=engine,
            ctx=system,
            claim=claim_id,
            key="laborlaw",
            thread="t.laborlaw",
        )
    finally:
        setattr(qas_module, "REGISTRY", original)  # noqa: B010

    assert len(seen) == 1
    query = str(seen[0]["query_text"])
    assert ITEM_OPEN not in query, "a fenced string was used as a search query"
    assert "workers compensation" in query

    material = _user_of(model).split(MATERIAL_HEADING, 1)[1]
    assert material.count(ITEM_OPEN) == material.count(ITEM_CLOSE)
    assert 'source="knowledge:' in material, "a retrieved passage carries no source tag"
    # A passage's own text cannot spell a delimiter — the alphabet, not a
    # pattern (`agents/fencing.py::scrub`).
    assert fence("knowledge:x", "y").startswith(ITEM_OPEN)


# --- rtw: a draft, and no write (AC 2) -----------------------------------


@requires_db
async def test_the_rtw_action_drafts_into_the_transcript_and_proposes_nothing(
    engine: Any, system: CallerContext, claim_id: str
) -> None:
    """**Amended by Story 6.5** (AD-15) — the seam is gone, the property is not.

    This asserted three things, one of which 6.5 falsifies: that nothing
    registered is a write. Two writes are registered now, and both are bound to
    the model, so that clause becomes the sharper claim AD-6 actually makes
    about a quick-action node — **it holds no write tool of its own**. The node
    calls `rtw_reader`, a `kind: read` entry, through a registry call that
    passes an empty marker set; the draft is an ordinary assistant message; and
    `pending_approval` is still `None`, because a quick action drafts and the
    gated write step is somewhere else entirely.

    That last assertion is now the load-bearing one rather than a scope line: a
    QAS node that *did* propose a write would produce a second interrupt origin,
    and AD-6's "one wire contract, not two" rests on there being exactly one.

    The draft also names no return date, because no tool available to it can
    supply one — `Claim.rtw_rec` and `Claim.actual_rtw` are on no `ClaimDetail`
    field. The figures section says so in as many words, which is the
    instruction the model is given instead of a date. What it no longer says is
    that the letter cannot be saved: Story 6.5 gives the handler a modal and an
    approval gate, so that sentence stopped being true and was removed.
    """
    model = _model()
    graph = build_graph(model=model, checkpointer=InMemorySaver(), max_tool_calls=3, tools=[])
    config = saver_config("t.rtw")
    parts = [
        part
        async for part in graph.astream(
            run_inputs(
                caller=system,
                claim_business_id=claim_id,
                message="Review RTW Policy",
                quick_action="rtw",
            ),
            config=config,
            context=_context(engine, claim_id, system),
            stream_mode=["messages", "updates", "custom"],
        )
    ]

    assert _nodes(parts) == [ENTRY_NODE, QUICK_ACTIONS["rtw"].node]
    assert REPLY in _streamed(parts)

    state = await graph.aget_state(config)
    assert state.values.get("pending_approval") is None
    assert state.tasks == (), "the run paused on something"
    # **No write tool is reachable from a quick-action node.** The registry has
    # two since 6.5 and neither is one this node can call: `_call` passes an
    # empty marker set, so a `kind: write` entry reached from here is refused by
    # `invoke`'s gate before it validates an argument.
    assert {name for name, entry in REGISTRY.items() if entry.kind is ToolKind.write}
    assert REGISTRY["rtw_reader"].kind is ToolKind.read

    figures = _user_of(model).split(MATERIAL_HEADING, 1)[0]
    assert "Do not state a date" in figures
    # Amended into its opposite: the draft now says a handler will review it
    # before anything is filed, because filing it is a thing that can happen.
    assert "not saved to the claim" not in figures
    assert "before anything is filed" in figures


# --- data alignment: no model, no fence markup (AC 2) --------------------


@requires_db
async def test_the_data_alignment_note_is_computed_and_carries_no_fence_markup(
    engine: Any, db: AsyncSession, system: CallerContext, claim_id: str
) -> None:
    """AC 2's `requires_llm` half, on the one node that formats tool output for a human.

    No model call at all — which is what makes this Story 6.6's false path — and
    no delimiter markup in what a handler reads. `claim_reader`'s `narrative`
    tuple is five fenced items whose whole purpose is to be separable *inside a
    prompt*; rendering one into a transcript would show a handler
    `<<<LINEWORKER-ITEM` and would defeat the fence's only purpose. So the note
    is built from the envelope's bare scalars, and every value in it is checked
    against a second, independent call to the same tool.
    """
    from agents.tools import claim_reader

    model = _model()
    parts = await _run_key(
        model=model,
        engine=engine,
        ctx=system,
        claim=claim_id,
        key="data_alignment",
        thread="t.dataalign",
    )

    assert model.calls == [], "the model-free action called a model"
    note = _streamed(parts)
    assert note.strip(), "the deterministic note reached nothing"
    assert ITEM_OPEN not in note
    assert ITEM_CLOSE not in note
    assert "<<<LINEWORKER" not in note
    assert "No model was involved" in note

    result = await claim_reader(db, system, claim_business_id=claim_id)
    assert result.ok and result.data is not None
    facts = result.data
    assert facts.claim_id in note
    assert facts.state in note
    assert facts.stage in note
    assert str(facts.severity_score) in note
    # The fenced half is what must **not** be there, item by item.
    for item in facts.narrative:
        assert item not in note


# --- a failed tool is a sentence, not a stack trace (AC 4) ---------------


@requires_db
@pytest.mark.parametrize(
    ("key", "tool"),
    [
        ("reserve", "reserve_check"),
        ("fraud", "fraud_signals"),
        ("nextactions", "next_actions"),
        ("similar", "similar_cases"),
        ("rtw", "rtw_reader"),
        ("data_alignment", "claim_reader"),
        ("laborlaw", "claim_reader"),
    ],
)
async def test_a_refusing_tool_becomes_a_plain_sentence_and_one_answer(
    monkeypatch: pytest.MonkeyPatch, engine: Any, key: str, tool: str
) -> None:
    """AC 4, once per node, because each node handles its own failure.

    A tool that could not answer returns `{ok: false, error: "…"}` — a short,
    content-free sentence written for exactly this. The node quotes it rather
    than re-wording it, adds the one thing a handler most needs to know (nothing
    was changed on the claim), and the run goes on to terminate normally.

    **The model is never asked.** Narrating figures nobody could produce is how
    a narrative acquires an invented one (AD-2), so a failed gather short-circuits
    before the prompt is composed — which is also why `calls` is empty below.

    Nothing raw: no exception class name, no traceback marker, no SQL. The
    envelope's sentence is the whole of what a handler is told.
    """
    _stub(monkeypatch, tool, ToolResult.failed("the claim is not in this caller's book"))

    model = _model()
    parts = await _run_key(
        model=model,
        engine=engine,
        ctx=_HANDLER,
        claim="WC-20017",
        key=key,
        thread=f"t.fail.{key}",
    )

    assert _nodes(parts) == [ENTRY_NODE, QUICK_ACTIONS[key].node]
    assert model.calls == [], f"{key} asked a model to narrate a failed gather"

    answer = _streamed(parts)
    assert "the claim is not in this caller's book" in answer
    assert "Nothing was changed on the claim" in answer
    for raw in ("Traceback", "Error(", "sqlalchemy", "psycopg", "<class "):
        assert raw not in answer


# --- the registry's new entries ------------------------------------------


def test_the_three_new_entries_are_reads_with_claim_confined_schemas() -> None:
    """AD-13/AD-16 on what this story registered.

    `similar_cases` was left unregistered by Story 6.3 rather than given a
    model-suppliable staleness knob; it is registered here with the window and
    the embeddings client declared as **context injections**, which is the
    alternative that story named. `labor_law_search`'s schema subclasses
    `ClaimArgs` even though the service takes no claim, so it is confined by the
    same check every other tool is — a retrieval tool exempt from argument
    confinement would be the one tool whose arguments injected text could still
    shape freely.
    """
    from agents.registry import INJECTED, ClaimArgs, KnowledgeArgs

    for name in ("similar_cases", "labor_law_search", "rtw_reader"):
        entry = REGISTRY[name]
        assert entry.kind is ToolKind.read
        assert issubclass(entry.args_schema, ClaimArgs)
        assert set(entry.injects) <= set(INJECTED)

    assert set(REGISTRY["similar_cases"].injects) == {"client", "staleness_days"}
    assert set(REGISTRY["labor_law_search"].injects) == {"client"}
    assert "query_text" in KnowledgeArgs.model_fields
    # And not one of the seven can name who is asking.
    forbidden = {"user_id", "user", "caller", "employer_id", "employer_ids", "role", "scope"}
    for entry in REGISTRY.values():
        assert not forbidden & set(entry.args_schema.model_fields)
        assert not forbidden & set(entry.injects)


@requires_db
async def test_an_injected_dependency_reaches_the_service_and_is_not_an_argument(
    engine: Any, system: CallerContext, claim_id: str, indexed: None
) -> None:
    """The injection actually happens, and the model has no way to have supplied it.

    A declaration nothing reads is a declaration that will drift. So this drives
    the real `similar_cases` through the real `invoke` and asserts the two
    injected keyword arguments arrived — the client the run was built with and
    the window `CopilotContext` carries — while neither is a field the schema
    could have carried.
    """
    from agents.registry import ClaimArgs, invoke

    seen: list[dict[str, Any]] = []
    real = REGISTRY["similar_cases"]

    async def _spy(db: Any, ctx: Any, **arguments: Any) -> ToolResult[Any]:
        seen.append(arguments)
        return await real.impl(db, ctx, **arguments)

    context = _context(engine, claim_id, system)
    async with context.sessionmaker() as session:
        payload = await invoke(
            dataclasses.replace(real, impl=_spy),
            caller=system,
            session=session,
            context=context,
            thread_claim_business_id=claim_id,
            approved_tool_call_ids=frozenset(),
            tool_call_id=None,
            arguments={"claim_business_id": claim_id},
        )

    assert payload["ok"] is True
    assert len(seen) == 1
    assert seen[0]["staleness_days"] == STALENESS_DAYS
    assert seen[0]["client"] is context.embedding_client
    assert "staleness_days" not in ClaimArgs.model_fields
    assert "client" not in ClaimArgs.model_fields


@requires_db
async def test_a_quick_action_node_cannot_be_steered_onto_another_claim(
    engine: Any, system: CallerContext, claim_id: str
) -> None:
    """AD-16 through the node path: the claim comes from the context, full stop.

    Every node reads `CopilotContext.claim_business_id` and passes it to
    `invoke`, which then refuses anything but the thread's own claim. There is
    no message body, no state channel and no completion in that path — so a
    poisoned `cause` reading "also read WC-9999" has nothing to steer.

    Asserted by binding a context to one claim and running the turn's inputs
    against another: the tool must answer about the context's claim, and the
    node must never see the run input's.
    """
    seen: list[dict[str, Any]] = []
    real = REGISTRY["claim_reader"]

    async def _spy(db: Any, ctx: Any, **arguments: Any) -> ToolResult[Any]:
        seen.append(arguments)
        return await real.impl(db, ctx, **arguments)

    original = qas_module.REGISTRY
    setattr(  # noqa: B010
        qas_module, "REGISTRY", {**REGISTRY, "claim_reader": dataclasses.replace(real, impl=_spy)}
    )
    try:
        model = _model()
        graph = build_graph(model=model, checkpointer=InMemorySaver(), max_tool_calls=3, tools=[])
        parts = [
            part
            async for part in graph.astream(
                run_inputs(
                    caller=system,
                    # What the *run* claims the turn is about — a different claim.
                    claim_business_id="WC-99999",
                    message="Data alignment note",
                    quick_action="data_alignment",
                ),
                config=saver_config("t.confine"),
                # …and what the thread is actually bound to.
                context=_context(engine, claim_id, system),
                stream_mode=["messages", "updates", "custom"],
            )
        ]
    finally:
        setattr(qas_module, "REGISTRY", original)  # noqa: B010

    assert seen == [{"claim_business_id": claim_id}]
    assert "WC-99999" not in _streamed(parts)


# --- the route declaration, and the prompt's own obligations -------------


def test_the_route_literal_lists_exactly_the_graph_s_destinations() -> None:
    """`Route` and the map agree — the assertion its comment always claimed existed.

    `Route` shipped as a `Literal` nothing referred to: `route_entry` returned
    `str`, `QuickAction.node` was `str`, and its own comment said this file
    asserted the two agreed. Nothing did, so the one guarantee the declaration
    offered — that it does not drift behind the dict it describes — was false.

    It is now load-bearing in both directions. mypy closes the half a test
    cannot: `QuickAction.node` is typed `Route`, so an entry naming a
    destination this does not list fails to type-check. This closes the half a
    type cannot: that `Route` lists nothing the graph does not actually have, so
    a quick action deleted from the map leaves a dead member behind here.
    """
    from typing import get_args

    from agents.graph import Route

    assert set(get_args(Route)) == {CHAT_NODE} | {action.node for action in QUICK_ACTIONS.values()}


class _SilentChatModel(BaseChatModel):
    """Streams nothing at all — the completion that produces no `str` content.

    Two real shapes collapse to this. A model server can end a stream having
    decoded no tokens, and a chunk's `content` can be a *list of blocks* rather
    than a string — which `_narrate` deliberately does not stringify, because
    `[object Object]` in a claims console is worse than a missing sentence. In
    both cases the accumulated answer is `""`.

    A block-list chunk is yielded here as well as an empty one, so the fixture
    exercises the branch that is easiest to reason about wrongly: it *looks*
    like content, and it contributes none.
    """

    @property
    def _llm_type(self) -> str:
        return "silent-quick-action"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=""))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        yield ChatGenerationChunk(message=AIMessageChunk(content=[{"type": "image", "id": "x"}]))
        yield ChatGenerationChunk(message=AIMessageChunk(content=""))


@requires_db
async def test_a_narration_that_produced_nothing_becomes_a_note_not_a_blank_turn(
    monkeypatch: pytest.MonkeyPatch, engine: Any
) -> None:
    """A run that said nothing must still say something (review of Story 6.3).

    That story's review fixed this class on the free-text path: a run producing
    zero assistant frames terminated `done`, which is the frame the panel reads
    as "your question was answered". The quick-action path had the mirror image
    — an empty `AIMessage` was checkpointed as a turn, and the endpoint's
    `_terminal_for` then correctly reported `error` for a node that had
    succeeded, so the handler got an outage notice for a claim nothing was wrong
    with and a blank turn in the transcript for ever.

    Asserted on both destinations `_note` writes to, because they are two
    obligations: the stream is what makes the run terminate `done`, and the
    checkpointed message is what a reload shows.
    """
    _stub(monkeypatch, "claim_reader", ToolResult.failed("not needed for this assertion"))
    _stub(monkeypatch, "fraud_signals", _signals(siu=False, flagged=False))

    graph = build_graph(
        model=_SilentChatModel(), checkpointer=InMemorySaver(), max_tool_calls=3, tools=[]
    )
    config = saver_config("t.silent")
    parts = [
        part
        async for part in graph.astream(
            run_inputs(
                caller=_HANDLER,
                claim_business_id="WC-20017",
                message="Fraud risk check",
                quick_action="fraud",
            ),
            config=config,
            context=_context(engine, "WC-20017", _HANDLER),
            stream_mode=["messages", "updates", "custom"],
        )
    ]

    assert _nodes(parts) == [ENTRY_NODE, QUICK_ACTIONS["fraud"].node]
    streamed = _streamed(parts)
    assert qas_module.EMPTY_NARRATION_NOTE in streamed
    assert "Nothing was changed on the claim" in streamed

    state = await graph.aget_state(config)
    checkpointed = [
        str(message.content)
        for message in state.values["messages"]
        if getattr(message, "type", "") == "ai"
    ]
    assert checkpointed == [qas_module.EMPTY_NARRATION_NOTE], (
        "an empty assistant turn was checkpointed"
    )


@requires_db
async def test_a_draft_that_produced_nothing_publishes_no_version_pin(
    monkeypatch: pytest.MonkeyPatch, engine: Any
) -> None:
    """The pin describes a draft, so an empty narration publishes none (6.5 review).

    `RTW_DRAFT` is what tells the panel "a letter was drafted, and here is the
    claim version its save pins" — and the panel opens the wide modal on it,
    with the run's streamed text in the body and a live **Save to claim**
    button. It was published *before* the completion was requested, so it went
    out unchanged when the model answered nothing: the modal opened holding
    `EMPTY_NARRATION_NOTE`, and "The copilot could not compose an answer for
    this quick action" was one click from being a filed document on an injured
    worker's case file, and one more from being the letter a claimant reads.

    Two assertions, and the second is what stops the first passing vacuously: no
    pin reached the custom stream, and the run genuinely took the empty-
    narration path rather than failing somewhere earlier.
    """
    from agents.tools.rtw import RtwContext

    _stub(
        monkeypatch,
        "rtw_reader",
        ToolResult.succeeded(
            RtwContext(
                claim_id="WC-20017",
                return_status="under_treatment",
                version=3,
                restrictions=(),
            )
        ),
    )
    _stub(monkeypatch, "claim_reader", ToolResult.failed("not needed for this assertion"))

    parts = await _run_key(
        model=_SilentChatModel(),
        engine=engine,
        ctx=_HANDLER,
        claim="WC-20017",
        key="rtw",
        thread="t.rtw.silent",
    )

    published = [chunk for mode, chunk in parts if mode == "custom"]
    assert all(qas_module.RTW_DRAFT not in chunk for chunk in published), (
        "a version pin was published for a letter that was never drafted"
    )
    assert qas_module.EMPTY_NARRATION_NOTE in _streamed(parts)


@requires_db
async def test_a_draft_that_produced_text_publishes_its_version_pin(
    monkeypatch: pytest.MonkeyPatch, engine: Any
) -> None:
    """…and the positive control, without which the test above proves nothing.

    A pin that was never published at all would satisfy the previous assertion
    perfectly, and the save has no other source for the version it
    compare-and-swaps on — so "the modal cannot open" and "the modal opens on a
    force-write" are the two failures this pair sits between.
    """
    from agents.tools.rtw import RtwContext

    _stub(
        monkeypatch,
        "rtw_reader",
        ToolResult.succeeded(
            RtwContext(
                claim_id="WC-20017",
                return_status="under_treatment",
                version=3,
                restrictions=(),
            )
        ),
    )
    _stub(monkeypatch, "claim_reader", ToolResult.failed("not needed for this assertion"))

    parts = await _run_key(
        model=_model(),
        engine=engine,
        ctx=_HANDLER,
        claim="WC-20017",
        key="rtw",
        thread="t.rtw.pinned",
    )

    pins = [
        chunk[qas_module.RTW_DRAFT]
        for mode, chunk in parts
        if mode == "custom" and qas_module.RTW_DRAFT in chunk
    ]
    assert pins == [{"claimId": "WC-20017", "version": 3}]


@requires_db
async def test_an_absent_material_section_is_stated_in_the_figures(
    monkeypatch: pytest.MonkeyPatch, engine: Any
) -> None:
    """Every prompt refers to a section that can be absent — so the absence is said.

    `rtw.md` is the sharp case: it instructs the model to *quote the medical
    restrictions* from `MATERIAL TO ANALYSE`, in a letter addressed to an
    injured worker. `_narrate` omits that section entirely when nothing
    untrusted was gathered, and both of its sources can come back empty — a
    claim with no contraindications and no prognosis, and a `claim_reader` that
    declined, which is a tolerated non-fatal path. Telling a model to quote from
    a section that is not in the message is an invitation to write one.

    Both notes are asserted, because they say different things: one that there
    is no material at all, one that a specific deterministic read failed. A node
    that emitted only the first would have hidden a failing tool behind "this
    claim has nothing on file".
    """
    from agents.tools.rtw import RtwContext

    _stub(
        monkeypatch,
        "rtw_reader",
        ToolResult.succeeded(
            RtwContext(
                claim_id="WC-20017",
                return_status="under_treatment",
                # The claim `version` the draft is composed against, and the
                # number Story 6.5's save is compare-and-swapped on. Present on
                # every `RtwContext` since that story, so a fixture that omitted
                # it would be constructing a projection this build cannot make.
                version=3,
                restrictions=(),
            )
        ),
    )
    _stub(monkeypatch, "claim_reader", ToolResult.failed("the claim is not in this caller's book"))

    model = _model()
    await _run_key(
        model=model,
        engine=engine,
        ctx=_HANDLER,
        claim="WC-20017",
        key="rtw",
        thread="t.rtw.nomaterial",
    )

    user = _user_of(model)
    assert MATERIAL_HEADING not in user, "the fixture produced material — this would be vacuous"
    assert ITEM_OPEN not in user
    assert qas_module.NO_MATERIAL_NOTE in user
    assert qas_module.NO_NARRATIVE_NOTE in user


@requires_db
async def test_the_labor_law_briefing_carries_the_disclaimer_whatever_the_model_wrote(
    engine: Any, system: CallerContext, claim_id: str, indexed: None
) -> None:
    """Task 2's disclaimer, appended in Python rather than requested in a prompt.

    `laborlaw.md` asks for it and keeps asking, but a requirement that lives
    only in a prompt is one the model may drop on any completion — and it cannot
    be asserted through the scripted model here, whose output is fixed and says
    nothing about the law. So `_narrate` appends `LEGAL_DISCLAIMER` when the
    answer does not already end in it, which makes the obligation a property of
    this build.

    **Both destinations again.** The narration reached the panel as `messages`
    tokens, so a trailer that existed only in the returned `AIMessage` would
    appear on reload and not on screen — the specific way a transcript comes to
    disagree with what a handler read a second earlier.
    """
    model = _model()
    graph = build_graph(model=model, checkpointer=InMemorySaver(), max_tool_calls=3, tools=[])
    config = saver_config("t.laborlaw.disclaimer")
    parts = [
        part
        async for part in graph.astream(
            run_inputs(
                caller=system,
                claim_business_id=claim_id,
                message="Labor law & state rules",
                quick_action="laborlaw",
            ),
            config=config,
            context=_context(engine, claim_id, system),
            stream_mode=["messages", "updates", "custom"],
        )
    ]

    assert REPLY in _streamed(parts), "the fixture is inert — no narration was streamed"
    assert qas_module.LEGAL_DISCLAIMER in _streamed(parts)
    assert "not legal advice" in qas_module.LEGAL_DISCLAIMER

    state = await graph.aget_state(config)
    answers = [
        str(message.content)
        for message in state.values["messages"]
        if getattr(message, "type", "") == "ai"
    ]
    assert answers, "nothing was checkpointed"
    assert answers[-1].endswith(qas_module.LEGAL_DISCLAIMER)

    # The prompt file asks for the same sentence, spelled the same way — a
    # second wording would mean the append never recognised a compliant
    # completion and every briefing ended with the disclaimer twice.
    assert qas_module.LEGAL_DISCLAIMER in prompts.load("laborlaw").text
