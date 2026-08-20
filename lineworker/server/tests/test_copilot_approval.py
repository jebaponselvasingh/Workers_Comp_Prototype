"""The write gate's round trips — approve, edit, reject, stale, scope (Story 6.5).

The tests AD-6 names as the ones that settle the mechanism it deliberately left
unfixed: "the approval-marker channel's exact name and lifecycle, the QAS-to-
write handoff, the frontend's decision reverse-mapping … the interrupt round-trip
test is what settles the rest". So this module drives the **real compiled graph**
— the real `create_agent` harness, the real registry, the real
`HumanInTheLoopMiddleware`, the real approval middleware — against a scripted
chat model and a stubbed service boundary.

## What is stubbed, and what is deliberately not

Stubbed: the two AD-4 commands (`update_claim_fields`, `create_document`) and
the audit sink. Those are the service boundary, and they have their own tests
with a real database behind them; reaching them here would make every assertion
about a gate cost a schema.

Not stubbed: everything between a model's tool call and that boundary. The
middleware chain, the interrupt, the `Command(resume=…)` round trip, the
approval marker's lifecycle through `registry.APPROVED_TOOL_CALL`, the
marker-less raise, the argument validation, and the AD-16 claim confinement all
run for real. That is the point — the failure this module exists to catch is a
gate that is subtly not installed, and every one of those pieces can be present
and mis-wired.

## The two origins, and why they are tested against each other

A free-chat write is a tool call a model chose. The RTW letter's save is a tool
call `WriteProposalMiddleware` synthesises with no model call at all. AC 1 asks
that the two produce the *same* interrupt payload and resume through the *same*
decision, "asserted by comparing the two frames, not by inspecting one" — so
`test_both_write_origins_produce_one_interrupt_shape` runs both and compares,
rather than asserting a shape twice and hoping the two spellings agree.

## Why the stale test asserts a row count and not an error

A passing approve test can satisfy "the stale branch ran" vacuously: an
exception, a refusal, a silently swallowed failure and a correct fail-safe all
look alike from outside. So the stale case counts what the command did — and the
command is a stub that records every call it received, which makes "nothing was
written" an assertion about zero recorded calls rather than about the absence of
a raise.
"""

import json
from collections.abc import Iterator, Sequence
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agents import approval, registry
from agents.context import CopilotContext
from agents.graph import build_graph, resume_inputs, run_inputs
from agents.registry import REGISTRY, WriteNotApproved, build_tools, invoke
from agents.state import ProposedWrite
from agents.threads import saver_config
from data.context import CallerContext
from data.models.enums import UserRole

pytestmark = pytest.mark.anyio

HANDLER_CTX = CallerContext(user_id=7, role=UserRole.handler, employer_ids=frozenset({1, 2}))
CLAIM = "WC-20017"
DRAFTED_VERSION = 3
REPLY = "Deterministic test answer from a scripted chat model."
LETTER = "Dear A. Kowalski,\n\nWe are pleased to offer modified duty.\n\nSincerely,"


# --- scaffolding ---------------------------------------------------------


class _ScriptedModel(BaseChatModel):
    """A model that answers from a script and can therefore call a tool.

    Declared local to this module, per the house convention and for the reason
    `tests/test_copilot_graph.py` records: `GenericFakeChatModel` has no
    `bind_tools`, which `create_agent` calls, so a model that cannot be bound to
    tools is a model that can never make a tool call.

    It is only ever used where a model call is *expected*; the path that must
    make none gets `_NeverCalledModel` below, which fails at the call rather
    than at a count.
    """

    script: Any

    @property
    def _llm_type(self) -> str:
        return "scripted-approval"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=next(self.script))])


class _NeverCalledModel(BaseChatModel):
    """A model that fails the test if anything asks it for a completion.

    The RTW save's whole boundary in one class: AD-14 keeps the deterministic
    path deterministic, and the handler's edited text is the payload, so a model
    call anywhere on that path is a defect whatever it returns. Asserting a call
    *count* of zero would pass against a model that was called and answered;
    this fails at the call.
    """

    @property
    def _llm_type(self) -> str:
        return "never-called"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:  # pragma: no cover - the assertion is that this never runs
        raise AssertionError("the deterministic write path called the model")


class _Sessions:
    """A session factory whose sessions are inert objects.

    Nothing under test opens a transaction — both commands and the audit sink
    are stubbed — so the session only has to be *something* a tool can be handed
    and a stub can compare. It records opens and closes, which is how the
    registry's "each tool opens its own session and closes it" rule stays
    asserted on the write path too.
    """

    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0
        self.session = object()

    def __call__(self) -> "_Sessions":
        return self

    async def __aenter__(self) -> Any:
        self.opened += 1
        return self.session

    async def __aexit__(self, *exc: Any) -> bool:
        self.closed += 1
        return False


class _NoEmbeddings:
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:  # pragma: no cover
        raise AssertionError("an approval test embedded something")


def _context(sessions: _Sessions, claim: str | None = CLAIM) -> CopilotContext:
    return CopilotContext(
        caller=HANDLER_CTX,
        sessionmaker=sessions,  # type: ignore[arg-type]
        claim_business_id=claim,
        embedding_client=_NoEmbeddings(),
        embedding_staleness_days=7,
    )


class _Audited:
    """Every `record_copilot_approval` this run made, in order.

    A list rather than a counter, because two of the ACs are about *which*
    outcome was recorded and one is about the row being content-free — and a
    counter can satisfy "exactly one row" while recording the wrong thing.
    """

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, db: Any, ctx: Any, **kwargs: Any) -> Any:
        self.rows.append({"caller": ctx, **kwargs})
        return None


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> _Audited:
    """The approval audit sink, captured.

    Patched on `services.audit` rather than on `agents.approval`, because that
    is the module the function lives in and `approval.py` reaches it through the
    package — so a future caller elsewhere is captured by the same fixture.
    """
    sink = _Audited()
    from services import audit as audit_module

    monkeypatch.setattr(audit_module, "record_copilot_approval", sink.record)
    return sink


class _Command:
    """A stubbed AD-4 command that records what it was asked to do.

    `raises` lets one line turn the command into the loser of a compare-and-swap
    race, which is how the stale branch is driven without a second database.
    """

    def __init__(self, raises: BaseException | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raises = raises

    async def __call__(self, db: Any, ctx: Any, claim_business_id: str, **kwargs: Any) -> Any:
        self.calls.append({"claim": claim_business_id, **kwargs})
        if self.raises is not None:
            raise self.raises
        from types import SimpleNamespace

        return SimpleNamespace(
            claim_id=claim_business_id,
            version=DRAFTED_VERSION + 1,
            documents=SimpleNamespace(documents=(object(),)),
        )


def _field_write(claim: str = CLAIM, version: int = DRAFTED_VERSION) -> AIMessage:
    """The model proposing a claim-field edit — the free-chat write origin."""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "update_claim_field",
                "args": {
                    "claim_business_id": claim,
                    "field": "icd_desc",
                    "value": "Laceration of left hand",
                    "expected_version": version,
                },
                "id": "call-write-1",
            }
        ],
    )


def _letter_proposal(
    version: int = DRAFTED_VERSION,
    *,
    body: str = LETTER,
    proposal_id: str | None = None,
) -> ProposedWrite:
    """The RTW modal's save, as the runs endpoint builds it.

    `proposal_id` is minted per request there (`_proposed_write`), so it is
    minted per call here — and it is what makes a *second* save on one thread a
    second proposal rather than a lookup that finds the first letter's
    checkpointed tool call and reports success without gating anything.
    """
    return ProposedWrite(
        tool_name="save_rtw_letter",
        proposal_id=proposal_id or str(uuid4()),
        arguments={
            "claim_business_id": CLAIM,
            "name": "Return-to-work offer letter",
            "body_text": body,
            "expected_version": version,
        },
    )


def _letter_write(
    claim: str = CLAIM, version: int = DRAFTED_VERSION, call_id: str = "call-write-2"
) -> AIMessage:
    """The model proposing a letter — the *second* write tool, for the batch test."""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "save_rtw_letter",
                "args": {
                    "claim_business_id": claim,
                    "name": "Return-to-work offer letter",
                    "body_text": LETTER,
                    "expected_version": version,
                },
                "id": call_id,
            }
        ],
    )


def _both_writes() -> AIMessage:
    """One `AIMessage` calling **both** write tools — AD-6's batching hazard.

    The per-tool `ToolCallLimitMiddleware`s cannot see this: each counts only
    the tool it names, so both calls pass both limiters and reach
    `HumanInTheLoopMiddleware` together.
    """
    field = _field_write()
    letter = _letter_write()
    return AIMessage(content="", tool_calls=[*field.tool_calls, *letter.tool_calls])


def _graph(model: BaseChatModel) -> Any:
    return build_graph(
        model=model,
        checkpointer=InMemorySaver(),
        max_tool_calls=5,
        tools=list(build_tools()),
    )


def _script(*messages: AIMessage) -> Iterator[AIMessage]:
    yield from messages
    while True:  # pragma: no cover - a turn that asks for more than it was given
        yield AIMessage(content=REPLY)


async def _pause(graph: Any, thread: str, *, inputs: dict[str, Any], sessions: _Sessions) -> Any:
    """Run until the gate pauses, and return the pending interrupt's payload."""
    await graph.ainvoke(inputs, config=saver_config(thread), context=_context(sessions))
    state = await graph.aget_state(saver_config(thread))
    interrupts = [item for task in state.tasks for item in task.interrupts]
    assert len(interrupts) == 1, "the run did not pause on exactly one approval"
    return interrupts[0].value


async def _paused_values(graph: Any, thread: str) -> dict[str, Any]:
    """The state of the node that is paused — **the subgraph's, not the outer graph's**.

    `grounded_chat` *is* a compiled graph (that is what `create_agent` returns),
    and a subgraph that has not finished has not merged anything into its
    parent. So while an approval pends, `pending_approval` exists only one level
    down, under the paused task — which is exactly where a reviewer reading a
    checkpoint would find it, and exactly where it has to be for the pause to
    survive a restart. `subgraphs=True` is the vendor's way of walking there,
    and it is the same flag the runs endpoint passes `astream` for the same
    structural reason.
    """
    outer = await graph.aget_state(saver_config(thread), subgraphs=True)
    inner = [task.state for task in outer.tasks if getattr(task, "state", None) is not None]
    assert inner, "the paused task carried no state to read"
    values: dict[str, Any] = dict(inner[0].values)
    return values


async def _resume(graph: Any, thread: str, decision: dict[str, Any], sessions: _Sessions) -> Any:
    """Answer the pending approval, exactly as the runs endpoint does.

    `Command(resume=…, update=…)` rather than a bare `Command(resume=…)`: the
    bare form discards state inputs, which is how a resume once threw away the
    freshly resolved caller (review of Story 6.3). This is the shape
    `api/routers/copilot.py` builds, spelled the same way.
    """
    return await graph.ainvoke(
        Command(resume={"decisions": [decision]}, update=dict(resume_inputs(caller=HANDLER_CTX))),
        config=saver_config(thread),
        context=_context(sessions),
    )


@pytest.fixture
def field_command(monkeypatch: pytest.MonkeyPatch) -> _Command:
    command = _Command()
    import agents.tools.claim_write as tool_module

    monkeypatch.setattr(tool_module, "update_claim_fields", command)
    return command


@pytest.fixture
def letter_command(monkeypatch: pytest.MonkeyPatch) -> _Command:
    command = _Command()
    import agents.tools.documents as tool_module

    monkeypatch.setattr(tool_module, "create_document", command)
    return command


# --- the pause -----------------------------------------------------------


async def test_a_proposed_write_pauses_and_records_what_pends(
    field_command: _Command, audited: _Audited
) -> None:
    """AC 1: the run pauses, and `pending_approval` records the drafted call.

    Three things at once, and they only mean something together: the graph
    stopped, the channel holds the tool call *including the version it was
    drafted against*, and the command was never reached. The third is what makes
    the first two a gate rather than a delay.
    """
    sessions = _Sessions()
    model = _ScriptedModel(script=_script(_field_write(), AIMessage(content=REPLY)))
    graph = _graph(model)

    payload = await _pause(
        graph,
        "t-pause",
        inputs=run_inputs(caller=HANDLER_CTX, claim_business_id=CLAIM, message="fix the ICD text"),
        sessions=sessions,
    )

    assert [action["name"] for action in payload["action_requests"]] == ["update_claim_field"]
    assert payload["review_configs"][0]["allowed_decisions"] == ["approve", "edit", "reject"]

    pending = (await _paused_values(graph, "t-pause"))["pending_approval"]
    assert pending["tool_name"] == "update_claim_field"
    assert pending["tool_call_id"] == "call-write-1"
    # The drafted version rides *inside* the arguments, because that is what it
    # is — see `PendingApproval`. `identity` is the projection an `edit` is
    # checked against, not a second copy.
    assert pending["arguments"]["expected_version"] == DRAFTED_VERSION
    assert pending["identity"] == {
        "claim_business_id": CLAIM,
        "expected_version": DRAFTED_VERSION,
    }

    assert field_command.calls == [], "the command ran before anybody approved it"
    assert audited.rows == [], "a decision was audited before one was taken"


async def test_both_write_origins_produce_one_interrupt_shape(
    field_command: _Command, letter_command: _Command, audited: _Audited
) -> None:
    """AC 1, the way the story asks for it: **by comparing the two frames**.

    A free-chat write and the RTW letter's deterministic save pause on the same
    payload *shape* and offer the same decisions. Asserting the shape twice
    would pass against two payloads that had drifted into agreeing about the
    keys a test happened to name; comparing them cannot.

    The values differ, of course — different tool, different arguments — so what
    is compared is the structure: the keys of the request, the keys of each
    action request, and the decision set. That is exactly the surface a client
    renders and resumes through.
    """
    sessions = _Sessions()
    chat_graph = _graph(_ScriptedModel(script=_script(_field_write(), AIMessage(content=REPLY))))
    chat_payload = await _pause(
        chat_graph,
        "t-shape-chat",
        inputs=run_inputs(caller=HANDLER_CTX, claim_business_id=CLAIM, message="fix the ICD text"),
        sessions=sessions,
    )

    # **A model that raises if it is called.** The save path may not call one.
    letter_graph = _graph(_NeverCalledModel())
    letter_payload = await _pause(
        letter_graph,
        "t-shape-letter",
        inputs=run_inputs(
            caller=HANDLER_CTX,
            claim_business_id=CLAIM,
            message="Save the return-to-work letter to this claim.",
            proposed_write=_letter_proposal(),
        ),
        sessions=sessions,
    )

    assert set(chat_payload) == set(letter_payload) == {"action_requests", "review_configs"}
    assert len(chat_payload["action_requests"]) == len(letter_payload["action_requests"]) == 1
    assert set(chat_payload["action_requests"][0]) == set(letter_payload["action_requests"][0])
    assert set(chat_payload["review_configs"][0]) == set(letter_payload["review_configs"][0])
    assert (
        chat_payload["review_configs"][0]["allowed_decisions"]
        == letter_payload["review_configs"][0]["allowed_decisions"]
    )
    # …and the letter's payload is the handler's own text, unregenerated.
    assert letter_payload["action_requests"][0]["args"]["body_text"] == LETTER
    assert letter_payload["action_requests"][0]["name"] == "save_rtw_letter"


# --- the three decisions -------------------------------------------------


async def test_approving_executes_the_write_once_and_audits_it_content_free(
    field_command: _Command, audited: _Audited
) -> None:
    """AC 2: the command runs, CAS-ed on the drafted version, and one row is written.

    The audit assertion is the fussy half and the one the AC actually names: the
    row must contain **no drafted value**, so the whole recorded call is checked
    for the field, the value and the letter rather than just its `after` being
    `None`.
    """
    sessions = _Sessions()
    model = _ScriptedModel(script=_script(_field_write(), AIMessage(content=REPLY)))
    graph = _graph(model)
    await _pause(
        graph,
        "t-approve",
        inputs=run_inputs(caller=HANDLER_CTX, claim_business_id=CLAIM, message="fix the ICD text"),
        sessions=sessions,
    )

    await _resume(graph, "t-approve", {"type": "approve"}, sessions)

    assert len(field_command.calls) == 1
    written = field_command.calls[0]
    assert written["claim"] == CLAIM
    assert written["expected_version"] == DRAFTED_VERSION
    assert written["patch"] == {"icd_desc": "Laceration of left hand"}

    assert len(audited.rows) == 1
    row = audited.rows[0]
    assert row["outcome"] == "approved"
    assert row["claim_business_id"] == CLAIM
    assert row["caller"] is HANDLER_CTX
    assert set(row) == {"caller", "outcome", "claim_business_id"}
    assert "icd_desc" not in str(row) and "Laceration" not in str(row)

    state = await graph.aget_state(saver_config("t-approve"))
    assert state.values["pending_approval"] is None
    assert state.values["approved_tool_call_id"] is None, "the marker outlived its tool call"


async def test_rejecting_writes_nothing_and_audits_exactly_one_row(
    field_command: _Command, audited: _Audited
) -> None:
    """AC 3: no entity write, `pending_approval` cleared, one audit row and nothing else."""
    sessions = _Sessions()
    model = _ScriptedModel(
        script=_script(_field_write(), AIMessage(content="Cancelled — nothing was written."))
    )
    graph = _graph(model)
    await _pause(
        graph,
        "t-reject",
        inputs=run_inputs(caller=HANDLER_CTX, claim_business_id=CLAIM, message="fix the ICD text"),
        sessions=sessions,
    )

    await _resume(graph, "t-reject", {"type": "reject"}, sessions)

    assert field_command.calls == []
    assert [row["outcome"] for row in audited.rows] == ["rejected"]

    state = await graph.aget_state(saver_config("t-reject"))
    assert state.values["pending_approval"] is None
    assert state.values["write_outcome"] == "rejected"
    assert not state.tasks


async def test_an_edit_of_a_non_identity_argument_executes_the_revision(
    field_command: _Command, audited: _Audited
) -> None:
    """AD-6's `edit`, in the direction it is permitted.

    The handler revises *what the write says* — the value — and the write runs
    with their revision rather than with the model's. The audit row says
    `edited`, not `approved`, because those are two different things a
    compliance reader may need to tell apart.
    """
    sessions = _Sessions()
    model = _ScriptedModel(script=_script(_field_write(), AIMessage(content=REPLY)))
    graph = _graph(model)
    await _pause(
        graph,
        "t-edit",
        inputs=run_inputs(caller=HANDLER_CTX, claim_business_id=CLAIM, message="fix the ICD text"),
        sessions=sessions,
    )

    await _resume(
        graph,
        "t-edit",
        {
            "type": "edit",
            "edited_action": {
                "name": "update_claim_field",
                "args": {
                    "claim_business_id": CLAIM,
                    "field": "icd_desc",
                    "value": "Deep laceration, left hand",
                    "expected_version": DRAFTED_VERSION,
                },
            },
        },
        sessions,
    )

    assert len(field_command.calls) == 1
    assert field_command.calls[0]["patch"] == {"icd_desc": "Deep laceration, left hand"}
    assert [row["outcome"] for row in audited.rows] == ["edited"]


@pytest.mark.parametrize(
    ("moved", "value"),
    [("claim_business_id", "WC-20018"), ("expected_version", DRAFTED_VERSION + 1)],
)
async def test_an_edit_that_moves_the_target_or_the_version_is_refused(
    field_command: _Command, audited: _Audited, moved: str, value: object
) -> None:
    """AC 5: an `edit` may revise what a write says, never what it is written to.

    Both identity arguments, because they fail differently in production and
    identically here: moving the claim writes to the wrong file, and moving the
    version force-writes over a claim that has changed. AD-6 forbids both in one
    sentence and this refuses both in one branch.

    Audited as a **rejection**, which is the honest record: the human expressed
    an intent the gate could not honour, and nothing was written.
    """
    sessions = _Sessions()
    model = _ScriptedModel(script=_script(_field_write(), AIMessage(content=REPLY)))
    graph = _graph(model)
    await _pause(
        graph,
        f"t-edit-{moved}",
        inputs=run_inputs(caller=HANDLER_CTX, claim_business_id=CLAIM, message="fix the ICD text"),
        sessions=sessions,
    )

    args: dict[str, Any] = {
        "claim_business_id": CLAIM,
        "field": "icd_desc",
        "value": "Laceration of left hand",
        "expected_version": DRAFTED_VERSION,
    }
    args[moved] = value
    await _resume(
        graph,
        f"t-edit-{moved}",
        {"type": "edit", "edited_action": {"name": "update_claim_field", "args": args}},
        sessions,
    )

    assert field_command.calls == [], "a refused edit reached the command"
    assert [row["outcome"] for row in audited.rows] == ["rejected"]
    state = await graph.aget_state(saver_config(f"t-edit-{moved}"))
    assert state.values["write_outcome"] == "edit_refused"
    assert state.values["pending_approval"] is None


async def test_an_edit_that_renames_the_tool_is_refused(
    field_command: _Command, letter_command: _Command, audited: _Audited
) -> None:
    """AC 5, in the direction an `edit` makes cheapest: a **different write**.

    The vendored middleware builds the revised call from `edited_action["name"]`
    and `awrap_tool_call` then resolves the registry entry from that revised
    name — so a decision that renamed `update_claim_field` to `save_rtw_letter`
    executed a tool the card never showed, under the marker a handler minted for
    the one it did. The card's whole promise is "this is what will execute", so
    a decision naming a different tool is refused before anything runs.

    Both commands are stubbed, so "neither ran" is an assertion about two
    recorded call lists rather than about the absence of an exception — a
    refusal that merely failed differently would satisfy one of them.
    """
    sessions = _Sessions()
    model = _ScriptedModel(script=_script(_field_write(), AIMessage(content=REPLY)))
    graph = _graph(model)
    await _pause(
        graph,
        "t-edit-rename",
        inputs=run_inputs(caller=HANDLER_CTX, claim_business_id=CLAIM, message="fix the ICD text"),
        sessions=sessions,
    )

    await _resume(
        graph,
        "t-edit-rename",
        {
            "type": "edit",
            "edited_action": {
                # A different tool entirely, with arguments that would validate
                # for it — the identity arguments are untouched, which is what
                # made the value-only guard pass this.
                "name": "save_rtw_letter",
                "args": {
                    "claim_business_id": CLAIM,
                    "name": "Return-to-work offer letter",
                    "body_text": LETTER,
                    "expected_version": DRAFTED_VERSION,
                },
            },
        },
        sessions,
    )

    assert field_command.calls == []
    assert letter_command.calls == [], "an edit executed a tool the card never showed"
    assert [row["outcome"] for row in audited.rows] == ["rejected"]
    state = await graph.aget_state(saver_config("t-edit-rename"))
    assert state.values["write_outcome"] == "edit_refused"
    assert state.values["pending_approval"] is None


async def test_an_edit_that_adds_an_identity_argument_the_draft_omitted_is_refused(
    letter_command: _Command, audited: _Audited
) -> None:
    """AC 5, in the direction a value comparison could not see.

    `_identity_of` projects only the identity keys **present** in the drafted
    arguments, and the guard compared values for keys in that projection — so an
    `edit` supplying an `expected_version` the draft never carried was compared
    against nothing and passed, pinning a version the handler never saw on the
    card they approved. Key *sets* are compared now, before any value is.

    Driven from a drafted call that genuinely omits `expected_version`, which is
    what a model that forgot the field looks like. The write is refused twice
    over in production — the schema would reject it too — and this asserts the
    gate's own refusal, which is the one that has to happen *before* a handler
    is asked anything.
    """
    sessions = _Sessions()
    incomplete = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "save_rtw_letter",
                "args": {
                    "claim_business_id": CLAIM,
                    "name": "Return-to-work offer letter",
                    "body_text": LETTER,
                },
                "id": "call-write-noversion",
            }
        ],
    )
    model = _ScriptedModel(script=_script(incomplete, AIMessage(content=REPLY)))
    graph = _graph(model)

    await graph.ainvoke(
        run_inputs(caller=HANDLER_CTX, claim_business_id=CLAIM, message="file the letter"),
        config=saver_config("t-edit-add"),
        context=_context(sessions),
    )

    # The proposal never reached a handler: arguments that would not validate
    # are refused before the pause (see the next test), so there is nothing to
    # resume and nothing was written.
    state = await graph.aget_state(saver_config("t-edit-add"))
    assert not state.tasks, "an unvalidatable write was put to a handler"
    assert letter_command.calls == []
    assert audited.rows == []
    tool_messages = [m for m in state.values["messages"] if getattr(m, "type", "") == "tool"]
    assert any(approval.INVALID_WRITE_ARGUMENTS_ERROR in str(m.content) for m in tool_messages)


async def test_an_edit_may_not_add_or_remove_an_identity_key() -> None:
    """The key-set half of the guard, at the unit the graph cannot reach.

    The graph test above shows that a draft *missing* an identity argument never
    reaches a handler at all, because the schema pre-check refuses it first —
    which is the right production behaviour and is why the key-set comparison
    has no graph-level route left. It is still the guard that has to hold: a
    future write tool whose schema makes `expected_version` optional would put
    a draft with no pin in front of a handler, and an `edit` adding one would
    then be an approval for a version nobody saw.

    So the two directions are asserted directly against the branch: adding a
    key the draft did not carry, and removing one it did.
    """
    refuse = approval.ApprovalOutcomeMiddleware._identity_refusal
    runtime = SimpleNamespace(context=SimpleNamespace(claim_business_id=CLAIM))
    drafted = {"claim_business_id": CLAIM}

    added = refuse(
        runtime,  # type: ignore[arg-type]
        "save_rtw_letter",
        "save_rtw_letter",
        {"claim_business_id": CLAIM, "expected_version": DRAFTED_VERSION},
        drafted,
    )
    assert added is not None and "identity argument" in added

    removed = refuse(
        runtime,  # type: ignore[arg-type]
        "update_claim_field",
        "update_claim_field",
        {"field": "icd_desc"},
        {"claim_business_id": CLAIM, "expected_version": DRAFTED_VERSION},
    )
    assert removed is not None and "identity argument" in removed

    # …and the honest control: the same shape, unaltered, is not a refusal.
    assert (
        refuse(
            runtime,  # type: ignore[arg-type]
            "update_claim_field",
            "update_claim_field",
            {"claim_business_id": CLAIM, "expected_version": DRAFTED_VERSION, "field": "icd_desc"},
            {"claim_business_id": CLAIM, "expected_version": DRAFTED_VERSION},
        )
        is None
    )


async def test_two_write_calls_in_one_turn_produce_one_interrupt(
    field_command: _Command, letter_command: _Command, audited: _Audited
) -> None:
    """AD-6's single pending write, against the batching the limiters cannot see.

    `agents/graph.py` installs one `ToolCallLimitMiddleware` per write tool, and
    the vendor's filter makes each count only its own — so one `AIMessage`
    calling both write tools passes both limiters, `HumanInTheLoopMiddleware`
    emits **two** `action_requests`, and `WriteProposalMiddleware` records one.
    The panel refuses any payload that does not carry exactly one action, so no
    card rendered and the thread 409-ed every later message for ever: a
    conversation wedged by a model emitting two tool calls.

    So: exactly one action request, and it is the *first* call — the model gets
    the write it asked for first, not whichever the loop reached last. The
    second is answered rather than left hanging, which is what keeps the agent
    loop from routing it to the tool node.
    """
    sessions = _Sessions()
    model = _ScriptedModel(script=_script(_both_writes(), AIMessage(content=REPLY)))
    graph = _graph(model)

    payload = await _pause(
        graph,
        "t-batch",
        inputs=run_inputs(caller=HANDLER_CTX, claim_business_id=CLAIM, message="do both"),
        sessions=sessions,
    )

    assert len(payload["action_requests"]) == 1
    assert payload["action_requests"][0]["name"] == "update_claim_field"

    pending = (await _paused_values(graph, "t-batch"))["pending_approval"]
    assert pending["tool_call_id"] == "call-write-1"

    await _resume(graph, "t-batch", {"type": "approve"}, sessions)

    assert len(field_command.calls) == 1
    assert letter_command.calls == [], "the dropped write executed anyway"
    state = await graph.aget_state(saver_config("t-batch"))
    assert not state.tasks, "the thread stayed paused after its one decision"
    refusals = [
        m
        for m in state.values["messages"]
        if getattr(m, "type", "") == "tool" and m.tool_call_id == "call-write-2"
    ]
    # Parsed rather than substring-matched: the refusal is the same
    # `{ok, error}` envelope every tool answers with, and `json.dumps` escapes
    # the sentence's em dash — so a substring test would be asserting against
    # the serialisation rather than against the message.
    assert refusals
    assert json.loads(str(refusals[0].content)) == {
        "ok": False,
        "error": approval.SECOND_WRITE_ERROR,
    }
    assert [row["outcome"] for row in audited.rows] == ["approved"]


async def test_a_write_whose_arguments_do_not_validate_never_reaches_the_card(
    field_command: _Command, audited: _Audited
) -> None:
    """AC 5's premise: the card promises what will execute, so it must be able to.

    `registry.invoke` validates arguments **after** the approval gate, and that
    ordering is load-bearing — the marker-less raise has to come first. But it
    meant a card could render an argument set the registry would then refuse:
    the handler approved a write, and a validation failure came back instead.
    Refusing the proposal outright keeps the promise true.

    The claim id is an integer here, which no `WriteArgs` schema accepts. The
    tool still validates; this asserts that a handler is never asked about
    something that cannot happen.
    """
    sessions = _Sessions()
    nonsense = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "update_claim_field",
                "args": {"claim_business_id": 17, "field": "icd_desc", "value": "x"},
                "id": "call-write-bad",
            }
        ],
    )
    model = _ScriptedModel(script=_script(nonsense, AIMessage(content=REPLY)))
    graph = _graph(model)

    await graph.ainvoke(
        run_inputs(caller=HANDLER_CTX, claim_business_id=CLAIM, message="fix the ICD text"),
        config=saver_config("t-bad-args"),
        context=_context(sessions),
    )

    state = await graph.aget_state(saver_config("t-bad-args"))
    assert not state.tasks, "an unvalidatable write paused the thread"
    assert state.values["pending_approval"] is None
    assert field_command.calls == []
    assert audited.rows == [], "a decision was audited for a proposal nobody was shown"
    tool_messages = [m for m in state.values["messages"] if getattr(m, "type", "") == "tool"]
    assert any(approval.INVALID_WRITE_ARGUMENTS_ERROR in str(m.content) for m in tool_messages)
    # AD-11: the refusal names the rule, never the value that broke it.
    assert all("icd_desc" not in str(m.content) for m in tool_messages)


async def test_a_write_already_refused_by_the_tool_budget_is_not_put_to_a_handler(
    field_command: _Command, audited: _Audited
) -> None:
    """A call with a result before the tool ran is a refusal, not a proposal.

    `ToolCallLimitMiddleware`'s `"continue"` behaviour blocks an over-budget
    write, writes an error `ToolMessage` for it, and leaves the call on the
    `AIMessage`. The gate then interrupted on it anyway — so a handler saw a
    card for a write that could never run, and pressing Approve recorded a
    **rejection**, because "a result exists before the tool ran" is exactly how
    `ApprovalOutcomeMiddleware` recognises a refusal. `interrupt_config`'s `when`
    predicate skips such a call now.

    Driven by two calls to the same write tool in one turn, which is what the
    per-tool `run_limit=1` refuses.
    """
    sessions = _Sessions()
    twice = AIMessage(
        content="",
        tool_calls=[
            *_field_write().tool_calls,
            {
                "name": "update_claim_field",
                "args": {
                    "claim_business_id": CLAIM,
                    "field": "icd_desc",
                    "value": "Something else entirely",
                    "expected_version": DRAFTED_VERSION,
                },
                "id": "call-write-over-budget",
            },
        ],
    )
    model = _ScriptedModel(script=_script(twice, AIMessage(content=REPLY)))
    graph = _graph(model)

    payload = await _pause(
        graph,
        "t-budget",
        inputs=run_inputs(caller=HANDLER_CTX, claim_business_id=CLAIM, message="fix it twice"),
        sessions=sessions,
    )
    assert len(payload["action_requests"]) == 1
    assert payload["action_requests"][0]["args"]["value"] == "Laceration of left hand"

    await _resume(graph, "t-budget", {"type": "approve"}, sessions)

    # The decision the handler took is the decision that was recorded.
    assert [row["outcome"] for row in audited.rows] == ["approved"]
    assert len(field_command.calls) == 1
    assert field_command.calls[0]["patch"] == {"icd_desc": "Laceration of left hand"}


# --- failing safe --------------------------------------------------------


async def test_a_stale_approval_writes_nothing_and_says_the_claim_changed(
    monkeypatch: pytest.MonkeyPatch, audited: _Audited
) -> None:
    """AC 4 / AC 11: the claim moved between the draft and the approval.

    The command is the loser of its own compare-and-swap — a real `StaleClaim`,
    which is what `update_claim_fields` raises when its `WHERE … AND version = ?`
    matches no rows. What the gate must do with that is *nothing*: no retry, no
    re-read-and-write, no merge.

    Asserted by what the command recorded, not by an exception reaching the
    test: a fail-safe that swallowed the error and a fail-safe that force-wrote
    both look like "no exception" from outside.
    """
    from types import SimpleNamespace

    from services.claims.edit import StaleClaim

    fresh = SimpleNamespace(claim_id=CLAIM, version=DRAFTED_VERSION + 1)
    command = _Command(raises=StaleClaim(fresh))  # type: ignore[arg-type]
    import agents.tools.claim_write as tool_module

    monkeypatch.setattr(tool_module, "update_claim_fields", command)

    sessions = _Sessions()
    model = _ScriptedModel(
        script=_script(
            _field_write(),
            AIMessage(content="The claim changed since this was drafted; nothing was written."),
        )
    )
    graph = _graph(model)
    await _pause(
        graph,
        "t-stale",
        inputs=run_inputs(caller=HANDLER_CTX, claim_business_id=CLAIM, message="fix the ICD text"),
        sessions=sessions,
    )
    await _resume(graph, "t-stale", {"type": "approve"}, sessions)

    # The command was *called* — that is what a compare-and-swap is — and it
    # wrote nothing, which its own tests assert against a real database. What
    # this asserts is that the gate did not then try again with a fresher
    # version, which is the force-write AD-6 forbids by name.
    assert len(command.calls) == 1
    assert command.calls[0]["expected_version"] == DRAFTED_VERSION

    state = await graph.aget_state(saver_config("t-stale"))
    assert state.values["pending_approval"] is None
    assert not state.tasks
    tool_messages = [m for m in state.values["messages"] if getattr(m, "type", "") == "tool"]
    assert any("changed since" in str(m.content) for m in tool_messages)


async def test_a_resume_whose_run_is_about_another_claim_fails_safe(
    field_command: _Command, audited: _Audited
) -> None:
    """AC 6's scope half (AD-7): the caller is re-resolved, and the write is not.

    The resume arrives under a context bound to a *different* claim — which is
    what a re-resolved scope that no longer covers the drafted target looks like
    from inside the graph. The proposal is discarded exactly like a stale one:
    nothing is written, one audit row records the decision, and the marker is
    never minted.
    """
    sessions = _Sessions()
    model = _ScriptedModel(script=_script(_field_write(), AIMessage(content=REPLY)))
    graph = _graph(model)
    await _pause(
        graph,
        "t-scope",
        inputs=run_inputs(caller=HANDLER_CTX, claim_business_id=CLAIM, message="fix the ICD text"),
        sessions=sessions,
    )

    await graph.ainvoke(
        Command(
            resume={"decisions": [{"type": "approve"}]},
            update=dict(resume_inputs(caller=HANDLER_CTX)),
        ),
        config=saver_config("t-scope"),
        # The run's context, re-resolved — and no longer this claim's.
        context=_context(sessions, claim="WC-20099"),
    )

    assert field_command.calls == [], "a write executed against a claim the run had left"
    assert [row["outcome"] for row in audited.rows] == ["rejected"]
    state = await graph.aget_state(saver_config("t-scope"))
    assert state.values["write_outcome"] == "edit_refused"


# --- the injection-shaped unrequested write -------------------------------


async def test_an_unrequested_write_pauses_at_the_same_gate(
    field_command: _Command, audited: _Audited
) -> None:
    """AC 6: the stub proposes a write nobody asked for, and it changes nothing.

    The handler's message is a *question*. The model — scripted here as an
    injected one would behave — answers it by calling a write tool. AD-16's
    containment floor says the blast radius of that is bounded by the gate, not
    by detecting it: so the run pauses, the route is unchanged, the claim the
    tool was confined to is unchanged, and rejecting it leaves every row
    untouched.
    """
    sessions = _Sessions()
    model = _ScriptedModel(
        script=_script(_field_write(), AIMessage(content="Cancelled — nothing was written."))
    )
    graph = _graph(model)
    payload = await _pause(
        graph,
        "t-injection",
        inputs=run_inputs(
            caller=HANDLER_CTX,
            claim_business_id=CLAIM,
            message="What does the treating clinician say about lifting?",
        ),
        sessions=sessions,
    )

    assert payload["action_requests"][0]["name"] == "update_claim_field"
    state = await graph.aget_state(saver_config("t-injection"))
    assert state.values["route"] == "grounded_chat", "an unrequested write moved the route"
    assert state.values["claim_business_id"] == CLAIM

    await _resume(graph, "t-injection", {"type": "reject"}, sessions)
    assert field_command.calls == []
    assert [row["outcome"] for row in audited.rows] == ["rejected"]


async def test_a_write_reaching_the_registry_without_a_marker_raises() -> None:
    """AC 7: defence in depth, on a *registered* write rather than a fixture one.

    `tests/test_copilot_graph.py` asserts the same raise against a synthetic
    entry, which was the only thing it could do while nothing registered was a
    write. This asserts it against `save_rtw_letter` — the real entry, the real
    schema — and it is the path `agents/qas.py::_call` takes on every quick
    action, since a QAS node passes an empty marker set by design.

    **Before argument validation**, which is the ordering `invoke`'s docstring
    calls load-bearing: the arguments here are deliberately nonsense, and an
    unapproved write must be refused whether or not the model got them right.
    """
    with pytest.raises(WriteNotApproved):
        await invoke(
            REGISTRY["save_rtw_letter"],
            caller=HANDLER_CTX,
            session=object(),  # type: ignore[arg-type]
            context=_context(_Sessions()),
            thread_claim_business_id=CLAIM,
            approved_tool_call_ids=frozenset(),
            tool_call_id="call-1",
            arguments={"nothing": "valid"},
        )


async def test_a_tool_body_is_handed_the_id_of_the_call_it_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wiring half of AC 7's id match: where the executing id comes from.

    `invoke`'s gate asks whether the marker was minted for *this* call, and the
    tool body used to answer both halves of that question with the marker — so
    it degenerated into "is `APPROVED_TOOL_CALL` set at all". The id now arrives
    through LangChain's `InjectedToolCallId`, off the `ToolCall` the tool node
    is executing.

    **Asserted on a read**, which is the one shape that tells the two apart
    without reaching around the middleware. A read never has a marker, so under
    the old wiring its `tool_call_id` was `None` on every call; under the new
    one it is the call's own id. Everything else about the run is identical, so
    a non-`None` id here can only have come from the call.
    """
    seen: list[Any] = []
    real = registry.invoke

    async def recording(entry: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("tool_call_id"))
        return {"ok": True, "data": {}, "display": {}}

    monkeypatch.setattr(registry, "invoke", recording)
    assert real is not recording  # the patch replaced something that existed

    read = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "claim_reader",
                "args": {"claim_business_id": CLAIM},
                "id": "call-read-1",
            }
        ],
    )
    graph = _graph(_ScriptedModel(script=_script(read, AIMessage(content=REPLY))))
    await graph.ainvoke(
        run_inputs(caller=HANDLER_CTX, claim_business_id=CLAIM, message="what is on file?"),
        config=saver_config("t-read-id"),
        context=_context(_Sessions()),
    )

    assert seen == ["call-read-1"]


async def test_a_marker_for_one_call_does_not_authorise_another() -> None:
    """AC 7's other half: the id match is a match, not a presence check.

    The tool body used to pass the *marker* as the executing call's id, so
    `invoke` compared a value against a set built from that same value: the gate
    degenerated into "is `APPROVED_TOOL_CALL` set at all", and an approval a
    handler gave for one call would have authorised whatever call happened to be
    executing under it. The id now comes off the `ToolCall` itself, through
    LangChain's `InjectedToolCallId`, so the two sides have independent sources.

    Asserted at `invoke`, which is where the raise is, and in the shape the
    substitution would take: a marker minted for `call-approved`, a call whose
    own id is `call-other`.
    """
    with pytest.raises(WriteNotApproved):
        await invoke(
            REGISTRY["save_rtw_letter"],
            caller=HANDLER_CTX,
            session=object(),  # type: ignore[arg-type]
            context=_context(_Sessions()),
            thread_claim_business_id=CLAIM,
            approved_tool_call_ids=frozenset({"call-approved"}),
            tool_call_id="call-other",
            arguments={
                "claim_business_id": CLAIM,
                "name": "Return-to-work offer letter",
                "body_text": LETTER,
                "expected_version": DRAFTED_VERSION,
            },
        )


# --- the deterministic save, end to end ----------------------------------


async def test_the_rtw_save_files_the_handlers_own_text_with_no_model_call(
    letter_command: _Command, audited: _Audited
) -> None:
    """AC 10: the letter that is filed is the one the handler edited.

    The model in this graph raises if it is asked for a completion, so "no model
    call" is enforced rather than counted — on the proposal *and* on the
    confirmation, which is the half a count would have missed. What reaches the
    command is the modal's text verbatim, pinned on the version the draft was
    composed against.
    """
    sessions = _Sessions()
    graph = _graph(_NeverCalledModel())
    await _pause(
        graph,
        "t-letter",
        inputs=run_inputs(
            caller=HANDLER_CTX,
            claim_business_id=CLAIM,
            message="Save the return-to-work letter to this claim.",
            proposed_write=_letter_proposal(),
        ),
        sessions=sessions,
    )

    await _resume(graph, "t-letter", {"type": "approve"}, sessions)

    assert len(letter_command.calls) == 1
    filed = letter_command.calls[0]
    assert filed["claim"] == CLAIM
    assert filed["body_text"] == LETTER
    assert filed["expected_version"] == DRAFTED_VERSION
    assert filed["name"] == "Return-to-work offer letter"
    assert [row["outcome"] for row in audited.rows] == ["approved"]

    state = await graph.aget_state(saver_config("t-letter"))
    assert not state.tasks
    assert state.values["pending_approval"] is None
    # The confirmation is committed code, not prose a model produced.
    assert any(
        approval.LETTER_SAVED_NOTE in str(getattr(m, "content", ""))
        for m in state.values["messages"]
    )


async def test_a_second_save_on_one_thread_is_proposed_and_gated_again(
    letter_command: _Command, audited: _Audited
) -> None:
    """The thread's second letter is a second proposal — not a silent no-op.

    **The defect this replaces was a false success.** The middleware asked "has
    this proposal already been drafted?" by scanning the whole checkpointed
    history for *any* `AIMessage` tool call named `save_rtw_letter`. The first
    save's call is in that history for ever, so every later save on the thread
    took the "already drafted" branch: it read the first letter's success
    envelope back out of the history, told the handler "The letter has been
    filed on this claim", raised no interrupt, wrote no audit row, inserted no
    `document` row — and discarded the letter they had just edited. It matched
    on the tool name alone, so a completely different letter to a different
    reader was answered by the first one's result.

    So the assertions are about the *second* save specifically: it pauses, its
    payload carries **its own** body, approving it reaches the command a second
    time with that body, and two decisions are on the audit trail.
    """
    sessions = _Sessions()
    graph = _graph(_NeverCalledModel())
    second = "Dear A. Kowalski,\n\nRevised: modified duty starts on the 4th.\n\nSincerely,"

    await _pause(
        graph,
        "t-two-saves",
        inputs=run_inputs(
            caller=HANDLER_CTX,
            claim_business_id=CLAIM,
            message="Save the return-to-work letter to this claim.",
            proposed_write=_letter_proposal(),
        ),
        sessions=sessions,
    )
    await _resume(graph, "t-two-saves", {"type": "approve"}, sessions)
    assert len(letter_command.calls) == 1

    payload = await _pause(
        graph,
        "t-two-saves",
        inputs=run_inputs(
            caller=HANDLER_CTX,
            claim_business_id=CLAIM,
            message="Save the return-to-work letter to this claim.",
            proposed_write=_letter_proposal(body=second),
        ),
        sessions=sessions,
    )
    # It paused at all — `_pause` asserts exactly one interrupt — and it paused
    # on **this** letter rather than on the one already filed.
    assert payload["action_requests"][0]["name"] == "save_rtw_letter"
    assert payload["action_requests"][0]["args"]["body_text"] == second

    await _resume(graph, "t-two-saves", {"type": "approve"}, sessions)

    assert len(letter_command.calls) == 2, "the second save was swallowed by the first"
    assert letter_command.calls[1]["body_text"] == second
    assert [row["outcome"] for row in audited.rows] == ["approved", "approved"]


async def test_rejecting_the_rtw_save_files_nothing_and_says_so(
    letter_command: _Command, audited: _Audited
) -> None:
    """The other half of the modal's round trip, with the model still forbidden.

    A rejected deterministic write has to produce a cancellation sentence from
    somewhere, and the somewhere may not be a model — the free-chat path lets
    the model narrate its own refusal, which is exactly what this path cannot
    do. `agents/approval.DEFAULT_REJECTED_NOTE` is the sentence, and it is
    committed code.
    """
    sessions = _Sessions()
    graph = _graph(_NeverCalledModel())
    await _pause(
        graph,
        "t-letter-reject",
        inputs=run_inputs(
            caller=HANDLER_CTX,
            claim_business_id=CLAIM,
            message="Save the return-to-work letter to this claim.",
            proposed_write=_letter_proposal(),
        ),
        sessions=sessions,
    )

    await _resume(graph, "t-letter-reject", {"type": "reject"}, sessions)

    assert letter_command.calls == []
    assert [row["outcome"] for row in audited.rows] == ["rejected"]
    state = await graph.aget_state(saver_config("t-letter-reject"))
    assert any(
        approval.DEFAULT_REJECTED_NOTE in str(getattr(m, "content", ""))
        for m in state.values["messages"]
    )
