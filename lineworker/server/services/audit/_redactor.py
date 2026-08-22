"""The one door to `audit_redactor` (AD-4's registered exception, Story 8.1).

`audit_event` is append-only to the application: migration 0003 grants
`lineworker_app` `SELECT, INSERT` and nothing else, enables row-level security,
and gives a second role — `audit_redactor`, `NOLOGIN` — exactly three grants:
`SELECT`, `UPDATE ("before","after")`, and `DELETE` bounded by an RLS policy to
rows past the retention floor. That role is the *whole* of the exception to
append-only audit, and Epic 8's context says in as many words that widening it
"breaks the invariant the whole audit design rests on".

An exception with one door is an exception somebody can reason about. This
module is that door: `purge.py` redacts through it and `retention.py` sweeps
through it, and nothing else in `server/` imports it (the leading underscore is
the convention; `tests/test_purge_ownership.py` is the enforcement).

## Why `SET ROLE` on the owner connection, and not a fourth login role

The obvious alternative — give `audit_redactor` a password and a `LOGIN` bit and
put its DSN in the settings module — is refused by the story's own Block If, and
the reason is operational rather than aesthetic. A login role is a credential:
one more secret to mint, store outside version control, rotate, and reconcile on
every boot the way `data/roles.py::sync_app_role` reconciles the app role's. It
would also be a credential whose entire purpose is to be able to overwrite the
audit log, sitting in the same env file as everything else.

`SET ROLE` needs no secret at all. The role is created `NOLOGIN`, migration 0049
grants membership in it to whoever ran the migration, and the owner connection
this project already has (`ALEMBIC_DATABASE_URL`) assumes it for the length of
one purge.  `tests/test_schema_seed.py::test_audit_redactor_can_redact_specific_
rows` has proved the mechanism works since Story 1.2; this module is that test's
arrangement, in production code.

## The engine is per call, and disposed in a `finally`

`data/roles.py::sync_app_role` is the template and its shape is the argument: a
process-lifetime engine on owner credentials would be a pool of connections
entitled to rewrite the audit log, kept open for the lifetime of an api process
in which almost nothing ever redacts anything. A purge is a rare, deliberate
act — a management command, or a housekeeping job that fires once a day — so the
connection it needs is one it opens, uses and closes. The cost is one connection
setup per purge; the benefit is that for every other second of the process's
life there is no open handle holding this privilege.

## `SET ROLE` is a *session* setting, and that has two consequences worth stating

It is issued once and then committed, so it survives the caller's own commits:
a `SET` (as opposed to `SET LOCAL`) made inside a transaction sticks when that
transaction commits. That is what lets `retention.py` count and delete in one
transaction and commit it without silently reverting to the owner role half way
through.

The other half is the hazard, and it is worth stating precisely rather than as
the folk version. A rollback does **not** revert this `SET ROLE`: `_assume_role`
issues it and commits it immediately, and a committed `SET` is a session
setting, so it survives every later `rollback()` on the connection.
`retention.py::purge_expired_audit_events` depends on exactly that — it rolls
back on a floor mismatch and the connection is still the redactor afterwards.

What a rollback *would* revert is a `SET ROLE` that had never been committed,
which is the shape this module deliberately does not have. The residual hazard
is therefore the other direction: because the switch is sticky, a connection
handed on or held open is a handle entitled to rewrite the audit log for as long
as it lives. So the contract is that this connection is single-use and
single-transaction: use it, commit it, and leave — the `RESET ROLE` in the
`finally` and the engine's disposal two lines later are what make "and leave"
enforced rather than asked for. `_assume_role` verifies `current_user` after the
switch rather than trusting that the statement did what it said.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

import sqlalchemy as sa
import structlog
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from config import Settings

log = structlog.get_logger()

#: The role migration 0003 grants and `data/roles.py` creates. One spelling.
REDACTOR_ROLE: Final[str] = "audit_redactor"


class RedactorUnavailable(RuntimeError):
    """The redactor role could not be assumed, so no purge may start.

    Raised *before* anything is deleted rather than when the redaction is
    reached, which is the whole reason `purge_claim` opens this connection up
    front. A cascade that deleted a claim's fourteen child tables and then
    discovered it could not redact the diffs describing them would have produced
    the one outcome AD-11 forbids in both directions at once: the rows are gone
    and their contents survive in the audit log, with no way to re-run the part
    that failed because the part that succeeded is not repeatable.

    **The message names the role and never the DSN.** An owner connection string
    carries the owner's password, and an exception is a string that reaches a
    log line, a CLI's stderr and a bug report. `RuntimeError` rather than a
    `LookupError` because this is a deployment fault with an operator's fix
    (grant the membership, set `ALEMBIC_DATABASE_URL`), not an absence.
    """


@asynccontextmanager
async def redactor_connection(settings: Settings) -> AsyncIterator[AsyncConnection]:
    """Yield a connection running as `audit_redactor`. Single-use, single-transaction.

    Raises `RedactorUnavailable` — before yielding anything — when the owner URL
    is not configured or when the switch is refused. Both failures are the same
    thing to a caller (there is no redaction path available), and telling them
    apart in the *message* rather than in the type is deliberate: a caller's
    behaviour is identical and only an operator needs the distinction.

    **`alembic_database_url` is required rather than defaulted.**
    `Settings.async_alembic_database_url` falls back to `database_url` for
    consistency with its three siblings, and that fallback is exactly wrong
    here: it would hand this function the *app* role's credentials, whose
    `SET ROLE` is refused because the app role is not a member of the redactor.
    The refusal would be correct and would arrive one statement later, which is
    a worse failure than this one only because it is harder to read. So the
    absence is checked here, where the sentence can name the variable.

    `RESET ROLE` on the way out, in a `finally`, so a connection that somehow
    outlived its engine could not carry the privilege anywhere. It is belt and
    braces — the engine is disposed two lines later — and it costs one
    round trip on a path that runs once per purge.
    """
    # Falsy rather than `is None`, because an **empty string** is the same
    # deployment fault wearing a different type: `ALEMBIC_DATABASE_URL=` in a
    # compose file or an `.env` sets the field to `""`, which is not `None`, is
    # not a DSN, and would fall through to the app-role fallback below and fail
    # one statement later with a message about `SET ROLE` instead of a message
    # about the variable the operator actually has to fix.
    if not settings.alembic_database_url:
        raise RedactorUnavailable(
            f"ALEMBIC_DATABASE_URL is not set, so there is no owner connection to "
            f"assume the {REDACTOR_ROLE} role on. The application role may not "
            "redact: its audit_event grants are SELECT and INSERT by design "
            "(AD-4), and falling back to them would refuse one statement later."
        )

    engine = create_async_engine(settings.async_alembic_database_url)
    try:
        async with engine.connect() as conn:
            await _assume_role(conn)
            try:
                yield conn
            finally:
                # Best effort: if the caller left the connection in a failed
                # transaction this cannot run, and it does not need to — the
                # dispose below ends the session outright.
                try:
                    await conn.exec_driver_sql("RESET ROLE")
                except SQLAlchemyError as exc:  # pragma: no cover - defensive
                    log.warning("audit.redactor_reset_failed", error=type(exc).__name__)
    finally:
        await engine.dispose()


async def _assume_role(conn: AsyncConnection) -> None:
    """`SET ROLE audit_redactor`, committed, and then verified.

    Three statements where one would appear to do, and each of the other two is
    load-bearing.

    The **commit** is what makes the switch outlive the caller's own transaction
    boundaries. `SET` inside a transaction persists when that transaction
    commits, so issuing it and committing immediately leaves the caller with a
    clean transaction on a session that is already the redactor — rather than
    with a role that would quietly revert the first time it committed anything.

    The **verification** is what stops a silent no-op. `SET ROLE` to a role one
    is not a member of raises, which is the case this catches; but the reason to
    read `current_user` back is the case that does not raise — a future
    connection pool, proxy or `search_path` arrangement that swallowed the
    statement would leave every subsequent `UPDATE` running as the *owner*, who
    bypasses row-level security. That is a failure with no symptom at all: the
    redaction would succeed, the retention floor would not apply, and nothing
    anywhere would say so.

    The role name is interpolated because `SET ROLE` takes an identifier and
    identifiers cannot be bind parameters. It is a module constant, never a
    caller's string, which is what makes that safe.
    """
    try:
        await conn.exec_driver_sql(f"SET ROLE {REDACTOR_ROLE}")
        await conn.commit()
        current = await conn.scalar(sa.text("SELECT current_user"))
    except SQLAlchemyError as exc:
        raise RedactorUnavailable(
            f"could not assume the {REDACTOR_ROLE} role on the owner connection "
            f"({type(exc).__name__}). The role is created NOLOGIN by data/roles.py "
            "and migration 0049 grants membership in it to whoever ran the "
            "migration; an owner that ran migrations elsewhere needs "
            f"GRANT {REDACTOR_ROLE} TO <owner>."
        ) from exc
    if current != REDACTOR_ROLE:
        raise RedactorUnavailable(
            f"SET ROLE reported success but current_user is {current!r} rather "
            f"than {REDACTOR_ROLE!r}; refusing to redact as a role that may "
            "bypass row-level security"
        )


__all__ = ["REDACTOR_ROLE", "RedactorUnavailable", "redactor_connection"]
