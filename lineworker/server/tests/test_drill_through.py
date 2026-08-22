"""Story 5.5 — the dashboard drill-through, and what makes it honest.

Two halves, `test_priority_claims.py`'s arrangement.

The first is database-free: the twelve predicates over synthetic projections,
and the cursor codec. One test per facet, each turning on exactly one fact, so
the delta *is* the predicate under test.

The second is the endpoint, and its centre of gravity is the **reconciliation
set**. This story's whole promise is that the list a number opens holds the
claims that number counted, and the way that promise fails is not by returning
the wrong claims — it is by returning a *plausible* set: the nine SIU claims
behind a card that counted thirteen fraud-flagged ones, the fifty-four
`settled_closed` claims behind a donut slice that folded sixty-two by stage.
Both of those pairs exist in this codebase as recorded near-misses.

So the reconciliation tests do not compare against constants. They read the KPI
endpoint (or the charts endpoint) and the drill endpoint **in the same test, on
the same scope**, and assert the two numbers are equal. A constant would pass
while both surfaces were wrong together; an equality between two live aggregates
cannot. The independent seed oracle is asserted beside it, so the pair also
fails if the two agree on a wrong answer.
"""

import base64
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import fields, replace
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from api.routers.claims import ClaimCardResponse
from api.routers.dashboard import DrillClaimRowResponse
from config import Settings
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, AuditEvent
from data.models.enums import (
    ClaimStatus,
    Disability,
    Gender,
    RecoveryWindow,
    ReturnStatus,
    Stage,
)
from data.repositories.identity import employer_ids_for
from rules.parameters import (
    DerivationThresholds,
    PriorityWeights,
    thresholds_for,
    weights_for,
)
from services.derivations import RiskBand
from services.worklist import priority
from services.worklist.drill_through import (
    FILTER_KEYS,
    WIRE_KEYS,
    Cursor,
    DrillClaim,
    DrillFilters,
    InvalidCursor,
    decode_cursor,
    drill_through_claims,
    encode_cursor,
    select,
)
from tests import seed_fixture
from tests.conftest import requires_db

DRILL = "/dashboard/claims"
SUMMARY = "/dashboard/summary"
CHARTS = "/dashboard/charts"
BENCHMARKS = "/dashboard/handler-benchmarks"
PRIORITY = "/dashboard/priority-claims"

BLINE = ("David Bline", "supervisor")
PARK = ("Jennifer Park", "supervisor")
ANALYST = ("David Bline", "analyst")
HANDLER = ("Sarah Williams", "handler")

#: The fourteen wire keys of one row, named rather than read off a response.
#:
#: They are `ClaimCardResponse`'s, and that is asserted separately against the
#: model itself — this constant exists so a column silently disappearing from
#: the payload fails a comparison rather than shrinking one.
ROW_KEYS = {
    "claimId",
    "daysOpen",
    "risk",
    "workerName",
    "injuryType",
    "stage",
    "employerShortName",
    "fraudFlag",
    "litigationFlag",
    "paymentDue",
    "siuReview",
    "rtwBlocked",
    "priorityScore",
    "priorityMarker",
}

#: The twenty-six parameters the route may declare, and no others.
#:
#: Derived from `WIRE_KEYS` rather than written out, which makes it an allowlist
#: about *shape* — what may appear beside the facets — rather than about the
#: vocabulary. The vocabulary is pinned separately and by name in
#: `test_fraud_analytics.py::test_the_two_new_facets_join_the_vocabulary_and_
#: change_no_other`, which spells all twenty-four out, and the **count** is
#: asserted below so a facet cannot be added to `DrillFilters` and reach the
#: route without a number in a test moving.
PARAMETER_NAMES = {f"filter[{wire}]" for wire in WIRE_KEYS.values()} | {"cursor"}

#: How many facets the route publishes. Its own literal, so "twenty-five facets
#: and a cursor" is a sentence a reader can check rather than a set comparison
#: that would pass at any size.
#:
#: Story 7.4 moved it from 24 to 25 — `filter[reserveVerdict]`, appended — which
#: is the change this constant exists to require rather than one it was surprised
#: by. It is also the one facet that changes what the route *costs*: the verdict
#: is not a column, so setting it loads each claim's schedule and bills and the
#: `reserve_bands` document. `test_financial_decomposition.py` counts those
#: reads, in both directions, so the shipped one-scoped-read guarantee stays true
#: of every other drill URL.
FACET_COUNT = 25

TODAY = date(2026, 8, 18)

#: The date a synthetic drill claim is filed and injured on unless a test says
#: otherwise. Its own constant rather than `TODAY`, so a range test that brackets
#: it reads as a statement about the *claim* rather than about the clock — the
#: two facets are a comparison between two stored dates and never involve today
#: at all.
QUIET_DAY = date(2026, 5, 15)

#: The two rule blocks the pure half needs, restated rather than loaded —
#: `test_priority_claims.py`'s discipline and its reason: an expectation
#: computed with the block the code under test read agrees with it however wrong
#: both are.
SEEDED_WEIGHTS = PriorityWeights(
    version=1,
    litigation=40,
    siu_review=35,
    rtw_blocked=30,
    pending_approval=25,
    pending_approval_statuses=frozenset({ClaimStatus.initial, ClaimStatus.ch_assessment_process}),
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

SEEDED_THRESHOLDS = DerivationThresholds(
    version=7,
    risk_high_min=65,
    risk_med_min=35,
    siu_fraud_score_min=60,
    rtw_blocked_hash_modulus=5,
    payment_due_hash_modulus=3,
    treatment_early_max_ratio=0.3,
    treatment_active_max_ratio=0.7,
    recovery_year_expected_days=180,
    recovery_default_expected_days=42,
    path_minor_severity_max=35,
    path_minor_recovery_windows=frozenset({RecoveryWindow.weeks_0_2}),
    path_fatality_severity_min=100,
    ptd_severity_threshold=85,
    fraud_flag_score_min=55,
    # Story 7.1's two, added in version 6 — the edges of the fraud-score BAND the
    # analyst workspace distributes on. A third rule over the fraud columns, not a
    # re-spelling of either above it: both of those are conjoined with
    # `fraud_flag`, and this pair bands the score alone. `fraud_band_high_min`
    # carries the same integer as `fraud_flag_score_min` today, which is exactly
    # why the oracle restates it separately rather than reusing the name.
    fraud_band_high_min=55,
    fraud_band_med_min=35,
    # Story 7.3's three, added in version 7 — the edges of the AGE band the
    # analyst workspace segments by. Restated separately from every number above
    # them although two of the three coincide: `age_younger_min` is 35 like
    # `risk_med_min` and `fraud_band_med_min`, and `age_oldest_min` is 55 like
    # `fraud_flag_score_min` and `fraud_band_high_min`. Three columns, three
    # rules, one integer twice — and an oracle that shared a name between any of
    # them could not notice the day a document moved one.
    age_younger_min=35,
    age_older_min=45,
    age_oldest_min=55,
)


# --- the pure half: `select` over synthetic caseloads --------------------


def drill_claim(
    claim_id: str = "WC-0001",
    *,
    stage: Stage = Stage.intake,
    status: ClaimStatus = ClaimStatus.ch_approved,
    return_status: ReturnStatus = ReturnStatus.returned_and_fully_recovered,
    severity: int = 10,
    days_open: int = 0,
    injury_type: str = "Laceration",
    fraud_flag: bool = False,
    fraud_score: int = 0,
    litigation_flag: bool = False,
    surgery_required: bool = False,
    employer_id: int = 1,
    handler_id: int = 1,
    state: str = "WA",
    osha_recordable: bool = False,
    froi_date: date = QUIET_DAY,
    doi: date = QUIET_DAY,
    disability: Disability = Disability.temporary,
    sector: str = "Aerospace",
    region: str = "Midwest",
    icd: str = "S61.219A",
    age: int = 30,
    gender: Gender = Gender.male,
    reserve: int = 0,
    recovery: RecoveryWindow = RecoveryWindow.weeks_4_6,
) -> DrillClaim:
    """One synthetic projection row, with every field defaulted to *quiet*.

    Keyword-only and fully defaulted so a test can name the one or two facts it
    is actually about — `priority_claim`'s shape in `test_priority_claims.py`,
    and its reason. The defaults are chosen so the baseline claim matches
    **none** of the twelve facets' interesting values: intake stage, a low
    severity, no fraud, no litigation, no surgery, no OSHA record. Every facet
    test therefore turns exactly one thing on.

    Story 7.2's four are defaulted the same way, with one difference worth
    naming: a date facet has no "off" value the way a boolean does, so the two
    dates default to `QUIET_DAY` and a range test names bounds either side of it
    rather than turning a flag on. The two categorical ones default to the
    commonest seeded value, so a test that narrows to the *other* one is
    narrowing rather than confirming.

    Story 7.3's four follow the same rule, and `age` is the one with a
    deliberate value: 30 is below `age_younger_min`, so the baseline claim is in
    the `youngest` band and a case that names any other band is narrowing away
    from it. `region` and `icd` take the commonest seeded values and `gender` the
    commonest enum member, for 7.2's reason.
    """
    return DrillClaim(
        claim_id=claim_id,
        stage=stage,
        status=status,
        return_status=return_status,
        severity_score=severity,
        days_open=days_open,
        injury_type=injury_type,
        worker_name="Dana Reyes",
        employer_short_name="3M",
        surgery_required=surgery_required,
        litigation_flag=litigation_flag,
        fraud_flag=fraud_flag,
        fraud_score=fraud_score,
        employer_id=employer_id,
        handler_id=handler_id,
        handler_name="Sarah Williams",
        state=state,
        osha_recordable=osha_recordable,
        froi_date=froi_date,
        doi=doi,
        disability=disability,
        sector=sector,
        region=region,
        icd=icd,
        age=age,
        gender=gender,
        reserve=reserve,
        recovery=recovery,
    )


def selected_ids(caseload: Sequence[DrillClaim], **filters: Any) -> list[str]:
    """`select` over the seeded blocks, reduced to the surviving ids in order."""
    ranked, total = select(caseload, DrillFilters(**filters), SEEDED_THRESHOLDS, SEEDED_WEIGHTS)
    assert total == len(ranked), "total is the length of the ranked list, uncapped"
    return [entry.claim.claim_id for entry in ranked]


def test_no_filter_selects_the_whole_caseload() -> None:
    """An unset facet narrows nothing — the Total Claims card's list."""
    caseload = [drill_claim("WC-0001"), drill_claim("WC-0002", stage=Stage.settled)]

    assert sorted(selected_ids(caseload)) == ["WC-0001", "WC-0002"]


def test_the_stage_facet_reads_the_stage_column_and_not_the_status() -> None:
    """Story 5.1's ruling, and the trap this facet is most exposed to.

    A claim can sit in the `settled` **stage** without carrying the
    `settled_closed` **status**; the seeded book has eight of them, which is the
    gap between the donut's 62 and the status rule's 54. The endpoint test below
    pins the same rule against both live numbers.
    """
    caseload = [
        drill_claim("WC-0001", stage=Stage.settled, status=ClaimStatus.settled),
        drill_claim("WC-0002", stage=Stage.settled, status=ClaimStatus.settled_closed),
        drill_claim("WC-0003", stage=Stage.treatment, status=ClaimStatus.settled_closed),
    ]

    assert sorted(selected_ids(caseload, stage=Stage.settled)) == ["WC-0001", "WC-0002"]


def test_the_severity_facet_goes_through_the_registered_risk_band() -> None:
    """`derivations.risk`, the same computer the High Risk card counts with.

    Written against the *band* rather than against a score, which is the point:
    this module holds no boundary, so the only way the facet can be wrong is for
    the registry to be wrong — and then the card is wrong with it.
    """
    caseload = [
        drill_claim("WC-0001", severity=SEEDED_THRESHOLDS.risk_high_min),
        drill_claim("WC-0002", severity=SEEDED_THRESHOLDS.risk_high_min - 1),
        drill_claim("WC-0003", severity=0),
    ]

    assert selected_ids(caseload, severity_band=RiskBand.high) == ["WC-0001"]
    assert selected_ids(caseload, severity_band=RiskBand.low) == ["WC-0003"]


def test_the_fraud_facet_is_the_review_rule_and_not_the_siu_referral_one() -> None:
    """13 seeded claims against 9 — the single most plausible way to get this wrong.

    A claim flagged at a score between the two cut-offs is counted by the
    dashboard's Fraud Flags card and is *not* referred for SIU review. It has to
    be in this facet's answer, or the list is shorter than the card that opened
    it.
    """
    between = SEEDED_THRESHOLDS.fraud_flag_score_min
    assert between < SEEDED_THRESHOLDS.siu_fraud_score_min, "this test needs the gap"
    caseload = [
        drill_claim("WC-0001", fraud_flag=True, fraud_score=between),
        drill_claim("WC-0002", fraud_flag=True, fraud_score=between - 1),
        drill_claim("WC-0003", fraud_flag=False, fraud_score=100),
    ]

    assert selected_ids(caseload, fraud_flagged=True) == ["WC-0001"]


def test_the_litigation_facet_reads_the_stored_flag() -> None:
    caseload = [drill_claim("WC-0001", litigation_flag=True), drill_claim("WC-0002")]

    assert selected_ids(caseload, litigation=True) == ["WC-0001"]
    assert selected_ids(caseload, litigation=False) == ["WC-0002"]


def test_the_surgery_facet_reads_the_stored_flag() -> None:
    caseload = [drill_claim("WC-0001", surgery_required=True), drill_claim("WC-0002")]

    assert selected_ids(caseload, surgery=True) == ["WC-0001"]


def test_the_osha_facet_reads_the_stored_flag() -> None:
    caseload = [drill_claim("WC-0001", osha_recordable=True), drill_claim("WC-0002")]

    assert selected_ids(caseload, osha_recordable=True) == ["WC-0001"]


def test_the_recovery_facet_reads_the_return_status_column() -> None:
    caseload = [
        drill_claim("WC-0001", return_status=ReturnStatus.under_treatment),
        drill_claim("WC-0002", return_status=ReturnStatus.returned_and_under_therapy),
    ]

    assert selected_ids(caseload, recovery_status=ReturnStatus.under_treatment) == ["WC-0001"]


def test_the_injury_type_facet_matches_the_exact_stored_string() -> None:
    """No trim, no case-fold, no merge — `charts.LabelCount`'s ruling.

    Canonicalizing free text is a data-quality decision with an owner. A facet
    that folded case would return claims the bar it was clicked from never
    counted, which is the same failure as the fraud cut-off in the other
    direction: a list longer than its number.
    """
    caseload = [
        drill_claim("WC-0001", injury_type="Amputation"),
        drill_claim("WC-0002", injury_type="amputation"),
        drill_claim("WC-0003", injury_type=" Amputation"),
    ]

    assert selected_ids(caseload, injury_type="Amputation") == ["WC-0001"]


def test_the_state_facet_matches_the_exact_stored_string() -> None:
    caseload = [drill_claim("WC-0001", state="WA"), drill_claim("WC-0002", state="wa")]

    assert selected_ids(caseload, state="WA") == ["WC-0001"]


def test_the_employer_facet_matches_the_id_and_never_the_label() -> None:
    """Two employers sharing a short name are two employers.

    `EmployerPaid.employer_id` exists for this, and its own docstring names this
    story: the label is a `short_name`, and a name is not an identity.
    """
    caseload = [
        drill_claim("WC-0001", employer_id=7),
        drill_claim("WC-0002", employer_id=8),
    ]

    assert selected_ids(caseload, employer_id=7) == ["WC-0001"]


def test_the_handler_facet_matches_the_id_and_never_the_name() -> None:
    caseload = [drill_claim("WC-0001", handler_id=3), drill_claim("WC-0002", handler_id=4)]

    assert selected_ids(caseload, handler_id=3) == ["WC-0001"]


def test_the_priority_facet_is_the_worklists_own_population_predicate() -> None:
    """Imported, never restated — the three arms and their union.

    A claim in two arms is selected once, and a claim in only the third is
    selected at all. Restating the union here would be checking the drill
    against a fourth copy of a rule that already has one.
    """
    caseload = [
        drill_claim("WC-0001", stage=Stage.treatment),
        drill_claim("WC-0002", litigation_flag=True),
        drill_claim("WC-0003", fraud_flag=True, fraud_score=SEEDED_THRESHOLDS.fraud_flag_score_min),
        drill_claim("WC-0004", stage=Stage.treatment, litigation_flag=True),
        drill_claim("WC-0005"),
    ]

    selected = selected_ids(caseload, priority=True)

    assert sorted(selected) == ["WC-0001", "WC-0002", "WC-0003", "WC-0004"]
    assert len(selected) == len(set(selected)), "a claim in two arms appears once"
    assert selected_ids(caseload, priority=False) == ["WC-0005"]


def test_two_facets_intersect_rather_than_widen() -> None:
    """`filter[…]` brackets mean independent dimensions, ANDed.

    A caller who narrows High Risk to one employer is asking for the
    intersection; a union would hand back more claims than either surface
    counted, which is the opposite of what a drill-through is for.
    """
    caseload = [
        drill_claim("WC-0001", severity=SEEDED_THRESHOLDS.risk_high_min, employer_id=7),
        drill_claim("WC-0002", severity=SEEDED_THRESHOLDS.risk_high_min, employer_id=8),
        drill_claim("WC-0003", employer_id=7),
    ]

    assert selected_ids(caseload, severity_band=RiskBand.high, employer_id=7) == ["WC-0001"]


def test_an_unsatisfiable_combination_is_an_empty_page_and_not_a_refusal() -> None:
    """`DrillFilters` refuses nothing — see its docstring.

    A well-formed question whose answer is "none" is a legitimate empty page.
    Refusing it would mean this class knowing which combinations the *data* can
    satisfy, which is a fact about a seed rather than about a contract.
    """
    caseload = [drill_claim("WC-0001", stage=Stage.settled)]

    assert selected_ids(caseload, stage=Stage.settled, priority=True) == []


def test_the_order_is_the_queues_ordering_over_the_same_claims() -> None:
    """`priority.order_key` over `priority.priority_score`, imported.

    Asserted against the two symbols rather than against a copied sequence, so
    this stays an equality between two call sites rather than a transliteration
    of the scorer into a fourth language.
    """
    caseload = [
        drill_claim("WC-0003", severity=90, litigation_flag=True),
        drill_claim("WC-0001", severity=90, litigation_flag=True),
        drill_claim("WC-0002", severity=20),
    ]
    ranked, _total = select(caseload, DrillFilters(), SEEDED_THRESHOLDS, SEEDED_WEIGHTS)

    scored = [
        (
            priority.QueueClaim(
                claim_id=entry.claim.claim_id,
                stage=entry.claim.stage,
                status=entry.claim.status,
                severity_score=entry.claim.severity_score,
                days_open=entry.claim.days_open,
                injury_type=entry.claim.injury_type,
                worker_name=entry.claim.worker_name,
                employer_short_name=entry.claim.employer_short_name,
                surgery_required=entry.claim.surgery_required,
                litigation_flag=entry.claim.litigation_flag,
                fraud_flag=entry.claim.fraud_flag,
            ),
            entry.priority_score,
        )
        for entry in ranked
    ]
    assert [claim.claim_id for claim, _score in sorted(scored, key=priority.order_key)] == [
        entry.claim.claim_id for entry in ranked
    ]
    # …and the tie-break is ascending business id, which is what a cursor into
    # this list depends on absolutely.
    assert [entry.claim.claim_id for entry in ranked][:2] == ["WC-0001", "WC-0003"]


def test_every_facet_has_a_predicate_and_every_predicate_has_a_facet() -> None:
    """The import-time assertion, restated as a test a reader finds by name.

    A facet declared on `DrillFilters` with no predicate would be accepted by
    the route, published as a chip and narrow nothing — a list that says it is
    filtered and is not.
    """
    assert set(FILTER_KEYS) == {field.name for field in fields(DrillFilters)}
    assert set(WIRE_KEYS) == set(FILTER_KEYS)
    assert len(FILTER_KEYS) == FACET_COUNT
    for key in FILTER_KEYS:
        assert selected_ids([], **{key: None}) == []


# --- the cursor, with no database ---------------------------------------


def _cursor(**overrides: Any) -> Cursor:
    base = Cursor(
        offset=50,
        limit=50,
        filters=DrillFilters(severity_band=RiskBand.high),
        weights_version=1,
        thresholds_version=5,
        as_of=TODAY,
    )
    return replace(base, **overrides)


def test_a_cursor_round_trips_with_its_filter_set() -> None:
    original = _cursor()

    assert decode_cursor(encode_cursor(original), TODAY) == original


def test_a_cursor_with_no_filters_round_trips() -> None:
    original = _cursor(filters=DrillFilters())

    assert decode_cursor(encode_cursor(original), TODAY) == original


def _forged(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("not base64 at all!!", id="not-base64"),
        pytest.param(
            base64.urlsafe_b64encode(b'{"hello":1}').decode().rstrip("="), id="not-a-cursor"
        ),
        pytest.param(
            _forged({"o": -1, "l": 50, "f": {}, "v": 1, "t": 5, "d": TODAY.isoformat()}),
            id="negative-offset",
        ),
        pytest.param(
            _forged({"o": 0, "l": 10_000, "f": {}, "v": 1, "t": 5, "d": TODAY.isoformat()}),
            id="page-size-past-the-ceiling",
        ),
        pytest.param(
            _forged(
                {
                    "o": 0,
                    "l": 50,
                    "f": {},
                    "v": 1,
                    "t": 5,
                    "d": (TODAY + timedelta(days=1)).isoformat(),
                }
            ),
            id="dated-in-the-future",
        ),
        pytest.param(
            _forged(
                {
                    "o": 0,
                    "l": 50,
                    "f": {},
                    "v": 1,
                    "t": 5,
                    "d": (TODAY - timedelta(days=8)).isoformat(),
                }
            ),
            id="older-than-the-maximum-age",
        ),
        pytest.param(
            _forged({"o": 0, "l": 50, "f": {"banana": True}, "v": 1, "t": 5, "d": "2026-08-18"}),
            id="filter-that-does-not-exist",
        ),
        pytest.param(
            _forged({"o": 0, "l": 50, "f": {"stage": "banana"}, "v": 1, "t": 5, "d": "2026-08-18"}),
            id="enum-value-that-does-not-exist",
        ),
        pytest.param(
            _forged(
                {"o": 0, "l": 50, "f": {"employer_id": "3"}, "v": 1, "t": 5, "d": "2026-08-18"}
            ),
            id="id-smuggled-as-a-string",
        ),
        pytest.param(
            _forged(
                {"o": 0, "l": 50, "f": {"litigation": "false"}, "v": 1, "t": 5, "d": "2026-08-18"}
            ),
            id="boolean-smuggled-as-a-string",
        ),
        pytest.param(
            _forged({"o": 0, "l": 50, "f": [], "v": 1, "t": 5, "d": "2026-08-18"}),
            id="filter-set-that-is-not-an-object",
        ),
    ],
)
def test_a_cursor_that_does_not_describe_this_list_is_refused(raw: str) -> None:
    """Never a silent fallback to page one — see `decode_cursor`.

    The two string-smuggling cases are the ones a hand-edited cursor produces:
    `"false"` is truthy and `"3"` never equals an integer column, so a decoder
    that trusted JSON's types would flip a facet's meaning or filter on nothing
    at all, in both cases decoding cleanly.
    """
    with pytest.raises(InvalidCursor):
        decode_cursor(raw, TODAY)


def test_a_cursor_carrying_an_infinite_offset_is_refused_rather_than_crashing() -> None:
    """`json` accepts the literal `Infinity` and `int(float("inf"))` raises
    `OverflowError`, which is not a `ValueError` — the defect that escaped the
    queue as a 500.
    """
    forged = (
        base64.urlsafe_b64encode(b'{"o":Infinity,"l":50,"f":{},"v":1,"t":5,"d":"2026-08-18"}')
        .decode()
        .rstrip("=")
    )

    with pytest.raises(InvalidCursor):
        decode_cursor(forged, TODAY)


# --- plumbing -----------------------------------------------------------


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
    `test_priority_claims.py`'s reason: David Bline has *no* rows in
    `user_employer_assignment`, so reading his assignments would hand the
    aggregate an empty book and every equality below would hold trivially.
    """
    user = (
        await db.scalars(sa.select(AppUser).where(AppUser.name == name, AppUser.role == role))
    ).one()
    return CallerContext(
        user_id=user.id,
        role=user.role,
        employer_ids=ALL_EMPLOYERS if user.scope_all else await employer_ids_for(db, user.id),
    )


def query_of(**filters: Any) -> dict[str, str]:
    """Snake_case facet names and Python values, as the route's URL spells them.

    Booleans become `true`/`false` — the query string's spelling, not Python's
    `True` — so a test can pass the same dict to this helper and to the seed
    oracle and know the two are asking one question.
    """
    return {
        f"filter[{WIRE_KEYS[key]}]": ("true" if value else "false")
        if isinstance(value, bool)
        else str(value)
        for key, value in filters.items()
    }


async def drill_for(db_url: str, name: str, role: str, **kwargs: Any) -> dict[str, Any]:
    async with make_client(db_url) as client:
        await login_as(client, name, role)
        resp = await client.get(DRILL, **kwargs)
        assert resp.status_code == 200, resp.text
        payload: dict[str, Any] = resp.json()
        return payload


async def walk(
    db_url: str, name: str, role: str, params: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Every page of one persona's drill list, followed to exhaustion.

    Returns the pages rather than the concatenated rows, because two of the
    properties under test — `total` stable across pages, `nextCursor` null
    exactly once — are about the pages and not about the claims.
    """
    async with make_client(db_url) as client:
        await login_as(client, name, role)
        pages: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            query = dict(params or {})
            if cursor is not None:
                query["cursor"] = cursor
            resp = await client.get(DRILL, params=query)
            assert resp.status_code == 200, resp.text
            page: dict[str, Any] = resp.json()
            pages.append(page)
            cursor = page["nextCursor"]
            if cursor is None:
                return pages
            assert len(pages) < 20, "the walk is not terminating"


def ids_of(pages: Sequence[dict[str, Any]]) -> list[str]:
    return [row["claimId"] for page in pages for row in page["items"]]


# --- the list, against the seed (AC 1) ----------------------------------


@requires_db
@pytest.mark.parametrize(("name", "role"), [BLINE, PARK, ANALYST, HANDLER])
async def test_the_unfiltered_list_covers_exactly_the_personas_book(
    seeded_db_url: str, name: str, role: str
) -> None:
    """Every persona's whole book, walked to exhaustion, against the seed.

    Unfiltered is the Total Claims card's list, and it is also the property that
    makes every filtered assertion below meaningful: a drill-through that
    silently capped or dropped rows would still pass a one-page check.
    """
    expected = seed_fixture.expected_drill_claims(name, role)
    pages = await walk(seeded_db_url, name, role)

    assert ids_of(pages) == expected["claimIds"]
    assert [page["total"] for page in pages] == [expected["total"]] * len(pages)


@requires_db
async def test_the_full_portfolio_walk_is_two_pages_with_no_page_three(
    seeded_db_url: str,
) -> None:
    """The cursor, exercised end to end by the seeded book rather than by a mock.

    A hundred claims at the published page size is two pages, so the walk is a
    real one: `total` is stable, the rows are distinct, and the second page's
    `nextCursor` is null rather than pointing past the end.
    """
    pages = await walk(seeded_db_url, *BLINE)
    rows = ids_of(pages)

    assert len(pages) == 2
    assert len(rows) == len(set(rows)) == 100
    assert [len(page["items"]) for page in pages] == [50, 50]
    assert pages[0]["nextCursor"] is not None
    assert pages[1]["nextCursor"] is None


@requires_db
async def test_a_scoped_supervisors_book_fits_one_page_with_no_cursor(
    seeded_db_url: str,
) -> None:
    payload = await drill_for(seeded_db_url, *PARK)
    expected = seed_fixture.expected_drill_claims(*PARK)

    assert payload["total"] == expected["total"] == 27
    assert payload["nextCursor"] is None
    assert [row["claimId"] for row in payload["items"]] == expected["claimIds"]


# --- the reconciliation set (AC 1) --------------------------------------
#
# Read from the live endpoints on the same scope in the same test, never from
# constants: a constant would pass while both surfaces were wrong together.


@requires_db
@pytest.mark.parametrize(
    ("card", "filters"),
    [
        pytest.param("highRisk", {"severity_band": "high"}, id="high-risk"),
        pytest.param("fraudFlagged", {"fraud_flagged": True}, id="fraud-flags"),
        pytest.param("litigation", {"litigation": True}, id="litigation"),
        pytest.param("surgeryRequired", {"surgery": True}, id="surgery"),
        pytest.param("oshaRecordable", {"osha_recordable": True}, id="osha-recordable"),
        pytest.param("underTreatment", {"stage": "treatment"}, id="under-treatment"),
        pytest.param("settledClosed", {"stage": "settled"}, id="settled-closed"),
        pytest.param("totalClaims", {}, id="total-claims"),
    ],
)
@pytest.mark.parametrize(("name", "role"), [BLINE, PARK])
async def test_each_kpi_cards_drill_through_counts_what_the_card_counted(
    seeded_db_url: str, card: str, filters: dict[str, Any], name: str, role: str
) -> None:
    """AC 1 — the story's whole promise, as an equality between two aggregates.

    Both numbers come off live endpoints on one scope, so the assertion cannot
    be satisfied by a drill-through that agrees with a *stale* constant, and it
    fails if either surface moves without the other. The independent seed oracle
    is asserted beside them so the pair also fails if the two agree wrongly.

    Parametrized over both a full-portfolio and a scoped persona, because a
    predicate applied to the wrong *set* would still reconcile on the portfolio
    where the scope predicate is a tautology.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, name, role)
        summary = (await client.get(SUMMARY)).json()
        drill = (await client.get(DRILL, params=query_of(**filters))).json()

    oracle = seed_fixture.expected_drill_claims(name, role, **filters)
    assert drill["total"] == summary[card]
    assert drill["total"] == oracle["total"]


@requires_db
async def test_the_settled_drill_is_the_stage_and_never_the_status(
    seeded_db_url: str,
) -> None:
    """The near-miss, pinned as a difference rather than as an agreement.

    62 seeded claims sit in the settled *stage* and 54 carry the
    `settled_closed` **status**. A drill-through that read the status would
    still equal a plausible number, so the assertion that matters is that the
    two are *not* the same and that this endpoint answers the first.
    """
    by_status = sum(
        1 for claim in seed_fixture.claims_for(*BLINE) if claim["status"] == "settled_closed"
    )
    payload = await drill_for(seeded_db_url, *BLINE, params=query_of(stage="settled"))

    assert payload["total"] == 62
    assert by_status == 54
    assert payload["total"] != by_status


@requires_db
async def test_the_fraud_drill_is_the_review_rule_and_never_the_siu_one(
    seeded_db_url: str,
) -> None:
    """13 against 9, the other recorded near-miss.

    The queue's SIU filter is read live rather than restated, so this compares
    two endpoints rather than an endpoint and a number.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *BLINE)
        drill = (await client.get(DRILL, params=query_of(fraud_flagged=True))).json()
        queue = (await client.get("/claims/queue", params={"filter": "siu", "limit": 200})).json()

    siu = {card["claimId"] for group in queue["groups"].values() for card in group["items"]}
    assert drill["total"] == 13
    assert len(siu) == 9
    assert siu < {row["claimId"] for row in drill["items"]} | set()


@requires_db
@pytest.mark.parametrize(
    ("series", "facet", "key_field"),
    [
        pytest.param("byStage", "stage", "key", id="settlement-donut"),
        pytest.param("bySeverity", "severity_band", "key", id="severity-donut"),
        pytest.param("byRecoveryStatus", "recovery_status", "key", id="recovery-bars"),
        pytest.param("byInjuryType", "injury_type", "label", id="injury-type-bars"),
        pytest.param("byState", "state", "label", id="state-bars"),
    ],
)
async def test_each_chart_segments_drill_through_counts_what_the_segment_counted(
    seeded_db_url: str, series: str, facet: str, key_field: str
) -> None:
    """AC 1 for the five *counted* chart families, segment by segment.

    Every published item of every series, not one sample: a facet that agreed
    with the largest bucket and disagreed with a small one would pass a
    spot-check and would open the wrong list on the segment a supervisor most
    needs to trust.

    The employer series is absent by construction and has its own test below —
    it distributes **cents**, not claims, so a bar's value is not a count and
    there is nothing here to equate.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *BLINE)
        charts = (await client.get(CHARTS)).json()
        for item in charts[series]["items"]:
            drill = (await client.get(DRILL, params=query_of(**{facet: item[key_field]}))).json()
            assert drill["total"] == item["count"], f"{series}:{item[key_field]}"


@requires_db
async def test_the_employer_drill_through_covers_the_employer_series(
    seeded_db_url: str,
) -> None:
    """The sixth chart facet, reconciled the only way its series permits.

    `byEmployer` distributes **money**, so a bar's value is cents and there is
    no per-employer claim count on the wire to equate a drill total with. What
    can be asserted is the partition: every employer the chart draws opens a
    non-empty list, and the twelve lists together are the whole book — which is
    what would fail if the facet matched on the wrong column or on a label.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *BLINE)
        charts = (await client.get(CHARTS)).json()
        summary = (await client.get(SUMMARY)).json()
        totals = {}
        for item in charts["byEmployer"]["items"]:
            drill = (
                await client.get(DRILL, params=query_of(employer_id=item["employerId"]))
            ).json()
            totals[item["employerId"]] = drill["total"]

    assert all(total > 0 for total in totals.values())
    assert sum(totals.values()) == summary["totalClaims"]


@requires_db
async def test_the_priority_facet_opens_the_worklists_population_before_its_cap(
    seeded_db_url: str,
) -> None:
    """`filter[priority]` == the worklist's `total`, which is the *uncapped* count.

    The worklist publishes both numbers — 30 shown of a population of 38 — and
    the drill-through has to answer the population, because a drill list has a
    cursor and no cap. Equating it with the *capped* row count would be the
    quiet version of the same failure: a list shorter than the number above it.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *BLINE)
        worklist = (await client.get(PRIORITY)).json()
        drill = (await client.get(DRILL, params=query_of(priority=True))).json()

    assert drill["total"] == worklist["total"] == 38
    assert worklist["cap"] < drill["total"], "this test needs the cap to have bitten"
    assert len(drill["items"]) > worklist["cap"]


@requires_db
async def test_the_handler_facet_opens_the_benchmark_rows_caseload(
    seeded_db_url: str,
) -> None:
    """AC 3 — the handler table's row click, reconciled against its own count.

    `caseCount` is that handler's claims *inside the caller's scope*, which is
    exactly what `filter[handlerId]` returns; and the id it filters on is the
    one this story published, never the display name.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *BLINE)
        table = (await client.get(BENCHMARKS)).json()
        for row in table["items"]:
            drill = (await client.get(DRILL, params=query_of(handler_id=row["handlerId"]))).json()
            assert drill["total"] == row["caseCount"], row["handlerName"]


# --- scope (AC 2, AD-7) -------------------------------------------------


@requires_db
async def test_every_claim_behind_a_scoped_personas_list_is_inside_her_book(
    seeded_db_url: str,
) -> None:
    pages = await walk(seeded_db_url, *PARK)
    seen = set(ids_of(pages))

    assert seen == seed_fixture.expected_claim_ids(*PARK)
    # The pair is the proof: equal sets would mean scoping stopped working and
    # the assertion above would still pass against the whole portfolio.
    assert seen < seed_fixture.expected_claim_ids(*BLINE)


@requires_db
async def test_an_employer_outside_the_scope_intersects_to_nothing(
    seeded_db_url: str,
) -> None:
    """The smuggled scope, and the answer is a defined empty page.

    Never a 403 and never a row: `filter[employerId]` is a narrowing applied
    after `employer_scope`, so naming Boeing from Jennifer Park's session asks
    for the intersection of her book with an employer it does not contain.
    """
    boeing = seed_fixture.employer_id_of("Boeing")
    assert boeing not in {
        seed_fixture.employer_id_of(name)
        for name in {claim["employer"] for claim in seed_fixture.claims_for(*PARK)}
    }

    async with make_client(seeded_db_url) as client:
        await login_as(client, *PARK)
        resp = await client.get(DRILL, params=query_of(employer_id=boeing))

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["items"] == []
    assert payload["total"] == 0
    # …and the chip carries no name, so it cannot become an oracle for the
    # existence of an employer she cannot see.
    assert payload["appliedFilters"] == [
        {"key": "employerId", "value": str(boeing), "display": None}
    ]


@requires_db
async def test_a_handler_inside_the_scope_narrows_rather_than_widens(
    seeded_db_url: str,
) -> None:
    """Marcus Chen's book, read through a scoped supervisor's session.

    Nineteen of his claims are inside Jennifer Park's scope, and every row is
    hers to read — the filter narrows her book by a handler and never reaches
    across it.
    """
    marcus = seed_fixture.handler_id_of("Marcus Chen")
    payload = await drill_for(seeded_db_url, *PARK, params=query_of(handler_id=marcus))
    expected = seed_fixture.expected_drill_claims(*PARK, handler_id=marcus)

    assert payload["total"] == expected["total"] == 19
    assert {row["claimId"] for row in payload["items"]} <= seed_fixture.expected_claim_ids(*PARK)
    assert payload["appliedFilters"][0]["display"] == "Marcus Chen"


@requires_db
async def test_a_defined_empty_page_names_the_filter_that_produced_it(
    seeded_db_url: str,
) -> None:
    """Jennifer Park has no litigated claims — a fact about her book, not a failure.

    `appliedFilters` is still populated, which is what lets the view render an
    empty state naming the active filter rather than a blank page.
    """
    payload = await drill_for(seeded_db_url, *PARK, params=query_of(litigation=True))

    assert payload["items"] == []
    assert payload["total"] == 0
    assert payload["appliedFilters"] == [{"key": "litigation", "value": "true", "display": None}]


@requires_db
async def test_an_unknown_free_string_is_an_empty_page_and_not_an_error(
    seeded_db_url: str,
) -> None:
    """`filter[injuryType]=Nonexistent` is a legitimate question with no answer.

    Free text has no vocabulary to validate against — that is the whole reason
    `charts.py` refuses to canonicalize it — so the honest answer is an empty
    set rather than a refusal that would imply the server holds a list.
    """
    payload = await drill_for(seeded_db_url, *BLINE, params=query_of(injury_type="Nonexistent"))

    assert payload["items"] == []
    assert payload["total"] == 0


@requires_db
@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        pytest.param("filter[stage]", "banana", id="stage"),
        pytest.param("filter[severityBand]", "extreme", id="severity-band"),
        pytest.param("filter[recoveryStatus]", "zzconvalescing", id="recovery-status"),
        pytest.param("filter[employerId]", "not-a-number", id="employer-id"),
        pytest.param("filter[priority]", "maybe", id="priority"),
    ],
)
async def test_an_unknown_enum_value_is_a_validation_error_and_never_a_bad_cursor(
    seeded_db_url: str, parameter: str, value: str
) -> None:
    """422 `/problems/validation-error`, `test_claims_queue.py`'s ruling.

    400 stays reserved for `/problems/invalid-cursor` on this route, so the two
    refusals cannot be confused by a client deciding whether to retry. The
    submitted value is deliberately not echoed: a problem document that quoted
    it back would be a reflection point on a shared surface. The values below
    are chosen so none is a *substring* of a permitted one — `recovered` would
    have false-failed against `returned_and_fully_recovered` in the enum list
    the document legitimately publishes.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *BLINE)
        resp = await client.get(DRILL, params={parameter: value})

    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"] == "/problems/validation-error"
    assert value not in json.dumps(body)


# --- the cursor, against the endpoint -----------------------------------


@requires_db
async def test_a_cursor_minted_under_a_different_filter_set_is_refused(
    seeded_db_url: str,
) -> None:
    """The queue's ruling on a set — see `Cursor`.

    Replaying page one's cursor under a different filter would page into a
    different list at the offset this one ended at, silently skipping or
    repeating claims with nothing on screen to say so.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *BLINE)
        first = (await client.get(DRILL)).json()
        assert first["nextCursor"] is not None
        resp = await client.get(
            DRILL, params={**query_of(severity_band="high"), "cursor": first["nextCursor"]}
        )

    assert resp.status_code == 400
    assert resp.json()["type"] == "/problems/invalid-cursor"
    assert resp.headers["cache-control"] == "no-store"


@requires_db
@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("////", id="not-base64"),
        pytest.param(
            base64.urlsafe_b64encode(b'{"nope":true}').decode().rstrip("="), id="not-a-cursor"
        ),
        pytest.param(
            _forged({"o": 10_000, "l": 50, "f": {}, "v": 1, "t": 5, "d": "2026-08-18"}),
            id="offset-past-the-end",
        ),
        pytest.param(
            _forged({"o": 50, "l": 50, "f": {}, "v": 99, "t": 5, "d": "2026-08-18"}),
            id="superseded-priority-weights",
        ),
        pytest.param(
            _forged({"o": 50, "l": 50, "f": {}, "v": 1, "t": 99, "d": "2026-08-18"}),
            id="superseded-derivation-thresholds",
        ),
        pytest.param(
            _forged({"o": 50, "l": 10, "f": {}, "v": 1, "t": 5, "d": "2026-08-18"}),
            id="page-size-the-rules-no-longer-publish",
        ),
        pytest.param(
            _forged({"o": 50, "l": 50, "f": {}, "v": 1, "t": 5, "d": "2020-01-01"}),
            id="older-than-the-maximum-age",
        ),
        pytest.param(
            _forged({"o": 50, "l": 50, "f": {}, "v": 1, "t": 5, "d": "2099-01-01"}),
            id="dated-in-the-future",
        ),
    ],
)
async def test_a_forged_or_stale_cursor_is_four_hundred_and_never_a_silent_page_one(
    seeded_db_url: str, raw: str
) -> None:
    """400 problem+json, `no-store`, and never a 500 or a quiet reset.

    Answering page one for an unreadable cursor would turn a client bug into an
    infinite "Show more" that re-appends the same claims for ever, which is why
    the refusal is loud.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *BLINE)
        resp = await client.get(DRILL, params={"cursor": raw})

    assert resp.status_code == 400, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["type"] == "/problems/invalid-cursor"
    assert resp.headers["cache-control"] == "no-store"


@requires_db
async def test_a_filtered_walk_pages_the_filtered_list(seeded_db_url: str) -> None:
    """A filter narrow enough to page: settled claims, over two pages.

    The cursor has to carry the filter set *and* be accepted when it is replayed
    with it, which is the other half of the refusal above — a cursor that
    refused its own filters would make every filtered list one page long.
    """
    params = query_of(osha_recordable=True)
    pages = await walk(seeded_db_url, *BLINE, params=params)
    expected = seed_fixture.expected_drill_claims(*BLINE, osha_recordable=True)

    assert ids_of(pages) == expected["claimIds"]
    assert [page["total"] for page in pages] == [expected["total"]] * len(pages)
    assert len(pages) == len(expected["pages"])


# --- the payload --------------------------------------------------------


def test_the_drill_row_is_the_queue_card_field_for_field() -> None:
    """One shape, so `ClaimCard` renders both lists and they cannot drift.

    Asserted between the two response **models** rather than between two
    fixtures, because that is where a divergence would appear: a field added to
    the queue card and forgotten here would leave every fixture-level assertion
    green and the shared component rendering an undefined value.
    """
    assert set(DrillClaimRowResponse.model_fields) == set(ClaimCardResponse.model_fields)


@requires_db
async def test_the_response_is_camel_case_and_carries_nothing_else(
    seeded_db_url: str,
) -> None:
    payload = await drill_for(seeded_db_url, *PARK)

    assert set(payload) == {
        "items",
        "nextCursor",
        "total",
        "appliedFilters",
        "rulesVersion",
        "thresholdsVersion",
        # Story 7.3's three, and they are the one thing on this payload that is
        # neither a row, a count nor a version: the edges an `ageGroup` chip's
        # range label is composed from. See `DrillClaimsResponse` for why a
        # composed `display` string was refused in their place.
        "ageYoungerMin",
        "ageOlderMin",
        "ageOldestMin",
    }
    assert set(payload["items"][0]) == ROW_KEYS
    # Business ids on the wire, never surrogates (the ID convention).
    assert all(row["claimId"].startswith("WC-") for row in payload["items"])


@requires_db
async def test_the_applied_filters_are_the_servers_reading_of_the_url(
    seeded_db_url: str,
) -> None:
    """The chip row, in `FILTER_KEYS` order, with the two id displays resolved.

    An unknown parameter name produces no chip, because it narrowed nothing —
    which is what makes "the chips, the request and the result agree" a property
    rather than a hope.
    """
    payload = await drill_for(
        seeded_db_url,
        *BLINE,
        params={
            **query_of(
                severity_band="high",
                litigation=True,
                employer_id=seed_fixture.employer_id_of("Boeing"),
            ),
            "filter[banana]": "1",
            "sort": "-severity",
        },
    )

    assert [item["key"] for item in payload["appliedFilters"]] == [
        "severityBand",
        "litigation",
        "employerId",
    ]
    assert [item["display"] for item in payload["appliedFilters"]] == [None, None, "Boeing"]
    assert [item["value"] for item in payload["appliedFilters"]] == [
        "high",
        "true",
        str(seed_fixture.employer_id_of("Boeing")),
    ]


@requires_db
async def test_the_published_rule_versions_are_the_documents_effective_today(
    seeded_db_url: str, db: AsyncSession
) -> None:
    payload = await drill_for(seeded_db_url, *BLINE)

    assert payload["rulesVersion"] == (await weights_for(db)).version
    assert payload["thresholdsVersion"] == (await thresholds_for(db)).version
    # …and the three age edges come from that same document rather than from a
    # constant here, so a retune moves the chip's label and the version together.
    thresholds = await thresholds_for(db)
    assert payload["ageYoungerMin"] == thresholds.age_younger_min
    assert payload["ageOlderMin"] == thresholds.age_older_min
    assert payload["ageOldestMin"] == thresholds.age_oldest_min


@requires_db
async def test_the_marker_describes_the_top_of_the_filtered_list(
    seeded_db_url: str,
) -> None:
    """`priority_markers`' ruling, inherited unchanged.

    Decided over the whole filtered list before the page is cut, so page two can
    never make a fourth claim sprout a marker — and it is the *filtered* list,
    so the same claim can carry the marker under one filter and not under
    another. That is genuinely a different list.
    """
    pages = await walk(seeded_db_url, *BLINE)
    marked = [row["claimId"] for page in pages for row in page["items"] if row["priorityMarker"]]

    assert len(marked) == SEEDED_WEIGHTS.marker_count
    assert all(row["priorityMarker"] is False for row in pages[1]["items"])


# --- the contract -------------------------------------------------------


@requires_db
async def test_the_route_declares_exactly_twenty_five_parameters(seeded_db_url: str) -> None:
    """AD-7 structurally, as an allowlist rather than as an assertion of emptiness.

    Twenty-four facets and a cursor, and it is an **allowlist** because what
    matters is what else could appear beside them: a `limit` would be the page
    size becoming a caller's choice, a `sort` would make every outstanding cursor
    ambiguous, and an `employerScope` of any spelling would be a scope.

    Thirteen when Story 5.5 wrote this; 7.1 appended two, 7.2 six and 7.3 four.
    The count is asserted as well as the set, so a facet cannot join the
    vocabulary without a number in a test moving — see `FACET_COUNT`.

    Asserted against the published OpenAPI document rather than the function
    signature, because the contract is what a client (and a reviewer) reads —
    which is also what pins the `filter[…]` bracket spelling.
    """
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"][DRILL]["get"]

    assert {parameter["name"] for parameter in operation["parameters"]} == PARAMETER_NAMES
    assert len(operation["parameters"]) == FACET_COUNT + 1
    assert all(parameter["in"] == "query" for parameter in operation["parameters"])
    assert "requestBody" not in operation


@requires_db
async def test_query_parameters_cannot_widen_or_change_the_scope(seeded_db_url: str) -> None:
    """The smuggling attempt: ask as Park, name Bline's portfolio anyway.

    Unknown parameters are ignored rather than rejected (FastAPI's default, and
    the safer direction — a 422 here would tell an attacker which parameter
    names exist). What matters is that the answer is byte-identical.

    `limit` and `sort` are in the list and stay there: both are published rules
    on this route rather than caller choices, so `?limit=200` has to be as inert
    as `?scopeAll=true`.
    """
    honest = await drill_for(seeded_db_url, *PARK)

    for smuggled in (
        {"employerId": "3"},
        {"employerIds": "1,2,3"},
        {"scopeAll": "true"},
        {"limit": "200"},
        {"sort": "-severity"},
        {"cap": "1"},
        {"scopeAll": "true", "limit": "200", "sort": "-severity", "cap": "1"},
    ):
        attempt = await drill_for(seeded_db_url, *PARK, params=smuggled)
        assert attempt == honest, f"{smuggled} changed the answer"


@requires_db
async def test_the_response_is_never_cached(seeded_db_url: str) -> None:
    """One persona's list must not be served to another from upstream."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *PARK)
        resp = await client.get(DRILL)
    assert resp.headers["cache-control"] == "no-store"


@requires_db
async def test_the_endpoint_requires_a_session(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.get(DRILL)

    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


@requires_db
async def test_reading_a_drill_through_writes_no_audit_event(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """A query is not a mutation (Epic 5 preamble).

    Counted before and after rather than asserted absent, because other rows
    exist: what must not happen is this request adding one.
    """
    count = sa.select(sa.func.count()).select_from(AuditEvent)
    before = (await db.execute(count)).scalar_one()

    await drill_for(seeded_db_url, *BLINE, params=query_of(severity_band="high"))

    await db.commit()  # a new snapshot, or the count would be the old one
    assert (await db.execute(count)).scalar_one() == before


@requires_db
async def test_the_analyst_reads_byte_identically_to_the_supervisor(
    seeded_db_url: str,
) -> None:
    """Two roles, one scope, one answer — the property AD-7 buys.

    David Bline holds both a supervisor and an analyst persona over the same
    `scope_all` book, so any difference between these two responses could only
    come from a role branch, and there is none anywhere on the path.
    """
    params = query_of(severity_band="high")
    supervisor = await drill_for(seeded_db_url, *BLINE, params=params)
    analyst = await drill_for(seeded_db_url, *ANALYST, params=params)

    assert analyst == supervisor


@requires_db
async def test_a_handler_may_drill_into_her_own_book(seeded_db_url: str) -> None:
    """The access decision, as a contract rather than an omission.

    This endpoint is ungated *by default* where `/dashboard/handler-benchmarks`
    answers 403, and the discriminator is this file's: the payload is a list of
    claims the caller can already open one at a time, and the row carries no
    handler at all. `employer_scope` is the same predicate here as in the queue,
    so Sarah Williams sees nothing her own caseload does not already contain.

    The one gated facet is `filter[handlerId]` naming somebody else — see the
    three tests below it. This request carries no facet at all, so it is the
    default path.
    """
    payload = await drill_for(seeded_db_url, *HANDLER)

    assert {row["claimId"] for row in payload["items"]} <= seed_fixture.expected_claim_ids(*HANDLER)
    assert set(payload["items"][0]) == ROW_KEYS


@requires_db
async def test_a_handler_may_not_drill_into_a_colleagues_book(seeded_db_url: str) -> None:
    """The gate on the one facet that publishes a figure about a named person.

    `total` under a sole `handlerId` facet is that handler's claim count inside
    the caller's scope — the same number `/dashboard/handler-benchmarks`
    publishes as `caseCount` and refuses a handler for reading — and
    `appliedFilters[0].display` names them. Ungated, twelve requests over a small
    integer range would rebuild the gated column, so the facet carries the gate
    the column carries.

    The refusal is the benchmarks refusal, not a second one: same status, same
    problem type, so a client already handling one handles both.
    """
    marcus = seed_fixture.handler_id_of("Marcus Chen")
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        resp = await client.get(DRILL, params=query_of(handler_id=marcus))

    assert resp.status_code == 403, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.headers["cache-control"] == "no-store"
    assert resp.json()["type"] == "/problems/benchmarks-not-permitted"


@requires_db
async def test_a_handler_may_still_filter_her_own_id(seeded_db_url: str) -> None:
    """Her own book is not oversight, so the gate does not reach it.

    A handler filtering to herself learns nothing she cannot already count from
    her own queue, and refusing it would break the row-click path on her own
    surfaces for a leak that is not one. `is not None` in the route rather than a
    truthiness test is what keeps a falsy id from skipping the check.
    """
    sarah = seed_fixture.handler_id_of("Sarah Williams")
    payload = await drill_for(seeded_db_url, *HANDLER, params=query_of(handler_id=sarah))

    assert {row["claimId"] for row in payload["items"]} <= seed_fixture.expected_claim_ids(*HANDLER)
    assert payload["appliedFilters"] == [
        {"key": "handlerId", "value": str(sarah), "display": "Sarah Williams"}
    ]


@requires_db
async def test_the_gate_reads_nothing_before_it_refuses(seeded_db_url: str) -> None:
    """The refusal precedes every read on this route, including the rule documents.

    `/handler-benchmarks` moved its gate to the top of the route for exactly this
    reason, and its docstring says so. A handler id that names nobody is refused
    on the same terms as one that names a colleague — the check is the caller's
    role, never the target's existence, so the 403 is not an enumeration oracle.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        absent = (await client.get(DRILL, params=query_of(handler_id=99_999))).status_code
        colleague = (
            await client.get(
                DRILL, params=query_of(handler_id=seed_fixture.handler_id_of("Marcus Chen"))
            )
        ).status_code

    assert absent == colleague == 403


@requires_db
async def test_oversight_roles_are_untouched_by_the_handler_gate(seeded_db_url: str) -> None:
    """The gate is `require_benchmarks_access`, so it lets through exactly who that lets through."""
    marcus = seed_fixture.handler_id_of("Marcus Chen")
    for persona in (PARK, ANALYST):
        payload = await drill_for(seeded_db_url, *persona, params=query_of(handler_id=marcus))
        assert payload["appliedFilters"][0]["display"] == "Marcus Chen"


@requires_db
async def test_the_aggregate_takes_exactly_one_scoped_read(db: AsyncSession) -> None:
    """ "One read" as a counted fact rather than a claim.

    One `select_drill_rows` over the scoped book, and nothing else. The two
    id-valued chips are resolved from the rows that read returned, which is the
    cheapest way for this to stop being true: a lookup for a caption reads like
    a formatting detail and would double the endpoint's cost on every request.

    The two rule documents are deliberately *not* in the count: they are the
    route's reads, loaded once and handed down, which is what keeps this
    aggregate a composition of scope and parameters.
    """
    ctx = await context_for(db, *BLINE)
    thresholds = await thresholds_for(db)
    weights = await weights_for(db)

    executed: list[str] = []
    original = db.execute

    async def counting(statement: Any, *args: Any, **kwargs: Any) -> Any:
        executed.append(str(statement).split("\n")[0])
        return await original(statement, *args, **kwargs)

    db.execute = counting  # type: ignore[method-assign]
    try:
        await drill_through_claims(
            db,
            ctx,
            thresholds,
            weights,
            DrillFilters(handler_id=seed_fixture.handler_id_of("Marcus Chen")),
        )
    finally:
        db.execute = original  # type: ignore[method-assign]

    assert len(executed) == 1, executed
    assert executed[0].startswith("SELECT")
