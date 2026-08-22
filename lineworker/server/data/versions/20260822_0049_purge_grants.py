"""The two grants Story 8.1's purge cascade needs and cannot give itself.

No tables, no columns, no policies — two `GRANT` statements, and both are here
because the cascade discovered at write time that the schema said no.

**1. `GRANT DELETE ON photo TO lineworker_app`.** Migration 0020 granted the app
role `SELECT` and nothing else, and argued the point at length: nothing in Epics
1–8 uploads, annotates or removes a photograph, so the seed migration is the
table's only writer and a wider grant would have been "a capability nobody asked
for behind an endpoint that cannot use it". That argument was right about
*writes* and it stops one verb short. AD-11 makes an incident photograph
claim-derived PHI, and a purge cascade that can delete a claim's documents, bills
and diary notes but not its photographs is a cascade that leaves PHI behind — or,
worse, one that fails half-way through with `permission denied` on a table whose
parent row it has already queued for deletion. `services/audit/purge.py` is the
only caller, `tests/test_purge_ownership.py` is what keeps that true, and
`tests/test_photo_migration.py` asserts the grant is exactly `SELECT, DELETE` so
that INSERT and UPDATE stay refused by the database rather than by convention.

**2. `GRANT audit_redactor TO CURRENT_USER`.** The redaction path reaches its
role by `SET ROLE audit_redactor` on the owner connection — `data/roles.py`
creates the role `NOLOGIN` precisely so there is no second password to rotate,
and `tests/test_schema_seed.py::test_audit_redactor_can_redact_specific_rows`
has done it that way since Story 1.2. It has worked so far only because every
owner this project has run under is a superuser (`POSTGRES_USER` in dev and in
the test container, `lineworker_e2e_owner` in the e2e profile), and a superuser
may `SET ROLE` to anything. An ordinary owner may not: PostgreSQL requires
*membership*. So a deployment that follows AD-4 properly and gives the migration
role no superuser bit would install this schema and then find that the one
mechanism the whole redaction design rests on raises "permission denied to set
role" — at purge time, which is the worst possible moment to discover it.

`CURRENT_USER` rather than a named role, because the owner's name is a
deployment fact this migration must not assume: it is `lineworker` in dev,
`lineworker_e2e_owner` under the e2e profile, and whatever a production operator
chose. Whoever runs the migration is by definition the role that will later run
the purge script's owner connection, so granting membership to the runner is the
statement that is true in every environment. Re-granting an existing membership
is a NOTICE rather than an error, which is what keeps the e2e reset's repeated
`alembic upgrade head` idempotent.

## Why this migration does **not** turn the retention floor into a GUC

Story 8.1's task list suggests driving `audit_redactor_delete`'s `USING` clause
from `current_setting()` so that `AUDIT_RETENTION_YEARS` reaches the policy. That
is refused here, and the refusal is the point of the policy.

A GUC is set *by the session the policy is guarding*. The purge and retention
code would set it, which means the safety net would be woven by the thing it is
supposed to catch: a bug that set the interval to zero — or an operator who set
`AUDIT_RETENTION_YEARS=0` — would delete the entire audit log, and the RLS policy
would agree, because it was told to. Story 8.1's Task 5 asks for the opposite
property in as many words: "the RLS policy is the safety net — a bug cannot
delete younger rows".

Migration 0003 bakes the configured floor into the policy at migration time
(`get_settings().audit_retention_years`), so the floor is changeable only by a
migration — which is a reviewed diff with a deployment behind it, and the correct
authority for a number that decides how long a compliance record survives.

The residual is real and is closed elsewhere rather than by weakening this:
configuration can say five years while the policy still says seven, in which case
the housekeeping job would silently delete nothing and look like it had run.
`services/audit/retention.py::purge_expired_audit_events` counts the rows past
its own cutoff before deleting and raises `RetentionFloorMismatch` when the
delete comes up short, so a divergence is a loud failure naming both numbers
instead of a quiet under-delete.

Revision ID: 0049_purge_grants
Revises: 0048_export_limits
Create Date: 2026-08-22

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0049_purge_grants"
down_revision: str | None = "0048_export_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The NOLOGIN role `data/roles.py` creates and migration 0003 grants on
#: `audit_event`. Named here so this migration and its `downgrade()` spell it
#: once; `services/audit/_redactor.py` carries its own copy for the same reason
#: the two ends of every other name in this tree do — a migration is a frozen
#: historical record and must not import live code.
REDACTOR_ROLE = "audit_redactor"


def upgrade() -> None:
    # Grants only. Both statements are idempotent in the sense that matters for
    # the e2e reset: `GRANT` over an existing privilege is a no-op, and a
    # duplicate role membership is a NOTICE.
    op.execute("GRANT DELETE ON photo TO lineworker_app")
    op.execute(f"GRANT {REDACTOR_ROLE} TO CURRENT_USER")


def downgrade() -> None:
    # Symmetric, and in the reverse order, though neither depends on the other.
    # Revoking the membership does not drop the role: roles are cluster objects
    # that survive the e2e schema reset and belong to no migration
    # (`data/roles.py`), so dropping one here would take the audit_event
    # policies' grantee with it and leave migration 0003 un-downgradable.
    op.execute(f"REVOKE {REDACTOR_ROLE} FROM CURRENT_USER")
    op.execute("REVOKE DELETE ON photo FROM lineworker_app")
