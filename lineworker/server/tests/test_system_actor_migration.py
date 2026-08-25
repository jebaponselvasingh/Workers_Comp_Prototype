"""Migration 0029 — the system actor, and the login path it must stay out of.

Two halves, and only one of them is about a migration.

The **schema** half is ordinary: the `user_role` enum gains a member and one
`app_user` row appears with it. Worth asserting because the enum change runs
inside an `autocommit_block`, which is the fiddliest thing in the chain, and a
partially-applied revision would leave an enum value with no row behind it.

The **security** half is the one that matters. `POST /auth/login` is a
`PUBLIC_PATHS` endpoint that takes an integer and mints a session for it, and
`app_user.id` is a dense identity column — so an unauthenticated caller can
enumerate ids whatever the picker shows. The system actor is `scope_all`, which
makes it the widest account in the system. `get_persona` refuses it, and these
tests are what keep that refusal from being deleted as "dead code because it
isn't in the picker anyway".
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.models import AppUser
from data.models.enums import LOGIN_ROLES, SYSTEM_ACTOR_NAME, UserRole
from data.repositories.identity import get_persona, list_personas
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


@asynccontextmanager
async def make_client(db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(database_url=db_url, env="e2e"))  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


# --- the schema ----------------------------------------------------------


async def test_the_enum_gained_its_member_and_the_row_landed_with_it(
    db: AsyncSession,
) -> None:
    """Both halves of the revision, checked together.

    Separately they are each satisfiable by a half-applied migration: the enum
    value without the row is a vocabulary nothing uses, and the row without the
    value cannot exist at all. The pairing is what the `autocommit_block` makes
    possible to get wrong.
    """
    labels = set(
        (
            await db.scalars(
                sa.text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'user_role'"
                )
            )
        ).all()
    )
    assert labels == {role.value for role in UserRole}

    actor = (await db.scalars(sa.select(AppUser).where(AppUser.name == SYSTEM_ACTOR_NAME))).one()
    assert actor.role is UserRole.system
    # `scope_all` is the honest value: the batch disburses across the whole
    # portfolio, so its AD-7 predicate is `TRUE` because its scope really is
    # everything — the tautology, not a repository skipping a filter.
    assert actor.scope_all is True


async def test_the_system_actor_holds_no_employer_assignments(db: AsyncSession) -> None:
    """`scope_all` personas carry no assignment rows — the seed's own rule."""
    rows = (
        await db.scalars(
            sa.text(
                "SELECT count(*) FROM user_employer_assignment a "
                "JOIN app_user u ON u.id = a.user_id WHERE u.role = 'system'"
            )
        )
    ).one()
    assert rows == 0


async def test_exactly_one_system_actor_exists(db: AsyncSession) -> None:
    """A second machine identity would make "who ran the batch" ambiguous, and
    `system_context` resolves by name and role — `one_or_none()` would raise."""
    count = (
        await db.scalars(
            sa.select(sa.func.count()).select_from(AppUser).where(AppUser.role == UserRole.system)
        )
    ).one()
    assert count == 1


# --- the login path ------------------------------------------------------


async def test_the_picker_offers_only_personas(db: AsyncSession) -> None:
    # `limit` is required since Story 9.8 made the picker genuinely paged; a
    # number well past the seeded directory keeps this a statement about *every*
    # persona rather than about a page of them.
    personas = await list_personas(db, limit=200)
    assert personas, "the picker is empty"
    assert all(persona["role"] in LOGIN_ROLES for persona in personas)
    assert all(persona["name"] != SYSTEM_ACTOR_NAME for persona in personas)


async def test_the_system_actor_cannot_be_resolved_as_a_persona(db: AsyncSession) -> None:
    """The refusal that is actually the control.

    Omitting the row from a list is not a control when the login endpoint takes
    an id. `None` rather than a distinct refusal, so a system id is
    indistinguishable from an id that does not exist.
    """
    actor = (await db.scalars(sa.select(AppUser).where(AppUser.name == SYSTEM_ACTOR_NAME))).one()
    assert await get_persona(db, actor.id) is None
    # And a real persona still resolves, so the guard has not simply broken
    # `get_persona` for everybody.
    handler = (
        await db.scalars(sa.select(AppUser).where(AppUser.role == UserRole.handler).limit(1))
    ).one()
    assert await get_persona(db, handler.id) is not None


async def test_logging_in_as_the_batch_is_refused_over_http(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """End to end, through the public endpoint, with the id in hand.

    This is the test that would fail if somebody deleted the `LOGIN_ROLES`
    check in `get_persona` on the grounds that the picker already omits the
    row — which is exactly the reasoning that would make a `scope_all` account
    assumable by anyone who can reach the API.
    """
    actor = (await db.scalars(sa.select(AppUser).where(AppUser.name == SYSTEM_ACTOR_NAME))).one()
    async with make_client(seeded_db_url) as client:
        listed = (await client.get("/personas")).json()["items"]
        assert all(persona["id"] != actor.id for persona in listed)

        resp = await client.post("/auth/login", json={"personaId": actor.id})
        missing = await client.post("/auth/login", json={"personaId": 10_000_000})

    assert resp.status_code == 401
    # Indistinguishable from an id that does not exist: the refusal must not be
    # the thing that tells a caller a privileged account is there.
    assert resp.status_code == missing.status_code
    assert resp.json()["detail"] == missing.json()["detail"]
