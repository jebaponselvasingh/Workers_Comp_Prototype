"""The seven deterministic quick actions — one node each (Story 6.4, AD-14).

`agents/graph.py` owns the map from a quick-action key to a node name; this
module owns the nodes themselves and the shared composition they all use. The
two files are read together, and the split is the dependency direction: the
graph imports this, this imports the registry and the prompts, and nothing
imports the graph back.

## What a quick action is, mechanically

A key arrives on the `quick_action` channel. `route_entry` looks it up in a
static dict **before any model call** and returns a node name. That node calls
one or two registered read tools for its facts, composes a two-section user
message out of what they returned, and — for six of the seven — asks the chat
model to narrate it. The seventh, `data_alignment`, calls a tool and formats the
answer itself.

The determinism AD-14 asks for is therefore structural rather than behavioural:
a quick action reaches the same node every time because a dict said so, not
because a router model agreed with itself twice. What varies between two runs of
the same key is the model's prose about the same figures, which is the only
thing that is allowed to vary.

## The two sections, and which half is fenced

`agents/insights.py::_narrate` established the shape and this reuses it exactly.
The **system** message comes only from prompt files on disk, composed by
`agents/prompts.qas_system_message` on `copilot_system.md` (never `system.md`,
which ends by demanding a JSON object). The **user** message has two sections:

- `FIGURES_HEADING`, then plain unfenced lines. Everything here was computed or
  assigned by this system — a verdict, a formatted money string, a count, a
  threshold, a rule-document version. It is unfenced precisely because it is
  *not* somebody's text, and the system prompt attaches quoting authority to it.
- `MATERIAL_HEADING`, then one `agents/fencing.fence` item per piece of
  human-written text. Claim narrative, retrieved passages, clinical
  restrictions, a comparable claim's injury description.

What this does **not** reuse is the structured-output half. An insight is a
constrained completion into a schema with nowhere to put a number; a quick
action is streamed prose. So the AD-2 guarantee here rests on the other two
mechanisms: the prompt files state no figure at all, and every figure in the
figures section came out of a tool envelope — most of them out of its `display`
map, already formatted by the service that owns the number.

## Nothing here decides anything

The reserve verdict is whichever of `ReserveVerdict`'s five members the service
returned. The fraud variant is `siu_review or fraud_flagged`, computed in Python
below and stated to the model as an instruction about which variant to write —
`agents/insights.py:441`'s arrangement, and for its reason: a model asked to
choose its own outcome could write a confident low-risk confirmation on a
referable claim, and it would read exactly like a correct one. The neighbour
ranking is the vector search's. The staleness disclosure is
`agents/tools/similar.py::_disclosure`'s sentence, quoted verbatim and
re-derived nowhere.

## Fenced strings never reach a transcript

`claim_reader`'s `narrative` and this module's other fenced items carry
`<<<LINEWORKER-ITEM source="…">>>` delimiters. They exist to keep untrusted text
separable *inside a prompt*, and a node that rendered one into the transcript
would show a handler delimiter markup and would defeat the fence's only purpose.
`data_alignment` is the node most at risk, because it is the one that formats
tool output directly for a reader — so it is built from the envelope's **bare
scalars only** and never touches `narrative`.

## How a model-free node still streams

Every run must end with exactly one terminal event, and `done` requires that
some assistant content reached the wire (`api/routers/copilot.py::_terminal_for`
— a run that finished having said nothing is an `error`, because `done` is the
frame the panel reads as "your question was answered"). LangGraph's `messages`
stream mode carries *model* tokens, and `data_alignment` has none.

So a note that no model wrote goes out through LangGraph's custom stream —
`get_stream_writer()`, a payload keyed `QAS_NOTE`, which the runs endpoint turns
into an ordinary `messages` frame. It is an addition to the stream path rather
than a change to it: the client's vocabulary is untouched, the terminal decision
still lives in one place, and the same mechanism carries a tool-failure sentence
from any of the seven.

## A node holds no session across model time

Each `_call` below opens a session from `CopilotContext.sessionmaker`, uses it
for one registry invocation and closes it. That is `agents/registry.py`'s rule
and it matters more here than there: a quick action makes two tool calls and
then waits on a streamed completion, so a session held across the node would be
a pooled connection idle-in-transaction for the length of an answer.
"""

from collections.abc import Awaitable, Mapping, Sequence
from typing import Any, Protocol

import structlog
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime

from agents import prompts
from agents.context import CopilotContext
from agents.fencing import FIGURES_HEADING, MATERIAL_HEADING, fence
from agents.registry import REGISTRY, invoke
from agents.state import CopilotState

log = structlog.get_logger()

#: The key the RTW draft's structured pin travels under on the custom stream.
#:
#: **The one thing a quick action publishes that is not prose** (Story 6.5), and
#: it exists because the letter's save has to pin a version. `rtw_reader` reads
#: the claim's `version` at draft time; the handler then edits the letter in the
#: modal and approves a save that compare-and-swaps on that number — so if the
#: browser had to fetch a version of its own when the modal opened, a claim that
#: moved between the draft and the modal would be saved against the *newer*
#: version and the stale branch would never fire. That is the force-write this
#: story exists to prevent, arrived at by omission.
#:
#: It travels on the custom stream beside `QAS_NOTE` rather than as a new SSE
#: event, and the runs endpoint turns it into an ordinary `updates` frame: the
#: client's vocabulary is unchanged, and `updates` is already the frame that
#: carries "something happened in the graph that is not assistant text".
#:
#: Nothing here is claim content — a business id and an integer — so it is safe
#: on a wire that also carries a transcript, and there is no second copy of the
#: letter anywhere: the body the modal opens with is the transcript's, which is
#: the copy the handler has already read.
RTW_DRAFT = "rtw_draft"

#: The key a model-free note travels under on LangGraph's custom stream.
#:
#: One key rather than a shape per node, because the runs endpoint's job is to
#: turn it into a `messages` frame and it should not have to know which node
#: wrote it. Spelled here and read in `api/routers/copilot.py`; a constant
#: rather than a literal at both ends, for `MESSAGES_FRAME_PREFIX`'s reason.
QAS_NOTE = "qas_note"

#: The sentence every labour-law briefing ends with (Story 6.4, Task 2).
#:
#: The story's own wording — "informational only — not legal advice" — spelled
#: once, here, and used twice: `_build_labor_law` passes it to `_narrate` as the
#: trailer, and `agents/prompts/laborlaw.md` asks the model to end with the same
#: sentence so a compliant completion needs nothing appended. Two spellings
#: would mean the append never recognised the model's own attempt and every
#: briefing carried the disclaimer twice.
LEGAL_DISCLAIMER = "This briefing is informational only — not legal advice."


class QasNode(Protocol):
    """What a quick-action node is: LangGraph's node signature, narrowed.

    A `Protocol` rather than a `Callable` alias because `runtime` is
    **keyword-only** in the vendor's `_NodeWithRuntime` and `Callable` cannot
    say so — a positional-runtime node type-checks against a `Callable` alias
    and is then rejected by `add_node` at graph-compile time, which is a startup
    failure for a typo the type system was supposed to have caught.

    Narrowed to `CopilotContext` rather than left generic, so a node that read a
    field off some other context object is a mypy error here rather than an
    `AttributeError` inside a run.
    """

    def __call__(
        self, state: CopilotState, *, runtime: Runtime[CopilotContext]
    ) -> Awaitable[dict[str, Any]]: ...


class NarratingBuilder(Protocol):
    """A builder for a `requires_llm: True` key: it is handed a model and a prompt.

    Half of Story 6.6's structural half. `_BUILDERS` used to be one
    `Mapping[str, Callable[..., QasNode]]`, and that `...` erased the whole
    pairing the map exists to declare: every builder was handed a model, whether
    or not its entry said it needed one, and `prompt_key` arrived as `str | None`
    at builders that require a `str`. The review of Story 6.4 recorded the
    erasure in `deferred-work.md` and named this story as the one that would
    feel it.

    Split in two, the flag becomes a type. A narrating builder's signature says
    it takes a model and a *non-optional* prompt key; a deterministic one's says
    it takes neither. See `DeterministicBuilder` for what that buys.
    """

    def __call__(self, *, model: BaseChatModel, prompt_key: str) -> QasNode: ...


class DeterministicBuilder(Protocol):
    """A builder for a `requires_llm: False` key: **it is handed no model at all**.

    The other half, and the one that makes AD-14's false path honest by types
    rather than by a promise. `data_alignment` genuinely calls no model today and
    `tests/test_copilot_qas.py` asserts it — but nothing prevented the next
    `_narrate(...)` from being added inside its builder, because the builder was
    holding a `BaseChatModel` it had been handed "so that every entry in the map
    is built the same way". A model in scope is an invitation with a comment
    beside it asking readers not to accept.

    Not handing it one makes the mistake a mypy error at the moment it is
    written, in the builder, rather than an outage the e2e degradation spec
    discovers three stories later. The existing test becomes the belt to this
    braces: types stop the model being *reachable*, the test stops it being
    *called*, and neither alone is the guarantee.
    """

    def __call__(self) -> QasNode: ...


#: What a node says when the thread has no claim to ask about.
#:
#: Unreachable in v1 — every thread is minted against a claim and
#: `CopilotContext.claim_business_id` is `None` only for the reserved
#: `dashboard` scope (Epic 7). It is written anyway rather than asserted,
#: because the alternative when Epic 7 arrives is an `AttributeError` inside a
#: node becoming one `error` frame with nothing in it that says why.
NO_CLAIM_NOTE = (
    "This conversation is not about a single claim, so this quick action has "
    "nothing to look up. Open the copilot on a claim and try again."
)

#: What a node says when the model narrated nothing at all.
#:
#: A completion can end having produced no `str` content: an empty stream, or
#: chunks whose `content` is a list of blocks rather than a string (LangChain's
#: other shape, which `_narrate` deliberately does not stringify — see there).
#: The accumulated answer is then `""`, and returning that as an `AIMessage`
#: checkpointed a blank turn *and* made the run terminate `error` at the
#: endpoint after every node had succeeded, because `_terminal_for` asks whether
#: any assistant content reached the client. The review of Story 6.3 fixed this
#: same class on the free-text path; a quick action is the other half.
#:
#: It ends with the sentence every failure note here ends with, because the
#: handler's next question is the same one either way.
EMPTY_NARRATION_NOTE = (
    "The copilot could not compose an answer for this quick action. Nothing "
    "was changed on the claim — try the action again in a moment."
)

#: What the figures section says when there is **no material section at all**.
#:
#: Every quick-action prompt file refers to `MATERIAL TO ANALYSE`, and `rtw.md`
#: goes furthest: it instructs the model to *quote* the medical restrictions
#: from it, in a letter addressed to an injured worker. But `_narrate` omits the
#: section entirely when nothing untrusted was gathered, and that is reachable —
#: a claim with no contraindications and no prognosis, or a `claim_reader` that
#: declined (a tolerated non-fatal path, see `_claim_material`). Telling a model
#: to quote from a section that is not in the message is an invitation to supply
#: one, which is the hallucination this whole story exists to prevent.
#:
#: So the absence is stated, in the half of the message the model is told is
#: authoritative, rather than left to be inferred from a missing heading.
NO_MATERIAL_NOTE = (
    "This message carries no section of material to analyse: no claim "
    "narrative, no retrieved passage and no clinical restriction was available "
    "for it. Do not quote from one, do not infer what it would have said, and "
    "say plainly which of those is missing where your answer needed it."
)

#: What the figures section says when the claim's own narrative could not be read.
#:
#: `_claim_material` tolerates a failed `claim_reader` — the narrative is
#: context, not a figure — but it used to tolerate it *silently*, which left the
#: model with a message that simply had less in it and no way to know a source
#: had declined. A briefing that is less specific because a read failed should
#: say so; the alternative is a confident answer that quietly stopped mentioning
#: the injury.
NO_NARRATIVE_NOTE = (
    "The claim's own case-file narrative could not be read for this answer, so "
    "the injury description and the cause are not in this message. Do not "
    "describe the injury; answer from the figures and say the narrative was "
    "unavailable."
)


# --- the shared machinery ------------------------------------------------


def _note(text: str) -> dict[str, Any]:
    """Put deterministic text on the wire **and** in the transcript.

    Two destinations because they are two different obligations. The stream
    writer is what makes the run terminate `done`: the endpoint's terminal
    decision asks whether any assistant content reached the client, and a note
    that only landed in state would have answered a handler's question with a
    blank space under a success frame. The returned `messages` update is what
    checkpoints it, so a reload shows the note where it was said.

    The two carry the same string. Anything else would be a transcript that
    disagreed with what was on screen a second earlier, which is the specific
    way a chat panel loses a reader's trust.
    """
    get_stream_writer()({QAS_NOTE: text})
    return {"messages": [AIMessage(content=text)]}


def _failure_note(what: str, envelope: Mapping[str, Any]) -> dict[str, Any]:
    """The plain-terms sentence a failed tool becomes. Never a stack trace.

    AD-13's structured-error rule at the node boundary. `envelope["error"]` is
    the wrapper's own short sentence — "the claim is not in this caller's book",
    "the local model server did not answer" — written for exactly this and
    content-free by construction (AD-11). It is quoted rather than re-worded,
    because a second wording is a second place the same failure is described.

    The run continues to a terminal `done` rather than an `error` frame, and
    that is deliberate: the graph did not fail, a deterministic source declined,
    and the handler is entitled to be told which one in a sentence rather than
    to see the whole panel report an outage.
    """
    reason = str(envelope.get("error") or "the deterministic source did not answer")
    return _note(f"{what} could not be read: {reason}. Nothing was changed on the claim.")


async def _call(
    name: str,
    runtime: Runtime[CopilotContext],
    **arguments: Any,
) -> dict[str, Any]:
    """One registered entry, through `agents/registry.invoke`, on its own session.

    **Never the service directly** (AD-13). Going through `invoke` is what gets
    a node the same four guarantees a model-issued tool call gets: the write
    gate, argument validation against the entry's own schema, the AD-16
    confinement of `claim_business_id` to the thread's claim, and the declared
    context injections. A node that called `services/...` itself would be a
    second path to a figure with none of them.

    `approved_tool_call_ids` is empty and `tool_call_id` is `None`, which is the
    honest pair for this caller: no quick-action node holds a write tool and
    none may (Story 6.5 owns the RTW letter's save), so a `kind: write` entry
    reached from here is refused by the gate — the safe direction.
    """
    context = runtime.context
    async with context.sessionmaker() as session:
        return await invoke(
            REGISTRY[name],
            caller=context.caller,
            session=session,
            context=context,
            thread_claim_business_id=context.claim_business_id,
            approved_tool_call_ids=frozenset(),
            tool_call_id=None,
            arguments=arguments,
        )


async def _narrate(
    model: BaseChatModel,
    prompt_key: str,
    *,
    figures: Sequence[str],
    untrusted: Sequence[str],
    trailer: str | None = None,
    publish_on_success: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One streamed completion. `agents/insights.py::_narrate`'s shape, minus the schema.

    The system message comes only from prompt files. The user message is the
    figures, unfenced, under `FIGURES_HEADING`, then the untrusted items, each
    already fenced by the tool that produced it, under `MATERIAL_HEADING`. The
    headings are the module constants rather than literals so that
    `agents/fencing.scrub` can strip them out of item text — a heading an item
    could forge is a heading that means nothing (review of Story 6.2, M1).

    **An absent material section is stated, not left blank.** When `untrusted`
    is empty there is no `MATERIAL_HEADING` in the message at all, and every
    prompt file refers to that section — `rtw.md` instructs the model to quote
    the clinical restrictions out of it. `NO_MATERIAL_NOTE` goes into the
    figures instead, so "there is nothing to quote" is something the model was
    told rather than something it has to notice.

    **`astream` rather than `ainvoke`**, and it is the whole of "a quick action
    streams". LangGraph's `messages` stream mode carries tokens as a model
    decodes them; a node that awaited a whole completion would deliver one frame
    at the end, which is indistinguishable from success in every assertion
    except a frame count and reads as a thirty-second hang in a panel.

    The accumulated text is returned as one `AIMessage` so the answer is
    checkpointed as a single turn — the chunks are the wire's business and the
    transcript's business is the answer. **An empty accumulation is a note, not
    an empty turn**: see `EMPTY_NARRATION_NOTE`.

    `trailer` is a sentence appended in Python after the model has finished, for
    the one action that must carry one whatever the completion said — see
    `LEGAL_DISCLAIMER`. It is added only if the model did not write it, and it
    goes out on the custom stream as well as into the checkpoint, because the
    tokens the panel already rendered came from `messages` and a trailer that
    existed only in state would appear on reload and not on screen.

    **`publish_on_success` is a custom-stream payload written only when the
    narration actually produced text**, and it exists because publishing one
    before the completion started was a real defect (review of Story 6.5). The
    RTW draft's version pin is what tells the panel "a letter was drafted, and
    here is the version its save pins" — so the panel opens the modal on it. Sent
    before the model was asked for anything, it was still sent when the model
    answered nothing, and the modal then opened with `EMPTY_NARRATION_NOTE` in
    the body and a live Save button under it: "The copilot could not compose an
    answer for this quick action" was one click from being a filed document on
    an injured worker's case file, and one more from being the letter a claimant
    reads. A pin published after the text exists cannot describe a draft that
    does not.

    Ordering is unaffected on the success path: the payload goes out inside the
    same run, before its terminal frame, which is all the panel requires.
    """
    system, version = prompts.qas_system_message(prompt_key)
    sections = [FIGURES_HEADING, *figures]
    if untrusted:
        sections += ["", MATERIAL_HEADING, *untrusted]
    else:
        sections.append(NO_MATERIAL_NOTE)

    parts: list[str] = []
    async for chunk in model.astream(
        [SystemMessage(content=system), HumanMessage(content="\n".join(sections))]
    ):
        text = chunk.content
        if isinstance(text, str):
            parts.append(text)
    answer = "".join(parts)
    # Ids and a version (AD-11). Not one word of the prompt, the figures or the
    # answer — the prompt version is an operational fact worth having, the
    # narration is PHI.
    log.info("copilot.quick_action_narrated", prompt_key=prompt_key, prompt_version=version)

    if not answer.strip():
        # Nothing was decoded, or nothing decoded as a `str`. Either way the
        # node succeeded and the handler has no answer, and `_note` is what puts
        # a sentence on both the wire and the transcript — an empty `AIMessage`
        # put one on neither and turned a working run into an `error` frame.
        log.info("copilot.quick_action_empty_narration", prompt_key=prompt_key)
        return _note(EMPTY_NARRATION_NOTE)

    if publish_on_success is not None:
        # See the docstring: the pin describes a draft, so it is published once
        # there demonstrably is one.
        get_stream_writer()(dict(publish_on_success))

    if trailer is not None and trailer not in answer:
        get_stream_writer()({QAS_NOTE: f"\n\n{trailer}"})
        answer = f"{answer.rstrip()}\n\n{trailer}"
    return {"messages": [AIMessage(content=answer)]}


def _claim_of(runtime: Runtime[CopilotContext]) -> str | None:
    """The claim this thread is about — from the context, never from the model.

    AD-16 in one function. The claim a node asks about is the one the run bound
    from `copilot_thread`; it is not on a channel a checkpoint could poison, not
    in a message body, and not something a completion could name.
    """
    return runtime.context.claim_business_id


async def _claim_material(
    runtime: Runtime[CopilotContext], claim: str
) -> tuple[list[str], list[str]]:
    """The claim's own free text, fenced — and whatever the figures must say about it.

    Returns `(material, figures)`. Every narrating node sends the first, because
    a briefing about a claim needs to know what the injury is, and `claim_reader`
    is where fenced claim narrative comes from.

    **A failed header is not a failed action.** The narrative is *context*, not
    a figure: a reserve answer written without the injury description is a
    slightly less specific answer, where one written without the reserve figures
    would be a wrong one. `agents/insights.py::_knowledge` draws the same line
    and `agents/envelope.py::ToolResult.require` is what makes it explicit on
    the other side.

    **But the failure is now stated in the prompt rather than only in the log.**
    A tolerated failure that says nothing to the model is a message that is
    quietly missing a source the prompt file refers to, and the model has no way
    to distinguish "this claim has no narrative" from "the read declined". The
    second return value is the sentence that closes that gap; it goes into the
    figures section, because "a deterministic source did not answer" is a fact
    about this system and not somebody's text.
    """
    header = await _call("claim_reader", runtime, claim_business_id=claim)
    if not header["ok"]:
        log.info("copilot.quick_action_narrative_unavailable")
        return [], [NO_NARRATIVE_NOTE]
    narrative = header["data"]["narrative"]
    return [str(item) for item in narrative], []


# --- the seven nodes -----------------------------------------------------


def _build_reserve(*, model: BaseChatModel, prompt_key: str) -> QasNode:
    """✓ Reserve review — the service's verdict, narrated.

    Five members, not three. `ReserveVerdict` is `light | adequate | heavy |
    closed_final | indeterminate`, and the last two are the ones a naive
    narration gets wrong: `closed_final` means a settled claim has been judged
    to have no further exposure, and `indeterminate` means the bills are not on
    file and it *cannot* be judged.

    When the bills are not on file `reserve_check`'s `display` map omits
    `remainingMedicalCents`, `projectedRemainingCents` and `ratioBp` precisely
    so there is nothing to quote — the absence of a key is the state. So this
    node states the absence in words rather than passing a `None` the model
    would render as `$0`, which is the difference between "nobody knows" and
    "nothing is outstanding".

    **"No bills on file" and "indeterminate" are not the same condition**, and
    the node reads each from the thing that decides it: the missing display key
    for the first, `data['verdict']` for the second. A claim with no bills can
    still come back `light` (the service is sound on a lower bound) or
    `closed_final` (a settled claim is judged before any band arithmetic runs).
    """

    async def node(state: CopilotState, *, runtime: Runtime[CopilotContext]) -> dict[str, Any]:
        claim = _claim_of(runtime)
        if claim is None:
            return _note(NO_CLAIM_NOTE)
        result = await _call("reserve_check", runtime, claim_business_id=claim)
        if not result["ok"]:
            return _failure_note("The reserve adequacy check", result)

        data = result["data"]
        display = result["display"]
        figures = [
            f"Reserve adequacy verdict, computed by this system: {data['verdict']}.",
            f"This system's own rationale for that verdict: {data['rationale']}",
            f"Reserve carried on the claim: {display['reserveCents']}",
            f"Indemnity still scheduled: {display['remainingIndemnityCents']}",
            f"Indemnity disbursed so far: {display['disbursedIndemnityCents']}",
            f"Medical disbursed so far: {display['disbursedMedicalCents']}",
        ]
        # **Two conditions, not one, and the difference is a real state.**
        # `remainingMedicalCents` and `projectedRemainingCents` are present
        # together — `reserve_check` adds both exactly when the bills are on
        # file. `ratioBp` is *not* part of that pair: `ReserveCheck` says
        # `ratio_bp` is `None` "whenever no complete comparison happened — a
        # settled claim, or an incomplete exposure", so a `closed_final` claim
        # has its bills on file and no ratio. Collapsing the two conditions
        # raised a `KeyError` inside the node on the first settled claim it met,
        # which the endpoint correctly reported as one `error` frame — a quick
        # action that never answered, for a state a third of the portfolio is in.
        if "remainingMedicalCents" in display:
            figures += [
                f"Medical still unpaid: {display['remainingMedicalCents']}",
                f"Projected remaining exposure: {display['projectedRemainingCents']}",
            ]
        else:
            # **The absence and the verdict are two statements, and only the
            # first follows from `remainingMedicalCents` being missing.**
            # `services/financials/reserve.py` withholds `adequate` and `heavy`
            # on an incomplete exposure — those are claims about an upper bound
            # — but it still returns `light` when the *known* exposure has
            # already passed the light band (sound on a lower bound), and
            # `closed_final` for any settled claim whatever its bills. So this
            # branch asserted "the verdict says the reserve cannot be judged"
            # for two states where the verdict says something else entirely: an
            # intake claim with no bills and remaining indemnity above the light
            # band would have carried `verdict: light` and "cannot be judged" in
            # the same authoritative block, and a model reading both could tell
            # a handler an under-reserved claim needs no action. The unknown-not-
            # zero sentence is load-bearing and stays; the clause about what the
            # verdict says is now conditioned on the verdict.
            figures.append(
                "The bills for this claim are not on file, so there is no "
                "remaining-medical figure and no projected total exposure. "
                "Neither is zero: they are unknown."
            )
            if data["verdict"] == "indeterminate":
                figures.append(
                    "That is why the verdict is indeterminate: the reserve "
                    "cannot be judged on what is on file."
                )
            else:
                figures.append(
                    "The verdict above was still reached, on the exposure that "
                    "*is* known — which is a lower bound on the real one. State "
                    "the verdict as given and say the medical exposure is "
                    "incomplete; do not describe the claim as unjudged."
                )
        if "ratioBp" in display:
            figures.append(f"Remaining exposure as a share of the reserve: {display['ratioBp']}")
        else:
            figures.append(
                "No exposure ratio was computed for this claim, because no "
                "complete comparison was made — the claim is settled, or its "
                "exposure is not fully known. Do not state a ratio."
            )
        figures.append(f"(Reserve bands from rule document v{data['bands_version']}.)")

        material, notes = await _claim_material(runtime, claim)
        return await _narrate(
            model,
            prompt_key,
            figures=[*figures, *notes],
            untrusted=material,
        )

    return node


def _build_fraud(*, model: BaseChatModel, prompt_key: str) -> QasNode:
    """🔍 Fraud risk check — the variant chosen here, in Python.

    `elevated = siu_review or fraud_flagged` is `agents/insights.py:441`'s line,
    and it is the same line for the same reason: which answer a claim gets is a
    registered derivation's verdict over a stored score and a rule document's
    thresholds, and a model asked to pick would sometimes pick the reassuring
    one. The insight cache expresses the choice as which *schema* it requests;
    a streamed answer has no schema, so it is expressed as an instruction in the
    figures section naming which variant to write — which is why the prompt file
    describes both variants and chooses neither.
    """

    async def node(state: CopilotState, *, runtime: Runtime[CopilotContext]) -> dict[str, Any]:
        claim = _claim_of(runtime)
        if claim is None:
            return _note(NO_CLAIM_NOTE)
        result = await _call("fraud_signals", runtime, claim_business_id=claim)
        if not result["ok"]:
            return _failure_note("The claim's fraud signals", result)

        data = result["data"]
        elevated = bool(data["siu_review"] or data["fraud_flagged"])
        figures = [
            f"Stored fraud score: {data['fraud_score']}. Fraud flag set: {data['fraud_flag']}.",
            f"SIU referral threshold: {data['siu_fraud_score_min']}. "
            f"Clears it: {data['siu_review']}.",
            f"Fraud review threshold: {data['fraud_flag_score_min']}. "
            f"Clears it: {data['fraud_flagged']}.",
            f"(Thresholds from derivation rule document v{data['thresholds_version']}.)",
            (
                "This claim clears at least one threshold: write the red-flag variant."
                if elevated
                else "This claim clears neither threshold: write the low-risk confirmation."
            ),
        ]
        material, notes = await _claim_material(runtime, claim)
        return await _narrate(
            model,
            prompt_key,
            figures=[*figures, *notes],
            untrusted=material,
        )

    return node


def _build_next_actions(*, model: BaseChatModel, prompt_key: str) -> QasNode:
    """! Next best actions — the checklist, explained in the order it came in.

    The ranking, the cap and the padding floor are `services/worklist`'s, and
    the figures section says so with the rule document's version. AD-10 gives
    each derivation exactly one computer and a narration is not it, so the
    prompt forbids re-ranking and this node hands the rows over in order.
    """

    async def node(state: CopilotState, *, runtime: Runtime[CopilotContext]) -> dict[str, Any]:
        claim = _claim_of(runtime)
        if claim is None:
            return _note(NO_CLAIM_NOTE)
        result = await _call("next_actions", runtime, claim_business_id=claim)
        if not result["ok"]:
            return _failure_note("The claim's action checklist", result)

        data = result["data"]
        items = list(data["items"])
        figures = [
            f"Outstanding actions on this claim, already ranked and capped at "
            f"{data['cap']} by rule document v{data['rules_version']}: {len(items)}.",
            *(f"- [{item['urgency']}] {item['label']}" for item in items),
        ]
        if not items:
            figures.append("The checklist is empty: nothing is outstanding on this claim.")
        material, notes = await _claim_material(runtime, claim)
        return await _narrate(
            model,
            prompt_key,
            figures=[*figures, *notes],
            untrusted=material,
        )

    return node


def _build_similar(*, model: BaseChatModel, prompt_key: str) -> QasNode:
    """↻ Similar case outcomes — this claim's employer, and the freshness sentence.

    Two things this node must not get wrong, and both were review findings on
    the insight card that narrates the same tool.

    **The set is the subject claim's employer partition, not the caller's book**
    (follow-up review of Story 6.2, B9). `similar_cases` narrows to it through
    `services/rag.subject_scoped_context`, and for a handler covering two
    employers "your caseload" names a strictly larger set than the one that was
    searched. The framing in the figures says "at this claim's employer" and the
    prompt file repeats the prohibition.

    **The disclosure sentence is the tool's, verbatim.** It states two counts and
    a configured window — three figures, and AD-2 says the model originates
    none of them. It is produced in exactly one place,
    `agents/tools/similar.py::_disclosure`, so the Insights card and the chat
    quote the same string. When nothing is stale it is `None` and **no staleness
    sentence appears at all**, which is a different fact from an empty one.

    A neighbour's employer short name and injury description are written by
    people, so they travel as fenced items rather than as figure lines — the
    distance and the count stay in the figures, where a quotable string belongs.
    """

    async def node(state: CopilotState, *, runtime: Runtime[CopilotContext]) -> dict[str, Any]:
        claim = _claim_of(runtime)
        if claim is None:
            return _note(NO_CLAIM_NOTE)
        result = await _call("similar_cases", runtime, claim_business_id=claim)
        if not result["ok"]:
            return _failure_note("The comparable-claim search", result)

        data = result["data"]
        display = result["display"]
        items = list(data["items"])
        figures = [
            f"Comparable claims found at this claim's employer: {len(items)}.",
            *(
                f"- {item['claim_id']} · severity {item['severity_score']} · "
                f"distance {display[item['claim_id']]}"
                for item in items
            ),
        ]
        if not items:
            figures.append(
                "The search returned no comparable claims at this claim's "
                "employer. That means this employer has no other indexed claim "
                "near this one, not that the injury is unusual."
            )
        if data["disclosure"] is not None:
            figures.append(f"Freshness disclosure to include verbatim: {data['disclosure']}")

        material = [
            fence(
                f"claim:{item['claim_id']}:comparable",
                f"{item['claim_id']}: {item['employer_short_name']} — {item['injury_type']}",
            )
            for item in items
        ]
        claim_material, notes = await _claim_material(runtime, claim)
        material += claim_material
        return await _narrate(model, prompt_key, figures=[*figures, *notes], untrusted=material)

    return node


def _build_labor_law(*, model: BaseChatModel, prompt_key: str) -> QasNode:
    """§ Labor law & state rules — grounded in retrieved passages, and nothing else.

    ## The query is built from bare scalars, and it loses nothing

    `claim_reader` returns `injury_type`, `cause` and `icd + body_part` **only**
    as `fence()` strings, delimiter markup included, because they are
    human-written. They are therefore unusable as a search query, and unwrapping
    a fence to recover the raw value would defeat the fence — so the query is
    composed from the header's bare scalars: the state, the stage, the risk band
    and the severity score.

    That is the half that actually selects the right passages. The corpus is
    chunked by jurisdiction and `KnowledgeHit` carries a `state_code`, so the
    state is what discriminates; the rest narrows the register. Injury
    specificity is not lost, because the fenced claim material travels in the
    same user message under `MATERIAL_HEADING` — the model narrates the
    retrieved rules *against* the injury, without any claim text having steered
    retrieval.

    Which keeps the AD-16 property exact rather than approximate: untrusted
    content shaped no query, selected no tool and named no scope.

    **Empty retrieval says so.** A corpus with nothing near the question is a
    real state, and the prompt's standing instruction is that a rule no passage
    carried may not be stated. The alternative — a model filling the gap from
    its own training — is precisely the failure a labour-law briefing must not
    have.

    ## The disclaimer is appended here, not requested there

    Task 2 requires the briefing to carry `LEGAL_DISCLAIMER`. `laborlaw.md` asks
    for it in as many words and keeps asking — an instruction the model can
    follow is worth having — but a *requirement* that lives only in a prompt is
    a requirement the model may drop on any given completion, and there is no
    assertion that can hold it: the tests narrate against a scripted model whose
    output is fixed. So `_narrate` appends it when the answer does not already
    end in it, which makes "the briefing carries the disclaimer" a property of
    this file rather than a hope about a completion.
    """

    async def node(state: CopilotState, *, runtime: Runtime[CopilotContext]) -> dict[str, Any]:
        claim = _claim_of(runtime)
        if claim is None:
            return _note(NO_CLAIM_NOTE)
        header = await _call("claim_reader", runtime, claim_business_id=claim)
        if not header["ok"]:
            return _failure_note("The claim's case-file header", header)

        facts = header["data"]
        query = (
            f"{facts['state']} workers compensation rules: reporting deadlines, "
            f"indemnity benefits, waiting period and return-to-work obligations "
            f"for a {facts['risk']} risk claim at the {facts['stage']} stage "
            f"with severity score {facts['severity_score']}"
        )
        found = await _call("labor_law_search", runtime, claim_business_id=claim, query_text=query)
        if not found["ok"]:
            return _failure_note("The labour-law reference corpus", found)

        passages = list(found["data"]["passages"])
        figures = [
            f"Claim jurisdiction: {facts['state']}. Stage: {facts['stage']}. "
            f"Risk band: {facts['risk']}. Severity score: {facts['severity_score']}.",
            f"Reference passages retrieved for this briefing: {len(passages)}.",
            *(
                f"- source {passage['source']} · jurisdiction "
                f"{passage['state_code'] or 'not stated'}"
                for passage in passages
            ),
        ]
        if not passages:
            figures.append(
                "The reference corpus returned nothing for this claim. Say so; "
                "do not state a rule from any other source."
            )

        material = [str(passage["text"]) for passage in passages]
        material += [str(item) for item in facts["narrative"]]
        return await _narrate(
            model, prompt_key, figures=figures, untrusted=material, trailer=LEGAL_DISCLAIMER
        )

    return node


def _build_rtw(*, model: BaseChatModel, prompt_key: str) -> QasNode:
    """📄 Review RTW Policy — a draft in the transcript, and **no write at all**.

    **Still no write, after Story 6.5.** That story added the editable modal,
    the `interrupt()` gate and the save-to-claim, and it reused this key rather
    than adding an eighth — but AD-6 is explicit that a quick-action node holds
    no write tools of its own and routes any proposal into the one gated step.
    So this node still calls one `kind: read` entry, raises no interrupt, leaves
    `pending_approval` `None`, and streams its letter as an ordinary assistant
    message. What it gained is one line: it publishes the claim `version` its
    draft was composed against, on the custom stream, because that is the number
    the handler's later save is pinned on (see `RTW_DRAFT`).

    The save itself enters the graph as `proposed_write` on a *new* run, is
    turned into a write tool call by `agents/approval.py` without a model call,
    and pauses at the same gate a free-chat write pauses at. None of that is
    here, and that is the AD-6 property rather than a division of labour.

    **It names no return date**, and the reason has to be stated precisely
    rather than broadly. `Claim.rtw_rec` and `Claim.actual_rtw` are ORM columns
    on **no `ClaimDetail` field**, so no reader available to this action — not
    `rtw_reader`, not `claim_reader` — projects a return date, and AD-2 forbids
    the model originating one as firmly as it forbids a money amount. What is
    *not* true is that the dates are absent from the build: `services/worklist/
    actions.py` reads both columns directly and can put an overdue-RTW row on
    the `nextactions` checklist, one button up. A figures line claiming "no tool
    can supply one" therefore contradicted a sibling quick action about the same
    claim — so it says what it can defend: this action cannot see a date, and
    must not state one. `agents/tools/rtw.py` argues the projection gap.
    """

    async def node(state: CopilotState, *, runtime: Runtime[CopilotContext]) -> dict[str, Any]:
        claim = _claim_of(runtime)
        if claim is None:
            return _note(NO_CLAIM_NOTE)
        result = await _call("rtw_reader", runtime, claim_business_id=claim)
        if not result["ok"]:
            return _failure_note("The claim's return-to-work record", result)

        data = result["data"]
        status = data["return_status"]
        figures = [
            f"Claim this letter is about: {data['claim_id']}.",
            (
                f"Return status recorded on the case file: {status}."
                if status is not None
                else "The claim's current stage records no return status."
            ),
            "No return date is available to this action: the case-file "
            "projection this letter is drafted from carries none. Do not state "
            "a date; say the start date will be confirmed by the handler.",
            # **The "not saved to the claim" sentence is gone since Story 6.5**,
            # and its removal is the point rather than a tidy-up: the handler
            # now opens this draft in a modal, edits it, and can file it on the
            # claim through the approval gate. A figure telling them it cannot
            # be saved would be a figure that is no longer true, stated in the
            # half of the message the prompt grants quoting authority — so a
            # compliant model would repeat it, in a letter, under a Save button.
            "This is a draft. The handler will review and edit it before "
            "anything is filed, and nothing has been sent to anybody.",
        ]
        # The pin. See `RTW_DRAFT`: this is the claim `version` the save
        # compare-and-swaps on, read at draft time by `rtw_reader` and carried
        # no other way.
        #
        # **Handed to `_narrate` rather than published here**, so that it goes
        # out only if a letter was actually drafted. Published before the
        # completion — which is what this did — it was published even when the
        # model answered nothing, and the modal then opened on
        # `EMPTY_NARRATION_NOTE` with a live Save button under it (review of
        # Story 6.5). See `_narrate`.
        pin = {RTW_DRAFT: {"claimId": data["claim_id"], "version": data["version"]}}

        material = [str(item) for item in data["restrictions"]]
        if not material:
            figures.append(
                "No contraindications and no return-to-work prognosis are "
                "recorded for this claim. Say that the restrictions must be "
                "confirmed with the treating clinician before the offer is made."
            )
        claim_material, notes = await _claim_material(runtime, claim)
        material += claim_material
        return await _narrate(
            model,
            prompt_key,
            figures=[*figures, *notes],
            untrusted=material,
            publish_on_success=pin,
        )

    return node


def _build_data_alignment() -> QasNode:
    """📊 Data alignment note — **no model call**, and that is declared up front.

    `requires_llm: False`, which makes this the false path Story 6.6 gates its
    degradation on: with the model container stopped, this action still answers.

    ## Why this is not the pattern AD-14 bans

    AD-14 bans the prototype's `offlineAnswer` by name — canned text *presented
    as model output* while a live model was claimed. This is the opposite on
    every clause. The map declares before the run that no model is involved, the
    note says so in its own first paragraph, and its content is assembled in
    committed code from a registered tool's answer about this specific claim.
    Nothing here is pre-authored prose about a claim; the sentences are the
    frame and the claim's own scalars are the content.

    ## Bare scalars only

    `claim_reader`'s envelope has two halves and only one of them may be
    rendered. The `narrative` tuple is five `fence()` strings carrying
    `<<<LINEWORKER-ITEM source="…">>>` delimiters; they exist to keep untrusted
    text separable inside a *prompt*, and formatting one for a reader would show
    a handler delimiter markup and defeat the fence's only purpose. This is the
    node most at risk of it, because it is the only one that formats tool output
    for a human — so it touches the structured half and nothing else.

    ## It takes no arguments at all, and that is Story 6.6's correction

    Until 6.6 this builder accepted `model` and `prompt_key` and used neither,
    "so that every entry in the map is built the same way". That symmetry was
    the wrong thing to optimise for: it put a live `BaseChatModel` in the scope
    of the one node whose entire declared property is that it does not have one,
    leaving the `requires_llm: False` flag enforced by a comment asking the next
    author not to type `_narrate`. The parameters are gone, so that mistake is
    now a mypy error in this function rather than a stopped container's problem.
    `build_node` selects on the flag and hands this builder nothing; see
    `DeterministicBuilder`.
    """

    async def node(state: CopilotState, *, runtime: Runtime[CopilotContext]) -> dict[str, Any]:
        claim = _claim_of(runtime)
        if claim is None:
            return _note(NO_CLAIM_NOTE)
        result = await _call("claim_reader", runtime, claim_business_id=claim)
        if not result["ok"]:
            return _failure_note("The claim's case-file header", result)

        facts = result["data"]
        flags = [
            ("Fraud flag", facts["fraud_flag"]),
            ("Litigation", facts["litigation_flag"]),
            ("Surgery required", facts["surgery_required"]),
            ("OSHA recordable", facts["osha_recordable"]),
        ]
        lines = [
            f"**Data alignment note — {facts['claim_id']}**",
            "",
            "No model was involved in this note. Every value below was read "
            "back through the same deterministic services the case-file screens "
            "use, at the moment you pressed the button.",
            "",
            "**Identity and classification**",
            f"- Claim id: {facts['claim_id']}",
            f"- Jurisdiction: {facts['state']}",
            f"- Stage: {facts['stage']} · Status: {facts['status']}",
            f"- Risk band: {facts['risk']} (severity score {facts['severity_score']})",
            "",
            "**Flags on the case file**",
            *(f"- {label}: {'yes' if value else 'no'}" for label, value in flags),
            "",
            "**Where the rest of this claim's data comes from**",
            "- The injured worker, the employer, the injury description, the "
            "cause and the ICD-10 coding are free-text fields on the case file "
            "and are shown on the Overview and Injury tabs.",
            "- Money figures are computed by this system's financial services "
            "from the payment schedule and the bills on file, and are shown on "
            "the Bills and Overview tabs — this note states none of them.",
            "- The severity score, the risk band and the flags above are "
            "registered derivations over stored columns, evaluated against the "
            "current rule document.",
            "",
            "Nothing in this note was generated, estimated or inferred.",
        ]
        return _note("\n".join(lines))

    return node


#: Quick-action key → the builder that makes its node, **for the six that narrate**.
#:
#: A dict rather than a chain of `if`s so that `build_node` cannot silently
#: answer for a key nobody wrote a node for: a missing key is a `KeyError` at
#: graph-compile time, in `lifespan`, which is a process that fails to start
#: rather than a quick action that falls through to free text at run time.
#:
#: The keys of the two maps together, and the keys in
#: `agents/graph.py::QUICK_ACTIONS`, are asserted equal by
#: `tests/test_copilot_qas.py` — which is what keeps "one importable structure"
#: true across the two files the split needs, now that the builders are in two
#: maps rather than one.
_NARRATING_BUILDERS: Mapping[str, NarratingBuilder] = {
    "laborlaw": _build_labor_law,
    "similar": _build_similar,
    "rtw": _build_rtw,
    "reserve": _build_reserve,
    "fraud": _build_fraud,
    "nextactions": _build_next_actions,
}

#: Quick-action key → the builder that makes its node, **for the ones that do not**.
#:
#: One entry today. A map rather than a special case in `build_node` because
#: the seventh key is not special — it is the only *current* member of a
#: category the architecture declares (`requires_llm: False`, AD-14), and a
#: second deterministic action added later should be an entry here rather than
#: an `if` somebody has to notice.
_DETERMINISTIC_BUILDERS: Mapping[str, DeterministicBuilder] = {
    "data_alignment": _build_data_alignment,
}


def build_node(
    key: str, *, requires_llm: bool, prompt_key: str | None, model: BaseChatModel
) -> QasNode:
    """The node for one quick-action key — with a model, or without one.

    Called once per key from `agents/graph.build_graph`, which is itself called
    once from `lifespan`. **No node constructs a model** — the model arrives by
    closure through this seam, which is what makes the graph tests able to drive
    all seven against a scripted one and what keeps AD-6's "one graph, compiled
    once" from acquiring a second model per node.

    `prompt_key` comes from the map entry rather than from a constant in this
    module, so a key's prompt file is named in exactly one place.

    ## `requires_llm` selects, and that is Story 6.6's structural claim

    The flag was a declaration nothing acted on: every builder was handed the
    model regardless, so "this action needs no model" was true because the
    author of one function had chosen not to call one. Selecting on it here
    means a `requires_llm: False` key is **not handed a model** — see
    `DeterministicBuilder` — so the declaration is load-bearing in the type
    system rather than in a comment.

    ## Both mismatches raise, and both raise in `lifespan`

    A `requires_llm: True` entry with no prompt file has no instructions to send
    (AD-16: the versioned files are the only instruction channel), and a
    `requires_llm: False` entry *with* one is a prompt nothing will ever load.
    Neither can be expressed as a type on `QuickAction` — the two fields are
    independent there — so they are checked here, where the pairing is used, and
    they fail the process at start-up rather than one quick action at run time.
    `graph.py::QuickAction` records that the two `None`s travel together by
    construction; this is that sentence made checkable.

    ## The missing builder is diagnosed first, and that ordering is the fix

    Raises `KeyError` for a key with no node, at compile time, exactly as the
    single-map lookup did before the split — and the *order* of the checks below
    is what preserves that. Written the other way round, a key added to
    `QUICK_ACTIONS` with no builder anywhere fell out of this function as
    "declares requires_llm and names no prompt file", which is a sentence about
    the wrong field: the author's mistake was a node they had not written, and
    the message sent them to look at a prompt directory. Splitting one map into
    two made a `KeyError` naming the key reachable only after two other
    conditions had been satisfied, so the lookup moved first.

    A key registered in the map its flag does not name gets its own sentence for
    the same reason — "there is no builder for this" and "the builder for this
    is the other kind" are different mistakes, and a reader at start-up should
    not have to diff two dicts to tell which they made.

    See the two maps.
    """
    narrating = _NARRATING_BUILDERS.get(key)
    deterministic = _DETERMINISTIC_BUILDERS.get(key)
    if narrating is None and deterministic is None:
        raise KeyError(f"quick action {key!r} has no node builder in either map")
    if requires_llm:
        if narrating is None:
            raise KeyError(
                f"quick action {key!r} declares requires_llm and its only builder is deterministic"
            )
        if prompt_key is None:
            raise ValueError(f"quick action {key!r} declares requires_llm and names no prompt file")
        return narrating(model=model, prompt_key=prompt_key)
    if deterministic is None:
        raise KeyError(
            f"quick action {key!r} declares no model call and its only builder is a narrating one"
        )
    if prompt_key is not None:
        raise ValueError(f"quick action {key!r} makes no model call and names a prompt file")
    return deterministic()


#: `REGISTRY` is re-exported deliberately, and it is the one name here that is
#: not this module's own. Every node reaches its tools through the map bound at
#: import, so that binding is the seam a test replaces to put a claim into a
#: state the seeded portfolio does not have — a reserve with no bills, a stale
#: neighbour, a tool that refuses. Patching `agents.registry.REGISTRY` would
#: leave these nodes reading the original dict, so the seam has to be named
#: here to be reachable at all.
__all__ = [
    "EMPTY_NARRATION_NOTE",
    "RTW_DRAFT",
    "LEGAL_DISCLAIMER",
    "NO_CLAIM_NOTE",
    "NO_MATERIAL_NOTE",
    "NO_NARRATIVE_NOTE",
    "QAS_NOTE",
    "REGISTRY",
    "QasNode",
    "build_node",
]
