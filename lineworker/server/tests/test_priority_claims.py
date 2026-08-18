"""Story 5.4 — the supervisor's priority claims worklist.

Three parts, deliberately kept apart:

- The **pure** part exercises `rank` over synthetic caseloads: each arm of the
  population alone, a claim in two arms, a claim in none, and the cap. It needs
  no database and runs in the lint job.
- The **cursor** part decodes forged and stale tokens with no database either —
  `test_diary_notes.py`'s block, and its parametrization.
- The **DB-backed** part checks the rows against
  `seed_fixture.expected_priority_claims`, which recounts `seed_data.json`
  independently, and then checks the four things this story is really about.

Those four are cross-implementation equalities, and each is asserted **between
two call sites of one function** rather than against a restated answer — which
is the only form that survives a retune:

- the order *is* `priority.priority_score` sequenced by `priority.order_key`,
- each `nextBestAction` *is* `actions.generate_actions(...)[0].label`,
- the row count *is* the `worklist_actions` document's cap,
- and `tests/test_claims_queue.py` still passes unmodified, which is the proof
  that lifting `order_key` out of `queue._ranked_group` changed no behaviour.

A test that compared any of those against a number or a string typed into this
file would pass just as happily with two independent implementations that
happened to agree today, which is the exact failure AD-2 and AD-10 exist to
prevent.

The DB-backed tests **mutate** the seeded portfolio (two supersession tests
insert and remove a rule document). The module-scoped `seeded_db_url` fixture
rebuilds the schema for this file, so the mutation is contained here and is
rolled back within its own test regardless.
"""

import base64
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, AuditEvent
from data.models.enums import (
    ActionKey,
    ActionUrgency,
    ClaimStatus,
    RecoveryWindow,
    ReturnStatus,
    Stage,
    UserRole,
)
from data.repositories import claims as claim_repo
from data.repositories.identity import employer_ids_for
from rules.engine import utc_today
from rules.parameters import (
    CURSOR_PAGE_CEILING,
    DERIVATION_THRESHOLDS_KEY,
    PRIORITY_WEIGHTS_KEY,
    WORKLIST_ACTIONS_KEY,
    DerivationThresholds,
    PriorityWeights,
    RuleParameterError,
    WorklistActions,
    thresholds_for,
    weights_for,
    worklist_actions_for,
)
from services.derivations import RiskBand
from services.worklist import actions as action_rules
from services.worklist import priority
from services.worklist.priority_claims import (
    Cursor,
    InvalidCursor,
    PriorityClaim,
    decode_cursor,
    encode_cursor,
    priority_claims,
    rank,
)
from services.worklist.queue import MAX_PAGE_LIMIT
from tests import seed_fixture
from tests.conftest import requires_db

WORKLIST = "/dashboard/priority-claims"
QUEUE = "/claims/queue"

BLINE = ("David Bline", "supervisor")
PARK = ("Jennifer Park", "supervisor")
ANALYST = ("David Bline", "analyst")
HANDLER = ("Sarah Williams", "handler")

#: The twelve wire keys of one row, named rather than read off a response, so a
#: column that silently disappeared from the payload would fail the comparison
#: rather than shrink it. `test_portfolio_charts.py`'s `SERIES_KEYS` discipline.
ROW_KEYS = {
    "claimId",
    "worker",
    "employerShortName",
    "injuryType",
    "severityBand",
    "fraudScore",
    "fraudFlagged",
    "handlerName",
    "daysOpen",
    "nextBestAction",
    "stage",
    "litigationFlag",
}

TODAY = date(2026, 8, 18)

#: The two rule blocks the pure half needs, restated rather than loaded.
#:
#: `test_priority_score.py`'s discipline, for its reason: an expectation
#: computed with the block the code under test read agrees with it however wrong
#: both are. `tests/test_rules_engine.py` is what ties these numbers back to the
#: committed documents, and the DB-backed half below is what proves a real
#: document change moves a real table.
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
    version=5,
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
)


# --- the pure half: `rank` over synthetic caseloads ----------------------


def priority_claim(
    claim_id: str = "WC-0001",
    *,
    stage: Stage = Stage.intake,
    status: ClaimStatus = ClaimStatus.ch_approved,
    return_status: ReturnStatus = ReturnStatus.returned_and_fully_recovered,
    severity: int = 10,
    days_open: int = 0,
    fraud_flag: bool = False,
    fraud_score: int = 0,
    litigation_flag: bool = False,
    surgery_required: bool = False,
) -> PriorityClaim:
    """One synthetic projection row, with every field defaulted to *quiet*.

    Keyword-only and fully defaulted so a test can name the one or two facts it
    is actually about — a caseload written out in full would bury "these three
    claims differ only in which arm puts them in the population" under sixteen
    irrelevant columns. `chart_claim`'s shape in `test_portfolio_charts.py`.

    The defaults are chosen so the baseline claim **does not qualify**: intake
    stage, no fraud flag, no litigation. Every population test therefore turns
    exactly one thing on, and the delta *is* the arm under test.
    """
    return PriorityClaim(
        claim_id=claim_id,
        stage=stage,
        status=status,
        return_status=return_status,
        severity_score=severity,
        days_open=days_open,
        injury_type="Laceration",
        worker_name="Dana Reyes",
        employer_short_name="3M",
        handler_name="Sarah Williams",
        surgery_required=surgery_required,
        litigation_flag=litigation_flag,
        fraud_flag=fraud_flag,
        fraud_score=fraud_score,
        osha_recordable=False,
        osha_logged=True,
        attorney_rep=False,
        rtw_rec=None,
        actual_rtw=None,
    )


def ranked_ids(caseload: Sequence[PriorityClaim], cap: int = 30) -> tuple[list[str], int]:
    """`rank` over the seeded blocks, reduced to the ids and the population size."""
    members, total = rank(caseload, SEEDED_THRESHOLDS, SEEDED_WEIGHTS, cap)
    return [claim.claim_id for claim, _flags in members], total


def test_a_claim_in_active_treatment_is_in_the_population() -> None:
    """The first arm, and it is the **stage** rather than the status.

    Story 5.1 ruled on exactly this for the KPI cards (`summary.py`): the
    console groups by stage everywhere, and a worklist that read `status` here
    would disagree with the Under Treatment card sitting three inches above it
    on the same screen.
    """
    ids, total = ranked_ids([priority_claim("WC-0001", stage=Stage.treatment)])

    assert ids == ["WC-0001"]
    assert total == 1


def test_a_fraud_flagged_claim_is_in_the_population_at_the_review_threshold() -> None:
    """The second arm — the dashboard's *review* rule, not the queue's referral one.

    Both conditions, not either: `FraudFlaggedDerivation` requires the flag
    *and* the score, so a scored-but-untriaged claim stays out. The threshold is
    the block's `fraud_flag_score_min`, which is deliberately lower than
    `siu_fraud_score_min` — 13 seeded claims against 9 — and a claim scoring
    between the two is the case that tells the two rules apart.
    """
    between = (SEEDED_THRESHOLDS.fraud_flag_score_min + SEEDED_THRESHOLDS.siu_fraud_score_min) // 2
    ids, total = ranked_ids(
        [
            priority_claim("WC-0001", fraud_flag=True, fraud_score=between),
            # Scored, never triaged: the flag is missing, so the review rule
            # does not fire however high the score.
            priority_claim("WC-0002", fraud_flag=False, fraud_score=100),
            # Flagged, but below the review cut-off.
            priority_claim(
                "WC-0003",
                fraud_flag=True,
                fraud_score=SEEDED_THRESHOLDS.fraud_flag_score_min - 1,
            ),
        ]
    )

    assert ids == ["WC-0001"]
    assert total == 1


def test_a_litigated_claim_is_in_the_population_whatever_its_stage() -> None:
    """The third arm, and it is a stored column rather than a derivation.

    Settled and litigated is the case worth stating: the claim is out of
    treatment and carries no fraud flag, so the union is the only thing putting
    it in the table at all — a filter chain would have dropped it.
    """
    ids, total = ranked_ids([priority_claim("WC-0001", stage=Stage.settled, litigation_flag=True)])

    assert ids == ["WC-0001"]
    assert total == 1


def test_a_claim_matching_two_arms_appears_exactly_once() -> None:
    """Union semantics, which is the whole reason `_qualifies` is one expression.

    Written as three narrowing filters — the shape a `WHERE` clause invites —
    the arms would intersect rather than unite; written as three concatenated
    lists they would duplicate. This claim is in treatment *and* fraud-flagged
    *and* litigated, and it is one row.
    """
    ids, total = ranked_ids(
        [
            priority_claim(
                "WC-0001",
                stage=Stage.treatment,
                fraud_flag=True,
                fraud_score=90,
                litigation_flag=True,
            )
        ]
    )

    assert ids == ["WC-0001"]
    assert total == 1


def test_a_claim_in_none_of_the_three_arms_is_excluded() -> None:
    """The negative case, without which every assertion above is vacuous.

    A high-severity, surgical, payment-due claim in investigation is exactly the
    row somebody would expect to see on a "priority" table and which this
    population deliberately does not contain — the three arms are the story's
    definition, not "the claims that score highest".
    """
    ids, total = ranked_ids(
        [priority_claim("WC-0001", stage=Stage.investigation, severity=99, surgery_required=True)]
    )

    assert ids == []
    assert total == 0


def test_the_cap_takes_the_highest_scoring_claims_not_the_first_ones() -> None:
    """The prototype's defect, as a test.

    `renderSV` cuts with `.slice(0, 30)` over a filter in *dataset order*, so
    its "priority claims" table is priority-ordered in its heading and nowhere
    else. Here the input arrives worst-first and the cap keeps the best: a cut
    taken before the sort would return `WC-0001`.
    """
    caseload = [
        priority_claim("WC-0001", stage=Stage.treatment, severity=1),
        priority_claim("WC-0002", stage=Stage.treatment, severity=50),
        priority_claim("WC-0003", stage=Stage.treatment, severity=99),
    ]

    ids, total = ranked_ids(caseload, cap=1)

    assert ids == ["WC-0003"]
    # The population is reported *before* the cap — the caption reads "top 1 of
    # 3", and a `total` of 1 would tell a supervisor her book held one claim.
    assert total == 3


def test_the_cap_is_the_callers_number_and_this_function_holds_none() -> None:
    """AD-8 at the seam: `rank` has no cap of its own to fall back on.

    Two different caps over one caseload, so a hardcoded thirty (or six, or any
    other number this codebase already contains) could not satisfy both.
    """
    caseload = [priority_claim(f"WC-{n:04d}", stage=Stage.treatment) for n in range(1, 6)]

    assert len(ranked_ids(caseload, cap=2)[0]) == 2
    assert len(ranked_ids(caseload, cap=4)[0]) == 4


def as_queue_claim(claim: PriorityClaim) -> priority.QueueClaim:
    """The projection narrowed to what the scorer reads, restated for this file.

    The service has its own `_queue_claim` doing the same eleven-field mapping,
    and this is deliberately *not* that function: an oracle that called the
    private helper under test would agree with it whichever fields it dropped.
    Named field by field for the same reason the service's is — a positional
    mapping over four adjacent free-text columns type-checks and runs.
    """
    return priority.QueueClaim(
        claim_id=claim.claim_id,
        stage=claim.stage,
        status=claim.status,
        severity_score=claim.severity_score,
        days_open=claim.days_open,
        injury_type=claim.injury_type,
        worker_name=claim.worker_name,
        employer_short_name=claim.employer_short_name,
        surgery_required=claim.surgery_required,
        litigation_flag=claim.litigation_flag,
        fraud_flag=claim.fraud_flag,
    )


def test_the_order_is_story_2_1s_scorer_sequenced_by_its_own_order_key() -> None:
    """AC 3, asserted against the two functions rather than a copied sequence.

    `rank` is compared with `priority_score` + `order_key` applied to the same
    claims — the same two symbols it calls, from the module that owns them. That
    is circular only in the sense it *should* be: what it catches is the single
    most plausible way to get this wrong, a second ordering written here that
    agrees on every score and disagrees on every tie. The caseload carries
    deliberate ties (three claims identical but for their id) so the tie-break is
    exercised rather than assumed, and they are declared out of order so a
    stable-sort no-op would fail.

    **The population arm is litigation, not treatment, and that is what makes
    the flags in the expectation honest.** Two of the five registered
    derivations — `rtw_blocked` and `payment_due` — are demo hash buckets over
    the *claim id* and fire only on treatment claims (`queue_flags.py`), so a
    caseload in treatment would have three "identical" claims scoring
    differently by the digits of their business ids, and an expectation stating
    one set of flags for all of them would be wrong. Litigated intake claims
    turn all four scorer-visible flags off deterministically, which leaves
    severity and the tie-break as the only things deciding this order — which is
    what the test is about.
    """
    caseload = [
        priority_claim("WC-0009", litigation_flag=True, severity=50),
        priority_claim("WC-0007", litigation_flag=True, severity=50),
        priority_claim("WC-0008", litigation_flag=True, severity=50),
        priority_claim("WC-0001", litigation_flag=True, severity=99),
        priority_claim("WC-0002", litigation_flag=True, severity=10),
    ]
    quiet = priority.QueueFlags(
        risk=RiskBand.med, siu_review=False, rtw_blocked=False, payment_due=False
    )

    ids, _total = ranked_ids(caseload)

    expected = sorted(
        caseload,
        key=lambda claim: priority.order_key(
            (
                as_queue_claim(claim),
                priority.priority_score(as_queue_claim(claim), quiet, SEEDED_WEIGHTS),
            )
        ),
    )

    # The four flags above are off for every member, which the expectation
    # depends on absolutely — asserted rather than assumed, because a claim that
    # turned one on would make this oracle wrong in a way that reads exactly
    # like an implementation bug.
    assert all(claim.stage is not Stage.treatment for claim in caseload)
    assert all(claim.fraud_flag is False for claim in caseload)
    assert ids == [claim.claim_id for claim in expected]
    # And the tie-break really was exercised: the three fifties are adjacent and
    # in ascending business-id order.
    assert ids[1:4] == ["WC-0007", "WC-0008", "WC-0009"]


def test_the_ordering_key_is_the_one_the_queue_sorts_with() -> None:
    """The extraction, asserted as an identity rather than as behaviour.

    `queue._ranked_group` and `rank` both call `priority.order_key`, and this is
    the check that the symbol they call is the same object — the cheapest form
    of "one definition, two callers", and the one that would fail the day
    somebody re-inlined the lambda in either place.
    """
    from services.worklist import queue

    # Read out of the module's namespace rather than as an attribute, because
    # `queue` does not re-export the name and mypy's strict mode is right to say
    # so: the import there exists for the sort inside `_ranked_group`, not as
    # part of that module's surface. What is being asserted is that the object
    # bound in the queue's namespace is the same object this module's ordering
    # comes from, which is exactly what a namespace lookup answers.
    assert vars(queue)["order_key"] is priority.order_key


def test_the_projection_satisfies_the_action_generators_protocol() -> None:
    """`PriorityClaim` is an `ActionClaim` by shape, the way `ChartClaim` is a
    `PaidColumns`.

    Asserted at runtime rather than left to a type-checker nobody runs at review
    time: the conformance is what lets this module hand its own projection
    straight to `generate_actions` instead of building a second stand-in, and it
    would break silently the day a rule started reading an eleventh column.
    """
    claim = priority_claim("WC-0001", stage=Stage.treatment)

    for member in (
        "stage",
        "status",
        "return_status",
        "surgery_required",
        "osha_recordable",
        "osha_logged",
        "litigation_flag",
        "attorney_rep",
        "rtw_rec",
        "actual_rtw",
    ):
        assert hasattr(claim, member), f"ActionClaim needs {member}"

    # And it is accepted where the protocol is asked for, not merely shaped like
    # it: the generator is called with the projection itself.
    generated = action_rules.generate_actions(
        claim,
        documents=(),
        bills=(),
        weeks=(),
        latest_note_at=None,
        flags=action_rules.ClaimFlags(siu_review=False, rtw_blocked=False, payment_due=False),
        params=ACTION_PARAMS,
        as_of=TODAY,
    )
    assert generated, "a treatment claim should produce at least a routine review row"


#: The action-checklist parameters, restated at the committed document's values.
#:
#: `test_action_checklist.py`'s block, which this file cannot import from
#: (a test module importing another test module's fixtures is how two suites
#: start sharing a wrong expectation). The two supervisor-worklist numbers are
#: what this story added; the eleven urgencies are 3.5's.
ACTION_PARAMS = WorklistActions(
    version=2,
    cap=6,
    padding_floor=3,
    urgencies={
        ActionKey.assessment_approval: ActionUrgency.high,
        ActionKey.siu_escalation: ActionUrgency.high,
        ActionKey.overdue_rtw: ActionUrgency.high,
        ActionKey.surgical_pre_auth: ActionUrgency.high,
        ActionKey.bill_review: ActionUrgency.medium,
        ActionKey.payment_confirmation: ActionUrgency.medium,
        ActionKey.osha_log: ActionUrgency.medium,
        ActionKey.defense_counsel: ActionUrgency.medium,
        ActionKey.modified_duty: ActionUrgency.medium,
        ActionKey.diary_check_in: ActionUrgency.low,
        ActionKey.routine_review: ActionUrgency.low,
    },
    supervisor_worklist_cap=30,
    supervisor_worklist_page_limit=10,
)


# --- the cursor, without a database -------------------------------------


def a_cursor(**changes: Any) -> Cursor:
    """A well-formed cursor, with named fields replaced."""
    return replace(
        Cursor(
            offset=10,
            limit=10,
            weights_version=1,
            thresholds_version=5,
            actions_version=2,
            as_of=TODAY,
        ),
        **changes,
    )


def test_a_cursor_round_trips() -> None:
    assert decode_cursor(encode_cursor(a_cursor()), TODAY) == a_cursor()


@pytest.mark.parametrize(
    "raw",
    [
        "not-base64-at-all!!",
        # Valid base64 of JSON that is not a cursor.
        "e30",
        # A position that cannot exist.
        encode_cursor(a_cursor(offset=-1)),
        # A forged page size past the route's ceiling. This is the *only* way a
        # page size could be smuggled into this endpoint — it declares no
        # `limit` parameter at all — so leaving it unbounded here would be
        # leaving it unbounded everywhere.
        encode_cursor(a_cursor(limit=100_000)),
        encode_cursor(a_cursor(limit=0)),
        # Dated in the future: no cursor this service issued can name one.
        encode_cursor(a_cursor(as_of=TODAY + timedelta(days=1))),
        # Older than MAX_CURSOR_AGE.
        encode_cursor(a_cursor(as_of=TODAY - timedelta(days=8))),
    ],
)
def test_a_cursor_that_does_not_describe_this_list_is_refused(raw: str) -> None:
    """Never a silent page one — that turns a client bug into an infinite "Show
    more" that re-appends the same claims for ever."""
    with pytest.raises(InvalidCursor):
        decode_cursor(raw, TODAY)


def test_a_cursor_carrying_an_infinite_offset_is_refused_rather_than_crashing() -> None:
    """`json` accepts the literal `Infinity` and `int(float("inf"))` raises
    `OverflowError`, which is **not** a `ValueError` — the defect that escaped
    the queue's decoder as a 500 rather than the 400 this function exists to
    produce. `ArithmeticError` in the except tuple is what catches it."""
    forged = (
        base64.urlsafe_b64encode(b'{"o":Infinity,"l":10,"v":1,"t":5,"a":2,"d":"2026-08-18"}')
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
    `test_portfolio_charts.py`'s reason: David Bline has *no* rows in
    `user_employer_assignment`, so reading his assignments would hand the
    aggregate an empty book and every equality below would hold trivially
    between two empty answers.
    """
    user = (
        await db.scalars(sa.select(AppUser).where(AppUser.name == name, AppUser.role == role))
    ).one()
    return CallerContext(
        user_id=user.id,
        role=user.role,
        employer_ids=ALL_EMPLOYERS if user.scope_all else await employer_ids_for(db, user.id),
    )


async def worklist_for(db_url: str, name: str, role: str, **kwargs: Any) -> dict[str, Any]:
    async with make_client(db_url) as client:
        await login_as(client, name, role)
        resp = await client.get(WORKLIST, **kwargs)
        assert resp.status_code == 200, resp.text
        payload: dict[str, Any] = resp.json()
        return payload


async def walk(db_url: str, name: str, role: str) -> list[dict[str, Any]]:
    """Every page of one persona's worklist, followed to exhaustion.

    Returns the pages themselves rather than the concatenated rows, because two
    of the properties under test — `total` stable across pages, `nextCursor`
    null exactly once — are about the pages and not about the claims.
    """
    async with make_client(db_url) as client:
        await login_as(client, name, role)
        pages: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params = {} if cursor is None else {"cursor": cursor}
            resp = await client.get(WORKLIST, params=params)
            assert resp.status_code == 200, resp.text
            page: dict[str, Any] = resp.json()
            pages.append(page)
            cursor = page["nextCursor"]
            if cursor is None:
                return pages
            assert len(pages) < 20, "the walk is not terminating"


# --- the population and the ordering, against the seed (AC 1, 3) ---------


@requires_db
@pytest.mark.parametrize(("name", "role"), [BLINE, PARK, ANALYST, HANDLER])
async def test_the_worklist_covers_exactly_the_personas_qualifying_claims(
    seeded_db_url: str, name: str, role: str
) -> None:
    """Every persona's table, against the seed counted independently.

    Walked to exhaustion rather than read one page deep: the capped list is what
    the story promises, and a first page that happened to be right would say
    nothing about the twenty rows behind it.
    """
    expected = seed_fixture.expected_priority_claims(name, role)
    pages = await walk(seeded_db_url, name, role)
    rows = [row for page in pages for row in page["items"]]

    assert [row["claimId"] for row in rows] == expected["claimIds"]
    assert pages[0]["total"] == expected["total"]


@requires_db
async def test_the_full_portfolio_supervisor_sees_the_cap_not_the_population(
    seeded_db_url: str,
) -> None:
    """The seeded numbers, written out — the one place this file states them.

    Thirty-eight of the hundred seeded claims qualify and thirty are shown, so
    the cap is genuinely exercised end to end rather than being a bound the data
    never reaches. Both numbers are asserted against the oracle *and* against the
    document, so this fails loudly if the seed changes rather than silently
    passing over a smaller book.
    """
    expected = seed_fixture.expected_priority_claims(*BLINE)
    pages = await walk(seeded_db_url, *BLINE)
    rows = [row for page in pages for row in page["items"]]

    assert expected["total"] > pages[0]["cap"], (
        "the seeded population no longer exceeds the cap, so the cut is untested "
        f"({expected['total']} qualifying against a cap of {pages[0]['cap']})"
    )
    assert len(rows) == pages[0]["cap"]
    assert pages[0]["total"] == expected["total"]
    assert [row["claimId"] for row in rows] == expected["claimIds"]
    # And what the cap *removed* is the tail of the same ordering, not an
    # arbitrary eight claims. Asserted from the oracle's uncapped sequence,
    # because "thirty rows in the right order" is also true of an
    # implementation that cut the population before ranking it and happened to
    # keep thirty that sort correctly among themselves.
    assert set(expected["population"][pages[0]["cap"] :]).isdisjoint(row["claimId"] for row in rows)


@requires_db
async def test_a_claim_qualifying_under_two_arms_appears_exactly_once(
    seeded_db_url: str,
) -> None:
    """AC 1's dedupe, over the real seed rather than a synthetic pair.

    The overlap is asserted to be non-empty first: on a book where no claim
    matched two arms this test would pass without testing anything, and the
    seeded portfolio's overlap is a fact that could change.
    """
    claims = seed_fixture.claims_for(*BLINE)
    overlapping = [
        claim["claim_id"]
        for claim in claims
        if sum(
            (
                claim["stage"] == "treatment",
                bool(claim["fraud_flag"])
                and claim["fraud_score"] >= seed_fixture.FRAUD_FLAG_SCORE_MIN,
                bool(claim["litigation_flag"]),
            )
        )
        > 1
    ]
    assert overlapping, "the seed no longer contains a claim in two arms"

    rows = [row for page in await walk(seeded_db_url, *BLINE) for row in page["items"]]
    ids = [row["claimId"] for row in rows]

    assert len(ids) == len(set(ids))
    for claim_id in overlapping:
        assert ids.count(claim_id) <= 1


@requires_db
async def test_the_order_is_the_queues_ordering_over_the_same_claims(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """AC 3, against `priority_score` and `order_key` rather than a sequence.

    The claims are re-scored here from the repository's own projection through
    the two functions the service calls, and the resulting order is compared
    with the rendered one. Not a restated formula: the same two symbols, so a
    re-weighting in either would move both sides and a *reordering* in only one
    of them is what this catches.
    """
    ctx = await context_for(db, *BLINE)
    thresholds = await thresholds_for(db)
    weights = await weights_for(db)
    params = await worklist_actions_for(db)
    aggregate = await priority_claims(db, ctx, thresholds, weights, params)

    members, total = rank(
        [_projection(row, thresholds) for row in await claim_repo.select_priority_rows(db, ctx)],
        thresholds,
        weights,
        params.supervisor_worklist_cap,
    )

    assert [row.claim_id for row in aggregate.items] == [
        claim.claim_id for claim, _flags in members
    ][: len(aggregate.items)]
    assert aggregate.total == total


def _projection(row: Any, thresholds: DerivationThresholds) -> PriorityClaim:
    """A repository row as the service projects it, restated for the test above.

    Named field by field for the service's own reason: a positional mapping over
    four adjacent free-text columns would type-check, run and compare two
    identically-wrong orderings.
    """
    from services import derivations

    return PriorityClaim(
        claim_id=row.claim_id,
        stage=row.stage,
        status=row.status,
        return_status=row.return_status,
        severity_score=row.severity_score,
        days_open=derivations.days_open.for_thresholds(thresholds).of(row.froi_date, utc_today()),
        injury_type=row.injury_type,
        worker_name=row.worker_name,
        employer_short_name=row.employer_short_name,
        handler_name=row.handler_name,
        surgery_required=row.surgery_required,
        litigation_flag=row.litigation_flag,
        fraud_flag=row.fraud_flag,
        fraud_score=row.fraud_score,
        osha_recordable=row.osha_recordable,
        osha_logged=row.osha_logged,
        attorney_rep=row.attorney_rep,
        rtw_rec=row.rtw_rec,
        actual_rtw=row.actual_rtw,
    )


# --- the action column (AC 2) -------------------------------------------


@requires_db
async def test_the_action_column_is_the_generators_top_row_for_the_same_claim(
    db: AsyncSession,
) -> None:
    """AC 2, asserted against the generator rather than against copied strings.

    Every row on the first page is re-generated here through
    `actions.claim_actions` — the *async* wrapper, which reads its own inputs
    from the database — and compared with what the aggregate published from its
    bulk reads. Comparing against the wrapper rather than against
    `generate_actions` with hand-assembled inputs is what makes this meaningful:
    it proves the four bulk reads returned the same rows the four single-claim
    reads do, which is the only thing the optimisation could have got wrong.
    """
    ctx = await context_for(db, *BLINE)
    page = await priority_claims(
        db,
        ctx,
        await thresholds_for(db),
        await weights_for(db),
        await worklist_actions_for(db),
    )
    assert page.items, "the seeded book should produce rows"

    for row in page.items:
        checklist = await action_rules.claim_actions(db, ctx, row.claim_id)
        assert checklist.items, f"{row.claim_id} generated no checklist"
        assert row.next_best_action == checklist.items[0].label, row.claim_id


@requires_db
async def test_the_action_column_is_generated_with_no_note_of_the_readers_own(
    db: AsyncSession,
) -> None:
    """The diary check-in's reader-relative behaviour, pinned rather than fixed.

    `select_latest_note_at_for_claims` is scoped to the **caller's own** diary,
    with an argued docstring: counting anybody's note would publish a handler's
    private working record to whoever else read the claim. A supervisor has no
    notes, so every row of her worklist is generated with `latest_note_at=None`
    and the check-in rule fires wherever the stage gate allows.

    Asserted as a fact about the *read* rather than about the rendered labels,
    because the labels only differ on the rare claim where nothing louder fired:
    `urgencyDiaryCheckIn` is `low` and the generator ranks on urgency first. If
    somebody ever passes the handler's note time instead, this fails on the
    read, immediately, rather than on whichever claim happened to be quiet.
    """
    ctx = await context_for(db, *BLINE)
    rows = await claim_repo.select_priority_rows(db, ctx)
    notes = await claim_repo.select_latest_note_at_for_claims(
        db, ctx, [row.claim_id for row in rows]
    )

    assert notes == {}, "a supervisor has no diary of her own; this row set says otherwise"


@requires_db
async def test_nothing_on_the_path_reads_the_ai_insight_cache(db: AsyncSession) -> None:
    """AD-2, structurally: the action column has no LLM anywhere near it.

    A grep over the module's source rather than a behavioural check, because
    what is being asserted is an *absence* — Epic 6's narrative "next best
    actions" card is a different, later thing, and the way this column would go
    wrong is by somebody wiring it to that cache when it lands.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "services" / "worklist" / "priority_claims.py"
    ).read_text(encoding="utf-8")

    assert "ai_insight" not in source.replace("`ai_insight` cache is not a source here", "")


# --- paging (AC 3) -------------------------------------------------------


@requires_db
async def test_the_walk_visits_thirty_distinct_claims_across_three_pages(
    seeded_db_url: str,
) -> None:
    """The cursor, walked to exhaustion at the document's page size.

    Three pages of ten under a cap of thirty, thirty distinct rows, `total`
    reading the same population size on every one of them, and a fourth page
    never offered. Every clause is a way this could go wrong on its own: a page
    that re-served its own offset repeats rows, a `total` recomputed per page
    shrinks as the window moves, and a `nextCursor` issued at the boundary hands
    the client an offset past the end.
    """
    pages = await walk(seeded_db_url, *BLINE)
    ids = [row["claimId"] for page in pages for row in page["items"]]
    cap = pages[0]["cap"]
    expected = seed_fixture.expected_priority_claims(*BLINE)

    assert len(pages) == 3
    assert [len(page["items"]) for page in pages] == [10, 10, 10]
    assert len(ids) == len(set(ids)) == cap
    assert {page["total"] for page in pages} == {expected["total"]}
    assert [page["nextCursor"] is None for page in pages] == [False, False, True]


@requires_db
async def test_a_scoped_supervisors_book_fits_one_page_with_no_cursor(
    seeded_db_url: str,
) -> None:
    """Jennifer Park: eight qualifying claims, one page, no "Show more".

    The other side of the paging contract — `nextCursor` is null exactly when
    the list is finished, never "null because the page came back short" and
    never non-null on a list with nothing behind it.
    """
    pages = await walk(seeded_db_url, *PARK)
    expected = seed_fixture.expected_priority_claims(*PARK)

    assert len(pages) == 1
    assert pages[0]["nextCursor"] is None
    assert pages[0]["total"] == expected["total"]
    assert [row["claimId"] for row in pages[0]["items"]] == expected["claimIds"]
    # Her book carries no litigated claim at all, so the LITIG chip's absence is
    # a property of the data rather than of the renderer — worth pinning here so
    # the e2e spec's "no LITIG chip anywhere" assertion has a server-side reason.
    assert all(row["litigationFlag"] is False for row in pages[0]["items"])


@requires_db
@pytest.mark.parametrize(
    "forge",
    [
        pytest.param(lambda _p: "not-base64-at-all!!", id="not-base64"),
        pytest.param(lambda _p: "e30", id="valid-base64-not-a-cursor"),
        pytest.param(lambda _p: encode_cursor(a_cursor(offset=10_000)), id="offset-past-the-end"),
        pytest.param(
            lambda _p: encode_cursor(a_cursor(as_of=utc_today() + timedelta(days=1))),
            id="dated-in-the-future",
        ),
        pytest.param(
            lambda _p: encode_cursor(a_cursor(as_of=utc_today() - timedelta(days=8))),
            id="older-than-the-maximum-age",
        ),
        pytest.param(lambda _p: encode_cursor(a_cursor(limit=100_000)), id="forged-page-size"),
        pytest.param(
            lambda _p: (
                base64.urlsafe_b64encode(
                    b'{"o":Infinity,"l":10,"v":1,"t":5,"a":2,"d":"2026-08-18"}'
                )
                .decode()
                .rstrip("=")
            ),
            id="infinite-offset",
        ),
    ],
)
async def test_a_forged_cursor_is_four_hundred_and_never_five_hundred(
    seeded_db_url: str, forge: Any
) -> None:
    """400 problem+json, `no-store`, and never a silent page one.

    Every case here is caller-supplied input: the token is base64 of JSON and
    forging one is trivial. What must not happen is a 500 (the `Infinity` case
    reached the queue's decoder as one before `ArithmeticError` joined the
    except tuple) and what must not happen more quietly is page one — a client
    that got rows back from a rejected cursor would append the same thirty
    claims for ever.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *BLINE)
        resp = await client.get(WORKLIST, params={"cursor": forge(None)})

    assert resp.status_code == 400, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.headers["cache-control"] == "no-store"
    assert resp.json()["type"] == "/problems/invalid-cursor"


@requires_db
@pytest.mark.parametrize(
    "key",
    [DERIVATION_THRESHOLDS_KEY, PRIORITY_WEIGHTS_KEY, WORKLIST_ACTIONS_KEY],
)
async def test_a_cursor_cut_under_a_superseded_document_is_refused(
    db: AsyncSession, seeded_db_url: str, key: str
) -> None:
    """All three recorded versions are compared, and each one alone refuses.

    Parametrized over the three because the failure this catches is a version
    recorded and never checked: a cursor carrying a field nothing reads is
    decoration, and the *third* — `worklist_actions`, which decides the cap — is
    the one the queue's cursor has no equivalent for and the one most likely to
    be forgotten.

    The cursor is forged with a wrong version rather than by superseding a real
    document, deliberately: a supersession test would also change the ordering
    or the cap, so a refusal could come from the offset check instead and the
    test would pass for the wrong reason.
    """
    first = await worklist_for(seeded_db_url, *BLINE)
    assert first["nextCursor"] is not None

    current = {
        DERIVATION_THRESHOLDS_KEY: "thresholds_version",
        PRIORITY_WEIGHTS_KEY: "weights_version",
        WORKLIST_ACTIONS_KEY: "actions_version",
    }[key]
    real = decode_cursor(first["nextCursor"])
    forged = encode_cursor(replace(real, **{current: getattr(real, current) + 1}))

    async with make_client(seeded_db_url) as client:
        await login_as(client, *BLINE)
        resp = await client.get(WORKLIST, params={"cursor": forged})

    assert resp.status_code == 400, resp.text
    assert resp.json()["type"] == "/problems/invalid-cursor"


# --- the rules tier (AD-8) ----------------------------------------------


@requires_db
async def test_the_published_thresholds_come_from_the_rule_documents(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """Compared against the JDM resolution path, never against a literal.

    A test asserting `cap == 30` would keep passing if the endpoint had
    hardcoded it, which is the one thing worth checking here. Note which
    document each field comes from: the three thresholds are
    `derivation_thresholds`' and `rulesVersion` is **`worklist_actions`'**,
    because it names the document that decided the cap.
    """
    thresholds = await thresholds_for(db)
    params = await worklist_actions_for(db)
    payload = await worklist_for(seeded_db_url, *BLINE)

    assert payload["highRiskSeverityMin"] == thresholds.risk_high_min
    assert payload["medRiskSeverityMin"] == thresholds.risk_med_min
    assert payload["fraudFlagScoreMin"] == thresholds.fraud_flag_score_min
    assert payload["cap"] == params.supervisor_worklist_cap
    assert payload["rulesVersion"] == params.version


@requires_db
async def test_a_superseded_document_with_a_smaller_cap_shortens_the_table(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """AD-8 end to end: the row count follows the document, with no code change.

    A v3 of `worklist_actions` with a smaller cap is inserted effective today;
    the same request comes back with fewer rows *and* a smaller published `cap`,
    so the caption follows the document. Nothing is deployed, nothing is
    restarted and no Python changes.

    `total` must **not** move, which is the other half of the assertion: the cap
    bounds what is shown and says nothing about how many claims qualified. A
    retune that shifted `total` would mean the cut had been applied before the
    population was counted.
    """
    smaller = 5
    before = await worklist_for(seeded_db_url, *BLINE)

    content = (
        await db.execute(
            sa.text(
                "SELECT content FROM rule_document WHERE key = :key ORDER BY version DESC LIMIT 1"
            ),
            {"key": WORKLIST_ACTIONS_KEY},
        )
    ).scalar_one()
    await db.execute(
        sa.text(
            "INSERT INTO rule_document (key, version, effective_from, content, created_at) "
            "VALUES (:key, 3, :today, CAST(:content AS jsonb), now())"
        ),
        {
            "key": WORKLIST_ACTIONS_KEY,
            # `utc_today()`, not `date.today()`: the loader filters
            # `effective_from <= as_of` against a UTC-derived today, so a local
            # date east of UTC near midnight runs a day ahead, the document is
            # not yet effective, and this fails as if the loader were broken.
            "today": utc_today(),
            "content": json.dumps(
                _retuned(
                    content,
                    supervisorWorklistCap=smaller,
                    supervisorWorklistPageLimit=smaller,
                )
            ),
        },
    )
    await db.commit()

    try:
        after = await worklist_for(seeded_db_url, *BLINE)

        assert before["cap"] > smaller, "the retune must actually shorten the table"
        assert after["cap"] == smaller
        assert len(after["items"]) == smaller
        assert after["nextCursor"] is None, "a cap equal to the page size is one page"
        assert after["rulesVersion"] == 3
        # The population is unchanged: the cap cuts the list, it does not
        # narrow the book.
        assert after["total"] == before["total"]
        # And the rows that survive are the *top* of the same ordering — asserted
        # against the oracle at the retuned cap rather than against `before`'s
        # own first five. Comparing the endpoint with its earlier self proves
        # the prefix is stable and nothing more: an implementation that ranked
        # the whole book backwards would satisfy it on both runs. Recounting
        # `seed_data.json` at `cap=smaller` is what makes this an independent
        # check, and it is what the oracle's `cap` parameter exists for.
        expected = seed_fixture.expected_priority_claims(*BLINE, cap=smaller)
        assert [row["claimId"] for row in after["items"]] == expected["claimIds"]
        assert after["total"] == expected["total"]
        assert [row["claimId"] for row in before["items"][:smaller]] == expected["claimIds"]
    finally:
        await db.execute(
            sa.text("DELETE FROM rule_document WHERE key = :key AND version = 3"),
            {"key": WORKLIST_ACTIONS_KEY},
        )
        await db.commit()


def _retuned(content: dict[str, Any], **overrides: int) -> dict[str, Any]:
    """A copy of a document with named expressions given new values.

    `test_portfolio_charts.py`'s helper, restated: the document's expression
    values are *strings* of ZEN expressions, so the override is stringified
    rather than assigned — a JSON number in that slot is a different thing from
    the expression `"5"`, and only one of them evaluates.
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


# --- scope (AD-7) --------------------------------------------------------


@requires_db
async def test_every_claim_behind_a_scoped_personas_worklist_is_inside_her_book(
    seeded_db_url: str,
) -> None:
    """Park's rows against her scoped claim ids, and Bline's against all hundred.

    The containment is the assertion; the *non*-containment beside it is what
    makes it worth making — Bline's table draws from claims Park cannot see, so
    a scope predicate that had quietly become a tautology would fail here.
    """
    park_ids = seed_fixture.expected_claim_ids(*PARK)
    all_ids = seed_fixture.expected_claim_ids(*BLINE)

    park_rows = [row for page in await walk(seeded_db_url, *PARK) for row in page["items"]]
    bline_rows = [row for page in await walk(seeded_db_url, *BLINE) for row in page["items"]]

    assert {row["claimId"] for row in park_rows} <= park_ids
    assert {row["claimId"] for row in bline_rows} <= all_ids
    assert park_ids < all_ids, "the seed no longer scopes this supervisor"
    assert not {row["claimId"] for row in bline_rows} <= park_ids


@requires_db
async def test_an_empty_book_reports_an_empty_worklist_rather_than_failing(
    db: AsyncSession,
) -> None:
    """The empty book, through the real read path rather than the pure fold.

    `employer_scope` reads "assigned to no employers" as a predicate matching
    nothing rather than as "no filter", and that distinction is the whole of
    AD-7's mechanism. The published rules still arrive: "nothing in this book"
    says nothing about which rules were in force.
    """
    page = await priority_claims(
        db,
        CallerContext(user_id=0, role=UserRole.supervisor, employer_ids=frozenset()),
        await thresholds_for(db),
        await weights_for(db),
        await worklist_actions_for(db),
    )

    assert page.items == ()
    assert page.total == 0
    assert page.next_cursor is None
    assert page.high_risk_severity_min == (await thresholds_for(db)).risk_high_min
    assert page.cap == (await worklist_actions_for(db)).supervisor_worklist_cap


@requires_db
async def test_the_aggregate_takes_exactly_five_scoped_reads(db: AsyncSession) -> None:
    """ "Five reads, not forty" as a counted fact rather than a claim.

    One `select_priority_rows` over the scoped book, then four bulk child reads
    over the page's ids. The cheapest way for that to stop being true is somebody
    reaching for `actions.claim_actions` per row, which would be correct, would
    pass every other test in this file, and would cost forty round trips for a
    ten-row page and a hundred and twenty for a full walk. Counting the
    statements the session executes is blunt and is the only check that notices.

    The three rule documents are deliberately *not* in the count: they are the
    route's reads, loaded once and handed down, which is what keeps this
    aggregate a composition of scope and parameters.
    """
    ctx = await context_for(db, *BLINE)
    thresholds = await thresholds_for(db)
    weights = await weights_for(db)
    params = await worklist_actions_for(db)

    executed: list[str] = []
    original = db.execute

    async def counting(statement: Any, *args: Any, **kwargs: Any) -> Any:
        executed.append(str(statement).split("\n")[0])
        return await original(statement, *args, **kwargs)

    db.execute = counting  # type: ignore[method-assign]
    try:
        await priority_claims(db, ctx, thresholds, weights, params)
    finally:
        db.execute = original  # type: ignore[method-assign]

    assert len(executed) == 5, executed
    assert all(statement.startswith("SELECT") for statement in executed), executed


# --- the contract --------------------------------------------------------


@requires_db
async def test_the_route_declares_exactly_one_parameter_and_it_is_the_cursor(
    seeded_db_url: str,
) -> None:
    """AD-7 structurally, relaxed to an allowlist of exactly one.

    Its three siblings declare no parameters at all, and
    `test_the_route_declares_no_parameters_at_all` asserts that emptiness. This
    route needs a cursor, so the honest form of the same check is an allowlist
    — and it is an *allowlist* rather than a "cursor is present" assertion,
    because what matters is what else could appear beside it. A `limit` here
    would be the page size becoming a caller's choice; an `employerId` would be
    a scope.

    Asserted against the published OpenAPI document rather than the function
    signature, because the contract is what a client (and a reviewer) reads.
    """
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"][WORKLIST]["get"]

    assert {parameter["name"] for parameter in operation.get("parameters", [])} == {"cursor"}
    assert all(parameter["in"] == "query" for parameter in operation.get("parameters", []))
    assert "requestBody" not in operation


@requires_db
async def test_query_parameters_cannot_widen_or_change_the_scope(seeded_db_url: str) -> None:
    """The smuggling attempt: ask as Park, name Bline's portfolio anyway.

    Unknown parameters are ignored rather than rejected (FastAPI's default, and
    the safer direction — a 422 here would tell an attacker which parameter
    names exist). What matters is that the answer is byte-identical.

    `limit` is in the list and stays there, which is this route's own version of
    the test: the page size is a published rule, so `?limit=200` has to be as
    inert as `?scopeAll=true`.
    """
    honest = await worklist_for(seeded_db_url, *PARK)

    for smuggled in (
        {"employerId": "3"},
        {"employerIds": "1,2,3"},
        {"scopeAll": "true"},
        {"limit": "200"},
        {"cap": "100"},
        {"sort": "-severity"},
    ):
        attempt = await worklist_for(seeded_db_url, *PARK, params=smuggled)
        assert attempt == honest, f"{smuggled} changed the answer"


@requires_db
async def test_the_response_is_camel_case_and_carries_nothing_else(
    seeded_db_url: str,
) -> None:
    payload = await worklist_for(seeded_db_url, *PARK)

    assert set(payload) == {
        "items",
        "nextCursor",
        "total",
        "cap",
        "truncated",
        "highRiskSeverityMin",
        "medRiskSeverityMin",
        "fraudFlagScoreMin",
        "rulesVersion",
    }
    assert set(payload["items"][0]) == ROW_KEYS
    # Business ids on the wire, never surrogates (the ID convention).
    assert all(row["claimId"].startswith("WC-") for row in payload["items"])


@requires_db
async def test_the_response_is_never_cached(seeded_db_url: str) -> None:
    """One persona's worklist must not be served to another from upstream."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *PARK)
        resp = await client.get(WORKLIST)
    assert resp.headers["cache-control"] == "no-store"


@requires_db
async def test_the_endpoint_requires_a_session(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.get(WORKLIST)

    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


@requires_db
async def test_reading_the_worklist_writes_no_audit_event(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """A query is not a mutation (Epic 5 preamble).

    Counted before and after rather than asserted absent, because other rows
    exist: what must not happen is this request adding one.
    """
    count = sa.select(sa.func.count()).select_from(AuditEvent)
    before = (await db.execute(count)).scalar_one()

    await worklist_for(seeded_db_url, *BLINE)

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
    supervisor = await worklist_for(seeded_db_url, *BLINE)
    analyst = await worklist_for(seeded_db_url, *ANALYST)

    assert analyst == supervisor


@requires_db
async def test_a_handler_may_read_her_own_books_priority_claims(seeded_db_url: str) -> None:
    """The access decision, as a contract rather than an omission.

    This endpoint is ungated where `/dashboard/handler-benchmarks` answers 403,
    and it is the harder call because this payload names a handler in every row.
    It names them as the **owner** of a claim, with no metric attached — and
    `employer_scope` is the same predicate here as in the queue, so Sarah
    Williams sees nothing her own caseload does not already contain.

    Asserted structurally on the payload rather than by substring-matching a
    persona's name against a serialised body: a surname appearing inside an
    injury type would false-fail that, which is a trap `test_portfolio_charts.py`
    fell into and corrected.
    """
    payload = await worklist_for(seeded_db_url, *HANDLER)
    expected = seed_fixture.expected_priority_claims(*HANDLER)

    shown = len(payload["items"])
    assert [row["claimId"] for row in payload["items"]] == expected["claimIds"][:shown]
    assert set(payload["items"][0]) == ROW_KEYS
    # Every handler named is one whose claim is in her scope — the payload names
    # owners, and the rows are hers to read.
    assert {row["claimId"] for row in payload["items"]} <= seed_fixture.expected_claim_ids(*HANDLER)


@requires_db
async def test_the_worklist_and_the_queue_agree_about_a_shared_claim(
    seeded_db_url: str,
) -> None:
    """AD-10 across two surfaces: one claim, two lists, the same derived cells.

    A handler's queue card and her worklist row are cut from two different
    projections by two different aggregates, and both are supposed to read the
    registry. The risk band and the days-open figure are where a second
    implementation would show up first, and they are checked here for every
    claim that appears on both.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *HANDLER)
        queue = (await client.get(QUEUE, params={"limit": 200})).json()
        rows = (await client.get(WORKLIST)).json()["items"]

    cards = {card["claimId"]: card for group in queue["groups"].values() for card in group["items"]}
    shared = [row for row in rows if row["claimId"] in cards]
    assert shared, "the two surfaces should overlap on a handler's own book"

    for row in shared:
        card = cards[row["claimId"]]
        assert row["severityBand"] == card["risk"], row["claimId"]
        assert row["daysOpen"] == card["daysOpen"], row["claimId"]
        assert row["litigationFlag"] == card["litigationFlag"], row["claimId"]


@requires_db
async def test_the_caption_is_told_whether_the_cap_actually_cut_anything(
    seeded_db_url: str,
) -> None:
    """`truncated`, and the sentence a scoped supervisor would otherwise read.

    Bline's thirty-eight qualifying claims are cut to thirty, so his caption is
    "showing top 30 of 38". Park's eight are not cut at all, and a caption built
    from `cap` and `total` alone would tell her "showing top 30 of 8" — a cap
    that removed nothing, quoted as though it had, beside eight rows. The
    comparison is a rule comparison, so it is decided here rather than in the
    browser (AD-1); `noDerivation.test.ts` is what stops the browser doing it.
    """
    bline = await worklist_for(seeded_db_url, *BLINE)
    park = await worklist_for(seeded_db_url, *PARK)

    assert bline["total"] > bline["cap"]
    assert bline["truncated"] is True

    assert park["total"] < park["cap"], (
        "the scoped supervisor's book no longer fits under the cap, so the "
        "untruncated caption is untested"
    )
    assert park["truncated"] is False


def test_the_rules_tier_page_ceiling_is_the_one_the_cursor_enforces() -> None:
    """The two spellings of the page ceiling, pinned against each other.

    `rules/parameters.py` refuses a published page size above
    `CURSOR_PAGE_CEILING`, and `decode_cursor` refuses a decoded one above
    `MAX_PAGE_LIMIT`. They must be the same number: a rules tier that allowed
    what the transport refuses would mint cursors that are 400s on arrival, and
    one that refused what the transport allows would be arbitrarily strict.
    `rules` sits below `services` and cannot import the constant, so this
    assertion is what keeps the restatement honest.
    """
    assert CURSOR_PAGE_CEILING == MAX_PAGE_LIMIT


def test_a_page_size_above_the_cursor_ceiling_is_refused_at_the_document() -> None:
    """The refusal that stops a rules migration switching off "Show more".

    A cap of five hundred with a page of two hundred and fifty satisfies both of
    the other two rules — it is at least one, and it does not exceed the cap —
    and it would serve a first page, mint a cursor carrying `l: 250`, and 400 on
    every subsequent click. Refused where it is authored instead.
    """
    with pytest.raises(RuleParameterError, match="largest page any cursor"):
        replace(
            ACTION_PARAMS,
            supervisor_worklist_cap=500,
            supervisor_worklist_page_limit=CURSOR_PAGE_CEILING + 50,
        )


@requires_db
async def test_a_cursor_naming_a_page_size_the_rules_no_longer_publish_is_refused(
    seeded_db_url: str,
) -> None:
    """The page size is not a caller's choice, including through the cursor.

    A cursor is unsigned base64 JSON, so its `limit` is caller-supplied in every
    sense that matters. Honouring it would hand back through the cursor exactly
    the request parameter the route refuses to declare — and one forged `l`
    would make a single request generate actions for the whole capped list
    rather than a page, which is the cost the page size exists to bound.
    """
    honest = await worklist_for(seeded_db_url, *BLINE)
    forged = encode_cursor(replace(decode_cursor(honest["nextCursor"]), limit=MAX_PAGE_LIMIT))

    async with make_client(seeded_db_url) as client:
        await login_as(client, *BLINE)
        resp = await client.get(WORKLIST, params={"cursor": forged})

    assert resp.status_code == 400, resp.text
    assert resp.json()["type"] == "/problems/invalid-cursor"
    assert "pages at" in resp.json()["detail"]


def test_a_cap_below_one_is_refused_by_the_pure_ranker() -> None:
    """`rank` is exported and pure, so it refuses rather than slices.

    `WorklistActions.__post_init__` already refuses a cap below one at the only
    production source. This is the second half: `scored[:0]` would return an
    empty page beside a non-zero `total` — a worklist that is populated and
    finished at once, which no caption can render.
    """
    with pytest.raises(ValueError, match="cap must be at least 1"):
        rank([], SEEDED_THRESHOLDS, SEEDED_WEIGHTS, 0)
