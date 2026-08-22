---
title: 'Story 8.1 — PHI Purge Cascade & Retention'
type: 'feature'
created: '2026-08-22'
baseline_revision: 'c9d9158a2e97032e3757704db6bea77aac0ca546'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: true # seventeen findings were patched and five of them changed behaviour rather than prose: the redaction predicate was silently missing every financial and document audit row that names a claim under a child entity, the cascade could not be re-run after a post-commit failure, a single blob error aborted redaction, and the e2e spec's post-purge assertions were vacuous. What a follow-up should read is whether the widened `entity_id == business_id` route now over-matches anything it should not, whether `_resume_claim_purge`'s discrimination by the surviving `purge` event holds under a partial first run that never got as far as recording one, and whether the batched redaction UPDATE and the guarded checkpoint sweep leave any store half-swept in ways the new tests do not enumerate
context:
  - '{project-root}/_bmad-output/implementation-artifacts/8-1-phi-purge-cascade-retention.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-8-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-6-3-copilot-chat-with-persistent-threads.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-7-5-dataset-chart-export.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Seven epics have written PHI into eleven claim-keyed tables, three user-keyed ones, a vector store, an insight cache, four vendored checkpoint tables, a blob volume and an append-only audit log — and nothing in the build can delete any of it. Every Epic 4/6/7 story that touched a PHI table recorded the same line in `deferred-work.md`: it left the cascade a handle and did not invent a deletion path, because Story 8.1 owns the cascade. There is no purge command, no retention job, no redaction path, and no guard preventing a future story from growing its own delete.

**Approach:** One cascade in `services/audit` — `purge_claim(business_id)` and `purge_user(user_id)` — that deletes every PHI-class store for its subject in FK-safe order, walks `document`/`photo` blob keys through the `BlobStore` protocol, deletes copilot checkpoints through an injected thread-deleter (never SQL against a vendored table), and redacts the subject's `audit_event` diffs in place on a second connection that has `SET ROLE audit_redactor`. Two scheduled jobs enforce the two retention floors that already exist as config knobs. A management command under `scripts/` is the composition root and the only invocation surface. An AST/regex guard test makes "no other code path deletes PHI" a failing test rather than a convention.

## Boundaries & Constraints

**Always:**
- **`services/audit` is the only cascade deleter.** The three existing per-entity deletes (`materialize.py`'s schedule re-materialisation, `remove-secondary-injury`, `delete_meeting`) are AD-4 user-feature commands owned by their AD-12 services; they stay, are registered by name in the guard's exemption map with a reason, and no new one may appear.
- **`services/audit` may not import `agents/`, and no module may put a checkpoint table name in a string literal** (`tests/test_layering.py` enforces both). Checkpoint deletion enters the cascade as an injected `Callable[[str], Awaitable[None]]`; the composition root binds it to `agents.threads.discard_thread`, which calls the saver's own `adelete_thread`.
- **Binaries go through `BlobStore.delete`,** which is documented idempotent precisely so a half-completed purge can be re-run. Never a filesystem path.
- **Redaction runs on a redactor connection and nothing else does.** One private module in `services/audit` opens it; the app role's INSERT-only posture on `audit_event` is untouched. The `redact` and `purge` audit events are INSERTed by the app role, because the redactor cannot insert.
- **Redaction overwrites only non-NULL `before`/`after`.** A content-free row (both NULL) is not "redacted" and emits no `redact` event — writing a marker into a column that never held content would assert a redaction that did not happen.
- **The whole cascade is idempotent and re-runnable.** A second run of the same purge deletes nothing, redacts nothing (rows already carrying the marker are skipped) and emits no new events. A purge that cannot be re-run after a partial failure is a purge that cannot be trusted.
- **Logs carry counts and ids only** (AD-11); an empty run is silent; exceptions log `type(exc).__name__`, never `str(exc)`.
- Migrations only — no manual DDL. New revision chains from `0048_export_limits`.
- The retention floor stays baked into the `audit_redactor_delete` RLS policy. The housekeeping job derives its own cutoff from `Settings.audit_retention_years` and **must detect** a policy/config divergence rather than silently under-delete.

**Block If:**
- Deleting a subject's PHI requires deleting an `app_user` row. It does not and must not: `claim.handler_id` and `audit_event.actor_id` point at it, and the audit skeleton has to survive. `purge_user` purges the user's artifacts, not their account.
- Reaching the redactor role requires a new login role with its own password. It does not: `audit_redactor` is `NOLOGIN` and is reached by `SET ROLE` on the owner connection (`ALEMBIC_DATABASE_URL`), which is how `tests/test_schema_seed.py::test_audit_redactor_can_redact_specific_rows` already does it.
- Closing the export-row gap requires persisting the claim ids an export covered. AD-11 and Story 7.5's AC forbid it — a hundred claim ids is the file's index. Accept and document the limit (see Design Note 5).

**Never:** an HTTP endpoint or UI for purge (admin surfaces ride the deferred IdP decision); a widening of `audit_redactor` beyond SELECT + UPDATE(`before`,`after`) + RLS-bounded DELETE; a `FORCE ROW LEVEL SECURITY` change; a rewrite of any Epic 4/6/7 `_diff` to stop recording field values (see Design Note 4); pgaudit or log hardening (8.2); backups (8.3); CI/health/compose work (8.4); a second system actor; a scheduler container.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Claim purge, happy path | `purge_claim("WC-20017")` on the seeded DB | Every claim-keyed row gone across all 14 relational tables, `claim_embedding`/`ai_insight`/`ai_insight_attempt` gone, `copilot_thread` + its checkpoints gone, blob keys deleted, `employee` gone (no other claim references it), one content-free `purge` event per touched store, one `redact` event per redacted audit row | No error expected |
| Re-run of the same purge | `purge_claim("WC-20017")` twice | Second run returns an empty `PurgeRun`, writes no audit event, logs nothing | Never an error |
| Unknown claim | `purge_claim("WC-99999")` | Raises `ClaimNotFound`; nothing deleted, nothing redacted, no audit event | CLI exits non-zero with the business id and no other detail |
| Shared employee | The claim's `employee_id` is referenced by another claim | The claim goes; the `employee` row survives | No error expected |
| Blob key absent from the store | `document.blob_key` set, object already gone | `BlobStore.delete` is a no-op; the row is still deleted and counted | `BlobNotFound` never raised by `delete` |
| No blob store injected | Command run without a `BlobStore` | Refuses before deleting anything if any `blob_key IS NOT NULL` for the subject; proceeds when there are none (today's seed) | `BlobStoreRequired` |
| User purge | `purge_user(7)` | That user's `diary_note`/`meeting`/`email_log` rows (claim-tagged **and** null-claim), `copilot_thread` + checkpoints, and `session` rows deleted; their `app_user` row untouched; their audit diffs redacted | No error expected |
| Redaction | An audit row with non-NULL `after` for the subject | `after` becomes `{"redacted": true, "redacted_at": "<iso>"}`; `actor_id`, `actor_role`, `action`, `entity`, `entity_id`, `at` unchanged; a `redact` event names `entity="audit_event"`, `entity_id=<row id>`, both diffs NULL | No error expected |
| Content-free audit row | `copilot_approval.*` / `export.*` row for the subject | Left exactly as it is; no marker, no `redact` event | No error expected |
| Redactor unreachable | `ALEMBIC_DATABASE_URL` unset or `SET ROLE` denied | Refuses the whole purge before any delete | `RedactorUnavailable`, message names the role, never the DSN |
| App role tries to redact | `UPDATE audit_event SET after=...` as `lineworker_app` | `permission denied` | Asserted by test |
| Redactor tries to insert | `INSERT INTO audit_event` after `SET ROLE audit_redactor` | `permission denied` | Asserted by test |
| Redactor deletes a young row | Row with `at = now() - 1 day`, floor 7 years | `rowcount == 0` (RLS), no error | Asserted by test |
| Audit retention job | Rows older than `audit_retention_years` exist | Deletes exactly those under the redactor role; one content-free summary event; logs `retention.audit_swept` with a count | Shortfall vs the pre-count raises and logs `retention.floor_mismatch` |
| Audit retention job, nothing due | No row past the floor | Deletes nothing, emits no event, logs nothing | Never an error |
| Checkpoint retention job | `copilot_thread.created_at < now() - copilot_checkpoint_retention_days` | Those threads' checkpoints deleted via the injected deleter, then the `copilot_thread` rows; one content-free summary event; logs a count | A failing thread deletion aborts the run; already-deleted threads are re-runnable |
| Boundary | A row exactly at the floor / a thread exactly at the cutoff | Survives (`<`, never `<=`) | No error expected |

</intent-contract>

## Code Map

- `lineworker/server/services/audit/__init__.py` -- existing `record` (session-borrowing, never commits), `record_copilot_approval`, `record_export`; the declared owner of retention and redaction. Add nothing here but the two new action constants' neighbours if needed.
- `lineworker/server/services/audit/purge.py` -- **new.** The cascade, its `PurgeRun` summary, the redaction predicate, the action constants.
- `lineworker/server/services/audit/_redactor.py` -- **new, private.** The only module that reaches `audit_redactor`.
- `lineworker/server/services/audit/retention.py` -- **new.** The two housekeeping commands.
- `lineworker/server/scripts/purge_claim.py` -- **new.** CLI composition root; `dump_openapi.py` is the shape (`def main()` + `if __name__ == "__main__"`).
- `lineworker/server/api/app.py:74` `build_job_runner`, `:288` `build_copilot`, lifespan at `:394` -- where the two jobs register and where the checkpointer is available to bind the thread deleter.
- `lineworker/server/services/jobs.py` -- `ScheduledJob`, `every_seconds`; `run` takes no arguments and opens its own session.
- `lineworker/server/services/financials/batch.py:157` `system_context(db)` -- the one system actor; reuse, do not mint a second.
- `lineworker/server/data/models/core.py` -- `Claim:104` (`id` surrogate, `claim_id` business `WC-nnnn`), `AuditEvent:1080`, `CopilotThread:1450` (`created_at`), `Photo:414`, `Document:265` (`blob_key:329`/`:462`). **No FK anywhere has `ondelete`; no `relationship()` exists** — every child delete is explicit and ordered.
- `lineworker/server/data/versions/20260810_0003_core_schema.py:206-251` -- the `audit_redactor` grants and the four RLS policies **already shipped**; `audit_redactor_delete` is `USING (at < now() - interval '7 years')`, baked from config at migration time.
- `lineworker/server/data/versions/20260812_0020_photo.py:80` -- `GRANT SELECT ON photo` only. The one grant gap.
- `lineworker/server/data/roles.py` -- `audit_redactor` created `NOLOGIN`; `sync_app_role()` is the template for an owner-credential engine.
- `lineworker/server/config.py:176` `audit_retention_years=7`, `:365` `copilot_checkpoint_retention_days=90` (**both already exist**); `sync_alembic_database_url` / `async_database_url` properties.
- `lineworker/server/agents/threads.py:370` `discard_thread(checkpointer, *, thread_id)` -- written for this story; wraps `adelete_thread`.
- `lineworker/server/services/blobstore/__init__.py:68` -- `BlobStore` Protocol; `delete` documented idempotent for this purge.
- `lineworker/server/tests/test_schema_seed.py:144` -- the `SET ROLE audit_redactor` test to extend.
- `lineworker/server/tests/test_layering.py` -- AST guard style, exemption-dict convention, mandatory negative controls.
- `lineworker/server/tests/test_claim_edit_validation.py:471` -- regex-over-source guard style.
- `lineworker/e2e/fixtures/reset.ts` -- `compose(...)` and `resetDb()`; the `compose("exec","-T","api","uv","run","--no-dev",…)` shape the purge helper must reuse.

## Tasks & Acceptance

**Execution:**
- [x] `lineworker/server/data/versions/20260822_0049_purge_grants.py` -- new revision `0049_purge_grants`, `down_revision = "0048_export_limits"`: `GRANT DELETE ON photo TO lineworker_app` (the cascade cannot delete photo rows today) and `GRANT audit_redactor TO CURRENT_USER` (so `SET ROLE` works for a non-superuser owner, not only the superuser dev/e2e owners). `downgrade()` revokes both. Module docstring states why the RLS policy is **not** changed to a GUC -- rationale in Design Note 1.
- [x] `lineworker/server/config.py` -- add an `async_alembic_database_url` property beside the existing three, mirroring `_with_driver()` (the redactor needs owner credentials over asyncpg and no such property exists), and two new fields `audit_retention_interval_seconds` and `checkpoint_retention_interval_seconds` (daily defaults, `gt=0`) giving the two housekeeping jobs a cadence. The retention *floors* stay the two knobs that already exist -- do not add or rename them.
- [x] `lineworker/server/services/audit/_redactor.py` -- new private module: an async context manager yielding a connection that has run `SET ROLE audit_redactor`, built on a per-call engine from `settings.async_alembic_database_url` and disposed in a `finally` (`data/roles.py::sync_app_role` is the template). Raise `RedactorUnavailable` when the URL is absent or `SET ROLE` is denied, naming the role and never the DSN. `RESET ROLE` on exit.
- [x] `lineworker/server/services/audit/purge.py` -- the cascade. `PURGE_ACTION = "purge"`, `REDACT_ACTION = "redact"`, `REDACTED_ENTITY = "audit_event"`, `REDACTION_MARKER`; frozen `PurgeRun(rows_deleted_by_entity, blobs_deleted, rows_redacted, is_empty)` with a `rows_deleted` sum property. `purge_claim(db, ctx, *, business_id, settings, blobs, delete_thread)` and `purge_user(db, ctx, *, user_id, settings, blobs, delete_thread)`. Deletes children before parents in the order given in Design Note 2, emits one content-free `purge` event per non-empty store, then redacts. Raise `ClaimNotFound` / `UserNotFound` before touching anything.
- [x] `lineworker/server/services/audit/retention.py` -- `purge_expired_audit_events(db, ctx, *, settings)`: under the redactor connection, count-then-delete in one transaction with cutoff `now() - audit_retention_years`; a delete count short of the pre-count means RLS refused rows, so log `retention.floor_mismatch` and raise. `purge_expired_checkpoints(db, ctx, *, settings, delete_thread)`: select `copilot_thread` older than `copilot_checkpoint_retention_days`, delete each thread's checkpoints through `delete_thread`, then the rows. Both return frozen run summaries, emit one content-free summary audit event per non-empty run, and log counts only.
- [x] `lineworker/server/api/app.py` -- `AUDIT_RETENTION_JOB = "audit_retention"`, `CHECKPOINT_RETENTION_JOB = "checkpoint_retention"`; `build_job_runner` gains a `delete_thread` parameter bound in `lifespan` to `partial(discard_thread, runtime.checkpointer)`; register both jobs with `every_seconds(...)` on new interval knobs, following the `payment_batch` closure shape exactly (`async with sessionmaker() as session: await <command>(session, await system_context(session), …)`).
- [x] `lineworker/server/scripts/purge_claim.py` -- CLI: `python -m scripts.purge_claim WC-nnnn` (and `--user <id>`). Builds `configure_logging`, settings, an async engine on `settings.async_database_url`, a psycopg pool + `AsyncPostgresSaver` for the thread deleter bound via `agents.threads.discard_thread`, an optional `VolumeBlobStore`, resolves `system_context`, calls the cascade, prints the run summary as one JSON line, exits non-zero on `ClaimNotFound`/`RedactorUnavailable`/`BlobStoreRequired`. Disposes every resource in `finally`.
- [x] `lineworker/deploy/.env.example` -- append a `# --- PHI purge cascade & retention (Story 8.1) ---` section in the house style: `AUDIT_RETENTION_YEARS`, `COPILOT_CHECKPOINT_RETENTION_DAYS` and the two interval knobs, each commented-out at its default with prose explaining the 7-year floor vs the 90-day checkpoint distinction and that changing the floor also requires a migration (Design Note 1).
- [x] `lineworker/server/tests/test_photo_migration.py:220` -- amend `test_the_app_role_can_read_the_photos_and_cannot_write_them`: rename to say the app role may now delete, assert `set(granted) == {"SELECT", "DELETE"}`, and keep the INSERT-denied assertion. Its docstring must say the DELETE grant exists for the cascade and for nothing else.
- [x] `lineworker/server/tests/test_purge_cascade.py` -- new. Covers every row of the I/O matrix: the AC-6 sweep (seed a blob key, a thread with checkpoints, an insight and an embedding, then assert every store is empty for the subject **and** that the seeded worker's name appears in no `audit_event` diff), idempotency, `employee` shared vs orphan, user purge including null-claim rows, redaction skeleton survival, one `redact` event per redacted row, content-free rows untouched, and the four grant/RLS negative tests. Module-scoped `seeded_db_url`, per-module `db` fixture, `seed_fixture` as the oracle, full-sentence test names.
- [x] `lineworker/server/tests/test_purge_ownership.py` -- new AD-11 guard. Scan `api/ services/ rules/ agents/ data/` (excluding `data/versions/`) for deletes against the PHI model/table names; assert the only offenders are `services/audit/` plus an explicit `PHI_DELETE_EXEMPTIONS` dict naming `services/financials/materialize.py` and `data/repositories/claims.py` with reasons. Ship the two mandatory controls: a positive control proving the guard would notice a new delete, and `test_every_phi_delete_exemption_still_exists`.
- [x] `lineworker/e2e/fixtures/reset.ts` -- export `purgeClaim(businessId: string): void` running `compose("exec","-T","api","uv","run","--no-dev","python","-m","scripts.purge_claim",businessId)`. One named helper, not a second `execFileSync` at the call site.
- [x] `lineworker/e2e/stories/8-1-phi-purge-cascade-retention.spec.ts` -- describe title exactly `@story:8-1 @epic:8 PHI purge cascade & retention`; `@smoke` on the happy path: log in as the handler persona, assert a seeded claim renders in queue and detail, run `purgeClaim`, reload, assert it is gone from the queue, its detail route shows the not-found/empty state rather than an error page, and that another claim's detail and a supervisor login still work.
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- append a Story 8.1 section recording the three decisions this story closes (Design Notes 1, 4, 5) and the residuals it leaves (checkpoint retention keyed on thread creation; export rows purged by age and actor, never by claim).

**Acceptance Criteria:**
- Given a purged claim, when every PHI store is swept, then no relational row, vector, cache row, insight attempt, checkpoint, thread row, blob or `employee` row for that subject remains, and no `audit_event` diff contains any of its values.
- Given `audit_event` after a purge, when a row belonging to the subject is read, then `actor_id`, `actor_role`, `action`, `entity`, `entity_id` and `at` are byte-identical to before and only the non-NULL diffs carry the marker.
- Given the guard test, when any module outside `services/audit` and outside the registered exemption map deletes from a PHI-class table, then the suite fails.
- Given a deployment, when `AUDIT_RETENTION_YEARS` or `COPILOT_CHECKPOINT_RETENTION_DAYS` is set, then the corresponding job's cutoff moves with it and the audit job refuses loudly rather than under-deleting if the RLS floor disagrees.
- Given the composed e2e stack, when `8-1-phi-purge-cascade-retention.spec.ts` runs against a freshly reset DB, then it passes.

## Spec Change Log

## Review Triage Log

### 2026-08-22 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 17: (high 5, medium 6, low 6)
- defer: 10: (high 0, medium 6, low 4)
- reject: 3: (high 0, medium 0, low 3)
- addressed_findings:
  - `[high]` `[patch]` `_claim_audit_predicate` conjoined `entity == 'claim'` onto its business-id route, so every audit row written by `financials/batch.py`, `financials/approval.py`, `financials/materialize.py` and `claims/assessment.py` — which set `entity_id` to the claim's business id under a *child* entity and put no `claim_id` in the diff — survived the purge of the claim it named. Conjunct dropped; docstring and the spec's Design Note 3 corrected to match.
  - `[high]` `[patch]` The sweep oracle scanned only for the injured worker's name, which none of those four diff shapes ever contains, so it could not have caught the gap above. It now also fails on the purged claim's business id in `before`/`after`, and a new test seeds the exact financial audit shape and asserts it is redacted.
  - `[high]` `[patch]` The cascade was not re-runnable: `_delete_blobs` and `_redact` run after the commit, but `purge_claim` raised `ClaimNotFound` once the `claim` row was gone — so a post-commit failure stranded PHI with no recovery path, contradicting both the module docstring and the I/O matrix. A missing claim row is now discriminated from an unknown id by the surviving `purge` audit event; a resumed run completes the redaction and returns a zero-row `PurgeRun`.
  - `[high]` `[patch]` A `BlobStoreError` on one key aborted the whole cascade post-commit and skipped redaction entirely. Each key is now guarded, failures are counted and logged as `audit.blob_orphaned` (counts only), and `PurgeRun` carries `blobs_failed`.
  - `[high]` `[patch]` The e2e spec's seven post-purge table assertions were vacuous: `rowsFor` filtered on a subquery that returns NULL once the claim is deleted, so they passed whether or not anything was removed. The numeric claim pk is captured before the purge, the pre-purge counts are asserted non-zero, and the post-purge counts run against the literal pk.
  - `[medium]` `[patch]` `audit_retention_years` had no validator despite being interpolated into the `audit_redactor_delete` RLS policy — `AUDIT_RETENTION_YEARS=0` would have produced a policy with no floor, the precise outcome migration 0049 refuses the GUC to prevent. Now `Field(default=7, gt=0)`.
  - `[medium]` `[patch]` `purge_user` would have purged the seeded system actor, redacting every payment-batch, embedding-refresh and retention diff in the log. Refused with `SystemActorRefused`.
  - `[medium]` `[patch]` `_redact`'s `UPDATE … IN (ids)` and the checkpoint sweep's row delete expanded one bind parameter per id against a 16-bit protocol limit, failing after the commit for a large subject. Both batch at 5000.
  - `[medium]` `[patch]` One permanently-undeletable thread stalled the checkpoint sweep forever, so the ninety-day floor would never be enforced again. Each thread is guarded, only the discarded ones have their rows deleted, and failures log a count.
  - `[medium]` `[patch]` The AD-11 ownership guard did not scan `scripts/` — the one directory this story added a PHI-deleting entry point to. Added to `SCAN_ROOTS`.
  - `[medium]` `[patch]` `test_every_exempted_file_really_does_still_delete_something` was satisfied by `"sa.delete(" in source`, so a file that stopped deleting PHI kept its exemption silently. The disjunct is gone and `NON_PHI_DELETERS` is now held to the stronger claim its entries actually make.
  - `[low]` `[patch]` `--blob-root` accepted a non-existent directory, whose `delete` silently no-ops while the run reports objects removed. Refused at parse time.
  - `[low]` `[patch]` The redactor checked `alembic_database_url is None`, so an empty string fell through to the app-role fallback and failed one statement later with a worse message. Falsy check.
  - `[low]` `[patch]` `await pool.open()` sat outside the `try` owning the CLI's `finally` teardown, leaking the engine on failure. Moved inside.
  - `[low]` `[patch]` Three docstrings asserted false things: `_purge_stores` claimed no relational delete had run before the checkpoint sweep (three have), `_redactor` claimed a rollback reverts a committed `SET ROLE` (it does not, and the retention job depends on that), and `purge_claim` claimed cheapest-first refusal ordering that `BlobStoreRequired` does not have. All corrected.
  - `[low]` `[patch]` The checkpoint-sweep test indexed `claims[20]`/`claims[21]` outside `_CLAIM_FOR`, the exact unreserved indexing that map exists to prevent and which its length assertion did not cover. Both reserved and reached through `claim_for()`.
  - `[low]` `[patch]` The e2e supervisor test never referenced the purged claim and passed identically against an unpurged database. It now asserts the portfolio is one claim smaller.

## Design Notes

**1. The RLS floor stays baked; the job detects divergence.** Story 8.1's task list suggests driving `audit_redactor_delete` from a `current_setting()` GUC. That would make the policy settable by the very session it guards — a bug setting the GUC to zero would delete the whole audit log, destroying the property Task 5 asks for ("the RLS policy is the safety net — a bug cannot delete younger rows"). Migration 0003 already bakes the configured floor into the policy, so the floor is only changeable by a migration, which is the correct authority. The residual — config says 5 years, policy still says 7, job silently deletes nothing — is closed by the count-then-delete check rather than by weakening the policy.

**2. Delete order (no FK has `ondelete`, so the database will refuse a parent-first delete).** `ai_insight` → `ai_insight_attempt` → `claim_embedding` → checkpoints (via `delete_thread`) → `copilot_thread` → `payment_schedule_week` → `bill` → `expense` → `document` → `photo` → `timeline_event` → `additional_injury` → `treatment_plan_step` → claim-tagged `meeting`/`diary_note`/`email_log` → `claim` → `employee` *only if* no other claim references it. Blob keys are collected from `document`/`photo` **before** their rows are deleted and handed to `BlobStore.delete` after the transaction commits — a blob deleted for a transaction that then rolls back is unrecoverable, and `delete` is idempotent so the reverse order is safe to re-run.

**3. Finding the subject's audit rows.** Three disjoint mechanisms, all required: `entity_id = :wc` **for any entity**, `before->>'claim_id' = :wc OR after->>'claim_id' = :wc` for child-entity rows whose diffs carry the business id precisely so this cascade can find them (`notes.py`, `emails.py`, `meetings.py` all say so), and `entity = 'ai_insight' AND entity_id LIKE :wc || ':%'` for the generation rows.

The first mechanism was originally written `entity = 'claim' AND entity_id = :wc`, and that narrower form was wrong. Four commands record against a *child* entity while putting the **claim's** business id in `entity_id`, each with a comment saying it does so deliberately so the log answers "what happened to WC-20017" without a join: `services/financials/batch.py` (`bill`/`expense`/`payment_schedule_week`), `services/financials/approval.py` (the same three), `services/financials/materialize.py` (`payment_schedule_week`) and `services/claims/assessment.py` (`document`). None of their diffs carries a `claim_id` key, so the second mechanism did not reach them either — payment plans, line-item statuses, week amounts and document review state all survived a purge of the claim they name. Dropping the `entity` conjunct closes the class rather than enumerating four more entities.

The predicate is belt-and-braces; the *oracle* is the sweep test scanning every remaining diff for **both** the seeded worker's name and the purged claim's business id, which fails if the predicate has a gap. The business-id half is load-bearing: those four commands never write a person's name into a diff, so a name-only scan shared the narrow predicate's exact blind spot.

**4. Audit diffs keep recording field values — decision, not omission.** `deferred-work.md` hands this story the question of whether `notes.py`/`meetings.py` should stop copying free text into `after` (and whether `emails.py`'s omission of `body` should be adopted or reverted). The decision: **they keep it.** A diff that records which key changed but not to what is not an audit trail, and AD-4 fixes the schema around `before`/`after` precisely to hold the change. The PHI this leaves at rest is addressed by the mechanism the architecture already designed for it — redact-in-place, which this story delivers and which treats all three tables identically. `emails.py`'s omission of `body` stays as a proportionality choice (the largest blob the console stores, in an append-only table with no delete path whose diff is therefore never needed to reconstruct a removed row) and is documented as such rather than harmonised away. Residual, recorded: PHI free text lives in `audit_event.after` until the subject is purged or the 7-year floor passes.

**5. Export rows are purged by age and actor, never by claim — accepted.** `record_export` sets `entity=<target>`, `entity_id=<filter digest>` because an export spans a set of claims and naming one would be a lie the floor preserves for seven years. The only way to make them claim-reachable is a join table holding the claim ids the file contained, which is exactly the content AD-11 and Story 7.5's AC forbid persisting. Accepted as a documented limit: the rows' `after` is content-free by construction (query terms, proven by a scan for a seeded worker's name), so there is nothing in them to redact, and they age out under the audit retention job.

**6. Checkpoint retention is keyed on `copilot_thread.created_at`.** The checkpoint tables carry no timestamp column this codebase may read, and no module may name them in SQL. `copilot_thread` is this project's own table, indexed, and its `created_at` is minted by the command that starts the conversation. The knob therefore bounds a conversation's *age*, not its idleness; a thread created 91 days ago is purged even if it was used yesterday. Recorded in `deferred-work.md` rather than worked around.

**7. Why the composition root is `scripts/`, not `services/audit/purge.py`'s `__main__`.** The story's task list writes the invocation as `python -m server.services.audit.purge`. Module paths are rooted at `server/`, so that path is wrong twice over — and more importantly, wiring the saver means importing `agents/`, which `test_layering.py::test_no_module_under_services_imports_agents` forbids inside `services/`. `scripts/` is outside that rule and already holds the project's other management command, so the cascade stays a pure service function and the script is the only place that knows how to build a checkpointer.

## Verification

**Commands:**
- `cd lineworker/server && uv run ruff check . ../deploy && uv run ruff format --check . ../deploy` -- expected: clean.
- `cd lineworker/server && uv run mypy .` -- expected: no errors.
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker uv run pytest` -- expected: all pass, including the new `test_purge_cascade.py` and `test_purge_ownership.py`, and the amended `test_photo_migration.py`. DB tests must not skip.
- `cd lineworker/server && uv run alembic upgrade head` -- expected: `0049_purge_grants` applies clean against a fresh database.
- `cd lineworker && docker compose -f deploy/compose.e2e.yaml up -d --build --wait && cd e2e && npm run typecheck && npx playwright test --grep "@story:8-1\b"` -- expected: the story spec passes against the freshly reset stack.

## Auto Run Result

Status: done

### What was implemented

Story 8.1 delivers AD-11's deletion side: one purge cascade owned by `services/audit`, redact-in-place for `audit_event`, and enforced retention on both floors.

`purge_claim(business_id)` and `purge_user(user_id)` delete every PHI-class store for their subject in FK-safe order (no FK in this schema carries `ondelete`, so every child delete is explicit), collect `document`/`photo` blob keys and remove the objects through the `BlobStore` protocol, discard copilot checkpoints through an injected thread-deleter — never SQL against a vendored table, which `test_layering.py` forbids — and then redact the subject's `audit_event` diffs on a second connection that has `SET ROLE audit_redactor`. The row skeleton (actor, role, action, entity, entity_id, timestamp) survives untouched; only diff columns that actually hold content are overwritten, and each redacted row emits its own content-free `redact` event through the app role, which is the only role that may insert. A resumed purge — one whose first run failed after the commit — is discriminated from an unknown id by the surviving `purge` event and completes the redaction rather than refusing.

Two scheduled jobs enforce retention: the audit sweep deletes past `audit_retention_years` under the redactor role and refuses loudly if its delete count falls short of its own pre-count (which is what an RLS floor disagreeing with configuration looks like), and the checkpoint sweep discards conversations past `copilot_checkpoint_retention_days`, skipping and counting any thread the saver cannot discard so one bad thread cannot stall the floor forever. A `scripts/purge_claim.py` management command is the only invocation surface — no HTTP endpoint, no UI. An AST guard makes "no other code path deletes PHI" a failing test, with the three pre-existing per-entity user-feature deletes registered by name and reason.

Three decisions the architecture had deferred to this story are closed in the Design Notes: audit diffs keep recording field values (redact-in-place is the mechanism that addresses the PHI they hold, and it treats the diary/meeting/email tables identically); export rows are purged by age and actor rather than by claim, because the only way to make them claim-reachable is to persist the claim-id list AD-11 forbids; and the `audit_redactor_delete` RLS floor stays baked into the policy rather than becoming a session-settable GUC, so a bug cannot widen its own permission.

### Files changed

**New**
- `lineworker/server/services/audit/purge.py` — the cascade, its ordered store lists, the redaction predicate, `PurgeRun`, and the refusals.
- `lineworker/server/services/audit/_redactor.py` — the only module that assumes `audit_redactor`.
- `lineworker/server/services/audit/retention.py` — the two housekeeping commands.
- `lineworker/server/data/versions/20260822_0049_purge_grants.py` — `GRANT DELETE ON photo`, redactor role membership, symmetric downgrade.
- `lineworker/server/scripts/purge_claim.py` — the CLI composition root.
- `lineworker/server/tests/test_purge_cascade.py` — the AC-6 sweep and every I/O-matrix row.
- `lineworker/server/tests/test_purge_ownership.py` — the AD-11 delete-ownership guard and its controls.
- `lineworker/e2e/stories/8-1-phi-purge-cascade-retention.spec.ts` — the AD-15 story gate.

**Modified**
- `lineworker/server/config.py` — `async_alembic_database_url`, the two job cadence knobs, and a lower bound on `audit_retention_years`.
- `lineworker/server/api/app.py` — both retention jobs registered; `build_job_runner` takes the thread deleter bound in `lifespan`.
- `lineworker/server/tests/test_photo_migration.py` — the grant test now expects `DELETE` and says why.
- `lineworker/server/tests/test_rag_embeddings.py`, `tests/test_ai_insights.py` — updated for `build_job_runner`'s new argument.
- `lineworker/e2e/fixtures/reset.ts` — `purgeClaim` helper.
- `lineworker/deploy/.env.example` — the Story 8.1 knob section.
- `_bmad-output/implementation-artifacts/deferred-work.md` — decisions closed and residuals recorded.

### Review findings

17 patched (5 high, 6 medium, 6 low), 10 deferred (6 medium, 4 low), 3 rejected. No intent gaps and no spec-level loopbacks. The five high-severity patches were behavioural: the redaction predicate was missing every audit row that names a claim in `entity_id` under a child entity, the cascade could not be re-run after a post-commit failure, one blob error aborted redaction entirely, the sweep oracle could not see the predicate gap, and the e2e spec's post-purge table assertions were vacuous against a deleted parent row. Rejected: the claim that `RetentionFloorMismatch` is invisible (its `log.error` already carries both numbers), the call for unit tests over argparse wiring, and the intra-module test ordering objection, which is the repo-wide module-scoped-database convention rather than this file's defect.

### Verification

All five commands were run by the orchestrator after the patch pass, not only by the implementing agents:

- `uv run ruff check . ../deploy && uv run ruff format --check . ../deploy` — `All checks passed!` / `303 files already formatted`
- `uv run mypy .` — `Success: no issues found in 289 source files`
- `MIGRATION_TEST_DATABASE_URL=… uv run pytest` — `3306 passed, 1 skipped` in 380s; the single skip is a pre-existing data-conditional one, and no DB test skipped
- `cd e2e && npm run typecheck` — clean
- `docker compose -f deploy/compose.e2e.yaml up -d --build --wait` then `npx playwright test --grep "@story:8-1\b"` — `4 passed`

`alembic upgrade head` against a fresh database is exercised on every e2e reset, which applies `0048_export_limits → 0049_purge_grants`.

### Residual risks

- **The spec's I/O matrix now disagrees with the shipped checkpoint sweep.** The matrix cell says a failing thread deletion aborts the run; that behaviour would let one undeletable thread stall the ninety-day floor permanently, contradicting AC 5 in the same document. The sweep skips and counts instead. The matrix is inside the read-only intent contract and needs amending by its owner.
- **Checkpoint discards are irreversible mid-cascade** — the saver's pool is `autocommit=True` while the relational deletes are not, so a failure rolls back the rows and not the transcripts.
- **Blob keys are unrecoverable once their rows are gone**, so a resumed purge can finish the redaction but cannot retry an orphaned object; orphans are logged by count.
- **The redactor role membership is granted to whichever role ran the migration**, which is not the role the api assumes it on in the e2e profile. It works only because every owner in every profile is currently a superuser.
- **The daily audit sweep is unindexed and unbatched** and will not stay cheap once the table has seven years in it.
- **`purge_user` removes notes, meetings and emails attached to claims that were not purged.** Defensible as a subject-deletion, but it is a compliance decision nobody has ratified.

All six are recorded in `deferred-work.md` with the evidence behind them.
