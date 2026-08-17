---
title: 'Story 3.5: Auto-Generated Action Checklist & Approval'
type: 'feature'
created: '2026-08-17'
status: 'done'
baseline_revision: 'fbb9ca08af051044ad88399dbc26a799e84dc445'
final_revision: 'f3ad6ccb751a419b8bc92b9ce138aa53434ee661'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/_bmad-output/implementation-artifacts/3-5-auto-generated-action-checklist-approval.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** A handler opening a case file gets figures and history but no answer to "what do I do next" — the prototype's Upcoming Actions block is document-driven look-and-feel with no rules behind it, and the claim's own assessment status can only be advanced by editing a field.

**Approach:** One deterministic generator in `services/worklist` evaluates eleven trigger rules against claim, document and financial state, ranks and caps them from rules-tier parameters, and serves them on a claim actions endpoint; the Overview renders them with real deep links (and honestly disabled ones for surfaces later epics own), and three status-guarded audited commands let a handler act without leaving the card.

## Boundaries & Constraints

**Always:**
- The generator is pure typed Python and deterministic — never LLM-originated, and the same claim state yields the same list in the same order (AD-2). Stable ordering is a contract: Story 5.4 reads element 0 as the claim's "next best action".
- Every tunable — the cap, the padding floor, and each rule's urgency — lives in a versioned, effective-dated rules document (`worklist_actions`), never as a Python literal (AD-8). Trigger *logic* stays in Python.
- Derived flags (`siu_review`, `rtw_blocked`, `payment_due`) are read from `services/derivations` only; no surface re-derives them (AD-10).
- Every write is a status-guarded, CAS'd, audited command whose audit event and timeline row land in the same transaction (AD-4), routed to the entity's owning service (AD-12): claim status / OSHA / document review → `services/claims`; payment confirmation → Story 3.4's existing `services/worklist.approve_payment` delegation.
- Command ordering is role → scope → validation → version, so 403 and 404 never form an existence oracle.
- The action `target` is a typed enum of navigable surfaces so Stories 4.2 and 6.2 can flip `enabled` without touching the generator.

**Block If:**
- A generic "action state" / "completed actions" table appears necessary. Completions are entity-backed by design; a new table is an AD-12 registry addition requiring an architecture conversation, not a quiet migration. HALT rather than add one.
- The eleven rules cannot be evaluated without a claim field that does not exist and is not named in this spec's migration (`osha_logged`, `document.reviewed`, `document.confirmed` are the three sanctioned additions).

**Never:**
- No Diary, Meetings, Fraud Indicators or RTW-letter surfaces — their actions render with the go-to control disabled and a tooltip naming the arriving epic. No dead clicks.
- No client-side computation of urgency, ordering, counts or thresholds; `noDerivation.test.ts` enforces this.
- No toast infrastructure invented here (Story 3.4 deferred it) — feedback is in-place with a polite live region.
- No change to Story 3.4's payment approval commands; the payment-confirmation action calls them as they are.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Trigger fires | Claim has ≥1 bill `under_review` | `bill_review` action present, `target: bills`, `enabled: true` | No error expected |
| Trigger silent | Same claim, no bill `under_review` | `bill_review` absent from the list | No error expected |
| Cap | 9 rules fire | Exactly `cap` (6) actions returned, highest urgency first, ties broken by a fixed rule order | No error expected |
| Padding | 1 rule fires | List padded with routine review items to the padding floor (3) | No error expected |
| Seam target | `siu_review` true | `siu_escalation` action present with `enabled: false` and `disabledReason` naming Epic 6 | No error expected |
| Approve assessment | Claim `initial` or `ch_assessment_process`, matching version | Status → `ch_approved`; audit + timeline written; fresh case file returned | — |
| Approve assessment refused | Claim already `ch_approved` | Status unchanged, nothing audited | 409 problem+json with the fresh claim |
| Approve assessment stale | Correct status, stale `expectedVersion` | No write | 409 problem+json with the fresh claim |
| Approve assessment role | Caller is supervisor/analyst | No write, no claim lookup | 403, identical for a claim in and outside the caller's book |
| Confirm before review | Document with `reviewed = false` | No write | 409 problem+json — confirm requires reviewed |
| Toggle re-render | OSHA "Mark Logged" succeeds | `osha_log` action drops out of the regenerated list | — |
| Claim with nothing due | Settled claim, no open items | Padding floor still met, or the empty-state sentence when no rule and no padding item applies | No error expected |

</intent-contract>

## Code Map

- `lineworker/server/rules/documents/worklist_actions.jdm.json` -- NEW. Cap, padding floor, and the eleven per-rule urgency values as flat scalar expressions. Copy the node/edge shape of `reserve_bands.jdm.json` exactly.
- `lineworker/server/rules/parameters.py` -- ADD `WORKLIST_ACTIONS_KEY`, frozen `WorklistActions` block with `of()` + `__post_init__` range checks, and `worklist_actions_for(db, as_of=None)`. Follow `ReserveBands`.
- `lineworker/server/data/versions/20260817_0030_action_checklist_columns.py` -- NEW. `claim.osha_logged` (bool, default false), `document.reviewed` / `document.confirmed` (bool, default false). `down_revision = "0029_system_actor"`.
- `lineworker/server/data/versions/20260817_0031_worklist_action_rules.py` -- NEW. Seeds the rule document. Copy `20260814_0025_reserve_bands.py` verbatim in shape.
- `lineworker/server/services/worklist/actions.py` -- NEW. `generate_actions(...) -> list[Action]`; the eleven rules, ranking, cap, padding. The heart of the story.
- `lineworker/server/services/worklist/__init__.py` -- Export `generate_actions`, `Action`, `ActionTarget`, `ActionUrgency`.
- `lineworker/server/services/claims/assessment.py` -- NEW. `approve_assessment`, `set_document_review`, `mark_osha_logged` — three audited status-guarded commands. Mirror `services/financials/approval.py::_approve`'s locals-before-write discipline.
- `lineworker/server/data/repositories/claims.py` -- ADD `transition_claim_status_cas(db, ctx, claim_business_id, *, expected_version, expected_statuses, new_status) -> int` and a scoped document read/CAS. No `Session.get` — the employer predicate must be on the WHERE clause.
- `lineworker/server/api/routers/claims.py` -- ADD `GET /claims/{id}/actions` and the three command routes with their 409/403/404 response schemas.
- `lineworker/server/data/models/core.py`, `enums.py` -- The three new columns; `ActionKey` / `ActionUrgency` / `ActionTarget` / `ActionCommand` enums, plus `TimelineTag.document` and `.compliance`. **AS BUILT:** the enums are here for a reason the Code Map did not anticipate — the rules tier resolves urgency members and `rules/` must not import from `services/`, so `data/models/enums.py` is the only vocabulary module below both tiers. `ActionKey` and `ActionCommand` joined them: the first because `urgency_parameter_name` derives the document key from the member, the second because the offered completion is a server decision published on the action. `services/claims/detail.py` and `CaseHeaderResponse` also gained `status` and `osha_logged` — AC 4 asks the header to show a status the payload did not carry.
- `lineworker/web/src/features/claim-detail/actions/ActionsCard.tsx` -- NEW. The "⏰ Upcoming actions required" card, urgency chips, go-to buttons, disabled+tooltip seam controls, empty state.
- `lineworker/web/src/features/claim-detail/ClaimDetailPane.tsx` -- Widen the three narrow Overview variants' props and pass an `onNavigate(target)` callback built from `setActiveTab`.
- `lineworker/web/src/features/claim-detail/{Intake,Investigation,Treatment,Settled}Overview.tsx` -- Insert the card in the full-width slot between `CardGrid` and `TimelineCard`.
- `lineworker/web/src/api/claims.ts`, `queryKeys.ts` -- `useClaimActions`, the three mutation hooks, `queryKeys.claims.actions(claimId)` nested under `detail`.
- `lineworker/web/src/features/claim-detail/labels.ts`, `.../actions/actionTone.ts` -- Label and tone maps for the two new enums.
- `lineworker/web/src/features/queue/noDerivation.test.ts` -- Register the new server-computed field names; add the `toContain` assertion for the new `actions/` folder.
- `lineworker/e2e/stories/3-5-auto-generated-action-checklist-approval.spec.ts` -- NEW, `@story:3-5 @epic:3`, one `@smoke`.

## Tasks & Acceptance

**Execution:**
- [x] `rules/documents/worklist_actions.jdm.json` + `rules/parameters.py` -- author the document and its frozen block -- AC 2; every threshold out of Python.
- [x] `data/versions/20260817_0030_action_checklist_columns.py` -- add the three boolean columns -- the eleven rules and two toggles cannot read state that does not exist.
- [x] `data/versions/20260817_0031_worklist_action_rules.py` + `tests/test_rules_engine.py` -- seed and register the document -- the registry test fails on an uncovered file.
- [x] `services/worklist/actions.py` -- the generator: eleven rules, urgency rank, cap, padding, stable order -- AC 1; Story 5.4 consumes element 0.
- [x] `data/repositories/claims.py` -- `transition_claim_status_cas` + scoped document CAS -- no status-guarded claim primitive exists yet.
- [x] `services/claims/assessment.py` -- the three audited commands -- AC 4, AC 5; AD-12 routes them to the claim's owner.
- [x] `api/routers/claims.py` -- the actions endpoint and three command routes -- AC 1, 4, 5.
- [x] `web/src/api/{claims.ts,queryKeys.ts}` -- query + three mutations, invalidating claim, queue and top-bar keys -- AC 4's "queue, detail and counts refresh together".
- [x] `web/src/features/claim-detail/actions/ActionsCard.tsx` + the four Overview variants -- render the card -- AC 1, 3, 5.
- [x] `web/src/features/queue/noDerivation.test.ts` -- register the new field names and folder -- otherwise the guard fails the build.
- [x] `server/tests/test_action_checklist.py`, `test_assessment_commands.py`, `test_rule_parameters.py` -- unit-test the I/O matrix: each rule firing and not firing, cap, padding, ordering stability, the status-guard matrix, confirm-requires-reviewed -- the matrix is the definition of done.
- [x] `web/src/features/claim-detail/actions/ActionsCard.test.tsx` -- render, urgency chips, deep link fires the callback, disabled control shows its tooltip, empty state.
- [x] `e2e/stories/3-5-auto-generated-action-checklist-approval.spec.ts` -- `@smoke` walk: actions render with urgency tags, View Bill jumps to the Bills tab, a seam control is disabled with its tooltip, Approve Assessment flips the header chip and refreshes the queue card -- AD-15 gates the story on this.

**Acceptance Criteria:**
- Given a claim in any stage, when its case file loads, then the Overview shows an Upcoming Actions card whose contents came from the server in the order the server sent them, with no client-side sort, filter, count or threshold comparison.
- Given the rules document is superseded by a version with a different cap, when a case file is re-read, then the rendered list length changes with no code deployment.
- Given a handler approves an assessment, when the command succeeds, then the case-file header, the queue card and the top-bar pending-approval count all show the new status without a manual refresh.
- Given any of the three commands is called by a supervisor or analyst, when it is refused, then the response is identical whether or not the claim exists in that caller's book.
- Given the full gate is run, when it completes, then ruff, format, mypy, tsc and eslint are clean, and the server, vitest and Playwright suites pass with the new tests included.

## Spec Change Log

- 2026-08-17 (implementation): **`ActionKey` and `ActionCommand` joined the two enums the Code Map named**, in `data/models/enums.py`, and the reason for all four being there is the rules tier rather than a column — see the Code Map note.
- 2026-08-17 (implementation): **`CaseHeader` gained `status` and `osha_logged`.** AC 4 requires the case-file header to show the new status without a manual refresh, and the payload carried only `stage` — the claim's assessment status had no surface before this story wrote it.
- 2026-08-17 (implementation): **The eleventh rule is `routine_review` and it is the padding rule**, so the eleven urgency keys are ten triggers plus one. The three padding rows share that key and are distinguished by target, which is also what makes each action's `id` (`{key}:{target}`) unique.
- 2026-08-17 (implementation): **`modified_duty` targets `meetings`.** The story's task list names four disabled controls (diary, meetings, fraud, RTW letter) and the eleven rules only reach three of them; giving the modified-duty row a "Schedule the modified-duty review with the plant" meeting target makes the fourth seam real rather than leaving an enum member no rule uses.
- 2026-08-17 (implementation): **The payment-confirmation action deep-links to the Bills tab rather than carrying Story 3.4's ✓.** Recorded in `deferred-work.md` with what carrying it would cost.
- 2026-08-17 (implementation): **`GET /claims/{id}/actions` does not refresh the payment schedule**, deliberately — a third write-on-read path was the alternative. Recorded in `deferred-work.md`.

## Review Triage Log

### 2026-08-17 — Review pass

- intent_gap: 0
- bad_spec: 0
- patch: 10: (high 0, medium 4, low 6)
- defer: 2: (high 0, medium 0, low 2)
- reject: 1: (high 0, medium 0, low 1)
- addressed_findings:
  - `[medium]` `[patch]` The `approve` and `overview` targets rendered an *enabled* go-to button whose `onNavigate` fell through `ClaimDetailPane.navigate` to nothing — two near-identical buttons on every `initial` claim, one of which worked. `ActionsCard` now states the set of targets that lead somewhere from the Overview tab it is mounted in; disabled seam controls still render, because a control that says why it is refused is not a dead click.
  - `[medium]` `[patch]` All three checklist mutations installed the fresh entity on a 409 but never invalidated `queryKeys.claims.actions` — the same defect fixed in `useApprovePayment.onError` earlier the same day. The header chip flipped while the row that caused the refusal stayed on screen, and a document row kept a superseded `documentVersion` and refused every subsequent click until `staleTime` lapsed.
  - `[medium]` `[patch]` `_overdue_rtw` read only `actual_rtw is None`, so seeded claims WC-21122 and WC-21683 — `returned_and_under_therapy` with a stale records gap — carried a `high` "chase the return to work" row beside `modified_duty`'s "the worker is back on restricted capacity". The rule now gates on `return_status`, which is the condition its own docstring always claimed. Consequence absorbed deliberately: `overdue_rtw` and `modified_duty` are now mutually exclusive, so `everything_fires` fires nine of ten and the premise test asserts the union across both return-status worlds.
  - `[medium]` `[patch]` A refusal from one command survived a later success from another: nothing called `reset()`, so a failed approval stayed on screen under a successful OSHA entry. `complete()` now resets all three first.
  - `[low]` `[patch]` The `role="status"` region concatenated three sticky `isSuccess` ternaries with no separator, re-announcing earlier completions. Now one sentence, guaranteed single by the reset above.
  - `[low]` `[patch]` `busy` drove the *label* as well as the disabled state, so editing the recovery window put "Approving…", "Saving…" and "Confirming…" on commands nobody had started. Split into `busy` (disables) and `pending` (relabels), matching `LineItemDialog`.
  - `[low]` `[patch]` Padding rows were appended after the sort and never re-ranked, so ordering was correct only while `urgencyRoutineReview` was the lowest urgency in play — a retune the story advertises as deploy-free would have put padded rows above triggers and broken Story 5.4's element 0. The list is ranked again after padding; a no-op on today's parameters.
  - `[low]` `[patch]` `BILL_REVIEW_STATUSES` restated `APPROVABLE_LINE_ITEM_STATUSES` instead of importing it, in a module that imports `APPROVABLE_ASSESSMENT_STATUSES` from its command for exactly that reason. Now imported.
  - `[low]` `[patch]` The module docstring of `services/claims/assessment.py` promised that re-approving an already-approved claim answers 200, which the command deliberately does not do. Corrected the docstring rather than the behaviour: a completion records a fact and may be re-asserted, a transition whose precondition is consumed is a 409 — the policy the function, its unit test and its e2e all already encoded. (An attempt to change the behaviour instead was reverted when it broke both tests, which is what established which of the two was authoritative.)
  - `[low]` `[patch]` Two comments asserted mechanisms that do not exist: the `queryKeys.actions` note claimed the split saves work for an Overview that "has not scrolled to the card" (the card mounts unconditionally), and the e2e claimed the AD-15 database reset is per spec *file* (one `setup` project covers the whole `stories` project; determinism comes from `workers: 1`). Both rewritten to say what the code does.

## Design Notes

**AC 2 says "decision table"; this ships an expression node, deliberately.** All nine committed JDM documents use the same `inputNode → expressionNode → outputNode` shape with constant expressions, and `rules/engine.py` evaluates each document **once per request** with an empty context, memoised on `(key, version, content, context)`. A `decisionTableNode` keyed on a per-claim or per-rule input would be evaluated many times per request, thrash that cache, and have no home in the frozen-parameter-block pattern `parameters.py` is built around. What AD-8 is actually asking for — parameters DB-versioned, effective-dated, and out of Python — is satisfied exactly by the existing shape. The eleven urgencies ship as eleven flat scalar keys (`urgencyBillReview`, `urgencyOverdueRtw`, …) beside `cap` and `paddingFloor`, matching the 14-parameter `derivation_thresholds`. Record the node-type deviation in `deferred-work.md`.

**Values in a JDM expression are ZEN expression strings, not JSON.** A list is written `"['a', 'b']"` with single quotes; writing real JSON there fails at evaluation, not at load.

**Ordering must be total, not just sorted by urgency.** Urgency alone leaves ties, and a tie broken by dict iteration is a list that reorders between releases — which breaks Story 5.4's "top action" silently. Rank on `(urgency_rank, fixed_rule_index)` where the rule index is the declaration order of the eleven rules, and assert stability in a test.

**AC 3 asks for disabled-with-tooltip, which this codebase has no precedent for.** The house style elsewhere is to *replace* an unavailable control with an explanatory sentence (`ApproveControl`'s three branches), and Radix tooltips are used only inside `SlaStrip`, which hosts its own `TooltipProvider`. The AC is explicit and the cross-epic seam is the point, so ship the disabled button plus a tooltip, with a card-local `TooltipProvider` following `SlaStrip`'s precedent. The `disabledReason` sentence comes from the server with the action, so Stories 4.2 and 6.2 flip one field.

**The three new booleans are the only sanctioned schema growth.** `osha_logged`, `document.reviewed` and `document.confirmed` are what make completions entity-backed — the narrative's "worklist and ledgers stay in sync because they read the same status row". Confirm requires reviewed, which is the prototype's own sequencing (`confirmDocument` returns early when `!st.reviewed`) and is enforced server-side as a status guard, not a disabled button.

**Two derivations feeding these rules are demo-grade.** `rtw_blocked` and `payment_due` are hash-bucket flags over `claim_id`, not real signals; the story says consume them as-is and the real definitions stay deferred. Do not "fix" them here — the actions list will look arbitrary in places and that is a known, recorded property of the seed.

**After `db.commit()` you must `db.expire_all()`** (the sessionmaker sets `expire_on_commit=False`), and on the conflict path `db.rollback()` expires everything — read any attribute you still need into a local *before* it, or the re-read raises `MissingGreenlet`. Story 3.4 shipped this as a live bug fix; do not rediscover it.

## Verification

**Commands:**
- `cd lineworker/server && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .` -- expected: clean, all files formatted.
- `cd lineworker/server && .venv/bin/python -m mypy .` -- expected: no issues in 170+ source files.
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker PATH=".venv/bin:$PATH" .venv/bin/python -m pytest -q` -- expected: all pass, 0 skipped, count above 1539. The `lw-test-pg` container is already running; do not let these skip.
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=... alembic check` and a `downgrade 0029` → `upgrade head` round trip -- expected: no drift, round trip clean.
- `cd lineworker/web && npm run generate:api` -- expected: `src/api/schema.d.ts` regenerated and byte-identical on a second run.
- `cd lineworker/web && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/eslint src && ./node_modules/.bin/vitest run` -- expected: clean, 0 eslint errors, all tests pass above 296.
- `cd lineworker/e2e && npx playwright test` against the rebuilt e2e stack -- expected: all pass including the new `@story:3-5` spec; `@smoke` and `@epic:3` sets green.

**Manual checks (if no CLI):**
- Supersede `worklist_actions` with a variant table in a test and confirm the cap changes with no code edit — this is the AD-8 claim, and only an end-to-end parameter swap demonstrates it.

## Auto Run Result

Status: done

**Implemented change.** Story 3.5 closes Epic 3: a deterministic eleven-rule action generator in `services/worklist/actions.py`, its `worklist_actions` rules document (cap, padding floor and eleven urgencies, DB-versioned and effective-dated), the Upcoming Actions card on all four Overview variants with real deep links and disabled-with-tooltip seam controls, and three status-guarded audited commands in `services/claims/assessment.py` (approve assessment, document review→confirm, OSHA log) behind a claim actions endpoint. Completions are entity-backed — three new boolean columns, no action-state table.

**Files changed.** 24 modified, 11 new. Server: the generator, the assessment commands, the rules document and block, migrations 0030 (columns) and 0031 (rule seed), a status-guarded claim CAS and a scoped document CAS in the repository, four routes, four new enums. Web: `useClaimActions` plus three mutation hooks, the actions query key, `ActionsCard`, the four Overview variants and `ClaimDetailPane`'s navigate callback, label and tone maps, the `noDerivation` registrations. Plus `e2e/stories/3-5-…spec.ts` and three new server test modules.

**Review findings.** 10 patched (4 medium, 6 low), 2 deferred, 1 rejected. No intent gaps and no spec defects — every finding was a localized fault the spec had already forbidden, so no re-derivation loop was needed. Details in the Review Triage Log above.

**Verification performed** (all re-run by the orchestrator after patching, not merely reported): ruff check and format clean, mypy clean across 175 files; **pytest 1646 passed, 0 skipped** against the real `lw-test-pg` Postgres (baseline 1539); `alembic check` no drift and a `downgrade 0029` → `upgrade head` round trip clean; `npm run generate:api` byte-identical on re-run; tsc clean, eslint 0 errors (8 pre-existing warnings), **vitest 314 passed** (baseline 296); **Playwright 123/123** against an e2e stack rebuilt from the patched source — the first run was against pre-patch images and was discarded.

**Residual risks.** Two triggers ride demo hash-bucket derivations, so some rows are arbitrary on the seeded portfolio (recorded, story-sanctioned). The rules document is an `expressionNode` rather than a `decisionTableNode`, matching all nine existing documents and the engine's once-per-request model — the spec predicted this and it is on the deferred list. A newer API sending an unknown enum member would crash the pane, which is the console-wide label-map convention rather than this story's defect.
