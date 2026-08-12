"""`derivation_thresholds` v3 — the claim-path parameters (Story 2.5, AD-8).

Version 2's nine parameters unchanged, plus the three
`services/derivations/path_classification.py` reads. A *new row*, never an edit to
0012's, for the reason that migration gives at length: v2 is what Story 2.2
shipped, and a banner or an ordering produced under it stays explainable only
while the document that produced it still says what it said. The v3 content
lives in `derivation_thresholds.v3.jdm.json` under the naming convention 0012
established.

**In this document rather than one of its own.** `injury_capture` (0016) and
`intake_required_documents` (0012) are separate documents because they
parameterise `services/claims` *commands*; `claim_path` is a **registered
derivation** (AD-10), and `DerivationThresholds` is the single argument every
registered derivation is built from. Splitting it out would have meant a
second loader call on the case-file path to answer one classification.

**It takes v2's effective date, not its own**, under the rule 0012 wrote down:
future-date a version freely when it only changes values; give it the
superseded version's date when it adds a parameter the typed block *requires*.
All three of these are required by `DerivationThresholds.of`, and that block
feeds the queue, `/stats/topbar` and the case file — so a gap between the
migration and the effective date would 500 the whole console, not just this
story's card.

Bumping the document invalidates outstanding queue cursors, by design
(`services/worklist/queue.py` refuses a page cut under a superseded ranking).
That is correct here even though no path parameter enters a score: the cursor
records *which document* produced the ordering, and "v2 and v3 happen to rank
identically" is a fact about today's values rather than a property of the
version.

Revision ID: 0019_claim_path_rules
Revises: 0018_seed_path_forms
Create Date: 2026-08-12

"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0019_claim_path_rules"
down_revision: str | None = "0018_seed_path_forms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENTS_DIR = Path(__file__).resolve().parents[2] / "rules" / "documents"

# Spelled out rather than globbed, for 0009's reason: a file dropped into the
# directory must not become a live rule without a migration saying so.
ROWS = (("derivation_thresholds", 3, "derivation_thresholds.v3.jdm.json"),)

# v1's and v2's date, deliberately — see the module docstring.
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
    op.execute("DELETE FROM rule_document WHERE key = 'derivation_thresholds' AND version = 3")
