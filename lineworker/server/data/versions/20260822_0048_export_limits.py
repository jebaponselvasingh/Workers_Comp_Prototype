"""`export_limits` v1 (Story 7.5, AD-8).

One document, one key, one parameter: how many rows of one table a single export
request may extract.

**A new key rather than another expression in `derivation_thresholds`**, for
0025's, 0038's and 0046's reason at the point where it stops being about which
service owns the parameter and starts being about what it *is*. That block is the
single argument every registered derivation is *built* from (`Derivation.build`
takes `DerivationThresholds` and nothing else); this parameter reaches no
derivation at all. It bounds an **answer's size**, the way `trend_periods
.maxBuckets` does, and it is the closest sibling this tree has — with one
difference worth writing down. `maxBuckets` caps how legible a chart is;
`maxRows` caps how much PHI one authenticated session may carry out of the
console in one gesture (NFR-5), which is a compliance posture with an owner who
does not deploy code. AD-8 gives JDM "weights, thresholds, bands, **caps**", and
this is the one cap in the tree whose retuning is a control decision rather than
a product one.

**No `derivation_thresholds` bump**, unlike 0045 and 0047. Story 7.5 adds no
figure, no band and no population — the export re-runs the folds 7.1–7.4 already
ship — so every outstanding queue, worklist and drill-through cursor stays valid.
The right outcome when no ranking input has moved.

**Effective from v1's date**, under the rule 0012 wrote down and 0024, 0025,
0037, 0038, 0045 and 0046 have each restated: a document with no earlier version
has nothing to supersede and must be effective the moment the code that reads it
ships. `ExportLimits.of` requires the parameter and every one of the six export
routes loads this document on every request, so a resolvable date with no
`export_limits` row is not a degraded export — it is `RuleDocumentMissing`, i.e.
a 500 on every export request in the gap, before a claim is read. The spec's
Block If names exactly this, and 2026-08-11 is the date every rule document in
this tree is effective from.

**The filename carries `.v1`** where `handler_performance.jdm.json` and its
siblings do not, for 0046's reason: the unversioned name is a *constraint* on the
two keys 0009 seeds by reading `<key>.jdm.json` at migration time, not a naming
convention this document inherits. This migration names its file explicitly, so
the version is spelled where a reader of `rules/documents/` can see it.

Revision ID: 0048_export_limits
Revises: 0047_age_band_thresholds
Create Date: 2026-08-22

"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0048_export_limits"
down_revision: str | None = "0047_age_band_thresholds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENTS_DIR = Path(__file__).resolve().parents[2] / "rules" / "documents"

# Spelled out rather than globbed, for 0009's reason: a file dropped into the
# directory must not become a live rule without a migration saying so.
ROWS = (("export_limits", 1, "export_limits.v1.jdm.json"),)

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
    op.execute("DELETE FROM rule_document WHERE key = 'export_limits' AND version = 1")
