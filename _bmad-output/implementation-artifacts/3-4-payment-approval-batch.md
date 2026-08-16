---
baseline_commit: 8f61ad589b5c0385d29b3fd1475b6181ee1471c2
---

# Story 3.4: Payment Approval & Batch

Status: review

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

- [x] Task 1: Financials approval command (AC: 1)
  - [x] `services/financials.approve_schedule_week(claim_id, week_no, expected_version, caller_ctx)`: CAS on `expected_version` AND status-guard on expected current status (`pending_approval` or `due_this_week`/`upcoming` per the approvable set — document the chosen set; the AC's canonical case is `pending_approval`) → sets `payment_scheduled`, increments version, emits audit event in the same transaction (AD-4)
  - [x] Companion `approve_bill_payment` / `approve_expense_payment` commands: `under_review` → `payment_scheduled` (Excel rows 64–66 write-back; prototype `approvePayment`, lines 818–822) — same CAS + status-guard + audit shape
  - [x] Guard violations (wrong status, stale version) → 409 problem+json carrying the fresh row; no force-write, no read-modify-write
  - [x] `timeline_event` emitted by the owning command as a side effect (AD-12) so approvals appear in the Overview timeline
- [x] Task 2: Worklist approval path (AC: 1)
  - [x] `services/worklist` approval command (the surface 3.5's actions and the UI call) delegates to the owning financials command — it never writes `payment_schedule_week`/`bill`/`expense` itself (AD-12); role gates capability: handler role required (AD-7)
  - [x] API route(s) exposing approval; SPA wires the "✓ Approve Payment" button into the week/line-item detail sheets shipped by 3.3, with optimistic-free update (status is server-derived — invalidate and refetch per AD-9), 409 rendered inline
- [x] Task 3: Payment batch (AC: 2)
  - [x] `services/financials.run_payment_batch()` command: single UPDATE-where-status transition `payment_scheduled → paid` across schedule weeks, bills, and expenses; per-row audit events in the same transaction; returns a run summary (rows paid, claims touched)
  - [x] Status guard makes re-runs idempotent by construction: a re-run selects nothing new; running with zero eligible rows is a clean no-op. **Nothing else in the codebase ever sets `paid`** — enforce with a service-layer assertion/test, not convention (Excel row-29 vs row-64 conflict resolved per the spine)
  - [x] In-process scheduling inside the `api` process (mechanism deferred — a minimal asyncio periodic task is sufficient; do NOT add a worker container or pin APScheduler as an architectural choice): cadence from config (default Tue/Fri per prototype `nextBatchDate`, lines 808–817), and the command remains directly invocable (tests, e2e, admin trigger)
  - [x] `next_batch_date` registered derivation from the configured cadence; Bills summary shows "Approved payments are disbursed in the next scheduled batch run… Next batch: <date>" (server-computed, prototype line 1451)
- [x] Task 4: Post-batch refresh (AC: 3)
  - [x] Paid To Date / Installments Paid / Next Payment Due recompute via 3.3's registered derivations — no new computation sites (AD-10)
  - [x] SPA invalidates the claim-financials query key(s) after approval mutations; batch-driven changes surface on normal refetch — document that no push channel exists yet (acceptable; nothing in the ACs requires live push)
  - [x] Approved week sheet shows "✓ Scheduled for next payment batch — <date>"; paid rows show paid state (prototype line 865)
- [x] Task 5: Tests (AC: all)
  - [x] Unit: approval sets `payment_scheduled` + audit; wrong-status approval 409s; stale-version approval 409s; batch transitions only `payment_scheduled`; double-run idempotency (second run pays 0); `pending_approval`/`due_this_week`/`upcoming`/`paid` rows untouched by batch; derivations reflect post-batch state
  - [x] Ownership test: grep/architecture test asserting no module outside `services/financials` writes the three payment tables or the `paid` status (AD-12 regression floor)
  - [x] E2E `e2e/stories/3-4-payment-approval-batch.spec.ts` `@story:3-4 @epic:3`: `@smoke` happy path — handler opens a pending-approval week, approves → chip shows "Payment Scheduled" + batch note; trigger the batch (direct command invocation via the e2e harness/API hook) → chip shows "Paid", Paid To Date and Installments Paid increase, audit trail asserted via timeline

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

claude-opus-5[1m] (Claude Opus 5, 1M context), via the `bmad-dev-story` workflow.

### Debug Log References

- **The batch needed an actor before it needed anything else, and the three obvious answers were all worse than a fourth.** AD-4 fixes `audit_event` with a non-nullable `actor_id`, and the batch is the first command in this build with no request behind it. Attributing thousands of disbursements to whichever handler the seed lists first is a false record of who acted; making `actor_id` nullable rewrites a schema the architecture names as fixed to accommodate one caller; leaving the batch unaudited fails AC 3 outright. So `UserRole` gained a `system` member and one `app_user` row. The cost is that a *login* vocabulary now carries a non-login member, and that cost is paid where it is dangerous: `POST /auth/login` is a `PUBLIC_PATHS` endpoint taking an integer, `app_user.id` is dense, and the new row is `scope_all` — the widest account in the system. `get_persona` refuses it and `tests/test_system_actor_migration.py` holds that refusal over HTTP with the id in hand, because the picker omitting the row is tidiness and this is the control.
- **`ALTER TYPE … ADD VALUE` and `data/env.py` are incompatible by default, and the fix has a price worth writing down.** The whole upgrade run is wrapped in one transaction, and PostgreSQL leaves a newly-added enum value unusable until its transaction commits — so the INSERT two lines later fails with "unsafe use of new value of enum type". `op.get_context().autocommit_block()` is Alembic's mechanism for exactly this, and it commits everything the run has done so far. Both statements are therefore guarded with existence checks, and the `downgrade` is a documented no-op rather than a `delete()`: the row is referenced by every audit event the batch has written, so deleting it either fails on the constraint (on a database where the batch ran) or is made to succeed by deleting audit rows, which is destroying the record of real disbursements to reverse a schema change.
- **The story's invariant is two sentences that need different enforcement, and only one of them is exactly true.** "Approval never marks `paid`" is absolute and structural: no approval command names `paid` as a destination, and the grep test says so. "Only the batch ever marks `paid`" is exactly true of `bill` and `expense` — nothing else assigns a `LineItemStatus.paid` anywhere in the tree — and needs one qualification for schedule weeks, because `schedule.py::_status` **projects** `paid` for a week the calendar has passed. That is the prototype's rule and the reason the seeded portfolio has any disbursement history at all. What survives without qualification is the half the Excel conflict is actually about: an *approved* week is paid only by the batch, because `plan_materialization` never touches a row in `DECIDED_STATUSES`. That is asserted directly over the whole status cross-product rather than inferred from where a literal appears, and the residual is in `deferred-work.md` with the two honest fixes named.
- **`db.rollback()` expires the row, and reading `row.id` afterwards is a 500 on the one path that exists to answer 409.** Found by `test_a_stale_version_is_refused_with_the_fresh_payload` against a real database: implicit IO on an `AsyncSession` outside an awaited call raises `MissingGreenlet`, so the conflict path — which rolls back and then re-reads — failed while constructing the exception it was about to raise. Two integers are now held in locals before the write. The general shape is worth remembering for every later AD-4 command: after a rollback, nothing loaded before it can be read.
- **`_conflict` originally re-read with `db.get(table, row_id)`, and the scoped-repository guard caught it.** `Session.get` goes to the identity map and the primary key, so it re-reads *without* the employer predicate — and `test_no_service_function_accepts_a_caller_context_it_never_reads` fired on a function holding a `CallerContext` it did not use. Nothing was reachable that should not have been (the row had been resolved in scope moments earlier), but a signature that reads as scoping applied where none is is how the next unscoped read gets written. The commands now close over their own scoped repository call and hand it in.
- **The financials query key is nested *under* the case file's, and TanStack matches by prefix — so invalidating the case file after an approval refetched and reverted the payload the mutation had just installed.** Caught by the component test asserting the sheet re-renders from the response. Story 3.3 nested it deliberately, so that an edit invalidating the case file could reach the Bills tab; that is right for an edit and wrong for a mutation whose own response *is* the authoritative payload for that key. `exact: true` closes it, and the comment says why the nesting is still correct.
- **The e2e's `weekRow` used `filter({has})` on an attribute that sits on the row itself.** `has` matches descendants, so the locator selected nothing and spent thirty seconds saying so. `.and()` is the right combinator. Recorded because the failure mode — a timeout rather than a "not found" — reads as a slow app rather than a wrong selector.
- **A treatment claim is not the same thing as a claim with something to approve, and two tests were written on the assumption that it is.** A claim old enough comes back with every week `paid`, because the projection pays elapsed weeks. Both the approval tests and the batch's figure test now *search* the caseload for a claim with an approvable row rather than indexing into a stage — and the batch's test asserts rather than skips, because a skipped acceptance criterion is an uncovered one.

### Completion Notes List

- **AC 1 — the approval is one command with two guards, and the second guard is not redundant with the first.** `transition_payment_row_cas` compare-and-swaps on `expected_version` **and** constrains the row's current status to the approvable set, in one statement. They fail on different histories: approving a week somebody else just approved fails the version check; approving a week whose sheet was left open across a batch run — same version, now `paid` — fails the status check. Without the second, the console would emit a second approval audit event for a payment that had already gone out. AD-4 names this rung in as many words for lifecycle updates, and this story is its reference implementation.
- **AC 1 — the approvable sets are the prototype's, and they differ per table on purpose.** A schedule week is approvable from `pending_approval`, `due_this_week` or `upcoming` (`reviewScheduleWeek`'s `canApprove = status !== "Paid" && status !== "PaymentScheduled"`); a bill or expense only from `under_review` (`reviewLineItem`'s `canApprove = it.status === "UnderReview"`). The AC's canonical case is `pending_approval`; the other two are included because a handler *can* approve next week's payment early and refusing that would be a rule the prototype does not have. Both sets are published on the payload as `approvable`, so the ✓ button and the command's refusal are one answer rather than two that happen to agree — and a `pending_submission` bill gets a sentence saying the provider has not filed it, where the prototype falls through to "✓ Payment already made".
- **AC 1 — AD-12's delegation is the shape the architecture names, and it is asserted structurally.** `services/worklist/approvals.py` gates capability, resolves the claim and calls the owning financials command; it contains no SQL against the three payment tables and `tests/test_payment_ownership.py` proves it, along with the wider claim that nothing outside `services/financials` writes them at all. That test is the AD-12 regression floor the story asks for — the approval surface is precisely where a second writer would appear, because it is the one place outside the package with a reason to want one.
- **AC 2 — idempotence is a property of the statement, not of any bookkeeping.** Each of the three UPDATEs carries `WHERE status = 'payment_scheduled'`, so a second run selects nothing because the first run's rows are no longer eligible. There is no run ledger to consult and therefore nothing that can be out of step with the rows themselves; an empty run writes no audit rows at all, which keeps "paid nothing" from burying the runs that did something. Held by a test that runs the batch twice and by the e2e, which does the same through the browser.
- **AC 2 — the scheduler is a general hook, deliberately smaller than the deferred decision.** `services/jobs.py` is a name, a predicate over the clock and something to await: no cron strings, no persistence, no retry. `tick(now)` takes its clock as an argument, so a week runs through the scheduler in microseconds and the tests assert exactly which days fired. The batch is registered on it and remains directly invocable, which is what keeps "APScheduler versus worker container" from reaching the command. Epic 6's embedding refresh registers a second job here; nothing in the module mentions payments.
- **AC 2 — the cadence is configuration and the tick is not the cadence.** `PAYMENT_BATCH_WEEKDAYS` defaults to `tue,fri` (the prototype's `nextBatchDate`) and is parsed from day *names*, because `[1,4]` is a value no operator can check at a glance — and an unparseable day stops the process naming what was typed rather than leaving a batch that silently never runs. `SCHEDULER_TICK_SECONDS` is a poll interval, and conflating the two is how a twice-weekly job ends up running every five minutes. The scheduler is off under `ENV=e2e`, which is a determinism property rather than a preference: a suite whose stack pays invoices on a wall clock cannot assert what a claim's figures are.
- **AC 3 — one audit event per row transition, not one per run.** The AC says "each transition", and a run that paid 40 rows across 12 claims leaves 40 rows in the log — because the question an auditor asks is "when was *this* payment made". Each names the claim's business id (matching the approval events, so the two halves of one payment's life are one filter apart) and carries both statuses and the row id, and nothing else: no amount, no claimant (AD-11).
- **AC 3 — the figures move because the rows moved, and no new computation site was added (AD-10).** `paid_to_date`, `installments_paid` and `next_payment_due` are Story 3.3's registered derivations, unchanged; the batch writes statuses and they answer differently. The one new derivation is `next_batch_date`, and it is the first entry in the registry whose parameter is *deployment* config rather than an AD-8 threshold — a disbursement calendar is not a business rule, and `batch_calendar.py` argues that where a reader will find it. Its one interesting rule is that the answer is never today: the sentence sits beside an approval a handler is about to make, and today's batch may already have run.
- **AD-9 — nothing is optimistic, and this is the clearest case in the console for that rule.** A payment status is a server decision the browser cannot evaluate: the command may refuse it on the row's current status, which may have moved since the payload was fetched. Flipping the chip to "Payment Scheduled" and rolling it back would show a handler money queued that was not. So the mutation installs the response and the sheet re-renders from it — which is also why `SheetTarget` holds an *identity* rather than a row, and why the sheet stays open after approving: what the handler needs to see is the result.
- **A 409 renders inline and names which conflict it was.** "Somebody else approved this" and "the batch paid it while your sheet was open" are different sentences with different things to do next, and the problem document carries `paymentStatus` so the sheet can tell them apart. The refusal is keyed to the row it was raised about and read back by comparison rather than cleared by an effect, so it cannot follow a handler onto the next week they open.
- **The UX note's toast is the one thing not shipped as specified**, and it is recorded rather than quietly dropped: there is no toast infrastructure in `web/`, and inventing a cross-cutting UI primitive inside a payments story is the wrong diff. What ships satisfies the property — non-blocking, where the handler is already looking, announced by `role="status"`, refusals inline at the control. In `deferred-work.md` with the story that should own it.
- **The e2e triggers the real command through a route that does not exist outside `ENV=e2e`.** `api/routers/admin.py` is included by `create_app` only under that environment, so in dev and prod the path is a 404 from the router rather than a 403 from a guard somebody could get wrong — and it calls `run_payment_batch` directly, so the spec tests the batch rather than a stub. It runs under the *requesting handler's* context, which is both more deterministic (one book) and a stronger assertion: a run under Kaya's context that paid Sarah's claims would show up as a failing count.
- **Two earlier specs were amended rather than left to fail** (AD-15: "specs are amended, never deleted"). `1-2`'s seed count now counts *personas* — `WHERE role <> 'system'` — and asserts the machine actor separately, so the predicate says what it means instead of reading "ten personas" while meaning "ten of eleven accounts". `BillsTab.test.tsx`'s "no approve button" test is replaced by six that exercise the button, which is the thing 3.3 said 3.4 would do.
- **Tests:** **1538 server** (+454 — the approval's guard matrix over HTTP and over the command, the status-guard case a version alone would have allowed, the role refusal answering identically for three kinds of claim, the audit and timeline pair, and the sole-`paid` walk over every approvable status on every table; the batch's three-table sweep, its untouched-status parametrisation over all seven other statuses, double-run idempotency, the empty no-op, per-row audit events, the system actor, and a scoped run that pays one book and not another; the scheduler's week-long fire pattern, its once-per-day rule, its failure containment and its duplicate-name refusal; 382 date cases over a year for `next_batch_date` plus the registry entry; migration 0029's enum-and-row pairing and the four assertions that keep the system actor out of the login path; the cadence parser's five cases and its two refusals; and the four ownership guards with their own smell tests). **294 vitest** (+6 — the approve control in all four states, the sheet re-rendering from the response rather than a held copy, the 409's inline message and refreshed figures, a refusal not following the handler to the next row, the unsubmitted-bill branch, and the batch note; plus `approvable` and `nextBatchDate` added to the derivation guard). **116 Playwright** (8 new, `@story:3-4 @epic:3`, one `@smoke` walking approve → batch → paid with the figures and the timeline).
- **Verified live.** e2e stack (`compose.e2e.yaml`, :8081) rebuilt with the new API and SPA: full suite **116/116**, the 17-test `@smoke` set and the 31-test `@epic:3` set all green against a freshly reset stack. `alembic check` reports no drift and a `downgrade 0028` → `upgrade head` round trip completes clean. `npm run generate:api` leaves `src/api/schema.d.ts` byte-identical on a second run. ruff + format + mypy clean across 169 files; eslint clean (8 pre-existing warnings, none new); tsc clean for `web/` and `e2e/`. No new runtime or dev dependency.

### File List

**New — server**

- `lineworker/server/data/versions/20260814_0029_system_actor.py`
- `lineworker/server/services/financials/approval.py`
- `lineworker/server/services/financials/batch.py`
- `lineworker/server/services/worklist/approvals.py`
- `lineworker/server/services/derivations/batch_calendar.py`
- `lineworker/server/services/jobs.py`
- `lineworker/server/api/routers/admin.py`
- `lineworker/server/tests/test_payment_approval.py`
- `lineworker/server/tests/test_payment_batch.py`
- `lineworker/server/tests/test_payment_ownership.py`
- `lineworker/server/tests/test_batch_calendar.py`
- `lineworker/server/tests/test_system_actor_migration.py`

**Modified — server**

- `lineworker/server/config.py` (the cadence, the tick, the scheduler switch, and the weekday parser)
- `lineworker/server/data/models/enums.py` (`UserRole.system`, `LOGIN_ROLES`, `SYSTEM_ROLES`, `SYSTEM_ACTOR_NAME`)
- `lineworker/server/data/repositories/identity.py` (the picker filter and `get_persona`'s refusal)
- `lineworker/server/data/repositories/claims.py` (three scoped single-row reads, the CAS transition, the batch's UPDATE, and the claim-ref lookup)
- `lineworker/server/services/financials/summary.py` (`version` and `approvable` on both views, `next_batch_date` on the summary, `batch_weekdays`)
- `lineworker/server/services/financials/__init__.py` (the package's seventh and eighth modules, and their exports)
- `lineworker/server/services/worklist/__init__.py` (the package's first command)
- `lineworker/server/services/derivations/__init__.py` (registration + the config-parameter note)
- `lineworker/server/services/claims/detail.py` (`batch_weekdays` threaded to the read model)
- `lineworker/server/api/routers/claims.py` (the approval route, its 409 schema, and the three response models' new fields)
- `lineworker/server/api/routers/__init__.py` (the admin router)
- `lineworker/server/api/app.py` (the job runner, the lifespan task, and the e2e-only router)
- `lineworker/server/tests/test_bills_tab.py` (`nextBatchDate` on the field list)
- `lineworker/server/tests/test_schema_seed.py` (the eleventh `app_user`)
- `lineworker/server/tests/test_personas.py` (the login-role complement and the unknown-role label)
- `lineworker/server/tests/test_config.py` (the cadence parser and the scheduler switch)

**New — web / e2e**

- `lineworker/e2e/stories/3-4-payment-approval-batch.spec.ts`

**Modified — web / e2e**

- `lineworker/web/src/api/claims.ts` (`useApprovePayment`, `conflictPaymentStatus`, the exact-invalidation fix)
- `lineworker/web/src/api/schema.d.ts` (regenerated)
- `lineworker/web/src/features/claim-detail/bills/LineItemDialog.tsx` (the approve control, the identity-keyed target, the inline refusal)
- `lineworker/web/src/features/claim-detail/bills/BillsTab.tsx` (the target is an identity; the payload is handed to the sheet)
- `lineworker/web/src/features/claim-detail/bills/FinancialSummaryCard.tsx` (the batch note)
- `lineworker/web/src/features/claim-detail/bills/BillsTab.test.tsx` (six approval tests replace 3.3's absence test)
- `lineworker/web/src/features/queue/noDerivation.test.ts` (`approvable`, `nextBatchDate`)
- `lineworker/web/src/features/shell/TopBar.tsx` (the two exhaustive role maps)
- `lineworker/web/src/test/api-mock.ts` (the new row fields, `CLAIM_FINANCIALS_APPROVED`, `APPROVAL_CONFLICT_PAID`, the approval route)
- `lineworker/e2e/stories/1-2-persisted-claim-portfolio-schema-seed.spec.ts` (personas counted as personas; the machine actor asserted separately)

**Modified — repo**

- `lineworker/deploy/.env.example` (the two batch knobs and the scheduler switch)
- `_bmad-output/implementation-artifacts/deferred-work.md` (five items)
- `_bmad-output/implementation-artifacts/3-4-payment-approval-batch.md` (this file), `_bmad-output/implementation-artifacts/sprint-status.yaml`

### Change Log

- 2026-08-14: Story 3.4 implemented end to end — the payment lifecycle, from a handler's ✓ to money marked disbursed. Three approval commands in `services/financials` move a schedule week, a bill or an expense to `payment_scheduled` under a compare-and-swap **and** a status guard (AD-4's extra rung for lifecycle updates), each emitting an audit event and a timeline row in the same transaction; `services/worklist` gets its first command, and it writes nothing — it gates the handler capability, resolves the claim and delegates, which is the arrangement AD-12 names by name and which `tests/test_payment_ownership.py` now holds structurally. `services/financials/batch.py` is the only place anything becomes `paid`: three status-guarded UPDATEs, one audit event per row moved, idempotent by construction rather than by a run ledger. It runs as a seeded **system actor** (a new `UserRole.system` and migration 0029) so the audit log answers "who did this" honestly, and on a small generic scheduled-jobs hook in the api process that Epic 6's embedding refresh will reuse. The figures recompute through Story 3.3's registered derivations with no new computation site, and `next_batch_date` joins the registry as the first entry parameterised by deployment config rather than an AD-8 threshold. The Excel's row-29-versus-row-64 conflict is resolved in code and in tests: approval never writes `paid`, nothing outside the batch assigns a line item's `paid` at all, and an approved week can only be paid by the batch. Full gate green: ruff + format + mypy clean, pytest 1538, vitest 294, Playwright 116/116 plus the 17-test `@smoke` and 31-test `@epic:3` sets against a rebuilt e2e stack. Status → review.
