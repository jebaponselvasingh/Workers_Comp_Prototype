"""Story 2.1 AC 1/3/4 — `GET /claims/queue` over a real seeded database.

Driven through the app, not the service, because half of what this story
promises is a *contract*: four groups whatever the caseload, an envelope
with a real cursor and a real count, snake_case filter values, and no
parameter anywhere that could name somebody else's book.

Every expectation comes from `seed_fixture.expected_queue`, which restates
the grouping, flag, scoring, marker and filter rules from the prototype
rather than importing them from `services/`. The two implementations agree
here or one of them is wrong.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

import httpx
import pytest

from api import create_app
from config import Settings
from services.derivations import utc_today
from services.worklist.priority import QueueFilter
from services.worklist.queue import decode_cursor, encode_cursor
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

QUEUE = "/claims/queue"
STAGES = seed_fixture.STAGE_ORDER

# Kaya has claims in all four stages (45 of them); Sarah's 3M book is eight
# claims with an *empty* intake group, which is the only way to test the
# per-stage empty state against real data rather than a contrived filter.
KAYA = ("Kaya Johnson", "handler")
SARAH = ("Sarah Williams", "handler")


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
    resp = await client.post("/auth/login", json={"personaId": match[0]["id"]})
    assert resp.status_code == 200


async def queue_for(db_url: str, name: str, role: str, **kwargs: Any) -> dict[str, Any]:
    async with make_client(db_url) as client:
        await login_as(client, name, role)
        resp = await client.get(QUEUE, **kwargs)
        assert resp.status_code == 200, resp.text
        payload: dict[str, Any] = resp.json()
        return payload


def ids(group: dict[str, Any]) -> list[str]:
    return [item["claimId"] for item in group["items"]]


# --- grouping and ordering (AC 1, 4) ------------------------------------


async def test_the_four_groups_carry_the_personas_claims_in_priority_order(
    seeded_db_url: str,
) -> None:
    payload = await queue_for(seeded_db_url, *KAYA)
    expected = seed_fixture.expected_queue(*KAYA)

    for stage in STAGES:
        group = payload["groups"][stage]
        assert ids(group) == [card["claimId"] for card in expected[stage]], stage
        assert group["total"] == len(expected[stage]), stage


async def test_every_group_is_present_even_when_a_persona_has_nothing_in_it(
    seeded_db_url: str,
) -> None:
    """Sarah's 3M book has no intake claim. The group still has to be there,
    empty, with a truthful zero — that is what the pane renders "No claims in
    this stage." against (NFR-3)."""
    payload = await queue_for(seeded_db_url, *SARAH)

    assert set(payload["groups"]) == set(STAGES)
    assert payload["groups"]["intake"] == {"items": [], "nextCursor": None, "total": 0}
    assert payload["groups"]["settled"]["total"] > 0


async def test_the_items_are_sorted_by_the_score_they_publish(seeded_db_url: str) -> None:
    """Ordering and `priorityScore` have to agree with each other, or the
    published figure is decoration rather than the reason for the order."""
    payload = await queue_for(seeded_db_url, *KAYA)

    for stage in STAGES:
        scores = [item["priorityScore"] for item in payload["groups"][stage]["items"]]
        assert scores == sorted(scores, reverse=True), stage


# --- the card (AC 5) ----------------------------------------------------


async def test_a_card_carries_every_field_the_queue_renders(seeded_db_url: str) -> None:
    payload = await queue_for(seeded_db_url, *KAYA)
    card = payload["groups"]["treatment"]["items"][0]

    assert set(card) == {
        "claimId",
        "daysOpen",
        "risk",
        "workerName",
        "injuryType",
        "stage",
        "employerShortName",
        "fraudFlag",
        "litigationFlag",
        "paymentDue",
        "siuReview",
        "rtwBlocked",
        "priorityScore",
        "priorityMarker",
    }
    # The worker's name and the employer's short name are joins, not claim
    # columns — the two fields most likely to arrive empty if the query
    # changed shape.
    assert card["workerName"]
    assert card["employerShortName"] in {e["short_name"] for e in seed_fixture.seed()["employers"]}


async def test_the_derived_values_on_the_card_match_the_restated_rules(
    seeded_db_url: str,
) -> None:
    payload = await queue_for(seeded_db_url, *KAYA)
    oracle = seed_fixture.expected_queue(*KAYA)
    expected = {card["claimId"]: card for stage in STAGES for card in oracle[stage]}

    for stage in STAGES:
        for item in payload["groups"][stage]["items"]:
            predicted = expected[item["claimId"]]
            for field in ("daysOpen", "risk", "siuReview", "rtwBlocked", "paymentDue"):
                assert item[field] == predicted[field], f"{item['claimId']} {field}"


async def test_the_marker_lands_on_the_top_scoring_claims_of_each_group(
    seeded_db_url: str,
) -> None:
    """Top `markerCount` above `markerThreshold` — and a fourth equally high
    claim does not get one, which is the half of the rule a naive
    "score > 30" implementation would get wrong."""
    payload = await queue_for(seeded_db_url, *KAYA)
    expected = seed_fixture.expected_queue(*KAYA)

    for stage in STAGES:
        marked = [i["claimId"] for i in payload["groups"][stage]["items"] if i["priorityMarker"]]
        assert marked == [c["claimId"] for c in expected[stage] if c["priorityMarker"]], stage
        assert len(marked) <= seed_fixture.MARKER_COUNT


async def test_no_settled_claim_in_the_seeded_portfolio_carries_the_marker(
    seeded_db_url: str,
) -> None:
    """A property of *this* portfolio, not of the arithmetic.

    The −100 penalty is a bias, not a floor: the categorical weights total
    165, so a settled claim carrying every flag would clear the marker
    threshold, and `test_the_settled_penalty_is_a_bias_and_not_an_absolute_floor`
    constructs exactly that claim. What is true here is narrower and worth
    asserting anyway — none of Kaya's 26 settled claims is loud enough, so
    the queue a handler actually sees never puts a 🔺 on closed work. The
    oracle agrees independently; if a later seed adds a settled claim that
    does clear it, this fails and the two sentences above are what a reader
    needs to decide whether the seed or the weights are wrong.
    """
    payload = await queue_for(seeded_db_url, *KAYA)
    expected = seed_fixture.expected_queue(*KAYA)["settled"]
    settled = payload["groups"]["settled"]["items"]

    assert settled, "the seeded handler should have settled claims"
    assert all(card["priorityMarker"] is False for card in expected), (
        "the seed now holds a settled claim loud enough to clear the marker "
        "threshold despite the penalty — the assertion below is no longer a "
        "property of this portfolio"
    )
    assert all(item["priorityMarker"] is False for item in settled)
    # Same status: every one of these settled claims scores below zero, so
    # the group sinks beneath every live one. Also a fact about the seed
    # rather than about the formula, and the reason the marker never lands.
    assert all(item["priorityScore"] < 0 for item in settled)


# --- the eight filters (AC 3) -------------------------------------------


@pytest.mark.parametrize("queue_filter", sorted(f.value for f in QueueFilter))
async def test_each_filter_returns_exactly_the_claims_the_rule_selects(
    seeded_db_url: str, queue_filter: str
) -> None:
    payload = await queue_for(seeded_db_url, *KAYA, params={"filter": queue_filter})
    expected = seed_fixture.expected_queue(*KAYA, queue_filter=queue_filter)

    for stage in STAGES:
        assert ids(payload["groups"][stage]) == [c["claimId"] for c in expected[stage]], (
            f"{queue_filter}/{stage}"
        )


async def test_a_filter_that_matches_nothing_still_returns_four_empty_groups(
    seeded_db_url: str,
) -> None:
    """The distinction NFR-3 rests on: "nothing matches this filter" is a
    successful response with four empty groups, not an error and not an
    absent group.

    `litigation` rather than `fraud`: Sarah has a settled fraud claim, so
    the fraud filter matches something and this case would have tested
    nothing. The premise is asserted rather than guarded — a seed change
    that gives her a litigated claim must fail here, loudly, instead of
    quietly turning the interesting assertion off.
    """
    expected = seed_fixture.expected_queue(*SARAH, queue_filter="litigation")
    assert not any(expected.values()), (
        "this case needs a filter that matches none of Sarah's claims; "
        f"litigation now matches {sum(len(g) for g in expected.values())}"
    )

    payload = await queue_for(seeded_db_url, *SARAH, params={"filter": "litigation"})

    assert all(payload["groups"][stage]["total"] == 0 for stage in STAGES)
    assert all(payload["groups"][stage]["items"] == [] for stage in STAGES)
    # …and the response still says her book is not empty, which is what
    # lets the pane pick the filter sentence over the scope one.
    assert payload["unfilteredTotal"] == len(seed_fixture.expected_claim_ids(*SARAH))


async def test_an_unknown_filter_is_refused_as_problem_json(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        resp = await client.get(QUEUE, params={"filter": "everything"})

    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["type"] == "/problems/validation-error"


# --- scope (AC: AD-7) ---------------------------------------------------


async def test_a_scoped_handler_sees_only_their_own_employers_claims(
    seeded_db_url: str,
) -> None:
    payload = await queue_for(seeded_db_url, *SARAH)
    seen = {item["claimId"] for stage in STAGES for item in payload["groups"][stage]["items"]}

    assert seen == seed_fixture.expected_claim_ids(*SARAH)
    # The pair is the proof: equal sets would mean scoping stopped working
    # and the assertion above would still pass against the whole portfolio.
    assert seen < seed_fixture.expected_claim_ids("David Bline", "supervisor")


async def test_no_query_parameter_can_widen_or_change_the_scope(seeded_db_url: str) -> None:
    """The smuggling attempt: ask as Sarah, name somebody else's book anyway.

    Unknown parameters are ignored rather than rejected (FastAPI's default,
    and the safer direction — a 422 would tell an attacker which names
    exist). What matters is that the answer is byte-identical.
    """
    honest = await queue_for(seeded_db_url, *SARAH)

    for smuggled in (
        {"employerId": "1"},
        {"employer_id": "1"},
        {"employerIds": "1,2,3"},
        {"scopeAll": "true"},
        {"scope_all": "true"},
        {"userId": "7"},
        {"role": "supervisor"},
        {"handler": "Kaya Johnson"},
    ):
        attempt = await queue_for(seeded_db_url, *SARAH, params=smuggled)
        assert attempt == honest, f"{smuggled} changed the answer"


async def test_the_route_offers_nowhere_to_put_a_scope(seeded_db_url: str) -> None:
    """AC 2 structurally, against the published contract rather than the
    function signature — the contract is what a client and a reviewer read."""
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()
    parameters = {p["name"] for p in schema["paths"][QUEUE]["get"].get("parameters", [])}

    assert parameters == {"filter", "stage", "cursor", "limit"}
    assert "requestBody" not in schema["paths"][QUEUE]["get"]


async def test_the_endpoint_requires_a_session(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.get(QUEUE)
    assert resp.status_code == 401


async def test_the_response_is_not_cacheable(seeded_db_url: str) -> None:
    """One persona's caseload must never be served to another from a cache
    upstream — the same reason `/me` and `/stats/*` say so."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        resp = await client.get(QUEUE)
    assert resp.headers["cache-control"] == "no-store"


# --- paging -------------------------------------------------------------


async def test_a_page_plus_its_successor_is_the_whole_group_with_no_repeats(
    seeded_db_url: str,
) -> None:
    """Cursor continuity, over a group deliberately made to page.

    `limit` is small so the seeded settled group (26 claims for Kaya) needs
    several pages; the default page limit of 50 would never exercise this.
    """
    expected = [card["claimId"] for card in seed_fixture.expected_queue(*KAYA)["settled"]]
    assert len(expected) > 10, "the settled group must be big enough to page"

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)

        collected: list[str] = []
        params: dict[str, Any] = {"limit": 5}
        for _ in range(20):
            group = (await client.get(QUEUE, params=params)).json()["groups"]["settled"]
            assert group["total"] == len(expected)
            collected.extend(ids(group))
            if group["nextCursor"] is None:
                break
            params = {"limit": 5, "stage": "settled", "cursor": group["nextCursor"]}
        else:  # pragma: no cover - a cursor that never exhausts is the defect
            pytest.fail("the settled group never reported a final page")

    assert collected == expected
    assert len(collected) == len(set(collected))


async def test_paging_one_group_leaves_the_others_truthful(seeded_db_url: str) -> None:
    """`stage` says which group the cursor addresses; it does not blank the
    rest. A response whose other three groups read zero would be
    indistinguishable from a caseload that had nothing in them."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        first = (await client.get(QUEUE, params={"limit": 5})).json()
        cursor = first["groups"]["settled"]["nextCursor"]
        assert cursor is not None
        second = (
            await client.get(QUEUE, params={"limit": 5, "stage": "settled", "cursor": cursor})
        ).json()

    assert ids(second["groups"]["settled"]) != ids(first["groups"]["settled"])
    for stage in ("intake", "investigation", "treatment"):
        assert second["groups"][stage] == first["groups"][stage], stage


async def test_the_last_page_reports_no_successor(seeded_db_url: str) -> None:
    payload = await queue_for(seeded_db_url, *KAYA, params={"limit": 200})

    for stage in STAGES:
        assert payload["groups"][stage]["nextCursor"] is None, stage


async def test_a_cursor_from_a_different_filter_is_refused(seeded_db_url: str) -> None:
    """Serving it would silently mix two orderings — the caller would get a
    page that overlaps or skips the one before it with nothing to say why."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        cursor = (await client.get(QUEUE, params={"limit": 5})).json()["groups"]["settled"][
            "nextCursor"
        ]
        resp = await client.get(
            QUEUE, params={"limit": 5, "filter": "litigation", "stage": "settled", "cursor": cursor}
        )

    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["type"] == "/problems/invalid-cursor"


async def test_a_cursor_for_another_group_is_refused(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        cursor = (await client.get(QUEUE, params={"limit": 5})).json()["groups"]["settled"][
            "nextCursor"
        ]
        resp = await client.get(QUEUE, params={"limit": 5, "stage": "treatment", "cursor": cursor})

    assert resp.status_code == 400


@pytest.mark.parametrize("cursor", ["not-base64", "eyJmb28iOiJiYXIifQ", ""])
async def test_an_unreadable_cursor_is_refused_rather_than_reset_to_page_one(
    seeded_db_url: str, cursor: str
) -> None:
    """Answering page one would turn a client bug into a "Show more" that
    re-appends the same claims for ever."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        resp = await client.get(QUEUE, params={"cursor": cursor})

    assert resp.status_code == 400, resp.text


async def test_a_cursor_carries_the_page_size_it_was_cut_at(seeded_db_url: str) -> None:
    """The overlap this prevents is the easy one to ship.

    Page 1 at `limit=5` ends at offset 5. A follow-up that hands the cursor
    back without repeating `limit` would otherwise read `[5:55]` under the
    default page size and re-serve claims the caller already has — a "Show
    more" that appends duplicates.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        first = (await client.get(QUEUE, params={"limit": 5})).json()["groups"]["settled"]
        second = (
            await client.get(QUEUE, params={"stage": "settled", "cursor": first["nextCursor"]})
        ).json()["groups"]["settled"]

    assert len(second["items"]) == 5
    assert not set(ids(first)) & set(ids(second))


async def test_a_cursor_whose_thresholds_version_is_stale_is_refused(
    seeded_db_url: str,
) -> None:
    """The second rule document also decides the ordering.

    `derivation_thresholds` produces `risk`, `siu_review` and `rtw_blocked`
    — three *inputs* to the score. A cursor that recorded only the weights'
    version would be served happily against a re-banded portfolio, from a
    group that had re-sorted underneath it.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        cursor = decode_cursor(
            (await client.get(QUEUE, params={"limit": 5})).json()["groups"]["settled"]["nextCursor"]
        )
        stale = encode_cursor(replace(cursor, thresholds_version=cursor.thresholds_version + 1))
        resp = await client.get(QUEUE, params={"limit": 5, "stage": "settled", "cursor": stale})

    assert resp.status_code == 400
    assert "derivation_thresholds" in resp.json()["detail"]


async def test_a_cursor_past_the_end_of_its_group_is_refused(seeded_db_url: str) -> None:
    """The alternative is a group that reads as populated and finished at
    once: empty `items`, a non-zero `total`, and a null `nextCursor` with
    nothing to ask for next."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        cursor = decode_cursor(
            (await client.get(QUEUE, params={"limit": 5})).json()["groups"]["settled"]["nextCursor"]
        )
        beyond = encode_cursor(replace(cursor, offset=10_000))
        resp = await client.get(QUEUE, params={"limit": 5, "stage": "settled", "cursor": beyond})

    assert resp.status_code == 400
    assert resp.json()["type"] == "/problems/invalid-cursor"


async def test_a_cursor_pins_the_day_the_claims_were_aged_against(seeded_db_url: str) -> None:
    """`days_open` feeds the score, so the day is part of the ordering.

    A "Show more" issued at 23:59:59 UTC and answered at 00:00:01 would
    otherwise re-age every claim and re-rank the group between the two
    pages. The cursor records the date and the service reuses it, so page 2
    is cut from the list page 1 was.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        cursor = (await client.get(QUEUE, params={"limit": 5})).json()["groups"]["settled"][
            "nextCursor"
        ]

    assert decode_cursor(cursor).as_of == utc_today()


async def test_the_rules_version_that_ranked_the_queue_is_reported(seeded_db_url: str) -> None:
    payload = await queue_for(seeded_db_url, *KAYA)
    assert payload["rulesVersion"] == 1


async def test_the_unfiltered_total_counts_the_book_behind_the_filter(
    seeded_db_url: str,
) -> None:
    """What lets the pane tell an empty book from an empty filter (NFR-3).

    Under `all` it is the sum of the four groups; under a filter it stays
    the same while the groups shrink — which is the whole point, and the
    thing a client could not work out for itself without holding the
    unfiltered caseload it is not allowed to have (AD-1).
    """
    unfiltered = await queue_for(seeded_db_url, *KAYA)
    litigated = await queue_for(seeded_db_url, *KAYA, params={"filter": "litigation"})

    expected = len(seed_fixture.expected_claim_ids(*KAYA))
    assert unfiltered["unfilteredTotal"] == expected
    assert sum(unfiltered["groups"][stage]["total"] for stage in STAGES) == expected

    assert litigated["unfilteredTotal"] == expected
    assert sum(litigated["groups"][stage]["total"] for stage in STAGES) < expected
