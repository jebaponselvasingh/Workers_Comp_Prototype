"""Story 4.1 — the three meeting commands and their list, against a database.

The pure half is small enough to live here too: `meeting_status` is one
comparison and `decode_cursor` is a parser, and both are exercised without a
session at the top of the file. What needs a transaction is everything the I/O
matrix is actually about — that the row and its audit event land together, that
a claim outside the caller's book is refused before anything is written, that a
stale ✓ answers 409 with the fresh entity attached, and that one handler's
diary is invisible to another.

Driven through the app for the contract tests and through the commands for the
atomicity ones, because a rollback is not observable over HTTP —
`test_injury_commands.py`'s division.

**These tests mutate the seeded portfolio.** The module-scoped `seeded_db_url`
fixture rebuilds the schema for this file, so the mutations are contained here
— but they persist *between* tests in this module, which is why nothing below
hardcodes a version number or a row count that the seed alone decides: every
helper reads the current state first, the way a client does.
"""

import base64
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, AuditEvent, Meeting, TimelineEvent
from data.models.enums import MeetingParticipant, MeetingType
from data.repositories.identity import employer_ids_for
from services.claims.edit import EditNotPermitted, InvalidPatch
from services.claims.meetings import (
    COMPLETE_ACTION,
    CREATE_ACTION,
    DELETE_ACTION,
    ENTITY,
    MAX_NOTES_LENGTH,
    MAX_PAGE_LIMIT,
    Cursor,
    InvalidCursor,
    MeetingClaimNotVisible,
    MeetingNotVisible,
    StaleMeeting,
    complete_meeting,
    create_meeting,
    decode_cursor,
    delete_meeting,
    encode_cursor,
    list_meetings,
    normalise_new_meeting,
    normalise_participants,
)
from services.derivations import MeetingStatus, meeting_status, utc_today
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

KAYA = ("Kaya Johnson", "handler")
SARAH = ("Sarah Williams", "handler")
JENNIFER = ("Jennifer Park", "supervisor")
ANALYST = ("David Bline", "analyst")

TODAY = date(2026, 8, 17)


# --- the derivation, without a database ---------------------------------


@dataclass(frozen=True)
class FakeMeeting:
    """A meeting as the horizon rule sees it — a date and a flag, nothing else."""

    meeting_date: date
    is_done: bool = False


@pytest.mark.parametrize(
    ("meeting", "expected"),
    [
        (FakeMeeting(TODAY + timedelta(days=1)), MeetingStatus.upcoming),
        # Today counts as ahead: the column is a calendar date and judging "has
        # it happened yet" from the optional wall-clock time would need a
        # timezone this console does not collect.
        (FakeMeeting(TODAY), MeetingStatus.upcoming),
        (FakeMeeting(TODAY - timedelta(days=1)), MeetingStatus.done),
        # The tick wins over the calendar in both directions.
        (FakeMeeting(TODAY + timedelta(days=1), is_done=True), MeetingStatus.done),
        (FakeMeeting(TODAY - timedelta(days=1), is_done=True), MeetingStatus.done),
    ],
)
def test_the_horizon_rule_is_the_date_and_the_tick(
    meeting: FakeMeeting, expected: MeetingStatus
) -> None:
    """AD-10's single computer, asserted at its boundaries.

    The prototype writes `m.date >= today && !m.done` inside the component that
    draws the card; this is that rule with the boundary pinned, so a later
    surface asking the registry cannot disagree with the one drawing the card.
    """
    assert meeting_status.build(None).of(meeting, TODAY) is expected  # type: ignore[arg-type]


def test_participants_are_stored_in_the_vocabularys_order_and_deduplicated() -> None:
    """The tags read left to right on every card, so the order is the enum's.

    A request that happened to send its checkboxes in a different order would
    otherwise render two identical meetings with their tags shuffled, and an
    unchanged set would produce a different audit diff each time it was
    written.
    """
    scrambled = [
        MeetingParticipant.attorney,
        MeetingParticipant.employee,
        MeetingParticipant.attorney,
        MeetingParticipant.ncm,
    ]
    assert normalise_participants(scrambled) == (
        MeetingParticipant.employee,
        MeetingParticipant.ncm,
        MeetingParticipant.attorney,
    )


def test_an_unknown_participant_is_refused_rather_than_dropped() -> None:
    """`MeetingParticipant`'s promise: refused at the boundary.

    Pydantic already refuses one on the HTTP path, so this is about the caller
    AD-16 anticipates — an Epic 6 agent tool calling the command directly.
    A silent filter would write a set the caller never sent, record *that* set
    in the audit diff, and leave the next list read raising on the token it
    could not decode.
    """
    with pytest.raises(InvalidPatch) as refusal:
        normalise_participants([MeetingParticipant.employee, "chief_of_staff"])

    assert "participants" in str(refusal.value)
    # And the caller's own text is not echoed back at them (AD-11).
    assert "chief_of_staff" not in str(refusal.value)


# --- the cursor, without a database -------------------------------------


NOON = time(12, 0)


@pytest.mark.parametrize("day", [None, TODAY])
def test_a_cursor_round_trips(day: date | None) -> None:
    cursor = Cursor(last_date=TODAY, last_time=NOON, last_id=42, limit=25, issued_on=TODAY, day=day)
    assert decode_cursor(encode_cursor(cursor), TODAY) == cursor


def test_a_cursor_carrying_an_offset_time_is_refused_at_the_boundary() -> None:
    """`time.fromisoformat("12:00:00+05:00")` parses happily and passes every
    bound — and the column is a naive `Time`, so the offset would reach the
    row-value comparison and raise inside the driver instead of answering 400
    here. `notes.py::decode_cursor` guards its datetime for this and tests it;
    this is the same guard one table over (review, 2026-08-17)."""
    forged = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "d": TODAY.isoformat(),
                    "t": "12:00:00+05:00",
                    "i": 1,
                    "l": 25,
                    "s": TODAY.isoformat(),
                    "g": None,
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )

    with pytest.raises(InvalidCursor):
        decode_cursor(forged, TODAY)


def test_a_cursor_issued_before_the_day_was_recorded_is_refused() -> None:
    """The `day` member is Story 4.2's review fix, and a cursor without it is
    refused rather than read as "the whole diary".

    Defaulting the missing key would silently turn a day-filtered walk into a
    whole-book one at the same position, which is exactly the failure the member
    exists to prevent — so the absence is unreadable, like the sort time's.
    """
    stale = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "d": TODAY.isoformat(),
                    "t": NOON.isoformat(),
                    "i": 1,
                    "l": 25,
                    "s": TODAY.isoformat(),
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )

    with pytest.raises(InvalidCursor):
        decode_cursor(stale, TODAY)


def test_a_cursor_issued_before_the_sort_time_existed_is_refused() -> None:
    """Story 4.2 made the sort key `(date, time, id)`, so a two-member cursor
    names a position in an ordering that no longer exists.

    Refused rather than defaulted: substituting midnight for the missing member
    would resume the walk at whatever row happened to sort there, silently
    skipping or repeating the ones between. The SPA drops the cursor and
    reloads from page one, which is what a 400 asks it to do.
    """
    stale = (
        base64.urlsafe_b64encode(
            json.dumps({"d": TODAY.isoformat(), "i": 1, "l": 25, "s": TODAY.isoformat()}).encode()
        )
        .decode()
        .rstrip("=")
    )

    with pytest.raises(InvalidCursor):
        decode_cursor(stale, TODAY)


@pytest.mark.parametrize(
    "raw",
    [
        "not-base64-at-all!!",
        # Valid base64 of JSON that is not a cursor.
        "e30",
        # A forged page size past the route's ceiling — the cursor is *reused*
        # when the request omits a limit, so this is the way round the
        # validator if it were not bounded here.
        encode_cursor(Cursor(TODAY, NOON, 1, 100_000, TODAY, None)),
        # A position that cannot exist.
        encode_cursor(Cursor(TODAY, NOON, 0, 25, TODAY, None)),
        # Dated in the future: no cursor this service issued can name one.
        encode_cursor(Cursor(TODAY, NOON, 1, 25, TODAY + timedelta(days=1), None)),
        # Older than MAX_CURSOR_AGE.
        encode_cursor(Cursor(TODAY, NOON, 1, 25, TODAY - timedelta(days=8), None)),
    ],
)
def test_a_cursor_that_does_not_describe_this_list_is_refused(raw: str) -> None:
    """Never a silent page one — that turns a client bug into an infinite
    "Show more" that re-appends the same meetings for ever."""
    with pytest.raises(InvalidCursor):
        decode_cursor(raw, TODAY)


# --- plumbing -----------------------------------------------------------


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def context_for(db: AsyncSession, name: str, role: str) -> CallerContext:
    user = (
        await db.scalars(sa.select(AppUser).where(AppUser.name == name, AppUser.role == role))
    ).one()
    return CallerContext(
        user_id=user.id,
        role=user.role,
        employer_ids=ALL_EMPLOYERS if user.scope_all else await employer_ids_for(db, user.id),
    )


@asynccontextmanager
async def make_client(db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(database_url=db_url, env="e2e"))  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def login_as(client: httpx.AsyncClient, name: str, role: str) -> None:
    personas = (await client.get("/personas")).json()["items"]
    match = [p for p in personas if p["name"] == name and p["role"] == role]
    assert len(match) == 1
    assert (await client.post("/auth/login", json={"personaId": match[0]["id"]})).status_code == 200


def a_claim_of(persona: tuple[str, str]) -> str:
    return sorted(seed_fixture.expected_claim_ids(*persona))[0]


def a_claim_outside(persona: tuple[str, str]) -> str:
    """A claim id that exists in the portfolio but not in this persona's book."""
    visible = seed_fixture.expected_claim_ids(*persona)
    outside = sorted({claim["claim_id"] for claim in seed_fixture.seed()["claims"]} - visible)
    assert outside, f"{persona[0]} sees the whole portfolio; no out-of-scope claim to use"
    return str(outside[0])


def a_meeting_body(claim_id: str | None = None, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "claimId": claim_id,
        "meetingType": MeetingType.rtw_conference.value,
        # 2099, not a date a month or two out: the route derives `status` from
        # `utc_today()`, so a body dated 2026-09-01 asserted as `upcoming` was a
        # test that would flip to `done` on 2026-09-02 — a failure about the
        # calendar rather than about the code. The e2e spec fixes its date the
        # same way, and the vitest fixtures theirs.
        "meetingDate": "2099-09-01",
        "meetingTime": "10:00",
        "location": "Plant office",
        "notes": "Confirm light-duty availability.",
        "participants": [MeetingParticipant.employee.value, MeetingParticipant.ncm.value],
    }
    body.update(overrides)
    return body


async def audit_rows(db: AsyncSession, action: str, entity_id: str) -> list[AuditEvent]:
    rows = await db.scalars(
        sa.select(AuditEvent).where(AuditEvent.action == action, AuditEvent.entity_id == entity_id)
    )
    return list(rows.all())


# --- create -------------------------------------------------------------


async def test_a_handler_schedules_a_meeting_and_it_comes_back_with_its_status(
    seeded_db_url: str,
) -> None:
    """The happy path (AC 1, AC 3): 201, the row, and the server's `status`."""
    claim_id = a_claim_of(KAYA)
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.post("/claims-diary/meetings", json=a_meeting_body(claim_id))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["claimId"] == claim_id
    assert body["workerName"]
    assert body["meetingType"] == MeetingType.rtw_conference.value
    assert body["participants"] == ["employee", "ncm"]
    assert body["isDone"] is False
    assert body["version"] == 1
    # A 2099-09-01 meeting is ahead of the day the suite runs — on every day
    # the suite will ever run — and `status` is on the wire rather than
    # something the client works out from the date.
    assert body["status"] == MeetingStatus.upcoming.value


async def test_a_body_without_a_date_is_refused_by_the_schema(seeded_db_url: str) -> None:
    """AC 2's server half — the field is named, and nothing is written.

    FastAPI's own body validation answers this, which is why there is no
    "date is required" check in the command duplicating it. The SPA renders
    the refusal inline at the date input rather than in a native dialog.
    """
    body = a_meeting_body(a_claim_of(KAYA))
    del body["meetingDate"]
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.post("/claims-diary/meetings", json=body)

    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["type"] == "/problems/validation-error"
    assert any("meetingDate" in str(error["loc"]) for error in problem["errors"])


async def test_a_claim_outside_the_book_is_the_same_404_as_one_that_does_not_exist(
    seeded_db_url: str, db: AsyncSession
) -> None:
    """AD-7's single answer, and no row and no audit event behind it."""
    before = await db.scalar(sa.select(sa.func.count()).select_from(Meeting))
    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        outside = await client.post(
            "/claims-diary/meetings", json=a_meeting_body(a_claim_outside(SARAH))
        )
        absent = await client.post("/claims-diary/meetings", json=a_meeting_body("WC-99999"))

    assert outside.status_code == absent.status_code == 404
    assert outside.json()["type"] == absent.json()["type"] == "/problems/meeting-claim-not-found"
    assert outside.json()["title"] == absent.json()["title"]
    assert await db.scalar(sa.select(sa.func.count()).select_from(Meeting)) == before


@pytest.mark.parametrize("persona", [JENNIFER, ANALYST])
async def test_only_a_handler_may_schedule(seeded_db_url: str, persona: tuple[str, str]) -> None:
    """403, raised before anything is looked up.

    So the answer is identical for a claim in the caller's book and one that
    does not exist — a supervisor learns nothing from it.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *persona)
        response = await client.post("/claims-diary/meetings", json=a_meeting_body("WC-99999"))

    assert response.status_code == 403, response.text
    assert response.json()["type"] == "/problems/edit-not-permitted"


async def test_free_text_past_its_cap_is_a_422_naming_the_field(seeded_db_url: str) -> None:
    """And the layer that refuses it is the schema, not the command.

    `NewMeetingRequest` declares `max_length` on `notes`, so Pydantic answers
    first with `/problems/validation-error`. Asserting the status alone passed
    against either layer and hid which one was doing the work — which mattered,
    because the route's own `responses=` documentation claimed the other one.
    The command's identical cap is still enforced, for the caller that is not
    this schema; `test_the_command_enforces_its_own_caps` is where that lives.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.post(
            "/claims-diary/meetings",
            json=a_meeting_body(a_claim_of(KAYA), notes="x" * (MAX_NOTES_LENGTH + 1)),
        )

    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["type"] == "/problems/validation-error"
    assert any("notes" in str(error["loc"]) for error in problem["errors"])


async def test_free_text_the_column_cannot_store_is_the_commands_own_422(
    seeded_db_url: str,
) -> None:
    """The one refusal that really does reach the route as `/problems/invalid-patch`.

    A control character passes the schema — it is a string, and it is short —
    and is refused by `_optional_text`, so this is the branch the route's 422
    documentation is actually about. PostgreSQL `text` cannot hold a NUL and
    asyncpg raises rather than truncating, which reached the request as a 500
    before the check existed.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.post(
            "/claims-diary/meetings",
            json=a_meeting_body(a_claim_of(KAYA), notes="Confirm light duty\x00now"),
        )

    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["type"] == "/problems/invalid-patch"
    assert "notes" in problem["detail"]
    # The refusal names the field and the rule and never echoes the value.
    assert "Confirm light duty" not in problem["detail"]


def test_the_command_enforces_its_own_caps_whatever_the_schema_did() -> None:
    """AD-16: the caps are the command's, not only the wire model's.

    Called directly rather than over HTTP, because that is the whole point —
    an Epic 6 agent tool never passes through `NewMeetingRequest`.
    """
    with pytest.raises(InvalidPatch) as refusal:
        normalise_new_meeting(
            MeetingType.rtw_conference,
            TODAY,
            None,
            None,
            "x" * (MAX_NOTES_LENGTH + 1),
            [],
        )

    assert "notes" in str(refusal.value)


async def test_creating_writes_one_audit_event_in_the_same_transaction(db: AsyncSession) -> None:
    """AD-4, asserted through the command because a transaction is not
    observable over HTTP."""
    ctx = await context_for(db, *KAYA)
    claim_id = a_claim_of(KAYA)

    view = await create_meeting(
        db,
        ctx,
        claim_business_id=claim_id,
        meeting_type=MeetingType.ime_preparation,
        meeting_date=TODAY,
        meeting_time=time(9, 30),
        location="Teams",
        notes="Prepare the IME bundle.",
        participants=[MeetingParticipant.employee],
        as_of=TODAY,
    )

    events = await audit_rows(db, CREATE_ACTION, str(view.id))
    assert len(events) == 1
    event = events[0]
    assert event.entity == ENTITY
    assert event.actor_id == ctx.user_id
    assert event.before is None
    assert event.after is not None
    # The claim's *business* id, so an audit row for a meeting whose claim is
    # later purged still names something Story 8.1 can find.
    assert event.after["claim_id"] == claim_id
    assert event.after["meeting_type"] == MeetingType.ime_preparation.value
    assert event.after["participants"] == ["employee"]


async def test_scheduling_a_meeting_emits_no_timeline_event(db: AsyncSession) -> None:
    """AD-12: the claim timeline does not surface meetings.

    A meeting changes no column of `claim`, and a log line saying otherwise
    would put one handler's diary into another's view of the case file.
    """
    ctx = await context_for(db, *KAYA)
    before = await db.scalar(sa.select(sa.func.count()).select_from(TimelineEvent))

    await create_meeting(
        db,
        ctx,
        claim_business_id=a_claim_of(KAYA),
        meeting_type=MeetingType.other,
        meeting_date=TODAY,
        as_of=TODAY,
    )

    assert await db.scalar(sa.select(sa.func.count()).select_from(TimelineEvent)) == before


async def test_the_command_refuses_a_supervisor_before_it_reads_anything(db: AsyncSession) -> None:
    ctx = await context_for(db, *JENNIFER)
    with pytest.raises(EditNotPermitted):
        await create_meeting(
            db,
            ctx,
            claim_business_id=a_claim_of(KAYA),
            meeting_type=MeetingType.other,
            meeting_date=TODAY,
        )


async def test_the_command_refuses_free_text_the_column_cannot_hold(db: AsyncSession) -> None:
    """A NUL in `text` reaches asyncpg as an error rather than a truncation,
    which surfaced as a 500 in Story 2.3 before `require_text` refused it."""
    ctx = await context_for(db, *KAYA)
    with pytest.raises(InvalidPatch):
        await create_meeting(
            db,
            ctx,
            claim_business_id=a_claim_of(KAYA),
            meeting_type=MeetingType.other,
            meeting_date=TODAY,
            location="plant\x00office",
        )


async def test_an_unlinked_meeting_is_allowed_and_carries_no_claim(db: AsyncSession) -> None:
    """The ERD's `CLAIM |o--o{ MEETING`, exercised.

    The scheduler always opens from a selected claim, so this is not a state
    the SPA produces — the column admits null because a touchpoint that is
    genuinely not about one file is a real thing to record, and a NOT NULL
    would make it unrecordable rather than unrepresented.
    """
    ctx = await context_for(db, *KAYA)
    view = await create_meeting(
        db,
        ctx,
        claim_business_id=None,
        meeting_type=MeetingType.claim_review_supervisor,
        meeting_date=TODAY,
        as_of=TODAY,
    )
    assert view.claim_business_id is None
    assert view.worker_name is None
    assert view.status is MeetingStatus.upcoming


# --- list ---------------------------------------------------------------


async def test_a_handler_sees_the_two_seeded_meetings_and_nothing_else(
    seeded_db_url: str,
) -> None:
    """AC 6 through the endpoint, and the isolation half of AD-7 with it."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        response = await client.get("/claims-diary/meetings")

    assert response.status_code == 200, response.text
    body = response.json()
    expected = seed_fixture.expected_meetings_for(SARAH[0])
    assert body["total"] == len(expected)
    assert [
        {"claim_id": item["claimId"], "meeting_type": item["meetingType"]} for item in body["items"]
    ] == expected


async def test_one_handlers_diary_is_invisible_to_another(db: AsyncSession) -> None:
    """The owner half of `meeting_scope`, which `employer_scope` alone would
    not give: the seed puts two handlers on John Deere."""
    sarah = await context_for(db, *SARAH)
    kaya = await context_for(db, *KAYA)

    created = await create_meeting(
        db,
        sarah,
        claim_business_id=a_claim_of(SARAH),
        meeting_type=MeetingType.settlement_discussion,
        meeting_date=TODAY,
        as_of=TODAY,
    )

    mine = await list_meetings(db, sarah, as_of=TODAY)
    theirs = await list_meetings(db, kaya, as_of=TODAY)
    assert created.id in {item.id for item in mine.items}
    assert created.id not in {item.id for item in theirs.items}


async def test_the_list_is_ordered_by_date_then_time_then_id(db: AsyncSession) -> None:
    """Total, not merely sorted — Story 3.5's learning, with Story 4.2's clock.

    Two meetings on one day is the ordinary case, so a page that ended inside
    the tie would repeat one row and drop the other. The *time* is the middle
    member: a day's meetings have to read down the clock, and the same ordering
    then serves the whole diary and the one-day filter without a second sort.

    The three below are inserted out of clock order deliberately, so an
    ordering that fell back to insertion order would fail here.
    """
    ctx = await context_for(db, *KAYA)
    same_day = TODAY + timedelta(days=30)
    for at in (time(15, 0), time(9, 0), time(11, 30)):
        await create_meeting(
            db,
            ctx,
            claim_business_id=a_claim_of(KAYA),
            meeting_type=MeetingType.other,
            meeting_date=same_day,
            meeting_time=at,
            as_of=TODAY,
        )

    page = await list_meetings(db, ctx, limit=200, as_of=TODAY)
    keys = [(item.meeting_date, item.meeting_time or time(0, 0), item.id) for item in page.items]
    assert keys == sorted(keys)

    that_day = [item.meeting_time for item in page.items if item.meeting_date == same_day]
    assert that_day == [time(9, 0), time(11, 30), time(15, 0)]


async def test_an_all_day_meeting_sorts_at_the_head_of_its_day_and_pages(
    db: AsyncSession,
) -> None:
    """`COALESCE`, not `NULLS FIRST`, and the paging half is why.

    Both orderings put an untimed meeting first. Only the coalesced one keeps
    the keyset comparison NULL-free — a NULL member makes `tuple_(…) > tuple_(…)`
    evaluate to NULL rather than false, so the page *after* an all-day meeting
    would come back empty and the walk would stop mid-list with no error.
    """
    ctx = await context_for(db, *KAYA)
    day = TODAY + timedelta(days=45)
    for at in (time(8, 0), None):
        await create_meeting(
            db,
            ctx,
            claim_business_id=a_claim_of(KAYA),
            meeting_type=MeetingType.other,
            meeting_date=day,
            meeting_time=at,
            as_of=TODAY,
        )

    page = await list_meetings(db, ctx, day=day, as_of=TODAY)
    assert [item.meeting_time for item in page.items] == [None, time(8, 0)]

    # And the cursor issued *at* the all-day row resumes correctly.
    first = await list_meetings(db, ctx, day=day, limit=1, as_of=TODAY)
    assert first.next_cursor is not None
    second = await list_meetings(db, ctx, day=day, cursor=first.next_cursor, as_of=TODAY)
    assert [item.id for item in second.items] == [page.items[1].id]


async def test_the_day_filter_narrows_the_page_and_its_total_but_not_the_count(
    db: AsyncSession,
) -> None:
    """AC 1's server half: one read serves the summary *and* the greeting.

    `items` and `total` describe the day the caller asked for — the summary's
    "📅 Today's Meetings (N)". `upcomingCount` describes the whole book, which
    is what the greeting's sentence claims, so it must not move with the
    filter.
    """
    ctx = await context_for(db, *KAYA)
    day = TODAY + timedelta(days=60)
    for _ in range(2):
        await create_meeting(
            db,
            ctx,
            claim_business_id=a_claim_of(KAYA),
            meeting_type=MeetingType.other,
            meeting_date=day,
            meeting_time=time(9, 0),
            as_of=TODAY,
        )

    whole = await list_meetings(db, ctx, limit=200, as_of=TODAY)
    filtered = await list_meetings(db, ctx, day=day, limit=200, as_of=TODAY)

    assert filtered.total == 2
    assert {item.meeting_date for item in filtered.items} == {day}
    assert filtered.total < whole.total
    assert filtered.upcoming_count == whole.upcoming_count


async def test_the_day_is_the_clock_the_page_is_judged_against(db: AsyncSession) -> None:
    """The timezone skew, pinned (review, 2026-08-17).

    `day` is the **viewer's local** calendar day and `as_of` fell back to
    `utc_today()`, so the two could name different days — and routinely did: a
    Pacific handler after 17:00 local is on the day *before* the server's UTC
    date. Every meeting on the day they asked about then failed
    `meeting_date >= as_of`, came back `done`, rendered in the done tone with no
    ✓ control, and was left out of the greeting's count. Nothing was wrong with
    either comparison; there were two of them, against two notions of today.

    Reproduced by asking about a day *behind* the one the service would resolve
    on its own — which is exactly the shape of the skew, without a timezone or a
    frozen clock.
    """
    ctx = await context_for(db, *KAYA)
    # A day *behind* the one the service resolves for itself — the Pacific
    # evening, expressed without a timezone. Taken from `utc_today()` rather
    # than from this module's `TODAY` precisely so the gap is real whatever day
    # the suite runs on.
    local_day = utc_today() - timedelta(days=1)

    created = await create_meeting(
        db,
        ctx,
        claim_business_id=a_claim_of(KAYA),
        meeting_type=MeetingType.other,
        meeting_date=local_day,
        meeting_time=time(17, 30),
        as_of=local_day,
    )

    # `as_of` deliberately omitted: this is the router's call, and the point is
    # that `day` alone now settles the horizon.
    page = await list_meetings(db, ctx, day=local_day, limit=MAX_PAGE_LIMIT)
    shown = next(item for item in page.items if item.id == created.id)
    assert shown.status is MeetingStatus.upcoming
    assert page.upcoming_count != 0

    # And the day still only *narrows*: judged against the server's own day, the
    # same meeting is behind the horizon — which is what the unfiltered Meetings
    # sub-tab shows, and is the answer for a caller asking about that day.
    later = await list_meetings(db, ctx, day=local_day, as_of=utc_today(), limit=MAX_PAGE_LIMIT)
    assert next(item for item in later.items if item.id == created.id).status is MeetingStatus.done


async def test_a_cursor_from_one_day_cannot_page_another_list(db: AsyncSession) -> None:
    """A day-filtered cursor replayed without the day is refused (review).

    Left unchecked it answers 200 and pages the *whole book* from that position,
    skipping everything that sorts earlier — silently, which is the failure
    `queue.py` compares `queue_filter` and `stage` to prevent, and which the
    route's own 400 description already promised to refuse.
    """
    ctx = await context_for(db, *KAYA)
    day = TODAY + timedelta(days=75)
    for at in (time(9, 0), time(10, 0)):
        await create_meeting(
            db,
            ctx,
            claim_business_id=a_claim_of(KAYA),
            meeting_type=MeetingType.other,
            meeting_date=day,
            meeting_time=at,
            as_of=TODAY,
        )

    first = await list_meetings(db, ctx, day=day, limit=1, as_of=TODAY)
    assert first.next_cursor is not None

    with pytest.raises(InvalidCursor):
        await list_meetings(db, ctx, cursor=first.next_cursor, as_of=TODAY)

    # …and the whole-book cursor cannot be narrowed after the fact either.
    whole = await list_meetings(db, ctx, limit=1, as_of=TODAY)
    assert whole.next_cursor is not None
    with pytest.raises(InvalidCursor):
        await list_meetings(db, ctx, cursor=whole.next_cursor, day=day, as_of=TODAY)

    # The matching replay still works, so the guard refuses the wrong list
    # rather than every continuation.
    whole_day = await list_meetings(db, ctx, day=day, limit=MAX_PAGE_LIMIT, as_of=TODAY)
    second = await list_meetings(db, ctx, day=day, cursor=first.next_cursor, as_of=TODAY)
    assert [item.id for item in second.items] == [item.id for item in whole_day.items[1:]]


async def test_the_two_renderings_of_the_upcoming_rule_agree(db: AsyncSession) -> None:
    """The Python classifier and the SQL predicate, over the boundary matrix.

    Two independent restatements of "upcoming" is how a console starts
    disagreeing with itself — the greeting says three and the list below shows
    two, and both look right. `meeting_horizon.py` holds them adjacent; this
    holds them equal, across yesterday / today / tomorrow × done / not done.

    Counted over the caller's *whole* book rather than over the six rows
    inserted here, so a predicate that quietly dropped the scope or the
    coalesce would show up as a mismatch too.
    """
    ctx = await context_for(db, *SARAH)
    for offset in (-1, 0, 1):
        for done in (False, True):
            created = await create_meeting(
                db,
                ctx,
                claim_business_id=a_claim_of(SARAH),
                meeting_type=MeetingType.other,
                meeting_date=TODAY + timedelta(days=offset),
                as_of=TODAY,
            )
            if done:
                await complete_meeting(
                    db, ctx, created.id, expected_version=created.version, as_of=TODAY
                )

    page = await list_meetings(db, ctx, limit=MAX_PAGE_LIMIT, as_of=TODAY)
    assert page.total == len(page.items), "the whole book has to fit one page for this count"

    in_python = sum(1 for item in page.items if item.status is MeetingStatus.upcoming)
    assert page.upcoming_count == in_python


async def test_paging_visits_every_meeting_exactly_once(db: AsyncSession) -> None:
    """The keyset cursor, walked to exhaustion.

    `total` is the whole list rather than the page, so it is also what the walk
    is checked against.
    """
    ctx = await context_for(db, *KAYA)
    first = await list_meetings(db, ctx, limit=1, as_of=TODAY)

    seen = [item.id for item in first.items]
    cursor = first.next_cursor
    while cursor is not None:
        page = await list_meetings(db, ctx, cursor=cursor, as_of=TODAY)
        seen.extend(item.id for item in page.items)
        cursor = page.next_cursor

    assert len(seen) == len(set(seen)) == first.total


async def test_a_bad_cursor_is_a_400_and_never_a_silent_page_one(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.get("/claims-diary/meetings", params={"cursor": "nonsense!!"})

    assert response.status_code == 400, response.text
    assert response.json()["type"] == "/problems/invalid-cursor"


async def test_the_list_answers_for_whoever_holds_the_cookie(seeded_db_url: str) -> None:
    """AD-7: there is no scope-shaped parameter, and an invented one is ignored
    rather than refused — a 422 would tell a caller which names exist."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        plain = await client.get("/claims-diary/meetings")
        invented = await client.get("/claims-diary/meetings", params={"userId": 1})

    assert plain.json() == invented.json()
    assert plain.headers["cache-control"] == "no-store"


# --- complete -----------------------------------------------------------


async def _own_meeting(client: httpx.AsyncClient) -> dict[str, Any]:
    listing = await client.get("/claims-diary/meetings")
    assert listing.status_code == 200, listing.text
    items: list[dict[str, Any]] = listing.json()["items"]
    assert items, "the seed should have given this persona meetings"
    return items[0]


async def test_completing_bumps_the_version_and_flips_the_status(seeded_db_url: str) -> None:
    """AC 4. A completed meeting is `done` whatever its date, which is why the
    card's styling follows one server field rather than two combined."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        meeting = await _own_meeting(client)
        response = await client.patch(
            f"/claims-diary/meetings/{meeting['id']}",
            json={"expectedVersion": meeting["version"]},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["isDone"] is True
    assert body["version"] == meeting["version"] + 1
    assert body["status"] == MeetingStatus.done.value


async def test_a_stale_completion_is_a_409_carrying_the_fresh_meeting(
    seeded_db_url: str,
) -> None:
    """The conflict round trip, and the entity a client renders from it.

    `version + 1` rather than `version - 1`: `expectedVersion` has `ge=1` on
    the wire, so a decrement on a version-1 row is a 422 and never reaches the
    command (Epic 3's learning).
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        meeting = await _own_meeting(client)
        response = await client.patch(
            f"/claims-diary/meetings/{meeting['id']}",
            json={"expectedVersion": meeting["version"] + 1},
        )

    assert response.status_code == 409, response.text
    problem = response.json()
    assert problem["type"] == "/problems/stale-write"
    assert problem["meeting"]["id"] == meeting["id"]
    assert problem["meeting"]["version"] == meeting["version"]
    # The whole entity, not a status: the SPA installs it and re-renders the
    # card from one object, which is what the 200 path does too (AD-9).
    assert problem["meeting"]["meetingType"] == meeting["meetingType"]


async def test_completing_twice_is_a_conflict_rather_than_a_second_audit_event(
    db: AsyncSession,
) -> None:
    """AD-4's extra rung for a lifecycle move.

    A version alone would let a client holding a stale-but-matching version
    complete a meeting somebody already completed, emitting a second audit
    event for a decision that was made once.
    """
    ctx = await context_for(db, *KAYA)
    created = await create_meeting(
        db,
        ctx,
        claim_business_id=a_claim_of(KAYA),
        meeting_type=MeetingType.physician_consultation,
        meeting_date=TODAY,
        as_of=TODAY,
    )
    done = await complete_meeting(
        db, ctx, created.id, expected_version=created.version, as_of=TODAY
    )

    with pytest.raises(StaleMeeting) as refused:
        await complete_meeting(db, ctx, created.id, expected_version=created.version, as_of=TODAY)

    assert refused.value.fresh.version == done.version
    assert len(await audit_rows(db, COMPLETE_ACTION, str(created.id))) == 1


async def test_completing_writes_a_before_and_after_diff(db: AsyncSession) -> None:
    ctx = await context_for(db, *KAYA)
    created = await create_meeting(
        db,
        ctx,
        claim_business_id=a_claim_of(KAYA),
        meeting_type=MeetingType.litigation_prep,
        meeting_date=TODAY,
        as_of=TODAY,
    )
    await complete_meeting(db, ctx, created.id, expected_version=created.version, as_of=TODAY)

    event = (await audit_rows(db, COMPLETE_ACTION, str(created.id)))[0]
    assert event.before == {"claim_id": created.claim_business_id, "is_done": False}
    assert event.after == {"claim_id": created.claim_business_id, "is_done": True}


async def test_completing_somebody_elses_meeting_is_the_absent_404(db: AsyncSession) -> None:
    sarah = await context_for(db, *SARAH)
    kaya = await context_for(db, *KAYA)
    created = await create_meeting(
        db,
        sarah,
        claim_business_id=a_claim_of(SARAH),
        meeting_type=MeetingType.other,
        meeting_date=TODAY,
        as_of=TODAY,
    )

    with pytest.raises(MeetingNotVisible):
        await complete_meeting(db, kaya, created.id, expected_version=created.version)
    with pytest.raises(MeetingNotVisible):
        await complete_meeting(db, kaya, 10_000_000, expected_version=1)


@pytest.mark.parametrize("persona", [JENNIFER, ANALYST])
async def test_only_a_handler_may_complete_or_delete(
    seeded_db_url: str, persona: tuple[str, str]
) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *persona)
        patched = await client.patch("/claims-diary/meetings/1", json={"expectedVersion": 1})
        deleted = await client.delete("/claims-diary/meetings/1", params={"expectedVersion": 1})

    assert patched.status_code == deleted.status_code == 403
    assert patched.json()["type"] == deleted.json()["type"] == "/problems/edit-not-permitted"


# --- delete -------------------------------------------------------------


async def test_deleting_answers_204_and_removes_the_row(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        created = await client.post(
            "/claims-diary/meetings", json=a_meeting_body(a_claim_of(SARAH))
        )
        assert created.status_code == 201, created.text
        meeting = created.json()

        response = await client.delete(
            f"/claims-diary/meetings/{meeting['id']}",
            params={"expectedVersion": meeting["version"]},
        )
        remaining = await client.get("/claims-diary/meetings")

    assert response.status_code == 204, response.text
    assert response.content == b""
    assert meeting["id"] not in {item["id"] for item in remaining.json()["items"]}


async def test_deleting_records_the_removed_row_as_the_before_diff(db: AsyncSession) -> None:
    """The two halves of "this existed and now does not".

    A whole-row diff is right here for `remove_additional_injury`'s reason: the
    row *is* the change, and a diff that recorded less would leave the log
    unable to say what was deleted.
    """
    ctx = await context_for(db, *KAYA)
    created = await create_meeting(
        db,
        ctx,
        claim_business_id=a_claim_of(KAYA),
        meeting_type=MeetingType.employer_accommodation_review,
        meeting_date=TODAY,
        meeting_time=time(14, 0),
        location="Plant 3",
        notes="Walk the line for accommodation options.",
        participants=[MeetingParticipant.employer_hr, MeetingParticipant.supervisor],
        as_of=TODAY,
    )

    await delete_meeting(db, ctx, created.id, expected_version=created.version, as_of=TODAY)

    event = (await audit_rows(db, DELETE_ACTION, str(created.id)))[0]
    assert event.after is None
    assert event.before is not None
    assert event.before["claim_id"] == created.claim_business_id
    assert event.before["meeting_type"] == MeetingType.employer_accommodation_review.value
    assert event.before["participants"] == ["employer_hr", "supervisor"]
    assert event.before["location"] == "Plant 3"
    assert await db.get(Meeting, created.id) is None


async def test_a_stale_delete_is_a_409_and_leaves_the_row_alone(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        meeting = await _own_meeting(client)
        response = await client.delete(
            f"/claims-diary/meetings/{meeting['id']}",
            params={"expectedVersion": meeting["version"] + 1},
        )
        still_there = await client.get("/claims-diary/meetings")

    assert response.status_code == 409, response.text
    assert response.json()["meeting"]["version"] == meeting["version"]
    assert meeting["id"] in {item["id"] for item in still_there.json()["items"]}


async def test_deleting_somebody_elses_meeting_is_the_absent_404(db: AsyncSession) -> None:
    sarah = await context_for(db, *SARAH)
    kaya = await context_for(db, *KAYA)
    created = await create_meeting(
        db,
        sarah,
        claim_business_id=a_claim_of(SARAH),
        meeting_type=MeetingType.other,
        meeting_date=TODAY,
        as_of=TODAY,
    )

    with pytest.raises(MeetingNotVisible):
        await delete_meeting(db, kaya, created.id, expected_version=created.version)
    assert await db.get(Meeting, created.id) is not None


async def test_a_version_below_one_never_reaches_the_command(seeded_db_url: str) -> None:
    """`expectedVersion` has `ge=1` on the wire, so staleness is tested with
    `version + 1` — a decrement is a 422 from the schema (Epic 3's learning,
    written down here so the next author does not re-learn it)."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.delete("/claims-diary/meetings/1", params={"expectedVersion": 0})

    assert response.status_code == 422, response.text


# --- the claim link, once more, through the command ---------------------


async def test_the_command_raises_for_a_claim_outside_the_book(db: AsyncSession) -> None:
    """Nothing is written and the transaction is rolled back before the router
    answers, so a refused create cannot leave a snapshot behind."""
    ctx = await context_for(db, *SARAH)
    events = sa.select(sa.func.count()).select_from(AuditEvent).where(AuditEvent.entity == ENTITY)
    before = await db.scalar(events)

    with pytest.raises(MeetingClaimNotVisible):
        await create_meeting(
            db,
            ctx,
            claim_business_id=a_claim_outside(SARAH),
            meeting_type=MeetingType.other,
            meeting_date=TODAY,
        )

    assert await db.scalar(events) == before


async def test_created_at_is_the_databases_fact(db: AsyncSession) -> None:
    ctx = await context_for(db, *KAYA)
    created = await create_meeting(
        db,
        ctx,
        claim_business_id=a_claim_of(KAYA),
        meeting_type=MeetingType.ncm_care_coordination,
        meeting_date=TODAY,
        as_of=TODAY,
    )
    assert created.created_at.tzinfo is not None
    assert abs((datetime.now(UTC) - created.created_at).total_seconds()) < 300
