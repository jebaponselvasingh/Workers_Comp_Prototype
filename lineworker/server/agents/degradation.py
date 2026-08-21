"""The model boundary's failure semantics — one translation seam, one probe
(Story 6.6, AD-14).

Everything AD-14 promises about an Ollama outage used to be prose. `ChatOllama`
raised whatever the vendor's transport raised, `api/routers/copilot.py`'s
catch-all reported it as `/problems/copilot-run-failed`, and a stopped model
container was therefore byte-identical to a bug in the graph — which is the
dishonesty this module exists to remove. There are three things in it and they
are here together for `agents/approval.py`'s reason: the degradation seam is one
idea, and a reader who wants to know what this build does when the model is down
should be able to read one file rather than assemble the answer from four.

## Why a wrapper, and not a `try/except` at the call sites

There are two places the copilot asks a model for a completion. `agents/qas.py`
`_narrate` is one and it is visible; the other is inside `langchain.agents
.create_agent`'s harness, where this build has no call site at all — the
free-chat node *is* a compiled graph the vendor wrote, and its model call
happens two frames down from anything in `agents/`.

So the only seam both inherit is the model **object**, and
`agents/chat_model.py::copilot_chat_model` is the one place it is constructed.
Wrapping what that function returns is what makes free chat and a quick action
agree about what an outage is. Two `try/except` blocks at the two visible call
sites would translate one of them and miss the other, and free chat and QAS
disagreeing about whether the model is down is exactly the failure this story
was written to close.

**A subclass rather than a delegating `BaseChatModel`.** `create_agent`
introspects the model it is given — `isinstance(model, BaseChatModel)`,
`model.profile`, `model.bind_tools(...)` — and `_narrate` calls `astream` on it.
A hand-written delegate would have to re-publish every one of those, correctly,
for ever, and would break silently the first time the harness reached for a
property nobody had thought to forward. Subclassing `ChatOllama` and overriding
the two async entry points the copilot actually uses leaves every other surface
exactly as it was, and `bind_tools` keeps working for free: the vendor's
`RunnableBinding` delegates back to *this* object's `_astream`, so a tool-bound
model translates identically to a bare one.

## Two timeouts, and the wrapper must not confuse them

`chat_request_timeout_seconds` bounds **one HTTP hop** and belongs to the model
server. A hop that times out is a server that did not answer, so it translates
to `ChatUnavailable` and the run reports `ai_unavailable`. That is the honest
reading and it is the same one `agents/client.py` already takes for the
insight path.

`copilot_run_timeout_seconds` bounds **the run** and belongs to this build.
`api/routers/copilot.py` enforces it with `asyncio.timeout` *outside* the graph,
deliberately, "because a timeout that fired inside a node would leave the
terminal decision in two places" — and it reaches `_terminal_for` as a
`TimeoutError`, which is `ai_limit`. The hazard is that the run bound can fire
while the task is suspended inside a model call: `asyncio.timeout` cancels the
task, and an over-broad `except Exception` here would relabel a run that ran out
of wall clock as a model server that was down, moving the terminal decision back
into the node the existing design pushed it out of.

Hence the re-raise rule below: `asyncio.CancelledError` passes through
untranslated. It is `BaseException` rather than `Exception` and so would not be
caught by an `except Exception` anyway; it is named explicitly regardless,
because the property is load-bearing and a reader should not have to know the
exception hierarchy to see that it holds.

## Only a transport failure is an outage — the review of this story's first cut

The first version of this file caught `except Exception` around the vendor call
and reported everything as `ChatUnavailable`. That is the conflation the
paragraph at the top says this module exists to remove, inverted: a `ValueError`
from NDJSON parsing, a pydantic `ValidationError`, a `KeyError` in the harness
or an exception raised by somebody's callback would all have become
`/problems/ai-unavailable`, and the panel would have greyed the composer and
told the handler the model server did not answer — about a bug in this build.

`_TRANSPORT_FAILURES` below is therefore an **allow-list**, and everything
outside it propagates to `api/routers/copilot.py::_terminal_for`'s catch-all as
`copilot_run_failed`, which is what that catch-all is for. The spec's Code Map
pointed at `agents/client.py:198-203` as the pattern to copy, and the important
half of that pattern is easy to miss: it names the *parse* failures first and is
only then allowed a catch-all. The streaming path has no such narrowing
available — a failure here can be anything the vendor, the harness or a callback
raises — so the allow-list runs the other way round, naming what an outage is
made of rather than what it is not. The spec's binding rule is the arbiter:
"only a failure to reach or be answered by the model server is `ai_unavailable`".

## Retry, and why it stops at the first chunk

The copilot streams. A retry after tokens have been emitted would decode a
second answer on top of a half-rendered first one, so the handler would read a
sentence, then read it again from the top — which is worse than the failure it
was trying to hide. The bound is therefore on *establishing* a completion, not
on finishing one: a connection refused retries, a connection dropped at token
two hundred does not.

`chat_max_attempts` and `chat_retry_backoff_seconds` are the knobs, and
`config.py` records why the default is two rather than larger. Nothing here
queues, nothing waits for the container to come back, and there is no unbounded
loop — AD-14 says "bounded retry, no retry storms" and the bound is a small
integer read off configuration.

## The probe is a UI signal and nothing else

`probe_model` and `ModelAvailabilityProbe` exist to drive
`GET /copilot/availability`, which exists to disable exactly the affected inputs
in the copilot panel. **No run consults them.** A `requires_llm: true` run
attempts the model and reports what actually happened, because two sources of
truth about the model's reachability would eventually disagree and the one that
matters is the one the run experienced. That is also why the panel marks the
model unavailable *reactively* on an `ai_unavailable` frame rather than waiting
for the next poll.

**And it must never be wired into a container healthcheck.** `api/app.py`'s
`/healthz` is DB-only and deliberately so: the api container is healthy without
its model. `deploy/compose.e2e.yaml` has no `restart:` policy, so a stub outage
cannot bounce the api today — folding an Ollama check into `/healthz` would
invert that, would make compose treat a model outage as an api failure, and
would grow the unauthenticated public-route set that `tests/test_problem_json.py`
pins with a route that leaks a dependency's state. AD-14's own words are that
claim operations never depend on the agent runtime; a health endpoint that goes
red when the model goes down is that dependency, written in YAML.

## AD-11

Nothing here logs a prompt, a token, a completion or a probe response body. The
outage log line carries an event name, an attempt number and the failing
exception's *class* — which distinguishes a connect refusal from a read timeout
from a defect and contains no claim data — and the probe logs nothing but the
fact that it answered false.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import ollama
import structlog
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import generate_from_stream
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_ollama import ChatOllama

from agents.client import ChatUnavailable
from config import Settings

log = structlog.get_logger()

#: What "the model server did not answer" is actually made of. **An allow-list.**
#:
#: Everything outside this tuple propagates untranslated and is classified by
#: `api/routers/copilot.py::_terminal_for`'s catch-all as `copilot_run_failed`.
#: See the module docstring: reporting a defect in this build as an outage in
#: another container is the exact dishonesty this module was written to remove,
#: and an `except Exception` here did precisely that.
#:
#: Member by member, and each earns its place by being a way the *server* fails
#: rather than a way this process does:
#:
#: * `httpx.TransportError` — the vendor's HTTP stack under `ollama`. Covers
#:   connect refusals, read/write errors, pool and read timeouts, and DNS. Note
#:   what it deliberately excludes: `httpx.HTTPStatusError` and
#:   `httpx.InvalidURL` are not transport failures, and a decode error is a
#:   malformed answer rather than an absent one.
#: * `ollama.RequestError` / `ollama.ResponseError` — the client's own two. The
#:   second is a non-2xx from the model server, which is a server that answered
#:   with a refusal; a handler cannot tell that from "down" and neither
#:   usefully can this build, so it reads as unavailability.
#: * `OSError` — the socket layer beneath all of the above, and the reason
#:   `TimeoutError` is not listed separately: it has been an `OSError` subclass
#:   since 3.3, and `asyncio.TimeoutError` has been an alias of it since 3.11.
#:   A hop that timed out is `chat_request_timeout_seconds` doing its job, which
#:   *is* an unavailability — the run-level bound is a different mechanism
#:   entirely and arrives as a `CancelledError` that is re-raised untouched.
#: * `ChatUnavailable` — already translated, by `_astream`'s zero-chunk rule
#:   below or by a nested call. Listed so that the retry and the log line treat
#:   it like any other outage rather than letting it skip the seam.
_TRANSPORT_FAILURES: tuple[type[Exception], ...] = (
    httpx.TransportError,
    ollama.RequestError,
    ollama.ResponseError,
    OSError,
    ChatUnavailable,
)

#: Ollama's liveness endpoint, and the whole of what the probe asks for.
#:
#: Named here for `CHAT_PATH`'s reason one module over: it is the string a
#: reviewer greps for when they want to know what this process sends where. It
#: is also the path `deploy/compose.e2e.yaml`'s `model-stub` healthcheck already
#: targets and the one a real Ollama serves, so the probe and the container's
#: own definition of "up" ask the same question — which is what makes the e2e
#: spec's stop-the-stub technique (AD-15) mean the same thing to both.
#:
#: `GET`, and it takes no model name: a probe that asked for a completion would
#: cost a model load and would report "unavailable" for a server that was merely
#: busy, which is a different fact from the one the panel renders.
VERSION_PATH = "/api/version"


class DegradingChatOllama(ChatOllama):
    """`ChatOllama` that says `ChatUnavailable` when the server does not answer.

    The one seam `agents/qas.py::_narrate` and `create_agent`'s internal model
    call both inherit — see the module docstring on why it is the object and not
    the call sites, and on why it is a subclass and not a delegate.

    Two extra fields and two overridden methods. The fields are pydantic fields
    because `ChatOllama` is a pydantic model and a plain attribute assigned in
    `__init__` would be rejected by it; they are constructor arguments rather
    than reads of `get_settings()` because `config.py` is the only module in this
    server allowed to touch the environment, which is `copilot_chat_model`'s
    rule and this class is built by that function.

    Only the **async** entry points are overridden, and that is a scope decision
    rather than an omission: the copilot is async end to end — the runs endpoint
    streams, the graph is `astream`ed, `_narrate` awaits — so the sync pair is
    unreachable from anything this build ships. Overriding them anyway would be
    two more code paths with no test that could exercise them, which is how a
    guarantee becomes a claim.
    """

    #: How many times one completion is attempted. See `config.chat_max_attempts`.
    max_attempts: int = 2
    #: The wait between those attempts. See `config.chat_retry_backoff_seconds`.
    retry_backoff_seconds: float = 0.5

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """One streamed completion, retried only if it never started.

        `streamed` is the whole retry policy in one boolean. Once a chunk has
        been yielded the tokens are on the wire — LangGraph's `messages` stream
        mode carries them out of the callback the base class fires — and a second
        attempt would append a second answer to a half-rendered first one. So a
        failure after the first chunk is translated and re-raised immediately,
        and only a failure *before* it is worth another go.

        **Only `_TRANSPORT_FAILURES` are translated.** Anything else propagates
        exactly as the vendor, the harness or a callback raised it, and is
        classified as `copilot_run_failed` — see the module docstring on why a
        catch-all here reported every defect in this build as an outage in
        another container.

        **A completion that yields nothing at all is an outage too**, and that
        is a decision this method now owns for both entry points. The parent's
        `_agenerate` raises `ValueError("No data received from Ollama stream.")`
        for it, which would reach `_terminal_for` as `copilot_run_failed`; the
        streaming path used to reach the end of the graph having emitted nothing
        and be reported as `copilot_empty_answer`. Two answers for one fact. It
        is stated here, once: a server that accepted the request and then said
        nothing at all did not answer, which is `ai_unavailable` — and because
        the failure is by definition before the first chunk, it is retried like
        any other. `copilot_empty_answer` keeps its own, different meaning: the
        *graph* finished and no assistant text reached the wire, which a
        tool-only run can do with a perfectly healthy model.

        `asyncio.CancelledError` is re-raised untouched, which is what keeps the
        run's own wall-clock bound classified as `ai_limit` rather than as an
        outage. See the module docstring.
        """
        for attempt in range(1, self.max_attempts + 1):
            streamed = False
            try:
                async for chunk in super()._astream(
                    messages, stop=stop, run_manager=run_manager, **kwargs
                ):
                    streamed = True
                    yield chunk
                if not streamed:
                    raise ChatUnavailable("the local model server answered nothing at all")
                return
            except asyncio.CancelledError:
                # Not an outage. The run ran out of wall clock, or the client
                # went away; either way the terminal decision belongs to
                # `_terminal_for` and not to this frame.
                raise
            except _TRANSPORT_FAILURES as exc:
                if streamed or attempt == self.max_attempts:
                    raise self._unavailable(exc, attempt=attempt) from exc
                # AD-11: an event name, an ordinal and a class name. Not a
                # prompt, not a token, not the vendor's message — an HTTP
                # error's text can echo the request body, and the request body
                # here is a claim narrative (`agents/client.py`'s rule).
                log.info(
                    "copilot.model_call_retried",
                    attempt=attempt,
                    max_attempts=self.max_attempts,
                    error=type(exc).__name__,
                )
            await asyncio.sleep(self.retry_backoff_seconds)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """One awaited completion, assembled from `_astream` above.

        **Built on the streaming path rather than beside it**, which is what
        makes "retry only before the first chunk" true on both entry points.
        `ChatOllama._agenerate` streams internally too — it aggregates
        `_aiterate_over_stream` and fires `on_llm_new_token` as it goes, so its
        tokens reach LangGraph's `messages` mode exactly like a streamed run's —
        but it hands back only the final result, so a wrapper around it could not
        tell "the server refused the connection" from "the server stopped
        talking after two hundred tokens". Re-implementing it over this class's
        own `_astream` costs four lines and makes one retry policy serve both.

        **`run_manager` is passed *inward* as `None`, and fired here instead.**
        The reason is below this method rather than above it: `ChatOllama
        ._astream` fires `on_llm_new_token` itself for every chunk it yields, so
        handing it the manager and then firing again per chunk emits each token
        twice. `BaseChatModel.astream` and `_agenerate_with_cache` both call
        `_astream` without a manager for exactly this reason. LangGraph's own
        handler happens to ignore the parent's call, which is why the copilot
        path never showed it — but any tracer or token counter attached to a run
        would have double-counted, and the earlier justification for passing it
        in reasoned about what runs above this method when the problem was
        underneath it.

        **The generation info is merged into the message's `response_metadata`**
        before the callback fires, which is what `_agenerate_with_cache` does one
        frame up and is not decoration. Ollama publishes `done_reason` on the
        final chunk's `generation_info`; `api/routers/copilot.py
        ::_stop_reason` reads `response_metadata`. Without the merge,
        `ai_limit` was structurally undetectable on this path — a backstop that
        silently lost the feature this story exists to add. Written out rather
        than importing `langchain_core`'s `_gen_info_and_msg_metadata`, because
        that name is private and a vendor's underscore is not an API; the one
        behaviour dropped with it is a gateway-metadata filter for a gateway
        this build does not have.

        **In the copilot's own path this method is not reached at all**, and that
        is worth knowing rather than discovering. `BaseChatModel._should_stream`
        answers `True` whenever a streaming callback handler is attached, and
        LangGraph's `messages` stream mode attaches one — so a run inside the
        graph takes `_astream` above even when the node awaits `ainvoke`. This is
        the backstop for every other context, and the reason it exists rather
        than being left to the parent is that the parent's version cannot honour
        the retry rule.

        A completion that streamed nothing cannot reach the aggregation below:
        `_astream` raises `ChatUnavailable` for it, which is where the two entry
        points were made to agree. See that method.
        """
        chunks: list[ChatGenerationChunk] = []
        async for chunk in self._astream(messages, stop=stop, run_manager=None, **kwargs):
            chunk.message.response_metadata = {
                **(chunk.generation_info or {}),
                **chunk.message.response_metadata,
            }
            if run_manager is not None:
                await run_manager.on_llm_new_token(chunk.text, chunk=chunk)
            chunks.append(chunk)
        return generate_from_stream(iter(chunks))

    def _unavailable(self, exc: Exception, *, attempt: int) -> ChatUnavailable:
        """The translated exception, plus the one log line an outage gets.

        A method rather than an inline raise so the log and the exception cannot
        drift apart, and so there is exactly one place in this build that decides
        a vendor failure means "the model server did not answer".

        The message is the fixed sentence `agents/client.py` uses, and it never
        stringifies `exc`: the cause is chained for a debugger, which is where a
        vendor's message may safely be read, and nowhere else.
        """
        log.warning(
            "copilot.model_unavailable",
            attempts=attempt,
            error=type(exc).__name__,
        )
        return ChatUnavailable("the local model server did not answer")


async def probe_model(settings: Settings) -> bool:
    """Is the model server answering? One `GET`, a bool, and never a raise.

    Total by construction, because every caller is a UI signal and none of them
    has anything useful to do with an exception: `GET /copilot/availability`
    answers 200 with `available: false` during an outage precisely so the panel
    has something it can render. A health endpoint that errors when the thing it
    reports on is unhealthy has told the caller nothing.

    **Nothing about the response is read except its status.** Not the version
    string, not the body, not its length. AD-11 forbids a payload in a log line,
    and a probe that parsed a body would be a probe that could be made to log
    one; there is also nothing in the answer this build would know what to do
    with — the question is "did something answer on the model port", and a
    status code is the whole of it.
    """
    url = f"{settings.ollama_base_url.rstrip('/')}{VERSION_PATH}"
    try:
        async with httpx.AsyncClient(timeout=settings.ai_health_probe_timeout_seconds) as client:
            response = await client.get(url)
    except Exception as exc:
        # AD-11: a class name and nothing else. `info` rather than `warning`:
        # an outage is already logged, loudly, by the run that hit it — this is
        # a panel asking a question, and a probe that logged at warning every
        # ten seconds for the length of an outage would bury it.
        log.info("copilot.availability_probe_failed", error=type(exc).__name__)
        return False
    return response.is_success


class ModelAvailabilityProbe:
    """`probe_model`, cached for `ai_health_probe_cache_seconds`.

    The object `api/app.py` builds once and hangs on `CopilotRuntime`, so a
    request reads the probe's bounds off the runtime rather than re-reading
    `Settings` at request time — the same rule the per-run bounds keep, and for
    the same reason: configuration is resolved once, in `lifespan`, by the one
    module allowed to.

    ## Two properties, and both are about N clients rather than one

    **The cache.** Every open copilot panel polls availability, and a handler may
    have the console open in three tabs. Without a cache that is one upstream
    request per panel per interval, against a server whose whole problem might be
    that it is overloaded. Ten seconds of staleness is free here because nothing
    correctness-bearing reads this (see the module docstring: no run consults the
    probe).

    **The lock.** A cache alone still lets N concurrent callers arriving on a
    cold entry all miss it and all probe. `asyncio.Lock` makes them one probe and
    N readers of its result, which is what "so N clients are one upstream
    request" actually requires. The lock is created lazily rather than in
    `__init__` for `agents/client.py`'s recorded reason: an asyncio primitive
    binds itself to a loop, and this object is constructed in `lifespan` on the
    loop it will be used on, but the tests construct it outside one.

    `time.monotonic` rather than a wall clock, because the only question asked of
    it is "how long since", and a wall clock that steps backwards over an NTP
    correction would freeze a cached `false` for as long as the step.

    ## `force`, because a human pressing "Try again" is not a poll

    Three docstrings in this build — `config.py`, `AVAILABILITY_POLL_MS` and
    `ActionsTab.tsx` — promised that the manual retry "shortens to zero" the
    wait after a recovery, and AC 5 depends on it. It did not: `refetch()`
    re-issued `GET /copilot/availability`, which was served from this cache, so
    inside the window the retry handed back the identical stale `false` and the
    handler pressed a button that could not help them. The review of this story
    found it; `force` is the fix, and the cache stays, because coalescing the
    *poll* is the whole reason it exists.

    So the two callers are told apart at the seam rather than by a second
    object: the background poll asks the cached question, an explicit human
    retry asks the server. Forced callers are still coalesced with each other —
    a caller that waited on the lock while somebody else's probe ran gets that
    answer if it was taken **after this call began**, which is the honest
    reading of "no older than the moment I asked" and stops three tabs pressing
    "Try again" from making three upstream requests.

    Nothing on the run path can reach `force`: no run consults the probe at all
    (see the module docstring), so there is no path by which a retry storm in a
    browser becomes load on the model server beyond one request per press.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock: asyncio.Lock | None = None
        self._answered_at: float | None = None
        self._available = False

    async def available(self, *, force: bool = False) -> bool:
        """The cached answer, or one fresh probe shared by everyone waiting.

        `force` skips the cached answer — but not the coalescing. See the class
        docstring: it is what a handler pressing "Try again" asks for, and it is
        never asked for by the poll.
        """
        asked_at = time.monotonic()
        if not force and self._fresh():
            return self._available
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            # Re-checked under the lock: whoever held it while this caller was
            # waiting has just answered the same question, and probing again
            # would make the lock a queue rather than a coalescer. A forced
            # caller accepts that answer only if it was taken after this call
            # began — which is the difference between "fresh enough to poll
            # with" and "fresh enough to have answered *me*".
            if self._answered_at is not None and (
                self._answered_at >= asked_at if force else self._fresh()
            ):
                return self._available
            self._available = await probe_model(self._settings)
            self._answered_at = time.monotonic()
        return self._available

    def _fresh(self) -> bool:
        """Whether the cached answer is still inside its window.

        Reads the knob off `self._settings` rather than off a copy taken in
        `__init__`: two paths to one number is two things to keep in step, and
        `CopilotRuntime` already holds this object precisely so that the bound
        is resolved once, in `lifespan`.
        """
        if self._answered_at is None:
            return False
        return (time.monotonic() - self._answered_at) < self._settings.ai_health_probe_cache_seconds


__all__ = [
    "VERSION_PATH",
    "DegradingChatOllama",
    "ModelAvailabilityProbe",
    "probe_model",
]
