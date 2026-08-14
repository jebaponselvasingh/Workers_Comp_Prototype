"""Story 3.1 AC 4 — the comp-rate override, against a real database.

The pure half of the calculation is `test_benefit_calculation.py`. What is left
is everything that is only true of a *transaction* and of the contract around
it: that the version guard is atomic, that the claim row, the audit event and
the timeline event land together or not at all, that a supervisor is refused
before the claim is looked up, and — the acceptance criterion itself — that the
benefit coming back was **recomputed server-side** rather than echoed.

Driven through the app for the contract tests and through the command for the
atomicity ones, because a rollback is not observable over HTTP.

**These tests mutate the seeded portfolio.** The module-scoped `seeded_db_url`
fixture rebuilds the schema for this file, so the mutations are contained here
— but they persist *between* tests in this module, which is why nothing below
hardcodes a version number: every helper reads the current one first, the way a
client would.
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
from data.models import AppUser, AuditEvent, Claim, TimelineEvent
from data.models.enums import TimelineTag
from data.repositories import claims as claim_repo
from data.repositories.identity import employer_ids_for
from services.claims import timeline
from services.claims.comp_rate import ACTION, COLUMN, update_comp_rate_override
from services.claims.edit import EditNotPermitted, InvalidPatch, StaleClaim
from services.financials import COMP_RATE_MAX_BP, COMP_RATE_MIN_BP
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

KAYA = ("Kaya Johnson", "handler")
SARAH = ("Sarah Williams", "handler")
JENNIFER = ("Jennifer Park", "supervisor")
ANALYST = ("David Bline", "analyst")

#: A rate that is not the statutory default, so "the override applied" is
#: distinguishable from "nothing happened".
OVERRIDE_BP = 7_025


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


def claims_of(persona: tuple[str, str], stage: str) -> list[str]:
    return sorted(
        str(c["claim_id"]) for c in seed_fixture.claims_for(*persona) if c["stage"] == stage
    )


def a_claim_of(persona: tuple[str, str], stage: str) -> str:
    """A seeded claim id in `stage` from the persona's book, lowest first.

    Read from the seed rather than typed in — Story 1.4's rule, and the one
    `test_photos_tab.py` records tripping over: `WC-20017` is nobody's default.
    """
    claims = claims_of(persona, stage)
    assert claims, f"the seed has no {stage} claim for {persona[0]}"
    return claims[0]


def a_claim_outside(persona: tuple[str, str], other: tuple[str, str]) -> str:
    """A claim in `other`'s book and not in `persona`'s — computed, not assumed."""
    mine = {str(c["claim_id"]) for c in seed_fixture.claims_for(*persona)}
    theirs = sorted(
        str(c["claim_id"])
        for c in seed_fixture.claims_for(*other)
        if str(c["claim_id"]) not in mine
    )
    assert theirs, f"{other} has no claim outside {persona}'s book"
    return theirs[0]


async def current(client: httpx.AsyncClient, claim_id: str) -> dict[str, Any]:
    resp = await client.get(f"/claims/{claim_id}")
    assert resp.status_code == 200, resp.text
    payload: dict[str, Any] = resp.json()
    return payload


async def set_rate(
    client: httpx.AsyncClient, claim_id: str, comp_rate_bp: int | None
) -> httpx.Response:
    """One PATCH, at whatever version the claim is on right now."""
    version = (await current(client, claim_id))["version"]
    return await client.patch(
        f"/claims/{claim_id}/comp-rate",
        json={"expectedVersion": version, "compRateBp": comp_rate_bp},
    )


async def stored_override(db: AsyncSession, claim_id: str) -> int | None:
    db.expire_all()
    value = (
        await db.execute(sa.select(Claim.comp_rate_override_bp).where(Claim.claim_id == claim_id))
    ).scalar_one()
    return int(value) if value is not None else None


async def audit_rows(db: AsyncSession, claim_id: str) -> list[AuditEvent]:
    rows = await db.scalars(
        sa.select(AuditEvent)
        .where(AuditEvent.entity_id == claim_id, AuditEvent.action == ACTION)
        .order_by(AuditEvent.id)
    )
    return list(rows.all())


async def benefit_events(db: AsyncSession, claim_id: str) -> list[TimelineEvent]:
    rows = await db.scalars(
        sa.select(TimelineEvent)
        .join(Claim, TimelineEvent.claim_id == Claim.id)
        .where(Claim.claim_id == claim_id, TimelineEvent.tag == TimelineTag.benefit.value)
        .order_by(TimelineEvent.id)
    )
    return list(rows.all())


# --- AC 4: the write, and the recomputation that follows it -------------


async def test_an_override_persists_bumps_the_version_and_recomputes_the_benefit(
    seeded_db_url: str,
) -> None:
    claim_id = a_claim_of(KAYA, "investigation")
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        before = await current(client, claim_id)

        resp = await set_rate(client, claim_id, OVERRIDE_BP)

        assert resp.status_code == 200, resp.text
        after = resp.json()
        assert after["version"] == before["version"] + 1
        assert after["benefit"]["compRateBp"] == OVERRIDE_BP
        assert after["benefit"]["isOverridden"] is True
        # The default is still published — the ↺ has to say what it restores.
        assert after["benefit"]["defaultCompRateBp"] == before["benefit"]["defaultCompRateBp"]
        assert after["benefit"]["weeklyCents"] != before["benefit"]["weeklyCents"]
        # …and the response is the entity, so a re-read agrees with it.
        assert (await current(client, claim_id))["benefit"] == after["benefit"]


async def test_the_weekly_figure_is_the_servers_answer_not_the_clients(
    seeded_db_url: str,
) -> None:
    """AC 4's "recomputes server-side", stated so that only a server could
    satisfy it.

    At 100% of wage the answer is the AWW clamped into the state's range,
    which this test computes from the payload's *own* figures — no rounding,
    no rate arithmetic, nothing that restates the formula. A route that echoed
    the submitted rate without recalculating would fail on the weekly figure
    while looking entirely correct on the rate.
    """
    # An intake claim, because `awwCents` is on that variant and this test
    # needs the wage the server computed from — the investigation variant
    # carries it too, and is left for the tests above.
    claim_id = a_claim_of(KAYA, "intake")
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        aww_cents = (await current(client, claim_id))["overview"]["awwCents"]

        resp = await set_rate(client, claim_id, 10_000)

        assert resp.status_code == 200, resp.text
        benefit = resp.json()["benefit"]
        expected = min(max(aww_cents, benefit["stateMinCents"]), benefit["stateMaxCents"])
        assert benefit["weeklyCents"] == expected


async def test_the_rationale_gains_the_override_sentence_and_loses_it_again(
    seeded_db_url: str,
) -> None:
    """The paragraph is part of the recomputation, not a cached string."""
    claim_id = claims_of(KAYA, "treatment")[0]
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        assert (
            "manually adjusted"
            not in (await current(client, claim_id))["benefit"]["reserveRationale"]
        )

        overridden = (await set_rate(client, claim_id, OVERRIDE_BP)).json()
        assert (
            "manually adjusted to 70.25% (default 66.67%) by handler."
            in (overridden["benefit"]["reserveRationale"])
        )

        reset = (await set_rate(client, claim_id, None)).json()
        assert "manually adjusted" not in reset["benefit"]["reserveRationale"]


async def test_the_reset_restores_the_default_exactly(seeded_db_url: str) -> None:
    """AC 4's ↺: not merely a similar benefit, the same one."""
    claim_id = claims_of(KAYA, "treatment")[1]
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        original = (await current(client, claim_id))["benefit"]

        assert (await set_rate(client, claim_id, 12_000)).status_code == 200
        restored = (await set_rate(client, claim_id, None)).json()["benefit"]

    assert restored == original


async def test_the_column_holds_basis_points_and_null_after_a_reset(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """What is actually in the row — integers and a real NULL.

    A reset written as `0` would look identical on the card for a claim whose
    benefit clamps to the state minimum, and would mean something completely
    different: an override of zero percent rather than no override at all.
    """
    claim_id = claims_of(KAYA, "settled")[0]
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        await set_rate(client, claim_id, 9_999)
        assert await stored_override(db, claim_id) == 9_999

        await set_rate(client, claim_id, None)
        assert await stored_override(db, claim_id) is None


# --- AD-4: the audit event and the timeline event -----------------------


async def test_the_write_emits_one_audit_event_with_integer_before_and_after(
    db: AsyncSession, seeded_db_url: str
) -> None:
    claim_id = claims_of(SARAH, "settled")[0]
    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        await set_rate(client, claim_id, OVERRIDE_BP)
        await set_rate(client, claim_id, None)

    rows = await audit_rows(db, claim_id)

    assert [row.action for row in rows] == [ACTION, ACTION]
    assert [row.entity for row in rows] == ["claim", "claim"]
    # Integers and a real null, never their string forms: an audit log that
    # has to be parsed to be compared is a log that will be compared wrongly.
    assert rows[0].before == {COLUMN: None}
    assert rows[0].after == {COLUMN: OVERRIDE_BP}
    assert rows[1].before == {COLUMN: OVERRIDE_BP}
    assert rows[1].after == {COLUMN: None}
    assert all(row.actor_role == "handler" for row in rows)


async def test_the_timeline_says_what_happened_and_never_the_rate(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """Story 2.4's rule for the severity score, applied to money.

    Set, changed and cleared are three different events and the sentence says
    which. None of them quotes a percentage: a log line naming one rate out of
    a sequence of adjustments reads as *the* comp rate on the claim, and the
    audit row is where both values live.
    """
    claim_id = claims_of(SARAH, "settled")[1]
    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        await set_rate(client, claim_id, 7_000)
        await set_rate(client, claim_id, 8_000)
        await set_rate(client, claim_id, None)

    events = await benefit_events(db, claim_id)

    assert [event.description for event in events] == [
        "Comp rate override applied",
        "Comp rate override changed",
        "Comp rate override removed — statutory default restored",
    ]
    assert not any("%" in event.description for event in events)


async def test_a_no_op_writes_nothing_at_all(db: AsyncSession, seeded_db_url: str) -> None:
    """Story 2.3's rule: the log is a record of changes.

    Re-submitting the rate already stored, and resetting a claim that was
    never overridden, are both non-events — no version bump, no audit row, no
    timeline entry. The caller still gets the current entity and cannot tell.
    """
    claim_id = claims_of(SARAH, "settled")[2]
    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        started_at = (await current(client, claim_id))["version"]

        # A reset on a claim that has never been overridden: nothing to undo.
        reset = await set_rate(client, claim_id, None)
        assert reset.status_code == 200
        assert reset.json()["version"] == started_at

        # One real write…
        written = await set_rate(client, claim_id, OVERRIDE_BP)
        assert written.json()["version"] == started_at + 1

        # …and the same rate again, which is not a change.
        again = await set_rate(client, claim_id, OVERRIDE_BP)
        assert again.status_code == 200
        assert again.json()["version"] == started_at + 1

    assert len(await audit_rows(db, claim_id)) == 1
    assert len(await benefit_events(db, claim_id)) == 1


# --- the refusals, in the order the module docstring states -------------


@pytest.mark.parametrize("persona", [JENNIFER, ANALYST])
async def test_only_a_handler_may_override_and_the_refusal_says_nothing_else(
    seeded_db_url: str, persona: tuple[str, str]
) -> None:
    """Role first, before the claim is looked up — a supervisor learns nothing.

    Asserted three ways: a claim in their own book, one in somebody else's,
    and one that does not exist. Identical status, `type` and `detail`, which
    is what closes the enumeration oracle a 404-versus-403 split would open.
    """
    theirs = a_claim_of(KAYA, "investigation")
    async with make_client(seeded_db_url) as client:
        await login_as(client, *persona)
        answers = [
            await client.patch(
                f"/claims/{claim_id}/comp-rate",
                json={"expectedVersion": 1, "compRateBp": OVERRIDE_BP},
            )
            for claim_id in (theirs, a_claim_outside(KAYA, SARAH), "WC-99999")
        ]

    assert {resp.status_code for resp in answers} == {403}
    bodies = [resp.json() for resp in answers]
    assert {body["type"] for body in bodies} == {"/problems/edit-not-permitted"}
    assert len({body["detail"] for body in bodies}) == 1


async def test_a_claim_outside_the_handlers_book_is_a_404(seeded_db_url: str) -> None:
    """The same 404 the GET answers, word for word — a caller comparing the
    two must not learn that the claim exists."""
    outside = a_claim_outside(SARAH, KAYA)
    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        resp = await client.patch(
            f"/claims/{outside}/comp-rate",
            json={"expectedVersion": 1, "compRateBp": OVERRIDE_BP},
        )
        read = await client.get(f"/claims/{outside}")

    assert resp.status_code == 404
    assert resp.json() == read.json()


@pytest.mark.parametrize("comp_rate_bp", [-1, COMP_RATE_MAX_BP + 1, 100_000])
async def test_a_rate_outside_the_domain_is_refused_by_the_contract(
    seeded_db_url: str, comp_rate_bp: int
) -> None:
    """The declared bound, so the generated client refuses before a round trip.

    A schema refusal (422 from FastAPI's validator) rather than the command's,
    which is the layer `SeverityPatch` established: the domain is in the
    OpenAPI document and reaches the browser.
    """
    claim_id = a_claim_of(KAYA, "investigation")
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        version = (await current(client, claim_id))["version"]
        resp = await client.patch(
            f"/claims/{claim_id}/comp-rate",
            json={"expectedVersion": version, "compRateBp": comp_rate_bp},
        )

    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize(
    "body",
    [
        {"expectedVersion": 1},
        {"expectedVersion": 1, "compRateBp": 7000, "severityScore": 4},
        {"compRateBp": 7000},
    ],
    ids=["rate omitted", "unknown key", "version omitted"],
)
async def test_the_body_is_exactly_two_required_fields(
    seeded_db_url: str, body: dict[str, Any]
) -> None:
    """`compRateBp` is **required and nullable**, which is the opposite of
    `ClaimFieldPatch`'s optional members — null is the reset here, so omitting
    the field is not a way to ask for anything. `extra="forbid"` is the outer
    wall around the rest."""
    claim_id = a_claim_of(KAYA, "investigation")
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        resp = await client.patch(f"/claims/{claim_id}/comp-rate", json=body)

    assert resp.status_code == 422


async def test_a_stale_version_conflicts_and_carries_the_fresh_entity(
    seeded_db_url: str,
) -> None:
    claim_id = claims_of(KAYA, "treatment")[2]
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        stale = (await current(client, claim_id))["version"]

        winner = await client.patch(
            f"/claims/{claim_id}/comp-rate",
            json={"expectedVersion": stale, "compRateBp": 7_500},
        )
        loser = await client.patch(
            f"/claims/{claim_id}/comp-rate",
            json={"expectedVersion": stale, "compRateBp": 8_500},
        )

    assert winner.status_code == 200
    assert loser.status_code == 409
    assert loser.headers["content-type"].startswith("application/problem+json")
    body = loser.json()
    assert body["type"] == "/problems/stale-write"
    # Current state travels with the refusal, so the SPA renders it without a
    # second round trip — including the benefit the *winner* produced.
    assert body["claim"]["version"] == stale + 1
    assert body["claim"]["benefit"]["compRateBp"] == 7_500
    assert body["claim"] == winner.json()


# --- the command, called directly (AD-13's path) ------------------------


@pytest.mark.parametrize("value", [-1, COMP_RATE_MAX_BP + 1, "6667", 66.67, True])
async def test_the_command_refuses_a_bad_rate_without_the_pydantic_model(
    db: AsyncSession, seeded_db_url: str, value: object
) -> None:
    """An agent tool calls the command directly, so the command has to be safe
    on its own — `normalise`'s and `normalise_severity`'s argument.

    `True` is in the list because it is an `int` in Python: a caller sending
    `true` would otherwise record a comp rate of one basis point, which is
    0.01% of wage on the column that decides a worker's weekly payment.
    """
    claim_id = claims_of(SARAH, "settled")[3]
    with pytest.raises(InvalidPatch):
        await update_comp_rate_override(
            db,
            await context_for(db, *SARAH),
            claim_id,
            expected_version=1,
            comp_rate_bp=value,
        )
    await db.rollback()


@pytest.mark.parametrize("value", [COMP_RATE_MIN_BP, COMP_RATE_MAX_BP])
async def test_the_command_accepts_both_ends_of_the_domain(
    db: AsyncSession, seeded_db_url: str, value: int
) -> None:
    """Inclusive at both ends — 0% and 150% are the prototype's own input
    attributes, and a bound that was exclusive would refuse a rate the card
    offers."""
    claim_id = claims_of(SARAH, "settled")[4]
    version = (
        await db.execute(sa.select(Claim.version).where(Claim.claim_id == claim_id))
    ).scalar_one()

    detail = await update_comp_rate_override(
        db,
        await context_for(db, *SARAH),
        claim_id,
        expected_version=version,
        comp_rate_bp=value,
    )

    assert detail.benefit.comp_rate_bp == value
    assert detail.benefit.is_overridden is True


async def test_the_role_refusal_precedes_the_claim_lookup(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """Asserted at the command rather than through HTTP, so the *order* is
    what is under test: a claim id that does not exist still answers with the
    role refusal, which it could not do if scope were resolved first."""
    with pytest.raises(EditNotPermitted):
        await update_comp_rate_override(
            db,
            await context_for(db, *JENNIFER),
            "WC-99999",
            expected_version=1,
            comp_rate_bp=OVERRIDE_BP,
        )
    await db.rollback()


async def test_the_update_statement_is_scoped_like_every_read(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """A write is not exempt from AD-7 because its caller already checked.

    Sarah is handed a real claim id from Kaya's book with the *correct*
    version; the statement must change nothing. The predicate is on the
    UPDATE's own WHERE clause, not on the caller's conscience.
    """
    theirs = a_claim_outside(SARAH, KAYA)
    version = (
        await db.execute(sa.select(Claim.version).where(Claim.claim_id == theirs))
    ).scalar_one()

    changed = await claim_repo.update_claim_fields_cas(
        db, await context_for(db, *SARAH), theirs, version, {COLUMN: 9_000}
    )

    assert changed == 0
    assert await stored_override(db, theirs) is None
    await db.rollback()


async def test_a_race_between_the_read_and_the_write_conflicts(
    db: AsyncSession, seeded_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window the version pre-check cannot see, closed end to end.

    Another connection bumps the version *after* the command has read the
    claim and immediately before its UPDATE. The command must answer
    `StaleClaim` carrying the version that won, and must not have written —
    including no timeline row, which is what the second monkeypatch asserts.
    The interleaving is forced rather than raced on a sleep, so this fails
    deterministically if the guard in the statement is removed.
    """
    claim_id = claims_of(SARAH, "settled")[5]
    version = (
        await db.execute(sa.select(Claim.version).where(Claim.claim_id == claim_id))
    ).scalar_one()
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
            await update_comp_rate_override(
                db,
                await context_for(db, *SARAH),
                claim_id,
                expected_version=version,
                comp_rate_bp=OVERRIDE_BP,
            )
    finally:
        await engine.dispose()

    assert caught.value.fresh.version == version + 1
    assert await stored_override(db, claim_id) is None
