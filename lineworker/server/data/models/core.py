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
    BodyRegion,
    ClaimStatus,
    CommStatus,
    Disability,
    DocType,
    Gender,
    RecoveryWindow,
    ReturnStatus,
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
    """

    __tablename__ = "document"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claim.id"), index=True)
    name: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[DocType] = mapped_column(_enum(DocType, "doc_type"))
    filed_date: Mapped[date | None] = mapped_column(Date)
    blob_key: Mapped[str | None] = mapped_column(Text)
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
