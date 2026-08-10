# LINEWORKER

Workers' Compensation claims console — production rebuild of the single-file
prototype as a 3-tier system with local-only AI.

## One-command dev environment

```sh
docker compose -f deploy/compose.yaml up
```

That's it. From a clean checkout this builds the SPA, starts PostgreSQL 18
(+pgvector) on the internal network, runs Alembic migrations, and serves
everything through nginx at <http://localhost:8080>. nginx is the **only**
published port — the API is reached via the `/api` proxy and the database is
never exposed to the host.

Copy `deploy/.env.example` to `deploy/.env` first if you want to override
defaults (the compose file ships working dev values; compose auto-loads
`.env` from the `deploy/` project directory for `${…}` interpolation).

## Source tree

```text
lineworker/
  web/                     # React 19 SPA (Vite)
    src/features/          #   queue/ claim-detail/ dashboard/ copilot/ diary/
    src/components/ui/     #   vendored shadcn/ui
    src/api/               #   generated OpenAPI client + queryKeys (later)
  server/
    api/                   # FastAPI app factory, /healthz
    services/              #   claims/ financials/ worklist/ derivations/ rag/ audit/ blobstore
    rules/                 #   ZEN engine + JDM documents (arrives Epic 2/3)
    agents/                #   LangGraph copilot (arrives Epic 6)
    data/                  #   SQLAlchemy models + Alembic migrations
  e2e/                     # Playwright story-gate suite (AD-15)
    fixtures/              #   DB reset, selector policy
    stories/               #   one spec per story, named by sprint story key
  deploy/                  # compose.yaml, compose.e2e.yaml, nginx/, *.env.example
```

## Working on the server

```sh
cd server
uv sync                     # Python 3.12 env + deps
uv run pytest               # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy .
```

## Working on the web app

```sh
cd web
npm install
npm run dev                 # Vite dev server (proxies /api to the compose
                            # stack's nginx at localhost:8080; override with
                            # VITE_PROXY_TARGET for a host-run uvicorn)
npm test                    # vitest
npm run lint && npm run typecheck
```

## E2E (story gate)

```sh
cd e2e
npm install && npx playwright install chromium
docker compose -f ../deploy/compose.e2e.yaml up -d --build --wait
npm test                    # full suite, workers=1
npm run smoke               # @smoke set only
```

Every story ships `e2e/stories/<story-key>.spec.ts`; a story is not done until
its spec passes against the freshly reset e2e stack.

## Documentation

Planning artifacts and the architecture live outside this directory:

- Architecture: `../docs/Architecture-LINEWORKER.md` (rendered) and
  `../_bmad-output/planning-artifacts/architecture/` (spine — binding ADs)
- BRD & prototype (design reference only): `../docs/`
