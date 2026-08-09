# Story 3.4: Payment Approval & Batch

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want payment approvals to schedule disbursement and a batch to execute it,
so that indemnity goes out on time with a clean status trail.

## Acceptance Criteria

1. **Given** a schedule week pending approval, **when** the handler approves it, **then** the worklist approval command calls the owning financials command (AD-12), which CAS-guards, sets `payment_scheduled`, and emits an audit event (AD-4).
2. **Given** the payment batch (in-process scheduled job per the deferred scheduler decision), **when** it runs, **then** it transitions only `payment_scheduled` rows to `paid` — status-guarded so re-runs are idempotent and nothing else ever marks `paid` (conventions; Excel row-29/64 resolution).
3. **Given** batch completion, **when** figures refresh, **then** affected claims' Paid To Date, Installments Paid, and Next Payment Due recompute from derivations, and each transition carries an audit event.

## Tasks / Subtasks

- [ ] Task 1: Financials approval command (AC: 1)
  - [ ] `services/financials.approve_schedule_week(claim_id, week_no, expected_version, caller_ctx)`: CAS on `expected_version` AND status-guard on expected current status (`pending_approval` or `due_this_week`/`upcoming` per the approvable set — document the chosen set; the AC's canonical case is `pending_approval`) → sets `payment_scheduled`, increments version, emits audit event in the same transaction (AD-4)
  - [ ] Companion `approve_bill_payment` / `approve_expense_payment` commands: `under_review` → `payment_scheduled` (Excel rows 64–66 write-back; prototype `approvePayment`, lines 818–822) — same CAS + status-guard + audit shape
  - [ ] Guard violations (wrong status, stale version) → 409 problem+json carrying the fresh row; no force-write, no read-modify-write
  - [ ] `timeline_event` emitted by the owning command as a side effect (AD-12) so approvals appear in the Overview timeline
- [ ] Task 2: Worklist approval path (AC: 1)
  - [ ] `services/worklist` approval command (the surface 3.5's actions and the UI call) delegates to the owning financials command — it never writes `payment_schedule_week`/`bill`/`expense` itself (AD-12); role gates capability: handler role required (AD-7)
  - [ ] API route(s) exposing approval; SPA wires the "✓ Approve Payment" button into the week/line-item detail sheets shipped by 3.3, with optimistic-free update (status is server-derived — invalidate and refetch per AD-9), 409 rendered inline
- [ ] Task 3: Payment batch (AC: 2)
  - [ ] `services/financials.run_payment_batch()` command: single UPDATE-where-status transition `payment_scheduled → paid` across schedule weeks, bills, and expenses; per-row audit events in the same transaction; returns a run summary (rows paid, claims touched)
  - [ ] Status guard makes re-runs idempotent by construction: a re-run selects nothing new; running with zero eligible rows is a clean no-op. **Nothing else in the codebase ever sets `paid`** — enforce with a service-layer assertion/test, not convention (Excel row-29 vs row-64 conflict resolved per the spine)
  - [ ] In-process scheduling inside the `api` process (mechanism deferred — a minimal asyncio periodic task is sufficient; do NOT add a worker container or pin APScheduler as an architectural choice): cadence from config (default Tue/Fri per prototype `nextBatchDate`, lines 808–817), and the command remains directly invocable (tests, e2e, admin trigger)
  - [ ] `next_batch_date` registered derivation from the configured cadence; Bills summary shows "Approved payments are disbursed in the next scheduled batch run… Next batch: <date>" (server-computed, prototype line 1451)
- [ ] Task 4: Post-batch refresh (AC: 3)
  - [ ] Paid To Date / Installments Paid / Next Payment Due recompute via 3.3's registered derivations — no new computation sites (AD-10)
  - [ ] SPA invalidates the claim-financials query key(s) after approval mutations; batch-driven changes surface on normal refetch — document that no push channel exists yet (acceptable; nothing in the ACs requires live push)
  - [ ] Approved week sheet shows "✓ Scheduled for next payment batch — <date>"; paid rows show paid state (prototype line 865)
- [ ] Task 5: Tests (AC: all)
  - [ ] Unit: approval sets `payment_scheduled` + audit; wrong-status approval 409s; stale-version approval 409s; batch transitions only `payment_scheduled`; double-run idempotency (second run pays 0); `pending_approval`/`due_this_week`/`upcoming`/`paid` rows untouched by batch; derivations reflect post-batch state
  - [ ] Ownership test: grep/architecture test asserting no module outside `services/financials` writes the three payment tables or the `paid` status (AD-12 regression floor)
  - [ ] E2E `e2e/stories/3-4-payment-approval-batch.spec.ts` `@story:3-4 @epic:3`: `@smoke` happy path — handler opens a pending-approval week, approves → chip shows "Payment Scheduled" + batch note; trigger the batch (direct command invocation via the e2e harness/API hook) → chip shows "Paid", Paid To Date and Installments Paid increase, audit trail asserted via timeline

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story is the canonical payment lifecycle: approval commands (`→ payment_scheduled`) and the batch (`payment_scheduled → paid`), plus the figure refresh. It builds directly on 3.3's tables, read model, and detail sheets — the Approve buttons land in sheets 3.3 already renders.

It is **not**: schedule/bill generation or seeding (3.3), the action checklist that *links* to bill review (3.5 — the "Approve Payment" button inside the bill sheet ships here, so 3.5's bill-review deep link lands on an already-working surface), a worker-container scheduler, real disbursement/banking integration, or push notifications. The scheduler *mechanism* is an explicit architecture Deferred item — keep it in-process and boring; the `payment_scheduled → paid` transition contract is the fixed part.

**The one-sentence invariant to protect:** approval never marks `paid`, and only the batch ever does. The Excel contradicts itself here (row 29: approval marks Paid; rows 64–66: approval sets Payment Scheduled); the spine's conventions resolve it in favor of rows 64–66, and this story implements that resolution.

### Architecture compliance (binding ADs for this story)

- **AD-4:** every transition (approve, batch per-row) is a CAS'd, status-guarded, audited command — "lifecycle/status updates additionally guard on expected current status (the payment batch pays only `payment_scheduled` rows)" is quoted verbatim from the spine; this story is its reference implementation.
- **AD-12:** `services/financials` is the sole writer of `payment_schedule_week`/`bill`/`expense`; the worklist approval command calls the owning financials command — the spine names this exact delegation.
- **AD-7:** approval routes require handler capability and scope-checked claim access; the batch runs as a system actor (record a system `actor_id` in audit events — establish the convention here and document it).
- **AD-10:** post-batch figures come from 3.3's registered derivations only.
- **AD-9:** payment statuses are server-derived — no optimistic status flips; invalidate and refetch; 409 inline.
- **Conventions:** payment transition contract (`payment_scheduled → paid`, batch-only); scheduled jobs in-process (deployment note in the spine's Structural Seed); config-driven cadence, never hardcoded.

### Data notes

- **Creates:** no tables (the `payment_scheduled` enum value was defined by 3.3). **Mutates:** `payment_schedule_week`, `bill`, `expense` via owning commands; emits `audit_event` + `timeline_event`.
- Batch audit volume: one audit event per row transition (AC 3 says "each transition carries an audit event") — do not collapse to one event per run.
- System actor for the batch: seed or designate a system `app_user` (or a documented null-actor convention) so the fixed audit schema's `actor_id` stays honest — decide, document in code, and note it in the Dev Agent Record for Epic 8's audit review.

### UX notes

- UX-DR5 (Bills & Payments tab), UX-DR11 (no native dialogs — approval feedback is the updated chip + a non-blocking toast; conflicts inline). Sheet states: Approve button (approvable) / "✓ Scheduled for next payment batch — <date>" / "✓ Payment already made" (prototype line 865).
- Status chip labels: "Payment Scheduled" for `payment_scheduled`, "Paid" for `paid` — distinct from 3.3's `pending_submission` ("Pending Submission"); UI owns labels over snake_case enums.
- Design-token ruling: prototype LIGHT palette canonical; status colors from Epic 1 tokens.

### Testing requirements (this story's definition of done)

- Unit tests on guard matrix (status × version), batch idempotency, batch selectivity, and the sole-writer/sole-`paid`-setter assertions.
- E2E: `e2e/stories/3-4-payment-approval-batch.spec.ts` tagged `@story:3-4 @epic:3`, one `@smoke` happy path covering approve → batch → paid with figure refresh. The e2e harness needs a deterministic way to trigger the batch — expose the command through a test-profile hook or run it via the fixture; never wait on wall-clock cadence. Story cannot move to `review`/`done` until it passes (AD-15).

### Project Structure Notes

- Code lands in `lineworker/server/services/financials/` (approval + batch commands), `services/worklist/` (delegating approval command), `server/api/` (routes), `web/src/features/claim-detail/` (sheet buttons/states), config module (batch cadence knob in the single pydantic-settings surface from 1.1).
- The in-process job registration point established here is reused by Epic 6's embedding refresh — keep it a small generic "scheduled jobs" hook in the api process, not a payments-specific one-off.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 3.4]
- AD-4 status-guarded lifecycle + batch example, AD-12 worklist→financials delegation: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- Payment transition contract + Excel row-29/64 resolution: [Source: ARCHITECTURE-SPINE.md#Consistency Conventions (Enums & statuses)]; [Source: docs/Architecture-LINEWORKER.md#4.2 Key data rules]
- Scheduler mechanism deferred; jobs in-process in `api`: [Source: ARCHITECTURE-SPINE.md#Deferred / #Structural Seed]
- Behavioral reference: `approvePayment` (818–822), `approveScheduleWeek` (846–847), `nextBatchDate` Tue/Fri (808–817), sheet states (855–866), batch note (1451): [Source: docs/Workers_Comp_Prototype.html]
- FR-H-7: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
