"""Story 2.5 AC 2 — the claim-path derivation, across all three paths.

The acceptance criterion names this test: "path classification is a registered
derivation with its rule parameters in JDM … with tests covering all three
paths". So the file below is organised around the three answers rather than
around the code's branches, and every boundary of every parameter is exercised
from both sides.

**No database.** `ClaimPathDerivation` is a frozen dataclass built from a
parameter block, so a case is five scalars — which is the whole point of AD-8's
split, and what lets Hypothesis range over the domain at the bottom.

**The thresholds are restated here, not imported.** `seed_fixture.HIGH_RISK_MIN`'s
discipline: a test that read `pathMinorSeverityMax` from the block under test
would agree with it whatever either of them said. `test_rules_engine.py` is
what ties these numbers back to the committed document.
"""

from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from data.models.enums import ClaimPath, Disability, RecoveryWindow, ReturnStatus
from services import derivations
from services.derivations.path_classification import ClaimPathDerivation
from tests.test_derivations import SEEDED_THRESHOLDS

# The deployed `derivation_thresholds` v3 values, restated. See the docstring.
MINOR_SEVERITY_MAX = 35
MINOR_WINDOWS = frozenset({RecoveryWindow.weeks_0_2})
FATALITY_SEVERITY_MIN = 100

DEPLOYED = ClaimPathDerivation(
    minor_severity_max=MINOR_SEVERITY_MAX,
    minor_recovery_windows=MINOR_WINDOWS,
    fatality_severity_min=FATALITY_SEVERITY_MIN,
)

#: A claim that classifies **A**: below the minor ceiling, no surgery,
#: temporary disability, shortest recovery window. Every negative case below
#: changes exactly one of these, so the delta *is* the thing under test.
MINOR = {
    "severity_score": MINOR_SEVERITY_MAX - 1,
    "disability": Disability.temporary,
    "recovery": RecoveryWindow.weeks_0_2,
    "surgery_required": False,
    "return_status": ReturnStatus.returned_and_fully_recovered,
}

#: A claim that classifies **C** — the strongest statement the seeded schema
#: can make about a fatal outcome, because it carries no fatality indicator.
#: This is a *fixture* claim and deliberately not a seeded one: see
#: `test_no_seeded_claim_is_classified_a_fatality` below.
FATAL = {
    "severity_score": FATALITY_SEVERITY_MIN,
    "disability": Disability.permanent,
    "recovery": RecoveryWindow.over_1_year,
    "surgery_required": True,
    "return_status": ReturnStatus.under_treatment,
}


def path(**overrides: object) -> ClaimPath:
    return DEPLOYED.of(**{**MINOR, **overrides})  # type: ignore[arg-type]


# --- the three answers ---------------------------------------------------


def test_a_minor_claim_is_path_a() -> None:
    assert path() is ClaimPath.a


def test_a_fatal_claim_is_path_c() -> None:
    assert DEPLOYED.of(**FATAL) is ClaimPath.c  # type: ignore[arg-type]


def test_everything_else_is_path_b() -> None:
    """B is the fallback, exactly as the prototype's own default is.

    A claim that is neither trivial nor fatal is the ordinary WC lifecycle,
    and writing a positive test for it would be a third rule to keep in step
    with the other two.
    """
    assert path(severity_score=MINOR_SEVERITY_MAX + 20, surgery_required=True) is ClaimPath.b


# --- what disqualifies Path A -------------------------------------------
#
# Four conditions, each removed on its own. A test that only checked the
# happy case would pass against a derivation that ignored three of them.


@pytest.mark.parametrize(
    ("field", "value"),
    [
        # A permanent disability is not a minor injury however low the score.
        ("disability", Disability.permanent),
        # First aid does not involve an operating theatre.
        ("surgery_required", True),
        # A recovery window the document does not count as "no lost time".
        ("recovery", RecoveryWindow.weeks_6_8),
    ],
)
def test_one_disqualifying_condition_is_enough_to_leave_path_a(field: str, value: object) -> None:
    assert path(**{field: value}) is ClaimPath.b


def test_the_minor_ceiling_is_exclusive() -> None:
    """`severity_score < max`, not `<=`.

    Asserted from both sides of the boundary in one test, because an
    off-by-one here silently reclassifies a band of real claims — and the two
    assertions are only meaningful together.
    """
    assert path(severity_score=MINOR_SEVERITY_MAX - 1) is ClaimPath.a
    assert path(severity_score=MINOR_SEVERITY_MAX) is ClaimPath.b


def test_the_fatality_floor_is_inclusive() -> None:
    """`severity_score >= min`, the mirror of the ceiling above."""
    assert DEPLOYED.of(**{**FATAL, "severity_score": FATALITY_SEVERITY_MIN}) is ClaimPath.c  # type: ignore[arg-type]
    assert (
        DEPLOYED.of(**{**FATAL, "severity_score": FATALITY_SEVERITY_MIN - 1})  # type: ignore[arg-type]
        is ClaimPath.b
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        # A survivor: permanently impaired, maximum severity, back at work.
        ("return_status", ReturnStatus.returned_and_fully_recovered),
        ("return_status", ReturnStatus.returned_and_under_therapy),
        # A temporary disability at maximum severity is a catastrophic injury,
        # not a death.
        ("disability", Disability.temporary),
    ],
)
def test_one_missing_condition_is_enough_to_leave_path_c(field: str, value: object) -> None:
    """The condition this file exists to defend.

    Classifying a living worker as a fatality is not a cosmetic error: it
    shows their handler the AFF-1 (dependants' death-benefit claim), the C-64
    (proof of death) and the C-65 (burial expenses). The seeded book's seven
    permanent claims are all amputations and several of them returned to work
    fully recovered, so these three cases are the ones a lowered cut-off would
    get wrong first.
    """
    assert DEPLOYED.of(**{**FATAL, field: value}) is not ClaimPath.c  # type: ignore[arg-type]


# --- the parameters really are parameters --------------------------------


def test_an_empty_minor_window_set_switches_path_a_off() -> None:
    """A legitimate thing to want from the document, and the reason the
    qualifying condition is data rather than an `if` (see `_recovery_window_set`)."""
    off = replace(DEPLOYED, minor_recovery_windows=frozenset())

    assert off.of(**MINOR) is ClaimPath.b  # type: ignore[arg-type]


def test_widening_the_window_set_moves_a_claim_onto_path_a() -> None:
    """The other direction, so the test above cannot pass by the branch being
    unreachable."""
    wider = replace(DEPLOYED, minor_recovery_windows=MINOR_WINDOWS | {RecoveryWindow.weeks_2_4})

    assert path(recovery=RecoveryWindow.weeks_2_4) is ClaimPath.b
    assert wider.of(**{**MINOR, "recovery": RecoveryWindow.weeks_2_4}) is ClaimPath.a  # type: ignore[arg-type]


def test_the_registered_derivation_is_the_one_under_test() -> None:
    """AD-10: the forms card and Epic 3's worklist call *this* function.

    Built from the seeded parameter block rather than constructed by hand, so
    a document whose values stopped reaching the derivation fails here.
    """
    built = derivations.claim_path.for_thresholds(SEEDED_THRESHOLDS)

    assert built == DEPLOYED
    assert derivations.get("claim_path") is derivations.claim_path


# --- properties over the whole domain ------------------------------------


@given(
    severity=st.integers(min_value=0, max_value=100),
    disability=st.sampled_from(list(Disability)),
    recovery=st.sampled_from(list(RecoveryWindow)),
    surgery=st.booleans(),
    return_status=st.sampled_from(list(ReturnStatus)),
)
def test_every_claim_gets_exactly_one_of_three_paths(
    severity: int,
    disability: Disability,
    recovery: RecoveryWindow,
    surgery: bool,
    return_status: ReturnStatus,
) -> None:
    """Total over the domain, with no fourth answer and no `None`.

    The prototype's `PATH_DOCS[path] || PATH_DOCS.B` fallback exists because
    its classification *can* answer something the form table has no entry for.
    This one cannot, and that is worth a property rather than an example.
    """
    answer = DEPLOYED.of(
        severity_score=severity,
        disability=disability,
        recovery=recovery,
        surgery_required=surgery,
        return_status=return_status,
    )

    assert answer in set(ClaimPath)


@given(severity=st.integers(min_value=0, max_value=100))
def test_no_claim_is_both_minor_and_fatal_under_a_coherent_document(severity: int) -> None:
    """What `pathMinorSeverityMax <= pathFatalitySeverityMin` buys.

    The block refuses an inverted pair, so the two conditions are mutually
    exclusive by construction — which is what makes "C is tested first" an
    ordering choice rather than a tie-break that decides real claims.
    """
    minor_qualified = severity < DEPLOYED.minor_severity_max
    fatal_qualified = severity >= DEPLOYED.fatality_severity_min

    assert not (minor_qualified and fatal_qualified)
