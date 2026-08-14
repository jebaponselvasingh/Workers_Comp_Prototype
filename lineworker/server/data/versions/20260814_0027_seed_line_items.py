"""Seed the medical bills and claim expenses (Story 3.3, AC 3).

Loads `data/seed/bills.json` (520 rows) and `data/seed/expenses.json` (340
rows), both produced by `data/seed/extract_prototype_line_items.py` from the
prototype's `buildBills` and `buildExpenses` (AD-12).

**These figures are computed, not copied, and that is unusual enough to say
here too.** The prototype derives every line item from a hash of the claim id
at render time, so there was nothing written down to lift; the extractor runs
the prototype's arithmetic once and this migration inserts the result. The
extractor's docstring carries the full argument, including the one deliberate
divergence — JavaScript's signed `>>` over a hash its own author made unsigned
produced a status outside the declared vocabulary on 26 of 860 rows, and those
26 take the status the unsigned shift selects instead of the prototype's
phantom "Pending".

**Insertion order is the contract**, as it is for 0011's timeline and 0021's
photos. Neither table has another total order — a claim's six bills carry six
distinct categories but nothing orders the categories themselves — so the
Bills tab's display order *is* the identity column's order, and the executemany
below takes each list as-is. Nothing here sorts or groups it.

**Claims are resolved by business id**, 0011's and 0021's rule: the seed files
name `WC-nnnn` and the foreign key wants the surrogate, and resolving here
keeps the seed files readable and independent of whatever identity values a
particular database handed out.

**Every claim gets rows in both tables.** Four bills and two expenses are
unconditional in the prototype's builders, so a claim with none is an
extraction failure rather than a claim that cost nothing — which is why the
coverage check below is on 100 claims for each table rather than on a row
count alone. A silently under-seeded book would migrate cleanly and leave a
handler reading an empty Bills tab as a fact about the claim.

Revision ID: 0027_seed_line_items
Revises: 0026_financial_tables
Create Date: 2026-08-14

"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0027_seed_line_items"
down_revision: str | None = "0026_financial_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_DIR = Path(__file__).parent.parent / "seed"

COLUMNS = {"claim_id", "category", "label", "amount_cents", "status"}

#: How many rows must arrive and how many claims they must cover. Literals for
#: 0009's reason about globbing, and per-table because the two builders emit
#: independently — a change that lost every expense would still leave the bill
#: count right.
EXPECTED_BILLS = 520
EXPECTED_EXPENSES = 340
EXPECTED_CLAIMS = 100


def _load(name: str, expected_rows: int) -> list[dict[str, Any]]:
    path = SEED_DIR / name
    rows: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))

    # A shape check before the insert, because the failure without one is
    # unreadable: drifted extractor output reaches the database as a NOT NULL
    # violation or a CompileError about a bind parameter, and leaves the chain
    # halted with the table created and empty — which the API then serves as a
    # successful, cost-free claim (0007's argument).
    if not rows:
        raise ValueError(f"{path} is empty — re-run data/seed/extract_prototype_line_items.py")
    # Every row, not just the first (0011's code-review lesson): `executemany`
    # compiles its statement from row 0, so drift that begins further down is
    # either silently dropped or dies as a bind-parameter error halfway through.
    for index, row in enumerate(rows):
        if set(row) != COLUMNS:
            raise ValueError(
                f"{path}[{index}] has fields {sorted(row)}, expected "
                f"{sorted(COLUMNS)} — the extractor and this migration have drifted apart"
            )

    covered = {row["claim_id"] for row in rows}
    if len(rows) != expected_rows or len(covered) != EXPECTED_CLAIMS:
        raise ValueError(
            f"{path} holds {len(rows)} rows across {len(covered)} claims, expected "
            f"{expected_rows} across {EXPECTED_CLAIMS} — re-run "
            "data/seed/extract_prototype_line_items.py against the prototype"
        )
    return rows


def upgrade() -> None:
    bills = _load("bills.json", EXPECTED_BILLS)
    expenses = _load("expenses.json", EXPECTED_EXPENSES)

    bind = op.get_bind()
    meta = sa.MetaData()
    claim = sa.Table("claim", meta, autoload_with=bind)
    bill_table = sa.Table("bill", meta, autoload_with=bind)
    expense_table = sa.Table("expense", meta, autoload_with=bind)

    claim_ids = {
        business_id: surrogate
        for surrogate, business_id in bind.execute(sa.select(claim.c.id, claim.c.claim_id))
    }

    def resolve(rows: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
        resolved = []
        for row in rows:
            surrogate = claim_ids.get(row["claim_id"])
            if surrogate is None:
                raise ValueError(
                    f"{source} names claim {row['claim_id']!r}, which is not in the "
                    "seeded portfolio — the two seed files have drifted apart"
                )
            resolved.append({**row, "claim_id": surrogate})
        return resolved

    bind.execute(bill_table.insert(), resolve(bills, "bills.json"))
    bind.execute(expense_table.insert(), resolve(expenses, "expenses.json"))


def downgrade() -> None:
    op.execute("DELETE FROM expense")
    op.execute("DELETE FROM bill")
