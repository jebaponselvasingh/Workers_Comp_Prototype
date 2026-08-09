# Story 3.5: Auto-Generated Action Checklist & Approval

Status: ready-for-dev

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

- [ ] Task 1: Action generator in `services/worklist` (AC: 1, 2)
  - [ ] One generator `generate_actions(claim, financials, caller_ctx) -> list[Action]` evaluating the 11 trigger rules against claim state + 3.3/3.4 financial state; each action carries `{key, label, urgency, target, enabled}`; deterministic — never LLM-originated (AD-2)
  - [ ] The 11 rules (behavior contract; conditions read claim/financial state): bill review (bills `under_review` on file), weekly diary check-in (treatment stage), surgical pre-auth (`surgery_required` and not yet authorized), overdue RTW (`rtw_blocked` / RTW date passed without actual RTW), modified duty (return status indicates therapy/modified capacity), defense counsel (`litigation_flag`/attorney represented), SIU escalation (`siu_review` derived flag), OSHA log (`osha_recordable` not yet logged), payment confirmation (schedule week `due_this_week`/`pending_approval`), assessment approval (status `initial`/`ch_assessment_process`), padding rules to bring the list to ≥ 3 (routine review items)
  - [ ] Cap the rendered list at 6 with urgency-ranked selection; cap, padding floor, and trigger thresholds are parameters in a ZEN JDM **decision table** (`worklist_actions`), DB-versioned with effective dates (AD-8) — trigger *logic* stays typed Python
  - [ ] Flags consumed (`siu_review`, `rtw_blocked`, `payment_due`) come only from `services/derivations` (AD-10); real business definitions for the demo hash-bucket flags remain a Deferred item — consume the registered derivation as-is
  - [ ] Expose via a claim actions endpoint; this same generator's top action feeds Story 5.4's "Priority Next Best Action" column — return a stable ordering
- [ ] Task 2: Upcoming Actions UI (AC: 1, 3)
  - [ ] "⏰ Upcoming actions required" card in Overview (prototype `actionsBlockHTML`, lines 1224–1254, for layout/feel: sectioned rows, urgency tags high/med, right-aligned go-to buttons; empty state "No outstanding actions — claim is on track")
  - [ ] Enabled deep links: View Bill → Bills & Payments tab (3.3, approval works per 3.4), View Documents → Documents & ID tab (2.5), Overview targets; disabled with tooltip ("Available with Diary — Epic 4" / "Available with AI Insights — Epic 6"): Log Diary Entry + Schedule Meeting (Story 4.2 explicitly enables the diary link later), View Fraud Indicators (Story 6.2 enables it), and Open RTW Letter (the RTW-letter modal is Epic 6 / FR-H-11 — same disabled-with-tooltip pattern; the overdue-RTW *action* still renders, only its modal link waits)
  - [ ] Deep links are real navigation (tab switch within the detail pane) — reuse Epic 2's tab state, no bespoke routing
- [ ] Task 3: Approve Assessment command (AC: 4)
  - [ ] Claim status enum (snake_case): `initial | ch_assessment_process | ch_approved | …` (UI owns labels "Initial", "CH Assessment Process", "CH Approved")
  - [ ] Status-guarded audited CAS command in `services/claims` (claim write-owner, AD-12): allowed only from `initial`/`ch_assessment_process` → `ch_approved`; guard violation → 409 problem+json; audit + `timeline_event` in-transaction (AD-4); prototype `approveClaim` (925–930) is the behavioral reference
  - [ ] On success the SPA invalidates claim, queue, and stats query keys so queue card, detail header, and pending-approval counts (top bar / priority score input) refresh together (AD-9/AD-10)
- [ ] Task 4: Status-toggle persistence (AC: 5)
  - [ ] Completions persist on the entity each trigger reads — no new generic "action state" table (the narrative fixes this: worklist and ledgers stay in sync "because they read the same status row"): Mark Reviewed / Confirmed on documents → `reviewed`/`confirmed` fields on `document` via an audited `services/claims` command (Excel Documents review→confirm write-back; prototype `docReviewState`, 871–883); payment confirmation → 3.4's approval commands; OSHA "Mark Logged" → an audited `osha_logged` claim field via `services/claims`
  - [ ] Toggles whose backing entity ships later (diary check-in completion → Epic 4 note) render disabled with the same tooltip pattern as their deep links
  - [ ] Every toggle re-renders the action list from the server (the trigger re-evaluates and the item drops/changes) — no client-side list mutation
- [ ] Task 5: Tests (AC: all)
  - [ ] Unit: each of the 11 triggers firing and not firing (fixture claims per rule); cap at 6 with urgency ranking; padding to ≥ 3; JDM parameter resolution (load a variant decision table); stable top-action ordering
  - [ ] Command tests: Approve Assessment from `initial`, from `ch_assessment_process`, rejected from `ch_approved` (409); audit + timeline emission; document review→confirm sequencing (confirm requires reviewed)
  - [ ] E2E `e2e/stories/3-5-auto-generated-action-checklist-approval.spec.ts` `@story:3-5 @epic:3`: `@smoke` happy path — handler opens a claim with status `ch_assessment_process`, actions block lists ≥ 3 items with urgency tags, "View Bill" jumps to Bills tab, a disabled link shows its tooltip, "Approve Assessment" → status chip becomes CH Approved and the queue card refreshes

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

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
