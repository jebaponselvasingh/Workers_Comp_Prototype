"""`benefit_params` v1 and `derivation_thresholds` v4 (Story 3.1, AD-8).

Two documents, because the story's tunables belong to two different owners and
AD-8 puts each rule element in exactly one tier:

- **`benefit_params` v1** — `defaultCompRateBp`, `ptdCompRateBp`,
  `waitingPeriodDays`. `services/financials` owns the benefit formula, so its
  parameters get a document of their own, exactly as `intake_required_documents`
  (0012) and `injury_capture` (0016) did for `services/claims` commands.
- **`derivation_thresholds` v4** — v3's twelve unchanged, plus
  `ptdSeverityThreshold`. That one parameterises a *registered derivation*
  (`indemnity_type`), and `DerivationThresholds` is the single argument every
  registered derivation is built from — the argument 0019 made for the path
  parameters. It is asked once, of the derivation; `compute_benefit` consumes
  the answer rather than re-deriving it, so the threshold has one reader.

A *new row*, never an edit to 0019's, for the reason that migration gives at
length: v3 is what Story 2.5 shipped, and a forms card produced under it stays
explainable only while the document that produced it still says what it said.

**v4 takes v1's effective date, not its own**, under the rule 0012 wrote down:
future-date a version freely when it only changes values; give it the
superseded version's date when it adds a parameter the typed block *requires*.
`ptdSeverityThreshold` is required by `DerivationThresholds.of`, and that block
feeds the queue, `/stats/topbar` and the case file — so a gap between the
migration and the effective date would 500 the whole console, not just this
story's card. `benefit_params` v1 takes the same date for the simpler reason
that a document with no earlier version has nothing to supersede and must be
effective the moment the code that reads it ships.

Bumping the thresholds document invalidates outstanding queue cursors, by
design (`services/worklist/queue.py` refuses a page cut under a superseded
ranking). Correct here even though no benefit parameter enters a score: the
cursor records *which document* produced the ordering, and "v3 and v4 happen to
rank identically" is a fact about today's values, not a property of the version.

Revision ID: 0024_benefit_rules
Revises: 0023_seed_state_rates
Create Date: 2026-08-14

"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0024_benefit_rules"
down_revision: str | None = "0023_seed_state_rates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENTS_DIR = Path(__file__).resolve().parents[2] / "rules" / "documents"

# Spelled out rather than globbed, for 0009's reason: a file dropped into the
# directory must not become a live rule without a migration saying so.
ROWS = (
    ("benefit_params", 1, "benefit_params.jdm.json"),
    ("derivation_thresholds", 4, "derivation_thresholds.v4.jdm.json"),
)

# v1's, v2's and v3's date, deliberately — see the module docstring.
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
    op.execute("DELETE FROM rule_document WHERE key = 'benefit_params' AND version = 1")
    op.execute("DELETE FROM rule_document WHERE key = 'derivation_thresholds' AND version = 4")
