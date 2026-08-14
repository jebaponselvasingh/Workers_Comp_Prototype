---
baseline_commit: 20365792560a83c5bf5a681cee939e9b6d20c4ab
---

# Story 3.3: Bills & Indemnity Payment Schedule

Status: done

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

- [x] Task 1: Migrations — `payment_schedule_week`, `bill`, `expense` (AC: 2, 3)
  - [x] `payment_schedule_week`: surrogate `id`, `claim_id` FK, `week_no int`, `period_start date`, `period_end date`, `amount_cents int`, `status` enum `paid|due_this_week|upcoming|pending_approval|payment_scheduled`, `version int` (mutable entity — CAS per conventions), unique `(claim_id, week_no)`. Define `payment_scheduled` in the enum now; no row carries it until Story 3.4's approval command
  - [x] `bill`: surrogate `id`, `claim_id` FK, `category` enum (`initial_treatment|surgery_facility|imaging|physical_therapy|follow_up|pharmacy`), `label`, `amount_cents int`, `status` enum `paid|under_review|pending_submission|payment_scheduled`, `version int`
  - [x] `expense`: same shape; `category` enum (`mileage_travel|dme|prosthetic_assistive|home_workstation_mod|misc`)
  - [x] Snake_case per the Excel canonical naming; money integer cents; app-role grants consistent with 1.2 (no derived user-writable columns)
- [x] Task 2: Schedule generation in `services/financials` (AC: 2)
  - [x] One generator (the same pure projection referenced by 3.2 — extend it, never fork it): weeks parsed from recovery window (`"N-M Weeks"` → M; `"…Year"` → 26; clamp 4–20 — prototype lines 742–745), start = DOI + waiting-period days (JDM `benefit_params` from 3.1), weekly amount from 3.1's `compute_benefit`
  - [x] Status assignment relative to the reference date: intake/investigation stage → all `pending_approval`; settled → `paid`; past weeks → `paid`; current week → `due_this_week`; future → `upcoming` (prototype lines 753–758). Reference date is a registered derivation (AD-10), not `Date.now()` scattered in code
  - [x] Materialization command (owner: `services/financials`) writes/refreshes `payment_schedule_week` rows for a claim — **status-preserving**: regeneration never clobbers `payment_scheduled`/`paid` rows written by approval/batch (the exact AD-12 clobbering scenario the spine names); audited (AD-4)
  - [x] Seed migration materializes schedules for the 100 seeded claims through this command path
- [x] Task 3: Bill & expense seed (AC: 3)
  - [x] Seed `bill` rows per claim mirroring the prototype's deterministic builder (lines 766–783): initial treatment always; surgery/facility when `surgery_required`; imaging always; PT bundle when the treatment plan includes therapy; follow-up visit; pharmacy when disabled — amounts and statuses precomputed deterministically (hash-variance is fine to reproduce; it's seed data, not runtime logic)
  - [x] Seed `expense` rows per the prototype builder (lines 784–800): mileage/travel; DME when past intake; prosthetic when surgical; home/workstation modification when permanent; misc
  - [x] Settled-stage claims seed everything `paid` (matches prototype), so expense totals correctly feed the settled-stage payout breakdown (readiness-review fix: `expense` was added to this story precisely for that consumer)
- [x] Task 4: Financial summary + derivations (AC: 1, 4)
  - [x] Registered derivations in `services/derivations` (one computer each, AD-10): `paid_to_date_cents` (indemnity paid-so-far + paid bills + paid expenses, honoring the seeded static paid fields when present — prototype's effective-breakdown logic, lines 1428–1436), `total_claim_projected_cents` (= paid to date + reserve), `installments_paid`, `next_payment_due` (first `due_this_week` else first `upcoming` week), `bills_on_file`
  - [x] One claim-financials read model endpoint returning summary + metrics + schedule + bills + expenses + the 3.2 reserve verdict (same server value, same query key — AC 1/AC 4)
  - [x] Cost bar breakdown (Indemnity/Medical/Expenses % of paid) server-computed
- [x] Task 5: Bills & Payments tab UI (AC: 1, 2, 3, 4)
  - [x] Replace Epic 2's empty-state Bills tab shell with: financial summary card (4 paycards + cost bar + reserve-check ratbox), week-by-week schedule table (Wk / Period / Amount / Status chips), medical-bills card ("n on file, $x of $y paid"), expenses card with empty state ("No expenses filed for this claim") (prototype 1419–1459)
  - [x] Status chips use UI-owned labels over snake_case enums ("Due This Week", "Pending Approval", "Under Review", "Pending Submission", "Payment Scheduled", "Paid"); row/line-item click opens the read-only detail sheet (approval buttons inside arrive with 3.4)
  - [x] Treatment-stage Overview paid-vs-reserve card gets the "View full Bills & Payments →" jump-link switching to this tab; both surfaces read the same TanStack Query key so figures cannot diverge (AD-9/AD-10)
  - [x] Loading / error / empty states throughout (NFR-3)
- [x] Task 6: Tests (AC: all)
  - [x] Unit: weeks parsing (ranges, "Greater than 1 Year", clamp bounds 4 and 20), status assignment per stage and reference date, status-preserving regeneration, derivations (paid-to-date with and without static paid fields, next-due selection)
  - [x] Property (Hypothesis): ∀ claims, `sum(week amounts) == weekly × weeks`; regeneration is idempotent on unchanged inputs
  - [x] Migration: `alembic upgrade head` clean on fresh DB; seed materializes schedules/bills/expenses for all 100 claims
  - [x] E2E `e2e/stories/3-3-bills-indemnity-payment-schedule.spec.ts` `@story:3-3 @epic:3`: `@smoke` happy path — handler opens a treatment claim, Bills tab shows summary + schedule + bills; Overview jump-link lands on the tab with matching Paid To Date figure asserted on both surfaces

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

claude-opus-5[1m] (Claude Opus 5, 1M context), via the `bmad-dev-story` workflow.

### Debug Log References

- **The story asks for the schedule to be persisted *and* for two surfaces never to disagree, and those two together decide the whole design.** Three of the five week statuses are the calendar's answer, so persisted rows go stale on a clock rather than on an edit. If only the Bills tab refreshed them, the case file's reserve check would be computed from rows nobody had touched, and AC 4's "identical figures" would hold on most requests and fail on the one crossing a week boundary — which is the failure mode a test never catches and a demo does. So `materialize_schedule` is called ahead of *both* reads, which makes two GETs able to write. That is affordable only because the command is a genuine no-op on unchanged inputs, so that is asserted as a property rather than assumed, and the cost is recorded in `deferred-work.md` as the shape question it opens.
- **The prototype's line-item statuses are undefined on 26 of 860 rows, and the bug is one operator.** `hashStr` ends `>>> 0` — the author stating the hash is 32-bit unsigned — and then `buildBills` reads it back with `>>`, which is *signed*. For the 56 of 200 claim-id hashes above 2^31 the shift is negative, JavaScript's `%` keeps the sign of its dividend, and `pool[-1]` is `undefined`; `billStatusLabel`'s `|| "Pending"` turns that into a status appearing in no vocabulary the prototype declares. The extractor uses the unsigned shift throughout, which is the intent rather than the transliteration. Verified rather than argued: all 860 rows were diffed against a Node run of the prototype's own two builders, **every amount matches**, and the status differs on exactly the 26 rows where the prototype had no answer.
- **Migration 0028 imports from `services/`, which no other migration does, and the house rule that forbids it does not apply.** 0013 and 0014 write their vocabularies out as literals under an explicit rule — a migration is a frozen record and must not read a live constant. That rule protects *declarations*: `BODY_KEYS` decides an enum type's member order, and freezing a copy is the only way to keep recording what the schema became. 0028 declares nothing; it seeds derived data whose defining property is that exactly one function computes it (AD-2), and the story forbids forking the projection in as many words. A frozen copy there would not preserve history, it would create the second generator. The cost — a generator change silently altering what a historical migration does — is converted into a test failure by `test_the_seeded_schedule_is_the_live_generators_answer`.
- **The AD-10 phase guard fired on five modules, every one a false positive, and the honest fix was to make the guard stricter rather than to exempt them.** `PHASE_RULE_READ` matched `weeks` followed by `)` or `]` — which is to say, any code iterating a list of weeks, and this is the story that makes a claim's schedule exactly that. Adding five `PHASE_HOME` entries would have kept the tick green by switching the guard off across most of `services/financials`. Narrowing the follow-set to regex metacharacters then revealed something worse: **neither existing `PHASE_HOME` entry had ever matched on its own merits.** `schedule.py` was caught by the prose "`Math.max(4, Math.min(weeks, 20))`" in a docstring, not by `SCHEDULE_WEEKS` — the actual second `RecoveryWindow`-keyed table that Story 3.2 argued for at length. A guard that would have missed the very thing its exemption describes is not a guard, so a third alternative now matches a `RecoveryWindow`-keyed mapping to a number, both homes trip it for their stated reasons, and `data/models/enums.py` was retired from `PHASE_HOME` because the noise it was admitted for no longer fires. Two new tests hold both directions: that ordinary week iteration is *not* an offence, and that a second duration table is.
- **`paid` is produced by the calendar as well as by a decision, and that ambiguity is the one real subtlety in the materializer.** The partition it needs is "may a regeneration overwrite this?", so `paid` sits with `payment_scheduled` on the frozen side even though the projection also emits it. The consequence is stated rather than hidden: a correction that moves a schedule *forward* leaves already-paid weeks paid although the new projection puts them in the future. That is the safe direction — the alternative un-pays weeks — and a claim whose date of injury was wrong enough to matter needs a command that says so, not a side effect of somebody opening a tab.
- **A decided week is frozen entirely, not merely status-preserved**, which is stricter than the story's wording and is the right strictness. The amount on a paid week is what was actually disbursed, so refreshing it from a newly computed weekly figure — after a comp-rate override, say — would quietly restate history. It is also why `installments_paid` counts rows instead of dividing a total by the weekly figure: once two weeks can carry different amounts, the division is wrong.
- **The story's own premise for creating `expense` here does not survive checking.** The readiness review moved it into 3.3 because it "feeds the settled-stage payout breakdown rendered by Story 2.2", and the Dev Notes ask for that surface to be verified after seeding rather than assumed. Verified: it was never empty. `SettledOverview.tsx` renders `claim.paid_expense`, and all 62 seeded settled claims carry a non-zero one from the Story 1.2 seed. Creating the table here is still right — the Bills tab's own Expenses card is AC 3, and open claims need the effective figure — but the docstrings that repeated the review's sentence now say what is actually true, because a later story reading it would go looking for a dependency that is not there.
- **`PaidColumns` and `ReserveClaim` had to be joined into one protocol, and mypy is what noticed.** The read model reads a claim as two different things — the thing whose schedule is projected and judged, and the snapshot the paid figure falls back *from* — and passing a `ReserveClaim` to `PaidToDateDerivation.of` type-checked as an error rather than failing at runtime on a missing attribute. `FinancialsClaim` extends both rather than restating their members, which is `ReserveClaim`'s own arrangement over `ScheduleClaim`.
- **A `CostSplitResponse` already existed and I published a second one**, which openapi-typescript resolved by inventing `api__routers__claims__CostSplitResponse__2` and broke the SPA's typecheck. Deduplicated to the existing model with its docstring widened: the Bills bar is drawn over a different triple (the effective breakdown rather than the paid columns) but the shape and the sums-to-100 rule are one contract, and two identical models would have published two names for it to the generated client.

### Completion Notes List

- **AC 2 — the generator was not forked, and the seam Story 3.2 built is what made that cheap.** 3.2 landed `project_payments` complete — weeks, dates, statuses, totals — precisely so 3.3 would persist *through* it (AD-2). This story adds `services/financials/materialize.py`, which is the only writer of `payment_schedule_week` (AD-12) and contains no schedule arithmetic at all: it takes a projection and the rows that exist and decides what to do about the difference. The planning half is pure and synchronous, which is what lets a Hypothesis property range over schedules and stored states without a database *and* what lets migration 0028 apply the same decisions with Alembic's own connection.
- **AC 2 — "status-preserving" is held by a property, not by an example.** The parametrized test proves one decided week in one position survives; `test_no_plan_ever_touches_a_decided_week` ranges over every arrangement of five statuses across six weeks and asserts no decided week appears in `updates` or `deletes`. That is the difference between "the case I thought of works" and "there is no arrangement that slips one through", and this is the invariant the architecture spine names by name.
- **AC 1 — every summary figure is a registered derivation, and the effective breakdown is the one with a real rule in it.** `paid_to_date` ports the prototype's fallback exactly, including its all-or-nothing shape: the source is decided once **on the sum**, not per component. A claim whose columns say `(0, 45000, 0)` reports medical $450 and nothing else, rather than mixing a real medical column with a live indemnity figure — two sources in one total with no way to say which a number came from. That per-field fallback is what a reader is most likely to "fix" it into, so it has a test naming the choice.
- **AC 1 — `total_paid` was not widened, and the two now sit beside each other with their difference written down.** `claim_money.total_paid` answers "what do the paid columns say", which is the right answer on the investigation card and the settled breakdown; `paid_to_date` answers "what has actually been disbursed". They are different questions, so the new derivation *calls* the old one for its static half rather than adding the three columns again, and the cost-bar arithmetic moved into a shared `split_of` so the rounding convention that makes three shares total exactly 100 lives in one place whichever triple it is given.
- **AC 4 — "identical figures" is structural, and it is *not* structural in the way the story sketches.** The story asks for one query key; `reserveCheck` shipped on the case file in 3.2 with tests pinning it there, and the Bills payload is far too heavy to put on the console's most-fetched response. So the two surfaces are two query keys and cannot disagree anyway, because both come from one assembler over one set of rows: `services/claims/detail.py` takes its verdict out of the same `ClaimFinancials` object the endpoint returns. `test_the_reserve_verdict_is_identical_on_both_surfaces` compares the two payloads field for field at all four stages, and the `@smoke` e2e walks the human path — read the card, click through, assert the same numbers.
- **AC 4's e2e reads the Overview card *before* the jump**, so what it compares is two renderings a handler sees in sequence rather than two reads of one payload. The pair it pins is the one both surfaces genuinely show: `disbursedIndemnityCents` of `scheduledIndemnityCents`, rendered as "Indemnity paid" on the card and as the schedule's sub-heading on the tab, plus the verdict label. The story's task text says "matching Paid To Date figure"; the card is titled "Paid to date vs. reserve" and shows that figure's components, and adding a whole financial fetch to the Overview to render one more row would be disproportionate. Recorded as a reading rather than done silently.
- **AC 3 — the prototype's label bug is refused, and the refusal is in the enum rather than in a comment.** `BILL_STATUS_LABEL` maps `PendingSubmission` to the string "Payment Scheduled", so a bill nobody has filed reads as money already queued — opposite facts about what a claim is about to cost. They are two members with two labels here, Story 3.4's approval is the transition between them, and a component test asserts a `pending_submission` row does not render the other words.
- **Story 3.2's seam closed exactly where it said it would.** `unpaid_medical_cents` was one function body returning `None`, with a test asserting that state so 3.3's seed would *fail* it — "the reminder rather than a surprise". It did. The body is now the scoped bill query 3.2's docstring predicted, the two tests were re-pointed to assert the state that replaced it, and no line of `classify_reserve` changed.
- **The portfolio's verdicts are now complete, which is the story's value in one number.** Before: 4 `light`, 15 `indeterminate`, 26 `closed_final` in Kaya's book — honest, and 15 of 19 open claims reading "Awaiting Bill Data". After, across all 100: **15 `light`, 6 `adequate`, 17 `heavy`, 62 `closed_final`**, no claim withheld. And **0 of 100 claims show "$0 paid to date"**, which is the effective breakdown doing its job — the figure the prototype's own comment says the static columns get wrong.
- **The Bills tab fetches its own payload, and the query key says why.** The schedule, two line-item lists and their totals are far more than the Overview needs, on a case file re-read after every command and embedded in every 409 body. The key is nested under the claim's own segment so an edit that invalidates the case file can reach it, and the panel is mounted only while its tab is selected, so the cost is paid by the tab that shows it.
- **`VERDICT_ACCENT` moved out of `TreatmentOverview` because a second card now renders the verdict.** "The UI owns the colour" has to mean *one* place, or the two surfaces end up disagreeing about whether a light reserve is red or amber — which reads as two judgements of one claim rather than one judgement shown twice. The light-is-the-error pairing is argued in the new module, because it is exactly the kind of thing a later refactor "corrects".
- **A real discrepancy is recorded rather than papered over.** The prototype invented its line-item amounts from a hash and inherited its `paid_*` columns from its dataset, and never reconciled them: on settled claim WC-20051 the Overview reads "Expenses paid $122" while the Bills tab reads "5 on file ($636 of $636 paid)", a median 1.49× across the 62 settled claims. Faithfully ported rather than introduced — the prototype has the same divergence between its own two cards — but demo-visible, so it is in `deferred-work.md` with the cheapest honest fix, and the e2e test asserts the state that exists instead of an agreement that does not.
- **Tests:** **1084 server** (68 new — the planner's empty, unchanged, clobbering, shortening, lengthening and repricing cases, three Hypothesis properties over the whole input space, the command's no-op/audit/decided-week-survival trio against a real database; the five derivations with the fallback decided on the sum, the paid/unpaid partition, `installments_paid` refusing to count approved weeks, `next_payment_due`'s two exclusions and its agreement with the projection's own answer; the migration's four enum tuples against their enums, the shared status type, the seeded amounts re-derived independently from the prototype's HTML, whole-dollar amounts, settled claims fully paid, the unique constraint, three foreign keys and the CRUD grants; and the endpoint's field list, its internal identities at four stages, both fallback branches, the schedule's density, AD-7's matching 404 and the verdict's identity with the case file's). **285 vitest** (18 new — the three NFR-3 states, the four paycards, the metrics row, the cost bar's server-computed widths and its null branch, the contradictory-payload test, every week and line item rendered with its own chip, the pending-submission label, the read-only sheet and its Escape close; plus Story 3.3's fourteen derived fields and three smells added to the derivation guard, and the AC-4 identity test on the pane). **109 Playwright** (9 new, `@story:3-3 @epic:3`, one `@smoke`).
- **Verified live.** e2e stack (`compose.e2e.yaml`, :8081) rebuilt with the new API and SPA: full suite **109/109**, the 16-test `@smoke` set and the 24-test `@epic:3` set all green against a freshly reset stack. `alembic upgrade head` runs clean on a dropped schema, `alembic check` reports no drift, and a `downgrade 0025` → `upgrade head` round trip completes clean and re-seeds all 728 schedule weeks, 520 bills and 340 expenses. `npm run generate:api` leaves `src/api/schema.d.ts` byte-identical on a second run. ruff + format + mypy clean; eslint clean (8 pre-existing warnings, none new); tsc clean for `web/` and `e2e/`. No new runtime or dev dependency.

### File List

**New — server**

- `lineworker/server/data/versions/20260814_0026_financial_tables.py`
- `lineworker/server/data/versions/20260814_0027_seed_line_items.py`
- `lineworker/server/data/versions/20260814_0028_materialize_schedules.py`
- `lineworker/server/data/seed/extract_prototype_line_items.py`
- `lineworker/server/data/seed/bills.json`
- `lineworker/server/data/seed/expenses.json`
- `lineworker/server/services/financials/materialize.py`
- `lineworker/server/services/financials/summary.py`
- `lineworker/server/services/derivations/claim_financials.py`
- `lineworker/server/tests/test_schedule_materialization.py`
- `lineworker/server/tests/test_claim_financials.py`
- `lineworker/server/tests/test_financial_tables_migration.py`
- `lineworker/server/tests/test_bills_tab.py`

**Modified — server**

- `lineworker/server/data/models/enums.py` (`LineItemStatus`, `BillCategory`, `ExpenseCategory`)
- `lineworker/server/data/models/core.py` (`PaymentScheduleWeek`, `Bill`, `Expense`)
- `lineworker/server/data/models/__init__.py` (exports)
- `lineworker/server/data/repositories/claims.py` (`select_payment_schedule`, `select_bills`, `select_expenses`)
- `lineworker/server/services/financials/__init__.py` (the package's fifth and sixth modules, and their exports)
- `lineworker/server/services/financials/reserve.py` (`unpaid_medical_cents` filled in, `indemnity_terms`, `reserve_check_from_rows`, and the schedule refresh)
- `lineworker/server/services/derivations/claim_money.py` (`split_of` extracted; `TotalPaidDerivation`'s docstring, and what it is *not*)
- `lineworker/server/services/derivations/__init__.py` (registration + the naming note)
- `lineworker/server/services/claims/detail.py` (`claim_financial_detail`, and the case file's refreshed reserve check)
- `lineworker/server/api/routers/claims.py` (seven response models, `GET /claims/{id}/financials`, and the deduplicated `CostSplitResponse`)
- `lineworker/server/tests/test_reserve_block.py` (the two seam tests, re-pointed; the field list; two tests for the paid-medical figure)
- `lineworker/server/tests/test_reserve_check.py` (the `check()` helper and two call sites, for the classifier's new carried term)
- `lineworker/server/tests/test_case_file_derivations.py` (the phase guard narrowed and then strengthened; `PHASE_HOME`'s dead entry retired; two new tests)

**New — web / e2e**

- `lineworker/web/src/features/claim-detail/bills/BillsTab.tsx`
- `lineworker/web/src/features/claim-detail/bills/FinancialSummaryCard.tsx`
- `lineworker/web/src/features/claim-detail/bills/PaymentScheduleCard.tsx`
- `lineworker/web/src/features/claim-detail/bills/LineItemsCard.tsx`
- `lineworker/web/src/features/claim-detail/bills/LineItemDialog.tsx`
- `lineworker/web/src/features/claim-detail/bills/statusTone.ts`
- `lineworker/web/src/features/claim-detail/bills/BillsTab.test.tsx`
- `lineworker/web/src/features/claim-detail/reserveAccent.ts`
- `lineworker/e2e/stories/3-3-bills-indemnity-payment-schedule.spec.ts`

**Modified — web / e2e**

- `lineworker/web/src/api/claims.ts` (the nine Bills types and `useClaimFinancials`)
- `lineworker/web/src/api/queryKeys.ts` (`claims.financials`)
- `lineworker/web/src/api/schema.d.ts` (regenerated)
- `lineworker/web/src/features/claim-detail/labels.ts` (four label maps)
- `lineworker/web/src/features/claim-detail/DetailTabs.tsx` (the Bills seam removed, the panel prop added)
- `lineworker/web/src/features/claim-detail/ClaimDetailPane.tsx` (the Bills panel)
- `lineworker/web/src/features/claim-detail/overview/TreatmentOverview.tsx` (`VERDICT_ACCENT` extracted; the jump-link's docstring; the corrected "Medical paid" row)
- `lineworker/web/src/features/claim-detail/ReserveCheckCard.test.tsx` (the new field on five fixtures)
- `lineworker/web/src/features/claim-detail/ClaimDetailPane.test.tsx` (the seam test re-pointed; the AC-4 identity test)
- `lineworker/web/src/features/queue/noDerivation.test.ts` (Story 3.3's fourteen derived fields, three smells, two scan assertions)
- `lineworker/web/src/test/api-mock.ts` (`CLAIM_FINANCIALS`, `CLAIM_FINANCIALS_UNPAID`, the `claimFinancials` route)
- `lineworker/e2e/fixtures/seed.ts` (the 3.3 oracles: bills, expenses, totals, `expectedPaidToDate`, `expectedWeekCount`, two label maps; and `expectedReserveCheck`'s medical term)
- `lineworker/e2e/stories/3-2-reserve-adequacy-check.spec.ts` (the withholding assertions, re-pointed to complete verdicts)
- `lineworker/e2e/stories/2-2-case-header-stage-adaptive-overview.spec.ts` (the Bills seam, re-pointed to the built tab)

**Modified — repo**

- `_bmad-output/implementation-artifacts/deferred-work.md` (six items)
- `_bmad-output/implementation-artifacts/3-3-bills-indemnity-payment-schedule.md` (this file), `_bmad-output/implementation-artifacts/sprint-status.yaml`

### Change Log

- 2026-08-14: Addressed code review findings — **6 findings, 5 fixed, 1 mitigated and recorded, 0 dismissed.** All six were real; I verified each against the seed and the running stack before acting.

  **The one a handler would have seen.** The treatment card's "Medical paid" row read `overview.paidMedicalCents` — `claim.paid_medical`, which is **0 on all 38 open seeded claims** — sitting directly above the indemnity row that Story 3.2's review had corrected for exactly this reason, and directly above the jump-link to a tab showing that same claim's paid bills as a real figure. So **28 of 28** of Kaya's treatment claims rendered "Medical paid $0.00" beside a live indemnity figure and one click from "$6,144 paid". It is the identical defect in the identical place one row up, so it takes the identical shape: `ReserveCheck` publishes `disbursed_medical_cents`, the paid half of the same bill list whose unpaid half is the exposure term, summed from one read. Now **0 of 28**. Three tests hold it — the two halves partition the bill list at every stage, an open claim's figure is non-zero while the column is zero, and the component renders the block's field.

  **`reserve_check_from_rows` now takes the bill rows rather than a pre-summed figure**, which is what makes "one read" true rather than convenient: the paid and unpaid halves cannot come from two queries of one table taken at different moments.

  **The audit log said a supervisor rewrote a payment schedule.** The refresh runs on a read path under the reading caller's context, and `GET /claims/{id}` has no role gate — so a supervisor or analyst, neither of whom can write anything through any command here, could produce an `AuditEvent` naming them as actor for a change no human made. AD-4's log is meant to answer "who changed what". The `after` diff now carries `"trigger": "calendar_refresh"`, so the row says what it is; the actor stays because `actor_id` is not nullable and "under whose request" is a real question. A proper fix needs a system actor or a background refresher, and both are recorded.

  **A concurrent read could 500.** The read-path write had no conflict handling, and the two financial query keys share a prefix — one `invalidateQueries` fires both refetches at once. The loser of a concurrent insert violates the unique constraint; the loser of an update-versus-delete raises `StaleDataError`. Both were an unhandled 500 on a GET. Now caught, rolled back and re-read — no retry needed, because the winner computed the same plan from the same inputs, which is exactly what the idempotence property means.

  **`select_payment_schedule` was dead code that read as an AD-7 enforcement point.** I had written and documented it and then had `materialize._existing` issue its own unscoped query. The service now goes through the repository. The unscoped read was not reachable-but-wrong — the claim had already been resolved through `select_claim_detail` — but a repository function documented as the entry point while the only caller bypasses it is how the next bypass gets justified.

  **A docstring pointed at a test file that does not exist.** `tests/test_line_item_seed_file.py` was never written; the assertions it describes live in `tests/test_financial_tables_migration.py`, and the docstring now names them.

  **One finding mitigated rather than fixed, and it is the interesting one.** On a settled claim the paid columns answer the summary while the cards below show their own rows, and the seeded figures were never reconciled — so the cost-bar legend can read "Medical $475" about 40px above a bills card reading "$6,144 paid", a 13× gap, with indemnity inverted on other claims. My original deferred note described a smaller, cross-tab, expenses-only version of this; the reviewer measured the real thing. Reconciling the data is a decision about what the demo dataset *means* and belongs to nobody in this story, so what shipped is honesty instead: the summary renders a provenance line whenever the columns answered, naming them as the carrier's ledger and the cards below as the claim's own rows. Two tests pin that it appears in that case and only in that case. The corrected measurements are in `deferred-work.md`.

  Gate re-run green: ruff + format + mypy clean, **pytest 1086**, **vitest 288**, **Playwright 109/109** against a rebuilt e2e stack.

- 2026-08-14: Story 3.3 implemented end to end — the bills, the expenses and the week-by-week indemnity schedule, and the read model that makes them one surface. Three tables created and seeded (`payment_schedule_week`, `bill`, `expense` — 728 weeks, 520 bills, 340 expenses across all 100 claims), with the line items ported from the prototype's own deterministic builders and verified amount-for-amount against a JavaScript run of them. `services/financials/materialize.py` is the single writer: it persists *through* Story 3.2's generator rather than beside it (AD-2), and refuses to overwrite a status a human decision put there (AD-12) — the exact clobbering scenario the spine names, held by a Hypothesis property over every arrangement of statuses. Five registered derivations (AD-10) answer the summary's figures, including the effective-breakdown fallback that stops an open claim reading "$0 paid to date" while its schedule shows elapsed weeks. One endpoint carries the whole tab, and the case file takes its reserve verdict out of the same assembler — which is what makes AC 4's "identical figures" a property of the code rather than of two components kept in step. Story 3.2's deliberate seam closed: `unpaid_medical_cents` is a real sum, and the open portfolio went from 15 of 19 claims reading "Awaiting Bill Data" to every claim banded (15 light, 6 adequate, 17 heavy, 62 closed_final). Full gate green: ruff + format + mypy clean, pytest 1084, vitest 285, Playwright 109/109 plus the 16-test `@smoke` and 24-test `@epic:3` sets against a rebuilt e2e stack. Status → review.
