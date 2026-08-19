"""The build's **only** embeddings client (AD-5, and the dependency diagram's
`SVC -->|embeddings only| OLLAMA` edge).

One module issues one kind of HTTP request to one host, and that is the whole
of this file's job. The property is worth stating as a property because it is
what makes AD-5 checkable rather than aspirational: "no PHI leaves the network"
is a claim about every outbound call in the process, and it stays verifiable in
about ten seconds — grep the tree for `ollama_base_url` and confirm that the
files which match are the ones this paragraph names.

**The grep answers *two* files since Story 6.2, and the second one is the
point rather than an exception.** `agents/client.py` is the build's chat
client: it exists because AD-5 and the dependency diagram allow inference to
originate only from `agents/`, so the alternative to a second file was a chat
call in *this* one — which would put the embeddings client and the chat client
in one module and make "what does `services/rag` send, and where?" a question
about a branch rather than about a file. Two named modules, one endpoint each,
both reading the base URL from `config.py` through a factory, is a property a
reviewer can still hold in their head; three would not be, and there must not
be a third.

**Nothing here calls the chat endpoint**, and that half is unchanged. This
module speaks to `/api/embed` and nothing else. A convenience `chat()` added
here "while we are in the file" would be a second code path with a model name
and a URL in it, sitting in the module whose singular purpose is the reason
anyone trusts the deployment.

**And there is no cloud fallback.** The prototype's copilot was a
credential-less browser `fetch` to `api.anthropic.com` that always failed into
pre-written canned text (`docs/Workers_Comp_Prototype.html`); that call is the
specific anti-pattern AD-5 exists to kill, and it has no successor here — not
behind a flag, not as a degraded mode. When Ollama is unreachable the refresh
job logs a failure and the rows stay stale, which is Story 6.6's honest
degradation rather than a second provider.

## Why there is a protocol as well as an implementation

`EmbeddingClient` is a `Protocol`, and every command in this package takes one
as a parameter rather than constructing an `OllamaEmbeddingClient` itself. That
is what lets the entire test suite — composer determinism, refresh ordering,
batch bounds, scope enforcement, the staleness round trip — run with no model
server, no network and no container, against a fake whose vectors are chosen to
make an ordering assertion legible. A test suite that needed a GPU to say
whether stale rows are embedded first is a test suite that gets skipped.

It is also how the e2e profile's `model-stub` works: nothing is swapped there
at all. The stub speaks Ollama's HTTP API, so the *real* client talks to it and
the code under test is the shipped code path (`deploy/model-stub/`).

## The dimension check, and why it raises instead of coping

`bge-m3` emits 1024 floats and `claim_embedding.embedding` is `vector(1024)`.
The architecture's constrained-hardware pairing names `nomic-embed-text`, which
emits 768 — so pointing `EMBEDDING_MODEL` at the CPU-friendly model against
this schema is a mistake somebody will make, deliberately, believing it is the
supported dev configuration.

Padding to 1024, truncating to 1024 or re-scaling would all "work": the insert
would succeed, the index would accept the row, the search would return
neighbours, and every one of those neighbours would be noise. There would be
nothing on any screen to explain it and nothing in any log to find. So the
client refuses, by name and by number — the model it asked, the length it got,
the length the column wants — and the refresh run fails loudly having written
nothing. Changing the embedding model's dimensionality is a migration, and this
exception is where that sentence is enforced rather than merely documented.

The CPU fallback for *chat* costs nothing and still works; it is only the
embedding width that is pinned, because only the embedding width is a column.

## AD-11

No text goes to a log line from this module — not the input, not a hash of it,
not a length in characters. The log carries the model name, the number of
inputs and the number of vectors returned. A claim summary is PHI and an
embedding is derived from one.
"""

from collections.abc import Sequence
from typing import Any, Protocol

import httpx
import structlog

from config import Settings

log = structlog.get_logger()

#: The width every vector in this build has, and the width both embedding
#: columns declare. Kept beside the client rather than imported from
#: `data/models/core.py` because this is the *validation* bound — the number a
#: model's answer is checked against — and the model's copy is a column type.
#: They are asserted equal (along with migration 0040's frozen literal) in
#: `tests/test_embedding_tables_migration.py`.
EMBEDDING_DIMENSIONS = 1024

#: Ollama's batch embeddings endpoint. `/api/embed` rather than the older
#: `/api/embeddings`: the newer route takes `input` as a string *or* a list and
#: answers `embeddings` as a list of vectors, so one round trip serves a whole
#: refresh batch. The older one is single-input only, which would turn a
#: 25-claim batch into 25 sequential requests against a server that processes
#: them one at a time anyway — but sequentially *and* with 25 round trips.
EMBED_PATH = "/api/embed"


class EmbeddingDimensionMismatch(RuntimeError):
    """A model returned vectors the column cannot hold. See the module doc.

    Carries all three numbers in its message because the useful version of this
    failure names them: which model was asked, what it returned, and what the
    schema expects. "dimension mismatch" alone sends the reader to the docs to
    find out what `bge-m3` emits.
    """

    def __init__(self, *, model: str, returned: int, expected: int) -> None:
        super().__init__(
            f"embedding model {model!r} returned {returned}-dimension vectors but the "
            f"claim_embedding/knowledge_embedding columns are vector({expected}). "
            "Changing EMBEDDING_MODEL to a model of a different dimensionality is a "
            "migration, not a configuration change — nothing was written."
        )
        self.model = model
        self.returned = returned
        self.expected = expected


class EmbeddingClient(Protocol):
    """What `services/rag`'s commands need from a model server: one method.

    Batch-shaped (`Sequence[str]` in, list of vectors out) rather than
    single-shaped, because every caller in this package has a batch: a refresh
    run has up to `embedding_refresh_batch_size` claims and the corpus seed has
    fifteen chunks. A one-at-a-time protocol would have made the batching a
    concern of each caller, and each would have got it slightly differently.

    The single-query case — a handler asking for similar cases — passes a
    one-element sequence, which is the right asymmetry: one shape to implement,
    one shape to fake, and the interesting path is the one that runs unattended.
    """

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each input, in order. The `i`-th vector is the `i`-th text."""
        ...


class OllamaEmbeddingClient:
    """`EmbeddingClient` over Ollama's HTTP API. The only implementation shipped.

    Built through `embedding_client(settings)` at the bottom of this file rather
    than by its callers, so this module is the only place in `services/` that
    names `OLLAMA_BASE_URL` — the other two readers being `config.py`, where it
    is declared, and `agents/client.py`, which the module docstring accounts
    for.

    **A client per call rather than a pooled one.** `httpx.AsyncClient` is
    created inside `embed` and closed when it returns. That is the wrong default
    for a hot request path and the right one here: the callers are a scheduled
    job that fires every fifteen minutes and an interactive query that runs
    once, so connection reuse would save nothing measurable, while a long-lived
    client held on a module global would outlive the event loop it was created
    on — which is the specific failure that makes an asyncio HTTP client
    unusable after a test suite's second app instance. Story 6.3's chat
    streaming has genuinely different needs and can own its own lifecycle.
    """

    def __init__(self, *, base_url: str, model: str, timeout_seconds: float) -> None:
        # Trailing slash stripped so `base_url + EMBED_PATH` cannot produce a
        # double slash. Cosmetic against Ollama, which tolerates it — but the
        # value comes from an operator's env file, and a URL that works
        # depending on how it was typed is a support question waiting to happen.
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds

    @property
    def model(self) -> str:
        """The model name, published so commands can record it as provenance.

        `claim_embedding.model` is written from this rather than from settings
        read a second time, so the column records what actually answered rather
        than what configuration said should have.
        """
        return self._model

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One POST, one vector per input, validated on the way back.

        Raises `EmbeddingDimensionMismatch` for a width the columns cannot hold,
        and `httpx.HTTPError` for anything the transport or the server refuses —
        both propagate. The refresh job catches, counts and logs; a caller that
        swallowed them here would leave rows silently unembedded for ever.
        """
        if not texts:
            # Not an error, and not a request: an empty batch is what a refresh
            # tick with nothing pending has, which is the steady state.
            return []

        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(
                f"{self._base_url}{EMBED_PATH}",
                json={"model": self._model, "input": list(texts)},
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()

        vectors = [[float(value) for value in vector] for vector in payload.get("embeddings", [])]

        if len(vectors) != len(texts):
            raise RuntimeError(
                f"embedding model {self._model!r} returned {len(vectors)} vectors for "
                f"{len(texts)} inputs; the i-th vector must be the i-th text's"
            )
        for vector in vectors:
            if len(vector) != EMBEDDING_DIMENSIONS:
                raise EmbeddingDimensionMismatch(
                    model=self._model,
                    returned=len(vector),
                    expected=EMBEDDING_DIMENSIONS,
                )

        # Counts and the model name. Never an input, never a hash of one, never
        # a character length — AD-11, and the inputs here are composed claim
        # summaries.
        log.info("rag.embedded", model=self._model, inputs=len(texts), vectors=len(vectors))
        return vectors


def embedding_client(settings: Settings) -> OllamaEmbeddingClient:
    """Build the shipped client from configuration. The one construction site.

    Four callers want one — the scheduled refresh job, the `/similar` route,
    the e2e admin trigger and (since Story 6.2) the similar-case insight's tool
    wrapper — and each needs the same three fields wired the same way. A
    factory rather than four copies of the constructor call, for a reason
    beyond tidiness: it keeps `OLLAMA_BASE_URL` named in one place per client
    rather than one place per call site. AD-5's "no PHI leaves the network" is a
    claim about every outbound call in the process, and it stays a ten-second
    grep rather than an audit only while each client has exactly one
    construction site — see the module docstring on why there are two clients
    and must not be a third.

    Takes `Settings` rather than reading `get_settings()`, because `config.py`
    is the only module in the server allowed to touch the environment and a
    service that reached for the global would be a second one.
    """
    return OllamaEmbeddingClient(
        base_url=settings.ollama_base_url,
        model=settings.embedding_model,
        timeout_seconds=settings.embedding_request_timeout_seconds,
    )
