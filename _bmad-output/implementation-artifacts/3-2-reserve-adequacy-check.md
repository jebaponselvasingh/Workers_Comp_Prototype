# Story 3.2: Reserve Adequacy Check

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want an automatic reserve adequacy verdict,
so that under- and over-reserved claims surface before they become problems.

## Acceptance Criteria

1. **Given** a claim, **when** the Reserve Check computes, **then** `services/financials` compares projected remaining exposure (unpaid indemnity + unpaid medical) to the current reserve and classifies Light / Adequate / Heavy with a rationale (FR-H-4, FR-DET-3), computed in exactly one place (AD-2).
2. **Given** the classification bands, **when** resolved, **then** they are parameters in a ZEN JDM document, versioned with effective dates (AD-8).
3. **Given** the verdict, **when** displayed, **then** it renders identically in the Bills financial summary and the treatment-stage Overview card from the same server value (AD-10).
4. **And** unit tests cover all three bands and boundary values.

## Tasks / Subtasks

- [ ] Task 1: Reserve-check function in `services/financials` (AC: 1)
  - [ ] Pure typed core `classify_reserve(reserve_cents, remaining_indemnity_cents, remaining_medical_cents, bands) -> ReserveVerdict` returning `{verdict, ratio, projected_remaining_cents, rationale}` — the single computation site (AD-2)
  - [ ] Classification per the prototype reference (`reserveCheck`, lines 1407–1418): `ratio = projected_remaining / reserve`; `ratio > light_ratio` → `light` (under-reserved, error semantics); `ratio < heavy_ratio` → `heavy` (over-reserved, warn semantics); else `adequate` (ok semantics)
  - [ ] Edge cases codified from the prototype: `reserve == 0` → ratio treated as 2 when remaining > 0 (light) else 1 (adequate); settled stage → distinct `closed_final` verdict ("Claim settled and closed. No further reserve exposure."), no band math
  - [ ] Deterministic rationale strings per verdict (light: exposure exceeds reserve, recommend re-evaluating upward; heavy: reserve comfortably exceeds exposure, consider reallocating surplus; adequate: well aligned) with both figures embedded — service output, never LLM (AD-2)
  - [ ] snake_case enum `reserve_verdict: light|adequate|heavy|closed_final`; UI owns labels ("Reserve Light", …)
- [ ] Task 2: Exposure inputs (AC: 1)
  - [ ] Remaining indemnity = `total_scheduled_cents − paid_so_far_cents` (floored at 0) from the payment-schedule projection in `services/financials` — implement the pure projection math here if 3.3 has not landed it yet (weeks from recovery window, weekly from Story 3.1's `compute_benefit`, statuses by stage/reference date); 3.3 persists rows through this same function — one generator, never two (AD-2)
  - [ ] Remaining medical = sum of `bill` rows with status ≠ `paid`, read via the scope-enforced repository. Sequencing note: the `bill` table is created/seeded by Story 3.3 — wire the query now; if 3.2 is executed before 3.3 it legally returns 0 and verdicts complete when 3.3's seed lands. Unit tests inject fixtures directly, so no test depends on the seed
  - [ ] Assemble in a `reserve_check(claim_id, caller_ctx)` service query; expose on the claim financial read model so both consuming surfaces read one value (AC 3)
- [ ] Task 3: JDM bands document (AC: 2)
  - [ ] ZEN JDM document `reserve_bands` with parameters `light_ratio: 1.15`, `heavy_ratio: 0.60` (prototype-seeded values), DB-versioned with effective dates, loaded via the rules path established in Story 3.1 (AD-8)
  - [ ] Formula (ratio arithmetic, clamping, edge cases) stays in typed Python; only the band thresholds live in JDM — one tier per rule element
- [ ] Task 4: Treatment-stage Overview rendering (AC: 3)
  - [ ] Wire the verdict into Story 2.2's treatment-stage paid-vs-reserve card: verdict chip + rationale ratbox, colored by ok/warn/error tokens (adequate/heavy/light respectively — note the prototype maps light→error, heavy→warn)
  - [ ] The SPA renders the server verdict verbatim — no client-side ratio math (AD-1/AD-9); query key shared via the `queryKeys` module so 3.3's Bills summary consumes the identical cached value
- [ ] Task 5: Tests (AC: 4)
  - [ ] Unit: all three bands; boundary values — ratio exactly `1.15` → adequate (strict `>`), exactly `0.60` → adequate (strict `<`), just above/below each; `reserve == 0` with and without remaining exposure; settled → `closed_final`; floor-at-0 remaining indemnity
  - [ ] Property test (Hypothesis, per the NFR-7 pattern from 3.1): ∀ non-negative inputs, exactly one verdict is returned and `projected_remaining_cents == remaining_indemnity + remaining_medical`
  - [ ] JDM: band values resolve from the effective-dated document, not from constants in code (assert by loading a variant document in a test)
  - [ ] E2E `e2e/stories/3-2-reserve-adequacy-check.spec.ts` `@story:3-2 @epic:3`: `@smoke` happy path — login as handler, open a treatment-stage claim, verdict chip + rationale visible in Overview with one of the three labels

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story is the reserve-adequacy verdict: the classification function, its JDM bands, the exposure assembly, and the treatment-Overview rendering. It builds on 3.1 (`compute_benefit` supplies the weekly figure inside the indemnity projection) and Epic 2 (Story 2.2's treatment Overview card is the render target — extend it, don't rebuild it).

It is **not**: the Bills & Payments tab (3.3 renders this same verdict in its financial summary — AC 3's "identical rendering" is completed by 3.3 consuming this story's server value and shared query key); not schedule/bill persistence (3.3); not payment transitions (3.4). No new tables are created here — `payment_schedule_week`, `bill`, `expense` are all 3.3's migrations. The prototype is a behavior reference only.

**Sequencing seam (deliberate):** projected remaining exposure needs the schedule projection and bill data whose persistence/seed land in 3.3. The projection math is pure and belongs to `services/financials` either way — implement it here if absent, and 3.3 must persist through the same function (AD-2 forbids a second generator). The bill query legitimately returns 0 until 3.3 seeds; verdicts are fully populated once 3.3 lands. This is the honest reading of the epic ordering, not a defect.

### Architecture compliance (binding ADs for this story)

- **AD-1:** the verdict and ratio are computed server-side only; the SPA renders strings and enum values.
- **AD-2:** reserve check exists exactly once in `services/financials`; the rationale is deterministic service text, and Epic 6's "reserve adequacy review" insight may quote it but never re-derive it.
- **AD-7:** the `reserve_check` query goes through scope-enforced repositories with the caller context — a handler cannot compute a verdict for a claim outside their book.
- **AD-8:** bands (1.15 / 0.60) are JDM parameters, effective-dated; ratio math is typed Python. One tier per element.
- **AD-10:** one computing function; the Bills summary (3.3) and treatment Overview must render from the same server value — never two computations, never client math.
- **Conventions:** money integer cents everywhere including across the ZEN boundary (ratios are dimensionless — fine); enums snake_case, UI labels.

### Data notes

- **Creates:** no tables. **Uses:** `claim` (reserve_cents, stage, recovery, disability, severity — via 1.2 seed), Story 3.1's `state_rate_schedule`/benefit, and (once 3.3 lands) `bill` rows read via repository. JDM `reserve_bands` document stored in the DB-versioned rules store.
- Write-owner impact: none — this story is read/compute only; no audit events are emitted by a pure read (AD-4 binds mutations).

### UX notes

- UX-DR5 (Overview tab, treatment variant). Verdict presentation: chip in the card + `ratbox` rationale (prototype lines 1442–1451 show the Bills-side twin for visual consistency; 3.3 implements that side).
- Color semantics ride Epic 1's tokens: light→error, adequate→ok, heavy→warn, closed_final→muted. Design-token ruling: the prototype's LIGHT palette is canonical (epics' "dark console aesthetic" is a documented discrepancy).
- Loading/error states on the financial read model query (NFR-3).

### Testing requirements (this story's definition of done)

- Unit tests on all three bands + boundaries + edge cases (AC 4) and the JDM-resolution test.
- Hypothesis property test on classification totality.
- E2E: `e2e/stories/3-2-reserve-adequacy-check.spec.ts` tagged `@story:3-2 @epic:3`, one `@smoke` happy path, against the freshly reset e2e stack. Story cannot move to `review`/`done` until it passes (AD-15).

### Project Structure Notes

- Code lands in `lineworker/server/services/financials/` (classification + projection), `server/rules/` (JDM `reserve_bands`), `web/src/features/claim-detail/` (Overview card wiring), shared query key in `web/src/api/queryKeys`.
- Register the verdict read model under one query key now; 3.3 reuses it — that is how AC 3's "identical" is enforced structurally rather than by convention.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 3.2]
- AD-2 (reserve check exists once), AD-8, AD-10: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- Capability map row (reserve check → `services/financials`): [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map]
- 115%/60% adequacy bands named in the narrative: [Source: docs/Architecture-LINEWORKER.md#3.2 The three logic layers]
- Behavioral reference: `reserveCheck` (lines 1407–1418), treatment-overview usage (line 1353), Bills-summary twin (1437–1451): [Source: docs/Workers_Comp_Prototype.html]
- FR-H-4 / FR-DET-3: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
