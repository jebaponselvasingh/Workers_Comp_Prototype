"""The system actor the payment batch audits under (Story 3.4).

AD-4 fixes the audit schema, and `audit_event.actor_id` is a non-nullable
foreign key to `app_user`. Every write in this build so far has had a request
behind it, so the actor was whoever held the cookie. Story 3.4 adds the first
command with no request at all — the payment batch runs on a schedule inside
the api process — and it still has to answer "who did this".

So the batch gets an identity: one `app_user` row with the new `system` role.
`data/models/enums.py::UserRole` argues why that is better than the three
alternatives (a handler's name on thousands of disbursements, a nullable
actor, or an unaudited batch); this migration is the storage half.

**`scope_all = true`, and it is the honest value.** The batch pays across the
whole portfolio, so its AD-7 predicate compiles to `TRUE` because its scope
really is everything — the tautology `employer_scope` exists for, not a
repository skipping a filter for a privileged caller. It gets no
`user_employer_assignment` rows for the same reason `David Bline` gets none.

**This row can never log in**, and that is enforced in code rather than here:
`list_personas` omits `system` rows from the picker and `get_persona` refuses
them, so the id being guessable buys nothing. `POST /auth/login` is a public
path, which is why the refusal is the one in `get_persona` and not the one in
the picker.

## Why `autocommit_block`, and what it costs

`user_role` is a native PostgreSQL enum, and `ALTER TYPE … ADD VALUE` inside a
transaction leaves the new value unusable until that transaction commits —
which would make the INSERT below fail with "unsafe use of new value of enum
type". `data/env.py` wraps the *whole* upgrade run in one transaction, so the
ADD VALUE has to escape it. `op.get_context().autocommit_block()` is Alembic's
own mechanism for exactly this case.

The cost is worth stating: entering the block commits everything the run has
done so far, so a failure *after* this point no longer rolls the earlier
migrations back. On a fresh database that is a chain that was going to be
re-run from a dropped schema anyway (the e2e reset, and `conftest.py`'s
`seeded_db_url`), and on a live one the preceding migrations are the ones that
have already succeeded. It is the standard price of a PostgreSQL enum, and the
alternative — splitting this into two revisions — pays the same price with the
boundary hidden in the version graph instead of written down here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_system_actor"
down_revision: str | None = "0028_materialize_schedules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUM_NAME = "user_role"
SYSTEM_ROLE = "system"

#: Kept as a literal rather than imported from `data/models/enums.py`, the
#: rule migrations 0013 and 0014 set: a migration is a frozen record of what
#: the schema became, and reading a live constant would let a later rename
#: silently change what this revision did.
SYSTEM_ACTOR_NAME = "LINEWORKER Payment Batch"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # IF NOT EXISTS so a partially-applied chain (the autocommit block
        # above is precisely where a run can be interrupted between the type
        # change and the insert) can be re-run without a manual repair.
        op.execute(f"ALTER TYPE {ENUM_NAME} ADD VALUE IF NOT EXISTS '{SYSTEM_ROLE}'")

    bind = op.get_bind()
    meta = sa.MetaData()
    app_user = sa.Table("app_user", meta, autoload_with=bind)

    existing = bind.execute(
        sa.select(app_user.c.id).where(app_user.c.name == SYSTEM_ACTOR_NAME)
    ).first()
    if existing is None:
        bind.execute(
            app_user.insert().values(
                name=SYSTEM_ACTOR_NAME,
                role=SYSTEM_ROLE,
                scope_all=True,
            )
        )


def downgrade() -> None:
    """Deliberately a no-op, and the reason is the audit log.

    Neither half of this revision can be honestly undone.

    **The enum value cannot be dropped at all.** PostgreSQL has no `ALTER TYPE
    … DROP VALUE`; removing it means rebuilding `user_role` and rewriting every
    column typed by it — `app_user.role` and `audit_event.actor_role`.

    **The row must not be deleted**, which is the interesting half. Every
    payment the batch has disbursed is an `audit_event` naming this actor, and
    `actor_id` is a non-nullable foreign key. So the delete either fails on the
    constraint (a downgrade that works only on a database where the batch never
    ran) or is made to succeed by deleting audit rows — destroying the record
    of real disbursements to reverse a schema change. AD-4 makes that log
    append-only for exactly this reason.

    What is left is idempotent instead: `upgrade()` guards both statements with
    existence checks, so a `downgrade 0028` → `upgrade head` round trip
    completes cleanly and lands on the same state. The revision is a one-way
    door, and saying so is better than a `delete()` that passes in CI because
    CI never runs the batch.
    """
