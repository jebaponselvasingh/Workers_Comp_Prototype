"""The one compiled copilot graph (AD-6, §5.2) — built once, read off `app.state`.

Every copilot turn in the process enters here. There is one `StateGraph`, it is
compiled in `api/app.py`'s `lifespan` with the `AsyncPostgresSaver` attached,
and nothing else in the build constructs a graph or an agent.

    entry router ──(dispatch map)──▶ seven quick-action nodes ──▶ END
                 └─(free text)────▶ grounded chat            ──▶ END

## The entry router is deterministic, and that is AD-14

`route_entry` chooses a node from **two channels and nothing else**: whether a
write was drafted outside the model (`proposed_write`, Story 6.5) and which
quick-action key, if any, routed the turn. It does not ask a model, it does not
read claim text, and it does not parse anything out of a message body — which is
AD-16's "nothing derived from injected content may select a tool, alter a route
or name a scope", made structural rather than promised.

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

`HumanInTheLoopMiddleware` **is** here since Story 6.5, which registered the
first two `kind: write` tools and therefore the first thing for it to gate. It
is the single `interrupt()` producer in this build: both write origins — a
model-selected write on a free-chat turn, and the RTW letter's deterministic
save — arrive at it as ordinary entries in `AIMessage.tool_calls`, so both
produce one payload and resume through one decision shape (AD-6). The two
middleware either side of it are `agents/approval.py`'s, and **the order of the
three in the list below is load-bearing**: `create_agent` chains `after_model`
hooks in reverse list order, so `[Outcome, HumanInTheLoop, Proposal]` runs as
Proposal → HumanInTheLoop → Outcome. `agents/registry.py`'s `WriteNotApproved`
raise stays as defence in depth behind all three.

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
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents import approval, prompts, qas
from agents.context import CopilotContext
from agents.registry import WRITE_TOOLS, build_tools
from agents.state import CallerRef, CopilotAgentState, CopilotState, ProposedWrite
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

    It reads **two channels** and nothing else — no message body, no claim text,
    no model call. See the module docstring on why that is the AD-14/AD-16
    property rather than a simplification.

    `proposed_write` is checked first and is Story 6.5's: a run carrying a write
    drafted outside the model goes to the agent node, because that is where the
    approval gate lives. It cannot conflict with a quick action — the runs
    endpoint refuses a body carrying both — and it is checked first anyway, so
    that the two orderings a reader might assume give the same answer.

    **Not an eighth quick-action key.** The RTW letter's save reuses the `rtw`
    key's whole surface (`QUICK_ACTIONS` above says so) and adds a channel
    instead: a key routes a *question* to a node, and this is not a question.
    """
    if state.get("proposed_write") is not None:
        return CHAT_NODE
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
    proposed_write: ProposedWrite | None = None,
) -> dict[str, Any]:
    """The state update that starts one turn. **The only place run inputs are made.**

    Always writes `caller`, freshly narrowed from a context this request just
    resolved — so whatever a checkpoint holds is overwritten before the entry
    node reads it (AD-7). `claim_business_id` is written for the same reason:
    a thread's claim is a fact about the thread row, not about the checkpoint,
    and re-asserting it is free.

    The human message is appended by `add_messages` rather than replacing the
    history, which is what makes a thread a conversation.

    `quick_action` carries one of Story 6.4's seven keys. `proposed_write`
    carries Story 6.5's RTW letter save — a write drafted by a handler in the
    modal, not by a model. Both are **always written**, `None` included, for the
    same last-write-wins reason: a value left in a checkpoint would dispatch the
    *next* free-text message down a quick action's node, or re-propose
    yesterday's letter over a question somebody typed today.

    The two are mutually exclusive in practice — `api/routers/copilot.py`
    refuses a body carrying both — and this function does not enforce that,
    deliberately: it is the single place run inputs are *made*, not the place
    requests are validated, and a second refusal here would be a second answer
    to "is this body well formed?".
    """
    update: dict[str, Any] = {
        "messages": [HumanMessage(content=message)],
        "caller": caller_ref(caller),
        "claim_business_id": claim_business_id,
        # Cleared rather than omitted — see the docstring. Story 6.4 is where
        # `quick_action` became a live bug; `proposed_write` joins it under the
        # same rule rather than waiting to become one.
        "quick_action": quick_action,
        "proposed_write": proposed_write,
    }
    return update


def resume_inputs(*, caller: CallerContext) -> dict[str, Any]:
    """The state update that resumes an interrupted turn — the same overwrite.

    AD-7 asks for re-resolution at run start **and every resume**, and this is
    the second half. It writes `caller`: a resume adds no message and changes no
    claim, it only continues a run that a human has answered.

    **And it clears `quick_action`, for `run_inputs`' reason.** That channel is
    last-write-wins, so an update that omits it leaves the previous turn's key
    in the checkpoint — and `agents/state.py` documents "the writer always
    writes it" as an invariant of the channel rather than as a habit of one
    function. Inert in practice, because a resume re-enters at the interrupted
    node rather than at the router, and correct anyway.

    **`proposed_write` is deliberately *not* cleared**, which is the one place
    this function and `run_inputs` disagree and the disagreement is the whole
    of the RTW save's second half. A resume continues the run that proposed the
    letter: `agents/approval.py`'s middleware still has to recognise that this
    run is the deterministic one, so that the confirmation or cancellation is
    composed in committed code rather than by a model call the save path is not
    allowed to make. Clearing it here would put an LLM on the one path AD-14
    keeps deterministic — and would do it silently, because the run would still
    answer. The channel is cleared by the next `run_inputs`, which is every
    other way into the graph.
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
    # **The middleware list is a named local with a widened element type**, and
    # the widening is a vendor-typing accommodation rather than a loosening.
    # `AgentMiddleware` is generic in its state schema and that parameter is
    # invariant, so a list mixing this build's `CopilotAgentState`-typed
    # middleware with the vendor's own — which bind their state schemas
    # separately — has no common inferred element type and every entry is then
    # checked against whichever one mypy picked first. Declaring the list is
    # what lets the three approval middleware sit beside the two budget ones;
    # each class still declares its real `state_schema`, which is what
    # `create_agent` actually merges into the graph.
    middleware: list[AgentMiddleware[Any, Any, Any]] = [
        # **The write gate, and the order of these three is the gate.**
        #
        # `create_agent` chains `after_model` hooks in *reverse* list order, so
        # what runs is Proposal → HumanInTheLoop → Outcome: the proposal is
        # recorded on state, the vendor's middleware pauses the run on it, and
        # the resolved decision is guarded, audited and marked. A reviewer who
        # reorders these puts the guard on the wrong side of the pause —
        # `agents/approval.py` says so at length, and
        # `tests/test_copilot_approval.py` asserts the sequence rather than the
        # list.
        #
        # `interrupt_on` is built from `registry.WRITE_TOOLS`, so a write entry
        # added to the registry is gated by construction rather than by
        # somebody remembering to widen a literal here.
        approval.ApprovalOutcomeMiddleware(),
        HumanInTheLoopMiddleware(interrupt_on=approval.interrupt_config()),
        approval.WriteProposalMiddleware(),
        # The run's tool budget, and **`exit_behavior` is passed** rather than
        # left at the vendor's default (review of Story 6.3: it was argued here
        # and never given, so the installed behaviour was `"continue"` — block
        # the exceeded tool, hand the model an error message, and let it keep
        # calling. The runaway loop this knob exists to stop was stopped by the
        # 300-second wall clock instead, which is the bound in the dimension it
        # was explicitly *not* supposed to be).
        #
        # `"end"` rather than `"error"`: a turn that exhausted its tools should
        # finish with whatever it has and let the handler ask again, not
        # terminate the stream with an error frame about an internal bound.
        ToolCallLimitMiddleware(thread_limit=None, run_limit=max_tool_calls, exit_behavior="end"),
        # **One call per run, per write tool** — the *repeat* half of AD-6's
        # "at most one write pends at a time".
        #
        # **It is not the whole of that guarantee, and the comment here claimed
        # it was** (review of Story 6.5). The vendor's limiter counts only the
        # tool it names (`_matches_tool_filter`), so one `AIMessage` calling
        # `save_rtw_letter` *and* `update_claim_field` passes both limiters
        # untouched — and `HumanInTheLoopMiddleware` then emits two
        # `action_requests` while `pending_approval` records one. The panel
        # refuses a payload carrying anything but exactly one, so no card
        # renders and the thread 409s every later message with no way forward.
        # There is no vendor knob for "one across this *set* of tools":
        # `tool_name` takes one name and `None` means every tool in the build.
        # So the across-the-set cap lives in `agents/approval.py`, which drops
        # every write call after the first and answers it with an error
        # `ToolMessage` before the pause ever sees it.
        #
        # What these still buy is the per-tool budget across a run: a model that
        # calls the same write tool again after one has resolved is refused
        # here rather than proposing a second card in one turn.
        #
        # `"continue"` rather than `"end"`, unlike the budget above: a turn that
        # asked for a second write should be told it cannot have one and be
        # allowed to finish answering, where a turn that exhausted its *read*
        # budget has probably looped and should stop. A call refused here
        # carries a result before the gate looks at it, which is why
        # `interrupt_config`'s `when` predicate skips answered calls — a card
        # for a write that can never run would be audited as a rejection
        # whatever the handler pressed.
        *(
            ToolCallLimitMiddleware(
                tool_name=name,
                thread_limit=None,
                run_limit=approval.WRITE_CALLS_PER_RUN,
                exit_behavior="continue",
            )
            for name in sorted(WRITE_TOOLS)
        ),
    ]
    # **No `type: ignore` here since Story 6.5**, and the reason is the named
    # `middleware` local above rather than anything about this call. The ignore
    # was for `ContextT`, which the vendor's overloads bind from the model's own
    # generic — something `BaseChatModel` does not carry — and giving the
    # middleware list an explicit element type is what now lets the overload
    # resolve. It is deliberately not re-added: an unused ignore is a comment
    # claiming a constraint that is no longer there.
    agent = create_agent(
        model,
        tools=list(tools) if tools is not None else list(build_tools()),
        system_prompt=system_prompt,
        state_schema=CopilotAgentState,
        context_schema=CopilotContext,
        middleware=middleware,
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
