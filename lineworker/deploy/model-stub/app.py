"""A deterministic, Ollama-API-compatible model server for the e2e profile only.

AD-15 requires the e2e stack to be reproducible and to "never wait on wall-clock
cadence"; an LLM is the opposite of both. This container stands in for Ollama so
that every Epic 6 spec — this story's retrieval assertions, 6.2's insight cache,
6.3's stream events, 6.4's quick actions, 6.5's interrupt round trip — runs
against fixed bytes.

## What makes this honest rather than a fixture

**Nothing is swapped in the server.** The api container talks to this over HTTP
with the real `OllamaEmbeddingClient`, at the real `/api/embed` path, with the
real request body, and validates the response the real way. So an e2e run
exercises the shipped client, the shipped dimension check and the shipped
refresh command; only the model behind them is fake. A stub injected in Python
would have tested everything except the part that talks to the network.

**It lives under `deploy/` and not under `server/`.** It can therefore never be
imported by application code and never ship in the api image — the story's
Project Structure note asks for exactly that, and the placement is the
enforcement. It is also absent from `compose.yaml` and `compose.gpu.yaml`: dev
and prod run real Ollama, governed by AD-5, and this file has no way to reach
them.

## Why the vectors are hashes

`POST /api/embed` returns a unit vector derived from the sha256 of each input.
That gives the one property the specs need and nothing more:

- **Equal text always yields an equal vector**, so a similarity ordering is
  identical on every run, on every machine, in any order the specs execute in.
- **Different text yields a different vector**, so "the claim changed, therefore
  its embedding changed" is observable end to end.

What it deliberately does *not* give is semantic similarity: two claims a real
model would call neighbours are, here, as far apart as any other pair. No spec
asserts that a particular claim is similar to another, because that would be a
test of `bge-m3` rather than of this codebase. The assertions are structural —
scope, freshness, exclusion of the target, counts.

`tests/embedding_fixture.py` is the same construction in Python, for the unit
suite. Two implementations of one idea, on purpose: the unit suite needs no
HTTP, and the e2e suite needs the real client.

## The chat endpoint

`POST /api/chat` returns one fixed assistant message, and nothing in the build
calls it yet — `services/rag/client.py` speaks only to the embeddings endpoint
and Story 6.1 ships no chat client at all. It is here because the compose
service is this story's deliverable and 6.3 should extend a stub that already
answers the right shape rather than add the service and the route together in a
diff about a graph.
"""

import hashlib
import math
import os
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

#: bge-m3's width, which is what `vector(1024)` and the client's validation both
#: expect. Written out here rather than shared with the server, because this
#: container must be able to *disagree* with the server — a stub that imported
#: the constant could never be used to reproduce a dimension-mismatch failure.
#:
#: Settable for exactly that reason. The default is the width every current
#: spec wants, and a profile that sets `STUB_EMBEDDING_DIMENSIONS` to something
#: else gets a server that returns vectors the columns cannot hold — which is
#: how a later story exercises `EmbeddingDimensionMismatch` end to end instead
#: of only in a unit test with a fake client. A constant that could not be
#: moved made the paragraph above a claim the container could not honour.
EMBEDDING_DIMENSIONS = int(os.environ.get("STUB_EMBEDDING_DIMENSIONS", "1024"))

app = FastAPI(title="LINEWORKER model-stub", docs_url=None, redoc_url=None)


def deterministic_vector(text: str) -> list[float]:
    """A unit vector that depends only on `text`.

    sha256 gives 32 bytes and the vector wants `EMBEDDING_DIMENSIONS` floats, so
    the digest is
    re-hashed with a counter until there are enough. Each byte becomes a float
    in [-1, 1] and the result is L2-normalised — which puts every vector on the
    unit sphere, where cosine distance is well behaved and two identical inputs
    are at distance 0 rather than a float epsilon away from it.
    """
    raw = bytearray()
    counter = 0
    while len(raw) < EMBEDDING_DIMENSIONS:
        raw.extend(hashlib.sha256(f"{counter}:{text}".encode()).digest())
        counter += 1

    values = [(byte / 127.5) - 1.0 for byte in raw[:EMBEDDING_DIMENSIONS]]
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


class EmbedRequest(BaseModel):
    """Ollama's `/api/embed` body: a model name and one string or a list.

    Both input shapes are accepted because the endpoint accepts both and a stub
    that only handled the list form would pass today and fail the first time a
    caller embedded a single query — which is exactly what
    `similar_claims` does.
    """

    model: str
    input: str | list[str]


@app.get("/api/version")
def version() -> dict[str, str]:
    """Ollama's version probe — what the compose healthcheck polls."""
    return {"version": "0.0.0-model-stub"}


@app.get("/api/tags")
def tags() -> dict[str, Any]:
    """The pulled-model list. Answers "yes, whatever you asked for is here".

    Real Ollama would list what has been pulled; this container has no models
    and no disk to keep them on, so it reports a single synthetic entry. Present
    because a caller checking readiness by model name is a reasonable thing for
    a later story to do, and discovering then that the stub 404s would be a
    puzzle rather than a decision.
    """
    return {"models": [{"name": "model-stub", "model": "model-stub"}]}


@app.post("/api/embed")
def embed(request: EmbedRequest) -> dict[str, Any]:
    """One vector per input, in order, derived from the input's hash."""
    texts = [request.input] if isinstance(request.input, str) else request.input
    return {
        "model": request.model,
        "embeddings": [deterministic_vector(text) for text in texts],
    }


@app.post("/api/chat")
def chat(body: dict[str, Any]) -> dict[str, Any]:
    """A fixed, non-streaming assistant turn. No caller in this story.

    Deliberately bland and deliberately constant: Story 6.3's specs assert
    *event structure* — that a run terminates with exactly one terminal event,
    that an interrupt carries the pending tool call — and never model prose. A
    stub that produced varied text would invite a spec to assert on it.
    """
    return {
        "model": body.get("model", "model-stub"),
        "message": {
            "role": "assistant",
            "content": "model-stub response: deterministic text for the e2e profile.",
        },
        "done": True,
        "done_reason": "stop",
    }
