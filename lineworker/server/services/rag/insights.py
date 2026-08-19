"""The commands that write `ai_insight` — AD-12's sole owner, one refresh path.

One table, one package allowed to write it, and one function that does the
writing. `agents/insights.py` composes the prompts, calls the model and
validates the answer; then it calls `store_insights` here, exactly as
`services/claims/edit.py` calls `mark_claim_stale` rather than touching
`claim_embedding` itself. The scheduled job, the on-demand `POST
…/insights/refresh` and the e2e admin trigger all reach this one function —
there is no second path and no fixture path (AD-12).

## Why the generator is an injected Protocol

`services/rag` owns `ai_insight`, and `services/rag` must not contain chat
code: the dependency diagram allows inference only from `agents/`, and
`services/` may never import `agents/` (AD-5, layering). Those two rules look
like they conflict, and Story 6.1 already showed they do not — `refresh_stale_
embeddings` takes `client: EmbeddingClient`, a `Protocol`, and the concrete
`OllamaEmbeddingClient` is built by a factory at the composition root.

`InsightGenerator` is the same move for narratives. This command asks *for* the
content of a kind and stores what it is given; the thing that knows how to
produce it lives in `agents/`, is constructed there, and is passed in. So the
call graph runs one way throughout — composition root → `agents/` → `services/`
→ `data/` — with no import cycle, no chat client in this package, and no
`ai_insight` write anywhere else.

The practical dividend is the same one the embedding protocol pays: **every
test of the cache runs with no model server.** Upsert-not-duplicate, the AD-2
figure equality, the schema-rejection path, the scope refusals and the audit
trail are all asserted against a fake generator, in milliseconds, with no
container. A suite that needed a GPU to say whether a re-refresh duplicates
rows is a suite that gets skipped.

## Failure is per kind, and each kind is its own transaction

A kind whose generation fails — a model server that did not answer, or an
answer that failed the kind's Pydantic schema — is counted and skipped; the
other three still generate and still persist. That is the I/O matrix's
"malformed model output" row and it is the honest behaviour for a cache of four
independent narratives: a fraud card that could not be generated is no reason
for the reserve card to be missing.

**Generation happens outside any transaction, and the transaction covers the
write.** This shipped the other way round — the whole of `_generate_and_write`,
completion included, sat inside a `begin_nested()` savepoint with one commit at
the end — and the Story 6.2 review is what corrected it (H2). A chat completion
is an HTTP request `CHAT_REQUEST_TIMEOUT_SECONDS` permits to take two minutes,
four of them run per claim, and `POST /admin/insight-refresh` runs the whole
book: that arrangement held a pooled connection idle-in-transaction for eight
minutes per claim, against a pool the rest of the API is sharing. Now each kind
generates with no transaction open (`agents/insights.py` closes the read
transaction before it asks the model), and the transaction spans exactly the
upsert and its audit event, which is the pair that must be atomic.

A commit per kind rather than one at the end, and it is a *stronger* form of
the isolation the savepoint was there for: a kind that fails cannot unwind the
three already committed, because they are no longer in the same transaction to
unwind. A partial run was always a good outcome — three cards a handler can
read — and now it is durable the moment each card is written rather than at the
end of a run the fourth completion could still take down.

**A failed kind leaves the previous generation exactly as it was**, including
its `generated_at`. That is not a fallback — nothing is substituted and no
pre-authored text is presented as model output (AD-14) — it is what a cache
does: the card keeps saying what it said, beside the timestamp that says when.

## AD-11

Every log line here carries claim ids, kind names and counts. Never a prompt,
never a completion, never a field of `content`. The audit event is the same
discipline in the database: it records *that* a narrative was generated, for
which claim and which kind, by which model — and not one word of what it said.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models.enums import InsightKind
from data.repositories import insights as insight_repo
from services import audit
from services.claims.detail import ClaimNotVisible
from services.rag.client import EmbeddingDimensionMismatch

log = structlog.get_logger()

#: The kinds a full refresh generates, in the order the tab renders them.
#: Derived from the enum rather than restated, so a fifth member is generated
#: the day it is declared instead of silently never being asked for.
ALL_KINDS: tuple[InsightKind, ...] = tuple(InsightKind)

#: The AD-4 action name every insight write records. One string, here, because
#: Epic 8's audit review filters on it and a second spelling somewhere would be
#: a class of events that review never sees.
INSIGHT_GENERATED_ACTION = "ai_insight.generated"


class InsightGenerationError(RuntimeError):
    """The generator could not produce a valid narrative for one kind.

    Raised by `agents/`, caught here, counted, and never allowed to take the
    other three kinds down with it. The two failures it covers are genuinely
    different and the distinction is the one field on it: a model server that
    did not answer is temporary and *nothing* can be generated until it comes
    back (`unavailable=True`), while an answer that failed the kind's schema is
    a bad completion the next attempt may well get right.

    `POST /claims/{id}/insights/refresh` is what reads the difference: a run
    that wrote nothing because the model is down answers 503, and one that
    wrote nothing because every completion was malformed answers 200 with the
    cards unchanged. A single exception type would have made those one status
    code, and the one it would have chosen is wrong for the other case.

    **The message names the kind and nothing else.** Vendor exception text can
    echo a request body, and a request body here is a prompt containing claim
    narrative (AD-11) — so the cause is chained for a debugger and never
    stringified into a log line or a response.
    """

    def __init__(self, kind: InsightKind, *, unavailable: bool) -> None:
        super().__init__(f"could not generate the {kind.value} insight")
        self.kind = kind
        self.unavailable = unavailable


class InsightGenerator(Protocol):
    """What `store_insights` needs from `agents/`: a model name and one method.

    Per *kind* rather than per claim, which is what lets one kind fail without
    the others: the command drives the loop, so it owns the savepoint, the
    counting and the audit event, and the generator's whole job is to answer
    "what does this claim's fraud narrative say?".

    `content` comes back as a plain mapping — the JSON form of a Pydantic model
    that `agents/schemas.py` has already validated — because that is what the
    column holds and because a `services/` signature naming an `agents/` type
    would be the import this arrangement exists to avoid.

    `model` is the model that actually answered, published for
    `EmbeddingClient`'s reason one story earlier: `ai_insight.model` is written
    from the thing that produced the narrative rather than from `Settings` read
    a second time, so the card labels what wrote it rather than what
    configuration said should have.
    """

    @property
    def model(self) -> str:
        """The serving model's name, for `ai_insight.model`."""
        ...

    async def generate(self, kind: InsightKind, *, claim_business_id: str) -> Mapping[str, Any]:
        """The validated content for one kind, or `InsightGenerationError`."""
        ...


@dataclass(frozen=True)
class CachedInsight:
    """One cached narrative, as every reader of the cache receives it.

    A value object rather than the ORM row, `SimilarClaim`'s arrangement: the
    route maps it onto the wire and a test compares it, and neither should be
    holding a detached SQLAlchemy instance whose attributes may or may not still
    be loadable.

    `generated_at` is not optional and never will be. AD-10's rule is that an AI
    narrative is always rendered with the time it was generated, and a nullable
    timestamp is the shape that lets a card ship without one.
    """

    kind: InsightKind
    content: Mapping[str, Any]
    model: str
    generated_at: datetime


@dataclass(frozen=True)
class InsightRun:
    """What one refresh did. Counts and one flag — no ids, no content.

    Published as the admin route's response body, as the scheduler's log line
    and as what `POST …/insights/refresh` decides its status code from.

    `claims` and `written` are separate because they answer different questions:
    a run over five claims that wrote twenty rows finished its batch, and one
    over five claims that wrote three did not. `failed` is rows rather than
    claims for the same reason `store_insights` loops kinds — the unit of
    success here is a card.

    **`model_unavailable` is a flag rather than a count**, and it is the one
    piece of shape in this object that is not arithmetic. It says "at least one
    kind failed because the model server did not answer", which is the fact a
    route needs to choose between 503 and 200 and the fact an operator needs to
    tell a bad completion from a dead container. Which *kinds* were affected is
    a question the cache itself answers, without putting claim ids in a log line
    (AD-11).

    **`failed_kinds` names which cards were refused**, added by the Story 6.2
    review (M7). A count alone made a partially successful refresh
    indistinguishable from a clean one at the API: three of four written
    answered 200 with a fresh payload, and the fourth card silently kept reading
    `not_generated` with no way for the tab to say why the button the handler
    pressed had not filled it. A kind token is not PHI and is not content — it
    is the same vocabulary the payload is already keyed by — so it can be
    published where the narrative cannot.

    Deduplicated and in enum order rather than in failure order, because for a
    batch run the same kind can fail on many claims and a reader wants the set.
    """

    claims: int
    written: int
    failed: int
    model_unavailable: bool
    failed_kinds: tuple[InsightKind, ...] = ()

    @property
    def is_empty(self) -> bool:
        return self.claims == 0 and self.written == 0 and self.failed == 0


async def store_insights(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
    generator: InsightGenerator,
    kinds: Sequence[InsightKind] = ALL_KINDS,
) -> InsightRun:
    """Generate and persist one claim's narratives. Commits.

    The **only** function in the build that writes `ai_insight` (AD-12). Both
    refresh paths reach it: `agents/insights.py::refresh_claim_insights` calls
    it for one claim and `refresh_pending_insights` calls it once per claim in
    a batch, so the scheduled job and the handler's Refresh button do the same
    thing to the same table through the same statement.

    Raises `ClaimNotVisible` for a claim outside the caller's book **and** for
    one that does not exist — `select_claim_detail`'s single-answer rule, which
    the route turns into one 404 so that walking `WC-20000`…`WC-20999` teaches a
    caller nothing (AD-7). Resolved *before* any generation, so a stranger's
    request never reaches the model at all.

    **One audit event per kind written, and it is content-free** (AD-4, AD-11).
    Insight writes are exempt from the approval gate Story 6.5 builds — nothing
    here mutates a claim-owned entity, and pausing a cache refresh for a human
    would make the tab useless — but they are not exempt from audit, and the
    exemption is precisely why the event matters: this is the record that a
    model wrote something into the case file's neighbourhood, who asked, and
    when. `after` carries the kind and the model name; the narrative itself
    never leaves the `content` column.

    **Commits per kind**, not once at the end — see the module docstring on why
    the completion moved out of the transaction. Nothing is lost by it: each
    card and its audit event are one transaction, and there was never anything
    spanning two cards that a single commit was protecting.

    **The attempt is recorded first, and committed before any generation.** That
    row is what stops one claim starving the portfolio: `select_claims_needing_
    insights` orders by it, so a claim that has just been tried goes to the back
    of the queue whatever the outcome (H4, and `data/repositories/insights.py`
    argues it). Committed up front rather than at the end precisely because the
    interesting case is the run that does not finish.

    **It stops at the first kind whose failure was the model server.** A
    container that did not answer for the fraud narrative will not answer for
    the next three, and pressing on would spend `CHAT_REQUEST_TIMEOUT_SECONDS`
    per kind — the interactive route waiting four timeouts to learn what the
    first one already said (M6). `refresh_pending_insights` makes the same call
    one level up, for the same reason.
    """
    claim_pk = await insight_repo.select_insight_claim_pk(
        db, ctx, claim_business_id=claim_business_id
    )
    if claim_pk is None:
        raise ClaimNotVisible(claim_business_id)

    model = generator.model
    at = datetime.now(UTC)
    await insight_repo.record_insight_attempt(db, ctx, claim_pk=claim_pk, attempted_at=at)
    await db.commit()

    written = 0
    failed = 0
    unavailable = False
    refused: list[InsightKind] = []

    for kind in kinds:
        outcome = await _attempt(
            db,
            ctx,
            claim_pk=claim_pk,
            claim_business_id=claim_business_id,
            kind=kind,
            generator=generator,
            model=model,
            at=at,
        )
        written += outcome.written
        failed += outcome.failed
        if outcome.failed:
            refused.append(kind)
        if outcome.unavailable:
            unavailable = True
            # The kinds after this one were never attempted, so they are not
            # counted as failures — they are simply still pending, which is what
            # the next tick and the next Refresh both act on.
            break

    run = InsightRun(
        claims=1,
        written=written,
        failed=failed,
        model_unavailable=unavailable,
        failed_kinds=tuple(refused),
    )
    log.info(
        "rag.insights_stored",
        claim_id=claim_business_id,
        written=run.written,
        failed=run.failed,
        model_unavailable=run.model_unavailable,
    )
    return run


async def claim_insights(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
) -> Sequence[CachedInsight]:
    """Every narrative cached for one claim, in kind order. Scoped.

    Raises `ClaimNotVisible` for an unknown claim and for one outside the
    caller's book, deliberately the same answer — `store_insights`' rule and
    its reason. **The claim is resolved before the rows are read**, and that
    ordering is the whole of the scope guarantee on this path: an invisible
    claim and a claim nobody has generated for both hold zero `ai_insight`
    rows, so a function that returned the empty list for both would render four
    empty cards at a stranger and tell them the claim exists.

    A claim in scope with no rows returns `[]`, which is a first-class answer:
    the route turns it into four `not_generated` cards (NFR-3), never a 404.
    """
    claim_pk = await insight_repo.select_insight_claim_pk(
        db, ctx, claim_business_id=claim_business_id
    )
    if claim_pk is None:
        raise ClaimNotVisible(claim_business_id)

    rows = await insight_repo.select_claim_insights(db, ctx, claim_business_id=claim_business_id)
    return [
        CachedInsight(
            kind=row.kind,
            content=row.content,
            model=row.model,
            generated_at=row.generated_at,
        )
        for row in rows
    ]


async def claims_needing_insights(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    limit: int | None,
) -> Sequence[str]:
    """Claims in the caller's book missing at least one kind. Scoped.

    The scheduled job's work queue, and the reason it is here rather than in
    `agents/` is AD-12's other half: `services/rag` owns the table, so "what
    still needs generating?" is a question about its rows and is answered beside
    the command that writes them.

    `len(ALL_KINDS)` is passed down rather than hardcoded in the repository, so
    the count that decides pending-ness is the same enum the generator loops
    over — see `select_claims_needing_insights` on why a repository that
    believed in four kinds would quietly stop selecting on the day a fifth
    arrived, and on why an *old* insight is deliberately not pending.
    """
    return await insight_repo.select_claims_needing_insights(
        db, ctx, limit=limit, kinds=len(ALL_KINDS)
    )


@dataclass(frozen=True)
class _Outcome:
    """One kind's result: whether it was written, and why it was not."""

    written: int
    failed: int
    unavailable: bool


class InsightRowNotWritten(RuntimeError):
    """`upsert_insight` reported zero rows, so nothing was stored.

    The statement is `INSERT … SELECT … ON CONFLICT DO UPDATE`, and its `SELECT`
    carries the scope predicate — so zero rows means the claim was not in the
    caller's book *at the moment of the write*, which after a successful
    `select_insight_claim_pk` means it moved out from under the run. A race, and
    a rare one, but the outcome has to be a counted failure: before the Story
    6.2 review this branch counted the kind as neither written nor failed,
    emitted no audit event, and left the card pending for ever while every
    refresh silently re-spent its completion (M10).

    Never re-raised past `_attempt`, which turns it into one failed kind and a
    content-free log line like any other write failure.
    """


async def _attempt(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_pk: int,
    claim_business_id: str,
    kind: InsightKind,
    generator: InsightGenerator,
    model: str,
    at: datetime,
) -> _Outcome:
    """Generate one kind, then write it in its own transaction.

    **Two phases, and the split is the whole point** (review of Story 6.2, H2).
    The generator's `generate` is an HTTP request to a model server with a
    two-minute timeout in front of it, and it must not run with a transaction
    open — `agents/insights.py` closes the read transaction its gather opened
    before it asks. Only the second phase touches the database, and it is one
    short transaction spanning the upsert and its audit event, which is the pair
    AD-4 requires to be atomic.

    A commit rather than a savepoint, which is a stronger form of the same
    isolation: a later kind that fails cannot unwind this one, because this one
    is already durable. A failure inside the write rolls back explicitly, so the
    session is usable for the next kind rather than poisoned into
    `PendingRollbackError` — the failure the savepoint was originally there to
    prevent, prevented the same way.

    Nothing is re-raised except a permanent configuration error. A refresh that
    could not reach the model is a *reported* outcome rather than an exception —
    the scheduled job logs and carries on, the admin route answers 200 with the
    counts, and the interactive route reads `model_unavailable` to decide its
    status code. `EmbeddingDimensionMismatch` is the exception, and it is
    `services/rag/embeddings.py::_attempt`'s exception for the same reason: it
    is a configuration error that will fail identically on every future tick and
    names the model, the width it returned and the width the column holds, so
    reporting it as a failed kind would bury the one message that says what to
    do (M2).
    """
    try:
        content = await generator.generate(kind, claim_business_id=claim_business_id)
    except InsightGenerationError as exc:
        # The kind, the claim and whether the model answered. Never the prompt,
        # never the completion, never the exception's own text — for a transport
        # error that text can carry a request body, and a request body here
        # contains claim narrative (AD-11).
        log.warning(
            "rag.insight_generation_failed",
            claim_id=claim_business_id,
            kind=kind.value,
            model_unavailable=exc.unavailable,
        )
        return _Outcome(written=0, failed=1, unavailable=exc.unavailable)
    except EmbeddingDimensionMismatch:
        raise
    except Exception as exc:
        # Anything the generator raised that it did not label — a database error
        # during the gather, or a plain bug. Counted as a failure of this kind
        # rather than allowed to take the run down, and logged by exception
        # *class* for the reason `services/jobs.py` makes the same choice: a
        # message can echo a value.
        log.error(
            "rag.insight_generation_failed",
            claim_id=claim_business_id,
            kind=kind.value,
            error=type(exc).__name__,
        )
        return _Outcome(written=0, failed=1, unavailable=False)

    try:
        written = await _write(
            db,
            ctx,
            claim_pk=claim_pk,
            claim_business_id=claim_business_id,
            kind=kind,
            content=content,
            model=model,
            at=at,
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        log.error(
            "rag.insight_write_failed",
            claim_id=claim_business_id,
            kind=kind.value,
            error=type(exc).__name__,
        )
        return _Outcome(written=0, failed=1, unavailable=False)
    return _Outcome(written=written, failed=0, unavailable=False)


async def _write(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_pk: int,
    claim_business_id: str,
    kind: InsightKind,
    content: Mapping[str, Any],
    model: str,
    at: datetime,
) -> int:
    """Upsert one already-validated narrative and its audit event. No commit.

    The audit event rides in the same transaction as the row, which is the
    contract every AD-4 command in this codebase keeps: an insight that existed
    without its event, or an event without its insight, would be the two halves
    of one fact committed separately.

    `at` is the run's instant rather than a fresh `now()` per kind, so a claim's
    four cards carry one timestamp and the tab does not show four times four
    seconds apart for what was one refresh — `audit.record`'s reason for taking
    `at` as a parameter.

    A zero-row upsert raises rather than returning quietly — see
    `InsightRowNotWritten`.
    """
    written = await insight_repo.upsert_insight(
        db,
        ctx,
        claim_pk=claim_pk,
        kind=kind,
        content=content,
        model=model,
        generated_at=at,
    )
    if not written:
        raise InsightRowNotWritten(f"no row was written for the {kind.value} insight")
    await audit.record(
        db,
        ctx,
        action=INSIGHT_GENERATED_ACTION,
        entity="ai_insight",
        entity_id=f"{claim_business_id}:{kind.value}",
        before=None,
        # Provenance, not content: which narrative was replaced and what
        # wrote it. `audit.record` logs the *keys* of this mapping and never
        # its values, and neither a kind token nor a model name is PHI.
        after={"kind": kind.value, "model": model},
        at=at,
    )
    return written


__all__ = [
    "ALL_KINDS",
    "INSIGHT_GENERATED_ACTION",
    "CachedInsight",
    "InsightGenerationError",
    "InsightGenerator",
    "InsightRowNotWritten",
    "InsightRun",
    "claim_insights",
    "claims_needing_insights",
    "store_insights",
]
