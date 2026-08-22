"""Shared DB-backed test plumbing.

Same contract as Story 1.2's schema tests: DB-backed tests need
MIGRATION_TEST_DATABASE_URL pointing at a disposable Postgres, and skip
without it, so `uv run pytest` stays green on a laptop with no database.

**Since Story 8.2 the disposable Postgres must be the built pgaudit image,
started with the hardening flags.** The stock `pgvector/pgvector:pg18` no
longer works: migration `0050_pgaudit` runs `CREATE EXTENSION pgaudit`, which
raises unless the postmaster preloaded the library — so `alembic upgrade head`
*fails* against an unconfigured server rather than skipping, and every module
using `seeded_db_url` fails with it. That is the migration's design (see its
docstring) rather than a rough edge, and `tests/test_pgaudit_posture.py` asserts
the settings the flags below produce.

Run both commands from `lineworker/`:

    docker build -t lineworker/postgres:pg18-pgaudit deploy/postgres
    docker run -d --name lw-test-pg -e POSTGRES_USER=lineworker \
      -e POSTGRES_PASSWORD=test -e POSTGRES_DB=lineworker \
      -p 55432:5432 lineworker/postgres:pg18-pgaudit \
      postgres -c shared_preload_libraries=pgaudit -c pgaudit.log=ddl,role \
      -c pgaudit.log_catalog=off -c pgaudit.log_parameter=off \
      -c pgaudit.log_relation=off -c pgaudit.log_statement_once=on \
      -c log_statement=none -c log_duration=off \
      -c log_min_duration_statement=-1 -c log_min_error_statement=panic \
      -c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0 \
      -c logging_collector=on -c log_destination=stderr -c log_directory=log \
      -c log_filename=postgresql-%a.log -c log_rotation_age=1d \
      -c log_rotation_size=0 -c log_truncate_on_rotation=on

    cd server && \
    MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker \
      uv run pytest

The flag list is `deploy/compose.yaml`'s, and that file argues each entry.
"""

import os
import shutil
import subprocess

import pytest
import sqlalchemy as sa

MIGRATION_DB_URL = os.environ.get("MIGRATION_TEST_DATABASE_URL")
APP_PASSWORD = os.environ.get("APP_DB_PASSWORD", "lineworker_app_dev")

requires_db = pytest.mark.skipif(
    MIGRATION_DB_URL is None,
    reason="MIGRATION_TEST_DATABASE_URL not set (needs a disposable Postgres)",
)


def run_alembic(*args: str) -> None:
    alembic = shutil.which("alembic")
    assert alembic, "alembic not on PATH"
    env = dict(os.environ, ALEMBIC_DATABASE_URL=str(MIGRATION_DB_URL), APP_DB_PASSWORD=APP_PASSWORD)
    subprocess.run([alembic, *args], check=True, env=env, capture_output=True)


@pytest.fixture(scope="module")
def seeded_db_url() -> str:
    """Fresh schema -> `alembic upgrade head` (which includes the seed)."""
    assert MIGRATION_DB_URL is not None
    engine = sa.create_engine(
        MIGRATION_DB_URL.replace("postgresql://", "postgresql+psycopg://", 1),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("DROP SCHEMA public CASCADE"))
            conn.execute(sa.text("CREATE SCHEMA public"))
    finally:
        engine.dispose()
    run_alembic("upgrade", "head")
    return MIGRATION_DB_URL
