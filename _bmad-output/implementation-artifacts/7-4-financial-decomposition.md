# Story 7.4: Financial Decomposition

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a data analyst,
I want portfolio financials decomposed,
so that cost drivers and reserving quality are quantified.

## Acceptance Criteria

1. **Given** the Financial section, **when** it renders, **then** paid vs. reserve vs. incurred render across the scoped portfolio with breakdowns by the segmentation dimensions (FR-AN-5).
2. **Given** reserve adequacy, **when** the portfolio distribution renders, **then** the Light/Adequate/Heavy distribution uses Epic 3's single reserve-check computation per claim (AD-2, AD-10) — never a re-derivation.
3. **Given** cost-driver analysis, **when** it renders, **then** comparative views quantify surgery vs. non-surgery and litigation vs. non-litigation cost differences (FR-AN-5), each drillable to claims (FR-AN-3 pattern).

## Tasks / Subtasks

- [ ] Task 1: Financial aggregate endpoints in `services/worklist` (AC: 1)
  - [ ] Paid / reserve / incurred portfolio totals plus breakdowns grouped by any Story 7.3 segmentation dimension (employer, sector, state/region, injury type, ICD-10, severity band, age group, gender, disability type), all behind the repository scope context (AD-7) and honoring the active 7.3 filter
  - [ ] Totals come from the registered derivations (`total_paid`, `total_incurred` — AD-10) summed server-side; money is integer cents in DB/API, formatted only in the UI (conventions)
  - [ ] Read-only routes gated to the `analyst` role; loading/error states in the UI (NFR-3)
- [ ] Task 2: Reserve-adequacy distribution (AC: 2)
  - [ ] Per-claim verdicts come from Epic 3's single reserve-check computation in `services/financials` (AD-2); `services/worklist` only counts verdicts into the Light/Adequate/Heavy distribution — it never re-implements the exposure-vs-reserve math or the band thresholds (which live in JDM per AD-8)
  - [ ] Distribution renders with the same verdict labels/colors Epic 3's claim-level Reserve Check uses (one semantic, every surface — AD-10); segment click drills to the claims holding that verdict
  - [ ] Unit test pins agreement: for a sampled claim, the portfolio bucket it lands in matches its claim-detail Reserve Check verdict exactly
- [ ] Task 3: Cost-driver analysis (AC: 3)
  - [ ] Comparative aggregates: surgery vs. non-surgery and litigation vs. non-litigation cohorts — claim counts, total and average paid/incurred, computed server-side from the same totals derivations (AD-10)
  - [ ] Comparative UI (grouped bars or paired stat cards per Epic 5 idioms) quantifying the cost difference; each cohort side drillable to its constituent claims via the 5.5 drill-through list with the cohort + active segmentation filter applied
- [ ] Task 4: Financial section UI (AC: 1, 2, 3)
  - [ ] Add the **Financial** section to the analyst workspace shell (7.1), inheriting the 7.3 segmentation bar (all queries include the active filter in their TanStack Query keys — AD-9)
  - [ ] Recharts stacked/grouped bars + distribution chart reusing Epic 5 chart-card idioms (UX-DR7); status colors from the Story 1.1 tokens; defined empty/zero states per card (NFR-3)
- [ ] Task 5: Tests (AC: all)
  - [ ] pytest: totals correctness over seeded claims (integer-cents arithmetic — no float money), breakdown-by-dimension grouping, adequacy distribution vs per-claim verdict agreement, cost-driver cohort math, scope + filter enforcement
  - [ ] Vitest: Financial section states, money formatting only at render
  - [ ] Playwright `e2e/stories/7-4-financial-decomposition.spec.ts` tagged `@story:7-4 @epic:7`, one `@smoke` happy path: analyst login → Financial section renders paid/reserve/incurred + adequacy distribution → apply a segmentation filter → figures recompute → click a cost-driver cohort → drill-through claim list → read-only claim view

## Dev Notes

> **Prerequisite advisory: run `bmad-ux` for the analyst workspace surfaces before starting this epic (readiness report, standing advisory #1). If a UX spec exists by dev time, it supersedes the layout suggestions here.** No prototype screen exists for financial decomposition; where layout is open below, reuse Epic 5's established dashboard patterns (KPI cards, Recharts chart cards, drill-through lists) rather than inventing new idioms.

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

The Financial section of the analyst workspace: paid/reserve/incurred decomposition, the portfolio reserve-adequacy distribution, and surgery/litigation cost-driver comparisons. It is **not**: any change to Epic 3's financial engine (benefit calc, reserve check, schedules are consumed, never touched), reserve re-derivation of any kind (AC 2 is explicit — the portfolio distribution is a count of Epic 3's per-claim verdicts), trend-over-time financials (7.2 owns the cost-trend series), export (7.5), or actuarial modeling (loss development, IBNR, forecasting — none of that exists in scope). No mutation endpoints; the analyst surface stays read-only by role capability.

### Architecture compliance (binding ADs for this story)

- **AD-1:** every figure on screen comes from a `services/worklist` aggregate endpoint; the SPA sums nothing.
- **AD-2:** reserve-check math exists exactly once in `services/financials`; this story calls it (or its persisted per-claim verdict path) and aggregates the results — a second exposure-vs-reserve implementation is an architecture violation, not a shortcut.
- **AD-7:** aggregates behind the repository scope context, honoring the 7.3 filter, which can only narrow scope; routes read-only, role-gated to analyst.
- **AD-8:** reserve-band thresholds (the 115%/60% adequacy bands) stay JDM parameters read by the one financials computation — this story never reads them directly.
- **AD-10:** `total_paid` / `total_incurred` / reserve figures come from their single registered derivations; the dashboard, claim detail, and this section can never disagree because they call the same functions.
- **AD-9:** TanStack Query with filter-inclusive keys in the shared `queryKeys` module.
- **AD-15:** story spec + done-gate (see Testing requirements).

### Data notes

Creates **no tables**. Reads: `claim` (reserve, surgery/litigation flags, segmentation columns), `bill` / `expense` / `payment_schedule_week` via the totals derivations (write-owned by `services/financials` per AD-12 — this story reads through derivations only), `employee` / `employer` for breakdown dimensions. Aggregation lives in `services/worklist`; the per-claim reserve verdict lives in `services/financials` (AD-12 ownership unchanged). Money is integer cents end-to-end below the UI (conventions).

### UX notes

No UX-DR covers this screen (the Epic 7 gap). Borrow: UX-DR7 (Recharts idioms; every segment/row clickable for drill-through), UX-DR11 (loading/empty/error states), Epic 3's Reserve Check verdict styling (Light/Adequate/Heavy labels + status colors must match the claim-detail card exactly). ⚠️ Design-token ruling: the epics' UX-DR12 "dark console aesthetic" is a **documented discrepancy** — the prototype palette is LIGHT and canonical; use Story 1.1's Tailwind tokens.

### Testing requirements

- pytest: integer-cents aggregate correctness, dimension breakdowns, the verdict-agreement test (portfolio bucket == claim-detail verdict for sampled claims), cost-driver cohort math, scope/filter enforcement. Property-based (Hypothesis) checks on aggregation invariants (e.g. sum of breakdown groups == portfolio total under any filter) fit NFR-7's posture for financial code.
- Vitest: section states; cents-to-display formatting at the edge only.
- Playwright `e2e/stories/7-4-financial-decomposition.spec.ts` tagged `@story:7-4 @epic:7` (anchored grep), exactly one `@smoke` happy path, against the freshly reset e2e stack. **The story cannot move to review/done until this spec passes (AD-15 done-gate).**

### Project Structure Notes

- UI in `web/src/features/dashboard/` (analyst subfolder from 7.1); aggregates in `server/services/worklist/`; reserve verdicts consumed from `server/services/financials/`; thin routes in `server/api/`.
- Depends on 7.1 (shell), 7.3 (segmentation schema + bar — this section inherits it), and Epic 3 (reserve check, totals). 7.5's export will reuse these endpoints' underlying aggregate functions — keep them cleanly callable from the service layer, not router-bound.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 7.4]
- FR-AN-3, FR-AN-5: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]
- Epic 7 UX gap + advisory: [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Standing Advisories item 1]
- Single reserve-check computation + adequacy bands: [Source: _bmad-output/planning-artifacts/epics.md#Story 3.2]; [Source: docs/Architecture-LINEWORKER.md#3.2 The three logic layers]
- AD-2 / AD-8 / AD-10 / AD-12 ownership: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-2 / #AD-8 / #AD-10 / #AD-12 / #Capability → Architecture Map]
- Money-as-cents + list conventions: [Source: ARCHITECTURE-SPINE.md#Consistency Conventions]
- E2E gate: [Source: ARCHITECTURE-SPINE.md#AD-15]
- Design-token ruling (light palette canonical): [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]

## Dev Agent Record

### Agent Model Used

Claude Opus 5 (1M context), via `bmad-dev-auto` — plan, implement, two adversarial review passes, patch.

### Debug Log References

### Completion Notes List

- Spec, review triage and full result: [spec-7-4-financial-decomposition.md](spec-7-4-financial-decomposition.md).
- All three ACs met. AC 2's "never a re-derivation" is enforced as agreement: every seeded claim's portfolio bucket is compared against `reserve_check_for_claim`'s own verdict, and the claim-level path is in turn compared against the rule restated in `seed_fixture.py`.
- Two decisions the story file left open: the distribution publishes **all five** `ReserveVerdict` members rather than only Light/Adequate/Heavy (the two others hold most of a settled book, so three buckets would have contradicted `claimsInScope`), and the third money figure is published as `projectedCents` / "Total (projected)" rather than "incurred", following `claim_financials.py`'s recorded refusal to spread that naming discrepancy.
- Six deferred entries were recorded across implementation and review; none blocks 7.5, which reuses these aggregate functions for export.

### File List
