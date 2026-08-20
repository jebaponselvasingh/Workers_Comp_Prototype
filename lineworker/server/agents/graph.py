"""The one compiled copilot graph (AD-6, §5.2) — built once, read off `app.state`.

Every copilot turn in the process enters here. There is one `StateGraph`, it is
compiled in `api/app.py`'s `lifespan` with the `AsyncPostgresSaver` attached,
and nothing else in the build constructs a graph or an agent.

    entry router ──(dispatch map)──▶ [6.4's quick-action nodes]
                 └─(free text)────▶ grounded chat  ──▶ END

## The entry router is deterministic, and that is AD-14

`route_entry` chooses a node from **two things and nothing else**: the
`quick_action` channel, and whether there is one at all. It does not ask a
model, it does not read claim text, and it does not parse anything out of a
message body — which is AD-16's "nothing derived from injected content may
select a tool, alter a route or name a scope", made structural rather than
promised.

`QUICK_ACTIONS` is **empty in this story** and that is deliberate. Story 6.4
owns the seven keys and their node map; what it needs from 6.3 is a dispatch
hook already wired, already tested, and already the first thing every run goes
through — so that adding a key is a dict entry and a node, not a re-architecture
of the entry path. `tests/test_copilot_graph.py` asserts the map is empty *and*
that a key in it would route, so the hook is not merely present but working.

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
from typing import Any, Literal

import structlog
from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents import prompts
from agents.context import CopilotContext
from agents.registry import build_tools
from agents.state import CallerRef, CopilotAgentState, CopilotState
from data.context import CallerContext

log = structlog.get_logger()

#: The node the entry router sends free text to, and — until 6.4 — everything.
CHAT_NODE = "grounded_chat"

#: The entry router's node name, so the graph test and the log lines agree on it.
ENTRY_NODE = "entry_router"

#: Quick-action key → node name. **Empty, on purpose** (Story 6.4 fills it).
#:
#: The seven deterministic keys — labour law, similar cases, RTW policy, reserve
#: review, fraud check, next actions, data alignment — and the nodes they
#: dispatch to are 6.4's whole subject. What lands here now is the hook: a
#: static map consulted *before* any model call, so a quick action is answered
#: identically every time because its key routed it, not because a router model
#: happened to agree with itself (AD-14).
#:
#: A key absent from this map falls through to free text, which is the correct
#: behaviour for an unknown key and the reason the map can be empty without the
#: router being a special case.
QUICK_ACTIONS: Mapping[str, str] = {}


def route_entry(state: CopilotState) -> str:
    """Which node this turn goes to. Deterministic; reads two channels.

    Returns a node *name*, consulted by the conditional edge below. It reads
    `quick_action` and nothing else — no message body, no claim text, no model
    call. See the module docstring on why that is the AD-14/AD-16 property
    rather than a simplification.
    """
    key = state.get("quick_action")
    if key is not None and key in QUICK_ACTIONS:
        return QUICK_ACTIONS[key]
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
    the second half. It writes `caller` and nothing else: a resume adds no
    message and changes no claim, it only continues a run that a human has
    answered.

    Nothing raises an interrupt in this story (Story 6.5 owns the producers), so
    this function has no live caller yet beyond the resume branch of the runs
    endpoint and its test. It ships now because the alternative is 6.5 adding
    the re-resolution at the same time as the thing that needs it, and the
    review that catches a missing overwrite is the one that never happens.
    """
    return {"caller": caller_ref(caller)}


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
    builder.add_edge(START, ENTRY_NODE)
    # The dispatch hook. `route_entry` returns a node name, and the path map is
    # the chat node plus whatever 6.4 registers — written as a dict rather than
    # a bare callable so the graph's own structure names its destinations and a
    # key routing somewhere unregistered is a compile-time failure.
    builder.add_conditional_edges(
        ENTRY_NODE,
        route_entry,
        {CHAT_NODE: CHAT_NODE, **{node: node for node in QUICK_ACTIONS.values()}},
    )
    builder.add_edge(CHAT_NODE, END)

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


#: What `route_entry` may answer while `QUICK_ACTIONS` is empty. Typed so 6.4's
#: additions widen a declaration rather than only a dict.
Route = Literal["grounded_chat"]


__all__ = [
    "CHAT_NODE",
    "ENTRY_NODE",
    "QUICK_ACTIONS",
    "Route",
    "build_graph",
    "caller_ref",
    "declared_channels",
    "entry_router",
    "resume_inputs",
    "route_entry",
    "run_inputs",
]
