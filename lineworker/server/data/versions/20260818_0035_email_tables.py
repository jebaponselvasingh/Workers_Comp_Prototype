"""email_template + email_log — the diary aggregate's last two tables (4.3).

Structure only; 0036 seeds the six templates. The split is the one 0032/0033,
0026/0027, 0020/0021, 0017/0018, 0014/0015, 0010/0011 and 0003/0004 already
use, and it keeps `downgrade()` honest: dropping the table takes its rows with
it, and re-seeding is a revision of its own rather than an edit to a structural
migration.

**Two tables in one revision**, unlike the meeting/diary-note pair, because
`email_log.template_id` is a foreign key into `email_template`: they cannot be
created independently, and splitting them would produce a revision whose
`upgrade()` leaves a dangling constraint.

**One native enum, not two.** `priority` is a database type because a column
holds one and the three values are the composer's whole vocabulary.
`recipients` and `default_recipients` are JSONB and their six values are
enforced by the command rather than by the database — 0032's ruling for
`meeting.participants`, and for its reasons: no `ARRAY` column exists anywhere
in this schema, and a CHECK constraining the JSONB to known strings would be a
second copy of `MeetingParticipant` written in SQL, in a migration nobody
re-reads, free to drift from the enum the command validates against. The
failure it would catch (a writer other than `services/claims/emails.py`) is the
one AD-12 closes by where the code lives.

The member tuple below is written out literally rather than imported from
`data/models/enums.py`, which is the rule 0013, 0014, 0026 and 0032
established: a migration is a frozen historical record, and one that read a
live constant would silently change what it did when somebody reordered that
constant. The order decides the type's label order in PostgreSQL;
`tests/test_emails.py` asserts the tuple against `EmailPriority`.

**No `version` column on either table.** `email_template` is reference data
written by a migration and read-only at runtime (`GlossaryTerm`'s and
`RuleDocument`'s shape); `email_log` is append-only with no edit and no delete
command, so nothing is ever read-modify-written and there is no concurrent
writer for a compare-and-swap to arbitrate. `diary_note` (0034),
`timeline_event` (0010) and `audit_event` (0003) are the same shape for the
same reason, and the write-concurrency convention names append-only stores as
exempt outright.

**Two indexes on `email_log`, and each earns its place.**

`ix_email_log_app_user_id_sent_at_id` is `(app_user_id, sent_at DESC, id DESC)`
and it is the *only* index the list read wants. There is one query against this
table — `WHERE app_user_id = :me ORDER BY sent_at DESC, id DESC` with a
`(sent_at, id) < (:last_sent_at, :last_id)` keyset, and `COUNT(*)` over the same
predicate — and a composite in the ordering's own direction serves the filter,
the sort and the keyset comparison from one structure: PostgreSQL walks the
caller's slice backwards from the cursor's position and stops at `LIMIT`, with
no sort node at all. Three separate single-column indexes could not do that
between them, which an earlier draft of this docstring claimed the `sent_at` one
did; a single-column index on `sent_at` is not usable for a query whose leading
predicate is on another column, so the plan for every page was a scan of the
caller's rows plus a sort of them. The two single-column indexes it replaces are
gone: `(app_user_id)` is redundant as a prefix of this one, and `(sent_at)`
served nothing — no query filters or orders by the timestamp across every
sender.

`ix_email_log_claim_id` stays, and its reason is **not** the projection: the
card's claim reference joins `claim` by its primary key, which needs no index
here. It is that PostgreSQL does not index a referencing column automatically,
so without it every `DELETE FROM claim` — Story 8.1's purge cascade — scans this
whole table once per claim to check the constraint.

`template_id` gets none. It is the same kind of FK, and the asymmetry is
deliberate: `email_template` holds six seeded rows that no purge deletes, so
there is no delete to make the scan happen.

**`claim_id` is nullable**, per the ERD's `CLAIM |o--o{ EMAIL_LOG`. The six
templates are claim-aware and refuse to merge without one, but free composition
is legal with nothing selected. **`template_id` is nullable** for the same
reason from the other side — the ERD's `EMAIL_TEMPLATE ||--o{ EMAIL_LOG` — and
it is a real FK so that a bad key is refused rather than nulled.

**`ON DELETE` is left to the FK's default**, 0032's, 0034's and `photo`'s
ruling: a logged email whose claim is gone is PHI with nothing to scope it by,
and Epic 8's purge cascade is where the deletion order is decided. Until then
the constraint's job is to make an orphan impossible rather than to make one
silently disappear.

Grants (AD-4). `email_log` takes the full CRUD grant the other domain tables
hold, and the two halves are wanted for different reasons: the command INSERTs
at runtime, and Story 8.1's PHI purge has to be able to DELETE these rows —
which is why it is not narrowed to `SELECT, INSERT` even though no application
code updates or deletes one. `email_template` takes the same grant rather than
`SELECT` alone, and that is a deliberate divergence from `glossary_term` and
`rule_document`: those hold no PHI and nothing will ever purge them, whereas a
template's *text* is the source of every merged body in `email_log` and 8.1's
cascade should not discover that it cannot touch this table. That
`services/claims/emails.py` is the *only* application writer is a design rule
enforced by where the code lives, not by the grant (0010's argument for
`timeline_event`).

Revision ID: 0035_email_tables
Revises: 0034_diary_note
Create Date: 2026-08-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035_email_tables"
down_revision: str | None = "0034_diary_note"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The three priorities, in the composer select's option order.
#: [Source: docs/Workers_Comp_Prototype.html line 612]
EMAIL_PRIORITIES: tuple[str, ...] = ("normal", "high", "urgent")

EMAIL_PRIORITY_ENUM = "email_priority"

#: `(app_user_id, sent_at DESC, id DESC)` — the sent log's whole access pattern
#: in one index. Named as a constant so `downgrade()` and `data/models/core.py`
#: spell it the same way; `op.f()` is not used because the name is not one
#: Alembic's convention would have generated for a mixed-direction composite.
EMAIL_LOG_LIST_INDEX = "ix_email_log_app_user_id_sent_at_id"


def upgrade() -> None:
    op.create_table(
        "email_template",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("template_key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("subject_template", sa.Text(), nullable=False),
        sa.Column("body_template", sa.Text(), nullable=False),
        sa.Column("default_recipients", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_template")),
        sa.UniqueConstraint("template_key", name=op.f("uq_email_template_template_key")),
    )

    op.create_table(
        "email_log",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("app_user_id", sa.Integer(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=True),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        # The type is created by `create_table` as a side effect of the column,
        # exactly as 0010 creates `doc_type`, 0026 the four financial ones and
        # 0032 `meeting_type`. `downgrade()` has to drop it by hand — see below.
        sa.Column(
            "priority",
            sa.Enum(*EMAIL_PRIORITIES, name=EMAIL_PRIORITY_ENUM),
            nullable=False,
        ),
        sa.Column("recipients", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            # `now()` rather than nothing, 0008's rule: a NOT NULL timestamp
            # with no default is a trap for the first INSERT that omits it. The
            # command supplies the value so that the row and its audit event
            # carry one instant, and this default is what catches anything that
            # does not.
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["app_user_id"], ["app_user.id"], name=op.f("fk_email_log_app_user_id_app_user")
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claim.id"], name=op.f("fk_email_log_claim_id_claim")
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["email_template.id"],
            name=op.f("fk_email_log_template_id_email_template"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_log")),
    )
    op.create_index(op.f("ix_email_log_claim_id"), "email_log", ["claim_id"], unique=False)
    # The list's own index. `sa.text` for the two descending members because a
    # bare column name in `create_index` is ascending and a mixed-direction
    # index is only usable by a scan running in one of its two directions — the
    # keyset walks `(sent_at DESC, id DESC)`, so that is what is stored.
    op.create_index(
        EMAIL_LOG_LIST_INDEX,
        "email_log",
        ["app_user_id", sa.text("sent_at DESC"), sa.text("id DESC")],
        unique=False,
    )

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON email_template TO lineworker_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON email_log TO lineworker_app")
    # The identity sequences are new; Story 1.2's blanket sequence grant ran
    # before they existed, so it has to be repeated for them (0010's note).
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lineworker_app")


def downgrade() -> None:
    op.drop_index(EMAIL_LOG_LIST_INDEX, table_name="email_log")
    op.drop_index(op.f("ix_email_log_claim_id"), table_name="email_log")
    # The child first: `email_log.template_id` references `email_template`, and
    # dropping the parent while the constraint stands fails.
    op.drop_table("email_log")
    op.drop_table("email_template")

    # `op.drop_table` leaves a native enum behind — it is a schema object in its
    # own right, and a re-upgrade would fail on "type … already exists". 0010
    # learned this about `doc_type`, 0013 about `recovery_window`, 0014 about
    # `body_region`, 0026 about the four financial types and 0032 about
    # `meeting_type`.
    sa.Enum(name=EMAIL_PRIORITY_ENUM).drop(op.get_bind(), checkfirst=True)
