"""Meetings — schedule, complete, delete, list (Story 4.1, FR-DIARY-2, AD-4).

The first slice of the **diary aggregate**, which AD-12 puts under
`services/claims`: this module is the only writer of `meeting`, and Stories
4.2 and 4.3 add their tables beside it rather than reaching into this one.

The prototype keeps meetings in a browser-lifetime object (`meetingsStore`,
line 1838) re-seeded at every login; here the three operations that change one
are AD-4 commands with the shape `services/claims/injuries.py` established —
the refusal ladder, the compare-and-swap, and the same-transaction audit event.
**Nothing here is a second write idiom**: the role refusal is
`services/claims/edit.py`'s `EditNotPermitted`, imported rather than restated,
so the router keeps one vocabulary across seven commands.

## What is deliberately absent

**No timeline event.** The prototype does not surface meetings on the claim
timeline, and AD-12 scopes `timeline_event` to *claim-mutating* commands — a
meeting changes no column of `claim`, and a log line saying otherwise would put
one handler's diary into another's view of the case file. `services/claims/
timeline.py::append` is not imported here.

**No egress of any kind.** No ICS, no calendar API, no invitation, no
notification. "Scheduling" a meeting writes a row and nothing else; real
calendar egress is a Deferred architecture decision with its own compliance
review. The row is a log of intent, and this module must not grow a delivery
path in a later story without that decision being made first.

**No claim version anywhere.** `add_additional_injury` compare-and-swaps on the
*claim's* version because the case file the handler read is what the finding
belongs to; a meeting is not a fact about the claim, so scheduling one against
a claim somebody edited a second ago is not a conflict — it is two people doing
unrelated work. What *is* compare-and-swapped is the meeting row itself, on
complete and on delete, because that is the thing being destroyed or moved.

## Two scopes, one predicate

Every function here takes `CallerContext` and applies it in the repository
only (AD-7). `meeting_scope` is owner **and** employer scope: a meeting is
visible to the handler who holds it, and its claim link additionally has to
survive that handler's current employer scope. Out of scope and absent are the
same 404 — `MeetingNotVisible` carries no distinction, for the reason
`select_claim_detail` conflates them.

## AD-11

`notes`, `location` and `participants` are PHI-class. The audit diff carries
them because the row *is* the change and a log that recorded less could not say
what was scheduled or removed (`injuries.py::_diff`'s argument); the structlog
line `services.audit` emits carries ids and field *names* only, and nothing in
this module logs a value.
"""

import base64
import binascii
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models import Meeting
from data.models.enums import MeetingParticipant, MeetingType, UserRole
from data.repositories import claims as claim_repo
from data.repositories.claims import MEETING_ALL_DAY_SORT_TIME
from rules.parameters import thresholds_for
from services import audit, derivations
from services.claims.edit import WHITESPACE_KEPT, EditNotPermitted, InvalidPatch
from services.derivations import (
    MeetingStatus,
    MeetingStatusDerivation,
    upcoming_predicate,
    utc_today,
)

CREATE_ACTION: Final[str] = "create_meeting"
COMPLETE_ACTION: Final[str] = "complete_meeting"
DELETE_ACTION: Final[str] = "delete_meeting"
ENTITY: Final[str] = "meeting"

#: Page-size bounds, declared once and enforced twice — FastAPI refuses a
#: `limit` outside them before this module is reached, and `decode_cursor`
#: refuses one smuggled inside a cursor. `services/worklist/queue.py`'s rule:
#: a cursor is caller-supplied input like any other query parameter, and a
#: forged one must not be the way past the route's ceiling.
MIN_PAGE_LIMIT: Final[int] = 1
MAX_PAGE_LIMIT: Final[int] = 200
DEFAULT_PAGE_LIMIT: Final[int] = 50

#: How stale a cursor's recorded day may be before it stops describing a list
#: worth continuing — `queue.py`'s bound, for its reason. A day in the future
#: is refused outright: no cursor this service issued can name one.
MAX_CURSOR_AGE: Final[timedelta] = timedelta(days=7)

#: How far a caller-supplied calendar day may sit from the server's own before
#: it stops being a timezone and starts being a request about a different list.
#:
#: `day` and `as_of` are the viewer's local calendar day, sent by a browser
#: because a server with no timezone for the reader cannot resolve "today". The
#: widest real offset is UTC+14 to UTC−12, so one day either side covers every
#: reader on earth with room to spare — and refuses `?day=1970-01-01`, which
#: since these two parameters became the *horizon* would answer "every meeting
#: you have ever had is upcoming" for the whole book. Before that they only
#: narrowed membership and an absurd value was merely an empty page.
#:
#: Enforced here rather than only on the route for AD-16's reason: an Epic 6
#: agent tool calling `list_meetings` directly is untrusted input too.
MAX_CLOCK_SKEW: Final[timedelta] = timedelta(days=1)

#: The field name the refusal spells, for the clock parameter this module
#: bounds. `day` is a query parameter rather than a body field, and a 422 still
#: has to name the one that was wrong. (`asOf` is bounded at the route, which
#: spells its own alias — see `api/routers/diary.py::_viewer_day`.)
DAY_FIELD: Final[str] = "day"


class MeetingNotVisible(Exception):
    """No such meeting in the caller's diary — a 404.

    One exception for "does not exist", "belongs to another handler" and "its
    claim has left your book", deliberately, and the sameness is the security
    property: a meeting is addressed by a dense surrogate id, so a caller
    comparing two refusals must not be able to walk 1…10000 and learn how many
    meetings the portfolio holds. `ClaimNotVisible` makes the same argument one
    aggregate up.
    """

    def __init__(self, meeting_id: int) -> None:
        super().__init__(f"no meeting {meeting_id} in your diary")
        self.meeting_id = meeting_id


class MeetingClaimNotVisible(Exception):
    """The claim a new meeting names is not in the caller's book — a 404.

    Distinct from `MeetingNotVisible` because the two name different things (a
    meeting id and a claim business id) and the router's wording differs, not
    because the *caller* learns anything from the difference: both are the same
    "no such thing, for you" answer, and neither confirms existence.
    """

    def __init__(self, claim_business_id: str | None) -> None:
        super().__init__(f"no claim {claim_business_id} in your caseload")
        self.claim_business_id = claim_business_id


class StaleMeeting(Exception):
    """Somebody else changed the meeting first — a 409 carrying fresh state.

    The fresh entity travels *with* the exception rather than being fetched
    again by the router, `StaleClaim`'s call: the command already has it in
    hand, so the 409 body can carry it and the SPA can render current state
    without a second request.
    """

    def __init__(self, fresh: "MeetingView") -> None:
        super().__init__(f"meeting {fresh.id} has moved on from the version you were holding")
        self.fresh = fresh


class InvalidCursor(ValueError):
    """A cursor that does not describe a position in the caller's diary."""


@dataclass(frozen=True)
class MeetingView:
    """One meeting as every surface reads it — the row, its claim, its status.

    `claim_business_id` and `worker_name` are on the view rather than looked up
    by the card, because the card renders `WC-nnnn — Worker Name` and a browser
    that assembled that from a second request would be showing a claim
    reference the list read did not scope.

    `status` is the registered derivation's answer (AD-10), computed once per
    request against one `as_of` so that every meeting in a page is judged
    against the same day. The SPA renders it and never recomputes it from
    `meeting_date` — the prototype's `m.date >= today` in the component is
    exactly what this replaces.
    """

    id: int
    claim_business_id: str | None
    worker_name: str | None
    meeting_type: MeetingType
    meeting_date: date
    meeting_time: time | None
    location: str | None
    notes: str | None
    participants: tuple[MeetingParticipant, ...]
    is_done: bool
    version: int
    created_at: datetime
    status: MeetingStatus


@dataclass(frozen=True)
class MeetingPage:
    """One page of the caller's diary, in the `{items, nextCursor, total}` shape.

    `total` is the size of the whole scoped list rather than of `items`, the
    Lists convention's rule and `StageGroup`'s: it is what a count beside the
    sub-tab shows, and a number that shrank when the page did would misdescribe
    the diary. When the read is narrowed to one `day`, `total` describes *that*
    list — the summary's "📅 Today's Meetings (N)" — because a total that
    counted rows the caller did not ask for would be a number about a different
    question.

    `upcoming_count` is the opposite: **always the whole book, never the
    filtered day** (Story 4.2). It is the greeting's "📅 N upcoming meetings —
    see below / Meetings tab", which is a statement about the diary, and it
    rides this envelope so that the Notes sub-tab makes *one* request rather
    than a second one purely to count.
    """

    items: tuple[MeetingView, ...]
    next_cursor: str | None
    total: int
    upcoming_count: int


@dataclass(frozen=True)
class Cursor:
    """Where a page ended, and the page size that cut it.

    **Keyset, not offset**, which is why the position is a
    `(date, time, id)` triple rather than a row count: a meeting scheduled or
    completed between two pages shifts every offset after it, and a handler
    scheduling one mid-scroll is the ordinary case rather than the exotic one.
    The triple is the sort key itself, so the row it names is unambiguous.

    **`last_time` is the *coalesced* sort time, not the stored column.** An
    all-day meeting sorts at midnight (`data/repositories/claims.py::
    MEETING_ALL_DAY_SORT_TIME`), and a cursor carrying `None` for one would put
    a NULL inside a row-value comparison — which evaluates to NULL rather than
    to false, so every page beginning at an all-day meeting would silently
    return nothing.

    Story 4.2 added that member, so **cursors issued before it are refused** as
    `/problems/invalid-cursor` rather than misread: `decode_cursor` requires the
    key, and the alternative — defaulting a missing time — would resume a walk
    at the wrong row without saying so.

    `limit` is *reused* when a request omits one, `queue.py`'s division: it
    describes the window rather than the ordering, and re-deriving it mid-list
    is what skips a row. `issued_on` is *validated* and never used to select
    anything — it exists so a cursor found in a bookmark a year later is
    refused with a sentence instead of silently re-paging a list that has
    moved on. It is stamped from `utc_today()` and checked against it, never
    against the caller's `day`: taking the caller's day for both made the two
    bounds compare a value with itself, so the expiry could not fire on any
    day-filtered page.

    **`day` is *compared*, not reused**, which is the other half of
    `queue.py`'s division and the reason it is on the cursor at all. A cursor
    issued by `?day=2026-08-17` names a position in *that day's* list; replayed
    without the parameter it would name a position in the whole book and page
    forward from there, silently skipping every meeting that sorts earlier. The
    route's own 400 already promises to refuse "a cursor that belongs to a
    different filter", so `list_meetings` compares this member against the
    request's `day` and answers `/problems/invalid-cursor` on a mismatch —
    `queue.py` compares `queue_filter` and `stage` for exactly this reason.

    `None` is a value like any other here: it means "the whole diary", and a
    whole-diary cursor replayed *with* a day is refused just as loudly.

    Nothing about the ordering can change underneath a caller the way the
    queue's can: this list is sorted in SQL by stored columns, not by Python
    arithmetic over a rule document, so there is no rules version to record and
    no re-ranking to detect.
    """

    last_date: date
    last_time: time
    last_id: int
    limit: int
    issued_on: date
    day: date | None


def encode_cursor(cursor: Cursor) -> str:
    """Base64url of a compact JSON object, unpadded — `queue.py`'s encoding.

    Opaque by intent rather than by encryption: it carries no claim data and
    nothing a caller could use to widen their scope (scope is never in the
    request — AD-7). What the encoding buys is that clients treat it as a token
    to hand back rather than a key to increment.
    """
    payload = json.dumps(
        {
            "d": cursor.last_date.isoformat(),
            "t": cursor.last_time.isoformat(),
            "i": cursor.last_id,
            "l": cursor.limit,
            "s": cursor.issued_on.isoformat(),
            "g": None if cursor.day is None else cursor.day.isoformat(),
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(raw: str, as_of: date | None = None) -> Cursor:
    """Parse a cursor and bound its age, or refuse it (never a silent page one).

    The two halves are separate functions because `list_meetings` needs them in
    the other order — see `_check_cursor_age`. This is the whole check, in the
    order a caller with no filter to compare wants it, and it is what the tests
    exercise.
    """
    cursor = _parse_cursor(raw)
    _check_cursor_age(cursor, as_of or utc_today())
    return cursor


def _parse_cursor(raw: str) -> Cursor:
    """The structural half: decode it, and refuse a position that cannot exist.

    Answering page one for an undecodable cursor turns a client bug into an
    infinite "Show more" that re-appends the same meetings for ever — the
    failure `queue.py` names, and every bound below is its answer:

    - `ArithmeticError` sits beside `ValueError` in the except tuple, because
      `json` accepts the literal `Infinity` and `int(float("inf"))` raises
      `OverflowError`, which is not a `ValueError`. A forged cursor carrying
      one escaped the queue's decoder as a 500 before it was added.
    - `limit` is held to the same range the route declares, or the cursor is a
      way around the route's ceiling: it is *reused* when the request omits
      one, so a forged page size would never pass FastAPI's validator.

    **A tz-aware `last_time` is refused**, `notes.py::decode_cursor`'s guard for
    its reason one table over: `time.fromisoformat("12:00:00+05:00")` parses
    happily and passes every bound below, and the column is a naive `Time` — so
    the offset would reach the row-value comparison and raise deep inside the
    driver instead of answering 400 at the boundary.
    """
    try:
        padded = raw + "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        raw_day = data["g"]
        cursor = Cursor(
            last_date=date.fromisoformat(data["d"]),
            # `KeyError` for a cursor issued before Story 4.2 added the sort
            # time, which is the intended answer: it is refused as unreadable
            # rather than resumed at a row it does not name. See `Cursor`.
            last_time=time.fromisoformat(data["t"]),
            last_id=int(data["i"]),
            limit=int(data["l"]),
            issued_on=date.fromisoformat(data["s"]),
            # Read above rather than inline so the `KeyError` is the same
            # refusal the sort time's is: a cursor issued before the day was
            # recorded names a position in a list this decoder cannot identify.
            day=None if raw_day is None else date.fromisoformat(raw_day),
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
    if cursor.last_time.tzinfo is not None:
        raise InvalidCursor("The pagination cursor names a position that cannot exist.")
    if cursor.last_id < 1:
        raise InvalidCursor("The pagination cursor names a position that cannot exist.")
    if not MIN_PAGE_LIMIT <= cursor.limit <= MAX_PAGE_LIMIT:
        raise InvalidCursor(
            f"The pagination cursor names a page size of {cursor.limit}; "
            f"it must be between {MIN_PAGE_LIMIT} and {MAX_PAGE_LIMIT}."
        )
    return cursor


def _check_cursor_age(cursor: Cursor, today: date) -> None:
    """The clock half: `issued_on` is bounded by `MAX_CURSOR_AGE` both ways.

    **Judged against the server's own day, never the caller's**, which is the
    other half of the fix that made `issued_on` a server stamp. The parameter
    stays a parameter so a test can name a day, and `decode_cursor` defaults it
    to `utc_today()`; nothing passes the viewer's `day` in.

    **Run after the filter comparison, not before it.** `list_meetings` decodes
    with `_parse_cursor`, compares the cursor's `day` against the request's, and
    only then calls this — because a cursor from another day that is *also* old
    was being refused with "…is in the future" or "…has expired", a sentence
    about the clock for a failure about the filter. Order the checks so the
    message names what actually went wrong.
    """
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


def check_viewer_day(field: str, value: date | None, today: date | None = None) -> None:
    """Refuse a caller-supplied *viewer clock* too far from the server's own.

    See `MAX_CLOCK_SKEW`. `InvalidPatch` rather than a type of its own, so the
    route answers the 422 it already answers for every other unusable parameter
    value, naming the field the way the request spells it.

    **What this bounds is a clock, not a filter**, and the distinction is the
    whole of why `day` is only checked when `as_of` is absent. A caller who
    sends `day` alone is saying "this is the day I am in", and a value a
    timezone cannot explain is then a request for a horizon rather than for a
    page — `?day=1970-01-01` answering that every meeting the caller has ever
    had is still ahead. A caller who sends both has named the horizon
    separately, so `day` is doing nothing but narrowing membership, and
    narrowing to an arbitrary date is a legitimate (if unused) read. `as_of`
    itself is bounded at the route, where it arrives from a client;
    `list_meetings`' own parameter stays open for the reason every derivation
    in this codebase takes an `as_of` — a test has to be able to name a day.
    """
    if value is None:
        return
    reference = today if today is not None else utc_today()
    if abs(value - reference) > MAX_CLOCK_SKEW:
        raise InvalidPatch(
            f"{field} must be within {MAX_CLOCK_SKEW.days} day of the server's "
            f"current date ({reference.isoformat()})"
        )


#: The longest a meeting's free text may be. Two different caps because the
#: two fields are different kinds of thing: a location is a room name or a
#: meeting link, an agenda is prose. Enforced on the wire (`api/routers/
#: diary.py` declares them on its request model) *and* here, so a caller that
#: is not the SPA — an Epic 6 agent tool, whose arguments are untrusted
#: content (AD-16) — meets the same bound.
MAX_LOCATION_LENGTH: Final[int] = 200
MAX_NOTES_LENGTH: Final[int] = 2000

#: Field names as the refusal messages spell them, so a 422 names the control
#: the handler is looking at rather than a column.
LOCATION_FIELD: Final[str] = "location"
NOTES_FIELD: Final[str] = "notes"
PARTICIPANTS_FIELD: Final[str] = "participants"
MEETING_TIME_FIELD: Final[str] = "meetingTime"


@dataclass(frozen=True)
class NewMeeting:
    """A validated meeting, ready to insert.

    Returned by `normalise_new_meeting` so that validation is callable — and
    testable — without a database, exactly as `edit.normalise` and
    `injuries.normalise_new_injury` are.
    """

    meeting_type: MeetingType
    meeting_date: date
    meeting_time: time | None
    location: str | None
    notes: str | None
    participants: tuple[MeetingParticipant, ...]


#: The characters that end a line, and therefore the ones a single-line control
#: cannot carry. `\v` and `\f` are in `edit.WHITESPACE_KEPT` because a word
#: processor writes them into prose; they still break a one-line render.
_LINE_BREAKS: Final[frozenset[str]] = frozenset({"\n", "\r", "\v", "\f"})


def _optional_text(field: str, raw: object, limit: int, *, multiline: bool) -> str | None:
    """A trimmed, capped, control-character-free string — or `None` if empty.

    `edit.require_text`'s rules with the requirement removed, because these two
    fields are genuinely optional: a meeting with no agenda is a meeting
    somebody has not written the agenda for yet. An empty string normalises to
    `None` rather than being stored, so "not set" has one representation and
    the card's `notes && …` branch cannot be fooled by whitespace.

    The C0 range and DEL are refused for `require_text`'s reason: PostgreSQL
    `text` cannot hold a NUL and asyncpg raises rather than truncating, which
    reached the request as a 500 rather than a 422.

    **`multiline` decides which whitespace survives that rule, and the two
    answers are different because the two controls are.** An agenda is a
    `<textarea>` holding pasted prose, so it keeps `edit.WHITESPACE_KEPT` —
    Story 4.2 made that fix for a diary note and did not back-port it here,
    which left a tab-indented agenda pasted out of Word or Outlook refused as
    "contains characters that cannot be stored", a sentence that is not true of
    a tab and that names nothing the handler can act on. A location is a
    single-line `<input>` and the card renders it after a middot on the
    when-line, so a newline in it breaks that line into two: it keeps the tab
    (a paste artefact with no layout consequence) and refuses the two line
    breaks with a message that says which.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise InvalidPatch(f"{field} must be text")
    value = raw.strip()
    if not value:
        return None
    kept = WHITESPACE_KEPT if multiline else WHITESPACE_KEPT - _LINE_BREAKS
    if not multiline and any(character in _LINE_BREAKS for character in value):
        raise InvalidPatch(f"{field} must be a single line")
    if any(character < " " or character == "\x7f" for character in value if character not in kept):
        raise InvalidPatch(f"{field} contains characters that cannot be stored")
    if len(value) > limit:
        raise InvalidPatch(f"{field} must be {limit} characters or fewer")
    return value


def normalise_participants(
    raw: Sequence[MeetingParticipant | str],
) -> tuple[MeetingParticipant, ...]:
    """De-duplicate and re-order the checkbox set into enum order.

    **The stored order is the vocabulary's, not the request's**, and that is
    worth a function rather than a `list(...)`: the participant tags are read
    left to right on every card, and a payload built by a client that happened
    to send its checkboxes in a different order would render two identical
    meetings with their tags shuffled. Ordering here also makes the audit diff
    of an unchanged set byte-identical, so a future edit command's diff shows
    what actually moved.

    Duplicates are dropped rather than refused: `["employee", "employee"]` is a
    client bug with an unambiguous meaning, and a 422 for it would be a refusal
    a handler could neither see nor fix.

    **An unknown token is refused, not dropped**, which is `MeetingParticipant`'s
    own promise ("refused at the boundary") and not merely tidiness. Pydantic
    already refuses one on the HTTP path, but AD-16 anticipates a caller that is
    not this SPA — an Epic 6 agent tool, whose arguments are untrusted content —
    and a silent filter would write a set the caller never sent, record *that*
    set in the audit diff, and leave `_view` raising on the next list read.
    """
    chosen: set[MeetingParticipant] = set()
    for value in raw:
        try:
            chosen.add(MeetingParticipant(value))
        except ValueError as exc:
            # The token is not echoed: it is caller-supplied text, and the
            # vocabulary is small enough that naming the field is enough to fix
            # the call (`_optional_text`'s rule, AD-11).
            raise InvalidPatch(
                f"{PARTICIPANTS_FIELD} names a stakeholder role that does not exist"
            ) from exc
    return tuple(member for member in MeetingParticipant if member in chosen)


def normalise_new_meeting(
    meeting_type: MeetingType,
    meeting_date: date,
    meeting_time: time | None,
    location: object,
    notes: object,
    participants: Sequence[MeetingParticipant | str],
) -> NewMeeting:
    """Check what a scheduler modal can send, or raise `InvalidPatch` (a 422).

    `meeting_type` and `meeting_date` are already typed by the time they
    arrive over HTTP: the route declares them, so a missing date or an unknown
    type is FastAPI's global body validation and answers
    `/problems/validation-error` naming the field — which is AC 2's server
    half, and the reason there is no "date is required" check here to
    duplicate it.

    What is left is the free text, the participant vocabulary and the time's
    shape, all re-checked here rather than trusted because this command has a
    caller that is not the route (AD-16). Messages name the field and the rule
    and never echo the value (AD-11).

    **A `meeting_time` carrying a UTC offset is refused**, `decode_cursor`'s
    guard for its reason at the other end of the same column.
    `time.fromisoformat("10:00+05:00")` parses happily and Pydantic hands it
    straight through, and the column is a naive `Time` — so the offset reaches
    asyncpg and raises inside the driver instead of answering 422 at the
    boundary. There is nowhere to put an offset even if it survived: the card
    renders the wall clock the handler typed, and this console collects no
    timezone to interpret one against.
    """
    if meeting_time is not None and meeting_time.tzinfo is not None:
        raise InvalidPatch(f"{MEETING_TIME_FIELD} must be a wall-clock time with no UTC offset")
    return NewMeeting(
        meeting_type=meeting_type,
        meeting_date=meeting_date,
        meeting_time=meeting_time,
        location=_optional_text(LOCATION_FIELD, location, MAX_LOCATION_LENGTH, multiline=False),
        notes=_optional_text(NOTES_FIELD, notes, MAX_NOTES_LENGTH, multiline=True),
        participants=normalise_participants(participants),
    )


def _view(row: sa.Row[Any], as_of: date, status: MeetingStatusDerivation) -> MeetingView:
    """One repository row → the view every surface reads.

    The status is derived here rather than on the wire model for AD-10's
    reason: one computer, called once per row, against the `as_of` the request
    resolved — so a page cannot contain two meetings judged against two days.
    """
    meeting = row.Meeting
    return MeetingView(
        id=meeting.id,
        claim_business_id=row.claim_business_id,
        worker_name=row.worker_name,
        meeting_type=meeting.meeting_type,
        meeting_date=meeting.meeting_date,
        meeting_time=meeting.meeting_time,
        location=meeting.location,
        notes=meeting.notes,
        # Stored as a JSONB array of tokens, which the database hands back as
        # plain strings — the column has no enum type to decode them with, so
        # the vocabulary is re-applied here. A token the enum does not know
        # would raise, which is the right answer: it could only have been
        # written by something that is not this module (AD-12).
        participants=tuple(MeetingParticipant(value) for value in meeting.participants),
        is_done=meeting.is_done,
        version=meeting.version,
        created_at=meeting.created_at,
        status=status.of(meeting, as_of),
    )


async def _status_computer(db: AsyncSession, as_of: date) -> MeetingStatusDerivation:
    """The registered `meeting_status` computer, built from today's thresholds.

    The derivation reads no threshold, but it is still reached through the
    registry rather than instantiated directly — that is the whole point of
    AD-10's single entry, and a call site that constructed the class would be
    the second computer the registry exists to prevent.

    Resolved once per request and handed to every row, so a page cannot
    contain two meetings judged by two objects.
    """
    thresholds = await thresholds_for(db, as_of)
    return derivations.meeting_status.for_thresholds(thresholds)


async def list_meetings(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    cursor: str | None = None,
    limit: int | None = None,
    day: date | None = None,
    as_of: date | None = None,
) -> MeetingPage:
    """The caller's meetings, oldest first, one page at a time (AC 3).

    Scoped to the caller and nothing else (AD-7) — there is no parameter here
    that could name a user, an employer or a role, so "whose meetings?" has one
    answer and it comes from the session.

    **The ordering is total.** `(meeting_date, COALESCE(meeting_time, '00:00'),
    id)`, never the date alone: a handler routinely schedules two touchpoints on
    one day, and a keyset page that ended inside that tie would repeat one row
    and drop the other (Story 3.5's learning, restated in
    `select_meetings_page`). The time member is Story 4.2's, and it is what lets
    one ordering serve both the whole diary and a single day.

    **`day` narrows the same list rather than opening a second one.** The Notes
    sub-tab's today's-meetings summary is this read with the parameter set, so
    there is no new endpoint, no second writer and no second sort. Filtering a
    *page* of the unfiltered list in the browser was the alternative and it is
    the fifty-row truncation bug Story 4.1's review already caught once: today's
    meetings sort after every past one.

    The day is the **viewer's local calendar day**, resolved by the browser and
    sent as a parameter — the same class of client input as `as_of`, and the
    only kind of "today" a server without the reader's timezone can honour.

    **`day` and `as_of` are one clock, not two**, and that is the correction a
    review forced. A caller who names a `day` is asking about *that* day, so it
    is also the day this read judges against: `status` and `upcoming_count` are
    resolved at `as_of or day or utc_today()`. Before, `day` came from the
    browser's local calendar and `as_of` fell back to `utc_today()`, so a
    Pacific handler after 17:00 local asked about a day the server had already
    left — every one of today's meetings evaluated `meeting_date >= as_of` as
    false, came back `done`, rendered in the done tone with no ✓ control, and
    was excluded from the greeting's count. The two comparisons were against two
    notions of today, which is the exact drift `meeting_horizon.py` exists to
    prevent, reached through a parameter instead of through a second `>=`.

    **`as_of` without `day` is the *unfiltered* caller's half of that same
    fix**, and it is why the parameter is on the wire rather than only in tests.
    The Meetings sub-tab reads the whole book and had no day to judge against,
    so it fell back to `utc_today()` while the Notes summary judged at the
    viewer's — and the same meeting rendered `upcoming` with a ✓ Done control in
    one sub-tab and greyed-out in the other, one click apart. Both surfaces now
    send the reader's local day: one as `day` (which filters *and* judges), one
    as `as_of` (which only judges).

    **Both are bounded** — see `MAX_CLOCK_SKEW`. They were free-form before,
    which was survivable while `day` merely narrowed membership and is not now
    that it sets the horizon: `?day=1970-01-01` would answer that every meeting
    the caller has ever had is still ahead.

    `upcoming_count` is still the **whole book** rather than the filtered day —
    only the day it is judged against moved. See `MeetingPage`.

    Raises `InvalidCursor` for a cursor that does not describe a position in
    this list (the router answers 400) and `InvalidPatch` for a clock outside
    the window (422). Never a silent page one.
    """
    server_today = utc_today()
    if as_of is None:
        # `day` is the horizon on this call, so it is a clock and is bounded.
        # With `as_of` supplied it is only a filter — see `check_viewer_day`.
        check_viewer_day(DAY_FIELD, day, server_today)
    today = as_of if as_of is not None else (day if day is not None else server_today)

    # Parsed, then compared against the filter, then aged — in that order.
    # Aging first meant a cursor cut from another day *and* a week old was
    # refused with "…has expired", a sentence about the clock for a failure
    # about the filter. See `_check_cursor_age`.
    decoded = _parse_cursor(cursor) if cursor is not None else None
    if decoded is not None and decoded.day != day:
        # `queue.py`'s refusal, one aggregate over: a cursor names a position in
        # the list it was cut from, and replaying a day-filtered one without the
        # day would page the whole diary from that position — skipping every
        # meeting that sorts earlier, silently and with a 200.
        raise InvalidCursor(
            f"That page belongs to {decoded.day.isoformat() if decoded.day else 'the whole diary'}"
            f", not {day.isoformat() if day else 'the whole diary'}."
        )
    if decoded is not None:
        # Against the **server's** day, because that is what stamped
        # `issued_on`. Judging it against the caller's `day` made both bounds
        # `day > day` and `day - day > 7 days` on every day-filtered page —
        # structurally unreachable, so the expiry was dead code on the one read
        # that uses it most.
        _check_cursor_age(decoded, server_today)
    page_size = decoded.limit if decoded is not None else (limit or DEFAULT_PAGE_LIMIT)

    rows = await claim_repo.select_meetings_page(
        db,
        ctx,
        after=(
            None if decoded is None else (decoded.last_date, decoded.last_time, decoded.last_id)
        ),
        # One more than the page, so "is there another page?" is answered by
        # what came back rather than by comparing against `total` — which would
        # be wrong the moment somebody else's write changed the count between
        # the two statements.
        limit=page_size + 1,
        day=day,
    )
    total = await claim_repo.count_meetings(db, ctx, day=day)
    # The rule, in SQL, from the one module that owns it (AD-10) — never a
    # second `>=` written here beside the Python classifier three lines down.
    upcoming = await claim_repo.count_meetings_matching(
        db, ctx, upcoming_predicate(Meeting.meeting_date, Meeting.is_done, as_of=today)
    )

    status = await _status_computer(db, today)
    has_more = len(rows) > page_size
    items = tuple(_view(row, today, status) for row in rows[:page_size])
    next_cursor = (
        encode_cursor(
            Cursor(
                last_date=items[-1].meeting_date,
                # The coalesced sort position, not the stored column: an
                # all-day meeting sorts at midnight and a NULL in the cursor
                # would make the next page's row-value comparison evaluate to
                # NULL. See `Cursor`.
                last_time=items[-1].meeting_time or MEETING_ALL_DAY_SORT_TIME,
                last_id=items[-1].id,
                limit=page_size,
                # The **server's** day, never the resolved clock: `issued_on`
                # exists so a cursor found in a bookmark a year later is refused,
                # and a value the caller supplied is not evidence of when this
                # service issued anything.
                issued_on=server_today,
                # Recorded so the next page can be refused if it arrives asking
                # about a different list. See `Cursor`.
                day=day,
            )
        )
        if has_more and items
        else None
    )
    return MeetingPage(items=items, next_cursor=next_cursor, total=total, upcoming_count=upcoming)


async def get_meeting(
    db: AsyncSession,
    ctx: CallerContext,
    meeting_id: int,
    *,
    as_of: date | None = None,
) -> MeetingView:
    """One meeting of the caller's, or `MeetingNotVisible` (a 404).

    Not a route of its own — the SPA reads the list — but the two commands
    below need it to build a 409's fresh entity and to tell a stale write from
    a missing row.
    """
    row = await claim_repo.select_meeting(db, ctx, meeting_id)
    if row is None:
        raise MeetingNotVisible(meeting_id)
    today = as_of or utc_today()
    return _view(row, today, await _status_computer(db, today))


async def create_meeting(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str | None,
    meeting_type: MeetingType,
    meeting_date: date,
    meeting_time: time | None = None,
    location: object = None,
    notes: object = None,
    participants: Sequence[MeetingParticipant | str] = (),
    as_of: date | None = None,
    now: datetime | None = None,
) -> MeetingView:
    """Schedule one meeting, audited (AC 1, AC 3).

    The ladder, in the house order: role, then the claim link's scope (inside
    the insert, so there is no window), then the request's validity, then the
    write, then the audit event in the same transaction, then the commit.

    **There is no version to compare.** Creating is not a compare-and-swap:
    nothing exists yet to have moved, and the claim the meeting names is not
    being changed. `add_additional_injury` guards on the *claim's* version
    because a secondary injury is a finding about the case file the handler was
    reading; a meeting is a note in the handler's own diary that happens to
    reference a claim, so a concurrent edit to that claim is not a conflict
    with it.

    **`as_of` is the viewer's day here too**, and it is what the 201 body's
    `status` is judged against. Without it the create path fell back to
    `utc_today()` while the scheduler pre-fills the handler's *local* today, so
    a Pacific handler scheduling a meeting for this afternoon received it back
    marked `done` — the same one-clock defect the list had, on the one response
    a handler sees immediately after acting. Bounded like the list's.

    Raises `EditNotPermitted` (403), `MeetingClaimNotVisible` (404) or
    `InvalidPatch` (422).
    """
    if ctx.role is not UserRole.handler:
        raise EditNotPermitted("Only a claims handler can schedule a meeting.")

    meeting = normalise_new_meeting(
        meeting_type, meeting_date, meeting_time, location, notes, participants
    )

    meeting_id = await claim_repo.insert_meeting(
        db,
        ctx,
        claim_business_id=claim_business_id,
        values={
            "meeting_type": meeting.meeting_type,
            "meeting_date": meeting.meeting_date,
            "meeting_time": meeting.meeting_time,
            "location": meeting.location,
            "notes": meeting.notes,
            "participants": [member.value for member in meeting.participants],
            "is_done": False,
        },
    )
    if meeting_id is None:
        # `insert_meeting` answers `None` only when it was given a claim to
        # resolve and the `INSERT … SELECT` matched none — absent, or outside
        # the caller's book, and the caller learns nothing from which. Nothing
        # was written, but the transaction has taken a snapshot, so roll it
        # back before the router answers (`add_additional_injury`'s note).
        await db.rollback()
        raise MeetingClaimNotVisible(claim_business_id)

    await audit.record(
        db,
        ctx,
        action=CREATE_ACTION,
        entity=ENTITY,
        entity_id=str(meeting_id),
        before=None,
        after=_diff(
            claim_business_id,
            meeting.meeting_type,
            meeting_date=meeting.meeting_date,
            meeting_time=meeting.meeting_time,
            location=meeting.location,
            notes=meeting.notes,
            participants=[member.value for member in meeting.participants],
            is_done=False,
        ),
        at=now or datetime.now(UTC),
    )
    await db.commit()

    db.expire_all()
    return await get_meeting(db, ctx, meeting_id, as_of=as_of)


async def complete_meeting(
    db: AsyncSession,
    ctx: CallerContext,
    meeting_id: int,
    *,
    expected_version: int,
    as_of: date | None = None,
    now: datetime | None = None,
) -> MeetingView:
    """Mark one meeting done under compare-and-swap, audited (AC 4).

    Raises `EditNotPermitted` (403), `MeetingNotVisible` (404) or
    `StaleMeeting` (409 carrying the fresh entity).

    **The `before` diff is read before the write and the values are read into
    locals first.** The session is `expire_on_commit=False`, so an ORM object
    read after the commit would compare against itself; the two integers and
    the flag this event records are taken while they are still the old ones.
    """
    if ctx.role is not UserRole.handler:
        raise EditNotPermitted("Only a claims handler can complete a meeting.")

    existing = await get_meeting(db, ctx, meeting_id, as_of=as_of)
    if existing.version != expected_version:
        # The pre-check answers a *stale* client before the statement runs, for
        # `update_claim_fields`' reason: it is the more useful answer, and the
        # statement's own predicate is still what makes the guard sound.
        raise StaleMeeting(existing)

    claim_ref = existing.claim_business_id

    changed = await claim_repo.complete_meeting_cas(db, ctx, meeting_id, expected_version)
    if changed == 0:
        # The row moved between the read above and the update — or it was
        # already done, which the statement also refuses. Roll back first: the
        # failed UPDATE has taken a snapshot, and the fresh entity is read
        # after it.
        await db.rollback()
        raise StaleMeeting(await get_meeting(db, ctx, meeting_id, as_of=as_of))

    await audit.record(
        db,
        ctx,
        action=COMPLETE_ACTION,
        entity=ENTITY,
        entity_id=str(meeting_id),
        before={"claim_id": claim_ref, "is_done": False},
        after={"claim_id": claim_ref, "is_done": True},
        at=now or datetime.now(UTC),
    )
    await db.commit()

    db.expire_all()
    return await get_meeting(db, ctx, meeting_id, as_of=as_of)


async def delete_meeting(
    db: AsyncSession,
    ctx: CallerContext,
    meeting_id: int,
    *,
    expected_version: int,
    as_of: date | None = None,
    now: datetime | None = None,
) -> None:
    """Remove one meeting under compare-and-swap, audited (AC 4).

    The audit event's `before` is the whole row and its `after` is `None` — the
    two halves of "this existed and now does not". A whole-row diff is right
    here for `remove_additional_injury`'s reason: the row *is* the change, and
    a diff that recorded less would leave the log unable to say what was
    deleted.

    Returns nothing; the route answers 204. Unlike the claim commands there is
    no entity to hand back, because the list is a separate read model and the
    SPA invalidates its key.

    Raises `EditNotPermitted` (403), `MeetingNotVisible` (404) or
    `StaleMeeting` (409 carrying the fresh entity).
    """
    if ctx.role is not UserRole.handler:
        raise EditNotPermitted("Only a claims handler can delete a meeting.")

    # Read first, for the claim reference the audit diff needs: the DELETE's
    # `RETURNING` gives back the surrogate `claim_id`, and an audit row naming
    # a primary key rather than a `WC-nnnn` would leave Story 8.1's purge
    # cascade unable to find the events belonging to a claim it is purging
    # (`injuries.py::_diff`).
    existing = await get_meeting(db, ctx, meeting_id, as_of=as_of)

    removed = await claim_repo.delete_meeting_cas(db, ctx, meeting_id, expected_version)
    if removed is None:
        await db.rollback()
        raise StaleMeeting(await get_meeting(db, ctx, meeting_id, as_of=as_of))

    await audit.record(
        db,
        ctx,
        action=DELETE_ACTION,
        entity=ENTITY,
        entity_id=str(meeting_id),
        before=_diff(
            existing.claim_business_id,
            removed.meeting_type,
            meeting_date=removed.meeting_date,
            meeting_time=removed.meeting_time,
            location=removed.location,
            notes=removed.notes,
            participants=list(removed.participants),
            is_done=removed.is_done,
        ),
        after=None,
        at=now or datetime.now(UTC),
    )
    await db.commit()

    db.expire_all()


def _diff(
    claim_business_id: str | None,
    meeting_type: MeetingType,
    *,
    meeting_date: date,
    meeting_time: time | None,
    location: str | None,
    notes: str | None,
    participants: list[str],
    is_done: bool,
) -> Mapping[str, Any]:
    """The audit diff for one meeting — the row, plus its claim.

    `claim_id` is in the diff and not only in `entity_id` because `entity_id`
    holds the *meeting's* surrogate id: without this, an audit row for a
    deleted meeting would name a primary key that no longer resolves to
    anything, and Story 8.1's purge cascade would have no way to find the
    events belonging to a claim it is purging. `injuries.py::_diff` makes the
    same argument for the same reason.

    Dates and times are ISO strings rather than Python objects because the
    column is JSONB and `json.dumps` has never heard of `datetime.date`.
    """
    return {
        "claim_id": claim_business_id,
        "meeting_type": meeting_type.value,
        "meeting_date": meeting_date.isoformat(),
        "meeting_time": None if meeting_time is None else meeting_time.isoformat(),
        "location": location,
        "notes": notes,
        "participants": participants,
        "is_done": is_done,
    }
