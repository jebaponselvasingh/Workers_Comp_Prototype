# Story 8.4: Complete Quality & Operations Gate

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a development organization,
I want the full CI and operational gate locked in,
so that every future change ships against the same bar.

## Acceptance Criteria

1. **Given** CI on every merge, **when** it runs, **then** the complete gate executes: lint, typecheck, pytest (including Hypothesis property tests on financials/derivations), agent routing + tool-registry tests (write-tool gate raises without approval token), graph interrupt round-trip tests, the SSE/assistant-ui integration test, Vitest + Playwright smoke per web feature, and Alembic clean-upgrade against a fresh DB (NFR-7).
2. **Given** the deployed stack, **when** booted from a clean host, **then** every container exposes a health endpoint, compose healthchecks gate startup order in dev and prod, and the prod compose (GPU, TLS, encrypted volumes) starts the full stack following the deployment docs (NFR-8).
3. **Given** the deferred-decision registry, **when** deployment docs are read, **then** each deferred item (IdP, MinIO, scheduler mechanism, observability stack, Ollama queueing) is listed with its reopening trigger, so operations knows what is intentionally not built.

## Tasks / Subtasks

- [ ] Task 1: CI gate completion audit (AC: 1)
  - [ ] Enumerate the full NFR-7 checklist against the CI workflow as it exists after Epics 1–7 and close every gap; each element was owed by an earlier story — this story's job is verification and wiring, not writing new test suites (missing suites are defects to raise, not silently author):
    - lint + typecheck, server (`ruff`, `mypy`) and web (`eslint`, `tsc`) — since 1.1
    - pytest incl. Hypothesis property tests on `services/financials` (statutory clamp, 3.1) and `services/derivations`
    - agent routing-map unit tests (QAS key→node, 6.4) and tool-registry tests including the write-tool gate raising without the approval token (6.5)
    - graph interrupt approve/reject round-trip tests against the stub chat model (6.5)
    - the one end-to-end SSE + assistant-ui stream-protocol integration test (6.5)
    - Vitest unit tests; Playwright `@smoke` set spanning every web feature area (queue, claim-detail, dashboard, copilot, diary — verify each feature has at least one `@smoke`-tagged story spec)
    - `alembic upgrade head` clean against a fresh Postgres 18 + pgvector service container — now spanning all migrations from 1.2 through 8.2
  - [ ] Confirm every job is a **required check** for merge to main; PR-level stage = declared story spec(s) + `@smoke` per AD-15
- [ ] Task 2: Merge-to-main stage — full suite + bijection lint (AC: 1)
  - [ ] Merge-to-main CI stage runs the **full** `e2e/` suite (`workers: 1`, `fullyParallel: false`, per-spec DB reset) against the e2e compose profile with the model stub
  - [ ] Implement the sprint-status↔spec **bijection lint**: every non-backlog story key in `sprint-status.yaml` has `e2e/stories/<story-key>.spec.ts` and vice versa; tag greps anchored (`@story:8-1\b` style — AD-15's `1-3` vs `1-30` trap); lint fails the merge stage on any orphan in either direction
  - [ ] Verify the CI story-key declaration path (branch name or PR label) selects the right spec subset on PRs
- [ ] Task 3: Health endpoints + startup gating, dev and prod (AC: 2)
  - [ ] Verify/complete a health surface on **every** container: nginx (HTTP check), api (`/healthz` with DB connectivity — since 1.1), postgres (`pg_isready`), ollama (API tags/version probe — since 6.1), backup (status-file healthcheck — since 8.3); add whatever is missing
  - [ ] Compose `depends_on: condition: service_healthy` chains verified in dev **and** prod profiles: postgres → api → nginx; ollama healthy before api marks agent routes available (per 6.x degradation design, api must not hard-fail when ollama is down — healthcheck gates startup order only, AD-14)
  - [ ] `docker compose ps` from a clean boot shows all containers healthy in both profiles; capture the verification in the deployment docs
- [ ] Task 4: Prod compose profile finalized (AC: 2)
  - [ ] Assemble the prod profile end to end: GPU for ollama (NVIDIA Container Toolkit `deploy.resources.reservations`, model tags from env per AD-5), TLS at nginx + TLS to Postgres (as configured in 8.2), backup container with real off-host destination (8.3), all ports internal except nginx (443)
  - [ ] Encrypted volumes: document the host-level encryption requirement (LUKS/dm-crypt or equivalent under the Docker data root / named-volume paths) as a hard prerequisite in the deployment docs — compose cannot enforce it, so the docs carry a pre-flight verification step (e.g. `lsblk`/mount check) in the boot runbook
  - [ ] Write/finalize the deployment runbook in `deploy/` (clean host → prerequisites → secrets/env setup → volume encryption pre-flight → `docker compose -f … up` → health verification → smoke)
  - [ ] **Execute a clean-host boot** following only the runbook (fresh VM or wiped host, GPU where available; if no GPU host exists at story time, run the boot with the documented CPU-model fallback and record the deviation) — the doc is proven by use, matching 8.3's drill standard
- [ ] Task 5: Deferred-decision registry in deployment docs (AC: 3)
  - [ ] Add a "Deliberately not built" section to the deployment docs listing, at minimum: IdP (reopen before first non-dev deployment), MinIO/binary store (when real files replace placeholders), scheduler mechanism (when payment volume outgrows in-process jobs), observability stack (when operations ownership is assigned), Ollama capacity/queueing (before any multi-user rollout) — each with its reopening trigger, sourced from the spine's Deferred list
  - [ ] Cross-link the fuller Deferred registry in the architecture spine rather than duplicating its prose
- [ ] Task 6: E2E story spec (AC: 2)
  - [ ] `e2e/stories/8-4-complete-quality-operations-gate.spec.ts` tagged `@story:8-4 @epic:8`, `@smoke` on the happy path — see Testing requirements for the ops-only design and reasoning

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This is the closing story of the roadmap: it **completes and proves** the quality/operations bar that Stories 1.1 onward have been building incrementally — full CI gate as required checks, merge-to-main full-suite + bijection lint, health on every container, the finalized prod compose, and the deferred-decision registry. It is an audit-and-finish story: most gate elements already exist from their owning stories; anything found missing is a gap to close (or a defect to surface via `bmad-correct-course` if it implies unfinished earlier scope), not an invitation to rebuild suites. Not in this story: choosing or installing an observability/monitoring stack, IdP, MinIO, a worker-container scheduler, or K8s/HA — all deferred by decision and *documented as such* here (that documentation IS the AC 3 deliverable). No UI work; if any incidental UI surface is touched, the prototype's light palette remains canonical per Story 1.1's design-token ruling.

### Architecture compliance (binding ADs for this story)

- **AD-15:** the merge-to-main stage (full `e2e/` suite + sprint-status↔spec bijection lint, anchored tag greps) is this story's headline deliverable; the PR stage (declared story specs + `@smoke`) must already be enforced — verify.
- **Conventions (Testing & CI row):** the exact CI gate composition this story completes — treat that row as the checklist of record for Task 1.
- **Conventions (Operations row):** health endpoint on every container; compose healthchecks gate startup order; this story verifies both across dev and prod.
- **AD-5:** prod ollama stays internal-only with GPU config and env-driven model names; no port published, no cloud path — re-verify in the finalized prod profile.
- **AD-14:** startup gating must not create a hard api→ollama runtime dependency — degradation paths keep working when ollama later dies; the healthcheck governs boot order only.
- **AD-11 / NFR-5:** prod profile carries the encrypted-volume prerequisite, TLS (8.2), and encrypted off-host backups (8.3) — this story assembles and boot-proves them together.
- **Conventions (Config & secrets):** prod secrets from the host secret store; env templates only in VCS.

### Data notes

- **No new tables, roles, or migrations.** This story writes CI workflow config, compose profiles, deployment docs, and one e2e spec. **Write-owner note (AD-12):** no entity writes; no service ownership changes. The fresh-DB Alembic CI job now exercises the complete migration chain (1.2 → 8.2) — any ordering or drift defect it exposes is fixed in the offending migration's story style (amend forward, never edit applied migrations).

### Testing requirements

- The story's product **is** the test gate; its own verification is meta: every Task 1 element demonstrably runs and is a required check (capture the required-checks configuration in the docs or repo settings-as-code); the bijection lint has its own unit test (orphan spec ↔ orphan story key both fail); the clean-host boot and health verification are executed and recorded.
- Full `e2e/` suite green on the merge-to-main stage against the freshly reset e2e stack — this story cannot merge without the very gate it finishes.
- **AD-15 Playwright spec:** `e2e/stories/8-4-complete-quality-operations-gate.spec.ts` tagged `@story:8-4 @epic:8`. This story is ops/CI-only with no UI surface, so the spec is a thin structural check through the browser (reasoning: CI wiring and prod-compose assembly cannot be asserted from inside a Playwright run; the browser-observable invariant this story owns is "the composed stack is healthy end to end" — the same invariant the clean-host runbook verifies). Design: drive the browser to the console via nginx, assert the SPA shell + login render, assert `/api/healthz` answers healthy through the proxy, and log in as one persona to a rendered workspace — tag that happy path `@smoke`. The story cannot move to `review`/`done` until this spec passes against the freshly reset stack — and until the full-suite merge stage it introduces is green.

### Project Structure Notes

- CI workflow: extend the GitHub Actions workflow established in Story 1.1 (provider choice documented there); the bijection lint is a small script living with the e2e project (e.g. `e2e/` or repo `scripts/`), reading `_bmad-output/implementation-artifacts/sprint-status.yaml` — read-only; CI never mutates sprint status.
- Prod compose material under `deploy/` (`compose.yaml` + prod/GPU overlay per 1.1/6.1 layout; nginx TLS from 8.2; backup from 8.3); deployment runbook + deferred-decision registry in `deploy/` docs alongside `RESTORE-DRILL.md`.
- The spine's own build roadmap names this phase "Hardening … CI gate complete" — after this story, the AD-15 full-suite floor is the permanent regression bar for all future work; specs are amended, never deleted.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 8.4]
- CI gate composition (checklist of record): [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Consistency Conventions — Testing & CI]
- AD-15 (PR stage, merge-to-main full suite, bijection lint, anchored greps, done-gate): [Source: ARCHITECTURE-SPINE.md#AD-15]
- Operations conventions (health endpoints, healthcheck gating): [Source: ARCHITECTURE-SPINE.md#Consistency Conventions — Operations]
- Deployment view, environments, GPU/hardware guidance: [Source: docs/Architecture-LINEWORKER.md#8. Deployment & operations]
- Deferred-decision registry source (items + reopening triggers): [Source: ARCHITECTURE-SPINE.md#Deferred; docs/Architecture-LINEWORKER.md#10. Deliberately deferred]
- NFR-7 / NFR-8 text: [Source: _bmad-output/planning-artifacts/epics.md#NonFunctional Requirements]
- CI provider + tooling choices to extend, design-token ruling: [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Project Structure Notes / #Design tokens]
- Epic 8 accepted no-FR deviation: [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Minor Concerns item 2]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
