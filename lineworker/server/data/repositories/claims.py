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
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from data.context import AllEmployers, CallerContext
from data.models import (
    AdditionalInjury,
    AppUser,
    Claim,
    Document,
    Employee,
    Employer,
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
