"""Story 7.3 — one filter, ten dimensions, and the vocabulary it shares.

Two halves, `test_fraud_analytics.py`'s and `test_trend_analytics.py`'s
arrangement, and the first one is in that shape for a reason specific to this
story.

**A segmentation is a *cut*, and this suite's job is to prove which claims
survive it.** Every assertion below that could have been a count is a set of
claim ids instead, because a count is satisfiable by the wrong population — the
exact failure Story 7.1's review found in an oracle that had inherited the
implementation's own cut. The seeded portfolio can demonstrate the ordinary
combinations, and it cannot demonstrate the boundaries: no seeded worker is
younger than the low twenties or older than the high fifties, so the age band's
outer edges are never actually reached, and no seeded book is empty. The
synthetic rows in the pure half are therefore not thoroughness — they are the
only place the band edges, the empty book and the impossible combination are
exercised at all.

The second half is the endpoints, and its centre of gravity is the **one
vocabulary** and the **one read**.

*One vocabulary* is asserted three ways and none subsumes the others: the module
asserts at import that its keys, its order and its field types are a subset of
`drill_through`'s; `test_the_ten_dimensions_are_the_drill_lists_own_facets`
restates the ten names here so a respelling on both sides still fails; and
`test_a_segmented_aggregate_reconciles_with_the_drill_list_it_opens` asks the
*live* endpoints, which is the only assertion that can catch two modules agreeing
on a wrong answer.

*One read* is a counted fact. Segmentation narrows a fold and adds no query, so
`test_the_panel_takes_exactly_one_scoped_read` and the parametrised trends guard
have to stay green **with a filter applied** — and this file re-counts them under
one, because a guard that only ever ran unfiltered would not notice the
implementation that grew a second read to answer a filter.

**Scope is asserted at the service level**, with a hand-built `CallerContext`,
for the reason 7.1 and 7.2 both recorded rather than papered over: there is
exactly one analyst persona in the seed and they are `scope_all`, so no HTTP
request in this codebase can demonstrate a *narrowed* analyst. Seeding one moves
persona counts in three earlier stories and in the e2e login fixture. What this
story adds to that arrangement is a **Hypothesis property** — no `Segmentation`
value can produce a claim outside the unfiltered scoped set — which is the
honest general form of "a filter can only narrow", and the first property test on
an analyst aggregate.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
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
from data.models import AppUser, AuditEvent
from data.models.enums import Disability, Gender, RecoveryWindow, UserRole
from data.repositories import claims as claim_repo
from data.repositories.identity import employer_ids_for
from rules.parameters import (
    MAX_WORKING_AGE,
    MIN_AGE_BAND_SPAN,
    MIN_WORKING_AGE,
    DerivationThresholds,
    RuleParameterError,
    thresholds_for,
    trend_periods_for,
)
from services.derivations import AgeBand, RiskBand, age_band, registered_names
from services.worklist import fraud as fraud_service
from services.worklist import trends as trend_service
from services.worklist.drill_through import FILTER_KEYS, WIRE_KEYS
from services.worklist.fraud import FraudRateSorts, fraud_panel, fraud_rates, segmentation_values
from services.worklist.segmentation import (
    SEGMENTATION_KEYS,
    SEGMENTATION_WIRE_KEYS,
    Computers,
    Segmentation,
    applied_of,
    bands_of,
    narrowed,
    to_drill_filters,
    values_of,
)
from tests import seed_fixture
from tests.conftest import requires_db

SETTINGS = Settings(database_url="postgresql://unused", env="e2e")  # type: ignore[arg-type]

VALUES = "/dashboard/segmentation/values"
PANEL = "/dashboard/fraud"
RATES = "/dashboard/fraud/rates"
RED_FLAGS = "/dashboard/fraud/red-flags"
TRENDS = "/dashboard/trends"
DRILL = "/dashboard/claims"

ANALYST = ("David Bline", "analyst")
SUPERVISOR = ("David Bline", "supervisor")
SCOPED_SUPERVISOR = ("Jennifer Park", "supervisor")

#: The three age edges and the two severity edges, restated — never imported
#: from the block the code under test reads.
#:
#: `seed_fixture.HIGH_RISK_MIN`'s discipline, and the reason it matters here is
#: the coincidence: `AGE_YOUNGER_MIN` and `RISK_MED_MIN` are the same integer over
#: two different columns, and `AGE_OLDEST_MIN` is a third rule's number as well.
#: A case that moved one of them by reusing a name would be testing two changes
#: at once, and the failure it would hide is a fraud threshold re-banding a
#: workforce.
AGE_YOUNGER_MIN = 35
AGE_OLDER_MIN = 45
AGE_OLDEST_MIN = 55
RISK_HIGH_MIN = 65
RISK_MED_MIN = 35

#: The ten dimensions as the wire spells them, written out.
#:
#: Deliberately **not** read off `SEGMENTATION_WIRE_KEYS`: the whole claim of this
#: story is that these ten strings are the same ones `/dashboard/claims` accepts,
#: and an expectation derived from the module under test would agree with it
#: however either was respelled.
EXPECTED_WIRE_KEYS_BARE = (
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

#: The same ten as the route's parameter names — `filter[…]` around each.
EXPECTED_WIRE_KEYS = (
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
)


# --- the pure half: synthetic rows ---------------------------------------


@dataclass(frozen=True)
class Row:
    """A synthetic projection that satisfies `LabelledClaim` **by shape**.

    Its own dataclass rather than a `FraudClaim` or a `TrendClaim`, and that is
    the protocol's point being exercised rather than a shortcut: the three
    projections that narrow through this module share no base class, so a test
    row that joined by having the right attributes is the same way they join. A
    case built from `FraudClaim` would also be asserting that class's other nine
    fields, none of which segmentation reads.

    Every field is defaulted *quiet*: the baseline row is `low` severity,
    `youngest`, temporary, male, and carries the commonest seeded free-text
    values — so a case that names any dimension is narrowing away from it rather
    than confirming it.
    """

    claim_id: str = "WC-0001"
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


def _thresholds(**changes: int) -> DerivationThresholds:
    """A `DerivationThresholds` carrying this story's five edges.

    Built through the real block so `__post_init__` runs — a case that wanted an
    inverted age pair is refused here rather than silently folded — and
    hand-written rather than loaded so the pure half needs no database and a case
    can move an edge without publishing a rule document.
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


def _computers(**changes: int) -> Computers:
    return Computers.of(_thresholds(**changes))


def surviving(caseload: Sequence[Row], **dimensions: Any) -> list[str]:
    """The claim ids that survive one segmentation, in the order they arrived."""
    return [row.claim_id for row in narrowed(caseload, Segmentation(**dimensions), _computers())]


# --- one vocabulary, not two ---------------------------------------------


def test_the_ten_dimensions_are_the_drill_lists_own_facets() -> None:
    """The story's central claim, restated here rather than read off the module.

    `segmentation.py` asserts the subset relationship at import, which is the
    guard that runs in production; this is the assertion that would still fail if
    somebody respelled a facet on *both* sides — the names are written out, and
    they are written out in the spelling a URL carries.
    """
    assert tuple(f"filter[{SEGMENTATION_WIRE_KEYS[key]}]" for key in SEGMENTATION_KEYS) == (
        EXPECTED_WIRE_KEYS
    )
    assert set(SEGMENTATION_KEYS) <= set(FILTER_KEYS)
    for key in SEGMENTATION_KEYS:
        assert SEGMENTATION_WIRE_KEYS[key] == WIRE_KEYS[key], key


def test_the_chip_order_is_the_drill_lists_order_and_not_a_reading_order() -> None:
    """A subsequence, not merely a subset — which is what a chip row depends on.

    An analyst who narrows by sector and gender and then clicks a chart sees the
    same two chips in the same two positions on the list that opens. A
    `Segmentation` whose fields were in a more natural reading order would draw
    the right chips in the wrong sequence, which reads as two different filters.
    """
    assert tuple(key for key in FILTER_KEYS if key in set(SEGMENTATION_KEYS)) == SEGMENTATION_KEYS


def test_the_four_new_facets_are_appended_after_this_storys_six() -> None:
    """`appliedFilters` is the chip row's order, and the tail is this tuple.

    Appended, never inserted: putting `gender` beside `state` — where it reads
    more naturally — would silently re-order the chips on every drill-through URL
    anybody has already shared. The other end is asserted in
    `test_trend_analytics.py`, which owns the six before these four.
    """
    assert FILTER_KEYS[-4:] == ("region", "icd10", "age_group", "gender")
    assert [WIRE_KEYS[key] for key in FILTER_KEYS[-4:]] == [
        "region",
        "icd10",
        "ageGroup",
        "gender",
    ]


def test_every_dimension_has_a_predicate_and_every_predicate_has_a_dimension() -> None:
    """The import-time assertion, restated as a test a reader finds by name.

    A dimension declared on `Segmentation` with no predicate would be accepted by
    four routes, published as a chip and narrow nothing — four cards, five charts
    and three tables describing the whole book under a chip row saying otherwise.
    """
    for key in SEGMENTATION_KEYS:
        assert surviving([Row()], **{key: None}) == ["WC-0001"]
    assert len(SEGMENTATION_KEYS) == len(EXPECTED_WIRE_KEYS)


def test_a_segmentation_becomes_the_drill_filters_that_select_the_same_claims() -> None:
    """ "One vocabulary" as behaviour rather than as a name comparison.

    `to_drill_filters` is what a test calls to say that a segmentation and the
    drill-through it opens select the same claims; the import-time checks only say
    the names agree. Every dimension is set at once, so a mapping that dropped one
    fails here.
    """
    filters = Segmentation(
        severity_band=RiskBand.high,
        injury_type="Fracture",
        state="MI",
        employer_id=4,
        disability=Disability.permanent,
        sector="Automotive",
        region="Midwest",
        icd10="W24.0XXA",
        age_group=AgeBand.older,
        gender=Gender.female,
    )

    drill = to_drill_filters(filters)

    for key in SEGMENTATION_KEYS:
        assert getattr(drill, key) == getattr(filters, key), key
    # …and nothing else was set: a segmentation narrows on ten dimensions and the
    # drill list has twenty-four, so the fourteen it does not name must stay
    # `None` or the workspace would be silently narrowing on a facet nobody chose.
    for key in set(FILTER_KEYS) - set(SEGMENTATION_KEYS):
        assert getattr(drill, key) is None, key


# --- AND across dimensions, and no OR --------------------------------------


def test_dimensions_and_together() -> None:
    """AC 4's word. Three claims, each failing exactly one of two dimensions."""
    caseload = [
        Row("WC-0001", sector="Aerospace", gender=Gender.female),
        Row("WC-0002", sector="Aerospace", gender=Gender.male),
        Row("WC-0003", sector="Automotive", gender=Gender.female),
    ]

    assert surviving(caseload, sector="Aerospace") == ["WC-0001", "WC-0002"]
    assert surviving(caseload, gender=Gender.female) == ["WC-0001", "WC-0003"]
    assert surviving(caseload, sector="Aerospace", gender=Gender.female) == ["WC-0001"]


def test_an_omitted_dimension_is_a_question_nobody_asked_and_not_a_wildcard() -> None:
    """`is not None` on the field, never a truthiness test on the value.

    The distinction is invisible on today's data and would not stay that way:
    `employer_id=0` does not exist because the identity sequence starts at one,
    and a truthiness test would be a silent trap the day a surrogate sequence
    started anywhere else.
    """
    caseload = [Row("WC-0001", employer_id=0), Row("WC-0002", employer_id=1)]

    assert surviving(caseload) == ["WC-0001", "WC-0002"]
    assert surviving(caseload, employer_id=0) == ["WC-0001"]


def test_one_value_per_dimension_and_no_or_within_one() -> None:
    """The shape that makes an OR impossible rather than merely unimplemented.

    Every field is a single optional value, so there is nowhere in this class to
    put two sectors. It is a real product request and a real contract change — it
    would multiply the wire form, the cursor payload, the chip row and every
    oracle — and the assertion is that the *type* refuses it, not that a function
    happens not to implement it.
    """
    for field_name in SEGMENTATION_KEYS:
        annotation = Segmentation.__annotations__[field_name]
        assert "list" not in str(annotation), field_name
        assert "Sequence" not in str(annotation), field_name


def test_an_impossible_combination_is_an_empty_answer_and_never_a_refusal() -> None:
    """AC 4's zero-result state, at the source: nothing raises.

    `Segmentation` declares no `__post_init__` at all — `DrillFilters`' ruling,
    reached by having no validation hook rather than by having one that returns:
    a combination no claim satisfies is a well-formed question whose answer is
    "none", and refusing it would mean this class knowing which combinations the
    *data* can satisfy.
    """
    caseload = [Row("WC-0001", sector="Aerospace", region="Midwest")]

    assert surviving(caseload, sector="Aerospace", region="Northeast") == []


def test_a_value_no_claim_carries_is_an_empty_answer_and_never_a_refusal() -> None:
    """The free-text case: only the server's enum coercion refuses anything."""
    caseload = [Row("WC-0001", sector="Aerospace")]

    assert surviving(caseload, sector="Nonexistent") == []


# --- the two derived dimensions ------------------------------------------


def test_the_severity_dimension_is_the_registered_risk_band() -> None:
    """Not a comparison against a score — the same band the High Risk card counts.

    Asserted at the edges, because that is where a re-implementation differs: a
    claim exactly at `riskHighMin` is high, and one a point below it is not.
    """
    caseload = [
        Row("WC-0001", severity_score=RISK_HIGH_MIN),
        Row("WC-0002", severity_score=RISK_HIGH_MIN - 1),
        Row("WC-0003", severity_score=RISK_MED_MIN - 1),
    ]

    assert surviving(caseload, severity_band=RiskBand.high) == ["WC-0001"]
    assert surviving(caseload, severity_band=RiskBand.med) == ["WC-0002"]
    assert surviving(caseload, severity_band=RiskBand.low) == ["WC-0003"]


def test_the_age_dimension_bands_at_the_documents_edges() -> None:
    """Four bands from three edges, each asserted at its own boundary.

    A band is inclusive at its lower edge — a worker exactly at `ageOlderMin` is
    `older`, not `younger` — which is the reading `AgeBandDerivation` publishes
    and the only one under which three comparisons produce four non-overlapping
    groups.
    """
    caseload = [
        Row("WC-0001", age=AGE_YOUNGER_MIN - 1),
        Row("WC-0002", age=AGE_YOUNGER_MIN),
        Row("WC-0003", age=AGE_OLDER_MIN),
        Row("WC-0004", age=AGE_OLDEST_MIN),
    ]

    assert surviving(caseload, age_group=AgeBand.youngest) == ["WC-0001"]
    assert surviving(caseload, age_group=AgeBand.younger) == ["WC-0002"]
    assert surviving(caseload, age_group=AgeBand.older) == ["WC-0003"]
    assert surviving(caseload, age_group=AgeBand.oldest) == ["WC-0004"]


def test_the_age_band_follows_the_rule_document_and_not_a_constant() -> None:
    """AD-8, on the parameter this story added.

    The same worker lands in two different bands under two documents, and nothing
    in Python changed. The edge moves *down*, so a worker who was `younger`
    becomes `older` — the direction that would look like a correct answer if the
    band had been hardcoded at the shipped number.
    """
    caseload = [Row("WC-0001", age=AGE_OLDER_MIN - 1)]
    lowered = Segmentation(age_group=AgeBand.older)

    assert narrowed(caseload, lowered, _computers()) == []
    assert narrowed(caseload, lowered, _computers(age_older_min=AGE_OLDER_MIN - 1)) == caseload


def test_the_bands_come_from_the_registry_and_nothing_here_re_bands() -> None:
    """AD-10: both derived dimensions are registered computers, asked by name.

    The registry membership is the load-bearing half — a second computer for
    either value would be refused at import — and the two answers below are what
    makes "asked" rather than "declared" true: `Computers.of` builds both from
    one parameter block and `bands_of` returns exactly what they said.
    """
    assert {"risk", "age_band"} <= registered_names()
    thresholds = _thresholds()
    row = Row(severity_score=RISK_HIGH_MIN, age=AGE_OLDEST_MIN)

    bands = bands_of(row, Computers.of(thresholds))

    assert bands.severity_band is RiskBand.high
    assert bands.age_group is AgeBand.oldest
    assert age_band.for_thresholds(thresholds).of(age=row.age) is bands.age_group


def test_the_age_bands_edges_are_refused_when_they_would_delete_a_band() -> None:
    """`>=`, not `>`, and the difference is a band that vanishes silently.

    An equal pair does not invert the scale: `age_band` tests the edges downward,
    so `ageOlderMin == ageOldestMin` makes `older` unreachable and files every
    worker in it as `oldest` — no exception, no failing test, and every picker
    still rendering. `deferred-work.md` records the shipped `riskMedMin` hole this
    refusal exists not to repeat, and Story 5.2's review is why every parameter
    added since refuses equality.
    """
    with pytest.raises(RuleParameterError, match="ageOlderMin"):
        _thresholds(age_older_min=AGE_OLDEST_MIN)
    with pytest.raises(RuleParameterError, match="ageYoungerMin"):
        _thresholds(age_younger_min=AGE_OLDER_MIN)


@pytest.mark.parametrize(
    ("edge", "name"),
    [
        ("age_younger_min", "ageYoungerMin"),
        ("age_older_min", "ageOlderMin"),
        ("age_oldest_min", "ageOldestMin"),
    ],
)
def test_an_age_edge_outside_a_working_range_is_refused(edge: str, name: str) -> None:
    """The domain check, and the domain is a workforce rather than a 0-100 column.

    `ageOldestMin: 500` files the whole workforce as `youngest` and offers three
    groups that match nothing — silently, with every picker still rendering. So
    does `ageOldestMin: 119`, which the first spelling of this bound accepted
    because 120 is the domain of a human *age* rather than of a *cut-off*.

    **Both ends, and the message must name the parameter it is about.** The
    expected pattern is the parameter's full wire name, passed in beside the
    field rather than sliced out of it: the first version of this test matched
    `edge.replace("_min", "Min")[:3]`, which is the regex `"age"` for all three
    cases — so an implementation that refused `ageOldestMin` while naming
    `ageYoungerMin` was green, and every case asserted the same thing.
    """
    for outside in (500, MAX_WORKING_AGE + 1, 0, MIN_WORKING_AGE - 1):
        with pytest.raises(RuleParameterError, match=name):
            _thresholds(**{edge: outside})


def test_age_edges_too_close_together_are_refused_although_each_is_in_range() -> None:
    """Three edges inside the range and strictly ordered can still be nonsense.

    `(14, 15, 16)` passes the per-edge domain check and passes the ordering pair
    below it, and files a 22-year-old as `oldest` with three of the four bands
    describing nobody — the domain check's own failure, assembled out of values
    that each satisfy it. The spread is the only thing that sees it.

    Refused on the *first-to-last* span rather than on each adjacent pair,
    because that is the property a reader can state: four bands over a workforce
    need the scale to cover one.
    """
    with pytest.raises(RuleParameterError, match="spans"):
        _thresholds(
            age_younger_min=MIN_WORKING_AGE,
            age_older_min=MIN_WORKING_AGE + 1,
            age_oldest_min=MIN_WORKING_AGE + 2,
        )
    # …and the narrowest spread that *is* allowed still loads, so the bound is a
    # floor rather than an accidental ban on retuning the scale.
    edges = _thresholds(
        age_younger_min=MIN_WORKING_AGE,
        age_older_min=MIN_WORKING_AGE + 1,
        age_oldest_min=MIN_WORKING_AGE + MIN_AGE_BAND_SPAN,
    )
    assert edges.age_oldest_min - edges.age_younger_min == MIN_AGE_BAND_SPAN


# --- the chips -------------------------------------------------------------


def test_the_chips_are_drawn_in_the_vocabularys_order_and_not_the_callers() -> None:
    """A chip row is the *server's* reading of the URL, in one fixed sequence.

    Set in an order deliberately unlike the published one, because that is the
    failure worth catching: a chip row built by walking the caller's parameters
    would render differently depending on which one FastAPI happened to bind
    first.
    """
    applied = applied_of(
        Segmentation(gender=Gender.female, severity_band=RiskBand.high, sector="Aerospace"),
        [Row()],
    )

    assert [chip.key for chip in applied] == ["severityBand", "sector", "gender"]
    assert [chip.value for chip in applied] == ["high", "Aerospace", "female"]


def test_only_the_employer_chip_carries_a_server_resolved_name() -> None:
    """`AppliedFilter.display`'s split, with `handlerId` out of the vocabulary.

    An id is not a label and nothing in the browser can turn `employerId=1` into
    "3M" on a cold URL load; the two enums, the two bands and the five free-text
    dimensions all carry a value the SPA already owns copy for or *is* the label.
    """
    applied = applied_of(
        Segmentation(employer_id=1, severity_band=RiskBand.low, region="Midwest"),
        [Row(employer_id=1, employer_label="3M")],
    )

    assert {chip.key: chip.display for chip in applied} == {
        "severityBand": None,
        "employerId": "3M",
        "region": None,
    }


def test_an_employer_no_row_carries_gets_no_name_rather_than_somebody_elses() -> None:
    """AD-7 on a caption: the chip must not confirm an employer's existence.

    A caller pasting an id from outside their book gets `display: null` — the same
    answer an employer with no claims today gets, which is `deferred-work.md`'s
    recorded conflation and the direction that leaks nothing.
    """
    applied = applied_of(Segmentation(employer_id=99), [Row(employer_id=1)])

    assert [(chip.key, chip.value, chip.display) for chip in applied] == [
        ("employerId", "99", None)
    ]


# --- the values a picker may offer ----------------------------------------


def test_the_values_are_folded_from_the_callers_own_rows() -> None:
    """A picker cannot offer a value with no claims behind it.

    Which is what keeps emptiness an *answer* rather than a navigational dead
    end: every option on the control leads somewhere, so a zero-result page is
    something the analyst composed rather than something the control offered.
    """
    caseload = [
        Row("WC-0001", sector="Aerospace", region="Midwest"),
        Row("WC-0002", sector="Automotive", region="Midwest"),
    ]

    values = values_of(caseload, Segmentation(), _computers(), _thresholds())
    by_key = {
        dimension.key: [option.value for option in dimension.values]
        for dimension in values.dimensions
    }

    assert by_key["sector"] == ["Aerospace", "Automotive"]
    assert by_key["region"] == ["Midwest"]
    # …and the two derived dimensions offer only the bands some claim is in.
    assert by_key["severityBand"] == ["low"]
    assert by_key["ageGroup"] == ["youngest"]


def test_the_options_are_the_unfiltered_book_and_the_count_is_the_filtered_one() -> None:
    """The asymmetry the endpoint exists for, and the reason it is not a bug.

    A picker whose options had been cut by the active filter could not be used to
    *widen* one, which would make every narrowing a one-way door. So the options
    describe what the caller could ask and `claimsMatching` describes what they
    have asked.
    """
    caseload = [Row("WC-0001", sector="Aerospace"), Row("WC-0002", sector="Automotive")]

    values = values_of(caseload, Segmentation(sector="Aerospace"), _computers(), _thresholds())
    sectors = next(d for d in values.dimensions if d.key == "sector")

    assert [option.value for option in sectors.values] == ["Aerospace", "Automotive"]
    assert values.claims_in_scope == 2
    assert values.claims_matching == 1


def test_a_vocabulary_keeps_its_declaration_order_and_free_text_sorts() -> None:
    """Two ordering rules, and which applies is a property of the dimension.

    "High, Low, Medium" is a severity picker nobody can scan, so a band keeps the
    order its enum declares; a sector has no declared order, so the only one a
    reader can verify from the control itself is ascending by what is on screen.
    """
    caseload = [
        Row("WC-0001", severity_score=RISK_MED_MIN - 1, sector="Zinc", age=AGE_OLDEST_MIN),
        Row("WC-0002", severity_score=RISK_HIGH_MIN, sector="Aerospace", age=30),
        Row("WC-0003", severity_score=RISK_MED_MIN, sector="Motors", age=AGE_OLDER_MIN),
    ]

    values = values_of(caseload, Segmentation(), _computers(), _thresholds())
    by_key = {
        dimension.key: [option.value for option in dimension.values]
        for dimension in values.dimensions
    }

    assert by_key["severityBand"] == ["high", "med", "low"]
    assert by_key["ageGroup"] == ["youngest", "older", "oldest"]
    assert by_key["sector"] == ["Aerospace", "Motors", "Zinc"]


def test_the_employer_options_carry_their_labels_and_sort_by_them() -> None:
    """The one labelled dimension, ordered by what a reader sees.

    Sorted on the label with the id behind it, which is not cosmetic: two
    employers may share a `short_name`, and an order decided only by the label
    would leave the two options in whatever order the fold happened to see them.
    """
    caseload = [
        Row("WC-0001", employer_id=7, employer_label="Toyota"),
        Row("WC-0002", employer_id=2, employer_label="Boeing"),
    ]

    values = values_of(caseload, Segmentation(), _computers(), _thresholds())
    employers = next(d for d in values.dimensions if d.key == "employerId")

    assert [(option.value, option.label) for option in employers.values] == [
        ("2", "Boeing"),
        ("7", "Toyota"),
    ]


def test_the_age_edges_are_published_because_the_band_names_carry_no_numbers() -> None:
    """One source for the range label, quoted once.

    `AgeBand`'s members are ordinal words precisely so that moving an edge in the
    document cannot leave a member name asserting the old one; the consequence is
    that the browser needs the edges to compose "the range" from, and this payload
    is where they come from.
    """
    values = values_of([Row()], Segmentation(), _computers(), _thresholds())

    assert (values.age_younger_min, values.age_older_min, values.age_oldest_min) == (
        AGE_YOUNGER_MIN,
        AGE_OLDER_MIN,
        AGE_OLDEST_MIN,
    )
    assert not any(character.isdigit() for band in AgeBand for character in band.value)


def test_an_empty_book_publishes_every_dimension_with_no_values() -> None:
    """The zero-result state at its extreme: ten pickers, all empty, no error.

    The *dimensions* are still all ten, because which questions may be asked is a
    property of the vocabulary rather than of the data — a control that lost a
    picker on an empty book would be a control that changed shape.
    """
    values = values_of([], Segmentation(sector="Aerospace"), _computers(), _thresholds())

    assert [dimension.key for dimension in values.dimensions] == list(EXPECTED_WIRE_KEYS_BARE)
    assert all(dimension.values == () for dimension in values.dimensions)
    assert values.claims_in_scope == 0
    assert values.claims_matching == 0
    # The chip is still drawn: an analyst on an empty result must be able to clear
    # the filter that produced it, or they are stranded.
    assert [chip.key for chip in values.applied_filters] == ["sector"]


# --- Hypothesis: a filter can only narrow (AD-7) --------------------------
#
# The first property test on an analyst aggregate, and it is here rather than on
# `fraud` or `trends` because this is the module every one of them narrows
# through: a property proved of `narrowed` is a property of all four surfaces at
# once.
#
# The vocabulary the strategies draw from is deliberately **wider than the
# caseload's**: a sector nobody carries and a band nobody is in are exactly the
# values a hand-written case would forget, and the property has to hold for them
# too — that is what "an impossible combination is an empty answer" means as a
# statement about every combination rather than about the two somebody thought of.

rows = st.builds(
    Row,
    claim_id=st.integers(min_value=1, max_value=40).map(lambda n: f"WC-{n:04d}"),
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
    injury_type=st.none() | st.sampled_from(["Laceration", "Fracture", "Burn", "Crush"]),
    state=st.none() | st.sampled_from(["WA", "MI", "IL", "TX"]),
    employer_id=st.none() | st.integers(min_value=1, max_value=6),
    disability=st.none() | st.sampled_from(list(Disability)),
    sector=st.none() | st.sampled_from(["Aerospace", "Automotive", "Appliances", "Mining"]),
    region=st.none() | st.sampled_from(["Midwest", "Northeast", "West", "Southeast"]),
    icd10=st.none() | st.sampled_from(["S61.219A", "W24.0XXA", "G56.00", "T07.XXXA"]),
    age_group=st.none() | st.sampled_from(list(AgeBand)),
    gender=st.none() | st.sampled_from(list(Gender)),
)


@given(caseloads, segmentations)
@hyp_settings(max_examples=300)
def test_a_filter_can_only_ever_narrow(caseload: list[Row], filters: Segmentation) -> None:
    """AD-7's central promise, over arbitrary filters rather than chosen ones.

    The filtered set is always a **subset** of the unfiltered one — never a row
    the caller was not already entitled to read, never a row invented, never a
    row duplicated. It is the general form of the sentence every service
    docstring in this story asserts, and the only form that survives a dimension
    being added.
    """
    computers = _computers()
    filtered = narrowed(caseload, filters, computers)

    assert {row.claim_id for row in filtered} <= {row.claim_id for row in caseload}
    assert len(filtered) <= len(caseload)
    for row in filtered:
        assert row in caseload


@given(caseloads, segmentations)
@hyp_settings(max_examples=300)
def test_filtering_twice_is_filtering_once(caseload: list[Row], filters: Segmentation) -> None:
    """Idempotence, which is what "narrow, then look again" rests on.

    An analyst applies a filter, the workspace refetches, and every section folds
    the same predicate over its own read. If applying it twice removed anything,
    two sections that happened to apply it a different number of times would
    describe different populations — and the failure would look like a real
    difference between two charts.
    """
    computers = _computers()
    once = narrowed(caseload, filters, computers)

    assert narrowed(once, filters, computers) == once


@given(caseloads, segmentations)
@hyp_settings(max_examples=200)
def test_the_order_the_caseload_arrived_in_is_preserved(
    caseload: list[Row], filters: Segmentation
) -> None:
    """A filter is a cut, never a re-ordering.

    It matters to exactly one consumer today — `fraud.red_flags_of` fixes a
    clause's published spelling by first arrival, which is `claim_id` order from
    the repository — and a filter that reordered its input would make that
    spelling a property of the filter rather than of the data.
    """
    filtered = narrowed(caseload, filters, _computers())
    survivors = {id(row) for row in filtered}

    assert [row for row in caseload if id(row) in survivors] == filtered


@given(caseloads)
@hyp_settings(max_examples=200)
def test_every_value_a_picker_offers_matches_at_least_one_claim(caseload: list[Row]) -> None:
    """The values endpoint's whole contract, as a property.

    A control that could offer a value with no claims behind it would lead an
    analyst to a zero-result page that was unreachable from the data — and it is
    the shape a hardcoded vocabulary, or a values list read off an enum, would
    take.
    """
    computers = _computers()
    values = values_of(caseload, Segmentation(), computers, _thresholds())

    for dimension in values.dimensions:
        key = next(k for k in SEGMENTATION_KEYS if SEGMENTATION_WIRE_KEYS[k] == dimension.key)
        for option in dimension.values:
            filters = Segmentation(**{key: _typed(key, option.value)})
            assert narrowed(caseload, filters, computers), (dimension.key, option.value)


def _typed(key: str, wire: str) -> Any:
    """One wire value back into the field's own type — the route's coercion.

    Restated here rather than reached for in `drill_through._COERCE`, because the
    property above is about the *values endpoint agreeing with the filter*, and
    borrowing the implementation's coercion to check it would let one bug satisfy
    both halves.
    """
    if key == "severity_band":
        return RiskBand(wire)
    if key == "age_group":
        return AgeBand(wire)
    if key == "disability":
        return Disability(wire)
    if key == "gender":
        return Gender(wire)
    if key == "employer_id":
        return int(wire)
    return wire


# --- the gate (AD-7), without a database ---------------------------------


def test_the_values_endpoint_reuses_the_analyst_workspace_allowlist() -> None:
    """One allowlist for one workspace, not a third spelling of it.

    Story 7.1 declared `FRAUD_ANALYTICS_ROLES`; 7.2's section reused it and this
    story's control reuses it again. Two spellings of one allowlist is how a
    future `UserRole` gets admitted by one of them.
    """
    assert frozenset({UserRole.analyst}) == fraud_service.FRAUD_ANALYTICS_ROLES


@pytest.mark.parametrize("role", sorted(set(UserRole) - {UserRole.analyst}))
async def test_every_role_outside_the_allowlist_is_refused(role: UserRole) -> None:
    """Enumerated from the enum, so a role added later fails here rather than
    inheriting the analyst workspace.

    The session is passed as `None`, so a read of any kind would be an
    `AttributeError` rather than a silent success — which is what makes "the
    refusal precedes the read" a property of this test rather than a claim.
    """
    assert role not in fraud_service.FRAUD_ANALYTICS_ROLES
    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await segmentation_values(
            None,  # type: ignore[arg-type]
            CallerContext(user_id=1, role=role, employer_ids=frozenset({1})),
            _thresholds(),
            Segmentation(),
        )


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

    `test_fraud_analytics.py`'s helper, restated here rather than imported: a test
    module importing another test module's private helper is how two unrelated
    suites acquire a shared failure mode. Rule-document loads are *not* excluded
    and do not need to be — every parameter block arrives as an argument, so a
    fold that reached the engine would show up as an extra statement, which is
    exactly what these tests are for.
    """
    original = db.execute

    async def counting(statement: Any, *args: Any, **kwargs: Any) -> Any:
        executed.append(str(statement).split("\n")[0])
        return await original(statement, *args, **kwargs)

    return original, counting


#: The window that covers every seeded claim, in months — `test_trend_analytics`'
#: constant, restated rather than imported for `_counting`'s reason.
WHOLE_YEAR = {"from": "2026-01-01", "to": "2026-12-31"}

#: One filter set the seeded portfolio genuinely narrows on, as the URL spells it.
#:
#: Two dimensions, and deliberately one stored column and one *derived* band: the
#: pair is what makes AC 1's "folded over the intersection" a real intersection
#: rather than two readings of one column, and it is the combination a SQL
#: push-down could not express.
TWO_DIMENSIONS = {"filter[sector]": "Aerospace", "filter[severityBand]": "high"}


@requires_db
async def test_a_supervisor_and_a_handler_are_both_refused(seeded_db_url: str) -> None:
    """The inversion Story 7.1 introduced, holding for the workspace's control.

    A gate added to three routes and forgotten on the fourth is the ordinary way
    a workspace half-opens, so the refusal is asserted on *this* route rather
    than inherited.
    """
    for persona in (SUPERVISOR, ("Kaya Johnson", "handler")):
        async with make_client(seeded_db_url) as client:
            await login_as(client, *persona)
            resp = await client.get(VALUES)

        assert resp.status_code == 403, persona
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["type"] == "/problems/fraud-analytics-not-permitted"
        assert resp.headers["cache-control"] == "no-store"


@requires_db
async def test_the_refusal_happens_before_any_claim_is_read(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordering, asserted rather than described.

    A refusal that depended on what the scoped read returned would answer
    differently for an analyst with employers and one between assignments, which
    is an oracle about the assignment table.
    """

    async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a claim was read before the refusal")

    monkeypatch.setattr(claim_repo, "select_drill_rows", forbidden)
    ctx = CallerContext(user_id=1, role=UserRole.supervisor, employer_ids=frozenset({1}))

    with pytest.raises(fraud_service.FraudAnalyticsNotPermitted):
        await segmentation_values(db, ctx, await thresholds_for(db), Segmentation())


@requires_db
async def test_a_refused_caller_loads_no_rule_document_either(
    seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate is above the document load in the route, not below it.

    The refusal has to precede *every* read on this path, and the rule-document
    read is the one the route makes on the service's behalf — so it is patched to
    fail rather than trusted to be ordered correctly.
    """
    from api.routers import dashboard as dashboard_router

    async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a rule document was loaded before the refusal")

    monkeypatch.setattr(dashboard_router, "thresholds_for", forbidden)

    async with make_client(seeded_db_url) as client:
        await login_as(client, *SUPERVISOR)
        resp = await client.get(VALUES)

    assert resp.status_code == 403
    assert resp.json()["type"] == "/problems/fraud-analytics-not-permitted"


@requires_db
async def test_the_values_endpoint_takes_exactly_one_scoped_read(db: AsyncSession) -> None:
    """ "One scoped read, one pure fold" as a counted fact rather than a claim."""
    ctx = await context_for(db, *ANALYST)
    thresholds = await thresholds_for(db)
    executed: list[str] = []
    original, counting = _counting(db, executed)

    db.execute = counting  # type: ignore[method-assign]
    try:
        await segmentation_values(db, ctx, thresholds, Segmentation(sector="Aerospace"))
    finally:
        db.execute = original  # type: ignore[method-assign]

    assert len(executed) == 1, executed
    assert executed[0].startswith("SELECT"), executed


@requires_db
async def test_the_panel_still_takes_exactly_one_scoped_read_under_a_filter(
    db: AsyncSession,
) -> None:
    """A filter narrows the fold and adds no query, counted.

    Story 7.1's guard runs unfiltered and would stay green against an
    implementation that answered a *filtered* request with a second read — a
    narrowing pushed into SQL, or a lookup to resolve a chip. This is that guard
    with the filter on.
    """
    ctx = await context_for(db, *ANALYST)
    thresholds = await thresholds_for(db)
    executed: list[str] = []
    original, counting = _counting(db, executed)

    db.execute = counting  # type: ignore[method-assign]
    try:
        await fraud_panel(db, ctx, thresholds, Segmentation(sector="Aerospace"))
        await fraud_rates(
            db, ctx, thresholds, FraudRateSorts(), Segmentation(severity_band=RiskBand.high)
        )
    finally:
        db.execute = original  # type: ignore[method-assign]

    assert len(executed) == 2, executed
    assert all(statement.startswith("SELECT") for statement in executed), executed


@requires_db
@pytest.mark.parametrize("cohort", list(trend_service.TrendCohort))
async def test_the_trends_aggregate_still_takes_one_scoped_read_under_a_filter(
    db: AsyncSession, cohort: trend_service.TrendCohort
) -> None:
    """The parametrised trends guard, with a filter on.

    Parametrised over the cohort because the tempting implementation is the one
    that reads per cohort once a filter is involved — and because Design Note 4's
    retired clause was exactly "grain × cohort × segmentation on one request".
    """
    ctx = await context_for(db, *ANALYST)
    thresholds = await thresholds_for(db)
    periods = await trend_periods_for(db)
    executed: list[str] = []
    original, counting = _counting(db, executed)

    db.execute = counting  # type: ignore[method-assign]
    try:
        await trend_service.portfolio_trends(
            db,
            ctx,
            thresholds,
            periods,
            SETTINGS,
            Segmentation(sector="Aerospace", severity_band=RiskBand.high),
            cohort=cohort,
        )
    finally:
        db.execute = original  # type: ignore[method-assign]

    assert len(executed) == 1, executed


# --- scope (AD-7), where an analyst can actually be scoped ----------------


@requires_db
async def test_a_scoped_analysts_values_name_only_her_own_book(db: AsyncSession) -> None:
    """The picker cannot enumerate anything outside the caller's book.

    This is the endpoint on the workspace that *lists* what exists — employers,
    sectors, regions, ICD-10 codes — so it is the one where a scope leak would be
    an enumeration rather than a count. Jennifer Park's employer set is used
    because it is a real subset with a known shape rather than an arbitrary pair
    of ids.
    """
    scoped_employers = {
        seed_fixture.employer_id_of(name) for name in seed_fixture.employers_of(*SCOPED_SUPERVISOR)
    }
    ctx = CallerContext(user_id=1, role=UserRole.analyst, employer_ids=frozenset(scoped_employers))

    values = await segmentation_values(db, ctx, await thresholds_for(db), Segmentation())
    expected = seed_fixture.expected_segmentation_values(*SCOPED_SUPERVISOR)

    assert values.claims_in_scope == expected["claimsInScope"]
    assert [
        {
            "key": dimension.key,
            "values": [{"value": v.value, "label": v.label} for v in dimension.values],
        }
        for dimension in values.dimensions
    ] == expected["dimensions"]
    # …and the employer ids it names are hers, which is the enumeration half.
    employers = next(d for d in values.dimensions if d.key == "employerId")
    assert {int(option.value) for option in employers.values} <= scoped_employers


@requires_db
async def test_an_analyst_with_no_employers_reads_an_empty_book_not_the_portfolio(
    db: AsyncSession,
) -> None:
    """`employer_scope` reads "assigned to nobody" as a predicate matching nothing.

    The distinction is the whole of AD-7's mechanism and the one that fails open
    if anybody ever writes `if not ctx.employer_ids: skip the filter`. On this
    endpoint the failure would be maximally visible — every employer in the
    portfolio in a picker.
    """
    ctx = CallerContext(user_id=0, role=UserRole.analyst, employer_ids=frozenset())

    values = await segmentation_values(db, ctx, await thresholds_for(db), Segmentation())

    assert values.claims_in_scope == 0
    assert values.claims_matching == 0
    assert [dimension.key for dimension in values.dimensions] == list(EXPECTED_WIRE_KEYS_BARE)
    assert all(dimension.values == () for dimension in values.dimensions)


@requires_db
async def test_every_claim_behind_a_scoped_analysts_filtered_panel_is_inside_that_book(
    db: AsyncSession,
) -> None:
    """A filter narrows inside the scope and can never reach past it.

    The property the Hypothesis case above proves in general, asserted here
    against the *seeded* book so a scope predicate that had been dropped from the
    read would fail even though the pure fold is still correct.
    """
    scoped_employers = {
        seed_fixture.employer_id_of(name) for name in seed_fixture.employers_of(*SCOPED_SUPERVISOR)
    }
    ctx = CallerContext(user_id=1, role=UserRole.analyst, employer_ids=frozenset(scoped_employers))
    thresholds = await thresholds_for(db)

    panel = await fraud_panel(db, ctx, thresholds, Segmentation(severity_band=RiskBand.high))
    expected = seed_fixture.expected_segmented_ids(*SCOPED_SUPERVISOR, severityBand="high")

    assert panel.claims_in_scope == len(expected)
    assert panel.by_band.total == len(expected)
    assert expected <= seed_fixture.expected_claim_ids(*SCOPED_SUPERVISOR)


# --- the whole workspace recomputes under one filter (AC 1) ---------------


@requires_db
async def test_every_analyst_surface_folds_over_the_same_intersection(
    seeded_db_url: str,
) -> None:
    """AC 1, as an equality between four live endpoints and one oracle.

    Two dimensions — one stored column and one *derived* band, so the pair is a
    real intersection rather than two readings of one column — sent to all four
    analyst routes at once. Each publishes a population figure, and the assertion
    is that all four equal the seed's own answer: a constant would pass while
    every surface was wrong together, and an equality between the surfaces alone
    would pass while they agreed on a wrong number.
    """
    expected = seed_fixture.expected_segmented_ids(
        *ANALYST, sector="Aerospace", severityBand="high"
    )

    panel = await get_json(seeded_db_url, *ANALYST, PANEL, params=TWO_DIMENSIONS)
    rates = await get_json(seeded_db_url, *ANALYST, RATES, params=TWO_DIMENSIONS)
    flags = await get_json(seeded_db_url, *ANALYST, RED_FLAGS, params=TWO_DIMENSIONS)
    trends = await get_json(
        seeded_db_url, *ANALYST, TRENDS, params={**TWO_DIMENSIONS, **WHOLE_YEAR}
    )
    values = await get_json(seeded_db_url, *ANALYST, VALUES, params=TWO_DIMENSIONS)

    assert len(expected) != 0, "the filter has to actually narrow, or this asserts nothing"
    assert panel["claimsInScope"] == len(expected)
    assert panel["byBand"]["total"] == len(expected)
    assert rates["claimsInScope"] == len(expected)
    assert flags["claimsInScope"] == len(expected)
    assert trends["claimsInScope"] == len(expected)
    assert values["claimsMatching"] == len(expected)
    # …and `claimsInScope` on the values payload is the **unfiltered** book, which
    # is the one figure on this screen that must not move with the filter.
    assert values["claimsInScope"] == len(seed_fixture.expected_claim_ids(*ANALYST))


@requires_db
async def test_a_segmented_aggregate_reconciles_with_the_drill_list_it_opens(
    seeded_db_url: str,
) -> None:
    """AC 2's promise, as **set identity** rather than as two equal counts.

    The workspace's filter and the drill list's are the same words, so sending the
    identical query string to `/dashboard/claims` has to return exactly the claims
    the panel folded — and the assertion compares the ids, because a count is
    satisfiable by the wrong population and that is the failure this story's
    oracles were written to catch.
    """
    expected = seed_fixture.expected_segmented_ids(
        *ANALYST, sector="Aerospace", severityBand="high"
    )
    panel = await get_json(seeded_db_url, *ANALYST, PANEL, params=TWO_DIMENSIONS)
    drill = await get_json(seeded_db_url, *ANALYST, DRILL, params=TWO_DIMENSIONS)

    assert drill["total"] == panel["claimsInScope"]
    assert {row["claimId"] for row in drill["items"]} == expected


@requires_db
async def test_a_workspace_filter_and_a_clicked_slice_compose_into_three_chips(
    seeded_db_url: str,
) -> None:
    """AC 2's other half: the segmentation survives the click *and* the slice lands.

    The drill URL is the workspace's own two parameters plus the one the segment
    published, and the server's reading of it is three chips — in `FILTER_KEYS`
    order, which is why the fraud band's chip is drawn before the sector's even
    though the analyst chose the sector first.
    """
    merged = {**TWO_DIMENSIONS, "filter[fraudBand]": "high"}

    drill = await get_json(seeded_db_url, *ANALYST, DRILL, params=merged)

    assert [chip["key"] for chip in drill["appliedFilters"]] == [
        "severityBand",
        "fraudBand",
        "sector",
    ]
    assert drill["total"] == len(
        seed_fixture.expected_segmented_ids(*ANALYST, sector="Aerospace", severityBand="high")
        & {
            claim["claim_id"]
            for claim in seed_fixture.claims_for(*ANALYST)
            if seed_fixture.fraud_band(claim["fraud_score"]) == "high"
        }
    )


@requires_db
async def test_an_impossible_combination_is_a_200_with_empty_aggregates(
    seeded_db_url: str,
) -> None:
    """AC 4's zero-result state, end to end and on every surface.

    Not an error and not an empty *page*: a 200 whose every aggregate is empty,
    with the band vocabulary still complete (a rule's answer is always three
    segments, and "nothing scored high" is the headline), the trend window still
    published, and the chips still drawn so the analyst can clear what stranded
    them.
    """
    impossible = {"filter[sector]": "Aerospace", "filter[state]": "MI"}
    assert seed_fixture.expected_segmented_ids(*ANALYST, sector="Aerospace", state="MI") == set()

    panel = await get_json(seeded_db_url, *ANALYST, PANEL, params=impossible)
    trends = await get_json(seeded_db_url, *ANALYST, TRENDS, params={**impossible, **WHOLE_YEAR})
    values = await get_json(seeded_db_url, *ANALYST, VALUES, params=impossible)
    drill = await get_json(seeded_db_url, *ANALYST, DRILL, params=impossible)

    assert panel["claimsInScope"] == 0
    assert [item["count"] for item in panel["byBand"]["items"]] == [0, 0, 0]
    assert panel["byBand"]["total"] == 0
    assert panel["siuByStage"]["items"] == []
    assert trends["claimsInWindow"] == 0
    assert all(series["seriesTotal"] == 0 for series in trends["series"])
    # The window's vocabulary survives an empty fold, which is what keeps the
    # period control and its drill button live — `TrendBucketResponse`'s reason.
    assert len(trends["buckets"]) == trends["bucketCount"] != 0
    assert values["claimsMatching"] == 0
    assert [chip["key"] for chip in values["appliedFilters"]] == ["state", "sector"]
    assert drill["total"] == 0


@requires_db
async def test_an_unknown_free_text_value_is_a_legitimate_empty_page(
    seeded_db_url: str,
) -> None:
    """A well-formed question whose answer is "none" — never a refusal."""
    payload = await get_json(
        seeded_db_url, *ANALYST, PANEL, params={"filter[region]": "Nonexistent"}
    )

    assert payload["claimsInScope"] == 0


@requires_db
@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("filter[gender]", "nonsense"),
        ("filter[ageGroup]", "25_34"),
        ("filter[severityBand]", "medium"),
        ("filter[disability]", "partial"),
    ],
)
async def test_an_unknown_enum_value_is_a_validation_error(
    seeded_db_url: str, parameter: str, value: str
) -> None:
    """The type *is* the check — refused by the contract, before the service.

    `filter[ageGroup]=25_34` is the interesting one: it is exactly the spelling a
    reader would guess from the label, and the band's members are ordinal words
    precisely so that a range never becomes a wire value. `severityBand=medium`
    is the second: `RiskBand` spells it `med` and `FraudBand` spells it `medium`,
    which is a real trap and one only the enum can close.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *ANALYST)
        resp = await client.get(PANEL, params={parameter: value})

    assert resp.status_code == 422
    assert resp.json()["type"] == "/problems/validation-error"
    # The refused parameter is named, which is what lets the workspace clear that
    # one dimension and keep the rest — AC 5's degradation, at its source.
    assert any(parameter in error["loc"] for error in resp.json()["errors"])


@requires_db
async def test_an_unknown_parameter_name_narrows_nothing_and_draws_no_chip(
    seeded_db_url: str,
) -> None:
    """Ignored, exactly as it is on every route in this file.

    And the chip row says so: `appliedFilters` is the *server's* reading of the
    URL, so a parameter it did not recognise cannot appear as a narrowing that
    never happened.
    """
    honest = await get_json(seeded_db_url, *ANALYST, VALUES)
    attempt = await get_json(seeded_db_url, *ANALYST, VALUES, params={"filter[nope]": "x"})

    assert attempt == honest
    assert attempt["appliedFilters"] == []


# --- the contract ---------------------------------------------------------


@requires_db
async def test_the_values_route_declares_exactly_the_ten_dimensions(
    seeded_db_url: str,
) -> None:
    """An allowlist of exactly ten names rather than an assertion of emptiness.

    Every one is a `filter[…]` narrowing applied after `employer_scope(ctx)`, so
    there is nowhere in the signature to put a user or an "as" — and
    `filter[employerId]` intersects the caller's book rather than choosing it.
    """
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"][VALUES]["get"]

    assert {parameter["name"] for parameter in operation["parameters"]} == set(EXPECTED_WIRE_KEYS)
    assert all(parameter["in"] == "query" for parameter in operation["parameters"])
    assert "requestBody" not in operation


@requires_db
async def test_query_parameters_cannot_widen_or_change_the_scope(seeded_db_url: str) -> None:
    """The smuggling attempt, on the endpoint that enumerates a book.

    The seeded analyst is `scope_all`, so no HTTP request can demonstrate a
    *narrowed* analyst — the gap 7.1 recorded and `deferred-work.md` still holds
    open. What can be demonstrated is that the answer is a function of the session
    plus the ten declared dimensions: a smuggled scope, caller or employer changes
    nothing about the response.
    """
    honest = await get_json(seeded_db_url, *ANALYST, VALUES)

    for smuggled in (
        {"employerId": "3"},
        {"employerIds": "1,2,3"},
        {"scopeAll": "true"},
        {"as": "7"},
        {"userId": "8"},
        {"handlerId": "1"},
    ):
        attempt = await get_json(seeded_db_url, *ANALYST, VALUES, params=smuggled)
        assert attempt == honest, f"{smuggled} changed the answer"


@requires_db
async def test_the_values_response_is_camel_case_and_carries_nothing_else(
    seeded_db_url: str, db: AsyncSession
) -> None:
    """The exact key set on the payload and on both nestings.

    So nothing is added unnoticed, and so a field that vanished is a failure
    rather than a silently empty picker.
    """
    payload = await get_json(
        seeded_db_url, *ANALYST, VALUES, params={"filter[sector]": "Aerospace"}
    )

    assert set(payload) == {
        "dimensions",
        "appliedFilters",
        "claimsInScope",
        "claimsMatching",
        "ageYoungerMin",
        "ageOlderMin",
        "ageOldestMin",
        "rulesVersion",
    }
    assert set(payload["dimensions"][0]) == {"key", "values"}
    assert set(payload["dimensions"][0]["values"][0]) == {"value", "label"}
    assert set(payload["appliedFilters"][0]) == {"key", "value", "display"}
    assert [dimension["key"] for dimension in payload["dimensions"]] == list(
        EXPECTED_WIRE_KEYS_BARE
    )
    thresholds = await thresholds_for(db)
    assert payload["rulesVersion"] == thresholds.version
    assert payload["ageYoungerMin"] == thresholds.age_younger_min
    assert payload["ageOlderMin"] == thresholds.age_older_min
    assert payload["ageOldestMin"] == thresholds.age_oldest_min


@requires_db
async def test_the_values_payloads_options_chips_and_counts_are_the_seeded_oracles(
    seeded_db_url: str,
) -> None:
    """Every option, every chip and both counts, against an independent oracle.

    `seed_fixture.expected_segmentation_values` joins the employee rows itself,
    bands the ages with its own function and puts each dimension in order by
    *executing* the story's two ordering rules rather than by restating the
    fold's expression — see `_expected_option_order`, which exists because the
    first version of it was the implementation's own `sorted(...)` key.

    **Named for what it checks.** It was `…_is_the_seeded_oracles_whole_answer`,
    and the payload has two fields the oracle does not carry: `rulesVersion`,
    which is a fact about the document in the database rather than about the seed
    file, is asserted against `thresholds_for(db)` in the test above.

    Two dimensions, and `employerId` is one of them on purpose: it is the only
    chip the server resolves a `display` for, so a request naming a sector alone
    would leave the whole of that resolution — nine nulls and one name — unasserted.
    """
    employer_id = seed_fixture.employer_id_of("Boeing")
    query = {"filter[sector]": "Aerospace", "filter[employerId]": str(employer_id)}
    payload = await get_json(seeded_db_url, *ANALYST, VALUES, params=query)
    expected = seed_fixture.expected_segmentation_values(
        *ANALYST, sector="Aerospace", employerId=str(employer_id)
    )

    assert {key: payload[key] for key in expected} == expected
    # …and the one resolved name is a name rather than the id echoed back, which
    # a chip row of ten nulls would also have satisfied.
    assert [chip["display"] for chip in payload["appliedFilters"]] == ["Boeing", None]


@requires_db
async def test_the_endpoint_requires_a_session(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.get(VALUES)

    assert resp.status_code == 401


@requires_db
async def test_the_response_is_never_cached(seeded_db_url: str) -> None:
    """One persona's book must not be served to another from upstream."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *ANALYST)
        resp = await client.get(VALUES)

    assert resp.headers["cache-control"] == "no-store"


@requires_db
async def test_reading_the_workspace_under_a_filter_writes_no_audit_event(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """A query is not a mutation, and a filtered query is still a query (AD-12).

    Counted before and after rather than asserted absent, because other rows
    exist: what must not happen is these requests adding one.
    """
    before = (await db.execute(sa.select(sa.func.count()).select_from(AuditEvent))).scalar_one()

    for path in (VALUES, PANEL, RATES, RED_FLAGS):
        await get_json(seeded_db_url, *ANALYST, path, params=TWO_DIMENSIONS)

    after = (await db.execute(sa.select(sa.func.count()).select_from(AuditEvent))).scalar_one()
    assert after == before


@requires_db
async def test_a_url_written_before_this_story_still_answers_identically(
    seeded_db_url: str,
) -> None:
    """Four facets were appended to the drill list and none was changed.

    A supervisor's bookmark from Story 5.5 has to produce the same list and the
    same chips in the same order — which is the contract `drill_through` owns and
    this story does not, and the reason the four went on the end.
    """
    payload = await get_json(seeded_db_url, *SUPERVISOR, DRILL, params={"filter[stage]": "settled"})
    oracle = seed_fixture.expected_drill_claims(*SUPERVISOR, stage="settled")

    assert payload["total"] == oracle["total"]
    assert [chip["key"] for chip in payload["appliedFilters"]] == ["stage"]
