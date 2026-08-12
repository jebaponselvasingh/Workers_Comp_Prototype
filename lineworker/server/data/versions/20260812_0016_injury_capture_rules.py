"""`injury_capture` v1 — the add-injury form's parameters (Story 2.4, AD-8).

One row, one parameter: the severity score a newly captured secondary injury
starts at. A document of its own rather than a tenth key in
`derivation_thresholds`, for the reason 0012 gave `intake_required_documents`
— that block is the single argument every *registered derivation* is built
from, and this number is a `services/claims` command's default, not a
derivation's input.

`effective_from` is the story's date, not `CURRENT_DATE`, so a re-migration
next year produces the same row (0009's and 0012's rule).

Revision ID: 0016_injury_capture_rules
Revises: 0015_seed_injury_diagram
Create Date: 2026-08-12

"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0016_injury_capture_rules"
down_revision: str | None = "0015_seed_injury_diagram"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENTS_DIR = Path(__file__).resolve().parents[2] / "rules" / "documents"

# Spelled out rather than globbed, for 0009's reason: a file dropped into the
# directory must not become a live rule without a migration saying so.
ROWS = (("injury_capture", 1, "injury_capture.jdm.json"),)

EFFECTIVE_FROM = date(2026, 8, 12)


def upgrade() -> None:
    rows = []
    for key, version, filename in ROWS:
        path = DOCUMENTS_DIR / filename
        if not path.is_file():
            raise ValueError(f"{path} is missing — the rule document and this migration disagree")
        content = json.loads(path.read_text(encoding="utf-8"))
        if not content.get("nodes"):
            raise ValueError(f"{path} declares no nodes — it cannot be evaluated")
        rows.append(
            {
                "key": key,
                "version": version,
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
    op.execute("DELETE FROM rule_document WHERE key = 'injury_capture' AND version = 1")
