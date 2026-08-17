"""Core entities: employer, employee, claim, app_user, user_employer_assignment,
audit_event (Story 1.2), glossary_term (Story 1.6).

Naming is canonical per docs/WC_Feature_Element_Details.xlsx. Money columns
are integer cents (Excel names kept, values in cents). No derived value
(days_open, risk, severity band, totals, flags) is a column — AD-10.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Integer,
    Text,
    UniqueConstraint,
    func,
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
    ExpenseCategory,
    Gender,
    LineItemStatus,
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
