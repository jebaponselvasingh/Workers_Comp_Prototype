"""Identity plumbing: sessions, personas, and the rows scope is resolved from.

This module predates the AD-7 caller context by construction — it is what
builds it — and reads only `app_user`, `user_employer_assignment`,
`employer`, and `session`. It never touches claim-scoped data, so the
"every repository method takes a context" rule has nothing to bind here.

Persona display labels are *derived* from the scope actually stored, never
stored strings: a label can therefore not drift away from the assignments
it describes.
"""

import base64
import binascii
import enum
import hashlib
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from data.models import AppUser, Employer, Session, UserEmployerAssignment
from data.models.enums import LOGIN_ROLES, UserRole


class LoginRole(enum.StrEnum):
    """The roles `/personas` will list — the wire type of `filter[role]`.

    A separate enum rather than `UserRole` on the query parameter, so a request
    naming the machine actor is a **422 from FastAPI's own validator**, before
    any handler runs and in exactly the shape `filter[stage]=banana` already
    produces on the drill-through. Typing the parameter as `UserRole` would have
    made `?filter[role]=system` a well-formed request answered with an empty
    page — and an empty page is the shape a caller probes with, so "no such
    personas" and "you may not ask about those" would look identical from
    outside. `decode_persona_cursor` refuses the same value inside a cursor;
    this is the same refusal on the other door.

    The import-time check below is what stops the two lists drifting: `LOGIN_ROLES`
    is the *enforcement* (it is `AND`-ed into every persona query, so the facet
    can only narrow what it already permits) and this is the vocabulary the API
    publishes for it. A role added to one and not the other fails the process
    rather than shipping a facet nobody can ask for, or a 422 on a role the
    picker lists.
    """

    handler = "handler"
    supervisor = "supervisor"
    analyst = "analyst"


# An explicit raise rather than `assert`: the docstring above calls this guard
# load-bearing, and `python -O` deletes assertions — so an `assert` would leave
# exactly the drift it exists to catch shipping in the optimised build.
if {member.value for member in LoginRole} != {role.value for role in LOGIN_ROLES}:
    raise RuntimeError("the published login-role facet and LOGIN_ROLES must name the same roles")


class PersonaSort(enum.StrEnum):
    """The orderings `?sort=` may name on the picker. Lowercase, per conventions.

    Two, not "any column": every option has to be paired with a total order and
    tested as one, and a `sort` that accepted an arbitrary column name would be
    an unbounded contract on a **pre-auth** endpoint — the one place in this API
    where an unbounded contract is reachable without a session.

    `id` is the default and is the seed order the picker has always rendered.
    `name` exists because the Lists convention names `sort` and because a
    directory of people sorted alphabetically is what a picker becomes the
    moment there are more than a screenful; it is not what the SPA sends.
    """

    id = "id"
    name = "name"


#: Which column each ordering sorts on, before the tie-break.
#:
#: A mapping rather than a `match`, `glossary._SORT_COLUMNS`' reason: the
#: check below runs at import, so a third member added to `PersonaSort`
#: without a column fails the process rather than sorting by whatever the `else`
#: branch happened to be.
_PERSONA_SORT_COLUMNS: Mapping[PersonaSort, InstrumentedAttribute[Any]] = {
    PersonaSort.id: AppUser.id,
    PersonaSort.name: AppUser.name,
}
# An explicit raise rather than `assert`, `glossary._SORT_COLUMNS`' reason:
# `python -O` deletes assertions, and this one is a drift guard.
if set(_PERSONA_SORT_COLUMNS) != set(PersonaSort):
    raise RuntimeError("every persona sort option needs a column")


class InvalidCursor(ValueError):
    """A cursor that does not describe a position in the persona list.

    Its own class rather than the worklist's, for `glossary.InvalidCursor`'s two
    reasons: no module in this project shares a pagination codec, and importing
    one from `services/` would point `data/` upward, which AD-1's dependency
    rule forbids.
    """


@dataclass(frozen=True)
class Cursor:
    """Where a page of the persona picker ended, and what decided that.

    **The codec lives beside the query, not in a service.** `/personas` has no
    service — it is the login picker, and what it publishes is decided here —
    so the token sits next to the `ORDER BY` it names, `glossary.Cursor`'s
    argument.

    **It carries no session, no scope and nothing role-derived.** That is the
    property this endpoint's whole design rests on: it answers before anyone has
    authenticated, so a cursor that recorded *who was asking* would be the first
    piece of state on a deliberately stateless path. What it records is the
    request's own shape — the ordering, the role facet, the page size — plus a
    position in a list of rows that were already going to be published in full.

    **Compared, all three**, `glossary.Cursor`'s division: a position in one
    ordering is not a position in another, a role-filtered cursor replayed
    unfiltered would page the whole picker from a key cut from a subset, and a
    `limit` that changed mid-walk cuts a window the caller never asked for.
    `limit` is *reused* when the request omits one.

    No `as_of` and no expiry: a persona has no clock, so a token found in a
    bookmark still names exactly the row it named, and refusing it would be
    theatre. (Whether the *persona* still exists is `get_persona`'s question,
    asked at login, where it is a security control rather than a paging one.)
    """

    last_value: str | int
    last_id: int
    limit: int
    sort: PersonaSort
    role: UserRole | None


def encode_persona_cursor(cursor: Cursor) -> str:
    """Base64url of a compact JSON object, unpadded — `queue.encode_cursor`'s form.

    Opaque by intent rather than by encryption. There is nothing to protect: the
    picker's whole payload is unauthenticated by design, so the token's contents
    are a subset of what the caller is already reading. What the encoding buys
    is that clients treat it as a token to hand back rather than a key to
    increment.
    """
    payload = json.dumps(
        {
            "k": [cursor.last_value, cursor.last_id],
            "l": cursor.limit,
            "s": cursor.sort.value,
            "r": None if cursor.role is None else cursor.role.value,
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


#: Postgres' `integer` range, restated here because a cursor is caller-supplied
#: input and this decoder is the only thing standing between it and the driver.
#:
#: ``app_user.id`` is an `integer` column, so a forged cursor naming 2**63 reaches
#: asyncpg as an out-of-range parameter and raises `DataError` — which is not in
#: the except tuple below and escaped as a **500** rather than the 400 this
#: function exists to produce. Bounded here, beside every other bound on the
#: token, rather than by widening the except tuple to a driver exception this
#: module would otherwise never name.
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1


def _int32(value: int, what: str) -> int:
    """One of the cursor's integer members, held to what an `integer` column holds."""
    if not _INT32_MIN <= value <= _INT32_MAX:
        raise ValueError(f"the cursor's {what} is outside the range of an integer column")
    return value


def decode_persona_cursor(raw: str, *, min_limit: int, max_limit: int) -> Cursor:
    """Parse a cursor, or refuse it. Never a silent fallback to page one.

    `glossary.decode_cursor`'s body, and every bound on it matters more here
    than there: this is the one paged endpoint in the API that answers without a
    session, so its only input validation is what is written in this function.

    - `sort` and `role` go through their enums, so a forged ordering or a role
      that does not exist is a refusal here rather than something reaching the
      query builder.
    - `role` is additionally held to `LOGIN_ROLES`. `list_personas` already
      excludes the machine actor, and a cursor naming `UserRole.system` must be
      refused rather than silently answered with an empty page — an empty page
      is the shape a caller probes with, and "no such personas" and "you may not
      ask about those" should not look alike from outside.
    - `limit` is held to the range the route declares, or the cursor is a way
      past the route's ceiling without passing FastAPI's validator.
    - `k`'s first member must be the type the named ordering sorts on: an `int`
      for `id`, a `str` for `name`. A mismatch would otherwise reach the driver
      as a row-value comparison between a text column and an integer parameter.
    - both integer members are held to the **int32** range, because `id` is an
      `integer` column. `/personas` is unauthenticated, so this decoder is the
      only validator a cursor passes: a forged 2**63 reached asyncpg as a
      `DataError` — not a `ValueError`, not in the except tuple — and escaped as
      a 500 rather than the 400 this function exists to produce.
    """
    try:
        padded = raw + "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        key = data["k"]
        if not isinstance(key, list) or len(key) != 2:
            raise TypeError("the cursor's resumption key is not a two-element array")
        sort = PersonaSort(data["s"])
        role = None if data["r"] is None else UserRole(data["r"])
        if role is not None and role not in LOGIN_ROLES:
            raise ValueError(f"{role.value!r} is not a role this picker lists")
        cursor = Cursor(
            last_value=_persona_sort_value_of_raw(key[0], sort),
            last_id=_int32(int(key[1]), "persona id"),
            limit=int(data["l"]),
            sort=sort,
            role=role,
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
    if not min_limit <= cursor.limit <= max_limit:
        raise InvalidCursor(
            f"The pagination cursor names a page size of {cursor.limit}; "
            f"it must be between {min_limit} and {max_limit}."
        )
    return cursor


def _persona_sort_value_of_raw(raw: object, sort: PersonaSort) -> str | int:
    """A cursor's sort value, coerced through the type its ordering sorts on.

    `bool` is an `int` in Python and is refused explicitly,
    `drill_through._as_int`'s rule.
    """
    if sort is PersonaSort.id:
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise TypeError(f"expected an integer id, got {type(raw).__name__}")
        return _int32(raw, "sort value")
    if not isinstance(raw, str):
        raise TypeError(f"expected a string sort value, got {type(raw).__name__}")
    return raw


def persona_sort_value_of(persona: Mapping[str, object], sort: PersonaSort) -> str | int:
    """The value the named ordering sorted this persona by — the cursor's key.

    Here rather than in the router for `Cursor`'s reason: the value the cursor
    records has to be the value the `ORDER BY` used, and one mapping means the
    two cannot come to disagree the way a second `if` in a route would.
    """
    if sort is PersonaSort.name:
        return str(persona["name"])
    return int(str(persona["id"]))


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


def _persona_scope(role: UserRole | None) -> sa.ColumnElement[bool]:
    """Which `app_user` rows the picker lists — one predicate, two callers.

    The page and the `COUNT(*)` have to narrow identically or `total` describes
    a different question from `items`, which is the defect `len(items)` had in a
    louder form. So both go through this.

    `LOGIN_ROLES` is the machine-actor filter and is unconditional; `role` is
    the caller's optional facet on top of it. The order matters: a `filter[role]`
    can only ever narrow what `LOGIN_ROLES` already permits, so the parameter
    can never surface the system actor however it is spelled.
    """
    predicate: sa.ColumnElement[bool] = AppUser.role.in_(LOGIN_ROLES)
    if role is not None:
        predicate = sa.and_(predicate, AppUser.role == role)
    return predicate


async def count_personas(db: AsyncSession, *, role: UserRole | None = None) -> int:
    """How many personas are in the list being paged — the envelope's `total`.

    A `COUNT(*)` over the same predicate the page applies, so `total` means
    "personas that exist" and never "personas on this page". Before Story 9.8
    this endpoint published `len(items)`, a number that agreed with itself
    whatever happened to the table — so the moment a `LIMIT` appeared it would
    have changed meaning with no test able to see it.
    """
    count = await db.scalar(
        sa.select(sa.func.count()).select_from(AppUser).where(_persona_scope(role))
    )
    return int(count or 0)


async def list_personas(
    db: AsyncSession,
    *,
    limit: int,
    after: tuple[str | int, int] | None = None,
    role: UserRole | None = None,
    sort: PersonaSort = PersonaSort.id,
) -> list[dict[str, object]]:
    """One page of seeded personas with their derived labels.

    **Paged for real since Story 9.8**, and paged *without a session*: the
    position is a keyset on stored columns, so nothing here needs to know who is
    asking. That was the open question — the register deferred this machinery
    partly because "a ten-row public picker" looked like the wrong place to
    improvise it — and the answer is that it does not need one. `after` is the
    previous page's last row as `(sort value, id)` and the predicate is the
    row-value comparison against the same two expressions the sort uses,
    `select_meetings_page`'s idiom.

    **The tie-break is `id` in every ordering, including its own.** `name` is
    not unique — two seeded personas could share one, and a real directory
    certainly will — so an ordering on it alone is partial, and a keyset page
    that ended inside a tie would repeat one row and drop another. `id` is the
    identity PK, which is what makes both orderings total by construction.

    **The scope read is narrowed to the page.** It used to load every
    assignment row in the table to build labels for every persona; now it loads
    them for the ids on this page, which is what keeps a paged picker's cost
    proportional to the page rather than to the directory behind it.

    Pre-auth by necessity — this *is* the login picker — and therefore
    restricted to identity tables. It never reads claim data (AD-7), and
    returns no employer ids; the label's parenthetical does name a scoped
    persona's employers, which is a deliberate demo affordance the story
    specifies, but nothing here is machine-consumable scope.

    **Machine actors are filtered out** (Story 3.4). `app_user` gained a
    `system` row — the identity the payment batch audits under — and it is not
    a persona: nobody logs in as the batch, and listing it would offer a
    full-portfolio account in an unauthenticated picker. The *enforcement* is
    `get_persona`'s refusal one function down, because omitting a row from a
    list is not a control when the login endpoint takes an id; this filter is
    what stops the picker showing something a click cannot use.
    """
    column = _PERSONA_SORT_COLUMNS[sort]
    statement = sa.select(AppUser).where(_persona_scope(role))
    if after is not None:
        last_value, last_id = after
        statement = statement.where(
            sa.tuple_(column, AppUser.id)
            > sa.tuple_(
                # Typed literals rather than bare Python values: a row-value
                # comparison hands both sides to the driver as parameters, and
                # an untyped one reaches asyncpg with nothing to encode it as
                # (`select_meetings_page`'s lesson).
                sa.literal(last_value, column.type),
                sa.literal(last_id, AppUser.id.type),
            )
        )
    users = (await db.scalars(statement.order_by(column, AppUser.id).limit(limit))).all()

    short_names: dict[int, list[str]] = {}
    if users:
        scope_rows = (
            await db.execute(
                sa.select(UserEmployerAssignment.user_id, Employer.short_name)
                .join(Employer, Employer.id == UserEmployerAssignment.employer_id)
                .where(UserEmployerAssignment.user_id.in_([user.id for user in users]))
                .order_by(UserEmployerAssignment.user_id, Employer.name)
            )
        ).all()
        for user_id, short_name in scope_rows:
            short_names.setdefault(user_id, []).append(short_name)

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
    """The `app_user` a login request names, or `None` if it may not log in.

    **The `system` refusal is the security control, not the picker's filter**
    (Story 3.4). `POST /auth/login` is a `PUBLIC_PATHS` endpoint that takes an
    integer and mints a session for it, and `app_user.id` is a dense identity
    column — so an unauthenticated caller can enumerate ids whatever the picker
    shows. The batch's actor is `scope_all`, which makes it the widest account
    in the system; without this line, assuming it would be a matter of POSTing
    the right small integer.

    `None` rather than a distinct refusal, so the router's existing "unknown
    persona" branch answers, and a system id is indistinguishable from an id
    that does not exist.
    """
    user = await db.get(AppUser, persona_id)
    if user is None or user.role not in LOGIN_ROLES:
        return None
    return user


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
