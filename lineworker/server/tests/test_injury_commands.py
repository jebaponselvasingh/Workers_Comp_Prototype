"""Story 2.4 — the three injury writes, against a real database.

The pure half (the severity domain, the region vocabulary, the injury-type
text rules, the banding property) is `test_injury_validation.py`. What is
left is everything that is only true of a *transaction*: that the
compare-and-swaps are atomic, that the claim row, the audit event and the
timeline event land together or not at all, that a supervisor is refused
before the claim is looked up, that a removal audits the row it destroyed,
and that the entity coming back was recomputed rather than echoed.

Driven through the app for the contract tests and through the commands for
the atomicity ones, because a rollback is not observable over HTTP.

**These tests mutate the seeded portfolio.** The module-scoped
`seeded_db_url` fixture rebuilds the schema for this file, so the mutations
are contained here — but they persist *between* tests in this module, which
is why nothing below hardcodes a version number: every helper reads the
current one first, the way a client does.
"""

from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AdditionalInjury, AppUser, AuditEvent, Claim, TimelineEvent
from data.models.enums import BodyRegion, TimelineTag
from data.repositories.identity import employer_ids_for
from services.claims.detail import ClaimDetail, ClaimNotVisible, claim_detail
from services.claims.edit import (
    EditNotPermitted,
    InvalidPatch,
    StaleClaim,
    update_claim_severity,
)
from services.claims.injuries import (
    ADD_ACTION,
    REMOVE_ACTION,
    add_additional_injury,
    remove_additional_injury,
)
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

KAYA = ("Kaya Johnson", "handler")
SARAH = ("Sarah Williams", "handler")
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
    assert len(match) == 1
    assert (await client.post("/auth/login", json={"personaId": match[0]["id"]})).status_code == 200


def a_claim_of(persona: tuple[str, str], stage: str) -> str:
    claims = sorted(
        (c for c in seed_fixture.claims_for(*persona) if c["stage"] == stage),
        key=lambda c: c["claim_id"],
    )
    assert claims, f"the seed has no {stage} claim for {persona[0]}"
    return str(claims[0]["claim_id"])


async def current(client: httpx.AsyncClient, claim_id: str) -> dict[str, Any]:
    response = await client.get(f"/claims/{claim_id}")
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def add_via_api(
    client: httpx.AsyncClient,
    claim_id: str,
    *,
    body_key: str = BodyRegion.hand_left.value,
    injury_type: str = "Laceration",
    severity_score: int = 40,
) -> httpx.Response:
    claim = await current(client, claim_id)
    return await client.post(
        f"/claims/{claim_id}/injuries",
        json={
            "expectedVersion": claim["version"],
            "bodyKey": body_key,
            "injuryType": injury_type,
            "severityScore": severity_score,
        },
    )


def markers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    injury: list[dict[str, Any]] = payload["injury"]["markers"]
    return injury


def secondaries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [marker for marker in markers(payload) if not marker["primary"]]


# --- AC 1: the payload --------------------------------------------------


async def test_the_case_file_carries_the_diagram_at_every_stage(seeded_db_url: str) -> None:
    """The tab is readable on a settled claim as much as an intake one.

    Which is why the block is on the case file rather than on a stage
    variant: putting it on one would have meant four copies, or a tab that
    disappeared when a claim settled.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        for stage in ("intake", "investigation", "treatment", "settled"):
            payload = await current(client, a_claim_of(KAYA, stage))
            injury = payload["injury"]
            assert len(injury["markers"]) >= 1
            assert injury["treatmentPlan"], "every seeded claim has five steps"
            assert injury["prognosis"]["mmi"]
            assert injury["contraindications"]


async def test_the_primary_marker_is_the_claim_and_carries_the_headers_band(
    seeded_db_url: str,
) -> None:
    """One claim, one band — the gauge's and the diagram's are the same value.

    Not "equal by coincidence": the assembly passes the header's `risk` into
    the primary marker rather than re-deriving it, so this is the assertion
    that would fail the moment somebody computed it twice.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        payload = await current(client, a_claim_of(KAYA, "treatment"))

        primary = markers(payload)[0]
        assert primary["primary"] is True
        assert primary["id"] is None and primary["version"] is None
        assert primary["bodyKey"] == payload["header"]["bodyKey"]
        assert primary["severityScore"] == payload["header"]["severityScore"]
        assert primary["band"] == payload["header"]["risk"]


async def test_the_treatment_plan_is_the_seed_in_step_order(seeded_db_url: str) -> None:
    """Numbered from `step_no`, and the numbers are the seed file's order."""
    claim_id = a_claim_of(KAYA, "treatment")
    expected = seed_fixture.treatment_plan_for(claim_id)

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        plan = (await current(client, claim_id))["injury"]["treatmentPlan"]

    assert [step["description"] for step in plan] == expected
    assert [step["stepNo"] for step in plan] == list(range(1, len(expected) + 1))


async def test_the_prognosis_is_the_seeds(seeded_db_url: str) -> None:
    claim_id = a_claim_of(KAYA, "treatment")
    expected = seed_fixture.prognosis_for(claim_id)

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        prognosis = (await current(client, claim_id))["injury"]["prognosis"]

    assert prognosis == expected


async def test_no_claim_starts_with_a_secondary_injury(seeded_db_url: str) -> None:
    """`additional_injury` seeds empty, and that is the correct row count.

    The prototype's store has no entries for any of the 100 claims, so there
    is nothing to extract; the epic's "creates + seeds" phrasing reads as if
    there were. Asserted rather than left implicit, because a later seed
    migration that invented clinical data would otherwise pass silently.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        for stage in ("intake", "investigation"):
            payload = await current(client, a_claim_of(KAYA, stage))
            assert secondaries(payload) == []


# --- AC 2: adding -------------------------------------------------------


async def test_adding_an_injury_returns_the_case_file_with_the_new_marker(
    seeded_db_url: str,
) -> None:
    claim_id = a_claim_of(KAYA, "intake")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        before = len(secondaries(await current(client, claim_id)))

        response = await add_via_api(client, claim_id, body_key=BodyRegion.tibia_right.value)
        assert response.status_code == 201, response.text

        added = secondaries(response.json())
        assert len(added) == before + 1
        new = added[-1]
        assert new["bodyKey"] == "tibia_right"
        # The label is the server's answer for the key, never the caller's.
        assert new["bodyPart"] == "Right Lower Leg"
        assert new["injuryType"] == "Laceration"
        assert new["id"] is not None and new["version"] == 1
        assert new["primary"] is False
        # And it survives a re-read: the response is the persisted entity.
        assert len(secondaries(await current(client, claim_id))) == before + 1


async def test_a_secondary_is_banded_by_its_own_score(seeded_db_url: str) -> None:
    """Two markers on one claim, in two bands.

    The prototype's own behaviour, and the thing a payload that reused the
    claim's band for every marker would get wrong.
    """
    claim_id = a_claim_of(KAYA, "investigation")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        low = await add_via_api(client, claim_id, injury_type="Bruise", severity_score=1)
        assert low.status_code == 201, low.text
        high = await add_via_api(
            client,
            claim_id,
            body_key=BodyRegion.head.value,
            injury_type="Concussion",
            severity_score=100,
        )
        assert high.status_code == 201, high.text

        bands = {marker["severityScore"]: marker["band"] for marker in secondaries(high.json())}

    assert bands[1] == "low"
    assert bands[100] == "high"


async def test_adding_writes_an_audit_event_and_a_timeline_event_together(
    db: AsyncSession,
) -> None:
    """AD-4: one transaction, three rows, or none of them."""
    ctx = await context_for(db, *KAYA)
    claim_id = a_claim_of(KAYA, "settled")
    claim = (await db.scalars(sa.select(Claim).where(Claim.claim_id == claim_id))).one()

    events_before = await _count(db, TimelineEvent, TimelineEvent.claim_id == claim.id)
    audits_before = await _count(db, AuditEvent, AuditEvent.action == ADD_ACTION)

    await add_additional_injury(
        db,
        ctx,
        claim_id,
        expected_version=claim.version,
        body_key=BodyRegion.shoulder_left.value,
        injury_type="Rotator Cuff Tear",
        severity_score=55,
    )

    assert await _count(db, TimelineEvent, TimelineEvent.claim_id == claim.id) == events_before + 1
    assert await _count(db, AuditEvent, AuditEvent.action == ADD_ACTION) == audits_before + 1

    event = (
        await db.scalars(
            sa.select(TimelineEvent)
            .where(TimelineEvent.claim_id == claim.id)
            .order_by(TimelineEvent.id.desc())
            .limit(1)
        )
    ).one()
    assert event.tag == TimelineTag.edit.value
    # Names the region, from the server's closed vocabulary — never the free
    # text the caller typed (AD-11, AD-16).
    assert event.description == "Additional injury recorded (Left Shoulder)"
    assert "Rotator Cuff Tear" not in event.description

    audit = (
        await db.scalars(
            sa.select(AuditEvent)
            .where(AuditEvent.action == ADD_ACTION)
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
    ).one()
    assert audit.entity == "additional_injury"
    assert audit.before is None
    assert audit.after == {
        "claim_id": claim_id,
        "body_key": "shoulder_left",
        "body_part": "Left Shoulder",
        "injury_type": "Rotator Cuff Tear",
        "severity_score": 55,
    }
    assert audit.actor_id == ctx.user_id


async def test_adding_against_a_stale_version_conflicts_and_writes_nothing(
    db: AsyncSession,
) -> None:
    ctx = await context_for(db, *KAYA)
    claim_id = a_claim_of(KAYA, "treatment")
    claim = (await db.scalars(sa.select(Claim).where(Claim.claim_id == claim_id))).one()
    before = await _count(db, AdditionalInjury, AdditionalInjury.claim_id == claim.id)

    with pytest.raises(StaleClaim) as conflict:
        await add_additional_injury(
            db,
            ctx,
            claim_id,
            expected_version=claim.version + 99,
            body_key=BodyRegion.torso.value,
            injury_type="Contusion",
            severity_score=10,
        )

    # The 409 carries the fresh entity, so the SPA renders current state
    # without a second round trip (AD-9).
    assert conflict.value.fresh.claim_id == claim_id
    assert await _count(db, AdditionalInjury, AdditionalInjury.claim_id == claim.id) == before


async def test_the_insert_is_guarded_by_the_statement_not_only_by_the_pre_check(
    seeded_db_url: str,
) -> None:
    """Force the interleaving the pre-check cannot see.

    Another connection bumps the claim's version between this command's read
    and its insert. Every sequential test passes against a read-then-write
    implementation; only this one fails if the version predicate is not
    inside the `INSERT … SELECT`.
    """
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    other = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            ctx = await context_for(db, *KAYA)
            claim_id = a_claim_of(KAYA, "treatment")
            claim = (await db.scalars(sa.select(Claim).where(Claim.claim_id == claim_id))).one()
            held = claim.version
            before = await _count(db, AdditionalInjury, AdditionalInjury.claim_id == claim.id)

            # The interleaving: somebody else's write lands first.
            async with other.begin() as winner:
                await winner.execute(
                    sa.update(Claim)
                    .where(Claim.claim_id == claim_id)
                    .values(version=Claim.version + 1)
                )

            with pytest.raises(StaleClaim):
                await add_additional_injury(
                    db,
                    ctx,
                    claim_id,
                    expected_version=held,
                    body_key=BodyRegion.torso.value,
                    injury_type="Contusion",
                    severity_score=10,
                )

        async with maker() as check:
            assert (
                await _count(check, AdditionalInjury, AdditionalInjury.claim_id == claim.id)
                == before
            )
    finally:
        await engine.dispose()
        await other.dispose()


# --- AC 2: removing -----------------------------------------------------


async def test_removing_an_injury_audits_the_row_it_destroyed(db: AsyncSession) -> None:
    """`before` is the whole row and `after` is null.

    A whole-row diff is right here for the reason it is wrong in
    `update_claim_fields`: the row *is* the change, and a diff recording less
    would leave the log unable to say what was deleted.
    """
    ctx = await context_for(db, *KAYA)
    claim_id = a_claim_of(KAYA, "investigation")
    claim = (await db.scalars(sa.select(Claim).where(Claim.claim_id == claim_id))).one()

    detail = await add_additional_injury(
        db,
        ctx,
        claim_id,
        expected_version=claim.version,
        body_key=BodyRegion.forearm_right.value,
        injury_type="Abrasion",
        severity_score=12,
    )
    added = [marker for marker in detail.injury.markers if not marker.primary][-1]
    assert added.id is not None and added.version is not None

    after = await remove_additional_injury(
        db, ctx, claim_id, added.id, expected_version=added.version
    )

    assert [marker.id for marker in after.injury.markers if not marker.primary] != [added.id]

    audit = (
        await db.scalars(
            sa.select(AuditEvent)
            .where(AuditEvent.action == REMOVE_ACTION)
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
    ).one()
    assert audit.entity_id == str(added.id)
    assert audit.after is None
    assert audit.before == {
        "claim_id": claim_id,
        "body_key": "forearm_right",
        "body_part": "Right Forearm",
        "injury_type": "Abrasion",
        "severity_score": 12,
    }

    event = (
        await db.scalars(
            sa.select(TimelineEvent)
            .where(TimelineEvent.claim_id == claim.id)
            .order_by(TimelineEvent.id.desc())
            .limit(1)
        )
    ).one()
    assert event.description == "Additional injury removed (Right Forearm)"


async def test_removing_with_a_stale_row_version_conflicts(db: AsyncSession) -> None:
    ctx = await context_for(db, *KAYA)
    claim_id = a_claim_of(KAYA, "investigation")
    claim = (await db.scalars(sa.select(Claim).where(Claim.claim_id == claim_id))).one()

    detail = await add_additional_injury(
        db,
        ctx,
        claim_id,
        expected_version=claim.version,
        body_key=BodyRegion.ears.value,
        injury_type="Tinnitus",
        severity_score=20,
    )
    added = [marker for marker in detail.injury.markers if not marker.primary][-1]
    assert added.id is not None and added.version is not None

    with pytest.raises(StaleClaim):
        await remove_additional_injury(
            db, ctx, claim_id, added.id, expected_version=added.version + 5
        )

    # Refused, not removed.
    still_there = await claim_detail(db, ctx, claim_id)
    assert added.id in [marker.id for marker in still_there.injury.markers]


async def test_removing_an_injury_that_is_not_there_is_the_claims_own_404(
    db: AsyncSession,
) -> None:
    """One 404 for "no such injury" and for "no such claim".

    A distinct "no such injury" would confirm that the *claim* exists, which
    is precisely the enumeration oracle `select_claim_detail`'s
    single-answer rule closes.
    """
    ctx = await context_for(db, *KAYA)
    with pytest.raises(ClaimNotVisible):
        await remove_additional_injury(
            db, ctx, a_claim_of(KAYA, "treatment"), 999_999, expected_version=1
        )


async def test_a_handler_cannot_remove_another_books_injury(seeded_db_url: str) -> None:
    """The scope predicate is on the DELETE, not only on the read before it."""
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            kaya = await context_for(db, *KAYA)
            sarah = await context_for(db, *SARAH)
            claim_id = a_claim_of(KAYA, "treatment")
            claim = (await db.scalars(sa.select(Claim).where(Claim.claim_id == claim_id))).one()

            detail = await add_additional_injury(
                db,
                ctx := kaya,
                claim_id,
                expected_version=claim.version,
                body_key=BodyRegion.hand_right.value,
                injury_type="Crush",
                severity_score=61,
            )
            assert ctx is kaya
            added = [marker for marker in detail.injury.markers if not marker.primary][-1]
            assert added.id is not None and added.version is not None

            # Sarah cannot see the claim, so she cannot reach its injuries —
            # and the answer says nothing about whether either exists.
            with pytest.raises(ClaimNotVisible):
                await remove_additional_injury(
                    db, sarah, claim_id, added.id, expected_version=added.version
                )
    finally:
        await engine.dispose()


# --- AC 3: the severity score -------------------------------------------


async def test_editing_the_severity_score_moves_the_band_everywhere_at_once(
    seeded_db_url: str,
) -> None:
    """The gauge, the primary marker and the queue card, from one write.

    None of them because the command told them to — all of them because the
    band is a derivation over the column that changed (AD-10). Asserted
    across two endpoints so a payload that cached a band would fail.
    """
    claim_id = a_claim_of(KAYA, "treatment")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim = await current(client, claim_id)

        response = await client.patch(
            f"/claims/{claim_id}/severity",
            json={"expectedVersion": claim["version"], "severityScore": 100},
        )
        assert response.status_code == 200, response.text
        edited = response.json()

        assert edited["header"]["severityScore"] == 100
        assert edited["header"]["risk"] == "high"
        assert markers(edited)[0]["band"] == "high"
        assert edited["version"] == claim["version"] + 1

        # …and the queue agrees, having been asked separately.
        queue = (await client.get("/claims/queue")).json()
        cards = [
            card
            for group in queue["groups"].values()
            for card in group["items"]
            if card["claimId"] == claim_id
        ]
        assert cards and cards[0]["risk"] == "high"

        # Now the other end of the domain, through the same command.
        lowered = await client.patch(
            f"/claims/{claim_id}/severity",
            json={"expectedVersion": edited["version"], "severityScore": 0},
        )
        assert lowered.status_code == 200, lowered.text
        assert lowered.json()["header"]["risk"] == "low"
        assert markers(lowered.json())[0]["band"] == "low"


async def test_the_severity_edit_is_audited_with_both_numbers(db: AsyncSession) -> None:
    ctx = await context_for(db, *KAYA)
    claim_id = a_claim_of(KAYA, "settled")
    claim = (await db.scalars(sa.select(Claim).where(Claim.claim_id == claim_id))).one()
    before_score = claim.severity_score
    target = 7 if before_score != 7 else 8

    await update_claim_severity(
        db, ctx, claim_id, expected_version=claim.version, severity_score=target
    )

    audit = (
        await db.scalars(
            sa.select(AuditEvent)
            .where(AuditEvent.action == "update_claim_severity")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
    ).one()
    # Integers, not their string forms: the column is numeric, and a log that
    # has to be parsed to be compared will be compared wrongly.
    assert audit.before == {"severity_score": before_score}
    assert audit.after == {"severity_score": target}
    assert audit.entity == "claim"
    assert audit.entity_id == claim_id


async def test_re_submitting_the_same_score_writes_nothing(db: AsyncSession) -> None:
    """2.3's rule: the log is a record of changes, and this is not one."""
    ctx = await context_for(db, *KAYA)
    claim_id = a_claim_of(KAYA, "settled")
    claim = (await db.scalars(sa.select(Claim).where(Claim.claim_id == claim_id))).one()
    audits_before = await _count(db, AuditEvent, AuditEvent.action == "update_claim_severity")

    result = await update_claim_severity(
        db,
        ctx,
        claim_id,
        expected_version=claim.version,
        severity_score=claim.severity_score,
    )

    assert result.version == claim.version
    assert (
        await _count(db, AuditEvent, AuditEvent.action == "update_claim_severity") == audits_before
    )


async def test_a_no_op_on_a_stale_version_still_conflicts(db: AsyncSession) -> None:
    """There is no UPDATE for the compare-and-swap to fail on, so the
    pre-check is what tells a client looking at replaced values to re-read."""
    ctx = await context_for(db, *KAYA)
    claim_id = a_claim_of(KAYA, "settled")
    claim = (await db.scalars(sa.select(Claim).where(Claim.claim_id == claim_id))).one()

    with pytest.raises(StaleClaim):
        await update_claim_severity(
            db,
            ctx,
            claim_id,
            expected_version=claim.version + 50,
            severity_score=claim.severity_score,
        )


async def test_the_command_validates_before_it_conflicts(db: AsyncSession) -> None:
    """A 422 is the more useful answer, and the 409 will still be there."""
    ctx = await context_for(db, *KAYA)
    claim_id = a_claim_of(KAYA, "settled")

    with pytest.raises(InvalidPatch):
        await update_claim_severity(db, ctx, claim_id, expected_version=999_999, severity_score=101)


# --- AD-7: role and scope -----------------------------------------------


@pytest.mark.parametrize("persona", [JENNIFER, ANALYST])
async def test_only_a_handler_may_write(db: AsyncSession, persona: tuple[str, str]) -> None:
    """Refused before the claim is read, so the answer says nothing about it."""
    ctx = await context_for(db, *persona)
    claim_id = a_claim_of(KAYA, "treatment")

    calls: tuple[Coroutine[Any, Any, ClaimDetail], ...] = (
        add_additional_injury(
            db,
            ctx,
            claim_id,
            expected_version=1,
            body_key=BodyRegion.torso.value,
            injury_type="Contusion",
            severity_score=10,
        ),
        remove_additional_injury(db, ctx, claim_id, 1, expected_version=1),
        update_claim_severity(db, ctx, claim_id, expected_version=1, severity_score=10),
    )
    for call in calls:
        with pytest.raises(EditNotPermitted):
            await call


async def test_the_three_routes_answer_the_same_403_for_every_claim(
    seeded_db_url: str,
) -> None:
    """A supervisor learns nothing from the difference between them.

    Their own book, somebody else's, and a claim that does not exist — one
    status, one `type`, three routes.
    """
    mine = a_claim_of(JENNIFER, "treatment")
    theirs = a_claim_of(KAYA, "treatment")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *JENNIFER)
        answers = set()
        for claim_id in (mine, theirs, "WC-99999"):
            for response in (
                await client.post(
                    f"/claims/{claim_id}/injuries",
                    json={
                        "expectedVersion": 1,
                        "bodyKey": "torso",
                        "injuryType": "Contusion",
                        "severityScore": 10,
                    },
                ),
                await client.delete(
                    f"/claims/{claim_id}/injuries/1", params={"expectedVersion": 1}
                ),
                await client.patch(
                    f"/claims/{claim_id}/severity",
                    json={"expectedVersion": 1, "severityScore": 10},
                ),
            ):
                assert response.status_code == 403, response.text
                answers.add((response.status_code, response.json()["type"]))

    assert answers == {(403, "/problems/edit-not-permitted")}


async def test_a_handler_gets_one_404_for_out_of_scope_and_for_absent(
    seeded_db_url: str,
) -> None:
    theirs = a_claim_of(SARAH, "treatment")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        out_of_scope = await client.patch(
            f"/claims/{theirs}/severity", json={"expectedVersion": 1, "severityScore": 10}
        )
        invented = await client.patch(
            "/claims/WC-99999/severity", json={"expectedVersion": 1, "severityScore": 10}
        )

    assert out_of_scope.status_code == invented.status_code == 404
    assert out_of_scope.json()["type"] == invented.json()["type"]
    assert out_of_scope.json()["title"] == invented.json()["title"]


# --- the endpoints' refusals --------------------------------------------


async def test_the_api_refuses_a_score_outside_the_domain(seeded_db_url: str) -> None:
    """Refused by the schema, so it is in the OpenAPI document too — and the
    command refuses it again for the agent tools that never pass through
    Pydantic (AD-13)."""
    claim_id = a_claim_of(KAYA, "treatment")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim = await current(client, claim_id)
        for score in (-1, 101):
            response = await client.patch(
                f"/claims/{claim_id}/severity",
                json={"expectedVersion": claim["version"], "severityScore": score},
            )
            assert response.status_code == 422, response.text
        # …and nothing was written.
        assert (await current(client, claim_id))["version"] == claim["version"]


async def test_the_api_refuses_an_unknown_region_with_a_problem_document(
    seeded_db_url: str,
) -> None:
    claim_id = a_claim_of(KAYA, "treatment")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        response = await add_via_api(client, claim_id, body_key="lower_back")

    assert response.status_code == 422
    problem = response.json()
    assert problem["type"] == "/problems/invalid-patch"
    assert "region" in problem["detail"]
    # AD-11: the refusal names the rule, never the value.
    assert "lower_back" not in problem["detail"]


async def test_the_add_body_refuses_a_caller_supplied_label(seeded_db_url: str) -> None:
    """`extra="forbid"` is the whitelist's outer wall.

    `bodyPart` is the one field a caller might reasonably think it should
    send, and the one it must not: the label is derived from the key.
    """
    claim_id = a_claim_of(KAYA, "treatment")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim = await current(client, claim_id)
        response = await client.post(
            f"/claims/{claim_id}/injuries",
            json={
                "expectedVersion": claim["version"],
                "bodyKey": "torso",
                "bodyPart": "Left Hand",
                "injuryType": "Contusion",
                "severityScore": 10,
            },
        )

    assert response.status_code == 422


async def test_the_delete_conflict_carries_the_fresh_entity(seeded_db_url: str) -> None:
    """The Write-concurrency convention, on a route that is not a PATCH."""
    claim_id = a_claim_of(KAYA, "intake")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        created = await add_via_api(client, claim_id, body_key=BodyRegion.head.value)
        assert created.status_code == 201, created.text
        added = secondaries(created.json())[-1]

        response = await client.delete(
            f"/claims/{claim_id}/injuries/{added['id']}",
            params={"expectedVersion": added["version"] + 3},
        )

    assert response.status_code == 409
    body = response.json()
    assert body["type"] == "/problems/stale-write"
    assert body["claim"]["claimId"] == claim_id
    # The fresh entity still holds the injury, because nothing was deleted.
    assert added["id"] in [marker["id"] for marker in body["claim"]["injury"]["markers"]]


async def test_the_add_route_answers_201_and_the_delete_route_200(seeded_db_url: str) -> None:
    """A row is created, so 201; a row is destroyed and the case file comes
    back, so 200 rather than 204 — the caller needs the diagram without the
    marker."""
    claim_id = a_claim_of(KAYA, "intake")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        created = await add_via_api(client, claim_id, body_key=BodyRegion.ears.value)
        assert created.status_code == 201
        added = secondaries(created.json())[-1]

        removed = await client.delete(
            f"/claims/{claim_id}/injuries/{added['id']}",
            params={"expectedVersion": added["version"]},
        )

    assert removed.status_code == 200
    assert added["id"] not in [marker["id"] for marker in removed.json()["injury"]["markers"]]


async def test_the_writes_are_never_cached(seeded_db_url: str) -> None:
    """Persona-specific answers must not be replayed to the next handler."""
    claim_id = a_claim_of(KAYA, "intake")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        created = await add_via_api(client, claim_id, body_key=BodyRegion.tibia_left.value)
        assert created.headers["cache-control"] == "no-store"

        claim = await current(client, claim_id)
        patched = await client.patch(
            f"/claims/{claim_id}/severity",
            json={"expectedVersion": claim["version"], "severityScore": 33},
        )
        assert patched.headers["cache-control"] == "no-store"


async def _count(db: AsyncSession, model: Any, predicate: Any) -> int:
    return int(
        (await db.scalar(sa.select(sa.func.count()).select_from(model).where(predicate))) or 0
    )
