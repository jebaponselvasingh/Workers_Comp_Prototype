"""Story 3.2 AC 1 and 3 — the verdict on the case file, over a real database.

`test_reserve_check.py` proves the banding and `test_payment_projection.py` the
exposure it bands. This is about the *contract*: that every claim carries a
reserve-check block whatever stage it is in, that it sits beside `benefit`
rather than inside a stage variant, that every figure crosses as integer cents,
and that the judgement is the server's alone.

Driven through the app, because what the story promises is a payload — and
because AC 3's "renders identically in both surfaces from the same server
value" is a claim about one field under one query key, which is only checkable
where the field is.
"""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.models.enums import Stage
from services.financials import ReserveVerdict
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

KAYA = ("Kaya Johnson", "handler")
#: The full-portfolio supervisor — `test_benefit_block.py` records why it is
#: not Jennifer Park (her three employers do not include Kaya's).
DAVID = ("David Bline", "supervisor")

SERVER_ROOT = Path(__file__).resolve().parents[1]

#: Every field the block publishes. Written out so that a field added without a
#: story behind it fails here, and so that a field *removed* fails in the same
#: place rather than as an `undefined` in a component.
RESERVE_CHECK_FIELDS = {
    "verdict",
    "ratioBp",
    "projectedRemainingCents",
    "remainingIndemnityCents",
    "remainingMedicalCents",
    "scheduledIndemnityCents",
    "disbursedIndemnityCents",
    "reserveCents",
    "rationale",
    "bandsVersion",
}

CENTS_FIELDS = {
    "projectedRemainingCents",
    "remainingIndemnityCents",
    "remainingMedicalCents",
    "scheduledIndemnityCents",
    "disbursedIndemnityCents",
    "reserveCents",
}

#: The two that are `null` while a claim's bills are not on file — see
#: `ReserveCheckResponse`. `remainingMedicalCents` is the unknown term itself;
#: `projectedRemainingCents` is `null` with it, because a total missing a term
#: is not a total.
NULLABLE_CENTS_FIELDS = {"projectedRemainingCents", "remainingMedicalCents"}

STAGES = ("intake", "investigation", "treatment", "settled")


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


async def detail_for(db_url: str, persona: tuple[str, str], claim_id: str) -> dict[str, Any]:
    async with make_client(db_url) as client:
        await login_as(client, *persona)
        resp = await client.get(f"/claims/{claim_id}")
        assert resp.status_code == 200, resp.text
        payload: dict[str, Any] = resp.json()
        return payload


def claim_ids_in_stage(persona: tuple[str, str], stage: str) -> list[str]:
    claims = sorted(
        str(claim["claim_id"])
        for claim in seed_fixture.claims_for(*persona)
        if claim["stage"] == stage
    )
    assert claims, f"no seeded {stage} claim for {persona}"
    return claims


def a_claim_in_stage(persona: tuple[str, str], stage: str) -> str:
    return claim_ids_in_stage(persona, stage)[0]


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


# --- the block, at every stage -------------------------------------------


@pytest.mark.parametrize("stage", ["intake", "investigation", "treatment", "settled"])
async def test_every_stage_carries_a_complete_reserve_check(seeded_db_url: str, stage: str) -> None:
    """Outside the stage-variant union, and at every stage.

    The *chip* renders on the treatment variant — the one the prototype puts it
    on — but the judgement is a fact about the claim throughout, and Story 3.3's
    Bills tab reads this same field at whatever stage the claim is in.
    """
    payload = await detail_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, stage))

    assert set(payload["reserveCheck"]) == RESERVE_CHECK_FIELDS
    check = payload["reserveCheck"]
    assert check["verdict"] in {member.value for member in ReserveVerdict}
    assert check["rationale"]
    assert check["bandsVersion"] == 1


async def test_the_block_is_not_inside_the_stage_variant(seeded_db_url: str) -> None:
    """AC 3, structurally.

    One field on the case file, not a copy on the treatment overview — which is
    what makes "the Bills summary and the Overview card show the same verdict"
    a property of the payload rather than a convention two components are
    trusted to keep. A copy on the variant would let Story 3.3 read the other
    one, and nothing anywhere would say they had drifted.
    """
    payload = await detail_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, "treatment"))

    assert "reserveCheck" in payload
    assert "reserveCheck" not in payload["overview"]
    assert "verdict" not in payload["overview"]


async def test_a_settled_claim_is_closed_final_over_the_wire(seeded_db_url: str) -> None:
    payload = await detail_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, "settled"))

    assert payload["reserveCheck"]["verdict"] == ReserveVerdict.closed_final.value
    assert payload["reserveCheck"]["ratioBp"] is None


@pytest.mark.parametrize("stage", ["intake", "investigation", "treatment"])
async def test_an_open_claim_is_never_closed_final(seeded_db_url: str, stage: str) -> None:
    """`closed_final` is the settled claim's answer and nobody else's.

    What an open claim *is* answered with depends on whether its bills are on
    file — today they are not, so it is `light` or `indeterminate` (see below).
    """
    check = (await detail_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, stage)))["reserveCheck"]

    assert check["verdict"] != ReserveVerdict.closed_final.value


async def test_a_supervisor_reading_a_case_file_gets_the_same_verdict(
    seeded_db_url: str,
) -> None:
    """Role gates capability, scope gates visibility.

    The verdict a reserve is reviewed against is not handler-only — a
    supervisor asking whether a book is under-reserved is the person it is most
    for. `David Bline` sees the whole portfolio, so the same claim reaches both.
    """
    claim_id = a_claim_in_stage(KAYA, "treatment")

    as_handler = await detail_for(seeded_db_url, KAYA, claim_id)
    as_supervisor = await detail_for(seeded_db_url, DAVID, claim_id)

    assert as_supervisor["reserveCheck"] == as_handler["reserveCheck"]


# --- the money convention -------------------------------------------------


async def test_every_money_field_crosses_as_an_integer(seeded_db_url: str) -> None:
    for stage in ("intake", "investigation", "treatment", "settled"):
        check = (await detail_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, stage)))[
            "reserveCheck"
        ]
        for field in CENTS_FIELDS:
            # Nullable where an unknown term makes the figure meaningless — a
            # `null` is a fact about the claim, an integer is a cents amount,
            # and a float would be neither.
            if check[field] is None:
                assert field in NULLABLE_CENTS_FIELDS, f"{stage}.{field} is unexpectedly null"
                continue
            assert isinstance(check[field], int), f"{stage}.{field} is not an integer"
            assert not isinstance(check[field], bool)


async def test_the_only_formatted_money_on_the_block_is_the_rationale(
    seeded_db_url: str,
) -> None:
    """The money convention, and the one sanctioned exception to it.

    Cents end to end, formatted only in the UI — except for a *sentence*, which
    is prose rather than a figure and is written server-side for AD-1's reason
    (`services/financials/rationale.py` argues it at length). So the dollar
    signs on this block belong to the rationale and to nothing else.
    """
    check = (await detail_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, "treatment")))[
        "reserveCheck"
    ]

    formatted = {
        key: value for key, value in check.items() if isinstance(value, str) and "$" in value
    }
    assert set(formatted) == {"rationale"}


async def test_the_exposure_total_is_the_sum_of_its_two_terms_or_absent(
    seeded_db_url: str,
) -> None:
    """Sent rather than left to be added in a browser (AD-1) — and correct.

    Or `null`, exactly when the medical term is: a total missing a term is not
    a total, and publishing the lower bound under this name is the mislabel the
    field is nullable to refuse. Both halves of that are asserted here because
    a payload that quietly started reporting the partial sum would read as a
    complete one to every consumer downstream.
    """
    for stage in STAGES:
        check = (await detail_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, stage)))[
            "reserveCheck"
        ]
        if check["remainingMedicalCents"] is None:
            assert check["projectedRemainingCents"] is None, stage
            continue
        assert check["projectedRemainingCents"] == (
            check["remainingIndemnityCents"] + check["remainingMedicalCents"]
        )


async def test_remaining_indemnity_is_the_difference_of_the_two_halves(
    seeded_db_url: str,
) -> None:
    """The identity the card's indemnity row depends on (code review).

    `remaining = max(0, scheduled - disbursed)` is the projection's guarantee,
    and the block publishes all three so the card can state the first two
    beside a verdict computed from the third. If they ever stopped agreeing,
    the card would be back to showing one number and judging another.
    """
    for stage in STAGES:
        check = (await detail_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, stage)))[
            "reserveCheck"
        ]
        assert check["remainingIndemnityCents"] == max(
            0, check["scheduledIndemnityCents"] - check["disbursedIndemnityCents"]
        ), stage
        assert check["disbursedIndemnityCents"] <= check["scheduledIndemnityCents"], stage


async def test_the_column_and_the_projection_genuinely_disagree_about_indemnity_paid(
    seeded_db_url: str,
) -> None:
    """Why the card must render one of them rather than both (code review).

    `claim.paid_indemnity` is 0 on every open seeded claim; the projection
    counts elapsed weeks as disbursed. So the payload carries two different
    answers to "how much indemnity has been paid", and the treatment card shows
    an indemnity-paid figure directly above a verdict computed from the other
    one — which is how it came to read "Indemnity paid $0.00" above "no
    exposure remains, reallocate the surplus".

    This test records the divergence rather than forbidding it: the projection
    is the live figure and the column is a stale snapshot (the prototype's
    `billsHTML` says so in as many words), so the resolution is that the *card*
    picks one, which `ReserveCheckCard.test.tsx` pins. If a later story
    reconciles the two — a real disbursement ledger in 3.3, say — this fails,
    and the card's source becomes a decision to re-make rather than an
    assumption that quietly stopped being true.
    """
    diverged = 0
    for claim_id in claim_ids_in_stage(KAYA, "treatment"):
        payload = await detail_for(seeded_db_url, KAYA, claim_id)
        if (
            payload["overview"]["paidIndemnityCents"]
            != (payload["reserveCheck"]["disbursedIndemnityCents"])
        ):
            diverged += 1

    assert diverged > 0, (
        "the column and the projection now agree on every treatment claim — "
        "re-read why the card renders the projection's figures"
    )


async def test_the_reserve_on_the_block_is_the_claims_own_reserve(
    seeded_db_url: str,
) -> None:
    """The same column the treatment card's "Reserve balance" row reads.

    Two figures on one card that disagreed about one claim's reserve would be
    the clearest possible sign that the verdict was computed against something
    else.
    """
    payload = await detail_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, "treatment"))

    assert payload["reserveCheck"]["reserveCents"] == payload["overview"]["reserveCents"]


# --- the Story 3.3 seam ---------------------------------------------------


async def test_the_medical_term_is_unknown_until_story_3_3_seeds_the_bills(
    seeded_db_url: str,
) -> None:
    """The seam, asserted rather than assumed.

    The `bill` table is Story 3.3's migration, so there is nothing to sum and
    no honest number to report — `null`, not `0`, because "nobody can see this
    claim's bills" is not "this claim has no unpaid bills". This test turns the
    seam from an unstated assumption into a recorded state: when 3.3 lands its
    seed this fails, and the failure is the reminder rather than a surprise.
    """
    for stage in STAGES:
        check = (await detail_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, stage)))[
            "reserveCheck"
        ]
        assert check["remainingMedicalCents"] is None


async def test_no_open_claim_is_judged_adequate_or_heavy_while_bills_are_unseen(
    seeded_db_url: str,
) -> None:
    """Honest degradation, over the whole book (Story 3.3's seam).

    The unknown medical term is non-negative, so an exposure computed without
    it is a lower bound — which leaves `light` sound and makes `adequate` and
    `heavy` claims about an upper bound that nobody can stand behind. Asserted
    across every open claim rather than one, because the failure it replaced
    was portfolio-wide: 26 of 38 open claims carried "reallocate surplus"
    advice derived from half their inputs.
    """
    withheld = 0
    for stage in ("intake", "investigation", "treatment"):
        for claim_id in claim_ids_in_stage(KAYA, stage):
            check = (await detail_for(seeded_db_url, KAYA, claim_id))["reserveCheck"]
            assert check["verdict"] in {"light", "indeterminate"}, claim_id
            assert "reallocating surplus" not in check["rationale"], claim_id
            withheld += check["verdict"] == "indeterminate"

    assert withheld > 0, "no claim exercised the withheld path — is the seam still open?"


async def test_the_under_reserved_warnings_survive_the_missing_term(
    seeded_db_url: str,
) -> None:
    """The other half, and the reason the withholding is not blanket.

    Suppressing every verdict would have cost the console exactly the signal
    the story exists for. A claim whose scheduled indemnity alone exceeds its
    reserve is under-reserved whatever its bills say, and the seeded portfolio
    has several — if this ever finds none, the degradation has become a
    blackout and somebody should know.
    """
    light = [
        claim_id
        for stage in ("intake", "investigation", "treatment")
        for claim_id in claim_ids_in_stage(KAYA, stage)
        if (await detail_for(seeded_db_url, KAYA, claim_id))["reserveCheck"]["verdict"] == "light"
    ]

    assert light, "every under-reserved warning has been withheld along with the rest"


# --- AD-2: nobody else bands a reserve ------------------------------------

#: The two thresholds, by the names they travel under. A module that named
#: either is either the classifier, the block that validates the document, or a
#: second implementation.
BAND_READ = re.compile(r"light_ratio_bp|heavy_ratio_bp|lightRatioBp|heavyRatioBp")

BAND_HOME = frozenset(
    {
        # The classifier.
        "services/financials/reserve.py",
        # The typed edge that validates the document into a block. Reading a
        # parameter's *name* to check it is not applying the rule — the same
        # carve-out `COORDINATION_HOME` makes for the enum that names its
        # vocabulary.
        "rules/parameters.py",
        # The migration that seeds the document, and the document itself is not
        # Python. Naming a key in an INSERT is not banding a reserve.
        "data/versions/20260814_0025_reserve_bands.py",
    }
)


def _python_sources() -> list[tuple[str, str]]:
    """Every server module outside `tests/` and the virtualenv.

    `test_case_file_derivations.py`'s helper, restated rather than imported for
    that file's own reason: an oracle that shared its subject's machinery would
    agree with it. Paths are relativised *before* the exclusion, because
    testing `parts[0] != "tests"` on an absolute path compares against `"/"`
    and excludes nothing.
    """
    sources = []
    for path in sorted(SERVER_ROOT.rglob("*.py")):
        name = path.relative_to(SERVER_ROOT)
        if name.parts[0] in {"tests", ".venv"} or "__pycache__" in name.parts:
            continue
        sources.append((name.as_posix(), path.read_text(encoding="utf-8")))
    return sources


def test_no_module_outside_the_classifier_reads_a_reserve_band() -> None:
    """AD-2 and AD-10, as a fact about the source rather than a convention.

    The failure this prevents is specific and quiet: Story 3.3's Bills summary,
    or Epic 6's "reserve adequacy review" insight, computing its own ratio from
    the same two figures. Both surfaces would render a confident verdict, they
    would agree on almost every claim, and they would disagree exactly at a
    boundary — which is the one place a handler is being asked to act.

    Blunt on purpose: the fix is either to call `classify_reserve` or to add an
    entry to `BAND_HOME`, which is a choice a reviewer sees rather than infers.
    """
    offenders = [
        name
        for name, source in _python_sources()
        if name not in BAND_HOME and BAND_READ.search(source)
    ]

    assert offenders == []


def test_the_verdict_vocabulary_is_snake_case_tokens_not_labels() -> None:
    """The Enums convention: the UI owns "Reserve Light", the wire owns `light`.

    The prototype ships the display strings *as* the value (`label: "Reserve
    Light"`), which is what makes a re-wording a breaking change to every
    consumer that compared against one.
    """
    assert {member.value for member in ReserveVerdict} == {
        "light",
        "adequate",
        "heavy",
        "closed_final",
        "indeterminate",
    }
    for member in ReserveVerdict:
        assert member.value == member.value.lower()
        assert " " not in member.value


def test_the_settled_stage_is_the_only_short_circuit() -> None:
    """Stated here so that adding a fifth `Stage` has to face the question.

    `classify_reserve` tests `stage is Stage.settled` and bands everything
    else; a member added without a decision about it would silently be judged.
    """
    assert {stage for stage in Stage} == {
        Stage.intake,
        Stage.investigation,
        Stage.treatment,
        Stage.settled,
    }
