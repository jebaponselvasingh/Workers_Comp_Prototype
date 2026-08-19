"""Story 5.2 — the handler performance table, pure and over a real database.

Two halves, and the split is the module's shape:

- **The fold, without a database.** `benchmarks_of` is pure, so the blend's
  cut-points, the status bands, the substitution rule, the composite's
  definition and the sort's tie-break are all exercised over caseloads written
  by hand. Those are the cases AC 3 names by name, and none of them is
  reachable from the seeded portfolio — every seeded handler has settled claims
  and only one carries any litigation at all.
- **The endpoint, against the migrated seed.** Every figure is checked against
  `seed_fixture.expected_handler_benchmarks`, which re-derives the whole table
  from `seed_data.json` independently: the expectations here share no code with
  the aggregate under test, so a fold that was wrong in the same way as its
  oracle is not a state these tests can reach.

Three properties beyond "the numbers are right" get their own tests, because
they are what this story is exposed on:

- **Handlers come from claims, never from a roster (AD-7).** Kaya Johnson holds
  a John Deere assignment and handles none of its claims, so Ken Stoker's table
  must not contain her. An implementation that enumerated "the handlers assigned
  to my employers" would pass every count assertion and fail that one.
- **Scope, not role.** Jennifer Park's two rows are recomputed inside her
  27-claim book rather than sliced out of David Bline's hundred — her deviations
  differ from his for the same two handlers, which is only true if the portfolio
  row was recomputed too.
- **Role gates the surface.** A handler is refused before any claim is read,
  which is this story's own answer to the access question `/dashboard/summary`
  left open.

The DB-backed tests **mutate** the seeded rules (the supersession test inserts
and removes a `handler_performance` v2). The module-scoped `seeded_db_url`
fixture rebuilds the schema for this file, so the mutation is contained here and
is rolled back within its own test regardless.
"""

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, AuditEvent
from data.models.enums import ClaimStatus, Stage, UserRole
from data.repositories import claims as claim_repo
from data.repositories.identity import employer_ids_for
from rules import parameters as rule_parameters
from rules.engine import utc_today
from rules.parameters import (
    HANDLER_PERFORMANCE_KEY,
    HandlerPerformance,
    handler_performance_for,
    thresholds_for,
    weights_for,
)
from services.derivations import (
    ComplexityBand,
    CycleStatus,
    CycleTimeStatusDerivation,
    HandlerComplexityDerivation,
    HandlerMix,
)
from services.worklist import benchmarks as benchmarks_module
from services.worklist import sla
from services.worklist.benchmarks import (
    BenchmarkClaim,
    BenchmarksNotPermitted,
    benchmarks_of,
    handler_benchmarks,
)
from tests import seed_fixture
from tests.conftest import requires_db

BENCHMARKS = "/dashboard/handler-benchmarks"

BLINE = ("David Bline", "supervisor")
PARK = ("Jennifer Park", "supervisor")
STOKER = ("Ken Stoker", "supervisor")
ANALYST = ("David Bline", "analyst")
HANDLER = ("Kaya Johnson", "handler")

SETTINGS = Settings(database_url="postgresql://x/y")
TARGETS = sla.targets_for(SETTINGS)

#: The seeded `handler_performance` document, restated as an independent oracle
#: — `test_derivations.SEEDED_THRESHOLDS`' discipline. `tests/test_rules_engine.py`
#: is what ties these values back to the committed document.
PARAMS = HandlerPerformance(
    version=1,
    on_track_deviation_pct_max=-8,
    attention_deviation_pct_min=8,
    severity_weight_bp=10_000,
    surgery_rate_weight_bp=1_000,
    litigation_rate_weight_bp=1_500,
    complexity_score_max=100,
    complexity_high_min=65,
    complexity_med_min=40,
)

COMPLEXITY = HandlerComplexityDerivation()
CYCLE_STATUS = CycleTimeStatusDerivation()
PENDING = frozenset({ClaimStatus.initial, ClaimStatus.ch_assessment_process})

#: Every key one row carries, named rather than derived from a response — a
#: field that silently disappeared from the payload would otherwise shrink the
#: comparison instead of failing it.
ROW_KEYS = frozenset(
    {
        # Story 5.5's addition: the id a drill-through filters on. It is on the
        # wire beside the name and never instead of it — the column shows a
        # person, and the link under it points at an identity.
        "handlerId",
        "rank",
        "handlerName",
        "caseCount",
        "cycleSpeedPct",
        "compositeDays",
        "rtwPct",
        "complexityScore",
        "complexityBand",
        "pendingApprovals",
        "deviationPct",
        "cycleStatus",
    }
)


# --- the pure fold ------------------------------------------------------


def claim(
    *,
    handler_id: int = 1,
    handler_name: str = "Ada Lovelace",
    pick: int | None = 2,
    approve: int | None = 8,
    settlement: int | None = 60,
    settled: bool = True,
    recovered: bool = True,
    severity: int = 50,
    surgery: bool = False,
    litigation: bool = False,
    status: ClaimStatus = ClaimStatus.ch_approved,
) -> BenchmarkClaim:
    """One synthetic benchmark row input, with every default a neutral value."""
    return BenchmarkClaim(
        handler_id=handler_id,
        handler_name=handler_name,
        sample=sla.SlaSample(
            pick_days=pick,
            approve_days=approve,
            settlement_days=settlement,
            is_settled=settled,
            fully_recovered=recovered,
        ),
        severity_score=severity,
        surgery_required=surgery,
        litigation_flag=litigation,
        status=status,
    )


def fold(caseload: Sequence[BenchmarkClaim], params: HandlerPerformance = PARAMS) -> Any:
    return benchmarks_of(caseload, TARGETS, COMPLEXITY, CYCLE_STATUS, params, PENDING)


# --- complexity: the blend and its bands (AC 3) -------------------------


def test_an_all_low_severity_book_with_no_surgery_and_no_litigation_grades_low() -> None:
    """AC 3's first named case, over the derivation directly."""
    graded = COMPLEXITY.of(
        HandlerMix(case_count=4, severity_total=40, surgery_count=0, litigation_count=0), PARAMS
    )

    assert graded.score == 10
    assert graded.band is ComplexityBand.low


def test_a_heavy_surgery_and_litigation_book_grades_high() -> None:
    """AC 3's second. 55 average severity is `med` on its own; the two rates are
    what carry it over the High cut-off, which is the whole point of blending."""
    severity_only = COMPLEXITY.of(
        HandlerMix(case_count=2, severity_total=110, surgery_count=0, litigation_count=0), PARAMS
    )
    blended = COMPLEXITY.of(
        HandlerMix(case_count=2, severity_total=110, surgery_count=2, litigation_count=2), PARAMS
    )

    assert severity_only.score == 55
    assert severity_only.band is ComplexityBand.med
    assert blended.score == 80  # 55 + 10 (all surgical) + 15 (all litigated)
    assert blended.band is ComplexityBand.high


@pytest.mark.parametrize(
    ("severity", "band"),
    [
        (0, ComplexityBand.low),
        (39, ComplexityBand.low),  # boundary: last low
        (40, ComplexityBand.med),  # boundary: first med
        (64, ComplexityBand.med),  # boundary: last med
        (65, ComplexityBand.high),  # boundary: first high
        (100, ComplexityBand.high),
    ],
)
def test_the_complexity_bands_are_inclusive_at_both_cut_points(
    severity: int, band: ComplexityBand
) -> None:
    """Both cut-points from both sides (AC 3), over a one-claim book with no
    surgery and no litigation — so the score *is* the severity and the boundary
    is not obscured by the blend."""
    mix = HandlerMix(case_count=1, severity_total=severity, surgery_count=0, litigation_count=0)

    assert COMPLEXITY.of(mix, PARAMS).score == severity
    assert COMPLEXITY.of(mix, PARAMS).band is band


def test_the_blend_rounds_half_up_rather_than_to_even() -> None:
    """64.5 grades High, not Medium.

    `round()` in Python is banker's rounding, which would send 64.5 down to 64
    and 65.5 up to 66 — a scale that rounds one boundary down and the next one
    up, which is the defect `sla._rounded` refuses in the same words.
    """
    half = HandlerMix(case_count=2, severity_total=129, surgery_count=0, litigation_count=0)

    assert COMPLEXITY.of(half, PARAMS).score == 65
    assert COMPLEXITY.of(half, PARAMS).band is ComplexityBand.high


def test_the_score_is_capped_at_the_documents_maximum() -> None:
    """The prototype's `Math.min(100, …)`. Without it a maximal book blends to
    125 and the chip reads a number outside the scale beside it."""
    maximal = HandlerMix(case_count=1, severity_total=100, surgery_count=1, litigation_count=1)

    assert COMPLEXITY.of(maximal, PARAMS).score == PARAMS.complexity_score_max


def test_the_blend_weights_are_parameters_not_literals() -> None:
    """Retuning the document moves the grade with no code change (AD-8).

    Doubling the litigation weight is chosen deliberately: it is the term the
    seeded portfolio barely exercises, so nothing else in this suite would
    notice if it were wired to the wrong parameter.
    """
    mix = HandlerMix(case_count=2, severity_total=100, surgery_count=0, litigation_count=2)
    retuned = replace(PARAMS, litigation_rate_weight_bp=3_000)

    assert COMPLEXITY.of(mix, PARAMS).score == 65  # 50 + 15
    assert COMPLEXITY.of(mix, retuned).score == 80  # 50 + 30


@pytest.mark.parametrize(
    "mix",
    [
        {"case_count": 0, "severity_total": 0, "surgery_count": 0, "litigation_count": 0},
        {"case_count": 2, "severity_total": -1, "surgery_count": 0, "litigation_count": 0},
        {"case_count": 2, "severity_total": 10, "surgery_count": 3, "litigation_count": 0},
        {"case_count": 2, "severity_total": 10, "surgery_count": 0, "litigation_count": -1},
    ],
)
def test_a_mix_that_cannot_describe_a_real_book_is_refused(mix: dict[str, int]) -> None:
    """A rate above 100% inflates the blend silently and grades a light book High;
    a zero-claim mix is a `ZeroDivisionError` inside the derivation."""
    with pytest.raises(ValueError):
        HandlerMix(**mix)


# --- cycle-time status: the two deviation bands (AC 2) ------------------


@pytest.mark.parametrize(
    ("deviation", "status"),
    [
        (-100, CycleStatus.on_track),
        (-9, CycleStatus.on_track),
        (-8, CycleStatus.on_track),  # boundary: inclusive
        (-7, CycleStatus.watch),
        (0, CycleStatus.watch),
        (7, CycleStatus.watch),
        (8, CycleStatus.attention),  # boundary: inclusive
        (9, CycleStatus.attention),
        (100, CycleStatus.attention),
    ],
)
def test_the_status_bands_are_inclusive_at_and_either_side_of_both_thresholds(
    deviation: int, status: CycleStatus
) -> None:
    assert CYCLE_STATUS.of(deviation, PARAMS) is status


def test_the_deviation_bands_are_parameters_not_literals() -> None:
    """Widening the document moves the chip with no code change (AD-8)."""
    widened = replace(PARAMS, on_track_deviation_pct_max=-20, attention_deviation_pct_min=20)

    assert CYCLE_STATUS.of(-9, PARAMS) is CycleStatus.on_track
    assert CYCLE_STATUS.of(-9, widened) is CycleStatus.watch
    assert CYCLE_STATUS.of(9, PARAMS) is CycleStatus.attention
    assert CYCLE_STATUS.of(9, widened) is CycleStatus.watch


# --- the composite, the ranking and the substitution rule ---------------


def test_the_composite_is_the_sum_of_the_three_segment_averages() -> None:
    """AC 1's ranking key, asserted against `sla.segment_means_of` rather than
    restated.

    The point of the assertion is the *identity*: the table's Avg Days column
    and the SLA strip's three tiles are the same three averages, so the two
    surfaces can only disagree by `sla.py` disagreeing with itself (AD-2).
    """
    caseload = [
        claim(pick=2, approve=9, settlement=61),
        claim(pick=3, approve=8, settlement=64),
    ]
    means = sla.segment_means_of([row.sample for row in caseload])
    segments = [means[key] for key in sla.DURATION_SEGMENTS]

    assert segments == [Decimal("2.5"), Decimal("8.5"), Decimal("62.5")]
    assert fold(caseload).items[0].composite_days == float(sum(segments))  # type: ignore[arg-type]


def test_the_composite_is_summed_unrounded_rather_than_from_the_published_tiles() -> None:
    """The defect this story was re-derived for, pinned on the smallest case
    that shows it.

    The settle tile is published to *whole days*, so a book settling in 62.5
    days on average shows `63d` on the SLA strip. Summing the published tiles
    would make the composite 2.5 + 8.5 + 63 = 74.0; summing the means makes it
    73.5. Half a day of error per row, which is larger than the gap between four
    of the six seeded handlers — enough to reorder them, which is exactly what it
    did.
    """
    caseload = [
        claim(pick=2, approve=9, settlement=61),
        claim(pick=3, approve=8, settlement=64),
    ]
    strip = sla.strip_of([row.sample for row in caseload], TARGETS)
    published = [
        strip[sla.SlaMetricKey.pick].value,
        strip[sla.SlaMetricKey.approve].value,
        strip[sla.SlaMetricKey.settle].value,
    ]

    # The tiles still round — that ruling is untouched, and it is what makes the
    # two figures differ at all.
    assert published == [2.5, 8.5, 63.0]
    assert fold(caseload).items[0].composite_days == 73.5
    assert fold(caseload).items[0].composite_days != sum(published)  # type: ignore[arg-type]


def test_two_handlers_are_ordered_by_a_gap_the_published_column_cannot_show() -> None:
    """The seeded misordering, reproduced in miniature.

    `Ahead` settles at 60.4 days on average and `Behind` at 60.6 — a real gap of
    a fifth of a day. Rounding each to whole days first makes them 60 and 61 …
    which happens to preserve the order here; the failure mode is the *other*
    direction, where rounding inverts it. So the assertion is the stronger one:
    the order follows the exact composite, and the published column is allowed to
    show the two rows as equal rather than being allowed to decide which is
    first.
    """
    table = fold(
        [
            claim(handler_id=1, handler_name="Behind", pick=1, approve=1, settlement=61),
            claim(handler_id=1, handler_name="Behind", pick=1, approve=1, settlement=61),
            claim(handler_id=1, handler_name="Behind", pick=1, approve=1, settlement=60),
            claim(handler_id=1, handler_name="Behind", pick=1, approve=1, settlement=60),
            claim(handler_id=1, handler_name="Behind", pick=1, approve=1, settlement=61),
            claim(handler_id=2, handler_name="Ahead", pick=1, approve=1, settlement=61),
            claim(handler_id=2, handler_name="Ahead", pick=1, approve=1, settlement=60),
            claim(handler_id=2, handler_name="Ahead", pick=1, approve=1, settlement=60),
            claim(handler_id=2, handler_name="Ahead", pick=1, approve=1, settlement=61),
            claim(handler_id=2, handler_name="Ahead", pick=1, approve=1, settlement=60),
        ]
    )

    assert [row.handler_name for row in table.items] == ["Ahead", "Behind"]
    # 1 + 1 + 60.4 and 1 + 1 + 60.6, published to the tenth the order was decided
    # at — so a reader can see the gap the sort used.
    assert [row.composite_days for row in table.items] == [62.4, 62.6]


def test_the_rows_are_ranked_by_ascending_composite() -> None:
    """Fastest desk first, which is the prototype's order and AC 1's."""
    table = fold(
        [
            claim(handler_id=1, handler_name="Slow", pick=9, approve=9, settlement=90),
            claim(handler_id=2, handler_name="Fast", pick=1, approve=1, settlement=10),
            claim(handler_id=3, handler_name="Middling", pick=4, approve=4, settlement=40),
        ]
    )

    assert [(row.rank, row.handler_name) for row in table.items] == [
        (1, "Fast"),
        (2, "Middling"),
        (3, "Slow"),
    ]
    assert table.leader == "Fast"
    assert table.laggard == "Slow"


def test_a_tie_on_the_composite_is_broken_by_handler_name() -> None:
    """Two handlers whose books average identically are ordered by name.

    Exact ties are reachable whenever two desks average the same — small books
    do this routinely — and an unstable order would make two consecutive
    requests disagree about who is third, on a list whose whole subject is the
    order. Name first because it is the visible column and a reader can check it.
    """
    identical = [
        claim(handler_id=1, handler_name="Zoe Adams"),
        claim(handler_id=2, handler_name="Al Baker"),
        claim(handler_id=3, handler_name="Mia Clark"),
    ]

    assert [row.handler_name for row in fold(identical).items] == [
        "Al Baker",
        "Mia Clark",
        "Zoe Adams",
    ]
    assert [row.rank for row in fold(identical).items] == [1, 2, 3]
    assert len({row.composite_days for row in fold(identical).items}) == 1


def test_a_tie_between_two_handlers_sharing_a_name_is_broken_by_handler_id() -> None:
    """The third sort key, and the only one with nothing visible behind it.

    Names are not unique — the seed's happen to be — so a table of two people
    called Sam Ray with identical books would have no total order without this,
    and the two rows could swap between requests with nothing on screen changing.
    The relative order is asserted rather than merely the row count, because "the
    order is deterministic" is a claim about *which* row comes first.
    """
    shared = [
        claim(handler_id=7, handler_name="Sam Ray", severity=40),
        claim(handler_id=3, handler_name="Sam Ray", severity=80),
    ]

    table = fold(shared)
    assert [row.rank for row in table.items] == [1, 2]
    assert [row.handler_name for row in table.items] == ["Sam Ray", "Sam Ray"]
    # The lower id first — the complexity score is what tells the two rows apart
    # on the wire, since the id itself is deliberately not published.
    assert [row.complexity_score for row in table.items] == [80, 40]
    assert table.leader == "Sam Ray"


def test_two_handlers_sharing_a_name_stay_two_rows() -> None:
    """Grouping is on `handler_id`, so a display-name collision is two rows
    rather than one merged book averaging both."""
    table = fold(
        [
            claim(handler_id=1, handler_name="Sam Ray", pick=1),
            claim(handler_id=2, handler_name="Sam Ray", pick=9),
        ]
    )

    assert [row.case_count for row in table.items] == [1, 1]
    assert {row.handler_name for row in table.items} == {"Sam Ray"}


def test_a_handler_with_no_settled_claim_borrows_the_portfolios_settle_average() -> None:
    """The generalised substitution rule (Design Note 2).

    The prototype substitutes the portfolio's settle average and lets a missing
    pick or approve average fall through as `0`, which reads as "instant" and
    ranks that handler first. Here the substitution is uniform, and the assertion
    is that the borrowed segment is the portfolio's own — not zero, and not the
    handler's own claim count standing in for one.
    """
    settled_book = [claim(handler_id=1, handler_name="Settler", settlement=60, settled=True)]
    open_book = [claim(handler_id=2, handler_name="Newcomer", settlement=None, settled=False)]
    table = fold([*settled_book, *open_book])

    rows = {row.handler_name: row for row in table.items}
    assert rows["Newcomer"].composite_days == rows["Settler"].composite_days
    assert table.portfolio_composite_days == rows["Newcomer"].composite_days
    # And the borrowed figure is not the zero the prototype would have shown for
    # a missing pick or approve segment.
    assert rows["Newcomer"].composite_days is not None
    assert rows["Newcomer"].composite_days > 0


def test_a_missing_pick_segment_is_substituted_too_rather_than_read_as_instant() -> None:
    """The half of the rule the prototype does not have. A handler with no
    recorded pick-up duration would otherwise compose as `0 + approve + settle`
    and be ranked fastest on the desk."""
    table = fold(
        [
            claim(handler_id=1, handler_name="Recorded", pick=6),
            claim(handler_id=2, handler_name="Unrecorded", pick=None),
        ]
    )

    rows = {row.handler_name: row for row in table.items}
    assert rows["Unrecorded"].composite_days == rows["Recorded"].composite_days
    # The borrowed pick-up average is the portfolio's 6 days, so the composite
    # is 6 + 8 + 60 — not the 68 the prototype's fallthrough would have produced,
    # which would have put this handler first on a desk it is level with.
    assert rows["Unrecorded"].composite_days == 74.0
    assert [row.handler_name for row in table.items] == ["Recorded", "Unrecorded"]


def test_a_segment_the_whole_scope_is_missing_leaves_the_composite_unknown() -> None:
    """The state the spec's Block If reserves for a product decision.

    Nothing honest is left to substitute — a partial sum silently compared
    against other partial sums is the one answer this module refuses — so every
    composite-derived field is `None` together and the rows fall back to name
    order. Unreachable on any seeded scope; asserted because the branch exists.
    """
    table = fold(
        [
            claim(handler_id=1, handler_name="Bea", settlement=None, settled=False),
            claim(handler_id=2, handler_name="Abe", settlement=None, settled=False),
        ]
    )

    assert table.portfolio_composite_days is None
    assert [row.handler_name for row in table.items] == ["Abe", "Bea"]
    for row in table.items:
        assert row.composite_days is None
        assert row.deviation_pct is None
        assert row.cycle_status is None
        assert row.cycle_speed_pct is None
        # …and no rank, which is the same condition. A `#` column numbering
        # rows that are in *name* order is a ranking claim the data one field
        # away explicitly denies.
        assert row.rank is None
    # The callout has nothing to say about a table with no ordering.
    assert table.leader is None
    assert table.laggard is None
    # …and the row is still a row: the counts and the grade do not depend on a
    # cycle time at all.
    assert [row.case_count for row in table.items] == [1, 1]
    assert all(row.complexity_score > 0 for row in table.items)


def test_the_bar_is_relative_to_the_slowest_handler_in_scope() -> None:
    """The prototype's `comp / maxComp`: the slowest bar is always full."""
    table = fold(
        [
            claim(handler_id=1, handler_name="Half", pick=1, approve=1, settlement=48),
            claim(handler_id=2, handler_name="Full", pick=2, approve=2, settlement=96),
        ]
    )

    rows = {row.handler_name: row for row in table.items}
    assert rows["Full"].cycle_speed_pct == 100
    assert rows["Half"].cycle_speed_pct == 50


def test_the_rtw_rate_is_null_rather_than_zero_when_nothing_has_settled() -> None:
    """`sla.py`'s honesty rule, carried onto a row.

    The prototype divides by `max(1, settledCount)` and prints "0%", which reads
    as "nobody came back" rather than "nobody has settled yet".
    """
    table = fold([claim(settled=False, settlement=None, recovered=False)])

    assert table.items[0].rtw_pct is None


def test_pending_approvals_count_the_documents_statuses() -> None:
    """The queue scorer's `pendingApprovalStatuses`, not a payment week's status
    (Design Note 4)."""
    table = fold(
        [
            claim(status=ClaimStatus.initial),
            claim(status=ClaimStatus.ch_assessment_process),
            claim(status=ClaimStatus.ch_approved),
            claim(status=ClaimStatus.settled_closed),
        ]
    )

    assert table.items[0].pending_approvals == 2
    assert table.items[0].case_count == 4


def test_a_scope_with_no_claims_has_no_rows_and_no_callout() -> None:
    """The NFR-3 empty state at the fold, and the shape the endpoint's empty
    book produces: an empty list rather than a row of zeros."""
    table = fold([])

    assert table.items == ()
    assert table.leader is None
    assert table.laggard is None
    assert table.portfolio_composite_days is None


def test_the_deviation_is_zero_rather_than_a_division_when_the_desk_takes_no_time() -> None:
    """A composite of zero is not reachable on real data and a division by it
    is. The prototype guards it the same way (`compPortfolio ? … : 0`)."""
    table = fold([claim(pick=0, approve=0, settlement=0)])

    assert table.items[0].composite_days == 0
    assert table.items[0].deviation_pct == 0
    assert table.items[0].cycle_status is CycleStatus.watch
    assert table.items[0].cycle_speed_pct == 100


# --- the endpoint, against the seeded portfolio -------------------------
#
# `requires_db` is applied per test rather than as a module-level `pytestmark`,
# unlike `test_portfolio_summary.py`: everything above this line is pure and
# runs in the lint job, and a module-wide skip would silently take the blend's
# cut-points and the status bands — the two things AC 3 asks for by name — out
# of every run without a Postgres.


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
    which is the whole reason this is a helper: David Bline has *no* rows in
    `user_employer_assignment`, so reading his assignments would hand the
    aggregate an empty book and every figure below would be zero for a reason
    that has nothing to do with the code under test.
    """
    user = (
        await db.scalars(sa.select(AppUser).where(AppUser.name == name, AppUser.role == role))
    ).one()
    return CallerContext(
        user_id=user.id,
        role=user.role,
        employer_ids=ALL_EMPLOYERS if user.scope_all else await employer_ids_for(db, user.id),
    )


async def benchmarks_for(db_url: str, name: str, role: str, **kwargs: Any) -> dict[str, Any]:
    async with make_client(db_url) as client:
        await login_as(client, name, role)
        resp = await client.get(BENCHMARKS, **kwargs)
        assert resp.status_code == 200, resp.text
        payload: dict[str, Any] = resp.json()
        return payload


def table_of(payload: dict[str, Any]) -> dict[str, Any]:
    """The four counted/ordered fields, without the five rule-provenance ones."""
    return {key: payload[key] for key in ("items", "portfolioCompositeDays", "leader", "laggard")}


@pytest.mark.parametrize(("name", "role"), [BLINE, PARK, STOKER, ANALYST])
@requires_db
async def test_the_table_covers_exactly_the_personas_book(
    seeded_db_url: str, name: str, role: str
) -> None:
    """Every row, every figure, every rank, against the independent oracle."""
    payload = await benchmarks_for(seeded_db_url, name, role)

    assert table_of(payload) == seed_fixture.expected_handler_benchmarks(name, role)


#: The full portfolio's ranking, written out as a literal sequence.
#:
#: The one assertion in this suite that does not go through the oracle, and the
#: reason it exists is that the oracle and the implementation could in principle
#: share a misreading of the *rule*. This is the order the spec's I/O matrix
#: names, typed out by hand from the dataset, and it is the assertion that
#: failed under the first implementation: summing the published segment tiles
#: put Fatima Al-Mansoori (72.667 days) above Marcus Chen (72.036) because
#: rounding their settle segments to whole days made 72.3 look faster than 72.4.
#:
#: A set plus `rank == [1..6]` pins nothing about order and would have passed.
FULL_PORTFOLIO_ORDER = [
    "Liam O'Sullivan",
    "Kaya Johnson",
    "Marcus Chen",
    "Fatima Al-Mansoori",
    "Sarah Williams",
    "Dante Reyes",
]

#: Their case counts, in the same order — the matrix's 7 · 38 · 19 · 4 · 8 · 24.
FULL_PORTFOLIO_CASE_COUNTS = [7, 38, 19, 4, 8, 24]


@requires_db
async def test_the_full_portfolio_supervisor_sees_all_six_handlers(seeded_db_url: str) -> None:
    """Named in the story text, so asserted literally as well as by derivation —
    and as an *ordered list*, because the ordering is the contract."""
    payload = await benchmarks_for(seeded_db_url, *BLINE)
    seeded = {claim["handler"] for claim in seed_fixture.seed()["claims"]}

    assert {row["handlerName"] for row in payload["items"]} == seeded
    assert len(payload["items"]) == 6
    assert [row["rank"] for row in payload["items"]] == [1, 2, 3, 4, 5, 6]
    assert sum(row["caseCount"] for row in payload["items"]) == len(seed_fixture.seed()["claims"])
    # The two the callout names, and the two ends of the order they came from.
    assert payload["leader"] == payload["items"][0]["handlerName"]
    assert payload["laggard"] == payload["items"][-1]["handlerName"]


@requires_db
async def test_the_full_portfolio_ranks_in_exactly_this_sequence(seeded_db_url: str) -> None:
    """The spec's I/O matrix, asserted literally — see `FULL_PORTFOLIO_ORDER`.

    Marcus Chen sits above Fatima Al-Mansoori and the gap between them is 0.63
    days, which is smaller than the half-day of error that summing whole-day
    settle tiles admits. That pair is the assertion; the other four rows are
    here so the sequence is the whole contract rather than one comparison.
    """
    payload = await benchmarks_for(seeded_db_url, *BLINE)

    assert [row["handlerName"] for row in payload["items"]] == FULL_PORTFOLIO_ORDER
    assert [row["caseCount"] for row in payload["items"]] == FULL_PORTFOLIO_CASE_COUNTS
    assert payload["leader"] == "Liam O'Sullivan"
    assert payload["laggard"] == "Dante Reyes"
    # Published to a tenth, ascending, and strictly so for this pair: the column
    # a supervisor reads shows the gap the sort was decided on rather than
    # hiding it behind a whole day.
    composites = [row["compositeDays"] for row in payload["items"]]
    assert composites == sorted(composites)
    marcus, fatima = composites[2], composites[3]
    assert marcus < fatima, "the rounding artefact is back — see the spec's change log"


@requires_db
async def test_the_analyst_reads_byte_identically_to_the_supervisor(seeded_db_url: str) -> None:
    """The same persona wearing the other hat. Any difference at all would mean
    a figure came from a role branch rather than from the scope predicate."""
    assert await benchmarks_for(seeded_db_url, *ANALYST) == await benchmarks_for(
        seeded_db_url, *BLINE
    )


@requires_db
async def test_a_scoped_supervisor_sees_only_her_own_handlers(seeded_db_url: str) -> None:
    """Jennifer Park's Toyota/GM/3M book: Marcus Chen and Sarah Williams only."""
    payload = await benchmarks_for(seeded_db_url, *PARK)

    assert [row["handlerName"] for row in payload["items"]] == ["Marcus Chen", "Sarah Williams"]
    assert [row["caseCount"] for row in payload["items"]] == [19, 8]
    assert sum(row["caseCount"] for row in payload["items"]) == len(seed_fixture.claims_for(*PARK))


@requires_db
async def test_a_scoped_supervisors_figures_are_recomputed_not_sliced(
    seeded_db_url: str,
) -> None:
    """AD-7's sharpest form on this surface, and the one an implementation that
    computed the table once and filtered it would fail.

    The same two handlers appear in David Bline's table. Their case counts and
    composites are identical — those are properties of a handler's whole book,
    and both handlers' books sit entirely inside Park's scope — but the
    *deviation* is a comparison against the viewer's portfolio, and Park's
    27-claim portfolio is slower than the full hundred. So the same handler is
    -1% against her book and +1% against his, which is only true if the
    portfolio row was recomputed inside her scope.
    """
    park_payload = await benchmarks_for(seeded_db_url, *PARK)
    park = {row["handlerName"]: row for row in park_payload["items"]}
    bline_payload = await benchmarks_for(seeded_db_url, *BLINE)
    bline = {row["handlerName"]: row for row in bline_payload["items"]}

    for handler in ("Marcus Chen", "Sarah Williams"):
        assert park[handler]["caseCount"] == bline[handler]["caseCount"]
        assert park[handler]["compositeDays"] == bline[handler]["compositeDays"]

    assert park_payload["portfolioCompositeDays"] != bline_payload["portfolioCompositeDays"]
    assert park["Marcus Chen"]["deviationPct"] != bline["Marcus Chen"]["deviationPct"]
    # …and the bar is peer-relative, so it moves with the peer set too.
    assert park["Marcus Chen"]["cycleSpeedPct"] != bline["Marcus Chen"]["cycleSpeedPct"]


@requires_db
async def test_a_handler_assigned_an_employer_she_handles_no_claim_for_is_absent(
    seeded_db_url: str,
) -> None:
    """The roster-versus-claims test, and the reason AD-7 names the straddle case.

    Kaya Johnson holds a John Deere assignment in `user_employer_assignment`;
    every seeded Deere claim is Liam O'Sullivan's. Ken Stoker supervises Deere
    and Lockheed, so his table is Liam and Fatima — and an implementation that
    enumerated "the handlers assigned to my employers" would list Kaya with an
    empty book, or worse, with her Caterpillar figures.

    The premise is asserted from the seed rather than assumed, so the day
    somebody gives Kaya a Deere claim this test says the *fixture* changed
    instead of failing as if the code had.
    """
    assigned = seed_fixture.employers_of(*HANDLER)
    stoker_employers = seed_fixture.employers_of(*STOKER)
    overlap = assigned & stoker_employers
    assert overlap, "Kaya must share an employer with Ken Stoker for this test to mean anything"
    assert not [
        claim
        for claim in seed_fixture.seed()["claims"]
        if claim["handler"] == HANDLER[0] and claim["employer"] in overlap
    ], "Kaya now handles a claim in Ken Stoker's scope — the straddle premise has moved"

    payload = await benchmarks_for(seeded_db_url, *STOKER)

    assert [row["handlerName"] for row in payload["items"]] == [
        "Liam O'Sullivan",
        "Fatima Al-Mansoori",
    ]
    assert HANDLER[0] not in {row["handlerName"] for row in payload["items"]}


@requires_db
async def test_an_empty_book_reports_no_rows_rather_than_failing(db: AsyncSession) -> None:
    """A persona assigned to no employers is a legitimate state, and
    `employer_scope` reads it as a predicate matching nothing rather than as "no
    filter". No rows, no callout — and the rules still arrive, because "nothing
    in the book" says nothing about which rules were in force."""
    params = await handler_performance_for(db)
    table = await handler_benchmarks(
        db,
        CallerContext(user_id=0, role=UserRole.supervisor, employer_ids=frozenset()),
        params,
        await weights_for(db),
        await thresholds_for(db),
        SETTINGS,
    )

    assert table.items == ()
    assert table.leader is None
    assert table.laggard is None
    assert table.portfolio_composite_days is None


@requires_db
async def test_every_claim_behind_a_scoped_personas_rows_is_inside_her_book(
    db: AsyncSession,
) -> None:
    """AC 4's named proof, taken at the aggregate's own read path.

    The counts agreeing with an oracle shows the arithmetic; this shows the
    *set*. Her context is built the way `api/deps.py` builds it, and the handlers
    behind the rows the aggregate folded are compared against the handlers of
    her seeded claims — so a widened predicate would show up as a handler she
    cannot see rather than as a number that happened to match.
    """
    ctx = await context_for(db, *PARK)

    table = await handler_benchmarks(
        db,
        ctx,
        await handler_performance_for(db),
        await weights_for(db),
        await thresholds_for(db),
        SETTINGS,
    )

    assert {row.handler_name for row in table.items} == {
        claim["handler"] for claim in seed_fixture.claims_for(*PARK)
    }


# --- the role gate (AC 3 of the spec's matrix) --------------------------


@pytest.mark.parametrize("role", sorted(set(UserRole) - {UserRole.supervisor, UserRole.analyst}))
def test_the_gate_is_an_allowlist_so_every_other_role_is_refused(role: UserRole) -> None:
    """Enumerated from the enum, so a role added later fails here rather than
    inheriting the whole desk's performance figures.

    That is not hypothetical: `UserRole.system` already exists — the payment
    batch's actor — and a denylist spelled `role is UserRole.handler` would have
    admitted it on the day it was declared, with nothing to notice. The
    parametrisation is over `set(UserRole) - PERMITTED_ROLES` rather than over a
    written-out list for the same reason: adding a member has to break this test.
    """
    assert role not in benchmarks_module.PERMITTED_ROLES


def test_the_two_permitted_roles_are_the_oversight_pair() -> None:
    """The allowlist itself, asserted once — the set is the access decision."""
    permitted = benchmarks_module.PERMITTED_ROLES
    assert permitted == frozenset({UserRole.supervisor, UserRole.analyst})


@requires_db
async def test_a_handler_is_refused(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        resp = await client.get(BENCHMARKS)

    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["type"] == "/problems/benchmarks-not-permitted"
    assert resp.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("role", sorted(set(UserRole) - {UserRole.supervisor, UserRole.analyst}))
@requires_db
async def test_the_refusal_happens_before_any_claim_is_read(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, role: UserRole
) -> None:
    """The ordering, asserted rather than described.

    A refusal that depended on what the scoped read returned would answer
    differently for a handler with a full desk and one between assignments,
    which is an oracle about the roster. The repository is replaced with a
    function that fails if it is reached at all.

    Over every non-permitted role rather than over the handler alone, so this is
    also the running assertion that the gate is an allowlist: a `UserRole` added
    later arrives here without anyone remembering to add it.
    """

    async def forbidden(*_args: object, **_kwargs: object) -> Sequence[sa.Row[Any]]:
        raise AssertionError("the scoped read ran before the role gate")

    monkeypatch.setattr(claim_repo, "select_claim_columns_with_handler", forbidden)

    with pytest.raises(BenchmarksNotPermitted):
        await handler_benchmarks(
            db,
            CallerContext(user_id=1, role=role, employer_ids=frozenset({1})),
            await handler_performance_for(db),
            await weights_for(db),
            await thresholds_for(db),
            SETTINGS,
        )


@requires_db
async def test_a_refused_caller_loads_no_rule_document_either(
    seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal precedes every read, not only the claim read.

    The route loads three rule documents — `handler_performance`,
    `priority_weights` and `derivation_thresholds` — before it calls the
    aggregate, so a gate that lived only in the service answered 403 *after*
    three `rules_document` queries had already run. That is not a leak, but four
    docstrings claim an ordering, and an ordering nothing asserts is one
    refactor from being false. `rules.parameters.load` is the single door all
    three go through; replacing it with a function that fails is the whole
    assertion.
    """

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a rule document was loaded before the role gate answered")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        monkeypatch.setattr(rule_parameters, "load", forbidden)
        resp = await client.get(BENCHMARKS)

    assert resp.status_code == 403
    assert resp.json()["type"] == "/problems/benchmarks-not-permitted"


# --- the rules are the document's (AC 2, AD-8) --------------------------


@requires_db
async def test_the_published_bands_come_from_the_rule_document(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """Compared against the JDM resolution path, never against a literal."""
    params = await handler_performance_for(db)
    payload = await benchmarks_for(seeded_db_url, *BLINE)

    assert payload["onTrackDeviationPctMax"] == params.on_track_deviation_pct_max
    assert payload["attentionDeviationPctMin"] == params.attention_deviation_pct_min
    assert payload["complexityHighMin"] == params.complexity_high_min
    assert payload["complexityMedMin"] == params.complexity_med_min
    assert payload["rulesVersion"] == params.version


@requires_db
async def test_a_superseded_rule_document_moves_the_chips_and_the_published_bands(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """AD-8 end to end, and the only demonstration of it worth anything.

    A v2 of `handler_performance` widening both deviation bands is inserted
    effective today; the same request comes back with every status chip moved to
    Watch *and* the published bands following the document. Nothing is deployed,
    nothing is restarted and no Python changes.

    Rolled back afterwards so the rest of the module sees the seeded document,
    and read back before the rollback because the assertion is about what a
    request answered while the version was live.
    """
    before = await benchmarks_for(seeded_db_url, *BLINE)
    assert {row["cycleStatus"] for row in before["items"]} != {"watch"}, (
        "the seeded portfolio must produce more than one status for this to assert anything"
    )

    content = (
        await db.execute(
            sa.text(
                "SELECT content FROM rule_document WHERE key = :key ORDER BY version DESC LIMIT 1"
            ),
            {"key": HANDLER_PERFORMANCE_KEY},
        )
    ).scalar_one()
    await db.execute(
        sa.text(
            "INSERT INTO rule_document (key, version, effective_from, content, created_at) "
            "VALUES (:key, 2, :today, CAST(:content AS jsonb), now())"
        ),
        {
            "key": HANDLER_PERFORMANCE_KEY,
            # `utc_today()`, not `date.today()`: the loader filters
            # `effective_from <= as_of` against a UTC-derived today, so a local
            # date east of UTC near midnight runs a day ahead and this fails as
            # if the loader were broken.
            "today": utc_today(),
            "content": _retuned(content, onTrackDeviationPctMax=-50, attentionDeviationPctMin=50),
        },
    )
    await db.commit()

    try:
        after = await benchmarks_for(seeded_db_url, *BLINE)

        assert {row["cycleStatus"] for row in after["items"]} == {"watch"}
        assert after["onTrackDeviationPctMax"] == -50
        assert after["attentionDeviationPctMin"] == 50
        assert after["rulesVersion"] == 2
        # One document changed, one column moved: the deviations themselves are
        # arithmetic over the caseload and cannot follow a band.
        assert [row["deviationPct"] for row in after["items"]] == [
            row["deviationPct"] for row in before["items"]
        ]
        assert [row["complexityBand"] for row in after["items"]] == [
            row["complexityBand"] for row in before["items"]
        ]
    finally:
        await db.execute(
            sa.text("DELETE FROM rule_document WHERE key = :key AND version = 2"),
            {"key": HANDLER_PERFORMANCE_KEY},
        )
        await db.commit()


def _retuned(content: dict[str, Any], **overrides: int) -> str:
    """A copy of the document with named expressions given new values, as JSON.

    The expression values are *strings* of ZEN expressions, which is why the
    override is stringified rather than assigned: a JSON number in that slot is
    a different thing from the expression `"-50"`, and only one of them
    evaluates.
    """
    retuned: dict[str, Any] = json.loads(json.dumps(content))
    seen: set[str] = set()
    for node in retuned["nodes"]:
        for expression in node.get("content", {}).get("expressions", []):
            if expression["key"] in overrides:
                expression["value"] = str(overrides[expression["key"]])
                seen.add(expression["key"])
    assert seen == set(overrides), f"no such expression: {sorted(set(overrides) - seen)}"
    return json.dumps(retuned)


# --- the contract -------------------------------------------------------


@requires_db
async def test_the_route_declares_no_parameters_at_all(seeded_db_url: str) -> None:
    """AD-7 structurally: the contract itself offers nowhere to put a scope.

    Asserted against the published OpenAPI document rather than the function
    signature, because the contract is what a client (and a reviewer) reads.
    """
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"][BENCHMARKS]["get"]

    assert operation.get("parameters", []) == []
    assert "requestBody" not in operation
    assert set(operation["responses"]) == {"200", "401", "403"}


@requires_db
async def test_query_parameters_cannot_widen_or_change_the_scope(seeded_db_url: str) -> None:
    """The smuggling attempt: ask as Park, name Bline's portfolio anyway.

    Unknown parameters are ignored rather than rejected (FastAPI's default, and
    the safer direction). What matters is that the answer is byte-identical.
    """
    honest = await benchmarks_for(seeded_db_url, *PARK)

    for smuggled in (
        {"employerId": "1"},
        {"employerIds": "1,2,3"},
        {"scopeAll": "true"},
        {"handlerId": "1"},
        {"role": "supervisor"},
        {"sort": "-compositeDays"},
    ):
        attempt = await benchmarks_for(seeded_db_url, *PARK, params=smuggled)
        assert attempt == honest, f"{smuggled} changed the answer"


@requires_db
async def test_the_response_is_camel_case_and_carries_nothing_else(seeded_db_url: str) -> None:
    """The key sets, exactly — no `total` and no `nextCursor` on a list whose
    length is the number of handlers in scope (see `HandlerBenchmarksResponse`)."""
    payload = await benchmarks_for(seeded_db_url, *PARK)

    assert set(payload) == {
        "items",
        "portfolioCompositeDays",
        "leader",
        "laggard",
        "onTrackDeviationPctMax",
        "attentionDeviationPctMin",
        "complexityHighMin",
        "complexityMedMin",
        "rulesVersion",
    }
    for row in payload["items"]:
        assert set(row) == ROW_KEYS


@requires_db
async def test_the_response_is_never_cached(seeded_db_url: str) -> None:
    """One persona's desk must not be served to another from upstream."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *PARK)
        resp = await client.get(BENCHMARKS)

    assert resp.headers["cache-control"] == "no-store"


@requires_db
async def test_the_endpoint_requires_a_session(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.get(BENCHMARKS)

    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


@requires_db
async def test_reading_the_table_writes_no_audit_event(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """A query is not a mutation (Epic 5 preamble).

    Counted before and after rather than asserted absent, because other rows
    exist: what must not happen is this request adding one.
    """
    count = sa.select(sa.func.count()).select_from(AuditEvent)
    before = (await db.execute(count)).scalar_one()

    await benchmarks_for(seeded_db_url, *BLINE)

    await db.commit()  # a new snapshot, or the count would be the old one
    assert (await db.execute(count)).scalar_one() == before


@requires_db
async def test_the_cycle_segments_are_the_sla_strips_own(db: AsyncSession) -> None:
    """AD-2, on real data: the table's portfolio composite is the strip's three
    averages added up, for the same scope.

    Asserted through the functions `/stats/sla` itself calls rather than through
    a restated average, because the claim being made is that the two surfaces
    share one aggregation rather than two that agree today.

    The composite is the sum of the **unrounded** means, so it is deliberately
    *not* the sum of the three published tiles — on the seeded portfolio those
    differ by 0.2 days. Both halves are asserted: the composite is the exact sum,
    and each published tile is that same average rounded, so a reader can
    reconcile the column with the strip without the strip having decided the
    order.
    """
    ctx = await context_for(db, *BLINE)

    strip = await sla.sla_strip(db, ctx, SETTINGS)
    rows = await claim_repo.select_claim_columns_with_handler(db, ctx, list(sla.SAMPLE_COLUMNS))
    means = sla.segment_means_of([sla.sample_of(row) for row in rows])
    table = await handler_benchmarks(
        db,
        ctx,
        await handler_performance_for(db),
        await weights_for(db),
        await thresholds_for(db),
        SETTINGS,
    )

    exact = Decimal(0)
    for key in sla.DURATION_SEGMENTS:
        mean = means[key]
        assert mean is not None, f"the seeded portfolio has no {key} data"
        exact += mean
        # The tile is the same average, quantized to the precision it is
        # displayed and judged at — which is what makes the two reconcilable.
        tile = strip[key].value
        assert tile == float(
            mean.quantize(Decimal(1).scaleb(-strip[key].decimals), rounding=ROUND_HALF_UP)
        )

    assert table.portfolio_composite_days == float(
        exact.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    )
    # …and the sum of the published tiles is a *different* number, which is the
    # whole reason the composite is not computed from them.
    published = sum(strip[key].value or 0 for key in sla.DURATION_SEGMENTS)
    assert table.portfolio_composite_days != published


@requires_db
async def test_only_settled_claims_reach_the_settle_segment(db: AsyncSession) -> None:
    """A property of the shared mapper rather than of this aggregate, asserted
    here because this is the second consumer and the first one to be able to get
    it wrong: the projection is widened with five more columns, and a mapper
    reading them positionally would have shifted the stage flag."""
    rows = await claim_repo.select_claim_columns_with_handler(
        db,
        CallerContext(user_id=0, role=UserRole.supervisor, employer_ids=frozenset()),
        list(sla.SAMPLE_COLUMNS),
    )
    assert rows == []  # the empty-scope predicate, on the new read

    ctx = await context_for(db, *BLINE)
    rows = await claim_repo.select_claim_columns_with_handler(db, ctx, list(sla.SAMPLE_COLUMNS))
    samples = [sla.sample_of(row) for row in rows]

    assert len(samples) == len(seed_fixture.seed()["claims"])
    assert sum(1 for sample in samples if sample.is_settled) == sum(
        1 for claim in seed_fixture.seed()["claims"] if claim["stage"] == Stage.settled.value
    )
    # And the join answered a name for every row rather than dropping any.
    assert all(row.handler_name for row in rows)


# --- Story 5.5: the id a row's drill-through link filters on -------------


@requires_db
async def test_every_row_publishes_the_handler_id_the_drill_through_filters_on(
    seeded_db_url: str,
) -> None:
    """`handlerId`, present, integral and distinct per row.

    Distinct is the assertion that matters: this table's whole reason for
    grouping on an id rather than on a display name is that two handlers may
    share one, and a response that published the same id twice would collapse
    two desks into one drill-through list.
    """
    payload = await benchmarks_for(seeded_db_url, *BLINE)
    ids = [row["handlerId"] for row in payload["items"]]

    assert ids, "the full portfolio has handlers"
    assert all(isinstance(handler_id, int) for handler_id in ids)
    assert all(not isinstance(handler_id, bool) for handler_id in ids)
    assert len(set(ids)) == len(ids)


@requires_db
async def test_the_published_handler_id_is_the_one_whose_claims_the_drill_returns(
    seeded_db_url: str,
) -> None:
    """The link's target, checked against the number the row shows.

    `caseCount` is that handler's claims inside the caller's scope, and
    `filter[handlerId]` narrows the caller's scope to that handler — so the two
    are the same set counted twice. An id published for the wrong row would
    still be an integer and would still be distinct; only this equality notices.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *PARK)
        payload = (await client.get(BENCHMARKS)).json()
        for row in payload["items"]:
            drill = (
                await client.get(
                    "/dashboard/claims",
                    params={"filter[handlerId]": str(row["handlerId"])},
                )
            ).json()
            assert drill["total"] == row["caseCount"], row["handlerName"]
            assert seed_fixture.handler_id_of(row["handlerName"]) == row["handlerId"]
