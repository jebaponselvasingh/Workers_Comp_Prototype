"""Identity endpoints: the persona picker, login, logout, and who-am-I.

Thin by AD-1: these routes hold no policy. Which personas exist, what a
label says, and what a session is are all decided in
`data/repositories/identity.py`; who a request is and what it may see are
decided in `api/deps.py`.

`/personas` rides the Deferred IdP decision — when real identity lands, it
is deleted rather than adapted, because there is no such thing as "list the
credentials you may log in as" against a real directory.
"""

from typing import Annotated, NoReturn

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import Field

from api.deps import (
    CurrentUser,
    DbDep,
    SettingsDep,
    clear_session_cookie,
    session_token,
    set_session_cookie,
)
from api.errors import PROBLEM_CONTENT_TYPE, ProblemDocument, ProblemException
from api.schemas import ApiModel
from data.models.enums import UserRole
from data.repositories.identity import (
    Cursor,
    InvalidCursor,
    LoginRole,
    PersonaSort,
    count_personas,
    decode_persona_cursor,
    encode_persona_cursor,
    get_persona,
    initials,
    list_personas,
    mint_session,
    persona_sort_value_of,
    revoke_session,
)

router = APIRouter(tags=["auth"])

#: The picker's page-size range, declared once and enforced twice —
#: `queue.py`'s rule: FastAPI refuses a `limit` outside it with a 422 before the
#: read, and `decode_persona_cursor` refuses one smuggled inside a cursor. On
#: this endpoint that second enforcement is the only one, since a cursor
#: arriving pre-auth passes through no other validator.
MIN_PERSONA_PAGE_LIMIT = 1
MAX_PERSONA_PAGE_LIMIT = 200

#: The default page size — deliberately larger than the seeded directory.
#:
#: Ten personas ship in the seed migration (`test_personas_groups_the_seed_by_
#: role` counts them) and `usePersonas` makes one unparameterised read, so a
#: default below the row count would have turned Story 9.8's honest `LIMIT` into
#: a login screen missing personas — the one thing worse than the fake
#: pagination it replaces.
#: `test_the_seeded_personas_fit_inside_the_default_page` pins it, so a
#: migration that pushes the directory past this number fails CI rather than
#: quietly hiding somebody's login.
DEFAULT_PERSONA_PAGE_LIMIT = 100

BAD_PERSONA_CURSOR_RESPONSE: dict[int | str, dict[str, object]] = {
    400: {
        "description": (
            "The pagination cursor is unreadable, or belongs to a different "
            "sort or role filter (RFC 9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}

# Declared so the generated TypeScript client knows 401 is a possible
# outcome. Without it the client's error type for these operations is
# `never`, which makes the SPA's non-null assertions typecheck for the
# wrong reason and leaves the contract silent about the API's single most
# important behaviour.
#
# The schema is supplied inline under `application/problem+json` rather than
# via `model=`: FastAPI files a `model` under the default `application/json`,
# which would make the published contract name a media type this endpoint
# never sends. The document is four scalars, so inlining costs nothing.
UNAUTHENTICATED_RESPONSE: dict[int | str, dict[str, object]] = {
    401: {
        "description": "No valid session (RFC 9457 problem document).",
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}


class Persona(ApiModel):
    id: int
    name: str
    role: UserRole
    label: str


class PersonaList(ApiModel):
    """The `{items, nextCursor, total?}` envelope (Lists convention), for real.

    It used to say the list was "a fixed ten rows, so `nextCursor` is
    structurally always null — the envelope is here so the generated client sees
    one list shape across the whole API, not because this list will ever page".
    Story 9.8 made the shape a contract instead of a costume: `nextCursor` is
    non-null exactly while personas remain beyond `items`, and `total` is a
    `COUNT(*)` over the list being paged rather than `len(items)`.

    Nothing here is scope. `total` counts the rows this picker publishes in
    full — every one of them is in `items` on an unparameterised read — so it
    discloses nothing the payload does not already carry, which is the property
    an unauthenticated endpoint has to keep.
    """

    items: list[Persona]
    next_cursor: str | None = None
    total: int


class LoginRequest(ApiModel):
    # Bounded to int4: app_user.id is an Integer column, so an out-of-range
    # value would reach the driver and come back as a 500 — which is both a
    # crash and an enumeration oracle, since 500 and 401 would then
    # distinguish "no such id" from "impossible id".
    persona_id: int = Field(ge=1, le=2_147_483_647)


class Me(ApiModel):
    id: int
    name: str
    role: UserRole
    initials: str


@router.get(
    "/personas",
    response_model=PersonaList,
    summary="Personas available to log in as, paged",
    responses=BAD_PERSONA_CURSOR_RESPONSE,
)
async def personas(
    db: DbDep,
    cursor: Annotated[
        str | None,
        Query(description="An opaque `nextCursor` from a previous response."),
    ] = None,
    limit: Annotated[
        int | None,
        Query(
            ge=MIN_PERSONA_PAGE_LIMIT,
            le=MAX_PERSONA_PAGE_LIMIT,
            description=f"Page size; defaults to {DEFAULT_PERSONA_PAGE_LIMIT}.",
        ),
    ] = None,
    role: Annotated[
        LoginRole | None,
        Query(
            alias="filter[role]",
            description=(
                "One of the three login roles. The SPA partitions the picker by "
                "role in the browser today; this is the server-side facet the "
                "Lists convention specifies. The machine actor is not a member "
                "of this enum, so naming it is a 422 rather than an empty page."
            ),
        ),
    ] = None,
    sort: Annotated[
        PersonaSort,
        Query(description="Which ordering to page. Defaults to the seeded order."),
    ] = PersonaSort.id,
) -> PersonaList:
    """The login picker — still reachable with no session, now genuinely paged.

    **Paging this endpoint needed neither a session nor a scope decision**,
    which was the open question: the position is a keyset over stored identity
    columns, so nothing here knows or records who is asking. The cursor carries
    the request's own shape and a position in rows this endpoint publishes in
    full; there is nothing in it a caller could not read off the response.

    **`filter[role]` narrows what `LOGIN_ROLES` already permits and can never
    widen it.** The machine actor is excluded by a predicate this parameter is
    `AND`-ed into, not by a default this parameter could replace — see
    `_persona_scope` — and a cursor naming `system` is refused in the decoder
    rather than answered with an empty page, so "no such personas" and "you may
    not ask about those" do not look alike from outside.

    Raises 400 `/problems/invalid-cursor` for a cursor that does not describe a
    position in this list. Never a silent page one.
    """
    # Back to the storage vocabulary at the one boundary that holds both. The
    # published facet is `LoginRole` so FastAPI can refuse the machine actor;
    # the predicate and the cursor are over `app_user.role`, which is `UserRole`.
    scoped_role = None if role is None else UserRole(role.value)

    decoded = None
    if cursor is not None:
        try:
            decoded = decode_persona_cursor(
                cursor,
                min_limit=MIN_PERSONA_PAGE_LIMIT,
                max_limit=MAX_PERSONA_PAGE_LIMIT,
            )
        except InvalidCursor as exc:
            _refuse_cursor(str(exc), exc)
    if decoded is not None:
        if decoded.sort is not sort:
            _refuse_cursor(
                f"That page belongs to the {decoded.sort.value!r} ordering, not {sort.value!r}."
            )
        if decoded.role != scoped_role:
            _refuse_cursor(
                "That page was cut from a differently filtered list; "
                "reload the picker from the first page."
            )
        if limit is not None and limit != decoded.limit:
            _refuse_cursor(
                f"That page was cut at {decoded.limit} rows, and this request asks "
                f"for {limit}; reload the picker from the first page."
            )
    page_size = decoded.limit if decoded is not None else (limit or DEFAULT_PERSONA_PAGE_LIMIT)

    rows = await list_personas(
        db,
        # One more than the page, so "is there another page?" is answered by
        # what came back rather than by comparing against `total` — which would
        # be wrong the moment a migration changed the count between the two
        # statements. `list_email_logs`' idiom.
        limit=page_size + 1,
        after=None if decoded is None else (decoded.last_value, decoded.last_id),
        role=scoped_role,
        sort=sort,
    )
    total = await count_personas(db, role=scoped_role)

    has_more = len(rows) > page_size
    window = rows[:page_size]
    return PersonaList(
        items=[Persona.model_validate(row) for row in window],
        next_cursor=(
            encode_persona_cursor(
                Cursor(
                    last_value=persona_sort_value_of(window[-1], sort),
                    last_id=int(str(window[-1]["id"])),
                    limit=page_size,
                    sort=sort,
                    role=scoped_role,
                )
            )
            if has_more and window
            else None
        ),
        total=total,
    )


def _refuse_cursor(detail: str, cause: Exception | None = None) -> NoReturn:
    """400 `/problems/invalid-cursor` — `claims.queue`'s ruling, on the picker.

    `NoReturn`, not `None`: this function always raises, and typed as `None` its
    call sites read as though an unreadable cursor could fall through to the
    page-one path below them. The type is the statement that it cannot.

    400 rather than 422: the cursor is syntactically a string and passed
    validation. What failed is that it does not describe a position in *this*
    list.

    No `Cache-Control: no-store`, unlike the worklist's identical refusal: that
    one names a caller's own worklist length. This one names an ordering and a
    role the caller sent, on a list every visitor sees identically and before
    any session exists, so there is nothing an intermediary could leak by
    holding it.
    """
    raise ProblemException(
        status_code=status.HTTP_400_BAD_REQUEST,
        title="Bad Request",
        detail=detail,
        type_="/problems/invalid-cursor",
    ) from cause


@router.post(
    "/auth/login",
    response_model=Me,
    summary="Start a session as a persona",
    responses=UNAUTHENTICATED_RESPONSE,
)
async def login(body: LoginRequest, response: Response, db: DbDep, settings: SettingsDep) -> Me:
    persona = await get_persona(db, body.persona_id)
    if persona is None:
        # Deliberately not "no such persona": the same answer for an unknown
        # id and (later) a bad credential keeps this endpoint from becoming
        # an enumeration oracle once real identity lands.
        raise ProblemException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            title="Unauthenticated",
            detail="That persona is not available.",
            type_="/problems/unauthenticated",
        )

    token = await mint_session(db, persona.id, settings.session_ttl_hours)
    await db.commit()
    set_session_cookie(response, settings, token)
    return Me(id=persona.id, name=persona.name, role=persona.role, initials=initials(persona.name))


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End the session server-side",
)
async def logout(request: Request, response: Response, db: DbDep, settings: SettingsDep) -> None:
    """Idempotent by design: ending a session never requires a valid one.

    Requiring authentication here would 401 exactly the caller who most
    needs the cookie cleared — one holding an expired or already-revoked
    token — and leave that dead cookie in their browser until it aged out.
    """
    token = session_token(request, settings)
    if token is not None:
        await revoke_session(db, token)
        await db.commit()
    clear_session_cookie(response, settings)


@router.get(
    "/me",
    response_model=Me,
    summary="The current persona",
    responses=UNAUTHENTICATED_RESPONSE,
)
async def me(user: CurrentUser, response: Response) -> Me:
    # This response decides which persona the SPA renders, so it must never
    # be reused for a different one. nginx sets proxy_cache off today, but
    # this must not depend on an ingress detail staying put.
    response.headers["Cache-Control"] = "no-store"
    return Me(id=user.id, name=user.name, role=user.role, initials=initials(user.name))
