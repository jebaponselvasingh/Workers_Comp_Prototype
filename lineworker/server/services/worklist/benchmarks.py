"""Handler performance benchmarking — one ranked table over one scoped book (5.2).

The prototype builds this inside `renderSV` (line 1015): it groups the global
claim array by handler, averages three duration columns per group *and* over the
whole array, adds the three averages up, compares each handler's total with the
portfolio's, and bands the difference — all in the browser, over data it had
already filtered client-side to whatever persona was selected. Both halves are
what this module replaces. The *set* is the scoped repository's (AD-7) and the
*figures* are decided here (AD-1), so a supervisor receives a ranked table she
has no way to have invented and no way to disagree with the top bar about.

## Where each number comes from, and why none of them is computed here

- **The three cycle-time segments** come from `sla.segment_means_of`, per
  handler and once more over the whole in-scope book, and **the RTW rate** from
  `sla.strip_of` beside it. Those are one set of definitions at two precisions,
  not two aggregations: `sla._segment_values` states the three denominators and
  both functions read it. AD-2 says the benchmark table and the top bar can
  never disagree about what "average settle" means, and this module is what
  makes that structural rather than aspirational: it never names a duration
  column — `test_nothing_outside_the_worklist_aggregation_reads_the_sla_source_columns`
  greps for those names, comments and docstrings included — so it *cannot* have
  averaged anything itself. It composes `sla.SAMPLE_COLUMNS` into its one read
  and hands each row to `sla.sample_of`.

  **`strip_of` is called per handler group for the RTW rate alone, and that
  costs duplicate arithmetic.** `strip_of` computes all three duration segments
  and their verdicts on its way to publishing a tile set; `segment_means_of` is
  then asked for those same three durations at full precision, so each group's
  three averages are computed twice. The waste is accepted deliberately.
  `strip_of` is the *single definition* of the return-to-work rate — its
  denominator (settled claims carrying a return status) and its rounding are
  stated once, in `sla.py`, and re-implementing either here would be exactly the
  second aggregation AD-2 forbids, in the module that is forbidden from naming
  the columns it would need. The alternative that avoids both — a narrower
  `sla.rtw_rate_of` — would be a third public entry point into the same fold for
  a saving measured in six extra divisions per request. The duplication is
  therefore a known, bounded cost, not an oversight; what must never happen is a
  rate computed anywhere but `sla.py`.
- **The complexity grade** is `derivations.handler_complexity`, and **the status
  chip** is `derivations.cycle_time_status`. One registered computer each
  (AD-10, AC 3), both parameterised by the `handler_performance` document.
- **The pending-approval count** reads `PriorityWeights.pending_approval_statuses`
  — the same list the queue scorer's +25 term is worth, in the same document.

What is left for this module is composition and one arithmetic step per row: the
composite, the deviation, the bar ratio and the rank. Those are stated below
rather than left implicit, because each is a decision.

## The composite is summed from *unrounded* segment means, and published to 1dp

`strip_of` publishes pick and approve to one decimal and settle to whole days,
because that is the precision a tile is read and its verdict decided at. Adding
those three published figures would be the obvious thing to do here and is
wrong: whole-day rounding admits up to half a day of error into the total, and
on the seeded portfolio that is *larger than the gap between four of the six
handlers*. Marcus Chen's true composite is 72.036 days and Fatima
Al-Mansoori's is 72.667 — Marcus is over half a day faster — yet rounding their
settle segments (60.571 → 61 against 63.333 → 63) produces 72.4 against 72.3 and
puts the slower handler above the faster one. A rendering convention would then
be deciding who a supervisor is told to check in with.

So the composite is summed from `sla.segment_means_of`, which is `strip_of`'s
own three averages with the quantization left off, and rounded **once** at the
end to one decimal for display. `sla.py`'s ruling is not being overturned: that
ruling is about a *verdict on a single figure* against a target beside it, and
it still governs every tile. A rank is a comparison between figures, and nothing
that only exists to be displayed may decide one.

Rounding last also keeps the visible column consistent with the order it is in.
Quantization is monotonic, so a row ranked above another can never show a larger
Avg Days figure; two rows can show the *same* figure in a different order, which
is the honest reading — they differ by less than the column's precision, and the
column says so. `deviationPct` is likewise computed from the unrounded
composites and rounded once, for the same reason applied to a band.

## A missing segment is substituted from the scoped portfolio, uniformly

`renderSV` substitutes the portfolio's settle average when a handler has none
(`settle || pAvgSettle`) and lets a missing pick or approve average fall through
as `0`, which reads as "instant" and ranks that handler first. Here *any*
missing segment is substituted by the scoped portfolio's own average for the
same segment. One rule instead of two, no zero that means "unknown", and on the
seeded portfolio it never fires — every handler has settled claims — so the
ported figures are unchanged.

If the **portfolio** has no data for a segment either, there is nothing honest
to substitute: the composite would be a partial sum silently compared against
other partial sums. Every composite-derived field is then `None` and the rows
are ordered by name. That state is unreachable on any seeded scope (a handler's
samples are a subset of the portfolio's, so the two are missing a segment
together, and the full book has all three), and what a *rank* should mean in it
is a product decision rather than a dev-time pick — see the spec's Block If.

## Pending approvals are claim statuses, not payment weeks

Two things in this console are called "pending approval": a `ClaimStatus` in
`pendingApprovalStatuses`, and a `ScheduleWeekStatus` on a table
`services/financials` owns (AD-12). The prototype counts the first, the queue
scorer already means the first, and it is one column on a row this aggregate is
reading anyway. The second would need a new cross-service scoped aggregate for a
column the design contract never asked for.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings
from data.context import CallerContext
from data.models import Claim
from data.models.enums import ClaimStatus, UserRole
from data.repositories import claims as claim_repo
from rules.parameters import DerivationThresholds, HandlerPerformance, PriorityWeights
from services import derivations
from services.derivations import (
    ComplexityBand,
    CycleStatus,
    CycleTimeStatusDerivation,
    HandlerComplexityDerivation,
    HandlerMix,
)
from services.worklist import sla

#: The three segments that make up a composite cycle time, in the order the
#: prototype adds them — `sla.py`'s own tuple rather than a copy of it, so the
#: composite cannot come to mean something the strip does not.
#:
#: `rtw_rate` is deliberately absent from it. It is a *rate*, not a duration —
#: adding a percentage to a count of days would be meaningless — and it is
#: reported in its own column beside the composite rather than folded into it.
CYCLE_SEGMENTS: Final[tuple[sla.SlaMetricKey, ...]] = sla.DURATION_SEGMENTS

#: The scale a deviation and a bar ratio are reported on.
PERCENT: Final[int] = 100

#: The precision a composite is *published* at, having been ranked at full.
#:
#: One decimal rather than the whole days the slowest of its three segments is
#: displayed at: the ordering is decided at a finer precision than that, and a
#: column that showed less of the figure than the sort used would leave a reader
#: unable to tell a real gap from a rounding artefact. See the module docstring.
COMPOSITE_DECIMALS: Final[int] = 1

#: The roles that carry the oversight capability, as an **allowlist**.
#:
#: Written this way round deliberately. A denylist (`role is UserRole.handler`)
#: grants every `UserRole` member added after today the whole desk's performance
#: figures by default — at import time, with no test failing and no reviewer
#: prompted, because the new member simply is not mentioned anywhere. This
#: endpoint publishes colleagues' numbers; it is the wrong surface to open by
#: omission. `BenchmarksNotPermitted` carries the argument for *which* two.
PERMITTED_ROLES: Final[frozenset[UserRole]] = frozenset({UserRole.supervisor, UserRole.analyst})


class BenchmarksNotPermitted(PermissionError):
    """The caller's role does not carry the benchmark-reading capability.

    **Raised before any claim is read**, which is `ApprovalNotPermitted`'s
    ordering rule inverted onto a query: there, answering 403 first keeps a
    refusal from telling a supervisor whether a claim exists. Here there is no
    identifier to leak, and the ordering is still the right one — a refusal that
    depended on what the scoped read returned would answer differently for a
    handler with claims and a handler between assignments, which is an oracle
    about the roster.

    **Why this endpoint has a role gate at all when `/dashboard/summary` does
    not.** That one answers a count of the caller's own book: a handler's claims
    aggregated are her own data in another shape, so the scope predicate is the
    whole of the control. This one answers rows *about colleagues* — how fast
    each of them closes, how much of their book is litigated, whether their desk
    needs a check-in. Scope does not narrow that to the caller, because a
    handler shares employers with the peers she would be reading about. Role is
    what separates "oversight" from "my own numbers", and BR-ROLE puts oversight
    with supervisors and analysts.

    **Named as an allowlist** (`PERMITTED_ROLES`), not as a refusal of the one
    role that exists to be refused today. The difference only shows up in the
    future, which is when it matters: a fourth `UserRole` — an auditor, a
    broker, a read-only executive — would be admitted to a table of colleagues'
    performance the moment it was declared, silently, with every test still
    green. Adding a role should not grant a capability by default.
    """


def require_benchmarks_access(ctx: CallerContext) -> None:
    """Refuse a caller outside `PERMITTED_ROLES`, reading nothing to decide it.

    The gate, extracted so that a *caller* can ask it too. `handler_benchmarks`
    still calls it first and remains the real gate — capability belongs with the
    service that owns the rows, and a router-only check would be one deployment
    away from a second entry point serving colleagues' figures to anybody. But
    the router loads three rule documents before it reaches the service, so "the
    refusal happens before any read" was true of the *claim* read and false of
    the three `rules_document` reads that preceded it. Four docstrings said
    otherwise. Calling this at the top of the route makes the sentence true of
    every read, and the duplicate check inside the service costs one comparison.

    Deliberately returns `None` rather than a boolean: a predicate invites
    `if not can_read(ctx): pass`, and a caller that forgets the `not` opens the
    surface silently. Raising is the only outcome that cannot be ignored.
    """
    if ctx.role not in PERMITTED_ROLES:
        raise BenchmarksNotPermitted("Only a supervisor or analyst can read handler benchmarks.")


@dataclass(frozen=True)
class BenchmarkClaim:
    """One claim, reduced to the facts a benchmark row is folded from.

    A projection rather than the ORM entity, for `PortfolioClaim`'s two reasons:
    it keeps `benchmarks_of` pure and generatable, and it puts the row
    definitions next to each other instead of spread across a row object.

    `sample` is an `SlaSample` rather than three loose columns because the SLA
    aggregation owns those three facts and their meaning (AD-2). Nothing here
    unpacks it; it is passed straight back to `sla.strip_of`.

    `handler_id` is carried beside `handler_name` and the grouping is on the
    **id**. Two handlers who share a display name are two rows, not one merged
    row averaging both their books — the sort of defect that surfaces years
    later as a supervisor asking why one person appears to carry ninety claims.
    """

    handler_id: int
    handler_name: str
    sample: sla.SlaSample
    severity_score: int
    surgery_required: bool
    litigation_flag: bool
    status: ClaimStatus


@dataclass(frozen=True)
class HandlerBenchmark:
    """One row of the table, in the columns' own order.

    Every field is a figure the SPA renders verbatim (AD-1). Five of them are
    nullable, and the two reasons are different:

    - `rtw_pct` is `None` when the handler has settled nothing. That is
      reachable and ordinary — a handler three weeks into a new desk — and the
      UI draws an em dash, exactly as the SLA strip does for a `no_data` tile.
      The prototype divides by `max(1, settled)` and prints "0%", which reads as
      "nobody came back" rather than "nobody has settled yet".
    - `rank`, `composite_days`, `deviation_pct`, `cycle_speed_pct` and
      `cycle_status` are `None` together, and only in the state the module
      docstring reserves for a product decision: the scoped portfolio itself has
      no data for one of the three segments. They are one condition, so a
      renderer that handles the composite handles all five.

    **`rank` is null exactly when `composite_days` is.** It is the composite's
    ordinal, and there is no ordinal for a quantity that could not be computed.
    Numbering those rows anyway would put `1`, `2`, `3` in a `#` column beside a
    row of em dashes, on rows that are in *name* order — a ranking claim the
    data does not support, made in the one column a reader trusts without
    checking. They sort to the bottom and carry no number.

    Otherwise `rank` is 1-based and is the server's, so the `#` column is not a
    row index the browser happened to compute — which matters because the
    ordering is a function of a rule document's thresholds and a scope
    predicate, neither of which the SPA has.

    **`handler_id` rides along beside the name, and only the id is an
    identity.** The grouping has been on it since this module was written (see
    `BenchmarkClaim`), and Story 5.5 is the first consumer that needs it on the
    wire: clicking a row opens `filter[handlerId]=…`, and filtering on a display
    name would merge two handlers who share one. `EmployerPaid.employer_id` is
    the same field for the same reason on the same dashboard.
    """

    handler_id: int
    rank: int | None
    handler_name: str
    case_count: int
    cycle_speed_pct: int | None
    composite_days: float | None
    rtw_pct: float | None
    complexity_score: int
    complexity_band: ComplexityBand
    pending_approvals: int
    deviation_pct: int | None
    cycle_status: CycleStatus | None


@dataclass(frozen=True)
class HandlerBenchmarks:
    """The ranked rows, the book they were ranked against, and the callout's facts.

    `portfolio_composite_days` is on the block because every `deviation_pct` is
    relative to it: a table that published percentages without the figure they
    are percentages *of* would leave "+9%" uninterpretable, and the SPA cannot
    add three averages of its own to recover it.

    `leader` and `laggard` are **names, decided here**, rather than left to the
    UI to read off the first and last row. They are the two facts the prototype's
    callout states ("🏆 X leads the desk … ⚠ Y is running slowest"), and picking
    them in the browser would mean indexing a list on the assumption it is
    ordered — true today, and exactly the assumption that breaks when a later
    story adds a column sort. Both are `None` for an empty scope, and both name
    the same handler when the scope holds exactly one.

    No `total` and no `next_cursor`. `items` *is* the list and its length is the
    number of handlers in the caller's scope — `ClaimActionsResponse`'s
    argument, and the count here is bounded by the desk rather than by the
    portfolio.
    """

    items: tuple[HandlerBenchmark, ...]
    portfolio_composite_days: float | None
    leader: str | None
    laggard: str | None


def _percent(numerator: Decimal, denominator: Decimal) -> int:
    """`numerator / denominator` as a whole percentage, rounded half-up.

    Exact decimal arithmetic and half-up rounding for `sla._rounded`'s two
    reasons, one of which bites harder here: the result is compared against the
    deviation bands, so a true -8 that arrived as -7.999999999999999 through
    binary division would move a handler from On Track to Watch.

    Half-up is symmetric about zero (`ROUND_HALF_UP` on a `Decimal` rounds away
    from it), and deviations are signed. That is a deliberate difference from
    the prototype's `Math.round`, which rounds -8.5 to -8 and +8.5 to +9: a
    display that treats the two halves of its own scale differently is the
    defect `_rounded`'s docstring already refuses.
    """
    return int((numerator / denominator * PERCENT).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _composite(
    means: Mapping[sla.SlaMetricKey, Decimal | None],
    fallback: Mapping[sla.SlaMetricKey, Decimal | None],
) -> Decimal | None:
    """The three **unrounded** segment means added up, substituting from `fallback`.

    Returns `None` when a segment is missing from *both* — see the module
    docstring on why that is not a partial sum.

    The inputs are `sla.segment_means_of`'s, not `strip_of`'s published tiles:
    this total is what the table is ranked on, and a ranking decided on display
    precision is a ranking decided by a rendering convention. `_published`
    rounds it, once, at the end.

    `Decimal` throughout rather than `float`, so the sum is exact over exact
    quotients and two composites that are genuinely equal compare equal instead
    of differing in the sixteenth place.
    """
    total = Decimal(0)
    for segment in CYCLE_SEGMENTS:
        value = means[segment]
        if value is None:
            value = fallback[segment]
        if value is None:
            return None
        total += value
    return total


def _published(days: Decimal) -> float:
    """A composite at the precision the Avg Days column shows it.

    The only place a composite is rounded, and it happens after the order, the
    deviation and the bar ratio have all been decided on the exact figure.
    """
    return float(days.quantize(Decimal(1).scaleb(-COMPOSITE_DECIMALS), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class _Group:
    """One handler's claims, before the row is built. Internal to the fold."""

    handler_id: int
    handler_name: str
    claims: list[BenchmarkClaim]


def _grouped(caseload: Sequence[BenchmarkClaim]) -> list[_Group]:
    """The caseload by `handler_id`, in first-appearance order.

    Order here is not the table's — the rows are sorted by composite below — but
    it is deterministic, which keeps the fold reproducible for a property test
    and keeps a debugger's view of the intermediate stable.
    """
    groups: dict[int, _Group] = {}
    for claim in caseload:
        group = groups.get(claim.handler_id)
        if group is None:
            group = _Group(claim.handler_id, claim.handler_name, [])
            groups[claim.handler_id] = group
        group.claims.append(claim)
    return list(groups.values())


def benchmarks_of(
    caseload: Sequence[BenchmarkClaim],
    targets: Mapping[sla.SlaMetricKey, sla.SlaTarget],
    complexity: HandlerComplexityDerivation,
    cycle_status: CycleTimeStatusDerivation,
    params: HandlerPerformance,
    pending_approval_statuses: frozenset[ClaimStatus],
) -> HandlerBenchmarks:
    """The ranked table over one scoped caseload. Pure — no session, no clock.

    `summary_of`'s split, and for its reasons: the arithmetic is database-free
    and Hypothesis-testable, and the one scoped read happens exactly once in the
    async wrapper below.

    **Handlers come from the claims, never from a roster (AD-7).** Grouping the
    rows the scope predicate returned gets the straddle case right by
    construction: Kaya Johnson holds a John Deere assignment and handles none of
    its claims, so Ken Stoker's Deere-and-Lockheed table shows Liam O'Sullivan
    and Fatima Al-Mansoori and not her. An implementation that enumerated "the
    handlers assigned to my employers" and then counted their claims would list
    her with a book of zero — which is the shape of AD-7 error this signature
    makes impossible: there is no roster in it.

    **Ranking is ascending composite, ties broken by handler name and then by
    handler id.** Fastest first, which is the prototype's order (it sorts on the
    deviation, a monotonic function of the same quantity). The composite is the
    exact one, not the published one — see the module docstring.

    The tie-break is needed and is not cosmetic. Exact ties are reachable
    whenever two handlers' books average identically, which small desks do, and
    an unstable order would make two consecutive requests disagree about who is
    third — on a list whose whole subject is the order. Name first because it is
    the visible column and a reader can check it; `handler_id` behind it because
    two handlers can share a display name and the order still has to be total.
    """
    portfolio_means = sla.segment_means_of([claim.sample for claim in caseload])
    portfolio_composite = _composite(portfolio_means, portfolio_means)

    rows: list[tuple[Decimal | None, str, int, HandlerBenchmark]] = []
    for group in _grouped(caseload):
        samples = [claim.sample for claim in group.claims]
        # Two folds over the same samples, and neither is a second averaging:
        # `strip_of` is asked only for the RTW rate, which is a published
        # percentage with its own denominator, and `segment_means_of` for the
        # three durations at full precision. Both are `sla.py`'s.
        strip = sla.strip_of(samples, targets)
        composite = _composite(sla.segment_means_of(samples), portfolio_means)
        graded = complexity.of(
            HandlerMix(
                case_count=len(group.claims),
                severity_total=sum(claim.severity_score for claim in group.claims),
                surgery_count=sum(1 for claim in group.claims if claim.surgery_required),
                litigation_count=sum(1 for claim in group.claims if claim.litigation_flag),
            ),
            params,
        )
        # A portfolio composite of zero would be a book picked up, approved and
        # settled the same day — not reachable on real data, and a division by
        # it is. The prototype guards it the same way (`compPortfolio ? … : 0`),
        # and zero deviation is the honest answer: a handler cannot be a
        # percentage slower than a desk that takes no time at all.
        deviation = (
            None
            if composite is None or portfolio_composite is None
            else 0
            if portfolio_composite == 0
            else _percent(composite - portfolio_composite, portfolio_composite)
        )
        rows.append(
            (
                composite,
                group.handler_name,
                group.handler_id,
                HandlerBenchmark(
                    handler_id=group.handler_id,
                    # Replaced once the order is known — `rank` is a property of
                    # the table, not of the handler, so it cannot be decided
                    # while the rows are still being built. `None` rather than a
                    # `0` sentinel, so a row that never acquires one is already
                    # in the state the contract publishes.
                    rank=None,
                    handler_name=group.handler_name,
                    case_count=len(group.claims),
                    cycle_speed_pct=None,
                    composite_days=None if composite is None else _published(composite),
                    rtw_pct=strip[sla.SlaMetricKey.rtw_rate].value,
                    complexity_score=graded.score,
                    complexity_band=graded.band,
                    pending_approvals=sum(
                        1 for claim in group.claims if claim.status in pending_approval_statuses
                    ),
                    deviation_pct=deviation,
                    cycle_status=(
                        None if deviation is None else cycle_status.of(deviation, params)
                    ),
                ),
            )
        )

    # `composite is None` first in the key so the unrankable rows sort last
    # without needing a sentinel figure standing in for "unknown".
    rows.sort(key=lambda row: (row[0] is None, row[0] or Decimal(0), row[1], row[2]))

    composites = [composite for composite, _name, _id, _row in rows if composite is not None]
    # The bar is drawn against the *slowest* handler in scope, so the widest bar
    # is always full and the fastest is the shortest — the prototype's
    # `comp / maxComp`. It is a peer-relative figure by design: a desk where
    # everybody is within a day of each other should look even, not be stretched
    # across the whole track by a normalisation this table never promised. The
    # direction is examined rather than inherited; see the Dev Agent Record and
    # `HandlerBenchmarkTable.tsx`, which states the units in the bar's own
    # accessible name so nothing is left to be inferred from its length.
    slowest = max(composites, default=None)

    #: A running 1-based ordinal over the *rankable* rows only. The unrankable
    #: ones sort behind them and are numbered `None`, because a `#` beside a row
    #: of em dashes would be a ranking claim over rows that are in name order.
    ordinal = 0
    ranked: list[HandlerBenchmark] = []
    for composite, _name, _id, row in rows:
        if composite is None or slowest is None:
            ranked.append(row)
            continue
        ordinal += 1
        ranked.append(
            replace(
                row,
                rank=ordinal,
                cycle_speed_pct=(
                    # A desk on which nobody takes any time at all: every handler
                    # sits at the top of a track nobody is slower than. Reachable
                    # only alongside a zero portfolio composite, and answered the
                    # same way — a full bar rather than a division.
                    PERCENT if slowest == 0 else _percent(composite, slowest)
                ),
            )
        )

    rankable = [row for row in ranked if row.rank is not None]
    return HandlerBenchmarks(
        items=tuple(ranked),
        portfolio_composite_days=(
            None if portfolio_composite is None else _published(portfolio_composite)
        ),
        leader=rankable[0].handler_name if rankable else None,
        laggard=rankable[-1].handler_name if rankable else None,
    )


def _computers(
    thresholds: DerivationThresholds,
) -> tuple[HandlerComplexityDerivation, CycleTimeStatusDerivation]:
    """The two registered computers, built from the block the router loaded.

    Both ignore the `DerivationThresholds` they are built from — their tunables
    are in `handler_performance` and arrive at `.of()` — but they are still
    reached *through the registry* rather than instantiated here, which is
    `services/claims/meetings._status_computer`'s argument and AD-10's whole
    point: a call site that constructed the class would be the second computer
    the registry exists to prevent, and nothing would object.

    Synchronous, and that is the change worth noticing: the block arrives as an
    argument instead of being fetched here, so this function no longer touches
    the session at all. `handler_benchmarks` is then a scoped read and a pure
    fold with nothing else in it, which is what its own docstring claims.
    """
    return (
        derivations.handler_complexity.for_thresholds(thresholds),
        derivations.cycle_time_status.for_thresholds(thresholds),
    )


async def handler_benchmarks(
    db: AsyncSession,
    ctx: CallerContext,
    params: HandlerPerformance,
    weights: PriorityWeights,
    thresholds: DerivationThresholds,
    settings: Settings,
) -> HandlerBenchmarks:
    """The benchmark table for the caller's book — the endpoint's one call.

    Takes **all three** parameter blocks rather than fetching any of them,
    `portfolio_summary`'s signature and its reason: the route loads them once
    and hands them down, so this stays a composition of scope and parameters.
    `thresholds` is on the list even though both derivations ignore the block
    they are built from — a rule block half injected and half fetched is worse
    than either, because the next reader cannot tell from the signature where
    this function's parameters come from. `settings` arrives for the same reason
    one step further out: the SLA targets are deployment configuration and
    `sla.targets_for` is the one place they become targets.

    One scoped read, one pure fold — and now literally so: no other await in the
    body. The projection is `sla.SAMPLE_COLUMNS` widened with the five columns
    this table needs beyond the cycle time, and the handler's display name comes
    from the repository's join rather than from a second query per row. "One
    pure fold" is the *shape* rather than a claim of minimal arithmetic:
    `benchmarks_of` folds each group twice, once through `sla.strip_of` for the
    RTW rate and once through `sla.segment_means_of` for the three durations, and
    the module docstring records why that duplication is accepted.

    Raises `BenchmarksNotPermitted` (403) for any role outside
    `PERMITTED_ROLES`, before the read. The route calls
    `require_benchmarks_access` first as well, so the refusal also precedes the
    three rule-document reads it makes on this function's behalf; the check here
    stays because capability belongs with the service, not with one caller.
    """
    require_benchmarks_access(ctx)

    rows = await claim_repo.select_claim_columns_with_handler(
        db,
        ctx,
        [
            *sla.SAMPLE_COLUMNS,
            Claim.handler_id,
            Claim.severity_score,
            Claim.surgery_required,
            Claim.litigation_flag,
            Claim.status,
        ],
    )
    # Named rather than positional, for `portfolio_summary`'s reason: two of
    # these are booleans, so a reordered projection would type-check, run, and
    # grade every handler's book on the wrong flag.
    caseload = [
        BenchmarkClaim(
            handler_id=row.handler_id,
            handler_name=row.handler_name,
            sample=sla.sample_of(row),
            severity_score=row.severity_score,
            surgery_required=row.surgery_required,
            litigation_flag=row.litigation_flag,
            status=row.status,
        )
        for row in rows
    ]
    complexity, cycle_status = _computers(thresholds)
    return benchmarks_of(
        caseload,
        sla.targets_for(settings),
        complexity,
        cycle_status,
        params,
        weights.pending_approval_statuses,
    )
