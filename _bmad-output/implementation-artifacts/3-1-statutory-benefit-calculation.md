# Story 3.1: Statutory Benefit Calculation

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want the weekly indemnity benefit computed from AWW with statutory clamping and an override,
so that benefit amounts are correct and defensible.

## Acceptance Criteria

1. **Given** a claim, **when** the benefit card renders in Overview, **then** it shows the weekly indemnity computed once in `services/financials`: comp-rate % of AWW (default 66.67%; 100% PTD when disability is permanent and severity ≥ 85), clamped to the state statutory min/max from a maintained `state_rate_schedule` table created and seeded by this story (FR-H-3, NFR-4).
2. **And** all money is integer cents end to end, formatted only in the UI.
3. **Given** the derived indemnity type, **when** displayed, **then** TTD / TPD / PPD / PTD is determined from disability + return status by the single registered derivation, and the card shows type, state min/max, the 7-day waiting-period payment note, and the auto-generated reserve rationale paragraph.
4. **Given** the editable comp-rate override, **when** changed, **then** an audited CAS command persists it, the benefit recomputes server-side, and a ↺ reset control restores the default (FR-H-3, AD-4).
5. **Given** the benefit formulas, **when** tested, **then** they live in typed Python with tunables (default rate, PTD threshold) read from ZEN (AD-8), and Hypothesis property tests assert the clamp holds across statutory ranges for all seeded states (NFR-7).

## Tasks / Subtasks

- [ ] Task 1: `state_rate_schedule` migration + seed (AC: 1, 2)
  - [ ] Alembic migration creating `state_rate_schedule` (surrogate `id` PK, `state_code` unique, `state_name`, `weekly_min_cents int`, `weekly_max_cents int`, `effective_date date`) — money columns integer cents, snake_case per the Excel canonical naming
  - [ ] Seed the prototype's 17 jurisdictions (`STATE_WC_RATES`, prototype line 638–645: OH/MN/IL/WA/TX/MA/IA/MI/SC/KY/AL/NJ/SD/IN/CA/NC/AZ), dollar values × 100 into cents
  - [ ] Migration-time assertion: every seeded claim's `state` has a `state_rate_schedule` row — the prototype's silent `{max:1200,min:250}` fallback (line 717) does NOT survive; a missing state is a data error surfaced as problem+json, not a default (NFR-4)
- [ ] Task 2: Benefit formula in `services/financials` (AC: 1, 2, 5)
  - [ ] Typed function `compute_benefit(claim, rate_row, params) -> Benefit` returning `{weekly_cents, comp_rate_pct, default_pct, indemnity_type, state_min_cents, state_max_cents, waiting_days, is_overridden}`
  - [ ] Formula (typed Python): `weekly_cents = round(aww_cents * pct / 100)` then clamp `max(min_cents, min(max_cents, weekly_cents))`; document the rounding convention (round-half-up on cents) in the function docstring
  - [ ] PTD branch: `disability == permanent AND severity_score >= ptd_threshold` → comp rate 100%
  - [ ] Tunables from a ZEN JDM document (`benefit_params`): `default_comp_rate_pct: 66.67`, `ptd_comp_rate_pct: 100`, `ptd_severity_threshold: 85`, `waiting_period_days: 7` — DB-versioned with effective dates (AD-8); the formula itself never lives in JDM
  - [ ] Expose via the claim-detail read model / a `GET .../benefit` projection so the SPA never computes any of this (AD-1)
- [ ] Task 3: Indemnity-type derivation (AC: 3)
  - [ ] Single registered function in `services/derivations`: permanent → `ptd` (when PTD branch true) else `ppd`; temporary → `tpd` when return status indicates therapy/modified duty else `ttd` (prototype line 723–725 is the behavioral reference)
  - [ ] snake_case enum `indemnity_type: ttd|tpd|ppd|ptd` in DB/API; UI owns the display labels ("TTD — Temporary Total Disability", …)
  - [ ] Never a user-writable column (AD-10)
- [ ] Task 4: Reserve rationale paragraph (AC: 3)
  - [ ] Server-side deterministic template in `services/financials` mirroring prototype line 1257: severity + injury + recovery window, surgical-exposure clause, litigation-buffer clause, permanent-vs-TTD clause, statutory min/max reference, and the override clause when `is_overridden`
  - [ ] This is deterministic service output, NOT an LLM narrative (AD-2) and NOT an `ai_insight` row — it renders synchronously with the benefit card
- [ ] Task 5: Comp-rate override command (AC: 4)
  - [ ] Nullable `comp_rate_override_bp int` (basis points; 6667 = 66.67%) on `claim` — exact integer storage, no floats in DB
  - [ ] PATCH-shaped audited CAS command in `services/claims` (the claim entity's write-owner, AD-12) with `expected_version`; validation 0–150% inclusive rejected inline via problem+json (prototype input bounds, line 731/1261); audit event with before/after in the same transaction (AD-4)
  - [ ] Reset command (↺) nulls the override through the same audited CAS path; benefit recomputes on read in `services/financials`
  - [ ] SPA: editable number input + ↺ reset shown only when overridden; optimistic update allowed (user-entered scalar), 409 rollback renders fresh state inline (AD-9); mutation invalidates the claim + benefit query keys
- [ ] Task 6: Benefit card UI (AC: 1, 2, 3, 4)
  - [ ] "Benefit calculation & reserve details" card in the Overview tab (in the stage variants that carry it in the prototype — investigation and treatment), fields: Comp rate (editable, ↺ when overridden) · Weekly indemnity benefit · Indemnity type · state statutory min/max · "Weekly, starting 7-day waiting period after DOI" payment-schedule note · state name · reserve-rationale ratbox (prototype 1258–1273 for layout/feel)
  - [ ] All money formatted in the UI only, from cents (AC 2); figures typeset in the mono token per Epic 1 design tokens
- [ ] Task 7: Tests (AC: 5)
  - [ ] Hypothesis property test: ∀ seeded state rows, ∀ `aww_cents ≥ 0`, ∀ valid override pct ∈ [0,150] → `state_min_cents ≤ weekly_cents ≤ state_max_cents` (NFR-7)
  - [ ] Unit tests: PTD branch on/off around threshold 85 (84/85/86), default vs override, reset restores default, indemnity-type derivation for all four types, missing-state error path
  - [ ] Command tests: CAS 409 on stale version; audit row emitted in-transaction; out-of-range override rejected
  - [ ] E2E `e2e/stories/3-1-statutory-benefit-calculation.spec.ts` `@story:3-1 @epic:3`: `@smoke` happy path — login as handler persona, open a claim, benefit card shows weekly/type/min-max; then edit comp rate → recompute; ↺ reset → default restored

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story is the first slice of the Epic 3 financial engine: the benefit **formula**, its statutory rate table, the indemnity-type derivation, the rationale paragraph, and the comp-rate override command + card UI. It builds on Epic 1 (schema/auth/scoping/e2e harness) and Epic 2 (detail pane, stage-adaptive Overview from Story 2.2 — the card slots into that existing Overview; do not rebuild the tab).

It is **not**: the reserve adequacy verdict (3.2), the payment schedule or `bill`/`expense` tables (3.3), payment approval/batch (3.4), or the action checklist (3.5). The 7-day waiting period is displayed here as the payment note; the schedule that *applies* it (start = DOI + 7 days) is generated in 3.3. Do not create `payment_schedule_week` here. The prototype (`docs/Workers_Comp_Prototype.html`) is a behavior/design reference only — never a code source.

### Architecture compliance (binding ADs for this story)

- **AD-1:** the benefit figure appears on screen ⇒ a service computed it; the SPA never multiplies AWW by anything.
- **AD-2:** `compute_benefit` exists exactly once, in `services/financials`; every consumer (Overview card, 3.3 schedule, Epic 6 copilot tools) calls it — no re-derivation anywhere.
- **AD-4:** the override is a PATCH-shaped, `expected_version`-CAS'd, audited command; 409 problem+json with fresh entity on mismatch.
- **AD-8:** tunables (66.67 default, PTD 100/85, waiting 7) live in a DB-versioned, effective-dated ZEN JDM document; the clamp/rounding formula is typed Python. Each rule element in exactly one tier.
- **AD-10:** `indemnity_type` has one registered derivation in `services/derivations`; not user-writable.
- **AD-12:** `claim` writes (the override field) go through `services/claims` commands; `services/financials` computes but does not write the claim row.
- **Conventions:** money integer cents in DB, API, and across the ZEN boundary (the pct tunables are rates, not money); camelCase JSON via Pydantic alias; enums snake_case with UI labels.

### Data notes

- **Creates + seeds:** `state_rate_schedule` (reference data; refresh ownership/update cadence is an architecture Deferred item — seed the 17 prototype states, structure for effective-dating so a later refresh process slots in).
- **Alters:** `claim` — adds nullable `comp_rate_override_bp` (write-owner `services/claims`).
- **Uses:** `claim` (aww, disability, severity_score, return_status, state), seeded by Story 1.2.
- The prototype stores AWW in whole dollars (`aww:1432`); the 1.2 seed already carries cents — verify the seed's `aww_cents` before wiring the formula (a ×100 mismatch is the classic bug here).

### UX notes

- UX-DR5 (6-tab detail pane; the card lives inside Overview's stage variants). Card layout, kv rows, ratbox rationale, ↺ affordance: prototype lines 1255–1274.
- Inline validation on the override input, no native dialogs (NFR-3, UX-DR11); 409 conflict renders fresh state inline at the field (AD-9).
- Design-token ruling (from Story 1.1): the prototype's LIGHT palette is canonical; epics.md's "dark console aesthetic" wording is a documented discrepancy. Use Epic 1's tokens — no new colors.
- Drop the prototype's "Illustrative figures" disclaimer line only when NFR-4 validation is done; until statutory data is production-validated (Deferred), keep an equivalent provenance note sourced from the `state_rate_schedule` effective date.

### Testing requirements (this story's definition of done)

- Hypothesis property tests on the clamp across all seeded states (AC 5, NFR-7) — this is the named NFR-7 exemplar; do not substitute example-based tests.
- Unit: PTD threshold boundaries, all four indemnity types, override/reset, missing-state error.
- Command: CAS 409, in-transaction audit emission, validation rejection.
- E2E: `e2e/stories/3-1-statutory-benefit-calculation.spec.ts` tagged `@story:3-1 @epic:3`, exactly one `@smoke` happy path, run against the freshly reset e2e compose stack. Story cannot move to `review`/`done` until it passes (AD-15).

### Project Structure Notes

- New code lands in `lineworker/server/services/financials/` (formula, rationale), `services/derivations/` (indemnity type), `services/claims/` (override command), `server/rules/` (JDM `benefit_params` document + ZEN wrapper first use — the ZEN engine dependency (`zen-engine`, pinned) is installed by this story if Epic 2 has not already), `server/data/` (migration), `web/src/features/claim-detail/` (card).
- First JDM document of the build: establish the loading path (DB-versioned document, effective-date selection) cleanly — 3.2 and 3.5 reuse it.
- Rounding convention and basis-point storage are decisions made here; record them in code docstrings so later financial stories inherit them.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 3.1]
- AD-2 deterministic core, AD-8 two-tier rules, AD-4 audited CAS, AD-10 derivations, AD-12 ownership: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- Money-in-cents (incl. ZEN boundary), enums, write-concurrency conventions: [Source: ARCHITECTURE-SPINE.md#Consistency Conventions]
- Capability map row (benefit calc → `services/financials`): [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map]
- Deferred: state statutory coverage & refresh cadence: [Source: ARCHITECTURE-SPINE.md#Deferred]
- Behavioral reference: `computeBenefit`/override (lines 715–738), `STATE_WC_RATES` (638–645), benefit card + rationale (1255–1274): [Source: docs/Workers_Comp_Prototype.html]
- Narrative on the three logic layers: [Source: docs/Architecture-LINEWORKER.md#3.2 The three logic layers]
- FR-H-3 / NFR-4 definitions: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
