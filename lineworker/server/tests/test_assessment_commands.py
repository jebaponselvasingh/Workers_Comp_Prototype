"""Story 3.5 AC 4 and AC 5 — the checklist's three writes, against a database.

What is worth asserting here is everything that is only true of a *transaction*
and of the refusal ladder around it: that the row, the audit event and the
timeline row land together, that a status guard refuses a transition a version
alone would have allowed, that a supervisor is refused **before** the claim is
looked up so 403 and 404 never form an existence oracle, and — the half AC 5 is
actually about — that a completion makes its own checklist row disappear,
because the trigger and the write read the same column.

Driven through the app for the contract tests and through the commands for the
ones a router cannot express. `tests/test_action_checklist.py` covers the
generator itself.

**These tests mutate the seeded portfolio.** The module-scoped `seeded_db_url`
fixture rebuilds the schema for this file, so the mutations are contained here
but persist *between* tests in it — which is why nothing below hardcodes a
version, every helper reads the current state first the way a client would, and
each test takes a claim no earlier one has touched.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, AuditEvent, Claim, Document, TimelineEvent
from data.models.enums import ActionCommand, ActionKey, ClaimStatus, TimelineTag
from data.repositories.identity import employer_ids_for
from services.claims.assessment import (
    APPROVE_ASSESSMENT_ACTION,
    MARK_OSHA_LOGGED_ACTION,
    SET_DOCUMENT_REVIEW_ACTION,
    approve_assessment,
    document_step_of,
)
from services.claims.edit import EditNotPermitted, StaleClaim
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

KAYA = ("Kaya Johnson", "handler")
DANTE = ("Dante Reyes", "handler")
JENNIFER = ("Jennifer Park", "supervisor")
ANALYST = ("David Bline", "analyst")


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
    assert len(match) == 1, f"expected exactly one {name}/{role} persona"
    assert (await client.post("/auth/login", json={"personaId": match[0]["id"]})).status_code == 200


def claims_for(persona: tuple[str, str]) -> list[str]:
    return sorted(str(claim["claim_id"]) for claim in seed_fixture.claims_for(*persona))


#: Claims this module has already written to. Every test takes a fresh one —
#: the module-scoped schema means an approval in one test is still there in the
#: next, and a test that re-approved an already-approved claim would fail for a
#: reason that has nothing to do with what it is checking
#: (`tests/test_payment_approval.py`'s rule).
USED: set[str] = set()


async def case_file(client: httpx.AsyncClient, claim_id: str) -> dict[str, Any]:
    resp = await client.get(f"/claims/{claim_id}")
    assert resp.status_code == 200, resp.text
    payload: dict[str, Any] = resp.json()
    return payload


async def checklist(client: httpx.AsyncClient, claim_id: str) -> set[str]:
    resp = await client.get(f"/claims/{claim_id}/actions")
    assert resp.status_code == 200, resp.text
    return {str(item["key"]) for item in resp.json()["items"]}


async def action_row(client: httpx.AsyncClient, claim_id: str, key: ActionKey) -> dict[str, Any]:
    resp = await client.get(f"/claims/{claim_id}/actions")
    assert resp.status_code == 200, resp.text
    row = next(item for item in resp.json()["items"] if item["key"] == key.value)
    assert isinstance(row, dict)
    return row


async def unused_claim_where(
    client: httpx.AsyncClient,
    persona: tuple[str, str],
    matches: Callable[[dict[str, Any]], bool],
) -> str:
    """A claim this module has not written to whose case file satisfies `matches`.

    Searched rather than picked by index, `tests/test_payment_approval.py`'s
    correction: "a claim in the treatment stage" and "a claim with something to
    approve" are different sets, and only the second is what a command test
    needs. An explicit loop rather than a generator expression, because `await`
    inside one makes it an async generator and `next` then raises `TypeError`.
    """
    for claim_id in claims_for(persona):
        if claim_id in USED:
            continue
        if matches(await case_file(client, claim_id)):
            USED.add(claim_id)
            return claim_id
    pytest.fail(f"no unused {persona[0]} claim matches the predicate")


async def audit_rows(db: AsyncSession, claim_id: str, action: str) -> list[AuditEvent]:
    db.expire_all()
    rows = await db.scalars(
        sa.select(AuditEvent)
        .where(AuditEvent.entity_id == claim_id, AuditEvent.action == action)
        .order_by(AuditEvent.id)
    )
    return list(rows.all())


async def timeline_rows(db: AsyncSession, claim_id: str, tag: TimelineTag) -> list[TimelineEvent]:
    db.expire_all()
    rows = await db.scalars(
        sa.select(TimelineEvent)
        .join(Claim, TimelineEvent.claim_id == Claim.id)
        .where(Claim.claim_id == claim_id, TimelineEvent.tag == tag.value)
        .order_by(TimelineEvent.id)
    )
    return list(rows.all())


async def set_status(db: AsyncSession, claim_id: str, status: ClaimStatus) -> None:
    """Put a seeded claim into a status the API cannot move it to.

    Raw rather than through a command on purpose: these tests set up states no
    route produces (a `denied` claim, a claim in `initial`), and the scoping and
    guarding they exercise is the *command's*, not this helper's.
    """
    await db.execute(sa.update(Claim).where(Claim.claim_id == claim_id).values(status=status))
    await db.commit()


# --- AC 4: approving the assessment -------------------------------------


@pytest.mark.parametrize(
    "start", [ClaimStatus.initial, ClaimStatus.ch_assessment_process], ids=lambda s: s.value
)
async def test_the_assessment_is_approved_from_either_starting_status(
    db: AsyncSession, seeded_db_url: str, start: ClaimStatus
) -> None:
    """Both statuses the prototype's `approveClaim` accepts, and the whole
    transaction: the row moves, an audit event names both statuses, and a
    timeline row appears under the tag the seed already uses for a claim's own
    lifecycle approval."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id = await unused_claim_where(client, KAYA, lambda _claim: True)
        await set_status(db, claim_id, start)

        before_timeline = len(await timeline_rows(db, claim_id, TimelineTag.approval))
        version = (await case_file(client, claim_id))["version"]

        resp = await client.post(
            f"/claims/{claim_id}/assessment/approval", json={"expectedVersion": version}
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["header"]["status"] == ClaimStatus.ch_approved.value
        assert resp.json()["version"] == version + 1

        events = await audit_rows(db, claim_id, APPROVE_ASSESSMENT_ACTION)
        assert len(events) == 1
        assert events[0].before == {"status": start.value}
        assert events[0].after == {"status": ClaimStatus.ch_approved.value}
        assert len(await timeline_rows(db, claim_id, TimelineTag.approval)) == before_timeline + 1


async def test_the_approval_makes_its_own_checklist_row_disappear(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """AC 4 and AC 5's shared property, and the reason there is no action-state
    table: the trigger and the command read the same column, so the row goes
    because the claim moved rather than because anything recorded that a button
    was pressed."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id = await unused_claim_where(client, KAYA, lambda _claim: True)
        await set_status(db, claim_id, ClaimStatus.ch_assessment_process)

        assert ActionKey.assessment_approval.value in await checklist(client, claim_id)

        version = (await case_file(client, claim_id))["version"]
        await client.post(
            f"/claims/{claim_id}/assessment/approval", json={"expectedVersion": version}
        )

        assert ActionKey.assessment_approval.value not in await checklist(client, claim_id)


@pytest.mark.parametrize(
    "status",
    [ClaimStatus.ch_approved, ClaimStatus.denied, ClaimStatus.settled_closed],
    ids=lambda s: s.value,
)
async def test_a_claim_outside_the_approvable_statuses_is_refused_with_the_fresh_entity(
    db: AsyncSession, seeded_db_url: str, status: ClaimStatus
) -> None:
    """The status guard, with a *current* version — so the version alone would
    have let the write through.

    This is the case AD-4's extra rung exists for, and the parametrisation is
    the point: an already-approved claim is the obvious one, but a `denied`
    claim quietly becoming `ch_approved` is the one that would matter.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id = await unused_claim_where(client, KAYA, lambda _claim: True)
        await set_status(db, claim_id, status)

        version = (await case_file(client, claim_id))["version"]
        resp = await client.post(
            f"/claims/{claim_id}/assessment/approval", json={"expectedVersion": version}
        )

        assert resp.status_code == 409, resp.text
        assert resp.json()["claim"]["header"]["status"] == status.value
        assert await audit_rows(db, claim_id, APPROVE_ASSESSMENT_ACTION) == []


async def test_a_stale_version_is_refused_with_the_fresh_entity(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """The compare-and-swap, with an approvable status — so the guard alone
    would have let it through. The 409 carries the case file, which is what lets
    the SPA render the current state without a second round trip (AD-9)."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id = await unused_claim_where(client, KAYA, lambda _claim: True)
        await set_status(db, claim_id, ClaimStatus.initial)

        # **A version *ahead* of the row's, not behind it.** `expectedVersion`
        # has a `ge=1` floor on the wire, so `version - 1` on a never-edited
        # claim is a 422 from the contract before the command sees it — which
        # would make this test assert about Pydantic rather than about the
        # compare-and-swap. Any mismatch is a mismatch.
        version = (await case_file(client, claim_id))["version"]
        resp = await client.post(
            f"/claims/{claim_id}/assessment/approval", json={"expectedVersion": version + 1}
        )

        assert resp.status_code == 409, resp.text
        assert resp.json()["claim"]["claimId"] == claim_id
        assert await audit_rows(db, claim_id, APPROVE_ASSESSMENT_ACTION) == []


@pytest.mark.parametrize("persona", [JENNIFER, ANALYST], ids=lambda p: p[1])
async def test_a_supervisor_or_analyst_is_refused_identically_whatever_the_claim(
    seeded_db_url: str, persona: tuple[str, str]
) -> None:
    """AC 4's fourth criterion, over three kinds of claim.

    The role check runs before the claim is read, so a claim in the caller's
    book, one in somebody else's and one that does not exist must produce byte-
    identical refusals — otherwise the difference answers "does this claim
    exist?" for a caller who may not ask (AD-7).
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *persona)
        in_book = claims_for(persona)[0]
        # Another handler's claim. For the **analyst** this is in their book
        # too, because their scope is the whole portfolio — which strengthens
        # the assertion rather than weakening it: the refusal must not vary
        # with scope at all, and the analyst is the persona for whom scope
        # could not have narrowed it.
        another = claims_for(KAYA)[0]

        bodies = []
        for claim_id in (in_book, another, "WC-99999"):
            resp = await client.post(
                f"/claims/{claim_id}/assessment/approval", json={"expectedVersion": 1}
            )
            assert resp.status_code == 403, resp.text
            bodies.append(resp.json())

        assert bodies[0] == bodies[1] == bodies[2]


async def test_the_command_refuses_a_non_handler_before_reading_the_claim(
    db: AsyncSession,
) -> None:
    """The ordering, asserted at the command rather than over HTTP: a claim id
    that does not exist at all still raises the *role* error, which is only true
    if nothing was looked up first."""
    ctx = await context_for(db, *JENNIFER)
    with pytest.raises(EditNotPermitted):
        await approve_assessment(db, ctx, "WC-99999", expected_version=1)


async def test_the_approval_moves_the_queue_cards_pending_approval_weight(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """AC 4's "pending-approval counts" clause, through the surface that owns it.

    **There is no pending-approval *tile*.** The top bar counts caseload,
    active treatment and high risk (Story 1.4), and none of the three moves on
    an approval. What the story's phrase names is the other half of its own
    sentence — "top bar / priority score input" — and that half is real: the
    priority score carries a `pendingApproval` weight over exactly the statuses
    this command moves a claim out of (`priority_weights`), so approving drops
    the claim's score and can move its card.

    Asserted as a *decrease* rather than as the weight's value, because the
    weight is a rule document's number and restating it here would be this test
    deciding a parameter.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id = await unused_claim_where(client, KAYA, lambda _claim: True)
        await set_status(db, claim_id, ClaimStatus.ch_assessment_process)

        before = await _priority_score(client, claim_id)
        version = (await case_file(client, claim_id))["version"]
        assert (
            await client.post(
                f"/claims/{claim_id}/assessment/approval", json={"expectedVersion": version}
            )
        ).status_code == 200

        assert await _priority_score(client, claim_id) < before


async def _priority_score(client: httpx.AsyncClient, claim_id: str) -> float:
    """One claim's queue-card score, found across the four stage groups.

    The queue pages, so a claim can legitimately be absent from the first page
    of its group; every claim these tests use is reachable there, and a failure
    to find one is a `fail` rather than a silent skip so that a paging change
    does not quietly turn this into an assertion about nothing.
    """
    payload = (await client.get("/claims/queue")).json()
    for group in payload["groups"].values():
        for card in group["items"]:
            if card["claimId"] == claim_id:
                return float(card["priorityScore"])
    pytest.fail(f"{claim_id} is not on the first page of any stage group")


# --- AC 5: the document review→confirm sequence -------------------------


async def documents_of(db: AsyncSession, claim_id: str) -> list[Document]:
    db.expire_all()
    rows = await db.scalars(
        sa.select(Document)
        .join(Claim, Document.claim_id == Claim.id)
        .where(Claim.claim_id == claim_id)
        .order_by(Document.id)
    )
    return list(rows.all())


async def a_document(
    client: httpx.AsyncClient, db: AsyncSession, persona: tuple[str, str] = KAYA
) -> tuple[str, Document]:
    claim_id = await unused_claim_where(
        client, persona, lambda claim: len(claim["documents"]["documents"]) > 0
    )
    return claim_id, (await documents_of(db, claim_id))[0]


async def test_marking_a_document_reviewed_persists_and_audits_it(
    db: AsyncSession, seeded_db_url: str
) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id, document = await a_document(client, db)

        # **Held as integers before the write.** `documents_of` and `audit_rows`
        # both `expire_all()`, so reading `document.version` afterwards refreshes
        # the ORM row and compares it against itself — a test that passes for a
        # command that wrote nothing.
        document_id, before_version = document.id, document.version

        resp = await client.post(
            f"/claims/{claim_id}/documents/{document_id}/review",
            json={"expectedVersion": before_version, "step": "mark_document_reviewed"},
        )

        assert resp.status_code == 200, resp.text
        fresh = (await documents_of(db, claim_id))[0]
        assert (fresh.reviewed, fresh.confirmed) == (True, False)
        assert fresh.version == before_version + 1

        events = await audit_rows(db, claim_id, SET_DOCUMENT_REVIEW_ACTION)
        assert len(events) == 1
        assert events[0].before == {
            "document": document_id,
            "reviewed": False,
            "confirmed": False,
        }
        assert events[0].after == {"document": document_id, "reviewed": True, "confirmed": False}
        assert len(await timeline_rows(db, claim_id, TimelineTag.document)) == 1


async def test_confirming_a_document_nobody_reviewed_is_refused(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """The prototype's own sequencing, enforced as a status guard rather than as
    a disabled button — so an agent tool or a replayed request cannot skip it.

    409 rather than 422: it is a statement about where the row is, not about the
    request, and the fresh case file attached is what tells the handler to press
    Review first.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id, document = await a_document(client, db)

        resp = await client.post(
            f"/claims/{claim_id}/documents/{document.id}/review",
            json={"expectedVersion": document.version, "step": "confirm_document"},
        )

        assert resp.status_code == 409, resp.text
        fresh = (await documents_of(db, claim_id))[0]
        assert (fresh.reviewed, fresh.confirmed) == (False, False)
        assert fresh.version == document.version
        assert await audit_rows(db, claim_id, SET_DOCUMENT_REVIEW_ACTION) == []


async def test_review_then_confirm_is_two_events_on_one_document(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """Two columns because they are two events with two audit rows — a document
    is read, and then it is accepted."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id, document = await a_document(client, db)

        first = await client.post(
            f"/claims/{claim_id}/documents/{document.id}/review",
            json={"expectedVersion": document.version, "step": "mark_document_reviewed"},
        )
        assert first.status_code == 200, first.text

        reviewed = (await documents_of(db, claim_id))[0]
        second = await client.post(
            f"/claims/{claim_id}/documents/{document.id}/review",
            json={"expectedVersion": reviewed.version, "step": "confirm_document"},
        )

        assert second.status_code == 200, second.text
        final = (await documents_of(db, claim_id))[0]
        assert (final.reviewed, final.confirmed) == (True, True)
        assert len(await audit_rows(db, claim_id, SET_DOCUMENT_REVIEW_ACTION)) == 2
        assert len(await timeline_rows(db, claim_id, TimelineTag.document)) == 2


async def test_repeating_a_completion_writes_nothing_and_still_answers_the_case_file(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """`update_comp_rate_override`'s rule: the log is a record of changes, and a
    second click on a control that has not re-rendered yet is not one.

    The second call carries the *fresh* version, so it is not a stale write —
    which is exactly the case that must not 409, because telling a handler that
    somebody else changed their claim would be a lie about their own click.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id, document = await a_document(client, db)

        await client.post(
            f"/claims/{claim_id}/documents/{document.id}/review",
            json={"expectedVersion": document.version, "step": "mark_document_reviewed"},
        )
        reviewed = (await documents_of(db, claim_id))[0]

        again = await client.post(
            f"/claims/{claim_id}/documents/{document.id}/review",
            json={"expectedVersion": reviewed.version, "step": "mark_document_reviewed"},
        )

        assert again.status_code == 200, again.text
        assert (await documents_of(db, claim_id))[0].version == reviewed.version
        assert len(await audit_rows(db, claim_id, SET_DOCUMENT_REVIEW_ACTION)) == 1


async def test_a_document_on_another_claim_is_the_claims_own_404(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """A document id that *is* in the caller's scope but belongs to a different
    claim must not resolve through this claim's URL, or the path segment is
    decoration and two claims' viewers are interchangeable."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        mine, document = await a_document(client, db)
        other = await unused_claim_where(client, KAYA, lambda claim: claim["claimId"] != mine)

        resp = await client.post(
            f"/claims/{other}/documents/{document.id}/review",
            json={"expectedVersion": document.version, "step": "mark_document_reviewed"},
        )

        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == f"No claim {other} in your caseload."


async def test_a_step_that_is_not_a_document_step_is_refused_by_the_contract(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """`step` is an `ActionCommand`, so `approve_assessment` is a 422 from the
    generated contract before the command sees it — and the command refuses it
    again for the agent tools that never pass through this model."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id, document = await a_document(client, db)

        resp = await client.post(
            f"/claims/{claim_id}/documents/{document.id}/review",
            json={"expectedVersion": document.version, "step": "approve_assessment"},
        )

        assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("persona", [JENNIFER, ANALYST], ids=lambda p: p[1])
async def test_a_non_handler_cannot_record_a_document_review(
    seeded_db_url: str, persona: tuple[str, str]
) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *persona)
        claim_id = claims_for(persona)[0]

        resp = await client.post(
            f"/claims/{claim_id}/documents/1/review",
            json={"expectedVersion": 1, "step": "mark_document_reviewed"},
        )

        assert resp.status_code == 403, resp.text


def test_the_offered_document_step_advances_with_the_row() -> None:
    """The server's answer to which control a checklist row shows.

    A pure function, so it is asserted here rather than inferred from a payload:
    "reviewed but not confirmed" looks like the whole rule and is not — a
    confirmed document is eligible for nothing.
    """
    assert document_step_of(reviewed=False, confirmed=False) is ActionCommand.mark_document_reviewed
    assert document_step_of(reviewed=True, confirmed=False) is ActionCommand.confirm_document
    assert document_step_of(reviewed=True, confirmed=True) is None


# --- AC 5: the OSHA 300 entry -------------------------------------------


def recordable_and_unlogged(claim: dict[str, Any]) -> bool:
    return bool(claim["header"]["oshaRecordable"])


async def test_recording_the_osha_entry_persists_audits_and_clears_its_row(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """The whole of AC 5 for this toggle: the column moves, the write is
    audited, a timeline row appears, and the checklist row it was generated
    from is gone on the next read — from the server, not from a client-side
    list mutation."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id = await unused_claim_where(client, KAYA, recordable_and_unlogged)

        assert ActionKey.osha_log.value in await checklist(client, claim_id)
        version = (await case_file(client, claim_id))["version"]

        resp = await client.post(f"/claims/{claim_id}/osha-log", json={"expectedVersion": version})

        assert resp.status_code == 200, resp.text
        logged = (
            await db.scalars(sa.select(Claim.osha_logged).where(Claim.claim_id == claim_id))
        ).one()
        assert logged is True

        events = await audit_rows(db, claim_id, MARK_OSHA_LOGGED_ACTION)
        assert len(events) == 1
        assert (events[0].before, events[0].after) == (
            {"osha_logged": False},
            {"osha_logged": True},
        )
        assert len(await timeline_rows(db, claim_id, TimelineTag.compliance)) == 1
        assert ActionKey.osha_log.value not in await checklist(client, claim_id)


async def test_a_claim_that_is_not_recordable_is_refused_as_unprocessable(
    seeded_db_url: str,
) -> None:
    """422 rather than 409: there is nothing to re-read and trying again will
    never work, because recordability is a property of the injury rather than a
    state a claim passes through."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id = await unused_claim_where(
            client, KAYA, lambda claim: not claim["header"]["oshaRecordable"]
        )

        version = (await case_file(client, claim_id))["version"]
        resp = await client.post(f"/claims/{claim_id}/osha-log", json={"expectedVersion": version})

        assert resp.status_code == 422, resp.text
        assert "recordable" in resp.json()["detail"]


async def test_logging_an_already_logged_injury_writes_nothing(
    db: AsyncSession, seeded_db_url: str
) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id = await unused_claim_where(client, KAYA, recordable_and_unlogged)

        first_version = (await case_file(client, claim_id))["version"]
        await client.post(f"/claims/{claim_id}/osha-log", json={"expectedVersion": first_version})
        after_first = (await case_file(client, claim_id))["version"]

        again = await client.post(
            f"/claims/{claim_id}/osha-log", json={"expectedVersion": after_first}
        )

        assert again.status_code == 200, again.text
        assert (await case_file(client, claim_id))["version"] == after_first
        assert len(await audit_rows(db, claim_id, MARK_OSHA_LOGGED_ACTION)) == 1


async def test_a_stale_version_on_the_osha_entry_is_refused(
    db: AsyncSession, seeded_db_url: str
) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id = await unused_claim_where(client, KAYA, recordable_and_unlogged)

        version = (await case_file(client, claim_id))["version"]
        resp = await client.post(
            f"/claims/{claim_id}/osha-log", json={"expectedVersion": version + 1}
        )

        assert resp.status_code == 409, resp.text
        assert resp.json()["claim"]["claimId"] == claim_id
        assert await audit_rows(db, claim_id, MARK_OSHA_LOGGED_ACTION) == []


@pytest.mark.parametrize("persona", [JENNIFER, ANALYST], ids=lambda p: p[1])
async def test_a_non_handler_cannot_record_an_osha_entry(
    seeded_db_url: str, persona: tuple[str, str]
) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *persona)

        resp = await client.post(
            f"/claims/{claims_for(persona)[0]}/osha-log", json={"expectedVersion": 1}
        )

        assert resp.status_code == 403, resp.text


async def test_a_claim_outside_the_book_is_the_same_404_on_every_command(
    seeded_db_url: str,
) -> None:
    """Three routes, one wording — written once in `_not_found` because the
    sameness *is* the security property: a caller comparing one route's refusal
    with another's must not learn from the difference that a claim exists."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        mine = set(claims_for(KAYA))
        theirs = next(claim for claim in claims_for(DANTE) if claim not in mine)

        responses = [
            await client.post(f"/claims/{theirs}/assessment/approval", json={"expectedVersion": 1}),
            await client.post(f"/claims/{theirs}/osha-log", json={"expectedVersion": 1}),
            await client.post(
                f"/claims/{theirs}/documents/1/review",
                json={"expectedVersion": 1, "step": "mark_document_reviewed"},
            ),
        ]

        assert [resp.status_code for resp in responses] == [404, 404, 404]
        assert len({resp.json()["detail"] for resp in responses}) == 1


async def test_the_command_raises_stale_claim_carrying_the_fresh_entity(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """The exception's payload, at the command rather than over HTTP.

    `StaleClaim.fresh` is what the route turns into the 409's `claim` extension,
    and it has to be a *re-read* rather than the snapshot the command was
    holding — the whole point is to show the caller what won.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id = await unused_claim_where(client, KAYA, lambda _claim: True)
        await set_status(db, claim_id, ClaimStatus.denied)

    ctx = await context_for(db, *KAYA)
    current = (await db.scalars(sa.select(Claim.version).where(Claim.claim_id == claim_id))).one()

    with pytest.raises(StaleClaim) as raised:
        await approve_assessment(db, ctx, claim_id, expected_version=current)

    assert raised.value.fresh.claim_id == claim_id
    assert raised.value.fresh.header.status is ClaimStatus.denied
