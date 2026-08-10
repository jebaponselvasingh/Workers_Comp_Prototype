"""Cluster-level role management (AD-3, AD-4).

Roles are cluster objects, not schema objects — they survive the e2e schema
reset and belong to no migration. Migration 0002 creates them, but a
versioned migration runs exactly once: ``alembic upgrade head`` is a no-op
against an already-migrated database, so a rotated ``APP_DB_PASSWORD`` would
never reach ``lineworker_app`` and the api could never authenticate again
(healthz 503 forever, web blocked on ``service_healthy``). The entrypoint
therefore runs this module on every boot, as the owner role; it is
idempotent, and the ALTER is unconditional so rotation actually sticks.
"""

import sqlalchemy as sa
import structlog

from config import Settings, get_settings
from logging_config import configure_logging

log = structlog.get_logger()


def _literal(value: str) -> str:
    """Single-quoted SQL string literal — DDL cannot take bind parameters."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def ensure_roles_sql() -> str:
    """Create both roles if absent. Passwordless by design — set_app_password_sql
    owns the credential so it is written in exactly one place, every run."""
    return """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'lineworker_app') THEN
                CREATE ROLE lineworker_app LOGIN;
            END IF;
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'audit_redactor') THEN
                CREATE ROLE audit_redactor NOLOGIN;
            END IF;
        END
        $$;
        """


def set_app_password_sql(app_password: str) -> str:
    """Unconditional: the one statement that makes a rotated password stick."""
    return f"ALTER ROLE lineworker_app WITH LOGIN PASSWORD {_literal(app_password)}"


def sync_app_role(settings: Settings | None = None) -> None:
    """Reconcile the runtime role with configuration. Runs as the owner role."""
    settings = settings or get_settings()
    engine = sa.create_engine(settings.sync_alembic_database_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(sa.text(ensure_roles_sql()))
            conn.execute(sa.text(set_app_password_sql(settings.app_db_password)))
    finally:
        engine.dispose()
    log.info("roles.synced", role="lineworker_app")


if __name__ == "__main__":
    configure_logging(get_settings().log_level)
    sync_app_role()
