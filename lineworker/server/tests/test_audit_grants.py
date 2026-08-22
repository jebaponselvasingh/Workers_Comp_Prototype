"""Task 4: the `audit_event` grant posture, read from the catalog (AD-4, AD-11).

**This story verifies; Story 8.1 granted.** Nothing here creates a role, widens
a privilege or moves a policy — migration 0003 wrote the grants and the five
row-level-security policies, migration 0049 added the role membership the
redaction path needs, and `tests/test_schema_seed.py` proves the *behaviour*
those grants produce (the app role's DELETE is refused, the redactor's UPDATE
is not, its DELETE stops at the retention floor). What was missing was the
description: an assertion about what the catalog says, run against the
fresh-migration database, so that a future migration which widens the posture
fails a test instead of passing review.

The two kinds of check are worth having separately. A behavioural test says
"this statement is refused today"; a catalog test says "this role holds exactly
these privileges" — and it is the second that catches a grant added for some
unrelated feature, because nobody would think to write a new refusal test for a
privilege they had just handed out.

## Every comparison is `==`, and that is the whole design

`tests/test_photo_migration.py` makes the same choice for `photo` and states
the reason: a `>=` or a subset check would let a future migration widen this
table's grants without anything noticing, which is precisely the failure mode
being guarded. Append-only audit is the invariant the entire AD-4 design rests
on — if the application role can rewrite an audit row, the row stops being
evidence — so the assertions here are equalities and the fix for a failure is
to remove the grant, not to update the expectation.

## The one exception, and why it is a role and not a bug

`audit_redactor` exists because AD-11 requires the *content* of an audit diff
to be destroyable while the row skeleton survives. It holds SELECT (which the
qualified UPDATE and DELETE cannot run without — Postgres demands SELECT on
every column named in a WHERE clause), column-scoped UPDATE on `before` and
`after`, and a DELETE the `audit_redactor_delete` policy bounds to rows past
the retention floor. Three grants, and the floor is baked into the policy by
migration rather than driven from a session setting, for the reason migration
0049's docstring spends four paragraphs on: a policy the guarded session can
set is not a safety net.
"""

from typing import Any

import pytest
import sqlalchemy as sa

from tests.conftest import requires_db

pytestmark = requires_db

TABLE = "audit_event"

#: The application role's entire entitlement on the audit log.
#:
#: INSERT because every AD-4 command writes one; SELECT because
#: `services/audit/purge.py` has to *find* a subject's rows before the redactor
#: rewrites them. No UPDATE, no DELETE, no TRUNCATE — append-only is enforced by
#: the database rather than by the discipline of whoever writes the next command.
APP_ROLE_GRANTS = {"SELECT", "INSERT"}

#: The redactor's table-level entitlement. Its UPDATE is column-scoped and so
#: appears in `role_column_grants` rather than here, which is itself worth
#: knowing: a table-level UPDATE showing up in this set would mean somebody had
#: replaced `GRANT UPDATE ("before","after")` with a bare `GRANT UPDATE`.
REDACTOR_GRANTS = {"SELECT", "DELETE"}

#: The only two columns the redactor may write, and the only two that ever hold
#: content. `at`, `actor_id`, `actor_role`, `action`, `entity` and `entity_id`
#: are the skeleton AD-11 requires to survive a purge, so a grant on any of them
#: would let the redaction path rewrite history rather than empty it.
REDACTOR_COLUMN_UPDATES = {("before", "UPDATE"), ("after", "UPDATE")}

#: The five policies migration 0003 creates, by name and command.
#:
#: Named rather than counted, because "there are five policies" would keep
#: passing after somebody replaced one with another. The `audit_app_insert`
#: policy has a `WITH CHECK` and no `USING`, which is why `qual` is NULL for it
#: and why the qualifier assertion below is scoped to the delete policy.
EXPECTED_POLICIES = {
    "audit_app_insert": "INSERT",
    "audit_app_select": "SELECT",
    "audit_redactor_select": "SELECT",
    "audit_redactor_update": "UPDATE",
    "audit_redactor_delete": "DELETE",
}


@pytest.fixture(scope="module")
def catalog(seeded_db_url: str) -> Any:
    """A connection to the fresh-migration database, for reading `information_schema`.

    `seeded_db_url` rather than a bare URL, deliberately: the posture this file
    describes is the one a *fresh* `alembic upgrade head` produces, which is the
    thing CI runs and the thing a new deployment gets. A test against whatever
    database happened to be lying around would be a test about history.
    """
    engine = sa.create_engine(
        seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1),
        isolation_level="AUTOCOMMIT",
    )
    try:
        yield engine
    finally:
        engine.dispose()


def table_grants(engine: sa.Engine, grantee: str) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                sa.text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = :t AND grantee = :g"
                ),
                {"t": TABLE, "g": grantee},
            ).scalars()
        )


def test_the_application_role_can_only_append_to_the_audit_log(catalog: sa.Engine) -> None:
    """AD-4's central invariant, as the catalog states it.

    An audit row is evidence that somebody did something. A role that can
    UPDATE one can make the evidence say something else, and a role that can
    DELETE one can make it say nothing at all — so the application role, which
    is what every request in this build runs as, holds neither. TRUNCATE is in
    the same sentence for the same reason and is the one people forget: it is
    not a DELETE and it empties the table just as thoroughly.
    """
    granted = table_grants(catalog, "lineworker_app")
    assert granted == APP_ROLE_GRANTS, (
        f"lineworker_app holds {sorted(granted)} on {TABLE}; AD-4 permits exactly "
        f"{sorted(APP_ROLE_GRANTS)}. Append-only audit is the invariant the whole "
        "design rests on — remove the grant rather than widening this expectation."
    )


def test_the_redactor_holds_three_grants_and_no_more(catalog: sa.Engine) -> None:
    """AD-4's registered exception, bounded to exactly what AD-11 needs.

    SELECT and DELETE at the table, UPDATE at two columns, and the DELETE is
    bounded again by RLS. Widening any of it is what the epic context calls out
    by name — "widening that role's grants breaks the invariant the whole audit
    design rests on" — and the column half is where a widening would be easiest
    to miss: a bare `GRANT UPDATE ON audit_event` reads almost identically to
    the scoped one in a diff and would let the redaction path rewrite the
    actor, the action or the timestamp.

    The column query is filtered to UPDATE because the table-level SELECT
    grant produces a `role_column_grants` row for every column — a real effect
    of a real grant, and one that would make an unfiltered equality assert the
    schema's column list instead of this role's write reach.
    """
    granted = table_grants(catalog, "audit_redactor")
    assert granted == REDACTOR_GRANTS, (
        f"audit_redactor holds {sorted(granted)} at table level on {TABLE}; "
        f"AD-4 gives it exactly {sorted(REDACTOR_GRANTS)} there, with UPDATE scoped "
        "to two columns."
    )

    with catalog.connect() as conn:
        columns = set(
            conn.execute(
                sa.text(
                    "SELECT column_name, privilege_type "
                    "FROM information_schema.role_column_grants "
                    "WHERE table_name = :t AND grantee = 'audit_redactor' "
                    "AND privilege_type = 'UPDATE'"
                ),
                {"t": TABLE},
            ).all()
        )
    assert columns == REDACTOR_COLUMN_UPDATES, (
        f"audit_redactor may UPDATE {sorted(columns)}; AD-11 permits only the two diff "
        "columns. The row skeleton — actor, role, action, entity, entity_id, at — must "
        "survive a purge, which is the difference between redaction and rewriting."
    )


def test_no_role_other_than_the_two_and_the_owner_touches_the_audit_log(
    catalog: sa.Engine,
) -> None:
    """The half the two tests above cannot see: a *third* role holding writes.

    Each of them is an equality about one grantee, and both would pass happily
    against a schema that had granted UPDATE on `audit_event` to some reporting
    role added by a later story. So the grantee set itself is bounded.

    The owner is in the permitted set and cannot sensibly be excluded: it is
    whoever ran the migration — `lineworker` in dev and in the test container,
    `lineworker_e2e_owner` under the e2e profile, whatever a production operator
    chose — and PostgreSQL gives a table's owner every privilege on it
    implicitly. Read from `pg_tables` rather than assumed, for migration 0049's
    reason: the owner's name is a deployment fact this suite must not hardcode.
    """
    with catalog.connect() as conn:
        owner = conn.execute(
            sa.text("SELECT tableowner FROM pg_tables WHERE tablename = :t"), {"t": TABLE}
        ).scalar_one()
        grantees = set(
            conn.execute(
                sa.text(
                    "SELECT DISTINCT grantee FROM information_schema.role_table_grants "
                    "WHERE table_name = :t"
                ),
                {"t": TABLE},
            ).scalars()
        )
    permitted = {"lineworker_app", "audit_redactor", str(owner)}
    assert grantees <= permitted, (
        f"{sorted(grantees - permitted)} hold privileges on {TABLE}. Only the "
        "application role, the audit_redactor exception and the table's owner may."
    )
    # …and the two that must be there are there, or the subset above is
    # satisfied by a table nobody can reach at all.
    assert {"lineworker_app", "audit_redactor"} <= grantees


def test_the_five_row_level_security_policies_are_intact(catalog: sa.Engine) -> None:
    """RLS is what makes the redactor's DELETE safe, so its absence is silent.

    Without `audit_redactor_delete` the grant is unbounded and the retention job
    could take the whole table; without `audit_redactor_select` a *targeted*
    redaction raises nothing at all and simply matches zero rows (migration
    0003's comment records that one). Neither failure announces itself, which is
    why the policies are asserted by name and command rather than by count.

    The delete policy's qualifier is checked for an `interval` floor rather than
    for the exact seven years. The number is `AUDIT_RETENTION_YEARS` at
    migration time by design, so pinning the literal here would make this test
    fail for a deployment that legitimately configured a different floor and
    migrated to match — while a policy that had *lost* its interval, which is
    the actual hazard, fails either way.
    """
    with catalog.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT policyname, cmd, qual FROM pg_policies WHERE tablename = :t"),
            {"t": TABLE},
        ).all()
        rls_enabled = conn.execute(
            sa.text("SELECT relrowsecurity FROM pg_class WHERE relname = :t"), {"t": TABLE}
        ).scalar_one()

    assert rls_enabled is True, f"row-level security is disabled on {TABLE}; every policy is inert"
    assert {row.policyname: row.cmd for row in rows} == EXPECTED_POLICIES

    delete_policy = next(row for row in rows if row.policyname == "audit_redactor_delete")
    assert "interval" in str(delete_policy.qual), (
        f"audit_redactor_delete's qualifier is {delete_policy.qual!r} and no longer "
        "carries a retention floor — the role's DELETE is unbounded."
    )
    assert "now()" in str(delete_policy.qual), delete_policy.qual


def test_pgaudit_is_installed_by_the_migration_that_says_it_is(catalog: sa.Engine) -> None:
    """Migration 0050's own claim, asserted against a fresh upgrade.

    It belongs in this file rather than in `test_pgaudit_posture.py` because it
    is a statement about what `alembic upgrade head` produced, not about how the
    server was started — the same kind of claim as every grant above, and it
    would otherwise be proved only by the fact that the migration did not raise.
    """
    with catalog.connect() as conn:
        installed = set(conn.execute(sa.text("SELECT extname FROM pg_extension")).scalars())
    assert "pgaudit" in installed, (
        "pgaudit is not installed after `alembic upgrade head`. Migration 0050 is "
        "supposed to fail loudly against a server that has not preloaded it, so a "
        "clean upgrade and a missing extension should not be able to coexist."
    )
