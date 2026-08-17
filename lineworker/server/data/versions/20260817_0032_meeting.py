"""meeting — the first table of the diary aggregate (Story 4.1, AC 3).

Structure only; 0033 seeds the two demo meetings per handler persona. The
split is the one 0026/0027, 0020/0021, 0017/0018, 0014/0015, 0010/0011 and
0003/0004 already use, and it keeps `downgrade()` honest: dropping the table
takes its rows with it, and re-seeding is a revision of its own rather than an
edit to a structural migration.

**One native enum, not two.** `meeting_type` is a database type because a
column holds one and the ten values are the scheduler's whole vocabulary.
`participants` is JSONB and its six values are enforced by the command rather
than by the database — the story's ruling that a fixed six-value list read
whole with its meeting does not earn a join table, and PostgreSQL has no
array-of-enum constraint worth the migration tax here. The trade-off is stated
where it is made (`MeetingParticipant`) rather than left to be inferred from
this file.

The member tuple below is written out literally rather than imported from
`data/models/enums.py`, which is the rule 0013, 0014 and 0026 established: a
migration is a frozen historical record, and one that read a live constant
would silently change what it did when somebody reordered that constant. The
order decides the type's label order in PostgreSQL;
`tests/test_meeting_seed.py` asserts the tuple against `MeetingType`.

**Two indexes, both on foreign keys, and both earn their place.** Every read
of this table filters on `app_user_id` (the list is caller-scoped — a meeting
belongs to the handler who holds it), and the scope predicate additionally
resolves `claim_id` against the caller's employers. Without the second, that
subquery would drive a sequential scan of every handler's meetings on every
list.

**`claim_id` is nullable**, per the ERD's `CLAIM |o--o{ MEETING`. The
scheduler always opens from a selected claim; the column admits null so that a
touchpoint which is genuinely not about one file is recordable rather than
unrepresented.

**No CHECK on `participants`.** A JSONB column can be constrained to an array
of known strings, and the constraint that could be written here would be a
second copy of `MeetingParticipant` — in SQL, in a migration nobody re-reads,
free to drift from the enum the command validates against. The failure it
would catch (a writer other than `services/claims/meetings.py`) is the one
AD-12 closes by where the code lives. `state_rate_schedule`'s bounds CHECK is
the opposite case and says why: an inverted min/max is reachable from ordinary
data, an unknown participant token is not.

**`ON DELETE` is left to the FK's default**, `photo`'s and 0026's ruling: a
meeting whose claim is gone is PHI with nothing to scope it by, and Epic 8's
purge cascade is where the deletion order is decided. Until then the
constraint's job is to make an orphan impossible rather than to make one
silently disappear.

Grants (AD-4). The full CRUD grant `lineworker_app` holds on the other domain
tables: the three commands insert, update and delete at runtime, and Story
8.1's PHI purge has to be able to remove these rows. That
`services/claims/meetings.py` is the *only* writer is a design rule enforced
by where the code lives, not by the grant — 0010's argument for
`timeline_event`.

Revision ID: 0032_meeting
Revises: 0031_worklist_action_rules
Create Date: 2026-08-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032_meeting"
down_revision: str | None = "0031_worklist_action_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The ten meeting types, in the scheduler modal's option order.
#: [Source: docs/Workers_Comp_Prototype.html lines 541-551]
MEETING_TYPES: tuple[str, ...] = (
    "three_point_contact_initial",
    "rtw_conference",
    "ncm_care_coordination",
    "ime_preparation",
    "settlement_discussion",
    "physician_consultation",
    "employer_accommodation_review",
    "litigation_prep",
    "claim_review_supervisor",
    "other",
)

MEETING_TYPE_ENUM = "meeting_type"


def upgrade() -> None:
    op.create_table(
        "meeting",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("app_user_id", sa.Integer(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=True),
        # The type is created by `create_table` as a side effect of the
        # column, exactly as 0010 creates `doc_type` and 0026 the four
        # financial ones. `downgrade()` has to drop it by hand — see below.
        sa.Column(
            "meeting_type",
            sa.Enum(*MEETING_TYPES, name=MEETING_TYPE_ENUM),
            nullable=False,
        ),
        sa.Column("meeting_date", sa.Date(), nullable=False),
        sa.Column("meeting_time", sa.Time(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("participants", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_done", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            # `now()` rather than a caller-supplied value, 0008's rule: a NOT
            # NULL timestamp with no default is a trap for the first INSERT
            # that omits it, and when a meeting was scheduled is the
            # database's fact rather than the command's.
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["app_user_id"], ["app_user.id"], name=op.f("fk_meeting_app_user_id_app_user")
        ),
        sa.ForeignKeyConstraint(["claim_id"], ["claim.id"], name=op.f("fk_meeting_claim_id_claim")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meeting")),
    )
    op.create_index(op.f("ix_meeting_app_user_id"), "meeting", ["app_user_id"], unique=False)
    op.create_index(op.f("ix_meeting_claim_id"), "meeting", ["claim_id"], unique=False)

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON meeting TO lineworker_app")
    # The identity sequence is new; Story 1.2's blanket sequence grant ran
    # before it existed, so it has to be repeated for it (0010's note).
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lineworker_app")


def downgrade() -> None:
    op.drop_index(op.f("ix_meeting_claim_id"), table_name="meeting")
    op.drop_index(op.f("ix_meeting_app_user_id"), table_name="meeting")
    op.drop_table("meeting")

    # `op.drop_table` leaves a native enum behind — it is a schema object in
    # its own right, and a re-upgrade would fail on "type … already exists".
    # 0010 learned this about `doc_type`, 0013 about `recovery_window`, 0014
    # about `body_region` and 0026 about the four financial types.
    sa.Enum(name=MEETING_TYPE_ENUM).drop(op.get_bind(), checkfirst=True)
