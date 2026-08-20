"""The LangGraph checkpoint tables, vendored — plus `copilot_thread` (Story 6.3).

Two things at once, and they are here together because neither is usable
without the other: the tables the `AsyncPostgresSaver` writes conversations
into, and the small append-only table that mints and enumerates the threads
those conversations hang off.

## This is AD-3's registered exception, and the DDL below is frozen

Every other table in this database is declared as an ORM model and created by
a migration written from it. These four are not. `checkpoints`,
`checkpoint_blobs`, `checkpoint_writes` and `checkpoint_migrations` belong to
`langgraph-checkpoint-postgres`; their shape is the vendor's, their contents
are written by the saver alone, and **no application code reads or writes them
directly** — no model class, no repository, no raw SQL anywhere under
`server/`. The history endpoint asks the saver; it does not select from
`checkpoints`.

**Pinned to LangGraph 1.2.11 / langgraph-checkpoint-postgres 3.1.2**, which is
the range `pyproject.toml` resolves. The SQL below was produced by running
`AsyncPostgresSaver.setup()` against a throwaway database on that exact
version and freezing the result. **Upgrading LangGraph requires a new
migration**: if a future version's `setup()` would add a column, an index or a
table, this migration will not have created it, and the saver will fail at
runtime rather than at deploy time. `tests/test_copilot_migration.py` compares
the frozen statements against the installed package's own migration list, so
a bump that changes the DDL fails the suite instead of production.

**`saver.setup()` is never called in prod, and never from application code.**
It issues DDL, which means the application role would need schema-modification
rights it deliberately does not have, and it would run on every boot against a
database Alembic already owns. The scratch script that generated this SQL is a
developer artefact and is not in the tree.

### Two guards, because vendored DDL has two failure modes nothing else here has

`upgrade()` **refuses a database that already has the saver's tables**. Every
statement below says `IF NOT EXISTS`, so an upgrade over tables somebody created
with `saver.setup()` would do nothing and then stamp `checkpoint_migrations`
with ten rows claiming the vendor's steps had been applied — against a schema
nobody checked. That is a lie a deployment cannot see.

`downgrade()` **refuses to drop tables that still hold conversations**, naming
the row counts. A checkpoint is a transcript about an injured worker (AD-11), so
these four `DROP TABLE`s are the only irreversible loss of PHI any migration in
this tree can cause. Both guards arrived with the review of Story 6.3.

### Two edits to the vendor's SQL, both forced, both mechanical

1. **`CREATE INDEX CONCURRENTLY` becomes `CREATE INDEX`.** Alembic runs a
   migration inside a transaction and PostgreSQL refuses `CONCURRENTLY` there.
   The vendor uses it because `setup()` may run against a live database with
   rows in it; this migration runs against tables it has just created in the
   same transaction, where there is nothing to lock and nothing to block.
2. **`checkpoint_migrations` is seeded with the ten version rows `setup()`
   would have written.** Without them a `setup()` run in some future
   developer's scratch would try to re-apply statements against tables that
   already exist — mostly `IF NOT EXISTS` no-ops, but the `ALTER TABLE …` ones
   are not, and a half-applied vendor migration is exactly the state this
   freeze exists to make impossible.

Everything else is the vendor's text verbatim, including its constraint names.
Those are PostgreSQL's defaults (`checkpoints_pkey`, not `pk_checkpoints`) and
they deliberately do **not** follow `data/models/base.NAMING_CONVENTION`: the
convention exists so that migrations *this project writes* are reviewable, and
renaming a vendor's constraint would be this project asserting ownership of a
schema it has just declared it does not own.

### …and `data/env.py` has to know

`target_metadata = Base.metadata` with no filter, and `alembic check` is in the
CI gate. A table that exists in the database and has no ORM model reads to
autogenerate as drift, so without an exclusion the very next `alembic check`
proposes `drop_table` for all four of these and fails. `data/env.py` carries an
`include_name`/`include_object` pair naming the same objects, written out there
rather than imported from here — a migration is a frozen historical record and
must not import live code, and `data/versions/` is not a package for the
inverse to be possible either. `tests/test_copilot_migration.py` asserts the two
lists agree, which is how `INSIGHT_KINDS` is kept honest in 0042.

## `copilot_thread`

The story permits a bookkeeping table "if it proves necessary", and it is,
twice. Sequence minting needs a uniqueness arbiter — `(claim, user, seq)` is
the thread key and two "new conversation" clicks racing must not both mint
`seq 3`. And listing a claim's threads needs enumeration. Both are ordinary
scoped SQL against this table and both would be reads of the saver's tables
otherwise, which AD-3 forbids. Keeping the saver's tables genuinely untouched
is what the table buys.

## No `version` column

AD-4's compare-and-swap arbitrates concurrent writers of a **mutable** row, and
this table has no mutation at all. A row is inserted when a conversation
starts and is never updated: the thread id is derived from the three columns
beside it, so there is no field whose value could change without the row
becoming a different thread. Read-only-ness of a superseded thread is not a
column either — it is `seq < max(seq)`, computed from the rows.

That makes this the append-only case rather than the derived-data one:
`AuditEvent` and `TimelineEvent` reach the same conclusion from the same
direction, and `ClaimEmbedding`/`AiInsight` reach it from the other. The
corollary is the part worth stating, because an unexplained missing `version`
is indistinguishable from an oversight: there is no `expectedVersion` to send
back because there is no edit affordance to send it from, and a story that
wanted a handler to rename or re-target a thread would be proposing that a
conversation's identity become editable, which would silently re-key every
checkpoint hanging off it.

## Grants

`SELECT, INSERT, UPDATE, DELETE` on all five tables.

The saver genuinely needs all four verbs on its own: it inserts checkpoints,
upserts blobs and writes, and deletes a thread's rows when one is discarded.
`copilot_thread` needs `SELECT` and `INSERT` for the application, and `DELETE`
is granted for the reason 0035, 0040 and 0042 each argue: these tables are
PHI-class under AD-11 — a checkpoint is a transcript about an injured worker —
and Story 8.1's purge cascade should not discover at runtime that it cannot
touch a table. `UPDATE` on `copilot_thread` is granted for symmetry with the
other four rather than because anything uses it; that no application code
updates the row is a design rule enforced by where the code lives, not by the
grant (0010's argument for `timeline_event`).

The blanket sequence grant is repeated because `copilot_thread`'s identity
sequence is new and Story 1.2's grant ran before it existed (0010's note). The
checkpoint tables have no sequences — every key in them is supplied by the
saver — and no enum types either, so there is no `GRANT USAGE ON TYPE` here;
`tests/test_copilot_migration.py` asserts that the vendored DDL creates none,
so the day it does the omission is a failing test rather than a runtime error.

Revision ID: 0043_copilot_threads
Revises: 0042_ai_insight
Create Date: 2026-08-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_copilot_threads"
down_revision: str | None = "0042_ai_insight"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The LangGraph version this DDL was frozen from. Compared against the
#: installed distribution in `tests/test_copilot_migration.py`, so a bump is a
#: failing test with this line named rather than a runtime surprise.
PINNED_LANGGRAPH_CHECKPOINT_POSTGRES = "3.1.2"

#: The vendor's tables, in creation order. A module constant rather than four
#: string literals below because `tests/test_copilot_migration.py` compares it
#: against `data/env.py::CHECKPOINT_OBJECTS`, which is what keeps the
#: autogenerate exclusion and this migration from disagreeing about which tables
#: are the saver's.
CHECKPOINT_TABLES: tuple[str, ...] = (
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)

#: The vendor's indexes, named here for `data/env.py`'s benefit for the same
#: reason the tables are: an index on an excluded table is still an object
#: autogenerate would notice.
CHECKPOINT_INDEXES: tuple[str, ...] = (
    "checkpoints_thread_id_idx",
    "checkpoint_blobs_thread_id_idx",
    "checkpoint_writes_thread_id_idx",
)

#: How many rows `setup()` writes into `checkpoint_migrations` on the pinned
#: version — one per statement in the vendor's own migration list, `0 … 9`.
CHECKPOINT_MIGRATION_VERSIONS = 10

#: `AsyncPostgresSaver.setup()`'s DDL for the pinned version, frozen.
#:
#: Verbatim from the vendor apart from the two edits the module docstring
#: names. Kept as one list of statements rather than as `op.create_table`
#: calls, because the point of a vendored migration is that it is the
#: *vendor's* SQL — a hand-translation into SQLAlchemy constructs would be this
#: project's rendering of somebody else's schema, and a rendering can be subtly
#: wrong in ways a comparison against the source cannot catch.
CHECKPOINT_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS checkpoint_migrations (
        v INTEGER PRIMARY KEY
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS checkpoints (
        thread_id TEXT NOT NULL,
        checkpoint_ns TEXT NOT NULL DEFAULT '',
        checkpoint_id TEXT NOT NULL,
        parent_checkpoint_id TEXT,
        type TEXT,
        checkpoint JSONB NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}',
        PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS checkpoint_blobs (
        thread_id TEXT NOT NULL,
        checkpoint_ns TEXT NOT NULL DEFAULT '',
        channel TEXT NOT NULL,
        version TEXT NOT NULL,
        type TEXT NOT NULL,
        blob BYTEA,
        PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS checkpoint_writes (
        thread_id TEXT NOT NULL,
        checkpoint_ns TEXT NOT NULL DEFAULT '',
        checkpoint_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        idx INTEGER NOT NULL,
        channel TEXT NOT NULL,
        type TEXT,
        blob BYTEA NOT NULL,
        task_path TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
    );
    """,
    # `CONCURRENTLY` removed — see the module docstring. These run against
    # tables created moments ago in this same transaction.
    "CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id);",
    "CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id);",
    "CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id);",
)


#: The checkpoint tables that hold conversations, as opposed to bookkeeping.
#:
#: `checkpoint_migrations` is excluded: it holds ten integers saying which of the
#: vendor's own steps have been applied, and `upgrade()` writes them itself. Its
#: rows are not PHI and its presence is not evidence of a conversation.
CHECKPOINT_DATA_TABLES: tuple[str, ...] = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)


def _existing_checkpoint_tables() -> list[str]:
    """Which of the vendor's tables this database already has. `to_regclass`, not a catalog join."""
    bind = op.get_bind()
    return [
        table
        for table in CHECKPOINT_TABLES
        if bind.scalar(sa.text("SELECT to_regclass(:name)"), {"name": table}) is not None
    ]


def upgrade() -> None:
    # **Refuse a database that already has the saver's tables.**
    #
    # Every statement below says `IF NOT EXISTS` — the vendor's own text, kept
    # verbatim — so an upgrade against a database where somebody once ran
    # `saver.setup()` would silently do nothing, then stamp
    # `checkpoint_migrations` with ten rows saying the vendor's steps had been
    # applied. If those pre-existing tables were at an *older* vendor schema,
    # every one of the ten rows would be a lie, and the next `setup()` — or the
    # next saver that trusted them — would skip exactly the `ALTER TABLE` steps
    # that were missing. That is a corruption a deployment cannot see, so it
    # fails here instead (review of Story 6.3).
    existing = _existing_checkpoint_tables()
    if existing:
        raise RuntimeError(
            "this database already has LangGraph checkpoint tables "
            f"({', '.join(existing)}), which migration 0043 vendors and owns. "
            "They were created outside Alembic — most likely by a saver.setup() "
            "run — and their schema may not be the one this migration freezes. "
            "Inspect them, migrate or drop them deliberately, and re-run: "
            "applying 0043 over them would mark ten vendor migrations applied "
            "against a schema nobody has checked."
        )

    for statement in CHECKPOINT_DDL:
        op.execute(statement)

    # The version rows `setup()` would have written. See the module docstring
    # on why a future scratch `setup()` must find them already there.
    op.execute(
        "INSERT INTO checkpoint_migrations (v) "
        f"SELECT generate_series(0, {CHECKPOINT_MIGRATION_VERSIONS - 1}) "
        "ON CONFLICT (v) DO NOTHING"
    )

    op.create_table(
        "copilot_thread",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        # The saver's key, minted by `agents/threads.py` from the three columns
        # below and never supplied by a client. Text rather than an integer
        # because that is what `checkpoints.thread_id` is, and unique because
        # two rows sharing one would be two conversations sharing one
        # transcript.
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        # 1, 2, 3 … per (claim, user). "New conversation" mints the next one and
        # the previous becomes read-only history — which is a comparison
        # against `max(conversation_seq)`, not a column.
        sa.Column("conversation_seq", sa.Integer(), nullable=False),
        # NOT NULL and supplied by the command rather than defaulted by the
        # database, `ai_insight.generated_at`'s rule: the instant is the
        # minting run's own, so the row and the log line that records it carry
        # one timestamp.
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claim.id"], name=op.f("fk_copilot_thread_claim_id_claim")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name=op.f("fk_copilot_thread_user_id_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_copilot_thread")),
        # The thread key. Its backing index is what makes `select_thread` — the
        # read on the hot path of every run — a lookup rather than a scan, and
        # it doubles as the index the `thread_id` foreign-key-shaped join from
        # the saver's tables would want if there were one.
        sa.UniqueConstraint("thread_id", name=op.f("uq_copilot_thread_thread_id")),
        # The uniqueness arbiter minting relies on: `INSERT … SELECT
        # max(seq) + 1` races are resolved here rather than by a lock, so two
        # simultaneous "new conversation" clicks produce one new thread and one
        # refused insert instead of two threads at the same sequence.
        #
        # It also indexes `(claim_id, user_id)` as a prefix, which is exactly
        # the list read, and gives the `claim_id` foreign key the index
        # PostgreSQL does not create for it — without which Story 8.1's purge
        # cascade would scan this table once per deleted claim (0035's reason).
        sa.UniqueConstraint(
            "claim_id",
            "user_id",
            "conversation_seq",
            name=op.f("uq_copilot_thread_claim_id"),
        ),
    )
    # The `user_id` foreign key's own index, which the composite unique above
    # does not provide (it leads with `claim_id`). Story 8.1 purges by claim,
    # but a user is deletable in principle and the same scan argument applies.
    op.create_index(op.f("ix_copilot_thread_user_id"), "copilot_thread", ["user_id"], unique=False)

    for table in (*CHECKPOINT_TABLES, "copilot_thread"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO lineworker_app")
    # `copilot_thread`'s identity sequence is new; Story 1.2's blanket grant ran
    # before it existed, so it has to be repeated for it (0010's note).
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lineworker_app")


def downgrade() -> None:
    # **A downgrade will not destroy conversations** (AD-11).
    #
    # A checkpoint is a transcript quoting an injured worker's diagnosis, wage
    # and reserve, and the four `DROP TABLE`s below are irreversible, silent and
    # one `alembic downgrade` away. Every other migration in this tree drops
    # tables it created moments earlier in the same deployment; this one can be
    # asked to drop months of PHI, so it asks first — by refusing, because there
    # is nobody to ask at the point a migration runs.
    #
    # Deliberately no `--force` and no environment variable. An operator who
    # genuinely means it deletes the rows first, which is an explicit act
    # against a named table, and Epic 8 owns the purge that does it properly.
    bind = op.get_bind()
    present = set(_existing_checkpoint_tables())
    counts = {
        table: bind.scalar(sa.text(f"SELECT count(*) FROM {table}"))  # noqa: S608 - fixed names
        for table in CHECKPOINT_DATA_TABLES
        if table in present
    }
    occupied = {table: rows for table, rows in counts.items() if rows}
    if occupied:
        detail = ", ".join(f"{table}: {rows}" for table, rows in sorted(occupied.items()))
        raise RuntimeError(
            "refusing to downgrade 0043: the checkpoint tables still hold "
            f"conversations ({detail}). Those rows are PHI under AD-11 — "
            "transcripts about injured workers — and this downgrade would drop "
            "them with no backup and no audit trail. Purge them deliberately "
            "first (Epic 8 owns the retention cascade), then downgrade."
        )

    op.drop_index(op.f("ix_copilot_thread_user_id"), table_name="copilot_thread")
    op.drop_table("copilot_thread")
    # The vendor's tables, dropped in reverse creation order. No enum types to
    # clean up after — the trap 0010, 0013, 0014, 0026, 0032 and 0042 each
    # record does not bite here, because this DDL creates none. Dropping the
    # tables takes their indexes and primary keys with them.
    for table in reversed(CHECKPOINT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
