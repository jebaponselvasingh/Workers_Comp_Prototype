"""`derivation_thresholds` v6 — the fraud-score band's two edges (Story 7.1, AD-8).

One document, two new parameters: `fraudBandMedMin` and `fraudBandHighMin`. The
analyst workspace's fraud distribution bands `claim.fraud_score` against them,
and that banding is a **third** rule over the fraud columns rather than a
re-spelling of either of the two already in this document. `siuFraudScoreMin` is
the SIU *referral* population and `fraudFlagScoreMin` the wider *review* one; both
are conjoined with `claim.fraud_flag`, so a claim nobody triaged is in neither
however high it scored. The band has no such conjunct — which is the only reason
a distribution can answer "how much of this portfolio scores high and was never
flagged". See `services/derivations/fraud_score_band.py`.

The high edge carries the same number as `fraudFlagScoreMin` today, and that
coincidence is exactly why they are two parameters: collapsing them would make
the analyst's high band definitionally the supervisor's Fraud Flags card, so
widening the review threshold tomorrow would silently re-band the whole
portfolio's distribution with it. 0037 makes the same argument for the pair *it*
added; this is that argument at the point where it stops being an anecdote.

A *new row*, never an edit to 0037's, for the reason every rules migration since
0012 gives: v5 is what Story 5.1 shipped, and a Fraud Flags count produced under
it stays explainable only while the document that produced it still says what it
said.

**v6 takes v1's effective date, not its own**, under the rule 0012 wrote down and
0024 and 0037 restated: future-date a version freely when it only changes values;
give it the superseded version's date when it adds a parameter the typed block
*requires*. `DerivationThresholds.of` requires both new keys, and that block is
loaded by the queue, `/stats/topbar`, the case file, the benefit calculation, the
whole dashboard and now the analyst workspace — so a gap between this migration
landing and its effective date arriving would not degrade one card, it would 500
every console surface at once. There is no version of these parameters that is
optional-for-a-day.

Bumping the thresholds document invalidates every outstanding queue, worklist and
drill-through cursor, by design: each of those cursors records which
`derivation_thresholds` version produced its ranking and refuses a page cut under
a superseded one. Correct here even though no seeded claim changes band, flag or
rank under v6 — the cursor records *which document* decided the ordering, and "v5
and v6 rank identically" is a fact about today's values rather than a property of
the version.

Revision ID: 0045_fraud_band_thresholds
Revises: 0044_document_body_text
Create Date: 2026-08-21

"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0045_fraud_band_thresholds"
down_revision: str | None = "0044_document_body_text"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENTS_DIR = Path(__file__).resolve().parents[2] / "rules" / "documents"

# Spelled out rather than globbed, for 0009's reason: a file dropped into the
# directory must not become a live rule without a migration saying so.
ROWS = (("derivation_thresholds", 6, "derivation_thresholds.v6.jdm.json"),)

# v1's through v5's date, deliberately — see the module docstring.
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
    op.execute("DELETE FROM rule_document WHERE key = 'derivation_thresholds' AND version = 6")
