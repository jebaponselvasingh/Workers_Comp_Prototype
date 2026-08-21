"""What the copilot does when the model is down or a bound is reached (Story 6.6).

AD-14's degradation clauses were prose until this story: `ai_unavailable` and
`ai_limit` existed in the architecture and nowhere in the code, and an
unreachable Ollama arrived at the runs endpoint as an anonymous vendor exception
reported as `/problems/copilot-run-failed` — byte-identical to a bug in the
graph. This module is what makes each clause checkable.

## Two levels, and both are needed

**Unit, against the wrapper.** `agents/degradation.DegradingChatOllama` is the
one translation seam, and the two properties that keep the bound honest are
countable rather than observable from outside: the attempt count, and the rule
that a call which already streamed a chunk is *not* retried. A stream-level test
could pass with an unbounded retry loop behind it — it would just be slow.

**Through the shipped stream, against a real database.** Every other assertion
here is about a composition — a wrapper, a graph, a producer task, a terminal
classifier, an advisory lock and a checkpoint saver — and the failure this
module exists to catch is one of them being subtly not wired to the next. So the
outage tests drive the **real `DegradingChatOllama`** with its transport
monkeypatched, rather than a fake that raises `ChatUnavailable` itself: a fake
would skip the vendor-error-to-`ChatUnavailable` translation, which is one of
the two translations that can go wrong.

## What is deliberately not asserted

A sentence. `read_stream_with_payloads` returns problem documents, which are
this build's own structure; nothing here reads a word a model wrote (AD-15). Nor
is there any assertion that a *cloud* call was avoided, because there is no
cloud code path to avoid — AD-5's guarantee is the absence of one, and
`tests/test_layering.py` plus the spec's grep are where an absence is checked.
"""

import asyncio
import contextlib
import dataclasses
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import httpx
import ollama
import pytest
from langchain_core.callbacks import AsyncCallbackHandler, AsyncCallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.messages.tool import tool_call_chunk as create_tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_ollama import ChatOllama
from pydantic import ValidationError

from agents import degradation, qas
from agents.chat_model import copilot_chat_model
from agents.client import ChatUnavailable
from agents.degradation import DegradingChatOllama, ModelAvailabilityProbe, probe_model
from agents.graph import QUICK_ACTIONS, quick_action_flags
from agents.registry import build_tools
from api.routers.copilot import _stop_reason
from config import Settings
from tests.conftest import requires_db
from tests.test_copilot_threads import (
    HANDLER,
    a_claim_of,
    a_run_held_mid_flight,
    login_as,
    make_client,
)

pytestmark = pytest.mark.anyio

#: A base URL nothing is listening on. Port 1 is privileged and unused, so a
#: connect fails immediately rather than after a DNS or a firewall timeout —
#: which matters because these tests assert what happens on a refusal and would
#: otherwise be asserting how long a refusal takes.
CLOSED_URL = "http://127.0.0.1:1"


def _settings(**overrides: Any) -> Settings:
    """`Settings` with a database URL that is never connected to.

    Only the model knobs matter to the objects built from it here — the probe
    reads `ollama_base_url` and two timeouts, the wrapper reads two bounds — but
    `database_url` has no default, so it has to be something.
    """
    return Settings(
        database_url="postgresql://unused:unused@127.0.0.1:1/unused",
        env="e2e",  # type: ignore[arg-type]
        **overrides,
    )


# --- the wrapper, counted ------------------------------------------------


def _failing_transport(calls: list[str], error: BaseException) -> Any:
    """A `ChatOllama._astream` that records the call and refuses before yielding.

    Monkeypatched onto **`ChatOllama`** rather than onto the subclass, because
    `DegradingChatOllama._astream` reaches its parent through `super()` — so this
    replaces exactly the vendor half and leaves the whole of the code under test
    running for real.

    The unreachable `yield` is what makes this an async generator function; the
    raise happens on the first `__anext__`, which is a connection refused.

    `error` is a `BaseException` rather than an `Exception` because one caller
    raises `asyncio.CancelledError` — the case the wrapper must **not** treat as
    an outage, and therefore the one worth being able to script here.
    """

    async def _astream(
        self: ChatOllama,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        calls.append("attempt")
        raise error
        yield  # pragma: no cover - unreachable; makes this an async generator

    return _astream


def _dropping_transport(calls: list[str], error: Exception) -> Any:
    """A `ChatOllama._astream` that yields one chunk and *then* fails.

    The mid-stream failure. Its whole purpose is to be **not retried**: a second
    attempt would decode a second answer on top of a half-rendered first one, so
    the handler would read a sentence and then read it again from the top.
    """

    async def _astream(
        self: ChatOllama,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        calls.append("attempt")
        yield ChatGenerationChunk(message=AIMessageChunk(content="half an ans"))
        raise error

    return _astream


def _degrading(**overrides: Any) -> DegradingChatOllama:
    """The shipped wrapper, pointed nowhere, with the retry bounds a test wants."""
    return DegradingChatOllama(
        **{
            "base_url": CLOSED_URL,
            "model": "test-model",
            "max_attempts": 2,
            "retry_backoff_seconds": 0.0,
            **overrides,
        }
    )


async def _drain(model: BaseChatModel) -> list[str]:
    """Everything one completion streamed, as text. Raises whatever it raised."""
    return [
        str(chunk.content) async for chunk in model.astream([HumanMessage(content="a question")])
    ]


async def test_a_refused_connection_is_translated_and_tried_exactly_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC 1's bound, asserted as a **call count** rather than as "it stopped".

    "Bounded retry" is the one clause of AD-14 that a passing outage test can
    satisfy vacuously: an unbounded loop that eventually gives up and a bounded
    one produce the same terminal frame. So this counts.

    Three rather than the shipped default of two, so that the assertion is
    reading the knob rather than coinciding with a hard-coded number — a wrapper
    that always tried twice would pass an `== 2` and fail this.
    """
    calls: list[str] = []
    monkeypatch.setattr(
        ChatOllama, "_astream", _failing_transport(calls, httpx.ConnectError("refused"))
    )
    model = _degrading(max_attempts=3)

    with pytest.raises(ChatUnavailable):
        await _drain(model)

    assert len(calls) == 3


async def test_a_failure_after_the_first_chunk_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the bound, and the one a naive retry gets wrong.

    The copilot streams, so a retry after tokens have reached the wire would put
    the answer on screen twice. The rule is therefore about *establishing* a
    completion rather than about finishing one — and the assertion is that the
    partial text survived and no second attempt was made.
    """
    calls: list[str] = []
    monkeypatch.setattr(
        ChatOllama, "_astream", _dropping_transport(calls, httpx.ReadError("dropped"))
    )
    model = _degrading(max_attempts=3)

    streamed: list[str] = []
    with pytest.raises(ChatUnavailable):
        async for chunk in model.astream([HumanMessage(content="a question")]):
            streamed.append(str(chunk.content))

    assert len(calls) == 1, "a mid-stream failure was retried; the answer would be duplicated"
    assert "".join(streamed) == "half an ans"


@pytest.mark.parametrize(
    "defect",
    [
        ValueError("the NDJSON body was not JSON"),
        KeyError("message"),
        RuntimeError("a callback raised"),
    ],
    ids=["parse", "key", "callback"],
)
async def test_a_defect_in_this_build_is_never_reported_as_an_outage(
    monkeypatch: pytest.MonkeyPatch, defect: Exception
) -> None:
    """The narrowing, and it is the review of this story's largest finding.

    The first cut of `agents/degradation.py` caught `except Exception` around
    the vendor call, so a `ValueError` from NDJSON parsing, a `KeyError` in the
    harness or an exception raised by somebody's callback all became
    `ChatUnavailable` → `/problems/ai-unavailable` → a greyed-out composer and a
    sentence telling the handler the local model server did not answer, about a
    bug in this build. That is the conflation the module's own opening paragraph
    says it exists to remove, inverted.

    The spec's binding rule is the arbiter: "only a failure to reach or be
    answered by the model server is `ai_unavailable`". So these propagate
    untouched, reach `_terminal_for`'s catch-all and are reported as
    `copilot_run_failed` — asserted end to end by
    `test_a_defect_reaches_the_stream_as_copilot_run_failed` below.

    The call count is half the assertion: a defect is not retried either, which
    it would have been while it was being read as a transport failure.
    """
    calls: list[str] = []
    monkeypatch.setattr(ChatOllama, "_astream", _failing_transport(calls, defect))
    model = _degrading(max_attempts=3)

    with pytest.raises(type(defect)):
        await _drain(model)

    assert len(calls) == 1, "a defect was retried as though the model server had not answered"


@pytest.mark.parametrize(
    "outage",
    [
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("the hop timed out"),
        ConnectionResetError("the socket went away"),
        ollama.ResponseError("the model server refused"),
    ],
    ids=["connect", "hop-timeout", "socket", "vendor"],
)
async def test_every_transport_failure_is_the_same_outage(
    monkeypatch: pytest.MonkeyPatch, outage: Exception
) -> None:
    """…and the other side of the allow-list, so narrowing did not become a hole.

    Each of these is a way the *server* failed rather than a way this process
    did, and each has to keep arriving as `ChatUnavailable`. The hop timeout is
    the one worth naming: `chat_request_timeout_seconds` bounds one HTTP request
    and belongs to the model server, so a hop that timed out is a server that
    did not answer — unlike `copilot_run_timeout_seconds`, which belongs to this
    build and reaches the classifier as a `TimeoutError` after a cancellation.
    """
    calls: list[str] = []
    monkeypatch.setattr(ChatOllama, "_astream", _failing_transport(calls, outage))
    model = _degrading(max_attempts=2)

    with pytest.raises(ChatUnavailable):
        await _drain(model)

    assert len(calls) == 2


async def test_a_completion_that_yields_nothing_is_an_outage_on_both_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two entry points now give one answer to one fact (6.6 review, LOW 17).

    A server that accepted the request and then said nothing at all used to be
    two different failures depending on which method the caller reached: the
    awaited path raised `ChatUnavailable` and the streaming path ran to the end
    of the graph having emitted nothing, which `_terminal_for` reported as
    `copilot_empty_answer`.

    The intended answer is `ai_unavailable` on both, and it is decided in
    `_astream` so there is one place it is decided. `copilot_empty_answer` keeps
    its own, different meaning — the *graph* finished and no assistant text
    reached the wire, which a tool-only run can do against a perfectly healthy
    model.

    Retried, too, because a completion that never produced a chunk failed by
    definition before its first one.
    """
    calls: list[str] = []

    async def _silent(
        self: ChatOllama,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        calls.append("attempt")
        return
        yield  # pragma: no cover - unreachable; makes this an async generator

    monkeypatch.setattr(ChatOllama, "_astream", _silent)
    model = _degrading(max_attempts=2)

    with pytest.raises(ChatUnavailable):
        await _drain(model)
    with pytest.raises(ChatUnavailable):
        await model.ainvoke([HumanMessage(content="a question")])

    assert len(calls) == 4, "the silent completion was not retried on one of the two paths"


async def test_cancellation_is_never_reported_as_an_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run that ran out of wall clock is `ai_limit`, and this is why it stays so.

    `api/routers/copilot.py` wraps the whole stream in `asyncio.timeout`, which
    cancels the task — and the task is very often suspended inside a model call
    when it fires. An over-broad `except Exception` in the wrapper would relabel
    that cancellation as a model server that did not answer, moving the terminal
    decision back into the node the existing design deliberately pushed it out
    of, and telling the handler the model was down when it was merely slow.

    Asserted at the wrapper because that is where the mistake would be made; the
    end-to-end half is `test_a_wall_clock_overrun_is_one_ai_limit_frame` below.
    """
    calls: list[str] = []
    monkeypatch.setattr(ChatOllama, "_astream", _failing_transport(calls, asyncio.CancelledError()))
    model = _degrading(max_attempts=3)

    with pytest.raises(asyncio.CancelledError):
        await _drain(model)

    assert len(calls) == 1, "a cancelled call was retried; the run bound would not bound anything"


async def test_the_awaited_path_translates_and_bounds_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ainvoke` inherits the seam, because `_agenerate` is built on `_astream`.

    `create_agent`'s model node awaits rather than streams, and LangGraph turns
    that into a streamed call only while a streaming callback handler is
    installed. Both shapes have to translate the same way or free chat and a
    quick action would disagree about what an outage is — which is exactly the
    dishonesty this story exists to remove.
    """
    calls: list[str] = []
    monkeypatch.setattr(
        ChatOllama, "_astream", _failing_transport(calls, httpx.ConnectError("refused"))
    )
    model = _degrading(max_attempts=2)

    with pytest.raises(ChatUnavailable):
        await model.ainvoke([HumanMessage(content="a question")])

    assert len(calls) == 2


def test_the_shipped_model_carries_the_configured_bounds() -> None:
    """The knobs reach the object, which is what makes them knobs.

    `copilot_chat_model` is the one construction site; a wrapper built there with
    its defaults rather than with `Settings` would leave `chat_max_attempts` a
    field nobody read, and the count assertions above would still pass.

    The per-request timeout is lowered alongside the attempt count because
    `Settings` now refuses a combination whose retry cannot finish inside the run
    bound — see `test_a_retry_that_cannot_fit_inside_the_run_bound_is_refused`.
    Five attempts at the default 120 s is exactly the shape that validator
    exists to stop, so a test that wanted five attempts has to say what it is
    doing to the hop.
    """
    settings = _settings(
        chat_max_attempts=5,
        chat_retry_backoff_seconds=1.5,
        chat_request_timeout_seconds=30.0,
    )

    model = copilot_chat_model(settings)

    assert isinstance(model, DegradingChatOllama)
    assert model.max_attempts == 5
    assert model.retry_backoff_seconds == 1.5


def test_a_retry_that_cannot_fit_inside_the_run_bound_is_refused() -> None:
    """Four knobs, one arithmetic relationship, checked at start-up (6.6 review).

    The retry does not shorten the worst case against a *hung* server — it
    doubles it. Two attempts at 120 s plus a backoff is 240.5 s inside a 300 s
    run bound; three attempts is 361 s, which the run's own `asyncio.timeout`
    cuts off first, so **every outage would be reported as `ai_limit`** — the
    code that means "a bound this deployment set was reached" — and the panel
    would never disable an input over a model server that was genuinely down.
    Silent, production-only, and the exact conflation this story exists to
    remove.

    So the process refuses to start and names both numbers, rather than leaving
    the arithmetic to whoever next raises a knob. Asserted in both directions:
    the shipped defaults must be a combination that starts.
    """
    with pytest.raises(ValidationError) as raised:
        _settings(chat_max_attempts=3)

    message = str(raised.value)
    assert "CHAT_MAX_ATTEMPTS=3" in message
    assert "COPILOT_RUN_TIMEOUT_SECONDS=300.0" in message

    # The positive control, and it is not decoration: a validator with the
    # comparison the wrong way round would pass the assertion above and refuse
    # every deployment there is.
    assert _settings().chat_max_attempts == 2


class _TokenRecorder(AsyncCallbackHandler):
    """Counts `on_llm_new_token`, which is how a double-fire becomes visible."""

    def __init__(self) -> None:
        self.tokens: list[str] = []

    async def on_llm_new_token(self, token: Any, **kwargs: Any) -> None:
        self.tokens.append(str(token))


def _parent_shaped_transport(calls: list[str]) -> Any:
    """A `ChatOllama._astream` that behaves like the real one in the two ways that matter.

    It **fires `on_llm_new_token` itself** when it is handed a `run_manager`,
    which the vendor does, and which is the whole reason `_agenerate` must not
    hand it one; and it publishes the completion's stop reason on the final
    chunk's `generation_info`, which is where `langchain_ollama` puts it (pinned
    against the real client by
    `test_the_vendor_publishes_its_stop_reason_where_this_build_reads_it`).

    A faithful stand-in rather than a convenient one: a fake that did not fire
    the callback would make the double-fire it exists to catch unobservable.
    """

    async def _astream(
        self: ChatOllama,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        calls.append("attempt")
        chunk = ChatGenerationChunk(message=AIMessageChunk(content=TRUNCATED_TEXT))
        if run_manager is not None:
            await run_manager.on_llm_new_token(chunk.text, chunk=chunk)
        yield chunk
        final = ChatGenerationChunk(
            message=AIMessageChunk(content=""),
            generation_info={"done": True, "done_reason": "length"},
        )
        if run_manager is not None:
            await run_manager.on_llm_new_token(final.text, chunk=final)
        yield final

    return _astream


async def test_the_awaited_path_fires_one_callback_per_chunk_and_keeps_the_stop_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two defects the review of this story found in one method, asserted together.

    **One callback per chunk.** `_agenerate` passed `run_manager` into
    `self._astream(...)` and then fired `on_llm_new_token` again per chunk —
    and `ChatOllama._astream` fires it too, so every token was announced twice.
    `BaseChatModel.astream` and `_agenerate_with_cache` both call `_astream`
    *without* a manager for exactly this reason. LangGraph's handler happens to
    ignore the parent's call, which is what hid it on the copilot path; any
    tracer or token counter would have double-counted.

    **And the stop reason survives.** Ollama publishes `done_reason` on the
    final chunk's `generation_info`, and `api/routers/copilot.py::_stop_reason`
    reads `response_metadata` — the merge `_agenerate_with_cache` does one frame
    up was missing here, so `ai_limit` was *structurally undetectable* on this
    path: a backstop that silently lost the feature this story added.

    Driven through a real `AsyncCallbackManagerForLLMRun` rather than a stub, so
    the count is the count a tracer would see.
    """
    calls: list[str] = []
    monkeypatch.setattr(ChatOllama, "_astream", _parent_shaped_transport(calls))
    recorder = _TokenRecorder()
    manager = AsyncCallbackManagerForLLMRun(
        run_id=uuid4(), handlers=[recorder], inheritable_handlers=[]
    )
    model = _degrading()

    result = await model._agenerate([HumanMessage(content="a question")], run_manager=manager)

    assert recorder.tokens == [TRUNCATED_TEXT, ""], "each token was announced more than once"
    assert result.generations[0].message.response_metadata.get("done_reason") == "length"


async def test_the_vendor_publishes_its_stop_reason_where_this_build_reads_it() -> None:
    """**Pins `langchain_ollama`'s wire contract**, which no other test does.

    Everything else in this build that asserts `ai_limit` drives a fake whose
    `generation_info` this repository writes — so it asserts this repository's
    idea of the shape rather than the vendor's. If `langchain_ollama` stopped
    copying the final NDJSON object onto the chunk, or `langchain_core` stopped
    merging that into `response_metadata`, truncated answers would quietly go
    back to terminating `done` and every one of those tests would still pass.

    So this one speaks Ollama's own wire format over a mocked transport and
    reads the answer through `_stop_reason` — the shipped function, at the
    shipped attribute. The `ai_limit` path's two ends are then both real: this is
    the vendor half, and `e2e/stories/6-6-*.spec.ts` is the composed-stack half
    against `deploy/model-stub`.
    """
    lines = [
        {"message": {"role": "assistant", "content": TRUNCATED_TEXT}, "done": False},
        {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "length"},
    ]

    def _handler(request: httpx.Request) -> httpx.Response:
        body = b"".join((json.dumps(line) + "\n").encode() for line in lines)
        return httpx.Response(200, content=body, headers={"content-type": "application/x-ndjson"})

    model = _degrading(async_client_kwargs={"transport": httpx.MockTransport(_handler)})

    reasons = [
        _stop_reason(chunk) async for chunk in model.astream([HumanMessage(content="a question")])
    ]

    assert "length" in reasons, (
        "langchain_ollama no longer publishes done_reason where _stop_reason looks; "
        "ai_limit is undetectable and every fake-driven test still passes"
    )


# --- the probe -----------------------------------------------------------


async def test_the_probe_answers_false_for_an_unreachable_server_and_never_raises() -> None:
    """Total by construction. Every caller is a UI signal with no use for a raise.

    `GET /copilot/availability` answers 200 with `available: false` during an
    outage precisely so the panel has something it can render, and that is only
    possible if the probe underneath it cannot fail.
    """
    settings = _settings(ollama_base_url=CLOSED_URL, ai_health_probe_timeout_seconds=1.0)

    assert await probe_model(settings) is False


async def test_concurrent_callers_cost_one_upstream_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cache **and** the lock, asserted together because either alone leaks.

    Every open copilot panel polls this, and a handler may have three tabs. A
    cache with no lock still lets N callers arriving on a cold entry all miss it
    and all probe — against a server whose whole problem might be that it is
    overloaded. So the property is not "the second call is cached", it is "ten
    simultaneous first calls are one request".
    """
    calls: list[str] = []

    async def _probe(settings: Settings) -> bool:
        calls.append("probe")
        await asyncio.sleep(0)
        return True

    monkeypatch.setattr(degradation, "probe_model", _probe)
    probe = ModelAvailabilityProbe(_settings(ai_health_probe_cache_seconds=60.0))

    answers = await asyncio.gather(*(probe.available() for _ in range(10)))
    answers += [await probe.available()]

    assert answers == [True] * 11
    assert len(calls) == 1


async def test_an_expired_cache_probes_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recovery has to be observable, or the panel stays disabled after it.

    The positive control on the test above: a cache that never expired would
    pass that one and would leave a handler looking at a greyed-out composer for
    as long as the process lived.
    """
    calls: list[str] = []

    async def _probe(settings: Settings) -> bool:
        calls.append("probe")
        return len(calls) > 1

    monkeypatch.setattr(degradation, "probe_model", _probe)
    probe = ModelAvailabilityProbe(_settings(ai_health_probe_cache_seconds=0.01))

    assert await probe.available() is False
    await asyncio.sleep(0.05)
    assert await probe.available() is True
    assert len(calls) == 2


async def test_a_forced_probe_bypasses_the_cache_and_a_poll_still_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The manual retry asks the server; the poll asks the cache (6.6 review).

    Three docstrings in this build promised that "Try again" shortens the
    post-recovery wait to zero, and AC 5 rests on it — and it did not: the retry
    re-issued a request the cache answered, so inside the window it handed back
    the identical stale `false` and a handler who had just restarted their model
    container pressed a control that could not help them.

    Both halves are asserted here because either alone is the wrong fix. Forcing
    has to see the recovery *now*, and a poll immediately after must still be
    coalesced — deleting the cache would have made the first assertion pass and
    turned an endpoint every open panel polls into one upstream request per
    panel per interval.
    """
    answers = iter([False, True])
    probes: list[str] = []

    async def _probe(settings: Settings) -> bool:
        probes.append("probe")
        return next(answers)

    monkeypatch.setattr(degradation, "probe_model", _probe)
    probe = ModelAvailabilityProbe(_settings(ai_health_probe_cache_seconds=60.0))

    assert await probe.available() is False
    # The poll, inside the window: cached, and still wrong, which is the
    # deliberate part.
    assert await probe.available() is False
    assert len(probes) == 1

    # The human. One more upstream request, and the truth.
    assert await probe.available(force=True) is True
    assert len(probes) == 2

    # …and the poll that follows reads the forced answer rather than paying for
    # a third probe: forcing refreshes the cache, it does not disable it.
    assert await probe.available() is True
    assert len(probes) == 2


async def test_forced_probes_are_coalesced_with_each_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three tabs pressing "Try again" are one upstream request, not three.

    The positive control on `force` not being a hole in the coalescing. A caller
    that waited on the lock accepts an answer taken **after its own call began**,
    which is the honest reading of "no older than the moment I asked" — and is
    why `force` re-checks a timestamp under the lock rather than probing
    unconditionally.
    """
    probes: list[str] = []

    async def _probe(settings: Settings) -> bool:
        probes.append("probe")
        await asyncio.sleep(0)
        return True

    monkeypatch.setattr(degradation, "probe_model", _probe)
    probe = ModelAvailabilityProbe(_settings(ai_health_probe_cache_seconds=60.0))

    answers = await asyncio.gather(*(probe.available(force=True) for _ in range(3)))

    assert answers == [True] * 3
    assert len(probes) == 1


# --- the quick-action builder split --------------------------------------


def test_a_key_with_no_builder_says_so_rather_than_blaming_a_prompt_file() -> None:
    """The start-up diagnosis, restored after the map was split (6.6 review).

    A single `_BUILDERS` map answered a key it had no builder for with a
    `KeyError` naming the key — a two-word answer to "what did I forget?".
    Splitting it in two put the prompt-file pairing check first, so a key added
    to `QUICK_ACTIONS` with no node anywhere came out as "declares requires_llm
    and names no prompt file": a sentence about the wrong field, sending its
    reader to look in a prompt directory for a function they had not written.

    So the lookup happens first, and a key registered in the map its flag does
    not name gets its own sentence — "there is no builder for this" and "the
    builder for this is the other kind" are different mistakes, and neither
    reader should have to diff two dicts to find out which one they made.

    Every one of these fails in `lifespan`, which is a process that will not
    start rather than one quick action that misbehaves at run time.
    """
    model = _NeverCalledModel()

    with pytest.raises(KeyError, match="no node builder"):
        qas.build_node("nosuchkey", requires_llm=True, prompt_key="nosuch", model=model)
    with pytest.raises(KeyError, match="no node builder"):
        qas.build_node("nosuchkey", requires_llm=False, prompt_key=None, model=model)

    # Registered, but in the other map. Named as such rather than as a missing
    # prompt file or a missing node.
    with pytest.raises(KeyError, match="only builder is deterministic"):
        qas.build_node("data_alignment", requires_llm=True, prompt_key="reserve", model=model)
    with pytest.raises(KeyError, match="only builder is a narrating one"):
        qas.build_node("reserve", requires_llm=False, prompt_key=None, model=model)

    # …and the prompt-file pairing still raises, for the key it is really about.
    with pytest.raises(ValueError, match="names no prompt file"):
        qas.build_node("reserve", requires_llm=True, prompt_key=None, model=model)
    with pytest.raises(ValueError, match="names a prompt file"):
        qas.build_node("data_alignment", requires_llm=False, prompt_key="reserve", model=model)


# --- the stream, through the shipped routes ------------------------------


class _NeverCalledModel(BaseChatModel):
    """A model that fails the test if anything asks it for a completion.

    `tests/test_copilot_approval.py`'s device, declared local to this module per
    the house convention. It is what turns "the false path makes no model call"
    from a count into a structural claim: a call *count* of zero passes against a
    model that was called and answered, while this fails at the call, with the
    stack that made it.

    Story 6.6 adds a second guard beside it — `agents/qas.build_node` hands a
    `requires_llm: False` builder no model at all — and the two are belt and
    braces rather than duplicates: types stop the model being reachable, this
    stops it being called, and neither alone is the guarantee.
    """

    @property
    def _llm_type(self) -> str:
        return "never-called"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:  # pragma: no cover - the assertion is that this never runs
        raise AssertionError("the requires_llm: false path called the model")


class _TruncatedChatModel(BaseChatModel):
    """A model whose completion stops on its token ceiling, the way Ollama says so.

    The final chunk carries `done_reason: "length"` on its `generation_info`,
    which is the shape `langchain_ollama` produces (it copies the whole final
    NDJSON object there) and which `langchain_core` merges into
    `AIMessageChunk.response_metadata` before the callback LangGraph's `messages`
    stream mode listens on. `deploy/model-stub/app.py` emits the same field, so
    this fake and the e2e stack are describing one wire format.

    The content chunk before it is what AC 4 is actually about: the decoded
    tokens must **stay in the transcript**, because the run is a completed answer
    that was cut short and not a lost one.
    """

    @property
    def _llm_type(self) -> str:
        return "truncated"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:  # pragma: no cover - the streaming path is the one under test
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=TRUNCATED_TEXT))])

    async def _astream(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        yield ChatGenerationChunk(message=AIMessageChunk(content=TRUNCATED_TEXT))
        yield ChatGenerationChunk(
            message=AIMessageChunk(content=""),
            generation_info={"done": True, "done_reason": "length"},
        )


#: What the truncated model decodes before it is cut off. Filler, and asserted
#: only for its presence — this suite reads no model's prose (AD-15).
TRUNCATED_TEXT = "Deterministic test answer that stops before it is"

#: What the *completed* answer in the two-turn script below says. Filler for the
#: same reason, and asserted for the same one thing: that it arrived.
ANSWERED_TEXT = "Deterministic test answer that finishes."


@dataclasses.dataclass(frozen=True)
class _ScriptedTurn:
    """One completion: what it said, what it called, and how it stopped.

    The third field is the point. Every other scripted model in this suite can
    say what a turn *contains*; none of them can say how it *ended*, and "how it
    ended" is the whole of Story 6.6's `num_predict` half. A turn that stops on
    `length` and a turn that stops on `stop` are otherwise identical on the
    wire.
    """

    content: str
    tool_call: dict[str, Any] | None
    done_reason: str


#: A read this build already registers, so the loop below runs the real tool.
#:
#: `claim_reader` on the claim the thread is about — the tool's own scope guard
#: refuses anything else, which is what makes it safe to script here without
#: teaching this test what a caller may see.
_A_TOOL_CALL: dict[str, Any] = {
    "name": "claim_reader",
    "args": json.dumps({"claim_business_id": a_claim_of(HANDLER)}),
    "id": "call-truncated-1",
    "index": 0,
}


class _ScriptedStreamingChatModel(BaseChatModel):
    """A model that streams a scripted sequence of completions, stop reasons included.

    Streaming rather than `_generate`-only, because the fact under test rides on
    a chunk: `done_reason` reaches this build through the callback LangGraph's
    `messages` stream mode listens on, and a model that only implemented
    `_generate` would never produce one.

    `bind_tools` returns `self`, `_ToolCallingChatModel`'s move in
    `tests/test_copilot_graph.py` and for its reason: `create_agent` binds
    tools, and a fake that cannot be bound is a fake that can never make the
    tool call this test needs to produce a second completion.
    """

    script: Any

    @property
    def _llm_type(self) -> str:
        return "scripted-streaming"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:  # pragma: no cover - the streaming path is the one under test
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=ANSWERED_TEXT))])

    async def _astream(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        turn: _ScriptedTurn = next(self.script)
        if turn.content:
            yield ChatGenerationChunk(message=AIMessageChunk(content=turn.content))
        # The terminal chunk: empty content, the tool call if there is one, and
        # the stop reason on `generation_info` — which is exactly where
        # `langchain_ollama` puts it (pinned by the MockTransport test above).
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                tool_call_chunks=(
                    [create_tool_call_chunk(**turn.tool_call)] if turn.tool_call is not None else []
                ),
            ),
            generation_info={"done": True, "done_reason": turn.done_reason},
        )


@contextlib.contextmanager
def _unreachable_model(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """The shipped wrapper over a transport that refuses. Yields the attempt log."""
    calls: list[str] = []
    monkeypatch.setattr(
        ChatOllama, "_astream", _failing_transport(calls, httpx.ConnectError("refused"))
    )
    yield calls


async def _run(
    client: httpx.AsyncClient, thread_id: str, body: dict[str, Any]
) -> tuple[list[str], list[Any]]:
    """One run, as `(event names, parsed payloads)`.

    `tests/test_copilot_threads.read_stream_with_payloads` posts a bare message;
    this one takes the whole body, because half the runs in this module carry a
    `quickAction`.
    """
    events: list[str] = []
    payloads: list[Any] = []
    async with client.stream("POST", f"/copilot/threads/{thread_id}/runs", json=body) as response:
        assert response.status_code == 200, await response.aread()
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                events.append(line.removeprefix("event: "))
            elif line.startswith("data: "):
                payloads.append(json.loads(line.removeprefix("data: ")))
    return events, payloads


def _terminals(events: list[str]) -> list[str]:
    return [event for event in events if event in {"done", "interrupt", "error"}]


async def _open_thread(client: httpx.AsyncClient, claim_id: str) -> str:
    response = await client.post(f"/copilot/claims/{claim_id}/threads")
    assert response.status_code == 201, response.text
    return str(response.json()["threadId"])


async def _accepts_another_message(client: httpx.AsyncClient, thread_id: str) -> bool:
    """Whether the thread takes a new message — AD-6's single-flight, cleared.

    Read as a status code rather than as a state field, because that is what a
    handler experiences: a lock that was not released and an interrupt that was
    left pending both surface as one 409, and neither is distinguishable from
    "the copilot is broken" from inside the panel.
    """
    async with client.stream(
        "POST", f"/copilot/threads/{thread_id}/runs", json={"message": "and again?"}
    ) as response:
        accepted = response.status_code == 200
        await response.aread()
    return accepted


@requires_db
async def test_an_unreachable_model_ends_free_chat_with_one_ai_unavailable_frame(
    seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC 1, end to end: the vendor's refusal becomes a typed, terminal outage.

    Three things at once, and each was a way this could look right and be wrong.
    The run ends with **exactly one** terminal event, so the wrapper's raise did
    not become a second frame. The frame's problem document carries the new
    `code` beside the four members every other failure on this API carries, so
    the SPA needs no second shape. And the transport was tried
    `chat_max_attempts` times and no more, which is the retry bound observed at
    the level a handler's outage actually happens.
    """
    with _unreachable_model(monkeypatch) as attempts:
        async with make_client(seeded_db_url, model=copilot_chat_model(_settings())) as client:
            await login_as(client, *HANDLER)
            thread = await _open_thread(client, a_claim_of(HANDLER))

            events, payloads = await _run(client, thread, {"message": "what happened?"})

            assert _terminals(events) == ["error"]
            problem = payloads[-1]
            assert problem["code"] == "ai_unavailable"
            assert problem["type"] == "/problems/ai-unavailable"
            assert problem["status"] == 503
            assert set(problem) == {"type", "title", "status", "detail", "code"}
            assert len(attempts) == _settings().chat_max_attempts

            # AD-6: after a terminal error the thread is accepting again. The
            # advisory lock released and nothing is pending, so the handler's
            # next question is answered rather than 409-ed.
            assert await _accepts_another_message(client, thread)


@requires_db
async def test_an_unreachable_model_ends_a_narrating_quick_action_the_same_way(
    seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One outage vocabulary for both model callers (AC 1, the QAS half).

    `_narrate` and `create_agent`'s internal call reach the model through the
    same object, which is the whole argument for wrapping the object rather than
    the two call sites. This is that argument as an assertion: a quick action
    that declares `requires_llm: True` reports the identical code and the
    identical `type` free chat does.

    The tools are bound because a narrating node gathers its figures from the
    registry first — the outage happens at the narration, after real service
    reads, which is the shape a handler actually hits.
    """
    with _unreachable_model(monkeypatch):
        async with make_client(seeded_db_url, model=copilot_chat_model(_settings())) as client:
            await login_as(client, *HANDLER)
            thread = await _open_thread(client, a_claim_of(HANDLER))

            events, payloads = await _run(
                client, thread, {"message": "Reserve review", "quickAction": "reserve"}
            )

            assert _terminals(events) == ["error"]
            assert payloads[-1]["code"] == "ai_unavailable"
            assert payloads[-1]["type"] == "/problems/ai-unavailable"
            assert await _accepts_another_message(client, thread)


@requires_db
async def test_the_model_free_quick_action_still_answers_during_an_outage(
    seeded_db_url: str,
) -> None:
    """AC 2, and the assertion that makes `requires_llm` mean something.

    With a model that fails at the *call* rather than at a count, the
    `data_alignment` action executes its tool, streams its note and terminates
    `done`. That is AD-14's promise that deterministic actions keep working —
    and it is the one thing in this story that a handler experiences as the
    console still being useful rather than as a well-typed refusal.

    No monkeypatched transport here on purpose: `_NeverCalledModel` is a
    stronger statement than an unreachable one. An unreachable model proves the
    action survives a refusal; this proves it never asks.
    """
    async with make_client(seeded_db_url, model=_NeverCalledModel()) as client:
        await login_as(client, *HANDLER)
        thread = await _open_thread(client, a_claim_of(HANDLER))

        events, _payloads = await _run(
            client, thread, {"message": "Data alignment note", "quickAction": "data_alignment"}
        )

        assert _terminals(events) == ["done"]
        assert "messages" in events, "the model-free action put nothing on the wire"


@requires_db
async def test_a_length_stopped_completion_is_ai_limit_and_keeps_its_tokens(
    seeded_db_url: str,
) -> None:
    """AC 4's `num_predict` half, which nothing noticed before this story.

    `copilot_max_output_tokens` has been passed to every completion since Story
    6.3 and reaching it terminated `done` — so a four-hundred-word answer cut
    off mid-sentence was reported to the handler as a question that had been
    answered. It is now an `error` whose `code` is `ai_limit`.

    **The tokens stay**, which is the half that decides where the raise goes:
    the decoded text is asserted to have reached the wire *before* the terminal
    frame, because the run is a completed answer that was cut short rather than
    a lost one.
    """
    async with make_client(seeded_db_url, model=_TruncatedChatModel()) as client:
        await login_as(client, *HANDLER)
        thread = await _open_thread(client, a_claim_of(HANDLER))

        events, payloads = await _run(client, thread, {"message": "tell me everything"})

        assert _terminals(events) == ["error"]
        problem = payloads[-1]
        assert problem["code"] == "ai_limit"
        assert problem["type"] == "/problems/copilot-output-truncated"
        assert problem["status"] == 503
        streamed = "".join(
            payload["content"]
            for event, payload in zip(events, payloads, strict=True)
            if event == "messages"
        )
        assert TRUNCATED_TEXT in streamed
        assert await _accepts_another_message(client, thread)


@requires_db
async def test_a_defect_reaches_the_stream_as_copilot_run_failed(
    seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The narrowing, observed where a handler would experience it (6.6 review).

    The unit test above asserts that a `ValueError` from the vendor's own layer
    propagates rather than being translated; this asserts the consequence, which
    is the thing that actually matters: the run ends `copilot_run_failed` — "the
    copilot could not finish answering" — and **not** `ai_unavailable`, so the
    panel does not grey out its composer and tell the handler the local model
    server did not answer about a bug in this build.

    The distinction is the whole story in one assertion. Before Story 6.6 both
    read as `copilot_run_failed`; the first cut of Story 6.6 made both read as
    `ai_unavailable`. Neither is two codes for two facts.
    """
    calls: list[str] = []
    monkeypatch.setattr(
        ChatOllama, "_astream", _failing_transport(calls, ValueError("the body was not JSON"))
    )

    async with make_client(seeded_db_url, model=copilot_chat_model(_settings())) as client:
        await login_as(client, *HANDLER)
        thread = await _open_thread(client, a_claim_of(HANDLER))

        events, payloads = await _run(client, thread, {"message": "what happened?"})

        assert _terminals(events) == ["error"]
        assert payloads[-1]["code"] == "copilot_run_failed"
        assert payloads[-1]["type"] == "/problems/copilot-run-failed"
        assert len(calls) == 1, "a defect was retried as though the model server had not answered"
        assert await _accepts_another_message(client, thread)


@requires_db
async def test_an_outage_after_tokens_have_streamed_does_not_deny_them(
    seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The frame must not contradict the screen (6.6 review, MEDIUM 12).

    A model that drops the connection after two hundred tokens raises
    un-retried, correctly — retrying would decode a second answer on top of a
    half-rendered first one. What was wrong was the sentence: the terminal frame
    said the copilot "could not reply" while a half-answer sat in the transcript
    above it, and a handler reading a paragraph under a notice telling them
    nothing was said learns to distrust the notice.

    So the detail branches on whether anything was answered, and nothing else
    does: the `code`, the `type` and the status are identical either way,
    because the failure is. Asserted on both shapes in one place, because the
    property is the *difference* between them.
    """
    calls: list[str] = []
    monkeypatch.setattr(
        ChatOllama, "_astream", _dropping_transport(calls, httpx.ReadError("dropped"))
    )

    async with make_client(seeded_db_url, model=copilot_chat_model(_settings())) as client:
        await login_as(client, *HANDLER)
        thread = await _open_thread(client, a_claim_of(HANDLER))

        events, payloads = await _run(client, thread, {"message": "what happened?"})

        assert _terminals(events) == ["error"]
        assert "messages" in events, "the partial answer never reached the wire"
        problem = payloads[-1]
        assert problem["code"] == "ai_unavailable"
        assert problem["type"] == "/problems/ai-unavailable"
        assert "part-way through" in problem["detail"]
        assert "could not reply" not in problem["detail"]
        assert len(calls) == 1, "a mid-stream failure was retried; the answer would be duplicated"


@requires_db
async def test_an_intermediate_truncated_turn_does_not_fail_a_completed_answer(
    seeded_db_url: str,
) -> None:
    """`truncated` tracks the **last** completion, not any of them (6.6 review).

    The first cut set one boolean off any `messages` chunk in any namespace and
    never cleared it. In a `create_agent` tool loop that is wrong in the
    direction that shows: an intermediate tool-call turn that hits `num_predict`
    — a plausible thing for a small model deciding on arguments to do — flipped
    the flag, and the run then terminated `error`/`ai_limit` even though the
    final visible answer completed normally. The handler is looking at a
    complete answer under a notice saying it was cut short.

    Two completions here, and the *second* one is the answer: turn one calls a
    tool and stops on `length`, turn two answers and stops on `stop`. The run
    must end `done`.
    """
    script = iter(
        [
            _ScriptedTurn(content="", tool_call=_A_TOOL_CALL, done_reason="length"),
            _ScriptedTurn(content=ANSWERED_TEXT, tool_call=None, done_reason="stop"),
        ]
    )
    async with make_client(
        seeded_db_url,
        model=_ScriptedStreamingChatModel(script=script),
        tools=list(build_tools()),
    ) as client:
        await login_as(client, *HANDLER)
        thread = await _open_thread(client, a_claim_of(HANDLER))

        events, payloads = await _run(client, thread, {"message": "what is this claim?"})

        assert _terminals(events) == ["done"], (
            "an intermediate turn's token ceiling failed a run whose answer completed"
        )
        streamed = "".join(
            payload["content"]
            for event, payload in zip(events, payloads, strict=True)
            if event == "messages"
        )
        assert ANSWERED_TEXT in streamed


@requires_db
async def test_a_wall_clock_overrun_is_one_ai_limit_frame(seeded_db_url: str) -> None:
    """AC 4's other half, and the code that distinguishes it from an outage.

    `copilot_run_timeout_seconds` is a bound *this build* set on its own loop. A
    run that reached it says nothing about whether the model server is up, so it
    is `ai_limit` and not `ai_unavailable` — reporting it as an outage would grey
    out the composer for a model that is answering perfectly well.

    `type` and `status` are unchanged from Story 6.3 (`/problems/copilot-run-
    timeout`, 504) and asserted here as well as in `test_copilot_threads.py`,
    because "the code was added without disturbing the two members the SPA
    already discriminates on" is this story's claim rather than that one's.
    """
    async with (
        a_run_held_mid_flight() as blocking,
        make_client(seeded_db_url, model=blocking, run_timeout_seconds=0.1) as client,
    ):
        await login_as(client, *HANDLER)
        thread = await _open_thread(client, a_claim_of(HANDLER))

        events, payloads = await _run(client, thread, {"message": "what happened?"})

        assert _terminals(events) == ["error"]
        assert payloads[-1]["code"] == "ai_limit"
        assert payloads[-1]["type"] == "/problems/copilot-run-timeout"
        assert payloads[-1]["status"] == 504
        # …and the thread is accepting, which is AC 4's other clause and is
        # asserted the same way for both `ai_limit` shapes: the 409 fires on a
        # held lock *or* on a pending approval, so a 200 here is the proof that
        # neither was left behind. (This second run hits the same short wall
        # clock and fails the same honest way; what is under test is that it was
        # let in at all.)
        assert await _accepts_another_message(client, thread)


@requires_db
async def test_no_approval_is_left_pending_after_a_terminal_error(
    seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stuck-thread failure, asserted at the saver rather than at the panel.

    AC 4 asks that a bounded run leave "no `pending_approval` set", and the
    reason it is worth its own test is that the symptom is invisible from
    outside until the *next* message: a thread the saver believes is
    interrupt-pending 409s for ever, with an approval card that renders nothing
    because no interrupt was ever raised.

    Read off the **transcript route**, which publishes `interruptPending` from
    the same `thread_state` the runs endpoint's 409 consults (Story 6.5's
    review put it there so a remounted panel could re-seed its card). So this
    asserts the condition the refusal is derived from, through the shipped
    surface, rather than reaching into the app object for it.
    """
    with _unreachable_model(monkeypatch):
        async with make_client(seeded_db_url, model=copilot_chat_model(_settings())) as client:
            await login_as(client, *HANDLER)
            thread = await _open_thread(client, a_claim_of(HANDLER))
            await _run(client, thread, {"message": "what happened?"})

            transcript = (await client.get(f"/copilot/threads/{thread}/messages")).json()

            assert transcript["interruptPending"] is False
            assert transcript["pendingApproval"] is None


# --- the availability endpoint -------------------------------------------


@requires_db
async def test_availability_reports_the_outage_as_a_200_and_names_the_llm_actions(
    seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint answers **false**, not an error, and carries both facts.

    A health signal that 503s during an outage has told its caller nothing it
    can render, and the panel would then have to treat "the probe failed" and
    "the model is down" as two states with one meaning.

    **The runtime's real `ModelAvailabilityProbe` is used**, with only the
    socket-touching function beneath it replaced — so the cache the route is
    "backed by" is the shipped one, and the second request asserts it: two
    polls, one upstream probe, which is the property that makes this endpoint
    cheap enough for every open panel to poll.

    The `quickActions` array is asserted against `QUICK_ACTIONS` itself rather
    than against a written-out list of six keys, because a hard-coded expectation
    here would be the client-side mirror of server truth that shipping these
    flags over the wire exists to avoid — one copy of the map is the whole point.
    """
    probes: list[str] = []

    async def _probe(settings: Settings) -> bool:
        probes.append("probe")
        return False

    monkeypatch.setattr(degradation, "probe_model", _probe)

    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)

        body = (await client.get("/copilot/availability")).json()
        again = (await client.get("/copilot/availability")).json()

        assert body["available"] is False
        assert again["available"] is False
        assert len(probes) == 1, "the route probed twice; the cache is not wired to it"
        assert [(entry["key"], entry["requiresLlm"]) for entry in body["quickActions"]] == [
            (flag.key, flag.requires_llm) for flag in quick_action_flags()
        ]
        assert {entry["key"] for entry in body["quickActions"]} == set(QUICK_ACTIONS)


@requires_db
async def test_availability_needs_a_session(seeded_db_url: str) -> None:
    """Authenticated like the rest of the router; `/healthz` is untouched.

    The api container is deliberately healthy without its model, and folding an
    Ollama check into `/healthz` would both invert that and grow the
    unauthenticated public-route set `tests/test_problem_json.py` pins with a
    route that leaks a dependency's state to anonymous callers. This is the
    assertion that the new route did not become that.
    """
    async with make_client(seeded_db_url) as client:
        assert (await client.get("/copilot/availability")).status_code == 401
