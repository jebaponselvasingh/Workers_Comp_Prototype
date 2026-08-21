"""`fraud_band` — a banding of `claim.fraud_score`, and the *third* fraud rule.

`risk_band.py` for severity, one column over. What makes this module worth
reading rather than skimming is that the registry now holds three rules over
one column pair, two of which already look alike enough that
`queue_flags.FraudFlaggedDerivation` spends a docstring keeping them apart:

    siu_review     = fraud_flag and fraud_score >= siuFraudScoreMin      (60)
    fraud_flagged  = fraud_flag and fraud_score >= fraudFlagScoreMin     (55)
    fraud_band     = band(fraud_score) against fraudBandMedMin/HighMin   (35/55)

**This one has no `fraud_flag` conjunct, and that is the whole difference.**
The first two are *populations* gated on a triage flag: a claim nobody flagged
is not in either of them however high it scored. This is a reading of the score
column on its own, so a claim nobody flagged still lands in a band — which is
precisely what a distribution over the portfolio has to show, because "how many
claims score high and were never flagged" is the question a fraud analyst opens
the screen to ask, and a banding that inherited the flag could not express it.

**`fraudBandHighMin` and `fraudFlagScoreMin` both read 55 today, and they are
still two parameters.** That coincidence is the trap this module exists to keep
out of: collapsing them would make the band chart's `high` segment *definitionally*
the Fraud Flags card's population, and the two would then move together for ever
— so the day an operator widened the review threshold to 50, the analyst's
distribution would silently re-band the whole portfolio with it. `queue_flags.py`
records the same argument for the pair it owns; this is that argument extended to
a third rule, which is the point at which it stops being a coincidence and starts
being a convention.

**No `sql_band()` twin, unlike `RiskDerivation`.** That function exists because
an aggregate counts risk bands in SQL; every consumer of this one folds a
projection in Python (`services/worklist/fraud.py`), so a SQL spelling would be a
second encoding of the rule with no caller — the duplication `sql_is`'s docstring
argues is only worth paying when something actually asks for it.

**Why this module is not called `fraud_band.py`.** The package's naming rule
(`__init__.py`, code review 2026-08-10) is that a module is named after the
*rule* and never after the derived value it exports, because
`from services.derivations.fraud_band import fraud_band` silently rebinds the
package attribute from the submodule to the `Derivation` instance —
`services.derivations.fraud_band.FraudBand` would then raise `AttributeError`
with no obvious cause, and `test_no_derivation_module_is_named_after_the_value_
it_exports` fails the build over it. `risk_band.py` exporting `risk` is the
precedent; here the value is `fraud_band`, so the module says what the rule
bands: the fraud *score*.
"""

from dataclasses import dataclass
from enum import StrEnum

from services.derivations.registry import Derivation, register


class FraudBand(StrEnum):
    """Snake/lowercase values per the enum convention — the UI owns labels.

    **Declared low → high**, which is the opposite of `RiskBand`'s high → med →
    low, and the difference is deliberate rather than an oversight. `RiskBand`'s
    order is a legend's reading order for a donut whose loudest slice is read
    first; this vocabulary is drawn as a *distribution over a score*, where the
    axis runs from the least suspicious claim to the most and a reader scans it
    the way they would a histogram. `services/worklist/fraud.py` emits the bands
    in this declaration order, so the order on screen is the enum's.

    `medium` spelled out rather than `RiskBand`'s `med`: nothing here has to
    match that enum's wire values (they band different columns and no payload
    carries both), and an abbreviation is only worth its ambiguity when
    something already spells it that way.
    """

    low = "low"
    medium = "medium"
    high = "high"


@dataclass(frozen=True)
class FraudBandDerivation:
    """Bands `fraud_score`: high >= `high_min`, medium >= `med_min`, else low.

    `RiskDerivation`'s shape over a different column, and its thresholds arrive
    the same way — from the versioned `derivation_thresholds` JDM document
    (AD-8: parameters are data, formulas are Python). They are validated as
    `med_min < high_min` in `rules/parameters.py`, so this class does not
    re-check an ordering it cannot fix.

    `fraud_score` is keyword-only on `of()`, matching `SiuReviewDerivation.of`
    and `FraudFlaggedDerivation.of` rather than `RiskDerivation.of`'s positional
    severity. The three fraud rules are the ones most likely to be called side
    by side in one fold, and a positional `int` there is one transposition away
    from banding a severity score as a fraud score — which would type-check,
    run, and produce a plausible distribution of the wrong column.
    """

    high_min: int
    med_min: int

    def of(self, *, fraud_score: int) -> FraudBand:
        if fraud_score >= self.high_min:
            return FraudBand.high
        if fraud_score >= self.med_min:
            return FraudBand.medium
        return FraudBand.low


fraud_band = register(
    Derivation(
        name="fraud_band",
        describes=(
            "band of claim.fraud_score alone (low/medium/high) — deliberately NOT "
            "conjoined with fraud_flag, and therefore neither siu_review nor "
            "fraud_flagged; see FraudBandDerivation"
        ),
        build=lambda thresholds: FraudBandDerivation(
            high_min=thresholds.fraud_band_high_min,
            med_min=thresholds.fraud_band_med_min,
        ),
    )
)
