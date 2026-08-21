"""Story 5.3 — the seven portfolio analytics surfaces.

Two halves, deliberately kept apart:

- The **pure** half exercises `charts_of` over synthetic caseloads — the states
  the seed cannot reach (a book with nothing settled, an employer that has cost
  nothing, an empty scope) plus the Hypothesis properties that must hold for
  *any* mix (a ranked series is ordered, is cut at its limit, and reports the
  truncation). It needs no database and runs in the lint job.
- The **DB-backed** half checks the figures against
  `seed_fixture.expected_portfolio_charts`, which recounts `seed_data.json`
  independently, and then checks the four things this story is really about.

Those four are cross-surface equalities, and they are asserted **between two
aggregates rather than against restated numbers**, which is the only form that
survives a retune of the rules:

- the severity donut's High slice *is* the High Risk card's figure,
- the stage donut's slices *are* the Settled & Closed and Under Treatment cards
  and its total *is* Total Claims,
- the employer bars sum to Total Paid,
- and the four SLA tiles *are* `/stats/sla`'s strip, field for field.

A test that compared each of those against a number from the seed would pass
just as happily with two independent implementations that happened to agree
today, which is the exact failure AD-2 and AD-10 exist to prevent.

The DB-backed tests **mutate** the seeded portfolio (the supersession test
inserts and removes a rule document). The module-scoped `seeded_db_url` fixture
rebuilds the schema for this file, so the mutation is contained here and is
rolled back within its own test regardless.
"""

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
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
from data.models import AppUser, AuditEvent, Claim, Employer
from data.models.enums import ReturnStatus, Stage, UserRole
from data.repositories.identity import employer_ids_for
from rules.engine import utc_today
from rules.parameters import DERIVATION_THRESHOLDS_KEY, thresholds_for
from services.derivations import RiskDerivation, TotalPaidDerivation
from services.worklist import charts, portfolio_charts, portfolio_summary, sla
from services.worklist.sla import sla_strip
from tests import seed_fixture
from tests.conftest import requires_db

CHARTS = "/dashboard/charts"
SUMMARY = "/dashboard/summary"
SLA = "/stats/sla"

BLINE = ("David Bline", "supervisor")
PARK = ("Jennifer Park", "supervisor")
STOKER = ("Ken Stoker", "supervisor")
ANALYST = ("David Bline", "analyst")
HANDLER = ("Sarah Williams", "handler")

#: The six distribution keys, named rather than read off a response, so a series
#: that silently disappeared from the payload would fail rather than shrink the
#: comparison. `test_portfolio_summary.py`'s `FIGURE_KEYS` discipline.
SERIES_KEYS = (
    "byStage",
    "bySeverity",
    "byRecoveryStatus",
    "byInjuryType",
    "byEmployer",
    "byState",
)

SETTINGS = Settings(database_url="postgresql://x/y")
TARGETS = sla.targets_for(SETTINGS)


# --- the pure half: `charts_of` over synthetic caseloads -----------------


def chart_claim(
    *,
    stage: Stage = Stage.treatment,
    return_status: ReturnStatus = ReturnStatus.under_treatment,
    severity: int = 50,
    injury: str = "Fracture",
    state: str = "OH",
    employer_id: int = 1,
    employer_label: str = "Toyota",
    paid: int = 0,
    pick: int | None = None,
    approve: int | None = None,
    settlement: int | None = None,
) -> charts.ChartClaim:
    """One synthetic projection row, with every field defaulted.

    Keyword-only and fully defaulted so a test can name the one or two facts it
    is actually about — a caseload written out in full would bury "these three
    claims differ only in their injury type" under nine irrelevant columns.

    `paid` lands entirely on `paid_indemnity`; the split across the three
    columns is `TotalPaidDerivation`'s business and is covered where that
    derivation is, not here.
    """
    return charts.ChartClaim(
        stage=stage,
        return_status=return_status,
        severity_score=severity,
        injury_type=injury,
        state=state,
        employer_id=employer_id,
        employer_label=employer_label,
        paid_indemnity=paid,
        paid_medical=0,
        paid_expense=0,
        sample=sla.SlaSample(
            pick_days=pick,
            approve_days=approve,
            settlement_days=settlement,
            is_settled=stage is Stage.settled,
            fully_recovered=return_status is ReturnStatus.returned_and_fully_recovered,
        ),
    )


def folded(caseload: Sequence[charts.ChartClaim]) -> charts.PortfolioCharts:
    """`charts_of` with the two computers the router injects, built directly.

    Constructed here rather than reached through the registry, which is
    `test_handler_benchmarks.py`'s convention for its pure half and the same
    argument as every other oracle in this suite: a test that asked
    `derivations.risk.for_thresholds(thresholds_for(db))` would need a database
    to check arithmetic that has none, and would agree with a `build` that had
    been wired to the wrong parameter. The bands are the seed fixture's
    restated constants, and this directory is the one the 65/35 grep allowlists.
    """
    return charts.charts_of(
        caseload,
        TARGETS,
        RiskDerivation(high_min=seed_fixture.HIGH_RISK_MIN, med_min=seed_fixture.MED_RISK_MIN),
        TotalPaidDerivation(),
    )


def test_an_empty_book_reports_empty_series_rather_than_failing() -> None:
    """The NFR-3 zero state, at the fold.

    A persona assigned to no employers is a legitimate state, and every series
    then lists nothing — `items: []`, `total: 0`, `truncated: False` — rather
    than seven zero rows or a crash on `max()` over an empty sequence. The four
    SLA tiles report `no_data`, which is `strip_of`'s own answer and the whole
    point of Story 1.5: an empty segment reports having nothing to average, it
    does not report zero.
    """
    result = folded([])

    for series in (
        result.by_stage,
        result.by_severity,
        result.by_recovery_status,
        result.by_injury_type,
        result.by_state,
    ):
        assert series.items == ()
        assert series.total == 0
        assert series.total_categories == 0
        assert series.truncated is False
    assert result.by_employer.items == ()
    assert result.by_employer.total == 0

    for metric in result.sla.values():
        assert metric.value is None
        assert metric.status is sla.SlaStatus.no_data


def test_a_category_absent_from_the_scope_is_omitted_not_zeroed() -> None:
    """The contract the UI is built against (I/O matrix, Design Note 2).

    A book with nothing in intake or investigation publishes a two-item stage
    series, not a four-item one with two zeros in it. A zero row would draw an
    invisible slice in a donut and a zero-length bar in a chart, and it would
    let a client assume a fixed row count that no scope guarantees.
    """
    result = folded(
        [
            chart_claim(stage=Stage.treatment),
            chart_claim(
                stage=Stage.settled, return_status=ReturnStatus.returned_and_fully_recovered
            ),
        ]
    )

    assert [item.key for item in result.by_stage.items] == ["treatment", "settled"]
    assert result.by_stage.total_categories == 2
    assert "intake" not in {item.key for item in result.by_stage.items}


def test_the_enum_keyed_series_publish_in_declaration_order_not_count_order() -> None:
    """A legend has a fixed reading order (see `charts._declared`).

    The counts here are deliberately ascending in declaration order, so a
    ranked implementation would emit exactly the reverse and this would fail.
    """
    caseload = [
        *[chart_claim(stage=Stage.intake) for _ in range(1)],
        *[chart_claim(stage=Stage.investigation) for _ in range(2)],
        *[chart_claim(stage=Stage.treatment) for _ in range(3)],
        *[chart_claim(stage=Stage.settled, settlement=10) for _ in range(4)],
    ]

    assert [item.key for item in folded(caseload).by_stage.items] == [
        "intake",
        "investigation",
        "treatment",
        "settled",
    ]


def test_a_tie_at_the_limit_is_broken_by_label_and_is_stable() -> None:
    """Count descending, then label ascending — and the cut lands inside a tie.

    Nine injury types, five of them tied on one claim each, so the top-8 cut has
    to choose four of the five. The rule picks the four alphabetically first
    labels and drops "Echo"; an implementation that left the order to the dict
    would drop whichever one happened to be inserted last, and would drop a
    different one after an unrelated change to the seed.

    Stability is asserted by folding a **reversed** caseload: the same claims in
    the opposite insertion order must produce the identical series, which is the
    property a dict-order implementation does not have.
    """
    caseload = [
        *[chart_claim(injury="Amputation") for _ in range(5)],
        *[chart_claim(injury="Fracture") for _ in range(3)],
        *[chart_claim(injury="Crushing") for _ in range(2)],
        *[chart_claim(injury=label) for label in ("Echo", "Delta", "Charlie", "Bravo", "Alpha")],
        *[chart_claim(injury="Burn") for _ in range(1)],
    ]

    series = folded(caseload).by_injury_type
    assert [item.label for item in series.items] == [
        "Amputation",
        "Fracture",
        "Crushing",
        "Alpha",
        "Bravo",
        "Burn",
        "Charlie",
        "Delta",
    ]
    assert series.total_categories == 9
    assert series.truncated is True
    assert series.limit == charts.INJURY_TYPE_LIMIT
    # Every claim is still counted in the total even though four labels were
    # cut: "8 of 9 categories, 16 claims" is the honest caption.
    assert series.total == len(caseload)

    assert folded(list(reversed(caseload))).by_injury_type == series


def test_a_series_exactly_at_its_limit_is_not_marked_truncated() -> None:
    """The boundary the seed reaches through Ken Stoker's eight injury types.

    `truncated` is `len(categories) > limit`, not `>=`: a chart showing all
    eight of eight is complete, and a caption reading "8 of 8" beside it would
    be an apology for nothing.
    """
    series = folded(
        [chart_claim(injury=f"Type {index}") for index in range(charts.INJURY_TYPE_LIMIT)]
    ).by_injury_type

    assert len(series.items) == charts.INJURY_TYPE_LIMIT
    assert series.total_categories == charts.INJURY_TYPE_LIMIT
    assert series.truncated is False


def test_a_scope_with_nothing_settled_reports_no_data_for_settle_and_rtw() -> None:
    """I/O matrix: a pre-settlement book still has pick and approve figures.

    `strip_of`'s own denominators — settle averages over *settled* claims and
    the RTW rate over all settled claims — so a book with none of them reports
    `no_data` for those two and real numbers for the other two. Reproduced here
    because the seeded portfolio never reaches it, and because the failure mode
    (a zero standing in for "nothing to average") is the one Story 1.5 exists
    to prevent and the one a chart would render as a green bar at the origin.
    """
    result = folded(
        [
            chart_claim(stage=Stage.treatment, pick=2, approve=4),
            chart_claim(stage=Stage.intake, pick=4, approve=6),
        ]
    )

    assert result.sla[sla.SlaMetricKey.settle].status is sla.SlaStatus.no_data
    assert result.sla[sla.SlaMetricKey.rtw_rate].status is sla.SlaStatus.no_data
    assert result.sla[sla.SlaMetricKey.pick].value == 3.0
    assert result.sla[sla.SlaMetricKey.approve].value == 5.0


def test_an_employer_with_no_spend_keeps_its_bar_and_sorts_by_label() -> None:
    """I/O matrix: a zero-spend employer is a row, and its tie is broken by label.

    The chart's subject is "the employers in this book and what they have cost",
    and an employer that has cost nothing so far is an answer to that. Dropping
    it would be a silent omission of exactly the kind `truncated` exists to
    prevent, one level down — a supervisor would have no way to tell an employer
    with no spend from an employer with no claims.
    """
    series = folded(
        [
            chart_claim(employer_id=1, employer_label="Toyota", paid=500),
            chart_claim(employer_id=2, employer_label="Whirlpool", paid=0),
            chart_claim(employer_id=3, employer_label="GM", paid=0),
        ]
    ).by_employer

    assert [(item.label, item.paid_cents) for item in series.items] == [
        ("Toyota", 500),
        ("GM", 0),
        ("Whirlpool", 0),
    ]
    assert series.total == 500
    assert series.total_categories == 3
    assert series.truncated is False
    assert series.limit is None


def test_the_two_severity_boundaries_come_off_the_derivation_that_banded() -> None:
    """The legend's caption quotes the band it was counted at, not a constant."""
    result = folded([chart_claim(severity=90)])

    assert result.high_risk_severity_min == seed_fixture.HIGH_RISK_MIN
    assert result.med_risk_severity_min == seed_fixture.MED_RISK_MIN
    assert [item.key for item in result.by_severity.items] == ["high"]


# --- the pure half: Hypothesis properties over any mix (NFR-7) -----------

#: Enough distinct labels to overshoot both limits, short enough that a shrunk
#: counterexample is readable.
LABELS = st.sampled_from([f"L{index:02d}" for index in range(20)])
CASELOADS = st.lists(
    st.builds(
        chart_claim,
        stage=st.sampled_from(list(Stage)),
        return_status=st.sampled_from(list(ReturnStatus)),
        severity=st.integers(min_value=0, max_value=100),
        injury=LABELS,
        state=LABELS,
        employer_id=st.integers(min_value=1, max_value=6),
        paid=st.integers(min_value=0, max_value=1_000_000),
    ),
    max_size=40,
)


@given(CASELOADS)
@hyp_settings(max_examples=200)
def test_a_ranked_series_is_ordered_cut_and_honest_about_it(
    caseload: list[charts.ChartClaim],
) -> None:
    """The three invariants a ranked series has for *any* caseload.

    Ordering, the cut, and the truncation flag — asserted as properties rather
    than as examples because the interesting failures in a ranking are the
    inputs nobody writes down: every label tied, one label, no labels, a tie
    that straddles the limit exactly.

    The ordering is checked as a pairwise property over the emitted items and
    *also* against the labels that were dropped: a sort that ordered the top
    eight correctly while cutting the wrong eight would satisfy the first check
    alone. Every kept item must be at least as frequent as every dropped one,
    and a kept item tied with a dropped one must sort before it by label.
    """
    result = folded(caseload)
    counted: dict[str, int] = {}
    for claim in caseload:
        counted[claim.injury_type] = counted.get(claim.injury_type, 0) + 1

    for series, limit in (
        (result.by_injury_type, charts.INJURY_TYPE_LIMIT),
        (result.by_state, charts.STATE_LIMIT),
    ):
        keys = [(item.count, item.label) for item in series.items]
        assert keys == sorted(keys, key=lambda entry: (-entry[0], entry[1]))
        assert len(series.items) == min(series.total_categories, limit)
        assert series.truncated == (series.total_categories > limit)
        assert series.limit == limit
        assert series.total == len(caseload)

    kept = {item.label for item in result.by_injury_type.items}
    for dropped, dropped_count in counted.items():
        if dropped in kept:
            continue
        for item in result.by_injury_type.items:
            # Kept beats dropped on count, or ties on count and wins on label.
            #
            # Written as two explicit clauses rather than a tuple comparison
            # because the tuple form admitted the inverse: `(item.count,
            # item.label) < (dropped_count, dropped)` is satisfied whenever
            # `item.count < dropped_count`, so the assertion accepted a kept
            # item *less* frequent than a dropped one — precisely the failure it
            # exists to catch. Verified by reintroducing it: with `_ranked`
            # patched to drop the most frequent injury type and show the ninth
            # in its place, the old form passed and this one fails.
            assert item.count > dropped_count or (
                item.count == dropped_count and item.label < dropped
            )


@given(CASELOADS)
@hyp_settings(max_examples=200)
def test_every_series_accounts_for_every_claim_in_the_caseload(
    caseload: list[charts.ChartClaim],
) -> None:
    """Six distributions over one set of claims, and they say so.

    The five counted series total the caseload's length and the employer series
    totals its spend, whatever the mix. This is the property the single-pass
    fold exists to make structural — six separate comprehensions would produce
    the same numbers until one of them was written against a filtered copy, and
    nothing but a check like this one would notice.
    """
    result = folded(caseload)

    for series in (
        result.by_stage,
        result.by_severity,
        result.by_recovery_status,
        result.by_injury_type,
        result.by_state,
    ):
        assert series.total == len(caseload)
        assert sum(item.count for item in series.items) <= len(caseload)

    assert result.by_employer.total == sum(
        claim.paid_indemnity + claim.paid_medical + claim.paid_expense for claim in caseload
    )
    assert sum(item.paid_cents for item in result.by_employer.items) == result.by_employer.total
    # An enum series is never cut, so its items *do* account for every claim.
    assert sum(item.count for item in result.by_stage.items) == len(caseload)


@given(CASELOADS)
@hyp_settings(max_examples=100)
def test_no_series_ever_emits_a_zero_row(caseload: list[charts.ChartClaim]) -> None:
    """Absent is absent — the counted series never publish a category with none.

    The employer series is exempt and is the documented exception: a zero-spend
    employer is a row, because it has claims and they have cost nothing.
    """
    result = folded(caseload)

    for series in (
        result.by_stage,
        result.by_severity,
        result.by_recovery_status,
        result.by_injury_type,
        result.by_state,
    ):
        assert all(item.count > 0 for item in series.items)


# --- the DB-backed half --------------------------------------------------
#
# `requires_db` is applied per test rather than as a module-level `pytestmark`,
# `test_handler_benchmarks.py`'s convention and its reason: everything above
# this line is pure and runs in the lint job, and a module-wide skip would
# silently take the ranking tie-break and the empty-scope series — two of the
# four things the I/O matrix asks for by name — out of every run without a
# Postgres.


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
    aggregate an empty book and every cross-surface equality below would hold
    trivially between two empty answers — the worst possible way for a test
    about agreement to pass.
    """
    user = (
        await db.scalars(sa.select(AppUser).where(AppUser.name == name, AppUser.role == role))
    ).one()
    return CallerContext(
        user_id=user.id,
        role=user.role,
        employer_ids=ALL_EMPLOYERS if user.scope_all else await employer_ids_for(db, user.id),
    )


async def charts_for(db_url: str, name: str, role: str, **kwargs: Any) -> dict[str, Any]:
    async with make_client(db_url) as client:
        await login_as(client, name, role)
        resp = await client.get(CHARTS, **kwargs)
        assert resp.status_code == 200, resp.text
        payload: dict[str, Any] = resp.json()
        return payload


async def get_json(db_url: str, name: str, role: str, path: str) -> dict[str, Any]:
    """Any GET, as a logged-in persona — for the two cross-endpoint equalities."""
    async with make_client(db_url) as client:
        await login_as(client, name, role)
        resp = await client.get(path)
        assert resp.status_code == 200, resp.text
        payload: dict[str, Any] = resp.json()
        return payload


def series(payload: dict[str, Any]) -> dict[str, Any]:
    """The six distributions, without the SLA strip and the rule provenance."""
    return {key: payload[key] for key in SERIES_KEYS}


# --- the six distributions, per persona (AC 1, 2) ------------------------


@requires_db
@pytest.mark.parametrize(
    ("name", "role"),
    [
        BLINE,  # scope_all: the whole portfolio
        PARK,  # scoped: Toyota / GM / 3M
        STOKER,  # scoped: John Deere / Lockheed — eight injury types exactly
        ANALYST,  # the second role that lands on this dashboard
        HANDLER,  # a third role, taking the identical path
    ],
)
async def test_the_series_cover_exactly_the_personas_book(
    seeded_db_url: str, name: str, role: str
) -> None:
    payload = await charts_for(seeded_db_url, name, role)
    assert series(payload) == seed_fixture.expected_portfolio_charts(name, role)


@requires_db
async def test_the_full_portfolio_supervisors_donuts_are_the_named_figures(
    seeded_db_url: str,
) -> None:
    """The I/O matrix's literals, asserted literally as well as by derivation.

    Named in the contract, so a failure here says "the seed moved or the stage
    grouping changed" rather than "the oracle and the service disagree about
    something".
    """
    payload = await charts_for(seeded_db_url, *BLINE)

    assert {item["key"]: item["count"] for item in payload["byStage"]["items"]} == {
        "intake": 6,
        "investigation": 4,
        "treatment": 28,
        "settled": 62,
    }
    assert payload["byStage"]["total"] == 100
    assert {item["key"]: item["count"] for item in payload["byRecoveryStatus"]["items"]} == {
        "under_treatment": 36,
        "returned_and_under_therapy": 17,
        "returned_and_fully_recovered": 47,
    }


@requires_db
async def test_the_ranked_series_are_cut_at_the_limits_and_say_how_far(
    seeded_db_url: str,
) -> None:
    """Top 8 of 20 injury types, top 10 of 17 states — and both say so.

    `limit` and `totalCategories` are what let the caption read "8 of 20"
    without any client holding the number 8, which is the whole reason they are
    on the wire (Design Note 7).
    """
    payload = await charts_for(seeded_db_url, *BLINE)

    injuries = payload["byInjuryType"]
    assert injuries["limit"] == 8
    assert injuries["totalCategories"] == 20
    assert injuries["truncated"] is True
    assert [item["label"] for item in injuries["items"]][:3] == [
        "Amputation",
        "Fracture",
        "Carpal Tunnel Syndrome",
    ]

    states = payload["byState"]
    assert states["limit"] == 10
    assert states["totalCategories"] == 17
    assert states["truncated"] is True


@requires_db
async def test_a_tie_at_the_seeded_limits_is_broken_by_label(seeded_db_url: str) -> None:
    """Both cuts land inside a tie on the real portfolio (Design Note 3).

    Injury ranks 7-11 all count five and state ranks 9-11 all count five, so
    "count descending" alone does not decide what is on screen. With the label
    tie-break, Eye Injury and Laceration make the injury chart and Robotic Cell
    Injury and Strain or Tear do not; AL and KY make the state chart and SC does
    not. Asserted as membership *and* absence, because an implementation with no
    tie-break would still show two of the five and would pick a different two on
    a different day.
    """
    payload = await charts_for(seeded_db_url, *BLINE)
    injuries = [item["label"] for item in payload["byInjuryType"]["items"]]
    states = [item["label"] for item in payload["byState"]["items"]]

    assert injuries[6:] == ["Eye Injury", "Laceration"]
    # All three losers of the five-way tie, not two of them: ranks 7-11 are Eye
    # Injury, Laceration, Loss of Hearing, Robotic Cell Injury and Strain or
    # Tear, so the cut keeps two and drops three. Naming only two of the three
    # dropped would pass for an implementation that kept the wrong one.
    assert "Loss of Hearing" not in injuries
    assert "Robotic Cell Injury" not in injuries
    assert "Strain or Tear — Shoulder" not in injuries

    assert states[8:] == ["AL", "KY"]
    assert "SC" not in states

    # The tie is real: the five injury labels and the three state labels this
    # rule chose between all carry the same count, so the ordering above is the
    # tie-break's doing and not the count's.
    counts = {item["label"]: item["count"] for item in payload["byInjuryType"]["items"]}
    assert counts["Eye Injury"] == counts["Laceration"]


@requires_db
async def test_a_scope_with_exactly_the_limit_reports_no_truncation(
    seeded_db_url: str,
) -> None:
    """Ken Stoker's book holds eight distinct injury types — the boundary."""
    payload = await charts_for(seeded_db_url, *STOKER)
    injuries = payload["byInjuryType"]

    assert injuries["totalCategories"] == charts.INJURY_TYPE_LIMIT
    assert len(injuries["items"]) == charts.INJURY_TYPE_LIMIT
    assert injuries["truncated"] is False


# --- scope, not role (AC 2, AD-7) ----------------------------------------


@requires_db
async def test_a_scoped_supervisors_series_stay_inside_her_book(seeded_db_url: str) -> None:
    """Every category, employer and state is one Jennifer Park's book contains.

    The pair with Bline is the point: equal series would pass every
    "expected == seed" assertion above while scoping had silently stopped
    working.
    """
    park = await charts_for(seeded_db_url, *PARK)
    bline = await charts_for(seeded_db_url, *BLINE)

    assert [item["label"] for item in park["byEmployer"]["items"]] == ["Toyota", "GM", "3M"]
    for key in SERIES_KEYS:
        assert park[key]["total"] <= bline[key]["total"], f"{key} grew under a narrower scope"
    assert park["byStage"]["total"] == len(seed_fixture.claims_for(*PARK))
    assert park["byStage"]["total"] < bline["byStage"]["total"]
    # Her book contains no intake claim at all, so the category is absent rather
    # than present with a zero — the I/O matrix's "category absent from scope".
    assert "intake" not in {item["key"] for item in park["byStage"]["items"]}
    # Seven states, seven shown: a scope smaller than the limit is not truncated.
    assert park["byState"]["totalCategories"] == 7
    assert park["byState"]["truncated"] is False
    assert park["byInjuryType"]["totalCategories"] == 14
    assert park["byInjuryType"]["truncated"] is True


@requires_db
async def test_every_claim_behind_a_scoped_personas_charts_is_inside_her_book(
    db: AsyncSession,
) -> None:
    """AD-7's named proof, taken at the aggregate's own read path.

    The series agreeing with an oracle shows the arithmetic; this shows the
    *set*. Her context is built the way `api/deps.py` builds it, the aggregate
    is called directly, and the employers behind the rows it folded are read
    back from the database — so a widened predicate would show up as an employer
    she is not assigned to rather than as a number that happened to match.
    """
    park = (
        await db.scalars(sa.select(AppUser).where(AppUser.name == PARK[0], AppUser.role == PARK[1]))
    ).one()
    employer_ids = await employer_ids_for(db, park.id)
    ctx = CallerContext(user_id=park.id, role=park.role, employer_ids=employer_ids)

    assigned = set(
        (await db.scalars(sa.select(Employer.name).where(Employer.id.in_(employer_ids)))).all()
    )
    assert assigned == seed_fixture.employers_of(*PARK)

    result = await portfolio_charts(db, ctx, await thresholds_for(db), SETTINGS)

    short_names = seed_fixture.employer_short_names()
    assert {item.label for item in result.by_employer.items} == {
        short_names[name] for name in assigned
    }
    assert {item.employer_id for item in result.by_employer.items} <= set(employer_ids)
    assert result.by_stage.total == len(seed_fixture.claims_for(*PARK))


@requires_db
async def test_the_analyst_reads_byte_identically_to_the_supervisor(
    seeded_db_url: str,
) -> None:
    """No role branch anywhere in the service path (AD-7).

    David Bline holds both a supervisor row and an analyst row over the same
    unbounded scope, so the two responses must be indistinguishable — not
    "equivalent", identical. A difference of any kind would be a role branch
    somewhere in a path that is supposed to have none.
    """
    supervisor = await charts_for(seeded_db_url, *BLINE)
    analyst = await charts_for(seeded_db_url, *ANALYST)

    assert analyst == supervisor


@requires_db
async def test_a_handler_may_read_the_charts_for_her_employers(seeded_db_url: str) -> None:
    """The access decision, pinned as a contract rather than left as an omission.

    This endpoint is ungated where `/dashboard/handler-benchmarks` is not, and
    the discriminator is whether the payload reveals a named other person's
    performance. It does not: there is no handler dimension on it, so what a
    handler receives is a set of counts over claims she can already read one by
    one. The route docstring carries the argument; this is what makes it a
    decision instead of an accident.

    The absence of a handler dimension is asserted too, because that absence is
    what the argument rests on — a later story adding one has to fail here and
    revisit the gate rather than quietly widening what an ungated route
    publishes.
    """
    payload = await charts_for(seeded_db_url, *HANDLER)

    assert payload["byStage"]["total"] == len(seed_fixture.claims_for(*HANDLER))

    # Asserted structurally — the payload's *keys* are the six named series and
    # the SLA strip, and none of them is a handler cut.
    #
    # Not by searching the serialised body for persona names, which was the
    # first form and is a false-failure waiting to happen: a handler surname
    # that is also a substring of an injury type, an employer short name or a
    # two-letter state code would fail this test for a payload that is entirely
    # correct. What the argument rests on is the *absence of a dimension*, and a
    # dimension is a key.
    assert set(payload) == {
        *SERIES_KEYS,
        "sla",
        "highRiskSeverityMin",
        "medRiskSeverityMin",
        "rulesVersion",
    }
    labelled = {
        item["label"]
        for key in ("byInjuryType", "byEmployer", "byState")
        for item in payload[key]["items"]
    }
    assert labelled.isdisjoint(seed_fixture.handler_personas())


# --- the cross-surface equalities (AC 2, AC 3) ---------------------------


@requires_db
@pytest.mark.parametrize(("name", "role"), [BLINE, PARK, STOKER])
async def test_the_severity_donut_high_slice_equals_the_high_risk_card(
    db: AsyncSession, name: str, role: str
) -> None:
    """AC 2's first half, asserted between the two aggregates.

    Not "the High slice is 32". Both surfaces are called on one scope with one
    `DerivationThresholds`, and their answers are compared — so a retune of the
    band moves both or fails here, and no restated number can go stale. The
    published cut-off is compared too: the card's caption and the donut's legend
    quote the same document.
    """
    ctx = await context_for(db, name, role)
    thresholds = await thresholds_for(db)

    cards = await portfolio_summary(db, ctx, thresholds)
    donuts = await portfolio_charts(db, ctx, thresholds, SETTINGS)
    high = {item.key: item.count for item in donuts.by_severity.items}.get("high", 0)

    assert high == cards.high_risk
    assert donuts.high_risk_severity_min == cards.high_risk_severity_min


@requires_db
async def test_the_stage_donut_agrees_with_the_three_cards_it_shares_a_screen_with(
    db: AsyncSession,
) -> None:
    """AC 2's second half — the ruling Story 5.1 made, held in place.

    `stage = settled` counts 62 of the seeded 100 where `status = settled_closed`
    counts 54. A donut keyed on `status` would sit inches from a card reading 62
    and show 54, which is the failure AD-10 exists to prevent; this is what stops
    it drifting back. The `status` count is asserted to be *different* as well,
    because an equality alone would pass on a portfolio where the two rules
    happened to agree and would stop asserting anything.
    """
    ctx = await context_for(db, *BLINE)
    thresholds = await thresholds_for(db)

    cards = await portfolio_summary(db, ctx, thresholds)
    donuts = await portfolio_charts(db, ctx, thresholds, SETTINGS)
    slices = {item.key: item.count for item in donuts.by_stage.items}

    assert slices["settled"] == cards.settled_closed
    assert slices["treatment"] == cards.under_treatment
    assert donuts.by_stage.total == cards.total_claims

    by_status = sum(
        1 for claim in seed_fixture.seed()["claims"] if claim["status"] == "settled_closed"
    )
    assert by_status != slices["settled"], (
        "the stage and status rules no longer differ — this test has stopped asserting"
    )


@requires_db
async def test_the_employer_series_sums_to_the_total_paid_card(db: AsyncSession) -> None:
    """AC 2's third half, and Design Note 8's load-bearing assertion.

    `TotalPaidDerivation` excludes $335,985 of `status = paid` bills that the
    Bills tab shows, which is a recorded, owned product deferral rather than a
    defect. The employer bars inherit that gap deliberately, because the
    alternative is two different "total paid" figures on one screen. This
    equality is what makes the choice load-bearing: if somebody later fixes the
    derivation, the card and the bars move together or this fails.
    """
    ctx = await context_for(db, *BLINE)
    thresholds = await thresholds_for(db)

    cards = await portfolio_summary(db, ctx, thresholds)
    bars = (await portfolio_charts(db, ctx, thresholds, SETTINGS)).by_employer

    assert sum(item.paid_cents for item in bars.items) == cards.total_paid_cents
    assert bars.total == cards.total_paid_cents
    assert bars.total_categories == cards.employer_count


@requires_db
@pytest.mark.parametrize(("name", "role"), [BLINE, PARK, STOKER, HANDLER])
async def test_the_sla_tiles_are_the_top_bar_strip(db: AsyncSession, name: str, role: str) -> None:
    """AC 3, asserted field for field against the aggregation itself (AD-2).

    Not "the tiles look right": `sla_strip` — the function `/stats/sla` calls —
    is run over the same scope, and every field of every metric is compared,
    `no_data` included. One `strip_of` produced both, so the only way these can
    diverge is if somebody writes a second one, which is precisely what the
    column grep forbids and what this notices if the grep is ever relaxed.
    """
    ctx = await context_for(db, name, role)

    strip = await sla_strip(db, ctx, SETTINGS)
    tiles = (await portfolio_charts(db, ctx, await thresholds_for(db), SETTINGS)).sla

    assert dict(tiles) == dict(strip)
    for key, metric in tiles.items():
        assert (metric.value is None) == (metric.status is sla.SlaStatus.no_data), key


@requires_db
async def test_the_dashboard_tiles_and_the_stats_endpoint_answer_the_same_json(
    seeded_db_url: str,
) -> None:
    """AC 3 at the wire, which is where the two tiles actually sit side by side.

    The service-level equality above proves one fold; this proves the two
    *endpoints* serialise it identically, which is the part a component test can
    see. `SlaStripResponse` is imported by the dashboard router rather than
    redeclared, so this is a check on that import rather than on two models
    agreeing.
    """
    charts_payload = await charts_for(seeded_db_url, *PARK)
    strip_payload = await get_json(seeded_db_url, *PARK, path=SLA)

    assert charts_payload["sla"] == strip_payload


# --- the thresholds are the document's (AD-8) ----------------------------


@requires_db
async def test_the_published_thresholds_come_from_the_rule_document(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """Compared against the JDM resolution path, never against a literal.

    A test asserting `highRiskSeverityMin == 65` would keep passing if the
    endpoint had hardcoded it, which is the one thing worth checking here.
    """
    thresholds = await thresholds_for(db)
    payload = await charts_for(seeded_db_url, *BLINE)

    assert payload["highRiskSeverityMin"] == thresholds.risk_high_min
    assert payload["medRiskSeverityMin"] == thresholds.risk_med_min
    assert payload["rulesVersion"] == thresholds.version


@requires_db
async def test_a_superseded_rule_document_moves_the_bands(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """AD-8 end to end, on the one surface here that reads a rule.

    A v7 of `derivation_thresholds` lowering `riskHighMin` is inserted effective
    today; the same request comes back with a larger High slice *and* a lower
    published boundary, so the legend's caption follows the document. Nothing is
    deployed, nothing is restarted and no Python changes.

    The five series that read no rule must not move at all, which is the other
    half of the assertion: a retune that shifted the state chart would mean
    something in the fold was reading a threshold it has no business reading.
    """
    lowered = 40
    before = await charts_for(seeded_db_url, *BLINE)

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
            # date east of UTC near midnight runs a day ahead, the document is
            # not yet effective, and this fails as if the loader were broken.
            "today": utc_today(),
            "content": json.dumps(_retuned(content, riskHighMin=lowered)),
        },
    )
    await db.commit()

    try:
        after = await charts_for(seeded_db_url, *BLINE)
        expected = sum(
            1 for claim in seed_fixture.seed()["claims"] if claim["severity_score"] >= lowered
        )
        high_before = {item["key"]: item["count"] for item in before["bySeverity"]["items"]}["high"]
        high_after = {item["key"]: item["count"] for item in after["bySeverity"]["items"]}["high"]

        assert expected > high_before, "the retune must actually widen the band"
        assert high_after == expected
        assert after["highRiskSeverityMin"] == lowered
        assert after["rulesVersion"] == 7
        # One parameter changed, one donut changed.
        assert after["byStage"] == before["byStage"]
        assert after["byState"] == before["byState"]
        assert after["byEmployer"] == before["byEmployer"]
        assert after["sla"] == before["sla"]
    finally:
        await db.execute(
            sa.text("DELETE FROM rule_document WHERE key = :key AND version = 7"),
            {"key": DERIVATION_THRESHOLDS_KEY},
        )
        await db.commit()


def _retuned(content: dict[str, Any], **overrides: int) -> dict[str, Any]:
    """A copy of a thresholds document with named expressions given new values.

    `test_portfolio_summary.py`'s helper, restated: the document's expression
    values are *strings* of ZEN expressions, so the override is stringified
    rather than assigned — a JSON number in that slot is a different thing from
    the expression `"40"`, and only one of them evaluates.
    """
    retuned: dict[str, Any] = json.loads(json.dumps(content))
    seen: set[str] = set()
    for node in retuned["nodes"]:
        for expression in node.get("content", {}).get("expressions", []):
            if expression["key"] in overrides:
                expression["value"] = str(overrides[expression["key"]])
                seen.add(expression["key"])
    assert seen == set(overrides), f"no such expression: {sorted(set(overrides) - seen)}"
    return retuned


# --- the contract --------------------------------------------------------


@requires_db
async def test_the_route_declares_no_parameters_at_all(seeded_db_url: str) -> None:
    """AD-7 structurally: the contract itself offers nowhere to put a scope.

    Asserted against the published OpenAPI document rather than the function
    signature, because the contract is what a client (and a reviewer) reads.
    """
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"][CHARTS]["get"]

    assert operation.get("parameters", []) == []
    assert "requestBody" not in operation


@requires_db
async def test_query_parameters_cannot_widen_or_change_the_scope(seeded_db_url: str) -> None:
    """The smuggling attempt: ask as Park, name Bline's portfolio anyway.

    Unknown parameters are ignored rather than rejected (FastAPI's default, and
    the safer direction — a 422 here would tell an attacker which parameter
    names exist). What matters is that the answer is byte-identical.
    """
    honest = await charts_for(seeded_db_url, *PARK)

    for smuggled in (
        {"employerId": "3"},
        {"employerIds": "1,2,3"},
        {"scopeAll": "true"},
        {"limit": "20"},
        {"sort": "-paidCents"},
    ):
        attempt = await charts_for(seeded_db_url, *PARK, params=smuggled)
        assert attempt == honest, f"{smuggled} changed the answer"


@requires_db
async def test_the_response_is_camel_case_and_carries_nothing_else(
    seeded_db_url: str,
) -> None:
    payload = await charts_for(seeded_db_url, *PARK)

    assert set(payload) == {
        *SERIES_KEYS,
        "sla",
        "highRiskSeverityMin",
        "medRiskSeverityMin",
        "rulesVersion",
    }
    assert set(payload["byStage"]) == {"items", "total", "totalCategories", "truncated", "limit"}
    assert set(payload["byStage"]["items"][0]) == {"key", "count"}
    assert set(payload["byInjuryType"]["items"][0]) == {"label", "count"}
    assert set(payload["byEmployer"]["items"][0]) == {"employerId", "label", "paidCents"}


@requires_db
async def test_the_response_is_never_cached(seeded_db_url: str) -> None:
    """One persona's portfolio must not be served to another from upstream."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *PARK)
        resp = await client.get(CHARTS)
    assert resp.headers["cache-control"] == "no-store"


@requires_db
async def test_the_endpoint_requires_a_session(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.get(CHARTS)

    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


@requires_db
async def test_reading_the_dashboard_writes_no_audit_event(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """A query is not a mutation (Epic 5 preamble).

    Counted before and after rather than asserted absent, because other rows
    exist: what must not happen is this request adding one.
    """
    count = sa.select(sa.func.count()).select_from(AuditEvent)
    before = (await db.execute(count)).scalar_one()

    await charts_for(seeded_db_url, *BLINE)

    await db.commit()  # a new snapshot, or the count would be the old one
    assert (await db.execute(count)).scalar_one() == before


@requires_db
async def test_the_aggregate_takes_exactly_one_scoped_read(db: AsyncSession) -> None:
    """ "One scoped read, one pure fold" as a counted fact rather than a claim.

    The module docstring and the function docstring both say there is exactly
    one `await` in the body. Nothing enforced that, and the cheapest way for it
    to stop being true is a second read added for one more column. Counting the
    statements the session executes is blunt and is the only check that would
    notice.
    """
    ctx = await context_for(db, *BLINE)
    thresholds = await thresholds_for(db)

    executed: list[str] = []
    original = db.execute

    async def counting(statement: Any, *args: Any, **kwargs: Any) -> Any:
        executed.append(str(statement).split("\n")[0])
        return await original(statement, *args, **kwargs)

    db.execute = counting  # type: ignore[method-assign]
    try:
        await portfolio_charts(db, ctx, thresholds, SETTINGS)
    finally:
        db.execute = original  # type: ignore[method-assign]

    assert len(executed) == 1, executed
    assert executed[0].startswith("SELECT"), executed


@requires_db
async def test_the_projection_never_names_a_claim_outside_the_scope_predicate(
    db: AsyncSession,
) -> None:
    """The empty book, through the real read path rather than the pure fold.

    `employer_scope` reads "assigned to no employers" as a predicate matching
    nothing rather than as "no filter", and that distinction is the whole of
    AD-7's mechanism. The pure test above proves the fold survives an empty
    caseload; this proves the read produces one.
    """
    result = await portfolio_charts(
        db,
        CallerContext(user_id=0, role=UserRole.supervisor, employer_ids=frozenset()),
        await thresholds_for(db),
        SETTINGS,
    )

    assert result.by_stage.items == ()
    assert result.by_employer.items == ()
    assert result.by_stage.total == 0
    assert all(metric.status is sla.SlaStatus.no_data for metric in result.sla.values())
    # The bands still arrive: "nothing in the book" says nothing about which
    # rules were in force.
    assert result.high_risk_severity_min == (await thresholds_for(db)).risk_high_min


@requires_db
async def test_the_claim_projection_reads_the_employer_label_from_the_join(
    db: AsyncSession,
) -> None:
    """The label is `employer.short_name`, and it comes from the one read.

    Asserted against the table rather than against the seed file, because what
    is being checked is that the repository's join landed on the right column —
    the prototype produces these labels by stripping four suffixes off the full
    name with a chain of `String.replace`, and a service that shipped
    `Employer.name` instead would render "Toyota Motor Manufacturing" on a bar
    sized for "Toyota".
    """
    ctx = await context_for(db, *BLINE)

    bars = (await portfolio_charts(db, ctx, await thresholds_for(db), SETTINGS)).by_employer.items
    rows = (await db.execute(sa.select(Employer.id, Employer.short_name))).all()
    stored: dict[int, str] = {row.id: row.short_name for row in rows}

    assert {item.employer_id: item.label for item in bars} == {
        item.employer_id: stored[item.employer_id] for item in bars
    }
    assert all(item.label == stored[item.employer_id] for item in bars)
    # Every employer that has a claim, and no other.
    with_claims = set((await db.scalars(sa.select(Claim.employer_id).distinct())).all())
    assert {item.employer_id for item in bars} == with_claims
