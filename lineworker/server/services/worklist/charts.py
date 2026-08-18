"""The portfolio's seven analytics surfaces — one scoped read, finished series
(FR-SUP-4/C, UX-DR7).

The prototype builds all seven inside `renderSV` (line 1001) by folding the
global claim array in the browser: two `forEach` passes accumulating injury
types and states into plain objects, a third summing paid columns per employer,
three `filter` counts per donut, and `Object.entries(…).sort(…).slice(0, 8)` to
cut the top-N — over data it had already filtered client-side to whatever
persona was selected. Both halves are what this module replaces. The *set* is
the scoped repository's (AD-7) and the *figures* are decided here (AD-1), so the
SPA receives seven finished series it has no way to have invented and no way to
disagree with the cards above them about.

## The seven surfaces, and where each number actually comes from

- **Settlement status** — a distribution over `Claim.stage`. Not over
  `Claim.status`, and that is Story 5.1's ruling rather than a new one: the KPI
  card immediately above this donut counts `stage = settled` (62 of the seeded
  100, where `status = settled_closed` counts 54), and the queue's four groups
  and the SLA strip's settled segment group the same way. A donut on `status`
  beside a card on `stage` would put two different numbers for one quantity
  inches apart on one screen, which is the failure AD-10 exists to prevent. See
  `summary.summary_of`'s Settled & Closed bullet for the full argument;
  `test_the_stage_donut_agrees_with_the_three_cards_it_shares_a_screen_with`
  is what stops it drifting back.
- **Severity distribution** — a distribution over `derivations.risk`, the same
  registered band the High Risk card, the queue dot and the detail gauge read
  (AD-10). The band's boundaries are not known here and must not become known
  here; they travel *out* on the block below so a legend caption can quote them.
- **SLA performance** — `sla.strip_of` over `sla.sample_of(row)` for these same
  rows, which is the `/stats/sla` top bar's own fold (AD-2). Not "the same
  arithmetic": the same function, over the same scope, producing the same four
  `SlaMetric`s. This module never names an SLA duration column —
  `test_nothing_outside_the_worklist_aggregation_reads_the_sla_source_columns`
  greps for those names, comments and docstrings included — so it *cannot* have
  averaged anything itself.
- **Recovery status** — a distribution over `Claim.return_status`.
- **Injury type** and **claims by state** — distributions over two free `Text`
  columns, ranked and truncated here (see `_ranked`).
- **Total paid by employer** — `derivations.total_paid` per claim, summed per
  employer, ranked by spend. The same derivation Story 5.1's Total Paid card
  uses, including its recorded exclusion of `status = paid` bills; see
  `summary.summary_of`'s Total Paid bullet. Using anything else would put two
  different "total paid" figures on one screen, and
  `test_the_employer_series_sums_to_the_total_paid_card` is what makes the
  choice load-bearing rather than incidental — if someone later fixes the
  derivation, the card and the bars move together or the test fails.

## Why the browser receives finished series

AD-1 forbids the SPA aggregating, truncating, ranking or percentaging raw
claims, and every one of those verbs appears in `renderSV`. What arrives here
instead is a list of labels and integers already in the order they are drawn in,
already cut to the limit, already carrying the totals a caption needs. Recharts
is then free to turn a count into an arc or a bar width — that is what a chart
library is — but there is no percentage, ratio, ordering or "other" bucket
anywhere on the wire for it to have invented, because there is none here that
was not computed over the whole scoped set.

The corollary is that a truncation must never be silent. Every ranked series
publishes `limit`, `total_categories` and `truncated` beside its items, so the
caption reads "8 of 20" from the response rather than from a constant the client
would have to hold — and a scope with fewer categories than the limit says so.

## Absent categories are omitted, not zeroed

Jennifer Park's 27 claims contain no `intake` stage. Emitting
`{key: "intake", count: 0}` would put an invisible slice in a donut and a
zero-length bar in a chart; omitting it means the series lists exactly what the
scope contains. The cost is that a consumer cannot assume a fixed row count,
which is why the UI's label maps are lookups over whatever arrived rather than
a fixed tile array. The empty-scope case is the same rule at its limit: `items`
empty, `total` zero, and four `no_data` SLA tiles — never seven zero rows.

## One pure fold, one `await`

`summary.py`'s split and `benchmarks.py`'s composition, for their reasons: the
arithmetic is database-free and Hypothesis-testable, the scoped read happens
exactly once, and every parameter block arrives as an argument rather than being
fetched here — so this stays a composition of scope and parameters instead of
dragging the rules engine into an aggregate that mentions a band.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings
from data.context import CallerContext
from data.models import Claim
from data.models.enums import ReturnStatus, Stage
from data.repositories import claims as claim_repo
from rules.parameters import DerivationThresholds
from services import derivations
from services.derivations import RiskBand, RiskDerivation, TotalPaidDerivation
from services.worklist import sla

#: How many injury types and how many states a ranked series carries.
#:
#: **Module constants rather than a rules-tier document, deliberately.** AD-8
#: puts "weights, thresholds, bands, caps" in JDM, and Epic 5's rules-tier
#: numbers are named: the risk and fraud thresholds, the cycle-time deviation
#: bands, the worklist cap and the complexity blend. A chart's category count is
#: none of those. It is UX-DR7's *shape* — "top 8", "top 10" — and moving it
#: changes what the chart is rather than how the business behaves; a new
#: document plus a migration plus a loader plus validation, for two integers the
#: design contract fixes, would be ceremony with no reader.
#:
#: What they must not be is invisible. Both travel out on every ranked series
#: beside `total_categories` and `truncated`, so a caption can say "8 of 20"
#: without any client restating the number — see `Distribution`.
INJURY_TYPE_LIMIT: Final[int] = 8
STATE_LIMIT: Final[int] = 10


@dataclass(frozen=True)
class CategoryCount:
    """One slice of an enum-keyed distribution.

    `key` is the enum's wire value (snake_case, per the Enums convention), never
    a display label: the UI owns "Settled & Closed" and "Fully Recovered", and a
    server that shipped them would be deciding copy over a contract.
    """

    key: str
    count: int


@dataclass(frozen=True)
class LabelCount:
    """One bar of a free-text distribution.

    `label` rather than `key` because there is no enum behind it and nothing to
    look up: `injury_type` and `state` are `Text` columns with no reference
    table, so the stored value *is* the label. It is grouped on exactly as
    stored, with no trimming, case-folding or merging of near-duplicates —
    canonicalizing free text is a data-quality decision with an owner, not
    something an aggregate may invent on the way to drawing a bar.
    """

    label: str
    count: int


@dataclass(frozen=True)
class EmployerPaid:
    """One bar of the total-paid-by-employer chart, in integer cents.

    `employer_id` rides along beside the label because the label is a
    `short_name` and a name is not an identity — two employers could be given
    the same short name tomorrow, and Story 5.5's drill-through needs to filter
    on something that cannot collide. Nothing renders it today.
    """

    employer_id: int
    label: str
    paid_cents: int


@dataclass(frozen=True)
class Distribution[ItemT]:
    """A finished series: the items to draw, and everything a caption needs.

    Generic over its item type so the four scalar fields are declared once and
    a consumer still gets the concrete item back. The three item types are not
    interchangeable — a donut slice, a labelled bar and an employer's spend
    answer different questions — but "how many of how many, and was it cut" is
    the same question for all of them.

    - `items` — in the order they are drawn. Enum-keyed series are in their
      enum's declaration order (a legend has a fixed reading order and must not
      reshuffle when a count changes); ranked series are count descending, then
      label ascending. Either way the order is decided here, so no client sorts.
    - `total` — the total of the quantity this series distributes: a claim count
      for the five counted series, and **cents** for the employer series, which
      distributes money rather than claims. Stated rather than left to be
      inferred, because it is the one field whose unit varies: the donut's
      centre number and the employer chart's "of $N" caption are the same field
      answering two questions.
    - `total_categories` — how many distinct categories the scope contains,
      *before* any limit. This is what makes a truncation legible.
    - `truncated` — whether `items` is shorter than `total_categories`. Derived
      here rather than left to a client comparing two of the fields above,
      which is precisely the arithmetic AD-1 removes from the browser.
    - `limit` — the cap that was applied, or `None` for a series that has none.
      `None` rather than a large sentinel: "this series is never cut" is a
      different fact from "this series is cut at a thousand", and a caption
      that quoted a sentinel would be quoting a number nobody chose.
    """

    items: tuple[ItemT, ...]
    total: int
    total_categories: int
    truncated: bool
    limit: int | None


@dataclass(frozen=True)
class PortfolioCharts:
    """The seven surfaces over one scoped caseload, plus the bands behind one of them.

    **The two severity boundaries travel with the figures**, for the reason
    `PortfolioSummary` publishes its two thresholds: the severity donut's legend
    quotes "High (≥ N)", so a client holding that number would be a second copy
    of a rule it cannot see change, and superseding the document has to move the
    slice *and* the caption together. They are read off the derivation that did
    the banding rather than off `DerivationThresholds`, so the published numbers
    are provably the ones the fold used.

    `rules_version` is deliberately **absent**, exactly as it is from
    `PortfolioSummary`: it is the identity of the document rather than a figure
    computed from the caseload, and `charts_of` is pure over a caseload. The
    router adds it beside this block from the parameter block it loaded.

    `sla` is a mapping rather than four named fields so it is the *same shape*
    `sla.strip_of` and `sla.sla_strip` return — the router validates each metric
    into the response model `/stats/sla` already publishes, so the dashboard
    tiles and the top bar cannot come to disagree without disagreeing in
    `sla.py` first.
    """

    by_stage: Distribution[CategoryCount]
    by_severity: Distribution[CategoryCount]
    by_recovery_status: Distribution[CategoryCount]
    by_injury_type: Distribution[LabelCount]
    by_employer: Distribution[EmployerPaid]
    by_state: Distribution[LabelCount]
    sla: Mapping[sla.SlaMetricKey, sla.SlaMetric]
    high_risk_severity_min: int
    med_risk_severity_min: int


@dataclass(frozen=True)
class ChartClaim:
    """One claim, reduced to the eleven facts the seven surfaces are folded from.

    A projection rather than the ORM entity, for `PortfolioClaim`'s two reasons:
    it keeps `charts_of` pure and generatable, and it puts the series
    definitions next to each other instead of spread across a row object.

    The three `paid_*` fields are named exactly as `derivations.PaidColumns`
    declares them, which is the whole point of that protocol being structural —
    this class satisfies it by shape, so `total_paid` adds up a row projection
    without this module importing an ORM entity to add three integers.

    `sample` is an `SlaSample` rather than loose columns because the SLA
    aggregation owns those facts and their meaning (AD-2). Nothing here unpacks
    it; it is passed straight back to `sla.strip_of`. `stage` and
    `return_status` sit beside it as the raw enums because two of the
    distributions group on them directly, and `SlaSample` reduces both to
    booleans the strip needs and a donut cannot use.
    """

    stage: Stage
    return_status: ReturnStatus
    severity_score: int
    injury_type: str
    state: str
    employer_id: int
    employer_label: str
    paid_indemnity: int
    paid_medical: int
    paid_expense: int
    sample: sla.SlaSample


def _declared[MemberT: StrEnum](
    counts: Mapping[MemberT, int], members: Sequence[MemberT]
) -> Distribution[CategoryCount]:
    """An enum-keyed distribution, in the enum's declaration order.

    **Declaration order rather than count order, and that is a decision.** A
    ranked donut reshuffles its legend the moment two categories cross, so a
    reader who learned that the green slice is second has to re-learn it every
    time the portfolio moves; the three enum-keyed series here have a small,
    fixed vocabulary and a legend is a reading order. The two free-text series
    below are the opposite case — twenty categories with no natural order — and
    are ranked.

    Categories the scope does not contain are omitted rather than emitted with a
    zero, so `members` is a filter over what arrived rather than the shape of
    the answer. `truncated` is `False` and `limit` is `None`: an enum series
    shows every category it has.
    """
    return Distribution(
        items=tuple(
            CategoryCount(key=member.value, count=counts[member])
            for member in members
            if member in counts
        ),
        total=sum(counts.values()),
        total_categories=len(counts),
        truncated=False,
        limit=None,
    )


def _ranked(counts: Mapping[str, int], limit: int) -> Distribution[LabelCount]:
    """A free-text distribution: count descending, then label ascending, then cut.

    **The tie-break is load-bearing on the data that exists.** On the full
    seeded portfolio the injury types ranked seventh through eleventh all count
    five, and the states ranked ninth through eleventh all count five — so both
    the top-8 and the top-10 cut land *inside* a tie. An implementation that
    left the order to the dict, to the database, or to a sort keyed on the count
    alone would produce a different chart on a different day, from the same
    hundred claims, with nothing on screen to say why. Ascending label is chosen
    because it is the only tie-break a reader can verify from the chart itself.

    Sorting on `-count` rather than with `reverse=True`, because `reverse=True`
    would reverse the label order too and give a descending secondary key. Both
    parts of the key have to point the way they are documented.
    """
    ordered = sorted(counts.items(), key=lambda entry: (-entry[1], entry[0]))
    return Distribution(
        items=tuple(LabelCount(label=label, count=count) for label, count in ordered[:limit]),
        total=sum(counts.values()),
        total_categories=len(counts),
        truncated=len(counts) > limit,
        limit=limit,
    )


def _by_employer(
    paid_cents: Mapping[int, int], labels: Mapping[int, str]
) -> Distribution[EmployerPaid]:
    """The employer spend series: paid descending, then label ascending.

    `_ranked`'s ordering over money instead of counts, and **uncapped**: the
    employers in a caller's book are bounded by the assignment rather than by
    the data (ten on the whole seeded portfolio, three for a scoped supervisor),
    so a limit would cut a list that is already short and would hide a line of
    business rather than a long tail.

    An employer whose in-scope claims have all paid nothing is a row with
    `paid_cents: 0`, not an absent one. The chart's subject is "the employers in
    this book and what they have cost", and an employer that has cost nothing so
    far is an answer to that; dropping it would be the same silent omission the
    truncation fields exist to prevent, one level down.

    `total` is the sum of the spend, not a claim count — see `Distribution`. It
    is what makes `test_the_employer_series_sums_to_the_total_paid_card`
    checkable against `PortfolioSummary.total_paid_cents` without re-adding the
    rows.
    """
    ordered = sorted(paid_cents.items(), key=lambda entry: (-entry[1], labels[entry[0]]))
    return Distribution(
        items=tuple(
            EmployerPaid(employer_id=employer_id, label=labels[employer_id], paid_cents=cents)
            for employer_id, cents in ordered
        ),
        total=sum(paid_cents.values()),
        total_categories=len(paid_cents),
        truncated=False,
        limit=None,
    )


def charts_of(
    caseload: Sequence[ChartClaim],
    targets: Mapping[sla.SlaMetricKey, sla.SlaTarget],
    risk: RiskDerivation,
    total_paid: TotalPaidDerivation,
) -> PortfolioCharts:
    """The seven surfaces over one caseload. Pure — no session, no clock.

    `summary_of`'s split and its reasons: the arithmetic is database-free and
    Hypothesis-testable, and the one scoped read happens in the async wrapper
    below.

    **One pass accumulating six counters, then one shaping step each.** Six
    separate comprehensions over the same list would produce the same numbers
    today and is the wrong shape: "these six distributions describe one set of
    claims" becomes structural here, where it would be six opportunities for one
    of them to be written against a filtered copy. `summary_of` makes the same
    argument for its ten figures.

    Both bands and every total come from the computers passed in (AD-10). This
    function holds no threshold, no cut-off and no comparison against a score,
    and `test_no_module_outside_the_registry_hardcodes_the_band` greps this file
    — comments included — for a band boundary precisely because the reason it
    can is that there is nothing here to find.
    """
    by_stage: dict[Stage, int] = {}
    by_severity: dict[RiskBand, int] = {}
    by_recovery: dict[ReturnStatus, int] = {}
    by_injury: dict[str, int] = {}
    by_state: dict[str, int] = {}
    employer_paid: dict[int, int] = {}
    employer_labels: dict[int, str] = {}

    for claim in caseload:
        by_stage[claim.stage] = by_stage.get(claim.stage, 0) + 1
        band = risk.of(claim.severity_score)
        by_severity[band] = by_severity.get(band, 0) + 1
        by_recovery[claim.return_status] = by_recovery.get(claim.return_status, 0) + 1
        by_injury[claim.injury_type] = by_injury.get(claim.injury_type, 0) + 1
        by_state[claim.state] = by_state.get(claim.state, 0) + 1
        employer_paid[claim.employer_id] = employer_paid.get(claim.employer_id, 0) + total_paid.of(
            claim
        )
        # Last label wins, and they are all the same: `employer_short_name` is a
        # column on the joined `employer` row, so every claim of one employer
        # carries the identical string. Recorded rather than guarded, because a
        # guard here would be asserting a foreign key.
        employer_labels[claim.employer_id] = claim.employer_label

    return PortfolioCharts(
        by_stage=_declared(by_stage, tuple(Stage)),
        by_severity=_declared(by_severity, tuple(RiskBand)),
        by_recovery_status=_declared(by_recovery, tuple(ReturnStatus)),
        by_injury_type=_ranked(by_injury, INJURY_TYPE_LIMIT),
        by_employer=_by_employer(employer_paid, employer_labels),
        by_state=_ranked(by_state, STATE_LIMIT),
        # The top bar's own fold, over the same rows (AD-2). Not a second SLA
        # aggregation and not a re-averaging of one: `strip_of` is called once,
        # here, with the caseload's samples, and what it returns is published
        # unchanged. An empty caseload gets four `no_data` tiles from it, which
        # is the honest answer and the one the strip already gives.
        sla=sla.strip_of([claim.sample for claim in caseload], targets),
        high_risk_severity_min=risk.high_min,
        med_risk_severity_min=risk.med_min,
    )


async def portfolio_charts(
    db: AsyncSession,
    ctx: CallerContext,
    thresholds: DerivationThresholds,
    settings: Settings,
) -> PortfolioCharts:
    """The seven chart surfaces for the caller's book — the endpoint's one call.

    Takes its parameter block and its settings rather than fetching either,
    `portfolio_summary`'s and `handler_benchmarks`' signature and their reason:
    the route loads them once and hands them down, so this stays a composition
    of scope and parameters. `settings` arrives because the SLA targets are
    deployment configuration and `sla.targets_for` is the one place they become
    targets.

    One scoped read, one pure fold — and literally so: there is no other `await`
    in this body. The projection is `sla.SAMPLE_COLUMNS` widened with the seven
    columns the six distributions need beyond the cycle time, and the employer's
    display name comes from the repository's join rather than from a second
    query per row.

    **`stage` and `return_status` are read off the row and are not re-listed in
    the projection.** `sla.SAMPLE_COLUMNS` already carries both — the strip
    needs them to decide "settled" and "fully recovered" — so naming them again
    here would add a duplicate column to the SELECT and, worse, would suggest
    this module has its own reason to read them. It does: two of the
    distributions group on them. But it takes them from the same projection the
    strip does, so the donut and the strip can never be looking at two different
    readings of one row.

    No role appears anywhere in this path. Supervisor, analyst and handler take
    the identical scoped route through the repository, which is the whole of
    AD-7 on this surface — see the route's docstring for why this endpoint has
    no gate where `/dashboard/handler-benchmarks` has one.
    """
    rows = await claim_repo.select_claim_columns_with_employer(
        db,
        ctx,
        [
            *sla.SAMPLE_COLUMNS,
            Claim.severity_score,
            Claim.injury_type,
            Claim.state,
            Claim.employer_id,
            Claim.paid_indemnity,
            Claim.paid_medical,
            Claim.paid_expense,
        ],
    )
    # Named rather than positional (`ChartClaim(*row)`), which would work and
    # would be one reordered projection away from charting injury types by state
    # — both columns are free text, so a swap type-checks, runs, and produces
    # two wrong charts with nothing to say so. `summary_of`'s argument, on a
    # projection where the two most confusable columns sit adjacent.
    caseload = [
        ChartClaim(
            stage=row.stage,
            return_status=row.return_status,
            severity_score=row.severity_score,
            injury_type=row.injury_type,
            state=row.state,
            employer_id=row.employer_id,
            employer_label=row.employer_short_name,
            paid_indemnity=row.paid_indemnity,
            paid_medical=row.paid_medical,
            paid_expense=row.paid_expense,
            sample=sla.sample_of(row),
        )
        for row in rows
    ]
    return charts_of(
        caseload,
        sla.targets_for(settings),
        derivations.risk.for_thresholds(thresholds),
        derivations.total_paid.for_thresholds(thresholds),
    )
