# Story 8.2: Database Audit & Log Hardening

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a compliance officer,
I want database-level auditing that is itself PHI-safe,
so that operational visibility never becomes a leak.

## Acceptance Criteria

1. **Given** pgaudit, **when** enabled, **then** it captures DDL and role/privilege classes only — DML statement/parameter logging is disabled (value-level history is AD-4's job) — and the DB log destination lives on the encrypted volume with bounded rotation (AD-11).
2. **Given** application logs, **when** tested, **then** structlog output carries IDs and event names only, with an automated test/lint asserting no PHI field values, prompt bodies, or model outputs appear in log calls (AD-11).
3. **Given** transport, **when** the prod compose runs, **then** TLS terminates at nginx ingress and the API-to-Postgres connection uses TLS, both verified in configuration (NFR-5).

## Tasks / Subtasks

- [ ] Task 1: pgaudit — DDL + role classes only (AC: 1)
  - [ ] Postgres image/config: load `pgaudit` (`shared_preload_libraries = 'pgaudit'`; the `pgvector/pgvector:pg18` base may need the extension package added — extend the image in `deploy/` if so) and `CREATE EXTENSION pgaudit` via Alembic migration (AD-3: Alembic is the only DDL mechanism)
  - [ ] `pgaudit.log = 'ddl, role'` — explicitly **not** `read`, `write`, or `function`; `pgaudit.log_parameter = off`; `pgaudit.log_statement_once` as appropriate — the invariant is that no DML statement text or bound parameter (both PHI surfaces) ever reaches the log
  - [ ] Config lands in the postgres service definition (`postgresql.conf` fragment or `-c` flags in compose), identical mechanism in dev and prod profiles so drift can't hide
- [ ] Task 2: DB log destination on the encrypted volume, bounded rotation (AC: 1)
  - [ ] `logging_collector = on`, `log_destination` writing under the postgres data volume (the encrypted volume in prod — see Dev Notes on where encryption actually lives), never to an unencrypted bind mount and never solely to container stdout shipped elsewhere
  - [ ] Bounded rotation: `log_rotation_age` / `log_rotation_size` + `log_truncate_on_rotation = on` with a fixed filename cycle (e.g. day-of-week pattern) so total log footprint is capped — document the resulting retention window in `deploy/`
- [ ] Task 3: App-log PHI ban — automated enforcement (AC: 2)
  - [ ] Static lint over `server/`: every structlog call site passes only IDs/enums/event names — implement as a grep/AST lint with an explicit allowlist of permitted kwarg names (e.g. `claim_id`, `user_id`, `entity`, `action`, `count`, `duration_ms`), failing CI on any call passing fields like `body`, `content`, `prompt`, `diagnosis`, `notes`, or f-string interpolation of model output
  - [ ] Runtime test: capture structlog output while exercising representative flows (an audited claim edit, a copilot run against the test stub, a purge job summary) and assert no seeded PHI values (worker names, ICD-10 codes, wage figures, prompt/model text) appear in any emitted line
  - [ ] Add a structlog processor as a belt-and-braces guard (drop/redact keys not on the allowlist) — the lint is the gate, the processor is defense in depth
- [ ] Task 4: DB role-grant verification (AC: 1)
  - [ ] Automated pytest asserting, from `information_schema`/`pg_catalog`: the application DB role holds INSERT-only on `audit_event` (no UPDATE/DELETE/TRUNCATE), and the `audit_redactor` role (Story 8.1) holds exactly SELECT (which the qualified UPDATE/DELETE cannot run without), UPDATE on `before`/`after`, plus the RLS-scoped DELETE — nothing else, no other role holds writes on `audit_event`
  - [ ] Run this assertion in CI against the fresh-migration DB so grant drift in any future migration fails the gate
- [ ] Task 5: TLS at ingress and to Postgres (AC: 3)
  - [ ] Prod compose nginx: TLS server block (cert/key mounted from the host secret store per the config conventions — never in VCS), HTTP→HTTPS redirect, keeping the existing `/api` proxy + SSE settings intact; dev profile stays plain HTTP (Story 1.1 decision)
  - [ ] Postgres prod config: `ssl = on` with server cert/key from the host secret store
  - [ ] API prod config: `DATABASE_URL` carries `sslmode=verify-full` (or `require` at minimum — document the choice and the CA handling) via the pydantic-settings module; dev remains non-TLS
  - [ ] "Verified in configuration": a config-verification check (script or pytest, run in CI) asserting the prod compose/nginx/postgres files contain the TLS directives and the prod env template carries the sslmode — plus a documented manual verification command set (`openssl s_client` at ingress, `SELECT ssl FROM pg_stat_ssl` for the API connection) in the deploy docs
- [ ] Task 6: E2E story spec (AC: 1, 2)
  - [ ] `e2e/stories/8-2-database-audit-log-hardening.spec.ts` tagged `@story:8-2 @epic:8`, `@smoke` on the happy path — see Testing requirements for the ops-only design and reasoning

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story hardens the **observability surfaces** so they cannot leak PHI: pgaudit posture, DB log placement/rotation, the app-log PHI ban made mechanical, grant verification, and TLS. It does **not** build value-level audit history — that is AD-4's `audit_event` table, live since Epic 1 (verify, don't rebuild). Not in this story: the purge cascade and `audit_redactor` role creation (8.1 — this story only *verifies* its grants), backups (8.3), health endpoints/CI completion/prod-compose GPU finalization (8.4 — this story contributes only the TLS pieces of the prod profile), any monitoring/alerting stack (deferred by decision — log *shipping* and dashboards are explicitly out), and IdP/session hardening (deferred).

### Architecture compliance (binding ADs for this story)

- **AD-11 (pgaudit posture):** pgaudit output is a PHI surface — DML statement/parameter logging disabled, DDL + role/privilege classes only; the DB log destination lives on the encrypted volume with bounded rotation; operational logs carry IDs and event names only.
- **AD-4:** value history belongs to `audit_event`, not the DB log — that division of labor is why disabling pgaudit DML classes loses nothing; the INSERT-only app grant is re-verified here mechanically.
- **AD-3:** `CREATE EXTENSION pgaudit` and any grant assertions ride Alembic migrations/tests — no manual DDL.
- **AD-5 posture:** nothing here introduces any external egress; log shipping off-host is out of scope (deferred observability decision).
- **NFR-5 / §7 control table:** TLS at ingress and to Postgres; encrypted volumes at rest with the DB log destination included.
- **Conventions:** secrets (certs, keys) in env files/host secret store outside VCS; config through the single pydantic-settings module.

### Data notes

- **No new tables, no new roles.** DB-level configuration only (`pgaudit` extension via migration, postgresql.conf settings, TLS material).
- Verifies (does not alter) grants on `audit_event`: app role INSERT-only; `audit_redactor` per its AD-4 registered exception. **Write-owner note (AD-12):** no entity writes occur in this story; `services/audit` remains the audit/purge owner and nothing here adds a writer.
- Encrypted-volume reality check: Docker volumes are not self-encrypting — at-rest encryption is host-level (LUKS/dm-crypt or equivalent) per the deployment view ("On-prem Docker host — encrypted volumes"). This story's obligation is that the DB log path resolves **inside** that encrypted volume; documenting/finalizing the host-encryption setup itself is 8.4's prod-compose deliverable.

### Testing requirements

- pgaudit posture test: against the migrated DB, run a DDL statement and a DML statement with a distinctive literal value; assert the log captures the DDL/role event and that the literal value appears nowhere in the DB log.
- Rotation config test: assert the bounded-rotation settings are active (`SHOW log_rotation_age` etc.) and documented.
- App-log lint + runtime PHI-capture test (Task 3) wired into CI.
- Grant-verification pytest (Task 4) in CI against the fresh-migration DB.
- Config-verification check for TLS directives (Task 5) in CI; manual verification commands documented in deploy docs.
- **AD-15 Playwright spec:** `e2e/stories/8-2-database-audit-log-hardening.spec.ts` tagged `@story:8-2 @epic:8`. This story is ops-only with no UI surface, so the spec is a thin browser-level regression check plus structural assertions (reasoning: the hardening's browser-observable claim is precisely that the console still works unchanged with pgaudit active and hardened DB config — a functioning console over the hardened stack *is* the story's end-to-end proof; TLS is prod-profile-only and cannot be asserted in the e2e profile, so it is covered by the CI config-verification check instead). Enable the same pgaudit posture in the e2e compose profile's postgres so the gate runs against the hardened configuration; the spec logs in as a persona, performs one audited inline edit end to end, and asserts success — tag this happy path `@smoke`. The story cannot move to `review`/`done` until this spec passes against the freshly reset stack.

### Project Structure Notes

- Postgres config fragments, nginx TLS server block, and cert-mount wiring live under `deploy/` (`deploy/nginx/`, postgres conf alongside the compose files); prod-only material goes in the prod profile, never the dev/e2e profiles (except pgaudit posture, which is applied in all profiles).
- The log-lint lives with the server tooling (ruff custom rule or a small script under `server/` run by CI alongside ruff/mypy — extend the CI workflow from Story 1.1).
- `CREATE EXTENSION pgaudit` migration goes in `server/data/` Alembic versions; note in its docstring that the extension requires the preload config to be present (compose-level), so a bare `alembic upgrade` against a non-configured Postgres should fail loudly, not silently skip.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 8.2]
- AD-11 pgaudit posture, DB log destination, app-log PHI ban: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-11]
- AD-4 audit grants (INSERT-only app role; redactor exception): [Source: ARCHITECTURE-SPINE.md#AD-4]
- Security control table (audit, encryption, TLS rows): [Source: docs/Architecture-LINEWORKER.md#7. Security & compliance]
- Deployment view (encrypted volumes, pgaudit in the postgres container): [Source: docs/Architecture-LINEWORKER.md#8. Deployment & operations]
- Logging convention (structlog, IDs and event names only): [Source: ARCHITECTURE-SPINE.md#Consistency Conventions — Logging / Config & secrets]
- AD-15 e2e gate + e2e profile: [Source: ARCHITECTURE-SPINE.md#AD-15]
- Epic 8 accepted no-FR deviation: [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Minor Concerns item 2]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
