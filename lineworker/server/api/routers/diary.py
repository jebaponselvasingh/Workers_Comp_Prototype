"""The diary aggregate's routes — meetings today, notes and emails next.

A router of its own rather than four more paths on `api/routers/claims.py`,
which is 2622 lines and is the case file's. The split is by *aggregate*: these
endpoints read and write `meeting` (and, in Stories 4.2 and 4.3, `diary_note`
and `email_log`), none of which is part of a claim's read model. A handler's
diary is theirs, not the claim's — which is also why the prefix is
`/claims-diary` rather than `/claims/{id}/meetings`: the list is scoped to the
*caller*, and a path that nested it under a claim would publish a resource
whose contents do not belong to that claim.

Thin by AD-1: each route validates a body or four query parameters, calls one
command, and maps its refusals onto statuses. Every number, the sort, and the
upcoming/done status were decided in `services/claims/meetings.py` and
`services/derivations/meeting_horizon.py`.

Thin by AD-7 in the way `/claims/queue` is — **there is no scope-shaped
parameter here.** Not a rejected one: an absent one. Nothing on any of these
four routes can name a user, an employer or a role, so "whose meetings?" has
exactly one answer and it comes from the session cookie.
"""

from datetime import date, datetime, time
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Response, status
from pydantic import ConfigDict, Field

from api.deps import CallerContextDep, DbDep
from api.errors import PROBLEM_CONTENT_TYPE, ProblemDocument, ProblemException
from api.routers.auth import UNAUTHENTICATED_RESPONSE
from api.routers.claims import BAD_CURSOR_RESPONSE, CLAIM_ID_PATTERN, FORBIDDEN_RESPONSE
from api.schemas import ApiModel
from data.models.enums import MeetingParticipant, MeetingType
from services.claims.edit import EditNotPermitted, InvalidPatch
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
    complete_meeting,
    create_meeting,
    delete_meeting,
    list_meetings,
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

    `total` is the size of the caller's whole diary, not of `items` — the
    number a count beside the sub-tab shows. A total that shrank when the page
    did would misdescribe the list, which is `StageGroupResponse`'s argument.
    """

    items: list[MeetingResponse]
    next_cursor: str | None = None
    total: int


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
    summary="The session persona's scheduled meetings, oldest date first",
    responses={**UNAUTHENTICATED_RESPONSE, **BAD_CURSOR_RESPONSE},
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
) -> MeetingListResponse:
    """The caller's diary. Page it; you cannot re-scope it."""
    # Specific to one persona's diary, so it must never be served to another
    # from a cache upstream — the same reason `/me` and `/claims/queue` say so.
    response.headers["Cache-Control"] = "no-store"
    try:
        page = await list_meetings(db, ctx, cursor=cursor, limit=limit)
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
        **MEETING_NOT_FOUND_RESPONSE,
        **MEETING_UNPROCESSABLE_RESPONSE,
    },
)
async def schedule_meeting(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    body: NewMeetingRequest,
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
        )
    except EditNotPermitted as exc:
        raise _forbidden(exc) from exc
    except MeetingClaimNotVisible as exc:
        raise _claim_not_found(exc.claim_business_id) from exc
    except MeetingNotVisible as exc:
        # The row was written and audited, and then the post-commit re-read
        # (`create_meeting` ends with `get_meeting`) could not see it — which
        # takes a scope narrowing landing between the two statements. Rare, and
        # the 404 this router already documents is still the honest answer: the
        # meeting is not in the caller's diary *now*. What it must not do is
        # escape as an undocumented 500, which is what it did. The PATCH and
        # DELETE routes catch it for the same reason, one statement earlier.
        raise _meeting_not_found(exc.meeting_id) from exc
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
