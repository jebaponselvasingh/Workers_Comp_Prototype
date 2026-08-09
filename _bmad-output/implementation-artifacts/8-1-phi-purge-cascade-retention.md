# Story 8.1: PHI Purge Cascade & Retention

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a compliance officer,
I want one purge cascade that covers every PHI-class store with enforced retention,
so that deletion obligations are met without resurrectable copies.

## Acceptance Criteria

1. **Given** a purge request for a claim/user, **when** `services/audit`'s cascade runs, **then** it covers every PHI-class store — relational claim data, embeddings, `ai_insight` cache, document/photo binaries via `BlobStore`, and LangGraph checkpoints deleted by `thread_id` (AD-11).
2. **And** no other code path deletes PHI.
3. **Given** `audit_event`, **when** purge touches it, **then** rows are redacted in place — the skeleton (actor, action, entity, timestamps) survives while `before`/`after` are overwritten with a redaction marker — executed under the `audit_redactor` DB role whose only grants are UPDATE on those columns and RLS-constrained DELETE beyond the retention floor (AD-4 exception).
4. **And** every redaction emits its own content-free `redact` audit event.
5. **Given** retention configuration, **when** deployed, **then** the audit floor defaults to 7 years and copilot-checkpoint retention to 90 days as distinct config knobs (AD-11), with end-of-retention housekeeping running under the redactor role.
6. **Given** the cascade under test, **when** a purge completes, **then** no PHI for the purged subject is retrievable from any store — verified by an integration test sweeping relational rows, vectors, cache, checkpoints, and blobs.

## Tasks / Subtasks

- [ ] Task 1: `audit_redactor` DB role migration (AC: 3, 5)
  - [ ] Alembic migration creating the `audit_redactor` role with exactly two grants and nothing else: `GRANT UPDATE (before, after) ON audit_event` and `GRANT DELETE ON audit_event` constrained by a row-level-security policy `at < now() - <retention floor>` (floor injected from config at policy-evaluation time — see Task 2 for the knob; do not hardcode `7 years` in the policy if the config can drive it, e.g. via a `current_setting()` GUC set by the purge session)
  - [ ] Enable RLS on `audit_event` for the redactor role only; the app role's INSERT-only posture (Story 1.2) is untouched — add a migration-level assertion/test that app role still has no UPDATE/DELETE
  - [ ] Role is assumable solely by `services/audit` jobs: purge/retention code opens its dedicated connection (or `SET ROLE audit_redactor` on a grant-restricted login) from one place in `services/audit`; no other module imports it
- [ ] Task 2: Retention config knobs (AC: 5)
  - [ ] Extend `server/config.py` (the single pydantic-settings module): `audit_retention_years` (default 7) and `checkpoint_retention_days` (default 90) — two distinct knobs, per AD-11
  - [ ] Surface both in `deploy/*.env.example` templates with comments explaining the 7-year floor vs 90-day checkpoint distinction
- [ ] Task 3: The purge cascade command in `services/audit` (AC: 1, 2)
  - [ ] One command, e.g. `purge_claim(business_id)` (and a `purge_user(user_id)` variant for user-keyed PHI: diary notes, meetings, email logs, checkpoints by `user_id` component of the thread key), owned by `services/audit` — the only code in the system permitted to delete PHI
  - [ ] Relational claim aggregate: delete `claim` and all child rows — `additional_injury`, `treatment_plan_step`, `timeline_event`, `document`, `photo`, `bill`, `expense`, `payment_schedule_week`, and the claim-tagged `diary_note` / `meeting` / `email_log` rows — requesting deletion through DB-level cascade or explicit deletes inside this one command (AD-12: purge is the registered exception to per-entity write ownership; owning services do not grow their own delete commands)
  - [ ] Vector + cache stores: `claim_embedding` rows and `ai_insight` rows for the claim
  - [ ] Binaries: enumerate the claim's `document`/`photo` blob keys and delete each via the `BlobStore` protocol (`delete`) — never filesystem paths directly
  - [ ] LangGraph checkpoints: resolve every `thread_id` whose key scope is the claim's business ID (thread key is `(scope, user_id, conversation_seq)`, server-minted — AD-6) and delete the saver's checkpoint/writes rows by `thread_id`; for `purge_user`, resolve by the `user_id` key component
  - [ ] Each store's purge step emits a content-free audit event (`action: purge`, entity + entity_id, no before/after content) via the normal app-role INSERT path
  - [ ] Invocation surface: a management command (e.g. `python -m server.services.audit.purge WC-nnnn`) run host-side / `compose exec api` — no HTTP endpoint and no UI in this story (admin exposure rides the deferred IdP decision; see Dev Notes)
- [ ] Task 4: `audit_event` redact-in-place (AC: 3, 4)
  - [ ] Within the cascade, for every `audit_event` row whose `entity`/`entity_id` belongs to the purged subject: `UPDATE` `before`/`after` to a fixed redaction marker (e.g. `{"redacted": true, "redacted_at": <ts>}`) — actor, action, entity, entity_id, and timestamps survive untouched
  - [ ] The UPDATE runs on the redactor connection from Task 1; the accompanying `action: redact` audit event (one per redaction, content-free) is INSERTed via the app role — the redactor role cannot INSERT
- [ ] Task 5: End-of-retention housekeeping jobs (AC: 5)
  - [ ] Scheduled in-process job (same in-process mechanism as the payment batch / embedding refresh — scheduler container is a deferred decision): delete `audit_event` rows older than `audit_retention_years` under the redactor role (the RLS policy is the safety net — a bug cannot delete younger rows)
  - [ ] Second job: delete checkpoint threads older than `checkpoint_retention_days` (by checkpoint timestamp), via `services/audit` — same purge-owner rule
  - [ ] Both jobs log run summaries (counts and IDs only — AD-11 log ban)
- [ ] Task 6: "No other code path deletes PHI" enforcement (AC: 2)
  - [ ] Repo-level test/lint: no `DELETE`/`session.delete`/`drop` against PHI-class tables outside `services/audit` (grep-based lint over `server/` is acceptable; document the pattern) — the e2e DB-reset owner role is exempt by profile (AD-15, e2e-only role)
  - [ ] Verify no existing service from Epics 1–7 acquired a delete path (Story 2.4's remove-secondary-injury is a user feature owned by `services/claims` — confirm it is an audited command on `additional_injury` and document it as in-scope-of-AD-4, not a purge path; purge remains the only *cascade* deleter)
- [ ] Task 7: Integration test — the purge sweep (AC: 6)
  - [ ] pytest integration test against a seeded DB: create/verify PHI presence in every store (relational rows, `claim_embedding` vectors, `ai_insight`, checkpoints for a seeded thread, a blob), run `purge_claim`, then sweep every store asserting zero PHI for the subject remains, audit skeleton survives with redacted diffs, and one `redact`/`purge` event exists per touched store/row
  - [ ] Grant-boundary tests: app role UPDATE on `audit_event` fails; redactor DELETE of a row younger than the floor fails (RLS); redactor INSERT fails
- [ ] Task 8: E2E story spec (AC: 1, 6)
  - [ ] `e2e/stories/8-1-phi-purge-cascade-retention.spec.ts` tagged `@story:8-1 @epic:8`, `@smoke` on the happy path — see Testing requirements for the ops-shaped design

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story delivers the **deletion side** of AD-11: one cascade, one owner, enforced retention. Per-write audit events, INSERT-only app grants, and encryption in transit/at rest were built into Epics 1–7 as they went — do **not** re-implement or refactor them here. Not in this story: pgaudit and log hardening (8.2), backups and restore drill (8.3), CI/health/prod-compose completion (8.4), any purge-request UI or HTTP endpoint (admin surfaces ride the deferred IdP decision — the deliverable is the `services/audit` command + jobs), observability/alerting for the retention jobs (deferred stack decision — structlog summaries suffice), and MinIO (the `BlobStore` protocol is the boundary; the volume impl is what exists).

### Architecture compliance (binding ADs for this story)

- **AD-11 PHI lifecycle:** the core of this story — every claim-derived store is PHI-class; one purge cascade owned by `services/audit`; checkpoints deleted by `thread_id`; checkpoint retention (90d) distinct from the audit floor (7y); `audit_event` gets redact-in-place, not delete; no other code deletes PHI.
- **AD-4 (registered exception):** the `audit_redactor` role holds exactly two grants — UPDATE on `before`/`after` and RLS-constrained DELETE past the floor — assumable solely by `services/audit` jobs; every redaction emits its own content-free `redact` event; the app role stays INSERT-only.
- **AD-3 one PostgreSQL:** the single-store decision is what makes one cascade possible — every store the sweep must cover lives in the same instance (plus the `BlobStore` volume). The redactor role and RLS policy arrive by Alembic migration, never manual DDL.
- **AD-12 write ownership:** purge is the registered exception — `services/audit` deletes across entities other services own for writes; owning services do not gain delete commands.
- **AD-6 thread keys:** checkpoint purge resolves threads by the `(scope, user_id, conversation_seq)` key — scope = claim business ID for claim purges, `user_id` component for user purges.
- **Conventions (config):** both retention knobs land in the single pydantic-settings module; never hardcoded in SQL or job code.

### Data notes

- **No new tables.** New DB **role**: `audit_redactor` (created by this story's Alembic migration, with its grants and the `audit_event` RLS policy).
- Stores touched (read/delete): `claim` + children (`additional_injury`, `treatment_plan_step`, `timeline_event`, `document`, `photo`, `bill`, `expense`, `payment_schedule_week`), claim-tagged `diary_note`/`meeting`/`email_log`, `claim_embedding`, `ai_insight`, LangGraph checkpoint tables (the AD-3 vendored-migration exception tables), blob volume via `BlobStore`.
- `audit_event`: UPDATE (redaction) under `audit_redactor`; INSERT (`purge`/`redact` events) under the app role. **Write-owner (AD-12):** `services/audit` owns the cascade and both retention jobs; it is the sole PHI deleter in the codebase.

### Testing requirements

- pytest integration: the full purge sweep of AC 6 (all five store classes), plus the redaction-skeleton assertion and the per-redaction `redact` event.
- Grant/RLS negative tests: app-role UPDATE/DELETE on `audit_event` rejected; redactor DELETE younger than floor rejected; redactor INSERT rejected.
- Unit tests: thread-id resolution from claim/user; retention-cutoff arithmetic for both knobs (boundary: exactly-at-floor rows survive).
- Repo lint/test for AC 2 (no PHI deletes outside `services/audit`).
- **AD-15 Playwright spec:** `e2e/stories/8-1-phi-purge-cascade-retention.spec.ts` tagged `@story:8-1 @epic:8`. This is an ops-shaped story with no purge UI, so the spec asserts observable console behavior around a real purge (reasoning: AD-15 wants the story's effect proven through the browser against the real stack, and a purge *has* a browser-observable effect): after the standard DB reset, log in as a handler persona, assert a seeded claim renders in queue + detail; execute the purge management command against the e2e stack (compose exec from the spec's setup — the same host-side mechanism the reset fixture uses); reload and assert the claim is gone from the queue, its detail route yields the not-found/empty state (no error page), and the rest of the console (another claim's detail, dashboard login as supervisor) still works. Tag that happy path `@smoke`. The story cannot move to `review`/`done` until this spec passes against the freshly reset stack.

### Project Structure Notes

- Cascade + jobs live in `server/services/audit/` (package exists since Story 1.1's scaffold; `record_copilot_approval` landed there in Epic 6). Migration in `server/data/` Alembic versions.
- The redactor connection/`SET ROLE` helper is one private module inside `services/audit` — nothing else imports it; keep the credential in the settings module (env-driven), never in VCS.
- The e2e DB-reset owner role (AD-15) can still drop schema in the e2e profile — that is not a violation of AC 2 (test-profile-only role, never present in dev/prod).

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 8.1]
- Epic 8 scope (NFR closure; per-write audit built in Epics 1–7): [Source: _bmad-output/planning-artifacts/epics.md#Epic 8]
- AD-11 full text (purge cascade, redact-in-place, retention knobs): [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-11]
- AD-4 `audit_redactor` exception (exact grants, RLS DELETE, `redact` event): [Source: ARCHITECTURE-SPINE.md#AD-4]
- AD-6 thread key shape for checkpoint purge: [Source: ARCHITECTURE-SPINE.md#AD-6]
- PHI lifecycle control row + chat-history-is-PHI: [Source: docs/Architecture-LINEWORKER.md#7. Security & compliance / #5.4]
- One-database purge rationale: [Source: docs/Architecture-LINEWORKER.md#4.3 One database (AD-3)]
- AD-15 done-gate + e2e-profile reset role: [Source: ARCHITECTURE-SPINE.md#AD-15]
- Epic 8 accepted no-FR deviation: [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Minor Concerns item 2]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
