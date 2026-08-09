# Story 5.1: Portfolio KPI Cards

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a WC supervisor,
I want portfolio KPIs at the top of my dashboard,
so that volume, financial exposure, and risk concentration are visible at a glance.

## Acceptance Criteria

1. **Given** a supervisor or analyst, **when** the dashboard loads, **then** the header shows "Manufacturing WC — Portfolio Overview" with the dataset chip, and row 1 renders Total Claims · Under Treatment · Settled & Closed · High Risk (severity ≥ 65) · Total Paid ($) · Total Reserve ($) over the persona's scoped caseload (FR-SUP-1, FR-SUP-A).
2. **And** row 2 renders Fraud Flags (score ≥ 55) · OSHA Recordable · Litigation (attorney-represented) · Surgery Required from the same scoped aggregates.
3. **Given** the thresholds (65, 55), **when** resolved, **then** they come from JDM parameters — the same values every other surface uses (AD-8, AD-10), verified by a test that scoped supervisor Jennifer Park's counts cover only Toyota/GM/3M.
4. **Given** any aggregate endpoint, **when** called, **then** it computes in `services/worklist` behind the repository scope context (AD-7), with loading and error states in the UI (NFR-3).

## Tasks / Subtasks

- [ ] Task 1: Portfolio summary aggregate in `services/worklist` (AC: 1, 2, 3, 4)
  - [ ] One aggregate function (e.g. `portfolio_summary(ctx)`) computing all 10 KPI values in a single scoped pass: total claims, under treatment (stage = `treatment`), settled & closed (stage = `settled`), high risk (severity ≥ high-risk threshold), total paid (cents), total reserve (cents), fraud flags (fraud score ≥ fraud threshold), OSHA recordable, litigation (attorney-represented), surgery required
  - [ ] Every input flag/derived value (stage, severity band, `siu_review`-style flags, paid/reserve totals) obtained via the registered `services/derivations` functions — never re-derived inside the aggregate (AD-10)
  - [ ] Repository access exclusively through the caller scope context; no role branch anywhere in the aggregate (scope gates visibility — AD-7)
- [ ] Task 2: Thresholds from JDM (AC: 3)
  - [ ] Resolve high-risk severity (seeded 65) and fraud-flag score (seeded 55) from the existing ZEN JDM parameter documents introduced by Epics 2/3 (queue risk band, fraud flag) — reuse the same document keys, do NOT author a second dashboard copy of either value (AD-8, AD-10)
  - [ ] If a needed parameter does not yet exist as JDM, add it to the appropriate shared document (DB-versioned, effective-dated) so queue, detail, and dashboard all read one value
- [ ] Task 3: Read-only dashboard API (AC: 1, 2, 4)
  - [ ] `GET /api/dashboard/summary` (thin router → `services/worklist`), camelCase JSON via Pydantic alias, money as integer cents, no caller-supplied scope parameter of any kind (AD-7)
  - [ ] Endpoint requires a valid session (401 problem+json otherwise, per Epic 1 auth deps); it is a query — no mutation, no audit event
- [ ] Task 4: Dashboard page + KPI card rows (AC: 1, 2, 4)
  - [ ] Build the real dashboard page in `web/features/dashboard/` (Story 1.3 routes supervisor AND analyst here — the analyst sees this same dashboard until Epic 7)
  - [ ] Header: "Manufacturing WC — Portfolio Overview" + dataset chip (`WC_Manufacturing_Claims_2026.xlsx` label per the prototype); role badge/top bar already exist from Epic 1
  - [ ] Row 1 (6 cards) and row 2 (4 cards) styled with the Story 1.1 design tokens (information-dense card style, ok/warn/error hues); dollar values formatted from cents in the UI only
  - [ ] TanStack Query via the shared `queryKeys` module (add a `dashboard` key family); loading skeleton and problem+json → inline error state (NFR-3); no client-side computation over raw claims (AD-1)
  - [ ] Cards are static this story — click-to-drill-through interaction arrives in Story 5.5 (leave the card component API ready to accept an `onClick`/href without redesign)
- [ ] Task 5: Tests (AC: all)
  - [ ] Unit tests: `portfolio_summary` for full-scope David Bline (all 100 claims) vs scoped Jennifer Park (Toyota/GM/3M only — counts strictly smaller, every counted claim's employer in her assignment set); threshold values asserted to come from the JDM resolution path, not literals
  - [ ] Unit test: empty scope returns all-zero KPIs (feeds NFR-3 zero states)
  - [ ] Playwright `e2e/stories/5-1-portfolio-kpi-cards.spec.ts` tagged `@story:5-1 @epic:5`, one `@smoke` happy path: login as supervisor → dashboard renders header + 10 KPI cards with values; plus a scoped-persona assertion (Jennifer Park's Total Claims < David Bline's)

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story delivers the dashboard **page shell + the 10 KPI cards** and the first `services/worklist` portfolio aggregate. It is the foundation the rest of Epic 5 stacks onto. It is NOT: handler benchmarking (5.2), charts/Recharts (5.3 — do not install Recharts here), the top-30 worklist (5.4), or any drill-through interaction (5.5 — cards render values only this story). The dashboard is **read-only by role capability**: scope gates visibility, role gates capability (AD-7) — no mutating endpoint exists on any dashboard surface in this epic. The analyst logs into this exact dashboard until Epic 7 differentiates their workspace; do not build any analyst-specific variation.

### Architecture compliance (binding ADs for this story)

- **AD-1:** every KPI number is computed by `services/worklist`; the SPA renders values it received — never sums claims client-side.
- **AD-7:** aggregates run behind the repository scope context `(user_id, role, employer_ids | ALL)`; `scope_all` is a tautology predicate, never a skipped filter; no endpoint accepts caller-supplied scope; no role-conditional filter bypass.
- **AD-8:** the 65/55 thresholds are JDM parameters (DB-versioned, effective-dated); Python owns only the aggregation arithmetic.
- **AD-10:** thresholds and derived inputs (stage, flags, totals) resolve through the single registered computer per value — the dashboard, queue, and claim detail can never disagree on "high risk" or "fraud flagged".
- **AD-9:** server state via TanStack Query under the shared `queryKeys` module; no optimistic anything (read-only surface).
- **Conventions:** camelCase JSON, integer cents, RFC 9457 problem+json → inline error/toast (NFR-3).

### Data notes

- **Tables created: none.** Epic 5 creates no tables — all stories aggregate over existing data (claim, employer, employee, app_user, user_employer_assignment, bill/expense/payment_schedule_week for paid figures).
- **Writes: none.** No AD-12 ownership question arises; `services/worklist` is the computing (not writing) owner of these aggregates. No audit events (queries are not mutations).
- JDM parameter documents are rules content (DB-versioned via the Epic 2/3 rules seeding mechanism), not schema.

### UX notes

- UX-DR7 governs the dashboard: KPI card rows exactly as the prototype's supervisor view (`renderSV()` in `docs/Workers_Comp_Prototype.html`) lays them out — two rows, terse labels, dense cards. Clickability ships in 5.5.
- UX-DR2's top bar (stats + SLA strip) already exists from Epic 1 and must visually agree with the dashboard (same tokens, same values where they overlap).
- **Design-token ruling:** the prototype palette is LIGHT and canonical; epics.md UX-DR12's "dark console aesthetic" wording is a documented discrepancy (see Story 1.1 Dev Notes). Use the Story 1.1 Tailwind tokens; do not invent a dark theme.
- NFR-3 / UX-DR11: loading skeletons, inline error state, and zero-value rendering (a scoped persona with an empty book sees zeros, not blanks or crashes).

### Testing requirements

- Unit: `portfolio_summary` scoping (Jennifer Park = Toyota/GM/3M only; David Bline = all), threshold-from-JDM assertion, empty-scope zeros.
- Web: Vitest smoke on the KPI card component (loading → data → error states).
- E2E (AD-15): `e2e/stories/5-1-portfolio-kpi-cards.spec.ts` tagged `@story:5-1 @epic:5`, exactly one `@smoke` happy path, run against the freshly reset e2e compose stack. **The story cannot move to `review`/`done` until this spec passes.** Selector policy: accessible role first, `data-testid` second, never CSS classes.

### Project Structure Notes

- Server: aggregate in `server/services/worklist/`, router in `server/api/` (thin). Derivation lookups via `server/services/derivations/` registry.
- Web: `web/src/features/dashboard/` (page, KPI card components); query hooks + `dashboard` query keys in `web/src/api/`.
- The generated OpenAPI client picks up the new endpoint — regenerate, don't hand-write the TS types.
- Epic order: Epics 1–4 precede this story in sprint order; the auth deps (1.3), scope context builder (1.4), derivations registry (1.4/2.x), and JDM mechanism (2.1/3.x) all exist. If a prerequisite registered derivation is missing, add it to `services/derivations` — never inline it.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 5.1]
- Epic 5 preamble (read-only by role capability; analyst same dashboard): [Source: _bmad-output/planning-artifacts/epics.md#Epic 5]
- AD-7 scope enforcement, AD-8 two-tier rules, AD-10 one computer per derived value: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- Capability map row "Supervisor KPIs, charts, drill-through" → `services/worklist` + `web/features/dashboard`: [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map]
- Scoped-persona test fixture (Jennifer Park Toyota/GM/3M, David Bline all 100): [Source: _bmad-output/planning-artifacts/epics.md#Story 1.4]
- FR-SUP-1/A: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]
- UX-DR7, UX-DR11: [Source: _bmad-output/planning-artifacts/epics.md#UX Design Requirements]
- Design-token discrepancy ruling: [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]
- AD-15 E2E gate: [Source: ARCHITECTURE-SPINE.md#AD-15]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
