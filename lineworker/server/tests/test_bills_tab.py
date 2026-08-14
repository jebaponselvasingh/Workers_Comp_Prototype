"""Story 3.3 AC 1-4 — the claim-financials read model, over a real database.

`test_claim_financials.py` proves the derivations and
`test_schedule_materialization.py` the write path. This is about the
*contract*: that one endpoint carries the whole tab, that its figures are
internally consistent, that AD-7 refuses a claim outside the caller's book, and
— AC 4 — that the reserve verdict it publishes is the identical value the case
file publishes rather than a second opinion computed alongside it.

Driven through the app, because all four of those are claims about a payload.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from api import create_app
from config import Settings
from data.models.enums import LineItemStatus, ScheduleWeekStatus
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

KAYA = ("Kaya Johnson", "handler")
#: The full-portfolio supervisor — `test_benefit_block.py` records why it is
#: not Jennifer Park (her three employers do not include Kaya's).
DAVID = ("David Bline", "supervisor")

STAGES = ("intake", "investigation", "treatment", "settled")

SUMMARY_FIELDS = {
    "paidToDateCents",
    "totalClaimProjectedCents",
    "reserveCents",
    "costSplit",
    "paidIndemnityCents",
    "paidMedicalCents",
    "paidExpenseCents",
    "paidFromColumns",
    "weeklyIndemnityCents",
    "installmentsPaid",
    "weekCount",
    "nextPaymentDue",
    "billsOnFile",
    "scheduledIndemnityCents",
    "disbursedIndemnityCents",
}


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


async def financials_for(db_url: str, persona: tuple[str, str], claim_id: str) -> dict[str, Any]:
    async with make_client(db_url) as client:
        await login_as(client, *persona)
        resp = await client.get(f"/claims/{claim_id}/financials")
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


# --- AC 1: the summary ----------------------------------------------------


@pytest.mark.parametrize("stage", STAGES)
async def test_every_stage_serves_a_complete_payload(seeded_db_url: str, stage: str) -> None:
    payload = await financials_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, stage))

    assert set(payload) == {"summary", "schedule", "bills", "expenses", "reserveCheck"}
    assert set(payload["summary"]) == SUMMARY_FIELDS
    assert payload["schedule"], "every seeded claim has a materialized schedule"
    assert payload["bills"]["items"], "every seeded claim has bills"


@pytest.mark.parametrize("stage", STAGES)
async def test_the_summary_figures_agree_with_the_rows_they_total(
    seeded_db_url: str, stage: str
) -> None:
    """The identities a totals row has to satisfy to be worth showing.

    This is the assertion that makes one payload the right shape: the summary
    and the lists it summarises are served together, so they can be checked
    against each other rather than trusted.
    """
    payload = await financials_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, stage))
    summary, schedule = payload["summary"], payload["schedule"]

    assert summary["weekCount"] == len(schedule)
    assert summary["scheduledIndemnityCents"] == sum(w["amountCents"] for w in schedule)
    assert summary["disbursedIndemnityCents"] == sum(
        w["amountCents"] for w in schedule if w["status"] == ScheduleWeekStatus.paid
    )
    assert summary["installmentsPaid"] == sum(
        1 for w in schedule if w["status"] == ScheduleWeekStatus.paid
    )
    assert summary["billsOnFile"] == payload["bills"]["count"] == len(payload["bills"]["items"])

    for group in ("bills", "expenses"):
        block = payload[group]
        assert block["totalCents"] == sum(i["amountCents"] for i in block["items"])
        assert block["paidCents"] == sum(
            i["amountCents"] for i in block["items"] if i["status"] == LineItemStatus.paid
        )

    assert (
        summary["totalClaimProjectedCents"] == summary["paidToDateCents"] + summary["reserveCents"]
    )
    assert (
        summary["paidIndemnityCents"] + summary["paidMedicalCents"] + summary["paidExpenseCents"]
        == summary["paidToDateCents"]
    )


async def test_an_open_claim_reports_the_live_figures_not_the_paid_columns(
    seeded_db_url: str,
) -> None:
    """The prototype's own rule, and the reason this story has a fallback.

    Every open seeded claim has `paid_indemnity = paid_medical = paid_expense =
    0` while its schedule shows elapsed weeks and its bills show payments. A
    summary that read the columns would report $0 paid on a claim with real
    disbursements — which is the contradiction Story 3.2's review found on the
    treatment card, one surface over.
    """
    payload = await financials_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, "treatment"))

    assert payload["summary"]["paidFromColumns"] is False
    assert payload["summary"]["paidToDateCents"] > 0


async def test_a_settled_claim_reports_the_paid_columns(seeded_db_url: str) -> None:
    """The other branch: a settled claim's ledger is complete, so it answers."""
    payload = await financials_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, "settled"))

    assert payload["summary"]["paidFromColumns"] is True


async def test_the_cost_split_sums_to_one_hundred_or_is_absent(seeded_db_url: str) -> None:
    for stage in STAGES:
        for claim_id in claim_ids_in_stage(KAYA, stage):
            split = (await financials_for(seeded_db_url, KAYA, claim_id))["summary"]["costSplit"]
            if split is not None:
                assert split["indemnityPct"] + split["medicalPct"] + split["expensePct"] == 100, (
                    claim_id
                )


# --- AC 2 and 3: the rows -------------------------------------------------


async def test_the_schedule_is_week_ordered_and_dense(seeded_db_url: str) -> None:
    """Week numbers are 1-based and contiguous, and the periods are inclusive.

    Contiguity matters because the table renders "Wk n" from the number rather
    than from row order: a gap would show a schedule with a missing week and no
    indication that anything was missing.
    """
    payload = await financials_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, "treatment"))
    schedule = payload["schedule"]

    assert [w["weekNo"] for w in schedule] == list(range(1, len(schedule) + 1))
    for week in schedule:
        assert week["periodStart"] < week["periodEnd"]
        assert week["status"] in {member.value for member in ScheduleWeekStatus}


async def test_every_line_item_carries_a_category_from_its_own_vocabulary(
    seeded_db_url: str,
) -> None:
    """Two enums, not one — a surgical facility fee and a mileage claim are
    not members of one list, and a client switching on `category` gets
    exhaustiveness from the type."""
    payload = await financials_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, "treatment"))

    bill_categories = {item["category"] for item in payload["bills"]["items"]}
    expense_categories = {item["category"] for item in payload["expenses"]["items"]}

    assert bill_categories
    assert bill_categories.isdisjoint(expense_categories)


async def test_every_money_field_is_an_integer(seeded_db_url: str) -> None:
    """Integer cents end to end — a float anywhere is a rounding waiting to
    disagree with the figure beside it."""
    payload = await financials_for(seeded_db_url, KAYA, a_claim_in_stage(KAYA, "treatment"))

    money = [value for key, value in payload["summary"].items() if key.endswith("Cents")]
    money += [w["amountCents"] for w in payload["schedule"]]
    money += [i["amountCents"] for i in payload["bills"]["items"]]

    assert money
    assert all(isinstance(value, int) for value in money)


# --- AC 4: the two surfaces cannot disagree -------------------------------


@pytest.mark.parametrize("stage", STAGES)
async def test_the_reserve_verdict_is_identical_on_both_surfaces(
    seeded_db_url: str, stage: str
) -> None:
    """AC 4, and the whole reason the jump-link is worth testing.

    The case file and this endpoint are two requests under two query keys, so
    "identical" cannot rest on a shared cache entry. It rests on the server:
    both come from one assembler over one set of rows
    (`services/financials/summary.py`), and this compares the two payloads
    field for field to say so.
    """
    claim_id = a_claim_in_stage(KAYA, stage)
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        detail = (await client.get(f"/claims/{claim_id}")).json()
        financials = (await client.get(f"/claims/{claim_id}/financials")).json()

    assert financials["reserveCheck"] == detail["reserveCheck"]


async def test_the_indemnity_figures_the_two_cards_render_are_the_same(
    seeded_db_url: str,
) -> None:
    """The specific pair a handler sees on both sides of the jump-link.

    The treatment Overview card renders "Indemnity paid X of Y" from
    `reserveCheck`; the Bills tab's schedule sub-heading renders the same two
    figures from `summary`. Different blocks, so this is worth pinning
    separately from the verdict above.
    """
    claim_id = a_claim_in_stage(KAYA, "treatment")
    payload = await financials_for(seeded_db_url, KAYA, claim_id)

    assert (
        payload["summary"]["disbursedIndemnityCents"]
        == payload["reserveCheck"]["disbursedIndemnityCents"]
    )
    assert (
        payload["summary"]["scheduledIndemnityCents"]
        == payload["reserveCheck"]["scheduledIndemnityCents"]
    )


# --- AD-7 -----------------------------------------------------------------


async def test_a_claim_outside_the_callers_book_is_a_404(seeded_db_url: str) -> None:
    """The case file's refusal, in the case file's wording.

    A route that distinguished "no such claim" from "not yours" is an oracle a
    caller can walk `WC-20000`…`WC-20999` through to enumerate a portfolio they
    cannot read. The financial endpoint has to refuse identically or the
    *difference* between the two refusals is itself the answer.
    """
    outside = next(
        claim_id
        for claim_id in (str(c["claim_id"]) for c in seed_fixture.claims_for(*DAVID))
        if claim_id not in {str(c["claim_id"]) for c in seed_fixture.claims_for(*KAYA)}
    )

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        financials = await client.get(f"/claims/{outside}/financials")
        detail = await client.get(f"/claims/{outside}")

    assert financials.status_code == 404
    assert detail.status_code == 404
    assert financials.json()["detail"] == detail.json()["detail"]
    assert financials.json()["type"] == detail.json()["type"]


async def test_a_supervisor_reads_the_same_payload(seeded_db_url: str) -> None:
    """Scope gates visibility, not shape: a wider book is more claims, not a
    different answer for one of them."""
    claim_id = a_claim_in_stage(KAYA, "treatment")

    assert await financials_for(seeded_db_url, DAVID, claim_id) == await financials_for(
        seeded_db_url, KAYA, claim_id
    )


async def test_the_response_is_never_cached_upstream(seeded_db_url: str) -> None:
    """One persona's book, so never served to another from a shared cache."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        resp = await client.get(f"/claims/{a_claim_in_stage(KAYA, 'treatment')}/financials")

    assert resp.headers["Cache-Control"] == "no-store"
