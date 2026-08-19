"""`ai_insight` reads and its single owner's write — AD-7 applies to a cache of
model output exactly as it does to a claim column.

(Two tables, since the Story 6.2 review: `ai_insight` itself and
`ai_insight_attempt`, the one-row-per-claim cursor the pending queue orders by.
The second is scoped like the first, for a reason that is not obvious — it holds
no content at all — and is given below `select_claims_needing_insights`.)

The third scoped repository, after `claims` (Story 1.4) and `embeddings`
(Story 6.1), and the argument for scoping it is the one `embeddings.py` makes
about vector search turned up a notch. An insight *is* claim data: its content
was generated from a claim's clinical narrative, its reserve, its bills and its
fraud score, so serving one across an employer partition is the same leak as
serving the case file it was cut from. Every function here reaches `claim`
through `employer_scope(ctx)` — the predicate `data/repositories/claims.py`
defines and every other repository imports — including the write, and there is
no second predicate in this file.

That last clause is what `tests/test_scoped_repository.py`'s two structural
guards are for, and why this module was added to their list when it landed.
Their own docstring says they exist to stop "Story 6.x quietly adding an
unscoped query"; a guard still bound to the two earlier modules when this one
arrived would have let exactly that happen, in the file whose rows are the
hardest in the codebase to eyeball for scope because nothing in a JSONB blob
looks like it belongs to anybody.

## The write is scoped too, and the tautology is the point

`upsert_insight` reaches `claim` through `employer_scope(ctx)`. For the
scheduled refresh that predicate compiles to `TRUE`, because the system actor's
unbounded scope makes it AD-7's tautology rather than a skipped filter — so it
costs the job nothing. What it buys is the interactive path: a handler pressing
Refresh on their own claim cannot, even by holding another claim's primary key,
write a narrative onto a case file outside their book.

It is expressed as `INSERT … SELECT … ON CONFLICT (claim_id, kind) DO UPDATE`,
one statement doing three jobs, `embeddings.py`'s shape and for its reasons.
The `SELECT` is where the scope predicate lives, so an out-of-scope claim
produces no source row and therefore no insert *and* no conflict — the update
half never fires either, and the call is a no-op reporting zero rows. The
`ON CONFLICT` half is what makes a re-refresh replace rather than duplicate,
which is the cache semantics migration 0042's unique constraint exists for. And
there is no read-then-write gap inside the statement for a second writer to
land in.

There is no `stale_at`-style mid-flight guard here, and the asymmetry with
`claim_embedding` is deliberate rather than an omission. That guard exists
because a stale vector is a *wrong* answer nothing on screen discloses; a
narrative generated a minute before an edit is a *dated* answer, rendered
beside its own `generated_at` (AD-10). Two refreshes racing on one claim leave
the row holding one of two complete generations, never a blend, because the
statement replaces the row whole.

## Why `select_claim_insights` does not answer "does this claim exist?"

It returns the rows a claim has, which is the empty list both for a claim with
no insights yet and for a claim the caller cannot see. Those are different
facts and the API answers them differently — an empty list is four
`not_generated` cards, an invisible claim is a 404 — so the existence question
is asked separately by `select_insight_claim_pk`, which is scoped and which
answers `None` for "absent" and `None` for "not yours" alike. That is
`select_claim_detail`'s single-answer rule, and it carries the same weight here:
two different answers would make the insights route an oracle for enumerating a
portfolio the caller cannot read.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models import AiInsight, AiInsightAttempt, Claim
from data.models.enums import InsightKind
from data.repositories.claims import employer_scope


async def select_insight_claim_pk(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
) -> int | None:
    """The claim's surrogate key, by business id, or `None` — scoped.

    A narrow read of one column rather than a reuse of
    `claim_repo.select_claim_detail`, which joins three tables to build a case
    file header nobody here wants. What the insight surface needs from the
    claim is the key its rows hang off and the answer to "may this caller see
    it at all".

    **`None` for out of scope and `None` for absent, deliberately the same
    answer.** See the module docstring.
    """
    found: int | None = await db.scalar(
        sa.select(Claim.id).where(employer_scope(ctx)).where(Claim.claim_id == claim_business_id)
    )
    return found


async def select_claim_insights(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
) -> Sequence[AiInsight]:
    """Every cached narrative on one claim, in kind order — scoped.

    Ordered by `kind` so two reads of one claim return the rows in one order.
    Nothing downstream depends on it — the API keys the payload by kind and the
    tab renders four fixed cards — but a list whose order is the database's
    happens to be the list a test has to sort before asserting, and an ordering
    nobody can rely on is one somebody eventually does.

    An empty list is the normal state of a claim nobody has generated for. It
    is *also* what a caller outside the claim's employer partition gets, which
    is why the route resolves the claim through `select_insight_claim_pk`
    first: an empty list rendered as four empty cards would tell a stranger the
    claim exists.
    """
    rows = await db.scalars(
        sa.select(AiInsight)
        .select_from(AiInsight)
        .join(Claim, AiInsight.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .order_by(AiInsight.kind)
    )
    return rows.all()


async def select_claims_needing_insights(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    limit: int | None,
    kinds: int,
) -> Sequence[str]:
    """Business ids of claims missing at least one kind — scoped, oldest first.

    "Pending" is one fact rather than the three `claim_embedding` distinguishes:
    a claim whose `ai_insight` row count is below `kinds` has a card the tab
    would render as `not_generated`. Passed in rather than computed from the
    enum here, because `data/` describing how many kinds the application
    believes in would be a second copy of a decision `InsightKind` already
    holds — and a repository that hardcoded four would keep selecting nothing
    on the day a fifth arrived.

    **A claim whose four rows are old is deliberately *not* pending**, and it is
    the one design decision in this file worth arguing. An insight is a cache
    rendered with its `generated_at` (AD-10), so an hour-old narrative is a
    dated answer the reader can see the age of, not a wrong one. Re-generating
    the whole portfolio on a timer would spend four chat completions per claim
    per interval, for ever, to replace text that says the same thing — against a
    model server that answers one request at a time and that Story 6.3 is about
    to put a handler's chat in front of. So the scheduled job's job is to
    *finish the first pass*, and re-generation is the on-demand path's: a
    handler who has just edited a claim presses Refresh and gets four fresh
    cards for that claim. Both enter the same command (AD-12); only the
    selection differs.

    **Ordered least-recently-attempted first, and that ordering is a safety
    property rather than a nicety.** This shipped as a plain `ORDER BY
    claim.id`, which is stable — and stable is exactly wrong here, because
    pending-ness is decided by rows a *failure* never writes. A claim whose kind
    the model reliably refuses (a schema it cannot satisfy, a narrative field it
    keeps leaving empty) is still missing a kind after every attempt, so it was
    selected again on the very next tick, at the same position, for ever. It
    re-spent its completions each time; and because `refresh_pending_insights`
    stops at the first claim whose failure was an unavailable model server, one
    such claim at position one could stop the whole portfolio from ever
    generating. That is the review of Story 6.2's H4.

    `ai_insight_attempt` is the fix: `store_insights` records the attempt before
    it generates, so a claim that has just been tried sorts behind every other
    pending claim whatever came of it. Never-attempted claims sort first
    (`NULLS FIRST` — the natural reading of "has waited longest"), and `Claim.id`
    is the tie-break so a batch is still deterministic within one instant, which
    is what AD-15's reproducibility rests on. A permanent failure now costs one
    slot per full lap of the pending set instead of the whole batch.

    Both subqueries are correlated rather than `GROUP BY` joins, so a claim with
    *no* insight rows and no attempt row is selected without an outer join whose
    NULL group has to be coalesced.

    `limit=None` means "everything pending", which is what the e2e admin
    trigger passes so a spec does not have to know the batch size; the
    scheduled job passes `Settings.insight_refresh_batch_size`.
    """
    generated = (
        sa.select(sa.func.count())
        .select_from(AiInsight)
        .where(AiInsight.claim_id == Claim.id)
        .scalar_subquery()
    )
    attempted = (
        sa.select(AiInsightAttempt.attempted_at)
        .where(AiInsightAttempt.claim_id == Claim.id)
        .scalar_subquery()
    )
    statement = (
        sa.select(Claim.claim_id)
        .select_from(Claim)
        .where(employer_scope(ctx))
        .where(generated < kinds)
        .order_by(sa.nullsfirst(attempted.asc()), Claim.id)
    )
    if limit is not None:
        statement = statement.limit(limit)
    rows = await db.scalars(statement)
    return rows.all()


async def record_insight_attempt(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_pk: int,
    attempted_at: datetime,
) -> int:
    """Mark one claim as just-attempted. Scoped, idempotent, no commit.

    The queue's cursor — `select_claims_needing_insights` orders by the column
    this writes, so a claim that has just been tried goes to the back of the
    pending set whatever the outcome. See that function on the starvation it
    prevents.

    **Written before the generation rather than after it**, which is the only
    ordering that helps: the case worth protecting against is the run that does
    not finish, and an attempt recorded at the end is an attempt a crashed or
    timed-out run never records at all.

    `upsert_insight`'s statement shape and for its reasons: `INSERT … SELECT …
    ON CONFLICT DO UPDATE`, with the scope predicate inside the `SELECT`, so a
    caller outside the claim's book produces no source row and therefore no
    write — reported as zero rows, the same silence a scoped read gives. There
    is no read-then-write gap for a second refresh to land in.
    """
    source = sa.select(Claim.id, sa.literal(attempted_at)).where(
        employer_scope(ctx), Claim.id == claim_pk
    )
    statement = pg_insert(AiInsightAttempt).from_select(["claim_id", "attempted_at"], source)
    result = await db.execute(
        statement.on_conflict_do_update(
            index_elements=[AiInsightAttempt.claim_id],
            set_={"attempted_at": statement.excluded.attempted_at},
        )
    )
    return int(cast(CursorResult[Any], result).rowcount)


async def upsert_insight(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_pk: int,
    kind: InsightKind,
    content: Mapping[str, Any],
    model: str,
    generated_at: datetime,
) -> int:
    """Store one claim's narrative for one kind. Scoped, idempotent, no commit.

    Returns the number of rows the statement affected — 0 when the claim is not
    in the caller's book, which is the same silence a scoped read gives and for
    the same reason.

    **No commit**, which is `audit.record`'s and `mark_claim_stale`'s contract
    and is load-bearing here for a specific reason: `store_insights` writes up
    to four rows and one audit event per row, and a partially committed refresh
    would leave a claim advertising a narrative whose AD-4 event was rolled
    back. The command owns the transaction boundary.

    **Replaces rather than appends**, which is the cache semantics migration
    0042's `UNIQUE (claim_id, kind)` exists for: `ON CONFLICT DO UPDATE` sets
    all three mutable columns together, so a row is never a new `content` under
    an old `generated_at`. A refresh that failed for this kind writes nothing at
    all and the previous generation keeps its own timestamp — which is what the
    card is supposed to show while the model server is down.
    """
    source = sa.select(
        Claim.id,
        sa.literal(kind.value, type_=AiInsight.kind.type),
        sa.literal(dict(content), type_=AiInsight.content.type),
        sa.literal(model),
        sa.literal(generated_at),
    ).where(employer_scope(ctx), Claim.id == claim_pk)
    statement = pg_insert(AiInsight).from_select(
        ["claim_id", "kind", "content", "model", "generated_at"], source
    )
    result = await db.execute(
        statement.on_conflict_do_update(
            index_elements=[AiInsight.claim_id, AiInsight.kind],
            set_={
                "content": statement.excluded.content,
                "model": statement.excluded.model,
                "generated_at": statement.excluded.generated_at,
            },
        )
    )
    # `cast` for `claims.py`'s reason: a DML statement always produces a
    # `CursorResult`, but `AsyncSession.execute` is typed as returning the
    # narrower `Result`, which has no `rowcount`.
    return int(cast(CursorResult[Any], result).rowcount)
