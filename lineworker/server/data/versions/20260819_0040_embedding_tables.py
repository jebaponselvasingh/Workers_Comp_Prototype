"""claim_embedding + knowledge_chunk + knowledge_embedding — the first vector
columns in the build (Story 6.1, AC 2).

Migration 0002 ran `CREATE EXTENSION vector` on the first day of the project
and nothing has used it since: for thirty-seven revisions pgvector has been an
installed extension with no column, no index and no Python dependency behind
it. This is where that changes, and the three tables it creates are the whole
storage surface Epic 6 retrieves from.

**Structure only; 0041 seeds.** The split is the one 0035/0036, 0032/0033,
0026/0027, 0020/0021 and 0003/0004 already use, and it keeps `downgrade()`
honest: dropping a table takes its rows with it, and re-seeding is a revision
of its own rather than an edit to a structural migration. It matters more than
usual here, because 0041 does something no other seed does — it pre-creates one
`claim_embedding` row per claim with `stale=true` and a NULL vector, which is
*work to be done* rather than data, and that belongs in a revision a reader can
identify by name.

**Three tables in one revision** for 0035's reason: `knowledge_embedding.chunk_id`
is a foreign key into `knowledge_chunk`, so they cannot be created
independently. `claim_embedding` rides along because it is the same idea about
a different subject and splitting it would produce two revisions nobody would
ever apply separately.

## Why the vector columns are nullable, and every other column with them

`embedding`, `source_text_hash`, `model`, `stale_at` and `embedded_at` are all NULL-able,
and a NULL `embedding` is the meaningful state rather than a missing value: it
is "this row is known to need embedding and has not been embedded yet". A
migration cannot reach Ollama — there is no model server during
`alembic upgrade head`, and there must not be one, or a schema migration would
fail because a GPU box was rebooting. So the schema has to be able to represent
pending work, and `refresh_stale_embeddings` (`services/rag/embeddings.py`) is
the single code path that ever fills one in.

That is also why `stale` is `NOT NULL DEFAULT false` while the vector is
nullable: the two columns answer different questions. `stale` means "the source
text changed under an embedding that already exists"; `embedding IS NULL` means
"there has never been one". The refresh command selects `stale OR embedding IS
NULL OR model IS DISTINCT FROM <the model answering now>` and orders stale
first, so all three are pending and one is more urgent.

## No `version` column, but `claim_embedding` does carry `stale_at`

AD-4's compare-and-swap arbitrates concurrent writers of a mutable row. These
tables have exactly one writer — `services/rag`, by AD-12 — and its writes are
idempotent recomputations of a derived value, not read-modify-writes of a fact
somebody typed. Two refresh runs racing on one claim both write the same vector
for the same `source_text_hash`; there is nothing for a CAS to protect.
`timeline_event` and `audit_event` reach the same conclusion from the other
direction (append-only), and `claim_embedding` is the AD-10 derived-data
version of the same argument: one computing path, never user-writable.

The race that *is* real is a different one, and `version` would not have caught
it: a refresh and a **handler's edit**. The refresh selects a pending row,
composes it, and then waits on one HTTP call for as long as
`EMBEDDING_REQUEST_TIMEOUT_SECONDS` allows; an edit committing inside that
window marks the row stale for a change the in-flight vector does not contain.
`stale_at` is the monotonic marker that makes the clear conditional — see the
column's own comment below. It is not a row version: it never blocks a write,
it only decides whether the flag may be cleared, so a mark-stale that lands
mid-embed survives and the next tick re-embeds.

## `UNIQUE` on the two foreign keys, and what it buys

`claim_embedding.claim_id` and `knowledge_embedding.chunk_id` are both unique,
which makes each a strict 1:1 with its parent. That is what lets
`mark_claim_stale` be an idempotent `INSERT … ON CONFLICT DO UPDATE` rather
than a select-then-branch: without the constraint there is no conflict target,
and the upsert becomes two statements with a race between them. It also makes
"how many embeddings does this claim have?" a question with one answer, which
matters the first time a similar-case result list is assembled and nobody wants
to explain a duplicate.

Both constraints double as the index the FK needs. PostgreSQL does not index a
referencing column automatically, so without one, Story 8.1's purge cascade
would scan `claim_embedding` once per deleted claim (0035's reason for
`ix_email_log_claim_id`) — a UNIQUE constraint is backed by a unique index, so
that scan is already paid for.

## The HNSW indexes are raw SQL, and that is the file's existing habit

`op.create_index` can pass `postgresql_using` and `postgresql_ops`, but the
readable statement of what these are is the SQL itself: an HNSW index over the
cosine operator class, which is what `<=>` uses and therefore what the ordering
in `data/repositories/embeddings.py` can actually be served from. A different
operator class here would leave the index present, valid and never chosen, with
nothing on screen to say so. 0002 sets the precedent of writing DDL out when
the DDL is the point.

**Cosine rather than L2**, because bge-m3's vectors are not normalised to unit
length by the server and cosine distance is what every retrieval example for
this family of models uses; mixing an L2 index with a cosine query operator is
the specific mistake that produces a silently unused index.

The indexes are also declared on the models in `data/models/core.py`, with the
same names, so `Base.metadata` — which is Alembic's `target_metadata` — knows
they exist and a later autogenerate does not propose dropping them.

## Grants

Full CRUD on all three, and each verb is wanted. `services/rag` INSERTs and
UPDATEs on every refresh; DELETE is there because all three tables are
PHI-class under AD-11 — a claim embedding is derived from claim text and a
knowledge embedding is not, but they live in one purge story, and Story 8.1's
cascade should not discover that it cannot touch a table (0035's argument for
`email_template`). That `services/rag` is the *only* application writer is a
design rule enforced by where the code lives, not by the grant (0010's argument
for `timeline_event`).

Revision ID: 0040_embedding_tables
Revises: 0039_supervisor_worklist_cap
Create Date: 2026-08-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0040_embedding_tables"
down_revision: str | None = "0039_supervisor_worklist_cap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: bge-m3's output width, and the reason `EMBEDDING_MODEL` is not a free
#: choice. Written out as a literal here rather than imported from
#: `services/rag/client.py` under the rule 0013, 0014, 0026, 0032 and 0035 set:
#: a migration is a frozen historical record, and one that read a live constant
#: would silently change what it did when somebody re-pointed the deployment at
#: a 768-dimension model. `tests/test_embedding_tables_migration.py` asserts
#: the two agree.
EMBEDDING_DIMENSIONS = 1024

#: Named as constants so `downgrade()` and `data/models/core.py` spell them the
#: same way. `op.f()` is not used because these are not names Alembic's
#: convention would have generated — the convention has no rendering for an
#: index built by a raw `CREATE INDEX`.
CLAIM_EMBEDDING_HNSW_INDEX = "ix_claim_embedding_embedding_hnsw"
KNOWLEDGE_EMBEDDING_HNSW_INDEX = "ix_knowledge_embedding_embedding_hnsw"


def upgrade() -> None:
    op.create_table(
        "claim_embedding",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True),
        # The sha256 of the composed claim text, and deliberately **not the
        # text itself**. The summary is re-derivable from the claim at zero
        # cost, so persisting it would put a second copy of PHI in the database
        # for Epic 8's purge cascade to chase in exchange for nothing.
        #
        # It is evidence, not a queue. Nothing selects on it: the refresh's
        # pending predicate is `stale OR embedding IS NULL OR model changed`,
        # because the flag is set by the commands that know a composer input
        # moved and needs no recomputation to be read. What the hash answers is
        # "did the summary *really* change?" — asked by
        # `tests/test_embedding_staleness.py`, which would otherwise pass
        # against a composer that had quietly dropped the edited field, and by
        # whoever is holding two rows and a retrieval result that disagree.
        sa.Column("source_text_hash", sa.Text(), nullable=True),
        # "The source text changed under a vector that already exists." Set by
        # `mark_claim_stale` inside the mutating command's own transaction and
        # cleared by the refresh — but only when `stale_at` below says no
        # second edit landed meanwhile.
        sa.Column("stale", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        # When the row was last marked stale, and the reason it is a column
        # rather than an inference from `embedded_at`. A refresh selects a
        # pending row, composes it, and then spends up to
        # EMBEDDING_REQUEST_TIMEOUT_SECONDS inside one HTTP call; a handler's
        # edit can commit inside that window, setting `stale = true` for a
        # change the in-flight vector does not contain. A write that cleared
        # the flag unconditionally would store the pre-edit vector with a fresh
        # `embedded_at` beside it and leave the row permanently wrong — the
        # exact "wrong answer with a fresh timestamp" this table exists to make
        # impossible.
        #
        # So the marker is monotonic and the clear is conditional: the refresh
        # reads `stale_at` before embedding and clears `stale` only if the
        # column still holds that value. A mark that landed mid-flight moves it,
        # the clear declines, and the next tick re-embeds. NULL is the
        # never-marked state, which is what 0041's pre-created rows carry.
        sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
        # Which model produced the vector. Provenance *and* a pending
        # condition: the refresh treats a row whose `model` is not the model
        # the current client answers as pending, so re-pointing
        # `EMBEDDING_MODEL` at another 1024-dimension model re-embeds the
        # portfolio instead of leaving two incomparable vector spaces mixed in
        # one index. It is also the first thing anybody debugging a retrieval
        # result asks for.
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claim.id"], name=op.f("fk_claim_embedding_claim_id_claim")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claim_embedding")),
        sa.UniqueConstraint("claim_id", name=op.f("uq_claim_embedding_claim_id")),
    )

    op.create_table(
        "knowledge_chunk",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        # Where the chunk came from. Every seeded row's value begins
        # `synthetic-demo:` and that prefix is load-bearing rather than
        # decorative — see 0041 and `data/seed/knowledge_chunks.json`.
        sa.Column("source", sa.Text(), nullable=False),
        # Nullable: a chunk about a jurisdiction carries its two-letter code, a
        # chunk about something general (the definition of maximum medical
        # improvement) belongs to no state and must not be filed under one.
        sa.Column("state_code", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_chunk")),
        # `(source, chunk_index)` rather than either alone: a source is a
        # document and its chunks are ordered within it, so the pair is the
        # chunk's identity. Without this a re-seed that ran twice would produce
        # two of every chunk and a knowledge search would return the same
        # paragraph twice under one heading — the failure `treatment_plan_step`
        # takes the same constraint to prevent.
        sa.UniqueConstraint("source", "chunk_index", name=op.f("uq_knowledge_chunk_source")),
    )

    op.create_table(
        "knowledge_embedding",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("chunk_id", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["knowledge_chunk.id"],
            name=op.f("fk_knowledge_embedding_chunk_id_knowledge_chunk"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_embedding")),
        sa.UniqueConstraint("chunk_id", name=op.f("uq_knowledge_embedding_chunk_id")),
    )

    # No `stale` / `stale_at` pair here, and the asymmetry with
    # `claim_embedding` is the point: a knowledge chunk is immutable reference
    # text written by a migration, so the only way its embedding becomes wrong
    # is a model change — which `model` records and which the refresh's pending
    # predicate acts on, invalidating every row at once rather than one row at a
    # time. A claim's summary changes when a handler edits the claim, which is a
    # per-row event, needs a per-row flag, and needs the flag to survive an edit
    # that lands mid-embed.

    for index, table in (
        (CLAIM_EMBEDDING_HNSW_INDEX, "claim_embedding"),
        (KNOWLEDGE_EMBEDDING_HNSW_INDEX, "knowledge_embedding"),
    ):
        op.execute(f"CREATE INDEX {index} ON {table} USING hnsw (embedding vector_cosine_ops)")

    for table in ("claim_embedding", "knowledge_chunk", "knowledge_embedding"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO lineworker_app")
    # The identity sequences are new; Story 1.2's blanket sequence grant ran
    # before they existed, so it has to be repeated for them (0010's note).
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lineworker_app")


def downgrade() -> None:
    # The indexes go with their tables, so they are not dropped by hand — but
    # the child table is, and it goes first: `knowledge_embedding.chunk_id`
    # references `knowledge_chunk`, and dropping the parent while the
    # constraint stands fails (0035's ordering).
    op.drop_table("knowledge_embedding")
    op.drop_table("knowledge_chunk")
    op.drop_table("claim_embedding")
    # Nothing drops the `vector` extension here. 0002 created it and 0002 is
    # where it is dropped; an extension is cluster-shaped state shared with
    # anything else in the database, and a revision that removed it on the way
    # down would break a downgrade to any point between 0002 and here.
