"""Story 6.1 AC 2 — migrations 0040 and 0041, asserted against a real database.

Mirrors the existing `test_*_migration.py` modules, and separates the same two
kinds of claim they do:

1. **One number, written down three times.** `vector(1024)` is bge-m3's output
   width, and it appears as a column type (migration 0040), as a model constant
   (`data/models/core.py`) and as a runtime validation bound
   (`services/rag/client.py`). Three copies exist for three good reasons — a
   frozen migration must not import a live constant, a column type is not a
   validation, and the client must be able to refuse a wrong-width vector
   without loading the ORM — so the thing to test is that they agree. This half
   needs no database.
2. **The schema is what the migration said it was**: three tables, the two
   unique constraints that make the upserts single statements, two HNSW indexes
   over the *cosine* operator class, the grants, and 0041's pre-created pending
   rows. This half needs one.

The HNSW half deserves its own sentence. An index built with `vector_l2_ops`
against a query ordered by `<=>` is present, valid and never chosen: nothing
fails, nothing warns, and retrieval silently becomes a sequential scan whose
cost only shows up at a portfolio size this seed does not have. So the operator
class is asserted from `pg_indexes`, out of the definition PostgreSQL actually
stored.
"""

import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from data.models.core import (
    CLAIM_EMBEDDING_HNSW_INDEX,
    KNOWLEDGE_EMBEDDING_HNSW_INDEX,
)
from data.models.core import (
    EMBEDDING_DIMENSIONS as MODEL_DIMENSIONS,
)
from services.rag.client import EMBEDDING_DIMENSIONS as CLIENT_DIMENSIONS
from tests.conftest import requires_db

SERVER_ROOT = Path(__file__).resolve().parents[1]


#: Loaded by path rather than imported, `test_financial_tables_migration.py`'s
#: device: `data/versions/` is Alembic's script directory, not a package, and
#: the module names begin with a digit.
def _load_migration(filename: str, alias: str) -> Any:
    spec = importlib.util.spec_from_file_location(
        alias, SERVER_ROOT / "data" / "versions" / filename
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m0040 = _load_migration("20260819_0040_embedding_tables.py", "_m0040")
m0041 = _load_migration("20260819_0041_seed_knowledge_corpus.py", "_m0041")

EXPECTED_CLAIMS = 100
EXPECTED_CHUNKS = 15
TABLES = ("claim_embedding", "knowledge_chunk", "knowledge_embedding")


# --- 1. the three copies of one number ------------------------------------


def test_the_dimension_is_one_number_in_three_places() -> None:
    """The migration's literal, the model's constant and the client's bound.

    A mismatch here is the worst kind of quiet: the column would accept what
    the client validated and reject what it did not, or the client would pass a
    vector the column truncates. Whichever way round, the symptom is a
    retrieval result that looks fine and means nothing.
    """
    assert m0040.EMBEDDING_DIMENSIONS == MODEL_DIMENSIONS == CLIENT_DIMENSIONS == 1024


def test_the_index_names_are_spelled_the_same_in_both_places() -> None:
    """The migration creates them by raw SQL; the models declare them.

    Both are needed — the raw SQL because it is the readable statement of the
    operator class, the model declaration because `Base.metadata` is Alembic's
    `target_metadata` and an index the metadata does not know about is a drop a
    later autogenerate proposes in good faith. Two spellings of one name would
    produce exactly that drop-and-recreate on every run.
    """
    assert m0040.CLAIM_EMBEDDING_HNSW_INDEX == CLAIM_EMBEDDING_HNSW_INDEX
    assert m0040.KNOWLEDGE_EMBEDDING_HNSW_INDEX == KNOWLEDGE_EMBEDDING_HNSW_INDEX


def test_the_seed_file_is_labelled_synthetic_throughout() -> None:
    """Read off the file the migration loads, before any database is involved.

    Knowledge-corpus sourcing is a Deferred architecture decision, so the corpus
    must be real rows without asserting real law. The migration refuses a row
    whose source is not prefixed; this asserts the file it will be given.
    """
    rows = m0041._load()

    assert len(rows) == m0041.EXPECTED_CHUNKS == EXPECTED_CHUNKS
    assert {row["state_code"] for row in rows} == {"OH", "MN", "IL", "WA", "TX"}
    for row in rows:
        assert row["source"].startswith(m0041.SYNTHETIC_SOURCE_PREFIX)
        assert "not statutory law" in row["chunk_text"]
        assert "not legal advice" in row["chunk_text"]


def test_the_seed_loader_refuses_an_unlabelled_row() -> None:
    """The guard, asserted by asking for the failure.

    A corpus row sourced from somewhere real is a decision somebody has to take
    on purpose — the migration is where that stops being possible by accident.

    **Written against a row of the right shape**, which is the whole difficulty:
    pointing the loader at some other seed file raises on the *field* check
    before the label check is ever reached, so a test that did that would pass
    for a reason unrelated to its name. This one takes the real corpus, strips
    the prefix from one row, and writes it to a temporary file — so the only
    thing wrong with the input is the thing under test.
    """
    original = m0041.SEED_PATH
    rows = json.loads(original.read_text(encoding="utf-8"))
    rows[0]["source"] = rows[0]["source"].removeprefix(m0041.SYNTHETIC_SOURCE_PREFIX)

    with tempfile.TemporaryDirectory() as tmp:
        unlabelled = Path(tmp) / "knowledge_chunks.json"
        unlabelled.write_text(json.dumps(rows), encoding="utf-8")
        try:
            m0041.SEED_PATH = unlabelled
            with pytest.raises(ValueError, match=m0041.SYNTHETIC_SOURCE_PREFIX):
                m0041._load()
        finally:
            m0041.SEED_PATH = original


def test_the_seed_loader_refuses_a_corpus_missing_a_jurisdiction() -> None:
    """The count guard, and specifically that a NULL state cannot satisfy it.

    `states` is a set, so a row whose `state_code` went missing would have
    contributed `None` and kept the jurisdiction count at five — passing the
    check whose entire purpose is to notice that a fifth of the portfolio has
    no labour-law text behind it.
    """
    original = m0041.SEED_PATH
    rows = json.loads(original.read_text(encoding="utf-8"))
    # Captured before the loop: reading it from `rows[0]` inside would compare
    # against a value the first iteration had already overwritten, and only one
    # of that state's three rows would lose its label — leaving the jurisdiction
    # still represented and the guard correctly silent.
    dropped = rows[0]["state_code"]
    for row in rows:
        if row["state_code"] == dropped:
            row["state_code"] = None

    with tempfile.TemporaryDirectory() as tmp:
        stateless = Path(tmp) / "knowledge_chunks.json"
        stateless.write_text(json.dumps(rows), encoding="utf-8")
        try:
            m0041.SEED_PATH = stateless
            with pytest.raises(ValueError, match="states"):
                m0041._load()
        finally:
            m0041.SEED_PATH = original


# --- 2. the schema -------------------------------------------------------


@pytest.fixture
def engine(seeded_db_url: str) -> Any:
    created = sa.create_engine(seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        yield created
    finally:
        created.dispose()


@requires_db
def test_the_three_tables_exist_with_vector_1024_columns(engine: Any) -> None:
    """The declared column type, read back from the catalogue.

    `format_type` renders the typmod, so this distinguishes `vector(1024)` from
    a bare `vector` — which pgvector accepts, which would index nothing usefully,
    and which no `information_schema` query would tell apart.
    """
    with engine.connect() as conn:
        present = (
            conn.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = ANY(:names) ORDER BY 1"
                ),
                {"names": list(TABLES)},
            )
            .scalars()
            .all()
        )
        assert set(present) == set(TABLES)

        for table in ("claim_embedding", "knowledge_embedding"):
            declared = conn.execute(
                sa.text(
                    "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                    "WHERE attrelid = CAST(:table AS regclass) AND attname = 'embedding'"
                ),
                {"table": table},
            ).scalar_one()
            assert declared == f"vector({MODEL_DIMENSIONS})", table


@requires_db
def test_the_two_hnsw_indexes_use_the_cosine_operator_class(engine: Any) -> None:
    """The index the query can actually be served from.

    `<=>` is cosine distance, so `vector_cosine_ops` is the operator class the
    planner needs to see. An L2 index would be valid, present and never chosen
    — and the only symptom would be a sequential scan at a portfolio size this
    seed does not reach.
    """
    with engine.connect() as conn:
        definitions = dict(
            conn.execute(
                sa.text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND indexname = ANY(:names)"
                ),
                {"names": [CLAIM_EMBEDDING_HNSW_INDEX, KNOWLEDGE_EMBEDDING_HNSW_INDEX]},
            ).all()
        )

    assert set(definitions) == {CLAIM_EMBEDDING_HNSW_INDEX, KNOWLEDGE_EMBEDDING_HNSW_INDEX}
    for name, definition in definitions.items():
        assert "USING hnsw" in definition, name
        assert "vector_cosine_ops" in definition, name


@requires_db
def test_the_unique_constraints_that_make_the_upserts_one_statement(engine: Any) -> None:
    """`claim_embedding.claim_id` and `knowledge_embedding.chunk_id`, both unique.

    Load-bearing rather than tidy: without a conflict target, `mark_claim_stale`
    is a select-then-branch with a race in the gap instead of one idempotent
    `INSERT … ON CONFLICT DO UPDATE`. Asserted by asking for the failure.
    """
    with engine.connect() as conn:
        existing = conn.execute(
            sa.text("SELECT claim_id FROM claim_embedding LIMIT 1")
        ).scalar_one()
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text("INSERT INTO claim_embedding (claim_id) VALUES (:pk)"), {"pk": existing}
            )
        conn.rollback()

        chunk = conn.execute(
            sa.text("SELECT chunk_id FROM knowledge_embedding LIMIT 1")
        ).scalar_one()
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text("INSERT INTO knowledge_embedding (chunk_id) VALUES (:pk)"), {"pk": chunk}
            )
        conn.rollback()


@requires_db
def test_a_chunk_source_cannot_hold_two_of_the_same_index(engine: Any) -> None:
    """`(source, chunk_index)` is the chunk's identity.

    A re-seed run twice would otherwise return the same paragraph twice under
    one heading — `treatment_plan_step` takes the same constraint against the
    same failure.
    """
    with engine.connect() as conn:
        existing = (
            conn.execute(
                sa.text(
                    "SELECT source, state_code, title, chunk_text, chunk_index "
                    "FROM knowledge_chunk LIMIT 1"
                )
            )
            .mappings()
            .one()
        )
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO knowledge_chunk "
                    "(source, state_code, title, chunk_text, chunk_index) VALUES "
                    "(:source, :state_code, :title, :chunk_text, :chunk_index)"
                ),
                dict(existing),
            )
        conn.rollback()


@requires_db
def test_the_migration_pre_creates_the_pending_work(engine: Any) -> None:
    """0041's whole point: a migration cannot reach Ollama, so it creates rows.

    One `claim_embedding` per claim, `stale = true` with a NULL vector, and one
    `knowledge_embedding` per chunk. That is what makes "stale first" meaningful
    on a fresh database, what keeps `mark_claim_stale` an upsert rather than a
    special case, and what makes "has this deployment finished its first pass?"
    answerable with one `count(*)`.
    """
    with engine.connect() as conn:
        claims, embeddings, stale, vectors = conn.execute(
            sa.text(
                "SELECT (SELECT count(*) FROM claim), (SELECT count(*) FROM claim_embedding), "
                "(SELECT count(*) FROM claim_embedding WHERE stale), "
                "(SELECT count(*) FROM claim_embedding WHERE embedding IS NOT NULL)"
            )
        ).one()
        chunks, chunk_embeddings = conn.execute(
            sa.text(
                "SELECT (SELECT count(*) FROM knowledge_chunk), "
                "(SELECT count(*) FROM knowledge_embedding)"
            )
        ).one()

    assert claims == embeddings == stale == EXPECTED_CLAIMS
    assert vectors == 0, "a migration must not invent a vector"
    assert chunks == chunk_embeddings == EXPECTED_CHUNKS


@requires_db
def test_the_app_role_holds_full_crud_on_all_three(engine: Any) -> None:
    """AD-4's grant statement, and why it is not narrowed to SELECT + INSERT.

    `services/rag` inserts and updates on every refresh. DELETE is there because
    all three tables live in one PHI purge story (AD-11), and Story 8.1's
    cascade should not discover that it cannot touch a table — 0035's argument
    for `email_template`, applied to the one table here that holds no claim
    data.
    """
    with engine.connect() as conn:
        for table in TABLES:
            granted = conn.execute(
                sa.text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = :table AND grantee = 'lineworker_app'"
                ),
                {"table": table},
            ).scalars()
            assert set(granted) == {"SELECT", "INSERT", "UPDATE", "DELETE"}, table


@requires_db
def test_an_embedding_row_cannot_outlive_its_parent(engine: Any) -> None:
    """The FKs, asserted by asking for the failure.

    Epic 8's purge cascade depends on these existing; until then their job is to
    make an orphan impossible rather than to make one silently disappear
    (`photo`'s ruling, and 0035's).
    """
    statements = {
        "claim_embedding": "INSERT INTO claim_embedding (claim_id) VALUES (0)",
        "knowledge_embedding": "INSERT INTO knowledge_embedding (chunk_id) VALUES (0)",
    }
    with engine.connect() as conn:
        for table, statement in statements.items():
            with pytest.raises(sa.exc.IntegrityError, match="foreign key"):
                conn.execute(sa.text(statement))
            conn.rollback()
            assert table  # names the failing table in the traceback
