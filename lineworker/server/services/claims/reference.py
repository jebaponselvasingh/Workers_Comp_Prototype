"""The editable clinical vocabularies — one source, shared with Story 2.4.

The six fields Story 2.3 makes editable are not free text in the prototype:
four of them are picked from a fixed list. Those lists live here rather than
beside the command, because the **same** body-part key set is what Story
2.4's SVG diagram addresses its regions by — the diagram and this select are
two views of one vocabulary, and a second copy is how they come to disagree
about whether the key is `lumbar` or `lower_back`.

**Not a JDM document (AD-8).** The rules tier owns *parameters and decision
tables* — weights, thresholds, bands, caps. None of these is one: "the
eleven regions the body diagram can point at" is a vocabulary, and an
operator retuning a threshold has no business editing it without the SVG
changing in the same commit. `derivation_thresholds` stays the place where
numbers a rule compares against live.

**`body_part` is not `body_key`'s label in the seeded data**, and that is
the prototype's behaviour rather than a defect here. The dataset's own
`body_part` vocabulary ("Wrist(s) & Hand(s)", "Multiple Upper Extremities")
describes the injury; `body_key` names a region on the diagram. The
prototype's `updateBodyPart` overwrites the first with the second's label
the moment a handler picks from the select, so an edited claim's body part
reads "Right Hand" where an untouched one reads "Wrist(s) & Hand(s)". Ported
exactly, and recorded in the story's Dev Agent Record, because the
alternative — inventing a mapping between the two vocabularies — would be
this codebase deciding a clinical question the dataset did not answer.
"""

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from data.models.enums import BodyRegion, RecoveryWindow


@dataclass(frozen=True)
class BodyPartOption:
    """One region of the diagram: the key stored, the label shown.

    A pair with names rather than a bare tuple, because both members are
    strings and `option[0]` at a call site says nothing about which is which
    — and this list crosses the wire, where the field names become the
    contract.
    """

    key: BodyRegion
    label: str


# The prototype's `BODY_PART_OPTIONS`, in its order — the order the select
# renders and the order 2.4's diagram summary list reads.
#
# **The keys are `BodyRegion` members since Story 2.4**, which put the
# vocabulary in `data/models/enums.py` so that `additional_injury.body_key`
# could be a native enum column (a model must not import from `services/`).
# What lives here is what always lived here: the *labels*, and the order.
# Building the tuple from the enum rather than from eleven string literals is
# what keeps "the diagram, the select and the column address the same eleven
# regions" a fact rather than three lists that agree today.
# [Source: docs/Workers_Comp_Prototype.html lines 648-654]
BODY_PART_OPTIONS: Final[tuple[BodyPartOption, ...]] = (
    BodyPartOption(BodyRegion.head, "Head"),
    BodyPartOption(BodyRegion.ears, "Ears"),
    BodyPartOption(BodyRegion.shoulder_right, "Right Shoulder"),
    BodyPartOption(BodyRegion.shoulder_left, "Left Shoulder"),
    BodyPartOption(BodyRegion.forearm_right, "Right Forearm"),
    BodyPartOption(BodyRegion.hand_right, "Right Hand"),
    BodyPartOption(BodyRegion.hand_left, "Left Hand"),
    BodyPartOption(BodyRegion.torso, "Torso"),
    BodyPartOption(BodyRegion.lumbar, "Lower Back (Lumbar)"),
    BodyPartOption(BodyRegion.tibia_left, "Left Lower Leg"),
    BodyPartOption(BodyRegion.tibia_right, "Right Lower Leg"),
)

#: Key → label. **Typed `str` rather than `BodyRegion`, deliberately.** The
#: keys *are* `BodyRegion` members, and `BodyRegion` is a `StrEnum`, so a
#: plain string hashes and compares equal to the member it names — which is
#: what lets `edit.py` and `injuries.py` validate a caller-supplied string
#: against this mapping without converting it first, and what makes
#: `"lower_back" not in BODY_PART_LABELS` the refusal rather than a
#: `KeyError` three lines later. Declaring the key type as the enum would
#: make every one of those call sites a type error for doing the correct
#: thing.
BODY_PART_LABELS: Final[MappingProxyType[str, str]] = MappingProxyType(
    {option.key: option.label for option in BODY_PART_OPTIONS}
)

# The prototype's `RECOVERY_OPTIONS`, in its order — as tokens since the
# Story 2.3 code review. The display strings the prototype shows are the
# browser's now (`web/src/features/claim-detail/labels.ts`); migration 0013
# holds the mapping that converted the seeded column.
# [Source: docs/Workers_Comp_Prototype.html line 655]
RECOVERY_WINDOWS: Final[tuple[RecoveryWindow, ...]] = tuple(RecoveryWindow)

#: `severity_score`'s domain — the claim column's, and the `additional_injury`
#: CHECK constraint's.
#:
#: **Not a JDM parameter** (AD-8). The band *thresholds* are tuning and live
#: in `derivation_thresholds`; the domain of the column is not — moving it
#: would take a migration, a constraint change and a re-seed, so an operator
#: editing a rule document must not be able to widen it.
#:
#: **Published on the case file all the same.** The SPA needs the bounds for
#: a number input and a pre-flight refusal, and the alternative is two
#: literals in a React component — which `noDerivation.test.ts` refuses, and
#: rightly: the browser holding its own copy of a numeric rule is exactly the
#: shape that goes quietly wrong. Served, like the eleven body keys.
#:
#: Here rather than in `edit.py` because `detail.py` publishes them and
#: `edit.py` imports `detail.py`; this module is the one both already read.
SEVERITY_MIN: Final[int] = 0
SEVERITY_MAX: Final[int] = 100

# ICD-10-CM's own shape: a letter (never `U`), two characters of category,
# and an optional dotted extension of up to four. Every one of the 100
# seeded codes matches it (`tests/test_claim_edit_validation.py` asserts
# that, so the pattern cannot quietly become stricter than the data).
#
# Validated rather than left as free text because this is the field a
# mistyped character makes *silently* wrong: `S61.412A` and `S16.412A` are
# both plausible-looking strings, and only one of them is a wrist laceration.
ICD_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-TV-Z][0-9][0-9AB](?:\.[0-9A-TV-Z]{1,4})?$")

# What a handler is told the field is called — used to write the timeline
# event's description ("Injury details updated (injury type, ICD-10)"). The
# UI owns its *own* labels; this one is server-side because the timeline row
# is prose the server writes into the log (AD-12).
FIELD_LABELS: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "injury_type": "injury type",
        "cause": "cause",
        "body_key": "body part",
        "icd": "ICD-10",
        "icd_desc": "ICD-10 description",
        "disability": "disability",
        "recovery": "recovery window",
    }
)
