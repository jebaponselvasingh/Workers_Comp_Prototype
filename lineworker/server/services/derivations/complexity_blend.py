"""`handler_complexity` — how hard a handler's book is to carry (Story 5.2, AD-10).

The prototype's one-line blend (`renderSV`, line 1022):

    complexity = min(100, round(avgSev + 10 * (surgCt / n) + 15 * (litCt / n)))

Three facts about a caseload — how severe the average claim is, how much of the
book needed surgery, how much of it is in litigation — folded into one 0-100
grade and banded Low / Medium / High. Ported here as a registered derivation
because AC 3 asks for exactly one computing function for it, and because the
alternative is what the prototype does: the arithmetic sitting inside the
function that draws the table row, where Epic 7's fraud workspace would write it
again with a different weight and neither would be wrong on its own.

**Named after the rule, not after the value it exports.** `complexity_blend`
rather than `handler_complexity`, for the package's naming rule: re-exporting a
name that matches its own module rebinds the package attribute, and
`services.derivations.handler_complexity.ComplexityBand` would then raise
`AttributeError` with no obvious cause.
`test_no_derivation_module_is_named_after_the_value_it_exports` asserts it.

## Why the parameters arrive at `.of()` rather than through `build`

`Derivation.build` is typed `Callable[[DerivationThresholds], T]`, and these
tunables live in the `handler_performance` document instead — see
`rules/parameters.HandlerPerformance` for the argument. So the builder ignores
its thresholds and the block is handed to `of()`, which is
`batch_calendar.NextBatchDateDerivation.of(as_of, weekdays)`'s arrangement and
keeps "where did this weight come from?" answerable at the one call site.

## The blend is basis points over three 0-100 inputs, and that is not cosmetic

`HandlerMix` carries integer *counts*, not pre-divided rates, and the score is
computed as a single quotient. Averaging three floats and adding them lets the
answer depend on the order a loop happened to accumulate them; here the
numerator and the denominator are both exact integers and the only rounding is
the last step, half-up, onto the whole number the chip displays. That matters
because `complexityHighMin` is an inclusive boundary: a book that blends to
exactly the cut-off must grade High every time, not on four runs in five.

Reading the expression back: with `severityWeightBp = 10000` the severity term
is the plain average severity; with `surgeryRateWeightBp = 1000` an all-surgical
book adds ten; with `litigationRateWeightBp = 1500` an entirely litigated book
adds fifteen. Those are the prototype's three coefficients exactly.

## The cap is part of the rule, not a safety net

`complexityScoreMax` exists because the blend can exceed the scale it is read
against: a book averaging 95 severity that is entirely surgical and entirely
litigated blends to 120. The prototype clamps, and so does this — a chip reading
"High (120)" beside a scale the rest of the console treats as 0-100 would be a
number nobody can interpret, and the band it lands in is unchanged either way.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Final

from rules.parameters import HandlerPerformance
from services.derivations.registry import Derivation, register

#: One whole unit, in basis points. The weights are expressed against this, so
#: `severityWeightBp = 10000` means "one times the average severity".
BASIS_POINTS_PER_UNIT: Final[int] = 10_000

#: The scale the two *rate* inputs are expressed on. A rate is a share of the
#: book, and the blend reads it as a percentage rather than as a fraction so
#: that all three inputs run 0-100 and their weights are comparable at a
#: glance — which is the whole reason the document can state them as three
#: basis-point numbers side by side.
PERCENT: Final[int] = 100


class ComplexityBand(StrEnum):
    """Snake_case values per the enum convention — the UI owns the labels.

    `med` rather than `medium`, matching `RiskBand`: the two are different rules
    over different subjects (see `HandlerPerformance`), but a console that spelt
    one band two ways would invite exactly the confusion this docstring exists
    to prevent.
    """

    low = "low"
    med = "med"
    high = "high"


@dataclass(frozen=True)
class HandlerMix:
    """One handler's book, reduced to the four integers the blend needs.

    Counts rather than rates, for the module docstring's reason: the division
    happens once, inside the derivation, so the caller cannot round on the way
    in. `severity_total` is the sum of the book's severity scores — the average
    is `severity_total / case_count` and is deliberately not precomputed here.

    Validated on construction because every one of these is a fact about a set
    of claims, and a mix that cannot describe one would produce a perfectly
    plausible grade: `surgery_count > case_count` is a surgery rate above 100%,
    which inflates the term silently and bands a light book High.
    """

    case_count: int
    severity_total: int
    surgery_count: int
    litigation_count: int

    def __post_init__(self) -> None:
        # A handler with no claims is not a row on this table — handlers are
        # derived from the scoped claim set, so a zero-claim mix can only be a
        # caller error, and the alternative to refusing it is a
        # `ZeroDivisionError` from inside the derivation.
        if self.case_count < 1:
            raise ValueError(f"a handler mix needs at least one claim, got {self.case_count}")
        if self.severity_total < 0:
            raise ValueError(f"severity_total must not be negative, got {self.severity_total}")
        for name, count in (
            ("surgery_count", self.surgery_count),
            ("litigation_count", self.litigation_count),
        ):
            if not 0 <= count <= self.case_count:
                raise ValueError(
                    f"{name} must be between 0 and case_count ({self.case_count}), got {count}"
                )


@dataclass(frozen=True)
class HandlerComplexity:
    """The grade and the number behind it — `CoordinationResult`'s shape.

    Both travel together because the chip renders both ("High (71)"), and
    publishing only the band would leave the SPA with a label it cannot explain
    while publishing only the score would put the banding in the browser.
    """

    score: int
    band: ComplexityBand


@dataclass(frozen=True)
class HandlerComplexityDerivation:
    """The blend, then the bands — most severe first, as `risk` reads them."""

    def of(self, mix: HandlerMix, params: HandlerPerformance) -> HandlerComplexity:
        """Grade one handler's book under one version of the rule document.

        Both arguments are required and neither is stored on the instance: the
        derivation is built once per process by the registry and called once per
        handler per request, so the parameters have to arrive per call for the
        same reason the mix does.
        """
        weighted = (
            params.severity_weight_bp * mix.severity_total
            + params.surgery_rate_weight_bp * mix.surgery_count * PERCENT
            + params.litigation_rate_weight_bp * mix.litigation_count * PERCENT
        )
        # Exact rational division quantized once, half-up, onto the whole
        # number the chip shows — `sla._rounded`'s convention and its reason.
        # `round()` here would be banker's rounding, which grades a book that
        # blends to 64.5 as 64 and one that blends to 65.5 as 66; a scale that
        # rounds one boundary down and the next one up is a defect report
        # waiting to be filed.
        blended = int(
            (Decimal(weighted) / Decimal(mix.case_count * BASIS_POINTS_PER_UNIT)).quantize(
                Decimal(1), rounding=ROUND_HALF_UP
            )
        )
        score = min(params.complexity_score_max, blended)
        return HandlerComplexity(score=score, band=self._band(score, params))

    @staticmethod
    def _band(score: int, params: HandlerPerformance) -> ComplexityBand:
        """High first, then Medium, then Low.

        The order is the rule, exactly as it is in `RiskDerivation`: the two
        cut-offs are validated as `med_min < high_min` when the document is read
        — strictly, so an equal pair that would delete the `med` band is refused
        there rather than silently honoured here — so this reads them in order
        and does not re-check an ordering it could not fix.
        """
        if score >= params.complexity_high_min:
            return ComplexityBand.high
        if score >= params.complexity_med_min:
            return ComplexityBand.med
        return ComplexityBand.low


handler_complexity = register(
    Derivation(
        name="handler_complexity",
        describes=(
            "a handler's caseload complexity grade — average severity blended with "
            "the surgery and litigation rates of their in-scope book, capped and banded "
            "by the handler_performance document"
        ),
        build=lambda _thresholds: HandlerComplexityDerivation(),
    )
)
