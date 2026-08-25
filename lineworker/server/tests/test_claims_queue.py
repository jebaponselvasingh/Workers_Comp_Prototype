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

import base64
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from api import create_app
from config import Settings
from data.context import CallerContext
from services.derivations import utc_today
from services.worklist.priority import QueueFilter
from services.worklist.queue import MAX_CURSOR_AGE, claim_queue, decode_cursor, encode_cursor
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

QUEUE = "/claims/queue"
STAGES = seed_fixture.STAGE_ORDER
DOCUMENTS_DIR = Path(__file__).resolve().parents[1] / "rules" / "documents"

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


@contextmanager
def superseding_weights(db_url: str, **overrides: Any) -> Iterator[None]:
    """Insert a real version 2 of `priority_weights`, then take it away again.

    A *document*, not a dataclass. The committed v1 is loaded from disk, its
    expression node is rewritten with `overrides`, and the result is inserted
    as version 2 effective today — the same row shape migration 0009 writes,
    reaching the queue through the same loader, ZEN evaluation and parameter
    validation as v1. That whole chain is what "the weights are data" means,
    and a test that replaced a `PriorityWeights` in Python would have proved
    none of it.

    Removed on the way out because `seeded_db_url` is module-scoped: a v2
    left behind would silently re-rank every test after this one, and the
    failures would surface somewhere else entirely.
    """
    document = json.loads((DOCUMENTS_DIR / "priority_weights.jdm.json").read_text())
    node = next(n for n in document["nodes"] if n["id"] == "weights")
    for expression in node["content"]["expressions"]:
        if expression["key"] in overrides:
            expression["value"] = overrides[expression["key"]]

    engine = sa.create_engine(
        db_url.replace("postgresql://", "postgresql+psycopg://", 1), isolation_level="AUTOCOMMIT"
    )
    insert = sa.text(
        "INSERT INTO rule_document (key, version, effective_from, content, created_at) "
        "VALUES ('priority_weights', 2, :today, CAST(:content AS jsonb), now())"
    )
    remove = sa.text("DELETE FROM rule_document WHERE key = 'priority_weights' AND version = 2")
    try:
        with engine.connect() as conn:
            conn.execute(insert, {"today": utc_today(), "content": json.dumps(document)})
        yield
    finally:
        with engine.connect() as conn:
            conn.execute(remove)
        engine.dispose()


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
    function signature — the contract is what a client and a reviewer read.

    **Amended by Story 9.8**: `groups` joins the allowlist. It narrows what the
    response *contains* and, like the four beside it, offers nowhere to put a
    scope — it is typed on `Stage`, so the only thing it can name is one of four
    lifecycle values, and it intersects the caller's book rather than choosing
    whose book it is. The property this test defends is unchanged: no parameter
    on this route can widen or re-point the caseload."""
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()
    parameters = {p["name"] for p in schema["paths"][QUEUE]["get"].get("parameters", [])}

    assert parameters == {"filter", "stage", "groups", "cursor", "limit"}
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


async def test_a_cursor_past_the_end_of_its_group_ends_the_walk(seeded_db_url: str) -> None:
    """**Amended by Story 9.8**, and the amendment is the fix rather than a relaxation.

    Its predecessor asserted a 400, on the argument that an offset past the end
    describes a list this one is not — and that empty `items` beside a non-zero
    `total` and a null `nextCursor` reads as a group that is populated and
    finished at once.

    A cursor no longer carries an offset. It carries the `order_key` of the last
    card served, and a key that sorts below every remaining card means every
    claim after the caller's position has left the group. That is a walk that
    has *ended*, and the three fields now say so precisely: nothing after your
    position, N in the group, no next page.

    The property under test is unchanged — a client must never be able to read
    one response as both populated and finished — and it is asserted here on the
    reading a keyset makes true. A forged cursor that is genuinely unreadable is
    still a 400; `test_a_forged_cursor_carrying_infinity_is_a_400_not_a_500` and
    the two below it cover that.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        first = (await client.get(QUEUE, params={"limit": 5})).json()["groups"]["settled"]
        cursor = decode_cursor(first["nextCursor"])
        # A key below every card in the group: the lowest possible score, and a
        # business id after every seeded one.
        beyond = encode_cursor(replace(cursor, last_score=-1e9, last_claim_id="WC-ZZZZ"))
        resp = await client.get(QUEUE, params={"limit": 5, "stage": "settled", "cursor": beyond})

    assert resp.status_code == 200
    group = resp.json()["groups"]["settled"]
    assert group["items"] == []
    assert group["nextCursor"] is None
    assert group["total"] == first["total"]


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


async def test_a_forged_cursor_carrying_infinity_is_a_400_not_a_500(seeded_db_url: str) -> None:
    """`json.loads` accepts the bare literal `Infinity`, and `int(inf)` is an
    `OverflowError` — not a `ValueError`, so it escaped `decode_cursor`'s
    except clause and reached the unhandled-exception handler. Driven
    through the app rather than the function because the defect was
    *which status code the caller saw*.

    **Amended by Story 9.8**: the field carrying the infinity moved from the
    offset (`o`, which the decoder no longer reads) to the page size (`l`), and
    the payload carries a well-formed `k` so it survives as far as the guard.
    Left on `o` the cursor died on the missing `k` before `int(...)` was ever
    reached, and the test passed while exercising nothing — the amendment
    restores the property it was written to defend, on a field that still goes
    through `int(...)`. `test_drill_through.py` and `test_priority_claims.py`
    moved theirs the same way."""
    payload = {
        "f": "all",
        "s": "settled",
        "k": [1.0, "WC-0001"],
        "v": 1,
        "t": 1,
        "d": utc_today().isoformat(),
        "l": float("inf"),
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        resp = await client.get(QUEUE, params={"cursor": raw})

    assert resp.status_code == 400, resp.text
    assert resp.json()["type"] == "/problems/invalid-cursor"


async def test_a_forged_cursor_cannot_exceed_the_routes_page_ceiling(seeded_db_url: str) -> None:
    """`limit` inside a cursor is *reused* when the request omits one, so an
    unbounded cursor limit is a way past the route's 1–200 validator rather
    than a cosmetic gap."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        cursor = decode_cursor(
            (await client.get(QUEUE, params={"limit": 5})).json()["groups"]["settled"]["nextCursor"]
        )
        oversized = encode_cursor(replace(cursor, limit=100_000))
        resp = await client.get(QUEUE, params={"stage": "settled", "cursor": oversized})

    assert resp.status_code == 400
    assert "page size" in resp.json()["detail"]


@pytest.mark.parametrize("offset_days", [1, -(MAX_CURSOR_AGE.days + 1)])
async def test_a_cursor_dated_outside_the_plausible_window_is_refused(
    seeded_db_url: str, offset_days: int
) -> None:
    """A future date names a list this service never cut; a date older than
    `MAX_CURSOR_AGE` names one nobody is still reading.

    Before the documents were pinned to today's date, the second of these
    was worse than a wrong answer: the cursor's date chose which rule
    document to load, so a date before migration 0009's effective date
    raised `RuleDocumentMissing` — a `LookupError` the router does not
    catch, and therefore a 500 where the module promised a 400.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        cursor = decode_cursor(
            (await client.get(QUEUE, params={"limit": 5})).json()["groups"]["settled"]["nextCursor"]
        )
        shifted = encode_cursor(replace(cursor, as_of=utc_today() + timedelta(days=offset_days)))
        resp = await client.get(QUEUE, params={"limit": 5, "stage": "settled", "cursor": shifted})

    assert resp.status_code == 400, resp.text
    assert resp.json()["type"] == "/problems/invalid-cursor"


async def test_a_cursor_predating_the_first_rule_document_is_still_a_400(
    seeded_db_url: str,
) -> None:
    """The regression, named. Migration 0009 dates both documents 2026-08-11;
    a cursor claiming an earlier day used to make the loader look for a
    document effective then, find none, and raise past the router."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        cursor = decode_cursor(
            (await client.get(QUEUE, params={"limit": 5})).json()["groups"]["settled"]["nextCursor"]
        )
        ancient = encode_cursor(replace(cursor, as_of=date(2020, 1, 1)))
        resp = await client.get(QUEUE, params={"limit": 5, "stage": "settled", "cursor": ancient})

    assert resp.status_code == 400, resp.text
    assert resp.json()["type"] == "/problems/invalid-cursor"


async def test_a_refused_cursor_is_not_cacheable_either(seeded_db_url: str) -> None:
    """Raising abandons the injected `Response`, so the header has to be
    carried onto the problem document. A 400 naming a caller's filter and
    stage is as persona-specific as the 200 beside it — and it is the
    response most likely to be retried."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        resp = await client.get(QUEUE, params={"cursor": "not-a-cursor"})

    assert resp.status_code == 400
    assert resp.headers["cache-control"] == "no-store"


async def test_both_rule_document_versions_that_ranked_the_queue_are_reported(
    seeded_db_url: str,
) -> None:
    """Reporting only the weights version was reporting half the answer: the
    thresholds decide `risk`, `siuReview` and `rtwBlocked`, which are inputs
    to every score in the payload. The cursor already recorded both."""
    payload = await queue_for(seeded_db_url, *KAYA)

    assert payload["rulesVersion"] == 1
    # Six, not one, since Story 2.2 superseded the thresholds document with
    # a v2 carrying the treatment-phase parameters, Story 2.5 a v3 carrying the
    # claim-path ones, Story 3.1 a v4 carrying the PTD cut-off, Story 5.1 a
    # v5 carrying the dashboard's fraud-review threshold and Story 7.1 a v6
    # carrying the analyst workspace's two fraud-band edges. Pinned rather than
    # read from the loader for `seed_fixture`'s reason — and the number moving
    # is the mechanism working: a retuned threshold re-ranks the queue, so the
    # payload has to say which version answered. (v6 re-ranks nothing on
    # today's values, and the cursor still refuses a page cut under v5: the
    # version records which document decided the ordering, not whether the
    # ordering changed.)
    assert payload["thresholdsVersion"] == 7


async def test_both_totals_are_published_so_the_client_adds_nothing_up(
    seeded_db_url: str,
) -> None:
    """What lets the pane tell an empty book from an empty filter (NFR-3).

    `unfilteredTotal` is the scoped book before the predicate;
    `filteredTotal` is what the predicate left. The second is the sum of the
    four group totals and is sent anyway — a client that adds them up has
    re-implemented "how big is this queue" in the browser (AD-1), which is
    what `web/src/features/queue/noDerivation.test.ts` fails a build over.
    """
    unfiltered = await queue_for(seeded_db_url, *KAYA)
    litigated = await queue_for(seeded_db_url, *KAYA, params={"filter": "litigation"})

    expected = len(seed_fixture.expected_claim_ids(*KAYA))
    assert unfiltered["unfilteredTotal"] == expected
    assert unfiltered["filteredTotal"] == expected
    assert sum(unfiltered["groups"][stage]["total"] for stage in STAGES) == expected

    # Under a filter the two part company — which is the entire reason both
    # are on the wire.
    assert litigated["unfilteredTotal"] == expected
    assert litigated["filteredTotal"] < expected
    assert litigated["filteredTotal"] == sum(
        litigated["groups"][stage]["total"] for stage in STAGES
    )


# --- the weights really are data (AC 4, Task 2) -------------------------


async def test_a_second_priority_weights_version_reranks_the_queue(seeded_db_url: str) -> None:
    """**The acceptance criterion, as a test.** A real document, not a
    dataclass.

    Version 2 of `priority_weights` is inserted into `rule_document`
    effective today, with every term zeroed but litigation. The same request
    to the same endpoint, with no code path different between the two runs,
    then comes back in a different order — and the order is one this test
    can state from the seed file alone: litigated claims first, everything
    else after, each block by claim id (the tie-break, doing real work for
    once, since every claim in a block now scores identically).

    This is the half `test_priority_score.py` structurally cannot reach. It
    hands `priority_score` a `PriorityWeights` it built itself, which proves
    the arithmetic reads its parameters and nothing at all about the
    document → ZEN → validation → scorer chain that produces them.
    """
    zeroed = {
        "litigation": "1",
        "siuReview": "0",
        "rtwBlocked": "0",
        "pendingApproval": "0",
        "paymentDue": "0",
        "surgery": "0",
        "severityFactor": "0",
        "daysOpenFactor": "0",
        "settledPenalty": "0",
    }
    treatment = [
        claim
        for claim in seed_fixture.claims_for(*KAYA)
        if claim["stage"] == seed_fixture.STAGE_ORDER[2]
    ]
    expected = sorted(c["claim_id"] for c in treatment if c["litigation_flag"]) + sorted(
        c["claim_id"] for c in treatment if not c["litigation_flag"]
    )
    assert expected, "the treatment group must not be empty for this to say anything"

    before = await queue_for(seeded_db_url, *KAYA)
    assert ids(before["groups"]["treatment"]) != expected, (
        "v1 already ranks the group litigation-first-then-claim-id, so a change "
        "of ordering would prove nothing"
    )

    with superseding_weights(seeded_db_url, **zeroed):
        after = await queue_for(seeded_db_url, *KAYA)

    assert after["rulesVersion"] == 2
    assert after["thresholdsVersion"] == 7, "only one document was superseded"
    assert ids(after["groups"]["treatment"]) == expected
    # The marker rule is data too: with the categorical weights gone, no
    # claim clears a threshold of 30 and no card carries a 🔺.
    assert not any(
        item["priorityMarker"] for stage in STAGES for item in after["groups"][stage]["items"]
    )

    # …and the superseded document really is gone again, or every test after
    # this one would be running against v2.
    assert (await queue_for(seeded_db_url, *KAYA))["rulesVersion"] == 1


async def test_a_cursor_ranked_by_a_superseded_document_is_refused(seeded_db_url: str) -> None:
    """The guard the cursor's docstring has always claimed, now reachable.

    It was unreachable on the production path: `today` resolved to the
    cursor's own recorded date, so the documents were loaded *effective on
    that date* and `decoded.rules_version != weights.version` compared a
    version against itself. A cursor issued under v1 therefore went on being
    served from a v1 ranking for ever, however many versions had superseded
    it — silently, since the payload's `rulesVersion` came from the same
    stale lookup.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        cursor = (await client.get(QUEUE, params={"limit": 5})).json()["groups"]["settled"][
            "nextCursor"
        ]
        assert cursor is not None

        with superseding_weights(seeded_db_url, settledPenalty="0"):
            resp = await client.get(
                QUEUE, params={"limit": 5, "stage": "settled", "cursor": cursor}
            )

    assert resp.status_code == 400, resp.text
    assert resp.json()["type"] == "/problems/invalid-cursor"
    assert "priority_weights v1" in resp.json()["detail"]
    assert "v2 is now effective" in resp.json()["detail"]


# --- Story 9.8: `groups`, and the day the cards were aged against --------


async def test_the_initial_load_still_returns_all_four_groups(seeded_db_url: str) -> None:
    """ "Every group is always the truth" survives, and this is where it is pinned.

    A request with no `groups` parameter is the pane's initial load, and it must
    keep returning four groups — including empty ones, because a response with a
    group silently missing is indistinguishable from a caseload that really has
    nothing in that stage, and "no claims in this stage" is a message this
    console owes the user honestly (NFR-3).

    Sarah's 3M book is the one with an *empty* intake group in real seeded data,
    which is what makes "present and empty" testable rather than contrived.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        groups = (await client.get(QUEUE)).json()["groups"]

    assert set(groups) == set(STAGES)
    assert all(groups[stage] is not None for stage in STAGES)
    assert groups["intake"]["items"] == []
    assert groups["intake"]["total"] == 0


async def test_naming_one_group_returns_that_group_and_omits_the_others(
    seeded_db_url: str,
) -> None:
    """AC 5 — the "Show more" request, narrowed.

    Omitted, not emptied. `null` is "you did not ask"; a present group with
    `items: []` and `total: 0` is "there is nothing in this stage". Collapsing
    the two would be the very confusion the unnarrowed contract was written to
    avoid, reintroduced through the back door.

    The narrowed group is byte-identical to the same group in the unnarrowed
    response, which is the other half of the contract: `groups` decides what is
    computed and serialised, never what any of it says.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        full = (await client.get(QUEUE, params={"limit": 5})).json()
        narrowed = (await client.get(QUEUE, params={"limit": 5, "groups": "treatment"})).json()

    assert narrowed["groups"]["treatment"] == full["groups"]["treatment"]
    assert narrowed["groups"]["intake"] is None
    assert narrowed["groups"]["investigation"] is None
    assert narrowed["groups"]["settled"] is None


async def test_a_narrowed_response_still_states_how_big_the_whole_queue_is(
    seeded_db_url: str,
) -> None:
    """The two totals are counted over the book, never summed from the payload.

    They are what the pane's chips and its two empty-state sentences are drawn
    from (NFR-3), and they must not change because a client asked for one
    section. `filteredTotal` in particular used to be the sum of the four group
    totals — correct while all four were always present, and a lie the moment
    one request holds one group.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        full = (await client.get(QUEUE, params={"limit": 5})).json()
        narrowed = (await client.get(QUEUE, params={"limit": 5, "groups": "settled"})).json()

    assert narrowed["unfilteredTotal"] == full["unfilteredTotal"]
    assert narrowed["filteredTotal"] == full["filteredTotal"]
    assert narrowed["filteredTotal"] > narrowed["groups"]["settled"]["total"]


async def test_naming_several_groups_returns_exactly_those(seeded_db_url: str) -> None:
    """The parameter is a list, and the response is its image.

    Two rather than one, because a "narrowing" implemented as "the one group the
    cursor addresses" would pass every test above and fail this one.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        body = (
            await client.get(QUEUE, params=[("groups", "intake"), ("groups", "settled")])
        ).json()

    assert body["groups"]["intake"] is not None
    assert body["groups"]["settled"] is not None
    assert body["groups"]["investigation"] is None
    assert body["groups"]["treatment"] is None


async def test_a_narrowed_show_more_walks_the_same_group_the_cursor_names(
    seeded_db_url: str,
) -> None:
    """The two parameters together, as the SPA sends them.

    `stage` says which group the cursor addresses and narrows nothing; `groups`
    says what to compute and return. A "Show more" sends both, naming the same
    stage in each, and gets one group's next page — the request the endpoint
    used to answer by ranking, marking and serialising four.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        first = (await client.get(QUEUE, params={"limit": 5})).json()["groups"]["settled"]
        assert first["nextCursor"] is not None
        second = (
            await client.get(
                QUEUE,
                params={
                    "limit": 5,
                    "stage": "settled",
                    "groups": "settled",
                    "cursor": first["nextCursor"],
                },
            )
        ).json()

    assert second["groups"]["settled"]["total"] == first["total"]
    assert second["groups"]["intake"] is None
    served = {card["claimId"] for card in first["items"]}
    assert served.isdisjoint({card["claimId"] for card in second["groups"]["settled"]["items"]})


async def test_a_cursor_for_a_group_the_request_did_not_ask_for_is_refused(
    seeded_db_url: str,
) -> None:
    """A cursor names a group; `groups` decides which groups come back.

    Pair a settled cursor with `groups=intake` and every check the decoded
    cursor faces passes — same filter, same two rule versions, same day, same
    page size — because all of them describe the list it was cut from. What no
    other check catches is that the loop never reaches the settled group at all,
    so `resume_after` is never applied: the answer was intake **page one** with
    a fresh cursor, the settled group absent entirely, and a 200 over the top of
    a walk that had silently restarted somewhere else. "Never silently
    reinterpreted, never a silent page one" is the story's Always, and it has to
    hold for the parameter that decides what comes back and not only for the one
    that names what the cursor addresses.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        cursor = (await client.get(QUEUE, params={"limit": 5})).json()["groups"]["settled"][
            "nextCursor"
        ]
        assert cursor is not None
        resp = await client.get(QUEUE, params={"limit": 5, "groups": "intake", "cursor": cursor})

    assert resp.status_code == 400, resp.text
    assert resp.json()["type"] == "/problems/invalid-cursor"
    # Names both sides, because "that cursor is wrong" is unactionable and the
    # caller has to know which group it belongs to.
    assert "settled" in resp.json()["detail"]
    assert "intake" in resp.json()["detail"]


async def test_a_cursor_is_accepted_when_its_group_is_among_those_asked_for(
    seeded_db_url: str,
) -> None:
    """The control on the refusal above: naming more groups than one still walks.

    Without it the check could refuse every narrowed "Show more" and this file
    would still be green — a cursor that refused its own group would make every
    narrowed walk one page long.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        first = (await client.get(QUEUE, params={"limit": 5})).json()["groups"]["settled"]
        assert first["nextCursor"] is not None
        resp = await client.get(
            QUEUE,
            params=[
                ("limit", "5"),
                ("groups", "settled"),
                ("groups", "intake"),
                ("cursor", first["nextCursor"]),
            ],
        )

    assert resp.status_code == 200, resp.text
    served = {card["claimId"] for card in first["items"]}
    body = resp.json()
    assert served.isdisjoint({card["claimId"] for card in body["groups"]["settled"]["items"]})
    assert body["groups"]["intake"] is not None


async def test_an_unknown_group_is_refused_rather_than_ignored(seeded_db_url: str) -> None:
    """A typo must not silently widen the response back to four groups.

    Typed on the `Stage` enum, so FastAPI answers 422 before the handler runs —
    the same shape `filter=banana` already produces on this route.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        resp = await client.get(QUEUE, params={"groups": "banana"})

    assert resp.status_code == 422


async def test_an_empty_groups_list_is_refused_rather_than_answered_with_four_nulls() -> None:
    """`None` means all four; `[]` can only be a caller who built the list empty.

    Answering it returned a 200 whose four groups were all `null` beside a
    non-zero `filteredTotal` — a payload saying at once "your queue holds 41
    claims" and "you have no groups", which is indistinguishable from an error
    and reads on screen as a caseload that vanished.

    Asserted against the service rather than the route because the route cannot
    produce one: an absent query parameter binds to `None`, and there is no wire
    spelling for a zero-length list — `?groups=` is the nearest thing and the
    enum answers 422 (the test below). So this is a programming error, and a
    `ValueError` rather than the `InvalidCursor` the cursor checks raise. The
    guard runs before the session is touched, which is why the two `None`s below
    are never dereferenced.
    """
    with pytest.raises(ValueError, match="cannot be empty"):
        await claim_queue(cast(AsyncSession, None), cast(CallerContext, None), groups=())


async def test_an_empty_groups_parameter_is_refused_by_the_route(seeded_db_url: str) -> None:
    """The other half: `?groups=` is not a zero-length list, it is an invalid
    stage, and FastAPI answers 422 off the enum before the handler runs."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        resp = await client.get(QUEUE, params={"groups": ""})

    assert resp.status_code == 422


async def test_the_queue_publishes_the_day_it_aged_the_cards_against(
    seeded_db_url: str,
) -> None:
    """AC 2 — `/dashboard/trends`' field, on the handler's primary surface.

    Page one resolves it to today; a "Show more" reuses the day the cursor
    pinned, which is what makes page two a page of the list page one was. A
    queue left open across UTC midnight therefore publishes `daysOpen` values
    and an ordering computed against yesterday — already true, and until now
    unstated. Both pages state the same day, which is the property that matters.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        first = (await client.get(QUEUE, params={"limit": 5})).json()
        cursor = first["groups"]["settled"]["nextCursor"]
        assert cursor is not None
        second = (
            await client.get(QUEUE, params={"limit": 5, "stage": "settled", "cursor": cursor})
        ).json()

    assert first["asOf"] == utc_today().isoformat()
    assert second["asOf"] == first["asOf"]


async def test_the_queue_does_not_accept_as_of_as_an_input(seeded_db_url: str) -> None:
    """`asOf` is published, never accepted (the story's Never list).

    A caller who could choose the day could choose the ranking — `days_open`
    feeds the score. Asserted against the published contract rather than by
    sending one, because an unknown query parameter is silently ignored and an
    "it had no effect" test would keep passing the day somebody declared it.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        schema = (await client.get("/openapi.json")).json()

    names = {p["name"] for p in schema["paths"]["/claims/queue"]["get"]["parameters"]}
    assert names == {"filter", "stage", "groups", "cursor", "limit"}


async def test_a_cursor_predating_the_keyset_change_is_refused(seeded_db_url: str) -> None:
    """AC 1: an offset-shaped cursor is refused, never silently reinterpreted.

    A cursor minted before Story 9.8 carries `o` and no `k`. Reading the offset
    as a key would resume at an arbitrary row; answering page one would
    re-append cards the handler already has. Both are silent, which is why the
    only acceptable answer is this 400.
    """
    stale = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "f": "all",
                    "s": "settled",
                    "o": 5,
                    "v": 1,
                    "t": 5,
                    "d": utc_today().isoformat(),
                    "l": 5,
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        resp = await client.get(QUEUE, params={"limit": 5, "stage": "settled", "cursor": stale})

    assert resp.status_code == 400
    assert resp.json()["type"] == "/problems/invalid-cursor"
