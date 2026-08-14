"""payment_schedule_week, bill and expense (Story 3.3, AC 2 and AC 3).

Structure only — 0027 seeds the bills and expenses and 0028 materializes the
schedules, the split 0003/0004, 0006/0007, 0010/0011, 0014/0015, 0017/0018 and
0020/0021 already use. It keeps `downgrade()` honest: dropping a table takes
its rows with it, and re-seeding is a revision of its own rather than an edit
to a structural migration.

**Three tables in one revision because they are one surface.** The Bills &
Payments tab reads all three and its financial summary adds them together;
shipping the schedule without the bills would leave the reserve check's second
exposure term unavailable for another migration's worth of history, which is
the state Story 3.2 recorded as a known wrong answer.

**Four enum types, and the reason there are not five.** `schedule_week_status`
already exists as a Python enum (`ScheduleWeekStatus`, declared in Story 3.2
against the column this migration finally creates); `bill_category` and
`expense_category` are two vocabularies because a surgical facility fee and a
mileage reimbursement are not members of one list. But `bill.status` and
`expense.status` share **one** type, `line_item_status`: the states a line item
moves through are identical and the prototype says so by rendering both through
one `billStatusLabel`. Two types holding the same four tokens would be two
places to widen when Story 3.4's approval adds a transition.

The member tuples below are written out literally rather than imported from
`data/models/enums.py`, which is the rule 0013 and 0014 established: a
migration is a frozen historical record, and one that read a live constant
would silently change what it did when somebody reordered that constant. The
order matters concretely — it decides the type's label order in PostgreSQL, so
it is what `ORDER BY status` would sort by — and
`tests/test_financial_tables_migration.py` asserts each tuple against its enum.

**`version` on all three**, unlike `photo` and `treatment_plan_step`. AD-4's
compare-and-swap column arbitrates concurrent writers, and here there will be
some: Story 3.4 moves a week's status and a bill's status through approval
commands. The column arrives with the table rather than with the story that
first writes it, which is the rule `document` and `additional_injury` follow.

**The unique constraint on `(claim_id, week_no)` is load-bearing.** A claim's
schedule is a materialized projection keyed by week, so the command that
refreshes it is an upsert; without the constraint, a regeneration bug would
append a second Wk 3 and the Bills table would render two, with the totals
row quietly disagreeing with the sum of what is on screen.

**No CHECK on `amount_cents`.** A zero-amount week is real (a claim whose
weekly benefit clamps to a state minimum of zero would be a data error caught
by 3.1's refusal, not here), and a negative one is not reachable from the
generator. The constraint that *is* worth having is the uniqueness above,
because it is the one an ordinary bug can violate.

**`ON DELETE` is left to the FK's default**, `photo`'s ruling: a financial row
whose claim is gone is PHI with nothing to scope it by, and Epic 8's purge
cascade is where the deletion order is decided. Until then the constraint's job
is to make an orphan impossible rather than to make one silently disappear.

Grants (AD-4). All three tables get the full CRUD grant `lineworker_app` holds
on the other domain tables — the schedule needs INSERT and UPDATE at runtime
(the materialization command) and DELETE for the surplus-week case, and the two
line-item tables need UPDATE for Story 3.4's approval. The restriction that
`services/financials` is the only writer is a design rule enforced by where the
code lives, not by the grant (0010's argument for `timeline_event`), and Story
8.1's PHI purge has to be able to delete these rows.

Revision ID: 0026_financial_tables
Revises: 0025_reserve_bands
Create Date: 2026-08-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_financial_tables"
down_revision: str | None = "0025_reserve_bands"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The five states one week of the schedule can hold, in the order a week
#: moves through them — the prototype's `SCHEDULE_STATUS_LABEL` order.
#: [Source: docs/Workers_Comp_Prototype.html line 844]
SCHEDULE_WEEK_STATUSES: tuple[str, ...] = (
    "pending_approval",
    "due_this_week",
    "upcoming",
    "payment_scheduled",
    "paid",
)

#: The four states a bill or an expense can hold. `pending_submission` and
#: `payment_scheduled` are deliberately distinct — the prototype labels the
#: first one "Payment Scheduled", conflating a bill nobody has filed with one
#: a handler has approved into a batch. See `LineItemStatus`.
LINE_ITEM_STATUSES: tuple[str, ...] = (
    "pending_submission",
    "under_review",
    "payment_scheduled",
    "paid",
)

#: The six medical-bill categories, in the prototype's emission order.
#: [Source: docs/Workers_Comp_Prototype.html lines 774-780]
BILL_CATEGORIES: tuple[str, ...] = (
    "initial_treatment",
    "surgery_facility",
    "imaging",
    "physical_therapy",
    "follow_up",
    "pharmacy",
)

#: The five claim-expense categories, in the prototype's emission order.
#: [Source: docs/Workers_Comp_Prototype.html lines 793-798]
EXPENSE_CATEGORIES: tuple[str, ...] = (
    "mileage_travel",
    "dme",
    "prosthetic_assistive",
    "home_workstation_mod",
    "misc",
)

SCHEDULE_STATUS_ENUM = "schedule_week_status"
LINE_ITEM_STATUS_ENUM = "line_item_status"
BILL_CATEGORY_ENUM = "bill_category"
EXPENSE_CATEGORY_ENUM = "expense_category"


def upgrade() -> None:
    op.create_table(
        "payment_schedule_week",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("week_no", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        # The type is created by `create_table` as a side effect of the
        # column, exactly as 0010 creates `doc_type` and 0014 `body_region`.
        # `downgrade()` has to drop it by hand — see the note there.
        sa.Column(
            "status",
            sa.Enum(*SCHEDULE_WEEK_STATUSES, name=SCHEDULE_STATUS_ENUM),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claim.id"], name=op.f("fk_payment_schedule_week_claim_id_claim")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_schedule_week")),
        # The rows *are* the projection, keyed by week. Without this a
        # regeneration bug appends a second Wk 3 and the Bills table renders
        # two of them under a total that counts it once.
        sa.UniqueConstraint("claim_id", "week_no", name=op.f("uq_payment_schedule_week_claim_id")),
    )
    op.create_index(
        op.f("ix_payment_schedule_week_claim_id"),
        "payment_schedule_week",
        ["claim_id"],
        unique=False,
    )

    op.create_table(
        "bill",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.Enum(*BILL_CATEGORIES, name=BILL_CATEGORY_ENUM), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column(
            "status", sa.Enum(*LINE_ITEM_STATUSES, name=LINE_ITEM_STATUS_ENUM), nullable=False
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["claim_id"], ["claim.id"], name=op.f("fk_bill_claim_id_claim")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bill")),
    )
    op.create_index(op.f("ix_bill_claim_id"), "bill", ["claim_id"], unique=False)

    op.create_table(
        "expense",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column(
            "category", sa.Enum(*EXPENSE_CATEGORIES, name=EXPENSE_CATEGORY_ENUM), nullable=False
        ),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        # The type already exists — `bill` created it above. `create_type`
        # is False so this column reuses it instead of failing on "type
        # line_item_status already exists", which is the shared-enum half of
        # the decision the module docstring argues for.
        sa.Column(
            "status",
            postgresql.ENUM(*LINE_ITEM_STATUSES, name=LINE_ITEM_STATUS_ENUM, create_type=False),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["claim_id"], ["claim.id"], name=op.f("fk_expense_claim_id_claim")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expense")),
    )
    op.create_index(op.f("ix_expense_claim_id"), "expense", ["claim_id"], unique=False)

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON payment_schedule_week, bill, expense TO lineworker_app"
    )
    # The identity sequences are new; Story 1.2's blanket sequence grant ran
    # before they existed, so it has to be repeated for them (0010's note).
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lineworker_app")


def downgrade() -> None:
    op.drop_index(op.f("ix_expense_claim_id"), table_name="expense")
    op.drop_table("expense")
    op.drop_index(op.f("ix_bill_claim_id"), table_name="bill")
    op.drop_table("bill")
    op.drop_index(op.f("ix_payment_schedule_week_claim_id"), table_name="payment_schedule_week")
    op.drop_table("payment_schedule_week")

    # `op.drop_table` leaves a native enum behind — it is a schema object in
    # its own right, and a re-upgrade would fail on "type … already exists".
    # 0010 learned this about `doc_type`, 0013 about `recovery_window` and
    # 0014 about `body_region`. `line_item_status` is dropped once although
    # two tables used it, which is what `checkfirst` makes safe.
    for name in (
        EXPENSE_CATEGORY_ENUM,
        BILL_CATEGORY_ENUM,
        LINE_ITEM_STATUS_ENUM,
        SCHEDULE_STATUS_ENUM,
    ):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
