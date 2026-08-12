"""Seed the treatment plans and the prognosis columns (Story 2.4, AC 1).

Both come from `data/seed/seed_data.json`'s `deferred` block — the part of
the prototype's `ALL_CLAIMS` that Story 1.2 extracted but had no column for
yet. No new extractor: the data has been in the repository since 1.2 and
re-running an extractor to move it would put the *prototype file* in this
migration's dependency chain for content that is already committed.

**500 treatment-plan steps, five per claim, all 100 claims.** The array's
index is the step number (1-based, as the card renders it), and that is the
whole ordering contract — `treatment_plan_step` does not rely on insertion
order the way `timeline_event` does, because `step_no` is a real column with
a uniqueness constraint behind it.

**`additional_injury` is deliberately left empty.** The prototype's
`additionalInjuries` store starts `{}` and no claim has an entry (line 656),
so there is nothing to extract. The epic's "creates + seeds the tables"
phrasing reads as if there were; seeding it would mean inventing clinical
data, which is the one thing a seed migration must not do.

**The prognosis columns become NOT NULL here, not in 0014.** They arrive
nullable so that this revision is the only place a claim can be without one;
after the UPDATE below every row has all four, and the constraint says so.
A claim with a blank prognosis card would otherwise be indistinguishable
from one whose prognosis nobody recorded.

Revision ID: 0015_seed_injury_diagram
Revises: 0014_injury_diagram_tables
Create Date: 2026-08-12

"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0015_seed_injury_diagram"
down_revision: str | None = "0014_injury_diagram_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_PATH = Path(__file__).parent.parent / "seed" / "seed_data.json"

#: The prototype's `prognosis` keys → the columns 0014 added.
PROGNOSIS_FIELDS = {
    "mmi": "prognosis_mmi",
    "rtw": "prognosis_rtw",
    "impairment": "prognosis_impairment",
    "litigation": "prognosis_litigation",
}


def _deferred(seed: dict[str, Any]) -> list[dict[str, Any]]:
    """The `deferred` rows, refused with a sentence if they have drifted.

    Story 1.6's lesson, restated by 0011: without this, a renamed or
    truncated extraction reaches the database as a `NOT NULL` violation or a
    bind-parameter error, halting the chain with the tables created and empty
    — a state the API serves as a claim with no treatment plan and, once the
    NOT NULL below is applied, not at all.

    Every row is checked, not just the first: `executemany` compiles its
    statement from row 0, so drift that begins further down is silently
    dropped rather than reported.
    """
    rows = seed.get("deferred")
    if not rows:
        raise ValueError(f"{SEED_PATH} has no `deferred` block — the seed file is not the 1.2 one")
    for index, row in enumerate(rows):
        steps = row.get("treatment_plan")
        prognosis = row.get("prognosis")
        if not isinstance(steps, list) or not steps or not all(isinstance(s, str) for s in steps):
            raise ValueError(
                f"{SEED_PATH} deferred[{index}] ({row.get('claim_id')!r}) has no "
                "list-of-strings `treatment_plan` — the seed file and this migration disagree"
            )
        if not isinstance(prognosis, dict) or set(prognosis) != set(PROGNOSIS_FIELDS):
            raise ValueError(
                f"{SEED_PATH} deferred[{index}] ({row.get('claim_id')!r}) has prognosis keys "
                f"{sorted(prognosis) if isinstance(prognosis, dict) else prognosis!r}, "
                f"expected {sorted(PROGNOSIS_FIELDS)}"
            )
    return list(rows)


def upgrade() -> None:
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    deferred = _deferred(seed)

    bind = op.get_bind()
    meta = sa.MetaData()
    claim = sa.Table("claim", meta, autoload_with=bind)
    step = sa.Table("treatment_plan_step", meta, autoload_with=bind)

    claim_ids = {
        business_id: surrogate
        for surrogate, business_id in bind.execute(sa.select(claim.c.id, claim.c.claim_id))
    }

    steps: list[dict[str, Any]] = []
    for row in deferred:
        surrogate = claim_ids.get(row["claim_id"])
        if surrogate is None:
            raise ValueError(
                f"{SEED_PATH} deferred names claim {row['claim_id']!r}, which is not in the "
                "seeded portfolio — the two halves of the seed file have drifted apart"
            )
        steps.extend(
            {"claim_id": surrogate, "step_no": number, "description": description}
            for number, description in enumerate(row["treatment_plan"], start=1)
        )
        bind.execute(
            claim.update()
            .where(claim.c.id == surrogate)
            .values(**{column: row["prognosis"][key] for key, column in PROGNOSIS_FIELDS.items()})
        )

    bind.execute(step.insert(), steps)

    # Every claim now has all four; the columns can say so. A row that
    # somehow escaped the loop above fails here rather than reaching the
    # prognosis card as four empty labels.
    for column in PROGNOSIS_FIELDS.values():
        op.alter_column("claim", column, existing_type=sa.Text(), nullable=False)


def downgrade() -> None:
    for column in PROGNOSIS_FIELDS.values():
        op.alter_column("claim", column, existing_type=sa.Text(), nullable=True)
    op.execute("UPDATE claim SET " + ", ".join(f"{c} = NULL" for c in PROGNOSIS_FIELDS.values()))
    op.execute("DELETE FROM treatment_plan_step")
