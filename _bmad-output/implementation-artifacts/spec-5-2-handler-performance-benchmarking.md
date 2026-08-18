---
title: 'Story 5.2 — Handler Performance Benchmarking'
type: 'feature'
created: '2026-08-18'
baseline_revision: '4f94b880ed824f841eca12d1cd0e1c74691114ca'
final_revision: '1e0fe2e5064cd11d6b0410706c9ea237378b7710'
status: 'done'
review_loop_iteration: 1
followup_review_recommended: true # a full bad_spec loopback re-derived the ranking arithmetic, then 14 patches across server, web, fixtures and both oracles — two of them tests that did not test what they claimed. Breadth plus a reverted core makes an independent look worthwhile
context:
  - '{project-root}/_bmad-output/implementation-artifacts/5-2-handler-performance-benchmarking.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-5-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-5-1-portfolio-kpi-cards.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The dashboard Story 5.1 built ends after ten KPI cards. A supervisor can see that 32 claims are high risk but not who is carrying them, how fast each handler is closing, or who is drifting behind the desk — the prototype's ranked handler table with cycle-speed bars and a leader/laggard callout has no server-side equivalent, and the cycle-time deviation bands and complexity blend it needs exist nowhere as parameters.

**Approach:** One scoped aggregate in `services/worklist` groups the caller's claims by assigned handler, folds each group through the *same* `sla.strip_of` the top bar's SLA strip uses, and asks two new registered derivations for the complexity band and the cycle-time status. A new `handler_performance` JDM document carries the two deviation thresholds and the complexity blend. `GET /dashboard/handler-benchmarks` serves the ranked rows plus the leader/laggard facts to a plain semantic table on the dashboard page.

## Boundaries & Constraints

**Always:**
- Cycle-time components come from `services/worklist/sla.py` — `SlaSample`, `sample_of`, the pure `strip_of`, `targets_for`. Per handler this is `strip_of` over that handler's samples; the portfolio row is `strip_of` over all in-scope samples (AD-2). No second averaging anywhere.
- `test_nothing_outside_the_worklist_aggregation_reads_the_sla_source_columns` greps every server `.py` — **comments and docstrings included** — for the three SLA source column names and allowlists only `sla.py`, `data/models/core.py`, `open_duration.py`, tests, seed and versions. The new benchmark module must never spell those names: `sla.py` grows a `SAMPLE_COLUMNS` tuple and a `sample_of(row) -> SlaSample` mapper, and the aggregate composes its one scoped read from `[*sla.SAMPLE_COLUMNS, Claim.handler_id, Claim.severity_score, Claim.surgery_required, Claim.litigation_flag, Claim.status]`.
- Handlers are derived from in-scope claims, never from a roster (AD-7). A handler with zero claims inside the viewer's scope does not appear; a handler whose book straddles scopes appears with only the in-scope part.
- Complexity and cycle-time status are each exactly one registered derivation (AD-10). `Derivation.build` receives only `DerivationThresholds`, so the `HandlerPerformance` block reaches them through `.of(...)` — the precedent is `NextBatchDateDerivation.of(as_of, weekdays)`.
- Enum values are snake_case (`low|med|high`, `on_track|watch|attention`); the UI owns display labels.
- Every displayed number — rank, composite days, the bar's percentage, deviation, RTW %, complexity score — is server-computed (AD-1). `noDerivation.test.ts` already scans `features/dashboard` and bans `.sort(`, `.reduce(` and arithmetic on any listed field.
- Read-only: no mutation, no audit event, `Cache-Control: no-store`, and a signature with nowhere to put a scope.

**Block If:**
- Adding the `handler_performance` document would require re-dating any existing rule document, or `alembic check` reports drift the new migration did not cause.
- A handler segment and the scoped portfolio's same segment are **both** `no_data` on the full-portfolio book — the composite would then be a partial sum with no honest fallback, and what a rank means in that state is a product call, not a dev-time pick.

**Never:**
- No `useReactTable`. TanStack Table is a dependency with zero uses in `src/`; the one real table in the app (`claim-detail/bills/PaymentScheduleCard.tsx`) is hand-written semantic HTML, and `getSortedRowModel` would re-sort in the browser a list the server ranked — the exact thing `noDerivation.test.ts` forbids. **This is a deliberate departure from Task 4's wording**; record it in the Dev Agent Record.
- No Recharts and no chart of any kind (5.3). The cycle-speed bar is a token-styled div, copying `injury/SeverityCard.tsx`'s server-computed `width: ${n}%` fill.
- No row click, `onRowClick`, href or drill-through (5.5); no top-30 worklist (5.4); no new KPI card (5.1 owns that table).
- No per-handler pending-approval count over `payment_schedule_week` — that table is owned by `services/financials` (AD-12) and no scoped count exists. Pending approvals are claims whose `status` is in `PriorityWeights.pending_approval_statuses`, which is what the prototype counts and what the queue scorer already means by the phrase.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Full portfolio | Session for David Bline (`scope_all`) | Six rows ranked by ascending composite: Liam O'Sullivan, Kaya Johnson, Marcus Chen, Fatima Al-Mansoori, Sarah Williams, Dante Reyes; case counts 7 · 38 · 19 · 4 · 8 · 24; leader Liam, laggard Dante | No error expected |
| Analyst | Session for David Bline (`analyst`) | Byte-identical to his supervisor row — same scope, no role branch in any figure | No error expected |
| Scoped supervisor | Session for Jennifer Park (Toyota/GM/3M) | Exactly two rows — Marcus Chen (19) and Sarah Williams (8). Kaya, Dante, Liam and Fatima absent; every figure recomputed against her 27-claim portfolio, not sliced from Bline's | No error expected |
| The straddle | Session for Ken Stoker (John Deere/Lockheed) | Liam O'Sullivan (7) and Fatima Al-Mansoori (4) only. **Kaya Johnson is assigned John Deere in `user_employer_assignment` yet handles no Deere claim — she must not appear**, which is the roster-vs-claims test | No error expected |
| Handler asks | Session for Kaya Johnson (`handler`) | `403` problem+json, raised before any read | `BenchmarksNotPermitted` → `FORBIDDEN_RESPONSE` |
| Empty book | `CallerContext` with `employer_ids=frozenset()` | `items: []`, `leader`/`laggard` null, thresholds and `rulesVersion` still populated | No error expected |
| Missing segment | A handler with no settled claim (settle metric `no_data`) | That segment is substituted by the **scoped portfolio's** average for the same segment before summing — never 0, never a partial sum | No error expected |
| Thresholds superseded | A `handler_performance` v2 widening the deviation bands, effective today | Status chips move and the response's echoed thresholds follow, with no code change | No error expected |
| No session | Request without a session cookie | `401` problem+json | `UNAUTHENTICATED_RESPONSE` |
| Fetch in flight | Query pending | Row skeletons, container `aria-busy`, no zeros, no rank numbers | Never invent a figure |
| Fetch failed | `ApiError` from the endpoint | Inline `role="alert"` in place of the table; the KPI cards above are unaffected | No native dialog, no retry loop |

</intent-contract>

## Code Map

- `server/services/worklist/sla.py` -- `SlaSample`, pure `strip_of`, `targets_for`, `_rounded`; the module the three SLA column names may never leave. Grows `SAMPLE_COLUMNS` + `sample_of`.
- `server/services/worklist/summary.py` -- Story 5.1's aggregate: the frozen-projection / pure-fold / async-read split, derivations passed in as arguments, thresholds passed down from the router. The shape to copy.
- `server/data/repositories/claims.py` -- `employer_scope`, `select_claim_columns` (no join, so no handler name), `select_claim_detail` (L164-201) the only `aliased(AppUser)` join precedent.
- `server/data/context.py` -- `CallerContext`, the `ALL_EMPLOYERS` sentinel, `scopes_all_employers`.
- `server/services/derivations/registry.py` -- `Derivation[T](name, describes, build)`, `register`, `get`, `registered_names`; `build` takes only `DerivationThresholds`.
- `server/services/derivations/batch_calendar.py` -- `NextBatchDateDerivation.of(as_of, weekdays)`: the precedent for non-`DerivationThresholds` parameters arriving at `.of()`.
- `server/services/derivations/queue_flags.py` -- `SiuReviewDerivation`: the frozen-dataclass-with-tunables pattern.
- `server/rules/parameters.py` -- `ReserveBands` + `reserve_bands_for` (L690-738): the template for a two-ordered-threshold block. **Not** in the 65/35 grep allowlist.
- `server/rules/documents/reserve_bands.jdm.json` -- the minimal JDM shape (inputNode → expressionNode → outputNode, scalars as string-valued expressions).
- `server/data/versions/20260814_0025_reserve_bands.py` -- the new-key migration pattern; head is `0037_fraud_flag_threshold`.
- `server/api/routers/dashboard.py` -- the parameter-free `no-store` router; its docstring already assigns this story the access decision.
- `server/api/routers/claims.py` -- `FORBIDDEN_RESPONSE` (L1212) and the `ApprovalNotPermitted` → 403 translation (L2201-2210); `ClaimActionsResponse` (L2341), the items-without-`total` envelope precedent.
- `server/services/worklist/approvals.py` -- the role gate at L135, raised before any lookup: the placement rule, inverted here.
- `server/rules/documents/priority_weights.jdm.json` -- `pendingApprovalStatuses`, read via `weights_for(db)`.
- `server/tests/seed_fixture.py` -- the independent oracle; `claims_for`, `_mean`, `SLA_TARGETS`, `PENDING_APPROVAL_STATUSES`, `handler_personas()`.
- `server/tests/test_portfolio_summary.py` -- `make_client`, `login_as`, the inline `CallerContext` build, and the contract tests to mirror.
- `server/tests/test_derivations.py` -- the 65/35 grep (allowlist: `rules/documents/**`, `services/derivations/**`, `tests/**`, `data/seed/**`, `data/versions/**`) and `test_no_derivation_module_is_named_after_the_value_it_exports`.
- `server/tests/test_rules_engine.py` -- `EFFECTIVE_DOCUMENTS`/`SEEDED_DOCUMENTS`: a new key must join both or the coverage test fails.
- `web/src/features/claim-detail/bills/PaymentScheduleCard.tsx` -- the house table: `sr-only` caption, `scope="col"`, `data-testid` + `data-*` rows, `font-mono` figures, `CHIP_CLASS` chips.
- `web/src/features/claim-detail/injury/SeverityCard.tsx` -- the single-fill bar (`width: ${n}%` straight from a server figure).
- `web/src/features/dashboard/DashboardPage.tsx` -- the page and its `CardSpec` list-as-contract idiom; the table slots in after the two card rows.
- `web/src/api/dashboard.ts`, `web/src/api/queryKeys.ts` -- `useDashboardSummary` and the `dashboard` key group whose docstring pre-authorizes a `handlerBenchmarks` sibling.
- `web/src/test/api-mock.ts` -- `StubRoutes`, `DASHBOARD_SUMMARY` and its retuned contrast fixture; the `stubApi` URL chain.
- `web/src/features/queue/noDerivation.test.ts` -- `DERIVED_FIELDS`, `FORBIDDEN` (bans `.sort(`/`.reduce(`), the scan-reaches assertion, the `smells`/`innocent` lists.
- `e2e/fixtures/seed.ts` -- the TS oracle and `SeedClaim` (needs `handler` added); `e2e/stories/5-1-portfolio-kpi-cards.spec.ts` the spec template.
- `docs/Workers_Comp_Prototype.html` -- `renderSV` L1010-1050: the design contract for columns, ordering, the deviation bands, the blend, chip labels and the callout wording.

## Tasks & Acceptance

**Execution:**
- [x] `server/rules/documents/handler_performance.jdm.json` -- new v1 document carrying `onTrackDeviationPctMax` (-8), `attentionDeviationPctMin` (8), `severityWeightBp` (10000), `surgeryRateWeightBp` (1000), `litigationRateWeightBp` (1500), `complexityScoreMax` (100), `complexityHighMin` (65), `complexityMedMin` (40) -- the prototype's blend restated as basis points over three 0-100 inputs so the arithmetic is integer-exact; this directory is inside the 65/35 grep allowlist.
- [x] `server/rules/parameters.py` -- `HANDLER_PERFORMANCE_KEY`, a frozen `HandlerPerformance` block with `.of()` camelCase mapping, `__post_init__` refusing negative weights, an out-of-range score cap, and both ordering inversions **including equality** (`>=`, not `>`: equal cut-points do not invert anything, they silently make the `med` and `watch` bands unreachable), plus `handler_performance_for(db, as_of=None)` -- `ReserveBands` is the ordering-refusal template; no numeric literal may appear in this file.
- [x] `server/data/versions/20260818_0038_handler_performance_rules.py` -- revision `0038_handler_performance_rules`, `down_revision = "0037_fraud_flag_threshold"`, spelled-out `ROWS`, `EFFECTIVE_FROM = date(2026, 8, 11)` (a new key has nothing to supersede and must be live the moment its reader ships), downgrade deletes `(key, version)` -- migration 0028 is untouched: it loads only `benefit_params` and `derivation_thresholds`.
- [x] `server/services/derivations/complexity_blend.py` -- `ComplexityBand` StrEnum (`low|med|high`), a `HandlerMix` input, `HandlerComplexityDerivation.of(mix, params)`, registered as `handler_complexity` -- module named after the rule, not the value, or `test_no_derivation_module_is_named_after_the_value_it_exports` fails on the shadowed attribute.
- [x] `server/services/derivations/cycle_deviation.py` -- `CycleStatus` StrEnum (`on_track|watch|attention`), `CycleTimeStatusDerivation.of(deviation_pct, params)`, registered as `cycle_time_status` -- the second new derived value the epic context names.
- [x] `server/services/derivations/__init__.py` -- import both modules, export classes, enums and instances in `__all__`, append the narration paragraph -- a derivation not imported here does not exist to `get()`.
- [x] `server/services/worklist/sla.py` -- add `SAMPLE_COLUMNS` and `sample_of(row) -> SlaSample`, and rebuild `sla_strip` on them with no behaviour change -- this is what lets a second worklist module build samples without naming the allowlisted columns.
- [x] `server/data/repositories/claims.py` -- `select_claim_columns_with_handler(db, ctx, columns)`: the same scoped projection plus `aliased(AppUser).name` joined on `Claim.handler_id` -- no list surface resolves handler names today; this sets the precedent, mirroring `select_claim_detail`'s aliasing.
- [x] `server/services/worklist/benchmarks.py` -- new module: a `BenchmarkClaim` projection, frozen `HandlerBenchmark` and `HandlerBenchmarks`, the pure `benchmarks_of(...)`, `async handler_benchmarks(db, ctx, params, weights, thresholds, settings)`, and `BenchmarksNotPermitted` raised for any role **other than** `supervisor` and `analyst` before any read -- one scoped read, one pure fold, the 5.1 split. Three rules this module must obey and one it must not invent:
  - **The composite ranks on unrounded segment means.** `strip_of` publishes pick/approve to 1dp and settle to whole days; summing the *published* figures injects up to half a day of error, which is larger than the gap between four of the six seeded handlers and silently reorders them. Compute the composite from the unrounded means and publish it to 1dp, so the Avg Days column has the precision the order is decided at. The rank is the point of this table (FR-SUP-2); the rounding is a rendering concern and must not decide it.
  - **`rank` is null when `compositeDays` is null**, alongside the other four nullable figures. A `#` column numbering rows that are ordered by name is a ranking claim the data does not support.
  - **The gate is an allowlist.** `if ctx.role not in (UserRole.supervisor, UserRole.analyst)` — a denylist grants every future `UserRole` member the whole desk's performance by default, at import time, with no test failing. This endpoint publishes colleagues' figures; it is the wrong one to open by omission.
  - **Every parameter block is injected, none fetched.** The module docstring says "one scoped read, one pure fold" and the function says it takes its blocks "rather than fetching them" — so `thresholds_for`, `handler_performance_for` and `weights_for` are all loaded by the router and passed in, even where a derivation ignores the block it is built from.
- [x] `server/services/worklist/__init__.py` -- re-export the module, dataclasses, function and exception in the sorted `__all__`.
- [x] `server/api/routers/dashboard.py` -- `GET /dashboard/handler-benchmarks` taking only `ctx`, `db`, `response`; loads **all three** rule blocks (`handler_performance_for`, `weights_for`, `thresholds_for`) and `Settings` and passes them down; declares `FORBIDDEN_RESPONSE` locally rather than importing the claims copy, whose description names the edit capability and would be wrong in a read-only router's published OpenAPI; sets `no-store`; declares `{**UNAUTHENTICATED_RESPONSE, **FORBIDDEN_RESPONSE}`; returns `items` + leader/laggard + echoed thresholds + `rulesVersion`, with **no `total` and no `nextCursor`** and a docstring saying why (the row count is the handler count in scope; `ClaimActionsResponse` is the precedent).
- [x] `server/tests/seed_fixture.py` -- `expected_handler_benchmarks(name, role)` re-deriving rows, ranks, composites, deviations, complexity and pending counts from `seed_data.json` -- restated rules, never imported ones.
- [x] `server/tests/test_handler_benchmarks.py` -- every I/O Matrix row, plus: complexity over representative mixes (all-low severity, no surgery, no litigation → `low`; a heavy surgery-and-litigation mix → `high`) and at both cut-points from both sides; status at and either side of both deviation thresholds; composite equals the sum of the three segment averages; the full-portfolio six-name order asserted **literally as a sequence** (a set plus `rank == [1..6]` pins nothing about order); ranking deterministic with ties broken by handler name and then id, with the same-name pair's relative order actually asserted and both oracles carrying the same tie-break; the 403 raised before any claim read; route declares no parameters; smuggled query params change nothing; camelCase key set exact; no audit event written.
- [x] `server/tests/test_rules_engine.py` + `server/tests/test_rule_parameters.py` -- add the new key to `EFFECTIVE_DOCUMENTS` and `SEEDED_DOCUMENTS` with an `EXPECTED_*` values dict and a typed-block assertion; add `VALID_HANDLER_PERFORMANCE` and its helper to the parameter refusals and to `test_every_parameter_is_required`.
- [x] `server/tests/test_derivations.py` -- extend the registration assertions with `handler_complexity` and `cycle_time_status`.
- [x] `web/src/api/schema.d.ts` + `web/openapi.json` -- regenerate with `npm run generate:api`; never hand-edit.
- [x] `web/src/api/queryKeys.ts` + `web/src/api/dashboard.ts` -- a `dashboard.handlerBenchmarks` key and `useHandlerBenchmarks()` with `staleTime: 30_000`, exporting the response type off `components["schemas"]` -- no `select`, so `api/dashboard.ts` stays out of `ROOT_FILES`.
- [x] `web/src/features/dashboard/HandlerBenchmarkTable.tsx` -- a `readonly ColumnSpec[]` declaring the nine columns in order, a plain `<table>` with an `sr-only` caption and `scope="col"` headers, `data-testid="handler-row"` + `data-handler` per row, `font-mono` figures, complexity and status chips from local tone maps (`ok`/`warn`/`error` only — no new colors), the peer bar as a `width: ${ratio}%` fill, and the leader/laggard callout above it; loading skeleton rows, an empty state for a scope with no handlers, inline `role="alert"` on error. Four specifics the first pass got wrong:
  - **The Status chip carries its own `deviationPct`** as visible or screen-reader text. The band footnote quotes thresholds as percentages; a chip banded by a percentage the page never shows leaves the footnote measuring against nothing and the chip unexplainable from the screen.
  - **The Cycle Speed cell has an accessible name.** A `<td>` whose only content is a decorative `<span>` reads as an unnamed empty cell under a header promising a figure. Give the bar an `aria-label`/`title` carrying the composite, and do not let the tests assert `""` for that cell.
  - **The bar's direction is a decision, not an inheritance.** The prototype scales the fill against the *slowest* peer, so the worst performer gets the widest, reddest bar under a header reading "Cycle Speed". Either invert it so a longer bar means faster, or keep the prototype's direction and say why in the Dev Agent Record beside the other departures — the one thing not permitted is shipping it unexamined.
  - **The empty state keeps the band footnote.** The response populates thresholds and `rulesVersion` for an empty book on purpose; a bare paragraph throws away the only statement of which rules were in force.
- [x] `web/src/features/dashboard/DashboardPage.tsx` -- render the table in its own section after the two card rows, owning its own query state so a benchmark failure does not blank the KPI cards, and decide `aria-busy` across two queries explicitly rather than by accident.
- [x] `web/src/test/api-mock.ts` -- a `HANDLER_BENCHMARKS` fixture over a real persona's real book with no two figures coincidentally equal and a docstring naming **every** figure that diverges from the seed (a band flip is not a "nudge"), plus a contrast fixture that is internally reachable — retuned cut-points must be consistent with the scores and bands the rows carry, and must move a **complexity** chip, not only a status chip, or AC 2's complexity half has no component-level coverage — the `StubRoutes` field and the `stubApi` branch beside `/dashboard/summary`.
- [x] `web/src/features/dashboard/HandlerBenchmarkTable.test.tsx` -- pending → skeletons and no figures; data → all nine columns, with rank order asserted **as a list** (`querySelectorAll` → `toEqual`) because order is the contract; the contrast fixture re-ranks the same component; error → alert, no rows, no skeletons, `aria-busy` false; empty → the empty state, not a headless table.
- [x] `web/src/features/queue/noDerivation.test.ts` -- append Story 5.2's server-derived field names to `DERIVED_FIELDS` — **`rank` included**, since rendering `{index + 1}` in the `#` column instead of `row.rank` is the single most likely way to get this table wrong — add the scan-reaches assertion for `HandlerBenchmarkTable.tsx`, and add a re-sort line and a ratio-division line to `smells` -- re-ranking the table and dividing a composite by the peer average are precisely the temptations here.
- [x] `e2e/fixtures/seed.ts` -- add `handler` to `SeedClaim` and `expectedHandlerBenchmarksFor(persona)` returning what the cells read.
- [x] `e2e/stories/5-2-handler-performance-benchmarking.spec.ts` -- `test.describe("@story:5-2 @epic:5 handler performance benchmarking")`, exactly one `@smoke` happy path (supervisor login → nine columns, six ranked rows, callout), plus Jennifer Park's two-row scoped table, Ken Stoker's straddle table without Kaya, and a handler receiving 403 from the endpoint.

**Acceptance Criteria:**
- Given a supervisor or analyst session, when the dashboard loads, then a ranked table renders beneath the KPI cards with columns # · Handler · Cases · Cycle Speed · Avg Days · RTW % · Complexity · Pending Approvals · Status, ordered by ascending composite cycle time, **beneath** a leader/laggard callout naming the fastest and slowest handler. (Corrected in review: this AC and the story file's Task 4 both said the callout sits *under* the table "per the prototype's supervisor view", but the prototype puts it above — `docs/Workers_Comp_Prototype.html:1076` precedes the `<table>` at :1077. The design contract wins over the sentence that misquoted it.)
- Given the Status and Complexity columns, when they render, then every band came from the `handler_performance` document through a registered derivation, and superseding that document moves the chips with no code change.
- Given a handler session, when `GET /api/dashboard/handler-benchmarks` is called, then it answers `403` problem+json before reading a claim — this story's own answer to the access question `/dashboard/summary` left open, because a table of peers' performance is information about colleagues rather than a count of your own book.
- Given any two personas, when both load the table, then no figure could have come from a role branch: the only difference is the scope predicate the repository applied, and each persona's averages are recomputed within their own book rather than sliced from a wider one.
- Given the verification commands below, when they run, then server tests, ruff, mypy, vitest, eslint, tsc, the OpenAPI regeneration diff, `alembic check` plus a `downgrade 0037` → `upgrade head` round trip, and the full Playwright suite against a freshly reset stack all pass.

## Spec Change Log

### 2026-08-18 — Iteration 1 → 2 (composite precision)

**Triggering finding.** `[high]` The ranking was decided by display rounding and was already wrong on the only dataset that exists. The first implementation summed the three *published* segment averages from `sla.strip_of` — pick/approve at 1dp, settle at whole days. On the seeded portfolio Marcus Chen's true composite is 72.036 days and Fatima Al-Mansoori's is 72.667, so Marcus is 0.63 days faster; rounding settle to whole days (60.571 → 61 against 63.333 → 63) produced 72.4 against 72.3 and ranked the slower handler above the faster one. The whole-day rounding admits up to half a day of error, which is larger than the gap separating four of the six seeded handlers. This also put the code in direct contradiction with the I/O matrix inside `<intent-contract>`, which names the full-portfolio order.

**What was amended.** The `benchmarks.py` task now states that the composite is computed from unrounded segment means and published to 1dp; that `rank` is null whenever `compositeDays` is null; that the role gate is an allowlist; and that every parameter block is injected by the router rather than fetched inside the aggregate. The router, table, parameters, api-mock, noDerivation and server-test tasks carry the matching specifics. Nothing inside `<intent-contract>` was touched — the matrix was right and the mechanism was wrong.

**Known-bad state avoided.** A supervisor acting on a rank order, and on a leader/laggard callout, that is noise for any close pair — with no field on the response letting a reader tell a real gap from a rounding artifact, and no test pinning the sequence.

**KEEP — these survived review and must survive re-derivation:**
- The `SAMPLE_COLUMNS` + `sample_of` seam in `sla.py`. It satisfied the AD-2 column grep exactly as intended and `sla_strip` rebuilt on it with no behaviour change.
- The two derivation modules named after their rules (`complexity_blend.py`, `cycle_deviation.py`), with the blend parameters reaching `.of()` rather than `build`.
- The JDM document and migration `0038`: spelled-out `ROWS`, `EFFECTIVE_FROM = date(2026, 8, 11)`, a clean `downgrade 0037` → `upgrade head` round trip, and migration `0028` untouched.
- `select_claim_columns_with_handler` as the first list-surface handler-name join, mirroring `select_claim_detail`'s aliasing.
- The explicit, documented guards on a zero portfolio composite and a zero slowest composite — both correct and both to be kept verbatim.
- The error branch replacing the table rather than rendering stale rows beside an alert. This is Story 5.1's settled pattern and a reviewer's suggestion to keep stale rows was rejected.
- Purely additive edits to the four shared fixtures — no Prettier churn, per 5.1's review finding.
- The 2056/516/158 verification battery, all of it re-run rather than reported.

## Review Triage Log

### 2026-08-18 — Review pass
- intent_gap: 0
- bad_spec: 1: (high 1, medium 0, low 0)
- patch: 0
- defer: 2: (high 0, medium 0, low 2)
- reject: 3: (high 0, medium 0, low 3)
- addressed_findings:
  - `[high]` `[bad_spec]` The composite was summed from `strip_of`'s rounded segment averages, so display precision decided the ranking — and already misordered Marcus Chen and Fatima Al-Mansoori on the seeded portfolio, contradicting the I/O matrix inside the intent contract. Spec amended to fix the composite's precision, `rank`'s nullability, the gate's polarity and the parameter-injection rule; code reverted for re-derivation. Twelve further findings (deviation rendered nowhere, the empty Cycle Speed cell, the inverted bar, the incoherent contrast fixture, `rank` missing from the client-side guard, the equality-permitting threshold validation, the unasserted tie-break, the discarded empty-state footnote, the misstating fixture docstring, the third rule-document fetch, and two unrecorded prototype departures) were folded into the same amendment rather than patched onto code that is being re-derived.

### 2026-08-18 — Review pass 2 (post-loopback)
- intent_gap: 0
- bad_spec: 0
- patch: 14: (high 0, medium 4, low 10)
- defer: 0
- reject: 3: (high 0, medium 0, low 3)
- addressed_findings:
  - `[medium]` `[patch]` `figure()` rendered composites with `String(value)`, so a composite of `78.0` printed "78d" beside "72.7d" — reintroducing the very ambiguity the loopback existed to remove, with both oracles baking the truncation in. Split into a `compositeFigure()` at the server's published precision; a reintroduced `String(value)` now fails `each cell shows the figure the server put in its column`.
  - `[medium]` `[patch]` The component test written to catch `{index + 1}` in the `#` column could not catch it — every fixture had `rank === index + 1`, and `noDerivation.test.ts` explicitly delegates that job here, so the field the spec called "the single most likely way to get this table wrong" had no coverage at any level. A new unrankable-scope fixture (all ranks null, rows in name order) closes it; a reintroduced `{index + 1}` now fails `a scope the server could not rank numbers no row at all`.
  - `[medium]` `[patch]` Three rule-document reads happened before the 403, while four docstrings claimed the refusal came "before any read" — true of *claim* reads only. `require_benchmarks_access(ctx)` is now called in the router ahead of the three loads, with a test that a refused caller triggers no rule-document load at all.
  - `[medium]` `[patch]` The contrast fixture carried the base fixture's composites, ratios, deviations and portfolio figure with the names reversed, so the pair never exercised a different portfolio composite or bar denominator — while its docstring claimed "a different scope, not the same rows reversed". Rebuilt as a genuinely different scope against a 55.0d portfolio with every band reachable from the score it publishes.
  - `[low]` `[patch]` All three complexity blend weights could be zero, scoring every handler 0 and collapsing the desk into one chip silently; now refused at load, with a test that two zeros still load because severity-only is a policy.
  - `[low]` `[patch]` The empty state's band footnote read "against this portfolio's — —"; the clause is now omitted rather than filled with an em dash, and the test tightened from a substring to a whole-sentence match.
  - `[low]` `[patch]` The primary fixture's mandated docstring listed five seed values for six handlers, silently attributing Dante's 3 to Sarah. Recounted from the seed: 1 · 2 · 1 · 0 · 1 · 3.
  - `[low]` `[patch]` The contrast fixture's docstring attributed the two moved status chips to the wrong handlers.
  - `[low]` `[patch]` Neither oracle carried the implementation's `handler_id` tie-break although the task text required it, and the e2e oracle broke name ties with `localeCompare` against the server's code-point ordering — two collations for one tie-break. Both now sort `(composite, name, handlerId)` with a code-point comparator.
  - `[low]` `[patch]` `aria-busy` keyed on `isPending` while the skeleton branch keyed on `data === undefined`; one predicate now drives both.
  - `[low]` `[patch]` The single-handler callout claimed "the only handler with claims in this portfolio" from a condition establishing only "one *rankable* row"; reworded, and the branch — which no fixture covered — now has one.
  - `[low]` `[patch]` The Cycle Speed cell carried its sentence twice (`sr-only` plus `title`), risking a double announcement through a tooltip unreachable by keyboard and touch; `sr-only` kept as the single mechanism.
  - `[low]` `[patch]` `rulesVersion` was argued for in two docstrings as something the footnote states, and rendered nowhere; docstrings corrected to what is true (it rides along unrendered, as 5.1's does). `benchmarks.py`'s "one pure fold" now says plainly that `strip_of` is called per group solely for the RTW rate and why the duplicated segment arithmetic is accepted over re-implementing a rate AD-2 gives one owner.
  - `[low]` `[patch]` AC 1 and the story's Task 4 both placed the leader/laggard callout *under* the table "per the prototype's supervisor view", but the prototype puts it above (`Workers_Comp_Prototype.html:1076` precedes the table at :1077). The AC was corrected to the design contract rather than the code to the misquotation.
  - Rejected: two duplicate-display-name findings (a mislabelled callout and a React key collision) are unreachable — `AppUser`'s `UniqueConstraint("name", "role")` forbids two handler-role rows sharing a name; and a `Decimal` inexactness finding concerns ties differing below any published precision, where no ordering is observable.

## Design Notes

**1. "Reuse Epic 1's registered derivations" resolves to `sla.py`, because there are none.** The story file and the epic context both say the cycle-time components come from registered derivations. They do not exist: the registry has `days_open` and `days_to_settlement`, and neither is what the SLA strip uses. Story 1.5 reads three nullable claim columns and folds them in `sla.strip_of`. So AD-2 is satisfied by calling `strip_of` per handler group — which `services/worklist/__init__.py`'s own docstring already names this story for — and the grep test enforces it far more strictly than a registry lookup would: a second averaging cannot even be written outside `sla.py` without naming a forbidden column.

**2. The missing-segment rule generalises the prototype's one fallback instead of copying its two.** `renderSV` substitutes the portfolio settle average when a handler has none (`settle||pAvgSettle`) but lets a missing pick or approve average fall through as `0`, which reads as "instant". The server substitutes the scoped portfolio's average for *any* missing segment, uniformly. One rule, no zero that means "unknown", and on the seeded portfolio it never fires — every handler has settled claims — so the ported figures are unchanged.

**3. `complexityHighMin` is 65 and that is a coincidence, not a shared rule.** `riskHighMin` is also 65, and the two will be confused unless the difference is written down: risk bands a *single claim's* severity score; complexity bands a *handler's blended mix* of average severity, surgery rate and litigation rate. They answer different questions about different subjects and must be able to move independently, which is the argument for a separate document. Both live in files the 65/35 grep allowlists; `rules/parameters.py` does not, so its validation must be expressed without literals.

**4. Pending approvals count claim status, not payment weeks.** Two things here are called "pending approval": `ClaimStatus ∈ {initial, ch_assessment_process}` (the queue scorer's `pendingApprovalStatuses`) and `ScheduleWeekStatus.pending_approval` on a table `services/financials` owns. The prototype counts the first, the queue already means the first, and it is one column on a row this aggregate is already reading. The second would need a new cross-service scoped aggregate and an AD-12 argument, for a column the design contract never asked for.

**5. Only Kaya Johnson has litigated claims — all three of them.** Every other handler's litigation rate is 0, and Jennifer Park's scoped table has no litigation at all. Two consequences: the blend's litigation term is exercised on exactly one handler on the seeded portfolio, so it needs unit coverage over synthetic mixes rather than seed coverage; and no assertion may lean on litigation being non-zero for a persona, which is the mistake `test_a_scoped_supervisor_sees_strictly_less_than_the_portfolio` was patched for in 5.1.

**6. The straddle case is the reverse of what the story file describes.** Kaya Johnson is *assigned* John Deere but handles none of its seven claims — Liam O'Sullivan does. So the roster-vs-claims property is best tested through Ken Stoker (Deere + Lockheed), whose table must contain Liam and Fatima and must **not** contain Kaya even though she holds a Deere assignment. Grouping over the scoped claim set gets this right by construction; enumerating "the handlers in my employers" gets it wrong.

## Verification

**Commands:**
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker uv run pytest` -- expected: all green including the new suite; nothing previously passing regressed.
- `cd lineworker/server && uv run ruff check . && uv run ruff format --check . && uv run mypy .` -- expected: clean.
- `cd lineworker/server && uv run alembic check` -- expected: no drift; then `uv run alembic downgrade 0037_fraud_flag_threshold && uv run alembic upgrade head` completes clean.
- `cd lineworker/web && npm run generate:api` -- expected: `src/api/schema.d.ts` and `openapi.json` change once and are byte-identical on a second run.
- `cd lineworker/web && npm test && npm run lint && npm run typecheck && npm run build` -- expected: vitest green including `noDerivation.test.ts`, eslint no new warnings, tsc and build clean.
- `cd lineworker && docker compose -f deploy/compose.e2e.yaml up -d --build --wait` then `cd e2e && npm run typecheck && npm test` -- expected: full suite green against a freshly reset stack, with the new `@story:5-2` spec and the `@smoke` set included.

## Auto Run Result

Status: done

**Implemented.** A ranked handler-performance table on the dashboard Story 5.1 built, and the scoped server aggregate behind it. `handler_benchmarks` takes one `select_claim_columns_with_handler` read — the first list surface in the console to resolve handler names, joining `aliased(AppUser)` the way `select_claim_detail` does — groups it by assigned handler, and folds each group through the *same* `sla.strip_of` the top bar's SLA strip uses. Composite cycle time, the peer bar ratio, the deviation percentage and the rank are all decided on unrounded `Decimal` segment means and published once at one decimal. Complexity and cycle-time status are two new registered derivations over a new `handler_performance` rule document; `GET /dashboard/handler-benchmarks` is parameter-free, `no-store`, audit-free, and refused to anyone outside a named supervisor/analyst allowlist.

**Files changed.** New — `rules/documents/handler_performance.jdm.json`, `data/versions/20260818_0038_handler_performance_rules.py`, `services/derivations/complexity_blend.py` and `cycle_deviation.py`, `services/worklist/benchmarks.py`, `tests/test_handler_benchmarks.py`, `web/src/features/dashboard/HandlerBenchmarkTable.{tsx,test.tsx}`, `e2e/stories/5-2-handler-performance-benchmarking.spec.ts`. Modified — `services/worklist/sla.py` (the `SAMPLE_COLUMNS`/`sample_of`/`segment_means_of` seam that keeps the three SLA column names inside their one allowlisted module), `rules/parameters.py`, `data/repositories/claims.py`, `api/routers/dashboard.py`, the derivations and worklist package exports, five server test modules, `web/src/api/{queryKeys,dashboard,schema.d}.ts`, `DashboardPage.tsx`, `noDerivation.test.ts`, `api-mock.ts`, `e2e/fixtures/seed.ts`.

**Review findings.** Two passes. Pass 1 produced one `bad_spec` (high) and a full loopback: the composite had been summed from `strip_of`'s *published* averages, so whole-day settle rounding decided the ranking and already ranked Fatima Al-Mansoori above the faster Marcus Chen — half a day of admitted error against a 0.63-day gap, contradicting the I/O matrix inside the intent contract. The spec was amended outside the contract, the code reverted, and twelve further findings folded into the same amendment. Pass 2 produced no `bad_spec` and 14 patches (4 medium, 10 low), 3 rejects, 0 intent gaps. Two of the mediums were tests that did not test what they claimed, and both fixes were bite-proofed by reintroducing the bug and naming the test that caught it.

**Deferred.** Two entries, both pre-existing and both recorded with evidence: `noDerivation.test.ts` cannot see a derivation written one prop-rename past a component boundary and its operator set excludes `===`; and `DerivationThresholds` still permits `riskMedMin == riskHighMin`, the same equality hole this story's new parameters were amended to close, left alone because tightening a shipped block's validation can turn a currently-loading document into a startup failure.

**Verification.** Re-run independently at every stage rather than taken on report: ruff and `ruff format --check` clean over 198 files, mypy clean over 198, **2069 pytest** (1971 at 5.1), `alembic check` clean plus a `downgrade 0037` → `upgrade head` round trip, `npm run generate:api` byte-identical on a second run, **521 vitest** across 33 files, eslint 0 errors (11 pre-existing warnings), tsc and build clean, and **158 Playwright** against a freshly reset stack with all `@story:5-2` specs green.

**Residual risks.** The two deferred items. Beyond them: the bar still scales so the *slowest* peer fills the track — the prototype's direction, kept deliberately and recorded, because the bar sits beside Avg Days and inverting makes it a reciprocal of the adjacent number; and the unrankable state (every composite null when the whole scope is missing a duration segment) is implemented, documented and tested but unreachable on any seeded scope, so it has never run against real data.
