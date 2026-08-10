#!/bin/sh
# Migrate, then serve: a clean checkout reaches healthy with one command (AC 1).
set -e

uv run --no-dev alembic upgrade head
# Reconcile lineworker_app with APP_DB_PASSWORD on every boot: the migration
# that creates the role runs once, so rotation must not depend on it.
uv run --no-dev python -m data.roles
exec uv run --no-dev uvicorn --factory api.app:create_app --host 0.0.0.0 --port 8000
