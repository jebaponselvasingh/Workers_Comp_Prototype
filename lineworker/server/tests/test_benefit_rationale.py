"""Story 3.1 AC 3 — the reserve-rationale paragraph, clause by clause.

The paragraph is a *deterministic service output* (AD-2): the same claim always
produces the same sentence, from its own columns, with no model involved. That
is the property that lets it sit in a case file, and it is what the whole-string
comparison below is really asserting — an approximate match would pass against a
paragraph that had quietly started saying something else.

Every expected string here is written out rather than assembled from the code
under test. It reads as duplication and is the point: a test that built the
sentence the same way the module does would agree with any wording at all.

The prototype's own template is the contract
(`docs/Workers_Comp_Prototype.html`, `benefitCardHTML`), so the clause wordings
below are its, and the ordering assertion is about *its* order.
"""

import pytest

from data.models.enums import Disability, RecoveryWindow, ReturnStatus
from services.derivations import IndemnityType, RiskBand
from services.financials import compute_benefit, format_comp_rate
from tests.test_benefit_calculation import (
    DEFAULT_COMP_RATE_BP,
    PARAMS,
    THRESHOLDS,
    FakeClaim,
    rate_for,
)

#: The baseline claim's paragraph, written out. Prototype's `WC-20017`: a
#: $22,349 reserve, a medium-band fall from height scoring 52, a 6-8 week
#: window, no surgery, no attorney, temporary disability, Washington.
BASELINE = (
    "Reserve set at $22,349 reflects a medium severity fall from height "
    "(score 52/100) with a 6-8 weeks expected recovery window. Indemnity "
    "exposure estimated at TTD weekly benefit through projected MMI. "
    "Reviewed against WA statutory min/max ($257–$1,711/wk)."
)

SURGICAL = "Includes surgical exposure"
LITIGATION = "Attorney representation on file"
PERMANENT = "Permanent disability rating expected"
OVERRIDE = "Comp rate manually adjusted"


def rationale_for(claim: FakeClaim, state_code: str = "WA") -> str:
    """The paragraph as the card receives it — through `compute_benefit`.

    Deliberately not by calling `reserve_rationale` directly: the arguments it
    takes (the band, the indemnity type, the two rates, the override flag) are
    exactly the things `compute_benefit` decides, and a test that supplied them
    itself would be asserting the template against its own opinion of the
    claim rather than against the console's.
    """
    return compute_benefit(claim, rate_for(state_code), PARAMS, THRESHOLDS).reserve_rationale


# --- the whole sentence -------------------------------------------------


def test_the_baseline_paragraph_is_exactly_the_prototypes() -> None:
    assert rationale_for(FakeClaim()) == BASELINE


def test_the_paragraph_is_deterministic() -> None:
    """AD-2, asserted rather than assumed: this is a service output, not a
    narrative. Two calls on one claim are one sentence."""
    claim = FakeClaim(surgery_required=True, litigation_flag=True)

    assert rationale_for(claim) == rationale_for(claim)


# --- the conditional clauses -------------------------------------------


@pytest.mark.parametrize(
    ("claim", "present", "absent"),
    [
        (FakeClaim(surgery_required=True), SURGICAL, LITIGATION),
        (FakeClaim(litigation_flag=True), LITIGATION, SURGICAL),
        (
            FakeClaim(disability=Disability.permanent, severity_score=95),
            PERMANENT,
            "weekly benefit through projected MMI",
        ),
    ],
)
def test_each_clause_appears_only_for_the_claim_it_describes(
    claim: FakeClaim, present: str, absent: str
) -> None:
    """Each conditional clause, with the one it must *not* drag in beside it.

    The negative half is what makes these assertions worth writing: a template
    that concatenated every clause unconditionally would pass a test that only
    looked for the one it expected.
    """
    paragraph = rationale_for(claim)

    assert present in paragraph
    assert absent not in paragraph


def test_a_claim_with_every_driver_carries_every_clause_in_the_prototypes_order() -> None:
    """Order matters: the paragraph reads as a narrowing argument, from what
    the injury is to what was done to the number."""
    paragraph = rationale_for(
        FakeClaim(
            disability=Disability.permanent,
            severity_score=95,
            surgery_required=True,
            litigation_flag=True,
            comp_rate_override_bp=7_000,
        )
    )
    positions = [paragraph.index(clause) for clause in (SURGICAL, LITIGATION, PERMANENT, OVERRIDE)]

    assert positions == sorted(positions)
    assert paragraph.startswith("Reserve set at")
    # The statutory review sits between the disability clause and the override,
    # which is the one ordering the prototype's template makes and a reader
    # would not guess.
    assert (
        paragraph.index(PERMANENT) < paragraph.index("Reviewed against") < paragraph.index(OVERRIDE)
    )


@pytest.mark.parametrize(
    ("return_status", "expected_abbreviation"),
    [
        (ReturnStatus.under_treatment, "TTD"),
        (ReturnStatus.returned_and_under_therapy, "TPD"),
    ],
)
def test_the_temporary_clause_names_the_indemnity_type_the_claim_is_on(
    return_status: ReturnStatus, expected_abbreviation: str
) -> None:
    """The prototype recovers the abbreviation from its own display label with
    `.split(" — ")[0]`; here the token *is* the abbreviation, so the label
    stays in the browser and this sentence spells the short form itself."""
    paragraph = rationale_for(FakeClaim(return_status=return_status))

    assert f"estimated at {expected_abbreviation} weekly benefit" in paragraph


def test_a_permanent_partial_claim_gets_the_permanent_clause_not_a_ppd_sentence() -> None:
    """The branch is on `disability`, not on the indemnity type — the
    prototype's, and the reason PPD never appears in this paragraph."""
    paragraph = rationale_for(FakeClaim(disability=Disability.permanent, severity_score=50))

    assert PERMANENT in paragraph
    assert IndemnityType.ppd.value.upper() not in paragraph


# --- the severity word and the recovery phrase --------------------------


@pytest.mark.parametrize(
    ("severity_score", "word"),
    # The seeded bands: low below 35, medium below 65, high at or above it.
    # Restated as scores rather than as thresholds, so this reads as three
    # claims rather than as a second copy of the banding rule.
    [(10, "low"), (34, "low"), (35, "medium"), (64, "medium"), (65, "high"), (90, "high")],
)
def test_the_severity_word_is_the_registered_bands(severity_score: int, word: str) -> None:
    """`"med"` would read "a med severity fracture", so the server's prose map
    spells the band out.

    The *band* is still the one registered `risk` derivation's — this map turns
    a value into a word and decides nothing — which is why the boundaries here
    are the same ones the gauge and the queue dot use. A second opinion about
    where medium starts would put two severities on one claim.
    """
    paragraph = rationale_for(FakeClaim(severity_score=severity_score))

    assert f"a {word} severity" in paragraph


@pytest.mark.parametrize(
    ("window", "phrase"),
    [
        (RecoveryWindow.weeks_0_2, "0-2 weeks"),
        (RecoveryWindow.weeks_4_6, "4-6 weeks"),
        (RecoveryWindow.over_1_year, "greater than 1 year"),
    ],
)
def test_the_recovery_phrase_is_the_prototypes_lower_cased_label(
    window: RecoveryWindow, phrase: str
) -> None:
    """The column holds a token since Story 2.3's code review, so the sentence
    needs its own wording — and it is not the select's ("6-8 Weeks"), which is
    why the two maps are separate rather than one lower-cased at a call site."""
    paragraph = rationale_for(FakeClaim(recovery=window))

    assert f"a {phrase} expected recovery window" in paragraph


def test_every_band_and_every_window_has_a_word() -> None:
    """The maps are total, which `Record<Enum, string>` gives the browser for
    free and Python does not: a member with no entry is a `KeyError` inside a
    case-file read, which blanks the pane rather than the sentence."""
    from services.financials.rationale import RECOVERY_PHRASES, SEVERITY_WORDS

    assert set(SEVERITY_WORDS) == set(RiskBand)
    assert set(RECOVERY_PHRASES) == set(RecoveryWindow)


# --- money and rates in prose -------------------------------------------


def test_the_statutory_clause_names_the_state_and_its_bounds_in_dollars() -> None:
    paragraph = rationale_for(FakeClaim(state="IA"), state_code="IA")

    assert "Reviewed against IA statutory min/max ($420–$2,101/wk)." in paragraph


def test_the_reserve_is_formatted_as_the_spa_formats_cents() -> None:
    """`lib/money.ts`'s rule, restated: whole dollars, thousands separators.

    The obligation is real rather than stylistic — the reserve appears as prose
    here and as a formatted figure two rows above on the same card, and a
    reader seeing $22,349 in one and $22,348 in the other has found a bug in
    whichever is wrong.
    """
    assert "Reserve set at $1,000,000 " in rationale_for(FakeClaim(reserve=100_000_000))
    assert "Reserve set at $0 " in rationale_for(FakeClaim(reserve=0))
    # Half up on the cents, matching `Intl.NumberFormat`'s `halfExpand` — the
    # case the seeded book never produces and Epic 3's bill lines will.
    assert "Reserve set at $101 " in rationale_for(FakeClaim(reserve=10_050))
    assert "Reserve set at $100 " in rationale_for(FakeClaim(reserve=10_049))


def test_the_override_clause_states_both_rates_to_two_decimals() -> None:
    paragraph = rationale_for(FakeClaim(comp_rate_override_bp=7_025))

    assert "Comp rate manually adjusted to 70.25% (default 66.67%) by handler." in paragraph


def test_a_claim_on_the_default_carries_no_override_clause() -> None:
    assert OVERRIDE not in rationale_for(FakeClaim())


def test_typing_the_default_back_in_still_says_the_handler_adjusted_it() -> None:
    """`is_overridden`'s argument, in prose: the column is non-null, so the
    handler made a decision the case file records — even though the two rates
    the sentence quotes are the same number."""
    paragraph = rationale_for(FakeClaim(comp_rate_override_bp=DEFAULT_COMP_RATE_BP))

    assert "adjusted to 66.67% (default 66.67%) by handler." in paragraph


@pytest.mark.parametrize(
    ("basis_points", "text"),
    [(0, "0.00"), (6_667, "66.67"), (6_670, "66.70"), (10_000, "100.00"), (15_000, "150.00")],
)
def test_basis_points_format_exactly(basis_points: int, text: str) -> None:
    """The whole reason the rate is an integer: 66.67 does not round-trip
    through a float, and `6670` must read "66.70" rather than "66.7"."""
    assert format_comp_rate(basis_points) == text
