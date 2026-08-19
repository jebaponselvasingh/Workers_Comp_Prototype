"""The e2e model stub's schema walker, asserted against the real schemas.

`deploy/model-stub/app.py` is the deterministic Ollama stand-in the e2e profile
runs against. It lives outside `server/` so application code can never import it
— but nothing else in the build could tell whether it *works*, and that turned
out to matter: the Story 6.2 review found that it did not handle the `allOf`
wrapper Pydantic emits for a nested model with a description, so the first
narrative schema to grow one would have broken every e2e generation at once,
silently, with a structured-parse failure and no clue as to why (M11).

Two halves, and they fail for different reasons:

1. **The construct in isolation.** `allOf` around a `$ref`, which is the shape
   that was unhandled. Written as a literal schema so the assertion says what it
   is about rather than depending on which of `agents/schemas.py`'s fields
   happens to carry a description today.
2. **Every schema the stub is actually sent**, round-tripped: synthesize an
   instance from the real `model_json_schema()` and validate it back through the
   real model. That is the property the e2e suite depends on and the one nothing
   asserted — and it keeps working as the schemas change, which a hand-written
   fixture would not.

The module is loaded **by path**, `test_ai_insight_migration.py`'s device for
Alembic's script directory and for the same reason: `deploy/model-stub/` is not
a package and must not become importable from the server. A test is the
independent oracle and is allowed to reach for what it is asserting about; the
placement rule is about the shipped image.
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter

from agents.schemas import (
    NARRATIVE_SCHEMAS,
    FraudLowRiskNarrative,
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

    Which is the whole fixture: Pydantic wraps a `$ref` in a single-element
    `allOf` whenever the field has anything of its own beside it, and that is
    what a `Field(description=…)` on a nested model produces. Every card model
    in `agents/schemas.py` documents its fields, so this shape is one field
    declaration away at all times.
    """

    figure: MoneyFigure


def test_the_walker_follows_an_allof_wrapped_ref() -> None:
    """The construct that was unhandled, asserted as a literal.

    Before the fix `_resolve` looked only for a bare `$ref`, so a wrapped one
    fell through every branch to the string default — the stub answered a nested
    *object* with a sentence, `with_structured_output` refused it, and the kind
    failed to generate. In the e2e profile that is every card of every claim.

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
    # And the same schema Pydantic actually emits, so this does not depend on a
    # hand-written approximation of the wrapper staying accurate.
    emitted = _Nested.model_json_schema()
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
    which renders as a `const` in each branch's schema — so a stub that ignored
    `const` would answer a discriminator the union cannot narrow, and the failure
    would be confined to the one kind AC 3 is about. The union is not in
    `NARRATIVE_SCHEMAS` (those are the *narrative* halves), so it is asserted on
    its own.
    """
    emitted = FraudLowRiskNarrative.model_json_schema()
    adapter = TypeAdapter(FraudLowRiskNarrative)

    adapter.validate_python(stub.synthesize(emitted, emitted, "", "a prompt"))
