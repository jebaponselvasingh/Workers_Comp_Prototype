# Story 3.5: Auto-Generated Action Checklist & Approval

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want a claim-specific action checklist with deep links and one-click approval,
so that nothing statutory or operational slips.

## Acceptance Criteria

1. **Given** a claim, **when** the Upcoming Actions block renders, **then** `services/worklist` generates actions from claim state per the 11 trigger rules (bill review, weekly diary check-in, surgical pre-auth, overdue RTW, modified duty, defense counsel, SIU escalation, OSHA log, payment confirmation, assessment approval, padding to ≥3), capped at 6, each with an urgency tag (FR-ACT-1, FR-H-5).
2. **And** trigger parameters live in a JDM decision table (AD-8).
3. **Given** an action's "go to" button, **when** clicked, **then** it deep-links to the relevant existing tab/modal (View Bill → Bills, View Documents → Documents), while links to not-yet-built surfaces (Diary/Meetings → Epic 4, Fraud Indicators → Epic 6) render disabled with a tooltip.
4. **Given** "Approve Assessment" on a claim with status `initial` / `ch_assessment_process`, **when** clicked, **then** a status-guarded audited command sets `ch_approved`, and queue, detail, and pending-approval counts refresh (FR-ACT-1).
5. **Given** a status-toggle button (Mark Reviewed / Logged / Confirmed …), **when** clicked, **then** the completion is persisted and audited, and the action list re-renders.

## Tasks / Subtasks

- [x] Task 1: Action generator in `services/worklist` (AC: 1, 2)
  - [x] One generator `generate_actions(claim, financials, caller_ctx) -> list[Action]` evaluating the 11 trigger rules against claim state + 3.3/3.4 financial state; each action carries `{key, label, urgency, target, enabled}`; deterministic — never LLM-originated (AD-2)
  - [x] The 11 rules (behavior contract; conditions read claim/financial state): bill review (bills `under_review` on file), weekly diary check-in (treatment stage), surgical pre-auth (`surgery_required` and not yet authorized), overdue RTW (`rtw_blocked` / RTW date passed without actual RTW), modified duty (return status indicates therapy/modified capacity), defense counsel (`litigation_flag`/attorney represented), SIU escalation (`siu_review` derived flag), OSHA log (`osha_recordable` not yet logged), payment confirmation (schedule week `due_this_week`/`pending_approval`), assessment approval (status `initial`/`ch_assessment_process`), padding rules to bring the list to ≥ 3 (routine review items)
  - [x] Cap the rendered list at 6 with urgency-ranked selection; cap, padding floor, and trigger thresholds are parameters in a ZEN JDM **decision table** (`worklist_actions`), DB-versioned with effective dates (AD-8) — trigger *logic* stays typed Python
  - [x] Flags consumed (`siu_review`, `rtw_blocked`, `payment_due`) come only from `services/derivations` (AD-10); real business definitions for the demo hash-bucket flags remain a Deferred item — consume the registered derivation as-is
  - [x] Expose via a claim actions endpoint; this same generator's top action feeds Story 5.4's "Priority Next Best Action" column — return a stable ordering
- [x] Task 2: Upcoming Actions UI (AC: 1, 3)
  - [x] "⏰ Upcoming actions required" card in Overview (prototype `actionsBlockHTML`, lines 1224–1254, for layout/feel: sectioned rows, urgency tags high/med, right-aligned go-to buttons; empty state "No outstanding actions — claim is on track")
  - [x] Enabled deep links: View Bill → Bills & Payments tab (3.3, approval works per 3.4), View Documents → Documents & ID tab (2.5), Overview targets; disabled with tooltip ("Available with Diary — Epic 4" / "Available with AI Insights — Epic 6"): Log Diary Entry + Schedule Meeting (Story 4.2 explicitly enables the diary link later), View Fraud Indicators (Story 6.2 enables it), and Open RTW Letter (the RTW-letter modal is Epic 6 / FR-H-11 — same disabled-with-tooltip pattern; the overdue-RTW *action* still renders, only its modal link waits)
  - [x] Deep links are real navigation (tab switch within the detail pane) — reuse Epic 2's tab state, no bespoke routing
- [x] Task 3: Approve Assessment command (AC: 4)
  - [x] Claim status enum (snake_case): `initial | ch_assessment_process | ch_approved | …` (UI owns labels "Initial", "CH Assessment Process", "CH Approved")
  - [x] Status-guarded audited CAS command in `services/claims` (claim write-owner, AD-12): allowed only from `initial`/`ch_assessment_process` → `ch_approved`; guard violation → 409 problem+json; audit + `timeline_event` in-transaction (AD-4); prototype `approveClaim` (925–930) is the behavioral reference
  - [x] On success the SPA invalidates claim, queue, and stats query keys so queue card, detail header, and pending-approval counts (top bar / priority score input) refresh together (AD-9/AD-10)
- [x] Task 4: Status-toggle persistence (AC: 5)
  - [x] Completions persist on the entity each trigger reads — no new generic "action state" table (the narrative fixes this: worklist and ledgers stay in sync "because they read the same status row"): Mark Reviewed / Confirmed on documents → `reviewed`/`confirmed` fields on `document` via an audited `services/claims` command (Excel Documents review→confirm write-back; prototype `docReviewState`, 871–883); payment confirmation → 3.4's approval commands; OSHA "Mark Logged" → an audited `osha_logged` claim field via `services/claims`
  - [x] Toggles whose backing entity ships later (diary check-in completion → Epic 4 note) render disabled with the same tooltip pattern as their deep links
  - [x] Every toggle re-renders the action list from the server (the trigger re-evaluates and the item drops/changes) — no client-side list mutation
- [x] Task 5: Tests (AC: all)
  - [x] Unit: each of the 11 triggers firing and not firing (fixture claims per rule); cap at 6 with urgency ranking; padding to ≥ 3; JDM parameter resolution (load a variant decision table); stable top-action ordering
  - [x] Command tests: Approve Assessment from `initial`, from `ch_assessment_process`, rejected from `ch_approved` (409); audit + timeline emission; document review→confirm sequencing (confirm requires reviewed)
  - [x] E2E `e2e/stories/3-5-auto-generated-action-checklist-approval.spec.ts` `@story:3-5 @epic:3`: `@smoke` happy path — handler opens a claim with status `ch_assessment_process`, actions block lists ≥ 3 items with urgency tags, "View Bill" jumps to Bills tab, a disabled link shows its tooltip, "Approve Assessment" → status chip becomes CH Approved and the queue card refreshes

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story closes Epic 3: the deterministic action generator, its JDM decision table, the Upcoming Actions card with real deep links, one-click Approve Assessment, and entity-backed completion toggles. It consumes everything the epic built (3.3 bills/schedule state, 3.4 approval surfaces) plus Epic 2 (tabs, documents, queue).

It is **not**: the Diary/Meetings surfaces (Epic 4 — links disabled with tooltip; Story 4.2 will enable the diary deep-link this story leaves disabled), the Fraud Indicators / AI Insights surface or RTW letter (Epic 6 — disabled with tooltip; 6.2 enables the fraud link), the supervisor top-30 worklist (5.4 — it *consumes* this generator's top action), or any LLM involvement (next-best actions here are deterministic; Epic 6's AI "next best actions" insight is a separate cached narrative that may quote — never replace — this generator's output, AD-2/AD-10).

**Cap discrepancy (documented):** epics.md AC says capped at **6**; the narrative architecture (§3.2) mentions "the worklist's 7-item cap" and the prototype's current block uses `budget=7` (line 1241). The epics AC governs this story: seed the JDM cap parameter at 6. Since it's a JDM parameter, tuning to 7 later is a rules change, not a code change. Note also the prototype's as-built block is a document/bill-driven variant, not the 11 named rules — the 11-rule list in the AC (from the BRD/Excel) is the production contract; the prototype supplies look-and-feel only.

### Architecture compliance (binding ADs for this story)

- **AD-2:** action generation exists once in `services/worklist`; deterministic; 5.4 and Epic 6 consume, never re-derive.
- **AD-8:** trigger parameters/thresholds/cap/padding floor in a JDM decision table; trigger logic in typed Python — one tier per element.
- **AD-4:** Approve Assessment and every toggle are status-guarded/CAS'd audited commands with in-transaction audit events.
- **AD-12:** writes route to entity owners — claim status + document review + OSHA flag via `services/claims`; payment confirmations via `services/financials` (through 3.4's worklist delegation); `services/worklist` itself owns no table writes here, matching the capability map ("worklist commands → owning services").
- **AD-10:** derived flags feeding triggers come from registered derivations; queue, detail, and action list always agree.
- **AD-7:** generation and commands run under the caller's scope context; approval is a handler capability.
- **Conventions:** snake_case status enums with UI labels; 409 problem+json on guard violations; no native dialogs.

### Data notes

- **Creates:** no new tables (deliberate — completions are entity-backed; if a dev concludes a generic action-state store is unavoidable, that is an AD-12 registry addition requiring an architecture conversation, not a quiet migration). **Alters:** `document` (+`reviewed`,`confirmed`), `claim` (+`osha_logged`, and the status enum formalization if 1.2 seeded status as text).
- Seed check: prototype claims carry statuses "Initial", "CH Assessment Process", "CH Approved", etc. — confirm 1.2's seed normalized them to snake_case enum values; if not, this story's migration normalizes.
- JDM `worklist_actions` decision table seeded with: cap 6, padding floor 3, urgency mapping per rule, and per-rule thresholds (e.g. SIU escalation rides the `siu_review` derivation, which encodes fraud score ≥ 60).

### UX notes

- UX-DR5 (Overview hosts the block). Look/feel: prototype 1224–1254 — sectioned action rows, `acti-tag` urgency chips (high/med), go-to buttons, empty state. Urgency chip colors from Epic 1 tokens (design-token ruling: prototype LIGHT palette is canonical; epics' "dark console aesthetic" is a documented discrepancy).
- Disabled-link treatment: visibly disabled button + tooltip naming the arriving epic (UX-DR11 tooltip patterns; NFR-3 no dead clicks) — this is the cross-epic seam pattern the readiness review verified for epic independence.
- Approve Assessment success: non-blocking toast + synchronized refresh of queue card, header badge row, and top-bar counts.

### Testing requirements (this story's definition of done)

- Unit coverage of all 11 triggers (fire / no-fire), cap + padding, JDM resolution, ordering stability.
- Command tests for the status-guard matrix on Approve Assessment and document review→confirm.
- E2E: `e2e/stories/3-5-auto-generated-action-checklist-approval.spec.ts` tagged `@story:3-5 @epic:3`, one `@smoke` happy path (list renders, deep link works, disabled tooltip shown, approval flips status and refreshes queue). Story cannot move to `review`/`done` until it passes (AD-15).

### Project Structure Notes

- Code lands in `lineworker/server/services/worklist/` (generator + delegating commands), `services/claims/` (assessment/document/OSHA commands), `server/rules/` (JDM `worklist_actions`), `web/src/features/claim-detail/` (actions card, tooltips, deep-link wiring), query keys in `web/src/api/queryKeys`.
- Keep the action `target` field a typed enum of navigable surfaces (bills/documents/overview/diary/meetings/fraud/rtw_letter/approve) — Stories 4.2 and 6.2 flip `enabled` for their targets without touching the generator.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 3.5]
- AD-2, AD-8 (decision tables), AD-4, AD-12 (worklist commands → owning services), AD-10: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- Capability map row (action worklist + approvals; Excel rows 29/64–66): [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map]
- Entity-backed sync rationale ("read the same status row") + write-back semantics: [Source: docs/Architecture-LINEWORKER.md#4.2 Key data rules]
- 7-item cap mention (discrepancy vs AC's 6): [Source: docs/Architecture-LINEWORKER.md#3.2 The three logic layers]
- Cross-epic seam enablement: diary link enabled by 4.2, fraud link by 6.2: [Source: _bmad-output/planning-artifacts/epics.md#Story 4.2 / #Story 6.2]
- Behavioral reference: `actionsBlockHTML` (1224–1254), `handleActionGoto` (904–912), `approveClaim` (925–930), `docReviewState` review/confirm (871–883): [Source: docs/Workers_Comp_Prototype.html]
- FR-ACT-1 / FR-H-5; consumer FR-SUP-5 (top action in 5.4): [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory / #Story 5.4]

## Dev Agent Record

### Agent Model Used

claude-opus-5[1m] (Claude Opus 5, 1M context), via the `bmad-dev-auto` workflow.

### Debug Log References

- **`ActionUrgency` had to live in `data/models/enums.py`, and the usual reason for that is not the reason.** `BodyRegion` and `ScheduleWeekStatus` are there because a native enum column needs its members below `services/`; nothing here will ever be a column. What forced it is the *rules tier*: `worklist_actions` carries one urgency per rule, and `WorklistActions.of` resolves each member by name at load time — `_status_set`'s discipline, which turns a typo in an operator-edited document into one refusal naming the value instead of a chip rendering a token no stylesheet has a colour for. `rules/` must not import from `services/` (the direction `parameters.py` states, and which `_COMP_RATE_MIN_BP` already pays for the hard way by copying a bound rather than importing it), so `data/models/enums.py` is the only vocabulary module below both tiers. `ActionKey` and `ActionCommand` followed for cohesion, and `ActionKey` earns its place independently: `urgency_parameter_name` derives `urgencyBillReview` from the member, so a twelfth rule cannot ship without the document gaining a key.
- **The case-file payload had no `status` at all, and AC 4 asks the header to show it change.** `CaseHeader` has carried `stage` since Story 2.2 because that is what the pill and the stepper render; the claim's *assessment* status had no surface — nothing displayed it and nothing wrote it, so nothing missed it. This story writes it, so the column joined the header (and `osha_logged` beside `osha_recordable`, which is the pair the OSHA trigger is the gap between). Found by a command test asserting `header.status` and getting a `KeyError`, which is the right way to find it: the AC named a surface that did not exist.
- **AC 4's "pending-approval counts" has no top-bar tile to move, and the story's own sentence says where the count actually is.** The top bar counts caseload, active treatment and high risk (Story 1.4); none moves on an approval, and a test written against `pendingApproval` failed with a `KeyError`. The AC's phrase is "top bar / priority score input", and the second half is real: `priority_weights` carries a `pendingApproval` term over exactly the statuses this command moves a claim *out of*, so approving drops the claim's score and can move its card. The test now asserts the score decreases rather than the weight's value, because restating the weight would be a test deciding a rules parameter.
- **`next(... for c in ... if await ...)` is an async generator, and `next` on one raises `TypeError`.** Both new test modules were first written with that shape to search the caseload for a claim carrying a given trigger, and both failed with a message about iterators rather than about the predicate. Explicit loops now, in helpers whose docstrings say why — recorded because the failure reads like a bad search and is a wrong comprehension.
- **`expectedVersion` has a `ge=1` floor on the wire, so `version - 1` is a 422 rather than a 409 on a never-edited claim.** The stale-write tests send `version + 1` instead: any mismatch is a mismatch, and the point is the compare-and-swap rather than Pydantic's bound. The same shape will bite every later story that tests a stale write against a freshly seeded row.
- **An ORM row read back after `expire_all()` compares against itself.** `test_marking_a_document_reviewed_persists_and_audits_it` asserted `fresh.version == document.version + 1` and failed with `2 == 3` — because `documents_of` expires the session, `document.version` refreshed to the *new* value, and the assertion was about one number. Two integers held in locals before the write. This is the same class of mistake Story 3.4 recorded for `db.rollback()`, from the other direction: there the object was unreadable, here it was too readable.
- **`openapi-fetch` calls `fetch(new Request(...))`, so `String(input)` in a component test reads `[object Request]`.** Three assertions about which endpoint a control called passed vacuously until a `requested()` helper unwrapped the same three shapes the fetch stub already unwraps. Worth remembering: any vitest assertion about a *request* (rather than about the DOM) needs that unwrapping, and without it it silently passes.
- **`invalidateQueries` on an *active* key is not observable through `isInvalidated`.** The test asserting the checklist re-renders after an approval flipped between pass and fail depending on whether the refetch had already cleared the flag. It now counts requests to `/actions` instead, which is the thing AC 5 actually asks for and cannot be racy in the direction that would make the test lie.
- **`getByTestId(id, { selector })` narrows which elements the *matcher* considers, not which match is returned** — so addressing one of six action rows that way throws "multiple elements found". A `row(id)` helper filters `getAllByTestId` by `data-action`, which is also the better test: the id is the identity the server sends, and a test that indexed into the list would pass against a card that had reordered it.
- **Kaya's book holds exactly one `ch_assessment_process` claim, and the smoke test approves it.** The e2e's double-approval test took "the second one", addressed `undefined`, and failed on a 422 that said nothing about compare-and-swap. It uses an `initial` claim now — the other half of the approvable set — with a comment naming the per-file reset as the reason state carries between tests in one spec.

### Completion Notes List

- **AC 1 — the generator is a table of eleven small functions, and the table's order is a published contract.** Rows rank on `(urgency, rule index)` where the index is `ActionKey`'s declaration order, so the list is *totally* ordered rather than merely sorted. That matters exactly once and it matters a lot: Story 5.4 reads element 0 as a claim's "Priority Next Best Action", and a tie among the five `medium` rules broken by dict iteration would give a claim a different next action between releases — invisible on a six-row card and wrong in a supervisor's column. `test_the_order_is_stable_when_the_inputs_are_shuffled` reverses every input list and asserts the output is identical, and the e2e asserts row *n* of the DOM is item *n* of the payload rather than asserting the set.
- **AC 1 — ranking happens before capping, and padding only afterwards.** Cutting first would make the cap mean "whichever six fired earliest in the table"; padding before the cut would let a routine review row displace a real trigger. Both are asserted directly (`test_the_cap_keeps_the_most_urgent_rather_than_the_first_to_fire`, `test_padding_never_displaces_a_trigger`), and `test_all_ten_trigger_rules_can_fire_on_one_claim` guards the premise — a rule that could no longer fire alongside the others would leave the cap tests exercising nothing.
- **AC 1 — the padding rows are rules, not filler, which is what keeps the empty state reachable.** All three are conditional (an open claim's case file, a claim with documents, a claim with a schedule), so a settled claim with neither produces *nothing* and the card renders the prototype's own "No outstanding actions — claim is on track." A generator that padded unconditionally would make that branch dead code, and the story's I/O matrix names it as a case.
- **AC 2 — thirteen parameters out of Python, and the claim is demonstrated rather than asserted.** `worklist_actions` carries `cap`, `paddingFloor` and one urgency per rule; the eleven trigger *conditions* stay typed Python, which is AD-8's split. The proof is `test_a_superseded_rule_document_changes_the_list_with_no_code_change`: a v2 with `cap: 1` is inserted effective today, the same claim's list comes back one row long with `rulesVersion: 2`, and the survivor is the first row of the original — the ranking unchanged, only the length. Nothing is deployed or restarted. The **node type** is an `expressionNode` rather than the AC's "decision table", for the reason all nine committed documents are and the reason `rules/engine.py` evaluates once per request; recorded in `deferred-work.md` with what a real table would buy.
- **AC 3 — the seam is one server field, and that is what makes Stories 4.2 and 6.2 one-line changes.** Four targets have no surface yet (diary, meetings, fraud, RTW letter) and the generator sends them `enabled: false` with the sentence naming the epic. The browser holds no map of what has shipped — `test_the_seam_reason_shown_is_the_servers` retunes the sentence in the payload and watches the card follow it. Disabled rows also carry no completion command, because a button recording work nobody could do would be a false record.
- **AC 3 — disabled-with-a-tooltip is new here, and the tooltip is not the only way to read the reason.** The house pattern elsewhere is to *replace* an unavailable control with a sentence (`ApproveControl`'s four branches); the AC asks for the other thing, because the point is that the work is real and the surface is not. A disabled `<button>` fires no pointer events, so the Radix trigger is a wrapping span (their own guidance), the reason is also the control's `title`, and it is announced through `aria-describedby` — NFR-3's "no dead clicks" is not satisfied by an explanation only a mouse can reach. The provider is card-local, following `SlaStrip`'s precedent rather than adding one at the app root for one card.
- **AC 4 — the status guard is not redundant with the version, and the test that proves it uses a *current* version.** `transition_claim_status_cas` compare-and-swaps on `expected_version` **and** constrains the claim's status to `{initial, ch_assessment_process}`, in one statement. `test_a_claim_outside_the_approvable_statuses_is_refused_with_the_fresh_entity` is parametrised over `ch_approved`, `denied` and `settled_closed` with the version the caller just read — so the version alone would have let all three through, and a `denied` claim quietly becoming `ch_approved` is the one that would have mattered.
- **AC 4 — one command moves three surfaces because all three read one status row.** The response is the whole case file (the header's chip comes from it directly), the checklist key is invalidated `exact: true` so the row disappears from a *server* re-read rather than from a client-side splice, and the queue and top-bar keys are invalidated because the claim's `pendingApproval` term has changed and nothing in the browser could compute that. The e2e walks all three in one test.
- **AC 4 — the three role refusals are byte-identical across a claim in the book, one outside it and one that does not exist.** Asserted as an equality over three response bodies, for both non-handler personas, and the analyst is the interesting case: their scope is the whole portfolio, so they are the persona for whom scope could not have narrowed the answer. Role is checked before the claim is read, which is what makes that true rather than lucky.
- **AC 5 — completions are entity-backed and there is no action-state table.** `claim.osha_logged`, `document.reviewed` and `document.confirmed` are the three sanctioned columns, and every completion makes its own row stop firing because the trigger and the write read the same column — the narrative's "the worklist and the ledgers stay in sync because they read the same status row", made structural. The review→confirm ordering is a WHERE clause (`reviewed = true AND confirmed = false`), not a disabled button, so an agent tool or a replayed request cannot skip it; confirming an unreviewed document is a 409 with the fresh entity, because it is a statement about where the row is rather than about the request.
- **AC 5 — re-doing a completion is a 200 that writes nothing, and that distinction is load-bearing on this card.** `update_comp_rate_override`'s rule: the log is a record of changes. These controls sit on a card that re-renders after every write, and a handler double-clicking one must not be told somebody else changed their claim. A *stale* version is still a 409 — the pre-check runs first — so the no-op path is only reached once the version is known to be current.
- **The OSHA refusal is a 422 where the others are 409s, and that is the only place these three differ in kind.** A claim that is not `osha_recordable` has nothing to re-read and trying again will never work: recordability is a property of the injury, not a state a claim passes through. Every other refusal on all three routes is one of `_answer`'s four, which is why the routes are three lambdas — the mapping was written once in Story 2.3 and Epic 3 inherits it rather than copying it.
- **`document_step_of` takes two booleans rather than a `Document`, and that is what keeps one reader.** The offered control and the accepted transition are one rule; `services/worklist` holds documents behind a structural protocol (which is what keeps `generate_actions` pure and database-free) and must not have that protocol imported back. Two booleans let both packages ask the same function instead of agreeing about "reviewed but not confirmed" — which looks like the whole rule and is not, because a *confirmed* document is eligible for nothing.
- **The card is where the toggles are, and the payment confirmation is deliberately not one.** Story 3.4 owns that command and its ✓ lives in the Bills sheet, so the payment row deep-links there rather than growing a second approval surface with its own version handling and its own copy of the approvable-status rule. Recorded in `deferred-work.md` as the one part of Task 4 that routes rather than acts.
- **`services/worklist` still writes nothing.** It generates the list; the three completions are `services/claims` commands because `claim` and `document` are that package's tables (AD-12). The import direction is worklist → claims, one-way, the same direction 3.4's approval already runs in towards `services/financials`.
- **Tests:** **1645 server** (+106 — the eleven rules firing and not firing, parametrised over whole vocabularies where one exists so the *other* direction is covered too; the cap keeping the most urgent rather than the first, over a claim on which all ten fire; ordering stability under shuffled inputs and the equal-urgency tiebreak in isolation; padding to the floor, padding not displacing a trigger, padding offering only what applies, and the floor switched off; the seam's four targets and the rule that a disabled row carries no command; the pre-auth row's version and its advancing step; the endpoint over twelve claims asserting cap and rank; the superseded-document swap end to end; the status-guard matrix over three terminal statuses with a current version; the stale-version 409 with its payload; both non-handler personas refused identically over three kinds of claim; the review→confirm pair, the out-of-order refusal, the no-op re-click and the cross-claim document 404; the OSHA trigger, its 422, its idempotent re-click and its stale refusal; and the thirteen rule-parameter refusals including a read-only urgency mapping). **314 vitest** (+18 — the four states the server cannot describe, the six rows rendered in the server's order with the server's chips, the deep link firing the pane's navigation, the seam control disabled with its reason reachable without a pointer, the reason following the server rather than a local map, the three completion controls with the versions and steps they send, the polite live region, and the 409 rendered inline; plus the checklist fields and the actions folder added to `noDerivation.test.ts`). **123 Playwright** (8 new, `@story:3-5 @epic:3`, one `@smoke` walking the card, a deep link, a disabled seam control, the approval and its timeline row).
- **Verified live.** e2e stack (`compose.e2e.yaml`, :8081) rebuilt with the new API and SPA: full suite **123/123**, the 18-test `@smoke` set and the 38-test `@epic:3` set all green against a freshly reset stack. `alembic check` reports no drift and a `downgrade 0029` → `upgrade head` round trip completes clean. `npm run generate:api` leaves `src/api/schema.d.ts` byte-identical on a second run. ruff + format + mypy clean across 175 files; eslint clean (8 pre-existing warnings, none new); tsc clean for `web/` and `e2e/`. No new runtime or dev dependency.

### File List

**New — server**

- `lineworker/server/rules/documents/worklist_actions.jdm.json`
- `lineworker/server/data/versions/20260817_0030_action_checklist_columns.py`
- `lineworker/server/data/versions/20260817_0031_worklist_action_rules.py`
- `lineworker/server/services/worklist/actions.py`
- `lineworker/server/services/claims/assessment.py`
- `lineworker/server/tests/test_action_checklist.py`
- `lineworker/server/tests/test_assessment_commands.py`

**Modified — server**

- `lineworker/server/data/models/enums.py` (`ActionKey`, `ActionUrgency`, `ActionTarget`, `ActionCommand`; `TimelineTag.document` and `.compliance`)
- `lineworker/server/data/models/core.py` (`claim.osha_logged`, `document.reviewed`, `document.confirmed`)
- `lineworker/server/data/repositories/claims.py` (the claim status CAS, the OSHA CAS, the document review CAS)
- `lineworker/server/rules/parameters.py` (`WORKLIST_ACTIONS_KEY`, `WorklistActions`, `urgency_parameter_name`, `_urgencies`)
- `lineworker/server/services/claims/detail.py` (`status` and `osha_logged` on the case header)
- `lineworker/server/services/worklist/__init__.py` (the package's second command-adjacent module and its exports)
- `lineworker/server/api/routers/claims.py` (the actions endpoint and the three command routes with their schemas)
- `lineworker/server/tests/test_rules_engine.py` (the sixth effective document, its values, and the per-rule urgency coverage)
- `lineworker/server/tests/test_rule_parameters.py` (the block's refusals)

**New — web / e2e**

- `lineworker/web/src/features/claim-detail/actions/ActionsCard.tsx`
- `lineworker/web/src/features/claim-detail/actions/actionTone.ts`
- `lineworker/web/src/features/claim-detail/actions/ActionsCard.test.tsx`
- `lineworker/e2e/stories/3-5-auto-generated-action-checklist-approval.spec.ts`

**Modified — web / e2e**

- `lineworker/web/src/api/claims.ts` (`useClaimActions` and the three mutation hooks, `afterChecklistWrite`)
- `lineworker/web/src/api/queryKeys.ts` (`claims.actions`)
- `lineworker/web/src/api/schema.d.ts` (regenerated)
- `lineworker/web/src/features/claim-detail/labels.ts` (`CLAIM_STATUS_LABEL`, `ACTION_URGENCY_LABEL`, `ACTION_TARGET_LABEL`)
- `lineworker/web/src/features/claim-detail/CaseHeader.tsx` (the status pill)
- `lineworker/web/src/features/claim-detail/ClaimDetailPane.tsx` (the `onNavigate` built from the tab state)
- `lineworker/web/src/features/claim-detail/overview/{Intake,Investigation,Treatment,Settled}Overview.tsx` (the card, in the full-width slot)
- `lineworker/web/src/features/queue/noDerivation.test.ts` (the checklist's fields and the new folder)
- `lineworker/web/src/test/api-mock.ts` (the four routes and their fixtures, `status`/`oshaLogged` on the header)
- `lineworker/e2e/fixtures/seed.ts` (`claimIdsWithStatus`, `isOshaRecordable`, `osha_recordable` on the seed type)

**Modified — repo**

- `_bmad-output/implementation-artifacts/deferred-work.md` (six items)
- `_bmad-output/implementation-artifacts/3-5-auto-generated-action-checklist-approval.md` (this file), `_bmad-output/implementation-artifacts/spec-3-5-auto-generated-action-checklist-approval.md`

### Change Log

- 2026-08-17: Story 3.5 implemented end to end — the epic's last surface, and the one that answers "what do I do next". `services/worklist/actions.py` is one deterministic generator: eleven trigger rules over claim, document and financial state, ranked on `(urgency, declaration order)` so the ordering is *total* rather than merely sorted (Story 5.4 reads element 0 of it), capped at six and padded to three from a versioned JDM document that owns every tunable and none of the logic — a v2 with a different cap changes the rendered list with no deploy, demonstrated end to end rather than asserted. `services/claims/assessment.py` adds the three audited commands the card acts through, each with AD-4's extra rung — a guard on the expected current state in the same statement as the version — and each proved against a *current* version so the guard is shown to be doing work the CAS does not. Completions are entity-backed by design: three boolean columns (migration 0030), no action-state table, and every completion makes its own row stop firing because the trigger and the write read the same column. The Overview gains the "⏰ Upcoming actions required" card on all four stage variants, with real tab-switch deep links, four cross-epic seam controls that are visibly disabled and say which epic enables them (reachable without a pointer), and a header status chip the approval moves. Full gate green: ruff + format + mypy clean across 175 files, pytest 1645 (0 skipped), vitest 314, tsc and eslint clean, Playwright 123/123 plus the 18-test `@smoke` and 38-test `@epic:3` sets against a rebuilt e2e stack; `alembic check` clean with a `downgrade 0029` → `upgrade head` round trip.
- 2026-08-17: Review pass (bmad-dev-auto step 4 — Blind Hunter and Edge Case Hunter over the whole diff). Twelve distinct findings after dedup: ten patched, two deferred, one rejected; no intent gaps and no spec defects, so no re-derivation loop. The four that mattered were all cases of a surface telling a handler something untrue. The `approve` and `overview` rows rendered a *second*, enabled go-to button that navigated nowhere — the dead click NFR-3 forbids, sitting beside the ✓ that works; the card now states which targets lead somewhere from the Overview tab it is mounted in. All three checklist mutations installed the fresh entity on a 409 but never invalidated the checklist key, so the header chip moved while the row that caused the refusal stayed put — and a document row kept a superseded version and refused every later click; this is the same defect fixed in `useApprovePayment.onError` that morning, which is why the comment names it. `_overdue_rtw` read only a null `actual_rtw`, so WC-21122 and WC-21683 — returned, under therapy, with a records gap — carried a `high` "chase the return to work" beside `modified_duty`'s "the worker is back on restricted capacity"; the rule now gates on `return_status`, which is what its own first line always claimed, and the consequence is absorbed rather than papered over: the two rules are mutually exclusive by construction, so the premise test asserts the union across both return-status worlds instead of pretending ten rules fire on one claim. And a refusal from one command survived a later success from another, because nothing reset the siblings. Six smaller ones: the live region concatenated sticky success sentences, `busy` relabelled buttons for commands nobody had started, padding rows were never re-ranked (correct only while the routine urgency stays lowest — a retune the story advertises as deploy-free), `BILL_REVIEW_STATUSES` restated a set it could import, and two comments described mechanisms that do not exist. One attempted patch was reverted: making re-approval a 200 no-op broke both its unit test and its e2e, which is how it became clear the 409 was the author's tested policy — the module docstring promising otherwise was corrected instead. Full gate re-run after patching, including an e2e stack rebuilt from the patched source (the first run had used pre-patch images and was discarded): ruff + format + mypy clean, pytest 1646, vitest 314, Playwright 123/123. Status → review.
- 2026-08-17: Accepted after human review — status → done, and Epic 3 closes with it. Accepted without the independent follow-up pass the review recommended (`followup_review_recommended: true` on the spec, set because ten patches carried four medium-severity behaviour changes); recorded here rather than dropped, so the next reader knows the second look was offered and declined rather than forgotten. The four surfaces those patches touched — the go-to controls, the three 409 cache paths, the overdue-RTW gate and the card's refusal/announcement state — are the ones to read first if anything on this card ever looks wrong.
