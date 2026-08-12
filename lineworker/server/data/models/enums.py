"""Snake_case enum values per the conventions — the UI owns display labels.

Status values are the dataset's display strings mapped mechanically
(Story 3.5 depends on these exact tokens).
"""

from enum import StrEnum


class Stage(StrEnum):
    intake = "intake"
    investigation = "investigation"
    treatment = "treatment"
    settled = "settled"


class ClaimStatus(StrEnum):
    initial = "initial"
    ch_assessment_process = "ch_assessment_process"
    ch_approved = "ch_approved"
    denied = "denied"
    settled = "settled"
    settled_closed = "settled_closed"


class CommStatus(StrEnum):
    fnol_received = "fnol_received"
    incomplete_information = "incomplete_information"
    need_for_additional_information = "need_for_additional_information"
    documents_received_and_approved = "documents_received_and_approved"
    initial_approval_provided_treatment_underway = "initial_approval_provided_treatment_underway"


class ReturnStatus(StrEnum):
    under_treatment = "under_treatment"
    returned_and_under_therapy = "returned_and_under_therapy"
    returned_and_fully_recovered = "returned_and_fully_recovered"


class Disability(StrEnum):
    temporary = "temporary"
    permanent = "permanent"


class RecoveryWindow(StrEnum):
    """How long the claim is expected to take — the prototype's five options.

    **Snake_case tokens, not the display strings** (Story 2.3, code review).
    Story 1.2 seeded this column as the dataset's own free text (`"4-6 Weeks"`,
    `"Greater than 1 Year"`) because at that point it *was* free text: nothing
    constrained it and nothing but a regex read it. Story 2.3 is what closed
    it — the inline-edit command refuses anything outside these five — so this
    is the point at which the Enums convention attaches ("enum values
    snake_case lowercase in DB/API; UI owns display labels").

    The change is not cosmetic. While the stored value *was* the display
    string, `services/derivations/treatment_progress.py` had to recover the
    expected duration by running a regex over prose — which meant a reworded
    label silently changed a derived value, and a lowercase `w` fell through
    to a default window. With tokens, the duration is a lookup keyed by a
    member, the label is the browser's to reword, and neither can move the
    other.

    Member names encode the bound rather than the wording, so `weeks_6_8` and
    a future re-labelling of it to "6 to 8 weeks" are the same value.
    """

    weeks_0_2 = "weeks_0_2"
    weeks_2_4 = "weeks_2_4"
    weeks_4_6 = "weeks_4_6"
    weeks_6_8 = "weeks_6_8"
    over_1_year = "over_1_year"


class BodyRegion(StrEnum):
    """The eleven regions the injury diagram can point at (Story 2.4).

    **The vocabulary moved here; the labels stayed put.** Story 2.3 wrote
    these eleven keys down in `services/claims/reference.py` as a tuple of
    `(key, label)` pairs and argued — correctly — that they are a vocabulary
    rather than a rule, so they do not belong in the JDM tier. Story 2.4 adds
    a *column* that holds one (`additional_injury.body_key`), and a native
    enum column needs its members in the data layer: `data/` must not import
    from `services/`. So the keys live here and `reference.py` now hangs its
    display labels off them, which keeps the single source the 2.3 Dev Notes
    asked for and makes the direction of the dependency the right way round.

    Member order is the prototype's `BODY_PART_OPTIONS` order — the order the
    body-part select renders and the order the diagram's summary list reads.
    [Source: docs/Workers_Comp_Prototype.html lines 648-654]

    **`claim.body_key` is still `Text`, and that asymmetry is deliberate.**
    Converting it is a migration over a column Stories 1.2 and 2.3 own, and
    2.4's task list scopes the enum to the table it creates. The vocabulary
    is single-sourced either way — `services/claims/edit.py` validates the
    claim's key against these same eleven members — so what differs is only
    where the refusal comes from. Recorded in `deferred-work.md`.
    """

    head = "head"
    ears = "ears"
    shoulder_right = "shoulder_right"
    shoulder_left = "shoulder_left"
    forearm_right = "forearm_right"
    hand_right = "hand_right"
    hand_left = "hand_left"
    torso = "torso"
    lumbar = "lumbar"
    tibia_left = "tibia_left"
    tibia_right = "tibia_right"


class ClaimPath(StrEnum):
    """The three statutory handling paths a claim can be classified onto (2.5).

    The prototype has this vocabulary and never uses it: `pathDocsHTML` reads
    `c.path || "B"` against a dataset in which **no claim carries a `path`
    field at all**, so all 100 claims render the Path B form set. Story 2.5
    closes that gap with a registered derivation (`services/derivations/
    path_classification.py`), and this is the vocabulary it answers in.

    Here rather than beside the derivation because `path_required_form.path`
    is a native enum column, and a column's members must live in the data
    layer (`data/` must not import from `services/`) — the same argument
    `BodyRegion` makes. `RiskBand` stays in `services/derivations` precisely
    because no column holds one.

    Snake_case wire tokens (`a`/`b`/`c`), never the prototype's display keys:
    the label ("Path A — Minor Injury"), the icon and the banner colour are
    UI-owned per the Enums convention. Member order is the prototype's, which
    is also increasing severity, and it is the order PostgreSQL will sort the
    type in.
    [Source: docs/Workers_Comp_Prototype.html lines 1542-1563]
    """

    a = "a"
    b = "b"
    c = "c"


class Gender(StrEnum):
    female = "female"
    male = "male"
    other = "other"


class UserRole(StrEnum):
    handler = "handler"
    supervisor = "supervisor"
    analyst = "analyst"


class DocType(StrEnum):
    """The prototype's document classes (Story 2.2).

    A native database enum, unlike `TimelineTag` below: the set is closed by
    what a claim file *is* — a first report, an incident investigation, a
    medical authorization, a wage statement, a return-to-work letter, a legal
    filing — and Story 2.5 maps every member to a UI chip and a viewer
    layout, so a seventh member is a change to that story's surface and not
    something a later migration should be able to introduce quietly.
    """

    froi = "froi"
    incident = "incident"
    medauth = "medauth"
    wage = "wage"
    rtw = "rtw"
    legal = "legal"


class TimelineTag(StrEnum):
    """The tags the case timeline is seeded with, and the ones code reads.

    **Deliberately not a database enum**, which is the one place this story
    departs from the "snake_case enums" convention. `timeline_event` is an
    append-only log that every later epic writes into — 2.3's field edits,
    Epic 3's payment approvals, Epic 4's meetings and diary notes, Epic 6's
    generated letters — so a native enum would make each of those stories
    carry an `ALTER TYPE … ADD VALUE` migration whose only purpose is to
    widen a display label. That is migration tax without a matching risk:
    the tag is rendered as a chip, and exactly one rule reads it (the settled
    overview looking for the settlement event).

    This enum is where that one rule gets its typing. `tag` on the column is
    `Text`; code compares against these members rather than bare strings, and
    `tests/test_case_file_seed.py` asserts the seeded set is exactly this set
    **minus the runtime-only members below** — so a tag the prototype writes
    and nothing here names still fails loudly, in a test, rather than by a
    branch that silently stops matching.
    """

    intake = "intake"
    froi = "froi"
    assignment = "assignment"
    approval = "approval"
    medical = "medical"
    rtw = "rtw"
    litigation = "litigation"
    settlement = "settlement"
    # Story 2.3, and the first member the *seed* does not contain: an audited
    # inline edit appends one of these at runtime. The distinction is real
    # enough to be named rather than left for a reader to infer from a
    # failing test — see `RUNTIME_ONLY_TIMELINE_TAGS`.
    edit = "edit"


#: Tags no seeded row carries because they are emitted by a command rather
#: than by the dataset. `tests/test_case_file_seed.py` subtracts these before
#: asserting that the seeded tag set is exactly the enum — which keeps that
#: assertion an equality (it would be vacuous as a subset check) while
#: letting later epics add their own event kinds.
RUNTIME_ONLY_TIMELINE_TAGS: frozenset[TimelineTag] = frozenset({TimelineTag.edit})
