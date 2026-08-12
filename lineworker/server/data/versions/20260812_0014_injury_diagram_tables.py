"""Injury-diagram structure: additional_injury, treatment_plan_step, prognosis
(Story 2.4, AC 1 and AC 2).

Structure only — 0015 puts the rows in, the split 0003/0004, 0006/0007,
0008/0009 and 0010/0011 already use.

**Three things arrive together because one screen needs all three.** The
Injury Diagram tab draws a body map (which needs the secondary injuries),
a numbered treatment plan (which needs its own table) and a prognosis card
(four columns Story 1.2 left in `seed_data.json`'s `deferred` block because
nothing rendered them yet).

**`body_region` is a native enum; `claim.body_key` is still `Text`.** The
asymmetry is deliberate and is argued in `data/models/enums.py`: this story's
task list scopes the enum to the table it creates, and converting a column
two earlier stories own is a migration of its own. Both are validated against
the same eleven members in `services/claims`, so the vocabulary is single
even where its storage is not. `BODY_KEYS` below is written out literally
rather than imported from `BodyRegion`, for the reason 0013 wrote its
recovery-window mapping out: a migration is a historical record, and one that
imported a live constant would silently change what it did when that constant
was reordered.

**The prognosis columns arrive nullable and 0015 makes them NOT NULL.** The
alternative — NOT NULL with a `''` server default — would leave a hundred
claims briefly holding an empty string that reads as a real (blank) prognosis
rather than as an absent one, and would put the honesty of the column in a
default nobody reads. The nullability change belongs with the data that makes
it true.

Grants (AD-4). Both tables get the full CRUD grant `lineworker_app` holds on
the other domain tables. `additional_injury` needs INSERT and DELETE by
definition (that is the whole feature). `treatment_plan_step` is reference
content with no runtime writer this epic and is granted alike, for the reason
0010 gave for `timeline_event`: the restriction is a design rule enforced by
where the code lives, and Story 8.1's PHI purge has to be able to delete
these rows when a claim is purged.

Revision ID: 0014_injury_diagram_tables
Revises: 0013_recovery_window_enum
Create Date: 2026-08-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_injury_diagram_tables"
down_revision: str | None = "0013_recovery_window_enum"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The eleven regions the diagram addresses, in the prototype's order.
#: `tests/test_injury_validation.py` asserts this list is exactly
#: `BodyRegion` — order included, because it is what decides the enum's label
#: order in PostgreSQL — so the literal cannot drift from the enum unnoticed.
#: [Source: docs/Workers_Comp_Prototype.html lines 648-654]
BODY_KEYS: tuple[str, ...] = (
    "head",
    "ears",
    "shoulder_right",
    "shoulder_left",
    "forearm_right",
    "hand_right",
    "hand_left",
    "torso",
    "lumbar",
    "tibia_left",
    "tibia_right",
)

ENUM_NAME = "body_region"

PROGNOSIS_COLUMNS = (
    "prognosis_mmi",
    "prognosis_rtw",
    "prognosis_impairment",
    "prognosis_litigation",
)


def upgrade() -> None:
    op.create_table(
        "additional_injury",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        # The type is created by `create_table` as a side effect of the
        # column, exactly as 0010 creates `doc_type`. `downgrade()` has to
        # drop it by hand — see the note there.
        sa.Column("body_key", sa.Enum(*BODY_KEYS, name=ENUM_NAME), nullable=False),
        sa.Column("body_part", sa.Text(), nullable=False),
        sa.Column("injury_type", sa.Text(), nullable=False),
        sa.Column("severity_score", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        # The command answers 422 for a score outside 0-100; this is what
        # makes "no such row exists" true of the table rather than of the
        # code paths anyone remembered to route through.
        sa.CheckConstraint(
            "severity_score BETWEEN 0 AND 100", name=op.f("ck_additional_injury_ck_severity")
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claim.id"], name=op.f("fk_additional_injury_claim_id_claim")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_additional_injury")),
    )
    op.create_index(
        op.f("ix_additional_injury_claim_id"), "additional_injury", ["claim_id"], unique=False
    )

    op.create_table(
        "treatment_plan_step",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claim.id"], name=op.f("fk_treatment_plan_step_claim_id_claim")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_treatment_plan_step")),
        # The card numbers its rows from `step_no`, so two rows claiming the
        # same step render an ambiguous plan. A re-seed that duplicated a
        # claim's steps fails here rather than on screen.
        sa.UniqueConstraint("claim_id", "step_no", name=op.f("uq_treatment_plan_step_claim_id")),
    )
    op.create_index(
        op.f("ix_treatment_plan_step_claim_id"), "treatment_plan_step", ["claim_id"], unique=False
    )

    for column in PROGNOSIS_COLUMNS:
        op.add_column("claim", sa.Column(column, sa.Text(), nullable=True))

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON additional_injury, treatment_plan_step TO lineworker_app"
    )
    # The identity sequences are new; Story 1.2's blanket sequence grant ran
    # before they existed, so it has to be repeated for them (0010's note).
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lineworker_app")


def downgrade() -> None:
    for column in reversed(PROGNOSIS_COLUMNS):
        op.drop_column("claim", column)

    op.drop_index(op.f("ix_treatment_plan_step_claim_id"), table_name="treatment_plan_step")
    op.drop_table("treatment_plan_step")
    op.drop_index(op.f("ix_additional_injury_claim_id"), table_name="additional_injury")
    op.drop_table("additional_injury")
    # `op.drop_table` leaves the native enum behind — it is a schema object in
    # its own right, and a re-upgrade would fail on "type body_region already
    # exists". 0010 learned this about `doc_type` and 0013 about
    # `recovery_window`.
    sa.Enum(name=ENUM_NAME).drop(op.get_bind(), checkfirst=True)
