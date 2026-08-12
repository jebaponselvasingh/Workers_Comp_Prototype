"""Statutory forms by handling path (Story 2.5, AC 1).

Structure only — 0018 puts the nine rows in, the split 0003/0004, 0006/0007,
0008/0009, 0010/0011 and 0014/0015 already use. It keeps `downgrade()` honest:
dropping the table takes its rows with it, and re-seeding is a revision of its
own rather than an edit to a structural migration.

**`claim_path` is a native enum, unlike the classification it names.** The
column is created here because a row *belongs* to a path; the claim's own path
is **not** a column — it is derived per request from
`services/derivations/path_classification.py`, which is what closes the prototype's
always-Path-B gap without reintroducing the derived column Story 1.2 banned.
`PATH_KEYS` is written out literally rather than imported from `ClaimPath`,
for the reason 0013 wrote its recovery map out and 0014 its eleven regions: a
migration is a historical record, and one that read a live constant would
silently change what it did when somebody reordered it.

**`(path, sort_order)` is unique, not `sort_order` alone.** Each path's forms
are numbered from zero in the prototype's array order; a global constraint
would force three independent lists into one sequence and make adding a Path A
form renumber Path C.

Grants (AD-4). `SELECT` to `lineworker_app`, and nothing else — the same
statement 0006 makes for `glossary_term`, for the same reason. The application
never writes a statutory form row; the migration inserts as the schema owner.
No sequence grant follows (nothing at runtime inserts), no `audit_redactor`
grant (there is no PHI here), no RLS. An app role that could rewrite the
statutory form list would be a capability nobody asked for behind an endpoint
that cannot use it.

**The seeded `download_url`s are placeholders.** They are the NY WCB and FNSB
links the prototype chose to make its demo concrete. NFR-4 and an explicit
Deferred architecture decision put per-jurisdiction validation and real
statutory PDF sourcing before go-live and outside this story; nothing here
resolves or checks them.

Revision ID: 0017_path_required_form
Revises: 0016_injury_capture_rules
Create Date: 2026-08-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_path_required_form"
down_revision: str | None = "0016_injury_capture_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The three handling paths, in the prototype's order (which is also
#: increasing severity, and the order PostgreSQL sorts the type in).
#: `tests/test_path_forms_migration.py` asserts this tuple is exactly
#: `ClaimPath` — order included — so the literal cannot drift unnoticed.
#: [Source: docs/Workers_Comp_Prototype.html lines 1542-1563]
PATH_KEYS: tuple[str, ...] = ("a", "b", "c")

ENUM_NAME = "claim_path"


def upgrade() -> None:
    op.create_table(
        "path_required_form",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        # The type is created by `create_table` as a side effect of the column,
        # exactly as 0010 creates `doc_type` and 0014 `body_region`.
        # `downgrade()` has to drop it by hand — see the note there.
        sa.Column("path", sa.Enum(*PATH_KEYS, name=ENUM_NAME), nullable=False),
        sa.Column("form_code", sa.Text(), nullable=False),
        sa.Column("form_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("timing", sa.Text(), nullable=False),
        sa.Column("download_url", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_path_required_form")),
        sa.UniqueConstraint("path", "sort_order", name=op.f("uq_path_required_form_path")),
    )

    op.execute("GRANT SELECT ON path_required_form TO lineworker_app")


def downgrade() -> None:
    op.drop_table("path_required_form")
    # `op.drop_table` leaves the native enum behind — it is a schema object in
    # its own right, and a re-upgrade would fail on "type claim_path already
    # exists". 0010 learned this about `doc_type`, 0013 about
    # `recovery_window` and 0014 about `body_region`.
    sa.Enum(name=ENUM_NAME).drop(op.get_bind(), checkfirst=True)
