"""A deterministic `EmbeddingClient` for tests, and why the suite has one.

`services/rag/client.py` defines `EmbeddingClient` as a `Protocol` precisely so
that everything above it — the composer, the refresh ordering, the batch bound,
the scope enforcement, the staleness round trip — can be asserted without a
model server, a GPU, a container or a network. This is that fake.

**Its vectors are derived from a hash of the input**, which gives the one
property the assertions need: equal text always produces an equal vector, and
different text produces a different one. That makes similarity ordering stable
across runs and across machines. It does *not* make it semantically meaningful
— two claims that a real model would rank as neighbours are, here, as far apart
as any other pair — and no test in this suite asserts that a particular claim is
"similar" to another, because that would be a test of `bge-m3` rather than of
this codebase.

It mirrors `deploy/model-stub/app.py`, which serves the same construction over
HTTP for the e2e profile. The two are separate implementations of one idea on
purpose: the stub exists so the *real* `OllamaEmbeddingClient` is the code under
test end to end, and this exists so the unit suite needs no HTTP at all.
"""

import hashlib
import math
from collections.abc import Sequence

from services.rag.client import EMBEDDING_DIMENSIONS


def deterministic_vector(text: str, *, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    """A unit vector that depends only on `text`.

    sha256 gives 32 bytes; the vector wants 1024 floats, so the digest is
    re-hashed with a counter until there are enough bytes. Each byte becomes a
    float in [-1, 1] and the result is L2-normalised, which puts every vector on
    the unit sphere — where cosine distance is a well-behaved metric and two
    identical inputs are at distance 0 rather than at some float epsilon away
    from it.
    """
    raw = bytearray()
    counter = 0
    while len(raw) < dimensions:
        raw.extend(hashlib.sha256(f"{counter}:{text}".encode()).digest())
        counter += 1

    values = [(byte / 127.5) - 1.0 for byte in raw[:dimensions]]
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


class FakeEmbeddingClient:
    """`EmbeddingClient` with no I/O. Records what it was asked to embed.

    `calls` holds one entry per `embed()` invocation, each the list of inputs it
    received. That is what lets a test assert the *batch bound* — "exactly
    twenty-five texts reached the model on this run" — which is a property of
    how the command batches rather than of how many rows it wrote, and the two
    can disagree.

    `model` is present because `services/rag/embeddings.py` reads it off the
    client to write `claim_embedding.model`. A fake that omitted it would still
    work (the command falls back to `"unknown"`), and then the provenance
    column would be untested.
    """

    model = "fake-embed"

    def __init__(self, *, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self._dimensions = dimensions
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [deterministic_vector(text, dimensions=self._dimensions) for text in texts]

    @property
    def embedded_count(self) -> int:
        """How many texts reached the client across every call."""
        return sum(len(call) for call in self.calls)


class UnreachableEmbeddingClient:
    """An `EmbeddingClient` that always fails, for the degradation path.

    Raises a plain `ConnectionError` rather than an `httpx` exception, and that
    is the assertion rather than a shortcut: `refresh_stale_embeddings` must
    survive *any* failure from the client, not a list of transport types it was
    written against. A version that caught `httpx.HTTPError` specifically would
    let a JSON decode error take the scheduler's tick down.
    """

    model = "unreachable"

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        raise ConnectionError(f"no model server (asked for {len(texts)} vectors)")
