"""Story 3.1 AC 1, 2, 3, 5 — the benefit formula, without a database.

`compute_benefit` is pure by design (`benefit_for_claim` is the thin async
wrapper that fetches its two data arguments), which is what lets the clamp
property below run over every seeded jurisdiction ten thousand times without a
connection. That split is the point of the arrangement, not a convenience.

**The parameters here are restated, never imported from the rules tier.** Same
oracle discipline as `seed_fixture.HIGH_RISK_MIN` and
`tests/test_derivations.py`'s `SEEDED_THRESHOLDS`: a test that read the comp
rate out of the document under test would agree with it however wrong both
were. `tests/test_rules_engine.py` is what ties these values back to the
committed documents.

**The states are read from the seed file, and that is deliberate rather than
lazy.** NFR-7 asks for the clamp to hold "across statutory ranges for all
seeded states", so the strategy samples the real seventeen: a property written
against invented bounds would prove something about arithmetic and nothing
about Iowa.
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from data.models.core import StateRateSchedule
from data.models.enums import Disability, RecoveryWindow, ReturnStatus
from rules.parameters import BenefitParams, DerivationThresholds
from services import derivations
from services.derivations import IndemnityType
from services.financials import (
    BASIS_POINTS_PER_UNIT,
    COMP_RATE_MAX_BP,
    COMP_RATE_MIN_BP,
    compute_benefit,
)

SERVER_ROOT = Path(__file__).resolve().parents[1]
RATES_PATH = SERVER_ROOT / "data" / "seed" / "state_rates.json"

# The seeded documents, restated. See the module docstring.
DEFAULT_COMP_RATE_BP = 6667
PTD_COMP_RATE_BP = 10_000
WAITING_PERIOD_DAYS = 7
PTD_SEVERITY_THRESHOLD = 85

PARAMS = BenefitParams(
    version=1,
    default_comp_rate_bp=DEFAULT_COMP_RATE_BP,
    ptd_comp_rate_bp=PTD_COMP_RATE_BP,
    waiting_period_days=WAITING_PERIOD_DAYS,
)

THRESHOLDS = DerivationThresholds(
    version=7,
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
    ptd_severity_threshold=PTD_SEVERITY_THRESHOLD,
    # Story 5.1's, added in version 5 — the dashboard's fraud REVIEW cut-off,
    # which is deliberately not `siu_fraud_score_min` (the referral one).
    fraud_flag_score_min=55,
    # Story 7.1's two, added in version 6 — the edges of the fraud-score BAND the
    # analyst workspace distributes on. A third rule over the fraud columns, not a
    # re-spelling of either above it: both of those are conjoined with
    # `fraud_flag`, and this pair bands the score alone. `fraud_band_high_min`
    # carries the same integer as `fraud_flag_score_min` today, which is exactly
    # why the oracle restates it separately rather than reusing the name.
    fraud_band_high_min=55,
    fraud_band_med_min=35,
    # Story 7.3's three, added in version 7 — the edges of the AGE band the
    # analyst workspace segments by. Restated separately from every number above
    # them although two of the three coincide: `age_younger_min` is 35 like
    # `risk_med_min` and `fraud_band_med_min`, and `age_oldest_min` is 55 like
    # `fraud_flag_score_min` and `fraud_band_high_min`. Three columns, three
    # rules, one integer twice — and an oracle that shared a name between any of
    # them could not notice the day a document moved one.
    age_younger_min=35,
    age_older_min=45,
    age_oldest_min=55,
)


@dataclass(frozen=True)
class FakeClaim:
    """The eleven columns `BenefitClaim` names, and nothing else.

    A stand-in rather than a `Claim`, for `EditableClaim`'s reason (Story
    2.3): constructing the ORM row means supplying forty unrelated NOT NULL
    columns to ask about eleven. It is a *typed* stand-in — mypy checks it
    against the protocol at every call below — so it documents the read
    surface rather than casting past it.
    """

    state: str = "WA"
    aww: int = 143_200
    reserve: int = 2_234_900
    severity_score: int = 52
    disability: Disability = Disability.temporary
    return_status: ReturnStatus = ReturnStatus.under_treatment
    recovery: RecoveryWindow = RecoveryWindow.weeks_6_8
    injury_type: str = "Fall from Height"
    surgery_required: bool = False
    litigation_flag: bool = False
    comp_rate_override_bp: int | None = None


def seeded_rates() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(RATES_PATH.read_text(encoding="utf-8"))
    return rows


def rate_of(row: dict[str, Any]) -> StateRateSchedule:
    return StateRateSchedule(
        state_code=row["state_code"],
        state_name=row["state_name"],
        weekly_min_cents=row["weekly_min_cents"],
        weekly_max_cents=row["weekly_max_cents"],
        effective_date=date(2026, 1, 1),
    )


def rate_for(state_code: str) -> StateRateSchedule:
    row = next(rate for rate in seeded_rates() if rate["state_code"] == state_code)
    return rate_of(row)


#: A generous but bounded wage domain. The upper end is about $2.1M a week —
#: far past anything a manufacturing payroll produces, and chosen so the clamp
#: is exercised at both ends rather than only at the minimum.
AWW_CENTS = st.integers(min_value=0, max_value=210_000_000)
COMP_RATE_BP = st.integers(min_value=COMP_RATE_MIN_BP, max_value=COMP_RATE_MAX_BP)
SEEDED_RATES = st.sampled_from(seeded_rates())


# --- AC 5 (NFR-7): the clamp, as a property -----------------------------


@settings(max_examples=500)
@given(row=SEEDED_RATES, aww_cents=AWW_CENTS, comp_rate_bp=COMP_RATE_BP)
def test_the_weekly_benefit_is_always_inside_the_states_statutory_range(
    row: dict[str, Any], aww_cents: int, comp_rate_bp: int
) -> None:
    """NFR-7's named exemplar: ∀ seeded state, ∀ AWW ≥ 0, ∀ valid rate.

    The reason this is a property rather than a table of examples is that the
    clamp's failure mode is *invisible at the boundary you thought to check*.
    A formula that clamped only the upper end would pass every example anybody
    writes with a realistic wage, and would pay a minimum-wage worker in
    Indiana $0.75 a week.
    """
    rate = rate_of(row)
    benefit = compute_benefit(
        FakeClaim(state=row["state_code"], aww=aww_cents, comp_rate_override_bp=comp_rate_bp),
        rate,
        PARAMS,
        THRESHOLDS,
    )

    assert rate.weekly_min_cents <= benefit.weekly_cents <= rate.weekly_max_cents


@settings(max_examples=500)
@given(
    row=SEEDED_RATES,
    comp_rate_bp=st.integers(min_value=1, max_value=COMP_RATE_MAX_BP),
    data=st.data(),
)
def test_an_unclamped_benefit_is_the_rate_applied_to_the_wage(
    row: dict[str, Any], comp_rate_bp: int, data: st.DataObject
) -> None:
    """The other half of the property: the clamp must not be doing the work.

    A `compute_benefit` that returned the state minimum unconditionally would
    satisfy the test above for every input. So where the figure lands *inside*
    the bounds, it has to be the rate applied to the wage — stated as "within
    half a cent", which is exactly what rounding is allowed to move it by, and
    written as integer arithmetic so the assertion has no rounding of its own.

    The wage is **constructed** to land inside the range rather than filtered
    towards it: a target weekly figure is drawn from the state's own interval
    and the wage that produces it is derived. Filtering a wide wage domain
    would discard nineteen inputs in twenty (most wages clamp, which is what
    the test above is for) and Hypothesis would rightly complain that the
    surviving distribution says little.
    """
    rate = rate_of(row)
    target_cents = data.draw(
        st.integers(min_value=rate.weekly_min_cents + 1, max_value=rate.weekly_max_cents - 1)
    )
    aww_cents = target_cents * BASIS_POINTS_PER_UNIT // comp_rate_bp

    benefit = compute_benefit(
        FakeClaim(state=row["state_code"], aww=aww_cents, comp_rate_override_bp=comp_rate_bp),
        rate,
        PARAMS,
        THRESHOLDS,
    )
    # The integer division above can land a cent outside the interval it aimed
    # at, in which case the clamp legitimately fires and there is nothing here
    # to assert. Rare by construction rather than by luck.
    assume(rate.weekly_min_cents < benefit.weekly_cents < rate.weekly_max_cents)

    exact = aww_cents * comp_rate_bp
    assert abs(benefit.weekly_cents * BASIS_POINTS_PER_UNIT - exact) * 2 <= BASIS_POINTS_PER_UNIT


@given(row=SEEDED_RATES)
def test_a_zero_wage_still_pays_the_states_minimum(row: dict[str, Any]) -> None:
    """The end of the clamp a realistic example never reaches.

    A claim whose AWW is zero — a seasonal worker in their first week, a data
    error — is entitled to the statutory floor, not to nothing. It is the
    single most consequential thing the lower clamp does and the one no
    example-based test would think to write.
    """
    rate = rate_of(row)
    benefit = compute_benefit(FakeClaim(state=row["state_code"], aww=0), rate, PARAMS, THRESHOLDS)

    assert benefit.weekly_cents == rate.weekly_min_cents


# --- AC 2: integer cents, and the rounding convention -------------------


@pytest.mark.parametrize(
    ("aww_cents", "comp_rate_bp", "expected_cents"),
    [
        # Exact: no rounding involved.
        (100_000, 5_000, 50_000),
        # A half-cent, rounded **up** — the convention, and the case Python's
        # own `round()` would send to the even neighbour (50 rather than 51).
        (101, 5_000, 51),
        # …and the neighbour on the other side of the tie, which half-even
        # would round the same way and half-up does not.
        (103, 5_000, 52),
        # The prototype's own worked example, in cents rather than dollars:
        # $1,432 AWW at 66.67% is $954.7144, which this console reports to the
        # cent and the prototype rounds to $955 because its whole data model
        # is dollars.
        (143_200, DEFAULT_COMP_RATE_BP, 95_471),
    ],
)
def test_the_weekly_figure_rounds_half_up_on_cents(
    aww_cents: int, comp_rate_bp: int, expected_cents: int
) -> None:
    """The rounding convention every later financial story inherits.

    Written as examples rather than as a property because the *convention* is
    the thing under test — half up, on cents — and a property would restate
    the implementation. Two of the four cases are exact ties, which is where
    half-up and Python's default half-even disagree and where a `round()` in
    the formula would show up.
    """
    # Bounds wide enough that the clamp cannot be what produces the answer.
    rate = StateRateSchedule(
        state_code="ZZ",
        state_name="Nowhere",
        weekly_min_cents=0,
        weekly_max_cents=100_000_000,
        effective_date=date(2026, 1, 1),
    )
    benefit = compute_benefit(
        FakeClaim(aww=aww_cents, comp_rate_override_bp=comp_rate_bp), rate, PARAMS, THRESHOLDS
    )

    assert benefit.weekly_cents == expected_cents


def test_every_money_field_on_the_benefit_is_an_integer() -> None:
    """AC 2, structurally. A float that reached one of these would format
    correctly on the card and drift the moment Story 3.3 sums a schedule."""
    benefit = compute_benefit(FakeClaim(), rate_for("WA"), PARAMS, THRESHOLDS)

    for value in (
        benefit.weekly_cents,
        benefit.state_min_cents,
        benefit.state_max_cents,
        benefit.comp_rate_bp,
        benefit.default_comp_rate_bp,
        benefit.comp_rate_min_bp,
        benefit.comp_rate_max_bp,
    ):
        assert isinstance(value, int) and not isinstance(value, bool)


# --- AC 1 and 3: the PTD branch, and what it changes --------------------


@pytest.mark.parametrize(
    ("severity_score", "expected_type", "expected_bp"),
    [
        (PTD_SEVERITY_THRESHOLD - 1, IndemnityType.ppd, DEFAULT_COMP_RATE_BP),
        (PTD_SEVERITY_THRESHOLD, IndemnityType.ptd, PTD_COMP_RATE_BP),
        (PTD_SEVERITY_THRESHOLD + 1, IndemnityType.ptd, PTD_COMP_RATE_BP),
    ],
)
def test_the_ptd_branch_turns_on_at_the_threshold_and_pays_full_wage(
    severity_score: int, expected_type: IndemnityType, expected_bp: int
) -> None:
    """84 / 85 / 86, because the cut-off is `>=` and off-by-one here is the
    difference between two thirds of a wage and all of it.

    The rate and the type are asserted **together**, which is the property the
    design is really about: `compute_benefit` does not test the score itself,
    it asks `indemnity_type` and pays accordingly, so the two cannot disagree.
    """
    claim = FakeClaim(disability=Disability.permanent, severity_score=severity_score)
    benefit = compute_benefit(claim, rate_for("IA"), PARAMS, THRESHOLDS)

    assert benefit.indemnity_type is expected_type
    assert benefit.default_comp_rate_bp == expected_bp
    assert benefit.comp_rate_bp == expected_bp


@pytest.mark.parametrize(
    ("disability", "severity_score", "return_status", "expected"),
    [
        (
            Disability.temporary,
            50,
            ReturnStatus.under_treatment,
            IndemnityType.ttd,
        ),
        (
            Disability.temporary,
            50,
            ReturnStatus.returned_and_under_therapy,
            IndemnityType.tpd,
        ),
        # Ported from the prototype and odd on its face: a worker who has
        # returned fully recovered is still TTD, because `disability` is what
        # the *claim* pays on. Recorded in the story's Dev Agent Record as a
        # question for a product owner rather than quietly "fixed".
        (
            Disability.temporary,
            50,
            ReturnStatus.returned_and_fully_recovered,
            IndemnityType.ttd,
        ),
        (Disability.permanent, 50, ReturnStatus.under_treatment, IndemnityType.ppd),
        (Disability.permanent, 95, ReturnStatus.under_treatment, IndemnityType.ptd),
        # A permanent claim's return status does not enter the branch at all.
        (
            Disability.permanent,
            95,
            ReturnStatus.returned_and_under_therapy,
            IndemnityType.ptd,
        ),
    ],
)
def test_the_derivation_answers_all_four_indemnity_types(
    disability: Disability,
    severity_score: int,
    return_status: ReturnStatus,
    expected: IndemnityType,
) -> None:
    """AC 3, at the derivation rather than through the card.

    Every combination that reaches a different branch, including the two the
    prototype's substring scan over `"…Therapy"` gets right by accident and
    the one it gets wrong-looking on purpose.
    """
    answered = derivations.indemnity_type.for_thresholds(THRESHOLDS).of(
        disability=disability,
        severity_score=severity_score,
        return_status=return_status,
    )

    assert answered is expected


def test_the_benefit_and_the_derivation_cannot_disagree_about_the_type() -> None:
    """The placement argument, asserted: one reader of the PTD condition.

    The prototype computes `isPTD` inline and uses it twice — for the rate and
    for the label — so a change to one leaves the other. Here the *derivation*
    is the only thing that decides, and `compute_benefit` publishes its answer,
    which is what this compares.
    """
    for severity_score in range(80, 91):
        claim = FakeClaim(disability=Disability.permanent, severity_score=severity_score)
        benefit = compute_benefit(claim, rate_for("IA"), PARAMS, THRESHOLDS)
        expected = derivations.indemnity_type.for_thresholds(THRESHOLDS).of_claim(
            # `of_claim` reads the same three columns off a row; the stand-in
            # satisfies it structurally, which is the point of the protocol.
            claim  # type: ignore[arg-type]
        )

        assert benefit.indemnity_type is expected
        assert (benefit.default_comp_rate_bp == PTD_COMP_RATE_BP) is (expected is IndemnityType.ptd)


# --- AC 4: the override, and what resets it -----------------------------


def test_without_an_override_the_statutory_default_applies() -> None:
    benefit = compute_benefit(FakeClaim(), rate_for("WA"), PARAMS, THRESHOLDS)

    assert benefit.is_overridden is False
    assert benefit.comp_rate_bp == DEFAULT_COMP_RATE_BP
    assert benefit.default_comp_rate_bp == DEFAULT_COMP_RATE_BP


def test_an_override_replaces_the_rate_and_moves_the_weekly_figure() -> None:
    claim = FakeClaim(comp_rate_override_bp=7_500)
    benefit = compute_benefit(claim, rate_for("WA"), PARAMS, THRESHOLDS)
    baseline = compute_benefit(FakeClaim(), rate_for("WA"), PARAMS, THRESHOLDS)

    assert benefit.is_overridden is True
    assert benefit.comp_rate_bp == 7_500
    # The default is still published, because the ↺ control has to say what it
    # would restore.
    assert benefit.default_comp_rate_bp == DEFAULT_COMP_RATE_BP
    assert benefit.weekly_cents > baseline.weekly_cents


def test_clearing_the_override_restores_the_default_exactly() -> None:
    """AC 4's ↺, at the level the command's write reaches: a null column.

    Compared field by field against a claim that was never overridden, because
    "reset" has to mean *the same benefit*, not merely a similar one — a
    rationale still carrying the override sentence would be the visible half of
    getting this wrong.
    """
    overridden = compute_benefit(
        FakeClaim(comp_rate_override_bp=12_000), rate_for("WA"), PARAMS, THRESHOLDS
    )
    reset = compute_benefit(
        FakeClaim(comp_rate_override_bp=None), rate_for("WA"), PARAMS, THRESHOLDS
    )
    untouched = compute_benefit(FakeClaim(), rate_for("WA"), PARAMS, THRESHOLDS)

    assert overridden != reset
    assert reset == untouched


def test_typing_the_default_back_in_is_still_an_override() -> None:
    """Why `isOverridden` is published rather than inferred from the two rates.

    A handler who sets the rate to exactly 66.67% has made a decision the case
    file records — the column is non-null — and the ↺ control has to appear for
    them. A client comparing `compRateBp` with `defaultCompRateBp` would answer
    "not overridden" and hide the only way back.
    """
    benefit = compute_benefit(
        FakeClaim(comp_rate_override_bp=DEFAULT_COMP_RATE_BP), rate_for("WA"), PARAMS, THRESHOLDS
    )

    assert benefit.is_overridden is True
    assert benefit.comp_rate_bp == benefit.default_comp_rate_bp


def test_an_override_on_a_ptd_claim_replaces_the_full_wage_rate() -> None:
    """The two rate sources meeting: the override wins over the PTD rate, and
    the *default* the ↺ restores is still the PTD one rather than 66.67%."""
    claim = FakeClaim(
        disability=Disability.permanent, severity_score=95, comp_rate_override_bp=8_000
    )
    benefit = compute_benefit(claim, rate_for("IA"), PARAMS, THRESHOLDS)

    assert benefit.comp_rate_bp == 8_000
    assert benefit.default_comp_rate_bp == PTD_COMP_RATE_BP
    assert benefit.indemnity_type is IndemnityType.ptd


# --- the rest of the card's facts ---------------------------------------


def test_the_benefit_carries_the_states_identity_and_provenance() -> None:
    """The card names the jurisdiction, its bounds and the schedule's date —
    the last standing in for the prototype's "Illustrative figures" line."""
    benefit = compute_benefit(FakeClaim(state="IA"), rate_for("IA"), PARAMS, THRESHOLDS)

    assert benefit.state_code == "IA"
    assert benefit.state_name == "Iowa"
    assert benefit.state_min_cents == 42_000
    assert benefit.state_max_cents == 210_100
    assert benefit.schedule_effective_date == date(2026, 1, 1)
    assert benefit.waiting_days == WAITING_PERIOD_DAYS
    assert benefit.params_version == PARAMS.version


def test_the_override_bounds_are_published_for_the_input_to_use() -> None:
    """Served rather than restated in the SPA, exactly as the severity bounds
    are — `noDerivation.test.ts` fails a build over a numeric rule in a React
    component."""
    benefit = compute_benefit(FakeClaim(), rate_for("WA"), PARAMS, THRESHOLDS)

    assert (benefit.comp_rate_min_bp, benefit.comp_rate_max_bp) == (
        COMP_RATE_MIN_BP,
        COMP_RATE_MAX_BP,
    )
