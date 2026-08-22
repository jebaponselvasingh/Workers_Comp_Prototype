"""The analyst workspace's Financial section — what the book costs, and how it
is reserved (FR-AN-5, Story 7.4).

Epic 7 shipped three sections that can narrow a portfolio nine ways and still
cannot say what it costs. `charts.portfolio_charts` publishes exactly one money
figure — `by_employer.paid_cents` — and nothing anywhere sums a reserve, sums
paid-plus-reserve, counts Epic 3's reserve verdict over a book, or compares a
surgical claim with a non-surgical one. This module is the answer, and it is
three folds rather than one: the money, the reserving quality, and the two cost
drivers the story names.

Structurally it is `fraud.py`'s shape — a frozen row projection built **named**,
frozen results, pure folds, thin async wrappers whose parameter blocks arrive as
arguments — and it inherits that module's gate rather than declaring a second
one. There is exactly one analyst-workspace allowlist and it is
`fraud.FRAUD_ANALYTICS_ROLES`; a fourth section carrying a fourth spelling of it
is how a future `UserRole` gets admitted by one of them.

## Three money words, three quantities, and none of them is renamed here

    paid_cents      = derivations.total_paid          (the static paid_* columns)
    reserve_cents   = claim.reserve                   (the column)
    projected_cents = derivations.total_claim_projected(paid, reserve)

`paid_cents` is the **registered `total_paid` derivation** and deliberately not
`paid_to_date`, because this section has to agree with the figures already on
screen: Epic 5's Total Paid KPI card and 5.3's by-employer chart both fold
`total_paid`, and a decomposition that summed a different notion of "paid" would
put two portfolio totals one route apart. `deferred-work.md` records what that
costs — the paid columns are zero on all 38 open seeded claims while their bills
and expenses carry about 20% of the headline — as a **product decision with an
owner**, quantified, explicitly not a dev-time correction. This story inherits
the finding and footnotes it; it does not fix it, because fixing it moves Epic
5's cards with it.

`projected_cents` is the registered `total_claim_projected` derivation and is
published under that name rather than as "incurred". The epic's word is
"incurred"; the case file already uses "Total incurred" for the paid-only figure;
`claim_money.py` records the discrepancy and `claim_financials.py` refuses in
writing to spread it. This module adopts the refusal rather than the label — the
card says what the figure sums, and the naming question stays with its owner.

## Nothing here computes a money figure of its own

Every claim's contribution comes from a registered derivation (AD-10) and every
verdict from `services/financials/reserve.py` (AD-2). What this module does is
**add integers and count members**, which is the only arithmetic AD-1 leaves on
the server side of the line. There is no ratio, no percentage and no float
anywhere in it: the one division is the cohort average, and it is floor
division over cents.

## The reserve distribution is a *count of Epic 3's verdicts*

`adequacy_of` takes a mapping of verdicts and counts them. It holds no band, no
ratio, no comparison against `light_ratio_bp` and no reading of a reserve — the
verdicts arrive from `reserve.reserve_checks_for_claims`, which folds stored
rows through `reserve_check_from_rows`, which is the same function the case
file's chip and the Bills tab's summary reach the verdict through. AC 2 says
"never a re-derivation" and the only checkable form of that is *agreement*:
`tests/test_financial_decomposition.py` asserts that the bucket this
distribution puts a sampled claim in is the verdict `reserve_check_for_claim`
returns for that same claim.

**All five members are published, not three.** The AC names Light/Adequate/Heavy
and `ReserveVerdict` has five, two of which — `closed_final` (settled, no
exposure left) and `indeterminate` (bills not on file) — are not band answers at
all. On the seeded book `closed_final` alone holds 62 of 100 claims, so a
three-bucket chart would have shown a total that contradicted `claims_in_scope`
on a card headed "portfolio". That is `fraud._banded`'s zero-fill rule reaching
its fifth surface: a *rule's* vocabulary is complete for every book, and "no
claim in this segment is under-reserved" is an answer rather than an absent
category.

## Averages are `None` for an empty cohort, never `0`

`trends.TrendPoint` established the sentinel and the argument is the same one:
a mean over an empty set is not zero, and a cohort with no claims that reported
`$0` average projected cost would read as a cohort that costs nothing. Floor
division rather than rounding, because the figure is a *cents* average and
half-up rounding on money that nothing multiplies would be precision the
underlying data does not have.

## Aggregates fold in Python over scope-predicated reads (AD-7)

`data/repositories/claims.py` refuses a `predicate` parameter twice and in
writing, and this module does not reopen it: `employer_scope(ctx)` is on the
WHERE clause of the one read each service makes, and the `Segmentation`, the
breakdown dimension and both cost-driver cohorts are decided in Python over the
rows it returned. Two of the ten segmentation dimensions are registered
derivations, so a SQL narrowing would put part of one filter in `data/` and the
rest here — and the reserve verdict is not a column at all and cannot become one
(`tests/test_no_derived_columns.py`).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models import Claim, Employee, Employer
from data.models.enums import Disability, Gender, RecoveryWindow, Stage
from data.repositories import claims as claim_repo
from rules.parameters import DerivationThresholds, ReserveBands
from services import derivations
from services.derivations import TotalClaimProjectedDerivation, TotalPaidDerivation
from services.financials.reserve import (
    MissingReserveVerdict,
    ReserveCheck,
    ReserveVerdict,
    reserve_checks_for_claims,
)
from services.worklist.fraud import require_fraud_analytics_access
from services.worklist.segmentation import (
    SEGMENTATION_WIRE_KEYS,
    Segmentation,
    VocabularyDivergence,
)
from services.worklist.segmentation import Computers as SegmentationComputers
from services.worklist.segmentation import narrowed as segmentation_narrowed

#: How many breakdown groups the wire carries.
#:
#: **A module constant rather than a rules-tier parameter**, `charts.py`'s
#: recorded ruling for `INJURY_TYPE_LIMIT` and `STATE_LIMIT`: a chart's category
#: count is UX-DR7's *shape*, and moving it changes what the chart is rather than
#: how the business behaves. AD-8's "weights, thresholds, bands, caps" is about
#: numbers the business tunes, and nothing about this one is a business rule.
#:
#: Its own number rather than a reuse of either chart cap, because it cuts a
#: different kind of tail: those two cut one dimension each (twenty injury types,
#: ten states) and this one cuts **whichever of ten dimensions the analyst
#: selected** — `icd10` alone carries far more distinct values than any chart
#: series does. Twelve is a bar chart a reader can still scan, and the cut is
#: never silent: `group_count` and `truncated` travel with the rows so the card's
#: caption says "top 12 of N", which is `Distribution`'s contract.
BREAKDOWN_LIMIT: Final[int] = 12


class BreakdownDimension(StrEnum):
    """Which dimension the money is broken down by — the segmentation's own ten.

    **The members are the camelCase *facet* spellings, and that is the one place
    in this codebase where an enum's values are not snake_case.** The convention
    exists so a wire value is a stable token whose copy the UI owns; these values
    are not tokens of that kind at all — they *are* facet names, and the whole
    point of Story 7.3 is that a facet has exactly one spelling. `groupBy=
    severityBand` has to be the same word as `filter[severityBand]`, because the
    client composes each group's drill target and its chip label from that one
    key: the group whose key is an employer id opens `filter[employerId]`, and a
    snake_case member here would need a translation table whose only job is to be
    correct forever in both directions. That is the layer `segmentation.py` was
    written to abolish, and re-introducing it for a naming convention would be
    trading a real invariant for a cosmetic one.

    Pinned to `SEGMENTATION_WIRE_KEYS` at import — an eleventh dimension added to
    `Segmentation` and forgotten here would leave a picker the analyst cannot
    group by, and a member spelled differently from its facet would produce a
    breakdown whose groups drill to the wrong list.
    """

    severityBand = "severityBand"  # noqa: N815 - the facet's own spelling; see above
    injuryType = "injuryType"  # noqa: N815
    state = "state"
    employerId = "employerId"  # noqa: N815
    disability = "disability"
    sector = "sector"
    region = "region"
    icd10 = "icd10"
    ageGroup = "ageGroup"  # noqa: N815
    gender = "gender"


#: The dimension a request that names none is broken down by.
#:
#: The employer, because that is the only breakdown this console already ships a
#: money chart for (`charts._by_employer`), so an analyst opening the section
#: cold sees the figures they can reconcile against a screen they already know.
DEFAULT_BREAKDOWN: Final[BreakdownDimension] = BreakdownDimension.employerId

# The vocabulary guard, at import, and an explicit `raise` rather than an
# `assert` for `segmentation.VocabularyDivergence`' recorded reason: `python -O`
# strips asserts, and "the group key and the filter key are one string" is the
# whole contract of this enum rather than a debugging aid.
_BREAKDOWN_VALUES: Final[frozenset[str]] = frozenset(member.value for member in BreakdownDimension)
_SEGMENTATION_VALUES: Final[frozenset[str]] = frozenset(SEGMENTATION_WIRE_KEYS.values())
if _BREAKDOWN_VALUES != _SEGMENTATION_VALUES:
    raise VocabularyDivergence(
        "BreakdownDimension and the segmentation vocabulary have diverged: "
        f"{sorted(_BREAKDOWN_VALUES ^ _SEGMENTATION_VALUES)}"
    )


@dataclass(frozen=True)
class FinancialClaim:
    """One claim: the money, the two cost-driver flags, and the columns the
    filter and the breakdown read.

    A projection rather than the ORM entity, for `FraudClaim`'s and
    `ChartClaim`'s reasons: it keeps the folds pure and constructible by hand,
    and it puts every input to the three answers next to each other instead of
    spread across a row carrying forty other columns.

    It satisfies **three** structural protocols by shape and imports none of
    them as a base class, which is `derivations.PaidColumns`' idiom taken to its
    conclusion:

    - `derivations.PaidColumns` — the three `paid_*` fields are spelled exactly
      as that protocol declares them, so `total_paid` adds up this projection
      without this module doing the addition.
    - `segmentation.LabelledClaim` — the ten dimension columns plus
      `employer_label`, so `segmentation.narrowed` narrows this projection the
      same way it narrows `FraudClaim` and `TrendClaim`.
    - `financials.IdentifiedReserveClaim` — `claim_id`, `stage`, `doi`,
      `recovery` and `reserve`, so `reserve_checks_for_claims` can reach the
      verdict for each of these rows without this module knowing what a verdict
      is.

    `doi` and `recovery` are carried and **never read here**: they are the
    schedule projection's two inputs, and they exist on this class only because
    the reserve protocol needs a claim it can hand to `classify_reserve`'s
    upstream. `severity_score` and `age` are the same kind of passenger one layer
    over — raw columns the registered `risk` and `age_band` derivations band,
    never bands themselves, because a stored band would be the derived column
    Story 1.2 banned and the second computer AD-10 forbids.

    `surgery_required` and `litigation_flag` are the only two fields this module
    reads *as facts about the claim*, and they are the two the story names. They
    are already `DrillFilters` facets (`filter[surgery]`, `filter[litigation]`),
    which is why the cost-driver drills cost this story no new facet.
    """

    claim_id: str
    stage: Stage
    doi: date
    recovery: RecoveryWindow
    reserve: int
    paid_indemnity: int
    paid_medical: int
    paid_expense: int
    surgery_required: bool
    litigation_flag: bool
    # The segmentation vocabulary's columns, read by nothing in this module and
    # by `services/worklist/segmentation.py` alone — `FraudClaim`'s arrangement.
    severity_score: int
    injury_type: str
    state: str
    employer_id: int
    employer_label: str
    disability: Disability
    sector: str
    region: str
    icd: str
    age: int
    gender: Gender


@dataclass(frozen=True)
class MoneyTotals:
    """The three figures, for a book or for any part of one.

    One class rather than three loose integers repeated on four payload types,
    and the grouping is the contract: these three describe *the same claims*, and
    a shape that let a caller build a paid figure over one population and a
    reserve figure over another is the shape this section exists not to have.

    Every field is integer cents and says so in its name — the money convention,
    end to end below `web/src/lib/money.ts`.
    """

    paid_cents: int
    reserve_cents: int
    projected_cents: int


@dataclass
class _Money:
    """A mutable accumulator for one group's three sums. Internal.

    A tiny class rather than three parallel dicts, `fraud._tallied`'s argument:
    the three sums are one group's three halves, so a key that existed in one
    dict and not in another would be a group with a paid figure and no reserve —
    reachable the moment somebody adds a fourth figure and updates two of the
    three places. One structure, one `setdefault`, one increment site.

    Not frozen, unlike everything else in this module, because it exists for
    exactly the length of one fold and is copied into a frozen `MoneyTotals` on
    the way out.
    """

    paid_cents: int = 0
    reserve_cents: int = 0
    projected_cents: int = 0
    claim_count: int = 0
    label: str | None = None

    def totals(self) -> MoneyTotals:
        return MoneyTotals(
            paid_cents=self.paid_cents,
            reserve_cents=self.reserve_cents,
            projected_cents=self.projected_cents,
        )


@dataclass(frozen=True)
class BreakdownGroup:
    """One row of the breakdown: what it is, how many claims, and what they cost.

    `key` is the **wire value the group's own facet takes** — `high`, `female`,
    `Aerospace`, `3` — so the client's drill target is `filter[<dimension>]=<key>`
    with no translation, exactly as a chart segment's is. `label` is a human name
    **or `None`**, which is `segmentation.DimensionValue`'s split restated on a
    row so the client's rule is one rule in three places:
    `label ?? UI_LABEL[key][value] ?? value`.

    Only `employerId` carries a label, for `AppliedFilter.display`'s recorded
    reason: an id is not a name and nothing in the browser can turn `3` into
    "Boeing Everett" on a cold URL load, while the two bands and the two enums
    are tokens the SPA owns copy for and the five free-text dimensions are
    columns whose stored value *is* the label.

    `claim_count` rides beside the money for `InjuryTypeRate`'s reason: a spend
    figure is uninterpretable without the population behind it — one settled
    claim can outspend twenty open ones — and the honest fix is to publish the
    denominator rather than to invent a suppression rule.
    """

    key: str
    label: str | None
    claim_count: int
    totals: MoneyTotals


@dataclass(frozen=True)
class FinancialBreakdown:
    """One dimension's groups, and how they were cut.

    `Distribution`'s truncation contract, restated on a series that distributes
    three quantities rather than one: `group_count` is how many groups the
    *segment* contains before any limit, `truncated` is decided here rather than
    left to a client comparing two fields, and `limit` is the cap that was
    applied.

    **There is no `total` on this class, and its absence is deliberate.** The
    portfolio totals sit one level up on `FinancialDecomposition` and are
    whole-book (whole-*segment*) figures that do not move when the tail is cut —
    which is what lets the card's caption say "top 12 of 34" beside a total that
    is still the answer to "what does this segment cost". A `total` here would be
    a second sum that a reader could not tell apart from that one, and it would
    have to be either the kept groups' sum (contradicting the card above it) or
    the whole segment's (a third copy of a figure already published twice).

    `dimension` echoes what was grouped by, `RateBreakdown.sort`'s reason: a
    control that rendered its own last click rather than the server's answer has
    no way back from a request that failed, and a stored or forwarded response
    should be self-describing.
    """

    dimension: BreakdownDimension
    items: tuple[BreakdownGroup, ...]
    group_count: int
    truncated: bool
    limit: int


@dataclass(frozen=True)
class CostDriverCohort:
    """One side of a cost-driver comparison: how many claims and what they cost.

    `key` is the wire form of the *facet value* the cohort corresponds to —
    `"true"` or `"false"` — so a click opens `filter[surgery]=true` with no
    mapping, the same string `drill_through.chip_value` publishes for a boolean
    facet and the same string the client's boolean label map is keyed on.

    `average_projected_cents` is the headline and is **`None`, never `0`, for an
    empty cohort** — `trends.TrendPoint`'s sentinel and its argument: a mean over
    an empty set is not zero, and a cohort reporting `$0` would read as a cohort
    that costs nothing. Floor division rather than half-up rounding, because this
    is an average of cents that nothing multiplies and a rounded half-cent would
    be precision the figures do not carry.

    **The average is the headline and all three totals travel with it**, which is
    what makes the litigation comparison survive the seeded data: all three
    litigated claims are open, so their paid total is exactly zero and a
    paid-only comparison would report that litigation costs nothing. What those
    three claims actually carry is `$53,342` of held reserve, which is `$17,780`
    a claim, and the only way a card can say so is if the reserve and the
    projected figures are on the wire beside the paid one.

    **On this seed the litigated cohort comes out *cheaper* than the other one**
    — `$17,780` a claim against `$21,522` — and that is reported rather than
    tuned, which is the Block-If this story was given in as many words. The
    reason is legible on the card: the ninety-seven include all 62 settled
    claims, whose paid columns are populated and large, while the three litigated
    ones are open and carry nothing but a reserve. That is a fact about what
    `paid_cents` sums (`deferred-work.md`'s open decision) crossed with a
    three-claim cohort, and it is exactly why the counts sit beside the averages:
    a reader who can see `3` against `97` can see that the comparison is thin,
    and a card that published the averages alone would be inviting a conclusion
    the data does not carry.
    """

    key: str
    claim_count: int
    totals: MoneyTotals
    average_projected_cents: int | None


@dataclass(frozen=True)
class CostDriverPair:
    """A driver and its complement — the two cohorts that partition the segment.

    A pair rather than two independent rows, and the pairing is the assertion:
    `with_driver.claim_count + without_driver.claim_count` is the segment's whole
    population and neither cohort is a sample of it. A shape that published one
    cohort would let a card compare a surgery cohort against a portfolio total
    that *contains* it, which is the comparison the story's word "versus" rules
    out.

    `facet` is the `filter[…]` name each side drills on, published rather than
    inferred from `driver` so the client composes no parameter name — the
    `DimensionValues.key` rule, one payload over.
    """

    facet: str
    with_driver: CostDriverCohort
    without_driver: CostDriverCohort


@dataclass(frozen=True)
class FinancialDecomposition:
    """The whole Financial section's first payload: totals, a breakdown, two pairs.

    Three answers on one object because they are folded from **one traversal of
    one read**, `fraud.panel_of`'s argument: three passes over the same list
    would produce the same numbers today and would be three opportunities for one
    of them to be written against a filtered copy — which on this surface means a
    breakdown that sums to a different book from the total above it.

    `claims_in_scope` counts the **segmented** book, which is what every other
    analyst payload's field of that name has counted since Story 7.3; the
    unfiltered denominator is on `/dashboard/segmentation/values`.

    `rules_version` is deliberately **absent**, exactly as it is from
    `FraudPanel` and `PortfolioCharts`: it is the identity of a document rather
    than a figure computed from the caseload, and this fold is pure. The router
    adds it from the block it loaded.
    """

    totals: MoneyTotals
    claims_in_scope: int
    breakdown: FinancialBreakdown
    surgery: CostDriverPair
    litigation: CostDriverPair


@dataclass(frozen=True)
class VerdictCount:
    """One bucket of the reserve-adequacy distribution.

    `verdict` is the wire value of a `ReserveVerdict` member (snake_case, per the
    enum convention — the UI owns "Reserve Light"), and it is also the value
    `filter[reserveVerdict]` takes, so a segment's drill target is the segment's
    own key.

    `CategoryCount`'s shape with its own field name rather than that class
    reused: this is not a chart series over a column, and a reader looking at a
    payload should be able to see from the field name which vocabulary the key
    belongs to.
    """

    verdict: ReserveVerdict
    count: int


@dataclass(frozen=True)
class ReserveAdequacy:
    """The portfolio's reserve verdicts, counted, with the bands behind them.

    **Five items, always, in `ReserveVerdict` declaration order** — the zero-fill
    rule `fraud._banded` states and the fourth surface to apply it. A verdict is a
    *rule's* answer over a claim every book contains, so all five members exist
    for every portfolio that exists at all, and "no claim in this segment is
    under-reserved" is the single most valuable thing this chart can say. Omitting
    an empty bucket would draw a distribution a reader could not distinguish from
    a build that forgot to draw it, and the reassuring reading is the wrong one.

    `total` equals `claims_in_scope` by construction — every claim in the segment
    lands in exactly one bucket — and both are published because a card that
    reported one and implied the other would be asking a reader to trust an
    identity rather than see it. `DistributionDonut` decides emptiness on a
    *total* and never on a row count, which the zero-fill makes necessary.

    `light_ratio_bp`, `heavy_ratio_bp` and `bands_version` travel with the figures
    for `FraudPanel`'s recorded reason: the card's footnote quotes the edges the
    buckets were produced at, so a client holding either number would be a second
    copy of a rule it cannot see change, and superseding `reserve_bands` has to
    move the segments *and* the caption together. They are read off the
    `ReserveBands` block the verdicts were computed with, so the published numbers
    are provably the ones the counts were produced at — and `bands_version` is on
    this payload rather than a `rules_version` added by the router because
    `reserve_bands` is a *different document* from `derivation_thresholds`, and
    one field could only name one of them.
    """

    items: tuple[VerdictCount, ...]
    total: int
    claims_in_scope: int
    light_ratio_bp: int
    heavy_ratio_bp: int
    bands_version: int


def _group_key(
    claim: FinancialClaim,
    dimension: BreakdownDimension,
    computers: SegmentationComputers,
) -> tuple[str, str | None]:
    """Which group one claim falls in, and what that group reads as.

    A `match` over the ten rather than a mapping of lambdas, and this is the one
    place in the package where that choice goes the other way from
    `_PREDICATES`' — because `BreakdownDimension` is a `StrEnum` and mypy checks
    a `match` over one for exhaustiveness. A missing arm is a type error here,
    which is strictly stronger than the import-time set equality a table would
    get, and the vocabulary is already pinned to `SEGMENTATION_WIRE_KEYS` above.

    **The two derived dimensions go through the registry** (AD-10), the same
    `risk` and `age_band` computers `segmentation.bands_of` narrows with — so a
    breakdown by severity band and a filter on one are the same band rather than
    two readings of one score. The eight stored ones are compared and grouped as
    stored, with no trim, case-fold or merge, which is `charts.LabelCount`'s
    ruling: canonicalizing free text is a data-quality decision with an owner,
    and a group that did it would open a drill list no group ever counted.

    The **label** is `None` for nine of the ten, `BreakdownGroup`'s recorded
    split: only an employer id is not already the string a reader reads.
    """
    match dimension:
        case BreakdownDimension.severityBand:
            return computers.risk.of(claim.severity_score).value, None
        case BreakdownDimension.injuryType:
            return claim.injury_type, None
        case BreakdownDimension.state:
            return claim.state, None
        case BreakdownDimension.employerId:
            return str(claim.employer_id), claim.employer_label
        case BreakdownDimension.disability:
            return claim.disability.value, None
        case BreakdownDimension.sector:
            return claim.sector, None
        case BreakdownDimension.region:
            return claim.region, None
        case BreakdownDimension.icd10:
            return claim.icd, None
        case BreakdownDimension.ageGroup:
            return computers.age_band.of(age=claim.age).value, None
        case BreakdownDimension.gender:
            return claim.gender.value, None


def _breakdown(dimension: BreakdownDimension, groups: Mapping[str, _Money]) -> FinancialBreakdown:
    """The kept groups, ranked by projected cost, then cut.

    **Ranked by `projected_cents` descending, then by key ascending**, and the
    tie-break is load-bearing rather than cosmetic: on a segmented book whole
    groups routinely carry identical figures (a dimension whose members all hold
    one open claim, or — reachable on this seed — several groups at exactly
    zero), so an order decided only by the figure would leave the tied rows to
    whatever the dict happened to hold, and the *cut* would land inside the tie.
    That is a chart that renders differently on two consecutive requests from one
    book with nothing on screen to say why — `charts._ranked`'s recorded
    argument, over money instead of counts.

    The tie-break is the **key** rather than the label, which is the one
    difference from `_ranked`: nine of the ten dimensions have no label at all,
    and the tenth's is a `short_name` two employers could share. Sorting by a
    field that is `None` for nine dimensions would be sorting by nothing.

    **Projected rather than paid**, because paid is zero for every open claim on
    this seed (`deferred-work.md`'s finding) — a paid ranking would put the
    entire open book in a tie at the bottom and cut the tail arbitrarily inside
    it. Projected is the only one of the three that is non-zero for both halves
    of the book.

    Sorting on `-value` rather than with `reverse=True`, `_ranked`'s reason:
    `reverse=True` would reverse the key order too and give a descending
    secondary key. Both parts of the sort key have to point the way they are
    documented.
    """
    ordered = sorted(groups.items(), key=lambda entry: (-entry[1].projected_cents, entry[0]))
    return FinancialBreakdown(
        dimension=dimension,
        items=tuple(
            BreakdownGroup(
                key=key,
                label=money.label,
                claim_count=money.claim_count,
                totals=money.totals(),
            )
            for key, money in ordered[:BREAKDOWN_LIMIT]
        ),
        group_count=len(groups),
        truncated=len(groups) > BREAKDOWN_LIMIT,
        limit=BREAKDOWN_LIMIT,
    )


def _cohort(key: str, money: _Money) -> CostDriverCohort:
    """One side of a cost-driver pair, with its average.

    Floor division, and `None` rather than `0` for an empty cohort — see
    `CostDriverCohort`, which carries the argument. The emptiness test is
    `claim_count == 0` written as an equality rather than as a truthiness check,
    because a cohort of zero claims and a cohort of zero *cents* are different
    facts and only the first has no average.
    """
    return CostDriverCohort(
        key=key,
        claim_count=money.claim_count,
        totals=money.totals(),
        average_projected_cents=(
            None if money.claim_count == 0 else money.projected_cents // money.claim_count
        ),
    )


def _pair(facet: str, present: _Money, absent: _Money) -> CostDriverPair:
    """The two cohorts of one driver, keyed by the facet values they drill on.

    `"true"` and `"false"` rather than a name of this module's choosing, because
    those are the strings `drill_through.chip_value` publishes for a boolean
    facet and the strings the client's boolean label map is keyed on — the group
    key and the URL are one word, `BreakdownGroup.key`'s rule applied to a
    cohort.
    """
    return CostDriverPair(
        facet=facet,
        with_driver=_cohort("true", present),
        without_driver=_cohort("false", absent),
    )


def decomposition_of(
    caseload: Sequence[FinancialClaim],
    dimension: BreakdownDimension,
    computers: SegmentationComputers,
    total_paid: TotalPaidDerivation,
    total_projected: TotalClaimProjectedDerivation,
) -> FinancialDecomposition:
    """The portfolio totals, one breakdown and both cost-driver pairs. Pure.

    `fraud.panel_of`'s split and its reasons: the arithmetic is database-free and
    constructible by hand, and the one scoped read happens in the async wrapper
    below.

    **One pass accumulating six accumulators**, not six comprehensions over the
    same list. They would produce the same numbers today and it is the wrong
    shape — `panel_of`'s argument, sharper here than anywhere else in the package
    because the assertions this section rests on are *identities between its own
    figures*: the breakdown groups sum to the portfolio totals, and each cohort
    pair partitions the same book. Six separate passes would be six chances for
    one of them to be written against a filtered copy, and the symptom would be a
    card whose parts do not add up to its heading.

    Both money figures come from the derivations passed in (AD-10). This function
    adds the three columns nowhere: `total_paid.of(claim)` reads them, and the
    projected figure is `total_claim_projected.of(paid, reserve)` — the same two
    computers the case file's Bills tab calls, so a portfolio total and a claim's
    own figure are one rule with two callers.
    """
    portfolio = _Money()
    groups: dict[str, _Money] = {}
    surgery = _Money()
    no_surgery = _Money()
    litigation = _Money()
    no_litigation = _Money()

    for claim in caseload:
        paid = total_paid.of(claim)
        reserve = claim.reserve
        projected = total_projected.of(paid_to_date_cents=paid, reserve_cents=reserve)
        key, label = _group_key(claim, dimension, computers)
        # The claim goes into the portfolio, its group and one side of each pair
        # in one step, so the four cannot come to describe four populations.
        # `groups.setdefault` carries the label with the bucket for `charts_of`'s
        # recorded reason — the *first* claim in a group names it and every later
        # one keeps that name, which is `setdefault`'s whole behaviour and is
        # safe here because the label is a column on the joined row rather than
        # anything derived: two claims of one employer cannot disagree about it.
        for bucket in (
            portfolio,
            groups.setdefault(key, _Money(label=label)),
            surgery if claim.surgery_required else no_surgery,
            litigation if claim.litigation_flag else no_litigation,
        ):
            bucket.paid_cents += paid
            bucket.reserve_cents += reserve
            bucket.projected_cents += projected
            bucket.claim_count += 1

    return FinancialDecomposition(
        totals=portfolio.totals(),
        claims_in_scope=len(caseload),
        breakdown=_breakdown(dimension, groups),
        surgery=_pair("surgery", surgery, no_surgery),
        litigation=_pair("litigation", litigation, no_litigation),
    )


def adequacy_of(
    caseload: Sequence[FinancialClaim],
    verdicts: Mapping[str, ReserveCheck],
    bands: ReserveBands,
) -> ReserveAdequacy:
    """Epic 3's verdicts, **counted** — never re-derived. Pure.

    The whole of AC 2 in one function, and what it does not do is the point:
    there is no ratio here, no comparison against a reserve, no reading of
    `light_ratio_bp` or `heavy_ratio_bp` for anything but publication, and no
    branch on `stage`. `verdicts` arrives from
    `reserve.reserve_checks_for_claims`, which folds stored rows through
    `reserve_check_from_rows` — the same function the case file's chip and the
    Bills tab's summary reach the verdict through.

    A claim missing from `verdicts` is a **programming error and is raised**,
    not skipped and not defaulted. The two are produced from one caseload one
    line apart in `reserve_adequacy` below, so a gap can only mean the mapping
    was built from a different population — and a fold that silently dropped
    those claims would publish a distribution whose total was quietly smaller
    than `claims_in_scope` on the one card whose whole subject is how a *whole*
    book is reserved.

    **Zero-filled over the whole vocabulary**, in declaration order — see
    `ReserveAdequacy`.
    """
    counts: dict[ReserveVerdict, int] = {}
    for claim in caseload:
        check = verdicts.get(claim.claim_id)
        if check is None:
            raise MissingReserveVerdict(
                f"no reserve verdict was loaded for {claim.claim_id}; "
                "the verdict map and the caseload describe different populations"
            )
        counts[check.verdict] = counts.get(check.verdict, 0) + 1

    return ReserveAdequacy(
        items=tuple(
            VerdictCount(verdict=verdict, count=counts.get(verdict, 0))
            for verdict in ReserveVerdict
        ),
        total=sum(counts.values()),
        claims_in_scope=len(caseload),
        # Read off the block the verdicts were computed with — `panel_of`'s rule,
        # so the published edges are provably the ones the buckets were produced
        # at rather than a second load of the same document.
        light_ratio_bp=bands.light_ratio_bp,
        heavy_ratio_bp=bands.heavy_ratio_bp,
        bands_version=bands.version,
    )


#: The columns this section reads, named once.
#:
#: `sla.SAMPLE_COLUMNS`' idiom: two services compose a projection from this list
#: and a list written out twice is two lists that agree until one of them gains a
#: column — and the drift would be silent, because both reads feed a
#: `FinancialClaim` and a missing attribute only shows up when the fold reads it.
#:
#: The **fourth** member of the projection family (`select_claim_columns_with_
#: employer_and_employee`) rather than `select_drill_rows`, and the difference is
#: one join: this section needs no handler at all — it names nobody, groups by
#: nothing on `app_user`, and publishes no handler figure — so reading the
#: three-join drill projection would be an `AppUser` join for a column nothing
#: here touches. Story 7.3 added this sibling for exactly the columns two of the
#: ten dimensions live in.
_FINANCIAL_COLUMNS: Final[tuple[Any, ...]] = (
    Claim.claim_id,
    Claim.stage,
    Claim.doi,
    Claim.recovery,
    Claim.reserve,
    Claim.paid_indemnity,
    Claim.paid_medical,
    Claim.paid_expense,
    Claim.surgery_required,
    Claim.litigation_flag,
    Claim.severity_score,
    Claim.injury_type,
    Claim.state,
    Claim.employer_id,
    Claim.disability,
    Claim.region,
    Claim.icd,
    Employer.sector,
    Employee.age,
    Employee.gender,
)


def _projection(rows: Sequence[sa.Row[Any]]) -> list[FinancialClaim]:
    """The read's rows as this module's projection. Named, never positional.

    `fraud._projection`'s argument, on a projection carrying **six adjacent
    integers of the same kind** — three paid columns, a reserve, a severity score
    and a worker's age. `FinancialClaim(*row)` would work, would type-check the
    whole way, and would be one reordered projection away from banding a
    worker's age as a severity score while charging medical spend to indemnity.
    Six lines of keywords is what stops that being a possible mistake rather than
    an unlikely one.
    """
    return [
        FinancialClaim(
            claim_id=row.claim_id,
            stage=row.stage,
            doi=row.doi,
            recovery=row.recovery,
            reserve=row.reserve,
            paid_indemnity=row.paid_indemnity,
            paid_medical=row.paid_medical,
            paid_expense=row.paid_expense,
            surgery_required=row.surgery_required,
            litigation_flag=row.litigation_flag,
            severity_score=row.severity_score,
            injury_type=row.injury_type,
            state=row.state,
            employer_id=row.employer_id,
            employer_label=row.employer_short_name,
            disability=row.disability,
            sector=row.sector,
            region=row.region,
            icd=row.icd,
            age=row.age,
            gender=row.gender,
        )
        for row in rows
    ]


async def financial_decomposition(
    db: AsyncSession,
    ctx: CallerContext,
    thresholds: DerivationThresholds,
    filters: Segmentation,
    *,
    dimension: BreakdownDimension = DEFAULT_BREAKDOWN,
) -> FinancialDecomposition:
    """The Financial section's totals, breakdown and cost drivers — one call.

    Takes its parameter block rather than fetching it, `fraud_panel`'s signature
    and its reason: the route loads it once and hands it down, so this stays a
    composition of scope and parameters instead of dragging the rules engine into
    an aggregate that mentions a band.

    **One scoped read, one pure fold** — and literally so: there is no other
    `await` in this body. `dimension` changes which key the fold groups on and
    changes nothing about the read, which is what makes a `groupBy` control cost
    the same as no control at all.

    The segmentation narrows the fold and adds no read (Story 7.3's rule, fourth
    section): it is applied here over the rows `employer_scope(ctx)` already
    decided existed, rather than pushed into the repository, because two of its
    ten dimensions are registered derivations and a SQL narrowing would split one
    filter across two tiers.

    A filter that no claim satisfies produces an empty caseload and therefore
    three zero totals, an empty breakdown, and four cohorts with `claim_count: 0`
    and `average_projected_cents: null` — a 200 with nothing in it, which is the
    ordinary case on a nine-dimension AND rather than an edge one.

    Raises `FraudAnalyticsNotPermitted` (403) for any role outside
    `fraud.FRAUD_ANALYTICS_ROLES`, **before the read**. The route calls the same
    gate first as well, so the refusal also precedes the rule-document read it
    makes on this function's behalf; the check here stays because capability
    belongs with the service that owns the rows and not with one caller.
    """
    require_fraud_analytics_access(ctx)
    rows = await claim_repo.select_claim_columns_with_employer_and_employee(
        db, ctx, list(_FINANCIAL_COLUMNS)
    )
    # Built once and handed to both, rather than twice: the block is the same
    # block, and two builds per request is two chances for a future edit to hand
    # the fold and the filter different computers over one caseload.
    computers = SegmentationComputers.of(thresholds)
    caseload = segmentation_narrowed(_projection(rows), filters, computers)
    return decomposition_of(
        caseload,
        dimension,
        computers,
        derivations.total_paid.for_thresholds(thresholds),
        derivations.total_claim_projected.for_thresholds(thresholds),
    )


async def reserve_adequacy(
    db: AsyncSession,
    ctx: CallerContext,
    thresholds: DerivationThresholds,
    bands: ReserveBands,
    filters: Segmentation,
) -> ReserveAdequacy:
    """The portfolio's reserve verdicts, counted over the segmented book.

    **Three scoped reads, and the number is the point rather than an accident.**
    One for the claims, and two inside `reserve.reserve_checks_for_claims` for
    the payment-schedule weeks and the bills every verdict's exposure terms are
    read off. `fraud_red_flags` records the same shape for its two, and
    `tests/test_financial_decomposition.py` counts these three so a fourth
    cannot appear quietly — the failure that guard exists for is the obvious one
    on this path: a loop calling `reserve_check_for_claim` per claim, which would
    be 3N reads *and* N schedule refreshes on a read-only route.

    **A separate route from `/dashboard/financials`, and this signature is why.**
    That aggregate is one read; this one is three and loads a second rule
    document. Serving both from one endpoint would make every totals render pay
    for the adequacy chart and would let one card's failure blank the other —
    `FraudPage`'s four-query composition, applied to a section whose two halves
    genuinely cost different amounts.

    `bands` arrives as an argument for `thresholds`' reason. It is a **different
    document** (`reserve_bands`, not `derivation_thresholds`) and both are loaded
    by the route: the thresholds build the segmentation's two band computers, and
    the bands decide the verdict.

    Raises `FraudAnalyticsNotPermitted` (403) before either read, for
    `financial_decomposition`'s reason.
    """
    require_fraud_analytics_access(ctx)
    rows = await claim_repo.select_claim_columns_with_employer_and_employee(
        db, ctx, list(_FINANCIAL_COLUMNS)
    )
    caseload = segmentation_narrowed(
        _projection(rows), filters, SegmentationComputers.of(thresholds)
    )
    # The verdicts are loaded for the **segmented** caseload, not for the book:
    # the two bulk reads take an `IN` list, so a filter that removes 90 claims
    # removes them from the schedule and bill reads as well.
    verdicts = await reserve_checks_for_claims(db, ctx, caseload, bands=bands)
    return adequacy_of(caseload, verdicts, bands)


__all__ = [
    "BREAKDOWN_LIMIT",
    "DEFAULT_BREAKDOWN",
    "BreakdownDimension",
    "BreakdownGroup",
    "CostDriverCohort",
    "CostDriverPair",
    "FinancialBreakdown",
    "FinancialClaim",
    "FinancialDecomposition",
    "MoneyTotals",
    "ReserveAdequacy",
    "VerdictCount",
    "adequacy_of",
    "decomposition_of",
    "financial_decomposition",
    "reserve_adequacy",
]
