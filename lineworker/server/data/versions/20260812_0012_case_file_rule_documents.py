"""Rule documents for the case file (Story 2.2, AD-8).

Two rows:

- **`derivation_thresholds` v2** — version 1's five parameters unchanged plus
  the four `treatment_phase` reads. A *new row*, never an edit to 0009's, for
  the reason 0009's own docstring gives: v1 is what Story 2.1 shipped, and an
  ordering produced under it stays explainable only while the document that
  produced it still says what it said. That is also why the v2 content lives
  in its own file, `derivation_thresholds.v2.jdm.json` — 0009 reads the
  unversioned name at migration time, so editing that file would rewrite v1's
  content on every fresh database.

  **The naming convention this establishes:** the unversioned
  `<key>.jdm.json` is version 1, and every superseding version is authored as
  `<key>.v<N>.jdm.json`. One file per row, and a reviewer diffing a rule
  change sees a new file rather than a mutation of a shipped one.

  Bumping this document invalidates outstanding queue cursors, by design —
  `services/worklist/queue.py` refuses a page cut under a superseded
  ranking, and the thresholds decide `risk`, `siuReview` and `rtwBlocked`,
  which are inputs to every score.

- **`intake_required_documents` v1** — the intake checklist's baseline. A
  document of its own rather than four more parameters in the thresholds
  block: this is not a derivation parameter. `services/claims` owns the
  checklist, while `DerivationThresholds` is the single argument every
  registered derivation is built from (AD-10's registry contract).

`effective_from` is the story's date, not `CURRENT_DATE`, so a re-migration
next year produces the same rows.

**Why v2 shares v1's effective date rather than taking its own** (code
review, 2026-08-12). AD-8 advertises future-dating: "a rule change can be
migrated ahead of the day it applies". That is safe for a version that
*retunes* a parameter and unsafe for one that *adds a required* parameter,
because between the migration and the effective date the loader still
resolves the older document — and `DerivationThresholds.of` refuses it for
the keys it does not have. The failure is not local to this document: the
same block feeds the queue, `/stats/topbar` and the case file, so the whole
console 500s until the date arrives. Dating v2 from the day v1 took effect
closes the window by construction.

**The rule this sets for later versions:** future-date a version freely when
it only changes values; give it the superseded version's effective date when
it adds a parameter the typed block requires.

Revision ID: 0012_case_file_rule_documents
Revises: 0011_seed_case_file
Create Date: 2026-08-12

"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0012_case_file_rule_documents"
down_revision: str | None = "0011_seed_case_file"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENTS_DIR = Path(__file__).resolve().parents[2] / "rules" / "documents"

# (key, version, authoring file). Spelled out rather than globbed, for 0009's
# reason: a file dropped into the directory must not become a live rule
# without a migration saying so.
ROWS = (
    ("derivation_thresholds", 2, "derivation_thresholds.v2.jdm.json"),
    ("intake_required_documents", 1, "intake_required_documents.jdm.json"),
)

# v1's date, deliberately — see the module docstring.
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
    op.execute(
        "DELETE FROM rule_document WHERE "
        "(key = 'derivation_thresholds' AND version = 2) OR "
        "(key = 'intake_required_documents' AND version = 1)"
    )
