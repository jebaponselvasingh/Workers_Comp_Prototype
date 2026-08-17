"""Story 4.2 — the diary-note command and its list, against a database.

The pure half is small enough to live here too: `normalise_note_text` is a
validator and `decode_cursor` is a parser, and both are exercised without a
session at the top of the file. What needs a transaction is everything the I/O
matrix is actually about — that the row and its audit event land together, that
a claim outside the caller's book is refused before anything is written, that
one handler's diary is invisible to another, and that walking the cursor visits
every note exactly once in newest-first order.

Driven through the app for the contract tests and through the command for the
atomicity ones, because a rollback is not observable over HTTP —
`test_meetings.py`'s division, one table over.

**`requires_db` is a per-test decorator, not a `pytestmark`.** It was the module
marker, which skipped the whole validator and the whole cursor decoder on any
machine without a Postgres — every one of them a pure function with an oracle
beside it, and a laptop with no database is exactly where somebody would most
like them to run. `test_meetings.py` carries the same correction.

**These tests mutate the seeded portfolio**, and unlike meetings they start from
nothing: `diary_note` has no seed, deliberately (a seeded note would be words
nobody wrote attributed to a named handler). The module-scoped `seeded_db_url`
fixture rebuilds the schema for this file, so the rows created below are
contained here — but they persist *between* tests in this module, which is why
nothing hardcodes a count the earlier tests decide: every helper reads the
current state first, the way a client does.
"""

import base64
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, AuditEvent, DiaryNote, TimelineEvent
from data.repositories.identity import employer_ids_for
from services.claims.edit import EditNotPermitted, InvalidPatch
from services.claims.notes import (
    CREATE_ACTION,
    ENTITY,
    MAX_NOTE_LENGTH,
    NOTE_TEXT_FIELD,
    Cursor,
    DiaryNoteClaimNotVisible,
    InvalidCursor,
    create_diary_note,
    decode_cursor,
    encode_cursor,
    list_diary_notes,
    normalise_note_text,
)
from tests import seed_fixture
from tests.conftest import requires_db

KAYA = ("Kaya Johnson", "handler")
SARAH = ("Sarah Williams", "handler")
JENNIFER = ("Jennifer Park", "supervisor")
ANALYST = ("David Bline", "analyst")

TODAY = date(2026, 8, 17)
NOW = datetime(2026, 8, 17, 9, 30, tzinfo=UTC)


# --- validation, without a database -------------------------------------


def test_a_note_is_trimmed_and_kept() -> None:
    assert normalise_note_text("  Called the plant.  ") == "Called the plant."


@pytest.mark.parametrize("raw", ["", "   ", "\n\n", "\t "])
def test_an_empty_note_is_refused_rather_than_silently_ignored(raw: str) -> None:
    """The prototype's `if(!t) return` (line 1757) is the behaviour this replaces.

    Whitespace-only is the same refusal as empty, because the two are the same
    thing to a reader: a card carrying three spaces is a card with nothing on
    it. AC 3 asks for an inline message, which needs a refusal to render.
    """
    with pytest.raises(InvalidPatch) as refusal:
        normalise_note_text(raw)
    assert NOTE_TEXT_FIELD in str(refusal.value)


def test_a_note_past_the_cap_is_refused_by_the_command_whatever_the_schema_did() -> None:
    """The route declares `maxLength`, and the command enforces it anyway.

    AD-16 anticipates a caller that is not this SPA — an Epic 6 agent tool,
    whose arguments are untrusted content — and a bound that lived only in the
    Pydantic model would not be there for it.
    """
    with pytest.raises(InvalidPatch) as refusal:
        normalise_note_text("x" * (MAX_NOTE_LENGTH + 1))
    assert str(MAX_NOTE_LENGTH) in str(refusal.value)


def test_a_note_carrying_a_nul_is_a_422_and_not_a_500() -> None:
    """PostgreSQL `text` cannot hold a NUL and asyncpg raises rather than
    truncating, which reached the request as a 500 before `require_text`
    learned to refuse the C0 range."""
    with pytest.raises(InvalidPatch):
        normalise_note_text("before\x00after")


def test_a_newline_is_allowed_because_a_diary_note_is_prose() -> None:
    assert normalise_note_text("line one\nline two") == "line one\nline two"


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        # A paragraph pasted out of a Windows-authored document.
        ("line one\r\nline two", "line one\r\nline two"),
        # A bare CR — old Mac line endings, and what some clipboards produce.
        ("line one\rline two", "line one\rline two"),
        # An indented list pasted out of a spreadsheet.
        ("visit 1\tMonday", "visit 1\tMonday"),
        # Word writes U+000B for Shift+Enter and U+000C for a page break, and
        # both arrive in prose pasted out of it.
        ("line one\vline two", "line one\vline two"),
        ("page one\fpage two", "page one\fpage two"),
    ],
)
def test_a_tab_or_a_carriage_return_is_prose_too(raw: str, kept: str) -> None:
    """The refusal that was both wrong and dishonest (review, 2026-08-17).

    `character < " "` catches `\\t` (0x09) and `\\r` (0x0D) as well as NUL, and
    only `\\n` was exempted — so pasting a Windows-authored paragraph or a
    tab-indented list was refused with "contains characters that cannot be
    stored". PostgreSQL `text` stores both without complaint; NUL is the real
    constraint the docstring cites, and it is the only one. The message told the
    handler something untrue about their own words and gave them nothing to
    correct.
    """
    assert normalise_note_text(raw) == kept


@pytest.mark.parametrize("control", ["\x00", "\x01", "\x08", "\x0e", "\x1f", "\x7f"])
def test_every_other_control_character_is_still_refused(control: str) -> None:
    """The other half of the exemption above: the prose whitespace in
    `edit.WHITESPACE_KEPT` is let through, not the C0 range.

    The reason the rest are refused is the *downstream* one — a NUL PostgreSQL
    cannot store, and the others corrupt a log line or a CSV export — rather
    than "nobody typed them", which is a claim U+000B falsifies on its own and
    which is why it moved to the kept side above.
    """
    with pytest.raises(InvalidPatch):
        normalise_note_text(f"before{control}after")


def test_a_refusal_never_echoes_the_note_back(caplog: pytest.LogCaptureFixture) -> None:
    """AD-11, and this is the field it matters most for: a diary note is the
    handler's unfiltered read of a worker's file, so the one thing a refusal
    must not do is quote it into a message or a log."""
    secret = "Worker disclosed a prior back injury\x00"
    with pytest.raises(InvalidPatch) as refusal:
        normalise_note_text(secret)
    assert "prior back injury" not in str(refusal.value)
    assert "prior back injury" not in caplog.text


# --- the cursor, without a database -------------------------------------


def test_a_cursor_round_trips() -> None:
    cursor = Cursor(last_noted_at=NOW, last_id=42, limit=25, issued_on=TODAY)
    assert decode_cursor(encode_cursor(cursor), TODAY) == cursor


@pytest.mark.parametrize(
    "raw",
    [
        "not-base64-at-all!!",
        # Valid base64 of JSON that is not a cursor.
        "e30",
        # A forged page size past the route's ceiling — the cursor is *reused*
        # when the request omits a limit, so this is the way round the
        # validator if it were not bounded here.
        encode_cursor(Cursor(NOW, 1, 100_000, TODAY)),
        # A position that cannot exist.
        encode_cursor(Cursor(NOW, 0, 25, TODAY)),
        # Dated in the future: no cursor this service issued can name one.
        encode_cursor(Cursor(NOW, 1, 25, TODAY + timedelta(days=1))),
        # Older than MAX_CURSOR_AGE.
        encode_cursor(Cursor(NOW, 1, 25, TODAY - timedelta(days=8))),
    ],
)
def test_a_cursor_that_does_not_describe_this_list_is_refused(raw: str) -> None:
    """Never a silent page one — that turns a client bug into an infinite
    "Show more" that re-appends the same notes for ever."""
    with pytest.raises(InvalidCursor):
        decode_cursor(raw, TODAY)


def test_a_cursor_carrying_a_naive_timestamp_is_refused_at_the_boundary() -> None:
    """The column is `timestamptz` and every value this service issues is
    UTC-aware. A naive one would raise `TypeError` deep inside the row-value
    comparison — a 500 about a caller-supplied token."""
    naive = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "n": NOW.replace(tzinfo=None).isoformat(),
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
        decode_cursor(naive, TODAY)


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


async def audit_rows(db: AsyncSession, action: str, entity_id: str) -> list[AuditEvent]:
    rows = await db.scalars(
        sa.select(AuditEvent).where(AuditEvent.action == action, AuditEvent.entity_id == entity_id)
    )
    return list(rows.all())


# --- create -------------------------------------------------------------


@requires_db
async def test_a_handler_writes_a_note_and_it_comes_back_tagged(seeded_db_url: str) -> None:
    """The happy path (AC 2): 201, the row, and the claim tag the card renders."""
    claim_id = a_claim_of(KAYA)
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.post(
            "/claims-diary/notes",
            json={"claimId": claim_id, "noteText": "Called the plant; light duty from Monday."},
        )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["claimId"] == claim_id
    assert body["noteText"] == "Called the plant; light duty from Monday."
    assert body["notedAt"]
    # No lifecycle and no compare-and-swap: a client that found either here
    # would reasonably build an edit control the command does not have.
    assert "version" not in body
    assert "status" not in body
    # And no `workerName`. A note card renders `📎 WC-nnnn` and never the name,
    # so shipping it was the injured worker's name on every row of every
    # handler's diary with nothing at the other end reading it (AD-11).
    assert "workerName" not in body


@requires_db
async def test_a_note_with_no_claim_selected_is_written_untagged(seeded_db_url: str) -> None:
    """AC 2's second row: the add-note input is on screen whether or not a
    claim is selected, and the column is nullable so that is legal."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.post("/claims-diary/notes", json={"noteText": "General catch-up."})

    assert response.status_code == 201, response.text
    assert response.json()["claimId"] is None


@requires_db
async def test_an_empty_note_is_a_422_naming_the_field_and_writes_nothing(
    seeded_db_url: str,
) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        before = (await client.get("/claims-diary/notes")).json()["total"]
        response = await client.post(
            "/claims-diary/notes", json={"claimId": a_claim_of(KAYA), "noteText": "   "}
        )
        after = (await client.get("/claims-diary/notes")).json()["total"]

    assert response.status_code == 422, response.text
    assert response.json()["type"] == "/problems/invalid-patch"
    assert NOTE_TEXT_FIELD in response.json()["detail"]
    assert after == before


@requires_db
async def test_a_note_past_the_cap_is_refused_by_the_schema(seeded_db_url: str) -> None:
    """`maxLength` on the request model refuses first, so the type is
    `/problems/validation-error` rather than the command's `/problems/
    invalid-patch`. Story 4.1's review corrected the same documentation error
    on the meetings route; this test pins which one actually fires."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.post(
            "/claims-diary/notes", json={"noteText": "x" * (MAX_NOTE_LENGTH + 1)}
        )

    assert response.status_code == 422, response.text
    assert response.json()["type"] == "/problems/validation-error"


@requires_db
async def test_a_note_at_the_cap_with_a_trailing_newline_is_accepted(seeded_db_url: str) -> None:
    """The two bounds measure the same string (review, 2026-08-17).

    `max_length` counted what arrived on the wire and the command counted what
    it would store, so a full-length note ending in the Enter a handler pressed
    was 2001 on the wire and 2000 in the column — refused as
    `/problems/validation-error` for exceeding a limit it did not exceed, with
    nothing on screen to say which end to trim. The request model now strips
    before it measures.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.post(
            "/claims-diary/notes", json={"noteText": "x" * MAX_NOTE_LENGTH + "\n"}
        )

    assert response.status_code == 201, response.text
    assert len(response.json()["noteText"]) == MAX_NOTE_LENGTH


@requires_db
async def test_a_note_that_was_written_but_cannot_be_read_back_says_so(
    seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`DiaryNoteNotVisible` is its own refusal, not the claim-tag 404.

    Reachable only when a scope narrowing lands between the commit and the
    post-commit re-read, which is why it is forced here rather than provoked.
    Two things were wrong with mapping it onto `_note_claim_not_found(claim_id)`:
    an untagged note produced "No claim None in your caseload.", and — the half
    that matters — the row is *already committed and audited* by then, so a
    caller told the write failed re-sends it and duplicates a row in a table with
    no edit and no delete.
    """
    from api.routers import diary as diary_router
    from services.claims.notes import DiaryNoteNotVisible

    async def _vanished(*_args: object, **_kwargs: object) -> object:
        raise DiaryNoteNotVisible(4242)

    monkeypatch.setattr(diary_router, "create_diary_note", _vanished)

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.post("/claims-diary/notes", json={"noteText": "Written once."})

    assert response.status_code == 404, response.text
    body = response.json()
    assert body["type"] == "/problems/note-not-readable"
    # Not the claim 404's wording, and no `None` anywhere in a sentence a
    # handler reads.
    assert "None" not in body["detail"]
    assert "4242" in body["detail"]
    assert "saved" in body["detail"]


@requires_db
async def test_a_claim_outside_the_book_is_the_same_404_as_one_that_does_not_exist(
    seeded_db_url: str,
) -> None:
    """AD-7: a caller must not be able to learn that a claim exists."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        outside = await client.post(
            "/claims-diary/notes", json={"claimId": a_claim_outside(KAYA), "noteText": "n"}
        )
        absent = await client.post(
            "/claims-diary/notes", json={"claimId": "WC-99999", "noteText": "n"}
        )

    assert outside.status_code == absent.status_code == 404
    assert outside.json()["type"] == absent.json()["type"] == "/problems/note-claim-not-found"
    # The same sentence shape, differing only in the id the caller supplied.
    assert outside.json()["detail"].startswith("No claim ")
    assert absent.json()["detail"].startswith("No claim ")


@requires_db
@pytest.mark.parametrize("persona", [JENNIFER, ANALYST])
async def test_only_a_handler_may_write_a_note(
    seeded_db_url: str, persona: tuple[str, str]
) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *persona)
        response = await client.post("/claims-diary/notes", json={"noteText": "n"})

    assert response.status_code == 403, response.text
    assert response.json()["type"] == "/problems/edit-not-permitted"


@requires_db
async def test_the_command_refuses_a_supervisor_before_it_reads_anything(
    db: AsyncSession,
) -> None:
    """The role gate is the first rung, so the answer is identical for a claim
    in the caller's book and one that is not — `services/claims/edit.py`'s
    ordering."""
    ctx = await context_for(db, *JENNIFER)
    with pytest.raises(EditNotPermitted):
        await create_diary_note(db, ctx, note_text="n", claim_business_id=a_claim_of(KAYA))


@requires_db
async def test_the_command_raises_for_a_claim_outside_the_book(db: AsyncSession) -> None:
    ctx = await context_for(db, *KAYA)
    with pytest.raises(DiaryNoteClaimNotVisible):
        await create_diary_note(db, ctx, note_text="n", claim_business_id=a_claim_outside(KAYA))


@requires_db
async def test_a_refused_claim_tag_writes_neither_a_row_nor_an_audit_event(
    db: AsyncSession,
) -> None:
    """The refusal is inside the `INSERT … SELECT`, so there is no window and
    no partial write to roll back — but the transaction has taken a snapshot,
    and this asserts the state afterwards rather than the mechanism."""
    ctx = await context_for(db, *KAYA)
    before_notes = await db.scalar(sa.select(sa.func.count()).select_from(DiaryNote))
    before_audit = await db.scalar(
        sa.select(sa.func.count()).select_from(AuditEvent).where(AuditEvent.entity == ENTITY)
    )

    with pytest.raises(DiaryNoteClaimNotVisible):
        await create_diary_note(db, ctx, note_text="n", claim_business_id=a_claim_outside(KAYA))

    assert await db.scalar(sa.select(sa.func.count()).select_from(DiaryNote)) == before_notes
    assert (
        await db.scalar(
            sa.select(sa.func.count()).select_from(AuditEvent).where(AuditEvent.entity == ENTITY)
        )
        == before_audit
    )


@requires_db
async def test_writing_a_note_emits_one_audit_event_in_the_same_transaction(
    db: AsyncSession,
) -> None:
    """AD-4. `before` is null (nothing existed) and `after` is the row, with the
    claim's **business** id — `entity_id` holds the note's surrogate, so
    without this Story 8.1's purge would have no way to find the events
    belonging to a claim it is purging."""
    ctx = await context_for(db, *KAYA)
    claim_id = a_claim_of(KAYA)

    note = await create_diary_note(db, ctx, note_text="Audited note.", claim_business_id=claim_id)

    events = await audit_rows(db, CREATE_ACTION, str(note.id))
    assert len(events) == 1
    event = events[0]
    assert event.entity == ENTITY
    assert event.actor_id == ctx.user_id
    assert event.before is None
    assert event.after is not None
    assert event.after["claim_id"] == claim_id
    assert event.after["note_text"] == "Audited note."
    # One instant for the row and the record of it: the event's `at` is the
    # note's `noted_at`, not a second call to the clock.
    assert event.after["noted_at"] == note.noted_at.isoformat()
    assert event.at == note.noted_at


@requires_db
async def test_writing_a_note_emits_no_timeline_event(db: AsyncSession) -> None:
    """AD-12 scopes `timeline_event` to *claim-mutating* commands, and a note
    changes no column of `claim`. The prototype does not surface notes on the
    case timeline either — and a log line saying otherwise would put one
    handler's private diary into another reader's view of the file."""
    ctx = await context_for(db, *KAYA)
    before = await db.scalar(sa.select(sa.func.count()).select_from(TimelineEvent))

    await create_diary_note(db, ctx, note_text="n", claim_business_id=a_claim_of(KAYA))

    assert await db.scalar(sa.select(sa.func.count()).select_from(TimelineEvent)) == before


@requires_db
async def test_the_command_stamps_one_instant_it_was_given(db: AsyncSession) -> None:
    """`now` is a parameter with a UTC default, `audit.record`'s shape: the
    note happened when the command decided it happened."""
    ctx = await context_for(db, *KAYA)
    note = await create_diary_note(db, ctx, note_text="n", now=NOW)
    assert note.noted_at == NOW


# --- list ---------------------------------------------------------------


@requires_db
async def test_the_list_is_newest_first_and_the_order_is_total(db: AsyncSession) -> None:
    """AC 3. `noted_at DESC, id DESC` — the timestamp alone is a partial order,
    because two saves inside one clock tick tie, and a keyset page that ended
    inside the tie would repeat one row and drop the other."""
    ctx = await context_for(db, *KAYA)
    tick = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    for _ in range(3):
        # The same instant three times, deliberately: this is the tie the `id`
        # member exists for, and it is not exotic — the SPA can submit two
        # notes inside one millisecond.
        await create_diary_note(db, ctx, note_text="tied", now=tick)

    page = await list_diary_notes(db, ctx, limit=200, as_of=TODAY)
    keys = [(item.noted_at, item.id) for item in page.items]
    assert keys == sorted(keys, reverse=True)


@requires_db
async def test_paging_visits_every_note_exactly_once(db: AsyncSession) -> None:
    """The keyset cursor, walked to exhaustion at a page size of one.

    `total` is the whole list rather than the page, so it is also what the walk
    is checked against — and the descending comparison is what this catches: a
    `>` left over from the ascending meetings list pages away from the rows it
    just served and returns nothing.
    """
    ctx = await context_for(db, *KAYA)
    first = await list_diary_notes(db, ctx, limit=1, as_of=TODAY)
    assert first.total > 1, "earlier tests in this module should have written several"

    seen = [item.id for item in first.items]
    cursor = first.next_cursor
    while cursor is not None:
        page = await list_diary_notes(db, ctx, cursor=cursor, as_of=TODAY)
        seen.extend(item.id for item in page.items)
        cursor = page.next_cursor

    assert len(seen) == len(set(seen)) == first.total


@requires_db
async def test_one_handlers_diary_is_invisible_to_another(db: AsyncSession) -> None:
    """A note belongs to its author, not to the claim — so two handlers whose
    books overlap read their own working notes and not each other's."""
    kaya = await context_for(db, *KAYA)
    sarah = await context_for(db, *SARAH)

    mine = await create_diary_note(db, sarah, note_text="Sarah's own note.")

    hers = await list_diary_notes(db, sarah, limit=200, as_of=TODAY)
    theirs = await list_diary_notes(db, kaya, limit=200, as_of=TODAY)
    assert mine.id in {item.id for item in hers.items}
    assert mine.id not in {item.id for item in theirs.items}


@requires_db
async def test_a_supervisor_over_the_book_still_sees_none_of_it(db: AsyncSession) -> None:
    """Scope is author, not employer. A diary is a handler's working record and
    not a management report — `employer_scope` alone would make it one."""
    kaya = await context_for(db, *KAYA)
    supervisor = await context_for(db, *JENNIFER)

    await create_diary_note(db, kaya, note_text="n", claim_business_id=a_claim_of(KAYA))

    page = await list_diary_notes(db, supervisor, limit=200, as_of=TODAY)
    assert page.total == 0
    assert page.items == ()


@requires_db
async def test_the_list_answers_for_whoever_holds_the_cookie(seeded_db_url: str) -> None:
    """AD-7: there is no parameter on this route that could name a user, an
    employer or a role, so "whose notes?" has exactly one answer."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        kaya = (await client.get("/claims-diary/notes")).json()
        await login_as(client, *SARAH)
        sarah = (await client.get("/claims-diary/notes")).json()

    assert {item["id"] for item in kaya["items"]} & {item["id"] for item in sarah["items"]} == set()


@requires_db
async def test_a_bad_cursor_is_a_400_and_never_a_silent_page_one(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await client.get("/claims-diary/notes", params={"cursor": "nonsense!!"})

    assert response.status_code == 400, response.text
    assert response.json()["type"] == "/problems/invalid-cursor"


@requires_db
async def test_the_list_and_the_write_are_never_cached(seeded_db_url: str) -> None:
    """Specific to one persona's diary, so it must never be served to another
    from a cache upstream — `/me` and `/claims/queue`'s rule."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        listed = await client.get("/claims-diary/notes")
        written = await client.post("/claims-diary/notes", json={"noteText": "n"})

    assert listed.headers["Cache-Control"] == "no-store"
    assert written.headers["Cache-Control"] == "no-store"


@requires_db
async def test_a_note_whose_claim_leaves_the_book_stays_in_the_authors_list(
    db: AsyncSession,
) -> None:
    """The I/O matrix's List-isolation row, which the code contradicted.

    "A note tagged to a claim now outside A's scope is still A's own note and
    is still returned." `diary_note_scope` used to AND `employer_scope` onto
    the tag — `meeting_scope`'s shape, copied — so a re-scoping silently
    deleted entries from a handler's own diary, in a table with no edit, no
    delete and no other copy of what was written. This test asserted the
    deletion as intended; the contract is explicit and wins.

    The two tables answer differently because they are different records: a
    meeting is a plan against a case file (and its card republishes the
    worker's name), a note is the handler's own account of work they did.
    Employer scope still gates *accepting* a tag on write —
    `test_the_command_raises_for_a_claim_outside_the_book` is that half.
    """
    ctx = await context_for(db, *KAYA)
    tagged = await create_diary_note(
        db, ctx, note_text="About one claim.", claim_business_id=a_claim_of(KAYA)
    )

    narrowed = CallerContext(user_id=ctx.user_id, role=ctx.role, employer_ids=frozenset())
    page = await list_diary_notes(db, narrowed, limit=200, as_of=TODAY)

    assert tagged.id in {item.id for item in page.items}
    # `total` is the same list's size, so it has to agree with the predicate —
    # a count that still applied employer scope would disagree with the page it
    # describes, which is the shape of every "N notes" lie.
    assert page.total == len(page.items)
    # An untagged note survives the same narrowing, as it always did.
    untagged = await create_diary_note(db, ctx, note_text="About nothing in particular.")
    after = await list_diary_notes(db, narrowed, limit=200, as_of=TODAY)
    assert untagged.id in {item.id for item in after.items}
    assert after.total == len(after.items)


# --- the checklist seam -------------------------------------------------


@requires_db
async def test_a_note_closes_the_diary_check_in_for_that_claim(seeded_db_url: str) -> None:
    """AC 5, end to end — the completion Story 3.5 left with nowhere to write.

    The claim is chosen from the server's own answer rather than from a
    membership test here: which claims emit a `diary_check_in` is
    `services/worklist`'s to say, and a spec that guessed would be asserting
    about its own guess.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)

        target: str | None = None
        for claim_id in sorted(seed_fixture.expected_claim_ids(*KAYA)):
            checklist = (await client.get(f"/claims/{claim_id}/actions")).json()
            row = next(
                (item for item in checklist["items"] if item["id"].startswith("diary_check_in:")),
                None,
            )
            if row is not None:
                # The seam is gone: the row is live and carries a real link.
                assert row["enabled"] is True
                assert row["disabledReason"] is None
                # And no completion command — the note *is* the completion.
                assert row["command"] is None
                target = claim_id
                break
        assert target is not None, "no seeded claim in this book emits a diary check-in"

        write = await client.post(
            "/claims-diary/notes", json={"claimId": target, "noteText": "Weekly check-in done."}
        )
        assert write.status_code == 201, write.text

        after = (await client.get(f"/claims/{target}/actions")).json()

    assert all(not item["id"].startswith("diary_check_in:") for item in after["items"])


# --- persistence --------------------------------------------------------


@requires_db
async def test_a_note_is_a_row_and_survives_a_new_session(seeded_db_url: str) -> None:
    """The point of FR-DIARY-1, asserted where it is cheapest: the prototype's
    `diaryNotes` is a browser-lifetime object, and this reads the note back
    through a second application instance with its own connections."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        written = (
            await client.post("/claims-diary/notes", json={"noteText": "Survives a restart."})
        ).json()

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        listed = (await client.get("/claims-diary/notes")).json()

    found = [item for item in listed["items"] if item["id"] == written["id"]]
    assert len(found) == 1
    assert found[0]["noteText"] == "Survives a restart."


@requires_db
async def test_the_row_carries_no_version_column(db: AsyncSession) -> None:
    """The AD-4 statement for this table, asserted structurally rather than in
    prose: append-only rows are exempt from compare-and-swap, and a `version`
    here would be a column whose only possible value is 1."""
    columns: set[str] = set(DiaryNote.__table__.c.keys())
    assert "version" not in columns
    assert columns == {"id", "app_user_id", "claim_id", "note_text", "noted_at"}


@requires_db
async def test_the_app_role_can_delete_so_story_8_1_can_purge(seeded_db_url: str) -> None:
    """`diary_note` is PHI and belongs in Epic 8's cascade, which does not
    exist yet. The grant is the handle it will need — asserted here so a later
    narrowing of it is a failure rather than a surprise in 8.1."""
    engine = sa.create_engine(seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.connect() as conn:
            granted = set(
                conn.execute(
                    sa.text(
                        "SELECT privilege_type FROM information_schema.role_table_grants "
                        "WHERE table_name = 'diary_note' AND grantee = 'lineworker_app'"
                    )
                ).scalars()
            )
    finally:
        engine.dispose()

    assert {"SELECT", "INSERT", "DELETE"} <= granted


@requires_db
async def test_the_table_starts_empty_because_there_is_no_seed(seeded_db_url: str) -> None:
    """No seed migration follows 0034, deliberately: a seeded note would be
    words nobody wrote attributed to a named handler.

    Asserted against the *audit* trail rather than a row count, because the
    tests above have written rows into this module's database — an event with
    no actor could only have come from a migration.
    """
    engine = sa.create_engine(seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.connect() as conn:
            unaudited = conn.execute(
                sa.text(
                    "SELECT count(*) FROM diary_note n WHERE NOT EXISTS ("
                    "  SELECT 1 FROM audit_event e"
                    "  WHERE e.entity = 'diary_note' AND e.entity_id = n.id::text)"
                )
            ).scalar_one()
    finally:
        engine.dispose()

    assert unaudited == 0


def test_the_module_that_owns_this_table_is_the_only_writer() -> None:
    """AD-12, enforced by where the code lives and asserted structurally.

    The database grant cannot say "only `services/claims/notes.py` may INSERT",
    so the rule is a property of the source tree — which means it is worth a
    test, exactly as `test_payment_ownership.py` is for the money.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    writers = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "DiaryNote" in path.read_text()
        and any(verb in path.read_text() for verb in ("sa.insert(DiaryNote", "insert_diary_note"))
        and not path.as_posix().startswith(str(root / "tests"))
        and "tests/" not in path.relative_to(root).as_posix()
    )

    # Two files, and the pair is the architecture: the repository holds the
    # statement (with the scope predicate inside it) and the command is the only
    # thing that calls it. The router is deliberately *not* on this list — it
    # never names the model, which is what "thin by AD-1" means in practice.
    assert writers == [
        "data/repositories/claims.py",
        "services/claims/notes.py",
    ], writers


@requires_db
async def test_the_row_and_its_audit_event_are_one_transaction(
    db: AsyncSession, seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AD-4's "same transaction", proved by breaking the commit.

    Reading the event back *after* the commit looks identical whether the two
    statements shared a transaction or ran in two — which is what
    `test_writing_a_note_emits_one_audit_event_in_the_same_transaction` was
    doing, under a name that claimed otherwise. Failing between `audit.record`
    and `db.commit()` tells them apart: with one transaction neither the note
    nor its event survives, and with two the note would already be on disk with
    no record that it was written.
    """
    ctx = await context_for(db, *KAYA)
    marker = "One transaction or none."

    async def refuse_to_commit() -> None:
        raise RuntimeError("the commit failed")

    monkeypatch.setattr(db, "commit", refuse_to_commit)
    with pytest.raises(RuntimeError):
        await create_diary_note(
            db, ctx, note_text=marker, claim_business_id=a_claim_of(KAYA), now=NOW
        )
    await db.rollback()

    # A **separate** session, because the one above still holds the aborted
    # transaction and would see its own uncommitted rows.
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as other:
            notes = await other.scalars(sa.select(DiaryNote).where(DiaryNote.note_text == marker))
            assert list(notes.all()) == []
            events = await other.scalars(
                sa.select(AuditEvent).where(
                    AuditEvent.entity == ENTITY, AuditEvent.action == CREATE_ACTION
                )
            )
            assert all((event.after or {}).get("note_text") != marker for event in events.all())
    finally:
        await engine.dispose()
