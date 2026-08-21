---
title: 'Story 7.2 — Trend & Cohort Analytics'
type: 'feature'
created: '2026-08-21'
baseline_revision: '807ab560a66a3b09fad00a3b1ddd94992dcd05cd'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: true # sixteen findings were patched, three of them high and all three on the same misconception: a metric's population is the claims that fed it, not the claims in the bucket. That one idea had to be applied across the point count, the low-confidence flag, the total that decides emptiness, the drill filters, the empty-state copy and all three oracles — and the reason it survived implementation is that both oracles restated it, which is the Story 7.1 failure mode repeating one story later. The patch pass also changed the wire (a new 'partial' flag, and claimCount/seriesTotal now mean a different population than they did), narrowed two drill paths, and reached into sla.py for a new denominators_of. What a follow-up should read is whether any per-metric population is still conflated on a surface nobody named, whether the two restated oracles are genuinely independent of each other and not just of the server, and whether the partial-terminal-bucket marking holds at week and quarter grain where the partial period is a larger share of the point
context:
  - '{project-root}/_bmad-output/implementation-artifacts/7-2-trend-cohort-analytics.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-7-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-7-1-fraud-analytics-workspace.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-5-3-portfolio-analytics-charts.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-5-5-dashboard-drill-through.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The analyst workspace has one section. Nothing in the console answers "is the portfolio improving or deteriorating" — every aggregate is a snapshot of today, there is no time-bucketing anywhere in `services/` (a grep for `month|quarter|trend|date_trunc` returns only `hash_bucket` and prose), and no line or area chart exists in `web/src`.

**Approach:** Add a Trends section: one analyst-gated aggregate in `services/worklist/trends.py` publishing five metric series over a bucketed window, optionally split one dimension at a time into cohorts; one new rules document owning the window and low-confidence parameters; a Recharts line-chart family; and four drill facets so a bucket reaches its claims through Story 5.5's existing list.

## Boundaries & Constraints

**Always:**
- **Every bucket is anchored on a date the dataset actually carries.** The `claim` table has six date columns and **no settlement or closure date**; `core.py:205-210` records that the SLA duration figures "don't reconcile with its dates by design". `froi_date + settlement_days` is therefore an invented date and is forbidden. Every metric buckets claims by one anchor — `froi_date` (default) or `doi` — chosen per request; settlement cycle time is consequently a **cohort** cycle time ("claims filed in March took N days"), and the UI says so.
- **AD-2: the three SLA-derived figures are never re-averaged here.** Settlement cycle time and RTW rate come from `sla.strip_of(bucket_samples, targets)` called once per bucket — N pure folds over one read. `services/worklist/trends.py` must never name `settlement_days`, `sla_pick_days` or `sla_approve_days`; those columns reach it only as `sla.SAMPLE_COLUMNS` passed to the repository and `sla.sample_of` applied to the row. `tests/test_sla_aggregation.py:394` fails the build otherwise.
- **AD-10: one computer per value.** Volume is a count; `avg_days_open` folds `derivations.days_open` (which takes `as_of`); `paid_cents` folds `derivations.total_paid`; the severity cohort bands with `derivations.risk`. Nothing is re-implemented.
- **One clock per request.** `as_of` is resolved once from `rules.engine.utc_today()` at the top of the service and threaded to every `days_open` call, and it is **published on the response as `asOf`**. Two claims in one request may never be scored against different days.
- **Zero and null are different answers, and the split is by metric kind, not by bucket.** Counts and sums zero-fill (an empty period genuinely saw zero claims and zero dollars). Means and rates are `null` — reusing `sla.strip_of`'s existing `no_data` vocabulary rather than inventing a second one. A `null` never reaches the wire as `0`.
- **Emptiness is decided on a total, never on row count.** Story 7.1 shipped this bug once: a zero-filled series has rows, so `items.length === 0` is false and the empty state never fires. Every trend surface decides emptiness the way `DistributionDonut.tsx:127` now does.
- **AD-8: no number in `trends.py`, in the route, or anywhere under `web/src`.** The default window, the maximum window and the low-confidence claim ceiling are parameters of a new `trend_periods` rules document. Severity band edges keep coming from `derivation_thresholds` via `derivations.risk`; the RTW target keeps coming from `sla.targets_for(settings)`, which is deployment config, exactly as `charts.py:428-431` reads it.
- **AD-7: role gates capability, scope gates visibility.** The route reuses `fraud.FRAUD_ANALYTICS_ROLES` / `require_fraud_analytics_access` — the analyst-workspace allowlist — and must not widen `benchmarks.PERMITTED_ROLES`. The refusal happens before any claim row and before any rule document is loaded. The signature has no slot for an employer, a user or an "as".
- **One scoped read.** One `select_claim_columns_with_employer` call; every metric folds from that one projection. Pinned by a query-count test.
- **Cohort colour is identity, never rank.** `CATEGORICAL_FILLS` is closed to addition and its docstring is explicit that a hue there means rank position. The server publishes a stable `paletteSlot` per cohort value (assigned by sorting cohort values on their **wire key**, not their label), so a cohort keeps its colour across all five charts and across reloads regardless of where it ranks. The severity cohort instead uses `RISK_FILL`, because that dimension already owns a semantic palette the KPI cards use.

**Block If:**
- The `trend_periods` document cannot be given an effective date at or before `derivation_thresholds` v1's (`2026-08-11`) — a required block absent for any resolvable date 500s every trends request in the gap.
- Reusing `sla.strip_of` per bucket turns out to require a change to `sla.py`'s public surface beyond adding a caller. AD-2 makes the shape of that module a decision above this story.

**Never:**
- No 9-dimension segmentation filter, no filter chips owned by the workspace, no workspace-wide URL filter state — that is Story 7.3, and Story 7.1 already deferred its own sort state to it for the same reason. Period and cohort selectors are local React state.
- No forecasting, no trendlines, no smoothing, no statistical modelling. Descriptive aggregates only.
- No export (7.5), no financial decomposition beyond the paid series (7.4), no dashboard-scope copilot.
- No polling. `useCopilotAvailability()` is the SPA's only `refetchInterval` and `api/dashboard.ts` records that as a deliberate stance.
- No `GROUP BY` push-down in this story (see Design Notes 4) and therefore no new index — the story's index allowance goes unused, deliberately.
- No fourth spelling of an existing threshold, and no reuse of the name `total_incurred` (`test_derivations.py:510` pins that it raises `KeyError`).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Default read | analyst, no params | `grain=month`, `anchor=fnol`, `cohort=none`, window = last `defaultBuckets` buckets ending in `asOf`'s bucket; 5 series, each one point per bucket | none |
| Cohort split | `cohort=severity_band` | 5 metrics × 3 bands = 15 series; each carries `cohortKey`, `cohortLabel`, `paletteSlot` | none |
| Empty bucket | a month with no claims in scope | `volume.value=0`, `paidCents.value=0`, `avgDaysOpen.value=null`, `avgSettlementDays.value=null`, `rtwRateBp.value=null`, `claimCount=0` | none |
| No settled claims in a bucket | bucket has claims, none settled | settlement and RTW points are `null` while volume/paid/days-open are numbers | none |
| Low-sample bucket | `0 < claimCount <= lowConfidenceClaimMax` | point carries `lowConfidence: true`; UI renders the low-confidence treatment | none |
| Empty book | scoped persona with no claims | every bucket present, counts `0`, means `null`, `bucketCount>0`, `seriesTotal=0` → UI empty state | none |
| Window too wide | requested span exceeds `maxBuckets` for the grain | refusal naming the cap | 422 problem+json `/problems/trend-range-too-wide` |
| Inverted range | `from > to` | refusal | 422 problem+json `/problems/trend-range-invalid` |
| Unknown grain/cohort/anchor | `grain=fortnight` | FastAPI enum validation answers before the service runs | 422, vocabulary never restated in the body |
| Non-analyst | supervisor or handler session | refusal before any claim read and before any rule document load | 403 problem+json `/problems/fraud-analytics-not-permitted` |
| Anonymous | no session | refusal | 401 |
| Bucket drill | click a bucket on a cohort series | `/dashboard/claims?filter[fnolFrom]=…&filter[fnolTo]=…&filter[severityBand]=high`, chips clearable individually | 422 on a hand-edited bad value still renders clearable chips (`DrillClaimsPage.tsx:131-133`) |

</intent-contract>

## Code Map

**Server — the aggregate**
- `server/services/worklist/charts.py:237` `ChartClaim`, `:362` `charts_of`, `:439` `portfolio_charts` — the frozen-projection → pure-fold → one-`await`-wrapper shape every aggregate copies. `:270` `_declared` (omits absent categories), `:301` `_ranked`, `:327` `_by_employer` (emits a zero row) — the three existing zero-fill policies a fourth must be argued against. `:428-431` republishes `sla.strip_of` unchanged.
- `server/services/worklist/fraud.py` — Story 7.1, the closest template: `:121` `FRAUD_ANALYTICS_ROLES`, `:201` `require_fraud_analytics_access`, `:222` `FraudClaim`, `:272-292` the `_Computers` frozen dataclass built once via `of(thresholds)`, `:690-724` `RateBreakdown` (an envelope that is not a `Distribution`), `:1264` `_projection` reusing an existing selector rather than adding a sibling.
- `server/services/worklist/sla.py` — AD-2's single aggregation. `:163` `SAMPLE_COLUMNS`, `:172` `sample_of` (by label), `:196` `targets_for(settings)`, `:327` `strip_of` (pure; settle mean over settled claims that have one, RTW rate over all settled — deliberately different denominators; `no_data` for an empty segment). `benchmarks.py` is the precedent for a second consumer.
- `server/services/derivations/open_duration.py:111` `days_open` (requires `as_of`), `:119` `days_to_settlement`. `claim_money.py:119` `total_paid` (structural `PaidColumns` protocol — a projection satisfies it by shape). `risk_band.py:76` `risk`. `registry.py:50` `register` refusing a duplicate name.
- `server/data/repositories/claims.py:179` `select_claim_columns_with_employer` (un-aliased `Employer` join, caller-chosen `columns`) — the one read. `:266-278` and `:382-395` the recorded refusal of a `predicate` parameter.
- `server/data/models/core.py:120-126` the six date columns; `:205-210` the durations **and the comment that they do not reconcile with the dates**; `:66` `Employer.sector`; `:136` `disability`.

**Server — rules**
- `server/rules/parameters.py:178` `DerivationThresholds` and `:401` its `of(document, result)`; helpers `_number` `:66`, `_integer` `:89`; `thresholds_for(db)` `:488`. Sibling blocks (`weights_for`, `handler_performance_for`, `worklist_actions_for`) are the precedent for adding a **new document** rather than a field on this one.
- `server/data/versions/20260821_0045_fraud_band_thresholds.py` — the migration to clone: `ROWS` spelled out, effective-date rule, and the docstring carrying the whole argument.
- `server/tests/test_derivations.py:544` `test_no_module_outside_the_registry_hardcodes_the_band` — regex `(?<![\w.])(65|55|35)(?![\w.])` over every `*.py`/`*.jdm.json`, comments included, allowlist a set of resolved paths.

**Server — route**
- `server/api/routers/dashboard.py` — `:123` the router; `:1602` `/dashboard/fraud` is the decorator and body ordering to copy (`no-store` first statement, role gate second, `thresholds_for` third, service fourth, response built field by field); `:1477` `FRAUD_ANALYTICS_FORBIDDEN_RESPONSE`, `:1490` `_fraud_forbidden`; `:1873-1886` typed enum query params; `:1167-1240` the `filter[…]` facets on `/dashboard/claims`.
- `server/api/schemas.py:17` `ApiModel`.

**Web**
- `web/src/features/shell/WorkspaceNav.tsx:67-88` `NavSpec`, `:90` `DESTINATIONS`, `:98` the `owns` **partition** (Portfolio owns "not fraud"), `:55` `inFraudSection`, `:28-33` a docstring that pre-authorises this story's line. `features/shell/routes.ts:18` `FRAUD_ROUTE`, `:42` `homeRouteFor` (leave alone). `App.tsx:70-72` the analyst-only guard.
- `web/src/features/dashboard/fraud/FraudPage.tsx:11-17` the composition rule (page owns queries, sections take `{data,isPending,isError}`), `:182-191` the `pendingSort` idiom for a control that re-keys, `:202-205` why `aria-busy` tracks only the primary query.
- `web/src/features/dashboard/charts/ChartFrame.tsx:39-78` props, `:98-134` the four exclusive states, `:37`/`:139` the always-reserved `footnote` row. `chartTheme.ts:51` `RISK_FILL`, `:104` `SERIES_FILL`, `:117-149` `CATEGORICAL_FILLS` + the closed-to-addition ruling, `:166` `PALETTE_OVERFLOW_FILL`, `:178` `UNKNOWN_KEY_FILL`, `:207-212` `CHART_HEIGHT`. `DistributionDonut.tsx:118-127` the emptiness rule 7.1 fixed; `DistributionBars.tsx:215-236` `total === 0`, never `> 0`.
- `web/src/api/queryKeys.ts:202` `dashboard.fraud` and `:138-149` the prefix blast-radius lesson. `api/dashboard.ts:333-342` the canonical hook, `:400` `toFraudRateSortKey` (serialize params into the key), `:439` `keepPreviousData`.
- `web/src/features/dashboard/drill/filters.ts:44-51` the load-bearing `FILTER_KEYS` order, `:52-67` the 14 facets, `:122` `toSearchParams`, `:156` `toQueryParams`, `:255` `drillHref`, `:299` `FILTER_LABEL`, `:387` `VALUE_LABEL`.
- `web/src/features/queue/noDerivation.test.ts:50-70` `ROOTS` (includes `features/dashboard`), `:562-717` the per-file registration block new files must join.

**Tests / oracles**
- `server/tests/test_fraud_analytics.py` — pure half (`:125` keyword-only row factory, `:167` `_thresholds`), DB half (`:1095` `context_for`, `:1127`/`:1138` role allowlist, `:1181`/`:1210` refusal ordering, `:1752`/`:1771` the query-count guard).
- `server/tests/seed_fixture.py:1212` Story 7.1's banner and `:1229-1234` the ruling that an oracle spells its constants separately even when they coincide.
- `e2e/fixtures/seed.ts:5-17` the independent-oracle doctrine; `:2970-3003` `topRate` **after** the 7.1 fix (population cut before display order).
- `e2e/stories/7-1-fraud-analytics-workspace.spec.ts:71-72` tagging; `:270` the set-identity assertion the 7.1 review added.

## Tasks & Acceptance

**Execution:**
- [x] `server/rules/documents/trend_periods.v1.jdm.json` -- new rules document with `defaultBuckets`, `maxBuckets`, `lowConfidenceClaimMax` -- AD-8 owns the window and the low-sample ceiling; the description narrates why these are policy, not arithmetic.
- [x] `server/rules/parameters.py` -- add a `TrendPeriods` frozen block, its `of(document, result)` using `_integer`, `__post_init__` range checks (all positive; `defaultBuckets <= maxBuckets`, refused with `>` not `>=` reasoning stated), and `trend_periods_for(db, as_of)` beside `thresholds_for` -- a new document, not a field on `DerivationThresholds`, because a window is not a derivation cut-off.
- [x] `server/data/versions/20260821_0046_trend_periods.py` -- insert the v1 row, `ROWS` spelled out, `EFFECTIVE_FROM = date(2026, 8, 11)` so no resolvable date exists without the required block; docstring carries the argument.
- [x] `server/services/worklist/trends.py` -- `TrendClaim` frozen projection (built by keyword), a `_Computers.of(thresholds)`, the bucketing helpers, the pure `trends_of(...)`, and `async def portfolio_trends(db, ctx, thresholds, periods, settings, as_of)` with exactly one `await` -- the five metric series and the cohort split.
- [x] `server/services/worklist/__init__.py` -- re-export the new public names -- match the existing surface.
- [x] `server/api/routers/dashboard.py` -- `GET /dashboard/trends` with `grain`, `anchor`, `cohort`, `from`, `to` typed params; `no-store` first, `require_fraud_analytics_access` second, both rule blocks third, service fourth; response models mirroring the service field by field; two new problem translators -- the read-only analyst surface.
- [x] `server/api/routers/dashboard.py` (drill) -- append `filter[fnolFrom]`, `filter[fnolTo]`, `filter[doiFrom]`, `filter[doiTo]`, `filter[disability]`, `filter[sector]` to `GET /dashboard/claims` in that order -- appended, never inserted: `appliedFilters` order is the chip row's order.
- [x] `server/services/worklist/drill_through.py` -- accept and apply the six new facets under the same scope predicate -- date facets are inclusive range bounds on the anchor column; sector narrows through the existing employer join.
- [x] `web/src/api/schema.d.ts` + `web/openapi.json` -- regenerate via `npm run generate:api` **after all server route work** -- CI fails on an undiffed client.
- [x] `web/src/api/queryKeys.ts` -- add `dashboard.trends: (paramsKey) => ["dashboard","trends","series",paramsKey]` -- a leaf under an unowned prefix, per the blast-radius lesson.
- [x] `web/src/api/dashboard.ts` -- `useTrends(params)` with `toTrendParamsKey`, `staleTime: 30_000`, `placeholderData: keepPreviousData`, no `select` -- so a grain/cohort change keeps the previous chart and reports busy only on the changed surface.
- [x] `web/src/features/dashboard/charts/chartTheme.ts` -- add `CHART_HEIGHT.trend` -- a line chart's body height, not a row equaliser.
- [x] `web/src/features/dashboard/trends/trendColors.ts` -- `cohortFill(dimension, key, paletteSlot)`: severity → `RISK_FILL`, otherwise `CATEGORICAL_FILLS[paletteSlot] ?? PALETTE_OVERFLOW_FILL`; label falls back to `?? key` -- identity colour, no palette invention.
- [x] `web/src/features/dashboard/trends/TrendChartCard.tsx` -- one `ChartFrame` wrapping a Recharts `LineChart` with `connectNulls={false}`, `isAnimationActive={false}`, `aria-hidden` SVG plus an accessible `sr-only` list of `bucketLabel: value` per series, low-confidence dot treatment, and the sparse-bucket caption in `footnote` -- gaps, not zero lines.
- [x] `web/src/features/dashboard/trends/TrendsPage.tsx` -- section page owning the one query, the grain/anchor/cohort selects in the header, five cards, the period `<select>` + "View claims" drill control, and cohort legend buttons -- page owns queries, cards take props.
- [x] `web/src/features/shell/routes.ts` + `web/src/App.tsx` -- add `TRENDS_ROUTE` and the route inside the existing analyst-only guard -- a section, not a home.
- [x] `web/src/features/shell/WorkspaceNav.tsx` -- add the Trends destination **and amend Portfolio's `owns`** to exclude both sections -- otherwise two entries render current on `/dashboard/trends`.
- [x] `web/src/features/dashboard/drill/filters.ts` -- append the same six facets to `FILTER_KEYS` in the identical order, one spelled-out `toQueryParams` line each, and `FILTER_LABEL` entries; update the `FILTER_KEYS[-2:]` position assertion that pins the tail -- the order must match the server's or the chip row desyncs.
- [x] `web/src/features/queue/noDerivation.test.ts` -- register the new `features/dashboard/trends/*` files in the scanned block and append the new wire names to `DERIVED_FIELDS` scoped as properties (`\.paletteSlot`, `\.lowConfidence`, `\.bucketLabel`) -- a bare token breaks ordinary arithmetic elsewhere.
- [x] `server/tests/test_trend_analytics.py` -- the pure half (bucket boundaries, null-vs-zero by metric kind, cohort splits, palette-slot stability, window refusals) and the DB half (scope containment with a hand-built narrowed `CallerContext`, role allowlist from the enum, refusal ordering, one-scoped-read query count, cross-surface reconciliation against `/dashboard/charts` and `/dashboard/summary`) -- covers the I/O matrix.
- [x] `server/tests/seed_fixture.py` -- a Story 7.2 banner restating the bucketing independently, returning whole payload-shaped objects -- constants spelled separately even where they coincide.
- [x] `web/src/features/dashboard/trends/TrendsPage.test.tsx` -- data fidelity, a contrast fixture (different scope, different grain, a moved band edge, a missing cohort), gap-not-zero rendering, all-empty state, low-confidence treatment, cohort colour stability across two charts and across a refetch, loading, error (404 fixtures), error isolation, no-reflow, drill URL -- reads the accessible list, never Recharts SVG internals.
- [x] `e2e/fixtures/seed.ts` -- `expectedTrendsFor(persona)` restated independently, returning rendered strings -- must not share a helper shaped like the server's pipeline.
- [x] `e2e/stories/7-2-trend-cohort-analytics.spec.ts` -- `@story:7-2 @epic:7`, one `@smoke`: analyst login → Trends renders five series → switch cohort dimension → side-by-side series → drill a bucket to the claim list; plus set-identity on the bucket population when grain changes, a sparse-bucket assertion, and the smuggled-parameter check via `page.evaluate(fetch)` -- the AD-15 done-gate.

**Acceptance Criteria:**
- Given an analyst session, when the Trends section renders, then five server-computed series (volume, average days open, settlement cycle time, RTW rate, paid) appear over the selected grain and anchor, and no figure on screen was computed in the browser.
- Given the cohort selector set to severity band, disability type or sector, when the series render, then each cohort value holds the same colour in every chart of the section and across a refetch, and the severity cohort's colours match the KPI cards' risk colours exactly.
- Given a bucket whose claims cannot produce a mean, when the chart renders, then the line shows a gap rather than a point at zero, the card's footnote states how many buckets in the window are without data, and a bucket at or below the low-confidence ceiling is visually marked.
- Given a persona whose scope contains no claims, when Trends renders, then every card shows its defined empty state — decided on the series total, not on the number of points.
- Given a supervisor or handler session, when `/dashboard/trends` is requested directly, then a 403 problem+json is returned without reading a claim row or loading a rule document.
- Given a bucket drill, when the analyst opens its claims, then Story 5.5's list appears filtered to that period and cohort with each filter shown as an independently clearable chip, and the claim rows open the read-only claim view.
- Given the grain or cohort control changes, when the new data is in flight, then the previously rendered charts remain visible and only the affected surface reports busy.

## Spec Change Log

## Review Triage Log

### 2026-08-21 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 16: (high 3, medium 5, low 8)
- defer: 2: (high 0, medium 0, low 2)
- reject: 1: (high 0, medium 0, low 1)
- addressed_findings:
  - `[high]` `[patch]` `claim_count`, `low_confidence` and `series_total` were the bucket's whole claim count on all five metrics, but `avg_settlement_days` and `rtw_rate_bp` divide by settled claims. On the shipped seed the Temporary settlement line drew Jul 2026 = 71d from three durations while publishing `claimCount: 7, lowConfidence: false` against a ceiling of 3 — the rule this story added to catch thin figures was inoperative on the two metrics most likely to be thin, and a 100-claim scope with nothing settled rendered the Settlement card as non-empty. Added `sla.denominators_of`, so a denominator and the figure that divided by it are read off the same pass and cannot disagree; all three counts are now the metric's own population.
  - `[high]` `[patch]` Both oracles restated the same conflation (`seed_fixture.py:1746`, `e2e/fixtures/seed.ts:3751`), so neither could catch it — the Story 7.1 failure mode repeating. Each now derives the two denominators independently, sharing no helper or expression with the server or with each other; the divergent-case test asserts all five counts and verdicts rather than only the two values.
  - `[high]` `[patch]` The e2e oracle derived the cohort vocabulary from the persona's whole book while the server derives it from the window — green only by accident of today's date. Verified: at `as_of=2026-09-01` the window-derived vocabulary is 8 sectors against the book's 9, and because Aerospace sorts first every `paletteSlot` shifted. Now derived from the window.
  - `[medium]` `[patch]` Drilling a settlement or RTW point opened the whole bucket rather than the population the point was folded from. Those two metrics now narrow to `stage=settled`, a null-valued point is no longer clickable, and a server test reconciles the drill total against `claimCount` bucket by bucket.
  - `[medium]` `[patch]` The terminal bucket is a partial period and was drawn identically to complete ones, so the rightmost point — the one the section exists to read — was under-counted and dragged toward zero unmarked. Now carries `partial` on the wire and is marked three ways on the card.
  - `[medium]` `[patch]` A card's title was drawn from the pending selector while its lines were the previous payload, so switching anchor titled FNOL-bucketed lines "by injury date". Title now follows `data.anchor`, as the drill already did.
  - `[medium]` `[patch]` "Average days open" named no anchor although its axis follows the anchor while its values never do; it now names both the axis and the measure.
  - `[medium]` `[patch]` The RTW-target test asserted `int(v)*100` against an implementation computing `int(round(v*100))` — agreeing only for whole numbers, while its name promised it checked the conversion. Now asserts the conversion the code makes, exercised at 80.5%.
  - `[low]` `[patch]` Calendar-bound overflow (`to=9999-12-31`, `grain=week`, `to=0001-06-15`) raised uncaught `ValueError`/`OverflowError` as a 500; now the existing 422.
  - `[low]` `[patch]` `_point_value` fell through to the RTW branch, so a sixth metric would silently publish the RTW rate under its own name; both dispatch tables now end in an exhaustiveness raise.
  - `[low]` `[patch]` Unguarded `line.points[index]` would throw during render with no ErrorBoundary; `YAxis` drew fractional ticks for integer metrics; `filtersFor` answered a failed bucket lookup with the entire unfiltered book. All three closed.
  - `[low]` `[patch]` Five constants `seed_fixture.py` restated "so this oracle notices the day a setting moved" were never read, making the stated protection fictional; they are now part of the oracle's answer. The `maxBuckets` description claimed to bound work it cannot bound, and `select_drill_rows` gave two facet counts twenty lines apart; both corrected.
  - `[medium]` `[patch]` Found while patching, same conflation, not on the reviewers' list: the section-wide empty sentence "No claims fall in this window." became a true statement about the wrong population once the SLA cards empty independently. Those two now say "No claims in this window have settled."


## Design Notes

**1. Why settlement cycle time is a cohort metric, and why that is the honest reading rather than a compromise.** There is no closure date in the schema — only `settlement_days`, and `core.py:205-210` says outright that the SLA durations do not reconcile with the dataset's dates. So "settlement cycle time in March" cannot mean "claims that settled in March"; the only defensible reading is "claims *filed* in March took N days to settle". That is a genuinely useful analyst question and it is the one the data can answer. The alternative — synthesising `froi_date + settlement_days` — manufactures a date the dataset explicitly disclaims, which is AD-2 territory. The chart title and the card footnote both say "by FNOL cohort" so no reader can mistake the two.

**2. Null by metric kind, not by bucket.** A bucket with no claims paid nothing and saw nothing: `volume: 0` and `paidCents: 0` are facts, and drawing them as zero is not the "misleading zero line" AC 3 forbids. What AC 3 forbids is a *mean* or a *rate* rendered as zero when the denominator was empty — "average days open: 0" and "RTW rate: 0%" are both false. `sla.strip_of` already draws exactly this distinction with `no_data`, so the rule is inherited rather than invented:

```
volume, paidCents        -> zero-filled   (a sum over an empty set is 0)
avgDaysOpen              -> null when claimCount == 0
avgSettlementDays        -> null when no settled claim in the bucket   (sla no_data)
rtwRateBp                -> null when no settled claim in the bucket   (sla no_data)
```

This is the fourth zero-fill policy in the codebase, and it differs from all three in `charts.py` because those band a *vocabulary* while this one bands a *timeline*: a month that produced no claims is neither an absent category (`_declared`) nor a ranked survivor (`_ranked`) — it is an observed zero, and the buckets between the window edges are known in advance.

**3. Why `days_open` slopes, and why that is left visible.** `days_open` counts `froi_date → as_of` and never freezes at settlement, so an average-days-open series bucketed by filing month slopes upward toward older buckets purely because those claims are older. That is a property of the metric, not a defect, and hiding it would require a second computer for the same value — precisely what AD-10 forbids. The response publishes `asOf` and the card says "as of {asOf}", which is also the wire change `deferred-work.md` has wanted since Story 2.1 ("better made when a second date-sensitive endpoint needs it" — this is that endpoint). Related seed artefact worth knowing while reading a chart: 10 claims carry a future `froi_date` against today, and `days_open` floors at 0, so the last buckets read near zero by design.

**4. The `GROUP BY` push-down: decided, not inherited.** Five endpoints now fold O(scope) in Python per request and each deferred the question to this epic. The decision here is **no push-down**, and the reason is that it would only move one of five metrics. `date_trunc` is not a rule value, so bucketing pushes down cleanly — but `avgDaysOpen` folds a registered derivation, `paidCents` folds another, and the two SLA metrics fold `sla.strip_of`, all Python-tier by AD-2/AD-10. Pushing the bucket into SQL would leave four metrics needing the rows anyway, turning one scoped read into two read shapes and splitting one aggregate across two tiers to save a count. The trigger to revisit is stated rather than left implicit: when a single scope exceeds roughly 10⁴ claims, or when Story 7.3 multiplies grain × cohort × segmentation on one request. This story is the sixth endpoint on that list and the first to say why it stays.

**5. `paletteSlot` is an ordinal, not a colour.** The server publishes an integer per cohort value, assigned by sorting cohort values on their wire key; the browser maps it through `CATEGORICAL_FILLS`. This keeps the palette itself in the theme module (which is closed to addition) while fixing the flaw its own docstring names — that a hue there means rank position, so a cohort would change colour when its ranking moved. Sorting on the wire key rather than the display label is the 5.3 review's finding restated: two labels can collapse where two keys cannot. Sector is 9 free-text values across 10 employers (Automotive twice, the rest 1:1), which fits under the 10-hue ceiling but is close enough to it that the overflow fill must still be wired; the card also says "sector" is an employer attribute, not an industry rollup, so nobody reads more into it than the column carries.

**6. Two rules-document versions on one payload, named separately.** `rulesVersion` keeps meaning `derivation_thresholds` — the document the severity cohort's band edges come from — exactly as it does on the fraud payloads, and the new window parameters travel as `periodsVersion`. `deferred-work.md` records that `rulesVersion` already names different documents on different routes; publishing two explicitly named fields is the one move that reduces that ambiguity instead of adding to it.

**7. What the oracles must not do.** The 7.1 review's lesson was that `e2e/fixtures/seed.ts::topRate` carried the same sort-then-cut defect as the implementation and therefore agreed with it. A trend story has the same shape of hazard twice over: a window is a cut, and a cohort split is a partition. So the oracles bucket from the raw seed with their own date arithmetic and their own window arithmetic, sharing no helper with the server; and the specs assert **which buckets and which cohort values exist** (set equality) alongside their order and their values, because an ordering assertion is satisfiable by the wrong population.

**8. Scope is asserted where an analyst can be scoped.** There is one analyst in the seed and they are `scope_all`, so no HTTP request can demonstrate a narrowed analyst — the identical gap Story 7.1 recorded rather than papered over. The service takes `CallerContext` directly, so AD-7 is asserted there with a hand-built narrowed context (including the empty-book case), while the HTTP layer still carries "no query parameter can widen the scope". Seeding a scoped analyst moves persona counts across Stories 1.3, 1.4 and 5.1 plus the e2e login fixture, which is out of scope here; the gap stays named in `deferred-work.md`.

## Verification

**Commands:**
- `cd lineworker/server && uv run ruff check . ../deploy && uv run ruff format --check . ../deploy` -- expected: clean.
- `cd lineworker/server && uv run mypy .` -- expected: clean under strict.
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker uv run pytest` -- expected: all pass, count above the 2871 baseline; `test_no_module_outside_the_registry_hardcodes_the_band` and `test_nothing_outside_the_worklist_aggregation_reads_the_sla_source_columns` both green.
- `cd lineworker/server && uv run alembic check` -- expected: "No new upgrade operations detected".
- `cd lineworker/web && npm run generate:api && git diff --exit-code -- src/api/schema.d.ts` -- expected: regenerated client committed, no diff after.
- `cd lineworker/web && npm run lint && npm run typecheck && npm test` -- expected: clean; vitest above the 702 baseline; `noDerivation.test.ts` passes **and** reports the new `features/dashboard/trends/*` files as scanned.
- `cd lineworker/web && npm run build` -- expected: succeeds.
- `cd lineworker && docker compose -f deploy/compose.e2e.yaml up -d --build --wait` then `cd e2e && npm run typecheck && npx playwright test --grep "@story:7-2\b|@story:7-1\b|@story:5-5\b|@story:5-3\b|@smoke"` -- expected: all pass. The cross-story set is 7-1 (the nav partition changed), 5-5 (six facets joined its filter vocabulary) and 5-3 (`chartTheme` gained a height).
- `cd lineworker && cd e2e && npm test` -- expected: full suite green, above the 225 baseline.
- `cd lineworker/server && grep -rnE "\b(35|55|65)\b" services/worklist/trends.py api/routers/dashboard.py` -- expected: no match.
- `cd lineworker/server && grep -rnE "(?<![\w.])(settlement_days|sla_pick_days|sla_approve_days)(?![\w.])" services/worklist/trends.py` -- expected: no match.
- `cd lineworker/web && grep -rnE "\.sort\(|\.reduce\(|\.filter\(" src/features/dashboard/trends/` -- expected: no match.

**Manual checks (if no CLI):**
- Log in as the analyst, open Trends: exactly one nav entry is marked current on `/dashboard/trends`, and exactly one on `/dashboard` and `/dashboard/fraud`.
- Switch grain to quarter: three buckets render (the seed spans 2026-01 → 2026-09) and the footnote states the window rather than implying more history exists.
- Switch grain to week with the sector cohort: most cells are absent — confirm gaps, not zero lines, and that low-confidence buckets are marked.
