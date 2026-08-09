# Story 2.3: Audited Inline Field Editing

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want to correct clinical and classification fields inline,
so that the case file stays accurate with a full audit trail.

## Acceptance Criteria

1. **Given** an investigation-stage claim, **when** the handler edits injury type, cause, body part, ICD-10, disability, or recovery window inline, **then** the change goes through a PATCH-shaped service command carrying `expected_version`, is compare-and-swapped, and emits an audit event with before/after diffs in the same transaction (AD-4; FR-DET-2, FR-H-2, NFR-1).
2. **Given** a concurrent edit (version mismatch), **when** the command runs, **then** it returns 409 problem+json with the fresh entity, and the SPA rolls back the optimistic value and renders the fresh state inline at the edited field — no silent retry (AD-9).
3. **Given** a successful edit of a field feeding derived values, **when** the mutation completes, **then** dependent computations (risk, severity band) recompute via their single derivation functions and affected TanStack Query keys invalidate so queue and detail re-render consistently (FR-DET-2, AD-10).
4. **Given** any inline edit, **when** it commits, **then** a `timeline_event` row is emitted by the owning service command (AD-12) and appears in the Overview timeline.

## Tasks / Subtasks

- [ ] Task 1: PATCH-shaped CAS command in `services/claims` (AC: 1, 4)
  - [ ] `update_claim_fields(caller_ctx, claim_business_id, expected_version, patch)` — patch carries **edited fields only** from the whitelist: `injury_type`, `cause`, `body_key` (+ derived `body_part` label server-side, mirroring the prototype's key→label mapping), `icd` (+ `icd_desc` if supplied), `disability` (enum `temporary | permanent`), `recovery` (enum of the 5 recovery windows); anything else in the patch → 422 problem+json
  - [ ] CAS per the Write-concurrency convention: `WHERE id = ? AND version = ?`, increment `version` on success — no unguarded read-modify-write; on mismatch raise the conflict carrying the freshly-read entity
  - [ ] In the **same transaction**: emit an `audit_event` row with the fixed AD-4 schema `(id, at, actor_id, actor_role, action, entity, entity_id, before, after)` — `before`/`after` as JSONB diffs of only the edited fields (never the whole row; audit diffs are PHI-class, AD-11)
  - [ ] In the same transaction: emit a `timeline_event` row describing the edit (e.g. desc "Injury details updated (injury type, ICD-10)", tag "Edit") — `services/claims` is the sole `timeline_event` writer (AD-12)
  - [ ] No router touches a SQLAlchemy session for writes — the command is the only write path (AD-4)
- [ ] Task 2: API endpoint + error contract (AC: 1, 2)
  - [ ] `PATCH /api/claims/{claimBusinessId}` accepting `{expectedVersion, ...editedFields}` (camelCase via Pydantic alias); behind the AD-7 scope context; role-gates capability (handlers edit; supervisor/analyst read-only — role gates capability, scope gates visibility)
  - [ ] Success → 200 with the updated entity (new `version`, freshly derived `risk`/severity band from `services/derivations`)
  - [ ] Version mismatch → **409 RFC 9457 problem+json carrying the fresh entity** in the problem body so the SPA can render it without a second round-trip; validation failures → 422 problem+json (mapped to inline messages, never a blocking dialog — NFR-3)
  - [ ] Regenerate the OpenAPI client
- [ ] Task 3: Inline-edit UI in the investigation Overview injury card (AC: 1, 2, 3)
  - [ ] Replace 2.2's read-only injury-card values with inline controls: text inputs for injury type / cause / ICD-10 (widths per the prototype's dense style), selects for body part (the 11 `BODY_PART_OPTIONS`), disability (Temporary/Permanent), recovery window (5 options) [Source: docs/Workers_Comp_Prototype.html lines 648–667, 1286–1292]
  - [ ] TanStack mutation with **optimistic update for the edited user-entered scalar only** (AD-9); server-derived values (risk, severity band, priority) always wait for the server
  - [ ] On success: invalidate the affected `queryKeys` — `claimDetail(claimId)` and the queue list keys — so queue card and detail re-render from one server truth (AC 3); the new timeline event appears in the Overview timeline without a manual refresh
  - [ ] On 409: roll back the optimistic value, render the returned fresh entity's value inline at the edited field with a visible conflict notice ("Updated by someone else — showing latest") and refresh the tracked `version`; **no silent retry, no client-side merge** (AD-9)
  - [ ] On 422: inline validation message at the field (NFR-3)
- [ ] Task 4: Tests (AC: all)
  - [ ] Unit (server): whitelist enforcement; CAS success increments version; CAS mismatch 409 with fresh entity; audit event + timeline event committed in the same transaction as the field change (assert rollback removes all three); audit diff contains only edited fields; role-gate (supervisor PATCH → 403)
  - [ ] Unit (server): editing `disability` or `body_key` recomputes derived values through the registered derivation functions only (spy/registry assertion, AD-10)
  - [ ] Vitest: optimistic render → server settle; 409 rollback renders fresh value inline; 422 inline message
  - [ ] E2E: see Testing requirements
- [ ] Task 5: E2E story spec (AC: all)
  - [ ] `e2e/stories/2-3-audited-inline-field-editing.spec.ts` tagged `@story:2-3 @epic:2`
  - [ ] `@smoke` happy path: handler selects an investigation-stage seeded claim → edits injury type inline → value persists across reload → timeline card shows the edit event → queue card still agrees with detail
  - [ ] Conflict test: simulate the stale write by issuing a direct API PATCH (via request context) between read and save → UI shows the 409 fresh-state inline rendering, no native dialog
  - [ ] Audit test (API-level assertion within the spec or a pytest integration): the edit produced exactly one audit_event with before/after diffs

## Dev Notes

### What this story is — and is not

This story establishes the **first AD-4 audited CAS write path of the build** — the pattern every later mutation (2.4 injuries, 3.x financial commands, 4.x diary) copies. Scope is exactly the six clinical/classification fields on the investigation Overview injury card. It does **not** include: severity-score or body-map editing (2.4 — note `body_key` editing appears in both surfaces; 2.4 reuses this story's command), comp-rate override (3.1), benefit/reserve recalculation (Epic 3 — the AC's "dependent calculations" here are the derivations that already exist: risk and severity band), approval/status transitions (3.5), or any copilot-proposed write (Epic 6). The prototype's `editText`/`editSelect`/`updateClaimField` globals are behavior reference only — never code.

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### Architecture compliance (binding ADs for this story)

- **AD-1:** the SPA captures input only; validation, derivation, and persistence are server-side.
- **AD-4:** PATCH-shaped command, `expected_version` CAS, same-transaction fixed-schema audit event with JSONB before/after diffs; app DB role is INSERT-only on `audit_event` (grants from 1.2); no unguarded read-modify-write.
- **AD-7:** scope enforced in the repository; role gates the edit capability (read-only dashboards stay read-only).
- **AD-9:** optimistic update for the edited scalar only; 409 → rollback + inline fresh-state render, no silent retry; invalidation via the shared `queryKeys` module.
- **AD-10:** risk/severity band recompute only through their registered derivation functions — the command calls them, the response carries them, the SPA never re-derives.
- **AD-11:** audit diffs are PHI-class — diff only edited fields; structlog lines carry IDs and event names, never field values.
- **AD-12:** `services/claims` owns `claim` writes and is the sole `timeline_event` emitter — the event rides the owning command, never a second writer.
- **AD-15:** story spec is the done-gate (see Testing requirements).

### Data notes

- **No new tables.** Writes `claim` (whitelisted columns + `version`), inserts `audit_event` (1.2) and `timeline_event` (2.2). First runtime emitter of `timeline_event`.
- Enum casing: `disability` and `recovery` stored as snake_case enums; UI owns the display labels ("Temporary", "6-8 Weeks") per conventions. The prototype's display strings are the label set [Source: docs/Workers_Comp_Prototype.html line 655].
- Body-part key→label mapping (11 options) becomes reference data or a service constant shared with 2.4's diagram — one source, since the SVG coordinate dictionary keys off the same `body_key` set.
- Write-owner (AD-12): `claim`, `timeline_event` → `services/claims`; `audit_event` → written by the command through the audit helper (`services/audit` owns the schema/purge, commands insert in-transaction per AD-4).

### UX notes

- UX-DR5/UX-DR3: edits live inline in the investigation Overview injury card (2.2's component) — terse dense inputs matching the prototype's `.editfield` feel, "(editable)" hint on the card title.
- UX-DR11/NFR-3: no native dialogs anywhere — 409 conflict and 422 validation render inline at the field; a toast may accompany the conflict but never blocks.
- UX-DR12 + Story 1.1 ruling: prototype palette is **LIGHT and canonical** ("dark console aesthetic" in epics.md is a documented discrepancy); inputs and notices ride the 1.1 tokens.
- Keep focus behavior sane: saving on blur/enter, no full-pane re-render that steals focus mid-edit (the prototype's full `renderDet()` redraw is exactly what React + targeted invalidation replaces).

### Testing requirements

- Unit/property tests the ACs demand: CAS semantics (success/mismatch), same-transaction atomicity of field+audit+timeline, whitelist, role-gate, derivation-registry usage. Property test (Hypothesis): for any whitelisted patch, audit `before`/`after` keys equal the patch keys and `after` matches the persisted row.
- Web: optimistic/rollback/inline-conflict behaviors.
- E2E (AD-15): `e2e/stories/2-3-audited-inline-field-editing.spec.ts` tagged `@story:2-3 @epic:2`, one `@smoke` happy path, per-spec DB reset; conflict path covered. **The story cannot move to `review`/`done` until this spec passes.**

### Project Structure Notes

- Server: command in `server/services/claims/`; audit-event helper shared from `server/services/audit/`; router in `server/api/`; problem+json exception handlers extended for the 409-with-entity shape (establish once, reuse everywhere).
- Web: edit controls inside `web/src/features/claim-detail/overview/InvestigationOverview.tsx` (+ a reusable `InlineEditField` component — later stories reuse it); mutations + invalidation in the feature's hooks; keys in `web/src/api/queryKeys`.
- The 409-carrying-fresh-entity problem+json shape defined here is the project-wide contract (conventions row "Write concurrency") — document it in the OpenAPI schema so Epic 3+ commands inherit it.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 2.3]
- AD-4 full text (fixed audit schema, CAS, same-transaction): [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-4]
- AD-9 (optimistic scalars, 409 rollback + inline fresh state): [Source: ARCHITECTURE-SPINE.md#AD-9]
- AD-10 / AD-12 / AD-7 / AD-11: [Source: ARCHITECTURE-SPINE.md#Invariants & Rules]
- Write-concurrency + errors conventions (409 problem+json with fresh entity): [Source: ARCHITECTURE-SPINE.md#Consistency Conventions]
- Narrative on concurrent-edit UX: [Source: docs/Architecture-LINEWORKER.md#4.2 Key data rules / #6 Frontend architecture]
- Prototype editable fields + options (behavior/data reference): [Source: docs/Workers_Comp_Prototype.html — BODY_PART_OPTIONS 648, RECOVERY_OPTIONS 655, editText/editSelect/updateClaimField 659–681, investigation card 1286–1292]
- Audit NFR: [Source: _bmad-output/planning-artifacts/epics.md#NonFunctional Requirements NFR-1]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
