"""Seed the WC glossary from the prototype (Story 1.6, AC 1).

Loads data/seed/glossary_terms.json — 25 terms extracted from the
prototype's GLOSS array by data/seed/extract_prototype_glossary.py (AD-12).
The text is copied verbatim: it is *content*, not code, so nothing here
trims, title-cases or de-duplicates it.

Note the count. FR-GLOS-1 and the epics say "24 domain terms"; the
prototype — the design contract — has 25, and the contract file wins, the
same ruling Story 1.1 applied to the design tokens. Changing the set is a
change request, not a migration.

Revision ID: 0007_seed_glossary
Revises: 0006_glossary_term
Create Date: 2026-08-10

"""

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0007_seed_glossary"
down_revision: str | None = "0006_glossary_term"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_PATH = Path(__file__).parent.parent / "seed" / "glossary_terms.json"

COLUMNS = {"abbreviation", "term", "definition", "sort_order"}


def upgrade() -> None:
    terms = json.loads(SEED_PATH.read_text(encoding="utf-8"))

    # A shape check before the insert, because the failure without one is
    # unreadable. A drifted or truncated extractor output reaches the
    # database as a NOT NULL violation or a SQLAlchemy CompileError about a
    # bind parameter, and leaves the migration halted at 0006 with the table
    # created and empty — which the API then serves as a successful, empty
    # glossary. Naming the file in the message is the difference between
    # "regenerate the seed" and an afternoon in a stack trace.
    if not terms:
        raise ValueError(f"{SEED_PATH} is empty — re-run data/seed/extract_prototype_glossary.py")
    if set(terms[0]) != COLUMNS:
        raise ValueError(
            f"{SEED_PATH} rows have fields {sorted(terms[0])}, expected {sorted(COLUMNS)} — "
            "the extractor and this migration have drifted apart"
        )

    bind = op.get_bind()
    meta = sa.MetaData()
    glossary_term = sa.Table("glossary_term", meta, autoload_with=bind)

    # Rows carry the file's own sort_order rather than an insertion counter:
    # display order is a property of the data file a reviewer can read, not
    # of the order this loop happens to run in.
    bind.execute(glossary_term.insert(), terms)


def downgrade() -> None:
    op.execute("DELETE FROM glossary_term")
