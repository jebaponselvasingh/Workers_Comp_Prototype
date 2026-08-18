"""`handler_performance` v1 (Story 5.2, AD-8).

One document, one key, eight parameters: the two cycle-time deviation bands that
produce On Track / Watch / Attention, and the six that make up the Low/Med/High
complexity blend. A document of its own for 0025's reason applied one epic
later — `services/worklist` owns the benchmark aggregate, so its tunables get a
key of their own rather than being folded into `derivation_thresholds`.

**Two of these parameters do feed registered derivations, and that is still not
an argument for putting them in the thresholds document.** `handler_complexity`
and `cycle_time_status` are registered (AD-10), but `Derivation.build` takes
`DerivationThresholds` and nothing else, so a derivation whose parameters live
elsewhere receives them at `.of()` — the arrangement `next_batch_date` already
uses for the disbursement cadence. `derivation_thresholds` is the block every
derivation is *built* from, not the block every derivation *reads*.

**No `derivation_thresholds` bump this time**, unlike 0037. Nothing in Story 5.2
retunes a threshold the queue scores on, so every outstanding queue cursor stays
valid — the right outcome when no ranking input has moved. (The benchmark table
ranks handlers, not claims, and its ordering is not cursored at all: the row
count is the number of handlers in the caller's scope.)

**Effective from v1's date**, under the rule 0012 wrote down and 0024, 0025 and
0037 have each restated: a document with no earlier version has nothing to
supersede and must be effective the moment the code that reads it ships.
`HandlerPerformance.of` requires all eight parameters and the dashboard's
benchmark endpoint loads them on every request, so a gap between this migration
and the effective date would 500 the table rather than blank it.

**0028 is deliberately untouched.** That migration materializes payment
schedules and loads only `benefit_params` and `derivation_thresholds` to do it;
nothing in a handler-benchmark parameter reaches a payment projection, so there
is no overlay to extend here. Adding this key to its loader would couple a
financial materialization to a dashboard rule for no reason and would have to be
undone the next time either moved.

Revision ID: 0038_handler_performance_rules
Revises: 0037_fraud_flag_threshold
Create Date: 2026-08-18

"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0038_handler_performance_rules"
down_revision: str | None = "0037_fraud_flag_threshold"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENTS_DIR = Path(__file__).resolve().parents[2] / "rules" / "documents"

# Spelled out rather than globbed, for 0009's reason: a file dropped into the
# directory must not become a live rule without a migration saying so.
ROWS = (("handler_performance", 1, "handler_performance.jdm.json"),)

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
    op.execute("DELETE FROM rule_document WHERE key = 'handler_performance' AND version = 1")
