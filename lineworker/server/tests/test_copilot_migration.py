"""Migration 0043 — the vendored checkpoint DDL and `copilot_thread` (Story 6.3).

`test_ai_insight_migration.py`'s shape one revision on, with one extra job that
no earlier migration test has had: **this migration vendors somebody else's
schema**, so the tests have to check not only that the tables came out right but
that the frozen SQL still matches the version it was frozen from and that the
autogenerate exclusion is actually keeping `alembic check` quiet.

Four things are asserted here and each fails for a different reason:

1. **The frozen DDL agrees with the installed saver.** A LangGraph bump that
   changes the checkpoint schema fails here, in a test naming the migration,
   rather than at runtime in a container that cannot write a checkpoint.
2. **`data/checkpoint_tables.py`'s list names the same objects as the migration.** Two
   lists exist deliberately (a migration must not import live code, and
   `data/versions/` is not a package for the inverse), so something has to hold
   them together.
3. **`alembic check` is clean, and would not be without the exclusion.** The
   second half is what makes the first non-vacuous: a guard that only ever runs
   against a passing configuration cannot tell "nothing is wrong" from "nothing
   is checked".
4. **The tables, constraints and grants are what the schema needs.**

The migration module is loaded **by path**, `test_ai_insight_migration.py`'s
device: `data/versions/` is Alembic's script directory and is not an importable
package.
"""

import importlib.util
import subprocess
from collections.abc import Iterator
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from data.checkpoint_tables import CHECKPOINT_OBJECTS
from tests.conftest import requires_db, run_alembic

pytestmark = requires_db

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "versions" / "20260820_0043_copilot_threads.py"
)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("_copilot_migration", MIGRATION_PATH)
    assert spec and spec.loader, f"no migration at {MIGRATION_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()


def _normalise(sql: str) -> str:
    """One SQL statement as a comparable string: whitespace collapsed, lowercased.

    The vendor's own formatting and this migration's differ in indentation
    because the frozen copy is indented into a Python tuple. Comparing token
    streams rather than bytes keeps the assertion about *what the SQL says*,
    which is the only thing that can break a deployment.
    """
    return " ".join(sql.lower().split())


# --- the freeze -----------------------------------------------------------
#
# The engine fixture is declared here rather than below because the strongest
# freeze assertion needs a database: it replays the vendor's own `setup()` into
# a scratch schema and diffs.


@pytest.fixture(scope="module")
def migrated(seeded_db_url: str) -> Iterator[sa.Engine]:
    engine = sa.create_engine(
        seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1),
        isolation_level="AUTOCOMMIT",
    )
    yield engine
    engine.dispose()


def test_the_pinned_saver_version_is_the_installed_one() -> None:
    """The migration names a version; that version is what is installed.

    The header says "upgrading LangGraph requires a new migration", and this is
    the tripwire that makes the sentence enforceable rather than advisory. It is
    deliberately an equality on the *patch* version: the checkpoint schema is not
    something the vendor promises to keep stable across patches, and the cost of
    being wrong is a saver writing against columns that do not exist.
    """
    installed = version("langgraph-checkpoint-postgres")
    assert installed == migration.PINNED_LANGGRAPH_CHECKPOINT_POSTGRES, (
        "the installed langgraph-checkpoint-postgres is not the version migration 0043's "
        "DDL was frozen from — re-generate the DDL against a scratch database and write a "
        "new migration; do not edit 0043"
    )


def test_the_frozen_ddl_produces_the_savers_own_schema(migrated: sa.Engine) -> None:
    """The vendor's `setup()`, replayed into a scratch schema, and diffed.

    **A schema comparison rather than a text one, and the difference matters.**
    The vendor's migration list is a *history*: `checkpoint_writes` is created
    without `task_path` and gains it three statements later, and
    `checkpoint_blobs.blob` is created `NOT NULL` and relaxed afterwards. A
    frozen migration creates the **end state** instead of replaying that
    history, so a statement-for-statement comparison would fail on two
    statements that are correct by construction, and it would go on failing
    every time the vendor added another `ALTER`.

    What has to be true is that the tables this project creates are the tables
    the saver would have created. So the vendor's own list is applied to a
    throwaway schema in the same database and the two are reflected and
    compared: table names, column names, column types, nullability, and primary
    keys. A LangGraph bump that adds a column fails here, naming it.

    `CONCURRENTLY` is stripped for the reason the migration strips it — this
    runs inside a transaction — and the vendor's bare `SELECT 1;` placeholder is
    harmless.
    """
    from langgraph.checkpoint.postgres import base

    scratch = "_saver_reference"
    with migrated.connect() as conn:
        conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{scratch}" CASCADE'))
        conn.execute(sa.text(f'CREATE SCHEMA "{scratch}"'))
    try:
        with migrated.connect() as conn:
            # **`search_path` is set and reset on the same connection**, because
            # the engine pools them and an AUTOCOMMIT `SET` survives the
            # `with` block — a leaked `search_path` made every later test in
            # this module see an empty schema. The saver's DDL names its tables
            # unqualified, so pointing the path at the scratch schema is the
            # only way to run the vendor's statements verbatim.
            conn.execute(sa.text(f'SET search_path TO "{scratch}"'))
            try:
                for statement in base.MIGRATIONS:
                    conn.execute(sa.text(statement.replace("CONCURRENTLY", "")))
            finally:
                conn.execute(sa.text("RESET search_path"))

            inspector = sa.inspect(conn)
            for table in migration.CHECKPOINT_TABLES:
                expected = _shape(inspector, table, schema=scratch)
                actual = _shape(inspector, table, schema="public")
                assert actual == expected, (
                    f"the frozen DDL for {table!r} no longer matches what the installed "
                    "saver's own setup() produces — re-generate against a scratch "
                    "database and write a new migration; do not edit 0043"
                )
    finally:
        with migrated.connect() as conn:
            conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{scratch}" CASCADE'))


def _shape(inspector: Any, table: str, *, schema: str) -> dict[str, Any]:
    """One table's comparable shape: columns, their types, nullability, and its PK.

    Deliberately not the whole reflection. Index *names* are the vendor's and
    are identical either way; constraint names are PostgreSQL's defaults in both
    schemas; and comments and storage parameters are noise. What a saver
    actually breaks on is a column that is missing, differently typed, or
    differently nullable.
    """
    columns = {
        column["name"]: (str(column["type"]), bool(column["nullable"]))
        for column in inspector.get_columns(table, schema=schema)
    }
    primary = tuple(inspector.get_pk_constraint(table, schema=schema)["constrained_columns"])
    return {"columns": columns, "primary_key": primary}


def test_the_vendored_ddl_creates_no_enum_type() -> None:
    """The grant block omits `GRANT USAGE ON TYPE`, and this is why that is safe.

    Migration 0042's header records the trap: a type created as a side effect of
    a column has to be dropped by hand and granted by hand. The checkpoint DDL
    creates none — every column is `text`, `integer`, `jsonb` or `bytea` — so
    both omissions are correct. Asserted rather than assumed, because the day a
    vendor upgrade adds an enum the failure would otherwise be a permission
    error in a container.
    """
    joined = " ".join(migration.CHECKPOINT_DDL).lower()
    assert "create type" not in joined
    assert "enum" not in joined


def test_the_env_exclusion_and_the_migration_name_the_same_objects() -> None:
    """Two lists, one meaning — the reason there is a test rather than an import.

    `data/env.py` cannot import from `data/versions/` (not a package) and the
    migration must not import live code (it is a frozen historical record), so
    the names are written twice. `INSIGHT_KINDS` in 0042 is kept honest exactly
    this way.
    """
    vendored = frozenset({*migration.CHECKPOINT_TABLES, *migration.CHECKPOINT_INDEXES})
    assert vendored == CHECKPOINT_OBJECTS


# --- the schema itself ----------------------------------------------------


def test_every_checkpoint_table_exists(migrated: sa.Engine) -> None:
    with migrated.connect() as conn:
        present = set(sa.inspect(conn).get_table_names())
    assert set(migration.CHECKPOINT_TABLES) <= present
    assert "copilot_thread" in present


def test_the_checkpoint_migration_rows_are_seeded(migrated: sa.Engine) -> None:
    """`setup()`'s bookkeeping, pre-written — see the migration's header.

    Without these, a developer running `setup()` against a scratch copy of this
    schema would re-apply the vendor's `ALTER TABLE` steps against tables that
    already have them.
    """
    with migrated.connect() as conn:
        rows = sorted(conn.scalars(sa.text("SELECT v FROM checkpoint_migrations")))
    assert rows == list(range(migration.CHECKPOINT_MIGRATION_VERSIONS))


def test_checkpoint_writes_has_the_columns_the_alter_steps_add(migrated: sa.Engine) -> None:
    """The vendor's `ALTER TABLE` history, folded into the frozen `CREATE`.

    `task_path` is added by the last vendor step and `blob` is made nullable on
    `checkpoint_blobs` by an earlier one. A frozen migration creates the end
    state rather than replaying the history, so the effect is what is asserted.
    """
    with migrated.connect() as conn:
        writes = {c["name"]: c for c in sa.inspect(conn).get_columns("checkpoint_writes")}
        blobs = {c["name"]: c for c in sa.inspect(conn).get_columns("checkpoint_blobs")}
    assert "task_path" in writes
    assert writes["task_path"]["nullable"] is False
    assert blobs["blob"]["nullable"] is True


def test_copilot_thread_constraints(migrated: sa.Engine) -> None:
    """The unique key that arbitrates minting, and the thread id's own.

    `(claim_id, user_id, conversation_seq)` is what makes two racing "new
    conversation" clicks produce one thread rather than two at the same
    sequence — see `data/repositories/copilot.py` on why a constraint rather
    than a lock.
    """
    with migrated.connect() as conn:
        inspector = sa.inspect(conn)
        uniques = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("copilot_thread")
        }
        foreign = {
            tuple(key["constrained_columns"]): key["referred_table"]
            for key in inspector.get_foreign_keys("copilot_thread")
        }
    assert ("thread_id",) in uniques.values()
    assert ("claim_id", "user_id", "conversation_seq") in uniques.values()
    assert foreign[("claim_id",)] == "claim"
    assert foreign[("user_id",)] == "app_user"


def test_copilot_thread_has_no_version_column(migrated: sa.Engine) -> None:
    """AD-4's statement for this table, asserted rather than only argued.

    `CopilotThread`'s docstring says the absence is structural: no version means
    no `expectedVersion` means no edit affordance, and a story that added one
    would be proposing that a conversation's identity become editable. A missing
    column is indistinguishable from an oversight unless something says so.
    """
    with migrated.connect() as conn:
        columns = {c["name"] for c in sa.inspect(conn).get_columns("copilot_thread")}
    assert "version" not in columns
    assert columns == {"id", "thread_id", "claim_id", "user_id", "conversation_seq", "created_at"}


@pytest.mark.parametrize(
    "table",
    [
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
        "copilot_thread",
    ],
)
def test_the_app_role_has_full_crud(migrated: sa.Engine, table: str) -> None:
    """All four verbs, including DELETE — PHI-class under AD-11.

    0035, 0040 and 0042 each argue the DELETE: Story 8.1's purge cascade should
    not discover at runtime that it cannot touch a table. A checkpoint is a
    transcript quoting a claim's diagnosis, so it is in scope for that purge by
    definition.
    """
    with migrated.connect() as conn:
        granted = set(
            conn.scalars(
                sa.text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE grantee = 'lineworker_app' AND table_name = :table"
                ),
                {"table": table},
            )
        )
    assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= granted


# --- the drift check ------------------------------------------------------


def test_alembic_check_is_clean_at_head(seeded_db_url: str) -> None:
    """ "No new upgrade operations detected" — what the `env.py` exclusion buys."""
    run_alembic("check")


def test_alembic_check_would_fail_without_the_exclusion(migrated: sa.Engine) -> None:
    """The other half: the guard above is not passing vacuously.

    Autogenerate is run directly with `include_name`/`include_object` **omitted**,
    and the four checkpoint tables must appear as proposed drops. Without this
    test, deleting the exclusion from `data/env.py` would leave the suite green
    and the failure would surface as a `drop_table` in somebody's autogenerated
    migration months later.

    Run in-process rather than through the CLI, because the point is to
    configure the comparison *differently* from the way `env.py` does.
    """
    from alembic.autogenerate import produce_migrations
    from alembic.migration import MigrationContext

    from data.models import Base

    with migrated.connect() as conn:
        context = MigrationContext.configure(conn)
        script = produce_migrations(context, Base.metadata)

    dropped = {
        getattr(directive, "table_name", None)
        for directive in script.upgrade_ops.ops  # type: ignore[union-attr]
        if type(directive).__name__ == "DropTableOp"
    }
    assert set(migration.CHECKPOINT_TABLES) <= dropped, (
        "autogenerate did not propose dropping the checkpoint tables even without the "
        "exclusion — this guard can no longer tell whether data/env.py's exclusion works"
    )


# --- the two guards -------------------------------------------------------


def test_a_downgrade_refuses_to_destroy_a_conversation(migrated: sa.Engine) -> None:
    """The PHI guard, and the only irreversible loss any migration here can cause.

    `downgrade()` drops four tables full of transcripts about injured workers
    (AD-11). Every other migration in this tree drops tables it created moments
    earlier in the same deployment; this one can be asked to drop months of
    conversations, silently, from one `alembic downgrade`.

    So it counts first and refuses, naming the counts — and this test creates
    exactly one checkpoint row to prove the refusal is real rather than argued.
    The row is removed afterwards, because the round-trip test below has to be
    able to downgrade.
    """
    with migrated.connect() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO checkpoints "
                "(thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata) "
                "VALUES ('claim.WC-20017.u1.s1', '', 'c1', '{}'::jsonb, '{}'::jsonb)"
            )
        )
    try:
        with pytest.raises(subprocess.CalledProcessError) as refused:
            run_alembic("downgrade", "0042_ai_insight")
        stderr = (refused.value.stderr or b"").decode()
        assert "refusing to downgrade" in stderr, stderr
        assert "checkpoints: 1" in stderr, stderr

        # …and nothing was dropped on the way to refusing.
        with migrated.connect() as conn:
            present = set(sa.inspect(conn).get_table_names())
        assert set(migration.CHECKPOINT_TABLES) <= present
        assert "copilot_thread" in present
    finally:
        with migrated.connect() as conn:
            conn.execute(sa.text("DELETE FROM checkpoints WHERE checkpoint_id = 'c1'"))


def test_an_upgrade_refuses_a_database_that_already_has_the_savers_tables(
    seeded_db_url: str, migrated: sa.Engine
) -> None:
    """The other half of vendoring somebody else's schema.

    Every statement in `CHECKPOINT_DDL` says `IF NOT EXISTS` — the vendor's own
    text, kept verbatim — so an upgrade against a database where somebody once
    ran `saver.setup()` creates nothing and then seeds `checkpoint_migrations`
    with ten rows asserting that the vendor's ten steps have been applied. If
    those pre-existing tables are at an older vendor schema, all ten rows are a
    lie and the missing `ALTER TABLE`s are the ones a later `setup()` will skip.
    A deployment cannot see that; a failing migration is visible immediately.

    The pre-existing table is deliberately the *wrong shape*, because that is
    the case the guard exists for — a right-shaped one would be harmless and is
    not what makes this worth failing over.
    """
    run_alembic("downgrade", "0042_ai_insight")
    try:
        with migrated.connect() as conn:
            conn.execute(sa.text("CREATE TABLE checkpoints (thread_id text)"))
        with pytest.raises(subprocess.CalledProcessError) as refused:
            run_alembic("upgrade", "head")
        stderr = (refused.value.stderr or b"").decode()
        assert "already has LangGraph checkpoint tables" in stderr, stderr
        assert "checkpoints" in stderr, stderr
    finally:
        with migrated.connect() as conn:
            conn.execute(sa.text("DROP TABLE IF EXISTS checkpoints"))
        run_alembic("upgrade", "head")


def test_the_round_trip_is_an_exact_inverse(seeded_db_url: str) -> None:
    """Down to 0042 and back up, with `alembic check` clean at the end.

    An exact-inverse `downgrade()` is the house rule every migration keeps, and
    it is harder here than usual: the checkpoint tables are dropped by raw SQL
    rather than by `op.drop_table`, so a table left behind would be invisible to
    the revision bookkeeping and would make a re-`upgrade()` fail on
    `CREATE TABLE` — except that the vendor's statements all say
    `IF NOT EXISTS`, which would have made it fail *silently* instead.
    """
    run_alembic("downgrade", "0042_ai_insight")
    engine = sa.create_engine(
        seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with engine.connect() as conn:
            present = set(sa.inspect(conn).get_table_names())
        assert not set(migration.CHECKPOINT_TABLES) & present
        assert "copilot_thread" not in present
    finally:
        engine.dispose()
    run_alembic("upgrade", "head")
    run_alembic("check")
