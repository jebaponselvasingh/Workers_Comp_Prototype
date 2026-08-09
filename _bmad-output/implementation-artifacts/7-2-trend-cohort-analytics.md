# Story 7.2: Trend & Cohort Analytics

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a data analyst,
I want time-series and cohort views,
so that I can see whether the portfolio is improving or deteriorating.

## Acceptance Criteria

1. **Given** the Trends section, **when** it renders, **then** time-series charts show FNOL/DOI volume, average days-open, settlement cycle time, RTW rate, and cost trends over selectable periods (FR-AN-2), all computed server-side over the analyst's scope (AD-1, AD-7).
2. **Given** cohort comparison, **when** the analyst selects cohorts (severity band, disability type, sector), **then** the series render side by side with consistent colors and thresholds (FR-AN-2, UX-DR7).
3. **Given** sparse periods, **when** few or no claims exist, **then** charts render defined empty/partial states rather than misleading zero lines (NFR-3).

## Tasks / Subtasks

- [ ] Task 1: Time-series aggregate endpoints in `services/worklist` (AC: 1)
  - [ ] One time-series aggregate service (parameterized by metric + period grain + date range), computed entirely server-side behind the repository scope context (AD-1, AD-7); exposed via thin read-only routes gated to the `analyst` role
  - [ ] Metrics: claim volume bucketed by FNOL date and by DOI date; average days-open (call the registered `days_open` derivation per claim — AD-10, never an inline date-diff); settlement cycle time (reuse Epic 1's single SLA aggregation components — AD-2: pick/approve/settle math exists once); RTW rate per period; cost trend (paid, from the same totals derivations Epic 3/5 use)
  - [ ] Period selection: query params for grain (week/month/quarter) and range; response is a series envelope carrying per-bucket `value` **and** `claimCount`, plus explicit `null` (not `0`) for buckets with no observable value, so the UI can render partial states honestly (AC 3)
  - [ ] Severity band edges and any threshold lines (e.g. RTW > 80% SLA target) resolved from JDM parameters (AD-8) — the same documents every other surface reads
- [ ] Task 2: Cohort comparison (AC: 2)
  - [ ] Cohort dimension param on the same aggregate (severity band / disability type / sector — one dimension at a time, per the AC); server returns one series per cohort value under the same scope context
  - [ ] Cohort banding uses registered derivations/JDM bands (severity band from the same edges as the severity donut in 5.3 — AD-8/AD-10); disability type and sector come from claim/employer columns
- [ ] Task 3: Trends section UI (AC: 1, 2, 3)
  - [ ] Add the **Trends** section to the analyst workspace shell from Story 7.1, reusing Epic 5's dashboard idioms (chart cards, consistent Recharts styling — UX-DR7); period + cohort selectors as compact controls in the section header
  - [ ] Cohort series side by side with a stable color assignment per cohort value (consistent across charts in the section) and threshold reference lines matching KPI coloring exactly (AC 2)
  - [ ] Sparse-data handling: buckets with `null` render as gaps/partial-state annotations, not zero lines; an all-empty range renders a defined empty state; low-sample buckets (few claims) get a visual low-confidence treatment (AC 3, NFR-3)
  - [ ] Loading/error states on every chart; TanStack Query with keys in the shared `queryKeys` module (AD-9)
- [ ] Task 4: Drill-through (AC: 1 — FR-AN-3 pattern)
  - [ ] Clicking a series point/bucket drills through to Story 5.5's filtered claim-list view (period + metric + cohort filter applied, visible, clearable); claim rows open the read-only claim view
- [ ] Task 5: Tests (AC: all)
  - [ ] pytest: bucket math over seeded claims (known FNOL/DOI distribution), `null`-vs-zero semantics, cohort split correctness, scope enforcement (scoped analyst vs `scope_all`), JDM band resolution
  - [ ] Vitest: sparse/partial/empty chart states; cohort color stability
  - [ ] Playwright `e2e/stories/7-2-trend-cohort-analytics.spec.ts` tagged `@story:7-2 @epic:7`, one `@smoke` happy path: analyst login → Trends section renders series → select a cohort dimension → side-by-side series render → drill through a bucket to the claim list

## Dev Notes

> **Prerequisite advisory: run `bmad-ux` for the analyst workspace surfaces before starting this epic (readiness report, standing advisory #1). If a UX spec exists by dev time, it supersedes the layout suggestions here.** No prototype screen exists for trends/cohorts; where layout is open below, reuse Epic 5's established dashboard patterns (chart cards, Recharts, drill-through lists) rather than inventing new idioms.

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

The Trends section of the analyst workspace: time-series metrics + single-dimension cohort comparison. It is **not**: the 9-dimension segmentation filter (7.3 — cohorting here is a chart-level split, not the workspace-wide filter; don't build filter chips or URL filter state in this story), financial decomposition beyond the cost-trend line (7.4), export (7.5), or the fraud views (7.1). No forecasting, no statistical modeling — series are descriptive aggregates over the seeded portfolio. Dashboard-scope copilot stays deferred.

### Architecture compliance (binding ADs for this story)

- **AD-1 / AD-7:** every series is computed in `services/worklist` behind the repository scope context; the SPA never aggregates raw claims; routes are read-only, role-gated to analyst — scope gates visibility, role gates capability.
- **AD-2:** settlement/pick/approve cycle math reuses Epic 1's single SLA aggregation — never re-derived here.
- **AD-8:** severity-band edges, SLA/threshold reference values are JDM parameters, identical to Epic 5's charts.
- **AD-10:** `days_open`, RTW, totals come from their one registered derivation each — the trend service calls them, never re-implements.
- **AD-9:** TanStack Query + shared `queryKeys`; read-only surface, no optimistic updates.
- **AD-15:** story spec + done-gate (see Testing requirements).

### Data notes

Creates **no tables**. Reads: `claim` (FNOL/DOI dates, settlement dates, RTW fields, disability type, severity, financial totals via derivations), `employer` (sector). All aggregates in `services/worklist` (Capability Map: analyst workspace rides "same aggregates" as the supervisor dashboard). If date-bucketed aggregation needs an index, add it in this story's Alembic migration (index-only — no schema shape change).

### UX notes

No UX-DR covers this screen (the Epic 7 gap). Borrow: UX-DR7 (Recharts idioms, consistent thresholds/coloring, clickable segments), UX-DR11 (loading/empty/error states, no native dialogs). ⚠️ Design-token ruling: the epics' UX-DR12 "dark console aesthetic" is a **documented discrepancy** — the prototype palette is LIGHT and canonical; use Story 1.1's Tailwind tokens. Line charts are a new chart type for this codebase (Epic 5 ships donuts/bars) — use Recharts `LineChart`/`AreaChart` with the same card framing and token colors; keep cohort palettes accessible (distinguishable without color alone where feasible).

### Testing requirements

- pytest: time-bucket correctness (edge: claims on bucket boundaries), null-vs-zero envelope semantics, cohort splits, scope enforcement, JDM resolution.
- Vitest: sparse/partial/empty rendering, selector behavior.
- Playwright `e2e/stories/7-2-trend-cohort-analytics.spec.ts` tagged `@story:7-2 @epic:7` (anchored grep), exactly one `@smoke` happy path, against the freshly reset e2e stack. **The story cannot move to review/done until this spec passes (AD-15 done-gate).**

### Project Structure Notes

- UI in `web/src/features/dashboard/` (analyst subfolder from 7.1); server aggregates in `server/services/worklist/`; thin routes in `server/api/`.
- Extends Story 7.1's workspace shell — 7.1 must be done first (sequential within the epic; no forward dependencies).
- Drill-through reuses Story 5.5 components in place; amend `5-5`'s spec in this PR only if its behavior legitimately changes (AD-15 amendment rule).

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 7.2]
- FR-AN-2, FR-AN-3: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]
- Epic 7 UX gap + advisory: [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Standing Advisories item 1]
- Single SLA aggregation, derivations, JDM tier, scoping: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-2 / #AD-8 / #AD-10 / #AD-7]
- Capability ownership ("same aggregates", `web/features/dashboard`): [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map]
- List/query conventions, Recharts 3.x: [Source: ARCHITECTURE-SPINE.md#Consistency Conventions / #Stack]
- E2E gate: [Source: ARCHITECTURE-SPINE.md#AD-15]
- Design-token ruling (light palette canonical): [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
