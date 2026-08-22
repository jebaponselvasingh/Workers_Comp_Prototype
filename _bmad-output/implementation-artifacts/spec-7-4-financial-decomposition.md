---
title: 'Story 7.4 — Financial Decomposition'
type: 'feature'
created: '2026-08-22'
baseline_revision: '0dc7a6b0bb55280be944c0fac03c1bcb3ac4bd95'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: true # fifteen findings were patched across both halves of the stack, and the pass changed behaviour rather than only prose: cursor semantics gained a third compared document, the drill list's verdict load was re-ordered behind the other twenty-four facets, an API response gained a field, and four client surfaces changed what they say. What a follow-up should read is whether the narrowing in `drill_through_claims` preserves `total` and the chip labels under every facet combination (the population handed to `select` moved, `_applied` deliberately did not), whether `bands_version` is minted and compared on exactly the paths that load the document, and whether the two new mutation-closed guards hold against a second verdict written some third way
context:
  - '{project-root}/_bmad-output/implementation-artifacts/7-4-financial-decomposition.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-7-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-7-3-segmentation-universal-drill-down.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-7-1-fraud-analytics-workspace.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The analyst workspace can narrow a book nine ways and still cannot answer what it costs. `charts.portfolio_charts` publishes exactly one money figure — `by_employer.paid_cents` — and nothing anywhere aggregates reserve, aggregates paid-plus-reserve, counts Epic 3's reserve verdict over a portfolio, or compares a surgical claim with a non-surgical one. The reserve verdict exists per claim only (`reserve_check_for_claim`, one claim, one materialization, one bill read), so "how well is this book reserved" is currently answerable only by opening claims one at a time.

**Approach:** A fourth analyst section — **Financial** — carrying three folds over the segmented book: paid / reserve / projected totals with a breakdown by any one segmentation dimension; the reserve-verdict distribution, counted from Epic 3's own `reserve_check_from_rows` fed by bulk reads rather than re-derived; and the surgery and litigation cost-driver pairs. Every figure drills, which costs the drill vocabulary one appended facet (`reserveVerdict`) — surgery and litigation are already facets.

## Boundaries & Constraints

**Always:**
- **The verdict is Epic 3's, reached by Epic 3's function.** The portfolio distribution calls `services/financials/reserve.reserve_check_from_rows` — the same function the case file and the Bills tab reach the verdict through — over bulk-loaded `payment_schedule_week` and `bill` rows. No exposure-vs-reserve arithmetic, no band comparison and no reading of `light_ratio_bp`/`heavy_ratio_bp` outside `classify_reserve` (AD-2, AD-8).
- **All five verdicts are published, not three.** `ReserveVerdict` has five members and the seeded book is mostly `closed_final` and `indeterminate`; a Light/Adequate/Heavy-only chart would silently drop the majority of the portfolio and its total would contradict `claims_in_scope`. Labels come from `RESERVE_VERDICT_LABEL` and tones from `VERDICT_ACCENT` — imported, never restated.
- **One money basis per figure, named on the surface.** `paid_cents` is the registered `total_paid` derivation (the static `paid_*` columns) because that is what the Epic 5 KPI cards and the by-employer chart already show; the third figure is the registered `total_claim_projected` derivation and is published as `projected_cents`, never as "incurred" — `claim_financials.py` refuses to spread that naming discrepancy and this story inherits the refusal, not the label.
- **Aggregates fold in Python over scope-predicated reads** (`claims.py:462-486`'s refusal of a `predicate` parameter): no `Segmentation`, no verdict and no cohort ever becomes a SQL `WHERE`.
- Analyst-only, read-only, `no-store`, gate order `header → role → rules → service`, reusing `require_fraud_analytics_access` — one analyst-workspace allowlist, as the values endpoint already does.
- Money is integer cents everywhere below `web/src/lib/money.ts`; averages are floor division and are `None`, never `0`, for an empty cohort.
- Every card carries the four exclusive states and a zero-result state that distinguishes an empty book from an emptied segment (the Story 7.3 high finding).

**Block If:**
- Reaching the seeded reserve-verdict spread requires changing seed data or the reserve bands — the distribution is what it is; report it, never tune it.
- Making the portfolio figures agree with the Bills tab requires changing `total_paid`'s definition — that is the unowned product question at `deferred-work.md:501` and moves Epic 5's KPI cards with it.

**Never:**
- No materialization on this path. An analyst route is read-only; `reserve_check_for_claim`'s per-claim refresh-then-read is not looped over a book.
- No new tables, no writes, no mutation routes, no audit event (this story exports nothing — 7.5 owns that).
- No trend-over-time financials (7.2), no export (7.5), no actuarial modelling, no change to `services/financials`' arithmetic, and no second reserve-band read.
- No client-side money: no `.reduce`, no `paid + reserve`, no division but `formatCents`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Unfiltered decomposition | `GET /dashboard/financials` as analyst | Portfolio `paidCents`/`reserveCents`/`projectedCents`, breakdown by the default dimension, both cost-driver pairs | No error expected |
| Segmented | `?filter[sector]=Aerospace&groupBy=state` | Every figure folded over the intersection; `claimsInScope` is the segmented count | No error expected |
| Impossible segmentation | A filter set no claim satisfies | 200; all totals `0`, no groups, cohorts present with `claimCount: 0` and `averageProjectedCents: null`; every card shows its emptied-segment state | Never an error |
| Truncated breakdown | `groupBy=icd10` with more groups than the cap | Top `limit` groups by projected cents, `truncated: true`, `groupCount` = all; portfolio totals stay whole-book | No error |
| Unknown `groupBy` | `?groupBy=nonsense` | 422 problem+json before the service runs | FastAPI enum validation |
| Adequacy distribution | `GET /dashboard/financials/reserve-adequacy` | One entry per `ReserveVerdict` member in vocabulary order, `total` = `claimsInScope`, band edges + `bandsVersion` on the payload | No error |
| Verdict drill | Click a distribution segment | Drill list under `filter[reserveVerdict]=…` plus the active segmentation; its `total` equals the segment's count | No error |
| Cost-driver drill | Click "surgery" | Drill list under `filter[surgery]=true` plus the active segmentation | No error |
| Verdict facet without verdicts | `filter[reserveVerdict]` set on a code path that did not load them | Loud `RuntimeError` at the fold, never a silently empty list | Programming error, not a response |
| Non-analyst | Supervisor/handler requests either route | 403 problem+json, `no-store`, before any claim row or rule document is read | `_fraud_forbidden` |
| Scope tamper | Analyst appends `?employerId=…&scopeAll=true` | Byte-identical response to the honest read | Params ignored |

</intent-contract>

## Code Map

**Server — what is consumed, not touched**
- `server/services/financials/reserve.py` — `ReserveVerdict:131` (five members), `ReserveClaim:163` (`stage`, `doi`, `recovery`, `reserve`), `ReserveCheck:177`, `classify_reserve:302`, `indemnity_terms:465`, **`reserve_check_from_rows:490`** (the shared seam — both existing callers go through it), `reserve_check_for_claim:529` (materializes; *not* the aggregate path), `ReserveBands` + `reserve_bands_for` at `server/rules/parameters.py:876,940`.
- `server/services/derivations/claim_money.py` — `PaidColumns` Protocol, `TotalPaidDerivation.of`, registered `total_paid` (`:119`, with the "Total incurred" naming note at `:122`). `claim_financials.py` — `TotalClaimProjectedDerivation.of(paid_to_date_cents, reserve_cents)`, registered `total_claim_projected`, whose docstring is the ruling this story follows on the word "incurred".
- `server/data/repositories/claims.py` — `employer_scope:62`; `select_claim_columns_with_employer_and_employee:232` (the 7.3 sibling, un-aliased joins, adds `employer_short_name`); **`select_payment_schedule_for_claims:741`** and **`select_bills_for_claims:770`** (both scoped, both grouped by business id — already exist); the twice-recorded refusal of a `predicate` parameter `:325`, `:462-486`.
- `server/services/worklist/priority_claims.py:895-901` — the precedent for bulk `weeks`+`bills` reads folded per claim with no materialization.

**Server — the vocabulary being extended**
- `server/services/worklist/segmentation.py` — `Segmentation:82` (ten fields), `SEGMENTATION_KEYS:138`, `SEGMENTATION_WIRE_KEYS:150`, `SegmentedClaim:227`, `LabelledClaim:279`, `Computers.of:320`, `narrowed:434`, `applied_of:458`, `to_drill_filters:704`, and the four import-time `if`-raises (`VocabularyDivergence:155`) that are the model for this story's own guards.
- `server/services/worklist/drill_through.py` — `DrillClaim:152`, `DrillFilters:284` (24 fields; append-never-insert `:325`), `FILTER_KEYS:412`, `WIRE_KEYS:428` (+ set assert `:462`), `DrillFlags`/`_flags_of`, `_PREDICATES:620` (+ totality assert), `_COERCERS`, `select`, `drill_through_claims:1244`.
- `server/services/worklist/fraud.py` — the analyst-service template: `require_fraud_analytics_access:205`, `FraudClaim:226`, `_Computers.of:315`, pure `panel_of:507`, `_projection:1294` (named kwargs), `fraud_panel:1336` (one `await`), `FraudPanel:366` (threshold edges published with the figures), `fraud_red_flags:1415` (`read_clauses` — the injected-reader precedent).
- `server/services/worklist/charts.py` — `Distribution:164`, `EmployerPaid:149`, `INJURY_TYPE_LIMIT:115`/`STATE_LIMIT:116` (display caps as module constants, not rules-tier).
- `server/services/worklist/trends.py` — the `value=None`-never-`0` sentinel (`TrendPoint:684`) and bucket-vocabulary-independent-of-series rule.
- `server/services/worklist/__init__.py:191-204` (tier-1 module tuple), `:205-346` (per-module blocks, alphabetical), `:347` (`__all__`).

**Server — routes**
- `server/api/routers/dashboard.py` — `_segmentation:1714` + `SegmentationDep:1859`; `FRAUD_ANALYTICS_FORBIDDEN_RESPONSE:1667`; `_fraud_forbidden:1680`; templates `fraud_rate_breakdowns:2221` and `trends:2796` (gate order `:2290-2298`); `drill_claims:1200` with its 24 `filter[…]` params `:1204-1396` and field-by-field `DrillFilters(...)` `:1566-1590`; response models live in this file over `ApiModel` (`server/api/schemas.py:19`), money fields are `*_cents: int` (`EmployerPaidResponse.paid_cents:515`).

**Web**
- `web/src/features/shell/routes.ts:37`, `WorkspaceNav.tsx` (`SECTION_ROUTES:75`, `inSection:82`, `DESTINATIONS:122`, `SegmentationSection:171`, `hrefWithFilters:213`), `DashboardShell.tsx:65`, `App.tsx:70-81` (analyst-only inner guard).
- `web/src/features/dashboard/segmentation/useSegmentation.ts` — `WorkspaceUrl:54`, `ControlName:47`, `pickOption:180`, `clearRefused:151`.
- `web/src/features/dashboard/drill/filters.ts` — `FILTER_KEYS:54`, `SEGMENTATION_KEYS:120` (and why a click-target facet stays out, `:110-119`), `toQueryParams:224`, `withSegmentation:339`, `isFiltered:442`, `NO_MATCHING_CLAIMS:462`, `drillHref:481`, `FILTER_LABEL:543`, `FRAUD_BAND_VALUE_LABEL:592` (the value-label model), `VALUE_LABEL:680`.
- `web/src/api/queryKeys.ts` — analyst leaves `:202-311`, blast-radius lesson `:190-201`, the `trends` note naming this story `:274-283`. `web/src/api/dashboard.ts` — `useTrends:612`, `useFraudRates:440`, `toTrendParamsKey:570`, `TREND_DEFAULTS:547`, the no-`select` rule.
- `web/src/features/dashboard/charts/` — `ChartFrame.tsx:40`, `DistributionDonut.tsx:68` (`total === 0` rule `:127`), `DistributionBars.tsx:72` (`formatValue`), `chartTheme.ts` (`RISK_FILL:52`, `CHART_HEIGHT:214`). `KpiCard.tsx:57` (`drill:98`).
- `web/src/features/claim-detail/labels.ts:169` `RESERVE_VERDICT_LABEL`; `reserveAccent.ts:27` `VERDICT_ACCENT` (light = error, heavy = warn — stated `:12-19`); `web/src/lib/money.ts:33` `formatCents` (the only division by 100; its docstring names this epic).
- `web/src/features/queue/noDerivation.test.ts` — `ROOTS:50`, `DERIVED_FIELDS:204`, `FORBIDDEN:597` (unconditional `.sort(`/`.reduce(`), per-file registration block `:693`.

**Tests / oracles**
- `server/tests/test_segmentation.py` — pure/DB halves `:1-45`, `context_for:921`, `get_json:940`, `_counting:949`, role allowlist `:863`, every-`UserRole` refusal `:873`, refusal-before-read `:1000`, refusal-before-rule-load `:1021`, one-scoped-read `:1046`/`:1064`, Hypothesis strategies `:718-836`, camelCase key set `:1440`, no-audit/no-cache `:1517-1527`. `test_trend_analytics.py:1930` — the openapi parameter-set contract.
- `server/tests/seed_fixture.py` — oracle doctrine `:1-13`, separate-constants ruling `:1229-1234`, Story 7.2 banner `:1470`, Story 7.3 banner `:1846`.
- `e2e/fixtures/seed.ts` — independent-oracle doctrine `:4-18`, `formatCents:1350`, `RESERVE_VERDICT_LABEL:1533`, `expectedDrillClaimsFor:2855`, `drillUrl:2909`, `workspaceUrl:4314`, 7.1 banner `:2919-2938`. `fixtures/login.ts:27-43` (`analyst` = David Bline, `scope_all`), `fixtures/test.ts:20-38`. `e2e/stories/7-3-…spec.ts:87-88` (tag + single `@smoke`).
- **Seed facts that shape the tests:** 100 claims; `surgery_required` on 39, `litigation_flag` on 3 (all three open); paid and reserve are mutually exclusive per claim (62 settled carry paid, 38 open carry reserve); portfolio paid 167,049,700¢, reserve 47,050,100¢.

## Tasks & Acceptance

**Execution:**
- [x] `server/services/financials/reserve.py` -- add `reserve_checks_for_claims(db, ctx, claims, *, bands) -> Mapping[str, ReserveCheck]` over `select_bills_for_claims` + `select_payment_schedule_for_claims`, delegating per claim to `reserve_check_from_rows`, plus an `IdentifiedReserveClaim(ReserveClaim, Protocol)` carrying `claim_id` -- the bulk verdict belongs in the module that owns the verdict (AD-2/AD-12); the docstring must state why it does **not** materialize (read-only analyst path; `priority_claims.py:895` is the precedent) and what that costs.
- [x] `server/services/worklist/decomposition.py` -- new module: `FinancialClaim` projection (satisfies `LabelledClaim`, `PaidColumns` and `IdentifiedReserveClaim` structurally), `BreakdownDimension` StrEnum whose members are the **segmentation wire spellings** with an import-time raise pinning it to `SEGMENTATION_WIRE_KEYS`, `BREAKDOWN_LIMIT`, pure folds `decomposition_of(...)` and `adequacy_of(caseload, verdicts)`, and the two services `financial_decomposition(...)` (one scoped read) and `reserve_adequacy(...)` (three reads) -- `fraud.py` is the template, including named-kwarg row projection and one-pass counters.
- [x] `server/services/worklist/drill_through.py` -- append `reserve_verdict` as the 25th facet (`filter[reserveVerdict]`) through all five declaration sites — `DrillFilters`, `WIRE_KEYS`, `_PREDICATES`, `_COERCERS` and `DrillFlags`/`_flags_of` — with the verdict handed in rather than derived from the row, and a loud raise if the facet is set while no verdict map was supplied -- appended, because `FILTER_KEYS` order is the chip order of every drill URL already issued.
- [x] `server/services/worklist/drill_through.py` (service) -- `drill_through_claims` loads verdicts via `reserve_checks_for_claims` + `reserve_bands_for` **only** when `filters.reserve_verdict is not None` -- the existing one-scoped-read guarantee survives for every other caller, and the extra reads are visible exactly where they are paid for.
- [x] `server/services/worklist/__init__.py` -- re-export the new module and its public names in the established three tiers -- match the existing surface.
- [x] `server/api/routers/dashboard.py` -- `GET /dashboard/financials` (`groupBy` control param, defaulting to `employerId`, + `SegmentationDep`) and `GET /dashboard/financials/reserve-adequacy`, both analyst-gated in the `header → role → rules → service` order, with `ApiModel` response models declared in this file and every money field `*Cents`; append `filter[reserveVerdict]` to `GET /dashboard/claims` and build it into `DrillFilters` field by field -- two routes rather than one so a card's failure isolates and the read counts stay legible.
- [x] `web/openapi.json` + `web/src/api/schema.d.ts` -- regenerate with `npm run generate:api` **after** all server route work -- CI fails on an undiffed client.
- [x] `web/src/features/dashboard/drill/filters.ts` -- append `reserveVerdict` to `FILTER_KEYS` (not `SEGMENTATION_KEYS` — it is a click target, `:110-119`'s rule), one spelled-out `toQueryParams` line, its `FILTER_LABEL` entry and a value-label map registered in `VALUE_LABEL` reusing the imported verdict labels -- the order must match the server's or every existing chip row renumbers.
- [x] `web/src/features/dashboard/segmentation/useSegmentation.ts` -- add `"groupBy"` to `ControlName` -- a control name outside that union cannot be written to the URL.
- [x] `web/src/api/queryKeys.ts` + `web/src/api/dashboard.ts` -- `financials` and `reserveAdequacy` leaves under an unowned prefix, `toFinancialParamsKey` beside `toTrendParamsKey`, two hooks with `placeholderData: keepPreviousData`, `staleTime: 30_000` and no `select` -- a `groupBy` change must keep the previous render and report busy only on the card that changed.
- [x] `web/src/features/dashboard/financial/FinancialPage.tsx` + `TotalsCard.tsx` + `BreakdownCard.tsx` + `ReserveAdequacyCard.tsx` + `CostDriverCard.tsx` -- the section: three KPI tiles with drill targets, a `groupBy` selector over a `DistributionBars` money chart, the verdict donut (imported labels and accents), and the two paired cohort comparisons; each card takes `segmented` and renders its own emptied-segment copy -- clone `FraudPage.tsx`'s composition, `FraudDistributionCard.tsx` for the donut, `SiuPipelineCard.tsx` for the paired bars.
- [x] `web/src/features/dashboard/charts/chartTheme.ts` -- `RESERVE_VERDICT_FILL: Record<ReserveVerdict, string>` total over the five members, reusing the existing semantic tones so the donut agrees with `VERDICT_ACCENT` (light = error, heavy = warn) -- a chart needs a fill where the card needs a class; two spellings of one semantic, so state the correspondence where the map is declared.
- [x] `web/src/features/shell/routes.ts` + `WorkspaceNav.tsx` + `App.tsx` -- `FINANCIAL_ROUTE = "/dashboard/financials"`, a fourth `DESTINATIONS` entry, `SECTION_ROUTES` membership, and the route inside the existing analyst-only guard -- `SECTION_ROUTES` is also the segmentation bar's mount predicate, so the section inherits the bar for free.
- [x] `web/src/features/queue/noDerivation.test.ts` -- register every new `features/dashboard/financial/*` file in the scan block with its own prose (the tempting derivations are paid + reserve, the cohort delta and the verdict banding) and append the genuinely-new wire names to `DERIVED_FIELDS` -- read the alternation before adding to it.
- [x] `server/tests/test_financial_decomposition.py` -- pure half (breakdown grouping and ranking, truncation leaving portfolio totals whole, cost-driver cohorts including the empty-cohort `None` average, integer-cents arithmetic with no float, all five verdicts published in vocabulary order) and DB half (`context_for` with narrowed and empty-book analysts, the role allowlist over every `UserRole`, refusal before read *and* before rule load, one-scoped-read for the decomposition and exactly three for adequacy, reconciliation of every drillable figure against the drill list's `total`) -- covers the I/O matrix.
- [x] `server/tests/test_financial_decomposition.py` (agreement + property) -- **the verdict-agreement test**: for sampled seeded claims, the bucket the portfolio distribution puts a claim in equals `reserve_check_for_claim`'s verdict for that same claim; plus Hypothesis properties that the breakdown groups sum to the portfolio totals under any `Segmentation`, and that a cost-driver pair's two cohorts partition the segmented book exactly -- AC 2's "never a re-derivation" is only checkable as agreement.
- [x] `server/tests/test_drill_through.py` + `test_segmentation.py` -- update the facet-order and route-parameter-set contracts for the 25th facet, and cover `filter[reserveVerdict]` (a real narrowing, its extra reads, and the raise when verdicts are absent) -- a contract test that was loosened rather than extended is not a contract.
- [x] `server/tests/seed_fixture.py` -- a Story 7.4 banner restating the money and cohort constants independently: paid/reserve/projected totals, the surgery and litigation cohort splits, and the expected verdict distribution -- every constant read into an answer, spelled separately even where it coincides with an existing one.
- [x] `web/src/features/dashboard/financial/FinancialPage.test.tsx` -- contrast fixtures that pull the cohorts apart, card states, cents formatted only at render, `groupBy` and every drill click asserted through the URL -- reads accessible output, never Recharts internals.
- [x] `e2e/fixtures/seed.ts` -- `expectedFinancialsFor(persona, filters)` and `expectedAdequacyFor(persona, filters)` restated from the raw seed, sharing no helper with the server or with `expectedStatsFor` -- 7.1, 7.2 and 7.3 each shipped an oracle that carried the implementation's own fold; this one re-derives.
- [x] `e2e/stories/7-4-financial-decomposition.spec.ts` -- `@story:7-4 @epic:7`, exactly one `@smoke`: analyst login → Financial renders totals + adequacy → apply a segmentation filter → figures recompute → click a cost-driver cohort → drill list → read-only claim view; plus a URL-restore test, an impossible-combination zero-result test across every card, a verdict-segment drill whose list total equals the segment count, a role-gate check and a smuggled-parameter check asserting byte-identical bodies *and* the on-screen figure against the oracle -- the AD-15 done-gate.
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- append this story's entries: the seventh O(scope) fold endpoint for the push-down count, the `total_paid` basis now carrying a decomposition surface (`:501`), and the still-unprovable narrowed analyst at HTTP level -- the count is only useful if it keeps being kept.

**Acceptance Criteria:**
- Given an analyst session, when the Financial section renders, then paid, reserve and projected totals plus a breakdown by the selected segmentation dimension are all folded server-side over the scoped book, and no figure on screen was computed in the browser.
- Given a segmentation filter, when it is applied or changed, then every figure in the section recomputes over the intersection, previously rendered cards stay visible while the new data is in flight, and only the affected card reports busy.
- Given the reserve-adequacy distribution, when it renders, then each claim's bucket is the verdict Epic 3's own computation returns for that claim, all five verdicts are represented with the case file's labels and tones, and the band edges and rules version travel with the figures.
- Given any KPI tile, breakdown group, verdict segment or cost-driver cohort, when it is clicked, then the Story 5.5 list opens carrying that slice plus the active segmentation as independently clearable chips, and its `total` equals the figure that was clicked.
- Given a segmentation no claim satisfies, when the section renders, then every card shows an emptied-segment state distinct from an empty book, the chips stay clearable, and nothing errors.
- Given a supervisor or handler session, when either financial route is requested directly, then a 403 problem+json is returned before a claim row is read and before a rule document is loaded.
- Given a URL carrying the section's filter and `groupBy`, when it is shared or reloaded, then the whole state restores and scope re-resolves server-side, and no parameter added to that URL widens the caller's book.

## Spec Change Log

## Review Triage Log

### 2026-08-22 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 15: (high 0, medium 8, low 7)
- defer: 2: (high 0, medium 1, low 1)
- reject: 1: (high 0, medium 0, low 1)
- addressed_findings:
  - `[medium]` `[patch]` Both AD-2 guards this story added were decorative, and the reviewer proved it by mutation: a complete second `classify_reserve` written into `decomposition.py` as two local bindings and a cross-multiplication passed `test_no_module_outside_the_classifier_compares_a_reserve_band`, because the regex only sees an operator beside a band's *literal name*. Closed the demonstrated evasion with `BAND_RENAME` — binding an edge to a differently-named local is a comparison in waiting, while `light_ratio_bp=bands.light_ratio_bp` is publication — and verified the reviewer's own mutation now fails. The docstrings no longer overclaim: they state that an edge passed as a bare argument still escapes, that the scan is Python-only, and that AC 2's real enforcement is the per-claim agreement test.
  - `[medium]` `[patch]` The Story 7.4 reserve oracle in `seed_fixture.py` was dead code — sabotaging `RESERVE_LIGHT_RATIO_BP` to 99,999 and `RESERVE_HEAVY_RATIO_BP` to 1 left 98 tests passing, against a banner asserting that a document retune "has to break a test rather than move an expectation with it". `expected_reserve_verdict` is now read into the agreement test for every one of the 100 claims, so the claim-level path is checked against the restated rule rather than against itself; the same sabotage now fails. `RESERVE_VERDICT_ORDER`, which nothing read and which the test module already restates for a stated reason, was deleted rather than wired — a constant nothing reads protects nothing.
  - `[medium]` `[patch]` `filter[reserveVerdict]` loaded verdicts for the entire scoped caseload *before* any other facet narrowed it, on a route with no role gate — so one query parameter turned any authenticated session into a whole-book schedule-and-bill materialisation, with `filter[employerId]` beside it reducing nothing. The other twenty-four facets now run first (`_without_reserve_verdict`), and the population handed to the two reads is pinned at 62 rather than 100 by watching the call rather than counting statements.
  - `[medium]` `[patch]` The cursor compares `weights_version` and `thresholds_version` precisely because a document that decides the population re-partitions the list a cursor is an offset into — and `reserve_bands`, which decides every bucket behind the new facet, had no field. Added `bands_version`, minted and compared only when the facet is set (so no other drill URL pays the read, and existing cursors stay byte-identical), with a test that mints the route's own cursor and moves one field.
  - `[medium]` `[patch]` The `groupBy` select reverted under the analyst's hand: with `keepPreviousData` the held answer is the *previous* dimension's, so `data?.breakdown.dimension ?? groupBy` put "Employer" back after a click on "ICD-10", left the heading disagreeing with the URL, and drilled the wrong facet in that window. Now `TrendsPage`'s `shown` predicate exactly — asked-for while in flight, the server's echo once it lands.
  - `[medium]` `[patch]` AC 2's "only the affected card reports busy" was neither implemented nor tested: `isPending` is `false` throughout a refetch when a placeholder is on screen, so **no** card reported busy while its figures were stale. Threaded `isPlaceholderData && isFetching` per query, gave both cards their own `aria-busy`, and added the two regression tests — both verified to fail against the pre-patch code.
  - `[medium]` `[patch]` Two money words meant two quantities one drill click apart. The Paid tile read "Paid to date" while publishing `total_paid` (the static columns), which is *not* the case file's "Total Paid To Date" (`paidToDateCents`, five figures on an open claim where this reads zero); it now reads "Total Paid", the portfolio dashboard's own label for the same derivation. The projected tile's caption said only "paid plus reserve", true of both it and the case file's "Total Claim (Projected)" over the other basis, and now names which paid.
  - `[medium]` `[patch]` `ReserveAdequacyResponse` published `bandsVersion` alone on the argument that one field could only name one document — but the route also loads `derivation_thresholds` and narrows the distribution through the `risk` and `age_band` computers it builds, so `filter[severityBand]=high` moved every bucket through edges the payload never named. That was an argument for a second field: `rulesVersion` now travels beside it, and the contract test that had locked the omission in asserts both.
  - `[low]` `[patch]` `RESERVE_VERDICT_FILL` and `VERDICT_ACCENT` disagreed at birth on `closed_final` — `muted-text` against `faint`, on the bucket holding 62 of 100 seeded claims — while the new map's docstring asserted the two were one semantic in two spellings. Aligned to the accent map's token.
  - `[low]` `[patch]` The adequacy card named its own region with its 60-word footnote: `aria-labelledby` pointed at the band-explanation paragraph, so a screen reader announced all three sentences as the region's accessible name. It has an `sr-only` heading now, `BreakdownCard`'s pattern.
  - `[low]` `[patch]` `FINANCIALS_BY_ICD` was a payload no route can emit — three rows under `limit: 3` where `BREAKDOWN_LIMIT` is 12, claiming six further groups while its items summed to the whole portfolio, so the very derivation it exists to catch ("put the remainder in an Other bucket") computed a remainder of exactly zero against it. Rebuilt as twelve rows holding 24 of 40 claims and 2,940,000 of 4,000,000 projected cents.
  - `[low]` `[patch]` A test written to be read miscounted its own allowlist ("grew from three entries to six"; `BAND_HOME` has five, as the same docstring says nine lines later).
  - `[low]` `[patch]` `decomposition.py` commented "last label wins" over a `setdefault`, where the first wins; the comment now describes the behaviour and why it is safe.
  - `[low]` `[patch]` `financial_decomposition` built `SegmentationComputers.of(thresholds)` twice per request — two chances for a later edit to hand the filter and the fold different computers over one caseload.
  - `[low]` `[patch]` `GROUP_BY_ORDER` was declared identically in two files whose own docstrings argue that a second copy of the list would be "an eleventh name for ten things"; it is exported from one now.

## Design Notes

**1. Why the verdict is bulk-read and not materialized.** `reserve_check_for_claim` refreshes the schedule before reading it because two *claim-level* surfaces must not disagree, and that refresh is a write. An analyst aggregate cannot take it: it is a read-only route by role capability, and materializing a whole book on a dashboard request would be a write amplification with an audit story nobody asked for. So the portfolio path reads the stored rows — exactly as Story 5.4's worklist already does for its bulk action labels — and reaches the verdict through `reserve_check_from_rows`, which is the same function `reserve_check_for_claim` calls after refreshing. The residual gap is a claim whose week boundary passed since anyone last opened it; the agreement test pins the shared computation, which is what AD-2 actually requires, and the gap is named in the card's footnote rather than hidden.

**2. Five buckets, because three would lie.** The AC says Light/Adequate/Heavy and the shipped vocabulary has five members, two of which — `closed_final` (settled, no exposure left) and `indeterminate` (bills not on file) — hold most of the seeded book. Publishing three would leave a donut whose slices sum to a fraction of `claimsInScope` while the card said "portfolio", which is precisely the class of finding Story 7.3's review produced twice. Five buckets, the case file's own labels, and the two non-verdict buckets explained on the card.

**3. Three money words, three quantities.** `paid_cents` = `total_paid` (the static columns) so this section cannot disagree with the KPI cards or the by-employer chart; `reserve_cents` = the column; the third = `total_claim_projected`, published as `projected_cents` and labelled "Total (projected)". The epic's word for it is "incurred" and the case file already uses "Total incurred" for paid-only — a discrepancy `claim_money.py` records and `claim_financials.py` explicitly refuses to spread. This story adopts the refusal: the footnote says what the figure sums, and the naming question stays with its owner. Likewise `deferred-work.md:501`'s finding — that `total_paid` excludes ~20% of paid bills and expenses — is inherited and footnoted, not fixed here, because fixing it moves Epic 5.

**4. `BreakdownDimension` members are the camelCase facet names, not snake_case.** The convention is snake_case enums, and this enum is the exception for the reason Story 7.3 exists: its members *are* facet names, `groupBy=severityBand` must be the same word as `filter[severityBand]`, and the client composes the group's drill target and its label from that one key. A snake_case spelling would be an eleventh name for ten things and a translation layer whose only job is to be correct forever in both directions. Pinned to `SEGMENTATION_WIRE_KEYS` at import.

**5. The 25th facet earns its two reads.** `surgery` and `litigation` are already facets, so the cost-driver drills cost nothing. The verdict is not a column and cannot become one (`test_no_derived_columns.py`), so `filter[reserveVerdict]` is the only way a distribution segment can open its own claims — AC 2's drill. The reads happen only when the facet is set, which keeps the shipped one-scoped-read guarantee true for every existing drill URL and makes the cost visible exactly where it is incurred.

**6. Truncation without a lying total.** The pure fold returns every group; the service truncates to `BREAKDOWN_LIMIT` for the wire and publishes `groupCount` and `truncated`, while the portfolio totals stay whole-book — `charts.py`'s `Distribution` rule. That is also what keeps the "groups sum to the total" property assertable: it is a property of the fold, not of the payload, and the card's footnote says "top N of M".

**7. Litigation is three claims, and the comparison has to survive that.** All three seeded litigated claims are open, so their paid total is zero and a paid-only comparison would read as "litigation costs nothing". Each cohort therefore publishes claim count, all three money figures and a floor-divided average, with the average as the headline and `None` — never `0` — for an empty cohort, following the trends sentinel.

## Verification

**Commands:**
- `cd lineworker/server && uv run ruff check . ../deploy && uv run ruff format --check . ../deploy` -- expected: clean.
- `cd lineworker/server && uv run mypy .` -- expected: clean under strict.
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker uv run pytest` -- expected: all pass, including the verdict-agreement test, both read-count guards, the facet-order and route-parameter contracts, and `test_no_derived_columns.py`.
- `cd lineworker/web && npm run generate:api && git diff --exit-code -- src/api/schema.d.ts openapi.json` -- expected: no diff once the regeneration is committed.
- `cd lineworker/web && npm run lint && npm run typecheck && npm test` -- expected: clean; `noDerivation.test.ts` green including the scan-reaches-its-files assertion.
- `cd lineworker/e2e && npm run typecheck` -- expected: clean.
- `docker compose -f lineworker/deploy/compose.e2e.yaml up -d --build --wait && cd lineworker/e2e && npx playwright test --grep "@story:7-4\b|@smoke\b"` -- expected: the new spec and every `@smoke` pass against the freshly reset stack.

## Auto Run Result

Status: done

**What was implemented.** The analyst workspace's fourth section. Three folds over the segmented book: paid / reserve / projected totals with a breakdown by any one of the ten segmentation dimensions, the portfolio reserve-verdict distribution counted from Epic 3's own `reserve_check_from_rows` over bulk-loaded schedule and bill rows, and the surgery and litigation cost-driver pairs. Every figure drills, which cost the drill vocabulary its 25th facet — `filter[reserveVerdict]`, the first facet whose predicate needs rows the claim read does not carry, appended and paid for only when it is set. Three money words were kept meaning three quantities: `paidCents` is the registered `total_paid` derivation so this section cannot disagree with Epic 5's KPI cards, and the third figure is `total_claim_projected` published as `projectedCents` and labelled "Total (projected)" rather than "incurred", inheriting `claim_financials.py`'s refusal to spread that naming discrepancy.

**Files changed.**
- `server/services/worklist/decomposition.py` — new: the projection, `BreakdownDimension` pinned at import to the segmentation vocabulary, the two pure folds and the two services (one scoped read; three for adequacy).
- `server/services/financials/reserve.py` — `reserve_checks_for_claims`, the bulk verdict path, materialization-free and argued as such.
- `server/services/worklist/drill_through.py` — the 25th facet through all five declaration sites, the conditional verdict load narrowed by the other twenty-four facets first, and `bands_version` on the cursor.
- `server/api/routers/dashboard.py` — `GET /dashboard/financials`, `GET /dashboard/financials/reserve-adequacy`, `filter[reserveVerdict]` on the drill list.
- `web/src/features/dashboard/financial/*` (5 components) plus `chartTheme.ts`, `drill/filters.ts`, `useSegmentation.ts`, `api/{queryKeys,dashboard,schema.d}.ts`, `shell/{routes,WorkspaceNav}.tsx`, `App.tsx`.
- Tests: `server/tests/test_financial_decomposition.py` (78), `test_reserve_block.py` (two new guards), `seed_fixture.py`'s Story 7.4 oracles, `FinancialPage.test.tsx` (20), `e2e/fixtures/seed.ts`, `e2e/stories/7-4-financial-decomposition.spec.ts` (9), and the contract tests in five existing server files re-pinned to 25 facets.

**Review findings.** Two adversarial passes produced 18 distinct findings after deduplication: **15 patched** (0 high, 8 medium, 7 low), **2 deferred**, 1 rejected, 0 intent gaps, 0 spec defects — no loopback. The two most valuable were mutation-proven by the reviewer rather than argued: both AD-2 guards this story added could be defeated by binding a band edge to a local first, and the reserve-band oracle in `seed_fixture.py` was dead code that survived having its constants sabotaged. Both are closed and both closures were re-verified by re-running the reviewer's own mutations. The rest were a whole-book read reachable from an ungated route, a cursor missing the third document that decides its population, and a cluster of surfaces saying something the data did not support — a control reverting under the user's hand, no card reporting busy during a refetch, and two money labels meaning two quantities one drill click apart.

**Verification performed.** Every gate re-run by the orchestrator after the patch pass, not taken on report: `ruff check` + `ruff format --check` clean over 293 files; `mypy` clean under strict over 279; **3121 pytest passed, 1 skipped** against Postgres; `npm run generate:api` idempotent; web lint 0 errors (12 pre-existing warnings), typecheck clean, **780 vitest passed**; e2e typecheck clean; and the AD-15 done-gate — **43 Playwright tests passed** against a freshly rebuilt stack, including all nine of `@story:7-4`. Four mutation checks confirmed the new guards fail without their fixes.

**Residual risks.** The portfolio verdict reads stored schedule rows and never refreshes them, so a claim whose week boundary has just passed can be counted one band behind its own case file — deliberate, footnoted on the card, and recorded. The verdict facet remains a two-table read on a route with no role gate when no other facet narrows it, and no cursor field can pin a verdict that a concurrent case-file read rewrites; both are in `deferred-work.md` with their reasoning. The `total_paid` basis question and the "incurred" naming discrepancy are inherited from their existing owners, not settled here.
