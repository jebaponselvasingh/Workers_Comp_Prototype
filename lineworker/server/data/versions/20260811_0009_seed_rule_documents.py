"""Version 1 of the two JDM documents Story 2.1 introduces (AD-8).

Both are read from the committed files under `rules/documents/` rather than
inlined here, the same pattern 0004 and 0007 use for the portfolio and the
glossary. That is the whole point of the arrangement: the file a reviewer
reads in the diff and the row the server evaluates are the same bytes, so
they cannot drift — and `tests/test_rules_engine.py` asserts exactly that
against a migrated database.

`effective_from` is the story's date, not `CURRENT_DATE`: a migration run
next year must produce the same row it produced today, and "when did this
rule take over?" has to survive a re-migration to be worth recording.

Superseding either document later is a *new row* (version 2, its own
migration, its own effective date) — never an edit to this one. Nothing in
the application can write here; the app role holds SELECT only (0008).

Revision ID: 0009_seed_rule_documents
Revises: 0008_rule_document
Create Date: 2026-08-11

"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0009_seed_rule_documents"
down_revision: str | None = "0008_rule_document"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENTS_DIR = Path(__file__).resolve().parents[2] / "rules" / "documents"

# The keys this revision seeds. Named here rather than globbed: a file
# dropped into the directory should not become a live rule without a
# migration saying so.
KEYS = ("derivation_thresholds", "priority_weights")

EFFECTIVE_FROM = date(2026, 8, 11)


def upgrade() -> None:
    rows = []
    for key in KEYS:
        path = DOCUMENTS_DIR / f"{key}.jdm.json"
        # A shape check before the insert, for 0007's reason: without one, a
        # missing or truncated document reaches the database as a NOT NULL
        # violation or an opaque bind-parameter error, and leaves the
        # migration halted with an empty rules table — which the loader then
        # reports as "no such rule" at the first queue request, a long way
        # from the actual cause.
        if not path.is_file():
            raise ValueError(f"{path} is missing — the rule document and this migration disagree")
        content = json.loads(path.read_text(encoding="utf-8"))
        if not content.get("nodes"):
            raise ValueError(f"{path} declares no nodes — it cannot be evaluated")
        rows.append(
            {
                "key": key,
                "version": 1,
                "effective_from": EFFECTIVE_FROM,
                "content": content,
                "created_at": datetime.now(UTC),
            }
        )

    bind = op.get_bind()
    meta = sa.MetaData()
    rule_document = sa.Table("rule_document", meta, autoload_with=bind)
    bind.execute(rule_document.insert(), rows)


def downgrade() -> None:
    op.execute(
        "DELETE FROM rule_document WHERE version = 1 AND key IN "
        "('derivation_thresholds', 'priority_weights')"
    )
