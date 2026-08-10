"""Identity endpoints: the persona picker, login, logout, and who-am-I.

Thin by AD-1: these routes hold no policy. Which personas exist, what a
label says, and what a session is are all decided in
`data/repositories/identity.py`; who a request is and what it may see are
decided in `api/deps.py`.

`/personas` rides the Deferred IdP decision — when real identity lands, it
is deleted rather than adapted, because there is no such thing as "list the
credentials you may log in as" against a real directory.
"""

from fastapi import APIRouter, Request, Response, status
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
    get_persona,
    initials,
    list_personas,
    mint_session,
    revoke_session,
)

router = APIRouter(tags=["auth"])

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
    """The `{items, nextCursor, total?}` envelope (Lists convention).

    The persona list is a fixed ten rows, so `nextCursor` is structurally
    always null — the envelope is here so the generated client sees one
    list shape across the whole API, not because this list will ever page.
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


@router.get("/personas", response_model=PersonaList, summary="Personas available to log in as")
async def personas(db: DbDep) -> PersonaList:
    items = [Persona.model_validate(row) for row in await list_personas(db)]
    return PersonaList(items=items, total=len(items))


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
