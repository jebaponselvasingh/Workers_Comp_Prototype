"""The analyst workspace's Fraud section — three read aggregates over one book
(FR-AN-1, Story 7.1).

Epic 5 gave the portfolio one fraud surface: a KPI card counting thirteen claims,
with a drill-through behind it. This module is the rest of the question. Where is
the suspicion concentrated, how far along the SIU pipeline is it, which injury
types and which desks carry it — and, from the cache Epic 6 fills, what the model
keeps saying about it.

Structurally it is `charts.py`'s shape, three times: a frozen row projection built
**named**, frozen results, pure folds, and thin async wrappers whose parameter
blocks arrive as arguments rather than being fetched here. Nothing in this file
touches the rules engine, and nothing in it holds a cut-off —
`test_no_module_outside_the_registry_hardcodes_the_band` greps it, comments
included, precisely because the reason it can is that there is nothing here to
find.

## Three fraud rules, and this module calls all three of them by name

    siu_review     = fraud_flag and fraud_score >= siuFraudScoreMin       -> the pipeline
    fraud_flagged  = fraud_flag and fraud_score >= fraudFlagScoreMin      -> the rates
    fraud_band     = band(fraud_score) against two edges, no flag         -> the distribution

They are one registered computer each (AD-10), built once per request from the
block the router loaded. Two of them are gated on a triage flag and the third is
not, which is the whole reason the distribution can show what it shows: a claim
scoring high that nobody flagged is *absent* from the flagged population and
*present* in the high band, and "how much of this portfolio is that claim" is the
question a fraud analyst opens this screen to ask.

On today's seed `fraud_flag` and `fraud_score >= fraudFlagScoreMin` coincide
exactly, so the seeded data cannot tell the three rules apart. That is a fact
about the dataset rather than about the rules, and it is why
`tests/test_fraud_analytics.py` folds synthetic projections where they disagree —
those are not thoroughness, they are the only place the distinction is tested at
all. `services/derivations/queue_flags.py` records the same for the pair it owns.

## Absent bands are zero, absent stages are omitted, and the difference is a rule

`charts._declared` omits a category the scope does not contain, because an
enum-keyed distribution over a *column* lists what the scope holds. `_banded`
below does the opposite, and the reason is that its vocabulary is not a column's:
`FraudBand` is a rule's answer, so all three members exist for every portfolio and
"no claim scored high" is an answer rather than a category that does not apply.
The SIU pipeline groups on `Claim.stage`, which *is* a column, and therefore
follows `_declared`'s rule — see `_pipeline`.

## Sorting without a cursor

`drill_through.py:1234-1237` refuses a `sort` parameter on a cursored list,
because an offset into one ranking means nothing against another. The three rate
breakdowns take `benchmarks.HandlerBenchmarks`' shape instead — `items` **is** the
list, no `total`, no `next_cursor` — which is the shape that makes sorting free:
a total order over an uncapped list is just an order, and there is no page of it
whose meaning could drift. The injury-type cut survives that argument because it
is a chart's problem rather than a page's, and it is published with `truncated`
exactly as `charts.py`'s ranked series are.

## The red-flag view is exact-text grouping, and it says so on screen

`FraudRedFlagsNarrative.red_flags` is `list[str]`: model-authored clauses, bounded
in length, with no code, category or enum anywhere in the stored shape. Grouping
on normalised text is the only fold available that does not require this module to
decide that two differently-worded sentences mean the same thing — and that
decision, made silently, is exactly the derived judgement AD-2 keeps out of the
model's hands and AD-10 keeps in one place. Clustering or keyword-bucketing would
be the server originating a classification nobody can version, review or explain
to the analyst reading it. So: normalise, group, count distinct claims, publish
the coverage, and let the caption admit that a count of one is the ordinary case.
A real red-flag taxonomy is a product decision with an owner and a vocabulary; it
is not a fold, and this is not one.

## Role gates capability, scope gates visibility (AD-7)

`FRAUD_ANALYTICS_ROLES` is a **new** allowlist beside `benchmarks.PERMITTED_ROLES`
rather than an edit to it, and the two say different things on purpose: that one
is the oversight capability (supervisor *and* analyst) and this one is the analyst
workspace (analyst alone). Weakening the benchmark allowlist to reach across both
would open colleagues' performance figures to whatever the wider list grew to
hold. Every read below still runs behind `employer_scope(ctx)`, and no function in
this module branches on role to widen or skip it — the gate decides whether the
surface answers at all, never what it answers.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, Final

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models.enums import Stage, UserRole
from data.repositories import claims as claim_repo
from data.repositories import insights as insight_repo
from rules.parameters import DerivationThresholds
from services import derivations
from services.derivations import (
    FraudBand,
    FraudBandDerivation,
    FraudFlaggedDerivation,
    SiuReviewDerivation,
)
from services.worklist.charts import CategoryCount, Distribution

log = structlog.get_logger()

#: The roles that carry the analyst-workspace capability, as an **allowlist**.
#:
#: `benchmarks.PERMITTED_ROLES`' form and its argument — a denylist grants every
#: `UserRole` added after today by omission, at import time, with no test failing
#: and no reviewer prompted — applied to a narrower set. This is deliberately a
#: *second* constant rather than a widening of that one: they answer different
#: questions (oversight of colleagues against the analyst's own workspace), they
#: happen to overlap in one member today, and collapsing them would mean a change
#: to either surface's access silently moved the other's.
FRAUD_ANALYTICS_ROLES: Final[frozenset[UserRole]] = frozenset({UserRole.analyst})

#: How many injury types a rate breakdown carries.
#:
#: **This module's own constant, deliberately set to the number
#: `charts.INJURY_TYPE_LIMIT` already cuts the injury-type chart at.** Two views
#: of one dimension must not disagree about where the tail starts: a supervisor
#: reading "8 of 20 injury types" on the portfolio chart and an analyst reading a
#: differently-cut list of the same dimension one route over would have no way to
#: reconcile them, and neither caption would be wrong.
#:
#: Matching the *number* is only half of that, and the half that was wrong first.
#: A cut applied after the caller's sort makes the eight kept rows a function of
#: a display preference: `sort[injuryType]=rate_asc` would have kept the eight
#: injury types with the **lowest** flagged rate — eight buckets with no flagged
#: claim at all — under a heading reading "Flagged-claim rates" and a caption
#: reading "Showing 8 of 20 injury types", with nothing on screen to say which
#: eight. `_breakdown` therefore selects the kept set by the *population*
#: ranking `charts._ranked` uses (claim count descending, then label ascending)
#: and orders only what survives; see its docstring for the rule in full.
#:
#: Restated rather than imported for `rules.CURSOR_PAGE_CEILING`'s reason — the
#: duplication is made safe by a test rather than by a comment, and
#: `tests/test_fraud_analytics.py::test_the_injury_cut_is_the_injury_charts_cut`
#: pins the two together so they cannot drift apart without a failure naming both.
#: A module constant rather than a rules-tier parameter for `charts.py`'s recorded
#: reason: a chart's category count is UX-DR7's *shape*, not a threshold, and
#: moving it changes what the chart is rather than how the business behaves.
#:
#: The employer and handler breakdowns are **uncapped**, `_by_employer`'s reason:
#: both are bounded by the caller's assignment rather than by the data, so a limit
#: would cut a list that is already short and hide a line of business rather than
#: a long tail.
INJURY_TYPE_LIMIT: Final[int] = 8

#: How many distinct red-flag clauses the ranked view carries.
#:
#: Its own number rather than a share of the one above, because it cuts a
#: different kind of tail: injury types are a closed vocabulary of twenty, and
#: clauses are free prose whose count grows with the cache. Published with
#: `truncated` and `total_clauses` beside it, so the cut is never silent —
#: `charts.Distribution`'s contract, restated for a list that is not a
#: distribution.
RED_FLAG_LIMIT: Final[int] = 10

#: The scale a rate is published on. Basis points, not a percentage and not a
#: float: `BenefitParams`' argument, which is that an integer over a fixed scale
#: is exact where a ratio of two counts is a division whose rounding the browser
#: would otherwise have to reproduce. 1 250 is 12.5%.
RATE_BASIS_POINTS: Final[int] = 10_000


class FraudAnalyticsNotPermitted(PermissionError):
    """The caller's role does not carry the analyst-workspace capability.

    **Raised before any claim is read and before any rule document is loaded**,
    which is `BenchmarksNotPermitted`'s ordering rule on a surface where it buys
    slightly more: these routes answer aggregates over a whole portfolio, so a
    refusal that depended on what a scoped read returned would answer differently
    for an analyst with employers and one between assignments — an oracle about
    the assignment table.

    **Why this endpoint has a role gate where `/dashboard/charts` does not.**
    That one publishes distributions over the caller's scope and names nobody, so
    there is no capability to gate and the scope predicate is the whole control.
    This surface is a *persona's workspace* rather than a view of a payload: the
    section it opens is the analyst's, it names handlers in the SIU pipeline and
    in a rate breakdown the way `/dashboard/handler-benchmarks` does, and Epic 7's
    remaining stories hang further analyst-only sections off the same shell. Role
    is what separates "this console has an analyst workspace" from "any
    authenticated caller may read it".

    **Named as an allowlist over exactly `{analyst}`**, which is the deliberate
    inversion of the property Epic 5 shipped: `test_the_analyst_reads_byte_
    identically_to_the_supervisor` is still true of every endpoint it was written
    about, and false here by construction. A supervisor is refused, and that is
    the story rather than a side effect of it.
    """


def require_fraud_analytics_access(ctx: CallerContext) -> None:
    """Refuse a caller outside `FRAUD_ANALYTICS_ROLES`, reading nothing to decide it.

    `require_benchmarks_access`' shape and every one of its arguments: the gate is
    extracted so the *route* can call it above its rule-document loads while the
    service keeps its own check, because capability belongs with the module that
    owns the rows and a router-only check would be one deployment away from a
    second entry point serving the workspace to anybody.

    Deliberately returns `None` rather than a boolean: a predicate invites
    `if not can_read(ctx): pass`, and a caller that forgets the `not` opens the
    surface silently. Raising is the only outcome that cannot be ignored.
    """
    if ctx.role not in FRAUD_ANALYTICS_ROLES:
        raise FraudAnalyticsNotPermitted("Only an analyst can read the fraud analytics workspace.")


# --- the projection and the computers ------------------------------------


@dataclass(frozen=True)
class FraudClaim:
    """One claim, reduced to the nine facts the three aggregates are folded from.

    A projection rather than the ORM entity, for `ChartClaim`'s two reasons: it
    keeps the folds pure and generatable, and it puts every input to the three
    fraud rules and the three breakdown keys next to each other instead of spread
    across a row object carrying forty other columns.

    `fraud_flag` and `fraud_score` sit adjacent because two of the three rules
    read **both** and the third reads only the second — which is the distinction
    this whole module is arranged around, and the one a reader should be able to
    check by looking at one class.

    The two ids ride beside their two display strings, `BenchmarkClaim`'s rule:
    the grouping is on the **id**, because two handlers or two employers may share
    a display name and merging them would be one row averaging two books. The name
    travels so a row can be labelled and a drill chip resolved without a second
    query.
    """

    claim_id: str
    fraud_flag: bool
    fraud_score: int
    stage: Stage
    injury_type: str
    employer_id: int
    employer_label: str
    handler_id: int
    handler_name: str


@dataclass(frozen=True)
class FraudFlags:
    """One claim's three fraud answers, every one of them asked of the registry.

    Declared here rather than reused from `drill_through.DrillFlags`, whose first
    two members these are: what the two modules share is the *registry*, not the
    bundle. That class carries the risk band and two hash-bucket demo flags this
    surface has no use for, and importing it would make this module's folds depend
    on a dataclass owned by a list that ranks and pages — so a sixth member added
    there for the scorer's benefit would silently become an input here.
    `DrillFlags` split from `QueueFlags` one level down for the same reason.
    """

    band: FraudBand
    flagged: bool
    siu_review: bool


@dataclass(frozen=True)
class _Computers:
    """The three registered computers this module folds with, built once.

    A bundle rather than three parameters, for `drill_through._Computers`' reason:
    `flags_of` is called once per claim in a loop, and a three-argument call there
    would put the build order and the call order in two places.
    """

    fraud_band: FraudBandDerivation
    fraud_flagged: FraudFlaggedDerivation
    siu_review: SiuReviewDerivation

    @classmethod
    def of(cls, thresholds: DerivationThresholds) -> "_Computers":
        """Build all three from one parameter block — the registry's whole point."""
        return cls(
            fraud_band=derivations.fraud_band.for_thresholds(thresholds),
            fraud_flagged=derivations.fraud_flagged.for_thresholds(thresholds),
            siu_review=derivations.siu_review.for_thresholds(thresholds),
        )


def flags_of(claim: FraudClaim, computers: _Computers) -> FraudFlags:
    """One claim's three derived fraud values (AD-10).

    Takes the built computers rather than the threshold block, which is the
    difference between one build and one per claim — `drill_through._flags_of`'s
    shape, for its reason.

    Every argument is keyword, which is not decoration on this call: all three
    computers read the same two columns, and a positional `(fraud_score,
    fraud_flag)` transposition would type-check nowhere and a `(fraud_flag,
    fraud_score)` one would type-check everywhere.
    """
    return FraudFlags(
        band=computers.fraud_band.of(fraud_score=claim.fraud_score),
        flagged=computers.fraud_flagged.of(
            fraud_flag=claim.fraud_flag, fraud_score=claim.fraud_score
        ),
        siu_review=computers.siu_review.of(
            fraud_flag=claim.fraud_flag, fraud_score=claim.fraud_score
        ),
    )


# --- the panel -----------------------------------------------------------


@dataclass(frozen=True)
class HandlerCount:
    """One row of a per-handler count — the id, the name, and the figure.

    `handler_id` is the identity and `handler_name` the label,
    `HandlerBenchmark`'s split and its reason: the drill-through filters on
    `filter[handlerId]`, and filtering on a display name would merge two desks
    that share one.
    """

    handler_id: int
    handler_name: str
    count: int


@dataclass(frozen=True)
class FraudPanel:
    """The band distribution, the SIU pipeline, and the rules behind both.

    **The four thresholds travel with the figures**, for the reason
    `PortfolioCharts` publishes its two: the band legend quotes its edges ("High
    ≥ N") and the pipeline caption names the referral cut-off, so a client holding
    either would be a second copy of a rule it cannot see change — and superseding
    the document has to move the segments *and* the captions together. All four
    are read off the derivations that did the deciding, so the published numbers
    are provably the ones the counts were produced at.

    `flagged_claims` is here beside a distribution that does **not** contain it,
    and that is the point rather than an inconsistency: it is the *review*
    population (`fraud_flagged`), the same figure `/dashboard/summary` publishes
    as `fraudFlagged` and `/dashboard/claims?filter[fraudFlagged]=true` reports as
    `total`, and the section's flagged-claims panel is that existing list rather
    than a second one. Publishing the count here is what lets the entry point
    state the size of the list before it is opened, with no second aggregate
    behind it.

    `rules_version` is deliberately **absent**, exactly as it is from
    `PortfolioCharts` and `PortfolioSummary`: it is the identity of the document
    rather than a figure computed from the caseload, and the folds are pure over a
    caseload. The router adds it beside this block from the parameter block it
    loaded.
    """

    by_band: Distribution[CategoryCount]
    siu_by_stage: Distribution[CategoryCount]
    siu_by_handler: Distribution[HandlerCount]
    claims_in_scope: int
    flagged_claims: int
    siu_claims: int
    fraud_band_high_min: int
    fraud_band_med_min: int
    fraud_flag_score_min: int
    siu_fraud_score_min: int


def _banded(counts: Mapping[FraudBand, int]) -> Distribution[CategoryCount]:
    """The band distribution, in the enum's declaration order, **zero-filled**.

    **This is the one place in the codebase where an absent category is emitted
    rather than omitted, and the difference from `charts._declared` is a rule
    rather than a preference.** That helper drops a category the scope does not
    contain because its vocabulary is a *column's*: Jennifer Park's book holds no
    `intake` claim, so an `intake` slice would be an invisible arc describing
    nothing she has.

    `FraudBand` is not a column. It is a rule's answer over a score every claim
    carries, so all three members exist for every portfolio that exists at all,
    and "no claim in this book scored into the high band" is the single most
    valuable thing this chart can say. Omitting it would render a two-segment
    distribution that a reader has no way to distinguish from a build that forgot
    to draw the third — and the reassuring reading is the wrong one.

    The cost is that the two helpers behave differently, which is why this is its
    own function with the difference written down rather than a flag on
    `_declared`: a boolean parameter would put the argument in a call site instead
    of in a docstring, and the next reader would have to reconstruct which way it
    should point.

    `truncated` is `False` and `limit` is `None`: a rule's whole vocabulary is
    three members and there is nothing to cut. `total_categories` is the number of
    members rather than the number that happen to be occupied, for the same
    reason the rows are — the vocabulary is the answer.
    """
    return Distribution(
        items=tuple(CategoryCount(key=band.value, count=counts.get(band, 0)) for band in FraudBand),
        total=sum(counts.values()),
        total_categories=len(FraudBand),
        truncated=False,
        limit=None,
    )


def _pipeline(counts: Mapping[Stage, int]) -> Distribution[CategoryCount]:
    """The SIU population by stage, in `Stage`'s declaration order, absent omitted.

    `charts._declared`'s rule, restated here rather than imported because it is
    that module's private helper and this is a different fold — but restated
    *deliberately unchanged*, because `stage` is a column and this is therefore
    the case that helper's argument was written for. A scope whose SIU claims are
    all in investigation has one segment, and a stage with no referred claim in it
    is a stage this pipeline does not reach rather than a stage that is empty.

    Which is exactly the opposite of `_banded` one function up. The two sit
    adjacent so the difference is legible: the vocabulary of a band is a rule's
    and is always complete; the vocabulary of a pipeline is the lifecycle a claim
    happens to be in.

    Declaration order rather than count order for `_declared`'s reason: a pipeline
    is read left to right and must not reshuffle when a claim moves.
    """
    return Distribution(
        items=tuple(
            CategoryCount(key=stage.value, count=counts[stage])
            for stage in Stage
            if stage in counts
        ),
        total=sum(counts.values()),
        total_categories=len(counts),
        truncated=False,
        limit=None,
    )


def _by_handler(counts: Mapping[int, int], names: Mapping[int, str]) -> Distribution[HandlerCount]:
    """The SIU population by handler: count descending, then name, then id.

    `_ranked`'s ordering over a keyed series instead of a free-text one, and
    **uncapped** for `_by_employer`'s reason: the handlers in a caller's book are
    bounded by the desk rather than by the data (six on the whole seeded
    portfolio), so a limit would cut a list that is already short.

    A handler with no referred claim is **absent**, not a zero row, and that is
    `_pipeline`'s rule rather than `_banded`'s: this is a list of the desks
    carrying SIU work, and a desk carrying none is not a segment of it. The
    denominator a reader needs — how many claims that handler holds — is the rate
    breakdown's answer one route over, published there with its own `claims`
    column precisely so it is on screen rather than inferred.

    The tie-break is total and is not cosmetic: two handlers with equal referral
    counts are reachable on any small desk, and an unstable order would make two
    consecutive requests disagree about who is third. Name first because it is the
    visible column and a reader can check it; the id behind it because two
    handlers can share a display name and the order still has to be total.
    """
    ordered = sorted(counts.items(), key=lambda entry: (-entry[1], names[entry[0]], entry[0]))
    return Distribution(
        items=tuple(
            HandlerCount(handler_id=handler_id, handler_name=names[handler_id], count=count)
            for handler_id, count in ordered
        ),
        total=sum(counts.values()),
        total_categories=len(counts),
        truncated=False,
        limit=None,
    )


def panel_of(caseload: Sequence[FraudClaim], computers: _Computers) -> FraudPanel:
    """The band distribution and the SIU pipeline over one caseload. Pure.

    `charts_of`'s split and its reasons: the arithmetic is database-free and
    Hypothesis-testable, and the one scoped read happens in the async wrapper
    below.

    **One pass accumulating four counters**, not four comprehensions over the same
    list. They would produce the same numbers today and it is the wrong shape:
    "these figures describe one set of claims" becomes structural here, where four
    separate passes would be four opportunities for one of them to be written
    against a filtered copy — which on this surface would mean a pipeline that
    quietly described a different population from the distribution beside it.

    Every band and every flag comes from the computers passed in (AD-10). This
    function holds no threshold, no cut-off and no comparison against a score.
    """
    by_band: dict[FraudBand, int] = {}
    siu_by_stage: dict[Stage, int] = {}
    siu_by_handler: dict[int, int] = {}
    handler_names: dict[int, str] = {}
    flagged = 0
    siu = 0

    for claim in caseload:
        flags = flags_of(claim, computers)
        by_band[flags.band] = by_band.get(flags.band, 0) + 1
        if flags.flagged:
            flagged += 1
        if flags.siu_review:
            siu += 1
            siu_by_stage[claim.stage] = siu_by_stage.get(claim.stage, 0) + 1
            siu_by_handler[claim.handler_id] = siu_by_handler.get(claim.handler_id, 0) + 1
            # Last name wins, and they are all the same: `handler_name` is a
            # column on the joined `app_user` row, so every claim of one handler
            # carries the identical string. Recorded rather than guarded, because
            # a guard here would be asserting a foreign key (`charts_of`'s note
            # on the employer label).
            handler_names[claim.handler_id] = claim.handler_name

    return FraudPanel(
        by_band=_banded(by_band),
        siu_by_stage=_pipeline(siu_by_stage),
        siu_by_handler=_by_handler(siu_by_handler, handler_names),
        claims_in_scope=len(caseload),
        flagged_claims=flagged,
        siu_claims=siu,
        # Read off the derivations that did the deciding rather than off the
        # threshold block, so the published numbers are provably the ones the
        # segments were produced at — `PortfolioCharts`' rule.
        fraud_band_high_min=computers.fraud_band.high_min,
        fraud_band_med_min=computers.fraud_band.med_min,
        fraud_flag_score_min=computers.fraud_flagged.fraud_score_min,
        siu_fraud_score_min=computers.siu_review.fraud_score_min,
    )


# --- the rate breakdowns -------------------------------------------------


class FraudRateSort(StrEnum):
    """The five orders a rate breakdown may be asked for. Closed, and typed.

    A `StrEnum` rather than a free string, and the **type is the validation**:
    `sort[injuryType]=severity` cannot reach this module, because FastAPI coerces
    the query parameter into this enum and answers 422 before the service is
    called. `DrillFilters` makes the identical argument for its facets, and the
    consequence is the same — there is no vocabulary check in this file and there
    must not be one, or the enum would be spelled twice.

    Five values and not more. Each is an order a reader of *this* table could
    plausibly want: the worst rate first, the best rate first, the largest
    absolute exposure first, the biggest denominator first, and alphabetical for
    looking one row up. `flagged_asc` and `claims_asc` are deliberately absent —
    "the desks with fewest flagged claims, ascending" is not a question this
    surface asks, and an enum that offered every direction of every column would
    be a sort language rather than a contract.

    Snake_case values per the enum convention; the UI owns the labels.
    """

    rate_desc = "rate_desc"
    rate_asc = "rate_asc"
    flagged_desc = "flagged_desc"
    claims_desc = "claims_desc"
    label_asc = "label_asc"


#: The order a breakdown is served in when the caller asks for none.
#:
#: The worst rate first, because that is what the table is for: an analyst opens
#: a fraud-rate breakdown to find where the suspicion concentrates, and a default
#: of alphabetical would make the answer a scan rather than a read.
DEFAULT_RATE_SORT: Final[FraudRateSort] = FraudRateSort.rate_desc


@dataclass(frozen=True)
class FraudRateSorts:
    """One order per breakdown — three independent controls, not one.

    A frozen block rather than three loose parameters threaded through two call
    layers, `HandlerPerformance`'s shape for a different kind of input: it keeps
    "which table is sorted how" one object a router builds field by field and a
    test constructs by hand, and it makes adding a fourth breakdown a change to
    this class rather than to every signature between the route and the fold.

    Each defaults independently, which is the behaviour the I/O matrix names:
    sorting the injury-type table must change that table's order and leave the
    other two at their declared defaults.
    """

    injury_type: FraudRateSort = DEFAULT_RATE_SORT
    employer: FraudRateSort = DEFAULT_RATE_SORT
    handler: FraudRateSort = DEFAULT_RATE_SORT


@dataclass(frozen=True)
class _Tally:
    """One breakdown row before it acquires its published shape. Internal.

    `key` is the value the drill-through filters on — the exact stored injury
    type, or an id rendered as a string — and `label` is what a reader sees. They
    coincide for injury type, where the stored string *is* the label, and differ
    for the two id-keyed dimensions, which is the whole reason both are carried.

    The intermediate exists so the five orders are implemented **once**, over one
    shape, rather than three times over three published row types that differ only
    in how they name their subject.
    """

    key: str
    label: str
    flagged: int
    claims: int

    @property
    def rate_bp(self) -> int:
        """`flagged / claims` in basis points, rounded half-up.

        Exact decimal arithmetic and half-up rounding for `benchmarks._percent`'s
        two reasons, one of which bites here: the result is what the table is
        *ordered* by, so a true 1 250 arriving as 1 249.9999999999998 through
        binary division would put two rows in the wrong order with nothing on
        screen to say why.

        `claims` is never zero: a tally exists because a claim was counted into
        it, so there is no division to guard. Stated rather than guarded, because
        a guard would be asserting the loop below.
        """
        return int(
            (Decimal(self.flagged) / Decimal(self.claims) * RATE_BASIS_POINTS).quantize(
                Decimal(1), rounding=ROUND_HALF_UP
            )
        )


@dataclass(frozen=True)
class InjuryTypeRate:
    """One injury type's flagged share, with its denominator on the wire.

    `flagged`, `claims` **and** `rate_bp`, all three, which is the one shape
    decision on this payload worth arguing. A rate alone is uninterpretable over a
    thin bucket — one flagged claim of one is 100%, and so is fifty of fifty — and
    the honest fix is not a suppression rule this module would have to invent, it
    is publishing the denominator so the reader can see what the percentage is a
    percentage *of*. `HandlerBenchmarks.portfolio_composite_days` is on the wire
    for the identical reason one dashboard over.

    `injury_type` is the exact stored string, matched by `filter[injuryType]` with
    no trim, case-fold or merge — `charts.LabelCount`'s ruling, which this
    breakdown inherits because it groups the same column and a canonicalising
    fold here would produce rows the chart never counted.
    """

    injury_type: str
    flagged: int
    claims: int
    rate_bp: int


@dataclass(frozen=True)
class EmployerRate:
    """One employer's flagged share. `InjuryTypeRate`'s three figures, keyed by id.

    The id rather than the label, `EmployerPaid`'s rule: a `short_name` is a
    label and not an identity, and the drill-through has to filter on something
    that cannot collide.
    """

    employer_id: int
    label: str
    flagged: int
    claims: int
    rate_bp: int


@dataclass(frozen=True)
class HandlerRate:
    """One handler's flagged share. `EmployerRate`'s shape over the other id.

    This is the row that puts a figure beside a named colleague, which is exactly
    the discriminator `api/routers/dashboard.py` uses to decide whether a surface
    needs a role gate — and it is one of the reasons this whole section has one.
    """

    handler_id: int
    handler_name: str
    flagged: int
    claims: int
    rate_bp: int


@dataclass(frozen=True)
class RateBreakdown[RowT]:
    """One finished breakdown: the rows in their order, and how they were cut.

    Generic over its row type so the four scalar fields are declared once and a
    consumer still gets the concrete row back — `Distribution`'s arrangement, for
    its reason.

    **No `total` and no `next_cursor`.** `items` *is* the list, which is
    `HandlerBenchmarks`' shape and is what makes the `sort` parameter safe:
    `drill_through.py` refuses a sort on a cursored list because an offset into
    one ranking means nothing against another, and a list with no offset has
    nothing to be ambiguous about. The three breakdowns are twenty, ten and six
    rows on the full portfolio.

    `sort` echoes the order that was applied. It rides along so a stored or
    forwarded response is self-describing and so the UI's control renders the
    server's answer rather than its own last click — the same reason
    `DrillClaims.applied_filters` echoes the facets, and it is read: the
    `<select>` in `web/src/features/dashboard/fraud/FraudRateTables.tsx` takes its
    value from this field once an answer is on screen. The state that argument was
    written for is the failing one — a request that 422s or times out leaves a
    control claiming an order the rows beside it are not in, and a control that
    renders its own last click has no way back from that.

    `limit`, `total_categories` and `truncated` are `Distribution`'s truncation
    contract, restated on a list that is not a distribution: no cut is silent, and
    `truncated` is decided here rather than left to a client comparing two of the
    other fields.
    """

    items: tuple[RowT, ...]
    total_categories: int
    truncated: bool
    limit: int | None
    sort: FraudRateSort


@dataclass(frozen=True)
class FraudRates:
    """The three breakdowns over one scoped book, and the rule behind all of them.

    `fraud_flag_score_min` travels for `FraudPanel`'s reason: the tables' footnote
    quotes the review cut-off every `flagged` column was counted at, so a client
    holding it would be a second copy of a rule it cannot see change.

    `claims_in_scope` and `flagged_claims` are the portfolio-level pair every row
    is a partition of. They make the table checkable from the screen — the
    `claims` column sums to one and the `flagged` column to the other, for the two
    uncapped breakdowns — which is the property that would otherwise need a second
    request to establish.
    """

    by_injury_type: RateBreakdown[InjuryTypeRate]
    by_employer: RateBreakdown[EmployerRate]
    by_handler: RateBreakdown[HandlerRate]
    claims_in_scope: int
    flagged_claims: int
    fraud_flag_score_min: int


def _ordered(tallies: Sequence[_Tally], sort: FraudRateSort) -> list[_Tally]:
    """The rows it is handed, in the caller's order — **a total order, whichever one**.

    Called by `_breakdown` on the rows that survived the cut rather than on the
    whole tally set, which is that function's recorded rule: this decides how a
    table reads and never which rows are on it.

    Every branch ends in `(…, label, key)`, and that is the load-bearing part
    rather than the sort key in front of it. `_ranked`'s recorded argument applies
    unchanged: ties are reachable on real data (three seeded injury types carry
    the same claim count, and a rate of exactly zero is shared by every dimension
    member with no flagged claim at all), so an order decided only by the figure
    would leave the tied rows to whatever the dict happened to hold — a table that
    renders differently on two consecutive requests, from one book, with nothing
    on screen to say why.

    Ascending label because it is the only tie-break a reader can verify from the
    table itself; the key behind it because two employers or two handlers may
    share a display name and the order still has to be total.

    Sorting on `-value` rather than with `reverse=True`, `_ranked`'s reason:
    `reverse=True` would reverse the label order too and give a descending
    secondary key. Both parts of every key have to point the way they are
    documented.
    """
    match sort:
        case FraudRateSort.rate_desc:
            return sorted(tallies, key=lambda row: (-row.rate_bp, row.label, row.key))
        case FraudRateSort.rate_asc:
            return sorted(tallies, key=lambda row: (row.rate_bp, row.label, row.key))
        case FraudRateSort.flagged_desc:
            return sorted(tallies, key=lambda row: (-row.flagged, row.label, row.key))
        case FraudRateSort.claims_desc:
            return sorted(tallies, key=lambda row: (-row.claims, row.label, row.key))
        case FraudRateSort.label_asc:
            return sorted(tallies, key=lambda row: (row.label, row.key))


def _population_ranked(tallies: Sequence[_Tally]) -> list[_Tally]:
    """Which rows a capped breakdown keeps — claim count descending, then label.

    **`charts._ranked`'s ranking, restated over this module's row shape**, and
    restated deliberately unchanged: the injury-type chart on the portfolio
    dashboard and the injury-type rate table in this workspace cut the same
    dimension at the same eight, so they have to cut at the same eight *rows* and
    not merely at the same number. `tests/test_fraud_analytics.py::
    test_the_injury_cut_is_the_injury_charts_cut` pins the limit and
    `test_the_kept_injury_types_are_the_charts_eight_whatever_the_sort` pins the
    set.

    The tie-break is total for `_ranked`'s recorded reason — on the seeded
    portfolio the injury types ranked seventh through eleventh all count five, so
    the top-8 cut lands *inside* a tie — with the key behind the label because two
    id-keyed dimensions may share a display name. The key is never reached for
    injury type, where the key is the label, which is the only dimension this
    function is called for today.
    """
    return sorted(tallies, key=lambda row: (-row.claims, row.label, row.key))


def _breakdown[RowT](
    tallies: Sequence[_Tally],
    sort: FraudRateSort,
    limit: int | None,
    row_of: Callable[[_Tally], RowT],
) -> RateBreakdown[RowT]:
    """Cut, then order, then shape one breakdown. The three tables' one implementation.

    **The two steps are in that order on purpose, and the reverse is a table that
    hides data on a click.** The cut decides *which rows exist* and the sort
    decides *how they read*; letting one control do both means a display
    preference silently changes the population. Concretely: with the sort applied
    first, `sort[injuryType]=rate_asc` returns the eight injury types with the
    lowest flagged rate — typically eight buckets carrying no flagged claim at all
    — under a card headed "Flagged-claim rates", captioned "Showing 8 of 20
    injury types", with the twelve types carrying the actual concentration gone
    and nothing on screen saying which eight are on it. `label_asc` returns the
    alphabetically first eight, for the same reason and with the same silence.

    So the kept set comes from `_population_ranked`, a fixed ranking that does not
    read `sort` at all, and the caller's order is applied to what survives. The
    published `truncated` / `total_categories` / `limit` semantics are unchanged:
    they describe the whole tally set, which is what makes the caption true under
    every order.

    `limit=None` is a breakdown that is never cut — the employer and handler
    tables — and publishes `truncated: False` and `limit: null`. `None` rather
    than a large sentinel, `Distribution.limit`'s ruling: "this list is never cut"
    is a different fact from "cut at a thousand", and a caption quoting a sentinel
    would be quoting a number nobody chose. An uncapped breakdown skips the
    population ranking entirely rather than ranking and slicing at `len`, because
    there is no tail to decide and a ranking nobody cuts on is a sort nobody asked
    for.

    `row_of` maps an ordered tally into the published row type. It is the one
    thing the three tables do differently, which is why it is the one thing passed
    in.
    """
    kept = list(tallies) if limit is None else _population_ranked(tallies)[:limit]
    rows: tuple[RowT, ...] = tuple(row_of(tally) for tally in _ordered(kept, sort))
    return RateBreakdown(
        items=rows,
        total_categories=len(tallies),
        truncated=limit is not None and len(tallies) > limit,
        limit=limit,
        sort=sort,
    )


def _tallied[KeyT](bucket: dict[KeyT, list[int]], key: KeyT, flagged: int) -> None:
    """Add one claim to a `[flagged, claims]` pair, creating the bucket if new.

    A mutable two-element list rather than a frozen pair, and rather than two
    parallel dicts: the two counters are one row's two halves, so a bucket that
    existed in one dict and not in the other would be a row with a numerator and
    no denominator — reachable the moment somebody adds a fourth dimension and
    updates two of the three places. One structure, one `setdefault`, three call
    sites that cannot disagree.
    """
    pair = bucket.setdefault(key, [0, 0])
    pair[0] += flagged
    pair[1] += 1


def rates_of(
    caseload: Sequence[FraudClaim], computers: _Computers, sorts: FraudRateSorts
) -> FraudRates:
    """Flagged-over-total by injury type, employer and handler. Pure.

    `panel_of`'s split and its reasons, over the *review* population: the `flagged`
    column is `derivations.fraud_flagged`, the same rule the Fraud Flags card was
    counted with and the same rule `filter[fraudFlagged]=true` opens — never
    `siu_review`, which is the narrower referral set and would put a rate under a
    heading that promised the wider one. That is the near-miss
    `FraudFlaggedDerivation`'s docstring exists for, and this is the third surface
    it applies to.

    One pass accumulating three pairs of counters, `panel_of`'s rule: three
    breakdowns of one set, so they are folded from one traversal rather than three.
    """
    injury: dict[str, list[int]] = {}
    employer: dict[int, list[int]] = {}
    handler: dict[int, list[int]] = {}
    employer_labels: dict[int, str] = {}
    handler_names: dict[int, str] = {}
    flagged_total = 0

    for claim in caseload:
        flagged = flags_of(claim, computers).flagged
        if flagged:
            flagged_total += 1
        hit = int(flagged)
        _tallied(injury, claim.injury_type, hit)
        _tallied(employer, claim.employer_id, hit)
        _tallied(handler, claim.handler_id, hit)
        # Last label wins, and they are all the same — `panel_of`'s note on the
        # handler name, applied to both joined display columns.
        employer_labels[claim.employer_id] = claim.employer_label
        handler_names[claim.handler_id] = claim.handler_name

    return FraudRates(
        by_injury_type=_breakdown(
            [
                _Tally(key=label, label=label, flagged=pair[0], claims=pair[1])
                for label, pair in injury.items()
            ],
            sorts.injury_type,
            INJURY_TYPE_LIMIT,
            lambda tally: InjuryTypeRate(
                injury_type=tally.label,
                flagged=tally.flagged,
                claims=tally.claims,
                rate_bp=tally.rate_bp,
            ),
        ),
        by_employer=_breakdown(
            [
                _Tally(
                    key=str(employer_id),
                    label=employer_labels[employer_id],
                    flagged=pair[0],
                    claims=pair[1],
                )
                for employer_id, pair in employer.items()
            ],
            sorts.employer,
            None,
            lambda tally: EmployerRate(
                employer_id=int(tally.key),
                label=tally.label,
                flagged=tally.flagged,
                claims=tally.claims,
                rate_bp=tally.rate_bp,
            ),
        ),
        by_handler=_breakdown(
            [
                _Tally(
                    key=str(handler_id),
                    label=handler_names[handler_id],
                    flagged=pair[0],
                    claims=pair[1],
                )
                for handler_id, pair in handler.items()
            ],
            sorts.handler,
            None,
            lambda tally: HandlerRate(
                handler_id=int(tally.key),
                handler_name=tally.label,
                flagged=tally.flagged,
                claims=tally.claims,
                rate_bp=tally.rate_bp,
            ),
        ),
        claims_in_scope=len(caseload),
        flagged_claims=flagged_total,
        fraud_flag_score_min=computers.fraud_flagged.fraud_score_min,
    )


# --- the red-flag frequency view -----------------------------------------


#: How one stored fraud card becomes the clauses this fold counts.
#:
#: **An injected callable rather than an import**, and the reason is the layering
#: rule `tests/test_layering.py` machine-checks: `services/` may never import
#: `agents/`. The stored shape belongs to `agents/schemas.py` — it is what the
#: decoder was constrained to and what the writer dumped — so the reader of that
#: shape belongs there too, and this module receives it as an argument the router
#: supplies. `services/rag/insights.py` takes an injected `InsightGenerator` for
#: exactly this reason and states the argument at length: the call graph runs one
#: way, composition root → `agents/` → `services/` → `data/`, and a second
#: `TypeAdapter` written down here would be a copy of a schema that changes there.
#:
#: **Three answers, and the third is what makes the coverage figure honest.** A
#: list of clauses for a red-flag card, an **empty** list for a low-risk one — it
#: is a fraud narrative and raises coverage while contributing no clause — and
#: `None` for a row this build cannot read, which is excluded and counted. A
#: reader that collapsed the last two would report an unparseable row as covered.
FraudClauseReader = Callable[[Mapping[str, Any]], list[str] | None]


@dataclass(frozen=True)
class RedFlagClause:
    """One clause and how many claims named it.

    `claims` is a count of **distinct claims**, never of mentions: a narrative
    listing one indicator twice is one claim worrying about one thing, and
    counting it twice would make a single model's repetition look like a pattern
    across the book. That is the difference between a frequency view and a word
    count, and it is the only counting rule on this payload.

    `clause` is the **first-seen spelling, whitespace-collapsed**, in the caller's
    claim-id order. Two claims whose clauses differ only in case or in a trailing
    full stop are one row, and the row reads the way the first of them wrote it —
    because displaying the *normalised* form would put a casefolded sentence on
    screen and make the view look like it had been generated by the fold rather
    than by the model.

    Collapsed rather than raw, which is the one difference between what is
    displayed and what the model literally stored: a clause the model wrapped
    across two lines would otherwise be published with the newline and the double
    space in it, which renders identically in HTML and wrongly everywhere else.
    `collapse_clause` carries the argument.
    """

    clause: str
    claims: int


@dataclass(frozen=True)
class FraudRedFlags:
    """The ranked clauses, the coverage behind them, and their age.

    **Not a claim aggregate.** Every figure here describes the *cache*: how many
    claims in scope have a fraud narrative at all, when the oldest and newest of
    them were generated, and how many rows this build could not read. AD-10's rule
    is that model output is rendered with its generation time and never presented
    as claim data, and the practical form of that rule on a portfolio-wide view is
    that the coverage is published beside the ranking. A card showing five clauses
    over a book of a hundred claims and four narratives is saying something very
    different from one showing five over a hundred and a hundred.

    `generated_from` / `generated_to` are both `None` on a cold cache, which is
    the seeded state and the ordinary one: nothing has been generated for a claim
    until a refresh reaches it. A zero-length range spelled as two nulls, rather
    than as an epoch or as "now", because a card cannot draw a range that does not
    exist and must not invent one.

    `unreadable` counts rows whose stored `content` failed re-validation — a row
    written by an older prompt version whose schema has since moved. They are
    excluded from the ranking, from the coverage and from the range, and counted
    here, `api/routers/claims.py:2963`'s tolerance applied to a fold: one bad row
    must not take a portfolio view down, and it must not be invisible either.

    `models` is every distinct model that wrote a readable row, sorted. A portfolio
    view spans generations, so there is no single model to label it with — and the
    honest answer to "which model wrote this?" over a set is the set. Empty when
    nothing was readable, which is when the card has no provenance line to draw.

    No `total` and no cursor, `RateBreakdown`'s shape and its reason: `items` is
    the ranking, cut at `RED_FLAG_LIMIT` and saying so.
    """

    items: tuple[RedFlagClause, ...]
    total_clauses: int
    truncated: bool
    limit: int
    claims_with_insight: int
    claims_in_scope: int
    unreadable: int
    generated_from: datetime | None
    generated_to: datetime | None
    models: tuple[str, ...]


def collapse_clause(clause: str) -> str:
    """One clause as it is **displayed**: internal whitespace collapsed, nothing else.

    Split out from `normalise_clause` so the published spelling and the grouping
    key are folded by the *same* first step rather than by two spellings of it.
    The asymmetry that made this its own function is worth naming: grouping on
    the collapsed text while displaying the raw text means a model that wrapped a
    line writes `"Late  reporting of the\\nnjury"` into the ranking whenever that
    claim happens to sort first by `claim_id` — harmless in HTML, which collapses
    it back, and wrong in a CSV, a log line, a test assertion or anything else
    that reads the payload as text. So both halves collapse, and the *only*
    difference between what is grouped and what is shown is the case and the
    trailing full stop that `normalise_clause` also folds.

    Whitespace collapsing rather than trimming: `str.split()` with no argument
    splits on runs of any whitespace and drops the leading and trailing runs, so
    one expression handles the wrapped line, the double space and the stray
    indent a prompt template left in front of a clause.
    """
    return " ".join(clause.split())


def normalise_clause(clause: str) -> str:
    """The grouping key for one clause: whitespace collapsed, no full stop, casefolded.

    Three normalisations and **no more**, each chosen because it is a difference in
    *transcription* rather than in meaning:

    - internal whitespace collapsed — `collapse_clause`, which is also what the
      published spelling goes through — because a model that wrapped a line and
      one that did not wrote the same clause;
    - a single trailing full stop removed, because a list item punctuated as a
      sentence and one punctuated as a fragment are the same item;
    - `casefold` rather than `lower`, because it is the Unicode-correct fold and
      the clauses are free prose that may not be ASCII.

    The `strip` sits **after** `removesuffix` rather than before it, and it is
    not the no-op it looks like: `collapse_clause` has already removed the outer
    whitespace, so the only thing left for it to take is the space a model wrote
    *in front of* its full stop — `"… on the incident report ."` — which is the
    same transcription difference as the full stop itself and folds with it.
    Before `removesuffix` it really would be dead.

    What is deliberately **not** here is anything that would make two differently
    *worded* clauses equal: no stemming, no stop-word removal, no synonym table,
    no keyword bucketing. Every one of those is the server deciding that two
    sentences mean the same thing, which is a classification with an owner and a
    version, and this fold has neither (AD-2). The module docstring carries the
    argument in full.

    Exported rather than private because the test file restates the rule and
    asserts against it, and because the one thing a reader of this view has to be
    able to check is exactly which differences were folded away.
    """
    return collapse_clause(clause).removesuffix(".").strip().casefold()


@dataclass(frozen=True)
class CachedFraudInsight:
    """One `ai_insight` row of the fraud kind, as the fold reads it.

    A projection rather than the ORM entity or a raw `sa.Row`, `FraudClaim`'s
    reasons: it keeps `red_flags_of` pure and constructible by hand, and it names
    the four columns the fold actually uses in one place instead of leaving them
    implicit in attribute accesses.

    `content` is the stored JSONB **unvalidated** — a `Mapping`, not one of the
    typed insight models — because deciding whether it can be read is the fold's
    job and not the projection's. A projection that validated would have nowhere
    to put a row that failed, and "excluded and counted" is a published figure on
    this surface rather than an internal detail.

    `claim_id` is the *business* id, because the count is of distinct claims and
    the console names a claim by that id everywhere else.
    """

    claim_id: str
    content: Mapping[str, Any]
    generated_at: datetime
    model: str


def red_flags_of(
    rows: Sequence[CachedFraudInsight],
    claims_in_scope: int,
    read_clauses: FraudClauseReader,
) -> FraudRedFlags:
    """The clause frequency fold over one book's cached fraud narratives. Pure.

    Takes a projection rather than a session, `charts_of`'s split: the two scoped
    reads happen in the async wrapper below and everything decided here is decided
    over what they returned.

    **`low_risk` rows raise the coverage and contribute no clause**, and that is
    not a special case bolted on — it is what the stored union means. The
    low-risk variant has no `red_flags` key at all: the model was asked to
    *confirm* that the score sits below both thresholds, and there is nothing in
    its schema for an indicator to hide in. So the reader answers `[]` for it, the
    loop below counts it as coverage, and it adds nothing to the ranking. A fold
    that skipped those rows would publish a coverage figure counting only the
    claims with something to say, which is the one number on this card that must
    not flatter itself.

    A row the reader cannot parse answers `None`, and is excluded and counted
    rather than raised: one superseded prompt version must not take a portfolio
    view down. It is logged with the claim id and nothing else.

    Order is the caller's, which is `Claim.claim_id`, which is what makes
    "first-seen spelling" a property of the data rather than of the database's
    mood.

    **The published coverage pair is reconciled here, and it has to be**, because
    its two halves come from two statements. The wrapper counts the claims with a
    fraud narrative from the insight join and the claims in scope from a separate
    `count_claims_matching`, both under READ COMMITTED, so a claim inserted
    between them lands in the numerator and not in the denominator — and the card
    then reads "5 of 4 claims in this portfolio have a cached fraud narrative",
    which is a coverage above 100% on the one surface whose whole subject is how
    much of the book has been analysed. Taking the larger of the two as the
    denominator is the smallest fix that keeps the pair readable as a sentence: it
    admits the race in the direction that under-states coverage rather than
    over-stating it, and it is decided in the fold so a test can construct the
    skew by hand rather than having to win a race against the database.
    """
    #: normalised clause -> (display spelling, the claims that named it).
    #:
    #: A `set` of claim ids rather than a counter, because the count is of
    #: *distinct claims* and a narrative may legitimately name one indicator
    #: twice. `dict` preserves insertion order, so first-seen is free.
    seen: dict[str, tuple[str, set[str]]] = {}
    claims_with_insight = 0
    unreadable = 0
    stamps: list[datetime] = []
    models: set[str] = set()

    for row in rows:
        clauses = read_clauses(row.content)
        if clauses is None:
            # Content-free by construction: the reader swallows the
            # `ValidationError` because its message quotes the narrative that
            # failed (AD-11), and the log line carries the claim id and nothing
            # else — enough to find the row, never enough to leak what it said.
            # `api/routers/claims.py::_slot` makes the identical choice.
            unreadable += 1
            log.warning("fraud.insight_content_rejected", claim_id=row.claim_id)
            continue
        claims_with_insight += 1
        stamps.append(row.generated_at)
        models.add(row.model)
        for clause in clauses:
            # `setdefault` rather than a `get`/assign pair, so the display
            # spelling is fixed by the first arrival and can never be replaced by
            # a later one — which is the whole of "first-seen spelling" as a
            # property rather than as a convention. The whole entry is bound and
            # the claim id added through it, rather than unpacking a display
            # spelling nothing here reads and then deleting the name: a value the
            # next line has to discard is a name a reader has to account for.
            #
            # `collapse_clause` on the display half, `normalise_clause` on the
            # key: the two differ only by the case and the trailing full stop, so
            # a wrapped line is never published with its newline in it. See
            # `collapse_clause` for why the asymmetry mattered.
            entry = seen.setdefault(normalise_clause(clause), (collapse_clause(clause), set()))
            entry[1].add(row.claim_id)

    #: Count descending, then the **normalised** clause ascending — `_ranked`'s
    #: ordering and its recorded reason. The normalised form is the tie-break
    #: rather than the display form because it is what the rows were grouped on:
    #: two rows tied at one claim each, whose spellings differ only in case, would
    #: otherwise order by an accident of capitalisation.
    ranked = sorted(seen.items(), key=lambda entry: (-len(entry[1][1]), entry[0]))
    return FraudRedFlags(
        items=tuple(
            RedFlagClause(clause=display, claims=len(claim_ids))
            for _key, (display, claim_ids) in ranked[:RED_FLAG_LIMIT]
        ),
        total_clauses=len(ranked),
        truncated=len(ranked) > RED_FLAG_LIMIT,
        limit=RED_FLAG_LIMIT,
        claims_with_insight=claims_with_insight,
        # Never fewer than the numerator — the two counts are two statements and
        # a claim can be inserted between them. See the docstring.
        claims_in_scope=max(claims_in_scope, claims_with_insight),
        unreadable=unreadable,
        # `min`/`max` over the readable rows, and `None` for both on a cold
        # cache — see `FraudRedFlags`. Not `rows[0]`/`rows[-1]`: the read is
        # ordered by claim id, not by generation time.
        generated_from=min(stamps, default=None),
        generated_to=max(stamps, default=None),
        models=tuple(sorted(models)),
    )


# --- the three endpoints' one call each ----------------------------------


def _projection(rows: Sequence[sa.Row[Any]]) -> list[FraudClaim]:
    """`select_drill_rows`' rows as this module's projection. Named, never positional.

    `charts.portfolio_charts`' argument, on a projection carrying two adjacent
    integer ids and two adjacent display strings: `FraudClaim(*row)` would work,
    would be one reordered projection away from grouping fraud rates by employer
    under a handler's name, and would type-check the whole way.

    **`select_drill_rows` rather than a fourth sibling selector.** Story 5.5's
    projection already carries every column these three aggregates read —
    `fraud_flag`, `fraud_score`, `stage`, `injury_type`, both ids and both display
    names — and it is scoped, joined and ordered exactly as this module needs. A
    new selector would be a fifth spelling of one `employer_scope` read for no
    column; `select_priority_rows`' docstring argues the opposite direction (a
    sibling rather than a parameter) for the case where the columns genuinely
    differ, and they do not here. The cost is a handful of columns this module
    never reads, which is a wider SELECT and not a wider *scope*.
    """
    return [
        FraudClaim(
            claim_id=row.claim_id,
            fraud_flag=row.fraud_flag,
            fraud_score=row.fraud_score,
            stage=row.stage,
            injury_type=row.injury_type,
            employer_id=row.employer_id,
            employer_label=row.employer_short_name,
            handler_id=row.handler_id,
            handler_name=row.handler_name,
        )
        for row in rows
    ]


async def fraud_panel(
    db: AsyncSession,
    ctx: CallerContext,
    thresholds: DerivationThresholds,
) -> FraudPanel:
    """The band distribution and the SIU pipeline for the caller's book.

    Takes its parameter block rather than fetching it, `portfolio_charts`'
    signature and its reason: the route loads it once and hands it down, so this
    stays a composition of scope and parameters instead of dragging the rules
    engine into an aggregate that mentions a band.

    One scoped read, one pure fold — and literally so: there is no other `await`
    in this body. `test_the_panel_takes_exactly_one_scoped_read` counts the
    statements.

    Raises `FraudAnalyticsNotPermitted` (403) for any role outside
    `FRAUD_ANALYTICS_ROLES`, **before the read**. The route calls the same gate
    first as well, so the refusal also precedes the rule-document read it makes on
    this function's behalf; the check here stays because capability belongs with
    the service that owns the rows and not with one caller.
    """
    require_fraud_analytics_access(ctx)
    rows = await claim_repo.select_drill_rows(db, ctx)
    return panel_of(_projection(rows), _Computers.of(thresholds))


async def fraud_rates(
    db: AsyncSession,
    ctx: CallerContext,
    thresholds: DerivationThresholds,
    sorts: FraudRateSorts,
) -> FraudRates:
    """The three flagged-rate breakdowns for the caller's book.

    `fraud_panel`'s signature with the sort block added, and the sort block is
    the only thing a caller may send: there is nowhere in it to put an employer, a
    user or an "as", which is what keeps
    `test_query_parameters_cannot_widen_or_change_the_scope` a property of the
    shape rather than of a validator (AD-7).

    One scoped read, one pure fold. The ordering is a total order over an uncapped
    list computed after the read, so a different sort is the same read.
    """
    require_fraud_analytics_access(ctx)
    rows = await claim_repo.select_drill_rows(db, ctx)
    return rates_of(_projection(rows), _Computers.of(thresholds), sorts)


async def fraud_red_flags(
    db: AsyncSession, ctx: CallerContext, read_clauses: FraudClauseReader
) -> FraudRedFlags:
    """The ranked red-flag clauses across the caller's book — read-only (AD-12).

    **Takes no parameter block, because this view reaches no rule.** It groups
    prose and counts claims; there is no threshold, no band and no cut-off in it,
    so the route loads no document on its behalf and the response publishes none.
    That is stated here rather than left to be inferred, because every sibling
    aggregate on this dashboard takes one and a reader will wonder what happened
    to it.

    **Two scoped reads, not one**, and this is the one place in this module where
    the count departs from `charts.py`'s discipline. The published coverage is
    "how many claims in scope carry a fraud narrative, out of how many claims are
    in scope", and the second half of that cannot come from the insight join: a
    claim with no `ai_insight` row produces no row to count. An outer join from
    `claim` would return one row per claim in the portfolio to answer a question
    about a handful of them, and a count is one aggregate statement.
    `test_the_red_flag_aggregate_takes_exactly_two_scoped_reads` pins the number
    so it stays two rather than growing quietly. Two statements under READ
    COMMITTED can disagree about a claim inserted between them, which is why
    `red_flags_of` reconciles the pair rather than publishing whatever the two
    reads happened to see.

    **`read_clauses` is injected rather than imported**, which is the layering
    rule this package is arranged around: `services/` may never import `agents/`,
    and the stored card's shape is `agents/schemas.py`'s. The router supplies
    `agents.schemas.read_fraud_clauses`; a test supplies whatever it wants to
    prove the fold's tolerance with. See `FraudClauseReader`.

    Nothing here writes. `ai_insight` is `services/rag`'s table (AD-12) and this
    module holds no command, triggers no refresh and has no session write in its
    call graph — the cache is read exactly as a claim column is read, and rendered
    with its generation time exactly as a claim column is not.
    """
    require_fraud_analytics_access(ctx)
    rows = await insight_repo.select_fraud_insights(db, ctx)
    counted = await claim_repo.count_claims_matching(db, ctx, {"claims": sa.true()})
    # Named rather than positional, `_projection`'s argument over four columns of
    # which two are strings: `CachedFraudInsight(*row)` would type-check and would
    # be one reordered projection away from grouping clauses by model name.
    return red_flags_of(
        [
            CachedFraudInsight(
                claim_id=row.claim_id,
                content=row.content,
                generated_at=row.generated_at,
                model=row.model,
            )
            for row in rows
        ],
        counted["claims"],
        read_clauses,
    )


__all__ = [
    "DEFAULT_RATE_SORT",
    "FRAUD_ANALYTICS_ROLES",
    "INJURY_TYPE_LIMIT",
    "RED_FLAG_LIMIT",
    "CachedFraudInsight",
    "FraudClauseReader",
    "EmployerRate",
    "FraudAnalyticsNotPermitted",
    "FraudClaim",
    "FraudFlags",
    "FraudPanel",
    "FraudRateSort",
    "FraudRateSorts",
    "FraudRates",
    "FraudRedFlags",
    "HandlerCount",
    "HandlerRate",
    "InjuryTypeRate",
    "RateBreakdown",
    "RedFlagClause",
    "collapse_clause",
    "flags_of",
    "fraud_panel",
    "fraud_rates",
    "fraud_red_flags",
    "normalise_clause",
    "panel_of",
    "rates_of",
    "red_flags_of",
    "require_fraud_analytics_access",
]
