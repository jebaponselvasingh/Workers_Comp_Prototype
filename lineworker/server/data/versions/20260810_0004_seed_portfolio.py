"""Seed the claim portfolio from the prototype dataset (Story 1.2, AC 2).

Loads data/seed/seed_data.json (extracted from the prototype's ALL_CLAIMS by
data/seed/extract_prototype_seed.py — the sanctioned initial loader, AD-12):
10 employers, 100 employees, 10 app_user rows (9 personas; David Bline is
both supervisor and analyst), HANDLER_MAP-derived scope assignments, and
100 claims. Money is already integer cents; enums already snake_case.

Revision ID: 0004_seed_portfolio
Revises: 0003_core_schema
Create Date: 2026-08-10

"""

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0004_seed_portfolio"
down_revision: str | None = "0003_core_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_PATH = Path(__file__).parent.parent / "seed" / "seed_data.json"


def upgrade() -> None:
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    bind = op.get_bind()
    meta = sa.MetaData()
    employer = sa.Table("employer", meta, autoload_with=bind)
    employee = sa.Table("employee", meta, autoload_with=bind)
    app_user = sa.Table("app_user", meta, autoload_with=bind)
    assignment = sa.Table("user_employer_assignment", meta, autoload_with=bind)
    claim = sa.Table("claim", meta, autoload_with=bind)

    employer_ids: dict[str, int] = {}
    for row in seed["employers"]:
        result = bind.execute(employer.insert().values(**row).returning(employer.c.id))
        employer_ids[row["name"]] = result.scalar_one()

    employee_ids: dict[str, int] = {}
    for row in seed["employees"]:
        result = bind.execute(employee.insert().values(**row).returning(employee.c.id))
        employee_ids[row["employee_id"]] = result.scalar_one()

    # One row per name|role login option; scope rows only for non-scope_all
    # personas (full portfolio comes only from the flag — AD-7).
    user_ids: dict[tuple[str, str], int] = {}
    for persona in seed["app_users"]:
        result = bind.execute(
            app_user.insert()
            .values(name=persona["name"], role=persona["role"], scope_all=persona["scope_all"])
            .returning(app_user.c.id)
        )
        user_id = result.scalar_one()
        user_ids[(persona["name"], persona["role"])] = user_id
        for employer_name in persona["employers"]:
            bind.execute(
                assignment.insert().values(user_id=user_id, employer_id=employer_ids[employer_name])
            )

    for row in seed["claims"]:
        values = dict(row)
        values["employer_id"] = employer_ids[values.pop("employer")]
        values["employee_id"] = employee_ids[values.pop("employee_id")]
        values["handler_id"] = user_ids[(values.pop("handler"), "handler")]
        bind.execute(claim.insert().values(**values))


def downgrade() -> None:
    # audit_event first: its actor_id FK references seeded app_user rows
    # (owner-level schema teardown — the append-only ban binds the app role).
    for table in (
        "audit_event",
        "claim",
        "user_employer_assignment",
        "app_user",
        "employee",
        "employer",
    ):
        op.execute(f"DELETE FROM {table}")
