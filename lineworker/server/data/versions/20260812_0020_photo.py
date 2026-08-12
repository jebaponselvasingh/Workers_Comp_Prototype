"""Incident and site photos (Story 2.6, AC 1).

Structure only — 0021 puts the 293 rows in, the split 0003/0004, 0006/0007,
0008/0009, 0010/0011, 0014/0015 and 0017/0018 already use. It keeps
`downgrade()` honest: dropping the table takes its rows with it, and re-seeding
is a revision of its own rather than an edit to a structural migration.

**No enum, no `sort_order`, no `version`** — and each absence is a decision:

- Nothing here is a vocabulary. A caption and a source line are free text a
  human wrote, so there is no token to constrain and nothing for a native type
  to hold (`document` has `doc_type` because a *type* is a vocabulary; "Plant
  safety, 03/22" is not).
- Display order is insertion order, carried by the identity column, exactly as
  `document` and `timeline_event` carry theirs. `path_required_form` needed an
  explicit number only because three independent reference lists share one
  table; a claim's photos are one list. The seed inserts in the prototype's
  array order and the read orders by `id`.
- `version` is AD-4's compare-and-swap column and arbitrates concurrent
  *writers*. This table has none — no story in Epics 1–8 uploads, annotates or
  deletes a photo — so a version column would be a promise about a command that
  does not exist. `document` carries one because a filing date can be corrected
  and a blob key attached; a photo, in this system, is only ever read.

**`blob_key` is nullable and every seeded row leaves it null** (`document`'s
rule, and for the same reason twice over): the prototype has no image files —
its thumbnails are the 📷 emoji — so there are no bytes to put anywhere yet.
The column exists now because the *boundary* is what is binding, not the
backing store: when real files arrive they ride the one `BlobStore` protocol
(`put/get/delete/url`) and this is the handle they are addressed by. The
volume-versus-MinIO decision stays Deferred.

**`ON DELETE` is deliberately left to the FK's default.** A photo whose claim
is gone is PHI with nothing to scope it by — AD-7 filters on the claim's
employer, so an orphan is a row no scope predicate can refuse. Epic 8's purge
cascade is where the deletion order is decided; until then the constraint's job
is to make an orphan impossible rather than to make one silently disappear.

Grants (AD-4). `SELECT` to `lineworker_app`, and nothing else — the statement
0006 makes for `glossary_term` and 0017 for `path_required_form`. The
application never writes a photo row; the migration inserts as the schema
owner. No sequence grant follows (nothing at runtime inserts). No
`audit_redactor` grant and no RLS: that role's three grants are on
`audit_event` alone (0003), and a photo is claim-derived PHI reached through
the scoped claim path like every other child row.

Revision ID: 0020_photo
Revises: 0019_claim_path_rules
Create Date: 2026-08-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_photo"
down_revision: str | None = "0019_claim_path_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "photo",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("blob_key", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["claim_id"], ["claim.id"], name=op.f("fk_photo_claim_id_claim")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_photo")),
    )
    # Every read of this table is "one claim's photos" — the tab is never
    # rendered without a claim — so the FK column is the only index worth
    # having, exactly as on `document`.
    op.create_index(op.f("ix_photo_claim_id"), "photo", ["claim_id"])

    op.execute("GRANT SELECT ON photo TO lineworker_app")


def downgrade() -> None:
    op.drop_index(op.f("ix_photo_claim_id"), table_name="photo")
    op.drop_table("photo")
