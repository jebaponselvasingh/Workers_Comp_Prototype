"""Claim reads — the first repository that enforces AD-7, and the template
every later one copies.

Three rules hold here, and the tests in `tests/test_scoped_repository.py`
assert each of them structurally rather than by example:

1. **A caller context is mandatory by signature.** Every function takes
   `ctx: CallerContext` as a required positional parameter. No default, no
   `| None`, no keyword-only escape hatch — because a default is all it
   takes for an unscoped query to look like a normal one in review.

2. **The employer filter is applied unconditionally.** `employer_scope()`
   is the only place scope becomes SQL, and it always returns a predicate:
   for `ALL_EMPLOYERS` that predicate is `TRUE`, a tautology rather than an
   omitted `WHERE`. `if ctx.scopes_all_employers: skip the filter` would
   produce identical rows today and lose the guarantee the first time
   someone adds a second condition to the query.

3. **Nothing branches on the caller's role.** Scope gates visibility; role
   gates capability (which commands and routes are available), and that
   decision belongs in the API and service layers. A repository that widens
   its filter for supervisors re-implements scoping a second time, next to
   the first, with nothing keeping the two in agreement.

Predicates for *what* is being counted (a stage, a risk band) arrive from
`services/`, because the repository must not know what "high risk" means —
that is the derivations registry's job (AD-10). Those predicates are ANDed
with the scope filter and can only ever narrow the result.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from typing import Any, Final, cast

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from data.context import AllEmployers, CallerContext
from data.models import (
    AdditionalInjury,
    AppUser,
    Bill,
    Claim,
    DiaryNote,
    Document,
    Employee,
    Employer,
    Expense,
    Meeting,
    PaymentScheduleWeek,
    Photo,
    TimelineEvent,
    TreatmentPlanStep,
)


def employer_scope(ctx: CallerContext) -> ColumnElement[bool]:
    """The AD-7 employer predicate — always a predicate, never a no-op.

    An unbounded scope compiles to `TRUE`; a bounded one to `employer_id IN
    (…)`; an empty book to a predicate that matches nothing (which is the
    correct reading of "assigned to no employers", and the reason
    `ALL_EMPLOYERS` is a distinct type rather than an empty collection).
    """
    if isinstance(ctx.employer_ids, AllEmployers):
        return sa.true()
    return Claim.employer_id.in_(ctx.employer_ids)


async def list_claims(db: AsyncSession, ctx: CallerContext) -> Sequence[Claim]:
    """Every claim in the caller's book, in business-id order.

    Story 1.4 uses this only to prove scoping; Story 2.1 adds the filters,
    ordering and paging the queue needs. Ordering is fixed here rather than
    left to the database so a paged version cannot silently repeat rows.
    """
    rows = await db.scalars(sa.select(Claim).where(employer_scope(ctx)).order_by(Claim.claim_id))
    return rows.all()


async def select_claim_columns(
    db: AsyncSession,
    ctx: CallerContext,
    columns: Sequence[InstrumentedAttribute[Any]],
) -> Sequence[sa.Row[Any]]:
    """The caller's claims, projected to the columns a service asks for.

    The counterpart to `count_claims_matching` for aggregates the database
    cannot express in one `COUNT(… ) FILTER (…)` — the SLA strip (Story
    1.5) averages four different subsets of the same scoped set, so it
    reads five narrow columns and aggregates them in Python rather than
    issuing four scans.

    *Which* columns is the service's business (this module must not learn
    what an SLA duration is); *which rows* is this module's, and it is the
    same `employer_scope` predicate as everywhere else. `select_from(Claim)`
    for the same reason it is load-bearing below: the FROM clause is a
    property of the repository, never of the caller's expressions.
    """
    rows = await db.execute(
        sa.select(*columns).select_from(Claim).where(employer_scope(ctx)).order_by(Claim.claim_id)
    )
    return rows.all()


async def select_queue_rows(
    db: AsyncSession,
    ctx: CallerContext,
) -> Sequence[sa.Row[Any]]:
    """The caller's claims, projected to the columns a queue card needs.

    Two joins, because two of the card's four rows name things that are not
    on `claim`: the injured worker (`employee.name`) and the employer's
    short name. Both are inner joins — every claim has an employee and an
    employer by foreign key, so an outer join would only add a `None` branch
    that cannot happen and a reader would have to reason about.

    **No `predicate` parameter, unlike `count_claims_matching`.** There is
    nothing for one to do here. Seven of the queue's eight filters read
    derived values (`risk`, `payment_due`, `siu_review`) or are only
    meaningful next to one, and the priority ordering is Python arithmetic
    over a JDM parameter block — so a SQL narrowing would put half the rule
    here and half in `services/worklist`, which is the split AD-10 exists to
    prevent. An unused seam is not free: the next reader has to work out
    which of the two places a filter belongs in, and the answer is always
    the service. This module decides *which rows* — scope, and nothing
    else.

    Ordered by `claim_id` like every other read here: the service re-sorts by
    score, and a total, deterministic order underneath is what makes that
    sort stable — two claims with equal scores must not swap between
    requests, or a cursor into the group would repeat or skip one.
    """
    rows = await db.execute(
        sa.select(
            Claim.claim_id,
            Claim.stage,
            Claim.status,
            Claim.severity_score,
            Claim.froi_date,
            Claim.injury_type,
            Claim.surgery_required,
            Claim.litigation_flag,
            Claim.fraud_flag,
            Claim.fraud_score,
            Claim.return_status,
            Employee.name.label("worker_name"),
            Employer.short_name.label("employer_short_name"),
        )
        .select_from(Claim)
        .join(Employee, Claim.employee_id == Employee.id)
        .join(Employer, Claim.employer_id == Employer.id)
        .where(employer_scope(ctx))
        .order_by(Claim.claim_id)
    )
    return rows.all()


async def select_claim_detail(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
) -> sa.Row[Any] | None:
    """One claim by its `WC-nnnn` business id, or `None` — scoped (Story 2.2).

    **`None` for out of scope and `None` for absent, deliberately the same
    answer.** AD-7 is not only "a caller cannot read another employer's
    claim"; it is that they cannot *learn one exists*. Two different answers — a
    404 for a claim id nobody has and a 403 for one that belongs to somebody
    else — turns this endpoint into an oracle a curious caller can walk
    `WC-20000`…`WC-20999` through to enumerate the portfolio. The scope
    predicate is on the WHERE clause, so a claim outside the caller's book
    simply is not in the result set, and the route has one branch.

    Three joins for the header: the injured worker, the employer, and the
    handler (an `app_user`, aliased because a later join to the same table
    would otherwise collide). All inner — every claim has all three by
    foreign key, so an outer join would add `None` branches that cannot
    happen.
    """
    handler = sa.orm.aliased(AppUser)
    rows = await db.execute(
        sa.select(
            Claim,
            Employee.employee_id.label("employee_business_id"),
            Employee.name.label("worker_name"),
            Employee.role.label("worker_role"),
            Employer.name.label("employer_name"),
            Employer.short_name.label("employer_short_name"),
            handler.name.label("handler_name"),
        )
        .select_from(Claim)
        .join(Employee, Claim.employee_id == Employee.id)
        .join(Employer, Claim.employer_id == Employer.id)
        .join(handler, Claim.handler_id == handler.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
    )
    return rows.one_or_none()


async def select_timeline_events(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
) -> Sequence[TimelineEvent]:
    """A claim's timeline, oldest first — the order it was appended in.

    **Ordered by `id`, which is the append order.** The log has no total
    order in its own columns: `event_date` repeats within a claim and is null
    on every settlement event. See `TimelineEvent`'s docstring; the treatment
    overview shows the last six, so this order is load-bearing.

    **Scoped, even though the caller already resolved the claim.** The join
    back to `claim` and the `employer_scope` predicate are not redundancy
    theatre: this module's contract is that *every* read here applies the
    filter, and the structural test in `tests/test_scoped_repository.py`
    enforces it by walking the module. A child read that trusted its caller
    would be the one function in the file where scope was somebody else's
    problem.
    """
    rows = await db.scalars(
        sa.select(TimelineEvent)
        .select_from(TimelineEvent)
        .join(Claim, TimelineEvent.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .order_by(TimelineEvent.id)
    )
    return rows.all()


async def select_documents(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
) -> Sequence[Document]:
    """A claim's documents, in filing order (`id`) — scoped like everything else."""
    rows = await db.scalars(
        sa.select(Document)
        .select_from(Document)
        .join(Claim, Document.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .order_by(Document.id)
    )
    return rows.all()


async def select_photos(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
) -> Sequence[Photo]:
    """A claim's incident photos, in filing order (`id`) — scoped like everything else.

    `ORDER BY id` is the display order rather than a tiebreak: `photo` carries
    no `sort_order` because the grid renders the prototype's array order, which
    migration 0021 preserved by inserting in it. Nothing else in the row is a
    total order — captions repeat across the book and two photos of one claim
    routinely share a source line.
    """
    rows = await db.scalars(
        sa.select(Photo)
        .select_from(Photo)
        .join(Claim, Photo.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .order_by(Photo.id)
    )
    return rows.all()


async def select_payment_schedule(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
) -> Sequence[PaymentScheduleWeek]:
    """A claim's indemnity schedule, in week order — scoped like everything else.

    `ORDER BY week_no` rather than `id`, unlike every other child read here,
    and the difference is the table's: these rows are a materialized projection
    keyed by `(claim_id, week_no)`, so a week that was inserted late by a
    refresh (a schedule that lengthened) carries a higher `id` than weeks it
    precedes. Insertion order is the display order for `document` and `photo`
    because nothing else orders them; here the week number is the order, and it
    is what the table's unique constraint makes total.
    """
    rows = await db.scalars(
        sa.select(PaymentScheduleWeek)
        .select_from(PaymentScheduleWeek)
        .join(Claim, PaymentScheduleWeek.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .order_by(PaymentScheduleWeek.week_no)
    )
    return rows.all()


async def select_bills(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
) -> Sequence[Bill]:
    """A claim's medical bills, in seeded order (`id`) — scoped like everything else.

    `ORDER BY id` is the display order for `select_documents`' reason: the seed
    inserted each claim's bills in the prototype's emission order and nothing
    else in the row is a total order. Category would look like a natural sort
    key and is not — it is a grouping, and two claims' bills in category order
    would render in an order neither the prototype nor a handler expects.
    """
    rows = await db.scalars(
        sa.select(Bill)
        .select_from(Bill)
        .join(Claim, Bill.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .order_by(Bill.id)
    )
    return rows.all()


async def select_expenses(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
) -> Sequence[Expense]:
    """A claim's expenses, in seeded order (`id`) — `select_bills`' rule."""
    rows = await db.scalars(
        sa.select(Expense)
        .select_from(Expense)
        .join(Claim, Expense.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .order_by(Expense.id)
    )
    return rows.all()


async def select_schedule_week(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    week_no: int,
) -> PaymentScheduleWeek | None:
    """One week of one claim's schedule, or `None` — scoped (Story 3.4).

    `select_additional_injury`'s counterpart for the approval command, and it
    exists for the same reason: the compare-and-swapped UPDATE reports only
    "one row or none", and 404 (no such week) and 409 (somebody moved it first)
    are different answers to a handler. Reading the row back is the only way to
    tell them apart, and it happens only after the write has already failed.

    Addressed by `(claim, week_no)` rather than by a surrogate, which is the
    table's own unique constraint and the reason `ScheduleWeekResponse`
    publishes no `id`.
    """
    rows = await db.scalars(
        sa.select(PaymentScheduleWeek)
        .select_from(PaymentScheduleWeek)
        .join(Claim, PaymentScheduleWeek.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .where(PaymentScheduleWeek.week_no == week_no)
    )
    return rows.one_or_none()


async def select_bill(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    bill_id: int,
) -> Bill | None:
    """One medical bill of one claim, or `None` — scoped (Story 3.4).

    `select_document`'s shape, and its conflation of "no such row", "another
    claim's row" and "not your claim" into one answer, for the same reason: a
    line item is addressed by a dense surrogate id, so a route that
    distinguished them would let a caller size the portfolio's bill table.
    """
    rows = await db.scalars(
        sa.select(Bill)
        .select_from(Bill)
        .join(Claim, Bill.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .where(Bill.id == bill_id)
    )
    return rows.one_or_none()


async def select_expense(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    expense_id: int,
) -> Expense | None:
    """One expense of one claim, or `None` — `select_bill`'s rule (Story 3.4)."""
    rows = await db.scalars(
        sa.select(Expense)
        .select_from(Expense)
        .join(Claim, Expense.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .where(Expense.id == expense_id)
    )
    return rows.one_or_none()


#: The three tables `services/financials` owns (AD-12), and the two write
#: functions below are generic over them.
type PaymentRow = PaymentScheduleWeek | Bill | Expense
PAYMENT_TABLES: tuple[type[PaymentScheduleWeek], type[Bill], type[Expense]] = (
    PaymentScheduleWeek,
    Bill,
    Expense,
)


async def transition_payment_row_cas(
    db: AsyncSession,
    ctx: CallerContext,
    table: type[PaymentRow],
    *,
    row_id: int,
    expected_version: int,
    expected_statuses: frozenset[Any],
    new_status: Any,
) -> int:
    """Move one payment row's status under compare-and-swap. Returns rows changed.

    `update_claim_fields_cas`'s contract, over the three tables
    `services/financials` owns — and with **one predicate the claim's CAS does
    not have**: the row's current status must be in `expected_statuses`.

    That extra guard is AD-4's, quoted: "lifecycle/status updates additionally
    guard on expected current status". A version alone is not enough for a
    lifecycle move, because two callers reading the same version can want
    different transitions — and the failure it prevents is concrete: a week
    already approved into a batch could be approved again by a client holding
    a stale-but-matching version, emitting a second audit event for a decision
    that was made once.

    Generic over the table rather than written three times, because the three
    statements would be identical but for a class name, and the first one to
    drift would be the one that dropped the scope predicate. The status
    vocabularies differ (`ScheduleWeekStatus` for weeks, `LineItemStatus` for
    line items), so those arrive as values.

    **The scope predicate is here, exactly as on every read.** The service has
    already resolved the claim, and the filter still goes on the WHERE clause —
    `update_claim_fields_cas`'s argument, and here it also closes the same race
    it does there: scope is re-resolved per request, and this is the statement
    that decides.

    `version = version + 1` is a SQL expression, so the increment happens
    inside the same row lock as the predicate.
    """
    result = await db.execute(
        sa.update(table)
        .where(table.id == row_id)
        .where(table.version == expected_version)
        .where(table.status.in_(expected_statuses))
        .where(table.claim_id.in_(sa.select(Claim.id).where(employer_scope(ctx))))
        .values(status=new_status, version=table.version + 1)
        .execution_options(synchronize_session=False)
    )
    return int(cast(CursorResult[Any], result).rowcount)


async def pay_scheduled_rows(
    db: AsyncSession,
    ctx: CallerContext,
    table: type[PaymentRow],
    *,
    scheduled_status: Any,
    paid_status: Any,
) -> Sequence[sa.Row[Any]]:
    """The payment batch's one statement per table: `payment_scheduled` → `paid`.

    Returns `(id, claim_id)` for every row it moved — the surrogate claim id,
    which the command resolves to business ids in one further query so that
    each audit event can name a claim the way `AuditEvent.entity_id` always
    does.

    **A single UPDATE … WHERE status, not a SELECT then an UPDATE.** The status
    predicate is what makes the batch idempotent *by construction* rather than
    by bookkeeping: a second run selects nothing, because the first run's rows
    are no longer `payment_scheduled`. A read-modify-write would be a lost
    update waiting for two batch processes, which is precisely what AD-4
    forbids ("no command performs an unguarded read-modify-write").

    **No version predicate, and that is not an omission.** Compare-and-swap
    arbitrates between a caller's *stale read* and the current row; the batch
    has read nothing. Its guard is the status, and the status is stronger here:
    it is the exact set of rows that should be paid, whatever version each is
    at. `version` still increments, so a client holding one of these rows gets
    its 409 on the next write.

    **Scoped like every other query in this module** (AD-7). The batch runs as
    the system actor, whose `scope_all` makes the predicate `TRUE` — the
    tautology, not a skipped filter. Passing a *bounded* context here is
    meaningful rather than nonsense: it pays that book and no other, which is
    what an admin-triggered per-caller run would want.
    """
    result = await db.execute(
        sa.update(table)
        .where(table.status == scheduled_status)
        .where(table.claim_id.in_(sa.select(Claim.id).where(employer_scope(ctx))))
        .values(status=paid_status, version=table.version + 1)
        .returning(table.id, table.claim_id)
        .execution_options(synchronize_session=False)
    )
    return result.all()


async def select_claim_refs(
    db: AsyncSession,
    ctx: CallerContext,
    claim_pks: Sequence[int],
) -> dict[int, str]:
    """Surrogate claim ids → business ids, for rows already resolved in scope.

    The payment batch's second query: `pay_scheduled_rows` returns the rows it
    moved keyed by `claim.id`, and every audit event has to name a claim the
    way `AuditEvent.entity_id` always does — by its `WC-nnnn` business id.

    **Scoped like everything else, although the filter can never remove a
    row.** The primary keys handed in came from a statement run under this very
    context, so the predicate is satisfied by construction. It goes on the
    WHERE clause anyway, for `update_claim_fields_cas`' reason: "every query in
    this module applies the filter" is only an invariant while it has no
    exceptions, and a function that looked up claim identity *without* one
    would be the obvious thing for a later story to reach for with pks from
    somewhere else. The signature takes explicit keys rather than a predicate
    for the same reason — this cannot be pointed at the table at large.
    """
    if not claim_pks:
        return {}
    rows = await db.execute(
        sa.select(Claim.id, Claim.claim_id)
        .where(employer_scope(ctx))
        .where(Claim.id.in_(set(claim_pks)))
    )
    return {pk: ref for pk, ref in rows.all()}


async def select_document(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    document_id: int,
) -> Document | None:
    """One document of one claim, or `None` — scoped (Story 2.5).

    **`None` for "no such document", "not this claim's document" and "not your
    claim", deliberately the same answer** — `select_claim_detail`'s rule,
    applied one level down. The viewer addresses a document by surrogate id,
    and surrogate ids are dense: a caller walking 1…10000 against a route that
    distinguished "not yours" from "does not exist" would learn how many
    documents the portfolio holds and which ids are live, which is the
    enumeration AD-7 closes at the claim level and would be pointless to leave
    open here.

    The claim is named in the predicate as well as the document, so a document
    id that *is* in the caller's scope but belongs to a different claim does
    not resolve through this claim's URL. Without that, the path segment would
    be decoration and two claims' viewers would be interchangeable.
    """
    rows = await db.scalars(
        sa.select(Document)
        .select_from(Document)
        .join(Claim, Document.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .where(Document.id == document_id)
    )
    return rows.one_or_none()


async def select_additional_injuries(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
) -> Sequence[AdditionalInjury]:
    """A claim's secondary injuries, oldest first — scoped (Story 2.4).

    Ordered by `id`, which is capture order. Unlike `timeline_event` this
    table *could* be ordered by something else (severity, region), but the
    diagram's summary list reads as a log of what a handler recorded, and a
    list that re-ordered itself when a score was edited would move the ✕ a
    handler was reaching for.
    """
    rows = await db.scalars(
        sa.select(AdditionalInjury)
        .select_from(AdditionalInjury)
        .join(Claim, AdditionalInjury.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .order_by(AdditionalInjury.id)
    )
    return rows.all()


async def select_additional_injury(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    injury_id: int,
) -> AdditionalInjury | None:
    """One secondary injury of one claim, or `None` — scoped (Story 2.4).

    Exists so the remove command can tell a *stale* version from a row that
    is not there: the compare-and-swapped DELETE below reports only "one row
    or none", and 409 and 404 are different answers to the caller. Reading it
    back is the only way to distinguish them, and it is done *after* the
    delete has already failed, so the happy path pays nothing.
    """
    # `scalars().one_or_none()` rather than `scalar()`: the latter is typed
    # `Any`, which would let a wrong element type through silently.
    rows = await db.scalars(
        sa.select(AdditionalInjury)
        .select_from(AdditionalInjury)
        .join(Claim, AdditionalInjury.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .where(AdditionalInjury.id == injury_id)
    )
    return rows.one_or_none()


async def select_treatment_plan_steps(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
) -> Sequence[TreatmentPlanStep]:
    """A claim's treatment plan, in step order — scoped like everything else.

    By `step_no`, not by `id`: the step number is a real column here (unlike
    `timeline_event`, whose only order is the order it was appended in), and
    it is what the card numbers its rows from. Ordering by anything else
    would let the list render 1, 3, 2.
    """
    rows = await db.scalars(
        sa.select(TreatmentPlanStep)
        .select_from(TreatmentPlanStep)
        .join(Claim, TreatmentPlanStep.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .order_by(TreatmentPlanStep.step_no)
    )
    return rows.all()


async def insert_additional_injury_cas(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    expected_version: int,
    values: Mapping[str, Any],
) -> int | None:
    """Add one secondary injury, guarded on the claim's version — scoped (2.4).

    Returns the new row's id, or `None` when the claim is out of scope,
    absent, or has moved on from `expected_version`. Which of those it was is
    the service's question, and it answers it by re-reading — the same
    division of labour `update_claim_fields_cas` uses.

    **`INSERT … SELECT`, not `INSERT … VALUES`, and that is the whole point
    of the shape.** An INSERT has no WHERE clause, so the two things this
    write has to be guarded by — the caller's employer scope and the version
    they were looking at — would otherwise be a `SELECT` in Python followed
    by an unguarded insert: a read-modify-write with a window in it (AD-4).
    Selecting the claim row *as the source of the insert* puts both
    predicates inside the statement, so the guard and the write are one
    operation and a claim edited in the gap inserts nothing.

    **The literals are typed.** `body_key` is a native enum column, and an
    untyped parameter in an `INSERT … SELECT` reaches asyncpg with no type to
    encode it as. Taking each literal's type from the column it lands in also
    means a column that changes type does not need a second edit here.
    """
    columns = AdditionalInjury.__table__.c
    fields = ("body_key", "body_part", "injury_type", "severity_score")
    source = (
        sa.select(
            Claim.id,
            *[sa.literal(values[field], columns[field].type).label(field) for field in fields],
        )
        .select_from(Claim)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .where(Claim.version == expected_version)
    )
    inserted = await db.execute(
        sa.insert(AdditionalInjury)
        .from_select(["claim_id", *fields], source)
        .returning(AdditionalInjury.id)
    )
    return inserted.scalar_one_or_none()


async def delete_additional_injury_cas(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    injury_id: int,
    expected_version: int,
) -> sa.Row[Any] | None:
    """Remove one secondary injury under compare-and-swap — scoped (2.4).

    Returns the deleted row's columns, or `None` if nothing matched. The
    columns come back through `RETURNING` rather than from a prior read for a
    reason that is specific to a delete: the audit event has to record what
    was removed as its `before` diff (AD-4), and a value read *before* the
    statement is a value another writer could have changed in between — so
    the log would describe a row that never existed in that state. `RETURNING`
    reports what the statement actually deleted.

    The scope predicate is a subquery on `claim` rather than a join, because
    `DELETE … USING` is dialect-specific and this reads as what it is: delete
    this claim's injury, where "this claim" is resolved under the caller's
    scope exactly as every read in this module resolves it.
    """
    owner = (
        sa.select(Claim.id)
        .select_from(Claim)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .scalar_subquery()
    )
    deleted = await db.execute(
        sa.delete(AdditionalInjury)
        .where(AdditionalInjury.claim_id == owner)
        .where(AdditionalInjury.id == injury_id)
        .where(AdditionalInjury.version == expected_version)
        .returning(
            AdditionalInjury.body_key,
            AdditionalInjury.body_part,
            AdditionalInjury.injury_type,
            AdditionalInjury.severity_score,
        )
        # The ORM cannot evaluate this predicate in Python (it contains a
        # subquery), and there is nothing in the identity map worth
        # synchronising: the command re-reads the case file afterwards.
        .execution_options(synchronize_session=False)
    )
    return deleted.one_or_none()


async def update_claim_fields_cas(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    expected_version: int,
    values: Mapping[str, Any],
) -> int:
    """The first **write** in this module — compare-and-swap, scoped (2.3).

    Returns the number of rows the statement changed: `1` on success, `0`
    when the claim is out of scope, absent, or has moved on from
    `expected_version`. Which of those it was is the *service's* question and
    it answers it by re-reading; this function's contract is only "the row
    was updated, or it was not".

    Three things are load-bearing in the one statement below.

    **The scope predicate is here, exactly as on every read.** A caller that
    resolved the claim through `select_claim_detail` already knows it is in
    scope, and the filter still goes on the WHERE clause — because "every
    query in this module applies the filter" is the invariant, and the moment
    one write trusts its caller, the structural test in
    `tests/test_scoped_repository.py` is asserting something weaker than it
    reads. It also closes a real race: scope is re-resolved per request, but
    a read and a write are two statements, and this is the one that decides.

    **`version = version + 1` is computed by the database**, not read into
    Python and written back. `Claim.version + 1` compiles to a SQL
    expression, so the increment happens inside the same row lock as the
    predicate — which is what makes the compare-and-swap atomic rather than
    merely optimistic-looking (AD-4: "no command performs an unguarded
    read-modify-write").

    **The caller decides the columns, and cannot invent them.** `values` is
    the *effective patch* — already whitelisted, validated and normalised by
    `services/claims/edit.py`. Passing a mapping rather than a typed
    parameter per field is what lets a PATCH write only what changed;
    SQLAlchemy refuses an unknown key at statement-compile time, so the
    whitelist has a second, structural enforcement here even though the
    service is where the 422 comes from.
    """
    result = await db.execute(
        sa.update(Claim)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .where(Claim.version == expected_version)
        .values(**values, version=Claim.version + 1)
    )
    # `AsyncSession.execute` is typed as returning `Result`, which has no
    # `rowcount`; a DML statement always produces a `CursorResult`, which
    # does. The cast is the narrowing SQLAlchemy's own typing cannot express.
    return int(cast(CursorResult[Any], result).rowcount)


async def transition_claim_status_cas(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    *,
    expected_version: int,
    expected_statuses: frozenset[Any],
    new_status: Any,
) -> int:
    """Move a claim's `status` under compare-and-swap **and** a status guard (3.5).

    `transition_payment_row_cas`' contract over `claim`, and it exists for the
    same reason that one does rather than reusing `update_claim_fields_cas`:
    AD-4 says a lifecycle update "additionally guards on expected current
    status", and a status written through the generic field patch would be
    guarded only on the version.

    The two predicates fail on different histories, which is the thing worth
    being clear about. A version says "nobody has touched this claim since you
    read it"; the status set says "the transition you are asking for is
    available from where the claim is now". Approving an assessment somebody
    else approved a second ago fails the *version*; approving one on a case file
    a handler left open while the claim was denied — same version if nothing
    else wrote — fails the *status*. Without the second, the timeline would
    gain a second approval event for a decision that was made once, and the
    audit log would say a denied claim was approved.

    Returns rows changed: `1`, or `0` for out of scope, absent, moved on, or in
    a status this transition cannot start from. Which of the four it was is the
    service's question and it answers it by re-reading — `update_claim_fields_cas`'
    division of labour.

    **The scope predicate is on the WHERE clause**, exactly as on every read and
    on the other two writes here, and `version = version + 1` is a SQL
    expression so the increment happens inside the same row lock as the guards.
    """
    result = await db.execute(
        sa.update(Claim)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .where(Claim.version == expected_version)
        .where(Claim.status.in_(expected_statuses))
        .values(status=new_status, version=Claim.version + 1)
    )
    return int(cast(CursorResult[Any], result).rowcount)


async def mark_osha_logged_cas(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    *,
    expected_version: int,
) -> int:
    """Record the OSHA 300 entry as filed, guarded on both OSHA columns (3.5).

    A function of its own rather than `update_claim_fields_cas` with one value,
    because the guard is the point: `osha_recordable = true` and `osha_logged =
    false` are in the statement, so the row cannot come to say that a filing was
    made for an injury the statute never required one for, and a second click
    cannot write a second audit event for one that was already made.

    That is the same argument `transition_claim_status_cas` makes above with a
    different pair of columns — a completion flag is a lifecycle of exactly two
    states, and AD-4's extra rung applies to it for the same reason.

    Returns rows changed; `0` covers out of scope, absent, stale, not
    recordable, and already logged, and the command tells them apart by
    re-reading. Deliberately **not** four return values: this module's contract
    is "the row was updated, or it was not", and a repository that classified
    refusals would be deciding what each one means to a caller.
    """
    result = await db.execute(
        sa.update(Claim)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .where(Claim.version == expected_version)
        .where(Claim.osha_recordable.is_(True))
        .where(Claim.osha_logged.is_(False))
        .values(osha_logged=True, version=Claim.version + 1)
    )
    return int(cast(CursorResult[Any], result).rowcount)


async def update_document_review_cas(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    document_id: int,
    *,
    expected_version: int,
    expected_reviewed: bool,
    expected_confirmed: bool,
    values: Mapping[str, Any],
) -> int:
    """Move a document's review state under compare-and-swap and a state guard (3.5).

    **The review→confirm ordering is a WHERE clause here, not a disabled
    button.** Confirming asks for `reviewed = true AND confirmed = false`;
    marking reviewed asks for both false. The prototype expresses the same rule
    by returning early from `confirmDocument` when `!st.reviewed` (line 880) and
    by disabling the control — which is a rule the browser keeps, and therefore
    a rule an agent tool or a replayed request does not. Putting the expected
    state in the statement makes it a property of the table.

    The **claim** is named in the predicate as well as the document, for
    `select_document`'s reason: a document id that is in the caller's scope but
    belongs to a different claim must not resolve through this claim's URL, or
    the path segment is decoration.

    The scope predicate is a subquery on `claim` rather than a join, because
    `UPDATE … FROM` is dialect-specific and this reads as what it is — update
    this claim's document, where "this claim" is resolved under the caller's
    scope exactly as every read in this module resolves it.
    `delete_additional_injury_cas` takes the same shape for the same reason.
    """
    owner = (
        sa.select(Claim.id)
        .select_from(Claim)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .scalar_subquery()
    )
    result = await db.execute(
        sa.update(Document)
        .where(Document.claim_id == owner)
        .where(Document.id == document_id)
        .where(Document.version == expected_version)
        .where(Document.reviewed.is_(expected_reviewed))
        .where(Document.confirmed.is_(expected_confirmed))
        .values(**values, version=Document.version + 1)
        # The ORM cannot evaluate a predicate containing a subquery in Python,
        # and there is nothing in the identity map worth synchronising: the
        # command expires the session and re-reads the case file afterwards.
        .execution_options(synchronize_session=False)
    )
    return int(cast(CursorResult[Any], result).rowcount)


# --- Story 4.1: the diary aggregate's first table ------------------------


def meeting_scope(ctx: CallerContext) -> ColumnElement[bool]:
    """The AD-7 predicate for `meeting` — owner **and** employer scope.

    Two conditions, and neither is redundant.

    **Owner.** A meeting belongs to the handler who holds it, not to the claim
    it references, so `list_meetings` is a caller-scoped list rather than a
    child read-model of the case file. Two handlers whose books overlap (the
    seed puts two on John Deere) must not see each other's diaries, and
    `employer_scope` alone would let them.

    **Employer scope, on the linked claim.** The owner predicate would already
    be sufficient for *visibility* — you can only ever see your own rows — but
    scope is re-resolved per request (AD-7), so a handler whose book narrowed
    between scheduling a meeting and reading it back must stop seeing the claim
    it names. A row surviving that narrowing would republish a claim reference
    the caller has lost the right to. `claim_id IS NULL` passes: a meeting that
    names no claim is scoped by its owner and nothing else.

    Written as a helper rather than spelled at five call sites for
    `employer_scope`'s reason — the first statement that omitted half of it
    would look exactly like the other four.
    """
    return sa.and_(
        Meeting.app_user_id == ctx.user_id,
        sa.or_(
            Meeting.claim_id.is_(None),
            Meeting.claim_id.in_(sa.select(Claim.id).where(employer_scope(ctx))),
        ),
    )


def _meeting_query() -> sa.Select[Any]:
    """The columns a meeting card renders beside the row itself.

    Two joins, both **outer**, because `claim_id` is nullable — an inner join
    would silently drop every meeting that names no claim, which is the one
    case the ERD's `CLAIM |o--o{ MEETING` exists to allow.
    """
    return (
        sa.select(
            Meeting,
            Claim.claim_id.label("claim_business_id"),
            Employee.name.label("worker_name"),
        )
        .select_from(Meeting)
        .outerjoin(Claim, Meeting.claim_id == Claim.id)
        .outerjoin(Employee, Claim.employee_id == Employee.id)
    )


#: Midnight, the sort position an all-day meeting takes.
#:
#: **`COALESCE`, never `NULLS FIRST`** (Story 4.2). Both put an untimed meeting
#: at the head of its day, and only one of them is safe in a keyset: a NULL
#: member makes `tuple_(…) > tuple_(…)` evaluate to NULL rather than to false,
#: which PostgreSQL treats as "not matched" — so the row-value comparison would
#: silently drop every page that began at an all-day meeting. Substituting the
#: value the ordering already implies keeps the comparison NULL-free.
MEETING_ALL_DAY_SORT_TIME: Final[time] = time(0, 0)


def _meeting_sort_time() -> ColumnElement[time]:
    """`COALESCE(meeting_time, '00:00')` — the second member of the sort key.

    Private, and `MEETING_ALL_DAY_SORT_TIME` above is not: the *expression*
    belongs to the statements in this module, but the **value** it substitutes
    has to be recorded in the cursor by `services/claims/meetings.py`, and a
    service that wrote its own midnight would be a second place the
    substitution is spelled out. `tests/test_scoped_repository.py` also
    requires every public function here to take a `CallerContext`, which this
    one has no use for — a helper that takes no scope must not look like a
    read that forgot one.
    """
    return sa.func.coalesce(
        Meeting.meeting_time,
        sa.literal(MEETING_ALL_DAY_SORT_TIME, Meeting.meeting_time.type),
    )


async def select_meetings_page(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    after: tuple[date, time, int] | None,
    limit: int,
    day: date | None = None,
) -> Sequence[sa.Row[Any]]:
    """One page of the caller's meetings, oldest first — scoped (4.1, 4.2).

    **Ordered by `(meeting_date, COALESCE(meeting_time, '00:00'), id)`, and
    every member is load-bearing.** The date alone is a partial order: a handler
    routinely schedules two touchpoints on one day, two rows tie, and a keyset
    page that ended inside the tie would repeat one and drop the other (Story
    3.5's lesson). The `id` closes that. The *time* was added by Story 4.2 and
    is the one that changed behaviour: a day's meetings have to read down the
    clock, and the same ordering serves the whole diary and the one-day filter
    without a second sort or a second cursor shape. See
    `MEETING_ALL_DAY_SORT_TIME` on why the null is coalesced rather than
    ordered around.

    **Keyset, not offset.** `after` is the last row of the previous page as a
    `(date, time, id)` triple and the predicate is the row-value comparison
    against the same three expressions the sort uses. A meeting inserted or
    completed between two pages therefore cannot shift the window: an offset
    would, and a handler scheduling a meeting mid-scroll is exactly the case
    that produces one.

    `day` narrows to one calendar date — the viewer's local day, resolved by
    the browser and sent as a parameter, the same class of client input as
    `as_of`. It is a *filter on the one list*, not a second list: the Notes
    sub-tab's today's-meetings summary is this query with the parameter set, so
    there is no second endpoint and no second ordering to keep in step. With the
    filter applied the sort reduces to time-then-id for free.
    """
    statement = _meeting_query().where(meeting_scope(ctx))
    if day is not None:
        statement = statement.where(Meeting.meeting_date == day)
    if after is not None:
        last_date, last_time, last_id = after
        statement = statement.where(
            sa.tuple_(Meeting.meeting_date, _meeting_sort_time(), Meeting.id)
            > sa.tuple_(
                # Typed literals rather than bare Python values: a row-value
                # comparison hands both sides to the driver as parameters, and
                # an untyped `date` reaches asyncpg with nothing to encode it
                # as (`insert_additional_injury_cas`' lesson, one operator
                # over).
                sa.literal(last_date, Meeting.meeting_date.type),
                sa.literal(last_time, Meeting.meeting_time.type),
                sa.literal(last_id, Meeting.id.type),
            )
        )
    rows = await db.execute(
        statement.order_by(Meeting.meeting_date, _meeting_sort_time(), Meeting.id).limit(limit)
    )
    return rows.all()


async def count_meetings(db: AsyncSession, ctx: CallerContext, *, day: date | None = None) -> int:
    """How many meetings the caller has in the list being paged — scoped (4.1).

    A second statement rather than a window function on the page above,
    because `total` is the size of the whole list and the page is a slice of
    it: a `count(*) OVER ()` would answer the size of the *page's* result set,
    which is the number the envelope must not carry.

    `day` is taken for the same reason `select_meetings_page` takes it and must
    be passed with it: `total` describes *the list the caller asked for*, so a
    day-filtered page whose total counted the whole diary would tell the
    summary that there are forty meetings today.
    """
    statement = sa.select(sa.func.count()).select_from(Meeting).where(meeting_scope(ctx))
    if day is not None:
        statement = statement.where(Meeting.meeting_date == day)
    total = await db.scalar(statement)
    return int(total or 0)


async def count_meetings_matching(
    db: AsyncSession,
    ctx: CallerContext,
    predicate: ColumnElement[bool],
) -> int:
    """How many of the caller's meetings satisfy a predicate — scoped (4.2).

    **The predicate arrives from `services/`**, which is this module's opening
    rule: the greeting's "📅 N upcoming meetings" is a count of the *upcoming*
    ones, and what "upcoming" means is `services/derivations/meeting_horizon.py`'s
    single answer (AD-10). A repository that spelled `is_done IS false AND
    meeting_date >= :today` here would be the second copy of that rule, in the
    layer least likely to be re-read when the first one changes.

    The predicate is ANDed with the scope filter and can therefore only narrow,
    exactly as `count_claims_matching`'s buckets can.

    **Always the whole book, never a filtered day**, which is why there is no
    `day` parameter: the greeting's sentence is a statement about the diary,
    and a count that moved with the summary's filter would say something the
    sentence does not.
    """
    total = await db.scalar(
        sa.select(sa.func.count()).select_from(Meeting).where(meeting_scope(ctx)).where(predicate)
    )
    return int(total or 0)


async def select_meeting(
    db: AsyncSession,
    ctx: CallerContext,
    meeting_id: int,
) -> sa.Row[Any] | None:
    """One meeting of the caller's, or `None` — scoped (4.1).

    **`None` for absent, for somebody else's, and for one whose claim has left
    the caller's book, deliberately the same answer** — `select_claim_detail`'s
    rule one aggregate over. A meeting is addressed by a dense surrogate id, so
    a route that distinguished "not yours" from "does not exist" would let a
    caller walk 1…10000 and size the portfolio's diary.

    Exists so the two commands can tell a *stale* version from a row that is
    not there: their compare-and-swapped statements report only "one row or
    none", and 409 and 404 are different answers to a handler. It is read only
    *after* a write has already failed, so the happy path pays nothing —
    `select_additional_injury`'s division of labour.
    """
    rows = await db.execute(
        _meeting_query().where(meeting_scope(ctx)).where(Meeting.id == meeting_id)
    )
    return rows.one_or_none()


async def insert_meeting(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str | None,
    values: Mapping[str, Any],
) -> int | None:
    """Schedule one meeting, with the claim link resolved under scope (4.1).

    Returns the new row's id, or `None` when a `claim_business_id` was given
    and it names no claim in the caller's book. Which of "absent" or "not
    yours" it was is nobody's question — the command answers 404 either way,
    `select_claim_detail`'s rule.

    **`INSERT … SELECT` for the linked case**, `insert_additional_injury_cas`'
    shape and for its reason: an INSERT has no WHERE clause, so resolving the
    claim in Python and then inserting would be a read-modify-write with a
    window in it. Selecting the claim row *as the source of the insert* puts
    the scope predicate inside the statement, so a claim that leaves the
    caller's book in the gap inserts nothing.

    **`INSERT … VALUES` for the unlinked case**, and the asymmetry is the
    absence of anything to guard: with no claim there is no scope predicate to
    put inside a statement, and the owner is `ctx.user_id`, which no caller
    supplies. Forcing the unlinked insert through a one-row `SELECT` purely for
    symmetry would be a statement whose predicate is `TRUE`.

    **The literals are typed** in the linked branch. `meeting_type` is a native
    enum and `participants` is JSONB; an untyped parameter in an
    `INSERT … SELECT` reaches asyncpg with no type to encode it as. Taking each
    literal's type from the column it lands in also means a column that changes
    type does not need a second edit here.
    """
    fields = tuple(values)
    if claim_business_id is None:
        inserted = await db.execute(
            sa.insert(Meeting)
            .values(app_user_id=ctx.user_id, claim_id=None, **values)
            .returning(Meeting.id)
        )
        return inserted.scalar_one()

    columns = Meeting.__table__.c
    source = (
        sa.select(
            sa.literal(ctx.user_id, columns["app_user_id"].type).label("app_user_id"),
            Claim.id.label("claim_id"),
            *[sa.literal(values[field], columns[field].type).label(field) for field in fields],
        )
        .select_from(Claim)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
    )
    inserted = await db.execute(
        sa.insert(Meeting)
        .from_select(["app_user_id", "claim_id", *fields], source)
        .returning(Meeting.id)
    )
    return inserted.scalar_one_or_none()


async def complete_meeting_cas(
    db: AsyncSession,
    ctx: CallerContext,
    meeting_id: int,
    expected_version: int,
) -> int:
    """Mark one meeting done under compare-and-swap — scoped (4.1).

    Returns rows changed: `1`, or `0` for absent, somebody else's, out of
    scope, moved on from `expected_version`, or **already done**. Which of the
    five it was is the command's question and it answers it by re-reading —
    `update_claim_fields_cas`' division of labour.

    **`is_done IS false` is in the statement**, which is AD-4's extra rung for
    a lifecycle move: a completion is a two-state lifecycle, and a version
    alone would let a client holding a stale-but-matching version complete a
    meeting somebody already completed, emitting a second audit event for a
    decision that was made once. `mark_osha_logged_cas` guards the identical
    shape for the identical reason.

    `version = version + 1` is a SQL expression, so the increment happens
    inside the same row lock as the predicates.
    """
    result = await db.execute(
        sa.update(Meeting)
        .where(meeting_scope(ctx))
        .where(Meeting.id == meeting_id)
        .where(Meeting.version == expected_version)
        .where(Meeting.is_done.is_(False))
        .values(is_done=True, version=Meeting.version + 1)
        # The ORM cannot evaluate a predicate containing a subquery in Python,
        # and there is nothing in the identity map worth synchronising: the
        # command expires the session and re-reads the row afterwards.
        .execution_options(synchronize_session=False)
    )
    return int(cast(CursorResult[Any], result).rowcount)


async def delete_meeting_cas(
    db: AsyncSession,
    ctx: CallerContext,
    meeting_id: int,
    expected_version: int,
) -> sa.Row[Any] | None:
    """Remove one meeting under compare-and-swap — scoped (4.1).

    Returns the deleted row's columns, or `None` if nothing matched. The
    columns come back through `RETURNING` rather than from a prior read for
    `delete_additional_injury_cas`' reason, which is specific to a delete: the
    audit event has to record what was removed as its `before` diff (AD-4), and
    a value read *before* the statement is one another writer could have
    changed in between — so the log would describe a row that never existed in
    that state.

    `claim_id` comes back as the surrogate, not the `WC-nnnn` string. The
    command resolves it for the audit diff from the entity it read before the
    delete, so an audit row for a deleted meeting names a claim a purge can
    still find (`services/claims/injuries.py::_diff`'s argument).
    """
    deleted = await db.execute(
        sa.delete(Meeting)
        .where(meeting_scope(ctx))
        .where(Meeting.id == meeting_id)
        .where(Meeting.version == expected_version)
        .returning(
            Meeting.id,
            Meeting.claim_id,
            Meeting.meeting_type,
            Meeting.meeting_date,
            Meeting.meeting_time,
            Meeting.location,
            Meeting.notes,
            Meeting.participants,
            Meeting.is_done,
        )
        .execution_options(synchronize_session=False)
    )
    return deleted.one_or_none()


# --- Story 4.2: the diary aggregate's notes ------------------------------


def diary_note_scope(ctx: CallerContext) -> ColumnElement[bool]:
    """The AD-7 predicate for `diary_note` — **author scope, and only that**.

    **Author.** A note belongs to the handler who wrote it, not to the claim it
    is tagged to, so `list_diary_notes` is a caller-scoped list rather than a
    child read-model of the case file. Two handlers whose books overlap (the
    seed puts two on John Deere) must not read each other's working notes, and
    a supervisor over both must not either — a diary is not a management
    report, and `employer_scope` alone would make it one.

    **Employer scope is deliberately *not* here, unlike `meeting_scope`**, and
    the divergence is the story's own contract rather than an oversight. The
    I/O matrix's List-isolation row states that a note tagged to a claim now
    outside the caller's scope "is still A's own note and is still returned".
    This predicate used to AND in `employer_scope` on the tag, so a re-scoping
    silently deleted entries from a handler's own diary — a table with no edit,
    no delete and no other copy of what was written.

    The two tables answer differently because they are different kinds of
    record. A meeting is a *plan against a case file*, and its card republishes
    the worker's name, so a handler who has lost the file should lose the plan.
    A note is a handler's own account of work they did, and its card renders
    `📎 WC-nnnn` and nothing else — the claim reference it carries is the one
    the author typed it against, not a fact the read discovered for them (which
    is also why `_diary_note_query` no longer projects the worker's name).

    Scope still gates the **write**: `insert_diary_note` resolves the tag
    through `employer_scope` inside its statement, so a claim outside the
    caller's book is a 404 and no row is written. Accepting a tag and keeping a
    note are different questions, and only the first is about current scope.
    """
    return DiaryNote.app_user_id == ctx.user_id


def _diary_note_query() -> sa.Select[Any]:
    """The row, plus the one column a note card renders beside it.

    The join is **outer**, because `claim_id` is nullable — an inner join would
    silently drop every untagged note, which is the one case the ERD's
    `CLAIM |o--o{ DIARY_NOTE` exists to allow. `_meeting_query`'s shape and its
    lesson.

    **One join, where `_meeting_query` has two.** The second one there reaches
    `employee.name` for the meeting card's `WC-nnnn — Worker Name`; a note card
    renders `📎 WC-nnnn` and never the name, so joining for it shipped the
    injured worker's name on every row of every handler's diary with no
    consumer at the other end. AD-11's rule is that the payload carries what
    the surface renders.
    """
    return (
        sa.select(DiaryNote, Claim.claim_id.label("claim_business_id"))
        .select_from(DiaryNote)
        .outerjoin(Claim, DiaryNote.claim_id == Claim.id)
    )


async def select_diary_notes_page(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    after: tuple[datetime, int] | None,
    limit: int,
) -> Sequence[sa.Row[Any]]:
    """One page of the caller's notes, **newest first** — scoped (4.2).

    **Ordered by `(noted_at DESC, id DESC)`, and the descent is the whole
    point**: a diary is read from the top, and the prototype reverses its array
    for exactly this reason. `noted_at` alone is a partial order — two notes
    saved inside the same clock tick tie, and a keyset page that ended inside
    the tie would repeat one row and drop the other — so `id` closes it, and it
    descends *with* the timestamp because a keyset comparison has to run in the
    ordering's own direction.

    **Both the comparison and the sort are flipped**, which is the mistake this
    docstring exists to prevent: `>` with `ORDER BY … DESC` walks away from the
    page it just served and pages forward through nothing. `after` is the last
    row of the previous page, and the predicate is
    `(noted_at, id) < (last_noted_at, last_id)`.
    """
    statement = _diary_note_query().where(diary_note_scope(ctx))
    if after is not None:
        last_noted_at, last_id = after
        statement = statement.where(
            sa.tuple_(DiaryNote.noted_at, DiaryNote.id)
            < sa.tuple_(
                # Typed literals rather than bare Python values, for
                # `select_meetings_page`'s reason: a row-value comparison hands
                # both sides to the driver as parameters, and an untyped
                # `datetime` reaches asyncpg with nothing to encode it as.
                sa.literal(last_noted_at, DiaryNote.noted_at.type),
                sa.literal(last_id, DiaryNote.id.type),
            )
        )
    rows = await db.execute(
        statement.order_by(DiaryNote.noted_at.desc(), DiaryNote.id.desc()).limit(limit)
    )
    return rows.all()


async def count_diary_notes(db: AsyncSession, ctx: CallerContext) -> int:
    """How many notes the caller has in total — scoped (4.2).

    A second statement rather than a window function on the page above, for
    `count_meetings`' reason: `total` is the size of the whole list and the
    page is a slice of it.
    """
    total = await db.scalar(
        sa.select(sa.func.count()).select_from(DiaryNote).where(diary_note_scope(ctx))
    )
    return int(total or 0)


async def select_diary_note(
    db: AsyncSession,
    ctx: CallerContext,
    note_id: int,
) -> sa.Row[Any] | None:
    """One note of the caller's, or `None` — scoped (4.2).

    Not a route: there is no `GET /claims-diary/notes/{id}`, because the diary
    is read as a list and a second way to read one row would be a second place
    its shape is decided. It exists so `create_diary_note` can re-read what it
    just wrote through the same scoped query the list uses — the alternative,
    building the response from the values the command was handed, is how a
    payload starts disagreeing with what is stored.
    """
    rows = await db.execute(
        _diary_note_query().where(diary_note_scope(ctx)).where(DiaryNote.id == note_id)
    )
    return rows.one_or_none()


async def select_latest_note_at(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
) -> datetime | None:
    """When the caller last wrote a note about one claim, or `None` (4.2).

    The read behind the diary check-in's completion: `services/worklist/
    actions.py` stops firing "Log the weekly diary check-in" once a recent note
    exists, which is the entity-backed completion the module's own docstring
    insists on ("there is no 'completed actions' store, deliberately").

    **Scoped to the caller, not to the claim.** A note is its author's, so
    "has this been checked in on?" is answered from the reader's own diary.
    Counting *anybody's* note would publish the existence of a handler's private
    working record to whoever else read the claim, which is a leak rather than a
    convenience — that is the whole of the argument, and an earlier version of
    this docstring propped it up with a second claim ("a supervisor opening the
    same case file still sees the row") that is simply not true: `web/src/App.tsx`
    gates the workspace route to `handler`, so no supervisor or analyst reaches a
    case file at all. The scope is right; the illustration was false.

    **The join to `Claim` is the whole of the claim predicate**, and since
    `diary_note_scope` narrowed to the author alone it is also the only one.
    That is not a hole: this is called from `generate_actions`, which is
    already answering about a claim the caller can open, and the row it finds
    is the caller's own note either way. What the caller could learn from an
    out-of-scope argument is that *they themselves* once wrote a note about it,
    which is not a fact about anybody else's data.

    Returns the *maximum* `noted_at` rather than a boolean, so the seven-day
    window lives with the rule in `services/worklist` and this query knows
    nothing about it.
    """
    latest: datetime | None = await db.scalar(
        sa.select(sa.func.max(DiaryNote.noted_at))
        .select_from(DiaryNote)
        .join(Claim, DiaryNote.claim_id == Claim.id)
        .where(diary_note_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
    )
    return latest


async def insert_diary_note(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str | None,
    values: Mapping[str, Any],
) -> int | None:
    """Write one note, with the claim tag resolved under scope (4.2).

    Returns the new row's id, or `None` when a `claim_business_id` was given and
    it names no claim in the caller's book. Which of "absent" or "not yours" it
    was is nobody's question — the command answers 404 either way,
    `select_claim_detail`'s rule.

    **`INSERT … SELECT` for the tagged case**, `insert_meeting`'s shape and for
    its reason: an INSERT has no WHERE clause, so resolving the claim in Python
    and then inserting would be a read-modify-write with a window in it.
    Selecting the claim row *as the source of the insert* puts the scope
    predicate inside the statement, so a claim that leaves the caller's book in
    the gap inserts nothing.

    **`INSERT … VALUES` for the untagged case**, and the asymmetry is the
    absence of anything to guard: with no claim there is no scope predicate to
    put inside a statement, and the author is `ctx.user_id`, which no caller
    supplies.

    **The literals are typed** in the tagged branch: an untyped parameter in an
    `INSERT … SELECT` reaches asyncpg with no type to encode it as, and
    `noted_at` is a `timestamptz`. Taking each literal's type from the column it
    lands in also means a column that changes type needs no second edit here.
    """
    fields = tuple(values)
    if claim_business_id is None:
        inserted = await db.execute(
            sa.insert(DiaryNote)
            .values(app_user_id=ctx.user_id, claim_id=None, **values)
            .returning(DiaryNote.id)
        )
        return inserted.scalar_one()

    columns = DiaryNote.__table__.c
    source = (
        sa.select(
            sa.literal(ctx.user_id, columns["app_user_id"].type).label("app_user_id"),
            Claim.id.label("claim_id"),
            *[sa.literal(values[field], columns[field].type).label(field) for field in fields],
        )
        .select_from(Claim)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
    )
    inserted = await db.execute(
        sa.insert(DiaryNote)
        .from_select(["app_user_id", "claim_id", *fields], source)
        .returning(DiaryNote.id)
    )
    return inserted.scalar_one_or_none()


async def count_claims_matching(
    db: AsyncSession,
    ctx: CallerContext,
    buckets: Mapping[str, ColumnElement[bool]],
) -> dict[str, int]:
    """Count the caller's claims once per named predicate, in one query.

    `COUNT(*) FILTER (WHERE …)` rather than one round trip per bucket:
    the three top-bar tiles are three counts over the same scoped set, and
    running them separately would let the numbers disagree with each other
    if a claim changed between queries.

    Every bucket is evaluated *inside* the scoped set — the scope predicate
    is on the statement's `WHERE`, so a bucket predicate can only narrow.

    `select_from(Claim)` is load-bearing, not decoration. SELECT with only
    aggregate columns has no table of its own, so SQLAlchemy infers the
    FROM clause from whatever the expressions mention — and both an
    unbounded scope (`WHERE true`) and a tautology bucket (`sa.true()`)
    mention nothing. A caller asking for a single `{"caseload": sa.true()}`
    count as a full-portfolio supervisor would otherwise compile to
    `SELECT count(*) FILTER (WHERE true) WHERE true`, which is a valid
    query over no rows at all: it answers 1, for everybody, with the scope
    predicate silently dropped. Naming the table makes the FROM clause a
    property of the repository rather than of the caller's predicates.
    """
    if not buckets:
        return {}

    names = list(buckets)
    row = (await db.execute(count_statement(ctx, buckets, names))).one()
    return {name: int(value) for name, value in zip(names, row, strict=True)}


def count_statement(
    ctx: CallerContext,
    buckets: Mapping[str, ColumnElement[bool]],
    names: Sequence[str],
) -> sa.Select[tuple[int, ...]]:
    """The statement `count_claims_matching` runs, exposed so its shape can
    be asserted without a database (see the FROM-clause note above)."""
    return (
        sa.select(*[sa.func.count().filter(buckets[name]) for name in names])
        .select_from(Claim)
        .where(employer_scope(ctx))
    )
