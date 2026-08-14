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


class ScheduleWeekStatus(StrEnum):
    """Where one week of the indemnity payment schedule stands (Story 3.2).

    **In the data layer although Story 3.2 creates no column**, which is the
    one thing about this enum worth arguing. `payment_schedule_week.status` is
    Story 3.3's migration, and a native enum column needs its members here
    (`data/` must not import from `services/`) — the argument `BodyRegion`
    makes. What lands first is the *projection* that produces the value:
    `services/financials/schedule.py`, which 3.2 needs for the reserve check's
    remaining-indemnity term and which 3.3 must persist through rather than
    write a second generator beside (AD-2). Declaring the vocabulary here now
    means 3.3 adds a column over an existing type instead of renaming one out
    of `services/` on its way past.

    `RiskBand` and `TreatmentPhase` stay in `services/derivations` precisely
    because no column will ever hold one; this is the other case.

    Member order is the prototype's `SCHEDULE_STATUS_LABEL` order, which is
    also the order a week moves through — pending approval, due, upcoming,
    paid. `payment_scheduled` is the state Story 3.4's approval batch moves a
    week into; it is named now for the reason the others are, and nothing in
    3.2 produces it.
    [Source: docs/Workers_Comp_Prototype.html line 844]
    """

    pending_approval = "pending_approval"
    due_this_week = "due_this_week"
    upcoming = "upcoming"
    payment_scheduled = "payment_scheduled"
    paid = "paid"


class LineItemStatus(StrEnum):
    """Where one medical bill or claim expense stands (Story 3.3).

    **One vocabulary for both tables**, which is the decision worth stating.
    `bill` and `expense` are separate tables because their *categories* are
    different vocabularies — a surgical facility fee and a mileage
    reimbursement are not members of one list — but the states a line item
    moves through are identical, and the prototype says so by rendering both
    through one `billStatusLabel`. Two enums holding the same five tokens
    would be two places to widen when 3.4's approval adds a transition, and
    the first divergence between them would be a bug nothing could name.

    **`pending_submission` and `payment_scheduled` are distinct members with
    distinct labels, and the prototype conflates them.** Its
    `BILL_STATUS_LABEL` maps `PendingSubmission` to the string "Payment
    Scheduled" (line 806) — so a bill nobody has submitted reads, on screen,
    as money already queued for disbursement. Those are opposite facts about
    a claim's cost. The console keeps them apart: `pending_submission` is a
    bill the provider has not filed, `payment_scheduled` is one a handler has
    approved into the next batch, and Story 3.4's approval is the single
    transition `under_review` → `payment_scheduled` that connects them. The
    story's Dev Notes name this as an enum-label quirk not to copy.

    Member order is the order a line item moves through, which is also the
    order PostgreSQL sorts the type in.
    [Source: docs/Workers_Comp_Prototype.html lines 806, 843-845]
    """

    pending_submission = "pending_submission"
    under_review = "under_review"
    payment_scheduled = "payment_scheduled"
    paid = "paid"


class BillCategory(StrEnum):
    """What kind of medical bill a `bill` row is (Story 3.3, AC 3).

    The prototype has no category field: `buildBills` (line 766) emits six
    line items identified only by their English labels, and `billsHTML`
    groups nothing. The story asks for rows "by category", so the label's
    *identity* becomes a token and the label stays the label — which is the
    `RecoveryWindow` move one epic later, and it buys the same thing: the
    seeded label is content a later story may reword, while the category is
    what a query groups by and what Epic 7's financial decomposition will
    sum over.

    Member order is the prototype's emission order, which is also roughly
    the order a claim incurs them.
    [Source: docs/Workers_Comp_Prototype.html lines 774-780]
    """

    initial_treatment = "initial_treatment"
    surgery_facility = "surgery_facility"
    imaging = "imaging"
    physical_therapy = "physical_therapy"
    follow_up = "follow_up"
    pharmacy = "pharmacy"


class ExpenseCategory(StrEnum):
    """What kind of claim expense an `expense` row is (Story 3.3, AC 3).

    `BillCategory`'s argument, over `buildExpenses` (line 784). A separate
    enum rather than more members on that one because the two lists answer
    different questions — "what did treating this injury cost" against "what
    did *administering* the claim cost" — and the settled-stage payout
    breakdown adds them as two figures, not as one grouped total.

    Member order is the prototype's emission order.
    [Source: docs/Workers_Comp_Prototype.html lines 793-798]
    """

    mileage_travel = "mileage_travel"
    dme = "dme"
    prosthetic_assistive = "prosthetic_assistive"
    home_workstation_mod = "home_workstation_mod"
    misc = "misc"


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
    # Story 3.1, and the first Epic 3 member: a financial decision on the case
    # file (today, the comp-rate override; 3.4's payment approvals next). Not
    # folded into `edit` because a reader scanning a timeline for what moved a
    # claim's money should be able to filter on a token rather than parse
    # descriptions — which is the one thing `settlement` already proves a tag
    # is good for (`_settlement_date` matches on it).
    benefit = "benefit"


#: Tags no seeded row carries because they are emitted by a command rather
#: than by the dataset. `tests/test_case_file_seed.py` subtracts these before
#: asserting that the seeded tag set is exactly the enum — which keeps that
#: assertion an equality (it would be vacuous as a subset check) while
#: letting later epics add their own event kinds.
RUNTIME_ONLY_TIMELINE_TAGS: frozenset[TimelineTag] = frozenset(
    {TimelineTag.edit, TimelineTag.benefit}
)
