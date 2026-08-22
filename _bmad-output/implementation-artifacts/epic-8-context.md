# Epic 8 Context: Production Hardening & Compliance Operations

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Epic 8 is the point at which the system becomes safe to run on real protected health information rather than seeded synthetic claims. It delivers the four compliance capabilities that could not be distributed into feature work: a single purge cascade covering every PHI-bearing store with audit rows redacted in place rather than deleted, database-level auditing and logging that are themselves PHI-safe, nightly encrypted off-host backups proven by an executed restore drill, and the complete CI and operations gate locked in so every future change ships against the same bar. It carries no functional requirements — per-write audit, optimistic concurrency and encryption were built into Epics 1–7 as they went — and instead closes the security/PHI, quality-gate and operations non-functional requirements. This is the last epic; what it hardens is everything the previous seven built.

## Stories

- Story 8.1: PHI Purge Cascade & Retention
- Story 8.2: Database Audit & Log Hardening
- Story 8.3: Encrypted Backups & Restore Drill
- Story 8.4: Complete Quality & Operations Gate

## Requirements & Constraints

- **One purge path, no exceptions.** Deletion of a claim's or user's data runs through a single cascade owned by the audit service, covering every PHI-class store: relational claim data, pgvector embeddings, the AI-insight cache, document and photo binaries through the blob-store protocol, and copilot checkpoints deleted by thread id. No other code path deletes PHI. The done-gate is an integration test that sweeps all five store families after a purge and finds nothing retrievable for the purged subject.
- **Audit rows are redacted, never deleted.** The audit log's purge disposition differs from every other store: the row skeleton (actor, action, entity, timestamps) survives so action history remains provable, while the before/after diffs are overwritten with a redaction marker. Each redaction emits its own content-free `redact` audit event.
- **Retention is configuration, not code.** The audit retention floor (7-year default) and copilot-checkpoint retention (90-day default) are separate deployment knobs. End-of-retention housekeeping runs on the same restricted path as redaction.
- **Database auditing must not become the leak.** pgaudit captures DDL and role/privilege classes only; DML statement and parameter logging stays off, because value-level history is the application audit log's job. The database log destination sits on the encrypted volume with bounded rotation.
- **Operational logs carry identifiers only.** Structured application logs may contain IDs and event names — never PHI field values, prompt bodies, or model outputs. This needs an automated test or lint asserting it, not a convention anyone remembers.
- **Encryption in transit is verified in configuration.** TLS terminates at the nginx ingress and the API-to-Postgres connection uses TLS; both are checked against the production compose, not assumed.
- **Backups are provable, not aspirational.** A nightly dump/WAL archive is encrypted before it leaves the host and copied off-host. The restore drill is executed against a clean environment, must pass the smoke suite (login, scoped queue, claim detail, dashboard), and is documented with steps, duration and a verification checklist. A failed nightly run surfaces non-silently through health or status output rather than being discovered at restore time.
- **The CI gate is the complete one.** Lint, typecheck, pytest including property-based tests over financial formulas and derivations, agent routing and tool-registry tests (the write-tool gate must raise without an approval marker), graph interrupt round-trip tests, the end-to-end SSE/assistant-ui stream integration test, adversarial prompt-injection fixtures asserting containment, Vitest plus Playwright smoke per web feature, and Alembic migrations running clean against a fresh database.
- **The stack must boot from nothing.** Every container exposes a health endpoint, compose healthchecks gate startup order in both dev and prod, and the production compose (GPU, TLS, encrypted volumes) brings the full stack up by following the deployment docs alone.
- **Intentional gaps are documented, not hidden.** The deployment documentation lists each deferred decision with the trigger that reopens it, so operations knows what was deliberately not built.

## Technical Decisions

- **Purge and retention live in the audit service.** It is the sole owner of the cascade. Other services expose whatever deletion primitive it needs; none of them implement their own purge.
- **A dedicated database role is the only exception to append-only audit.** The application role holds INSERT-only rights on the audit table. A separate `audit_redactor` role — assumable solely by the purge and retention jobs — holds exactly two grants and nothing else: UPDATE on the before/after columns, and DELETE constrained by a row-level-security policy to rows past the retention floor. Widening that role's grants breaks the invariant the whole audit design rests on.
- **Everything claim-derived is PHI-class.** Checkpoints, embeddings, the insight cache, audit diffs and binaries all inherit the same encryption-at-rest, encryption-in-transit, log-ban and purge obligations as claim rows. There is no "internal" store exempt from this.
- **Checkpoints are addressed by thread key.** Copilot threads are keyed by scope plus user plus conversation sequence, so purging a claim's or a user's conversations means deleting by thread id — the checkpoint tables are written only by the saver and migrated by a dedicated vendored Alembic migration, never by the saver's own setup in production.
- **Binaries go through the blob-store protocol.** The purge deletes via that protocol's delete operation, so it keeps working when the backing implementation is swapped.
- **Migrations stay Alembic-only.** Any schema, role, grant or RLS policy this epic introduces arrives as a migration that runs clean against a fresh database — that clean-upgrade run is itself part of the gate.
- **Configuration comes from the settings module.** Retention floors, backup destinations and log paths are 12-factor environment configuration resolved through the single pydantic-settings module; secrets live outside version control (env file in dev, host secret store in prod). Nothing here is hardcoded.
- **Deployment shape is Docker Compose on-prem.** Two environments: dev (CPU acceptable) and prod (GPU, TLS, encrypted volumes, off-host backups). nginx is the sole ingress; Ollama and Postgres ports stay internal-only. No Kubernetes at this stage.
- **The E2E suite is the regression floor.** One Playwright spec per story against the freshly reset composed stack, with the database reset before every spec file, plus a lint asserting a one-to-one mapping between non-backlog story keys and spec files. The smoke set the restore drill validates against is the same tagged happy-path set CI runs.
- **Test data is synthetic by rule.** Running the E2E suite against any environment containing real claim data is prohibited, which is what makes traces and screenshots safe as CI artifacts. The restore drill inherits the same constraint.
- **Do not build ahead.** The identity provider, MinIO blob backend, the scheduler/worker-container mechanism, the observability and alerting stack, and multi-user Ollama queueing are all deferred by decision. Story 8.4 documents them with reopening triggers; it does not implement them.

## Cross-Story Dependencies

- **Epics 1–7 → all of Epic 8.** This epic hardens what already exists. The purge cascade can only be written against the full set of PHI stores, so it depends on the relational schema and seed, the blob-store-backed documents and photos, the embeddings and insight cache, and the copilot checkpoints. The CI gate depends on every test category those epics introduced.
- **Story 8.1 → 8.2.** Redact-in-place establishes what the restricted database role may do; the database-audit hardening must not reintroduce value-level history through pgaudit that the redaction path cannot reach.
- **Story 8.1/8.2 → 8.3.** Backups inherit the PHI classification, so encryption-before-leaving-host is a consequence of the same rule — and a restored environment must satisfy the same purge and log guarantees.
- **Story 8.3 → 8.4.** The restore drill validates against the smoke suite that Story 8.4's gate defines and runs.
- **Story 8.4 → the deferred-decision registry.** The deployment documentation it produces must enumerate each intentionally-unbuilt item with its reopening trigger; that registry is maintained in the architecture, so keep the two in sync rather than restating them independently.
