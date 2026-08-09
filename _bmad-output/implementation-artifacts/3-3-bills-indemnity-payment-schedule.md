# Story 3.3: Bills & Indemnity Payment Schedule

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want the full financial picture — bills and a week-by-week indemnity schedule,
so that I can track what's paid, due, and pending.

## Acceptance Criteria

1. **Given** the Bills & Payments tab, **when** it renders, **then** the financial summary shows Total Claim Amount (projected), Paid To Date, Reserve Remaining, and the Reserve Check verdict, plus the metrics row (Weekly Indemnity, Installments Paid, Next Payment Due, Bills On File) (FR-H-7).
2. **Given** the indemnity schedule, **when** generated, **then** `payment_schedule_week` rows (table created here, owned by `services/financials` — AD-12) come from the financials service exactly once and render week-by-week with statuses `paid` / `due_this_week` / `upcoming` / `pending_approval` (snake_case enums, UI-owned labels).
3. **Given** the medical bills list, **when** it renders, **then** `bill` and `expense` rows (both tables created and seeded here) show by category (initial treatment, surgery/facility, imaging, PT, follow-up, pharmacy) with statuses Paid / Under Review / Pending Submission, and expense totals feed the settled-stage payout breakdown.
4. **Given** the treatment-stage Overview paid-vs-reserve card, **when** its "View full Bills & Payments →" link is clicked, **then** it jumps to this tab and both surfaces show identical figures (AD-10).

## Tasks / Subtasks

- [ ] Task 1: Migrations — `payment_schedule_week`, `bill`, `expense` (AC: 2, 3)
  - [ ] `payment_schedule_week`: surrogate `id`, `claim_id` FK, `week_no int`, `period_start date`, `period_end date`, `amount_cents int`, `status` enum `paid|due_this_week|upcoming|pending_approval|payment_scheduled`, `version int` (mutable entity — CAS per conventions), unique `(claim_id, week_no)`. Define `payment_scheduled` in the enum now; no row carries it until Story 3.4's approval command
  - [ ] `bill`: surrogate `id`, `claim_id` FK, `category` enum (`initial_treatment|surgery_facility|imaging|physical_therapy|follow_up|pharmacy`), `label`, `amount_cents int`, `status` enum `paid|under_review|pending_submission|payment_scheduled`, `version int`
  - [ ] `expense`: same shape; `category` enum (`mileage_travel|dme|prosthetic_assistive|home_workstation_mod|misc`)
  - [ ] Snake_case per the Excel canonical naming; money integer cents; app-role grants consistent with 1.2 (no derived user-writable columns)
- [ ] Task 2: Schedule generation in `services/financials` (AC: 2)
  - [ ] One generator (the same pure projection referenced by 3.2 — extend it, never fork it): weeks parsed from recovery window (`"N-M Weeks"` → M; `"…Year"` → 26; clamp 4–20 — prototype lines 742–745), start = DOI + waiting-period days (JDM `benefit_params` from 3.1), weekly amount from 3.1's `compute_benefit`
  - [ ] Status assignment relative to the reference date: intake/investigation stage → all `pending_approval`; settled → `paid`; past weeks → `paid`; current week → `due_this_week`; future → `upcoming` (prototype lines 753–758). Reference date is a registered derivation (AD-10), not `Date.now()` scattered in code
  - [ ] Materialization command (owner: `services/financials`) writes/refreshes `payment_schedule_week` rows for a claim — **status-preserving**: regeneration never clobbers `payment_scheduled`/`paid` rows written by approval/batch (the exact AD-12 clobbering scenario the spine names); audited (AD-4)
  - [ ] Seed migration materializes schedules for the 100 seeded claims through this command path
- [ ] Task 3: Bill & expense seed (AC: 3)
  - [ ] Seed `bill` rows per claim mirroring the prototype's deterministic builder (lines 766–783): initial treatment always; surgery/facility when `surgery_required`; imaging always; PT bundle when the treatment plan includes therapy; follow-up visit; pharmacy when disabled — amounts and statuses precomputed deterministically (hash-variance is fine to reproduce; it's seed data, not runtime logic)
  - [ ] Seed `expense` rows per the prototype builder (lines 784–800): mileage/travel; DME when past intake; prosthetic when surgical; home/workstation modification when permanent; misc
  - [ ] Settled-stage claims seed everything `paid` (matches prototype), so expense totals correctly feed the settled-stage payout breakdown (readiness-review fix: `expense` was added to this story precisely for that consumer)
- [ ] Task 4: Financial summary + derivations (AC: 1, 4)
  - [ ] Registered derivations in `services/derivations` (one computer each, AD-10): `paid_to_date_cents` (indemnity paid-so-far + paid bills + paid expenses, honoring the seeded static paid fields when present — prototype's effective-breakdown logic, lines 1428–1436), `total_claim_projected_cents` (= paid to date + reserve), `installments_paid`, `next_payment_due` (first `due_this_week` else first `upcoming` week), `bills_on_file`
  - [ ] One claim-financials read model endpoint returning summary + metrics + schedule + bills + expenses + the 3.2 reserve verdict (same server value, same query key — AC 1/AC 4)
  - [ ] Cost bar breakdown (Indemnity/Medical/Expenses % of paid) server-computed
- [ ] Task 5: Bills & Payments tab UI (AC: 1, 2, 3, 4)
  - [ ] Replace Epic 2's empty-state Bills tab shell with: financial summary card (4 paycards + cost bar + reserve-check ratbox), week-by-week schedule table (Wk / Period / Amount / Status chips), medical-bills card ("n on file, $x of $y paid"), expenses card with empty state ("No expenses filed for this claim") (prototype 1419–1459)
  - [ ] Status chips use UI-owned labels over snake_case enums ("Due This Week", "Pending Approval", "Under Review", "Pending Submission", "Payment Scheduled", "Paid"); row/line-item click opens the read-only detail sheet (approval buttons inside arrive with 3.4)
  - [ ] Treatment-stage Overview paid-vs-reserve card gets the "View full Bills & Payments →" jump-link switching to this tab; both surfaces read the same TanStack Query key so figures cannot diverge (AD-9/AD-10)
  - [ ] Loading / error / empty states throughout (NFR-3)
- [ ] Task 6: Tests (AC: all)
  - [ ] Unit: weeks parsing (ranges, "Greater than 1 Year", clamp bounds 4 and 20), status assignment per stage and reference date, status-preserving regeneration, derivations (paid-to-date with and without static paid fields, next-due selection)
  - [ ] Property (Hypothesis): ∀ claims, `sum(week amounts) == weekly × weeks`; regeneration is idempotent on unchanged inputs
  - [ ] Migration: `alembic upgrade head` clean on fresh DB; seed materializes schedules/bills/expenses for all 100 claims
  - [ ] E2E `e2e/stories/3-3-bills-indemnity-payment-schedule.spec.ts` `@story:3-3 @epic:3`: `@smoke` happy path — handler opens a treatment claim, Bills tab shows summary + schedule + bills; Overview jump-link lands on the tab with matching Paid To Date figure asserted on both surfaces

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story delivers the financial *picture*: three tables, the schedule generator's persistence, the seed, the derivations, and the Bills & Payments tab content (Epic 2 shipped the tab shell with an explicit empty state — fill it, don't rebuild the tab bar). It consumes 3.1 (`compute_benefit`) and 3.2 (reserve verdict — render the same server value; do not recompute).

It is **not**: payment approval or the batch (3.4 — no status-mutating commands on weeks/bills/expenses ship here beyond the status-preserving generator; the line-item sheets render without Approve buttons until 3.4), and not the action checklist (3.5). The "next batch: Tuesday/Friday" messaging line belongs to 3.4 with the batch itself. The prototype is a behavior reference only — its in-memory `scheduleStatusOverride`/`billStatusOverride` maps (lines 803–845) are exactly what real rows replace.

### Architecture compliance (binding ADs for this story)

- **AD-2:** payment-schedule generation exists exactly once in `services/financials`; 3.2's projection and this story's persistence are one function, not two.
- **AD-4:** the materialization command and seed writes are audited service commands; mutable rows carry `version` for CAS from birth.
- **AD-10:** every summary figure (`paid_to_date`, `total_claim_projected`, `installments_paid`, `next_payment_due`, `bills_on_file`) has one registered derivation; Overview card and Bills tab call the same one (AC 4 is an AD-10 test case).
- **AD-12:** `payment_schedule_week`, `bill`, `expense` are owned by `services/financials` — the only writer. Story 3.4's worklist approval will *call* financials' command; nothing else touches these tables. Regeneration must never clobber approval/batch statuses (the spine names this exact failure mode).
- **AD-7:** all reads via scope-enforced repositories with caller context.
- **Conventions:** snake_case enums with UI labels; money integer cents; `{items, nextCursor}` if any list endpoint paginates (the per-claim schedule is small — embedding in the read model is acceptable; document the choice).

### Data notes

- **Creates + seeds:** `payment_schedule_week` (owner `services/financials`), `bill`, `expense` (readiness review explicitly moved `expense` creation here — it feeds the settled-stage payout breakdown rendered by Story 2.2's settled Overview; after this story's seed, verify that surface populates).
- **Uses:** `claim` (doi, recovery, stage, days_open, paid_* static fields, reserve), 3.1 benefit, 3.2 verdict.
- Enum-label quirk to NOT copy: the prototype labels bill status `PendingSubmission` as "Payment Scheduled" (line 806) — a conflation. Production keeps `pending_submission` ("Pending Submission") and `payment_scheduled` ("Payment Scheduled") as distinct enum values with distinct labels; 3.4's approval moves `under_review` → `payment_scheduled`.

### UX notes

- UX-DR5 (Bills & Payments is tab 3 of the 6-tab pane). Layout/feel: prototype `billsHTML` lines 1419–1459 — 4-up paycard row, cost bar with legend, ratbox verdict, mono-font amounts, dense tables.
- Reserve-check chip colors ride Epic 1 tokens (light→error, adequate→ok, heavy→warn). Design-token ruling: prototype LIGHT palette is canonical; epics' "dark console aesthetic" is a documented discrepancy.
- Empty states: expenses card ("No expenses filed"), zero-payments cost bar ("No payments disbursed yet — reserve of $X held against projected exposure") (NFR-3).

### Testing requirements (this story's definition of done)

- Unit + Hypothesis property tests on generation and derivations (NFR-7 pattern).
- Migration + seed verified against a fresh DB (CI job).
- E2E: `e2e/stories/3-3-bills-indemnity-payment-schedule.spec.ts` tagged `@story:3-3 @epic:3`, one `@smoke` happy path including the jump-link figure-identity assertion. Story cannot move to `review`/`done` until it passes (AD-15).

### Project Structure Notes

- Code lands in `lineworker/server/services/financials/` (generator, materialization command), `services/derivations/` (summary figures), `server/data/` (three migrations + seed), `web/src/features/claim-detail/` (Bills tab content, Overview jump-link), query keys in `web/src/api/queryKeys`.
- The claim-financials read model built here is the single financial surface later consumed by 3.4 (post-batch refresh), 3.5 (bill-review actions), 5.x aggregates, and Epic 6 tools — keep it one endpoint/query key.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 3.3]
- AD-12 ownership incl. the schedule-clobbering prevention, AD-2, AD-10: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- Capability map row (`services/financials` owns `payment_schedule_week`, `bill`, `expense` writes): [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map]
- Payment-transition enum contract: [Source: ARCHITECTURE-SPINE.md#Consistency Conventions (Enums & statuses)]
- `expense` creation assigned here by readiness review: [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Defects Found & Remediated During Review]
- Behavioral reference: `buildPaymentSchedule` (740–765), `buildBills` (766–783), `buildExpenses` (784–800), status labels (806, 843–845), `billsHTML` (1419–1459): [Source: docs/Workers_Comp_Prototype.html]
- FR-H-7: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
