"""Thread identity, single-flight, and the interrupt probe (Story 6.3, AD-6).

Three small things that all answer questions about a *thread* rather than about
a graph:

1. **What is this conversation called?** `mint_thread_id` spells
   `(claim, user, seq)` into the string the saver files checkpoints under.
2. **May a message be accepted right now?** `run_lock` holds a Postgres
   advisory lock for the length of a run, and `thread_state` asks the saver
   whether the thread is paused on a human.
3. **What did this conversation say?** `thread_state` also reads the
   checkpointed messages back through the saver — never with SQL against
   `checkpoints`, which AD-3 forbids.

…and, at the bottom, **the thread service**: `list_threads`, `open_thread` and
`resolve_thread`, which own `copilot_thread` and are the only writers of it.
Story 6.3's Data note asks for exactly that ("owned by the thread service in
`agents/`'s server-side module"), and it is also what keeps
`api/routers/copilot.py` free of a `commit()` —
`tests/test_claim_edit_validation.py::test_no_router_writes_through_a_session`
enforces that on every aggregate in the build.

## This module imports `data/repositories/` directly, and that is the one place
`agents/` does

The dependency direction is composition root → `agents/` → `services/` →
`data/`, and every other module in this package respects it: the tools call
services, `insights.py` calls `services/rag`, and none of them holds a
repository. This one skips a layer, so it owes an argument.

AD-12 gives every entity exactly one write-owning module, and `copilot_thread`'s
owner is *this* one — the story says so by name. A `services/copilot.py` that
existed only to hold three statements this module immediately wraps would be a
layer with no decisions in it, and it would put a conversation's identity in the
package whose whole rule is that it never runs inference; the thread and the
graph would then be owned by two modules that have to agree about single-flight.

What the direction actually protects is that **`services/` never imports
`agents/`** — the cycle that would put chat code inside the package owning
`ai_insight` — and that is untouched and now machine-checked
(`tests/test_layering.py`). The skip is downward, acyclic, and confined to this
file.

## Single-flight is two conditions and one answer

AD-6: a second message on a thread that is running **or** interrupt-pending is
refused. Those are genuinely two conditions, and neither implies the other:

- A **run in flight** holds `run_lock`. That is crash-safe by construction — a
  dead process drops its connection and PostgreSQL drops the lock with it — which
  an in-memory flag or a `running` column is not: both survive a `kill -9` as a
  thread nobody can ever message again.
- A run that ends in an **interrupt** releases its lock while the thread must
  still refuse new messages, because the next thing it expects is an approval,
  not a question. So the second condition is read back **from the saver's own
  state**, which is what the story means by "re-derive from the saver rather
  than trusting an in-memory flag". It is true across processes and across
  restarts for free.

Both answer the same 409 with the same `type`. A caller does not need to know
which, and telling them would be telling them about a run they cannot see.

There is a third condition and it is on a different route: **"new conversation"
while a run is in flight** (`open_thread` → `ThreadBusy`). It is the same lock
read from the other side, and it was missing until the review of Story 6.3 —
minting `seq + 1` while `seq` was still streaming left the run checkpointing an
answer into a thread that had just become read-only, so the handler's question
was answered into a conversation they could no longer continue. Same 409, same
`type`, same advice.

## Why the lock is session-scoped, on its own connection, in AUTOCOMMIT

`services/rag/insights.py::_claim_generation_lock` is the working precedent and
its docstring argues the mechanism in full; the same three constraints bite here
harder, because a chat turn is longer than a refresh:

- `pg_try_advisory_xact_lock` (transaction-scoped) is released by the first
  commit — and this lock has to outlive *every* commit a turn makes.
- A session-scoped lock taken on the **request's** session rides a connection
  SQLAlchemy hands back to the pool at that commit, leaking the lock onto
  whatever asks for that connection next.
- So the lock lives on a connection this module checks out and returns itself,
  in `AUTOCOMMIT`, which also means it is never an idle-in-transaction hold —
  the thing the Story 6.2 review removed from the refresh path (H2) and the
  thing a streaming endpoint must not reintroduce at a bigger multiplier.

`pg_try_advisory_lock` rather than the blocking form, and here the *loser does
not yield*: unlike a duplicate insight refresh, a second chat message is not
work somebody else is already doing. It is a different question, and the honest
answer is 409.
"""

import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
import structlog
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from data.context import CallerContext
from data.repositories import copilot as copilot_repo

log = structlog.get_logger()

#: The advisory-lock namespace for copilot runs.
#:
#: `60_002` is the insight refresh's (`services/rag/insights.py`), so this is
#: `60_003`. Distinct namespaces because the two locks protect different things
#: and are keyed by different ids — a claim's primary key there, a thread's hash
#: here — and a shared namespace would let a refresh of claim 47 block a
#: conversation whose thread happened to hash to 47.
RUN_LOCK_NAMESPACE = 60_003

#: How many times a mint may lose the unique-constraint race before it is a 409.
#:
#: Five, and the number is chosen against the thing being raced rather than
#: pulled out of the air: the contention is a human pressing "+ New", and five
#: simultaneous presses on one claim by one handler is already past what a
#: person can do. Bounded rather than unbounded because a spin under any *other*
#: kind of contention would be the wrong answer — exhausting the bound says
#: something is happening that a retry will not fix, and 409 says so.
_MINT_ATTEMPTS = 5

#: The thread-id shape: `claim.<claim business id>.u<user id>.s<seq>`.
#:
#: Readable on purpose. This string appears in log lines (it is the one copilot
#: identifier AD-11 permits there), in the saver's tables, and in an operator's
#: `psql` session at three in the morning — and a UUID would make every one of
#: those a join. It carries no PHI: a claim business id, a surrogate user id and
#: a small integer.
#:
#: `claim.` is a literal scope prefix rather than decoration. AD-6 keys threads
#: `(scope, user_id, conversation_seq)` with `dashboard` reserved and unused in
#: v1, so the prefix is where that reservation lives: Epic 7 mints
#: `dashboard.u7.s1` and changes no key that already exists.
THREAD_ID_TEMPLATE = "claim.{claim_business_id}.u{user_id}.s{seq}"

#: What a minted thread id looks like, for the route's own validation.
#:
#: The API never *accepts* a thread id as a value it acts on without looking it
#: up — `select_thread` is scoped and owned, so a forged id is a 404 — but a
#: path parameter still needs a pattern, both to keep junk out of the generated
#: client's types and so an obviously malformed id is refused by the schema
#: rather than by a database round trip.
THREAD_ID_PATTERN = r"^claim\.WC-\d{4,6}\.u\d+\.s\d+$"

_THREAD_ID = re.compile(THREAD_ID_PATTERN)


def mint_thread_id(claim_business_id: str, user_id: int, seq: int) -> str:
    """The thread id for one `(claim, user, seq)`. **Servers mint; clients never do.**

    AD-6 states it and the API enforces it by omission: there is no request body
    anywhere on the copilot router with a thread id in it, and the only way a
    thread comes into existence is `POST …/threads`, which takes a claim and
    reads the caller off the cookie.

    Deterministic, so the id and the row can be reconstructed from each other —
    which is what makes `tests/test_copilot_threads.py` able to assert the key
    without reading it back out of the database it just wrote.
    """
    return THREAD_ID_TEMPLATE.format(claim_business_id=claim_business_id, user_id=user_id, seq=seq)


def is_thread_id(value: str) -> bool:
    """Whether a string is shaped like a minted id. Shape only — never authority."""
    return _THREAD_ID.match(value) is not None


def _lock_key(thread_id: str) -> int:
    """A stable 32-bit key for one thread id.

    `pg_try_advisory_lock(int, int)` takes two 32-bit integers, and a thread has
    no small integer of its own that is stable across processes — the
    `copilot_thread.id` surrogate key is, but reading it would make taking the
    lock a database round trip the run has already made and would couple the
    lock to a row the saver knows nothing about.

    A hash therefore, and a collision is not a correctness problem: two threads
    that collide serialise against each other, which costs one of them a 409 it
    did not earn. Rare (32 bits over the number of concurrently-running threads
    in a single-site deployment) and harmless in the direction that matters —
    the failure is a refusal, never a shared conversation.

    `zlib.crc32` rather than `hash()`, which is randomised per process by
    `PYTHONHASHSEED` and would give two api workers two different locks for one
    thread — a single-flight guard that does not guard.
    """
    from zlib import crc32

    # Signed, because `pg_try_advisory_lock`'s arguments are `int4`.
    return crc32(thread_id.encode()) - 0x80000000


@asynccontextmanager
async def run_lock(db: AsyncSession, *, thread_id: str) -> AsyncIterator[bool]:
    """Hold the thread's run lock for one turn. Yields whether it was won.

    See the module docstring for the mechanism and for why the loser does not
    wait. `db` is used only to reach the engine — nothing is executed on the
    request's session, and the lock deliberately does not ride it.

    A bind that is not an `AsyncEngine` — a session built around a single
    connection, a shape this build does not use but the type admits — yields
    `True` unlocked, `_claim_generation_lock`'s rule: refusing to answer because
    a lock could not be *taken* is worse than the interleaving it prevents.
    """
    bind = db.bind
    if not isinstance(bind, AsyncEngine):  # pragma: no cover - not a shape this build builds
        yield True
        return

    key = _lock_key(thread_id)
    async with bind.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        acquired = bool(
            await conn.scalar(sa.select(sa.func.pg_try_advisory_lock(RUN_LOCK_NAMESPACE, key)))
        )
        try:
            yield acquired
        finally:
            if acquired:
                await conn.scalar(sa.select(sa.func.pg_advisory_unlock(RUN_LOCK_NAMESPACE, key)))


async def is_thread_running(db: AsyncSession, *, thread_id: str) -> bool:
    """Whether a run currently holds this thread's advisory lock. **Asks; never takes.**

    A read of `pg_locks` rather than a `pg_try_advisory_lock` probe, and the
    difference is the whole of why this function is safe to call from a route
    that is not starting a run. A try-acquire that *won* would hold the lock for
    the microseconds until it released it, which is exactly long enough to make
    a genuine run's request lose a race it should have won — a guard that caused
    the refusal it was written to report.

    `pg_locks` is a system view rather than one of this build's tables, so this
    is not the SQL AD-3 forbids: it names no checkpoint table and reads no
    conversation. `objid` is unsigned where `_lock_key` is signed (the lock's
    own arguments are `int4`), which is what the mask is for.

    Scoped to the current database, because advisory locks are per-database and
    `pg_locks` is not: a second database on the same cluster holding the same
    key is not this thread running.

    Used by `open_thread`, so that "new conversation" cannot be minted out from
    under a run that is still streaming into the thread it would supersede —
    which would checkpoint an in-flight answer into a thread that had just
    become read-only, with no route left that could show it.
    """
    bind = db.bind
    if not isinstance(bind, AsyncEngine):  # pragma: no cover - not a shape this build builds
        return False
    held = await db.scalar(
        sa.text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted "
            "AND classid = :namespace AND objid = :key "
            "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
        ),
        {"namespace": RUN_LOCK_NAMESPACE, "key": _lock_key(thread_id) & 0xFFFFFFFF},
    )
    return bool(held)


def saver_config(thread_id: str) -> RunnableConfig:
    """The `configurable` dict every saver call takes. One spelling, one place.

    Trivial, and named anyway: `{"configurable": {"thread_id": …}}` spelled at
    six call sites is six chances to write `threadId`, and the failure mode is a
    run that silently starts a *new* conversation rather than raising.

    Typed as the vendor's `RunnableConfig` rather than as `dict[str, Any]`,
    because that is what `astream` and `aget_state` actually take: a plain dict
    satisfies neither of their overloads, which is a thing nobody noticed for as
    long as the graph it was passed to was itself typed `Any`.
    """
    return {"configurable": {"thread_id": thread_id}}


@dataclass(frozen=True)
class ThreadRunState:
    """What the saver knows about a thread right now.

    Two booleans, a message list and the pending pause's payload, so that the
    runs endpoint asks one question and the history route asks another without
    either reaching into a checkpoint structure itself.
    """

    exists: bool
    interrupt_pending: bool
    messages: tuple[BaseMessage, ...]
    #: The `HITLRequest` the thread is paused on, or `None` (Story 6.5 review).
    #:
    #: **Read from the saver, which is what makes an approval survive a
    #: remount.** The interrupt payload reached the client on the run's terminal
    #: frame and nowhere else, so it lived only in the browser's runtime state:
    #: switching to 📓 Diary, reloading the page or coming back tomorrow
    #: discarded the card while the thread stayed interrupt-pending — a
    #: conversation that 409s every message with no reachable way to answer it.
    #: The pause itself was always durable; only the description of it was not.
    #:
    #: Typed `Any` for `_interrupt_value`'s reason in the runs endpoint: this is
    #: the vendored middleware's own payload, passed through untouched so the
    #: approval card renders the server's pending call rather than a paraphrase
    #: of it (AD-16). `None` whenever nothing pends, and also when something
    #: pends whose payload this build cannot read — a client renders no card
    #: either way, and the run endpoint is where that is reported as an error.
    interrupt_value: Any = None


async def thread_state(
    graph: Any,
    *,
    thread_id: str,
) -> ThreadRunState:
    """Read one thread's checkpointed state back — **through the graph, not SQL**.

    AD-3's exception has a second half that is easy to miss: vendoring the DDL
    buys nothing if application code then selects from the tables anyway. So
    this asks the compiled graph, which asks its checkpointer. There is no
    `SELECT … FROM checkpoints` anywhere in `server/`, and
    `tests/test_layering.py` greps for one.

    `interrupt_pending` is derived from the saver's own next-task state rather
    than from a flag this process kept: a thread paused on an approval has tasks
    with interrupts attached, and that is true across a restart and across two
    api workers. An in-memory flag is true in one process until it is restarted,
    which is the failure the story means by "re-derive from the saver".

    A thread with no checkpoint at all — a freshly minted one nobody has messaged
    — answers `exists=False` with an empty transcript, which is a first-class
    state and not an error: the panel renders the deterministic greeting and an
    empty composer.

    **The pause's payload comes back with it** (`interrupt_value`), off the same
    snapshot rather than from a second read: "is this thread paused?" and "what
    is it paused on?" are one question asked of one structure, and answering
    them separately is how they come to disagree.
    """
    snapshot = await graph.aget_state(saver_config(thread_id))
    values = snapshot.values if snapshot is not None else None
    if not values:
        return ThreadRunState(exists=False, interrupt_pending=False, messages=())
    interrupts = [
        item for task in (snapshot.tasks or ()) for item in getattr(task, "interrupts", ())
    ]
    messages: Sequence[BaseMessage] = values.get("messages", ())
    return ThreadRunState(
        exists=True,
        interrupt_pending=bool(interrupts),
        messages=tuple(messages),
        # `.value` through `getattr` for the runs endpoint's recorded reason:
        # the vendor's `Interrupt` is a dataclass in one version and a
        # `NamedTuple` in another, and a client rendering no card is a better
        # failure than a transcript read that raises.
        interrupt_value=next(
            (value for value in (getattr(item, "value", None) for item in interrupts) if value),
            None,
        ),
    )


async def discard_thread(
    checkpointer: BaseCheckpointSaver[Any],
    *,
    thread_id: str,
) -> None:
    """Delete every checkpoint for one thread, through the saver.

    Not reachable from any route in this story — nothing deletes a conversation
    — and present because Epic 8's purge cascade needs a door that is not a
    `DELETE` statement against a vendored table. `adelete_thread` is the saver's
    own, so a future schema change is the vendor's problem rather than a purge
    job silently missing a table.
    """
    await checkpointer.adelete_thread(thread_id)


__all__ = [
    "RUN_LOCK_NAMESPACE",
    "ClaimNotInBook",
    "ThreadBusy",
    "ThreadList",
    "ThreadMintContended",
    "ThreadNotFound",
    "ThreadNotOwned",
    "ThreadReadOnly",
    "ThreadView",
    "THREAD_ID_PATTERN",
    "THREAD_ID_TEMPLATE",
    "ThreadRunState",
    "discard_thread",
    "is_thread_id",
    "is_thread_running",
    "mint_thread_id",
    "list_threads",
    "open_thread",
    "resolve_thread",
    "run_lock",
    "saver_config",
    "thread_state",
]


# --- the thread service -------------------------------------------------
#
# Story 6.3's Data note is explicit: if a bookkeeping table proves necessary,
# "it is owned by the thread service in `agents/`'s server-side module". This is
# that service. It exists as a layer rather than as three repository calls in a
# router for the reason `tests/test_claim_edit_validation.py` enforces on every
# other aggregate — a router that could write is a route whose mutation nobody
# audited, and the invariant rests on there being one place a write happens.


@dataclass(frozen=True)
class ThreadView:
    """One conversation as the API publishes it.

    A view rather than the ORM row, `MeetingView`'s arrangement and for its
    reason: `is_current` is **server-derived** (`seq == max(seq)`) and is not a
    column, so a payload built straight off the model would either omit it or
    invite the router to compute it — which would put the read-only rule in two
    places, with the 409 arriving as a surprise when they disagreed.
    """

    thread_id: str
    claim_business_id: str
    conversation_seq: int
    is_current: bool
    created_at: datetime


@dataclass(frozen=True)
class ThreadList:
    """A claim's conversations for one caller, and which of them is current."""

    items: tuple[ThreadView, ...]
    current_thread_id: str | None


class ClaimNotInBook(Exception):
    """The claim is not in this caller's book — the route's 404.

    Named after `services/claims/detail.ClaimNotVisible` rather than reusing it,
    because this layer must not depend on which service happened to resolve the
    claim. The router maps both onto the case file's one 404 wording.
    """


class ThreadNotFound(Exception):
    """No such thread for this caller — absent, foreign, or out of scope alike.

    One exception for three facts, deliberately, so the route cannot answer them
    differently. See `data/repositories/copilot.py::select_thread`.
    """


class ThreadNotOwned(Exception):
    """A resume was posted to another handler's conversation — the route's 403.

    **The one place this build distinguishes "not yours" from "not there"**, and
    it is reachable only from a resume. AD-6 and Story 6.5's AC 2 require 403
    when the thread's own user is not the caller; every other route on the
    copilot router answers the same 404 for absent, foreign and out-of-scope
    alike, deliberately, because thread ids are readable and a distinguishable
    refusal would be an enumeration oracle.

    The reconciliation is `copilot_repo.select_thread_in_scope`, which keeps the
    employer predicate and drops only the owner one: a 403 therefore means
    "inside your own book of business, somebody else's conversation" and says
    nothing about any claim the caller cannot already see. Out of scope, or
    never minted, stays `ThreadNotFound`.
    """


class ThreadReadOnly(Exception):
    """A superseded thread was posted to. The route's `/problems/thread-read-only`."""


class ThreadBusy(Exception):
    """A new conversation was asked for while the current one is still answering.

    The route's `/problems/thread-busy`, and the *third* cause of a 409 that the
    module docstring's two conditions did not cover: not "a second message on a
    running thread" but "supersede a running thread". The outcome is the same
    kind of harm — an answer checkpointed into a conversation that became
    read-only while it was being written — so it is the same refusal with the
    same `type`, and a handler waits a moment and presses the button again.
    """


class ThreadMintContended(Exception):
    """`insert_next_thread` lost the minting race every attempt it was given.

    Distinct from `ThreadBusy` because it says something different: not "this
    conversation is answering" but "this claim's sequence is being minted faster
    than a bounded retry can follow". The route answers 409 either way — retry
    is the only useful advice for both — but the two are separate exceptions so
    a log line and a future metric can tell an operator which one is happening.
    """


async def list_threads(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
) -> ThreadList:
    """The caller's conversations about one claim, oldest first. Reads only."""
    rows = await copilot_repo.select_claim_threads(db, ctx, claim_business_id=claim_business_id)
    current = rows[-1].conversation_seq if rows else None
    return ThreadList(
        items=tuple(
            ThreadView(
                thread_id=row.thread_id,
                claim_business_id=claim_business_id,
                conversation_seq=row.conversation_seq,
                is_current=row.conversation_seq == current,
                created_at=row.created_at,
            )
            for row in rows
        ),
        current_thread_id=rows[-1].thread_id if rows else None,
    )


async def open_thread(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
    now: datetime | None = None,
) -> ThreadView:
    """Mint the caller's next conversation on a claim, and commit it.

    **One operation behind two affordances** — "the panel opened on a claim with
    no conversation" and "the handler pressed New conversation" are the same
    request, `max(seq) + 1`. A separate get-or-create would need a "did it
    already exist?" answer the switcher does not use and would make the first
    conversation a different code path from every later one.

    The prior thread becomes read-only by arithmetic rather than by an update:
    `is_current` is `seq == max(seq)`, so nothing is written to the old row.

    **A run in flight on the current thread refuses the mint** (`ThreadBusy`).
    Superseding a conversation that is still answering would leave the run
    checkpointing an assistant turn into a thread that had become read-only
    while it was being written: the transcript route would still show it, but no
    composer would, and the handler would have asked a question whose answer
    landed in a conversation they can no longer continue. The check is
    `is_thread_running`, which reads `pg_locks` rather than taking the lock —
    see that function on why a try-acquire probe would cause the refusal it
    reports.

    It is a check and not a lock, so it is a narrow race rather than a
    guarantee: a run that starts in the microseconds after the read still wins.
    That is the right trade here, because the failure it leaves is the one that
    was already possible before either request arrived, and the alternative —
    holding the run lock across a mint — would make "new conversation" wait on a
    model.

    **A race between two mints is arbitrated by the unique constraint, and
    retried up to `_MINT_ATTEMPTS` times.** Two clicks both compute the same
    next sequence, both insert, and `uq_copilot_thread_claim_id` refuses one —
    whose retry then reads a `max(seq)` that includes the winner. This was
    retried exactly **once** until the review of Story 6.3, on a claim in
    `data/repositories/copilot.py` that there was "no read-then-write gap" in
    the minting statement. There is: it is a `SELECT` for the claim and the next
    sequence, then a `db.add()` and a `flush()`. Two clicks fit through it, and
    a third makes the second retry lose too — which surfaced as a 500. A small
    bound rather than an unbounded loop, because the contention this covers is
    humans pressing a button and a spin would be the wrong answer to anything
    else; exhausting it raises `ThreadMintContended`, which the route answers
    409 rather than 500.

    **No `audit_event`, and that is a decision rather than an omission.** AD-4 is
    stated without a carve-out, so a committed write with no event beside it has
    to say why. This row records neither a change to a claim nor a fact about
    one: it is a conversation's *identity*, the same class of thing as a session
    or a cursor, and nothing else in this build audits one of those.
    `AiInsightAttempt` makes the same argument for the same reason, and the cost
    of the alternative settles it — one event per panel-open, in the table Epic
    8's retention review reads, burying the events that say a model wrote
    something into the case file's neighbourhood.

    What *would* be audited is a copilot write to a claim, and there is none:
    Story 6.5's `record_copilot_approval` is where that arrives.
    """
    at = now or datetime.now(UTC)
    current = await copilot_repo.select_current_thread_id(
        db, ctx, claim_business_id=claim_business_id
    )
    if current is not None and await is_thread_running(db, thread_id=current):
        raise ThreadBusy(current)

    for attempt in range(1, _MINT_ATTEMPTS + 1):
        try:
            row = await copilot_repo.insert_next_thread(
                db,
                ctx,
                claim_business_id=claim_business_id,
                thread_id_for=mint_thread_id,
                created_at=at,
            )
        except IntegrityError as exc:
            await db.rollback()
            if attempt == _MINT_ATTEMPTS:
                log.info("copilot.thread_mint_contended", attempts=attempt)
                raise ThreadMintContended(claim_business_id) from exc
            continue
        if row is None:
            raise ClaimNotInBook(claim_business_id)
        await db.commit()
        log.info(
            "copilot.thread_minted",
            thread_id=row.thread_id,
            conversation_seq=row.conversation_seq,
        )
        # Freshly minted, so by definition the highest sequence there is.
        return ThreadView(
            thread_id=row.thread_id,
            claim_business_id=claim_business_id,
            conversation_seq=row.conversation_seq,
            is_current=True,
            created_at=row.created_at,
        )
    raise ThreadMintContended(claim_business_id)  # pragma: no cover - the loop returns or raises


async def resolve_thread(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    thread_id: str,
    for_posting: bool,
    for_resume: bool = False,
) -> ThreadView:
    """One thread, under a freshly resolved context. Raises the route's refusals.

    The AD-7 re-resolution in one call: `ctx` was built by
    `api.deps.get_caller_context` from *this* request's cookie, so a thread whose
    `user_id` was fine yesterday and whose claim has since left the caller's book
    is a `ThreadNotFound` today — never a 403, and never distinguishable from an
    id that was never minted.

    `for_posting` is what adds the read-only check. A superseded thread stays
    readable for as long as its checkpoints do; only a run against it is refused.

    `for_resume` is Story 6.5's, and it is the **only** flag that can turn a 404
    into a 403. AD-6 requires that a decision on a paused conversation be
    refused 403 when the caller is not the thread's own user, and the second
    lookup below is what tells "somebody else's" from "nowhere": it keeps the
    employer predicate and drops only the owner one, so the distinction it draws
    is bounded to claims the caller can already see. See `ThreadNotOwned` and
    `copilot_repo.select_thread_in_scope`.

    The extra query happens **only after the ordinary lookup has already failed**
    and only on a resume, so the common path is unchanged and no message POST
    can reach it — which is what keeps both shipped ownership tests green.
    """
    row = await copilot_repo.select_thread(db, ctx, thread_id=thread_id)
    if row is None:
        if for_resume:
            foreign = await copilot_repo.select_thread_in_scope(db, ctx, thread_id=thread_id)
            if foreign is not None:
                raise ThreadNotOwned(thread_id)
        raise ThreadNotFound(thread_id)
    claim_business_id = await copilot_repo.select_claim_business_id(db, ctx, claim_pk=row.claim_id)
    current = await copilot_repo.select_max_seq(db, ctx, claim_business_id=claim_business_id)
    is_current = row.conversation_seq == current
    if for_posting and not is_current:
        raise ThreadReadOnly(thread_id)
    return ThreadView(
        thread_id=row.thread_id,
        claim_business_id=claim_business_id,
        conversation_seq=row.conversation_seq,
        is_current=is_current,
        created_at=row.created_at,
    )
