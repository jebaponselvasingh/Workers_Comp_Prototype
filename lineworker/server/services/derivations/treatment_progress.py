"""`treatment_phase` — where a claim is inside its expected recovery window.

The prototype's `treatmentPhase` (line 1310), ported: read an expected number
of days out of the recovery-window text, divide the claim's age by it, and
band the ratio. AC 5 makes this a *registered* derivation rather than a
component helper, and that is the whole point of moving it — the phase banner
on the treatment overview and any later surface that shows a phase read the
same computer, so they cannot disagree about a claim a handler is looking at.

**Where the expected duration comes from.** `claim.recovery` is a
`RecoveryWindow` — five snake_case tokens since Story 2.3's code review. The
duration each one implies is a lookup, `EXPECTED_WEEKS` below, and the
year-scale case reads a JDM parameter because "how long is 'more than a
year', for scheduling purposes" is a tunable and 180 days is the prototype's
answer rather than an arithmetic fact.

Until that review this function ran a **regex over the display string** —
`claim.recovery` held `"4-6 Weeks"` and the upper bound was parsed out of it.
That made the label load-bearing: rewording it changed a derived value, and
the parser had been deliberately widened to accept `"6-8 weeks"` because
Story 2.3 was expected to make the field free text. It did the opposite, and
the tokens removed the parsing problem rather than making it more tolerant.

**The parameters are JDM (AD-8), the branching is here.** The two ratio
boundaries and the two day constants come from `derivation_thresholds`; the
regex, the "upper bound" choice and the order of the two comparisons are the
formula and stay in typed Python.

**The note is server-provided, the label is not.** Each phase carries a
sentence describing what that phase *is*, and it is prose about the rule, so
it belongs with the rule (AD-1: derived strings come from the endpoint). The
short display label — "Early Treatment & Diagnosis" — is a UI-owned label over
a snake_case enum value, like every other enum in this codebase.
"""

from dataclasses import dataclass
from enum import StrEnum

from data.models.enums import RecoveryWindow
from services.derivations.registry import Derivation, register

#: Weeks per member, for the four bounded windows. The **upper** bound in
#: each case: the optimistic end of a window is not what a claim is behind
#: schedule against. `over_1_year` is absent deliberately — it has no week
#: count, and its duration is the JDM parameter read in the builder below.
EXPECTED_WEEKS: dict[RecoveryWindow, int] = {
    RecoveryWindow.weeks_0_2: 2,
    RecoveryWindow.weeks_2_4: 4,
    RecoveryWindow.weeks_4_6: 6,
    RecoveryWindow.weeks_6_8: 8,
}

DAYS_PER_WEEK = 7
"""Not a JDM parameter. A week is seven days whatever the business rules say;
putting it in a document would invite an operator to answer a scheduling
question by redefining the calendar."""


class TreatmentPhase(StrEnum):
    """Snake_case values per the enum convention — the UI owns labels."""

    early = "early"
    active = "active"
    approaching_mmi = "approaching_mmi"


# The prototype's three sentences, verbatim. Keyed by the phase they describe
# so a fourth phase cannot be added without one.
PHASE_NOTES: dict[TreatmentPhase, str] = {
    TreatmentPhase.early: (
        "Initial medical workup, diagnostics, and treatment plan establishment."
    ),
    TreatmentPhase.active: (
        "Ongoing therapy/rehabilitation per treatment plan; regular provider check-ins."
    ),
    TreatmentPhase.approaching_mmi: (
        "Nearing maximum medical improvement — RTW and closure planning underway."
    ),
}


@dataclass(frozen=True)
class TreatmentPhaseResult:
    """The phase, the sentence that describes it, and the window behind both.

    `expected_days` rides along because the banner shows "Day N of claim ·
    Expected recovery: …" and because it is the number that makes the phase
    explainable — a handler asking why a six-week claim is "Approaching MMI"
    on day 30 is answered by 42, not by 0.71.
    """

    phase: TreatmentPhase
    note: str
    expected_days: int


@dataclass(frozen=True)
class TreatmentPhaseDerivation:
    """Bands `days_open / expected_days` into three phases.

    The boundaries are exclusive-below, matching the prototype's `<`: a claim
    exactly at 0.3 of its window is `active`, not `early`. Stated because it
    is the kind of edge a reimplementation flips without noticing, and
    `tests/test_case_file_derivations.py` pins both.
    """

    early_max_ratio: float
    active_max_ratio: float
    year_expected_days: int
    default_expected_days: int

    def expected_days_for(self, recovery: RecoveryWindow | None) -> int:
        """Days the window implies — a lookup, not a parse.

        `default_expected_days` is the answer for a member with no configured
        duration. It is unreachable while `EXPECTED_WEEKS` covers the four
        bounded windows and the fifth reads `year_expected_days`
        (`tests/test_case_file_derivations.py` asserts that coverage), and it
        stays because adding a sixth member should give a sensible window
        rather than a `KeyError` in a request path.
        """
        if recovery is None:
            return self.default_expected_days
        if recovery is RecoveryWindow.over_1_year:
            return self.year_expected_days
        weeks = EXPECTED_WEEKS.get(recovery)
        return weeks * DAYS_PER_WEEK if weeks is not None else self.default_expected_days

    def of(self, *, days_open: int, recovery: RecoveryWindow | None) -> TreatmentPhaseResult:
        expected_days = self.expected_days_for(recovery)
        # `expected_days` is validated positive when the parameters are read
        # (`rules/parameters.py`), and every member maps to a positive week
        # count — so this is unreachable today. It stays because the default
        # branch reads a JDM parameter a document could set to zero, and "no
        # time was expected" means any elapsed day is already past the end of
        # the window rather than a division by zero in a request path.
        if expected_days <= 0:
            return self._result(TreatmentPhase.approaching_mmi, expected_days)

        ratio = days_open / expected_days
        if ratio < self.early_max_ratio:
            return self._result(TreatmentPhase.early, expected_days)
        if ratio < self.active_max_ratio:
            return self._result(TreatmentPhase.active, expected_days)
        return self._result(TreatmentPhase.approaching_mmi, expected_days)

    @staticmethod
    def _result(phase: TreatmentPhase, expected_days: int) -> TreatmentPhaseResult:
        return TreatmentPhaseResult(
            phase=phase, note=PHASE_NOTES[phase], expected_days=expected_days
        )


treatment_phase = register(
    Derivation(
        name="treatment_phase",
        describes="where claim age sits inside its recovery window (early/active/approaching MMI)",
        build=lambda thresholds: TreatmentPhaseDerivation(
            early_max_ratio=thresholds.treatment_early_max_ratio,
            active_max_ratio=thresholds.treatment_active_max_ratio,
            year_expected_days=thresholds.recovery_year_expected_days,
            default_expected_days=thresholds.recovery_default_expected_days,
        ),
    )
)
