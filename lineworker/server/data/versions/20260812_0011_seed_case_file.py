"""Seed the case timeline and document rows (Story 2.2, AC 4).

Loads data/seed/case_file_seed.json — 556 timeline events and 563 documents
extracted from the prototype's `timeline`/`documents` arrays by
data/seed/extract_case_file_seed.py. Descriptions and document names are
copied verbatim: they are *content*, not code.

**Insertion order is the contract.** The seed file lists each claim's rows in
the prototype's array order, and both tables are inserted in file order so
the identity columns preserve it. `timeline_event` has no other total order —
`event_date` repeats within a claim and is null on every settlement event —
and the treatment overview renders the *last six* events, so a re-ordered
insert would quietly change which six a handler sees. The executemany below
therefore takes the list as-is; nothing here sorts or groups it.

**Claims are resolved by business id.** The seed file names `WC-nnnn`; the
foreign key wants the surrogate. Resolving here rather than in the extractor
keeps the seed file readable and independent of whatever identity values a
particular database handed out.

Revision ID: 0011_seed_case_file
Revises: 0010_case_file_tables
Create Date: 2026-08-12

"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0011_seed_case_file"
down_revision: str | None = "0010_case_file_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_PATH = Path(__file__).parent.parent / "seed" / "case_file_seed.json"

EVENT_FIELDS = {"claim_id", "event_date", "description", "tag"}
DOCUMENT_FIELDS = {"claim_id", "name", "doc_type", "filed_date"}


def _checked(rows: list[dict[str, Any]], label: str, fields: set[str]) -> list[dict[str, Any]]:
    """Refuse a drifted seed file with a sentence, not a bind-parameter error.

    Story 1.6's lesson: without this, a truncated or renamed extraction
    reaches the database as a `NOT NULL` violation or a `CompileError` about a
    bind parameter, halting the chain with the tables created and empty — a
    state the API happily serves as a claim with no history.
    """
    if not rows:
        raise ValueError(f"{SEED_PATH} has no {label} — re-run data/seed/extract_case_file_seed.py")
    # Every row, not just the first (code review, 2026-08-12). `executemany`
    # compiles its statement from row 0, so drift that begins further down
    # is either silently dropped or dies as a bind-parameter error halfway
    # through the chain — which is the failure this check exists to replace.
    for index, row in enumerate(rows):
        if set(row) != fields:
            raise ValueError(
                f"{SEED_PATH} {label}[{index}] has fields {sorted(row)}, "
                f"expected {sorted(fields)} — the extractor and this migration have drifted apart"
            )
    return rows


def upgrade() -> None:
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    events = _checked(seed["timeline_events"], "timeline_events", EVENT_FIELDS)
    documents = _checked(seed["documents"], "documents", DOCUMENT_FIELDS)

    bind = op.get_bind()
    meta = sa.MetaData()
    claim = sa.Table("claim", meta, autoload_with=bind)
    timeline_event = sa.Table("timeline_event", meta, autoload_with=bind)
    document = sa.Table("document", meta, autoload_with=bind)

    claim_ids = {
        business_id: surrogate
        for surrogate, business_id in bind.execute(sa.select(claim.c.id, claim.c.claim_id))
    }

    def resolved(rows: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
        out = []
        for row in rows:
            surrogate = claim_ids.get(row["claim_id"])
            if surrogate is None:
                raise ValueError(
                    f"{SEED_PATH} {label} names claim {row['claim_id']!r}, which is not "
                    "in the seeded portfolio — the two seed files have drifted apart"
                )
            out.append({**row, "claim_id": surrogate})
        return out

    bind.execute(timeline_event.insert(), resolved(events, "timeline_events"))
    bind.execute(document.insert(), resolved(documents, "documents"))


def downgrade() -> None:
    op.execute("DELETE FROM document")
    op.execute("DELETE FROM timeline_event")
