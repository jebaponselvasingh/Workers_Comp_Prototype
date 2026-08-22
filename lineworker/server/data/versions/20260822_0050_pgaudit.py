"""Install pgaudit — the half of AD-11's database auditing that is DDL.

AD-3 makes Alembic the only mechanism that changes a database, and
`CREATE EXTENSION` is a change to a database, so it is here rather than in an
init script or in somebody's shell history. It is deliberately the *smaller*
half: what pgaudit captures is not decided by this file at all.

## Why an upgrade against an unconfigured Postgres fails here, by design

pgaudit hooks the executor and the utility processor, which it can only do if
the postmaster loaded it at start — `shared_preload_libraries` must name it, and
`CREATE EXTENSION pgaudit` raises

    pgaudit must be loaded via "shared_preload_libraries"

when it does not. That is the correct behaviour and Story 8.2's Project
Structure Notes ask for it in as many words: a bare `alembic upgrade head`
against a Postgres nobody configured must fail loudly rather than skip
silently, because a database that reports a successful migration and captures
no DDL is a compliance claim with nothing behind it.

**The refusal is conditional, and the condition is worth stating precisely.**
It holds on a database that does not yet have the extension — which is every
fresh one, and therefore every case this migration is the first thing to meet.
It does *not* hold in the other direction: `IF NOT EXISTS` (see below) makes
this a no-op on a database where pgaudit was installed while the library was
preloaded and the server was later restarted without it. That upgrade succeeds
in silence, and the promise above would be a false one if it were left
unqualified.

Nothing is added here to close that, because the missing claim is not this
file's to make. Installing an extension and configuring a postmaster are two
different things, and the second is `tests/test_pgaudit_posture.py`, which
reads `shared_preload_libraries` and `pgaudit.log` off the *running server*
and fails on exactly the state described above. A migration that also tried to
verify the preload would be asserting a property of a process from inside a
transaction that will be replayed, from a file whose job is to describe what
the schema became.

The other half is the `command:` flag list on the postgres service in
`deploy/compose.yaml`, `deploy/compose.e2e.yaml` and `deploy/compose.prod.yaml`
— identical in all three, and `tests/test_deploy_tls_posture.py` fails when they
disagree. The image that has the extension to load is `deploy/postgres/
Dockerfile`. A local test database therefore needs both (see
`tests/conftest.py`), and `tests/test_pgaudit_posture.py` asserts the running
server's settings rather than this migration's success — installing an
extension and configuring it are two different claims.

## What it captures, stated here because this is where a reader will look

`pgaudit.log = 'ddl, role'` and nothing else. No `read`, no `write`, no
`function`, no `all`, and `pgaudit.log_parameter = off`. Value-level history is
AD-4's `audit_event`, whose diffs the Story 8.1 purge cascade can redact in
place; a line in the database's own log file cannot be redacted by anything
this system owns, so a pgaudit posture that captured DML would be a second,
permanent copy of exactly the data the cascade exists to remove.

`IF NOT EXISTS` for the e2e reset's reason — `alembic upgrade head` runs on
every spec file against a schema that was just dropped, and an extension is a
database-level object that survives `DROP SCHEMA public CASCADE` when it was
installed into another schema, so a bare `CREATE EXTENSION` is a coin flip
between "created" and "already exists" depending on where it landed.

Revision ID: 0050_pgaudit
Revises: 0049_purge_grants
Create Date: 2026-08-22

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0050_pgaudit"
down_revision: str | None = "0049_purge_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgaudit")


def downgrade() -> None:
    # Symmetric, and `IF EXISTS` for the same reason the upgrade is
    # `IF NOT EXISTS`: this migration must be re-runnable in both directions
    # against a database whose extensions outlive its schema.
    op.execute("DROP EXTENSION IF EXISTS pgaudit")
