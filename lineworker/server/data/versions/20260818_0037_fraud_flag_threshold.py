"""`derivation_thresholds` v5 — the dashboard's fraud-flag cut-off (Story 5.1, AD-8).

One document, one new parameter: `fraudFlagScoreMin`. The supervisor's Fraud
Flags card counts fraud-flagged claims scoring at or above it, and it is
**not** `siuFraudScoreMin`. The two are separate rules over the same column
pair, and the prototype has them at different numbers on purpose — the queue's
SIU chip refers at 60 (9 seeded claims), the dashboard card reviews at 55 (13).
Reusing the referral threshold would put the referral count under a caption
that says "review needed", quietly, with both numbers defensible on their own.
See `services/derivations/queue_flags.py::FraudFlaggedDerivation`.

A *new row*, never an edit to 0024's, for the reason every rules migration
since 0012 gives: v4 is what Story 3.1 shipped, and a benefit figure or a
banner produced under it stays explainable only while the document that
produced it still says what it said.

**v5 takes v1's effective date, not its own**, under the rule 0012 wrote down
and 0024 restated: future-date a version freely when it only changes values;
give it the superseded version's date when it adds a parameter the typed block
*requires*. `fraudFlagScoreMin` is required by `DerivationThresholds.of`, and
that block is loaded by the queue, `/stats/topbar`, the case file, the benefit
calculation and now the dashboard — so a gap between this migration landing and
its effective date arriving would not degrade one card, it would 500 every
console surface at once. There is no version of this parameter that is
optional-for-a-day.

Bumping the thresholds document invalidates outstanding queue cursors, by
design (`services/worklist/queue.py` refuses a page cut under a superseded
ranking). Correct here even though no dashboard figure enters a priority score:
the cursor records *which document* produced the ordering, and "v4 and v5 rank
identically" is a fact about today's values rather than a property of the
version.

Revision ID: 0037_fraud_flag_threshold
Revises: 0036_seed_email_templates
Create Date: 2026-08-18

"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0037_fraud_flag_threshold"
down_revision: str | None = "0036_seed_email_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENTS_DIR = Path(__file__).resolve().parents[2] / "rules" / "documents"

# Spelled out rather than globbed, for 0009's reason: a file dropped into the
# directory must not become a live rule without a migration saying so.
ROWS = (("derivation_thresholds", 5, "derivation_thresholds.v5.jdm.json"),)

# v1's, v2's, v3's and v4's date, deliberately — see the module docstring.
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
    op.execute("DELETE FROM rule_document WHERE key = 'derivation_thresholds' AND version = 5")
