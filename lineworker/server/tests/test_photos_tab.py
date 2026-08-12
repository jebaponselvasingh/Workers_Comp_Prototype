"""Story 2.6 — the Photos tab's payload, over a real database.

Driven through the app for `test_documents_tab.py`'s reason: what this story
promises is a *contract* — a grid whose cards are the claim's own photos in the
order they were filed, a count that cannot disagree with them, and an honest
answer about bytes that do not exist yet.

Expectations come from `data/seed/photos.json`, the same file the migration
loaded. Nothing here imports the assembly under test to decide what the
assembly should produce.

The `BlobStore` half is unit-tested rather than driven through the app,
deliberately: no seeded row has a `blob_key`, so the only way to exercise the
resolution is to build a row that does — and the interesting case is the one a
volume-backed deployment produces, where the bytes exist and `url()` still
answers `None`.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from api import create_app
from config import Settings
from data.models.core import Photo
from services.blobstore import BlobStore
from services.claims.photos import photos_block
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

KAYA = ("Kaya Johnson", "handler")
SARAH = ("Sarah Williams", "handler")
DAVID = ("David Bline", "supervisor")

SERVER_ROOT = Path(__file__).resolve().parents[1]
PHOTOS_SEED = SERVER_ROOT / "data" / "seed" / "photos.json"


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


def seeded_photos_for(claim_id: str) -> list[dict[str, Any]]:
    """One claim's photos, in the order the migration filed them."""
    rows: list[dict[str, Any]] = json.loads(PHOTOS_SEED.read_text(encoding="utf-8"))
    return [row for row in rows if row["claim_id"] == claim_id]


def a_claim_of(payload_source: dict[str, Any]) -> str:
    claim_id: str = payload_source["claimId"]
    return claim_id


def a_claim_in_stage(persona: tuple[str, str], stage: str) -> str:
    """The persona's lowest claim id in one stage, from the seed."""
    claims = sorted(
        str(claim["claim_id"])
        for claim in seed_fixture.claims_for(*persona)
        if claim["stage"] == stage
    )
    assert claims, f"no seeded {stage} claim for {persona}"
    return claims[0]


def a_claim_outside(persona: tuple[str, str], other: tuple[str, str]) -> str:
    """A claim in `other`'s book and not in `persona`'s — computed, not assumed.

    Story 2.5's code review turned on exactly this distinction: a test that
    *believes* it is asking across a scope boundary and is not asserts nothing
    about scope. The set difference is what makes the premise true rather than
    plausible.
    """
    mine = {claim["claim_id"] for claim in seed_fixture.claims_for(*persona)}
    theirs = sorted(
        str(claim["claim_id"])
        for claim in seed_fixture.claims_for(*other)
        if claim["claim_id"] not in mine
    )
    assert theirs, f"{other} has no claim outside {persona}'s book"
    return theirs[0]


def a_claim_in(persona: tuple[str, str]) -> str:
    """The persona's lowest claim id — read from the seed, not typed in.

    Story 1.4's rule: a hardcoded `WC-20017` says nothing about *why* that
    claim, and is wrong the day the personas' books are re-cut (which is
    exactly how the first run of this file failed — that claim is not Kaya's).
    """
    claims = sorted(str(claim["claim_id"]) for claim in seed_fixture.claims_for(*persona))
    assert claims, f"no seeded claims for {persona}"
    return claims[0]


# --- AC 1: the grid, and the count that cannot disagree with it ----------


async def test_the_case_file_carries_the_claims_photos_in_filing_order(
    seeded_db_url: str,
) -> None:
    """The grid's cards, compared against the seed file card by card.

    Order included: `photo` has no total order in its own columns — captions
    repeat across the book and two photos of one claim routinely share a
    source — so filing order *is* identity order, and a comparison of sets
    would let a shuffled claim ship.
    """
    claim_id = a_claim_in(KAYA)
    payload = await detail_for(seeded_db_url, KAYA, claim_id)
    expected = seeded_photos_for(claim_id)
    assert expected, "the fixture claim must have photos"

    assert [(photo["caption"], photo["source"]) for photo in payload["photos"]["photos"]] == [
        (photo["caption"], photo["source"]) for photo in expected
    ]


async def test_the_count_is_the_servers_and_agrees_with_the_rows(seeded_db_url: str) -> None:
    """AC 1 — the tab label's number comes from the payload, not from a length.

    The story is explicit that the count is published rather than computed in
    the browser, and the reason is not tidiness: the tab bar and the grid are
    different components, and a label counting a list it does not hold is a
    second answer to "how many photos does this claim have". Asserted from both
    ends — against the rows in the same payload, and against the seed file — so
    a server that shipped a `count` of its own invention fails here.
    """
    claim_id = a_claim_in(KAYA)
    payload = await detail_for(seeded_db_url, KAYA, claim_id)
    block = payload["photos"]

    assert block["count"] == len(block["photos"])
    assert block["count"] == len(seeded_photos_for(claim_id))


async def test_each_card_carries_an_id_a_caption_and_a_source(seeded_db_url: str) -> None:
    """What a card renders, and nothing else.

    `id` is on the contract because the viewer addresses a photo by it — the
    prototype's `openPhoto(c, i)` takes an *array index*, which is a handle
    that changes meaning the moment anything is filed or removed.
    """
    payload = await detail_for(seeded_db_url, KAYA, a_claim_in(KAYA))

    for card in payload["photos"]["photos"]:
        assert isinstance(card["id"], int)
        assert card["caption"].strip()
        assert card["source"].strip()

    ids = [card["id"] for card in payload["photos"]["photos"]]
    assert len(set(ids)) == len(ids), "two cards share an id — the viewer cannot address them"


async def test_the_photos_block_is_outside_the_stage_variant(seeded_db_url: str) -> None:
    """A claim does not stop having evidence when it settles.

    `injury` (2.4) and `documents` (2.5) sit outside the discriminated union
    for this reason and `photos` joins them: putting the block on a variant
    would mean four copies or a tab that emptied itself at settlement.
    Asserted across all four stages rather than argued in a docstring — the
    claims are picked from the seed file by stage, so the test cannot be
    satisfied by a payload that decided its own stage.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *DAVID)

        for stage in ("intake", "investigation", "treatment", "settled"):
            claim_id = a_claim_in_stage(DAVID, stage)
            payload = (await client.get(f"/claims/{claim_id}")).json()

            assert payload["overview"]["stageVariant"] == stage
            assert payload["photos"]["count"] == len(seeded_photos_for(claim_id))
            assert payload["photos"]["count"] > 0


# --- AC 1: scope ---------------------------------------------------------


async def test_a_supervisor_sees_a_claims_photos(seeded_db_url: str) -> None:
    """Viewing is a read, and reads are gated by scope, not by role.

    Worth its own test rather than an assumption (Story 2.5's lesson from the
    document sheet): a block that had copied a write command's handler-only
    check would hide the case file's evidence from the person reviewing it.
    """
    claim_id = a_claim_in(DAVID)
    payload = await detail_for(seeded_db_url, DAVID, claim_id)
    assert payload["photos"]["count"] == len(seeded_photos_for(claim_id))


async def test_a_claim_outside_the_callers_book_exposes_no_photos(seeded_db_url: str) -> None:
    """AD-7 — the block rides the scoped claim path and nothing else.

    There is no photo route of its own, so the refusal is the case file's 404;
    the point of asserting it here is that adding a block to a payload must not
    open a second way in. (`test_scoped_repository.py` pins the repository-level
    guarantee, where this module's "every query applies the filter" invariant
    lives.)
    """
    hers = a_claim_outside(KAYA, SARAH)

    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        refused = await client.get(f"/claims/{hers}")

    assert refused.status_code == 404
    assert "photos" not in refused.json()


# --- AC 1: the bytes that are not there yet ------------------------------


async def test_no_seeded_photo_claims_to_have_bytes_behind_it(seeded_db_url: str) -> None:
    """The API signals "no binary", and the UI's placeholder is the designed state.

    All 293 seeded rows have a null `blob_key` because the prototype has no
    image files. `hasBlob: false` with `blobUrl: null` is what says so — the
    alternative, a `blobUrl` pointing at something that cannot answer, renders a
    broken image where the console currently renders a deliberate placeholder.
    """
    payload = await detail_for(seeded_db_url, KAYA, a_claim_in(KAYA))

    for card in payload["photos"]["photos"]:
        assert card["hasBlob"] is False
        assert card["blobUrl"] is None


class _PresigningStore:
    """A `BlobStore` that mints links — the MinIO half of the deferred decision."""

    def put(self, key: str, data: bytes, *, content_type: str) -> None: ...  # pragma: no cover

    def get(self, key: str) -> bytes:  # pragma: no cover
        raise AssertionError("the block must not read bytes to build a card")

    def delete(self, key: str) -> None: ...  # pragma: no cover

    def url(self, key: str) -> str | None:
        return f"https://blobs.example/{key}"


class _VolumeStore:
    """A `BlobStore` that cannot mint links — the mounted-volume half."""

    def put(self, key: str, data: bytes, *, content_type: str) -> None: ...  # pragma: no cover

    def get(self, key: str) -> bytes:  # pragma: no cover
        raise AssertionError("the block must not read bytes to build a card")

    def delete(self, key: str) -> None: ...  # pragma: no cover

    def url(self, key: str) -> str | None:
        return None


def _photo(photo_id: int, blob_key: str | None) -> Photo:
    row = Photo()
    row.id = photo_id
    row.caption = "Machine guard / interlock mechanism detail"
    row.source = "Plant safety, 03/22"
    row.blob_key = blob_key
    return row


def test_a_photo_with_a_key_resolves_its_url_through_the_store() -> None:
    """The protocol boundary, exercised rather than merely declared.

    `_PresigningStore` refuses `get`, which is the assertion that matters as
    much as the URL: assembling a card must not pull bytes through the API
    container for a grid of thumbnails.
    """
    block = photos_block([_photo(1, "photo-1.jpg")], store=_PresigningStore())

    assert block.photos[0].has_blob is True
    assert block.photos[0].blob_url == "https://blobs.example/photo-1.jpg"


def test_a_volume_backed_photo_has_bytes_and_no_url() -> None:
    """`url()` answering `None` is a normal answer, not an absence of the file.

    This is the case the `BlobStore` docstring warns callers about, and it is
    the reason `hasBlob` is published beside `blobUrl` instead of being inferred
    from it: a UI that read `blobUrl === null` as "no photo" would show the
    placeholder for every photo in a volume-backed deployment.
    """
    block = photos_block([_photo(1, "photo-1.jpg")], store=_VolumeStore())

    assert block.photos[0].has_blob is True
    assert block.photos[0].blob_url is None


def test_without_a_store_a_keyed_photo_still_reports_that_it_has_bytes() -> None:
    """No store configured is not the same fact as no file.

    `claim_detail` passes no store today — nothing is configured, because the
    backend decision is Deferred — and the honest answer for a row that has a
    key is "there are bytes, I cannot address them from here".
    """
    block = photos_block([_photo(1, "photo-1.jpg")])

    assert block.photos[0].has_blob is True
    assert block.photos[0].blob_url is None


def test_the_store_is_never_asked_about_a_photo_with_no_key() -> None:
    """A null key is not a key, and must not reach the store.

    `VolumeBlobStore` refuses a key that is not a flat name — including the
    empty one — so a block that passed `None` through would turn all 293 seeded
    rows into a `BlobStoreError` the day a store is wired in.
    """

    class _Refusing:
        def put(self, key: str, data: bytes, *, content_type: str) -> None: ...  # pragma: no cover

        def get(self, key: str) -> bytes:  # pragma: no cover
            raise AssertionError("unreachable")

        def delete(self, key: str) -> None: ...  # pragma: no cover

        def url(self, key: str) -> str | None:
            raise AssertionError(f"the store was asked about {key!r} for a keyless photo")

    store: BlobStore = _Refusing()
    block = photos_block([_photo(1, None)], store=store)

    assert block.photos[0].has_blob is False
    assert block.photos[0].blob_url is None


def test_the_count_is_the_number_of_cards_by_construction() -> None:
    """The one invariant the tab label depends on, asserted at the unit.

    The API test above compares the count with the seed file; this one says the
    two can never come apart in the first place, whatever rows are handed in.
    """
    for size in (0, 1, 4):
        block = photos_block([_photo(index, None) for index in range(size)])
        assert block.count == size == len(block.photos)


# --- AC 3: the empty state's premise --------------------------------------


def test_every_claim_in_the_portfolio_has_photos_so_the_empty_state_needs_a_fixture() -> None:
    """AC 3 is unreachable against the dev seed, and this is where it is stated.

    Compared across the *two* seed files rather than within one: `photos.json`
    knowing about 100 claims is only the premise if `seed_data.json` has exactly
    those 100. A claim added to the portfolio without photos would make the
    empty state reachable — which is a legitimate change, but one that has to
    be noticed, because the vitest fixture and the e2e test that *produce* the
    state would otherwise start looking like overkill nobody could justify.
    """
    photographed = {photo["claim_id"] for photo in json.loads(PHOTOS_SEED.read_text("utf-8"))}
    portfolio = {claim["claim_id"] for claim in seed_fixture.seed()["claims"]}

    assert portfolio - photographed == set(), "a seeded claim has no photos"


# --- The wire carries data, not decoration -------------------------------


async def test_no_thumbnail_placeholder_crosses_the_wire(seeded_db_url: str) -> None:
    """📷 is the UI's rendering of "no bytes", not a field.

    The prototype writes the emoji into `photosHTML` and again into `openPhoto`.
    The Enums convention keeps display metadata in the browser — the same
    argument that kept `PATH_META`'s labels and hex colours out of the statutory
    form seed in Story 2.5 — and a placeholder glyph on the wire would be a
    server deciding what an absent image looks like.
    """
    payload = await detail_for(seeded_db_url, KAYA, a_claim_in(KAYA))

    assert "📷" not in json.dumps(payload, ensure_ascii=False)


@pytest.mark.parametrize("forbidden", ["POST", "PUT", "DELETE"])
async def test_the_case_file_route_has_no_write_verb_for_photos(
    seeded_db_url: str, forbidden: str
) -> None:
    """Read-only asserted structurally, as Story 2.5 asserted it for the sheet.

    There is no photo route at all — the block rides the case file — so the
    thing to pin is that no verb was quietly added to the endpoint that serves
    it.

    **PATCH is deliberately not in this list**, and the omission is the finding
    rather than a hole: Story 2.3 owns `PATCH /claims/{id}` for the six inline
    fields, and its `extra="forbid"` body is what keeps a photo field out of it.
    That is asserted where that command's whitelist lives; here the claim is
    only that no *new* verb appeared.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        resp = await client.request(forbidden, f"/claims/{a_claim_in(KAYA)}")

    assert resp.status_code == 405
