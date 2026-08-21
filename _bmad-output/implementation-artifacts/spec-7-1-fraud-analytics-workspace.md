---
title: 'Story 7.1 — Fraud Analytics Workspace'
type: 'feature'
created: '2026-08-21'
baseline_revision: 'a921899504b118fcaff22c66db2c28d683d4d026'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/_bmad-output/implementation-artifacts/7-1-fraud-analytics-workspace.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-7-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-5-5-dashboard-drill-through.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-6-2-ai-insight-cache-insights-tab.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The analyst persona has been a supervisor clone since Epic 5 — `homeRouteFor("analyst")` returns the same `/dashboard`, `PERMITTED_ROLES` treats the two roles as one allowlist, and `test_the_analyst_reads_byte_identically_to_the_supervisor` is a live assertion that nothing differentiates them. Fraud concentration and the SIU pipeline are invisible: the portfolio's only fraud surface is one KPI card counting 13 claims.

**Approach:** Ship the analyst workspace shell (the console's first navigation) plus its Fraud section only: three analyst-gated read-only aggregates in `services/worklist` — a band distribution, the SIU pipeline, and fraud-rate breakdowns — a portfolio-wide read over the `ai_insight` fraud cache, and two new drill facets so every segment reaches its claims through Story 5.5's existing list and read-only claim view.

## Boundaries & Constraints

**Always:**
- **Every fraud population is a registered derivation, never a comparison.** `fraud_flagged` (`fraud_flag and score >= fraudFlagScoreMin`) is the AC's "score ≥ 55"; `siu_review` (`fraud_flag and score >= siuFraudScoreMin`) is the SIU population; the new `fraud_band` is a banding of `fraud_score` **alone**. Three rules over one column pair; `queue_flags.py:88-113` records why collapsing any two is defensible at every step and wrong on the screen. Computers come from `derivations.<name>.for_thresholds(thresholds)`, built once per request from the block the router loaded.
- **AD-8:** every cut-off — both existing fraud thresholds and both new band edges — is a `derivation_thresholds` parameter. No number in `services/worklist/fraud.py`, `api/routers/dashboard.py`, or anywhere under `web/src`.
- **AD-7:** every read runs behind `employer_scope(ctx)`; no repository, service or route branches on role to widen or skip it. Role gates capability (the new routes are analyst-only); scope gates visibility. No caller-supplied scope, no `predicate` parameter added to any repository selector.
- **AD-1:** every number on screen arrives computed. `noDerivation.test.ts` will scan the new `features/dashboard/fraud/` files: no `.sort(`, no `.reduce(`, no comparison against a numeric literal, no arithmetic on a derived field, no client-side percentage.
- **AD-12/AD-10:** this story writes nothing. `ai_insight` is read-only here and stays owned by `services/rag`; no refresh is triggered from these surfaces; cached narrative renders with its `generated_at` and is never presented as claim data.
- **AD-15:** `e2e/stories/7-1-fraud-analytics-workspace.spec.ts` passes against the freshly reset stack before this story moves to review.
- Reuse, never fork: Story 5.5's `DrillClaimsPage` / `ReadOnlyClaimPage` / `filters.ts`, Epic 5's `ChartFrame` / `DistributionBars` / `ColumnSpec` table idiom, Story 6.2's `InsightShell` / `InsightBullets` / `FRAUD_OUTCOME_LABEL` / `formatNotedAt`.

**Block If:**
- Extending `filter[fraudBand]` / `filter[siuReview]` onto `/dashboard/claims` cannot be done without changing what an existing facet returns. Adding facets is sanctioned; altering `fraudFlagged`, `stage`, `handlerId` or `employerId` semantics is a contract change to Story 5.5 that this story does not own — HALT rather than reinterpret one.
- Gating the fraud routes to `analyst` turns out to require weakening `benchmarks.PERMITTED_ROLES`, or editing any existing gate, rather than adding a new allowlist beside it.

**Never:**
- No fraud-score modelling, no score recomputation, no SIU state machine, no writes, no copilot pane on this surface, no export. No trends (7.2), no segmentation control (7.3), no financial decomposition (7.4) — their nav slots are not rendered at all rather than rendered broken.
- **Never invent a red-flag taxonomy.** `narrative.red_flags` are model-authored prose clauses; clustering, keyword-bucketing or a canned vocabulary would be the server originating a classification (AD-2). Group on the stored string under a stated normalisation, or not at all.
- No second flagged-claims list: `/dashboard/claims?filter[fraudFlagged]=true` already is one.
- No dark theme. Story 1.1's light palette is canonical; the epics' "dark console aesthetic" wording and the Epic 7 context's echo of it are a documented discrepancy.
- No new colours: `chartTheme.ts` owns every fill and `CATEGORICAL_FILLS` is closed.
- No client-side sorting, and no seeded persona added or removed.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Analyst reads the fraud panel | `GET /dashboard/fraud`, analyst session | Band distribution over **all** claims in scope, SIU pipeline by stage and by handler, published thresholds + versions | No error expected |
| Supervisor reads it | Same request, supervisor session | 403 `/problems/fraud-analytics-not-permitted`, `Cache-Control: no-store`, no claim read and no rule document loaded | 403 problem+json |
| Handler reads it | Same request, handler session | 403, identical shape — the gate is an allowlist over `{analyst}` | 403 problem+json |
| Empty band | Scope contains no claim in a band | The band is present with `count: 0` — the rule defines the vocabulary, absence is the answer | No error expected |
| Empty book | Analyst context with no employers | Every band `0`, empty pipeline, empty rate tables; `200`, never a 500 or a 404 | No error expected |
| Rate over a thin bucket | One claim of an injury type | `rateBp` published beside `flagged` and `claims` so the denominator is on screen; no suppression rule invented | No error expected |
| Sort a breakdown | `GET /dashboard/fraud/rates?sort[injuryType]=label_asc` | Only that table's order changes; the other two keep their declared defaults | Unknown value → 422 validation-error (the type is the check) |
| Red flags, cold cache | No `ai_insight` fraud rows in scope (the seeded state) | `200` with empty `items`, `generatedFrom`/`generatedTo` `null`, `claimsWithInsight: 0`; the card renders its empty state | No error expected |
| Red flags, mixed cache | Some `red_flags` rows, some `low_risk`, one row failing re-validation | Clauses ranked by distinct-claim count then clause ascending; `low_risk` rows raise coverage and contribute no clause; the unreadable row is excluded and counted in `unreadable` | Logged with ids only, never a 500 |
| Segment clicked | Band `high` legend row | Navigates to `/dashboard/claims?filter[fraudBand]=high`, chip "Fraud band: High", scope re-resolved server-side | Bad facet value → 422, chips degrade via `appliedFromFilters` |
| Forged cursor on the drill list | Cursor cut under a superseded `derivation_thresholds` | 400 `/problems/invalid-cursor` — never a silent page one, never a 500 | 400 problem+json |
| Direct load of a drill URL | Scoped persona pastes a URL naming a claim outside their book | The list is re-resolved server-side and the claim is absent; the read-only view answers not-found | In-app not-found, URL left as typed |

</intent-contract>

## Code Map

**Server — rules and derivations**
- `server/rules/parameters.py:180-270` — `DerivationThresholds`, the single argument every registered derivation is built from; `fraud_flag_score_min` at `:229` with v5's rationale, `siu_fraud_score_min` at `:198`, `_integer(document, result, "fraudFlagScoreMin")` at `:371`, the `0..100` range checks at `:249-269` and the recorded refusal to cross-validate the two fraud numbers at `:260-264`. `thresholds_for(db, as_of)` at `:488`; `DERIVATION_THRESHOLDS_KEY` at `:52`.
- `server/rules/documents/derivation_thresholds.v5.jdm.json` — current effective document; v6 is a **full copy plus the new expressions**, never an edit.
- `server/data/versions/20260818_0037_fraud_flag_threshold.py` — the migration to clone: `ROWS` spelled out rather than globbed (`:57-59`), `EFFECTIVE_FROM = date(2026, 8, 11)` deliberately taking v1's date because the typed block *requires* the parameter (`:17-25`), and the docstring recording that bumping this document invalidates outstanding cursors by design.
- `server/services/derivations/queue_flags.py:49-113` — `SiuReviewDerivation` and `FraudFlaggedDerivation` side by side with the difference written down; registration at `:162` and `:172`. `server/services/derivations/risk_band.py` — the shape a banding derivation takes (its own module, its own enum). `registry.py:36-60` — `Derivation`, `for_thresholds`, `register` refusing a duplicate name.
- `server/tests/test_derivations.py:536` — `test_no_module_outside_the_registry_hardcodes_the_band` greps `(?<![\w.])(65|35)(?![\w.])` over every server `*.py`/`*.jdm.json`, comments included, with an allowlist of `rules/documents/`, `services/derivations/`, `tests/`, `data/seed/`, `data/versions/`.

**Server — aggregates**
- `server/services/worklist/charts.py` — the aggregate template: frozen row projection `ChartClaim` `:236-267` built **named, never positional** (`:478-482`), frozen result `PortfolioCharts` `:201` publishing the thresholds **read off the derivation that banded** (`:232-233`), pure fold `charts_of` `:362`, async wrapper `charts` `:429` whose parameter blocks are arguments and never fetched here (`:437-441`). `_declared` `:270` **omits** absent categories and says so (`:284-287`); `_ranked` `:301` is count-desc then label-asc with the tie-break argued at `:311-316`; `INJURY_TYPE_LIMIT`/`STATE_LIMIT` are module constants published on the wire with `truncated` (`:101-116`).
- `server/services/worklist/benchmarks.py:156` — `PERMITTED_ROLES` as a deliberate allowlist (`:150-155`), `require_benchmarks_access` `:189`, `_grouped` `:382` grouping on `handler_id` and carrying `handler_name` beside it (`:230-236`), `_computers` `:548`, and the result at `:305-317` that has **no `total` and no `next_cursor` — `items` is the list**. This is the precedent for an uncapped, uncursored, therefore freely sortable breakdown.
- `server/services/worklist/drill_through.py` — the cursor end-to-end: `DrillClaim` `:142`, `DrillFilters`, `select` `:832`, the codec `:565/:588`, the except tuple including `ArithmeticError` `:628-636`, versions **compared** against today while `as_of`/`limit` are **reused** `:1013-1047`, `next_cursor is None` only when finished `:1102-1105`, and the recorded ruling at `:1234-1237` that an offset cursor into a ranking means nothing against a different one — which is why there is no `sort` parameter in this codebase yet.
- `server/data/repositories/claims.py` — `employer_scope` `:62` always a predicate; `select_claim_columns` `:112`; **`select_drill_rows` `:354` already carries `fraud_score`, `fraud_flag`, `injury_type`, `employer_id`, `handler_id`, `handler_name`, `employer_short_name`, `stage`** — everything the three new aggregates need; the "**No `predicate` parameter**" refusal at `:266-278` and `:383-395`; `count_claims_matching` `:2309` as the one predicate-taking helper.
- `server/data/repositories/insights.py:105` — `select_claim_insights` is per-claim only; a portfolio-wide scoped selector is the one genuinely new read.

**Server — routes**
- `server/api/routers/dashboard.py` — `router = APIRouter(tags=["dashboard"])` `:88`; the decorator to copy `:331-336`; `no-store` as the **first statement in the body** `:357-359` and restated inside every `ProblemException` because raising abandons the injected `Response` `:1347-1357`; the gate called at the top of the route `:370-373` and translated by `_forbidden` `:130`; `Annotated[T | None, Query(alias="filter[…]")]` params `:1132-1214`; `InvalidCursor` → **400** `/problems/invalid-cursor` `:1337-1357`. Existing paths: `/dashboard/summary`, `/dashboard/handler-benchmarks`, `/dashboard/charts`, `/dashboard/priority-claims`, `/dashboard/claims` — no `/dashboard/fraud*` exists.
- `server/api/schemas.py:17` — `ApiModel` (`to_camel`, `populate_by_name`, `from_attributes`), built field by field.

**Server — the insight cache**
- `server/data/models/core.py:1313-1392` — `AiInsight(claim_id, kind, content JSONB, model, generated_at)`, unique `(claim_id, kind)`; the latest generation per kind, **not a history**. `enums.py:587` — `InsightKind`, four values, fraud is `fraud_risk_indicators`.
- `server/agents/schemas.py:288-385` — the stored fraud shape. Discriminated on `outcome`, and `:373-380` records that the discriminator is a service's answer and never the model's. `FraudRedFlagsInsight` `:353` carries `narrative.red_flags: list[str]` (1–5 clauses, 8–220 chars each); `FraudLowRiskInsight` `:362` has **no `red_flags` key at all**. `FraudSignalFigures` `:291` is the structured half. `:18` states outright that this structure exists so this story can aggregate the fraud kind portfolio-wide. `agents/insights.py:222-226` — `model_dump(mode="json")` writes **snake_case** keys into JSONB.
- `server/api/routers/claims.py:2963` — the read side re-validates through `TypeAdapter(FraudRiskInsight)` and renders a failing row as an empty slot rather than raising. Adopt that tolerance.

**Frontend**
- `web/src/App.tsx:46-58` — `<RequireSession allow={["supervisor","analyst"]}>` wrapping `DashboardShell` with `index` / `claims` / `claims/:claimId`. `features/shell/RequireSession.tsx:33-78` bounces a disallowed role to `homeRouteFor(role)`; `features/shell/routes.ts:21-31` maps analyst to `DASHBOARD_ROUTE` today. `features/shell/DashboardShell.tsx` is `TopBar` + `<Outlet/>` and nothing else — **there is no nav component in this console yet**; `App.test.tsx` and `WorkspaceShell.test.tsx` identify shells by `aria-label`.
- `web/src/features/dashboard/DashboardPage.tsx:55-230` — the `CardSpec` table (`testId`/`label`/`caption`/`tone`/`drill`) and `:292-414` the composition rule: **sections never fetch their own data**, they receive `{data, isPending, isError}`; page `aria-busy` tracks only the primary query; the `sr-only` `role="status"` line at `:343`.
- `web/src/features/dashboard/charts/ChartFrame.tsx:39-142` — the mandatory wrapper and its five testids plus the always-reserved footnote row. `charts/DistributionBars.tsx:72-366` — generic bars with `onSelect` turning the value list into a visible chip row of `{testId}-value-link` buttons; the SVG is `aria-hidden` and the list is the accessible rendering. `charts/DistributionDonut.tsx:154-156` — legend rows carry `data-key`. `charts/chartTheme.ts` — `RISK_FILL`, `CATEGORICAL_FILLS` (closed), `PALETTE_OVERFLOW_FILL`, `CHART_HEIGHT`. `charts/PortfolioCharts.tsx:142-144` — `openClaims(key)` curried per chart over `drillHref`.
- `web/src/features/dashboard/HandlerBenchmarkTable.tsx:224-231` — `ColumnSpec`; `:242-357` `COLUMNS` generating `thead` and `tbody` together; `:491` `isLoading = isPending && data === undefined` driving both `aria-busy` and skeletons; `:510-588` the fixed `isError → empty → data` branch order; `:452` `SkeletonRows`; `:220` the local `Chip` over `CHIP_CLASS` (`claim-detail/bills/statusTone.ts:40`).
- `web/src/features/dashboard/drill/filters.ts:46-59` — `FILTER_KEYS`, the twelve-name vocabulary; `paramNameOf` making the URL param name the API param name; `toQueryParams` `:148-165` typed off `paths["/dashboard/claims"]` so a renamed facet is a compile error; `chipLabel` `:238`, `FILTER_LABEL`, `appliedFromFilters`, `drillHref`, `claimHref`, `DrillOrigin` carried in router state. `drill/DrillClaimsPage.tsx:83-306` and `drill/ReadOnlyClaimPage.tsx` — the two views to reuse unchanged.
- `web/src/features/claim-detail/insights/InsightShell.tsx:33-89` — `{title, testId, status, kind, model, generatedAt, children}`, emitting `data-kind`/`data-status` and the `Generated {formatNotedAt(...)} · {model}` line only when ready; `InsightBullets` `:98-126` + `BULLET_CLASS`; `insights/insightTone.ts:24-33` — `FRAUD_OUTCOME_TONE` and `FRAUD_OUTCOME_LABEL` ("Review indicated" / "Low risk"), already the right vocabulary. `lib/clock.ts:99-106` — `formatNotedAt`.
- `web/src/api/queryKeys.ts:53-171` — the `dashboard` group; flat keys for base queries, functions for parameterised ones; every docstring states the key carries no persona/role/scope segment because the server answers for the session cookie. `web/src/api/dashboard.ts` — `api.GET` + `return data!`, `staleTime: 30_000`, and the written policy at `:74-79` that **`select` is forbidden**.
- `web/src/features/queue/noDerivation.test.ts:50-70` `ROOTS` (includes `features/dashboard`, recursive), `:204+` `DERIVED_FIELDS` (already carries `fraudScore`, `fraudFlag`, `siuReview`, `fraudFlagged`, `fraudFlagScoreMin`, `total`, `nextCursor`, `handlerId`), `:466-538` the eight `FORBIDDEN` rules, `:562-717` the per-file `expect(scanned).toContain(...)` registration block with a rationale comment per file.
- `web/src/test/api-mock.ts:17-27` `StubRoute`/`StubRouteFor`, `:4470` `stubApi`, `:4490` the **ordered** `if (url.includes(...))` chain — the dashboard block runs `/summary` → `/handler-benchmarks` → `/charts` → `/priority-claims` → `/dashboard/claims`; every surface ships a contrast fixture.

**Tests & E2E**
- `server/tests/test_portfolio_charts.py` / `test_drill_through.py` / `test_handler_benchmarks.py` — the convention battery by name (route parameter count, scope cannot be widened, camelCase key set, `no-store`, session required, no audit row, allowlist gate parametrized over roles, refusal before any claim read and before any rule load, `test_the_aggregate_takes_exactly_N_scoped_reads` monkeypatching `db.execute` (`test_drill_through.py:1438`, rule loads explicitly not counted `:1447-1450`), published thresholds come from the document, a superseded document moves the bands, and the cursor forgery set). `test_drill_through.py:1-24` — the reconciliation banner: never compare a list to a constant; call two live aggregates on one scope and assert equality beside the independent oracle. `test_portfolio_summary.py:315` / `test_drill_through.py:253,794` — the three existing "review rule, not the SIU referral rule" guards.
- `server/tests/seed_fixture.py` — the independent oracle. `claims_for` `:57`, `expected_claim_ids` `:129`, `handler_id_of` `:1121`, `employer_id_of` `:1126`, `_drill_matches` `:1131`; constants **restated, never imported** — `HIGH_RISK_MIN = 65` `:31`, `FRAUD_FLAG_SCORE_MIN = 55` `:84`, `MED_RISK_MIN = 35` `:230`, `SIU_FRAUD_SCORE_MIN = 60` `:231`, with `:78-83` recording that the two fraud numbers must stay separate here too. Per-story `# --- Story N.M: … ---` banners.
- `server/tests/insight_fixture.py:35-116` — `_SENTENCE`, `_CLAUSE`, `fill[T](schema)`: the deterministic fake generator. **Its clauses are identical across claims**, so a frequency ranking built on it collapses to one row of count N.
- `server/tests/conftest.py:24-51` — only `requires_db` and the module-scoped `seeded_db_url`; every module copies its own `db` / `make_client` / `login_as` / `context_for` helpers (`test_portfolio_charts.py:483-527`).
- `e2e/fixtures/login.ts:27-42` — `PERSONAS.analyst` is David Bline, `scope_all`, the whole 100-claim portfolio; there is **no scoped analyst persona anywhere**. `e2e/fixtures/seed.ts` — oracles return rendered strings (`:1690-1697`); `expectedDrillClaimsFor(persona, {fraudFlagged:"true"})` `:2727` and `drillUrl` `:2781` already exist; `FRAUD_FLAG_SCORE_MIN` `:1712` and `SIU_FRAUD_SCORE_MIN` `:305` are module-private and each story restates its own cut-offs under its banner.
- `e2e/stories/5-5-dashboard-drill-through.spec.ts:75-141` — tags on `test.describe` only, exactly one `@smoke` prefixed to the first test title, segment clicks keyed by `data-key` and never by position, chip assertion, `expectFirstPage`, and read-only proved by **absence** (`getByRole("textbox")).toHaveCount(0)`). `e2e/playwright.config.ts:24-29` — the `stories` project globs `stories/*.spec.ts`, so a new spec needs no registration. `.github/workflows/ci.yaml:185-207` — CI derives its grep from `[0-9]+-[0-9]+` in the branch/labels and only if `stories/<key>-*.spec.ts` exists; `@epic:` is never grepped; `:70-75` diffs a regenerated `web/src/api/schema.d.ts`. Nothing in `deploy/` or `.github/` is keyed per epic — **Epic 7 needs no CI entry**.

## Tasks & Acceptance

**Execution:**

- [x] `lineworker/server/services/derivations/fraud_band.py` -- **new**. A `FraudBand` StrEnum (`low`, `medium`, `high`, declared low→high) and a frozen `FraudBandDerivation(high_min, med_min).of(*, fraud_score: int) -> FraudBand`, modelled on `risk_band.py`. Its docstring must state the thing that will otherwise be collapsed: this is a banding of `fraud_score` **alone** — no `fraud_flag` conjunct — and it is therefore a *third* rule beside `siu_review` and `fraud_flagged`, not a re-spelling of either, even though `fraudBandHighMin` and `fraudFlagScoreMin` both read 55 today. Register it in `services/derivations/__init__.py` beside the others.
- [x] `lineworker/server/rules/documents/derivation_thresholds.v6.jdm.json` -- **new**. A full copy of v5 plus `fraudBandHighMin: "55"` and `fraudBandMedMin: "35"`, with a `description` carrying the same argument v5's makes for `fraudFlagScoreMin` vs `siuFraudScoreMin`: two numbers that coincide today are still two rules, and the document is where that is said.
- [x] `lineworker/server/rules/parameters.py` -- add `fraud_band_high_min` and `fraud_band_med_min` to `DerivationThresholds` with a Story 7.1 comment block in the style of v3/v4/v5's, read them via `_integer(..., "fraudBandHighMin" / "fraudBandMedMin")`, and extend `__post_init__` with the `0..100` range checks and a **band-overlap** check (`med_min < high_min`) — the one cross-check that *is* an ordering rather than a policy, unlike the deliberately-absent `fraudFlagScoreMin` vs `siuFraudScoreMin` one. Say why the two cases differ in the comment.
- [x] `lineworker/server/data/versions/20260821_0045_fraud_band_thresholds.py` -- **new** migration cloning `0037`: `revision = "0045_fraud_band_thresholds"`, `down_revision = "0044_document_body_text"` (today's head), `ROWS = (("derivation_thresholds", 6, "derivation_thresholds.v6.jdm.json"),)` spelled out, `EFFECTIVE_FROM = date(2026, 8, 11)` because `DerivationThresholds.of` requires the new parameters and a gap would 500 every console surface, `downgrade` deleting the row, and a docstring recording that this bump invalidates every outstanding queue / worklist / drill cursor by design.
- [x] `lineworker/server/data/repositories/insights.py` -- add `select_fraud_insights(db, ctx)` returning `(claim_business_id, content, generated_at, model)` for `AiInsight.kind == InsightKind.fraud_risk_indicators`, joined to `claim` under `employer_scope(ctx)`, ordered by `Claim.claim_id`. Scope and nothing else — no `predicate` parameter, no `outcome` filter in SQL (the shape is the service's business, per the module's standing refusal).
- [x] `lineworker/server/services/worklist/fraud.py` -- **new**. The whole Fraud section's server half, in `charts.py`'s shape: a frozen `FraudClaim` projection built **named**, frozen results, pure folds, thin async wrappers taking the parameter block as an argument. (a) `fraud_panel` — the band distribution over **every claim in scope** plus the SIU pipeline grouped by `stage` and by `handler_id` (carrying `handler_name` beside it, `benchmarks.py:230-236`'s rule); (b) `fraud_rates` — flagged/total by injury type, employer and handler, each row publishing `flagged`, `claims` and `rate_bp` so the denominator is on screen, injury types cut at this module's own `Final` limit set to the same 8 the injury-type chart already cuts at — two views of one dimension must not disagree about where the tail starts — published with `truncated` per `charts.py:101-116`, employers and handlers uncapped; (c) `fraud_red_flags` — the clause frequency fold over the insight rows. Add a `_banded` helper beside a comment explaining why it **zero-fills every band** where `_declared` (`charts.py:284-287`) omits absent categories: the rule defines this vocabulary, so an empty band is a fact about the portfolio rather than a category the portfolio does not have. Both gates live here: a new `FRAUD_ANALYTICS_ROLES: Final = frozenset({UserRole.analyst})` allowlist and `require_fraud_analytics_access(ctx)` — a **new** constant and exception beside `benchmarks.PERMITTED_ROLES`, never an edit to it.
- [x] `lineworker/server/services/worklist/fraud.py` (red-flag fold) -- validate each row through `TypeAdapter(FraudRiskInsight)` and, on failure, exclude it and count it in `unreadable` rather than raising (`api/routers/claims.py:2963`'s tolerance). `low_risk` rows carry no `red_flags` key: they raise coverage and contribute no clause. Normalise each clause exactly once — casefold, collapse internal whitespace, strip a trailing full stop — keep the **first-seen original spelling** for display, count **distinct claims** (a claim naming one flag twice counts once), order by count descending then normalised clause ascending in `_ranked`'s manner and for `_ranked`'s recorded reason, and cut at this module's own `Final` limit, published with `truncated`. Publish `claimsWithInsight`, `claimsInScope`, `unreadable`, and the `generatedFrom`/`generatedTo` range (both `null` on a cold cache). The docstring must say plainly what this view is and is not: exact-text grouping over model-authored prose, so a count of 1 is the ordinary case and the ranking is a reading aid, not a taxonomy — the alternative, clustering, would be the server originating a classification nobody can version.
- [x] `lineworker/server/services/worklist/drill_through.py` + `server/api/routers/dashboard.py` -- add exactly two facets to `DrillFilters` and the `/dashboard/claims` query: `filter[fraudBand]` (a `FraudBand` value, matched through the registered derivation) and `filter[siuReview]` (a bool, matched through the registered `siu_review` derivation). Both join the existing filter set with no change to any existing facet's meaning; both become part of the cursor's compared `filters` payload automatically. Do not add a `predicate` to the repository.
- [x] `lineworker/server/api/routers/dashboard.py` -- three analyst-gated routes, each following `:331-336`'s decorator, `no-store` as the first body statement and restated in every `ProblemException`, and `require_fraud_analytics_access(ctx)` called at the top before any claim is read: `GET /dashboard/fraud` (no parameters), `GET /dashboard/fraud/rates` (exactly three parameters — `sort[injuryType]`, `sort[employer]`, `sort[handler]`, each a closed `FraudRateSort` enum over exactly `rate_desc | rate_asc | flagged_desc | claims_desc | label_asc`, defaulting to `rate_desc`, aliased in `filter[…]`'s style so the wire name is the param name), `GET /dashboard/fraud/red-flags` (no parameters). Response models inherit `ApiModel` and are built field by field. Add `FRAUD_ANALYTICS_FORBIDDEN_RESPONSE` as a module-local constant in `FORBIDDEN_RESPONSE`'s style — deliberately not imported from elsewhere.
- [x] `lineworker/server/services/worklist/fraud.py` (sorting) -- sorting is a **server-side total order over an uncapped list**, which is why these three tables carry no cursor and no `total` — `benchmarks.py:305-317`'s shape, chosen precisely because `drill_through.py:1234-1237` rules out an offset cursor into a re-orderable ranking. Every `FraudRateSort` value must be a total order with an explicit label tie-break, for `_ranked`'s recorded reason.
- [x] `lineworker/web/package.json` flow -- run `npm run generate:api` and commit `src/api/schema.d.ts`; CI diffs it.
- [x] `lineworker/web/src/api/dashboard.ts` + `queryKeys.ts` -- add `useFraudPanel()`, `useFraudRates(sorts)` and `useFraudRedFlags()` beside their siblings: `api.GET` + `return data!`, `staleTime: 30_000`, no `select`. Keys go in the `dashboard` group as `fraud`, `fraudRates(sortKey)` and `fraudRedFlags`, each with the group's standing docstring paragraph on why the key carries no persona, role or scope segment.
- [x] `lineworker/web/src/features/dashboard/drill/filters.ts` -- add `fraudBand` and `siuReview` to `FILTER_KEYS`, `toQueryParams`, `FILTER_LABEL` ("Fraud band", "SIU review") and their `VALUE_LABEL` maps ("Low"/"Medium"/"High", "Yes"/"No"). One file, per its own standing rule; `toQueryParams`'s generated type makes a rename a compile error.
- [x] `lineworker/web/src/features/shell/DashboardShell.tsx` + `routes.ts` + `App.tsx` -- the console's first navigation. Add `FRAUD_ROUTE = "/dashboard/fraud"` and a `WorkspaceNav` rendered **only when `me.role === "analyst"`**, offering exactly two destinations — Portfolio and Fraud — with the remaining Epic 7 sections not rendered at all. Route it as a child of the existing dashboard block wrapped in an inner `<RequireSession allow={["analyst"]} />`, so a supervisor is bounced to `homeRouteFor("supervisor")` and their dashboard is byte-identical to what Epic 5 shipped. `homeRouteFor("analyst")` stays `DASHBOARD_ROUTE`.
- [x] `lineworker/web/src/features/dashboard/fraud/FraudPage.tsx` -- **new**. `DashboardPage`'s composition rule exactly: the page owns the three queries and passes `{data, isPending, isError}` down; sections never fetch. Root `<section aria-label="Fraud analytics" data-testid="fraud-analytics" aria-busy={panel.isPending}>` with the `sr-only` `role="status"` line. Includes the flagged-claims panel, which is **the existing drill list** — a `KpiCard`-style entry point plus the first page through the existing `useDrillClaims` hook at `filter[fraudFlagged]=true`, never a second list implementation.
- [x] `lineworker/web/src/features/dashboard/fraud/FraudDistributionCard.tsx` + `SiuPipelineCard.tsx` -- **new**. `ChartFrame` + `DistributionBars`/`DistributionDonut` with `onSelect` wired through `drillHref` in `PortfolioCharts.tsx:142-144`'s curried style: band → `filter[fraudBand]`, pipeline stage → `filter[siuReview]=true` + `filter[stage]`, pipeline handler → `filter[siuReview]=true` + `filter[handlerId]`. Add a `FRAUD_BAND_FILL` map to `chartTheme.ts` reusing existing `var(--color-…)` values — no new colour enters the file.
- [x] `lineworker/web/src/features/dashboard/fraud/FraudRateTables.tsx` -- **new**. Three `ColumnSpec`/`COLUMNS` tables in `HandlerBenchmarkTable`'s idiom (one `isLoading` predicate driving `aria-busy` and skeletons; `isError → empty → data` branch order; `role="alert"` error replacing the table). Sort controls set the server parameter and refetch on the new key: **no `.sort(`, no `.reduce(`, no rate computed in the browser** — `rateBp` arrives formatted-ready and renders through `Intl`.
- [x] `lineworker/web/src/features/dashboard/fraud/RedFlagFrequencyCard.tsx` -- **new**. Renders through `InsightShell` (or a clone carrying its `data-kind`/`data-status` attributes and its `Generated … · model` line) so the view cannot ship without its provenance, `InsightBullets` for the ranked clauses in the server's order, `FRAUD_OUTCOME_LABEL`/`FRAUD_OUTCOME_TONE` for the vocabulary, and a caption stating the coverage (`claimsWithInsight` of `claimsInScope`) and the `generatedFrom`–`generatedTo` range. Cold cache renders a first-class empty card in `InsightShell.tsx:57`'s shape, never a spinner or a gap. Rows are **not** clickable — a clause is not a claim population, and inventing one would be the browser deciding which claims a phrase names.
- [x] `lineworker/web/src/features/queue/noDerivation.test.ts` -- register every new `features/dashboard/fraud/` file in the "the scan reaches the files it claims to" block with the per-file rationale comment the block requires, and extend `DERIVED_FIELDS` with the genuinely new wire names (`fraudBand`, `rateBp`, `flagged`, `claimsWithInsight`) — checking the alternation does not repeat an existing token, which 5.4's review caught once already.
- [x] `lineworker/server/tests/test_fraud_analytics.py` -- **new**, opening with the Story 7.1 banner docstring in `test_drill_through.py:1-24`'s form. The full convention battery per route (parameter count off the OpenAPI document, scope cannot be widened, camelCase exact key set, `no-store`, session required, no audit row, `test_the_aggregate_takes_exactly_one_scoped_read`, published thresholds come from the document, a superseded v7 moves the bands). The gate: parametrized over every `UserRole` so **supervisor is refused** — the deliberate inversion of `test_the_analyst_reads_byte_identically_to_the_supervisor`, which stays true for the endpoints it was written about — plus refusal before any claim read and before any rule document load. **Reconciliation**: the flagged population must equal `/dashboard/summary`'s `fraudFlagged` count and `/dashboard/claims?filter[fraudFlagged]=true`'s `total` on one scope, with `seed_fixture`'s independent oracle beside it. **Rule separation**: synthetic projections where `fraud_flag` is false at a score above 55, and where the score sits between 55 and 60, so the three fraud rules are distinguishable — the seed alone cannot tell them apart (`queue_flags.py:105-109`).
- [x] `lineworker/server/tests/test_fraud_analytics.py` (scope) -- **scope is asserted at the service level** with a hand-built `CallerContext(role=analyst, employer_ids=frozenset({…}))`, because the seed has exactly one analyst and they are `scope_all`; adding a scoped analyst persona would move persona counts in Stories 1.3, 1.4, 5.1 and the e2e login fixture, which is a change this story does not own. Assert every claim behind a scoped analyst's panel, rates and red flags is inside that book, and that no query parameter widens it at the HTTP layer. Record the residual — that there is no HTTP-level scoped-analyst path — in `deferred-work.md`.
- [x] `lineworker/server/tests/test_fraud_analytics.py` (red flags) -- a fixture that varies `red_flags` **per claim**, because `insight_fixture.py`'s `_CLAUSE` is one string and a frequency ranking over it collapses to a single row of count N. Cover: cold cache (empty items, `null` range, `200`); a `low_risk` row raising coverage and contributing no clause; a row failing `TypeAdapter` re-validation excluded and counted; one claim naming a clause twice counted once; two claims naming the same clause with different casing and trailing punctuation grouped once, displayed in the first-seen spelling; the count-then-clause tie-break; the cut and its `truncated` flag.
- [x] `lineworker/server/tests/seed_fixture.py` + `test_derivations.py` -- add a `# --- Story 7.1: the fraud workspace, restated independently ---` block with the band cut-offs restated (never imported) and oracles for the band distribution, the SIU pipeline and the three rate breakdowns. Extend `test_no_module_outside_the_registry_hardcodes_the_band` to cover the fraud numbers as well as 65/35; where an existing occurrence outside the allowlist is demonstrably not a rule value, leave that number out of the set rather than weakening the allowlist.
- [x] `lineworker/web/src/test/api-mock.ts` -- add the three fraud routes to the dashboard block **before** `/api/dashboard/claims` so a prefix cannot shadow them, with `StubRoutes` fields and docstrings, plus the house contrast fixtures: `FRAUD_PANEL`, `FRAUD_PANEL_SCOPED`, `FRAUD_PANEL_EMPTY`, `FRAUD_RATES`, `FRAUD_RATES_SORTED`, `FRAUD_RED_FLAGS`, `FRAUD_RED_FLAGS_EMPTY`.
- [x] `lineworker/web/src/features/dashboard/fraud/FraudPage.test.tsx` -- render through `FraudPage`, never a section alone. Loading (`"pending"` → `aria-busy="true"` + skeletons), error (`role="alert"`, no figures, no skeletons, `aria-busy="false"`), empty (the `*_EMPTY` fixtures), and data read off the accessible list renderings — never off Recharts' SVG. Assert the AI card's `data-kind`, `data-status` and its `Generated …` line are present whenever items are; assert a `LocationProbe` sees `filter[fraudBand]=high` after a legend click and `filter[siuReview]=true&filter[stage]=treatment` after a pipeline click; assert changing a sort control changes the request and not the rendered order in the browser.
- [x] `lineworker/web/src/App.test.tsx` + `features/shell/*.test.tsx` -- amend for the nav: an analyst sees exactly two destinations and reaches `/dashboard/fraud`; a supervisor sees **no nav at all** and is bounced from `/dashboard/fraud` to `/dashboard`; the existing shell `aria-label` assertions still hold.
- [x] `lineworker/e2e/fixtures/seed.ts` -- add a `// --- Story 7.1: the fraud workspace ---` block restating its own band cut-offs and exporting `expectedFraudAnalyticsFor(persona)` returning **rendered strings** per the file's standing rule: the band legend rows, the SIU pipeline rows, and the three rate tables' first rows.
- [x] `lineworker/e2e/stories/7-1-fraud-analytics-workspace.spec.ts` -- **new**, `test.describe("@story:7-1 @epic:7 fraud analytics workspace", …)`, tags on the describe only. Exactly one `@smoke`: login as the analyst → the Fraud nav destination → distribution and flagged list render → click the `high` band legend row **by `data-key`** → the Story 5.5 drill list with the chip visible and clearable → open a claim → read-only proved by absence (`textbox`/`combobox`/`copilot-pane` count 0). Then, not `@smoke`: a supervisor logging in sees no nav and is bounced from the URL; the SIU pipeline drills with both facets in the URL; a sort control re-orders a rate table and the URL/back-navigation still restores the dashboard; the red-flag card renders its **empty** state on the freshly reset stack; and — after driving the existing per-claim insight refresh against `model-stub` for one claim — it renders that claim's flags with a generation timestamp.
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- record what this story closed and what it did not: `fraud_band` now exists as a registered derivation (closing the entry at `:78`, whose remaining half — the investigation card's uncoloured score — belongs to Story 2.2's surface, not this one); the two wire spellings of `fraud_flag_score_min` (`:560`) are **not** settled here and this story adds no third spelling; and the absent HTTP-level scoped-analyst path is a new entry.
- [x] `_bmad-output/implementation-artifacts/sprint-status.yaml` -- `7-1-fraud-analytics-workspace: done` once the e2e gate passes; `epic-7` stays `in-progress`.

**Acceptance Criteria:**
- Given an analyst session, when the Fraud section renders, then the band distribution covers every claim in that analyst's scope with every band present (zero-filled), the flagged panel's population equals `/dashboard/summary`'s `fraudFlagged` and `/dashboard/claims?filter[fraudFlagged]=true`'s `total` on the same scope, and the SIU pipeline shows the `siu_review` population grouped by stage and by handler — all three read behind `employer_scope`, with no number computed in the browser.
- Given a supervisor or handler session, when any `/dashboard/fraud*` route is requested, then it answers 403 `/problems/fraud-analytics-not-permitted` with `Cache-Control: no-store`, before any claim is read and before any rule document is loaded, while every Epic 5 dashboard route continues to answer both roles byte-identically to what it answered before this story.
- Given the `derivation_thresholds` document is superseded by a v7 moving `fraudBandHighMin`, when the panel is read, then the band boundaries and the published threshold both follow the new document with no code change, and no fraud cut-off appears as a literal in `services/worklist/`, `api/` or `web/src`.
- Given fraud-kind `ai_insight` rows in scope, when the red-flag view computes, then clauses are grouped on their normalised text and counted by distinct claim, ranked count-descending then clause-ascending, rendered in the first-seen spelling with the coverage figure and the oldest/newest `generated_at`, with `low_risk` rows raising coverage and contributing no clause and unreadable rows excluded and counted — and given no such rows exist, then it answers 200 with an empty ranking and a `null` range, and the card renders a first-class empty state.
- Given any band segment, pipeline segment or rate-table row, when it is clicked, then the browser navigates to Story 5.5's drill list with the corresponding `filter[…]` params in the URL, the chips visible and clearable, scope re-resolved server-side on a direct load, and a claim opening in the existing read-only view with no edit affordance and no copilot pane.
- Given a rate table's sort control, when it changes, then a new request carries the new `sort[…]` value and the rendered order is the server's — the browser sorts nothing, and `noDerivation.test.ts` passes over every new file with each one registered in its scan block.

## Spec Change Log

## Review Triage Log

## Design Notes

**1. Why a band distribution rather than a decile histogram.** The seeded `fraud_score` runs 3–65, so a ten-bucket 0–100 histogram has four permanently empty top bins — and `charts.py:284-287` omits absent categories, which for a histogram loses the information the bin was there to carry. Bands dissolve the problem and buy three things a histogram does not: segments that name a population the drill list can filter on, a vocabulary the rules tier owns, and the severity-distribution idiom the console already reads. The cost is that `_banded` has to zero-fill where `_declared` omits, which is why it is a separate helper with the difference written down rather than a flag on the existing one.

**2. Three fraud rules, and why the third is not a fourth copy of the first two.**

```
siu_review     = fraud_flag and fraud_score >= siuFraudScoreMin      (60) -> 9 claims
fraud_flagged  = fraud_flag and fraud_score >= fraudFlagScoreMin     (55) -> 13 claims
fraud_band     = band(fraud_score) against fraudBandMedMin/HighMin   (35/55)
```

The first two are populations gated on a triage flag; the third is a reading of the score column on its own, so a claim nobody flagged still lands in a band. On today's seed `fraud_flag` and `score >= 55` coincide exactly, which means the seed cannot tell the three apart — the synthetic projections in the test file are not thoroughness, they are the only way the distinction is tested at all.

**3. The red-flag ranking is exact-text grouping, and the spec says so on screen.** `red_flags` is `list[str]` of model-authored clauses bounded at 220 characters, with no code, category or enum anywhere in the stored shape. Grouping on normalised text is the only operation that does not require the server to decide that two differently-worded sentences mean the same thing — and that decision, made silently, is exactly the kind of derived judgement AD-2 exists to keep out of the model's hands and AD-10 exists to keep in exactly one place. So: normalise, group, count distinct claims, publish the coverage, and let the caption admit that a count of 1 is ordinary. A real taxonomy is a product decision with an owner and a vocabulary; it is not a fold.

**4. Sorting without a cursor, because the codebase already ruled on the alternative.** `drill_through.py:1234-1237` refuses a `sort` parameter on a cursored list because an offset into one ranking means nothing against another. The three rate breakdowns are 20, 10 and 6 rows on the full portfolio, so they take `benchmarks.py:305-317`'s shape instead — `items` *is* the list, no `total`, no cursor — and sorting is then just a total order the server applies. The injury-type cut stays because 20 rows is a chart's problem, not a page's, and it is published with `truncated` exactly as `charts.py:101-116` does.

**5. The flagged list is not a new endpoint.** `/dashboard/claims?filter[fraudFlagged]=true` is already the flagged-claims list: cursor-paginated, scoped, server-sorted, tested for the review-vs-referral rule at `test_drill_through.py:794`, with an e2e oracle at `seed.ts:2727`. Story 7.1's task list asks for a flagged-claims list endpoint; building a second one would fork the component the same task list forbids forking. The Fraud section embeds the existing one.

**6. "Status" in the acceptance criterion means `stage`.** The SIU pipeline is grouped on `stage` (`intake | investigation | treatment | settled`), not on `status`. `summary.py:152-161` already records why: `status` carries 6 seeded values that mix lifecycle with disposition, `stage` is the four-value lifecycle every other surface groups on, and `stage` is the facet the drill list already accepts — so grouping on `status` would produce segments no click could resolve. The epics' wording is the looser word for the same idea.

**7. Scope is asserted where an analyst can actually be scoped.** There is exactly one analyst in the seed and they are `scope_all`, so no HTTP request can demonstrate a *narrowed* analyst. Seeding a scoped analyst would move persona counts across four earlier stories and the e2e login fixture — out of scope here. The service takes `CallerContext` directly, so the AD-7 assertion is made there, with the HTTP layer still carrying "no query parameter can widen the scope". The gap is named in `deferred-work.md` rather than papered over.

## Verification

**Commands:**
- `cd lineworker/server && uv run ruff check . ../deploy && uv run ruff format --check . ../deploy` -- expected: clean.
- `cd lineworker/server && uv run mypy .` -- expected: clean under strict; the three `sort[…]` enums and the `FraudRiskInsight` `TypeAdapter` must typecheck without `Any` or `cast`.
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker uv run pytest` -- expected: all pass, including the amended `test_derivations.py` grep and every pre-existing dashboard test.
- `cd lineworker/server && uv run alembic check` -- expected: "No new upgrade operations detected"; this story adds a data row, not schema.
- `cd lineworker/web && npm run generate:api && git diff --exit-code -- src/api/schema.d.ts` -- expected: no diff after committing the regenerated file.
- `cd lineworker/web && npm run lint && npm run typecheck && npm test` -- expected: clean; `noDerivation.test.ts` must both pass and report the new files as scanned.
- `cd lineworker/server && grep -rnE "\b(35|55|60)\b" services/worklist/fraud.py api/routers/dashboard.py` -- expected: no match.
- `cd lineworker/web && grep -rnE "\.sort\(|\.reduce\(" src/features/dashboard/fraud/` -- expected: no match.
- `cd lineworker && docker compose -f deploy/compose.e2e.yaml up -d --build --wait` -- expected: all services healthy including `model-stub`.
- `cd lineworker/e2e && npm run typecheck && npx playwright test --grep "@story:7-1\b|@story:5-5\b|@story:5-1\b|@story:2-1\b"` -- expected: pass. 5-5 because two facets join its filter vocabulary, 5-1 because the fraud KPI must still reconcile, 2-1 because the thresholds bump invalidates queue cursors.
- `cd lineworker/e2e && npm run smoke` -- expected: pass, with exactly one `@smoke` test in the new spec.

**Manual checks (if no CLI):**
- Log in as the analyst: the nav shows exactly Portfolio and Fraud, and no unbuilt Epic 7 slot is visible.
- Log in as the supervisor: the dashboard is visually unchanged from Epic 5, there is no nav, and typing `/dashboard/fraud` returns to `/dashboard`.
- On a freshly reset stack, the red-flag card shows its empty state with no spinner and no gap; after refreshing one claim's insights it shows that claim's clauses with a generation timestamp and the model name.
- Click a band segment, then the browser back button: the fraud panel returns with its figures intact and the drill chip is gone.

## Auto Run Result

Status: implemented, all verification green, committed.

### Neither `Block If` triggered, and both were checked rather than assumed

**The two facets joined `/dashboard/claims` without changing what any existing facet returns.**
`DrillFilters` gained `fraud_band` and `siu_review` as two appended fields, so `FILTER_KEYS`,
`WIRE_KEYS`, `_PREDICATES` and `_COERCE` all grew by two through the assertions that already run at
import, and no line of any of the twelve existing predicates changed. The position is load-bearing
and is now asserted: `FILTER_KEYS[-2:]` is pinned, because inserting `fraud_band` beside
`fraud_flagged` — where it reads more naturally — would silently re-order the chip row on every
drill URL carrying both. `test_the_two_new_facets_join_the_vocabulary_and_change_no_other` reads the
OpenAPI parameter set and then re-checks three pre-existing facets against `seed_fixture`'s oracle,
which is the same oracle `test_drill_through.py` uses; the full 5.5 e2e spec passes unchanged.

**Gating the fraud routes needed no edit to `benchmarks.PERMITTED_ROLES`.**
`fraud.FRAUD_ANALYTICS_ROLES: Final = frozenset({UserRole.analyst})` is a second allowlist beside it,
with `require_fraud_analytics_access` beside `require_benchmarks_access` and
`/problems/fraud-analytics-not-permitted` beside `/problems/benchmarks-not-permitted`. That is the
whole of the story's inversion: `test_portfolio_charts.py::
test_the_analyst_reads_byte_identically_to_the_supervisor` still passes,
`test_every_epic_five_dashboard_route_still_answers_a_supervisor` asserts the other direction, and
`test_a_supervisor_and_a_handler_are_both_refused` asserts this one across all three routes.

### Verification

| Check | Result |
|---|---|
| `ruff check` + `ruff format --check` over `. ../deploy` | clean, 284 files |
| `mypy` strict | clean, 270 files; the three `sort[…]` enums and the insight reader typecheck with no `Any` and no `cast` |
| `alembic check` | "No new upgrade operations detected" — 0045 adds a data row, not schema |
| `pytest` against live Postgres | **2862 passed, 1 skipped** (95 of them new in `test_fraud_analytics.py`) |
| `grep -rnE "\b(35\|55\|60)\b" services/worklist/fraud.py api/routers/dashboard.py` | no match |
| `grep -rnE "\.sort\(\|\.reduce\(" web/src/features/dashboard/fraud/` | no match |
| `npm run generate:api` | idempotent (identical sha over two runs); `schema.d.ts` regenerated and committed |
| `npm run lint` / `typecheck` | clean (0 errors; the 11 warnings are pre-existing `react-refresh` notes) |
| `npm test` | **694 vitest tests passing**, 21 of them new in `FraudPage.test.tsx` |
| `noDerivation.test.ts` | passes, and reports all five `features/dashboard/fraud/` files plus `features/shell/WorkspaceNav.tsx` as scanned |
| `e2e npm run typecheck` | clean |
| Playwright `--grep "@story:7-1\|@story:5-5\|@story:5-1\|@story:2-1"` | **36 passed** |
| Playwright, full suite | **225 passed** |
| `npm run smoke` | **33 passed**, exactly one of them this story's |

### Deviations from this spec's letter

1. **The derivation's module is `services/derivations/fraud_score_band.py`, not `fraud_band.py`.**
   Forced by `test_no_derivation_module_is_named_after_the_value_it_exports`: a module named after
   the value it exports rebinds the package attribute from the submodule to the `Derivation`, so
   `services.derivations.fraud_band.FraudBand` would raise `AttributeError` with no obvious cause.
   `risk_band.py` exporting `risk` is the precedent the rule was written from. The registered name
   is still `fraud_band` and every call site reads `derivations.fraud_band`.
2. **`red_flags_of` takes the stored-card reader as an injected callable rather than importing
   `agents.schemas`.** The spec says "validate each row through `TypeAdapter(FraudRiskInsight)`",
   which is what happens — but `tests/test_layering.py` machine-checks that `services/` never
   imports `agents/` (AD-5), and the first draft failed it. The `TypeAdapter` therefore lives in
   `agents/schemas.py::read_fraud_clauses` and `api/routers/dashboard.py` passes it down;
   `services/rag/insights.py`'s injected `InsightGenerator` is the precedent and gives the same
   argument at length. There is still exactly one `TypeAdapter` for that union in the build, and the
   reader's three answers — clauses, `[]` for low-risk, `None` for unreadable — are what keep the
   coverage figure honest.
3. **`fraud_red_flags` takes two scoped reads, and its test says so in its name.** The spec's
   convention battery lists `test_the_aggregate_takes_exactly_one_scoped_read`. The published
   coverage is "claims with a fraud narrative, out of claims in scope", and the second half cannot
   come from the insight join — a claim with no `ai_insight` row produces no row to count. The
   panel and the rate breakdowns each take exactly one, pinned; the red-flag view takes exactly two,
   pinned by `test_the_red_flag_aggregate_takes_exactly_two_scoped_reads`, and the departure is
   recorded in `deferred-work.md` rather than hidden behind a renamed test.
4. **`test_no_module_outside_the_registry_hardcodes_the_band` gained `55` and deliberately not
   `60`.** The spec sanctions exactly this ("where an existing occurrence outside the allowlist is
   demonstrably not a rule value, leave that number out of the set"). Six files outside the
   allowlist carry a bare 60 and none is a rule value — two HTTP timeouts, a basis-points note, a
   list slice, and two *prose* mentions of `score >= 60` written to explain why the comparison is
   not in the code. The test's docstring enumerates all six; `deferred-work.md` records what stays
   uncovered.
5. **The superseded-document test publishes v7 rather than v6, and lowers the band edge to 40
   rather than to something smaller.** v6 is now a seeded row, so the two Epic 5 tests that
   published a "v6" moved to v7 with it. 40 rather than 20 because `fraudBandMedMin` is 35 and
   `rules/parameters.py` refuses an inverted pair at load — which the first draft discovered by
   500ing, and which is itself the point: a retune that made `medium` unreachable is a refusal at
   the boundary rather than a silently two-segment chart.
6. **`FraudRateTables.tsx`'s docstring names the banned array operation rather than spelling it.**
   The spec's verification grep over `features/dashboard/fraud/` is comment-inclusive, and the
   sentence explaining why there is no client-side re-ordering matched it. `noDerivation.test.ts` —
   the real guard — strips comments before scanning, so nothing was weakened; the comment says so.

### What is on `deferred-work.md`

Six entries. One is a **partial closure**: 2.2's "the investigation card's fraud score is
uncoloured because those cut-offs appear in no rule document" is now unblocked — `fraud_band` exists
with both edges in `derivation_thresholds` v6 — but the card itself is untouched, because
`ClaimDetailResponse` publishes neither the band nor the edges and adding them is a contract change
to a story this one does not own. The five new entries are: the absent HTTP-level scoped-analyst
path (the spec's own Design Note 7, asserted at the service level instead); the two wire spellings of
`fraud_flag_score_min`, unsettled and now sitting on a dashboard publishing four fraud numbers of
which two are one parameter and two are different parameters carrying the same integer; the
red-flag view's second scoped read; the fifth endpoint to re-fold the whole book per request, which
this surface multiplies by *sort clicks*; and the rate tables' sort state being component state
rather than URL state, deliberately left for Story 7.3 to decide alongside its segmentation filters.
