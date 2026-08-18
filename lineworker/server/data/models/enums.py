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


class ActionKey(StrEnum):
    """The eleven trigger rules of the action checklist (Story 3.5, AC 1).

    **In the data layer although no column holds one**, and the reason is
    neither `BodyRegion`'s nor `ScheduleWeekStatus`'s. Those two are here
    because a native enum column needs its members below `services/`; these
    three action enums are here because the **rules tier** needs them and
    `rules/` must not import from `services/` (the direction
    `rules/parameters.py` states and `_COMP_RATE_MIN_BP` restates the hard way
    by copying a bound rather than importing it). `worklist_actions` carries one
    urgency per rule, and `WorklistActions.of` resolves each member by name at
    load time — the `_status_set` discipline, which turns a typo in a rule
    document into one refusal naming the value instead of a chip that silently
    stops rendering. `data/models/enums.py` is the only vocabulary module below
    both tiers, so it is where a vocabulary two tiers share has to live.

    `RiskBand` and `TreatmentPhase` stay in `services/derivations` precisely
    because nothing *outside* that package needs to name their members.

    **Member order is the rules' declaration order, and it is a contract.**
    `services/worklist/actions.py` evaluates the rules in this order and ranks
    on `(urgency, this index)`, so the list a claim produces is totally ordered
    rather than merely sorted — Story 5.4 reads element 0 as the claim's "next
    best action", and a tie broken by dict iteration is a top action that moves
    between releases with nothing anywhere saying so.

    `routine_review` is the eleventh and is the padding rule: it is what brings
    a quiet claim's list up to the floor, and it is a rule rather than a
    filler string because it too can decline to fire (a settled claim with no
    documents and no schedule produces the empty state instead).
    """

    assessment_approval = "assessment_approval"
    siu_escalation = "siu_escalation"
    overdue_rtw = "overdue_rtw"
    surgical_pre_auth = "surgical_pre_auth"
    bill_review = "bill_review"
    payment_confirmation = "payment_confirmation"
    osha_log = "osha_log"
    defense_counsel = "defense_counsel"
    modified_duty = "modified_duty"
    diary_check_in = "diary_check_in"
    routine_review = "routine_review"


class ActionUrgency(StrEnum):
    """How loudly one checklist row speaks (Story 3.5, AC 1).

    The prototype's `acti-tag` chips are two-tone — `high` and `med` — and this
    is that scheme with a third value, because the padding rows are a different
    kind of thing from the rules: "read the case file when you have a moment"
    and "this claim's OSHA 300 entry is unfiled" rendering at one volume is a
    worklist that has stopped distinguishing.

    Read as: `high` — a statutory or lifecycle clock is running; `medium` —
    somebody is waiting on this handler; `low` — routine.

    **Member order is rank order**, most urgent first, which is what
    `services/worklist/actions.py` sorts on and what PostgreSQL would sort the
    type in if a column ever held one. The *values* per rule are a rule
    document's answer (`worklist_actions`), never a Python literal — AD-8.

    `medium`, not the prototype's `med`, although `RiskBand` uses the short
    form. These are different vocabularies with different consumers and the
    long form is the one a reader of a JSON payload does not have to expand;
    `RiskBand`'s abbreviation exists because it is drawn inside a 68px gauge.
    """

    high = "high"
    medium = "medium"
    low = "low"


class ActionTarget(StrEnum):
    """Where a checklist row's "go to" control takes a handler (Story 3.5, AC 3).

    The prototype's `handleActionGoto` kinds (lines 904-912), as a closed type.
    Three of them are tabs this console already has, four are surfaces later
    epics own, and one is not a place at all:

    - `overview`, `bills`, `documents` — tabs Epic 2 and Story 3.3 built. The
      control switches tabs within the detail pane; there is no bespoke routing
      and no second notion of "where am I" (Story 2.2's tab state is local UI
      state, and this reuses it).
    - `diary`, `meetings` — Epic 4. `fraud` — Epic 6's AI Insights.
      `rtw_letter` — Epic 6's RTW-letter modal (FR-H-11).
    - `approve` — the assessment approval, which is a *command* rather than a
      destination. The prototype models it the same way, and keeping it in this
      enum is what lets the card render one control per row.

    **This enum is the cross-epic seam, and it is why `enabled` is a server
    field rather than a client-side membership test.** Story 4.2 enables the
    diary link and Story 6.2 the fraud one; both are a change to
    `services/worklist/actions.py`'s target table and to nothing in the SPA. A
    browser that decided which targets exist would be a second copy of that
    table, and it would go stale in the release where one of them shipped.
    """

    overview = "overview"
    bills = "bills"
    documents = "documents"
    diary = "diary"
    meetings = "meetings"
    fraud = "fraud"
    rtw_letter = "rtw_letter"
    approve = "approve"


class ActionCommand(StrEnum):
    """The completion an action offers, when it offers one (Story 3.5, AC 5).

    A checklist row can carry a second control beside its "go to": the audited
    write that makes the row stop firing. Which write — and whether there is one
    at all — is the **server's** answer, published on the action, for
    `ScheduleWeekView.approvable`'s reason: a browser deciding that a document
    is ready to confirm would offer a button the command refuses, and a 409 a
    handler cannot act on is worse than no button.

    The two document members are one command with two states rather than a
    toggle. `mark_document_reviewed` is offered while the document is unread;
    `confirm_document` replaces it once it has been reviewed, which is the
    prototype's own sequencing (`confirmDocument` returns early when
    `!st.reviewed`) and is enforced server-side as a status guard rather than
    only as a disabled button.

    There is deliberately no member for a payment confirmation: Story 3.4 owns
    that command and its ✓ lives in the Bills tab's sheet, so the payment action
    deep-links there rather than growing a second approval surface with its own
    version handling.
    """

    approve_assessment = "approve_assessment"
    mark_document_reviewed = "mark_document_reviewed"
    confirm_document = "confirm_document"
    mark_osha_logged = "mark_osha_logged"


class MeetingType(StrEnum):
    """The ten kinds of stakeholder meeting a handler can schedule (Story 4.1).

    The scheduler modal's `<select>` (prototype lines 541-551), as a closed
    type. A native enum column holds one, so the vocabulary lives here for
    `BodyRegion`'s reason — `data/` must not import from `services/` — and the
    display strings stay the browser's: "3-Point Contact — Initial" carries an
    em dash and a numeral that no wire token should have to encode.

    **The seed's two demo meetings are not among these ten, and that is a
    documented mapping rather than a gap.** The prototype's
    `seedMeetingsIfEmpty` (line 1840) writes free-text titles — "RTW Check-In
    Call" and "Case Review — Reserve & Treatment Plan" — which its scheduler
    could never have produced, because the scheduler only offers this list.
    Since `meeting_type` is an enum, migration 0033 seeds them as
    `rtw_conference` and `claim_review_supervisor` and carries the prototype's
    fuller wording in `notes`. Story 4.1's Dev Notes flag the discrepancy; a
    free-text type would be a schema change request, not dev discretion.

    Member order is the modal's option order, which is roughly the order a
    claim encounters them — first contact, return to work, care coordination,
    then the escalations — and it is the order PostgreSQL sorts the type in.
    `other` is last because it is the catch-all rather than a stage.
    [Source: docs/Workers_Comp_Prototype.html lines 541-551]
    """

    three_point_contact_initial = "three_point_contact_initial"
    rtw_conference = "rtw_conference"
    ncm_care_coordination = "ncm_care_coordination"
    ime_preparation = "ime_preparation"
    settlement_discussion = "settlement_discussion"
    physician_consultation = "physician_consultation"
    employer_accommodation_review = "employer_accommodation_review"
    litigation_prep = "litigation_prep"
    claim_review_supervisor = "claim_review_supervisor"
    other = "other"


class MeetingParticipant(StrEnum):
    """The six stakeholder roles a meeting can be scheduled with (Story 4.1).

    The modal's checkbox grid (prototype lines 555-562). **Six values in a
    JSONB array on `meeting`, not a join table**, which is the story's own
    ruling and worth restating where the vocabulary lives: nothing in Epics 1-8
    asks a participant-side question ("which meetings is the NCM on?"), so a
    child table would buy a join and a second write path for a list that is
    always read whole with its meeting.

    That JSONB column is why this is still a `StrEnum` rather than a tuple of
    strings: the members are what the command validates against and what the
    409's fresh entity round-trips, so an unknown participant is refused at the
    boundary even though no database type constrains the array's elements.

    Labels are the UI's, and two of them are deliberately longer than their
    tokens — `ncm` renders "Nurse Case Manager" in the modal and "NCM" on a
    card's participant tag, and `supervisor` renders "My Supervisor". A token
    that carried either wording would make the tag and the checkbox two
    different vocabularies.

    **Since Story 4.3 this is the shared stakeholder vocabulary, and the name
    is now too narrow for what it names.** `email_log.recipients` holds these
    same six values, because the story's own ruling is "one shared 6-value set
    with 4.1's participant enum" and because convert-to-email has to map a
    meeting's participants onto an email's recipients — with two enums that
    mapping is a translation table, and a translation table between two lists
    of identical strings is how they start to drift. The prototype's email modal
    keys them `employer`/`physician` where the meeting modal says
    `employer_hr`/`treating_physician`; those are the same six roles and the
    longer tokens win. A rename to `Stakeholder` would be the honest fix and it
    is deliberately not made here — it would touch this enum, `meetings.py`,
    the router, the generated `schema.d.ts` type name and every test that
    spells it, inside a story already shipping two tables and five endpoints.
    Recorded in `deferred-work.md`.

    Member order is the checkbox grid's order, reading left to right — the same
    order in both modals, which is what makes the shared vocabulary's stored
    order (`normalise_participants`, `normalise_recipients`) one order.
    [Source: docs/Workers_Comp_Prototype.html lines 555-562, 588-593]
    """

    employee = "employee"
    employer_hr = "employer_hr"
    ncm = "ncm"
    treating_physician = "treating_physician"
    supervisor = "supervisor"
    attorney = "attorney"


class EmailPriority(StrEnum):
    """How a logged stakeholder email is flagged (Story 4.3).

    The composer's `<select>`, as a closed type. A native enum column holds one,
    so the vocabulary lives here for `BodyRegion`'s reason — `data/` must not
    import from `services/` — and the display strings stay the browser's.

    **Three members and no `low`**, which is the prototype's own list rather
    than a truncation of a general priority scale: the select offers Normal,
    High and Urgent, and a fourth value would be a control nothing renders.

    Member order is the select's option order, which is also the order the card
    accent escalates in (none → warn → error) and the order PostgreSQL sorts the
    type in. `normal` is first because it is the default the composer opens on.
    [Source: docs/Workers_Comp_Prototype.html line 612]
    """

    normal = "normal"
    high = "high"
    urgent = "urgent"


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
    """Who an `app_user` row is, and therefore what an audit event's actor was.

    The first three are the prototype's personas and the login picker's whole
    vocabulary. `system` is Story 3.4's and is a different kind of member, so
    the distinction is written down rather than left to be inferred:

    **`system` is an actor, not a persona.** AD-4 fixes the audit schema with a
    non-nullable `actor_id` and an `actor_role`, and the payment batch is a
    scheduled job with no request and no human behind it. Something has to be
    named in `audit_event.actor_id` for every row the batch writes, and the
    three honest alternatives were all worse: attributing thousands of
    disbursements to whichever handler the seed happened to list first is a
    false record of who acted; making `actor_id` nullable rewrites the fixed
    schema AD-4 names to accommodate one caller; and leaving the batch
    unaudited fails AC 3 outright. So the batch has an identity, and its role
    says what kind of identity it is.

    **It can never hold a session.** `list_personas` excludes it from the login
    picker and `get_persona` refuses it outright — both pinned by tests in
    `tests/test_personas.py`, because `POST /auth/login` is a *public* path and
    a system actor that could be selected by id would be a scope-all account
    anyone could assume. That refusal is the security property; the picker
    omission is only tidiness.

    **Every capability gate already refuses it**, without knowing it exists:
    the five commands that gate on a role spell the check `is not
    UserRole.handler`, so a fourth member is refused by construction rather
    than by an allowlist somebody has to remember to narrow.

    Its `scope_all` is `true`, and that is the honest value rather than a
    convenience: the batch disburses across the whole portfolio, so its
    `employer_scope` predicate is `TRUE` because its scope genuinely is
    everything — the AD-7 tautology, not a repository skipping a filter.

    Epic 6's scheduled embedding refresh is the second job that will need an
    actor; it inherits this one rather than minting a second convention.
    """

    handler = "handler"
    supervisor = "supervisor"
    analyst = "analyst"
    system = "system"


#: The roles a human can log in as — `UserRole` minus the machine actors.
#:
#: Declared as the *complement* of the system set rather than as a literal
#: triple, so a fifth persona role added above is admitted automatically while
#: a second machine actor has to be named here to be excluded. The failure this
#: shape prevents is the dangerous direction: a new machine actor silently
#: becoming selectable in a picker that fronts an unauthenticated endpoint.
SYSTEM_ROLES: frozenset[UserRole] = frozenset({UserRole.system})
LOGIN_ROLES: frozenset[UserRole] = frozenset(UserRole) - SYSTEM_ROLES

#: The seeded system actor's name. Referenced by the migration that inserts it
#: and by the batch command that looks it up, so the two cannot drift — and it
#: reads as a machine in an audit log a human is scanning.
SYSTEM_ACTOR_NAME: str = "LINEWORKER Payment Batch"


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
    # Story 3.5's two, and they are two rather than one for the reason
    # `benefit` is not folded into `edit`: a reader scanning a case file for
    # what a handler *completed* is asking a different question from one
    # scanning it for what they corrected. `document` is a document read and
    # accepted (the review→confirm write-back); `compliance` is a regulatory
    # filing recorded — today the OSHA 300 entry, and the tag Epic 8's audit
    # review will filter on when it asks which claims were logged and when.
    #
    # The assessment approval is deliberately **not** here: it is `approval`,
    # the tag the seed already uses for a claim's own lifecycle approval, which
    # is exactly what this command performs. One event per claim, in its
    # lifecycle, is what that tag has always meant.
    document = "document"
    compliance = "compliance"


#: Tags no seeded row carries because they are emitted by a command rather
#: than by the dataset. `tests/test_case_file_seed.py` subtracts these before
#: asserting that the seeded tag set is exactly the enum — which keeps that
#: assertion an equality (it would be vacuous as a subset check) while
#: letting later epics add their own event kinds.
RUNTIME_ONLY_TIMELINE_TAGS: frozenset[TimelineTag] = frozenset(
    {TimelineTag.edit, TimelineTag.benefit, TimelineTag.document, TimelineTag.compliance}
)
