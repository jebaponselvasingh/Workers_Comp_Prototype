"""The copilot aggregate's routes — threads, transcripts, and the SSE run stream.

A router of its own, `prefix="/copilot"`, on the `/claims-diary` precedent: a
**router per aggregate, scoped to the caller**. A thread belongs to the handler
who started it and merely references a claim, exactly as a meeting does — so
nesting these under `/claims/{id}` would publish a resource whose contents do
not belong to that claim, and would imply that two handlers covering one
employer share a conversation. They do not; `data/repositories/copilot.py`
carries the ownership predicate that makes that true.

Thin by AD-1: each route validates a path parameter, calls one function, and
maps refusals onto statuses. Thread ids are minted in `agents/threads.py`, the
transcript comes back through the saver, and the run is the compiled graph
built once in `lifespan`.

Thin by AD-7 in the way `/claims/queue` is — **there is no scope-shaped
parameter anywhere on this router**, and not a rejected one: an absent one.
Nothing here can name a user, an employer or a role, so "whose conversation?"
has exactly one answer and it comes from the session cookie. There is also **no
thread-id parameter on any request body** (AD-6: clients never supply one); the
only way a thread exists is `POST …/threads`, which takes a claim.

## The two new 409s, and why neither carries an extension member

`/problems/thread-busy` and `/problems/thread-read-only`. Every other 409 in
this build carries the fresh entity — `ConflictProblemDocument` hands back the
claim, `MeetingConflictProblemDocument` the meeting — because a version-CAS
conflict means "you were holding a stale copy, here is the current one".

Neither of these is that. "A run is in flight" is not an entity the caller was
holding a stale copy of; there is nothing fresher to hand back, and the honest
answer is a bare problem document. Attaching the thread would invite a client to
render it as though something had changed, and attaching the *other* run's state
would be publishing a conversation's progress to a request that was refused.

`thread-busy` has **three causes and one `type`** (AD-6): a run holding the
thread's advisory lock, a run that ended in an interrupt and is waiting on a
human, and — on the mint route rather than the run route — "new conversation"
pressed while the current one is still answering. A caller cannot act
differently on any of them, and telling them apart would tell them about a run
they cannot see.

## Nothing on this router holds a database session across model time

`run` takes the request's session, validates against it, and calls
`await db.rollback()` **before the stream begins** — after which it never
touches it again. That is not tidiness: a streamed turn lasts minutes and a
`create_agent` loop makes several model calls, so a session left open would
hold a pooled connection idle-in-transaction for the length of a conversation.
`services/rag/insights.py` learned this at refresh scale (review of Story 6.2,
H2) and fixed it with exactly this call; here the multiplier is bigger and the
fix is the same one, plus the structural half — the registry's tools open their
own short-lived sessions from `app.state.sessionmaker` and close them, so no
tool can hold the request's.

## One terminal event per run

The invariant most likely to regress, so it is made structural rather than
asserted. `_run_frames` produces `messages` and `updates` frames and has no
vocabulary for a terminal one — an interrupt *raises* rather than yielding — and
`_stream` yields exactly one terminal frame, from the single `yield _sse(…)` at
the end of its body, after every path has assigned one. The only way to emit two
would be to add a second `yield`, which is a one-line diff a reviewer can see.

That `yield` sits **inside** the `try` whose `finally` releases the run's lock,
so the frame reaches the client before the thread becomes available again. It
is one statement's worth of nesting and it closes a window in which a finished
run had released its lock and had not yet said it was finished.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, suppress
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal

import anyio
import structlog
from fastapi import APIRouter, Path, Request, Response, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import BaseMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agents.context import CopilotContext
from agents.graph import resume_inputs, run_inputs
from agents.greeting import claim_greeting
from agents.threads import (
    THREAD_ID_PATTERN,
    ClaimNotInBook,
    ThreadBusy,
    ThreadMintContended,
    ThreadNotFound,
    ThreadReadOnly,
    ThreadRunState,
    ThreadView,
    list_threads,
    open_thread,
    resolve_thread,
    run_lock,
    saver_config,
    thread_state,
)
from api.deps import CallerContextDep, DbDep
from api.errors import PROBLEM_CONTENT_TYPE, ProblemDocument, ProblemException
from api.routers.auth import UNAUTHENTICATED_RESPONSE
from api.routers.claims import CLAIM_ID_PATTERN, NOT_FOUND_RESPONSE
from api.schemas import ApiModel
from data.context import CallerContext

if TYPE_CHECKING:  # pragma: no cover - a type-only import, to keep `api.app` acyclic
    # `api/app.py` imports this router, so importing it back at runtime would be
    # a cycle. Under `TYPE_CHECKING` it is not — and the point is that mypy
    # checks every `copilot.` attribute below against the real frozen dataclass
    # rather than against `Any`, which is what this module's run path was doing
    # until the review of Story 6.3 (five parameters typed `Any` in the one file
    # where a stream, a lock and a graph meet).
    from api.app import CopilotRuntime

log = structlog.get_logger()

router = APIRouter(prefix="/copilot", tags=["copilot"])

#: The two stream modes a run subscribes to, typed rather than spelled inline.
#:
#: `astream` overloads on this argument, so a bare `["messages", "updates"]`
#: infers as `list[str]` and matches no overload — which is why it type-checked
#: for as long as the graph it was called on was `Any`. `messages` carries the
#: model's tokens as they are decoded and `updates` says a node finished; no
#: other mode is subscribed to, and `values` in particular is not, because it
#: would put the whole conversation on the wire after every step.
_STREAM_MODES: list[Literal["messages", "updates"]] = ["messages", "updates"]

#: The path parameter every claim-addressed route on this router declares.
CLAIM_ID_PATH = Annotated[
    str,
    Path(
        pattern=CLAIM_ID_PATTERN,
        description="The claim's business id, `WC-nnnn`.",
        examples=["WC-20017"],
    ),
]

#: …and the thread one. Pattern-validated for shape only — a well-formed id is
#: never authority for anything, because `select_thread` is scoped *and* owned
#: and a forged id is a 404 like any other. What the pattern buys is a refusal
#: at the contract for obvious junk, and a generated client whose type is not
#: `string`.
THREAD_ID_PATH = Annotated[
    str,
    Path(
        pattern=THREAD_ID_PATTERN,
        description=(
            "A server-minted thread id, `claim.<claimId>.u<userId>.s<seq>`. "
            "Clients never construct one — they come from the threads route."
        ),
        examples=["claim.WC-20017.u7.s1"],
    ),
]

#: The run endpoint's 409, which has **two `type`s under one status**.
#:
#: OpenAPI keys a response by status code, so the two refusals cannot be two
#: entries — and merging them is honest rather than a workaround, because a
#: client reads `type` and not the status to tell them apart. That is the
#: Errors convention's whole point: `status` says what kind of failure, `type`
#: says which one.
#:
#: Both are described here so a generated client's documentation names both,
#: and both are argued in the module docstring: **neither carries an extension
#: member**, unlike every version-CAS 409 in this build, because there is no
#: fresh entity to hand back.
THREAD_CONFLICT_RESPONSE: dict[int | str, dict[str, object]] = {
    409: {
        "description": (
            "Two refusals, told apart by `type`.\n\n"
            "`/problems/thread-busy` — the thread is single-flight and is not "
            "free: either a run is streaming on it, or a previous run paused "
            "for an approval nobody has answered. One `type` for both causes, "
            "because the caller cannot act differently on them and the "
            "difference is a fact about a run they cannot see. Retry when the "
            "current run finishes.\n\n"
            "`/problems/thread-read-only` — the thread has been superseded by a "
            "newer conversation on the same claim. Its transcript stays "
            "readable through the messages route; only posting is refused.\n\n"
            "**Neither carries an extension member**: unlike a version conflict "
            "there is no fresh entity to hand back (RFC 9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}

#: The mint endpoint's 409 — the same `type` read from the other side.
#:
#: "New conversation" while the current one is still answering. It is
#: `thread-busy`'s third cause and not a fourth `type`, for the reason the other
#: two are one: the caller's move is the same (wait a moment, press it again),
#: and the difference is a fact about a run they cannot see.
THREAD_MINT_CONFLICT_RESPONSE: dict[int | str, dict[str, object]] = {
    409: {
        "description": (
            "`/problems/thread-busy` — a new conversation cannot be started "
            "right now. Either a run is still streaming into the current "
            "conversation (superseding it would checkpoint that answer into a "
            "conversation nobody can continue), or several mints raced and this "
            "one lost every attempt. Retry in a moment.\n\n"
            "**No extension member**, like the other 409s on this router (RFC "
            "9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}

#: What every route answers when the checkpoint store cannot be reached.
#:
#: `build_copilot` opens its psycopg pool **without waiting for a connection**,
#: deliberately: AD-14's posture is that claim operations never depend on the
#: agent runtime, so a database the copilot cannot reach must not stop the api
#: booting or answering `/healthz`. The consequence is that "the pool is
#: unreachable" is a state this process starts in by design and can enter at any
#: time — and until the review of Story 6.3 it surfaced as an unhandled 500 with
#: an `about:blank` body, which is the one shape this build's Errors convention
#: says never to emit.
COPILOT_UNAVAILABLE_RESPONSE: dict[int | str, dict[str, object]] = {
    503: {
        "description": (
            "The copilot's conversation store could not be reached. Nothing was "
            "changed on the claim, and the rest of the console is unaffected — "
            "the copilot's database pool is deliberately separate from the "
            "application's (RFC 9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}

THREAD_NOT_FOUND_RESPONSE: dict[int | str, dict[str, object]] = {
    404: {
        "description": (
            "No such thread for this caller. Deliberately the same answer for a "
            "thread that does not exist, one that belongs to somebody else, and "
            "one whose claim has left the caller's caseload — see the route "
            "docstring on why it is never 403 (RFC 9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}


def _thread_not_found() -> ProblemException:
    """The 404 every thread route answers — one wording, one `type`.

    Written once because the sameness **is** the security property, `_not_found`'s
    argument in `claims.py` with an extra clause. Three different facts reach
    here — no such thread, somebody else's thread, and a thread whose claim has
    left the caller's book — and a caller who could tell them apart could
    enumerate other handlers' conversations by guessing ids.

    **Never 403.** A 403 would say "this exists and is not yours", which is
    precisely the sentence that must not be available.

    **The detail names no thread**, unlike `claims.py::_not_found`, which echoes
    the claim id. The difference is what "byte-identical" costs: a foreign
    thread's id and an unknown thread's id are different strings, so echoing
    either would make the two documents differ in the one field a caller can
    read. The id is in the request URL regardless, so nothing is lost.

    `Cache-Control` is restated because raising abandons the injected `response`
    — the handler builds a fresh `JSONResponse` — and a 404 that depends on who
    is asking must not be cached by an intermediary and replayed to somebody
    whose book *does* contain the thread.
    """
    return ProblemException(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Not Found",
        detail="No such conversation.",
        type_="/problems/thread-not-found",
        headers={"Cache-Control": "no-store"},
    )


def _claim_not_found(claim_business_id: str) -> ProblemException:
    """The 404 for a claim outside the caller's book, in the case file's wording.

    Deliberately `claims.py::_not_found`'s exact sentence and exact `type`: a
    caller comparing this router's refusal with the case file's must not be able
    to learn from the difference that a claim exists but has no copilot.
    """
    return ProblemException(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Not Found",
        detail=f"No claim {claim_business_id} in your caseload.",
        type_="/problems/claim-not-found",
        headers={"Cache-Control": "no-store"},
    )


def _busy() -> ProblemException:
    """The single-flight 409. Two causes, one `type` — see the module docstring."""
    return ProblemException(
        status_code=status.HTTP_409_CONFLICT,
        title="Conflict",
        detail=(
            "This conversation is busy. A message is being answered, or the "
            "copilot is waiting for you to approve something. Try again in a "
            "moment."
        ),
        type_="/problems/thread-busy",
        headers={"Cache-Control": "no-store"},
    )


def _mint_busy() -> ProblemException:
    """The 409 for "new conversation" while the current one is still answering.

    Same `type` as `_busy`, different sentence, because the affordance the
    handler pressed is a different one and "this conversation is busy" would
    read as being about the conversation they were trying to leave.
    """
    return ProblemException(
        status_code=status.HTTP_409_CONFLICT,
        title="Conflict",
        detail=(
            "A new conversation cannot be started while the current one is "
            "still answering. Try again in a moment."
        ),
        type_="/problems/thread-busy",
        headers={"Cache-Control": "no-store"},
    )


def _copilot_unavailable() -> ProblemException:
    """The 503 for a checkpoint store this process cannot reach.

    A problem document rather than the unhandled 500 this used to be. The
    detail says the two things a handler can act on — that the copilot is the
    part that is unavailable, and that nothing was written to the claim — and
    names no host, no driver and no exception (AD-11); the class name goes to
    the log, where an operator can read it.
    """
    return ProblemException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        title="Copilot unavailable",
        detail=(
            "The copilot's conversation store could not be reached. Nothing was "
            "changed on the claim. Try again shortly."
        ),
        type_="/problems/copilot-unavailable",
        headers={"Cache-Control": "no-store"},
    )


async def _thread_state(
    graph: CompiledStateGraph[Any, Any, Any, Any], *, thread_id: str
) -> ThreadRunState:
    """`thread_state`, with an unreachable checkpoint store as a problem document.

    The one place both reading routes ask the saver anything, so the translation
    from "psycopg could not get a connection" to a 503 happens once rather than
    at two call sites that could disagree.

    A bare `except Exception` because the vendor's failure surface here is not
    one type: a pool timeout, a dropped connection, an authentication failure
    and a serialisation error all arrive from different packages, and every one
    of them means the same thing to a caller. `CancelledError` is a
    `BaseException` and is deliberately not caught — a client that has gone is
    not a 503.
    """
    try:
        return await thread_state(graph, thread_id=thread_id)
    except Exception as exc:
        log.warning(
            "copilot.checkpoints_unreachable", thread_id=thread_id, error=type(exc).__name__
        )
        raise _copilot_unavailable() from exc


def _read_only() -> ProblemException:
    """The superseded-thread 409."""
    return ProblemException(
        status_code=status.HTTP_409_CONFLICT,
        title="Conflict",
        detail=(
            "This conversation has been replaced by a newer one on the same "
            "claim. You can still read it; to ask something new, use the "
            "current conversation."
        ),
        type_="/problems/thread-read-only",
        headers={"Cache-Control": "no-store"},
    )


# --- payloads -----------------------------------------------------------


class ThreadResponse(ApiModel):
    """One conversation's identity — never its contents.

    `threadId` is the server-minted key; a client stores it and sends it back in
    a path, and has no way to construct one (AD-6).

    `isCurrent` is **server-derived** and is `conversation_seq == max(seq)`. It
    is on the wire rather than left to the browser for AD-10's reason in
    miniature: the SPA would have to sort the list and compare to reproduce it,
    and a client that computed "current" would be a second place the read-only
    rule lives — with the 409 arriving as a surprise when the two disagreed.
    """

    thread_id: str
    conversation_seq: int
    is_current: bool
    created_at: datetime


class ThreadListResponse(ApiModel):
    """A claim's conversations for this caller, plus the two things the panel opens with.

    Not the `{items, nextCursor, total}` list envelope, and the difference is
    real rather than an omission: this list is not paged and cannot be. A
    handler has a handful of conversations per claim, all of them are rendered
    in the switcher at once, and a cursor would be a contract promising
    pagination that the switcher has no affordance for.

    `currentThreadId` is `null` for a claim nobody has opened the panel on. That
    is a first-class state — the SPA `POST`s once to mint `seq 1` — and not an
    error: answering a fresh claim with a 404 would send the panel down its
    error branch for the normal state of every claim on a fresh deployment
    (NFR-3).

    `greeting` is the seeded case-summary line (UX-DR8), and it is
    **deterministic service output rather than model prose** (AD-2) — see
    `agents/greeting.py`. It rides on this payload rather than on the transcript
    because it is a property of the claim, not of a conversation: every thread
    on one claim opens with the same one, and it renders before the first
    message exists.
    """

    items: list[ThreadResponse]
    current_thread_id: str | None
    greeting: str = Field(
        description=(
            "The seeded case-summary greeting for this claim. Server-composed "
            "from deterministic figures — the model does not write it, and it "
            "is identical on every read."
        )
    )
    greeting_version: int = Field(
        description="Which version of the greeting template produced the line above."
    )


class TranscriptMessage(ApiModel):
    """One turn of a checkpointed conversation.

    `role` is `user` or `assistant` and nothing else. Tool calls and tool
    results are part of *how* an answer was produced and are deliberately not
    published: they carry raw service payloads, they are not what the transcript
    renders, and a client that received them would be one component away from
    rendering a claim's reserve twice in two formats.

    **A message with empty content is omitted entirely**, not sent as a blank
    bubble — an assistant turn that only carried tool calls has nothing to say.
    """

    role: str
    content: str


class TranscriptResponse(ApiModel):
    """A thread's messages, oldest first, read back through the saver.

    Read-only, always, for every thread — current or superseded. That is what
    makes "new conversation freezes the prior thread" a freeze of *posting*
    rather than of reading: the transcript stays available for as long as the
    checkpoints do (`copilot_checkpoint_retention_days`, purged by Epic 8).
    """

    thread_id: str
    is_current: bool
    messages: list[TranscriptMessage]


class RunRequest(ApiModel):
    """What starts a run: a message, or a resume decision. Never a thread id.

    `extra="forbid"` for `ClaimFieldPatch`'s reason: an unknown key is a 422
    from the contract rather than a value silently dropped on the way to a
    graph. It matters more here than anywhere else in the build, because the
    keys that are *not* on this model are the ones that must never be: there is
    no `threadId`, no `userId`, no `employerId`, no `scope`, no `systemPrompt`
    and no `model`.

    `command` is the resume shape, landing now and unused: Story 6.5 raises the
    first interrupt, and this story's job is that its round trip does not need a
    new endpoint.

    ## Exactly one of the two, and a `command` that is really one

    A body carrying **neither** is a 422, and so is a body carrying **both** —
    which was a 200 until the review of Story 6.3, and a bad one. The route
    prefers the `Command` when it is present and skips the interrupt-pending
    check when it is, so `{"message": "…", "command": {}}` walked past the
    spec's "second message while interrupt pending → 409" row *and* discarded
    the question, answering 200 with a stream that resumed nothing. Two fields
    that mean "which kind of run is this?" have to be mutually exclusive, or the
    answer to "which kind?" is decided by the order of two `if`s.

    A `command` without a `resume` key is a 422 for the same reason rather than
    a resume with `None`: `Command(resume=None)` is a real instruction to
    LangGraph — it resumes an interrupt with the value `None` — so accepting a
    body that never said so would be inventing a decision on the caller's
    behalf, and 6.5's approve/edit/reject round trip is exactly where that would
    become a wrong answer to a question about a claim.
    """

    model_config = ApiModel.model_config | ConfigDict(extra="forbid")

    message: str | None = Field(
        default=None,
        min_length=1,
        max_length=4000,
        description=(
            "The handler's message. Free text, treated as a question about the "
            "claim this thread is on — never as an instruction that can change "
            "scope, routing or tool selection (AD-16)."
        ),
    )
    command: dict[str, Any] | None = Field(
        default=None,
        description=(
            'Resume an interrupted run, `{"resume": …}`. The shape ships with '
            "Story 6.3; the first interrupt that can produce one is Story 6.5's."
        ),
    )


# --- helpers ------------------------------------------------------------


def _thread_payload(view: ThreadView) -> ThreadResponse:
    """One service view → one wire object.

    Written out rather than `model_validate(view)` even though `ApiModel` sets
    `from_attributes`, `_meeting`'s rule: the view carries `claimBusinessId`,
    which the wire does not publish here (the thread id already spells it), and
    an attribute mapping that silently included it would put a field on the
    contract nobody decided to add.
    """
    return ThreadResponse(
        thread_id=view.thread_id,
        conversation_seq=view.conversation_seq,
        is_current=view.is_current,
        created_at=view.created_at,
    )


async def _greeting_for(
    db: AsyncSession, ctx: CallerContext, claim_business_id: str
) -> tuple[str, int]:
    """The claim's seeded greeting, or the case file's 404.

    It is also what resolves the claim on this route: a handler who cannot see
    the claim must not learn that it exists from an empty thread list, so the
    greeting's own scoped read is the check, and the two cannot drift apart
    because there is only one.
    """
    greeting = await claim_greeting(db, ctx, claim_business_id=claim_business_id)
    if greeting is None:
        raise _claim_not_found(claim_business_id)
    return greeting.text, greeting.version


def _sse(event: str, data: Mapping[str, Any] | list[Any]) -> bytes:
    """One SSE frame. The only place this wire format is spelled.

    `event:` then `data:` then a blank line, with the payload as compact JSON on
    a single line — which is what makes the framing safe: a JSON document
    containing a newline would otherwise become two `data:` lines, and a
    conforming client concatenates those with a `\\n` and hands back something
    that no longer parses.
    """
    body = json.dumps(data, separators=(",", ":"), default=str)
    return f"event: {event}\ndata: {body}\n\n".encode()


#: How `_stream` recognises an assistant-content frame without re-parsing SSE.
#:
#: Spelled here, beside `_sse`, because this is the only place in the build that
#: writes the wire format and a prefix invented at the reading end would be a
#: second copy of it. What it buys is `_terminal_for`'s `answered` argument: a
#: run that finished having emitted no content frame is a question answered by
#: nothing, and `done` would tell the handler otherwise.
MESSAGES_FRAME_PREFIX = b"event: messages"


def _keepalive() -> bytes:
    """An SSE comment. Two bytes of nothing, so an idle proxy does not hang up.

    A comment rather than an event: a conforming client discards it without
    dispatching anything, so it cannot be mistaken for a frame and no client
    code has to know about it. `copilot_sse_keepalive_seconds` sets the cadence.
    """
    return b": keepalive\n\n"


def _problem_frame(*, title: str, detail: str, type_: str, status_code: int) -> dict[str, Any]:
    """The problem+json body an `error` frame carries inline.

    The Copilot stream convention: an `error` event's payload **is** an RFC 9457
    document, because by the time a stream has started the HTTP status is
    already 200 and there is nowhere else to put one. So a client reads the same
    four members it reads from any other failure on this API, and the SPA's
    existing `Problem` type covers it with no second shape to learn.
    """
    return {"type": type_, "title": title, "status": status_code, "detail": detail}


# --- routes -------------------------------------------------------------


@router.get(
    "/claims/{claim_business_id}/threads",
    response_model=ThreadListResponse,
    summary="This caller's conversations about one claim, plus the seeded greeting",
    responses={**UNAUTHENTICATED_RESPONSE, **NOT_FOUND_RESPONSE},
)
async def threads(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    claim_business_id: CLAIM_ID_PATH,
) -> ThreadListResponse:
    """List the caller's own threads on a claim. Creates nothing.

    A GET that minted would make opening the panel write a row — and would mint
    one for every claim a handler merely clicked through in the queue. The SPA
    `POST`s once when the list comes back empty, which is one extra round trip
    on the first conversation and none thereafter.

    **An empty list is 200, never 404**, for `GET …/insights`' reason: "nobody
    has talked to the copilot about this claim yet" is a state with an
    affordance attached, and answering it as a missing resource would send the
    panel down its error branch for the normal state of a fresh deployment.

    404 for a claim outside the caller's book, in the case file's exact wording
    — which is also what resolves the claim, since the greeting needs it.
    """
    # Specific to one persona's book, so never served to another from a cache
    # upstream — every route in this build that answers per-caller says so, and
    # it matters here because the payload names a claim and an injured worker.
    response.headers["Cache-Control"] = "no-store"
    greeting, greeting_version = await _greeting_for(db, ctx, claim_business_id)
    listed = await list_threads(db, ctx, claim_business_id=claim_business_id)
    return ThreadListResponse(
        items=[_thread_payload(view) for view in listed.items],
        current_thread_id=listed.current_thread_id,
        greeting=greeting,
        greeting_version=greeting_version,
    )


@router.post(
    "/claims/{claim_business_id}/threads",
    response_model=ThreadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new conversation on this claim (mints the next sequence)",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **NOT_FOUND_RESPONSE,
        **THREAD_MINT_CONFLICT_RESPONSE,
    },
)
async def new_thread(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    claim_business_id: CLAIM_ID_PATH,
) -> ThreadResponse:
    """Mint the caller's next conversation on this claim.

    **One operation behind two affordances**, and deliberately not two routes.
    "The panel opened on a claim with no conversation" and "the handler pressed
    New conversation" are the same request: mint `max(seq) + 1`. A separate
    `get-or-create` would need a "did it already exist?" answer that the
    switcher does not use and would make the first-conversation path different
    from every later one.

    The prior thread becomes read-only by arithmetic rather than by an update —
    `is_current` is `seq == max(seq)`, so nothing is written to the old row (see
    `CopilotThread` on why the table is append-only).

    **201 with no `Location` header**, `schedule_meeting`'s call: the created
    entity is the body, and the only way to address a thread afterwards is
    through the two routes below, both of which take the id this response
    carries.

    A race between two clicks is arbitrated by `uq_copilot_thread_claim_id`
    rather than by a lock. The loser sees an `IntegrityError` and is retried a
    **bounded** number of times: each attempt reads a `max(seq)` that includes
    the winner's row, so it mints the next one. Exhausting the bound is a 409
    rather than the 500 it used to be — see `open_thread` on why "retried once"
    was wrong, and `data/repositories/copilot.py` on the read-then-write gap it
    was wrong about.

    **409 while the current conversation is still answering.** Minting `seq + 1`
    against a running thread freezes a run mid-answer: the assistant turn is
    still checkpointed, into a thread that has become read-only, so the handler
    gets no reply and no way to ask again in the conversation they asked in.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        view = await open_thread(db, ctx, claim_business_id=claim_business_id)
    except ClaimNotInBook as exc:
        raise _claim_not_found(claim_business_id) from exc
    except (ThreadBusy, ThreadMintContended) as exc:
        raise _mint_busy() from exc
    return _thread_payload(view)


@router.get(
    "/threads/{thread_id}/messages",
    response_model=TranscriptResponse,
    summary="One conversation's transcript, read back from the checkpoints",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **THREAD_NOT_FOUND_RESPONSE,
        **COPILOT_UNAVAILABLE_RESPONSE,
    },
)
async def transcript(
    request: Request,
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    thread_id: THREAD_ID_PATH,
) -> TranscriptResponse:
    """Read a conversation back — **the story's point, on one route**.

    FR-CP-2: per-claim history survives navigation and re-login. That is this
    endpoint: the messages come out of the checkpoint tables, so they survive a
    reload, a logout, and the api process being replaced under them.

    **Through the saver, never with SQL.** AD-3's exception vendors the
    checkpoint DDL on the understanding that application code does not read
    those tables, and a history route that ran `SELECT … FROM checkpoints`
    would be the thing that understanding forbids.
    `tests/test_layering.py` greps for one.

    Read-only for every thread, current or superseded — freezing a prior thread
    freezes *posting*, not reading.

    404 for a thread that is not the caller's own, in the same document an
    unknown id produces (see `_thread_not_found`).
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        view = await resolve_thread(db, ctx, thread_id=thread_id, for_posting=False)
    except ThreadNotFound as exc:
        raise _thread_not_found() from exc

    copilot: CopilotRuntime = request.app.state.copilot
    state = await _thread_state(copilot.graph, thread_id=thread_id)
    return TranscriptResponse(
        thread_id=thread_id,
        is_current=view.is_current,
        messages=[
            TranscriptMessage(role=role, content=text)
            for role, text in (_rendered(message) for message in state.messages)
            if role is not None and text
        ],
    )


def _rendered(message: BaseMessage) -> tuple[str | None, str]:
    """One checkpointed message as `(role, text)`, or `(None, "")` to drop it.

    Only `human` and `ai` turns are published — see `TranscriptMessage` on why
    tool calls and tool results are not. An `ai` message whose content is empty
    is an assistant turn that did nothing but call tools, and it is dropped
    rather than rendered as a blank bubble.

    `content` can be a list of content blocks rather than a string on some
    providers; the text parts are joined and everything else is ignored, because
    a transcript is text and a client that received a structured block would
    need a renderer for a shape this build never produces.
    """
    kind = getattr(message, "type", None)
    role = {"human": "user", "ai": "assistant"}.get(str(kind))
    if role is None:
        return None, ""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return role, content.strip()
    parts = [
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return role, "".join(parts).strip()


@router.post(
    "/threads/{thread_id}/runs",
    summary="Send a message and stream the answer (SSE)",
    response_class=StreamingResponse,
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **THREAD_NOT_FOUND_RESPONSE,
        **THREAD_CONFLICT_RESPONSE,
        **COPILOT_UNAVAILABLE_RESPONSE,
        200: {
            "description": (
                "A `text/event-stream` of the run. Frames are `messages` "
                "(assistant tokens, as they are decoded) and `updates` (a node "
                "finished), followed by **exactly one** terminal frame: `done`, "
                "`interrupt`, or `error`. An `error` frame's payload is an RFC "
                "9457 problem document, because by the time a stream has "
                "started the status is already 200."
            ),
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
    },
)
async def run(
    request: Request,
    ctx: CallerContextDep,
    db: DbDep,
    body: RunRequest,
    thread_id: THREAD_ID_PATH,
) -> StreamingResponse:
    """Run one turn on a thread and stream it. **Holds no session while it does.**

    The order below is the whole of the endpoint's correctness:

    0. **Decide which kind of run this is, before anything else.** Exactly one
       of `message` and `command`, and a `command` that actually carries a
       `resume` — see `RunRequest`. This step is numbered zero because it used
       to be split in two and interleaved with step 3, which is how a body
       carrying both fields walked past the interrupt-pending 409 and had its
       message silently dropped.
    1. **Resolve the thread under a freshly built context.** `ctx` came from
       `api.deps.get_caller_context` on *this* request, so the ownership and
       scope checks are against who the caller is now — not against anything a
       checkpoint remembers (AD-7). A thread that is not this caller's, or whose
       claim has left their book, is the `_thread_not_found` 404.
    2. **Refuse a superseded thread**, 409 `/problems/thread-read-only`.
    3. **Refuse a busy thread**, 409 `/problems/thread-busy`, for either of its
       two causes: the advisory lock is held, or the saver reports a pending
       interrupt. The lock is *tried* here and, when it is not won, **never
       taken** — the rejected request holds nothing.
    4. **Release the session**, `await db.rollback()`, before a single token is
       requested. See the module docstring; this is the H2 fix at conversation
       scale.
    5. **Stream**, holding the lock for the length of the run.

    The wall clock is enforced around the whole stream
    (`copilot_run_timeout_seconds`), not inside the graph, so that the decision
    to terminate lives in exactly one place — the same place that decides which
    terminal frame to send.
    """
    _validate_run_body(body)

    try:
        view = await resolve_thread(db, ctx, thread_id=thread_id, for_posting=True)
    except ThreadNotFound as exc:
        raise _thread_not_found() from exc
    except ThreadReadOnly as exc:
        raise _read_only() from exc

    copilot: CopilotRuntime = request.app.state.copilot
    state = await _thread_state(copilot.graph, thread_id=thread_id)
    if state.interrupt_pending and body.command is None:
        raise _busy()

    # The lock is acquired here and released by `_stream`'s own `finally`. It
    # cannot be taken inside the generator, because a 409 has to be an HTTP
    # status and by the time the generator runs the response has already begun.
    lock = run_lock(db, thread_id=thread_id)
    acquired = await lock.__aenter__()
    if not acquired:
        await lock.__aexit__(None, None, None)
        raise _busy()

    # **Everything from here to the `StreamingResponse` is inside a `try`, and
    # that is not defensive habit** (review of Story 6.3). The lock is a
    # *session-scoped* advisory lock on a connection this process holds; it
    # survives the `ROLLBACK` that returns a connection to the pool and it is
    # released by exactly one line of code, in `_stream`'s `finally`. If
    # anything between the acquisition and the construction of that generator
    # raises — `db.rollback()` on a dropped pooled connection is the realistic
    # one — the generator is never created, its `finally` never runs, and the
    # thread answers 409 for the life of the process. A 500 on one request is a
    # bad afternoon; a permanently unusable conversation is a lost one.
    try:
        # **Before any model time.** Everything above used the request's session
        # and nothing below touches it; the rollback hands its pooled connection
        # back rather than leaving it idle-in-transaction for the length of a
        # conversation. The registry's tools open their own.
        await db.rollback()

        inputs = (
            run_inputs(caller=ctx, claim_business_id=view.claim_business_id, message=body.message)
            if body.message is not None
            else resume_inputs(caller=ctx)
        )
        body_iterator = _stream(
            lock=lock,
            copilot=copilot,
            ctx=ctx,
            thread_id=thread_id,
            claim_business_id=view.claim_business_id,
            inputs=inputs,
            resume=body.command,
        )
    except BaseException:
        await _release(lock, thread_id=thread_id)
        raise

    return StreamingResponse(
        body_iterator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            # nginx has `proxy_buffering off` on `/api` since Story 1.1, so this
            # is belt and braces for any other intermediary: a buffered SSE
            # stream is a stream that arrives all at once at the end, which
            # looks exactly like a hung panel.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _validate_run_body(body: RunRequest) -> None:
    """Which kind of run this is — decided once, before anything else happens.

    Four refusals and one `type`, all 422, because all four are the same
    statement: this body does not describe a run. See `RunRequest` on why
    "both" and "a `command` with no `resume`" are refusals rather than
    precedences.

    The order is the order the questions have to be asked in: **which kind of
    run is this** before **is this kind of run well formed**. A body carrying
    both fields is refused as "both" rather than as whichever half happens to be
    malformed, because the caller's mistake is the ambiguity and not the value.
    """
    if body.message is None and body.command is None:
        raise _unprocessable("A run needs either a message or a resume command.")
    if body.message is not None and body.command is not None:
        raise _unprocessable(
            "A run is either a message or a resume command, never both. "
            "Send the message on its own, or the command on its own."
        )
    if body.message is not None and not body.message.strip():
        # `min_length=1` on the field catches `""`; whitespace is a string of
        # length three that says nothing, and it used to be checkpointed into
        # the conversation for ever and answered with a whole model run.
        raise _unprocessable("A run's message cannot be blank.")
    if body.command is not None and "resume" not in body.command:
        raise _unprocessable('A resume command must carry a "resume" value.')


def _unprocessable(detail: str) -> ProblemException:
    """The router's 422, in the shape every other validation refusal here has."""
    return ProblemException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        title="Unprocessable Content",
        detail=detail,
        type_="/problems/validation-error",
        headers={"Cache-Control": "no-store"},
    )


async def _release(lock: AbstractAsyncContextManager[bool], *, thread_id: str) -> None:
    """Release a run's advisory lock. **On every exit path, and never fatally.**

    Two properties, and each answers a way the lock leaked (review of Story
    6.3).

    **Shielded.** Starlette streams a response inside an anyio task group and
    cancels that group when the client disconnects, so the `await` in
    `_stream`'s `finally` was being cancelled *before* `pg_advisory_unlock` ran.
    A session-scoped advisory lock outlives the `ROLLBACK` that returns its
    connection to the pool, so the outcome was a thread that answered 409 for
    ever — the single-flight guard turned into a permanent refusal by the one
    event it has to survive, a handler closing a tab. `anyio.CancelScope` with
    `shield=True` is the documented way to run cleanup to completion inside a
    cancelled scope; the cancellation is re-delivered the moment the scope
    exits, so nothing is swallowed except the timing.

    **Non-fatal.** A release that raised would replace the terminal frame with
    an exception on the way out of a generator that had already decided how the
    run ended — turning "the copilot answered" into a broken stream because the
    *unlock* failed. The lock is dropped by PostgreSQL when the connection goes
    anyway, so the honest handling of a failed release is a log line.
    """
    with anyio.CancelScope(shield=True):
        try:
            await lock.__aexit__(None, None, None)
        except Exception as exc:
            # AD-11: a class name, an id, and nothing else.
            log.warning(
                "copilot.run_lock_release_failed", thread_id=thread_id, error=type(exc).__name__
            )


class _Interrupted(Exception):
    """The graph paused on a human. Raised so the terminal decision stays in one place.

    `_run_frames` has no vocabulary for a terminal frame — that is what makes
    the one-terminal-event rule structural rather than remembered. An interrupt
    is nonetheless something only that function can see, so it raises, and
    `_stream`'s `except` turns it into the single terminal `interrupt` frame.

    Unreachable in this story: nothing registered is a `kind: write` tool and no
    node calls `interrupt()`. Story 6.5 makes it live, and it ships now so that
    the story which raises the first interrupt does not also have to invent the
    path it takes to the wire.
    """


async def _stream(
    *,
    lock: AbstractAsyncContextManager[bool],
    copilot: "CopilotRuntime",
    ctx: CallerContext,
    thread_id: str,
    claim_business_id: str,
    inputs: Mapping[str, Any],
    resume: Mapping[str, Any] | None,
) -> AsyncIterator[bytes]:
    """The response body: frames, keepalives, then **exactly one** terminal frame.

    There is one `yield` of a terminal frame in this module and it is the last
    line of this function. Every path through the block below assigns `terminal`
    before reaching it, so emitting two would take adding a second `yield` —
    which is a one-line diff a reviewer can see, rather than an invariant nobody
    can check.

    ## Why the frames come through a queue rather than a plain `async for`

    The keepalive. `copilot_sse_keepalive_seconds` says a comment frame must go
    out when nothing else has for a while, so that an idle proxy between the
    browser and this process does not decide the connection is dead while a cold
    model is thinking — and nginx's own `proxy_read_timeout` default is shorter
    than a cold first token can take.

    Racing a timer against a generator cannot be done with
    `asyncio.wait_for(agen.__anext__(), …)`: a timeout cancels the awaited step,
    and a cancelled step of an async generator leaves the generator unusable.
    Cancelling `queue.get()` is harmless, so the producer runs as its own task
    and this loop waits on the queue. The producer's exception is carried across
    rather than raised in the wrong task, which is what `failure` holds.

    `CancelledError` propagates without a terminal frame, and that does not
    violate the rule: the client has gone, so there is nobody to emit to. It
    does **not** leave the lock held — see `_release`, which is why that is now
    true rather than only intended.

    ## Cleanup order: drain the producer, *then* release, and both after the frame

    Three things that used to be in the wrong order (review of Story 6.3).

    The terminal frame is yielded **inside** the `try`, so the lock outlives the
    frame rather than being dropped a moment before it. A lock released first
    left a window in which the next message on this thread was accepted while
    the finished run had not yet finished.

    The producer is **awaited** after it is cancelled, not merely told to stop.
    `Task.cancel()` schedules a `CancelledError`; it does not wait for it to be
    delivered. On the timeout path that left an orphan still inside
    `graph.astream`, issuing checkpoint writes for a run that had already
    ended — into a thread a new run might by then have started on.

    And the release is last, so nothing that is still running can be holding the
    lock's connection when it is handed back.
    """
    started = time.monotonic()
    log.info("copilot.run_started", thread_id=thread_id)
    frames: asyncio.Queue[bytes | None] = asyncio.Queue()
    failure: list[BaseException] = []
    # Whether any assistant text reached the wire, as a one-element cell for
    # `failure`'s reason above: the producer is a nested task and the value is
    # read after it has finished.
    answered = [False]

    async def produce() -> None:
        try:
            async for frame in _run_frames(
                copilot=copilot,
                ctx=ctx,
                thread_id=thread_id,
                claim_business_id=claim_business_id,
                inputs=inputs,
                resume=resume,
            ):
                if frame.startswith(MESSAGES_FRAME_PREFIX):
                    answered[0] = True
                await frames.put(frame)
        except BaseException as exc:  # noqa: BLE001 - carried, then re-classified below
            failure.append(exc)
        finally:
            await frames.put(None)

    producer = asyncio.create_task(produce())
    try:
        terminal: tuple[str, dict[str, Any]]
        try:
            while True:
                try:
                    item = await asyncio.wait_for(frames.get(), copilot.sse_keepalive_seconds)
                except TimeoutError:
                    yield _keepalive()
                    continue
                if item is None:
                    break
                yield item
            terminal = _terminal_for(
                failure[0] if failure else None,
                thread_id=thread_id,
                started=started,
                answered=answered[0],
            )
        except asyncio.CancelledError:
            log.info("copilot.run_cancelled", thread_id=thread_id)
            raise

        yield _sse(*terminal)
    finally:
        producer.cancel()
        with anyio.CancelScope(shield=True):
            with suppress(BaseException):
                await producer
        await _release(lock, thread_id=thread_id)
        log.info(
            "copilot.run_finished",
            thread_id=thread_id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def _terminal_for(
    failure: BaseException | None,
    *,
    thread_id: str,
    started: float,
    answered: bool,
) -> tuple[str, dict[str, Any]]:
    """Which single terminal frame this run ends with. The whole decision, once.

    Three outcomes and no fourth, which is AD-6's event set on this surface:
    `done` when the graph finished **and said something**, `interrupt` when it
    paused for a human, and `error` for everything else — a model that did not
    answer, a timeout, a bug.

    **`done` requires an answer**, which it did not until the review of Story
    6.3. A graph that completed without emitting one token of assistant text is
    a run that finished cleanly and told the handler nothing, and `done` is the
    frame the panel reads as "your question was answered" — so a handler saw
    their question sitting under a blank space with no notice that anything had
    gone wrong. It is `error` because that is what happened, and because the
    alternatives are worse: `done` is a lie, and inventing a placeholder
    sentence would be the model's answer being written by this module (AD-14).
    An interrupt is exempt, because pausing for a human before answering is the
    normal shape of a run that has nothing to say yet.

    **Every `error` is content-free** (AD-11). The log line carries the exception
    *class name*, which distinguishes an outage from a database failure from a
    defect and contains no prompt, no token and no claim data; the frame carries
    a fixed sentence and the reassurance that matters most to a handler, which is
    that nothing was written to the claim.
    """
    if failure is None and answered:
        return "done", {"threadId": thread_id}
    if failure is None:
        log.warning(
            "copilot.run_answered_nothing",
            thread_id=thread_id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return "error", _problem_frame(
            title="Copilot did not answer",
            detail=(
                "The copilot finished without writing an answer. Nothing was "
                "changed on the claim — please ask again."
            ),
            type_="/problems/copilot-empty-answer",
            status_code=503,
        )
    if isinstance(failure, _Interrupted):
        return "interrupt", {"threadId": thread_id}
    if isinstance(failure, TimeoutError):
        log.warning(
            "copilot.run_timed_out",
            thread_id=thread_id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return "error", _problem_frame(
            title="Copilot run timed out",
            detail=(
                "The copilot did not finish answering in time. Nothing was changed on the claim."
            ),
            type_="/problems/copilot-run-timeout",
            status_code=504,
        )
    log.warning("copilot.run_failed", thread_id=thread_id, error=type(failure).__name__)
    return "error", _problem_frame(
        title="Copilot unavailable",
        detail=("The copilot could not finish answering. Nothing was changed on the claim."),
        type_="/problems/copilot-run-failed",
        status_code=503,
    )


async def _run_frames(
    *,
    copilot: "CopilotRuntime",
    ctx: CallerContext,
    thread_id: str,
    claim_business_id: str,
    inputs: Mapping[str, Any],
    resume: Mapping[str, Any] | None,
) -> AsyncIterator[bytes]:
    """Every **non-terminal** frame of one run. Raises; never emits a terminal.

    The split from `_stream` is what makes the one-terminal rule structural: this
    function's vocabulary is `messages` and `updates`, and there is no `done`,
    `interrupt` or `error` string anywhere in it. An interrupt raises
    `_Interrupted` rather than yielding, so the decision stays in `_terminal_for`.

    The iteration is bounded by `copilot_run_timeout_seconds` with
    `asyncio.timeout`, so a model that stops producing becomes a `TimeoutError`
    the caller turns into one `error` frame. The bound is here rather than inside
    the graph because a timeout that fired inside a node would leave the terminal
    decision in two places.

    **The caller context is built here, per run, and never checkpointed** (AD-7):
    `CopilotContext` carries the freshly resolved scope, the session factory and
    the thread's own claim, LangGraph passes it to tools through its runtime, and
    it dies with the run.

    **A resume carries the freshly resolved caller too, in its `update`.** AD-7
    asks for the overwrite at run start *and every resume*, and `resume_inputs`
    computes exactly that — but a bare `Command(resume=…)` **discards state
    inputs entirely**, so the checkpointed `caller` survived and the freshly
    resolved one was thrown away at the one call site the rule names twice
    (review of Story 6.3). `Command`'s own `update` is the channel for it: the
    values are applied before the interrupted task resumes, which is the same
    "before any node reads it" ordering `run_inputs` gets from being the run's
    input.
    """
    context = CopilotContext(
        caller=ctx,
        sessionmaker=copilot.sessionmaker,
        claim_business_id=claim_business_id,
    )
    payload: Any = inputs
    if resume is not None:
        payload = Command(resume=resume["resume"], update=dict(inputs))

    stream: AsyncIterator[Any] = copilot.graph.astream(
        payload,
        config=saver_config(thread_id),
        context=context,
        stream_mode=_STREAM_MODES,
        # **`subgraphs=True`, and it is load-bearing rather than tidy.** The
        # grounded-chat node *is* a compiled graph — `create_agent` returns
        # one — so the model's token stream happens one level down. Without
        # this flag the outer graph reports only what that node wrote to
        # `messages` when it finished: exactly one frame, carrying the whole
        # answer, indistinguishable from a model that does not stream at all.
        # The e2e spec asserts more than one content frame precisely because
        # that failure looks identical to success in every other assertion.
        subgraphs=True,
    )
    async with asyncio.timeout(copilot.run_timeout_seconds):
        # `astream`'s declared element type is the union of every stream mode's
        # shape, so the three-way unpack has to happen in the body rather than in
        # the `for` clause — with `subgraphs=True` and two modes subscribed, the
        # vendor yields `(namespace, mode, chunk)` and nothing else.
        async for part in stream:
            namespace, mode, chunk = part
            if mode == "messages":
                message, _metadata = chunk
                text = getattr(message, "content", "")
                # Only assistant text goes on the wire. A tool-call chunk has
                # empty content, and a tool *result* is a service payload the
                # transcript does not render (see `TranscriptMessage`).
                if isinstance(text, str) and text and getattr(message, "type", "") != "tool":
                    yield _sse("messages", {"content": text})
            elif mode == "updates":
                for node in dict(chunk):
                    if node == "__interrupt__":
                        raise _Interrupted
                    # **Only the outer graph's nodes are published.** A
                    # namespace is non-empty for an update from inside the
                    # `create_agent` harness, whose internal node names
                    # ("model", "tools") are the vendor's rather than this
                    # build's — publishing them would put a private structure on
                    # a public wire and would change under a dependency bump.
                    # An interrupt is checked at every level, because that is
                    # where 6.5 will raise one.
                    if not namespace:
                        yield _sse("updates", {"node": node})


__all__ = ["router"]
