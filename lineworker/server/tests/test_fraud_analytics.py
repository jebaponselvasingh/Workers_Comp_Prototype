"""Story 7.1 — the analyst's fraud workspace, and the three rules it keeps apart.

Two halves, `test_drill_through.py`'s arrangement.

The first is database-free: the folds over synthetic projections. It exists in
that shape for a reason specific to this story rather than as a convention.
**On the seeded portfolio the three fraud rules are indistinguishable.** Every
claim carrying `fraud_flag` scores at or above 55 and every claim scoring at or
above 55 carries `fraud_flag`, so `fraud_flagged`, the `high` band and — but for
four claims scoring between the two thresholds — `siu_review` all name the same
population. An implementation that pointed the band distribution at the flagged
rule, or the SIU pipeline at the review threshold, would pass every test written
against the seed. The synthetic projections below are therefore not thoroughness:
they are the only place in this suite where the distinction is tested at all.

The second is the endpoints, and its centre of gravity is the **reconciliation
set** and the **gate**.

Reconciliation is `test_drill_through.py`'s discipline, unchanged: the flagged
population is read off `/dashboard/summary`, off `/dashboard/claims` and off this
story's own panel **in one test, on one scope**, and the three are asserted equal
with `seed_fixture`'s independent oracle beside them. A constant would pass while
every surface was wrong together; an equality between live aggregates cannot, and
the oracle catches the case where they agree on a wrong answer.

The gate is the inversion. `test_portfolio_charts.py::
test_the_analyst_reads_byte_identically_to_the_supervisor` is a live assertion
that Epic 5 gave the two roles one dashboard, and it stays true of every endpoint
it was written about. Here the analyst reads and the supervisor is refused, which
is the first thing in this console that separates them — so the allowlist is
asserted over every `UserRole` member rather than over the two that exist to be
refused today.

**Scope is asserted at the service level**, with a hand-built `CallerContext`,
and the reason is a fact about the seed rather than a preference: there is
exactly one analyst persona and they are `scope_all`, so no HTTP request in this
codebase can demonstrate a *narrowed* analyst. Seeding a scoped one would move
persona counts in Stories 1.3, 1.4 and 5.1 and in the e2e login fixture, which is
a change this story does not own. The HTTP layer still carries "no query
parameter can widen the scope"; the gap is recorded in `deferred-work.md` rather
than papered over.
"""

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agents.schemas import (
    FraudLowRiskInsight,
    FraudLowRiskNarrative,
    FraudRedFlagsInsight,
    FraudRedFlagsNarrative,
    FraudSignalFigures,
    read_fraud_clauses,
)
from api import create_app
from config import Settings
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, AuditEvent, Claim
from data.models.enums import InsightKind, Stage, UserRole
from data.repositories import claims as claim_repo
from data.repositories import insights as insight_repo
from data.repositories.identity import employer_ids_for
from rules import parameters as rule_parameters
from rules.parameters import DERIVATION_THRESHOLDS_KEY, thresholds_for
from services.derivations import FraudBand, utc_today
from services.worklist import charts
from services.worklist import fraud as fraud_service
from services.worklist.fraud import (
    CachedFraudInsight,
    FraudAnalyticsNotPermitted,
    FraudClaim,
    FraudRateSort,
    FraudRateSorts,
    fraud_panel,
    fraud_rates,
    fraud_red_flags,
    normalise_clause,
    panel_of,
    rates_of,
    red_flags_of,
)
from tests import seed_fixture
from tests.conftest import requires_db

PANEL = "/dashboard/fraud"
RATES = "/dashboard/fraud/rates"
RED_FLAGS = "/dashboard/fraud/red-flags"
SUMMARY = "/dashboard/summary"
DRILL = "/dashboard/claims"

ANALYST = ("David Bline", "analyst")
SUPERVISOR = ("David Bline", "supervisor")
SCOPED_SUPERVISOR = ("Jennifer Park", "supervisor")
HANDLER = ("Kaya Johnson", "handler")

#: The three cut-offs this story reads, restated — never imported.
#:
#: `seed_fixture` already restates all three and is the oracle every DB-backed
#: assertion below goes through. These three exist for the *pure* half, where a
#: synthetic projection has to be built at a score that sits deliberately either
#: side of a boundary — and writing `fraud_score=57` with no name beside it would
#: make the case unreadable the first time somebody asked why 57.
#:
#: They are three separate constants although two of them are 55, which is this
#: story's whole subject in one declaration.
SIU_FRAUD_SCORE_MIN = 60
FRAUD_FLAG_SCORE_MIN = 55
FRAUD_BAND_HIGH_MIN = 55
FRAUD_BAND_MED_MIN = 35

SETTINGS = Settings(database_url="postgresql://unused", env="e2e")  # type: ignore[arg-type]


# --- the pure half: synthetic projections -------------------------------


def claim(
    claim_id: str,
    *,
    fraud_flag: bool = False,
    fraud_score: int = 0,
    stage: Stage = Stage.treatment,
    injury_type: str = "Fracture",
    employer_id: int = 1,
    employer_label: str = "Acme",
    handler_id: int = 1,
    handler_name: str = "Ada",
) -> FraudClaim:
    """One projection row, with every field this story does not vary defaulted.

    Named keyword-only, so each case below reads as the *delta* from a claim that
    is unremarkable in every other way — `test_drill_through.py`'s discipline for
    a fold whose whole subject is a two-column pair.
    """
    return FraudClaim(
        claim_id=claim_id,
        fraud_flag=fraud_flag,
        fraud_score=fraud_score,
        stage=stage,
        injury_type=injury_type,
        employer_id=employer_id,
        employer_label=employer_label,
        handler_id=handler_id,
        handler_name=handler_name,
    )


@pytest.fixture
def computers() -> fraud_service._Computers:
    """The three registered computers, built from a hand-written block.

    Hand-written rather than loaded, so the pure half needs no database and so a
    case can move a threshold without publishing a rule document. Every number in
    it is one of the four restated at the top of this file.
    """
    return fraud_service._Computers.of(_thresholds())


def _thresholds(**changes: int) -> Any:
    """A `DerivationThresholds` carrying this story's four numbers.

    Built through the real block so the ordering checks in `__post_init__` run —
    a case that wanted an inverted band pair would be refused here rather than
    silently folded.
    """
    from data.models.enums import RecoveryWindow
    from rules.parameters import DerivationThresholds

    values: dict[str, Any] = {
        "version": 6,
        "risk_high_min": 65,
        "risk_med_min": 35,
        "siu_fraud_score_min": SIU_FRAUD_SCORE_MIN,
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
        "fraud_flag_score_min": FRAUD_FLAG_SCORE_MIN,
        "fraud_band_high_min": FRAUD_BAND_HIGH_MIN,
        "fraud_band_med_min": FRAUD_BAND_MED_MIN,
    }
    values.update(changes)
    return DerivationThresholds(**values)


def test_the_three_fraud_rules_name_three_different_populations(
    computers: fraud_service._Computers,
) -> None:
    """The assertion the seeded portfolio cannot make.

    Three claims, each chosen so exactly one pair of rules disagrees about it:

    - `WC-UNFLAGGED` scores above every cut-off and carries **no flag**. It is in
      the `high` band and in neither population, which is the case the band
      exists for — "how much of this book scores high and was never triaged" is
      the analyst's opening question and a banding conjoined with the flag could
      not answer it.
    - `WC-BETWEEN` is flagged and scores between the review and referral
      thresholds. It is in the review population and in the `high` band, and
      **not** in the SIU pipeline — the 13-against-9 gap the dashboard and the
      queue have carried since Story 5.1.
    - `WC-REFERRED` is flagged above both. It is in all three.

    On the seed all three rules coincide, so this is where the difference is
    tested. Every figure is read off the panel rather than off the flags, because
    what has to be distinguishable is the *published* population.
    """
    panel = panel_of(
        [
            claim("WC-UNFLAGGED", fraud_flag=False, fraud_score=90),
            claim("WC-BETWEEN", fraud_flag=True, fraud_score=57),
            claim("WC-REFERRED", fraud_flag=True, fraud_score=80),
        ],
        computers,
    )
    bands = {item.key: item.count for item in panel.by_band.items}

    assert bands[FraudBand.high.value] == 3, "the band ignores the flag"
    assert panel.flagged_claims == 2, "the review population needs the flag"
    assert panel.siu_claims == 1, "the referral population needs the higher score"


def test_the_band_ignores_the_flag_at_every_edge(
    computers: fraud_service._Computers,
) -> None:
    """The banding, at and either side of both edges, with the flag off.

    Parametrisation would read better and would hide the point: what is being
    asserted is that one *unflagged* claim lands in each of the three bands,
    which is a statement about the set rather than about three independent rows.
    """
    panel = panel_of(
        [
            claim("WC-LOW", fraud_score=FRAUD_BAND_MED_MIN - 1),
            claim("WC-MED-EDGE", fraud_score=FRAUD_BAND_MED_MIN),
            claim("WC-MED", fraud_score=FRAUD_BAND_HIGH_MIN - 1),
            claim("WC-HIGH-EDGE", fraud_score=FRAUD_BAND_HIGH_MIN),
        ],
        computers,
    )

    assert [(item.key, item.count) for item in panel.by_band.items] == [
        (FraudBand.low.value, 1),
        (FraudBand.medium.value, 2),
        (FraudBand.high.value, 1),
    ]
    assert panel.flagged_claims == 0


def test_every_band_is_published_even_when_the_scope_holds_none(
    computers: fraud_service._Computers,
) -> None:
    """The zero-fill, which is the one place this module departs from `charts`.

    `charts._declared` omits a category the scope does not contain; `_banded`
    emits it with a zero. The difference is a rule about the vocabulary — a band
    is a rule's answer over a column every claim has — and the failure it prevents
    is the reassuring one: a two-segment distribution nobody can tell from a build
    that forgot to draw the third.
    """
    panel = panel_of([claim("WC-1", fraud_score=1)], computers)

    assert [item.key for item in panel.by_band.items] == [band.value for band in FraudBand]
    assert {item.key: item.count for item in panel.by_band.items} == {
        FraudBand.low.value: 1,
        FraudBand.medium.value: 0,
        FraudBand.high.value: 0,
    }
    # …and the vocabulary is the answer, so the category count is the rule's
    # rather than the number that happen to be occupied.
    assert panel.by_band.total_categories == len(FraudBand)


def test_the_pipeline_omits_a_stage_it_does_not_reach(
    computers: fraud_service._Computers,
) -> None:
    """The other half of the same rule, pointing the other way.

    `stage` is a column, so a stage with no referred claim is a stage this
    pipeline does not reach rather than an empty segment of it —
    `charts._declared`'s contract, which the SIU pipeline follows unchanged. The
    two helpers sit one function apart in the service precisely so the difference
    is legible; this is the pair of tests that keeps it.
    """
    panel = panel_of(
        [
            claim("WC-1", fraud_flag=True, fraud_score=90, stage=Stage.investigation),
            claim("WC-2", fraud_flag=True, fraud_score=90, stage=Stage.settled),
            claim("WC-3", fraud_score=90, stage=Stage.intake),
        ],
        computers,
    )

    # Declaration order, and only the stages the *referred* claims are in.
    assert [item.key for item in panel.siu_by_stage.items] == [
        Stage.investigation.value,
        Stage.settled.value,
    ]
    assert panel.siu_by_stage.total == 2


def test_an_empty_book_answers_zeros_rather_than_failing(
    computers: fraud_service._Computers,
) -> None:
    """The empty-scope case at its limit — 200, never a 500 and never a 404.

    Reachable: an analyst assigned to no employers has a book of nothing, and
    `employer_scope` reads that as a predicate matching nothing rather than as no
    filter. Every band is present at zero, both pipelines are empty, and the
    thresholds still arrive — "nothing in this book" says nothing about which
    rules were in force.
    """
    panel = panel_of([], computers)

    assert [item.count for item in panel.by_band.items] == [0, 0, 0]
    assert panel.siu_by_stage.items == ()
    assert panel.siu_by_handler.items == ()
    assert panel.claims_in_scope == 0
    assert panel.flagged_claims == 0
    assert panel.fraud_band_high_min == FRAUD_BAND_HIGH_MIN
    assert panel.fraud_flag_score_min == FRAUD_FLAG_SCORE_MIN

    rates = rates_of([], computers, FraudRateSorts())
    assert rates.by_injury_type.items == ()
    assert rates.by_employer.items == ()
    assert rates.by_handler.items == ()
    assert rates.claims_in_scope == 0


def test_the_handler_pipeline_orders_by_count_then_name_then_id(
    computers: fraud_service._Computers,
) -> None:
    """A total order, and the tie-break is not cosmetic.

    Two handlers with equal referral counts are reachable on any small desk, and
    an unstable order would make two consecutive requests disagree about who is
    second — on a list whose whole subject is the order. The id is behind the name
    because two handlers can share a display name, which is why `benchmarks.py`
    has grouped on the id since Story 5.2.
    """
    referred = {"fraud_flag": True, "fraud_score": 90}
    panel = panel_of(
        [
            claim("WC-1", handler_id=3, handler_name="Zoe", **referred),  # type: ignore[arg-type]
            claim("WC-2", handler_id=3, handler_name="Zoe", **referred),  # type: ignore[arg-type]
            claim("WC-3", handler_id=9, handler_name="Ada", **referred),  # type: ignore[arg-type]
            claim("WC-4", handler_id=2, handler_name="Ada", **referred),  # type: ignore[arg-type]
        ],
        computers,
    )

    assert [
        (row.handler_name, row.handler_id, row.count) for row in panel.siu_by_handler.items
    ] == [
        ("Zoe", 3, 2),
        # Both count one and both are called Ada; the id breaks the tie.
        ("Ada", 2, 1),
        ("Ada", 9, 1),
    ]


def test_a_handler_with_no_referred_claim_is_absent_from_the_pipeline(
    computers: fraud_service._Computers,
) -> None:
    """`_pipeline`'s rule over the handler dimension rather than `_banded`'s.

    A desk carrying no SIU work is not a segment of the SIU pipeline. The
    denominator a reader wants — how many claims that handler holds — is the rate
    breakdown's `claims` column, which is why that column is on the wire.
    """
    panel = panel_of(
        [
            claim("WC-1", fraud_flag=True, fraud_score=90, handler_id=1, handler_name="Ada"),
            claim("WC-2", handler_id=2, handler_name="Bo"),
        ],
        computers,
    )

    assert [row.handler_id for row in panel.siu_by_handler.items] == [1]


# --- the rate breakdowns ------------------------------------------------


def test_a_rate_publishes_the_denominator_it_was_computed_over(
    computers: fraud_service._Computers,
) -> None:
    """The thin-bucket answer, and it is not a suppression rule.

    One flagged claim of one is 100% and so is fifty of fifty. The I/O matrix
    refuses to invent a minimum sample size — that would be this module deciding
    which of an analyst's own claims she may see a rate for — and publishes
    `flagged` and `claims` beside `rateBp` instead, so the reader can see what the
    percentage is a percentage of.
    """
    rates = rates_of(
        [
            claim("WC-1", fraud_flag=True, fraud_score=90, injury_type="Amputation"),
            claim("WC-2", injury_type="Fracture"),
            claim("WC-3", fraud_flag=True, fraud_score=90, injury_type="Fracture"),
        ],
        computers,
        FraudRateSorts(),
    )
    rows = {row.injury_type: row for row in rates.by_injury_type.items}

    assert (rows["Amputation"].flagged, rows["Amputation"].claims) == (1, 1)
    assert rows["Amputation"].rate_bp == 10_000
    assert (rows["Fracture"].flagged, rows["Fracture"].claims) == (1, 2)
    assert rows["Fracture"].rate_bp == 5_000


def test_the_rate_is_the_review_rule_and_never_the_referral_one(
    computers: fraud_service._Computers,
) -> None:
    """The near-miss, on a rate rather than on a count.

    A referral numerator over a review denominator produces a plausible smaller
    percentage on every row and nothing on screen says which rule it counted. The
    claim below is flagged and scores between the two thresholds, so it is in the
    review population and not in the referral one — a table built on `siu_review`
    would report 0% here.
    """
    rates = rates_of(
        [claim("WC-BETWEEN", fraud_flag=True, fraud_score=57, injury_type="Fracture")],
        computers,
        FraudRateSorts(),
    )

    assert rates.by_injury_type.items[0].flagged == 1
    assert rates.by_injury_type.items[0].rate_bp == 10_000
    assert rates.flagged_claims == 1


@pytest.mark.parametrize("sort", sorted(FraudRateSort))
def test_every_sort_is_a_total_order_over_rows_that_tie_on_its_figure(
    computers: fraud_service._Computers, sort: FraudRateSort
) -> None:
    """Each of the five, over a caseload built so its own figure ties.

    Four injury types, all with one claim and none flagged: every row ties on the
    rate, on `flagged` and on `claims` at once, so whichever sort is under test
    falls through to the label. Without an explicit tie-break the order would be
    the dict's, and two consecutive requests could disagree — on a table whose
    only content is an order.

    Parametrised over `sorted(FraudRateSort)` rather than a written-out list, so a
    sixth member cannot be added without arriving here.
    """
    caseload = [
        claim("WC-1", injury_type="Delta"),
        claim("WC-2", injury_type="Bravo"),
        claim("WC-3", injury_type="Alpha"),
        claim("WC-4", injury_type="Charlie"),
    ]
    rates = rates_of(caseload, computers, FraudRateSorts(injury_type=sort))

    assert [row.injury_type for row in rates.by_injury_type.items] == [
        "Alpha",
        "Bravo",
        "Charlie",
        "Delta",
    ]
    assert rates.by_injury_type.sort is sort


def test_each_sort_orders_by_the_figure_it_names(
    computers: fraud_service._Computers,
) -> None:
    """The five orders, over a caseload where all five differ.

    Written as one caseload and five assertions rather than five parametrised
    cases, because the property is that the five produce five *different*
    sequences: a table sorted by `flagged_desc` that happened to agree with
    `rate_desc` would satisfy a per-sort test and prove nothing.
    """
    caseload = [
        # One of one flagged: the highest rate, the fewest claims.
        claim("WC-1", fraud_flag=True, fraud_score=90, injury_type="Alpha"),
        # Two of four flagged: a middling rate on the biggest denominator.
        *[claim(f"WC-2{n}", injury_type="Bravo") for n in range(2)],
        *[
            claim(f"WC-2f{n}", fraud_flag=True, fraud_score=90, injury_type="Bravo")
            for n in range(2)
        ],
        # None of two: the lowest rate.
        *[claim(f"WC-3{n}", injury_type="Charlie") for n in range(2)],
    ]

    def order(sort: FraudRateSort) -> list[str]:
        rates = rates_of(caseload, computers, FraudRateSorts(injury_type=sort))
        return [row.injury_type for row in rates.by_injury_type.items]

    assert order(FraudRateSort.rate_desc) == ["Alpha", "Bravo", "Charlie"]
    assert order(FraudRateSort.rate_asc) == ["Charlie", "Bravo", "Alpha"]
    assert order(FraudRateSort.flagged_desc) == ["Bravo", "Alpha", "Charlie"]
    assert order(FraudRateSort.claims_desc) == ["Bravo", "Charlie", "Alpha"]
    assert order(FraudRateSort.label_asc) == ["Alpha", "Bravo", "Charlie"]


def test_sorting_one_table_leaves_the_other_two_at_their_defaults(
    computers: fraud_service._Computers,
) -> None:
    """Three independent controls, which is why there are three parameters.

    The I/O matrix asks for exactly this: sorting a breakdown changes that
    table's order and nothing else. A single shared `sort` could not express it,
    and a shared *default* would make the property untestable.
    """
    caseload = [
        claim("WC-1", fraud_flag=True, fraud_score=90, employer_id=1, employer_label="Zeta"),
        claim("WC-2", employer_id=2, employer_label="Alpha"),
    ]
    rates = rates_of(caseload, computers, FraudRateSorts(injury_type=FraudRateSort.label_asc))

    assert rates.by_injury_type.sort is FraudRateSort.label_asc
    assert rates.by_employer.sort is FraudRateSort.rate_desc
    assert rates.by_handler.sort is FraudRateSort.rate_desc
    # …and the employer table really is in rate order, not in label order.
    assert [row.label for row in rates.by_employer.items] == ["Zeta", "Alpha"]


def test_the_injury_breakdown_is_cut_and_says_how_far(
    computers: fraud_service._Computers,
) -> None:
    """No truncation is silent — `charts.Distribution`'s contract on a table."""
    caseload = [claim(f"WC-{n}", injury_type=f"Type {n:02d}") for n in range(12)]
    rates = rates_of(caseload, computers, FraudRateSorts())

    assert len(rates.by_injury_type.items) == fraud_service.INJURY_TYPE_LIMIT
    assert rates.by_injury_type.total_categories == 12
    assert rates.by_injury_type.truncated is True
    assert rates.by_injury_type.limit == fraud_service.INJURY_TYPE_LIMIT


def test_a_breakdown_exactly_at_its_limit_is_not_marked_truncated(
    computers: fraud_service._Computers,
) -> None:
    """The boundary, because `>=` here would apologise for a cut that did not
    happen — `charts.py`'s own off-by-one, restated on a table."""
    caseload = [
        claim(f"WC-{n}", injury_type=f"Type {n:02d}")
        for n in range(fraud_service.INJURY_TYPE_LIMIT)
    ]
    rates = rates_of(caseload, computers, FraudRateSorts())

    assert rates.by_injury_type.truncated is False


def test_the_two_uncapped_breakdowns_are_never_cut(
    computers: fraud_service._Computers,
) -> None:
    """Employers and handlers are bounded by the assignment, not by the data.

    A limit would cut a list that is already short and would hide a line of
    business rather than a long tail — `_by_employer`'s recorded argument, applied
    to two dimensions instead of one. `limit: None` rather than a large sentinel:
    "never cut" is a different fact from "cut at a thousand".
    """
    caseload = [
        claim(
            f"WC-{n}",
            employer_id=n,
            employer_label=f"E{n:02d}",
            handler_id=n,
            handler_name=f"H{n:02d}",
        )
        for n in range(20)
    ]
    rates = rates_of(caseload, computers, FraudRateSorts())

    assert len(rates.by_employer.items) == 20
    assert rates.by_employer.limit is None
    assert rates.by_employer.truncated is False
    assert len(rates.by_handler.items) == 20
    assert rates.by_handler.limit is None


def test_the_injury_cut_is_the_injury_charts_cut() -> None:
    """Two views of one dimension must not disagree about where the tail starts.

    `services/worklist/fraud.py` restates the number rather than importing it —
    `rules.CURSOR_PAGE_CEILING`'s arrangement, where the duplication is made safe
    by a test rather than by a comment. This is that test: a supervisor reading
    "8 of 20 injury types" on the portfolio chart and an analyst reading a
    differently-cut list of the same dimension would have no way to reconcile
    them, and neither caption would be wrong.
    """
    assert fraud_service.INJURY_TYPE_LIMIT == charts.INJURY_TYPE_LIMIT


# --- the red-flag frequency fold ----------------------------------------


def _stamp(offset_days: int = 0) -> datetime:
    """A generation time, offset from a fixed instant. Deterministic on purpose."""
    return datetime(2026, 8, 1, 12, 0, tzinfo=UTC) + timedelta(days=offset_days)


def _signals() -> FraudSignalFigures:
    """The structured half of a stored fraud card. Never read by this fold.

    Present because the stored shape requires it, and written out rather than
    faked with zeros so the fixture is a *valid* card — the fold's whole
    tolerance story is about rows that fail re-validation, so every row that is
    supposed to pass has to actually pass.
    """
    return FraudSignalFigures(
        fraud_score=90,
        fraud_flag=True,
        siu_review=True,
        fraud_flagged=True,
        siu_fraud_score_min=SIU_FRAUD_SCORE_MIN,
        fraud_flag_score_min=FRAUD_FLAG_SCORE_MIN,
        thresholds_version=6,
    )


_SUMMARY = "A deterministic test summary, written by this fixture and not by any model."


def red_flag_row(claim_id: str, *clauses: str, stamp: datetime | None = None) -> CachedFraudInsight:
    """A stored `red_flags` card carrying exactly the clauses given.

    **The clauses are the argument.** `tests/insight_fixture.py`'s `FakeChatClient`
    fills every `list[str]` field with one constant string, so a frequency
    ranking built on it collapses to a single row of count N — which is a fixture
    that cannot tell a correct fold from one that counted mentions, grouped on the
    wrong key or ignored the normalisation entirely. Every case below therefore
    names its own clauses, per claim.

    Built through the real Pydantic model and dumped the way
    `agents/insights.py` dumps it (`mode="json"`, snake_case keys), so the fold
    reads exactly the shape the database holds.
    """
    insight = FraudRedFlagsInsight(
        signals=_signals(),
        narrative=FraudRedFlagsNarrative(summary=_SUMMARY, red_flags=list(clauses)),
        prompt_version=1,
    )
    return CachedFraudInsight(
        claim_id=claim_id,
        content=insight.model_dump(mode="json"),
        generated_at=stamp or _stamp(),
        model="fake-chat",
    )


def low_risk_row(claim_id: str, stamp: datetime | None = None) -> CachedFraudInsight:
    """A stored `low_risk` card — the variant with **no `red_flags` key at all**.

    Not "a red-flag card with an empty list": `FraudLowRiskInsight` is a different
    member of the stored union with a different narrative schema, and the
    discriminator was decided by two registered derivations on the server. A row
    of this shape raises the coverage and contributes no clause, which is the
    behaviour that makes the published coverage figure honest.
    """
    insight = FraudLowRiskInsight(
        signals=_signals(),
        narrative=FraudLowRiskNarrative(
            confirmation=_SUMMARY,
            monitoring=["Watch for a change in the treating physician."],
        ),
        prompt_version=1,
    )
    return CachedFraudInsight(
        claim_id=claim_id,
        content=insight.model_dump(mode="json"),
        generated_at=stamp or _stamp(),
        model="fake-chat",
    )


def unreadable_row(claim_id: str) -> CachedFraudInsight:
    """A stored row an older prompt version wrote, whose schema has since moved.

    `{"outcome": "red_flags"}` and nothing else: the discriminator resolves, the
    body does not validate. That is the realistic shape of the failure — a card
    written under a schema that has since gained a field — rather than arbitrary
    JSON, which would exercise the discriminator's own refusal instead of the
    tolerance under test.
    """
    return CachedFraudInsight(
        claim_id=claim_id,
        content={"outcome": "red_flags"},
        generated_at=_stamp(),
        model="fake-chat",
    )


def test_a_cold_cache_answers_an_empty_ranking_and_a_null_range() -> None:
    """The seeded state, and the ordinary one — 200, never an error.

    Nothing has been generated for a claim until a refresh reaches it, so this is
    what the card renders on a freshly reset stack. Two nulls rather than an epoch
    or a "now": a card cannot draw a range that does not exist and must not invent
    one.
    """
    ranked = red_flags_of([], claims_in_scope=100, read_clauses=read_fraud_clauses)

    assert ranked.items == ()
    assert ranked.total_clauses == 0
    assert ranked.truncated is False
    assert ranked.claims_with_insight == 0
    assert ranked.claims_in_scope == 100
    assert ranked.unreadable == 0
    assert ranked.generated_from is None
    assert ranked.generated_to is None
    assert ranked.models == ()


def test_a_low_risk_row_raises_the_coverage_and_contributes_no_clause() -> None:
    """The half of the coverage figure that makes it honest.

    A fold that skipped `low_risk` rows would publish a denominator counting only
    the claims with something to say, which is the one number on this card that
    must not flatter itself: "3 of 4 claims have a fraud narrative, and one of
    them found nothing" is a very different sentence from "3 of 3".
    """
    ranked = red_flags_of(
        [
            red_flag_row("WC-1", "Late reporting of the injury"),
            low_risk_row("WC-2"),
        ],
        claims_in_scope=4,
        read_clauses=read_fraud_clauses,
    )

    assert ranked.claims_with_insight == 2
    assert [row.clause for row in ranked.items] == ["Late reporting of the injury"]
    assert ranked.total_clauses == 1


def test_an_unreadable_row_is_excluded_and_counted_rather_than_raised() -> None:
    """`api/routers/claims.py::_slot`'s tolerance, applied to a portfolio fold.

    One superseded prompt version must not take a whole view down, and it must not
    be invisible either. Excluded from the ranking, from the coverage *and* from
    the range: a row this build cannot read contributes nothing but its count.
    """
    ranked = red_flags_of(
        [
            red_flag_row("WC-1", "Late reporting of the injury", stamp=_stamp(1)),
            unreadable_row("WC-2"),
        ],
        claims_in_scope=2,
        read_clauses=read_fraud_clauses,
    )

    assert ranked.unreadable == 1
    assert ranked.claims_with_insight == 1
    assert ranked.generated_from == _stamp(1)
    assert ranked.generated_to == _stamp(1)


def test_a_claim_naming_one_clause_twice_counts_once() -> None:
    """Distinct claims, never mentions.

    A narrative listing one indicator twice is one claim worrying about one
    thing; counting it twice would make a single model's repetition look like a
    pattern across the book. This is the difference between a frequency view and a
    word count.
    """
    ranked = red_flags_of(
        [red_flag_row("WC-1", "Late reporting of the injury", "Late reporting of the injury.")],
        claims_in_scope=1,
        read_clauses=read_fraud_clauses,
    )

    assert [(row.clause, row.claims) for row in ranked.items] == [
        ("Late reporting of the injury", 1)
    ]


def test_clauses_differing_only_in_case_and_punctuation_are_one_row() -> None:
    """The normalisation, end to end, and the spelling that survives it.

    Three claims, three transcriptions of one clause: a trailing full stop, a
    capital, and a wrapped line's double space. They are one row counting three
    claims, displayed in the **first-seen** spelling — first by claim id, which is
    the order the repository reads in. Displaying the normalised form would put a
    casefolded sentence on screen and make the view look generated by the fold
    rather than by the model.
    """
    ranked = red_flags_of(
        [
            red_flag_row("WC-1", "Late reporting of the injury"),
            red_flag_row("WC-2", "late reporting of the injury."),
            red_flag_row("WC-3", "Late  reporting of the\ninjury"),
        ],
        claims_in_scope=3,
        read_clauses=read_fraud_clauses,
    )

    assert [(row.clause, row.claims) for row in ranked.items] == [
        ("Late reporting of the injury", 3)
    ]


@pytest.mark.parametrize(
    ("written", "normalised"),
    [
        ("Late reporting of the injury.", "late reporting of the injury"),
        ("  Late   reporting\tof the injury  ", "late reporting of the injury"),
        ("LATE REPORTING OF THE INJURY", "late reporting of the injury"),
        # Only *one* trailing stop, and nothing else about the punctuation: an
        # ellipsis is not a full stop the model forgot, and a question mark is a
        # different clause.
        ("Late reporting of the injury..", "late reporting of the injury."),
        ("Late reporting of the injury?", "late reporting of the injury?"),
    ],
)
def test_the_normalisation_folds_transcription_and_nothing_else(
    written: str, normalised: str
) -> None:
    """Three folds, and the fourth case is what is deliberately *not* folded.

    Whitespace, a trailing stop and case are differences in transcription. What
    the rule must never do is make two differently *worded* clauses equal — no
    stemming, no stop-word removal, no synonym table — because each of those is
    the server deciding two sentences mean the same thing, which is a
    classification with an owner and a version that this fold has neither of
    (AD-2).
    """
    assert normalise_clause(written) == normalised


def test_the_ranking_is_count_descending_then_clause_ascending() -> None:
    """`_ranked`'s ordering, and the tie-break is on the **normalised** form.

    Two clauses tied at one claim each, whose spellings differ only in case:
    ordering on the display form would leave the sequence to an accident of
    capitalisation, and two requests over one book could disagree.
    """
    ranked = red_flags_of(
        [
            red_flag_row("WC-1", "Zebra crossing incident", "banana delivery dispute"),
            red_flag_row("WC-2", "Zebra crossing incident"),
        ],
        claims_in_scope=2,
        read_clauses=read_fraud_clauses,
    )

    assert [(row.clause, row.claims) for row in ranked.items] == [
        ("Zebra crossing incident", 2),
        ("banana delivery dispute", 1),
    ]


def test_the_ranking_is_cut_and_says_so() -> None:
    """The cut is published with `truncated` and `totalClauses`, never silent."""
    limit = fraud_service.RED_FLAG_LIMIT
    rows = [red_flag_row(f"WC-{n:02d}", f"Indicator number {n:02d}") for n in range(limit + 3)]
    ranked = red_flags_of(rows, claims_in_scope=limit + 3, read_clauses=read_fraud_clauses)

    assert len(ranked.items) == limit
    assert ranked.total_clauses == limit + 3
    assert ranked.truncated is True
    assert ranked.limit == limit


def test_the_range_is_the_oldest_and_newest_generation_not_the_first_and_last_row() -> None:
    """The read is ordered by claim id, not by generation time.

    `rows[0].generated_at` would be the first *claim*, which is only the oldest
    generation by coincidence — and on a portfolio refreshed claim by claim it
    usually is not.
    """
    ranked = red_flags_of(
        [
            red_flag_row("WC-1", "Indicator one", stamp=_stamp(5)),
            red_flag_row("WC-2", "Indicator two", stamp=_stamp(1)),
            red_flag_row("WC-3", "Indicator three", stamp=_stamp(3)),
        ],
        claims_in_scope=3,
        read_clauses=read_fraud_clauses,
    )

    assert ranked.generated_from == _stamp(1)
    assert ranked.generated_to == _stamp(5)
    assert ranked.models == ("fake-chat",)


# --- the DB-backed half --------------------------------------------------
#
# `requires_db` per test rather than a module-level `pytestmark`,
# `test_portfolio_charts.py`'s convention and its reason: everything above this
# line is pure and runs in the lint job, and a module-wide skip would silently
# take the three-rule separation — the one thing in this file the seed cannot
# demonstrate — out of every run without a Postgres.


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
    `test_portfolio_charts.py`'s helper and its reason: David Bline has no rows in
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


# --- the gate (AC 2) -----------------------------------------------------


def test_the_allowlist_is_exactly_the_analyst() -> None:
    """The set *is* the access decision, asserted once.

    A new constant beside `benchmarks.PERMITTED_ROLES` rather than a widening of
    it: that one is the oversight capability two roles share, and reaching across
    both with one list would mean a change to either surface's access silently
    moved the other's.
    """
    assert frozenset({UserRole.analyst}) == fraud_service.FRAUD_ANALYTICS_ROLES


@pytest.mark.parametrize("role", sorted(set(UserRole) - {UserRole.analyst}))
def test_the_gate_is_an_allowlist_so_every_other_role_is_refused(role: UserRole) -> None:
    """Enumerated from the enum, so a role added later fails here rather than
    inheriting the analyst workspace.

    Parametrising over `set(UserRole) - {analyst}` rather than over a written-out
    list is what makes that true: `UserRole.system` already exists, and a denylist
    spelled `role is UserRole.supervisor` would have admitted it on the day it was
    declared with nothing to notice.
    """
    assert role not in fraud_service.FRAUD_ANALYTICS_ROLES
    with pytest.raises(FraudAnalyticsNotPermitted):
        fraud_service.require_fraud_analytics_access(
            CallerContext(user_id=1, role=role, employer_ids=frozenset({1}))
        )


@requires_db
@pytest.mark.parametrize("path", [PANEL, RATES, RED_FLAGS])
@pytest.mark.parametrize(("name", "role"), [SUPERVISOR, SCOPED_SUPERVISOR, HANDLER])
async def test_a_supervisor_and_a_handler_are_both_refused(
    seeded_db_url: str, path: str, name: str, role: str
) -> None:
    """**The supervisor's refusal is the story**, not a side effect of it.

    Epic 5 shipped an analyst who read the supervisor's dashboard byte for byte;
    `test_portfolio_charts.py::test_the_analyst_reads_byte_identically_to_the_
    supervisor` is still true of every endpoint it was written about. These three
    routes are the first thing in this console that the analyst has and the
    supervisor does not, and the inversion is asserted here rather than left to be
    inferred from an allowlist.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, name, role)
        resp = await client.get(path)

    assert resp.status_code == 403
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
    is an oracle about the assignment table. Both repositories this section reads
    through are replaced with functions that fail if they are reached at all.
    """

    async def forbidden(*_args: object, **_kwargs: object) -> Sequence[sa.Row[Any]]:
        raise AssertionError("a scoped read ran before the role gate")

    monkeypatch.setattr(claim_repo, "select_drill_rows", forbidden)
    monkeypatch.setattr(insight_repo, "select_fraud_insights", forbidden)
    ctx = CallerContext(user_id=1, role=role, employer_ids=frozenset({1}))
    thresholds = await thresholds_for(db)

    with pytest.raises(FraudAnalyticsNotPermitted):
        await fraud_panel(db, ctx, thresholds)
    with pytest.raises(FraudAnalyticsNotPermitted):
        await fraud_rates(db, ctx, thresholds, FraudRateSorts())
    with pytest.raises(FraudAnalyticsNotPermitted):
        await fraud_red_flags(db, ctx, read_fraud_clauses)


@requires_db
@pytest.mark.parametrize("path", [PANEL, RATES, RED_FLAGS])
async def test_a_refused_caller_loads_no_rule_document_either(
    seeded_db_url: str, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """A refusal precedes every read, not only the claim read.

    Two of these routes load `derivation_thresholds` before calling the aggregate,
    so a gate that lived only in the service would answer 403 *after* a
    `rule_document` query had already run. `rules.parameters.load` is the single
    door it goes through; replacing it with a function that fails is the whole
    assertion. (The third route loads nothing at all, and this run proves that
    too.)
    """

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a rule document was loaded before the role gate answered")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *SUPERVISOR)
        monkeypatch.setattr(rule_parameters, "load", forbidden)
        resp = await client.get(path)

    assert resp.status_code == 403
    assert resp.json()["type"] == "/problems/fraud-analytics-not-permitted"


@requires_db
async def test_every_epic_five_dashboard_route_still_answers_a_supervisor(
    seeded_db_url: str,
) -> None:
    """The other half of AC 2: nothing this story did narrowed Epic 5.

    A gate added to a shared router is exactly the change that closes a
    neighbouring route by accident — a decorator on the wrong function, a
    dependency added to `APIRouter`. Asserted by reading all five, as the
    supervisor, and expecting 200.
    """
    for path in (SUMMARY, "/dashboard/handler-benchmarks", "/dashboard/charts", DRILL):
        async with make_client(seeded_db_url) as client:
            await login_as(client, *SUPERVISOR)
            assert (await client.get(path)).status_code == 200, path


# --- reconciliation (AC 1) -----------------------------------------------


@requires_db
async def test_the_flagged_population_is_the_kpi_cards_and_the_drill_lists(
    seeded_db_url: str, db: AsyncSession
) -> None:
    """The story's whole promise, asserted between three live surfaces.

    Not "the flagged count is 13". `/dashboard/summary`'s `fraudFlagged`,
    `/dashboard/claims?filter[fraudFlagged]=true`'s `total` and this panel's
    `flaggedClaims` are read on one scope in one test and compared — so a retune
    of the review threshold moves all three or fails here, and no restated number
    can go stale.

    The seed oracle sits beside them, because an equality between three surfaces
    still passes if all three are wrong together. It restates the rule from the
    seed file and shares nothing with the implementation.
    """
    panel = await get_json(seeded_db_url, *ANALYST, PANEL)
    cards = await get_json(seeded_db_url, *SUPERVISOR, SUMMARY)
    drill = await get_json(
        seeded_db_url, *SUPERVISOR, DRILL, params={"filter[fraudFlagged]": "true"}
    )
    oracle = seed_fixture.expected_fraud_panel(*ANALYST)

    assert panel["flaggedClaims"] == cards["fraudFlagged"]
    assert panel["flaggedClaims"] == drill["total"]
    assert panel["flaggedClaims"] == oracle["flaggedClaims"]
    assert panel["fraudFlagScoreMin"] == cards["fraudScoreMin"]
    del db  # the fixture is required for the seeded schema, not read here


@requires_db
async def test_the_panel_and_the_pipeline_are_the_seeds_own_figures(
    seeded_db_url: str,
) -> None:
    """The whole payload against the independent oracle, object for object.

    Compared as whole series rather than figure by figure, `expectTable`'s
    discipline: a payload that rendered every band in the wrong order would
    satisfy a per-band lookup.
    """
    payload = await get_json(seeded_db_url, *ANALYST, PANEL)
    expected = seed_fixture.expected_fraud_panel(*ANALYST)

    assert payload["byBand"] == expected["byBand"]
    assert payload["siuByStage"] == expected["siuByStage"]
    assert payload["siuByHandler"] == expected["siuByHandler"]
    assert payload["claimsInScope"] == expected["claimsInScope"]
    assert payload["siuClaims"] == expected["siuClaims"]


@requires_db
async def test_the_rate_breakdowns_are_the_seeds_own_figures(seeded_db_url: str) -> None:
    """The three tables against the oracle, in the order the server chose."""
    payload = await get_json(seeded_db_url, *ANALYST, RATES)
    expected = seed_fixture.expected_fraud_rates(*ANALYST)

    assert [
        (row["injuryType"], row["flagged"], row["claims"], row["rateBp"])
        for row in payload["byInjuryType"]["items"]
    ] == [
        (row["label"], row["flagged"], row["claims"], row["rateBp"])
        for row in expected["byInjuryType"]["items"]
    ]
    assert [
        (row["employerId"], row["label"], row["flagged"], row["claims"], row["rateBp"])
        for row in payload["byEmployer"]["items"]
    ] == [
        (row["key"], row["label"], row["flagged"], row["claims"], row["rateBp"])
        for row in expected["byEmployer"]["items"]
    ]
    assert [
        (row["handlerId"], row["handlerName"], row["flagged"], row["claims"], row["rateBp"])
        for row in payload["byHandler"]["items"]
    ] == [
        (row["key"], row["label"], row["flagged"], row["claims"], row["rateBp"])
        for row in expected["byHandler"]["items"]
    ]
    assert payload["claimsInScope"] == expected["claimsInScope"]
    assert payload["flaggedClaims"] == expected["flaggedClaims"]


@requires_db
async def test_the_claims_column_sums_to_the_book_on_the_uncapped_tables(
    seeded_db_url: str,
) -> None:
    """Every claim is in exactly one bucket of each uncapped dimension.

    A partition, asserted rather than assumed: a fold that dropped a claim with a
    null-ish key would still produce a plausible table, and the `claims` column is
    the denominator every rate on the screen is read against.
    """
    payload = await get_json(seeded_db_url, *ANALYST, RATES)

    for table in ("byEmployer", "byHandler"):
        assert sum(row["claims"] for row in payload[table]["items"]) == payload["claimsInScope"]
        assert sum(row["flagged"] for row in payload[table]["items"]) == payload["flaggedClaims"]


@requires_db
@pytest.mark.parametrize("band", sorted(FraudBand))
async def test_a_band_segment_opens_exactly_the_claims_it_counted(
    seeded_db_url: str, band: FraudBand
) -> None:
    """AC 4's reconciliation, per band, between two live aggregates.

    This is the new facet earning its place: `filter[fraudBand]` is matched
    through the same registered derivation the segment was counted with, so the
    list a click opens holds the claims the segment counted. Asserted for every
    member, so a band with nothing behind it is covered too — the drill answers an
    empty list rather than the unfiltered book.
    """
    panel = await get_json(seeded_db_url, *ANALYST, PANEL)
    counted = {item["key"]: item["count"] for item in panel["byBand"]["items"]}[band.value]
    drill = await get_json(seeded_db_url, *ANALYST, DRILL, params={"filter[fraudBand]": band.value})

    assert drill["total"] == counted
    assert drill["appliedFilters"] == [{"key": "fraudBand", "value": band.value, "display": None}]


@requires_db
async def test_the_pipeline_drills_on_both_facets_at_once(seeded_db_url: str) -> None:
    """A pipeline segment is two narrowings, and the list is their intersection.

    `filter[siuReview]=true` plus `filter[stage]=…`, which is what a click on a
    stage of the pipeline sends. Asserted against the panel's own segment counts
    rather than against a constant, so the two move together.
    """
    panel = await get_json(seeded_db_url, *ANALYST, PANEL)

    for segment in panel["siuByStage"]["items"]:
        drill = await get_json(
            seeded_db_url,
            *ANALYST,
            DRILL,
            params={"filter[siuReview]": "true", "filter[stage]": segment["key"]},
        )
        assert drill["total"] == segment["count"], segment
        assert [chip["key"] for chip in drill["appliedFilters"]] == ["stage", "siuReview"]


@requires_db
async def test_the_siu_facet_is_narrower_than_the_fraud_flagged_one(
    seeded_db_url: str,
) -> None:
    """The 13-against-9 gap, as a property of the two facets rather than of a seed.

    Both lists are read and the *sets* compared, so what is asserted is that the
    referral population is a strict subset of the review one — which is the
    relationship the two rules have on this document and the one a collapsed
    implementation would flatten.
    """
    referral = await get_json(seeded_db_url, *ANALYST, DRILL, params={"filter[siuReview]": "true"})
    review = await get_json(seeded_db_url, *ANALYST, DRILL, params={"filter[fraudFlagged]": "true"})
    referral_ids = {row["claimId"] for row in referral["items"]}
    review_ids = {row["claimId"] for row in review["items"]}

    assert referral_ids < review_ids, "the referral rule must be strictly narrower"
    assert referral["total"] == (await get_json(seeded_db_url, *ANALYST, PANEL))["siuClaims"]


# --- scope (AD-7) --------------------------------------------------------


@requires_db
async def test_every_claim_behind_a_scoped_analysts_panel_is_inside_that_book(
    db: AsyncSession,
) -> None:
    """AD-7, asserted where an analyst can actually be scoped.

    **There is exactly one analyst in the seed and they are `scope_all`**, so no
    HTTP request in this codebase can demonstrate a *narrowed* analyst. Seeding a
    scoped one would move persona counts in Stories 1.3, 1.4 and 5.1 and in the
    e2e login fixture, which is a change this story does not own — so the
    assertion is made at the service level, where `CallerContext` is an argument,
    and the gap is recorded in `deferred-work.md`.

    The employers are Jennifer Park's, because her book is a real subset with a
    known shape rather than an arbitrary pair of ids.
    """
    scoped_employers = {
        seed_fixture.employer_id_of(name) for name in seed_fixture.employers_of(*SCOPED_SUPERVISOR)
    }
    ctx = CallerContext(user_id=1, role=UserRole.analyst, employer_ids=frozenset(scoped_employers))
    thresholds = await thresholds_for(db)

    panel = await fraud_panel(db, ctx, thresholds)
    rates = await fraud_rates(db, ctx, thresholds, FraudRateSorts())
    expected = len(seed_fixture.claims_for(*SCOPED_SUPERVISOR))

    assert panel.claims_in_scope == expected
    assert rates.claims_in_scope == expected
    # Every employer the rate table names is one of hers, and every handler on
    # the pipeline holds one of her claims.
    assert {row.employer_id for row in rates.by_employer.items} <= scoped_employers
    assert panel.by_band.total == expected


@requires_db
async def test_a_scoped_analysts_red_flag_coverage_counts_only_her_book(
    db: AsyncSession,
) -> None:
    """The denominator is scoped too, which is the half a second read could lose.

    `claimsInScope` comes from `count_claims_matching`, a different statement from
    the insight read, so "both halves of the coverage figure are scoped" is a
    property of two queries rather than of one — and therefore worth asserting.
    """
    scoped_employers = {
        seed_fixture.employer_id_of(name) for name in seed_fixture.employers_of(*SCOPED_SUPERVISOR)
    }
    ctx = CallerContext(user_id=1, role=UserRole.analyst, employer_ids=frozenset(scoped_employers))

    ranked = await fraud_red_flags(db, ctx, read_fraud_clauses)

    assert ranked.claims_in_scope == len(seed_fixture.claims_for(*SCOPED_SUPERVISOR))
    # The freshly reset stack has no fraud narratives at all — the cold-cache
    # state the card renders its empty state for.
    assert ranked.claims_with_insight == 0


@requires_db
async def test_an_analyst_with_no_employers_reads_an_empty_book_not_the_portfolio(
    db: AsyncSession,
) -> None:
    """`employer_scope` reads "assigned to nobody" as a predicate matching nothing.

    The distinction is the whole of AD-7's mechanism, and it is the one that fails
    open if anybody ever writes `if not ctx.employer_ids: skip the filter`.
    """
    ctx = CallerContext(user_id=0, role=UserRole.analyst, employer_ids=frozenset())

    panel = await fraud_panel(db, ctx, await thresholds_for(db))

    assert panel.claims_in_scope == 0
    assert [item.count for item in panel.by_band.items] == [0, 0, 0]
    # The thresholds still arrive — an empty book says nothing about the rules.
    assert panel.fraud_band_high_min == (await thresholds_for(db)).fraud_band_high_min


# --- the contract --------------------------------------------------------


@requires_db
@pytest.mark.parametrize("path", [PANEL, RED_FLAGS])
async def test_the_parameterless_routes_declare_no_parameters_at_all(
    seeded_db_url: str, path: str
) -> None:
    """AD-7 structurally: the contract itself offers nowhere to put a scope.

    Asserted against the published OpenAPI document rather than the function
    signature, because the contract is what a client (and a reviewer) reads.
    """
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"][path]["get"]

    assert operation.get("parameters", []) == []
    assert "requestBody" not in operation


@requires_db
async def test_the_rates_route_declares_exactly_three_parameters_and_all_are_sorts(
    seeded_db_url: str,
) -> None:
    """An allowlist of exactly three names rather than an assertion of emptiness.

    `/dashboard/priority-claims` set the precedent when it grew a cursor: a test
    called "no parameters at all" that passes on a route with three is a sentence
    a reader would have to disbelieve. Every one of the three is a `sort`, so
    there is still nowhere to put an employer, a user or an "as".
    """
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"][RATES]["get"]
    names = {parameter["name"] for parameter in operation["parameters"]}

    assert names == {"sort[injuryType]", "sort[employer]", "sort[handler]"}
    assert "requestBody" not in operation
    # …and each is closed, so an unknown value is a 422 from the contract rather
    # than a branch in the service.
    for parameter in operation["parameters"]:
        schema_node = parameter["schema"]
        enum = schema_node.get("enum") or schema_node.get("allOf", [{}])[0].get("enum")
        if enum is None:
            enum = schema["components"]["schemas"]["FraudRateSort"]["enum"]
        assert set(enum) == {sort.value for sort in FraudRateSort}


@requires_db
async def test_an_unknown_sort_value_is_a_validation_error(seeded_db_url: str) -> None:
    """The type *is* the check — refused by the contract, before the service."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *ANALYST)
        resp = await client.get(RATES, params={"sort[injuryType]": "severity"})

    assert resp.status_code == 422
    assert resp.json()["type"] == "/problems/validation-error"


@requires_db
@pytest.mark.parametrize("path", [PANEL, RATES, RED_FLAGS])
async def test_query_parameters_cannot_widen_or_change_the_scope(
    seeded_db_url: str, path: str
) -> None:
    """The smuggling attempt: ask as the analyst, name a narrower book anyway.

    Unknown parameters are ignored rather than rejected (FastAPI's default, and
    the safer direction — a 422 here would tell an attacker which parameter names
    exist). What matters is that the answer is byte-identical.
    """
    honest = await get_json(seeded_db_url, *ANALYST, path)

    for smuggled in (
        {"employerId": "3"},
        {"employerIds": "1,2,3"},
        {"scopeAll": "true"},
        {"limit": "20"},
        {"handlerId": "4"},
    ):
        attempt = await get_json(seeded_db_url, *ANALYST, path, params=smuggled)
        assert attempt == honest, f"{smuggled} changed the answer to {path}"


@requires_db
async def test_the_responses_are_camel_case_and_carry_nothing_else(
    seeded_db_url: str,
) -> None:
    """The exact key set on all three payloads, so nothing is added unnoticed."""
    panel = await get_json(seeded_db_url, *ANALYST, PANEL)
    rates = await get_json(seeded_db_url, *ANALYST, RATES)
    flags = await get_json(seeded_db_url, *ANALYST, RED_FLAGS)

    assert set(panel) == {
        "byBand",
        "siuByStage",
        "siuByHandler",
        "claimsInScope",
        "flaggedClaims",
        "siuClaims",
        "fraudBandHighMin",
        "fraudBandMedMin",
        "fraudFlagScoreMin",
        "siuFraudScoreMin",
        "rulesVersion",
    }
    assert set(panel["byBand"]) == {"items", "total", "totalCategories", "truncated", "limit"}
    assert set(panel["byBand"]["items"][0]) == {"key", "count"}
    assert set(panel["siuByHandler"]["items"][0]) == {"handlerId", "handlerName", "count"}

    assert set(rates) == {
        "byInjuryType",
        "byEmployer",
        "byHandler",
        "claimsInScope",
        "flaggedClaims",
        "fraudFlagScoreMin",
        "rulesVersion",
    }
    assert set(rates["byInjuryType"]) == {
        "items",
        "totalCategories",
        "truncated",
        "limit",
        "sort",
    }
    assert set(rates["byInjuryType"]["items"][0]) == {
        "injuryType",
        "flagged",
        "claims",
        "rateBp",
    }
    assert set(rates["byEmployer"]["items"][0]) == {
        "employerId",
        "label",
        "flagged",
        "claims",
        "rateBp",
    }
    assert set(rates["byHandler"]["items"][0]) == {
        "handlerId",
        "handlerName",
        "flagged",
        "claims",
        "rateBp",
    }

    assert set(flags) == {
        "items",
        "totalClauses",
        "truncated",
        "limit",
        "claimsWithInsight",
        "claimsInScope",
        "unreadable",
        "generatedFrom",
        "generatedTo",
        "models",
    }
    # **No `rulesVersion`** on the red-flag payload, and the absence is the
    # assertion: this view reaches no rule, so there is no version to name and
    # publishing one would claim a provenance the figures do not have.
    assert "rulesVersion" not in flags


@requires_db
@pytest.mark.parametrize("path", [PANEL, RATES, RED_FLAGS])
async def test_the_response_is_never_cached(seeded_db_url: str, path: str) -> None:
    """One persona's portfolio must not be served to another from upstream."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *ANALYST)
        resp = await client.get(path)

    assert resp.headers["cache-control"] == "no-store"


@requires_db
@pytest.mark.parametrize("path", [PANEL, RATES, RED_FLAGS])
async def test_the_endpoint_requires_a_session(seeded_db_url: str, path: str) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.get(path)

    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


@requires_db
async def test_reading_the_workspace_writes_no_audit_event(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """A query is not a mutation, and this story writes nothing at all (AD-12).

    Counted before and after rather than asserted absent, because other rows
    exist: what must not happen is these three requests adding one. It covers the
    red-flag route in particular, which reads `services/rag`'s table — a surface
    that regenerated a narrative on read would show up here as an audit row.
    """
    count = sa.select(sa.func.count()).select_from(AuditEvent)
    before = (await db.execute(count)).scalar_one()

    for path in (PANEL, RATES, RED_FLAGS):
        await get_json(seeded_db_url, *ANALYST, path)

    await db.commit()  # a new snapshot, or the count would be the old one
    assert (await db.execute(count)).scalar_one() == before


@requires_db
async def test_reading_the_red_flag_view_writes_no_insight_row(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """AD-12 on the one table this story reads and does not own.

    `services/rag` is the sole writer of `ai_insight`, and nothing on this path
    triggers a refresh: a cold cache answers 200 with an empty ranking rather than
    generating one. Counted, because "the card renders its empty state" and "the
    card quietly filled the cache" look identical from the outside.
    """
    count = sa.select(sa.func.count()).select_from(sa.table("ai_insight"))
    before = (await db.execute(count)).scalar_one()

    await get_json(seeded_db_url, *ANALYST, RED_FLAGS)

    await db.commit()
    assert (await db.execute(count)).scalar_one() == before


def _counting(db: AsyncSession, executed: list[str]) -> Any:
    """Wrap `db.execute` so the statements one aggregate runs can be counted.

    `test_portfolio_charts.py`'s helper, extracted here because three aggregates
    need it. Rule-document loads are *not* excluded and do not need to be: every
    parameter block arrives as an argument, so a fold that reached the engine
    would show up as an extra statement, which is exactly what these tests are
    for.
    """
    original = db.execute

    async def counting(statement: Any, *args: Any, **kwargs: Any) -> Any:
        executed.append(str(statement).split("\n")[0])
        return await original(statement, *args, **kwargs)

    return original, counting


@requires_db
async def test_the_panel_takes_exactly_one_scoped_read(db: AsyncSession) -> None:
    """ "One scoped read, one pure fold" as a counted fact rather than a claim."""
    ctx = await context_for(db, *ANALYST)
    thresholds = await thresholds_for(db)
    executed: list[str] = []
    original, counting = _counting(db, executed)

    db.execute = counting  # type: ignore[method-assign]
    try:
        await fraud_panel(db, ctx, thresholds)
    finally:
        db.execute = original  # type: ignore[method-assign]

    assert len(executed) == 1, executed
    assert executed[0].startswith("SELECT"), executed


@requires_db
async def test_the_rates_take_exactly_one_scoped_read_whatever_the_sort(
    db: AsyncSession,
) -> None:
    """A sort is a total order applied after a read that did not change.

    The count is what makes that true rather than merely plausible: an
    implementation that pushed the ordering into SQL would take one read per
    table, and would then have to re-derive the flagged rule there.
    """
    ctx = await context_for(db, *ANALYST)
    thresholds = await thresholds_for(db)
    executed: list[str] = []
    original, counting = _counting(db, executed)

    db.execute = counting  # type: ignore[method-assign]
    try:
        await fraud_rates(
            db,
            ctx,
            thresholds,
            FraudRateSorts(
                injury_type=FraudRateSort.label_asc,
                employer=FraudRateSort.claims_desc,
                handler=FraudRateSort.flagged_desc,
            ),
        )
    finally:
        db.execute = original  # type: ignore[method-assign]

    assert len(executed) == 1, executed


@requires_db
async def test_the_red_flag_aggregate_takes_exactly_two_scoped_reads(
    db: AsyncSession,
) -> None:
    """**Two, and the second one is the denominator** — the module's one departure.

    Every other aggregate on this dashboard takes exactly one scoped read, and
    this one does not, so the number is pinned rather than left to grow quietly.
    The published coverage is "claims with a fraud narrative, out of claims in
    scope", and the second half cannot come from the insight join: a claim with no
    `ai_insight` row produces no row to count. An outer join from `claim` would
    return one row per claim in the portfolio to answer a question about a handful
    of them; a count is one aggregate statement.
    """
    ctx = await context_for(db, *ANALYST)
    executed: list[str] = []
    original, counting = _counting(db, executed)

    db.execute = counting  # type: ignore[method-assign]
    try:
        await fraud_red_flags(db, ctx, read_fraud_clauses)
    finally:
        db.execute = original  # type: ignore[method-assign]

    assert len(executed) == 2, executed
    assert all(statement.startswith("SELECT") for statement in executed), executed


# --- the rules are the document's (AD-8) --------------------------------


@requires_db
async def test_the_published_thresholds_come_from_the_rule_document(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """Compared against the JDM resolution path, never against a literal.

    All four, and the pair that reads the same integer is the point: the band's
    high edge and the review threshold arrive from two different parameters, so a
    document that moved one has to move exactly one number on this payload.
    """
    thresholds = await thresholds_for(db)
    panel = await get_json(seeded_db_url, *ANALYST, PANEL)

    assert panel["fraudBandHighMin"] == thresholds.fraud_band_high_min
    assert panel["fraudBandMedMin"] == thresholds.fraud_band_med_min
    assert panel["fraudFlagScoreMin"] == thresholds.fraud_flag_score_min
    assert panel["siuFraudScoreMin"] == thresholds.siu_fraud_score_min
    assert panel["rulesVersion"] == thresholds.version


def _retuned(content: dict[str, Any], **changes: int) -> dict[str, Any]:
    """A copy of a JDM document with some expression values replaced.

    `test_portfolio_charts.py`'s helper, restated here rather than imported: a
    test module importing another test module's private helper is how two
    unrelated suites acquire a shared failure mode.
    """
    copy: dict[str, Any] = json.loads(json.dumps(content))
    for node in copy["nodes"]:
        for expression in node.get("content", {}).get("expressions", []):
            if expression["key"] in changes:
                expression["value"] = str(changes[expression["key"]])
    return copy


@requires_db
async def test_a_superseded_rule_document_moves_the_bands(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """AD-8, end to end, on the parameter that is this story's whole subject.

    A v7 lowering `fraudBandHighMin` is inserted effective today; the same request
    comes back with a larger `high` segment *and* a lower published edge, so the
    legend's caption follows the document. Nothing is deployed, nothing is
    restarted and no Python changes.

    **And `flaggedClaims` must not move**, which is the other half of the
    assertion and the reason this test exists on this story rather than being
    inherited from 5.3: the band edge and the review threshold carry the same
    number in the seeded document, so an implementation that had collapsed them
    would move both here and would look, in every other test, exactly correct.

    The new edge is 40 rather than something lower because it has to stay above
    `fraudBandMedMin` — an inverted pair is refused at load (`rules/parameters.py`),
    which is itself the point: a retune that made `medium` unreachable would be a
    500 at the boundary rather than a silently two-segment chart. On the seeded
    portfolio 29 claims score at or above 40 against 13 at or above 55, so the
    high band genuinely widens.
    """
    lowered = 40
    before = await get_json(seeded_db_url, *ANALYST, PANEL)

    content = (
        await db.execute(
            sa.text(
                "SELECT content FROM rule_document WHERE key = :key ORDER BY version DESC LIMIT 1"
            ),
            {"key": DERIVATION_THRESHOLDS_KEY},
        )
    ).scalar_one()
    await db.execute(
        sa.text(
            "INSERT INTO rule_document (key, version, effective_from, content, created_at) "
            "VALUES (:key, 7, :today, CAST(:content AS jsonb), now())"
        ),
        {
            "key": DERIVATION_THRESHOLDS_KEY,
            # `utc_today()`, not `date.today()`: the loader filters
            # `effective_from <= as_of` against a UTC-derived today, so a local
            # date east of UTC near midnight runs a day ahead, the document is not
            # yet effective, and this fails as if the loader were broken.
            "today": utc_today(),
            "content": json.dumps(_retuned(content, fraudBandHighMin=lowered)),
        },
    )
    await db.commit()

    try:
        after = await get_json(seeded_db_url, *ANALYST, PANEL)
        expected = sum(1 for row in seed_fixture.seed()["claims"] if row["fraud_score"] >= lowered)
        high_before = {item["key"]: item["count"] for item in before["byBand"]["items"]}["high"]
        high_after = {item["key"]: item["count"] for item in after["byBand"]["items"]}["high"]

        assert expected > high_before, "the retune must actually widen the band"
        assert high_after == expected
        assert after["fraudBandHighMin"] == lowered
        assert after["rulesVersion"] == 7
        # One parameter changed, and the two populations that read a *different*
        # rule are exactly where they were — which is the assertion an
        # implementation that had collapsed the band edge into the review
        # threshold would fail, and the only test in this suite that could catch
        # it, because the two numbers are equal in the seeded document.
        assert after["flaggedClaims"] == before["flaggedClaims"]
        assert after["fraudFlagScoreMin"] == before["fraudFlagScoreMin"]
        assert after["siuClaims"] == before["siuClaims"]
    finally:
        await db.execute(
            sa.text("DELETE FROM rule_document WHERE key = :key AND version = 7"),
            {"key": DERIVATION_THRESHOLDS_KEY},
        )
        await db.commit()


@requires_db
async def test_the_red_flag_view_reads_the_cache_it_is_given(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """The one DB-backed red-flag test, and it writes its own rows.

    The seeded stack has no fraud narratives — that is the cold-cache state the
    card renders its empty state for — so the only way to exercise the read path
    end to end is to insert two. Written directly rather than through
    `services/rag`, because what is under test is the *read*: a fixture that went
    through the generator would be testing Epic 6's command and would need a chat
    client to do it.

    Rolled back afterwards so the rest of the module sees the cold cache.
    """
    claims = (await db.execute(sa.select(Claim.id, Claim.claim_id).limit(2))).all()
    stored = {
        row.claim_id: red_flag_row(row.claim_id, "Late reporting of the injury").content
        for row in claims
    }
    for row in claims:
        await db.execute(
            sa.text(
                "INSERT INTO ai_insight (claim_id, kind, content, model, generated_at) "
                "VALUES (:claim, CAST(:kind AS insight_kind), CAST(:content AS jsonb), "
                ":model, now())"
            ),
            {
                "claim": row.id,
                "kind": InsightKind.fraud_risk_indicators.value,
                "content": json.dumps(stored[row.claim_id]),
                "model": "fake-chat",
            },
        )
    await db.commit()

    try:
        payload = await get_json(seeded_db_url, *ANALYST, RED_FLAGS)

        assert payload["claimsWithInsight"] == 2
        assert payload["claimsInScope"] == len(seed_fixture.claims_for(*ANALYST))
        assert payload["unreadable"] == 0
        assert payload["models"] == ["fake-chat"]
        assert [(item["clause"], item["claims"]) for item in payload["items"]] == [
            ("Late reporting of the injury", 2)
        ]
        assert payload["generatedFrom"] is not None
        assert payload["generatedTo"] is not None
    finally:
        await db.execute(sa.text("DELETE FROM ai_insight"))
        await db.commit()


@requires_db
async def test_the_red_flag_view_is_cold_on_a_freshly_reset_stack(
    seeded_db_url: str,
) -> None:
    """The seeded state, through the route.

    Named as its own test rather than folded into the coverage one because it is
    the state the e2e spec asserts against and the state a reviewer opening the
    console will see: an empty ranking, a null range, and a coverage figure whose
    denominator is the whole book.
    """
    payload = await get_json(seeded_db_url, *ANALYST, RED_FLAGS)

    assert payload["items"] == []
    assert payload["claimsWithInsight"] == 0
    assert payload["generatedFrom"] is None
    assert payload["generatedTo"] is None
    assert payload["claimsInScope"] == len(seed_fixture.claims_for(*ANALYST))


# --- the drill contract Story 5.5 owns and this story extends -------------


@requires_db
async def test_the_two_new_facets_join_the_vocabulary_and_change_no_other(
    seeded_db_url: str,
) -> None:
    """Adding facets is sanctioned; altering one is a contract change 5.5 owns.

    Every pre-existing facet is asserted to answer what it answered before, by
    reading each one on its own and comparing against the seed oracle — which is
    the same oracle `test_drill_through.py` uses, so a change in either direction
    fails in both suites.
    """
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()
    names = {parameter["name"] for parameter in schema["paths"][DRILL]["get"]["parameters"]}

    assert "filter[fraudBand]" in names
    assert "filter[siuReview]" in names
    assert names == {
        "filter[stage]",
        "filter[severityBand]",
        "filter[fraudFlagged]",
        "filter[litigation]",
        "filter[surgery]",
        "filter[oshaRecordable]",
        "filter[recoveryStatus]",
        "filter[injuryType]",
        "filter[state]",
        "filter[employerId]",
        "filter[handlerId]",
        "filter[priority]",
        "filter[fraudBand]",
        "filter[siuReview]",
        "cursor",
    }

    unchanged: list[tuple[str, str, dict[str, Any]]] = [
        ("filter[fraudFlagged]", "true", {"fraud_flagged": True}),
        ("filter[severityBand]", "high", {"severity_band": "high"}),
        ("filter[stage]", "settled", {"stage": "settled"}),
    ]
    for facet, value, oracle_filters in unchanged:
        payload = await get_json(seeded_db_url, *SUPERVISOR, DRILL, params={facet: value})
        oracle = seed_fixture.expected_drill_claims(*SUPERVISOR, **oracle_filters)
        assert payload["total"] == oracle["total"], facet


@requires_db
async def test_the_new_facets_are_carried_in_the_cursor(seeded_db_url: str) -> None:
    """Both join the compared filter payload automatically, by being fields.

    `FILTER_KEYS` is read off `DrillFilters`, so a facet added to that dataclass
    is in the cursor's payload without anybody writing a line — which is the
    property the assertion checks: a cursor minted under `filter[fraudBand]=high`
    is refused when replayed unfiltered.
    """
    page = await get_json(
        seeded_db_url,
        *ANALYST,
        DRILL,
        params={"filter[fraudBand]": FraudBand.low.value},
    )
    cursor = page["nextCursor"]
    if cursor is None:
        pytest.skip("the low band fits on one page in this seed")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *ANALYST)
        resp = await client.get(DRILL, params={"cursor": cursor})

    assert resp.status_code == 400
    assert resp.json()["type"] == "/problems/invalid-cursor"


@requires_db
async def test_a_scoped_persona_cannot_reach_another_book_through_a_new_facet(
    seeded_db_url: str,
) -> None:
    """The facets narrow inside the scope and can never widen it (AD-7).

    Jennifer Park asks for the high band and gets her own high-band claims —
    never the portfolio's — which is asserted against the oracle for *her* book
    rather than against a smaller number.
    """
    payload = await get_json(
        seeded_db_url,
        *SCOPED_SUPERVISOR,
        DRILL,
        params={"filter[fraudBand]": FraudBand.high.value},
    )
    oracle = seed_fixture.expected_drill_claims(*SCOPED_SUPERVISOR, fraud_band="high")

    assert payload["total"] == oracle["total"]
    assert {row["claimId"] for row in payload["items"]} <= seed_fixture.expected_claim_ids(
        *SCOPED_SUPERVISOR
    )


def test_the_wire_names_of_the_two_new_facets_are_the_query_parameter_names() -> None:
    """The property `drill/filters.ts` rests on, asserted on the server side.

    A chip publishes its `key` so clicking its ✕ can remove *that* parameter from
    the URL, which only works while the key the server publishes and the parameter
    name it accepts are one string.
    """
    from services.worklist.drill_through import WIRE_KEYS

    assert WIRE_KEYS["fraud_band"] == "fraudBand"
    assert WIRE_KEYS["siu_review"] == "siuReview"


def test_the_drill_filters_order_puts_the_new_facets_last() -> None:
    """The chip order is `DrillFilters`' field order, so position is a contract.

    Inserting `fraud_band` beside `fraud_flagged` would have re-ordered the chips
    on every existing drill-through URL — a change to a shipped surface for a
    cosmetic reason. Asserted rather than commented, because the field list is
    exactly the kind of thing a later reader tidies.
    """
    from services.worklist.drill_through import FILTER_KEYS

    assert FILTER_KEYS[-2:] == ("fraud_band", "siu_review")


def test_the_new_facet_values_survive_a_cursor_round_trip() -> None:
    """A forged or replayed cursor is coerced back through the field's own type.

    `{"fraud_band": "banana"}` must be a refusal and not a facet that matches
    nothing — which is what `_COERCE` is for, and what would be missing if a
    fourteenth facet were added to the dataclass and forgotten in that table.
    """
    from services.worklist.drill_through import (
        Cursor,
        DrillFilters,
        InvalidCursor,
        decode_cursor,
        encode_cursor,
    )

    filters = DrillFilters(fraud_band=FraudBand.high, siu_review=True)
    token = encode_cursor(
        Cursor(
            offset=0,
            limit=50,
            filters=filters,
            weights_version=1,
            thresholds_version=6,
            as_of=utc_today(),
        )
    )

    assert decode_cursor(token).filters == filters

    forged = encode_cursor(
        Cursor(
            offset=0,
            limit=50,
            filters=DrillFilters(),
            weights_version=1,
            thresholds_version=6,
            as_of=utc_today(),
        )
    )
    # …and a hand-edited payload naming a band nobody declared is refused rather
    # than silently matching nothing.
    import base64

    decoded = json.loads(base64.urlsafe_b64decode(forged + "=" * (-len(forged) % 4)))
    decoded["f"] = {"fraud_band": "banana"}
    tampered = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode().rstrip("=")
    with pytest.raises(InvalidCursor):
        decode_cursor(tampered)


def test_the_service_holds_no_fraud_cut_off_of_its_own() -> None:
    """AD-8 as a grep over the two files this story wrote.

    `tests/test_derivations.py::test_no_module_outside_the_registry_hardcodes_the_
    band` scans the whole tree for the band numbers and is the general guard; this
    is the narrow one the spec's Verification section names, and it is worth
    having separately because it names the *files* rather than the numbers. A
    reviewer who wants to know whether this story put a threshold in a service can
    read one assertion.
    """
    import re
    from pathlib import Path

    server_root = Path(__file__).resolve().parents[1]
    pattern = re.compile(r"(?<![\w.])(35|55|60)(?![\w.])")
    for relative in ("services/worklist/fraud.py", "api/routers/dashboard.py"):
        source = (server_root / relative).read_text()
        assert not pattern.search(source), f"{relative} names a fraud cut-off directly"
