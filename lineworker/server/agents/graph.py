"""The one compiled copilot graph (AD-6, §5.2) — built once, read off `app.state`.

Every copilot turn in the process enters here. There is one `StateGraph`, it is
compiled in `api/app.py`'s `lifespan` with the `AsyncPostgresSaver` attached,
and nothing else in the build constructs a graph or an agent.

    entry router ──(dispatch map)──▶ seven quick-action nodes ──▶ END
                 └─(free text)────▶ grounded chat            ──▶ END

## The entry router is deterministic, and that is AD-14

`route_entry` chooses a node from **two things and nothing else**: the
`quick_action` channel, and whether there is one at all. It does not ask a
model, it does not read claim text, and it does not parse anything out of a
message body — which is AD-16's "nothing derived from injected content may
select a tool, alter a route or name a scope", made structural rather than
promised.

`QUICK_ACTIONS` is **the seven keys since Story 6.4**, and the hook 6.3 shipped
took them without a re-architecture — which was the point of shipping it empty.
Each entry declares the node it routes to, whether it needs a model, and which
prompt file it is narrated from. One structure: `tests/test_copilot_qas.py`
enumerates it key by key, `agents/qas.py` builds a node for every entry, and
Story 6.6 gates its degradation on the `requires_llm` flags it declares. Two
structures would let the map and the graph disagree about which keys exist.

## The free-text path is `langchain.agents.create_agent`

§5.2's prescription and the successor to the deprecated
`langgraph.prebuilt.create_react_agent`. It is a compiled graph in its own
right, embedded here as one node, which is the arrangement the LangGraph docs
call the sweet spot: deterministic steps around an agentic one.

Its middleware is where the run bounds live. `ToolCallLimitMiddleware` caps how
many tools a turn may call — the bound in the dimension a wall clock does not
cover, since a model looping on `claim_reader` is cheap per call and unbounded
in aggregate. The wall clock itself is enforced by the runs endpoint around the
whole stream, because a timeout that lived here would fire inside the graph and
leave the terminal-event decision in two places.

`HumanInTheLoopMiddleware` is **not** here, and its absence is Story 6.5's
seam rather than an oversight: there is no `kind: write` tool registered, so
there is nothing for it to gate, and a middleware configured to interrupt on an
empty tool set is a moving part with no behaviour. `agents/registry.py`'s
`WriteNotApproved` raise is what stands in the gap in the meantime — defence in
depth that ships before the thing it defends.

## Caller re-resolution happens **outside** every node

`run_inputs` below is the single place a run's inputs are assembled, and it
always writes `caller`. Because `add_messages` merges and every other channel is
a last-write-wins scalar, the value the checkpoint held is overwritten before
the first node executes — which is what makes a poisoned or simply stale
checkpointed `caller` harmless. AD-7 asks for this at run start **and every
resume**, so `resume_inputs` writes it too, and the two functions are next to
each other so that a future third entry point cannot quietly skip it.

The **scope** is never here at all: it rides `CopilotContext` (see
`agents/context.py`), which LangGraph does not persist.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents import prompts, qas
from agents.context import CopilotContext
from agents.registry import build_tools
from agents.state import CallerRef, CopilotAgentState, CopilotState
from data.context import CallerContext

log = structlog.get_logger()

#: Every node name `route_entry` may answer with. Widened by Story 6.4 from the
#: single chat node to the eight destinations the graph now has.
#:
#: A `Literal` rather than a bare `str` because it is a *declaration*: adding a
#: quick action means widening this beside the map, so the set of routes is
#: something a reader can see in one line and mypy can check a caller against —
#: rather than something that has to be reconstructed by reading a dict's
#: values.
#:
#: **It is load-bearing rather than decorative**, and that is the correction the
#: review of this story made. It shipped as a declaration nothing referred to:
#: `route_entry` returned `str`, `QuickAction.node` was `str`, no test named it,
#: and its own docstring claimed `tests/test_copilot_qas.py` asserted the two
#: agreed. Nothing did. So `CHAT_NODE` and `QuickAction.node` are typed with it
#: below — a map entry routing somewhere this does not list is now a mypy error
#: — and `test_the_route_literal_lists_exactly_the_graph_s_destinations` closes
#: the direction a type cannot: that this lists nothing the graph does not have.
#:
#: Declared before the two names that use it, which is the only ordering
#: Python's evaluation allows for an annotated module constant.
Route = Literal[
    "grounded_chat",
    "qas_laborlaw",
    "qas_similar",
    "qas_rtw",
    "qas_reserve",
    "qas_fraud",
    "qas_nextactions",
    "qas_data_alignment",
]

#: The node the entry router sends free text to, and — until 6.4 — everything.
CHAT_NODE: Route = "grounded_chat"

#: The entry router's node name, so the graph test and the log lines agree on it.
ENTRY_NODE = "entry_router"


@dataclass(frozen=True)
class QuickAction:
    """One deterministic quick action: where it goes, and what it needs.

    Three fields, and each answers a question somebody would otherwise answer
    twice.

    `node` is the graph node this key dispatches to, typed `Route` so a new
    entry naming a destination the declaration does not list fails mypy rather
    than the graph. Spelled here rather than derived from the key, so the
    graph's own structure names its destinations and the conditional edge's path
    map is built from the same values the router returns — a key routing
    somewhere unregistered is a compile-time failure in `build_graph` rather
    than a run-time fall-through nobody sees.

    `requires_llm` is **the flag Story 6.6 gates degradation on**, which is why
    it is declared per entry rather than inferred from whether a node happens to
    call a model. When the model server is unreachable, the actions that can
    still answer are exactly the ones this says `False` for, and that has to be
    knowable before a run starts rather than discovered by one failing.

    `prompt_key` names the versioned file the action's instructions come from,
    1:1 with the key (the spine's Prompts convention). It is `None` for
    `data_alignment` alone, which makes no model call and therefore has no
    instructions to version — and the two `None`s travel together by
    construction, since a node with no prompt is a node with nothing to send.
    """

    node: Route
    requires_llm: bool
    prompt_key: str | None


#: Quick-action key → what routing it means (Story 6.4). **The one structure.**
#:
#: Seven snake_case keys, consulted *before any model call*, so a quick action
#: is answered identically every time because its key routed it and not because
#: a router model happened to agree with itself (AD-14). The emoji and the
#: button labels are the browser's — `web/src/features/copilot/quickActions.ts`
#: — per the Enums convention: what crosses the wire is the key.
#:
#: A key absent from this map falls through to free text. That is the correct
#: in-graph behaviour for an unknown key, and it is a net rather than the
#: refusal: `api/routers/copilot.py` validates the key against this map and
#: answers 422 before a thread is touched, which is where an unknown key is
#: actually caught.
QUICK_ACTIONS: Mapping[str, QuickAction] = {
    # § Labor law & state rules — retrieval over the seeded corpus.
    "laborlaw": QuickAction(node="qas_laborlaw", requires_llm=True, prompt_key="laborlaw"),
    # ↻ Similar case outcomes — scoped vector search, with AD-12's disclosure.
    "similar": QuickAction(node="qas_similar", requires_llm=True, prompt_key="similar"),
    # 📄 Review RTW Policy — a draft in the transcript. Story 6.5 owns the
    # modal, the approval gate and the save; this key is the one it reuses.
    "rtw": QuickAction(node="qas_rtw", requires_llm=True, prompt_key="rtw"),
    # ✓ Reserve review — `services/financials`' verdict, narrated.
    "reserve": QuickAction(node="qas_reserve", requires_llm=True, prompt_key="reserve"),
    # 🔍 Fraud risk check — the variant chosen in Python from two derivations.
    "fraud": QuickAction(node="qas_fraud", requires_llm=True, prompt_key="fraud"),
    # ! Next best actions — `services/worklist`' checklist, in its own order.
    "nextactions": QuickAction(node="qas_nextactions", requires_llm=True, prompt_key="nextactions"),
    # 📊 Data alignment note — **the model-free one**, and deliberately so: 6.6
    # needs a false path to gate on, and a provenance note is the answer that is
    # genuinely better computed than written.
    "data_alignment": QuickAction(node="qas_data_alignment", requires_llm=False, prompt_key=None),
}


def route_entry(state: CopilotState) -> Route:
    """Which node this turn goes to. Deterministic; reads one channel.

    Returns a node *name* from `Route`, consulted by the conditional edge below.
    Annotated with the literal rather than `str` so that the declaration and the
    only function that answers with one are checked against each other — which
    is what `Route`'s comment claims and, until the review of this story, was
    not true of anything.

    It reads `quick_action` and nothing else — no message body, no claim text,
    no model call. See the module docstring on why that is the AD-14/AD-16
    property rather than a simplification.
    """
    key = state.get("quick_action")
    if key is not None and key in QUICK_ACTIONS:
        return QUICK_ACTIONS[key].node
    return CHAT_NODE


def entry_router(state: CopilotState) -> dict[str, Any]:
    """The entry node: record the route it chose, change nothing else.

    A node rather than a bare conditional edge from `START`, because the route
    is worth having on state: it is what a log line names, what 6.4's tests
    assert against, and what the stream's `updates` frames carry. A conditional
    edge alone decides and forgets.

    It writes exactly one channel, and that channel is declared in
    `agents/state.py` — the rule this graph is checked against.
    """
    return {"route": route_entry(state)}


def caller_ref(caller: CallerContext) -> CallerRef:
    """A `CallerContext` reduced to the reference that may be checkpointed.

    The narrowing is the AD-7 boundary in one function: `user_id` and `role`
    come through, `employer_ids` does not, and there is no branch here that
    could let it. Written as its own function rather than inline at the two call
    sites below so that a third entry point cannot spell the narrowing slightly
    differently — the way to widen a scope by accident is to have two places
    that decide what a reference contains.
    """
    # `.value` rather than the member — see `CallerRef.role` on why a
    # checkpointed enum is a serialisation problem rather than a typing
    # preference.
    return CallerRef(user_id=caller.user_id, role=caller.role.value)


def run_inputs(
    *,
    caller: CallerContext,
    claim_business_id: str,
    message: str,
    quick_action: str | None = None,
) -> dict[str, Any]:
    """The state update that starts one turn. **The only place run inputs are made.**

    Always writes `caller`, freshly narrowed from a context this request just
    resolved — so whatever a checkpoint holds is overwritten before the entry
    node reads it (AD-7). `claim_business_id` is written for the same reason:
    a thread's claim is a fact about the thread row, not about the checkpoint,
    and re-asserting it is free.

    The human message is appended by `add_messages` rather than replacing the
    history, which is what makes a thread a conversation.

    `quick_action` is accepted and is `None` for every caller this story has;
    Story 6.4 passes a key. It is written to state only when present, so a
    free-text turn does not leave a stale key behind for the next one to
    dispatch on.
    """
    update: dict[str, Any] = {
        "messages": [HumanMessage(content=message)],
        "caller": caller_ref(caller),
        "claim_business_id": claim_business_id,
    }
    if quick_action is not None:
        update["quick_action"] = quick_action
    else:
        # Cleared rather than omitted: `quick_action` is last-write-wins, so an
        # omitted key leaves the previous turn's value in the checkpoint and the
        # router would dispatch this free-text message down a quick action's
        # node. Story 6.4 is where that becomes a live bug; clearing it now
        # means 6.4 does not have to find it.
        update["quick_action"] = None
    return update


def resume_inputs(*, caller: CallerContext) -> dict[str, Any]:
    """The state update that resumes an interrupted turn — the same overwrite.

    AD-7 asks for re-resolution at run start **and every resume**, and this is
    the second half. It writes `caller`: a resume adds no message and changes no
    claim, it only continues a run that a human has answered.

    **And it clears `quick_action`, for `run_inputs`' reason.** That channel is
    last-write-wins, so an update that omits it leaves the previous turn's key
    in the checkpoint — and `agents/state.py` now documents "the writer always
    writes it" as an invariant of the channel rather than as a habit of one
    function. It is inert today, because nothing interrupts and a resume
    re-enters at the interrupted node rather than at the router. Story 6.5
    raises the first interrupt against exactly this path, which is the story
    that would otherwise have to find it.

    Nothing raises an interrupt in this story (Story 6.5 owns the producers), so
    this function has no live caller yet beyond the resume branch of the runs
    endpoint and its test. It ships now because the alternative is 6.5 adding
    the re-resolution at the same time as the thing that needs it, and the
    review that catches a missing overwrite is the one that never happens.
    """
    return {"caller": caller_ref(caller), "quick_action": None}


def build_graph(
    *,
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver[Any],
    max_tool_calls: int,
    tools: Sequence[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the copilot graph. Called **once**, from `lifespan`.

    Once, because AD-6 says one graph — and because compiling per request would
    rebuild the agent harness, re-bind the tools and re-read the prompt file on
    every message, for a graph whose structure cannot vary between runs.

    `tools` is injectable for the graph tests, which drive the whole structure
    against a scripted chat model and a fake tool rather than a database. The
    default is the registry, which is what the composition root gets.

    **The system prompt comes from the versioned file** and is read here, at
    compile time, so a route physically cannot be served without it: there is no
    parameter on any run function that could supply a different one, and no
    caller that could omit it.
    """
    system_prompt, version = prompts.chat_system_message()
    # `create_agent`'s `ContextT` cannot be inferred from a frozen dataclass
    # passed as `context_schema` — the vendor's overloads bind it from the
    # model's own generic, which `BaseChatModel` does not carry. Ignored at the
    # one call rather than widened to `Any`, so a genuinely wrong argument here
    # still fails to type-check.
    agent = create_agent(  # type: ignore[misc]
        model,
        tools=list(tools) if tools is not None else list(build_tools()),
        system_prompt=system_prompt,
        state_schema=CopilotAgentState,
        context_schema=CopilotContext,
        middleware=[
            # The run's tool budget, and **`exit_behavior` is passed** rather
            # than left at the vendor's default (review of Story 6.3: it was
            # argued here and never given, so the installed behaviour was
            # `"continue"` — block the exceeded tool, hand the model an error
            # message, and let it keep calling. The runaway loop this knob
            # exists to stop was stopped by the 300-second wall clock instead,
            # which is the bound in the dimension it was explicitly *not*
            # supposed to be).
            #
            # `"end"` rather than `"error"`: a turn that exhausted its tools
            # should finish with whatever it has and let the handler ask again,
            # not terminate the stream with an error frame about an internal
            # bound.
            ToolCallLimitMiddleware(
                thread_limit=None, run_limit=max_tool_calls, exit_behavior="end"
            ),
        ],
    )

    builder: StateGraph[Any, Any, Any, Any] = StateGraph(
        CopilotState, context_schema=CopilotContext
    )
    builder.add_node(ENTRY_NODE, entry_router)
    builder.add_node(CHAT_NODE, agent)
    # **The seven quick-action nodes get the model by closure, and that is the
    # seam.** No node constructs one — `agents/qas.build_node` takes the same
    # `model` the agent harness above was given, so a graph test can drive all
    # seven against a scripted model and AD-6's "one graph, compiled once" does
    # not acquire a second model per node.
    for key, action in QUICK_ACTIONS.items():
        builder.add_node(
            action.node,
            qas.build_node(key, prompt_key=action.prompt_key, model=model),
        )
    builder.add_edge(START, ENTRY_NODE)
    # The dispatch hook, filled by Story 6.4. `route_entry` returns a node name,
    # and the path map is the chat node plus every registered quick action —
    # written as a dict rather than a bare callable so the graph's own structure
    # names its destinations and a key routing somewhere unregistered is a
    # compile-time failure. Both halves are built from `QUICK_ACTIONS`, so the
    # nodes that exist and the nodes that are reachable cannot disagree.
    builder.add_conditional_edges(
        ENTRY_NODE,
        route_entry,
        {CHAT_NODE: CHAT_NODE, **{action.node: action.node for action in QUICK_ACTIONS.values()}},
    )
    builder.add_edge(CHAT_NODE, END)
    for action in QUICK_ACTIONS.values():
        builder.add_edge(action.node, END)

    compiled = builder.compile(checkpointer=checkpointer)
    # Ids and a version only (AD-11). Which prompt version a process is running
    # is an operational fact worth having in the log; not one word of the prompt
    # is.
    log.info("copilot.graph_compiled", prompt_version=version, max_tool_calls=max_tool_calls)
    return compiled


def declared_channels(graph: CompiledStateGraph[Any, Any, Any, Any]) -> frozenset[str]:
    """Every channel the compiled graph actually has, **subgraphs included**.

    Read off the compiled object rather than off `CopilotState`, because the
    property worth asserting runs the other way: the *graph's* channels must be
    a subset of what `agents/state.py` declares, so a node writing an undeclared
    key is a failing test rather than a silently dropped update.

    **The walk into subgraphs is the half that was missing** (review of Story
    6.3). `grounded_chat` is not a function, it is a compiled graph — that is
    what `create_agent` returns — and its channels are its own: `AgentState`'s
    `jump_to` and `structured_response`, plus `ToolCallLimitMiddleware`'s two
    counters, all of them checkpointed under the same thread. Reading only the
    outer graph therefore checked every node except the one where the work
    happens, and the guard passed while four undeclared channels were being
    persisted. `get_subgraphs(recurse=True)` is the vendor's own enumeration, so
    a future node that is itself a graph is covered without a third edit.

    Internal channels LangGraph adds for its own bookkeeping — branch markers,
    the start sentinel — are filtered by their leading `__` or `branch:` prefix,
    because they are the runtime's and not this schema's.
    """
    names: set[str] = set(graph.channels)
    for _namespace, subgraph in graph.get_subgraphs(recurse=True):
        # `get_subgraphs` is declared as yielding the vendor's `PregelProtocol`,
        # which does not name `channels`; every object it actually yields is a
        # `Pregel` and does. Read through a narrowed `getattr` rather than a
        # `cast`, so a future vendor object that genuinely has no channels
        # contributes none instead of raising inside a test's guard.
        channels: Mapping[str, Any] = getattr(subgraph, "channels", {})
        names |= set(channels)
    return frozenset(
        name for name in names if not name.startswith("__") and not name.startswith("branch:")
    )


__all__ = [
    "CHAT_NODE",
    "ENTRY_NODE",
    "QUICK_ACTIONS",
    "QuickAction",
    "Route",
    "build_graph",
    "caller_ref",
    "declared_channels",
    "entry_router",
    "resume_inputs",
    "route_entry",
    "run_inputs",
]
