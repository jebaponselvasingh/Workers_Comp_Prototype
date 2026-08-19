# LINEWORKER

Workers' Compensation claims console — production rebuild of the single-file
prototype as a 3-tier system with local-only AI.

## One-command dev environment

```sh
docker compose -f deploy/compose.yaml up
```

That's it. From a clean checkout this builds the SPA, starts PostgreSQL 18
(+pgvector) and Ollama on the internal network, runs Alembic migrations, and
serves everything through nginx at <http://localhost:8080>. nginx is the
**only** published port — the API is reached via the `/api` proxy, and neither
the database nor the model server is ever exposed to the host.

**The first boot downloads models, and it is slow.** Since Story 6.1 the
`ollama` service pulls the chat and embedding models (`qwen3:14b` and `bge-m3`
by default — several gigabytes) *before* it reports healthy, and `api` waits
for that. So a first `up` on a clean machine takes as long as your connection
does, with nothing to see until it finishes. Every boot after that is instant:
the models live in the `ollama-models` volume, and only `docker compose down
-v` re-downloads them. Set `CHAT_MODEL`/`EMBEDDING_MODEL` in `deploy/.env` to
pull something smaller — but read the note in `deploy/.env.example` first,
because changing the *embedding* model's dimensionality is a migration rather
than a config flip.

**The chat model is the expensive half, and nothing in this build calls it
yet.** Story 6.1 serves chat and embeddings because the compose stack is
specified to (AD-5), but the only model traffic the application makes today is
embeddings — the chat client arrives with Stories 6.2/6.3. The default
`qwen3:14b` is sized for a GPU host (~9 GB on disk, and the architecture's
table wants ~24 GB of VRAM to run it well), so on a laptop set both knobs down
in `deploy/.env` before the first `up`:

```sh
CHAT_MODEL=qwen3:8b        # the documented CPU fallback
```

`EMBEDDING_MODEL` is the one knob that is *not* free to change: the columns are
`vector(1024)` and a model of another width is a migration rather than a
setting. `deploy/.env.example` says so at the knob.

A machine with an NVIDIA GPU can add the overlay that reserves it:

```sh
docker compose -f deploy/compose.yaml -f deploy/compose.gpu.yaml up
```

The base stack is CPU-capable on purpose — no GPU is required to run it, and
the overlay above is the only thing that asks for one. "CPU-capable" is about
what the stack *needs*, not about what the defaults cost: a CPU host should
still set `CHAT_MODEL` down, per the note above.

Copy `deploy/.env.example` to `deploy/.env` first if you want to override
defaults (the compose file ships working dev values; compose auto-loads
`.env` from the `deploy/` project directory for `${…}` interpolation).

## Source tree

```text
lineworker/
  web/                     # React 19 SPA (Vite)
    src/features/          #   shell/ (chrome: top bar, shells, route guard)
                           #   login/ queue/ claim-detail/ dashboard/ copilot/ diary/
    src/components/ui/     #   vendored shadcn/ui
    src/api/               #   generated OpenAPI client + queryKeys
  server/
    api/                   # FastAPI app factory, auth deps, routers
    services/              #   claims/ financials/ worklist/ derivations/ audit/ blobstore
                           #   rag/ — sole embeddings client + owner of the
                           #   three embedding tables (Story 6.1)
    rules/                 #   ZEN engine + JDM documents (Story 2.1+)
    agents/                #   LangGraph copilot (arrives Epic 6)
    data/                  #   SQLAlchemy models, repositories, Alembic
  e2e/                     # Playwright story-gate suite (AD-15)
    fixtures/              #   DB reset, persona login, selector policy
    stories/               #   one spec per story, named by sprint story key
  deploy/                  # compose.yaml, compose.gpu.yaml (prod GPU overlay),
                           # compose.e2e.yaml, nginx/, *.env.example
    model-stub/            #   deterministic Ollama-API stand-in, e2e profile
                           #   only — outside server/ so it can never ship
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
npm run generate:api        # regenerate src/api/schema.d.ts from the
                            # server's OpenAPI document (needs uv)
```

`src/api/schema.d.ts` is generated and **committed**; CI regenerates it and
fails on a diff, so an endpoint whose signature changed without the client
being regenerated is caught at build time. Nothing in `web/` may `fetch`
`/api` directly — go through `src/api/client.ts`.

### Signing in

There is no identity provider yet (a Deferred architecture decision, due
before the first non-dev deployment). Until then the login screen lists the
seeded personas and `POST /api/auth/login` mints a server-side session for
the chosen one. The cookie holds an opaque session id and nothing else:
role and employer scope are re-resolved from the database on every request,
which is the AD-7 invariant every later endpoint depends on.

Routes: `/` (login), `/dashboard` (supervisor, analyst), `/workspace`
(handler). Unauthenticated access to either shell redirects to `/`.

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
