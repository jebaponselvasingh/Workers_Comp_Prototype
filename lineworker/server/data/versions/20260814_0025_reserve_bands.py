"""`reserve_bands` v1 (Story 3.2, AD-8).

One document, one key, two parameters: `lightRatioBp` and `heavyRatioBp`, the
thresholds the reserve adequacy verdict is banded on. A document of its own for
0024's reason applied one story later — `services/financials` owns the reserve
check, so its tunables get a key of their own rather than being folded into
`derivation_thresholds`, which is the single argument every *registered
derivation* is built from.

**No `derivation_thresholds` bump this time**, unlike 0024. Nothing in Story
3.2 parameterises a registered derivation: the verdict is a service query over
two exposure terms, not a banding of one claim column, so there is no
threshold for the registry to carry. Leaving the thresholds document alone also
leaves every outstanding queue cursor valid, which is the right outcome when no
ranking input has moved.

**Effective from v1's date**, under the rule 0012 wrote down and 0024 restated:
a document with no earlier version has nothing to supersede and must be
effective the moment the code that reads it ships. `ReserveBands.of` requires
both parameters, and the case file loads them on every read of every claim — so
a gap between this migration and the effective date would 500 the whole console
rather than blank one card.

Revision ID: 0025_reserve_bands
Revises: 0024_benefit_rules
Create Date: 2026-08-14

"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0025_reserve_bands"
down_revision: str | None = "0024_benefit_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENTS_DIR = Path(__file__).resolve().parents[2] / "rules" / "documents"

# Spelled out rather than globbed, for 0009's reason: a file dropped into the
# directory must not become a live rule without a migration saying so.
ROWS = (("reserve_bands", 1, "reserve_bands.jdm.json"),)

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
    op.execute("DELETE FROM rule_document WHERE key = 'reserve_bands' AND version = 1")
