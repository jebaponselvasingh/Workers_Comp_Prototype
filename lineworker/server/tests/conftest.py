"""Shared DB-backed test plumbing.

Same contract as Story 1.2's schema tests: DB-backed tests need
MIGRATION_TEST_DATABASE_URL pointing at a disposable Postgres, and skip
without it, so `uv run pytest` stays green on a laptop with no database.

    docker run -d --name lw-test-pg -e POSTGRES_USER=lineworker \
      -e POSTGRES_PASSWORD=test -e POSTGRES_DB=lineworker \
      -p 55432:5432 pgvector/pgvector:pg18
    MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker \
      uv run pytest
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
