"""The commands that write the embedding tables — AD-12's sole owner.

Three tables (`claim_embedding`, `knowledge_chunk`, `knowledge_embedding`) and
exactly one package allowed to write them. Everything else that needs something
to happen to a row here asks: `services/claims/edit.py` and
`services/claims/injuries.py` call `mark_claim_stale`, and the scheduler and
the e2e admin route both call `refresh_stale_embeddings`. Neither of those
callers writes a vector, decides a batch size, or knows the tables exist.

## `mark_claim_stale` keeps the `audit.record` contract exactly

It adds to the caller's transaction and **does not commit**. That is the same
contract `services/audit.record` and `services/claims/timeline.append` have
kept since Story 2.3, and it is the whole reason the four wired commands can
call it: the flag lands with the edit or rolls back with it, in one commit,
with no window in which a claim's stored summary and its `stale` flag disagree.

A version that committed would produce the failure this design is arranged to
prevent — an embedding marked stale for an edit that then lost its
compare-and-swap and never happened, so the refresh re-embeds a claim that did
not change and `stale` stops meaning anything.

## Why the refresh is one command and not two paths

The scheduler calls `refresh_stale_embeddings`. The e2e admin route calls
`refresh_stale_embeddings`. There is no "and also, for tests, a version that…".
AD-12 says one refresh command for both the scheduled and on-demand paths, and
the practical form of that rule is that the e2e suite is testing the shipped
code rather than a fixture that resembles it — the same argument
`api/routers/admin.py` makes for the payment batch.

It handles the corpus as well as the claims, because "the pending embedding
work" is one queue from an operator's point of view even though it is two
tables. The alternative — a second scheduled job for fifteen immutable rows
that are embedded once and never again — is a registration, a predicate, a
config knob and a log line for work that finishes on the first tick.

## Failure is per run, and two kinds of it are different

**Ollama unreachable, or a batch that errors.** Logged as `rag.refresh_failed`
with a count, the rows stay pending, and the run returns having done what it
could. No retry loop: the next tick is the retry, fifteen minutes later, which
is a bounded retry by construction rather than a storm against a server that is
already unwell. Nothing degrades to a cloud provider, because there is nothing
to degrade to (AD-5).

**Both halves of the run degrade the same way, and each half is its own
savepoint.** The claim batch and the corpus batch are separately attempted,
separately counted (`rows_failed` / `chunks_failed`) and separately rolled back.
Two properties fall out of that, and both were bugs in the version without it.
The corpus half is *inside* the degradation contract — the state a fresh
deployment is in when the model server is down is "no claim vectors and no
chunk vectors", and a corpus failure that propagated would have turned the
admin route's documented "200 with zeroes and a non-zero failure count" into a
500. And a failure in the middle of a half — a database error raised while
writing row seventeen of twenty-five — rolls back to that half's savepoint
rather than poisoning the session, so the counts the run reports are true and
the *other* half still runs against a usable session. Rolling the whole
transaction back instead would throw away work the run had already finished,
which a partial run has no reason to do.

**A dimension mismatch propagates and takes the run down.** It is caught by
nothing here on purpose. A wrong-width vector is a configuration error that
will produce the same failure on every row of every future tick, so a run that
logged it and carried on would fill the log with identical lines while the
portfolio stayed unembedded and nothing said why. `services/rag/client.py`
argues the full case; the short version is that it names the model, the length
it got and the length the column wants, and stops. Both halves speak to one
client and one model, so the claim half raises first and there is no completed
work for the rollback to discard.

## AD-11

Every log line here is ids, counts and event names. No claim summary, no
vector, no chunk text, no model output. The composed text exists as a local
variable between the composer and the HTTP client and is stored nowhere — only
its hash is (`ClaimEmbedding`'s docstring argues why).
"""

from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models import AdditionalInjury, Claim, Employer, KnowledgeChunk
from data.repositories import embeddings as embedding_repo
from services.rag.claim_text import compose_and_hash
from services.rag.client import EmbeddingClient, EmbeddingDimensionMismatch

log = structlog.get_logger()


@dataclass(frozen=True)
class EmbedAttempt:
    """One half of a run: how many rows it wrote and how many it could not.

    Returned by both halves so the refresh command sums two identical shapes
    rather than treating the claims as the real work and the corpus as an
    afterthought. That symmetry *is* the fix for the corpus half sitting outside
    the degradation contract: a half that reports its own failures cannot
    accidentally raise them into the caller's face instead.

    `failed` is how many rows were pending when the half was attempted, not how
    many individual writes threw. The batch is one HTTP call, so a failure is
    all-or-nothing for the rows it was carrying.
    """

    embedded: int
    failed: int


@dataclass(frozen=True)
class RefreshRun:
    """What one refresh run did. Counts only — no ids, no text, no vectors.

    Published as the admin route's response body and as the scheduler's log
    line, which is why it is a value object rather than a pair of integers: the
    e2e spec asserts against `rowsEmbedded` and `chunksEmbedded` separately,
    because a run that embedded fifteen chunks and no claims and one that
    embedded fifteen claims and no chunks are different outcomes that a single
    total could not tell apart.

    **Four counts, because two halves fail independently.** `rows_failed` and
    `chunks_failed` are separate for the same reason the two embedded counts
    are: a tick in which the corpus embedded and the claims did not is a
    different event from one in which nothing reached the model at all, and the
    first is the steady state of a deployment whose corpus was seeded long ago.

    Failures are counts and not lists of ids: an operator needs to know that a
    tick did not complete, and *which* rows failed is a question the pending
    query answers without putting claim ids in a log line (AD-11).
    """

    rows_embedded: int
    chunks_embedded: int
    rows_failed: int
    chunks_failed: int

    @property
    def is_empty(self) -> bool:
        return (
            self.rows_embedded == 0
            and self.chunks_embedded == 0
            and self.rows_failed == 0
            and self.chunks_failed == 0
        )


async def mark_claim_stale(db: AsyncSession, ctx: CallerContext, *, claim_pk: int) -> None:
    """Flag a claim's embedding as needing recomputation. **Does not commit.**

    Called from inside an AD-4 command's transaction, after `audit.record` and
    `timeline.append` and before `db.commit()`, so that the three side effects
    of one mutation are one durable unit. See the module docstring.

    **`claim_pk`, not the `WC-nnnn` business id**, and that is what makes the
    function usable from `services/claims/injuries.py`: adding or removing a
    secondary injury writes no column of `claim` and bumps no `claim.version`,
    but it changes what the composer produces — so a command that mutates a
    *child* row still has to be able to mark the parent's embedding stale.
    `timeline.append` takes `claim_pk` for the same reason and with the same
    naming, so the mistake is a type error rather than a foreign-key violation
    at commit time.

    **It takes a caller context**, and the four wired commands all have one in
    hand at the point they call it. The repository reaches `claim` through
    `employer_scope(ctx)` like every other claim-touching read or write, so a
    command cannot mark stale a claim outside the caller's book even by passing
    its primary key — and for the system actor the predicate is the AD-7
    tautology, costing the refresh nothing.

    Silent for a claim the caller cannot see. That is the same answer a scoped
    read gives, for the same reason: a mark-stale that raised for an
    out-of-scope claim would be an existence oracle over the portfolio.
    """
    await embedding_repo.upsert_claim_embedding_stale(db, ctx, claim_pk=claim_pk)


async def embed_claims(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_pks: Collection[int],
    client: EmbeddingClient,
) -> int:
    """Recompute and store the vectors for the named claims. Does not commit.

    Returns how many rows were written. Exposed as its own command — rather
    than left inside `refresh_stale_embeddings` — because "embed exactly these
    claims" is what a test asserts against and what a future on-demand path
    (a handler asking for similar cases on a claim they have just edited) would
    want. It does not commit, so a caller can compose it with other work; the
    refresh command owns the transaction boundary.

    **One HTTP round trip for the whole batch.** The composition is per claim
    and the embedding is not: `client.embed` takes the whole list, which is why
    `EmbeddingClient` is batch-shaped. Against a model server that processes
    requests one at a time, twenty-five sequential requests cost twenty-five
    round trips on top of the same inference.
    """
    rows = await embedding_repo.select_claim_embedding_sources(db, ctx, claim_pks=claim_pks)
    return await _embed_rows(db, ctx, rows=rows, client=client, model=_model_name(client))


async def refresh_stale_embeddings(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    client: EmbeddingClient,
    limit: int | None,
) -> RefreshRun:
    """Embed what is pending — stale claims first — and the corpus. Commits.

    The single refresh path (AD-12). The scheduler calls it every
    `EMBEDDING_REFRESH_INTERVAL_SECONDS` with `limit` set to
    `EMBEDDING_REFRESH_BATCH_SIZE`; the e2e admin route calls it with
    `limit=None` so a spec does not have to know what the batch size is.

    **The ordering is the repository's**, not this function's:
    `select_pending_claim_embeddings` orders `stale DESC, embedded_at ASC NULLS
    FIRST, id` and takes the `LIMIT`, so "stale rows are embedded before
    never-embedded ones, at most `batch_size` per run" is a property of one
    statement rather than of a sort somewhere in Python that a later refactor
    could reorder. Re-implementing it here would be the second encoding of a
    rule, which is what this codebase spends most of its comments avoiding.

    **Commits once, at the end.** A partial run that has written some vectors
    is a perfectly good outcome — the rows it wrote are current and the rows it
    did not are still pending — so there is nothing to roll back and no reason
    to hold a transaction open across two batches of HTTP.

    **`limit` is a budget for the run, not for each half**, which is what
    `EMBEDDING_REFRESH_BATCH_SIZE`'s "how many rows one run embeds" already
    told an operator it was. Forwarding it unchanged to both halves would let a
    tick configured for twenty-five send fifty texts to the model. The claims
    spend it first because they are the half that recurs: the corpus is fifteen
    immutable rows that are embedded once, so on every tick after the first it
    asks for nothing and the claims keep the whole budget.
    """
    model = _model_name(client)
    rows = await embedding_repo.select_pending_claim_embeddings(db, ctx, model=model, limit=limit)
    claims = await _attempt(
        db,
        half="claims",
        pending=len(rows),
        write=lambda: _embed_rows(db, ctx, rows=rows, client=client, model=model),
    )

    # Selected here rather than inside `seed_knowledge_corpus` so that both
    # halves have the same shape: read what is pending, then attempt it. A half
    # that did its own select inside the savepoint could not report how many
    # rows it had been carrying when the model server refused them, and
    # `chunks_failed: 0` on a run that failed to embed fifteen chunks is a
    # count that lies.
    remaining = None if limit is None else max(0, limit - len(rows))
    pending_chunks = await embedding_repo.select_pending_knowledge_embeddings(
        db, ctx, model=model, limit=remaining
    )
    chunks = await _attempt(
        db,
        half="corpus",
        pending=len(pending_chunks),
        write=lambda: _embed_chunks(db, ctx, chunks=pending_chunks, client=client, model=model),
    )

    await db.commit()

    run = RefreshRun(
        rows_embedded=claims.embedded,
        chunks_embedded=chunks.embedded,
        rows_failed=claims.failed,
        chunks_failed=chunks.failed,
    )
    if not run.is_empty:
        # An empty run is silent, `run_payment_batch`'s rule: a job that logged
        # "ran, did nothing" every fifteen minutes would bury the runs that did
        # something. Counts only (AD-11).
        log.info(
            "rag.refresh_completed",
            rows=run.rows_embedded,
            chunks=run.chunks_embedded,
            rows_failed=run.rows_failed,
            chunks_failed=run.chunks_failed,
        )
    return run


async def _attempt(
    db: AsyncSession,
    *,
    half: str,
    pending: int,
    write: Callable[[], Awaitable[int]],
) -> EmbedAttempt:
    """Run one half of a refresh inside its own savepoint. Never commits.

    The savepoint is the whole point, and it fixes two failures the version
    without it had. A database error raised while writing row seventeen of
    twenty-five used to leave the session poisoned, so the *next* statement
    raised `PendingRollbackError` and the original cause was lost; rolling back
    to a savepoint leaves the session usable, so the other half still runs and
    the counts this run reports are true. And because only the failing half
    unwinds, a corpus failure no longer discards claim vectors the run had
    already computed — `SAVEPOINT` is what lets "a partial run is a perfectly
    good outcome" be true of a run in which something actually went wrong.

    A dimension mismatch is re-raised rather than counted — see the module
    docstring — but only after its three numbers reach the log. That matters
    on the scheduled path specifically: `JobRunner.tick` records
    `type(exc).__name__` and nothing else (correctly, for AD-11), so an
    exception whose entire design is that it names the model, the width it got
    and the width the column wants would otherwise surface in production as the
    bare string `EmbeddingDimensionMismatch`. None of the three is PHI.
    """
    try:
        async with db.begin_nested():
            result = await write()
    except EmbeddingDimensionMismatch as exc:
        log.error(
            "rag.refresh_dimension_mismatch",
            half=half,
            model=exc.model,
            returned=exc.returned,
            expected=exc.expected,
        )
        raise
    except Exception as exc:
        # Ollama unreachable, a transport error, a 5xx, a database error inside
        # the write loop. The rows stay pending, the next tick is the retry —
        # bounded by construction rather than by a retry loop — and the log
        # line carries the exception's *class* and a count, never its message,
        # which for an HTTP error can echo a request body (AD-11, and
        # `services/jobs.py` makes the same choice for the same reason).
        log.error("rag.refresh_failed", half=half, rows=pending, error=type(exc).__name__)
        return EmbedAttempt(embedded=0, failed=pending)

    return EmbedAttempt(embedded=result, failed=pending - result)


async def seed_knowledge_corpus(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    client: EmbeddingClient,
    model: str | None = None,
    limit: int | None = None,
) -> int:
    """Embed labour-law chunks that have no current vector. Does not commit.

    Idempotent by construction rather than by bookkeeping: the pending query is
    "no vector, or a vector from a different model", so a second call with the
    same model selects nothing. There is no "already seeded" flag to consult
    and therefore nothing that can disagree with the rows themselves —
    `run_payment_batch`'s shape, for its reason.

    Migration 0041 inserted the chunks and one empty embedding row per chunk; a
    migration cannot reach a model server, so this is where the vectors arrive.

    **`refresh_stale_embeddings` does not call this function**, and the split is
    deliberate rather than duplication: it selects the pending chunks itself so
    that it knows how many rows a failed half was carrying (see `_attempt`).
    What both paths share is `_embed_chunks` below, which is where the embedding
    and the writes actually happen. This wrapper is the standalone command — a
    seed-time or one-off "embed the corpus" — and it exists so that "the corpus
    is embedded by `services/rag`" has a name a caller can say.
    """
    resolved = model if model is not None else _model_name(client)
    chunks = await embedding_repo.select_pending_knowledge_embeddings(
        db, ctx, model=resolved, limit=limit
    )
    return await _embed_chunks(db, ctx, chunks=chunks, client=client, model=resolved)


async def _embed_chunks(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    chunks: Sequence[KnowledgeChunk],
    client: EmbeddingClient,
    model: str,
) -> int:
    """Embed and store one batch of knowledge chunks. Shared by both paths.

    The corpus half's counterpart to `_embed_rows`, and shaped the same way for
    the same reason: one HTTP round trip for the whole batch, every vector
    validated before the first write, so a dimension mismatch writes nothing
    rather than writing some rows and then stopping.
    """
    if not chunks:
        return 0

    vectors = await client.embed([chunk.chunk_text for chunk in chunks])
    at = datetime.now(UTC)
    written = 0
    for chunk, vector in zip(chunks, vectors, strict=True):
        written += await embedding_repo.write_knowledge_embedding(
            db,
            ctx,
            chunk_id=chunk.id,
            embedding=vector,
            model=model,
            embedded_at=at,
        )
    return written


async def _embed_rows(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    rows: Sequence[sa.Row[Any]],
    client: EmbeddingClient,
    model: str,
) -> int:
    """Compose, embed and store one batch of claim rows. Shared by both commands.

    `rows` are `(Claim, Employer, stale_at)` triples from either
    `select_pending_claim_embeddings` or `select_claim_embedding_sources`. The
    secondary injuries for the whole batch come back in one query, which is the
    N+1 this function exists to avoid — a refresh tick composes up to
    `batch_size` summaries and each of them wants a child read.

    **`stale_at` is read before the embed and handed back to the write**, and
    that round trip is the whole of the concurrency story. Between the select
    and the write sits one HTTP call the config allows up to
    `EMBEDDING_REQUEST_TIMEOUT_SECONDS`, and a handler's edit can commit inside
    it — marking the row stale for a change the in-flight vector does not
    contain. `write_claim_embedding` clears `stale` only when the marker it is
    given still matches the row's, so an edit that lands mid-flight keeps the
    row pending and the next tick re-embeds it. Without it the refresh would
    store the pre-edit vector, clear the flag, and leave the claim permanently
    wrong until some unrelated edit happened to touch it — a wrong answer with
    a fresh timestamp beside it, which is the one outcome AD-12's staleness
    contract exists to prevent.
    """
    if not rows:
        return 0

    claims: list[Claim] = [row.Claim for row in rows]
    employers: list[Employer] = [row.Employer for row in rows]
    claim_pks = [claim.id for claim in claims]

    injuries = await embedding_repo.select_additional_injuries_for_claims(
        db, ctx, claim_pks=claim_pks
    )
    by_claim: dict[int, list[AdditionalInjury]] = {pk: [] for pk in claim_pks}
    for injury in injuries:
        by_claim[injury.claim_id].append(injury)

    composed = [
        compose_and_hash(claim, employer, by_claim[claim.id])
        for claim, employer in zip(claims, employers, strict=True)
    ]

    # The whole batch in one request, and every vector validated before the
    # first write — which is what makes "a dimension mismatch writes nothing"
    # true rather than "writes some rows and then stops".
    vectors = await client.embed([text for text, _ in composed])

    at = datetime.now(UTC)
    written = 0
    for row, claim, (_, source_hash), vector in zip(rows, claims, composed, vectors, strict=True):
        written += await embedding_repo.write_claim_embedding(
            db,
            ctx,
            claim_pk=claim.id,
            embedding=vector,
            source_text_hash=source_hash,
            model=model,
            embedded_at=at,
            stale_at=row.stale_at,
        )
    return written


def _model_name(client: EmbeddingClient) -> str:
    """Which model answered, for `claim_embedding.model`.

    Read off the client rather than out of `Settings` a second time, so the
    column records what actually produced the vector. `EmbeddingClient` does not
    require the attribute — a fake in a test has no model name worth
    inventing — so the fallback names the absence rather than guessing, and a
    row that carries it is a row embedded by something other than the shipped
    client.
    """
    name = getattr(client, "model", None)
    return str(name) if name else "unknown"


__all__ = [
    "RefreshRun",
    "embed_claims",
    "mark_claim_stale",
    "refresh_stale_embeddings",
    "seed_knowledge_corpus",
]
