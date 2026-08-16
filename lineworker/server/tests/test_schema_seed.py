"""Story 1.2 verification: migrations, seed, grants (AC 1-4).

DB-backed tests need MIGRATION_TEST_DATABASE_URL (an owner/superuser URL to
a disposable database — dropped and re-migrated here). Locally:

    docker run -d --name lw-test-pg -e POSTGRES_USER=lineworker \
      -e POSTGRES_PASSWORD=test -e POSTGRES_DB=lineworker \
      -p 55432:5432 pgvector/pgvector:pg18
    MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker \
      uv run pytest tests/test_schema_seed.py
"""

import os
import shutil
import subprocess

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from config import Settings
from data.roles import sync_app_role

MIG_URL = os.environ.get("MIGRATION_TEST_DATABASE_URL")
APP_PASSWORD = os.environ.get("APP_DB_PASSWORD", "lineworker_app_dev")

pytestmark = pytest.mark.skipif(
    MIG_URL is None, reason="MIGRATION_TEST_DATABASE_URL not set (needs a disposable Postgres)"
)


def _sync(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def _alembic(*args: str) -> None:
    alembic = shutil.which("alembic")
    assert alembic, "alembic not on PATH"
    env = dict(os.environ, ALEMBIC_DATABASE_URL=str(MIG_URL), APP_DB_PASSWORD=APP_PASSWORD)
    subprocess.run([alembic, *args], check=True, env=env, capture_output=True)


@pytest.fixture(scope="module")
def engine() -> sa.Engine:
    """Fresh schema → alembic upgrade head, once per module."""
    eng = sa.create_engine(_sync(str(MIG_URL)), isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
    _alembic("upgrade", "head")
    return eng


@pytest.fixture(scope="module")
def app_engine(engine: sa.Engine) -> sa.Engine:
    """Connection as the runtime app role (created by migration 0002)."""
    url = sa.make_url(_sync(str(MIG_URL))).set(username="lineworker_app", password=APP_PASSWORD)
    return sa.create_engine(url)


def test_seed_counts(engine: sa.Engine) -> None:
    with engine.connect() as conn:
        counts = {
            t: conn.execute(sa.text(f"SELECT count(*) FROM {t}")).scalar_one()
            for t in ("claim", "employee", "employer", "app_user", "user_employer_assignment")
        }
    assert counts["claim"] == 100
    assert counts["employee"] == 100
    assert counts["employer"] == 10
    # Ten personas plus Story 3.4's system actor, which is not a persona: it is
    # the identity the payment batch audits under, it appears in no login
    # picker, and `get_persona` refuses it. `test_the_system_actor_cannot_log_in`
    # in `tests/test_personas.py` is where that is enforced rather than counted.
    assert counts["app_user"] == 11
    assert counts["user_employer_assignment"] == 16  # HANDLER_MAP partitions


def test_jennifer_park_scope(engine: sa.Engine) -> None:
    with engine.connect() as conn:
        employers = (
            conn.execute(
                sa.text(
                    "SELECT e.name FROM app_user u "
                    "JOIN user_employer_assignment a ON a.user_id = u.id "
                    "JOIN employer e ON e.id = a.employer_id "
                    "WHERE u.name = 'Jennifer Park' AND u.role = 'supervisor' ORDER BY e.name"
                )
            )
            .scalars()
            .all()
        )
    assert employers == ["3M Company", "General Motors", "Toyota Motor Manufacturing"]


def test_scope_all_personas_have_no_assignment_rows(engine: sa.Engine) -> None:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                sa.text(
                    "SELECT u.name, u.role, u.scope_all, count(a.id) AS assignments "
                    "FROM app_user u LEFT JOIN user_employer_assignment a ON a.user_id = u.id "
                    "GROUP BY u.id"
                )
            )
            .mappings()
            .all()
        )
    for row in rows:
        if row["scope_all"]:
            assert row["assignments"] == 0, f"{row['name']}|{row['role']} has assignment rows"
    bline_rows = [r for r in rows if r["name"] == "David Bline"]
    assert {r["role"] for r in bline_rows} == {"supervisor", "analyst"}
    assert all(r["scope_all"] for r in bline_rows)


def test_wc_business_id_unique(engine: sa.Engine) -> None:
    # OVERRIDING USER VALUE takes a fresh identity id, so the duplicate
    # trips the WC-nnnn unique constraint, not the primary key.
    with engine.connect() as conn, pytest.raises(IntegrityError, match="uq_claim_claim_id"):
        conn.execute(
            sa.text(
                "INSERT INTO claim OVERRIDING USER VALUE "
                "SELECT * FROM claim WHERE claim_id = 'WC-20017'"
            )
        )


def test_app_role_audit_grants(app_engine: sa.Engine) -> None:
    """AC 3: INSERT allowed; UPDATE and DELETE refused by the DB itself."""
    insert = sa.text(
        "INSERT INTO audit_event "
        "(at, actor_id, actor_role, action, entity, entity_id, before, after) "
        "VALUES (now(), (SELECT id FROM app_user LIMIT 1), 'handler', 'test', 'claim', "
        "'WC-20017', NULL, '{}'::jsonb) RETURNING id"
    )
    with app_engine.begin() as conn:
        event_id = conn.execute(insert).scalar_one()
    with app_engine.connect() as conn, pytest.raises(ProgrammingError, match="permission denied"):
        conn.execute(sa.text("UPDATE audit_event SET action = 'x' WHERE id = :i"), {"i": event_id})
    with app_engine.connect() as conn, pytest.raises(ProgrammingError, match="permission denied"):
        conn.execute(sa.text("DELETE FROM audit_event WHERE id = :i"), {"i": event_id})


def test_audit_redactor_can_redact_specific_rows(engine: sa.Engine) -> None:
    """AD-4: the redactor's grants must permit a *targeted* purge.

    Both failure modes this guards are silent-ish: without the SELECT grant a
    qualified statement raises "permission denied" and only the unqualified
    one (rewrite every row) runs; without the SELECT policy it raises nothing
    at all and simply matches zero rows.
    """
    old, recent = "WC-REDACT-OLD", "WC-REDACT-RECENT"
    insert = sa.text(
        "INSERT INTO audit_event "
        "(at, actor_id, actor_role, action, entity, entity_id, before, after) VALUES "
        "(now() - (:age)::interval, (SELECT id FROM app_user LIMIT 1), 'handler', "
        "'test', 'claim', :e, '{\"a\": 1}'::jsonb, '{\"a\": 2}'::jsonb)"
    )
    with engine.begin() as conn:
        conn.execute(insert, {"e": old, "age": "10 years"})
        conn.execute(insert, {"e": recent, "age": "1 day"})
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("SET ROLE audit_redactor"))
            try:
                redact = sa.text(
                    'UPDATE audit_event SET "before" = NULL, "after" = NULL WHERE entity_id = :e'
                )
                delete = sa.text("DELETE FROM audit_event WHERE entity_id = :e")
                # Redaction reaches any row, inside the retention window or not.
                assert conn.execute(redact, {"e": old}).rowcount == 1
                assert conn.execute(redact, {"e": recent}).rowcount == 1
                # Deletion stays bounded by the RLS retention floor.
                assert conn.execute(delete, {"e": old}).rowcount == 1
                assert conn.execute(delete, {"e": recent}).rowcount == 0
            finally:
                conn.execute(sa.text("RESET ROLE"))
    finally:
        with engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM audit_event WHERE entity_id IN (:o, :r)"),
                {"o": old, "r": recent},
            )


def test_app_password_rotation_is_applied(engine: sa.Engine) -> None:
    """A rotated APP_DB_PASSWORD must reach the role.

    `alembic upgrade head` is a no-op on an already-migrated database, so the
    role sync the entrypoint runs on every boot is the only thing that keeps
    the api able to authenticate after a password change.
    """
    rotated = f"{APP_PASSWORD}_rotated"

    def _settings(password: str) -> Settings:
        return Settings(alembic_database_url=str(MIG_URL), app_db_password=password)

    def _can_connect(password: str) -> bool:
        url = sa.make_url(_sync(str(MIG_URL))).set(username="lineworker_app", password=password)
        try:
            with sa.create_engine(url).connect():
                return True
        except OperationalError:
            return False

    sync_app_role(_settings(rotated))
    try:
        assert _can_connect(rotated)
        assert not _can_connect(APP_PASSWORD)
    finally:
        sync_app_role(_settings(APP_PASSWORD))
    assert _can_connect(APP_PASSWORD)


def test_app_role_has_no_ddl(app_engine: sa.Engine) -> None:
    with app_engine.connect() as conn, pytest.raises(ProgrammingError, match="permission denied"):
        conn.execute(sa.text("CREATE TABLE should_fail (id int)"))


def test_downgrade_upgrade_round_trip(engine: sa.Engine) -> None:
    _alembic("downgrade", "base")
    _alembic("upgrade", "head")
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM claim")).scalar_one() == 100
