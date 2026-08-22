"""`age_band` — a banding of `employee.age`, and the first band over a *person*.

`risk_band.py` for severity and `fraud_score_band.py` for the fraud score; this
one is their shape over a third column, and it exists because the analyst
workspace's segmentation control has nine dimensions of which eight are values a
claim or a person carries and one is a **number**. `employee.age` is a stored
integer between the low twenties and the high fifties on the seeded workforce,
so a picker offering it as a value would offer thirty-eight of them, and an
analyst comparing "claims for 41-year-olds" with "claims for 42-year-olds" is
reading two samples of one. A band is the only way this dimension is a dimension.

## The members are ordinal words, and that is the whole design

    youngest   age <  ageYoungerMin
    younger    age >= ageYoungerMin
    older      age >= ageOlderMin
    oldest     age >= ageOldestMin

The tempting spelling is `age_35_44`, because that is what an analyst wants to
read. It is refused, and not as a matter of taste: it would put the cut-off in
the Python tier *and* in the JDM tier, which AD-8 forbids in one sentence — and
it is a lie waiting to happen, because moving `ageYoungerMin` in the document
would leave the member's name asserting the old edge on every chip, legend and
shared URL that had ever carried it. So the wire value is an ordinal word, the
edges are published on the payload, and the UI composes "35–44" from the edges
it was given. Move an edge in v7 and every label follows; nothing drifts, and no
chip ever contradicts the document that produced it.

Four members rather than three, unlike both sibling bands, because a workforce
is not a score: the two ends of a working-age population are genuinely different
questions from its middle, and three groups would put the mid-career majority
and the near-retirement cohort in one bucket. Three edges, four groups.

## The comparisons run downward, and `__post_init__` refuses an equal pair

`of()` tests `oldest` first, exactly as `RiskDerivation` tests `high` first, so
the *first* edge a value clears wins. That makes an inverted or equal pair
silent rather than loud — `ageOlderMin == ageOldestMin` deletes `older` and
files everyone in it as `oldest`, with no exception raised and every picker
still rendering — which is why `DerivationThresholds.__post_init__` refuses
equality with `>=` for these three. `deferred-work.md` records the shipped
`riskMedMin`/`riskHighMin` hole that argument comes from.

## No `sql_band()` twin, unlike `RiskDerivation`

That function exists because an aggregate counts risk bands in SQL. Every
consumer of this one folds a projection in Python — `services/worklist/
segmentation.py` narrows rows a scope-predicated read already returned, because
half the segmentation vocabulary is derived and a SQL predicate would split one
filter across two tiers (`data/repositories/claims.py` refuses that in writing,
twice). A SQL spelling here would be a second encoding of the rule with no
caller, which is the duplication `sql_is`'s docstring argues is only worth
paying when something actually asks for it.

## Why this module is `worker_age_band.py` and not `age_band.py`

The package's naming rule (`__init__.py`, code review 2026-08-10) is that a
module is never named after the derived value it exports —
`from services.derivations.age_band import age_band` silently rebinds the package
attribute from the submodule to the `Derivation` instance, so
`services.derivations.age_band.AgeBand` would raise `AttributeError` with no
obvious cause. `risk_band.py` exporting `risk` and `fraud_score_band.py`
exporting `fraud_band` both dodge that by naming the *thing banded*; this rule
bands an age and the value is called `age_band`, so naming the thing banded is
exactly the collision. `worker_age_band` names the rule one notch more precisely
— it is the **injured worker's** age, a column of `employee` rather than of
`claim`, which is also why the Trends section needed a fourth projection-family
read to see it at all. `test_no_derivation_module_is_named_after_the_value_it_
exports` is what fails the build if a later story forgets.
"""

from dataclasses import dataclass
from enum import StrEnum

from services.derivations.registry import Derivation, register


class AgeBand(StrEnum):
    """Snake/lowercase ordinal words — the UI owns labels, and composes them.

    **Declared youngest → oldest**, which is a scale's reading order and matches
    `FraudBand`'s low → high rather than `RiskBand`'s high → med → low. A reader
    scans an age segmentation the way they scan a histogram, from the youngest
    cohort rightwards, and the picker draws the members in this order.

    **Not one member carries a number**, which is the rule this enum exists to
    keep. The label an analyst reads ("35–44") is composed in the browser from
    the edges the payload publishes, so the vocabulary here can outlive any
    particular set of cut-offs — see the module docstring.
    """

    youngest = "youngest"
    younger = "younger"
    older = "older"
    oldest = "oldest"


@dataclass(frozen=True)
class AgeBandDerivation:
    """Bands `age`: oldest >= `oldest_min`, older >= `older_min`, younger >= `younger_min`.

    `RiskDerivation`'s shape over a different column, and its three thresholds
    arrive the same way — from the versioned `derivation_thresholds` JDM document
    (AD-8: parameters are data, formulas are Python). They are validated as
    `younger_min < older_min < oldest_min` in `rules/parameters.py`, strictly, so
    this class does not re-check an ordering it cannot fix.

    `age` is keyword-only on `of()`, matching `FraudBandDerivation.of` rather
    than `RiskDerivation.of`'s positional severity. The reason is the same one
    that made the fraud rules keyword-only: this computer is called in the same
    fold as `risk`, over a projection carrying `severity_score` beside `age`, and
    a positional `int` there is one transposition away from banding a severity
    score as an age — which would type-check, run, and produce a plausible
    distribution of the wrong column.
    """

    younger_min: int
    older_min: int
    oldest_min: int

    def of(self, *, age: int) -> AgeBand:
        if age >= self.oldest_min:
            return AgeBand.oldest
        if age >= self.older_min:
            return AgeBand.older
        if age >= self.younger_min:
            return AgeBand.younger
        return AgeBand.youngest


age_band = register(
    Derivation(
        name="age_band",
        describes=(
            "band of employee.age into four ordinal groups (youngest/younger/older/oldest) — "
            "the members carry no numbers so the cut-offs live only in derivation_thresholds; "
            "see AgeBandDerivation"
        ),
        build=lambda thresholds: AgeBandDerivation(
            younger_min=thresholds.age_younger_min,
            older_min=thresholds.age_older_min,
            oldest_min=thresholds.age_oldest_min,
        ),
    )
)
