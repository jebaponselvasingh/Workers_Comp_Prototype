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

## The chat endpoint, and why it honours `format`

`POST /api/chat` gained its first caller in Story 6.2: insight generation asks
for structured output, which `ChatOllama.with_structured_output(method=
"json_schema")` implements by sending the Pydantic model's **JSON Schema** as
the request's `format` field. Real Ollama constrains decoding to that grammar.

A stub that ignored `format` and answered a prose sentence would fail every
structured parse in the e2e profile, so no insight could ever be generated
there and the whole of Story 6.2's done-gate would be unreachable. So this
container synthesizes a **deterministic instance of the supplied schema** —
walking `properties`, `required`, `type`, `enum`, `const`, `anyOf`, `allOf`,
`items`, `$ref`/`$defs` and the length bounds — and returns it JSON-serialised in
`message.content`, exactly where a real model would put it.

Deterministic in the same sense the vectors are: every scalar is derived from
the hash of its own path through the schema plus the prompt, so two runs of one
commit produce byte-identical cards and a spec can assert that a card rendered
without asserting on prose. It is not *plausible* text and is not meant to be —
the assertions in `e2e/stories/6-2-…spec.ts` are structural (four cards, four
timestamps, a model label), which is the only kind of assertion AD-15 allows
about a model's output anyway.

**Streaming is supported because the real client streams.** `ChatOllama` sends
`stream: true` by default and the `ollama` package reads an NDJSON body, so the
stub answers NDJSON when asked and a single JSON object otherwise. Handling
both is what keeps "nothing is swapped in Python" true of the chat path as well
as the embeddings one.
"""

import hashlib
import json
import math
import os
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
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


#: What an unconstrained chat request answers. Deliberately bland and
#: deliberately constant: Story 6.3's specs assert *event structure* — that a
#: run terminates with exactly one terminal event, that an interrupt carries
#: the pending tool call — and never model prose. A stub that produced varied
#: text would invite a spec to assert on it.
PLAIN_REPLY = "model-stub response: deterministic text for the e2e profile."

#: How long a synthesized string is when the schema does not say. Long enough
#: to clear the prose minimums Story 6.2's narrative schemas declare (20
#: characters for a summary, 8 for a bullet) without any knowledge of them —
#: the stub must not import the server's constants, so it satisfies them by
#: being comfortably above the largest.
DEFAULT_STRING_LENGTH = 48

#: Bounds on synthesized numbers. Small positive integers: every numeric field
#: in a schema this stub is asked to fill is a count or a score, and a negative
#: or enormous value would fail a plausibility check somebody adds later for
#: reasons unrelated to the stub.
MIN_NUMBER = 1
MAX_NUMBER = 9


def _digest(path: str, prompt: str) -> int:
    """A stable integer for one position in one request.

    The schema path *and* the prompt, so two different claims produce two
    different narratives (a spec asserting that four cards rendered would
    otherwise pass against four identical ones) while one claim produces the
    same narrative on every run of one commit — the reproducibility property
    AD-15 rests on.
    """
    return int.from_bytes(hashlib.sha256(f"{path}|{prompt}".encode()).digest()[:8], "big")


def _words(path: str, prompt: str, length: int) -> str:
    """Deterministic filler of at least `length` characters.

    Readable rather than random hex, because these strings land in an e2e
    browser and somebody will screenshot one. It says what it is: nobody should
    ever mistake stub output for a model's.
    """
    tail = hashlib.sha256(f"{path}|{prompt}".encode()).hexdigest()
    text = f"model-stub narrative for {path.strip('.') or 'root'} {tail}"
    while len(text) < length:
        text = f"{text} {tail}"
    return text[:length] if len(text) > length else text


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """Follow a local `$ref` into `$defs`, through an `allOf` wrapper if there is one.

    A nested model is a `$ref`, and a *described* nested model is a `$ref` with
    something beside it. Which shape that "something beside it" takes is a
    property of the emitter, and there are two in circulation:

        {"$ref": "#/$defs/MoneyFigure", "description": "…"}      ← siblings
        {"allOf": [{"$ref": "#/$defs/MoneyFigure"}], "…": "…"}   ← wrapper

    **This build emits the first.** `uv.lock` pins pydantic 2.13.4, which puts
    `description` and `title` straight beside the `$ref` under draft 2020-12,
    where siblings are legal. The sibling form has always worked here — the
    `$ref` lookup below simply ignores the neighbours — so the failure this
    branch was added for (review of Story 6.2, M11) is not one the pinned
    version can produce, and the docstring said otherwise until the follow-up
    review checked (B3).

    The `allOf` branch stays anyway, and deliberately: older Pydantic, and any
    other JSON-Schema producer this stub is ever pointed at, wrap rather than
    nest, and the cost of the branch is four lines against a failure mode that
    is silent (the stub answers a nested object with a sentence, the api's
    `with_structured_output` refuses it, and *every* e2e generation breaks with
    no clue as to why). A dependency bump is exactly the kind of change nobody
    would think to re-test this against.

    Only the *first* branch of an `allOf` is followed. A real intersection of
    two object schemas is not something Pydantic emits for the models in
    `server/agents/schemas.py`, and a stub that tried to merge constraints would
    be reimplementing a validator; taking branch one keeps the walk total and
    keeps a wrong answer loud (the api validates what comes back) rather than
    subtly plausible.
    """
    branches = schema.get("allOf")
    if isinstance(branches, list) and branches and isinstance(branches[0], dict):
        merged = {key: value for key, value in schema.items() if key != "allOf"}
        return _resolve({**merged, **branches[0]}, root)
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
        return schema
    resolved = root.get("$defs", {}).get(ref.removeprefix("#/$defs/"))
    return resolved if isinstance(resolved, dict) else {}


def synthesize(schema: dict[str, Any], root: dict[str, Any], path: str, prompt: str) -> Any:
    """One deterministic instance of `schema`.

    Handles the constructs Pydantic's `model_json_schema()` actually emits for
    the models in `server/agents/schemas.py`: objects with `properties` and
    `required`, arrays with `items` and `minItems`/`maxItems`, strings with
    `minLength`/`maxLength`, integers, numbers, booleans, `enum`, `const`,
    `anyOf` (which is how `X | None` renders), `allOf` (which is how a `$ref`
    with a description renders) and `$ref` into `$defs`.

    Unknown or empty schemas answer a string, which is the least surprising
    thing a JSON value can be and keeps the walk total: a stub that raised on a
    construct it had not seen would turn a schema change into an e2e failure
    with no useful message.
    """
    schema = _resolve(schema, root)

    if "const" in schema:
        return schema["const"]

    choices = schema.get("enum")
    if isinstance(choices, list) and choices:
        return choices[_digest(path, prompt) % len(choices)]

    variants = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(variants, list) and variants:
        # The first non-null branch, deterministically: `X | None` renders as
        # `[{...}, {"type": "null"}]`, and an optional field answered with null
        # every time would leave half of Story 6.2's content untested in e2e.
        for variant in variants:
            if _resolve(variant, root).get("type") != "null":
                return synthesize(variant, root, path, prompt)
        return None

    kind = schema.get("type")

    if kind == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        required = schema.get("required", list(properties))
        return {
            name: synthesize(properties[name], root, f"{path}.{name}", prompt)
            for name in properties
            # Required, **or a discriminator.** A tagged union's tag field
            # carries a default in Pydantic, so it is emitted with a `const` and
            # left out of `required` — and an instance without it cannot be
            # narrowed to a branch at all ("Unable to extract tag"). Answering
            # only the required properties therefore produced an object no
            # discriminated union could validate, which is a shape this stub is
            # otherwise perfectly able to fill: the `const` says exactly what the
            # value has to be.
            if name in required or "const" in _resolve(properties[name], root)
        }

    if kind == "array":
        low = int(schema.get("minItems", 1)) or 1
        high = int(schema.get("maxItems", low)) or low
        count = max(1, min(low, high))
        items = schema.get("items", {})
        return [
            synthesize(items, root, f"{path}[{index}]", prompt) for index in range(count)
        ]

    if kind == "integer":
        low = int(schema.get("minimum", MIN_NUMBER))
        high = int(schema.get("maximum", MAX_NUMBER))
        span = max(1, high - low + 1)
        return low + _digest(path, prompt) % span

    if kind == "number":
        return float(MIN_NUMBER + _digest(path, prompt) % MAX_NUMBER)

    if kind == "boolean":
        return bool(_digest(path, prompt) % 2)

    if kind == "null":
        return None

    minimum = int(schema.get("minLength", 0))
    maximum = int(schema.get("maxLength", max(minimum, DEFAULT_STRING_LENGTH)))
    length = max(minimum, min(DEFAULT_STRING_LENGTH, maximum))
    return _words(path, prompt, length)


def _content(body: dict[str, Any]) -> str:
    """What the assistant "said": a schema instance, or the fixed sentence.

    `format` is Ollama's structured-output field. A dict is a JSON Schema and
    is honoured; the string `"json"` (Ollama's older JSON mode) has no schema to
    walk, so it answers a JSON object rather than prose; anything else is an
    unconstrained request.
    """
    prompt = json.dumps(body.get("messages", []), sort_keys=True)
    fmt = body.get("format")
    if isinstance(fmt, dict) and fmt:
        return json.dumps(synthesize(fmt, fmt, "", prompt))
    if fmt == "json":
        return json.dumps({"reply": PLAIN_REPLY})
    return PLAIN_REPLY


def _envelope(body: dict[str, Any], content: str) -> dict[str, Any]:
    """One Ollama chat response object, done in a single turn."""
    return {
        "model": body.get("model", "model-stub"),
        "created_at": "2026-01-01T00:00:00Z",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
    }


@app.post("/api/chat")
def chat(body: dict[str, Any]) -> Any:
    """A deterministic assistant turn, honouring `format` and `stream`.

    **Streaming is answered when it is asked for**, because the shipped client
    asks for it: `ChatOllama` sends `stream: true` unless told otherwise, and
    the `ollama` package then reads an NDJSON body line by line. One line
    carrying both the content and `done: true` is a legal stream and is what a
    single-turn stub has to say; `done_reason` is `stop` rather than `load`,
    which the client skips.

    Non-streaming requests get the same object as a plain JSON body, so both
    call shapes exercise the real client against the real wire format.
    """
    content = _content(body)
    envelope = _envelope(body, content)
    if not body.get("stream", False):
        return envelope

    def lines() -> Iterator[bytes]:
        yield (json.dumps(envelope) + "\n").encode()

    return StreamingResponse(lines(), media_type="application/x-ndjson")
