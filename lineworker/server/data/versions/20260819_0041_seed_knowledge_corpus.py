"""Seed the labour-law knowledge corpus, and create the embedding work
(Story 6.1, AC 2 and AC 4).

Loads `data/seed/knowledge_chunks.json` — fifteen chunks, three each for the
five jurisdictions the seeded portfolio actually files claims in (OH, MN, IL,
WA, TX) — and then does something no other seed revision in this tree does: it
inserts one *empty* `knowledge_embedding` row per chunk and one *empty*
`claim_embedding` row per claim, every one of them with a NULL vector and, for
claims, `stale = true`.

## Why the migration creates rows it cannot fill

A migration cannot reach Ollama. There is no model server during
`alembic upgrade head` and there must not be one, or applying a schema
migration would fail because a GPU box was rebooting. So the vectors cannot be
computed here — and the alternative, having `services/rag` insert a row the
first time it embeds a claim, was rejected because it makes three separate
things harder at once:

- **`mark_claim_stale` stops being an upsert.** Story 2.3's and 2.4's commands
  call it inside their own transaction for a claim that may never have been
  embedded. With a row already present that call is `UPDATE … SET stale =
  true`; without one it is a create-or-update whose create half runs inside a
  handler's edit transaction, on a table that edit has no other business
  writing to. (It is still written as an idempotent upsert — see
  `services/rag/embeddings.py` — because a claim inserted after this migration
  by some later story would otherwise have no row. What this seed buys is that
  the upsert's insert branch is never the normal path.)
- **"Stale first" has no meaning on a fresh database.** The refresh command
  orders `stale DESC, embedded_at ASC NULLS FIRST` over rows that are pending
  either because they changed or because they never existed. If pending rows
  did not exist as rows, the ordering would be over a set the query cannot see.
- **Nothing would show the work is outstanding.** `SELECT count(*) FROM
  claim_embedding WHERE embedding IS NULL` is the operator's answer to "has
  this deployment finished its first embedding pass?", and it only works if the
  pending rows are rows.

`refresh_stale_embeddings` therefore stays the single code path that ever
writes a vector, which is exactly the AD-12 ownership statement this story is
about.

## `INSERT … SELECT`, not a Python loop

Both fan-outs are one statement each, sourced from the table they mirror. A
Python loop would need the claim ids read back first and would insert 100 rows
one at a time for no benefit; more importantly, `INSERT INTO claim_embedding
(claim_id, stale) SELECT id, true FROM claim` cannot drift from the set of
claims, whereas a loop over a list read a moment earlier can.

Both are also `ON CONFLICT DO NOTHING`. The unique constraints from 0040 make
that meaningful rather than defensive: it is what lets this revision be safe if
a future story inserts claims before it in a chain somebody re-orders, and it
costs one index probe per row.

## The corpus is synthetic, and says so in every row

Every `source` begins `synthetic-demo:` and every `chunk_text` opens with a
sentence stating that it is demonstration text rather than statutory law. That
is not decoration. The architecture's Deferred list holds knowledge-corpus
sourcing — where real statutory text comes from, how it is chunked, how often
it is refreshed — as an open decision, and this story is explicitly forbidden
from building an ingestion pipeline. What it needs is enough real rows that the
RAG path retrieves something rather than nothing. Presenting paraphrased
generalities as authoritative law would be the same failure NFR-4 and
`deferred-work.md` already record against the statutory forms: a surface that
behaves exactly like the real thing while being sourced from nobody. The label
travels *inside* the retrieved text so that a model quoting a chunk quotes the
disclaimer with it.

The five states are the seeded portfolio's five most common jurisdictions, so
Story 6.4's labour-law action retrieves something relevant for a plurality of
the book rather than for a state nobody has a claim in.

Revision ID: 0041_seed_knowledge_corpus
Revises: 0040_embedding_tables
Create Date: 2026-08-19

"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0041_seed_knowledge_corpus"
down_revision: str | None = "0040_embedding_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_PATH = Path(__file__).parent.parent / "seed" / "knowledge_chunks.json"

COLUMNS = {"source", "state_code", "title", "chunk_text", "chunk_index"}

#: Three chunks for each of five jurisdictions. A literal for 0009's reason
#: about globbing: a seed file that lost a state would otherwise migrate
#: cleanly and leave the labour-law action answering nothing for a fifth of the
#: portfolio, which reads as "no rule applies" rather than as a missing file.
EXPECTED_CHUNKS = 15
EXPECTED_STATES = 5

#: The label every row carries. Asserted here as well as in
#: `tests/test_rag_embeddings.py` so that a row sourced from somewhere real
#: cannot arrive without somebody first deciding what it is being cited as.
SYNTHETIC_SOURCE_PREFIX = "synthetic-demo:"


def _load() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(SEED_PATH.read_text(encoding="utf-8"))

    # A shape check before the insert, because the failure without one is
    # unreadable: drifted seed content reaches the database as a NOT NULL
    # violation or a CompileError about a bind parameter and leaves the chain
    # halted with the table created and empty — which the retrieval path then
    # serves as a jurisdiction with no rules (0027's argument).
    for index, row in enumerate(rows):
        if set(row) != COLUMNS:
            raise ValueError(
                f"{SEED_PATH}[{index}] has fields {sorted(row)}, expected {sorted(COLUMNS)}"
            )
        if not str(row["source"]).startswith(SYNTHETIC_SOURCE_PREFIX):
            raise ValueError(
                f"{SEED_PATH}[{index}] has source {row['source']!r}, which does not begin "
                f"{SYNTHETIC_SOURCE_PREFIX!r} — the corpus is demonstration text and every "
                "row has to say so, because nothing downstream re-checks it"
            )

    # Falsy `state_code`s dropped rather than counted: a row that lost its
    # state would otherwise contribute `None` to the set and let a corpus
    # missing a jurisdiction pass a check whose whole purpose is to catch that.
    states = {row["state_code"] for row in rows if row["state_code"]}
    if len(rows) != EXPECTED_CHUNKS or len(states) != EXPECTED_STATES:
        raise ValueError(
            f"{SEED_PATH} holds {len(rows)} chunks across {len(states)} states, expected "
            f"{EXPECTED_CHUNKS} across {EXPECTED_STATES}"
        )
    return rows


def upgrade() -> None:
    chunks = _load()
    bind = op.get_bind()

    # Declared locally rather than reflected or imported from
    # `data/models/core.py`, which is 0021's and 0027's idiom: a migration is a
    # frozen record of what it inserted, and a model that grows a column later
    # must not change what this revision did.
    knowledge_chunk = sa.table(
        "knowledge_chunk",
        sa.column("source", sa.Text),
        sa.column("state_code", sa.Text),
        sa.column("title", sa.Text),
        sa.column("chunk_text", sa.Text),
        sa.column("chunk_index", sa.Integer),
    )
    op.bulk_insert(knowledge_chunk, chunks)

    # One embedding row per chunk and one per claim, all pending. See the
    # module docstring for why the migration creates work rather than skipping
    # rows it cannot compute.
    bind.execute(
        sa.text(
            "INSERT INTO knowledge_embedding (chunk_id) "
            "SELECT id FROM knowledge_chunk ON CONFLICT DO NOTHING"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO claim_embedding (claim_id, stale) "
            "SELECT id, true FROM claim ON CONFLICT DO NOTHING"
        )
    )

    # Coverage, not just a row count: a claim with no pending embedding row is
    # a claim the refresh will never reach, and it would look identical to one
    # that had already been embedded.
    pending_claims = bind.execute(
        sa.text("SELECT count(*) FROM claim c LEFT JOIN claim_embedding e ON e.claim_id = c.id")
    ).scalar_one()
    uncovered = bind.execute(
        sa.text(
            "SELECT count(*) FROM claim c "
            "WHERE NOT EXISTS (SELECT 1 FROM claim_embedding e WHERE e.claim_id = c.id)"
        )
    ).scalar_one()
    if uncovered:
        raise ValueError(
            f"{uncovered} of {pending_claims} seeded claims have no claim_embedding row — "
            "the fan-out insert did not cover the portfolio"
        )


def downgrade() -> None:
    # The embedding rows first: both reference tables this deletes from or,
    # in `claim_embedding`'s case, reference `claim`, which this revision did
    # not create and must not touch.
    op.execute("DELETE FROM claim_embedding")
    op.execute("DELETE FROM knowledge_embedding")
    op.execute("DELETE FROM knowledge_chunk")
