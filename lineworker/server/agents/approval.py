"""The write gate — one `interrupt()` producer, one decision shape (Story 6.5).

AD-6's approval mechanism, in one module, because the gate should read as one
thing. What lives here: the `interrupt_on` configuration the vendor's
`HumanInTheLoopMiddleware` is built from, the `pending_approval` lifecycle, the
approval marker that lets a write reach `agents/registry.invoke`, the
identity-and-version guard on an `edit`, the fail-safes for a stale claim and a
lost scope, the `record_copilot_approval` audit call, and the deterministic
hand-off that turns the RTW letter's save into a tool call **without a model
call**.

## There is exactly one `interrupt()` in this build, and it is not in this file

It is inside `HumanInTheLoopMiddleware.after_model`, which is the vendored
implementation §5.2 prescribes. Nothing here calls `interrupt()`; the two
middleware below sit either side of it and are the reason its payload is worth
rendering. That is the whole architecture of the gate:

    model / short-circuit
      → WriteProposalMiddleware.after_model     records `pending_approval`
      → HumanInTheLoopMiddleware.after_model    interrupt() … resume
      → ApprovalOutcomeMiddleware.after_model   guards, audits, marks
      → tools (ApprovalOutcomeMiddleware.wrap)  marker → registry gate

**The ordering is not incidental and it is not spelled here.** `create_agent`
chains `after_model` hooks in *reverse* list order, so the list in
`agents/graph.build_graph` reads `[…, Outcome, HumanInTheLoop, Proposal]` to
produce the sequence above. A reviewer who reorders that list moves the guard to
the wrong side of the pause; `tests/test_copilot_approval.py` asserts the
sequence rather than the list.

## Both write origins produce one payload, and that is the story's core AC

A free-chat write is a tool call the model chose. The RTW letter's save is a
tool call `WriteProposalMiddleware` synthesises from `proposed_write`, with no
model call at all. Both are ordinary entries in `AIMessage.tool_calls` by the
time `HumanInTheLoopMiddleware.after_model` looks at them, so both produce the
identical `HITLRequest` — `action_requests[{name, args, description}]` plus
`review_configs[{action_name, allowed_decisions}]` — and both resume through the
identical `HITLResponse`. One wire contract, not two (AD-6), and it is one
because the second origin was made to look like the first rather than because
two producers were kept in step.

## Three decisions are configured; the v1 card offers two

AD-6, the Copilot-stream convention and the Testing convention all say
`approve | edit | reject`, and the vendor refuses an empty `allowed_decisions`.
So all three are configured and `edit` is guarded and tested. The inline
approval card ships Approve and Reject, because the handler's edit affordance
for the write this story centres on is the modal's text area — which revises the
payload *before* it is proposed, which is strictly better than revising it after.
`respond` exists in the vendor's enum and stays unused, as AD-6 says.

## What an `edit` may not touch, and why the check is here

AD-6: a human may revise only **non-identity** arguments — never the target
entity, never the versions the write was drafted against. `IDENTITY_ARGUMENTS`
names the two, and the comparison is against `pending_approval["identity"]`,
which was projected out of the drafted arguments *before* the pause. It is
therefore a server-side check against what was actually drafted, not a check
against what a client says it was given. A refused edit is discarded and audited
as a rejection: the human expressed an intent this gate cannot honour, and the
safe reading of that is "no".

**Three comparisons, not one, and the two that were missing are the two an
attacker would have reached for** (review of Story 6.5).

- *The tool name.* The vendor builds the revised call from
  `edited_action["name"]`, so an `edit` may rename the call — and
  `awrap_tool_call` then resolves the registry entry from the **revised** name.
  A decision that renamed `update_claim_field` to `save_rtw_letter` would have
  executed a different write than the one the card showed, under the marker the
  handler minted for the first. The card's promise is "this is what will
  execute", so a decision that names a different tool is refused outright.
- *The identity key set.* `_identity_of` projects only the keys **present** in
  the drafted arguments, and the value comparison skipped a key that was not in
  that projection — so an edit could *add* an `expected_version` a draft never
  carried, pinning a version no handler ever saw. Sets are compared before
  values, so adding an identity argument and removing one are both refusals.
- *The re-resolved claim*, which was already here — AD-7's half.

## The gate refuses two things **before** the pause, not after it

`HumanInTheLoopMiddleware` will interrupt on whatever tool calls it finds, so
what a card can promise is bounded by what is on the message before it looks.
`WriteProposalMiddleware.after_model` therefore runs first and takes two
decisions the pause cannot take for itself.

- *One pending write per turn.* The per-tool `ToolCallLimitMiddleware`s in
  `agents/graph.py` each count only their own tool, so one `AIMessage` calling
  **both** write tools passes both limiters — and the pause then batches two
  `action_requests` while this middleware records one, which the panel refuses
  to render and which leaves the thread interrupt-pending for ever. So every
  write call after the first is answered here with an error `ToolMessage` and
  never reaches the card.
- *Arguments that would not have executed.* `registry.invoke` validates against
  the tool's own schema, and it does so **after** the gate — correctly, since
  the marker-less raise must precede everything. But that means an unvalidated
  argument set could be rendered on a card, approved by a handler, and then
  refused by the schema: the card would have promised something that could not
  happen. Validating here and refusing the proposal outright keeps the promise
  true. The tool still validates; this is not a substitute for it.

Both refusals are written as the same `{ok, error}` envelope every tool returns,
so the model reads them like any other failure and the deterministic path
narrates them through `_resolution_note` without a third code path.

And `interrupt_on` carries a `when` predicate that skips any call already
carrying a result. That is the same principle applied to a call somebody *else*
answered: `ToolCallLimitMiddleware`'s `"continue"` behaviour writes an error
`ToolMessage` for a blocked write, and interrupting on a call that has already
been answered would put a card in front of a handler for a write that could
never run — and then audit their approval as a rejection, because a result
existing before the tool ran is exactly how this module recognises a refusal.

The same branch covers AD-7's scope fail-safe. The caller is re-resolved on
every resume, so if the run's re-resolved claim is no longer the claim the write
was drafted against, the approval is discarded exactly like a stale one. The
deeper half of that guarantee is not here and cannot be: `employer_scope` inside
the repository is what actually refuses a claim the caller can no longer see,
and it does so inside the same statement that would have written.

## Nothing here writes an entity, and the audit is its own transaction

`record_copilot_approval` opens a session from `CopilotContext.sessionmaker`,
records one content-free row and commits — `services/audit` argues why it must
be a separate transaction from the write's. The write itself happens where every
write happens: inside an AD-4 command, reached through `agents/registry.invoke`,
through a tool. This module never touches a repository and never composes a
write, which is AD-13's rule applied to the thing that authorises writes.

## Logs carry ids and event names only (AD-11)

Not one drafted value, not one sentence of a letter, not the model's paraphrase
of either. The tool *name* is committed code and is logged; its arguments are
claim data and are not.
"""

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Final

import structlog
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.human_in_the_loop import (
    DecisionType,
    InterruptOnConfig,
)
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, ToolCall, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from agents import registry
from agents.context import CopilotContext
from agents.registry import REGISTRY, WRITE_TOOLS, RegisteredTool, ToolKind
from agents.state import CopilotAgentState, PendingApproval, WriteOutcome
from services import audit
from services.audit import ApprovalOutcome

log = structlog.get_logger()

#: The decisions a handler may take on a proposed write (AD-6).
#:
#: Three, in the vendor's own vocabulary. `respond` is the fourth member of its
#: enum and is deliberately absent — AD-6 names it unused in v1, and a decision
#: with no producer and no guard would be a decision this gate could be handed
#: and would not know what to do with.
ALLOWED_DECISIONS: Final[list[DecisionType]] = ["approve", "edit", "reject"]

#: The arguments an `edit` may not change — the write's identity (AD-6).
#:
#: `claim_business_id` is *which* entity is written; `expected_version` is the
#: version it was drafted against. Revising either would turn "I want this
#: change made differently" into "I want a different change made", or into a
#: force-write over a claim that has since moved. Every write tool's schema
#: descends from `registry.WriteArgs`, so both fields exist on every proposal
#: this gate can see.
IDENTITY_ARGUMENTS: Final[tuple[str, ...]] = ("claim_business_id", "expected_version")

#: What the handler is told when an edit tried to move the write's target.
#:
#: Content-free and specific at the same time: it names the rule that was
#: broken, not the value that broke it (AD-11).
EDIT_REFUSED_NOTE: Final[str] = (
    "That change could not be applied: an approval may adjust what a write "
    "says, but not which claim it is written to or the version it was drafted "
    "against. Nothing was changed on the claim."
)

#: What the model is told in place of a refused edit's tool result.
#:
#: A `ToolMessage` with `status="error"` for the refused call, which is what
#: satisfies the agent loop's "every tool call needs a result" rule and keeps
#: the tool from ever executing. Written for a model rather than for a handler —
#: the handler's sentence is `EDIT_REFUSED_NOTE`.
EDIT_REFUSED_TOOL_MESSAGE: Final[str] = (
    "The revised arguments changed the write's target or its expected version, "
    "which is not permitted. The tool was not executed. Do not retry it unless "
    "the user asks again."
)

#: What the handler is told when a proposed write was rejected.
DEFAULT_REJECTED_NOTE: Final[str] = "Cancelled — nothing was written to the claim."

#: What the handler is told when an approved letter was filed.
LETTER_SAVED_NOTE: Final[str] = (
    "The letter has been filed on this claim. It is in the Documents tab, "
    "under the name it was saved with."
)

#: What the model is told about a second write call in one turn.
#:
#: AD-6 caps a thread at one *pending* write, so a turn that emitted two gets
#: the first put to the handler and the rest answered with this. Written for a
#: model and content-free (AD-11): it names the rule, not the claim, the field
#: or the letter. The prefix that a *tool* would have written is here too,
#: because the deterministic path narrates a refusal by quoting the envelope.
SECOND_WRITE_ERROR: Final[str] = (
    "only one write may be proposed at a time, and another one already awaits "
    "the handler — this call was not executed and was not put to them"
)

#: What the model is told when a drafted write's arguments would not validate.
#:
#: The proposal is discarded before the pause rather than rendered on a card the
#: registry would then refuse (see the module docstring). Content-free for
#: `invoke`'s own recorded reason: a validation message can echo the submitted
#: value, and a submitted value here is model output about a claim.
INVALID_WRITE_ARGUMENTS_ERROR: Final[str] = (
    "the arguments drafted for this write were not valid for the tool, so it "
    "was not put to the handler and nothing was written"
)


def _refused(tool_call_id: str, tool_name: str, error: str) -> ToolMessage:
    """A write refused *before* the pause, in the shape a tool's own failure has.

    `{"ok": false, "error": …}` is `agents/envelope.py`'s envelope, which is
    what every registered tool answers with — so the model reads a refusal from
    this module exactly as it reads a refusal from a command, and
    `_resolution_note` needs no branch of its own to narrate one. A bespoke
    sentence here would have been a second failure vocabulary on one wire.

    `status="error"` is what stops the agent loop routing the call to the tool
    node: `_make_model_to_tools_edge` sends only the calls that have no result
    yet, so a refusal written here is a call that will never execute.
    """
    return ToolMessage(
        content=json.dumps({"ok": False, "error": error}),
        name=tool_name,
        tool_call_id=tool_call_id,
        status="error",
    )


def _arguments_validate(entry: RegisteredTool, arguments: Mapping[str, Any]) -> bool:
    """Whether these arguments would survive the tool's own schema.

    The same `model_validate` `registry.invoke` performs, run **before** the
    pause so that a card cannot promise a write the registry would refuse. It
    is deliberately a *pre*-check and not a replacement: `invoke` still
    validates, because the gate is not the only way into a tool and the raise
    that guards it must stay the first statement of that function.

    The exception is swallowed rather than reported, for `invoke`'s reason: a
    Pydantic message quotes the value it refused, and a value here is model
    output about a claim (AD-11). What the caller does with `False` is log an
    id and a tool name.
    """
    try:
        entry.args_schema.model_validate(dict(arguments))
    except Exception:
        return False
    return True


def _call_is_unanswered(request: ToolCallRequest) -> bool:
    """`interrupt_on`'s `when`: never pause on a call somebody already answered.

    The vendor evaluates this per gated tool call before it composes the
    `HITLRequest`, so returning `False` removes the call from the pause
    entirely — no `action_request`, no decision expected, nothing to render.

    Two producers put a result on a write call before the gate sees it, and
    both would otherwise be pauses a handler cannot usefully answer.
    `ToolCallLimitMiddleware`'s `"continue"` behaviour writes an error
    `ToolMessage` for a blocked write and leaves the call on the `AIMessage`;
    `WriteProposalMiddleware` writes one for a second write in a turn and for a
    proposal whose arguments would not validate. Interrupting on either would
    show a card for a write that can never run — and then record the handler's
    approval as a *rejection*, since "a result exists before the tool ran" is
    precisely how `ApprovalOutcomeMiddleware` recognises a refusal.

    Written defensively about shape because `ToolCallRequest.state` is typed
    `Any` and the vendor documents it as possibly not being a mapping; a state
    this cannot read means "no result found", which pauses. Failing towards the
    pause is the safe direction: the worst case is a handler being asked about
    something they did not need to be asked about.
    """
    tool_call_id = str(request.tool_call.get("id") or "")
    if not tool_call_id:
        return True
    return _tool_result(_messages(request.state), tool_call_id) is None


def interrupt_config() -> dict[str, bool | InterruptOnConfig]:
    """`HumanInTheLoopMiddleware`'s `interrupt_on`, built from the registry.

    **Derived from `registry.WRITE_TOOLS` rather than listed**, which is the
    property that makes the gate impossible to forget: a `kind: write` entry
    added to `REGISTRY` is gated by construction, and a hand-written list beside
    the registry would be the copy that stops being updated — with an ungated
    write tool bound to the model as its failure mode.

    Every entry gets the same three decisions. A per-tool decision set would be
    a second policy surface for no gain: the difference between a letter and a
    field edit is what the payload contains, not what a human may do about it.

    `description` is deliberately not set, so the vendor composes its own from
    the tool name and the arguments. That is the AD-16 property in one omission:
    the approval payload is the *server's* rendering of the pending call, never
    a sentence written about it — and certainly never the model's paraphrase.

    `when` is set, and it is the one per-tool knob that is not policy: see
    `_call_is_unanswered`. It says nothing about *whether this write needs
    approval* — every one does — only that a call somebody has already answered
    is not a call to pause on.
    """
    return {
        name: InterruptOnConfig(
            allowed_decisions=list(ALLOWED_DECISIONS),
            when=_call_is_unanswered,
        )
        for name in sorted(WRITE_TOOLS)
    }


def _channel(state: Any, key: str) -> Any:
    """One channel off whatever shape the harness handed us.

    `AgentState` is a `TypedDict` at type-check time and a plain dict at run
    time, but `ToolCallRequest.state` is typed `Any` and the vendor documents it
    as possibly being a list or a `BaseModel`. Read through one helper rather
    than four `isinstance` ladders, so a future vendor shape is one edit.
    """
    if isinstance(state, Mapping):
        return state.get(key)
    return getattr(state, key, None)


def _messages(state: Any) -> list[BaseMessage]:
    """The conversation so far, defensively — see `_channel`."""
    messages = _channel(state, "messages")
    return list(messages) if isinstance(messages, Sequence) else []


def _last_ai_message(messages: Sequence[BaseMessage]) -> AIMessage | None:
    return next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)


def _tool_call_by_id(messages: Sequence[BaseMessage], tool_call_id: str) -> ToolCall | None:
    """The tool call with this id, wherever in the history it now lives.

    Searched from the end, because the message `HumanInTheLoopMiddleware`
    revised on an `edit` is the one nearest the end and `add_messages` merges it
    by id rather than appending a second copy.
    """
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            if call.get("id") == tool_call_id:
                return call
    return None


def _tool_result(messages: Sequence[BaseMessage], tool_call_id: str) -> ToolMessage | None:
    """The `ToolMessage` answering this call, if one has been written yet.

    Its presence is how the reject branch is recognised: on a rejection the
    vendor keeps the tool call on the `AIMessage` *and* appends an artificial
    error `ToolMessage` for it, which is what stops the agent loop routing the
    call to the tool node. So "a result exists before the tool ever ran" is
    exactly the shape of a refusal.
    """
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.tool_call_id == tool_call_id:
            return message
    return None


def _identity_of(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The subset of drafted arguments an `edit` may not revise."""
    return {key: arguments[key] for key in IDENTITY_ARGUMENTS if key in arguments}


class WriteProposalMiddleware(AgentMiddleware[CopilotAgentState, CopilotContext, Any]):
    """Before the pause: draft the deterministic write, then record what pends.

    Two hooks that look unrelated and are the same job from two directions —
    getting a write tool call in front of `HumanInTheLoopMiddleware`, and making
    the thing it is about to pause on legible to everything downstream.

    Installed **after** `HumanInTheLoopMiddleware` in `build_graph`'s list, which
    is what puts its `after_model` **before** the pause: `create_agent` chains
    `after_model` hooks in reverse list order. See the module docstring.

    Only the async hooks are implemented, and that is a statement about this
    build rather than an omission: the copilot graph is driven exclusively by
    `astream` from one endpoint, and the audit and session work below is
    genuinely asynchronous. A synchronous run would silently skip these hooks,
    which is why there is no synchronous caller and no way to add one without
    noticing this paragraph.
    """

    state_schema = CopilotAgentState

    async def awrap_model_call(
        self,
        request: ModelRequest[CopilotContext],
        handler: Callable[[ModelRequest[CopilotContext]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any] | AIMessage:
        """The RTW save's model-free hand-off (AD-6, AD-14).

        Three states, and the model is called in exactly one of them.

        **No `proposed_write`** — an ordinary turn. The handler runs, the model
        answers, nothing here happens. This is every free-chat turn and all
        seven quick actions.

        **A `proposed_write` with no tool call for it yet** — the letter's save,
        first step. The drafted call is returned directly as an `AIMessage` with
        `tool_calls`, so `HumanInTheLoopMiddleware.after_model` interrupts on it
        natively and the payload is indistinguishable from a model-selected
        write. **No model call is made**, which is the boundary the story states
        twice: the handler's edited text is the payload, and regenerating it
        would discard their edit and put an LLM on a path AD-14 keeps
        deterministic.

        **A `proposed_write` whose call has been answered** — the same run, one
        step later, after the decision resolved and (on approval) the tool ran.
        The confirmation or cancellation is composed here, from the tool's own
        content-free sentence or from `write_outcome`, and again **no model call
        is made**. That is what makes the whole save path work with the model
        server switched off, which is the honest reading of "deterministic".

        ## The three states are told apart by **this proposal's id**

        `ProposedWrite.proposal_id` is minted where the proposal is built from a
        request, and the tool call this synthesises carries it. So "has this
        proposal already been drafted?" is a lookup of one id in the message
        history.

        **It was a scan for any call to the same tool, and that made a thread's
        second save a silent no-op** (review of Story 6.5). The first letter's
        tool call is checkpointed for ever, so on the second save the scan found
        it, took the "already drafted" branch, read the *first* letter's success
        envelope back out of the history and told the handler their second
        letter had been filed — with no proposal, no gate, no `document` row and
        the second letter discarded. It matched on the tool name alone, so this
        was true even for a completely different letter to a different reader.
        Keying on the proposal is what makes each save its own event.
        """
        proposed = _channel(request.state, "proposed_write")
        if not isinstance(proposed, Mapping):
            return await handler(request)

        tool_name = str(proposed.get("tool_name") or "")
        arguments = proposed.get("arguments")
        proposal_id = str(proposed.get("proposal_id") or "")
        if tool_name not in WRITE_TOOLS or not isinstance(arguments, Mapping) or not proposal_id:
            # A channel value this build cannot honour. Falling through to the
            # model is the safe direction — the run answers a question rather
            # than proposing something nobody can read — and it is unreachable
            # while `run_inputs` is the only writer.
            log.warning("copilot.proposed_write_unusable")
            return await handler(request)

        messages = _messages(request.state)
        tool_call_id = f"deterministic-{proposal_id}"
        drafted = _tool_call_by_id(messages, tool_call_id)
        if drafted is None:
            log.info(
                "copilot.deterministic_write_proposed",
                tool=tool_name,
                tool_call_id=tool_call_id,
            )
            return AIMessage(
                content="",
                tool_calls=[
                    ToolCall(
                        type="tool_call",
                        name=tool_name,
                        args=dict(arguments),
                        id=tool_call_id,
                    )
                ],
            )

        text = self._resolution_note(request.state, messages, tool_call_id, tool_name)
        log.info("copilot.deterministic_write_resolved", tool=tool_name)
        # **Returned, not written to the custom stream.** `agents/qas.py::_note`
        # exists because LangGraph's `messages` stream mode carries model
        # tokens, so an *outer* node's `AIMessage` reaches the checkpoint and
        # never the wire. This is not an outer node: it is the model node's own
        # return value, and the vendor emits it on `messages` like any
        # completion. Doing both — which this did, once — put the confirmation
        # on the wire **twice**, and the panel rendered it twice, because the
        # two frames are indistinguishable to a client that is simply
        # concatenating content.
        return AIMessage(content=text)

    @staticmethod
    def _resolution_note(
        state: Any, messages: Sequence[BaseMessage], tool_call_id: str, tool_name: str
    ) -> str:
        """What a handler is told once their deterministic write has resolved.

        Composed from two committed sources and never from a model. The tool's
        envelope carries a short, content-free failure sentence written for
        exactly this (`agents/tools/documents.py`), and `agents/qas.py::
        _failure_note` established quoting it rather than re-wording it: a
        second wording is a second place the same failure is described.

        A refused or rejected decision has no tool result to quote — the tool
        never ran — so `write_outcome` supplies the sentence instead.
        """
        outcome = _channel(state, "write_outcome")
        if outcome == "edit_refused":
            return EDIT_REFUSED_NOTE
        if outcome == "rejected":
            return DEFAULT_REJECTED_NOTE

        result = _tool_result(messages, tool_call_id)
        payload: Any = None
        if result is not None and isinstance(result.content, str):
            try:
                payload = json.loads(result.content)
            except ValueError:
                payload = None
        if isinstance(payload, Mapping) and payload.get("ok") is True:
            return LETTER_SAVED_NOTE
        reason = ""
        if isinstance(payload, Mapping):
            reason = str(payload.get("error") or "")
        if not reason:
            # The tool answered in a shape this branch cannot read. Saying so
            # plainly beats inventing an outcome, and the reassurance a handler
            # actually needs is the second sentence either way.
            log.warning("copilot.deterministic_write_unreadable", tool=tool_name)
            reason = "the deterministic source did not answer"
        return f"The letter was not filed: {reason}. Nothing was changed on the claim."

    async def aafter_model(
        self, state: CopilotAgentState, runtime: Runtime[CopilotContext]
    ) -> dict[str, Any] | None:
        """Record the write that is about to pend, before anything pauses on it.

        Runs **before** `HumanInTheLoopMiddleware.after_model` (see the module
        docstring on the ordering), so the `pending_approval` it writes is
        committed to the checkpoint *while the thread is paused*. That is what
        makes an interrupt-pending thread describable across a process restart:
        `agents/threads.thread_state` re-derives the 409 from the saver, and a
        reviewer reading a checkpoint can see which write a conversation is
        waiting on.

        It writes nothing for a turn with no write in it, which is almost every
        turn — including a turn where the model called three read tools.

        ## …and it decides *which* write pends, which is the other half

        Three kinds of write call can be on one `AIMessage`, and only one of
        them may reach the card. See the module docstring for the arguments;
        what this loop does with them is:

        - **Already answered** — skipped entirely, and no proposal is recorded
          for it. That is `ToolCallLimitMiddleware`'s blocked write, and
          recording it used to mean a card whose approval was then audited as a
          rejection because the refusal `ToolMessage` was already there.
        - **The first unanswered one whose arguments validate** — the proposal.
        - **Anything after it, and anything that would not validate** —
          answered here with an error envelope, so the pause never sees it.

        The order matters: the *first* write call is the proposal, so a model
        that emitted two gets the one it asked for first rather than whichever
        one this loop happened to reach.
        """
        messages = _messages(state)
        last = _last_ai_message(messages)
        if last is None or not last.tool_calls:
            return None

        pending: PendingApproval | None = None
        refusals: list[ToolMessage] = []
        for call in last.tool_calls:
            name = str(call.get("name") or "")
            if name not in WRITE_TOOLS:
                continue
            tool_call_id = str(call.get("id") or "")
            if _tool_result(messages, tool_call_id) is not None:
                # Somebody has already refused this call — the write-scoped
                # tool budget, or an earlier pass of this loop. It will never
                # execute, so there is nothing to put to a handler.
                log.info("copilot.write_already_answered", tool=name, tool_call_id=tool_call_id)
                continue
            arguments = dict(call.get("args") or {})
            if pending is not None:
                log.info("copilot.second_write_refused", tool=name, tool_call_id=tool_call_id)
                refusals.append(_refused(tool_call_id, name, SECOND_WRITE_ERROR))
                continue
            entry = REGISTRY.get(name)
            if entry is None or not _arguments_validate(entry, arguments):
                # AD-11: the tool and the id, never the arguments that failed.
                log.info("copilot.write_arguments_rejected", tool=name, tool_call_id=tool_call_id)
                refusals.append(_refused(tool_call_id, name, INVALID_WRITE_ARGUMENTS_ERROR))
                continue
            log.info("copilot.write_proposed", tool=name, tool_call_id=tool_call_id)
            pending = PendingApproval(
                tool_name=name,
                tool_call_id=tool_call_id,
                arguments=arguments,
                identity=_identity_of(arguments),
            )

        if pending is None and not refusals:
            return None

        update: dict[str, Any] = {
            "pending_approval": pending,
            # Cleared, not omitted: the channel is last-write-wins and a stale
            # outcome from the previous proposal would be the sentence the
            # deterministic path narrated for this one.
            "write_outcome": None,
        }
        if refusals:
            update["messages"] = refusals
        return update


class ApprovalOutcomeMiddleware(AgentMiddleware[CopilotAgentState, CopilotContext, Any]):
    """After the pause: guard the decision, audit it, and mark what may execute.

    Installed **before** `HumanInTheLoopMiddleware` in `build_graph`'s list,
    which is what puts its `after_model` **after** the pause. See the module
    docstring; the inversion is `create_agent`'s and it is the single most
    confusing thing about this file.

    Three hooks, and each owns one segment of the marker's short life.
    `after_model` decides and sets it, `wrap_tool_call` spends it, `before_model`
    clears it. A marker that outlived its tool call would be a marker the next
    call in the same run executed under — which is the one way the gate could
    authorise something nobody approved.
    """

    state_schema = CopilotAgentState

    async def abefore_model(
        self, state: CopilotAgentState, runtime: Runtime[CopilotContext]
    ) -> dict[str, Any] | None:
        """Drop a spent approval marker before the next model step.

        The tool node runs between `after_model` and here, so by the time this
        executes the approved write has either run or failed. Either way the
        marker has done its job, and AD-6 keys it to *one* tool-call id: leaving
        it set would mean a second write proposed later in the same run started
        life already approved, which is the failure mode the whole gate exists
        to make impossible.

        Returns `None` when there is nothing to clear, so an ordinary turn
        writes no channel at all.
        """
        if _channel(state, "approved_tool_call_id") is None:
            return None
        return {"approved_tool_call_id": None}

    async def aafter_model(
        self, state: CopilotAgentState, runtime: Runtime[CopilotContext]
    ) -> dict[str, Any] | None:
        """Classify the resolved decision, guard it, audit it, mark it.

        By the time this runs, `HumanInTheLoopMiddleware` has raised its
        `interrupt()`, a human has answered, and it has rewritten the pending
        tool call in place. What is left is to work out *which* answer it was —
        the vendor records that nowhere — and to make the consequences real.

        The classification reads the messages rather than the resume body,
        deliberately: what matters is the call that will actually execute, not
        what a client claimed to send.

        - A **result already exists** for the pending call, before any tool has
          run: that is the vendor's artificial error `ToolMessage`, which it
          writes only on `reject`.
        - The arguments are **unchanged**: `approve`.
        - The arguments **changed**: `edit`, which is then guarded.

        All three record exactly one content-free audit row, which is the only
        persistence the reject branch is permitted (AD-6). The row is written
        before the write executes, so a decision that was taken stays recorded
        even when the write it authorised loses a race.
        """
        pending = _channel(state, "pending_approval")
        if not isinstance(pending, Mapping):
            return None

        tool_call_id = str(pending.get("tool_call_id") or "")
        tool_name = str(pending.get("tool_name") or "")
        messages = _messages(state)
        call = _tool_call_by_id(messages, tool_call_id)
        if call is None:
            # The pending call is gone from the history entirely — a shape no
            # decision produces (the vendor keeps the call for all three). Fail
            # closed: discard, audit as a rejection, mark nothing.
            log.warning("copilot.pending_write_vanished", tool_call_id=tool_call_id)
            await self._audit(runtime, pending, "rejected")
            # **Annotated rather than written inline**, and the four sites below
            # are annotated for the same reason. `write_outcome`'s vocabulary is
            # a `Literal` in `agents/state.py`, and a value written into a
            # `dict[str, Any]` return is checked against nothing at all — which
            # is exactly how that channel came to document seven values with
            # four producers. `agents/graph.py::Route` records the same lesson:
            # a declaration nothing is checked against is a comment.
            vanished: WriteOutcome = "rejected"
            return {
                "pending_approval": None,
                "approved_tool_call_id": None,
                "write_outcome": vanished,
            }

        if _tool_result(messages, tool_call_id) is not None:
            log.info("copilot.write_rejected", tool=tool_name, tool_call_id=tool_call_id)
            await self._audit(runtime, pending, "rejected")
            rejected: WriteOutcome = "rejected"
            return {
                "pending_approval": None,
                "approved_tool_call_id": None,
                "write_outcome": rejected,
            }

        arguments = dict(call.get("args") or {})
        drafted = dict(pending.get("arguments") or {})
        identity = dict(pending.get("identity") or {})
        refusal = self._identity_refusal(
            runtime, str(call.get("name") or ""), tool_name, arguments, identity
        )
        if refusal is not None:
            log.info(
                "copilot.write_edit_refused",
                tool=tool_name,
                tool_call_id=tool_call_id,
                reason=refusal,
            )
            await self._audit(runtime, pending, "rejected")
            refused: WriteOutcome = "edit_refused"
            return {
                "pending_approval": None,
                "approved_tool_call_id": None,
                "write_outcome": refused,
                # The refused call still needs a result, or the agent loop
                # routes it to the tool node. An error `ToolMessage` is the
                # vendor's own device for exactly this, and it is what keeps
                # "refused" and "not executed" the same event.
                "messages": [
                    ToolMessage(
                        content=EDIT_REFUSED_TOOL_MESSAGE,
                        name=tool_name,
                        tool_call_id=tool_call_id,
                        status="error",
                    )
                ],
            }

        # `ApprovalOutcome` is the audit's three-member vocabulary and
        # `WriteOutcome` is the channel's four; the two agree on these two
        # members by construction, and the second annotation is what makes that
        # a checked fact rather than a coincidence.
        outcome: ApprovalOutcome = "approved" if arguments == drafted else "edited"
        resolved: WriteOutcome = outcome
        log.info(f"copilot.write_{outcome}", tool=tool_name, tool_call_id=tool_call_id)
        await self._audit(runtime, pending, outcome)
        return {
            "pending_approval": None,
            "approved_tool_call_id": tool_call_id,
            "write_outcome": resolved,
        }

    @staticmethod
    def _identity_refusal(
        runtime: Runtime[CopilotContext],
        revised_name: str,
        pending_name: str,
        arguments: Mapping[str, Any],
        identity: Mapping[str, Any],
    ) -> str | None:
        """Why this decision cannot be honoured, or `None`.

        Four refusals, and they are one refusal from four directions: what
        executes must still be the write the card showed, about the entity and
        the version it was drafted against, for a caller still entitled to it.

        **The tool name is compared first**, and it has to be. The vendor builds
        an `edit`'s revised call from `edited_action["name"]`, and
        `awrap_tool_call` resolves the registry entry from that revised name —
        so without this an `edit` could rename `update_claim_field` to
        `save_rtw_letter` and a *different write* would execute under a marker a
        handler minted for the first. That is precisely the substitution AD-16's
        approval honesty exists to prevent, and it is the one an `edit` decision
        makes cheapest.

        **Then the identity key set**, before any value is compared.
        `_identity_of` projects only the keys the draft actually carried, and a
        value comparison that skipped a key absent from that projection let an
        `edit` *add* an `expected_version` the draft never had — pinning a
        version no handler ever saw on the card they approved. Adding a key and
        removing one are the same refusal.

        **Then the values**, which was the whole check.

        **And then the re-resolved claim**, which is AD-7's resume fail-safe at
        the gate. It is not the whole of it — `employer_scope` inside the
        repository is what actually refuses a claim the caller can no longer
        see, inside the statement that would otherwise have written — but it is
        the half that can answer *before* anything is attempted, and it catches
        the case a repository cannot: a resume whose re-resolved thread is about
        a different claim.

        Returns a short reason for the log, never for the handler; the
        handler's sentence is `EDIT_REFUSED_NOTE` and says nothing about which
        field moved.
        """
        if revised_name != pending_name:
            return "the decision named a different tool than the one that pends"
        if set(_identity_of(arguments)) != set(identity):
            return "the decision added or removed an identity argument"
        for key in IDENTITY_ARGUMENTS:
            if key in identity and arguments.get(key) != identity[key]:
                return f"identity argument {key} was altered"
        drafted_claim = identity.get("claim_business_id")
        resolved_claim = runtime.context.claim_business_id
        if (
            drafted_claim is not None
            and resolved_claim is not None
            and drafted_claim != resolved_claim
        ):
            return "the resumed run is no longer about the drafted claim"
        return None

    @staticmethod
    async def _audit(
        runtime: Runtime[CopilotContext],
        pending: Mapping[str, Any],
        outcome: ApprovalOutcome,
    ) -> None:
        """One content-free `record_copilot_approval` row, in its own session.

        Its own session for `agents/registry.py`'s rule — a node holds no
        session across model time — and its own transaction because
        `services/audit.record_copilot_approval` commits (see there, and see
        `update_claim_fields`' refusal to be wrapped by a composite).

        The claim comes from the **run context**, never from the proposal: the
        context was re-resolved by this request's auth dependency, and an audit
        row that named a claim taken out of model-drafted arguments would be a
        row a hijacked model could mis-file. The drafted identity is the
        fallback only for the reserved dashboard scope, which v1 never mints.
        """
        context = runtime.context
        identity = pending.get("identity") or {}
        claim = context.claim_business_id or str(identity.get("claim_business_id") or "")
        if not claim:  # pragma: no cover - v1 mints no thread without a claim
            log.warning("copilot.approval_unaudited_no_claim")
            return
        async with context.sessionmaker() as session:
            await audit.record_copilot_approval(
                session, context.caller, outcome=outcome, claim_business_id=claim
            )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Spend the approval marker on the one call it was minted for.

        The last hop of AD-6's marker, from graph state into
        `agents/registry.invoke`'s gate. A `StructuredTool` body has no access
        to either, so the marker travels the only distance it cannot cover
        itself through `registry.APPROVED_TOOL_CALL` — set here, around this one
        execution, and reset the moment it returns.

        **The id must match**, not merely be present. A marker set for one call
        that authorised a different one would be an approval a handler gave for
        something else, which is precisely the substitution AD-16's approval
        honesty exists to prevent.

        A write that arrives here without a matching marker is passed through
        **unmarked**, deliberately: `invoke` then raises `WriteNotApproved`, and
        refusing it here instead would move the gate out of the module that
        documents it and leave two places that decide the same thing. Reads pass
        through untouched — the marker means nothing to them, and setting it for
        one would make the context variable a thing reads could be audited
        against.
        """
        call = request.tool_call
        name = str(call.get("name") or "")
        entry = REGISTRY.get(name)
        if entry is None or entry.kind is not ToolKind.write:
            return await handler(request)

        tool_call_id = str(call.get("id") or "")
        marker = _channel(request.state, "approved_tool_call_id")
        if not tool_call_id or marker != tool_call_id:
            log.warning("copilot.unmarked_write_reached_tool_node", tool=name)
            return await handler(request)

        with registry.approved_write(tool_call_id):
            return await handler(request)


#: The write-tool budget one run gets, per write tool.
#:
#: One. `agents/graph.py` builds one write-scoped `ToolCallLimitMiddleware` per
#: member of `WRITE_TOOLS` from this, with the vendor's `"continue"` behaviour:
#: a second call to *that* tool in one run is refused with an explanation the
#: model can act on, rather than terminating a turn that has already answered a
#: question correctly.
#:
#: **It is half of AD-6's single pending write, not the whole of it.** The
#: vendor's limiter counts only the tool it names, so a turn calling both write
#: tools passes both limiters; `WriteProposalMiddleware.aafter_model` is what
#: caps the *set*, and the two together are why `pending_approval` is singular
#: by construction rather than by convention.
#:
#: It is a *per-pending-write serialization*, not a hard per-run quota on the
#: conversation: a genuinely required second write is issued after the first
#: resolves, which is a second run and a second budget.
WRITE_CALLS_PER_RUN: Final[int] = 1


__all__ = [
    "ALLOWED_DECISIONS",
    "DEFAULT_REJECTED_NOTE",
    "EDIT_REFUSED_NOTE",
    "IDENTITY_ARGUMENTS",
    "INVALID_WRITE_ARGUMENTS_ERROR",
    "LETTER_SAVED_NOTE",
    "SECOND_WRITE_ERROR",
    "WRITE_CALLS_PER_RUN",
    "ApprovalOutcomeMiddleware",
    "WriteProposalMiddleware",
    "interrupt_config",
]
