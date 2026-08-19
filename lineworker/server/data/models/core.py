"""Core entities: employer, employee, claim, app_user, user_employer_assignment,
audit_event (Story 1.2), glossary_term (Story 1.6).

Naming is canonical per docs/WC_Feature_Element_Details.xlsx. Money columns
are integer cents (Excel names kept, values in cents). No derived value
(days_open, risk, severity band, totals, flags) is a column — AD-10.
"""

from datetime import date, datetime, time

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from data.models.base import Base
from data.models.enums import (
    BillCategory,
    BodyRegion,
    ClaimPath,
    ClaimStatus,
    CommStatus,
    Disability,
    DocType,
    EmailPriority,
    ExpenseCategory,
    Gender,
    InsightKind,
    LineItemStatus,
    MeetingType,
    RecoveryWindow,
    ReturnStatus,
    ScheduleWeekStatus,
    Stage,
    UserRole,
)


def _enum(e: type, name: str) -> Enum:
    # Store enum *values* (snake_case), not Python member names.
    return Enum(e, name=name, values_callable=lambda x: [m.value for m in x])


class Employer(Base):
    __tablename__ = "employer"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    short_name: Mapped[str] = mapped_column(Text)
    sector: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class Employee(Base):
    __tablename__ = "employee"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    employee_id: Mapped[str] = mapped_column(Text, unique=True)  # business id EMP-*
    name: Mapped[str] = mapped_column(Text)
    gender: Mapped[Gender] = mapped_column(_enum(Gender, "gender"))
    age: Mapped[int] = mapped_column(Integer)
    hire_date: Mapped[date] = mapped_column(Date)
    role: Mapped[str] = mapped_column(Text)  # job title
    dept: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = (UniqueConstraint("name", "role"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    role: Mapped[UserRole] = mapped_column(_enum(UserRole, "user_role"))
    scope_all: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class UserEmployerAssignment(Base):
    __tablename__ = "user_employer_assignment"
    __table_args__ = (UniqueConstraint("user_id", "employer_id"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"))
    employer_id: Mapped[int] = mapped_column(ForeignKey("employer.id"))


class Claim(Base):
    __tablename__ = "claim"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    claim_id: Mapped[str] = mapped_column(Text, unique=True)  # business id WC-nnnn
    policy_num: Mapped[str] = mapped_column(Text)
    employer_id: Mapped[int] = mapped_column(ForeignKey("employer.id"))
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id"))
    handler_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"))

    # Worksite placement (plant determines region in the dataset; both kept
    # as claim scalars per the Excel's claim-level placement)
    plant: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)
    region: Mapped[str] = mapped_column(Text)

    # Timeline dates
    doi: Mapped[date] = mapped_column(Date)
    froi_date: Mapped[date] = mapped_column(Date)
    assign_date: Mapped[date] = mapped_column(Date)
    approval_date: Mapped[date | None] = mapped_column(Date)
    rtw_rec: Mapped[date | None] = mapped_column(Date)
    actual_rtw: Mapped[date | None] = mapped_column(Date)

    # Injury & clinical
    injury_type: Mapped[str] = mapped_column(Text)
    cause: Mapped[str] = mapped_column(Text)
    body_part: Mapped[str] = mapped_column(Text)
    body_key: Mapped[str] = mapped_column(Text)
    icd: Mapped[str] = mapped_column(Text)
    icd_desc: Mapped[str] = mapped_column(Text)
    severity_score: Mapped[int] = mapped_column(Integer)
    disability: Mapped[Disability] = mapped_column(_enum(Disability, "disability"))
    # A native enum since Story 2.3 (was Text, seeded with the dataset's
    # display strings). 2.3's inline-edit command closed the vocabulary to
    # five members, so the column now says so — see `RecoveryWindow`.
    recovery: Mapped[RecoveryWindow] = mapped_column(_enum(RecoveryWindow, "recovery_window"))
    surgery_required: Mapped[bool] = mapped_column(Boolean)
    contraindications: Mapped[str] = mapped_column(Text)

    # Prognosis (Story 2.4). Four short clinical strings the prototype keeps
    # in a nested `prognosis` object; flattened into columns because they are
    # four independent facts about one claim, not a document — and because a
    # JSONB blob would put four values behind one name in every audit diff.
    # Story 1.2 left them in `seed_data.json`'s `deferred` block; migration
    # 0015 is where they land.
    prognosis_mmi: Mapped[str] = mapped_column(Text)
    prognosis_rtw: Mapped[str] = mapped_column(Text)
    prognosis_impairment: Mapped[str] = mapped_column(Text)
    prognosis_litigation: Mapped[str] = mapped_column(Text)

    # Lifecycle & status
    stage: Mapped[Stage] = mapped_column(_enum(Stage, "stage"))
    status: Mapped[ClaimStatus] = mapped_column(_enum(ClaimStatus, "claim_status"))
    comm_status: Mapped[CommStatus] = mapped_column(_enum(CommStatus, "comm_status"))
    return_status: Mapped[ReturnStatus] = mapped_column(_enum(ReturnStatus, "return_status"))

    # Regulatory & risk source fields
    osha_recordable: Mapped[bool] = mapped_column(Boolean)

    # Whether the recordable injury has actually been entered on the OSHA 300
    # log (Story 3.5, AC 5). **Not derivable from `osha_recordable`, which is
    # the whole point of the pair**: one says the injury must be logged, the
    # other that somebody has logged it, and the checklist's `osha_log` action
    # fires on exactly the gap between them. Written only by
    # `services/claims/assessment.py` (AD-12), under a compare-and-swap that
    # additionally requires the claim to be recordable — so the column cannot
    # record a filing for an injury the statute never asked to be filed.
    osha_logged: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    litigation_flag: Mapped[bool] = mapped_column(Boolean)
    attorney_rep: Mapped[bool] = mapped_column(Boolean)
    fraud_score: Mapped[int] = mapped_column(Integer)
    fraud_flag: Mapped[bool] = mapped_column(Boolean)

    # Financials — integer cents (Excel names, cent values)
    paid_indemnity: Mapped[int] = mapped_column(BigInteger)
    paid_medical: Mapped[int] = mapped_column(BigInteger)
    paid_expense: Mapped[int] = mapped_column(BigInteger)
    reserve: Mapped[int] = mapped_column(BigInteger)
    aww: Mapped[int] = mapped_column(BigInteger)

    # The handler's comp-rate override, in **basis points** (6667 = 66.67%),
    # or NULL when the statutory default applies (Story 3.1, FR-H-3).
    #
    # **Integer basis points, not a percentage float.** The rest of this table
    # keeps money in integer cents for the reason a rate wants here too: 66.67
    # is not representable in binary floating point, and a column that stored
    # it would make "is this claim overridden?" a comparison two values could
    # fail by 1e-14. Basis points are the smallest unit the prototype's input
    # can produce (`step="0.5"`, two decimal places displayed), so nothing is
    # lost, and the weekly benefit is then computed with integer arithmetic
    # end to end — see `services/financials/benefit.py`.
    #
    # **Nullable, and null is the meaningful state**, not a missing value: it
    # is what the ↺ reset restores, and it is what makes "the default applies"
    # a fact about the row rather than a comparison against a rule document
    # version that may since have changed. Written only by
    # `services/claims/comp_rate.py` (AD-12).
    comp_rate_override_bp: Mapped[int | None] = mapped_column(Integer)

    # Duration / SLA source data (see story note: seeds as data, dataset's
    # SLA figures don't reconcile with its dates by design)
    days_recovery: Mapped[int | None] = mapped_column(Integer)
    settlement_days: Mapped[int | None] = mapped_column(Integer)
    sla_pick_days: Mapped[int | None] = mapped_column(Integer)
    sla_approve_days: Mapped[int | None] = mapped_column(Integer)
    sla_settle_days: Mapped[int | None] = mapped_column(Integer)

    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class TimelineEvent(Base):
    """One line of a claim's case timeline (Story 2.2) — append-only.

    **No `version` column, and that is the AD-4 statement for this table.**
    Compare-and-swap arbitrates concurrent writers of a mutable row; a log
    line is written once and never updated, so there is nothing to arbitrate.
    `AuditEvent` sets the same precedent for the same reason. AD-12 narrows
    it further: rows are emitted only as a side effect of the owning
    service's command (`services/claims`), never written by a router or a
    second service. This story only seeds and reads; Story 2.3's inline-edit
    command is the first runtime emitter.

    **`claim_id` is the surrogate FK, not the `WC-nnnn` business id.** That
    reads oddly beside `claim.claim_id`, which *is* the business string — but
    it is the house quirk Story 1.2 already documented for
    `claim.employee_id` (surrogate FK) against `employee.employee_id`
    (business id), and both names are Excel-canonical. Repeating the quirk is
    better than having two conventions.

    **Display order is append order, which is `id` order.** The log has no
    total order in its own columns: `event_date` repeats within a claim and
    is null on every settlement event (see below), so ordering by it is not
    even a partial answer. The seed inserts each claim's events in the
    prototype's array order and the identity column preserves it; a runtime
    emitter appends and lands after them, which is what "append-only" means.
    This matters concretely — the treatment overview shows the *last six*
    events, so an order that drifted would silently show a different six.

    **`event_date` is nullable because 62 of the seeded events have no
    date.** The prototype writes the literal string `Closed` where the
    settlement event's date belongs and renders it verbatim ("reached final
    settlement on *Closed*"). A `DATE` column cannot hold that and should
    not: the honest reading is that the date is unknown, and the UI omits the
    clause rather than printing a non-date where a date goes (NFR-3).
    `data/seed/extract_case_file_seed.py` documents the full parse.
    """

    __tablename__ = "timeline_event"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    # Indexed: every read of this table is "this claim's events".
    claim_id: Mapped[int] = mapped_column(ForeignKey("claim.id"), index=True)
    event_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text)
    # Text rather than a native enum — `TimelineTag` explains the trade-off
    # at length. Every later epic appends a new kind of event, and widening a
    # database enum once per story is migration tax on a display label.
    tag: Mapped[str] = mapped_column(Text)


class Document(Base):
    """A document on a claim file (Story 2.2 creates and seeds; 2.5 reads).

    Write-owner is `services/claims` (AD-12). This story seeds the rows and
    reads them for the intake checklist; Story 2.5 adds the Documents & ID
    tab, the path-classified statutory forms and the read-only viewer.

    **`blob_key` is nullable and every seeded row leaves it null.** The
    prototype has no files behind its document rows, so there are no bytes to
    put anywhere yet. The column exists now because the boundary is what is
    binding, not the backing store: when real files arrive they ride the
    `BlobStore` protocol (`put/get/delete/url`) and this is the handle they
    are addressed by. A nullable column is also the honest shape — a document
    the carrier has recorded but not yet received is a real state.

    **`filed_date` is nullable for `TimelineEvent.event_date`'s reason.** 101
    seeded documents carry `Post-surgery` or `Closed` where a filing date
    belongs — a timing note, not a date.

    `version` is present, unlike on `TimelineEvent`: a document row is a
    mutable entity (a filing date corrected, a blob key attached once the
    bytes land), so AD-4's compare-and-swap column belongs on it even though
    no story writes one yet. The same reasoning put `version` on `employer`
    and `employee` in Story 1.2.

    **`reviewed` and `confirmed` are Story 3.5's, and they are the first
    columns here that anything writes.** They are the prototype's
    `docReviewState` (lines 871-883), which it keeps in a module-level object
    lost on reload. Two columns rather than one three-valued state because they
    are two events with two audit rows — a document is read, and then it is
    accepted — and because the checklist's surgical pre-auth trigger asks a
    different question of each. The ordering is the prototype's
    (`confirmDocument` returns early when `!st.reviewed`) and is enforced in the
    write rather than in the column: the compare-and-swap that sets `confirmed`
    also requires `reviewed`, so an out-of-order confirmation is a 409 rather
    than a row nobody can explain.
    """

    __tablename__ = "document"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claim.id"), index=True)
    name: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[DocType] = mapped_column(_enum(DocType, "doc_type"))
    filed_date: Mapped[date | None] = mapped_column(Date)
    blob_key: Mapped[str | None] = mapped_column(Text)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class AdditionalInjury(Base):
    """A secondary injury captured on the body diagram (Story 2.4, FR-H-6).

    The prototype keeps these in a module-level `additionalInjuries` map that
    starts empty and is lost on reload; here they are rows a handler creates
    through an audited command and removes through another.

    **Seeded empty, and that is the correct row count rather than a gap.**
    The prototype's store has no entries for any of the 100 claims (line
    656), so there is nothing to extract. The epic's phrasing ("creates +
    seeds the tables") reads as if there were; the discrepancy is recorded in
    the story rather than papered over with invented clinical data.

    **`version` is present.** A row here is a mutable entity by AD-4's
    convention even though the only writes today are an insert and a delete:
    the delete compare-and-swaps on it, which is what stops one handler's ✕
    from removing the row another handler had just re-classified. `Document`
    carries the column for the same reason with even less to do.

    **`body_part` is stored, not derived at read time.** It is the label of
    `body_key` at the moment of capture, written by the command from
    `BODY_PART_LABELS`, exactly as `claim.body_part` is. Storing it keeps the
    audit diff a complete record of what was written, and keeps this row
    readable next to a `claim.body_part` that came from the carrier's own
    vocabulary.
    """

    __tablename__ = "additional_injury"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    # Indexed: every read of this table is "this claim's injuries".
    claim_id: Mapped[int] = mapped_column(ForeignKey("claim.id"), index=True)
    body_key: Mapped[BodyRegion] = mapped_column(_enum(BodyRegion, "body_region"))
    body_part: Mapped[str] = mapped_column(Text)
    injury_type: Mapped[str] = mapped_column(Text)
    # The CHECK is the database's half of a bound the command also enforces.
    # Not redundancy: the command answers 422 with a sentence a handler can
    # act on, and the constraint is what makes "no row outside 0-100 exists"
    # true of the table rather than of the code paths anyone remembered.
    severity_score: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __table_args__ = (CheckConstraint("severity_score BETWEEN 0 AND 100", name="ck_severity"),)


class TreatmentPlanStep(Base):
    """One numbered step of a claim's treatment plan (Story 2.4).

    Reference content rather than a live entity: the rows come from the
    prototype's `treatmentPlan` arrays and no command writes one this epic,
    which is why there is no `version` column — there is no concurrent writer
    to arbitrate (`GlossaryTerm` makes the same argument for the same reason).
    A later story that lets a handler edit the plan adds the column with the
    command that needs it.

    **`step_no` is the order, and it is unique per claim.** The card renders
    "1., 2., 3." from this number rather than from row order, so two rows
    claiming step 2 would render an ambiguous plan; the constraint makes a
    re-seed that duplicated a claim's steps fail at the migration rather than
    at the card.
    """

    __tablename__ = "treatment_plan_step"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claim.id"), index=True)
    step_no: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)

    # Unnamed, so `NAMING_CONVENTION` decides — an explicit name would be
    # taken verbatim and would then disagree with what the convention renders
    # in the migration, which `alembic check` reports as a constraint dropped
    # and re-added on every run.
    __table_args__ = (UniqueConstraint("claim_id", "step_no"),)


class Photo(Base):
    """One incident or site photograph on a claim file (Story 2.6, AC 1).

    Write-owner is `services/claims` (AD-12, the case-file aggregate) — and
    this epic its **only** writer is the seed migration. Nothing in Epics 1–8
    uploads, captures, annotates or deletes a photo, so the read-only viewer is
    not a discipline the components keep: it is the shape of the table's grant
    (`SELECT` and nothing else, migration 0020).

    Three columns that would look natural here and are deliberately absent:

    - **No `version`.** AD-4's compare-and-swap column arbitrates concurrent
      writers, and there are none. `Document` carries one because a filing date
      can be corrected and a blob key attached once bytes land; a photo, in
      this system, is only ever read. `TreatmentPlanStep` argues the same point.
    - **No `sort_order`.** Display order is insertion order, carried by the
      identity column exactly as `Document` and `TimelineEvent` carry theirs.
      `PathRequiredForm` needed an explicit number only because three
      independent reference lists share one table; a claim's photos are one
      list, seeded in the prototype's array order and read back by `id`.
    - **No `taken_on` date.** `source` is a provenance sentence — "Plant
      safety, 03/22", "OSHA inspector, 03/24" — whose trailing fragment carries
      no year and is not always the incident's day. Splitting it would produce
      a `DATE` column that is wrong about a third of the time beside a source
      column that had lost half its meaning.

    **`blob_key` is nullable and every seeded row leaves it null**, for
    `Document`'s reason: the prototype's thumbnails are the 📷 emoji and there
    are no image files behind them. The column exists because the *boundary* is
    what binds — bytes ride the one `BlobStore` protocol (`put/get/delete/url`)
    and this is the handle they are addressed by — while the volume-versus-MinIO
    decision stays Deferred. A null key is also the honest shape: a photograph
    the carrier has a record of but not a copy of is a real state, and it is the
    state all 293 seeded rows are in.

    AD-11: incident photos are claim-derived PHI-class binaries. When real files
    arrive they live on encrypted volumes behind `BlobStore`, and nothing about
    them reaches a log beyond ids and event names.
    """

    __tablename__ = "photo"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    # Indexed: every read of this table is "this claim's photos" — the tab is
    # never rendered without a claim.
    claim_id: Mapped[int] = mapped_column(ForeignKey("claim.id"), index=True)
    caption: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    blob_key: Mapped[str | None] = mapped_column(Text)


class PathRequiredForm(Base):
    """A statutory form one handling path requires (Story 2.5, FR-H-8).

    Reference data, seeded from the prototype's `PATH_DOCS` — nine rows across
    three paths. Read-only to the application in the same sense `GlossaryTerm`
    is, and absent for the same three reasons:

    - **No `version` column.** Compare-and-swap arbitrates concurrent writers
      and this table has none: the migration is its only writer (AD-12, under
      `services/claims`' "Clinical & regulatory fields — statutory forms by
      path" capability row).
    - **No audit wiring.** AD-4 audits commands; there are none here.
    - **No employer column and no claim FK.** Which forms a path requires is a
      statement about the *path*, not about anybody's claim, which is what
      makes the AD-7 carve-out in `data/repositories/statutory_forms.py`
      legitimate rather than convenient. The claim's path is derived
      (`services/derivations/path_classification.py`) and joins to these rows at read
      time; storing it on `claim` would be a derived column, which Story 1.2
      banned.

    **`download_url` holds a placeholder, deliberately and on the record.**
    The nine URLs are the NY WCB and FNSB links the prototype chose; NFR-4 and
    an explicit Deferred architecture decision put per-jurisdiction validation
    of statutory form references before go-live and outside this story. They
    are not surfaced as provisional in the UI (that would be a product claim
    this story has no basis for) — the caveat lives here and in the migration.

    **`sort_order` is unique *within a path*, not globally.** The card renders
    one path's forms in the prototype's array order, and each path's list is
    numbered from zero — so a global uniqueness constraint would force the
    three lists into one sequence and make adding a Path A form renumber Path
    C. The pair is what has to be total, and it is: `GlossaryTerm` argues the
    same point for its single-list case.
    """

    __tablename__ = "path_required_form"
    __table_args__ = (UniqueConstraint("path", "sort_order"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    path: Mapped[ClaimPath] = mapped_column(_enum(ClaimPath, "claim_path"))
    # Not unique, and that is the data rather than a missing constraint: a form
    # code is unique *within* a path here, but nothing about the statutory
    # scheme says two paths cannot require the same filing, and a constraint
    # asserting otherwise would be this table deciding a regulatory question.
    form_code: Mapped[str] = mapped_column(Text)
    form_name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    # Free text, not a duration: the prototype's timings are statutory
    # sentences ("Within 10 days of incident", "When physician determines
    # MMI"), and only some of them are expressible as a number of days. Parsing
    # the ones that are would leave the rest rendering an em dash, which is a
    # worse answer than the sentence the regulation actually uses.
    timing: Mapped[str] = mapped_column(Text)
    download_url: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer)


class StateRateSchedule(Base):
    """One jurisdiction's statutory weekly indemnity bounds (Story 3.1, AC 1).

    Reference data, seeded from the prototype's `STATE_WC_RATES` — the
    seventeen states the portfolio's fifteen plants sit in. Read-only to the
    application in the same sense `GlossaryTerm` and `PathRequiredForm` are,
    and absent the same three things for the same reasons:

    - **No `version` column.** Compare-and-swap arbitrates concurrent writers
      and this table has none: the migration is its only writer.
    - **No audit wiring.** AD-4 audits commands; there are none here.
    - **No employer column and no claim FK.** A statutory weekly maximum is a
      statement about a *jurisdiction*, not about anybody's claim, which is
      what makes the AD-7 carve-out in `data/repositories/state_rates.py`
      legitimate rather than convenient. The claim's state is a column on
      `claim` and joins to these rows at read time.

    **The prototype's silent fallback does not survive.** `computeBenefit`
    reads ``STATE_WC_RATES[c.state] || {max:1200, min:250}`` — so a claim in a
    state the object does not list is clamped to two numbers that belong to no
    jurisdiction, and the card names the state beside them with total
    confidence. Here a missing row is a data error: migration 0023 refuses to
    complete if any seeded claim's state has no schedule, and
    `services/financials` raises rather than defaulting (NFR-4).

    **`effective_date` is what the card cites, and today it is one row per
    state.** `state_code` is unique, so the table holds the *current* schedule
    rather than a history — the story's shape. The column is here because a
    weekly maximum is only true of a period: a refresh process adds the next
    year's figures, at which point the unique constraint moves to
    `(state_code, effective_date)` and the read selects the latest row whose
    date has arrived, exactly as `rule_document` already does for rules. Until
    then it is the provenance date the benefit card shows in place of the
    prototype's "Illustrative figures" disclaimer.

    **The seeded bounds are illustrative and this docstring says so where it
    matters.** NFR-4 and an explicit Deferred architecture decision put
    per-jurisdiction validation of statutory rates before go-live; the values
    are the prototype's, in cents.
    """

    __tablename__ = "state_rate_schedule"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    # Unique: one live schedule per jurisdiction — see the docstring on what
    # changes when a second one arrives.
    state_code: Mapped[str] = mapped_column(Text, unique=True)
    state_name: Mapped[str] = mapped_column(Text)
    # Integer cents, like every other money column. The prototype's dollars
    # were multiplied once, in the extractor.
    weekly_min_cents: Mapped[int] = mapped_column(BigInteger)
    weekly_max_cents: Mapped[int] = mapped_column(BigInteger)
    effective_date: Mapped[date] = mapped_column(Date)

    # The database's half of a bound the extractor and the migration also
    # check. Not redundancy: an inverted pair pins every claim in the state to
    # the lower bound and the card still renders a confident figure, so "no
    # such row exists" should be true of the table rather than of the code
    # paths anyone remembered.
    __table_args__ = (
        CheckConstraint("weekly_min_cents <= weekly_max_cents", name="ck_weekly_bounds"),
    )


class PaymentScheduleWeek(Base):
    """One week of a claim's indemnity payment schedule (Story 3.3, AC 2).

    Write-owner is `services/financials` (AD-12) and it is the **only**
    writer: Story 3.4's worklist approval calls that service's command rather
    than reaching this table, and nothing else touches it at all.

    **These rows are a materialized projection, not a ledger.**
    `services/financials/schedule.py::project_payments` decides how many weeks
    a claim is scheduled for, when each one runs and what it pays — one
    generator, AD-2 — and `services/financials/materialize.py` writes what it
    produced. That is why re-running the command is a no-op on unchanged
    inputs rather than an append: the rows *are* the projection, keyed by
    `(claim_id, week_no)`, and the unique constraint below is what makes that
    a fact about the table rather than a habit of the writer.

    **`status` is the one column the projection does not own outright**, and
    the reason is the failure AD-12 names by name. Two of the five statuses
    are *decisions* — `payment_scheduled` is a handler approving a week into
    the next batch (3.4) and `paid` can be the batch having disbursed it —
    while the other three are the calendar's answer. A regeneration that
    overwrote the column wholesale would silently revoke approvals every time
    a claim was re-read, so the command preserves decided statuses and
    refreshes only the calendar-derived ones. `materialize.py` states the
    partition and `tests/test_schedule_materialization.py` holds it.

    **`version` from birth.** A week is a mutable entity — 3.4 moves its
    status — so AD-4's compare-and-swap column belongs on it now rather than
    in the migration that first needs it, which is the rule `Document` and
    `AdditionalInjury` already follow.

    **`week_no` is 1-based** because it is a label a handler reads ("Wk 3"),
    not an index, and `period_start`/`period_end` are inclusive so a week is
    seven days — the prototype's arithmetic, kept because an exclusive end
    renders as the next week's start date in the Bills table.
    """

    __tablename__ = "payment_schedule_week"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    # Indexed: every read of this table is "this claim's schedule".
    claim_id: Mapped[int] = mapped_column(ForeignKey("claim.id"), index=True)
    week_no: Mapped[int] = mapped_column(Integer)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[ScheduleWeekStatus] = mapped_column(
        _enum(ScheduleWeekStatus, "schedule_week_status")
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    # Unnamed so `NAMING_CONVENTION` decides — `TreatmentPlanStep`'s note.
    # This is what makes the materialization command an upsert rather than an
    # append: a second row for one week of one claim is refused by the
    # database, not merely avoided by the code that happens to write.
    __table_args__ = (UniqueConstraint("claim_id", "week_no"),)


class Bill(Base):
    """One medical bill on a claim (Story 3.3, AC 3).

    Write-owner is `services/financials` (AD-12). This story's only writer is
    the seed migration; Story 3.4's approval is the first runtime one, moving
    `under_review` → `payment_scheduled`.

    **`label` and `category` are both stored, and neither is derivable from
    the other.** The category is the token a query groups by; the label is the
    sentence the prototype wrote ("Diagnostic Imaging (MRI/CT/X-Ray)") and is
    content rather than code. Deriving the label from the category at read
    time would put six English strings in a service, and deriving the category
    from the label would make a reworded bill fall out of its own group.
    `AdditionalInjury.body_part` stores its label beside its key for the same
    reason.

    **`amount_cents` is integer cents like every other money column**, and the
    prototype's dollar figures were multiplied once, in the extractor.

    `version` is present for `PaymentScheduleWeek`'s reason: 3.4 mutates the
    status, so the compare-and-swap column arrives with the table.
    """

    __tablename__ = "bill"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    # Indexed: every read is "this claim's bills" — the Bills tab is never
    # rendered without a claim, and the reserve check sums one claim's unpaid.
    claim_id: Mapped[int] = mapped_column(ForeignKey("claim.id"), index=True)
    category: Mapped[BillCategory] = mapped_column(_enum(BillCategory, "bill_category"))
    label: Mapped[str] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[LineItemStatus] = mapped_column(_enum(LineItemStatus, "line_item_status"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class Expense(Base):
    """One non-medical expense on a claim (Story 3.3, AC 3).

    `Bill`'s shape and `Bill`'s owner, over a different category vocabulary —
    see `ExpenseCategory` for why the two are separate enums and
    `LineItemStatus` for why the statuses are one.

    **The readiness review's stated reason for creating this table here does
    not survive checking, and the table is still right.** That review moved
    `expense` into Story 3.3 because "it feeds the settled-stage payout
    breakdown rendered by Story 2.2", and 3.3's Dev Notes ask for that surface
    to be verified after seeding rather than assumed. Verified: it was never
    empty. The settled Overview reads `claim.paid_expense`, and all 62 seeded
    settled claims carry a non-zero value for it from the Story 1.2 seed.

    What this table actually feeds is the Bills tab's own Expenses card (AC 3)
    and the *effective* expense figure on open claims, where the paid columns
    are zero — which is a good enough reason on its own. The correction is
    recorded because the premise was about a different surface, and a later
    story reading the old sentence would go looking for a dependency that is
    not there. The two figures do not agree, either; see `deferred-work.md`.
    """

    __tablename__ = "expense"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claim.id"), index=True)
    category: Mapped[ExpenseCategory] = mapped_column(_enum(ExpenseCategory, "expense_category"))
    label: Mapped[str] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[LineItemStatus] = mapped_column(_enum(LineItemStatus, "line_item_status"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class Meeting(Base):
    """One scheduled stakeholder touchpoint (Story 4.1, FR-DIARY-2).

    The prototype keeps these in a browser-lifetime object (`meetingsStore`,
    line 1838) re-seeded at every login and lost on reload; here they are rows
    written by the three audited commands in `services/claims/meetings.py`,
    which AD-12 names as the diary aggregate's only writer.

    **A row is a log of intent, not a delivery attempt.** No calendar invite,
    no ICS attachment and no notification is produced by scheduling one — real
    calendar egress is a Deferred architecture decision with its own compliance
    review. So there is no `sent_at`, no `external_event_id` and no delivery
    status: the table records that a handler planned a touchpoint, and nothing
    about it claims anybody was told.

    **`app_user_id` is the owner, and it is what scopes every read.** A meeting
    belongs to the handler who holds it rather than to the claim it references
    — which is why `list_meetings` is a *caller*-scoped list rather than a
    child read-model of the case file, and why the repository's predicate is
    the owner first and the employer scope second. Two handlers whose books
    overlap (the seed has two on John Deere) see their own meetings and not
    each other's.

    **`claim_id` is nullable**, which is the ERD's `CLAIM |o--o{ MEETING`. The
    scheduler always opens from a selected claim, so every meeting the UI
    creates carries one; the column admits null because "a supervisor catch-up
    that is not about one file" is a real thing to have scheduled, and a
    NOT NULL here would make it unrecordable rather than unrepresented.

    **`participants` is JSONB, not an array column and not a join table.** No
    `ARRAY` column exists anywhere in this schema, and the story's own ruling
    is that nothing in Epics 1-8 asks a participant-side question — the list is
    always read whole with its meeting. The elements are `MeetingParticipant`
    values; the command validates them, because a JSONB column cannot.

    **`version` from birth**, unlike `photo` and `treatment_plan_step`: the
    complete and delete commands compare-and-swap on it (AD-4), and a ✓ that
    could remove the row another handler had just re-dated is exactly what the
    column is for. `created_at` is the database's fact, defaulted there for
    `RuleDocument.created_at`'s reason.

    AD-11: `notes`, `location` and `participants` are PHI-class — a meeting
    agenda names a worker's treatment and a physician's practice. Nothing about
    them reaches a log beyond ids and event names, and the table belongs in
    Story 8.1's purge cascade, which does not exist yet and is not invented
    here.
    """

    __tablename__ = "meeting"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    # Indexed: every read of this table is "my meetings" — the list has no
    # other entry point, and the owner predicate is on every statement.
    app_user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), index=True)
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("claim.id"), index=True)
    meeting_type: Mapped[MeetingType] = mapped_column(_enum(MeetingType, "meeting_type"))
    # A calendar `date`, not a timestamp: the modal captures a day and an
    # optional wall-clock time in the handler's own locale, and combining them
    # into one instant would require a timezone this console does not collect.
    meeting_date: Mapped[date] = mapped_column(Date)
    # Nullable because the prototype's time input can be cleared and an
    # all-day touchpoint is a real thing to record. The card omits the clock
    # rather than printing midnight.
    meeting_time: Mapped[time | None] = mapped_column(Time)
    location: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    participants: Mapped[list[str]] = mapped_column(JSONB)
    is_done: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DiaryNote(Base):
    """One dated working note in a handler's diary (Story 4.2, FR-DIARY-1).

    The prototype keeps these in a browser-lifetime object (`diaryNotes`, line
    919) keyed by handler name and erased by a reload; here they are rows
    written by the one audited command in `services/claims/notes.py`, which
    AD-12 names as the diary aggregate's only writer.

    **No `version` column, and that is the AD-4 statement for this table.**
    Compare-and-swap arbitrates concurrent writers of a mutable row; a note is
    written once and never updated — there is no edit and no delete in the
    design contract, so nothing is ever read-modify-written and there is
    nothing to arbitrate. `TimelineEvent` and `AuditEvent` set the precedent
    for the same reason, and the write-concurrency convention names append-only
    stores as exempt outright. A `version` here would be a column whose only
    possible value is 1.

    **`app_user_id` is the author, and it is what scopes every read.** A note
    belongs to the handler who wrote it, not to the claim it is tagged to (the
    ERD's `APP_USER ||--o{ DIARY_NOTE : writes`) — so the list is *caller*
    scoped and a note is invisible to everybody else, including a supervisor
    over the same book. The claim tag is a reference, not an owner.

    **`claim_id` is nullable**, which is the ERD's `CLAIM |o--o{ DIARY_NOTE`.
    The add-note input is always on screen, including when no claim is
    selected, and a note somebody wrote with nothing selected is a real thing
    to have written rather than an error to refuse.

    **`noted_at` is one `timestamptz`, and that is a deliberate divergence
    from the Excel.** `docs/WC_Feature_Element_Details.xlsx` row 91 names
    `DiaryNotes.date` and `DiaryNotes.time` as two elements. `Meeting` keeps
    its date and time split because the *modal captures a calendar day* and an
    optional wall-clock time in the handler's own locale; a note has no
    user-entered time at all — `noted_at` is the server clock at the moment of
    the write — so one instant is the honest shape and two columns would be a
    split nothing ever writes independently. The UI formats it as
    `{date} · {time}`, which is the prototype's own header.

    Indexed because the list is ordered by it: `noted_at DESC, id DESC` is the
    keyset the cursor walks, and an unindexed sort key is a sequential scan of
    every handler's diary on every page.

    AD-11: `note_text` is PHI-class — a diary entry names a worker's treatment,
    their employer's position and the handler's own read of the file.

    **The text is written into `audit_event.after` in full**, and that is worth
    stating plainly because an earlier version of this docstring said the
    opposite ("nothing about it reaches a log beyond ids and field names"),
    which was true of the structlog line `services.audit` emits and false of the
    JSONB diff it stores. `services/claims/notes.py::_diff` carries the whole
    note for `injuries.py::_diff`'s reason — the row *is* the change, and a log
    that recorded a field name could not say what was written — and `Meeting`
    sets the same precedent for an agenda one table over. The consequence is a
    retention one: the note exists in two places, so Story 8.1's purge cascade
    has to reach `audit_event` as well as this table. That cascade does not
    exist yet and is not invented here; the deferral is recorded rather than
    described as an absence.
    """

    __tablename__ = "diary_note"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    # Indexed: every read of this table is "my notes" — the list has no other
    # entry point, and the author predicate is on every statement.
    app_user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), index=True)
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("claim.id"), index=True)
    note_text: Mapped[str] = mapped_column(Text)
    noted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, server_default=func.now()
    )


class EmailTemplate(Base):
    """One of the six claim-aware stakeholder letters (Story 4.3, FR-DIARY-3).

    The prototype rebuilds these in the browser from JS template literals
    (`loadEmailTemplate`, line 1955), so every letter is assembled client-side
    out of claim fields the page happened to be holding. Here the text is
    **reference data** and the merge is a server command (AD-1): the SPA asks
    for a merged template and renders what comes back.

    Read-only to the application, exactly like `GlossaryTerm`, and the same
    three absences follow from it:

    - **No `version` column.** Compare-and-swap arbitrates concurrent writers
      and this table has none: rows arrive in seed migration 0036 and change
      only in a later one. There is no template administration surface and the
      story forbids inventing one.
    - **No audit wiring.** AD-4 audits *commands*; there are none here, and a
      migration that edits reference data is already reviewed in the diff.
    - **No employer column and no claim FK.** A template is not about a claim —
      it is merged *against* one, per request, and the claim it was merged
      against is recorded on `email_log` rather than here.

    `default_recipients` is JSONB holding `MeetingParticipant` values, which is
    `Meeting.participants`' shape and its ruling: no `ARRAY` column exists
    anywhere in this schema, the list is always read whole with its template,
    and nothing asks a recipient-side question. The elements are validated by
    `services/claims/emails.py` when they are decoded, because a JSONB column
    cannot.

    `subject_template` and `body_template` carry `{{placeholder}}` tokens named
    after the column each reads (`{{claim_id}}`, `{{worker_name}}`, `{{doi}}`,
    …). Bracketed prompts meant for the handler — `$[AMOUNT]`, `[RATING]%`,
    `[Please add next steps]` — are **template text, not merge fields**, and
    AD-2 is explicit that the settlement figures stay handler-filled.

    `template_key` is unique for `GlossaryTerm.abbreviation`'s reason: it is the
    stable identity the wire publishes and the SPA keys its six buttons by, so
    the surrogate `id` never has to leave the server.
    """

    __tablename__ = "email_template"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    template_key: Mapped[str] = mapped_column(Text, unique=True)
    label: Mapped[str] = mapped_column(Text)
    subject_template: Mapped[str] = mapped_column(Text)
    body_template: Mapped[str] = mapped_column(Text)
    default_recipients: Mapped[list[str]] = mapped_column(JSONB)


class EmailLog(Base):
    """One stakeholder email a handler composed and logged (Story 4.3).

    The prototype keeps these in a browser-lifetime object (`emailsStore`) that
    a reload erases and announces each one with `alert()`; here a send is a row
    written by the one audited command in `services/claims/emails.py`, which
    AD-12 names as the diary aggregate's only writer.

    **A row is a log, not a delivery attempt, and that is an architecture
    decision rather than a shortcut.** Real SMTP egress is Deferred with its own
    compliance review, so there is no mail client, no outbox, no delivery
    status, no bounce, no message id and no per-recipient address — the button
    says "✉ Send Email (logged)" and this table is what it means. `sent_at` is
    when the handler pressed it, and it claims nothing about anybody having
    been told.

    **No `version` column, and that is the AD-4 statement for this table** —
    `DiaryNote`'s argument, one table over. Compare-and-swap arbitrates
    concurrent writers of a mutable row; a logged email is written once and
    never updated, because there is no edit and no delete in the design
    contract, so nothing is ever read-modify-written and there is nothing to
    arbitrate. The write-concurrency convention names append-only stores as
    exempt outright.

    **`app_user_id` is the sender, and it is what scopes every read** (the
    ERD's `APP_USER ||--o{ EMAIL_LOG : sends`). The sent log is the caller's
    own; a handler does not read another's, and neither does a supervisor over
    the same book.

    **`claim_id` is nullable**, which is the ERD's `CLAIM |o--o{ EMAIL_LOG`.
    The six templates are claim-aware and refuse to merge without one, but free
    composition — a subject and a body typed by hand — is legal with nothing
    selected, and that is what the optional edge is for.

    **`template_id` is nullable and is a real FK** (the ERD's
    `EMAIL_TEMPLATE ||--o{ EMAIL_LOG : seeds`): null for a free composition, and
    for a templated one the command resolves the key against this table rather
    than storing the string, so an unknown key is refused instead of recorded.

    AD-11: `subject`, `body` and `recipients` are PHI-class — a merged letter
    carries the worker's name, their date of injury and their ICD-10 code.
    Nothing about them reaches a log beyond ids and event names, and **the body
    is deliberately absent from the audit diff** (see
    `services/claims/emails.py::_diff`): the table is append-only with no delete
    path, so an after-diff is never needed to reconstruct a row somebody
    removed, and the body is the largest PHI blob this console stores. The table
    belongs in Story 8.1's purge cascade, which does not exist yet and is not
    invented here.
    """

    __tablename__ = "email_log"

    # `(app_user_id, sent_at DESC, id DESC)` — the sent log's whole access
    # pattern in one index, in the ordering's own direction so the keyset walk
    # needs no sort. Declared here rather than as three `index=True` flags
    # because that is what migration 0035 creates, and a model describing
    # indexes the database does not have is a plan nobody can trust.
    __table_args__ = (
        Index(
            "ix_email_log_app_user_id_sent_at_id",
            "app_user_id",
            text("sent_at DESC"),
            text("id DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    app_user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"))
    # Indexed on its own: PostgreSQL does not index a referencing column, so
    # without this every `DELETE FROM claim` (Story 8.1's purge) scans this
    # table to check the constraint.
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("claim.id"), index=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("email_template.id"))
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[EmailPriority] = mapped_column(_enum(EmailPriority, "email_priority"))
    recipients: Mapped[list[str]] = mapped_column(JSONB)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GlossaryTerm(Base):
    """WC domain reference data (Story 1.6, FR-GLOS-1) — the prototype's GLOSS.

    Read-only to the application, which is why three things other tables
    have are deliberately absent:

    - **No `version` column.** Compare-and-swap exists to arbitrate
      concurrent writers, and this table has none: rows arrive in a seed
      migration and change only in a later one. `AuditEvent` and `Session`
      set the same precedent for different reasons.
    - **No audit wiring.** AD-4 audits *commands*; there are none here, and
      a migration that edits reference data is already reviewed in the diff.
    - **No employer column and no claim FK.** The ERD's dotted
      `GLOSSARY_TERM }o--o{ CLAIM` association has no story behind it, so no
      join table is invented here. Nothing about a term is scoped to
      anybody, which is what makes the AD-7 exception in
      `data/repositories/glossary.py` legitimate rather than convenient.

    `sort_order` carries the prototype's display order (FNOL first, then the
    acronym cluster, then the manufacturing hazards). It is unique so the
    ordering is total — a repository that sorted on a column with ties would
    be free to repeat or drop a row once anything pages it.
    """

    __tablename__ = "glossary_term"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    # "OSHA 300" and "Arc Flash" are multi-word, and "Apportionment" repeats
    # its term verbatim — prototype data, preserved rather than normalized.
    #
    # Unique, and that constraint is load-bearing on the wire:
    # `GlossaryTermResponse` deliberately publishes neither `id` nor
    # `sortOrder` (a surrogate key is an implementation detail, and the
    # client has no use for one), which leaves the abbreviation as the only
    # stable identity a React list key can use. Enforcing it here is what
    # makes that payload keyable by construction instead of by luck — the
    # alternative, exposing `id` purely to key a list, leaks the surrogate
    # into a public contract to solve a problem the data does not have.
    abbreviation: Mapped[str] = mapped_column(Text, unique=True)
    term: Mapped[str] = mapped_column(Text)
    definition: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, unique=True)


class RuleDocument(Base):
    """A versioned, effective-dated ZEN JDM document (Story 2.1, AD-8).

    AD-8 splits every rule in two: JDM owns the parameters, typed Python
    owns the formula. This table is the JDM half's storage, and Story 2.1 is
    its first consumer — the queue's priority weights and the derivation
    registry's thresholds.

    Three columns carry the whole of AD-8's "versioned in the DB with
    effective dates":

    - **`key`** names the rule, not the file. `rules/documents/*.jdm.json`
      is where a version is *authored*; this row is what a request reads, so
      renaming a file cannot change which rule a running server evaluates.
    - **`version`** is monotonic per key, and unique with it. It is what the
      queue's cursor records: "this ordering was produced by
      `priority_weights` v1" is answerable rather than inferred.
    - **`effective_from`** is the date the version takes over. The loader
      picks the highest version whose date has arrived, so a rule change can
      be migrated ahead of the day it applies — which is the operability
      model AD-8 chose over an env-var override.

    No `version` column in the AD-4 compare-and-swap sense, and no audit
    wiring, for `GlossaryTerm`'s reasons: rows arrive in a migration and are
    read-only at runtime (the app role holds SELECT and nothing else).
    Superseding a document is a new row in a new migration, never an UPDATE —
    which is also what keeps an old cursor's `version` meaningful.
    """

    __tablename__ = "rule_document"
    __table_args__ = (UniqueConstraint("key", "version"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    key: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[date] = mapped_column(Date)
    # JSONB rather than Text: the document is structured data the database
    # can be asked about (which nodes, which weights), and a JSON parse
    # error becomes a migration failure instead of a 500 at first evaluation.
    content: Mapped[dict] = mapped_column(JSONB)  # type: ignore[type-arg]
    # Defaulted in the database rather than left to the writer. Every row so
    # far arrives from a seed migration that spells the timestamp out, so
    # the column looked fine — but the first ORM insert anyone wrote would
    # have failed on NOT NULL for a value nobody has an opinion about. When
    # a rule version was authored is the database's fact, not the caller's.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    """Append-only; fixed schema per AD-4. No version column."""

    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"))
    actor_role: Mapped[UserRole] = mapped_column(_enum(UserRole, "user_role"))
    action: Mapped[str] = mapped_column(Text)
    entity: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str] = mapped_column(Text)
    before: Mapped[dict | None] = mapped_column(JSONB)  # type: ignore[type-arg]
    after: Mapped[dict | None] = mapped_column(JSONB)  # type: ignore[type-arg]


#: The width of a `bge-m3` vector, and the width both embedding columns
#: declare. Restated as a module constant rather than written twice inline so
#: the two tables cannot disagree, and asserted against
#: `services/rag/client.EMBEDDING_DIMENSIONS` and migration 0040's literal in
#: `tests/test_embedding_tables_migration.py`. The three copies exist for three
#: different reasons — a column type, a runtime validation bound and a frozen
#: migration — and the test is what keeps them one number.
EMBEDDING_DIMENSIONS = 1024

#: The HNSW indexes, named here so the model, migration 0040 and its
#: `downgrade()` spell them identically. Declared on the models at all — rather
#: than left as objects only the migration knows about — because
#: `Base.metadata` is Alembic's `target_metadata`, so an index present in the
#: database and absent from the metadata is a drop that a later autogenerate
#: would propose in good faith.
CLAIM_EMBEDDING_HNSW_INDEX = "ix_claim_embedding_embedding_hnsw"
KNOWLEDGE_EMBEDDING_HNSW_INDEX = "ix_knowledge_embedding_embedding_hnsw"


class ClaimEmbedding(Base):
    """One claim's vector representation, and its freshness (Story 6.1, AD-12).

    Derived data in the AD-10 sense: there is exactly one computing path
    (`services/rag`), nothing user-writable, and every column here is a
    function of the claim rather than a fact anybody typed. Nothing else in the
    codebase inserts, updates or deletes a row — mutating commands ask for
    staleness through `services/rag.mark_claim_stale`, the same
    request-through-the-owner shape `audit.record` and `timeline.append`
    already use.

    **No `version` column, and this is the AD-4 statement for the table.**
    Compare-and-swap arbitrates concurrent writers of a mutable row. This table
    has one writer whose writes are idempotent recomputations: two refresh runs
    racing on one claim compute the same vector from the same source text and
    write the same `source_text_hash`, so there is nothing for a CAS to
    protect. `TimelineEvent` and `AuditEvent` reach the same conclusion from
    the append-only direction.

    **`stale_at` is not a version, and it guards a race a version would miss.**
    Refresh-versus-refresh is harmless, as above. Refresh-versus-*edit* is not:
    the refresh selects a pending row, composes it, and then waits on one HTTP
    call for up to `EMBEDDING_REQUEST_TIMEOUT_SECONDS`, and a handler's edit
    committing inside that window sets `stale = true` for a change the in-flight
    vector does not contain. A write that cleared the flag unconditionally would
    store the pre-edit vector with a fresh `embedded_at` beside it, and the row
    would stay wrong until some unrelated edit happened to touch it — a wrong
    answer with a fresh timestamp, which is precisely what AC 4 exists to
    prevent.

    So `mark_claim_stale` stamps `stale_at`, the pending read returns it, and
    `write_claim_embedding` clears `stale` **only if `stale_at` still holds the
    value that was read before embedding**. The vector is written either way —
    it is newer than the nothing it replaces — but a mark that landed mid-flight
    survives and the next tick re-embeds. A timestamp rather than a counter
    because it is also readable: "when did this row last go out of date" is a
    question an operator asks and a bare sequence number cannot answer.

    ## Four nullable columns, and a NULL that means something

    `embedding IS NULL` is not a missing value: it is "this claim has never
    been embedded", which migration 0041 creates deliberately for all 100
    seeded claims because a migration cannot reach a model server. It is one of
    the two pending states the refresh command selects on. The other is
    `stale`, which means "there *is* a vector and the text it was built from
    has since changed" — a different fact, which is why it is a different
    column and why the refresh orders stale rows first: a wrong answer is worse
    than no answer.

    **`source_text_hash` and no summary column.** The composed claim text is
    re-derivable from the claim at zero cost, so storing it would put a second
    copy of PHI in the database for Epic 8's purge cascade to chase in exchange
    for nothing.

    It is **evidence, not a queue**, and the distinction is worth stating
    because the column reads like a change detector. Nothing selects on it: the
    refresh's pending predicate is the three facts above, all of which are known
    without recomputing anything, whereas a hash comparison would require
    composing every candidate row before deciding whether to embed it — a
    second, weaker encoding of what `stale` already records. What the hash is
    for is proving the summary *really* moved:
    `tests/test_embedding_staleness.py` asserts it changes across an edit, which
    is the assertion that would fail against a composer that had quietly dropped
    the edited field while every flag-and-timestamp assertion stayed green.
    `services/rag/claim_text.py` is deterministic precisely so that comparison
    detects real change rather than dict iteration order.

    **`model` is provenance and a pending condition.** The refresh treats a row
    whose `model` is not the model the current client answers as pending, so
    re-pointing `EMBEDDING_MODEL` at another 1024-dimension model re-embeds the
    portfolio rather than leaving vectors from two incomparable model spaces
    mixed in one index and retrieved together. It is also the first thing
    anybody debugging a retrieval result asks for.

    **PHI-class (AD-11).** A vector derived from claim text is claim data: it
    lives on the same encrypted volume, joins Epic 8's purge cascade, and never
    appears in a log line. `services/rag` logs ids and counts.
    """

    __tablename__ = "claim_embedding"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    # Unique, not merely indexed: the 1:1 is what lets `mark_claim_stale` be a
    # single idempotent `INSERT … ON CONFLICT DO UPDATE` rather than a
    # select-then-branch with a race in the gap. It also backs the FK, which
    # PostgreSQL does not index automatically — without it Story 8.1's purge
    # would scan this table once per deleted claim.
    claim_id: Mapped[int] = mapped_column(ForeignKey("claim.id"), unique=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    source_text_hash: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # The monotonic mark-stale marker the conditional clear compares against.
    # NULL is "never marked", which is what migration 0041's pre-created rows
    # carry — and `IS NOT DISTINCT FROM` is what makes that state compare
    # correctly rather than silently never matching.
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Cosine, because `<=>` is what the repository orders by and bge-m3's
        # output is not unit-normalised by the server. An L2 operator class
        # here would leave a valid index that the planner never chooses, with
        # nothing on screen to say why retrieval got slow.
        Index(
            CLAIM_EMBEDDING_HNSW_INDEX,
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class KnowledgeChunk(Base):
    """One passage of the seeded labour-law corpus (Story 6.1, AC 2).

    Reference content rather than a live entity, so no `version` column, for
    `GlossaryTerm`'s and `TreatmentPlanStep`'s reason: rows arrive in a
    migration and nothing writes one at runtime, which means there is no
    concurrent writer for a compare-and-swap to arbitrate.

    **Every row is synthetic and says so.** `source` begins `synthetic-demo:`
    and `chunk_text` opens with a sentence stating that it is demonstration
    text, not statutory law. Knowledge-corpus sourcing is a Deferred
    architecture decision and this story is explicitly forbidden from building
    an ingestion pipeline; what it needs is enough real rows that the RAG path
    retrieves something. Presenting paraphrase as authoritative law would
    repeat the failure `deferred-work.md` already records against the statutory
    forms — a surface that behaves exactly like the real thing while being
    sourced from nobody. The label travels inside the retrieved text so a model
    quoting a chunk quotes the disclaimer with it.

    **`state_code` is nullable** because a chunk about a jurisdiction carries
    its two-letter code and a chunk about something general belongs to no state
    and must not be filed under one. Every seeded row happens to have one; the
    column is shaped for the corpus this becomes, not the fifteen rows it is.

    **`(source, chunk_index)` is the identity.** A source is a document and its
    chunks are ordered within it, so neither column alone identifies a row.
    Without the constraint a re-seed run twice would return the same paragraph
    twice under one heading.

    Not PHI: this is the one table of the three that holds no claim-derived
    data. It still lives with them under one purge story, because separating
    "which of these three tables may Epic 8 delete from" into a per-table
    decision is how a cascade acquires an exception nobody re-checks.
    """

    __tablename__ = "knowledge_chunk"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    state_code: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    chunk_text: Mapped[str] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column(Integer)

    # Unnamed, so `NAMING_CONVENTION` decides — `TreatmentPlanStep`'s note on
    # why an explicit name here would disagree with what the convention renders
    # in the migration.
    __table_args__ = (UniqueConstraint("source", "chunk_index"),)


class KnowledgeEmbedding(Base):
    """One knowledge chunk's vector (Story 6.1, AC 2).

    `ClaimEmbedding` without the staleness half, and the asymmetry is the
    point: a chunk is immutable text written by a migration, so the only way
    its embedding stops being right is a model change — which `model` records
    and which the refresh's pending predicate acts on
    (`model IS DISTINCT FROM <the model answering now>`), invalidating every row
    at once rather than one row at a time. A claim's summary changes when a
    handler edits the claim, which is a per-row event, needs a per-row flag, and
    needs that flag to survive an edit landing mid-embed — hence `stale_at`
    there and nothing like it here.

    Pending work is represented the same way: migration 0041 inserts one row
    per chunk with a NULL vector, and `seed_knowledge_corpus` fills them.
    """

    __tablename__ = "knowledge_embedding"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("knowledge_chunk.id"), unique=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    model: Mapped[str | None] = mapped_column(Text)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            KNOWLEDGE_EMBEDDING_HNSW_INDEX,
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class AiInsight(Base):
    """One cached AI narrative for one claim and one kind (Story 6.2, AD-10/AD-12).

    Derived data in the AD-10 sense, exactly as `ClaimEmbedding` is: one
    computing path, nothing user-writable, every column a function of the claim
    rather than a fact anybody typed. `services/rag` is the sole writer (AD-12)
    — `services/rag/insights.py::store_insights` is the only function in the
    build that inserts or updates a row here, and `agents/` reaches it by
    calling that command rather than by holding a session of its own.

    **No `version` column, and this is the AD-4 statement for the table.**
    Compare-and-swap arbitrates concurrent writers of a mutable row, and this
    table has neither. It has one writer, whose writes are whole-row
    replacements of a derived value rather than read-modify-writes of a fact
    somebody typed: a refresh does not *edit* a narrative, it generates a new
    one and overwrites, so two runs racing on one claim leave the row holding
    one of two complete generations and never a blend of both. There is nothing
    for a CAS to protect. `ClaimEmbedding` reaches the same conclusion from the
    derived-data direction and `TimelineEvent`/`AuditEvent` from the
    append-only one.

    The corollary matters more than the rule: **because there is no version,
    there is no user-editable affordance anywhere**, and there must not be. A
    version column is what an inline edit sends back as `expectedVersion`, so
    its absence is the schema saying that FR-H-9's "never user-editable" is
    structural rather than a habit the UI keeps. A story that wanted a handler
    to correct a narrative would be proposing that AI output become claim data,
    which AD-10 rules out — the honest move is to regenerate the cache.

    **And no `stale` flag either**, which is where this table parts company with
    `ClaimEmbedding`. A stale embedding is a *wrong answer* — a similar-case
    search ranked against a claim's pre-edit summary returns the wrong
    neighbours with nothing on screen to say so — so it needs a per-row flag and
    a mid-flight guard. A stale insight is a *dated* answer, and it is dated
    beside its own `generated_at` on the card, which is the whole of AD-10's
    "always rendered with its generation timestamp". The reader can see how old
    it is; nothing has to decide for them.

    ## The columns

    `content` is JSONB holding a **validated structure**, not prose. Each kind
    has a Pydantic model (`agents/schemas.py`) that the generated narrative is
    parsed into before anything reaches this table, so the UI renders typed
    cards and Story 7.1 can aggregate the fraud kind portfolio-wide. Every
    money, date, count, score and verdict inside it was copied from
    deterministic service output (AD-2); the model supplied the prose around
    them and nothing else.

    `model` records the model that actually answered — read off the chat client
    rather than out of `Settings` a second time, `ClaimEmbedding.model`'s rule
    — because the card labels the narrative with it and "which model wrote
    this?" is the first thing anybody asks of a generated sentence.

    `generated_at` is on the wire for every card, always. A cached narrative
    without its timestamp is indistinguishable from a claim fact, which is the
    confusion AD-10 exists to prevent.

    **PHI-class (AD-11).** A narrative about a claim is claim data: it lives on
    the same encrypted volume, joins Epic 8's purge cascade, and never appears
    in a log line. `services/rag` logs claim ids, kinds and counts.
    """

    __tablename__ = "ai_insight"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claim.id"))
    kind: Mapped[InsightKind] = mapped_column(_enum(InsightKind, "insight_kind"))
    content: Mapped[dict] = mapped_column(JSONB)  # type: ignore[type-arg]
    model: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # The cache key, and the reason a refresh replaces rather than appends:
        # this table holds the latest generation per kind, not a history. It is
        # also the conflict target that makes `upsert_insight` one idempotent
        # `INSERT … ON CONFLICT DO UPDATE` instead of a select-then-branch with
        # a race in the gap, and it doubles as the index the `claim_id` foreign
        # key needs — PostgreSQL does not create one, and without it Story 8.1's
        # purge would scan this table once per deleted claim.
        UniqueConstraint("claim_id", "kind"),
    )


class AiInsightAttempt(Base):
    """When one claim's insights were last generated *for* — outcome or not.

    A cursor for the scheduled refresh's work queue, not a record of anything a
    reader sees. `select_claims_needing_insights` orders by `attempted_at` with
    never-attempted claims first, so a claim that has just been tried moves to
    the back of the queue whatever came of it.

    **That is the whole reason the table exists.** The queue's membership test
    is "missing at least one kind", and a kind whose generation fails writes no
    `ai_insight` row at all — so a claim the model reliably refuses stays
    pending for ever, and under a stable `ORDER BY claim.id` it sat at the head
    of every batch, re-spending its completions each tick and (because the run
    stops at the first unavailable model server) potentially stopping every
    other claim from ever generating. Recording the attempt turns that into a
    backoff whose interval is "everything else first" (review of Story 6.2, H4).

    **One row per claim, keyed by the claim**, replaced in place. No surrogate
    id, no history and no failure counter: this is a cursor, the record of what
    a refresh actually wrote is `audit_event` (AD-4), and a counter would be a
    second piece of state with a "when does it reset?" question attached that
    the ordering already answers.

    No `version` column, `AiInsight`'s reason and more bluntly: nothing reads
    this but the queue, nothing edits it, and a whole-row replacement by a
    single writer has nothing for a CAS to protect.
    """

    __tablename__ = "ai_insight_attempt"

    claim_id: Mapped[int] = mapped_column(ForeignKey("claim.id"), primary_key=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
