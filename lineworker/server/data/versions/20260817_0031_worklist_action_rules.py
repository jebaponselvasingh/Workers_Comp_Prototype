"""`worklist_actions` v1 (Story 3.5, AD-8).

One document, one key, thirteen parameters: the checklist's `cap`, its
`paddingFloor`, and one urgency per trigger rule. A document of its own for
0025's reason applied one story later — `services/worklist` owns the action
generator, so its tunables get a key of their own rather than being folded into
`derivation_thresholds`, which is the single argument every *registered
derivation* is built from.

**No `derivation_thresholds` bump.** Nothing in Story 3.5 parameterises a
registered derivation: the generator *consumes* three of them (`siu_review`,
`rtw_blocked`, `payment_due`) exactly as they are and adds no threshold of its
own. Leaving that document alone also leaves every outstanding queue cursor
valid, which is the right outcome when no ranking input has moved — 0025's
argument, and it holds again.

**Effective from v1's date**, under the rule 0012 wrote down and 0024 and 0025
restated: a document with no earlier version has nothing to supersede and must
be effective the moment the code that reads it ships. `WorklistActions.of`
requires all thirteen parameters and the actions endpoint loads them on every
read, so a gap between this migration and the effective date would 500 the
card rather than blank it.

Revision ID: 0031_worklist_action_rules
Revises: 0030_action_checklist_columns
Create Date: 2026-08-17

"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0031_worklist_action_rules"
down_revision: str | None = "0030_action_checklist_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENTS_DIR = Path(__file__).resolve().parents[2] / "rules" / "documents"

# Spelled out rather than globbed, for 0009's reason: a file dropped into the
# directory must not become a live rule without a migration saying so.
ROWS = (("worklist_actions", 1, "worklist_actions.jdm.json"),)

# Every rule document's v1 date, deliberately — see the module docstring.
EFFECTIVE_FROM = date(2026, 8, 11)


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
    op.execute("DELETE FROM rule_document WHERE key = 'worklist_actions' AND version = 1")
