"""Migrations 0014/0015 — the tables, the grants, and what landed in them.

`test_injury_validation.py` asserts 0014's `BODY_KEYS` literal against
`BodyRegion` without a database. This file is the other half: that the schema
the migration builds is the one the models describe, that the app role holds
the rights AD-4 gives it, and that the 500 treatment-plan rows and 400
prognosis values are the seed file's — compared against an independently
written reading of it, so the migration and the test cannot both be wrong in
the same way.
"""

from typing import Any

import pytest
import sqlalchemy as sa

from data.models.enums import BodyRegion
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db

PROGNOSIS_COLUMNS = (
    "prognosis_mmi",
    "prognosis_rtw",
    "prognosis_impairment",
    "prognosis_litigation",
)


@pytest.fixture
def engine(seeded_db_url: str) -> Any:
    created = sa.create_engine(seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        yield created
    finally:
        created.dispose()


def test_the_native_enum_holds_exactly_the_eleven_regions(engine: Any) -> None:
    """A twelfth label in the database would be a region no code can render."""
    with engine.connect() as conn:
        labels = conn.execute(
            sa.text(
                "SELECT e.enumlabel FROM pg_enum e "
                "JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = 'body_region' ORDER BY e.enumsortorder"
            )
        ).scalars()
        assert list(labels) == [member.value for member in BodyRegion]


def test_the_severity_check_constraint_refuses_a_score_outside_the_domain(
    engine: Any,
) -> None:
    """The database's half of a bound the command also enforces.

    Not redundancy: the command answers 422 with a sentence a handler can act
    on, and the constraint is what makes "no such row exists" true of the
    *table* rather than of the code paths anyone remembered to route through.
    """
    with engine.connect() as conn:
        claim_pk = conn.execute(sa.text("SELECT id FROM claim ORDER BY id LIMIT 1")).scalar_one()
        for score in (-1, 101):
            with pytest.raises(sa.exc.IntegrityError):
                conn.execute(
                    sa.text(
                        "INSERT INTO additional_injury "
                        "(claim_id, body_key, body_part, injury_type, severity_score) "
                        "VALUES (:claim, 'torso', 'Torso', 'Contusion', :score)"
                    ).bindparams(claim=claim_pk, score=score)
                )
            conn.rollback()


def test_two_rows_cannot_claim_the_same_treatment_step(engine: Any) -> None:
    """The card numbers its rows from `step_no`, so a duplicate renders an
    ambiguous plan. A re-seed that duplicated a claim's steps fails here."""
    with engine.connect() as conn:
        claim_pk = conn.execute(sa.text("SELECT id FROM claim ORDER BY id LIMIT 1")).scalar_one()
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO treatment_plan_step (claim_id, step_no, description) "
                    "VALUES (:claim, 1, 'duplicate')"
                ).bindparams(claim=claim_pk)
            )
        conn.rollback()


def test_the_app_role_can_write_both_tables(engine: Any) -> None:
    """AD-4's grant surface. `additional_injury` needs INSERT and DELETE by
    definition — that is the whole feature — and `treatment_plan_step` is
    granted alike so Story 8.1's purge can delete a purged claim's rows."""
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = 'lineworker_app' "
                "AND table_name IN ('additional_injury', 'treatment_plan_step')"
            )
        ).all()

    granted: dict[str, set[str]] = {}
    for table, privilege in rows:
        granted.setdefault(table, set()).add(privilege)

    assert granted["additional_injury"] >= {"SELECT", "INSERT", "UPDATE", "DELETE"}
    assert granted["treatment_plan_step"] >= {"SELECT", "INSERT", "UPDATE", "DELETE"}


def test_every_claim_has_its_treatment_plan_in_the_seeds_order(engine: Any) -> None:
    """500 rows, five per claim, tied back to the seed file.

    The comparison is per claim and by step number, not by count: a migration
    that inserted every claim's plan against the *first* claim would produce
    the same total and nothing else would notice.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT c.claim_id, s.step_no, s.description "
                "FROM treatment_plan_step s JOIN claim c ON c.id = s.claim_id "
                "ORDER BY c.claim_id, s.step_no"
            )
        ).all()

    stored: dict[str, list[str]] = {}
    for claim_id, step_no, description in rows:
        plan = stored.setdefault(claim_id, [])
        assert step_no == len(plan) + 1, f"{claim_id} has a gap or a repeat at step {step_no}"
        plan.append(description)

    expected = {
        claim["claim_id"]: seed_fixture.treatment_plan_for(claim["claim_id"])
        for claim in seed_fixture.seed()["claims"]
    }
    assert stored == expected
    assert len(rows) == sum(len(plan) for plan in expected.values())


def test_every_claim_carries_all_four_prognosis_values(engine: Any) -> None:
    """NOT NULL is asserted by the schema; *correct* is asserted here.

    A claim with a blank prognosis card would be indistinguishable from one
    whose prognosis nobody recorded, which is why 0015 tightens the columns
    only after it has filled them.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(f"SELECT claim_id, {', '.join(PROGNOSIS_COLUMNS)} FROM claim")
        ).all()

    assert len(rows) == len(seed_fixture.seed()["claims"])
    for claim_id, mmi, rtw, impairment, litigation in rows:
        assert seed_fixture.prognosis_for(claim_id) == {
            "mmi": mmi,
            "rtw": rtw,
            "impairment": impairment,
            "litigation": litigation,
        }


def test_the_prognosis_columns_refuse_a_null(engine: Any) -> None:
    with engine.connect() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(sa.text("UPDATE claim SET prognosis_mmi = NULL"))
        conn.rollback()


def test_additional_injury_is_seeded_empty(engine: Any) -> None:
    """The correct row count, not a gap.

    The prototype's `additionalInjuries` store has no entries for any of the
    100 claims, so there is nothing to extract; seeding it would mean
    inventing clinical data. Asserted so that a later migration doing exactly
    that fails rather than passing quietly.
    """
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM additional_injury")).scalar_one() == 0
