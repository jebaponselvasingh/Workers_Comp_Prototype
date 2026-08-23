# Production boot runbook — LINEWORKER

**This document is the answer to "can somebody who has never seen this stack
bring it up?"** Everything else in Epic 8 — the TLS configuration, the audit
posture, the encrypted backups — is configuration that is correct in a file. A
runbook is the only artifact that is proved by *use*, which is why the last
section of this one records a boot that was actually executed rather than a boot
that would presumably work.

It is also a compliance document, in the same sense `RESTORE-DRILL.md` is.
`§ Executed boot` is evidence: recording a boot that was not run, or omitting a
deviation because it was inconvenient, converts "we do not know" into "we were
told we did".

Read it in order the first time. Sections 1 and 2 are prerequisites that cannot
be fixed after the fact — a certificate with the wrong name and an unencrypted
data volume both look exactly like a healthy stack.

---

## 0. What this deploys

Five containers on one Docker host, one published port pair, and nothing else:

| Container | What it is | Published |
|---|---|---|
| `web` | nginx: serves the built SPA, terminates TLS, proxies `/api/` | **443 and 80** — 80 answers `308` to https and nothing else |
| `api` | FastAPI + LangGraph. Runs `alembic upgrade head` at boot, then uvicorn. Hosts the payment batch, the embedding refresh and the audit-retention sweep as in-process scheduled jobs | no |
| `postgres` | PostgreSQL 18 + pgvector + pgaudit. The single database: claims, embeddings, LangGraph checkpoints, `ai_insight`, `audit_event` (AD-3) | no |
| `ollama` | The model server. Local inference only; pulls `CHAT_MODEL` and `EMBEDDING_MODEL` on first boot (AD-5) | no |
| `backup` | The nightly `pg_basebackup` + `pg_dump` + blob tar, `age`-encrypted before anything leaves the host, plus a streaming `pg_receivewal` | no |

**nginx is the sole ingress.** Postgres, Ollama and the backup container are
reachable only on the compose network, and CI fails the build if a `ports:` key
appears under any of them. That is not a hardening preference: an exposed Ollama
is an unauthenticated inference endpoint that will accept a prompt containing
PHI, and an exposed Postgres is the claim book.

Four named volumes hold everything that survives a restart: `pgdata`,
`ollama-models`, `backupdata`, `blobdata`. **Three of the four hold PHI**, which
is what § 2 is about.

**The boot does not wait for the model pull.** `api` depends on `ollama` with
`condition: service_started` (AD-14), so `postgres`, `api` and `web` reach
healthy and the console serves the queue and a claim detail while several
gigabytes download in the background. On a host that cannot reach the model
registry at all, that never completes, `ollama` stays unhealthy, the AI routes
answer `ai_unavailable`, and everything else works. This is deliberate and it is
the behaviour to expect at § 5 rather than a fault to chase.

---

## 1. Prerequisites

### Host

| What | Minimum | Notes |
|---|---|---|
| OS | Linux with a current kernel | The GPU overlay and LUKS both assume it. |
| Docker Engine + Compose v2 | Compose v2.24+ | `!override` on a sequence and `${…:?}` guards are both used. |
| NVIDIA Container Toolkit | required **if** `compose.gpu.yaml` is applied | A host without it does not fall back — the container *create* fails with a device-driver error. See that file's header. |
| GPU | 24 GB class (e.g. RTX 4090) for `qwen3:14b` + `bge-m3`; 48 GB for `qwen3:32b` | From the architecture's model table. VRAM rule of thumb: the Q4_K_M download size is the floor and the KV cache adds ~2–6 GB on top. |
| Disk | 3× the working database, plus the model volume (several GB per model), plus the local backup spool | `BACKUP_MAX_SPOOL_SEGMENTS` bounds the WAL spool at 8 GB by default. |

**No GPU is a supported deployment, and it is a documented deviation rather than
a silent one.** Omit `compose.gpu.yaml` and set `CHAT_MODEL=qwen3:8b` — the CPU
pairing `README.md` documents. It is slow and correct. Do not apply the GPU
overlay on a host without the toolkit "to see what happens": the failure is at
container create, so the stack comes up with four services and no model server.

### Certificate material

Two directories, from the host secret store, mounted read-only. **Nothing
cryptographic belongs in version control**, which is also why CI can render this
profile and can never boot it.

| Variable | Mounted at | Must contain |
|---|---|---|
| `TLS_CERT_DIR` | `/etc/nginx/tls` (web) | `server.crt`, `server.key` for **the deployment's public hostname** |
| `PG_TLS_DIR` | `/etc/postgresql/tls` (postgres) | `server.crt`, `server.key` for **the name `postgres`**, and `ca.crt` |
| `${PG_TLS_DIR}/ca.crt` | `/etc/postgresql/tls/ca.crt` (api, backup) | the single CA file, and only that file |

**The trap, and it is the likeliest first-deployment failure this profile has:
these are two different certificates.** `sslmode=verify-full` checks the
certificate against the host the *URL* names, and every database URL here
connects to `postgres` — the compose service name on the internal network. So
`${PG_TLS_DIR}/server.crt` needs `postgres` in its subjectAltName. Issuing one
certificate for the deployment's hostname and pointing both mounts at it renders
fine, starts fine, and fails at the api's first database connection with the
stack up and the console 500ing. For a self-signed pair:

```sh
openssl req -x509 -newkey rsa:4096 -nodes -days 825 \
    -keyout "$PG_TLS_DIR/server.key" -out "$PG_TLS_DIR/server.crt" \
    -subj "/CN=postgres" -addext "subjectAltName=DNS:postgres"

# `ca.crt` is a THIRD required file, and forgetting it is the second-likeliest
# first-deployment failure. `api` and `backup` mount `${PG_TLS_DIR}/ca.crt` as a
# single file to verify the chain against; a self-signed server certificate is
# its own issuer, so the CA file is a copy of it. With a real CA, put that CA's
# certificate here instead.
cp "$PG_TLS_DIR/server.crt" "$PG_TLS_DIR/ca.crt"
```

**If `ca.crt` is missing, Docker creates an empty *directory* at that path**
rather than failing, and the api starts and cannot verify anything. That is not
hypothetical: § Executed boot records an empty bind mount costing this story's
first `up` attempt, from the same class of mistake. Check before booting —
three files here, two under `TLS_CERT_DIR`:

```sh
ls -l "$PG_TLS_DIR"/{server.crt,server.key,ca.crt} "$TLS_CERT_DIR"/{server.crt,server.key}
```

**Postgres refuses to start if its key is group- or world-readable or is not
owned by the server's user.** On the host, before the first `up`:

```sh
chmod 0600 "$PG_TLS_DIR/server.key"
chown 999  "$PG_TLS_DIR/server.key"     # the postgres uid in the Debian base image
```

The api and the backup container get `ca.crt` as a **single file** and never the
directory. They verify a chain; `server.key` beside it is the database's private
key, and handing it to the container that parses handler input, model output and
uploaded documents would let a foothold there impersonate the database to the
application — which is a strictly worse position than the one `verify-full` was
added to prevent.

### The `age` keypair

Generated with `age-keygen`. **The recipient (public half) goes in the
deployment's `.env`; the identity (private half) must not be on this host** —
that asymmetry is what makes compromising the backup host survivable, and it is
the scenario off-host backups exist for. If the identity is lost, every backup
ever taken is permanently unreadable: no escrow, no support path.
`deploy/.env.example` and `deploy/RESTORE-DRILL.md § 0` carry the full argument.

---

## 2. Volume encryption pre-flight

**NFR-5 requires encryption at rest and compose cannot enforce it.** There is no
setting in any file in this directory that makes `pgdata` encrypted; the volumes
live under the Docker data root and are exactly as encrypted as that filesystem
is. So this is a *pre-flight step* — run it before anything starts, because
`docker compose up` on an unencrypted host produces a stack that is healthy,
correct, compliant-looking, and storing the claim book in the clear.

Three of the four volumes hold PHI (`pgdata`: everything; `backupdata`: the
local copy of the encrypted artifacts *plus* the plaintext WAL spool;
`blobdata`: documents and photos), and Postgres's own log files are inside
`pgdata` by deliberate design (`logging_collector=on`, `log_directory=log`).

Find the Docker data root, find the filesystem under it, and prove that
filesystem sits on a mapped device:

```sh
docker info --format '{{.DockerRootDir}}'          # usually /var/lib/docker
findmnt -no SOURCE,FSTYPE -T "$(docker info --format '{{.DockerRootDir}}')"
lsblk -o NAME,TYPE,MOUNTPOINT,FSTYPE
```

Expected: `findmnt` names a device under `/dev/mapper/…`, and the `lsblk` tree
shows a `crypt` TYPE row between the physical partition and the mount:

```
NAME              TYPE  MOUNTPOINT   FSTYPE
nvme0n1           disk
└─nvme0n1p3       part               crypto_LUKS
  └─cryptdata     crypt /var/lib/docker  ext4
```

Confirm the mapping is really LUKS rather than a name that looks like it:

```sh
sudo cryptsetup status cryptdata      # expect: type LUKS2, cipher aes-xts-plain64
```

**If `findmnt` names a bare partition (`/dev/nvme0n1p3`) and `lsblk` shows no
`crypt` row, stop.** The remedy is not a compose change: provision LUKS under
the Docker data root (or move the data root onto an encrypted filesystem) and
start again. A deployment that proceeds here has an unencrypted claim book and
nothing downstream will ever say so.

Record the output of these three commands in § Executed boot. It is the only
evidence this requirement was met, and "we use encrypted disks" is not evidence.

**On Docker Desktop (macOS or Windows) none of this works, and that is not a
missing tool.** `findmnt`, `lsblk` and `cryptsetup` do not exist there, and
`docker info` reports `/var/lib/docker` — a path inside Docker Desktop's own
Linux VM, not a host path, so even a Linux-shaped check would be asking the
wrong filesystem. What actually protects the volumes is the encryption of the
host disk holding the VM's disk image (FileVault on macOS, BitLocker on
Windows), which `fdesetup status` / `manage-bde -status` reports and which says
nothing about the guest layout. **The supported production target is a Linux
host with a native Docker Engine**, where the commands above mean what they say.
A Docker Desktop host is a development environment; a boot performed on one is a
rehearsal, and § Executed boot records it as such rather than letting a green
`docker compose ps` imply otherwise. The executed boot for this story hit exactly
this and the deviation is recorded below.

---

## 3. Secrets and env

`deploy/.env` is git-ignored and **must never be committed**. Copy
`deploy/.env.example`, replace the live values, and source the secret values from
the host secret store rather than typing them. Every knob is documented at its
own line in the template; what follows is only the set with no default, which
the prod overlay refuses to render without.

| Variable | What it is |
|---|---|
| `POSTGRES_PASSWORD` | The schema owner / bootstrap superuser. Read by Postgres **only when it initialises an empty data directory** — changing it later needs an `ALTER ROLE`, not an edit here. |
| `APP_DB_PASSWORD` | The `lineworker_app` role uvicorn connects as. Rotatable at any time: the api reconciles the role to this value on every boot. |
| `TLS_CERT_DIR` | § 1 — the ingress certificate directory. |
| `PG_TLS_DIR` | § 1 — the database certificate directory, including `ca.crt`. |
| `BACKUP_AGE_RECIPIENT` | The `age1…` public key backups are encrypted to. |
| `BACKUP_REMOTE` | The off-host destination: `user@host:/path` over SSH, or an absolute path on a **separately failing** filesystem. A second directory on the same disk is not off-host. |

Each is guarded with `${…:?}` in `compose.prod.yaml`, so a missing one fails
`docker compose config` with a message naming the variable — before an image is
pulled, which is the earliest point available. The two database passwords
acquired their guards in Story 8.4 for a specific reason: the dev defaults
(`lineworker_dev`, `lineworker_app_dev`) are published in this repository, and
before the guards a prod render that forgot the variable came up *successfully*
on a password anybody could read.

**`ENV` is not in that list and must not be set to `prod`.** The overlay renders
`ENV: dev` deliberately: `api/app.py::create_app` refuses to boot under
`Env.prod` while persona login is the authentication mechanism — anybody who can
reach the API can post a persona id and receive a full session — and that
refusal is Story 1.3's decision, unchanged. Setting it makes the api crash-loop
and `web`, which waits on `service_healthy`, never comes up. The settings whose
defaults would otherwise derive from `ENV` are set explicitly in the overlay
instead (`SESSION_COOKIE_SECURE`, `SCHEDULER_ENABLED`); see § 8, first entry, for
the decision that lifts this.

---

## 4. Boot

From `lineworker/`, with `deploy/.env` in place:

```sh
# With a GPU and the NVIDIA Container Toolkit installed:
docker compose -f deploy/compose.yaml \
               -f deploy/compose.gpu.yaml \
               -f deploy/compose.prod.yaml up -d

# Without a GPU — omit the overlay entirely and use the CPU model pairing
# (CHAT_MODEL=qwen3:8b in deploy/.env). This is a deviation to record, not a
# default to reach for.
docker compose -f deploy/compose.yaml \
               -f deploy/compose.prod.yaml up -d
```

**Render it before you run it.** `config` does every interpolation, applies
every overlay and refuses on any missing guarded variable, without pulling an
image or touching a volume:

```sh
docker compose -f deploy/compose.yaml -f deploy/compose.prod.yaml config >/dev/null
```

Order matters and is not alphabetical: `compose.prod.yaml` last, because compose
merges later files over earlier ones and the prod overlay's `!override` tags,
`restart:` policies and `pg_hba` mount all depend on being the last word.

What happens, in order: `postgres` initialises (first boot only) and reaches
`pg_isready`; `api` runs `alembic upgrade head` as the schema owner, reconciles
the `lineworker_app` role, and starts uvicorn; `web` waits for `api` to be
healthy and then serves. `ollama` starts alongside and pulls its two models,
which nothing waits for. `backup` starts with the stack — in prod the profile
gate is cleared, so no `--profile backup` flag is needed and nobody has to
remember one.

**Do not add `--wait`, and this is the one place in this document where the
obvious flag is the wrong one.** `docker compose up -d --wait` waits for *every*
service and **exits non-zero if any one of them never becomes healthy**. `ollama`
is exactly that service: on a first boot it is unhealthy for the length of the
model pull (275 s in § Executed boot, longer on a slow link), and on a host with
no route to the model registry it stays unhealthy for ever — at which point
`--wait` reports a **failed deployment for a stack that is serving correctly**.
That is the AD-14 scenario this story exists to make survivable, and `--wait`
would hand it back as a boot failure. `deploy/.env.example` already records the
same trap for the backup container.

So: plain `up -d`, then § 5. Use `--wait` only when you want the boot to block
on the model pull and to fail if it cannot complete — a deliberate choice on a
GPU host with a warm model volume, not a default.

---

## 5. Health verification

```sh
docker compose -f deploy/compose.yaml -f deploy/compose.prod.yaml ps
```

Expect `web`, `api`, `postgres` and `backup` **healthy**, and `ollama` healthy
once its pull finishes:

```
NAME                  SERVICE    STATUS
lineworker-api-1      api        Up 2 minutes (healthy)
lineworker-backup-1   backup     Up 2 minutes (healthy)
lineworker-ollama-1   ollama     Up 2 minutes (health: starting)
lineworker-postgres-1 postgres   Up 2 minutes (healthy)
lineworker-web-1      web        Up 2 minutes (healthy)
```

**`ollama` reading `health: starting` is expected on a first boot and the
console is already usable.** The healthcheck means "both models are pulled", not
"the server answered" — that is the AI-availability signal the copilot's
degradation path reads — and since Story 8.4 it is no longer a gate on anything.
Until it goes healthy, the queue, claim detail, dashboard and diary all work and
the AI routes answer `ai_unavailable`. On a host with no route to the model
registry it stays unhealthy for ever and that remains true; `docker compose logs
ollama` says which pull is failing.

The four checks worth running by hand, because a green `ps` proves the
containers started and not that the deployment is correct:

```sh
# 1. The ingress serves a valid chain, and plain HTTP is redirected.
openssl s_client -connect <host>:443 -servername <host> </dev/null
curl -sI http://<host>/ | head -2          # expect 308 and an https:// Location

# 2. The api is healthy *through the proxy*, which is what a browser does.
curl -sk https://<host>/api/healthz        # expect {"status":"ok",…}

# 3. The api's own pooled connections are encrypted — asked of the server,
#    about the sessions the api is holding right now.
docker compose -f deploy/compose.yaml -f deploy/compose.prod.yaml exec -T postgres \
    psql -U lineworker -d lineworker -c \
    "SELECT usename, client_addr, ssl, version FROM pg_stat_ssl \
     JOIN pg_stat_activity USING (pid) \
     WHERE usename IN ('lineworker_app','lineworker') AND client_addr IS NOT NULL"

# 4. And that an unencrypted connection is now REFUSED rather than accepted.
docker compose -f deploy/compose.yaml -f deploy/compose.prod.yaml exec -T postgres \
    psql "postgresql://lineworker_app@postgres:5432/lineworker?sslmode=disable"
```

Check 3: every row must read `t` with TLS 1.2 or 1.3. An `f` means that
connection fell back to plaintext and `DATABASE_URL` is not what you think it
is. Expect three or four rows: two `lineworker_app` (uvicorn's pool and the
copilot's psycopg pool) and `pg_receivewal` connecting as the owner from the
backup container.

**`AND client_addr IS NOT NULL` is load-bearing, and the executed boot is why.**
Without it the query returns its own backend as well — `docker compose exec …
psql` arrives over the container's Unix socket, which reports `ssl = f` because
there is no TLS on a local socket and none is wanted. The check then shows a
plaintext row on a correctly configured deployment, every time it is run, and an
operator either raises a false alarm or learns to ignore an `f`. A check whose
normal output includes the failure it looks for is not a check. Check 4 is Story 8.4's addition and it must **fail**, with

```
FATAL: no pg_hba.conf entry for host "…", user "lineworker_app", no encryption
```

That refusal is the whole point of `postgres/pg_hba.prod.conf`: before it,
`ssl=on` permitted TLS and refused nothing, so the encryption of every
connection rested on a query parameter surviving in an environment variable. A
check 4 that *succeeds* means the prod `pg_hba` is not mounted — confirm with
`docker compose -f deploy/compose.yaml -f deploy/compose.prod.yaml exec postgres
head -1 /etc/postgresql/pg_hba.conf`, which
should name the prod file.

---

## 6. Smoke

Two things, and the second is the one an auditor should ask for.

**The `@smoke` Playwright set — before you deploy, against the e2e profile.**
It is the gate, and it is the gate *there*:

```sh
cd e2e
npm install && npx playwright install chromium
docker compose -f ../deploy/compose.e2e.yaml up -d --build --wait
npm run smoke
```

**Do not point it at a deployment.** `E2E_BASE_URL=https://<host> npm run smoke`
looks like it would work and cannot, which is worse than a command that fails
cleanly — the executed boot for this story ran it to find out. `E2E_BASE_URL`
moves the *browser*; it does not move the **database reset**, and AD-15 requires
one before every spec file. `e2e/fixtures/reset.ts` hardcodes
`deploy/compose.e2e.yaml` and connects as `lineworker_e2e_owner`, a role only
`deploy/e2e-init` creates — so against a running deployment the suite either
fails in setup (`service "postgres" is not running`, which is what happened) or,
if an e2e stack happens to be up, silently drops *that* schema while the browser
drives *this* host and reports green. And on any deployment holding real claim
data the reset would be a `DROP SCHEMA` against production and the traces would
be PHI, which AD-15 prohibits in as many words.

**After the boot, walk the same happy path by hand** — it is what the `@smoke`
set asserts, and a person doing it once is the honest post-deployment check:

```sh
# Sign in through the ingress and read a claim, all over TLS.
curl -sk -c /tmp/c -X POST https://<host>/api/auth/login \
     -H 'content-type: application/json' -d '{"personaId":1}'
curl -sk -b /tmp/c https://<host>/api/stats/topbar        # scoped caseload counts
curl -sk -b /tmp/c https://<host>/api/claims/queue        # grouped by stage
curl -sk -b /tmp/c https://<host>/api/claims/<claimId>    # the assembled case file
```

Then open `https://<host>/` in a browser, log in as a handler persona, and
confirm the queue renders and a claim detail opens. If `ollama` is still pulling,
the copilot pane reports `ai_unavailable` and everything else works — that is
AD-14 behaving correctly, not a failed deployment.

**The backup pipeline, once, on demand**, because a nightly job that has never
run is a hypothesis:

```sh
docker compose -f deploy/compose.yaml -f deploy/compose.prod.yaml \
    run --rm backup once
docker compose -f deploy/compose.yaml -f deploy/compose.prod.yaml \
    exec -T backup cat /var/lib/lineworker/backup/status.json
```

Expect exit 0, `"result": "success"`, and artifact names with byte counts. Then
go to the destination and confirm what arrived is ciphertext — the only place
AD-11's promise is actually testable:

```sh
head -c 64 /srv/lineworker/<runId>/dump.age | strings | head -1
```

`age-encryption.org/v1` is correct. `PGDMP` is a plaintext dump of the claim
book on a machine that was never supposed to hold one, and the run that produced
it is a reportable incident rather than a bug.

`deploy/.env.example`'s verification block lists all four backup checks;
`deploy/RESTORE-DRILL.md` is the fourth and the only one that proves the file is
a backup rather than proving a file was written.

---

## 7. Operating notes

**The api holds schema-owner credentials and now exercises them daily.**
`ALEMBIC_DATABASE_URL` is the owner role. It has been on the api service in
every profile since Story 1.2 for migrations at boot, and Story 8.1 made the
long-lived web process use it on a schedule as well: the daily audit-retention
sweep assumes `audit_redactor` through it. This is deliberate and argued — the
alternative was a fourth login role whose entire purpose is to overwrite the
audit log, sitting in the same env file — but it means a compromise of the api
process reaches the schema owner, not just the app role. It is recorded in
`deferred-work.md` and belongs in whatever threat model this deployment
maintains. Rotating `POSTGRES_PASSWORD` after the first boot is an `ALTER ROLE`
plus an edit to `.env` plus a restart, in that order.

**Three schedules run inside the `api` process** (the worker-container split is
deferred, § 8): the payment batch on `PAYMENT_BATCH_WEEKDAYS`, the embedding
refresh on `EMBEDDING_REFRESH_INTERVAL_SECONDS`, and the audit-retention sweep
daily. `SCHEDULER_ENABLED` is set to `"true"` explicitly in the prod overlay
rather than inherited from `ENV`. Restarting `api` restarts all three; none of
them is idempotency-fragile (a batch with nothing approved pays nothing and
writes no audit rows).

**Reading the database's own log.** `logging_collector=on` deliberately takes it
off container stdout and into `pgdata`, which is the volume § 2 encrypts:

```sh
docker compose -f deploy/compose.yaml -f deploy/compose.prod.yaml exec postgres \
    cat "/var/lib/postgresql/18/docker/log/postgresql-$(date +%a).log"
```

Seven files, one per weekday, truncated when their day comes round. pgaudit
captures `ddl, role` and nothing else — value-level history lives in
`audit_event`, where the Story 8.1 purge cascade can redact it; a line in a log
file cannot be redacted by anything this system owns.

**Nothing reads any of it but a human.** There is no monitoring and no alerting
(§ 8). `docker compose ps` is the binding surface for backup failure, WAL-stream
gaps and container health alike. Somebody has to look.

**Restarts and reboots.** Every service carries `restart: unless-stopped`, so
the stack comes back after a host reboot and a deliberate `docker compose stop`
stays stopped. Take that as read in every procedure below and in
`RESTORE-DRILL.md`: if a container is down and you did not stop it, it exited
repeatedly and the logs say why.

**Upgrades.** Pull, rebuild, `up -d` (not `--wait` — see § 4). `api`'s entrypoint runs
`alembic upgrade head` before uvicorn, so migrations apply on the way up; take a
backup first (`run --rm backup once`), because a migration that fails leaves a
partially-upgraded schema and the dump is the way back.

---

## 8. Deliberately not built

Five decisions this system does not make, each with the trigger that reopens it.
**The fuller registry is the architecture spine's `## Deferred` section**
(`_bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md`),
projected in `docs/Architecture-LINEWORKER.md § 10`; this section names the five
that have operational consequences and links rather than restating, so the two
cannot drift into disagreeing.

### Identity provider — *reopen before the first non-dev deployment*

OIDC versus local credentials. The auth dependency (`api/deps.py::_resolve_user`)
is the swap point. User provisioning and assignment administration ride the same
decision; until it lands, `app_user` and `user_employer_assignment` are
seed-migration data — the demo's nine personas.

**This is the one with a hard operational consequence today.** `POST
/auth/login` mints a session from an unauthenticated persona id, so
`api/app.py:437` *refuses to boot* under `ENV=prod` and says so in as many
words. That is why § 3 says not to set it, why this overlay renders `ENV: dev`,
and why the settings that would derive from it are set explicitly. It also
leaves `/docs`, `/redoc` and `/openapi.json` served, since those are gated on
the same unreachable flag. Resolving this decision is what inverts all of it.

### Binary-store backend / MinIO — *reopen when real files replace placeholders*

Mounted volume versus MinIO, behind the fixed `BlobStore` protocol; only the
protocol is binding. `blobdata` exists, is mounted read-write on `api` and
read-only on `backup`, and is tarred into every backup from birth — but
`document.blob_key` and `photo.blob_key` are NULL on every seeded row and no
code path writes one. The volume gives today's implementation a location; it
does not settle the decision.

### Payment-batch & scheduler mechanism — *reopen when payment volume outgrows in-process jobs*

Batches run inside the `api` process. APScheduler versus a worker container is
open. The `payment_scheduled → paid` transition contract is already fixed, so
the move changes where the job runs and not what it means. Operationally: see
§ 7 — restarting `api` restarts every schedule, and a long batch and a web
request contend for the same process.

### Observability / alerting stack — *reopen when operations ownership is assigned*

Health endpoints and the backup drill are binding; the tool choice
(Prometheus/Grafana versus hosted) is not. **The practical consequence is worth
stating plainly, because it is easy to read the healthchecks as monitoring:**
the backup `status.json`, pgaudit's DDL and privilege log, and the WAL
receiver's state each have **no reader but a human**. A failed nightly run shows
as `unhealthy` in `docker compose ps` and nowhere else; a `wal_dropped` count is
a number in a JSON file; a schema change captured by pgaudit is a line in a log
inside a volume. Nothing pages anybody. Until this decision lands, "somebody
looks at `docker compose ps`" is the alerting design, and it should be on a
calendar.

### Ollama capacity & queueing — *reopen before any multi-user rollout*

A single-user demo assumption. Per-run bounds (`num_predict`, wall-clock
timeout) are binding; concurrent-load behaviour is unaddressed by decision —
parallel chat runs, and chat contending with the embedding refresh on one GPU.
Two handlers asking the copilot at once on a 24 GB card is not a tested
configuration.

**Also deliberately not built, and out of scope here:** Kubernetes / HA Postgres
(reopen if adoption exceeds one site), and client-certificate authentication to
Postgres (`ssl_ca_file` + `clientcert=verify-full`) — see
`postgres/pg_hba.prod.conf`, which argues why it rides the identity-provider
decision rather than being a line in that file.

---

## § Executed boot

**Date:** 2026-08-23 · **Revision:** `fbb94b4` + the Story 8.4 working tree ·
**Executed by:** the Story 8.4 dev run, following this document only.

**Environment — and the deviations, stated first because they bound what this
proves.** An arm64 macOS host running Docker Desktop 29.5.2. Three consequences,
none of them optional:

1. **No NVIDIA runtime, so `compose.gpu.yaml` was omitted.** The boot was
   `-f deploy/compose.yaml -f deploy/compose.prod.yaml`. The GPU overlay renders
   (CI proves that) and has never been booted anywhere. `CHAT_MODEL=qwen3:8b`,
   the CPU pairing § 4 documents.
2. **The § 2 volume-encryption pre-flight could not be run as written**, and the
   note now in § 2 is the result: `findmnt`, `lsblk` and `cryptsetup` do not
   exist on macOS, and `docker info` reported `/var/lib/docker` — a path inside
   Docker Desktop's VM, not on the host. `fdesetup status` reported *FileVault is
   On*, which encrypts the host disk holding the VM image and says nothing about
   the guest layout. **NFR-5's at-rest requirement is therefore not evidenced by
   this boot.** It needs a Linux host with a native engine, which is the target
   § 2 now names explicitly.
3. **A distinct project name (`-p lw-boot`) and fresh volumes.** A twelve-day-old
   `lineworker` dev stack was running on this host, on the pre-8.2 postgres
   image. Reusing it would have proved nothing about a clean host, and
   `down -v`-ing it would have destroyed a developer's state to make a point.

**Certificate material** was generated into `/Users/I5285/lineworker-boot-secrets`
— outside the repository, `0600` on both keys, a throwaway CA, `DNS:localhost`
for the ingress and `DNS:postgres` for the database (the SAN trap § 1 warns
about). Nothing cryptographic was written into VCS; `git status` after the boot
shows no key, no certificate and no `.env`.

### What happened, in order

| Phase | Wall clock | Notes |
|---|---|---|
| `config` render | < 1 s | Refused first with every guarded variable named — the § 3 list is complete and the messages are actionable. |
| First `up -d` attempt | ~15 s | **Failed.** `postgres`: `could not load server certificate file "/etc/postgresql/tls/server.crt": No such file or directory`. |
| Re-boot after the fix | **20 s from `up` to the console serving** | Measured from the `ps` taken immediately after `up` returned, where the `Up N` column reads how long each container had been *running*: `postgres` 19 s, `ollama` 19 s, `api` 13 s, `backup` 13 s, `web` 7 s. They are start times under the dependency chain — postgres first, api six seconds later once postgres was healthy, web last — not durations to add up. |
| `ollama` model pull | **healthy at +275 s** | `health: starting` for four and a half minutes while it pulled `qwen3:8b` (5.2 GB) and `bge-m3` (1.2 GB). **The console was fully usable throughout.** |

**The first failure was environmental and is worth recording**, because the
runbook cannot warn about what it does not know: the certificates had first been
generated under `/private/tmp/...`, a path Docker Desktop does not share, so the
bind mount produced an empty directory and Postgres refused to start. The mount
was correct, the files existed, and the container saw nothing. Moving them under
`/Users` fixed it. On a Linux host with a native engine this cannot happen; on a
Docker Desktop host, "the bind mount is empty" is the first thing to check when a
certificate is reported missing that plainly exists.

Two `docker compose ps` readings were taken, in this order. **At +3 minutes,
while `ollama` was still pulling:**

```
NAME                 SERVICE    STATUS
lw-boot-api-1        api        Up 3 minutes (healthy)
lw-boot-backup-1     backup     Up 3 minutes (healthy)
lw-boot-ollama-1     ollama     Up 3 minutes (health: starting)
lw-boot-postgres-1   postgres   Up 3 minutes (healthy)
lw-boot-web-1        web        Up 3 minutes (healthy)
```

**And at +5 minutes, after the pull finished — this is AC 2's "all containers
healthy from a clean boot" in full:**

```
NAME                 SERVICE    STATUS
lw-boot-api-1        api        Up 5 minutes (healthy)
lw-boot-backup-1     backup     Up 5 minutes (healthy)
lw-boot-ollama-1     ollama     Up 5 minutes (healthy)
lw-boot-postgres-1   postgres   Up 5 minutes (healthy)
lw-boot-web-1        web        Up 5 minutes (healthy)
```

That second `ollama` row also verifies this story's healthcheck change: the probe
is now `ollama list && test -f /tmp/ollama-ready`, so it reaches the container's
own HTTP API with a binary certainly in the image — an endpoint probe rather than
a bare file test — while keeping the marker's meaning. `ollama list` confirmed
both models present.

**The first reading is the story's headline result.** `api` reached healthy at 13
seconds while `ollama` was 22 % into its first pull and stayed `starting` for
minutes. Before Story 8.4 that boot could not have happened: `api` waited on
`ollama: service_healthy`, whose healthcheck requires *both* pulls to have
completed, behind a thirty-minute `start_period` — so the console did not exist
until the model server was fully loaded, and on a host with no route to the
registry it never would. That is the hard dependency AD-14 forbids, and this
table is it being gone.

### The § 5 checks, as run

1. **Ingress.** `openssl s_client` returned `subject=CN = localhost`,
   `issuer=CN = lineworker-boot-ca`, and `Verify return code: 21` — expected, the
   CA is a throwaway not in the host trust store. `curl -sI http://localhost/` →
   `HTTP/1.1 308 Permanent Redirect`.
2. **Health through the proxy.** `curl -sk https://localhost/api/healthz` →
   `{"status":"ok","db":"ok"}`.
3. **The api's connections are encrypted.**

   ```
   lineworker_app|172.18.0.4|t|TLSv1.3
   lineworker_app|172.18.0.4|t|TLSv1.3
   lineworker    |172.18.0.5|t|TLSv1.3   (pg_receivewal, from the backup container)
   ```

   Two `lineworker_app` pools and the backup job's WAL stream, all TLS 1.3.
   **This check was corrected by running it:** as originally written it also
   returned its own `docker compose exec … psql` backend, which arrives over the
   container's Unix socket and reports `ssl = f`. A check that shows a plaintext
   row on a correctly configured deployment every time it runs is a check people
   learn to ignore, so it now filters `client_addr IS NOT NULL` and § 5 says why.
4. **An unencrypted connection is refused.** Exactly as § 5 requires:

   ```
   FATAL:  no pg_hba.conf entry for host "172.18.0.3",
           user "lineworker_app", database "lineworker", no encryption
   ```

   `head -3 /etc/postgresql/pg_hba.conf` named the prod file. This is the
   Story 8.2 residual closed: before `pg_hba.prod.conf`, `ssl=on` *permitted*
   TLS and refused nothing, so encryption rested on a query parameter surviving
   in an environment variable.

### The § 6 checks, as run

**The `@smoke` set was run against the deployment exactly as § 6 then told an
operator to, and it could not work.** It failed in setup with `service
"postgres" is not running`, from `e2e/fixtures/reset.ts` — because
`E2E_BASE_URL` moves the browser and not the AD-15 database reset, which is
hardcoded to `compose.e2e.yaml` and to a role only the e2e profile creates. Had
an e2e stack been up, it would have dropped *that* schema while driving *this*
host and reported green. § 6 has been rewritten: the `@smoke` set is the
pre-deployment gate, run against the e2e profile, and the post-boot check is the
hand walkthrough below. This is the same class of finding as Story 8.3's
blob-restore step, and the same rule applies — a step that has to be worked
around proves the system and hides the runbook.

**The hand walkthrough**, all through the TLS ingress on the booted stack:
`POST /api/auth/login` as persona 1 returned `Kaya Johnson · handler`;
`/api/stats/topbar` returned her scoped caseload (`caseload 45, activeTx 15,
highRisk 16`); `/api/claims/queue` returned the four stage groups (intake 3,
investigation 1, treatment 15, settled 26); `/api/claims/WC-20561` returned the
assembled case file (`benefit`, `documents`, `injury`, `overview`, `photos`,
`reserveCheck`, `stepper`); and `GET /` served the SPA shell. Scoping, the rules
engine, the derivations and the financial blocks all answered from a database
this stack had migrated and seeded from empty ninety seconds earlier.

**The backup pipeline, once, on demand:**

```
backup.run_started   run_id=20260823T075557Z wal_streaming=true
backup.run_succeeded run_id=20260823T075557Z wal_segments=3 runs_local=1 duration_seconds=1
```

`status.json` read `"result": "success"`, `"stage": "done"`, `"exit_code": 0`,
`"wal_dropped": 0`, and nine artifacts with byte counts — `base.tar.gz.age`
(4.6 MB), `dump.age` (168 KB), `globals.sql.age`, `blobs.tar.age`,
`pg_wal.tar.gz.age` and three WAL segments. At the off-host destination
`head -c 21 dump.age` read `age-encryption.org/v1` and no `PGDMP` header
appeared in the first 512 bytes. The WAL stream ran over the `hostssl` link the
prod `pg_hba` now requires, which is the part of 8.3 this profile changed.

**One § 6 command does not work inside the backup container**: `strings` is not
installed in `lineworker/backup:pg18`. It is documented as a command for the
*destination host*, which normally is one — but when `BACKUP_REMOTE` is a local
directory, as it was here, the destination is that container. `head -c 21` and a
`grep -q PGDMP` were used instead and are the more portable spelling.

### Checklist

- [x] Rendered before booting; every guarded variable refused by name when unset
- [x] Certificate material outside VCS, `0600` keys, correct SANs, nothing committed
- [ ] **Volume-encryption pre-flight — NOT satisfied.** Not runnable on this host; see deviation 2
- [x] Clean boot from empty volumes under a distinct project, 20 s to serving
- [x] Every service healthy; `ollama` still pulling and nothing waiting on it (AD-14)
- [x] TLS at the ingress; plain HTTP redirected 308
- [x] Every network connection to Postgres TLS 1.3; an unencrypted one refused
- [x] Login → scoped queue → assembled claim detail → dashboard stats, through the ingress
- [x] One backup run, success, artifacts encrypted before reaching the destination
- [ ] **The `@smoke` set against this deployment — NOT done, and not doable.** The
      story's acceptance criterion asked for it; § 6 above records why it cannot
      work (the AD-15 reset is bound to the e2e profile, and against a deployment
      holding real claim data it would be a `DROP SCHEMA` with PHI in the traces).
      Substituted with the `@smoke` set green against the e2e profile (41 passed)
      plus the hand walkthrough above. Recorded as an amended criterion rather
      than a met one.
- [ ] **GPU overlay — NOT booted.** Renders only; no GPU host exists for this project

**What this boot did not prove:** the GPU overlay, at-rest volume encryption, a
real off-host SSH destination (`BACKUP_REMOTE` was a local directory, as in every
other exercise of this pipeline to date), and a certificate chain a browser
trusts. All four are in `_bmad-output/implementation-artifacts/deferred-work.md`
with the evidence.
