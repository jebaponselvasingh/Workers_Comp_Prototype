"""Story 3.4 AC 1 — approving a payment, against a real database.

What is worth asserting here is everything that is only true of a
*transaction* and of the contract around it: that the two guards are checked
in the same statement, that the row, the audit event and the timeline event
land together, that a supervisor is refused before the claim is looked up, and
— the story's whole invariant — that no approval anywhere writes `paid`.

Driven through the app for the contract tests and through the command for the
ones a router cannot express. `tests/test_payment_batch.py` covers the other
half of the lifecycle and `tests/test_payment_ownership.py` covers the
structural claims about who may write these tables at all.

**These tests mutate the seeded portfolio.** The module-scoped `seeded_db_url`
fixture rebuilds the schema for this file, so the mutations are contained
here — but they persist *between* tests in this module, which is why nothing
below hardcodes a version and every helper reads the current state first, the
way a client would. It is also why each test picks its own claim.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import (
    AppUser,
    AuditEvent,
    Bill,
    Claim,
    Expense,
    PaymentScheduleWeek,
    TimelineEvent,
)
from data.models.enums import LineItemStatus, ScheduleWeekStatus, TimelineTag
from data.repositories.identity import employer_ids_for
from services.financials.approval import (
    APPROVABLE_LINE_ITEM_STATUSES,
    APPROVABLE_WEEK_STATUSES,
    APPROVE_BILL_ACTION,
    APPROVE_WEEK_ACTION,
    PaymentNotVisible,
    StalePaymentRow,
    approve_bill_payment,
    approve_expense_payment,
    approve_schedule_week,
)
from services.worklist.approvals import ApprovalNotPermitted, approve_payment
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

KAYA = ("Kaya Johnson", "handler")
SARAH = ("Sarah Williams", "handler")
JENNIFER = ("Jennifer Park", "supervisor")
ANALYST = ("David Bline", "analyst")

#: The default cadence, restated rather than imported from `Settings`: these
#: tests are about the approval, and the batch date only has to be *a* valid
#: frozenset for the read model to assemble.
WEEKDAYS = frozenset({1, 4})


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


def claims_in_stage(persona: tuple[str, str], stage: str) -> list[str]:
    claims = sorted(
        str(claim["claim_id"])
        for claim in seed_fixture.claims_for(*persona)
        if claim["stage"] == stage
    )
    assert claims, f"no seeded {stage} claim for {persona}"
    return claims


def all_claims(persona: tuple[str, str]) -> list[str]:
    return sorted(str(claim["claim_id"]) for claim in seed_fixture.claims_for(*persona))


#: Claims this module has already mutated. Every test takes a *fresh* one,
#: because the module-scoped schema means an approval in one test is still
#: there in the next — and a test that re-approved an already-scheduled row
#: would fail for a reason that has nothing to do with what it is checking.
USED: set[str] = set()


async def claim_with_approvable(
    client: httpx.AsyncClient,
    persona: tuple[str, str],
    *,
    kind: str,
    stage: str | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """A claim this module has not touched that has something to approve.

    **Searched rather than picked by stage, and the search is the interesting
    part.** The obvious version of this helper was "the first treatment claim",
    and it does not work: a treatment claim whose date of injury is old enough
    comes back with *every* week `paid`, because the projection pays weeks the
    calendar has passed (`services/financials/schedule.py`). So "a claim in the
    treatment stage" and "a claim with a week awaiting a decision" are
    different sets, and only the second is what these tests need.

    Approvability is read off the payload's own `approvable` field rather than
    from a status list restated here, so what is exercised is the same answer
    the SPA's button obeys.
    """
    for claim_id in all_claims(persona):
        if claim_id in USED:
            continue
        if stage is not None and claim_id not in claims_in_stage(persona, stage):
            continue
        payload = await financials(client, claim_id)
        rows = (
            payload["schedule"]
            if kind == "week"
            else payload["bills"]["items"]
            if kind == "bill"
            else payload["expenses"]["items"]
        )
        row = next((r for r in rows if r["approvable"]), None)
        if row is not None:
            USED.add(claim_id)
            return claim_id, payload, row
    pytest.skip(f"no unused {persona[0]} claim with an approvable {kind}")


async def financials(client: httpx.AsyncClient, claim_id: str) -> dict[str, Any]:
    resp = await client.get(f"/claims/{claim_id}/financials")
    assert resp.status_code == 200, resp.text
    payload: dict[str, Any] = resp.json()
    return payload


async def claim_pk_of(db: AsyncSession, claim_business_id: str) -> int:
    """The surrogate id behind a `WC-nnnn`.

    A raw lookup rather than a repository call on purpose: these tests set up
    states the API cannot reach, so they address rows directly and the scoping
    they are exercising is the *command's*, not this helper's.
    """
    return (await db.scalars(sa.select(Claim.id).where(Claim.claim_id == claim_business_id))).one()


async def approve(
    client: httpx.AsyncClient,
    claim_id: str,
    *,
    kind: str,
    target_id: int,
    expected_version: int,
) -> httpx.Response:
    return await client.post(
        f"/claims/{claim_id}/payments/approvals",
        json={"kind": kind, "targetId": target_id, "expectedVersion": expected_version},
    )


# --- AC 1: the happy path -----------------------------------------------


async def test_approving_a_week_schedules_it_for_the_next_batch(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id, _before, week = await claim_with_approvable(client, KAYA, kind="week")

        resp = await approve(
            client,
            claim_id,
            kind="week",
            target_id=week["weekNo"],
            expected_version=week["version"],
        )

    assert resp.status_code == 200, resp.text
    after = resp.json()
    row = next(r for r in after["schedule"] if r["weekNo"] == week["weekNo"])
    assert row["status"] == "payment_scheduled"
    # **Not `paid`** — the story's one-sentence invariant, asserted on the
    # response a handler actually sees rather than only in the database.
    assert row["status"] != "paid"
    # The compare-and-swap moved, so the next approval of this row needs the
    # new number and a replayed request is refused.
    assert row["version"] == week["version"] + 1
    # And the server withdraws its own permission: the button goes away
    # because `approvable` is false, not because the browser decided.
    assert row["approvable"] is False


async def test_the_response_is_the_whole_refreshed_payload(seeded_db_url: str) -> None:
    """AD-9: one read model out, so nothing is left for the client to decide.

    The figure that moves is `nextPaymentDue`. An approved week is no longer
    *waiting to fall due* — it is waiting to be paid — so the derivation skips
    it and the answer advances. That is a summary field changing because a row
    changed, which is exactly why an acknowledgement would not have been
    enough.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id, before, week = await claim_with_approvable(client, KAYA, kind="week")
        if before["summary"]["nextPaymentDue"] != week["periodStart"]:
            pytest.skip("this claim's next-due week is not the first approvable one")

        after = (
            await approve(
                client,
                claim_id,
                kind="week",
                target_id=week["weekNo"],
                expected_version=week["version"],
            )
        ).json()

    assert after["summary"]["nextPaymentDue"] != before["summary"]["nextPaymentDue"]
    assert set(after) == set(before)


async def test_approving_a_bill_moves_it_out_of_review(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id, before, bill = await claim_with_approvable(client, KAYA, kind="bill")

        after = (
            await approve(
                client,
                claim_id,
                kind="bill",
                target_id=bill["id"],
                expected_version=bill["version"],
            )
        ).json()

    row = next(b for b in after["bills"]["items"] if b["id"] == bill["id"])
    assert row["status"] == "payment_scheduled"
    # The prototype's label bug refused end to end: an approved bill and a bill
    # nobody has filed are two different tokens, and this is the transition
    # between them rather than a relabelling of one.
    assert row["status"] != "pending_submission"
    # Nothing was disbursed, so the paid figure has not moved.
    assert after["bills"]["paidCents"] == before["bills"]["paidCents"]


async def test_approving_writes_an_audit_event_and_a_timeline_row(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """AD-4: the change, its audit row and its timeline row, in one transaction."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id, _payload, week = await claim_with_approvable(client, KAYA, kind="week")
        assert (
            await approve(
                client,
                claim_id,
                kind="week",
                target_id=week["weekNo"],
                expected_version=week["version"],
            )
        ).status_code == 200

    event = (
        await db.scalars(
            sa.select(AuditEvent)
            .where(AuditEvent.action == APPROVE_WEEK_ACTION)
            .where(AuditEvent.entity_id == claim_id)
        )
    ).one()
    assert event.entity == "payment_schedule_week"
    # The diff names the row and both statuses, and nothing else (AD-11): no
    # amount, no claimant, no columns the command did not write.
    assert event.after == {"status": "payment_scheduled", "item": f"week {week['weekNo']}"}
    assert event.before is not None and event.before["status"] == week["status"]
    assert set(event.after) == {"status", "item"}

    timeline = (
        await db.scalars(
            sa.select(TimelineEvent)
            .where(TimelineEvent.claim_id == await claim_pk_of(db, claim_id))
            .where(TimelineEvent.description.like("%approved for payment%"))
        )
    ).all()
    assert len(timeline) == 1, "the approval appended no timeline row, or more than one"
    # `benefit`, not `approval`: the seeded `approval` rows are the claim's own
    # approval at intake, and a handler filtering a timeline for that should not
    # get twenty payment lines back.
    assert timeline[0].tag == TimelineTag.benefit.value
    assert str(week["weekNo"]) in timeline[0].description


# --- AC 1: the refusals -------------------------------------------------


@pytest.mark.parametrize("persona", [JENNIFER, ANALYST])
async def test_only_a_handler_may_approve(seeded_db_url: str, persona: tuple[str, str]) -> None:
    """403, and answered before the claim is looked up.

    The claim id below is Kaya's, which neither of these personas may edit and
    both may read. The point is that the refusal does not depend on that: the
    role check runs first, so the same 403 comes back for a claim in their book,
    one in somebody else's, and one that does not exist.
    """
    claim_id = claims_in_stage(KAYA, "treatment")[0]
    async with make_client(seeded_db_url) as client:
        await login_as(client, *persona)
        resp = await approve(client, claim_id, kind="week", target_id=1, expected_version=1)
        missing = await approve(client, "WC-99999", kind="week", target_id=1, expected_version=1)

    assert resp.status_code == 403
    assert resp.json()["type"] == "/problems/approval-not-permitted"
    assert missing.status_code == 403
    assert missing.json()["detail"] == resp.json()["detail"]


async def test_a_claim_outside_the_book_is_a_404_in_the_case_files_wording(
    seeded_db_url: str,
) -> None:
    outside = next(
        claim
        for claim in claims_in_stage(SARAH, "treatment")
        if claim not in claims_in_stage(KAYA, "treatment")
    )
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        approval = await approve(client, outside, kind="week", target_id=1, expected_version=1)
        detail = await client.get(f"/claims/{outside}")

    assert approval.status_code == 404
    # Byte-identical to the case file's refusal. A difference here would tell a
    # caller that the claim exists but is not theirs, which is the enumeration
    # oracle AD-7 closes.
    assert approval.json()["detail"] == detail.json()["detail"]
    assert approval.json()["type"] == detail.json()["type"]


async def test_a_week_that_is_not_on_the_claim_answers_the_same_404(
    seeded_db_url: str,
) -> None:
    """A week number past the end of the schedule is *not* a distinct answer.

    Telling "no such week" apart from "no such claim" would confirm that the
    claim exists — `select_claim_detail`'s single-answer rule, one level down.
    """
    claim_id = claims_in_stage(KAYA, "treatment")[0]
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        resp = await approve(client, claim_id, kind="week", target_id=999, expected_version=1)
        outside = await client.get("/claims/WC-99999")

    assert resp.status_code == 404
    assert resp.json()["type"] == outside.json()["type"]


async def test_a_stale_version_is_refused_with_the_fresh_payload(seeded_db_url: str) -> None:
    """The compare-and-swap half of AD-4, over HTTP.

    The first approval consumes the version; the second sends the same one and
    is refused. What comes back is the *whole* Bills payload plus the row's
    current status, which is what lets the sheet render the state that won
    without a second round trip.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id, _payload, week = await claim_with_approvable(client, KAYA, kind="week")

        first = await approve(
            client,
            claim_id,
            kind="week",
            target_id=week["weekNo"],
            expected_version=week["version"],
        )
        replay = await approve(
            client,
            claim_id,
            kind="week",
            target_id=week["weekNo"],
            expected_version=week["version"],
        )

    assert first.status_code == 200
    assert replay.status_code == 409
    body = replay.json()
    assert body["type"] == "/problems/stale-payment"
    assert body["paymentStatus"] == "payment_scheduled"
    fresh = next(r for r in body["financials"]["schedule"] if r["weekNo"] == week["weekNo"])
    assert fresh["status"] == "payment_scheduled"
    assert fresh["version"] == week["version"] + 1


async def test_the_status_guard_refuses_where_the_version_would_have_allowed(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """The rung AD-4 adds for a lifecycle command, isolated.

    A version says "nobody has touched this row"; a status guard says "the move
    you want is available from here". This is the case that separates them: the
    row is set to `paid` **without touching its version**, so a caller holding
    the version it read is still current by the compare-and-swap's reckoning —
    and must still be refused, because approving a payment that has already
    gone out would emit a second approval event for one disbursement.

    Written through the command rather than the route: the state it needs
    cannot be reached through the API, which is the point.
    """
    claim_id = claims_in_stage(KAYA, "settled")[0]
    ctx = await context_for(db, *KAYA)
    claim_pk = await claim_pk_of(db, claim_id)
    week = (
        await db.scalars(
            sa.select(PaymentScheduleWeek)
            .where(PaymentScheduleWeek.claim_id == claim_pk)
            .order_by(PaymentScheduleWeek.week_no)
            .limit(1)
        )
    ).one()

    # Force an approvable status *and* remember the version, then move the
    # status alone — the version stays where the caller read it.
    week.status = ScheduleWeekStatus.pending_approval
    await db.commit()
    held_version = week.version
    await db.execute(
        sa.update(PaymentScheduleWeek)
        .where(PaymentScheduleWeek.id == week.id)
        .values(status=ScheduleWeekStatus.paid)
    )
    await db.commit()

    with pytest.raises(StalePaymentRow) as refused:
        await approve_schedule_week(
            db,
            ctx,
            claim_pk=claim_pk,
            claim_ref=claim_id,
            week_no=week.week_no,
            expected_version=held_version,
        )
    assert refused.value.status is ScheduleWeekStatus.paid


async def test_a_bill_nobody_has_submitted_cannot_be_approved(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """`pending_submission` is the provider's state, not the handler's.

    The prototype's own predicate (`canApprove = status === "UnderReview"`),
    and the reason the two approvable sets differ between the tables.
    """
    claim_id = claims_in_stage(KAYA, "treatment")[0]
    ctx = await context_for(db, *KAYA)
    claim_pk = await claim_pk_of(db, claim_id)
    bill = (
        await db.scalars(
            sa.select(Bill).where(Bill.claim_id == claim_pk).order_by(Bill.id).limit(1)
        )
    ).one()
    bill.status = LineItemStatus.pending_submission
    await db.commit()

    with pytest.raises(StalePaymentRow):
        await approve_bill_payment(
            db,
            ctx,
            claim_pk=claim_pk,
            claim_ref=claim_id,
            bill_id=bill.id,
            expected_version=bill.version,
        )


async def test_an_unknown_row_id_raises_payment_not_visible(
    db: AsyncSession, seeded_db_url: str
) -> None:
    claim_id = claims_in_stage(KAYA, "treatment")[0]
    ctx = await context_for(db, *KAYA)
    with pytest.raises(PaymentNotVisible):
        await approve_bill_payment(
            db,
            ctx,
            claim_pk=1,
            claim_ref=claim_id,
            bill_id=10_000_000,
            expected_version=1,
        )


async def test_the_command_refuses_a_supervisor_before_touching_the_claim(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """The worklist command's own gate, without a router in the way (AD-7)."""
    ctx = await context_for(db, *JENNIFER)
    with pytest.raises(ApprovalNotPermitted):
        await approve_payment(
            db,
            ctx,
            "WC-99999",
            kind="week",
            target_id=1,
            expected_version=1,
            batch_weekdays=WEEKDAYS,
        )


# --- the invariant, stated as a test ------------------------------------


async def test_no_approval_anywhere_writes_paid(db: AsyncSession, seeded_db_url: str) -> None:
    """Row 29 versus rows 64–66, decided.

    Every approvable status on every table is walked, each is approved, and
    the landing status is checked. `paid` never appears — which is the
    behavioural half of the invariant `tests/test_payment_ownership.py` holds
    structurally.
    """
    assert ScheduleWeekStatus.paid not in APPROVABLE_WEEK_STATUSES
    assert LineItemStatus.paid not in APPROVABLE_LINE_ITEM_STATUSES

    claim_id = claims_in_stage(KAYA, "intake")[0]
    ctx = await context_for(db, *KAYA)
    claim_pk = await claim_pk_of(db, claim_id)

    async def reset(table: Any, row_id: int, status: Any) -> int:
        """Put one row back into a given state and return its fresh version.

        A Core UPDATE and a fresh SELECT rather than ORM attribute assignment,
        because the command under test writes with `synchronize_session=False`
        and `expire_all()` — so the session's copy of this row is either stale
        or expired, and reading it in a plain expression is implicit IO on an
        `AsyncSession` (which raises rather than blocking). Going through SQL
        each time keeps the loop's state exactly the database's.
        """
        await db.execute(sa.update(table).where(table.id == row_id).values(status=status))
        await db.commit()
        db.expire_all()
        version: int = (await db.scalars(sa.select(table.version).where(table.id == row_id))).one()
        return version

    week_id, week_no = (
        await db.execute(
            sa.select(PaymentScheduleWeek.id, PaymentScheduleWeek.week_no)
            .where(PaymentScheduleWeek.claim_id == claim_pk)
            .order_by(PaymentScheduleWeek.week_no)
            .limit(1)
        )
    ).one()

    for status in sorted(APPROVABLE_WEEK_STATUSES):
        version = await reset(PaymentScheduleWeek, week_id, status)
        await approve_schedule_week(
            db,
            ctx,
            claim_pk=claim_pk,
            claim_ref=claim_id,
            week_no=week_no,
            expected_version=version,
        )
        db.expire_all()
        landed = (
            await db.scalars(
                sa.select(PaymentScheduleWeek.status).where(PaymentScheduleWeek.id == week_id)
            )
        ).one()
        assert landed is ScheduleWeekStatus.payment_scheduled, status

    expense_ids = (
        await db.scalars(sa.select(Expense.id).where(Expense.claim_id == claim_pk).limit(1))
    ).all()
    for expense_id in expense_ids:
        version = await reset(Expense, expense_id, LineItemStatus.under_review)
        await approve_expense_payment(
            db,
            ctx,
            claim_pk=claim_pk,
            claim_ref=claim_id,
            expense_id=expense_id,
            expected_version=version,
        )
        db.expire_all()
        landed_expense = (
            await db.scalars(sa.select(Expense.status).where(Expense.id == expense_id))
        ).one()
        assert landed_expense is LineItemStatus.payment_scheduled


async def test_the_audit_action_names_the_command_that_wrote_it(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """One action per command, so the log answers "what wrote this"."""
    actions = set(
        (
            await db.scalars(
                sa.select(AuditEvent.action).where(AuditEvent.action.like("approve_%"))
            )
        ).all()
    )
    assert actions <= {APPROVE_WEEK_ACTION, APPROVE_BILL_ACTION, "approve_expense_payment"}
