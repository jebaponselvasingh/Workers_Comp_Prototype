"""Story 2.1 — what `rules/parameters.py` refuses, and why each refusal exists.

A rule document is *data an operator edits*, shipped by migration and never
reviewed by the tier that consumes it. So the boundary between JSON and
typed Python is the only place a bad parameter can be caught, and every
check below exists because the alternative is not a crash — it is a queue
that keeps sorting and answers wrongly:

- a cut-off outside its column's range bands the whole portfolio into one
  band and says nothing about it;
- a negative factor inverts the ordering it was meant to weight, and the
  result still looks perfectly well sorted;
- a status the enum does not know silently stops a scoring term from ever
  firing;
- a non-finite number reaches arithmetic that raises `OverflowError`, which
  is not a `ValueError` and so escapes every typed handler above it.

No database: `LoadedDocument` is a dataclass and the parameter block readers
are pure, so each case is one dict.
"""

import pytest

from data.models.enums import ClaimStatus, DocType
from rules.engine import LoadedDocument
from rules.parameters import (
    BenefitParams,
    DerivationThresholds,
    IntakeRequirements,
    PriorityWeights,
    RuleParameterError,
)
from services.financials import COMP_RATE_MAX_BP, COMP_RATE_MIN_BP

THRESHOLDS_DOC = LoadedDocument(key="derivation_thresholds", version=4, content={})
WEIGHTS_DOC = LoadedDocument(key="priority_weights", version=7, content={})
REQUIREMENTS_DOC = LoadedDocument(key="intake_required_documents", version=3, content={})
BENEFIT_DOC = LoadedDocument(key="benefit_params", version=2, content={})

VALID_THRESHOLDS = {
    "riskHighMin": 65,
    "riskMedMin": 35,
    "siuFraudScoreMin": 60,
    "rtwBlockedHashModulus": 5,
    "paymentDueHashModulus": 3,
    # Story 2.2's four, which arrived with version 2 of the document.
    "treatmentEarlyMaxRatio": 0.3,
    "treatmentActiveMaxRatio": 0.7,
    "recoveryYearExpectedDays": 180,
    "recoveryDefaultExpectedDays": 42,
    # Story 2.5's three, which arrived with version 3.
    "pathMinorSeverityMax": 35,
    "pathMinorRecoveryWindows": ["weeks_0_2"],
    "pathFatalitySeverityMin": 100,
    # Story 3.1's one, which arrived with version 4.
    "ptdSeverityThreshold": 85,
}

VALID_BENEFIT_PARAMS = {
    "defaultCompRateBp": 6667,
    "ptdCompRateBp": 10_000,
    "waitingPeriodDays": 7,
}

VALID_INTAKE_REQUIREMENTS = {"requiredDocTypes": ["froi", "incident", "medauth", "wage"]}

VALID_WEIGHTS = {
    "litigation": 40,
    "siuReview": 35,
    "rtwBlocked": 30,
    "pendingApproval": 25,
    "pendingApprovalStatuses": ["initial", "ch_assessment_process"],
    "paymentDue": 20,
    "surgery": 15,
    "severityFactor": 0.3,
    "daysOpenFactor": 0.2,
    "daysOpenCap": 60,
    "settledPenalty": -100,
    "markerThreshold": 30,
    "markerCount": 3,
    "pageLimit": 50,
}


def thresholds(**changes: object) -> DerivationThresholds:
    return DerivationThresholds.of(THRESHOLDS_DOC, {**VALID_THRESHOLDS, **changes})


def weights(**changes: object) -> PriorityWeights:
    return PriorityWeights.of(WEIGHTS_DOC, {**VALID_WEIGHTS, **changes})


def benefit(**changes: object) -> BenefitParams:
    return BenefitParams.of(BENEFIT_DOC, {**VALID_BENEFIT_PARAMS, **changes})


def requirements(**changes: object) -> IntakeRequirements:
    return IntakeRequirements.of(REQUIREMENTS_DOC, {**VALID_INTAKE_REQUIREMENTS, **changes})


def test_the_valid_blocks_are_valid() -> None:
    """Every negative case below changes exactly one key of these, so the
    delta *is* the thing under test."""
    assert thresholds().version == 4
    assert weights().version == 7
    assert requirements().version == 3
    assert benefit().version == 2


# --- types --------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "40", [40], {"n": 40}])
def test_a_non_numeric_weight_is_refused_naming_the_document(value: object) -> None:
    with pytest.raises(RuleParameterError, match="priority_weights v7"):
        weights(litigation=value)


def test_a_boolean_weight_is_refused_rather_than_scoring_as_one() -> None:
    """`True` is an `int` in Python, so a document answering `true` for a
    weight would score as 1 instead of being refused."""
    with pytest.raises(RuleParameterError, match="litigation"):
        weights(litigation=True)


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_a_non_finite_parameter_is_a_typed_refusal_not_an_overflow(value: float) -> None:
    """`int(float("inf"))` raises `OverflowError`, which is not a
    `ValueError` — so without this check the failure surfaced as whatever
    the first caller to convert it happened to raise, a long way from the
    document that caused it."""
    with pytest.raises(RuleParameterError, match="finite"):
        weights(severityFactor=value)

    with pytest.raises(RuleParameterError, match="finite"):
        weights(daysOpenCap=value)


def test_a_fractional_integer_parameter_is_refused() -> None:
    with pytest.raises(RuleParameterError, match="whole number"):
        weights(markerCount=2.5)


# --- ranges -------------------------------------------------------------


@pytest.mark.parametrize("bound", [-1, 101, 1000])
def test_a_risk_band_outside_severity_scores_range_is_refused(bound: int) -> None:
    with pytest.raises(RuleParameterError, match="riskHighMin"):
        thresholds(riskHighMin=bound)


def test_inverted_risk_bands_are_refused() -> None:
    """An inverted pair puts a score in two bands at once and the derivation
    reads them in order, so it would answer "high" for everything above the
    medium cut-off."""
    with pytest.raises(RuleParameterError, match="riskMedMin"):
        thresholds(riskMedMin=80, riskHighMin=65)


@pytest.mark.parametrize("bound", [-1, 101, 60_000])
def test_the_siu_fraud_cut_off_is_held_to_fraud_scores_range(bound: int) -> None:
    """`fraud_score` is a 0–100 column. A cut-off above it refers nobody for
    review, and a queue whose SIU filter matches nothing looks exactly like
    a portfolio with no fraud in it."""
    with pytest.raises(RuleParameterError, match="siuFraudScoreMin"):
        thresholds(siuFraudScoreMin=bound)


@pytest.mark.parametrize("modulus", [0, -3])
def test_a_hash_modulus_below_one_is_refused(modulus: int) -> None:
    """Zero is a `ZeroDivisionError` a hundred rows into a queue request;
    negative silently inverts the demo bucket."""
    with pytest.raises(RuleParameterError, match="Modulus"):
        thresholds(rtwBlockedHashModulus=modulus)


@pytest.mark.parametrize("factor_key", ["severityFactor", "daysOpenFactor"])
def test_a_negative_factor_is_refused_because_it_inverts_the_ordering(factor_key: str) -> None:
    """Both factors multiply a magnitude that only ever grows, so a negative
    one does not re-weight the term — it reverses it. The worst-injured
    claim sinks below the least and the queue still looks well sorted."""
    with pytest.raises(RuleParameterError, match="inverts"):
        weights(**{factor_key: -0.3})


def test_the_settled_penalty_is_allowed_to_be_negative() -> None:
    """The counterpart to the test above, and the reason it is two named
    factors rather than a blanket ban on negatives: the penalty is *added*,
    so the document owns its sign, and it is negative by design."""
    assert weights(settledPenalty=-250).settled_penalty == -250


@pytest.mark.parametrize("limit", [0, -1])
def test_a_page_limit_below_one_is_refused(limit: int) -> None:
    """A zero page returns nothing with a non-null cursor for ever: the
    queue renders nothing and never stops asking."""
    with pytest.raises(RuleParameterError, match="pageLimit"):
        weights(pageLimit=limit)


# --- the status set (AD-8: the whole rule element in one tier) ----------


def test_the_pending_approval_statuses_are_read_from_the_document() -> None:
    assert weights().pending_approval_statuses == frozenset(
        {ClaimStatus.initial, ClaimStatus.ch_assessment_process}
    )


def test_a_status_the_enum_does_not_know_is_refused_naming_the_value() -> None:
    """The failure this replaces is the quiet one: a scorer comparing raw
    strings reads a typo as "this status never matches", so the term stops
    firing, the queue keeps sorting, and every test not using that status
    stays green."""
    with pytest.raises(RuleParameterError) as raised:
        weights(pendingApprovalStatuses=["initial", "awaiting_manager"])

    message = str(raised.value)
    assert "priority_weights v7" in message
    assert "awaiting_manager" in message
    assert "pendingApprovalStatuses" in message
    # …and it says what the valid values are, because the reader is an
    # operator holding a document, not someone with the enum open.
    assert "ch_assessment_process" in message


@pytest.mark.parametrize("value", [None, "initial", 25, {"initial": True}])
def test_a_status_set_that_is_not_a_list_is_refused(value: object) -> None:
    with pytest.raises(RuleParameterError, match="pendingApprovalStatuses"):
        weights(pendingApprovalStatuses=value)


def test_an_empty_status_set_switches_the_term_off_rather_than_failing() -> None:
    """A legitimate document: "no status counts as pending approval" is how
    an operator retires the term without a deploy, which is the entire point
    of the set living in the document."""
    assert weights(pendingApprovalStatuses=[]).pending_approval_statuses == frozenset()


def test_a_repeated_status_is_a_set_not_an_error() -> None:
    assert weights(pendingApprovalStatuses=["initial", "initial"]).pending_approval_statuses == (
        frozenset({ClaimStatus.initial})
    )


def test_every_parameter_is_required() -> None:
    """Absent is refused exactly like malformed. A missing key defaulting to
    zero would be a weight silently switched off — the loudest possible
    change to a ranking, with nothing anywhere to say it happened."""
    for key in VALID_WEIGHTS:
        block = {k: v for k, v in VALID_WEIGHTS.items() if k != key}
        with pytest.raises(RuleParameterError, match=key):
            PriorityWeights.of(WEIGHTS_DOC, block)

    for key in VALID_THRESHOLDS:
        block = {k: v for k, v in VALID_THRESHOLDS.items() if k != key}
        with pytest.raises(RuleParameterError, match=key):
            DerivationThresholds.of(THRESHOLDS_DOC, block)

    for key in VALID_INTAKE_REQUIREMENTS:
        block = {k: v for k, v in VALID_INTAKE_REQUIREMENTS.items() if k != key}
        with pytest.raises(RuleParameterError, match=key):
            IntakeRequirements.of(REQUIREMENTS_DOC, block)


# --- Story 2.2: the treatment-phase parameters --------------------------


@pytest.mark.parametrize("ratio", [-0.1, 1.5, 42])
def test_a_phase_boundary_outside_zero_to_one_is_refused(ratio: float) -> None:
    """A ratio boundary outside 0–1 is a phase that can never be reached.

    `treatmentEarlyMaxRatio: 2` leaves every claim "Early" for twice its own
    recovery window; a negative one skips the phase entirely. Both render a
    perfectly ordinary banner while saying something false about the claim.
    """
    with pytest.raises(RuleParameterError, match="treatmentEarlyMaxRatio"):
        thresholds(treatmentEarlyMaxRatio=ratio)


def test_inverted_phase_boundaries_are_refused() -> None:
    """`riskMedMin > riskHighMin`'s failure, one derivation over: the
    derivation reads the boundaries in order, so an inverted pair collapses
    the middle phase without an error anywhere."""
    with pytest.raises(RuleParameterError, match="treatmentEarlyMaxRatio"):
        thresholds(treatmentEarlyMaxRatio=0.8, treatmentActiveMaxRatio=0.5)


def test_equal_phase_boundaries_are_allowed() -> None:
    """ "No active phase" is a legitimate rule, unlike an inverted pair.

    The same reading `riskMedMin == riskHighMin` gets: the document is
    saying a claim goes straight from early to approaching-MMI, which is an
    opinion an operator is allowed to hold.
    """
    block = thresholds(treatmentEarlyMaxRatio=0.5, treatmentActiveMaxRatio=0.5)
    assert block.treatment_early_max_ratio == block.treatment_active_max_ratio


@pytest.mark.parametrize("days", [0, -30])
def test_an_expected_recovery_window_below_one_day_is_refused(days: int) -> None:
    """Both constants are divisors: zero raises inside a request, and a
    negative window reverses the phase order while still returning a phase."""
    with pytest.raises(RuleParameterError, match="recoveryYearExpectedDays"):
        thresholds(recoveryYearExpectedDays=days)
    with pytest.raises(RuleParameterError, match="recoveryDefaultExpectedDays"):
        thresholds(recoveryDefaultExpectedDays=days)


# --- Story 2.2: the intake required-document list ------------------------


def test_the_required_documents_keep_the_documents_order() -> None:
    """A tuple, not a set: the checklist renders one row per required type,
    in the order the document lists them rather than a hash's."""
    assert requirements(requiredDocTypes=["wage", "froi"]).required_doc_types == (
        DocType.wage,
        DocType.froi,
    )


@pytest.mark.parametrize("value", [None, "froi", 4, {"type": "froi"}])
def test_a_required_document_list_that_is_not_a_list_is_refused(value: object) -> None:
    with pytest.raises(RuleParameterError, match="requiredDocTypes"):
        requirements(requiredDocTypes=value)


def test_a_document_type_the_enum_does_not_know_is_refused_naming_it() -> None:
    """`pendingApprovalStatuses`' argument: a scorer comparing raw strings
    treats a typo as "this never matches" — a checklist row that is silently
    never required, on a screen whose whole job is to say what is missing."""
    with pytest.raises(RuleParameterError, match="'osha'"):
        requirements(requiredDocTypes=["froi", "osha"])


def test_a_repeated_document_type_is_refused_unlike_a_repeated_status() -> None:
    """The one place this reader deliberately disagrees with `_status_set`.

    A repeated member of a *set* said nothing new. A repeated member of a
    checklist is a row rendered twice, which is a document bug rather than a
    stronger requirement.
    """
    with pytest.raises(RuleParameterError, match="twice"):
        requirements(requiredDocTypes=["froi", "froi"])


def test_an_empty_required_list_is_a_checklist_with_no_rows() -> None:
    """Legitimate, for `pendingApprovalStatuses`' reason: "nothing is
    required at intake" is a position an operator may take from the
    document, and the UI's empty state is what renders it."""
    assert requirements(requiredDocTypes=[]).required_doc_types == ()


# --- Story 3.1: the PTD threshold and the benefit parameters -------------


@pytest.mark.parametrize("value", [-1, 101, 1000])
def test_the_ptd_threshold_is_held_to_severity_scores_range(value: int) -> None:
    """`riskHighMin`'s argument, applied to the cut-off that decides pay.

    A threshold above 100 is unreachable, so every permanently disabled
    worker is classified `ppd` and paid two thirds of wage where the statute
    pays all of it — silently, with the card still rendering a confident
    figure. Below zero it fires for everyone, which pays a partial disability
    at the total rate.
    """
    with pytest.raises(RuleParameterError, match="ptdSeverityThreshold"):
        thresholds(ptdSeverityThreshold=value)


@pytest.mark.parametrize("value", [0, 100])
def test_the_ptd_threshold_may_sit_on_either_end_of_the_range(value: int) -> None:
    """Both bounds inclusive: "every permanent disability is total" and "none
    is" are positions an operator may take from the document, and neither is
    the kind of mistake a range check should be catching."""
    assert thresholds(ptdSeverityThreshold=value).ptd_severity_threshold == value


@pytest.mark.parametrize("value", [None, "6667", [6667], True])
def test_a_non_integer_comp_rate_parameter_is_refused(value: object) -> None:
    """`True` is in the list for the reason it is everywhere else in this
    file: it is an `int` in Python, and a document answering `true` for the
    default comp rate would pay one basis point of wage — 0.01% — rather
    than being refused."""
    with pytest.raises(RuleParameterError, match="benefit_params v2"):
        benefit(defaultCompRateBp=value)


@pytest.mark.parametrize("value", [-1, 15_001, 100_000])
def test_a_comp_rate_parameter_outside_the_overrides_domain_is_refused(value: int) -> None:
    """A default a handler's own override input could not reproduce.

    The card offers 0-150%; a document declaring 1000% would render a comp
    rate nobody can correct back down, because the ↺ reset restores *this*
    value. Both parameters are checked, because either can be the one that
    applies to a given claim.
    """
    with pytest.raises(RuleParameterError, match="defaultCompRateBp"):
        benefit(defaultCompRateBp=value)
    with pytest.raises(RuleParameterError, match="ptdCompRateBp"):
        benefit(ptdCompRateBp=value)


@pytest.mark.parametrize("value", [COMP_RATE_MIN_BP, COMP_RATE_MAX_BP])
def test_a_comp_rate_parameter_may_sit_on_either_bound(value: int) -> None:
    assert benefit(defaultCompRateBp=value).default_comp_rate_bp == value


def test_the_rules_tiers_comp_rate_domain_agrees_with_the_services_one() -> None:
    """The duplication `rules/parameters.py` documents, pinned.

    The rules tier must not import from `services/`, so it restates the comp
    rate's domain to validate a document against it — exactly as it restates
    `severity_score`'s 0-100 for the risk bands. Restating is fine; drifting
    is not, and nothing else would notice: a services-side widening to 200%
    would leave documents refused at 150% with a message naming a bound
    nobody could find.

    Asserted from the outside, by probing the *boundary* rather than by
    importing the private constants — one basis point past each end must be
    refused and each end itself accepted, which is what makes this a statement
    about behaviour rather than about two names being equal.
    """
    assert benefit(defaultCompRateBp=COMP_RATE_MIN_BP).default_comp_rate_bp == COMP_RATE_MIN_BP
    assert benefit(defaultCompRateBp=COMP_RATE_MAX_BP).default_comp_rate_bp == COMP_RATE_MAX_BP
    with pytest.raises(RuleParameterError):
        benefit(defaultCompRateBp=COMP_RATE_MIN_BP - 1)
    with pytest.raises(RuleParameterError):
        benefit(defaultCompRateBp=COMP_RATE_MAX_BP + 1)


def test_a_negative_waiting_period_is_refused_but_zero_is_not() -> None:
    """Zero is a real jurisdiction's rule — "no waiting period" — and a
    parameter that could not express it would be describing the world
    incorrectly. A negative one is a first payment due before the injury."""
    assert benefit(waitingPeriodDays=0).waiting_period_days == 0
    with pytest.raises(RuleParameterError, match="waitingPeriodDays"):
        benefit(waitingPeriodDays=-1)
