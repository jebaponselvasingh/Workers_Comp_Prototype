# Story 2.1: Prioritized, Filterable Claim Queue

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want my caseload grouped by stage and sorted by computed priority with operational filters,
so that I always work the highest-risk claim first.

## Acceptance Criteria

1. **Given** a logged-in handler, **when** the workspace loads, **then** the queue lists only their scoped claims grouped into 4 collapsible stage sections with counts (📥 Intake · 🔍 Investigation · 🩺 Treatment · ✅ Settled) and empty-stage states (FR-H-1, UX-DR3).
2. **And** the first claim is auto-selected (FR-LOGIN-3 — the auto-select slice of that requirement lands here).
3. **Given** the filter dropdown, **when** any of the 8 filters is chosen (All / Active / High risk / Fraud / Litigation / Payment due / Surgery / SIU), **then** the server re-filters and returns grouped, priority-sorted results via cursor-paginated list endpoints (FR-Q-1).
4. **Given** priority scoring, **when** the queue is sorted, **then** the score is computed once in `services/worklist` with weights read from a ZEN JDM document (litigation +40, SIU +35, RTW-blocked +30, pending approval +25, payment due +20, surgery +15, severity/days-open terms, settled −100 as seeded values — AD-2/AD-8), **and** the top-3 cards above threshold carry the 🔺 priority marker (FR-Q-4).
5. **Given** a claim card, **when** it renders, **then** it shows claim ID, days open, risk dot, worker name, FRAUD/LITIG/PAY DUE/SIU badges, truncated injury type, stage pill, and employer short name (FR-Q-2), **and** derived flags (`siu_review`, `rtw_blocked`, `payment_due`) come only from registered derivation functions (AD-10).
6. **Given** a card click, **when** selection changes, **then** the detail pane loads that claim and the selection highlight moves (FR-Q-3), with queue and detail always agreeing on flags (AD-10).

## Tasks / Subtasks

- [x] Task 1: Registered derivations for queue flags — `services/derivations` (AC: 5, 6)
  - [x] Register one computing function each for `days_open`, `risk`, `siu_review`, `rtw_blocked`, `payment_due` (AD-10); no derived field becomes a user-writable column (Story 1.2 already enforces this at schema level)
  - [x] Port the prototype's demo definitions as the v1 implementations, documented as demo-grade per the architecture's Deferred list: `siu_review = fraud_flag AND fraud_score >= 60`; `rtw_blocked = stage == treatment AND return_status == under_treatment AND (hash_bucket(claim_business_id) % 5 == 0 OR risk == high)`; `payment_due = stage == treatment AND hash_bucket(claim_business_id) % 3 != 0` [Source: docs/Workers_Comp_Prototype.html lines 951–956]
  - [x] Thresholds inside these (fraud-score 60, risk band cut-offs) read from JDM parameters, not literals (AD-8)
  - [x] Unit tests: each derivation against representative seeded claims; property test that `days_open` is non-negative and date-consistent
- [x] Task 2: Priority-weights JDM document + scoring in `services/worklist` (AC: 4)
  - [x] Author the ZEN JDM document (DB-versioned, effective-dated per AD-8) seeding: litigation +40, SIU +35, RTW-blocked +30, pending approval (+25, status `initial` or `ch_assessment_process`), payment due +20, surgery +15, `severity_score × 0.3`, `min(days_open, 60) × 0.2` (the 60-day cap is itself a JDM parameter), settled −100, and the 🔺 priority-marker threshold (seed 30) [Source: docs/Workers_Comp_Prototype.html lines 1120–1132, 1146]
  - [x] Wire the ZEN engine (`zen-engine` PyPI, pinned) into `server/rules/` if Story 1.x has not already — this is the first JDM consumer; establish the load-by-key + version pattern here
  - [x] `services/worklist.priority_score(claim, derived_flags)` — the exactly-once scorer (AD-2); score arithmetic in typed Python, every weight/threshold from the JDM document (AD-8); never re-implemented in SQL or TS
  - [x] Unit tests: each weight term toggles the score as seeded; settled claims sink; marker threshold honored; a JDM-value change (test fixture) shifts scores without code change
- [x] Task 3: Queue query API — grouped, filtered, cursor-paginated (AC: 1, 3, 4)
  - [x] Repository query for the handler's scoped claims via the AD-7 caller context (built only by the auth dependency from Story 1.3/1.4 — no caller-supplied scope)
  - [x] `GET /api/claims/queue` (router in `server/api/`): `filter` query param with the 8 snake_case values `all | active | high_risk | fraud | litigation | payment_due | surgery | siu` (UI owns display labels per conventions; prototype mapping: active→stage==treatment, high_risk→risk==high, fraud→fraud_flag, litigation→litigation_flag, payment_due/siu→derived flags, surgery→surgery_required) [Source: docs/Workers_Comp_Prototype.html lines 1155–1165]
  - [x] Response: per-stage groups (`intake | investigation | treatment | settled` enum) each `{items, nextCursor, total?}` cursor-paginated per list conventions, items priority-sorted desc within stage, each item carrying the card fields (business ID `WC-nnnn`, days open, risk, name, flags, injury type, stage, employer short name) and `priorityMarker: bool` (top-3 above threshold, computed server-side) — camelCase JSON via Pydantic alias
  - [x] Scope test: scoped handler sees only mapped employers' claims; endpoint rejects any scope-shaped input
- [x] Task 4: Queue UI — `web/src/features/queue/` (AC: 1, 2, 5, 6)
  - [x] TanStack Query hook via the generated OpenAPI client; register queue keys in the shared `queryKeys` module (AD-9)
  - [x] 4 collapsible stage sections with icon + label + count chip; "No claims in this stage." empty state per section (NFR-3); loading and error states for the whole pane
  - [x] `ClaimCard` component: row 1 claim ID (mono font, 🔺 prefix when `priorityMarker`) + days-open; row 2 risk dot + worker name + FRAUD/LITIG/PAY DUE/SIU badges (wn/er/st token colors as in the prototype); row 3 injury type truncated ~28 chars; row 4 stage pill + employer first word [Source: docs/Workers_Comp_Prototype.html lines 1139–1151]
  - [x] Filter dropdown (8 options) drives the query param; changing it refetches — no client-side re-filtering of a cached superset (AD-1)
  - [x] Selection state: selected claim business ID held in URL/router state; card click moves the `sel` highlight and drives the detail pane region (2.2's components subscribe to this same selection); flags shown on the card come from the same server payload the detail will use — never recomputed in TS (AD-10)
  - [x] Auto-select the first claim (first card of the first non-empty stage group) when the handler workspace loads with no selection (AC 2)
  - [x] Vitest: card renders all fields/badges; empty-stage state; auto-select behavior
- [x] Task 5: E2E story spec (AC: all)
  - [x] `e2e/stories/2-1-prioritized-filterable-claim-queue.spec.ts` tagged `@story:2-1 @epic:2`; login as a handler persona via the shared fixture
  - [x] `@smoke` happy path: handler logs in → queue renders 4 stage groups over seeded data → first claim auto-selected → pick a filter → list re-renders filtered → click a card → selection highlight moves
  - [x] Additional tests: empty-stage state visible for a persona/filter combination that yields one; 🔺 marker present on a known high-priority seeded claim; scoped handler never sees an out-of-scope claim ID

### Review Findings

Three-layer code review, 2026-08-11 (Blind Hunter · Edge Case Hunter · Acceptance Auditor), against `e889089..b4a5494`. Severity in brackets. The auditor found 5 of 6 ACs met and AC 4 partially met; nothing was found unmet.

**Decisions taken (Jeba, 2026-08-11)** — each resolved to a patch. All 26 patch items below were applied on 2026-08-11 (second review pass). One item is partially complete and says so: the e2e suite now covers the collapse and filter-miss states, but the **empty-book** state remains Vitest-only, because every seeded persona has claims (the smallest, Fatima, has 4) and reaching it would need either a Story 1.2 seed change — which moves the `== 10` persona assertions in `test_schema_seed.py` and `test_auth.py` — or an HTTP stub, which the e2e suite refuses on principle.

- [x] [Review][Patch] `[medium]` **Move the pending-approval status set into the JDM document.** `priority_weights.jdm.json` carries the weight `pendingApproval: 25`, but *which* statuses count is `PENDING_APPROVAL_STATUSES = frozenset({initial, ch_assessment_process})` in `services/worklist/priority.py:116` — a code change, not a document version, contrary to Task 2 and AD-8's one-tier rule. **Ruling:** the status set belongs in the document. The parameter block gains a list-of-strings field validated against the `ClaimStatus` enum at the boundary; this is the first JDM document, so that shape is the precedent every later one copies. `priority_score`'s "free of every constant it uses" docstring becomes true. — done: `pendingApprovalStatuses` added to the committed document and its 0009 seed (edited in place, the story being unreleased); `rules/parameters._status_set` resolves every member against `ClaimStatus` and refuses an unknown one by name; `tests/test_rule_parameters.py` covers the shape.
- [x] [Review][Patch] `[medium]` **Publish `filteredTotal` beside `unfilteredTotal`.** — done: the payload carries `filteredTotal` (and `thresholdsVersion`); `QueuePane.totalOf` is gone, `StageGroup`'s `group.total - items.length` is gone with the "Show more (N)" count, and `total`/`unfilteredTotal`/`filteredTotal`/`nextCursor` are watched fields in the `noDerivation` guard.
- [x] [Review][Patch] `[low]` **Keep the single sentence in the empty and filter-miss states.** — done: two Vitest cases assert the collapse is deliberate (no `queue-group-*` sections render), an e2e case reaches the filter-miss state against the real stack, and the Dev Notes record the AC-1 wording below.

- [x] [Review][Patch] `[medium]` The cursor's two version guards are unreachable on the production path — fixed: the rule documents are resolved at `utc_today()` (or an explicit `as_of`), and the cursor's date is used only to age `days_open`. `test_claims_queue.test_a_cursor_ranked_by_a_superseded_document_is_refused` inserts a real v2 and gets the 400.
- [x] [Review][Patch] `[medium]` A cursor naming an `as_of` before migration 0009's effective date raised `RuleDocumentMissing` → 500 — fixed: the document load no longer depends on the cursor's date, and `decode_cursor` additionally refuses a future date or one older than `MAX_CURSOR_AGE` (7 days).
- [x] [Review][Patch] `[medium]` No test exercises a real JDM document change — fixed: `test_a_second_priority_weights_version_reranks_the_queue` inserts a genuine version 2 into `rule_document` and asserts the endpoint's ordering, marker and `rulesVersion` all move with no code change.
- [x] [Review][Patch] `[medium]` `presenceOf` reads only the base query's first page — fixed: `useLoadedClaimIds` reads the same cache entries the pages queries write, and expansion state moved to `WorkspaceShell` (`useStageExpansion`) so both panes decide from one fact. Post-click state covered.
- [x] [Review][Patch] `[medium]` The AC-2 guard scans less than it claims — fixed: recursive walk over `features/queue/` and `features/shell/`, a test asserting the scan reaches named files, the four count fields added, a `.reduce(` rule, and a single-pass stripper that removes whichever construct opens first.
- [x] [Review][Patch] `[medium]` Retiring `RISK_HIGH_MIN`/`RISK_MED_MIN` reintroduced the silent-config failure the `compose.yaml` comment condemns: `extra="ignore"` dropped a deliberate override and left the app on 65/35 with nothing to indicate why. `config.py` now carries a `RETIRED_SETTINGS` map and a `mode="before"` validator that refuses a retired name and says where the setting went — `mode="before"` because that is the only moment the name is still visible. Four tests in `test_config.py`, including case-insensitivity and proof that unrelated unknown variables are still ignored. *(Applied by the orchestrator: this item was accidentally omitted from the patch agent's brief, which correctly reported it as not done.)*
- [x] [Review][Patch] `[medium]` A refused cursor leaves a stage group unrecoverable — fixed: the error renders a *Reload this stage* button that removes the accumulated pages, collapses the group and invalidates the base queue query so a fresh first-page cursor arrives.
- [x] [Review][Patch] `[medium]` `services/worklist/queue.py` has no pure unit tests — fixed: `tests/test_queue_assembly.py`, 37 cases with no `requires_db` — cursor round trip and property test, forged payloads, the `(-score, claim_id)` tie-break on a constructed tie, marker assignment, and the page/offset boundaries.
- [x] [Review][Patch] `[medium]` The infinite-pages query key omits the first-page cursor — fixed: `queryKeys.claims.queuePages(filter, stage, firstCursor)`.
- [x] [Review][Patch] `[low]` A forged cursor carrying `Infinity` — fixed: `ArithmeticError` added to the except tuple; covered at unit and endpoint level with a hand-built payload.
- [x] [Review][Patch] `[low]` A forged cursor's `limit` is not bounded — fixed: `MIN_PAGE_LIMIT`/`MAX_PAGE_LIMIT` in `queue.py`, read by both `decode_cursor` and the route's `Query`.
- [x] [Review][Patch] `[low]` `expandedFilter` remembers rather than expires — fixed: expansion is a set of stages reset by whoever changes the filter; the `A → B → A` case is a test.
- [x] [Review][Patch] `[low]` `presenceOf` tests an optional field with strict `!== null` — fixed: the field is read through the shared `useLoadedClaimIds`, which normalises with `?? null` once.
- [x] [Review][Patch] `[low]` `rulesVersion` reports only the weights version — fixed: `thresholdsVersion` published beside it, and the docstring says which is which.
- [x] [Review][Patch] `[low]` `open_duration.py`'s opening docstring — fixed: "reconciles with **no** date in the record".
- [x] [Review][Patch] `[low]` `priority_markers` spends a budget rather than checking positions — fixed by taking the other option: the descending precondition is now stated *and enforced* (`ValueError`), which makes the budget form and the prototype's positional `i<3` the same function. The divergence and the choice are recorded in the docstring and in `test_an_unsorted_sequence_is_refused_rather_than_marked`.
- [x] [Review][Patch] `[low]` `siuFraudScoreMin` unbounded, non-finite parameters, negative factors — fixed in `rules/parameters.py`; `settledPenalty` stays legitimately negative and a test says so.
- [x] [Review][Patch] `[low]` The 400 loses `Cache-Control: no-store` — fixed: carried on the `ProblemException`.
- [x] [Review][Patch] `[low]` The e2e "three panes" test asserts two — fixed: `copilot-pane` asserted, with a note that the 1280×720 project viewport is exactly the `xl` breakpoint.
- [x] [Review][Patch] `[low]` An e2e assertion comparing two oracle computations — fixed: re-labelled as the premise it is, with a page-touching conclusion (rendered ids equal the filtered oracle) beside it.
- [x] [Review][Patch] `[low]` The e2e suite never collapses a group and never reaches the filter-miss or empty-book pane states — **partly done**: collapse and filter-miss are now e2e specs. The empty-book state is **not** reachable e2e — every seeded persona has claims, so it would need a Story 1.2 seed change (an eleventh `app_user`, which moves the persona-count assertions in `test_schema_seed.py` and `test_auth.py`) or an HTTP stub, which this spec file explicitly refuses. It stays covered by `QueuePane.test.tsx` and by `unfilteredTotal`'s server tests. Still exactly one `@smoke` test in the file.
- [x] [Review][Patch] `[low]` `rules/engine.py`'s docstring is false at import time — fixed by rewording, not by breaking the chain: `priority.py` imports `PriorityWeights` from `rules/parameters.py`, which imports `rules/engine.py`, so `zen` and SQLAlchemy load at import however `RiskBand` is reached. The docstring now scopes its claim to call time and says so.
- [x] [Review][Patch] `[low]` Accumulated minors — fixed: `STAGE_ICON`/`STAGE_LABEL` moved to `features/queue/stageLabels.ts` (which also clears the two `react-refresh` lint warnings), and `rules/engine.load` resolves its date once into `effective_on`.

- [x] [Review][Defer] `[medium]` `/stats/topbar` now 500s whenever the rules tier does, and no test covers a migrated database whose `rule_document` rows are missing or not yet effective [server/api/routers/stats.py] — deferred, already recorded from the previous pass
- [x] [Review][Defer] `[low]` `pageLimit` lives in the priority-weights document, so changing the queue's page size needs an Alembic migration and invalidates every outstanding cursor [server/rules/documents/priority_weights.jdm.json] — deferred, revisit when a second consumer needs it
- [x] [Review][Defer] `[low]` Every "Show more" re-serialises the first page of all four groups and the client discards three of them [server/api/routers/claims.py] — deferred, pre-existing shape of the endpoint
- [x] [Review][Defer] `[medium]` Every request reads, derives and scores the caller's entire scoped portfolio [server/services/worklist/queue.py] — deferred, already recorded from the previous pass
- [x] [Review][Defer] `[low]` A compiled `ZenEngine` decision is held in a module-level `lru_cache` and shared across concurrent requests, and the `context` plumbing through the cache key is exercised by no test [server/rules/engine.py] — deferred
- [x] [Review][Defer] `[low]` `uv.lock` was excluded from review, so the transitive dependency set `zen-engine==0.53.0` pulled in has not been looked at [server/uv.lock] — deferred
- [x] [Review][Defer] `[low]` The e2e oracle resolves "today" more than once within a run, so a midnight straddle can make its own sort comparator inconsistent [lineworker/e2e/fixtures/seed.ts] — deferred, already recorded from the previous pass

## Dev Notes

### What this story is — and is not

This story delivers the left pane of the handler 3-pane layout plus the server plumbing behind it: derivation registrations, the priority JDM document, the worklist scorer, and the queue endpoint. It also owns the **selection contract** (URL-held selected claim + auto-select-first). It does **not** build the detail pane — Story 2.2 renders the case header/overview against the selection this story establishes; until 2.2 lands, the center pane may be a minimal placeholder showing the selected claim ID. Inline editing is 2.3; the injury diagram 2.4; documents 2.5; photos 2.6. The copilot pane is Epic 6. The prototype (`docs/Workers_Comp_Prototype.html`) is a **design/behavior and DATA reference only — never a code source**; its `priorityScore`/`renderQ` JS informs behavior, none of it is ported as code.

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### AC 1's "four sections" holds for the populated case

The pane renders four collapsible stage sections with counts whenever there
is anything to show. When there is not — an empty book, or a filter that
matched nothing — it replaces all four with **one sentence** rather than
stacking four "No claims in this stage." messages under four zero chips.

That is a deliberate reading of NFR-3, which asks for three *distinguishable*
messages: "no claims in your caseload", "no claims match this filter" and
"no claims in this stage". Four repetitions of the third say nothing about
which of the first two is true, and the first two are the ones a handler
needs (one means "ask your supervisor", the other means "change the filter").
`QueuePane.test.tsx` asserts the collapse in both states so it reads as a
decision; the e2e spec reaches the filter-miss state against the real stack.

### Architecture compliance (binding ADs for this story)

- **AD-1:** the queue is server-assembled — grouping, filtering, sorting, and the top-3 marker all computed behind FastAPI; the SPA renders the payload.
- **AD-2:** `priority_score` exists exactly once, in `services/worklist`; the dashboard's top-30 table (Epic 5) will reuse this same scorer.
- **AD-7:** repository-layer scoping via the caller context from the auth dependency; `scope_all` is a tautology predicate, never a skipped filter; no endpoint accepts caller-supplied scope.
- **AD-8:** every weight, cap, and threshold lives in the ZEN JDM document; the score arithmetic is typed Python. A rule element in exactly one tier.
- **AD-9:** TanStack Query + shared `queryKeys`; the queue payload is server state, selection is local/URL state; no optimistic anything here (read-only story).
- **AD-10:** `siu_review`, `rtw_blocked`, `payment_due`, `days_open`, `risk` each have one registered computing function in `services/derivations`; queue and (future) detail consume the same values.
- **AD-15:** story spec is the done-gate (see Testing requirements).

### Data notes

- **No new tables.** Reads `claim`, `employer`, `app_user`, `user_employer_assignment` from Story 1.2's schema/seed. The priority-weights JDM document is stored per the AD-8 DB-versioned convention (rules storage established by its first consumer — that's this story; coordinate with any Epic 1 JDM groundwork before inventing a table).
- Derived flags are computed at read time by `services/derivations` — never persisted as user-writable columns (Story 1.2 AC 3 already guards this).
- The prototype's hash-bucket flag definitions are **demo-grade by explicit architecture decision** (Deferred: "Derived-flag definitions") — port them faithfully, comment them as demo definitions, and keep the tunables in JDM so real definitions later change parameters, not call sites.
- Write-owner: nothing in this story writes domain data (AD-12 not exercised beyond JDM document versioning).

### UX notes

- UX-DR3 governs: 3-pane handler layout with Queue left; collapsible stage groups with icons 📥 🔍 🩺 ✅ and count chips; empty-stage states; 🔺 top-3-over-threshold markers; cards show risk dot, flag badges, stage pill, employer short name.
- UX-DR12 + Story 1.1 design-token ruling: the prototype palette is **LIGHT and canonical** (epics.md's "dark console aesthetic" wording is a documented discrepancy — see 1-1's Dev Notes). Badge colors ride the ok/wn/er/st tokens established in 1.1; do not invent a dark theme.
- Information-dense card style per the prototype's `.qc` cards; JetBrains Mono for claim IDs and day counts.
- NFR-3: loading, error, and empty states on the queue pane and each stage group.

### Testing requirements

- Unit: each derivation function; scorer weight-by-weight against JDM seed values; filter → predicate mapping; cursor pagination continuity.
- Property (Hypothesis): score monotonicity — adding any positive-weight condition never lowers a non-settled claim's score; `days_open` term caps at the JDM cap.
- Scope tests: scoped handler payload contains only in-scope claims (per AD-7, mirroring Story 1.4's Jennifer Park / David Bline pattern with handler personas).
- Web: Vitest for card rendering, empty states, auto-select.
- E2E (AD-15): `e2e/stories/2-1-prioritized-filterable-claim-queue.spec.ts` tagged `@story:2-1 @epic:2`, exactly one `@smoke` happy path, run against the freshly reset e2e compose stack. **The story cannot move to `review`/`done` until this spec passes.**

### Project Structure Notes

- Server: `server/services/derivations/` (flag/risk/days-open registrations), `server/services/worklist/` (scorer + queue query assembly), `server/rules/` (ZEN wrapper + JDM documents), router in `server/api/`.
- Web: `web/src/features/queue/` (QueuePane, StageGroup, ClaimCard, FilterSelect); keys in `web/src/api/queryKeys`; API access only through the generated OpenAPI client (regenerate after adding the endpoint).
- Enum casing: snake_case wire values (`payment_due`, `high_risk`), UI-owned labels ("Payment due", "High risk") — conventions row "Enums & statuses".

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 2.1]
- Epic 2 scope + cross-epic seams: [Source: _bmad-output/planning-artifacts/epics.md#Epic 2]
- AD-2 / AD-7 / AD-8 / AD-9 / AD-10 / AD-15: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- List/enum/ID conventions: [Source: ARCHITECTURE-SPINE.md#Consistency Conventions]
- Capability map row "Queue, filters, priority sort": [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map]
- Prototype behavior + seed data: [Source: docs/Workers_Comp_Prototype.html — priorityScore line 1120, STAGE_GROUPS line 1133, buildQCard line 1139, renderQ/filters line 1152, derived-flag pass lines 951–956, ALL_CLAIMS line 632]
- Deferred demo-flag ruling: [Source: ARCHITECTURE-SPINE.md#Deferred — "Derived-flag definitions"]
- Dense-AC decomposition advisory: [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Standing Advisories item 4]
- Design-token light-palette ruling: [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]

## Dev Agent Record

### Agent Model Used

Claude Opus 5 (1M context), via the `bmad-dev-auto` unattended workflow. Working spec: [spec-2-1-prioritized-filterable-claim-queue.md](spec-2-1-prioritized-filterable-claim-queue.md).

### Debug Log References

None. No blocking condition was hit; one review pass, no spec loopback.

### Completion Notes List

Learnings worth carrying into 2.2–2.6 and Epic 5:

- **This story landed the whole rules tier.** `server/rules/` was empty and `zen-engine` was not a dependency, so Story 2.1 built the `rule_document` table (key + version + `effective_from` + JSONB, `SELECT`-only to the app role), the load-by-key wrapper, and the first two JDM documents. The pattern to copy: a document returns a **parameter block** evaluated once per request, and typed Python does the arithmetic. Do not evaluate ZEN per row.
- **Derivations are now built from `DerivationThresholds`, not `Settings`.** `Derivation.build` changed signature and `for_settings` became `for_thresholds`; the risk bands moved out of `config.py` into `derivation_thresholds.jdm.json`. The SLA targets deliberately did **not** move — that is still an open `TODO(JDM)` for whoever needs it. `RISK_HIGH_MIN` is no longer an env knob; changing a band is a new document version.
- **`days_open` is `(as_of − froi_date)`, floored at 0.** The prototype's static `daysOpen` matches no date in the record — it was checked against `doi`, `froi_date`, `actual_rtw`, `rtw_rec` and `settlement_days`. `as_of` is an explicit parameter, resolved once per request, so a response ages every claim against one day.
- **The score is Python-side, so paging cannot be keyset.** The cursor is an opaque base64 of `{filter, stage, offset, asOf, limit, weightsVersion, thresholdsVersion}` — everything that shaped the ordering — and any mismatch is a 400 rather than a best-effort re-page. If 2.2+ adds a sort, it must extend that record too.
- **Both marker and score live in `services/worklist/priority.py`.** Epic 5's top-30 imports them unchanged. Note the marker is a property of the *filtered* list it is computed over, and that the −100 settled penalty is a bias, **not** an absolute floor: the categorical weights total 165, so a heavily-flagged settled claim can still outrank a quiet open one. The ungrouped top-30 will need to decide whether it wants that.
- **The queue reads the caller's whole scoped portfolio on every request** (including page-2 requests) because the sort key is not expressible in SQL. Fine at 100 claims; recorded in `deferred-work.md` as the thing to revisit first if a portfolio grows.
- **NFR-3 needs three different facts, not two.** "No claims in your caseload", "no claims match this filter" and "no claims in this stage" are decided from `unfilteredTotal`, the filtered total, and the group total respectively. A pane that infers scope-emptiness from a filtered count will blame the filter for an empty book.
- **Selection is a `?claim=WC-nnnn` search param on `/workspace`** — auto-select replaces, a click pushes, a blank value reads as absent, and a claim the payload cannot confirm is reported as unconfirmed rather than as missing. 2.2's detail pane should read `useSelectedClaimId()` rather than take a prop.
- **CI trap:** the `migrations` job enumerates DB-backed test files by hand. A new DB-backed test that is not added to that list never runs in CI.

Second review pass (2026-08-11), worth carrying forward:

- **A rule element in one tier means the *whole* element.** `pendingApproval`
  was a weight in the document and a `frozenset` in Python — half the rule
  retunable by an operator, half needing a deploy. `pendingApprovalStatuses`
  is the first non-numeric JDM parameter and its validator (resolve every
  member against the enum at the boundary, name the document, the version
  and the offending value) is the precedent later documents should copy.
- **A cursor's recorded date must not choose the rules that read it.** The
  version guards were tautologies for a whole story because the documents
  were loaded effective on the cursor's own date. Documents resolve at
  today's date; the cursor's date ages `days_open` and nothing else. If a
  later story adds another versioned input to the ordering, it joins the
  *compared* half of the cursor, not the reused half.
- **A cursor is caller-supplied input.** It is base64, not a signature, so
  every field is bounded in `decode_cursor` — including `limit`, which the
  route caps and the cursor was quietly reusing past the cap.
- **Expansion state belongs above both panes.** "Which groups did the
  handler expand?" decides what the queue renders *and* what the detail pane
  may say about the selected claim. Held inside `StageGroup` the two
  disagreed about a claim that was on screen.
- **The client computes no count.** `filteredTotal` joined `unfilteredTotal`
  on the wire and the "Show more (N)" remainder was deleted rather than
  moved: the server cannot know how many pages a client is holding, so there
  is no honest number to send. `noDerivation.test.ts` now watches the count
  fields and walks `features/shell/` too.
- **`priority_markers` enforces its precondition.** Descending order is
  checked, so the budget rule and the prototype's positional `i<3` coincide
  and Epic 5's ungrouped top-30 cannot inherit an ambiguity.

### File List

**New — server**
- `lineworker/server/api/routers/claims.py`
- `lineworker/server/data/versions/20260811_0008_rule_document.py`
- `lineworker/server/data/versions/20260811_0009_seed_rule_documents.py`
- `lineworker/server/rules/documents/derivation_thresholds.jdm.json`
- `lineworker/server/rules/documents/priority_weights.jdm.json`
- `lineworker/server/rules/engine.py`
- `lineworker/server/rules/parameters.py`
- `lineworker/server/services/derivations/open_duration.py`
- `lineworker/server/services/derivations/queue_flags.py`
- `lineworker/server/services/worklist/priority.py`
- `lineworker/server/services/worklist/queue.py`
- `lineworker/server/tests/test_claims_queue.py`
- `lineworker/server/tests/test_priority_score.py`
- `lineworker/server/tests/test_queue_assembly.py`
- `lineworker/server/tests/test_rule_parameters.py`
- `lineworker/server/tests/test_rules_engine.py`

**New — web / e2e**
- `lineworker/e2e/stories/2-1-prioritized-filterable-claim-queue.spec.ts`
- `lineworker/web/src/api/claims.ts`
- `lineworker/web/src/features/queue/ClaimCard.test.tsx`
- `lineworker/web/src/features/queue/ClaimCard.tsx`
- `lineworker/web/src/features/queue/FilterSelect.tsx`
- `lineworker/web/src/features/queue/QueuePane.test.tsx`
- `lineworker/web/src/features/queue/QueuePane.tsx`
- `lineworker/web/src/features/queue/StageGroup.tsx`
- `lineworker/web/src/features/queue/noDerivation.test.ts`
- `lineworker/web/src/features/queue/stageLabels.ts`
- `lineworker/web/src/features/queue/useSelectedClaim.ts`
- `lineworker/web/src/features/queue/useStageExpansion.ts`
- `lineworker/web/src/features/shell/WorkspaceShell.test.tsx`

**Changed**
- `.github/workflows/ci.yaml`
- `lineworker/deploy/.env.example`
- `lineworker/deploy/compose.yaml`
- `lineworker/e2e/fixtures/login.ts`
- `lineworker/e2e/fixtures/seed.ts`
- `lineworker/server/api/app.py`
- `lineworker/server/api/routers/__init__.py`
- `lineworker/server/api/routers/stats.py`
- `lineworker/server/config.py`
- `lineworker/server/data/models/__init__.py`
- `lineworker/server/data/models/core.py`
- `lineworker/server/data/repositories/claims.py`
- `lineworker/server/pyproject.toml`
- `lineworker/server/rules/__init__.py`
- `lineworker/server/services/derivations/__init__.py`
- `lineworker/server/services/derivations/registry.py`
- `lineworker/server/services/derivations/risk_band.py`
- `lineworker/server/services/worklist/__init__.py`
- `lineworker/server/services/worklist/stats.py`
- `lineworker/server/tests/seed_fixture.py`
- `lineworker/server/tests/test_derivations.py`
- `lineworker/server/uv.lock`
- `lineworker/web/src/api/queryKeys.ts`
- `lineworker/web/src/api/schema.d.ts`
- `lineworker/web/src/features/shell/WorkspaceShell.tsx`
- `lineworker/web/src/test/api-mock.ts`
- `lineworker/web/src/test/setup.ts`
