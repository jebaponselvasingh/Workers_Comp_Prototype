"""Story 7.2 — the trend axis, and the three cuts it stacks on one another.

Two halves, `test_fraud_analytics.py`'s arrangement, and the first one is in that
shape for a reason specific to this story rather than as a convention.

**A trend is a window, a bucket and a cohort — a cut, a partition, and a
partition of the partition — and the seeded portfolio can demonstrate none of
them at their edges.** Every seeded claim was filed between January and September
of one year, so no seeded request crosses a year boundary, lands on a leap day,
touches the 53-week ISO year, or produces a bucket whose claims are all unsettled
beside one whose claims are all settled. The synthetic caseloads below are
therefore not thoroughness: they are the only place the boundary behaviour is
tested at all, and the only place the null-versus-zero rule — the prohibition
AC 3 exists for — can be exercised metric by metric.

The second half is the endpoint, and its centre of gravity is the
**reconciliation set** and the **gate**.

Reconciliation is `test_drill_through.py`'s discipline: the same population is
read off `/dashboard/trends`, off `/dashboard/summary`, off `/dashboard/charts`
and off `/dashboard/claims` in one test on one scope, and the four are asserted
equal with `seed_fixture`'s independent oracle beside them. A constant would pass
while every surface was wrong together; an equality between live aggregates
cannot, and the oracle catches the case where they agree on a wrong answer. That
oracle walks its window *backwards* and computes its bucket boundaries from the
calendar, sharing no helper with the pipeline it checks — Story 7.1's review
finding, applied to a story whose whole subject is a cut.

The gate is Story 7.1's, reused rather than re-declared, so the assertion here is
that the *same* allowlist decides this section: a second spelling of one
allowlist is how a future `UserRole` gets admitted by one of them.

**Scope is asserted at the service level**, with a hand-built `CallerContext`,
for the reason 7.1 recorded rather than papered over: there is exactly one
analyst persona in the seed and they are `scope_all`, so no HTTP request in this
codebase can demonstrate a *narrowed* analyst. Seeding a scoped one would move
persona counts in Stories 1.3, 1.4 and 5.1 and in the e2e login fixture. The HTTP
layer still carries "no query parameter can widen the scope".
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, AuditEvent
from data.models.enums import Disability, Gender, RecoveryWindow, UserRole
from data.repositories import claims as claim_repo
from data.repositories.identity import employer_ids_for
from rules import parameters as rule_parameters
from rules.parameters import (
    DerivationThresholds,
    RuleParameterError,
    TrendPeriods,
    thresholds_for,
    trend_periods_for,
)
from services.worklist import fraud as fraud_service
from services.worklist import sla
from services.worklist import trends as trend_service
from services.worklist.drill_through import FILTER_KEYS, WIRE_KEYS
from services.worklist.segmentation import Segmentation
from services.worklist.trends import (
    PortfolioTrends,
    TrendAnchor,
    TrendClaim,
    TrendCohort,
    TrendGrain,
    TrendMetric,
    TrendRangeInvalid,
    TrendRangeTooWide,
    TrendSeries,
    portfolio_trends,
    trends_of,
    window_for,
)
from tests import seed_fixture
from tests.conftest import requires_db

TRENDS = "/dashboard/trends"
SUMMARY = "/dashboard/summary"
CHARTS = "/dashboard/charts"
DRILL = "/dashboard/claims"

ANALYST = ("David Bline", "analyst")
SUPERVISOR = ("David Bline", "supervisor")
SCOPED_SUPERVISOR = ("Jennifer Park", "supervisor")
HANDLER = ("Kaya Johnson", "handler")

SETTINGS = Settings(database_url="postgresql://unused", env="e2e")  # type: ignore[arg-type]

#: The day the pure half's clock is fixed at. A Friday in the middle of a month,
#: so a default month window ends in a bucket that is neither its first nor its
#: last day — a clock on the 1st would let an off-by-one bucket walk pass.
AS_OF = date(2026, 8, 21)

#: The day a synthetic claim is filed and injured on unless a case says
#: otherwise: inside `AS_OF`'s own bucket at every grain, so a claim that says
#: nothing about dates lands in the newest bucket of any default window.
QUIET_DAY = date(2026, 8, 19)

#: The three window parameters, restated — never imported from the document.
#:
#: `seed_fixture` restates them too and is the oracle every DB-backed assertion
#: goes through; these exist for the *pure* half, where a window has to be built
#: at a size that sits deliberately either side of a cap. Writing
#: `max_buckets=24` with no name beside it would make the case unreadable the
#: first time somebody asked why 24.
DEFAULT_BUCKETS = 12
MAX_BUCKETS = 24
LOW_CONFIDENCE_CLAIM_MAX = 3

#: The severity band edges the cohort split uses, restated for the same reason
#: and separately from each other: `risk_high_min` and `risk_med_min` are one
#: three-band scale, and a case that moves one has to leave the other where it
#: is or it is testing two changes at once.
RISK_HIGH_MIN = 65
RISK_MED_MIN = 35

#: Story 7.3's three age edges, required by `DerivationThresholds` and read by no
#: fold in this file. Restated separately although two of them coincide with the
#: severity edges above — this file's standing rule, and the reason a case that
#: moved one is testing one change rather than two.
AGE_YOUNGER_MIN = 35
AGE_OLDER_MIN = 45
AGE_OLDEST_MIN = 55


# --- the pure half: synthetic caseloads ----------------------------------


def trend_claim(
    *,
    froi_date: date = QUIET_DAY,
    doi: date = QUIET_DAY,
    severity: int = 10,
    disability: Disability = Disability.temporary,
    sector: str = "Aerospace",
    paid_cents: int = 0,
    settled: bool = False,
    settle_duration: int | None = None,
    fully_recovered: bool = False,
    injury_type: str = "Fracture",
    state: str = "WA",
    employer_id: int = 1,
    region: str = "Midwest",
    icd: str = "S61.219A",
    age: int = 30,
    gender: Gender = Gender.male,
) -> TrendClaim:
    """One projection row, with every field this story does not vary defaulted.

    Keyword-only, so each case below reads as the *delta* from a claim that is
    unremarkable in every other way — `test_fraud_analytics.py`'s discipline for
    a fold whose subject is a boundary.

    Every default is chosen to be *quiet* against the five metrics: both dates in
    `AS_OF`'s own bucket, a low severity, nothing paid, not settled and not
    recovered. So a claim that names nothing contributes to `volume` and
    `avg_days_open` and to nothing else, and a case that wants a settlement mean
    has to say so.

    `settle_duration` is spelled differently from the column it fills because the
    SLA aggregation owns that column's name and its meaning (AD-2): what this
    factory builds is an `SlaSample`, which is the only shape a caller of
    `sla.strip_of` is ever supposed to hold.

    Story 7.3's seven are defaulted quiet too and none of them is read by a fold
    in this file: they exist because `TrendClaim` now satisfies
    `segmentation.SegmentedClaim` structurally, and the segmentation cases live in
    `test_segmentation.py`.
    """
    return TrendClaim(
        froi_date=froi_date,
        doi=doi,
        severity_score=severity,
        disability=disability,
        sector=sector,
        paid_indemnity=paid_cents,
        paid_medical=0,
        paid_expense=0,
        sample=sla.SlaSample(
            pick_days=None,
            approve_days=None,
            settlement_days=settle_duration,
            is_settled=settled,
            fully_recovered=fully_recovered,
        ),
        injury_type=injury_type,
        state=state,
        employer_id=employer_id,
        region=region,
        icd=icd,
        age=age,
        gender=gender,
    )


def _periods(
    default_buckets: int = DEFAULT_BUCKETS,
    max_buckets: int = MAX_BUCKETS,
    low_confidence_claim_max: int = LOW_CONFIDENCE_CLAIM_MAX,
) -> TrendPeriods:
    """A `TrendPeriods` carrying this story's three numbers.

    Built through the real block so `__post_init__` runs — a case that wanted a
    default above its cap would be refused here rather than silently folded.
    """
    return TrendPeriods(
        version=1,
        default_buckets=default_buckets,
        max_buckets=max_buckets,
        low_confidence_claim_max=low_confidence_claim_max,
    )


def _thresholds(**changes: int) -> DerivationThresholds:
    """A `DerivationThresholds` carrying this story's two band edges.

    Hand-written rather than loaded, so the pure half needs no database and so a
    case can move a band edge without publishing a rule document.
    """
    values: dict[str, Any] = {
        "version": 7,
        "risk_high_min": RISK_HIGH_MIN,
        "risk_med_min": RISK_MED_MIN,
        "siu_fraud_score_min": 60,
        "rtw_blocked_hash_modulus": 5,
        "payment_due_hash_modulus": 3,
        "treatment_early_max_ratio": 0.3,
        "treatment_active_max_ratio": 0.7,
        "recovery_year_expected_days": 180,
        "recovery_default_expected_days": 42,
        "path_minor_severity_max": 35,
        "path_minor_recovery_windows": frozenset({RecoveryWindow.weeks_0_2}),
        "path_fatality_severity_min": 100,
        "ptd_severity_threshold": 85,
        "fraud_flag_score_min": 55,
        "fraud_band_high_min": 55,
        "fraud_band_med_min": 35,
        # Story 7.3's three. Required by the block and read by no fold in this
        # file — the segmentation cases live in `test_segmentation.py`.
        "age_younger_min": AGE_YOUNGER_MIN,
        "age_older_min": AGE_OLDER_MIN,
        "age_oldest_min": AGE_OLDEST_MIN,
    }
    values.update(changes)
    return DerivationThresholds(**values)


def fold(
    caseload: Sequence[TrendClaim],
    *,
    grain: TrendGrain = TrendGrain.month,
    anchor: TrendAnchor = TrendAnchor.fnol,
    cohort: TrendCohort = TrendCohort.none,
    from_date: date | None = None,
    to_date: date | None = None,
    periods: TrendPeriods | None = None,
    thresholds: DerivationThresholds | None = None,
    as_of: date = AS_OF,
    settings: Settings = SETTINGS,
) -> PortfolioTrends:
    """The window and the fold, composed exactly as `portfolio_trends` composes them.

    One helper rather than eight lines per case, and it deliberately calls the
    two *public* functions in the order the async wrapper does rather than
    reproducing their bodies — the point is to test the fold, not a second
    arrangement of it.

    `settings` is a parameter for one reason and it is the SLA targets: they are
    deployment configuration rather than a rule document, so a case that wants to
    see the two published reference lines move has nowhere else to move them
    from.
    """
    block = periods or _periods()
    return trends_of(
        caseload,
        window_for(grain, block, as_of, from_date, to_date),
        anchor,
        cohort,
        trend_service._Computers.of(thresholds or _thresholds()),
        sla.targets_for(settings),
        block,
        as_of,
    )


def series_of(
    trends: PortfolioTrends, metric: TrendMetric, cohort_key: str | None = None
) -> TrendSeries:
    """One published series, keyed by metric and cohort — the lookup, once."""
    for series in trends.series:
        if series.metric is metric and series.cohort_key == cohort_key:
            return series
    raise AssertionError(f"no {metric} series for cohort {cohort_key!r}")


def values_of(
    trends: PortfolioTrends, metric: TrendMetric, cohort_key: str | None = None
) -> list[int | None]:
    """One series' points, as a bare list of values keyed by metric and cohort."""
    return [point.value for point in series_of(trends, metric, cohort_key).points]


def counts_of(
    trends: PortfolioTrends, metric: TrendMetric, cohort_key: str | None = None
) -> list[int]:
    """One series' points, as the claim counts the values were folded from.

    Its own accessor beside `values_of` rather than a tuple out of one, because
    the assertion this story's review turned on is that the two are *different
    lists* for two of the five metrics — a helper returning them together would
    have made the pairing look like a fact about the payload rather than the
    thing being checked.
    """
    return [point.claim_count for point in series_of(trends, metric, cohort_key).points]


# --- the window (bucket boundaries) --------------------------------------


def test_the_default_window_ends_in_the_bucket_today_falls_in() -> None:
    """The window is anchored on the clock, never on the data.

    Anchoring on the latest claim anybody happened to file would make the axis
    move when a claim is entered, and would hide the emptiest and most
    interesting thing a trend chart can say — that nothing has arrived for two
    months.
    """
    window = window_for(TrendGrain.month, _periods(), AS_OF)

    assert len(window.buckets) == DEFAULT_BUCKETS
    assert window.buckets[-1].key == "2026-08"
    assert window.buckets[-1].start == date(2026, 8, 1)
    assert window.buckets[-1].end == date(2026, 8, 31)
    # Twelve months back, inclusive of both ends — so the window crosses a year
    # boundary, which no seeded request does.
    assert window.buckets[0].key == "2025-09"
    assert window.buckets[0].start == date(2025, 9, 1)


def test_a_month_bucket_ends_on_the_last_day_of_its_own_month() -> None:
    """February, and a leap February, without a month-length table.

    The end is computed as "the day before the next bucket starts", so the two
    cases a hand-written table gets wrong are the two the arithmetic cannot.
    """
    window = window_for(TrendGrain.month, _periods(default_buckets=3), date(2024, 3, 15))

    assert [(bucket.key, bucket.end) for bucket in window.buckets] == [
        ("2024-01", date(2024, 1, 31)),
        ("2024-02", date(2024, 2, 29)),
        ("2024-03", date(2024, 3, 31)),
    ]


def test_a_week_window_walks_iso_mondays() -> None:
    """A week starts on Monday and its key is the ISO year-week.

    `AS_OF` is a Friday, so the newest bucket has to start four days earlier — a
    grain that keyed on the request date would put the same claims in two
    different weeks depending on when the chart was opened.
    """
    window = window_for(TrendGrain.week, _periods(default_buckets=3), AS_OF)

    assert [(bucket.key, bucket.start, bucket.end) for bucket in window.buckets] == [
        ("2026-W32", date(2026, 8, 3), date(2026, 8, 9)),
        ("2026-W33", date(2026, 8, 10), date(2026, 8, 16)),
        ("2026-W34", date(2026, 8, 17), date(2026, 8, 23)),
    ]


def test_a_week_window_crosses_an_iso_year_without_losing_a_week() -> None:
    """The 53-week year, which the seed cannot reach and a naive walk mishandles.

    2026 ends on a Thursday, so the week of 28 December belongs to ISO 2026 and
    the week of 4 January to ISO 2027 — a key built from the calendar year rather
    than the ISO year would give two different weeks the same name.
    """
    window = window_for(TrendGrain.week, _periods(default_buckets=3), date(2027, 1, 6))

    assert [bucket.key for bucket in window.buckets] == ["2026-W52", "2026-W53", "2027-W01"]
    assert window.buckets[1].start == date(2026, 12, 28)


def test_a_quarter_window_walks_calendar_quarters() -> None:
    """Calendar quarters, not fiscal ones.

    A fiscal year is an employer's property and this fold spans ten of them, so a
    Q1 that meant something different per employer would be one axis describing
    several.
    """
    window = window_for(TrendGrain.quarter, _periods(default_buckets=4), AS_OF)

    assert [(bucket.key, bucket.start, bucket.end) for bucket in window.buckets] == [
        ("2025-Q4", date(2025, 10, 1), date(2025, 12, 31)),
        ("2026-Q1", date(2026, 1, 1), date(2026, 3, 31)),
        ("2026-Q2", date(2026, 4, 1), date(2026, 6, 30)),
        ("2026-Q3", date(2026, 7, 1), date(2026, 9, 30)),
    ]


def test_a_bucket_label_names_the_period_and_never_a_range() -> None:
    """The labels, pinned — they are server-side copy and a client renders them.

    Asserted because they are the one place this server writes words. A change
    here is a change to what a chart's x-axis says, and it should require editing
    a test that says so.
    """
    months = window_for(TrendGrain.month, _periods(default_buckets=2), AS_OF)
    quarters = window_for(TrendGrain.quarter, _periods(default_buckets=1), AS_OF)
    weeks = window_for(TrendGrain.week, _periods(default_buckets=1), AS_OF)

    assert [bucket.label for bucket in months.buckets] == ["Jul 2026", "Aug 2026"]
    assert [bucket.label for bucket in quarters.buckets] == ["Q3 2026"]
    assert [bucket.label for bucket in weeks.buckets] == ["Wk 34 2026"]


def test_an_explicit_range_covers_both_end_buckets_whole() -> None:
    """Both bounds are inclusive and each pulls in its whole bucket.

    That is what makes a bucket's drill return exactly the claims its point was
    folded from: `filter[fnolFrom]`/`filter[fnolTo]` are filled from
    `bucket_from`/`bucket_to`, and a half-open window at either end would be one
    day out.
    """
    window = window_for(TrendGrain.month, _periods(), AS_OF, date(2026, 3, 17), date(2026, 5, 2))

    assert [bucket.key for bucket in window.buckets] == ["2026-03", "2026-04", "2026-05"]
    assert window.buckets[0].start == date(2026, 3, 1)
    assert window.buckets[-1].end == date(2026, 5, 31)


def test_an_inverted_range_is_refused_even_inside_one_bucket() -> None:
    """The check reads the caller's raw dates, not their buckets.

    `from=2026-03-31&to=2026-03-01` is a backwards range even though both days
    land in one March bucket, and answering it with a cheerful single-bucket
    series would be the service deciding the caller meant something else.
    """
    with pytest.raises(TrendRangeInvalid):
        window_for(TrendGrain.month, _periods(), AS_OF, date(2026, 3, 31), date(2026, 3, 1))
    with pytest.raises(TrendRangeInvalid):
        window_for(TrendGrain.month, _periods(), AS_OF, date(2026, 6, 1), date(2026, 3, 1))


def test_a_from_after_the_default_end_is_refused() -> None:
    """The half-specified case: a `from` in the future with `to` left to default.

    `to` fills from the clock, so this is an inverted range the caller did not
    spell as one — and it has to be refused for the same reason, rather than
    served as an empty window whose axis runs backwards.
    """
    with pytest.raises(TrendRangeInvalid):
        window_for(TrendGrain.month, _periods(), AS_OF, date(2027, 1, 1), None)


def test_a_range_that_runs_off_the_calendar_is_refused_and_never_raises() -> None:
    """The three windows `date` itself cannot express, at both ends and on a week.

    `to=9999-12-31` needs the day before the year ten thousand to close its last
    bucket, a week window at the same edge overflows by two days, and
    `to=0001-06-15` needs eleven months before year one to open its first. Each
    is a well-formed question with no answer, and each used to leave `window_for`
    as a bare `ValueError`/`OverflowError` — a 500 on a request the caller could
    have fixed by editing a date. They are the *invalid range* refusal rather
    than a third problem type because the caller's move is the same one.
    """
    for grain, from_date, to_date in (
        (TrendGrain.month, None, date(9999, 12, 31)),
        (TrendGrain.week, None, date(9999, 12, 31)),
        (TrendGrain.quarter, None, date(9999, 12, 31)),
        (TrendGrain.month, None, date(1, 6, 15)),
    ):
        with pytest.raises(TrendRangeInvalid):
            window_for(grain, _periods(), AS_OF, from_date, to_date)


def test_a_range_wider_than_the_cap_is_refused_naming_the_cap() -> None:
    """The refusal has to say where the edge is.

    The caller's only useful next move is a narrower window or a wider grain, and
    they cannot choose either without the number. It is also published on every
    successful payload as `maxBuckets`, so a control can refuse locally.
    """
    with pytest.raises(TrendRangeTooWide, match=str(MAX_BUCKETS)):
        window_for(TrendGrain.month, _periods(), AS_OF, date(2024, 1, 1), date(2026, 12, 31))


def test_a_range_exactly_at_the_cap_is_served() -> None:
    """The boundary is `>`, not `>=`: the cap is a width the caller may ask for."""
    window = window_for(TrendGrain.month, _periods(), AS_OF, date(2025, 1, 1), date(2026, 12, 31))

    assert len(window.buckets) == MAX_BUCKETS


def test_the_cap_counts_buckets_and_so_governs_every_grain() -> None:
    """One number over three grains, which is why it is a bucket count.

    The same twenty-four months that fit exactly are far too many weeks, and the
    refusal is the same refusal — three per-grain caps would be three parameters
    that agree today and drift the first time one is retuned.
    """
    span = (date(2025, 1, 1), date(2026, 12, 31))
    assert len(window_for(TrendGrain.quarter, _periods(), AS_OF, *span).buckets) == 8
    with pytest.raises(TrendRangeTooWide):
        window_for(TrendGrain.week, _periods(), AS_OF, *span)


# --- null versus zero (AC 3) ---------------------------------------------


def test_counts_zero_fill_an_empty_bucket_and_means_go_null() -> None:
    """The story's central prohibition, metric by metric on one caseload.

    One claim in the newest bucket and nothing anywhere else. The two counted
    series say zero for the eleven empty months, because a month that produced no
    claims saw nothing and paid nothing and both are facts a chart may draw. The
    three others say `null`, because "average days open: 0" and "RTW rate: 0%"
    are false statements about a month nobody filed a claim in.
    """
    trends = fold([trend_claim(froi_date=date(2026, 8, 4), paid_cents=1_000)])

    assert values_of(trends, TrendMetric.volume) == [0] * 11 + [1]
    assert values_of(trends, TrendMetric.paid_cents) == [0] * 11 + [1_000]
    assert values_of(trends, TrendMetric.avg_days_open) == [None] * 11 + [17]
    assert values_of(trends, TrendMetric.avg_settlement_days) == [None] * 12
    assert values_of(trends, TrendMetric.rtw_rate_bp) == [None] * 12


def test_a_bucket_with_claims_but_none_settled_reports_null_for_both_sla_series() -> None:
    """A *narrower* null than the empty-bucket one, and the distinction matters.

    A bucket can hold twenty claims and still have no settled one, so the two SLA
    series are `null` there while volume, paid and days-open are all numbers.
    That is `sla.strip_of`'s `no_data` arriving unchanged rather than a second
    emptiness rule invented here.
    """
    trends = fold(
        [
            trend_claim(froi_date=date(2026, 8, 4), paid_cents=500),
            trend_claim(froi_date=date(2026, 8, 5), paid_cents=500),
        ]
    )

    assert values_of(trends, TrendMetric.volume)[-1] == 2
    assert values_of(trends, TrendMetric.paid_cents)[-1] == 1_000
    assert values_of(trends, TrendMetric.avg_days_open)[-1] is not None
    assert values_of(trends, TrendMetric.avg_settlement_days)[-1] is None
    assert values_of(trends, TrendMetric.rtw_rate_bp)[-1] is None


def test_a_settled_claim_without_a_recorded_duration_still_rates_but_does_not_average() -> None:
    """`sla.strip_of`'s two denominators, arriving intact one bucket at a time.

    The settlement mean is over settled claims that carry a duration; the RTW
    rate is over *all* settled claims, so a settlement with no recorded duration
    still has a return-to-work outcome and still counts in the rate. Sharing one
    denominator would drop it, and this is the bucket-level restatement of the
    property `test_sla_aggregation.py` asserts portfolio-wide.

    **Three populations in one bucket, and the point says which one it means.**
    Six claims arrive, two of them settle and one of those carries a duration —
    so the two values differ *and* the two counts behind them differ, from each
    other and from the bucket. Asserting only the values leaves a payload that
    publishes six claims behind a mean over one, which is the reading a reader
    weighs the number by; asserting the counts as well is what makes the
    denominator a published fact rather than an implied one.

    The bucket is deliberately wider than the low-confidence ceiling, so the
    verdict divides the same way the counts do: the volume point is not thin and
    both SLA points are, on the same six claims.
    """
    caseload = [
        trend_claim(froi_date=date(2026, 8, 4), settled=True, fully_recovered=True),
        trend_claim(
            froi_date=date(2026, 8, 5),
            settled=True,
            settle_duration=40,
            fully_recovered=False,
        ),
        *[trend_claim(froi_date=date(2026, 8, 6))] * (LOW_CONFIDENCE_CLAIM_MAX + 1),
    ]
    trends = fold(caseload)

    # One duration recorded, so the mean is that duration…
    assert values_of(trends, TrendMetric.avg_settlement_days)[-1] == 40
    # …and the rate is one of *two* settled claims, not one of one.
    assert values_of(trends, TrendMetric.rtw_rate_bp)[-1] == 5_000

    # The three counts, which are three different answers about one bucket.
    assert counts_of(trends, TrendMetric.volume)[-1] == len(caseload)
    assert counts_of(trends, TrendMetric.avg_days_open)[-1] == len(caseload)
    assert counts_of(trends, TrendMetric.paid_cents)[-1] == len(caseload)
    assert counts_of(trends, TrendMetric.avg_settlement_days)[-1] == 1
    assert counts_of(trends, TrendMetric.rtw_rate_bp)[-1] == 2

    # …and the verdict follows the count rather than the bucket: a mean over one
    # claim is thin evidence in a month that saw six.
    marks = {metric: series_of(trends, metric).points[-1].low_confidence for metric in TrendMetric}
    assert marks == {
        TrendMetric.volume: False,
        TrendMetric.avg_days_open: False,
        TrendMetric.avg_settlement_days: True,
        TrendMetric.rtw_rate_bp: True,
        TrendMetric.paid_cents: False,
    }


def test_a_series_total_counts_what_fed_the_metric_and_so_empties_per_card() -> None:
    """The emptiness rule, one population down — and the case it was wrong on.

    A window full of claims none of which have settled has plenty to say about
    volume, ages and money and *nothing* to say about settlement or return to
    work. A `series_total` counting the bucket rather than the metric answers
    "twelve" on all five, so the two SLA cards draw an empty plot with axes, a
    target line and no sentence — a chart that looks like an answer and is not.

    Asserted as the whole mapping rather than two figures, so a fix that made the
    settlement total honest by making the volume total wrong fails here too.
    """
    trends = fold([trend_claim(froi_date=date(2026, 8, 4), paid_cents=100)] * 12)

    totals = {series.metric: series.series_total for series in trends.series}
    assert totals == {
        TrendMetric.volume: 12,
        TrendMetric.avg_days_open: 12,
        TrendMetric.avg_settlement_days: 0,
        TrendMetric.rtw_rate_bp: 0,
        TrendMetric.paid_cents: 12,
    }
    # …and the claims are genuinely there, so this is "nothing settled" rather
    # than "nothing happened".
    assert trends.claims_in_window == 12
    assert values_of(trends, TrendMetric.volume)[-1] == 12


def test_a_cohorts_series_total_is_that_cohorts_own_settlements() -> None:
    """The same rule under a split, where the two populations diverge per line.

    One cohort settles and the other does not, on a payload whose volume series
    are the same size. A total taken over the bucket would make both settlement
    lines look equally well-evidenced, and the empty one would draw a line
    through the periods it cannot describe.
    """
    trends = fold(
        [
            trend_claim(
                froi_date=date(2026, 8, 4),
                disability=Disability.permanent,
                settled=True,
                settle_duration=50,
            ),
            trend_claim(froi_date=date(2026, 8, 5), disability=Disability.temporary),
        ],
        cohort=TrendCohort.disability,
    )

    assert series_of(trends, TrendMetric.volume, "permanent").series_total == 1
    assert series_of(trends, TrendMetric.volume, "temporary").series_total == 1
    assert series_of(trends, TrendMetric.avg_settlement_days, "permanent").series_total == 1
    assert series_of(trends, TrendMetric.avg_settlement_days, "temporary").series_total == 0
    assert values_of(trends, TrendMetric.avg_settlement_days, "temporary") == [None] * 12


def test_the_rate_is_the_strips_own_figure_on_the_basis_point_scale() -> None:
    """AD-2 in its most tempting place: a rate, per bucket, that must not be re-derived.

    Asserted against `sla.strip_of` called directly on the same samples rather
    than against an arithmetic restatement — the claim under test is that this is
    *that* function's answer. The published value is a multiple of a hundred
    because the strip decides the RTW tile at whole-percent precision, which is
    the honest consequence of not re-averaging rather than a precision this
    module chose.
    """
    caseload = [
        trend_claim(froi_date=date(2026, 8, 4), settled=True, fully_recovered=True),
        trend_claim(froi_date=date(2026, 8, 5), settled=True, fully_recovered=False),
        trend_claim(froi_date=date(2026, 8, 6), settled=True, fully_recovered=False),
    ]
    strip = sla.strip_of([claim.sample for claim in caseload], sla.targets_for(SETTINGS))
    expected = strip[sla.SlaMetricKey.rtw_rate].value
    assert expected is not None

    trends = fold(caseload)

    assert values_of(trends, TrendMetric.rtw_rate_bp)[-1] == int(expected) * 100
    assert values_of(trends, TrendMetric.rtw_rate_bp)[-1] == 3_300


def test_the_days_open_mean_rounds_half_up_and_not_to_even() -> None:
    """Python's `round` is banker's rounding, and a chart that used it would
    round 10.5 down and 11.5 up on the same axis.

    Two claims filed one and two days before the clock average 1.5 days, which is
    16 and 17 days for the pair below — the mean lands exactly on a half, and
    half-up is the only reading a reader can check.
    """
    trends = fold(
        [
            trend_claim(froi_date=date(2026, 8, 4)),
            trend_claim(froi_date=date(2026, 8, 5)),
        ]
    )

    # 17 and 16 days open; the true mean is 16.5.
    assert values_of(trends, TrendMetric.avg_days_open)[-1] == 17


def test_value_total_is_a_number_for_the_two_summable_metrics_and_null_for_the_rest() -> None:
    """A summed mean is not a number, and `null` says so.

    Zero would be a lie of exactly the kind the point-level rule forbids, one
    level up: "these three do not have a window total" is a different statement
    from "their window total is nothing".
    """
    trends = fold(
        [
            trend_claim(froi_date=date(2026, 7, 4), paid_cents=700),
            trend_claim(froi_date=date(2026, 8, 4), paid_cents=300),
        ]
    )
    totals = {series.metric: series.value_total for series in trends.series}

    assert totals[TrendMetric.volume] == 2
    assert totals[TrendMetric.paid_cents] == 1_000
    assert totals[TrendMetric.avg_days_open] is None
    assert totals[TrendMetric.avg_settlement_days] is None
    assert totals[TrendMetric.rtw_rate_bp] is None


def test_emptiness_is_decided_on_a_total_and_never_on_the_point_count() -> None:
    """The bug Story 7.1 shipped once, made impossible here rather than avoided.

    A zero-filled series has as many points as any other, so `len(points) == 0`
    can never fire and an empty state keyed on it would never render. Every
    series therefore carries `series_total`, and it is zero exactly when nothing
    is behind it.
    """
    empty = fold([])

    assert empty.bucket_count == DEFAULT_BUCKETS
    assert all(len(series.points) == DEFAULT_BUCKETS for series in empty.series)
    assert all(series.series_total == 0 for series in empty.series)
    assert empty.claims_in_scope == 0
    assert empty.claims_in_window == 0
    # …and the counted series still say zero rather than null, because a book
    # with no claims really did see none.
    assert values_of(empty, TrendMetric.volume) == [0] * DEFAULT_BUCKETS
    assert values_of(empty, TrendMetric.avg_days_open) == [None] * DEFAULT_BUCKETS


def test_no_data_buckets_counts_the_gaps_the_footnote_quotes() -> None:
    """Published rather than left to a client counting nulls (AD-1)."""
    trends = fold([trend_claim(froi_date=date(2026, 8, 4))])
    gaps = {series.metric: series.no_data_buckets for series in trends.series}

    assert gaps[TrendMetric.volume] == 0
    assert gaps[TrendMetric.avg_days_open] == DEFAULT_BUCKETS - 1
    assert gaps[TrendMetric.avg_settlement_days] == DEFAULT_BUCKETS


# --- the window is a cut, and the cut is visible -------------------------


def test_a_claim_outside_the_window_is_dropped_and_never_bucketed_at_the_edge() -> None:
    """A claim from before the window is not a claim from its first month.

    Clamping it to the edge would put a spike on the oldest bucket that nothing
    on screen could explain; `claims_in_window` beside `claims_in_scope` is what
    stops the exclusion being silent.
    """
    trends = fold(
        [
            trend_claim(froi_date=date(2020, 1, 1)),
            trend_claim(froi_date=date(2026, 8, 4)),
        ]
    )

    assert trends.claims_in_scope == 2
    assert trends.claims_in_window == 1
    assert values_of(trends, TrendMetric.volume) == [0] * 11 + [1]


def test_the_anchor_decides_which_bucket_a_claim_lands_in() -> None:
    """One claim, two anchors, two different months.

    A falling FNOL volume with a flat DOI volume is a reporting lag rather than
    fewer injuries, which is the whole reason both anchors exist — and a fold
    that read one column under a caption naming the other would answer a
    different question convincingly.
    """
    late_report = [trend_claim(doi=date(2026, 6, 20), froi_date=date(2026, 8, 4))]

    by_fnol = fold(late_report, anchor=TrendAnchor.fnol)
    by_doi = fold(late_report, anchor=TrendAnchor.doi)

    assert values_of(by_fnol, TrendMetric.volume) == [0] * 11 + [1]
    assert values_of(by_doi, TrendMetric.volume) == [0] * 9 + [1, 0, 0]
    assert by_fnol.anchor is TrendAnchor.fnol
    assert by_doi.anchor is TrendAnchor.doi


def test_the_boundary_day_belongs_to_its_own_bucket_at_both_ends() -> None:
    """Claims on the first and last day of a month, and neither slips.

    Bucket boundaries are where an off-by-one lives, and the seeded portfolio
    happens to contain no claim on the first of a month with the previous month
    also in scope — so this is the only place the edge is tested.
    """
    trends = fold(
        [
            trend_claim(froi_date=date(2026, 7, 1)),
            trend_claim(froi_date=date(2026, 7, 31)),
            trend_claim(froi_date=date(2026, 8, 1)),
        ]
    )

    assert values_of(trends, TrendMetric.volume) == [0] * 10 + [2, 1]


# --- cohorts (AC 2) -------------------------------------------------------


def test_a_cohort_split_partitions_the_population_rather_than_narrowing_it() -> None:
    """Every cohort series over a metric sums back to the unsplit one.

    That is the difference between a *split* and a *filter*, and it is the
    property Story 7.3's segmentation will not have. A fold that dropped a claim
    with an unexpected cohort value — or counted one twice — would break here and
    nowhere else.
    """
    caseload = [
        trend_claim(froi_date=date(2026, 8, 4), severity=RISK_HIGH_MIN),
        trend_claim(froi_date=date(2026, 8, 5), severity=RISK_MED_MIN),
        trend_claim(froi_date=date(2026, 7, 5), severity=0),
        trend_claim(froi_date=date(2026, 7, 6), severity=RISK_HIGH_MIN),
    ]
    unsplit = fold(caseload)
    split = fold(caseload, cohort=TrendCohort.severity_band)

    per_bucket = [
        sum(
            series.points[index].value or 0
            for series in split.series
            if series.metric is TrendMetric.volume
        )
        for index in range(split.bucket_count)
    ]
    assert per_bucket == values_of(unsplit, TrendMetric.volume)
    assert split.claims_in_window == unsplit.claims_in_window


def test_each_cohort_dimension_reaches_its_own_owner() -> None:
    """Three dimensions, three sources, and the severity one is the registered band.

    A cohort here and a slice on 5.3's severity donut have to be one band with
    two callers, not two readings of one score — which is why the severity split
    goes through `derivations.risk` and is asserted at the edge rather than in
    the middle.
    """
    caseload = [
        trend_claim(
            froi_date=date(2026, 8, 4),
            severity=RISK_HIGH_MIN,
            disability=Disability.permanent,
            sector="Automotive",
        ),
        trend_claim(
            froi_date=date(2026, 8, 5),
            severity=RISK_HIGH_MIN - 1,
            disability=Disability.temporary,
            sector="Aerospace",
        ),
    ]

    def keys(cohort: TrendCohort) -> list[str]:
        return sorted(
            {
                series.cohort_key or ""
                for series in fold(caseload, cohort=cohort).series
                if series.metric is TrendMetric.volume
            }
        )

    assert keys(TrendCohort.severity_band) == ["high", "med"]
    assert keys(TrendCohort.disability) == ["permanent", "temporary"]
    assert keys(TrendCohort.sector) == ["Aerospace", "Automotive"]
    # `""` is this helper's spelling of the unsplit series' `None` — sorting a
    # set holding both a string and a `None` is not an order at all.
    assert keys(TrendCohort.none) == [""]


def test_the_severity_cohort_follows_the_rule_document_and_not_a_constant() -> None:
    """AD-8, end to end on the pure half: move the edge, move the cohort.

    A claim scoring one below the seeded high edge is `med`; retuned so the edge
    sits at its score, the same claim's series is `high`. Nothing about the fold
    changes, which is what "the band is the document's" has to mean.
    """
    claim = trend_claim(froi_date=date(2026, 8, 4), severity=RISK_HIGH_MIN - 1)

    before = fold([claim], cohort=TrendCohort.severity_band)
    after = fold(
        [claim],
        cohort=TrendCohort.severity_band,
        thresholds=_thresholds(risk_high_min=RISK_HIGH_MIN - 1),
    )

    assert {series.cohort_key for series in before.series} == {"med"}
    assert {series.cohort_key for series in after.series} == {"high"}
    assert before.high_risk_severity_min == RISK_HIGH_MIN
    assert after.high_risk_severity_min == RISK_HIGH_MIN - 1


def test_a_palette_slot_follows_the_wire_key_and_never_the_ranking() -> None:
    """The flaw the ordinal exists to fix, asserted by moving the ranking.

    The categorical palette's own docstring records that a hue there means *rank
    position*. So the slot is assigned by sorting cohort values on their wire
    key: two caseloads where the ranking is reversed hand the same cohort the
    same slot, and therefore the same colour across all five charts and across a
    refetch.
    """
    heavy_aerospace = [trend_claim(froi_date=date(2026, 8, 4), sector="Aerospace")] * 5 + [
        trend_claim(froi_date=date(2026, 8, 5), sector="Zinc")
    ]
    heavy_zinc = [trend_claim(froi_date=date(2026, 8, 4), sector="Aerospace")] + [
        trend_claim(froi_date=date(2026, 8, 5), sector="Zinc")
    ] * 5

    def slots(caseload: Sequence[TrendClaim]) -> dict[str | None, int | None]:
        return {
            series.cohort_key: series.palette_slot
            for series in fold(caseload, cohort=TrendCohort.sector).series
            if series.metric is TrendMetric.volume
        }

    assert slots(heavy_aerospace) == {"Aerospace": 0, "Zinc": 1}
    assert slots(heavy_zinc) == {"Aerospace": 0, "Zinc": 1}


def test_a_cohort_holds_one_slot_across_all_five_charts() -> None:
    """The colour is the cohort's, not the chart's — AC 2 in one assertion."""
    trends = fold(
        [
            trend_claim(froi_date=date(2026, 8, 4), sector="Aerospace"),
            trend_claim(froi_date=date(2026, 8, 5), sector="Zinc"),
        ],
        cohort=TrendCohort.sector,
    )
    by_key: dict[str | None, set[int | None]] = {}
    for series in trends.series:
        by_key.setdefault(series.cohort_key, set()).add(series.palette_slot)

    assert by_key == {"Aerospace": {0}, "Zinc": {1}}
    assert len(trends.series) == len(TrendMetric) * 2


def test_a_cohort_value_with_no_claim_in_the_window_gets_no_series() -> None:
    """`charts._declared`'s rule, and deliberately not `fraud._banded`'s.

    A cohort with no claims in the window is a line nobody drew, and rendering it
    would put a legend entry on screen describing nothing. A fraud band with no
    claims is the opposite case — a segment of a distribution whose vocabulary is
    a rule's, where "nothing scored high" is the headline.
    """
    trends = fold(
        [
            trend_claim(froi_date=date(2026, 8, 4), severity=0),
            # Outside the window, so its band contributes no series at all.
            trend_claim(froi_date=date(2020, 1, 1), severity=RISK_HIGH_MIN),
        ],
        cohort=TrendCohort.severity_band,
    )

    assert {series.cohort_key for series in trends.series} == {"low"}


def test_an_unsplit_series_carries_no_cohort_fields_at_all() -> None:
    """ "Is this a cohort?" is a field, not an inference from the request."""
    trends = fold([trend_claim()])

    assert len(trends.series) == len(TrendMetric)
    for series in trends.series:
        assert series.cohort_key is None
        assert series.cohort_label is None
        assert series.palette_slot is None


def test_the_five_metrics_are_published_in_declaration_order() -> None:
    """The section's reading order is decided here, so no client sorts (AD-1)."""
    trends = fold([trend_claim()])

    assert [series.metric for series in trends.series] == list(TrendMetric)


# --- low confidence -------------------------------------------------------


def test_low_confidence_marks_a_thin_bucket_and_never_an_empty_one() -> None:
    """`0 < claims <= ceiling`, and the left half is not decoration.

    A bucket with no claims is not low confidence, it is *no* confidence — its
    mean is already `null`, and marking it thin as well would put a warning badge
    on a gap. The two states are different answers and the UI draws them
    differently.
    """
    caseload = [trend_claim(froi_date=date(2026, 8, 4))] * LOW_CONFIDENCE_CLAIM_MAX + [
        trend_claim(froi_date=date(2026, 7, 4))
    ] * (LOW_CONFIDENCE_CLAIM_MAX + 1)
    trends = fold(caseload)
    flags = [
        (point.claim_count, point.low_confidence)
        for series in trends.series
        if series.metric is TrendMetric.volume
        for point in series.points
    ]

    assert flags[-1] == (LOW_CONFIDENCE_CLAIM_MAX, True)
    assert flags[-2] == (LOW_CONFIDENCE_CLAIM_MAX + 1, False)
    assert flags[0] == (0, False)


def test_the_newest_bucket_is_marked_partial_and_the_finished_ones_are_not() -> None:
    """A period in progress, drawn at a finished period's width.

    `AS_OF` is the 21st, so the window's last bucket holds two thirds of an
    August and every figure on it is a part of what the axis implies. The eleven
    behind it are over. Asserted on every series rather than on one, because the
    mark belongs to the *bucket* and a payload that marked it on one metric would
    put a caveat on one card and leave four beside it reading whole.
    """
    trends = fold([trend_claim(froi_date=date(2026, 8, 4))])

    for series in trends.series:
        flags = [point.partial for point in series.points]
        assert flags == [False] * 11 + [True], series.metric


def test_a_bucket_that_ended_before_the_clock_is_never_partial() -> None:
    """The window that stops short of today, where nothing is in progress.

    A caller asking for last year's four quarters gets four complete periods, so
    a card marking the rightmost one partial would be caveating a finished
    quarter — the mark has to read the bucket's own last day and not its position
    in the array. The mirror case is a window asked for *past* today, where every
    unfinished bucket is marked and not only the last.
    """
    finished = fold(
        [], grain=TrendGrain.quarter, from_date=date(2025, 1, 1), to_date=date(2025, 12, 31)
    )
    ahead = fold([], from_date=date(2026, 7, 1), to_date=date(2026, 10, 31))

    assert [point.partial for point in finished.series[0].points] == [False] * 4
    # July is over on the 21st of August; August, September and October are not.
    assert [point.partial for point in ahead.series[0].points] == [False, True, True, True]


def test_the_low_confidence_ceiling_comes_from_the_document() -> None:
    """AD-8 again: retune the ceiling, and a bucket changes its treatment."""
    caseload = [trend_claim(froi_date=date(2026, 8, 4))] * (LOW_CONFIDENCE_CLAIM_MAX + 1)

    def marked(ceiling: int) -> bool:
        trends = fold(caseload, periods=_periods(low_confidence_claim_max=ceiling))
        return trends.series[0].points[-1].low_confidence

    assert marked(LOW_CONFIDENCE_CLAIM_MAX) is False
    assert marked(LOW_CONFIDENCE_CLAIM_MAX + 1) is True


# --- the rules block (AD-8) ----------------------------------------------


def test_a_window_parameter_below_one_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A window of zero buckets is a chart with no x-axis, and it fails silently.

    Refused at load, which is where a rule that cannot be honoured belongs —
    `pageLimit: 0`'s ruling on the queue, over a different count.
    """
    for changes in ({"default_buckets": 0}, {"max_buckets": 0}, {"default_buckets": -1}):
        with pytest.raises(RuleParameterError):
            _periods(**changes)


def test_a_low_confidence_ceiling_of_zero_is_refused_rather_than_switching_it_off() -> None:
    """Zero would not tune the marking, it would delete it.

    The test is `0 < claimCount <= ceiling`, so a zero means no bucket is ever
    marked — and a section that never marks a thin bucket looks exactly like a
    section whose marking is broken. `_status_set` accepts an empty list because
    "no status counts as pending approval" is visible on screen; this one is
    visible only as an absence.
    """
    with pytest.raises(RuleParameterError, match="lowConfidenceClaimMax"):
        _periods(low_confidence_claim_max=0)


def test_a_default_above_the_cap_is_refused_and_equality_is_not() -> None:
    """`>` and deliberately not `>=`, which is the opposite call from a band pair.

    Two band edges that coincide *delete* the middle band — they change the rule
    into a different rule. A default equal to its cap deletes nothing: it says
    "the widest window is also the one you open on", which is a coherent policy.
    What cannot be honoured is a default above the cap, because the section would
    then refuse its own opening request on every load.
    """
    assert _periods(default_buckets=MAX_BUCKETS, max_buckets=MAX_BUCKETS).default_buckets
    with pytest.raises(RuleParameterError, match="defaultBuckets"):
        _periods(default_buckets=MAX_BUCKETS + 1, max_buckets=MAX_BUCKETS)


def test_the_published_window_parameters_are_the_blocks() -> None:
    """The three travel with the figures so a control can refuse locally.

    Read off the block that was passed in rather than compared against a literal,
    for the reason `PortfolioCharts` publishes its band edges off the derivation:
    the published numbers have to be provably the ones the fold used.
    """
    block = _periods(default_buckets=2, max_buckets=9, low_confidence_claim_max=1)
    trends = fold([], periods=block)

    assert trends.default_buckets == block.default_buckets
    assert trends.max_buckets == block.max_buckets
    assert trends.low_confidence_claim_max == block.low_confidence_claim_max
    assert trends.bucket_count == block.default_buckets


def test_the_two_targets_are_the_strips_own_targets_on_the_series_scales() -> None:
    """A reference line has to be the line the tile's verdict used.

    Read off `sla.targets_for` rather than off a literal, and the rate target is
    scaled the way the rate *value* is — a target on a different scale from the
    series it grades is a line nobody can draw.

    **Both conversions are exercised on a target that is not a whole number**,
    which is the difference between checking the conversion and checking a
    fixture. This deployment's targets are 30 and 80, and every arithmetic a
    reviewer might write agrees on those: `int(v) * 100` and `round(v * 100)`
    both answer 8000. They part company at 80.5% — one publishes 8050 basis
    points and the other 8000, which is half a point of return-to-work
    performance drawn as the line a whole series is graded against. A test named
    for the scales has to be able to tell them apart.
    """
    targets = sla.targets_for(SETTINGS)
    trends = fold([])

    assert trends.settle_target_days == int(round(targets[sla.SlaMetricKey.settle].value))
    assert trends.rtw_target_bp == int(round(targets[sla.SlaMetricKey.rtw_rate].value * 100))

    # A deployment whose targets fall between the units the series are published
    # in. Both must survive the trip whole: the rate onto the basis-point scale
    # to the last hundredth, and the day target to the nearest whole day, which
    # is the precision `sla.targets_for` decides the settlement tile at.
    fractional = Settings(
        database_url="postgresql://unused",
        env="e2e",  # type: ignore[arg-type]
        sla_rtw_target_pct=80.5,
        sla_settle_target_days=29.5,
    )
    tuned = fold([], settings=fractional)

    assert tuned.rtw_target_bp == 8_050
    assert tuned.settle_target_days == 30


def test_the_clock_is_published_and_is_the_one_the_ages_were_measured_against() -> None:
    """`avgDaysOpen` counts up to a day and never freezes.

    A chart of claim ages that did not say which day they were measured on would
    be unreadable the moment it was stored or forwarded — which is the wire change
    `deferred-work.md` has wanted since Story 2.1.
    """
    trends = fold([trend_claim(froi_date=date(2026, 8, 4))], as_of=date(2026, 8, 20))

    assert trends.as_of == date(2026, 8, 20)
    assert values_of(trends, TrendMetric.avg_days_open)[-1] == 16


def test_the_rate_scale_is_the_one_the_fraud_rates_publish_on() -> None:
    """The duplication is made safe by a test rather than by a comment.

    `trends.RATE_BASIS_POINTS` restates `fraud.RATE_BASIS_POINTS` rather than
    importing it, `rules.CURSOR_PAGE_CEILING`'s arrangement: importing would make
    this module's arithmetic depend on a constant owned by a surface that ranks
    flagged populations. This is the failure that keeps the two from drifting
    apart without a message naming both — a console publishing two rates on two
    different integer scales would be unreadable and would look correct on each
    screen alone.
    """
    assert trend_service.RATE_BASIS_POINTS == fraud_service.RATE_BASIS_POINTS


# --- the gate (AD-7), without a database ---------------------------------


def test_the_section_reuses_the_analyst_workspace_allowlist() -> None:
    """One allowlist for one workspace, not a second spelling of it.

    Story 7.1 declared `FRAUD_ANALYTICS_ROLES` as a new constant beside
    `benchmarks.PERMITTED_ROLES`; this section is a second *section* of the same
    workspace, so it reuses that list rather than declaring a third. Two
    spellings of one allowlist is how a future `UserRole` gets admitted by one of
    them.
    """
    assert frozenset({UserRole.analyst}) == fraud_service.FRAUD_ANALYTICS_ROLES


@pytest.mark.parametrize("role", sorted(set(UserRole) - {UserRole.analyst}))
async def test_every_role_outside_the_allowlist_is_refused(role: UserRole) -> None:
    """Enumerated from the enum, so a role added later fails here rather than
    inheriting the analyst workspace.

    Parametrising over `set(UserRole) - {analyst}` rather than over a written-out
    list is what makes that true: `UserRole.system` already exists, and a denylist
    spelled `role is UserRole.supervisor` would have admitted it on the day it was
    declared with nothing to notice.
    """
    assert role not in fraud_service.FRAUD_ANALYTICS_ROLES
    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await portfolio_trends(
            None,  # type: ignore[arg-type]
            CallerContext(user_id=1, role=role, employer_ids=frozenset({1})),
            _thresholds(),
            _periods(),
            SETTINGS,
            Segmentation(),
        )


async def test_the_refusal_precedes_the_window_and_the_read() -> None:
    """The gate answers before anything else in the body, including the window.

    A refused caller with a nonsensical range gets 403 rather than 422 — the two
    are decided in that order, and the session is passed as `None` so a read of
    any kind would be an `AttributeError` rather than a silent success.
    """
    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await portfolio_trends(
            None,  # type: ignore[arg-type]
            CallerContext(user_id=1, role=UserRole.supervisor, employer_ids=frozenset({1})),
            _thresholds(),
            _periods(),
            SETTINGS,
            Segmentation(),
            from_date=date(2026, 9, 1),
            to_date=date(2026, 1, 1),
        )


# --- the drill facets this story appends ---------------------------------


def test_the_six_new_facets_are_appended_in_this_order_and_nothing_moved() -> None:
    """`appliedFilters` is the chip row's order, and it is this tuple.

    Appended, never inserted: inserting a date facet beside `stage` would
    silently re-order the chips on every drill-through URL anybody has already
    shared. The head of the tuple is asserted too, because "appended" is a claim
    about both ends.

    **Story 7.3 appended four more, so this story's six are no longer the tail**,
    and the slice moved rather than the assertion weakening: the six still sit
    where they were sent, immediately after Story 7.1's two and immediately
    before the segmentation vocabulary's remainder.
    `test_segmentation.py::test_the_four_new_facets_are_appended_after_this_
    storys_six` asserts the other end.
    """
    assert FILTER_KEYS[:3] == ("stage", "severity_band", "fraud_flagged")
    assert FILTER_KEYS[-10:-4] == (
        "fnol_from",
        "fnol_to",
        "doi_from",
        "doi_to",
        "disability",
        "sector",
    )
    assert [WIRE_KEYS[key] for key in FILTER_KEYS[-10:-4]] == [
        "fnolFrom",
        "fnolTo",
        "doiFrom",
        "doiTo",
        "disability",
        "sector",
    ]


# --- the DB half ---------------------------------------------------------


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


@asynccontextmanager
async def make_client(db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(database_url=db_url, env="e2e"))  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def login_as(client: httpx.AsyncClient, name: str, role: str) -> None:
    personas = (await client.get("/personas")).json()["items"]
    match = [p for p in personas if p["name"] == name and p["role"] == role]
    assert len(match) == 1, f"expected exactly one {name}/{role} persona"
    assert (await client.post("/auth/login", json={"personaId": match[0]["id"]})).status_code == 200


async def context_for(db: AsyncSession, name: str, role: str) -> CallerContext:
    """Build a caller context the way the API dependency does (AD-7).

    `ALL_EMPLOYERS` rather than the assignment set for a `scope_all` persona,
    `test_fraud_analytics.py`'s helper and its reason: David Bline has no rows in
    `user_employer_assignment`, so reading his assignments would hand the
    aggregate an empty book and every cross-surface equality below would hold
    trivially between two empty answers.
    """
    user = (
        await db.scalars(sa.select(AppUser).where(AppUser.name == name, AppUser.role == role))
    ).one()
    return CallerContext(
        user_id=user.id,
        role=user.role,
        employer_ids=ALL_EMPLOYERS if user.scope_all else await employer_ids_for(db, user.id),
    )


async def get_json(db_url: str, name: str, role: str, path: str, **kwargs: Any) -> dict[str, Any]:
    async with make_client(db_url) as client:
        await login_as(client, name, role)
        resp = await client.get(path, **kwargs)
        assert resp.status_code == 200, resp.text
        payload: dict[str, Any] = resp.json()
        return payload


#: The window that covers every seeded claim, in months.
#:
#: The seeded portfolio runs from January to September of one year, so a twelve
#: month window over that year holds all hundred claims — which is what lets the
#: reconciliation tests below compare a *window total* against a portfolio card
#: without either of them being a subset of the other. Written as an explicit
#: range rather than relying on the default window, because the default ends
#: today and "today" moves.
WHOLE_YEAR = {"from": "2026-01-01", "to": "2026-12-31"}


@requires_db
async def test_a_supervisor_and_a_handler_are_both_refused(seeded_db_url: str) -> None:
    """The inversion Story 7.1 introduced, holding for the workspace's second section.

    A gate added to one route and forgotten on its neighbour is the ordinary way
    a workspace half-opens, so the refusal is asserted on this route rather than
    inherited from the fraud suite.
    """
    for name, role in (SUPERVISOR, SCOPED_SUPERVISOR, HANDLER):
        async with make_client(seeded_db_url) as client:
            await login_as(client, name, role)
            resp = await client.get(TRENDS)

        assert resp.status_code == 403, (name, role)
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["type"] == "/problems/fraud-analytics-not-permitted"
        assert resp.headers["cache-control"] == "no-store"


@requires_db
@pytest.mark.parametrize("role", sorted(set(UserRole) - {UserRole.analyst}))
async def test_the_refusal_happens_before_any_claim_is_read(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, role: UserRole
) -> None:
    """The ordering, asserted rather than described.

    A refusal that depended on what the scoped read returned would answer
    differently for an analyst with employers and one between assignments, which
    is an oracle about the assignment table. The repository this section reads
    through is replaced with a function that fails if it is reached at all.
    """

    async def forbidden(*_args: object, **_kwargs: object) -> Sequence[sa.Row[Any]]:
        raise AssertionError("a scoped read ran before the role gate")

    monkeypatch.setattr(claim_repo, "select_claim_columns_with_employer_and_employee", forbidden)
    ctx = CallerContext(user_id=1, role=role, employer_ids=frozenset({1}))

    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await portfolio_trends(
            db,
            ctx,
            await thresholds_for(db),
            await trend_periods_for(db),
            SETTINGS,
            Segmentation(),
        )


@requires_db
async def test_a_refused_caller_loads_no_rule_document_either(
    seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal precedes every read, not only the claim read.

    This route loads *two* rule documents before calling the aggregate, so a gate
    that lived only in the service would answer 403 two `rule_document` queries
    later. `rules.parameters.load` is the single door both go through; replacing
    it with a function that fails is the whole assertion.
    """

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a rule document was loaded before the role gate answered")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *SUPERVISOR)
        monkeypatch.setattr(rule_parameters, "load", forbidden)
        resp = await client.get(TRENDS)

    assert resp.status_code == 403
    assert resp.json()["type"] == "/problems/fraud-analytics-not-permitted"


def _counting(db: AsyncSession, executed: list[str]) -> Any:
    """Wrap `db.execute` so the statements one aggregate runs can be counted.

    `test_fraud_analytics.py`'s helper, restated here rather than imported: a test
    module importing another test module's private helper is how two unrelated
    suites acquire a shared failure mode. Rule-document loads are *not* excluded
    and do not need to be — every parameter block arrives as an argument, so a
    fold that reached the engine would show up as an extra statement, which is
    exactly what this test is for.
    """
    original = db.execute

    async def counting(statement: Any, *args: Any, **kwargs: Any) -> Any:
        executed.append(str(statement).split("\n")[0])
        return await original(statement, *args, **kwargs)

    return original, counting


@requires_db
@pytest.mark.parametrize("cohort", list(TrendCohort))
@pytest.mark.parametrize("grain", list(TrendGrain))
async def test_the_trends_aggregate_takes_exactly_one_scoped_read(
    db: AsyncSession, grain: TrendGrain, cohort: TrendCohort
) -> None:
    """ "One scoped read, one pure fold" as a counted fact rather than a claim.

    Parametrised over every grain and every cohort because the tempting
    implementation is the one that reads per bucket or per cohort — twelve
    statements for a monthly window, thirty-six for a split one. The count is
    what makes "the bucket is a fold, not a query" true rather than plausible,
    and it is the assertion the recorded no-push-down decision rests on.
    """
    ctx = await context_for(db, *ANALYST)
    # Both blocks are loaded *before* the counter is armed, which is the whole
    # point of them being arguments: the route loads them, the aggregate does
    # not, and a fold that reached the rules engine itself would show up in the
    # count below rather than being excluded from it.
    thresholds = await thresholds_for(db)
    periods = await trend_periods_for(db)
    executed: list[str] = []
    original, counting = _counting(db, executed)

    db.execute = counting  # type: ignore[method-assign]
    try:
        await portfolio_trends(
            db, ctx, thresholds, periods, SETTINGS, Segmentation(), grain=grain, cohort=cohort
        )
    finally:
        db.execute = original  # type: ignore[method-assign]

    assert len(executed) == 1, executed
    assert executed[0].startswith("SELECT"), executed


@requires_db
async def test_the_payload_is_the_seeded_oracles_whole_answer(
    seeded_db_url: str, db: AsyncSession
) -> None:
    """The whole payload against an oracle that shares no helper with the fold.

    `seed_fixture.expected_trends` walks its window *backwards* from the clock and
    computes every bucket boundary from the calendar, so an implementation that
    got the window right and the buckets wrong — or the reverse — disagrees here.
    Compared as whole objects rather than figure by figure, because a payload with
    two levels of nesting is the slowest possible thing to check one number at a
    time and the easiest place for a transposition to hide.

    The clock is read from the response rather than from this process, so the
    test cannot fail on the day a request straddles UTC midnight.
    """
    payload = await get_json(seeded_db_url, *ANALYST, TRENDS)
    as_of = date.fromisoformat(payload["asOf"])
    expected = seed_fixture.expected_trends(*ANALYST, as_of)

    assert {key: payload[key] for key in expected} == expected
    # …and the rule-derived scalars against the documents as well as against the
    # oracle's own restatements of them, which is two different failures rather
    # than one assertion made twice: this pair says the payload quotes the rules
    # the fold used and keeps saying it after a retune, while `expected_trends`
    # carrying the same five numbers is what notices the retune happening.
    thresholds = await thresholds_for(db)
    periods = await trend_periods_for(db)
    assert payload["highRiskSeverityMin"] == thresholds.risk_high_min
    assert payload["medRiskSeverityMin"] == thresholds.risk_med_min
    assert payload["rulesVersion"] == thresholds.version
    assert payload["periodsVersion"] == periods.version
    assert payload["defaultBuckets"] == periods.default_buckets
    assert payload["maxBuckets"] == periods.max_buckets
    assert payload["lowConfidenceClaimMax"] == periods.low_confidence_claim_max


@requires_db
@pytest.mark.parametrize("grain", ["week", "quarter"])
@pytest.mark.parametrize("cohort", ["severity_band", "sector"])
async def test_the_oracle_agrees_at_every_grain_and_every_cohort(
    seeded_db_url: str, grain: str, cohort: str
) -> None:
    """The contrast run: a different grain and a different partition, same oracle.

    A month-only assertion is satisfiable by an implementation whose week
    arithmetic is wrong, and an unsplit one by an implementation that mis-assigns
    every cohort. The two are varied together because the cheapest bug — a cell
    keyed on the bucket alone — only shows up when both are on.
    """
    payload = await get_json(
        seeded_db_url, *ANALYST, TRENDS, params={"grain": grain, "cohort": cohort}
    )
    expected = seed_fixture.expected_trends(
        *ANALYST, date.fromisoformat(payload["asOf"]), grain=grain, cohort=cohort
    )

    assert {key: payload[key] for key in expected} == expected


@requires_db
async def test_the_window_totals_reconcile_with_the_kpi_cards(seeded_db_url: str) -> None:
    """Three live surfaces on one scope, and the equality is the assertion.

    Over a window holding the whole seeded portfolio, the volume series' window
    total *is* the Total Claims card and the paid series' *is* the Total Paid
    card — the same registered `total_paid` derivation, folded per bucket here
    and once there. A constant would pass while both were wrong together; an
    equality between two live aggregates cannot.
    """
    trends = await get_json(seeded_db_url, *ANALYST, TRENDS, params=WHOLE_YEAR)
    summary = await get_json(seeded_db_url, *ANALYST, SUMMARY)
    totals = {series["metric"]: series["valueTotal"] for series in trends["series"]}

    assert trends["claimsInWindow"] == summary["totalClaims"]
    assert totals["volume"] == summary["totalClaims"]
    assert totals["paid_cents"] == summary["totalPaidCents"]


@requires_db
async def test_the_severity_cohort_reconciles_with_the_severity_donut(
    seeded_db_url: str,
) -> None:
    """One band, two callers — the property AD-10 exists to make true.

    The cohort's series totals and 5.3's severity distribution are the same
    counts, because both go through `derivations.risk`. Asserted as a mapping so
    a band present on one surface and absent on the other fails rather than
    passing on the bands they share.
    """
    trends = await get_json(
        seeded_db_url, *ANALYST, TRENDS, params={**WHOLE_YEAR, "cohort": "severity_band"}
    )
    charts = await get_json(seeded_db_url, *ANALYST, CHARTS)

    cohorts = {
        series["cohortKey"]: series["seriesTotal"]
        for series in trends["series"]
        if series["metric"] == "volume"
    }
    donut = {item["key"]: item["count"] for item in charts["bySeverity"]["items"]}

    assert cohorts == donut


@requires_db
async def test_a_bucket_drills_to_exactly_the_claims_it_was_folded_from(
    seeded_db_url: str,
) -> None:
    """The whole promise of a drill-through, on a date facet.

    Both bounds are inclusive on both sides of the wire, so the claim list a
    bucket opens has to hold exactly `claimCount` claims — one day out at either
    end and it would not. Run over *every* bucket in the window rather than one,
    because an off-by-one at a month boundary moves a claim from one bucket to
    its neighbour and both would still look plausible alone.
    """
    trends = await get_json(seeded_db_url, *ANALYST, TRENDS, params=WHOLE_YEAR)
    volume = next(series for series in trends["series"] if series["metric"] == "volume")

    for point in volume["points"]:
        drill = await get_json(
            seeded_db_url,
            *ANALYST,
            DRILL,
            params={
                "filter[fnolFrom]": point["bucketFrom"],
                "filter[fnolTo]": point["bucketTo"],
            },
        )
        assert drill["total"] == point["value"], point["bucketKey"]


@requires_db
async def test_an_sla_bucket_drills_to_the_population_that_fed_it_and_not_the_bucket(
    seeded_db_url: str,
) -> None:
    """The other half of that promise, on the two series with a narrower population.

    A settlement point folded from three claims in a bucket of seven opens a list
    of seven if its drill carries only the bucket's dates — seven rows under a
    heading reading "71 days", which is a list that cannot be reconciled with the
    figure that produced it. The card therefore adds `filter[stage]=settled`, and
    for the RTW rate that facet **is** the denominator: the rate is over every
    settled claim in the bucket, so the drilled total has to equal the point's
    own claim count, bucket by bucket.

    The settlement mean is the honest near-miss and is asserted as one: its
    denominator is the settled claims *carrying a duration* and no facet in the
    twenty narrows on that, so its list is the settled claims — never wider than
    them, and never the whole bucket. Inventing a `hasSettlementDuration` facet to
    close the gap would put an SLA column's meaning in the drill vocabulary,
    which is the thing AD-2 keeps in one place.
    """
    trends = await get_json(seeded_db_url, *ANALYST, TRENDS, params=WHOLE_YEAR)
    volume = next(s for s in trends["series"] if s["metric"] == "volume")
    rtw = next(s for s in trends["series"] if s["metric"] == "rtw_rate_bp")
    settlement = next(s for s in trends["series"] if s["metric"] == "avg_settlement_days")
    narrowed = 0

    # `strict`, because the three series *are* the same window in the same order
    # and a payload where they were not is the bug this test exists downstream of.
    for whole, rate, mean in zip(
        volume["points"], rtw["points"], settlement["points"], strict=True
    ):
        drill = await get_json(
            seeded_db_url,
            *ANALYST,
            DRILL,
            params={
                "filter[stage]": "settled",
                "filter[fnolFrom]": rate["bucketFrom"],
                "filter[fnolTo]": rate["bucketTo"],
            },
        )
        assert drill["total"] == rate["claimCount"], rate["bucketKey"]
        assert mean["claimCount"] <= drill["total"], mean["bucketKey"]
        if drill["total"] < whole["claimCount"]:
            narrowed += 1

    # …and the narrowing is real on this seed rather than an equality that holds
    # because every claim in every bucket happens to be settled.
    assert narrowed != 0, "no bucket in the seeded year holds an unsettled claim"


@requires_db
async def test_a_cohort_bucket_drills_through_both_facets_at_once(
    seeded_db_url: str,
) -> None:
    """A cohort point is two narrowings, and the list is their intersection.

    This is the URL the chart actually builds — a period *and* a cohort — and it
    is where a facet that narrowed on the wrong column would show up as a
    plausible shorter list under a heading that promised a longer one.
    """
    trends = await get_json(
        seeded_db_url, *ANALYST, TRENDS, params={**WHOLE_YEAR, "cohort": "sector"}
    )
    series = next(
        item for item in trends["series"] if item["metric"] == "volume" and item["seriesTotal"] > 0
    )
    point = max(series["points"], key=lambda entry: entry["value"])

    drill = await get_json(
        seeded_db_url,
        *ANALYST,
        DRILL,
        params={
            "filter[fnolFrom]": point["bucketFrom"],
            "filter[fnolTo]": point["bucketTo"],
            "filter[sector]": series["cohortKey"],
        },
    )

    assert drill["total"] == point["value"]
    # The chips are the server's reading of the URL, in `FILTER_KEYS` order, and
    # each is independently clearable because each is its own parameter.
    assert [chip["key"] for chip in drill["appliedFilters"]] == ["fnolFrom", "fnolTo", "sector"]
    assert [chip["display"] for chip in drill["appliedFilters"]] == [None, None, None]


@requires_db
async def test_the_doi_facets_narrow_a_different_column_from_the_fnol_ones(
    seeded_db_url: str,
) -> None:
    """Two anchors, two pairs, and the seeded book can tell them apart.

    Every seeded claim is reported some days after it happened, so a January
    window on the two anchors returns two different populations. A DOI point
    narrowed on the FNOL column would open a plausible list of the wrong claims —
    this route's founding failure mode with a date in it.
    """
    january = {"from": "2026-01-01", "to": "2026-01-31"}
    by_doi = await get_json(seeded_db_url, *ANALYST, TRENDS, params={**january, "anchor": "doi"})
    by_fnol = await get_json(seeded_db_url, *ANALYST, TRENDS, params=january)

    doi_drill = await get_json(
        seeded_db_url,
        *ANALYST,
        DRILL,
        params={"filter[doiFrom]": "2026-01-01", "filter[doiTo]": "2026-01-31"},
    )
    fnol_drill = await get_json(
        seeded_db_url,
        *ANALYST,
        DRILL,
        params={"filter[fnolFrom]": "2026-01-01", "filter[fnolTo]": "2026-01-31"},
    )

    assert doi_drill["total"] == by_doi["claimsInWindow"]
    assert fnol_drill["total"] == by_fnol["claimsInWindow"]
    assert doi_drill["total"] != fnol_drill["total"], "the seed cannot tell the anchors apart"


@requires_db
async def test_the_disability_facet_reconciles_with_the_disability_cohort(
    seeded_db_url: str,
) -> None:
    """The cohort and the facet are one column read twice, and they must agree."""
    trends = await get_json(
        seeded_db_url, *ANALYST, TRENDS, params={**WHOLE_YEAR, "cohort": "disability"}
    )
    for series in trends["series"]:
        if series["metric"] != "volume":
            continue
        drill = await get_json(
            seeded_db_url,
            *ANALYST,
            DRILL,
            params={
                "filter[fnolFrom]": WHOLE_YEAR["from"],
                "filter[fnolTo]": WHOLE_YEAR["to"],
                "filter[disability]": series["cohortKey"],
            },
        )
        assert drill["total"] == series["seriesTotal"], series["cohortKey"]


@requires_db
async def test_a_date_facet_survives_a_cursor_round_trip(seeded_db_url: str) -> None:
    """A cursor carries its filter set, and a date has to spell the same both ways.

    A cursor that wrote a date one way and read it back another would mint a
    token its own next page refuses — the "Show more" that 400s, which is the
    defect `decode_cursor`'s coercion table exists to prevent.
    """
    params = {"filter[fnolFrom]": "2026-01-01", "filter[fnolTo]": "2026-12-31"}
    first = await get_json(seeded_db_url, *ANALYST, DRILL, params=params)
    assert first["nextCursor"] is not None

    second = await get_json(
        seeded_db_url, *ANALYST, DRILL, params={**params, "cursor": first["nextCursor"]}
    )

    assert second["total"] == first["total"]
    assert [chip["key"] for chip in second["appliedFilters"]] == ["fnolFrom", "fnolTo"]
    assert {row["claimId"] for row in second["items"]}.isdisjoint(
        {row["claimId"] for row in first["items"]}
    )


# --- scope (AD-7) ---------------------------------------------------------


@requires_db
async def test_every_claim_behind_a_scoped_analysts_series_is_inside_that_book(
    db: AsyncSession,
) -> None:
    """AD-7, asserted where an analyst can actually be scoped.

    **There is exactly one analyst in the seed and they are `scope_all`**, so no
    HTTP request in this codebase can demonstrate a *narrowed* analyst. Seeding a
    scoped one would move persona counts in Stories 1.3, 1.4 and 5.1 and in the
    e2e login fixture, which is a change this story does not own — so the
    assertion is made at the service level, where `CallerContext` is an argument.

    The employers are Jennifer Park's, because her book is a real subset with a
    known shape rather than an arbitrary pair of ids.
    """
    scoped_employers = {
        seed_fixture.employer_id_of(name) for name in seed_fixture.employers_of(*SCOPED_SUPERVISOR)
    }
    ctx = CallerContext(user_id=1, role=UserRole.analyst, employer_ids=frozenset(scoped_employers))

    trends = await portfolio_trends(
        db,
        ctx,
        await thresholds_for(db),
        await trend_periods_for(db),
        SETTINGS,
        Segmentation(),
        from_date=date(2026, 1, 1),
        to_date=date(2026, 12, 31),
    )

    hers = seed_fixture.claims_for(*SCOPED_SUPERVISOR)
    assert trends.claims_in_scope == len(hers)
    assert trends.claims_in_window == len(hers)
    # …and strictly fewer than the portfolio, or the assertion above is vacuous.
    assert len(hers) < len(seed_fixture.claims_for(*ANALYST))
    volume = next(series for series in trends.series if series.metric is TrendMetric.volume)
    assert volume.value_total == len(hers)


@requires_db
async def test_a_scoped_analysts_sector_cohorts_are_only_her_employers(
    db: AsyncSession,
) -> None:
    """The partition is scoped too, which is the half a cohort split could lose.

    A cohort vocabulary read off a *roster* rather than off the caller's rows
    would name every sector in the portfolio and draw an empty line for each one
    she cannot see — an oracle for the existence of employers outside her book.
    """
    scoped_employers = {
        seed_fixture.employer_id_of(name) for name in seed_fixture.employers_of(*SCOPED_SUPERVISOR)
    }
    ctx = CallerContext(user_id=1, role=UserRole.analyst, employer_ids=frozenset(scoped_employers))
    sectors = seed_fixture.employer_sectors()
    hers = {sectors[name] for name in seed_fixture.employers_of(*SCOPED_SUPERVISOR)}

    trends = await portfolio_trends(
        db,
        ctx,
        await thresholds_for(db),
        await trend_periods_for(db),
        SETTINGS,
        Segmentation(),
        cohort=TrendCohort.sector,
        from_date=date(2026, 1, 1),
        to_date=date(2026, 12, 31),
    )

    assert {series.cohort_key for series in trends.series} == hers
    assert hers < set(sectors.values()), "her book must be a strict subset of the portfolio"


@requires_db
async def test_an_analyst_with_no_employers_reads_an_empty_book_not_the_portfolio(
    db: AsyncSession,
) -> None:
    """`employer_scope` reads "assigned to nobody" as a predicate matching nothing.

    The distinction is the whole of AD-7's mechanism, and it is the one that fails
    open if anybody ever writes `if not ctx.employer_ids: skip the filter`. The
    empty book still gets every bucket, every count at zero and every mean null —
    "nothing in this book" says nothing about which periods exist.
    """
    ctx = CallerContext(user_id=0, role=UserRole.analyst, employer_ids=frozenset())
    periods = await trend_periods_for(db)

    trends = await portfolio_trends(
        db, ctx, await thresholds_for(db), periods, SETTINGS, Segmentation()
    )

    assert trends.claims_in_scope == 0
    assert trends.claims_in_window == 0
    assert trends.bucket_count == periods.default_buckets
    assert all(series.series_total == 0 for series in trends.series)
    assert values_of(trends, TrendMetric.volume) == [0] * periods.default_buckets
    assert values_of(trends, TrendMetric.avg_days_open) == [None] * periods.default_buckets
    # The rules still arrive — an empty book says nothing about which were in force.
    assert trends.high_risk_severity_min == (await thresholds_for(db)).risk_high_min


@requires_db
async def test_an_empty_book_with_a_cohort_split_publishes_no_series_and_says_so(
    db: AsyncSession,
) -> None:
    """The one shape a client has to handle without a series to hang it on.

    A cohort value nobody has is a line nobody drew, so an empty book splits into
    *nothing* — which is why `claimsInScope` is on the payload rather than left to
    be inferred from a series total that does not exist.
    """
    ctx = CallerContext(user_id=0, role=UserRole.analyst, employer_ids=frozenset())

    trends = await portfolio_trends(
        db,
        ctx,
        await thresholds_for(db),
        await trend_periods_for(db),
        SETTINGS,
        Segmentation(),
        cohort=TrendCohort.sector,
    )

    assert trends.series == ()
    assert trends.claims_in_scope == 0
    assert trends.bucket_count > 0


# --- the contract ---------------------------------------------------------


@requires_db
async def test_the_route_declares_exactly_fifteen_parameters_and_none_is_a_scope(
    seeded_db_url: str,
) -> None:
    """An allowlist of exactly fifteen names rather than an assertion of emptiness.

    A test called "no parameters at all" that passes on a route with fifteen is a
    sentence a reader would have to disbelieve. Five decide what is *drawn* —
    three closed enums and two dates — and Story 7.3's ten decide which claims are
    folded into it. There is still nowhere to put a user or an "as", and
    `filter[employerId]` narrows the caller's book over rows a scope-predicated
    read already returned: it can never widen it.

    The ten are written out rather than read off `segmentation.SEGMENTATION_WIRE_
    KEYS`, for `seed_fixture.HIGH_RISK_MIN`'s reason — a contract test that
    derived its expectation from the module under test would agree with it
    however either was respelled, and the whole claim is that these are the same
    strings `/dashboard/claims` accepts.
    """
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"][TRENDS]["get"]

    assert {parameter["name"] for parameter in operation["parameters"]} == {
        "grain",
        "anchor",
        "cohort",
        "from",
        "to",
        "filter[severityBand]",
        "filter[injuryType]",
        "filter[state]",
        "filter[employerId]",
        "filter[disability]",
        "filter[sector]",
        "filter[region]",
        "filter[icd10]",
        "filter[ageGroup]",
        "filter[gender]",
    }
    assert "requestBody" not in operation


@requires_db
@pytest.mark.parametrize(
    ("parameter", "value"),
    [("grain", "fortnight"), ("anchor", "settlement"), ("cohort", "employer")],
)
async def test_an_unknown_enum_value_is_a_validation_error(
    seeded_db_url: str, parameter: str, value: str
) -> None:
    """The type *is* the check — refused by the contract, before the service.

    `cohort=employer` is the interesting one: it is a dimension a later story may
    well add, and until it does it has to be refused by the enum rather than
    silently folded as "no split".
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *ANALYST)
        resp = await client.get(TRENDS, params={parameter: value})

    assert resp.status_code == 422
    assert resp.json()["type"] == "/problems/validation-error"


@requires_db
@pytest.mark.parametrize(
    ("params", "problem"),
    [
        ({"from": "2026-09-01", "to": "2026-01-01"}, "/problems/trend-range-invalid"),
        ({"from": "2000-01-01", "to": "2026-12-31"}, "/problems/trend-range-too-wide"),
        (
            {"grain": "week", "from": "2026-01-01", "to": "2026-12-31"},
            "/problems/trend-range-too-wide",
        ),
        # The three the calendar itself refuses, over the wire: a bucket whose
        # last day is the first of the year ten thousand, the same edge on a
        # week grain (two days further, and a different exception), and a
        # default window that would open before year one. All three used to be
        # a 500 on a request the caller could fix by editing a date.
        ({"to": "9999-12-31"}, "/problems/trend-range-invalid"),
        ({"grain": "week", "to": "9999-12-31"}, "/problems/trend-range-invalid"),
        ({"to": "0001-06-15"}, "/problems/trend-range-invalid"),
    ],
)
async def test_an_unservable_window_is_a_problem_document_naming_which(
    seeded_db_url: str, params: dict[str, str], problem: str
) -> None:
    """Two refusals, two problem types, and a client can act on each differently.

    The third row is the same date range as the second is *not*: fifty-two weeks
    is over the cap where twelve months is under it, which is what "the cap counts
    buckets" means on the wire.

    The last three are the same problem type as the first for the reason
    `TrendRangeInvalid` records: an unanswerable date and a backwards one are one
    fix. What matters here is the status — a window off the end of the calendar
    is a 422 the caller can act on, never the 500 an unhandled `ValueError`
    turns into.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *ANALYST)
        resp = await client.get(TRENDS, params=params)

    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["type"] == problem
    assert resp.headers["cache-control"] == "no-store"


@requires_db
async def test_the_too_wide_refusal_names_the_cap(seeded_db_url: str, db: AsyncSession) -> None:
    """The caller's only useful next move needs the number, so the body carries it."""
    periods = await trend_periods_for(db)
    async with make_client(seeded_db_url) as client:
        await login_as(client, *ANALYST)
        resp = await client.get(TRENDS, params={"from": "2000-01-01", "to": "2026-12-31"})

    assert str(periods.max_buckets) in resp.json()["detail"]


@requires_db
async def test_the_endpoint_requires_a_session(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.get(TRENDS)

    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


@requires_db
async def test_query_parameters_cannot_widen_or_change_the_scope(seeded_db_url: str) -> None:
    """The smuggling attempt: ask as the analyst, name a narrower book anyway.

    Unknown parameters are ignored rather than rejected (FastAPI's default, and
    the safer direction — a 422 here would tell an attacker which parameter names
    exist). What matters is that the answer is byte-identical.
    """
    honest = await get_json(seeded_db_url, *ANALYST, TRENDS, params=WHOLE_YEAR)

    for smuggled in (
        {"employerId": "3"},
        {"employerIds": "1,2,3"},
        {"scopeAll": "true"},
        {"handlerId": "4"},
        {"limit": "20"},
    ):
        attempt = await get_json(seeded_db_url, *ANALYST, TRENDS, params={**WHOLE_YEAR, **smuggled})
        assert attempt == honest, f"{smuggled} changed the answer"


@requires_db
async def test_the_response_is_camel_case_and_carries_nothing_else(seeded_db_url: str) -> None:
    """The exact key set on the payload and on both nestings, so nothing is added
    unnoticed — and so a field that vanished is a failure rather than a silently
    absent chart."""
    payload = await get_json(seeded_db_url, *ANALYST, TRENDS)

    assert set(payload) == {
        "asOf",
        "grain",
        "anchor",
        "cohort",
        "windowFrom",
        "windowTo",
        "bucketCount",
        "claimsInScope",
        "claimsInWindow",
        # Story 7.3's one. The window's own vocabulary, published independently
        # of the series so a period control stays live when a filter or a cohort
        # split empties the fold — see `TrendBucketResponse`.
        "buckets",
        "series",
        "defaultBuckets",
        "maxBuckets",
        "lowConfidenceClaimMax",
        "settleTargetDays",
        "rtwTargetBp",
        "highRiskSeverityMin",
        "medRiskSeverityMin",
        "rulesVersion",
        "periodsVersion",
    }
    assert set(payload["buckets"][0]) == {
        "bucketKey",
        "bucketLabel",
        "bucketFrom",
        "bucketTo",
    }
    # …and it is the *whole* window, not the buckets that happened to hold a
    # claim: the payload's own `bucketCount` is what a client checks a point
    # count against, and the control reads this list.
    assert len(payload["buckets"]) == payload["bucketCount"]
    assert [bucket["bucketKey"] for bucket in payload["buckets"]] == [
        point["bucketKey"] for point in payload["series"][0]["points"]
    ]
    assert set(payload["series"][0]) == {
        "metric",
        "cohortKey",
        "cohortLabel",
        "paletteSlot",
        "points",
        "seriesTotal",
        "valueTotal",
        "noDataBuckets",
    }
    assert set(payload["series"][0]["points"][0]) == {
        "bucketKey",
        "bucketLabel",
        "bucketFrom",
        "bucketTo",
        "value",
        "claimCount",
        "lowConfidence",
        "partial",
    }
    assert {series["metric"] for series in payload["series"]} == {
        metric.value for metric in TrendMetric
    }


@requires_db
async def test_reading_the_section_writes_no_audit_event(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """A query is not a mutation, and this story writes nothing at all (AD-12).

    Counted before and after rather than asserted absent, because other rows
    exist: what must not happen is these requests adding one.
    """
    count = sa.select(sa.func.count()).select_from(AuditEvent)
    before = (await db.execute(count)).scalar_one()

    for params in ({}, {"cohort": "sector"}, {"grain": "week"}):
        await get_json(seeded_db_url, *ANALYST, TRENDS, params=params)

    await db.commit()  # a new snapshot, or the count would be the old one
    assert (await db.execute(count)).scalar_one() == before


@requires_db
async def test_every_epic_five_dashboard_route_still_answers_a_supervisor(
    seeded_db_url: str,
) -> None:
    """The other half of the gate: nothing this story did narrowed Epic 5.

    A route added to a shared router is exactly the change that closes a
    neighbouring one by accident — a decorator on the wrong function, a dependency
    added to `APIRouter`. Asserted by reading all four, as the supervisor, and
    expecting 200. The drill route is on the list because this story appended six
    facets to it.
    """
    for path in (SUMMARY, CHARTS, "/dashboard/handler-benchmarks", DRILL):
        async with make_client(seeded_db_url) as client:
            await login_as(client, *SUPERVISOR)
            assert (await client.get(path)).status_code == 200, path


@requires_db
async def test_a_url_written_before_this_story_still_answers_identically(
    seeded_db_url: str,
) -> None:
    """Story 5.5's contract, which this story is a consumer of and not an owner of.

    Six facets joined the vocabulary and none of the fourteen changed meaning, so
    an unfiltered drill and one filtered the old way return what they always did —
    including the chip order, which is read off `FILTER_KEYS` and would have moved
    if the new facets had been inserted rather than appended.
    """
    plain = await get_json(seeded_db_url, *SUPERVISOR, DRILL)
    filtered = await get_json(
        seeded_db_url, *SUPERVISOR, DRILL, params={"filter[stage]": "settled"}
    )

    assert plain["total"] == len(seed_fixture.claims_for(*SUPERVISOR))
    assert filtered["appliedFilters"] == [{"key": "stage", "value": "settled", "display": None}]
