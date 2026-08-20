"""A conversation survives the process that had it — the story's whole point.

FR-CP-2 asks for per-claim history that survives navigation *and re-login*, and
AC 3 states the acceptance bar: run a turn, replace the API process, come back
to the claim, and read the same transcript. Everything else in Story 6.3 is
machinery in service of this one property, and it is the only property that
cannot be asserted inside a single app instance — which is why it has a module
of its own rather than a test at the bottom of `test_copilot_threads.py`.

**"Replaced" means genuinely replaced.** Each `make_client` builds a new
`FastAPI` app, runs its real `lifespan`, opens a new psycopg pool and constructs
a new `AsyncPostgresSaver`. Nothing is shared between the two blocks except the
database — no cache, no module global, no in-memory saver. The first app is fully
torn down before the second is built, so a transcript that came back from
process memory would come back empty here.

The chat model is scripted, for `test_copilot_threads.py`'s reason and with one
addition: the *content* is what the second process reads back, so a model whose
answers varied would make the round-trip assertion an assertion about prose.
"""

import dataclasses
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import httpx
import pytest
import sqlalchemy as sa
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from agents.graph import build_graph
from api import create_app
from config import Settings
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

HANDLER = ("Sarah Williams", "handler")

#: The scripted answer. Constant, because the second process compares against it
#: — see the module docstring.
REPLY = "Deterministic test answer from a scripted chat model, not from any model server."


def _forever() -> Iterator[AIMessage]:
    while True:
        yield AIMessage(content=REPLY)


class _ScriptedChatModel(GenericFakeChatModel):
    """Answers `REPLY` for ever, with no I/O."""

    def __init__(self) -> None:
        super().__init__(messages=_forever())


@pytest.fixture(autouse=True)
def a_conversation_free_database(seeded_db_url: str) -> None:
    """Empty the conversation store before every test — see the sibling module.

    Load-bearing here in a way it is not there: this file mints thread ids and
    then expects a *second process* to find exactly the checkpoints the first
    wrote. A leftover checkpoint under the same id from an earlier test would
    make the round trip pass while proving nothing.
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
async def make_client(db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """A **new** app, with its own lifespan, pool and saver. Torn down on exit."""
    app = create_app(Settings(database_url=db_url, env="e2e"))  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        runtime = app.state.copilot
        app.state.copilot = dataclasses.replace(
            runtime,
            graph=build_graph(
                model=_ScriptedChatModel(),
                checkpointer=runtime.checkpointer,
                max_tool_calls=runtime.max_tool_calls,
                tools=[],
            ),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def login_as(client: httpx.AsyncClient, name: str, role: str) -> None:
    personas = (await client.get("/personas")).json()["items"]
    match = [p for p in personas if p["name"] == name and p["role"] == role]
    assert len(match) == 1
    assert (await client.post("/auth/login", json={"personaId": match[0]["id"]})).status_code == 200


def a_claim_of(persona: tuple[str, str]) -> str:
    return sorted(seed_fixture.expected_claim_ids(*persona))[0]


async def run_turn(client: httpx.AsyncClient, thread_id: str, message: str) -> None:
    async with client.stream(
        "POST", f"/copilot/threads/{thread_id}/runs", json={"message": message}
    ) as response:
        assert response.status_code == 200, await response.aread()
        await response.aread()


async def test_a_transcript_survives_the_process_that_wrote_it(seeded_db_url: str) -> None:
    """AC 3 — the story's point, in one test.

    A turn is run in one app instance. That instance is torn down completely.
    A second instance is built, the same persona logs in again, and the
    conversation is still there: the same thread listed as current, and the same
    two messages in the same order.

    **Re-login is part of it** (FR-CP-2). The second block authenticates from
    scratch, so a transcript that had been keyed to a session rather than to a
    thread would not come back.
    """
    claim_id = a_claim_of(HANDLER)

    async with make_client(seeded_db_url) as first:
        await login_as(first, *HANDLER)
        thread = (await first.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]
        await run_turn(first, thread, "what should I do next on this claim?")
        before = (await first.get(f"/copilot/threads/{thread}/messages")).json()

    async with make_client(seeded_db_url) as second:
        await login_as(second, *HANDLER)
        listed = (await second.get(f"/copilot/claims/{claim_id}/threads")).json()
        after = (await second.get(f"/copilot/threads/{thread}/messages")).json()

    assert listed["currentThreadId"] == thread
    assert after == before
    assert [message["role"] for message in after["messages"]] == ["user", "assistant"]
    assert after["messages"][0]["content"] == "what should I do next on this claim?"
    assert after["messages"][1]["content"] == REPLY


async def test_the_checkpoint_tables_actually_hold_the_rows(seeded_db_url: str) -> None:
    """The saver wrote to the vendored tables — the migration is doing its job.

    Asserted with SQL **from a test**, which is allowed and is the only place it
    is: AD-3's exception forbids *application* code from reading these tables,
    and a test is the independent oracle. Without this, a round trip that
    happened to work through some cache would look identical to one that
    persisted.
    """
    claim_id = a_claim_of(HANDLER)
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]
        await run_turn(client, thread, "summarise this claim")

    engine = sa.create_engine(
        seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with engine.connect() as conn:
            checkpoints = conn.scalar(
                sa.text("SELECT count(*) FROM checkpoints WHERE thread_id = :thread"),
                {"thread": thread},
            )
            writes = conn.scalar(
                sa.text("SELECT count(*) FROM checkpoint_writes WHERE thread_id = :thread"),
                {"thread": thread},
            )
    finally:
        engine.dispose()

    assert checkpoints and checkpoints > 0
    assert writes and writes > 0


async def test_a_second_conversation_does_not_inherit_the_first_ones_history(
    seeded_db_url: str,
) -> None:
    """ "New conversation" is a new thread id, so it is a new transcript.

    The prior thread stays readable — that is `test_copilot_threads.py`'s
    assertion — but the new one starts empty, which is what makes the affordance
    mean anything. A minting rule that reused an id would show a handler their
    old conversation under a button labelled "new".
    """
    claim_id = a_claim_of(HANDLER)
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        first = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]
        await run_turn(client, first, "the first conversation")

        second = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

        assert second != first
        assert (await client.get(f"/copilot/threads/{second}/messages")).json()["messages"] == []
        old = (await client.get(f"/copilot/threads/{first}/messages")).json()
        assert old["messages"][0]["content"] == "the first conversation"


async def test_history_is_readable_after_the_thread_is_superseded_and_the_process_replaced(
    seeded_db_url: str,
) -> None:
    """The two halves together, because that is the state a real handler is in.

    They came back tomorrow, the panel opened a new conversation, and the one
    from yesterday is still readable. A retention rule deletes it eventually
    (`copilot_checkpoint_retention_days`, purged by Epic 8) — nothing in this
    story does.
    """
    claim_id = a_claim_of(HANDLER)
    async with make_client(seeded_db_url) as first:
        await login_as(first, *HANDLER)
        old = (await first.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]
        await run_turn(first, old, "yesterday's question")
        await first.post(f"/copilot/claims/{claim_id}/threads")

    async with make_client(seeded_db_url) as second:
        await login_as(second, *HANDLER)
        history = (await second.get(f"/copilot/threads/{old}/messages")).json()
        refused = await second.post(
            f"/copilot/threads/{old}/runs", json={"message": "still there?"}
        )

    assert history["isCurrent"] is False
    assert history["messages"][0]["content"] == "yesterday's question"
    assert refused.status_code == 409
    assert refused.json()["type"] == "/problems/thread-read-only"
