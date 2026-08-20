"""`copilot_thread` reads and its single writer — AD-7 over a conversation's identity.

The fourth scoped repository, after `claims` (1.4), `embeddings` (6.1) and
`insights` (6.2), and the argument for scoping it is `insights.py`'s turned
sideways. A thread row holds no claim data at all — an id, a sequence, a
timestamp — but it is the **key to a transcript** that quotes a claim's
diagnosis, wage and fraud score back at whoever can read it. Serving a thread
id across an employer partition is therefore the same leak as serving the
conversation, one indirection later, which is the worst kind: nothing in the
row looks like it belongs to anybody.

So every statement in this module reaches `claim` through `employer_scope(ctx)`
— the predicate `data/repositories/claims.py` defines and every other
repository imports — including the write, and there is no second predicate in
this file. `tests/test_scoped_repository.py`'s structural guards bind it
through `SCOPED_REPOSITORY_MODULES`; a repository added without being
registered there escapes all four of them, which is why adding the module to
that list is part of adding the module.

## Two filters, not one, and the second is not scope

Scope answers "may this caller see this claim?". It does **not** answer "is
this thread this caller's?", and conflating them would be a real bug: two
handlers can share an employer, so `employer_scope` alone would let one of them
list — and post to — the other's conversations about a claim they both cover.
Every function here therefore carries `CopilotThread.user_id == ctx.user_id`
beside the scope predicate.

That is not a role branch and not a widening — nothing in this module reads the
caller's capability at all, which is what `test_no_repository_branches_on_role`
asserts. It is the ownership half of the same key the table is built on, and
the API turns its failure into the `_not_found` 404 rather than a 403: a caller
who can distinguish "not yours" from "does not exist" can enumerate other
people's conversations.

## Minting reads then writes, and the constraint arbitrates the race

`insert_next_thread` resolves the claim and `max(seq) + 1` in one scoped
`SELECT`, `upsert_insight`'s shape and for its reasons: an out-of-scope claim
produces no source row and therefore no insert, reported as `None` — the same
silence a scoped read gives.

**There is a read-then-write gap in it**, and this module said there was not
until the review of Story 6.3. The `SELECT` above and the `db.add()` + `flush()`
below are two statements with a window between them, and the correction matters
because the false claim was load-bearing: `agents/threads.open_thread` retried
exactly once *on the strength of it*, so a third simultaneous click made the
retry lose too and surfaced as a 500. The gap is closed by the constraint rather
than by the statement's shape, and the retry bound is what has to be right.

What minting deliberately does **not** have is a lock. Two "new conversation"
clicks racing both compute the same `max(seq) + 1`, both insert, and
`uq_copilot_thread_claim_id` refuses one of them with an `IntegrityError` the
caller retries. A lock would serialise a click that happens once a
conversation; a unique constraint costs nothing until the race actually
happens, and — unlike a lock — it is still correct across two api processes.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models import Claim, CopilotThread
from data.repositories.claims import employer_scope


class ThreadClaimMissing(RuntimeError):
    """A thread row names a claim its own caller cannot see. **Impossible; not asserted.**

    A `RuntimeError` and not an `AssertionError`, because `assert` is removed by
    `python -O` and this invariant may not be optional at runtime — see
    `select_claim_business_id`. Deliberately not one of `agents/threads.py`'s
    caller-facing exceptions: those describe things a request can legitimately
    ask for and be refused, and this describes a database that has stopped being
    internally consistent. It reaches the client as an ordinary 500.
    """


class ThreadIdFactory(Protocol):
    """What `insert_next_thread` calls to spell the id it is about to insert.

    A named `Protocol` rather than a bare `Callable` alias so the signature is
    documented where the parameter is read: the three arguments are the whole of
    the key, in the order the id spells them, and nothing else is available to a
    factory — which is the structural half of "no client ever supplies a thread
    id" (AD-6). The implementation lives in `agents/threads.py`; `data/` knows
    only that something can spell one.
    """

    def __call__(self, claim_business_id: str, user_id: int, seq: int) -> str:
        """The thread id for one `(claim, user, seq)`."""
        ...


async def select_claim_threads(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
) -> Sequence[CopilotThread]:
    """The caller's own conversations about one claim, oldest first — scoped.

    Ordered by `conversation_seq` so the switcher renders them in the order they
    happened and so "the current one" is `[-1]` rather than a second query. An
    ordering nobody relies on is one somebody eventually does, which is
    `select_claim_insights`' argument for ordering at all.

    An empty list is the normal state of a claim nobody has opened the panel on.
    It is **also** what a caller outside the claim's employer partition gets and
    what a caller whose colleague owns every thread gets — three different facts
    with one answer, deliberately. The route resolves the claim separately (a
    404 for an invisible one) so that an empty list never tells a stranger the
    claim exists; between two handlers who *can* both see the claim, an empty
    list correctly says "you have no conversations here".
    """
    rows = await db.scalars(
        sa.select(CopilotThread)
        .select_from(CopilotThread)
        .join(Claim, CopilotThread.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .where(CopilotThread.user_id == ctx.user_id)
        .order_by(CopilotThread.conversation_seq)
    )
    return rows.all()


async def select_thread(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    thread_id: str,
) -> CopilotThread | None:
    """One thread by its minted id, or `None` — scoped *and* owned.

    The read on the hot path of every run and every history fetch, and the one
    place the AD-7 re-resolution actually bites: `ctx` here was built by
    `api.deps.get_caller_context` from this request's own cookie, moments ago,
    so a thread whose `user_id` was fine yesterday and whose claim has since
    left the caller's book answers `None` today.

    **`None` for absent, `None` for out of scope, and `None` for somebody
    else's**, deliberately the same answer — `select_insight_claim_pk`'s rule.
    Three distinguishable answers would make this route an oracle for
    enumerating conversations the caller cannot read.
    """
    found: CopilotThread | None = await db.scalar(
        sa.select(CopilotThread)
        .select_from(CopilotThread)
        .join(Claim, CopilotThread.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(CopilotThread.thread_id == thread_id)
        .where(CopilotThread.user_id == ctx.user_id)
    )
    return found


async def select_thread_in_scope(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    thread_id: str,
) -> CopilotThread | None:
    """One thread by its minted id — **employer-scoped, owner-agnostic** (6.5).

    `select_thread` above with the `user_id` predicate dropped and nothing else
    changed. It exists for exactly one caller: the resume path, which AD-6 and
    Story 6.5's AC 2 require to answer **403** when another handler posts a
    decision on somebody else's paused conversation, where the shipped contract
    answers 404 for every unreachable thread.

    ## Why this does not reopen the enumeration oracle

    `select_thread`'s single-answer rule exists because thread ids are readable
    by design — `claim.WC-20017.u7.s1` spells its own claim, owner and sequence
    — so a distinguishable refusal would let a caller enumerate other handlers'
    conversations by guessing. That reasoning is about *reach*, and the employer
    predicate is what bounds it: this function still joins `Claim` under
    `employer_scope(ctx)`, so a row it finds is a conversation about a claim the
    caller can already see, in a book of business they already have. A 403
    derived from it therefore says "inside your own caseload, another handler's
    conversation" and reveals nothing across employers; an out-of-scope thread
    and an id nobody ever minted both still answer `None`, and both still become
    the same byte-identical 404.

    Two further containments, both deliberate. It is reached **only from the
    resume branch** — a message POST never calls it, so both shipped ownership
    tests keep their 404 — and it is reached only *after* `select_thread` has
    already answered `None`, so the common path costs one query as before.
    """
    found: CopilotThread | None = await db.scalar(
        sa.select(CopilotThread)
        .select_from(CopilotThread)
        .join(Claim, CopilotThread.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(CopilotThread.thread_id == thread_id)
    )
    return found


async def select_max_seq(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
) -> int | None:
    """The caller's highest conversation sequence on one claim, or `None` — scoped.

    What "this thread is read-only history" is computed from: a thread is
    current exactly when its `conversation_seq` equals this. A *column* saying
    so would be a second piece of state to keep true, and the one that goes
    wrong is the one that says a superseded thread is still current — see
    `CopilotThread` on why the table is append-only.

    Separate from `select_claim_threads` rather than folded into it because the
    run endpoint needs the comparison and not the list: a post to a thread must
    not cost a read of every conversation the handler has ever had about the
    claim.
    """
    found: int | None = await db.scalar(
        sa.select(sa.func.max(CopilotThread.conversation_seq))
        .select_from(CopilotThread)
        .join(Claim, CopilotThread.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .where(CopilotThread.user_id == ctx.user_id)
    )
    return found


async def select_current_thread_id(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
) -> str | None:
    """The caller's current conversation on one claim, or `None` — scoped.

    The thread with the highest sequence, which is what `is_current` means
    everywhere else in this build. Separate from `select_max_seq` because the
    mint guard needs the *id* (it is what an advisory lock is keyed on) and not
    the number, and separate from `select_claim_threads` because a guard that
    read every conversation a handler has ever had about a claim would make the
    common case pay for a check.

    `None` for a claim with no conversations, for a claim outside the caller's
    book, and for one where every thread belongs to a colleague — the same
    silence every read in this module gives, and for the same reason.
    """
    found: str | None = await db.scalar(
        sa.select(CopilotThread.thread_id)
        .select_from(CopilotThread)
        .join(Claim, CopilotThread.claim_id == Claim.id)
        .where(employer_scope(ctx))
        .where(Claim.claim_id == claim_business_id)
        .where(CopilotThread.user_id == ctx.user_id)
        .order_by(CopilotThread.conversation_seq.desc())
        .limit(1)
    )
    return found


async def insert_next_thread(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
    thread_id_for: ThreadIdFactory,
    created_at: datetime,
) -> CopilotThread | None:
    """Mint the caller's next conversation on one claim. Scoped, no commit.

    Returns the inserted row, or `None` when the claim is not in the caller's
    book — the same silence a scoped read gives, and for the same reason.

    **`thread_id_for` is a callback rather than a string**, because the id is
    derived from the sequence and the sequence is not known until the `SELECT`
    below computes it. Formatting the id in the caller would mean reading
    `max(seq)` there, formatting, and passing both down — the same window with a
    second place to get the arithmetic wrong. Deriving it from the sequence this
    function just read means the id and the sequence cannot disagree.

    It is not a model-supplied value and cannot be: the callback comes from
    `agents/threads.py` and takes only the claim id, the user id and the
    sequence (AD-6 — clients never supply a thread id).

    **No commit**, `upsert_insight`'s contract: the caller owns the transaction
    boundary, because minting a thread and answering with it are one operation
    and a partially committed mint is a thread the switcher shows and the run
    endpoint cannot find.

    Raises `IntegrityError` when two mints race — see the module docstring on
    why the constraint arbitrates rather than a lock, and on the read-then-write
    gap this **does** have. `agents/threads.open_thread` retries it a bounded
    number of times and answers a 409 when the bound is exhausted.
    """
    next_seq = sa.func.coalesce(
        sa.select(sa.func.max(CopilotThread.conversation_seq))
        .where(CopilotThread.claim_id == Claim.id)
        .where(CopilotThread.user_id == ctx.user_id)
        .scalar_subquery(),
        0,
    ) + sa.literal(1)

    resolved = (
        await db.execute(
            sa.select(Claim.id, next_seq.label("next_seq"))
            .where(employer_scope(ctx))
            .where(Claim.claim_id == claim_business_id)
        )
    ).one_or_none()
    if resolved is None:
        return None

    claim_pk, seq = int(resolved.id), int(resolved.next_seq)
    row = CopilotThread(
        thread_id=thread_id_for(claim_business_id, ctx.user_id, seq),
        claim_id=claim_pk,
        user_id=ctx.user_id,
        conversation_seq=seq,
        created_at=created_at,
    )
    db.add(row)
    # Flush rather than commit: the row (and its identity key) must exist for
    # the response to be built, and the constraint violation that arbitrates a
    # race must surface *here* rather than at an implicit commit the caller
    # cannot wrap.
    await db.flush()
    return row


async def select_claim_business_id(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_pk: int,
) -> str:
    """The claim's `WC-nnnn` for a thread row's surrogate key — scoped.

    Scoped although its caller has already resolved the thread through
    `select_thread` (which is scoped *and* owned, so the claim is one the caller
    demonstrably sees). The filter is therefore redundant today, and it is
    applied anyway for the reason `upsert_insight` applies its: **there is no
    second predicate in this file**, and a repository with one unscoped read is
    a repository whose next author reasonably concludes that scoping is
    situational. It costs one `AND` on an indexed column.

    Deliberately not folded into `select_thread`'s statement. That read is on the
    hot path of every run and every history fetch, and joining a third table to
    fetch a string only two of its callers need would make the common case pay
    for the uncommon one.

    **Raises rather than returning `None`, and raises rather than asserting.**
    The foreign key means a thread row cannot name a claim that does not exist,
    and the scope predicate cannot exclude a claim whose thread the same context
    just resolved — so a miss is a corrupted database rather than a caller
    error, and the run must stop.

    It was a bare `assert` until the review of Story 6.3, which is the wrong
    statement for a security-relevant invariant: `python -O` compiles `assert`
    away entirely, and the invariant would then not fail — `None` would flow on
    into `ThreadView.claim_business_id`, from there into the run's bound claim
    and into a thread id, silently. An invariant that disappears under an
    interpreter flag is not an invariant, and this is the one that says a
    conversation belongs to the claim it names.
    """
    found: str | None = await db.scalar(
        sa.select(Claim.claim_id).where(employer_scope(ctx)).where(Claim.id == claim_pk)
    )
    if found is None:
        raise ThreadClaimMissing(
            f"copilot_thread names claim {claim_pk}, which this caller cannot see"
        )
    return found
