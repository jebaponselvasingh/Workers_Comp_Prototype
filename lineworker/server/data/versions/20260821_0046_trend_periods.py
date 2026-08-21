"""`trend_periods` v1 (Story 7.2, AD-8).

One document, one key, three parameters: how many buckets the Trends section
opens on, the widest window a caller may ask for, and the claim count at or
below which a bucket's mean is marked as too thin to lean on.

**A new key rather than three more expressions in `derivation_thresholds`**, for
0025's and 0038's reason at the point where it stops being about which service
owns the parameters and starts being about what they *are*. That block is the
single argument every registered derivation is *built* from (`Derivation.build`
takes `DerivationThresholds` and nothing else); none of these three reaches a
derivation at all. They govern the shape of a **window**, and a window is not a
derivation cut-off. The severity cohort's band edges keep coming from
`derivation_thresholds` through `derivations.risk`, exactly as the severity donut
and the High Risk card read them — so the Trends payload names two documents
separately, `rulesVersion` for the thresholds and `periodsVersion` for this one.

**No `derivation_thresholds` bump**, unlike 0045. Nothing in Story 7.2 retunes a
threshold the queue scores on, so every outstanding queue, worklist and
drill-through cursor stays valid — the right outcome when no ranking input has
moved. The six drill facets this story appends narrow on columns and on one enum;
none of them is a derived population, so the thresholds document decides nothing
new.

**Effective from v1's date**, under the rule 0012 wrote down and 0024, 0025, 0037,
0038 and 0045 have each restated: a document with no earlier version has nothing
to supersede and must be effective the moment the code that reads it ships.
`TrendPeriods.of` requires all three parameters and `/dashboard/trends` loads them
on every request, so a resolvable date with no `trend_periods` row is not a
degraded chart — it is `RuleDocumentMissing`, i.e. a 500 on every trends request
in the gap. The spec's Block If names exactly this, and 2026-08-11 is the date
every rule document in this tree is effective from.

**The filename carries `.v1`** where `handler_performance.jdm.json` and its
siblings do not, and the difference is worth one sentence rather than a reader's
afternoon: the unversioned name is a *constraint* on the two keys 0009 seeds by
reading `<key>.jdm.json` at migration time, not a naming convention this document
inherits. This migration names its file explicitly, so the version is spelled
where a reader of `rules/documents/` can see it — which is what the versioned
names were introduced for in the first place (0012's note).

Revision ID: 0046_trend_periods
Revises: 0045_fraud_band_thresholds
Create Date: 2026-08-21

"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0046_trend_periods"
down_revision: str | None = "0045_fraud_band_thresholds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENTS_DIR = Path(__file__).resolve().parents[2] / "rules" / "documents"

# Spelled out rather than globbed, for 0009's reason: a file dropped into the
# directory must not become a live rule without a migration saying so.
ROWS = (("trend_periods", 1, "trend_periods.v1.jdm.json"),)

# Every rule document in this tree is effective from this date — see the module
# docstring for why a first version may not be future-dated.
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
    op.execute("DELETE FROM rule_document WHERE key = 'trend_periods' AND version = 1")
