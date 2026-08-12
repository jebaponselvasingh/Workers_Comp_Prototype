---
baseline_commit: b6151e02ea196d6f9b4c551a261d1d827dabfa85
---

# Story 2.2: Case Header & Stage-Adaptive Overview

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want the case file to open on a stage-appropriate overview,
so that I see what matters for where the claim is in its lifecycle.

## Acceptance Criteria

1. **Given** a selected claim, **when** the detail pane renders, **then** the header shows worker name, meta line (claimId · role · employer · state), injury summary, badge row (stage pill + conditional Fraud Score / Litigation / Surgery / OSHA), and the semicircular risk-gauge SVG colored by risk band (UX-DR4).
2. **Given** the 6-tab bar (UX-DR5), **when** Overview renders, **then** it always begins with the 4-step stage stepper marking done/current steps (FR-DET-1).
3. **Given** each lifecycle stage, **when** Overview renders, **then** content adapts — intake: summary + reported injury + document checklist (Received/Missing per required form) + timeline; investigation: injury card + financials/reserve card with cost bar + timeline; treatment: phase banner (Early/Active/Approaching-MMI) + paid-vs-reserve card with Bills jump-link + care & RTW coordination status + recent timeline; settled: settled banner + final payout breakdown + outcome card + full action summary (FR-DET-1).
4. **And** `timeline_event` and `document` tables are created by this story's migration and seeded from prototype data.
5. **Given** treatment-phase and coordination-status values, **when** displayed, **then** each is server-derived by a registered derivation (AD-10), never computed in the SPA.

## Tasks / Subtasks

- [x] Task 1: Migration — `timeline_event` + `document` tables, seeded (AC: 4)
  - [x] Alembic migration creating `timeline_event` (surrogate int PK, `claim_id` FK, `event_date` (ISO date), `description`, `tag`; **append-only** — no `version` column, exempt from CAS per the Write-concurrency convention) and `document` (surrogate int PK, `claim_id` FK, `name`, `doc_type` snake_case enum covering the prototype's FROI/INCIDENT/MEDAUTH/WAGE/RTW/LEGAL set, `filed_date`, `blob_key` nullable — bytes ride the `BlobStore` protocol when real files exist, viewer arrives 2.5)
  - [x] Seed both from each prototype claim's `timeline` (`{date, desc, tag}`) and `documents` (`{name, type, date}`) arrays — DATA source only, never code [Source: docs/Workers_Comp_Prototype.html ALL_CLAIMS line 632]
  - [x] Snake_case naming per the Excel canonical mapping; migration runs clean on a fresh DB (CI gate)
- [x] Task 2: Registered derivations — treatment phase + coordination status (AC: 3, 5)
  - [x] `services/derivations.treatment_phase(claim)` → `early | active | approaching_mmi` (+ server-provided note text): port the prototype rule — expected-days from the recovery window (upper bound × 7; ≥1-year → 180; default 42), phase by `days_open / expected_days` at < 0.3 / < 0.7 / else — with the 0.3/0.7 boundaries and day constants as JDM parameters (AD-8) [Source: docs/Workers_Comp_Prototype.html lines 1310–1318]
  - [x] `services/derivations.coordination_status(claim)` → `coordination_gap | awaiting_information | legal_coordination | on_track` (+ note): rtw_blocked → gap; comm-status matches "Need for Additional Information / Incomplete Information" → awaiting; litigation_flag → legal; else on-track [Source: docs/Workers_Comp_Prototype.html lines 1319–1324]
  - [x] Both registered in `services/derivations` (AD-10) and returned in the API payload; unit tests over all branches
- [x] Task 3: Claim-detail API (AC: 1, 2, 3, 5)
  - [x] `GET /api/claims/{claimBusinessId}` (route by `WC-nnnn` per ID conventions) behind the AD-7 scope context — 404/403-safe for out-of-scope IDs (do not leak existence)
  - [x] Payload: header block (name, role, employer, state, injury summary line, stage, conditional fraud-score/litigation/surgery/OSHA badge data, risk band + `version` for later CAS stories), stage-variant overview block (per-stage fields incl. derived phase/coordination values and their notes), timeline events (settled/intake/investigation: full; treatment: server marks the recent-6 slice), document checklist (see Task 5), and money fields as integer cents (format in UI only)
  - [x] Regenerate the OpenAPI client; register `claimDetail` keys in `queryKeys` (AD-9)
- [x] Task 4: Case header + tab shell — `web/src/features/claim-detail/` (AC: 1, 2)
  - [x] `CaseHeader`: h1 worker name; meta line `claimId · role · employer · state` (mono claim ID); 🩹 injury summary line (injury type — body part, cause · ICD-10 · severity); badge row: stage pill + conditional `⚠ Fraud Score: n` / `⚖ Litigation` / `🔪 Surgery` / `OSHA Rec.` badges
  - [x] `RiskGauge`: port the prototype's semicircular SVG as a typed React component — 100×56 viewBox, background arc + risk-colored arc (`stroke-dasharray` 115/75/35 over 132 for high/med/low), centered risk label, er/wn/ok token colors [Source: docs/Workers_Comp_Prototype.html line 1187]
  - [x] Tab bar with the 6 tabs: Overview · Injury Diagram · Bills & Payments · Documents & ID · Photos · AI Insights; tab state is local UI state; **cross-story seams render explicit empty states** — Bills & Payments ("Financial detail arrives with the financial engine") until Epic 3, AI Insights until Epic 6, Injury Diagram until 2.4, Documents & ID until 2.5, Photos until 2.6 (no count suffix in the label until 2.6 adds the `photo` table) (NFR-3)
  - [x] `StageStepper`: 4 steps Intake → Investigation → Treatment → Settled with done/current styling; always the first element of the Overview tab [Source: docs/Workers_Comp_Prototype.html lines 1201–1205]
- [x] Task 5: Four stage-variant Overview components (AC: 3)
  - [x] `IntakeOverview`: intake-summary card (employee, role/plant, DOI, FROI filed, assigned, handler, communication status) + reported-injury card (injury type, cause, body part, initial severity, AWW, initial reserve) + **intake document checklist**: Received/Missing row per required document, computed server-side by comparing the claim's `document` rows against a service-owned required-document baseline (the prototype's doc-type set FROI/INCIDENT/MEDAUTH/WAGE as a JDM-parameterized list — see Dev Notes seam: `path_required_form` reference data does not exist until 2.5 and is a different surface) + full timeline card
  - [x] `InvestigationOverview`: editable-injury card **rendered read-only this story** (inline editing is 2.3 — display values only, no inputs) + financials/reserve card (total incurred, reserve, policy number, fraud score, severity score, indemnity/medical cost bar with legend when paid > 0, "Active — payments pending" otherwise) + full timeline card
  - [ ] `TreatmentOverview`: phase banner (derived label Early Treatment & Diagnosis / Active Treatment & Therapy / Approaching MMI / RTW Planning, note, day-N-of-claim line) + paid-vs-reserve card **from claim-level financial fields** (paid_medical, paid_indemnity, reserve — bill/schedule tables arrive Epic 3; reserve-check verdict line renders a "verdict arrives with the financial engine" placeholder until 3.2) with "View full Bills & Payments →" jump-link switching to the Bills tab (which shows its Epic-3 empty state) + care & RTW coordination card (derived status label + note, return status, handler, supervisor) + recent timeline (server-marked last 6) — **not fully delivered:** the **supervisor** row is absent because `claim.supervisor` is not persisted (Story 1.2 deliberately did not seed it). See Completion Notes deviation #4; rendering it needs a schema back-fill, which is a change request, not this story.
  - [x] `SettledOverview`: ✅ settled banner (settlement date from the settlement-tagged timeline event, total incurred) + final payout breakdown (indemnity/medical/expense/total/final reserve + 3-segment cost bar) + claim-outcome card (disability, return status, days to settlement, litigation, handler) + full "Summary of actions taken" timeline
  - [x] All figures and derived strings come from the API payload — no TS re-derivation (AD-1/AD-10); loading/error/empty states throughout (NFR-3)
- [x] Task 6: Tests + E2E spec (AC: all)
  - [x] Unit: phase/coordination derivations all branches; checklist Received/Missing logic; timeline slicing
  - [x] Vitest: header badges conditional rendering; gauge band colors; stepper done/current for each stage; one render test per stage variant; seam empty states
  - [x] `e2e/stories/2-2-case-header-stage-adaptive-overview.spec.ts` tagged `@story:2-2 @epic:2`; `@smoke` happy path: select a treatment-stage seeded claim → header + gauge + stepper render → phase banner and coordination card show derived values → Bills jump-link lands on the Bills tab empty state; additional tests select one seeded claim per remaining stage and assert its variant's signature cards + timeline

### Review Findings

*All 16 patch findings applied 2026-08-12; the decision item resolved; the three deferrals recorded in `deferred-work.md`.*

Adversarial code review, 2026-08-12 — three parallel layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor). All five ACs verified met in substance; the items below are the gaps. Severity is the consequence for a handler using the console.

- [x] [Review][Decision] **Task 5 ticks a Supervisor row the card does not render** — *resolved 2026-08-12: subtask un-ticked and annotated; the schema stays as Story 1.2 decided it.* — the `TreatmentOverview` subtask specifies "care & RTW coordination card (… return status, handler, **supervisor**)" and is marked `[x]`, but neither the payload nor the component carries it. The reason is sound (Story 1.2 deliberately did not persist `supervisor`, and the omission is deviation #4 in the Completion Notes) — but the box is ticked for something not delivered. Un-ticked with a pointer to the deviation. [medium]

- [x] [Review][Patch] Five of the six case-file tabs are unreachable by keyboard [lineworker/web/src/features/claim-detail/DetailTabs.tsx:112] — `tabIndex={-1}` on every unselected tab is the roving-tabindex pattern, and no `onKeyDown` handler was written. The comment on the line above promises "the arrow keys a `tablist` implies" and explicitly rejects the accessible fallback. A keyboard user lands on Overview and can reach nothing else. WCAG 2.1.1. [high]
- [x] [Review][Patch] The intake checklist's rule-document version is never published [lineworker/server/api/routers/claims.py:497] — `IntakeRequirements.version` is resolved per request and discarded; the response carries `thresholdsVersion` only, three lines under a docstring asserting "which rules produced this? should be answerable from the response". The queue publishes both of its documents' versions. [medium]
- [x] [Review][Patch] `derivation_thresholds.jdm.json` (v1) lost its byte-for-byte tie to its seeded row [lineworker/server/tests/test_rules_engine.py:58] — `EFFECTIVE_DOCUMENTS` lists only the v2 file, so the v1 document migration 0009 still reads at migration time is now compared against nothing. Editing it would leave existing and fresh databases disagreeing about what v1 said, with every gate green. This is the drift the `<key>.v<N>.jdm.json` convention was introduced to prevent. [medium]
- [x] [Review][Patch] `pytest.skip()` after the assertions discards the test's result [lineworker/server/tests/test_claim_detail.py:332] — the cost-split test asserts the sum-to-100 property over the whole book, then skips if it did not also observe a zero-paid claim. pytest marks the whole test SKIPPED, so a green run reports the property as not run. It survives on exactly one seed row. [medium]
- [x] [Review][Patch] The AD-7 role guard is now evadable by renaming a local [lineworker/server/tests/test_scoped_repository.py:109] — narrowing it to `ctx|context|caller` was right (`Employee.role` is the worker's job title), but binding it to variable *names* means `scope = ctx; if scope.role == …` passes silently. Bind it to the parameter's type, or exclude the known-innocent forms while staying otherwise blunt. [medium]
- [x] [Review][Patch] A rule version that adds *required* parameters cannot be safely future-dated [lineworker/server/data/versions/20260812_0012_case_file_rule_documents.py:62] — `EFFECTIVE_FROM` is 2026-08-12 while migration 0009's v1 is 2026-08-11. Any date before the effective date resolves v1, which `DerivationThresholds.of` then refuses for the four missing keys — a 500 on the queue, `/stats/*` and the case file alike. Not live (the date has arrived), but AD-8 advertises future-dating as a feature and this story is the precedent the next version will copy. [medium]
- [x] [Review][Patch] The phase guard greps the bare word `weeks` across every non-test module [lineworker/server/tests/test_case_file_derivations.py:317] — Story 3.1 is the statutory benefit calculation, where "104 weeks" and waiting periods appear in ordinary prose. The first module that mentions a week will fail CI with "no module outside the derivation restates the phase rule", which is not what happened. The `0.3|0.7` half of the same pattern is precise. [medium]
- [x] [Review][Patch] The recovery-window parser is looser than the prototype it claims to port, and its test says the opposite [lineworker/server/services/derivations/treatment_progress.py:50] — the prototype's `/(\d+)-(\d+)\s*Weeks/` and `/Year/` are case-**sensitive** and do not tolerate spaces around the dash; ours are neither. `test_case_file_derivations.py:85` asserts `("4-6 weeks", 42)  # case-insensitive, like the prototype's regex`, which is false. Harmless until Story 2.3 makes `recovery` editable, then `"6-8 weeks"` gives a different phase here than in the contract file. [low]
- [x] [Review][Patch] The header prints "Med severity" where the prototype prints "Medium" [lineworker/web/src/features/claim-detail/CaseHeader.tsx:84] — the gauge's abbreviation is reused in a prose sentence. 51 of 100 claims are "Medium" in the dataset, and `RISK_DESCRIPTION` already says "Medium risk" for the gauge's accessible name, so the same claim reads two ways to a sighted user and a screen reader. [low]
- [x] [Review][Patch] `_settlement_date` takes the last settlement event by *append* order, which is not chronological [lineworker/server/services/claims/detail.py:296] — eight seeded claims already carry dated events that run backwards in append order. It also discards a known date when a later settlement event is undated. Taking the latest settlement event that *has* a date fixes both and still passes the existing tests. Masked today because all 62 settlement events are undated. [low]
- [x] [Review][Patch] The settled banner's date clause is never exercised [lineworker/web/src/features/claim-detail/overview/SettledOverview.tsx:31] — the only settled fixture hardcodes `settlementDate: null` and the e2e spec asserts `settled-date` has count 0, so the component's own claim that "this sentence gains it without a change" is unverified. One fixture with a non-null date closes it. [low]
- [x] [Review][Patch] `useClaimDetail`'s null handling is unreachable and its docstring describes wiring that does not exist [lineworker/web/src/api/claims.ts:126] — `ClaimDetailPane` branches on `selectedClaimId === null` before mounting the only caller, which is typed `claimId: string`. Harmless until someone trusts the comment. [low]
- [x] [Review][Patch] The stage dispatch falls through to the investigation variant [lineworker/server/services/claims/detail.py:460] — `return InvestigationOverview(...)` is the unconditional else, so a fifth `Stage` member would build an investigation body and then 500 inside `_stepper`'s `index()`. Make investigation explicit and raise on an unknown stage. [low]
- [x] [Review][Patch] A recorded negative `settlement_days` is not floored, unlike the fallback beside it [lineworker/server/services/derivations/open_duration.py:102] — `days_open` floors at zero and says why; the settled path returns the column verbatim, so the outcome card can render a negative "Days to settlement". An inconsistency inside one module. [low]
- [x] [Review][Patch] The seed shape check validates only the first row [lineworker/server/data/versions/20260812_0011_seed_case_file.py:56] — field drift beginning at any later row reaches `executemany`, which compiles its statement from row 0: extra keys are dropped silently or the migration dies mid-chain with a bind-parameter error, which is the failure the check exists to replace. [low]
- [x] [Review][Patch] The DB-test sample-claim rationale is wrong for two of three claims [lineworker/server/tests/test_case_file_seed.py:47] — measured against the committed seed: WC-20017 has **5** timeline events, not "more events than the recent-6 window" (and the story's own Debug Log records that no treatment claim exceeds six); WC-20289 is missing **nothing**, so it is not "the case the checklist exists for" — WC-20017 and WC-20051 are the claims missing `incident`. No assertion breaks, but a reader picking replacement samples from this comment picks wrong. [low]

- [x] [Review][Defer] Migration 0011's downgrade deletes every row in both tables, not the seeded ones [lineworker/server/data/versions/20260812_0011_seed_case_file.py:96] — deferred, pre-existing pattern (0004 and 0007 do the same), but sharper here because `timeline_event` is also the live append log Stories 2.3+ write into.
- [x] [Review][Defer] The settled payout rounds four figures independently, so components need not sum to the printed total [lineworker/web/src/lib/money.ts:22] — deferred, dormant: no seeded claim has non-zero cents. Becomes live with Epic 3's bill lines.
- [x] [Review][Defer] Enum lookups in the treatment card have no runtime fallback for a value the deployed bundle predates [lineworker/web/src/features/claim-detail/overview/TreatmentOverview.tsx:52] — deferred, reachable only under deploy skew (new server, cached old JS).

**Dismissed as unreachable (5):** a checklist keyed on `blob_key` (a row means the document is on file; `blob_key` is where bytes live and is NULL on every seeded row by design); negative paid-cents producing out-of-range percentages and the matching CSS-width clamp (verified: the remainder arithmetic cannot leave 0–100 for non-negative inputs, and no such data exists); the extractor's bare `ValueError` on a non-numeric slashed token (one-off dev tool, aborts either way); and `InvestigationOverview` keying its unpaid branch on `costSplit === null` (that *is* the server's signal — null exactly when nothing is paid — so the described payload cannot occur).

## Dev Notes

### What this story is — and is not

This story turns Story 2.1's selection into a real case file: header, gauge, 6-tab shell, stepper, and the four Overview variants, plus the `timeline_event`/`document` tables. It contains **no editing** — 2.3 adds audited inline edits (render the investigation injury card read-only), 2.4 the injury diagram and severity editing, 2.5 the documents/forms tab content and viewers, 2.6 photos. **No financial engine**: benefit card, reserve-check verdict, bills, payment schedule, and the Upcoming Actions block are Epic 3 — the prototype's overview builders embed `benefitCardHTML`/`actionsBlockHTML`; those regions are omitted (or given explicit "arrives with the financial engine" placeholders) this story, and Epic 3 slots them into these same variant components. AI Insights is Epic 6. Readiness report flags this story DENSE (four variants) — the task decomposition above is deliberately fine-grained; keep each variant a separate component and commit.

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### Architecture compliance (binding ADs for this story)

- **AD-1:** every figure and derived string in the pane comes from the detail endpoint; the SPA renders payloads.
- **AD-7:** detail endpoint enforces repository-layer scope; an out-of-scope `WC-nnnn` must not resolve.
- **AD-8:** phase boundaries (0.3/0.7), expected-days constants, and the intake required-document list are JDM parameters; the branching logic is typed Python.
- **AD-9:** `claimDetail` in the shared `queryKeys` module; tab selection is local UI state; read-only story — no optimistic updates.
- **AD-10:** `treatment_phase` and `coordination_status` are registered derivations; risk band shown by the gauge is the same `risk` derivation 2.1 registered — queue dot and gauge can never disagree.
- **AD-12:** `timeline_event` rows are emitted only by owning-service commands; this story only **seeds** (migration) and **reads** — the first runtime emitter is 2.3's edit command. `document` writes belong to `services/claims` (Capability Map: stage-adaptive claim detail).
- **AD-15:** story spec is the done-gate (see Testing requirements).

### Data notes

- Creates + seeds: `timeline_event` (append-only, CAS-exempt), `document` — seeded from each ALL_CLAIMS entry's `timeline`/`documents` arrays; ~100 claims' worth. Prototype data is a DATA source only.
- Uses: `claim` (incl. paid_indemnity/paid_medical/paid_expense/reserve/aww in integer cents from 1.2), `employer`, scope tables.
- Write-owner (AD-12): both new tables belong to `services/claims`.
- **Seam — intake document checklist:** epics.md AC says "Received/Missing per required form", but `path_required_form` reference data is created in **2.5** (a later story; forward dependencies are banned) and feeds a *different* surface (the statutory-forms card on the Documents & ID tab). Resolution: the intake checklist compares `document` rows against a JDM-parameterized required-document baseline owned by `services/claims`. This checklist also has **no prototype precedent** — the prototype's intake overview has no checklist — so the epics AC is the contract for it. Both points are flagged as discrepancies in this batch's creation report.
- **Seam — treatment paid-vs-reserve card:** prototype computes it from `buildBills`/`buildPaymentSchedule`; those tables arrive 3.3. Render from claim-level paid/reserve columns now; 3.3 upgrades the source and 3.2 fills the verdict line. Figures must match Epic 3's later surfaces because both read the same claim columns via the same derivations (AD-10).

### UX notes

- UX-DR4 (case header + risk gauge), UX-DR5 (6-tab pane + stage-adaptive variants + 4-step stepper) govern this story; UX-DR3's center-pane placement follows 2.1's layout.
- UX-DR12 + Story 1.1 ruling: prototype palette is **LIGHT and canonical**; "dark console aesthetic" in epics.md is a documented discrepancy — do not build a dark theme. Stage pills, badges, cost bars, and the gauge use the ok/wn/er/st/ac tokens from 1.1.
- NFR-3: loading skeleton for the pane, error state, and explicit empty states on every not-yet-built tab; timeline card shows a "No timeline events on file." empty state.
- Visual reference for every card: prototype builders `intakeOverviewHTML` (1325), `investigationOverviewHTML` (1282), `treatmentOverviewHTML` (1348), `settledOverviewHTML` (1381) — replicate layout/feel with shadcn/ui + tokens, not their code.

### Testing requirements

- Unit tests the ACs demand: `treatment_phase` (all three phases + recovery-window parsing edge cases: no match, "Greater than 1 Year"), `coordination_status` (all four branches, precedence order), checklist derivation (received, missing, mixed), timeline recent-6 slicing.
- Property (Hypothesis): `treatment_phase` total over the days-open × recovery-window domain (never throws, always one of three values).
- Scope test: out-of-scope claim detail request fails safely.
- E2E (AD-15): `e2e/stories/2-2-case-header-stage-adaptive-overview.spec.ts` tagged `@story:2-2 @epic:2`, one `@smoke` happy path, per-spec DB reset. **The story cannot move to `review`/`done` until this spec passes.**

### Project Structure Notes

- Server: migration in `server/data/` versions; derivations in `server/services/derivations/`; detail assembly in `server/services/claims/`; router in `server/api/`.
- Web: `web/src/features/claim-detail/` — `CaseHeader.tsx`, `RiskGauge.tsx`, `StageStepper.tsx`, `DetailTabs.tsx`, `overview/{IntakeOverview,InvestigationOverview,TreatmentOverview,SettledOverview}.tsx`.
- Conventions: business-ID routing (`/api/claims/WC-1234`), camelCase JSON, integer cents formatted only in UI, snake_case enums with UI labels.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 2.2]
- Epic 2 seams (Bills → Epic 3, AI Insights → Epic 6): [Source: _bmad-output/planning-artifacts/epics.md#Epic 2]
- AD-1 / AD-7 / AD-8 / AD-9 / AD-10 / AD-12 / AD-15: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- ERD (`timeline_event`, `document` under CLAIM): [Source: ARCHITECTURE-SPINE.md#Core entity ERD]
- ID/dates/money/enum conventions: [Source: ARCHITECTURE-SPINE.md#Consistency Conventions]
- Prototype design contract: [Source: docs/Workers_Comp_Prototype.html — renderDet/gauge line 1183–1199, stepper 1201, ovHTML dispatch 1275, stage builders 1282/1325/1348/1381, treatmentPhase 1310, coordinationCheck 1319, ALL_CLAIMS 632]
- DENSE-story advisory: [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Story Sizing Assessment / #Standing Advisories item 4]
- Design-token light-palette ruling: [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]

## Dev Agent Record

### Agent Model Used

claude-opus-5[1m] (Claude Opus 5, 1M context), via the `bmad-dev-story` workflow.

### Debug Log References

- **The prototype's timeline and document dates are display strings in five shapes, and two of them are not dates.** `MM/DD/YYYY` on documents, `MM/DD` on every timeline event after the first, `24/MM/DD` on the first (the leading component is the literal string `24` on all 100 claims, and `MM/DD` equals the DOI exactly — a stale year token from before the dataset was shifted to 2026), and `Closed` / `Post-surgery` on 163 rows where a date belongs. `extract_case_file_seed.py` documents the whole parse and *asserts* the two assumptions (the prefix is `24`; the month/day match the DOI) so a data change aborts extraction instead of mis-dating a hundred claims. WC-20969 carries the `2026-02-29` quirk Story 1.2 found, in three more rows; the same forward-roll house rule applies.
- **No treatment claim in the portfolio has more than six timeline events**, so the recent-6 slice — and therefore `timelineTruncated` — never fires against seeded data. A claim still in treatment has not accumulated the RTW and settlement entries that make settled timelines seven. The prototype's own `.slice(-6)` never fired either. Covered by `test_claim_detail_assembly.py` over a synthetic twelve-event claim; recorded in `deferred-work.md`.
- **`alembic check` fails on an index declared in a migration but not on the model.** The two `claim_id` indexes were written in 0010 first; the models needed `index=True` to match. Worth knowing because the failure message names a *removed* index, which reads backwards.
- **`op.drop_table` leaves a native enum behind.** 0010's `downgrade()` has to drop `doc_type` explicitly or a re-upgrade fails on "type already exists". 0003's enums escape this because 0002's `DROP OWNED BY` sweeps them up; a table created after that migration has no such umbrella.
- **Two structural guards from earlier stories fired, and both were worth fixing rather than allowlisting.** (1) `test_no_repository_branches_on_role` greps the repository source for `".role"`, which matched `Employee.role` — the injured *worker's job title*, a column the case header has to select. Narrowed to reads of the caller context's role (`ctx|context|caller`.`role`, plus `UserRole`), with smell tests both ways. (2) The AD-2 SLA guard flagged `services/claims/detail.py` for reading `claim.settlement_days`. That is a real hit but not the failure the rule is about (a second *aggregation*), so the fallback became a registered `days_to_settlement` derivation taking the claim through a Protocol — the guard now allowlists one module instead of one per consuming screen.
- **`noDerivation.test.ts` fired on JSX text.** The stripper removes comments and string literals, but JSX *text* is neither, so `>Claim risk</span>` read as the field `risk` followed by `<`. Fixed by excluding `/` and `>` as the right-hand side of a comparison — a real comparison never has one — rather than rewording the heading, which would have been the guard training the code. Smell tests added in both directions.
- The `derivation_thresholds` document is now at **v2**; `test_claims_queue.py`'s two `thresholdsVersion == 1` assertions moved to 2. That is the AD-8 mechanism working rather than a regression: retuning a threshold re-ranks the queue, so the payload has to say which version answered, and outstanding cursors are refused.

### Completion Notes List

- **AC 4 — the two tables, and the date ruling behind them.** Migration 0010 creates `timeline_event` (append-only, no `version`, CAS-exempt) and `document` (`version` present — it is a mutable entity by AD-4's convention even though no story writes one yet); 0011 seeds 556 events and 563 documents from `data/seed/case_file_seed.json`. Insertion order is the contract: the log has no total order in its own columns (dates repeat, 62 are null), so display order is append order, which is identity order, and the treatment overview's *last six* would silently become a different six if it drifted. `tests/test_case_file_seed_file.py` ties the committed JSON back to the prototype with an independently written transformation, so the other four readers cannot all agree with the same wrong file.
- **`tag` is `Text`, not a native enum — the one deviation from the snake_case-enum convention.** `timeline_event` is a log every later epic appends to (2.3's edits, Epic 3's approvals, Epic 4's meetings, Epic 6's letters), and a database enum would make each of those carry an `ALTER TYPE … ADD VALUE` whose only purpose is to widen a display label. `TimelineTag` in Python gives the one rule that branches on a tag (the settled banner) its typing, and a DB-backed test asserts the seeded set is exactly that enum. `doc_type` **is** a native enum: that set is closed by what a claim file is, and Story 2.5 maps every member to a chip and a viewer.
- **AC 5 — two registered derivations, and two more the case file needed.** `treatment_phase` (early / active / approaching_mmi + note + expected days) and `coordination_status` (gap / awaiting / legal / on-track + note) are in the AD-10 registry with their parameters in `derivation_thresholds` v2. Building the payload also surfaced two derived values with no owner: `total_paid` + `cost_split` (`services/derivations/claim_money.py`) and `days_to_settlement`. All four are registered rather than inlined, because each is a figure Epic 5 and Epic 7 will show again.
- **The precedence order *is* the coordination rule.** A claim can satisfy three of the four conditions at once, and first-match-wins is ordered by what a handler must act on first. Independent booleans would let the card show "Legal Coordination" on a claim whose recommended return date lapsed a month ago. Pinned by its own parametrised test.
- **The phase boundaries are exclusive-below**, matching the prototype's `<`: a claim exactly at 0.3 of its window is `active`. Asserted against a 100-day window so the ratios are exact and the test is about the comparison rather than floating-point luck.
- **AC 1/2/3 — `GET /api/claims/{claimBusinessId}`.** A **discriminated union** on `stageVariant`, not four optional blocks: the alternative shape can describe a claim as simultaneously in intake and settled, and leaves the client with four truthiness checks where the prototype's `ovHTML` has one dispatch. The generated TypeScript narrows on `overview.stageVariant === "intake"` with no casts. The stepper arrives already marked (`done`/`current` per step) for the same reason — "which steps are behind this claim" is a statement about its lifecycle, and deciding it client-side would be a second place that knows what follows investigation.
- **AD-7 — a 404 for out of scope, identical to a 404 for a claim that does not exist.** A 403 would confirm the claim exists, which turns the route into an oracle a caller can walk `WC-20000`…`WC-20999` through to enumerate a portfolio they cannot read. The repository returns nothing for both cases, so the route has one branch and the leak cannot be written back in. Asserted at three levels: the repository, the endpoint (`title` and `type` compared between the two cases), and the browser.
- **Money is integer cents and every wire field says so** — `reserveCents`, `paidMedicalCents`, `totalPaidCents`. The suffix is not decoration: the convention is cents end to end formatted only in the UI, and a bare `reserve` reads like dollars at every call site. `web/src/lib/money.ts` is the only place a hundred is divided by, in `lib/` because Epic 3, 5 and 7 will all format the same amounts.
- **The cost bar's three shares sum to exactly 100.** The prototype rounds each independently with `toFixed(0)`, which totals 99 on thirds and 101 on other splits — under- and overflowing the track it is drawn in. Two are rounded and the third takes the remainder; a Hypothesis property covers the whole domain.
- **Story 2.1's presence machinery is gone, deliberately.** Its centre pane answered "is this claim in my caseload?" from the queue payload it happened to hold, which needed three states (present / absent / unconfirmed) because a filtered or partially-paged queue cannot rule a claim out. The detail endpoint answers authoritatively, so `presenceOf`, `DetailPlaceholder` and `useLoadedClaimIds` were removed: keeping both would be two answers to one question, and the retired one is the one that guessed. Every 2.1 assertion that rested on them was **re-pointed, not deleted** (1.5's and 1.6's precedent) — each now asserts the stronger version of what it was really guaranteeing, e.g. "a queue that failed to load no longer affects the case file" rather than "reports the claim as unchecked".
- **Five tabs ship as explicit empty states naming their story** (Injury Diagram → 2.4, Bills → Epic 3, Documents → 2.5, Photos → 2.6, AI Insights → Epic 6), written as data rather than five JSX branches so the seam is a list a reviewer can read against the epic. No `(n)` on the Photos label: the `photo` table arrives with 2.6, and a hardcoded `(0)` would be a claim about the data.
- **Four documented deviations from the prototype**, each because reproducing it would have meant putting an unversioned rule in the browser or printing a non-date:
  1. **The settled banner omits the settlement date.** Every seeded settlement event carries the literal string `Closed` where a date belongs, so `settlementDate` is null and the clause is dropped. The prototype renders "reached final settlement on **Closed**".
  2. **The fraud score is uncoloured.** The prototype tints it at 55 and 35, two cut-offs in no rule document. Deferred to a `fraud_band` derivation (Epic 7 wants the same bands).
  3. **The settlement event is found by tag equality, not by `/settl|clos/i` over the tag *or description*.** The prototype's regex also matches "closure planning underway", which would date the banner from an event about planning.
  4. **The Supervisor row and the MMI estimate are omitted** from the treatment coordination card and phase banner. Neither is persisted — Story 1.2 deliberately did not seed `supervisor`, and `prognosis` is Story 2.4's — and a row that always reads "—" is furniture, not honesty.
- **One naming discrepancy preserved and recorded**: the prototype labels `paidIndemnity + paidMedical + paidExpense` "Total incurred", where insurance practice means paid **plus reserve**. The figure is ported exactly (the prototype is the design contract and it is what stakeholders reviewed); the derivation is named `total_paid` for what it actually adds up, and changing the number is flagged in `deferred-work.md` as a product decision.
- **The intake checklist has no prototype precedent** — the prototype's intake overview has no checklist — so the epics' AC is its contract. The required set is a JDM parameter in a new `intake_required_documents` document rather than four more entries in `derivation_thresholds`: the checklist is owned by `services/claims`, and the thresholds block is the single argument every *derivation* is built from. Established the versioned-filename convention along the way (`<key>.jdm.json` is v1, `<key>.v<N>.jdm.json` supersedes) because 0009 reads the unversioned name at migration time, so editing it would rewrite v1's content on a fresh database.
- **Tests:** 511 server (66 new — the seeded tables and their grants, the prototype tie-back, both new derivations over every branch plus three Hypothesis properties, the parameter-block refusals, the assembly rules over inputs the seed does not contain, and the endpoint's contract, scope and per-variant payloads), 148 vitest (36 new — the four NFR-3 states, the header and its conditional badges both ways, the gauge per band, the stepper per stage, one render test per variant, the checklist, the five seam panels, the Bills jump-link and the tab reset), 61 Playwright (10 new, `@story:2-2 @epic:2`, one `@smoke`). `noDerivation.test.ts` now walks `features/claim-detail/` too, and its DERIVED_FIELDS list gained this story's eight.
- **Verified live.** e2e stack (`compose.e2e.yaml`, :8081) rebuilt from clean: full suite **61/61**, the 9-test `@smoke` set, and the 22-test `@epic:2` close-out all green against a freshly reset stack, torn down with `down -v` afterwards. All four stage variants were screenshotted at 1280×720 and compared against the prototype's `intakeOverviewHTML` / `investigationOverviewHTML` / `treatmentOverviewHTML` / `settledOverviewHTML`. `alembic check` reports no drift; an explicit `downgrade 0009` → `upgrade head` round trip completes clean. `npm run generate:api` leaves `src/api/schema.d.ts` byte-identical on a second run.
- **For the repo admin:** the migrations CI job now also runs `tests/test_case_file_seed.py` and `tests/test_claim_detail.py` (DB-backed; they would otherwise skip in the server job). Story 2.1's warning about that hand-maintained list is now restated in the workflow itself. No new runtime or dev dependency was added by this story.

### File List

**New — server**

- `lineworker/server/data/versions/20260812_0010_case_file_tables.py`
- `lineworker/server/data/versions/20260812_0011_seed_case_file.py`
- `lineworker/server/data/versions/20260812_0012_case_file_rule_documents.py`
- `lineworker/server/data/seed/extract_case_file_seed.py`
- `lineworker/server/data/seed/case_file_seed.json` (generated, committed)
- `lineworker/server/rules/documents/derivation_thresholds.v2.jdm.json`
- `lineworker/server/rules/documents/intake_required_documents.jdm.json`
- `lineworker/server/services/claims/detail.py`
- `lineworker/server/services/derivations/treatment_progress.py`
- `lineworker/server/services/derivations/care_coordination.py`
- `lineworker/server/services/derivations/claim_money.py`
- `lineworker/server/tests/test_case_file_seed.py`
- `lineworker/server/tests/test_case_file_seed_file.py`
- `lineworker/server/tests/test_case_file_derivations.py`
- `lineworker/server/tests/test_claim_detail.py`
- `lineworker/server/tests/test_claim_detail_assembly.py`

**New — web / e2e**

- `lineworker/web/src/lib/money.ts`
- `lineworker/web/src/features/claim-detail/ClaimDetailPane.tsx`
- `lineworker/web/src/features/claim-detail/ClaimDetailPane.test.tsx`
- `lineworker/web/src/features/claim-detail/CaseHeader.tsx`
- `lineworker/web/src/features/claim-detail/RiskGauge.tsx`
- `lineworker/web/src/features/claim-detail/StageStepper.tsx`
- `lineworker/web/src/features/claim-detail/DetailTabs.tsx`
- `lineworker/web/src/features/claim-detail/Cards.tsx`
- `lineworker/web/src/features/claim-detail/labels.ts`
- `lineworker/web/src/features/claim-detail/overview/IntakeOverview.tsx`
- `lineworker/web/src/features/claim-detail/overview/InvestigationOverview.tsx`
- `lineworker/web/src/features/claim-detail/overview/TreatmentOverview.tsx`
- `lineworker/web/src/features/claim-detail/overview/SettledOverview.tsx`
- `lineworker/e2e/stories/2-2-case-header-stage-adaptive-overview.spec.ts`

**Modified — server**

- `lineworker/server/data/models/core.py` (`TimelineEvent`, `Document`), `lineworker/server/data/models/enums.py` (`DocType`, `TimelineTag`), `lineworker/server/data/models/__init__.py` (exports)
- `lineworker/server/data/repositories/claims.py` (`select_claim_detail`, `select_timeline_events`, `select_documents`)
- `lineworker/server/rules/parameters.py` (four thresholds on `DerivationThresholds` + their range checks; `IntakeRequirements` and `_doc_type_list`; `intake_requirements_for`)
- `lineworker/server/services/derivations/__init__.py` (registers the four new derivations), `lineworker/server/services/derivations/open_duration.py` (`days_to_settlement`)
- `lineworker/server/api/routers/claims.py` (`GET /claims/{claim_business_id}` + its response models)
- `lineworker/server/tests/seed_fixture.py` (case-file oracles), `tests/test_rules_engine.py` (per-key effective version + authoring file), `tests/test_rule_parameters.py` (the new refusals), `tests/test_derivations.py` (v2 block), `tests/test_claims_queue.py` (thresholds version 1 → 2), `tests/test_scoped_repository.py` (role guard narrowed + smell tests), `tests/test_sla_aggregation.py` (allowlist entry with its argument)

**Modified — web / e2e**

- `lineworker/web/src/api/claims.ts` (`useClaimDetail` + the case-file types; `useLoadedClaimIds` removed), `src/api/queryKeys.ts` (`claims.detail`), `src/api/errors.ts` (`isNotFound`), `src/api/schema.d.ts` (regenerated)
- `lineworker/web/src/features/shell/WorkspaceShell.tsx` (renders `ClaimDetailPane`; presence machinery removed), `src/features/shell/WorkspaceShell.test.tsx` (2.1's assertions re-pointed)
- `lineworker/web/src/features/queue/noDerivation.test.ts` (scans `features/claim-detail/`; DERIVED_FIELDS extended; JSX-text false positive fixed)
- `lineworker/web/src/test/api-mock.ts` (`claimDetail` route + seven case-file fixtures)
- `lineworker/e2e/fixtures/seed.ts` (case-file oracles), `lineworker/e2e/stories/2-1-prioritized-filterable-claim-queue.spec.ts` (seam assertions re-pointed)

**Modified — repo**

- `.github/workflows/ci.yaml` (two new DB-backed test files in the migrations job)
- `lineworker/README.md` (`rules/` is no longer "arrives Epic 2/3")
- `_bmad-output/implementation-artifacts/deferred-work.md` (four items), `_bmad-output/implementation-artifacts/2-2-case-header-stage-adaptive-overview.md` (this file), `_bmad-output/implementation-artifacts/sprint-status.yaml`

### Change Log

- 2026-08-12: Story 2.2 implemented end to end — `timeline_event` + `document` tables created and seeded (556 events, 563 documents) from a new prototype extractor; `treatment_phase`, `coordination_status`, `total_paid`, `cost_split` and `days_to_settlement` registered as derivations with their parameters in `derivation_thresholds` v2 and a new `intake_required_documents` document; `GET /api/claims/{claimBusinessId}` assembling a header, a marked stepper and one discriminated stage variant under AD-7 scope; `web/src/features/claim-detail/` rendering all of it with five seam tabs and NFR-3 states throughout. Story 2.1's centre-pane presence machinery retired in favour of the authoritative endpoint, with its assertions re-pointed. Full gate green: ruff + format + mypy clean, pytest 511, vitest 148, Playwright 61/61 plus `@smoke` and the `@epic:2` close-out. Status → review.
- 2026-08-12: Addressed code review findings — 1 decision resolved, **16 patches applied**, 3 deferred, 5 dismissed as unreachable. The standout was a keyboard trap: `tabIndex={-1}` on the five unselected tabs with no arrow-key handler, under a comment promising the behaviour, made five of six tabs mouse-only (WCAG 2.1.1); `useTabKeys` now implements Arrow/Home/End with focus following selection, covered by three vitest tests and one browser-level Playwright test. Three others were guards that looked like coverage and were not: v1 of `derivation_thresholds` had lost its byte-for-byte tie to its committed file in the very change that introduced the versioned-filename convention; a `pytest.skip()` placed *after* the assertions reported a passing property as not-run; and the AD-7 role guard, narrowed to three identifier names, was evadable by `scope = ctx`. Also: a rule version that adds *required* parameters can no longer be future-dated (v2 now shares v1's effective date, closing a window in which the whole console 500s); the intake checklist's `requirementsVersion` is published; `_settlement_date` takes the latest *dated* settlement event rather than the last appended; the recovery-window parser's deliberate widening beyond the prototype is now documented rather than mis-described; and the header prints "Medium severity" as the prototype does. Full gate re-run green: ruff + format + mypy clean, **pytest 517**, **vitest 152**, **Playwright 62/62** plus the 9-test `@smoke` set and the 23-test `@epic:2` close-out against a rebuilt e2e stack. Status → done.
