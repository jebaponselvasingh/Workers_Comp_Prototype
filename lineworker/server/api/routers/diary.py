"""The diary aggregate's routes — meetings, notes, emails and their templates.

A router of its own rather than eleven more paths on `api/routers/claims.py`,
which is 2622 lines and is the case file's. The split is by *aggregate*: these
endpoints read and write `meeting` (Story 4.1), `diary_note` (4.2) and
`email_log`/`email_template` (4.3), none of which is part of a claim's read
model. A handler's diary is theirs, not the claim's — which is also why the
prefix is `/claims-diary` rather than `/claims/{id}/meetings`: the lists are
scoped to the *caller*, and a path that nested one under a claim would publish
a resource whose contents do not belong to that claim.

Thin by AD-1: each route validates a body or a few query parameters, calls one
command, and maps its refusals onto statuses. Every number, every sort, the
upcoming/done status and **every merged letter** were decided in
`services/claims/{meetings,notes,emails}.py` and `services/derivations/`. In
particular no template text and no claim field is interpolated here — the merge
endpoints hand back what the service composed.

Thin by AD-7 in the way `/claims/queue` is — **there is no scope-shaped
parameter here.** Not a rejected one: an absent one. Nothing on any of these
routes can name a user, an employer or a role, so "whose diary?" has exactly
one answer and it comes from the session cookie.

**Nothing on this router sends anything anywhere.** `POST /emails` writes a row
and `POST /meetings` writes a row; neither produces an email, a calendar invite
or any other network egress — that is a Deferred architecture decision with its
own compliance review, and `services/claims/emails.py` states it at length.
"""

from datetime import date, datetime, time
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Response, status
from pydantic import AfterValidator, BeforeValidator, ConfigDict, Field

from api.deps import CallerContextDep, DbDep
from api.errors import PROBLEM_CONTENT_TYPE, ProblemDocument, ProblemException
from api.routers.auth import UNAUTHENTICATED_RESPONSE
from api.routers.claims import BAD_CURSOR_RESPONSE, CLAIM_ID_PATTERN, FORBIDDEN_RESPONSE
from api.schemas import ApiModel
from data.models.enums import EmailPriority, MeetingParticipant, MeetingType
from services.claims.edit import EditNotPermitted, InvalidPatch
from services.claims.emails import (
    MAX_BODY_LENGTH,
    MAX_SUBJECT_LENGTH,
    EmailClaimNotVisible,
    EmailLogNotVisible,
    EmailLogPage,
    EmailLogView,
    EmailTemplateNotFound,
    EmailTemplateSummary,
    MergedEmail,
    list_email_logs,
    list_email_templates,
    meeting_email_draft,
    merged_template,
    send_email,
)
from services.claims.emails import (
    MAX_PAGE_LIMIT as EMAIL_MAX_PAGE_LIMIT,
)
from services.claims.emails import (
    MIN_PAGE_LIMIT as EMAIL_MIN_PAGE_LIMIT,
)

# Aliased for the reason the notes list's bounds are: each list declares its own
# page-size window and its own cursor failure, and importing either unqualified
# would silently give one table the other's.
from services.claims.emails import (
    InvalidCursor as EmailInvalidCursor,
)
from services.claims.meetings import (
    MAX_LOCATION_LENGTH,
    MAX_NOTES_LENGTH,
    MAX_PAGE_LIMIT,
    MIN_PAGE_LIMIT,
    InvalidCursor,
    MeetingClaimNotVisible,
    MeetingNotVisible,
    MeetingPage,
    MeetingView,
    StaleMeeting,
    check_viewer_day,
    complete_meeting,
    create_meeting,
    delete_meeting,
    list_meetings,
)
from services.claims.notes import (
    MAX_NOTE_LENGTH,
    DiaryNoteClaimNotVisible,
    DiaryNoteNotVisible,
    DiaryNotePage,
    DiaryNoteView,
    create_diary_note,
    list_diary_notes,
)
from services.claims.notes import (
    MAX_PAGE_LIMIT as NOTE_MAX_PAGE_LIMIT,
)
from services.claims.notes import (
    MIN_PAGE_LIMIT as NOTE_MIN_PAGE_LIMIT,
)

# Aliased because the two lists declare their own bounds and their own cursor
# failure, and importing either unqualified would silently give one aggregate
# the other's. They happen to hold the same numbers today; that is a
# coincidence of two modules copying one precedent, not a shared constant.
from services.claims.notes import (
    InvalidCursor as NoteInvalidCursor,
)
from services.derivations import MeetingStatus

router = APIRouter(prefix="/claims-diary", tags=["diary"])


class MeetingResponse(ApiModel):
    """One meeting, as every surface reads it.

    **`status` is on the wire and `meetingDate` is not enough to reproduce
    it** — that is AD-10 stated in a payload. The prototype computes
    `m.date >= today && !m.done` inside the component that draws the card; here
    the server answers, against one `as_of` for the whole page, and the SPA
    renders what it was sent. Story 4.2's today's-meetings summary reads the
    same field rather than writing a second comparison.

    `claimId` and `workerName` travel together and are both nullable, because
    `claim_id` is (the ERD's `CLAIM |o--o{ MEETING`). The card renders
    `WC-nnnn — Worker Name`, which is why the name is here rather than fetched:
    a browser assembling that reference from a second request would be showing
    a claim this list did not scope.

    `version` is what the ✓ and the ✕ compare-and-swap on, published for
    `ScheduleWeekResponse.version`'s reason — a control that swaps must be
    holding the number it will send, or the handler's first click 409s against
    a payload they never saw.
    """

    id: int
    claim_id: str | None = Field(description="The linked claim's `WC-nnnn`, or null.")
    worker_name: str | None = Field(description="The linked claim's injured worker, or null.")
    meeting_type: MeetingType
    meeting_date: date = Field(description="ISO calendar date, `YYYY-MM-DD`.")
    meeting_time: time | None = Field(description="ISO wall-clock time, or null for all day.")
    location: str | None
    notes: str | None
    participants: list[MeetingParticipant]
    is_done: bool
    version: int
    created_at: datetime
    status: MeetingStatus = Field(
        description=(
            "Server-derived. `upcoming` while the date is on or after today "
            "and nobody has marked it complete, `done` otherwise. Do not "
            "recompute it from `meetingDate`."
        ),
    )


class MeetingListResponse(ApiModel):
    """The list envelope the Lists convention fixes: `{items, nextCursor, total}`.

    `total` is the size of the list the caller asked for, not of `items` — the
    number a count beside the sub-tab shows, and, when `day` is set, the number
    in "📅 Today's Meetings (N)". A total that shrank when the page did would
    misdescribe the list, which is `StageGroupResponse`'s argument.

    **It is null on a cursor page** (Story 9.8), which is the Lists convention's
    optional member rather than a divergence from it: the SPA reads the count
    from the first page and keeps it, so recounting the whole diary on every
    "Show more" bought a number nothing rendered. `EmailLogListResponse` shipped
    this first and named this endpoint as the one still to do it.

    `upcomingCount` is the extra member Story 4.2 added. It is deliberately *not*
    affected by `day`, and — unlike `total` — it is present on **every** page:
    the greeting that renders it sits beside a list the reader is paging, so a
    value that vanished on page two would empty a sentence mid-scroll. See its
    field description.
    """

    items: list[MeetingResponse]
    next_cursor: str | None = None
    total: int | None = Field(
        default=None,
        description=(
            "The size of the list the caller asked for — **present on the "
            "first page only**, null on any page fetched with a `cursor`. Read "
            "it from the first page and keep it; do not count `items`. When "
            "`day` is set it describes that day's list."
        ),
    )
    upcoming_count: int = Field(
        description=(
            "Server-derived. How many of the caller's meetings are still "
            "ahead — the whole book, **independent of `day`**, judged by the "
            "same registered rule that decides each item's `status`. The "
            "greeting reads it directly; do not count `items` to reproduce it."
        ),
    )


def _meeting(view: MeetingView) -> MeetingResponse:
    """One view → one wire object.

    Written out rather than `model_validate(view)` even though `ApiModel` sets
    `from_attributes`: two field names differ deliberately (`claimBusinessId`
    is the repository's word for what the wire calls `claimId`), and an
    attribute mapping that silently dropped one would leave the card rendering
    a meeting with no claim reference.
    """
    return MeetingResponse(
        id=view.id,
        claim_id=view.claim_business_id,
        worker_name=view.worker_name,
        meeting_type=view.meeting_type,
        meeting_date=view.meeting_date,
        meeting_time=view.meeting_time,
        location=view.location,
        notes=view.notes,
        participants=list(view.participants),
        is_done=view.is_done,
        version=view.version,
        created_at=view.created_at,
        status=view.status,
    )


def _page(page: MeetingPage) -> MeetingListResponse:
    return MeetingListResponse(
        items=[_meeting(view) for view in page.items],
        next_cursor=page.next_cursor,
        total=page.total,
        upcoming_count=page.upcoming_count,
    )


class MeetingConflictProblemDocument(ProblemDocument):
    """The 409 body — a problem document carrying the fresh meeting.

    `ConflictProblemDocument`'s shape with this aggregate's entity attached.
    The member is `meeting` rather than `claim` because that is what moved, and
    it is the whole row rather than a status because the SPA installs it and
    re-renders the card from one object — the same thing the 200 path does, so
    a conflict is not a second code path (AD-9).
    """

    meeting: MeetingResponse


def _meeting_conflict_schema() -> dict[str, Any]:
    """`MeetingConflictProblemDocument`'s schema, pointed at the components
    section — `_conflict_schema`'s fix, for the same reason and with the same
    consequence if it is skipped (dangling `$defs` references and a generated
    client that will not build)."""
    schema = MeetingConflictProblemDocument.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )
    schema.pop("$defs", None)
    return schema


MEETING_CONFLICT_RESPONSE: dict[int | str, dict[str, object]] = {
    409: {
        "description": (
            "The meeting has changed since the caller read it — completed or "
            "re-dated by another session. The body is an RFC 9457 problem "
            "document carrying the fresh entity under `meeting`. Re-read and "
            "redo; nothing is merged server-side."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": _meeting_conflict_schema()}},
    }
}

MEETING_UNPROCESSABLE_RESPONSE: dict[int | str, dict[str, object]] = {
    422: {
        "description": (
            "The request cannot be applied — free text carrying characters the "
            "column cannot store, which is the one refusal that reaches this "
            "route as `/problems/invalid-patch`. Free text *longer* than the "
            "field allows is caught a layer earlier: `location` and `notes` "
            "declare `maxLength`, so an over-long value is refused by the "
            "schema with `/problems/validation-error`, exactly as a body "
            "missing `meetingDate` or naming an unknown meeting type or "
            "participant is. The service enforces both caps regardless, for a "
            "caller that is not this schema (RFC 9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}

MEETING_NOT_FOUND_RESPONSE: dict[int | str, dict[str, object]] = {
    404: {
        "description": (
            "No such meeting in the caller's diary, or no such claim in their "
            "caseload. Deliberately the same answer for a row that does not "
            "exist and one that belongs to somebody else (RFC 9457 problem "
            "document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}

MEETING_CREATE_NOT_FOUND_RESPONSE: dict[int | str, dict[str, object]] = {
    404: {
        "description": (
            "Either of two things, told apart by `type`. "
            "`/problems/meeting-claim-not-found` — no such claim in the "
            "caller's caseload, deliberately the same answer for a claim that "
            "does not exist and one that belongs to somebody else; **nothing "
            "was written**. `/problems/meeting-not-readable` — the meeting "
            "*was* written and audited and then could not be read back under "
            "the caller's scope; it exists, and re-sending it would create a "
            "second row and a second audit event (RFC 9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}

CLOCK_UNPROCESSABLE_RESPONSE: dict[int | str, dict[str, object]] = {
    422: {
        "description": (
            "A caller-supplied viewer clock names a calendar date more than a "
            "day from the server's own, which is further than any timezone can "
            "explain. `asOf` answers `/problems/validation-error` (the schema "
            "refuses it, naming the parameter in `errors[].loc`); a `day` sent "
            "*without* `asOf` — where it is the horizon rather than only a "
            "filter — answers `/problems/invalid-patch` naming `day`. Both "
            "decide the `status` and `upcomingCount` on the response since "
            "Story 4.2, so an unbounded value is a request for a horizon "
            "rather than for a page (RFC 9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}


def _viewer_day(value: date | None) -> date | None:
    """Bound `asOf` where it arrives from a client — see `check_viewer_day`.

    An `AfterValidator` rather than a check inside the handler, so the refusal
    is FastAPI's own `/problems/validation-error` naming `asOf` in `loc` —
    which is what every other out-of-range parameter on this router answers,
    and what a generated client already knows how to read. The service's own
    `as_of` stays unbounded: it is the seam a test or an Epic 6 tool resolves a
    day through, and every calendar-dependent service in this codebase takes
    one. What must be bounded is the value a *browser* sends, because since
    Story 4.2 it decides the horizon `status` and `upcomingCount` are judged
    at, and an unbounded one answers that the caller's whole book is ahead.
    """
    check_viewer_day("asOf", value)
    return value


AS_OF_QUERY = Annotated[
    date | None,
    AfterValidator(_viewer_day),
    Query(
        alias="asOf",
        description=(
            "The day this response's `status` and `upcomingCount` are judged "
            "against, `YYYY-MM-DD`. The **viewer's local** day: a server with "
            "no timezone for the reader cannot resolve 'today'. It narrows "
            "nothing — it is the horizon only. Omit it and `day` answers; omit "
            "both and the server's own date does, which is what left the "
            "unfiltered read disagreeing with the day-filtered one about the "
            "same meeting. Bounded to within a day of the server's date."
        ),
        examples=["2026-08-17"],
    ),
]


class NewMeetingRequest(ApiModel):
    """The scheduler modal's body — the ten types, six participants and a date.

    `extra="forbid"` for `ClaimFieldPatch`'s reason: an unknown key is a 422
    from the contract rather than a value silently dropped on the way to a
    command.

    **`meetingDate` is required and everything else about the meeting is not.**
    That is AC 2's server half: a body without it is refused by FastAPI's own
    body validation with `/problems/validation-error` naming the field, and the
    SPA renders that inline at the date input rather than in a native dialog.

    **`claimId` is optional on the wire and always sent by this SPA.** The
    modal opens from a selected claim and shows it read-only, so a caller
    cannot retarget one; the field admits null because the column does (a
    touchpoint that is genuinely not about one file), not because the modal has
    a state in which it is unset.
    """

    model_config = ApiModel.model_config | ConfigDict(extra="forbid")

    claim_id: str | None = Field(
        default=None,
        pattern=CLAIM_ID_PATTERN,
        description="The claim to link, `WC-nnnn`. Must be in the caller's caseload.",
        examples=["WC-20017"],
    )
    meeting_type: MeetingType = Field(description="One of the ten scheduler types.")
    meeting_date: date = Field(
        description="ISO calendar date, `YYYY-MM-DD`. Required — the one field AC 2 blocks on.",
        examples=["2026-08-17"],
    )
    meeting_time: time | None = Field(
        default=None,
        description="ISO wall-clock time, `HH:MM`. Null for an all-day touchpoint.",
        examples=["10:00"],
    )
    location: str | None = Field(
        default=None,
        max_length=MAX_LOCATION_LENGTH,
        description="Room, plant or meeting link. Free text.",
    )
    notes: str | None = Field(
        default=None,
        max_length=MAX_NOTES_LENGTH,
        description="Agenda and topics. Free text.",
    )
    participants: list[MeetingParticipant] = Field(
        default_factory=list,
        description=(
            "The stakeholder roles attending. Stored in the vocabulary's own "
            "order, de-duplicated — the request's order is not preserved."
        ),
    )


class MeetingCompletion(ApiModel):
    """The ✓ body: the version the card was rendered at, and nothing else.

    No `isDone` field, deliberately, for the reason the payment approval is a
    POST rather than a `PATCH {status: …}`: the client is not proposing a
    value, it is requesting the one transition this command offers, and a body
    that could carry `false` would publish an un-complete nothing implements.
    """

    model_config = ApiModel.model_config | ConfigDict(extra="forbid")

    expected_version: int = Field(
        ge=1,
        description=(
            "The **meeting's** `version`. The write is compare-and-swapped on "
            "it and additionally guarded on the meeting not already being "
            "done; either mismatch answers 409 with the fresh meeting."
        ),
    )


MEETING_ID_PATH = Annotated[
    int,
    Path(ge=1, description="The meeting's `id`, published on the list.", examples=[42]),
]


@router.get(
    "/meetings",
    response_model=MeetingListResponse,
    summary="The session persona's scheduled meetings, oldest first",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **BAD_CURSOR_RESPONSE,
        **CLOCK_UNPROCESSABLE_RESPONSE,
    },
)
async def meetings(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    cursor: Annotated[
        str | None,
        Query(description="An opaque `nextCursor` from a previous response."),
    ] = None,
    limit: Annotated[
        int | None,
        Query(
            ge=MIN_PAGE_LIMIT,
            le=MAX_PAGE_LIMIT,
            description="Page size. Reused from the cursor when one is supplied.",
        ),
    ] = None,
    day: Annotated[
        date | None,
        Query(
            description=(
                "Narrow to one calendar date, `YYYY-MM-DD`. The **viewer's "
                "local** day: a server with no timezone for the reader cannot "
                "resolve 'today', so the browser sends it, exactly as `asOf` "
                "is. It filters `items` and `total`, and — when `asOf` is "
                "absent — it is also the day every `status` on the response "
                "and `upcomingCount` itself are judged against. So it does not "
                "narrow the **membership** of `upcomingCount`, which stays "
                "whole-book, but it does set the **horizon** that count is "
                "taken at; those are different things and an earlier version "
                "of this description ran them together. Not a scope parameter "
                "— it can only narrow what the caller's session already "
                "permits — and it is bounded to within a day of the server's "
                "own date. A `cursor` issued with a different `day` (or with "
                "none) is refused as `/problems/invalid-cursor`."
            ),
            examples=["2026-08-17"],
        ),
    ] = None,
    as_of: AS_OF_QUERY = None,
) -> MeetingListResponse:
    """The caller's diary, optionally one day of it. You cannot re-scope it."""
    # Specific to one persona's diary, so it must never be served to another
    # from a cache upstream — the same reason `/me` and `/claims/queue` say so.
    response.headers["Cache-Control"] = "no-store"
    try:
        page = await list_meetings(db, ctx, cursor=cursor, limit=limit, day=day, as_of=as_of)
    except InvalidPatch as exc:
        raise _unprocessable(exc) from exc
    except InvalidCursor as exc:
        # 400 rather than 422: the cursor is syntactically a string and passed
        # validation. What failed is that it does not describe a position in
        # *this* list — a fact only the service knows. The header is re-stated
        # because raising abandons `response` (see `/claims/queue`).
        raise ProblemException(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Bad Request",
            detail=str(exc),
            type_="/problems/invalid-cursor",
            headers={"Cache-Control": "no-store"},
        ) from exc
    return _page(page)


@router.post(
    "/meetings",
    response_model=MeetingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a meeting (audited)",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **FORBIDDEN_RESPONSE,
        **MEETING_CREATE_NOT_FOUND_RESPONSE,
        **MEETING_UNPROCESSABLE_RESPONSE,
    },
)
async def schedule_meeting(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    body: NewMeetingRequest,
    as_of: AS_OF_QUERY = None,
) -> MeetingResponse:
    """Write one meeting row, and answer with it.

    **201 with no `Location` header.** A row is created, so 201 is the honest
    status; there is deliberately no `GET /claims-diary/meetings/{id}` to point
    at, because the diary is read as a list and a second way to read one row
    would be a second place its shape is decided (`add_injury`'s call).

    The body is the created entity rather than an acknowledgement: it carries
    the `id` and `version` the ✓ and ✕ will compare-and-swap on, and the
    server-derived `status` the card is drawn from.

    **No calendar invitation is sent, and none is queued.** Scheduling here is
    a log of intent — see `services/claims/meetings.py` on why egress is a
    Deferred decision rather than an omission.

    `asOf` is the viewer's local day and decides only the `status` the body
    comes back carrying — see `AS_OF_QUERY`.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        view = await create_meeting(
            db,
            ctx,
            claim_business_id=body.claim_id,
            meeting_type=body.meeting_type,
            meeting_date=body.meeting_date,
            meeting_time=body.meeting_time,
            location=body.location,
            notes=body.notes,
            participants=body.participants,
            as_of=as_of,
        )
    except EditNotPermitted as exc:
        raise _forbidden(exc) from exc
    except MeetingClaimNotVisible as exc:
        raise _claim_not_found(exc.claim_business_id) from exc
    except MeetingNotVisible as exc:
        # The row was written and audited, and then the post-commit re-read
        # (`create_meeting` ends with `get_meeting`) could not see it — which
        # takes a scope narrowing landing between the two statements. Its own
        # problem type and its own sentence, which is a correction: mapping it
        # onto `_meeting_not_found` answered "No meeting 47 in your diary."
        # *inside a create dialog*, about a row that had already committed and
        # already emitted its audit event. A handler told that re-submits, and
        # the second attempt is a duplicate meeting with a second audit event
        # behind it. `_note_not_readable` solved exactly this one table over;
        # this is that shape back-ported.
        raise _meeting_not_readable(exc.meeting_id) from exc
    except InvalidPatch as exc:
        raise _unprocessable(exc) from exc
    return _meeting(view)


@router.patch(
    "/meetings/{meeting_id}",
    response_model=MeetingResponse,
    summary="Mark a meeting done (audited, versioned)",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **FORBIDDEN_RESPONSE,
        **MEETING_NOT_FOUND_RESPONSE,
        **MEETING_CONFLICT_RESPONSE,
    },
)
async def complete_meeting_route(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    body: MeetingCompletion,
    meeting_id: MEETING_ID_PATH,
) -> MeetingResponse:
    """Set one meeting's `isDone`, and answer with the fresh row.

    The success body carries the new `version` the next command compares
    against and the recomputed `status` — a completed meeting is `done`
    whatever its date, so the card's styling follows from one field the server
    owns rather than from two the browser would have to combine.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        view = await complete_meeting(db, ctx, meeting_id, expected_version=body.expected_version)
    except EditNotPermitted as exc:
        raise _forbidden(exc) from exc
    except MeetingNotVisible as exc:
        raise _meeting_not_found(meeting_id) from exc
    except StaleMeeting as exc:
        raise _conflict(exc) from exc
    return _meeting(view)


@router.delete(
    "/meetings/{meeting_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a meeting (audited, versioned)",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **FORBIDDEN_RESPONSE,
        **MEETING_NOT_FOUND_RESPONSE,
        **MEETING_CONFLICT_RESPONSE,
    },
)
async def delete_meeting_route(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    meeting_id: MEETING_ID_PATH,
    expected_version: Annotated[
        int,
        Query(
            # Aliased, because the camelCase boundary is the whole contract's
            # and a query parameter is no less on the wire than a body field —
            # `remove_injury`'s note.
            alias="expectedVersion",
            ge=1,
            description="The **meeting's** `version`; the delete swaps on it.",
        ),
    ],
) -> Response:
    """Remove one meeting, and answer 204.

    **The version travels in the query string** because a DELETE body is
    permitted but widely dropped by proxies and generated clients, and a
    compare-and-swap whose guard can be silently discarded is not a guard.

    **204 rather than 200 with an entity**, which is where this departs from
    `remove_injury`: that route answers with the case file because the diagram
    it belongs to is the thing the caller is looking at. A meeting has no
    parent payload — the list is its own read model under its own key — so
    there is nothing to hand back, and the SPA invalidates the list.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        await delete_meeting(db, ctx, meeting_id, expected_version=expected_version)
    except EditNotPermitted as exc:
        raise _forbidden(exc) from exc
    except MeetingNotVisible as exc:
        raise _meeting_not_found(meeting_id) from exc
    except StaleMeeting as exc:
        raise _conflict(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})


# --- Story 4.2: diary notes ---------------------------------------------


class DiaryNoteResponse(ApiModel):
    """One diary note, as the list and the 201 both read it.

    **No `version` and no `status`**, unlike `MeetingResponse`, and both
    absences are the contract rather than an oversight: a note is create-only,
    so there is no compare-and-swap for a version to guard, and it has no
    lifecycle, so there is nothing for a derivation to answer about it. A
    client that found a `version` here would reasonably build an edit control.

    `claimId` is nullable because `claim_id` is (the ERD's
    `CLAIM |o--o{ DIARY_NOTE`), and the card renders it as `📎 WC-nnnn`.

    **There is no `workerName`, and `MeetingResponse` has one.** The meeting
    card renders `WC-nnnn — Worker Name`, so the name is the thing on screen
    there; a note card renders the claim reference alone and never the name. It
    was shipped on every row anyway — the injured worker's name, on every entry
    of every handler's diary, for no consumer — which is PHI on the wire that
    AD-11's "carry what the surface renders" rule does not permit. Removed
    rather than left as a field a later client might start reading.

    `notedAt` is a UTC instant. The `{date} · {time}` header is the browser's
    formatting of it, in the reader's own locale.
    """

    id: int
    claim_id: str | None = Field(description="The tagged claim's `WC-nnnn`, or null.")
    note_text: str = Field(description="The handler's note. PHI — never logged.")
    noted_at: datetime = Field(description="When the note was written, UTC. The server's clock.")


class DiaryNoteListResponse(ApiModel):
    """The list envelope the Lists convention fixes: `{items, nextCursor, total}`.

    Ordered **newest first** — `notedAt` descending, `id` descending — which is
    the one thing about this payload a client must not reproduce for itself.
    `total` is the size of the caller's whole diary, not of `items`.
    """

    items: list[DiaryNoteResponse]
    next_cursor: str | None = None
    total: int


def _note(view: DiaryNoteView) -> DiaryNoteResponse:
    """One view → one wire object.

    Written out rather than `model_validate(view)` for `_meeting`'s reason: the
    repository's `claim_business_id` is the wire's `claimId`, and an attribute
    mapping that silently dropped it would leave every note rendering untagged.
    """
    return DiaryNoteResponse(
        id=view.id,
        claim_id=view.claim_business_id,
        note_text=view.note_text,
        noted_at=view.noted_at,
    )


def _note_page(page: DiaryNotePage) -> DiaryNoteListResponse:
    return DiaryNoteListResponse(
        items=[_note(view) for view in page.items],
        next_cursor=page.next_cursor,
        total=page.total,
    )


NOTE_UNPROCESSABLE_RESPONSE: dict[int | str, dict[str, object]] = {
    422: {
        "description": (
            "The note cannot be stored — empty or whitespace-only text, or "
            "text carrying characters the column cannot hold. Both reach this "
            "route as `/problems/invalid-patch`. Text *longer* than the field "
            "allows is caught a layer earlier: `noteText` declares "
            "`maxLength`, so an over-long value is refused by the schema with "
            "`/problems/validation-error`. The command enforces both bounds "
            "regardless, for a caller that is not this schema (RFC 9457 "
            "problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}

NOTE_NOT_FOUND_RESPONSE: dict[int | str, dict[str, object]] = {
    404: {
        "description": (
            "Either of two things, told apart by `type`. "
            "`/problems/note-claim-not-found` — no such claim in the caller's "
            "caseload, deliberately the same answer for a claim that does not "
            "exist and one that belongs to somebody else; **nothing was "
            "written**. `/problems/note-not-readable` — the note *was* written "
            "and audited and then could not be read back under the caller's "
            "scope; it exists, and re-sending it would duplicate a row in an "
            "append-only table (RFC 9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}


def _trimmed(value: object) -> object:
    """Strip a string, and pass anything else through untouched.

    A `BeforeValidator`, so it runs before `max_length` — see
    `NewDiaryNoteRequest` on why the two bounds have to measure the same
    string. Non-strings are handed on unchanged rather than coerced: a `noteText`
    of `42` is a type error the schema should report as one, and a validator that
    stringified it would turn a client bug into a stored note.
    """
    return value.strip() if isinstance(value, str) else value


class NewDiaryNoteRequest(ApiModel):
    """The add-note input's body — a paragraph, and optionally a claim.

    `extra="forbid"` for `NewMeetingRequest`'s reason: an unknown key is a 422
    from the contract rather than a value silently dropped on the way to a
    command.

    **`noteText` is required and `claimId` is not.** The input sits at the
    bottom of the Notes sub-tab whether or not a claim is selected, and a note
    written with none is legal (the column is nullable) rather than refused.

    **The text is trimmed before it is measured**, which is the one piece of
    normalisation this schema does and it is here to keep two bounds from
    disagreeing. `max_length` counts what arrives on the wire; the command
    (`normalise_note_text`) counts what it will store, which is the *stripped*
    string. A 2000-character note ending in the newline a handler pressed is
    2001 on the wire and 2000 in the column — refused by the schema as
    `/problems/validation-error` for being over a limit it is not over. Trimming
    first makes both layers measure the same string, so the cap means one thing.
    It deliberately does **not** add a `min_length`: an empty note stays the
    command's `/problems/invalid-patch` (the I/O matrix's row), not a schema
    refusal.
    """

    model_config = ApiModel.model_config | ConfigDict(extra="forbid")

    claim_id: str | None = Field(
        default=None,
        pattern=CLAIM_ID_PATTERN,
        description="The claim to tag, `WC-nnnn`. Must be in the caller's caseload.",
        examples=["WC-20017"],
    )
    note_text: Annotated[str, BeforeValidator(_trimmed)] = Field(
        max_length=MAX_NOTE_LENGTH,
        description=(
            "The note. Required, and refused when it trims to nothing — the "
            "prototype silently ignores an empty input; here it is an inline "
            "422 at the control."
        ),
        examples=["Called the plant; light duty available from Monday."],
    )


@router.get(
    "/notes",
    response_model=DiaryNoteListResponse,
    summary="The session persona's diary notes, newest first",
    responses={**UNAUTHENTICATED_RESPONSE, **BAD_CURSOR_RESPONSE},
)
async def diary_notes(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    cursor: Annotated[
        str | None,
        Query(description="An opaque `nextCursor` from a previous response."),
    ] = None,
    limit: Annotated[
        int | None,
        Query(
            ge=NOTE_MIN_PAGE_LIMIT,
            le=NOTE_MAX_PAGE_LIMIT,
            description="Page size. Reused from the cursor when one is supplied.",
        ),
    ] = None,
) -> DiaryNoteListResponse:
    """The caller's own notes. Page them; you cannot re-scope them.

    **Not filtered by claim, and there is no parameter that could be.** The
    list is the handler's diary across their whole book — the prototype's own
    shape — and each row carries its tag. A `claimId` filter here would publish
    a per-claim read model this story does not have and the design contract
    does not ask for.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        page = await list_diary_notes(db, ctx, cursor=cursor, limit=limit)
    except NoteInvalidCursor as exc:
        # 400 rather than 422, for the meetings list's reason: the cursor is
        # syntactically a string and passed validation; what failed is that it
        # does not describe a position in *this* list.
        raise ProblemException(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Bad Request",
            detail=str(exc),
            type_="/problems/invalid-cursor",
            headers={"Cache-Control": "no-store"},
        ) from exc
    return _note_page(page)


@router.post(
    "/notes",
    response_model=DiaryNoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Write a diary note (audited)",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **FORBIDDEN_RESPONSE,
        **NOTE_NOT_FOUND_RESPONSE,
        **NOTE_UNPROCESSABLE_RESPONSE,
    },
)
async def write_diary_note(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    body: NewDiaryNoteRequest,
) -> DiaryNoteResponse:
    """Write one note row, and answer with it.

    **201 with no `Location` header**, `schedule_meeting`'s call: a row is
    created, so 201 is the honest status, and there is deliberately no
    `GET /claims-diary/notes/{id}` to point at because the diary is read as a
    list.

    The body is the created entity rather than an acknowledgement, so the SPA
    can render the new note at the top of the list from the response it already
    has — and, more to the point, so what it renders is what the *scoped read*
    returns rather than an echo of what was sent.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        view = await create_diary_note(
            db, ctx, note_text=body.note_text, claim_business_id=body.claim_id
        )
    except EditNotPermitted as exc:
        raise _forbidden(exc) from exc
    except DiaryNoteClaimNotVisible as exc:
        raise _note_claim_not_found(exc.claim_business_id) from exc
    except DiaryNoteNotVisible as exc:
        # The row was written and audited, and then the post-commit re-read
        # could not see it — a scope narrowing landing between the two
        # statements. Its **own** problem type and its own sentence, which is a
        # correction: mapping it onto `_note_claim_not_found(body.claim_id)`
        # answered "No claim None in your caseload." for an untagged note, and —
        # worse — described a write that had already committed as a failure. A
        # handler told that re-sends, and `diary_note` is append-only with no
        # edit and no delete, so the console's own refusal is what duplicates
        # the row. See `_note_not_readable`.
        raise _note_not_readable(exc.note_id) from exc
    except InvalidPatch as exc:
        raise _unprocessable(exc) from exc
    return _note(view)


def _note_claim_not_found(claim_business_id: str | None) -> ProblemException:
    """The 404 for a claim tag outside the caller's book.

    Its own `type` rather than the meetings one, because the two name different
    resources and a client mapping problem types to inline messages should not
    have to know which aggregate answered. The *wording* is the case file's, so
    a caller comparing this refusal with `GET /claims/{id}`'s learns nothing.
    """
    return ProblemException(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Not Found",
        detail=f"No claim {claim_business_id} in your caseload.",
        type_="/problems/note-claim-not-found",
        headers={"Cache-Control": "no-store"},
    )


def _note_not_readable(note_id: int) -> ProblemException:
    """The 404 for a note that was **written** and then could not be read back.

    Its own `type` and its own wording, and both matter more here than anywhere
    else in this router. The row is committed and audited by the time this is
    raised — only the scoped re-read failed — so the one thing the answer must
    not say is anything a caller would respond to by sending the note again:
    `diary_note` is append-only with no edit and no delete, so a retry is a
    duplicate nobody can remove. It is still a 404 rather than a 500 because the
    note genuinely is not in the caller's diary *now*, which is a fact about
    scope rather than a fault.
    """
    return ProblemException(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Not Found",
        detail=(
            f"Note {note_id} was saved, but it can no longer be read back from "
            "your diary — your caseload changed while it was being written. Do "
            "not write it again; reload the list."
        ),
        type_="/problems/note-not-readable",
        headers={"Cache-Control": "no-store"},
    )


def _forbidden(exc: EditNotPermitted) -> ProblemException:
    """The 403, raised before anything is looked up.

    So the answer is identical for a meeting in the caller's diary, one in
    somebody else's, and one that does not exist — the ordering
    `services/claims/edit.py` argues for. A supervisor learns nothing from it.
    """
    return ProblemException(
        status_code=status.HTTP_403_FORBIDDEN,
        title="Forbidden",
        detail=str(exc),
        type_="/problems/edit-not-permitted",
        headers={"Cache-Control": "no-store"},
    )


def _meeting_not_found(meeting_id: int) -> ProblemException:
    """The 404 both meeting commands answer — one wording, one `type`.

    Written once because the sameness *is* the security property: a caller
    comparing the PATCH's refusal with the DELETE's must not be able to learn
    from the difference that a meeting exists but is not theirs to change.
    """
    return ProblemException(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Not Found",
        detail=f"No meeting {meeting_id} in your diary.",
        type_="/problems/meeting-not-found",
        # Restated because raising abandons the injected `response`: the
        # handler builds a fresh `JSONResponse`. A 404 that depends on who is
        # asking must not be cached and replayed to somebody whose diary
        # *does* contain the meeting.
        headers={"Cache-Control": "no-store"},
    )


def _meeting_not_readable(meeting_id: int) -> ProblemException:
    """The 404 for a meeting that was **written** and then could not be read back.

    `_note_not_readable`'s shape, and it matters here for the same reason: the
    row is committed and audited by the time this is raised — only the scoped
    re-read failed — so the answer must not say anything a caller would respond
    to by sending the meeting again. `meeting` does have a delete, so a
    duplicate is recoverable in a way a duplicate note is not; what is not
    recoverable is the second `create_meeting` audit event for a decision taken
    once. It is still a 404 rather than a 500 because the meeting genuinely is
    not in the caller's diary *now*, which is a fact about scope rather than a
    fault.
    """
    return ProblemException(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Not Found",
        detail=(
            f"Meeting {meeting_id} was scheduled, but it can no longer be read "
            "back from your diary — your caseload changed while it was being "
            "written. Do not schedule it again; reload the list."
        ),
        type_="/problems/meeting-not-readable",
        headers={"Cache-Control": "no-store"},
    )


def _claim_not_found(claim_business_id: str | None) -> ProblemException:
    """The 404 for a claim link outside the caller's book.

    The case file's wording, deliberately: a caller comparing this refusal with
    `GET /claims/{id}`'s must not be able to learn that a claim exists but
    cannot be scheduled against.
    """
    return ProblemException(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Not Found",
        detail=f"No claim {claim_business_id} in your caseload.",
        type_="/problems/meeting-claim-not-found",
        headers={"Cache-Control": "no-store"},
    )


def _unprocessable(exc: InvalidPatch) -> ProblemException:
    """The 422 for free text this command cannot store.

    Not a bare `ValueError`: catching that would turn an unrelated bug deep in
    the service into a cheerful 422 that blames the caller.
    """
    return ProblemException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        title="Unprocessable Content",
        detail=str(exc),
        type_="/problems/invalid-patch",
        headers={"Cache-Control": "no-store"},
    )


def _conflict(exc: StaleMeeting) -> ProblemException:
    """The 409, carrying the fresh meeting under `meeting`."""
    return ProblemException(
        status_code=status.HTTP_409_CONFLICT,
        title="Conflict",
        detail=(
            "This meeting was changed by someone else while you were looking "
            "at it. The current version is attached."
        ),
        type_="/problems/stale-write",
        headers={"Cache-Control": "no-store"},
        extensions={
            # `mode="json"` because the extension is merged into a plain dict
            # and handed to `JSONResponse`, which encodes with `json.dumps` and
            # has never heard of `datetime.date` — the response model's own
            # serialiser is what the 200 path gets, and this path has to ask
            # for it explicitly.
            "meeting": _meeting(exc.fresh).model_dump(by_alias=True, mode="json")
        },
    )


# --- Story 4.3: stakeholder emails --------------------------------------


class EmailTemplateResponse(ApiModel):
    """One of the six quick templates, as the composer's button row reads it.

    **The template *text* is not on the wire, deliberately.** A client that
    received `subjectTemplate` and `bodyTemplate` would be one `replace()` away
    from merging in the browser, which is exactly what AD-1 moves to the server;
    the merged text arrives from `GET /email-templates/{key}/merged` instead.

    **No `id`.** `templateKey` is unique and is the identity every surface uses,
    so the surrogate never has to leave the server — `GlossaryTermResponse`'s
    argument, and it is what makes this payload keyable by construction.

    `defaultRecipients` is what the checkbox set becomes when the button is
    pressed, and it is here as well as on the merge so the SPA can render the
    six buttons before any claim is selected.
    """

    template_key: str = Field(description="Stable snake_case key, e.g. `rtw_offer`.")
    label: str = Field(description="The button's text, e.g. `RTW Offer`. UI-owned wording.")
    default_recipients: list[MeetingParticipant] = Field(
        description=(
            "The stakeholder roles this template addresses. The same six-value "
            "vocabulary a meeting's `participants` uses, so convert-to-email "
            "maps one to one."
        ),
    )


class EmailTemplateListResponse(ApiModel):
    """The six, in the composer's button order.

    **Not a paged envelope**, and that is not an oversight: this is reference
    data with a fixed cardinality of six, rendered as a row of buttons. A
    `nextCursor` here would publish a pagination contract for a list that
    cannot grow without a migration and a UI change in the same commit.
    """

    items: list[EmailTemplateResponse]


class MergedEmailResponse(ApiModel):
    """A pre-filled composition — what the modal opens holding (AD-1).

    The same shape for a template merge and for a meeting's email draft, so the
    SPA has one "fill the composer from the server" path rather than two.

    **`subject` and `body` arrive merged and contain no `{{…}}`.** The merge
    resolves every placeholder or fails; a client must not scan this text for
    tokens to substitute. Text in *square* brackets — `$[AMOUNT]`, `[RATING]%`,
    `[Please add next steps]` — is deliberate handler-fill prompt text and is
    left exactly as it is (AD-2: no financial figure is auto-filled).

    `claimId` is echoed back because the composer's read-only claim reference
    renders it, and because the value the merge resolved is the one the send
    should carry: a browser that re-read its own selection in between could
    compose against one claim and log against another.
    """

    claim_id: str | None = Field(description="The claim this text was merged against, or null.")
    subject: str = Field(description="Merged subject line. No `{{…}}` survives a merge.")
    body: str = Field(description="Merged letter body. No `{{…}}` survives a merge.")
    recipients: list[MeetingParticipant] = Field(
        description=(
            "The checkbox set to apply — exactly this set, not a union with "
            "whatever is currently ticked."
        ),
    )


class EmailLogResponse(ApiModel):
    """One logged email, as the sent-log card and the 201 both read it.

    **No `version` and no `status`**, unlike `MeetingResponse`, and both
    absences are the contract: the row is append-only, so there is no
    compare-and-swap for a version to guard, and it has no lifecycle. The "Sent"
    badge is `sentAt` formatted in the browser — **not a delivery state**. There
    is no delivery: `POST /emails` writes a row and nothing leaves the process.

    `claimId` and `workerName` travel together and are both nullable, because
    `claim_id` is (the ERD's `CLAIM |o--o{ EMAIL_LOG`). The card's recipients
    line reads `To: … · {workerName}`, which is why the name is here rather than
    fetched.

    `templateKey` is null for a free composition and names one of the six
    otherwise — the provenance of the text, not a promise that the text still
    matches the template (the handler may have edited it before sending).
    """

    id: int
    claim_id: str | None = Field(description="The referenced claim's `WC-nnnn`, or null.")
    worker_name: str | None = Field(description="The referenced claim's injured worker, or null.")
    template_key: str | None = Field(
        description="Which of the six templates this started from, or null for a free composition."
    )
    subject: str = Field(description="The subject as sent. PHI — never logged.")
    body: str | None = Field(description="The letter as sent. PHI — never logged.")
    priority: EmailPriority
    recipients: list[MeetingParticipant] = Field(
        description=(
            "Stakeholder **roles**, not addresses — there are no per-recipient "
            "addresses in this console. Stored in the vocabulary's own order, "
            "de-duplicated; the request's order is not preserved."
        ),
    )
    sent_at: datetime = Field(
        description=(
            "When the handler pressed the button, UTC. The server's clock. It "
            "records composition, not transmission."
        ),
    )


class EmailLogListResponse(ApiModel):
    """The list envelope the Lists convention fixes: `{items, nextCursor, total}`.

    Ordered **newest first** — `sentAt` descending, `id` descending — which is
    the one thing about this payload a client must not reproduce for itself.

    **`total` is null on a cursor page**, which is the Lists convention's
    optional member rather than a divergence from it: the SPA reads the count
    from the first page alone, so re-counting the whole log on every "Show more"
    would buy a number nothing renders. On the first page it is the size of the
    caller's whole sent log, not of `items`.
    """

    items: list[EmailLogResponse]
    next_cursor: str | None = None
    total: int | None = Field(
        default=None,
        description=(
            "The size of the caller's whole sent log — **present on the first "
            "page only**, null on any page fetched with a `cursor`. Read it "
            "from the first page and keep it; do not count `items`."
        ),
    )


def _email_template(view: EmailTemplateSummary) -> EmailTemplateResponse:
    return EmailTemplateResponse(
        template_key=view.template_key,
        label=view.label,
        default_recipients=list(view.default_recipients),
    )


def _merged(view: MergedEmail) -> MergedEmailResponse:
    """One merged composition → one wire object.

    Written out rather than `model_validate(view)` for `_meeting`'s reason: the
    service's `claim_business_id` is the wire's `claimId`, and an attribute
    mapping that silently dropped it would leave the composer's claim reference
    empty for a merge that had a claim.
    """
    return MergedEmailResponse(
        claim_id=view.claim_business_id,
        subject=view.subject,
        body=view.body,
        recipients=list(view.recipients),
    )


def _email(view: EmailLogView) -> EmailLogResponse:
    return EmailLogResponse(
        id=view.id,
        claim_id=view.claim_business_id,
        worker_name=view.worker_name,
        template_key=view.template_key,
        subject=view.subject,
        body=view.body,
        priority=view.priority,
        recipients=list(view.recipients),
        sent_at=view.sent_at,
    )


def _email_page(page: EmailLogPage) -> EmailLogListResponse:
    return EmailLogListResponse(
        items=[_email(view) for view in page.items],
        next_cursor=page.next_cursor,
        total=page.total,
    )


EMAIL_NOT_FOUND_RESPONSE: dict[int | str, dict[str, object]] = {
    404: {
        "description": (
            "Any of three things, told apart by `type`. "
            "`/problems/email-template-not-found` — no template with that key; "
            "the same answer for an absent key and a malformed one. "
            "`/problems/email-claim-not-found` — no such claim in the caller's "
            "caseload, deliberately the same answer for a claim that does not "
            "exist and one that belongs to somebody else; on the write path, "
            "**nothing was written**. `/problems/email-not-readable` — the row "
            "*was* written and audited and then could not be read back under "
            "the caller's scope; it exists, and re-sending it would duplicate a "
            "row in an append-only table with no delete path (RFC 9457 problem "
            "document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}

EMAIL_UNPROCESSABLE_RESPONSE: dict[int | str, dict[str, object]] = {
    422: {
        "description": (
            "The email cannot be logged — an empty or whitespace-only subject, "
            "a subject carrying a line break, no recipient selected, or text "
            "carrying characters the column cannot hold. All reach this route "
            "as `/problems/invalid-patch`, and the SPA renders each inline at "
            "the control it names. Text *longer* than the field allows is "
            "caught a layer earlier: `subject` and `body` declare `maxLength`, "
            "so an over-long value is refused by the schema with "
            "`/problems/validation-error`, exactly as an unknown recipient "
            "token or priority is. The command enforces every bound regardless, "
            "for a caller that is not this schema (RFC 9457 problem document). "
            "No refusal ever echoes the submitted value."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}


class NewEmailRequest(ApiModel):
    """The composer's body — recipients, a subject, and what it is about.

    `extra="forbid"` for `NewMeetingRequest`'s reason: an unknown key is a 422
    from the contract rather than a value silently dropped on the way to a
    command.

    **`subject` and `recipients` are required and everything else is not.**
    That is AC 2's server half twice over: a body with a blank subject or an
    empty recipient list is refused with `/problems/invalid-patch` naming the
    control, and the SPA renders it inline rather than in a native dialog.

    **`claimId` is optional**, because free composition with nothing selected is
    legal and logs with `claim_id` null (the ERD's optional edge). **The six
    templates are not**: they are claim-aware by definition, so the *merge*
    endpoint requires a claim and the composer disables the buttons with a
    stated reason when there is none.

    **`templateKey` is provenance, not a request to merge.** By the time this
    body is sent the text has already been merged and possibly edited by the
    handler; the key records which of the six it started from, and an unseeded
    one is a 404 rather than a silently nulled column.

    **The text is trimmed before it is measured**, `NewDiaryNoteRequest`'s fix:
    `max_length` counts what arrives on the wire and the command counts what it
    will store, so a full-length body ending in a newline would be refused for
    exceeding a limit it does not exceed. Trimming first makes both layers
    measure the same string.
    """

    model_config = ApiModel.model_config | ConfigDict(extra="forbid")

    claim_id: str | None = Field(
        default=None,
        pattern=CLAIM_ID_PATTERN,
        description="The claim this email is about, `WC-nnnn`. Must be in the caller's caseload.",
        examples=["WC-20017"],
    )
    template_key: str | None = Field(
        default=None,
        max_length=64,
        description="Which of the six templates the text started from, or null.",
        examples=["three_point_contact"],
    )
    subject: Annotated[str, BeforeValidator(_trimmed)] = Field(
        max_length=MAX_SUBJECT_LENGTH,
        description=(
            "The subject. Required, single line, and refused when it trims to "
            "nothing — the prototype answers an empty one with a native "
            "`alert()`; here it is an inline 422 at the control."
        ),
        examples=["3-Point Contact — WC Claim WC-20017"],
    )
    body: Annotated[str | None, BeforeValidator(_trimmed)] = Field(
        default=None,
        max_length=MAX_BODY_LENGTH,
        description="The letter. Optional; an empty one is stored as null.",
    )
    priority: EmailPriority = Field(
        default=EmailPriority.normal,
        description="Normal, High or Urgent. Defaults to the value the composer opens on.",
    )
    recipients: list[MeetingParticipant] = Field(
        description=(
            "The stakeholder roles to address. **At least one is required** — "
            "the prototype logs a send with none, and an email addressed to "
            "nobody records nothing about who was told. Stored in the "
            "vocabulary's own order, de-duplicated."
        ),
    )


TEMPLATE_KEY_PATH = Annotated[
    str,
    Path(
        max_length=64,
        description="The template's stable key, e.g. `rtw_offer`.",
        examples=["three_point_contact"],
    ),
]

MERGE_CLAIM_QUERY = Annotated[
    str,
    Query(
        alias="claimId",
        pattern=CLAIM_ID_PATTERN,
        description=(
            "The claim to merge against, `WC-nnnn`. **Required** — the six "
            "templates are claim-aware, and the prototype's claim-less render "
            "produced letters full of holes. Must be in the caller's caseload; "
            "one that is not answers 404, the same as one that does not exist."
        ),
        examples=["WC-20017"],
    ),
]


@router.get(
    "/email-templates",
    response_model=EmailTemplateListResponse,
    summary="The six quick email templates, in the composer's button order",
    responses={**UNAUTHENTICATED_RESPONSE},
)
async def email_templates(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
) -> EmailTemplateListResponse:
    """Reference data: the same six rows for every authenticated caller.

    Unscoped in the sense `/glossary` is — there is nothing here to scope, no
    employer column and no PHI — and still behind the session dependency, so
    "unscoped" means "the same answer for everyone signed in", never "public".
    `Cache-Control: no-store` all the same, because every route on this router
    says so and an exception would be the one somebody has to reason about.
    """
    response.headers["Cache-Control"] = "no-store"
    return EmailTemplateListResponse(
        items=[_email_template(view) for view in await list_email_templates(db, ctx)]
    )


@router.get(
    "/email-templates/{template_key}/merged",
    response_model=MergedEmailResponse,
    summary="One template, merged against one claim (server-side)",
    responses={**UNAUTHENTICATED_RESPONSE, **EMAIL_NOT_FOUND_RESPONSE},
)
async def merged_email_template(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    template_key: TEMPLATE_KEY_PATH,
    claim_id: MERGE_CLAIM_QUERY,
) -> MergedEmailResponse:
    """Fill the composer. **The browser does no merging** (AD-1).

    Every placeholder is resolved here, against a claim read under the caller's
    scope: `daysOpen` from the registered derivation (AD-10), `stage` and
    `status` as prose, the worker's name from the claim's employee and the
    signature from the caller's own persona. A `claimId` outside the caller's
    book answers 404, deliberately the same as one that does not exist.

    Omitting `claimId` is `/problems/validation-error` from the schema rather
    than a degraded letter — the SPA disables the six buttons with a stated
    reason instead of sending this.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        merged = await merged_template(db, ctx, template_key, claim_business_id=claim_id)
    except EmailTemplateNotFound as exc:
        raise _email_template_not_found() from exc
    except EmailClaimNotVisible as exc:
        raise _email_claim_not_found(exc.claim_business_id) from exc
    return _merged(merged)


@router.get(
    "/meetings/{meeting_id}/email-draft",
    response_model=MergedEmailResponse,
    summary="The convert-to-email letter for one meeting",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **MEETING_NOT_FOUND_RESPONSE,
        **CLOCK_UNPROCESSABLE_RESPONSE,
    },
)
async def meeting_draft(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    meeting_id: MEETING_ID_PATH,
    as_of: AS_OF_QUERY = None,
) -> MergedEmailResponse:
    """A meeting confirmation, ready to send — the ✉ button on a meeting card.

    The same payload a template merge returns, so the composer has one fill
    path. `recipients` is that meeting's participants: both sides are the same
    six-value vocabulary, so the mapping is the identity rather than the
    prototype's substring match on labels.

    **Read-only with respect to `meeting`** (AD-12). Converting a meeting to an
    email changes nothing about the meeting, and this route writes nothing at
    all — a subsequent `POST /emails` is what logs anything.

    404 for a meeting that is absent, held by another handler, or whose claim
    has left the caller's book — one answer for all three, which is the
    meetings module's security property and not something this route relaxes.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        draft = await meeting_email_draft(db, ctx, meeting_id, as_of=as_of)
    except MeetingNotVisible as exc:
        raise _meeting_not_found(meeting_id) from exc
    return _merged(draft)


@router.get(
    "/emails",
    response_model=EmailLogListResponse,
    summary="The session persona's logged emails, newest first",
    responses={**UNAUTHENTICATED_RESPONSE, **BAD_CURSOR_RESPONSE},
)
async def emails(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    cursor: Annotated[
        str | None,
        Query(description="An opaque `nextCursor` from a previous response."),
    ] = None,
    limit: Annotated[
        int | None,
        Query(
            ge=EMAIL_MIN_PAGE_LIMIT,
            le=EMAIL_MAX_PAGE_LIMIT,
            description="Page size. Reused from the cursor when one is supplied.",
        ),
    ] = None,
) -> EmailLogListResponse:
    """The caller's own sent log. Page it; you cannot re-scope it.

    **Sender scope, not employer scope**: two handlers whose books overlap read
    their own correspondence and not each other's, and a supervisor over both
    reads neither.

    **Not filtered by claim, and there is no parameter that could be.** The list
    is the handler's log across their whole book — the prototype's own shape —
    and each row carries its claim reference.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        page = await list_email_logs(db, ctx, cursor=cursor, limit=limit)
    except EmailInvalidCursor as exc:
        # 400 rather than 422, for the meetings list's reason: the cursor is
        # syntactically a string and passed validation; what failed is that it
        # does not describe a position in *this* list.
        raise ProblemException(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Bad Request",
            detail=str(exc),
            type_="/problems/invalid-cursor",
            headers={"Cache-Control": "no-store"},
        ) from exc
    return _email_page(page)


@router.post(
    "/emails",
    response_model=EmailLogResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Log a stakeholder email (audited) — no message is transmitted",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **FORBIDDEN_RESPONSE,
        **EMAIL_NOT_FOUND_RESPONSE,
        **EMAIL_UNPROCESSABLE_RESPONSE,
    },
)
async def log_email(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    body: NewEmailRequest,
) -> EmailLogResponse:
    """Write one `email_log` row, and answer with it.

    **Nothing is sent.** No SMTP, no queue, no webhook — the control is labelled
    "✉ Send Email (logged)" and this is what it means. Real egress is a Deferred
    architecture decision with its own compliance review; a client must not read
    the 201 as a delivery receipt, and `sentAt` records composition rather than
    transmission.

    **201 with no `Location` header**, `write_diary_note`'s call: a row is
    created, so 201 is the honest status, and there is deliberately no
    `GET /claims-diary/emails/{id}` to point at — the log is read as a list and
    the prototype's email card opens nothing.

    The body is the created entity rather than an acknowledgement, so what the
    SPA renders at the top of the list is what the *scoped read* returns rather
    than an echo of what was sent.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        view = await send_email(
            db,
            ctx,
            subject=body.subject,
            body=body.body,
            priority=body.priority,
            recipients=body.recipients,
            claim_business_id=body.claim_id,
            template_key=body.template_key,
        )
    except EditNotPermitted as exc:
        raise _forbidden(exc) from exc
    except EmailTemplateNotFound as exc:
        raise _email_template_not_found() from exc
    except EmailClaimNotVisible as exc:
        raise _email_claim_not_found(exc.claim_business_id) from exc
    except EmailLogNotVisible as exc:
        # The row was written and audited, and then the post-commit re-read
        # could not see it — a scope narrowing landing between the two
        # statements. Its own problem type and its own sentence, because the one
        # thing the answer must not say is anything a caller would respond to by
        # sending it again: `email_log` has no edit and no delete, so a retry is
        # a duplicate nobody can remove. `_note_not_readable`'s ruling.
        raise _email_not_readable(exc.email_id) from exc
    except InvalidPatch as exc:
        raise _unprocessable(exc) from exc
    return _email(view)


def _email_template_not_found() -> ProblemException:
    """The 404 for a template key that is not one of the six.

    **The key is not echoed**, and it used to be — on the argument that the six
    keys are published by `GET /email-templates` anyway, so repeating one back
    revealed nothing. That argument holds for the six and for nothing else: the
    value quoted is whatever the *caller* put in the path, up to 64 characters
    of it, and it lands in a problem document a browser may render and a proxy
    may log. `normalise_recipients` refuses a submitted token without quoting it
    for exactly this reason (AD-11), and one refusal on this router quoting
    caller text while its neighbour does not is the inconsistency worth removing
    rather than the echo worth keeping.

    A client with six buttons still knows which is broken: it sent the request.

    **No `pattern` on `TEMPLATE_KEY_PATH` to go with this.** A malformed key and
    an absent one answer the same 404 by design — there is no enumeration to
    protect, and a pattern would turn the malformed case into a 422, splitting
    one documented answer into two.
    """
    return ProblemException(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Not Found",
        detail="No email template with that key.",
        type_="/problems/email-template-not-found",
        headers={"Cache-Control": "no-store"},
    )


def _email_claim_not_found(claim_business_id: str | None) -> ProblemException:
    """The 404 for a claim reference outside the caller's book.

    Its own `type` rather than the meetings or notes one, because the three name
    different resources and a client mapping problem types to inline messages
    should not have to know which aggregate answered. The *wording* is the case
    file's, so a caller comparing this refusal with `GET /claims/{id}`'s learns
    nothing.
    """
    return ProblemException(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Not Found",
        detail=f"No claim {claim_business_id} in your caseload.",
        type_="/problems/email-claim-not-found",
        headers={"Cache-Control": "no-store"},
    )


def _email_not_readable(email_id: int) -> ProblemException:
    """The 404 for an email that was **logged** and then could not be read back.

    `_note_not_readable`'s shape, and it matters more here than anywhere else on
    this router. The row is committed and audited by the time this is raised —
    only the scoped re-read failed — and `email_log` has no edit and no delete,
    so a caller told the write failed re-sends it and the console's own refusal
    is what duplicates the record of a communication. It is still a 404 rather
    than a 500 because the row genuinely is not in the caller's sent log *now*,
    which is a fact about scope rather than a fault.
    """
    return ProblemException(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Not Found",
        detail=(
            f"Email {email_id} was logged, but it can no longer be read back "
            "from your sent log — your caseload changed while it was being "
            "written. Do not send it again; reload the list."
        ),
        type_="/problems/email-not-readable",
        headers={"Cache-Control": "no-store"},
    )
