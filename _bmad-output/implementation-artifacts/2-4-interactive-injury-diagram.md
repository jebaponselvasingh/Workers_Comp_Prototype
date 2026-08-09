# Story 2.4: Interactive Injury Diagram

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want a body-map of the worker's injuries I can edit,
so that clinical severity is captured visually and accurately.

## Acceptance Criteria

1. **Given** the Injury Diagram tab, **when** it renders, **then** the ported body-silhouette SVG shows severity-colored markers at injured regions with the primary injury pulsing (UX-DR6), plus ICD-10 diagnosis, prognosis, numbered treatment plan (from `treatment_plan_step` rows, table created and seeded here), and restrictions cards.
2. **Given** the "+ Add another injury" popover, **when** body part + injury type + severity (0–100) are submitted, **then** an `additional_injury` row is created via an audited command (table created here), a secondary marker renders, and secondaries are removable via ✕ with audit (FR-H-6).
3. **Given** the editable severity score or body-part selector, **when** changed, **then** the audited command updates the claim and severity band + risk recompute server-side, refreshing gauge, queue card, and stat tiles (FR-H-6, FR-DET-2).
4. **Given** invalid input (severity out of range, missing body part), **when** submitted, **then** inline validation blocks the save with an explanation — no native dialogs (NFR-3).

## Tasks / Subtasks

- [ ] Task 1: Migration — `additional_injury` + `treatment_plan_step`, seeded (AC: 1, 2)
  - [ ] Alembic migration creating `additional_injury` (surrogate int PK, `claim_id` FK, `body_key` enum (the 11 keys), `body_part` label, `injury_type`, `severity_score` int 0–100 CHECK, `version` int — mutable entity per Write-concurrency convention) and `treatment_plan_step` (surrogate int PK, `claim_id` FK, `step_no` int, `description`)
  - [ ] Seed `treatment_plan_step` from each prototype claim's `treatmentPlan` string array (ordered → `step_no`) — DATA source only, never code [Source: docs/Workers_Comp_Prototype.html ALL_CLAIMS line 632, rendered at line 1526]
  - [ ] `additional_injury` seeds **empty** — the prototype's `additionalInjuries` store starts `{}` with no seeded entries (line 656); creating the table with no rows is correct, not an omission (discrepancy vs. the epic's "creates + seeds" phrasing — flagged in the batch report)
  - [ ] If Story 1.2's `claim` schema did not carry the prognosis fields (`mmi`, `rtw`, `impairment`, `litigation` outlook) and `contraindications`, add them in this migration seeded from the prototype's `prognosis` object / `contraindications` string — check 1.2's Dev Agent Record first
- [ ] Task 2: Audited commands in `services/claims` (AC: 2, 3)
  - [ ] `add_additional_injury(caller_ctx, claim_business_id, expected_claim_version, {body_key, injury_type, severity_score})` — server-side validation (severity 0–100 int, body_key in the 11-key enum, injury_type non-empty) → 422 problem+json on failure; insert + same-transaction audit event + timeline event (AD-4/AD-12, pattern from 2.3)
  - [ ] `remove_additional_injury(caller_ctx, claim_business_id, injury_id, expected_version)` — CAS on the injury row; audit event records the removed row as `before` diff
  - [ ] `update_claim_severity(caller_ctx, claim_business_id, expected_version, severity_score)` — extends 2.3's PATCH whitelist (or a dedicated command): clamps nothing client-side, validates 0–100 server-side; on success severity band + risk recompute via the registered derivations with band thresholds (≥70 high / ≥40 med) as JDM parameters (AD-8/AD-10) [Source: docs/Workers_Comp_Prototype.html lines 682–692]
  - [ ] Body-part change from this tab reuses 2.3's `update_claim_fields` command with `body_key` — one command, two surfaces (AD-12: one write path)
- [ ] Task 3: API surface (AC: 1, 2, 3)
  - [ ] Extend the claim-detail payload (2.2's endpoint) with the injury-diagram block: primary marker (claim `body_key` + `severity_score` + injury type/part), additional injuries list (with row `id` + `version`), ICD-10 + description, prognosis, ordered treatment-plan steps, contraindications, and server-derived marker colors band (`high | med | low`) so the SPA never re-derives severity bands (AD-10)
  - [ ] `POST /api/claims/{claimBusinessId}/injuries` and `DELETE /api/claims/{claimBusinessId}/injuries/{id}` (+ severity via the PATCH route) — all behind AD-7 scope + handler role-gate; 409 problem+json with fresh entity on CAS mismatch (2.3 contract); regenerate the OpenAPI client
- [ ] Task 4: `BodyMap` typed React component — SVG port (AC: 1)
  - [ ] Port the prototype's silhouette as a typed component in `web/src/features/claim-detail/`: 130×310 viewBox figure (ellipses/rects per lines 1476–1494) and the **hotspot coordinate dictionary** for the 11 body keys (`head {65,28,18}`, `shoulder_right {98,72,12}`, `shoulder_left {32,72,12}`, `torso {65,100,16}`, `hand_right {116,148,9}`, `hand_left {14,148,9}`, `tibia_left {47,240,11}`, `tibia_right {83,240,11}`, `ears→head`, `forearm_right {110,115,10}`, `lumbar {65,158,13}`; unknown key falls back to torso) [Source: docs/Workers_Comp_Prototype.html line 1463]
  - [ ] Markers: three concentric circles (r+10 @ .12, r+5 @ .22, r @ .88 with white stroke) + crosshair lines, colored by the server-provided severity band (er/wn/ok tokens); primary marker adds the pulsing SMIL/CSS animation ring (expanding r, fading opacity, 1.8s loop) [Source: docs/Workers_Comp_Prototype.html lines 1466–1475]
  - [ ] Props are data-only (markers array from the API payload); no business logic in the component
- [ ] Task 5: Injury tab layout + cards (AC: 1, 3)
  - [ ] Replace 2.2's Injury Diagram empty state: left column — BodyMap + body-part selector (wired to the PATCH command) + ICD code caption; right column — cards: Severity score (editable 0–100 number input + severity bar + band labels Mild/Moderate/Severe + risk arrow), ICD-10 diagnosis (code + description), Prognosis (MMI estimate / RTW outlook / Impairment / Litigation risk), Treatment plan (numbered steps from `treatment_plan_step`), ⚠ Restrictions (contraindications, warning-token styling) [Source: docs/Workers_Comp_Prototype.html lines 1497–1529]
  - [ ] "+" popover: injuries-on-this-claim summary list (primary tagged PRIMARY, secondaries with ✕ remove), divider, add-form (body-part select, injury-type text, severity number defaulting 40) [Source: docs/Workers_Comp_Prototype.html lines 1501–1515]
  - [ ] Mutations per AD-9: optimistic update only for the user-entered scalar (severity number, injury-type text); marker colors/bands/risk wait for the server; on success invalidate `claimDetail`, queue keys, **and the top-bar stat-tile keys** (High Risk count can change — AC 3's "stat tiles" clause); 409 → rollback + inline fresh state (2.3 pattern)
  - [ ] Inline validation before submit and on 422: severity outside 0–100 or missing/empty injury type blocks save with an explanation at the field; no native dialogs (NFR-3)
- [ ] Task 6: Tests + E2E spec (AC: all)
  - [ ] Unit (server): add/remove/severity commands — validation bounds (0, 100, −1, 101, non-int), CAS mismatch 409, audit + timeline same-transaction, severity band/risk derivation registry usage; property test (Hypothesis): for any severity 0–100 the derived band/risk matches the JDM thresholds and never diverges between queue and detail payload assembly
  - [ ] Vitest: BodyMap renders a marker per injury with correct hotspot and band color, primary pulses, fallback hotspot for unknown key; popover validation messages; severity-bar width
  - [ ] `e2e/stories/2-4-interactive-injury-diagram.spec.ts` tagged `@story:2-4 @epic:2`; `@smoke` happy path: open Injury Diagram on a seeded claim → silhouette + primary marker + treatment-plan steps render → add a secondary injury via popover → marker appears → remove it via ✕ → gone after reload; additional tests: severity edit updates gauge + queue card risk dot; out-of-range severity shows inline error and saves nothing

## Dev Notes

### What this story is — and is not

This story delivers the Injury Diagram tab: the ported body-silhouette SVG as a typed React component, multi-injury capture with audited add/remove, and severity/body-part editing that recomputes band + risk server-side. It **reuses** 2.3's command/CAS/audit pattern and `InlineEditField` styling — do not invent a second write idiom. It does **not** recompute benefit or reserve (Epic 3 — the AC's recomputation clause here is severity band + risk only), does not touch documents/photos (2.5/2.6), and adds no copilot behavior. The prototype's `injHTML`/`addInjury` JS is behavior/coordinate reference only — never code. Cause editing lives on the Overview card (2.3); this tab's title shows cause read-only or via the shared component — keep one write path.

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### Architecture compliance (binding ADs for this story)

- **AD-1:** severity band, risk, marker colors — all server-computed; the component renders payload data.
- **AD-4:** every add/remove/edit is a CAS-guarded audited command with same-transaction audit events; removals audit the removed row in `before`.
- **AD-7:** all three endpoints scope-enforced in the repository; handler role-gated for writes.
- **AD-8:** severity-band thresholds (70/40) and the default new-injury severity (40) are JDM parameters; the banding logic is the registered Python derivation.
- **AD-9:** optimistic scalars only; derived values wait; 409 rollback + inline fresh state; invalidation spans claimDetail + queue + stat-tile keys.
- **AD-10:** severity band and risk have exactly one computing function each — gauge (2.2), queue dot (2.1), and stat tiles (1.4) all refresh from the same derivations after this story's edits.
- **AD-12:** `additional_injury`, `treatment_plan_step`, and the claim's severity fields are owned by `services/claims`; timeline events ride its commands.
- **AD-15:** story spec is the done-gate (see Testing requirements).

### Data notes

- Creates: `additional_injury` (mutable, versioned; **seeded empty** — see Task 1 note), `treatment_plan_step` (seeded from prototype `treatmentPlan` arrays; effectively static reference rows this epic — no runtime writer until a later epic needs one).
- Uses: `claim` (severity_score, body_key, prognosis/contraindication fields), `audit_event`, `timeline_event`.
- The 11-entry body-key enum + label mapping is shared with 2.3's body-part select — single source (reference constant or table) consumed by both the server enum and the SVG hotspot dictionary.
- Write-owner (AD-12): everything here → `services/claims`.

### UX notes

- UX-DR6 governs: animated severity-colored markers, pulsing primary, add-injury popover, removable secondary markers, ported SVG assets.
- UX-DR12 + Story 1.1 ruling: prototype palette is **LIGHT and canonical** (epics.md "dark console aesthetic" is a documented discrepancy); marker/band colors ride the er/wn/ok tokens; silhouette fills keep the prototype's neutral greys (#D8DFE6/#D0D8E0/#C8D1D9 with #B0BAC4 strokes) or token equivalents.
- UX-DR11/NFR-3: inline validation only — the prototype's silent-focus rejection on empty injury type becomes an explicit inline message; no `alert()`.
- Popover is non-modal (shadcn/ui Popover), anchored to the "+" button top-right of the silhouette panel, as in the prototype.

### Testing requirements

- Unit/property tests the ACs demand: validation bounds, CAS + audit atomicity, derivation-registry recompute, Hypothesis property over the 0–100 severity domain.
- Vitest: marker rendering/pulse/fallback, validation UX.
- E2E (AD-15): `e2e/stories/2-4-interactive-injury-diagram.spec.ts` tagged `@story:2-4 @epic:2`, exactly one `@smoke` happy path, per-spec DB reset; covers add/remove round-trip, severity-edit propagation to gauge and queue, and the inline-validation path. **The story cannot move to `review`/`done` until this spec passes.**

### Project Structure Notes

- Server: migration in `server/data/` versions; commands in `server/services/claims/`; band thresholds JDM document in `server/rules/` (or extend the existing derivation-parameters document from 2.1 — one rule element, one tier).
- Web: `web/src/features/claim-detail/injury/` — `BodyMap.tsx` (SVG + hotspots), `InjuryTab.tsx`, `SeverityCard.tsx`, `AddInjuryPopover.tsx`; reuse `InlineEditField` from 2.3; keys in `web/src/api/queryKeys`.
- SMIL `<animate>` works in React via lowercase attribute pass-through; if it fights the toolchain, an equivalent CSS keyframe pulse is acceptable — the *visual* contract (expanding, fading ring on the primary) is what's binding.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 2.4]
- AD-4 / AD-8 / AD-9 / AD-10 / AD-12 / AD-15: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- "The SVG body map ports as-is — coordinate dictionary and severity-colored markers become a typed React component": [Source: docs/Architecture-LINEWORKER.md#6. Frontend architecture]
- Capability map row "Body map & injuries": [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map]
- ERD (`additional_injury`, `treatment_plan_step`): [Source: ARCHITECTURE-SPINE.md#Core entity ERD]
- Readiness fix assigning `treatment_plan_step` creation here: [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Defects Found & Remediated item 2]
- Prototype design/coordinate contract: [Source: docs/Workers_Comp_Prototype.html — hotspots 1463, markers 1466–1475, silhouette 1476–1494, popover 1501–1515, cards 1517–1529, updateSevScore 682, addInjury/removeInjury 693–713, BODY_PART_OPTIONS 648]
- Design-token light-palette ruling: [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
