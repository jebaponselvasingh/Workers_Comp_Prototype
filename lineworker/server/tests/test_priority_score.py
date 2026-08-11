"""Story 2.1 AC 4 — the priority score, term by term.

The scorer is pure, so none of this needs a database: a `QueueClaim`, a
`QueueFlags` and a `PriorityWeights` in, a number out. That is the shape
AD-2 asks for — Epic 5's top-30 worklist imports the same function — and it
is what lets Hypothesis hammer the properties the ordering depends on.

The weights below are **restated**, not imported from the seeded document.
Same discipline as `seed_fixture.HIGH_RISK_MIN`: an expectation computed
with the code under test agrees with it however wrong both are.
`tests/test_rules_engine.py` is what ties these numbers back to the
committed `priority_weights.jdm.json`, and
`test_claims_queue.test_a_second_priority_weights_version_reranks_the_queue`
is what proves a real document change moves a real queue.

The pending-approval *status set* is restated here for the same reason as
the numbers, and it is restated in the same place — inside `SEEDED_WEIGHTS`
— because it is a parameter of the document like any other now, not a
constant importable from the scorer.
"""

from dataclasses import replace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from data.models.enums import ClaimStatus, Stage
from rules.parameters import PriorityWeights
from services.derivations import RiskBand
from services.worklist.priority import (
    QueueClaim,
    QueueFilter,
    QueueFlags,
    matches,
    priority_markers,
    priority_score,
)

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

# A claim that scores exactly zero: no flag set, no severity, no age, and a
# status that is not pending. Every test below adds one thing to it, so the
# delta *is* the weight under test.
BASELINE_CLAIM = QueueClaim(
    claim_id="WC-0001",
    stage=Stage.treatment,
    status=ClaimStatus.ch_approved,
    severity_score=0,
    days_open=0,
    injury_type="Laceration",
    worker_name="Dana Reyes",
    employer_short_name="3M",
    surgery_required=False,
    litigation_flag=False,
    fraud_flag=False,
)

BASELINE_FLAGS = QueueFlags(
    risk=RiskBand.low,
    siu_review=False,
    rtw_blocked=False,
    payment_due=False,
)


def claim_with(claim: QueueClaim = BASELINE_CLAIM, /, **changes: Any) -> QueueClaim:
    """A claim (the baseline by default) with named fields replaced.

    A helper rather than `replace(…, **mapping)` at each call site: several
    tests build their changes as a dict — a parametrized field name, a
    Hypothesis-drawn set of conditions — and `**dict[str, bool]` into a
    dataclass with mixed field types is not something a type checker can
    accept.
    """
    return replace(claim, **changes)


def flags_with(flags: QueueFlags = BASELINE_FLAGS, /, **changes: Any) -> QueueFlags:
    return replace(flags, **changes)


def score(claim: QueueClaim, flags: QueueFlags, weights: PriorityWeights = SEEDED_WEIGHTS) -> float:
    return priority_score(claim, flags, weights)


def test_the_baseline_claim_scores_nothing() -> None:
    """Every other test measures a delta from here, so this has to hold
    first — otherwise each of them would be asserting a weight plus an
    unexamined constant."""
    assert score(BASELINE_CLAIM, BASELINE_FLAGS) == 0


# --- one term at a time -------------------------------------------------


@pytest.mark.parametrize(
    ("field", "expected"),
    [("litigation_flag", 40.0), ("surgery_required", 15.0)],
)
def test_each_claim_condition_adds_exactly_its_weight(field: str, expected: float) -> None:
    claim = claim_with(**{field: True})
    assert score(claim, BASELINE_FLAGS) == expected


@pytest.mark.parametrize(
    ("flag", "expected"),
    [("siu_review", 35.0), ("rtw_blocked", 30.0), ("payment_due", 20.0)],
)
def test_each_derived_flag_adds_exactly_its_weight(flag: str, expected: float) -> None:
    flags = flags_with(**{flag: True})
    assert score(BASELINE_CLAIM, flags) == expected


@pytest.mark.parametrize("status", sorted(SEEDED_WEIGHTS.pending_approval_statuses))
def test_a_claim_awaiting_a_decision_adds_the_pending_approval_weight(
    status: ClaimStatus,
) -> None:
    assert score(replace(BASELINE_CLAIM, status=status), BASELINE_FLAGS) == 25.0


@pytest.mark.parametrize(
    "status", sorted(set(ClaimStatus) - SEEDED_WEIGHTS.pending_approval_statuses, key=str)
)
def test_every_other_status_adds_nothing(status: ClaimStatus) -> None:
    claim = replace(BASELINE_CLAIM, status=status)
    # `settled`/`settled_closed` are statuses, not stages: the −100 penalty
    # rides `stage`, so a settled *status* on a treatment claim must still
    # score zero. This is the pair of fields it would be easy to conflate.
    assert score(claim, BASELINE_FLAGS) == 0


def test_severity_contributes_a_fixed_fraction_of_its_score() -> None:
    assert score(replace(BASELINE_CLAIM, severity_score=80), BASELINE_FLAGS) == pytest.approx(24.0)


def test_the_days_open_term_saturates_at_the_cap() -> None:
    at_cap = score(replace(BASELINE_CLAIM, days_open=60), BASELINE_FLAGS)
    far_past = score(replace(BASELINE_CLAIM, days_open=6_000), BASELINE_FLAGS)

    assert at_cap == pytest.approx(12.0)
    assert far_past == at_cap


def test_settling_a_claim_costs_it_exactly_the_penalty() -> None:
    """Settling subtracts a constant — nothing more, nothing less.

    A constant is what makes the penalty behave sensibly in both places it
    matters: inside the settled group every member pays it, so it cancels
    and the group still orders by how much attention each claim needed;
    across groups it biases settled work downwards.
    """
    busy = replace(
        BASELINE_CLAIM,
        status=ClaimStatus.initial,
        severity_score=100,
        days_open=9_999,
        surgery_required=True,
        litigation_flag=True,
    )
    all_flags = QueueFlags(risk=RiskBand.high, siu_review=True, rtw_blocked=True, payment_due=True)

    live = score(busy, all_flags)
    settled = score(replace(busy, stage=Stage.settled), all_flags)

    assert settled == pytest.approx(live - 100)
    assert settled < live


def test_the_settled_penalty_is_a_bias_and_not_an_absolute_floor() -> None:
    """Stated as a test because the docstring's honesty depends on it.

    The seeded weights total 165 in categorical terms alone, so a settled
    claim carrying every one of them still scores above a quiet live claim.
    That is tolerable *because the queue is grouped by stage* and the two
    never compete in one list. Epic 5's ungrouped top-30 inherits this
    scorer and will have to decide whether it is tolerable there too — this
    assertion is the note it should find when it does.
    """
    worst_settled = replace(
        BASELINE_CLAIM,
        stage=Stage.settled,
        status=ClaimStatus.initial,
        severity_score=100,
        days_open=9_999,
        surgery_required=True,
        litigation_flag=True,
    )
    all_flags = QueueFlags(risk=RiskBand.high, siu_review=True, rtw_blocked=True, payment_due=True)

    assert score(worst_settled, all_flags) > score(BASELINE_CLAIM, BASELINE_FLAGS)


# --- the weights really are data (AD-8) ---------------------------------


def test_a_different_weight_block_reorders_the_same_claims() -> None:
    """The scorer half of the acceptance criterion: change the parameters,
    not the code, and the ranking changes.

    Two claims, one litigated and one with surgery. Under the seeded
    weights litigation wins; under a block that values surgery more, surgery
    wins — with no code path different between the two runs.

    Deliberately **not** billed as "the acceptance criterion, as a test":
    this constructs a `PriorityWeights` directly, so it proves the
    arithmetic reads its parameters and nothing about the document →
    parameters → score chain that produces them. The criterion is met by
    `test_claims_queue.test_a_second_priority_weights_version_reranks_the_queue`,
    which inserts a genuine version 2 into `rule_document` and re-requests
    the endpoint. Both are worth having; only one of them is the criterion.
    """
    litigated = replace(BASELINE_CLAIM, claim_id="WC-0002", litigation_flag=True)
    surgical = replace(BASELINE_CLAIM, claim_id="WC-0003", surgery_required=True)

    assert score(litigated, BASELINE_FLAGS) > score(surgical, BASELINE_FLAGS)

    surgery_first = replace(SEEDED_WEIGHTS, version=2, litigation=5, surgery=90)
    assert score(surgical, BASELINE_FLAGS, surgery_first) > score(
        litigated, BASELINE_FLAGS, surgery_first
    )


def test_no_weight_is_hardcoded_in_the_scorer() -> None:
    """A blunt structural check to go with the behavioural ones above.

    Every parameter zeroed means every claim scores zero. If any term had a
    literal beside it — a stray `+ 40`, a `* 0.3` — this claim would come
    back non-zero, whatever the document said.
    """
    zeroed = PriorityWeights(
        version=99,
        litigation=0,
        siu_review=0,
        rtw_blocked=0,
        pending_approval=0,
        # Left populated on purpose: the claim below *is* pending approval,
        # so the term fires and contributes its (zero) weight. Emptying the
        # set would have switched the branch off instead of the weight, and
        # a hardcoded `+ 25` beside it would have survived.
        pending_approval_statuses=frozenset({ClaimStatus.initial}),
        payment_due=0,
        surgery=0,
        severity_factor=0,
        days_open_factor=0,
        days_open_cap=0,
        settled_penalty=0,
        marker_threshold=0,
        marker_count=0,
        page_limit=1,
    )
    everything = replace(
        BASELINE_CLAIM,
        stage=Stage.settled,
        status=ClaimStatus.initial,
        severity_score=100,
        days_open=500,
        surgery_required=True,
        litigation_flag=True,
    )
    all_flags = QueueFlags(risk=RiskBand.high, siu_review=True, rtw_blocked=True, payment_due=True)

    assert score(everything, all_flags, zeroed) == 0


# --- the marker rule ----------------------------------------------------
#
# Tested here rather than only through the queue because it lives here: it
# is the second half of the same worklist rule, and Epic 5's top-30 will
# import it beside `priority_score`. A rule only exercised through an
# endpoint is a rule the next consumer re-derives from prose.


def test_the_marker_goes_to_the_top_few_above_the_threshold() -> None:
    scores = [90.0, 80.0, 70.0, 60.0, 50.0]

    assert priority_markers(scores, SEEDED_WEIGHTS) == [True, True, True, False, False]


def test_a_fourth_equally_high_claim_gets_no_marker() -> None:
    """The half a naive `score > threshold` gets wrong: the count is a cap
    on how many, not a coincidence of how high."""
    assert priority_markers([100.0] * 5, SEEDED_WEIGHTS) == [True, True, True, False, False]


def test_the_threshold_is_strictly_greater() -> None:
    """Matching the prototype's `priorityScore(c)>30`. A claim sitting
    exactly on the threshold is not above it."""
    assert priority_markers([30.1, 30.0], SEEDED_WEIGHTS) == [True, False]


def test_a_group_with_nothing_above_the_threshold_carries_no_marker() -> None:
    assert priority_markers([29.0, 10.0, -100.0], SEEDED_WEIGHTS) == [False, False, False]


def test_an_unsorted_sequence_is_refused_rather_than_marked() -> None:
    """The precondition, asserted instead of assumed.

    `priority_markers` used to answer for any sequence, spending a budget on
    marked claims rather than checking positions. Over a descending list
    that is the prototype's `i<3 && score>30` exactly; over an unsorted one
    the two rules diverge, and the old behaviour quietly picked one of them.
    A caller handing this an unsorted list has a bug — the marker is a
    statement about the top of a *ranked* list — so it is a `ValueError`
    naming the two positions that disagree, not a plausible-looking answer.
    """
    with pytest.raises(ValueError, match="descending"):
        priority_markers([90.0, 5.0, 80.0, 70.0], SEEDED_WEIGHTS)


def test_equal_scores_are_a_descending_sequence() -> None:
    """The check is `>`, not `>=`: a settled group where every claim pays
    the same penalty is flat, sorted, and perfectly legitimate."""
    assert priority_markers([50.0, 50.0, 50.0, 50.0], SEEDED_WEIGHTS) == [
        True,
        True,
        True,
        False,
    ]


def test_the_marker_parameters_are_data_like_every_other_weight() -> None:
    generous = replace(SEEDED_WEIGHTS, version=2, marker_count=1, marker_threshold=0)

    assert priority_markers([90.0, 80.0, 70.0], generous) == [True, False, False]


def test_marking_returns_one_answer_per_score() -> None:
    """The queue zips this against its cards with `strict=True`; a length
    mismatch would be a silent misalignment of markers to claims."""
    assert len(priority_markers([], SEEDED_WEIGHTS)) == 0
    assert len(priority_markers([4.0, 3.0, 2.0, 1.0], SEEDED_WEIGHTS)) == 4


# --- properties (NFR-7) -------------------------------------------------

CONDITIONS = ("litigation_flag", "surgery_required")
FLAG_NAMES = ("siu_review", "rtw_blocked", "payment_due")


@given(
    severity=st.integers(min_value=0, max_value=100),
    days=st.integers(min_value=0, max_value=2_000),
    conditions=st.lists(st.sampled_from(CONDITIONS), unique=True),
    flag_names=st.lists(st.sampled_from(FLAG_NAMES), unique=True),
    added=st.sampled_from(CONDITIONS + FLAG_NAMES),
    stage=st.sampled_from([Stage.intake, Stage.investigation, Stage.treatment]),
)
def test_adding_a_positive_condition_never_lowers_a_live_claims_score(
    severity: int,
    days: int,
    conditions: list[str],
    flag_names: list[str],
    added: str,
    stage: Stage,
) -> None:
    """Monotonicity — the property the whole ordering rests on.

    "This claim is litigated *as well*" must never move it down the queue.
    A single sign error in one term would satisfy every example-based test
    above (each measures its own term in isolation) and break exactly this.
    """
    claim = claim_with(
        stage=stage,
        severity_score=severity,
        days_open=days,
        **{name: True for name in conditions},
    )
    flags = flags_with(**{name: True for name in flag_names})

    before = score(claim, flags)
    if added in CONDITIONS:
        after = score(claim_with(claim, **{added: True}), flags)
    else:
        after = score(claim, flags_with(flags, **{added: True}))

    assert after >= before


@given(
    days=st.integers(min_value=0, max_value=100_000),
    extra=st.integers(min_value=0, max_value=100_000),
)
def test_the_days_open_term_is_bounded_by_the_cap(days: int, extra: int) -> None:
    aged = score(replace(BASELINE_CLAIM, days_open=days), BASELINE_FLAGS)
    older = score(replace(BASELINE_CLAIM, days_open=days + extra), BASELINE_FLAGS)

    assert aged <= older
    assert older <= SEEDED_WEIGHTS.days_open_cap * SEEDED_WEIGHTS.days_open_factor


# --- the filter table ---------------------------------------------------


def test_every_filter_has_a_predicate() -> None:
    """Totality, asserted rather than assumed.

    `matches` looks its predicate up in a mapping, so a ninth filter added
    to the enum without an entry is a `KeyError` in front of a user. This is
    the test that turns that into a failure at build time.
    """
    for queue_filter in QueueFilter:
        assert matches(queue_filter, BASELINE_CLAIM, BASELINE_FLAGS) in (True, False)


@pytest.mark.parametrize(
    ("queue_filter", "claim_changes", "flag_changes"),
    [
        (QueueFilter.active, {"stage": Stage.treatment}, {}),
        (QueueFilter.high_risk, {}, {"risk": RiskBand.high}),
        (QueueFilter.fraud, {"fraud_flag": True}, {}),
        (QueueFilter.litigation, {"litigation_flag": True}, {}),
        (QueueFilter.payment_due, {}, {"payment_due": True}),
        (QueueFilter.surgery, {"surgery_required": True}, {}),
        (QueueFilter.siu, {}, {"siu_review": True}),
    ],
)
def test_each_filter_selects_exactly_its_own_condition(
    queue_filter: QueueFilter,
    claim_changes: dict[str, object],
    flag_changes: dict[str, object],
) -> None:
    """The prototype's `renderQ` mapping (lines 1155-1165), restated.

    The negative half is the interesting one: a filter that returned `True`
    for everything would pass any "the matching claim is included" check.
    """
    matching_claim = claim_with(**claim_changes)
    matching_flags = flags_with(**flag_changes)
    # `intake` so `active` has something to exclude; every other filter's
    # condition is off in the baseline anyway.
    non_matching = replace(BASELINE_CLAIM, stage=Stage.intake)

    assert matches(queue_filter, matching_claim, matching_flags) is True
    assert matches(queue_filter, non_matching, BASELINE_FLAGS) is False


def test_the_all_filter_excludes_nothing() -> None:
    assert matches(QueueFilter.all, BASELINE_CLAIM, BASELINE_FLAGS) is True
    assert (
        matches(
            QueueFilter.all,
            replace(BASELINE_CLAIM, stage=Stage.settled),
            BASELINE_FLAGS,
        )
        is True
    )
