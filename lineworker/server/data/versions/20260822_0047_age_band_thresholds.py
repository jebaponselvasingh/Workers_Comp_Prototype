"""`derivation_thresholds` v7 — the age band's three edges (Story 7.3, AD-8).

One document, three new parameters: `ageYoungerMin`, `ageOlderMin` and
`ageOldestMin`. The analyst workspace segments the caller's book by nine
dimensions, and `employee.age` is the one of them that is a *number* rather than
a value — so it needs a banding, and a banding needs cut-offs. Four ordinal
groups from three edges: `youngest` below 35, `younger` from 35, `older` from
45, `oldest` from 55.

**The band members are words and carry no numbers, which is why the edges have
to be here.** A member spelled `age_35_44` would put this document's cut-off in
the Python tier as well, and AD-8 forbids one rule element living in two tiers
for a reason this case makes concrete: moving the edge here would leave the
member's *name* asserting the old one, on every chip, legend and URL that had
ever carried it. So the vocabulary is Python, the edges are this document, and
the payload publishes them for the UI to compose "35–44" from. See
`services/derivations/worker_age_band.py`.

**They live in `derivation_thresholds` rather than in a document of their own,
and `trend_periods` is deliberately not the precedent.** 0046 stood up a new
document because a window is not a derivation cut-off. An age band is nothing
else, and `registry.Derivation.build` is `Callable[[DerivationThresholds], T]` —
one parameter block for the whole registry — so a registered computer whose
parameters lived elsewhere could not be built without changing the registry's
shape for every derivation that already works. 0019, 0024, 0037 and 0045 made
the same call for the path parameters, the PTD cut-off, the review threshold and
the fraud band.

`ageYoungerMin` carries the same 35 as `riskMedMin` and `fraudBandMedMin`, and
`ageOldestMin` the same 55 as `fraudFlagScoreMin` and `fraudBandHighMin`. Three
coincidences of integer across three unrelated columns — a severity score, a
fraud score and a person's age — and exactly the reason they are separate
parameters: collapsing any pair would tie a worker's age band to a fraud
threshold, so widening the review cut-off tomorrow would silently re-band the
workforce. 0037 and 0045 make the identical argument for the pairs *they* added;
this is that argument reaching a third column.

A *new row*, never an edit to 0045's, for the reason every rules migration since
0012 gives: v6 is what Story 7.1 shipped, and a fraud distribution produced
under it stays explainable only while the document that produced it still says
what it said.

**v7 takes v1's effective date, not its own**, under the rule 0012 wrote down and
0024, 0037 and 0045 restated: future-date a version freely when it only changes
values; give it the superseded version's date when it adds a parameter the typed
block *requires*. `DerivationThresholds.of` requires all three new keys, and that
block is loaded by the queue, `/stats/topbar`, the case file, the benefit
calculation, the whole dashboard and the analyst workspace — so a gap between
this migration landing and its effective date arriving would not degrade one
card, it would 500 every console surface at once. There is no version of these
parameters that is optional-for-a-day.

Bumping the thresholds document invalidates every outstanding queue, worklist and
drill-through cursor, by design: each of those cursors records which
`derivation_thresholds` version produced its ranking and refuses a page cut under
a superseded one. Correct here even though no seeded claim changes band, flag or
rank under v7 — the cursor records *which document* decided the ordering, and "v6
and v7 rank identically" is a fact about today's values rather than a property of
the version.

Revision ID: 0047_age_band_thresholds
Revises: 0046_trend_periods
Create Date: 2026-08-22

"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0047_age_band_thresholds"
down_revision: str | None = "0046_trend_periods"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENTS_DIR = Path(__file__).resolve().parents[2] / "rules" / "documents"

# Spelled out rather than globbed, for 0009's reason: a file dropped into the
# directory must not become a live rule without a migration saying so.
ROWS = (("derivation_thresholds", 7, "derivation_thresholds.v7.jdm.json"),)

# v1's through v6's date, deliberately — see the module docstring.
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
    op.execute("DELETE FROM rule_document WHERE key = 'derivation_thresholds' AND version = 7")
