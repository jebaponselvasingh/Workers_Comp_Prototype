"""Server-side session store (Story 1.3, AC 2-4).

Infrastructure table, not an ERD entity: it holds a reference to app_user
and an expiry, never a role and never employer scope (AD-7 re-resolves
scope from app_user + user_employer_assignment on every request).

Grants follow AD-4's shape for the runtime role: the app role mints,
reads, and deletes its own sessions (logout must end the session
server-side — AC 3), so SELECT/INSERT/DELETE, and no UPDATE: a session is
created once and revoked, never mutated. Sliding expiry, if it ever
arrives, is a new grant plus a review, not a silent capability.

Revision ID: 0005_session
Revises: 0004_seed_portfolio
Create Date: 2026-08-10

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_session"
down_revision: str | None = "0004_seed_portfolio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_session_user_id_app_user"),
            # A session is meaningless without its user, and a re-seed or a
            # persona removal must not be blocked by whoever happens to be
            # logged in at the time.
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_session_token_hash")),
    )
    op.create_index(op.f("ix_session_user_id"), "session", ["user_id"], unique=False)
    # Supports the expired-row cleanup in mint_session, and the reaper that
    # will eventually replace it.
    op.create_index(op.f("ix_session_expires_at"), "session", ["expires_at"], unique=False)

    op.execute("GRANT SELECT, INSERT, DELETE ON session TO lineworker_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lineworker_app")


def downgrade() -> None:
    op.drop_index(op.f("ix_session_expires_at"), table_name="session")
    op.drop_index(op.f("ix_session_user_id"), table_name="session")
    op.drop_table("session")
