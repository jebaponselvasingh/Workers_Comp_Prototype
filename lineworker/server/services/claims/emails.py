"""Stakeholder emails — merge a template, log a send, list the caller's (4.3).

The third and last slice of the **diary aggregate**, which AD-12 puts under
`services/claims`: this module is the only writer of `email_log` and the only
reader of `email_template`, and it sits beside `meetings.py` and `notes.py`
rather than reaching into either.

## Send is a log, and that is the whole of it

The prototype keeps sends in a browser-lifetime object (`emailsStore`) a reload
erases and announces each one with `alert()`. Here a send is an AD-4 command
with the ladder `notes.py` established — role, then the request's validity,
then the claim reference's scope (inside the insert, so there is no window),
then the write, then the audit event in the **same transaction**, then the
commit.

**There is no SMTP client, no outbox, no delivery status and no network egress
of any kind, and none may be added here.** Real email egress is a Deferred
architecture decision with its own compliance review (`ARCHITECTURE-SPINE.md`
→ Deferred, "Email/calendar egress"). `meetings.py` says the same about
calendar invitations for the same reason. The button says "✉ Send Email
(logged)" and this module is what it means; `tests/test_emails.py` greps this
package for a mail library import so that a later story cannot quietly make the
sentence untrue.

## Merging happens here, not in the browser (AD-1)

`render_template` is a pure function over a `Mapping[str, str]`, so the six
letters are testable without a database, and it **fails loudly**: a `{{token}}`
the mapping does not carry raises rather than rendering empty, which is what
makes "no `{{…}}` survives a merge" a real assertion instead of a coincidence.
The SPA renders what `merged_template` returns and captures the handler's
edits; it never interpolates a claim field into a subject or a body.

`days_open` comes from the registered derivation (AD-10) and is never
recomputed here. `stage` and `status` merge as **prose** — see `STAGE_PROSE`.

## What is deliberately absent

**No edit and no delete, therefore no `version`, no compare-and-swap and no
409** — `notes.py`'s ruling for `diary_note`, and `email_log` is the same shape
for the same reason: nothing is ever read-modify-written, so there is no
concurrent writer for a version to arbitrate.

**No timeline event.** A logged email changes no column of `claim`, and AD-12
scopes `timeline_event` to claim-mutating commands, so
`services/claims/timeline.py::append` is not imported here — the ruling
`meetings.py` and `notes.py` both make.

**No writes to `meeting`.** `meeting_email_draft` reads one through Story 4.1's
scoped query and composes a letter from it. Convert-to-email changes nothing
about the meeting it converts.

**No template administration.** Templates are seed data (migration 0036); there
is no create, update or delete path for one and the story forbids inventing
either.

## Scope

Every function takes `CallerContext` and applies it in the repository only
(AD-7). `email_log_scope` is **sender** scope and nothing else: a logged email
is visible to the handler who composed it and to nobody else, including a
supervisor over the same book. Employer scope still gates *accepting* a claim
reference on the write — `insert_email_log` resolves it through
`employer_scope` inside the statement — and it gates the merge, which reads the
claim through `select_merge_source`.

## AD-11

`subject`, `body` and `recipients` are PHI-class: a merged letter carries the
worker's name, their date of injury and their ICD-10 code. Nothing here logs a
value, and no refusal echoes one. **The audit diff omits `body`** — see
`_diff`, which is the one place this module diverges from `notes.py` and says
why.
"""

import base64
import binascii
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models import EmailTemplate
from data.models.enums import (
    ClaimStatus,
    EmailPriority,
    MeetingParticipant,
    MeetingType,
    Stage,
    UserRole,
)
from data.repositories import claims as claim_repo
from data.repositories import identity as identity_repo
from rules.parameters import thresholds_for
from services import audit, derivations
from services.claims.edit import WHITESPACE_KEPT, EditNotPermitted, InvalidPatch
from services.claims.meetings import MeetingNotVisible, MeetingView, get_meeting
from services.derivations import utc_today

SEND_ACTION: Final[str] = "send_email"
ENTITY: Final[str] = "email_log"

#: The longest a subject and a body may be. Two different caps because the two
#: fields are different kinds of thing: a subject is one line of a list row, a
#: body is a letter. Enforced on the wire (`api/routers/diary.py` declares them
#: on its request model) *and* here, so a caller that is not the SPA — an Epic 6
#: agent tool, whose arguments are untrusted content (AD-16) — meets the same
#: bound. The body's cap is generous because the longest seeded template merges
#: to roughly 700 characters and a handler may paste a physician's report under
#: it; it exists to bound the column, not to edit the handler.
MAX_SUBJECT_LENGTH: Final[int] = 200
MAX_BODY_LENGTH: Final[int] = 10_000

#: Field names as the refusal messages spell them, so a 422 names the control
#: the handler is looking at rather than a column.
SUBJECT_FIELD: Final[str] = "subject"
BODY_FIELD: Final[str] = "body"
RECIPIENTS_FIELD: Final[str] = "recipients"
PRIORITY_FIELD: Final[str] = "priority"
#: Spelled as the wire spells it, unlike the four above — the four name controls
#: whose column and whose JSON key are the same word, and this one does not.
TEMPLATE_KEY_FIELD: Final[str] = "templateKey"

#: Page-size bounds, declared once and enforced twice — `notes.py`'s rule and
#: its reason: FastAPI refuses a `limit` outside them before this module is
#: reached, and `decode_cursor` refuses one smuggled inside a cursor.
MIN_PAGE_LIMIT: Final[int] = 1
MAX_PAGE_LIMIT: Final[int] = 200
DEFAULT_PAGE_LIMIT: Final[int] = 50

#: How stale a cursor's recorded day may be before it stops describing a list
#: worth continuing — `notes.py`'s bound, for its reason.
MAX_CURSOR_AGE: Final[timedelta] = timedelta(days=7)

#: The characters that end a line, and therefore the ones a single-line control
#: cannot carry — `meetings.py::_LINE_BREAKS`, which the subject needs for the
#: same reason a location does: the sent-log card draws `✉ {subject}` on one
#: line beside its badge.
_LINE_BREAKS: Final[frozenset[str]] = frozenset({"\n", "\r", "\v", "\f"})


class EmailTemplateNotFound(Exception):
    """No template with that key — a 404.

    Raised by the merge endpoint and by `send_email` when a body names a
    `templateKey` that is not seeded. The same answer for an absent key and a
    malformed one: there is no enumeration to protect here (the six keys are on
    the wire already), so the sameness costs nothing and keeps one branch.
    """

    def __init__(self, template_key: str) -> None:
        super().__init__(f"no email template {template_key!r}")
        self.template_key = template_key


class EmailClaimNotVisible(Exception):
    """The claim an email names is not in the caller's book — a 404.

    `DiaryNoteClaimNotVisible`'s shape and its ruling: the same "no such thing,
    for you" answer an absent claim gets, so a caller comparing two refusals
    learns nothing about which claims exist.
    """

    def __init__(self, claim_business_id: str | None) -> None:
        super().__init__(f"no claim {claim_business_id} in your caseload")
        self.claim_business_id = claim_business_id


class EmailLogNotVisible(Exception):
    """No such logged email in the caller's sent log — a 404.

    Reachable only from the post-commit re-read: the row was written and
    audited, and then the read back through the scoped query could not see it,
    which takes a scope narrowing landing between the two statements. Rare, and
    it must be *answered* rather than escaping as a 500 — the defect Story 4.1's
    review found on its own POST route, and it is sharper here because
    `email_log` has no delete path for a duplicate.
    """

    def __init__(self, email_id: int) -> None:
        super().__init__(f"no logged email {email_id} in your sent log")
        self.email_id = email_id


class InvalidCursor(ValueError):
    """A cursor that does not describe a position in the caller's sent log."""


class UnknownMergeField(ValueError):
    """A template names a `{{token}}` the merge cannot resolve.

    **Not a refusal, and never a 4xx.** Every caller-supplied value is checked
    before it reaches `render_template`; this fires only when the *seeded
    template text* names a placeholder this module does not know, which is a
    defect in reference data rather than in a request. Raising is the point:
    rendering the token as an empty string would ship a letter with a hole in it
    to a stakeholder and leave "no `{{…}}` survives a merge" true by accident.
    """


# --- the merge ----------------------------------------------------------

#: Every placeholder the six seeded templates may name, each called after the
#: column it reads. Eleven, matching the eleven interpolations in the
#: prototype's six bodies (`loadEmailTemplate`, lines 1955-1999).
#:
#: Declared as a tuple rather than inferred from a rendered mapping so that
#: `tests/test_emails.py` can assert both directions: every token the seed uses
#: is in here, and every name in here is used by at least one template. A
#: placeholder nothing renders is as much a defect as one nothing resolves.
MERGE_FIELDS: Final[tuple[str, ...]] = (
    "claim_id",
    "worker_name",
    "doi",
    "injury_type",
    "cause",
    "body_part",
    "icd",
    "days_open",
    "stage",
    "status",
    "handler_name",
)

#: The extra placeholders the meeting-confirmation draft names. Kept apart from
#: `MERGE_FIELDS` because they are not claim columns and no seeded template may
#: use them — `meeting_email_draft` builds its own mapping and its own text.
MEETING_MERGE_FIELDS: Final[tuple[str, ...]] = (
    "claim_id",
    "meeting_type",
    "date_time",
    "location",
    "claim_reference",
    "agenda",
    "handler_name",
)

_PLACEHOLDER: Final[re.Pattern[str]] = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")

#: The four lifecycle stages, spelled the way a letter has to spell them.
#:
#: **This is not the UI owning display labels, and the distinction is worth
#: stating.** That convention is about what a *component* renders. A merged
#: email body is not a component — it is a stored document composed on the
#: server by AD-1, sent to a physician and an employer's HR department, and it
#: must not read "Current Stage: treatment". So the strings live here, copied
#: **verbatim** from `web/src/features/claim-detail/labels.ts::STAGE_LABEL`,
#: with that file named as the source. `tests/test_emails.py` asserts the map
#: covers every member of the enum, so a new stage cannot silently merge as a
#: raw token.
STAGE_PROSE: Final[Mapping[Stage, str]] = {
    Stage.intake: "Intake",
    Stage.investigation: "Investigation",
    Stage.treatment: "Treatment",
    Stage.settled: "Settled",
}

#: The six claim statuses, as prose. Copied verbatim from
#: `web/src/features/claim-detail/labels.ts::CLAIM_STATUS_LABEL` — see
#: `STAGE_PROSE` on why the server carries a copy at all.
STATUS_PROSE: Final[Mapping[ClaimStatus, str]] = {
    ClaimStatus.initial: "Initial",
    ClaimStatus.ch_assessment_process: "CH Assessment Process",
    ClaimStatus.ch_approved: "CH Approved",
    ClaimStatus.denied: "Denied",
    ClaimStatus.settled: "Settled",
    ClaimStatus.settled_closed: "Settled — Closed",
}

#: The ten meeting types, as prose, for the convert-to-email draft's
#: "📅 Type:" line. Copied verbatim from
#: `web/src/features/diary/labels.ts::MEETING_TYPE_LABEL` — `STAGE_PROSE`'s
#: argument, and the prototype interpolates its own free-text `m.type` in
#: exactly this position.
MEETING_TYPE_PROSE: Final[Mapping[MeetingType, str]] = {
    MeetingType.three_point_contact_initial: "3-Point Contact — Initial",
    MeetingType.rtw_conference: "RTW Conference",
    MeetingType.ncm_care_coordination: "NCM Care Coordination",
    MeetingType.ime_preparation: "IME Preparation",
    MeetingType.settlement_discussion: "Settlement Discussion",
    MeetingType.physician_consultation: "Physician Consultation",
    MeetingType.employer_accommodation_review: "Employer Accommodation Review",
    MeetingType.litigation_prep: "Litigation Prep",
    MeetingType.claim_review_supervisor: "Claim Review — Supervisor",
    MeetingType.other: "Other",
}


def _meeting_draft_body(claim_line: str) -> str:
    """The confirmation letter, with or without its claim clause.

    One function rather than two hand-maintained copies of a fourteen-line
    letter, because the two differ by exactly one line and a second copy is how
    the wording of one of them starts drifting from the other.
    """
    return (
        "Dear Team,\n"
        "\n"
        "This is a confirmation of our scheduled meeting:\n"
        "\n"
        "📅 Type: {{meeting_type}}\n"
        "🕐 Date/Time: {{date_time}}\n"
        "📍 Location: {{location}}\n"
        f"{claim_line}"
        "\n"
        "Agenda / Notes:\n"
        "{{agenda}}\n"
        "\n"
        "Please confirm your attendance.\n"
        "\n"
        "Best regards,\n"
        "{{handler_name}}\n"
        "WC Claims Adjuster — Lineworker Manufacturing"
    )


#: The meeting-confirmation letter, ported from the prototype's
#: `quickEmailMeeting` (lines 1927-1938) with each JS interpolation replaced by
#: its placeholder. Held here rather than in `email_template` deliberately: the
#: list endpoint publishes exactly the six quick templates the composer's button
#: row offers, and a seventh row nothing renders would be a button that does not
#: exist. It still goes through `render_template` rather than an f-string, so
#: there is one merge engine and one "no `{{…}}` survives" guarantee.
#: [Source: docs/Workers_Comp_Prototype.html lines 1927-1938]
MEETING_DRAFT_SUBJECT: Final[str] = "Meeting Confirmation: {{meeting_type}} — {{claim_id}}"
MEETING_DRAFT_BODY: Final[str] = _meeting_draft_body("📋 Claim: {{claim_reference}}\n")

#: The same letter for a meeting with **no claim**, which the ERD allows
#: (`CLAIM |o--o{ MEETING`) and 4.1's scheduler writes: `claimId` is optional on
#: `POST /claims-diary/meetings`, so a handler's own diary entry — a supervisor
#: catch-up, a plant walk-through — carries no claim at all.
#:
#: **The clause is dropped rather than rendered empty**, which is the whole of
#: the divergence from the prototype. Merging an empty string into the pair
#: above produces a subject ending in a dangling em dash ("Meeting Confirmation:
#: RTW Conference — ") and a body line reading "📋 Claim: " with nothing after
#: it — a letter that looks like a merge failure to the stakeholder reading it,
#: which for a document composed by the console and stored under audit is worse
#: than saying less. Templates rather than a post-hoc `replace()` for
#: `render_template`'s reason: there is one merge engine, and both variants go
#: through it.
MEETING_DRAFT_SUBJECT_UNTAGGED: Final[str] = "Meeting Confirmation: {{meeting_type}}"
MEETING_DRAFT_BODY_UNTAGGED: Final[str] = _meeting_draft_body("")

#: The prototype's two fallbacks for a meeting with nothing recorded, kept
#: verbatim: `${m.loc||'TBD'}` and `${m.notes||'See attached claim file.'}`.
MEETING_LOCATION_FALLBACK: Final[str] = "TBD"
MEETING_AGENDA_FALLBACK: Final[str] = "See attached claim file."

#: The seven weekdays and twelve months, in English, written out rather than
#: obtained from `strftime` — see `_meeting_date_time`, which argues it. Indexed
#: by `date.weekday()` (Monday is 0) and by `month - 1`.
_WEEKDAY_NAMES: Final[tuple[str, ...]] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_MONTH_NAMES: Final[tuple[str, ...]] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def render_template(template: str, values: Mapping[str, str]) -> str:
    """Substitute every `{{token}}`, or raise. Never a silent empty string.

    A pure function, so the six letters are testable without a database — and
    **loud**, which is the property that matters: a token `values` does not
    carry raises `UnknownMergeField` rather than rendering nothing, so
    "no `{{…}}` survives a merge" is enforced rather than observed.

    Deliberately not `str.format` and not a template engine. `format` would
    choke on the literal braces a letter may legitimately contain and would
    accept attribute and index expressions (`{claim.__class__}`) out of text
    that is stored data; a `{{…}}` pattern matched by one regex over a fixed
    vocabulary can only ever do one thing. There is no recursion either: a
    substituted value carrying `{{…}}` is left alone, because the values come
    from claim columns and a worker whose name contained a placeholder must not
    be able to reach a second round of substitution.
    """

    def substitute(match: re.Match[str]) -> str:
        field = match.group(1)
        if field not in values:
            raise UnknownMergeField(
                f"the template names {{{{{field}}}}}, which this merge cannot resolve"
            )
        return values[field]

    return _PLACEHOLDER.sub(substitute, template)


# --- validation ---------------------------------------------------------


def normalise_subject(raw: object) -> str:
    """A trimmed, capped, single-line subject — or `InvalidPatch` (a 422).

    **Required**, which is AC 2's server half: the prototype answers an empty
    subject with `alert('Please enter a subject.')` and NFR-3 forbids the native
    dialog, so it is refused here with a sentence the composer renders inline at
    the field. Whitespace-only is the same refusal as empty, because the two are
    the same thing to a reader.

    Single-line for `meetings.py::_optional_text(multiline=False)`'s reason: the
    sent-log card draws `✉ {subject}` on one line beside its badge, so a newline
    in it breaks that line in two. The tab is kept (a paste artefact with no
    layout consequence) and the rest of C0 and DEL are refused because
    PostgreSQL `text` cannot hold a NUL and asyncpg raises rather than
    truncating — which reaches the request as a 500 rather than a 422.

    Messages name the field and the rule and **never echo the value** (AD-11).
    """
    if not isinstance(raw, str):
        raise InvalidPatch(f"{SUBJECT_FIELD} must be text")
    value = raw.strip()
    if not value:
        raise InvalidPatch(f"{SUBJECT_FIELD} cannot be empty")
    if any(character in _LINE_BREAKS for character in value):
        raise InvalidPatch(f"{SUBJECT_FIELD} must be a single line")
    kept = WHITESPACE_KEPT - _LINE_BREAKS
    if any(character < " " or character == "\x7f" for character in value if character not in kept):
        raise InvalidPatch(f"{SUBJECT_FIELD} contains characters that cannot be stored")
    if len(value) > MAX_SUBJECT_LENGTH:
        raise InvalidPatch(f"{SUBJECT_FIELD} must be {MAX_SUBJECT_LENGTH} characters or fewer")
    return value


def normalise_body(raw: object) -> str | None:
    """A trimmed, capped body — or `None` if empty. Prose, so it keeps its
    whitespace.

    Optional, unlike the subject: a send with a filled subject and nothing under
    it is a short note rather than an error, and the column is nullable. An
    empty string normalises to `None` so "not written" has one representation.

    `edit.WHITESPACE_KEPT` in full, `notes.py::normalise_note_text`'s ruling: a
    body is prose, a handler presses Enter, pastes a Windows-authored paragraph
    (CRLF) or an indented list out of a spreadsheet (tabs), and Word writes
    U+000B for a Shift+Enter break and U+000C for a page break. PostgreSQL
    stores all five; NUL is the constraint, and it is the only one.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise InvalidPatch(f"{BODY_FIELD} must be text")
    value = raw.strip()
    if not value:
        return None
    if any(
        character < " " or character == "\x7f"
        for character in value
        if character not in WHITESPACE_KEPT
    ):
        raise InvalidPatch(f"{BODY_FIELD} contains characters that cannot be stored")
    if len(value) > MAX_BODY_LENGTH:
        raise InvalidPatch(f"{BODY_FIELD} must be {MAX_BODY_LENGTH} characters or fewer")
    return value


def normalise_recipients(raw: object) -> tuple[MeetingParticipant, ...]:
    """De-duplicate, re-order into enum order, and require at least one.

    `meetings.py::normalise_participants` with one requirement added, and the
    addition is the story's own ruling rather than a copy drifting: the
    prototype's `sendEmail` happily logs a send with no recipient at all, and
    AC 1 asks for at least one — an email addressed to nobody is a card whose
    "To:" line is empty and a record that says nothing about who was told.

    **The stored order is the vocabulary's, not the request's**, so two
    identical sends cannot render their recipients shuffled and an unchanged set
    diffs byte-identically. Duplicates are dropped rather than refused: a client
    bug with an unambiguous meaning is not something a handler can see or fix.

    **An unknown token is refused, not dropped** — `MeetingParticipant`'s own
    promise. Pydantic already refuses one on the HTTP path, but AD-16
    anticipates a caller that is not this SPA, and a silent filter would log a
    recipient set the caller never sent. The token is **not echoed**: it is
    caller-supplied text (AD-11).

    **The shape is gated before the vocabulary is**, which is `normalise_
    priority`'s guard applied one field over and for the same reason: the
    argument arrives typed `object` because AD-16's caller is not the SPA, and
    only `MeetingParticipant("ceo")` raises `ValueError`. A non-iterable makes
    the `for` itself raise `TypeError`, and an unhashable member (`[["ceo"]]`)
    makes the *constructor* raise `TypeError` — both of which would escape this
    function as a 500 rather than the 422 the refusal below is. A bare string is
    refused with them rather than iterated: `"employee"` is a `Sequence`, and
    walking it would refuse a legitimate-looking call one character at a time.
    """
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise InvalidPatch(f"{RECIPIENTS_FIELD} must be a list of stakeholder roles")
    chosen: set[MeetingParticipant] = set()
    for value in raw:
        if not isinstance(value, str):
            raise InvalidPatch(f"{RECIPIENTS_FIELD} must be a list of stakeholder roles")
        try:
            chosen.add(MeetingParticipant(value))
        except ValueError as exc:
            raise InvalidPatch(
                f"{RECIPIENTS_FIELD} names a stakeholder role that does not exist"
            ) from exc
    if not chosen:
        raise InvalidPatch(f"{RECIPIENTS_FIELD} must name at least one stakeholder")
    return tuple(member for member in MeetingParticipant if member in chosen)


def normalise_priority(raw: object) -> EmailPriority:
    """One of the three, or `InvalidPatch`.

    Re-checked here rather than trusted from the route for AD-16's reason, and
    the token is not echoed for AD-11's.

    The `isinstance` gate is what keeps the refusal a 422: `EmailPriority(42)`
    raises `ValueError` and would be caught below, but `EmailPriority(["high"])`
    raises `TypeError` on the unhashable argument and would escape as a 500.
    """
    if not isinstance(raw, str):
        raise InvalidPatch(f"{PRIORITY_FIELD} must be text")
    try:
        return EmailPriority(raw)
    except ValueError as exc:
        raise InvalidPatch(
            f"{PRIORITY_FIELD} is not one of the priorities this console uses"
        ) from exc


def normalise_template_key(raw: object) -> str | None:
    """Text or nothing — a **type** check, never a vocabulary one.

    `None` for a free composition, and the string unchanged otherwise: whether
    the key names one of the six is decided by `select_email_template`, which
    answers 404 for a key that is absent *and* for one that is malformed. This
    function must not pre-empt that, so it neither trims nor pattern-matches.

    It exists because the key is a free input like the other four (AD-16: the
    caller is not necessarily this SPA), and an `int` or a `list` reaching the
    repository is not a 404 — it reaches asyncpg with nothing to encode it as
    and surfaces as a driver error, which is a 500 for what is a malformed
    request. `normalise_priority`'s `isinstance` gate, one field over.

    The value is **not echoed**, for `normalise_recipients`' reason (AD-11) —
    and the router's 404 no longer echoes it either.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise InvalidPatch(f"{TEMPLATE_KEY_FIELD} must be text")
    return raw


# --- the cursor ---------------------------------------------------------


@dataclass(frozen=True)
class Cursor:
    """Where a page ended, and the page size that cut it.

    **Keyset, not offset**, which is why the position is a `(sent_at, id)` pair
    rather than a row count: this list is newest-first and a handler sending an
    email mid-scroll would shift every offset after it — which is not the exotic
    case here, it is the *only* thing this surface does.

    The pair is the sort key itself, so the row it names is unambiguous. `id` is
    the second member because two sends can share a `sent_at`: the command
    stamps the instant, and two inside one clock tick tie.

    `limit` is *reused* when a request omits one, `queue.py`'s division: it
    describes the window rather than the ordering. `issued_on` is *validated*
    and never used to select anything — it exists so a cursor found in a
    bookmark a year later is refused with a sentence instead of silently
    re-paging a list that has moved on.
    """

    last_sent_at: datetime
    last_id: int
    limit: int
    issued_on: date


def encode_cursor(cursor: Cursor) -> str:
    """Base64url of a compact JSON object, unpadded — `notes.py`'s encoding.

    Opaque by intent rather than by encryption: it carries no letter text and
    nothing a caller could use to widen their scope (scope is never in the
    request — AD-7). What the encoding buys is that clients treat it as a token
    to hand back rather than a key to increment.
    """
    payload = json.dumps(
        {
            "n": cursor.last_sent_at.isoformat(),
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
    infinite "Show more" that re-appends the same emails for ever — the failure
    `queue.py` names, and every bound below is its answer. `ArithmeticError`
    sits beside `ValueError` in the except tuple because `json` accepts the
    literal `Infinity` and `int(float("inf"))` raises `OverflowError`, which is
    not a `ValueError`.

    **A naive `sent_at` is refused.** The column is `timestamptz` and every
    value this service issues is UTC-aware; a cursor carrying a naive datetime
    would raise `TypeError` deep inside the row-value comparison instead of
    answering 400 at the boundary (`notes.py::decode_cursor`'s guard).
    """
    try:
        padded = raw + "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        cursor = Cursor(
            last_sent_at=datetime.fromisoformat(data["n"]),
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
    if cursor.last_sent_at.tzinfo is None:
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


# --- views --------------------------------------------------------------


@dataclass(frozen=True)
class EmailTemplateSummary:
    """One of the six, as the composer's button row reads it.

    **No `subject_template` and no `body_template`.** The button row renders a
    label and nothing else, and the merged text arrives from
    `merged_template` — publishing the raw template would put `{{claim_id}}` on
    the wire and invite a client to interpolate it, which is exactly what AD-1
    moves to the server.

    **No `id`.** `template_key` is unique and is the identity every surface
    uses, so the surrogate never has to leave the server (`GlossaryTerm`'s
    argument).
    """

    template_key: str
    label: str
    default_recipients: tuple[MeetingParticipant, ...]


@dataclass(frozen=True)
class MergedEmail:
    """A pre-filled composition: what the modal opens holding.

    The same shape for a template merge and for a meeting draft, deliberately —
    the SPA has one "fill the composer from the server" path rather than two.

    `claim_business_id` is echoed back because the composer's read-only claim
    reference renders it, and because the value the merge resolved is the one
    the send should carry: a browser that re-read its own selection between the
    two could compose against one claim and log against another.
    """

    claim_business_id: str | None
    subject: str
    body: str
    recipients: tuple[MeetingParticipant, ...]


@dataclass(frozen=True)
class EmailLogView:
    """One logged email as the sent-log card reads it.

    `worker_name` is on the view — unlike `DiaryNoteView`, which drops it — for
    AD-11's own rule rather than against it: the card's recipients line reads
    `To: … · {claimName}`, which is the prototype's `e.claimName`, so the name
    is a thing on screen here and a payload carries what the surface renders.

    **No `version` and no `status`.** The row is append-only, so there is no
    compare-and-swap for a version to guard, and it has no lifecycle — a logged
    email is logged, and that is the whole of it. The "Sent" badge is
    `sent_at` formatted, not a delivery state (there is no delivery).
    """

    id: int
    claim_business_id: str | None
    worker_name: str | None
    template_key: str | None
    subject: str
    body: str | None
    priority: EmailPriority
    recipients: tuple[MeetingParticipant, ...]
    sent_at: datetime


@dataclass(frozen=True)
class EmailLogPage:
    """One page of the caller's sent log, in the `{items, nextCursor, total}` shape.

    **`total` is `None` on a cursor page**, and that is this story's answer to
    the envelope question `deferred-work.md` records against `list_meetings`:
    that list recounts the whole book on every "Show more" while the SPA reads
    `total` from `pages[0]` alone, so every page but the first pays for a count
    nothing displays. Here the count is issued only when no cursor was supplied.
    The Lists convention already writes `total` as optional
    (`{items, nextCursor, total?}`), so this is the convention rather than a
    divergence from it. `list_meetings` is deliberately **not** changed — that
    is shipped, tested behaviour and belongs with whoever next has a reason to
    open it.

    On the first page `total` is the size of the caller's whole sent log rather
    than of `items`, the Lists convention's rule: it is what a count beside the
    sub-tab shows, and a number that shrank when the page did would misdescribe
    the log.
    """

    items: tuple[EmailLogView, ...]
    next_cursor: str | None
    total: int | None


def _view(row: sa.Row[Any]) -> EmailLogView:
    """One repository row → the view every surface reads."""
    log = row.EmailLog
    return EmailLogView(
        id=log.id,
        claim_business_id=row.claim_business_id,
        worker_name=row.worker_name,
        template_key=row.template_key,
        subject=log.subject,
        body=log.body,
        priority=log.priority,
        # Stored as a JSONB array of tokens, which the database hands back as
        # plain strings — the column has no enum type to decode them with, so
        # the vocabulary is re-applied here. A token the enum does not know
        # would raise, which is the right answer: it could only have been
        # written by something that is not this module (AD-12).
        recipients=tuple(MeetingParticipant(value) for value in log.recipients),
        sent_at=log.sent_at,
    )


def _template(template: EmailTemplate) -> EmailTemplateSummary:
    """One reference row → the summary the button row reads."""
    return EmailTemplateSummary(
        template_key=template.template_key,
        label=template.label,
        default_recipients=tuple(
            MeetingParticipant(value) for value in template.default_recipients
        ),
    )


# --- reads --------------------------------------------------------------


async def list_email_templates(
    db: AsyncSession, ctx: CallerContext
) -> tuple[EmailTemplateSummary, ...]:
    """The six quick templates, in the composer's button order (AC 3).

    Reference data: the same six rows for every authenticated caller, which is
    why there is no scope to apply and why `select_email_templates` says so at
    length. Authentication still applies — the router sits behind the app-level
    dependency — so "unscoped" means "the same answer for everyone signed in",
    never "public".
    """
    rows = await claim_repo.select_email_templates(db, ctx)
    return tuple(_template(row) for row in rows)


async def merged_template(
    db: AsyncSession,
    ctx: CallerContext,
    template_key: str,
    *,
    claim_business_id: str,
    as_of: date | None = None,
) -> MergedEmail:
    """One template, merged against one claim, server-side (AC 3, AD-1).

    **The claim is required**, and a template merge without one is refused
    rather than degraded. The prototype renders `${c?c.claimId:''}` and produces
    letters full of holes; here the six buttons are disabled with a stated
    reason when nothing is selected, which is the house's disabled-with-tooltip
    pattern rather than a new one. Free composition still sends, and logs with
    `claim_id` null.

    `days_open` comes from the **registered derivation** (AD-10), resolved
    through the registry against today's thresholds, never recomputed here or in
    the browser. `stage` and `status` merge as prose — see `STAGE_PROSE`.

    Raises `EmailTemplateNotFound` (404) for an unseeded key and
    `EmailClaimNotVisible` (404) for a claim outside the caller's book —
    deliberately the same answer a claim that does not exist gets.
    """
    template = await claim_repo.select_email_template(db, ctx, template_key)
    if template is None:
        raise EmailTemplateNotFound(template_key)

    row = await claim_repo.select_merge_source(db, ctx, claim_business_id)
    if row is None:
        raise EmailClaimNotVisible(claim_business_id)

    today = as_of or utc_today()
    thresholds = await thresholds_for(db, today)
    claim = row.Claim
    values: dict[str, str] = {
        "claim_id": claim.claim_id,
        "worker_name": row.worker_name,
        "doi": claim.doi.isoformat(),
        "injury_type": claim.injury_type,
        "cause": claim.cause,
        "body_part": claim.body_part,
        "icd": claim.icd,
        "days_open": str(
            derivations.days_open.for_thresholds(thresholds).of(claim.froi_date, today)
        ),
        "stage": STAGE_PROSE[claim.stage],
        "status": STATUS_PROSE[claim.status],
        # `select_merge_source` reads the signature as a scalar subquery, which
        # answers NULL rather than dropping the row if the caller's `app_user`
        # is not there — and a `None` in this mapping makes `render_template`'s
        # substitution return one, which `re.sub` refuses with a `TypeError`
        # (a 500) rather than an unsigned letter. `meeting_email_draft` already
        # narrows the same value to `""` for the same reason; the two paths
        # merge the same field and now fail the same way, which is to say not
        # at all. The session produced the id, so this is a type narrowing
        # rather than a scenario.
        "handler_name": row.handler_name or "",
    }
    return MergedEmail(
        claim_business_id=claim.claim_id,
        subject=render_template(template.subject_template, values),
        body=render_template(template.body_template, values),
        recipients=tuple(MeetingParticipant(value) for value in template.default_recipients),
    )


def _meeting_date_time(meeting_date: date, meeting_time: time | None) -> str:
    """The letter's "🕐 Date/Time" line, long-form and **in English**.

    The prototype's `toLocaleString('en-US', {weekday:'long', month:'long',
    day:'numeric', hour:'2-digit', minute:'2-digit'})`, reproduced: "Monday,
    August 17 at 10:30 AM".

    **The names are written out rather than taken from `strftime`, and that is
    the point of the two tuples above.** `%A`, `%B` and `%p` read `LC_TIME` from
    the process, so the same meeting would draft as "Montag, August 17" on a
    container whose locale image differs from the developer's — and unlike a
    formatted instant in the browser, this text is merged into a document that
    is stored, audited and read by a physician. Every formatted instant in this
    console is explicitly `en-US` on the SPA side (`labels.ts`'s formatters); an
    environment variable is not an acceptable input to a letter's wording. The
    `%p` case is sharper still: several locales render it empty, which turns
    "10:30 AM" into "10:30 " and a morning appointment into an ambiguous one.

    **An all-day meeting renders the date alone**, which is a correction rather
    than a port. The prototype builds `new Date(m.date + 'T' + m.time)`, and a
    meeting with no time makes that `…Tnull` — an Invalid Date, printed into the
    letter as the literal string "Invalid Date". The card omits the clock rather
    than printing midnight (`Meeting.meeting_time`'s note), and so does this.
    """
    day = (
        f"{_WEEKDAY_NAMES[meeting_date.weekday()]}, "
        f"{_MONTH_NAMES[meeting_date.month - 1]} {meeting_date.day}"
    )
    if meeting_time is None:
        return day
    # `hour: '2-digit'` in the prototype's option bag, so 09:30 keeps its zero;
    # midnight and noon are the two the modulo has to get right (12 AM / 12 PM).
    hour = meeting_time.hour % 12 or 12
    meridiem = "AM" if meeting_time.hour < 12 else "PM"
    return f"{day} at {hour:02d}:{meeting_time.minute:02d} {meridiem}"


async def meeting_email_draft(
    db: AsyncSession,
    ctx: CallerContext,
    meeting_id: int,
    *,
    as_of: date | None = None,
) -> MergedEmail:
    """The convert-to-email letter for one meeting (AC 5).

    The same `{subject, body, recipients, claimId}` shape a template merge
    returns, so the SPA has one path for filling the composer. It goes through
    `render_template` with a meeting-sourced mapping rather than a second string
    builder, so there is one merge engine and one "no `{{…}}` survives"
    guarantee.

    **The meeting is read through Story 4.1's scoped query and nothing is
    written to it** (AD-12). `get_meeting` raises `MeetingNotVisible` for a
    meeting that is absent, belongs to another handler, or whose claim has left
    the caller's book — one 404 for all three, which is that module's security
    property and not something this one relaxes.

    **A meeting with no claim drafts a letter that does not mention one.** The
    ERD's `CLAIM |o--o{ MEETING` and 4.1's optional `claimId` both allow it, and
    the clause is dropped rather than merged empty — see
    `MEETING_DRAFT_SUBJECT_UNTAGGED`, which argues why a dangling em dash and a
    "📋 Claim: " line with nothing after it are worse than a shorter letter.

    **The recipients are the meeting's participants, unmapped.** The prototype
    pre-checks them by substring-matching participant *labels*
    (`toList.some(t => t.toLowerCase().includes(k))`, which is why "Employer HR"
    matched `employer`); here both sides are `MeetingParticipant` members, so
    the mapping is the identity and the fragile match disappears. That is the
    whole reason Story 4.3 reuses 4.1's vocabulary rather than declaring a
    second one.
    """
    meeting: MeetingView = await get_meeting(db, ctx, meeting_id, as_of=as_of)
    sender = await identity_repo.get_persona(db, ctx.user_id)
    # The session produced `ctx.user_id`, so the row is there; the fallback is a
    # type narrowing rather than a scenario. A letter is never signed with an
    # empty line.
    handler_name = sender.name if sender is not None else ""

    values = {
        "meeting_type": MEETING_TYPE_PROSE[meeting.meeting_type],
        "date_time": _meeting_date_time(meeting.meeting_date, meeting.meeting_time),
        "location": meeting.location or MEETING_LOCATION_FALLBACK,
        "agenda": meeting.notes or MEETING_AGENDA_FALLBACK,
        "handler_name": handler_name,
    }
    if meeting.claim_business_id is None:
        subject_template, body_template = (
            MEETING_DRAFT_SUBJECT_UNTAGGED,
            MEETING_DRAFT_BODY_UNTAGGED,
        )
    else:
        subject_template, body_template = MEETING_DRAFT_SUBJECT, MEETING_DRAFT_BODY
        values["claim_id"] = meeting.claim_business_id
        # `rstrip(" —")` for a claim whose worker the scoped read did not carry:
        # the reference is the claim on its own rather than "WC-1234 — ".
        values["claim_reference"] = (
            f"{meeting.claim_business_id} — {meeting.worker_name or ''}".rstrip(" —")
        )
    return MergedEmail(
        claim_business_id=meeting.claim_business_id,
        subject=render_template(subject_template, values),
        body=render_template(body_template, values),
        recipients=meeting.participants,
    )


async def list_email_logs(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    cursor: str | None = None,
    limit: int | None = None,
    as_of: date | None = None,
) -> EmailLogPage:
    """The caller's own sent log, newest first, one page at a time (AC 4).

    Scoped to the caller and nothing else (AD-7) — there is no parameter here
    that could name a user, an employer or a role, so "whose emails?" has one
    answer and it comes from the session.

    **Not filtered by the selected claim**, `list_diary_notes`' ruling: the
    prototype keys `emailsStore` by handler across their whole book, and the log
    a handler reads is theirs rather than this case file's. Each row carries its
    own claim reference.

    **The ordering is total.** `(sent_at DESC, id DESC)`, never the timestamp
    alone: two sends inside one clock tick tie, and a keyset page that ended
    inside the tie would repeat one row and drop the other.

    **`total` is counted on the first page only** — see `EmailLogPage`.

    Raises `InvalidCursor` for a cursor that does not describe a position in
    this list; the router answers 400. Never a silent page one.
    """
    today = as_of or utc_today()
    decoded = decode_cursor(cursor, today) if cursor is not None else None
    page_size = decoded.limit if decoded is not None else (limit or DEFAULT_PAGE_LIMIT)

    rows = await claim_repo.select_email_logs_page(
        db,
        ctx,
        after=None if decoded is None else (decoded.last_sent_at, decoded.last_id),
        # One more than the page, so "is there another page?" is answered by
        # what came back rather than by comparing against `total` — which would
        # be wrong the moment another send changed the count between the two
        # statements, and which is not even available on a cursor page.
        limit=page_size + 1,
    )
    total = None if decoded is not None else await claim_repo.count_email_logs(db, ctx)

    has_more = len(rows) > page_size
    items = tuple(_view(row) for row in rows[:page_size])
    next_cursor = (
        encode_cursor(
            Cursor(
                last_sent_at=items[-1].sent_at,
                last_id=items[-1].id,
                limit=page_size,
                issued_on=today,
            )
        )
        if has_more and items
        else None
    )
    return EmailLogPage(items=items, next_cursor=next_cursor, total=total)


async def get_email_log(db: AsyncSession, ctx: CallerContext, email_id: int) -> EmailLogView:
    """One logged email of the caller's, or `EmailLogNotVisible` (a 404).

    Not a route of its own — the SPA reads the list, and the prototype's
    `.email-card` opens nothing — but `send_email` ends with it, so that the 201
    body is what a subsequent list read will show rather than an echo of what
    the caller sent.
    """
    row = await claim_repo.select_email_log(db, ctx, email_id)
    if row is None:
        raise EmailLogNotVisible(email_id)
    return _view(row)


# --- the command --------------------------------------------------------


async def send_email(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    subject: object,
    body: object = None,
    priority: object = EmailPriority.normal,
    recipients: object = (),
    claim_business_id: str | None = None,
    template_key: object = None,
    now: datetime | None = None,
) -> EmailLogView:
    """Log one stakeholder email, audited (AC 4). **Send-as-log; no egress.**

    The ladder, in the house order: role, then the request's validity, then the
    template key's existence, then the claim reference's scope (inside the
    insert, so there is no window), then the write, then the audit event in the
    same transaction, then the commit.

    **Nothing leaves this process.** No SMTP, no queue, no webhook — see the
    module docstring. The row records that a handler composed a communication
    and to whom, and `sent_at` is when they pressed the button.

    **There is no version to compare.** Creating is not a compare-and-swap:
    nothing exists yet to have moved, and the claim the email references is not
    being changed. `create_diary_note` and `create_meeting` make the identical
    argument one table over each.

    **`sent_at` is stamped here, not by the database**, and it is the same
    instant the audit event carries. The column has a `now()` default so an
    INSERT that omitted it is not a trap, but a send and the record of it having
    been sent should not be able to disagree by the width of a statement.

    The free text arrives typed `object` so that untrusted input is re-validated
    here rather than trusted from the route (AD-16), and validation trims before
    it measures so that the wire's `max_length` and this module's cap measure
    the same string. `recipients` and `template_key` are typed the same way and
    for the same reason: every value a caller supplies is checked here, and a
    shape this module cannot use is a 422 rather than a driver error.

    Raises `EditNotPermitted` (403), `EmailTemplateNotFound` /
    `EmailClaimNotVisible` (404) or `InvalidPatch` (422).
    """
    if ctx.role is not UserRole.handler:
        raise EditNotPermitted("Only a claims handler can send a stakeholder email.")

    clean_subject = normalise_subject(subject)
    clean_body = normalise_body(body)
    clean_recipients = normalise_recipients(recipients)
    clean_priority = normalise_priority(priority)
    clean_template_key = normalise_template_key(template_key)

    template_id: int | None = None
    if clean_template_key is not None:
        # A real FK, so a bad key is refused rather than nulled: a log row
        # claiming to have started from a template that does not exist would
        # make the sent-log card's provenance a guess.
        template = await claim_repo.select_email_template(db, ctx, clean_template_key)
        if template is None:
            raise EmailTemplateNotFound(clean_template_key)
        template_id = template.id

    sent_at = now or datetime.now(UTC)

    email_id = await claim_repo.insert_email_log(
        db,
        ctx,
        claim_business_id=claim_business_id,
        values={
            "template_id": template_id,
            "subject": clean_subject,
            "body": clean_body,
            "priority": clean_priority,
            "recipients": [member.value for member in clean_recipients],
            "sent_at": sent_at,
        },
    )
    if email_id is None:
        # `insert_email_log` answers `None` only when it was given a claim to
        # resolve and the `INSERT … SELECT` matched none — absent, or outside
        # the caller's book, and the caller learns nothing from which. Nothing
        # was written, but the transaction has taken a snapshot, so roll it back
        # before the router answers (`create_diary_note`'s note).
        await db.rollback()
        raise EmailClaimNotVisible(claim_business_id)

    await audit.record(
        db,
        ctx,
        action=SEND_ACTION,
        entity=ENTITY,
        entity_id=str(email_id),
        before=None,
        after=_diff(
            claim_business_id,
            subject=clean_subject,
            recipients=[member.value for member in clean_recipients],
            priority=clean_priority,
            template_key=clean_template_key,
            sent_at=sent_at,
        ),
        at=sent_at,
    )
    await db.commit()

    db.expire_all()
    return await get_email_log(db, ctx, email_id)


def _diff(
    claim_business_id: str | None,
    *,
    subject: str,
    recipients: list[str],
    priority: EmailPriority,
    template_key: str | None,
    sent_at: datetime,
) -> Mapping[str, Any]:
    """The audit diff for one send — who, what about, and to whom. **Not the
    body.**

    `claim_id` is in the diff and not only in `entity_id` because `entity_id`
    holds the *email's* surrogate id: without this, Story 8.1's purge cascade
    would have no way to find the audit events belonging to a claim it is
    purging. It is the **business** id (`WC-nnnn`) rather than the surrogate,
    for the same reason — a surrogate that no longer resolves names nothing.
    `injuries.py::_diff`, `meetings.py::_diff` and `notes.py::_diff` make the
    identical argument.

    **`body` is deliberately omitted, and that is a divergence from `notes.py`
    and `meetings.py`.** Those two copy their free text into `audit_event.after`
    on the argument that the row *is* the change and a log recording less could
    not say what was written. The 4.1/4.2 follow-up review recorded the
    consequence: purging the row leaves the PHI behind in an append-only table
    whose disposition is redact-in-place. Story 8.1 owns that decision and this
    story neither pre-empts nor enlarges it.

    The reason for the omission is not squeamishness. `email_log` is append-only
    and has **no delete path**, so the after-diff is never needed to reconstruct
    a row somebody removed — which is the whole of the argument the other two
    rest on — and the body is the largest PHI blob this console stores, a full
    letter carrying a worker's name, date of injury and ICD-10 code.

    **What omitting it does not do is keep the worker out of `audit_event`, and
    this docstring used to imply otherwise.** Five of the six seeded subjects
    interpolate `{{worker_name}}` ("RTW Offer — Claim WC-20221 — Derek Hill"),
    and `claim_id` is in the diff deliberately, so a send's audit row carries
    the worker's name and their claim id whatever this function does about the
    body. The diff is a PHI-bearing record and Story 8.1's redaction has to
    treat it as one. What the omission buys is proportionality: the identifying
    line is one already held on `claim` and `employee`, where the purge can
    reach it, while the letter is prose that exists nowhere else and would be
    left behind in an append-only table by a purge that only deletes
    `email_log`. `subject` is carried because without it the event says a
    communication went out and nothing about which one — which is the difference
    between an audit trail and a counter. Recorded in `deferred-work.md` so 8.1
    harmonises all three rather than discovering the inconsistency.

    `sent_at` is an ISO string and `priority` its token because the column is
    JSONB and `json.dumps` has heard of neither a `datetime` nor a `StrEnum`
    member's identity.
    """
    return {
        "claim_id": claim_business_id,
        "subject": subject,
        "recipients": recipients,
        "priority": priority.value,
        "template_key": template_key,
        "sent_at": sent_at.isoformat(),
    }


__all__ = [
    "BODY_FIELD",
    "DEFAULT_PAGE_LIMIT",
    "ENTITY",
    "MAX_BODY_LENGTH",
    "MAX_CURSOR_AGE",
    "MAX_PAGE_LIMIT",
    "MAX_SUBJECT_LENGTH",
    "MEETING_AGENDA_FALLBACK",
    "MEETING_DRAFT_BODY",
    "MEETING_DRAFT_BODY_UNTAGGED",
    "MEETING_DRAFT_SUBJECT",
    "MEETING_DRAFT_SUBJECT_UNTAGGED",
    "MEETING_LOCATION_FALLBACK",
    "MEETING_MERGE_FIELDS",
    "MEETING_TYPE_PROSE",
    "MERGE_FIELDS",
    "MIN_PAGE_LIMIT",
    "PRIORITY_FIELD",
    "RECIPIENTS_FIELD",
    "SEND_ACTION",
    "STAGE_PROSE",
    "STATUS_PROSE",
    "SUBJECT_FIELD",
    "TEMPLATE_KEY_FIELD",
    "Cursor",
    "EmailClaimNotVisible",
    "EmailLogNotVisible",
    "EmailLogPage",
    "EmailLogView",
    "EmailTemplateNotFound",
    "EmailTemplateSummary",
    "InvalidCursor",
    "MergedEmail",
    "MeetingNotVisible",
    "UnknownMergeField",
    "decode_cursor",
    "encode_cursor",
    "get_email_log",
    "list_email_logs",
    "list_email_templates",
    "meeting_email_draft",
    "merged_template",
    "normalise_body",
    "normalise_priority",
    "normalise_recipients",
    "normalise_subject",
    "normalise_template_key",
    "render_template",
    "send_email",
]
