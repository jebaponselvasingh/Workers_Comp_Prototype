# Story 1.1: Running Project Skeleton

Status: ready-for-dev

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

- [ ] Task 1: Monorepo skeleton (AC: 4)
  - [ ] Create `lineworker/` at repo root with the exact tree from Dev Notes → Source Tree (empty `__init__.py` / `.gitkeep` where needed so the full layout exists on day one)
  - [ ] Root `README.md`: one-command dev instructions (`docker compose -f deploy/compose.yaml up`), tree overview, links to `docs/`
  - [ ] `.gitignore` covering Python, Node, env files (`.env*` ignored; only `*.env.example` templates committed)
- [ ] Task 2: Server scaffold (AC: 3, 4)
  - [ ] `server/` Python 3.12 project (managed with `uv`; `pyproject.toml`): FastAPI 0.115+, SQLAlchemy 2.0, Alembic 1.18, pydantic-settings, structlog, asyncpg/psycopg driver
  - [ ] One pydantic-settings module `server/config.py` (env-var driven: `DATABASE_URL`, `ENV`, log level; every future knob lands here — never scattered `os.environ` reads)
  - [ ] FastAPI app factory in `server/api/` with `GET /healthz` returning 200 + DB connectivity check (used by the compose healthcheck and CI smoke)
  - [ ] Alembic wired to `server/data/` (env.py reads `DATABASE_URL` from the settings module) with an empty baseline revision — **no domain tables; schema arrives in Story 1.2**
  - [ ] Tooling: `ruff` (lint + format), `mypy` (typecheck), `pytest` with one test asserting the app factory builds and `/healthz` returns 200 (httpx ASGI client, DB check monkeypatched or against the compose DB)
  - [ ] structlog configured JSON-mode, IDs-and-event-names only (AD-11 PHI log ban starts now)
- [ ] Task 3: Web scaffold (AC: 4, 5)
  - [ ] `web/`: Vite + React 19 + TypeScript strict; folders `src/features/{queue,claim-detail,dashboard,copilot,diary}/`, `src/components/ui/`, `src/api/`
  - [ ] Tailwind 4 (CSS-first `@theme`) with the prototype's token set translated 1:1 — see Dev Notes → Design Tokens (canonical hex values, names, and semantics)
  - [ ] Vendor shadcn/ui into `src/components/ui/` (init with the Tailwind theme; add a starter set: button, card, badge, dialog, tooltip, input, select)
  - [ ] Self-host fonts (no CDN on an on-prem network): Inter (body), Space Grotesk (display/brand), JetBrains Mono (IDs/figures) via `@fontsource` packages
  - [ ] SPA shell: renders the LINEWORKER brand mark + an empty layout frame styled with the tokens (dense card style visible) — no routes/screens yet (login is Story 1.3)
  - [ ] Tooling: `eslint` + `tsc --noEmit`, `vitest` with one smoke test rendering the shell
- [ ] Task 4: Compose dev environment (AC: 1, 2)
  - [ ] `deploy/compose.yaml`: services `web` (nginx), `api` (FastAPI/uvicorn), `postgres` (Postgres 18 + pgvector ≥ 0.8.2 image, e.g. `pgvector/pgvector:pg18`) — **no Ollama yet (Story 6.1), no backup job (Epic 8)**
  - [ ] nginx: serves the built SPA static files, proxies `/api/` → `api:8000`, is the **only** service with a published port; SSE-safe proxy settings on `/api` (`proxy_buffering off`) so the Epic 6 stream needs no nginx change
  - [ ] postgres on the internal compose network only — no `ports:` mapping; volume for data; `POSTGRES_PASSWORD` from env file
  - [ ] Healthchecks: postgres `pg_isready`, api `GET /healthz`, nginx config-test/HTTP check; `depends_on: condition: service_healthy` ordering postgres → api → web
  - [ ] `deploy/*.env.example` templates; real env files git-ignored (AC 4 no-secrets)
  - [ ] api container runs Alembic upgrade on start (entrypoint) so `docker compose up` from clean checkout reaches healthy unattended
- [ ] Task 5: E2E harness — AD-15 foundation (AC: 3)
  - [ ] `e2e/` Playwright project at monorepo root (`@playwright/test` 1.62.x pinned, Chromium only), `workers: 1`, `fullyParallel: false`
  - [ ] `deploy/compose.e2e.yaml` profile (nginx, api, postgres; the `model-stub` service is added by Story 6.1 — leave a comment placeholder)
  - [ ] DB-reset project-dependency setup (not `globalSetup`): drop schema → `alembic upgrade head` → seed (seed is a no-op until Story 1.2), running under a dedicated e2e-profile DB owner role
  - [ ] `e2e/fixtures/`: reset fixture + selector-policy helper (role-first, `data-testid` second, never CSS classes); login fixture arrives with Story 1.3
  - [ ] `e2e/stories/1-1-running-project-skeleton.spec.ts` tagged `@story:1-1 @epic:1` with the one happy path tagged `@smoke`: SPA shell loads through nginx, `/api/healthz` answers healthy through the proxy
- [ ] Task 6: CI gate (AC: 3)
  - [ ] GitHub Actions workflow (repo is GitHub-hosted; provider not architecture-pinned — document this choice in the workflow header): jobs for server lint+typecheck+pytest, web lint+typecheck+vitest, `alembic upgrade head` against a fresh Postgres 18 + pgvector service container, and the Playwright story spec + `@smoke` set against the e2e compose profile
  - [ ] All jobs configured as required checks for merge to main; full `e2e/` suite + (future) sprint-status↔spec bijection lint noted as the merge-to-main stage per AD-15
- [ ] Task 7: Verification (AC: all)
  - [ ] From a clean clone: `docker compose -f deploy/compose.yaml up` → all three containers healthy, SPA visible on the nginx port, `/api/healthz` 200 through the proxy, `docker compose ps` shows no published postgres port
  - [ ] Run every CI command locally and in CI; confirm required-check wiring

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

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
