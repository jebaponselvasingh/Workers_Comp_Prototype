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

A **production** stack adds a second overlay on top of that one, and needs two
host directories of TLS material (Story 8.2 — nothing cryptographic is in this
repository):

```sh
TLS_CERT_DIR=/etc/lineworker/tls PG_TLS_DIR=/etc/lineworker/pg-tls \
  docker compose -f deploy/compose.yaml \
                 -f deploy/compose.gpu.yaml \
                 -f deploy/compose.prod.yaml up -d
```

`compose.prod.yaml` terminates TLS at nginx (443, with 80 redirecting), points
both database URLs at `sslmode=verify-full`, and turns on `ssl` at Postgres.
`deploy/.env.example` documents what goes in each directory and the two commands
that verify a live deployment. The GPU overlay stays exactly one service wide,
which is why TLS is a separate file rather than more of it.

**That profile is not bootable yet, and the command above is written down for
the deployment it is being built towards rather than for today.** `api/app.py`
refuses to start under `ENV=prod` while persona selection is the authentication
mechanism — a Story 1.3 decision that stands until the Deferred IdP choice
lands — so the overlay deliberately does not set `ENV`, and an api started from
it is running a dev-flagged process behind a TLS ingress. What Story 8.2
delivers here is the TLS *configuration*, verified by
`server/tests/test_deploy_tls_posture.py` and by CI rendering the merged
document; finishing the profile (health endpoints, the `ENV` question, the
encrypted-volume documentation) is Story 8.4's.

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
                           # compose.prod.yaml (prod TLS overlay),
                           # compose.e2e.yaml, nginx/, *.env.example
    postgres/              #   pgvector + pgaudit image (Story 8.2)
    nginx/                 #   default.conf (dev/e2e, plain) and tls.conf (prod)
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
uv run python -m scripts.lint_log_phi   # AD-11 app-log PHI ban
```

`lint_log_phi` reads every `log.…()` call in the tree and holds its keyword
names to `logging_config.LOG_KEY_ALLOWLIST` — operational logs carry
identifiers and event names, never a claim field value, a prompt body or a
model's answer. It runs in CI beside ruff and mypy.

**The DB-backed tests need the built pgaudit image, not the stock one.** Since
Story 8.2 the migrations include `CREATE EXTENSION pgaudit`, which fails
(deliberately, and loudly) against a Postgres that has not preloaded the
library:

```sh
docker build -t lineworker/postgres:pg18-pgaudit deploy/postgres
docker run -d --name lw-test-pg -e POSTGRES_USER=lineworker \
  -e POSTGRES_PASSWORD=test -e POSTGRES_DB=lineworker -p 55432:5432 \
  -v "$PWD/deploy/postgres/pg_hba.conf:/etc/postgresql/pg_hba.conf:ro" \
  lineworker/postgres:pg18-pgaudit postgres \
  -c shared_preload_libraries=pgaudit -c pgaudit.log=ddl,role \
  -c pgaudit.log_catalog=off -c pgaudit.log_parameter=off \
  -c pgaudit.log_relation=off -c pgaudit.log_statement_once=on \
  -c log_statement=none -c log_duration=off \
  -c log_min_duration_statement=-1 -c log_min_error_statement=panic \
  -c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0 \
  -c logging_collector=on -c log_destination=stderr -c log_directory=log \
  -c log_filename=postgresql-%a.log -c log_rotation_age=1d \
  -c log_rotation_size=0 -c log_truncate_on_rotation=on \
  -c max_slot_wal_keep_size=2GB -c hba_file=/etc/postgresql/pg_hba.conf
cd server && MIGRATION_TEST_DATABASE_URL=\
postgresql://lineworker:test@localhost:55432/lineworker uv run pytest
```

The `pg_hba.conf` mount and the last two flags arrived with Story 8.3:
`tests/test_backup_restore.py` runs the shipped backup image against this
database, and `pg_basebackup` opens a physical replication connection that the
official image's `host all all all` record does not match. Without the mount
that module fails with "no pg_hba.conf entry for replication connection" — a
database that is not the deployment's, rather than a bug in the test.

**The whole flag list, not an abbreviation of it.** Two of these are not at the
value PostgreSQL ships — `pgaudit.log_catalog` defaults to `on` and
`log_parameter_max_length` to `-1` — so a database started from a shortened
recipe fails `tests/test_pgaudit_posture.py` with `assert 'on' == 'off'`, which
reads as a broken build and is really a broken command line. The argument for
each flag is in `deploy/compose.yaml` above the postgres service;
`server/tests/conftest.py` carries the same `docker run` line, and
`test_the_documented_recipe_would_actually_pass_this_suite` holds the copy in
the test module's failure messages to the settings that module asserts.

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

`--build` matters more than it used to: the e2e profile's postgres is the
locally built `lineworker/postgres:pg18-pgaudit` and runs with the same
hardening flags as dev and prod, so the gate exercises the hardened
configuration rather than a stock database that happens to pass.

Every story ships `e2e/stories/<story-key>.spec.ts`; a story is not done until
its spec passes against the freshly reset e2e stack.

## Backups and restore

The `backup` container (`deploy/backup/`) owns the whole disaster-recovery
pipeline: a nightly `pg_basebackup` + `pg_dump` + a tar of the blob volume, each
encrypted with `age` **before** anything is copied off-host, plus a
continuously-streaming `pg_receivewal`. One dump covers every PHI-class store —
relational, embeddings, checkpoints, insight cache, audit log — because there is
one database (AD-3).

It is **profile-gated in dev and e2e** so a clean checkout still comes up with
nothing configured, and always-on in prod:

```sh
docker compose --profile backup -f deploy/compose.yaml up -d   # scheduler
docker compose -f deploy/compose.yaml run --rm backup once     # one run, now
```

Health is derived from the container's own last run — unhealthy when it failed,
or when there has been no success within `BACKUP_MAX_AGE_HOURS` of the later of
(last success, container start):

```sh
docker compose -f deploy/compose.yaml ps                       # healthy / unhealthy
docker compose -f deploy/compose.yaml exec -T backup \
    cat /var/lib/lineworker/backup/status.json
```

Two variables have no default and the prod overlay refuses to render without
them: `BACKUP_AGE_RECIPIENT` (the `age` public key — **the identity that
decrypts it must not be on this host**) and `BACKUP_REMOTE`. Both are documented
in `deploy/.env.example`.

**`deploy/RESTORE-DRILL.md` is the runbook**, and it records a drill that was
actually executed. Read § 6 before restoring into production: a restored backup
resurrects PHI that the Story 8.1 purge cascade removed, and the mitigation is
bounded retention plus re-running the purge — never purge-aware filtering.

## Documentation

Planning artifacts and the architecture live outside this directory:

- Architecture: `../docs/Architecture-LINEWORKER.md` (rendered) and
  `../_bmad-output/planning-artifacts/architecture/` (spine — binding ADs)
- BRD & prototype (design reference only): `../docs/`
