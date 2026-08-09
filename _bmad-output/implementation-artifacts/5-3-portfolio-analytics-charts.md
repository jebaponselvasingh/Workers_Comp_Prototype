# Story 5.3: Portfolio Analytics Charts

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a WC supervisor,
I want distribution analytics across my portfolio,
so that I see where risk, cost, and delay concentrate.

## Acceptance Criteria

1. **Given** the dashboard, **when** charts render, **then** all 7 appear via Recharts (UX-DR7): settlement-status donut, severity donut (High ≥ 65 / Medium / Low), SLA tiles (pass/fail colored), recovery-status bars, injury-type top-8 bars, total-paid-by-employer bars, claims-by-state top-10 bars (FR-SUP-4, FR-SUP-C).
2. **Given** every chart, **when** its data resolves, **then** it comes from scoped `services/worklist` aggregates — never client-side computation over raw claims (AD-1) — and thresholds/colors match the KPI cards and queue exactly (FR-SUP-4, AD-10).
3. **Given** the SLA tiles, **when** rendered, **then** they reuse Epic 1's single SLA aggregation (AD-2) and agree with the top-bar strip.
4. **Given** an empty scope slice, **when** a chart has no data, **then** it renders a defined empty/zero state without breaking layout (NFR-3).

## Tasks / Subtasks

- [ ] Task 1: Chart aggregates in `services/worklist` (AC: 1, 2, 3)
  - [ ] Distribution aggregates over the scoped caseload: settlement-status breakdown; severity band breakdown (High / Medium / Low via the registered severity-band derivation, high threshold = the SAME JDM parameter Story 5.1's High Risk card uses); recovery-status breakdown; injury-type counts (server returns top 8); total paid by employer in cents (server-ranked); claims by state (server returns top 10)
  - [ ] SLA tile data: call Epic 1's existing `services/worklist` SLA aggregation (Story 1.5) for the caller's scope — do NOT write a second implementation (AD-2); tiles must equal the top-bar strip values for the same persona
  - [ ] Top-N truncation, ordering, and percentage math all server-side (AD-1); every derived input via its registered derivation (AD-10)
- [ ] Task 2: Read-only API (AC: 2)
  - [ ] `GET /api/dashboard/charts` (or one endpoint per chart group — document the choice; a single payload is acceptable for this bounded set), thin router → `services/worklist`, camelCase, integer cents, session-required, no caller-supplied scope (AD-7)
- [ ] Task 3: Recharts setup (AC: 1)
  - [ ] First consuming story: install **Recharts 3.x** in `web/` (version pinned by the spine's stack table; pin it, don't float)
  - [ ] Shared chart theming module mapping the Story 1.1 design tokens (ok/warn/error, accent, steel, muted) onto Recharts series colors so chart segments, KPI cards, and queue chips share exact hues (AD-10 consistency at the visual layer)
- [ ] Task 4: The 7 chart components (AC: 1, 3, 4)
  - [ ] Settlement-status donut and severity donut (Recharts PieChart, donut style, center/legend labels per the prototype's supervisor view)
  - [ ] SLA tiles: 4 tiles (Pick/Approve/Settle/RTW) with pass/warn coloring against the same JDM/config targets as the top-bar strip (< 1d / < 5d / < 30d / > 80%) — a tile is a styled card, not a Recharts chart, matching the prototype
  - [ ] Recovery-status horizontal bars; injury-type top-8 bars; total-paid-by-employer bars ($ formatted from cents in UI); claims-by-state top-10 bars (Recharts BarChart, horizontal layout per UX-DR7)
  - [ ] Grid layout on the dashboard page below the 5.1/5.2 surfaces, responsive within the dashboard column
  - [ ] Every chart: loading skeleton, defined empty/zero state (fixed-height placeholder — layout never collapses), inline error state (NFR-3)
  - [ ] Segment click drill-through is Story 5.5 — segments are non-interactive this story (structure the components so 5.5 can attach handlers without rewrites)
- [ ] Task 5: Tests (AC: all)
  - [ ] Unit tests: each aggregate against seed data for David Bline (full portfolio) and Jennifer Park (Toyota/GM/3M only — e.g. paid-by-employer contains exactly her three employers); top-8/top-10 truncation and ordering; severity-band split agrees with the 5.1 High Risk count (same JDM threshold, asserted equal on the same scope)
  - [ ] Unit test: empty scope → every aggregate returns empty/zero series without error
  - [ ] Vitest: chart components render data/empty/error states
  - [ ] Playwright `e2e/stories/5-3-portfolio-analytics-charts.spec.ts` tagged `@story:5-3 @epic:5`, one `@smoke` happy path: supervisor login → all 7 chart surfaces render; plus an assertion that the dashboard SLA tiles show the same values as the top-bar strip

## Tasks note

Chart form/color choices are constrained: the prototype's supervisor view is the design contract (UX-DR7) and series colors must ride the shared tokens — no palette invention.

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

The 7 analytics charts on the dashboard page (5.1's shell), plus the Recharts 3 install and the token→chart-color mapping every later chart surface (Epic 7) will reuse. It is NOT: KPI cards (5.1), the handler table (5.2), the top-30 worklist (5.4), or segment-click drill-through (5.5). Read-only by role capability (AD-7): no mutations, no audit events. The prototype rendered these charts as hand-rolled SVG/CSS — the production build replaces that with Recharts but keeps the visual contract (donuts, horizontal bars, tile coloring).

### Architecture compliance (binding ADs for this story)

- **AD-1:** the SPA receives finished series (labels + values + colors semantics); it never aggregates, truncates, ranks, or percentages raw claims client-side.
- **AD-2:** SLA tiles call the one existing SLA aggregation from Story 1.5 — the dashboard tiles and the top-bar strip are two renderings of one server value.
- **AD-7:** all aggregates behind the repository scope context; no caller-supplied scope; no role-conditional widening.
- **AD-8/AD-10:** the severity High threshold (65) and any band/target parameters are the same JDM values as 5.1's cards and the Epic 2 queue — one computing function per derived value; thresholds and status colors match those surfaces exactly.
- **AD-9:** TanStack Query under the shared `queryKeys` module; chart data is server state, cached and invalidated like any other.

### Data notes

- **Tables created: none** (Epic 5 creates no tables). Reads: claim (status, severity, recovery status, injury type, state, employer), employer (names), bill/expense/payment data for paid totals — all through scoped repositories.
- **Writes: none**; no AD-12 ownership impact.
- New dependency: Recharts 3.x (spine stack table) — first install lands in this story per the "each library arrives with its first consuming story" rule from Story 1.1.

### UX notes

- UX-DR7: "2 donuts, SLA tiles, recovery bars, 3 horizontal bar charts" — layout and chart forms per the prototype's supervisor view (`renderSV()` in `docs/Workers_Comp_Prototype.html`).
- Color semantics: settled/ok slices in ok-green, warnings in warn-amber, high severity in error-red, informational series in accent/steel — the exact Story 1.1 token hues. Chart colors ARE the threshold semantics; a High-severity slice must be the same red as a High Risk KPI card (AC 2).
- **Design-token ruling:** prototype palette is LIGHT and canonical; epics.md's "dark console aesthetic" is a documented discrepancy (Story 1.1 Dev Notes).
- NFR-3 / UX-DR11: loading, empty/zero (fixed-height, layout-stable), and error states on every chart surface.

### Testing requirements

- Unit: every aggregate under full and scoped personas; truncation/order; cross-surface consistency assertions (severity donut High count == 5.1 High Risk count; SLA tiles == Story 1.5 aggregation output); empty-scope series.
- Web: Vitest for chart component states.
- E2E (AD-15): `e2e/stories/5-3-portfolio-analytics-charts.spec.ts` tagged `@story:5-3 @epic:5`, exactly one `@smoke` happy path, against the freshly reset e2e stack; assert SLA tile / top-bar agreement. **Story cannot reach `review`/`done` until it passes.**

### Project Structure Notes

- Server: aggregates in `server/services/worklist/`; thin router in `server/api/`.
- Web: chart components in `web/src/features/dashboard/`; shared chart theme module in `web/src/features/dashboard/` (or `web/src/components/` if 5.4/Epic 7 reuse warrants — dev's call, document it); query hooks/keys in `web/src/api/`; regenerate the OpenAPI client.
- Depends on 5.1 (page shell, JDM threshold reuse) and Story 1.5 (SLA aggregation). Epic 7 will reuse these aggregates and the chart theme — keep them analyst-agnostic (scope-driven only).

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 5.3]
- FR-SUP-4/C: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]
- Recharts 3.x pin: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Stack]
- AD-1, AD-2 (SLA aggregation exists once), AD-7, AD-8, AD-10: [Source: ARCHITECTURE-SPINE.md#Invariants & Rules]
- SLA aggregation + targets: [Source: _bmad-output/planning-artifacts/epics.md#Story 1.5]
- UX-DR7 chart inventory, UX-DR11 states: [Source: _bmad-output/planning-artifacts/epics.md#UX Design Requirements]
- "Recharts 3 renders KPI donuts and payout stacked bars; every supervisor chart segment drills through": [Source: docs/Architecture-LINEWORKER.md#6. Frontend architecture]
- Design-token discrepancy ruling: [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]
- AD-15 E2E gate: [Source: ARCHITECTURE-SPINE.md#AD-15]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
