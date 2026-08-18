"""The SLA strip — computed **exactly once**, here (AD-2, FR-SLA-1).

Four numbers over one scoped caseload: how fast claims are picked up,
approved and settled, and how many settled workers actually returned to
work. Story 5.3's dashboard tiles, Story 5.2's benchmarking and any later
copilot narration call `strip_of` rather than averaging these columns
again — `test_nothing_outside_the_worklist_aggregation_reads_the_sla_source_columns`
enforces that literally.

**The honesty rule this story exists for.** The prototype's `recalcSLA`
substituted 7.4d / 42d / 87% whenever a segment was empty, defaulted a
missing pick duration to one day, and ran only on handler screens — so a
supervisor read invented numbers off a stale header and could not tell.
Here a segment with nothing in it reports `no_data` and the UI draws an em
dash. There is no code path in this module that produces a number the
caseload did not contain.

**Where each decision is made.** The *set* comes from the scoped
repository (AD-7) and nothing here can widen it. The *targets* come from
configuration (AD-8) and are read by name. The *verdict* is computed here,
not in the browser (AD-1): the SPA receives value, target, direction,
display precision and status, and only styles them.

Two deliberate structural choices, both for Story 5.3's benefit:

- `strip_of` is pure — a caseload and a set of targets in, metrics out. It
  has no session, no HTTP and no clock, so the dashboard composes it and
  Hypothesis can hammer it (NFR-7).
- Status is decided on the **rounded** value, the one the user can see. A
  mean of 29.6 days displays as `30d`, and a tile reading `30d ✓ <30d`
  would be indefensible whatever the unrounded arithmetic said.

**Rounding is for display and for a verdict, never for an ordering.** That
distinction is what `segment_means_of` exists to keep. A tile is a single
figure with a single target beside it, so deciding its status on the
rounded number is the honest choice; a *ranking* is a comparison between
figures, and rounding before comparing lets a display convention decide the
order. `strip_of` and `segment_means_of` therefore compute the same three
averages once, in `_segment_values`, and differ only in whether they
quantize — so a consumer that needs to compare cannot accidentally acquire
a second definition of "average settle" along with the precision it needs.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from config import Settings
from data.context import CallerContext
from data.models import Claim
from data.models.enums import ReturnStatus, Stage
from data.repositories import claims as claim_repo


class SlaMetricKey(StrEnum):
    """The four tiles, in the prototype's left-to-right order."""

    pick = "pick"
    approve = "approve"
    settle = "settle"
    rtw_rate = "rtw_rate"


class SlaStatus(StrEnum):
    """The server's verdict. `no_data` is a first-class answer, not an error.

    (`passing` rather than `pass`, which is a Python keyword — the wire
    value is `pass`, which is what the contract and the UI see.)
    """

    passing = "pass"
    warn = "warn"
    no_data = "no_data"


class SlaDirection(StrEnum):
    """Which side of the target is good.

    Reported alongside the target because the UI writes the comparison out
    (`✓ <1d`, `⚠ >5d`, `✓ >80%`): deriving the operator client-side would
    put half of each rule in the browser, where nothing checks it.
    """

    below = "below"
    above = "above"


@dataclass(frozen=True)
class SlaTarget:
    """A configured target: the number, the side, and the display precision.

    `decimals` lives with the target rather than with the formula because
    it is what makes the reported value and the verdict agree — both are
    computed from the same rounded figure.
    """

    value: float
    direction: SlaDirection
    decimals: int


@dataclass(frozen=True)
class SlaMetric:
    """One tile. `value is None` and `status is no_data` always travel together.

    `decimals` is reported rather than left for the client to know, for the
    same reason `direction` is. It is not a styling preference: it is the
    precision this service *rounded to*, and the verdict was decided on
    that rounded figure. A client formatting to a precision of its own
    would eventually display a number the server never computed — round
    61.5 to `62d` beside a `warn` that was decided on 61.5 — which is the
    class of quiet disagreement this story exists to remove.
    """

    value: float | None
    target: float
    direction: SlaDirection
    decimals: int
    status: SlaStatus


@dataclass(frozen=True)
class SlaSample:
    """One claim, reduced to the five facts the strip is computed from.

    A projection rather than the ORM entity: it keeps `strip_of` pure and
    generatable, and it makes the metric definitions readable next to each
    other instead of spread across a row object.
    """

    pick_days: int | None
    approve_days: int | None
    settlement_days: int | None
    is_settled: bool
    fully_recovered: bool


#: The claim columns one `SlaSample` is built from — the projection any scoped
#: read must include for `sample_of` to work.
#:
#: **This tuple is the whole reason a second aggregate can exist at all.**
#: `test_nothing_outside_the_worklist_aggregation_reads_the_sla_source_columns`
#: greps every server module — comments and docstrings included — for the three
#: duration column names and allowlists this file, the model, `open_duration`
#: and the tests. Story 5.2's handler benchmarking needs the same five columns
#: per handler, and there is no way to write `[Claim.sla_pick_days, …]` in
#: `benchmarks.py` that the guard would tolerate. Exporting the projection and
#: its mapper is not a way around that rule; it is the rule enforced properly.
#: A caller composes `[*sla.SAMPLE_COLUMNS, …its own columns…]`, hands each row
#: back to `sample_of`, and folds the result through `strip_of` — so the second
#: consumer gets the columns *and* the definitions, and cannot acquire one
#: without the other.
#:
#: Order is not load-bearing (`sample_of` reads the row by label), but it is
#: kept in the tiles' left-to-right order so the projection reads like the
#: strip it feeds.
SAMPLE_COLUMNS: Final[tuple[InstrumentedAttribute[Any], ...]] = (
    Claim.sla_pick_days,
    Claim.sla_approve_days,
    Claim.settlement_days,
    Claim.stage,
    Claim.return_status,
)


def sample_of(row: sa.Row[Any]) -> SlaSample:
    """One projected claim row, reduced to the five facts the strip needs.

    By label rather than by position (`SlaSample(*row)`), which would work and
    would be one reordered projection away from averaging approval durations as
    pick-up times: both columns are nullable integers, so a swap type-checks,
    runs, and produces two wrong numbers with nothing to say so. It also lets a
    caller widen the projection — `[*SAMPLE_COLUMNS, Claim.handler_id, …]` —
    without this function caring where the extra columns landed.

    The two derived booleans are decided here rather than by the caller for the
    grep guard's reason and for a better one: "settled" and "fully recovered"
    are what the strip's denominators *mean*, and a caller that computed them
    would own half of two metric definitions.
    """
    return SlaSample(
        pick_days=row.sla_pick_days,
        approve_days=row.sla_approve_days,
        settlement_days=row.settlement_days,
        is_settled=row.stage is Stage.settled,
        fully_recovered=row.return_status is ReturnStatus.returned_and_fully_recovered,
    )


def targets_for(settings: Settings) -> Mapping[SlaMetricKey, SlaTarget]:
    """The four targets, read from configuration by name (AD-8, AC 3).

    The one place in the server that turns configured parameters into the
    strip's targets. When they move into a ZEN JDM document, this function
    is the whole of the change.
    """
    return {
        SlaMetricKey.pick: SlaTarget(
            value=settings.sla_pick_target_days, direction=SlaDirection.below, decimals=1
        ),
        SlaMetricKey.approve: SlaTarget(
            value=settings.sla_approve_target_days, direction=SlaDirection.below, decimals=1
        ),
        SlaMetricKey.settle: SlaTarget(
            value=settings.sla_settle_target_days, direction=SlaDirection.below, decimals=0
        ),
        SlaMetricKey.rtw_rate: SlaTarget(
            value=settings.sla_rtw_target_pct, direction=SlaDirection.above, decimals=0
        ),
    }


def _rounded(numerator: int, denominator: int, decimals: int) -> float:
    """A ratio at display precision, rounded half-up.

    Exact decimal arithmetic rather than float division: `sum/len` in
    binary floating point turns a true 29.5 into 29.499999999999996 often
    enough to move a tile across its target, and half-up rather than
    Python's bankers' rounding because a display that rounds 10.5 down and
    11.5 up is a defect report waiting to be filed.
    """
    quantum = Decimal(1).scaleb(-decimals)
    quantized = (Decimal(numerator) / Decimal(denominator)).quantize(
        quantum, rounding=ROUND_HALF_UP
    )
    return float(quantized)


#: The three tiles that are *durations*, in the order a composite adds them.
#:
#: `rtw_rate` is deliberately absent: it is a rate, and the one thing a caller
#: may do with this tuple is sum the segments it names.
DURATION_SEGMENTS: Final[tuple[SlaMetricKey, ...]] = (
    SlaMetricKey.pick,
    SlaMetricKey.approve,
    SlaMetricKey.settle,
)


def _segment_values(caseload: Sequence[SlaSample]) -> Mapping[SlaMetricKey, Sequence[int]]:
    """The numbers behind each duration tile — the definitions, in one place.

    Both `strip_of` and `segment_means_of` read this, which is the whole point:
    the three denominators (pick and approve over every claim carrying the
    duration, settle over *settled* claims carrying it) are stated once, so the
    rounded figure on a tile and the unrounded figure a ranking is decided on
    can only ever be two precisions of the same average.
    """
    settled = [sample for sample in caseload if sample.is_settled]
    return {
        SlaMetricKey.pick: [s.pick_days for s in caseload if s.pick_days is not None],
        SlaMetricKey.approve: [s.approve_days for s in caseload if s.approve_days is not None],
        SlaMetricKey.settle: [s.settlement_days for s in settled if s.settlement_days is not None],
    }


def segment_means_of(caseload: Sequence[SlaSample]) -> Mapping[SlaMetricKey, Decimal | None]:
    """The three duration averages **unrounded**, or `None` for an empty segment.

    `strip_of` with the quantization left off, and it exists for exactly one
    consumer: `benchmarks.py`, which adds the three together and *ranks* on the
    total.

    **Why a ranking may not be decided on the published figures.** The strip
    publishes pick and approve to one decimal and settle to whole days, because
    that is the precision a tile is read and its verdict decided at. Summing
    those three admits up to half a day of error into the total — and on the
    seeded portfolio that is larger than the gap between four of the six
    handlers, which is enough to swap two of them. A display convention would
    then be deciding who a supervisor is told to check in with. So the composite
    is computed here, exactly, and rounded once at the end for display.

    Still `Decimal` rather than `float`: `sum/len` in binary floating point
    turns a true 29.5 into 29.499999999999996, and three of those added together
    would put the ordering back in the hands of representation error — a smaller
    error than rounding, but the same class of defect.

    This is not a second averaging (AD-2). It is `_segment_values`, which
    `strip_of` folds through `_rounded` and this returns as it is; the two
    cannot disagree about what an average is without disagreeing here first.
    """
    return {
        key: (Decimal(sum(values)) / Decimal(len(values))) if values else None
        for key, values in _segment_values(caseload).items()
    }


def _metric(values: Sequence[int], target: SlaTarget) -> SlaMetric:
    """Average `values` against `target` — or report having nothing to average.

    The empty branch is the story: it returns `None`, never a stand-in.
    """
    if not values:
        return _no_data(target)
    return _verdict(_rounded(sum(values), len(values), target.decimals), target)


def _no_data(target: SlaTarget) -> SlaMetric:
    """A tile with nothing behind it. The only `value=None` constructor,
    so "empty segment" cannot be spelled two subtly different ways."""
    return SlaMetric(
        value=None,
        target=target.value,
        direction=target.direction,
        decimals=target.decimals,
        status=SlaStatus.no_data,
    )


def _verdict(value: float, target: SlaTarget) -> SlaMetric:
    met = value < target.value if target.direction is SlaDirection.below else value > target.value
    return SlaMetric(
        value=value,
        target=target.value,
        direction=target.direction,
        decimals=target.decimals,
        status=SlaStatus.passing if met else SlaStatus.warn,
    )


def strip_of(
    caseload: Sequence[SlaSample],
    targets: Mapping[SlaMetricKey, SlaTarget],
) -> Mapping[SlaMetricKey, SlaMetric]:
    """The four metrics over one caseload. Pure, and the reuse point for 5.3.

    Metric definitions (BRD §7.1, matching the prototype's `recalcSLA`
    except where it fabricated):

    - **Pick** — mean `sla_pick_days` over the claims that have one. Claims
      with no recorded duration are *excluded*, not counted as one day.
    - **Approve** — mean `sla_approve_days` over the claims that have one.
    - **Settle** — mean `settlement_days` over *settled* claims that have
      one.
    - **RTW rate** — settled claims that came back fully recovered, over
      *all* settled claims. A different denominator from Settle on purpose:
      a settlement with no recorded duration still has a return-to-work
      outcome, and sharing one denominator would drop it from the rate.
    """
    settled = [sample for sample in caseload if sample.is_settled]
    values = _segment_values(caseload)
    metrics = {key: _metric(values[key], targets[key]) for key in values}

    rtw_target = targets[SlaMetricKey.rtw_rate]
    metrics[SlaMetricKey.rtw_rate] = (
        _verdict(
            # ×100 inside the ratio, not after rounding it: rounding a
            # fraction to whole percent precision first collapses every
            # rate to 0% or 100%.
            _rounded(
                sum(100 for s in settled if s.fully_recovered), len(settled), rtw_target.decimals
            ),
            rtw_target,
        )
        if settled
        else _no_data(rtw_target)
    )
    return {key: metrics[key] for key in SlaMetricKey}


async def sla_strip(
    db: AsyncSession,
    ctx: CallerContext,
    settings: Settings,
) -> Mapping[SlaMetricKey, SlaMetric]:
    """The strip for the caller's book — the endpoint's one call.

    No role appears anywhere in this path: supervisor, analyst and handler
    take the identical scoped route through the repository, which is the
    whole of FR-SLA-1 (the prototype recomputed the strip on handler flows
    only, so everyone else read a stale default).

    Built on `SAMPLE_COLUMNS` and `sample_of` rather than on an inline
    projection since Story 5.2, with no behaviour change: this call and the
    handler-benchmark aggregate now read the same five columns through the same
    mapper, so the two can only disagree about what "settled" means by
    disagreeing here first.
    """
    rows = await claim_repo.select_claim_columns(db, ctx, SAMPLE_COLUMNS)
    return strip_of([sample_of(row) for row in rows], targets_for(settings))
