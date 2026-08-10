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
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from data.models.base import Base
from data.models.enums import (
    ClaimStatus,
    CommStatus,
    Disability,
    Gender,
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
    recovery: Mapped[str] = mapped_column(Text)  # recovery-window text
    surgery_required: Mapped[bool] = mapped_column(Boolean)
    contraindications: Mapped[str] = mapped_column(Text)

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
