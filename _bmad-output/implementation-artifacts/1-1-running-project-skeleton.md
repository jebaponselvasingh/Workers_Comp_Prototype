---
baseline_commit: 78dc3454757be90bf159abfe4eb9022c2b6838a9
---

# Story 1.1: Running Project Skeleton

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want the `lineworker/` monorepo scaffold with a one-command dev environment and CI gate,
so that every subsequent story lands on a consistent, tested foundation.

## Acceptance Criteria

1. **Given** a clean checkout, **when** `docker compose up` runs, **then** nginx serves the SPA shell as sole ingress with `/api` proxied to FastAPI, and PostgreSQL 18 + pgvector runs on the internal network with **no published DB port**.
2. **And** every container reports healthy via its health endpoint, with compose healthchecks gating startup order.
3. **Given** a merge to main, **when** CI runs, **then** lint, typecheck, tests, and Alembic upgrade against a fresh DB all pass as required checks.
4. **Given** the repository, **when** the source tree is inspected, **then** it matches the architecture layout (`web/`, `server/api|services|rules|agents|data`, `deploy/`), configuration is one pydantic-settings module (12-factor), and no secrets live in VCS.
5. **And** the web scaffold vendors shadcn/ui with the prototype's visual identity translated into Tailwind design tokens — the console aesthetic, ok/warn/error status color semantics, information-dense card styles (UX-DR12; see Design Tokens below for the canonical values).

## Tasks / Subtasks

- [x] Task 1: Monorepo skeleton (AC: 4)
  - [x] Create `lineworker/` at repo root with the exact tree from Dev Notes → Source Tree (empty `__init__.py` / `.gitkeep` where needed so the full layout exists on day one)
  - [x] Root `README.md`: one-command dev instructions (`docker compose -f deploy/compose.yaml up`), tree overview, links to `docs/`
  - [x] `.gitignore` covering Python, Node, env files (`.env*` ignored; only `*.env.example` templates committed)
- [x] Task 2: Server scaffold (AC: 3, 4)
  - [x] `server/` Python 3.12 project (managed with `uv`; `pyproject.toml`): FastAPI 0.115+, SQLAlchemy 2.0, Alembic 1.18, pydantic-settings, structlog, asyncpg/psycopg driver
  - [x] One pydantic-settings module `server/config.py` (env-var driven: `DATABASE_URL`, `ENV`, log level; every future knob lands here — never scattered `os.environ` reads)
  - [x] FastAPI app factory in `server/api/` with `GET /healthz` returning 200 + DB connectivity check (used by the compose healthcheck and CI smoke)
  - [x] Alembic wired to `server/data/` (env.py reads `DATABASE_URL` from the settings module) with an empty baseline revision — **no domain tables; schema arrives in Story 1.2**
  - [x] Tooling: `ruff` (lint + format), `mypy` (typecheck), `pytest` with one test asserting the app factory builds and `/healthz` returns 200 (httpx ASGI client, DB check monkeypatched or against the compose DB)
  - [x] structlog configured JSON-mode, IDs-and-event-names only (AD-11 PHI log ban starts now)
- [x] Task 3: Web scaffold (AC: 4, 5)
  - [x] `web/`: Vite + React 19 + TypeScript strict; folders `src/features/{queue,claim-detail,dashboard,copilot,diary}/`, `src/components/ui/`, `src/api/`
  - [x] Tailwind 4 (CSS-first `@theme`) with the prototype's token set translated 1:1 — see Dev Notes → Design Tokens (canonical hex values, names, and semantics)
  - [x] Vendor shadcn/ui into `src/components/ui/` (init with the Tailwind theme; add a starter set: button, card, badge, dialog, tooltip, input, select)
  - [x] Self-host fonts (no CDN on an on-prem network): Inter (body), Space Grotesk (display/brand), JetBrains Mono (IDs/figures) via `@fontsource` packages
  - [x] SPA shell: renders the LINEWORKER brand mark + an empty layout frame styled with the tokens (dense card style visible) — no routes/screens yet (login is Story 1.3)
  - [x] Tooling: `eslint` + `tsc --noEmit`, `vitest` with one smoke test rendering the shell
- [x] Task 4: Compose dev environment (AC: 1, 2)
  - [x] `deploy/compose.yaml`: services `web` (nginx), `api` (FastAPI/uvicorn), `postgres` (Postgres 18 + pgvector ≥ 0.8.2 image, e.g. `pgvector/pgvector:pg18`) — **no Ollama yet (Story 6.1), no backup job (Epic 8)**
  - [x] nginx: serves the built SPA static files, proxies `/api/` → `api:8000`, is the **only** service with a published port; SSE-safe proxy settings on `/api` (`proxy_buffering off`) so the Epic 6 stream needs no nginx change
  - [x] postgres on the internal compose network only — no `ports:` mapping; volume for data; `POSTGRES_PASSWORD` from env file
  - [x] Healthchecks: postgres `pg_isready`, api `GET /healthz`, nginx config-test/HTTP check; `depends_on: condition: service_healthy` ordering postgres → api → web
  - [x] `deploy/*.env.example` templates; real env files git-ignored (AC 4 no-secrets)
  - [x] api container runs Alembic upgrade on start (entrypoint) so `docker compose up` from clean checkout reaches healthy unattended
- [x] Task 5: E2E harness — AD-15 foundation (AC: 3)
  - [x] `e2e/` Playwright project at monorepo root (`@playwright/test` 1.62.x pinned, Chromium only), `workers: 1`, `fullyParallel: false`
  - [x] `deploy/compose.e2e.yaml` profile (nginx, api, postgres; the `model-stub` service is added by Story 6.1 — leave a comment placeholder)
  - [x] DB-reset project-dependency setup (not `globalSetup`): drop schema → `alembic upgrade head` → seed (seed is a no-op until Story 1.2), running under a dedicated e2e-profile DB owner role
  - [x] `e2e/fixtures/`: reset fixture + selector-policy helper (role-first, `data-testid` second, never CSS classes); login fixture arrives with Story 1.3
  - [x] `e2e/stories/1-1-running-project-skeleton.spec.ts` tagged `@story:1-1 @epic:1` with the one happy path tagged `@smoke`: SPA shell loads through nginx, `/api/healthz` answers healthy through the proxy
- [x] Task 6: CI gate (AC: 3)
  - [x] GitHub Actions workflow (repo is GitHub-hosted; provider not architecture-pinned — document this choice in the workflow header): jobs for server lint+typecheck+pytest, web lint+typecheck+vitest, `alembic upgrade head` against a fresh Postgres 18 + pgvector service container, and the Playwright story spec + `@smoke` set against the e2e compose profile
  - [x] All jobs configured as required checks for merge to main; full `e2e/` suite + (future) sprint-status↔spec bijection lint noted as the merge-to-main stage per AD-15 *(required-check enforcement itself is a GitHub repo setting — flagged in Completion Notes for the repo admin)*
- [x] Task 7: Verification (AC: all)
  - [x] From a clean clone: `docker compose -f deploy/compose.yaml up` → all three containers healthy, SPA visible on the nginx port, `/api/healthz` 200 through the proxy, `docker compose ps` shows no published postgres port
  - [x] Run every CI command locally and in CI; confirm required-check wiring *(all CI commands verified locally; CI execution + required-check wiring happen on first push — see Completion Notes)*

### Review Follow-ups (AI)

- [x] [AI-Review][High] `.github/workflows/ci.yaml`: PR e2e stage hardcodes `--grep "@story:1-1\b|@smoke\b"` — derive the story key from branch/PR label; when no key is derivable, fall back to the full suite (fail closed, never vacuous)
- [x] [AI-Review][High] `deploy/compose.yaml`: `dev.env` override is a silent no-op (interpolation never reads a service `env_file`; `environment:` outranks it) — switch to compose's auto-loaded `deploy/.env`, drop the inert `env_file` block
- [x] [AI-Review][High] `e2e/fixtures/`: per-spec-file DB reset enforced only by a comment — export a base `test` with a file-scoped auto-reset fixture; recycle the api DB pool as part of reset
- [x] [AI-Review][High] `server/logging_config.py`: uvicorn/alembic/sqlalchemy stdlib logs bypass the AD-11 JSON pipeline — route stdlib logging through structlog's `ProcessorFormatter`
- [x] [AI-Review][Med] `deploy/nginx/default.conf`: literal `proxy_pass http://api:8000/` freezes the api IP at nginx startup — use Docker DNS resolver + variable upstream
- [x] [AI-Review][Med] `web/vite.config.ts`: dev proxy targets `localhost:8000` which nothing publishes — point it at the nginx ingress (`localhost:8080`)
- [x] [AI-Review][Med] `web/Dockerfile`: `web/.dockerignore` never applied (build context is `lineworker/`) — add `lineworker/.dockerignore`
- [x] [AI-Review][Med] `e2e/fixtures/reset.ts`: alembic step runs as the runtime app role, not `lineworker_e2e_owner` — override `DATABASE_URL` for the migrate exec
- [x] [AI-Review][Med] `web/src/index.css`: shadcn `--accent` alias dead (`bg-accent` resolves to brand orange, WCAG AA fail on focus states) — resolve the token collision
- [x] [AI-Review][Low] `web/src/index.css`: shadcn animation utilities are dead classes — add and import `tw-animate-css`

## Dev Notes

### What this story is — and is not

Greenfield scaffold. There is **no existing `lineworker/` code**; the only artifacts are planning docs and the single-file prototype (`docs/Workers_Comp_Prototype.html`) which is a **design/behavior reference, never a code source** — none of its code survives (architecture executive summary). Do NOT copy its JS; do NOT add auth (1.3), domain tables or seed data (1.2), Ollama (6.1), or any business endpoint. The deliverable is: tree + config + compose + CI + e2e harness + styled empty shell.

Scaffold location: create `lineworker/` as a directory at the repository root (per the spine's fixed source tree). The existing repo-level `docs/` stays where it is — do not move user files.

### Architecture compliance (binding ADs for this story)

- **AD-1 layering:** the tree itself enforces it — `web/` will only ever know the API contract; keep the FastAPI app in `api/` thin from day one.
- **AD-3 one PostgreSQL:** single postgres service; Alembic is the only migration mechanism — never `create_all()`, never a second store.
- **AD-5 posture:** no cloud AI code path exists, starting now; nothing in the scaffold references any external model API.
- **AD-11 logging:** structlog JSON, IDs and event names only — the PHI ban is cheapest to honor from the first line.
- **AD-15 E2E gate:** this story ships the harness every later story depends on: one Playwright project, e2e compose profile, per-spec DB reset, story-key spec naming, `@story`/`@epic`/`@smoke` tags (grep patterns anchored so `@story:1-1` never matches `1-10`).
- **Conventions:** 12-factor config via one pydantic-settings module; secrets in env files outside VCS; health endpoint on every container; compose healthchecks gate startup order; nginx sole ingress; DB/Ollama ports internal-only.

### Tech stack (versions pinned by the spine, verified 2026-08-07/09 by the architecture's deep-research pass — do not re-litigate; do not silently upgrade)

| Component | Version |
|---|---|
| Python / FastAPI | 3.12 / 0.115+ |
| SQLAlchemy / Alembic | 2.0 / 1.18 |
| PostgreSQL / pgvector | 18 / ≥ 0.8.2 (CVE-2026-3172 floor) |
| React / Vite / TypeScript | 19 / 8 / 7.0 |
| Tailwind / shadcn-ui | 4 / vendored |
| TanStack Query / Table | v5 / v8 (install now; first real use Story 1.3+) |
| Playwright | 1.62.x pinned, Chromium gate browser |
| Docker Compose | v2 |

Not installed this story: LangGraph, langchain-ollama, ZEN engine, Recharts, assistant-ui — each arrives with its first consuming story.

### Source tree (fixed by the spine — deviations are architecture changes, not dev discretion)

```text
lineworker/
  web/                     # React 19 SPA (Vite)
    src/features/          #   queue/ claim-detail/ dashboard/ copilot/ diary/  (empty dirs now)
    src/components/ui/     #   vendored shadcn/ui
    src/api/               #   (empty now; generated OpenAPI client + queryKeys later)
  server/
    api/                   # FastAPI app factory, /healthz  (auth deps arrive 1.3)
    services/              #   claims/ financials/ worklist/ derivations/ rag/ audit/ blobstore (empty pkgs)
    rules/                 #   (empty pkg; ZEN arrives Epic 2/3)
    agents/                #   (empty pkg; LangGraph arrives Epic 6)
    data/                  #   Alembic env + versions/ (baseline only), models pkg (empty)
  e2e/
    fixtures/              #   DB reset, selector policy  (persona login arrives 1.3)
    stories/               #   1-1-running-project-skeleton.spec.ts
  deploy/                  # compose.yaml, compose.e2e.yaml, nginx/, *.env.example
                           # (compose.gpu.yaml arrives with Story 6.1)
```

### Design tokens — UX-DR12 (canonical values extracted from the design contract)

⚠️ **Documented discrepancy:** epics.md UX-DR12 says "dark console aesthetic", but the actual design contract (`docs/Workers_Comp_Prototype.html` `:root`, line 7) defines a single **light** palette — there is no dark block anywhere in the file. **The file is the contract; these hex values are canonical.** Port them exactly; do not invent a dark theme. (Flagged to the user at story creation; if a dark identity is later wanted, that's a change request.)

| Prototype var | Value | Semantic → suggested Tailwind token |
|---|---|---|
| `--bg` | `#EEF1F4` | app background |
| `--pn` | `#fff` | panel/card surface |
| `--p2` | `#F5F7F9` | secondary surface |
| `--bd` | `#DCE1E7` | border |
| `--ac` / `--acd` | `#E8560A` / `#FEE9DC` | accent (orange) / accent-soft |
| `--st` / `--sld` | `#1D6A96` / `#DAE9F4` | steel blue (info/stage) / soft |
| `--tx` | `#1A2228` | primary text |
| `--mt` | `#57636E` | muted text |
| `--ft` | `#8B95A1` | faint text |
| `--ok` / `--okd` | `#1D7A45` / `#D9F0E4` | success / success-soft |
| `--wn` / `--wnd` | `#9A6E06` / `#FBF0D6` | warning / warning-soft |
| `--er` / `--erd` | `#C73E2D` / `#FADDDA` | error / error-soft |
| `--hr` | `rgba(20,30,40,.06)` | hairline/divider |

Name the Tailwind 4 `@theme` tokens semantically (`--color-surface`, `--color-accent`, `--color-ok`, …) and map shadcn/ui's CSS variables onto them. Status color semantics ok/warn/error must ride these exact hues — every later surface (queue chips, KPI cards, SLA tiles) reuses them.

Fonts (prototype loads from Google CDN — **self-host instead**, on-prem has no internet dependency): Inter 400/500/600/700 (body), Space Grotesk 500/600/700 (display/brand), JetBrains Mono 400/500/600 (claim IDs, figures). All OFL — use `@fontsource-*` packages.

Information-dense card style: tight paddings, terse labels, hairline dividers (see any `.qcard`/`.kpi` in the prototype for feel). Demonstrate it once in the shell frame so reviewers can compare side-by-side with the prototype.

### Compose specifics

- Only nginx publishes a port. `docker compose ps` must show nothing published for postgres (AC 1 is explicit about this) — and none for api either; it's reached only via the proxy.
- pgvector: use an image that bundles the extension (`pgvector/pgvector:pg18`); the extension itself gets `CREATE EXTENSION` in Story 1.2's migration — nothing needed now beyond image choice.
- api entrypoint: run `alembic upgrade head` before uvicorn so a clean checkout reaches healthy with one command (AC 1 "clean checkout").
- Healthcheck chain: postgres healthy → api starts (and can migrate) → api healthy → nginx starts. Test by `docker compose up` from scratch and watching states.
- nginx `/api/` proxy: strip or keep the prefix consistently (recommend keeping `/api` as the FastAPI root_path); include `proxy_buffering off` + long read timeout on this location now so Epic 6's SSE stream works without touching ingress.
- dev TLS is not required (prod TLS is an Epic 8 concern); don't build cert plumbing yet.

### E2E harness notes (read AD-15 in the spine before implementing — it is the most detailed AD)

- Reset semantics: project-dependency setup runs before **every spec file**; reset = drop schema → migrate → seed. With only a baseline migration, this is fast and proves the mechanism the whole roadmap relies on.
- The e2e DB owner role that can drop schema exists **only** in the e2e compose profile (in Story 1.2+ the runtime app role must not be able to — AD-4 grants).
- Spec assertion targets for 1-1: page loads through nginx (assert on the brand mark via accessible role), `/api/healthz` proxied response ok. Keep it structural; there is no login yet.
- CI grep patterns: anchor story tags (`@story:1-1\b` style) — AD-15 calls out the `1-3` vs `1-30` trap explicitly.

### Config module spec

One class in `server/config.py` (pydantic-settings `BaseSettings`): `database_url`, `env` (`dev|e2e|prod`), `log_level`; loaded once, imported everywhere; `.env` file support for local dev only. Every later config value (model names, SLA targets, retention knobs) extends this module — establishing it as the single 12-factor surface is the point of AC 4.

### Testing requirements (this story's definition of done)

- Server: pytest green (app factory + healthz).
- Web: vitest green (shell renders), eslint + tsc clean.
- Server: ruff + mypy clean.
- Alembic: `upgrade head` clean against a fresh DB (CI job with Postgres service container).
- E2E: `1-1-running-project-skeleton.spec.ts` passes against the freshly reset e2e compose stack; `@smoke` tagged.
- Story cannot move to `review`/`done` until the spec passes (AD-15 done-gate) — this rule starts with this story.

### Project Structure Notes

- This is the first code in the repo; there are no conflicts with existing modules. The BMad planning artifacts (`_bmad-output/`) and `docs/` are siblings of `lineworker/` and are not part of the build.
- Naming conventions locked now for everything downstream: Python snake_case, TS camelCase, React components PascalCase, DB snake_case per the Excel mapping (first used in 1.2), API JSON camelCase via Pydantic alias generator (first used in 1.3).
- Tool choices not pinned by architecture, chosen here as conventional defaults (documented so later stories don't churn): `uv` for Python env/deps, `ruff` + `mypy` server-side, `eslint` + `tsc` web-side, GitHub Actions for CI. Changing any of these later is allowed but must update this note and CI.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 1.1]
- Source tree, stack table, conventions rows (config, health, CI, logging): [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Structural Seed / #Stack / #Consistency Conventions]
- AD-15 E2E gate (full text): [Source: ARCHITECTURE-SPINE.md#AD-15]
- Deployment view & environments: [Source: docs/Architecture-LINEWORKER.md#8. Deployment & operations]
- Design tokens: [Source: docs/Workers_Comp_Prototype.html line 7 `:root`; fonts line 5]
- UX-DR12 + token advisory: [Source: _bmad-output/planning-artifacts/epics.md#UX Design Requirements; implementation-readiness-report-2026-08-09.md#Warnings item 3]
- Readiness confirmation Story 1.1 is the sanctioned scaffold story: [Source: implementation-readiness-report-2026-08-09.md#Compliance Checklist Results]

## Senior Developer Review (AI)

**Date:** 2026-08-10 · **Reviewer:** /code-review (multi-angle adversarial, fresh-context fork) · **Outcome:** Changes Requested

48 raw candidates from 8 finder angles → 20 verified distinct findings (16 CONFIRMED, 4 PLAUSIBLE, 0 REFUTED) → 10 most severe reported. Severity: 4 High, 5 Medium, 1 Low. Nothing invalidates the demonstrated ACs; the pattern is "works today, breaks on the next story."

### Action Items

- [x] [High] CI PR e2e grep hardcodes `@story:1-1` — future story specs silently excluded; gate goes vacuous (`.github/workflows/ci.yaml:107`)
- [x] [High] `deploy/dev.env` override mechanism is a silent no-op — committed default password survives a believed rotation (`deploy/compose.yaml:32`)
- [x] [High] AD-15 per-spec-file reset enforced only by a comment; no auto-reset base fixture; api pool never recycled (`e2e/fixtures/db-reset.setup.ts:7`)
- [x] [High] Only structlog emits JSON — uvicorn/alembic/sqlalchemy logs bypass the AD-11 PHI-safe pipeline (`server/logging_config.py:12`)
- [x] [Med] nginx resolves `api` once at startup — recreating api leaves a dead-IP proxy while healthchecks stay green (`deploy/nginx/default.conf:11`)
- [x] [Med] Vite dev proxy targets `localhost:8000`, which no compose service publishes — documented dev workflow cannot reach the API (`web/vite.config.ts:17`)
- [x] [Med] `web/.dockerignore` never applied (compose context is `lineworker/`) — host `node_modules` ships into the image, ~506MB context per build (`web/Dockerfile:7`)
- [x] [Med] e2e reset's alembic step runs as the app role, not `lineworker_e2e_owner` — breaks when AD-4 grants land (`e2e/fixtures/reset.ts:32`)
- [x] [Med] shadcn `--accent` alias dead — `bg-accent` focus/hover states render brand orange behind near-black text, WCAG AA fail (`web/src/index.css:58`)
- [x] [Low] `tw-animate-css` neither installed nor imported — all vendored shadcn animation utilities are dead classes (`web/src/index.css:1`)

### Verified-but-cut (below severity cap, no action required this story)

CI `uv run alembic` without `--no-dev`; no Playwright timeout override for the reset setup; ~~`config.py` scheme rewrite breaks on `postgres://` URLs~~; ~~TS6/TS7 compiler split leaves e2e untypechecked in CI~~; ~40 duplicated compose lines with credentials in 4 places; CI caching gaps (uv, Playwright browsers, docker layers); 101 font files (~1.5MB) from full @fontsource imports; dead `@tanstack/*` deps (spine-mandated pre-install); repo-root CLAUDE.md now misdescribes the repository.

Struck items were resolved during **Story 1.2's** review round (2026-08-10), which reviewed the whole untracked tree and re-raised both as findings — see 1.2's Senior Developer Review. The rest still stand.

## Dev Agent Record

### Agent Model Used

claude-fable-5 (Claude Fable 5)

### Debug Log References

- Host had no container runtime; user approved installing colima + docker CLI via Homebrew (headless). Compose plugin registered via `cliPluginsExtraDirs` in `~/.docker/config.json`. Local engine: Docker 29.5.2 / Compose 5.4.0.
- typescript-eslint@8.66 hard-refuses TS 7.0 at runtime (peer `<6.1.0`; upstream tracking issue typescript-eslint#10940). Resolution follows the documented TS 7 transition setup: root `typescript@6.0.3` serves the eslint toolchain's API needs; the real compiler is the npm alias `typescript7` (`npm:typescript@7.0.2`), invoked as `node node_modules/typescript7/bin/tsc --noEmit` by the `typecheck`/`build` scripts. Drop the shim when typescript-eslint ships TS 7 support.
- TS 7 removed `baseUrl` (TS5102) — `paths` is used without it.
- vitest runs with `globals: false`, so testing-library auto-cleanup never registers; `src/test/setup.ts` registers `afterEach(cleanup)` explicitly (first symptom: duplicate elements across tests).
- Review fix round: `pg_terminate_backend` via a `pg_signal_backend` grant failed because the bootstrap app role is a superuser in the ephemeral e2e DB (members of pg_signal_backend cannot signal superuser backends); the pool recycle now runs as the app role itself over the container-local socket (same-role termination — privilege-free, works before and after 1.2's role hardening). The grant stays in the init SQL for the post-1.2 world.

### Completion Notes List

- **AC 1–2 verified live:** `docker compose -f deploy/compose.yaml up` from the clean tree → postgres → api (Alembic upgrade in entrypoint) → web, all three healthy via compose healthchecks; SPA serves at :8080, `/api/healthz` → `{"status":"ok","db":"ok"}` through the nginx proxy; `docker compose ps` shows **no** published port for postgres or api.
- **AC 3:** `.github/workflows/ci.yaml` with four jobs (server lint+typecheck+pytest, web lint+typecheck+vitest, Alembic against fresh pgvector:pg18 service container, Playwright story+smoke against the e2e compose profile). Every job's commands ran green locally. ⚠️ Two items need repo-admin action on first push: marking the four jobs as required checks in GitHub branch protection, and confirming the first Actions run.
- **AC 4:** tree matches the spine's source-tree exactly; `config.py` is the single pydantic-settings surface (`DATABASE_URL`, `ENV`, `LOG_LEVEL`); no secrets in VCS — compose interpolation defaults are synthetic dev-only values, overridable via git-ignored `deploy/dev.env` (`dev.env.example` committed).
- **AC 5:** prototype `:root` palette ported 1:1 into Tailwind 4 `@theme` tokens (`--color-bg/surface/accent/ok/warn/error/…`), shadcn/ui semantic variables mapped onto them; 7 shadcn components vendored (button, card, badge, dialog, tooltip, input, select); Inter / Space Grotesk / JetBrains Mono self-hosted via @fontsource (bundled into dist — no CDN); shell renders brand mark + a dense sample card (tight paddings, hairline dividers, ok/warn/error chips).
- **AD-15 gate green:** e2e stack (port 8081, ephemeral DB, e2e-owner init SQL) + Playwright `workers:1`: setup project reset (drop schema → `alembic upgrade head` → no-op seed) ran under `lineworker_e2e_owner`, then both spec tests passed; `--grep @smoke` subset also verified. 3/3 passed.
- **Proxy prefix contract:** nginx strips `/api` (`proxy_pass http://api:8000/`), FastAPI carries `root_path="/api"`; the Vite dev proxy mirrors the strip. SSE-safe settings (`proxy_buffering off`, long read timeout) already on the `/api` location.
- **Review round (2026-08-10) — all 10 findings resolved and re-verified live:**
  - ✅ Resolved review finding [High]: CI PR e2e grep now derives story keys from branch name + PR labels, counts only keys with a matching spec file, and falls back to the **full** suite when none is derivable — the gate can narrow but never go vacuous. Logic simulated locally for story-branch, spurious-number, label-only, and no-key cases.
  - ✅ Resolved review finding [High]: compose now documents and uses compose's auto-loaded `deploy/.env` for interpolation (inert `env_file` block removed; example renamed to `deploy/.env.example`). Proven live: a test `deploy/.env` rotated `POSTGRES_PASSWORD` in `docker compose config` output; file confirmed git-ignored.
  - ✅ Resolved review finding [High]: `e2e/fixtures/test.ts` exports the base `test` with a file-scoped auto-reset fixture (reset runs when `testInfo.file` changes — enforced by construction under `workers: 1`); reset now ends by terminating the api's pooled DB connections, and the engine's new `pool_pre_ping` reconnects transparently (healthz green immediately after reset in the passing suite).
  - ✅ Resolved review finding [High]: stdlib logging routed through structlog `ProcessorFormatter` (uvicorn handlers stripped → propagate to root; alembic `env.py` calls `configure_logging`). Verified live in container logs: alembic, uvicorn.error, uvicorn.access, and app events all render as JSON. 3 new unit tests.
  - ✅ Resolved review finding [Med]: nginx `/api` uses Docker's embedded DNS resolver + variable upstream (`valid=10s`). Proven live: forced api onto a new IP (172.18.0.3 → .5) — proxy answered 200 without touching the web container.
  - ✅ Resolved review finding [Med]: Vite dev proxy targets the nginx ingress (`localhost:8080`, which strips `/api` itself); `VITE_PROXY_TARGET` overrides for host-run uvicorn. README corrected.
  - ✅ Resolved review finding [Med]: `lineworker/.dockerignore` added for the monorepo build context. Proven: web build stage contains only linux native bindings — no darwin leakage from host `node_modules`.
  - ✅ Resolved review finding [Med]: the e2e reset's alembic step execs with `DATABASE_URL` overridden to the `lineworker_e2e_owner` URL — schema objects are owned by the e2e owner role, matching the AC and surviving AD-4 grant hardening.
  - ✅ Resolved review finding [Med]: prototype orange renamed to `--color-brand`/`--color-brand-soft`; shadcn's `accent` semantic keeps the utility name and maps to the soft wash (`--color-accent: var(--accent)` in `@theme inline`). Verified in built CSS: all `bg-accent` variants resolve to `var(--accent)` → `#fee9dc`.
  - ✅ Resolved review finding [Low]: `tw-animate-css` installed and imported; built CSS now contains the animate-in/out keyframe utilities.
- **Post-1.2 review round (2026-08-10) — this story's files, changed after it reached `done`:** Story 1.2's review covered the whole untracked tree, so eight fixes landed on 1.1's surface. `config.py` now parses URLs with `make_url` instead of string-replacing the scheme (resolves a verified-but-cut item); `api/app.py` health check logs the exception class; `deploy/nginx/default.conf` answers a bare `/api` with a 308 and sets `absolute_redirect off` so the published port survives; `web/src/index.css` renames the `muted` text token out of shadcn's background namespace and the four vendored shadcn components lose their `dark:` utilities (the light-only rule is now stated in the CSS header for future `shadcn add`); `e2e/fixtures/test.ts` asserts `workers === 1` rather than trusting it; `e2e/package.json` + CI gain a `typecheck` step (resolves the second verified-but-cut item); `server/alembic.ini` gains `file_template`; `server/entrypoint.sh` gains the boot-time role reconcile. Full details and verification in Story 1.2's Senior Developer Review + Completion Notes.
- **Toolchain notes for later stories:** TS 7.0.2 is the compiler via `typescript7` npm alias; root `typescript@6.0.3` exists only for typescript-eslint (drop when upstream supports TS 7 — typescript-eslint#10940). TanStack Query v5 + Table v8 installed per spine, unused until 1.3+. Local dev container runtime is colima (installed this session with user approval).

### Change Log

- 2026-08-10: Story 1.1 implemented end to end — `lineworker/` monorepo scaffold (server, web, deploy, e2e), CI workflow, dev + e2e compose stacks verified healthy, AD-15 story spec green. Status → review.
- 2026-08-10: Addressed code review findings — 10 items resolved (4 High, 5 Med, 1 Low), each re-verified against the live stacks; full regression + AD-15 gate re-run green (pytest 5, vitest 2, Playwright 3/3 + smoke). Status → review.
- 2026-08-10: Eight fixes to this story's files landed during **Story 1.2's** review round (ci.yaml, nginx, index.css + vendored shadcn components, config.py, api/app.py, alembic.ini, entrypoint.sh, e2e fixtures/package.json), including two of this story's verified-but-cut items. Recorded here for traceability; the story stays `done` and its ACs are unaffected.

### File List

New files (paths relative to repo root):

- `.github/workflows/ci.yaml`
- `lineworker/README.md`
- `lineworker/.gitignore`
- `lineworker/.dockerignore` (added in review round)
- `lineworker/deploy/compose.yaml`
- `lineworker/deploy/compose.e2e.yaml`
- `lineworker/deploy/.env.example` (renamed from `dev.env.example` in review round)
- `lineworker/deploy/e2e-init/01-e2e-owner.sql`
- `lineworker/deploy/nginx/default.conf`
- `lineworker/server/pyproject.toml`
- `lineworker/server/uv.lock`
- `lineworker/server/alembic.ini`
- `lineworker/server/config.py`
- `lineworker/server/logging_config.py`
- `lineworker/server/Dockerfile`
- `lineworker/server/.dockerignore`
- `lineworker/server/entrypoint.sh`
- `lineworker/server/api/__init__.py`
- `lineworker/server/api/app.py`
- `lineworker/server/data/__init__.py`
- `lineworker/server/data/env.py`
- `lineworker/server/data/script.py.mako`
- `lineworker/server/data/versions/20260810_0001_baseline.py`
- `lineworker/server/data/models/__init__.py`
- `lineworker/server/services/{claims,financials,worklist,derivations,rag,audit,blobstore}/__init__.py` (7 empty packages)
- `lineworker/server/rules/__init__.py`
- `lineworker/server/agents/__init__.py`
- `lineworker/server/tests/__init__.py`
- `lineworker/server/tests/test_healthz.py`
- `lineworker/server/tests/test_logging.py` (added in review round)
- `lineworker/web/package.json`
- `lineworker/web/package-lock.json`
- `lineworker/web/index.html`
- `lineworker/web/vite.config.ts`
- `lineworker/web/tsconfig.json`
- `lineworker/web/eslint.config.js`
- `lineworker/web/components.json`
- `lineworker/web/Dockerfile`
- `lineworker/web/.dockerignore`
- `lineworker/web/src/main.tsx`
- `lineworker/web/src/App.tsx`
- `lineworker/web/src/App.test.tsx`
- `lineworker/web/src/index.css`
- `lineworker/web/src/lib/utils.ts`
- `lineworker/web/src/test/setup.ts`
- `lineworker/web/src/components/ui/{badge,button,card,dialog,input,select,tooltip}.tsx` (vendored shadcn/ui)
- `lineworker/web/src/features/{queue,claim-detail,dashboard,copilot,diary}/.gitkeep`, `lineworker/web/src/api/.gitkeep`, `lineworker/web/src/components/ui/.gitkeep`
- `lineworker/e2e/package.json`
- `lineworker/e2e/package-lock.json`
- `lineworker/e2e/playwright.config.ts`
- `lineworker/e2e/tsconfig.json`
- `lineworker/e2e/fixtures/db-reset.setup.ts`
- `lineworker/e2e/fixtures/reset.ts`
- `lineworker/e2e/fixtures/selectors.ts`
- `lineworker/e2e/fixtures/test.ts` (added in review round — base test with per-spec-file auto-reset)
- `lineworker/e2e/stories/1-1-running-project-skeleton.spec.ts`

Modified files:

- `_bmad-output/implementation-artifacts/1-1-running-project-skeleton.md` (this story file)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (status transitions)
