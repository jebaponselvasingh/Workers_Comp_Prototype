"""Versioned rule-document storage (Story 2.1, AD-8).

Structure only — 0009 puts the two JDM documents in it, the same split
0003/0004 and 0006/0007 use. That split is what keeps `downgrade()` honest
and makes "supersede a rule" a revision of its own rather than an edit to a
structural migration.

Grants are the whole AD-4 statement for this table: `SELECT`, to
`lineworker_app`, and nothing else — no INSERT, no UPDATE, no sequence
grant. Rules are read-only at runtime by design: authoring a new version is
a migration, reviewed in a diff, so a running server can never rewrite the
parameters it is being judged against. It is also what makes an old cursor's
recorded `version` trustworthy — a row that cannot be updated in place
cannot silently change the ordering it produced.

Revision ID: 0008_rule_document
Revises: 0007_seed_glossary
Create Date: 2026-08-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_rule_document"
down_revision: str | None = "0007_seed_glossary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rule_document",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        # `now()` rather than a caller-supplied value: 0009 spells its
        # timestamp out, but nothing else should have to. A NOT NULL column
        # with no default is a trap for the first INSERT that omits it.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule_document")),
        # Unique so "version 2 of the priority weights" names one document.
        # Without it, two rows could tie for highest version and the loader's
        # answer would depend on the plan — and a cursor recording `v2` would
        # no longer identify the ordering it was cut from.
        sa.UniqueConstraint("key", "version", name=op.f("uq_rule_document_key")),
    )
    # No index beyond that constraint, deliberately. This table holds one
    # row per rule version — two of them today — and the unique `(key,
    # version)` index already covers the loader's only query. An index on
    # `effective_from` would be a maintenance obligation supporting a scan
    # of a handful of rows.

    op.execute("GRANT SELECT ON rule_document TO lineworker_app")


def downgrade() -> None:
    op.drop_table("rule_document")
