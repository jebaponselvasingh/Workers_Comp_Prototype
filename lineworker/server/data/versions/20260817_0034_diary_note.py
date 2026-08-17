"""diary_note — the diary aggregate's second table (Story 4.2, AC 2).

Structure only, and **no seed migration follows it**: notes start empty. That
is the one place this revision departs from 0032/0033's pairing, and it is
deliberate — a meeting is a plan somebody made and the story asks for two demo
ones per persona, while a diary note is a handler's own working record and a
seeded one would be words nobody wrote attributed to a named person.

**No enum**, so `downgrade()` has nothing to drop by hand — the trap 0010,
0013, 0014, 0026 and 0032 each learned about a native type does not apply here.

**No `version` column.** Notes are create-only in v1: there is no edit and no
delete command, so nothing is ever read-modify-written and there is no
concurrent writer for a compare-and-swap to arbitrate. `timeline_event` (0010)
and `audit_event` (0003) are the same shape for the same reason; the
write-concurrency convention names append-only stores as exempt outright.

**Three indexes, and each earns its place.** Every read filters on
`app_user_id` (the list is caller-scoped — a note belongs to its author), the
scope predicate additionally resolves `claim_id` against the caller's
employers, and `noted_at` is the leading column of the keyset the cursor walks
(`noted_at DESC, id DESC`). Without the third, every page of the list is a
sequential scan plus a sort of the whole table.

**`claim_id` is nullable**, per the ERD's `CLAIM |o--o{ DIARY_NOTE`. The
add-note input is on screen whether or not a claim is selected, and a note
written with none is legal rather than refused.

**`ON DELETE` is left to the FK's default**, 0032's and `photo`'s ruling: a
note whose claim is gone is PHI with nothing to scope it by, and Epic 8's purge
cascade is where the deletion order is decided. Until then the constraint's job
is to make an orphan impossible rather than to make one silently disappear.

Grants (AD-4). The full CRUD grant the other domain tables hold, and the two
halves of it are wanted for different reasons: the command INSERTs at runtime,
and Story 8.1's PHI purge has to be able to DELETE these rows — which is why
the grant is not narrowed to `SELECT, INSERT` even though no application code
updates or deletes one. That `services/claims/notes.py` is the *only* writer is
a design rule enforced by where the code lives, not by the grant (0010's
argument for `timeline_event`).

Revision ID: 0034_diary_note
Revises: 0033_seed_meetings
Create Date: 2026-08-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_diary_note"
down_revision: str | None = "0033_seed_meetings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diary_note",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("app_user_id", sa.Integer(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=True),
        sa.Column("note_text", sa.Text(), nullable=False),
        sa.Column(
            "noted_at",
            sa.DateTime(timezone=True),
            # `now()` rather than nothing, 0008's rule: a NOT NULL timestamp
            # with no default is a trap for the first INSERT that omits it. The
            # command supplies the value so that the note and its audit event
            # carry one instant, and this default is what catches anything that
            # does not.
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["app_user_id"], ["app_user.id"], name=op.f("fk_diary_note_app_user_id_app_user")
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claim.id"], name=op.f("fk_diary_note_claim_id_claim")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_diary_note")),
    )
    op.create_index(op.f("ix_diary_note_app_user_id"), "diary_note", ["app_user_id"], unique=False)
    op.create_index(op.f("ix_diary_note_claim_id"), "diary_note", ["claim_id"], unique=False)
    op.create_index(op.f("ix_diary_note_noted_at"), "diary_note", ["noted_at"], unique=False)

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON diary_note TO lineworker_app")
    # The identity sequence is new; Story 1.2's blanket sequence grant ran
    # before it existed, so it has to be repeated for it (0010's note).
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lineworker_app")


def downgrade() -> None:
    op.drop_index(op.f("ix_diary_note_noted_at"), table_name="diary_note")
    op.drop_index(op.f("ix_diary_note_claim_id"), table_name="diary_note")
    op.drop_index(op.f("ix_diary_note_app_user_id"), table_name="diary_note")
    op.drop_table("diary_note")
