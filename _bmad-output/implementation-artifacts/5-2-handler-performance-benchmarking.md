# Story 5.2: Handler Performance Benchmarking

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a WC supervisor,
I want my handlers ranked with workload and speed context,
so that I can spot who needs a check-in before SLAs slip.

## Acceptance Criteria

1. **Given** the performance table, **when** it renders, **then** handlers rank by composite cycle time (pick + approve + settle) with columns # · Handler · Cases · Cycle Speed (bar vs peers) · Avg Days · RTW % · Complexity (Low/Med/High blending severity, surgery %, litigation %) · Pending Approvals · Status (FR-SUP-2, FR-SUP-B).
2. **Given** the Status column, **when** computed, **then** On Track / Watch / Attention derives from cycle-time deviation vs the portfolio with deviation thresholds as JDM parameters (FR-SUP-3, AD-8), and a leader/laggard callout renders.
3. **Given** the complexity score, **when** computed, **then** it comes from a single registered function (AD-10) with unit tests over representative handler mixes.
4. **Given** a scoped supervisor, **when** the table loads, **then** it includes only handlers with claims inside the persona's employer scope, computed server-side (AD-7).

## Tasks / Subtasks

- [ ] Task 1: Per-handler benchmark aggregate in `services/worklist` (AC: 1, 4)
  - [ ] One aggregate (e.g. `handler_benchmarks(ctx)`) grouping the caller's scoped claims by assigned handler and computing per handler: case count, avg pick days, avg approve days, avg settle days, **composite cycle time = pick + approve + settle averages** (the ranking key), RTW %, pending-approval count
  - [ ] Reuse Epic 1's single per-claim pick/approve/settle/RTW derivations (Story 1.5's SLA aggregation inputs) — same registered functions, grouped per handler; never a second cycle-time implementation (AD-2, AD-10)
  - [ ] Group over the **scoped claim set**, not over handler rosters: a handler whose book straddles supervisor scopes (the Kaya/Deere case is normative) appears with only the claims inside the viewer's scope, and handlers with zero in-scope claims don't appear (AD-7)
  - [ ] Server computes the Cycle Speed bar ratio (each handler's composite vs the peer best/worst in this scope) — the SPA renders the ratio, it does not derive it (AD-1)
- [ ] Task 2: Complexity classification — single registered function (AC: 3)
  - [ ] One registered function (in the `services/derivations` registry, invoked by the worklist aggregate) mapping a handler's claim mix → Low / Med / High, blending avg severity, surgery %, and litigation % of their in-scope caseload
  - [ ] Blend weights/cut-points are JDM parameters (AD-8); the blend arithmetic is typed Python; enum values snake_case (`low|med|high`), UI owns display labels
- [ ] Task 3: Status derivation + leader/laggard callout (AC: 2)
  - [ ] On Track / Watch / Attention from each handler's composite-cycle-time deviation vs the scoped-portfolio average; the two deviation thresholds (watch, attention) are JDM parameters in a benchmark JDM document (AD-8)
  - [ ] Aggregate response carries the leader (fastest composite) and laggard (slowest / attention-flagged) so the UI callout renders server-provided facts (AD-1)
- [ ] Task 4: Read-only API + table UI (AC: 1, 2, 4)
  - [ ] `GET /api/dashboard/handler-benchmarks` — thin router, camelCase, session-required, no caller-supplied scope (AD-7); returns ranked `items` (small bounded set — full list, no cursor needed, document why)
  - [ ] Table in `web/features/dashboard/` (TanStack Table v8): rank #, handler name, cases, cycle-speed bar (peer-relative, token colors), avg days, RTW %, complexity chip, pending approvals, status chip (ok/warn/error hues for On Track/Watch/Attention)
  - [ ] Leader/laggard callout line under the table per the prototype's supervisor view; loading/empty/error states (NFR-3) — empty scope renders a defined empty state, not a broken table
  - [ ] Row click drill-through is Story 5.5 — rows are non-interactive this story (component ready for an `onRowClick` later)
- [ ] Task 5: Tests (AC: all)
  - [ ] Unit tests: complexity function over representative handler mixes — all-low-severity/no-surgery/no-litigation → Low; heavy-surgery mix → High; boundary values at each JDM cut-point (AC 3 names this explicitly)
  - [ ] Unit tests: status derivation at/around both deviation thresholds; composite cycle time = sum of the three per-stage averages; ranking order stable and deterministic (tie-break documented, e.g. by handler name)
  - [ ] Scoping test: Jennifer Park's table contains only handlers with Toyota/GM/3M claims, with per-handler counts restricted to those employers; David Bline sees all handlers over all 100 claims
  - [ ] Playwright `e2e/stories/5-2-handler-performance-benchmarking.spec.ts` tagged `@story:5-2 @epic:5`, one `@smoke` happy path: supervisor login → ranked table renders with all 9 columns + callout

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

The handler benchmarking table + its server aggregate, on the dashboard page Story 5.1 built. It is NOT: KPI cards (5.1), charts (5.3 — still no Recharts; the cycle-speed bar is a simple token-styled div/progress element, not a chart library), the top-30 worklist (5.4), or handler-row drill-through (5.5). Read-only: no mutation, no audit events, role gates capability (AD-7). The prototype's ranked handler table with cycle-speed bars and leader/laggard callout (UX-DR7) is the design contract.

### Architecture compliance (binding ADs for this story)

- **AD-2:** cycle-time components come from the exact same single aggregation family Epic 1's SLA strip uses — the benchmark table and the top-bar strip can never disagree on what "avg settle" means.
- **AD-7:** grouping happens over the repository-scoped claim set; supervisor scope is employer-based, not a handler hierarchy — never enumerate "the supervisor's handlers", derive handlers from in-scope claims.
- **AD-8:** deviation thresholds and complexity blend parameters are JDM (DB-versioned, effective-dated); ranking/blend arithmetic is typed Python reading its tunables from ZEN.
- **AD-10:** complexity has exactly one registered computing function; per-claim inputs (severity, surgery flag, litigation flag, RTW status) resolve through their existing registered derivations.
- **AD-1/AD-9:** every displayed number (including bar ratios and deviation %) is server-computed; TanStack Query under shared `queryKeys`.

### Data notes

- **Tables created: none** (Epic 5 creates no tables). Reads: claim (handler assignment, severity, surgery/litigation flags, lifecycle dates, RTW status), app_user (handler names), user_employer_assignment (scope), payment/approval state for pending-approvals.
- **Writes: none**; no AD-12 ownership impact. New JDM content: a handler-benchmark document (deviation thresholds, complexity blend parameters) via the established rules-versioning mechanism.
- Handler identity: claims carry their assigned handler (seeded from the prototype's HANDLER_MAP in Story 1.2's persona/assignment seed). Handlers are `app_user` rows — join for display names.

### UX notes

- UX-DR7: "ranked handler table with cycle-speed bars and leader/laggard callout" — match the prototype's supervisor view column set and density.
- Status chips use the ok/warn/error token hues (On Track = ok, Watch = warn, Attention = error); complexity chips are neutral/info toned. Same tokens as queue and KPI cards — no new colors.
- **Design-token ruling:** prototype palette is LIGHT and canonical; epics.md's "dark console aesthetic" is a documented discrepancy (Story 1.1 Dev Notes).
- NFR-3: loading skeleton, empty state (scoped supervisor with no in-scope handlers), inline error state.

### Testing requirements

- Unit (the ACs demand these by name): complexity function over representative handler mixes + JDM boundary values; status/deviation thresholds; composite = pick + approve + settle; scoping (Jennifer Park vs David Bline).
- Web: Vitest on the table component states (loading/data/empty/error).
- E2E (AD-15): `e2e/stories/5-2-handler-performance-benchmarking.spec.ts` tagged `@story:5-2 @epic:5`, exactly one `@smoke` happy path, against the freshly reset e2e stack. **Story cannot reach `review`/`done` until it passes.** Selectors: role first, `data-testid` second.

### Project Structure Notes

- Server: aggregate + status logic in `server/services/worklist/`; complexity function registered in `server/services/derivations/`; JDM document under `server/rules/` content (DB-versioned).
- Web: table component in `web/src/features/dashboard/`; query hook + key in `web/src/api/`; regenerate the OpenAPI client.
- Depends on Story 5.1's dashboard page shell and Epic 1's SLA derivations (1.5). Pending-approval counts use Epic 3's assessment/payment approval state (3.4/3.5).

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 5.2]
- FR-SUP-2/3/B: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]
- AD-2 single SLA aggregation, AD-8 JDM parameters (handler-benchmark thresholds named explicitly), AD-10: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- AD-7 employer-based supervisor scope + Kaya/Deere straddle case: [Source: ARCHITECTURE-SPINE.md#AD-7]
- SLA aggregation origin: [Source: _bmad-output/planning-artifacts/epics.md#Story 1.5]
- UX-DR7 handler table: [Source: _bmad-output/planning-artifacts/epics.md#UX Design Requirements]
- Design-token discrepancy ruling: [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]
- AD-15 E2E gate: [Source: ARCHITECTURE-SPINE.md#AD-15]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
