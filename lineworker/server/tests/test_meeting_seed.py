"""Story 4.1 AC 6 — migration 0033's two demo meetings per handler persona.

Separate from `test_meetings.py` because the two files want opposite things
from the database: this one asserts against a *pristine* migrated schema, and
that one mutates the portfolio. The module-scoped `seeded_db_url` fixture
rebuilds per file, so keeping them apart is what stops a scheduled or deleted
meeting from one suite changing the counts asserted in the other.

The expectations come from `tests/seed_fixture.py`, which recomputes the
migration's rule (the two lowest-sorted claim business ids in each handler's
employer scope) from `seed_data.json` rather than reading the rows back — the
oracle discipline every seed test here follows.
"""

import importlib.util
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from data.models import AppUser, Claim, Meeting
from data.models.enums import MeetingParticipant, MeetingType, UserRole
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

SERVER_ROOT = Path(__file__).resolve().parents[1]

#: Loaded by path rather than imported, `test_financial_tables_migration.py`'s
#: device: `data/versions/` is Alembic's script directory, not a package, and
#: the module names begin with a digit.
_MIGRATION = SERVER_ROOT / "data" / "versions" / "20260817_0032_meeting.py"
_spec = importlib.util.spec_from_file_location("_m0032", _MIGRATION)
assert _spec and _spec.loader
m0032 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m0032)


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def _meetings_by_handler(db: AsyncSession) -> dict[str, list[sa.Row[Any]]]:
    rows = await db.execute(
        sa.select(Meeting, AppUser.name.label("handler_name"), Claim.claim_id.label("claim_ref"))
        .select_from(Meeting)
        .join(AppUser, Meeting.app_user_id == AppUser.id)
        .join(Claim, Meeting.claim_id == Claim.id)
        .order_by(AppUser.name, Meeting.meeting_date, Meeting.id)
    )
    grouped: dict[str, list[sa.Row[Any]]] = {}
    for row in rows.all():
        grouped.setdefault(row.handler_name, []).append(row)
    return grouped


async def test_every_handler_persona_gets_exactly_two_demo_meetings(db: AsyncSession) -> None:
    """AC 6, stated as an equality rather than a lower bound.

    "At least two" would pass a migration that seeded the whole portfolio, and
    a demo diary with forty rows in it is not the surface FR-LOGIN-3 asks for.
    """
    grouped = await _meetings_by_handler(db)
    assert sorted(grouped) == sorted(seed_fixture.handler_personas())
    for name, rows in grouped.items():
        assert len(rows) == len(seed_fixture.DEMO_MEETING_TYPES), name


async def test_the_meetings_link_to_the_two_lowest_sorted_scoped_claims(db: AsyncSession) -> None:
    """The claims are the ones the persona can actually see (AD-7).

    Scope, not assignment: a meeting linked to a claim outside the handler's
    employer scope would be filtered out of their own list by `meeting_scope`
    and the AC would read as a bug that nothing here would catch.
    """
    grouped = await _meetings_by_handler(db)
    for name, rows in grouped.items():
        actual = [
            {"claim_id": row.claim_ref, "meeting_type": row.Meeting.meeting_type.value}
            for row in rows
        ]
        assert actual == seed_fixture.expected_meetings_for(name), name


async def test_the_two_types_are_the_documented_mapping_of_the_prototype_titles(
    db: AsyncSession,
) -> None:
    """The prototype's two free-text titles map onto two enum members.

    "RTW Check-In Call" and "Case Review — Reserve & Treatment Plan" are not
    among the scheduler's ten options, so they could not be seeded verbatim
    without making `meeting_type` free text. The mapping is `rtw_conference`
    and `claim_review_supervisor`, and the fuller wording survives in `notes` —
    which this asserts, because losing it is the silent half of the trade.
    """
    grouped = await _meetings_by_handler(db)
    for name, rows in grouped.items():
        first, second = (row.Meeting for row in rows)
        assert first.meeting_type is MeetingType.rtw_conference, name
        assert (first.notes or "").startswith("RTW Check-In Call"), name
        assert second.meeting_type is MeetingType.claim_review_supervisor, name
        assert (second.notes or "").startswith("Case Review"), name


async def test_the_seeded_participants_are_members_of_the_six(db: AsyncSession) -> None:
    """The JSONB array holds tokens the enum knows.

    There is no database constraint on the column's elements — the story's
    ruling — so "the seed cannot write a participant nothing can render" is a
    property of this assertion rather than of the schema.
    """
    rows = (await db.scalars(sa.select(Meeting))).all()
    assert rows
    for meeting in rows:
        assert meeting.participants
        for token in meeting.participants:
            assert MeetingParticipant(token)


async def test_every_seeded_meeting_is_dated_the_migration_run_and_not_done(
    db: AsyncSession,
) -> None:
    """The migration-run date, which on the day of the run is today.

    Asserted as "not in the future and not before this repository existed"
    rather than as `== CURRENT_DATE`, because the schema is built once per test
    module and a suite that straddles a midnight would otherwise fail for a
    reason that has nothing to do with the migration. What is worth pinning is
    that the date is real and that nothing arrives pre-completed — a seeded
    `is_done` would render two greyed-out cards on a first login.

    **The upper bound is the database's day, not Python's.** Migration 0033
    stamps the row with `CURRENT_DATE` precisely because "the two can disagree
    across a UTC midnight"; bounding it with `date.today()` reintroduced the
    disagreement the migration avoided, and failed for any developer far
    enough west of UTC running the suite after local afternoon.
    """
    today: date = (await db.execute(sa.select(sa.func.current_date()))).scalar_one()
    rows = (await db.scalars(sa.select(Meeting))).all()
    assert rows
    for meeting in rows:
        assert meeting.is_done is False
        assert date(2026, 1, 1) <= meeting.meeting_date <= today
        assert meeting.version == 1


async def test_only_handlers_hold_seeded_meetings(db: AsyncSession) -> None:
    """No supervisor, analyst or system actor gets one.

    The diary is the handler's surface; a supervisor with two meetings in a
    table nothing shows them would be rows nobody can reach, and the seeded
    `system` actor holding PHI-adjacent content would be worse.
    """
    roles = (
        await db.scalars(
            sa.select(AppUser.role)
            .select_from(Meeting)
            .join(AppUser, Meeting.app_user_id == AppUser.id)
            .distinct()
        )
    ).all()
    assert set(roles) == {UserRole.handler}


def test_the_migrations_enum_tuple_matches_the_python_enum() -> None:
    """0032's literal member tuple against `MeetingType`, order included.

    The migration writes its ten values out rather than importing the enum —
    the rule 0013, 0014 and 0026 established, because a migration is a frozen
    historical record. That leaves exactly one thing to check: that the two
    have not drifted. Order is part of it, because it is the type's label
    order in PostgreSQL and therefore what `ORDER BY meeting_type` sorts by.
    """
    assert tuple(member.value for member in MeetingType) == m0032.MEETING_TYPES
