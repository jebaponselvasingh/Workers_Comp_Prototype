"""The analyst workspace's Trends section — five metrics over a bucketed window
(FR-AN-2, Story 7.2).

Every aggregate in this package answers a question about *today*. The KPI cards
count the book as it stands, the charts distribute it, the fraud panel bands it,
and not one of them can say whether any of it is getting better. This module is
the missing axis: the same scoped book, folded into periods, so "is the portfolio
improving or deteriorating" becomes a shape on a chart rather than a number
somebody has to remember from last month.

Structurally it is `charts.py`, once more: a frozen row projection built
**named**, a pure fold with no session and no clock of its own, and a thin async
wrapper whose parameter blocks arrive as arguments. Nothing here touches the
rules engine and nothing here holds a cut-off —
`test_no_module_outside_the_registry_hardcodes_the_band` greps this file,
comments included, precisely because the reason it can is that there is nothing
in it to find.

## Five metrics, and each one is computed by something that already existed

    volume               = how many claims fall in the bucket        -> a count
    avg_days_open        = mean of derivations.days_open(as_of)      -> AD-10
    avg_settlement_days  = sla.strip_of(bucket samples)[settle]      -> AD-2
    rtw_rate_bp          = sla.strip_of(bucket samples)[rtw_rate]    -> AD-2
    paid_cents           = sum of derivations.total_paid             -> AD-10

Two of those come out of **one** call to `sla.strip_of` per bucket, and that is
the whole of AD-2 on this surface. This module never names an SLA duration
column: the columns reach the read as `sla.SAMPLE_COLUMNS`, the row becomes an
`SlaSample` through `sla.sample_of`, and the average is whatever `strip_of`
says it is. `test_nothing_outside_the_worklist_aggregation_reads_the_sla_source_
columns` greps for those names with comments and docstrings included, and it
passes here for the same reason `benchmarks.py` and `charts.py` pass it — there
is no second aggregation to find, only a second caller.

## Settlement cycle time is a *cohort* cycle time, and that is the honest reading

The schema has six date columns and **no closure date**; `core.py:205-210` says
outright that the dataset's SLA durations do not reconcile with its dates. So
"settlement cycle time in March" cannot mean "claims that settled in March" —
there is nothing in the record that says when a claim settled. What the data can
answer is "claims *filed* in March took N days to settle", which is a genuine
analyst question and the only one available. The alternative — bucketing on
`froi_date` plus a stored duration — manufactures a date the dataset explicitly
disclaims, and is refused. The chart title and the card footnote both say "by
FNOL cohort" so no reader can mistake the two.

## Zero and null are different answers, and the split is by metric kind

`volume` and `paid_cents` zero-fill: a bucket with no claims saw nothing and paid
nothing, and both of those are facts a chart may draw. The three others are a
mean and a rate, and a mean over an empty set is not zero — "average days open:
0" and "RTW rate: 0%" are both false statements about a month nobody filed a
claim in. So they are `null`, and the vocabulary is inherited rather than
invented: `sla.strip_of` already answers `no_data` for an empty segment, and the
two SLA-derived series simply republish that answer.

This is the fourth zero-fill policy in the package and it differs from all three
in `charts.py` because those band a *vocabulary* while this one bands a
*timeline*. A month that produced no claims is neither an absent category
(`charts._declared` omits those) nor a ranked survivor (`charts._ranked` cuts
those): the buckets between the window's edges are known in advance, so an empty
one is an observed zero rather than a category that does not apply.

The corollary is that emptiness cannot be decided on the number of points. A
zero-filled series has as many points as any other, so `points` is never empty
and `len(points) == 0` is never the empty state. Every series therefore publishes
`series_total` — the claims behind it across the whole window — and that is the
figure a caller tests.

## A metric's population is the claims that fed it, not the claims in the bucket

Three of the five metrics fold the bucket's whole population and two do not:
`avg_settlement_days` is a mean over settled claims carrying a duration and
`rtw_rate_bp` is a rate over settled claims, which is `sla.strip_of`'s deliberate
asymmetry arriving one bucket at a time. So "seven claims" is the wrong count to
publish beside a settlement mean folded from three of them, `low_confidence`
decided on seven is a verdict about the wrong evidence, and a `series_total` of
seven says a window has settlement data in it when nothing in it has settled.

`claim_count` is therefore **per metric**, and the denominators come from
`sla.denominators_of` rather than from a count taken here — this module knows
what fed a settlement mean exactly as well as it knows what a settlement mean is,
which is not at all (AD-2). The bucket's own claim count is deliberately *not*
published beside it: it is the `volume` series' value, already on the same
payload, and a second field carrying the same number under a different name on
every point of every series is how the two come to be read for each other.

## Cohorts, and why colour is identity rather than rank

One dimension at a time: severity band (through `derivations.risk`, the same
registered band the KPI card and the donut read), disability type, or the
employer's sector. Each cohort value gets its own series of each metric, and each
carries a `palette_slot` — an **ordinal**, not a colour. The browser maps it
through the theme's categorical palette, whose own docstring records the flaw
this fixes: a hue there means *rank position*, so a cohort would change colour
the moment its ranking moved. Slots are assigned by sorting the cohort values on
their **wire key**, never on a display label, which is Story 5.3's review finding
restated — two labels can collapse where two keys cannot.

A cohort value the window contains no claim for is **absent** rather than an
all-zero line, which is `charts._declared`'s rule and deliberately not
`fraud._banded`'s. The distinction is the same one that decides the zero-fill
above, one axis over: a fraud band with no claims is a segment of a distribution
whose vocabulary is a rule's, and "nothing scored high" is the headline. A cohort
with no claims in the window is a *line nobody drew*, and rendering it would put
a legend entry on screen describing nothing.

## `days_open` slopes, and it is left visible

`days_open` counts from the claim's FNOL date to `as_of` and never freezes, so an
average-days-open series bucketed by filing month slopes upward toward older
buckets purely because those claims are older. That is a property of the metric
rather than a defect, and hiding it would need a second computer for a value that
already has one — precisely what AD-10 forbids. The payload publishes `as_of` and
the card says "as of {asOf}", which is also the wire change `deferred-work.md`
has wanted since Story 2.1.

## The newest bucket is a period in progress, and it says so

A window ends in the bucket `as_of` falls in, and that bucket is almost never
over: on the 21st of a 31-day month it holds two thirds of a period drawn at the
same width as the eleven complete ones beside it. Every metric on it is affected
and not in one direction — volume and paid are *under*-counted because the rest
of the month has not happened, while `avg_days_open` is dragged toward zero
because the claims in it are days old. The rightmost point is the one the whole
section exists to read, so a reader comparing it against last month is comparing
a part against a whole.

The fix is not to drop the bucket (that hides the most current thing on the
chart) and not to extrapolate it (that invents claims nobody filed, which is the
zero-fill prohibition wearing a different hat). It is to **say so**: every point
publishes `partial`, true when its bucket has not finished by `as_of`, and the
card marks it. The epic's own words for this state are "a defined partial/empty
state", and this is the partial half.

## One clock, one read, one fold

`as_of` is resolved once, at the top of `portfolio_trends`, and threaded into
every `days_open` call and into the window's own arithmetic. Two claims in one
request can never be scored against different days, and a request straddling
midnight cannot bucket half its book into one month and half into the next.

The read is one `select_claim_columns_with_employer_and_employee` — the
projection family's fourth member, which Story 7.3 added because two segmentation
dimensions are columns of `employee` — and every one of the five metrics folds
from that one projection. There is deliberately **no `GROUP BY` push-down**, and
the reason is that it would only move one of five metrics: `date_trunc` is not a
rule value so bucketing pushes down cleanly, but `avg_days_open` folds a
registered derivation, `paid_cents` folds another, and the two SLA figures fold
`sla.strip_of` — all Python-tier by AD-2 and AD-10. Pushing the bucket into SQL
would leave four metrics needing the rows anyway, turning one scoped read into
two read shapes and splitting one aggregate across two tiers to save a count.

**The revisit condition is now scope size alone.** Story 7.2 stated it as "a
single scope past roughly ten thousand claims, **or** Story 7.3 multiplying grain
× cohort × segmentation onto one request", and the second half is answered rather
than pending: a segmentation *narrows the fold*. It adds no read and multiplies
nothing — grain × cohort already multiplied and is bounded by `maxBuckets`, and
the filter only ever removes claims from the loop. Two of its ten dimensions are
derived, so pushing it into SQL would put part of one filter in `data/` and the
rest here, which is the split `data/repositories/claims.py` refuses in writing.
So the multiplication clause is retired and the size clause stands.

## Role gates capability, scope gates visibility (AD-7)

The gate is `fraud.require_fraud_analytics_access` — the analyst-workspace
allowlist Story 7.1 declared — reused rather than re-declared, because this is a
second section of one workspace and two spellings of one allowlist is how a
future `UserRole` gets admitted by one of them. It is emphatically not a widening
of `benchmarks.PERMITTED_ROLES`, which is the oversight capability a supervisor
also carries. The refusal happens before any claim row and before any rule
document is loaded, and there is nowhere in any signature here to put an
employer, a user or an "as".
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings
from data.context import CallerContext
from data.models import Claim, Employee, Employer
from data.models.enums import Disability, Gender
from data.repositories import claims as claim_repo
from rules.parameters import DerivationThresholds, TrendPeriods
from services import derivations
from services.derivations import OpenDurationDerivation, RiskDerivation, TotalPaidDerivation
from services.worklist import sla
from services.worklist.fraud import require_fraud_analytics_access
from services.worklist.segmentation import Computers as SegmentationComputers
from services.worklist.segmentation import Segmentation
from services.worklist.segmentation import narrowed as segmentation_narrowed

# --- the vocabulary ------------------------------------------------------


class TrendGrain(StrEnum):
    """How wide one bucket is. Closed, and the **type is the validation**.

    `grain=fortnight` cannot reach this module: FastAPI coerces the query
    parameter into this enum and answers 422 before the service runs, which is
    `FraudRateSort`'s arrangement and its consequence — there is no vocabulary
    check in this file and there must not be one, or the enum would be spelled
    twice.

    Three values, and each is a period a claims organisation actually reviews on.
    A day grain is deliberately absent: a hundred-claim portfolio spread over
    nine months has at most a handful of claims on any one day, so every point
    would be low-confidence and the chart would be a scatter of ones. Adding one
    is a product decision about what the section is *for*, not a fourth member.

    Snake_case values per the enum convention; the UI owns the labels.
    """

    week = "week"
    month = "month"
    quarter = "quarter"


class TrendAnchor(StrEnum):
    """Which date a claim is bucketed by — and there are exactly two candidates.

    `fnol` is `claim.froi_date`, when the claim came into existence for the
    carrier; `doi` is when the injury happened. They are different questions and
    a portfolio can improve on one while deteriorating on the other: a falling
    FNOL volume with a flat DOI volume is a reporting lag, not fewer injuries.

    **No third member, and that is a constraint rather than an omission.** The
    other four date columns are either an operational timestamp (`assign_date`,
    `approval_date`) or a return-to-work date that most claims do not carry —
    none of them is when the claim *happened*, and bucketing a cohort by a date
    two thirds of it is missing would silently describe a third of the book.
    """

    fnol = "fnol"
    doi = "doi"


class TrendCohort(StrEnum):
    """The dimension a series may be split by — one at a time, per the AC.

    `none` is a member rather than an absent parameter, so "no split" is a value
    the caller states and the response echoes, and a stored payload says which
    question it answered. The three real dimensions are the ones Story 7.2's AC 2
    names, and each is reached by the symbol that owns it: `severity_band`
    through the registered `risk` derivation (the same band the High Risk card
    and 5.3's donut count), `disability` through the claim's own enum column, and
    `sector` through the employer join the one read already carries.

    **Not nine dimensions, and not a filter.** Story 7.3 owns segmentation — a
    workspace-wide, AND-ed, URL-borne filter over nine columns. This is a
    chart-level *split* of one dimension, which is a different thing: it
    partitions the same population rather than narrowing it, so every cohort
    series over a metric sums back to the un-split one.
    """

    none = "none"
    severity_band = "severity_band"
    disability = "disability"
    sector = "sector"


class TrendMetric(StrEnum):
    """The five series, in the order they are published and drawn.

    The order is the section's reading order — how much work arrived, how old it
    is, how long it takes to close, how much of it came back to work, what it
    cost — and it is decided here so no client sorts (AD-1).

    **Each name carries its unit**, which is what makes every value on this
    payload an integer. `paid_cents` is cents and `rtw_rate_bp` is basis points,
    the two scales this console already uses for money and for rates; the two day
    metrics are whole days, the precision `sla.targets_for` decides the
    settlement tile at, so the two day-valued series on one screen are read at
    one precision rather than two.
    """

    volume = "volume"
    avg_days_open = "avg_days_open"
    avg_settlement_days = "avg_settlement_days"
    rtw_rate_bp = "rtw_rate_bp"
    paid_cents = "paid_cents"


#: The two metrics whose window total is a number, and the two zero-filled ones.
#:
#: The same set answers both questions, which is not a coincidence: a metric can
#: be summed over a window exactly when an empty bucket is an observed zero
#: rather than an absent answer. Counting claims and adding cents are both folds
#: over a set that may be empty; averaging days and rating returns are not.
#:
#: A frozen set rather than a method on the enum, so the rule is one line a
#: reviewer reads once instead of a branch inside two functions that could come
#: to disagree about which metrics they are talking about.
SUMMABLE_METRICS: Final[frozenset[TrendMetric]] = frozenset(
    {TrendMetric.volume, TrendMetric.paid_cents}
)

#: The rate scale, restated from `fraud.RATE_BASIS_POINTS` rather than imported.
#:
#: `rules.CURSOR_PAGE_CEILING`'s arrangement and its reason: importing it would
#: make this module's arithmetic depend on a constant owned by a surface that
#: ranks flagged populations, and the duplication is made safe by a test rather
#: than by a comment — `test_trend_analytics.py` pins the two together.
RATE_BASIS_POINTS: Final[int] = 10_000

#: The month names a bucket label is written with, in English, positionally.
#:
#: **Server-side copy, deliberately, and the one place in this package there is
#: any.** The Enums convention gives the UI the labels for an enum's members
#: because a server shipping "Settled & Closed" would be deciding copy over a
#: contract. A bucket is not an enum member: its label is a *rendering of a
#: computed period*, and the period was computed here — from the grain, the
#: anchor and the clock, none of which the browser holds. A client formatting its
#: own would be re-deriving the bucket boundary to name it, which is the
#: arithmetic AD-1 removes from the browser, and `noDerivation.test.ts` registers
#: `.bucketLabel` as a derived field for exactly that reason.
MONTH_ABBREVIATIONS: Final[tuple[str, ...]] = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

#: How many calendar months one bucket spans, for the two grains measured in
#: them. `week` is absent because a week is not a whole number of months, which
#: is the whole reason `_shift` and `_span` branch on it rather than looking it
#: up here.
_MONTHS_PER_BUCKET: Final[Mapping[TrendGrain, int]] = {
    TrendGrain.month: 1,
    TrendGrain.quarter: 3,
}


class TrendRangeInvalid(ValueError):
    """The requested range runs backwards, or runs off the calendar.

    A distinct exception from `TrendRangeTooWide` rather than one
    `TrendRangeError` with a message, because the two are different *problem
    types* on the wire and a client can act on each differently: an inverted
    range is a bug in whatever built the URL, and a too-wide one is a request to
    narrow. `_fraud_forbidden`'s ruling one layer up — one translator per
    refusal, because two copies of a problem type string is how the second one
    drifts.

    **Both refusals are one problem type, and the second one is why this is not
    named `TrendRangeInverted`.** `?to=9999-12-31` asks for a bucket whose last
    day is the first of the year ten thousand and `?to=0001-06-15` for a default
    window that starts before year one; `date` refuses both, and an unhandled
    `ValueError`/`OverflowError` from the calendar is a 500 for a request that is
    simply unanswerable. It belongs here rather than in a third exception because
    the caller's move is the same as for an inverted range — fix the dates in
    whatever built the URL — and a problem type nothing can act on differently is
    a problem type nobody reads.
    """


class TrendRangeTooWide(ValueError):
    """The requested range spans more buckets than the rules document allows.

    The cap is `trend_periods.maxBuckets` and the refusal **names it**, because
    the caller's only useful next move is to ask for a narrower window and they
    cannot do that without knowing where the edge is. Publishing the cap on the
    successful payload as well is what lets a control refuse locally before a
    request is ever made.
    """


# --- the window ----------------------------------------------------------


@dataclass(frozen=True)
class TrendBucket:
    """One period on the x-axis: how to key it, how to name it, what it covers.

    `key` is the machine identity (`2026-03`, `2026-Q1`, `2026-W12`) and is what
    a client keys a point on; `label` is what a tick reads. `start` and `end` are
    **inclusive** bounds and are what the drill-through's date facets are
    filled from, which is the reason they are published rather than left implicit
    in the key: `filter[fnolFrom]=2026-03-01&filter[fnolTo]=2026-03-31` has to be
    a copy of the boundary this fold used, not a boundary the browser re-derived
    from a key it would first have to parse.
    """

    key: str
    label: str
    start: date
    end: date


@dataclass(frozen=True)
class TrendWindow:
    """The buckets a request covers, in ascending order, and their grain.

    A bundle rather than a bare tuple because the grain travels with them: every
    consumer that has the buckets also needs to know what one of them *is*, and
    passing the pair separately is two arguments that can be handed over
    mismatched.

    Never empty. `window_for` refuses a range that produces no bucket before this
    is built, so a consumer folding over `buckets` does not need an empty branch
    and the payload's `bucket_count` is always at least one — which is what makes
    "decide emptiness on the total, never on the row count" a rule a client can
    follow rather than an edge case it has to special-case.
    """

    grain: TrendGrain
    buckets: tuple[TrendBucket, ...]


def _start_of(day: date, grain: TrendGrain) -> date:
    """The first day of the bucket `day` falls in.

    ISO Monday for a week, so a bucket boundary is the one every calendar in the
    console agrees on; the first of the month or of the calendar quarter for the
    other two. Calendar quarters rather than fiscal ones, because a fiscal year
    is an employer's property and this fold spans ten of them — a Q1 that meant
    something different per employer would be one axis describing several.
    """
    if grain is TrendGrain.week:
        return day - timedelta(days=day.weekday())
    if grain is TrendGrain.quarter:
        return date(day.year, (day.month - 1) // 3 * 3 + 1, 1)
    return date(day.year, day.month, 1)


def _shift(start: date, grain: TrendGrain, steps: int) -> date:
    """`steps` buckets forward (or back, when negative) from a bucket's start.

    Month arithmetic through a total-months integer rather than `timedelta`,
    which cannot express a month at all: adding 30 days to the 31st of January
    lands in March and would drop February from the axis entirely.
    """
    if grain is TrendGrain.week:
        return start + timedelta(weeks=steps)
    total = (start.year * 12 + start.month - 1) + _MONTHS_PER_BUCKET[grain] * steps
    return date(total // 12, total % 12 + 1, 1)


def _span(first: date, last: date, grain: TrendGrain) -> int:
    """How many buckets `first` through `last` covers, inclusive. Both are starts.

    Inclusive because a window from March to March is one month of data and not
    zero — the same reading `filter[fnolFrom]`/`filter[fnolTo]` take, so a bucket
    drilled from the axis returns exactly the claims the point was folded from.
    """
    if grain is TrendGrain.week:
        return (last - first).days // 7 + 1
    months = (last.year * 12 + last.month) - (first.year * 12 + first.month)
    return months // _MONTHS_PER_BUCKET[grain] + 1


def _bucket_at(start: date, grain: TrendGrain) -> TrendBucket:
    """One bucket, from its start day — the key, the label and both bounds.

    `end` is computed as "the day before the next bucket starts" rather than from
    a month-length table, so February, leap years and the 53-week ISO year are
    all handled by the shift arithmetic above instead of by a second calendar.
    """
    end = _shift(start, grain, 1) - timedelta(days=1)
    if grain is TrendGrain.week:
        iso = start.isocalendar()
        return TrendBucket(
            key=f"{iso.year:04d}-W{iso.week:02d}",
            label=f"Wk {iso.week:02d} {iso.year}",
            start=start,
            end=end,
        )
    if grain is TrendGrain.quarter:
        quarter = (start.month - 1) // 3 + 1
        return TrendBucket(
            key=f"{start.year:04d}-Q{quarter}",
            label=f"Q{quarter} {start.year}",
            start=start,
            end=end,
        )
    return TrendBucket(
        key=f"{start.year:04d}-{start.month:02d}",
        label=f"{MONTH_ABBREVIATIONS[start.month - 1]} {start.year}",
        start=start,
        end=end,
    )


def window_for(
    grain: TrendGrain,
    periods: TrendPeriods,
    as_of: date,
    from_date: date | None = None,
    to_date: date | None = None,
) -> TrendWindow:
    """The buckets a request covers, or a refusal naming why it cannot.

    Pure, and separated from the fold on purpose: the two refusals below are
    decided from the caller's parameters and the rules document alone, so they
    happen *before* the scoped read rather than after it. A window nobody can be
    served should not cost a query.

    **An omitted bound is filled from the document, never from the data.** `to`
    defaults to the bucket `as_of` falls in and `from` to `defaultBuckets - 1`
    buckets before it, so the default window ends *today* rather than at the
    latest claim anybody happens to have filed. Anchoring on the data would make
    the axis move when a claim is entered and would quietly hide the emptiest and
    most interesting fact a trend chart can show — that nothing has arrived for
    two months.

    The inverted check reads the caller's **raw dates** rather than their
    buckets, which is the stricter of the two and the one that matches what was
    asked: `from=2026-03-31&to=2026-03-01` is a backwards range even though both
    days land in one March bucket, and answering it with a cheerful single-bucket
    series would be this function deciding the caller meant something else.

    **The two calendar-bound guards are refusals, not defence.** A window is
    arithmetic on `date`, and `date` has ends: `to=9999-12-31` needs the day
    before the year ten thousand to close its last bucket and `to=0001-06-15`
    needs eleven months before year one to open its first. Both are questions
    with no answer rather than faults, so both become the same 422 the inverted
    range gets — and they are caught around the two statements that can raise
    rather than around the whole body, because `TrendRangeTooWide` is itself a
    `ValueError` and a blanket `except` here would swallow the cap refusal and
    republish it as an invalid range.
    """
    if from_date is not None and to_date is not None and from_date > to_date:
        raise TrendRangeInvalid(
            f"The range starts on {from_date} and ends on {to_date}, which is earlier."
        )
    try:
        last = _start_of(to_date if to_date is not None else as_of, grain)
        first = (
            _start_of(from_date, grain)
            if from_date is not None
            else _shift(last, grain, -(periods.default_buckets - 1))
        )
    except (ValueError, OverflowError) as exc:
        raise TrendRangeInvalid(_off_the_calendar(grain)) from exc
    if first > last:
        raise TrendRangeInvalid(
            f"The range starts in the {grain.value} of {first} and ends in the earlier "
            f"{grain.value} of {last}."
        )
    span = _span(first, last, grain)
    if span > periods.max_buckets:
        raise TrendRangeTooWide(
            f"That range covers {span} {grain.value}s and the widest window is "
            f"{periods.max_buckets}; ask for a narrower range or a wider grain."
        )
    try:
        buckets = tuple(_bucket_at(_shift(first, grain, step), grain) for step in range(span))
    except (ValueError, OverflowError) as exc:
        raise TrendRangeInvalid(_off_the_calendar(grain)) from exc
    return TrendWindow(grain=grain, buckets=buckets)


def _off_the_calendar(grain: TrendGrain) -> str:
    """The refusal a window that leaves `date`'s range gets.

    One sentence in one place, `_trend_range_invalid`'s ruling one layer down: a
    message written at each of the two `except` clauses is two messages the day
    somebody improves one of them. It names the grain because that is half of
    what the caller can change — a quarter window reaches four times as far back
    from the same `to` as a month one does.
    """
    return (
        f"That range needs a {grain.value} outside the dates this console can bucket; "
        "ask for one inside the ordinary calendar."
    )


# --- the projection and the computers ------------------------------------


@dataclass(frozen=True)
class TrendClaim:
    """One claim: the nine facts the five series fold, plus the seven the
    segmentation filter narrows on.

    A projection rather than the ORM entity, for `ChartClaim`'s two reasons: it
    keeps `trends_of` pure and generatable, and it puts every input to the five
    metrics and the three cohort keys next to each other instead of spread across
    a row object carrying forty other columns.

    The two dates sit adjacent because they are the two anchors and exactly one
    of them is chosen per request — which is the distinction this module is
    arranged around, and the one a reader should be able to check by looking at
    one class. Both are non-nullable columns, so there is no "claims we could not
    bucket" branch anywhere below.

    The three `paid_*` fields are named exactly as `derivations.PaidColumns`
    declares them, which is the point of that protocol being structural: this
    class satisfies it by shape, so `total_paid` adds up a row projection without
    this module importing an ORM entity to add three integers.

    `sample` is an `SlaSample` rather than loose columns because the SLA
    aggregation owns those facts and their meaning (AD-2). Nothing here unpacks
    it; it is handed straight back to `sla.strip_of`, one bucket at a time.

    **Story 7.3 widens it by seven and re-uses three.** `severity_score`,
    `disability` and `sector` were already here as *cohort* keys — the three
    dimensions a series may be split by — and the workspace's segmentation
    narrows on all three plus seven more. Nothing in this file reads the seven:
    they are handed to `segmentation.matches`, which is what makes this class
    satisfy `segmentation.SegmentedClaim` **structurally**, by shape, without
    this module importing that protocol's other consumers or the ORM.

    A cohort and a filter over the same column are still different things, and
    `TrendCohort`'s docstring says so: a split *partitions* the population and a
    filter *narrows* it, so the cohort series over one metric sum back to the
    unsplit one whatever the filter is.

    The seven arrive from `select_claim_columns_with_employer_and_employee`,
    which is the fourth member of the projection family and exists because two of
    them are columns of `employee` — see that function on why the employer-only
    sibling `charts.portfolio_charts` shares was not given a second join instead.
    """

    froi_date: date
    doi: date
    severity_score: int
    disability: Disability
    sector: str
    paid_indemnity: int
    paid_medical: int
    paid_expense: int
    sample: sla.SlaSample
    # Story 7.3's seven — the segmentation vocabulary's remaining columns, read
    # by nothing in this module and by `services/worklist/segmentation.py` alone.
    injury_type: str
    state: str
    employer_id: int
    region: str
    icd: str
    age: int
    gender: Gender


@dataclass(frozen=True)
class _Computers:
    """The three registered computers this module folds with, built once.

    A bundle rather than three parameters, for `fraud._Computers`' reason:
    `_cohort_key` and the two accumulating folds are called once per claim in a
    loop, and a three-argument call there would put the build order and the call
    order in two places.

    All three are asked of the registry (AD-10). `days_open` and `total_paid` are
    parameterless and are built through `for_thresholds` anyway, because the
    registry is the answer to "who computes this?" rather than a configuration
    mechanism — and because a value reached by two different routes is a value
    with two owners waiting to happen.
    """

    risk: RiskDerivation
    days_open: OpenDurationDerivation
    total_paid: TotalPaidDerivation

    @classmethod
    def of(cls, thresholds: DerivationThresholds) -> "_Computers":
        """Build all three from one parameter block — the registry's whole point."""
        return cls(
            risk=derivations.risk.for_thresholds(thresholds),
            days_open=derivations.days_open.for_thresholds(thresholds),
            total_paid=derivations.total_paid.for_thresholds(thresholds),
        )


# --- the finished series -------------------------------------------------


@dataclass(frozen=True)
class TrendPoint:
    """One bucket of one series: what it was, what it says, and how much is behind it.

    `value` is `None` when the metric has nothing to average or rate over, and
    **0 is never used as a stand-in** — that is the prohibition this whole story
    exists for (AC 3), inherited from `sla.SlaMetric` rather than invented.

    `claim_count` travels beside every value, including the null ones, and it is
    the field that makes a point interpretable: a settlement mean over two claims
    and one over forty are the same kind of number and not the same kind of
    evidence. It is **this metric's** population and not the bucket's — the
    claims that fed *this* value — which is a distinction three of the five
    series never notice and two of them turn on: a bucket of seven claims of
    which three settled with a recorded duration publishes `claim_count: 3` on
    its settlement point and `7` on its volume point, because a mean over three
    is evidence of three whatever else was filed that month.

    `low_confidence` is decided **here**, against `trend_periods
    .lowConfidenceClaimMax`, rather than published as a count for the browser to
    compare — a threshold applied in the SPA is a rule in the browser (AD-8), and
    it would be the one rule on this payload nobody could see change. It is
    decided on `claim_count`, so it says the same thing the count does: a
    settlement mean over three claims is thin even in a busy month.

    `partial` is true when the bucket had not finished by `as_of` — the newest
    bucket of an ordinary window, always. Every metric on such a point is a part
    period drawn at a whole period's width, and the direction differs per metric
    (volume and paid are short, `avg_days_open` is dragged toward zero by claims
    days old), so the honest move is to publish the fact and let the card mark
    it rather than to extrapolate five figures five ways.

    `bucket_from` and `bucket_to` ride along on every point rather than once per
    payload because the drill-through is per point: clicking a bucket has to
    produce `filter[fnolFrom]`/`filter[fnolTo]` from the boundary this fold used,
    and looking it up in a parallel array is one index slip away from opening the
    claims behind the month next door.
    """

    bucket_key: str
    bucket_label: str
    bucket_from: date
    bucket_to: date
    value: int | None
    claim_count: int
    low_confidence: bool
    partial: bool


@dataclass(frozen=True)
class TrendSeries:
    """One line: which metric, which cohort, and everything a caption needs.

    - `points` — one per bucket in the window, in the window's order, always the
      full set. A metric with nothing to say in a bucket says `None` there; it
      does not skip the bucket, because a line with a hole in its *index* would
      make two series on one chart disagree about where March is.
    - `cohort_key` / `cohort_label` / `palette_slot` — all three `None` on an
      unsplit series, which is what makes "is this a cohort?" a field rather
      than an inference from the request.
    - `series_total` — the claims behind this series across the whole window, and
      **the field emptiness is decided on**. A claim count rather than a value
      total for all five metrics, deliberately: the empty question is "was there
      anything here to describe", which is the same question for a mean as for a
      sum, and a summed mean is not a number. It is the sum of the points'
      `claim_count`s and therefore **per metric**, which is the whole of the
      difference between "this window has no claims in it" and "nothing in this
      window has settled" — a hundred-claim scope with no settlement anywhere
      gets the settlement card's empty sentence rather than a blank plot with
      axes and a target line on it.
    - `value_total` — the window total *of the metric*, and `None` for the three
      that cannot be summed. `None` rather than zero for the same reason a point
      is: "these three do not have a window total" is a different statement from
      "their window total is nothing".
    - `no_data_buckets` — how many of the points are `None`. Published rather
      than left to a client counting them, because that count is the card's
      footnote ("3 of 12 periods have no data") and counting nulls in the browser
      is the arithmetic AD-1 removes from it.
    """

    metric: TrendMetric
    cohort_key: str | None
    cohort_label: str | None
    palette_slot: int | None
    points: tuple[TrendPoint, ...]
    series_total: int
    value_total: int | None
    no_data_buckets: int


@dataclass(frozen=True)
class PortfolioTrends:
    """Five metrics over one window of one scoped book, plus what the axis means.

    **`as_of` is on the payload**, which is new on this dashboard and is the
    point rather than a detail: `avg_days_open` counts up to a day, and a chart
    that showed ages without saying which day they were measured on would be
    unreadable the moment it was stored, forwarded or screenshotted. It is also
    the clock every bucket boundary was decided against.

    **The window is described three ways and none of them is redundant.**
    `grain` and `anchor` say what a bucket *is* and what puts a claim in one;
    `window_from`/`window_to` are the outer bounds a caption quotes;
    `bucket_count` is what a client checks its point count against without
    counting an array. `default_buckets` and `max_buckets` ride along so a period
    control can offer exactly the range this deployment permits and refuse a
    wider one before a request is made, rather than discovering the cap from a
    422.

    **Two rule documents, named separately.** `rules_version` is
    `derivation_thresholds` — the document the severity cohort's band edges come
    from — exactly as it means on the fraud payloads, and the window parameters
    travel as `periods_version`. `deferred-work.md` records that `rulesVersion`
    already names different documents on different routes; publishing two
    explicitly named fields is the one move that reduces that ambiguity rather
    than adding to it. Both are added by the router from the blocks it loaded,
    exactly as `charts_of` leaves its version to the route.

    **The two targets and the two band edges travel with the figures**, for the
    reason `PortfolioCharts` publishes its two: a reference line on the RTW chart
    and a legend reading "High (≥ N)" are quotations of rules, and a client
    holding either would be a second copy of a rule it cannot see change. The
    targets are read off the `SlaTarget`s the strip was decided against and the
    edges off the derivation that did the banding, so both are provably the ones
    the fold used.

    `claims_in_scope` and `claims_in_window` are two different facts and the
    difference is worth stating on screen: the first is the population these
    figures describe, the second is how much of it the chosen window actually
    covers. A window that describes eleven of a hundred claims is not wrong, but
    a reader who thinks it describes a hundred is.

    **Since Story 7.3 `claims_in_scope` is the *segmented* book rather than the
    caller's whole one**, and the change is deliberate: with a filter applied,
    every figure on this payload describes the intersection, so a denominator
    quoting the unfiltered portfolio would put "11 of 100" under charts folded
    from twenty. `fraud.FraudPanel.claims_in_scope` means the same thing for the
    same reason. The unfiltered count has not gone anywhere — it is
    `claimsInScope` on `GET /dashboard/segmentation/values`, published beside
    `claimsMatching` precisely so the workspace can state both.

    **`buckets` is the window's vocabulary, published independently of the
    series**, and it closes a defect rather than adding a convenience. Until this
    story the only place a client could learn which periods the window covered
    was by walking a series' points — so an empty window under a cohort split
    published *no series at all*, and the period `<select>` and its "View claims"
    button both went dead, while the same empty window under `cohort=none` left
    them live because zero-filled points still exist. The availability of the
    keyboard's only drill path depended on an unrelated selector. A nine-dimension
    AND makes an empty result routine rather than rare, so the vocabulary is now a
    fact about the *window* — which is what it always was — and is on the payload
    whatever the fold found. `deferred-work.md` records the entry this closes.
    """

    as_of: date
    grain: TrendGrain
    anchor: TrendAnchor
    cohort: TrendCohort
    window_from: date
    window_to: date
    bucket_count: int
    claims_in_scope: int
    claims_in_window: int
    buckets: tuple[TrendBucket, ...]
    series: tuple[TrendSeries, ...]
    default_buckets: int
    max_buckets: int
    low_confidence_claim_max: int
    settle_target_days: int
    rtw_target_bp: int
    high_risk_severity_min: int
    med_risk_severity_min: int


# --- the pure fold -------------------------------------------------------


@dataclass
class _Tally:
    """One (cohort, bucket) cell's raw material, accumulated in the single pass.

    Mutable and private, unlike everything else in this module, because it is
    scratch: it exists between the one loop over the caseload and the shaping
    step below, and nothing outside this file ever sees one. `charts_of`
    accumulates into six plain dicts for the same reason; this one is a class
    only because a cell holds four different things and a four-tuple would put
    their order in every `+=`.

    `samples` is a list of `SlaSample` and is never *read* here — it is handed to
    `sla.strip_of` whole, once per cell (AD-2). Accumulating the samples rather
    than a running settlement total is the shape that keeps that true: a total
    would mean this class knowing which claims are eligible for the mean, which
    is precisely the definition the aggregation owns.

    `add` takes the built computers rather than the threshold block, which is the
    difference between one build and one per claim — `fraud.flags_of`'s shape,
    for its reason.
    """

    claims: int = 0
    paid_cents: int = 0
    days_open: int = 0
    samples: list[sla.SlaSample] = field(default_factory=list)

    def add(self, claim: TrendClaim, computers: _Computers, as_of: date) -> None:
        self.claims += 1
        self.paid_cents += computers.total_paid.of(claim)
        self.days_open += computers.days_open.of(claim.froi_date, as_of)
        self.samples.append(claim.sample)


def _anchor_day(claim: TrendClaim, anchor: TrendAnchor) -> date:
    """The date this request buckets by. One `if`, in one place.

    Extracted rather than inlined into the loop so that "exactly one anchor per
    request" is a function a reviewer reads once, instead of a conditional
    embedded in an accumulation where a second one could grow beside it.
    """
    return claim.froi_date if anchor is TrendAnchor.fnol else claim.doi


def cohort_key_of(claim: TrendClaim, cohort: TrendCohort, computers: _Computers) -> str | None:
    """Which cohort this claim belongs to, or `None` when the split is off.

    Each dimension reached through the symbol that owns it: the severity band
    through the registered `risk` derivation — the same band the High Risk card
    counts and the severity donut draws, so a cohort here and a slice there can
    never be two readings of one score — and the other two through their own
    columns.

    The answer is always the **wire** spelling (snake_case for the two enums, the
    stored string for the free-text one), because it is what `palette_slot` is
    sorted on and what the drill-through's `filter[severityBand]` /
    `filter[disability]` / `filter[sector]` facets match. A display label sorted
    or filtered on would be `AppliedFilter`'s recorded failure: two labels can
    collapse where two keys cannot.
    """
    if cohort is TrendCohort.severity_band:
        return computers.risk.of(claim.severity_score).value
    if cohort is TrendCohort.disability:
        return claim.disability.value
    if cohort is TrendCohort.sector:
        return claim.sector
    return None


def _rounded_days(total: int, claims: int) -> int:
    """A mean number of days, half-up, at whole-day precision.

    Exact decimal arithmetic rather than `total / claims`, `sla._rounded`'s
    argument restated over a different column: binary floating point turns a true
    29.5 into 29.499999999999996 often enough to move a rendered figure, and
    half-up rather than Python's bankers' rounding because a chart that rounded
    10.5 down and 11.5 up is a defect report waiting to be filed.

    Whole days rather than a decimal, matching the precision `sla.targets_for`
    decides the settlement tile at — the two day-valued series sit on one screen,
    and two precisions for one unit is a reconciliation the reader would have to
    do.
    """
    return int((Decimal(total) / Decimal(claims)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class _SlaCell:
    """One bucket's two SLA answers, each beside the population it was folded over.

    A pair of pairs rather than two tuples, for `_Tally`'s reason turned up one
    notch: four values of which two are `int | None` and two are `int`, and the
    two that are never null are counts of two *different* populations. A
    four-tuple would put that order in every unpacking site, and transposing the
    settlement mean's denominator with the rate's would type-check and publish
    two plausible counts.

    The counts are `sla.denominators_of`'s, not this module's (AD-2) — see
    `_settlement_and_rtw`.
    """

    settle: int | None
    settle_claims: int
    rtw: int | None
    rtw_claims: int


def _settlement_and_rtw(
    tally: _Tally,
    targets: Mapping[sla.SlaMetricKey, sla.SlaTarget],
) -> _SlaCell:
    """One bucket's settlement mean and RTW rate — **one** call to `sla.strip_of`.

    AD-2 in one function. This module does not know what a settlement mean is,
    what its denominator is, or which claims are eligible for it: it hands the
    bucket's samples to the aggregation that owns those definitions and reads two
    of the four answers back. `strip_of`'s deliberate asymmetry — the settlement
    mean over settled claims that carry a duration, the RTW rate over *all*
    settled claims — arrives with them, which is exactly why it is not restated
    here.

    Both are `None` when `strip_of` says `no_data`, which is the whole of the
    null-versus-zero rule for these two series: the vocabulary is inherited
    rather than invented.

    **The rate is the strip's own figure on the console's integer rate scale.**
    `sla.targets_for` decides the RTW tile at whole-percent precision, so the
    basis-point value published here is always a multiple of a hundred. That is
    the honest consequence of not re-averaging rather than a precision this
    module chose: computing a finer rate would mean a second denominator for a
    figure that already has one, which is the thing AD-2 forbids. If the rate
    ever needs more precision, `sla.py` is where it gains it and both the tile
    and this series move together.

    **The two denominators come back the same way the two values do.** A point
    publishes how many claims fed it, and for these two that is neither the
    bucket's claim count nor one number for both — the mean is over settled
    claims carrying a duration and the rate over every settled claim. Counting
    either here would mean this module holding half of a definition it just
    refused to hold the other half of, and it could not do it anyway: the columns
    those populations are decided from are ones this file may not name
    (`test_nothing_outside_the_worklist_aggregation_reads_the_sla_source_columns`).
    So `sla.denominators_of` answers, over the same samples, from the same
    comprehensions `strip_of` divided by.
    """
    strip = sla.strip_of(tally.samples, targets)
    behind = sla.denominators_of(tally.samples)
    settle = strip[sla.SlaMetricKey.settle].value
    rtw = strip[sla.SlaMetricKey.rtw_rate].value
    return _SlaCell(
        settle=None if settle is None else int(round(settle)),
        settle_claims=behind[sla.SlaMetricKey.settle],
        rtw=None if rtw is None else int(round(rtw * (RATE_BASIS_POINTS // 100))),
        rtw_claims=behind[sla.SlaMetricKey.rtw_rate],
    )


def _point_value(metric: TrendMetric, tally: _Tally, cell: _SlaCell) -> int | None:
    """One cell's published value — the null-versus-zero rule, in one table.

    A single function rather than five branches spread through the shaping loop,
    for `drill_through._PREDICATES`' reason: "which metrics zero-fill and which
    go null" is the rule this story is most likely to get quietly wrong, and it
    should be checkable by reading one screen rather than by tracing five
    accumulations.

    The two counts answer even for an empty cell — a bucket nobody filed a claim
    in saw zero claims and paid zero cents, both of which are facts. The mean
    goes `None` on an empty cell, and the two SLA figures go `None` whenever
    `strip_of` says so, which is a *narrower* condition: a bucket can hold twenty
    claims and still have no settled one.

    **The last branch is a raise rather than a fall-through**, which is the
    difference between adding a sixth metric and publishing the RTW rate under
    its name. The enum is closed and every member is spelled out, so a member
    added above without a line here fails loudly on the first bucket instead of
    drawing a plausible line under the wrong caption; `_point_population` is
    written the same way for the same reason, and mypy's exhaustiveness check
    cannot see either because both return a type the last branch could satisfy.
    """
    if metric is TrendMetric.volume:
        return tally.claims
    if metric is TrendMetric.paid_cents:
        return tally.paid_cents
    if metric is TrendMetric.avg_days_open:
        return None if tally.claims == 0 else _rounded_days(tally.days_open, tally.claims)
    if metric is TrendMetric.avg_settlement_days:
        return cell.settle
    if metric is TrendMetric.rtw_rate_bp:
        return cell.rtw
    raise AssertionError(f"no value rule for trend metric {metric!r}")


def _point_population(metric: TrendMetric, tally: _Tally, cell: _SlaCell) -> int:
    """How many claims fed this cell's value — the denominator, per metric.

    The table beside `_point_value`'s and deliberately a second function rather
    than a second return from it: a value and the population behind it are two
    facts a reader checks against each other, and reading them from two tables
    written to the same shape is how a reviewer sees that `avg_settlement_days`
    takes its count from a different place than `avg_days_open` does.

    Three of the five are the bucket's own claim count, and that is not laziness:
    a count *is* its own population, a mean of days open is over every claim in
    the bucket, and a sum of cents is over every claim that paid any. The two SLA
    metrics are the exceptions and their counts arrive from the aggregation that
    owns their denominators — `sla.denominators_of`, through `_SlaCell`.

    An empty cell answers `0` on all five, which is what makes `low_confidence`'s
    `0 < claims` half do its job: a bucket with nothing behind it is not thin
    evidence, it is none, and its value is already `None`.
    """
    if metric in (TrendMetric.volume, TrendMetric.paid_cents, TrendMetric.avg_days_open):
        return tally.claims
    if metric is TrendMetric.avg_settlement_days:
        return cell.settle_claims
    if metric is TrendMetric.rtw_rate_bp:
        return cell.rtw_claims
    raise AssertionError(f"no population rule for trend metric {metric!r}")


def trends_of(
    caseload: Sequence[TrendClaim],
    window: TrendWindow,
    anchor: TrendAnchor,
    cohort: TrendCohort,
    computers: _Computers,
    targets: Mapping[sla.SlaMetricKey, sla.SlaTarget],
    periods: TrendPeriods,
    as_of: date,
) -> PortfolioTrends:
    """The five series over one caseload and one window. Pure — no session, no clock.

    `charts_of`'s split and its reasons: the arithmetic is database-free and
    Hypothesis-testable, and the one scoped read happens in the async wrapper
    below. `as_of` arrives as an argument rather than being read here, which is
    what makes "one clock per request" a property of the signature.

    **One pass accumulating one cell per (cohort, bucket)**, then one shaping
    step per metric. Five passes over the same list would produce the same
    numbers today and is the wrong shape — `charts_of`'s argument, sharper here
    because the five series are drawn on one screen under one window caption:
    five separate folds would be five opportunities for one of them to be written
    against a differently-windowed copy, and the failure would look like a real
    trend.

    `sla.strip_of` is called once per cell, before the metric loop, so the
    settlement mean and the RTW rate on one bucket are two answers from one
    aggregation over one set of samples rather than two folds that agree.

    Claims whose anchor date falls outside the window are **dropped**, not
    bucketed into the nearest edge. A window is a question about a period, and a
    claim from before it is not a claim from its first month; `claims_in_window`
    beside `claims_in_scope` is what stops that exclusion being silent.

    Every band and every total comes from the computers passed in (AD-10). This
    function holds no threshold, no cut-off and no comparison against a score.
    """
    # The window's own buckets, indexed by their first day. Built once, and the
    # lookup is deliberately unguarded: the buckets tile the window
    # contiguously, so a day inside it *has* a bucket, and a `KeyError` here
    # would mean the window and the bucket arithmetic had come to disagree —
    # which is a fault to raise rather than a claim to drop silently.
    by_start = {bucket.start: bucket.key for bucket in window.buckets}
    cells: dict[tuple[str | None, str], _Tally] = {}
    cohort_keys: set[str] = set()
    in_window = 0

    for claim in caseload:
        day = _anchor_day(claim, anchor)
        if not window.buckets[0].start <= day <= window.buckets[-1].end:
            continue
        in_window += 1
        key = cohort_key_of(claim, cohort, computers)
        if key is not None:
            cohort_keys.add(key)
        cells.setdefault((key, by_start[_start_of(day, window.grain)]), _Tally()).add(
            claim, computers, as_of
        )

    # Ascending wire key, which is the order `palette_slot` is assigned in and
    # therefore the order a legend reads in. Deliberately **not** each
    # dimension's own declaration order: severity band has one and the other two
    # do not, so a per-dimension order would be three rules where this is one,
    # and it must not be a *ranked* order — `chartTheme`'s palette docstring
    # records that a hue there means rank position, which is the flaw
    # `palette_slot` exists to fix. An order that moved with the data would move
    # the colours with it.
    ordered_cohorts: tuple[str | None, ...] = (
        (None,) if cohort is TrendCohort.none else tuple(sorted(cohort_keys))
    )
    # Computed once per cell rather than once per (cell, metric): two of the five
    # metrics read it, and `strip_of` folds a bucket's whole sample list.
    sla_cells = {cell_key: _settlement_and_rtw(tally, targets) for cell_key, tally in cells.items()}
    empty = _Tally()
    # The answer for a cell nobody filed a claim into: two `None`s over two empty
    # populations. Built once beside the empty tally rather than written at the
    # `.get` below, so "an absent cell is an empty one" is one statement.
    nothing = _SlaCell(settle=None, settle_claims=0, rtw=None, rtw_claims=0)

    series: list[TrendSeries] = []
    for metric in TrendMetric:
        for slot, cohort_key in enumerate(ordered_cohorts):
            points = tuple(
                _point_of(
                    bucket,
                    metric,
                    cells.get((cohort_key, bucket.key), empty),
                    sla_cells.get((cohort_key, bucket.key), nothing),
                    periods,
                    as_of,
                )
                for bucket in window.buckets
            )
            # Per metric, because `claim_count` is: the settlement series' total
            # is the settled durations behind it and not the book beside them,
            # which is what makes the empty state answer "nothing here has
            # settled" instead of "this window is empty".
            claims = sum(point.claim_count for point in points)
            series.append(
                TrendSeries(
                    metric=metric,
                    cohort_key=cohort_key,
                    # `None` for all three dimensions this story ships, and the
                    # field exists anyway: a cohort *key* is an identity and a
                    # *label* is copy. Severity band and disability are enums
                    # whose labels the SPA owns (the Enums convention — a server
                    # that shipped "Permanent Partial" would be deciding copy
                    # over a contract), and sector is free text where the stored
                    # value *is* the label, so publishing it would be echoing the
                    # key. `AppliedFilter.display`'s exact split, and the client
                    # rule is the same one: `label ?? key`. It is published
                    # rather than omitted so a dimension keyed on an *id* —
                    # employer or handler, Story 7.3's territory — can fill it
                    # without the client changing.
                    cohort_label=None,
                    palette_slot=None if cohort_key is None else slot,
                    points=points,
                    series_total=claims,
                    value_total=(
                        sum(point.value or 0 for point in points)
                        if metric in SUMMABLE_METRICS
                        else None
                    ),
                    no_data_buckets=sum(1 for point in points if point.value is None),
                )
            )

    return PortfolioTrends(
        as_of=as_of,
        grain=window.grain,
        anchor=anchor,
        cohort=cohort,
        window_from=window.buckets[0].start,
        window_to=window.buckets[-1].end,
        bucket_count=len(window.buckets),
        claims_in_scope=len(caseload),
        claims_in_window=in_window,
        # The window's own buckets, whatever the fold found in them — see
        # `PortfolioTrends`. Passed through rather than rebuilt: `window_for`
        # computed them before the read, and a second construction here would be
        # a second calendar for one axis.
        buckets=window.buckets,
        series=tuple(series),
        default_buckets=periods.default_buckets,
        max_buckets=periods.max_buckets,
        low_confidence_claim_max=periods.low_confidence_claim_max,
        # Read off the `SlaTarget`s the strip was decided against rather than off
        # `Settings`, so a reference line is provably the line the tile's verdict
        # used — `PortfolioCharts`' rule over a target instead of a band edge.
        # The rate target is scaled the way the rate value is: a target on a
        # different scale from the series it grades is a line nobody can draw.
        settle_target_days=int(round(targets[sla.SlaMetricKey.settle].value)),
        rtw_target_bp=int(round(targets[sla.SlaMetricKey.rtw_rate].value * 100)),
        # Read off the derivation that did the banding rather than off the
        # threshold block, so the published edges are provably the ones the
        # severity cohort was split at — `PortfolioCharts`' rule.
        high_risk_severity_min=computers.risk.high_min,
        med_risk_severity_min=computers.risk.med_min,
    )


def _point_of(
    bucket: TrendBucket,
    metric: TrendMetric,
    tally: _Tally,
    cell: _SlaCell,
    periods: TrendPeriods,
    as_of: date,
) -> TrendPoint:
    """One cell as a published point.

    Named rather than positional at every field for `charts.portfolio_charts`'
    reason, on a class carrying two adjacent strings, two adjacent dates and two
    adjacent integers: a positional build would type-check the whole way and put
    a bucket's start where its end belongs.

    **`claim_count` is the metric's population, not the bucket's**, and
    everything downstream of it follows: the verdict below is decided on it and
    `series_total` sums it. See `TrendPoint`.

    **`low_confidence` is `0 < claims <= max`, and the left half is not
    decoration.** A bucket with no claims at all is not "low confidence", it is
    *no* confidence — its mean is already `None` and marking it thin as well
    would put a warning badge on a gap. The two states are different answers and
    the UI draws them differently.

    **`partial` compares the bucket's last day against the clock**, and that is a
    comparison of two facts rather than a cut-off: `as_of` is the request's own
    resolved day and `bucket.end` is arithmetic this module did, so there is no
    rule document with a stake in it and nothing here to move into one (AD-8). A
    window ending in the future — an explicit `to` past today — marks every
    unfinished bucket rather than only the last, because "this period is not
    over" is true of all of them.
    """
    behind = _point_population(metric, tally, cell)
    return TrendPoint(
        bucket_key=bucket.key,
        bucket_label=bucket.label,
        bucket_from=bucket.start,
        bucket_to=bucket.end,
        value=_point_value(metric, tally, cell),
        claim_count=behind,
        low_confidence=0 < behind <= periods.low_confidence_claim_max,
        partial=as_of < bucket.end,
    )


# --- the endpoint's one call ---------------------------------------------


async def portfolio_trends(
    db: AsyncSession,
    ctx: CallerContext,
    thresholds: DerivationThresholds,
    periods: TrendPeriods,
    settings: Settings,
    filters: Segmentation,
    *,
    grain: TrendGrain = TrendGrain.month,
    anchor: TrendAnchor = TrendAnchor.fnol,
    cohort: TrendCohort = TrendCohort.none,
    from_date: date | None = None,
    to_date: date | None = None,
    as_of: date | None = None,
) -> PortfolioTrends:
    """The Trends section's five series for the caller's book — the endpoint's one call.

    Takes both parameter blocks and its settings rather than fetching any of
    them, `portfolio_charts`' signature and its reason: the route loads them once
    and hands them down, so this stays a composition of scope and parameters
    instead of dragging the rules engine into an aggregate that mentions a band.
    `settings` arrives because the SLA targets are deployment configuration and
    `sla.targets_for` is the one place they become targets.

    **Five keyword-only parameters and one filter block, and not one of them is a
    scope.** A grain, an anchor, a cohort dimension and two dates decide what is
    *drawn*; the `Segmentation` decides which claims are folded into it. There is
    nowhere in either to put an employer, a user or an "as" — `Segmentation`
    carries `employer_id`, which **narrows** the caller's book over rows a
    scope-predicated read already returned and can never widen it, exactly as
    `filter[employerId]` does on the drill list. That is what keeps
    `test_query_parameters_cannot_widen_or_change_the_scope` a property of the
    shape rather than of a validator (AD-7). The five stay keyword-only because
    `from_date` and `to_date` are two adjacent optional dates and a transposed
    pair would type-check and silently invert a window.

    One scoped read, one pure fold — and literally so: there is no other `await`
    in this body. `test_the_trends_aggregate_takes_exactly_one_scoped_read`
    counts the statements, and it stays at one **with a filter applied**, because
    segmentation narrows the fold rather than adding a read.

    The projection is `sla.SAMPLE_COLUMNS` widened with the columns the five
    metrics, the three cohorts and the ten segmentation dimensions need beyond
    the cycle time, and it comes from
    `select_claim_columns_with_employer_and_employee` rather than from
    `charts.portfolio_charts`' employer-only sibling. That is a **fourth**
    projection-family read rather than a second join on the third, and the reason
    is written down at the repository: two segmentation dimensions are columns of
    `employee`, and giving the shared function an `employee` join in place would
    change the statement a Story 5.3 aggregate runs for the benefit of a filter it
    has no use for.

    **The window is cut before the filter, and both before the fold.** A window
    that holds no claim at all still publishes its buckets, so an impossible
    segmentation empties the series and leaves the period control and its drill
    button live — the defect `deferred-work.md` records against Story 7.2, and the
    reason `PortfolioTrends.buckets` exists.

    **The clock is resolved here, once**, and threaded into the window and into
    every `days_open` call below it. Two claims in one request may never be
    scored against different days, and a request straddling UTC midnight may
    never bucket half its book into one month and half into the next.

    **The window is built before the read**, so a range nobody can be served
    costs no query: both refusals are decided from the caller's parameters and
    the rules document alone.

    Raises `FraudAnalyticsNotPermitted` (403) for any role outside
    `fraud.FRAUD_ANALYTICS_ROLES`, **before the window and before the read**. The
    route calls the same gate first as well, so the refusal also precedes the two
    rule-document reads it makes on this function's behalf; the check here stays
    because capability belongs with the service that owns the rows and not with
    one caller.
    """
    require_fraud_analytics_access(ctx)
    today = as_of or derivations.utc_today()
    window = window_for(grain, periods, today, from_date, to_date)
    computers = _Computers.of(thresholds)
    rows = await claim_repo.select_claim_columns_with_employer_and_employee(
        db,
        ctx,
        [
            *sla.SAMPLE_COLUMNS,
            Claim.froi_date,
            Claim.doi,
            Claim.severity_score,
            Claim.disability,
            Claim.paid_indemnity,
            Claim.paid_medical,
            Claim.paid_expense,
            Employer.sector,
            # Story 7.3's seven, for the segmentation filter and for nothing this
            # module reads. `Employee.age` and `Employee.gender` are why the read
            # is the four-table sibling rather than the three-table one.
            Claim.injury_type,
            Claim.state,
            Claim.employer_id,
            Claim.region,
            Claim.icd,
            Employee.age,
            Employee.gender,
        ],
    )
    # Named rather than positional (`TrendClaim(*row)`), which would work and
    # would be one reordered projection away from bucketing every claim by its
    # injury date under a chart captioned FNOL — `charts.portfolio_charts`'
    # argument, on a projection whose two most confusable columns are two dates
    # sitting adjacent.
    caseload = [
        TrendClaim(
            froi_date=row.froi_date,
            doi=row.doi,
            severity_score=row.severity_score,
            disability=row.disability,
            sector=row.sector,
            paid_indemnity=row.paid_indemnity,
            paid_medical=row.paid_medical,
            paid_expense=row.paid_expense,
            sample=sla.sample_of(row),
            injury_type=row.injury_type,
            state=row.state,
            employer_id=row.employer_id,
            region=row.region,
            icd=row.icd,
            age=row.age,
            gender=row.gender,
        )
        for row in rows
    ]
    return trends_of(
        # Narrowed before the fold, so `claims_in_scope`, `claims_in_window`,
        # every cohort's vocabulary and every point describe one population.
        # Filtering inside `trends_of` instead would put the filter in the same
        # loop as the bucketing, where a cohort key computed before the predicate
        # would publish a legend entry for a line nobody drew.
        segmentation_narrowed(caseload, filters, SegmentationComputers.of(thresholds)),
        window,
        anchor,
        cohort,
        computers,
        sla.targets_for(settings),
        periods,
        today,
    )


__all__ = [
    "MONTH_ABBREVIATIONS",
    "RATE_BASIS_POINTS",
    "SUMMABLE_METRICS",
    "PortfolioTrends",
    "TrendAnchor",
    "TrendBucket",
    "TrendClaim",
    "TrendCohort",
    "TrendGrain",
    "TrendMetric",
    "TrendPoint",
    "TrendRangeInvalid",
    "TrendRangeTooWide",
    "TrendSeries",
    "TrendWindow",
    "cohort_key_of",
    "portfolio_trends",
    "trends_of",
    "window_for",
]
