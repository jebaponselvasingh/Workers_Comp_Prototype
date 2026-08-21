"""The typed shape of every insight — what the model may write, and what is stored.

Two families of model, and the split between them **is** AD-2.

- A `*Narrative` is what the language model is asked for, and it is passed to
  `with_structured_output(method="json_schema")` so Ollama's decoder is
  constrained to it. **Not one of these carries a money amount, a date, a
  count, a score or a verdict.** They are prose fields and lists of prose
  fields, which is the mechanical form of "the LLM supplies only the sentences
  around figures it cannot originate": a model that wanted to invent a dollar
  figure has nowhere in the grammar to put it.

- A `*Insight` is what lands in `ai_insight.content`. It carries the figures —
  copied verbatim from the tool envelopes in `agents/tools/`, which is to say
  from `services/financials`, `services/worklist`, `services/derivations` and
  `services/rag` — with the validated narrative nested inside it. `content`
  therefore stores a *validated structure* rather than a prose blob, which is
  what lets the tab render typed cards, what lets Story 7.1 aggregate the fraud
  kind portfolio-wide, and what makes the "figures equal service output" test
  possible to write at all.

`tests/test_ai_insights.py` asserts the first bullet structurally as well as by
example: every field of every `*Narrative` is a string or a list of strings.
A guard rather than a convention, because the tempting change — "let the model
fill in the reserve figure, it has it in the prompt anyway" — is one field
declaration, reads as a simplification, and is the whole failure mode Epic 6's
guarantees exist to prevent.

## Money travels as a value *and* its display string

`MoneyFigure` is `{cents, display}` and both halves are the server's. The
display string comes from `services/financials.format_dollars`, the same
function the reserve rationale is written with, so a sentence quoting `$48,000`
and the card row rendering `$48,000` are one rounding rather than two. See
`agents/envelope.py` on why this is the server's first money *display* envelope
and why it is scoped to insight content.

## camelCase on the wire, snake_case in the column

These models carry `api/schemas.py::ApiModel`'s alias generator rather than
inheriting from it, because `agents/` must not import `api/` — the dependency
runs the other way, and the composition root is allowed to reach in here for a
response type. The consequence is worth stating: `model_dump(mode="json")`
writes **snake_case** keys into JSONB (field names, no aliases), FastAPI
serialises **camelCase** out of the same class (aliases), and `populate_by_name`
is what lets a stored row be re-validated on the way back. One class, two
spellings, and neither is a translation anybody maintains.

`extra="forbid"` throughout, `ClaimFieldPatch`'s rule: an unknown key in a
model's answer is a schema rejection rather than a value silently dropped on
the way to the database.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator
from pydantic.alias_generators import to_camel

from data.models.enums import ActionKey, ActionUrgency
from services.financials import ReserveVerdict

#: The bounds every prose field carries. Long enough for two or three
#: sentences, short enough that a model which starts reciting cannot fill a
#: card — and, more usefully, short enough that the decoder's grammar refuses a
#: run-on before the timeout does. Stated once so the four kinds cannot drift
#: into four house styles.
PROSE_MIN = 20
PROSE_MAX = 700
#: A bullet is a clause, not a paragraph.
BULLET_MIN = 8
BULLET_MAX = 220


class InsightModel(BaseModel):
    """The shared base: camelCase aliases, populate-by-name, no extra keys."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class MoneyFigure(InsightModel):
    """One money amount: the integer cents, and the string a reader sees.

    Never one without the other. The cents are what Story 7.1 will aggregate
    and what the AD-2 test compares against the service's own field; the
    display string is what the narrative quotes and the card renders, so that
    neither the model nor the browser is ever the thing that decides how a
    figure looks.
    """

    cents: int
    display: str


# --- similar case outcomes ------------------------------------------------


class SimilarNeighbour(InsightModel):
    """One neighbouring claim, exactly as `services/rag.similar_claims` returned it.

    Field-for-field the service's `SimilarClaim`, including `distance` as raw
    cosine distance and the `embedded_at`/`stale` pair AD-12 requires retrieval
    to carry. Nothing is converted: a "92% similar" invented here would be a
    figure no service computed, sitting inside a narrative that quotes it.
    """

    claim_id: str
    employer_short_name: str
    injury_type: str
    severity_score: int
    distance: float
    embedded_at: datetime | None
    stale: bool


class SimilarCaseNarrative(InsightModel):
    """What the model writes about a neighbour list. Prose only."""

    summary: str = Field(min_length=PROSE_MIN, max_length=PROSE_MAX)
    takeaways: list[str] = Field(
        min_length=1,
        max_length=4,
        description="What the comparable claims suggest, one clause each.",
    )

    @model_validator(mode="after")
    def _bullets_are_clauses(self) -> "SimilarCaseNarrative":
        return _check_bullets(self, self.takeaways, "takeaways")


class SimilarCaseInsight(InsightModel):
    """The stored similar-case card: the neighbours, their freshness, the prose.

    `book_is_empty` is a server-side boolean rather than something the card
    works out from the list's length, for AD-1's standing reason — and here it
    also carries a meaning the length does not: a scoped handler with one claim
    in their partition and a handler between assignments both see zero
    neighbours, and the sentence the card shows is about the *search* having
    found none in this caller's book rather than about the list being short.

    `staleness_disclosure` is the sentence `agents/tools/similar.py` composed
    from `embedded_at`, the `stale` flags and the configured window (AD-12). It
    is `None` exactly when nothing is stale, so the card's disclosure row
    renders on a fact rather than on a threshold the browser applied.
    """

    neighbours: list[SimilarNeighbour]
    neighbour_count: int
    stale_count: int
    staleness_disclosure: str | None
    book_is_empty: bool
    narrative: SimilarCaseNarrative
    prompt_version: int


# --- reserve adequacy review ----------------------------------------------


class ReserveAdequacyNarrative(InsightModel):
    """What the model writes about a reserve verdict. Prose only."""

    summary: str = Field(min_length=PROSE_MIN, max_length=PROSE_MAX)
    considerations: list[str] = Field(
        min_length=1,
        max_length=4,
        description="What a handler should weigh next, one clause each.",
    )

    @model_validator(mode="after")
    def _bullets_are_clauses(self) -> "ReserveAdequacyNarrative":
        return _check_bullets(self, self.considerations, "considerations")


class ReserveAdequacyInsight(InsightModel):
    """The stored reserve card: the verdict, its exposure terms, the prose.

    `verdict` and `verdict_rationale` are both `services/financials`': the
    rationale is the deterministic sentence `_rationale` already writes for the
    card, carried here so the narrative has the service's own words to agree
    with rather than to paraphrase.

    ## `bills_on_file` false forbids a ratio, and the schema is where that is
    enforced

    `remaining_medical_cents is None` does not mean zero — it means the bills
    are not on file, which forces `ReserveVerdict.indeterminate` and leaves
    `ratio_bp` as `None`. A card that showed "115%" in that state would be
    quoting a comparison nobody made, from half the inputs, which is the exact
    failure `ReserveCheck`'s docstring records this console having shipped once.

    So the validator below refuses the combination outright: no bills on file,
    no ratio, no medical exposure figure, no projected total. It is a guard on a
    structure whose figures are copied from a service that already keeps the
    rule — which is the point. The service is the reason it holds today; the
    validator is what stops a later story "filling in" a `None` from a second
    source and discovering the problem on a handler's screen.
    """

    verdict: ReserveVerdict
    verdict_rationale: str
    ratio_bp: int | None
    reserve: MoneyFigure
    remaining_indemnity: MoneyFigure
    remaining_medical: MoneyFigure | None
    projected_remaining: MoneyFigure | None
    bills_on_file: bool
    bands_version: int
    narrative: ReserveAdequacyNarrative
    prompt_version: int

    @model_validator(mode="after")
    def _no_ratio_without_bills(self) -> "ReserveAdequacyInsight":
        if self.bills_on_file:
            return self
        absent = {
            "ratioBp": self.ratio_bp,
            "remainingMedical": self.remaining_medical,
            "projectedRemaining": self.projected_remaining,
        }
        offending = sorted(name for name, value in absent.items() if value is not None)
        if offending:
            raise ValueError(
                "this claim's bills are not on file, so its exposure is indeterminate and "
                f"{', '.join(offending)} cannot be stated"
            )
        return self


# --- next best actions ----------------------------------------------------


class NextActionFigure(InsightModel):
    """One checklist row, exactly as `services/worklist.claim_actions` ranked it.

    The row's identity, rule key, sentence and urgency — no `target`, no
    `enabled`, no `command`. Those three are about a *control* the checklist
    card renders, and an insight is not a place to press a button from; leaving
    them out is what stops the AI card growing a second, unaudited path into the
    three completion commands.
    """

    id: str
    key: ActionKey
    label: str
    urgency: ActionUrgency


class NextBestActionsNarrative(InsightModel):
    """What the model writes about a ranked checklist. Prose only."""

    summary: str = Field(min_length=PROSE_MIN, max_length=PROSE_MAX)
    considerations: list[str] = Field(
        min_length=1,
        max_length=4,
        description="Why these rows are the ones to work, one clause each.",
    )

    @model_validator(mode="after")
    def _bullets_are_clauses(self) -> "NextBestActionsNarrative":
        return _check_bullets(self, self.considerations, "considerations")


class NextBestActionsInsight(InsightModel):
    """The stored next-actions card: the server's list, in the server's order.

    `actions` is the checklist as generated — same rows, same order, same
    urgencies. The narrative explains it; it never re-ranks it, and there is
    nowhere in `NextBestActionsNarrative` to put a reordering even if a model
    wanted one.

    `cap` and `rules_version` ride along for `ClaimActionsResponse`'s reason:
    the length of the list is a rule document's answer, and "why are there six
    of these?" should be answerable from the card rather than reconstructed.
    """

    actions: list[NextActionFigure]
    action_count: int
    cap: int
    rules_version: int
    narrative: NextBestActionsNarrative
    prompt_version: int


# --- fraud risk indicators ------------------------------------------------


class FraudSignalFigures(InsightModel):
    """The claim's two fraud columns, both derived verdicts, and both thresholds.

    `agents/tools/fraud.py::FraudSignals` field for field. Both thresholds are
    published because they are two different rules — referral and review — and
    a card that quoted one score against the other's cut-off would be
    defensible at every step and wrong on the screen.
    """

    fraud_score: int
    fraud_flag: bool
    siu_review: bool
    fraud_flagged: bool
    siu_fraud_score_min: int
    fraud_flag_score_min: int
    thresholds_version: int


class FraudRedFlagsNarrative(InsightModel):
    """What the model writes when the derivations say there is something to look at.

    `red_flags` is `min_length=1`: this narrative is *only* requested when a
    registered derivation has already said the claim clears a threshold, so an
    empty list would be the model contradicting a service. The low-risk case is
    a different schema and a different prompt, not this one with nothing in it
    (AC 3).
    """

    summary: str = Field(min_length=PROSE_MIN, max_length=PROSE_MAX)
    red_flags: list[str] = Field(
        min_length=1,
        max_length=5,
        description="Each indicator worth reviewing, one clause each.",
    )

    @model_validator(mode="after")
    def _bullets_are_clauses(self) -> "FraudRedFlagsNarrative":
        return _check_bullets(self, self.red_flags, "redFlags")


class FraudLowRiskNarrative(InsightModel):
    """What the model writes when neither derivation fires.

    A *confirmation*, which is the whole of AC 3: a low-risk claim's card says
    that the score sits below both thresholds and that nothing needs referring,
    rather than rendering a heading over an empty list. `monitoring` is what to
    keep an eye on anyway — a short list, because "nothing is wrong" with five
    caveats is not a confirmation.
    """

    confirmation: str = Field(min_length=PROSE_MIN, max_length=PROSE_MAX)
    monitoring: list[str] = Field(
        min_length=1,
        max_length=3,
        description="What would change this assessment, one clause each.",
    )

    @model_validator(mode="after")
    def _bullets_are_clauses(self) -> "FraudLowRiskNarrative":
        return _check_bullets(self, self.monitoring, "monitoring")


class FraudRedFlagsInsight(InsightModel):
    """The stored fraud card, red-flag variant."""

    outcome: Literal["red_flags"] = "red_flags"
    signals: FraudSignalFigures
    narrative: FraudRedFlagsNarrative
    prompt_version: int


class FraudLowRiskInsight(InsightModel):
    """The stored fraud card, low-risk-confirmation variant."""

    outcome: Literal["low_risk"] = "low_risk"
    signals: FraudSignalFigures
    narrative: FraudLowRiskNarrative
    prompt_version: int


#: The fraud card's two shapes, discriminated on `outcome`.
#:
#: **The discriminator is a service's answer, never the model's.**
#: `agents/insights.py` reads `siu_review` and `fraud_flagged` off the tool
#: envelope, and *that* decides which narrative schema is requested and which
#: variant is stored. A model asked to choose its own outcome would be
#: originating a verdict, which is the one thing AD-2 rules out most plainly —
#: and the failure would be invisible, because a confidently written low-risk
#: confirmation on a referable claim reads exactly like a correct one.
FraudRiskInsight = Annotated[
    FraudRedFlagsInsight | FraudLowRiskInsight,
    Field(discriminator="outcome"),
]


def _check_bullets[T: BaseModel](model: T, bullets: list[str], field_name: str) -> T:
    """Every bullet is a clause: non-blank, and inside the shared bounds.

    A validator rather than `Field(min_length=…)` on the item type, because the
    useful failure names the field and the offending index — and because
    `list[str]`'s per-item constraints are invisible in the JSON Schema the
    decoder is constrained by, so this is a check that has to happen after the
    answer arrives whatever the grammar did.
    """
    for index, bullet in enumerate(bullets):
        stripped = bullet.strip()
        if not (BULLET_MIN <= len(stripped) <= BULLET_MAX):
            raise ValueError(
                f"{field_name}[{index}] must be between {BULLET_MIN} and {BULLET_MAX} "
                f"characters once trimmed; got {len(stripped)}"
            )
    return model


#: The stored fraud card's two shapes, re-validated coming *out* of JSONB.
#:
#: `TypeAdapter` rather than `Model.model_validate` because the fraud slot is a
#: **discriminated union** and not a class — `outcome` is what decides between the
#: red-flag and low-risk shapes on the way back out exactly as it decided between
#: them on the way in. Built once at import; the portfolio reader below runs it
#: over every insight row in a book.
_FRAUD_RISK = TypeAdapter[FraudRedFlagsInsight | FraudLowRiskInsight](FraudRiskInsight)


def read_fraud_clauses(content: Mapping[str, Any]) -> list[str] | None:
    """One stored fraud card's red-flag clauses, `[]`, or `None` for unreadable.

    **Three answers, and the difference between the last two is the whole point.**
    A `low_risk` card has no `red_flags` key at all — it is a different member of
    the stored union, written when two registered derivations said there was
    nothing to refer — so it answers `[]`: it *is* a fraud narrative and should
    raise a portfolio view's coverage figure, and it contributes no clause. A row
    this build cannot parse answers `None`: an older prompt version whose schema
    has since moved, which must be excluded and counted rather than counted as
    coverage.

    **Here rather than in `services/worklist/fraud.py`, and that is a layering
    rule rather than a preference.** `services/` may never import `agents/`
    (AD-5) — the direction is composition root → `agents/` → `services/` →
    `data/`, and `services/rag/insights.py` is built around it by taking an
    injected `InsightGenerator` rather than calling a model. The stored shape is
    this module's, so the *reader* of that shape is this module's too, and the
    analyst workspace's fold receives it as an argument the router supplies. That
    keeps exactly one `TypeAdapter` for this union in the build: a second one
    written inside `services/` would be a copy of a schema that changes here.

    Refusal is silent by construction: `ValidationError`'s message quotes the
    value that failed, and that value is model prose about a claim (AD-11), so it
    is neither logged nor chained onward. `api/routers/claims.py::_slot` makes the
    identical choice for the per-claim read and logs the claim id alone; the
    caller here does the same.
    """
    try:
        card = _FRAUD_RISK.validate_python(content)
    except ValidationError:
        return None
    if isinstance(card, FraudLowRiskInsight):
        return []
    return list(card.narrative.red_flags)


#: Every schema the model is ever asked to fill, for the structural guard in
#: `tests/test_ai_insights.py`. Listed rather than discovered by walking the
#: module, so a narrative added without being registered here fails the guard
#: that says narratives carry no figures — the failure mode of a discovered
#: list is that it silently covers whatever it happens to find.
NARRATIVE_SCHEMAS: tuple[type[InsightModel], ...] = (
    SimilarCaseNarrative,
    ReserveAdequacyNarrative,
    NextBestActionsNarrative,
    FraudRedFlagsNarrative,
    FraudLowRiskNarrative,
)

__all__ = [
    "BULLET_MAX",
    "BULLET_MIN",
    "NARRATIVE_SCHEMAS",
    "PROSE_MAX",
    "PROSE_MIN",
    "FraudLowRiskInsight",
    "FraudLowRiskNarrative",
    "FraudRedFlagsInsight",
    "FraudRedFlagsNarrative",
    "FraudRiskInsight",
    "FraudSignalFigures",
    "InsightModel",
    "MoneyFigure",
    "NextActionFigure",
    "NextBestActionsInsight",
    "NextBestActionsNarrative",
    "ReserveAdequacyInsight",
    "ReserveAdequacyNarrative",
    "SimilarCaseInsight",
    "SimilarCaseNarrative",
    "SimilarNeighbour",
    "read_fraud_clauses",
]
