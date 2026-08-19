---
title: 'Story 6.1 — Local Model Serving & Embedding Foundation'
type: 'feature'
created: '2026-08-19'
baseline_revision: '5bb502593d82545f0e225ded8e3b5d6239ae76c7'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: true # the review pass added a schema column with a concurrency guard, restructured the refresh's transaction into per-half savepoints, added a 503 path and a field to a published response, and changed twelve tests — breadth across data model, API contract and transaction semantics is worth an independent look
context:
  - '{project-root}/_bmad-output/implementation-artifacts/6-1-local-model-serving-embedding-foundation.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-6-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** `server/services/rag/` has been an empty `__init__.py` since Story 1.1, there is no Ollama anywhere in `deploy/`, and the only vector artefact in the build is `CREATE EXTENSION vector` from migration 0002 — no vector column, no index, no `pgvector` dependency. Meanwhile five audited commands (Stories 2.3, 2.4, 3.1) already mutate the clinical fields a similar-case search would index and call nothing, an obligation `deferred-work.md` has recorded three times and explicitly assigned to this story. Epic 6's remaining five stories all retrieve from tables that do not exist.

**Approach:** Stand up the AI substrate and nothing above it. An `ollama` service on the internal compose network (GPU overlay for prod, model names as config), one Alembic revision creating `claim_embedding` / `knowledge_chunk` / `knowledge_embedding` with `vector(1024)` + HNSW indexes, a seed revision for a small labelled-synthetic labor-law corpus, and `services/rag` as the single owner of those tables and the *only* module in the build that touches Ollama's embeddings API. Vector search is an ordinary scope-enforcing repository method. Staleness is plumbed end to end: four of the five existing commands call `mark_claim_stale` inside their own transaction, a second scheduled job re-embeds stale rows first, and retrieval carries `embedded_at`. The e2e profile gets `model-stub` — a deterministic Ollama-API-compatible container — so every later Epic 6 spec has a harness.

## Boundaries & Constraints

**Always:**
- AD-5: the `ollama` service declares **no `ports:` mapping** in any profile, ever. `ollama_base_url`, `chat_model`, `embedding_model` are `Settings` fields; no model name is a literal outside a default. No cloud HTTP client, not as a fallback.
- AD-12: `services/rag` performs every write to the three new tables. No other module inserts, updates or deletes them; mutating services request staleness through `mark_claim_stale(db, claim_pk=…)`, which adds to the caller's transaction and **does not commit** — the same contract `audit.record` and `timeline.append` already keep.
- AD-5 / dependency diagram: exactly one module issues the embeddings HTTP call. Nothing in `services/`, `api/` or `data/` calls Ollama's chat endpoint in this story.
- AC 2 literal rule: `services/rag` embeds text; repositories receive `list[float]` vectors and never a string.
- AD-7: every new repository function takes `CallerContext` and reaches claim rows only through the existing `employer_scope(ctx)` predicate joined via `claim`. No role branching — `server/tests/test_scoped_repository.py` AST-scans for both and must stay green.
- AD-11: the three tables are PHI-class. No claim text, prompt body, model output or vector is ever logged; log lines carry ids, counts and event names only.
- Conventions: snake_case DB names, `timestamptz` for events, migration revision `0040_…`/`0041_…` chained off `0039_supervisor_worklist_cap`, explicit `GRANT … TO lineworker_app` at the end of each `upgrade()`, enum members written literally in migrations.
- `web/` is untouched apart from the regenerated, committed `src/api/schema.d.ts`.

**Block If:**
- The three tables cannot be created without changing an existing table's columns (they can: `claim_embedding` references `claim.id` and adds nothing to `claim`).
- Wiring `mark_claim_stale` into a command would require restructuring its transaction rather than adding one call before `db.commit()`.

**Never:**
- No `ai_insight` table or Insights tab (6.2). No LangGraph, `langchain`, `langchain-ollama`, `create_agent`, checkpoints, SSE, chat client, prompts, tools registry, QAS nodes, interrupts or degradation UX (6.3–6.6). No `agents/` code.
- No knowledge-corpus ingestion pipeline — sourcing, chunking and cadence are Deferred by the spine. Seed fixed rows and stop.
- No real statutory text presented as authoritative law: corpus rows are clearly-labelled synthetic demo paraphrases.
- No `model-stub` in `compose.yaml` or `compose.gpu.yaml`; no `ollama` service in `compose.e2e.yaml`.
- Never store the composed claim summary text — only its hash. Never pad, truncate or re-scale a vector whose length disagrees with the column.
- No second refresh path: the scheduled job and the e2e admin trigger both call the one `refresh_stale_embeddings` command.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Composer determinism | Same `Claim` + employer + additional-injury rows, composed twice | Byte-identical text and `source_text_hash` | No error expected |
| Composer sees a change | `severity_score` 4 → 7, recomposed | Different text, different hash | No error expected |
| Mark stale in-transaction | `update_claim_fields` commits an injury-type edit | `claim_embedding.stale` is `true` and the audit + timeline rows exist, all from one commit | Command rolls back ⇒ stale flag rolls back too |
| Mark stale, no row yet | `mark_claim_stale` for a claim with no `claim_embedding` row | Idempotent upsert creates the row `stale=true, embedding=NULL` | No error expected |
| Refresh ordering | 3 rows: one `stale=true`, one `embedding IS NULL`, one fresh | Stale and NULL rows embedded (stale first), fresh row untouched, `embedded_at` set, `stale=false` | Embed failure leaves the row stale; run logs `rag.refresh_failed` with a count and continues |
| Refresh batch bound | 100 pending rows, `embedding_refresh_batch_size=25` | Exactly 25 embedded this run | No error expected |
| Scoped similar search | Handler scoped to Caterpillar/GE/Whirlpool/John Deere asks for similar to one of their claims, k=5 | Only claims in those four employers; target claim excluded; each item carries `distance`, `embeddedAt`, `stale` | No error expected |
| Unbounded scope | `scope_all` persona, same query | May include any employer's claims | No error expected |
| Out-of-scope target | Handler requests similar for a claim outside their book | 404 problem+json (the existing single-answer rule) | Same 404 for a non-existent claim id |
| Empty book | Context with zero employer assignments | Empty item list, HTTP 200 | No error expected |
| Knowledge search | `search_knowledge(ctx, "Ohio waiting period", k=3)` | Up to 3 chunks ordered by distance, each with `source`, `state_code`, `chunk_text` | No error expected |
| Dimension mismatch | `EMBEDDING_MODEL=nomic-embed-text` (768-d) against `vector(1024)` | `EmbeddingDimensionMismatch` naming the model, the returned length and the column length | Refresh run fails loudly; nothing written |
| Ollama unreachable | Embeddings endpoint down during a refresh tick | Job logs failure and survives; rows stay stale | No retry storm, no cloud fallback |

</intent-contract>

## Code Map

- `lineworker/deploy/compose.yaml` -- add `ollama` (no `ports:`, model volume, pull-then-healthy entrypoint); api gains `depends_on: ollama: service_healthy` and the three new env knobs.
- `lineworker/deploy/compose.gpu.yaml` -- **new**: prod-only NVIDIA device reservation overlay for `ollama`.
- `lineworker/deploy/compose.e2e.yaml` -- replace the two `model-stub` placeholder comments with the real service; api points at it.
- `lineworker/deploy/model-stub/` -- **new**: `Dockerfile` + `app.py`, deterministic Ollama-API-compatible stub (e2e profile only).
- `lineworker/deploy/.env.example` -- document `OLLAMA_BASE_URL`, `CHAT_MODEL`, `EMBEDDING_MODEL`, refresh knobs, first-boot model pull, and the dimension-migration caveat.
- `lineworker/server/config.py` -- new fields alongside the payment-batch block; same "knob, not code" discipline.
- `lineworker/server/pyproject.toml` -- add `pgvector` and promote `httpx` from dev to runtime; `uv lock`.
- `lineworker/server/data/versions/20260819_0040_embedding_tables.py` -- **new**: three tables, HNSW indexes, grants; head is `0039_supervisor_worklist_cap`.
- `lineworker/server/data/versions/20260819_0041_seed_knowledge_corpus.py` -- **new**: chunk rows from `data/seed/knowledge_chunks.json`; pre-creates `claim_embedding` / `knowledge_embedding` rows as `stale`/NULL.
- `lineworker/server/data/seed/knowledge_chunks.json` -- **new**: labelled-synthetic corpus.
- `lineworker/server/data/models/core.py` -- three ORM classes (append the `_enum`/`Identity`/naming-convention idiom already there).
- `lineworker/server/data/repositories/embeddings.py` + `__init__.py` -- **new**: scope-enforced vector reads and the owner-only writes; exported from the package.
- `lineworker/server/tests/test_scoped_repository.py:59,172,389` -- the AD-7 reflection/AST guards, today bound to `repositories.claims` alone; extended to cover the new module.
- `lineworker/README.md` -- first-boot model pull and the source-tree comment.
- `lineworker/server/services/rag/{client,claim_text,embeddings,retrieval,__init__}.py` -- **new**: the sole embeddings client, composer, commands, query path.
- `lineworker/server/services/claims/edit.py:427,560` · `injuries.py:195,275` -- one `mark_claim_stale` call before each `db.commit()`.
- `lineworker/server/services/claims/comp_rate.py` -- **deliberately not wired**; record the reason in the module docstring.
- `lineworker/server/services/jobs.py` -- add an `every_seconds` due-predicate beside `weekly_on`.
- `lineworker/server/api/app.py:44-84` -- register the second job (`embedding_refresh`) in `build_job_runner`.
- `lineworker/server/api/routers/claims.py` + `api/schemas.py` -- `GET /claims/{claimId}/similar`.
- `lineworker/server/api/routers/admin.py` -- `POST /admin/embedding-refresh` (e2e-only, mirrors the payment-batch trigger).
- `lineworker/e2e/stories/6-1-local-model-serving-embedding-foundation.spec.ts` -- **new**.
- `.github/workflows/ci.yaml` -- compose config check asserting no published Ollama port.

## Tasks & Acceptance

**Execution:**
- [x] `lineworker/server/pyproject.toml` -- add `pgvector>=0.3` and `httpx>=0.27` to `[project].dependencies` (httpx leaves the dev group), run `uv lock` -- the embeddings client and the `Vector` column type are runtime code, not test-only.
- [x] `lineworker/server/config.py` -- add `ollama_base_url` (default `http://ollama:11434`), `chat_model` (default `qwen3:14b`), `embedding_model` (default `bge-m3`), `embedding_refresh_interval_seconds` (`Field(default=900.0, gt=0)`), `embedding_refresh_batch_size` (`Field(default=25, gt=0)`), `embedding_request_timeout_seconds` (`Field(default=60.0, gt=0)`) -- AD-5 makes model names deployment config; the `gt=0` guards match the existing scheduler knobs, where a zero interval means a job that never usefully runs.
- [x] `lineworker/server/data/versions/20260819_0040_embedding_tables.py` -- create `claim_embedding` (`id`, `claim_id` FK→`claim.id` UNIQUE NOT NULL, `embedding vector(1024)` NULL, `source_text_hash text` NULL, `model text` NULL, `stale bool NOT NULL DEFAULT false`, `embedded_at timestamptz` NULL), `knowledge_chunk` (`id`, `source text`, `state_code text` NULL, `title text`, `chunk_text text`, `chunk_index int`, UNIQUE `(source, chunk_index)`), `knowledge_embedding` (`id`, `chunk_id` FK→`knowledge_chunk.id` UNIQUE NOT NULL, `embedding vector(1024)` NULL, `model text` NULL, `embedded_at timestamptz` NULL); add one HNSW index per embedding column via `op.execute("CREATE INDEX … USING hnsw (embedding vector_cosine_ops)")`; end with the usual `GRANT SELECT, INSERT, UPDATE, DELETE` + sequence grants -- `op.create_index` cannot express HNSW, matching the file's existing raw-SQL-for-unrepresentable-DDL habit.
- [x] `lineworker/server/data/seed/knowledge_chunks.json` -- 15 chunks: 3 each for OH, MN, IL, WA, TX (the five most common seed jurisdictions), every `source` prefixed `synthetic-demo:` -- the corpus must be real rows without asserting real law, since sourcing is a spine-level Deferred decision.
- [x] `lineworker/server/data/versions/20260819_0041_seed_knowledge_corpus.py` -- load the JSON with the `SEED_PATH` idiom, assert the expected row count, insert chunks, then `INSERT … SELECT` one `knowledge_embedding` row per chunk and one `claim_embedding` row per `claim` with `stale=true, embedding=NULL` -- a migration cannot reach Ollama, so it creates the work and `refresh_stale_embeddings` is the only thing that ever fills a vector.
- [x] `lineworker/server/data/models/core.py` -- add `ClaimEmbedding`, `KnowledgeChunk`, `KnowledgeEmbedding` using `pgvector.sqlalchemy.Vector(1024)`; docstring each with why it carries no `version` column (append-only derived data with a single writer, like `timeline_event`) -- `Base.metadata` is Alembic's `target_metadata`, so a missing model shows up as a spurious autogenerate diff.
- [x] `lineworker/server/data/repositories/embeddings.py` -- `select_similar_claims(db, ctx, *, embedding, k, exclude_claim_pk)` (join `claim`, `where(employer_scope(ctx))`, order by `<=>`, return claim ref + distance + `embedded_at` + `stale`), `select_pending_claim_embeddings(db, ctx, *, limit)`, `upsert_claim_embedding_stale(db, ctx, *, claim_pk)`, `write_claim_embedding(db, ctx, *, claim_pk, …)`, `select_similar_chunks(db, ctx, *, embedding, k)`, `select_pending_knowledge_embeddings`, `write_knowledge_embedding`; export from `data/repositories/__init__.py` -- every claim-touching function reaches `claim` through `employer_scope(ctx)`, including the two writes (you cannot mark stale or embed a claim you cannot see, and the system actor's `ALL` makes that a tautology rather than a skipped filter); the module docstring records why the two knowledge functions take a context they do not filter on, the way `statutory_forms.py` documents its own carve-out.
- [x] `lineworker/server/tests/test_scoped_repository.py` -- extend `test_every_repository_read_requires_a_caller_context` and `test_no_repository_branches_on_role` to run over `data.repositories.embeddings` as well as `claims`, and assert the package exports it -- the module's own docstring says these guards exist to stop "Story 6.x quietly adding an unscoped query"; a guard bound to one module would let this story do exactly that.
- [x] `lineworker/server/services/rag/client.py` -- `EmbeddingClient` protocol (`embed(texts: Sequence[str]) -> list[list[float]]`), `OllamaEmbeddingClient` posting to `{base_url}/api/embed` with `{"model", "input"}` via `httpx.AsyncClient`, module constant `EMBEDDING_DIMENSIONS = 1024`, and `EmbeddingDimensionMismatch` raised when a returned vector's length differs -- the sole embeddings-API caller in the build; the protocol is what lets every test run without a model server.
- [x] `lineworker/server/services/rag/claim_text.py` -- one deterministic `compose_claim_text(claim, employer, additional_injuries) -> str` plus `text_hash(text) -> str` (sha256 hex) over injury type, cause, body part, ICD-10 + description, severity band, disability, recovery window, surgery flag, employer sector, stage/status and RTW outcome, with additional injuries sorted by id -- deterministic ordering is what makes `source_text_hash` detect real change instead of dict iteration order.
- [x] `lineworker/server/services/rag/embeddings.py` -- `mark_claim_stale(db, *, claim_pk)` (idempotent upsert, no commit), `embed_claims(db, ctx, *, claim_pks, client)`, `refresh_stale_embeddings(db, ctx, *, client, limit)` selecting `stale OR embedding IS NULL` ordered `stale DESC, embedded_at ASC NULLS FIRST`, and `seed_knowledge_corpus(db, ctx, *, client)` embedding chunk rows that have none -- one refresh command reached by both the scheduler and the e2e trigger (AD-12: not two paths).
- [x] `lineworker/server/services/rag/retrieval.py` -- `similar_claims(db, ctx, *, claim_business_id=None, query_text=None, k, client)` composing-and-embedding the query here then calling the repository with a vector, and `search_knowledge(db, ctx, *, query_text, k, client)`; module docstring states why knowledge search takes a context it does not filter on -- AC 2's literal rule is that repositories never receive text.
- [x] `lineworker/server/services/claims/edit.py` -- add `await rag.mark_claim_stale(db, claim_pk=claim.id)` after `timeline.append` and before `db.commit()` in `update_claim_fields` (:427) and `update_claim_severity` (:560) -- both write composer inputs.
- [x] `lineworker/server/services/claims/injuries.py` -- same call before `db.commit()` in `add_additional_injury` (:195) and `remove_additional_injury` (:275) -- secondary injuries are composer inputs even though neither command touches a `claim` column.
- [x] `lineworker/server/services/claims/comp_rate.py` -- add a docstring paragraph recording that `update_comp_rate_override` is deliberately **not** wired, because a comp-rate override is a financial decision about one claim and is not a composer input -- `deferred-work.md` handed this call to 6.1; an undocumented omission is indistinguishable from a miss.
- [x] `lineworker/server/services/jobs.py` -- add `every_seconds(interval)` beside `weekly_on` -- the runner's cadence lives in predicates, not in the tick.
- [x] `lineworker/server/api/app.py` -- register `EMBEDDING_REFRESH_JOB` in `build_job_runner`, opening its own session and resolving `system_context` per run exactly as the payment batch does -- the module comment already reserved this slot.
- [x] `lineworker/server/api/routers/claims.py` + `api/schemas.py` -- `GET /claims/{claimId}/similar?k=` returning `{items: [{claimId, employerShortName, injuryType, severityScore, distance, embeddedAt, stale}]}`, 404 for an out-of-scope or unknown claim -- AD-15 needs one browser-reachable surface to prove AC 3; no `web/` component consumes it yet.
- [x] `lineworker/server/api/routers/admin.py` -- `POST /admin/embedding-refresh` calling the same command with `limit=None`, returning `{rowsEmbedded, chunksEmbedded}` -- the scheduler is off under `ENV=e2e` by design, so the suite triggers refresh explicitly, exactly as it does the payment batch.
- [x] `lineworker/web/src/api/schema.d.ts` -- regenerate with `npm run generate:api` and commit -- CI fails on a diff.
- [x] `lineworker/deploy/model-stub/{Dockerfile,app.py}` -- FastAPI stub serving `GET /api/version`, `POST /api/embed` (1024-d unit vector derived from sha256 of each input, so equal text always yields an equal vector and similarity assertions are stable) and a fixed `POST /api/chat` response for 6.3 to extend -- deterministic per input is what makes the e2e ordering assertable.
- [x] `lineworker/deploy/compose.yaml` -- add `ollama` with a named model volume, **no `ports:`**, an entrypoint that serves then pulls `CHAT_MODEL` and `EMBEDDING_MODEL` and marks a readiness file, a `CMD-SHELL` healthcheck on that file with a long `start_period`, and `depends_on: ollama: service_healthy` on api plus `OLLAMA_BASE_URL`/`CHAT_MODEL`/`EMBEDDING_MODEL` env -- the story requires `compose up` to reach healthy unattended, which means the pull is part of readiness.
- [x] `lineworker/deploy/compose.gpu.yaml` -- prod overlay reserving NVIDIA devices for `ollama` only -- base compose stays CPU-capable so dev needs no GPU.
- [x] `lineworker/deploy/compose.e2e.yaml` -- replace both placeholder comments with `model-stub` (built from `./model-stub`, no ports, healthcheck on `/api/version`); api gets `OLLAMA_BASE_URL: http://model-stub:11434` and `depends_on: model-stub: service_healthy`; no `ollama` service -- AD-5/AD-14 govern dev and prod, the stub exists only here.
- [x] `lineworker/deploy/.env.example` -- document the new knobs, the first-boot pull cost, and that changing `EMBEDDING_MODEL` to a different dimensionality is a migration rather than a config flip -- the file is the operator-facing record of every knob.
- [x] `lineworker/server/tests/test_rag_embeddings.py` -- **new**: composer determinism (Hypothesis over claim field permutations) and change-detection, refresh ordering and batch bound, `embedded_at` present in retrieval results, dimension-mismatch raise, mark-stale upsert idempotency -- covers the I/O matrix's composer, refresh and mismatch rows with a fake `EmbeddingClient`.
- [x] `lineworker/server/tests/test_rag_scope.py` -- **new**: an enumerated-employer handler's `select_similar_claims` and `similar_claims` never return an out-of-scope claim; a `scope_all` persona can; an empty book returns nothing -- AC 3 names this test explicitly.
- [x] `lineworker/server/tests/test_embedding_staleness.py` -- **new**: for each of the four wired commands, commit ⇒ stale flag set in the same transaction and rollback ⇒ neither written; then refresh ⇒ `stale=false` with a fresh `embedded_at`; plus an AST assertion that those four modules call `mark_claim_stale` and `comp_rate.py` does not -- the structural half is what stops a sixth command landing unwired.
- [x] `lineworker/server/tests/test_embedding_tables_migration.py` -- **new**: after `alembic upgrade head` the three tables, the unique constraints, the two HNSW indexes and the pre-created stale rows exist, and `vector(1024)` is the declared column type -- mirrors the existing `test_*_migration.py` modules.
- [x] `lineworker/e2e/stories/6-1-local-model-serving-embedding-foundation.spec.ts` -- **new**, `@story:6-1 @epic:6` with one `@smoke` test: log in as a scoped handler, `POST /api/admin/embedding-refresh`, `GET /api/claims/{id}/similar`, assert every returned `claimId` is in that persona's book (cross-checked against `fixtures/seed.ts`), that each item carries a non-null `embeddedAt`, and that the target claim is absent -- structural assertions only, no prose.
- [x] `lineworker/README.md` -- note under the one-command dev section that first boot pulls the two models before `api` starts, and add `services/rag` + the compose overlay to the source-tree comments -- "That's it" is currently true and must stay honest about a multi-gigabyte first run.
- [x] `.github/workflows/ci.yaml` -- add a step rendering `docker compose -f deploy/compose.yaml config` and `… -f deploy/compose.gpu.yaml config` and failing if the `ollama` service declares any published port -- AD-5's port rule needs a check that survives an edit nobody reviews closely.

**Acceptance Criteria:**
- Given the dev compose stack, when it starts, then `ollama` is reachable from `api` on the internal network with both models pulled, and no profile publishes its port.
- Given `services/rag`, when embeddings are produced or queried, then it is the only module issuing an Ollama embeddings request, and every repository call receives a vector rather than text.
- Given the four wired commands, when one commits, then the claim's embedding row is `stale` from the same transaction; when one rolls back, the flag rolls back with it.
- Given pending rows, when the scheduled refresh runs, then stale rows are embedded before never-embedded ones, at most `embedding_refresh_batch_size` per run, and each result carries `embedded_at`.
- Given a scoped persona, when similar-case retrieval runs, then results never cross the caller's employer partition; given `scope_all`, they may.
- Given the e2e profile, when the suite runs, then it drives `model-stub` — never a real model server — and `6-1-…spec.ts` passes against the freshly reset stack.

## Spec Change Log

## Review Triage Log

### 2026-08-19 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 24: (high 3, medium 10, low 11)
- defer: 7: (medium 3, low 4)
- reject: 1: (low 1)
- addressed_findings:
  - `[high]` `[patch]` The Ollama entrypoint ran under `/bin/sh -c` with no `set -e` and no `&&` chaining, so a failed `ollama pull` still reached `touch /tmp/ollama-ready`; the healthcheck passed, `api` started against a model server holding nothing, and every embed 404'd — verbatim the failure the comment above it said the readiness file prevents. Added `set -e`, and bounded the previously unbounded `until ollama list` wait so a `serve` that dies before listening fails the container instead of idling inside a 30-minute `start_period`.
  - `[high]` `[patch]` `seed_knowledge_corpus` sat outside the `try`, so on the state migration 0041 leaves every fresh deployment in — claims *and* chunks pending — a model-server outage raised out of `refresh_stale_embeddings`, turning the admin route's documented "200 with zeroes and a non-zero failure count" into a 500, and discarding any claim vectors the run had already written because `commit()` was never reached. Both halves now run in their own savepoint, are counted symmetrically (`rows_failed` / `chunks_failed`, the latter new on `RefreshRun` and the admin response), and a failure in one no longer unwinds the other or poisons the session.
  - `[high]` `[patch]` `write_claim_embedding` cleared `stale` unconditionally, so a handler edit committing inside the embed window (budgeted up to 60s) was silently overwritten: the pre-edit vector stored with a fresh `embedded_at`, the row permanently wrong until some unrelated edit touched it. That defeats AC 4, the story's headline invariant. Added a monotonic `stale_at` marker to `claim_embedding` (migration 0040, unreleased, amended in place): the pending select returns it, the write clears `stale` only when it still matches, so a mark that lands mid-flight survives and the next tick re-embeds. Covered by a regression test that commits the edit from a second session — confirmed failing against the pre-fix write and passing after, with a companion test so a guard that simply never cleared the flag could not pass.
  - `[medium]` `[patch]` `claim_embedding.model` / `knowledge_embedding.model` were written and never read, while both docstrings claimed the column decided what needed re-embedding; re-pointing `EMBEDDING_MODEL` at another 1024-dim model would have left two incomparable vector spaces mixed in one index. Both pending predicates now treat a model change as pending.
  - `[medium]` `[patch]` `select_pending_claim_embeddings` selected *from* `claim_embedding`, so a claim with no row could never be embedded, though two docstrings asserted the insert branch covered exactly that. Now an outer join from `claim`, with the row-less case ordered into the never-embedded group.
  - `[medium]` `[patch]` `GET /claims/{id}/similar` caught neither `httpx` errors nor `EmbeddingDimensionMismatch`, so a model-server outage turned an interactive claim read into an unlabelled 500 after up to 60s while the route documented only 401/404. Now a labelled 503 problem+json with a declared response. Story 6.6 still owns degradation UX; this is the status line it will build on.
  - `[medium]` `[patch]` The `EmbeddingDimensionMismatch` message never reached an operator on the scheduled path: `JobRunner.tick` logs `type(exc).__name__` only, correctly for AD-11, so the exception whose whole design is that it names the model and both widths surfaced as a bare class name. The three numbers (none of them PHI) are now logged where the run catches it, before it re-raises.
  - `[medium]` `[patch]` The broad `except Exception` also caught database errors raised inside the per-row write loop, committing rows already written while reporting `rows_embedded=0` and leaving a poisoned session whose next statement raised `PendingRollbackError` — losing the original cause. The savepoint fix above resolves it; counts are now true.
  - `[medium]` `[patch]` `deploy/model-stub/Dockerfile` installed `fastapi>=0.115,<1.0` and `uvicorn>=0.30` under a header claiming "two pinned packages", so a release could change the stub's answers between two CI runs of one commit — the reproducibility property AD-15 rests on. Pinned to the resolved set including starlette and pydantic, and the header made true.
  - `[medium]` `[patch]` `test_the_seed_loader_refuses_an_unlabelled_row` pointed `SEED_PATH` at `glossary_terms.json` and asserted the *shape* check, so the `synthetic-demo:` branch its name described had no coverage. Rewritten against a correctly-shaped corpus with one label stripped, plus a new test for the jurisdiction-count guard.
  - `[medium]` `[patch]` The e2e spec's tests 3 and 4 silently depended on test 1 having embedded the portfolio — the reset fixture rebuilds per spec *file*, not per test — so any `--grep` narrowing or reorder produced failures unrelated to the code. Added a shared `embedEverything` helper each test requests; verified by running both dependent tests in isolation.
  - `[medium]` `[patch]` `search_knowledge`, `embed_claims`, `select_claim_embedding_sources` and `similar_claims`' `query_text` branch had no caller and no test, though `embed_claims`' docstring justified itself as "what a test asserts against". Four service-level tests added, including that the knowledge corpus is deliberately not narrowed by the caller's book.
  - `[low]` `[patch]` The route declared `Query(le=MAX_K)` (422 above 25) while `retrieval.py` said in as many words that a caller asking for 500 gets an answer rather than a 422, making `min(k, MAX_K)` unreachable in three places, and the echoed `k` was the requested value though its docstring said it showed what you got. Prose and code reconciled on the 422 story; the dead clamp removed from the route.
  - `[low]` `[patch]` `limit` was forwarded unchanged to both halves, so a tick configured for 25 could embed 25 claims *and* 25 chunks against a knob documented as "how many rows one run embeds". The budget is now the run's, claims first.
  - `[low]` `[patch]` The stub hardcoded 1024 under a comment justifying the duplicated literal by saying it must be able to *disagree* with the server. Width is now an env knob defaulting to 1024, so a later story can drive a real dimension mismatch end to end.
  - `[low]` `[patch]` `.github/scripts/check_model_ports.py` indexed `EXPECTED[path.name]`, so an unexpected basename produced a bare `KeyError` instead of a readable CI failure, and its message claimed "nginx is the one published port" while checking one service per profile. Both fixed; verified the unknown-profile path now prints a named error.
  - `[low]` `[patch]` `_StubAsyncClient.last_json` was a class attribute written via `type(self)`, carrying across tests. Made per-instance.
  - `[low]` `[patch]` `test_no_command_writes_the_embedding_tables_itself` forbade `claim_embedding` / `ClaimEmbedding` / `KnowledgeEmbedding` anywhere in five modules *including prose*, while its neighbour required `comp_rate.py`'s docstring to explain the table it deliberately does not mark — a rule that made the correct comment unwritable. Comments and docstrings are now stripped by tokenizer before the scan; string literals are deliberately kept, so a raw-SQL second writer is still caught. Verified non-vacuous.
  - `[low]` `[patch]` `every_seconds` stalled until the clock caught up if the wall clock stepped backwards (NTP correction) after a run. Negative elapsed now reads as due; the job is idempotent, so an early re-run costs one query.
  - `[low]` `[patch]` `compose.gpu.yaml` told operators a misconfigured host "produces a stack that works and is slow rather than one that refuses to start". Backwards: a device reservation fails at container *creation* on a host without the NVIDIA toolkit, so Ollama never runs to fall back. Corrected, with the positive check named.
  - `[low]` `[patch]` Migration 0041's seed guard computed `states` without dropping falsy values, so a row that lost its `state_code` contributed `None` and could keep the jurisdiction count at five. Now filtered, and covered by the new test above.
  - `[low]` `[patch]` `config.py`/`compose.yaml` default `CHAT_MODEL` to `qwen3:14b`, `.env.example` said it wants ~24 GB of GPU, and `README.md` called the base stack "CPU-capable on purpose, so dev needs no GPU" — while nothing in this build calls the chat model at all. The three documents now agree on what a first `up` costs and what to turn down on a laptop.
  - `[low]` `[patch]` `source_text_hash` was written and read by nothing, though `core.py` and migration 0040 both stated its purpose. Resolved as prose plus a load-bearing assertion: the migration now says it is evidence rather than a queue and names the test that reads it, and that test genuinely does.

**On the absence of `bad_spec`, since the rule says to prefer it when in doubt.** The staleness race (H3) is the one finding with a plausible spec-level root cause: the spec specified the `stale` flag and the upsert but never the concurrency guard. It was triaged `patch` because the workflow's stated rationale for preferring `bad_spec` — that a spec-level fix produces more coherent code — does not apply here. The design shape was right; what was missing was one additive column and one conditional. A loopback would have reverted ~5,900 lines of otherwise-correct, fully-tested work to re-derive it around a guard that took a column, a predicate and two tests to add. The judgment is recorded here rather than left implicit.

## Design Notes

**Why the migration pre-creates rows.** A migration cannot reach Ollama, and `mark_claim_stale` must work for a claim that has never been embedded. Making the migration insert one `stale=true, embedding=NULL` row per claim gives both problems one answer: refresh is the single code path that ever writes a vector, "stale first" has real meaning on a fresh database, and `mark_claim_stale` stays an upsert rather than a special case.

**Hash, not text.** `claim_embedding` stores `source_text_hash` and no summary column. The composed text is re-derivable from the claim at zero cost, so persisting it would add a second copy of PHI for Epic 8's purge cascade to chase in exchange for nothing.

**The dimension is pinned, and says so loudly.** `bge-m3` emits 1024 floats; the column and `EMBEDDING_DIMENSIONS` agree. The architecture's CPU-dev pairing names `nomic-embed-text`, which emits 768 — so the client raises `EmbeddingDimensionMismatch` naming the model, the length it got and the length the column wants. Silently padding would produce a table of vectors that retrieve nonsense with nothing on screen to explain it; the CPU fallback for *chat* costs nothing and still works.

**Four commands, not five.** `deferred-work.md` recorded the mark-stale obligation against five commands and left the fifth to this story. `update_comp_rate_override` writes `comp_rate_override_bp` — a financial decision about one claim, not part of what makes two injuries similar — so it is not wired, and both the decision and its reason are asserted by `test_embedding_staleness.py` rather than left as an absence.

**`add_/remove_additional_injury` widen "claim changed".** Neither writes a `claim` column or bumps `claim.version`; both change what the composer produces. `mark_claim_stale` takes `claim_pk` precisely so a command that mutates a child row can still mark the parent's embedding stale. Note `add_additional_injury` returns early on CAS loss (injuries.py:174-177) before any emit call — the new call goes with the others, after that branch.

**Why a public `/similar` endpoint in a story with no UI.** AD-15 gates this story on a spec the browser can reach, and AC 3 requires the scope test to run through the service, not only the repository. A thin scoped read endpoint is the smallest honest surface; 6.4's similar-case quick action will call the same service function through a registered tool, not this route.

## Verification

**Commands:**
- `cd lineworker/server && uv run ruff check . && uv run ruff format --check .` -- expected: clean.
- `cd lineworker/server && uv run mypy .` -- expected: clean under `strict`.
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker uv run pytest` -- expected: all pass, including the four new modules; the DB-backed suite must not skip.
- `cd lineworker/web && npm run generate:api && git diff --exit-code -- src/api/schema.d.ts` -- expected: no diff after committing the regenerated client.
- `cd lineworker && docker compose -f deploy/compose.yaml config | grep -A40 '^  ollama:' | grep -q 'ports:' && echo FAIL || echo OK` -- expected: `OK`; repeat with `-f deploy/compose.gpu.yaml`.
- `cd lineworker && docker compose -f deploy/compose.e2e.yaml up -d --build --wait` -- expected: all services healthy, `model-stub` included, no model download.
- `cd lineworker/e2e && npx playwright test --grep "@story:6-1"` -- expected: pass against the freshly reset stack.

**Manual checks (if no CLI):**
- `deploy/compose.yaml`'s `ollama` block has no `ports:` key and a named volume for models; `compose.e2e.yaml` has no `ollama` service and both placeholder comments are gone.
- Grep confirms exactly one file (`services/rag/client.py`) references the Ollama base URL or an embeddings path.

## Auto Run Result

Status: done

### What was implemented

Epic 6's AI substrate, and nothing above it. An `ollama` service on the internal
compose network (model names as config, port never published, GPU overlay for
prod, a deterministic `model-stub` standing in for it under the e2e profile);
three pgvector tables with HNSW indexes created by one Alembic revision and
seeded by a second; `services/rag` as the sole owner of those tables and the
only module in the build that speaks to Ollama's embeddings API; vector search
as an ordinary scope-enforcing repository method; and AD-12's staleness contract
plumbed end to end — four of the five pre-existing AD-4 commands now call
`mark_claim_stale` inside their own transaction, a second scheduled job
re-embeds stale rows first, and retrieval carries `embedded_at`.

Three judgment calls the story left open, all argued in code rather than
recorded only here:

- **Four commands wired, not five.** `update_comp_rate_override` writes a
  financial override, which is not an input to the embedded summary — the
  composer carries no money at all. The decision is in that module's docstring
  and asserted in both directions by `test_embedding_staleness.py`, so the
  absence cannot later read as an oversight. This discharges the mark-stale
  obligation `deferred-work.md` had recorded three times since Story 2.3.
- **The embedding dimension is pinned at 1024 and fails loudly.** The
  architecture's CPU-dev pairing names `nomic-embed-text` (768), so the client
  raises `EmbeddingDimensionMismatch` naming the model and both widths rather
  than padding. That leaves AC 1's "CPU-small allowed in dev" true for chat and
  not for embeddings — deferred to the architect, recorded in `deferred-work.md`.
- **A thin `GET /claims/{id}/similar`.** The story has no UI, but AD-15 gates it
  on a browser-reachable spec and AC 3 asks for the scope guarantee through the
  service. Story 6.4's quick action will call the same service function through
  a registered tool, not this route.

### Files changed

Created — `deploy/compose.gpu.yaml` (prod GPU overlay); `deploy/model-stub/`
(`Dockerfile`, `app.py` — deterministic Ollama-API stand-in, e2e only);
`server/data/versions/20260819_0040_embedding_tables.py` and
`…_0041_seed_knowledge_corpus.py`; `server/data/seed/knowledge_chunks.json`
(15 labelled-synthetic chunks across the five commonest seed jurisdictions);
`server/data/repositories/embeddings.py`; `server/services/rag/{client,claim_text,embeddings,retrieval}.py`;
five test modules (`embedding_fixture`, `test_rag_embeddings`, `test_rag_scope`,
`test_embedding_staleness`, `test_embedding_tables_migration`);
`e2e/stories/6-1-local-model-serving-embedding-foundation.spec.ts`;
`.github/scripts/check_model_ports.py`.

Modified — `deploy/{compose.yaml,compose.e2e.yaml,.env.example}` (ollama service,
model-stub, new knobs); `server/config.py` (six fields); `server/pyproject.toml`
+ `uv.lock` (pgvector added, httpx promoted to runtime); `server/data/models/core.py`
(three ORM classes); `server/api/{app.py,routers/admin.py,routers/claims.py}`
(second scheduled job, e2e refresh trigger, the `/similar` read);
`server/services/claims/{edit.py,injuries.py}` (mark-stale, four call sites) and
`comp_rate.py` (the documented non-wiring); `server/services/jobs.py`
(`every_seconds`); `server/tests/test_scoped_repository.py` (AD-7 guards extended
over the new repository); `web/src/api/schema.d.ts` (regenerated);
`README.md`; `.github/workflows/ci.yaml` (compose port check).

### Review findings

Two adversarial reviewers over the full diff; 32 findings after dedup.
0 intent_gap, 0 bad_spec, **24 patched** (3 high, 10 medium, 11 low),
7 deferred, 1 rejected. Itemised in the Review Triage Log above; deferred items
are in `deferred-work.md` under this story's heading, which also records the
three now-discharged mark-stale entries so they are not read as outstanding.

The three high-severity findings were all confirmed against the code before
triage: a readiness file written after a failed model pull, a documented
degradation contract that returned 500 on exactly the deployment state it
described, and a refresh-versus-edit race that stored a pre-edit vector under a
fresh timestamp. The last is the one worth flagging: it defeated AC 4 silently,
and the regression test for it was checked against the pre-fix code to confirm
it actually catches the defect.

### Verification

- `uv run ruff check .` — All checks passed; `ruff format --check .` — 217 files
  already formatted.
- `uv run mypy .` (strict) — Success: no issues found in 217 source files.
- `MIGRATION_TEST_DATABASE_URL=… uv run pytest` — **2402 passed, 1 skipped**
  (3m40s). The DB-backed suite ran; it did not skip.
- `npm run generate:api` + `npm run typecheck` — clean; `schema.d.ts` regenerated
  and committed (the admin routes are absent by design, being e2e-only).
- `docker compose … config` port checks — dev, GPU overlay and e2e all report no
  published model port; the unknown-profile path prints a named CI error.
- `docker compose -f deploy/compose.e2e.yaml up -d --build --wait` — all four
  containers healthy including `model-stub`; its pinned versions verified inside
  the running container.
- `npx playwright test --grep "@story:6-1"` — 5 passed. Each dependent test also
  run in isolation, confirming the order-coupling fix.
- `npx playwright test` (full suite) — **183 passed** (2m).

### Residual risks

- **Scoped ANN recall.** pgvector post-filters HNSW candidates, so a narrowly
  scoped caller can get fewer than `k` neighbours than their book holds. Invisible
  at 100 seeded rows (the planner picks a sequential scan), which means the
  repository and e2e assertions about the bound pass while asserting something
  that stops being true at scale. Deferred with evidence; it is a tuning decision
  that wants a table big enough to measure.
- **The chat model is served but has no caller**, so a first `up` pulls several
  gigabytes for something 6.2/6.3 will be the first to use. Required by AC 1;
  the documents now say so plainly and name the knob to turn down.
- **`ollama/ollama:latest` is unpinned.** The architecture wants a digest pinned
  at deploy, and no deploy procedure exists yet to pin it in. Deferred to whoever
  owns the first non-dev deployment.
- **One pre-existing flake** was observed once during implementation
  (`test_sla_endpoint.py`, an asyncpg `ConnectionError` against the local docker
  Postgres) and did not recur across three subsequent full runs. Unrelated to
  this story.
