# Review: Web-Verification of Committed Technology Decisions

- **Document:** `ARCHITECTURE-SPINE.md` (architecture-lineworker-2026-08-07)
- **Review lens:** Every committed technology decision must be web-researched / reality-checked as of August 2026, not asserted from stale training data.
- **Reviewed:** 2026-08-07
- **Method:** Live WebSearch/WebFetch against PyPI, npm, official project blogs/release pages, ollama.com, and GitHub for every row of the Stack table plus the ecosystem claims (ZEN SDK existence + license, assistant-ui LangGraph package, Ollama model availability).

## Verdict

**Directionally sound, but the Stack table reads like a late-2024/early-2025 snapshot.** Every *product choice* checks out (all chosen technologies exist, are maintained, and fit the architecture), but roughly a third of the version pins are stale, and one is factually wrong (TanStack Table "v5"). None of the staleness invalidates an architectural decision (AD-1..10 all survive), but the pins should be refreshed before this spine becomes the build substrate, since story-level work will copy these numbers into `pyproject.toml` / `package.json` verbatim.

## Stack Table — Row-by-Row Verification (as of 2026-08-07)

| Stack row (spine) | Reality (verified Aug 2026) | Status |
| --- | --- | --- |
| Python 3.12 | Supported (EOL Oct 2028); 3.13 has been stable since Oct 2024. Conservative but defensible. | OK (conservative) |
| FastAPI 0.115+ | Latest is **0.136.1** (Apr 2026); monthly minor releases. `0.115+` is a floor pin so nothing breaks, but 0.115 is a Sept-2024 number — the floor is ~21 minors stale. | STALE FLOOR |
| SQLAlchemy 2.0 | Latest **2.0.51** (Jun 2026). Major-line pin correct. | OK |
| Alembic 1.14 | Latest is **1.18.5** (Jun 2026), docs already show 1.19.0. 1.14 is a late-2024 release. | OUTDATED PIN |
| PostgreSQL 16 | Current stable major is **18** (18.4 released; 19 in beta). 16 is still supported (16.14; EOL Nov 2028), but for a greenfield build starting Sept 2026 it is two majors behind with no rationale recorded. | OUTDATED CHOICE (works, but justify or bump to 17/18) |
| pgvector 0.8.x | Correct line — latest is **0.8.6**. Note: **0.8.2 (Feb 2026) fixed CVE-2026-3172** (buffer overflow in parallel HNSW builds), so the pin should be `>=0.8.2`, not bare `0.8.x`. | OK, TIGHTEN FLOOR (CVE) |
| LangGraph (Python) 1.0.x | Latest is **1.2.10** (Jul 2026). The 1.x line is right; a hard `1.0.x` pin would exclude a year of checkpointer/interrupt fixes the spine's AD-6 depends on. Should read `1.x` or `>=1.2`. | STALE PIN |
| langchain-ollama ≥0.3 | Latest **0.3.7**. Floor pin verified correct. | OK |
| Ollama / qwen3 / bge-m3 | `qwen3:14b` (9.3GB) and `qwen3:32b` (20GB) both live on ollama.com (Apache 2.0). `bge-m3:567m` (1.2GB, 8K ctx) still available. Note qwen3 dropped Apr 2025 and its Ollama tags are 5–10 months unrefreshed; the spine's Deferred item ("re-benchmark before pinning exact tags") correctly covers this. | OK (Deferred note is apt) |
| GoRules ZEN engine (Python SDK) | **Confirmed:** `zen-engine` on PyPI, native Python bindings, gorules/zen repo **MIT-licensed**. | OK |
| React 19 | Latest **19.2.7** (Jun 2026). | OK |
| Vite 6 | Current stable is **Vite 8** (8.0.9, Apr 2026 — unified Rust-based toolchain); Vite 6 is two majors behind. A greenfield SPA scaffolded in Sept 2026 would start on 8. | OUTDATED PIN |
| TypeScript 5.x | **TS 6.0 shipped Mar 2026** (final JS-based release) and **TS 7.0.2 went stable 2026-08-05** (Go-native compiler, ~10x builds, identical type-check semantics). 5.x is now two majors behind; 7.0's speed is exactly what a Vite SPA wants. | OUTDATED PIN |
| shadcn/ui (Radix + Tailwind 4) | Tailwind latest is **4.3.3** (Jul 2026) — "Tailwind 4" pin holds. shadcn/ui vendored model still current. | OK |
| TanStack Query / Table v5 | Query **v5 correct** (5.101.x, actively released). Table: **there is no TanStack Table v5** — the TanStack-branded line went v7 (React Table) → v8 (2022 rewrite) → **v9.0.0 (released this month)**. As written, the row pins Table to a version that does not exist. | **WRONG PIN (Table)** |
| Recharts 2.x | Latest is **3.10.1**; the 3.x line has been stable long enough to have its own migration guide, and 2.x is maintenance-only. New charts written today target 3.x. | OUTDATED PIN |
| assistant-ui `@assistant-ui/react-langgraph` | **Confirmed exists and active**: 0.13.12, published within the last week. Still 0.x, so "latest" pin is fine but expect API churn; lock the minor at scaffold time. | OK (0.x churn risk) |
| Docker Compose v2 / NVIDIA Container Toolkit | Still current. | OK |

## Findings

### F-1 [high] — TanStack Table pinned to a non-existent major ("v5")

Stack row `TanStack Query / Table | v5` is only half right. TanStack Query v5 is correct and current (5.101.x). But TanStack Table has never had a v5 under that name — v8 has been the stable major since 2022, and **v9.0.0 shipped in early August 2026**. This looks like the "v5" from Query being blanket-applied to the whole row without checking. Fix: split the row — Query v5, Table v8 (or evaluate v9, which is days old — v8 is the safe production pin).

### F-2 [high] — Vite 6 is two majors behind current stable (Vite 8)

Vite 8.0.9 is stable (Apr 2026), with Vite 7 in between (mid-2025). Vite 6 was current in late 2024 — a strong signal this pin came from training data, not research. A project scaffolded next month via `npm create vite` will get 8.x; pinning 6 means fighting the toolchain and every plugin's peer ranges from day one. Fix: pin Vite 8 (or 7 if a conservative LTS-ish posture is wanted — record the rationale either way).

### F-3 [medium] — PostgreSQL 16 chosen for a greenfield system when 18 is current stable

PG 16 is not wrong — it's supported until Nov 2028 and pgvector runs on it — but current stable is 18 (18.4), with 17 mature. The spine bakes "PostgreSQL 16" into an invariant heading (AD-3), the deployment diagram (`postgres:16`), and the Stack table with no recorded reason to start two majors back. For a system whose first deploy is months away, defaulting to 17 or 18 buys years of support runway at zero migration cost now. Fix: bump, or add one line of rationale (e.g., a pgaudit/pgvector packaging constraint) so the choice reads as decided rather than stale.

### F-4 [medium] — Backend/AI pins stale: Alembic 1.14, LangGraph 1.0.x, FastAPI 0.115 floor; pgvector floor misses a CVE

- Alembic latest is 1.18.5 (1.19 imminent); 1.14 is ~18 months old.
- LangGraph is at 1.2.10; a literal `1.0.x` pin excludes a year of fixes to exactly the checkpointer/interrupt machinery AD-6 relies on (`AsyncPostgresSaver`, `interrupt()`). Pin `1.x`/`>=1.2` instead.
- FastAPI `0.115+` floor is harmless but signals stale data; current is 0.136.x.
- pgvector `0.8.x` is the right line, but **0.8.2 fixed CVE-2026-3172** (buffer overflow in parallel HNSW index builds) — the floor should be `>=0.8.2` explicitly, since AD-3 puts embeddings in the primary datastore of a PHI system.

### F-5 [medium] — Frontend chain also stale: TypeScript 5.x, Recharts 2.x

- TypeScript 6.0 shipped Mar 2026 and 7.0.2 (Go-native compiler, ~10x builds, identical semantics) went stable **two days before this spine's date**. "5.x" is two majors behind; at minimum say `>=5.9` deliberately, but 7.0 is the natural choice for a fresh Vite SPA.
- Recharts is at 3.10.1; 2.x is legacy with a published 3.0 migration guide. New dashboard code should target 3.x — pinning 2.x guarantees a migration mid-project.

### F-6 [low] — Ecosystem existence claims all verified good

- **GoRules ZEN engine Python SDK**: `zen-engine` exists on PyPI with native Python bindings; the gorules/zen repository is MIT-licensed. AD-8's premise holds.
- **assistant-ui `@assistant-ui/react-langgraph`**: exists, actively published (0.13.12, updated within the last week). AD-9's copilot runtime premise holds. Caveat: still 0.x, so "latest" as a pin invites churn — freeze the minor at scaffold time.
- **Ollama models**: `qwen3:14b`, `qwen3:32b`, and `bge-m3` all remain in the Ollama library (Apache 2.0 / established). The spine's Deferred item on re-benchmarking before pinning exact tags is the right call — qwen3 is an Apr-2025 family and newer contemporaries may exist by build time.
- **Tailwind 4, React 19, SQLAlchemy 2.0, langchain-ollama ≥0.3, TanStack Query v5, Docker Compose v2**: all verified current and correctly pinned.

## Recommended Stack Table Corrections

| Row | Change to |
| --- | --- |
| FastAPI | `0.13x` current (floor `>=0.126` or similar recent) |
| Alembic | `1.18+` |
| PostgreSQL | `18` (or `17` with rationale); update AD-3 text + deployment diagram to match |
| pgvector | `>=0.8.2` (CVE-2026-3172) |
| LangGraph | `1.x` (`>=1.2`) |
| Vite | `8` |
| TypeScript | `7.0` (or `>=6.0` with rationale) |
| TanStack Query / Table | Query `v5` / Table `v8` (split the cell) |
| Recharts | `3.x` |
| assistant-ui | pin the current minor (`0.13.x`) rather than "latest" |

## Sources

- FastAPI releases: https://releasebot.io/updates/tiangolo/fastapi , https://pypi.org/project/fastapi-slim/
- SQLAlchemy 2.0.51: https://www.sqlalchemy.org/blog/2026/06/15/sqlalchemy-2.0.51-released/
- Alembic changelog (1.18.5 / 1.19.0 docs): https://alembic.sqlalchemy.org/en/latest/changelog.html
- PostgreSQL 18.4 / supported versions: https://www.postgresql.org/about/news/postgresql-184-1710-1614-1518-and-1423-released-3297/ , https://www.postgresql.org/support/versioning/
- pgvector 0.8.2 + CVE-2026-3172: https://www.postgresql.org/about/news/pgvector-082-released-3245 , https://github.com/pgvector/pgvector
- LangGraph 1.2.10: https://pypi.org/project/langgraph/
- langchain-ollama 0.3.7: https://pypi.org/project/langchain-ollama
- React versions: https://react.dev/versions
- Vite 8 releases: https://vite.dev/releases
- TypeScript 7.0 stable: https://www.infoq.com/news/2026/08/typescript-7-released/ , https://www.npmjs.com/package/typescript
- Tailwind 4.3.3: https://tailwindcss.com/blog , https://versionlog.com/tailwind-css/
- TanStack Query 5.101.x: https://www.npmjs.com/package/@tanstack/query-core
- TanStack Table v8→v9: https://www.npmjs.com/package/@tanstack/react-table , https://tanstack.com/table/v8/docs/installation
- Recharts 3.10.1: https://www.npmjs.com/package/recharts , https://github.com/recharts/recharts/wiki/3.0-migration-guide
- GoRules ZEN Python SDK + MIT license: https://pypi.org/project/zen-engine/ , https://github.com/gorules/zen/blob/master/LICENSE , https://docs.gorules.io/developers/sdks/python
- assistant-ui react-langgraph 0.13.12: https://www.npmjs.com/package/@assistant-ui/react-langgraph
- Ollama qwen3 tags: https://ollama.com/library/qwen3/tags , https://ollama.com/library/qwen3:14b
- Ollama bge-m3: https://ollama.com/library/bge-m3
