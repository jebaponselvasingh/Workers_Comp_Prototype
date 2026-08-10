"""Glossary reference table (Story 1.6, AC 1).

Structure only — 0007 puts the rows in it, the same split 0003/0004 use for
the portfolio. It keeps `downgrade()` honest: dropping the table takes its
rows with it, and re-seeding is a revision of its own rather than an edit to
a structural migration.

Grants are the whole AD-4 statement for this table: `SELECT`, to
`lineworker_app`, and nothing else. The application never writes a glossary
term — the migration inserts as the schema owner — so no INSERT, no sequence
grant, no `audit_redactor` grant (there is no PHI here to redact) and no RLS.
An app role that *could* rewrite the glossary would be a capability nobody
asked for, sitting behind an endpoint that cannot use it.

Revision ID: 0006_glossary_term
Revises: 0005_session
Create Date: 2026-08-10

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_glossary_term"
down_revision: str | None = "0005_session"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "glossary_term",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("abbreviation", sa.Text(), nullable=False),
        sa.Column("term", sa.Text(), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_glossary_term")),
        # Unique because the wire payload depends on it: `GlossaryTermResponse`
        # publishes neither `id` nor `sortOrder`, so the abbreviation is the
        # only stable identity the panel can key its rows on. This constraint
        # is what makes that payload keyable by construction — without it the
        # client is trusting a uniqueness nothing enforces, and the fix would
        # be leaking a surrogate id into a public contract.
        sa.UniqueConstraint("abbreviation", name=op.f("uq_glossary_term_abbreviation")),
        # Unique so the display order is total: the repository sorts on this
        # column alone, and a tie would let two rows swap places between
        # requests (or repeat across pages, if anything ever pages this).
        sa.UniqueConstraint("sort_order", name=op.f("uq_glossary_term_sort_order")),
    )

    op.execute("GRANT SELECT ON glossary_term TO lineworker_app")


def downgrade() -> None:
    op.drop_table("glossary_term")
