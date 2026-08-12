"""Story 2.4's pure half — what the three commands accept and refuse.

No database. The transactional half (compare-and-swap, the audit and
timeline rows landing together, the endpoint's answers) is
`tests/test_injury_commands.py`; what is left is everything that is true of a
*value*: the severity domain, the body-part vocabulary, the injury-type text
rules, and the four places the diagram's key list is written down.

The banding property at the end is the story's Hypothesis requirement: for
every score in the domain, the band a marker is drawn in is the band the
queue dot and the header gauge use — because there is exactly one function
that answers it (AD-10), and this asserts that answering it twice cannot
give two answers.
"""

import importlib.util
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from data.models.enums import BodyRegion
from rules.parameters import InjuryCapture, RuleParameterError
from services import derivations
from services.claims.detail import InjuryMarker
from services.claims.edit import InvalidPatch, normalise_severity
from services.claims.injuries import normalise_new_injury
from services.claims.reference import (
    BODY_PART_LABELS,
    BODY_PART_OPTIONS,
    SEVERITY_MAX,
    SEVERITY_MIN,
)
from services.derivations import RiskBand
from tests.test_derivations import SEEDED_THRESHOLDS

SERVER_ROOT = Path(__file__).resolve().parents[1]

_MIGRATION = SERVER_ROOT / "data" / "versions" / "20260812_0014_injury_diagram_tables.py"
_spec = importlib.util.spec_from_file_location("_m0014", _MIGRATION)
assert _spec and _spec.loader
_m0014 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m0014)

MIGRATION_BODY_KEYS: tuple[str, ...] = _m0014.BODY_KEYS

PROTOTYPE = SERVER_ROOT.parents[1] / "docs" / "Workers_Comp_Prototype.html"

#: `{key:"head",label:"Head"}` — the prototype's option list, read rather than
#: restated so a key it spells differently fails here.
_KEY_PATTERN = re.compile(r'key:"([a-z_]+)"')


# --- the vocabulary, in its four homes -----------------------------------


def test_the_enum_the_labels_and_the_migration_name_the_same_eleven_regions() -> None:
    """`BodyRegion`, `BODY_PART_OPTIONS` and migration 0014, compared.

    Three statements of one list, and they are separate on purpose: the enum
    is what the column can hold, the options are what a select offers, and
    the migration is a frozen historical record that must not import either
    (a migration that read a live constant would silently change what it did
    when somebody reordered that constant). Separate statements need a test
    that they agree, and this is it.
    """
    members = tuple(member.value for member in BodyRegion)

    assert tuple(option.key.value for option in BODY_PART_OPTIONS) == members
    assert tuple(BODY_PART_LABELS) == members
    # Order too, not just membership: the migration's tuple decides the
    # enum's *label order* in PostgreSQL, which is what `ORDER BY body_key`
    # would sort by.
    assert members == MIGRATION_BODY_KEYS
    assert len(members) == 11


def test_the_regions_are_the_prototypes() -> None:
    """The eleven keys are the prototype's `BODY_PART_OPTIONS`, not ours.

    Story 2.3 asserts the same thing against the labels; this asserts it
    against the *enum*, which is the copy that reached the database in 2.4.
    Read out of the HTML rather than restated, so a key the prototype spells
    differently fails here instead of on a body map with a missing marker.
    """
    source = PROTOTYPE.read_text(encoding="utf-8")
    start = source.index("const BODY_PART_OPTIONS=")
    block = source[start : source.index("const RECOVERY_OPTIONS")]
    keys = tuple(json.loads(f'"{key}"') for key in _KEY_PATTERN.findall(block))

    assert keys == tuple(member.value for member in BodyRegion)


# --- the severity domain -------------------------------------------------


@pytest.mark.parametrize("score", [SEVERITY_MIN, 1, 40, 99, SEVERITY_MAX])
def test_a_score_inside_the_domain_is_accepted(score: int) -> None:
    assert normalise_severity(score) == score


@pytest.mark.parametrize("score", [-1, SEVERITY_MAX + 1, 1000, -1000])
def test_a_score_outside_the_domain_is_refused_never_clamped(score: int) -> None:
    """The prototype's `Math.max(0, Math.min(100, n))` is not ported.

    Clamping turns a typo of `780` into a maximum-severity claim silently —
    a value nobody entered, in the column that decides the claim's risk band
    and its position in the queue. AC 4 asks for a refusal, and a refusal is
    also the only answer that lets the handler see what happened.
    """
    with pytest.raises(InvalidPatch) as refusal:
        normalise_severity(score)
    assert "between" in str(refusal.value)


@pytest.mark.parametrize("value", [1.5, "50", None, [50], True, False])
def test_a_score_that_is_not_a_whole_number_is_refused(value: Any) -> None:
    """`True` is in the list on purpose.

    It is an `int` in Python, so a caller sending JSON `true` would record a
    severity score of **1** — a real edit, made by nobody, in the column the
    risk band is computed from. `isinstance(raw, bool)` before
    `isinstance(raw, int)` is what stops it, and this is the test that would
    notice if the two were ever reordered.
    """
    with pytest.raises(InvalidPatch):
        normalise_severity(value)


# --- the add-injury payload ----------------------------------------------


def test_a_valid_injury_takes_its_label_from_the_key() -> None:
    """The label is derived, never accepted (the prototype's `updateBodyPart`).

    A caller that could supply its own label could file "Left Hand" against
    `head` — a body map whose marker and whose summary row disagree about
    where the injury is.
    """
    injury = normalise_new_injury(BodyRegion.hand_left, "  Laceration  ", 40)

    assert injury.body_key == "hand_left"
    assert injury.body_part == "Left Hand"
    # Trimmed, like every other text field the whitelist takes.
    assert injury.injury_type == "Laceration"
    assert injury.severity_score == 40


@pytest.mark.parametrize("key", ["lower_back", "", "HEAD", "torso ", 7, None])
def test_a_region_outside_the_diagram_is_refused(key: Any) -> None:
    with pytest.raises(InvalidPatch) as refusal:
        normalise_new_injury(key, "Laceration", 40)
    assert "region" in str(refusal.value)


@pytest.mark.parametrize("injury_type", ["", "   ", "\t", None, 7])
def test_an_empty_or_non_text_injury_type_is_refused(injury_type: Any) -> None:
    """The prototype's refusal here is a silent `typeEl.focus()`.

    Which tells a handler nothing at all, and is the behaviour UX-DR11 and
    NFR-3 replace with a sentence.
    """
    with pytest.raises(InvalidPatch):
        normalise_new_injury(BodyRegion.torso, injury_type, 40)


def test_an_injury_type_carrying_a_control_character_is_refused() -> None:
    """PostgreSQL `text` cannot hold a NUL and asyncpg raises rather than
    truncating — which reached Story 2.3's PATCH as an unhandled 500 before
    that story's code review. The add command shares the check because it
    shares the function."""
    with pytest.raises(InvalidPatch):
        normalise_new_injury(BodyRegion.torso, "Lacer\x00ation", 40)


def test_an_injury_type_longer_than_the_claim_columns_cap_is_refused() -> None:
    """One cap for one concept.

    `injury_type` on a secondary injury is the same kind of value as
    `injury_type` on the claim, shown in the same list — two different limits
    would be a rule a handler learns by being refused at one of them.
    """
    with pytest.raises(InvalidPatch):
        normalise_new_injury(BodyRegion.torso, "x" * 81, 40)


def test_the_add_command_refuses_a_bad_score_the_same_way_the_patch_does() -> None:
    with pytest.raises(InvalidPatch):
        normalise_new_injury(BodyRegion.torso, "Laceration", SEVERITY_MAX + 1)


def test_no_refusal_message_echoes_what_was_submitted() -> None:
    """AD-11: messages name the field and the rule, never the value.

    The value is distinctive enough that a leak could not be a coincidence.
    Every one of these refusals ends up in a problem document, a structlog
    line and a browser console.
    """
    secret = "Zx9-CLAIMANT-SECRET-Qq"
    for call in (
        lambda: normalise_new_injury(secret, "Laceration", 40),
        lambda: normalise_new_injury(BodyRegion.torso, secret * 10, 40),
        lambda: normalise_severity(secret),
    ):
        with pytest.raises(InvalidPatch) as refusal:
            call()
        assert secret not in str(refusal.value)


# --- the capture parameters ----------------------------------------------


@pytest.mark.parametrize("default", [-1, 101])
def test_a_default_severity_outside_the_domain_is_refused(default: int) -> None:
    """A form whose untouched submit the command refuses is a broken form.

    The parameter is operator-editable (AD-8), so the refusal belongs at the
    boundary where the document is read, not at the first handler to press
    the button.
    """
    with pytest.raises(RuleParameterError):
        InjuryCapture(version=1, new_injury_default_severity=default)


# --- the banding property ------------------------------------------------


def _band_by_the_document(score: int) -> RiskBand:
    """The thresholds, restated. An independent oracle, as everywhere else."""
    if score >= SEEDED_THRESHOLDS.risk_high_min:
        return RiskBand.high
    if score >= SEEDED_THRESHOLDS.risk_med_min:
        return RiskBand.med
    return RiskBand.low


@given(score=st.integers(min_value=SEVERITY_MIN, max_value=SEVERITY_MAX))
def test_every_score_in_the_domain_bands_as_the_document_says(score: int) -> None:
    """The whole 0-100 domain, against the JDM parameters.

    Parametrised tests cover the boundaries; this covers the *domain*, which
    is what makes "a marker's colour and the queue dot's colour are the same
    decision" a property rather than a spot check.
    """
    band = derivations.risk.for_thresholds(SEEDED_THRESHOLDS).of(score)
    assert band is _band_by_the_document(score)


@given(
    primary=st.integers(min_value=SEVERITY_MIN, max_value=SEVERITY_MAX),
    secondary=st.integers(min_value=SEVERITY_MIN, max_value=SEVERITY_MAX),
)
def test_a_marker_is_banded_by_the_same_function_whatever_it_is_a_marker_of(
    primary: int, secondary: int
) -> None:
    """The primary and a secondary with equal scores band identically.

    The prototype does *not* have this property: `injHTML` re-derives every
    marker's colour from a cut-off pair written into the drawing function,
    while the rest of the console uses the rule document's. Two scores that
    straddle the difference render one way on the diagram and another on the
    card beside it.
    """
    band = derivations.risk.for_thresholds(SEEDED_THRESHOLDS)
    if primary == secondary:
        assert band.of(primary) is band.of(secondary)
    # …and the ordering holds regardless: a higher score is never in a
    # *lower* band, which is the invariant a re-derivation would break.
    order = {RiskBand.low: 0, RiskBand.med: 1, RiskBand.high: 2}
    if primary >= secondary:
        assert order[band.of(primary)] >= order[band.of(secondary)]


@given(score=st.integers(min_value=SEVERITY_MIN, max_value=SEVERITY_MAX))
def test_retuning_the_document_moves_every_marker_together(score: int) -> None:
    """AD-8 in one property: the band follows the document, not the code.

    With both cut-offs at zero every score is high; with both above the
    domain every score is low. Neither is a sensible configuration — the
    point is that no score has a band of its own that survives the change,
    which is what a hardcoded pair in a component would produce.
    """
    everything_high = replace(SEEDED_THRESHOLDS, risk_high_min=0, risk_med_min=0)
    everything_low = replace(SEEDED_THRESHOLDS, risk_high_min=100, risk_med_min=100)

    assert derivations.risk.for_thresholds(everything_high).of(score) is RiskBand.high
    assert (
        derivations.risk.for_thresholds(everything_low).of(score) is RiskBand.high
        if score == 100
        else derivations.risk.for_thresholds(everything_low).of(score) is RiskBand.low
    )


def test_a_marker_carries_its_band_rather_than_its_own_thresholds() -> None:
    """The payload shape AC 1 rests on.

    `InjuryMarker` has a `band` field and no threshold anywhere near it, so
    a component rendering one has nothing to compare against. Asserted on
    the dataclass rather than through a request because it is a statement
    about the contract, not about one response.
    """
    fields = InjuryMarker.__dataclass_fields__
    assert "band" in fields
    assert not [name for name in fields if "min" in name or "threshold" in name]
