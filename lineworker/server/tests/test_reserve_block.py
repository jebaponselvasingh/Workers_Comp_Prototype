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
    "disbursedMedicalCents",
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
    "disbursedMedicalCents",
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


# --- the Story 3.3 seam, now closed ---------------------------------------


async def test_the_medical_term_is_a_real_sum_now_that_the_bills_are_seeded(
    seeded_db_url: str,
) -> None:
    """The seam Story 3.2 opened, asserted closed (Story 3.3).

    This test was `test_the_medical_term_is_unknown_until_story_3_3_seeds_the_bills`
    and it asserted `null` — a deliberate tripwire, so that seeding the `bill`
    table would fail a test rather than silently change every verdict in the
    portfolio. It did exactly that, and this is the other side of it.

    **`0` is now a legitimate answer and `null` is not.** Every claim has bills
    on file, so the medical term is always a sum; a claim whose bills are all
    paid reports `0`, which is the statement "no unpaid medical exposure" that
    3.2 refused to let a missing table make on a claim's behalf.
    """
    for stage in STAGES:
        check = (await detail_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, stage)))[
            "reserveCheck"
        ]
        assert check["remainingMedicalCents"] is not None
        assert check["remainingMedicalCents"] >= 0


async def test_every_open_claim_now_gets_a_complete_verdict(
    seeded_db_url: str,
) -> None:
    """No claim is `indeterminate` once both exposure terms are on file.

    The replacement for
    `test_no_open_claim_is_judged_adequate_or_heavy_while_bills_are_unseen`,
    and the point of the story from a handler's side: 3.2 shipped with 15 of
    Kaya's 19 open claims reading "Awaiting Bill Data" because one of the two
    exposure terms could not be seen, which was honest and nearly useless.
    With `bill` seeded, every open claim is banded.

    **`indeterminate` is asserted absent rather than deleted from the enum.**
    The withholding machinery is still the right answer for a term that
    genuinely cannot be read, and `test_reserve_check.py` still exercises it at
    the classifier. What this pins is that the seeded portfolio no longer
    *reaches* it — so a regression that broke the bill query would surface as
    a book full of withheld verdicts here, rather than as a quietly emptier
    Bills tab.

    The distribution is also checked for spread: a rule that answered one thing
    for every claim would pass an "is it banded" assertion and be visibly
    useless in a demo.
    """
    verdicts: dict[str, int] = {}
    for stage in ("intake", "investigation", "treatment"):
        for claim_id in claim_ids_in_stage(KAYA, stage):
            check = (await detail_for(seeded_db_url, KAYA, claim_id))["reserveCheck"]
            assert check["verdict"] in {"light", "adequate", "heavy"}, claim_id
            # Both terms are known, so the two figures 3.2 nulls out alongside
            # a withheld verdict must both be present.
            assert check["projectedRemainingCents"] is not None, claim_id
            assert check["ratioBp"] is not None, claim_id
            verdicts[check["verdict"]] = verdicts.get(check["verdict"], 0) + 1

    assert "indeterminate" not in verdicts
    assert len(verdicts) > 1, f"every open claim banded the same way: {verdicts}"


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
        # Story 7.4's two, and both of them *publish* the two edges
        # rather than comparing anything against them. That distinction is what
        # this guard is about — the failure it prevents is a **second verdict**,
        # not a second mention — and it is asserted rather than asserted-by-
        # allowlist: `test_no_module_outside_the_classifier_compares_a_reserve_band`
        # below applies to these two as well, and fires on the arithmetic a
        # re-derivation would have to contain.
        #
        # Why they name the edges at all is `PortfolioCharts`' rule, which every
        # rules-derived figure on this console follows: the card's footnote quotes
        # the ratios its buckets were produced at ("Light above 1.15× of
        # reserve"), so a client holding either number would be a second copy of a
        # rule it cannot see change, and superseding `reserve_bands` has to move
        # the segments *and* the caption together. `FraudPanel` publishes its four
        # thresholds for the identical reason.
        #
        # The fold, which counts verdicts and computes none — it reads the two
        # fields off the `ReserveBands` block the verdicts were computed with, so
        # the published numbers are provably the ones the counts were produced at.
        "services/worklist/decomposition.py",
        # The route's response model and its field-by-field build.
        "api/routers/dashboard.py",
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


#: A band *used in a comparison or in arithmetic* — the shape a second verdict
#: has to take, whoever writes it.
#:
#: The narrower half of this section's guard, added by Story 7.4 when the
#: allowlist above grew from three entries to five. `BAND_READ` asks "who names a
#: band", which is the blunt question and the right one for a file that has no
#: business seeing the document at all; this asks "who *bands* with one", and it
#: is the question that stays meaningful now that three modules legitimately
#: publish the edges for a caption.
#:
#: Every operator a comparison could be spelled with, on either side of the name,
#: because `classify_reserve` cross-multiplies rather than divides — the second
#: implementation would be `known * 10_000 > bands.light_ratio_bp * reserve`, and
#: a guard that only looked for `/` would miss the one form the codebase's own
#: rule takes. A publication is `light_ratio_bp=bands.light_ratio_bp`, which
#: contains no operator at all and is what the three new entries above do.
BAND_ARITHMETIC = re.compile(
    r"(?:[<>*/+%-]\s*\w*\.?(?:light_ratio_bp|heavy_ratio_bp|lightRatioBp|heavyRatioBp))"
    r"|(?:(?:light_ratio_bp|heavy_ratio_bp|lightRatioBp|heavyRatioBp)\s*[<>*/+%-])"
)

#: The two modules entitled to compare a band, and to what.
#:
#: `reserve.py` compares a band to a claim's *exposure*, which is the verdict.
#: `parameters.py` compares the two bands **to each other** in `__post_init__`,
#: which is the document refusing an inverted pair rather than judging a claim —
#: the same carve-out `BAND_HOME` already makes for it one guard up.
BAND_ARITHMETIC_HOME = frozenset({"services/financials/reserve.py", "rules/parameters.py"})


#: A band edge *renamed into a local* — the one evasion of `BAND_ARITHMETIC`
#: worth closing, because it is how a second verdict would actually be written.
#:
#: `light = bands.light_ratio_bp` puts a plain identifier next to every operator
#: that follows, so the guard above sees a clean file. Publication is spared by
#: the shape it already takes everywhere on this console: a keyword argument or a
#: field assignment whose **left-hand name is the edge's own** —
#: `light_ratio_bp=bands.light_ratio_bp`. A binding that renames the edge is
#: keeping it for something, and the only something is a comparison.
BAND_RENAME = re.compile(
    r"(?P<target>\w+)\s*=\s*[\w.]*\.(?P<edge>light_ratio_bp|heavy_ratio_bp|lightRatioBp|heavyRatioBp)"
)


def _renames_a_band(source: str) -> bool:
    """A band edge bound to a local under a different name — see `BAND_RENAME`."""
    return any(
        match.group("target") != match.group("edge") for match in BAND_RENAME.finditer(source)
    )


def test_no_module_outside_the_classifier_renames_a_reserve_band_into_a_local() -> None:
    """The evasion `BAND_ARITHMETIC` cannot see, closed where it is cheap to close.

    Mutation-proven rather than assumed: a complete second `classify_reserve`
    written into `services/worklist/decomposition.py` as two local bindings and a
    cross-multiplication passes the guard above and fails this one. The two homes
    are the same two, for the same reasons.
    """
    offenders = [
        name
        for name, source in _python_sources()
        if name not in BAND_ARITHMETIC_HOME and _renames_a_band(source)
    ]

    assert offenders == []


def test_the_band_rename_guard_tells_a_binding_from_a_publication() -> None:
    """The two shapes, written out — the smell test `BAND_ARITHMETIC` already has."""
    for binding in (
        "light = bands.light_ratio_bp",
        "    edge = check.lightRatioBp",
    ):
        assert _renames_a_band(binding), binding

    for publication in (
        "light_ratio_bp=bands.light_ratio_bp,",
        "lightRatioBp=adequacy.lightRatioBp,",
    ):
        assert not _renames_a_band(publication), publication


def test_no_module_outside_the_classifier_compares_a_reserve_band() -> None:
    """The sharper half: naming an edge is publication, comparing one is a verdict.

    `BAND_HOME` grew by two in Story 7.4 — the financial decomposition's fold and
    the route that publishes it both name the two ratios so the adequacy card's
    footnote can quote them, which is the rule every rules-derived figure on this
    console follows. An allowlist that only ever grows is an allowlist that
    eventually means nothing, so this is what the two new entries are held to
    instead: they may *carry* an edge and they may not *use* one.

    Applies to every module including the five exempt above, minus the two that
    genuinely compare — which is what makes it an extension of the guard rather
    than a hole beside it.

    **What it cannot catch, stated because a guard nobody has measured is worse
    than no guard.** It is a regular expression over text: it sees an operator
    beside an edge's *name*. A second verdict that binds the edge to a fresh
    local first — `light = bands.light_ratio_bp` and then `scaled > light *
    reserve_cents` — puts an unremarkable identifier next to every operator, and
    `BAND_RENAME` below exists because that is not a hypothetical evasion but the
    obvious way anybody would actually write it. Past those two, an edge handed
    to a helper as a bare argument still escapes, and nothing here reads
    TypeScript (`_python_sources` is Python-only; the client's own scan is
    `web/src/features/queue/noDerivation.test.ts`). So this guard is what makes a
    second verdict *awkward to write*, and AC 2's actual enforcement — that the
    portfolio buckets are Epic 3's own answers — is
    `test_the_bucket_a_claim_lands_in_is_its_own_case_files_verdict`, which
    compares every claim against the claim-level path and against the restated
    rule. This one narrows the ways a re-derivation can arrive unnoticed; that
    one notices it whatever way it arrives.

    It fires on **prose** as well as on code, which is a real cost and is the
    right trade here rather than a nuisance: `services/worklist/__init__.py`'s
    paragraph about this very temptation spelled the comparison out and had to be
    reworded, exactly as `test_derivations.py` requires a module to "leave the
    number out rather than weaken the allowlist, and say why". A guard that fired
    on the sentence explaining it would be a guard somebody deletes; a guard whose
    price is that the sentence has to describe the arithmetic rather than write it
    is a guard that stays.
    """
    offenders = [
        name
        for name, source in _python_sources()
        if name not in BAND_ARITHMETIC_HOME and BAND_ARITHMETIC.search(source)
    ]

    assert offenders == []


def test_the_band_comparison_guard_would_notice_a_second_verdict() -> None:
    """A guard that only ever reads clean files cannot tell clean from unchecked.

    The three shapes a re-derivation takes — the cross-multiplication the
    classifier itself uses, the division somebody would reach for instead, and a
    ratio scaled into basis points — each written out, and each has to match.
    Beside them, the two shapes that are *publication* and must not.
    """
    for banding in (
        "light = known * 10_000 > bands.light_ratio_bp * reserve_cents",
        "verdict = projected / reserve > check.lightRatioBp / 10_000",
        "over = ratio_bp - data.heavyRatioBp",
    ):
        assert BAND_ARITHMETIC.search(banding), banding

    for publication in (
        "light_ratio_bp=bands.light_ratio_bp,",
        "assert payload['lightRatioBp'] == bands.light_ratio_bp",
    ):
        assert not BAND_ARITHMETIC.search(publication), publication


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


async def test_the_two_medical_figures_are_the_halves_of_one_bill_list(
    seeded_db_url: str,
) -> None:
    """`disbursedMedicalCents` and `remainingMedicalCents` partition the bills.

    Added by the second review of Story 3.3. The card's "Medical paid" row read
    `claim.paid_medical` — 0 on all 38 open seeded claims — directly above a
    live indemnity figure and a link to a tab showing the same claim's paid
    bills as a real number. Publishing the paid half beside the unpaid one is
    the fix, and this is what keeps them two halves of *one* read rather than
    two sums of one table taken at different moments.
    """
    for stage in STAGES:
        claim_id = a_claim_in_stage(KAYA, stage)
        check = (await detail_for(seeded_db_url, KAYA, claim_id))["reserveCheck"]
        financials = None
        async with make_client(seeded_db_url) as client:
            await login_as(client, *KAYA)
            financials = (await client.get(f"/claims/{claim_id}/financials")).json()

        bills = financials["bills"]
        assert check["disbursedMedicalCents"] == bills["paidCents"], claim_id
        # The two halves add to the whole list, which is what makes them a
        # partition rather than two independently computed figures.
        assert (
            check["disbursedMedicalCents"] + check["remainingMedicalCents"] == bills["totalCents"]
        ), claim_id


async def test_an_open_claim_has_a_real_medical_paid_figure(seeded_db_url: str) -> None:
    """The defect in one assertion: not zero, while the column is.

    `overview.paidMedicalCents` is `claim.paid_medical` and is 0 on every open
    seeded claim; the figure the card now renders comes from the bills and is
    not. A regression that re-pointed the row at the column would leave the
    payload passing every other test in this file and the card reading $0.00.

    Treatment only, because that is the one open variant carrying
    `paidMedicalCents` — it is the stage whose card renders the row, and the
    stage the defect was found on. The other two open stages are covered by the
    partition test above, which needs no overview field.
    """
    payload = await detail_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, "treatment"))

    assert payload["overview"]["paidMedicalCents"] == 0, "the seed's premise has moved"
    assert payload["reserveCheck"]["disbursedMedicalCents"] > 0
