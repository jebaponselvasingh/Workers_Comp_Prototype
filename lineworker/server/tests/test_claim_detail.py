"""Story 2.2 — `GET /claims/{claimBusinessId}` over a real seeded database.

Driven through the app rather than the service, because most of what this
story promises is a *contract*: one stage variant per response, a stepper
already marked, money in cents, and a 404 that cannot be used to find out
whether somebody else's claim exists.

Expectations come from `seed_fixture` (the seed files, restated
independently) and from the derivation rules written out in
`test_case_file_derivations.py`. Nothing here imports the assembly under
test to decide what the assembly should produce.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from api import create_app
from config import Settings
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

# Kaya's book covers all four stages; Sarah's 3M book is eight claims with
# nothing in intake — which makes her the caller who must not be able to see
# a Caterpillar claim id even when handed one.
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


async def detail_for(db_url: str, persona: tuple[str, str], claim_id: str) -> dict[str, Any]:
    async with make_client(db_url) as client:
        await login_as(client, *persona)
        resp = await client.get(f"/claims/{claim_id}")
        assert resp.status_code == 200, resp.text
        payload: dict[str, Any] = resp.json()
        return payload


def _one_of(persona: tuple[str, str], stage: str) -> dict[str, Any]:
    """A seeded claim in `stage` from the persona's book — the first by id.

    Chosen from the seed file rather than hardcoded so the tests keep
    describing "a treatment claim of Kaya's" if the portfolio changes.
    """
    claims = sorted(
        (c for c in seed_fixture.claims_for(*persona) if c["stage"] == stage),
        key=lambda c: c["claim_id"],
    )
    assert claims, f"the seed has no {stage} claim for {persona[0]}"
    return claims[0]


# --- the contract -------------------------------------------------------


async def test_the_route_is_not_shadowed_by_the_queue(seeded_db_url: str) -> None:
    """`/claims/queue` and `/claims/{id}` share a prefix.

    The queue is declared first so it wins the match, and the path parameter
    is patterned to `WC-nnnn` so a stray segment cannot reach the detail
    service at all. Both facts are one route-ordering mistake away from
    breaking, and the symptom would be a 422 on the handler's whole queue.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)

        assert (await client.get("/claims/queue")).status_code == 200
        assert (await client.get("/claims/not-a-claim-id")).status_code == 422


async def test_an_unauthenticated_request_is_refused(seeded_db_url: str) -> None:
    async with make_client(seeded_db_url) as client:
        resp = await client.get("/claims/WC-20017")

    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


async def test_the_response_is_not_cacheable(seeded_db_url: str) -> None:
    """One persona's case file must never be served to another from a cache
    upstream — the reason `/me`, `/stats/*` and the queue all say so."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        resp = await client.get(f"/claims/{_one_of(KAYA, 'treatment')['claim_id']}")

    assert resp.headers["cache-control"] == "no-store"


# --- AD-7: scope, and the enumeration oracle it closes -------------------


async def test_a_claim_outside_the_callers_book_is_a_404(seeded_db_url: str) -> None:
    """The security assertion of this story.

    Sarah handles 3M alone. Handed a real Caterpillar claim id — one that
    exists, one that Kaya can open — she must get the *same* answer she would
    get for a claim id nobody has. A 403 would confirm the claim exists, and
    walking `WC-20000`…`WC-20999` would then enumerate a portfolio she cannot
    read.
    """
    someone_elses = _one_of(KAYA, "treatment")["claim_id"]
    assert someone_elses not in seed_fixture.expected_claim_ids(*SARAH)

    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        out_of_scope = await client.get(f"/claims/{someone_elses}")
        never_existed = await client.get("/claims/WC-99999")

    assert out_of_scope.status_code == 404
    assert never_existed.status_code == 404
    # Byte-identical but for the id, so the two cases are indistinguishable
    # to a caller probing for existence.
    assert out_of_scope.json()["title"] == never_existed.json()["title"]
    assert out_of_scope.json()["type"] == never_existed.json()["type"]
    assert someone_elses not in never_existed.text


async def test_the_404_is_not_cacheable_either(seeded_db_url: str) -> None:
    """It depends on who is asking: the same id is a 200 for Kaya. An
    intermediary that cached Sarah's refusal could replay it to him."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        resp = await client.get(f"/claims/{_one_of(KAYA, 'treatment')['claim_id']}")

    assert resp.status_code == 404
    assert resp.headers["cache-control"] == "no-store"


async def test_every_claim_in_the_book_opens(seeded_db_url: str) -> None:
    """The other half of scope: nothing a persona *should* see is refused."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        for claim_id in sorted(seed_fixture.expected_claim_ids(*SARAH)):
            resp = await client.get(f"/claims/{claim_id}")
            assert resp.status_code == 200, f"{claim_id}: {resp.text}"


# --- the header and the stepper -----------------------------------------


async def test_the_header_carries_the_claims_identity_and_flags(seeded_db_url: str) -> None:
    claim = _one_of(KAYA, "treatment")
    payload = await detail_for(seeded_db_url, KAYA, claim["claim_id"])
    header = payload["header"]

    assert header["claimId"] == claim["claim_id"]
    assert header["state"] == claim["state"]
    assert header["injuryType"] == claim["injury_type"]
    assert header["bodyPart"] == claim["body_part"]
    assert header["icd"] == claim["icd"]
    assert header["severityScore"] == claim["severity_score"]
    assert header["fraudFlag"] == claim["fraud_flag"]
    assert header["fraudScore"] == claim["fraud_score"]
    assert header["litigationFlag"] == claim["litigation_flag"]
    assert header["surgeryRequired"] == claim["surgery_required"]
    assert header["oshaRecordable"] == claim["osha_recordable"]


async def test_the_headers_risk_band_is_the_queue_cards_risk_band(seeded_db_url: str) -> None:
    """AD-10, asserted where it actually matters.

    The gauge and the queue dot are the two places a handler sees "how bad is
    this claim", side by side. They read the same registered derivation, so
    the assertion is that they *cannot* disagree — checked over the whole
    book rather than on one claim, because a single example would pass for a
    second implementation that happened to agree on that claim.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *SARAH)
        cards = {
            item["claimId"]: item["risk"]
            for group in (await client.get("/claims/queue")).json()["groups"].values()
            for item in group["items"]
        }
        assert cards, "the queue answered nothing — this would pass vacuously"
        for claim_id, band in cards.items():
            detail = (await client.get(f"/claims/{claim_id}")).json()
            assert detail["header"]["risk"] == band, claim_id


@pytest.mark.parametrize(
    ("stage", "done", "current"),
    [
        ("intake", [], "intake"),
        ("investigation", ["intake"], "investigation"),
        ("treatment", ["intake", "investigation"], "treatment"),
        ("settled", ["intake", "investigation", "treatment"], "settled"),
    ],
)
async def test_the_stepper_marks_done_and_current_for_each_stage(
    seeded_db_url: str, stage: str, done: list[str], current: str
) -> None:
    """AC 2. The four steps are always all four, in lifecycle order — a
    stepper that omitted the stages a claim has not reached would leave the
    handler unable to see where the claim is going."""
    payload = await detail_for(seeded_db_url, KAYA, _one_of(KAYA, stage)["claim_id"])
    stepper = payload["stepper"]

    assert [step["stage"] for step in stepper] == [
        "intake",
        "investigation",
        "treatment",
        "settled",
    ]
    assert [step["stage"] for step in stepper if step["done"]] == done
    assert [step["stage"] for step in stepper if step["current"]] == [current]


# --- one variant, and it is the claim's ---------------------------------


@pytest.mark.parametrize("stage", ["intake", "investigation", "treatment", "settled"])
async def test_the_overview_is_the_variant_for_the_claims_stage(
    seeded_db_url: str, stage: str
) -> None:
    """AC 3, structurally: one block, discriminated, and it matches the header.

    The alternative shape — four optional blocks — can describe a claim as
    simultaneously in intake and settled. This asserts the shape rather than
    trusting it.
    """
    payload = await detail_for(seeded_db_url, KAYA, _one_of(KAYA, stage)["claim_id"])

    assert payload["overview"]["stageVariant"] == stage
    assert payload["header"]["stage"] == stage


async def test_the_intake_variant_carries_the_document_checklist(seeded_db_url: str) -> None:
    """AC 3's Received/Missing rows, against the seeded documents.

    The expectation comes from `seed_fixture`, which compares the claim's
    seeded document types against the required list restated from the story
    text — not from the JDM document the service reads.
    """
    claim_id = _one_of(KAYA, "intake")["claim_id"]
    payload = await detail_for(seeded_db_url, KAYA, claim_id)

    assert payload["overview"]["checklist"] == seed_fixture.expected_intake_checklist(claim_id)


async def test_a_claim_missing_a_required_document_says_so(seeded_db_url: str) -> None:
    """The checklist exists for this case, so a test that only ever saw
    complete claims would assert nothing about it."""
    incomplete = [
        claim["claim_id"]
        for claim in seed_fixture.claims_for(*KAYA)
        if claim["stage"] == "intake"
        and any(
            not row["received"] for row in seed_fixture.expected_intake_checklist(claim["claim_id"])
        )
    ]
    assert incomplete, "no seeded intake claim is missing a required document"

    payload = await detail_for(seeded_db_url, KAYA, incomplete[0])
    checklist = payload["overview"]["checklist"]

    assert any(not row["received"] for row in checklist)
    assert any(row["received"] for row in checklist), "a checklist of all-missing proves less"


async def test_the_investigation_variant_is_read_only_and_carries_the_financials(
    seeded_db_url: str,
) -> None:
    """Inline editing is Story 2.3. What this asserts is the *figures*: the
    injury and financial fields the card draws, and the paid total the
    prototype labels "total incurred"."""
    claim = _one_of(KAYA, "investigation")
    overview = (await detail_for(seeded_db_url, KAYA, claim["claim_id"]))["overview"]

    assert overview["policyNum"] == claim["policy_num"]
    assert overview["reserveCents"] == claim["reserve"]
    assert overview["awwCents"] == claim["aww"]
    assert overview["totalPaidCents"] == (
        claim["paid_indemnity"] + claim["paid_medical"] + claim["paid_expense"]
    )
    assert overview["disability"] == claim["disability"]
    # The column holds tokens since the Story 2.3 code review; the seed file
    # still records the prototype's display strings, so the comparison runs
    # through migration 0013's map — the same map the conversion used.
    assert overview["recovery"] == seed_fixture.recovery_token(claim["recovery"])


async def test_the_cost_split_sums_to_a_hundred_or_is_absent(seeded_db_url: str) -> None:
    """The bar is three widths, so its shares have to total 100 exactly.

    The prototype rounds each independently with `toFixed(0)` and can total
    99 or 101.

    **No `pytest.skip` at the end of this test** (code review, 2026-08-12).
    It used to skip when the seeded book happened to contain no zero-paid
    claim — but pytest marks the *whole* test SKIPPED whatever it already
    asserted, so a green run reported this property as not run, and it
    survived on exactly one seed row. The null-bar case is its own test
    below, where a missing fixture is a skip that costs nothing.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        checked = 0
        for claim in seed_fixture.claims_for(*KAYA):
            if claim["stage"] not in {"investigation", "settled"}:
                continue
            overview = (await client.get(f"/claims/{claim['claim_id']}")).json()["overview"]
            split = overview["costSplit"]
            paid = claim["paid_indemnity"] + claim["paid_medical"] + claim["paid_expense"]
            if paid <= 0:
                assert split is None, claim["claim_id"]
                continue
            assert split["indemnityPct"] + split["medicalPct"] + split["expensePct"] == 100, claim[
                "claim_id"
            ]
            checked += 1

    assert checked, "no seeded claim exercised the populated bar"


async def test_a_claim_with_no_payments_has_no_cost_split(seeded_db_url: str) -> None:
    """`None` rather than three zeros — the other half of the bar's contract.

    Skipping here is honest: whether the portfolio contains an unpaid
    investigation or settled claim is a property of the data, not of this
    code, and the skip is decided before anything is asserted.
    """
    unpaid = [
        claim["claim_id"]
        for claim in seed_fixture.claims_for(*KAYA)
        if claim["stage"] in {"investigation", "settled"}
        and claim["paid_indemnity"] + claim["paid_medical"] + claim["paid_expense"] <= 0
    ]
    if not unpaid:
        pytest.skip("no seeded investigation/settled claim of Kaya's has zero payments")

    overview = (await detail_for(seeded_db_url, KAYA, unpaid[0]))["overview"]
    assert overview["costSplit"] is None


async def test_the_treatment_variant_carries_both_derived_states(seeded_db_url: str) -> None:
    """AC 5 on the wire: the phase and the coordination status are values the
    server decided, with the notes that describe them."""
    claim = _one_of(KAYA, "treatment")
    overview = (await detail_for(seeded_db_url, KAYA, claim["claim_id"]))["overview"]

    assert overview["phase"] in {"early", "active", "approaching_mmi"}
    assert overview["phaseNote"]
    assert overview["expectedDays"] > 0
    assert overview["daysOpen"] >= 0
    assert overview["coordinationStatus"] in {
        "coordination_gap",
        "awaiting_information",
        "legal_coordination",
        "on_track",
    }
    assert overview["coordinationNote"]
    assert overview["returnStatus"] == claim["return_status"]
    assert overview["reserveCents"] == claim["reserve"]


async def test_the_treatment_coordination_agrees_with_the_queues_blocked_flag(
    seeded_db_url: str,
) -> None:
    """`rtw_blocked` is an input to the coordination rule and a badge on the
    queue card. One derivation feeds both, so a blocked claim must never show
    "Coordination On Track" beside a card that says otherwise."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        treatment = (await client.get("/claims/queue")).json()["groups"]["treatment"]["items"]
        blocked = [item["claimId"] for item in treatment if item["rtwBlocked"]]
        assert blocked, "no blocked claim in the seeded treatment group"

        for claim_id in blocked:
            overview = (await client.get(f"/claims/{claim_id}")).json()["overview"]
            assert overview["coordinationStatus"] == "coordination_gap", claim_id


async def test_the_settled_variant_carries_the_payout_and_the_outcome(
    seeded_db_url: str,
) -> None:
    claim = _one_of(KAYA, "settled")
    overview = (await detail_for(seeded_db_url, KAYA, claim["claim_id"]))["overview"]

    assert overview["paidIndemnityCents"] == claim["paid_indemnity"]
    assert overview["paidMedicalCents"] == claim["paid_medical"]
    assert overview["paidExpenseCents"] == claim["paid_expense"]
    assert overview["reserveCents"] == claim["reserve"]
    assert overview["totalPaidCents"] == (
        claim["paid_indemnity"] + claim["paid_medical"] + claim["paid_expense"]
    )
    assert overview["disability"] == claim["disability"]
    assert overview["litigationFlag"] == claim["litigation_flag"]
    assert overview["daysToSettlement"] >= 0


async def test_the_settlement_date_is_null_because_the_prototype_has_none(
    seeded_db_url: str,
) -> None:
    """The data-quirk ruling, asserted end to end.

    Every seeded settlement event carries the literal string `Closed` where a
    date belongs, so there is no settlement date to report. The banner omits
    the clause; it does not print "settled on Closed" as the prototype does.
    """
    claim_id = _one_of(KAYA, "settled")["claim_id"]
    overview = (await detail_for(seeded_db_url, KAYA, claim_id))["overview"]

    settlement_events = [
        event
        for event in seed_fixture.timeline_events_for(claim_id)
        if event["tag"] == "settlement"
    ]
    assert settlement_events, "the chosen claim has no settlement event"
    assert all(event["event_date"] is None for event in settlement_events)
    assert overview["settlementDate"] is None


# --- the timeline -------------------------------------------------------


@pytest.mark.parametrize("stage", ["intake", "investigation", "settled"])
async def test_those_variants_carry_the_whole_timeline_in_append_order(
    seeded_db_url: str, stage: str
) -> None:
    claim_id = _one_of(KAYA, stage)["claim_id"]
    overview = (await detail_for(seeded_db_url, KAYA, claim_id))["overview"]

    expected = seed_fixture.timeline_events_for(claim_id)
    assert [
        {"eventDate": e["event_date"], "description": e["description"], "tag": e["tag"]}
        for e in expected
    ] == overview["timeline"]


async def test_the_treatment_variant_carries_the_recent_slice_the_server_cut(
    seeded_db_url: str,
) -> None:
    """The prototype's `.slice(-6)`, decided server-side (AD-1).

    **No seeded treatment claim reaches the window.** The longest treatment
    timeline in the portfolio is exactly six events — a claim in treatment
    has not yet accumulated the RTW and settlement entries that make the
    settled timelines seven — so `timelineTruncated` is `false` for every one
    of them today, and the prototype's slice never fired either. That is a
    property of the data, not of the rule, so it is asserted here as a fact
    about the seed and the *rule* is exercised over its whole domain in
    `test_claim_detail_assembly.py`, which needs no database and can build a
    twelve-event claim.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        checked = 0
        for claim in seed_fixture.claims_for(*KAYA):
            if claim["stage"] != "treatment":
                continue
            claim_id = claim["claim_id"]
            overview = (await client.get(f"/claims/{claim_id}")).json()["overview"]
            events = seed_fixture.timeline_events_for(claim_id)

            assert overview["timelineTruncated"] is (
                len(events) > seed_fixture.RECENT_TIMELINE_COUNT
            ), claim_id
            assert [
                {"eventDate": e["event_date"], "description": e["description"], "tag": e["tag"]}
                for e in seed_fixture.expected_recent_timeline(claim_id)
            ] == overview["timeline"], claim_id
            checked += 1

    assert checked, "no treatment claim in the book — this would pass vacuously"


# --- the contract's small print -----------------------------------------


async def test_the_payload_names_the_rules_version_behind_it(seeded_db_url: str) -> None:
    """The queue's argument: the risk band and the treatment phase both come
    from a versioned document, so "which rules produced this?" should be
    answerable from the response."""
    payload = await detail_for(seeded_db_url, KAYA, _one_of(KAYA, "treatment")["claim_id"])

    # Six since Story 7.1 added the analyst workspace's fraud-band edges (five
    # since 5.1 added the dashboard's fraud-review cut-off, four since 3.1 added
    # the PTD threshold). Pinned rather than read from the loader, for
    # `seed_fixture`'s reason.
    assert payload["thresholdsVersion"] == 6
    # Intake is the only variant that consults the requirements document, so
    # it is the only one that names a version for it.
    assert payload["requirementsVersion"] is None

    intake = await detail_for(seeded_db_url, KAYA, _one_of(KAYA, "intake")["claim_id"])
    assert intake["requirementsVersion"] == 1


async def test_the_claim_version_is_published_for_the_edit_story(
    seeded_db_url: str,
) -> None:
    """Story 2.3 sends it back as `expected_version`. An endpoint that made a
    client fetch the entity twice to edit it once would be the round trip
    AD-4 exists to avoid."""
    payload = await detail_for(seeded_db_url, KAYA, _one_of(KAYA, "intake")["claim_id"])

    assert payload["version"] >= 1


async def test_the_route_takes_no_scope_shaped_parameter(seeded_db_url: str) -> None:
    """`/stats/*` and the queue make the same assertion: the only input is
    which claim, never whose. Smuggled names are ignored like any unknown
    query string — a 422 would tell a caller which names exist."""
    claim_id = _one_of(KAYA, "treatment")["claim_id"]

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        plain = await client.get(f"/claims/{claim_id}")
        smuggled = await client.get(
            f"/claims/{claim_id}",
            params={"employerId": 1, "scopeAll": "true", "userId": 7, "role": "supervisor"},
        )

    assert plain.json() == smuggled.json()


# --- Story 5.5: what the read-only claim view rests on -------------------
#
# The dashboard's drill-through opens a **read-only** case file, composed from
# the presentational half of Epic 2/3's components and fed by this one GET and
# nothing else. Two properties have to hold for that to be honest, and neither
# was pinned before this story:
#
#   1. A supervisor and an analyst can read this endpoint inside their scope
#      and get a 404 outside it — so the view rests on a *scoped read*, not on
#      a role branch.
#   2. Every claim-mutating command refuses them — so "read-only" is enforced
#      by the server rather than by which components the SPA happened to render.
#
# Scope gates visibility; role gates capability (AD-7). Both halves, asserted.

BLINE = ("David Bline", "supervisor")
PARK = ("Jennifer Park", "supervisor")
ANALYST = ("David Bline", "analyst")


@pytest.mark.parametrize("persona", [BLINE, PARK, ANALYST])
async def test_an_oversight_persona_may_read_a_claim_inside_her_scope(
    seeded_db_url: str, persona: tuple[str, str]
) -> None:
    """The read the drill-through's claim view is built on.

    No role gate on this route at all — the only thing separating two callers'
    answers is the scope predicate, which is what lets the same GET serve a
    handler's workspace and a supervisor's read-only view.
    """
    claim_id = sorted(seed_fixture.expected_claim_ids(*persona))[0]

    payload = await detail_for(seeded_db_url, persona, claim_id)

    assert payload["claimId"] == claim_id
    assert payload["header"]["claimId"] == claim_id


@pytest.mark.parametrize("persona", [PARK, ANALYST])
async def test_an_oversight_persona_reading_outside_her_scope_gets_a_not_found(
    seeded_db_url: str, persona: tuple[str, str]
) -> None:
    """404, never 403 — no existence leak, whatever the role.

    Jennifer Park's book holds three employers, so a Boeing claim is a claim she
    must not be able to *learn exists*. The analyst is parametrized beside her
    because "the analyst reads everything" is exactly the assumption a
    role-branching implementation would encode.
    """
    outside = sorted(
        seed_fixture.expected_claim_ids(*BLINE) - seed_fixture.expected_claim_ids(*persona)
    )
    if not outside:
        pytest.skip("this persona's scope is the whole portfolio")

    async with make_client(seeded_db_url) as client:
        await login_as(client, *persona)
        resp = await client.get(f"/claims/{outside[0]}")

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["type"] == "/problems/claim-not-found"


#: Every claim-mutating command, by method and path suffix.
#:
#: Named here rather than discovered from the router, because the point is that
#: a *new* command added without a role gate should fail this test — and a test
#: that enumerated the routes would silently grow to cover it while asserting
#: nothing about it.
MUTATIONS: list[tuple[str, str, dict[str, Any]]] = [
    ("PATCH", "", {"injuryType": "Laceration", "expectedVersion": 1}),
    (
        "POST",
        "/injuries",
        {"bodyKey": "torso", "injuryType": "Strain", "severityScore": 20, "expectedVersion": 1},
    ),
    ("DELETE", "/injuries/1?expectedVersion=1", {}),
    ("PATCH", "/severity", {"severityScore": 50, "expectedVersion": 1}),
    ("PATCH", "/comp-rate", {"compRateBp": 6667, "expectedVersion": 1}),
    ("POST", "/payments/approvals", {"kind": "week", "targetId": 1, "expectedVersion": 1}),
    ("POST", "/assessment/approval", {"expectedVersion": 1}),
    (
        "POST",
        "/documents/1/review",
        {"expectedVersion": 1, "step": "mark_document_reviewed"},
    ),
    ("POST", "/osha-log", {"expectedVersion": 1}),
]


@pytest.mark.parametrize(("method", "suffix", "body"), MUTATIONS)
@pytest.mark.parametrize("persona", [BLINE, PARK, ANALYST])
async def test_every_claim_mutation_refuses_an_oversight_persona(
    seeded_db_url: str,
    persona: tuple[str, str],
    method: str,
    suffix: str,
    body: dict[str, Any],
) -> None:
    """Role gates capability — "read-only" is the server's answer, not the SPA's.

    The claim is one the persona **can** read, so a 404 here would mean the test
    had proved nothing about capability. 403 before any lookup is the contract —
    and the bodies above are well-formed rather than empty on purpose, because
    FastAPI validates a request body before the handler runs, so a malformed one
    would answer 422 and this test would be asserting nothing about the gate.
    """
    claim_id = sorted(seed_fixture.expected_claim_ids(*persona))[0]

    async with make_client(seeded_db_url) as client:
        await login_as(client, *persona)
        resp = await client.request(
            method,
            f"/claims/{claim_id}{suffix}",
            json=body if method != "DELETE" else None,
        )

    assert resp.status_code == 403, f"{method} {suffix} answered {resp.status_code}"
    assert resp.headers["content-type"].startswith("application/problem+json")
