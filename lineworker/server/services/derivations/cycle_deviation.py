"""`cycle_time_status` — is this handler ahead of the desk or behind it? (5.2, AD-10)

The prototype's `statusOf` (`renderSV`, line 1032):

    d <= -8  → On Track
    d >=  8  → Attention
    otherwise → Watch

`d` is one handler's composite cycle time expressed as a percentage difference
from the portfolio's — negative is faster. Three bands, two boundaries, and the
boundaries are a rule document's business (AD-8) rather than this module's:
`services/worklist/benchmarks.py` computes the deviation and asks here what it
means, so the chip on the dashboard and any later narration of the same number
cannot disagree about where Watch ends.

**A separate module from `complexity_blend`, although both read one document.**
They answer different questions about different quantities — one bands a blended
mix, the other bands a percentage difference — and registering them separately
is what lets a consumer ask for the one it wants by name. `queue_flags` homes
two fraud rules together precisely because they share a column pair and a shape;
these two share neither.

**Named after the rule, not the value**, for the package's naming rule:
`cycle_deviation` exports `cycle_time_status`, so importing the module cannot
hand back the `Derivation` instance instead.

## Why both edges are inclusive, and why the outer band wins

`<=` and `>=` are the prototype's, kept. A handler sitting *exactly* on the
attention boundary is running that much slower than their peers, and filing them
as Watch would mean the one row this table exists to surface is the one row it
softens. The same argument runs the other way at the fast end: a handler exactly
on the on-track boundary has earned it.

The two conditions cannot both hold — `rules/parameters.HandlerPerformance`
refuses `onTrackDeviationPctMax >= attentionDeviationPctMin` when the document
is read, *equality included*, precisely because both edges here are inclusive
and an equal pair would overlap on its shared value — so the order below is not
load-bearing for correctness. It is still written most-actionable-last rather
than arbitrarily, and the refusal is where an inverted or collapsed pair is
caught, because a derivation that resolved the overlap itself would let a bad
document ship and report a slow desk as On Track under a rule decided by a line
number.

## Deviation is an integer percentage, decided on the number the user sees

`of` takes an `int`. That is `sla.py`'s ruling restated: a status chip beside a
figure the reader can see must be decided on that figure, or a table will
eventually show "+8%" next to Watch. The rounding happens once, in the
aggregate, and the verdict is taken on its result.
"""

from dataclasses import dataclass
from enum import StrEnum

from rules.parameters import HandlerPerformance
from services.derivations.registry import Derivation, register


class CycleStatus(StrEnum):
    """Snake_case values per the enum convention — the UI owns the labels
    ("On Track", "Watch", "Attention")."""

    on_track = "on_track"
    watch = "watch"
    attention = "attention"


@dataclass(frozen=True)
class CycleTimeStatusDerivation:
    def of(self, deviation_pct: int, params: HandlerPerformance) -> CycleStatus:
        """Band one handler's deviation from the scoped portfolio's composite.

        `deviation_pct` is signed: negative means this handler closes claims
        faster than the book they are being compared against. It is a
        *percentage difference*, not a percentile and not a raw day count — the
        distinction matters because the bands are the same two numbers for a
        four-claim book and a forty-claim one.

        `params` arrives per call rather than on the instance for
        `HandlerComplexityDerivation`'s reason: the registry builds this once
        per process and the document can be superseded under it.
        """
        if deviation_pct <= params.on_track_deviation_pct_max:
            return CycleStatus.on_track
        if deviation_pct >= params.attention_deviation_pct_min:
            return CycleStatus.attention
        return CycleStatus.watch


cycle_time_status = register(
    Derivation(
        name="cycle_time_status",
        describes=(
            "whether a handler's composite cycle time is on track, worth watching or "
            "needs attention, banded on its percentage deviation from the scoped "
            "portfolio's by the handler_performance document"
        ),
        build=lambda _thresholds: CycleTimeStatusDerivation(),
    )
)
