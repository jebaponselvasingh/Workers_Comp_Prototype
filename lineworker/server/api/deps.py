"""Auth dependencies — the single OIDC swap point, and the only place
`scope_all` becomes `ALL` (AD-7).

Two things happen here and nowhere else:

1. **Authentication.** `enforce_authenticated` is registered as an
   application-level dependency in `create_app`, so it runs ahead of every
   route in every router — including routers no story has written yet.
   Auth is therefore *default-on*: a new endpoint is protected unless
   someone deliberately adds its path to `PUBLIC_PATHS`, which is a
   reviewable one-line diff. The alternative (per-router dependencies) fails
   open, and it fails open silently.

   Until the IdP decision lands (Deferred; trigger: before the first
   non-dev deployment), "authentication" is persona selection against the
   Story 1.2 seed. That interim lives entirely inside `_resolve_user`:
   swapping in OIDC means validating an IdP token there and mapping the
   subject to an `app_user` row. No endpoint, router, or repository changes.

2. **Scope resolution.** `get_caller_context` turns the resolved user into
   `(user_id, role, employer_ids | ALL)` — the one place `scope_all`
   becomes `ALL`. The *type* moved to `data/context.py` in Story 1.4 so
   repositories can require one without importing `api/`; the decision it
   encodes is still made here and nowhere else. The cookie contributes a *user
   reference* and nothing else, so scope is re-derived from the database on
   every single request — a persona whose book changed mid-session gets the
   new book on the next call, and a stolen cookie cannot carry a wider
   scope than its user currently has.
"""

from collections.abc import AsyncIterator
from typing import Annotated, Final

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.errors import AuthenticationRequired
from config import Settings
from data.context import ALL_EMPLOYERS, CallerContext, EmployerScope
from data.models import AppUser
from data.repositories.identity import employer_ids_for, resolve_session

# Endpoints that must answer without a session, with the reason each is
# exempt. Everything else is authenticated; adding to this set is the only
# way for an *API route* to opt out, and it should be argued for in review.
#
# Not listed, deliberately: `/openapi.json`, `/docs`, `/redoc`. FastAPI
# registers those as plain Starlette routes rather than API routes, so they
# never see this dependency at all — listing them here would imply a control
# that does not exist (and would suggest that removing them re-secures the
# paths, which it does not). They are gated in `create_app` instead, by not
# being served in prod.
PUBLIC_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/healthz",  # container healthcheck — runs before any user exists
        "/personas",  # *is* the login picker (identity + label only)
        "/auth/login",  # mints the session
        # Ending a session must never require a valid one: an expired cookie
        # still deserves to be cleared rather than 401'd (logout is
        # idempotent by nature).
        "/auth/logout",
    }
)


def route_path(request: Request) -> str:
    """The path Starlette matched routes against — `root_path` removed.

    `request.url.path` is the raw incoming path and still carries the mount
    prefix, while routing uses the stripped form. Comparing the allowlist
    against the raw path therefore works only while the ingress happens to
    strip `/api` itself: point the app at an ingress that forwards the
    prefix (an ALB or k8s Ingress with no rewrite rule, `uvicorn
    --root-path`, or the Vite dev proxy aimed at a host-run uvicorn) and
    `/personas` arrives as `/api/personas`, misses the allowlist, and 401s —
    making it impossible to log in at all.
    """
    path = request.url.path
    root = request.scope.get("root_path", "")
    if root and path.startswith(root):
        path = path[len(root) :] or "/"
    return path


def get_settings_from_app(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request. Connections are acquired lazily, so a
    request that never touches the database never opens one."""
    async with request.app.state.sessionmaker() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings_from_app)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


def session_token(request: Request, settings: Settings) -> str | None:
    token = request.cookies.get(settings.session_cookie_name)
    return token or None


async def _resolve_user(request: Request, db: AsyncSession, settings: Settings) -> AppUser:
    """Cookie -> session -> `app_user`. The OIDC swap point (see module doc)."""
    token = session_token(request, settings)
    if token is None:
        raise AuthenticationRequired()
    user = await resolve_session(db, token)
    if user is None:
        raise AuthenticationRequired("Your session has ended. Sign in again.")
    return user


async def enforce_authenticated(request: Request, db: DbDep, settings: SettingsDep) -> None:
    """Application-level dependency: authenticate everything not public."""
    if route_path(request) in PUBLIC_PATHS:
        return
    request.state.app_user = await _resolve_user(request, db, settings)


def get_current_user(request: Request) -> AppUser:
    """The user `enforce_authenticated` already resolved for this request."""
    user: AppUser | None = getattr(request.state, "app_user", None)
    if user is None:
        # Only reachable if a PUBLIC_PATHS endpoint asks for a user, which
        # is a coding error rather than a client one — but answering 401 is
        # the safe direction to be wrong in.
        raise AuthenticationRequired()
    return user


CurrentUser = Annotated[AppUser, Depends(get_current_user)]


async def get_caller_context(user: CurrentUser, db: DbDep) -> CallerContext:
    """AD-7's context builder. `scope_all` becomes `ALL` here and only here."""
    employer_ids: EmployerScope
    if user.scope_all:
        employer_ids = ALL_EMPLOYERS
    else:
        employer_ids = await employer_ids_for(db, user.id)
    return CallerContext(user_id=user.id, role=user.role, employer_ids=employer_ids)


CallerContextDep = Annotated[CallerContext, Depends(get_caller_context)]


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    """HttpOnly so script cannot read it; SameSite=Lax so a cross-site POST
    cannot ride it (the SPA is same-origin, so nothing legitimate needs
    more); Secure derived from the environment."""
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie_name,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
