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

from data.models.enums import ActionKey, ActionUrgency, ClaimStatus, DocType
from rules.engine import LoadedDocument
from rules.parameters import (
    CURSOR_PAGE_CEILING,
    BenefitParams,
    DerivationThresholds,
    HandlerPerformance,
    IntakeRequirements,
    PriorityWeights,
    RuleParameterError,
    WorklistActions,
    urgency_parameter_name,
)
from services.financials import COMP_RATE_MAX_BP, COMP_RATE_MIN_BP

THRESHOLDS_DOC = LoadedDocument(key="derivation_thresholds", version=5, content={})
WEIGHTS_DOC = LoadedDocument(key="priority_weights", version=7, content={})
REQUIREMENTS_DOC = LoadedDocument(key="intake_required_documents", version=3, content={})
BENEFIT_DOC = LoadedDocument(key="benefit_params", version=2, content={})
ACTIONS_DOC = LoadedDocument(key="worklist_actions", version=5, content={})
BENCHMARKS_DOC = LoadedDocument(key="handler_performance", version=2, content={})

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
    # Story 5.1's one, which arrived with version 5 — the dashboard's fraud
    # REVIEW cut-off, deliberately not `siuFraudScoreMin`'s referral one.
    "fraudFlagScoreMin": 55,
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


VALID_WORKLIST_ACTIONS: dict[str, object] = {
    "cap": 6,
    "paddingFloor": 3,
    # Built rather than written out, so this fixture cannot be the place a
    # twelfth rule is silently left untuned: `ActionKey` grows and this dict
    # grows with it. The *values* are deliberately uniform — the cases below
    # are about refusals, and a rule's own urgency is asserted against the
    # committed document in `tests/test_rules_engine.py`.
    **{urgency_parameter_name(key): "medium" for key in ActionKey},
    # Story 5.4's two, added by version 2 of the document. Required members of
    # the block, so every case below has to carry them; the refusal tests for
    # the pair are further down.
    "supervisorWorklistCap": 30,
    "supervisorWorklistPageLimit": 10,
}


#: Story 5.2's block. The deviation bands are whole percentages, one of them
#: negative; the three weights are basis points over 0-100 inputs.
VALID_HANDLER_PERFORMANCE = {
    "onTrackDeviationPctMax": -8,
    "attentionDeviationPctMin": 8,
    "severityWeightBp": 10_000,
    "surgeryRateWeightBp": 1_000,
    "litigationRateWeightBp": 1_500,
    "complexityScoreMax": 100,
    "complexityHighMin": 65,
    "complexityMedMin": 40,
}


def benchmarks(**changes: object) -> HandlerPerformance:
    return HandlerPerformance.of(BENCHMARKS_DOC, {**VALID_HANDLER_PERFORMANCE, **changes})


def actions(**changes: object) -> WorklistActions:
    return WorklistActions.of(ACTIONS_DOC, {**VALID_WORKLIST_ACTIONS, **changes})


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
    assert thresholds().version == 5
    assert weights().version == 7
    assert requirements().version == 3
    assert benefit().version == 2
    assert actions().version == 5
    assert benchmarks().version == 2


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


@pytest.mark.parametrize("bound", [-1, 101, 60_000])
def test_the_fraud_review_cut_off_is_held_to_fraud_scores_range(bound: int) -> None:
    """The same column, the other rule (Story 5.1).

    A cut-off above the column's range empties the dashboard's Fraud Flags
    card and empties Story 5.4's worklist of its fraud population, neither of
    which raises anything: a portfolio with no suspected fraud and a portfolio
    nobody is checking render identically.
    """
    with pytest.raises(RuleParameterError, match="fraudFlagScoreMin"):
        thresholds(fraudFlagScoreMin=bound)


def test_the_two_fraud_cut_offs_are_independent_parameters() -> None:
    """Neither validates against the other, and that is deliberate.

    They are two rules over one column pair, not a band pair: review is wider
    than referral today, but an operator who decided to refer everything they
    review would be stating a policy rather than inverting an ordering.
    Refusing that here would be the rules tier legislating one — the check that
    exists is the column's range, and nothing else.
    """
    block = thresholds(siuFraudScoreMin=40, fraudFlagScoreMin=90)

    assert block.siu_fraud_score_min == 40
    assert block.fraud_flag_score_min == 90


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


@pytest.mark.parametrize("limit", [CURSOR_PAGE_CEILING + 1, 1_000])
def test_a_page_limit_above_the_cursor_ceiling_is_refused(limit: int) -> None:
    """The symmetric refusal to `pageLimit: 0`, and the one it does not imply.

    A page size past what `decode_cursor` will accept serves page one and then
    400s every "Show more" — on *both* surfaces that read this field, the
    queue since Story 2.1 and the drill-through since 5.5. `WorklistActions`
    already refuses exactly this for its own page size; this is the same guard
    on the field with two cursors behind it.
    """
    with pytest.raises(RuleParameterError, match="pageLimit"):
        weights(pageLimit=limit)


def test_the_published_page_limit_sits_under_the_ceiling() -> None:
    """The shipped document, checked against the rule that governs it.

    A refusal nothing exercises is a refusal that can be wrong in the direction
    that matters. `priority_weights` v1 publishes 50; if a later version raised
    it past the ceiling, this fails beside the parametrized guard above rather
    than at a supervisor's second click.
    """
    assert weights().page_limit <= CURSOR_PAGE_CEILING


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

    for key in VALID_HANDLER_PERFORMANCE:
        block = {k: v for k, v in VALID_HANDLER_PERFORMANCE.items() if k != key}
        with pytest.raises(RuleParameterError, match=key):
            HandlerPerformance.of(BENCHMARKS_DOC, block)


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


# --- the action checklist's parameters (Story 3.5) ----------------------


def test_every_rule_gets_its_urgency_from_the_document() -> None:
    """The mapping is total, and it is keyed by the enum rather than by a string.

    Total because a rule with no rank has no place in the order, and a
    generator that defaulted one would have put a rule element back in Python —
    the tier AD-8 takes it out of. Keyed by the enum because the generator ranks
    on members: a string comparison downstream is how a typo becomes a rule that
    silently stops firing.
    """
    block = actions()

    assert set(block.urgencies) == set(ActionKey)
    for key in ActionKey:
        assert block.urgency_of(key) is ActionUrgency.medium


def test_the_urgency_parameter_names_are_the_documents_camel_case() -> None:
    """The one place the two spellings of a rule are asserted against each other.

    Derived rather than mapped by hand (see `urgency_parameter_name`), so the
    check is that the derivation matches the convention the committed document
    actually uses — `tests/test_rules_engine.py` holds the other half, that
    every derived name is present in that document.
    """
    assert urgency_parameter_name(ActionKey.bill_review) == "urgencyBillReview"
    assert urgency_parameter_name(ActionKey.assessment_approval) == "urgencyAssessmentApproval"
    assert urgency_parameter_name(ActionKey.osha_log) == "urgencyOshaLog"


@pytest.mark.parametrize("value", [None, "urgent", 3, ["high"], True])
def test_an_urgency_the_enum_does_not_know_is_refused_naming_the_value(value: object) -> None:
    """A rule that quietly loses its rank is the failure this prevents.

    Without the resolution, `"urgent"` would travel to the browser as an
    urgency no chip has a colour for — on a card that still renders, in a list
    that is still ordered, with nothing anywhere saying the rule was retuned
    into nonsense. `None` is in the parameter list because a *missing* key must
    be the same refusal as a wrong one; it is the likelier of the two.
    """
    with pytest.raises(RuleParameterError, match="worklist_actions v5"):
        actions(urgencyBillReview=value)


def test_a_missing_urgency_names_the_rule_it_belongs_to() -> None:
    """The message has to say which of the eleven, or it is a hunt."""
    incomplete = {
        key: value
        for key, value in VALID_WORKLIST_ACTIONS.items()
        if key != urgency_parameter_name(ActionKey.osha_log)
    }
    with pytest.raises(RuleParameterError, match="urgencyOshaLog"):
        WorklistActions.of(ACTIONS_DOC, incomplete)


@pytest.mark.parametrize("cap", [0, -1])
def test_a_cap_below_one_is_refused(cap: int) -> None:
    """A cap of zero renders an empty card on every claim in the portfolio,
    silently, with the heading still there — `pageLimit: 0`'s failure, one
    surface over. One is strange and is a policy, so it is allowed."""
    assert actions(cap=1, paddingFloor=1).cap == 1
    with pytest.raises(RuleParameterError, match="cap"):
        actions(cap=cap)


def test_a_padding_floor_of_zero_switches_padding_off_rather_than_failing() -> None:
    """`_status_set`'s empty-list argument, over a number: "never pad" is a
    defensible reading of a worklist and is the way an operator turns the
    routine review rows off from the document."""
    assert actions(paddingFloor=0).padding_floor == 0
    with pytest.raises(RuleParameterError, match="paddingFloor"):
        actions(paddingFloor=-1)


def test_a_padding_floor_above_the_cap_is_refused() -> None:
    """Not a silently-unreachable band but a directly contradictory instruction:
    pad up to eight rows, then cut to six. Whichever the generator obeyed would
    make the other parameter a lie. Equal is fine — it means every list is
    exactly full."""
    assert actions(cap=3, paddingFloor=3).padding_floor == 3
    with pytest.raises(RuleParameterError, match="paddingFloor"):
        actions(cap=3, paddingFloor=4)


@pytest.mark.parametrize("value", [None, "6", 6.5, [6]])
def test_a_non_integer_cap_is_refused(value: object) -> None:
    with pytest.raises(RuleParameterError, match="worklist_actions v5"):
        actions(cap=value)


@pytest.mark.parametrize("cap", [0, -1])
def test_a_supervisor_worklist_cap_below_one_is_refused(cap: int) -> None:
    """`cap: 0`'s failure one surface over, and the quieter of the two.

    A worklist capped at zero renders a table with a heading, ten column labels
    and no rows — indistinguishable on screen from a portfolio in which nothing
    needs attention, which is the single most reassuring thing this console can
    say wrongly. One is strange and is a policy: "show me only the worst claim
    in the book" is exactly the tuning a parameter is for.
    """
    assert (
        actions(supervisorWorklistCap=1, supervisorWorklistPageLimit=1).supervisor_worklist_cap == 1
    )
    with pytest.raises(RuleParameterError, match="supervisorWorklistCap"):
        actions(supervisorWorklistCap=cap)


@pytest.mark.parametrize("limit", [0, -1])
def test_a_supervisor_worklist_page_limit_below_one_is_refused(limit: int) -> None:
    """`pageLimit: 0`'s refusal, on the other list that has a page size.

    Zero serves an empty first page beside a non-null cursor and pages for ever
    without advancing; negative slices backwards. Neither is a tuning choice.
    """
    assert actions(supervisorWorklistPageLimit=1).supervisor_worklist_page_limit == 1
    with pytest.raises(RuleParameterError, match="supervisorWorklistPageLimit"):
        actions(supervisorWorklistPageLimit=limit)


def test_a_supervisor_worklist_page_wider_than_the_cap_is_refused() -> None:
    """Not a contradiction like `paddingFloor > cap`, and subtler for it.

    A page wider than the cap is not nonsense — the first page is simply the
    whole list, and the table still renders correctly. What it does is switch
    the cursor off: `nextCursor` is never issued, and the pagination the
    endpoint publishes becomes a mechanism no request can reach, silently, from
    a number. Equal is allowed and is a real policy ("one page, the whole
    worklist"), so the refusal is strictly-greater.
    """
    assert (
        actions(
            supervisorWorklistCap=10, supervisorWorklistPageLimit=10
        ).supervisor_worklist_page_limit
        == 10
    )
    with pytest.raises(RuleParameterError, match="supervisorWorklistPageLimit"):
        actions(supervisorWorklistCap=10, supervisorWorklistPageLimit=11)


def test_the_urgency_mapping_cannot_be_edited_by_a_consumer() -> None:
    """The block is frozen, and a frozen dataclass holding a mutable dict is
    frozen about the wrong thing.

    These blocks are loaded once per request and handed to a pure generator; a
    caller able to write into the mapping could retune a rule for every claim
    scored after it, from anywhere, with the dataclass still reporting itself
    immutable.
    """
    block = actions()
    with pytest.raises(TypeError):
        block.urgencies[ActionKey.bill_review] = ActionUrgency.high  # type: ignore[index]


# --- the handler-benchmark parameters (Story 5.2) ------------------------


@pytest.mark.parametrize(
    "weight_key",
    ["severityWeightBp", "surgeryRateWeightBp", "litigationRateWeightBp"],
)
def test_a_negative_blend_weight_is_refused_because_it_inverts_the_term(weight_key: str) -> None:
    """A negative weight does not merely re-tune the blend, it reverses a term.

    `surgeryRateWeightBp: -1000` makes an all-surgical desk *less* complex than a
    desk of sprains, the chip still renders a confident band, and nothing
    anywhere says the sign is wrong — the same class of silent inversion
    `severityFactor` is refused for on the queue.
    """
    with pytest.raises(RuleParameterError, match="must not be negative"):
        benchmarks(**{weight_key: -1})


def test_a_zero_blend_weight_switches_a_term_off_rather_than_failing() -> None:
    """Zero is a policy, not a mistake — `_status_set`'s empty-list argument.

    "Litigation does not enter the blend" is a legitimate thing for an operator
    to say, and switching a term off from the document is what the parameter is
    for.
    """
    assert benchmarks(litigationRateWeightBp=0).litigation_rate_weight_bp == 0


def test_two_zero_blend_weights_still_leave_a_working_blend() -> None:
    """Severity alone is a coherent complexity rule, and must stay loadable.

    The refusal below is about the blend having *nothing* in it, not about it
    being simple: an operator who decides complexity is average severity and
    nothing else has expressed a policy, and the column still separates one desk
    from another.
    """
    block = benchmarks(surgeryRateWeightBp=0, litigationRateWeightBp=0)
    assert (block.surgery_rate_weight_bp, block.litigation_rate_weight_bp) == (0, 0)


def test_all_three_blend_weights_zero_is_refused_because_it_bands_the_whole_desk() -> None:
    """The silent failure the individual zeros are allowed *because* it is caught.

    Every term switched off scores every handler 0, puts every score below
    `complexityMedMin`, and renders a single Low chip on every row of a column
    whose only job is to tell desks apart — with no request failing and nothing
    logged. `cap: 0` is refused for the same shape of silence; this is that
    mistake spread across three parameters instead of one.
    """
    with pytest.raises(RuleParameterError, match="are all zero"):
        benchmarks(severityWeightBp=0, surgeryRateWeightBp=0, litigationRateWeightBp=0)


@pytest.mark.parametrize("cap", [0, -1])
def test_a_complexity_cap_below_one_is_refused(cap: int) -> None:
    """A cap of zero grades every desk in the console identically, in silence."""
    with pytest.raises(RuleParameterError, match="complexityScoreMax must be at least 1"):
        benchmarks(complexityScoreMax=cap)


@pytest.mark.parametrize(
    ("band_key", "value"),
    [
        ("complexityHighMin", 101),
        ("complexityHighMin", -1),
        ("complexityMedMin", 200),
        ("complexityMedMin", -5),
    ],
)
def test_a_complexity_band_outside_the_capped_scale_is_refused(band_key: str, value: int) -> None:
    """A cut-off above the cap is a band that can never be reached.

    `complexityHighMin: 101` against a cap of 100 means no handler is ever
    graded High, however severe, however surgical and however litigated their
    book — silently, with a Medium chip on every row.
    """
    with pytest.raises(RuleParameterError, match="complexityScoreMax"):
        benchmarks(**{band_key: value})


def test_a_complexity_band_may_sit_on_either_end_of_the_capped_scale() -> None:
    """The bounds are inclusive, and both ends are meaningful policies: a floor
    of zero grades the whole desk Medium or above, a cut-off at the cap reserves
    High for a book that maxes the blend."""
    assert benchmarks(complexityMedMin=0).complexity_med_min == 0
    assert benchmarks(complexityHighMin=100).complexity_high_min == 100


def test_inverted_complexity_bands_are_refused() -> None:
    """`riskMedMin > riskHighMin`'s refusal over the complexity pair.

    `handler_complexity` tests High first, so an inverted pair makes `med`
    unreachable and grades a middling book High.
    """
    with pytest.raises(RuleParameterError, match="complexityMedMin"):
        benchmarks(complexityHighMin=40, complexityMedMin=60)


def test_equal_complexity_bands_are_refused_because_they_delete_a_band() -> None:
    """Equality is not the boundary between two policies; it is the loss of one.

    `complexityMedMin == complexityHighMin` does not invert anything, which is
    exactly why it is easy to wave through — and it makes `med` unreachable, so
    every desk grades Low or High and the chip two thirds of a desk normally
    wears simply stops appearing. The table publishes a three-band scale (the
    footnote quotes both cut-points, the UI ships three tones); a document that
    wants two bands is asking for a different rule.
    """
    with pytest.raises(RuleParameterError, match="complexityMedMin"):
        benchmarks(complexityHighMin=50, complexityMedMin=50)


def test_complexity_bands_one_apart_are_the_tightest_allowed() -> None:
    """The refusal is `>=`, so the narrowest legal `med` band is a single score.

    Asserted beside the refusal because "strictly below" has to mean *one* apart
    is fine — a check that demanded a gap would be a tuning constraint nobody
    wrote down.
    """
    assert benchmarks(complexityHighMin=51, complexityMedMin=50).complexity_med_min == 50


def test_inverted_deviation_bands_are_refused() -> None:
    """The refusal that matters most on this block.

    `cycle_time_status` tests On Track first, so `onTrackDeviationPctMax` above
    `attentionDeviationPctMin` reports a handler running far slower than the desk
    as On Track — the opposite of what the table exists to surface.
    """
    with pytest.raises(RuleParameterError, match="onTrackDeviationPctMax"):
        benchmarks(onTrackDeviationPctMax=20, attentionDeviationPctMin=-20)


def test_deviation_bands_may_both_be_positive_or_both_negative() -> None:
    """Neither band is required to sit on its own side of zero.

    "On track means no slower than the desk" (`0`) and "attention starts at any
    slippage at all" are both real policies, and a check that assumed a sign
    would forbid them while protecting against nothing — the ordering is the
    property that matters, and it is checked above.
    """
    strict = benchmarks(onTrackDeviationPctMax=0, attentionDeviationPctMin=1)
    lenient = benchmarks(onTrackDeviationPctMax=-20, attentionDeviationPctMin=-10)

    assert strict.on_track_deviation_pct_max == 0
    assert lenient.attention_deviation_pct_min == -10


def test_equal_deviation_bands_are_refused_because_their_edges_overlap() -> None:
    """Both edges are inclusive, so equal bands do not merely squeeze Watch out.

    They make the two conditions *both true* on their shared value, and which
    one wins is then decided by the order `cycle_time_status` happens to test
    them in — a rule set by a line number rather than by the document. Refused
    at load, where a tuning mistake is still a tuning mistake, rather than
    resolved silently at every call.
    """
    with pytest.raises(RuleParameterError, match="onTrackDeviationPctMax"):
        benchmarks(onTrackDeviationPctMax=0, attentionDeviationPctMin=0)


def test_deviation_bands_one_apart_are_the_tightest_allowed() -> None:
    """`-1` / `0` is a legal, very strict policy: everything at or above the
    desk average asks for attention. The refusal is equality, not proximity."""
    assert benchmarks(onTrackDeviationPctMax=-1, attentionDeviationPctMin=0).version == 2


@pytest.mark.parametrize("value", [1.5, "8", None, True])
def test_a_non_integer_deviation_band_is_refused(value: object) -> None:
    """A percentage band is compared against a rounded whole percentage, so a
    fractional one could never be reached exactly and a string could not be
    compared at all."""
    with pytest.raises(RuleParameterError, match="attentionDeviationPctMin"):
        benchmarks(attentionDeviationPctMin=value)
