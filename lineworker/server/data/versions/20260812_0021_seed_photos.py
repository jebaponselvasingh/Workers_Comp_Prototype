"""Seed the incident photos from the prototype (Story 2.6, AC 1).

Loads `data/seed/photos.json` — 293 photos across all 100 claims, extracted
from the prototype's per-claim `photos` arrays by
`data/seed/extract_prototype_photos.py` (AD-12). Captions and source lines are
copied verbatim: they are *content*, not code, so nothing here trims, re-cases
or re-words what a plant safety officer wrote.

**Insertion order is the contract**, as it is for 0011's timeline and
documents. `photo` has no other total order — captions repeat across the book
and two photos of one claim routinely share a source — so the grid's display
order is the identity column's order, and the executemany below takes the list
as-is. Nothing here sorts or groups it.

**Claims are resolved by business id.** The seed file names `WC-nnnn`; the
foreign key wants the surrogate. Resolving here rather than in the extractor
keeps the seed file readable and independent of whatever identity values a
particular database handed out (0011's rule).

**No `blob_key` is inserted.** There are no image files in the prototype, so
every row's key stays null and the tab renders its placeholder treatment — the
designed state, not a degradation. A seeded key would make the API answer
`hasBlob: true` for content no store can serve.

Revision ID: 0021_seed_photos
Revises: 0020_photo
Create Date: 2026-08-12

"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0021_seed_photos"
down_revision: str | None = "0020_photo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_PATH = Path(__file__).parent.parent / "seed" / "photos.json"

COLUMNS = {"claim_id", "caption", "source"}

#: How many rows must arrive, and how many claims they must cover. Literals for
#: 0009's reason about globbing: an extraction that lost a claim's photos to a
#: pattern change would otherwise migrate cleanly and leave a handler reading an
#: empty evidence grid as "nothing was photographed".
EXPECTED_PHOTOS = 293
EXPECTED_CLAIMS = 100


def upgrade() -> None:
    photos: list[dict[str, Any]] = json.loads(SEED_PATH.read_text(encoding="utf-8"))

    # A shape check before the insert, because the failure without one is
    # unreadable: a drifted extractor output reaches the database as a NOT NULL
    # violation or a CompileError about a bind parameter, and leaves the chain
    # halted with the table created and empty — which the API then serves as a
    # successful, photo-less case file (0007's argument).
    if not photos:
        raise ValueError(f"{SEED_PATH} is empty — re-run data/seed/extract_prototype_photos.py")
    # Every row, not just the first (0011's code-review lesson): `executemany`
    # compiles its statement from row 0, so drift that begins further down is
    # either silently dropped or dies as a bind-parameter error halfway through.
    for index, photo in enumerate(photos):
        if set(photo) != COLUMNS:
            raise ValueError(
                f"{SEED_PATH}[{index}] has fields {sorted(photo)}, expected "
                f"{sorted(COLUMNS)} — the extractor and this migration have drifted apart"
            )

    covered = {photo["claim_id"] for photo in photos}
    if len(photos) != EXPECTED_PHOTOS or len(covered) != EXPECTED_CLAIMS:
        raise ValueError(
            f"{SEED_PATH} holds {len(photos)} photos across {len(covered)} claims, "
            f"expected {EXPECTED_PHOTOS} across {EXPECTED_CLAIMS} — re-run "
            "data/seed/extract_prototype_photos.py against the prototype"
        )

    bind = op.get_bind()
    meta = sa.MetaData()
    claim = sa.Table("claim", meta, autoload_with=bind)
    photo_table = sa.Table("photo", meta, autoload_with=bind)

    claim_ids = {
        business_id: surrogate
        for surrogate, business_id in bind.execute(sa.select(claim.c.id, claim.c.claim_id))
    }

    rows = []
    for row in photos:
        surrogate = claim_ids.get(row["claim_id"])
        if surrogate is None:
            raise ValueError(
                f"{SEED_PATH} names claim {row['claim_id']!r}, which is not in the "
                "seeded portfolio — the two seed files have drifted apart"
            )
        rows.append({**row, "claim_id": surrogate})

    bind.execute(photo_table.insert(), rows)


def downgrade() -> None:
    op.execute("DELETE FROM photo")
