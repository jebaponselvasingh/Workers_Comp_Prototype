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
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from data.context import AllEmployers, CallerContext
from data.models import Claim, Employee, Employer


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
