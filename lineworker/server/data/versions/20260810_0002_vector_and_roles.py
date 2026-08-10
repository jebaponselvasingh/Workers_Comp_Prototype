"""pgvector extension + the two-role model (AD-3, AD-4).

Creates (idempotently — roles are cluster-level and survive e2e schema
resets):
- EXTENSION vector (pgvector ≥ 0.8.2, bundled by the 1.1 image)
- lineworker_app  — runtime app role the api connects as; no DDL rights.
  Its password is (re)set here and, because a migration runs only once, on
  every api boot by data/roles.py — see that module for why.
- audit_redactor  — AD-4 registered exception; NOLOGIN, assumable only by
  services/audit's purge/retention jobs (Epic 8). Grants arrive with the
  audit_event table in the next revision.

Revision ID: 0002_vector_and_roles
Revises: 0001_baseline
Create Date: 2026-08-10

"""

from collections.abc import Sequence

from alembic import op

from config import get_settings
from data.roles import ensure_roles_sql, set_app_password_sql

revision: str = "0002_vector_and_roles"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Shared with data/roles.py, which the entrypoint re-runs on every boot:
    # this migration fires once, so it cannot be the only place the password
    # is written or rotating APP_DB_PASSWORD would lock the api out for good.
    op.execute(ensure_roles_sql())
    op.execute(set_app_password_sql(get_settings().app_db_password))


def downgrade() -> None:
    # Surviving privileges (e.g. USAGE on schema public) block DROP ROLE;
    # DROP OWNED revokes them first (the roles own no objects).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'audit_redactor') THEN
                DROP OWNED BY audit_redactor;
            END IF;
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'lineworker_app') THEN
                DROP OWNED BY lineworker_app;
            END IF;
        END
        $$;
        """
    )
    op.execute("DROP ROLE IF EXISTS audit_redactor")
    op.execute("DROP ROLE IF EXISTS lineworker_app")
    op.execute("DROP EXTENSION IF EXISTS vector")
