"""Story 2.5 — the Documents & ID tab and the viewer, over a real database.

Driven through the app for `test_claim_detail.py`'s reason: most of what this
story promises is a *contract* — a path that was actually classified, forms in
the order a card renders them, an ID card that does not change shape with the
claim's stage, two viewer sheets, and a 404 that cannot be used to enumerate
document ids.

Expectations come from the seed files and from the classification rule written
out in `test_path_classification.py`. Nothing here imports the assembly under
test to decide what the assembly should produce.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.context import CallerContext
from data.models import AppUser
from data.models.enums import ClaimPath, Disability, RecoveryWindow, ReturnStatus, UserRole
from data.repositories.identity import employer_ids_for
from services.claims.detail import claim_detail
from tests import seed_fixture
from tests.conftest import requires_db
from tests.test_path_classification import DEPLOYED

pytestmark = requires_db

KAYA = ("Kaya Johnson", "handler")
SARAH = ("Sarah Williams", "handler")
DAVID = ("David Bline", "supervisor")

SERVER_ROOT = Path(__file__).resolve().parents[1]
CASE_FILE_SEED = SERVER_ROOT / "data" / "seed" / "case_file_seed.json"
FORMS_SEED = SERVER_ROOT / "data" / "seed" / "path_required_forms.json"


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


def expected_path(claim: dict[str, Any]) -> ClaimPath:
    """The claim's path, from the seed row, through the rule under test's twin.

    `DEPLOYED` is `test_path_classification.py`'s hand-built derivation over
    thresholds restated from the story — not the block the server loaded — so
    this stays an oracle rather than an echo of the response.
    """
    return DEPLOYED.of(
        severity_score=claim["severity_score"],
        disability=Disability(claim["disability"]),
        recovery=RecoveryWindow(RECOVERY_TOKENS[claim["recovery"]]),
        surgery_required=claim["surgery_required"],
        return_status=ReturnStatus(claim["return_status"]),
    )


#: Migration 0013's mapping, restated — the seed file still holds the display
#: strings it converted from.
RECOVERY_TOKENS = {
    "0-2 Weeks": "weeks_0_2",
    "2-4 Weeks": "weeks_2_4",
    "4-6 Weeks": "weeks_4_6",
    "6-8 Weeks": "weeks_6_8",
    "Greater than 1 Year": "over_1_year",
}


def seeded_forms_for(path: ClaimPath) -> list[dict[str, Any]]:
    forms: list[dict[str, Any]] = json.loads(FORMS_SEED.read_text(encoding="utf-8"))
    return sorted(
        (form for form in forms if form["path"] == path.value),
        key=lambda form: form["sort_order"],
    )


def seeded_documents_for(claim_id: str) -> list[dict[str, Any]]:
    seed = json.loads(CASE_FILE_SEED.read_text(encoding="utf-8"))
    return [row for row in seed["documents"] if row["claim_id"] == claim_id]


def claims_by_path(persona: tuple[str, str]) -> dict[ClaimPath, dict[str, Any]]:
    """One seeded claim per path the persona's book actually contains."""
    found: dict[ClaimPath, dict[str, Any]] = {}
    for claim in sorted(seed_fixture.claims_for(*persona), key=lambda c: c["claim_id"]):
        found.setdefault(expected_path(claim), claim)
    return found


# --- AC 1: the forms card ------------------------------------------------


async def test_the_seeded_book_contains_both_a_path_a_and_a_path_b_claim() -> None:
    """The premise every per-path assertion below rests on.

    Stated as its own test so that a seed change which flattened the portfolio
    onto one path fails *here*, saying so, rather than by silently making three
    later tests assert the same case twice.

    **Path C is deliberately absent**, and that is a finding rather than a gap:
    the 2026 book is a live-claims manufacturing portfolio with no fatality
    indicator in its schema at all. See `test_no_seeded_claim_is_a_fatality`.
    """
    paths = claims_by_path(KAYA)

    assert ClaimPath.a in paths, "no minor claim in Kaya's book — the A card is untested"
    assert ClaimPath.b in paths


async def test_no_seeded_claim_is_classified_a_fatality() -> None:
    """The classification's most important negative, asserted over all 100.

    A Path C claim shows its handler the dependants' death-benefit claim, the
    proof of death and the burial-expense filing. The seeded book's seven
    `permanent` claims are all amputations and several of them returned to work
    fully recovered, so a cut-off low enough to make C fire against this data
    would be telling a handler that a living worker is dead.

    If this test ever fails, the right response is to look at the claim it
    names — not to raise the threshold.
    """
    misclassified = [
        claim["claim_id"]
        for claim in seed_fixture.seed()["claims"]
        if expected_path(claim) is ClaimPath.c
    ]

    assert not misclassified, (
        f"{misclassified} classify as fatalities; the seeded schema carries no "
        "fatality indicator, so this is a threshold problem rather than a data one"
    )


async def test_the_forms_card_carries_the_classified_paths_forms_in_order(
    seeded_db_url: str,
) -> None:
    """AC 1, and the closing of the prototype's always-Path-B gap.

    Asserted over *both* paths the book contains in one test, because the bug
    being prevented is precisely a response that looks right for one claim and
    is identical for every other.
    """
    for path, claim in claims_by_path(KAYA).items():
        payload = await detail_for(seeded_db_url, KAYA, claim["claim_id"])
        block = payload["documents"]

        assert block["path"] == path.value, claim["claim_id"]
        assert [form["formCode"] for form in block["requiredForms"]] == [
            form["form_code"] for form in seeded_forms_for(path)
        ]
        for sent, seeded in zip(block["requiredForms"], seeded_forms_for(path), strict=True):
            assert sent["formName"] == seeded["form_name"]
            assert sent["description"] == seeded["description"]
            assert sent["timing"] == seeded["timing"]
            assert sent["downloadUrl"] == seeded["download_url"]


async def test_two_claims_on_different_paths_get_different_form_sets(
    seeded_db_url: str,
) -> None:
    """The prototype's bug, stated as the assertion that would have caught it.

    `pathDocsHTML` renders `PATH_DOCS[c.path || "B"]` against a dataset with no
    `path` field, so every one of its 100 claims shows the same four forms. A
    build with that behaviour passes every single-claim test above.
    """
    paths = claims_by_path(KAYA)
    minor = await detail_for(seeded_db_url, KAYA, paths[ClaimPath.a]["claim_id"])
    ordinary = await detail_for(seeded_db_url, KAYA, paths[ClaimPath.b]["claim_id"])

    minor_codes = [f["formCode"] for f in minor["documents"]["requiredForms"]]
    ordinary_codes = [f["formCode"] for f in ordinary["documents"]["requiredForms"]]

    assert minor_codes != ordinary_codes
    assert not set(minor_codes) & set(ordinary_codes)


async def test_the_block_names_the_rule_document_that_classified(
    seeded_db_url: str,
) -> None:
    """ "Which rules produced this?" — the queue payload's precedent.

    The same document the header's risk band came from, so a response cannot
    report one version for the gauge and another for the banner.
    """
    claim = claims_by_path(KAYA)[ClaimPath.b]
    payload = await detail_for(seeded_db_url, KAYA, claim["claim_id"])

    assert payload["documents"]["pathVersion"] == payload["thresholdsVersion"]


async def test_no_path_label_icon_or_colour_crosses_the_wire(seeded_db_url: str) -> None:
    """The Enums convention, asserted on the payload rather than trusted.

    The prototype's `PATH_META` holds a label, an emoji and two hex colours per
    path. All four are the browser's; a server that started sending them would
    make the banner's wording a migration.
    """
    claim = claims_by_path(KAYA)[ClaimPath.b]
    payload = await detail_for(seeded_db_url, KAYA, claim["claim_id"])
    wire = json.dumps(payload["documents"])

    assert "Path B" not in wire
    assert "#" not in wire.replace("https://", "")  # no hex colour, no anchor
    assert "🔵" not in wire


# --- AC 3: the ID card and the documents list ----------------------------


async def test_the_id_card_reads_the_same_on_every_stage(seeded_db_url: str) -> None:
    """Why the card is a block of its own rather than stitched by the client.

    `employeeBusinessId` and `plant` live on the *intake* variant, so a client
    assembling this itself would render a different card once a claim moved on.
    Asserted across two stages, which is the only way to see it.
    """
    fields = {
        "employeeBusinessId",
        "workerName",
        "workerRole",
        "policyNum",
        "doi",
        "handlerName",
        "plant",
        "state",
        "region",
    }

    for stage in ("intake", "treatment", "settled"):
        claim = next(
            c
            for c in sorted(seed_fixture.claims_for(*KAYA), key=lambda c: c["claim_id"])
            if c["stage"] == stage
        )
        card = (await detail_for(seeded_db_url, KAYA, claim["claim_id"]))["documents"]["idCard"]

        assert set(card) == fields, stage
        assert card["policyNum"] == claim["policy_num"]
        assert card["doi"] == claim["doi"]
        assert card["plant"] == claim["plant"]
        assert card["state"] == claim["state"]
        assert card["region"] == claim["region"]
        assert all(str(value).strip() for value in card.values()), stage


async def test_the_documents_list_is_the_seeded_file_in_filing_order(
    seeded_db_url: str,
) -> None:
    claim = claims_by_path(KAYA)[ClaimPath.b]
    listed = (await detail_for(seeded_db_url, KAYA, claim["claim_id"]))["documents"]["documents"]
    seeded = seeded_documents_for(claim["claim_id"])

    assert [row["name"] for row in listed] == [row["name"] for row in seeded]
    assert [row["docType"] for row in listed] == [row["doc_type"] for row in seeded]
    assert [row["filedDate"] for row in listed] == [row["filed_date"] for row in seeded]
    # Each row is addressable — the viewer opens by this id, and a payload of
    # rows without one would be a list nothing could click.
    assert all(isinstance(row["id"], int) for row in listed)


async def test_every_seeded_claim_has_documents_so_the_empty_state_needs_a_fixture() -> None:
    """AC 5's premise, recorded rather than assumed.

    The empty state is unreachable against the dev seed — every claim carries
    between three and eight documents — which is *why* it is covered by a
    vitest fixture and by an e2e test that empties one claim's file. Asserting
    the premise here means a seed change that made it reachable shows up as a
    failing test rather than as a state nobody re-checked.
    """
    seed = json.loads(CASE_FILE_SEED.read_text(encoding="utf-8"))
    with_documents = {row["claim_id"] for row in seed["documents"]}
    all_claims = {claim["claim_id"] for claim in seed_fixture.seed()["claims"]}

    assert all_claims - with_documents == set()


# --- AC 4: the viewer sheets ---------------------------------------------


async def sheet_for(
    db_url: str, persona: tuple[str, str], claim_id: str, document_id: int
) -> httpx.Response:
    async with make_client(db_url) as client:
        await login_as(client, *persona)
        return await client.get(f"/claims/{claim_id}/documents/{document_id}/content")


async def documents_of(db_url: str, claim_id: str) -> list[dict[str, Any]]:
    payload = await detail_for(db_url, KAYA, claim_id)
    documents: list[dict[str, Any]] = payload["documents"]["documents"]
    return documents


async def test_a_froi_renders_the_full_injury_sheet(seeded_db_url: str) -> None:
    """The prototype's `openDoc` FROI branch, as a payload.

    Every claim has exactly one FROI, so the row is found by type rather than
    by position — a test that indexed `documents[0]` would be asserting the
    seed's order rather than the dispatch.
    """
    claim = claims_by_path(KAYA)[ClaimPath.b]
    froi = next(
        row
        for row in await documents_of(seeded_db_url, claim["claim_id"])
        if row["docType"] == "froi"
    )

    resp = await sheet_for(seeded_db_url, KAYA, claim["claim_id"], froi["id"])
    assert resp.status_code == 200, resp.text
    sheet = resp.json()

    assert sheet["sheetVariant"] == "froi"
    labels = [row["label"] for row in sheet["rows"]]
    assert labels[:4] == ["Claim ID", "Policy Number", "Employee", "Employer / Plant"]
    for expected in ("Date of Injury", "Injury Type", "Body Part", "ICD-10", "Cause", "Severity"):
        assert expected in labels
    assert sheet["signatures"] == ["Supervisor / Date", "Adjuster / Date"]
    # No bytes behind any seeded document — the boundary is wired, the store
    # is deferred.
    assert sheet["hasBlob"] is False
    assert sheet["blobUrl"] is None


async def test_every_other_type_renders_the_summary_sheet(seeded_db_url: str) -> None:
    claim = claims_by_path(KAYA)[ClaimPath.b]
    other = next(
        row
        for row in await documents_of(seeded_db_url, claim["claim_id"])
        if row["docType"] != "froi"
    )

    sheet = (await sheet_for(seeded_db_url, KAYA, claim["claim_id"], other["id"])).json()

    assert sheet["sheetVariant"] == "summary"
    assert [row["label"] for row in sheet["rows"]] == [
        "Claim ID",
        "Policy Number",
        "Employee",
        "Employer / Plant",
        "Filed",
        "Status",
        "Handler",
    ]
    # The distinction that makes the two variants worth having: a wage
    # statement does not carry the claimant's diagnosis.
    assert "ICD-10" not in {row["label"] for row in sheet["rows"]}


async def test_exactly_one_of_text_and_cents_is_set_on_every_row(
    seeded_db_url: str,
) -> None:
    """The money convention, held at the one place it could have broken.

    A FROI's average weekly wage is the only amount on either sheet, and it
    crosses as integer cents like every other figure in this contract rather
    than as a formatted string. `text` may legitimately be null (an undated
    filing), so the assertion is "never both", not "always one".
    """
    claim = claims_by_path(KAYA)[ClaimPath.b]
    froi = next(
        row
        for row in await documents_of(seeded_db_url, claim["claim_id"])
        if row["docType"] == "froi"
    )

    sheet = (await sheet_for(seeded_db_url, KAYA, claim["claim_id"], froi["id"])).json()

    money = [row for row in sheet["rows"] if row["cents"] is not None]
    assert [row["label"] for row in money] == ["AWW"]
    assert money[0]["cents"] == next(
        c["aww"] for c in seed_fixture.seed()["claims"] if c["claim_id"] == claim["claim_id"]
    )
    for row in sheet["rows"]:
        assert row["text"] is None or row["cents"] is None
    # And no formatted currency anywhere in the payload.
    assert "$" not in json.dumps(sheet)


async def test_the_sheet_omits_the_supervisor_row_the_prototype_prints(
    seeded_db_url: str,
) -> None:
    """Story 2.2's ruling, applied to a statutory filing.

    `openDoc` prints a Supervisor line; nothing persists a supervisor (Story
    1.2 deliberately did not seed one), so the prototype's own viewer renders
    `undefined` there. "A row that always reads '—' is furniture, not honesty"
    — and a filing is the last place to print a blank where a name goes.
    """
    claim = claims_by_path(KAYA)[ClaimPath.b]
    froi = next(
        row
        for row in await documents_of(seeded_db_url, claim["claim_id"])
        if row["docType"] == "froi"
    )

    sheet = (await sheet_for(seeded_db_url, KAYA, claim["claim_id"], froi["id"])).json()

    assert "Supervisor" not in {row["label"] for row in sheet["rows"]}


async def test_the_severity_row_carries_the_score_not_a_band(seeded_db_url: str) -> None:
    """AD-10, on the one surface most likely to reintroduce a second banding.

    The prototype prints its dataset's own `severity` display string
    ("High"/"Medium"/"Low") here — a third reading of `severity_score`, beside
    the gauge's and the queue dot's. A first report records what was assessed.
    """
    claim = claims_by_path(KAYA)[ClaimPath.b]
    froi = next(
        row
        for row in await documents_of(seeded_db_url, claim["claim_id"])
        if row["docType"] == "froi"
    )

    sheet = (await sheet_for(seeded_db_url, KAYA, claim["claim_id"], froi["id"])).json()
    severity = next(row for row in sheet["rows"] if row["label"] == "Severity")

    assert severity["text"] == str(claim["severity_score"])


# --- AD-7: what the viewer refuses ---------------------------------------


async def a_foreign_claim_and_one_of_its_own_documents(db_url: str) -> tuple[str, int]:
    """A claim outside Kaya's book, paired with a document that really is on it.

    **Both halves matter, and getting this wrong makes the AD-7 test vacuous**
    (code review, 2026-08-12). The first version of these tests paired a
    *foreign claim id* with a *local document id*, which the claim/document
    mismatch predicate refuses on its own — so `employer_scope` in
    `select_document` was never the reason for the 404 and could have been
    deleted with every test still green. The document is therefore read as
    Sarah, who owns it, and then asked for as Kaya, who does not: the only
    predicate that can refuse that pair is the employer filter.
    """
    claim = sorted(seed_fixture.claims_for(*SARAH), key=lambda c: c["claim_id"])[0]["claim_id"]
    async with make_client(db_url) as client:
        await login_as(client, *SARAH)
        resp = await client.get(f"/claims/{claim}")
        assert resp.status_code == 200, resp.text
        documents = resp.json()["documents"]["documents"]
    assert documents, f"{claim} has no documents to ask for"
    return claim, documents[0]["id"]


async def test_a_document_outside_the_callers_scope_is_a_404(seeded_db_url: str) -> None:
    """The employer filter on `select_document`, exercised on its own.

    Kaya and Sarah have disjoint books, so this pair — Sarah's claim, Sarah's
    document, Kaya asking — can only be refused by `employer_scope`. Verified
    by deleting that predicate from `claims.select_document` and watching this
    test go red while every other test in the file stayed green.
    """
    claim, document_id = await a_foreign_claim_and_one_of_its_own_documents(seeded_db_url)

    refused = await sheet_for(seeded_db_url, KAYA, claim, document_id)

    assert refused.status_code == 404


async def test_a_document_of_another_claim_does_not_resolve_through_this_one(
    seeded_db_url: str,
) -> None:
    """The claim id in the path is a predicate, not decoration.

    Both claims are in the caller's own book, so scope is not what refuses
    this — which is the point. Without the claim in the WHERE clause, every
    claim's URL would be a viewer for every document the caller can see.
    """
    mine = sorted(seed_fixture.claims_for(*KAYA), key=lambda c: c["claim_id"])
    first, second = mine[0]["claim_id"], mine[1]["claim_id"]
    others = await documents_of(seeded_db_url, second)

    refused = await sheet_for(seeded_db_url, KAYA, first, others[0]["id"])
    assert refused.status_code == 404


async def test_an_absent_document_id_answers_exactly_as_an_out_of_scope_one(
    seeded_db_url: str,
) -> None:
    """Surrogate ids are dense, so a distinguishable refusal is an oracle.

    A caller walking 1…10000 against a route that told "not yours" from "does
    not exist" would learn how many documents the portfolio holds and which
    ids are live. Both the status *and* the problem `type` are compared.
    """
    claim = claims_by_path(KAYA)[ClaimPath.b]["claim_id"]
    foreign_claim, foreign_document = await a_foreign_claim_and_one_of_its_own_documents(
        seeded_db_url
    )

    # All three refusals are asked *as Kaya*, which is what makes them
    # comparable: a caller can only learn from a difference between answers
    # they can both see (code review, 2026-08-12 — the out-of-scope leg used to
    # be asked as Sarah, about her own claim, which is not a refusal she could
    # ever compare with anything).
    absent = await sheet_for(seeded_db_url, KAYA, claim, 10_000_000)
    out_of_scope = await sheet_for(seeded_db_url, KAYA, foreign_claim, foreign_document)
    wrong_claim = await sheet_for(seeded_db_url, KAYA, claim, foreign_document)

    statuses = {absent.status_code, out_of_scope.status_code, wrong_claim.status_code}
    types = {absent.json()["type"], out_of_scope.json()["type"], wrong_claim.json()["type"]}
    assert statuses == {404}
    assert len(types) == 1


@pytest.mark.parametrize("role", [DAVID])
async def test_a_read_only_role_can_still_read_a_document_sheet(
    seeded_db_url: str, role: tuple[str, str]
) -> None:
    """Viewing is a read, and reads are gated by scope rather than by role.

    Worth asserting rather than assuming: the four write commands refuse a
    supervisor, and a viewer that had copied their role check would have hidden
    the case file's own documents from the person reviewing the file.
    """
    claim = claims_by_path(KAYA)[ClaimPath.b]["claim_id"]
    documents = await documents_of(seeded_db_url, claim)

    resp = await sheet_for(seeded_db_url, role, claim, documents[0]["id"])

    assert resp.status_code == 200, resp.text


async def test_the_viewer_route_is_not_a_write_path(seeded_db_url: str) -> None:
    """AC 4's "read-only", asserted at the contract rather than in the UI.

    A viewer that a later story quietly gave a PATCH would be a second write
    path for columns the four AD-4 commands own (AD-12). The route table is
    where that becomes checkable.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim = claims_by_path(KAYA)[ClaimPath.b]["claim_id"]
        documents = await documents_of(seeded_db_url, claim)
        url = f"/claims/{claim}/documents/{documents[0]['id']}/content"

        for method in ("post", "patch", "put", "delete"):
            resp = await getattr(client, method)(url)
            assert resp.status_code == 405, f"{method.upper()} {url} is not read-only"


async def test_an_intake_case_file_reads_its_documents_once(seeded_db_url: str) -> None:
    """One scoped query, two consumers (code review, 2026-08-12).

    The intake variant's checklist and Story 2.5's block are built from the
    same rows, and each used to fetch its own — so the *most-fetched payload in
    the console* ran an identical query twice for every intake claim. The case
    file is re-read after each of the four AD-4 commands and embedded in every
    409 body, so this is not a micro-optimisation of a cold path.

    Counted by watching the statements the engine actually executes, because
    the alternative — reading `detail.py` and believing it — is exactly what
    let the duplicate in.
    """
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    executed: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        executed.append(" ".join(statement.split()))

    intake = next(
        c
        for c in sorted(seed_fixture.claims_for(*KAYA), key=lambda c: c["claim_id"])
        if c["stage"] == "intake"
    )
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            user = (
                await session.scalars(
                    sa.select(AppUser).where(
                        AppUser.name == KAYA[0], AppUser.role == UserRole.handler
                    )
                )
            ).one()
            ctx = CallerContext(
                user_id=user.id,
                role=user.role,
                employer_ids=await employer_ids_for(session, user.id),
            )
            executed.clear()
            await claim_detail(session, ctx, intake["claim_id"])
    finally:
        await engine.dispose()

    document_reads = [
        statement
        for statement in executed
        if "FROM document" in statement and "JOIN claim" in statement
    ]
    assert len(document_reads) == 1, f"the document table was read {len(document_reads)} times"
