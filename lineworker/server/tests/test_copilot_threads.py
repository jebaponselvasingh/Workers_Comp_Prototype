"""Threads: minting, sequence, read-only history, single-flight, ownership (6.3).

The API half of AD-6. Everything here is asserted through the shipped routes
against a real database, because every property under test is a property of the
*composition* — a scoped repository, a server-side minting rule, an advisory
lock and a saver, arranged so that a handler cannot address another handler's
conversation and cannot have two runs on one thread.

**The chat model is scripted and the graph is rebuilt over the real saver.**
`_replace_model` swaps `app.state.copilot.graph` for one compiled from a fake
chat model against the *same* `AsyncPostgresSaver`, so the checkpoints these
tests write are real rows written by the real saver — which is what makes the
persistence assertions in `test_copilot_persistence.py` meaningful — while
nothing waits on a model server.

What is deliberately **not** asserted anywhere in this module: a sentence. The
fake's answers are filler and say so, exactly as `tests/insight_fixture.py`
argues for the insight pipeline (AD-15: structure, never prose).
"""

import asyncio
import contextlib
import dataclasses
import json
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx
import pytest
import sqlalchemy as sa
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from sqlalchemy.exc import IntegrityError

from agents.graph import build_graph
from agents.threads import mint_thread_id
from api import create_app
from config import Settings
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

HANDLER = ("Sarah Williams", "handler")
OTHER_HANDLER = ("Kaya Johnson", "handler")

#: What the scripted model says. Deliberately bland and deliberately constant —
#: no test in this module reads it, and a fixture with interesting text is an
#: invitation for one to start.
REPLY = "Deterministic test answer from a scripted chat model, not from any model server."


class _ScriptedChatModel(GenericFakeChatModel):
    """A chat model that answers `REPLY` for ever, with no I/O.

    Declared as a local class above the tests that need it, the
    `test_ai_insights.py` pattern: a fake that lives beside its assertions
    cannot quietly acquire behaviour for a different test's benefit.

    `GenericFakeChatModel` is LangChain's own, so the object under test is a
    real `BaseChatModel` — the harness binds tools to it, streams from it and
    reconciles its chunks exactly as it would with `ChatOllama`. A hand-rolled
    stub would have tested everything except the part that talks to the
    vendor's abstraction.
    """

    def __init__(self) -> None:
        super().__init__(messages=_forever())


def _forever() -> Iterator[AIMessage]:
    while True:
        yield AIMessage(content=REPLY)


#: Held closed while a test wants a run to be **in flight**.
#:
#: Single-flight cannot be asserted against an instant model: the scripted one
#: answers in microseconds, so the first run finishes and releases its lock
#: before a second request can be made, and the assertion becomes a race that
#: usually fails in the wrong direction (a passing 200 where a 409 was wanted).
#: `_BlockingChatModel` waits on this, so "a run is in flight" is a state the
#: test creates deliberately rather than one it hopes to catch.
#:
#: Set by default, so every other test in this module is unaffected, and
#: **replaced per test** by `a_run_held_mid_flight` — see that helper on why a
#: module-level `asyncio.Event` is a trap rather than a shortcut.
_MODEL_GATE = asyncio.Event()
_MODEL_GATE.set()


@asynccontextmanager
async def a_run_held_mid_flight() -> AsyncIterator[GenericFakeChatModel]:
    """A chat model that will not answer until the gate is opened again.

    **A fresh `asyncio.Event` per test**, and that is a correctness fix rather
    than hygiene. `asyncio.Event` binds itself to the loop that first awaits it
    and raises `RuntimeError` on every other one — so a module-level gate works
    in whichever test happens to run first and fails in all the rest, which
    surfaces as a run that "failed" in eleven milliseconds and a single-flight
    assertion that never had a run in flight to assert against.

    The gate is opened on the way out whatever happened, so a failing assertion
    cannot leave a held run behind for the next test to trip over.
    """
    global _MODEL_GATE
    _MODEL_GATE = asyncio.Event()
    try:
        yield _BlockingChatModel(messages=_forever())
    finally:
        _MODEL_GATE.set()


class _BlockingChatModel(GenericFakeChatModel):
    """`_ScriptedChatModel` that will not answer until `_MODEL_GATE` opens.

    **Both entry points are overridden, and that was learned the hard way.**
    `BaseChatModel` decides per call whether to stream, and `GenericFakeChatModel`
    implements the sync `_stream` — so a fake that blocked only in `_agenerate`
    blocked when the harness invoked it and did *not* when the harness streamed.
    The two paths were taken in different runs of the same test depending on
    which module pytest had imported first, which is the worst kind of flake:
    the assertion that failed was about single-flight, and the actual cause was
    a fixture that only sometimes did what it said.

    So the gate is awaited in `_astream` as well. Whichever path the harness
    picks, "a run is in flight" is a state this test creates rather than one it
    hopes to catch.
    """

    async def _agenerate(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        await _MODEL_GATE.wait()
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=REPLY))])

    async def _astream(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        await _MODEL_GATE.wait()
        yield ChatGenerationChunk(message=AIMessageChunk(content=REPLY))


@pytest.fixture(autouse=True)
def a_conversation_free_claim(seeded_db_url: str) -> None:
    """Empty the conversation store before every test in this module.

    The database fixture is **module**-scoped — it rebuilds the schema once per
    file, not once per test — so without this every assertion about a sequence
    number would depend on how many tests ran before it, and the failure would
    look like a minting bug. That is 6.1's `embedEverything` lesson stated as a
    fixture: a shared prerequisite is requested per test, never inherited from
    execution order.

    Both stores are cleared, and both have to be: `copilot_thread` holds the
    sequences and the checkpoint tables hold the transcripts, and a thread id
    re-minted over a stale checkpoint would resume somebody else's conversation.
    `TRUNCATE … CASCADE` rather than `DELETE`, because it also resets the
    identity sequence, and a test that asserted on a row id would otherwise
    depend on the file's history too.
    """
    engine = sa.create_engine(
        seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with engine.connect() as conn:
            conn.execute(
                sa.text(
                    "TRUNCATE copilot_thread, checkpoints, checkpoint_blobs, "
                    "checkpoint_writes RESTART IDENTITY CASCADE"
                )
            )
    finally:
        engine.dispose()


@asynccontextmanager
async def make_client(
    db_url: str,
    *,
    model: GenericFakeChatModel | None = None,
    run_timeout_seconds: float | None = None,
    tools: Sequence[Any] | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """The shipped app, with the model swapped and — optionally — a shorter wall clock.

    `run_timeout_seconds` is overridden on the frozen `CopilotRuntime` rather
    than in `Settings`, which is the same distinction the runtime's own docstring
    draws: the bounds are values on that object precisely so a route cannot widen
    its own, and a test that wants a different one replaces the object.

    `tools` is `None` — meaning *none at all* — for almost every test here, and
    that is right for them: they assert transport, and a bound registry would
    put a database read behind every scripted answer. Story 6.5's interrupt
    tests are the exception, because a write that pauses has to be a write that
    can then *execute*, and an unbound tool is a `ToolMessage` reading "not a
    valid tool" where the approved row should be.
    """
    app = create_app(Settings(database_url=db_url, env="e2e"))  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        _replace_model(app, model or _ScriptedChatModel(), tools=tools)
        if run_timeout_seconds is not None:
            app.state.copilot = dataclasses.replace(
                app.state.copilot, run_timeout_seconds=run_timeout_seconds
            )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def _replace_model(
    app: object, model: GenericFakeChatModel, *, tools: Sequence[Any] | None = None
) -> None:
    """Recompile the graph over the same saver, with a scripted model.

    **The saver is reused rather than reconstructed**, which is why
    `CopilotRuntime` publishes it: a second `AsyncPostgresSaver` over the same
    tables would work, but it would let a test pass while the *shipped* wiring
    was broken. Everything except the model is the real object graph
    `lifespan` built.
    """
    runtime = app.state.copilot  # type: ignore[attr-defined]
    app.state.copilot = dataclasses.replace(  # type: ignore[attr-defined]
        runtime,
        graph=build_graph(
            model=model,
            checkpointer=runtime.checkpointer,
            max_tool_calls=runtime.max_tool_calls,
            tools=list(tools) if tools is not None else [],
        ),
    )


async def login_as(client: httpx.AsyncClient, name: str, role: str) -> None:
    personas = (await client.get("/personas")).json()["items"]
    match = [p for p in personas if p["name"] == name and p["role"] == role]
    assert len(match) == 1
    assert (await client.post("/auth/login", json={"personaId": match[0]["id"]})).status_code == 200


def a_claim_of(persona: tuple[str, str]) -> str:
    return sorted(seed_fixture.expected_claim_ids(*persona))[0]


def a_claim_outside(persona: tuple[str, str]) -> str:
    visible = seed_fixture.expected_claim_ids(*persona)
    outside = sorted({claim["claim_id"] for claim in seed_fixture.seed()["claims"]} - visible)
    assert outside, f"{persona[0]} sees the whole portfolio; no out-of-scope claim to use"
    return str(outside[0])


async def read_stream(client: httpx.AsyncClient, thread_id: str, message: str) -> list[str]:
    """Run one turn and return its SSE event names, in order.

    Names only. What every assertion in this suite is about is the *sequence* —
    `messages` frames, then exactly one terminal — and a helper that returned
    payloads would invite an assertion about the model's prose (AD-15).
    """
    events: list[str] = []
    async with client.stream(
        "POST", f"/copilot/threads/{thread_id}/runs", json={"message": message}
    ) as response:
        assert response.status_code == 200, await response.aread()
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                events.append(line.removeprefix("event: "))
    return events


# --- minting ------------------------------------------------------------


async def test_a_fresh_claim_has_no_thread_and_the_greeting_is_still_there(
    seeded_db_url: str,
) -> None:
    """The first-conversation state (AC 1): 200, an empty list, `currentThreadId: null`.

    A 404 here would send the panel down its error branch for the normal state
    of every claim on a fresh deployment, which is exactly what NFR-3 is about.
    The greeting is present regardless, because it is a property of the claim
    rather than of a conversation — and it is deterministic service output, not
    model prose (AD-2), which is why it can exist before any thread does.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)

        body = (await client.get(f"/copilot/claims/{claim_id}/threads")).json()

        assert body["items"] == []
        assert body["currentThreadId"] is None
        assert claim_id in body["greeting"]
        assert body["greetingVersion"] >= 1


async def test_the_greeting_is_identical_on_two_reads(seeded_db_url: str) -> None:
    """AD-2/AD-15: the seeded case summary is deterministic, so it is assertable.

    A model-written greeting would make "the panel opened" a claim about prose.
    This is the assertion that keeps it from becoming one.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)

        first = (await client.get(f"/copilot/claims/{claim_id}/threads")).json()["greeting"]
        second = (await client.get(f"/copilot/claims/{claim_id}/threads")).json()["greeting"]

        assert first == second


async def test_the_server_mints_the_key_and_nobody_supplies_one(seeded_db_url: str) -> None:
    """AC 1: `seq 1`, a server-derived id, and no request that could have named it.

    The id is compared against `mint_thread_id` rather than read back, because
    the property under test is that the *rule* produced it — a route that
    accepted a client-supplied id would still return one that looked right.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        me = (await client.get("/me")).json()

        created = await client.post(f"/copilot/claims/{claim_id}/threads")

        assert created.status_code == 201
        body = created.json()
        assert body["conversationSeq"] == 1
        assert body["isCurrent"] is True
        assert body["threadId"] == mint_thread_id(claim_id, me["id"], 1)


async def test_new_conversation_increments_the_sequence(seeded_db_url: str) -> None:
    """AC 4's first half: `seq + 1`, and it becomes the current one."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)

        first = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()
        second = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()
        listed = (await client.get(f"/copilot/claims/{claim_id}/threads")).json()

        assert (first["conversationSeq"], second["conversationSeq"]) == (1, 2)
        assert listed["currentThreadId"] == second["threadId"]
        assert [item["isCurrent"] for item in listed["items"]] == [False, True]


async def test_two_claims_have_independent_sequences(seeded_db_url: str) -> None:
    """The key is `(claim, user, seq)`, so a second claim starts at 1 again.

    Worth asserting because the obvious wrong implementation — a per-user
    counter — passes every other test in this module.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claims = sorted(seed_fixture.expected_claim_ids(*HANDLER))[:2]

        first = (await client.post(f"/copilot/claims/{claims[0]}/threads")).json()
        second = (await client.post(f"/copilot/claims/{claims[1]}/threads")).json()

        assert first["conversationSeq"] == second["conversationSeq"] == 1
        assert first["threadId"] != second["threadId"]


async def test_a_claim_outside_the_book_is_the_case_files_404(seeded_db_url: str) -> None:
    """AD-7, in the case file's exact wording — see `_claim_not_found`."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        outside = a_claim_outside(HANDLER)

        listed = await client.get(f"/copilot/claims/{outside}/threads")
        minted = await client.post(f"/copilot/claims/{outside}/threads")

        for response in (listed, minted):
            assert response.status_code == 404
            assert response.headers["content-type"] == "application/problem+json"
            assert response.json()["type"] == "/problems/claim-not-found"


# --- ownership ----------------------------------------------------------


async def test_a_foreign_thread_is_the_same_404_as_an_unknown_one(seeded_db_url: str) -> None:
    """AC 5: byte-identical documents, and never a 403.

    A caller who could tell "exists but is not yours" from "does not exist"
    could enumerate other handlers' conversations by guessing ids, and the ids
    are guessable by design (they are readable, and readability is a deliberate
    operational property). So the refusal is where the guarantee lives.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *OTHER_HANDLER)
        theirs = (await client.post(f"/copilot/claims/{a_claim_of(OTHER_HANDLER)}/threads")).json()[
            "threadId"
        ]

        await login_as(client, *HANDLER)
        foreign = await client.get(f"/copilot/threads/{theirs}/messages")
        unknown = await client.get(
            f"/copilot/threads/{mint_thread_id(a_claim_of(HANDLER), 999, 1)}/messages"
        )

        assert foreign.status_code == unknown.status_code == 404
        assert foreign.json() == unknown.json()
        assert foreign.json()["type"] == "/problems/thread-not-found"


async def test_a_foreign_thread_cannot_be_run(seeded_db_url: str) -> None:
    """The same refusal on the write path — a 404 rather than a 409 or a 403."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *OTHER_HANDLER)
        theirs = (await client.post(f"/copilot/claims/{a_claim_of(OTHER_HANDLER)}/threads")).json()[
            "threadId"
        ]

        await login_as(client, *HANDLER)
        refused = await client.post(
            f"/copilot/threads/{theirs}/runs", json={"message": "whose claim is this?"}
        )

        assert refused.status_code == 404
        assert refused.json()["type"] == "/problems/thread-not-found"


# --- the run ------------------------------------------------------------


async def test_a_free_text_run_streams_messages_then_exactly_one_done(
    seeded_db_url: str,
) -> None:
    """AC 2, and the invariant most likely to regress.

    Two assertions and the second is the one that matters: at least one
    `messages` frame arrived, and the terminal event set has exactly one member
    and it is at the end. A run that emitted `done` twice, or `done` followed by
    `error`, fails here.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        events = await read_stream(client, thread, "what is the status of this claim?")

        assert "messages" in events
        terminals = [event for event in events if event in {"done", "interrupt", "error"}]
        assert terminals == ["done"]
        assert events[-1] == "done"


async def test_the_turn_comes_back_from_the_transcript(seeded_db_url: str) -> None:
    """The checkpoint round trip within one process — persistence's first half.

    `test_copilot_persistence.py` does the harder version (a new app instance).
    This one fails earlier and more usefully if the saver is not attached at
    all.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]
        await read_stream(client, thread, "summarise this claim")

        body = (await client.get(f"/copilot/threads/{thread}/messages")).json()

        assert [message["role"] for message in body["messages"]] == ["user", "assistant"]
        assert body["messages"][0]["content"] == "summarise this claim"
        assert body["isCurrent"] is True


async def test_a_superseded_thread_is_read_only_and_still_readable(seeded_db_url: str) -> None:
    """AC 4's second half: 409 on the prior thread, and its transcript survives.

    Freezing a thread freezes **posting**, not reading — which is the whole
    point of keeping history at all, and is why the transcript route has no
    `isCurrent` gate on it.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        first = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]
        await read_stream(client, first, "the first conversation")
        await client.post(f"/copilot/claims/{claim_id}/threads")

        refused = await client.post(
            f"/copilot/threads/{first}/runs", json={"message": "still talking?"}
        )
        history = await client.get(f"/copilot/threads/{first}/messages")

        assert refused.status_code == 409
        assert refused.headers["content-type"] == "application/problem+json"
        assert refused.json()["type"] == "/problems/thread-read-only"
        # No extension member — there is no fresher entity to hand back. See
        # the router's docstring on why this 409 is unlike a version conflict.
        assert set(refused.json()) == {"type", "title", "status", "detail"}
        assert history.status_code == 200
        assert history.json()["isCurrent"] is False
        assert [m["content"] for m in history.json()["messages"]][0] == "the first conversation"


async def test_a_second_message_while_a_run_holds_the_lock_is_409(seeded_db_url: str) -> None:
    """Single-flight, cause one: the advisory lock is held (AC 3).

    The first run is held mid-flight by `_MODEL_GATE` — see that constant on
    why an instant model makes this assertion a race — and the second request
    must be refused **without ever taking the lock itself**, which the third
    part of the test proves by letting the first finish and then succeeding.
    """
    async with (
        a_run_held_mid_flight() as blocking,
        make_client(seeded_db_url, model=blocking) as client,
    ):
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        first = asyncio.create_task(
            client.post(f"/copilot/threads/{thread}/runs", json={"message": "first"})
        )
        # Yield until the run has certainly reached the model and is
        # therefore certainly holding the lock.
        while not first.done() and not _run_started(thread):
            await asyncio.sleep(0.02)

        second = await client.post(f"/copilot/threads/{thread}/runs", json={"message": "second"})

        assert second.status_code == 409
        assert second.headers["content-type"] == "application/problem+json"
        assert second.json()["type"] == "/problems/thread-busy"
        # No extension member — there is no fresher entity to hand back.
        assert set(second.json()) == {"type", "title", "status", "detail"}

        _MODEL_GATE.set()
        assert (await first).status_code == 200


def _run_started(thread_id: str) -> bool:
    """Whether a run currently holds `thread_id`'s advisory lock.

    Asked of PostgreSQL rather than of the application, because the property
    under test is that the lock is *in the database* — a guard that trusted an
    in-memory flag would pass a test that trusted the same flag, and the whole
    reason the lock is an advisory lock is that it is crash-safe across
    processes.
    """
    import sqlalchemy as sa

    from agents.threads import RUN_LOCK_NAMESPACE, _lock_key
    from tests.conftest import MIGRATION_DB_URL

    engine = sa.create_engine(
        str(MIGRATION_DB_URL).replace("postgresql://", "postgresql+psycopg://", 1),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with engine.connect() as conn:
            held = conn.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                    "AND classid = :namespace AND objid = :key"
                ),
                {"namespace": RUN_LOCK_NAMESPACE, "key": _lock_key(thread_id) & 0xFFFFFFFF},
            )
        return bool(held)
    finally:
        engine.dispose()


async def test_the_lock_is_released_when_the_run_finishes(seeded_db_url: str) -> None:
    """…and the thread is usable again, which is the other half of the guard.

    A single-flight guard that never released would be indistinguishable from a
    correct one in the test above, and would make every second message on every
    thread a 409 for ever.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        await read_stream(client, thread, "first")
        events = await read_stream(client, thread, "second")

        assert events[-1] == "done"
        body = (await client.get(f"/copilot/threads/{thread}/messages")).json()
        assert [m["role"] for m in body["messages"]] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]


async def test_a_run_needs_a_message_or_a_command(seeded_db_url: str) -> None:
    """An empty body is a 422 rather than a run that streams nothing."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        refused = await client.post(f"/copilot/threads/{thread}/runs", json={})

        assert refused.status_code == 422


async def test_a_body_cannot_name_a_thread_a_user_or_a_scope(seeded_db_url: str) -> None:
    """`extra="forbid"`, and the keys that must never be accepted.

    The point of this test is the *absence* of fields rather than the presence
    of a validator: AD-6 says clients never supply a thread id and AD-7 says
    nothing on a request may name a scope, and both are true here because the
    request model has nowhere to put one.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        for smuggled in ("threadId", "userId", "employerId", "scope", "systemPrompt", "model"):
            refused = await client.post(
                f"/copilot/threads/{thread}/runs",
                json={"message": "hello", smuggled: "anything"},
            )
            assert refused.status_code == 422, smuggled


async def test_two_new_conversation_clicks_produce_two_sequences(seeded_db_url: str) -> None:
    """The minting race, arbitrated by the unique constraint rather than a lock.

    Both requests are issued concurrently against one claim. Whatever order the
    database resolves them in, the outcome must be `{1, 2}` — never `{1, 1}`,
    which is what an unguarded `max(seq) + 1` produces, and never one failure.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)

        both = await asyncio.gather(
            client.post(f"/copilot/claims/{claim_id}/threads"),
            client.post(f"/copilot/claims/{claim_id}/threads"),
        )

        assert [response.status_code for response in both] == [201, 201]
        assert {response.json()["conversationSeq"] for response in both} == {1, 2}


@pytest.mark.parametrize(
    "route",
    ["/copilot/claims/{claim}/threads", "/copilot/threads/{thread}/messages"],
)
async def test_every_read_is_no_store(seeded_db_url: str, route: str) -> None:
    """Per-caller payloads must never be cached by an intermediary and replayed.

    It matters more here than on most routes: these bodies name a claim, an
    injured worker and a conversation, and the 404 they can answer with depends
    on who is asking.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        response = await client.get(route.format(claim=claim_id, thread=thread))

        assert response.headers["cache-control"] == "no-store"


# --- the run's refusals and its terminal frames --------------------------
#
# Everything below is a path the shipped suite never took (review of Story
# 6.3): the `error` terminal, the interrupt-pending cause of the 409, the run
# body a client can malform, and the two ways a lock could outlive the run that
# took it. The spec's task list names the first two as required coverage and
# "exactly one terminal event on every path including exceptions" was asserted
# only for the happy one.


class _FailingChatModel(GenericFakeChatModel):
    """A model that refuses, the way an unreachable Ollama does.

    `RuntimeError` rather than a transport exception, deliberately: the terminal
    decision is "everything that is not a timeout and not an interrupt", and
    binding the test to one vendor's exception class would assert the
    classification of that class rather than the shape of the rule.
    """

    async def _agenerate(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise RuntimeError("the model server refused")

    async def _astream(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        raise RuntimeError("the model server refused")
        yield  # pragma: no cover - unreachable; makes this an async generator


class _SilentChatModel(GenericFakeChatModel):
    """A model that completes without saying anything at all."""

    async def _agenerate(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=""))])

    async def _astream(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        yield ChatGenerationChunk(message=AIMessageChunk(content=""))


async def test_a_model_that_refuses_ends_the_run_with_exactly_one_error(
    seeded_db_url: str,
) -> None:
    """The `error` terminal, which nothing exercised (AC 2, the failure half).

    "Exactly one terminal event on every path **including exceptions**" was
    asserted only for the path with no exception on it. This is the other one:
    the model raises mid-run, the status is already 200, and the failure can
    only be an `error` frame carrying a problem document inline.
    """
    async with make_client(seeded_db_url, model=_FailingChatModel(messages=_forever())) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        events, payloads = await read_stream_with_payloads(client, thread, "what happened?")

        terminals = [event for event in events if event in {"done", "interrupt", "error"}]
        assert terminals == ["error"]
        assert events[-1] == "error"
        # The frame's payload **is** an RFC 9457 document — there is nowhere
        # else to put one once the status is 200.
        problem = payloads[-1]
        assert set(problem) == {"type", "title", "status", "detail"}
        assert problem["type"] == "/problems/copilot-run-failed"


async def test_a_run_that_answers_nothing_is_an_error_rather_than_a_done(
    seeded_db_url: str,
) -> None:
    """A graph that finished and said nothing is not a question answered.

    `done` is the frame the panel reads as "your question was answered", so a
    completed run with no assistant text put the handler's question on screen
    under a blank space with no notice that anything had gone wrong. The
    alternatives are worse: `done` is a lie, and writing a placeholder sentence
    would be this module composing the model's answer (AD-14).
    """
    async with make_client(seeded_db_url, model=_SilentChatModel(messages=_forever())) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        events, payloads = await read_stream_with_payloads(client, thread, "what happened?")

        assert [event for event in events if event in {"done", "interrupt", "error"}] == ["error"]
        assert payloads[-1]["type"] == "/problems/copilot-empty-answer"


async def read_save_stream(
    client: httpx.AsyncClient, thread_id: str, claim_id: str
) -> tuple[list[str], list[Any]]:
    """Post the RTW letter's save and read the stream it pauses on.

    The one helper the interrupt tests below share. It reads the claim's real
    `version` first, because the save compare-and-swaps on it — see
    `_claim_version` — and returns the same `(events, payloads)` pair
    `read_stream_with_payloads` does, since the interrupt frame's payload is
    structure this build owns rather than anybody's prose (AD-15).
    """
    events: list[str] = []
    payloads: list[Any] = []
    version = await _claim_version(client, claim_id)
    async with client.stream(
        "POST", f"/copilot/threads/{thread_id}/runs", json=_save_letter(version)
    ) as response:
        assert response.status_code == 200, await response.aread()
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                events.append(line.removeprefix("event: "))
            elif line.startswith("data: "):
                payloads.append(json.loads(line.removeprefix("data: ")))
    return events, payloads


async def read_stream_with_payloads(
    client: httpx.AsyncClient, thread_id: str, message: str
) -> tuple[list[str], list[Any]]:
    """`read_stream`, plus the parsed `data:` payloads.

    A second helper rather than a wider first one, because `read_stream`'s
    narrowness is deliberate: returning payloads invites an assertion about the
    model's prose (AD-15). Everything read here is a problem document, which is
    this build's own structure and not anybody's writing.
    """
    events: list[str] = []
    payloads: list[Any] = []
    async with client.stream(
        "POST", f"/copilot/threads/{thread_id}/runs", json={"message": message}
    ) as response:
        assert response.status_code == 200, await response.aread()
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                events.append(line.removeprefix("event: "))
            elif line.startswith("data: "):
                payloads.append(json.loads(line.removeprefix("data: ")))
    return events, payloads


# --- the interrupt, and the 409 it causes --------------------------------


#: A letter body a handler could plausibly have typed into the RTW modal.
#:
#: Short, and deliberately not prose about anybody: everything on this path is
#: real — a real `document` row lands on a seeded claim — so the body is the
#: shortest thing that satisfies the command's own validation.
LETTER_BODY = "We are pleased to offer modified duty from a date to be confirmed."


def _save_letter(version: int) -> dict[str, Any]:
    """The body the RTW modal's Save button posts — the run that pauses.

    **The real producer, and it needs no model.** Story 6.5 replaced this
    module's hand-written `_interrupting_graph` with this: a body carrying
    `rtwLetter` makes the shipped graph synthesise a write tool call and pause
    on it, through `HumanInTheLoopMiddleware`, with no completion requested from
    anything. So the transport tests below now exercise the interrupt this build
    actually raises rather than one written to stand in for it — which is the
    whole point of the replacement, since a scaffold's interrupt cannot tell you
    that the shipped one reaches the wire.

    The scaffold's docstring used to say "Story 6.5 owns the first shipped
    `interrupt()` producer, and this is not it". It is now.
    """
    return {
        "message": "Save the return-to-work letter to this claim.",
        "rtwLetter": {"bodyText": LETTER_BODY, "expectedVersion": version},
    }


async def _claim_version(client: httpx.AsyncClient, claim_id: str) -> int:
    """The claim's `version`, read the way the modal's own draft reads it.

    A real number rather than a literal, because the save compare-and-swaps on
    it: a hard-coded `1` would pass today and would fail silently the day the
    seed writes a claim twice.
    """
    detail = await client.get(f"/claims/{claim_id}")
    assert detail.status_code == 200, detail.text
    version: int = detail.json()["version"]
    return version


@asynccontextmanager
async def interrupting_client(db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """`make_client` — **unchanged, because the shipped graph interrupts now**.

    This used to swap the compiled graph for a scaffold whose node called
    `interrupt()` directly, because nothing in `agents/` could. Story 6.5
    registered two write tools and installed the gate, so the alias survives as
    a name the tests below read well under and delegates to the real thing.
    """
    # **With the real registry bound**, unlike every other client here: the
    # write this pauses on has to be able to execute on approval, and a tool
    # that is not bound answers "not a valid tool" instead of filing a letter.
    from agents.registry import build_tools

    async with make_client(db_url, tools=list(build_tools())) as client:
        yield client


async def test_an_interrupt_pending_thread_refuses_a_second_message(seeded_db_url: str) -> None:
    """Single-flight, cause two: the saver reports a pending interrupt (AC 3).

    The advisory-lock cause had a test; this one did not, because nothing in the
    build can raise an interrupt yet — so the condition the spec's I/O matrix
    calls "second message while interrupt pending → 409" was unreachable from
    the suite and the code path had never run.

    Three things at once, and they only exist together: the run ends in the
    `interrupt` terminal frame, a second *message* is refused 409 with the
    single-flight `type`, and the refusal is re-derived from the saver rather
    than from a flag — which is why it survives the first run having released
    its lock.
    """
    async with interrupting_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        events, payloads = await read_save_stream(client, thread, claim_id)
        assert [event for event in events if event in {"done", "interrupt", "error"}] == [
            "interrupt"
        ]
        # **The frame carries the pending call** (Story 6.5, AC 5), which is
        # what makes it renderable — it carried a `threadId` and nothing else
        # until this story. The tool name and its typed arguments are the
        # middleware's own, so the handler approves the payload that will
        # execute rather than a paraphrase of it (AD-16).
        request = payloads[-1]["value"]
        assert [action["name"] for action in request["action_requests"]] == ["save_rtw_letter"]
        assert request["action_requests"][0]["args"]["body_text"] == LETTER_BODY
        assert request["review_configs"][0]["allowed_decisions"] == ["approve", "edit", "reject"]

        # The lock is long gone — the run ended — so this 409 can only come from
        # the saver's own state.
        assert not _run_started(thread)
        refused = await client.post(
            f"/copilot/threads/{thread}/runs", json={"message": "a different question"}
        )

        assert refused.status_code == 409
        assert refused.json()["type"] == "/problems/thread-busy"
        assert set(refused.json()) == {"type", "title", "status", "detail"}


async def test_a_resume_is_accepted_while_a_message_is_refused(seeded_db_url: str) -> None:
    """…and the resume the 409 is holding the thread for goes through.

    The other half of the row above: a *command* is exactly what an
    interrupt-pending thread is waiting for, so it is not refused. Story 6.5
    raises the first interrupt in anger; what this story owes it is that the
    round trip already works.
    """
    async with interrupting_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]
        before = len((await client.get(f"/claims/{claim_id}")).json()["documents"]["documents"])
        await read_save_stream(client, thread, claim_id)

        async with client.stream(
            "POST",
            f"/copilot/threads/{thread}/runs",
            # **The real decision shape** (Story 6.5). It was `"approved"`, a
            # bare string, because nothing consumed it; the resume body now
            # carries `HITLResponse` — `{decisions: [{type: …}]}` — and the
            # round trip this test exists for is the one the gate actually
            # speaks.
            json={"command": {"resume": {"decisions": [{"type": "approve"}]}}},
        ) as response:
            assert response.status_code == 200, await response.aread()
            events = [
                line.removeprefix("event: ")
                async for line in response.aiter_lines()
                if line.startswith("event: ")
            ]

        assert [event for event in events if event in {"done", "interrupt", "error"}] == ["done"]
        body = (await client.get(f"/copilot/threads/{thread}/messages")).json()
        assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
        # …and the approved write actually happened, against a real database
        # through the real AD-4 command. A round trip that resumed cleanly and
        # wrote nothing would satisfy every assertion above it.
        after = (await client.get(f"/claims/{claim_id}")).json()["documents"]["documents"]
        assert len(after) == before + 1
        assert after[-1]["docType"] == "rtw"


async def test_a_foreign_handler_resuming_gets_403_and_a_stranger_still_gets_404(
    seeded_db_url: str,
) -> None:
    """AD-6 and AC 6: **the one place a thread is not a 404** (Story 6.5).

    A paused conversation resumes only on a decision from the thread's own user.
    Every other refusal on this router is the byte-identical 404 that keeps
    thread ids from being an enumeration oracle, and that rule is not weakened
    here — it is *bounded*: the resume-only lookup keeps `employer_scope` and
    drops only the owner predicate, so a 403 means "inside your own book of
    business, another handler's conversation" and reveals nothing across
    employers.

    Both halves are asserted in one test because the property is the pair. A 403
    on a colleague's thread is only safe while an out-of-scope one is still
    indistinguishable from an id nobody minted — so the second and third
    requests are exactly those two, and their documents are compared byte for
    byte.

    The colleague's thread is genuinely interrupt-pending, so the 403 is decided
    before the single-flight check rather than instead of it.
    """
    # **Liam and Kaya both cover John Deere**, which is what makes this test
    # possible at all: the 403 is bounded by employer scope, so it needs two
    # handlers whose books of business overlap. `HANDLER` is Sarah Williams,
    # whose single employer nobody else covers — a colleague of hers would get
    # the ordinary 404 and the assertion would be vacuous.
    owner = ("Liam O'Sullivan", "handler")
    colleague = OTHER_HANDLER

    async with interrupting_client(seeded_db_url) as client:
        await login_as(client, *owner)
        claim_id = a_claim_of(owner)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]
        await read_save_stream(client, thread, claim_id)

        await login_as(client, *colleague)
        foreign = await client.post(
            f"/copilot/threads/{thread}/runs",
            json={"command": {"resume": {"decisions": [{"type": "approve"}]}}},
        )
        assert foreign.status_code == 403, foreign.text
        assert foreign.json()["type"] == "/problems/thread-not-owned"

        # …and the two refusals that must stay identical: a well-formed id on a
        # claim outside this caller's book, and a well-formed id for a user who
        # does not exist. The second is the 6-3 e2e spec's forged-id shape.
        outside = mint_thread_id(a_claim_outside(colleague), 1, 1)
        unknown = mint_thread_id(a_claim_of(colleague), 99999, 1)
        refusals = [
            await client.post(
                f"/copilot/threads/{candidate}/runs",
                json={"command": {"resume": {"decisions": [{"type": "approve"}]}}},
            )
            for candidate in (outside, unknown)
        ]
        assert [response.status_code for response in refusals] == [404, 404]
        assert refusals[0].json() == refusals[1].json()
        assert refusals[0].json()["type"] == "/problems/thread-not-found"


async def test_a_body_carrying_both_a_message_and_a_command_is_refused(
    seeded_db_url: str,
) -> None:
    """The validation bypass that defeated single-flight, closed.

    `{"message": "…", "command": {}}` used to be a 200 that resumed nothing and
    dropped the question: the interrupt-pending check was skipped because a
    `command` was present, and the `Command` was preferred because it was
    non-`None`. So the spec's "second message while interrupt pending → 409" row
    was defeated by adding an empty object to the body, and the handler's
    question vanished with a success status on it.

    Both halves are asserted: the malformed body is refused, and the thread it
    was aimed at still refuses an ordinary message.
    """
    async with interrupting_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]
        await read_save_stream(client, thread, claim_id)

        smuggled = await client.post(
            f"/copilot/threads/{thread}/runs",
            json={"message": "a different question", "command": {}},
        )

        assert smuggled.status_code == 422, smuggled.text
        assert smuggled.json()["type"] == "/problems/validation-error"
        still_busy = await client.post(
            f"/copilot/threads/{thread}/runs", json={"message": "a different question"}
        )
        assert still_busy.status_code == 409


async def test_a_command_without_a_resume_value_is_refused(seeded_db_url: str) -> None:
    """`{"command": {}}` is a 422, never a resume with `None`.

    `Command(resume=None)` is a real instruction — it resumes an interrupt with
    the value `None` — so `resume.get("resume")` invented a decision the caller
    never made. Harmless while nothing interrupts; a wrong answer about a claim
    the moment 6.5's approve/edit/reject round trip lands.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        refused = await client.post(f"/copilot/threads/{thread}/runs", json={"command": {}})

        assert refused.status_code == 422
        assert refused.json()["type"] == "/problems/validation-error"


@pytest.mark.parametrize(
    "resume",
    [
        pytest.param("approve", id="a-bare-string"),
        pytest.param({}, id="no-decisions-key"),
        pytest.param({"decisions": []}, id="no-decisions"),
        pytest.param({"decisions": [{"type": "approved"}]}, id="a-type-nobody-defines"),
        pytest.param({"decisions": [{"type": "respond", "message": "x"}]}, id="an-unused-type"),
        pytest.param({"decisions": [{"type": "edit"}]}, id="an-edit-with-no-action"),
        pytest.param(
            {"decisions": [{"type": "edit", "edited_action": {"name": "save_rtw_letter"}}]},
            id="an-edit-with-no-args",
        ),
        pytest.param({"decisions": [{"type": "approve"}, {"type": "approve"}]}, id="two-decisions"),
    ],
)
async def test_a_malformed_resume_is_422_and_leaves_the_pause_intact(
    seeded_db_url: str, resume: Any
) -> None:
    """A decision this build cannot read is a refusal, not a 503 (Story 6.5 review).

    The resume value used to be handed to the graph untouched, so a malformed
    one raised *inside* `HumanInTheLoopMiddleware` — a `KeyError` for a missing
    `decisions`, a `ValueError` for a type it does not allow or a count that
    does not match. By then the run had started, so the status was already 200
    and the only place the failure could go was a generic `error` frame reading
    "The copilot could not finish answering". A caller cannot act on that: the
    thread is still paused, the sentence blames the copilot, and the fault is in
    the body they sent.

    So each of these is refused before a token is streamed. **And the pause has
    to survive it**, which is the half a status-code assertion would miss: a
    refusal that consumed the interrupt would leave the handler with no card and
    a claim that was never written to.
    """
    async with interrupting_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]
        await read_save_stream(client, thread, claim_id)

        refused = await client.post(
            f"/copilot/threads/{thread}/runs", json={"command": {"resume": resume}}
        )

        assert refused.status_code == 422, refused.text
        assert refused.json()["type"] == "/problems/validation-error"
        # The thread is still waiting for a decision it can read — which the
        # 409 on a message is the observable proof of.
        still_pending = await client.post(
            f"/copilot/threads/{thread}/runs", json={"message": "anything"}
        )
        assert still_pending.status_code == 409
        assert still_pending.json()["type"] == "/problems/thread-busy"


async def test_the_transcript_publishes_the_pause_a_thread_is_holding(
    seeded_db_url: str,
) -> None:
    """FR-CP-2 applied to the approval, not just to the messages (6.5 review).

    The interrupt payload reached the client on the run's terminal frame and
    nowhere else, so the approval card lived in the browser's runtime state and
    nowhere else: switching to 📓 Diary, reloading, or coming back tomorrow
    discarded it while the thread stayed interrupt-pending — a conversation that
    409s every message with no reachable way to answer it. The pause was always
    durable in the checkpoints; only the description of it was not.

    Asserted against the run's own frame rather than against a shape spelled
    twice: what the transcript publishes has to be what the handler would have
    approved, byte for byte, or the card re-seeded from it is a different card
    (AD-16).
    """
    async with interrupting_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        _events, payloads = await read_save_stream(client, thread, claim_id)
        streamed = payloads[-1]["value"]

        body = (await client.get(f"/copilot/threads/{thread}/messages")).json()
        assert body["interruptPending"] is True
        assert body["pendingApproval"] == streamed

        # …and it clears when the decision is taken, so a resolved thread does
        # not re-seed a card for a write that has already happened.
        async with client.stream(
            "POST",
            f"/copilot/threads/{thread}/runs",
            json={"command": {"resume": {"decisions": [{"type": "approve"}]}}},
        ) as response:
            assert response.status_code == 200, await response.aread()
            async for _line in response.aiter_lines():
                pass

        settled = (await client.get(f"/copilot/threads/{thread}/messages")).json()
        assert settled["interruptPending"] is False
        assert settled["pendingApproval"] is None


async def test_a_thread_nobody_has_messaged_reports_no_pending_approval(
    seeded_db_url: str,
) -> None:
    """The default, stated: a fresh conversation is not waiting for anything.

    The positive control's negative half. `interruptPending` defaults to `False`
    and `pendingApproval` to `null`, so a client that re-seeds its approval state
    from every transcript read gets "nothing pends" rather than `undefined` — and
    a thread with no checkpoint at all answers it without erroring, which is the
    first-class state `thread_state` documents.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        body = (await client.get(f"/copilot/threads/{thread}/messages")).json()

        assert body["messages"] == []
        assert body["interruptPending"] is False
        assert body["pendingApproval"] is None


async def test_a_pause_with_no_readable_payload_ends_the_run_as_an_error() -> None:
    """An `interrupt` frame carrying `null` is a card with nothing on it.

    The approval UI renders from the payload, so a frame with no `value`
    renders nothing — while the thread stays interrupt-pending and 409s every
    later message. That is a conversation with no reachable way forward and no
    notice that anything is wrong, produced by a defect the handler cannot see:
    an `Interrupt` object whose `.value` this build cannot read after a vendor
    bump, or a future producer pausing on something other than a `HITLRequest`.

    So the run ends `error` instead. The pause is still real and still
    checkpointed — nothing here resolves it — but the handler is told the
    copilot could not finish and that nothing was written, which is a sentence
    they can act on.

    Driven at `_stream` against a graph that reports a valueless `__interrupt__`,
    because there is no way to produce one through the shipped gate — which is
    the point: this is the branch for the day something changes underneath it.
    """
    from api.routers import copilot as router_module

    class _ValuelessInterruptGraph:
        async def astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            # `(namespace, mode, chunk)` — the shape `subgraphs=True` yields.
            yield ((), "updates", {"__interrupt__": ()})

    class _Lock:
        async def __aenter__(self) -> bool:  # pragma: no cover - not used here
            return True

        async def __aexit__(self, *exc: Any) -> bool:
            return False

    runtime = dataclasses.replace(_runtime_stub(), graph=_ValuelessInterruptGraph())
    frames = [
        frame
        async for frame in router_module._stream(
            lock=_Lock(),
            copilot=runtime,
            ctx=_a_context(),
            thread_id="claim.WC-20017.u7.s1",
            claim_business_id="WC-20017",
            inputs={},
            resume=None,
        )
    ]

    text = b"".join(frames).decode()
    assert "event: interrupt" not in text
    assert "event: error" in text
    assert "/problems/copilot-approval-unreadable" in text


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
async def test_a_blank_message_is_refused_rather_than_checkpointed(
    seeded_db_url: str, blank: str
) -> None:
    """`max_length` without `min_length` accepted `""` and whitespace.

    An empty `HumanMessage` was checkpointed into the conversation for ever and
    a whole model run was spent answering nothing. The composer already refuses
    an empty submit, so the only callers this can have are a mistake and a
    script.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        refused = await client.post(f"/copilot/threads/{thread}/runs", json={"message": blank})

        assert refused.status_code == 422, blank
        body = (await client.get(f"/copilot/threads/{thread}/messages")).json()
        assert body["messages"] == []


# --- the lock, and the ways it used to outlive its run --------------------


async def test_a_cancelled_stream_still_releases_its_lock() -> None:
    """The permanent-409 leak, reproduced at the mechanism (review of Story 6.3).

    Starlette streams a response inside an `anyio` task group and cancels that
    group's scope when the client goes away. Inside a cancelled anyio scope
    every unshielded `await` is cancelled again as soon as it starts — so
    `await lock.__aexit__(...)` in a plain `finally` never reached
    `pg_advisory_unlock`, and a **session-scoped** advisory lock survives the
    `ROLLBACK` that hands its connection back to the pool. The thread answered
    409 for the life of the process, and the event that caused it was a handler
    closing a tab.

    Written against the real `_stream` with a fake lock whose release *awaits* —
    which is the whole of the bug, since a synchronous release would never have
    been cancelled — and a graph that never produces, so the generator is
    suspended inside `__anext__` when the scope is cancelled and the
    `CancelledError` is delivered into it.
    """
    from api.routers import copilot as router_module

    released: list[bool] = []

    class _Lock:
        async def __aenter__(self) -> bool:  # pragma: no cover - not used here
            return True

        async def __aexit__(self, *exc: Any) -> bool:
            # A real release is a round trip to PostgreSQL.
            await asyncio.sleep(0)
            released.append(True)
            return False

    class _NeverAnswersGraph:
        async def astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            await asyncio.Event().wait()
            yield  # pragma: no cover - unreachable

    runtime = dataclasses.replace(_runtime_stub(), graph=_NeverAnswersGraph())

    stream = router_module._stream(
        lock=_Lock(),
        copilot=runtime,
        ctx=_a_context(),
        thread_id="claim.WC-20017.u7.s1",
        claim_business_id="WC-20017",
        inputs={},
        resume=None,
    )

    async def consume() -> None:
        async for _frame in stream:  # pragma: no cover - nothing is ever yielded
            pass

    with anyio.CancelScope() as scope:
        async with anyio.create_task_group() as group:
            group.start_soon(consume)
            await asyncio.sleep(0.05)
            scope.cancel()

    assert released == [True], (
        "the run's advisory lock was not released when the client disconnected — "
        "the thread would answer 409 for the life of the process"
    )


def _runtime_stub() -> Any:
    """A `CopilotRuntime` with the bounds a stream reads and nothing else usable."""
    from api.app import CopilotRuntime

    return CopilotRuntime(
        graph=None,  # type: ignore[arg-type]
        checkpointer=None,  # type: ignore[arg-type]
        sessionmaker=None,  # type: ignore[arg-type]
        max_tool_calls=3,
        run_timeout_seconds=30.0,
        sse_keepalive_seconds=30.0,
        # Story 6.4's retrieval dependencies. Present because the dataclass
        # requires them and unusable because nothing on this stream path may
        # embed anything — the same choice `graph=None` makes one field up.
        embedding_client=None,  # type: ignore[arg-type]
        embedding_staleness_days=7,
    )


def _a_context() -> Any:
    from data.context import CallerContext
    from data.models.enums import UserRole

    return CallerContext(user_id=7, role=UserRole.handler, employer_ids=frozenset({1}))


async def test_a_client_that_disconnects_mid_run_leaves_the_thread_usable(
    seeded_db_url: str,
) -> None:
    """…and the same property end to end: the next message is accepted.

    The first run is held mid-flight and its request task is then cancelled,
    which is what a browser closing a connection does to the ASGI call. What
    must be true afterwards is not "the lock was released" — that is the test
    above — but the thing a handler experiences: they reload the panel and can
    ask again.
    """
    async with (
        a_run_held_mid_flight() as blocking,
        make_client(seeded_db_url, model=blocking) as client,
    ):
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        abandoned = asyncio.create_task(
            client.post(f"/copilot/threads/{thread}/runs", json={"message": "first"})
        )
        while not abandoned.done() and not _run_started(thread):
            await asyncio.sleep(0.02)
        assert _run_started(thread), "the run never took its lock; the fixture is inert"

        abandoned.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await abandoned
        _MODEL_GATE.set()

        for _ in range(100):
            if not _run_started(thread):
                break
            await asyncio.sleep(0.02)
        assert not _run_started(thread), "the abandoned run kept its lock"

        events = await read_stream(client, thread, "second")
        assert events[-1] == "done"


# --- minting, guarded ----------------------------------------------------


async def test_a_new_conversation_is_refused_while_the_current_one_answers(
    seeded_db_url: str,
) -> None:
    """ "New conversation" cannot freeze a run that is still writing into it.

    Minting `seq + 1` against a running thread makes the in-flight answer land
    in a conversation that has just become read-only: it is still checkpointed,
    the transcript route still shows it, and the composer that could have asked
    again is gone. The handler asked a question and the answer arrived somewhere
    they cannot continue.

    Same 409 and same `type` as the run route's, because the caller's move is
    the same one — wait a moment and press it again.
    """
    async with (
        a_run_held_mid_flight() as blocking,
        make_client(seeded_db_url, model=blocking) as client,
    ):
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        running = asyncio.create_task(
            client.post(f"/copilot/threads/{thread}/runs", json={"message": "first"})
        )
        while not running.done() and not _run_started(thread):
            await asyncio.sleep(0.02)
        assert _run_started(thread), "the run never took its lock; the fixture is inert"

        refused = await client.post(f"/copilot/claims/{claim_id}/threads")

        assert refused.status_code == 409, refused.text
        assert refused.json()["type"] == "/problems/thread-busy"
        assert set(refused.json()) == {"type", "title", "status", "detail"}

        _MODEL_GATE.set()
        assert (await running).status_code == 200
        # …and once the run is over, the button works again.
        assert (await client.post(f"/copilot/claims/{claim_id}/threads")).status_code == 201


async def test_a_mint_that_loses_every_race_is_a_409_rather_than_a_500(
    seeded_db_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry bound, and what happens at the end of it.

    `open_thread` retried **once**, on a repository docstring's claim that its
    minting statement had "no read-then-write gap". It has one, so a third
    simultaneous click made the retry lose too and the handler got a 500 with a
    stack trace behind it. The bound is larger now and its exhaustion is a
    refusal a client can act on.

    Forced rather than raced: the repository is made to raise the constraint
    violation every time, which is the state a genuinely pathological contention
    produces and which no number of concurrent clicks can be relied on to create.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)

        attempts: list[int] = []

        async def _always_races(*args: Any, **kwargs: Any) -> Any:
            attempts.append(1)
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))

        monkeypatch.setattr("data.repositories.copilot.insert_next_thread", _always_races)
        refused = await client.post(f"/copilot/claims/{claim_id}/threads")

        assert refused.status_code == 409, refused.text
        assert refused.json()["type"] == "/problems/thread-busy"
        assert len(attempts) > 2, (
            "the mint gave up after one retry — the bound is what the "
            "read-then-write gap makes load-bearing"
        )


# --- the checkpoint store, unreachable -----------------------------------


@pytest.mark.parametrize("route", ["messages", "runs"])
async def test_an_unreachable_checkpoint_store_is_a_problem_document(
    seeded_db_url: str, route: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """503 with an RFC 9457 body, not an unhandled 500 with `about:blank`.

    This is not an exotic state. `build_copilot` opens its psycopg pool
    **without waiting for a connection** on purpose — AD-14's posture is that
    claim operations never depend on the agent runtime, so a copilot that cannot
    reach its store must not stop the api booting or answering `/healthz`. Which
    means the process routinely starts in exactly the state that used to produce
    an unhandled exception on both routes that ask the saver anything.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        async def _unreachable(*args: Any, **kwargs: Any) -> Any:
            raise OSError("the checkpoint pool has no connection")

        monkeypatch.setattr("api.routers.copilot.thread_state", _unreachable)
        answered = (
            await client.get(f"/copilot/threads/{thread}/messages")
            if route == "messages"
            else await client.post(f"/copilot/threads/{thread}/runs", json={"message": "anything"})
        )

        assert answered.status_code == 503, answered.text
        assert answered.headers["content-type"] == "application/problem+json"
        assert answered.json()["type"] == "/problems/copilot-unavailable"
        assert answered.headers["cache-control"] == "no-store"
        # …and the run's lock was never taken, so the thread is not stuck.
        assert not _run_started(thread)


# --- the invariant that must not vanish under `python -O` ----------------


async def test_a_thread_naming_an_invisible_claim_raises_rather_than_asserts(
    seeded_db_url: str,
) -> None:
    """`select_claim_business_id`'s guard is a `raise`, not a bare `assert`.

    Under `python -O` an `assert` is compiled away, and this one stands between
    a corrupted row and `None` flowing into `ThreadView.claim_business_id` — from
    there into the claim a run binds its tools to, and into a thread id. An
    invariant that disappears under an interpreter flag is not one.

    Asserted by asking for a claim the caller cannot see, which is the shape a
    corrupted `copilot_thread.claim_id` would have.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from data.context import CallerContext
    from data.models.enums import UserRole
    from data.repositories.copilot import ThreadClaimMissing, select_claim_business_id

    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine)() as session:
            ctx = CallerContext(user_id=1, role=UserRole.handler, employer_ids=frozenset({-1}))
            with pytest.raises(ThreadClaimMissing):
                await select_claim_business_id(session, ctx, claim_pk=1)
    finally:
        await engine.dispose()


async def test_a_failure_between_taking_the_lock_and_streaming_releases_it(
    seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lock was acquired **outside any `try`** — the other permanent 409.

    Between `lock.__aenter__()` and the construction of the streaming generator
    sat `await db.rollback()` and `run_inputs(...)`. A dropped pooled connection
    makes the rollback raise; the generator is then never created, its `finally`
    never runs, and a session-scoped advisory lock survives the `ROLLBACK` that
    returns the connection to the pool. One 500 on one request became a
    conversation that answered 409 for the life of the process.

    `run_inputs` stands in for the rollback because it is in the same window and
    is reachable without breaking the session the rest of the request needs;
    what is under test is the window, not which line inside it failed.
    """

    def _explodes(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("a pooled connection went away")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        monkeypatch.setattr("api.routers.copilot.run_inputs", _explodes)
        with contextlib.suppress(RuntimeError):
            await client.post(f"/copilot/threads/{thread}/runs", json={"message": "first"})
        monkeypatch.undo()

        assert not _run_started(thread), (
            "the run's lock outlived the request that took it — the thread would "
            "answer 409 for ever"
        )
        events = await read_stream(client, thread, "second")
        assert events[-1] == "done"


async def test_a_run_that_outlasts_its_wall_clock_is_one_error_frame(
    seeded_db_url: str,
) -> None:
    """The `TimeoutError` branch of the terminal decision, which nothing took.

    The bound is enforced around the whole stream rather than inside the graph,
    so that the decision to terminate lives in the same place that decides which
    terminal frame to send. That is only true if the timeout path actually ends
    in one frame, and it had never run.
    """
    async with (
        a_run_held_mid_flight() as blocking,
        # A wall clock short enough that the held model cannot beat it.
        make_client(seeded_db_url, model=blocking, run_timeout_seconds=0.1) as client,
    ):
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        events, payloads = await read_stream_with_payloads(client, thread, "what happened?")

        assert [event for event in events if event in {"done", "interrupt", "error"}] == ["error"]
        assert payloads[-1]["type"] == "/problems/copilot-run-timeout"
        assert payloads[-1]["status"] == 504
        # …and the lock is gone, which is what makes the thread usable again
        # after a timeout rather than after a restart.
        assert not _run_started(thread)


async def test_the_producer_is_awaited_before_the_lock_is_released() -> None:
    """A cancelled producer is *waited for*, not merely told to stop.

    `Task.cancel()` schedules a `CancelledError`; it does not wait for it to be
    delivered. On the timeout path that left an orphan still inside
    `graph.astream` — issuing checkpoint writes for a run that had already
    ended, into a thread on which a new run may by then have started, because
    the lock had been released the moment `cancel()` returned.

    Asserted as an **order**, which is the only thing that distinguishes the fix
    from the bug: both release the lock eventually.
    """
    from api.routers import copilot as router_module

    order: list[str] = []

    class _Lock:
        async def __aenter__(self) -> bool:  # pragma: no cover - not used here
            return True

        async def __aexit__(self, *exc: Any) -> bool:
            await asyncio.sleep(0)
            order.append("lock released")
            return False

    class _SlowToStopGraph:
        async def astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            try:
                await asyncio.Event().wait()
                yield  # pragma: no cover - unreachable
            finally:
                # A real `astream` unwinds through the saver on the way out.
                await asyncio.sleep(0.01)
                order.append("producer finished")

    runtime = dataclasses.replace(_runtime_stub(), graph=_SlowToStopGraph())

    stream = router_module._stream(
        lock=_Lock(),
        copilot=runtime,
        ctx=_a_context(),
        thread_id="claim.WC-20017.u7.s1",
        claim_business_id="WC-20017",
        inputs={},
        resume=None,
    )

    async def consume() -> None:
        async for _frame in stream:  # pragma: no cover - nothing is ever yielded
            pass

    with anyio.CancelScope() as scope:
        async with anyio.create_task_group() as group:
            group.start_soon(consume)
            await asyncio.sleep(0.05)
            scope.cancel()

    assert order == ["producer finished", "lock released"], (
        "the run's lock was handed back while its producer was still unwinding through the saver"
    )
