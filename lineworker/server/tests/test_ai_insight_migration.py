"""Story 6.2 AC 1 — migration 0042, asserted against a real database.

Mirrors the existing `test_*_migration.py` modules and separates the same two
kinds of claim they do:

1. **The vocabulary is written down twice and must agree.** `insight_kind`'s
   members are a frozen literal in the migration and a `StrEnum` in
   `data/models/enums.py`, and the split is deliberate — a migration that read a
   live enum would silently change what it did the day somebody added a fifth
   kind. What the split needs is a test that the two lists say the same thing
   *today*. This half needs no database.
2. **The schema is what the migration said it was**: the table, the native enum
   type, the unique constraint that makes the cache a cache, the NOT NULLs, the
   foreign key and the grants. This half needs one.

The unique constraint deserves its own sentence, because it is the one piece of
DDL here that is load-bearing rather than descriptive. Without `(claim_id,
kind)` there is no conflict target, so `upsert_insight` stops being one
idempotent statement and becomes a select-then-branch with a race in the gap —
and a re-refresh appends rather than replaces, which turns a cache into a
history nobody asked for and a tab into eight cards.
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from data.models.enums import InsightKind
from tests.conftest import requires_db

SERVER_ROOT = Path(__file__).resolve().parents[1]


def _load_migration(filename: str, alias: str) -> Any:
    """Loaded by path rather than imported, `test_embedding_tables_migration.py`'s
    device: `data/versions/` is Alembic's script directory, not a package, and
    the module names begin with a digit."""
    spec = importlib.util.spec_from_file_location(
        alias, SERVER_ROOT / "data" / "versions" / filename
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m0042 = _load_migration("20260820_0042_ai_insight.py", "_m0042")


# --- 1. one vocabulary, written down twice --------------------------------


def test_the_migrations_enum_members_match_the_application_enum() -> None:
    """The frozen literal and the live `StrEnum`, in the same order.

    A mismatch here is the worst kind of quiet: the column would accept the
    members the application does not send and refuse one it does, and the
    symptom would be a `DataError` on exactly one kind of insight, on the day
    somebody added it.

    **Order too, not just membership.** The tab renders the four cards in enum
    order and `services/rag.ALL_KINDS` is `tuple(InsightKind)`, so the sequence
    is a decision rather than an accident — and a migration whose members were
    the same set in a different order would create a type whose `enum_range` a
    reader could not line up against the code.
    """
    assert tuple(kind.value for kind in InsightKind) == m0042.INSIGHT_KINDS


def test_the_revision_chains_off_the_knowledge_corpus_seed() -> None:
    """0042 follows 0041, which is the head Story 6.1 left.

    Asserted because a revision with the wrong `down_revision` is an Alembic
    error nobody sees until a deploy, and because "this story adds one
    revision" is a claim about the chain rather than about the file.
    """
    assert m0042.revision == "0042_ai_insight"
    assert m0042.down_revision == "0041_seed_knowledge_corpus"


# --- 2. the schema --------------------------------------------------------


@pytest.fixture
def engine(seeded_db_url: str) -> Any:
    created = sa.create_engine(seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        yield created
    finally:
        created.dispose()


@requires_db
def test_the_table_exists_with_its_columns_and_nullability(engine: Any) -> None:
    """Six columns, and the three NOT NULLs are the ones AD-10 depends on.

    `content`, `model` and `generated_at` are all NOT NULL, and each is a
    statement rather than a default. A row with no content is not a card; a row
    with no model cannot be labelled, and the label is half of what stops a
    generated sentence reading as a claim fact; a row with no `generated_at`
    breaks AD-10's rule that an AI narrative is always rendered with its age.

    **And there is no `version` column**, asserted rather than left as an
    absence: FR-H-9's "never user-editable" is structural, because a version is
    what an inline edit would send back as `expectedVersion`. A later story that
    added one would be proposing that model output become claim data, and this
    is where that gets noticed.
    """
    with engine.connect() as conn:
        columns = dict(
            conn.execute(
                sa.text(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'ai_insight'"
                )
            ).all()
        )

    assert set(columns) == {"id", "claim_id", "kind", "content", "model", "generated_at"}
    assert "version" not in columns
    for column in ("claim_id", "kind", "content", "model", "generated_at"):
        assert columns[column] == "NO", column


@requires_db
def test_the_kind_column_is_a_native_enum_holding_exactly_the_four_members(
    engine: Any,
) -> None:
    """A native type rather than text, and its members read back from the catalogue.

    Native because the vocabulary is closed and a typo should be a database
    error rather than a fifth kind that renders nowhere — the same call every
    other enum column in this schema makes.
    """
    with engine.connect() as conn:
        declared = conn.execute(
            sa.text(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = CAST('ai_insight' AS regclass) AND attname = 'kind'"
            )
        ).scalar_one()
        members = (
            conn.execute(
                sa.text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = 'insight_kind' ORDER BY e.enumsortorder"
                )
            )
            .scalars()
            .all()
        )

    assert declared == "insight_kind"
    assert tuple(members) == m0042.INSIGHT_KINDS


@requires_db
def test_the_cache_key_refuses_a_second_row_for_one_claim_and_kind(engine: Any) -> None:
    """`UNIQUE (claim_id, kind)` — the constraint the whole cache rests on.

    Asserted by asking for the failure, `test_the_unique_constraints_that_make_
    the_upserts_one_statement`'s method: without it a re-refresh appends instead
    of replacing, and `upsert_insight` has no conflict target to name.

    Two rows are inserted rather than one, because the constraint is over a
    *pair*: a version that had been declared on `claim_id` alone would refuse
    the second kind, and this test would pass for the wrong reason.
    """
    with engine.connect() as conn:
        claim_pk = conn.execute(sa.text("SELECT id FROM claim ORDER BY id LIMIT 1")).scalar_one()
        insert = sa.text(
            "INSERT INTO ai_insight (claim_id, kind, content, model, generated_at) "
            "VALUES (:pk, CAST(:kind AS insight_kind), '{}'::jsonb, 'test', now())"
        )
        # Two kinds on one claim is legal and is the normal case.
        conn.execute(insert, {"pk": claim_pk, "kind": "next_best_actions"})
        conn.execute(insert, {"pk": claim_pk, "kind": "fraud_risk_indicators"})
        # The same pair twice is not.
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(insert, {"pk": claim_pk, "kind": "next_best_actions"})
        conn.rollback()


@requires_db
def test_the_claim_foreign_key_refuses_an_orphan(engine: Any) -> None:
    """An insight belongs to a claim, and the database says so.

    It also gives Story 8.1's purge cascade a constraint to cascade along — and
    the constraint's backing unique index is the index the foreign key needs,
    which PostgreSQL does not create automatically. Without one, purging a claim
    would scan this table once per deleted row.
    """
    with engine.connect() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO ai_insight (claim_id, kind, content, model, generated_at) "
                    "VALUES (-1, CAST('next_best_actions' AS insight_kind), "
                    "'{}'::jsonb, 'test', now())"
                )
            )
        conn.rollback()


@requires_db
def test_the_app_role_holds_the_full_crud_grant(engine: Any) -> None:
    """AD-4's grant statement, and why it is not narrowed to SELECT + INSERT.

    `services/rag` INSERTs and UPDATEs on every refresh. DELETE is there because
    this table is PHI-class under AD-11 — a narrative about a claim is claim
    data — and Story 8.1's purge cascade should not discover that it cannot
    touch a table. That `services/rag` is the *only* application writer is a
    design rule enforced by where the code lives, not by the grant.
    """
    with engine.connect() as conn:
        granted = (
            conn.execute(
                sa.text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = 'ai_insight' AND grantee = 'lineworker_app'"
                )
            )
            .scalars()
            .all()
        )

    assert set(granted) == {"SELECT", "INSERT", "UPDATE", "DELETE"}


@requires_db
def test_the_migration_seeds_nothing(engine: Any) -> None:
    """0042 creates the table and leaves it empty, unlike 0041 next door.

    A migration cannot reach a model server, and an insight's whole content is a
    model's answer — so there is nothing here to pre-create, not even the
    "pending work" rows 0041 writes for the embedding queue. The empty state is
    a first-class answer instead: the API reports every missing kind as
    `not_generated` and the tab renders four explicit empty cards.

    Asserted rather than assumed because the alternative is tempting and would
    be wrong: seeding a placeholder row per claim and kind would give the tab
    something to render and would be pre-authored text presented as AI output,
    which AD-14 forbids outright.
    """
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT count(*) FROM ai_insight")).scalar_one()

    assert rows == 0


@requires_db
def test_the_attempt_table_is_one_row_per_claim_with_its_own_grant(engine: Any) -> None:
    """`ai_insight_attempt` — the queue's cursor, added by the Story 6.2 review.

    Two columns and a primary key on `claim_id`, which is the whole design: one
    row per claim, replaced in place, no history and no surrogate id. The
    scheduled refresh orders its pending set by `attempted_at`, so a claim that
    has just been tried goes behind every other pending claim — which is what
    stops a claim the model deterministically refuses from holding the head of
    every batch for ever (H4).

    It is a separate table rather than a column because a *failed* kind writes no
    `ai_insight` row at all, so there is nowhere on that table for a failure to
    record itself. The primary key is asserted rather than a unique constraint
    because it is what makes `ON CONFLICT (claim_id)` a legal conflict target.

    The grant is asserted for `ai_insight`'s reason: DELETE is there for Story
    8.1's purge cascade, which follows the foreign key from `claim`.
    """
    with engine.connect() as conn:
        columns = dict(
            conn.execute(
                sa.text(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'ai_insight_attempt'"
                )
            ).all()
        )
        primary_key = (
            conn.execute(
                sa.text(
                    "SELECT a.attname FROM pg_index i "
                    "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                    "AND a.attnum = ANY(i.indkey) "
                    "WHERE i.indrelid = CAST('ai_insight_attempt' AS regclass) AND i.indisprimary"
                )
            )
            .scalars()
            .all()
        )
        granted = (
            conn.execute(
                sa.text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = 'ai_insight_attempt' AND grantee = 'lineworker_app'"
                )
            )
            .scalars()
            .all()
        )

    assert set(columns) == {"claim_id", "attempted_at"}
    assert columns["claim_id"] == "NO"
    assert columns["attempted_at"] == "NO"
    assert list(primary_key) == ["claim_id"]
    assert set(granted) == {"SELECT", "INSERT", "UPDATE", "DELETE"}

    # A second attempt for one claim replaces rather than appends, which is what
    # `ON CONFLICT (claim_id) DO UPDATE` needs the key above for.
    with engine.connect() as conn:
        claim_pk = conn.execute(sa.text("SELECT id FROM claim ORDER BY id LIMIT 1")).scalar_one()
        insert = sa.text(
            "INSERT INTO ai_insight_attempt (claim_id, attempted_at) VALUES (:pk, now())"
        )
        conn.execute(insert, {"pk": claim_pk})
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(insert, {"pk": claim_pk})
        conn.rollback()
