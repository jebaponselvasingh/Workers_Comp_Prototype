"""The e2e model stub's schema walker, asserted against the real schemas.

`deploy/model-stub/app.py` is the deterministic Ollama stand-in the e2e profile
runs against. It lives outside `server/` so application code can never import it
— but nothing else in the build could tell whether it *works*, and that turned
out to matter twice: the Story 6.2 review found it could not follow a `$ref`
wrapped in `allOf` (M11), and the follow-up review found it answered no
discriminator for a tagged union, so the fraud card's stored shape could not be
synthesized at all (B2). Both failures are silent — a structured-parse refusal
with no clue as to why — and both would appear under e2e as empty cards.

Three halves, and they fail for different reasons:

1. **The constructs in isolation.** The `allOf` wrapper as a literal, because
   this build's Pydantic does not emit one and the branch is a guard against a
   future emitter; the sibling `$ref` form taken from the real
   `model_json_schema()`, because that *is* what this build emits.
2. **Every schema the stub is actually sent**, round-tripped: synthesize an
   instance from the real `model_json_schema()` and validate it back through the
   real model. That is the property the e2e suite depends on and the one nothing
   asserted — and it keeps working as the schemas change, which a hand-written
   fixture would not.
3. **The one union**, which is the only `const` in this build's schemas and so
   the only exercise that branch of the walker gets.

The module is loaded **by path**, `test_ai_insight_migration.py`'s device for
Alembic's script directory and for the same reason: `deploy/model-stub/` is not
a package and must not become importable from the server. A test is the
independent oracle and is allowed to reach for what it is asserting about; the
placement rule is about the shipped image.
"""

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field, TypeAdapter

from agents.schemas import (
    NARRATIVE_SCHEMAS,
    FraudRiskInsight,
    MoneyFigure,
)

STUB_PATH = Path(__file__).resolve().parents[2] / "deploy" / "model-stub" / "app.py"


def _load_stub() -> Any:
    spec = importlib.util.spec_from_file_location("_model_stub", STUB_PATH)
    assert spec and spec.loader, f"no model stub at {STUB_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stub = _load_stub()


class _Nested(BaseModel):
    """A model with a nested field carrying a description.

    Which is the whole fixture: a described nested model is a `$ref` with
    something beside it, and that is what a `Field(description=…)` produces.
    Every card model in `agents/schemas.py` documents its fields, so this shape
    is one field declaration away at all times.

    **What "beside it" looks like depends on the emitter**, and the two tests
    below take one form each — see `_resolve` in the stub. The pinned pydantic
    puts the description straight beside the `$ref`; older versions and other
    producers wrap it in a single-element `allOf`.
    """

    figure: MoneyFigure = Field(description="the money")


def test_the_walker_follows_an_allof_wrapped_ref() -> None:
    """The wrapper form, asserted as a literal — and it is a literal on purpose.

    `_resolve` looked only for a bare `$ref` once, so a wrapped one fell through
    every branch to the string default: the stub answered a nested *object* with
    a sentence, `with_structured_output` refused it, and the kind failed to
    generate. In the e2e profile that is every card of every claim.

    **The literal is the whole fixture, because this build does not emit this
    shape.** The second half of this test used to claim to assert "the same
    schema Pydantic actually emits" and contained no `allOf` at all — pydantic
    2.13.4 emits siblings — so it exercised the plain `$ref` branch while
    reading as coverage of the wrapper (follow-up review of Story 6.2, B3). The
    sibling form now has its own test below, and this one is honest about being
    a guard against a *future* emitter rather than the current one.

    Asserted on the synthesized value's shape rather than on its contents: the
    strings are hash-derived filler and no test in this build asserts on those.
    """
    schema = {
        "$defs": {
            "MoneyFigure": {
                "type": "object",
                "properties": {"cents": {"type": "integer"}, "display": {"type": "string"}},
                "required": ["cents", "display"],
            }
        },
        "type": "object",
        "properties": {
            "figure": {"allOf": [{"$ref": "#/$defs/MoneyFigure"}], "description": "the money"}
        },
        "required": ["figure"],
    }

    produced = stub.synthesize(schema, schema, "", "prompt")

    assert isinstance(produced, dict)
    assert isinstance(produced["figure"], dict), (
        "an allOf-wrapped $ref was answered with a scalar — every e2e generation "
        "against a nested narrative schema would fail its structured parse"
    )
    assert set(produced["figure"]) == {"cents", "display"}


def test_the_walker_follows_the_ref_with_siblings_this_build_actually_emits() -> None:
    """The shape the pinned Pydantic produces, taken from the pinned Pydantic.

    Not hand-written, which is the point: the emitted form is a property of a
    dependency, and a literal fixture would go on asserting the 2026 spelling
    long after a bump changed it. The assertion is that whatever comes out of
    `model_json_schema()` walks into something the model validates — which is
    exactly what the api asks of the stub under e2e.

    The `description` beside the `$ref` is asserted first, so that a Pydantic
    version that started wrapping would fail *here*, with a message naming the
    change, rather than in the round-trip below.
    """
    emitted = _Nested.model_json_schema()
    field = emitted["properties"]["figure"]

    assert "$ref" in field and "allOf" not in field, (
        "this Pydantic wraps a described $ref in allOf — the sibling form this "
        "test is about is no longer what the api sends the stub"
    )
    assert field["description"] == "the money"

    _Nested.model_validate(stub.synthesize(emitted, emitted, "", "prompt"))


@pytest.mark.parametrize("schema", NARRATIVE_SCHEMAS, ids=lambda s: s.__name__)
def test_every_narrative_schema_round_trips_through_the_stub(schema: type[BaseModel]) -> None:
    """What the e2e profile depends on, stated once: the stub can answer any kind.

    The api sends `model_json_schema()` as the request's `format`; the stub
    synthesizes an instance; LangChain validates it back into the model. If that
    fails for any kind, that kind never generates under e2e and the story's
    done-gate is unreachable — so it is asserted here, in the unit suite, where
    the failure names the schema instead of appearing as an empty card.
    """
    emitted = schema.model_json_schema()

    produced = stub.synthesize(emitted, emitted, "", "a prompt")
    schema.model_validate(produced)

    # Deterministic for one prompt, which is the reproducibility property AD-15
    # rests on — two runs of one commit must produce byte-identical cards.
    assert stub.synthesize(emitted, emitted, "", "a prompt") == produced


def test_the_fraud_discriminated_union_round_trips_too() -> None:
    """The one card whose stored shape is a union rather than a class.

    `agents/schemas.py` makes the fraud card a discriminated union on `outcome`,
    which renders as a `oneOf` over two branches plus a `const` per branch — the
    only `const` anywhere in this build's schemas, and so the only exercise the
    stub's `const` handling gets.

    **This test named the union and then tested `FraudLowRiskNarrative`**, which
    is a plain class with two prose fields, no `const`, no union, and its own
    entry in the parametrized round-trip above (follow-up review of Story 6.2,
    B2). It could not fail for the reason it gave, and the branch it claimed to
    cover had no coverage at all — which is how the actual gap survived: the
    discriminator carries a default, so Pydantic emits it with a `const` and
    leaves it out of `required`, and a walker that answered only the required
    properties produced an object the union refused with "Unable to extract
    tag". The stub now answers a `const` property whether or not it is required.

    The union is not in `NARRATIVE_SCHEMAS` — those are the *narrative* halves,
    the part a model is actually asked to write — so it is asserted on its own,
    and what it guards is the stub's ability to fill the shape rather than a
    request the e2e profile makes today.
    """
    adapter: TypeAdapter[Any] = TypeAdapter(FraudRiskInsight)
    emitted = adapter.json_schema()

    assert "oneOf" in emitted and "discriminator" in emitted, (
        "the fraud card stopped being a discriminated union — this test is about "
        "the construct, not about the card"
    )

    produced = stub.synthesize(emitted, emitted, "", "a prompt")

    assert isinstance(produced, dict)
    assert produced["outcome"] in ("red_flags", "low_risk"), (
        "the stub answered no discriminator — a tagged union cannot be narrowed "
        "without one, whatever the rest of the object says"
    )
    adapter.validate_python(produced)


# --- Story 6.3: multi-chunk streaming -----------------------------------


def test_the_content_chunker_loses_nothing_and_makes_no_empty_piece() -> None:
    """The property the whole stream rests on: the pieces reassemble exactly.

    A client concatenates the content of every `done: false` line to rebuild the
    answer, so a split that dropped or duplicated a character would corrupt every
    structured generation in the e2e profile — as an unexplainable parse
    failure, three layers from the cause.

    A text shorter than the chunk count yields fewer pieces than asked for, which
    is honest: there is nothing to put in the extra lines. What must never happen
    is an empty piece, because a frame carrying no content is a frame a client
    cannot tell from a keepalive.
    """
    for text in ("abcdefghij", "short", "a", "x" * 101, PLAIN_REPLY := stub.PLAIN_REPLY):
        for count in (1, 2, 3, 7):
            pieces = stub._chunks(text, count)
            assert "".join(pieces) == text
            assert all(pieces), f"an empty chunk for {text!r} at {count}"
            assert len(pieces) <= max(1, count)
    assert PLAIN_REPLY


def test_an_empty_answer_is_one_empty_piece_rather_than_none() -> None:
    """The degenerate case, pinned rather than left to `range()`.

    `_chunks("")` returning `[]` would emit a stream with no content line at all
    — legal, and indistinguishable from a model that said nothing, which is not
    a state this stub should be able to produce by accident.
    """
    assert stub._chunks("", 3) == [""]


def test_a_streamed_chat_is_n_partials_then_one_empty_terminal() -> None:
    """Ollama's real shape, which is the whole reason this was changed (6.3).

    Before this, `stream: true` emitted exactly one line carrying both the
    content and `done: true`. Legal — but it made "the stream renders" a
    one-frame assertion and left the one-terminal-event property asserted
    vacuously, because there was no run in which a second terminal *could* have
    appeared.

    Three things are asserted and each is a different failure: the count (the
    stub really does chunk), the terminal line's emptiness (a client
    concatenating content must not double the last piece), and that exactly one
    line says `done: true`.
    """
    from fastapi.testclient import TestClient

    with TestClient(stub.app) as client:
        response = client.post(
            "/api/chat",
            json={
                "model": "model-stub",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        lines = [json.loads(line) for line in response.text.splitlines() if line.strip()]

    assert len(lines) == stub.STUB_CHAT_CHUNKS + 1
    assert [line["done"] for line in lines] == [False] * stub.STUB_CHAT_CHUNKS + [True]
    assert lines[-1]["message"]["content"] == ""
    assert "".join(line["message"]["content"] for line in lines) == stub.PLAIN_REPLY


def test_a_non_streamed_chat_is_still_one_object() -> None:
    """The other call shape, unchanged — both exercise the real client."""
    from fastapi.testclient import TestClient

    with TestClient(stub.app) as client:
        body = client.post(
            "/api/chat",
            json={"model": "model-stub", "messages": [{"role": "user", "content": "hi"}]},
        ).json()

    assert body["done"] is True
    assert body["message"]["content"] == stub.PLAIN_REPLY


def test_a_structured_answer_survives_being_chunked() -> None:
    """A JSON document arriving in three pieces is what a real stream looks like.

    The `ollama` package reassembles the lines before
    `with_structured_output` ever sees them, so chunking exercises that
    reassembly rather than stepping around it. If the split were lossy, every
    insight generation in the e2e profile would break — and this is the assertion
    that would say so first.
    """
    from fastapi.testclient import TestClient

    schema = MoneyFigure.model_json_schema()
    with TestClient(stub.app) as client:
        response = client.post(
            "/api/chat",
            json={
                "model": "model-stub",
                "stream": True,
                "format": schema,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        lines = [json.loads(line) for line in response.text.splitlines() if line.strip()]

    reassembled = "".join(line["message"]["content"] for line in lines)
    MoneyFigure.model_validate(json.loads(reassembled))
