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

from typing import Any, Literal, NotRequired

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

    Declared by Story 6.3 and **populated by Story 6.5**, which is the story
    that owns the `interrupt()` producer, the approval-marker lifecycle and the
    RTW letter. What 6.3 owed it was a channel that already existed, a stream
    that already spoke `interrupt`, and a registry that already refused an
    unmarked write — declared rather than deferred, because the alternative was
    6.5 adding a channel to a schema already carrying live conversations, which
    is a checkpoint-compatibility problem and the reason AD-6 wants the schema
    closed in one place.

    At most one per thread (AD-6: the model is capped at one write per turn), so
    this is a single optional value and not a list. The cap is enforced in
    **two** places, because neither covers the other's case: a write-scoped
    `ToolCallLimitMiddleware` per write tool bounds repeat calls to *one* tool,
    and `agents/approval.py`'s `WriteProposalMiddleware` drops every write call
    after the first *across* the two tools — which the vendor's per-tool
    limiters cannot see, since each counts only its own name. Together they are
    what make this channel singular by construction rather than by convention;
    the second was missing until the review of Story 6.5, and a turn calling
    both write tools produced an interrupt batching two pending writes against a
    channel that recorded one.

    ## Where the drafted versions are, and why they are not a separate field

    AC 1 asks the proposal to record "the `version` of every entity drafted
    against". They are in `arguments`, because that is what they *are*:
    `expected_version` is a field on every write tool's argument schema
    (`registry.WriteArgs`), the model reads it out of a tool result at draft
    time, and it is the value the AD-4 command compare-and-swaps on. A second
    copy beside the arguments would be a second thing to keep true, and the copy
    that drifts is the one the gate reads.

    What `identity` adds is not a copy but a *projection*: the subset of the
    arguments an `edit` decision may not touch. AD-6 permits a human to revise
    only non-identity arguments — never the target entity, never the versions
    the write was drafted against — and `agents/approval.py` enforces that
    server-side by comparing the resolved decision's arguments against this,
    rather than by trusting a client to send back what it was given.
    """

    tool_name: str
    tool_call_id: str
    #: The typed arguments the middleware will execute *verbatim* on approval.
    #: The approval UI renders these, never the model's paraphrase of them
    #: (AD-16). `Any` because the shape is the tool's own argument schema and
    #: differs per tool; it is validated by that schema before it is executed.
    arguments: dict[str, Any]
    #: The arguments an `edit` may not change — the write's identity.
    #:
    #: `claim_business_id` and `expected_version`, projected out of `arguments`
    #: at the moment the proposal is recorded, so the comparison an `edit` is
    #: checked against is the *drafted* value and not whatever the resume body
    #: happened to contain. See the class docstring.
    identity: dict[str, Any]


class ProposedWrite(TypedDict):
    """A write drafted **outside the model**, waiting to be emitted as a tool call.

    The RTW letter's save, and the seam that makes AD-6's "one wire contract,
    not two" literally true. A quick-action node holds no write tool (AD-6), and
    the handler's edited letter must not be regenerated — so the save arrives on
    the run request, is written to this channel by `agents/graph.run_inputs`, and
    `agents/approval.py`'s middleware short-circuits the model call to emit
    exactly this tool call. `HumanInTheLoopMiddleware.after_model` then interrupts
    on it natively, producing the identical `HITLRequest` a model-selected write
    produces and resuming through the identical decision.

    **No model call happens on this path**, which is the point rather than an
    optimisation: the payload is the handler's own text, regenerating it would
    discard their edit, and AD-14 keeps a deterministic action deterministic.

    A channel rather than a parameter because the graph is entered once and the
    middleware runs several nodes later; and a *distinct* channel from
    `pending_approval` because the two are different moments — this is "a write
    has been drafted and no tool call exists yet", `pending_approval` is "a tool
    call exists and a human has not answered".
    """

    #: The name of the write tool this proposal will be emitted as.
    tool_name: str
    #: The tool call's arguments, verbatim — the handler's own letter.
    arguments: dict[str, Any]
    #: **This proposal's identity, minted once when the run is assembled.**
    #:
    #: The tool call the middleware synthesises carries it, so "has this
    #: proposal already been drafted in this run?" is answered by looking up
    #: *that id* in the message history rather than by scanning it for any call
    #: to the same tool. The distinction is the whole of a thread's second save
    #: (review of Story 6.5): a name-keyed scan matched the *first* letter's
    #: call, which is checkpointed for ever, so every later save on the thread
    #: resolved instantly as though it had already been filed — reporting
    #: success, proposing nothing, gating nothing and discarding the handler's
    #: second letter. It matched on name alone, so even a differently-worded
    #: letter to a different reader was answered by the first one's history.
    #:
    #: Minted by `api/routers/copilot.py::_proposed_write`, which is where a
    #: proposal is built from a request, and carried through `run_inputs`
    #: untouched. A UUID rather than something derived from the thread or the
    #: version: two saves on one thread pinned to one claim version are two
    #: different proposals, and nothing about either of them differs.
    proposal_id: str


#: How the last gated write resolved. **Four values, and each has a producer.**
#:
#: `agents/approval.py` is the only writer and it writes exactly these: the
#: three decisions it can classify (`approved`, `edited`, `rejected`) plus the
#: one refusal it raises itself when an `edit` moved the write's identity
#: (`edit_refused`). The channel shipped documented as seven — `saved`, `stale`
#: and `failed` were also listed — and no code path ever produced those three
#: (review of Story 6.5). They were not a reserved vocabulary, they were a
#: description of a design that changed: an execution outcome is narrated from
#: the tool's own envelope, which the deterministic path reads out of the
#: `ToolMessage` rather than out of a channel. A `Literal` is what keeps the
#: list and the writers in step from here on — a fifth value is now a mypy
#: error at the assignment rather than a docstring nobody re-read.
WriteOutcome = Literal["approved", "edited", "rejected", "edit_refused"]


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

    #: The one write awaiting a human, or `None`. See `PendingApproval`.
    pending_approval: NotRequired[PendingApproval | None]

    #: The tool call a human has approved — the AD-6 **approval marker**.
    #:
    #: Set by `agents/approval.py` once `HumanInTheLoopMiddleware` has resolved
    #: an `approve` or an accepted `edit`, keyed by the pending tool-call id,
    #: and cleared before the next model step. It is a channel rather than a
    #: context variable *at this level* because AD-6 says the marker lives in
    #: graph state: a marker on state is one a resumed run still has after a
    #: process restart, and one a reviewer can read out of a checkpoint. The
    #: `ContextVar` in `agents/registry.py` is the last hop from here into a
    #: `StructuredTool` body, not a second source of truth.
    approved_tool_call_id: NotRequired[str | None]

    #: A write drafted outside the model, waiting to become a tool call.
    #:
    #: Written by `run_inputs` for the RTW letter's save and `None` for every
    #: other turn — cleared rather than omitted, for `quick_action`'s reason:
    #: the channel is last-write-wins, and a value left in a checkpoint would
    #: make the *next* free-text message re-propose yesterday's letter. See
    #: `ProposedWrite`.
    proposed_write: NotRequired[ProposedWrite | None]

    #: How the last gated write resolved, for the deterministic path to narrate.
    #:
    #: One of the four `WriteOutcome` members — set by `agents/approval.py` as
    #: the decision resolves. The free-chat path does not read it: there, the
    #: model narrates the outcome from the `ToolMessage` it can see. The RTW
    #: save does, because it makes **no model call** and therefore has to
    #: compose its own confirmation or cancellation sentence from something;
    #: this channel is that something, and it is a closed vocabulary rather than
    #: a message so that the sentence stays committed code rather than
    #: checkpointed prose.
    #:
    #: It does **not** carry the write's *execution* outcome — whether the
    #: command CAS-ed, lost the race or refused. That is in the tool's own
    #: `{ok, error}` envelope, which the deterministic path reads off the
    #: `ToolMessage`; see `WriteOutcome` on the three values that were declared
    #: for it and never written.
    write_outcome: NotRequired[WriteOutcome | None]


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
    approved_tool_call_id: NotRequired[str | None]
    proposed_write: NotRequired[ProposedWrite | None]
    write_outcome: NotRequired[WriteOutcome | None]


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
    "ProposedWrite",
    "WriteOutcome",
]
