"""Case-file tables: timeline_event + document (Story 2.2, AC 4).

Structure only — 0011 puts the rows in, the split 0003/0004, 0006/0007 and
0008/0009 already use. It keeps `downgrade()` honest and makes re-seeding a
revision of its own.

Grants (AD-4). Both tables get the full CRUD grant `lineworker_app` holds on
the other domain tables, **including `timeline_event`**, which is worth
saying out loud because `audit_event` — the other append-only table — is
deliberately INSERT+SELECT only:

- `audit_event` is the record of who did what. Append-only there is a
  *security* property, so the database enforces it and the application is
  denied UPDATE and DELETE outright.
- `timeline_event` is case-file content a handler reads. Append-only there is
  a *design* rule (AD-12: rows are emitted by the owning service's command),
  enforced by where the code lives, not by a grant. Story 8.1's PHI purge has
  to be able to delete these rows when a claim is purged, and a purge that
  needed a second database role would be a worse answer than a service that
  owns its writes.

Two indexes, both on `claim_id`: every read of either table is "this claim's
rows", and without them a detail request sequential-scans 556 timeline rows
and 563 document rows per claim. Small today; the shape is wrong at any size.

Revision ID: 0010_case_file_tables
Revises: 0009_seed_rule_documents
Create Date: 2026-08-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_case_file_tables"
down_revision: str | None = "0009_seed_rule_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "timeline_event",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        # Nullable: 62 seeded settlement events carry the prototype's literal
        # "Closed" where a date belongs. See the model docstring.
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("tag", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claim.id"], name=op.f("fk_timeline_event_claim_id_claim")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_timeline_event")),
        # No `version` column — append-only, exempt from compare-and-swap.
    )
    op.create_index(
        op.f("ix_timeline_event_claim_id"), "timeline_event", ["claim_id"], unique=False
    )

    op.create_table(
        "document",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "doc_type",
            sa.Enum("froi", "incident", "medauth", "wage", "rtw", "legal", name="doc_type"),
            nullable=False,
        ),
        sa.Column("filed_date", sa.Date(), nullable=True),
        sa.Column("blob_key", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claim.id"], name=op.f("fk_document_claim_id_claim")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document")),
    )
    op.create_index(op.f("ix_document_claim_id"), "document", ["claim_id"], unique=False)

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON timeline_event, document TO lineworker_app")
    # The identity sequences are new; Story 1.2's blanket sequence grant ran
    # before they existed, so it has to be repeated for them.
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lineworker_app")


def downgrade() -> None:
    op.drop_index(op.f("ix_document_claim_id"), table_name="document")
    op.drop_table("document")
    op.drop_index(op.f("ix_timeline_event_claim_id"), table_name="timeline_event")
    op.drop_table("timeline_event")
    # `op.drop_table` leaves the native enum behind — it is a schema object in
    # its own right, and a re-upgrade would fail on "type doc_type already
    # exists". 0003's enums are dropped by 0002's `DROP OWNED BY`; this one
    # has no such umbrella, so it says so itself.
    sa.Enum(name="doc_type").drop(op.get_bind(), checkfirst=True)
