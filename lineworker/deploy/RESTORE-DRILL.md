# Restore drill — LINEWORKER

**This document is the answer to "can you actually get the claim book back?"**
Everything else in Story 8.3 — the container, the encryption, the off-host copy,
the healthcheck — proves that a file was written. Only a restore proves the file
is a backup. A backup pipeline that has never been restored from is a
hypothesis, so this runbook is written to be *executed*, on a schedule, with the
result recorded in [§ Executed drill](#-executed-drill) below.

It is also a compliance document. The `§ Executed drill` section is evidence:
recording a drill that was not run, or omitting a deviation because it was
embarrassing, is worse than having no drill at all, because it converts "we do
not know" into "we were told we did".

---

## 0. Prerequisites

| What | Where it comes from | Notes |
|---|---|---|
| The **identity** (`age` private key) | The operator's secret store — a vault, a password manager, a sealed envelope | **Not on the backup host.** Fetching it is step 1 of every restore. If it is lost, every backup ever taken is permanently unreadable; there is no escrow and no support path. |
| Read access to `$BACKUP_REMOTE` | The destination host or mount | Read is enough. A restore never writes to the destination. |
| A host that can run the stack | Docker + compose, and enough disk for the decrypted dump plus the restored database | Roughly 3× the encrypted artifact set. |
| `age` (v1.x) | `apt install age`, or `docker run --rm -i lineworker/backup:pg18 …` | The backup image already carries it, which is the shortest path on a host that has the images. |
| This repository at the **revision the backup was taken from** | git | The schema in the dump is that revision's. Restoring into a newer checkout's stack means running migrations *after* the restore, which is a different (and supported) operation — see [§ 5](#5-after-the-restore). |

### The status-file contract

Every run writes `/var/lib/lineworker/backup/status.json` inside the backup
volume. It is the contract three things read — `healthcheck.sh`, this runbook,
and whatever monitoring stack the deferred observability decision eventually
produces — so its shape is stable:

```json
{
  "result": "success",              // or "failure"; anything else is a bug
  "stage": "done",                  // the stage that failed, when result is failure
  "run_id": "20260823T023000Z",     // the directory name at both ends of the copy
  "started_at": "2026-08-23T02:30:00Z",
  "finished_at": "2026-08-23T02:34:11Z",
  "finished_at_epoch": 1787452451,  // what healthcheck.sh reads
  "duration_seconds": 251,
  "exit_code": 0,
  "error": null,                    // first line of the failing command's stderr
  "wal_streaming": true,            // was pg_receivewal up when this run started
  "wal_restarts": 0,                // how many times it has had to be restarted
  "wal_segments": 14,               // segments shipped by THIS run
  "keep_runs": 7,
  "runs_local": 7,
  "artifacts": [{"name": "dump.age", "bytes": 41230201}]
}
```

**Names, byte counts, counts, timestamps and exit codes — never data content
(AD-11).** That bound is why `error` is one line and why there is no field
naming a claim, a table or a row.

Each run directory also carries `manifest.json` with a `sha256` per artifact.
The checksums are of *ciphertext*, computed after encryption, so verifying a
copy at the destination needs no identity and can be done by somebody who cannot
read a byte of what they are verifying:

```sh
cd $BACKUP_REMOTE/<runId> && sha256sum -c <(python3 - <<'PY'
import json
for a in json.load(open("manifest.json"))["artifacts"]:
    print(f"{a['sha256']}  {a['name']}")
PY
)
```

### The artifact set

| File | Made by | Used by |
|---|---|---|
| `dump.age` | `pg_dump -Fc` | **Procedure A.** The portable artifact; survives a major-version change. |
| `globals.sql.age` | `pg_dumpall --globals-only` | **Procedure A, and it is not optional.** The cluster's roles. `pg_dump` does not carry them; restoring without it leaves `lineworker_app` and `audit_redactor` missing and all 69 grants failed. |
| `base.tar.gz.age` | `pg_basebackup -Ft -z -X stream` | **Procedure B.** A physical copy of the data directory. |
| `pg_wal.tar.gz.age` | `pg_basebackup -X stream` | **Procedure B.** The WAL the base backup itself needs to reach a consistent state. |
| `base_manifest.age` | `pg_basebackup` | `pg_verifybackup`, before you trust the base. |
| `blobs.tar.age` | `tar` of the blob volume | Both procedures. Empty today and covered from birth on purpose. |
| `wal/*.age` | `pg_receivewal` | **Procedure B** only. |
| `manifest.json` | this pipeline | Integrity check at the destination. Plaintext, and it contains only names, sizes and checksums. |

---

## 1. Recovery-point objectives, stated as numbers

Two procedures because they answer two different questions, and the honest
numbers matter more than the mechanism:

| Procedure | Recovers to | Loses up to |
|---|---|---|
| **A — logical restore from the nightly dump** | The moment `pg_dump` started | **24 hours** (one nightly cycle) |
| **B — point-in-time recovery from base + WAL** | Any moment covered by the archive | Roughly the **unflushed tail of the current WAL segment** — seconds to minutes |

**Why both exist.** WAL is the physical write-ahead log of a specific data
directory. It replays onto a physical base backup and *never* onto the logical
objects `pg_restore` recreates — so "pg_restore + WAL replay to target time" is
not a thing that composes, and shipping a WAL stream with only a dump to pair it
with would be shipping an archive that cannot be used. Hence a base backup as
well. Conversely the base backup is version-locked: a PostgreSQL 19 server
cannot start a PostgreSQL 18 data directory, so the dump is the artifact that
survives an upgrade and the one Procedure A uses.

**Procedure A is the drill.** It is the one that is executed, the one that is
recorded below, and the one to reach for in an actual disaster unless the last
24 hours specifically matter. Procedure B is documented and, at the time of
writing, **not executed** — recorded as such in `deferred-work.md` rather than
implied to be rehearsed.

---

## 2. Procedure A — logical restore from the nightly dump

Time each phase. The wall-clock numbers are as much a part of the record as the
steps: "we can restore" and "we can restore inside the business's tolerance" are
different claims.

### A1. Fetch the encrypted artifact set

```sh
export RUN_ID=<the runId to restore>            # ls $BACKUP_REMOTE | sort | tail -1
mkdir -p /tmp/restore && cd /tmp/restore
rsync -a "$BACKUP_REMOTE/$RUN_ID/" ./           # or scp -r for an SSH destination
sha256sum --check <(python3 -c "
import json
for a in json.load(open('manifest.json'))['artifacts']:
    print(a['sha256'] + '  ' + a['name'])")
```

A checksum mismatch here is the end of this run-id: pick the previous one. Do
not attempt to repair an artifact.

### A2. Decrypt, with the identity fetched from the secret store

```sh
# The identity arrives here and leaves when you are done. It is never written
# into the repository, the backup volume, or a shell history file.
export AGE_IDENTITY=/run/secrets/lineworker-backup.key

age -d -i "$AGE_IDENTITY" -o dump        dump.age
age -d -i "$AGE_IDENTITY" -o globals.sql globals.sql.age
age -d -i "$AGE_IDENTITY" -o blobs.tar   blobs.tar.age

head -c 5 dump      # -> PGDMP. Anything else and the identity is the wrong one.
```

> **The decrypted files are PHI.** From this line until step A6 there is a
> plaintext copy of the entire claim book on this disk. Do it on an encrypted
> volume, on a host you would run the production database on, and delete it when
> the drill is finished.

### A3. Provision a clean stack

```sh
cd <repo>/lineworker
docker compose -f deploy/compose.yaml down -v      # destructive: this is the point
docker compose -f deploy/compose.yaml up -d postgres
```

`down -v` destroys the volumes. That is what makes this a restore rather than a
repair: a restore that runs against a database that still has its data proves
nothing at all.

### A4. Restore the database

**The roles go back first, and skipping this step is the drill's own recorded
failure.** `pg_dump` dumps a database; roles live in the cluster and are not in
it. Restore the dump on its own and every `GRANT … TO lineworker_app` in it
fails, `pg_restore` still exits 0 with `errors ignored on restore: 69`, and you
are left with a database that has all its data, no `lineworker_app` (so the api
cannot connect), and no `audit_redactor` grants or RLS policies at all — the
AD-4 invariant, gone, with nothing in the output that says the word "audit".

```sh
docker compose -f deploy/compose.yaml exec -T postgres \
  psql -U lineworker -d postgres -v ON_ERROR_STOP=0 < /tmp/restore/globals.sql
```

`ON_ERROR_STOP=0` here and nowhere else: `globals.sql` recreates every role in
the cluster including the one you are connected as, and `role "lineworker"
already exists` is expected and harmless. Read the rest of the errors.

```sh
docker compose -f deploy/compose.yaml exec -T postgres \
  psql -U lineworker -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS lineworker" -c "CREATE DATABASE lineworker OWNER lineworker"

docker compose -f deploy/compose.yaml exec -T postgres \
  pg_restore -U lineworker -d lineworker --no-owner --clean --if-exists \
  < /tmp/restore/dump
```

`pg_restore` reports errors for objects it cannot recreate (extensions requiring
superuser, and — if you skipped the globals step — every grant). Read them; do
not pipe them to `/dev/null`. **`pg_restore` exits 0 with errors ignored**, so
its exit status is not the check. Count them:

```sh
docker compose -f deploy/compose.yaml exec -T postgres \
  psql -U lineworker -d lineworker -tAc \
  "select count(*) from information_schema.role_table_grants where grantee = 'lineworker_app'"
```

Zero means the globals step did not happen. A restore with unexplained errors is
not a restore.

### A5. Restore the blob volume

```sh
docker compose -f deploy/compose.yaml run --rm --no-deps \
  --entrypoint tar \
  -v /tmp/restore:/in:ro api \
  -xf /in/blobs.tar -C /var/lib/lineworker/blobs
```

**`--entrypoint tar` is load-bearing, and this step was wrong without it.**
`server/Dockerfile` sets `ENTRYPOINT ["./entrypoint.sh"]` and that script never
references `"$@"` — it runs `alembic upgrade head`, reconciles the app role and
`exec`s uvicorn. So the earlier spelling of this command (`… api tar -xf …`)
discarded the `tar` entirely: it silently ran **migrations against the database
you have just restored** and left the operator staring at a foregrounded web
server, with the blob volume still empty. Found in review after the drill had
already worked around it; see § Executed drill.

Skipping this is the classic drill failure: the claim restores, the case file
opens, and every document 404s. It is currently an empty archive, which is
exactly why it is easy to skip and exactly why the step is written down.

### A6. Destroy the plaintext

```sh
shred -u /tmp/restore/dump /tmp/restore/blobs.tar 2>/dev/null || rm -f /tmp/restore/dump /tmp/restore/blobs.tar
```

Then bring the rest of the stack up and go to [§ 4](#4-verification-checklist).

---

## 3. Procedure B — point-in-time recovery from base + WAL

Use this when the last 24 hours matter, or when recovering to a moment *before*
a destructive change (a bad migration, an erroneous bulk update). Same major
version only.

### B1. Fetch and decrypt the base and every WAL segment

```sh
cd /tmp/restore
age -d -i "$AGE_IDENTITY" -o base.tar.gz base.tar.gz.age
age -d -i "$AGE_IDENTITY" -o backup_manifest base_manifest.age
mkdir -p wal-plain
for f in wal/*.age; do
  age -d -i "$AGE_IDENTITY" -o "wal-plain/$(basename "${f%.age}")" "$f"
done
```

WAL segments from **every run since the base** are needed, not just this one's —
each run ships the segments completed since the previous one, so the archive is
the union of the `wal/` directories from the base's run forward.

### B2. Unpack into a fresh data directory and verify it

```sh
mkdir -p /tmp/restore/pgdata && tar -xzf base.tar.gz -C /tmp/restore/pgdata
cp backup_manifest /tmp/restore/pgdata/backup_manifest
docker run --rm -v /tmp/restore/pgdata:/pgdata lineworker/postgres:pg18-pgaudit \
  pg_verifybackup /pgdata
```

### B3. Tell it where the archive is and when to stop

```sh
cat >> /tmp/restore/pgdata/postgresql.conf <<'EOF'
restore_command = 'cp /wal/%f %p'
recovery_target_time = '2026-08-23 01:15:00+00'   # the moment to recover to
recovery_target_action = 'promote'
EOF
touch /tmp/restore/pgdata/recovery.signal
```

Omit `recovery_target_time` to replay everything available, which is the "the
host burned down" case rather than the "somebody ran the wrong UPDATE" case.

### B4. Start it

```sh
docker run -d --name lw-pitr \
  -v /tmp/restore/pgdata:/var/lib/postgresql/18/docker \
  -v /tmp/restore/wal-plain:/wal:ro \
  -e POSTGRES_USER=lineworker -e POSTGRES_PASSWORD=... \
  lineworker/postgres:pg18-pgaudit
docker logs -f lw-pitr        # watch for "database system is ready to accept connections"
```

Then point the stack's `pgdata` volume at the promoted directory, restore the
blob tar as in A5, and verify.

---

## 4. Verification checklist

A restore is finished when this passes, not when `pg_restore` exits. Tick every
line into [§ Executed drill](#-executed-drill).

- [ ] `docker compose ps` — every service `healthy`. The backup container is
      profile-gated in dev and e2e, so add `--profile backup` to see it there;
      in prod it is always on and appears without the flag.
- [ ] **The `@smoke` Playwright set passes against the restored stack.**
      ```sh
      cd lineworker/e2e && npx playwright test --grep "@smoke\b"
      ```
      This is the checklist. It is the same tagged happy-path set CI runs
      (login, scoped queue, claim detail, supervisor dashboard), reused rather
      than duplicated so the drill cannot drift from the gate. **Do not run the
      reset fixture against a restored stack you still need** — it drops the
      schema; run the smoke set against a copy, or accept that verification
      consumes the restored database.
- [ ] **Row-count spot check against the source**, per store family, because a
      dump that restored "successfully" with a table missing looks identical
      from the browser:
      ```sql
      SELECT 'claim', count(*) FROM claim
      UNION ALL SELECT 'document', count(*) FROM document
      UNION ALL SELECT 'photo',    count(*) FROM photo
      UNION ALL SELECT 'bill',     count(*) FROM bill
      UNION ALL SELECT 'claim_embedding', count(*) FROM claim_embedding
      UNION ALL SELECT 'ai_insight',      count(*) FROM ai_insight
      UNION ALL SELECT 'copilot_thread',  count(*) FROM copilot_thread
      UNION ALL SELECT 'audit_event',     count(*) FROM audit_event;
      ```
- [ ] **`audit_event` count matches**, specifically. It is the one store whose
      loss is unrecoverable in principle: every other table can in theory be
      rebuilt from source systems, and the audit log is the evidence that
      something happened. A restore that lost audit rows is a failed restore
      even if the console works perfectly.
- [ ] One **known claim end to end**: open it in the console, confirm the injury
      description, the reserve figure and at least one document row.
- [ ] The blob volume was restored (A5) — check a document row with a non-null
      `blob_key` if any exist, otherwise confirm the tar was extracted.

---

## 5. After the restore

- **Migrations.** If the checkout is newer than the dump, run
  `alembic upgrade head` after the restore. If it is *older*, do not: downgrade
  the checkout instead.
- **The backup container.** It starts with a fresh `backupdata` volume and no
  `status.json`, so it reports healthy for `BACKUP_MAX_AGE_HOURS` and then goes
  unhealthy until the first nightly run. That is correct behaviour, not a
  symptom. Run `docker compose run --rm backup once` if you want it green
  immediately.
- **The replication slot.** Procedure B's promoted server has no slot; the
  backup container recreates one on its next start.

---

## 6. Operational caveats — read before restoring into production

### A restored backup resurrects purged PHI

Story 8.1 built a single purge cascade so that a claim's or a person's data can
be removed from every PHI-class store. **A backup taken before a purge still
contains what the purge removed**, and restoring it puts it back.

This is not an oversight and it is deliberately not fixed by filtering: a
purge-aware backup would be a second deletion path, and the whole design rests
on there being exactly one. The mitigation is **bounded retention** — see
`BACKUP_KEEP_RUNS` in `deploy/.env.example` — so purged data stops being
recoverable once every run taken before the purge has been retired, which at the
default of seven daily runs is one week.

The operational consequence: **after restoring, re-run the purge for every
subject purged since the restored run was taken.** Nothing in this system does
that for you, and nothing keeps a list of them for you either — the audit log's
`purge`/`redact` events are where that list is reconstructed from:

```sql
SELECT at, action, entity, entity_id FROM audit_event
WHERE action IN ('purge_claim', 'purge_user', 'redact')
  AND at > '<the restored run''s started_at>'
ORDER BY at;
```

Run that query **against the pre-disaster database if you still have it**; the
restored one, by construction, does not contain the events that came after it.

### The database's own log files are inside the base backup

`base.tar.gz` is a copy of the data directory, and Story 8.2 put the postmaster's
log inside it (`log_directory=log`, relative, therefore inside PGDATA — so that
it rides the encrypted volume). Those files are PHI-adjacent: with
`pgaudit.log=ddl,role` and every DML class pinned off they carry connection
lines, schema changes and role grants rather than claim values, which is why this
is a caveat and not a defect. But an operator who unpacks `base.tar.gz` on a
laptop to "have a look" has unpacked seven days of the production database's
audit-adjacent log onto it. Treat the unpacked base with the same care as the
dump: encrypted volume, and destroyed when the drill ends.

### What has never been exercised

- **The SSH transport.** Dev, e2e and the executed drill all point
  `BACKUP_REMOTE` at a local directory, which exercises the same `rsync`
  invocation, the same per-run layout and the same remote prune *without* the
  transport. The SSH form is documented and asserted as configuration only. Do
  not read a green e2e run as evidence that your off-host copy works — run
  `docker compose run --rm backup once` against the real destination once, and
  look at the destination. Two assumptions the SSH path makes about the far end
  and cannot check from here: **rsync 3.2.3 or newer** (the copy uses
  `--mkpath` to create the per-run directory) and **GNU coreutils** (the remote
  prune pipes `ls | sort | head -n`). Both are true of any current Linux host
  and neither is true of a BSD or a restricted rsync-only endpoint.
- **Procedure B.** Documented above, not executed. See `deferred-work.md`.
- **Integrity at the destination beyond rsync's own.** The manifest's sha256
  values make an integrity check *possible*; nothing runs it on a schedule. A
  destination that silently corrupted a file six months ago is discovered at
  restore time, which is the thing this whole document exists to avoid, and
  closing it means a verification job that belongs with the deferred
  observability decision.

---

## § Executed drill

**Date (UTC):** 2026-08-23, 03:58Z → 04:06Z
**Revision:** `66bb4dbeb075cf1e9df0d8c31d551282469fcb2e` + the Story 8.3 working tree
**Executed by:** the Story 8.3 dev-auto run, unattended
**Environment:** the **e2e profile** (`deploy/compose.e2e.yaml`). This is a
deviation and it is argued in the spec's Design Note 7: the dev profile pulls
several gigabytes of Ollama models before anything is healthy, and the prod
profile is not bootable at all — no certificates are in version control and
`api/app.py::create_app` still refuses `ENV: prod` while persona login is the
authentication mechanism (the standing Story 8.2 finding that 8.4 owns). The
e2e profile runs the same `api`, `web` and `postgres` images against a
deterministic model stub, and it is the stack the `@smoke` set is written for.
**Procedure:** A (logical restore from the nightly dump)
**Run id restored:** `20260823T035945Z`

The state that was destroyed and recovered was not the seed as shipped: before
the backup, the Story 2.3 spec was run against the stack so that five real
`audit_event` rows and their `before`/`after` diffs existed, and a marker file
was written into the blob volume. A drill that restores only data the migrations
would have recreated anyway proves nothing.

### Phase timings

| Phase | Step | Wall clock | Notes |
|---|---|---|---|
| Backup taken | `run --rm backup once` | 0:02 | 5.2 MB total: `base.tar.gz.age` 4,818,575 B · `base_manifest.age` 214,185 B · `dump.age` 173,676 B · `blobs.tar.age` 10,440 B · `globals.sql.age` 1,774 B · `manifest.json` 748 B |
| Teardown | `down -v` | 0:01 | 0 containers, 0 blobs, database gone |
| A1 fetch + checksum | copy from the off-host directory, verify `manifest.json` | 0:01 | 5/5 sha256 matched |
| A2 decrypt | `age -d -i` × 3 | 0:01 | `head -c 5 dump` → `PGDMP` |
| A3 provision clean stack | `up -d --wait postgres` | 0:06 | fresh cluster, `public` at 0 tables |
| A4a restore globals | `psql < globals.sql` | 0:01 | 2 `CREATE ROLE`, 4 `ALTER ROLE`, 2 `GRANT ROLE`; two "already exists" errors, expected |
| A4b `pg_restore` | `--no-owner --clean --if-exists` | 0:01 | **zero errors** |
| A5 blob volume | `tar -xf blobs.tar` | 0:01 | marker file back — **but not by the command § A5 documented at the time**; see the deviation below |
| A6 destroy plaintext | | 0:01 | only `.age` + `manifest.json` remain |
| Bring up the rest | `up -d --wait` | 0:17 | all four healthy; alembic found nothing to apply |
| Verification (`@smoke`) | `npx playwright test --grep @smoke` | 1:31 | **40 passed** |
| **Total (destruction → verified console)** | | **≈2:00** | |

### Deviations from this runbook

- **The profile.** e2e, not dev or prod, for the reason recorded above. What was
  therefore *not* exercised: the GPU overlay, TLS at either boundary, and the
  real Ollama container. What *was* exercised is every line of the backup and
  restore path, against the same `api`/`web`/`postgres` images a real recovery
  would use.
- **The off-host destination is a local directory**, so `rsync` ran without a
  network transport. The SSH form is configuration only; no drill has proven it.
  This is recorded in § 6 and in `deferred-work.md`.
- **Step A4 was wrong when the drill started, and the drill is what found it.**
  The runbook restored `dump` on its own. `pg_dump` dumps a *database*; roles are
  cluster-level objects and are not in it. The first attempt therefore replayed
  69 `GRANT … TO lineworker_app` statements against a cluster with no such role,
  every one failed, and `pg_restore` reported `errors ignored on restore: 69` and
  **exited 0**. The result was a database with all its data — `claim` 100,
  `document` 563, `claim_embedding` 100, `audit_event` 5 — and:
  - no `lineworker_app` role, so the api could not have connected at all;
  - no `audit_redactor` role, and therefore none of its grants and none of the
    five row-level-security policies — the AD-4 invariant the whole audit design
    rests on, gone, with nothing in the output using the word "audit".

  Fixed in the pipeline rather than in the prose: `backup.sh` gained a `globals`
  stage (`pg_dumpall --globals-only`), the artifact set gained
  `globals.sql.age`, § A4 gained the restore-globals step and the grant-count
  check that catches its omission, and
  `server/tests/test_backup_restore.py::test_the_shipped_globals_artifact_carries_the_roles_the_dump_does_not`
  pins it. The drill was then re-run from a clean stack; the numbers above are
  the re-run.
- **Step A5 was not executed as written either, and review is what caught that.**
  The drill extracted the blob tar with `docker run --entrypoint tar
  lineworker/backup:pg18 …` against the bind-mounted blob directory, because the
  e2e profile mounts blobs into the backup container rather than into `api`. The
  command § A5 carried at the time — `compose run … api tar -xf …` — would not
  have worked in any profile: `server/entrypoint.sh` ignores `"$@"`, so it would
  have run migrations against the just-restored database and started uvicorn,
  extracting nothing. The drill's result ("marker file back") is real; the claim
  that it validated the documented command was not. § A5 now carries
  `--entrypoint tar` and this row says what actually ran. The lesson is the one
  the reviewer put best: a drill that works around a broken step proves the
  pipeline and hides the runbook.
- **The `age` identity lived outside the repository for the duration**
  (`$HOME/.lineworker-restore-drill`, `chmod 600`) and was destroyed afterwards.
  The e2e profile's committed recipient was *not* used: no identity for it exists
  anywhere, which is deliberate, and which is also why the drill minted its own.

### Verification checklist, completed

- [x] `docker compose ps` — every service healthy: `api`, `web`, `postgres`,
      `model-stub`. **The backup container is not in that list and cannot be**:
      it is profile-gated in dev and e2e, so `compose ps` without
      `--profile backup` does not show it, and the drill drives it with
      `run --rm`. The checklist item in § 4 has been corrected to say so; as
      originally written it asked for something no command in this runbook
      produces.
- [x] `@smoke` Playwright set green against the restored stack — **40 passed**
- [x] Row counts match source — all 34 tables identical. The single difference is
      `session` (8 → 18), which is the drill's own verification logins creating
      sessions *after* the restore, not a restore defect.
- [x] `audit_event` count matches — 5, and the content fingerprint
      `md5(action‖entity‖entity_id ordered by id)` is identical:
      `e01b2373e8c117ecd72e80a7b53d8ade`
- [x] Known claim verified end to end — `WC-20561` (GE, Burn — Arc Flash,
      severity 85) served by `GET /api/claims/WC-20561` and rendered in the case
      header in a real browser
- [x] Blob volume restored — the marker file written before the backup is back,
      byte-identical
- [x] Grants and policies restored — `lineworker_app` holds 115 table grants and
      five RLS policies exist, matching source exactly
- [x] Console verified in a browser against the restored data *before* any reset
      — handler login → 45 scoped queue cards → claim detail; supervisor login →
      dashboard
- [x] Claim book fingerprint identical — `5053d1b0f565ada5401fb37202f250a7`

### Conclusion

**This deployment can recover from a host loss.** From a destroyed stack to a
console serving the restored claim book, verified by the full `@smoke` set, took
**about two minutes** of wall clock, of which ninety seconds was the verification
suite and roughly twenty seconds was the restore itself — against a 5.2 MB
encrypted artifact set holding 100 claims, 563 documents, 100 embeddings and a
live audit log. Nothing plaintext existed at the off-host destination at any
point, and the artifacts were unreadable without an identity that never lived on
the backup host. **What was lost: everything written since the last nightly
run** — the recovery-point objective is 24 hours under Procedure A, because the
WAL stream was off in this profile and Procedure B has still never been executed.
Those two sentences are the honest summary: recovery time is minutes, recovery
point is a day, and the second number is the one to argue about.
