"""The three completion flags the action checklist reads (Story 3.5, AC 5).

Structure only, and there is nothing for a companion seed migration to do:
every existing row's honest value is `false` — no document in this portfolio
has been reviewed by anybody and no claim's OSHA 300 entry has been filed
through this console, because until this story there was nowhere to record
either. The `server_default` is therefore the backfill.

**Three booleans, and deliberately not a table.** The obvious shape for "which
actions has this handler completed?" is a generic action-state store keyed by
claim and action, and the architecture rules it out by name: completions are
*entity-backed*, so that "the worklist and the ledgers stay in sync because
they read the same status row" (docs/Architecture-LINEWORKER.md 4.2). A
separate store would be a second place a claim's OSHA status is written down,
and the first divergence between the two would be a checklist that says an
entry is filed beside a claim that says it is not. A generic table is also an
AD-12 registry addition — a new entity needs a named write-owner — which is an
architecture conversation rather than a quiet migration.

- **`claim.osha_logged`** is the handler's record that a recordable injury has
  been entered on the OSHA 300 log. It is not derived from `osha_recordable`:
  one says the injury *must* be logged, the other that it *has been*, and the
  action fires on exactly the gap between them.
- **`document.reviewed` / `document.confirmed`** are the prototype's
  `docReviewState` (lines 871-883), which it keeps in a module-level object
  that is lost on reload. Two columns rather than one three-valued state
  because they are two events with two audit rows: a document is read, and then
  it is accepted. The ordering (`confirmDocument` returns early when
  `!st.reviewed`) is enforced by `services/claims/assessment.py` as a status
  guard, not by a disabled button.

All three are `NOT NULL` with a `false` default: "not yet done" is a fact about
every row, not a missing value, and a nullable flag would give the generator a
third case to have an opinion about.

Grants (AD-4). None needed. `claim` and `document` are already granted
`SELECT, INSERT, UPDATE, DELETE` to `lineworker_app` by 0003 and 0010, and a
new column on a granted table inherits the table's privileges — 0022's note
for `claim.comp_rate_override_bp`, restated because it is the thing a reader
checks first.

Revision ID: 0030_action_checklist_columns
Revises: 0029_system_actor
Create Date: 2026-08-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_action_checklist_columns"
down_revision: str | None = "0029_system_actor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "claim",
        sa.Column("osha_logged", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "document",
        sa.Column("reviewed", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "document",
        sa.Column("confirmed", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    # Dropping these loses real completions — a handler's record that an OSHA
    # entry was filed, and every document acceptance since. That is the honest
    # inverse of this migration rather than a hazard worth refusing (unlike
    # 0029's system actor, whose deletion would destroy the audit trail of
    # payments that actually went out): the flags are re-derivable from
    # `audit_event`, which this migration does not touch.
    op.drop_column("document", "confirmed")
    op.drop_column("document", "reviewed")
    op.drop_column("claim", "osha_logged")
