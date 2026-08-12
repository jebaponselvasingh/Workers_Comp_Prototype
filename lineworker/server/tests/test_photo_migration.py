"""Story 2.6 AC 1 — `photo`, from the prototype to the database.

Three separable claims, and the file is organised along them:

1. The **rows** are the prototype's. Asserted against
   `docs/Workers_Comp_Prototype.html` itself rather than against `photos.json`,
   so the extractor is under test too — a comparison with its own output would
   pass however wrong it was (Story 2.5's rule for `path_required_form`).
2. The **order** is the prototype's array order, carried by the identity
   column rather than by a `sort_order`. `photo` follows `document` and
   `timeline_event` here, not `path_required_form`: those two are per-claim
   child rows whose display order *is* their insertion order, and the forms
   table needed an explicit number only because three independent reference
   lists share one table.
3. The **grants** are SELECT and nothing else. No story in Epics 1–8 uploads,
   annotates or deletes a photo, so the seed migration is the table's only
   writer (AD-12) — and a grant that said otherwise would be a capability
   nobody asked for behind an endpoint that cannot use it.

Only the third needs a database, so the first two run everywhere.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from tests.conftest import requires_db

SERVER_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = SERVER_ROOT / "data" / "seed" / "photos.json"
PROTOTYPE = SERVER_ROOT.parents[1] / "docs" / "Workers_Comp_Prototype.html"

#: What the prototype's per-claim `photos` arrays hold: `{t, d}`, a caption and
#: a source line. Read rather than restated so a caption it spells differently
#: fails here rather than being accepted from the extractor's output.
_PROTOTYPE_PHOTO = re.compile(r'\{"t":"((?:[^"\\]|\\.)*)","d":"((?:[^"\\]|\\.)*)"\}')

#: The whole book carries photos, and none carries more than four. Written down
#: because both halves are load-bearing: the first says the empty state is
#: unreachable against the seed (so AC 3 needs a fixture rather than a claim),
#: and the second is what makes a three-column grid the right shape.
EXPECTED_TOTAL = 293
EXPECTED_PER_CLAIM_RANGE = (2, 4)


def seeded_photos() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return rows


def prototype_photos() -> dict[str, list[tuple[str, str]]]:
    """`{claim id: [(caption, source)]}`, parsed from the prototype's `ALL_CLAIMS`.

    A second, independently written reading of the same literal the extractor
    parses — deliberately *not* `json.loads` of the whole array (which is what
    the extractor does), but a split on the claim id followed by a regex over
    each claim's own `photos` block, so the two are unlikely to be wrong in the
    same way.
    """
    html = PROTOTYPE.read_text(encoding="utf-8")
    literal = re.search(r"const ALL_CLAIMS\s*=\s*(\[.*?\]);\s*\n", html, re.S)
    assert literal, "ALL_CLAIMS not found in the prototype"

    found: dict[str, list[tuple[str, str]]] = {}
    # Each chunk starts at a claim id and runs to the next one, so a `photos`
    # array can only be attributed to the claim it is written inside.
    chunks = re.split(r'"claimId":"(WC-\d+)"', literal.group(1))
    for claim_id, body in zip(chunks[1::2], chunks[2::2], strict=True):
        block = re.search(r'"photos":\[(.*?)\]', body, re.S)
        assert block, f"{claim_id} has no photos array"
        found[claim_id] = [
            (json.loads(f'"{caption}"'), json.loads(f'"{source}"'))
            for caption, source in _PROTOTYPE_PHOTO.findall(block.group(1))
        ]
    return found


# --- 1. the rows ---------------------------------------------------------


def test_the_seeded_photos_are_the_prototypes_photos_in_its_order() -> None:
    """The extraction, checked against the design contract itself.

    Per claim, and in order: the grid renders a claim's photos in the order the
    migration filed them, so comparing flat sets would let a shuffled claim
    ship. The caption *and* the source are compared, because the source line is
    what tells a handler whether they are looking at the plant's own record or
    an OSHA inspector's.
    """
    expected = prototype_photos()

    seeded: dict[str, list[tuple[str, str]]] = {}
    for photo in seeded_photos():
        seeded.setdefault(photo["claim_id"], []).append((photo["caption"], photo["source"]))

    assert seeded == expected


def test_the_book_carries_the_number_of_photos_the_prototype_does() -> None:
    """A count the regex cannot quietly shrink.

    `EXPECTED_TOTAL` is a literal for 0009's reason about globbing: an
    extraction that lost a claim's photos to a pattern change would otherwise
    migrate cleanly, and the surface it would be wrong on is one a handler
    reads as "this is the evidence on file".
    """
    rows = seeded_photos()
    assert len(rows) == EXPECTED_TOTAL

    per_claim: dict[str, int] = {}
    for photo in rows:
        per_claim[photo["claim_id"]] = per_claim.get(photo["claim_id"], 0) + 1

    low, high = EXPECTED_PER_CLAIM_RANGE
    assert min(per_claim.values()) >= low
    assert max(per_claim.values()) <= high


def test_every_seeded_claim_has_photos_so_the_empty_state_needs_a_fixture() -> None:
    """AC 3's premise, asserted rather than assumed (Story 2.5's rule).

    Every claim in the book carries at least two photos, so "a claim with no
    photos" cannot be *found* in the dev seed — it has to be produced. The
    empty state is therefore covered by a vitest fixture and by an e2e test
    that empties one claim's evidence through `psqlQuery`. If a future seed
    change makes the state reachable, this test fails and says so, rather than
    leaving two deliberately synthetic tests looking like overkill.
    """
    claims_with_photos = {photo["claim_id"] for photo in seeded_photos()}
    assert len(claims_with_photos) == 100


def test_every_seeded_photo_carries_a_caption_and_a_source() -> None:
    """A blank caption renders a nameless card in a grid of nameless cards.

    Worth asserting separately from the comparison above: the extractor could
    match every entry and still emit an empty string, and the migration's own
    shape check only compares *key names*.
    """
    for photo in seeded_photos():
        for field in ("claim_id", "caption", "source"):
            assert photo[field].strip(), f"{photo['claim_id']} has an empty {field}"


def test_no_seeded_photo_claims_to_have_bytes_behind_it() -> None:
    """`blob_key` is absent from the seed file, and that is the honest shape.

    The prototype's thumbnails are the 📷 emoji — there are no image files
    anywhere in it. A seed that invented a key would make the API answer
    `hasBlob: true` for content no store can serve, which is a worse failure
    than the placeholder: the UI would render a broken image where it currently
    renders a designed state.
    """
    assert not any("blob_key" in photo for photo in seeded_photos())


# --- 2. the database -----------------------------------------------------


@pytest.fixture
def engine(seeded_db_url: str) -> Any:
    created = sa.create_engine(seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        yield created
    finally:
        created.dispose()


@requires_db
def test_the_seeded_rows_are_the_seed_files_rows_in_its_order(engine: Any) -> None:
    """Identity order is the contract, so the assertion reads the table by it.

    `ORDER BY id` rather than by anything in the row: the table has no other
    total order — captions repeat across claims and two photos of one claim can
    share a source — which is exactly why insertion order had to be preserved
    rather than reconstructed.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT c.claim_id, p.caption, p.source, p.blob_key "
                "FROM photo p JOIN claim c ON c.id = p.claim_id ORDER BY p.id"
            )
        ).mappings()
        stored = [dict(row) for row in rows]

    expected = [
        {
            "claim_id": photo["claim_id"],
            "caption": photo["caption"],
            "source": photo["source"],
            "blob_key": None,
        }
        for photo in seeded_photos()
    ]
    assert stored == expected


@requires_db
def test_a_photo_cannot_outlive_the_claim_it_documents(engine: Any) -> None:
    """The FK is real, asserted by asking for the failure.

    A photo row whose claim is gone is PHI with nothing to scope it by — AD-7
    filters on the claim's employer, so an orphan is a row no scope predicate
    can refuse. Epic 8's purge cascade depends on this constraint existing.
    """
    with engine.connect() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text("INSERT INTO photo (claim_id, caption, source) VALUES (0, 'x', 'y')")
            )
        conn.rollback()


@requires_db
def test_the_app_role_can_read_the_photos_and_cannot_write_them(engine: Any) -> None:
    """AD-4's grant statement, asserted rather than trusted.

    Nothing in Epics 1–8 uploads, annotates or removes a photo — the story is
    explicit that the viewer is read-only and that no write command exists — so
    the seed migration is the table's only writer. `path_required_form` and
    `glossary_term` set the precedent: the grant is where "read-only" stops
    being a claim in a docstring.
    """
    with engine.connect() as conn:
        granted = conn.execute(
            sa.text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_name = 'photo' AND grantee = 'lineworker_app'"
            )
        ).scalars()

        assert set(granted) == {"SELECT"}
