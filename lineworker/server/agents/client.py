"""The build's **only** chat client (AD-5, and the dependency diagram's
`AGENTS -->|chat| OLLAMA` edge).

`services/rag/client.py` is the only module that speaks to Ollama's embeddings
endpoint and says at length why that property is worth keeping checkable. This
is its counterpart for inference, and the two together are the complete answer
to "what does this process send to a model, and where does it send it?" — two
files, one endpoint each, both reading `OLLAMA_BASE_URL` from `config.py`
through a factory. `services/rag/client.py`'s docstring accounts for this file
by name; neither claim survives a third client, and there must not be one.

**It lives in `agents/` and not in `services/` because the architecture says
so.** The dependency diagram allows chat inference only from `agents/`, and
`services/` may never import `agents/` — which is the entire reason
`services/rag/insights.py` takes an injected `InsightGenerator` rather than
calling a model itself. Moving this module one directory would collapse that
arrangement and put a chat client inside the package whose singular purpose is
being the reason anyone trusts the deployment.

**And there is no cloud path.** The prototype's copilot was a credential-less
browser `fetch` to `api.anthropic.com` that always failed into pre-written
canned text; that call is the specific anti-pattern AD-5 exists to kill, and it
has no successor here — not behind a flag, not as a degraded mode. When the
model server is unreachable, `ChatUnavailable` propagates, the affected insight
kind is not written, and the card keeps rendering its previous generation
beside its previous timestamp. That is a cache being honest, not a fallback.

## Structured output, and why the vendor does it rather than a parser here

`with_structured_output(schema, method="json_schema")` is `docs/Architecture-
LINEWORKER.md` §5.3's prescription, and what it does is pass the Pydantic
model's JSON Schema to Ollama as the request's `format` field. The server then
*constrains decoding* to that grammar rather than being asked politely for JSON
in a prompt — so a malformed answer is a rare failure of the model rather than
the ordinary case a hand-rolled parser has to survive. The response is parsed
back into the model class by LangChain's `PydanticOutputParser`.

That leaves this module with exactly one job of its own: turning the two ways
that can go wrong into two exception types, because the caller has to tell them
apart. A transport failure means *nothing* can be generated until the container
comes back (503 on the interactive route); a schema rejection means this
completion was bad and the next one may not be (200, cards unchanged). One
exception type would have collapsed those into one status code, and whichever
it picked would be wrong for the other case.

## AD-11

Nothing from this module reaches a log line — not a prompt, not a completion,
not a length in characters, not a hash. Both exceptions carry a fixed sentence
and chain their cause for a debugger; neither stringifies the vendor's message,
because an HTTP error's text can echo the request body, and the request body
here is a prompt containing claim narrative.
"""

import json
from typing import Any, Protocol

import structlog
from langchain_core.exceptions import OutputParserException
from langchain_ollama import ChatOllama
from pydantic import BaseModel, ValidationError

from config import Settings

log = structlog.get_logger()

#: Ollama's chat endpoint, named for the same reason `EMBED_PATH` is: it is the
#: string a reviewer greps for when they want to know what this process sends
#: where. The vendor client builds the URL itself, so this constant is
#: documentation and the assertion in `tests/test_ai_insights.py` is what keeps
#: it honest.
CHAT_PATH = "/api/chat"

#: Decoding temperature for every insight generation. Zero, and it is a
#: correctness knob rather than a taste one: AD-15's done-gate is a Playwright
#: spec that must produce the same result on two runs of one commit, and a
#: sampled narrative would make "the card renders" a flaky assertion about
#: prose. It is also the honest setting for a cache — a claim's reserve review
#: should not read differently because it was regenerated.
CHAT_TEMPERATURE = 0.0


class ChatError(RuntimeError):
    """Base for the two ways a structured completion fails. Never logged raw."""


class ChatUnavailable(ChatError):
    """The model server did not answer — down, unreachable, or timed out.

    Temporary and *global*: no kind can be generated while it holds, which is
    what `POST …/insights/refresh` turns into a 503 and what the scheduled job
    logs and survives. Story 6.6 owns the degradation UX; this is the exception
    it will be built on.
    """


class ChatSchemaRejected(ChatError):
    """The model answered, and the answer failed the kind's Pydantic schema.

    The AD-2 boundary doing its job: nothing that failed validation reaches
    `ai_insight`, so a kind is either a complete, typed card or it is absent —
    never a half-written one. Not `ChatUnavailable`, because the server is
    perfectly healthy and the next attempt may well succeed.
    """


class ChatClient(Protocol):
    """What insight generation needs from a model server: a name and one method.

    A `Protocol` for `EmbeddingClient`'s reason and with the same dividend:
    every test of the generation pipeline — the AD-2 equality assertion, the
    schema-rejection path, the low-risk fraud variant, the injection fixtures —
    runs against a fake with no model server, no network and no container.

    The method is *structured-only*. There is deliberately no `complete()`
    returning a string: this story persists validated structures rather than
    prose blobs (AD-10, and Story 7.1's portfolio aggregation depends on it),
    and a free-text method would be the shape a later story reaches for when it
    is in a hurry. Story 6.3's streaming chat has genuinely different needs and
    will own its own surface rather than widening this one.
    """

    @property
    def model(self) -> str:
        """The serving model's name, recorded on every row it produces."""
        ...

    async def structured[T: BaseModel](self, *, system: str, user: str, schema: type[T]) -> T:
        """One completion, decoded into `schema`, or a `ChatError`."""
        ...


class OllamaChatClient:
    """`ChatClient` over `ChatOllama`. The only implementation shipped.

    Built through `chat_client(settings)` at the bottom of this file rather
    than by its callers, so this module names `OLLAMA_BASE_URL` once.

    **A client per instance, held for the lifetime of a refresh run rather than
    of the process.** `ChatOllama` owns an `httpx.AsyncClient` underneath, and a
    long-lived one on a module global would outlive the event loop it was
    created on — the specific failure that makes an asyncio HTTP client unusable
    after a test suite's second app instance (`OllamaEmbeddingClient` records the
    same reasoning from the other end). A refresh run is the right scope: it is
    one job, on one loop, making up to four completions per claim.

    `async_client_kwargs` is where the timeout goes, because that is the dict
    `ChatOllama` hands to the `ollama` package's `AsyncClient`, which hands it
    to httpx. `CHAT_REQUEST_TIMEOUT_SECONDS` is generous for the reason its
    sibling knob is: a cold model on a CPU-only dev box takes a long time to
    answer its first request, and the architecture explicitly permits that
    hardware for chat.
    """

    def __init__(self, *, base_url: str, model: str, timeout_seconds: float) -> None:
        # Trailing slash stripped for `OllamaEmbeddingClient`'s reason: the
        # value comes from an operator's env file, and a URL that works
        # depending on how it was typed is a support question waiting to happen.
        self._model = model
        self._chat = ChatOllama(
            base_url=base_url.rstrip("/"),
            model=model,
            temperature=CHAT_TEMPERATURE,
            async_client_kwargs={"timeout": timeout_seconds},
        )

    @property
    def model(self) -> str:
        """The model name, published so the command can record it as provenance.

        `ai_insight.model` is written from this rather than from `Settings` read
        a second time, so the column records what actually answered rather than
        what configuration said should have — `OllamaEmbeddingClient.model`'s
        rule, and here it is on screen: the card labels the narrative with it.
        """
        return self._model

    async def structured[T: BaseModel](self, *, system: str, user: str, schema: type[T]) -> T:
        """One constrained completion, decoded into `schema`.

        The system message is the versioned prompt file's text — the only
        instruction channel (AD-16) — and `user` is the material to analyse,
        per-item delimited and tagged with its source id by the caller. This
        method does not concatenate them into one string: they travel as two
        messages so the model's own role separation is doing the work rather
        than a delimiter the caller invented.

        Raises `ChatSchemaRejected` when the answer does not validate and
        `ChatUnavailable` for everything else. The ordering of the `except`
        clauses is the whole of that distinction, so it is written as an
        allow-list of parse failures with a catch-all beneath: a new vendor
        transport error should read as "unavailable" (retry later, 503), which
        is the safe direction — mislabelling a transport outage as a bad
        completion would tell a handler their claim produced garbage when the
        container was simply down.
        """
        runnable = self._chat.with_structured_output(schema, method="json_schema")
        try:
            answer: Any = await runnable.ainvoke([("system", system), ("human", user)])
        except (OutputParserException, ValidationError, json.JSONDecodeError) as exc:
            raise ChatSchemaRejected(f"the model's answer did not match {schema.__name__}") from exc
        except Exception as exc:
            raise ChatUnavailable("the local model server did not answer") from exc

        # `with_structured_output` returns the parsed model for a Pydantic
        # schema, but its declared return type is `dict | BaseModel` — so the
        # dict branch is handled rather than cast away. Re-validating a model
        # instance is cheap and makes this function total: whatever the vendor
        # hands back, the caller receives `schema` or an exception.
        try:
            if isinstance(answer, schema):
                return answer
            if isinstance(answer, BaseModel):
                return schema.model_validate(answer.model_dump())
            return schema.model_validate(answer)
        except ValidationError as exc:
            raise ChatSchemaRejected(f"the model's answer did not match {schema.__name__}") from exc


def chat_client(settings: Settings) -> OllamaChatClient:
    """Build the shipped chat client from configuration. The one construction site.

    Three callers want one — the scheduled insight-refresh job, the on-demand
    `POST /claims/{id}/insights/refresh` route and the e2e admin trigger — and
    each needs the same three fields wired the same way. A factory rather than
    three copies of the constructor call, for `embedding_client`'s reason: it
    keeps `OLLAMA_BASE_URL` named once per client in the whole tree, which is
    what makes AD-5 a ten-second grep instead of an audit.

    Takes `Settings` rather than reading `get_settings()`, because `config.py`
    is the only module in the server allowed to touch the environment and an
    agent that reached for the global would be a second one.
    """
    return OllamaChatClient(
        base_url=settings.ollama_base_url,
        model=settings.chat_model,
        timeout_seconds=settings.chat_request_timeout_seconds,
    )


__all__ = [
    "CHAT_PATH",
    "CHAT_TEMPERATURE",
    "ChatClient",
    "ChatError",
    "ChatSchemaRejected",
    "ChatUnavailable",
    "OllamaChatClient",
    "chat_client",
]
