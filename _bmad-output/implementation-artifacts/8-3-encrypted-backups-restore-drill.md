# Story 8.3: Encrypted Backups & Restore Drill

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims organization,
I want provable disaster recovery,
so that a host loss never means claim-data loss.

## Acceptance Criteria

1. **Given** the backup job, **when** it runs nightly, **then** it produces a `pg_dump`/WAL archive encrypted before leaving the host and copies it off-host (AD-11, NFR-8).
2. **Given** the documented restore drill, **when** executed against a clean environment, **then** the restored system passes the smoke suite (login, scoped queue, claim detail, dashboard), and the drill document records steps, duration, and verification checklist.
3. **Given** a backup failure, **when** a nightly run fails, **then** the failure surfaces non-silently (health/status output), not discovered at restore time.

## Tasks / Subtasks

- [ ] Task 1: Backup job container (AC: 1)
  - [ ] Add the `backup` service to the compose stack (`deploy/`) per the spine's deployment view (the `bak` container Story 1.1 deliberately left out) — internal network only, reaches `postgres` directly, publishes nothing
  - [ ] Backup script: nightly `pg_dump` (custom format, all schemas — relational + vector + checkpoints + audit ride one dump per AD-3) plus WAL archiving (`archive_command` on postgres writing into the backup volume, or `pg_receivewal` from the backup container — pick one, document the choice and the resulting recovery-point objective)
  - [ ] **Encrypt before leaving the host:** encrypt the dump/WAL artifacts on the host with `age` (or GPG — document the choice) using a public key from the host secret store; the plaintext artifact never touches the off-host destination, and the private (decryption) key is never stored with the backups
  - [ ] Copy off-host: transfer the encrypted artifacts to the configured off-host destination (rsync/scp/rclone target from env config); destination, credentials, and retention count are env-driven via `deploy/*.env.example` — no secrets in VCS
  - [ ] Prune: bounded local + off-host retention (keep-last-N, env-driven) so backup storage cannot grow unbounded
- [ ] Task 2: Nightly scheduling (AC: 1)
  - [ ] Schedule inside the backup container (cron/supercronic — the container is its own scheduler; the deferred "scheduler mechanism" decision concerns the api-process jobs, not this container, which the spine's deployment view names explicitly)
  - [ ] Support a manual one-shot invocation (`docker compose run backup once`) for drills and the e2e/integration tests
- [ ] Task 3: Failure surfacing (AC: 3)
  - [ ] Each run writes a machine-readable status file (last run timestamp, success/failure, artifact name, size, off-host copy result) into the backup volume
  - [ ] Container healthcheck reads that status: unhealthy when the last run failed **or** when no successful run exists within the configured window (e.g. > 26h) — so a silently-not-running cron also surfaces; visible in `docker compose ps` alongside every other container's health (NFR-8 pattern)
  - [ ] Run summary logged via structured logging — artifact names, sizes, durations, exit codes only; never data content (AD-11)
- [ ] Task 4: Restore drill — documented AND executed (AC: 2)
  - [ ] Write the runbook in `deploy/` (e.g. `deploy/RESTORE-DRILL.md`): fetch encrypted artifact from off-host → decrypt with the private key from the secret store → provision a clean compose environment → restore (`pg_restore` + WAL replay to target time if WAL mode chosen) → start the stack → run the verification checklist
  - [ ] Verification checklist = the smoke suite: run the Playwright `@smoke` set against the restored stack (login as a persona, scoped queue renders, claim detail opens, supervisor dashboard loads) plus a row-count/audit-event spot check against the source
  - [ ] **Execute the drill** against a clean environment as part of this story's definition of done; record actual steps taken, wall-clock duration, deviations found, and the completed checklist in the drill document — an unexecuted runbook does not satisfy AC 2
- [ ] Task 5: Automated backup/restore tests (AC: 1, 3)
  - [ ] Integration test (CI-runnable): run the backup script one-shot against a seeded DB, assert the artifact is encrypted (magic-byte/`age`-header check — must **not** begin with `PGDMP` plaintext), restore it into a scratch DB, assert seeded row counts and a known claim survive round-trip
  - [ ] Failure-path test: point the off-host copy at an unreachable destination, run once, assert the status file records failure and the healthcheck reports unhealthy
- [ ] Task 6: E2E story spec (AC: 1, 2)
  - [ ] `e2e/stories/8-3-encrypted-backups-restore-drill.spec.ts` tagged `@story:8-3 @epic:8`, `@smoke` on the happy path — see Testing requirements for the ops-only design and reasoning

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story makes disaster recovery **provable**: an encrypted, off-host, nightly backup pipeline plus a restore drill that was actually run, not just written. It closes the backup/restore half of NFR-8. Not in this story: health endpoints on the other containers and the prod compose finalization (8.4 — this story adds only the backup container and its healthcheck), pgaudit/TLS (8.2), the purge cascade (8.1 — note purge and backup interact: a restored backup may resurrect purged PHI; record this as a documented operational caveat in the runbook with the mitigation being backup retention limits, do NOT build purge-aware backup filtering), monitoring/alerting integration (deferred observability decision — the healthcheck/status file is the binding surface), and K8s/HA Postgres (deferred).

### Architecture compliance (binding ADs for this story)

- **AD-11:** backups are PHI-class — encrypted **before** leaving the host; the backup volume rides the encrypted host volume; backup job logs carry names/sizes/IDs only, never data content.
- **AD-3 one PostgreSQL:** exactly one backup stream covers everything — relational entities, embeddings, checkpoints, `ai_insight`, audit log — which is the load-bearing rationale of the single-database decision; do not add per-store backup paths. Blob-volume binaries must also be covered: include the `BlobStore` volume in the same encrypted backup artifact set (a claim restore without its documents/photos fails the drill).
- **Conventions (Operations row):** nightly `pg_dump`/WAL archive, encrypted, copied off-host, restore drill documented; every container exposes health — the backup container included, from birth.
- **Conventions (Config & secrets):** destination, credentials, retention, and key material env-driven / host secret store; nothing in VCS.
- **NFR-8:** this story is its backup/restore clause; 8.4 completes the remainder.

### Data notes

- **No new tables, no schema changes, no DB roles.** New compose service: `backup` (+ its status/artifact volume). The backup DB connection uses a read-only replication/dump-capable role if one is introduced — otherwise the standard superless dump role; whichever is chosen, it gains **no** write grants (AD-4 posture untouched). **Write-owner note (AD-12):** the backup job writes no entities; it reads the whole instance and the blob volume.

### Testing requirements

- Integration round-trip test (backup → encrypted-artifact assertion → restore → data assertions) runnable in CI.
- Failure-path test proving AC 3's non-silent surfacing (status file + unhealthy healthcheck).
- Executed restore drill with the completed, recorded checklist in `deploy/RESTORE-DRILL.md` — part of this story's definition of done, reviewed like code.
- **AD-15 Playwright spec:** `e2e/stories/8-3-encrypted-backups-restore-drill.spec.ts` tagged `@story:8-3 @epic:8`. This story is ops-only with no UI surface, so the spec is structural (reasoning: the full restore drill is a host-level runbook that cannot run inside the browser gate; what *is* gate-checkable is that a backup run occurs against the real stack, produces an encrypted artifact, and leaves the console fully operational). Design: after the standard DB reset and a browser login proving the console is up, trigger a one-shot backup run against the e2e stack (compose run from the spec's setup, the same host-side mechanism the reset fixture uses — add the backup service to `deploy/compose.e2e.yaml` with off-host copy pointed at a local scratch directory), then assert the status file reports success, the artifact fails a plaintext `PGDMP` check (encrypted), and the browser still drives login → queue → claim detail normally. Tag that happy path `@smoke`. The restored-stack smoke verification lives in the drill (Task 4), which reuses the existing `@smoke` set rather than duplicating specs. The story cannot move to `review`/`done` until this spec passes against the freshly reset stack.

### Project Structure Notes

- Backup script, Dockerfile (if a custom image is needed for `pg_dump` 18 + `age` + rclone), cron definition, and the drill runbook all live under `deploy/` (e.g. `deploy/backup/`); compose service added to the dev, prod, and e2e profiles (e2e uses the one-shot mode only).
- Keep `pg_dump` client version matched to Postgres 18 (use the same major image family as the DB container).
- The status-file format is a tiny contract (timestamp, result, artifact, size) — document it in the runbook; 8.4's clean-host verification and any future monitoring stack (deferred) both read it.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 8.3]
- Operations conventions (nightly encrypted dump/WAL, off-host, restore drill, healthchecks): [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Consistency Conventions — Operations]
- AD-11 (backups encrypted before leaving the host; PHI-class stores): [Source: ARCHITECTURE-SPINE.md#AD-11]
- AD-3 single backup stream rationale: [Source: docs/Architecture-LINEWORKER.md#4.3 One database (AD-3)]
- Deployment view — `bak` container, environments: [Source: docs/Architecture-LINEWORKER.md#8. Deployment & operations; ARCHITECTURE-SPINE.md#Structural Seed — Container & deployment view]
- BlobStore protocol (binaries covered by backup): [Source: ARCHITECTURE-SPINE.md#Consistency Conventions — Binary storage]
- AD-15 e2e gate + e2e compose profile: [Source: ARCHITECTURE-SPINE.md#AD-15]
- Epic 8 accepted no-FR deviation: [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Minor Concerns item 2]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
