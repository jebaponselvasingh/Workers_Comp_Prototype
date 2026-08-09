# Story 7.3: Segmentation & Universal Drill-Down

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a data analyst,
I want to slice any view by any dimension and always reach the underlying claims,
so that no aggregate is a dead end.

## Acceptance Criteria

1. **Given** the segmentation control, **when** the analyst filters by employer, sector, state/region, injury type, ICD-10, severity band, age group, gender, or disability type, **then** every KPI, chart, and table in the workspace recomputes server-side under the combined filter (FR-AN-4).
2. **Given** any KPI, chart segment, or table row anywhere in the analyst workspace, **when** clicked, **then** it drills into the constituent claims with the active segmentation preserved (FR-AN-3), opening the read-only claim view from Epic 5.
3. **Given** filter state, **when** a URL is shared or reloaded, **then** it restores from the URL and scope re-resolves server-side on load (AD-7).
4. **Given** composed filters, **when** applied, **then** they AND together, display as clearable chips, and an impossible combination shows an explicit zero-result state (NFR-3).

## Tasks / Subtasks

- [ ] Task 1: Server-side segmentation filter model (AC: 1, 4)
  - [ ] Define one segmentation filter schema (Pydantic) accepted by every analyst aggregate endpoint via `filter[…]` query params (list/query conventions): employer, sector, state/region, injury type, ICD-10, severity band, age group, gender, disability type — 9 dimensions, multi-value per dimension, dimensions AND-composed (values within a dimension OR-composed)
  - [ ] Apply the filter in the repository/aggregate layer **in addition to** the unconditional AD-7 scope predicate — a filter can only narrow the caller's book of business, never widen it; no endpoint accepts caller-supplied scope
  - [ ] Severity band and age-group edges resolve from JDM parameters (AD-8) — never hardcoded band boundaries; ICD-10 and the other dimensions filter on claim/employee/employer columns directly
  - [ ] Retrofit the filter schema onto the Story 7.1 fraud aggregates and 7.2 trend aggregates (same `services/worklist` functions gain the filter param); Epic 5 supervisor endpoints are untouched
  - [ ] Dimension-values endpoint (distinct values per dimension within scope) to populate the control without client-side scans (AD-1)
- [ ] Task 2: Segmentation control UI (AC: 1, 4)
  - [ ] Workspace-level segmentation bar on the analyst workspace shell (7.1): per-dimension pickers (shadcn/ui select/combobox), active filters rendered as clearable chips with a clear-all action
  - [ ] All workspace queries (fraud, trends, and later financial sections) include the active filter in their TanStack Query keys (AD-9) so every KPI/chart/table recomputes from the server on filter change — no client-side re-filtering of cached data
  - [ ] Zero-result behavior: an impossible combination renders an explicit, non-broken zero-result state on every card/chart/table (NFR-3) — distinct from loading and error states
- [ ] Task 3: Universal drill-down (AC: 2)
  - [ ] Every KPI card, chart segment, and table row in the analyst workspace navigates to the Story 5.5 filtered claim-list view carrying **active segmentation + the clicked slice** as the combined filter (visible, clearable); list is cursor-paginated and server-sorted per conventions
  - [ ] Claim rows open Epic 5's read-only claim view (no edit affordances — role gates capability, AD-7)
  - [ ] Extend the 5.5 claim-list view in place to accept the segmentation filter params (do not fork it); amend its spec only if behavior legitimately changes (AD-15 amendment rule)
- [ ] Task 4: URL state (AC: 3)
  - [ ] Serialize the active segmentation into the workspace URL (and drill-through URLs); on direct load/reload, filter state restores from the URL and every request re-resolves scope server-side via the auth context — editing a URL can never reach claims outside the caller's book (AD-7, under test)
  - [ ] Unknown/invalid filter values in a URL degrade to a clear inline message + that dimension cleared — never a crash (NFR-3)
- [ ] Task 5: Tests (AC: all)
  - [ ] pytest: filter composition (AND across dimensions, OR within), scope-cannot-widen property (filtered result ⊆ scoped result — property-test worthy), JDM band edges, zero-result correctness, dimension-values endpoint scoping
  - [ ] Vitest: chip add/clear/clear-all, query-key inclusion, zero-result rendering, invalid-URL handling
  - [ ] Playwright `e2e/stories/7-3-segmentation-universal-drill-down.spec.ts` tagged `@story:7-3 @epic:7`, one `@smoke` happy path: analyst login → apply two filters → workspace recomputes → click a chart segment → drill-through list carries both chips + slice → open read-only claim view; plus a URL-reload restore test and a scope-tamper test (URL with out-of-scope employer returns only in-scope data)

## Dev Notes

> **Prerequisite advisory: run `bmad-ux` for the analyst workspace surfaces before starting this epic (readiness report, standing advisory #1). If a UX spec exists by dev time, it supersedes the layout suggestions here.** There is no prototype screen for the segmentation control; where layout is open below, reuse Epic 5's established dashboard patterns and shadcn/ui primitives rather than inventing new idioms.

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

The cross-cutting filter + drill-down contract for the analyst workspace: one filter schema, applied to the sections that exist so far (7.1 fraud, 7.2 trends) and inherited by 7.4's financial section when it lands. It is **not**: new analytics content (no new charts/KPIs), financial decomposition (7.4), export (7.5 — the export of "exactly the current filtered dataset" builds on this story's filter schema), or changes to the supervisor dashboard (Epic 5 endpoints and screens are untouched; only the shared 5.5 drill-through components may be extended in place). Saved/named filter sets are out of scope — URL state is the sharing mechanism.

### Architecture compliance (binding ADs for this story)

- **AD-1:** filtering and recomputation are entirely server-side; the SPA never re-filters cached aggregates or raw claims; dimension values come from an endpoint, not client scans.
- **AD-7:** the segmentation filter composes WITH the unconditional repository scope predicate, never replaces it; scope re-resolves server-side on every request including URL-restored loads; role (`analyst`) gates the routes; drill-through claim view stays read-only.
- **AD-8:** severity-band and age-group boundaries are JDM parameters — the same severity edges as 5.3's donut and 7.2's cohorts.
- **AD-9:** filter state lives in the URL + component state; server data exclusively via TanStack Query with filter-inclusive keys in the shared `queryKeys` module.
- **AD-10:** banding applied by the same registered derivations/JDM lookups other surfaces use — no second band computer.
- **AD-15:** story spec + done-gate (see Testing requirements).

### Data notes

Creates **no tables**. Reads: `claim` (injury type, ICD-10, severity, disability type, state/region, employer FK), `employee` (age → age group, gender), `employer` (sector, state). All filtered aggregates in `services/worklist`; filter application lives with the scope-enforcing repository layer in `server/data/` so no query path can take one without the other. Age group is computed from `employee` date-of-birth/age at query time via a registered derivation or JDM band lookup — not a stored column (AD-10).

### UX notes

No UX-DR covers this control (the Epic 7 gap). Borrow: UX-DR7 (clickable segments/rows for drill-through everywhere), UX-DR11 (loading/empty/error/zero states; no native dialogs), Story 5.5's drill-through list UX (applied filter visible and clearable — chips here generalize that pattern). ⚠️ Design-token ruling: the epics' UX-DR12 "dark console aesthetic" is a **documented discrepancy** — the prototype palette is LIGHT and canonical; use Story 1.1's Tailwind tokens.

### Testing requirements

- pytest: filter-composition semantics, the scope-cannot-widen property (Hypothesis-style property test recommended), JDM edge resolution, zero-result behavior, dimension-values scoping.
- Vitest: chip lifecycle, query-key correctness, URL parse/serialize round-trip, invalid-value degradation.
- Playwright `e2e/stories/7-3-segmentation-universal-drill-down.spec.ts` tagged `@story:7-3 @epic:7` (anchored grep), exactly one `@smoke` happy path, including URL-restore and the scope-tamper assertion, against the freshly reset e2e stack. **The story cannot move to review/done until this spec passes (AD-15 done-gate).**

### Project Structure Notes

- Filter schema shared server-side (one Pydantic module under `server/services/worklist/`), surfaced to TS via the generated OpenAPI client — no hand-written TS filter types.
- UI in `web/src/features/dashboard/` (analyst subfolder); segmentation bar is a workspace-shell component so 7.4's section inherits it for free.
- Depends on 7.1 (shell + fraud aggregates) and 7.2 (trend aggregates) being done; 7.4/7.5 build on this story's schema — keep it stable and typed.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 7.3]
- FR-AN-3, FR-AN-4: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]
- Epic 7 UX gap + advisory: [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Standing Advisories item 1]
- Scope enforcement incl. URL-tamper rule: [Source: _bmad-output/planning-artifacts/epics.md#Story 5.5]; [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-7]
- `filter[…]`/`sort`/cursor list conventions: [Source: ARCHITECTURE-SPINE.md#Consistency Conventions]
- JDM parameter tier: [Source: ARCHITECTURE-SPINE.md#AD-8]
- Frontend state discipline: [Source: ARCHITECTURE-SPINE.md#AD-9]
- E2E gate: [Source: ARCHITECTURE-SPINE.md#AD-15]
- Design-token ruling (light palette canonical): [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
