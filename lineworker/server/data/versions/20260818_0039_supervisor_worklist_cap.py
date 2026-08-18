"""`worklist_actions` v2 — the supervisor worklist's cap and page size (Story 5.4, AD-8).

One document, two new parameters: `supervisorWorklistCap` and
`supervisorWorklistPageLimit`. The priority-claims table cuts its
active-treatment ∪ fraud-flagged ∪ litigation population at the first and serves
it ten rows at a time under the second, and neither number appears anywhere in
Python — AD-8 names "worklist caps" as JDM-owned, so `services/worklist` may not
spell either one.

**Why here rather than in `priority_weights`, where `pageLimit` already lives.**
That is the document a reader would reach for first, and it is the wrong one.
`services/worklist/queue.py` records the `priority_weights` version inside every
pagination cursor and refuses a page cut under a version that has since been
superseded — so publishing a v2 of that document to add a number the queue never
reads would invalidate every outstanding queue cursor in the console, mid-scroll,
for every handler. `deferred-work.md` recorded exactly that cost when `pageLimit`
was placed there; paying it a second time, for a value the queue cannot see,
would be paying it for nothing. `worklist_actions` is already loaded by this
story's path (the table's Priority Next Best Action column is element 0 of what
`generate_actions` returns), is owned by the same service, and already carries
the family's other list-length knobs.

A *new row*, never an edit to 0031's, for the reason every rules migration since
0012 gives: v1 is what Story 3.5 shipped, and a checklist rendered under it stays
explainable only while the document that produced it still says what it said.

**v2 takes v1's effective date, not today's**, under the rule 0012 wrote down and
0024 and 0037 restated: future-date a version freely when it only changes values;
give it the superseded version's date when it adds a parameter the typed block
*requires*. `WorklistActions.of` requires both new keys, and that block is loaded
by `GET /claims/{id}/actions` on every case-file open — so a gap between this
migration landing and its effective date arriving would not degrade one table, it
would 500 the action checklist for every claim in the console, every day in
between. There is no version of these parameters that is optional-for-a-day.

Checked before writing this, and the check is the one that makes the shared date
safe: every caller of `worklist_actions_for` resolves the document at today or
later — `services/worklist/actions.py` and `api/routers/dashboard.py` both pass
today, and the tests take the default, which is today. Nothing in the codebase
resolves this document at an `as_of` before v2's effective date, so no caller can
be handed v1 and asked for the new keys. The property is what matters, not the
count: this same change adds a caller, so an enumeration written here would be
stale in its own commit.

Two versions sharing an effective date is a case `rules/engine.py::load` already
decides: it orders on `version` descending, not on `effective_from`, precisely so
a same-day supersession resolves to the later-authored document.

Bumping this document does **not** invalidate any queue cursor, which is the
whole point of the placement — but it does invalidate outstanding
*priority-claims* cursors, by design: that cursor records all three of the rule
versions that move its page, this document among them (it decides the cap), and a
page cut against a different cap is not a page of the list being asked for.

Revision ID: 0039_supervisor_worklist_cap
Revises: 0038_handler_performance_rules
Create Date: 2026-08-18

"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0039_supervisor_worklist_cap"
down_revision: str | None = "0038_handler_performance_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENTS_DIR = Path(__file__).resolve().parents[2] / "rules" / "documents"

# Spelled out rather than globbed, for 0009's reason: a file dropped into the
# directory must not become a live rule without a migration saying so.
ROWS = (("worklist_actions", 2, "worklist_actions.v2.jdm.json"),)

# v1's date, deliberately — see the module docstring.
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
    op.execute("DELETE FROM rule_document WHERE key = 'worklist_actions' AND version = 2")
