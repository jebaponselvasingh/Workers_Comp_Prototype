"""Identity plumbing: sessions, personas, and the rows scope is resolved from.

This module predates the AD-7 caller context by construction — it is what
builds it — and reads only `app_user`, `user_employer_assignment`,
`employer`, and `session`. It never touches claim-scoped data, so the
"every repository method takes a context" rule has nothing to bind here.

Persona display labels are *derived* from the scope actually stored, never
stored strings: a label can therefore not drift away from the assignments
it describes.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from data.models import AppUser, Employer, Session, UserEmployerAssignment
from data.models.enums import UserRole

# The persona picker is a demo affordance: the parenthetical tells a
# reviewer at a glance whether they are about to log in scoped or
# full-portfolio. Roles render their book differently only because the
# prototype does (supervisors slash-separated, handlers middot-separated).
#
# Looked up with .get() rather than indexed: this runs on the unauthenticated
# login picker, so a UserRole member added by a later story must degrade to a
# plain label, not KeyError the login screen into a 500 for everybody.
_ROLE_TITLE = {
    UserRole.handler: "Handler",
    UserRole.supervisor: "WC Supervisor",
    # David Bline is a supervisor wearing the analyst hat — the prototype
    # keeps his title and marks the hat in the hint.
    UserRole.analyst: "WC Supervisor",
}
_SCOPE_SEPARATOR = {
    UserRole.handler: " · ",
    UserRole.supervisor: "/",
    UserRole.analyst: "/",
}


def persona_label(
    name: str,
    role: UserRole,
    scope_all: bool,
    employer_short_names: list[str],
) -> str:
    """`"Jennifer Park — WC Supervisor (3M/GM/Toyota)"` and friends.

    The full-portfolio hint says "Full portfolio" rather than the
    prototype's "All 100 claims": producing that number meant counting the
    `claim` table from an endpoint that answers before anyone has
    authenticated, which AD-7 forbids outright ("no code path may query
    claim data without [a caller context]") and which published the
    portfolio size to any unauthenticated caller. Reviewed and accepted
    2026-08-10.
    """
    title = _ROLE_TITLE.get(role, role.value.replace("_", " ").title())
    if role is UserRole.analyst:
        hint = "Analyst view"
    elif scope_all:
        hint = "Full portfolio"
    elif employer_short_names:
        hint = _SCOPE_SEPARATOR.get(role, " · ").join(employer_short_names)
    else:
        # scope_all is false and no assignment rows exist: this persona can
        # see nothing. Say so, rather than rendering "Name — Handler ()".
        hint = "No employers assigned"
    return f"{name} — {title} ({hint})"


def initials(name: str) -> str:
    """`"David Bline"` -> `"DB"` — the top-bar user chip (Story 1.4 renders it)."""
    return "".join(part[0] for part in name.split() if part)[:2].upper()


def hash_token(token: str) -> str:
    """Sessions are stored hashed: a database dump is not a set of live cookies."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def list_personas(db: AsyncSession) -> list[dict[str, object]]:
    """Every seeded persona with its derived label, in seed order.

    Pre-auth by necessity — this *is* the login picker — and therefore
    restricted to identity tables. It never reads claim data (AD-7), and
    returns no employer ids; the label's parenthetical does name a scoped
    persona's employers, which is a deliberate demo affordance the story
    specifies, but nothing here is machine-consumable scope.
    """
    scope_rows = (
        await db.execute(
            sa.select(UserEmployerAssignment.user_id, Employer.short_name)
            .join(Employer, Employer.id == UserEmployerAssignment.employer_id)
            .order_by(UserEmployerAssignment.user_id, Employer.name)
        )
    ).all()
    short_names: dict[int, list[str]] = {}
    for user_id, short_name in scope_rows:
        short_names.setdefault(user_id, []).append(short_name)

    users = (await db.scalars(sa.select(AppUser).order_by(AppUser.id))).all()
    return [
        {
            "id": user.id,
            "name": user.name,
            "role": user.role,
            "label": persona_label(
                user.name,
                user.role,
                user.scope_all,
                short_names.get(user.id, []),
            ),
        }
        for user in users
    ]


async def get_persona(db: AsyncSession, persona_id: int) -> AppUser | None:
    return await db.get(AppUser, persona_id)


async def mint_session(db: AsyncSession, user_id: int, ttl_hours: int) -> str:
    """Create a session row and return the opaque token for the cookie.

    The token is returned exactly once — only its hash is persisted, so a
    lost token is unrecoverable rather than re-readable.

    Also drops this user's already-expired rows. That is not a substitute
    for a real reaper (see deferred-work.md — nothing sweeps personas who
    never log back in), but it keeps the common path bounded and it is a
    write, so it belongs in a transaction the caller commits rather than in
    the read path of the auth dependency.
    """
    now = datetime.now(UTC)
    await db.execute(
        sa.delete(Session).where(Session.user_id == user_id, Session.expires_at <= now)
    )

    token = secrets.token_urlsafe(32)
    await db.execute(
        sa.insert(Session).values(
            token_hash=hash_token(token),
            user_id=user_id,
            created_at=now,
            expires_at=now + timedelta(hours=ttl_hours),
        )
    )
    return token


async def resolve_session(db: AsyncSession, token: str) -> AppUser | None:
    """Cookie token -> the live `app_user` row, or None.

    Role and scope are deliberately *not* returned here: callers get the
    user row and the context builder re-derives everything else, which is
    what makes AD-7's "re-resolve on every request" cheap and unavoidable.

    Strictly a read. This runs inside the auth dependency, on the same
    `AsyncSession` the route handler will use, so committing here would
    make authentication decide a transaction boundary on behalf of every
    endpoint written from Story 1.4 on. Expired rows are cleared by
    `mint_session`, where the write has a caller who owns the commit.
    """
    row = (
        await db.execute(
            sa.select(AppUser)
            .join(Session, Session.user_id == AppUser.id)
            .where(
                Session.token_hash == hash_token(token),
                Session.expires_at > datetime.now(UTC),
            )
        )
    ).first()
    if row is None:
        return None
    user: AppUser = row[0]
    return user


async def revoke_session(db: AsyncSession, token: str) -> None:
    """Logout ends the session server-side — a replayed cookie 401s (AC 3)."""
    await db.execute(sa.delete(Session).where(Session.token_hash == hash_token(token)))


async def employer_ids_for(db: AsyncSession, user_id: int) -> frozenset[int]:
    rows = await db.scalars(
        sa.select(UserEmployerAssignment.employer_id).where(
            UserEmployerAssignment.user_id == user_id
        )
    )
    return frozenset(rows.all())
