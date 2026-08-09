# Story 2.2: Case Header & Stage-Adaptive Overview

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want the case file to open on a stage-appropriate overview,
so that I see what matters for where the claim is in its lifecycle.

## Acceptance Criteria

1. **Given** a selected claim, **when** the detail pane renders, **then** the header shows worker name, meta line (claimId · role · employer · state), injury summary, badge row (stage pill + conditional Fraud Score / Litigation / Surgery / OSHA), and the semicircular risk-gauge SVG colored by risk band (UX-DR4).
2. **Given** the 6-tab bar (UX-DR5), **when** Overview renders, **then** it always begins with the 4-step stage stepper marking done/current steps (FR-DET-1).
3. **Given** each lifecycle stage, **when** Overview renders, **then** content adapts — intake: summary + reported injury + document checklist (Received/Missing per required form) + timeline; investigation: injury card + financials/reserve card with cost bar + timeline; treatment: phase banner (Early/Active/Approaching-MMI) + paid-vs-reserve card with Bills jump-link + care & RTW coordination status + recent timeline; settled: settled banner + final payout breakdown + outcome card + full action summary (FR-DET-1).
4. **And** `timeline_event` and `document` tables are created by this story's migration and seeded from prototype data.
5. **Given** treatment-phase and coordination-status values, **when** displayed, **then** each is server-derived by a registered derivation (AD-10), never computed in the SPA.

## Tasks / Subtasks

- [ ] Task 1: Migration — `timeline_event` + `document` tables, seeded (AC: 4)
  - [ ] Alembic migration creating `timeline_event` (surrogate int PK, `claim_id` FK, `event_date` (ISO date), `description`, `tag`; **append-only** — no `version` column, exempt from CAS per the Write-concurrency convention) and `document` (surrogate int PK, `claim_id` FK, `name`, `doc_type` snake_case enum covering the prototype's FROI/INCIDENT/MEDAUTH/WAGE/RTW/LEGAL set, `filed_date`, `blob_key` nullable — bytes ride the `BlobStore` protocol when real files exist, viewer arrives 2.5)
  - [ ] Seed both from each prototype claim's `timeline` (`{date, desc, tag}`) and `documents` (`{name, type, date}`) arrays — DATA source only, never code [Source: docs/Workers_Comp_Prototype.html ALL_CLAIMS line 632]
  - [ ] Snake_case naming per the Excel canonical mapping; migration runs clean on a fresh DB (CI gate)
- [ ] Task 2: Registered derivations — treatment phase + coordination status (AC: 3, 5)
  - [ ] `services/derivations.treatment_phase(claim)` → `early | active | approaching_mmi` (+ server-provided note text): port the prototype rule — expected-days from the recovery window (upper bound × 7; ≥1-year → 180; default 42), phase by `days_open / expected_days` at < 0.3 / < 0.7 / else — with the 0.3/0.7 boundaries and day constants as JDM parameters (AD-8) [Source: docs/Workers_Comp_Prototype.html lines 1310–1318]
  - [ ] `services/derivations.coordination_status(claim)` → `coordination_gap | awaiting_information | legal_coordination | on_track` (+ note): rtw_blocked → gap; comm-status matches "Need for Additional Information / Incomplete Information" → awaiting; litigation_flag → legal; else on-track [Source: docs/Workers_Comp_Prototype.html lines 1319–1324]
  - [ ] Both registered in `services/derivations` (AD-10) and returned in the API payload; unit tests over all branches
- [ ] Task 3: Claim-detail API (AC: 1, 2, 3, 5)
  - [ ] `GET /api/claims/{claimBusinessId}` (route by `WC-nnnn` per ID conventions) behind the AD-7 scope context — 404/403-safe for out-of-scope IDs (do not leak existence)
  - [ ] Payload: header block (name, role, employer, state, injury summary line, stage, conditional fraud-score/litigation/surgery/OSHA badge data, risk band + `version` for later CAS stories), stage-variant overview block (per-stage fields incl. derived phase/coordination values and their notes), timeline events (settled/intake/investigation: full; treatment: server marks the recent-6 slice), document checklist (see Task 5), and money fields as integer cents (format in UI only)
  - [ ] Regenerate the OpenAPI client; register `claimDetail` keys in `queryKeys` (AD-9)
- [ ] Task 4: Case header + tab shell — `web/src/features/claim-detail/` (AC: 1, 2)
  - [ ] `CaseHeader`: h1 worker name; meta line `claimId · role · employer · state` (mono claim ID); 🩹 injury summary line (injury type — body part, cause · ICD-10 · severity); badge row: stage pill + conditional `⚠ Fraud Score: n` / `⚖ Litigation` / `🔪 Surgery` / `OSHA Rec.` badges
  - [ ] `RiskGauge`: port the prototype's semicircular SVG as a typed React component — 100×56 viewBox, background arc + risk-colored arc (`stroke-dasharray` 115/75/35 over 132 for high/med/low), centered risk label, er/wn/ok token colors [Source: docs/Workers_Comp_Prototype.html line 1187]
  - [ ] Tab bar with the 6 tabs: Overview · Injury Diagram · Bills & Payments · Documents & ID · Photos · AI Insights; tab state is local UI state; **cross-story seams render explicit empty states** — Bills & Payments ("Financial detail arrives with the financial engine") until Epic 3, AI Insights until Epic 6, Injury Diagram until 2.4, Documents & ID until 2.5, Photos until 2.6 (no count suffix in the label until 2.6 adds the `photo` table) (NFR-3)
  - [ ] `StageStepper`: 4 steps Intake → Investigation → Treatment → Settled with done/current styling; always the first element of the Overview tab [Source: docs/Workers_Comp_Prototype.html lines 1201–1205]
- [ ] Task 5: Four stage-variant Overview components (AC: 3)
  - [ ] `IntakeOverview`: intake-summary card (employee, role/plant, DOI, FROI filed, assigned, handler, communication status) + reported-injury card (injury type, cause, body part, initial severity, AWW, initial reserve) + **intake document checklist**: Received/Missing row per required document, computed server-side by comparing the claim's `document` rows against a service-owned required-document baseline (the prototype's doc-type set FROI/INCIDENT/MEDAUTH/WAGE as a JDM-parameterized list — see Dev Notes seam: `path_required_form` reference data does not exist until 2.5 and is a different surface) + full timeline card
  - [ ] `InvestigationOverview`: editable-injury card **rendered read-only this story** (inline editing is 2.3 — display values only, no inputs) + financials/reserve card (total incurred, reserve, policy number, fraud score, severity score, indemnity/medical cost bar with legend when paid > 0, "Active — payments pending" otherwise) + full timeline card
  - [ ] `TreatmentOverview`: phase banner (derived label Early Treatment & Diagnosis / Active Treatment & Therapy / Approaching MMI / RTW Planning, note, day-N-of-claim line) + paid-vs-reserve card **from claim-level financial fields** (paid_medical, paid_indemnity, reserve — bill/schedule tables arrive Epic 3; reserve-check verdict line renders a "verdict arrives with the financial engine" placeholder until 3.2) with "View full Bills & Payments →" jump-link switching to the Bills tab (which shows its Epic-3 empty state) + care & RTW coordination card (derived status label + note, return status, handler, supervisor) + recent timeline (server-marked last 6)
  - [ ] `SettledOverview`: ✅ settled banner (settlement date from the settlement-tagged timeline event, total incurred) + final payout breakdown (indemnity/medical/expense/total/final reserve + 3-segment cost bar) + claim-outcome card (disability, return status, days to settlement, litigation, handler) + full "Summary of actions taken" timeline
  - [ ] All figures and derived strings come from the API payload — no TS re-derivation (AD-1/AD-10); loading/error/empty states throughout (NFR-3)
- [ ] Task 6: Tests + E2E spec (AC: all)
  - [ ] Unit: phase/coordination derivations all branches; checklist Received/Missing logic; timeline slicing
  - [ ] Vitest: header badges conditional rendering; gauge band colors; stepper done/current for each stage; one render test per stage variant; seam empty states
  - [ ] `e2e/stories/2-2-case-header-stage-adaptive-overview.spec.ts` tagged `@story:2-2 @epic:2`; `@smoke` happy path: select a treatment-stage seeded claim → header + gauge + stepper render → phase banner and coordination card show derived values → Bills jump-link lands on the Bills tab empty state; additional tests select one seeded claim per remaining stage and assert its variant's signature cards + timeline

## Dev Notes

### What this story is — and is not

This story turns Story 2.1's selection into a real case file: header, gauge, 6-tab shell, stepper, and the four Overview variants, plus the `timeline_event`/`document` tables. It contains **no editing** — 2.3 adds audited inline edits (render the investigation injury card read-only), 2.4 the injury diagram and severity editing, 2.5 the documents/forms tab content and viewers, 2.6 photos. **No financial engine**: benefit card, reserve-check verdict, bills, payment schedule, and the Upcoming Actions block are Epic 3 — the prototype's overview builders embed `benefitCardHTML`/`actionsBlockHTML`; those regions are omitted (or given explicit "arrives with the financial engine" placeholders) this story, and Epic 3 slots them into these same variant components. AI Insights is Epic 6. Readiness report flags this story DENSE (four variants) — the task decomposition above is deliberately fine-grained; keep each variant a separate component and commit.

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### Architecture compliance (binding ADs for this story)

- **AD-1:** every figure and derived string in the pane comes from the detail endpoint; the SPA renders payloads.
- **AD-7:** detail endpoint enforces repository-layer scope; an out-of-scope `WC-nnnn` must not resolve.
- **AD-8:** phase boundaries (0.3/0.7), expected-days constants, and the intake required-document list are JDM parameters; the branching logic is typed Python.
- **AD-9:** `claimDetail` in the shared `queryKeys` module; tab selection is local UI state; read-only story — no optimistic updates.
- **AD-10:** `treatment_phase` and `coordination_status` are registered derivations; risk band shown by the gauge is the same `risk` derivation 2.1 registered — queue dot and gauge can never disagree.
- **AD-12:** `timeline_event` rows are emitted only by owning-service commands; this story only **seeds** (migration) and **reads** — the first runtime emitter is 2.3's edit command. `document` writes belong to `services/claims` (Capability Map: stage-adaptive claim detail).
- **AD-15:** story spec is the done-gate (see Testing requirements).

### Data notes

- Creates + seeds: `timeline_event` (append-only, CAS-exempt), `document` — seeded from each ALL_CLAIMS entry's `timeline`/`documents` arrays; ~100 claims' worth. Prototype data is a DATA source only.
- Uses: `claim` (incl. paid_indemnity/paid_medical/paid_expense/reserve/aww in integer cents from 1.2), `employer`, scope tables.
- Write-owner (AD-12): both new tables belong to `services/claims`.
- **Seam — intake document checklist:** epics.md AC says "Received/Missing per required form", but `path_required_form` reference data is created in **2.5** (a later story; forward dependencies are banned) and feeds a *different* surface (the statutory-forms card on the Documents & ID tab). Resolution: the intake checklist compares `document` rows against a JDM-parameterized required-document baseline owned by `services/claims`. This checklist also has **no prototype precedent** — the prototype's intake overview has no checklist — so the epics AC is the contract for it. Both points are flagged as discrepancies in this batch's creation report.
- **Seam — treatment paid-vs-reserve card:** prototype computes it from `buildBills`/`buildPaymentSchedule`; those tables arrive 3.3. Render from claim-level paid/reserve columns now; 3.3 upgrades the source and 3.2 fills the verdict line. Figures must match Epic 3's later surfaces because both read the same claim columns via the same derivations (AD-10).

### UX notes

- UX-DR4 (case header + risk gauge), UX-DR5 (6-tab pane + stage-adaptive variants + 4-step stepper) govern this story; UX-DR3's center-pane placement follows 2.1's layout.
- UX-DR12 + Story 1.1 ruling: prototype palette is **LIGHT and canonical**; "dark console aesthetic" in epics.md is a documented discrepancy — do not build a dark theme. Stage pills, badges, cost bars, and the gauge use the ok/wn/er/st/ac tokens from 1.1.
- NFR-3: loading skeleton for the pane, error state, and explicit empty states on every not-yet-built tab; timeline card shows a "No timeline events on file." empty state.
- Visual reference for every card: prototype builders `intakeOverviewHTML` (1325), `investigationOverviewHTML` (1282), `treatmentOverviewHTML` (1348), `settledOverviewHTML` (1381) — replicate layout/feel with shadcn/ui + tokens, not their code.

### Testing requirements

- Unit tests the ACs demand: `treatment_phase` (all three phases + recovery-window parsing edge cases: no match, "Greater than 1 Year"), `coordination_status` (all four branches, precedence order), checklist derivation (received, missing, mixed), timeline recent-6 slicing.
- Property (Hypothesis): `treatment_phase` total over the days-open × recovery-window domain (never throws, always one of three values).
- Scope test: out-of-scope claim detail request fails safely.
- E2E (AD-15): `e2e/stories/2-2-case-header-stage-adaptive-overview.spec.ts` tagged `@story:2-2 @epic:2`, one `@smoke` happy path, per-spec DB reset. **The story cannot move to `review`/`done` until this spec passes.**

### Project Structure Notes

- Server: migration in `server/data/` versions; derivations in `server/services/derivations/`; detail assembly in `server/services/claims/`; router in `server/api/`.
- Web: `web/src/features/claim-detail/` — `CaseHeader.tsx`, `RiskGauge.tsx`, `StageStepper.tsx`, `DetailTabs.tsx`, `overview/{IntakeOverview,InvestigationOverview,TreatmentOverview,SettledOverview}.tsx`.
- Conventions: business-ID routing (`/api/claims/WC-1234`), camelCase JSON, integer cents formatted only in UI, snake_case enums with UI labels.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 2.2]
- Epic 2 seams (Bills → Epic 3, AI Insights → Epic 6): [Source: _bmad-output/planning-artifacts/epics.md#Epic 2]
- AD-1 / AD-7 / AD-8 / AD-9 / AD-10 / AD-12 / AD-15: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- ERD (`timeline_event`, `document` under CLAIM): [Source: ARCHITECTURE-SPINE.md#Core entity ERD]
- ID/dates/money/enum conventions: [Source: ARCHITECTURE-SPINE.md#Consistency Conventions]
- Prototype design contract: [Source: docs/Workers_Comp_Prototype.html — renderDet/gauge line 1183–1199, stepper 1201, ovHTML dispatch 1275, stage builders 1282/1325/1348/1381, treatmentPhase 1310, coordinationCheck 1319, ALL_CLAIMS 632]
- DENSE-story advisory: [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Story Sizing Assessment / #Standing Advisories item 4]
- Design-token light-palette ruling: [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
