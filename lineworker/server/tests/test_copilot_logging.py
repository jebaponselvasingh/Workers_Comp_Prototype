"""AD-11 on the copilot's two new leak surfaces — asserted against real stderr.

`test_ai_insights.py::test_no_log_line_carries_a_prompt_or_the_model_s_answer`
does this for insight generation, and the technique carries over: run the real
path, capture the bytes the process actually emitted, and search them for the
distinctive strings the fixtures used. What does not carry over is *what* is at
risk, because Story 6.3 adds two surfaces nothing in the build has ever had:

1. **Checkpoint writes serialise the whole message history.** Every turn of a
   conversation — the handler's questions and the model's answers about a
   claim's diagnosis, wage and reserve — goes through the saver on every run. A
   debug line at that boundary ("what are we checkpointing?") would put an
   entire transcript in an operator's log aggregator for ever.
2. **SSE frames carry tokens.** The transport's whole job is moving model
   output, so the tempting line is at exactly the wrong place: logging a frame
   logs the answer.
3. **A quick action composes a whole prompt out of a claim.** Story 6.4's nodes
   build a two-section user message carrying the reserve verdict, the fraud
   thresholds, the retrieved labour-law passages and the claim's own fenced
   narrative, then stream a narration about all of it. That is the largest
   single block of claim data this build ever assembles, and the tempting log
   line — "what did we send?" — would put the whole of it in an aggregator.
4. **Tool results are the richest PHI on the wire.** `claim_reader`'s envelope
   carries the injured worker's name and role, the employer, the ICD text and
   the whole of `cause` — and it is serialised into a checkpoint and read back
   into the model on the next step. This one was added by the review of Story
   6.3: every run in this module was toolless, so the transcript being searched
   contained no `ToolMessage` and the strongest surface the story creates could
   not be seen from here.

None of the four had a precedent test, and all are the kind of thing a later
story adds while debugging and forgets to take out.

**Every assertion here has a positive control.** A test that searches stderr for
absent strings passes identically against a process that logged nothing at all —
including one where logging was misconfigured and the whole file is inert. So
each assertion is paired with a named event that *must* be present, which is
`test_ai_insights.py:976`'s device.
"""

import dataclasses
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agents.fencing import FIGURES_HEADING, ITEM_OPEN, MATERIAL_HEADING
from agents.graph import build_graph
from agents.registry import build_tools
from api import create_app
from config import Settings
from data.models.enums import ClaimStatus, Stage
from logging_config import configure_logging
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

HANDLER = ("Sarah Williams", "handler")

#: The handler's question and the model's answer, both deliberately unmistakable.
#:
#: Long, specific and unlike anything else the process emits, so the searches
#: below are real rather than a check that some field name is absent. If either
#: string is anywhere in stderr, something logged content.
QUESTION = (
    "Distinctive copilot question fixture about the injured worker's lumbar strain and reserve."
)
ANSWER = "Distinctive copilot answer fixture that no log line in this build is permitted to carry."


def _forever() -> Iterator[AIMessage]:
    while True:
        yield AIMessage(content=ANSWER)


class _ScriptedChatModel(GenericFakeChatModel):
    """Answers `ANSWER` for ever, with no I/O."""

    def __init__(self) -> None:
        super().__init__(messages=_forever())


@pytest.fixture(autouse=True)
def a_conversation_free_database(seeded_db_url: str) -> None:
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


async def _reserve_money(db_url: str, claim_business_id: str) -> list[str]:
    """Every money string one claim's reserve check produces, from a second call.

    `test_copilot_qas.py`'s provenance technique, used here for the opposite
    purpose: those are the strings the quick action put in front of the model,
    so they are exactly the strings that must not be in stderr. Reading them
    from the service rather than searching for `"$"` is what keeps the assertion
    about *this run's figures* — a bare `"$" not in emitted` is a claim about
    the whole of stderr, app construction and pool setup included, and any
    future log line carrying a dollar sign for an unrelated reason would fail it
    while reporting "a money figure reached the log".
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from agents.tools import reserve_check
    from data.context import ALL_EMPLOYERS, CallerContext
    from data.models import AppUser
    from data.models.enums import UserRole

    engine = create_async_engine(db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            user = (
                await session.scalars(sa.select(AppUser).where(AppUser.role == UserRole.system))
            ).one()
            ctx = CallerContext(user_id=user.id, role=user.role, employer_ids=ALL_EMPLOYERS)
            result = await reserve_check(session, ctx, claim_business_id=claim_business_id)
    finally:
        await engine.dispose()
    assert result.ok, "the reserve check declined — the assertion below would be vacuous"
    return list(result.display.values())


async def _one_run(db_url: str) -> tuple[str, list[str]]:
    """Run one whole turn through the shipped routes. Returns the thread and frames."""
    frames: list[str] = []
    async with make_client(db_url) as client:
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]
        async with client.stream(
            "POST", f"/copilot/threads/{thread}/runs", json={"message": QUESTION}
        ) as response:
            assert response.status_code == 200, await response.aread()
            async for line in response.aiter_lines():
                frames.append(line)
        # Read it back, so the *history* path is exercised too — it deserialises
        # a whole transcript and is the second place a debug line would be
        # tempting.
        await client.get(f"/copilot/threads/{thread}/messages")
    return thread, frames


async def test_no_log_line_carries_the_question_the_answer_or_a_checkpoint(
    seeded_db_url: str,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The three things a copilot log must never contain, and one it must.

    The positive control is `copilot.run_started`: if it is absent, logging was
    not configured or the run never happened, and every "not in stderr" above it
    would have passed vacuously.
    """
    configure_logging("INFO")
    thread, _frames = await _one_run(seeded_db_url)

    emitted = capfd.readouterr().err

    assert "copilot.run_started" in emitted, (
        "no copilot log line was emitted at all — this test cannot tell a clean log "
        "from a silent one"
    )
    assert QUESTION not in emitted, "the handler's message reached the log (AD-11)"
    assert ANSWER not in emitted, "the model's answer reached the log (AD-11)"
    # A checkpoint serialises the message list; the surest sign one was logged is
    # the vendor's own message envelope showing up beside the content.
    assert "HumanMessage" not in emitted
    assert "AIMessage" not in emitted


async def test_the_events_that_are_emitted_carry_ids_durations_and_nothing_else(
    seeded_db_url: str,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """What AD-11 *permits*: `thread_id`, event names, durations.

    Asserted positively as well as negatively, because "carries IDs only" is a
    claim about what is there as much as about what is not — an operator with no
    thread id in the log cannot investigate a run at all, and a story that
    stripped the field to be safe would have made the logs useless rather than
    compliant.
    """
    configure_logging("INFO")
    thread, _frames = await _one_run(seeded_db_url)

    emitted = capfd.readouterr().err

    assert "copilot.thread_minted" in emitted
    assert "copilot.run_started" in emitted
    assert "copilot.run_finished" in emitted
    assert thread in emitted, "the thread id is the one identifier a run's log must carry"
    assert "duration_ms" in emitted


async def test_the_stream_carries_the_answer_so_the_absence_above_means_something(
    seeded_db_url: str,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The positive control for the *content* half, not only for the logging half.

    The answer has to be somewhere, or "the answer is not in the log" is a
    statement about a run that produced nothing. It is on the wire — which is
    where it belongs — and the frames prove it.
    """
    configure_logging("INFO")
    _thread, frames = await _one_run(seeded_db_url)

    # The frames' *content*, reassembled — not the raw wire text. A streamed
    # answer arrives as several `messages` frames, so the sentence is split
    # across `data:` lines and joining the frames verbatim would put a newline
    # in the middle of it. Reassembling is what a client does anyway, and it is
    # what makes this assertion about the answer rather than about the framing.
    streamed = "".join(
        json.loads(line.removeprefix("data: "))["content"]
        for index, line in enumerate(frames)
        if line.startswith("data: ") and frames[index - 1] == "event: messages"
    )
    assert ANSWER in streamed, "the run produced no assistant content — the fixture is inert"
    assert ANSWER not in capfd.readouterr().err


# --- the strongest leak surface this story creates ------------------------
#
# Everything above runs the graph with `tools=[]`, so the transcript it searches
# stderr against contains no `ToolMessage` at all — and a tool result is the
# richest PHI this story puts on any wire (review of Story 6.3). `claim_reader`'s
# envelope carries the injured worker's name and role, the employer, the ICD text
# and the whole of `cause`, and every one of those is serialised into a
# checkpoint and passed back through the model on the next step.


#: What the stubbed case file says. Long, specific and unlike anything else the
#: process emits, exactly as `QUESTION` and `ANSWER` are.
WORKER = "Distinctive copilot worker fixture, Wieslawa Nowakowska-Testfixture"
CAUSE = "Distinctive copilot cause fixture: crushed between two press dies on the night shift."


class _ToolCallingChatModel(BaseChatModel):
    """Calls `claim_reader` once, then answers. The smallest model that can.

    `GenericFakeChatModel` does not implement `bind_tools`, which `create_agent`
    calls — so a fake built on it can never make a tool call, which is why every
    graph in this suite ran toolless until now.
    """

    script: Any

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-calling"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    def _generate(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=next(self.script))])


def _stub_header(claim_id: str) -> Any:
    from services.claims.detail import CaseHeader
    from services.derivations import RiskBand

    return CaseHeader(
        claim_id=claim_id,
        worker_name=WORKER,
        worker_role="Press Operator",
        employer_name="Caterpillar",
        state="IL",
        injury_type="Crush injury",
        body_part="Right Hand",
        body_key="hand_r",
        cause=CAUSE,
        icd="S67.20XA",
        severity_score=8,
        risk=RiskBand.high,
        stage=Stage.treatment,
        status=ClaimStatus.ch_approved,
        fraud_flag=False,
        fraud_score=9,
        litigation_flag=False,
        surgery_required=True,
        osha_recordable=True,
        osha_logged=False,
    )


async def test_no_log_line_carries_a_tool_result(
    seeded_db_url: str,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `ToolMessage` is the richest PHI on this story's wire, and it is not logged.

    The run below calls a real registered tool through the real registry, with
    only the *service* stubbed — so a real envelope containing a worker's name
    and a claim's cause enters the transcript, is checkpointed, and is fed back
    to the model. None of it may reach stderr.

    The positive controls are the two `copilot.tool_*` events the registry can
    emit and the run's own lifecycle lines: without `copilot.run_started` the
    searches below would pass against a process that logged nothing, and without
    a `ToolMessage` in the transcript they would pass against a run that never
    called a tool — which is exactly how this surface stayed untested.
    """
    import agents.tools.claim as claim_tool
    from agents.threads import thread_state

    async def _detail(db: Any, ctx: Any, claim_business_id: str) -> Any:
        return SimpleNamespace(header=_stub_header(claim_business_id))

    monkeypatch.setattr(claim_tool, "claim_detail", _detail)
    configure_logging("INFO")

    app = create_app(Settings(database_url=seeded_db_url, env="e2e"))  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        runtime = app.state.copilot
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await login_as(client, *HANDLER)
            claim_id = a_claim_of(HANDLER)
            thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]

            # The graph is built **after** the claim is known, because the tool
            # call has to name the thread's own claim: the registry confines that
            # argument to it, so a scripted call naming anything else is refused
            # before the service is reached — which would make this test search
            # stderr for strings no envelope ever carried.
            app.state.copilot = dataclasses.replace(
                runtime,
                graph=build_graph(
                    model=_ToolCallingChatModel(
                        script=iter(
                            [
                                AIMessage(
                                    content="",
                                    tool_calls=[
                                        {
                                            "name": "claim_reader",
                                            "args": {"claim_business_id": claim_id},
                                            "id": "call-1",
                                        }
                                    ],
                                ),
                                AIMessage(content=ANSWER),
                            ]
                        )
                    ),
                    checkpointer=runtime.checkpointer,
                    max_tool_calls=runtime.max_tool_calls,
                    tools=list(build_tools()),
                ),
            )

            async with client.stream(
                "POST", f"/copilot/threads/{thread}/runs", json={"message": QUESTION}
            ) as response:
                assert response.status_code == 200, await response.aread()
                async for _line in response.aiter_lines():
                    pass

            # Read the *checkpointed* transcript rather than the published one:
            # the history route deliberately drops tool messages, and the tool
            # message is the whole point of this test.
            state = await thread_state(app.state.copilot.graph, thread_id=thread)
            tool_messages = [m for m in state.messages if getattr(m, "type", "") == "tool"]

    emitted = capfd.readouterr().err

    assert "copilot.run_started" in emitted, "no copilot log line was emitted at all"
    assert len(tool_messages) == 1, (
        "the run made no tool call — this test cannot see the surface it exists for"
    )
    assert WORKER in str(tool_messages[0].content), "the stubbed case file never reached the tool"
    assert WORKER not in emitted, "a tool result's worker name reached the log (AD-11)"
    assert CAUSE not in emitted, "a tool result's claim narrative reached the log (AD-11)"
    assert "ToolMessage" not in emitted
    assert QUESTION not in emitted
    assert ANSWER not in emitted


# --- Story 6.4: a quick action's prompt, narration and retrieved text -----


#: A claim id that is definitely not the thread's, spelled distinctively.
#:
#: The quick-action log lines carry a `prompt_key` and a `prompt_version` and
#: nothing else, so the positive control below has to be the event name itself
#: rather than an id — which is the correct shape for AD-11 and the reason this
#: test asserts what *is* logged as carefully as what is not.
QAS_EVENT = "copilot.quick_action_narrated"


async def test_a_quick_action_logs_its_prompt_key_and_none_of_its_content(
    seeded_db_url: str,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """AD-11 on Story 6.4's surface: the biggest prompt this build composes.

    The run below is a real `reserve` quick action through the shipped route,
    against the real registry and the real `services/financials` — so a real
    reserve verdict, real formatted money strings and the claim's own fenced
    narrative are assembled into a user message and a narration is streamed
    back. None of it may reach stderr.

    **Three positive controls**, because "these strings are absent" passes
    identically against a run that never happened. `copilot.run_started` says a
    run happened; `copilot.quick_action_narrated` says it went through a
    quick-action node rather than falling through to free text; and the
    streamed answer proves the model was actually asked. Without the second,
    this test would have passed against a build where the key was silently
    ignored.

    The money assertion is the sharpest one and is worth stating plainly: a
    reserve figure is a claim's financial exposure, and the figures section
    carries several of them. If any `display` string appears in stderr,
    something logged a prompt.
    """
    configure_logging("INFO")

    frames: list[str] = []
    async with make_client(seeded_db_url) as client:
        # The shipped graph, not the toolless one `make_client` swaps in: the
        # quick-action nodes reach their tools through `agents/registry.invoke`
        # rather than through the agent harness, but the *model* has to be the
        # scripted one or this test would need a model server.
        await login_as(client, *HANDLER)
        claim_id = a_claim_of(HANDLER)
        thread = (await client.post(f"/copilot/claims/{claim_id}/threads")).json()["threadId"]
        async with client.stream(
            "POST",
            f"/copilot/threads/{thread}/runs",
            json={"message": "Reserve review", "quickAction": "reserve"},
        ) as response:
            assert response.status_code == 200, await response.aread()
            async for line in response.aiter_lines():
                frames.append(line)

    emitted = capfd.readouterr().err

    assert "copilot.run_started" in emitted, "no copilot log line was emitted at all"
    assert QAS_EVENT in emitted, (
        "the run did not go through a quick-action node — the absences below "
        "would be statements about a free-text turn"
    )
    assert '"prompt_key": "reserve"' in emitted, "the event carries no key to investigate a run by"
    assert ANSWER in "".join(frames), "the run produced no assistant content — the fixture is inert"

    # The narration, the two section headings, the fence and every money string
    # the claim's own reserve check produced.
    assert ANSWER not in emitted, "the narration reached the log (AD-11)"
    assert FIGURES_HEADING not in emitted, "a composed prompt reached the log (AD-11)"
    assert MATERIAL_HEADING not in emitted
    assert ITEM_OPEN not in emitted, "a fenced claim item reached the log (AD-11)"

    # **The run's own money, named** — not every `$` in stderr. The figures
    # section carried these exact strings, formatted by `services/financials`,
    # so each one appearing in the log is a prompt having been logged. The
    # previous form asserted `"$" not in emitted`, which is a statement about
    # the whole of stderr — app construction, lifespan, pool setup and every
    # structlog line — and would have failed on any unrelated future line
    # carrying a dollar sign, while reporting that a claim's exposure had
    # leaked.
    money = await _reserve_money(seeded_db_url, claim_id)
    assert money, "this claim has no reserve figures — the assertion below is vacuous"
    for amount in money:
        assert amount not in emitted, f"the reserve figure {amount} reached the log (AD-11)"
