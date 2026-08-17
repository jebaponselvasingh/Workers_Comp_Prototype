"""Diary notes — write one, list the caller's (Story 4.2, FR-DIARY-1, AD-4).

The second slice of the **diary aggregate**, which AD-12 puts under
`services/claims`: this module is the only writer of `diary_note`, and it sits
beside `meetings.py` rather than reaching into it.

The prototype keeps notes in a browser-lifetime object (`diaryNotes`, line 919)
keyed by handler name and erased by a reload; here saving one is an AD-4
command with the ladder `meetings.py` established — role, then the claim tag's
scope, then the request's validity, then the write, then the audit event in the
**same transaction**, then the commit.

## What is deliberately absent

**No edit and no delete, therefore no `version`, no compare-and-swap and no
409.** The design contract has no control for either, and an append-only row is
exempt from CAS by the write-concurrency convention: nothing here is ever
read-modify-written, so there is no concurrent writer for a version to
arbitrate. `complete_meeting` keeps its own CAS and its own 409 — the
today's-meetings summary calls it unchanged — and that asymmetry between two
tables in one aggregate is the convention working rather than an inconsistency.

**No SLA recalculation.** The prototype recomputes its SLA strip when a note is
saved (`saveDiary` → `recalcSLA()`, line 2082). That trigger is obsolete:
`services/worklist` has computed the strip for every role since Story 1.5, and
AD-2 exists to stop a second computation path appearing. Saving a note writes
`diary_note` and `audit_event`, and nothing else (FR-SLA-1).

**No timeline event.** A note is a handler's own working record, not a mutation
of the claim, and the prototype does not surface notes on the case timeline.
AD-12 scopes `timeline_event` to claim-mutating commands, so
`services/claims/timeline.py::append` is not imported here — the same ruling
`meetings.py` makes for the same reason.

## Two scopes, one predicate

Every function takes `CallerContext` and applies it in the repository only
(AD-7). `diary_note_scope` is author **and** employer scope: a note is visible
to the handler who wrote it and to nobody else, and its claim tag additionally
has to survive that handler's current employer scope. Out of scope and absent
are the same 404.

## AD-11

`note_text` is PHI-class and is the most sensitive free text in the console — a
handler's unfiltered read of a worker's file. The audit diff carries it because
the row *is* the change and a log that recorded less could not say what was
written (`injuries.py::_diff`'s argument); the structlog line `services.audit`
emits carries ids and field *names* only, and nothing in this module logs a
value. Nothing here echoes the text back in a refusal message either.
"""

import base64
import binascii
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models.enums import UserRole
from data.repositories import claims as claim_repo
from services import audit
from services.claims.edit import EditNotPermitted, InvalidPatch
from services.derivations import utc_today

CREATE_ACTION: Final[str] = "create_diary_note"
ENTITY: Final[str] = "diary_note"

#: The longest a note may be. Enforced on the wire (`api/routers/diary.py`
#: declares it on the request model) *and* here, so a caller that is not the
#: SPA — an Epic 6 agent tool, whose arguments are untrusted content (AD-16) —
#: meets the same bound. Twice the meeting agenda's cap would be a different
#: number for no reason; it is the same one, because both are "a paragraph or
#: two of a handler's prose".
MAX_NOTE_LENGTH: Final[int] = 2000

#: The field name as the refusal messages spell it, so a 422 names the control
#: the handler is looking at rather than a column.
NOTE_TEXT_FIELD: Final[str] = "noteText"

#: The three control characters a note may carry: newline, carriage return and
#: tab. Named rather than spelled inline in the comprehension because the set is
#: the *rule* — everything else below `0x20`, plus DEL, is refused — and a
#: reader checking "why is a tab allowed?" should find the answer beside the
#: constant rather than inside a generator expression. See
#: `normalise_note_text`.
WHITESPACE_KEPT: Final[frozenset[str]] = frozenset({"\n", "\r", "\t"})

#: Page-size bounds, declared once and enforced twice — `meetings.py`'s rule
#: and its reason: FastAPI refuses a `limit` outside them before this module is
#: reached, and `decode_cursor` refuses one smuggled inside a cursor.
MIN_PAGE_LIMIT: Final[int] = 1
MAX_PAGE_LIMIT: Final[int] = 200
DEFAULT_PAGE_LIMIT: Final[int] = 50

#: How stale a cursor's recorded day may be before it stops describing a list
#: worth continuing — `meetings.py`'s bound, for its reason.
MAX_CURSOR_AGE: Final[timedelta] = timedelta(days=7)


class DiaryNoteClaimNotVisible(Exception):
    """The claim a new note tags is not in the caller's book — a 404.

    `MeetingClaimNotVisible`'s shape and its ruling: the same "no such thing,
    for you" answer an absent claim gets, so a caller comparing two refusals
    learns nothing about which claims exist.
    """

    def __init__(self, claim_business_id: str | None) -> None:
        super().__init__(f"no claim {claim_business_id} in your caseload")
        self.claim_business_id = claim_business_id


class DiaryNoteNotVisible(Exception):
    """No such note in the caller's diary — a 404.

    Reachable only from the post-commit re-read: the note was written and
    audited, and then the read back through the scoped query could not see it,
    which takes a scope narrowing landing between the two statements. Rare, and
    it must be *answered* rather than escaping as a 500 — the defect Story
    4.1's review found on its own POST route.
    """

    def __init__(self, note_id: int) -> None:
        super().__init__(f"no note {note_id} in your diary")
        self.note_id = note_id


class InvalidCursor(ValueError):
    """A cursor that does not describe a position in the caller's diary."""


@dataclass(frozen=True)
class DiaryNoteView:
    """One note as every surface reads it — the row, and its claim.

    `claim_business_id` and `worker_name` are on the view rather than looked up
    by the card for `MeetingView`'s reason: the tag renders `📎 WC-nnnn`, and a
    browser that assembled that from a second request would be showing a claim
    reference the list read did not scope.

    **There is no `status` and no derived field of any kind**, unlike
    `MeetingView`. A note has no lifecycle: it is written, and that is the whole
    of it. `noted_at` is a stored instant, and the `{date} · {time}` header the
    card draws is formatting rather than a rule.
    """

    id: int
    claim_business_id: str | None
    worker_name: str | None
    note_text: str
    noted_at: datetime


@dataclass(frozen=True)
class DiaryNotePage:
    """One page of the caller's diary, in the `{items, nextCursor, total}` shape.

    `total` is the size of the whole scoped list rather than of `items`, the
    Lists convention's rule: it is what a count beside the list shows, and a
    number that shrank when the page did would misdescribe the diary.
    """

    items: tuple[DiaryNoteView, ...]
    next_cursor: str | None
    total: int


@dataclass(frozen=True)
class Cursor:
    """Where a page ended, and the page size that cut it.

    **Keyset, not offset**, which is why the position is a `(noted_at, id)`
    pair rather than a row count: this list is newest-first and a handler
    writing a note mid-scroll would shift every offset after it — which is not
    the exotic case here, it is the *only* thing this surface does.

    The pair is the sort key itself, so the row it names is unambiguous. `id`
    is the second member because two notes can share a `noted_at`: the command
    stamps the instant, and two saves inside one clock tick tie.

    `limit` is *reused* when a request omits one, `queue.py`'s division: it
    describes the window rather than the ordering. `issued_on` is *validated*
    and never used to select anything — it exists so a cursor found in a
    bookmark a year later is refused with a sentence instead of silently
    re-paging a list that has moved on.
    """

    last_noted_at: datetime
    last_id: int
    limit: int
    issued_on: date


def encode_cursor(cursor: Cursor) -> str:
    """Base64url of a compact JSON object, unpadded — `meetings.py`'s encoding.

    Opaque by intent rather than by encryption: it carries no note text and
    nothing a caller could use to widen their scope (scope is never in the
    request — AD-7). What the encoding buys is that clients treat it as a token
    to hand back rather than a key to increment.
    """
    payload = json.dumps(
        {
            "n": cursor.last_noted_at.isoformat(),
            "i": cursor.last_id,
            "l": cursor.limit,
            "s": cursor.issued_on.isoformat(),
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(raw: str, as_of: date | None = None) -> Cursor:
    """Parse a cursor, or refuse it. Never a silent fallback to page one.

    Answering page one for an undecodable cursor turns a client bug into an
    infinite "Show more" that re-appends the same notes for ever — the failure
    `queue.py` names, and every bound below is its answer. `ArithmeticError`
    sits beside `ValueError` in the except tuple because `json` accepts the
    literal `Infinity` and `int(float("inf"))` raises `OverflowError`, which is
    not a `ValueError`.

    **A naive `noted_at` is refused.** The column is `timestamptz` and every
    value this service issues is UTC-aware; a cursor carrying a naive datetime
    would raise `TypeError` deep inside the row-value comparison instead of
    answering 400 at the boundary.
    """
    try:
        padded = raw + "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        cursor = Cursor(
            last_noted_at=datetime.fromisoformat(data["n"]),
            last_id=int(data["i"]),
            limit=int(data["l"]),
            issued_on=date.fromisoformat(data["s"]),
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        ArithmeticError,
        binascii.Error,
        UnicodeDecodeError,
    ) as exc:
        raise InvalidCursor("The pagination cursor is not readable.") from exc
    if cursor.last_noted_at.tzinfo is None:
        raise InvalidCursor("The pagination cursor names a position that cannot exist.")
    if cursor.last_id < 1:
        raise InvalidCursor("The pagination cursor names a position that cannot exist.")
    if not MIN_PAGE_LIMIT <= cursor.limit <= MAX_PAGE_LIMIT:
        raise InvalidCursor(
            f"The pagination cursor names a page size of {cursor.limit}; "
            f"it must be between {MIN_PAGE_LIMIT} and {MAX_PAGE_LIMIT}."
        )
    today = as_of or utc_today()
    if cursor.issued_on > today:
        raise InvalidCursor(
            f"The pagination cursor is dated {cursor.issued_on}, which is in the future; "
            "reload the list from the first page."
        )
    if today - cursor.issued_on > MAX_CURSOR_AGE:
        raise InvalidCursor(
            f"The pagination cursor is dated {cursor.issued_on} and has expired; "
            "reload the list from the first page."
        )
    return cursor


def normalise_note_text(raw: object) -> str:
    """A trimmed, capped, control-character-free note — or `InvalidPatch`.

    `edit.require_text`'s rules, with the requirement kept: **a note is the
    whole of what this command writes**, so an empty one is not a note somebody
    has not finished, it is a save nobody meant. The prototype silently ignores
    an empty input (`if(!t) return`, line 1757); here it is refused with a
    sentence rendered inline at the input, which is what AC 3 asks for and what
    NFR-3 means by never a native dialog.

    Whitespace-only is the same refusal as empty, because the two are the same
    thing to a reader: a card carrying three spaces is a card with nothing on
    it.

    The C0 range and DEL go with it for `require_text`'s reason: PostgreSQL
    `text` cannot hold a NUL and asyncpg raises rather than truncating, which
    reached the request as a 500 rather than a 422.

    **`\\n`, `\\r` and `\\t` are exempt, and the exemption is the fix to a
    refusal that was both wrong and dishonest.** A diary note is prose: a
    handler presses Enter, pastes a Windows-authored paragraph (CRLF) or pastes
    an indented list out of a spreadsheet (tabs). PostgreSQL `text` stores all
    three without complaint — NUL is the constraint the paragraph above cites,
    and it is the only one — so refusing a carriage return with "contains
    characters that cannot be stored" told the handler something untrue about
    their own words and gave them nothing to correct. The rest of C0 and DEL
    are still refused, because none of them is anything a person typed.

    Messages name the field and the rule and **never echo the value** (AD-11):
    this is the one field in the console whose content is the most sensitive
    thing a refusal could quote back.
    """
    if not isinstance(raw, str):
        raise InvalidPatch(f"{NOTE_TEXT_FIELD} must be text")
    value = raw.strip()
    if not value:
        raise InvalidPatch(f"{NOTE_TEXT_FIELD} cannot be empty")
    if any(
        character < " " or character == "\x7f"
        for character in value
        if character not in WHITESPACE_KEPT
    ):
        raise InvalidPatch(f"{NOTE_TEXT_FIELD} contains characters that cannot be stored")
    if len(value) > MAX_NOTE_LENGTH:
        raise InvalidPatch(f"{NOTE_TEXT_FIELD} must be {MAX_NOTE_LENGTH} characters or fewer")
    return value


def _view(row: sa.Row[Any]) -> DiaryNoteView:
    """One repository row → the view every surface reads."""
    note = row.DiaryNote
    return DiaryNoteView(
        id=note.id,
        claim_business_id=row.claim_business_id,
        worker_name=row.worker_name,
        note_text=note.note_text,
        noted_at=note.noted_at,
    )


async def list_diary_notes(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    cursor: str | None = None,
    limit: int | None = None,
    as_of: date | None = None,
) -> DiaryNotePage:
    """The caller's own notes, newest first, one page at a time (AC 3).

    Scoped to the caller and nothing else (AD-7) — there is no parameter here
    that could name a user, an employer or a role, so "whose notes?" has one
    answer and it comes from the session.

    **Not filtered by the selected claim**, and that is the story's own ruling
    rather than an omission: the prototype keys `diaryNotes` by handler across
    all their claims, and the list a handler reads in the morning is their
    diary, not this case file's. Each row carries its own tag, which is how
    FR-DIARY-1's "tied to a claim" is satisfied.

    **The ordering is total.** `(noted_at DESC, id DESC)`, never the timestamp
    alone: two saves inside one clock tick tie, and a keyset page that ended
    inside the tie would repeat one row and drop the other.

    Raises `InvalidCursor` for a cursor that does not describe a position in
    this list; the router answers 400. Never a silent page one.
    """
    today = as_of or utc_today()
    decoded = decode_cursor(cursor, today) if cursor is not None else None
    page_size = decoded.limit if decoded is not None else (limit or DEFAULT_PAGE_LIMIT)

    rows = await claim_repo.select_diary_notes_page(
        db,
        ctx,
        after=None if decoded is None else (decoded.last_noted_at, decoded.last_id),
        # One more than the page, so "is there another page?" is answered by
        # what came back rather than by comparing against `total` — which would
        # be wrong the moment another save changed the count between the two
        # statements.
        limit=page_size + 1,
    )
    total = await claim_repo.count_diary_notes(db, ctx)

    has_more = len(rows) > page_size
    items = tuple(_view(row) for row in rows[:page_size])
    next_cursor = (
        encode_cursor(
            Cursor(
                last_noted_at=items[-1].noted_at,
                last_id=items[-1].id,
                limit=page_size,
                issued_on=today,
            )
        )
        if has_more and items
        else None
    )
    return DiaryNotePage(items=items, next_cursor=next_cursor, total=total)


async def get_diary_note(db: AsyncSession, ctx: CallerContext, note_id: int) -> DiaryNoteView:
    """One note of the caller's, or `DiaryNoteNotVisible` (a 404).

    Not a route of its own — the SPA reads the list — but `create_diary_note`
    ends with it, so that the 201 body is what a subsequent list read will show
    rather than an echo of what the caller sent.
    """
    row = await claim_repo.select_diary_note(db, ctx, note_id)
    if row is None:
        raise DiaryNoteNotVisible(note_id)
    return _view(row)


async def create_diary_note(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    note_text: object,
    claim_business_id: str | None = None,
    now: datetime | None = None,
) -> DiaryNoteView:
    """Write one diary note, audited (AC 2).

    The ladder, in the house order: role, then the claim tag's scope (inside
    the insert, so there is no window), then the request's validity, then the
    write, then the audit event in the same transaction, then the commit.

    **There is no version to compare.** Creating is not a compare-and-swap:
    nothing exists yet to have moved, and the claim the note tags is not being
    changed. A concurrent edit to that claim is not a conflict with a note
    about it — `create_meeting` makes the identical argument one table over.

    **`noted_at` is stamped here, not by the database**, and it is the same
    instant the audit event carries. The column has a `now()` default so an
    INSERT that omitted it is not a trap, but a note and the record of it
    having been written should not be able to disagree by the width of a
    statement.

    Raises `EditNotPermitted` (403), `DiaryNoteClaimNotVisible` (404) or
    `InvalidPatch` (422).
    """
    if ctx.role is not UserRole.handler:
        raise EditNotPermitted("Only a claims handler can write a diary note.")

    text = normalise_note_text(note_text)
    noted_at = now or datetime.now(UTC)

    note_id = await claim_repo.insert_diary_note(
        db,
        ctx,
        claim_business_id=claim_business_id,
        values={"note_text": text, "noted_at": noted_at},
    )
    if note_id is None:
        # `insert_diary_note` answers `None` only when it was given a claim to
        # resolve and the `INSERT … SELECT` matched none — absent, or outside
        # the caller's book, and the caller learns nothing from which. Nothing
        # was written, but the transaction has taken a snapshot, so roll it
        # back before the router answers (`create_meeting`'s note).
        await db.rollback()
        raise DiaryNoteClaimNotVisible(claim_business_id)

    await audit.record(
        db,
        ctx,
        action=CREATE_ACTION,
        entity=ENTITY,
        entity_id=str(note_id),
        before=None,
        after=_diff(claim_business_id, note_text=text, noted_at=noted_at),
        at=noted_at,
    )
    await db.commit()

    db.expire_all()
    return await get_diary_note(db, ctx, note_id)


def _diff(
    claim_business_id: str | None,
    *,
    note_text: str,
    noted_at: datetime,
) -> Mapping[str, Any]:
    """The audit diff for one note — the row, plus its claim.

    `claim_id` is in the diff and not only in `entity_id` because `entity_id`
    holds the *note's* surrogate id: without this, Story 8.1's purge cascade
    would have no way to find the audit events belonging to a claim it is
    purging. It is the **business** id (`WC-nnnn`) rather than the surrogate,
    for the same reason — a surrogate that no longer resolves names nothing.
    `injuries.py::_diff` and `meetings.py::_diff` make the identical argument.

    `noted_at` is an ISO string rather than a `datetime` because the column is
    JSONB and `json.dumps` has never heard of one.
    """
    return {
        "claim_id": claim_business_id,
        "note_text": note_text,
        "noted_at": noted_at.isoformat(),
    }


__all__ = [
    "CREATE_ACTION",
    "DEFAULT_PAGE_LIMIT",
    "ENTITY",
    "MAX_CURSOR_AGE",
    "MAX_NOTE_LENGTH",
    "MAX_PAGE_LIMIT",
    "MIN_PAGE_LIMIT",
    "NOTE_TEXT_FIELD",
    "WHITESPACE_KEPT",
    "Cursor",
    "DiaryNoteClaimNotVisible",
    "DiaryNoteNotVisible",
    "DiaryNotePage",
    "DiaryNoteView",
    "InvalidCursor",
    "create_diary_note",
    "decode_cursor",
    "encode_cursor",
    "get_diary_note",
    "list_diary_notes",
    "normalise_note_text",
]
