"""The copilot graph's **closed** state schema — every channel, in one module.

AD-6's rule, stated as a file: all nodes share one typed schema, and **new
channels are added here, by review, never node-locally**. A node that returns a
key this module does not declare is a node writing to a channel LangGraph will
either drop silently or accept into an untyped dict, and either way the schema
stops describing the graph. `tests/test_copilot_graph.py` asserts the compiled
graph's channel set is a subset of what is declared here, so the rule is
machine-checked rather than remembered.

This is the schema Stories 6.4, 6.5 and 6.6 extend. Their extensions are named
below where they land, so that "where does the QAS key go?" has an answer
before somebody invents a second place for it.

## What is a channel and what is context — the AD-7 split

The story's Task 3 puts `caller` in graph state; the architecture says caller
scope rides the `context_schema` and is "never a checkpointed state channel".
Both are true, because they are different things:

- **The reference** — who this thread belongs to, as a user id and a role — is
  a channel. It is checkpointed, and it is **overwritten from `app_user` at
  every run start and every resume**, so a stale value can never be read: the
  run's first act is to put the freshly resolved one there. That overwrite is
  what makes a poisoned checkpoint harmless, and
  `test_copilot_graph.py::test_a_poisoned_caller_channel_is_overwritten_on_resume`
  is the assertion that it happens *before any node reads it*.
- **The materialized scope** — `employer_ids`, `ALL_EMPLOYERS`, the whole
  `CallerContext` — is **not** a channel and must never become one. It rides
  `agents/context.py`'s per-run context, which LangGraph does not persist. A
  checkpoint carrying yesterday's book of business would let a resumed thread
  read claims the handler no longer covers, months later, with nothing on any
  screen to say so.

So the type below carries `CallerRef` and not `CallerContext`, and that is the
single most load-bearing line in this module.

## Why `MessagesState` is extended rather than re-declared

`messages` is `langgraph.graph.MessagesState`'s channel, with the `add_messages`
reducer that appends rather than replaces and that reconciles a streamed
message chunk with the message it belongs to. Re-declaring it here would be
this project owning a reducer whose correctness is the vendor's problem, and
getting it subtly wrong is how a streamed turn ends up duplicated in a
transcript.
"""

from typing import Any, NotRequired

from langchain.agents import AgentState
from langgraph.graph import MessagesState
from typing_extensions import TypedDict


class CallerRef(TypedDict):
    """Who the thread belongs to — a **reference**, never a scope.

    Two fields, and the absence of a third is the design. There is no
    `employer_ids` here, no `scope_all`, and no `CallerContext`: see the module
    docstring, and `agents/context.py` for where those actually live.

    `role` is present because a prompt may reasonably say "you are answering a
    claims handler" and because 6.5's approval flow needs to know whose approval
    it is. It is **not** a capability check — nothing branches on it to widen a
    read, which is AD-7's "scope gates visibility, role gates capability" in the
    direction that matters here: widening happens in `data/repositories/`, under
    a context this channel cannot reach.

    **`role` is a plain `str` rather than a `UserRole`, and that is about the
    checkpoint rather than about typing.** A checkpointed value is serialised by
    the saver, and an enum member is a *custom type* to it: LangGraph writes it
    with a type tag, warns on the way back in ("Deserializing unregistered type
    …; this will be blocked in a future version"), and would eventually refuse.
    Writing `UserRole.handler.value` costs nothing — `UserRole` is a `StrEnum`,
    so the token is the same string a prompt would interpolate — and makes the
    whole channel plain JSON, which is what "the saver never has to pickle
    anything" has to mean if it is to stay true.

    `agents/graph.caller_ref` is the one place the narrowing happens, so there
    is one place the conversion is spelled.
    """

    user_id: int
    role: str


class PendingApproval(TypedDict):
    """The write a run has paused on, waiting for approve / edit / reject.

    **Declared here and never populated by this story.** Story 6.5 owns the
    `interrupt()` producer, the approval-marker lifecycle and the RTW letter;
    what 6.3 owes it is a channel that already exists, a stream that already
    speaks `interrupt`, and a registry that already refuses an unmarked write.
    So this type is the shape of that future value, `None` in every checkpoint
    this story can produce, and `tests/test_copilot_graph.py` asserts nothing
    raises an interrupt yet.

    Declared rather than deferred because the alternative is 6.5 adding a
    channel to a schema that is already carrying live conversations — which is
    a checkpoint-compatibility problem, and the reason AD-6 wants the schema
    closed in one place in the first place.

    At most one per thread (AD-6: the model is capped at one write per turn), so
    this is a single optional value and not a list.
    """

    tool_name: str
    tool_call_id: str
    #: The typed arguments the middleware will execute *verbatim* on approval.
    #: The approval UI renders these, never the model's paraphrase of them
    #: (AD-16). `Any` because the shape is the tool's own argument schema and
    #: differs per tool; it is validated by that schema before it is executed.
    arguments: dict[str, Any]


class CopilotState(MessagesState):
    """Every channel the copilot graph has. Adding one is an edit to this class.

    `MessagesState` contributes `messages` with the `add_messages` reducer — see
    the module docstring on why that is inherited rather than restated.

    Nothing here is `Required` beyond `messages` and `caller`, and the
    `NotRequired` markers are load-bearing rather than convenience: a resumed
    run assembles its inputs as a partial update (`caller` and the new human
    message, nothing else), and a schema demanding every key would force the
    run assembler to invent values for channels the checkpoint already holds
    correctly.
    """

    #: The thread's owner, re-resolved and overwritten at every run start and
    #: every resume. See the module docstring; this is the AD-7 channel.
    caller: CallerRef

    #: Which claim this conversation is about — the business id, `WC-nnnn`.
    #:
    #: A business id rather than a surrogate key because it is what the thread
    #: key spells, what the tools take, and what a log line may carry. It is
    #: `NotRequired` because the `dashboard` scope is a reserved key shape:
    #: v1 never mints a thread without a claim, and Epic 7 reopens dashboard
    #: scope without changing this schema.
    claim_business_id: NotRequired[str]

    #: The quick-action key that routed this turn, or `None` for free text.
    #:
    #: One of the seven keys in `agents/graph.py::QUICK_ACTIONS`, validated
    #: against that map by the runs endpoint before it ever reaches state.
    #:
    #: **`str | None` rather than `str`, and the `None` is load-bearing.**
    #: `run_inputs` writes `None` for a free-text turn rather than omitting the
    #: key, because the channel is last-write-wins: an omitted key would leave
    #: the previous turn's value in the checkpoint and the entry router would
    #: dispatch a free-text message down a quick action's node. The declaration
    #: said `str` while the only writer wrote `None`, which type-checked for as
    #: long as the map was empty and nobody constructed the state literally
    #: (Story 6.4).
    quick_action: NotRequired[str | None]

    #: Which node the entry router chose, for the graph's own conditional edge.
    #:
    #: Routing scratch, not an answer: it is written by the router from the
    #: dispatch map and the message kind, and it is **never** parsed out of
    #: model output or out of claim text (AD-16 — nothing derived from injected
    #: content may select a route).
    route: NotRequired[str]

    #: The one write awaiting a human, or `None`. Story 6.5 populates it; see
    #: `PendingApproval`.
    pending_approval: NotRequired[PendingApproval | None]


class CopilotAgentState(AgentState[Any]):
    """`CopilotState`'s channels, on the base the `create_agent` harness requires.

    **The same channels, declared once above and inherited here** — not a second
    schema. `create_agent` demands a `state_schema` descended from its own
    `AgentState` (which adds two private channels of its own, `jump_to` and
    `structured_response`, both middleware bookkeeping), and `CopilotState` is
    descended from `MessagesState` because that is what the outer graph is built
    on. Python has no way to say "this TypedDict, on a different base", so the
    fields are restated — and they are restated by *name only*, from the class
    above, so a channel added there and forgotten here is a mypy error at the
    node that writes it rather than a value silently dropped.

    This is still one schema in one module, which is what AD-6 asks for: both
    classes are here, a reviewer reads them together, and there is no third
    place a channel can be declared.
    """

    caller: CallerRef
    claim_business_id: NotRequired[str]
    quick_action: NotRequired[str | None]
    route: NotRequired[str]
    pending_approval: NotRequired[PendingApproval | None]


#: The channels the `create_agent` harness and its middleware own, declared here
#: because **they are checkpointed too** and AD-6 says every channel is declared
#: in this module.
#:
#: They live one level down, in the compiled subgraph the `grounded_chat` node
#: *is*, which is why they were invisible until the schema guard learned to walk
#: subgraphs (review of Story 6.3): `declared_channels` read the outer graph, and
#: the outer graph does not have them. The node where the work happens was the
#: node the guard was not looking at.
#:
#: - `jump_to` and `structured_response` are `AgentState`'s own middleware
#:   bookkeeping, derived from the vendor's class rather than spelled, so a
#:   rename arrives as a changed set instead of a stale literal.
#: - `run_tool_call_count` and `thread_tool_call_count` are
#:   `ToolCallLimitMiddleware`'s tool budget. They are spelled out because
#:   reaching for the middleware's private `state_schema` from a schema module
#:   would make this file depend on which middleware `agents/graph.py` happens
#:   to install; `tests/test_copilot_graph.py` asserts the vendor's own schema is
#:   still a subset of what is declared here, so a bump that renames one fails
#:   with this constant named rather than silently widening the graph.
#:
#: Declared, not excluded: an exclusion would be a rule about *names*, and the
#: next vendor channel would be admitted by it without anybody deciding to.
HARNESS_CHANNELS: frozenset[str] = frozenset(AgentState.__annotations__) | frozenset(
    {"run_tool_call_count", "thread_tool_call_count"}
)

#: Every channel name this module declares, as the graph test's expected set.
#:
#: Derived from the classes rather than hand-listed, so a channel added above is
#: covered without a second edit — the failure mode of a duplicated list is that
#: the copy nobody updates is the one that stops failing
#: (`SCOPED_REPOSITORY_IDS` records the same lesson).
DECLARED_CHANNELS: frozenset[str] = (
    frozenset(CopilotState.__annotations__)
    | frozenset(MessagesState.__annotations__)
    | HARNESS_CHANNELS
)


__all__ = [
    "DECLARED_CHANNELS",
    "HARNESS_CHANNELS",
    "CallerRef",
    "CopilotAgentState",
    "CopilotState",
    "PendingApproval",
]
