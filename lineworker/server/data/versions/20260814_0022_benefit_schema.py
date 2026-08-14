"""The statutory benefit calculation's schema (Story 3.1, AC 1 and AC 4).

Two changes, structure only — 0023 puts the rows in, on the split 0003/0004,
0006/0007, 0008/0009, 0010/0011, 0014/0015 and 0017/0018 already use. It keeps
`downgrade()` honest: dropping the table takes its rows with it, and re-seeding
is a revision of its own rather than an edit to a structural migration.

**`state_rate_schedule`** holds the weekly indemnity bounds a jurisdiction
clamps a benefit to. Money in integer cents, like every other amount in this
schema; `state_code` unique, so this is the *current* schedule per state rather
than a history (see the model docstring on what moves when a refresh process
adds next year's figures). The `CHECK` on the two bounds is the database's half
of a check the extractor and 0023 also make: an inverted pair pins every claim
in the state to the lower bound, and the card still renders a confident figure.

**`claim.comp_rate_override_bp`** is the handler's override, in basis points
(6667 = 66.67%), nullable because null *is* the state the ↺ reset restores.
Integer rather than a percentage float for the reason the money columns are
integers: 66.67 has no exact binary representation, and "is this claim
overridden?" must not be a comparison two values can fail by 1e-14.

Grants (AD-4). `SELECT` on `state_rate_schedule` to `lineworker_app` and
nothing else — 0006's statement for `glossary_term` and 0017's for
`path_required_form`, for the same reason: the application never writes a
statutory rate, and the migration inserts as the schema owner. No sequence
grant follows (nothing at runtime inserts) and no `audit_redactor` grant (there
is no PHI in a state's weekly maximum). `claim` is already granted UPDATE, so
the new column needs no grant of its own.

Revision ID: 0022_benefit_schema
Revises: 0021_seed_photos
Create Date: 2026-08-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_benefit_schema"
down_revision: str | None = "0021_seed_photos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "state_rate_schedule",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("state_code", sa.Text(), nullable=False),
        sa.Column("state_name", sa.Text(), nullable=False),
        sa.Column("weekly_min_cents", sa.BigInteger(), nullable=False),
        sa.Column("weekly_max_cents", sa.BigInteger(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.CheckConstraint(
            "weekly_min_cents <= weekly_max_cents",
            name=op.f("ck_state_rate_schedule_ck_weekly_bounds"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_state_rate_schedule")),
        sa.UniqueConstraint("state_code", name=op.f("uq_state_rate_schedule_state_code")),
    )

    # Nullable with no server default: a null here means "the statutory default
    # applies", which is what every existing row should say and what the reset
    # command restores. A `server_default` would be a number nobody chose.
    op.add_column("claim", sa.Column("comp_rate_override_bp", sa.Integer(), nullable=True))

    op.execute("GRANT SELECT ON state_rate_schedule TO lineworker_app")


def downgrade() -> None:
    op.drop_column("claim", "comp_rate_override_bp")
    op.drop_table("state_rate_schedule")
