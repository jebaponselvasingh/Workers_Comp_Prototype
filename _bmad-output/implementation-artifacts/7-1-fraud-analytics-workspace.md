# Story 7.1: Fraud Analytics Workspace

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a data analyst,
I want a dedicated fraud view over the portfolio,
so that fraud concentration and the SIU pipeline are visible and actionable.

## Acceptance Criteria

1. **Given** the analyst workspace, **when** the Fraud section renders, **then** it shows the fraud-score distribution, the flagged-claims list (score ≥ 55 — the same JDM threshold as everywhere else, AD-8/AD-10), and the SIU-review pipeline view (claims with `siu_review` by status/handler) (FR-AN-1).
2. **Given** fraud-indicator aggregation, **when** it computes, **then** indicator narratives from the `ai_insight` fraud kind aggregate into a ranked red-flag frequency view, clearly labeled as AI-cached content with timestamps (AD-10).
3. **Given** fraud-rate breakdowns, **when** they render, **then** rate by injury type, employer, and handler show as sortable charts/tables from scoped aggregates (AD-7).
4. **Given** any fraud chart segment or row, **when** clicked, **then** it drills through to the constituent claims, reusing Epic 5's drill-through views (FR-AN-3 pattern).

## Tasks / Subtasks

- [ ] Task 1: Analyst workspace shell (AC: 1)
  - [ ] Add the analyst workspace surface inside `web/src/features/dashboard/` (the Capability Map places FR-AN-1..6 there — extend, don't create a new top-level feature): analyst role lands on the Epic 5 dashboard plus an analyst-only section navigation; this story ships only the **Fraud** section (Trends = 7.2, Financial = 7.4, segmentation control = 7.3 — leave nav slots unbuilt, not stubbed-broken)
  - [ ] Route/section authorization: the new endpoints are read-only and role-gated to `analyst` in the router dependency (role gates capability; scope gates visibility — AD-7); no mutation endpoint exists in this story
  - [ ] Supervisor login remains exactly the Epic 5 dashboard — no regression to Epic 5 routes or components
- [ ] Task 2: Fraud aggregate endpoints in `services/worklist` (AC: 1, 3)
  - [ ] Score-distribution aggregate (histogram buckets over `fraud_score`); bucket edges and the flagged threshold (55) resolved from JDM parameters (AD-8) — never hardcoded, and identical to the value Epic 5 KPI "Fraud Flags" uses
  - [ ] Flagged-claims list endpoint (`fraud_score ≥ threshold`), cursor-paginated `{items, nextCursor, total?}` per list conventions, server-sorted
  - [ ] SIU pipeline aggregate: claims where the registered `siu_review` derivation is true (call `services/derivations` — AD-10; the flag's hash-bucket demo definition is a documented deferred item, do not redefine it), grouped by claim status and by handler
  - [ ] Fraud-rate breakdowns: rate (flagged / total) by injury type, by employer, by handler — all computed in `services/worklist` behind the repository scope context `(user_id, role, employer_ids | ALL)` (AD-7, AD-1); sortable via `sort` query param
- [ ] Task 3: Fraud-indicator aggregation (AC: 2)
  - [ ] Aggregate `ai_insight` rows of the fraud kind: parse the structured indicator list (Epic 6's fraud-check node writes structured output — coordinate on the stored shape; if a claim's cache row is prose-only or absent, exclude it, never invent indicators) into a ranked red-flag frequency view
  - [ ] Read-only over the cache: `services/rag` owns `ai_insight` writes (AD-12) — this story only reads; no refresh is triggered here
  - [ ] Response carries per-indicator claim counts plus the oldest/newest `generated_at` so the UI can label the view as AI-cached content with timestamps (AD-10)
  - [ ] Defined empty state when no fraud-kind cache rows exist in scope (NFR-3) — e.g. before Epic 6 insights have been generated for these claims
- [ ] Task 4: Fraud section UI (AC: 1, 2, 3)
  - [ ] Recharts distribution chart + sortable tables reusing Epic 5's established dashboard idioms (KPI cards, chart cards, ranked tables — UX-DR7); status colors ride the Story 1.1 ok/warn/error tokens
  - [ ] AI red-flag view visually distinguished as cached AI content with its generation timestamps (AD-10) — never presented as claim data
  - [ ] Loading, empty, and error states on every card/table (NFR-3); TanStack Query with keys added to the shared `queryKeys` module (AD-9)
- [ ] Task 5: Drill-through wiring (AC: 4)
  - [ ] Every chart segment / table row navigates to Epic 5's Story 5.5 filtered claim-list view with the fraud filter applied and visible/clearable; claim rows open the existing read-only claim view (no edit affordances — role gates capability)
  - [ ] Drill-through URLs restore filter state on direct load with scope re-resolved server-side (AD-7)
- [ ] Task 6: Tests (AC: all)
  - [ ] pytest: aggregate correctness over seeded claims; JDM threshold resolution (change the JDM value in a test fixture → distribution/flagged list follow); scoped-analyst vs `scope_all` results; indicator aggregation incl. absent/malformed cache rows
  - [ ] Vitest: Fraud section renders states (loading/empty/error/data); AI-cache labeling present
  - [ ] Playwright `e2e/stories/7-1-fraud-analytics-workspace.spec.ts` tagged `@story:7-1 @epic:7`, one `@smoke` happy path: login as the analyst persona → Fraud section renders distribution + flagged list → click a segment → drill-through claim list → read-only claim view

## Dev Notes

> **Prerequisite advisory: run `bmad-ux` for the analyst workspace surfaces before starting this epic (readiness report, standing advisory #1). If a UX spec exists by dev time, it supersedes the layout suggestions here.** The prototype renders the analyst as a supervisor clone — there is no design contract for the fraud workspace; where layout is open below, reuse Epic 5's established dashboard patterns rather than inventing new idioms.

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

The first analyst-differentiating surface: the workspace shell + the Fraud section only. It is **not**: trends/cohorts (7.2), the 9-dimension segmentation control (7.3), financial decomposition (7.4), export (7.5) — leave those sections out entirely. It is **not** fraud-score modeling: `fraud_score` values are the **static seeded values** from the prototype dataset; a real scoring model and SIU workflow are explicitly deferred (spine Deferred list) — task no modeling, no score recomputation, no SIU state machine. It is **not** a new insight generator: the red-flag view only aggregates what Epic 6's `services/rag` already cached. Dashboard-scope copilot stays deferred — no copilot pane on this surface.

### Architecture compliance (binding ADs for this story)

- **AD-1:** every number on the fraud screens comes from a `services/worklist` aggregate endpoint — zero client-side computation over raw claims.
- **AD-7:** every aggregate and the flagged list run behind the repository scope context; role (`analyst`) gates capability on the new read-only routes, scope gates visibility — no role-based filter bypass, no caller-supplied scope.
- **AD-8:** the 55 threshold and distribution bucket edges are JDM parameters — the same document Epic 5's fraud KPI reads; changing JDM changes both.
- **AD-10:** `siu_review` comes from its one registered derivation; fraud narratives come from the `ai_insight` cache, displayed with `generated_at`, never editable, never confused with claim columns.
- **AD-12:** this story writes nothing; `ai_insight` stays owned by `services/rag`, claims by `services/claims` — read-only consumption only.
- **AD-9:** TanStack Query for all server state, keys in the shared `queryKeys` module; no optimistic anything (read-only surface).
- **AD-15:** story spec + done-gate (see Testing requirements).

### Data notes

Creates **no tables**. Reads: `claim` (seeded `fraud_score`, `fraud_flag`, status, injury type, handler, employer FK), `employer`, `app_user` (handler names), `ai_insight` (kind = fraud; owned by `services/rag` per AD-12), derived `siu_review` via `services/derivations`. All aggregates live in `services/worklist` (Capability Map: FR-AN-1..6 → "same aggregates" as the supervisor dashboard).

### UX notes

No UX-DR covers this screen (the Epic 7 gap). Borrow: UX-DR7 (Recharts dashboard idioms — chart cards, ranked tables, clickable segments/rows for drill-through), UX-DR11 (toasts/inline errors; loading/empty/error states everywhere), UX-DR12 as amended by Story 1.1 (⚠️ the epics' "dark console aesthetic" wording is a **documented discrepancy** — the prototype palette is LIGHT and canonical; use the Story 1.1 Tailwind tokens, do not invent a dark theme).

### Testing requirements

- pytest on aggregate endpoints: correctness, JDM-parameter resolution, scope enforcement (scoped analyst persona vs `scope_all`), cursor pagination contract, indicator-aggregation edge cases (no cache rows, partial coverage).
- Vitest on the Fraud section states and AI-cache labeling.
- Playwright `e2e/stories/7-1-fraud-analytics-workspace.spec.ts` tagged `@story:7-1 @epic:7` (anchored grep — `7-1` must not match a future `7-10`), exactly one `@smoke` happy path, run against the freshly reset e2e compose stack with the seeded analyst persona. **The story cannot move to review/done until this spec passes (AD-15 done-gate).**

### Project Structure Notes

- UI extends `web/src/features/dashboard/` (per the Capability → Architecture Map — analyst workspace shares the dashboard feature and its aggregates); a `dashboard/analyst/` subfolder keeps it tidy without changing the fixed tree.
- Server code: aggregates in `server/services/worklist/`, routes in `server/api/` (thin), derivations consumed from `server/services/derivations/`. No new packages.
- Reuse Story 5.5's drill-through list/read-only claim view components — do not fork them; if they need a new filter param, extend them in place (and amend `5-5`'s spec only if behavior legitimately changes, per AD-15 amendment rule).

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 7.1]
- FR-AN-1, FR-AN-3: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]
- Epic 7 UX gap + advisory: [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Standing Advisories item 1]
- Scoping/capability, JDM tier, derived values, ownership registry: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-7 / #AD-8 / #AD-10 / #AD-12 / #Capability → Architecture Map]
- Fraud-score modeling deferred; dashboard-copilot deferred: [Source: ARCHITECTURE-SPINE.md#Deferred]
- `ai_insight` structured fraud indicators: [Source: docs/Architecture-LINEWORKER.md#5.3 RAG design]
- E2E gate: [Source: ARCHITECTURE-SPINE.md#AD-15]
- Design-token ruling (light palette canonical): [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
