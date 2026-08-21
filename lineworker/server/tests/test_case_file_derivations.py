"""Story 2.2 AC 5 — `treatment_phase` and `coordination_status`.

Both are registered derivations (AD-10), so what needs asserting is the same
two things `test_derivations.py` asserts about `risk`: that the rule is
*right*, and that it is the *only* one — nothing in the SPA and nothing in a
second service recomputes it.

No database. The derivations are pure functions of a parameter block and a
few claim fields, so every case here is one call — which is what lets
Hypothesis run the phase rule over its whole input domain.

Parameters are written out rather than loaded: `tests/test_rules_engine.py`
is what ties these numbers to the committed document. A test that read the
document to decide what the document should say would pass against any
document at all.
"""

import re
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from data.models.enums import CommStatus, RecoveryWindow
from rules.parameters import DerivationThresholds
from services import derivations
from services.derivations import CoordinationStatus, TreatmentPhase
from services.derivations.care_coordination import STATUS_NOTES
from services.derivations.treatment_progress import EXPECTED_WEEKS, PHASE_NOTES

SERVER_ROOT = Path(__file__).resolve().parents[1]

# Version 5 of `derivation_thresholds`, restated — the current committed
# document. See the module docstring. (This comment said "version 3" through
# 3.1's version 4 and into 5.1's version 5: it names the version below it, so
# it moves with every supersession that touches this block.)
SEEDED_THRESHOLDS = DerivationThresholds(
    version=6,
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
    # Story 3.1's, added in version 4 — the PTD cut-off `indemnity_type`
    # bands `severity_score` against.
    ptd_severity_threshold=85,
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
)


def phase_for(**overrides: Any) -> derivations.TreatmentPhaseDerivation:
    return derivations.treatment_phase.for_thresholds(replace(SEEDED_THRESHOLDS, **overrides))


def coordination() -> derivations.CoordinationDerivation:
    return derivations.coordination_status.for_thresholds(SEEDED_THRESHOLDS)


# --- both are registered, once ------------------------------------------


@pytest.mark.parametrize("name", ["treatment_phase", "coordination_status"])
def test_the_derivation_is_in_the_registry_under_its_canonical_name(name: str) -> None:
    """AC 5 is a claim about *where* these are computed, so the registry
    entry is the acceptance criterion, not an implementation detail."""
    assert name in derivations.registered_names()
    assert derivations.get(name).describes


# --- the recovery window ------------------------------------------------


@pytest.mark.parametrize(
    ("recovery", "expected_days"),
    [
        (RecoveryWindow.weeks_0_2, 14),
        (RecoveryWindow.weeks_2_4, 28),
        (RecoveryWindow.weeks_4_6, 42),
        (RecoveryWindow.weeks_6_8, 56),
        (RecoveryWindow.over_1_year, 180),
    ],
)
def test_each_window_maps_to_its_upper_bound_in_days(
    recovery: RecoveryWindow, expected_days: int
) -> None:
    """The upper bound, times seven — never the optimistic end of the window.

    A claim is behind schedule against the *long* end of its estimate; using
    the lower bound would push every six-to-eight-week claim into
    "Approaching MMI" a fortnight early.

    **Re-pointed by the Story 2.3 code review.** This block used to feed the
    derivation display strings (`"4-6 weeks"`, `"Approximately 6-12 Weeks
    post-op"`, `"-6 Weeks"`) and assert what a regex made of them, because
    `claim.recovery` was free text. It is a `RecoveryWindow` enum now, so
    the parsing is gone and with it every one of those cases: a value outside
    these five cannot reach the column. What survives is the guarantee the
    block was really making — each window implies the right number of days.
    """
    assert phase_for().expected_days_for(recovery) == expected_days


def test_every_window_has_a_duration() -> None:
    """The lookup must be exhaustive, or a member falls to the default.

    `EXPECTED_WEEKS` covers the four bounded windows and the builder answers
    `over_1_year` from a JDM parameter, so a sixth member added without a
    duration is caught here rather than by a handler wondering why their
    claim is in the wrong phase.
    """
    covered = set(EXPECTED_WEEKS) | {RecoveryWindow.over_1_year}
    assert covered == set(RecoveryWindow)


def test_a_missing_recovery_window_is_the_default_not_a_crash() -> None:
    """`claim.recovery` is NOT NULL, but the derivation takes
    `RecoveryWindow | None` so a projection that omits it answers a default
    rather than raising inside a request."""
    assert phase_for().expected_days_for(None) == 42


# --- the phase ----------------------------------------------------------


@pytest.mark.parametrize(
    ("days_open", "phase"),
    [
        # A 6-week window is 42 expected days: early below 12.6, active
        # below 29.4, approaching MMI from there.
        (0, TreatmentPhase.early),
        (12, TreatmentPhase.early),
        (13, TreatmentPhase.active),
        (29, TreatmentPhase.active),
        (30, TreatmentPhase.approaching_mmi),
        (500, TreatmentPhase.approaching_mmi),
    ],
)
def test_the_phase_bands_claim_age_against_the_window(
    days_open: int, phase: TreatmentPhase
) -> None:
    assert phase_for().of(days_open=days_open, recovery=RecoveryWindow.weeks_4_6).phase is phase


@pytest.mark.parametrize(
    ("days_open", "phase"),
    [
        # Exactly on a boundary. The prototype compares with `<`, so the
        # boundary belongs to the *later* phase — the kind of edge a
        # reimplementation flips without noticing.
        (30, TreatmentPhase.active),  # 30/100 == 0.3 exactly
        (70, TreatmentPhase.approaching_mmi),  # 70/100 == 0.7 exactly
        (29, TreatmentPhase.early),
        (69, TreatmentPhase.active),
    ],
)
def test_a_boundary_ratio_belongs_to_the_later_phase(days_open: int, phase: TreatmentPhase) -> None:
    # A 100-day window, so the ratios are exact and the assertion is about
    # the comparison rather than about floating-point luck.
    # `None` rather than a member, because the 100-day window is the
    # *default* parameter and no member resolves to it.
    hundred_days = phase_for(recovery_default_expected_days=100)
    assert hundred_days.of(days_open=days_open, recovery=None).phase is phase


def test_a_zero_length_window_is_already_over_rather_than_a_division_by_zero() -> None:
    """The guard survives even though no member can reach it any more.

    While `recovery` was free text, `"0-0 Weeks"` was a legal string that
    multiplied out to zero and divided by it. The enum removes that input,
    and `rules/parameters.py` refuses a zero duration, so **nothing reachable
    produces one** — which is why the derivation is constructed directly here
    rather than through `phase_for`. The guard stays because it is the last
    thing between a division by zero and a request path, and it is asserted
    so that "unreachable" is a statement someone checked rather than assumed.
    """
    result = derivations.TreatmentPhaseDerivation(
        early_max_ratio=SEEDED_THRESHOLDS.treatment_early_max_ratio,
        active_max_ratio=SEEDED_THRESHOLDS.treatment_active_max_ratio,
        year_expected_days=SEEDED_THRESHOLDS.recovery_year_expected_days,
        default_expected_days=0,
    ).of(days_open=0, recovery=None)

    assert result.expected_days == 0
    assert result.phase is TreatmentPhase.approaching_mmi


def test_the_phase_carries_the_note_and_the_window_behind_it() -> None:
    """AD-1: the sentence is a derived string, so it comes from the server.

    `expected_days` rides along because it is what makes the phase
    explainable — a handler asking why a six-week claim is "Approaching MMI"
    on day 30 is answered by 42, not by 0.71.
    """
    result = phase_for().of(days_open=30, recovery=RecoveryWindow.weeks_4_6)

    assert result.expected_days == 42
    assert result.note == PHASE_NOTES[TreatmentPhase.approaching_mmi]
    assert result.note.strip() == result.note and result.note


def test_every_phase_has_a_note() -> None:
    """A fourth phase without a sentence would render an empty banner."""
    assert set(PHASE_NOTES) == set(TreatmentPhase)


@given(
    days_open=st.integers(min_value=0, max_value=100_000),
    recovery=st.one_of(st.none(), st.sampled_from(list(RecoveryWindow))),
)
def test_the_phase_is_total_over_its_whole_input_domain(
    days_open: int, recovery: RecoveryWindow | None
) -> None:
    """NFR-7's property: never raises, always one of three phases.

    The input domain shrank when `recovery` became an enum — it is now five
    members and `None` rather than arbitrary text — but `days_open` is still
    unbounded above, which is the half that turns a new boundary comparison
    into a 500 on a claim nobody thought to write a case for.
    """
    result = phase_for().of(days_open=days_open, recovery=recovery)

    assert result.phase in set(TreatmentPhase)
    assert result.note == PHASE_NOTES[result.phase]
    assert result.expected_days >= 0


# --- coordination -------------------------------------------------------


ON_TRACK_COMM = CommStatus.documents_received_and_approved


@pytest.mark.parametrize(
    ("rtw_blocked", "comm_status", "litigation_flag", "expected"),
    [
        (True, ON_TRACK_COMM, False, CoordinationStatus.coordination_gap),
        (
            False,
            CommStatus.need_for_additional_information,
            False,
            CoordinationStatus.awaiting_information,
        ),
        (
            False,
            CommStatus.incomplete_information,
            False,
            CoordinationStatus.awaiting_information,
        ),
        (False, ON_TRACK_COMM, True, CoordinationStatus.legal_coordination),
        (False, ON_TRACK_COMM, False, CoordinationStatus.on_track),
        (False, CommStatus.fnol_received, False, CoordinationStatus.on_track),
        (
            False,
            CommStatus.initial_approval_provided_treatment_underway,
            False,
            CoordinationStatus.on_track,
        ),
    ],
)
def test_each_coordination_branch(
    rtw_blocked: bool,
    comm_status: CommStatus,
    litigation_flag: bool,
    expected: CoordinationStatus,
) -> None:
    result = coordination().of(
        rtw_blocked=rtw_blocked, comm_status=comm_status, litigation_flag=litigation_flag
    )

    assert result.status is expected
    assert result.note == STATUS_NOTES[expected]


@pytest.mark.parametrize(
    ("rtw_blocked", "comm_status", "litigation_flag", "expected"),
    [
        # All three conditions at once: the blocked return wins.
        (
            True,
            CommStatus.need_for_additional_information,
            True,
            CoordinationStatus.coordination_gap,
        ),
        # Blocked plus litigation, no paperwork gap: still the blocked return.
        (True, ON_TRACK_COMM, True, CoordinationStatus.coordination_gap),
        # Paperwork gap plus litigation: the paperwork gap.
        (
            False,
            CommStatus.incomplete_information,
            True,
            CoordinationStatus.awaiting_information,
        ),
    ],
)
def test_the_precedence_order_is_the_rule(
    rtw_blocked: bool,
    comm_status: CommStatus,
    litigation_flag: bool,
    expected: CoordinationStatus,
) -> None:
    """A claim can satisfy several conditions at once, and the first match
    wins because the list is ordered by what a handler must act on first.

    Independent booleans would let the card show "Legal Coordination" on a
    claim whose recommended return date lapsed a month ago — the one of the
    four that must not be buried.
    """
    assert (
        coordination()
        .of(rtw_blocked=rtw_blocked, comm_status=comm_status, litigation_flag=litigation_flag)
        .status
        is expected
    )


def test_every_comm_status_resolves_to_a_coordination_state() -> None:
    """Total over the enum, so a sixth `comm_status` cannot land on a branch
    nobody wrote — the card would render with no label at all."""
    for comm_status in CommStatus:
        result = coordination().of(
            rtw_blocked=False, comm_status=comm_status, litigation_flag=False
        )
        assert result.status in set(CoordinationStatus)


def test_every_coordination_state_has_a_note() -> None:
    assert set(STATUS_NOTES) == set(CoordinationStatus)


# --- AD-10: nobody else computes either ---------------------------------


PHASE_RULE_READ = re.compile(
    # Two shapes a second implementation takes: a comparison against one of
    # the ratio boundaries, or a second *parse* of the recovery-window text.
    #
    # The window half matches `weeks` only where it is being read as data —
    # inside a regex or next to a subscript/match call — rather than the
    # bare word (code review, 2026-08-12). Story 3.1 is the statutory
    # benefit calculation, which discusses 104-week maxima and waiting
    # periods in ordinary prose; a word match would have failed that story's
    # CI with "no module outside the derivation restates the phase rule",
    # which is not what would have happened.
    #
    # **The follow-set lost `)` and `]` in Story 3.3**, and the reason is that
    # they were never evidence of anything. They matched `weeks)` and
    # `weeks]` — which is to say, any code that *iterates a list of weeks* —
    # and 3.3 is the story that makes a claim's schedule a persisted list of
    # them. Five modules tripped it, every one on a false positive:
    # `ScheduleWeekStatus` contains the letters `WeekS`, and
    # `sum(w.amount_cents for w in weeks)` is arithmetic over rows rather than
    # a second reading of the recovery window. Adding five entries to
    # `PHASE_HOME` would have kept the tick green by turning the guard off
    # over most of `services/financials`, which is exactly where a second
    # phase rule would be worth catching.
    #
    # What is left in the follow-set are regex metacharacters — `\s`, `\d` —
    # so `r"Weeks\s*"` still trips it and `for week in weeks)` does not. The
    # three assertions in `test_the_guards_would_notice_a_second_implementation`
    # were all matched by the other two alternatives already, and it gained a
    # fourth that pins ordinary week iteration as *not* an offence, so this
    # narrowing cannot be quietly undone.
    #
    # **The third alternative is new in Story 3.3, and it is the one that
    # catches the real thing.** Narrowing the window half showed that neither
    # `PHASE_HOME` entry had ever matched on its own merits: `schedule.py` was
    # caught by the prose "`Math.max(4, Math.min(weeks, 20))`" in a docstring,
    # not by `SCHEDULE_WEEKS` — the actual second table keyed by
    # `RecoveryWindow` that 3.2 argued for at length. A guard that would have
    # missed the very thing its entry describes is not a guard. Matching a
    # `RecoveryWindow`-keyed mapping to a number is what a second phase rule
    # *is*, so both homes now trip it for the reason their comments give.
    r"(?:0\.3|0\.7)\s*[<>]|[<>]\s*(?:0\.3|0\.7)"
    r"|weeks[^\n]{0,20}(?:\\s|\\d|re\.|match|search|compile)"
    r"|(?:re\.|match|search|compile|\\d\+|\\s\*)[^\n]{0,20}weeks"
    r"|RecoveryWindow[^\n]{0,20}\bint\b",
    re.IGNORECASE,
)

COORDINATION_RULE_READ = re.compile(
    r"need_for_additional_information|incomplete_information",
)

# Where each rule is allowed to be named. `tests/` is exempt for
# `test_derivations.py`'s reason — an oracle has to be allowed to restate a
# rule in order to disagree with it — and so are the two places that *define*
# the vocabulary rather than apply it: the enum, and the migration that
# creates the database type from it. Naming a member is not restating a rule;
# deciding that those two members mean "awaiting information" is.
PHASE_HOME = frozenset(
    {
        "services/derivations/treatment_progress.py",
        # `schedule.py` is **a real second table keyed by `RecoveryWindow`, and
        # deliberately not a second phase rule** (Story 3.2). `SCHEDULE_WEEKS`
        # answers "how many weekly indemnity payments does this claim have
        # scheduled"; `EXPECTED_WEEKS` answers "how long was this claim
        # expected to take". They read one column to decide two different
        # things and their numbers differ (a 0-2 week window is 2 expected
        # weeks and 4 scheduled ones, after the schedule's clamp), which is why
        # folding either into the other would be wrong rather than tidy —
        # `test_payment_projection.py::test_the_schedule_is_not_the_treatment_phases_window`
        # pins that they are two rules on purpose, so this entry does not
        # quietly become permission to grow a third.
        #
        # **`data/models/enums.py` was here and is not any more** (Story 3.3).
        # It was admitted as a *false positive* — `ScheduleWeekStatus` contains
        # the letters `WeekS`, which the old follow-set matched as "weeks"
        # beside a paren — and once that noise was removed from the pattern the
        # entry stopped covering anything. A home that excuses a file the guard
        # no longer accuses is an exemption waiting to hide a real one.
        # `COORDINATION_HOME` still lists the file, on its own argument.
        "services/financials/schedule.py",
    }
)
COORDINATION_HOME = frozenset(
    {
        "services/derivations/care_coordination.py",
        "data/models/enums.py",
        "data/versions/20260810_0003_core_schema.py",
    }
)


def _python_sources() -> list[tuple[str, str]]:
    """Every server module outside `tests/` and the virtualenv, as `[name, source]`.

    Paths are relativised *before* the exclusion is applied. Testing
    `path.parts[0] != "tests"` on an absolute path compares against `"/"` and
    excludes nothing, which would have left the guards' own oracle in the
    scan — a guard that fires on the file asserting it.
    """
    sources = []
    for path in sorted(SERVER_ROOT.rglob("*.py")):
        name = path.relative_to(SERVER_ROOT)
        if ".venv" in name.parts or name.parts[0] == "tests":
            continue
        sources.append((name.as_posix(), path.read_text(encoding="utf-8")))
    return sources


def test_the_rule_scan_reaches_the_files_it_claims_to() -> None:
    """The quiet failure both guards below would otherwise have.

    An exclusion that over-matches leaves them asserting over an empty list,
    and the green tick reads as coverage. Naming the two homes proves the
    scan descends into `services/` at all.
    """
    scanned = {name for name, _source in _python_sources()}

    assert "services/derivations/treatment_progress.py" in scanned
    assert "services/derivations/care_coordination.py" in scanned
    assert not any(name.startswith("tests/") for name in scanned)


def test_no_module_outside_the_derivation_restates_the_phase_rule() -> None:
    """`test_derivations.py`'s threshold grep, one derivation over.

    Blunt on purpose: the fix is either to delete the arithmetic or to add an
    entry to `PHASE_HOME`, which is a choice a reviewer sees rather than
    infers. The alternative — a second `pct < 0.3` in a dashboard aggregate —
    is invisible until two screens disagree about a claim.
    """
    offenders = [
        name
        for name, source in _python_sources()
        if name not in PHASE_HOME and PHASE_RULE_READ.search(source)
    ]

    assert offenders == []


def test_no_module_outside_the_derivation_restates_the_coordination_rule() -> None:
    offenders = [
        name
        for name, source in _python_sources()
        if name not in COORDINATION_HOME and COORDINATION_RULE_READ.search(source)
    ]

    assert offenders == []


def test_the_guards_would_notice_a_second_implementation() -> None:
    """A guard that only ever reads clean files cannot tell "nothing is
    wrong" from "nothing is checked"."""
    assert PHASE_RULE_READ.search("if ratio < 0.3: return 'early'")
    assert PHASE_RULE_READ.search('re.search(r"(\\d+)-(\\d+) Weeks", claim.recovery)')
    assert PHASE_RULE_READ.search('WINDOW = re.compile(r"(\\d+)\\s*weeks")')
    # A regex whose metacharacter comes *after* the word — the half of the
    # window pattern that survived Story 3.3's narrowing, kept honest here.
    assert PHASE_RULE_READ.search('TAIL = r"Weeks\\s*$"')
    # The shape a second phase rule actually takes, and the one the guard was
    # missing until Story 3.3: another duration table keyed by the window.
    assert PHASE_RULE_READ.search("PHASE_WEEKS: dict[RecoveryWindow, int] = {...}")
    assert COORDINATION_RULE_READ.search(
        'if claim.comm_status == "need_for_additional_information": ...'
    )
    assert COORDINATION_RULE_READ.search("statuses = {CommStatus.incomplete_information}")


def test_the_phase_guard_does_not_fire_on_ordinary_week_iteration() -> None:
    """The other half of a guard's honesty: what it must *not* call an offence.

    Story 3.3 persists a claim's schedule, so summing and filtering a list of
    weeks is now ordinary code in four modules. While the follow-set contained
    `)` and `]`, every one of these lines was an "offence" — and the cheap fix
    would have been five `PHASE_HOME` entries, which is the guard being
    switched off over the package it most needs to watch.

    Pinned as a test rather than left to the scan above, because the scan only
    fails when somebody *writes* such a line; this fails the moment somebody
    re-broadens the pattern, which is when the decision is actually being made.
    """
    assert not PHASE_RULE_READ.search("return sum(w.amount_cents for w in weeks)")
    assert not PHASE_RULE_READ.search("plan = plan_materialization(projection.weeks, rows)")
    assert not PHASE_RULE_READ.search("status: Mapped[ScheduleWeekStatus] = mapped_column(")
    assert not PHASE_RULE_READ.search("installments = derivation.of(weeks)")


def test_the_guards_leave_ordinary_code_alone() -> None:
    """The other half of a blunt check: a guard that over-matches collects
    allowlist entries until it means nothing."""
    for innocent in [
        "ratio = days_open / expected_days",
        # Prose about weeks is not a second parser. Story 3.1 is full of it.
        "# TTD is capped at 104 weeks in this jurisdiction.",
        '"""Weekly indemnity, paid for up to 104 weeks."""',
        "if severity_score >= self.high_min:",
        "comm_status: Mapped[CommStatus]",
        "return CoordinationStatus.on_track",
    ]:
        assert not PHASE_RULE_READ.search(innocent), innocent
        assert not COORDINATION_RULE_READ.search(innocent), innocent


# --- the money derivations (Story 2.2) ----------------------------------


class Paid:
    """A stand-in for the three paid columns — see `PaidColumns`."""

    def __init__(self, indemnity: int, medical: int, expense: int) -> None:
        self.paid_indemnity = indemnity
        self.paid_medical = medical
        self.paid_expense = expense


def total_paid() -> derivations.TotalPaidDerivation:
    return derivations.total_paid.for_thresholds(SEEDED_THRESHOLDS)


def split() -> derivations.CostSplitDerivation:
    return derivations.cost_split.for_thresholds(SEEDED_THRESHOLDS)


def test_total_paid_adds_all_three_columns() -> None:
    """The expense column is the one an inline sum forgets — it is absent
    from the investigation card's legend and present in its total."""
    assert total_paid().of(Paid(400_000, 550_000, 50_000)) == 1_000_000


def test_a_claim_with_no_payments_has_no_cost_split() -> None:
    """`None`, not three zeros: a claim with nothing paid has no
    composition, and a bar of three zero-width segments is a drawing of a
    fact the sentence beside it states better."""
    assert split().of(Paid(0, 0, 0)) is None


def test_the_cost_split_shares_always_total_exactly_a_hundred() -> None:
    """The bar is three widths in a fixed track.

    The prototype rounds each share independently with `toFixed(0)`; on
    thirds that totals 99, and on the case below it totals 101. Rounding two
    and giving the third the remainder is what makes the bar drawable.
    """
    thirds = split().of(Paid(1, 1, 1))
    assert thirds is not None
    assert thirds.indemnity_pct + thirds.medical_pct + thirds.expense_pct == 100


@given(
    indemnity=st.integers(min_value=0, max_value=10**12),
    medical=st.integers(min_value=0, max_value=10**12),
    expense=st.integers(min_value=0, max_value=10**12),
)
def test_the_split_is_total_and_always_sums_to_a_hundred(
    indemnity: int, medical: int, expense: int
) -> None:
    result = split().of(Paid(indemnity, medical, expense))

    if indemnity + medical + expense == 0:
        assert result is None
        return
    assert result is not None
    assert result.indemnity_pct + result.medical_pct + result.expense_pct == 100
    # The remainder share absorbs the rounding, so it can be one off in
    # either direction — but never nonsensical.
    assert -1 <= result.expense_pct <= 101


# --- days to settlement -------------------------------------------------


def settlement_duration() -> derivations.SettlementDurationDerivation:
    return derivations.days_to_settlement.for_thresholds(SEEDED_THRESHOLDS)


class Settled:
    """A stand-in for the two columns — see `SettlementColumns`."""

    def __init__(self, settlement_days: int | None, froi_date: date) -> None:
        self.settlement_days = settlement_days
        self.froi_date = froi_date


def test_a_recorded_settlement_duration_wins() -> None:
    assert settlement_duration().of(Settled(212, date(2026, 1, 1)), date(2026, 12, 31)) == 212


def test_an_unrecorded_one_falls_back_to_the_claims_age() -> None:
    """The prototype's `c.settlementDays || c.daysOpen`. A settled claim
    with no recorded duration still has an age, and an em dash would be a
    worse answer than the number of days it has been open."""
    assert settlement_duration().of(Settled(None, date(2026, 1, 1)), date(2026, 1, 31)) == 30


def test_a_recorded_zero_is_not_treated_as_absent() -> None:
    """The bug `||` has and `is not None` does not: JavaScript's falsy zero
    would send a same-day settlement through the fallback and report the
    claim's age instead of the 0 the data records."""
    assert settlement_duration().of(Settled(0, date(2026, 1, 1)), date(2026, 6, 1)) == 0
