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

# The same date every other rule document carries (0009, 0012, 0019), and it
# is deliberate rather than cosmetic (Story 2.6's review pass).
#
# This migration shipped with `2026-08-12` — the day it was written — while
# 0009, 0012 and 0019 all use `2026-08-11`. Migration 0019 spells out why that
# is dangerous: "a version that adds *required* parameters cannot be safely
# future-dated — between the migration and the effective date the loader
# resolves the older one". `injury_capture` v1 has no older one to resolve, so
# the failure is worse rather than milder: `injury_capture_for` raises
# `RuleDocumentMissing` and `_injury` is on the case-file path, so **the whole
# case file 500s** — including the 409 body every Story 2.4 write embeds.
#
# The window it was reachable in has passed, which is precisely why it was
# invisible. It is still reachable through `claim_detail(as_of=…)` with an
# earlier date, and it would have been live for any deployment migrated on
# 08-11. Corrected here so a *fresh* database matches the other three; a
# database already migrated keeps the row it was seeded with, which is
# harmless now that the date is in the past.
#
# `test_no_rule_document_is_seeded_with_a_future_effective_date` is what stops
# the next migration reintroducing it.
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
    op.execute("DELETE FROM rule_document WHERE key = 'injury_capture' AND version = 1")
