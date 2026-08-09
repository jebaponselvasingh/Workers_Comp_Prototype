# Story 5.4: Priority Claims Worklist (Top 30)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a WC supervisor,
I want the portfolio's highest-attention claims in one table,
so that I can direct handler effort where it matters most.

## Acceptance Criteria

1. **Given** the priority table, **when** it renders, **then** its population is active-treatment ∪ fraud-flagged ∪ litigation-flagged claims capped at 30 (cap as a JDM parameter — AD-8), with columns Claim ID · Worker · Employer · Injury Type · Severity · Fraud Score · Handler · Days Open · Priority Next Best Action · Status, and litigation rows carry a LITIG chip (FR-SUP-5, FR-SUP-D, UX-DR7).
2. **Given** the Priority Next Best Action column, **when** populated, **then** it shows the top action from the worklist action generator (Epic 3) — deterministic, never LLM-originated (AD-2).
3. **Given** the table, **when** sorted and paged, **then** rows are server-sorted by the same single priority scorer as the handler queue (AD-10) and cursor-paginated per list conventions.

## Tasks / Subtasks

- [ ] Task 1: Priority-claims query in `services/worklist` (AC: 1, 3)
  - [ ] Population predicate: claims in active treatment ∪ fraud-flagged (score ≥ the SAME fraud JDM parameter as 5.1's Fraud Flags card) ∪ litigation-flagged, evaluated over the caller's scoped repository (AD-7); union semantics — a claim matching several arms appears once
  - [ ] Sort by the existing single priority scorer from Story 2.1 (`services/worklist`, JDM weights) — do NOT write a dashboard-local scorer or re-weight (AD-2, AD-10); document the tie-break (e.g. days open desc, then claim ID)
  - [ ] Cap at 30 with the cap read from a JDM parameter (the spine's AD-8 names "worklist caps" as JDM-owned) — never a literal `30` in Python
  - [ ] Per-row fields via registered derivations: severity, fraud score, days open, litigation flag, status; handler + worker + employer names joined for display
- [ ] Task 2: Priority Next Best Action per row (AC: 2)
  - [ ] For each returned claim, call Epic 3's worklist action generator (Story 3.5's 11 trigger rules) and take its top (most urgent) action's label as the row's Next Best Action — one generator, deterministic, no LLM path anywhere near this column (AD-2); the `ai_insight` cache is NOT a source here
  - [ ] Compute server-side inside the same aggregate response (30-row bound keeps this cheap); the SPA renders the string it received
- [ ] Task 3: Read-only API with cursor pagination (AC: 1, 3)
  - [ ] `GET /api/dashboard/priority-claims` returning `{items, nextCursor, total?}` per list conventions — cursor-paginated even under the 30 cap (page size < cap exercises the mechanism; the cap bounds the population, the cursor pages within it)
  - [ ] Thin router → `services/worklist`; camelCase; session-required; no caller-supplied scope (AD-7); claims referenced by business ID `WC-nnnn`
- [ ] Task 4: Table UI (AC: 1, 2)
  - [ ] TanStack Table v8 in `web/features/dashboard/` with the 10 columns; LITIG chip on litigation rows (error-token hue per the queue's existing LITIG badge — same semantics, same hue); severity and fraud score colored by the shared band tokens; monospace claim IDs (JetBrains Mono token from Story 1.1)
  - [ ] Cursor "load more" per list conventions; loading/empty/error states (NFR-3) — an empty population (e.g. tiny scoped book) renders a defined empty state
  - [ ] Row click drill-through is Story 5.5 — rows non-interactive this story (component accepts a future `onRowClick`)
- [ ] Task 5: Tests (AC: all)
  - [ ] Unit tests: population union (each arm alone; overlapping claim appears once; non-qualifying claims excluded); cap enforcement at the JDM value (and that changing the JDM value changes the cap — no literal); ordering equals the Story 2.1 scorer's ordering on the same claims
  - [ ] Unit test: Next Best Action equals the first action Story 3.5's generator returns for that claim (assert against the generator, not a copied expectation)
  - [ ] Scoping test: Jennifer Park's table contains only Toyota/GM/3M claims; David Bline's draws from all 100
  - [ ] Playwright `e2e/stories/5-4-priority-claims-worklist-top-30.spec.ts` tagged `@story:5-4 @epic:5`, one `@smoke` happy path: supervisor login → table renders ≤ 30 priority-ordered rows with all columns, LITIG chips present on litigation rows

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

The top-30 priority claims table on the dashboard page (5.1's shell). It is NOT: a second priority scorer (reuse 2.1's), a second action generator (reuse 3.5's), an AI surface (the Next Best Action column is deterministic output of the Epic 3 generator — Epic 6's `ai_insight` "next best actions" narrative card is a different, later thing and never feeds this column), nor drill-through (5.5 makes rows clickable). Read-only by role capability (AD-7): no mutations, no audit events — a supervisor directs effort by talking to handlers, not by editing claims here.

### Architecture compliance (binding ADs for this story)

- **AD-2:** priority scoring and action generation each exist exactly once in `services/worklist`; this story only calls them. "AI-suggested next action" in FR-SUP-5's prose is implemented as the deterministic generator's top action — never LLM-originated.
- **AD-7:** population computed behind the repository scope context; no caller-supplied scope; scoped supervisors get a scoped top-30, not a filtered global top-30 (the cap applies after scoping).
- **AD-8:** the 30 cap, fraud threshold, and priority weights are JDM parameters — the same documents the queue and KPI cards read.
- **AD-10:** every derived cell (severity band, fraud flag, days open, flags, priority order) resolves through the single registered computer — this table, the handler queue, and the KPI cards can never disagree about the same claim.
- **Conventions:** `{items, nextCursor, total?}` cursor pagination; business IDs `WC-nnnn`; snake_case enums with UI-owned labels; camelCase JSON.

### Data notes

- **Tables created: none** (Epic 5 creates no tables). Reads: claim (+ joins to employee, employer, app_user for display names), plus whatever Story 3.5's generator reads (bills, schedule, document state) — all through scoped repositories.
- **Writes: none**; no AD-12 ownership impact. JDM: the cap parameter joins the existing worklist JDM document (AD-8 names worklist caps as JDM-owned).

### UX notes

- UX-DR7: "top-30 priority table with LITIG chips; every row clickable for drill-through" — the clickability clause lands in 5.5; this story delivers the table itself per the prototype's supervisor view.
- Chip/band hues ride the Story 1.1 tokens, identical to the queue's FRAUD/LITIG badges and the 5.1/5.3 threshold colors.
- **Design-token ruling:** prototype palette is LIGHT and canonical; epics.md's "dark console aesthetic" is a documented discrepancy (Story 1.1 Dev Notes).
- NFR-3 / UX-DR11: loading skeleton, defined empty state, inline error state.

### Testing requirements

- Unit: population union + dedupe, JDM cap, scorer-order equality vs Story 2.1, generator-output equality vs Story 3.5, scoped personas (Jennifer Park / David Bline), pagination cursor behavior.
- Web: Vitest on table states (loading/data/empty/error) and LITIG chip rendering.
- E2E (AD-15): `e2e/stories/5-4-priority-claims-worklist-top-30.spec.ts` tagged `@story:5-4 @epic:5`, exactly one `@smoke` happy path, against the freshly reset e2e stack. **Story cannot reach `review`/`done` until it passes.** (Grep anchoring matters here: `@story:5-4` must not match a hypothetical `5-40`.)

### Project Structure Notes

- Server: query + composition in `server/services/worklist/` (calls its own scorer and action generator); thin router in `server/api/`.
- Web: `web/src/features/dashboard/` table component; query hook/key in `web/src/api/`; regenerate the OpenAPI client.
- Depends on: 5.1 (page shell), Story 2.1 (priority scorer + JDM weights), Story 3.5 (action generator + trigger JDM). All precede this story in sprint order.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 5.4]
- FR-SUP-5/D: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]
- Priority scorer + JDM weights: [Source: _bmad-output/planning-artifacts/epics.md#Story 2.1]
- Action generator (11 trigger rules, JDM): [Source: _bmad-output/planning-artifacts/epics.md#Story 3.5]
- AD-2 (deterministic core; LLM never originates), AD-7, AD-8 (worklist caps JDM-owned), AD-10: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- List/pagination + ID conventions: [Source: ARCHITECTURE-SPINE.md#Consistency Conventions]
- UX-DR7 top-30 table: [Source: _bmad-output/planning-artifacts/epics.md#UX Design Requirements]
- Design-token discrepancy ruling: [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]
- AD-15 E2E gate: [Source: ARCHITECTURE-SPINE.md#AD-15]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
