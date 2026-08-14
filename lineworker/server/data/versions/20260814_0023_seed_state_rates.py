"""Seed the statutory weekly rate schedule (Story 3.1, AC 1).

Loads `data/seed/state_rates.json` — the seventeen jurisdictions extracted
from the prototype's `STATE_WC_RATES` by
`data/seed/extract_prototype_state_rates.py` (AD-12), already converted from
the prototype's whole dollars into the integer cents this schema stores.

**The coverage check is the point of this migration, not a nicety.** The
prototype reads ``STATE_WC_RATES[c.state] || {max:1200, min:250}`` — a claim in
an unlisted state is clamped to two numbers belonging to no jurisdiction, and
the benefit card prints the state's name beside them with total confidence. The
story is explicit that the fallback does not survive: a missing state is a data
error. So this migration refuses to complete if any claim in the portfolio sits
in a state with no schedule, which turns "every claim can have its benefit
computed" into a property established at migration time rather than discovered
at request time by a handler. `services/financials/benefit.py` raises for the
same case, which is what covers a claim inserted after this ran.

**`EFFECTIVE_FROM` is supplied here because the prototype has none.** Statutory
weekly bounds are set per benefit year, and these are the prototype's 2026
figures against a portfolio whose earliest date of injury is 2026-01-12 — so
the first day of that year is the honest reading, and it precedes every claim
in the book. It is *provenance*, not validation: NFR-4 and an explicit Deferred
architecture decision put per-jurisdiction sourcing of real statutory rates
before go-live. The benefit card cites this date in place of the prototype's
"Illustrative figures for prototype purposes" line, which is the equivalent
note the story asks for until that validation is done.

Revision ID: 0023_seed_state_rates
Revises: 0022_benefit_schema
Create Date: 2026-08-14

"""

import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0023_seed_state_rates"
down_revision: str | None = "0022_benefit_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_PATH = Path(__file__).parent.parent / "seed" / "state_rates.json"

COLUMNS = {"state_code", "state_name", "weekly_min_cents", "weekly_max_cents"}

#: How many jurisdictions must arrive. Spelled out rather than counted from the
#: file, for 0009's and 0018's reason: a seed file that lost a state to a regex
#: change would otherwise migrate cleanly, and the coverage check below would
#: catch it only if a claim happened to be filed there.
#: [Source: docs/Workers_Comp_Prototype.html lines 638-645]
EXPECTED_STATES = 17

EFFECTIVE_FROM = date(2026, 1, 1)


def upgrade() -> None:
    rates = json.loads(SEED_PATH.read_text(encoding="utf-8"))

    # A shape check before the insert, because the failure without one is
    # unreadable: a drifted extractor output reaches the database as a NOT NULL
    # violation or a CompileError about a bind parameter, and leaves the chain
    # halted with the table created and empty (0007's argument).
    if not rates:
        raise ValueError(
            f"{SEED_PATH} is empty — re-run data/seed/extract_prototype_state_rates.py"
        )
    if set(rates[0]) != COLUMNS:
        raise ValueError(
            f"{SEED_PATH} rows have fields {sorted(rates[0])}, expected {sorted(COLUMNS)} — "
            "the extractor and this migration have drifted apart"
        )
    if len(rates) != EXPECTED_STATES:
        raise ValueError(
            f"{SEED_PATH} holds {len(rates)} jurisdictions, expected {EXPECTED_STATES} — "
            "re-run data/seed/extract_prototype_state_rates.py"
        )
    for rate in rates:
        # Restated from the extractor rather than trusted, and again as a
        # database CHECK: this file is edited by hand more readily than a regex
        # is, and an inverted pair clamps every claim in the state to the wrong
        # bound without anything looking broken.
        if rate["weekly_min_cents"] > rate["weekly_max_cents"]:
            raise ValueError(
                f"{rate['state_code']}: weekly min {rate['weekly_min_cents']} exceeds max "
                f"{rate['weekly_max_cents']} — the schedule's bounds are reversed"
            )

    bind = op.get_bind()
    meta = sa.MetaData()
    schedule = sa.Table("state_rate_schedule", meta, autoload_with=bind)
    bind.execute(
        schedule.insert(),
        [{**rate, "effective_date": EFFECTIVE_FROM} for rate in rates],
    )

    # Every claim *that exists when this revision runs* has a schedule — see
    # the module docstring. Asked of the database rather than of
    # `seed_data.json`, because what has to be true is a property of the rows.
    #
    # **The scope of that is narrower than it looks, and the gap is covered
    # elsewhere** (code review, 2026-08-14). This revision runs before every
    # higher-numbered one, on a fresh database as much as on an existing one,
    # so a *later* migration that inserts a claim in an uncovered jurisdiction
    # passes `alembic upgrade head` cleanly and surfaces only as a 500 on that
    # claim's case file. The property at head is asserted by
    # `tests/test_state_rate_migration.py::test_every_claim_in_the_database_has_a_schedule`,
    # which runs after the whole chain; this check is what stops the *seed*
    # from shipping uncovered, where the failure would otherwise be a hundred
    # claims deep.
    seeded = {row[0] for row in bind.execute(sa.text("SELECT DISTINCT state FROM claim"))}
    uncovered = sorted(seeded - {rate["state_code"] for rate in rates})
    if uncovered:
        raise ValueError(
            f"claims are filed in {uncovered} and no state_rate_schedule row covers them — "
            "the prototype's silent {max:1200,min:250} fallback is deliberately not "
            "reproduced (NFR-4), so a benefit could not be computed for those claims"
        )


def downgrade() -> None:
    op.execute("DELETE FROM state_rate_schedule")
