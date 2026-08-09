# Story 6.1: Local Model Serving & Embedding Foundation

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims organization,
I want all AI inference and embeddings served locally with scope-enforced retrieval,
so that PHI never leaves the network.

## Acceptance Criteria

1. **Given** the compose stack, **when** it starts, **then** an Ollama container serves chat (`qwen3` family) and embeddings (`bge-m3`) on the internal network only — port never published, model names deployment config (AD-5) — with GPU config in prod and CPU-small allowed in dev.
2. **Given** `services/rag`, **when** embeddings are produced or queried, **then** it is Ollama's sole embeddings client, embedding claim text into `claim_embedding` and seeded labor-law chunks into `knowledge_chunk`/`knowledge_embedding` (tables created here), and query-time text is embedded in `services/rag` before the repository is invoked — repositories receive vectors, never text.
3. **Given** vector similarity search over claim embeddings, **when** invoked, **then** the repository applies the caller's employer-scope filter exactly like any relational query (AD-7), under test with a scoped persona.
4. **Given** an AD-4 command mutating an embedded source field (retro-wired into Epics 2–3 commands), **when** it commits, **then** it calls `services/rag`'s mark-stale command in the same transaction, the scheduled refresh re-embeds stale rows first, and retrieval returns `embedded_at` (AD-12).

## Tasks / Subtasks

- [ ] Task 1: Ollama service in compose (AC: 1)
  - [ ] Add `ollama` service to `deploy/compose.yaml`: official image digest-pinned at deploy, internal compose network only — **no `ports:` mapping ever** (same posture as postgres in Story 1.1); persistent model volume
  - [ ] `deploy/compose.gpu.yaml` overlay for prod: NVIDIA Container Toolkit device reservation; base compose stays CPU-capable so dev needs no GPU
  - [ ] Model names are config, not code: extend `server/config.py` (the single pydantic-settings module) with `ollama_base_url`, `chat_model` (default a `qwen3` tag), `embedding_model` (default `bge-m3`); dev may set CPU-small values (`qwen3:8b` + `nomic-embed-text` is the architecture's verified constrained-hardware pairing)
  - [ ] Healthcheck on the ollama container (e.g. `GET /api/version` via the internal network) and `depends_on: service_healthy` so the api container starts only against a live model server; document model pull (entrypoint pull or documented `ollama pull` step) so `compose up` reaches healthy unattended
  - [ ] Update `deploy/*.env.example` with the new knobs
- [ ] Task 2: Embedding tables migration (AC: 2)
  - [ ] New Alembic revision in `server/data/`: `claim_embedding` (surrogate `id`, `claim_id` FK, `embedding vector(1024)` for bge-m3, `source_text_hash`/summary provenance, `stale bool not null default false`, `embedded_at timestamptz`), `knowledge_chunk` (`id`, `source`, `state_code`, `title`, `chunk_text`, ordering metadata), `knowledge_embedding` (`id`, `chunk_id` FK, `embedding vector(1024)`, `embedded_at`)
  - [ ] `CREATE EXTENSION IF NOT EXISTS vector` if Story 1.2's migration did not already; add an appropriate ANN index (HNSW) per table — pgvector ≥ 0.8.2 floor from the spine stack table
  - [ ] Naming stays snake_case per the Excel-canonical convention; money/date conventions untouched (no money here); all three tables are PHI-class stores under AD-11 (encrypted volume — nothing extra to do in this story beyond not creating a second store)
- [ ] Task 3: `services/rag` — sole embeddings client (AC: 2)
  - [ ] Embedding client wrapper in `services/rag` calling Ollama's embeddings endpoint with `settings.embedding_model` — this module is the **only** place in the codebase that touches the embeddings API (AD-5 / dependency diagram: `SVC -->|embeddings only| OLLAMA`); no chat calls from services, ever
  - [ ] Claim-text composer: one function assembling the embeddable claim summary (injury, body part, ICD-10, severity band, sector, outcome fields) — deterministic so `source_text_hash` detects real changes
  - [ ] Commands (AD-12 — `services/rag` owns all writes to these tables): `embed_claim(claim_id)`, `mark_claim_stale(claim_id)`, `refresh_stale_embeddings()` (stale rows first), `seed_knowledge_corpus()`
  - [ ] Query path: `similar_claims(caller_ctx, claim_id | query_text, k)` and `search_knowledge(caller_ctx, query_text, k)` — **text is embedded here**, then the repository is invoked with the vector; repositories never receive text (AC 2 literal rule)
- [ ] Task 4: Scope-enforced vector repositories (AC: 3)
  - [ ] Repository methods in `server/data/` take the AD-7 caller context `(user_id, role, employer_ids | ALL)` and apply the **unconditional** employer filter joined through `claim` — `ALL` makes the predicate a tautology, it never skips the filter; no role-conditional branching
  - [ ] `similar_claims` repository returns claim ref + distance + `embedded_at` + `stale` (AC 4 retrieval contract); `knowledge` search is not claim-scoped (labor-law corpus is reference data) but still requires a caller context per the AD-7 "no code path may query claim data without one" discipline — document the distinction in the module docstring
- [ ] Task 5: Retro-wire mark-stale into Epics 2–3 mutation commands (AC: 4)
  - [ ] Inventory every existing AD-4 command that mutates a field feeding the claim-text composer (Story 2.3 inline edits: injury description/body part/severity; Story 2.4 added injuries/severity score; Story 3.x financial-outcome fields if embedded) and add a `services/rag.mark_claim_stale()` call **inside the same transaction** — this is the AD-12 request-through-the-owner path; the mutating service never writes `claim_embedding` itself
  - [ ] Amend the affected services' unit tests: mutation commit ⇒ embedding row flagged stale in the same transaction (rollback rolls both back)
- [ ] Task 6: Seeded labor-law corpus + scheduled refresh (AC: 2, 4)
  - [ ] Minimal seed corpus: a small set of synthetic/genuinely-public state WC statute chunks (a handful of states matching the seed claims' jurisdictions) loaded by a seed migration or seed command into `knowledge_chunk`; `seed_knowledge_corpus()` embeds them — **ingestion sourcing/chunking/cadence is a Deferred decision; do not build a pipeline**, just enough rows that Story 6.4's labor-law action retrieves something real
  - [ ] Initial claim embedding pass for the 100 seed claims (idempotent command, callable at startup/seed time)
  - [ ] Scheduled refresh: in-process job (mechanism deferred by the spine — keep it the same in-api-process style as the Epic 3 payment batch) running `refresh_stale_embeddings()` on an interval knob in config
- [ ] Task 7: `model-stub` joins the e2e profile — AD-15 (AC: 1–3)
  - [ ] Replace Story 1.1's placeholder comment in `deploy/compose.e2e.yaml` with the `model-stub` service: a deterministic Ollama-API-compatible container (tiny FastAPI or equivalent under `deploy/` or `e2e/`) serving scripted `/api/embeddings` (or `/api/embed`) vectors and scripted chat responses; no GPU; exists **only** in the e2e profile, never dev/prod (AD-5/AD-14 govern those)
  - [ ] The e2e api service points `ollama_base_url` at the stub via env
  - [ ] Stub embedding responses are deterministic per input text so similarity assertions are stable
- [ ] Task 8: Tests (AC: all)
  - [ ] Unit: claim-text composer determinism; mark-stale command; refresh ordering (stale rows first); `embedded_at` present in retrieval results
  - [ ] Scope test (AC 3 is explicit): with a scoped persona (an enumerated-employer handler from the seed's nine personas), `similar_claims` never returns a claim outside their employer partition; with a `scope_all` persona it can — both asserted at repository level and through the service
  - [ ] Integration: retro-wired command mutate → same-transaction stale flag → refresh → fresh `embedded_at`
  - [ ] E2E: `e2e/stories/6-1-local-model-serving-embedding-foundation.spec.ts` tagged `@story:6-1 @epic:6`, one `@smoke` happy path — since this story has no UI surface, the spec drives the composed e2e stack (with model-stub) through an API-level structural assertion the browser can reach (e.g. authenticated similar-case fetch for a scoped persona returns only in-scope claims with `embedded_at`); assert structure, never prose
  - [ ] Config check in CI or compose lint: `docker compose config` on dev + prod overlays shows no published port for ollama

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This is the **AI substrate** story: Ollama in compose, the three embedding tables, `services/rag` as sole embeddings owner/client, scoped vector search, staleness plumbing, and the e2e model stub. It is **not**: the `ai_insight` cache or Insights tab (6.2), LangGraph / checkpoints / chat / SSE (6.3), QAS nodes (6.4), interrupts (6.5), or degradation UX (6.6). No LangGraph or langchain-ollama chat usage lands here — the only model traffic this story creates is the embeddings endpoint. Nothing in `web/` changes. The chat model is *pulled and served* (AC 1) but has **no application client yet** — its first caller arrives in 6.2/6.3.

The prototype (`docs/Workers_Comp_Prototype.html`) is a design/behavior reference only; its credential-less `fetch` to `api.anthropic.com` is the anti-pattern AD-5 exists to kill — there is no successor, not even as a fallback.

### Architecture compliance (binding ADs for this story)

- **AD-5 local-only AI:** Ollama internal-network only, port never published; model names (`qwen3:*`, `bge-m3`) are deployment config, not code; no cloud code path exists.
- **AD-3 one PostgreSQL:** embeddings are pgvector rows in the same instance, Alembic-migrated — never a separate vector DB.
- **AD-7 scoping:** vector similarity search is a repository method like any other — caller context required, one unconditional employer filter, `ALL` as tautology; no role-conditional bypass.
- **AD-10 derived data:** `claim_embedding` rows are derived data of claim text — one computing path (`services/rag`), never user-writable.
- **AD-12 write ownership:** `services/rag` owns embeddings and all three tables; mutating services request staleness through `mark_claim_stale` (one refresh command, not two paths); retrieval returns `embedded_at`.
- **AD-11 PHI:** claim embeddings and anything derived from claim text are PHI-class; they live on the same encrypted volume, join the (Epic 8) purge cascade, and never appear in logs.
- **AD-15 E2E:** this story delivers the `model-stub` service to `deploy/compose.e2e.yaml` — the harness every later Epic-6 spec depends on.

### Data notes

Tables created here (all owned by `services/rag` per AD-12): `claim_embedding`, `knowledge_chunk`, `knowledge_embedding`. bge-m3 emits 1024-dim vectors — pin `vector(1024)` and note that changing `embedding_model` dimensionality is a migration, not a config flip. `stale`/`embedded_at` on `claim_embedding` carry the AD-12 staleness contract; Story 6.4's similar-case action discloses staleness beyond a configured threshold, so `embedded_at` must flow through retrieval results from day one. Knowledge-corpus ingestion (sourcing, chunking, refresh cadence) is explicitly **Deferred** — seed just enough real rows to make the RAG path honest.

### Stack arriving this story

| Component | Version / note |
|---|---|
| Ollama | current stable server, image digest pinned at deploy; chat `qwen3:14b` (24 GB GPU) / `qwen3:32b` (48 GB); embeddings `bge-m3`; CPU dev fallback `qwen3:8b` + `nomic-embed-text` |
| pgvector usage | already installed ≥ 0.8.2 (Story 1.1/1.2 image); first real vector columns land here |

Exact `qwen3` tag pinning is a Deferred decision (re-benchmark before pinning) — hence config, not code. LangGraph / langchain-ollama / assistant-ui do **not** install here (6.3).

### UX notes

None — this story has no UI surface. (The AI Insights tab empty state already exists from Epic 2 per UX-DR5 and is filled by 6.2.)

### Testing requirements

- Unit + integration tests per Task 8; the scoped-persona vector-search test is an explicit AC, not optional.
- Property-style determinism check on the claim-text composer (same claim state ⇒ same text/hash) is cheap and prevents refresh churn.
- Playwright: `e2e/stories/6-1-local-model-serving-embedding-foundation.spec.ts` tagged `@story:6-1 @epic:6` with exactly one `@smoke` happy path, run against the freshly reset e2e stack with the new model-stub. Story cannot move to `review`/`done` until it passes (AD-15 done-gate).
- CI: Alembic clean-upgrade against fresh DB must stay green with the new revision.

### Project Structure Notes

- `services/rag/` package exists (empty) since Story 1.1 — fill it; embedding repositories live in `server/data/` beside the existing scope-enforcing repositories.
- The model-stub implementation should live under `deploy/` (or `e2e/`) as e2e-profile-only tooling — keep it out of `server/` so it can never ship.
- `compose.gpu.yaml` was pre-announced in Story 1.1's tree comment ("arrives with Story 6.1") — this story creates it.
- Retro-wiring touches Epic 2/3 service commands: edit those services minimally (one owner-path call per command + test), no refactors.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 6.1]
- AD-5, AD-7, AD-12 full text; dependency diagram ("embeddings only" edge); Deferred (model pinning, knowledge-corpus ingestion, Ollama capacity): [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-5 / #AD-7 / #AD-12 / #Design Paradigm / #Deferred]
- Model table (footprints, CPU-dev pairing), RAG design & staleness narrative: [Source: docs/Architecture-LINEWORKER.md#5.1 / #5.3]
- AD-15 model-stub requirement + e2e profile: [Source: ARCHITECTURE-SPINE.md#AD-15]
- compose.e2e.yaml placeholder to replace: [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Task 5]
- Epic independence via backward retro-wiring (6.1→E2/E3): [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Compliance Checklist Results]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
