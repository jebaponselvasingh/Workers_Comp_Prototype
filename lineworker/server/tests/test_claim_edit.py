"""Story 2.3 — the audited CAS write, against a real database.

The pure half (whitelist, normalisation, the diff) is
`test_claim_edit_validation.py`. What is left is everything that is only
true of a *transaction*: that the version guard is atomic, that the claim
row, the audit event and the timeline event land together or not at all,
that a supervisor is refused before the claim is even looked up, and that
the entity coming back was recomputed rather than echoed.

Driven through the app for the contract tests and through the command for
the atomicity ones, because a rollback is not observable over HTTP.

**These tests mutate the seeded portfolio.** The module-scoped
`seeded_db_url` fixture rebuilds the schema for this file, so the mutations
are contained here — but they persist *between* tests in this module, which
is why nothing below hardcodes a version number: every helper reads the
current one first, the way a client would.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, AuditEvent, Claim, TimelineEvent
from data.models.enums import RecoveryWindow, TimelineTag
from data.repositories import claims as claim_repo
from data.repositories.identity import employer_ids_for
from rules.parameters import thresholds_for
from services import derivations
from services.claims import timeline
from services.claims.detail import ClaimNotVisible
from services.claims.edit import (
    ACTION,
    EditNotPermitted,
    InvalidPatch,
    StaleClaim,
    update_claim_fields,
)
from services.derivations import utc_today
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
    """The caller context the API dependency would build (AD-7)."""
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


def a_claim_of(persona: tuple[str, str], stage: str) -> str:
    """A seeded claim id in `stage` from the persona's book, lowest first."""
    claims = sorted(
        (c for c in seed_fixture.claims_for(*persona) if c["stage"] == stage),
        key=lambda c: c["claim_id"],
    )
    assert claims, f"the seed has no {stage} claim for {persona[0]}"
    return str(claims[0]["claim_id"])


def other_claims_of(persona: tuple[str, str], stage: str) -> list[str]:
    return sorted(
        str(c["claim_id"]) for c in seed_fixture.claims_for(*persona) if c["stage"] == stage
    )


async def current(client: httpx.AsyncClient, claim_id: str) -> dict[str, Any]:
    resp = await client.get(f"/claims/{claim_id}")
    assert resp.status_code == 200, resp.text
    payload: dict[str, Any] = resp.json()
    return payload


@dataclass(frozen=True)
class Snapshot:
    """A detached copy of the columns these tests read.

    **Not the ORM object**, and that is the point. A test that holds a
    `Claim` from the session is holding the *identity-mapped* instance the
    command will also use, so `stored.version` re-reads the database on the
    next access and `assert row.version == stored.version + 1` quietly
    compares a value with itself plus one. Copying the columns out makes
    "what it was before" a fact rather than a live reference — which is what
    a test asserting a change actually needs.
    """

    id: int
    version: int
    injury_type: str
    cause: str
    body_key: str
    body_part: str
    icd: str
    icd_desc: str
    disability: str
    recovery: str
    severity_score: int


async def load(db: AsyncSession, claim_id: str) -> Snapshot:
    """The claim's columns as they stand right now, detached from the session."""
    db.expire_all()
    row = (
        await db.execute(
            sa.select(
                Claim.id,
                Claim.version,
                Claim.injury_type,
                Claim.cause,
                Claim.body_key,
                Claim.body_part,
                Claim.icd,
                Claim.icd_desc,
                Claim.disability,
                Claim.recovery,
                Claim.severity_score,
            ).where(Claim.claim_id == claim_id)
        )
    ).one()
    return Snapshot(*row)


async def audit_rows(db: AsyncSession, claim_id: str) -> list[AuditEvent]:
    rows = await db.scalars(
        sa.select(AuditEvent).where(AuditEvent.entity_id == claim_id).order_by(AuditEvent.id)
    )
    return list(rows.all())


async def edit_events(db: AsyncSession, claim_pk: int) -> list[TimelineEvent]:
    rows = await db.scalars(
        sa.select(TimelineEvent)
        .where(TimelineEvent.claim_id == claim_pk, TimelineEvent.tag == TimelineTag.edit.value)
        .order_by(TimelineEvent.id)
    )
    return list(rows.all())


# --- AC 1: the write, the audit event and the timeline event ------------


async def test_an_edit_persists_bumps_the_version_and_returns_the_case_file(
    seeded_db_url: str,
) -> None:
    claim_id = a_claim_of(KAYA, "investigation")
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        before = await current(client, claim_id)

        resp = await client.patch(
            f"/claims/{claim_id}",
            json={"expectedVersion": before["version"], "cause": "Slip on Coolant"},
        )
        assert resp.status_code == 200, resp.text
        after = resp.json()

        assert after["version"] == before["version"] + 1
        assert after["overview"]["cause"] == "Slip on Coolant"
        assert after["header"]["cause"] == "Slip on Coolant"
        # The response is the whole entity, so a re-read must agree with it
        # byte for byte — anything else means the command answered from
        # something other than the persisted row.
        assert await current(client, claim_id) == after


async def test_the_response_is_not_cacheable(seeded_db_url: str) -> None:
    claim_id = a_claim_of(KAYA, "investigation")
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        version = (await current(client, claim_id))["version"]
        resp = await client.patch(
            f"/claims/{claim_id}",
            json={"expectedVersion": version, "injuryType": "Laceration"},
        )

    assert resp.headers["cache-control"] == "no-store"


async def test_the_edit_writes_one_audit_event_carrying_only_the_edited_fields(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """AD-4's fixed schema, and AD-11's rule about what a diff may contain.

    A whole-row snapshot would be the easy implementation and would put
    every PHI column of the claim into a table with a seven-year retention
    floor, for an edit that touched one field.
    """
    claim_id = other_claims_of(KAYA, "treatment")[0]
    stored = await load(db, claim_id)
    ctx = await context_for(db, *KAYA)
    original_cause = stored.cause

    await update_claim_fields(
        db,
        ctx,
        claim_id,
        expected_version=stored.version,
        patch={"cause": "Struck by Falling Tooling"},
    )

    events = await audit_rows(db, claim_id)
    assert len(events) == 1
    event = events[0]
    assert event.action == ACTION
    assert event.entity == "claim"
    assert event.entity_id == claim_id
    assert event.actor_id == ctx.user_id
    assert event.actor_role == ctx.role
    assert event.before == {"cause": original_cause}
    assert event.after == {"cause": "Struck by Falling Tooling"}
    # …and `after` is what the row actually holds now.
    assert (await load(db, claim_id)).cause == "Struck by Falling Tooling"


async def test_the_edit_appends_exactly_one_timeline_event(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """Two fields, one event — the row records the edit, not each field.

    Both values are chosen to differ from what is stored, so the sentence
    below is a statement about the description builder rather than about
    which claim the seed happened to put first.
    """
    claim_id = other_claims_of(KAYA, "treatment")[1]
    stored = await load(db, claim_id)
    icd = "M54.50" if stored.icd != "M54.50" else "G56.01"
    disability = "permanent" if stored.disability != "permanent" else "temporary"

    await update_claim_fields(
        db,
        await context_for(db, *KAYA),
        claim_id,
        expected_version=stored.version,
        # The ICD pair travels together — the command refuses one without the
        # other — so this is three fields in one patch and still one event.
        patch={"icd": icd, "icd_desc": "Low back pain, unspecified", "disability": disability},
    )

    events = await edit_events(db, stored.id)
    assert len(events) == 1
    assert events[0].description == (
        "Injury details updated (ICD-10, ICD-10 description, disability)"
    )
    assert events[0].event_date == utc_today()


async def test_the_new_timeline_event_appears_in_the_case_file(seeded_db_url: str) -> None:
    """AC 4 end to end: the event the command emitted is on the timeline the
    endpoint answers with, without a second request."""
    claim_id = a_claim_of(KAYA, "settled")
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        before = await current(client, claim_id)

        # Derived from the stored value so the patch is guaranteed to be a
        # real change: a value that happened to match would write nothing,
        # and the test would be asserting the no-op path by accident.
        resp = await client.patch(
            f"/claims/{claim_id}",
            json={
                "expectedVersion": before["version"],
                "cause": f"{before['header']['cause']} (revised)",
            },
        )

    after = resp.json()
    assert len(after["overview"]["timeline"]) == len(before["overview"]["timeline"]) + 1
    appended = after["overview"]["timeline"][-1]
    assert appended["tag"] == "edit"
    assert appended["description"] == "Injury details updated (cause)"


async def test_choosing_a_body_key_relabels_the_body_part_in_one_audited_change(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """The prototype's `updateBodyPart`, with an audit trail behind it."""
    claim_id = other_claims_of(KAYA, "treatment")[2]
    stored = await load(db, claim_id)
    before_key, before_label = stored.body_key, stored.body_part
    target = "lumbar" if before_key != "lumbar" else "torso"

    await update_claim_fields(
        db,
        await context_for(db, *KAYA),
        claim_id,
        expected_version=stored.version,
        patch={"body_key": target},
    )

    fresh = await load(db, claim_id)
    assert fresh.body_key == target
    assert fresh.body_part != before_label
    event = (await audit_rows(db, claim_id))[-1]
    assert set(event.before or {}) == {"body_key", "body_part"}
    assert event.before == {"body_key": before_key, "body_part": before_label}


# --- AC 1: atomicity ----------------------------------------------------


async def test_a_failure_after_the_update_leaves_no_trace_of_any_of_the_three(
    db: AsyncSession, seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The "same transaction" half of AD-4, tested the only way it can be.

    The three writes are ordered claim → audit → timeline, so a failure in
    the last one is the case where a non-transactional implementation would
    have already left a changed claim and an audit row behind. If any of the
    three survives, "audited in the same transaction" is decoration.
    """
    claim_id = other_claims_of(KAYA, "settled")[1]
    stored = await load(db, claim_id)
    before_cause, before_version = stored.cause, stored.version
    audit_before = len(await audit_rows(db, claim_id))
    events_before = len(await edit_events(db, stored.id))

    async def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("timeline write failed")

    monkeypatch.setattr(timeline, "append", explode)

    with pytest.raises(RuntimeError):
        await update_claim_fields(
            db,
            await context_for(db, *KAYA),
            claim_id,
            expected_version=before_version,
            patch={"cause": "Never Committed"},
        )
    await db.rollback()

    fresh = await load(db, claim_id)
    assert (fresh.cause, fresh.version) == (before_cause, before_version)
    assert len(await audit_rows(db, claim_id)) == audit_before
    assert len(await edit_events(db, stored.id)) == events_before


# --- AC 2: compare-and-swap --------------------------------------------


async def test_a_stale_version_is_refused_with_the_fresh_entity(seeded_db_url: str) -> None:
    claim_id = a_claim_of(KAYA, "intake")
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        stale = (await current(client, claim_id))["version"]

        # Somebody else's edit lands first.
        winner = await client.patch(
            f"/claims/{claim_id}",
            json={"expectedVersion": stale, "cause": "First Writer Wins"},
        )
        assert winner.status_code == 200

        loser = await client.patch(
            f"/claims/{claim_id}",
            json={"expectedVersion": stale, "cause": "Second Writer Loses"},
        )

    assert loser.status_code == 409
    assert loser.headers["content-type"].startswith("application/problem+json")
    body = loser.json()
    assert body["type"] == "/problems/stale-write"
    # The whole point of the convention: current state travels with the
    # refusal, so the client renders it without a second round trip.
    assert body["claim"]["version"] == stale + 1
    assert body["claim"]["overview"]["cause"] == "First Writer Wins"
    assert body["claim"] == winner.json()


async def test_a_lost_race_does_not_write(seeded_db_url: str) -> None:
    """The refusal is a refusal, not a delayed apply."""
    claim_id = a_claim_of(KAYA, "intake")
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        version = (await current(client, claim_id))["version"]
        await client.patch(
            f"/claims/{claim_id}", json={"expectedVersion": version, "cause": "Winner"}
        )
        await client.patch(
            f"/claims/{claim_id}", json={"expectedVersion": version, "cause": "Loser"}
        )
        latest = await current(client, claim_id)

    assert latest["overview"]["cause"] == "Winner"
    assert latest["version"] == version + 1


async def test_the_update_statement_refuses_a_stale_version_on_its_own(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """The repository's guard, without the command's pre-check in front of it.

    A read-then-write with a Python `if` would pass every endpoint test
    above, because the pre-check catches every *sequential* conflict. What
    it cannot catch is a row that moves between the command's SELECT and its
    UPDATE — and the only thing standing there is `WHERE version = ?` inside
    the statement. Called directly here so that guard is asserted rather than
    inferred.
    """
    claim_id = other_claims_of(SARAH, "settled")[3]
    stored = await load(db, claim_id)
    ctx = await context_for(db, *SARAH)

    stale = await claim_repo.update_claim_fields_cas(
        db, ctx, claim_id, stored.version - 1, {"cause": "Stale Write"}
    )
    assert stale == 0
    assert (await load(db, claim_id)).cause != "Stale Write"

    fresh = await claim_repo.update_claim_fields_cas(
        db, ctx, claim_id, stored.version, {"cause": "Fresh Write"}
    )
    assert fresh == 1
    row = await load(db, claim_id)
    assert (row.cause, row.version) == ("Fresh Write", stored.version + 1)
    await db.rollback()


async def test_the_update_statement_is_scoped_like_every_read(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """A write is not exempt from AD-7 because its caller already checked.

    Sarah is handed a real Caterpillar claim id with the *correct* version.
    The statement must still change nothing — the employer predicate is on
    the UPDATE's own WHERE clause, not on the caller's conscience.
    """
    theirs = next(
        claim_id
        for claim_id in other_claims_of(KAYA, "settled")
        if claim_id not in {c["claim_id"] for c in seed_fixture.claims_for(*SARAH)}
    )
    stored = await load(db, theirs)

    changed = await claim_repo.update_claim_fields_cas(
        db, await context_for(db, *SARAH), theirs, stored.version, {"cause": "Out Of Scope"}
    )

    assert changed == 0
    assert (await load(db, theirs)).cause == stored.cause
    await db.rollback()


async def test_a_race_between_the_read_and_the_write_conflicts(
    db: AsyncSession, seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window the pre-check cannot see, closed end to end.

    Another connection bumps the version *after* the command has read the
    claim and immediately before its UPDATE. The command must answer
    `StaleClaim` carrying the version that won, and must not have written.
    The interleaving is forced rather than raced on a sleep, so this test
    fails deterministically if the guard is removed.
    """
    claim_id = other_claims_of(SARAH, "settled")[4]
    stored = await load(db, claim_id)
    real_cas = claim_repo.update_claim_fields_cas
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))

    async def bump_then_write(*args: Any, **kwargs: Any) -> int:
        async with async_sessionmaker(engine, expire_on_commit=False)() as other:
            await other.execute(
                sa.update(Claim).where(Claim.claim_id == claim_id).values(version=Claim.version + 1)
            )
            await other.commit()
        return await real_cas(*args, **kwargs)

    async def must_not_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a lost race must not reach the timeline")

    monkeypatch.setattr(claim_repo, "update_claim_fields_cas", bump_then_write)
    monkeypatch.setattr(timeline, "append", must_not_run)
    try:
        with pytest.raises(StaleClaim) as caught:
            await update_claim_fields(
                db,
                await context_for(db, *SARAH),
                claim_id,
                expected_version=stored.version,
                patch={"cause": "Raced Write"},
            )
    finally:
        await engine.dispose()

    assert caught.value.fresh.version == stored.version + 1
    assert (await load(db, claim_id)).cause != "Raced Write"


# --- AC 3: derived values come back recomputed --------------------------


async def test_editing_the_recovery_window_recomputes_the_treatment_phase(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """AD-10: the response's derived values are the registry's answer.

    `recovery` is the one editable field that feeds a derivation, so it is
    where "dependent computations recompute" is actually observable. The
    expectation is computed here from `services/derivations`' registered
    `treatment_phase` — the same computer the command must have used, asked
    independently.
    """
    claim_id = other_claims_of(KAYA, "treatment")[3]
    stored = await load(db, claim_id)
    target = (
        RecoveryWindow.weeks_0_2
        if stored.recovery != RecoveryWindow.weeks_0_2
        else RecoveryWindow.weeks_6_8
    ).value

    detail = await update_claim_fields(
        db,
        await context_for(db, *KAYA),
        claim_id,
        expected_version=stored.version,
        patch={"recovery": target},
    )

    thresholds = await thresholds_for(db, utc_today())
    phase = derivations.get("treatment_phase").for_thresholds(thresholds)
    expected = phase.of(days_open=detail.overview.days_open, recovery=target)  # type: ignore[union-attr]

    assert detail.overview.recovery == target  # type: ignore[union-attr]
    assert detail.overview.expected_days == expected.expected_days  # type: ignore[union-attr]
    assert detail.overview.phase == expected.phase  # type: ignore[union-attr]


async def test_the_returned_risk_band_is_the_registrys(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """Nothing this story edits moves the band — which is the point.

    The severity score belongs to Story 2.4, so an edit here must leave
    `risk` exactly where the registry puts it. A command that carried the
    band along from its pre-edit read would pass an equality against itself;
    this compares against the derivation, computed here.
    """
    claim_id = other_claims_of(KAYA, "investigation")[0]
    stored = await load(db, claim_id)

    detail = await update_claim_fields(
        db,
        await context_for(db, *KAYA),
        claim_id,
        expected_version=stored.version,
        patch={"icd": "S63.501A", "icd_desc": "Sprain of unspecified site of left wrist"},
    )

    thresholds = await thresholds_for(db, utc_today())
    expected = derivations.get("risk").for_thresholds(thresholds).of(stored.severity_score)
    assert detail.header.risk == expected


# --- AC 1/2: the refusals ----------------------------------------------


@pytest.mark.parametrize("persona", [JENNIFER, ANALYST])
async def test_a_read_only_role_cannot_edit(seeded_db_url: str, persona: tuple[str, str]) -> None:
    """Role gates capability: the dashboards stay read-only (BR-ROLE, AD-7).

    A claim from the persona's *own* book, so the refusal cannot be
    mistaken for a scope miss — and the version is read first, so it is not
    a stale-write refusal wearing a 403 either. The only thing wrong with
    the request is who is making it.
    """
    claim_id = a_claim_of(persona, "investigation")
    async with make_client(seeded_db_url) as client:
        await login_as(client, *persona)
        version = (await current(client, claim_id))["version"]
        resp = await client.patch(
            f"/claims/{claim_id}", json={"expectedVersion": version, "cause": "Not Allowed"}
        )

    assert resp.status_code == 403
    assert resp.json()["type"] == "/problems/edit-not-permitted"


async def test_the_role_refusal_says_nothing_about_which_claims_exist(
    seeded_db_url: str,
) -> None:
    """The ordering assertion: role is checked *before* the claim is read.

    If it were the other way round, a supervisor would get 404 for a claim
    outside their scope and 403 for one inside it — an oracle for walking
    `WC-20000`…`WC-20999`, which is exactly what the detail route's
    single-answer rule closes. All three answers below must be identical.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *JENNIFER)
        mine = a_claim_of(JENNIFER, "treatment")
        theirs = next(
            claim_id
            for claim_id in other_claims_of(KAYA, "treatment")
            if claim_id not in {c["claim_id"] for c in seed_fixture.claims_for(*JENNIFER)}
        )
        answers = [
            await client.patch(f"/claims/{claim_id}", json={"expectedVersion": 1, "cause": "x"})
            for claim_id in (mine, theirs, "WC-99999")
        ]

    assert {resp.status_code for resp in answers} == {403}
    assert len({resp.json()["type"] for resp in answers}) == 1
    assert len({resp.json()["detail"] for resp in answers}) == 1


async def test_the_role_refusal_comes_before_the_patch_is_examined(
    seeded_db_url: str,
) -> None:
    """A malformed patch does not get a non-handler a 422 (code review).

    `ClaimFieldPatch.edited_fields()` used to refuse an explicit `null`, and
    the router evaluates it while *building* the command call — so it fired
    before the command's role check and a supervisor sending
    `{"cause": null}` learned the shape of the patch contract instead of
    being told their role cannot edit. The refusal moved into `normalise`,
    which runs after the role gate.

    No claim data leaked either way; what was wrong is that the module's
    stated ordering ("role first, before anything else is examined") held on
    every path but one — and `_answer` now hands that ordering to four routes
    and to every Epic 3 command that copies them.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *JENNIFER)
        malformed = await client.patch(
            f"/claims/{a_claim_of(JENNIFER, 'treatment')}",
            json={"expectedVersion": 1, "cause": None},
        )
        well_formed = await client.patch(
            f"/claims/{a_claim_of(JENNIFER, 'treatment')}",
            json={"expectedVersion": 1, "cause": "Anything"},
        )

    assert malformed.status_code == 403
    # …and indistinguishable from the well-formed refusal, so the shape of
    # the body teaches a read-only caller nothing.
    assert malformed.json()["type"] == well_formed.json()["type"]
    assert malformed.json()["detail"] == well_formed.json()["detail"]


async def test_the_role_refusal_comes_first_on_all_four_write_routes(
    seeded_db_url: str,
) -> None:
    """The ordering `_answer` promises, asserted on the three routes it was not.

    Story 2.4's review pass fixed the ordering and said `_answer` "now hands
    that ordering to four routes" — but only `PATCH /claims/{id}` had a test.
    Story 2.6's review pass pointed that out, so the other three are pinned
    here: a supervisor gets the same 403, with the same problem `type`, from
    the severity patch, the injury insert and the injury delete.

    Bodies are **schema-valid** on purpose — see
    `test_a_schema_invalid_body_is_refused_before_any_role_check` below, which
    states the one boundary this guarantee does not cross and why it should
    not.
    """
    claim = a_claim_of(JENNIFER, "treatment")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *JENNIFER)
        refusals = [
            await client.patch(
                f"/claims/{claim}/severity", json={"expectedVersion": 1, "severityScore": 50}
            ),
            await client.post(
                f"/claims/{claim}/injuries",
                json={
                    "expectedVersion": 1,
                    "bodyKey": "hand_right",
                    "injuryType": "Laceration",
                    "severityScore": 40,
                },
            ),
            await client.delete(f"/claims/{claim}/injuries/1", params={"expectedVersion": 1}),
        ]

    for refused in refusals:
        assert refused.status_code == 403, refused.text
        assert refused.json()["type"] == refusals[0].json()["type"]


async def test_a_schema_invalid_body_is_refused_before_any_role_check(
    seeded_db_url: str,
) -> None:
    """The boundary the "role first" rule stops at, stated rather than implied.

    Story 2.6's review pass read the 2.4 note ("role first, before anything
    else is examined") literally and found it overstated: `severityScore` is a
    Pydantic `Field(ge=…, le=…)` on the *request model*, so FastAPI refuses an
    out-of-range value before the endpoint function runs at all — before the
    role gate. A supervisor sending `severityScore: 101` therefore gets a 422
    where `severityScore: 50` gets a 403.

    **The constraint is deliberately not being moved into the handler**, and
    that is the finding's real resolution. The bound is part of the published
    contract: it is in the OpenAPI document, it reaches the generated
    TypeScript client, and Story 2.4 put it there for exactly that reason. What
    it can leak is nothing — the range 0–100 is public in the schema every
    caller downloads, and no claim data is involved.

    So the guarantee is narrower than the note claimed, and this is where the
    real one is written down: **once a request is a well-formed instance of the
    published contract, the role is checked before anything about the claim or
    the patch is examined.** Pinned so that a later story cannot quietly widen
    the schema layer into one that does touch claim state.
    """
    claim = a_claim_of(JENNIFER, "treatment")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *JENNIFER)
        out_of_range = await client.patch(
            f"/claims/{claim}/severity", json={"expectedVersion": 1, "severityScore": 101}
        )
        in_range = await client.patch(
            f"/claims/{claim}/severity", json={"expectedVersion": 1, "severityScore": 50}
        )

    assert out_of_range.status_code == 422
    assert in_range.status_code == 403
    # The 422 says nothing about the claim — it names the field and the bound,
    # both of which are in the OpenAPI document already.
    body = out_of_range.text
    assert claim not in body


async def test_a_handler_still_gets_the_422_for_an_explicit_null(
    seeded_db_url: str,
) -> None:
    """The other half: the refusal still exists, with its own wording."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        resp = await client.patch(
            f"/claims/{a_claim_of(KAYA, 'investigation')}",
            json={"expectedVersion": 1, "cause": None},
        )

    assert resp.status_code == 422
    assert "cannot be null" in resp.json()["detail"]


async def test_a_handler_editing_outside_their_book_gets_the_same_404_as_the_get(
    seeded_db_url: str,
) -> None:
    theirs = next(
        claim_id
        for claim_id in other_claims_of(KAYA, "treatment")
        if claim_id not in {c["claim_id"] for c in seed_fixture.claims_for(*SARAH)}
    )
    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        edit = await client.patch(
            f"/claims/{theirs}", json={"expectedVersion": 1, "cause": "Not Mine"}
        )
        invented = await client.patch(
            "/claims/WC-99999", json={"expectedVersion": 1, "cause": "Nobody's"}
        )
        read = await client.get(f"/claims/{theirs}")

    assert edit.status_code == invented.status_code == read.status_code == 404
    assert edit.json()["type"] == invented.json()["type"] == read.json()["type"]


async def test_an_unauthenticated_edit_is_refused(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.patch(
            "/claims/WC-20017", json={"expectedVersion": 1, "cause": "Anonymous"}
        )

    assert resp.status_code == 401


@pytest.mark.parametrize(
    "body, why",
    [
        ({"expectedVersion": 1, "severityScore": 99}, "a field another story owns"),
        ({"expectedVersion": 1, "version": 4}, "the concurrency column itself"),
        ({"expectedVersion": 1, "cause": None}, "an explicit null"),
        ({"expectedVersion": 1}, "no fields at all"),
        ({"cause": "No Version"}, "no expectedVersion"),
        ({"expectedVersion": 0, "cause": "Zero"}, "a version below the floor"),
        ({"expectedVersion": 1, "icd": "not-a-code"}, "a malformed ICD-10"),
        ({"expectedVersion": 1, "bodyKey": "left_elbow"}, "a body key off the diagram"),
        ({"expectedVersion": 1, "recovery": "about a month"}, "a free-text recovery window"),
        ({"expectedVersion": 1, "disability": "Permanent"}, "a display label, not the token"),
        ({"expectedVersion": 1, "cause": "   "}, "whitespace"),
    ],
)
async def test_a_patch_that_cannot_be_applied_is_a_422(
    seeded_db_url: str, body: dict[str, Any], why: str
) -> None:
    claim_id = a_claim_of(KAYA, "investigation")
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        before = await current(client, claim_id)
        resp = await client.patch(f"/claims/{claim_id}", json=body)
        after = await current(client, claim_id)

    assert resp.status_code == 422, f"{why}: {resp.text}"
    assert resp.headers["content-type"].startswith("application/problem+json")
    # A refused patch is not a partially applied one.
    assert after == before


async def test_a_no_op_patch_writes_nothing(db: AsyncSession, seeded_db_url: str) -> None:
    """Restating the stored value is not a change, so there is nothing to
    version, audit or log."""
    claim_id = other_claims_of(SARAH, "settled")[0]
    stored = await load(db, claim_id)
    audit_before = len(await audit_rows(db, claim_id))

    detail = await update_claim_fields(
        db,
        await context_for(db, *SARAH),
        claim_id,
        expected_version=stored.version,
        patch={"cause": stored.cause},
    )

    assert detail.version == stored.version
    assert len(await audit_rows(db, claim_id)) == audit_before
    assert await edit_events(db, stored.id) == []


async def test_a_no_op_patch_on_a_stale_version_still_conflicts(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """There is no UPDATE for the compare-and-swap to fail on, so this is
    the case the pre-check exists for. Telling the caller their version is
    stale is more honest than answering 200 to a client that is looking at
    values somebody else has already replaced."""
    claim_id = other_claims_of(SARAH, "settled")[1]
    stored = await load(db, claim_id)

    with pytest.raises(StaleClaim):
        await update_claim_fields(
            db,
            await context_for(db, *SARAH),
            claim_id,
            expected_version=stored.version - 1,
            patch={"cause": stored.cause},
        )


async def test_the_command_refuses_a_non_handler_before_touching_the_database(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """Asserted at the command, not only at the endpoint: an agent tool
    (AD-13) reaches this function without passing through the router."""
    with pytest.raises(EditNotPermitted):
        await update_claim_fields(
            db,
            await context_for(db, *JENNIFER),
            "WC-99999",
            expected_version=1,
            patch={"cause": "x"},
        )


async def test_the_command_refuses_an_unknown_claim(db: AsyncSession, seeded_db_url: str) -> None:
    with pytest.raises(ClaimNotVisible):
        await update_claim_fields(
            db,
            await context_for(db, *KAYA),
            "WC-99999",
            expected_version=1,
            patch={"cause": "x"},
        )


async def test_the_command_validates_before_it_conflicts(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """Both wrong: a bad value *and* a stale version. The 422 wins, because
    it tells the caller something about the request they wrote, and the
    conflict will still be there when they fix it."""
    claim_id = other_claims_of(SARAH, "settled")[2]
    stored = await load(db, claim_id)

    with pytest.raises(InvalidPatch):
        await update_claim_fields(
            db,
            await context_for(db, *SARAH),
            claim_id,
            expected_version=stored.version - 1,
            patch={"icd": "nonsense"},
        )


# --- the guards the first review found uncovered -------------------------


async def test_the_conflict_response_survives_without_the_rollback(
    db: AsyncSession, seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`conflict`'s `expire_all()` on its own, with the rollback removed.

    Both lines were documented as load-bearing and **neither had a test**:
    deleting either left all 35 tests green, because the `db.rollback()` on
    the lost-CAS path already expires everything and was silently doing the
    whole job (code review). This pins the other half — with the rollback
    stubbed out, the fresh entity in the conflict must still be fresh.

    That matters beyond bookkeeping. A lost compare-and-swap matches zero
    rows, but `synchronize_session="evaluate"` re-applies the change to the
    in-session object in Python, so without an expire the 409 would carry the
    winner's version attached to the loser's value.
    """
    claim_id = other_claims_of(SARAH, "settled")[5]
    stored = await load(db, claim_id)
    real_cas = claim_repo.update_claim_fields_cas
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))

    async def bump_then_write(*args: Any, **kwargs: Any) -> int:
        async with async_sessionmaker(engine, expire_on_commit=False)() as other:
            await other.execute(
                sa.update(Claim)
                .where(Claim.claim_id == claim_id)
                .values(version=Claim.version + 1, cause="Won By Someone Else")
            )
            await other.commit()
        return await real_cas(*args, **kwargs)

    async def no_rollback() -> None:
        return None

    monkeypatch.setattr(claim_repo, "update_claim_fields_cas", bump_then_write)
    monkeypatch.setattr(db, "rollback", no_rollback)
    try:
        with pytest.raises(StaleClaim) as caught:
            await update_claim_fields(
                db,
                await context_for(db, *SARAH),
                claim_id,
                expected_version=stored.version,
                patch={"cause": "Lost The Race"},
            )
    finally:
        await engine.dispose()

    fresh = caught.value.fresh
    assert fresh.version == stored.version + 1
    assert fresh.header.cause == "Won By Someone Else"


async def test_the_response_is_read_back_from_the_database_not_from_the_session(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """The post-commit `expire_all()`, which also had no coverage.

    A second connection is what proves it: the command commits, and the
    entity it returns must match what that other connection can see. An
    implementation that answered from the identity map would agree with
    itself and disagree with the database.
    """
    claim_id = other_claims_of(SARAH, "settled")[4]
    stored = await load(db, claim_id)

    detail = await update_claim_fields(
        db,
        await context_for(db, *SARAH),
        claim_id,
        expected_version=stored.version,
        patch={"injury_type": "Read Back From Postgres"},
    )

    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine)() as other:
            row = (
                await other.execute(
                    sa.select(Claim.injury_type, Claim.version).where(Claim.claim_id == claim_id)
                )
            ).one()
    finally:
        await engine.dispose()

    assert detail.header.injury_type == row[0] == "Read Back From Postgres"
    assert detail.version == row[1] == stored.version + 1


async def test_the_command_derives_only_through_the_registry(
    db: AsyncSession, seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AD-10, as a spy rather than as a value comparison (Task 4).

    The subtask this replaces asked for "editing `disability` or `body_key`
    recomputes derived values through the registered derivation functions
    only" — which could not be written as specified, because neither field
    feeds any derivation. `recovery` does: it is the one editable field that
    reaches a registered computer, `treatment_phase`.

    Comparing the response against a separately-computed expectation (as the
    other two tests here do) proves the *answer* is right; it cannot prove
    the command did not compute it inline and happen to agree. Wrapping the
    registry entry's builder can: if the command derives the phase any other
    way, the spy never fires.
    """
    claim_id = other_claims_of(KAYA, "treatment")[4]
    stored = await load(db, claim_id)
    target = (
        RecoveryWindow.weeks_0_2
        if stored.recovery != RecoveryWindow.weeks_0_2
        else RecoveryWindow.weeks_6_8
    )

    # `Derivation` is frozen, so the *entry* is replaced rather than
    # mutated — which is the stronger assertion in any case: it proves the
    # command reached the registered object, not merely that some
    # `TreatmentPhaseDerivation` was constructed somewhere.
    entry = derivations.get("treatment_phase")
    calls: list[Any] = []
    real_build = entry.build

    def spy(thresholds: Any) -> Any:
        calls.append(thresholds)
        return real_build(thresholds)

    monkeypatch.setattr(derivations, "treatment_phase", replace(entry, build=spy))

    detail = await update_claim_fields(
        db,
        await context_for(db, *KAYA),
        claim_id,
        expected_version=stored.version,
        patch={"recovery": target.value},
    )

    assert calls, "the response's treatment phase did not come from the registry"
    assert detail.overview.recovery is target  # type: ignore[union-attr]
    # …and the phase moved with it, through that same computer.
    expected = real_build(calls[0]).of(
        days_open=detail.overview.days_open,  # type: ignore[union-attr]
        recovery=target,
    )
    assert detail.overview.expected_days == expected.expected_days  # type: ignore[union-attr]
