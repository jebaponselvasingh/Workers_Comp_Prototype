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

**`requires_db` is a per-test decorator, not a `pytestmark`.** It was the
module marker, and the one test in this file that needs no database is
`test_the_migrations_enum_tuple_matches_the_python_enum` — the *only* guard
anywhere against migration 0032's frozen `MEETING_TYPES` drifting from
`MeetingType`, which is precisely the kind of check that has to run on a laptop
with no Postgres or it does not run at all.
"""

import importlib.util
from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from data.models import AppUser, Claim, Meeting
from data.models.enums import MeetingParticipant, MeetingType, UserRole
from tests import seed_fixture
from tests.conftest import requires_db, run_alembic

SERVER_ROOT = Path(__file__).resolve().parents[1]

#: Loaded by path rather than imported, `test_financial_tables_migration.py`'s
#: device: `data/versions/` is Alembic's script directory, not a package, and
#: the module names begin with a digit.
_MIGRATION = SERVER_ROOT / "data" / "versions" / "20260817_0032_meeting.py"
_spec = importlib.util.spec_from_file_location("_m0032", _MIGRATION)
assert _spec and _spec.loader
m0032 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m0032)

_SEED_MIGRATION = SERVER_ROOT / "data" / "versions" / "20260817_0033_seed_meetings.py"
_seed_spec = importlib.util.spec_from_file_location("_m0033", _SEED_MIGRATION)
assert _seed_spec and _seed_spec.loader
m0033 = importlib.util.module_from_spec(_seed_spec)
_seed_spec.loader.exec_module(m0033)


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


@requires_db
async def test_every_handler_persona_gets_exactly_two_demo_meetings(db: AsyncSession) -> None:
    """AC 6, stated as an equality rather than a lower bound.

    "At least two" would pass a migration that seeded the whole portfolio, and
    a demo diary with forty rows in it is not the surface FR-LOGIN-3 asks for.
    """
    grouped = await _meetings_by_handler(db)
    assert sorted(grouped) == sorted(seed_fixture.handler_personas())
    for name, rows in grouped.items():
        assert len(rows) == len(seed_fixture.DEMO_MEETING_TYPES), name


@requires_db
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


@requires_db
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


@requires_db
async def test_the_seeded_participants_are_the_two_documented_sets(db: AsyncSession) -> None:
    """The JSONB array holds the tokens the migration wrote, in enum order.

    There is no database constraint on the column's elements — the story's
    ruling — so "the seed cannot write a participant nothing can render" is a
    property of this assertion rather than of the schema. It used to assert
    non-emptiness and membership only, which is a claim about the *vocabulary*
    and not about this seed: swapping the two arrays between the two demo
    meetings passed it, and an RTW check-in attended by the NCM and the
    supervisor rather than the employee and HR is a different meeting.
    """
    expected = {
        MeetingType.rtw_conference: ["employee", "employer_hr"],
        MeetingType.claim_review_supervisor: ["ncm", "supervisor"],
    }
    rows = (await db.scalars(sa.select(Meeting))).all()
    assert rows
    for meeting in rows:
        for token in meeting.participants:
            assert MeetingParticipant(token)
        assert meeting.participants == expected[meeting.meeting_type], meeting.id


@requires_db
async def test_every_seeded_meeting_is_dated_the_migration_run_and_not_done(
    db: AsyncSession,
) -> None:
    """The migration-run date, which on the day of the run is today.

    **The window is one day wide, not eight months and widening.** The lower
    bound used to be the literal `2026-01-01`, which made the assertion weaker
    every day the project ran: by the time it mattered it would have accepted
    any date in the past year, including a hard-coded one — the exact mistake
    the migration's own docstring rejects. `CURRENT_DATE` at upgrade and
    `CURRENT_DATE` at assert are the same day unless the module's schema build
    straddled a midnight, which is the whole of the tolerance this needs.

    **Both bounds are the database's day, not Python's.** Migration 0033 stamps
    the row with `CURRENT_DATE` precisely because "the two can disagree across a
    UTC midnight"; bounding it with `date.today()` reintroduced the
    disagreement the migration avoided, and failed for any developer far
    enough west of UTC running the suite after local afternoon.

    Nothing arrives pre-completed either — a seeded `is_done` would render two
    greyed-out cards on a first login.
    """
    today: date = (await db.execute(sa.select(sa.func.current_date()))).scalar_one()
    rows = (await db.scalars(sa.select(Meeting))).all()
    assert rows
    for meeting in rows:
        assert meeting.is_done is False
        assert today - timedelta(days=1) <= meeting.meeting_date <= today
        assert meeting.version == 1


@requires_db
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


# --- the downgrade, which nothing exercised -----------------------------
#
# Last in the file deliberately: it rolls the schema back past `0033` and then
# forward again, and `seeded_db_url` is module-scoped, so anything after it
# would be reading a database this test has re-migrated. Every other module
# drops and rebuilds the schema in its own fixture, so the blast radius stops
# at the end of this file.


@requires_db
async def test_the_downgrade_removes_the_seed_and_spares_a_handlers_own_meeting(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """0033's `downgrade()`, run for the first time — with its bug set up.

    Two properties, and the second is the one that was broken. A handler's own
    `rtw_conference` against a seeded claim survives, because the discriminator
    is the two `notes` sentences only this revision writes. And the seeded rows
    go **even though the assignments they were derived from have changed** —
    `downgrade` used to re-run `_seeded_links`, which resolves each persona's
    employer scope at downgrade time, so a re-assignment silently left the real
    seeded rows behind and a persona that had dropped below two scoped claims
    made the helper raise `ValueError` and the rollback refuse to run at all.

    The re-assignment is simulated by emptying one handler's scope, which is
    both failure modes at once: the old code raises before it deletes anything.
    """
    handler = (
        await db.scalars(
            sa.select(AppUser).where(AppUser.role == UserRole.handler).order_by(AppUser.id)
        )
    ).first()
    assert handler is not None
    # Read into a local *before* anything expires the session: `db.expire_all()`
    # below would otherwise make `handler.id` a lazy refresh in a sync context.
    handler_id = handler.id
    seeded_claim = (
        await db.scalars(
            sa.select(Meeting.claim_id)
            .where(Meeting.app_user_id == handler_id)
            .order_by(Meeting.id)
        )
    ).first()
    assert seeded_claim is not None

    # A meeting of the handler's own, of a seeded *type* against a seeded
    # *claim*, differing only in its notes — the row `downgrade` must spare.
    await db.execute(
        sa.insert(Meeting).values(
            app_user_id=handler_id,
            claim_id=seeded_claim,
            meeting_type=MeetingType.rtw_conference,
            meeting_date=date(2099, 1, 1),
            notes="A handler's own RTW call, not the seed's.",
            participants=["employee"],
            is_done=False,
        )
    )
    # …and the re-scoping that made the old downgrade unable to find anything.
    # Captured first, because `upgrade` re-derives from the same table and the
    # restore below is what lets this module be left where it was found.
    assignments = (
        (
            await db.execute(
                sa.text(
                    "SELECT employer_id FROM user_employer_assignment WHERE user_id = :user_id"
                ),
                {"user_id": handler_id},
            )
        )
        .scalars()
        .all()
    )
    assert assignments
    await db.execute(
        sa.text("DELETE FROM user_employer_assignment WHERE user_id = :user_id"),
        {"user_id": handler_id},
    )
    await db.commit()

    seeded_before = (
        await db.scalars(sa.select(sa.func.count()).select_from(Meeting).where(_is_seeded()))
    ).one()
    assert seeded_before != 0

    run_alembic("downgrade", "0032_meeting")
    try:
        db.expire_all()
        seeded_after = (
            await db.scalars(sa.select(sa.func.count()).select_from(Meeting).where(_is_seeded()))
        ).one()
        assert seeded_after == 0
        survivors = (
            await db.scalars(sa.select(Meeting.notes).where(Meeting.app_user_id == handler_id))
        ).all()
        assert list(survivors) == ["A handler's own RTW call, not the seed's."]
    finally:
        # Put the scope back before re-migrating: `upgrade` still re-derives
        # (correctly — it is writing the rows it derives), and its own tripwire
        # refuses a persona with fewer than two scoped claims.
        for employer_id in assignments:
            await db.execute(
                sa.text(
                    "INSERT INTO user_employer_assignment (user_id, employer_id) "
                    "VALUES (:user_id, :employer_id)"
                ),
                {"user_id": handler_id, "employer_id": employer_id},
            )
        await db.commit()
        run_alembic("upgrade", "head")


def _is_seeded() -> Any:
    """The predicate `downgrade` uses, restated — the test's own oracle."""
    return sa.tuple_(Meeting.meeting_type, Meeting.notes).in_(
        [(demo["meeting_type"], demo["notes"]) for demo in m0033.DEMO_MEETINGS]
    )
