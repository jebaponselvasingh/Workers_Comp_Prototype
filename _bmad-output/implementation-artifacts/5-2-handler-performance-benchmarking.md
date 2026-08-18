# Story 5.2: Handler Performance Benchmarking

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a WC supervisor,
I want my handlers ranked with workload and speed context,
so that I can spot who needs a check-in before SLAs slip.

## Acceptance Criteria

1. **Given** the performance table, **when** it renders, **then** handlers rank by composite cycle time (pick + approve + settle) with columns # · Handler · Cases · Cycle Speed (bar vs peers) · Avg Days · RTW % · Complexity (Low/Med/High blending severity, surgery %, litigation %) · Pending Approvals · Status (FR-SUP-2, FR-SUP-B).
2. **Given** the Status column, **when** computed, **then** On Track / Watch / Attention derives from cycle-time deviation vs the portfolio with deviation thresholds as JDM parameters (FR-SUP-3, AD-8), and a leader/laggard callout renders.
3. **Given** the complexity score, **when** computed, **then** it comes from a single registered function (AD-10) with unit tests over representative handler mixes.
4. **Given** a scoped supervisor, **when** the table loads, **then** it includes only handlers with claims inside the persona's employer scope, computed server-side (AD-7).

## Tasks / Subtasks

- [x] Task 1: Per-handler benchmark aggregate in `services/worklist` (AC: 1, 4)
  - [x] One aggregate (e.g. `handler_benchmarks(ctx)`) grouping the caller's scoped claims by assigned handler and computing per handler: case count, avg pick days, avg approve days, avg settle days, **composite cycle time = pick + approve + settle averages** (the ranking key), RTW %, pending-approval count
  - [x] Reuse Epic 1's single per-claim pick/approve/settle/RTW derivations (Story 1.5's SLA aggregation inputs) — same registered functions, grouped per handler; never a second cycle-time implementation (AD-2, AD-10)
  - [x] Group over the **scoped claim set**, not over handler rosters: a handler whose book straddles supervisor scopes (the Kaya/Deere case is normative) appears with only the claims inside the viewer's scope, and handlers with zero in-scope claims don't appear (AD-7)
  - [x] Server computes the Cycle Speed bar ratio (each handler's composite vs the peer best/worst in this scope) — the SPA renders the ratio, it does not derive it (AD-1)
- [x] Task 2: Complexity classification — single registered function (AC: 3)
  - [x] One registered function (in the `services/derivations` registry, invoked by the worklist aggregate) mapping a handler's claim mix → Low / Med / High, blending avg severity, surgery %, and litigation % of their in-scope caseload
  - [x] Blend weights/cut-points are JDM parameters (AD-8); the blend arithmetic is typed Python; enum values snake_case (`low|med|high`), UI owns display labels
- [x] Task 3: Status derivation + leader/laggard callout (AC: 2)
  - [x] On Track / Watch / Attention from each handler's composite-cycle-time deviation vs the scoped-portfolio average; the two deviation thresholds (watch, attention) are JDM parameters in a benchmark JDM document (AD-8)
  - [x] Aggregate response carries the leader (fastest composite) and laggard (slowest / attention-flagged) so the UI callout renders server-provided facts (AD-1)
- [x] Task 4: Read-only API + table UI (AC: 1, 2, 4)
  - [x] `GET /api/dashboard/handler-benchmarks` — thin router, camelCase, session-required, no caller-supplied scope (AD-7); returns ranked `items` (small bounded set — full list, no cursor needed, document why)
  - [x] Table in `web/features/dashboard/` (TanStack Table v8): rank #, handler name, cases, cycle-speed bar (peer-relative, token colors), avg days, RTW %, complexity chip, pending approvals, status chip (ok/warn/error hues for On Track/Watch/Attention)
  - [x] Leader/laggard callout line under the table per the prototype's supervisor view; loading/empty/error states (NFR-3) — empty scope renders a defined empty state, not a broken table
  - [x] Row click drill-through is Story 5.5 — rows are non-interactive this story (component ready for an `onRowClick` later)
- [x] Task 5: Tests (AC: all)
  - [x] Unit tests: complexity function over representative handler mixes — all-low-severity/no-surgery/no-litigation → Low; heavy-surgery mix → High; boundary values at each JDM cut-point (AC 3 names this explicitly)
  - [x] Unit tests: status derivation at/around both deviation thresholds; composite cycle time = sum of the three per-stage averages; ranking order stable and deterministic (tie-break documented, e.g. by handler name)
  - [x] Scoping test: Jennifer Park's table contains only handlers with Toyota/GM/3M claims, with per-handler counts restricted to those employers; David Bline sees all handlers over all 100 claims
  - [x] Playwright `e2e/stories/5-2-handler-performance-benchmarking.spec.ts` tagged `@story:5-2 @epic:5`, one `@smoke` happy path: supervisor login → ranked table renders with all 9 columns + callout

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

The handler benchmarking table + its server aggregate, on the dashboard page Story 5.1 built. It is NOT: KPI cards (5.1), charts (5.3 — still no Recharts; the cycle-speed bar is a simple token-styled div/progress element, not a chart library), the top-30 worklist (5.4), or handler-row drill-through (5.5). Read-only: no mutation, no audit events, role gates capability (AD-7). The prototype's ranked handler table with cycle-speed bars and leader/laggard callout (UX-DR7) is the design contract.

### Architecture compliance (binding ADs for this story)

- **AD-2:** cycle-time components come from the exact same single aggregation family Epic 1's SLA strip uses — the benchmark table and the top-bar strip can never disagree on what "avg settle" means.
- **AD-7:** grouping happens over the repository-scoped claim set; supervisor scope is employer-based, not a handler hierarchy — never enumerate "the supervisor's handlers", derive handlers from in-scope claims.
- **AD-8:** deviation thresholds and complexity blend parameters are JDM (DB-versioned, effective-dated); ranking/blend arithmetic is typed Python reading its tunables from ZEN.
- **AD-10:** complexity has exactly one registered computing function; per-claim inputs (severity, surgery flag, litigation flag, RTW status) resolve through their existing registered derivations.
- **AD-1/AD-9:** every displayed number (including bar ratios and deviation %) is server-computed; TanStack Query under shared `queryKeys`.

### Data notes

- **Tables created: none** (Epic 5 creates no tables). Reads: claim (handler assignment, severity, surgery/litigation flags, lifecycle dates, RTW status), app_user (handler names), user_employer_assignment (scope), payment/approval state for pending-approvals.
- **Writes: none**; no AD-12 ownership impact. New JDM content: a handler-benchmark document (deviation thresholds, complexity blend parameters) via the established rules-versioning mechanism.
- Handler identity: claims carry their assigned handler (seeded from the prototype's HANDLER_MAP in Story 1.2's persona/assignment seed). Handlers are `app_user` rows — join for display names.

### UX notes

- UX-DR7: "ranked handler table with cycle-speed bars and leader/laggard callout" — match the prototype's supervisor view column set and density.
- Status chips use the ok/warn/error token hues (On Track = ok, Watch = warn, Attention = error); complexity chips are neutral/info toned. Same tokens as queue and KPI cards — no new colors.
- **Design-token ruling:** prototype palette is LIGHT and canonical; epics.md's "dark console aesthetic" is a documented discrepancy (Story 1.1 Dev Notes).
- NFR-3: loading skeleton, empty state (scoped supervisor with no in-scope handlers), inline error state.

### Testing requirements

- Unit (the ACs demand these by name): complexity function over representative handler mixes + JDM boundary values; status/deviation thresholds; composite = pick + approve + settle; scoping (Jennifer Park vs David Bline).
- Web: Vitest on the table component states (loading/data/empty/error).
- E2E (AD-15): `e2e/stories/5-2-handler-performance-benchmarking.spec.ts` tagged `@story:5-2 @epic:5`, exactly one `@smoke` happy path, against the freshly reset e2e stack. **Story cannot reach `review`/`done` until it passes.** Selectors: role first, `data-testid` second.

### Project Structure Notes

- Server: aggregate + status logic in `server/services/worklist/`; complexity function registered in `server/services/derivations/`; JDM document under `server/rules/` content (DB-versioned).
- Web: table component in `web/src/features/dashboard/`; query hook + key in `web/src/api/`; regenerate the OpenAPI client.
- Depends on Story 5.1's dashboard page shell and Epic 1's SLA derivations (1.5). Pending-approval counts use Epic 3's assessment/payment approval state (3.4/3.5).

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 5.2]
- FR-SUP-2/3/B: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]
- AD-2 single SLA aggregation, AD-8 JDM parameters (handler-benchmark thresholds named explicitly), AD-10: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- AD-7 employer-based supervisor scope + Kaya/Deere straddle case: [Source: ARCHITECTURE-SPINE.md#AD-7]
- SLA aggregation origin: [Source: _bmad-output/planning-artifacts/epics.md#Story 1.5]
- UX-DR7 handler table: [Source: _bmad-output/planning-artifacts/epics.md#UX Design Requirements]
- Design-token discrepancy ruling: [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]
- AD-15 E2E gate: [Source: ARCHITECTURE-SPINE.md#AD-15]

## Dev Agent Record

### Agent Model Used

claude-opus-5[1m] (Claude Opus 5, 1M context), via the `bmad-dev-auto` workflow. **Iteration 2** — the first implementation was reverted for a design defect (the composite was ranked on display-rounded segment averages); this is a re-derivation against the amended spec, not a patch on top of it.

### Debug Log References

- **The ranking was being decided by whole-day rounding, and it was already wrong on the only dataset that exists.** `sla.strip_of` publishes pick and approve to one decimal and settle to whole days. Summing those three admits up to half a day of error, which is larger than the gap between four of the six seeded handlers: Marcus Chen's true composite is 72.036 days and Fatima Al-Mansoori's 72.667, but their settle segments round 60.571 → 61 and 63.333 → 63, producing 72.4 against 72.3 and putting the *slower* handler above the faster one. Fixed by adding `sla.segment_means_of` — `strip_of`'s own three averages with the quantization left off, both reading one `_segment_values` so there is still exactly one definition of "average settle" (AD-2) — summing in `Decimal`, and rounding **once** to one decimal for display. Verified independently against `seed_data.json` before writing code: the resulting order is the spec's I/O matrix exactly (Liam · Kaya · Marcus · Fatima · Sarah · Dante), and `test_the_full_portfolio_ranks_in_exactly_this_sequence` asserts it as a literal sequence.
- **`sla.py`'s "status is decided on the rounded value" ruling is not overturned, and the distinction had to be written down.** That ruling is about a *verdict on one figure against a target beside it*, and it still governs every tile. A rank is a comparison *between* figures, and nothing that exists only to be displayed may decide one. Both functions now live beside a docstring paragraph saying so, because the next reader will otherwise "fix" one of them into the other.
- **Rounding last also removes the objection the first implementation was built on.** Quantization is monotonic, so a row ranked above another can never *show* a larger Avg Days figure; two rows can show the same figure in a different order, which is the honest reading — they differ by less than the column's precision. The column is published at one decimal (finer than its own slowest segment) precisely so a reader can tell a real gap from a rounding artefact.
- **The e2e oracle needed exact arithmetic, not tenths.** `fixtures/seed.ts` previously carried the composite as an integer number of tenths, which is the rounded sum by another name. It now sums three `{sum, count}` fractions over a common denominator — claim counts are at most 100, so every product stays a whole integer far inside `Number.MAX_SAFE_INTEGER` — and rounds once. The TS oracle and an independent Python computation over `seed_data.json` agree on all three personas' rows, bars, deviations and published composites.
- **`noDerivation.test.ts` cannot catch a literal `{index + 1}`, and pretending otherwise would be worse than the gap.** Adding `rank` to `DERIVED_FIELDS` catches `.sort()` on it, `row.rank - 1`, and any comparison — but a bare `index + 1` names no payload field, so no textual guard can see it. The field-list comment now says where the guard stops, and the component test pins the handler↔rank *pairing* against the contrast fixture (whose pairing differs from the default one) so the index version fails there instead.
- **`SIM300` fires on `assert module.CONSTANT == literal`.** ruff reads an upper-case attribute as the constant side and demands the Yoda form. Assigning to a lower-case local first is the readable way out.
- **The e2e stack had to be rebuilt, not just restarted.** `compose.e2e.yaml up -d --build --wait` re-ran migrations through the new `0038` on a fresh volume; the full 158-test suite then passed against it.

### Completion Notes List

- **AC 1 — nine columns, ranked server-side, over one scoped read.** `services/worklist/benchmarks.py` does one `select_claim_columns_with_handler` read behind `employer_scope` and one pure fold. Handlers are grouped from the returned claims, never from a roster (AD-7), which is what makes Ken Stoker's straddle table correct by construction rather than by a filter. The nine columns are a declared `readonly ColumnSpec[]` so the order can be checked against `renderSV` line by line, and both the component test and the Playwright spec assert the row order **as a list**.
- **AC 1 — the composite is ranked unrounded and published to one decimal.** See the Debug Log. `rank`, `compositeDays`, `cycleSpeedPct`, `deviationPct` and `cycleStatus` are five nullable fields that travel as one condition; `rank` is null exactly when `compositeDays` is, because a `#` beside a row of em dashes on rows that are in *name* order is a ranking claim the response itself denies one field away.
- **AC 2 — both chips come from registered derivations parameterised by a new JDM document, and the footnote quotes the response.** `handler_complexity` and `cycle_time_status` are two registered derivations (AD-10) whose tunables arrive at `.of()` because `Derivation.build` takes only `DerivationThresholds` — `NextBatchDateDerivation.of(as_of, weekdays)`'s precedent. `test_a_superseded_rule_document_moves_the_chips_and_the_published_bands` inserts a v2 effective today and watches the chips *and* the published bands move with nothing deployed, while the deviations and complexity bands they were computed from stay put.
- **AC 2 — the Status chip carries the deviation it was banded on.** The footnote quotes both thresholds as percentages; a chip banded by a number the page never shows would leave that sentence measuring against nothing, and a reader could not tell "Watch" at -7% from "Watch" at +7% — the difference between a desk that is fine and one about to need a check-in. Formatted with `Intl`'s `signDisplay: "exceptZero"` rather than a comparison against zero, so the guard has nothing to exempt.
- **AC 3 — the complexity blend is one registered function with unit coverage over synthetic mixes.** Only Kaya Johnson carries any litigation on the seeded portfolio (three claims), so the litigation term cannot be exercised from the seed; `test_handler_benchmarks.py` grades all-low / heavy-surgery-and-litigation books, both cut-points from both sides, the half-up boundary at 64.5, the cap, and a doubled litigation weight.
- **AC 4 — a scoped supervisor's figures are recomputed, not sliced.** Marcus Chen and Sarah Williams appear in both David Bline's and Jennifer Park's tables with identical case counts and composites (properties of their whole books, both inside her scope) but *different* deviations and bar percentages, which is only true if the portfolio row and the peer set were recomputed inside her 27 claims.
- **The role gate is an allowlist, and that is the access decision `/dashboard/summary` deferred.** `PERMITTED_ROLES = {supervisor, analyst}`, refused before any read. A denylist would have admitted `UserRole.system` on the day it was declared and every future member thereafter, silently, with no test failing — `test_the_refusal_happens_before_any_claim_is_read` is parametrized over `set(UserRole) - PERMITTED_ROLES` so a new member arrives there without anyone remembering.
- **Every parameter block is injected by the router; the aggregate fetches none.** `handler_performance_for`, `weights_for` and `thresholds_for` are all loaded in `dashboard.py` and passed down — including `thresholds`, which both new derivations ignore, because a block half injected and half fetched is the arrangement nobody can read off a signature. `handler_benchmarks` now has no `await` in its body other than the one scoped read.
- **`FORBIDDEN_RESPONSE` is declared locally in the read-only router.** The `claims.py` copy describes "the edit capability", which is accurate there and misleading in this router's published OpenAPI, where the refusal is about *whose* numbers these are.
- **Nothing was built ahead.** No Recharts and no chart of any kind (5.3 — the dependency is still absent); no top-30 worklist (5.4); no row click, `onRowClick`, href or drill-through (5.5); no new KPI card; no mutation, no audit event, no new table. `Cache-Control: no-store`, and the route declares zero parameters.
- **Tests:** **2066 server** (+95 over the 5.1 baseline of 1971 — the new `test_handler_benchmarks.py` covering every I/O-matrix row plus the composite's precision, the literal six-name sequence, both tie-breaks, the allowlist, and the AD-2 identity; plus the new parameter refusals, the new document's evaluated values and the two new registry names). **519 vitest** (+15 — nine columns, the rank *pairing* under two fixtures, every cell's text, the named Cycle Speed cell, the bar widths, the callout, the footnote under a seeded and a retuned document, complexity **and** status chips moving under retuned cut-points, the em dash, skeletons, the inline alert with the KPI cards still standing, and the empty state keeping its footnote). **158 Playwright** (+8, `@story:5-2 @epic:5`, one `@smoke`).
- **Verified live.** e2e stack rebuilt from scratch (`compose.e2e.yaml`, :8081): 158/158. `alembic check` clean, `downgrade 0037_fraud_flag_threshold` → `upgrade head` round trip clean with `check` still clean. `npm run generate:api` byte-identical on a second run. ruff + `ruff format --check` + mypy clean across 198 files; eslint 0 errors (11 pre-existing warnings, none new); tsc and `vite build` clean for `web/`, tsc clean for `e2e/`. No new runtime or dev dependency.

### Recorded discrepancies and deliberate departures

- **No TanStack Table, against Task 4's wording.** The dependency is installed with zero uses in `src/`; the app's one real table (`bills/PaymentScheduleCard.tsx`) is hand-written. What TanStack buys is client-side sorting, filtering and paging over a dataset the browser holds — and this browser holds one page of already-ranked rows (six, or two for a scoped supervisor) that it *must not* re-sort, because the ranking is a function of a rule document's thresholds and a scope predicate it does not have. `getSortedRowModel` would be exactly the derivation `noDerivation.test.ts` exists to forbid.
- **The cycle-speed bar keeps the prototype's direction: full width is the *slowest* handler in scope.** Examined rather than inherited. The alternative — inverting it so a longer bar means faster, which the column's name argues for — was rejected because the bar sits immediately beside the Avg Days column and with this direction its length *is* that column drawn: two handlers ten percent apart in days are ten percent apart in bar length, and the two encodings can be compared against each other. Inverting makes the bar a reciprocal of the number next to it, monotonic but no longer proportional, with nothing on screen to say so. The nine column names are fixed by AC 1, so the units are stated in the bar's own accessible name (`"64.1 days, 82% of the slowest cycle time in this portfolio"`, as screen-reader text and as a `title`) rather than left to be inferred from its length.
- **The Cycle Speed cell is named, and the bar is `aria-hidden`.** A `<td>` whose only content is a decorative span reads as an unnamed empty cell under a header promising a figure. One accessible name per cell, not a name and a nested graphic competing to be it — and the tests assert that name rather than `""`.
- **The empty state keeps the band footnote.** The response populates the thresholds and `rulesVersion` for a scope with no handlers on purpose: "nothing in this book" says nothing about which rules were in force, and that footnote is the only statement of it on a screen with no rows to explain.
- **The missing-segment rule generalises the prototype's one fallback instead of copying its two.** `renderSV` substitutes the portfolio's settle average (`settle || pAvgSettle`) but lets a missing pick or approve fall through as `0`, which reads as "instant" and ranks that handler first. Here *any* missing segment is substituted by the scoped portfolio's own. It never fires on the seeded book, so no ported figure changes.
- **Pending Approvals counts `ClaimStatus ∈ pendingApprovalStatuses`, not `ScheduleWeekStatus.pending_approval`.** The prototype counts the first, the queue scorer already means the first, and it is one column on a row this aggregate is reading anyway. The second is on a table `services/financials` owns (AD-12) with no scoped count.
- **`complexityHighMin` is 65 and so is `riskHighMin`, and that is a coincidence.** One bands a claim's severity score, the other a handler's blended mix; they answer different questions about different subjects and must be able to move independently. Both oracles restate them as separate constants so the most plausible way to get this wrong — pointing complexity at the risk threshold — cannot pass.
- **`HandlerPerformance` refuses *equal* cut-points, where `DerivationThresholds` permits them.** Equal cut-points are not an inversion; they delete a band. For complexity that means no desk is ever Medium; for the deviation bands, whose edges are both inclusive, it means the two conditions overlap on their shared value and which one wins is decided by the order the derivation tests them in. The identical hole in the pre-existing `riskMedMin`/`riskHighMin` pair was left alone and recorded in `deferred-work.md`: tightening a shipped block's validation can turn a currently-loading document into a hard startup failure, which wants its own change.
- **`web/openapi.json` is gitignored** (`lineworker/.gitignore:18`), so only `src/api/schema.d.ts` is tracked. A second `npm run generate:api` produced no further diff.

### File List

**New — server**

- `lineworker/server/rules/documents/handler_performance.jdm.json`
- `lineworker/server/data/versions/20260818_0038_handler_performance_rules.py`
- `lineworker/server/services/derivations/complexity_blend.py`
- `lineworker/server/services/derivations/cycle_deviation.py`
- `lineworker/server/services/worklist/benchmarks.py`
- `lineworker/server/tests/test_handler_benchmarks.py`

**Modified — server**

- `lineworker/server/services/worklist/sla.py` (`SAMPLE_COLUMNS`, `sample_of`, `DURATION_SEGMENTS`, `_segment_values`, `segment_means_of`; `strip_of` and `sla_strip` rebuilt on them with no behaviour change)
- `lineworker/server/rules/parameters.py` (`HANDLER_PERFORMANCE_KEY`, the frozen `HandlerPerformance` block with its refusals, `handler_performance_for`)
- `lineworker/server/services/derivations/__init__.py` (the new exports and the narration)
- `lineworker/server/services/worklist/__init__.py` (the `benchmarks` module and its exports)
- `lineworker/server/data/repositories/claims.py` (`select_claim_columns_with_handler`)
- `lineworker/server/api/routers/dashboard.py` (`GET /dashboard/handler-benchmarks`, the local `FORBIDDEN_RESPONSE`, the two response models)
- `lineworker/server/tests/seed_fixture.py` (`expected_handler_benchmarks` and the eight restated parameters)
- `lineworker/server/tests/test_derivations.py` (the two new registry names)
- `lineworker/server/tests/test_rule_parameters.py` (`VALID_HANDLER_PERFORMANCE`, the block's refusals)
- `lineworker/server/tests/test_rules_engine.py` (`EFFECTIVE_DOCUMENTS`, `EXPECTED_HANDLER_PERFORMANCE`, the typed-block assertion)

**New — web / e2e**

- `lineworker/web/src/features/dashboard/HandlerBenchmarkTable.tsx`
- `lineworker/web/src/features/dashboard/HandlerBenchmarkTable.test.tsx`
- `lineworker/e2e/stories/5-2-handler-performance-benchmarking.spec.ts`

**Modified — web / e2e**

- `lineworker/web/src/api/schema.d.ts` (regenerated)
- `lineworker/web/src/api/queryKeys.ts` (`dashboard.handlerBenchmarks`)
- `lineworker/web/src/api/dashboard.ts` (`useHandlerBenchmarks` and the four exported types)
- `lineworker/web/src/features/dashboard/DashboardPage.tsx` (the second query and the table section)
- `lineworker/web/src/features/queue/noDerivation.test.ts` (`rank` + Story 5.2's field names, the scan-reaches assertion, three new smells)
- `lineworker/web/src/test/api-mock.ts` (`HANDLER_BENCHMARKS`, `_RERANKED`, `_EMPTY`, the `StubRoutes` field and the `stubApi` branch)
- `lineworker/e2e/fixtures/seed.ts` (`handler` on `SeedClaim`, `expectedHandlerBenchmarksFor` and its helpers)
