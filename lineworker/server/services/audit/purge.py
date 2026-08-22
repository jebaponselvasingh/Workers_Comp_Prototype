"""The PHI purge cascade — AD-11's deletion side, and the only one there is.

Seven epics wrote protected health information into eleven claim-keyed tables,
three user-keyed ones, a vector store, an insight cache, four vendored
checkpoint tables, a blob volume and an append-only audit log, and until this
module nothing in the build could delete any of it. Every Epic 4/6/7 story that
touched a PHI table recorded the same line in `deferred-work.md`: it left the
cascade a handle and did not invent a deletion path, because Story 8.1 owns the
cascade. This is it.

Two commands, `purge_claim` and `purge_user`, and the rule they exist to make
true is AD-11's second sentence: **no other code path deletes PHI.** That is not
a convention here — `tests/test_purge_ownership.py` reads the tree and fails the
suite when a module outside `services/audit` grows a delete against a PHI-class
table. AD-12 registers this package as the one exception to per-entity write
ownership: `services/claims` owns writes to `document`, and `services/audit`
deletes it, because a cascade assembled out of fifteen services' own delete
commands is fifteen places for a store to be forgotten.

## Five properties, and each one shapes the code below

**Ordered, because no foreign key in this schema has `ondelete`.** Not one
across the whole case file, and that is deliberate — `data/models/core.py`
declares no `relationship()` either, so nothing in this build deletes anything
by implication. The consequence is that the database will refuse a parent-first
delete, and the order in `CLAIM_CHILDREN` is therefore load-bearing rather than
tidy. A store added to the schema and not to that tuple is a store the purge
silently skips, which is why the sweep test asserts emptiness table by table
rather than trusting a count.

**Idempotent and re-runnable.** A second purge of the same subject deletes
nothing, redacts nothing, emits no audit event and logs nothing — every
statement is "delete what matches", so a run with nothing matching is a clean
no-op (`services/financials/batch.py`'s structural idempotence, one story over).
This is not a nicety: a purge that cannot be re-run after a partial failure is a
purge that cannot be trusted, and the blob and redaction steps below are
precisely the ones that can fail half-way — both run *after* the relational
commit, at which point the `claim` row that names the subject is already gone.
So re-runnability cannot rest on that row existing. `purge_claim` therefore
falls back to the **audit skeleton** when the row is absent: a prior `purge`
event naming this business id is proof the relational phase committed, and the
command re-runs the redaction (whose predicate is business-id based and needs no
row) instead of raising `ClaimNotFound`. An id with no such event is a genuinely
unknown claim and is still refused.

**A blob key, once its row is gone, is unrecoverable.** That asymmetry is why
the re-run above cannot finish the *binary* half: `document.blob_key` is the
only handle an object has, and the relational commit destroyed it. So the blob
loop may not be allowed to abort the run either — `_delete_blobs` guards every
key, counts the failures and logs an orphan count rather than raising, because
the alternative is a cascade whose one recoverable step (redaction) is skipped
by a failure in its one unrecoverable step.

**Blobs go last and outside the transaction.** The keys are read off `document`
and `photo` *before* their rows are deleted, and `BlobStore.delete` is called
*after* the transaction commits. Both halves matter: a key cannot be recovered
from a deleted row, and an object deleted for a transaction that then rolled
back is unrecoverable — whereas an object left behind by a transaction that
committed is at worst orphaned, and `delete` is documented idempotent so a
re-run of the whole command is safe (`services/blobstore/__init__.py`).

**Checkpoints arrive as an injected callable.** `services/` may not import
`agents/` (`tests/test_layering.py`), and no module may name a checkpoint table
in a string literal (same file, AD-3's exception). So this module does not know
what a checkpoint *is*: it hands a thread id to a `Callable[[str],
Awaitable[None]]` and the composition root — `scripts/purge_claim.py`, or the
api's lifespan for the retention job — binds that to
`agents.threads.discard_thread`, which calls the saver's own `adelete_thread`.
The count this module reports is therefore **threads whose transcripts were
discarded**, not checkpoint rows, because the saver's API returns nothing and
this build may not count rows in a table it has declared it does not own.

**Audit rows are redacted, never deleted.** The audit log's disposition is the
one that differs from every other store: the skeleton (`actor_id`, `actor_role`,
`action`, `entity`, `entity_id`, `at`) survives so that action history stays
provable, and only the PHI-bearing diffs are overwritten. That runs on a second
connection under `SET ROLE audit_redactor` (`_redactor.py`), and the `purge` and
`redact` events it produces are INSERTed by the **app** role, because the
redactor cannot insert — which is the shape AD-4 designed and not an accident of
plumbing.

## What is deliberately not here

No HTTP endpoint and no UI. Story 8.1's Dev Notes put admin exposure behind the
deferred IdP decision, so the invocation surface is `scripts/purge_claim.py` and
the two scheduled jobs in `retention.py`, and nothing else. No `app_user` row is
ever deleted: `claim.handler_id` and `audit_event.actor_id` point at it and the
audit skeleton has to survive, so `purge_user` purges a user's *artifacts* and
leaves their account exactly where it was.
"""

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, cast

import sqlalchemy as sa
import structlog
from sqlalchemy import CursorResult
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from config import Settings
from data.context import CallerContext
from data.models import (
    AdditionalInjury,
    AiInsight,
    AiInsightAttempt,
    AppUser,
    AuditEvent,
    Base,
    Bill,
    Claim,
    ClaimEmbedding,
    CopilotThread,
    DiaryNote,
    Document,
    EmailLog,
    Employee,
    Expense,
    Meeting,
    PaymentScheduleWeek,
    Photo,
    Session,
    TimelineEvent,
    TreatmentPlanStep,
)
from data.models.enums import UserRole
from services import audit
from services.audit._redactor import RedactorUnavailable, redactor_connection
from services.blobstore import BlobStore, BlobStoreError

log = structlog.get_logger()

#: The `action` every store-level purge event carries.
#:
#: One flat name rather than a `purge.<store>` family, and the asymmetry with
#: `copilot_approval.rejected` and `export.xlsx` is deliberate. Those two encode
#: a *fact about the event* in `action` because the diff columns that would
#: otherwise hold it are erasable by this very cascade. A purge event has
#: nothing of that kind to protect: what was purged is already in `entity`,
#: which is skeleton and survives, so spelling it twice would buy nothing and
#: would make "show me everything one purge did" two filters instead of one.
PURGE_ACTION: Final[str] = "purge"

#: The `action` every redaction event carries. AC 4: one per redacted row.
REDACT_ACTION: Final[str] = "redact"

#: The `entity` a redaction event names — the table whose row was rewritten.
#:
#: `entity_id` carries the rewritten row's own `audit_event.id`, so a reader can
#: put the redaction and the row it redacted side by side. That is the one place
#: in this build where an `entity_id` is a surrogate key rather than a business
#: id, and it is right here for the reason it is wrong everywhere else: an audit
#: row has no business identity, and its surrogate never stops resolving because
#: the row is never deleted.
REDACTED_ENTITY: Final[str] = "audit_event"

#: The JSONB key a redacted diff carries, and the whole of the "already done"
#: test.
#:
#: A *key* rather than a whole document, because that is what the idempotence
#: check needs: "`before` has no `redacted` key" is the predicate that tells an
#: untouched diff from one this cascade has already overwritten, and it stays
#: true whatever `redacted_at` says. A marker matched on the timestamp instead
#: would make a second run rewrite every row it had already rewritten and emit a
#: second `redact` event for each — the opposite of the property this cascade
#: promises.
REDACTION_MARKER: Final[str] = "redacted"

#: When the redaction happened, beside the marker. ISO-8601, because the column
#: is JSONB and `json.dumps` has never heard of a `datetime`
#: (`services/claims/notes.py::_diff`'s note, one column over).
REDACTED_AT_KEY: Final[str] = "redacted_at"

#: The store name the checkpoint step reports under.
#:
#: Deliberately **not** the name of any of the four tables the saver owns. This
#: module may not name them (`tests/test_layering.py`), it does not know how
#: many of them there are, and the number it can honestly report is threads
#: discarded rather than rows deleted — so the store is named for what a reader
#: would call it rather than for a table this package has declared it does not
#: read.
CHECKPOINT_STORE: Final[str] = "copilot_checkpoint"

#: The `entity` an audit row about the claim itself carries, and the store name
#: the `claim` row is counted under.
CLAIM_ENTITY: Final[str] = "claim"

#: The store name the injured worker's own row is counted under.
EMPLOYEE_ENTITY: Final[str] = "employee"

#: The store whose deletion the checkpoint sweep must immediately precede — the
#: rows that are the only index of the thread ids the saver files under.
THREAD_ENTITY: Final[str] = "copilot_thread"

#: The `entity` an `ai_insight` audit row carries, whose `entity_id` is
#: `<claim business id>:<kind>` (`services/rag/insights.py`).
_INSIGHT_ENTITY: Final[str] = "ai_insight"

#: The key every child entity's audit diff carries so that this cascade can find
#: it: `services/claims/notes.py`, `meetings.py`, `emails.py` and `injuries.py`
#: each put the claim's **business** id in `before`/`after` and each says in its
#: `_diff` docstring that Story 8.1 is why.
_CLAIM_ID_KEY: Final[str] = "claim_id"

#: The audit log as a plain Core table.
#:
#: `AuditEvent.__table__` is typed `FromClause`, which the Core `select`/
#: `update` constructors will not take under strict mypy, and the cast is
#: narrowing rather than lying: a declarative model's `__table__` is a
#: `Table`. Core rather than the ORM entity on purpose — the redaction runs on
#: a bare `AsyncConnection` under a different role, where there is no session
#: and no identity map for an ORM-enabled statement to reach for.
_AUDIT: Final[sa.Table] = cast(sa.Table, AuditEvent.__table__)


class ClaimNotFound(LookupError):
    """No claim carries that business id.

    A `LookupError` for `BlobNotFound`'s reason: this is an absence to report,
    not a failure to retry. Raised **before anything is touched**, so a typo in
    a `WC-nnnn` at three in the morning is a non-zero exit and not a partial
    cascade against whatever the typo happened to match.

    Deliberately not a scope refusal: this is not a caller asking whether a
    claim is in their book (`services/claims/detail.ClaimNotVisible` is that
    question), it is the system asking whether a row exists. See `purge_claim`
    on why the cascade reads unscoped.
    """


class UserNotFound(LookupError):
    """No `app_user` carries that id. `ClaimNotFound`'s twin, one subject over."""


class SystemActorRefused(RuntimeError):
    """The subject is the machine identity, and purging it would erase the log.

    `purge_user` redacts **every diff its subject wrote** (`actor_id = :user_id`,
    and `purge_user`'s docstring argues that breadth at length). Applied to the
    one row `services/financials/batch.py::system_context` resolves, that
    predicate reaches every payment-batch transition, every embedding refresh
    and every retention sweep this build has ever recorded — the entire
    machine-written half of the audit log, in one command, from one mistyped
    id.

    That is not a purge of a subject's PHI. The system actor is not a person,
    holds no diary notes, no meetings, no email log and no conversations
    (`tests/test_personas.py` pins that it can never hold a session either), so
    there is nothing about it a retention obligation could be discharged
    against. What the command would actually do is destroy the evidence that the
    scheduled jobs ran, which is the one thing AD-4's append-only posture exists
    to preserve.

    A `RuntimeError` rather than a `UserNotFound`: the row is there and the id
    resolved. This is a refusal to act on it, and telling that apart from "no
    such user" is what stops an operator retyping the id.
    """


class BlobStoreRequired(RuntimeError):
    """The subject has binaries and no store was injected to delete them from.

    Raised before the first delete rather than when the blob step is reached,
    which is the same discipline `RedactorUnavailable` keeps and for the same
    reason: a cascade that deleted a claim's `document` rows and *then* found it
    had nowhere to delete their bytes would have destroyed the only handles
    those bytes had. The keys live on the rows; once the rows are gone the
    objects are unreachable, unpurgeable and undiscoverable.

    A purge with no `blob_key` anywhere in scope proceeds without a store, which
    is what makes the seeded portfolio purgeable today: all 563 documents and
    all 293 photographs carry a null key because the prototype has no files
    behind them.
    """


@dataclass(frozen=True)
class PurgeRun:
    """What one purge did. Counts and store names — never a value.

    `PaymentBatchRun`'s arrangement and its rule: a run summary is an
    operational fact, so it carries how many rows moved and in which table, and
    nothing about what was in them. It is the object the management command
    prints as one JSON line, which is precisely why it must be safe to print.

    Three counters rather than one total, because they are three different kinds
    of loss and an operator reading a purge report needs them apart: relational
    rows deleted, objects removed from the blob store, and audit rows whose
    diffs were overwritten in place. The last of those is not a deletion at all
    (AC 3) and summing it into the others would say something false.

    `blobs_failed` is a fourth number and is not a kind of loss at all — it is
    the opposite, a count of objects that are **still there** after a purge said
    it had finished. `_delete_blobs` refuses to abort the cascade on a store
    error (the redaction that follows is the recoverable half and must still
    run), so the failure has to leave a trace somewhere the caller can see, and
    a count on the summary is that trace. It is deliberately absent from
    `is_empty`: a run that deleted nothing and failed to delete nothing is still
    an empty run, and the orphan itself is reported by `audit.blob_orphaned`
    whether or not this summary is logged.
    """

    rows_deleted_by_entity: Mapping[str, int]
    blobs_deleted: int
    rows_redacted: int
    blobs_failed: int = 0

    @property
    def rows_deleted(self) -> int:
        return sum(self.rows_deleted_by_entity.values())

    @property
    def is_empty(self) -> bool:
        """Nothing was deleted, removed or redacted — the second run's answer.

        All three counters, not just the rows: a re-run that deleted no rows but
        redacted one more diff did something, and a summary that called it empty
        would be the one line in this module that lies to an operator.
        """
        return self.rows_deleted == 0 and self.blobs_deleted == 0 and self.rows_redacted == 0

    def as_json(self) -> dict[str, Any]:
        """The shape `scripts/purge_claim.py` prints. Content-free by construction."""
        return {
            "rowsDeleted": self.rows_deleted,
            "rowsDeletedByEntity": dict(sorted(self.rows_deleted_by_entity.items())),
            "blobsDeleted": self.blobs_deleted,
            "blobsFailed": self.blobs_failed,
            "rowsRedacted": self.rows_redacted,
            "isEmpty": self.is_empty,
        }


#: `(thread id) -> deletes that thread's checkpoints`. The seam that keeps
#: `services/` free of `agents/` — see the module docstring.
#:
#: Positional rather than keyword, unlike `agents.threads.discard_thread`'s own
#: `thread_id`, so that the contract this package publishes says nothing about
#: how the function on the other side spells its parameter. The composition root
#: adapts; see `api/app.py`.
ThreadDeleter = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class _Child:
    """One store the cascade deletes from: its name, its model, its key column.

    A table rather than twenty near-identical blocks, for
    `services/financials/batch.py::_Target`'s reason: the statements differ only
    by a class and a column, and the first copy to drift would be the one that
    forgot a `WHERE`.

    `model` is carried beside `column` rather than derived from it. It *is*
    derivable — an `InstrumentedAttribute` knows its mapper — but a delete
    statement built by reaching through an attribute's private-ish plumbing is a
    statement no reader can check at a glance, and this is the one module in the
    build whose statements destroy data.
    """

    entity: str
    model: type[Base]
    column: Any


#: Every claim-keyed store, **in deletion order**. Children before parents.
#:
#: No foreign key in this schema declares `ondelete`, so this order is enforced
#: by the database rather than merely preferred by it: get it wrong and the
#: statement raises rather than cascading. Within that constraint the order runs
#: from the derived and regenerable towards the irreplaceable, so that a run
#: interrupted half-way has destroyed the least valuable rows first — an
#: `ai_insight` can be regenerated from a claim, and a claim cannot be
#: reconstructed from anything.
#:
#: `copilot_thread` sits between the derived stores and the case file because a
#: thread hangs off `claim` and its transcripts hang off the thread: the
#: checkpoints are discarded through the injected deleter immediately *before*
#: this row is deleted (`_purge_stores`), because the row is the only index of
#: the string the saver files them under.
CLAIM_CHILDREN: Final[tuple[_Child, ...]] = (
    _Child("ai_insight", AiInsight, AiInsight.claim_id),
    _Child("ai_insight_attempt", AiInsightAttempt, AiInsightAttempt.claim_id),
    _Child("claim_embedding", ClaimEmbedding, ClaimEmbedding.claim_id),
    _Child(THREAD_ENTITY, CopilotThread, CopilotThread.claim_id),
    _Child("payment_schedule_week", PaymentScheduleWeek, PaymentScheduleWeek.claim_id),
    _Child("bill", Bill, Bill.claim_id),
    _Child("expense", Expense, Expense.claim_id),
    _Child("document", Document, Document.claim_id),
    _Child("photo", Photo, Photo.claim_id),
    _Child("timeline_event", TimelineEvent, TimelineEvent.claim_id),
    _Child("additional_injury", AdditionalInjury, AdditionalInjury.claim_id),
    _Child("treatment_plan_step", TreatmentPlanStep, TreatmentPlanStep.claim_id),
    # The three user-keyed tables' claim-tagged rows. Their null-claim rows
    # belong to a user rather than to a claim and are `purge_user`'s.
    _Child("meeting", Meeting, Meeting.claim_id),
    _Child("diary_note", DiaryNote, DiaryNote.claim_id),
    _Child("email_log", EmailLog, EmailLog.claim_id),
    # The parent, last, because nothing above may be orphaned by it.
    _Child(CLAIM_ENTITY, Claim, Claim.id),
)

#: Every user-keyed store, in deletion order. None of them references another,
#: so the order is only a reading order — but it is written down for the same
#: reason the claim's is, so that a store added to the schema and not to this
#: tuple is a visible omission rather than an invisible one.
#:
#: `session` is in here and is **not** PHI: a session row is a token hash and
#: two timestamps (`data/models/session.py`), and `tests/test_purge_ownership.py`
#: exempts `data/repositories/identity.py`'s deletes of it for exactly that
#: reason. It is purged anyway because a live session is a live *reference* to
#: an account whose artifacts have just been destroyed, and leaving it would let
#: the next request rebuild a caller context for a user this command has just
#: been asked to remove from the record.
USER_CHILDREN: Final[tuple[_Child, ...]] = (
    _Child(THREAD_ENTITY, CopilotThread, CopilotThread.user_id),
    _Child("meeting", Meeting, Meeting.app_user_id),
    _Child("diary_note", DiaryNote, DiaryNote.app_user_id),
    _Child("email_log", EmailLog, EmailLog.app_user_id),
    _Child("session", Session, Session.user_id),
)

#: The two tables whose rows carry a `blob_key`, with the column that keys them
#: to a claim. Read before their rows are deleted — see the module docstring.
BLOB_COLUMNS: Final[tuple[tuple[Any, Any], ...]] = (
    (Document.blob_key, Document.claim_id),
    (Photo.blob_key, Photo.claim_id),
)


async def purge_claim(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    business_id: str,
    settings: Settings,
    blobs: BlobStore | None,
    delete_thread: ThreadDeleter,
) -> PurgeRun:
    """Delete every PHI-class store for one claim, and redact its audit diffs.

    AC 1 and AC 6 in one call: relational rows across sixteen tables, the
    claim's vector, its insight cache and generation cursor, its conversations'
    checkpoints and the rows that name them, its document and photo binaries
    through `BlobStore`, the `employee` row if no other claim holds it, and then
    the diffs of every `audit_event` that named it.

    **Unscoped, deliberately.** Every other read in this build resolves a claim
    under `employer_scope(ctx)`, and this one does not. `ctx` is here as the
    *actor* — the identity the `purge` and `redact` events are written under,
    resolved by `services/financials/batch.py::system_context` at the
    composition root — and not as an authority: a purge is a compliance
    obligation discharged against a named claim, and a cascade that silently
    purged nothing because the system actor's employer assignments had drifted
    would be the worst possible way to fail. Authority lives at the invocation
    surface instead, which is a management command with no HTTP route in front
    of it.

    **Three refusals, all before the first delete.** `ClaimNotFound` when the
    business id names neither a live claim nor a purged one; `RedactorUnavailable`
    when the audit half of the cascade could not run (see `_redactor.py` on why a
    half-purge is worse than no purge); `BlobStoreRequired` when the claim has
    binaries and no store was injected.

    Only the first of those three is cheap, and the ordering claim this
    docstring used to make was wrong about the other two: `ClaimNotFound` costs
    one or two `SELECT`s on the caller's own session, but `BlobStoreRequired` is
    raised *inside* `async with redactor_connection(settings)` and therefore
    costs a whole owner engine, a connection and a `SET ROLE` round trip before
    it can be reached. That is not an oversight to fix by reordering — the
    redactor is opened first on purpose, because a purge that deleted rows and
    then found it could not redact would be unrecoverable, and a preflight that
    is only sometimes performed is not a preflight.

    **A re-run over a claim whose row is already gone.** The relational phase
    commits before the blob and redaction steps run, so a failure in either
    leaves a subject whose `claim` row no longer exists and whose diffs may
    still hold PHI. Raising `ClaimNotFound` there would make the cascade's
    central promise — "run it again" — false exactly when it is needed. So an
    absent row is disambiguated against the audit skeleton: a prior `purge`
    event for this business id means the relational phase committed, and the
    command skips straight to the redaction with a zero-row `PurgeRun`. No such
    event means the id names nothing and `ClaimNotFound` still stands. The blob
    half cannot be resumed this way and the module docstring says why.

    **Two transactions and one uncommitted gap, stated rather than implied.**
    The relational cascade is one transaction on `db`; the redaction is a second
    on the redactor connection, because the two roles are two roles by design
    and no transaction can span them. The redaction commits *before* its
    `redact` events do, which is the safe direction: a crash between the two
    commits leaves the PHI gone and its `redact` events unwritten, where the
    other ordering would leave events asserting a redaction that did not happen
    and a re-run emitting them a second time. The residual — a redaction whose
    events were lost is not re-emitted, because the rows now carry the marker —
    is the price of the AD-4 split and is recorded in `deferred-work.md`.

    Returns an empty `PurgeRun` on a second call, writes no audit event and logs
    nothing.
    """
    at = datetime.now(UTC)
    claim = (
        await db.execute(
            sa.select(Claim.id, Claim.employee_id).where(Claim.claim_id == business_id)
        )
    ).one_or_none()
    if claim is None:
        return await _resume_claim_purge(db, ctx, business_id=business_id, settings=settings, at=at)
    claim_pk, employee_pk = int(claim.id), int(claim.employee_id)

    async with redactor_connection(settings) as redactor:
        blob_keys = await _claim_blob_keys(db, claim_pk)
        if blob_keys and blobs is None:
            raise BlobStoreRequired(
                f"{business_id} has {len(blob_keys)} stored object(s) and no BlobStore "
                "was injected; refusing to delete the rows that hold their keys"
            )

        thread_ids = await _claim_thread_ids(db, claim_pk)
        deleted = await _purge_stores(db, CLAIM_CHILDREN, claim_pk, thread_ids, delete_thread)
        deleted[EMPLOYEE_ENTITY] = await _delete_orphan_employee(db, employee_pk)

        await _record_store_purges(db, ctx, deleted, entity_id=business_id, at=at)
        await db.commit()

        blobs_deleted, blobs_failed = _delete_blobs(blobs, blob_keys)
        redacted = await _redact(
            db, ctx, redactor, predicate=_claim_audit_predicate(business_id), at=at
        )

    run = PurgeRun(
        rows_deleted_by_entity=deleted,
        blobs_deleted=blobs_deleted,
        rows_redacted=redacted,
        blobs_failed=blobs_failed,
    )
    _log_run("audit.claim_purged", run, claim_id=business_id)
    return run


async def _resume_claim_purge(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    business_id: str,
    settings: Settings,
    at: datetime,
) -> PurgeRun:
    """The `claim` row is gone. Finish the redaction, or refuse the id outright.

    Called only when `purge_claim`'s first `SELECT` found nothing, and its whole
    job is to tell two states apart that look identical from the `claim` table:
    a claim this cascade has already destroyed, and a business id that never
    named anything.

    The discriminator is the audit skeleton, which is exactly the artefact AC 3
    keeps for this kind of question. `_record_store_purges` writes one `purge`
    event per non-empty store in the *same transaction* as the deletes, and the
    `claim` store is never empty on a successful run — so a committed relational
    phase always leaves an `action = 'purge', entity = 'claim', entity_id =
    <business id>` row behind, and an uncommitted one leaves none (the events
    roll back with the deletes they describe).

    When the marker is there the redaction runs again. It can: the predicate is
    keyed on the business id and on diff contents, neither of which needs the
    `claim` row, and `_unredacted()` means a completed redaction re-runs to zero
    rows and emits nothing. The returned `PurgeRun` carries an empty
    `rows_deleted_by_entity` because this run deleted no rows — not because it
    swept an empty set, but because that phase already happened and is recorded
    under the first run's events.

    When the marker is absent this raises `ClaimNotFound`, which is the same
    answer a typo got before this function existed.
    """
    purged = await db.scalar(
        sa.select(_AUDIT.c.id)
        .where(
            _AUDIT.c.action == PURGE_ACTION,
            _AUDIT.c.entity == CLAIM_ENTITY,
            _AUDIT.c.entity_id == business_id,
        )
        .limit(1)
    )
    if purged is None:
        raise ClaimNotFound(business_id)

    async with redactor_connection(settings) as redactor:
        redacted = await _redact(
            db, ctx, redactor, predicate=_claim_audit_predicate(business_id), at=at
        )

    run = PurgeRun(rows_deleted_by_entity={}, blobs_deleted=0, rows_redacted=redacted)
    _log_run("audit.claim_purged", run, claim_id=business_id)
    return run


async def purge_user(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    user_id: int,
    settings: Settings,
    blobs: BlobStore | None,
    delete_thread: ThreadDeleter,
) -> PurgeRun:
    """Delete one user's artifacts and redact their audit diffs. **Not their account.**

    The story's Block If, restated as behaviour: `app_user` is never touched.
    `claim.handler_id` and `audit_event.actor_id` both point at that row, so
    deleting it would either orphan a hundred claims or take the audit skeleton
    with them — and the skeleton is the thing AC 3 exists to preserve. What a
    user purge removes is what the user *produced*: their diary notes, their
    meetings, their email log, their conversations and their sessions.

    **Both the claim-tagged and the null-claim rows** (`meeting.claim_id`,
    `diary_note.claim_id` and `email_log.claim_id` are all nullable): a note a
    handler wrote against no claim is still that handler's note, and a purge
    keyed on the claim would leave exactly the rows nothing else can reach.

    **Every diff the user wrote is redacted, not only those on the tables above,
    and the breadth is the decision.** The predicate is `actor_id = :user_id`,
    so a claim-field edit this handler made loses its `before`/`after` too. The
    narrower alternative — redact only rows whose `entity` is one of the purged
    tables — would leave the same free text in the same JSONB column under a
    different `entity`, which is a distinction PHI does not have. The skeleton
    survives either way, so "this handler changed this field on this claim on
    this day" remains provable; what stops being readable is what they changed
    it to, which is precisely what a purge is for.

    **The one subject this refuses is the system actor.** The breadth above is
    what makes it necessary: `actor_id = :user_id` applied to
    `services/financials/batch.py`'s machine identity would redact every
    payment-batch, embedding-refresh and retention diff in the log at once, and
    that row is not a person with artifacts to purge in the first place. See
    `SystemActorRefused`.

    `blobs` is accepted and unused: no user-keyed store carries a `blob_key`
    today. It is in the signature so that the two commands are one shape at the
    composition root, and so that the day a user-keyed table grows binaries the
    argument is already threaded. `delete_thread` is genuinely used — a user's
    conversations are checkpointed exactly like a claim's, and AD-6 keys a
    thread by `(scope, user_id, seq)` so either component finds them.
    """
    del blobs

    at = datetime.now(UTC)
    subject = (
        await db.execute(sa.select(AppUser.id, AppUser.role).where(AppUser.id == user_id))
    ).one_or_none()
    if subject is None:
        raise UserNotFound(str(user_id))
    if subject.role == UserRole.system:
        raise SystemActorRefused(
            f"app_user {user_id} is the {UserRole.system.value} actor, whose audit diffs are "
            "the record that the scheduled jobs ran. Purging it would redact every "
            "payment-batch, embedding-refresh and retention row in the log, and it owns no "
            "diary notes, meetings, emails, conversations or sessions to purge in the first "
            "place. Refusing."
        )

    async with redactor_connection(settings) as redactor:
        thread_ids = await _user_thread_ids(db, user_id)
        deleted = await _purge_stores(db, USER_CHILDREN, user_id, thread_ids, delete_thread)

        await _record_store_purges(db, ctx, deleted, entity_id=str(user_id), at=at)
        await db.commit()

        redacted = await _redact(db, ctx, redactor, predicate=_AUDIT.c.actor_id == user_id, at=at)

    run = PurgeRun(rows_deleted_by_entity=deleted, blobs_deleted=0, rows_redacted=redacted)
    _log_run("audit.user_purged", run, user_id=user_id)
    return run


# --- the relational half -------------------------------------------------


async def _purge_stores(
    db: AsyncSession,
    children: Sequence[_Child],
    subject_pk: int,
    thread_ids: Sequence[str],
    delete_thread: ThreadDeleter,
) -> dict[str, int]:
    """Walk one ordered store list, sweeping checkpoints where they belong.

    The checkpoint step is interleaved rather than run first or last, and the
    position is the whole of it: immediately *before* `copilot_thread`, because
    that row is the only index of the thread id the saver files transcripts
    under. Delete the rows first and the transcripts become PHI nothing in the
    system can name; sweep them at the end of the run and a failure anywhere in
    between has already destroyed the index.

    **A failing thread deletion propagates, and what that costs is worth stating
    accurately.** It is not true that nothing has been deleted yet: `ai_insight`,
    `ai_insight_attempt` and `claim_embedding` come before `copilot_thread` in
    `CLAIM_CHILDREN`, so by the time the checkpoint step raises, three stores'
    statements have already run on `db`. What saves the run is that they have
    not been *committed* — `purge_claim` commits once, after this whole loop —
    so the exception unwinds through a transaction that is discarded and the
    relational half of the subject is exactly as it was.

    The checkpoints are the half that does not roll back. The saver's pool is
    opened `autocommit=True` (`api/app.py` and `scripts/purge_claim.py` both
    build it that way, because `AsyncPostgresSaver` runs its own pipeline), so a
    transcript discarded before the failure stays discarded. That is the safe
    direction: the `copilot_thread` rows that index those ids survive the
    rollback, so a re-run finds them again and hands the same ids to the
    deleter, which is idempotent by way of being a `DELETE` over a thread id
    that now matches nothing.
    """
    deleted: dict[str, int] = {}
    for child in children:
        if child.entity == THREAD_ENTITY:
            deleted[CHECKPOINT_STORE] = await _discard_threads(thread_ids, delete_thread)
        deleted[child.entity] = await _delete_where(db, child, subject_pk)
    return deleted


async def _delete_where(db: AsyncSession, child: _Child, subject_pk: int) -> int:
    """`DELETE FROM <child> WHERE <column> = :pk`, and how many rows went.

    `synchronize_session=False` because the ORM has nothing to synchronise: this
    command loads no entities and re-reads nothing afterwards, so the identity
    map is empty and the default's evaluate-then-fetch fallback would be a
    second query per table for no benefit (`data/repositories/claims.py`'s note
    on the same option).

    The `cast` to `CursorResult` is the tree's convention for `rowcount`
    (`data/repositories/embeddings.py` says why): `AsyncSession.execute` is
    typed as returning the narrower `Result`, which has no such attribute, while
    a DML statement always produces a `CursorResult`.
    """
    result = await db.execute(
        sa.delete(child.model)
        .where(child.column == subject_pk)
        .execution_options(synchronize_session=False)
    )
    return int(cast(CursorResult[Any], result).rowcount)


async def _delete_orphan_employee(db: AsyncSession, employee_pk: int) -> int:
    """Delete the injured worker's row — **only if no other claim names it**.

    An `employee` row is one person, and one person can have more than one
    claim: the seeded portfolio happens to be one-to-one, and the schema is not.
    So this runs after the `claim` row is gone and asks the question the schema
    actually answers — is anything still pointing here — rather than assuming
    the answer the dev seed happens to give.

    Returns 0 when the worker survives, which is a real outcome and not a
    failure: their remaining claim is still a claim, and a purge that took the
    worker's identity out from under it would break the foreign key that other
    claim depends on.
    """
    remaining = await db.scalar(
        sa.select(sa.func.count()).select_from(Claim).where(Claim.employee_id == employee_pk)
    )
    if remaining:
        return 0
    result = await db.execute(
        sa.delete(Employee)
        .where(Employee.id == employee_pk)
        .execution_options(synchronize_session=False)
    )
    return int(cast(CursorResult[Any], result).rowcount)


async def _claim_blob_keys(db: AsyncSession, claim_pk: int) -> tuple[str, ...]:
    """Every non-null `blob_key` on a claim's documents and photographs.

    Read **before** anything is deleted, because the key is the only handle the
    object has: a `document` row is where a `blob_key` lives, and once the row
    is gone the bytes are unreachable by any query this system can write. The
    preflight in `purge_claim` uses the same result to refuse a purge that has
    binaries and nowhere to delete them from.
    """
    keys: list[str] = []
    for key_column, claim_column in BLOB_COLUMNS:
        rows = await db.scalars(
            sa.select(key_column).where(claim_column == claim_pk, key_column.is_not(None))
        )
        keys.extend(str(key) for key in rows)
    return tuple(keys)


def _delete_blobs(blobs: BlobStore | None, keys: Sequence[str]) -> tuple[int, int]:
    """Remove every collected object. **After the commit, guarded, and idempotent.**

    Returns `(removed, failed)`.

    The ordering is the one irreversible decision in this module. A blob deleted
    inside the transaction would be gone even if the transaction rolled back,
    and there is no undo; an object still present after a committed delete is
    merely orphaned. So the loop runs immediately after the commit and does
    nothing else, which makes the window in which a crash can orphan an object
    as short as this design allows.

    `BlobStore.delete` is documented idempotent precisely for this cascade, so a
    key whose object is already gone is a no-op and is counted as removed. What
    it is *not* documented to be is infallible: `VolumeBlobStore.delete` raises
    `BlobStoreError` on any `OSError`, and an object store behind the same
    protocol will raise it on a network fault. So the count is **objects this
    call actually got through the store without an error**, not "keys this purge
    was responsible for" — the two differ by exactly the number an operator
    needs to go and look for, and reporting the second as though it were the
    first would tell them there was nothing to look for.

    **A store error may not abort the run.** This is the one loop in the cascade
    that runs after the relational commit and before the redaction, so an
    unguarded raise here would skip the redaction entirely and leave the
    subject's PHI in `audit_event` — trading an orphaned object, which is
    recoverable by re-running the command or by a sweep of the store, for
    unredacted PHI, which is not recoverable at all once the rows that named it
    are gone. Each key is therefore guarded and the failures are counted.

    The orphan line carries a **count and nothing else** (AD-11): a blob key is
    derived from a claim's own identifiers, so logging the key would put the
    subject of a purge into the log line that reports the purge.

    `blobs is None` here means the preflight found no keys at all, so there is
    nothing to remove and nothing to report.
    """
    if blobs is None:
        return 0, 0
    removed = 0
    failed = 0
    for key in keys:
        try:
            blobs.delete(key)
        except BlobStoreError:
            failed += 1
            continue
        removed += 1
    if failed:
        log.warning("audit.blob_orphaned", blobs_failed=failed, blobs_deleted=removed)
    return removed, failed


async def _claim_thread_ids(db: AsyncSession, claim_pk: int) -> tuple[str, ...]:
    """The saver's keys for a claim's conversations, from this build's own table.

    `copilot_thread` exists so that thread identity is a scoped query against a
    table this project owns rather than a read of the saver's (migration 0043's
    argument), and this is the read that argument was written for.
    """
    rows = await db.scalars(
        sa.select(CopilotThread.thread_id).where(CopilotThread.claim_id == claim_pk)
    )
    return tuple(str(row) for row in rows)


async def _user_thread_ids(db: AsyncSession, user_id: int) -> tuple[str, ...]:
    """`_claim_thread_ids`, keyed on the other component of AD-6's thread key."""
    rows = await db.scalars(
        sa.select(CopilotThread.thread_id).where(CopilotThread.user_id == user_id)
    )
    return tuple(str(row) for row in rows)


async def _discard_threads(thread_ids: Sequence[str], delete_thread: ThreadDeleter) -> int:
    """Hand every thread id to the injected deleter. Returns threads discarded.

    **Not rows**, and the module docstring says why: the saver's
    `adelete_thread` returns nothing, and this package may not count rows in
    tables AD-3's exception says only the saver reads.

    A failing deletion propagates rather than being swallowed, and
    `_purge_stores` states exactly what that costs and why it is survivable:
    the relational statements that ran before this one are uncommitted and are
    discarded with the transaction, and the `copilot_thread` rows that index
    these ids survive, so a re-run finds the same threads again.

    Deliberately unlike `retention.py::purge_expired_checkpoints`, which guards
    each thread. That one is a daily sweep over everybody's conversations, where
    one permanently-failing thread would block every other for ever; this is one
    operator purging one named subject, who should be told the purge did not
    complete rather than handed a summary that quietly omits a transcript.
    """
    for thread_id in thread_ids:
        await delete_thread(thread_id)
    return len(thread_ids)


async def _record_store_purges(
    db: AsyncSession,
    ctx: CallerContext,
    deleted: Mapping[str, int],
    *,
    entity_id: str,
    at: datetime,
) -> None:
    """One content-free `purge` event per store that actually lost something.

    **Per store rather than per run**, because the question an auditor asks is
    "was this claim's document set destroyed, and when" rather than "how big was
    Tuesday's purge" — `services/financials/batch.py` makes the identical choice
    one story over and states the identical reason.

    **Per non-empty store rather than per store**, so a re-run of a completed
    purge writes nothing at all. Twenty rows saying "deleted nothing from twenty
    tables" on every re-run would bury the one run that did something, in the
    very table Epic 8's retention job reads.

    `before` and `after` are `None` rather than `{}`: a purge changed nothing
    from one value to another, it removed rows, and `record_copilot_approval`
    argues the shape at length. The **count** is deliberately not in `after`
    either — it would be a fact this cascade could later erase from itself,
    since `after` is exactly what the redactor overwrites, and a purge event
    that survives redaction saying only "this store was purged for this subject"
    is the honest skeleton.

    Every event carries the *subject* as `entity_id` — the claim's business id,
    or the user's id — rather than each store's own key, so that one filter
    returns the whole purge.
    """
    for entity, count in deleted.items():
        if not count:
            continue
        await audit.record(
            db,
            ctx,
            action=PURGE_ACTION,
            entity=entity,
            entity_id=entity_id,
            before=None,
            after=None,
            at=at,
        )


# --- the audit half ------------------------------------------------------


def _diff_key(column: Any, key: str) -> Any:
    """`column ->> 'key'` as a typed expression.

    `jsonb_extract_path_text` rather than the `->>` operator, and it is a typing
    decision rather than a semantic one — the two are the same function. A bare
    `.op("->>")` produces an expression SQLAlchemy types as `ColumnOperators`,
    which `sa.and_` will not accept under strict mypy without a cast at every
    call site; a `func` call is typed as an ordinary SQL function and composes.
    """
    return sa.func.jsonb_extract_path_text(column, key)


def _claim_audit_predicate(business_id: str) -> Any:
    """Every `audit_event` row that names one claim. Three mechanisms, all required.

    The rows are reachable by three disjoint routes and a predicate missing any
    one of them leaves PHI in the log:

    1. **`entity_id = '<wc>'`, whatever the `entity` is.** Any row whose
       `entity_id` is this claim's business id is a row about this claim, and
       the entity beside it says only which table the change landed in.

       This route was originally written `entity = 'claim' AND entity_id =
       '<wc>'`, and the narrower form was wrong — not marginally, but for a
       whole class of rows. Four commands record against a *child* table while
       naming the **claim's** business id in `entity_id`, each with an explicit
       comment saying it does so precisely so the audit log answers "what
       happened to WC-20017" without a join:
       `services/financials/batch.py`'s payment transitions (`entity` is
       `bill` / `expense` / `payment_schedule_week`),
       `services/financials/approval.py`'s approvals (the same three),
       `services/financials/materialize.py`'s schedule re-materialisation
       (`payment_schedule_week`), and `services/claims/assessment.py`'s document
       review flags (`document`). None of their diffs carries a `claim_id` key,
       so route 2 does not see them either — meaning payment plans, line-item
       statuses, week-by-week amounts and document review state all survived a
       purge of the claim they name. Dropping the `entity` conjunct closes it,
       and closes it for the next such command as well, which is why the fix is
       to widen the route rather than to enumerate four more entities.
    2. **`before ->> 'claim_id'` / `after ->> 'claim_id'`** covers the child
       entities whose `entity_id` is the *child's* surrogate key.
       `notes.py::_diff`, `meetings.py::_diff`, `emails.py::_diff` and
       `injuries.py::_diff` each put the claim's business id in the diff and
       each names this cascade as the reason — this is the code that collects on
       that promise.
    3. **`entity = 'ai_insight' AND entity_id LIKE '<wc>:%'`** covers the
       generation rows, whose `entity_id` is `<claim>:<kind>`
       (`services/rag/insights.py`) and which are therefore matched by neither
       of the first two.

    Belt and braces on purpose: the three overlap, and overlapping is cheap
    while a gap is a diff nobody finds again. The *oracle* is not this
    predicate — it is `tests/test_purge_cascade.py`'s sweep, which reads every
    remaining `audit_event` diff and fails if either the purged worker's name or
    the purged claim's business id appears in any of them, so a route this
    function does not know about fails the suite rather than passing it. The
    business-id half of that scan was added because the four commands named
    under route 1 never write a worker's name into a diff at all: the name-only
    version of the oracle agreed with the narrow predicate exactly as often as
    it was blind to the same rows.

    What this deliberately does **not** reach is `export.*` rows. An export
    spans a set of claims and `record_export` names none of them, by an argument
    that function makes at length and that Story 7.5 recorded against this
    story. Accepted: those rows' `after` is content-free by construction (query
    terms, not claim values), so there is nothing in them to redact, and they
    age out under `retention.py`'s audit sweep. See `deferred-work.md`.
    """
    return sa.or_(
        _AUDIT.c.entity_id == business_id,
        sa.and_(
            _AUDIT.c.entity == _INSIGHT_ENTITY,
            _AUDIT.c.entity_id.startswith(f"{business_id}:", autoescape=True),
        ),
        _diff_key(_AUDIT.c.before, _CLAIM_ID_KEY) == business_id,
        _diff_key(_AUDIT.c.after, _CLAIM_ID_KEY) == business_id,
    )


def _has_content(column: Any) -> Any:
    """A diff column that actually holds something. **Two tests, not one.**

    `column IS NOT NULL` is the obvious check and it is wrong here, for a reason
    that is invisible from Python and cost this cascade a real defect. A
    `JSONB` column assigned `None` through SQLAlchemy does **not** store SQL
    NULL: `postgresql.JSONB` defaults to `none_as_null=False`, so `record(...,
    before=None, after=None)` writes the JSON scalar `null` into both columns.
    The row is content-free in every sense that matters — and `IS NOT NULL` is
    `true` of it.

    What makes it a trap rather than a curiosity is that the driver reads a JSON
    `null` back as Python `None`, exactly as it reads SQL NULL. So the two are
    indistinguishable from every test written in Python, from every assertion in
    `tests/`, and from the `record_copilot_approval` docstring that says its
    diffs are "`None` rather than empty objects". The first version of this
    cascade stamped the redaction marker over every content-free
    `copilot_approval` and `export` row and emitted a `redact` event for each —
    asserting, in the audit log, a redaction that had not happened.

    `jsonb_typeof(column) <> 'null'` is the half that sees it. Both halves are
    kept because a column *can* still be SQL NULL — nothing forbids it, and
    `jsonb_typeof(NULL)` is NULL rather than false, so the comparison alone
    would silently drop those rows out of every predicate it appears in.
    """
    return sa.and_(column.is_not(None), sa.func.jsonb_typeof(column) != "null")


def _unredacted() -> Any:
    """A row with at least one diff column that still holds content.

    The idempotence test, and the reason `REDACTION_MARKER` is a key rather than
    a whole document: a row already carrying the marker in every content-bearing
    column has nothing left to overwrite, so it is not selected, is not
    rewritten, and emits no second `redact` event.

    A row whose `before` and `after` are **both empty** is likewise not
    selected, and that is the matrix's content-free case rather than an
    optimisation. `copilot_approval.*` and `export.*` rows carry no diff at all;
    writing a marker into a column that never held content would assert a
    redaction that did not happen, and would put a JSON object where the schema
    means "there was nothing to record". See `_has_content` on why "empty" is
    not the same question as "NULL".
    """
    return sa.or_(
        sa.and_(
            _has_content(_AUDIT.c.before),
            _diff_key(_AUDIT.c.before, REDACTION_MARKER).is_(None),
        ),
        sa.and_(
            _has_content(_AUDIT.c.after),
            _diff_key(_AUDIT.c.after, REDACTION_MARKER).is_(None),
        ),
    )


async def _redact(
    db: AsyncSession,
    ctx: CallerContext,
    redactor: AsyncConnection,
    *,
    predicate: Any,
    at: datetime,
) -> int:
    """Overwrite the subject's diffs in place, and record one event per row.

    AC 3 and AC 4 together. The skeleton is untouched — no statement here names
    `actor_id`, `actor_role`, `action`, `entity`, `entity_id` or `at`, and the
    grant would refuse them if one did: `audit_redactor` holds `UPDATE` on
    exactly two columns.

    **Two statements rather than one `CASE`**, because the rule is "overwrite
    only what held content" and two `WHERE _has_content(…)` clauses say that
    without a conditional expression whose NULL typing has to be argued about.
    They are also exactly the two column privileges the role was granted, one
    statement each, which is a pleasing way for the code to read like the grant.

    **The ids are collected first and the events are written against them**, so
    the count reported and the events emitted describe the same set even though
    they are produced on two connections. A `RETURNING` on the update would be
    tidier and would not survive the split: the events cannot be written by the
    redactor (it has no INSERT, by design) and the app role cannot see the
    redactor's uncommitted rows.

    **The `IN (…)` is issued in batches, and the bound is the wire protocol's
    rather than a taste for small statements.** SQLAlchemy expands
    `id.in_(ids)` into one bind parameter per id, and PostgreSQL's extended
    query protocol caps a single statement at 65535 of them. A handler with two
    years of edits behind them, or a claim on a long-running litigated file, is
    not a hypothetical subject with that many audit rows — and the failure would
    land *after* the relational commit, which is the worst place in this cascade
    to discover a limit. `_ID_BATCH` is an order of magnitude inside the cap so
    that the statement stays well clear of it whatever else a future `WHERE`
    binds.

    See `purge_claim` on the commit ordering and on the residual it leaves.
    """
    ids = list(
        (await redactor.scalars(sa.select(_AUDIT.c.id).where(predicate, _unredacted()))).all()
    )
    if not ids:
        return 0

    marker = sa.bindparam(
        "redaction_marker",
        value={REDACTION_MARKER: True, REDACTED_AT_KEY: at.isoformat()},
        type_=JSONB,
    )
    for column in (_AUDIT.c.before, _AUDIT.c.after):
        for batch in in_batches(ids):
            await redactor.execute(
                sa.update(_AUDIT)
                .where(_AUDIT.c.id.in_(batch), _has_content(column))
                .values({column.name: marker})
            )
    await redactor.commit()

    for row_id in ids:
        await audit.record(
            db,
            ctx,
            action=REDACT_ACTION,
            entity=REDACTED_ENTITY,
            entity_id=str(row_id),
            before=None,
            after=None,
            at=at,
        )
    await db.commit()
    return len(ids)


#: How many ids may go into one expanded `IN (…)` list.
#:
#: PostgreSQL's extended query protocol carries a 16-bit parameter count, so a
#: statement may bind at most 65535 values and SQLAlchemy expands `col.in_(ids)`
#: into one bind per id. Five thousand is an order of magnitude inside that,
#: which leaves room for whatever else a statement binds and keeps the number a
#: round one rather than a limit expressed as `65535 - n`.
_ID_BATCH: Final[int] = 5000


def in_batches(values: Sequence[Any], size: int = _ID_BATCH) -> Iterable[Sequence[Any]]:
    """Slice a list of bind values into statement-sized chunks.

    Shared by this module's redaction and by `retention.py`'s checkpoint sweep,
    which are the two places in the package that build an `IN (…)` over a set
    whose size is a property of the *data* rather than of the code — a subject's
    audit rows, a retention window's expired threads. Both were written as one
    statement and both would have raised past ~65k rows, after their own
    commits, on the day somebody's history got long enough.
    """
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _log_run(event: str, run: PurgeRun, **subject: Any) -> None:
    """One operational line per non-empty run — counts and ids, never a value.

    **An empty run logs nothing at all**, which is the same discipline
    `run_payment_batch` keeps and matters more here: a purge is re-run by
    design, and a line per re-run saying "deleted nothing" would make the log of
    a system under a retention policy unreadable exactly when somebody is
    reading it to establish what was destroyed.

    The subject is a claim's business id or a user's surrogate id. Both are
    identifiers rather than content — AD-11 permits ids and event names — and
    the business id is the one string a compliance reader needs in order to tie
    this line to the audit rows beside it.
    """
    if run.is_empty:
        return
    log.info(
        event,
        rows_deleted=run.rows_deleted,
        by_entity={entity: count for entity, count in run.rows_deleted_by_entity.items() if count},
        blobs_deleted=run.blobs_deleted,
        blobs_failed=run.blobs_failed,
        rows_redacted=run.rows_redacted,
        **subject,
    )


def claim_store_names() -> Iterable[str]:
    """Every store `purge_claim` sweeps, for the tests and for nothing else.

    Read off the tuples rather than restated, so a store added to the cascade is
    covered by the sweep test without anybody remembering to widen a list —
    `services/financials/batch.py::entity_names` sets the precedent and its
    docstring gives the reason: a table added above and forgotten here would be
    a silent gap in the one test that exists to find gaps.
    """
    return (CHECKPOINT_STORE, *(child.entity for child in CLAIM_CHILDREN), EMPLOYEE_ENTITY)


__all__ = [
    "BLOB_COLUMNS",
    "CHECKPOINT_STORE",
    "CLAIM_CHILDREN",
    "CLAIM_ENTITY",
    "EMPLOYEE_ENTITY",
    "PURGE_ACTION",
    "REDACTED_AT_KEY",
    "REDACTED_ENTITY",
    "REDACTION_MARKER",
    "REDACT_ACTION",
    "THREAD_ENTITY",
    "USER_CHILDREN",
    "BlobStoreRequired",
    "ClaimNotFound",
    "PurgeRun",
    "RedactorUnavailable",
    "SystemActorRefused",
    "ThreadDeleter",
    "UserNotFound",
    "claim_store_names",
    "in_batches",
    "purge_claim",
    "purge_user",
]
