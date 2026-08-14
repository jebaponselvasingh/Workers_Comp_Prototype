"""Story 3.2 AC 1, 2 and 4 — the reserve verdict, without a database.

`classify_reserve` is pure by design (`reserve_check_for_claim` is the thin
async wrapper that assembles its two exposure terms), which is what lets the
property below run over the whole input space without a connection. That split
is the point of the arrangement, not a convenience — `test_benefit_calculation.py`
makes the same argument one module over.

**The bands here are restated, never imported from the rules tier.** Same
oracle discipline as `test_benefit_calculation.py`'s comp rates and
`seed_fixture.HIGH_RISK_MIN`: a test that read the thresholds out of the
document under test would agree with it however wrong both were.
`tests/test_rules_engine.py` is what ties these values back to the committed
document, and `test_the_bands_come_from_the_document_not_from_the_code` below
is what proves this module's *subject* reads them from there rather than
carrying its own copy.

**Why the boundaries get four tests each rather than one.** AC 4 asks for the
band edges specifically, and an edge is where a reimplementation silently flips
a `>` into a `>=`: both forms agree on every input except the exact boundary,
so a test that only sampled 1.20 and 1.10 would pass against either. The pairs
below straddle each threshold by a single cent.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from data.models.enums import Stage
from rules.engine import LoadedDocument
from rules.parameters import ReserveBands, RuleParameterError
from services.financials import (
    CLOSED_FINAL_RATIONALE,
    ZERO_RESERVE_CLEAR_RATIO_BP,
    ZERO_RESERVE_EXPOSED_RATIO_BP,
    ReserveCheck,
    ReserveVerdict,
    classify_reserve,
)

# The seeded document, restated. See the module docstring.
LIGHT_RATIO_BP = 11_500
HEAVY_RATIO_BP = 6_000

BANDS = ReserveBands(version=1, light_ratio_bp=LIGHT_RATIO_BP, heavy_ratio_bp=HEAVY_RATIO_BP)

#: A round reserve, so the boundary arithmetic below reads as the percentages
#: it is testing: 1% of this is exactly 1,000 cents.
RESERVE = 10_000_00

#: The exposure that sits exactly on each boundary for `RESERVE`.
AT_LIGHT = RESERVE * LIGHT_RATIO_BP // 10_000  # 115% — $11,500
AT_HEAVY = RESERVE * HEAVY_RATIO_BP // 10_000  # 60%  — $6,000

#: Every stage that is judged rather than short-circuited.
OPEN_STAGES = [Stage.intake, Stage.investigation, Stage.treatment]


def check(
    *,
    reserve: int = RESERVE,
    indemnity: int = 0,
    medical: int | None = 0,
    scheduled: int | None = None,
    disbursed: int = 0,
    stage: Stage = Stage.treatment,
    paid_medical: int = 0,
    bands: ReserveBands = BANDS,
) -> ReserveCheck:
    """`classify_reserve` with this module's defaults — one call, named args.

    `scheduled` defaults to `disbursed + indemnity`, which is the identity the
    projection guarantees — so a test that only cares about the banding does
    not have to restate two figures the banding never reads.
    """
    return classify_reserve(
        stage=stage,
        reserve_cents=reserve,
        remaining_indemnity_cents=indemnity,
        remaining_medical_cents=medical,
        scheduled_indemnity_cents=disbursed + indemnity if scheduled is None else scheduled,
        disbursed_indemnity_cents=disbursed,
        disbursed_medical_cents=paid_medical,
        bands=bands,
    )


# --- the three bands ------------------------------------------------------


def test_exposure_well_above_the_reserve_is_light() -> None:
    """Under-reserved: the exposure is half again the reserve."""
    result = check(indemnity=RESERVE + RESERVE // 2)

    assert result.verdict is ReserveVerdict.light
    assert result.ratio_bp == 15_000


def test_exposure_close_to_the_reserve_is_adequate() -> None:
    result = check(indemnity=RESERVE)

    assert result.verdict is ReserveVerdict.adequate
    assert result.ratio_bp == 10_000


def test_exposure_well_below_the_reserve_is_heavy() -> None:
    """Over-reserved: a quarter of the reserve is still needed."""
    result = check(indemnity=RESERVE // 4)

    assert result.verdict is ReserveVerdict.heavy
    assert result.ratio_bp == 2_500


@pytest.mark.parametrize("stage", OPEN_STAGES)
def test_every_open_stage_is_judged_by_the_same_bands(stage: Stage) -> None:
    """The verdict is about the money, not about the lifecycle position.

    Only `settled` short-circuits. An intake claim's schedule is entirely
    unapproved and its exposure is therefore its whole projection — which is a
    reason for the verdict to be *light*, not a reason to withhold one.
    """
    assert check(indemnity=RESERVE * 2, stage=stage).verdict is ReserveVerdict.light


# --- the boundaries, to the cent -----------------------------------------


def test_exactly_at_the_light_boundary_is_adequate() -> None:
    """Strictly greater than, so the boundary itself does not ask for action."""
    assert check(indemnity=AT_LIGHT).verdict is ReserveVerdict.adequate
    assert check(indemnity=AT_LIGHT).ratio_bp == LIGHT_RATIO_BP


def test_one_cent_above_the_light_boundary_is_light() -> None:
    assert check(indemnity=AT_LIGHT + 1).verdict is ReserveVerdict.light


def test_one_cent_below_the_light_boundary_is_adequate() -> None:
    assert check(indemnity=AT_LIGHT - 1).verdict is ReserveVerdict.adequate


def test_exactly_at_the_heavy_boundary_is_adequate() -> None:
    """Strictly less than, mirroring the light boundary above."""
    assert check(indemnity=AT_HEAVY).verdict is ReserveVerdict.adequate
    assert check(indemnity=AT_HEAVY).ratio_bp == HEAVY_RATIO_BP


def test_one_cent_below_the_heavy_boundary_is_heavy() -> None:
    assert check(indemnity=AT_HEAVY - 1).verdict is ReserveVerdict.heavy


def test_one_cent_above_the_heavy_boundary_is_adequate() -> None:
    assert check(indemnity=AT_HEAVY + 1).verdict is ReserveVerdict.adequate


def test_the_boundary_holds_on_a_reserve_that_is_not_a_round_number() -> None:
    """The edge that a float ratio would decide by rounding.

    115% of $7,777.77 is $8,944.43550 — not a whole cent, so no exposure sits
    exactly on this boundary and the two neighbouring cents must fall either
    side of it. Under a float comparison the answer here depends on which way
    the division rounded; under the integer cross-multiplication it does not.
    """
    reserve = 7_777_77
    below = (reserve * LIGHT_RATIO_BP) // 10_000

    assert check(reserve=reserve, indemnity=below).verdict is ReserveVerdict.adequate
    assert check(reserve=reserve, indemnity=below + 1).verdict is ReserveVerdict.light


# --- the exposure is the sum of two terms --------------------------------


def test_both_exposure_terms_count_toward_the_verdict() -> None:
    """Neither half alone is light; together they are.

    The test that would pass if the medical term were dropped on the floor —
    which is exactly what a wiring mistake in `reserve_check_for_claim` looks
    like while the bill table is still Story 3.3's.
    """
    # 70% of the reserve each: adequate on its own (between the 60% and 115%
    # bands), light at 140% once the two are added.
    part = 7_000_00

    assert check(indemnity=part).verdict is ReserveVerdict.adequate
    assert check(medical=part).verdict is ReserveVerdict.adequate
    assert check(indemnity=part, medical=part).verdict is ReserveVerdict.light


def test_the_three_money_figures_are_published_separately_and_agree() -> None:
    result = check(indemnity=300_00, medical=200_00)

    assert result.remaining_indemnity_cents == 300_00
    assert result.remaining_medical_cents == 200_00
    assert result.projected_remaining_cents == 500_00
    assert result.reserve_cents == RESERVE


def test_the_two_indemnity_halves_travel_with_the_verdict() -> None:
    """The figures the card states its indemnity-paid row from (code review).

    Carried, not consulted: the verdict is decided on the remaining figure, and
    these exist so the card shows the number the verdict was reached from
    instead of `claim.paid_indemnity`, which contradicted it.
    """
    result = check(indemnity=300_00, disbursed=700_00)

    assert result.disbursed_indemnity_cents == 700_00
    assert result.scheduled_indemnity_cents == 1_000_00
    assert result.remaining_indemnity_cents == 300_00


def test_the_indemnity_halves_do_not_change_the_verdict() -> None:
    """Two claims with the same exposure and different histories band alike.

    A claim halfway through its schedule and one that has not started are
    equally exposed if the same amount remains — which is what makes these two
    figures provenance rather than inputs. If either reached the comparison,
    these two verdicts would differ.
    """
    early = check(indemnity=AT_LIGHT + 1, disbursed=0)
    late = check(indemnity=AT_LIGHT + 1, disbursed=50_000_00)

    assert early.verdict is late.verdict is ReserveVerdict.light
    assert early.ratio_bp == late.ratio_bp


# --- an exposure term that is not on file --------------------------------
#
# `medical=None` is "the bills cannot be seen" — Story 3.3's seam — as against
# `medical=0`, "this claim has no unpaid bills". The unknown term is
# non-negative, so the exposure computed without it is a lower bound, and
# exactly one of the three bands survives that.


def test_an_unknown_medical_term_withholds_the_adequate_verdict() -> None:
    """The same exposure: judged when the bills are nil, withheld when unseen.

    The pair that shows `None` and `0` are different questions rather than two
    spellings of one. An exposure at 100% of the reserve is comfortably
    adequate *if* that is the whole exposure; if bills are outstanding it could
    be anything, and adequate is a claim about an upper bound.
    """
    assert check(indemnity=RESERVE, medical=0).verdict is ReserveVerdict.adequate
    assert check(indemnity=RESERVE, medical=None).verdict is ReserveVerdict.indeterminate


def test_an_unknown_medical_term_withholds_the_heavy_verdict() -> None:
    """The dangerous one, and the reason this rule exists.

    `heavy`'s rationale tells a handler to reallocate money away from a claim.
    Reaching that conclusion without the claim's bills is the console advising
    a financial decision on half the inputs — which is what it did on 26 of 38
    open seeded claims before this.
    """
    assert check(indemnity=RESERVE // 4, medical=0).verdict is ReserveVerdict.heavy
    assert check(indemnity=RESERVE // 4, medical=None).verdict is ReserveVerdict.indeterminate


def test_an_unknown_medical_term_does_not_withhold_the_light_verdict() -> None:
    """The one band that survives, and it survives by arithmetic.

    An unknown non-negative addition cannot bring an exposure back under a
    threshold it has already passed, so a claim that is under-reserved on
    indemnity alone is under-reserved whatever its bills say. Withholding it
    would lose exactly the signal the story exists for.
    """
    assert check(indemnity=AT_LIGHT + 1, medical=None).verdict is ReserveVerdict.light


def test_the_light_boundary_is_unchanged_by_a_missing_term() -> None:
    """Still strictly greater than — the floor is compared, not fudged."""
    assert check(indemnity=AT_LIGHT, medical=None).verdict is ReserveVerdict.indeterminate
    assert check(indemnity=AT_LIGHT + 1, medical=None).verdict is ReserveVerdict.light


def test_a_withheld_verdict_publishes_no_total_and_no_ratio() -> None:
    """A total with an unknown term is not a total, and neither is its ratio.

    The mislabel this refuses is publishing the lower bound under a name that
    reads as the whole figure — which is how a consumer downstream (Story 3.3's
    Bills summary, Epic 6's insight) would end up quoting it as the exposure.
    """
    result = check(indemnity=RESERVE, medical=None)

    assert result.projected_remaining_cents is None
    assert result.ratio_bp is None
    assert result.remaining_medical_cents is None
    # The half that *is* known is still published, because it is still true.
    assert result.remaining_indemnity_cents == RESERVE


def test_even_a_sound_light_verdict_publishes_no_ratio_when_a_term_is_missing() -> None:
    """One invariant rather than two: a ratio exists iff a full comparison did.

    The verdict is sound on a lower bound; the ratio behind it is a floor, and
    a floor published as the ratio is the same mislabel one field over.
    """
    result = check(indemnity=AT_LIGHT + 1, medical=None)

    assert result.verdict is ReserveVerdict.light
    assert result.ratio_bp is None
    assert result.projected_remaining_cents is None


def test_a_zero_medical_term_is_a_complete_exposure() -> None:
    """`0` is an answer about the claim; `None` is the absence of one."""
    result = check(indemnity=RESERVE, medical=0)

    assert result.remaining_medical_cents == 0
    assert result.projected_remaining_cents == RESERVE
    assert result.ratio_bp == 10_000


def test_a_settled_claim_is_still_closed_when_its_bills_are_unseen() -> None:
    """Lifecycle, not a band. Nothing an unknown term could make untrue."""
    result = check(stage=Stage.settled, indemnity=500_00, medical=None)

    assert result.verdict is ReserveVerdict.closed_final
    assert result.rationale == CLOSED_FINAL_RATIONALE


def test_the_withheld_rationale_names_what_is_missing_and_promises_nothing() -> None:
    # 90% of the reserve — comfortably `adequate` if this were the whole
    # exposure, which is exactly the verdict the missing term withholds.
    result = check(indemnity=9_000_00, medical=None)

    assert result.rationale == (
        "Medical bills are not yet on file, so remaining exposure cannot be totalled "
        "and the reserve ($10,000) cannot be judged. Scheduled indemnity alone "
        "accounts for $9,000. A verdict follows once bills are recorded."
    )


def test_the_partial_light_rationale_says_the_figure_is_a_floor() -> None:
    """A handler acting on it should know bills are not in the number."""
    result = check(indemnity=AT_LIGHT + 1, medical=None)

    assert result.rationale == (
        "Scheduled indemnity alone ($11,500) already exceeds current reserve "
        "($10,000) before medical bills are counted. Recommend re-evaluating "
        "reserve upward."
    )


def test_no_withheld_verdict_ever_advises_reallocating() -> None:
    """The sentence that must not survive an incomplete exposure.

    Blunt on purpose: whatever else changes about the wording, advice to move
    money off a claim must never be reachable without that claim's bills.
    """
    for indemnity in (0, 1, RESERVE // 4, RESERVE, RESERVE * 3):
        rationale = check(indemnity=indemnity, medical=None).rationale
        assert "reallocating surplus" not in rationale
        assert "comfortably exceeds" not in rationale


# --- the zero-reserve sentinel -------------------------------------------


def test_a_zero_reserve_with_exposure_remaining_is_light() -> None:
    """The prototype's `projectedRemaining > 0 ? 2 : 1`, first half.

    A claim carrying exposure against no reserve at all is under-reserved, and
    saying so is the honest verdict rather than a division guard.
    """
    result = check(reserve=0, indemnity=1)

    assert result.verdict is ReserveVerdict.light
    assert result.ratio_bp == ZERO_RESERVE_EXPOSED_RATIO_BP


def test_a_zero_reserve_with_nothing_remaining_is_adequate() -> None:
    """The second half: no exposure against no reserve is nothing to fix."""
    result = check(reserve=0)

    assert result.verdict is ReserveVerdict.adequate
    assert result.ratio_bp == ZERO_RESERVE_CLEAR_RATIO_BP


def test_a_zero_reserve_is_never_heavy() -> None:
    """There is no surplus to reallocate when there is no reserve."""
    assert check(reserve=0).verdict is not ReserveVerdict.heavy
    assert check(reserve=0, indemnity=10_00).verdict is not ReserveVerdict.heavy


def test_a_reserve_that_has_gone_negative_takes_the_zero_branch() -> None:
    """No column stops a reserve going below zero, so the rule has to answer.

    A negative reserve is a data state rather than a policy, and the sentinel
    branch is the one that describes it: any remaining exposure is
    under-reserved, which is what a handler needs to be told. The alternative —
    letting it through the cross-multiplication — flips both comparisons,
    because multiplying an inequality by a negative reverses it, and a claim
    with a $50,000 exposure against a negative reserve would be reported
    *heavy*.
    """
    assert check(reserve=-1, indemnity=50_000_00).verdict is ReserveVerdict.light


# --- a settled claim is not banded at all --------------------------------


def test_a_settled_claim_is_closed_final_with_no_ratio() -> None:
    result = check(stage=Stage.settled, indemnity=RESERVE * 5)

    assert result.verdict is ReserveVerdict.closed_final
    assert result.ratio_bp is None
    assert result.rationale == CLOSED_FINAL_RATIONALE


def test_a_settled_claims_rationale_names_no_figures() -> None:
    """ "No further reserve exposure" is a statement, not a comparison.

    Every other verdict quotes both sides of the comparison it made; this one
    made none, so quoting a reserve here would be the sentence implying an
    arithmetic that did not happen.
    """
    rationale = check(stage=Stage.settled, indemnity=RESERVE * 5).rationale

    assert "$" not in rationale


def test_a_settled_claim_still_reports_the_exposure_it_was_handed() -> None:
    """The figures are the real ones, not hardcoded zeroes.

    A settled claim's schedule is fully paid and its bills are closed, so these
    are zero in practice — reporting the computed values rather than asserting
    them away is what would let a settled claim with an unpaid bill be visible
    in the payload instead of silently rounded to "nothing to see".
    """
    result = check(stage=Stage.settled, indemnity=700_00, medical=300_00)

    assert result.remaining_indemnity_cents == 700_00
    assert result.remaining_medical_cents == 300_00
    assert result.projected_remaining_cents == 1_000_00


# --- the rationale -------------------------------------------------------


def test_the_light_rationale_asks_for_the_reserve_to_be_raised() -> None:
    result = check(indemnity=15_000_00)

    assert result.rationale == (
        "Projected remaining exposure ($15,000) exceeds current reserve ($10,000). "
        "Recommend re-evaluating reserve upward."
    )


def test_the_heavy_rationale_offers_the_surplus_back() -> None:
    result = check(indemnity=2_000_00)

    assert result.rationale == (
        "Current reserve ($10,000) comfortably exceeds projected remaining exposure "
        "($2,000). Consider reallocating surplus."
    )


def test_the_adequate_rationale_states_the_alignment() -> None:
    result = check(indemnity=10_000_00)

    assert result.rationale == (
        "Reserve ($10,000) is well aligned with projected remaining exposure ($10,000)."
    )


@pytest.mark.parametrize(
    "verdict_case",
    [
        pytest.param({"indemnity": 15_000_00}, id="light"),
        pytest.param({"indemnity": 10_000_00}, id="adequate"),
        pytest.param({"indemnity": 2_000_00}, id="heavy"),
    ],
)
def test_every_open_verdict_quotes_both_sides_of_its_comparison(
    verdict_case: dict[str, int],
) -> None:
    """A note stating one figure asks the reader to take the other on trust."""
    rationale = check(**verdict_case).rationale  # type: ignore[arg-type]

    assert "$10,000" in rationale
    assert rationale.count("$") == 2


def test_the_rationale_formats_money_the_way_the_card_beside_it_does() -> None:
    """`format_dollars`' half-up convention, on a claim with a cents component.

    The obligation `services/financials/rationale.py` writes down: this
    paragraph and the formatted reserve two rows above it are the same figure,
    so a second rounding rule here would show a handler two numbers for one
    reserve. $10,000.50 rounds up; $10,000.49 does not.
    """
    assert "$10,001" in check(reserve=10_000_50, indemnity=1).rationale
    assert "$10,000" in check(reserve=10_000_49, indemnity=1).rationale


# --- the bands are the document's, not the code's ------------------------


def test_the_bands_come_from_the_document_not_from_the_code() -> None:
    """AC 2, asserted where it can actually fail.

    A variant block — the shape a *different* effective-dated document would
    produce — is handed to the same function, and an exposure that is `light`
    under the seeded 1.15 must come back `adequate` under a 2.00. If either
    threshold were a literal in `services/financials/reserve.py`, this reads
    identically to the seeded case and the test fails.

    The block rather than a live document because the loader's date resolution
    is `test_rules_engine.py`'s subject and is exercised there against the real
    row; what is unproven anywhere else is that the *classifier consumes* what
    the loader returns.
    """
    loose = ReserveBands(version=99, light_ratio_bp=20_000, heavy_ratio_bp=1_000)
    exposure = RESERVE + RESERVE // 2  # 150% — light under the seeded bands

    assert check(indemnity=exposure).verdict is ReserveVerdict.light
    assert check(indemnity=exposure, bands=loose).verdict is ReserveVerdict.adequate


def test_the_verdict_names_the_document_version_that_decided_it() -> None:
    assert check(indemnity=1).bands_version == 1
    assert check(indemnity=1, bands=ReserveBands(7, 11_500, 6_000)).bands_version == 7


@pytest.mark.parametrize("bad", [-1, -11_500])
def test_a_negative_band_is_refused_by_the_rules_tier(bad: int) -> None:
    with pytest.raises(RuleParameterError, match="must not be negative"):
        ReserveBands(version=1, light_ratio_bp=bad, heavy_ratio_bp=bad)


def test_inverted_bands_are_refused_because_adequate_becomes_unreachable() -> None:
    with pytest.raises(RuleParameterError, match="must not exceed"):
        ReserveBands(version=1, light_ratio_bp=6_000, heavy_ratio_bp=11_500)


def test_equal_bands_are_allowed() -> None:
    """A hairline between light and heavy is a policy, not a mistake.

    `treatmentEarlyMaxRatio == treatmentActiveMaxRatio` is accepted for the
    same reason: the collapsed band is still *reachable* — an exposure exactly
    on the shared threshold is adequate, because both comparisons are strict.
    """
    hairline = ReserveBands(version=1, light_ratio_bp=10_000, heavy_ratio_bp=10_000)

    assert check(indemnity=RESERVE, bands=hairline).verdict is ReserveVerdict.adequate
    assert check(indemnity=RESERVE + 1, bands=hairline).verdict is ReserveVerdict.light
    assert check(indemnity=RESERVE - 1, bands=hairline).verdict is ReserveVerdict.heavy


@pytest.mark.parametrize("missing", ["lightRatioBp", "heavyRatioBp"])
def test_every_band_parameter_is_required(missing: str) -> None:
    """A document missing one is refused at the boundary, naming it.

    The reason `rules/parameters.py` exists: a classifier written against a raw
    dict would fail with a `KeyError` on a request path, naming a string rather
    than a rule.
    """
    document = LoadedDocument(key="reserve_bands", version=1, content={})
    result = {"lightRatioBp": 11_500, "heavyRatioBp": 6_000}
    del result[missing]

    with pytest.raises(RuleParameterError, match=missing):
        ReserveBands.of(document, result)


# --- NFR-7: the classification is total ----------------------------------


@settings(max_examples=500)
@given(
    reserve=st.integers(min_value=0, max_value=10_000_000_00),
    indemnity=st.integers(min_value=0, max_value=10_000_000_00),
    medical=st.one_of(st.none(), st.integers(min_value=0, max_value=10_000_000_00)),
    stage=st.sampled_from(list(Stage)),
)
def test_exactly_one_verdict_is_returned_and_the_exposure_is_its_two_terms(
    reserve: int, indemnity: int, medical: int | None, stage: Stage
) -> None:
    """Totality, over the whole non-negative input space (NFR-7).

    Two properties, deliberately asserted together because they are the two
    ways this function can fail without raising: it can fall off the end of its
    branches and return nothing meaningful, or it can publish a total that is
    not the sum of the parts beside it — which is how a payload starts telling
    a handler that $40,000 of exposure is made of $12,000 and $9,000.
    """
    result = classify_reserve(
        stage=stage,
        reserve_cents=reserve,
        remaining_indemnity_cents=indemnity,
        remaining_medical_cents=medical,
        scheduled_indemnity_cents=indemnity,
        disbursed_indemnity_cents=0,
        disbursed_medical_cents=0,
        bands=BANDS,
    )

    complete = medical is not None
    # Narrowed into a local so the assertions below read as arithmetic rather
    # than as a chain of `is not None` — mypy needs it and so does the reader.
    known_medical = medical if medical is not None else 0

    assert result.verdict in set(ReserveVerdict)
    assert result.remaining_indemnity_cents == indemnity
    assert result.remaining_medical_cents == medical
    assert result.reserve_cents == reserve
    assert result.rationale
    # A total exactly when every term is in it, and it is that sum.
    assert (result.projected_remaining_cents is None) != complete
    if complete:
        assert result.projected_remaining_cents == indemnity + known_medical
    # A ratio exactly when a complete comparison produced one.
    assert (result.ratio_bp is not None) == (complete and stage is not Stage.settled)
    # The two verdicts an unknown term could flip are never reached without it,
    # and `indeterminate` is never reached with it.
    if not complete and stage is not Stage.settled:
        assert result.verdict in {ReserveVerdict.light, ReserveVerdict.indeterminate}
    if complete:
        assert result.verdict is not ReserveVerdict.indeterminate


@settings(max_examples=500)
@given(
    reserve=st.integers(min_value=1, max_value=10_000_000_00),
    exposure=st.integers(min_value=0, max_value=10_000_000_00),
)
def test_the_verdict_agrees_with_the_ratio_it_publishes(reserve: int, exposure: int) -> None:
    """The published ratio and the decided verdict cannot contradict each other.

    They are computed differently on purpose — the verdict cross-multiplies
    exactly, the ratio rounds half up for display — so this is the property
    that keeps the rounding from ever being visible as a contradiction. It
    allows the ratio to *land on* a boundary the verdict did not cross (that is
    what rounding does) and forbids it from landing on the far side.
    """
    result = classify_reserve(
        stage=Stage.treatment,
        reserve_cents=reserve,
        remaining_indemnity_cents=exposure,
        remaining_medical_cents=0,
        scheduled_indemnity_cents=exposure,
        disbursed_indemnity_cents=0,
        disbursed_medical_cents=0,
        bands=BANDS,
    )
    assert result.ratio_bp is not None

    if result.verdict is ReserveVerdict.light:
        assert result.ratio_bp >= LIGHT_RATIO_BP
    elif result.verdict is ReserveVerdict.heavy:
        assert result.ratio_bp <= HEAVY_RATIO_BP
    else:
        assert HEAVY_RATIO_BP <= result.ratio_bp <= LIGHT_RATIO_BP
