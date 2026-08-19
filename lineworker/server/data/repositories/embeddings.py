"""Vector reads and the three embedding tables' writes — AD-7 applies to a
similarity search exactly as it does to a `SELECT … WHERE stage = 'intake'`.

That sentence is the whole design. A vector search *feels* like a different
kind of query — an operator nobody can read, an index nobody can eyeball, a
result set ordered by a float — and the temptation is to treat it as a
special case that gets its scoping somewhere else. It does not. Every function
here that reaches a claim row joins `claim` and applies `employer_scope(ctx)`,
the same predicate `data/repositories/claims.py` defines and every other
repository imports. There is no second predicate in this file, and adding one
would be the failure `tests/test_scoped_repository.py` exists to catch —
which is why that module's two structural guards were extended to run over
this one when it landed (Story 6.1).

## The two knowledge functions take a context they do not filter on

`select_similar_chunks`, `select_pending_knowledge_embeddings` and
`write_knowledge_embedding` reach `knowledge_chunk` and `knowledge_embedding`,
which have no employer column, no claim relationship and no PHI: fifteen rows
of clearly-labelled synthetic labour-law text, identical for every persona.
There is nothing for a filter to narrow.

They still require a `CallerContext`, and that is a deliberate divergence from
`statutory_forms.py`, `glossary.py` and `state_rates.py` — the three
repositories that carve themselves out of AD-7 entirely by taking no context at
all. Those modules argue, correctly, that an accepted-and-ignored context reads
to the next author as evidence that scoping was enforced when nothing was.

The reason this module lands the other way is that these functions sit in a
file whose *other* functions are claim-scoped, and they are reached from the
same service, in the same request, as the claim-scoped ones. A knowledge search
is one half of a copilot answer whose other half is a similar-case search over
the caller's book; a signature that dropped the context between them would make
"which of these two calls was scoped?" a question about which function name you
happened to read. AD-7's discipline is "no code path may query claim data
without one", and the cheapest way to keep that true of a module that does both
is for the parameter to be present throughout. The docstrings say which
functions use it and which do not, so nothing here is silently decorative.

## Writes are scoped too, and the tautology is the point

`upsert_claim_embedding_stale` and `write_claim_embedding` both reach `claim`
through `employer_scope(ctx)` — you cannot mark stale, or embed, a claim you
cannot see. For the scheduled refresh that predicate compiles to `TRUE`,
because the system actor's `scope_all` makes it a tautology (AD-7's rule that
`ALL` widens the predicate and never removes it). So the filter costs the
refresh nothing and buys the interactive path a guarantee: a handler's edit
command marks *their* claim stale and could not, even by passing another
claim's primary key, touch a row outside their book.

Both are expressed as `INSERT … SELECT … ON CONFLICT (claim_id) DO UPDATE`,
which is one statement doing three jobs. The `SELECT` is where the scope
predicate lives, so an out-of-scope claim produces no source row and therefore
no insert *and* no conflict — the update half never fires either, and the whole
call is a no-op that reports zero rows. The `ON CONFLICT` half is what makes it
idempotent against migration 0041's pre-created rows, which is the normal case.
And there is no read-then-write gap *within either statement* for a second
writer to land in.

There is, however, a gap the statements cannot close, and it is worth naming
because a single-statement upsert reads as though it had: between the refresh
selecting a pending row and writing its vector sits a model round trip that may
take a minute. A handler's edit commits inside it, and the vector arrives
describing a claim that no longer exists in that form. `claim_embedding.stale_at`
is the marker that survives that window — `write_claim_embedding` clears `stale`
only if the marker has not moved. See that function and
`data/models/core.py::ClaimEmbedding`.

## Vectors in, never text

`select_similar_claims` and `select_similar_chunks` take `list[float]`. The
embedding of a query happens in `services/rag`, one layer up, and that is AC
2's literal rule rather than a preference: the moment a repository accepts a
string it becomes the second place in the build that decides which model
embedded what, and the "sole embeddings client" property AD-5 rests on stops
being checkable by reading one file.

Ordering is by cosine distance (`<=>`), matching the operator class both HNSW
indexes were built with (migration 0040). A mismatch there would leave the
index valid, present and never chosen.
"""

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models import (
    AdditionalInjury,
    Claim,
    ClaimEmbedding,
    Employer,
    KnowledgeChunk,
    KnowledgeEmbedding,
)
from data.repositories.claims import employer_scope

#: The label the distance column carries in every similarity result. Named once
#: so the repository and the service spell it the same way; a `Row` attribute
#: typo is a runtime `AttributeError` in a code path that only runs when a model
#: server is up, which is the worst place to discover one.
DISTANCE = "distance"


# --- claim embeddings: the composer's inputs ----------------------------


async def select_claim_embedding_source(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
) -> sa.Row[Any] | None:
    """One claim and its employer, by business id, or `None` — scoped.

    The composer needs `employer.sector` alongside the claim's own columns, and
    `claim_repo.select_claim_detail` does not select it (it joins the employer
    for the header's *name*). Rather than widen that read for a consumer with
    different needs, this is a narrow one of its own.

    **`None` for out of scope and `None` for absent, deliberately the same
    answer** — `select_claim_detail`'s single-answer rule, and it carries the
    same weight here because `GET /claims/{id}/similar` answers 404 from it. Two
    different answers would make the route an oracle for enumerating a
    portfolio the caller cannot read.
    """
    rows = await db.execute(
        sa.select(Claim, Employer)
        .select_from(Claim)
        .join(Employer, Claim.employer_id == Employer.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
    )
    return rows.one_or_none()


async def select_claim_embedding_sources(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_pks: Collection[int],
) -> Sequence[sa.Row[Any]]:
    """The same `(Claim, Employer, stale_at)` triple for named claims — scoped.

    `select_pending_claim_embeddings` answers "what needs doing"; this answers
    "do exactly these", which is what `embed_claims` is for. They are two
    functions rather than one with an optional predicate because the pending
    query carries an ordering that only makes sense for the queue, and folding
    them together would produce a signature in which half the parameters are
    meaningless for half the callers.

    The `LEFT JOIN` and the `stale_at` column are the same shape the pending
    query returns, and for the same reason: `write_claim_embedding` needs the
    marker that was current *before* the embed in order to decide whether it may
    clear the flag. A caller that got `(Claim, Employer)` alone could only
    clear unconditionally, which is the mid-embed-edit bug this design exists to
    prevent (see `data/models/core.py::ClaimEmbedding`). Outer, because a claim
    with no embedding row yet is exactly the claim somebody would ask this
    function to embed.

    Ordered by `id` so a batch composes in a stable order — not because
    anything downstream depends on it, but because a test that asserts "these
    three claims were embedded" should not have to sort first.
    """
    if not claim_pks:
        return []
    rows = await db.execute(
        sa.select(Claim, Employer, ClaimEmbedding.stale_at)
        .select_from(Claim)
        .join(Employer, Claim.employer_id == Employer.id)
        .outerjoin(ClaimEmbedding, ClaimEmbedding.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.id.in_(claim_pks))
        .order_by(Claim.id)
    )
    return rows.all()


async def select_pending_claim_embeddings(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    limit: int | None,
    model: str,
) -> Sequence[sa.Row[Any]]:
    """Claims whose embedding is stale, absent or from another model — scoped.

    "Pending" is three different facts and the ordering distinguishes them:
    `stale = true` means a vector exists and the text it was built from has
    since changed, `embedding IS NULL` means there has never been one, and
    `model IS DISTINCT FROM :model` means there is one but the deployment is no
    longer running the model that produced it.

    **Stale rows go first** because a wrong answer is worse than no answer — a
    similar-case search that ranks against a claim's pre-edit clinical summary
    silently returns the wrong neighbours, whereas one that skips an unembedded
    claim returns fewer, which the caller can see.

    **The model predicate is what makes `claim_embedding.model` load-bearing**
    rather than decorative. Without it, re-pointing `EMBEDDING_MODEL` at another
    1024-dimension model leaves the table holding vectors from two incomparable
    spaces: every insert succeeds, every index accepts the row, and retrieval
    quietly ranks bge-m3 vectors against somebody else's with nothing on any
    screen to say so. `IS DISTINCT FROM` rather than `!=` because a NULL `model`
    — a row that has never been embedded — must count as pending, and `NULL !=
    'bge-m3'` is NULL.

    **Outer-joined from `claim`, not selected from `claim_embedding`.** A claim
    inserted by a later story has no `claim_embedding` row until somebody edits
    it, and a query rooted at the embedding table could never pick it up: it
    would sit unembedded for ever, invisible to the one count an operator uses
    to ask whether the first pass has finished. Migration 0041 makes that rare
    rather than impossible, and "rare" is not a property a queue should depend
    on. `coalesce(stale, false)` keeps the ordering total across the join — a
    row-less claim has a NULL `stale` and would otherwise sort by NULL ordering
    rather than with the never-embedded group where it belongs.

    Within each group, `embedded_at ASC NULLS FIRST` is round-robin fairness:
    the row that has gone longest without attention goes next, and a row that
    keeps failing to embed does not monopolise every batch (it is re-attempted
    after everything else pending, not before). `id` breaks the remaining ties
    so the ordering is total and a `LIMIT` cannot silently return overlapping
    or missing rows across two runs.

    `limit=None` means "everything pending", which is what the e2e admin
    trigger passes so a spec does not have to know the batch size. The
    scheduled job passes `Settings.embedding_refresh_batch_size`.
    """
    statement = (
        sa.select(Claim, Employer, ClaimEmbedding.stale_at)
        .select_from(Claim)
        .join(Employer, Claim.employer_id == Employer.id)
        .outerjoin(ClaimEmbedding, ClaimEmbedding.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(
            sa.or_(
                ClaimEmbedding.stale,
                ClaimEmbedding.embedding.is_(None),
                ClaimEmbedding.model.is_distinct_from(model),
            )
        )
        .order_by(
            sa.func.coalesce(ClaimEmbedding.stale, sa.false()).desc(),
            ClaimEmbedding.embedded_at.asc().nullsfirst(),
            Claim.id,
        )
    )
    if limit is not None:
        statement = statement.limit(limit)
    rows = await db.execute(statement)
    return rows.all()


async def select_additional_injuries_for_claims(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_pks: Collection[int],
) -> Sequence[AdditionalInjury]:
    """Every secondary injury on the named claims, scoped and ordered by id.

    One query for a batch rather than one per claim: a refresh run composes up
    to `embedding_refresh_batch_size` summaries and the injuries are a child
    read of each, so the per-claim version is the classic N+1 in the one place
    in this build that runs unattended on a timer.

    **Ordered by `id`, and that ordering is load-bearing** rather than tidy.
    `compose_claim_text` sorts the injuries it is handed, but a composer that
    depended on its caller for determinism would be one refactor from a
    `source_text_hash` that changes because the database returned rows in a
    different order — which would mark every claim stale on every run.
    Deterministic here *and* there is belt and braces on the one property the
    whole staleness design rests on.

    An empty `claim_pks` short-circuits: `IN ()` is not valid SQL and
    SQLAlchemy's rendering of an empty `in_` is a correct-but-wasteful round
    trip to the database to learn what the caller already knows.
    """
    if not claim_pks:
        return []
    rows = await db.scalars(
        sa.select(AdditionalInjury)
        .select_from(AdditionalInjury)
        .join(Claim, AdditionalInjury.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(AdditionalInjury.claim_id.in_(claim_pks))
        .order_by(AdditionalInjury.id)
    )
    return rows.all()


async def upsert_claim_embedding_stale(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_pk: int,
) -> int:
    """Flag a claim's embedding as stale. Idempotent, scoped, does not commit.

    Returns the number of rows the statement affected — 0 when the claim is not
    in the caller's book, which is the same silence a scoped read gives and for
    the same reason.

    **No commit**, which is the contract the whole staleness design rests on:
    this is called from inside an AD-4 command's transaction, between
    `timeline.append` and `db.commit()`, so the flag lands with the edit or
    rolls back with it. `audit.record` and `timeline.append` keep the same
    contract, and a row here that could be committed separately from the change
    that made it stale would be a claim whose embedding is marked wrong for a
    change that never happened.

    The insert branch is not the normal path — migration 0041 pre-created a row
    for every seeded claim — but it exists so that a claim inserted by some
    later story has one the first time anybody edits it, rather than an edit
    that silently marks nothing. (Embedding such a claim is covered too:
    `select_pending_claim_embeddings` outer-joins from `claim`, so a row-less
    claim is pending whether or not anybody has edited it yet.)

    **`stale_at` moves on every mark**, and that is the half that makes the flag
    survive a refresh already in flight. `write_claim_embedding` clears `stale`
    only when `stale_at` still holds the value it read before embedding, so a
    mark landing during the model round trip is not overwritten by the vector
    that predates it. `clock_timestamp()` rather than `now()`: `now()` is the
    *transaction's* start time, so two edits whose transactions began in the
    same instant would stamp the same marker and the second would be
    indistinguishable from no mark at all. The statement clock has no such
    aliasing, and nothing here needs the marker to be transactionally
    consistent — only to differ from what it was.
    """
    source = (
        sa.select(Claim.id, sa.literal(True), sa.func.clock_timestamp())
        .where(employer_scope(ctx))
        .where(Claim.id == claim_pk)
    )
    statement = pg_insert(ClaimEmbedding).from_select(["claim_id", "stale", "stale_at"], source)
    result = await db.execute(
        statement.on_conflict_do_update(
            index_elements=[ClaimEmbedding.claim_id],
            set_={"stale": True, "stale_at": sa.func.clock_timestamp()},
        )
    )
    # `cast` for `claims.py`'s reason: a DML statement always produces a
    # `CursorResult`, but `AsyncSession.execute` is typed as returning the
    # narrower `Result`, which has no `rowcount`.
    return int(cast(CursorResult[Any], result).rowcount)


async def write_claim_embedding(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_pk: int,
    embedding: Sequence[float],
    source_text_hash: str,
    model: str,
    embedded_at: datetime,
    stale_at: datetime | None,
) -> int:
    """Store one claim's vector, clearing `stale` only if nothing re-marked it.

    Scoped, does not commit.

    `stale` is cleared in the same statement that writes the vector, because
    they are one fact: the row is current as of `embedded_at`, for the text
    whose hash this is, under this model. Clearing the flag in a *separate*
    statement would open a window in which a vector is stored and still
    advertised as stale, and the refresh would re-embed it on every tick for
    ever.

    **`stale_at` is the marker read before the embed, and the clear is
    conditional on it.** Composing is cheap and embedding is not: the caller
    selected this row, composed its summary, and then spent up to
    `EMBEDDING_REQUEST_TIMEOUT_SECONDS` inside one HTTP call. A handler's edit
    committing inside that window sets `stale = true` for a change this vector
    does not contain. So the statement writes `stale = (claim_embedding.stale_at
    IS DISTINCT FROM :stale_at)` — false when the marker has not moved, true
    when it has. The vector is stored either way, because it is newer than the
    nothing or the older vector it replaces; only the flag survives, and the
    next tick re-embeds against the edited text.

    Unconditional clearing was the alternative and it is the bug: the row ends
    up holding the pre-edit vector with a fresh `embedded_at` beside it and
    stays wrong until some unrelated edit happens to touch it — a wrong answer
    with a fresh timestamp, which is the one outcome AC 4 rules out.

    `IS DISTINCT FROM` rather than `!=` because `stale_at` is NULL on a row
    migration 0041 created and never marked, and `NULL != NULL` is NULL, which
    would leave `stale` NULL against a NOT NULL column.

    Returns rows affected; 0 for a claim outside the caller's book.
    """
    marker = sa.literal(stale_at, type_=ClaimEmbedding.stale_at.type)
    source = sa.select(
        Claim.id,
        sa.literal(list(embedding), type_=ClaimEmbedding.embedding.type),
        sa.literal(source_text_hash),
        sa.literal(model),
        sa.literal(embedded_at),
        sa.literal(False),
    ).where(employer_scope(ctx), Claim.id == claim_pk)
    statement = pg_insert(ClaimEmbedding).from_select(
        ["claim_id", "embedding", "source_text_hash", "model", "embedded_at", "stale"],
        source,
    )
    result = await db.execute(
        statement.on_conflict_do_update(
            index_elements=[ClaimEmbedding.claim_id],
            set_={
                "embedding": statement.excluded.embedding,
                "source_text_hash": statement.excluded.source_text_hash,
                "model": statement.excluded.model,
                "embedded_at": statement.excluded.embedded_at,
                # `ClaimEmbedding.stale_at` renders as `claim_embedding.stale_at`
                # — the row as it stands *now*, after any concurrent
                # mark-stale — rather than `excluded.stale_at`, which is the
                # value this statement proposed.
                "stale": ClaimEmbedding.stale_at.is_distinct_from(marker),
            },
        )
    )
    # `cast` for `claims.py`'s reason: a DML statement always produces a
    # `CursorResult`, but `AsyncSession.execute` is typed as returning the
    # narrower `Result`, which has no `rowcount`.
    return int(cast(CursorResult[Any], result).rowcount)


async def select_similar_claims(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    embedding: Sequence[float],
    k: int,
    exclude_claim_pk: int | None,
) -> Sequence[sa.Row[Any]]:
    """The `k` nearest embedded claims in the caller's book, with freshness.

    Each row carries the claim's business id, its employer's short name, the
    two clinical fields a result list shows, the cosine `distance`, and the
    `embedded_at` / `stale` pair. **The freshness columns ride in the result
    from day one** because AD-12 requires retrieval to return them and Story
    6.4's similar-case action discloses staleness past a configured threshold —
    a retrieval shape that had to grow a column later would mean every consumer
    written before then quietly showed answers with no provenance.

    `exclude_claim_pk` keeps the target claim out of its own neighbour list. It
    is a parameter rather than a filter the caller applies afterwards because
    dropping it after the fact would return `k-1` neighbours whenever the
    target is embedded — the exclusion has to happen before `LIMIT`, not after.
    `None` is for a free-text query, which has no target to exclude.

    `embedding IS NOT NULL` is explicit rather than left to NULL ordering.
    PostgreSQL sorts NULLs last on an ascending order, so an unembedded claim
    would only appear once the book ran out of embedded ones — but it *would*
    appear, as a neighbour with no distance, and a result list is not the place
    to discover that.
    """
    distance = ClaimEmbedding.embedding.cosine_distance(list(embedding)).label(DISTANCE)
    statement = (
        sa.select(
            Claim.claim_id,
            Employer.short_name.label("employer_short_name"),
            Claim.injury_type,
            Claim.severity_score,
            ClaimEmbedding.embedded_at,
            ClaimEmbedding.stale,
            distance,
        )
        .select_from(ClaimEmbedding)
        .join(Claim, ClaimEmbedding.claim_id == Claim.id)
        .join(Employer, Claim.employer_id == Employer.id)
        .where(employer_scope(ctx))
        .where(ClaimEmbedding.embedding.is_not(None))
        .order_by(distance, Claim.id)
        .limit(k)
    )
    if exclude_claim_pk is not None:
        statement = statement.where(Claim.id != exclude_claim_pk)
    rows = await db.execute(statement)
    return rows.all()


# --- the knowledge corpus: reference data, unfiltered ---------------------


async def select_similar_chunks(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    embedding: Sequence[float],
    k: int,
) -> Sequence[sa.Row[Any]]:
    """The `k` nearest labour-law chunks, ordered by cosine distance.

    **The caller context is required and not used as a filter**, which the
    module docstring argues at length: this corpus has no employer, no claim
    and no PHI, so there is nothing to narrow. The parameter is here so that a
    reader of `services/rag/retrieval.py` sees one shape for both retrieval
    calls rather than having to remember which of the two was scoped.
    """
    del ctx  # reference data — nothing to scope (see the module docstring)
    distance = KnowledgeEmbedding.embedding.cosine_distance(list(embedding)).label(DISTANCE)
    rows = await db.execute(
        sa.select(
            KnowledgeChunk.source,
            KnowledgeChunk.state_code,
            KnowledgeChunk.title,
            KnowledgeChunk.chunk_text,
            distance,
        )
        .select_from(KnowledgeEmbedding)
        .join(KnowledgeChunk, KnowledgeEmbedding.chunk_id == KnowledgeChunk.id)
        .where(KnowledgeEmbedding.embedding.is_not(None))
        .order_by(distance, KnowledgeChunk.id)
        .limit(k)
    )
    return rows.all()


async def select_pending_knowledge_embeddings(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    limit: int | None,
    model: str,
) -> Sequence[KnowledgeChunk]:
    """Chunks with no vector, or one from another model, in insertion order.

    No `stale` column to order by — `KnowledgeEmbedding`'s docstring argues why
    a chunk has no per-row staleness: the text is immutable, so the only thing
    that can invalidate its vector is a model change, and a model change
    invalidates every row at once rather than one at a time. That is exactly
    what the second half of this predicate implements, and without it the
    docstring's claim would be a promise nothing kept: re-pointing
    `EMBEDDING_MODEL` would leave fifteen chunks embedded by the old model,
    retrieved against queries embedded by the new one.

    `IS DISTINCT FROM` rather than `!=` so a NULL `model` — a chunk that has
    never been embedded — counts as pending.

    Context required, not used as a filter (see the module docstring).
    """
    del ctx  # reference data — nothing to scope (see the module docstring)
    statement = (
        sa.select(KnowledgeChunk)
        .select_from(KnowledgeEmbedding)
        .join(KnowledgeChunk, KnowledgeEmbedding.chunk_id == KnowledgeChunk.id)
        .where(
            sa.or_(
                KnowledgeEmbedding.embedding.is_(None),
                KnowledgeEmbedding.model.is_distinct_from(model),
            )
        )
        .order_by(KnowledgeChunk.id)
    )
    if limit is not None:
        statement = statement.limit(limit)
    rows = await db.scalars(statement)
    return rows.all()


async def write_knowledge_embedding(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    chunk_id: int,
    embedding: Sequence[float],
    model: str,
    embedded_at: datetime,
) -> int:
    """Store one chunk's vector. Does not commit.

    An upsert on `chunk_id` for `write_claim_embedding`'s reason: migration
    0041 pre-created the row, and a chunk added by a later revision should not
    need a second code path.

    Context required, not used as a filter (see the module docstring).
    """
    del ctx  # reference data — nothing to scope (see the module docstring)
    statement = pg_insert(KnowledgeEmbedding).values(
        chunk_id=chunk_id,
        embedding=list(embedding),
        model=model,
        embedded_at=embedded_at,
    )
    result = await db.execute(
        statement.on_conflict_do_update(
            index_elements=[KnowledgeEmbedding.chunk_id],
            set_={
                "embedding": statement.excluded.embedding,
                "model": statement.excluded.model,
                "embedded_at": statement.excluded.embedded_at,
            },
        )
    )
    # `cast` for `claims.py`'s reason: a DML statement always produces a
    # `CursorResult`, but `AsyncSession.execute` is typed as returning the
    # narrower `Result`, which has no `rowcount`.
    return int(cast(CursorResult[Any], result).rowcount)
