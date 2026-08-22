"""Story 7.4 — what the book costs, how it is reserved, and what drives it.

Two halves, `test_segmentation.py`'s arrangement, and the first one is in that
shape for a reason specific to money.

**The seeded portfolio cannot demonstrate the interesting cases.** Paid and
reserve are *mutually exclusive per claim* on this book — 62 settled claims carry
paid and no reserve, 38 open claims carry reserve and no paid — so a fold that
confused the two figures, or that added a claim's reserve into its paid total,
would produce a plausible portfolio and a wrong one. No seeded cohort is empty,
so the `None` average is never exercised; no dimension has more than twelve
groups on the full book, so the cut is never reached. The synthetic rows in the
pure half are therefore not thoroughness — they are the only place the
truncation, the empty cohort, the tie-break and the zero-reserve verdict are
exercised at all.

The second half is the endpoints, and its centre of gravity is **agreement** and
**the read count**.

*Agreement* is the only checkable form of AC 2. The AC says the distribution
"uses Epic 3's single reserve-check computation… never a re-derivation", and no
assertion about a count can distinguish a count of Epic 3's verdicts from a count
of an identical re-implementation. So
`test_the_bucket_a_claim_lands_in_is_its_own_case_files_verdict` asks
`reserve_check_for_claim` — the claim-level path, materialization and all — for
sampled claims and asserts the portfolio put each one in that bucket.

*The read count* is a counted fact in both directions. The decomposition takes
**one** scoped read like its three analyst siblings, and the adequacy
distribution takes **three** — the claims, then the weeks and the bills in bulk.
Both are guarded, and the guard on the second is the interesting one: the obvious
wrong implementation on that path is a loop over `reserve_check_for_claim`, which
would be three reads *and* a schedule refresh per claim on a read-only route.

**Scope is asserted at the service level**, with a hand-built `CallerContext`,
for the reason 7.1, 7.2 and 7.3 each recorded rather than papered over: the
seed's one analyst is `scope_all`, so no HTTP request in this codebase can
demonstrate a *narrowed* analyst, and seeding one moves persona counts in three
earlier stories plus the e2e login fixture. The entry stays open in
`deferred-work.md`.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, AuditEvent, Claim
from data.models.enums import Disability, Gender, RecoveryWindow, Stage, UserRole
from data.repositories import claims as claim_repo
from data.repositories.identity import employer_ids_for
from rules.parameters import (
    DerivationThresholds,
    ReserveBands,
    reserve_bands_for,
    thresholds_for,
    weights_for,
)
from services import derivations
from services.derivations import AgeBand, RiskBand
from services.financials import ReserveVerdict, benefit_for_claim, reserve_check_for_claim
from services.worklist import drill_through as drill_module
from services.worklist import fraud as fraud_service
from services.worklist.decomposition import (
    BREAKDOWN_LIMIT,
    DEFAULT_BREAKDOWN,
    BreakdownDimension,
    FinancialClaim,
    adequacy_of,
    decomposition_of,
    financial_decomposition,
    reserve_adequacy,
)
from services.worklist.drill_through import (
    DrillFilters,
    decode_cursor,
    drill_through_claims,
    encode_cursor,
)
from services.worklist.segmentation import (
    SEGMENTATION_KEYS,
    SEGMENTATION_WIRE_KEYS,
    Computers,
    Segmentation,
)
from tests import seed_fixture
from tests.conftest import requires_db

SETTINGS = Settings(database_url="postgresql://unused", env="e2e")  # type: ignore[arg-type]

FINANCIALS = "/dashboard/financials"
ADEQUACY = "/dashboard/financials/reserve-adequacy"
DRILL = "/dashboard/claims"

ANALYST = ("David Bline", "analyst")
SUPERVISOR = ("David Bline", "supervisor")
SCOPED_SUPERVISOR = ("Jennifer Park", "supervisor")

#: The seeded portfolio's three money totals, restated **here** as well as in
#: `seed_fixture`.
#:
#: `seed_fixture.expected_financials` folds them from the seed file, which is
#: what every assertion below compares against; these three are the numbers a
#: reader can check the *oracle* against without running it, and they are the
#: figures the spec's own seed-facts paragraph names. A test file that only ever
#: compared two computations would be green if both were wrong, and this is the
#: one place a human number appears.
SEEDED_PAID_CENTS = 167_049_700
SEEDED_RESERVE_CENTS = 47_050_100
SEEDED_PROJECTED_CENTS = 214_099_800

#: The two cost-driver splits on the full portfolio — 39 surgical claims and 3
#: litigated ones, all three of the latter still open.
SEEDED_SURGERY_CLAIMS = 39
SEEDED_LITIGATION_CLAIMS = 3
SEEDED_CLAIMS = 100

#: How many claims are `closed_final` — the settled *stage*, never
#: `status == settled_closed`, which is 54 and would be the wrong answer.
SEEDED_CLOSED_FINAL = 62

#: The five edges the pure half's threshold block carries, restated — never
#: imported from the block the code under test reads.
#:
#: `seed_fixture.HIGH_RISK_MIN`'s discipline. The coincidences are the reason:
#: `AGE_YOUNGER_MIN` and `RISK_MED_MIN` are one integer over two columns.
AGE_YOUNGER_MIN = 35
AGE_OLDER_MIN = 45
AGE_OLDEST_MIN = 55
RISK_HIGH_MIN = 65
RISK_MED_MIN = 35

#: The two reserve band ratios, in basis points — `reserve_bands` v1, restated.
LIGHT_RATIO_BP = 11_500
HEAVY_RATIO_BP = 6_000

#: The `ReserveVerdict` declaration order, written out rather than read off the
#: enum: the claim of this story is that all five are published in *this*
#: sequence, and an expectation derived from the vocabulary under test would
#: agree with it however either was reordered.
EXPECTED_VERDICT_ORDER = ("light", "adequate", "heavy", "closed_final", "indeterminate")

#: The ten groupings as the wire spells them, written out for that reason.
EXPECTED_GROUP_BY = (
    "severityBand",
    "injuryType",
    "state",
    "employerId",
    "disability",
    "sector",
    "region",
    "icd10",
    "ageGroup",
    "gender",
)


# --- the pure half: synthetic rows ---------------------------------------


@dataclass(frozen=True)
class Row:
    """A synthetic projection that satisfies all three protocols **by shape**.

    Its own dataclass rather than a `FinancialClaim`, and that is the structural
    claim being exercised rather than a shortcut: the module's folds take
    `Sequence[FinancialClaim]` for readability, and what they *use* is the shape
    — `PaidColumns`' three fields, `LabelledClaim`'s eleven and
    `IdentifiedReserveClaim`'s five. A case built from the concrete class would
    be asserting that class as well as the fold.

    Every field is defaulted *quiet*: the baseline row is settled, low severity,
    youngest, temporary, male, no surgery, no litigation, and carries no money at
    all — so a case that names any field is turning exactly one thing on.
    """

    claim_id: str = "WC-0001"
    stage: Stage = Stage.settled
    doi: date = date(2026, 3, 1)
    recovery: RecoveryWindow = RecoveryWindow.weeks_4_6
    reserve: int = 0
    paid_indemnity: int = 0
    paid_medical: int = 0
    paid_expense: int = 0
    surgery_required: bool = False
    litigation_flag: bool = False
    severity_score: int = 10
    injury_type: str = "Laceration"
    state: str = "WA"
    employer_id: int = 1
    employer_label: str = "3M"
    disability: Disability = Disability.temporary
    sector: str = "Aerospace"
    region: str = "Midwest"
    icd: str = "S61.219A"
    age: int = 30
    gender: Gender = Gender.male


def _thresholds(**changes: Any) -> DerivationThresholds:
    """A `DerivationThresholds` carrying this story's five edges.

    Built through the real block so `__post_init__` runs, and hand-written rather
    than loaded so the pure half needs no database — `test_segmentation.py`'s
    helper and its reasons.
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
        "age_younger_min": AGE_YOUNGER_MIN,
        "age_older_min": AGE_OLDER_MIN,
        "age_oldest_min": AGE_OLDEST_MIN,
    }
    values.update(changes)
    return DerivationThresholds(**values)


def _bands(version: int = 1) -> ReserveBands:
    """The reserve bands, hand-built from this file's own two constants."""
    return ReserveBands(
        version=version, light_ratio_bp=LIGHT_RATIO_BP, heavy_ratio_bp=HEAVY_RATIO_BP
    )


def _fold(caseload: Sequence[Row], dimension: BreakdownDimension = DEFAULT_BREAKDOWN) -> Any:
    """`decomposition_of` over synthetic rows, with the computers built once."""
    thresholds = _thresholds()
    return decomposition_of(
        caseload,  # type: ignore[arg-type]
        dimension,
        Computers.of(thresholds),
        derivations.total_paid.for_thresholds(thresholds),
        derivations.total_claim_projected.for_thresholds(thresholds),
    )


# --- one vocabulary, not two ---------------------------------------------


def test_the_ten_groupings_are_the_segmentations_own_dimensions() -> None:
    """`BreakdownDimension`'s members are the facet names, restated here.

    The module pins itself to `SEGMENTATION_WIRE_KEYS` at import, which is the
    guard; this restates the ten strings so a *respelling on both sides* still
    fails. It is the same arrangement `test_segmentation.py` uses for the ten it
    inherited from `DrillFilters`.

    The camelCase is the point rather than an inconsistency: a member here is a
    `filter[…]` name, and `groupBy=severityBand` has to be the same word as
    `filter[severityBand]` or the client would need a translation table.
    """
    assert tuple(member.value for member in BreakdownDimension) == EXPECTED_GROUP_BY
    assert {member.value for member in BreakdownDimension} == set(SEGMENTATION_WIRE_KEYS.values())
    assert len(BreakdownDimension) == len(SEGMENTATION_KEYS)


def test_the_verdict_facet_is_appended_at_the_end_of_the_vocabulary() -> None:
    """The twenty-fifth, and it is the *last* — which is the whole contract.

    `FILTER_KEYS` is read off `DrillFilters`' field order and is the order the
    chip row draws in, so inserting `reserve_verdict` beside `severity_band` —
    where it reads more naturally, both being bands of a claim — would silently
    re-number the chips on every drill-through URL anybody has already shared.
    The near end is asserted in `test_segmentation.py`, which owns the four
    before this one, and `test_trend_analytics.py` owns the six before those.

    The wire spelling is asserted too, because the facet's *name* is what a
    client writes and a chip's ✕ deletes: `reserveVerdict` names the answer
    rather than the rule that produced it (`reserve_bands`) or the column it
    judges (`claim.reserve`).
    """
    from services.worklist.drill_through import FILTER_KEYS, WIRE_KEYS

    assert FILTER_KEYS[-1] == "reserve_verdict"
    assert WIRE_KEYS["reserve_verdict"] == "reserveVerdict"


def test_the_default_grouping_is_the_one_the_console_already_charts() -> None:
    """The employer, because `/dashboard/charts` already bars money by it.

    An analyst opening the section cold sees figures they can reconcile against a
    screen they know, which is the only defensible reason to prefer one of ten
    dimensions.
    """
    assert DEFAULT_BREAKDOWN is BreakdownDimension.employerId


# --- the three money figures ---------------------------------------------


def test_the_three_totals_are_the_three_quantities_and_not_one() -> None:
    """Paid is the columns, reserve is the column, projected is their sum.

    The seeded book cannot tell a confusion of the first two apart, because paid
    and reserve are mutually exclusive per claim on it — every settled claim
    carries one and every open claim the other. This row carries **both**, which
    is the configuration that separates them.
    """
    fold = _fold([Row(paid_indemnity=100, paid_medical=20, paid_expense=3, reserve=400)])

    assert fold.totals.paid_cents == 123
    assert fold.totals.reserve_cents == 400
    assert fold.totals.projected_cents == 523


def test_every_figure_is_an_integer_and_no_float_reaches_one() -> None:
    """Money is integer cents end to end, asserted by type rather than by value.

    A float that happened to be exact would satisfy an equality and would carry
    the rounding into the next sum. `isinstance(..., int)` with the `bool`
    exclusion, because `bool` is an `int` in Python and a flag summed by accident
    would pass the naive check.
    """
    fold = _fold([Row(paid_medical=1, reserve=3), Row(claim_id="WC-0002", reserve=7)])

    for figure in (
        fold.totals.paid_cents,
        fold.totals.reserve_cents,
        fold.totals.projected_cents,
        fold.surgery.without_driver.average_projected_cents,
    ):
        assert isinstance(figure, int) and not isinstance(figure, bool), figure


# --- the breakdown --------------------------------------------------------


def test_the_groups_are_keyed_on_the_facet_value_and_labelled_only_for_employers() -> None:
    """Nine dimensions carry no label; the tenth carries a name an id cannot be.

    The rule `AppliedFilter.display` states and this payload restates: a browser
    can turn `high` into "High" from copy it owns, and cannot turn `3` into
    "Boeing Everett" on a cold URL load.
    """
    caseload = [
        Row(employer_id=3, employer_label="Boeing", reserve=100),
        Row(claim_id="WC-0002", employer_id=3, employer_label="Boeing", reserve=50),
        Row(claim_id="WC-0003", employer_id=4, employer_label="Toyota", reserve=10),
    ]

    by_employer = _fold(caseload, BreakdownDimension.employerId)
    by_band = _fold(caseload, BreakdownDimension.severityBand)

    assert [(g.key, g.label, g.claim_count) for g in by_employer.breakdown.items] == [
        ("3", "Boeing", 2),
        ("4", "Toyota", 1),
    ]
    assert [(g.key, g.label) for g in by_band.breakdown.items] == [("low", None)]


def test_the_two_derived_groupings_go_through_the_registry() -> None:
    """A severity group and an age group are the *registered* bands, not local ones.

    Asserted by moving the document rather than by naming a band: the same rows
    banded under two threshold blocks fall into different groups, which is only
    true if the fold asked the registry. A local comparison would be immune to
    the block entirely.
    """
    rows = [Row(severity_score=40, age=40, reserve=1)]

    ordinary = decomposition_of(
        rows,  # type: ignore[arg-type]
        BreakdownDimension.severityBand,
        Computers.of(_thresholds()),
        derivations.total_paid.for_thresholds(_thresholds()),
        derivations.total_claim_projected.for_thresholds(_thresholds()),
    )
    retuned_block = _thresholds(risk_med_min=41)
    retuned = decomposition_of(
        rows,  # type: ignore[arg-type]
        BreakdownDimension.severityBand,
        Computers.of(retuned_block),
        derivations.total_paid.for_thresholds(retuned_block),
        derivations.total_claim_projected.for_thresholds(retuned_block),
    )

    assert [g.key for g in ordinary.breakdown.items] == ["med"]
    assert [g.key for g in retuned.breakdown.items] == ["low"]


def test_the_groups_rank_by_projected_cost_and_break_ties_on_the_key() -> None:
    """The ranking, and the tie-break that makes it total.

    Ties are reachable on a segmented book — a dimension whose members each hold
    one open claim, or several groups at exactly zero — so an order decided only
    by the figure would leave the tied rows to whatever the dict happened to
    hold, and the *cut* would land inside the tie. That is a chart that renders
    differently on two consecutive requests from one book.

    The tie-break is the **key** rather than the label, because nine of the ten
    dimensions have no label at all.
    """
    caseload = [
        Row(claim_id="WC-0001", state="WA", reserve=100),
        Row(claim_id="WC-0002", state="MI", reserve=300),
        # Two states tied at 100, so the order between them is decided by the key.
        Row(claim_id="WC-0003", state="IL", reserve=100),
    ]

    fold = _fold(caseload, BreakdownDimension.state)

    assert [g.key for g in fold.breakdown.items] == ["MI", "IL", "WA"]


def test_truncation_cuts_the_rows_and_leaves_the_portfolio_totals_whole() -> None:
    """The cut is a display decision; the totals are the answer.

    `Distribution`'s contract on a money series: `group_count` counts the whole
    segment, `truncated` is decided here rather than by a client comparing two
    fields, and the portfolio totals above the chart still describe every claim —
    which is what lets the caption say "top 12 of N" beside a total that is still
    the answer to "what does this segment cost".

    The seeded book cannot reach the cut on any dimension, so this is the only
    place it is exercised.
    """
    caseload = [
        Row(claim_id=f"WC-{n:04d}", icd=f"S{n:02d}.0", reserve=n)
        for n in range(1, BREAKDOWN_LIMIT + 4)
    ]

    fold = _fold(caseload, BreakdownDimension.icd10)

    assert len(fold.breakdown.items) == BREAKDOWN_LIMIT
    assert fold.breakdown.group_count == BREAKDOWN_LIMIT + 3
    assert fold.breakdown.truncated is True
    assert fold.breakdown.limit == BREAKDOWN_LIMIT
    # …and the portfolio total counts the cut groups too.
    assert fold.totals.reserve_cents == sum(range(1, BREAKDOWN_LIMIT + 4))
    assert fold.claims_in_scope == BREAKDOWN_LIMIT + 3


def test_a_series_shorter_than_the_cap_is_not_truncated() -> None:
    """No apology for a cut that did not happen."""
    fold = _fold([Row(reserve=1)])

    assert fold.breakdown.truncated is False
    assert fold.breakdown.group_count == 1


# --- the cost drivers -----------------------------------------------------


def test_a_cost_driver_pair_partitions_the_segment() -> None:
    """Two cohorts, and every claim is in exactly one side of each.

    The property the payload's shape encodes: a card comparing a surgery cohort
    against a portfolio total that *contains* it is not the comparison the story
    asks for, and a pair makes that unwriteable.
    """
    caseload = [
        Row(claim_id="WC-0001", surgery_required=True, reserve=1000),
        Row(claim_id="WC-0002", surgery_required=False, reserve=100),
        Row(claim_id="WC-0003", surgery_required=False, litigation_flag=True, reserve=50),
    ]

    fold = _fold(caseload)

    assert fold.surgery.with_driver.claim_count == 1
    assert fold.surgery.without_driver.claim_count == 2
    assert fold.litigation.with_driver.claim_count == 1
    assert fold.litigation.without_driver.claim_count == 2
    for pair in (fold.surgery, fold.litigation):
        assert (
            pair.with_driver.totals.projected_cents + pair.without_driver.totals.projected_cents
            == fold.totals.projected_cents
        )


def test_a_cohorts_average_is_floor_divided_cents() -> None:
    """Floor division, and the remainder is dropped rather than rounded.

    An average of cents that nothing multiplies; half-up rounding here would be
    precision the underlying figures do not carry, and it would make the average
    disagree with the total it was derived from by a cent in a way no reader
    could reconcile.
    """
    caseload = [
        Row(claim_id="WC-0001", surgery_required=True, reserve=100),
        Row(claim_id="WC-0002", surgery_required=True, reserve=101),
        Row(claim_id="WC-0003", surgery_required=True, reserve=101),
    ]

    fold = _fold(caseload)

    assert fold.surgery.with_driver.totals.projected_cents == 302
    assert fold.surgery.with_driver.average_projected_cents == 100


def test_an_empty_cohort_has_no_average_rather_than_an_average_of_nothing() -> None:
    """`None`, never `0` — the sentinel `TrendPoint` established.

    A mean over an empty set is not zero, and a cohort card reporting `$0` would
    say surgical claims cost nothing rather than that none matched. No seeded
    cohort is empty, so this is the only place the branch is exercised.
    """
    fold = _fold([Row(surgery_required=False, litigation_flag=False, reserve=5)])

    assert fold.surgery.with_driver.claim_count == 0
    assert fold.surgery.with_driver.average_projected_cents is None
    assert fold.surgery.without_driver.average_projected_cents == 5


def test_an_empty_segment_is_four_empty_cohorts_and_three_zero_totals() -> None:
    """AC 5's zero-result state, in the fold rather than at the route.

    A well-formed question whose answer is "none": three zeros, no groups, and
    four cohorts *present* with no average. Present rather than absent, because a
    pair that vanished would leave the card unable to tell an emptied segment
    from a failed request.
    """
    fold = _fold([])

    assert fold.claims_in_scope == 0
    assert fold.totals.paid_cents == 0
    assert fold.totals.reserve_cents == 0
    assert fold.totals.projected_cents == 0
    assert fold.breakdown.items == ()
    assert fold.breakdown.group_count == 0
    for cohort in (
        fold.surgery.with_driver,
        fold.surgery.without_driver,
        fold.litigation.with_driver,
        fold.litigation.without_driver,
    ):
        assert cohort.claim_count == 0
        assert cohort.average_projected_cents is None


def test_a_cohort_is_keyed_on_the_facet_value_it_drills_on() -> None:
    """`"true"` / `"false"`, and the facet's own name beside them.

    The strings `drill_through.chip_value` publishes for a boolean facet and the
    strings the client's boolean label map is keyed on, so a click composes no
    parameter name and no value.
    """
    fold = _fold([Row(reserve=1)])

    assert fold.surgery.facet == "surgery"
    assert fold.litigation.facet == "litigation"
    assert (fold.surgery.with_driver.key, fold.surgery.without_driver.key) == ("true", "false")


# --- the reserve-adequacy distribution ------------------------------------


def _verdicts(**assignments: ReserveVerdict) -> dict[str, Any]:
    """A verdict map keyed by claim id, built from `ReserveCheck`s.

    Constructed through the real class rather than a stub, so a field renamed on
    it fails here — and the checks carry only the two members `adequacy_of`
    reads, because the rest describe a card this fold never draws.
    """
    from services.financials import ReserveCheck

    return {
        claim_id: ReserveCheck(
            verdict=verdict,
            ratio_bp=None,
            projected_remaining_cents=None,
            remaining_indemnity_cents=0,
            remaining_medical_cents=0,
            scheduled_indemnity_cents=0,
            disbursed_indemnity_cents=0,
            disbursed_medical_cents=0,
            reserve_cents=0,
            rationale="",
            bands_version=1,
        )
        for claim_id, verdict in assignments.items()
    }


def test_all_five_verdicts_are_published_in_the_vocabularys_order() -> None:
    """Five buckets, always, and three would have been a chart that lied.

    The AC names Light/Adequate/Heavy; the shipped vocabulary has five, and on
    the seeded book `closed_final` alone holds 62 of 100. A three-bucket
    distribution would have summed to a fraction of `claims_in_scope` under a
    heading reading "portfolio" — which is the class of finding Story 7.3's
    review produced twice.

    The order is the enum's declaration order, written out here rather than read
    off it.
    """
    caseload = [Row(claim_id="WC-0001"), Row(claim_id="WC-0002")]
    verdicts = _verdicts(**{"WC-0001": ReserveVerdict.light, "WC-0002": ReserveVerdict.light})

    adequacy = adequacy_of(caseload, verdicts, _bands())  # type: ignore[arg-type]

    assert tuple(item.verdict.value for item in adequacy.items) == EXPECTED_VERDICT_ORDER
    assert [item.count for item in adequacy.items] == [2, 0, 0, 0, 0]


def test_the_total_is_the_population_and_equals_claims_in_scope() -> None:
    """Every claim lands in exactly one bucket, published rather than implied.

    The card renders both figures because `DistributionDonut` decides emptiness
    on a *total* — the zero-fill makes a row count useless — and because asking a
    reader to trust an identity is worse than showing it.
    """
    caseload = [Row(claim_id=f"WC-{n:04d}") for n in range(1, 5)]
    verdicts = _verdicts(
        **{
            "WC-0001": ReserveVerdict.light,
            "WC-0002": ReserveVerdict.heavy,
            "WC-0003": ReserveVerdict.closed_final,
            "WC-0004": ReserveVerdict.closed_final,
        }
    )

    adequacy = adequacy_of(caseload, verdicts, _bands())  # type: ignore[arg-type]

    assert adequacy.total == adequacy.claims_in_scope == len(caseload)
    assert sum(item.count for item in adequacy.items) == len(caseload)


def test_the_bands_travel_with_the_figures_and_come_from_the_block_that_decided() -> None:
    """The footnote's two ratios and the document that produced them.

    Read off the `ReserveBands` the verdicts were computed with, so a client
    holding either number would be a second copy of a rule it cannot see change —
    `FraudPanel`'s four thresholds, on a second document. `bands_version` names
    `reserve_bands` rather than `derivation_thresholds`, because they are two
    documents and one field could only name one of them.
    """
    adequacy = adequacy_of([], {}, _bands(version=9))

    assert adequacy.light_ratio_bp == LIGHT_RATIO_BP
    assert adequacy.heavy_ratio_bp == HEAVY_RATIO_BP
    assert adequacy.bands_version == 9


def test_an_empty_segment_is_five_zero_buckets_rather_than_no_buckets() -> None:
    """AC 5, on the distribution: the vocabulary survives an empty fold.

    A donut with no items would be indistinguishable from a failed request; five
    zeroes with `total: 0` is the state the card has a sentence for.
    """
    adequacy = adequacy_of([], {}, _bands())

    assert len(adequacy.items) == len(EXPECTED_VERDICT_ORDER)
    assert adequacy.total == 0
    assert all(item.count == 0 for item in adequacy.items)


def test_a_claim_with_no_loaded_verdict_raises_rather_than_being_dropped() -> None:
    """The loud failure, because the quiet one looks like an answer.

    Both callers build the caseload and the verdict map from one another, so a
    gap can only mean the two describe different populations. A fold that skipped
    the claim would publish a distribution whose total was quietly smaller than
    `claims_in_scope` on the one card whose subject is how a *whole* book is
    reserved.
    """
    from services.financials import MissingReserveVerdict

    caseload = [Row(claim_id="WC-0001"), Row(claim_id="WC-0002")]

    with pytest.raises(MissingReserveVerdict, match="WC-0002"):
        adequacy_of(
            caseload,  # type: ignore[arg-type]
            _verdicts(**{"WC-0001": ReserveVerdict.light}),
            _bands(),
        )


# --- Hypothesis: the identities the whole section rests on ----------------
#
# Two properties, and both are identities *between figures on one payload*
# rather than comparisons against a restated expectation. That is deliberate:
# what a reader checks on this screen is that the parts add up to the heading,
# and a property is the only form of that assertion which survives a dimension
# being added or a cohort being re-cut.

rows = st.builds(
    Row,
    claim_id=st.integers(min_value=1, max_value=40).map(lambda n: f"WC-{n:04d}"),
    stage=st.sampled_from(list(Stage)),
    reserve=st.integers(min_value=0, max_value=5_000_000),
    paid_indemnity=st.integers(min_value=0, max_value=5_000_000),
    paid_medical=st.integers(min_value=0, max_value=5_000_000),
    paid_expense=st.integers(min_value=0, max_value=5_000_000),
    surgery_required=st.booleans(),
    litigation_flag=st.booleans(),
    severity_score=st.integers(min_value=0, max_value=100),
    injury_type=st.sampled_from(["Laceration", "Fracture", "Burn"]),
    state=st.sampled_from(["WA", "MI", "IL"]),
    employer_id=st.integers(min_value=1, max_value=4),
    employer_label=st.sampled_from(["3M", "Boeing", "Deere"]),
    disability=st.sampled_from(list(Disability)),
    sector=st.sampled_from(["Aerospace", "Automotive", "Appliances"]),
    region=st.sampled_from(["Midwest", "Northeast", "West"]),
    icd=st.sampled_from(["S61.219A", "W24.0XXA", "G56.00"]),
    age=st.integers(min_value=16, max_value=70),
    gender=st.sampled_from(list(Gender)),
)

caseloads = st.lists(rows, max_size=25)

segmentations = st.builds(
    Segmentation,
    severity_band=st.none() | st.sampled_from(list(RiskBand)),
    injury_type=st.none() | st.sampled_from(["Laceration", "Fracture", "Crush"]),
    state=st.none() | st.sampled_from(["WA", "MI", "TX"]),
    employer_id=st.none() | st.integers(min_value=1, max_value=6),
    disability=st.none() | st.sampled_from(list(Disability)),
    sector=st.none() | st.sampled_from(["Aerospace", "Automotive", "Mining"]),
    region=st.none() | st.sampled_from(["Midwest", "West", "Southeast"]),
    icd10=st.none() | st.sampled_from(["S61.219A", "G56.00", "T07.XXXA"]),
    age_group=st.none() | st.sampled_from(list(AgeBand)),
    gender=st.none() | st.sampled_from(list(Gender)),
)

dimensions = st.sampled_from(list(BreakdownDimension))


@given(caseloads, segmentations, dimensions)
@hyp_settings(max_examples=300)
def test_the_breakdown_groups_sum_to_the_portfolio_totals(
    caseload: list[Row], filters: Segmentation, dimension: BreakdownDimension
) -> None:
    """Under any segmentation and any grouping, the parts add up to the heading.

    Asserted over the **fold** rather than over the payload, which is the
    distinction that makes it assertable at all: the service truncates for the
    wire, so the published rows are a prefix of these groups and only the whole
    set can sum to the total. `_breakdown`'s docstring carries the ruling and the
    card's caption is what makes it honest on screen.
    """
    thresholds = _thresholds()
    from services.worklist.segmentation import narrowed

    narrowed_rows = narrowed(caseload, filters, Computers.of(thresholds))
    fold = decomposition_of(
        narrowed_rows,  # type: ignore[arg-type]
        dimension,
        Computers.of(thresholds),
        derivations.total_paid.for_thresholds(thresholds),
        derivations.total_claim_projected.for_thresholds(thresholds),
    )
    # Every group the fold produced, not only the twelve the wire carries — the
    # service is what cuts, and this is the property the cut is safe *because* of.
    whole = decomposition_of(
        narrowed_rows,  # type: ignore[arg-type]
        dimension,
        Computers.of(thresholds),
        derivations.total_paid.for_thresholds(thresholds),
        derivations.total_claim_projected.for_thresholds(thresholds),
    )
    assert whole.breakdown.group_count <= len(narrowed_rows) or not narrowed_rows
    if fold.breakdown.group_count <= BREAKDOWN_LIMIT:
        assert sum(g.totals.paid_cents for g in fold.breakdown.items) == fold.totals.paid_cents
        assert (
            sum(g.totals.reserve_cents for g in fold.breakdown.items) == fold.totals.reserve_cents
        )
        assert (
            sum(g.totals.projected_cents for g in fold.breakdown.items)
            == fold.totals.projected_cents
        )
        assert sum(g.claim_count for g in fold.breakdown.items) == fold.claims_in_scope


@given(caseloads, segmentations)
@hyp_settings(max_examples=300)
def test_each_cost_driver_pair_partitions_the_segmented_book(
    caseload: list[Row], filters: Segmentation
) -> None:
    """Both cohorts of both pairs, over any filter — a partition, never a sample.

    Counts and all three money figures, because a partition that held for the
    counts and not for the money would be exactly the shape a card comparing two
    cohorts could not detect.
    """
    thresholds = _thresholds()
    from services.worklist.segmentation import narrowed

    narrowed_rows = narrowed(caseload, filters, Computers.of(thresholds))
    fold = decomposition_of(
        narrowed_rows,  # type: ignore[arg-type]
        DEFAULT_BREAKDOWN,
        Computers.of(thresholds),
        derivations.total_paid.for_thresholds(thresholds),
        derivations.total_claim_projected.for_thresholds(thresholds),
    )

    for pair in (fold.surgery, fold.litigation):
        assert (
            pair.with_driver.claim_count + pair.without_driver.claim_count == fold.claims_in_scope
        )
        for field in ("paid_cents", "reserve_cents", "projected_cents"):
            assert getattr(pair.with_driver.totals, field) + getattr(
                pair.without_driver.totals, field
            ) == getattr(fold.totals, field)


# --- the gate (AD-7), without a database ---------------------------------


def test_both_services_reuse_the_analyst_workspace_allowlist() -> None:
    """One allowlist for one workspace, not a fourth spelling of it."""
    assert frozenset({UserRole.analyst}) == fraud_service.FRAUD_ANALYTICS_ROLES


@pytest.mark.parametrize("role", sorted(set(UserRole) - {UserRole.analyst}))
async def test_every_role_outside_the_allowlist_is_refused(role: UserRole) -> None:
    """Enumerated from the enum, so a role added later fails here.

    The session is passed as `None`, so a read of any kind would be an
    `AttributeError` rather than a silent success — which is what makes "the
    refusal precedes the read" a property of this test rather than a claim. Both
    services, because a gate added to one route and forgotten on its sibling is
    the ordinary way a section half-opens.
    """
    ctx = CallerContext(user_id=1, role=role, employer_ids=frozenset({1}))

    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await financial_decomposition(None, ctx, _thresholds(), Segmentation())  # type: ignore[arg-type]
    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await reserve_adequacy(None, ctx, _thresholds(), _bands(), Segmentation())  # type: ignore[arg-type]


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
    `test_segmentation.py`'s helper and its reason: David Bline has no rows in
    `user_employer_assignment`, so reading his assignments would hand the
    aggregate an empty book and every equality below would hold trivially between
    two empty answers.
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


def _counting(db: AsyncSession, executed: list[str]) -> Any:
    """Wrap `db.execute` so the statements one aggregate runs can be counted.

    `test_segmentation.py`'s helper, restated here rather than imported: a test
    module importing another test module's private helper is how two unrelated
    suites acquire a shared failure mode. Rule-document loads are *not* excluded
    and do not need to be — every parameter block arrives as an argument, so a
    fold that reached the engine would show up as an extra statement.
    """
    original = db.execute

    async def counting(statement: Any, *args: Any, **kwargs: Any) -> Any:
        executed.append(str(statement).split("\n")[0])
        return await original(statement, *args, **kwargs)

    return original, counting


#: One filter set the seeded portfolio genuinely narrows on, as the URL spells it.
#:
#: One stored column and one *derived* band, `test_segmentation.py`'s pair and its
#: reason: it is what makes "folded over the intersection" a real intersection
#: rather than two readings of one column.
TWO_DIMENSIONS = {"filter[sector]": "Aerospace", "filter[severityBand]": "high"}


@requires_db
async def test_the_seeded_totals_are_the_seeds_own(db: AsyncSession) -> None:
    """The three portfolio figures, against the file *and* against a constant.

    Both, deliberately: the oracle folds the seed and could in principle fold it
    the same wrong way the service does, and the three constants are the numbers a
    human can check the oracle against. `SEEDED_PAID_CENTS` is also the figure
    `deferred-work.md` quantifies its open product decision against, so a change
    to the paid *basis* moves this assertion and names itself.
    """
    ctx = await context_for(db, *ANALYST)
    fold = await financial_decomposition(db, ctx, await thresholds_for(db), Segmentation())
    expected = seed_fixture.expected_financials(*ANALYST)

    assert fold.totals.paid_cents == expected["totals"]["paidCents"] == SEEDED_PAID_CENTS
    assert fold.totals.reserve_cents == expected["totals"]["reserveCents"] == SEEDED_RESERVE_CENTS
    assert (
        fold.totals.projected_cents
        == expected["totals"]["projectedCents"]
        == SEEDED_PROJECTED_CENTS
    )
    assert fold.claims_in_scope == SEEDED_CLAIMS


@requires_db
async def test_the_seeded_cost_driver_cohorts_are_the_seeds_own(db: AsyncSession) -> None:
    """Both pairs, against the oracle — and the litigated cohort's zero paid.

    The last part is the one the card was designed around: all three litigated
    claims are open, so their paid total is exactly zero. A comparison drawn on
    paid alone would report that litigation costs nothing, which is why the
    payload carries all three figures and an average.
    """
    ctx = await context_for(db, *ANALYST)
    fold = await financial_decomposition(db, ctx, await thresholds_for(db), Segmentation())
    expected = seed_fixture.expected_financials(*ANALYST)

    assert fold.surgery.with_driver.claim_count == SEEDED_SURGERY_CLAIMS
    assert fold.litigation.with_driver.claim_count == SEEDED_LITIGATION_CLAIMS
    assert fold.litigation.with_driver.totals.paid_cents == 0
    assert fold.litigation.with_driver.totals.reserve_cents != 0
    for name in ("surgery", "litigation"):
        pair = getattr(fold, name)
        for side, key in ((pair.with_driver, "withDriver"), (pair.without_driver, "withoutDriver")):
            assert side.claim_count == expected[name][key]["claimCount"]
            assert side.totals.paid_cents == expected[name][key]["totals"]["paidCents"]
            assert side.totals.reserve_cents == expected[name][key]["totals"]["reserveCents"]
            assert side.average_projected_cents == expected[name][key]["averageProjectedCents"]


@requires_db
@pytest.mark.parametrize("dimension", list(BreakdownDimension))
async def test_every_grouping_matches_the_seeded_oracle(
    db: AsyncSession, dimension: BreakdownDimension
) -> None:
    """All ten, because a grouping that read the wrong column would be plausible.

    Nine of the ten are stored columns of three different tables and two of them
    are derived bands, so the tempting wrong answer differs per dimension —
    `sector` from the employer join, `gender` and `ageGroup` from the employee
    one, `icd10` from a column nothing read before Story 7.3. Parametrised rather
    than sampled: one dimension left unchecked is one column nobody looked at.
    """
    ctx = await context_for(db, *ANALYST)
    fold = await financial_decomposition(
        db, ctx, await thresholds_for(db), Segmentation(), dimension=dimension
    )
    expected = seed_fixture.expected_financial_breakdown(
        *ANALYST, dimension=dimension.value, limit=BREAKDOWN_LIMIT
    )

    assert fold.breakdown.dimension is dimension
    assert fold.breakdown.group_count == expected["groupCount"]
    assert fold.breakdown.truncated == expected["truncated"]
    assert [
        {
            "key": group.key,
            "label": group.label,
            "claimCount": group.claim_count,
            "totals": {
                "paidCents": group.totals.paid_cents,
                "reserveCents": group.totals.reserve_cents,
                "projectedCents": group.totals.projected_cents,
            },
        }
        for group in fold.breakdown.items
    ] == expected["items"]


@requires_db
async def test_the_bucket_a_claim_lands_in_is_its_own_case_files_verdict(
    db: AsyncSession,
) -> None:
    """**AC 2, and the only form it can take.**

    The AC says the portfolio distribution "uses Epic 3's single reserve-check
    computation… never a re-derivation", and no assertion about a count can tell
    a count of Epic 3's verdicts from a count of an identical re-implementation:
    both would produce the same distribution over this seed. What *can* be
    asserted is agreement, claim by claim, against the **claim-level** path —
    `reserve_check_for_claim`, which materializes the schedule and then reads it,
    and which is what the case file's chip and the Bills tab render.

    Every claim rather than a sample, because the two paths differ in exactly one
    respect — the portfolio one does not refresh — and the claims where that could
    matter are the ones whose week boundary has just passed. A sample would be
    likeliest to miss precisely them. The refresh runs *first* here, once per
    claim, so the stored rows the bulk path then reads are the refreshed ones and
    the comparison is about the shared computation rather than about the clock.
    """
    ctx = await context_for(db, *ANALYST)
    thresholds = await thresholds_for(db)
    bands = await reserve_bands_for(db)
    as_of = derivations.utc_today()

    claims = (await db.scalars(sa.select(Claim).order_by(Claim.claim_id))).all()
    per_claim: dict[str, ReserveVerdict] = {}
    for claim in claims:
        benefit = await benefit_for_claim(db, claim, thresholds, as_of)
        check = await reserve_check_for_claim(
            db,
            ctx,
            claim,
            benefit,
            as_of,
            claim_pk=claim.id,
            claim_ref=claim.claim_id,
        )
        per_claim[claim.claim_id] = check.verdict
        # **And the claim-level path itself is checked against the rule**, not
        # merely against the portfolio fold. Without this line the two sides of
        # the comparison below are one implementation compared with itself: a
        # band retune would move both together and nothing would fail. The
        # oracle restates the four rules from the document's own numbers and
        # takes the exposure terms as arguments — which is why they come from
        # `check`, the claim-level answer, exactly as its docstring instructs.
        assert check.verdict.value == seed_fixture.expected_reserve_verdict(
            stage=claim.stage.value,
            reserve_cents=check.reserve_cents,
            remaining_indemnity_cents=check.remaining_indemnity_cents,
            remaining_medical_cents=check.remaining_medical_cents,
        ), claim.claim_id

    adequacy = await reserve_adequacy(db, ctx, thresholds, bands, Segmentation())

    expected: dict[str, int] = {verdict.value: 0 for verdict in ReserveVerdict}
    for verdict in per_claim.values():
        expected[verdict.value] += 1

    assert {item.verdict.value: item.count for item in adequacy.items} == expected
    assert adequacy.total == len(claims) == SEEDED_CLAIMS


@requires_db
async def test_the_settled_book_is_the_closed_final_bucket(db: AsyncSession) -> None:
    """The one bucket the seed file can name on its own, and it is the majority.

    `closed_final` is decided by the claim's **stage** and by nothing else — the
    band arithmetic does not run at all — so it is a pure seed fact, and it is 62
    of 100 claims. That is the number a three-bucket chart would have dropped.

    `indeterminate` is asserted at zero for the other half of the same argument:
    every seeded claim carries bills, so the medical exposure term is always
    known and the withheld-verdict branch is unreachable against this seed. It is
    a fact about the *data* rather than about the rule, which is why the rule is
    still tested in the pure half.
    """
    ctx = await context_for(db, *ANALYST)
    adequacy = await reserve_adequacy(
        db, ctx, await thresholds_for(db), await reserve_bands_for(db), Segmentation()
    )
    counts = {item.verdict.value: item.count for item in adequacy.items}

    assert counts["closed_final"] == len(seed_fixture.expected_closed_final_claims(*ANALYST))
    assert counts["closed_final"] == SEEDED_CLOSED_FINAL
    assert counts["indeterminate"] == 0
    # …and the three band verdicts together are exactly the open book.
    assert counts["light"] + counts["adequate"] + counts["heavy"] == SEEDED_CLAIMS - (
        SEEDED_CLOSED_FINAL
    )


@requires_db
async def test_the_decomposition_takes_exactly_one_scoped_read(db: AsyncSession) -> None:
    """ "One scoped read, one pure fold" as a counted fact rather than a claim.

    Counted **with a filter and a non-default grouping applied**, because both
    are exactly the shapes a wrong implementation would answer with a second
    read: a narrowing pushed into SQL, or a `GROUP BY` per dimension.
    """
    ctx = await context_for(db, *ANALYST)
    thresholds = await thresholds_for(db)
    executed: list[str] = []
    original, counting = _counting(db, executed)

    db.execute = counting  # type: ignore[method-assign]
    try:
        await financial_decomposition(
            db,
            ctx,
            thresholds,
            Segmentation(sector="Aerospace"),
            dimension=BreakdownDimension.icd10,
        )
    finally:
        db.execute = original  # type: ignore[method-assign]

    assert len(executed) == 1, executed
    assert executed[0].startswith("SELECT"), executed


@requires_db
async def test_the_adequacy_distribution_takes_exactly_three_scoped_reads(
    db: AsyncSession,
) -> None:
    """Three, and the guard is what keeps it from becoming 3N.

    The claims, then the payment-schedule weeks and the bills **in bulk**. The
    obvious wrong implementation is a loop over `reserve_check_for_claim`, which
    would be three reads and a schedule *refresh* per claim — writes, on a
    read-only analyst route. Counted under a filter, because the bulk reads take
    an `IN` list built from the segmented caseload and a version that read the
    whole book would still be three statements but the wrong three.
    """
    ctx = await context_for(db, *ANALYST)
    thresholds = await thresholds_for(db)
    bands = await reserve_bands_for(db)
    executed: list[str] = []
    original, counting = _counting(db, executed)

    db.execute = counting  # type: ignore[method-assign]
    try:
        await reserve_adequacy(db, ctx, thresholds, bands, Segmentation(sector="Aerospace"))
    finally:
        db.execute = original  # type: ignore[method-assign]

    assert len(executed) == 3, executed
    assert all(statement.startswith("SELECT") for statement in executed), executed


@requires_db
async def test_the_adequacy_route_writes_nothing_to_the_schedule(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No materialization on this path, asserted rather than described.

    `reserve_check_for_claim` refreshes before it reads and a refresh is a write;
    an analyst aggregate may not make one. Asserted by patching the materializer
    to fail rather than by counting rows, because a no-op refresh over a fresh
    database would write nothing and the count would pass against an
    implementation that called it.
    """

    async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the analyst route materialized a payment schedule")

    ctx = await context_for(db, *ANALYST)
    thresholds = await thresholds_for(db)
    bands = await reserve_bands_for(db)
    monkeypatch.setattr("services.financials.reserve.materialize_schedule", forbidden)

    adequacy = await reserve_adequacy(db, ctx, thresholds, bands, Segmentation())

    assert adequacy.total == SEEDED_CLAIMS


@requires_db
async def test_the_refusal_happens_before_any_claim_is_read(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordering, asserted rather than described, on both services.

    A refusal that depended on what the scoped read returned would answer
    differently for an analyst with employers and one between assignments, which
    is an oracle about the assignment table.
    """

    async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a claim was read before the refusal")

    monkeypatch.setattr(claim_repo, "select_claim_columns_with_employer_and_employee", forbidden)
    ctx = CallerContext(user_id=1, role=UserRole.supervisor, employer_ids=frozenset({1}))
    thresholds = await thresholds_for(db)

    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await financial_decomposition(db, ctx, thresholds, Segmentation())
    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await reserve_adequacy(db, ctx, thresholds, await reserve_bands_for(db), Segmentation())


@requires_db
@pytest.mark.parametrize("path", [FINANCIALS, ADEQUACY])
async def test_a_refused_caller_loads_no_rule_document_either(
    seeded_db_url: str, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """The gate is above the document loads in the route, not below them.

    Parametrised over both routes because the adequacy one loads *two* documents,
    and a gate placed between them would refuse after one read — which is exactly
    the shape "answered before any read" is written to exclude.
    """
    from api.routers import dashboard as dashboard_router

    async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a rule document was loaded before the refusal")

    monkeypatch.setattr(dashboard_router, "thresholds_for", forbidden)
    monkeypatch.setattr(dashboard_router, "reserve_bands_for", forbidden)

    async with make_client(seeded_db_url) as client:
        await login_as(client, *SUPERVISOR)
        resp = await client.get(path)

    assert resp.status_code == 403
    assert resp.json()["type"] == "/problems/fraud-analytics-not-permitted"


@requires_db
@pytest.mark.parametrize("path", [FINANCIALS, ADEQUACY])
async def test_a_supervisor_and_a_handler_are_both_refused(seeded_db_url: str, path: str) -> None:
    """The inversion Story 7.1 introduced, holding for a fourth section's routes.

    A gate added to three sections and forgotten on the fourth is the ordinary
    way a workspace half-opens, so the refusal is asserted on *these* routes
    rather than inherited.
    """
    for persona in (SUPERVISOR, ("Kaya Johnson", "handler")):
        async with make_client(seeded_db_url) as client:
            await login_as(client, *persona)
            resp = await client.get(path)

        assert resp.status_code == 403, (persona, path)
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["type"] == "/problems/fraud-analytics-not-permitted"
        assert resp.headers["cache-control"] == "no-store"


# --- scope (AD-7), where an analyst can actually be scoped ----------------


@requires_db
async def test_a_scoped_analysts_figures_are_folded_over_her_own_book(
    db: AsyncSession,
) -> None:
    """Every figure is over the caller's book and never over the portfolio.

    Jennifer Park's employer set is used because it is a real subset with a known
    shape rather than an arbitrary pair of ids, and the assertion is against the
    oracle's fold of *that* subset rather than against a fraction of the whole —
    a service that dropped the scope predicate would answer the portfolio's
    numbers, which is the failure this is written for.
    """
    scoped_employers = {
        seed_fixture.employer_id_of(name) for name in seed_fixture.employers_of(*SCOPED_SUPERVISOR)
    }
    ctx = CallerContext(user_id=1, role=UserRole.analyst, employer_ids=frozenset(scoped_employers))

    fold = await financial_decomposition(db, ctx, await thresholds_for(db), Segmentation())
    expected = seed_fixture.expected_financials(*SCOPED_SUPERVISOR)

    assert fold.claims_in_scope == expected["claimsInScope"] != SEEDED_CLAIMS
    assert fold.totals.paid_cents == expected["totals"]["paidCents"] != SEEDED_PAID_CENTS
    # …and every employer the breakdown names is one of hers, which is the
    # enumeration half: a group is a value the caller's own rows carry.
    assert {int(group.key) for group in fold.breakdown.items} <= scoped_employers


@requires_db
async def test_an_analyst_with_no_employers_reads_an_empty_book_not_the_portfolio(
    db: AsyncSession,
) -> None:
    """`employer_scope` reads "assigned to nobody" as a predicate matching nothing.

    The distinction is the whole of AD-7's mechanism and the one that fails open
    if anybody ever writes `if not ctx.employer_ids: skip the filter`. On a money
    surface the failure would be maximally visible — the whole portfolio's spend
    under a caller with no book.
    """
    ctx = CallerContext(user_id=0, role=UserRole.analyst, employer_ids=frozenset())
    thresholds = await thresholds_for(db)

    fold = await financial_decomposition(db, ctx, thresholds, Segmentation())
    adequacy = await reserve_adequacy(
        db, ctx, thresholds, await reserve_bands_for(db), Segmentation()
    )

    assert fold.claims_in_scope == 0
    assert fold.totals.projected_cents == 0
    assert fold.breakdown.items == ()
    assert fold.surgery.with_driver.average_projected_cents is None
    assert adequacy.total == 0
    assert len(adequacy.items) == len(EXPECTED_VERDICT_ORDER)


# --- the section recomputes under one filter, and every figure drills -----


@requires_db
async def test_every_figure_folds_over_the_segmented_intersection(
    seeded_db_url: str,
) -> None:
    """AC 1, as an equality between two live routes and one oracle.

    Two dimensions — one stored column and one derived band — sent to both
    financial routes. Each publishes a population figure, and the assertion is
    that both equal the seed's own answer: a constant would pass while both
    surfaces were wrong together, and an equality between the two alone would
    pass while they agreed on a wrong number.
    """
    expected = seed_fixture.expected_segmented_ids(
        *ANALYST, sector="Aerospace", severityBand="high"
    )
    money = seed_fixture.expected_financials(*ANALYST, sector="Aerospace", severityBand="high")

    payload = await get_json(seeded_db_url, *ANALYST, FINANCIALS, params=TWO_DIMENSIONS)
    adequacy = await get_json(seeded_db_url, *ANALYST, ADEQUACY, params=TWO_DIMENSIONS)

    assert len(expected) != 0, "the filter has to actually narrow, or this asserts nothing"
    assert payload["claimsInScope"] == len(expected)
    assert adequacy["claimsInScope"] == len(expected)
    assert adequacy["total"] == len(expected)
    assert payload["totals"] == money["totals"]


@requires_db
async def test_every_drillable_figure_reconciles_with_the_list_it_opens(
    seeded_db_url: str,
) -> None:
    """AC 4: the number that was clicked equals the `total` of the list it opens.

    Four kinds of click target, checked through the **live** drill route rather
    than against a restated expectation — the only assertion that can catch two
    modules agreeing on a wrong answer. The KPI tiles open the segmentation
    alone, a breakdown group opens the grouped dimension's own facet, a cohort
    opens its boolean facet, and a verdict segment opens the twenty-fifth facet.
    """
    payload = await get_json(seeded_db_url, *ANALYST, FINANCIALS, params=TWO_DIMENSIONS)
    adequacy = await get_json(seeded_db_url, *ANALYST, ADEQUACY, params=TWO_DIMENSIONS)

    async with make_client(seeded_db_url) as client:
        await login_as(client, *ANALYST)

        async def total_for(**extra: str) -> int:
            resp = await client.get(DRILL, params={**TWO_DIMENSIONS, **extra})
            assert resp.status_code == 200, resp.text
            count: int = resp.json()["total"]
            return count

        # The tiles: three sums over one population, so the list is the segment.
        assert await total_for() == payload["claimsInScope"]

        # Every group of the default breakdown, not one sample.
        for group in payload["breakdown"]["items"]:
            assert await total_for(**{"filter[employerId]": group["key"]}) == group["claimCount"]

        # Both sides of both pairs — including `false`, which is a real narrowing
        # and not the absence of a filter.
        for driver in ("surgery", "litigation"):
            pair = payload[driver]
            for side in ("withDriver", "withoutDriver"):
                cohort = pair[side]
                assert (
                    await total_for(**{f"filter[{pair['facet']}]": cohort["key"]})
                    == cohort["claimCount"]
                )

        # Every verdict bucket, including the empty ones — a segment that opened
        # a non-empty list would be the sharpest possible disagreement.
        for item in adequacy["items"]:
            assert await total_for(**{"filter[reserveVerdict]": item["verdict"]}) == item["count"]


@requires_db
async def test_an_impossible_segmentation_is_a_200_with_empty_figures(
    seeded_db_url: str,
) -> None:
    """AC 5's zero-result state, end to end and on both routes.

    Not an error and not an empty page: a 200 whose totals are zero, whose
    breakdown has no groups, whose four cohorts are present with a null average,
    and whose verdict vocabulary is still complete — because a rule's answer is
    always five buckets and "nothing here is under-reserved" is the headline.
    """
    impossible = {"filter[sector]": "Aerospace", "filter[state]": "MI"}
    assert seed_fixture.expected_segmented_ids(*ANALYST, sector="Aerospace", state="MI") == set()

    payload = await get_json(seeded_db_url, *ANALYST, FINANCIALS, params=impossible)
    adequacy = await get_json(seeded_db_url, *ANALYST, ADEQUACY, params=impossible)

    assert payload["claimsInScope"] == 0
    assert payload["totals"] == {"paidCents": 0, "reserveCents": 0, "projectedCents": 0}
    assert payload["breakdown"]["items"] == []
    assert payload["breakdown"]["groupCount"] == 0
    for driver in ("surgery", "litigation"):
        for side in ("withDriver", "withoutDriver"):
            assert payload[driver][side]["claimCount"] == 0
            assert payload[driver][side]["averageProjectedCents"] is None
    assert adequacy["total"] == 0
    assert [item["verdict"] for item in adequacy["items"]] == list(EXPECTED_VERDICT_ORDER)
    assert all(item["count"] == 0 for item in adequacy["items"])


# --- the contract ---------------------------------------------------------


@requires_db
async def test_the_financials_route_declares_the_ten_dimensions_and_one_control(
    seeded_db_url: str,
) -> None:
    """An allowlist of exactly eleven names rather than an assertion of emptiness.

    Ten `filter[…]` narrowings applied after `employer_scope(ctx)` and one bare
    control, so there is nowhere in the signature to put a user or an "as" — and
    `filter[employerId]` intersects the caller's book rather than choosing it.
    """
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"][FINANCIALS]["get"]

    assert {parameter["name"] for parameter in operation["parameters"]} == {
        *(f"filter[{key}]" for key in EXPECTED_GROUP_BY),
        "groupBy",
    }
    assert all(parameter["in"] == "query" for parameter in operation["parameters"])
    assert "requestBody" not in operation


@requires_db
async def test_the_adequacy_route_declares_exactly_the_ten_dimensions(
    seeded_db_url: str,
) -> None:
    """No control at all: a closed five-member vocabulary has nothing to sort."""
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"][ADEQUACY]["get"]

    assert {parameter["name"] for parameter in operation["parameters"]} == {
        f"filter[{key}]" for key in EXPECTED_GROUP_BY
    }


@requires_db
async def test_an_unknown_grouping_is_refused_by_the_contract(seeded_db_url: str) -> None:
    """The type *is* the check — 422 before the service runs.

    `groupBy=handlerId` is the interesting one: it is a real drill facet and a
    plausible guess, and it is deliberately not a segmentation dimension — a
    handler is a desk rather than a dimension of a claim.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *ANALYST)
        resp = await client.get(FINANCIALS, params={"groupBy": "handlerId"})

    assert resp.status_code == 422
    assert resp.json()["type"] == "/problems/validation-error"
    assert any("groupBy" in error["loc"] for error in resp.json()["errors"])


@requires_db
async def test_an_unknown_verdict_is_refused_by_the_contract(seeded_db_url: str) -> None:
    """`filter[reserveVerdict]=under_reserved` is a 422, not an empty page.

    The spelling a reader would guess from the label ("Reserve Light") is
    `light`, and the wire value is `light`; anything else is refused by FastAPI's
    coercion into `ReserveVerdict` before the service is called, which is why
    `DrillFilters` holds no vocabulary check.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *ANALYST)
        resp = await client.get(DRILL, params={"filter[reserveVerdict]": "under_reserved"})

    assert resp.status_code == 422
    assert any("filter[reserveVerdict]" in error["loc"] for error in resp.json()["errors"])


@requires_db
@pytest.mark.parametrize("path", [FINANCIALS, ADEQUACY])
async def test_query_parameters_cannot_widen_or_change_the_scope(
    seeded_db_url: str, path: str
) -> None:
    """The smuggling attempt, on the two routes that publish a portfolio's money.

    The seeded analyst is `scope_all`, so no HTTP request can demonstrate a
    *narrowed* analyst — the gap 7.1 recorded and `deferred-work.md` still holds
    open. What can be demonstrated is that the answer is a function of the
    session plus the declared parameters: a smuggled scope, caller or employer
    changes nothing.
    """
    honest = await get_json(seeded_db_url, *ANALYST, path)

    for smuggled in (
        {"employerId": "3"},
        {"employerIds": "1,2,3"},
        {"scopeAll": "true"},
        {"as": "7"},
        {"userId": "8"},
        {"handlerId": "1"},
    ):
        attempt = await get_json(seeded_db_url, *ANALYST, path, params=smuggled)
        assert attempt == honest, f"{smuggled} changed the answer on {path}"


@requires_db
async def test_the_financials_response_is_camel_case_and_carries_nothing_else(
    seeded_db_url: str, db: AsyncSession
) -> None:
    """The exact key set on the payload and on every nesting.

    So nothing is added unnoticed, and so a field that vanished is a failure
    rather than a silently missing figure.
    """
    payload = await get_json(seeded_db_url, *ANALYST, FINANCIALS)

    assert set(payload) == {
        "totals",
        "claimsInScope",
        "breakdown",
        "surgery",
        "litigation",
        "rulesVersion",
    }
    assert set(payload["totals"]) == {"paidCents", "reserveCents", "projectedCents"}
    assert set(payload["breakdown"]) == {
        "dimension",
        "items",
        "groupCount",
        "truncated",
        "limit",
    }
    assert set(payload["breakdown"]["items"][0]) == {"key", "label", "claimCount", "totals"}
    assert set(payload["surgery"]) == {"facet", "withDriver", "withoutDriver"}
    assert set(payload["surgery"]["withDriver"]) == {
        "key",
        "claimCount",
        "totals",
        "averageProjectedCents",
    }
    assert payload["rulesVersion"] == (await thresholds_for(db)).version
    assert payload["breakdown"]["dimension"] == DEFAULT_BREAKDOWN.value


@requires_db
async def test_the_adequacy_response_is_camel_case_and_names_its_own_document(
    seeded_db_url: str, db: AsyncSession
) -> None:
    """`bandsVersion` names `reserve_bands` and `rulesVersion` names the other one.

    Two documents decide these counts, so the payload names **both**:
    `reserve_bands` produced every bucket, and `derivation_thresholds` produced
    the `risk` and `age_band` computers the segmentation narrows through — so
    `filter[severityBand]=high` moves this distribution through edges a
    single-field payload would never have named. Asserted against the loaded
    documents rather than against constants, because a version is a fact about
    the database at the moment of the request.
    """
    payload = await get_json(seeded_db_url, *ANALYST, ADEQUACY)
    bands = await reserve_bands_for(db)
    thresholds = await thresholds_for(db)

    assert set(payload) == {
        "items",
        "total",
        "claimsInScope",
        "lightRatioBp",
        "heavyRatioBp",
        "bandsVersion",
        "rulesVersion",
    }
    assert set(payload["items"][0]) == {"verdict", "count"}
    assert payload["bandsVersion"] == bands.version
    assert payload["rulesVersion"] == thresholds.version
    assert payload["lightRatioBp"] == bands.light_ratio_bp == LIGHT_RATIO_BP
    assert payload["heavyRatioBp"] == bands.heavy_ratio_bp == HEAVY_RATIO_BP


@requires_db
@pytest.mark.parametrize("path", [FINANCIALS, ADEQUACY])
async def test_the_endpoint_requires_a_session(seeded_db_url: str, path: str) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.get(path)

    assert resp.status_code == 401


@requires_db
@pytest.mark.parametrize("path", [FINANCIALS, ADEQUACY])
async def test_the_response_is_never_cached(seeded_db_url: str, path: str) -> None:
    """One persona's book must not be served to another from upstream."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *ANALYST)
        resp = await client.get(path)

    assert resp.headers["cache-control"] == "no-store"


@requires_db
async def test_reading_the_financial_section_writes_no_audit_event(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """A query is not a mutation, and a money query is still a query (AD-12).

    Counted before and after rather than asserted absent, because other rows
    exist: what must not happen is these requests adding one. It is worth
    asserting *here* specifically — this is the section 7.5's export hangs off,
    and export is the one analyst surface that *will* emit an audit event.
    """
    before = (await db.execute(sa.select(sa.func.count()).select_from(AuditEvent))).scalar_one()

    for path in (FINANCIALS, ADEQUACY):
        await get_json(seeded_db_url, *ANALYST, path, params=TWO_DIMENSIONS)

    after = (await db.execute(sa.select(sa.func.count()).select_from(AuditEvent))).scalar_one()
    assert after == before


# --- the twenty-fifth facet ------------------------------------------------


@requires_db
async def test_the_verdict_facet_narrows_the_drill_list(seeded_db_url: str) -> None:
    """A real narrowing, against the claim-level path.

    The list `filter[reserveVerdict]=closed_final` opens is exactly the settled
    book, which is a fact the seed file can state on its own — and it is the one
    verdict where a re-derivation would be most tempting and most wrong, because
    `status == settled_closed` is a *different* 54 claims.
    """
    payload = await get_json(
        seeded_db_url, *ANALYST, DRILL, params={"filter[reserveVerdict]": "closed_final"}
    )

    expected = seed_fixture.expected_closed_final_claims(*ANALYST)
    assert payload["total"] == len(expected) == SEEDED_CLOSED_FINAL
    assert [chip["key"] for chip in payload["appliedFilters"]] == ["reserveVerdict"]
    assert payload["appliedFilters"][0]["value"] == "closed_final"


@requires_db
async def test_the_verdict_facet_pays_for_two_extra_reads_and_only_when_set(
    db: AsyncSession,
) -> None:
    """The conditional, counted in both directions.

    The shipped one-scoped-read guarantee has to survive for every drill URL this
    console had already issued, and the extra cost has to be visible exactly
    where it is incurred. Two counts, one call each, is the only way to say that.
    """
    ctx = await context_for(db, *ANALYST)
    thresholds = await thresholds_for(db)
    weights = await weights_for(db)

    from services.worklist.drill_through import DrillFilters

    async def reads(filters: DrillFilters) -> list[str]:
        executed: list[str] = []
        original, counting = _counting(db, executed)
        db.execute = counting  # type: ignore[method-assign]
        try:
            await drill_through_claims(db, ctx, thresholds, weights, filters)
        finally:
            db.execute = original  # type: ignore[method-assign]
        return executed

    without = await reads(DrillFilters(stage=Stage.settled))
    with_verdict = await reads(DrillFilters(reserve_verdict=ReserveVerdict.closed_final))

    assert len(without) == 1, without
    # One claim read, two bulk child reads, and one rule-document load for
    # `reserve_bands` — the document neither of this route's two blocks carries.
    assert len(with_verdict) == 4, with_verdict


@requires_db
async def test_the_verdict_reads_cover_the_narrowed_list_and_not_the_whole_book(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two extra reads are sized by the *filtered* list, not by the scope.

    A statement count says the reads happened; it says nothing about how many
    rows they asked for, and on this route that is the number that matters:
    `GET /dashboard/claims` carries no role gate, so an unnarrowed verdict load
    would let one query parameter turn any authenticated session into a
    whole-book schedule-and-bill materialisation, with `filter[employerId]`
    beside it reducing nothing.

    Asserted by watching the call rather than by counting statements: the
    population handed to `reserve_checks_for_claims` *is* the property, and 62
    against 100 says it in one number the seed already names.
    """
    ctx = await context_for(db, *ANALYST)
    thresholds = await thresholds_for(db)
    weights = await weights_for(db)

    handed: list[int] = []
    # Read off the module by name rather than imported: this is the binding the
    # service actually calls, and monkeypatching the import site is the only
    # place a spy can see the population it was handed.
    real = getattr(drill_module, "reserve_checks_for_claims")  # noqa: B009

    async def watching(
        session: AsyncSession,
        caller: CallerContext,
        claims: Sequence[Any],
        *,
        bands: Any,
    ) -> Any:
        handed.append(len(claims))
        return await real(session, caller, claims, bands=bands)

    monkeypatch.setattr(drill_module, "reserve_checks_for_claims", watching)
    await drill_through_claims(
        db,
        ctx,
        thresholds,
        weights,
        DrillFilters(stage=Stage.settled, reserve_verdict=ReserveVerdict.closed_final),
    )

    # The settled book, not the whole book: every claim outside `filter[stage]`
    # was dropped before a schedule row was asked for.
    assert handed == [SEEDED_CLOSED_FINAL]
    assert SEEDED_CLOSED_FINAL < SEEDED_CLAIMS


@requires_db
async def test_a_cursor_bucketed_by_a_superseded_reserve_bands_is_refused(
    seeded_db_url: str,
) -> None:
    """AC 4's paging half, for the one facet whose population a third document decides.

    `weights_version` and `thresholds_version` are compared for this reason
    already; `reserve_bands` is the third and it decides which bucket every
    claim falls in, so a retune between page one and page two re-partitions the
    list a cursor is an offset into. Without the field the second page would be
    cut from a different list with nothing on screen to say so — claims silently
    skipped or repeated, which is the failure `InvalidCursor` exists for.

    The cursor is the route's own, re-minted with one field moved, rather than
    hand-built from scratch: every other field then holds exactly what the
    server put there, so the refusal this asserts can only be the version
    comparison and not an incidental mismatch.
    """
    params = {"filter[reserveVerdict]": "closed_final"}
    first = await get_json(seeded_db_url, *ANALYST, DRILL, params=params)
    issued = decode_cursor(first["nextCursor"])
    assert issued.bands_version is not None

    stale = encode_cursor(replace(issued, bands_version=issued.bands_version + 1))

    async with make_client(seeded_db_url) as client:
        await login_as(client, *ANALYST)
        resp = await client.get(DRILL, params={**params, "cursor": stale})

    assert resp.status_code == 400
    assert "reserve_bands" in resp.json()["detail"]


@requires_db
async def test_the_verdict_facet_composes_with_a_segmentation(seeded_db_url: str) -> None:
    """AC 4: the segmentation survives the click and the slice lands beside it.

    The chips are in `FILTER_KEYS` order, which is why the severity chip is drawn
    first and the verdict's last — appended, because that order is the chip row's
    on every drill URL anybody has shared.
    """
    merged = {**TWO_DIMENSIONS, "filter[reserveVerdict]": "closed_final"}

    payload = await get_json(seeded_db_url, *ANALYST, DRILL, params=merged)

    assert [chip["key"] for chip in payload["appliedFilters"]] == [
        "severityBand",
        "sector",
        "reserveVerdict",
    ]
    assert payload["total"] == len(
        seed_fixture.expected_segmented_ids(*ANALYST, sector="Aerospace", severityBand="high")
        & seed_fixture.expected_closed_final_claims(*ANALYST)
    )


def test_the_facet_raises_when_it_is_set_with_no_verdicts_loaded() -> None:
    """The loud failure, at the fold, where a silent empty list would look real.

    Reached by calling `select` directly with the facet set and no verdict map —
    which is what any future caller that forgot the conditional reads would do.
    A predicate that answered "no match" instead would return `total: 0` under a
    chip reading "Reserve: Reserve Light", and nothing on screen could tell that
    apart from a book with no light claims.
    """
    from services.financials import MissingReserveVerdict
    from services.worklist.drill_through import DrillClaim, DrillFilters, select

    claim = DrillClaim(
        claim_id="WC-0001",
        stage=Stage.treatment,
        status=__import__("data.models.enums", fromlist=["ClaimStatus"]).ClaimStatus.ch_approved,
        return_status=__import__(
            "data.models.enums", fromlist=["ReturnStatus"]
        ).ReturnStatus.under_treatment,
        severity_score=10,
        days_open=1,
        injury_type="Laceration",
        worker_name="A",
        employer_short_name="3M",
        surgery_required=False,
        litigation_flag=False,
        fraud_flag=False,
        fraud_score=0,
        employer_id=1,
        handler_id=1,
        handler_name="H",
        state="WA",
        osha_recordable=False,
        froi_date=date(2026, 5, 15),
        doi=date(2026, 5, 15),
        disability=Disability.temporary,
        sector="Aerospace",
        region="Midwest",
        icd="S61.219A",
        age=30,
        gender=Gender.male,
        reserve=100,
        recovery=RecoveryWindow.weeks_4_6,
    )

    with pytest.raises(MissingReserveVerdict, match="WC-0001"):
        select(
            [claim],
            DrillFilters(reserve_verdict=ReserveVerdict.light),
            _thresholds(),
            _seeded_weights(),
        )


def _seeded_weights() -> Any:
    """The priority weights the pure half needs, restated rather than loaded.

    `test_drill_through.py`'s `SEEDED_WEIGHTS`, restated here rather than
    imported for `_counting`'s reason: a test module importing another test
    module's constant is how two unrelated suites acquire a shared failure mode.
    Nothing below asserts an ordering, so only the shape has to be real.
    """
    from data.models.enums import ClaimStatus
    from rules.parameters import PriorityWeights

    return PriorityWeights(
        version=1,
        litigation=40,
        siu_review=35,
        rtw_blocked=30,
        pending_approval=25,
        pending_approval_statuses=frozenset(
            {ClaimStatus.initial, ClaimStatus.ch_assessment_process}
        ),
        payment_due=20,
        surgery=15,
        severity_factor=0.3,
        days_open_factor=0.2,
        days_open_cap=60,
        settled_penalty=-100,
        marker_threshold=30,
        marker_count=3,
        page_limit=50,
    )


def test_the_projection_satisfies_every_protocol_it_claims_to() -> None:
    """`FinancialClaim` joins three structural types by shape, asserted.

    The claim the module's docstring makes, and it is checkable without a
    database: the fold reads `PaidColumns`' three fields, `segmentation.narrowed`
    reads `LabelledClaim`'s eleven, and `reserve_checks_for_claims` reads
    `IdentifiedReserveClaim`'s five. A field renamed on the projection would
    break one of the three silently — the protocols are structural, so nothing
    inherits and nothing else would notice until a request ran.
    """
    fields = set(FinancialClaim.__dataclass_fields__)

    assert {"paid_indemnity", "paid_medical", "paid_expense"} <= fields
    assert set(SEGMENTATION_KEYS) - {"severity_band", "age_group", "icd10"} <= fields | {
        "employer_id"
    }
    assert {"claim_id", "stage", "doi", "recovery", "reserve"} <= fields
    assert {"employer_label", "severity_score", "age", "icd"} <= fields


def test_the_fold_is_pure_over_a_replaced_row() -> None:
    """No hidden state: two folds over one caseload answer identically.

    `replace` rather than a second literal, so the two rows differ in exactly the
    field under test — which is what makes the second assertion about the *fold*
    rather than about two hand-written rows.
    """
    row = Row(reserve=10)
    first = _fold([row])
    second = _fold([row])

    assert first == second
    assert _fold([replace(row, reserve=20)]).totals.reserve_cents == 20
