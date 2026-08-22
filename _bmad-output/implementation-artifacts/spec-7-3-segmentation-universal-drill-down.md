---
title: 'Story 7.3 — Segmentation & Universal Drill-Down'
type: 'feature'
created: '2026-08-22'
baseline_revision: '9bdecdbbac29d9cc881d5f06098a6261380f45d4'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: true # sixteen findings were patched, two of them high, and both highs were surfaces stating something false to the analyst — a stranded filter reported as "unfiltered", and eight fraud surfaces plus the trend cards describing an intersection in the language of the whole portfolio. What a follow-up should read is whether the zero-result copy is now correct on every surface a filter can empty (the fix touched ten of them and the e2e assertion was widened to match, but the trends side was reached last), whether the two rewritten oracles are genuinely independent of the server and of each other this time rather than a third restatement, and whether the patch pass's wire change (DrillClaimsResponse now publishes the three age edges) and its tightened rule-document validation (a working-age domain plus a minimum band spread, which changes which documents are loadable at all) hold against the seeded v7 and every re-pinned test
context:
  - '{project-root}/_bmad-output/implementation-artifacts/7-3-segmentation-universal-drill-down.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-7-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-7-2-trend-cohort-analytics.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-7-1-fraud-analytics-workspace.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-5-5-dashboard-drill-through.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The analyst workspace has two sections whose aggregates answer one question each over the caller's whole book. Nothing narrows them: `fraud_panel`, `fraud_rates`, `fraud_red_flags` and `portfolio_trends` take no filter, four of the nine segmentation dimensions (`region`, `icd`, `employee.age`, `employee.gender`) have **no reader anywhere in `server/`**, `employee.age` has no banding rule, and both sections keep their controls in component state with a comment naming this story as the owner of the workspace's address bar. Every aggregate is therefore a dead end in the one direction an analyst works: narrow, then look again.

**Approach:** One segmentation vocabulary — declared once as a subset of the twenty facets `drill_through.py` already ships, so a workspace filter and a drill-through filter are the *same words* and a drill URL is a merge rather than a translation. Four facets are appended to that vocabulary, one new rules-tier banding gives `age` a band, every analyst aggregate gains one `Segmentation` parameter applied inside the service, a values endpoint publishes what the caller can pick, and the workspace's query string becomes the single home of filter and control state.

## Boundaries & Constraints

**Always:**
- **One vocabulary, not two.** The nine dimensions are ten facets, and six of them — `severity_band`, `injury_type`, `state`, `employer_id`, `disability`, `sector` — are already shipped facets of `DrillFilters` (`drill_through.py:328-347`). Segmentation reuses those names, those wire spellings and those value vocabularies exactly. A second spelling of a shipped facet is the failure this story exists to avoid: AC 2 requires the active segmentation to survive a click into the Story 5.5 list, and that only composes if both sides say `filter[severityBand]`.
- **Append, never insert.** The four new facets (`region`, `icd10`, `ageGroup`, `gender`) go on the **end** of `DrillFilters` in that order. `FILTER_KEYS` is read off the dataclass's field order (`:356`), that order is the chip row's draw order, and every drill URL ever generated depends on it. `drill_through.py` declares each facet in four places asserted set-equal at import (`DrillFilters:254`, `WIRE_KEYS:372`, `_PREDICATES:541`, `_COERCE:848`) — a facet added to three of them fails at import, and that is the guard, not a nuisance.
- **AD-7: a filter narrows, it can never widen.** Segmentation is applied in the **service**, over rows a scope-predicated read already returned. `claims.py:266-275` and `:394-409` refuse a `predicate` parameter on the repository twice and in writing; this story does not reopen that. Two of the ten dimensions are derived (`severity_band`, `age_group`), so a SQL narrowing would put part of the filter in `data/` and part in `services/` — the exact split those docstrings refuse. The role gate (`fraud.FRAUD_ANALYTICS_ROLES`) still fires before any read and before any rule document loads.
- **AD-8/AD-10: `age` gets exactly one band computer, and its edges are data.** `services/derivations/age_band.py` registers `age_band`, built from `DerivationThresholds` like every sibling (`registry.py:41` — `build` takes that block and nothing else, so the edges live on a **v7 of `derivation_thresholds`**, not on a new document). It is modelled on `risk_band.py:36-83` field for field. `severity_band` keeps coming from the registered `risk` derivation; nothing re-bands.
- **The band vocabulary is Python, the edges are JDM, and the *label* is composed from the published edges.** `AgeBand` members are ordinal words, never numbers: a member spelled `age_25_34` encodes a cut-off, which would put the same rule element in both tiers and would lie the day an edge moved. The payload publishes the edges; the UI composes "25–34" from them.
- **New cut-offs refuse equality.** `DerivationThresholds.__post_init__` must reject equal age edges with `>=`, not `>`. `deferred-work.md:519` records that the shipped `risk_med_min == risk_high_min` hole collapses a band silently and that Story 5.2's review required `>=` for every parameter added after it. Do not tighten the shipped risk pair here — that is its own change.
- **One scoped read per aggregate, still.** Segmentation adds no read to `fraud_panel`, `fraud_rates` or `portfolio_trends`; it narrows the fold. `test_the_panel_takes_exactly_one_scoped_read` and the parametrised trends query-count guard must stay green **with a filter applied**, and the values endpoint is itself one read and one pure fold.
- **Zero result is an answer, not an error or an empty page.** An impossible combination is a 200 with empty aggregates, and every card, chart and table says "no claims match these filters" in a state distinct from loading and from error. Emptiness is decided the way `DistributionDonut.tsx:118-127` decides it — on a total, never on row count — because zero-filled vocabularies produce rows for nothing.
- **The values endpoint only ever names values that match something.** Distinct values are folded from the caller's own scoped rows, so a picker cannot offer a value with no claims and cannot enumerate anything outside the book. This is what keeps `AppliedFilter.display`'s two null cases (`deferred-work.md:619`) from becoming reachable through the control.
- **One query string for the workspace.** `filter[<camelKey>]` is reserved for segmentation and is spelled identically to the drill list's. Section controls (`grain`, `anchor`, `cohort`, and the three fraud sorts) are bare named params and move out of component state into the URL — `FraudPage.tsx:158-166` and `TrendsPage.tsx:21-26` both defer that decision to this story, and deciding it without applying it leaves two schemes in one address bar.
- **AD-9: the filter is in every key.** Server data reaches the SPA only through TanStack Query with a segmentation-inclusive key serialised as an ordered string (the `toFilterKey`/`toTrendParamsKey` idiom, never `JSON.stringify`), under the unowned-prefix rule `queryKeys.ts:188-200` states. No cached aggregate is ever re-filtered in the browser.
- **AD-15: `e2e/stories/7-3-segmentation-universal-drill-down.spec.ts` passes** against the freshly reset stack before this story is done, tagged `@story:7-3 @epic:7` with exactly one `@smoke`.

**Block If:**
- Applying segmentation to an aggregate cannot be done without a second scoped read, or without moving part of the filter into SQL. Both are boundaries above; if one is genuinely unavoidable, stop rather than half-build it.
- Giving `trends` its `employee` columns cannot be done without changing the rows `charts.portfolio_charts` reads.

**Never:**
- No new analytics content — no new chart, KPI or metric. Financial decomposition is 7.4; export is 7.5; saved/named filter sets are out of scope, the URL is the sharing mechanism.
- Do not touch Epic 5's supervisor endpoints or screens, or `benchmarks.PERMITTED_ROLES`. Only the shared 5.5 drill components are extended, in place, never forked.
- Do not seed a scoped analyst. The seed's one analyst is `scope_all`, and adding a second moves persona counts in Stories 1.3, 1.4 and 5.1 plus `e2e/fixtures/login.ts` (`deferred-work.md:746`). AD-7 is asserted at the service level on a hand-built narrowed `CallerContext` — including the empty-book case — exactly as 7.1 and 7.2 do it, and the HTTP layer carries the smuggled-parameter check. The gap stays named in `deferred-work.md`.
- Do not rename `rulesVersion`, `fraudScoreMin`/`fraudFlagScoreMin`, or publish a third spelling of either (`deferred-work.md:751`, `:777`). New payloads reuse the existing spellings.
- Do not make `ClaimCard` polymorphic, do not fix the offset cursor, and do not add the thirteenth SLA facet (`deferred-work.md:591`) — three standing entries this story does not own.
- **Do not make red-flag rows drillable.** `RedFlagFrequencyCard` is the one analyst surface with no `drillHref`, and it stays that way: a red flag is a normalised free-text clause from the `ai_insight` cache, so a facet over it would make cached AI narrative a query dimension over claim data — which AD-10 forbids and which Story 7.1's Design Note 3 already refused as a product decision rather than a fold. The card says so on screen instead of offering a click that cannot be honoured.
- No number in a route, a service, or anywhere under `web/src`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Single dimension | `filter[sector]=Aerospace` on `/dashboard/fraud` | Panel folded over Aerospace claims only; `appliedFilters` carries one chip | No error expected |
| AND across dimensions | `filter[sector]=Aerospace&filter[gender]=female` | Claims matching both; two chips in `FILTER_KEYS` order | No error expected |
| Derived dimensions | `filter[severityBand]=high` with any `filter[ageGroup]` member | Banded by the registered `risk` and `age_band` computers, never re-derived | No error expected |
| Impossible combination | A filter set no claim satisfies | 200; every aggregate empty; each surface renders its zero-result state; `total: 0` | Never an error |
| Unknown enum value | `filter[gender]=nonsense` | 422 problem+json before the service runs | FastAPI type validation |
| Unknown free-text value | `filter[sector]=Nonexistent` | 200, empty result — a legitimate empty page | No error |
| Unknown param name | `filter[nope]=x` | Silently ignored; produces no chip | No error |
| Invalid value in a shared URL | Page loads with a refused facet | That dimension clears, an inline message names it, the rest of the filter still applies; never a crash | Inline, non-blocking |
| Scope tamper | Analyst adds `?employerId=…&scopeAll=true&as=…` | Byte-identical response to the honest read | Params ignored, never honoured |
| Non-analyst | Supervisor or handler requests any analyst route | 403 problem+json, `no-store`, before any claim row or rule document is read | `_fraud_forbidden` |
| Empty window + cohort | Trends window containing no claims, `cohort` set | Bucket vocabulary still published, so the period select and "View claims" stay live | Closes `deferred-work.md` 7.2 entry |
| Values in a narrow book | Scoped caller asks for dimension values | Only values carried by claims in that caller's book | No error |

</intent-contract>

## Code Map

**Server — the facet vocabulary (the thing being extended)**
- `server/services/worklist/drill_through.py` — `DrillClaim:150` (projection), `DrillFilters:254` with its twenty fields `:328-347` and the "append, never insert" docstring `:297-301`; `FILTER_KEYS:356` read off the field order; `WIRE_KEYS:372` (+ set-equality assert `:395`); `AppliedFilter:401`; `_PREDICATES:541` (+ assert `:588`); `matches:594` (AND over `is not None`, not truthiness); `_COERCE:848` (+ assert `:879`); `select:984` (pure: derive → match → rank); `_applied:1078` (chips resolved from rows already read, never a second query); `drill_through_claims:1126`.
- `server/data/repositories/claims.py:354` `select_drill_rows` — already inner-joins `Employee`, `Employer` and an aliased `AppUser` (`:446-449`); its projection `:434-445` is where `Claim.icd`, `Claim.region`, `Employee.age`, `Employee.gender` land. `:179` `select_claim_columns_with_employer` — **no `employee` join**, and shared with `charts.portfolio_charts`. `:62` `employer_scope`. The twice-recorded refusal of a `predicate` parameter: `:266-275`, `:394-409`.
- `server/data/models/core.py` — `Employee:70` (`gender:76`, `age:77` — a stored int, no DOB), `Employer.sector:66`, `Claim:104` (`state:117`, `region:118`, `injury_type:129`, `icd:133`, `severity_score:135`, `disability:136`). `region` and `icd` are real columns no facet reads today.

**Server — the aggregates gaining a parameter**
- `server/services/worklist/fraud.py` — `FRAUD_ANALYTICS_ROLES:121`, `require_fraud_analytics_access:201`, `FraudClaim:222` (9 fields), `_Computers.of:272-292`, folds `panel_of:477` / `rates_of:874` / `red_flags_of:1150`, `_projection:1264`, and the three services `fraud_panel:1298`, `fraud_rates:1325`, `fraud_red_flags:1347` (the documented two-read departure).
- `server/services/worklist/trends.py` — `TrendCohort:233` whose docstring `:245-248` hands segmentation to this story by name; `TrendClaim:575`; `cohort_key_of:842`; `trends_of:1019`; `portfolio_trends:1224` with its gate `:1279`, window `:1281` and the one read `:1282-1296`.
- `server/services/worklist/sla.py:163` `SAMPLE_COLUMNS` (callers compose `[*SAMPLE_COLUMNS, …]`).
- `server/services/derivations/risk_band.py:27-83` — `RiskBand`, `RiskDerivation.of`, `sql_band`/`sql_is`, `register`. The template for `age_band`.
- `server/services/derivations/registry.py:41` — `build: Callable[[DerivationThresholds], T]`; `register:50` refuses a duplicate name. This signature is why age edges belong on `derivation_thresholds`.
- `server/rules/parameters.py` — `DerivationThresholds:179` (risk edges `:186-187`, fraud band edges `:243-244`, validation `:247-260`), `thresholds_for:541`; `TrendPeriods:1209` / `trend_periods_for:1308` as the new-document precedent this story deliberately does **not** follow.
- `server/data/versions/20260821_0045_fraud_band_thresholds.py` — the migration to clone (`ROWS` spelled out, effective-date rule, argument in the docstring); `20260821_0046_trend_periods.py` is the newest.

**Server — routes**
- `server/api/routers/dashboard.py` — router `:132`; `drill_claims:1172` with the `filter[…]` idiom (`filter[stage]:1178`, `filter[severityBand]:1183`, `filter[injuryType]:1219`, `filter[employerId]:1236`, `filter[disability]:1318`, `filter[sector]:1325`), `# noqa: PLR0913` `:1172`, and `DrillFilters` built field by field `:1484-1505` refusing a `**locals()` shortcut. Analyst routes: `/dashboard/fraud:1689`, `/dashboard/fraud/rates:1950`, `/dashboard/fraud/red-flags:2118`, `/dashboard/trends:2449`. Gate ordering — `no-store` first, role gate second, rule blocks third, service fourth (`:1735-1749`). Translators `_fraud_forbidden:1577`, `_trend_range_invalid:2206`; `FRAUD_ANALYTICS_FORBIDDEN_RESPONSE:1565`.
- `server/api/schemas.py:17` `ApiModel`.

**Web**
- `web/src/features/dashboard/drill/filters.ts` — `FILTER_KEYS:54-82` (20, order load-bearing, append rule `:48-53`), `DrillFilters:102`, `paramNameOf:105`, `fromSearchParams:121`, `toSearchParams:137`, `toQueryParams:171-202` (spelled out key by key so a server rename is a compile error), converters `boolValue:213`/`idValue:226`/`enumValue:239`/`dateValue:256`, `toFilterKey:291`, `drillHref:299`, `FILTER_LABEL:343-378`, `VALUE_LABEL:454-475`, `chipLabel:485`, `fallbackValueLabel:507`.
- `web/src/features/dashboard/drill/FilterChips.tsx` — `Chip:36-64` (`CHIP_CLASS`, `data-testid="drill-chip"`, per-chip remove button), `FilterChips:66-115`, "Clear all" gated on `applied.length !== 1` written as an equality for `noDerivation`. `DrillClaimsPage.tsx` — `fromSearchParams:88`, `moveTo:147`, `removeFilter:152`, 422 fallback `:131`, empty state naming the filters `:231-240`.
- `web/src/features/shell/DashboardShell.tsx:43-53` — the shared shell (`TopBar` → `WorkspaceNav` → `<main><Outlet/></main>`), deliberately role-free. `WorkspaceNav.tsx` — `SECTION_ROUTES:70`, `inSection:79`, `DESTINATIONS:123-146`. `App.tsx:70-81` — the inner analyst-only guard.
- `web/src/features/dashboard/fraud/FraudPage.tsx` — composition rule `:15-19`, `sorts` state `:168` with the 7.3 note `:158-166`, `pendingSort:191`, drill sets `FLAGGED:67`, `KpiCard drill=:263`.
- `web/src/features/dashboard/trends/TrendsPage.tsx` — one query `:374`, params state `:373` with the 7.3 note `:21-26`, `planOf:330`, `COHORT_FACET:122-127`, `filtersFor:436-450`, `openClaims:452-460`, the `Selector` control `:669-709`.
- `web/src/api/queryKeys.ts` — `drillClaims:152`, `fraud:202`, `fraudRates:219`, `trends:272`; the blast-radius lesson `:188-200` and the unowned-prefix note `:259-266` that pre-authorises this story's keys.
- `web/src/api/dashboard.ts` — canonical hook idiom, `toFraudRateSortKey:400`, `toTrendParamsKey:551`, `placeholderData: keepPreviousData` `:439`/`:596`, and the no-`select` rule `:230-236`.
- `web/src/features/dashboard/charts/ChartFrame.tsx:98-134` (four exclusive states, fixed body height, always-reserved footnote), `DistributionDonut.tsx:118-127` (the `total === 0` emptiness rule).
- `web/src/components/ui/` — `select.tsx:177`, `popover.tsx:48`, `checkbox.tsx:34` are vendored and usable; `badge.tsx` is vendored and used nowhere — chips are hand-rolled from `CHIP_CLASS` (`statusTone.ts:40`), as `FilterChips.tsx:13-16` states outright.
- `web/src/features/queue/noDerivation.test.ts` — `ROOTS:50-71` (includes `features/dashboard`), `DERIVED_FIELDS:204-556` (property-scoped entries where a bare word is too common), `FORBIDDEN:566-637`, and the per-file registration block `:662-868` every new file must join with its own prose.

**Tests / oracles**
- `server/tests/test_trend_analytics.py` — the closest template: gate-without-a-DB `:1175`, parametrised refusal `:1187`, refusal-ordering `:1208`, appended-facet order `:1230`, `context_for:1286`, `_counting:1393` (restated, never imported), query-count per grain × cohort `:1413`, contract half `:1873-2120`.
- `server/tests/test_drill_through.py` — per-facet tests `:243-374`, totality guard `:465`, cursor `:478-601`, unknown free string = empty page `:1026`, unknown enum = 422 `:1052`, applied-filter chips `:1206`, route parameter-count contract `:1274`, `test_query_parameters_cannot_widen_or_change_the_scope:1296`, one-scoped-read `:1469`.
- `server/tests/test_derivations.py:544-602` — `test_no_module_outside_the_registry_hardcodes_the_band`: regex `(?<![\w.])(65|55|35)(?![\w.])` over every `*.py` and `*.jdm.json`, comments included; allowlist `:581-587`; the "leave the number out rather than weaken the allowlist, and say why" rule `:569-579`. Also `test_no_derivation_module_is_named_after_the_value_it_exports:523`.
- `server/tests/test_sla_aggregation.py:298-395` — the Hypothesis idiom (strategies, `@given`) this story's scope-containment property follows.
- `server/tests/seed_fixture.py` — doctrine `:1-13`, `HIGH_RISK_MIN:31`, the separate-constants ruling `:75-82`, Story 7.1 banner `:1212-1250`, Story 7.2 banner `:1470-1530` ("every constant is read into the answer — a constant nothing reads protects nothing").
- `e2e/fixtures/seed.ts:4-17` the independent-oracle doctrine; drill helpers `DrillFilters:2543-2608`, `expectedDrillClaimsFor:2834`, `drillUrl:2888`; 7.2 block `:3146-3160`.
- `e2e/fixtures/test.ts:21-38` (`_freshDbPerFile`, throws unless `workers === 1`), `e2e/fixtures/login.ts:26-43` (`PERSONAS`), `e2e/fixtures/reset.ts:61-108` (`resetDb`).
- Smuggled-parameter precedents: `e2e/stories/7-2-trend-cohort-analytics.spec.ts:539-568` (honest vs tampered fetch, byte-identical, **plus** the on-screen figure against the oracle so two matching wrong answers still fail) and `:570-591` (role gate from the URL); `e2e/stories/5-5-dashboard-drill-through.spec.ts:269-292` (scoped supervisor).

## Tasks & Acceptance

**Execution:**
- [x] `server/rules/documents/derivation_thresholds.v7.jdm.json` -- clone v6 and add three age-band edges -- the edges are parameters, and v7 rather than a new document because `registry.Derivation.build` takes `DerivationThresholds` and nothing else.
- [x] `server/rules/parameters.py` -- add the three age edge fields to `DerivationThresholds` and validate them in `__post_init__`: inside a sane human-age range, strictly ordered, **equality refused with `>=`** -- an equal pair collapses a band silently, which is the shipped hole `deferred-work.md:519` records and Story 5.2's review closed for every parameter added after it.
- [x] `server/data/versions/20260822_0047_age_band_thresholds.py` -- insert the v7 row, `ROWS` spelled out, effective-date rule and the whole argument in the docstring -- clone `20260821_0045_fraud_band_thresholds.py`.
- [x] `server/services/derivations/age_band.py` -- `AgeBand` StrEnum with **ordinal, non-numeric** members and `AgeBandDerivation.of(age)`, registered as `age_band`, modelled on `risk_band.py:36-83` -- one computer for the value; a numeric member name would put the cut-off in both tiers.
- [x] `server/services/derivations/__init__.py` -- import the new module so registration happens -- the import list *is* the registry.
- [x] `server/services/worklist/segmentation.py` -- new module owning `Segmentation` (ten fields, wire spellings identical to the drill facets), `SEGMENTATION_KEYS`, a `SegmentedClaim` structural Protocol (the `claim_money.PaidColumns` idiom — a projection satisfies it by shape), `matches`, `applied_of`, `values_of` and `to_drill_filters`; assert at import that its keys and spellings are a **subset** of `drill_through.FILTER_KEYS`/`WIRE_KEYS` and that its predicate map is total -- one vocabulary, asserted rather than remembered.
- [x] `server/services/worklist/drill_through.py` -- append `region`, `icd10`, `age_group`, `gender` to `DrillFilters` in that order and to `WIRE_KEYS`, `_PREDICATES` and `_COERCE`; `age_group` bands through the registered derivation in `_flags_of` -- four declarations or the import fails, which is the point.
- [x] `server/data/repositories/claims.py` -- widen `select_drill_rows`' projection with `Claim.icd`, `Claim.region`, `Employee.age`, `Employee.gender` (all three joins already exist), and add the projection-family member that reaches `employee` for trends -- **no existing caller's query shape may change**, so `select_claim_columns_with_employer` is not given a second join in place while `charts.portfolio_charts` shares it.
- [x] `server/services/worklist/fraud.py` -- widen `FraudClaim`/`_projection` with the dimension columns and thread one `Segmentation` parameter through `fraud_panel`, `fraud_rates` and `fraud_red_flags`, narrowing before the fold -- still one scoped read each (two for red flags, unchanged).
- [x] `server/services/worklist/trends.py` -- widen `TrendClaim`, switch to the employee-reaching read, accept `Segmentation`, and **publish the window's bucket vocabulary independently of the series** -- the last part closes the 7.2 entry where an empty window under a cohort split killed the period select and the drill button.
- [x] `server/services/worklist/__init__.py` -- re-export the new public names -- match the existing surface.
- [x] `server/api/routers/dashboard.py` (segmentation) -- declare the ten `filter[…]` params **once** as a FastAPI dependency yielding a `Segmentation`, and take it on `/dashboard/fraud`, `/dashboard/fraud/rates`, `/dashboard/fraud/red-flags` and `/dashboard/trends` -- ten params written out five times is five places to drift; the gate ordering (`no-store`, role, rules, service) is unchanged.
- [x] `server/api/routers/dashboard.py` (drill) -- append `filter[region]`, `filter[icd10]`, `filter[ageGroup]`, `filter[gender]` to `GET /dashboard/claims` in that order and build them into `DrillFilters` field by field -- appended, because `appliedFilters` order is the chip row's order.
- [x] `server/api/routers/dashboard.py` (values) -- `GET /dashboard/segmentation/values`: analyst-gated, one scoped read, one pure fold, publishing each dimension's distinct in-scope values plus the age edges the UI composes its labels from -- a picker that can only offer values the caller's book actually carries.
- [x] `web/openapi.json` + `web/src/api/schema.d.ts` -- regenerate via `npm run generate:api` **after all server route work** -- CI fails on an undiffed client.
- [x] `web/src/features/dashboard/drill/filters.ts` -- append the same four facets to `FILTER_KEYS` in the identical order with one spelled-out `toQueryParams` line each, their `FILTER_LABEL` entries, and export `SEGMENTATION_KEYS` plus the merge helper drill targets use -- the order must match the server's or the chip row desyncs.
- [x] `web/src/features/dashboard/segmentation/useSegmentation.ts` -- the workspace URL contract in one hook: parse segmentation and the section controls out of `useSearchParams`, write them back, degrade an invalid value to a cleared dimension plus a message -- `filter[…]` for segmentation, bare names for controls, one scheme.
- [x] `web/src/features/dashboard/segmentation/SegmentationBar.tsx` -- per-dimension pickers over the values endpoint and the active filter as clearable chips with clear-all, hand-rolled from `CHIP_CLASS` -- reuse `FilterChips`' chip and aria idiom rather than a second chip.
- [x] `web/src/features/shell/DashboardShell.tsx` -- mount the bar for the analyst sections only, keeping the shell's role branch where `WorkspaceNav` already keeps it -- 7.4 inherits the bar for free.
- [x] `web/src/api/queryKeys.ts` -- re-key the four analyst reads on the segmentation and add `segmentationValues`, as leaves under prefixes nothing owns -- the blast-radius rule.
- [x] `web/src/api/dashboard.ts` -- thread segmentation through the four hooks with an ordered-string key serialiser beside `toFraudRateSortKey`/`toTrendParamsKey`, keep `placeholderData: keepPreviousData`, add the values hook, no `select` -- a filter change keeps the previous render and reports busy only where it changed.
- [x] `web/src/features/dashboard/fraud/FraudPage.tsx` + `trends/TrendsPage.tsx` -- consume the segmentation, move `sorts` and `grain`/`anchor`/`cohort` from component state into the URL, and merge the active segmentation into every drill target -- the two files whose comments name this story.
- [x] `web/src/features/dashboard/fraud/RedFlagFrequencyCard.tsx` -- state on the card why its rows are the one surface that does not drill -- an unexplained dead row reads as the bug AC 2 forbids; the reason is AD-10, not an omission.
- [x] `web/src/features/queue/noDerivation.test.ts` -- register the new `features/dashboard/segmentation/*` files in the scanned block with their own prose and append genuinely-new wire names to `DERIVED_FIELDS`, property-scoped where the bare word is common -- read the alternation before adding to it.
- [x] `server/tests/test_segmentation.py` -- the pure half (AND across dimensions, OR is not offered, band edges, unknown-value emptiness, subset/totality guards, chip order) and the DB half (`context_for` with a hand-built narrowed and empty-book analyst, role allowlist over every `UserRole`, refusal before read *and* before rule load, one-scoped-read under a filter, values-endpoint scoping, reconciliation of a filtered aggregate against the drill list's `total`) -- covers the I/O matrix.
- [x] `server/tests/test_segmentation.py` (property) -- a Hypothesis property over arbitrary `Segmentation` values: the filtered set is always a **subset** of the unfiltered scoped set, and filtering twice equals filtering once -- the first property test on an analyst aggregate, and the honest form of "a filter can only narrow".
- [x] `server/tests/test_derivations.py` + `test_drill_through.py` -- extend the hardcoded-band guard with any new age edge whose every in-tree occurrence is already allowlisted (otherwise leave it out and say why, as 7.1 did for 60), and update the route parameter-count and facet-order contracts -- a guard that had to be weakened to pass is not a guard.
- [x] `server/tests/seed_fixture.py` -- a Story 7.3 banner restating the dimensions and the age edges under their own names, every constant read into the answer -- constants spelled separately even where they coincide with a risk or fraud edge.
- [x] `web/src/features/dashboard/segmentation/SegmentationBar.test.tsx` -- chip add/clear/clear-all lifecycle, query-key inclusion, URL parse/serialise round-trip, invalid-value degradation, zero-result rendering, loading and error isolation -- reads accessible output, never chart internals.
- [x] `e2e/fixtures/seed.ts` -- `expectedSegmentedFor(persona, filters)` restated independently from the raw seed, sharing no helper with the server or with the existing drill oracle -- a filter is a cut, and 7.1's lesson was an oracle that carried the implementation's cut.
- [x] `e2e/stories/7-3-segmentation-universal-drill-down.spec.ts` -- `@story:7-3 @epic:7`, one `@smoke`: analyst login → apply two filters → both sections recompute → click a chart segment → the drill list carries both chips **and** the slice → open the read-only claim view; plus a URL-reload restore, an impossible-combination zero-result assertion, an invalid-URL-value degradation, and the smuggled-parameter check via `page.evaluate(fetch)` asserting byte-identical bodies *and* the on-screen figure against the oracle -- the AD-15 done-gate.

**Acceptance Criteria:**
- Given an analyst session with filters on two dimensions, when the workspace renders, then every KPI, chart and table in both sections is folded server-side over the intersection, and no figure on screen was recomputed in the browser.
- Given a filter set applied in the workspace, when the analyst clicks any KPI, chart segment or table row, then the Story 5.5 list opens carrying the active segmentation **and** the clicked slice, each as an independently clearable chip, and its rows open the read-only claim view with no edit affordance.
- Given a workspace URL carrying filters and section controls, when it is shared or reloaded, then the full state restores from the URL and scope re-resolves server-side on that load — and no parameter added to it widens the caller's book.
- Given a filter combination no claim satisfies, when the workspace renders, then every card, chart and table shows its zero-result state — decided on a total, not on a row count — the chips stay clearable, and nothing errors.
- Given a URL carrying a value the server refuses, when the page loads, then that dimension clears with an inline message naming it, the remaining filters still apply, and the page renders.
- Given a supervisor or handler session, when any analyst route is requested directly, then a 403 problem+json is returned before a claim row is read and before a rule document is loaded.
- Given a caller whose book is narrower than the portfolio, when the dimension-values endpoint answers, then it names only values carried by claims inside that book.
- Given a filter is applied or changed, when the new data is in flight, then previously rendered surfaces stay visible and only the affected surface reports busy.

## Spec Change Log

## Review Triage Log

### 2026-08-22 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 16: (high 2, medium 8, low 6)
- defer: 4: (high 0, medium 1, low 3)
- reject: 0
- addressed_findings:
  - `[high]` `[patch]` A failed `/dashboard/segmentation/values` request rendered its error paragraph *instead of* the bar, but `FraudPage` and `TrendsPage` read the filter from the URL independently — so every figure below stayed folded over the intersection while the chips, "Clear all" and all ten pickers vanished, and the message asserted "The figures below are unfiltered." False precisely when a dimension was set: the analyst read a subset as portfolio-wide with no way out but the address bar. The alert now renders *above* a chip row drawn from the URL, and the copy is conditional. The existing test passed because it exercised the error branch with **no filter applied** — the one configuration in which the sentence was true; the filtered case is now tested.
  - `[high]` `[patch]` AC 4's per-surface zero-result state was never built — only the bar got one. Eight fraud surfaces plus the trend cards said "portfolio" while describing an intersection, so a zero-claim filter rendered "No claim in this portfolio is under SIU review.", a false statement about the book on a screen whose whole subject is a subset of it; `RedFlagFrequencyCard` additionally blamed a cold AI cache for what the filter did. `isFiltered` is now threaded into every card, chart and table, and the e2e AC-4 test asserts all eight fraud surfaces and two trend cards rather than only the bar.
  - `[medium]` `[patch]` The age-edge validation closed one end only: `0 <= edge <= 120` plus strict ordering accepted `ageYoungerMin: 0` (deleting the `youngest` band, labels composing "Under 0") and `(0, 1, 2)`, after which a 22-year-old banded as `oldest`, three of four bands were unreachable, every picker still rendered and no test failed — verbatim the failure the block's own comment claimed to prevent. Replaced with a working-age range and a minimum first-to-last spread, which is what catches `(14, 15, 16)`.
  - `[medium]` `[patch]` A picker reported "Any" for a filter that was applied: options are folded from the caller's book, so any value the book does not carry left the chip row saying the dimension was set, every figure at zero, and the picker saying it was not. `useSegmentation.ts` argues at length that a `<select>` must never carry a value none of its options has, and had applied that discipline only to the six control params. The URL's value is now a transient option marked "(no claims)".
  - `[medium]` `[patch]` Both new oracles reproduced the implementation instead of checking it — the third occurrence of the failure mode 7.1 and 7.2 were reviewed for. `seed_fixture.py` restated the server's sort key character-for-character including the `or` that treats an empty label as absent; it now executes the two rules independently and uses `label is None`. `e2e/fixtures/seed.ts` sorted with `localeCompare` in direct violation of the `byCodePoint` ruling written twice in that same file, and keyed a `Set` by **label** so two employers sharing a `short_name` would have made a missing option look correct — now keyed by value and sorted by code point.
  - `[medium]` `[patch]` A shipped contract guard was weakened rather than narrowed: a blanket `startswith("sort[")` skip exempted all ten new dimensions from the "every parameter is a closed enum" assertion on `/dashboard/fraud/rates`, including the four that *are* closed enums and whose type-is-the-refusal property is this feature's entire validation story. The six genuinely open parameters are now exempted by name, and the resolver follows `$ref`/`anyOf`/`allOf` so a vocabulary moved into a component cannot pass it either.
  - `[medium]` `[patch]` One value rendered under two names one click apart — `filter[ageGroup]=older` read "Age group: 45–54" on the workspace and "Age group: older" on the Story 5.5 list the click opened, which is the divergence this same diff condemns at `SegmentationBar.tsx:169-174` and a weak form of AC 2's "survives the click as a clearable chip". The drill payload now publishes the three edges (rather than a server-composed label, which is the second copy `DimensionValueResponse` refuses in writing) and both surfaces compose from the same helper.
  - `[medium]` `[patch]` The import-time vocabulary guards were brittle in two directions: they compared `dataclasses.Field.type` across two modules, which works only while neither file has `from __future__ import annotations` — a routine ruff `FA` autofix on either side would have turned one to strings and broken the import — and all four were `assert`, so the "asserted rather than remembered" guarantee vanished under `python -O`. Now explicit raises over `typing.get_type_hints()`.
  - `[low]` `[patch]` An empty book with no filter told the analyst to clear a filter they never set; the copy now distinguishes empty-book from empty-segment.
  - `[low]` `[patch]` After any rate table had been sorted, a segmentation change made one untouched table report busy while the other two swapped silently; `requestedSort` now resets when the segmentation key changes.
  - `[low]` `[patch]` A parametrised refusal test's `match=edge.replace("_min","Min")[:3]` evaluated to the regex `"age"` for all three parameters, so it passed if the message mentioned "age" anywhere — an implementation refusing one edge while naming another would have been green. It now matches the actual parameter name.
  - `[low]` `[patch]` On the red-flags route a "Belt and braces — unreachable today" comment had migrated *above* the new unconditional `thresholds_for(db)` call, labelling a rule-document read as unreachable. Moved back below the load, with the reason the document is read there at all.
  - `[low]` `[patch]` `segmentation.py` and a test asserted twice that `Segmentation.__post_init__` "refuses nothing" and built an argument on it; the class declares no `__post_init__`. The prose now states the actual ruling.
  - `[low]` `[patch]` `_segmentation` was a sync `def` FastAPI dependency, costing every request to all five analyst routes a threadpool hop to build a frozen dataclass; now `async def`.

## Design Notes

**1. The segmentation filter is not a new filter.** Six of the ten facets are already shipped in `DrillFilters`, and AC 2 requires the workspace filter to survive a click into the list that consumes `DrillFilters`. Building a second schema would mean a translation layer between two spellings of `severityBand` whose only job is to be correct, forever, in both directions — and `filters.ts:11-16` already records that the URL uses the API's own alias precisely so no such layer exists. So `Segmentation` is declared as a **subset of one vocabulary**, asserted at import against `drill_through`'s keys and wire names. The import direction is one-way and deliberate: `drill_through.py`'s field order is load-bearing for every drill URL ever generated (`:297-301`), so the shipped block stays where it is and the new module points at it, rather than the vocabulary being re-homed and every chip renumbered.

**2. Why age bands are `derivation_thresholds` v7 and not a new document.** `TrendPeriods` is the recent precedent for a new document, and its argument was that a window is not a derivation cut-off. Age bands are the opposite: they *are* a derivation cut-off, and `registry.py:41` makes that decisive — `Derivation.build` is `Callable[[DerivationThresholds], T]`, one block for the whole registry, so a computer whose parameters lived elsewhere could not be registered without changing the registry's shape for every derivation that already works. v7 is the consistent home.

**3. The band names carry no numbers, and that is the whole point.** An analyst wants `25–34`, and the temptation is to spell the member `age_25_34`. That puts the cut-off in the Python tier *and* the JDM tier, which AD-8 forbids in one sentence — and it is a lie waiting to happen, because moving the edge in the document leaves the member's name asserting the old one. So the members are ordinal words, the edges are published on the payload, and the UI composes the range label from the published edges. Three edges give four bands, in the shape `risk_band.py` already ships:

```python
class AgeBand(StrEnum):      # wire values; the UI owns the label
    youngest = "youngest"    # age <  younger_min
    younger  = "younger"     # age >= younger_min
    older    = "older"       # age >= older_min
    oldest   = "oldest"      # age >= oldest_min
```

Move an edge in v7 and every legend follows; nothing drifts, and no chip ever contradicts the document that produced it.

**4. The `GROUP BY` push-down: decided here, because 7.2 said this story is the trigger.** Story 7.2's Design Note 4 stated the revisit condition as "a scope beyond roughly 10⁴ claims **or Story 7.3 multiplying grain × cohort × segmentation on one request". That condition is now reached and the answer is still **no push-down**, for a reason specific to what a filter is: segmentation *narrows the fold* — it adds no read, and it multiplies nothing. Grain × cohort already multiplied and is bounded by `maxBuckets`. Two of the ten dimensions are derived, so a SQL predicate would put part of one filter in `data/` and the rest in `services/`, which is the split `claims.py:394-409` refuses in writing and the reason "the list reconciles with the number that opened it" is a property of one rule called once. The trigger for the seventh endpoint is therefore restated as scope size alone, and the multiplication clause is retired as answered.

**5. Zero result is now routine, which changes two things.** Until this story an empty aggregate meant an empty book — rare, and every surface could treat it as an edge case. A nine-dimension AND makes emptiness a normal outcome of a normal gesture, so the zero-result state has to be as designed as the happy path: distinct from loading, distinct from error, chips still clearable (or the analyst is stranded), and decided on a total rather than a row count because zero-filled vocabularies produce rows for nothing (`DistributionDonut.tsx:118-127`). It is also why trends must publish its bucket vocabulary independently of its series: `deferred-work.md` records that an empty window under a cohort split published zero series and killed the period select and the drill button — a control going dead was tolerable while empty was rare and is not now.

**6. What the oracles must not do.** A filter is a cut, and 7.1's review found an oracle that carried the implementation's own cut and therefore agreed with it; 7.2's found the same shape twice more. So the e2e oracle re-derives each dimension from the raw seed with its own predicates, shares no helper with the server *or* with the existing drill oracle, and the specs assert **which claims** survive a filter (set identity) alongside the counts — a count is satisfiable by the wrong population. The age edges are restated in `seed_fixture.py` under their own names even where they coincide with a risk or fraud edge, per the ruling at `:1229-1234`.

**7. Scope is asserted where an analyst can be scoped — again, and for the last time cheaply.** The seed's one analyst is `scope_all`, so no HTTP request can demonstrate a narrowed analyst; 7.1 and 7.2 both recorded this rather than papering over it, and the fix (one seeded scoped analyst) moves persona counts across three earlier stories plus the login fixture. This story does not touch the seed, so it inherits the same shape: AD-7 is proved at the service level against hand-built narrowed and empty-book contexts, strengthened here by a Hypothesis property that no `Segmentation` value can produce a row outside the unfiltered scoped set, and the HTTP layer carries the smuggled-parameter check. The entry stays open in `deferred-work.md`, now naming 7.5 (which touches export and its audit trail) as the next candidate.

## Verification

**Commands:**
- `cd lineworker/server && uv run ruff check . ../deploy && uv run ruff format --check . ../deploy` -- expected: clean.
- `cd lineworker/server && uv run mypy .` -- expected: clean under strict.
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker uv run pytest` -- expected: all pass; `test_no_module_outside_the_registry_hardcodes_the_band`, `test_nothing_outside_the_worklist_aggregation_reads_the_sla_source_columns`, both one-scoped-read guards and the facet-order contract all green.
- `cd lineworker/web && npm run generate:api && git diff --exit-code -- src/api/schema.d.ts openapi.json` -- expected: no diff after the regeneration is committed.
- `cd lineworker/web && npm run lint && npm run typecheck && npm test` -- expected: clean; `noDerivation.test.ts` green including the scan-reaches-its-files assertion.
- `cd lineworker/e2e && npm run typecheck` -- expected: clean.
- `docker compose -f lineworker/deploy/compose.e2e.yaml up -d --build --wait && cd lineworker/e2e && npx playwright test --grep "@story:7-3\b|@smoke\b"` -- expected: the new spec and every `@smoke` pass against the freshly reset stack.

## Auto Run Result

Status: done

**What was implemented.** One segmentation vocabulary for the analyst workspace, declared as an asserted subset of the twenty facets `drill_through.py` already shipped rather than as a second filter model — so a workspace filter and a drill-through filter are the same words and a drill URL is a merge, not a translation. Four facets (`region`, `icd10`, `ageGroup`, `gender`) were appended to that vocabulary; `employee.age` gained its first reader in the codebase and its first banding rule; the four analyst aggregates each gained one `Segmentation` parameter applied inside the service over rows a scope-predicated read already returned; a values endpoint publishes only what the caller's own book carries; and the workspace query string became the single home of filter and control state, closing the two entries where Stories 7.1 and 7.2 parked their sort and grain/anchor/cohort state pending this story's decision.

**Files changed.**
- `server/rules/documents/derivation_thresholds.v7.jdm.json`, `data/versions/20260822_0047_age_band_thresholds.py`, `rules/parameters.py` — three age edges as rules-tier parameters, with a working-age domain, strict ordering, refused equality and a minimum spread.
- `server/services/derivations/worker_age_band.py` (+ `__init__.py`) — `AgeBand` and its one registered computer, named for the rule rather than the value so the module-naming guard stays satisfied.
- `server/services/worklist/segmentation.py` — the vocabulary: `Segmentation`, `SEGMENTATION_KEYS`, structural claim protocols, `matches`, `narrowed`, `applied_of`, `values_of`, `to_drill_filters`, and four import-time guards that raise rather than assert.
- `server/services/worklist/{drill_through,fraud,trends,__init__}.py`, `data/repositories/claims.py` — four appended facets in all four declaration sites; widened projections; a new employee-reaching member of the projection family so no existing caller's query shape changed; trends now publishes its bucket vocabulary independently of its series.
- `server/api/routers/dashboard.py` — the ten `filter[…]` params declared once as an async dependency and taken by four analyst routes, four appended facets on the drill list, and `GET /dashboard/segmentation/values`.
- `web/src/features/dashboard/segmentation/{ageBands,useSegmentation,SegmentationBar}.ts(x)` — the URL contract and the control; `drill/{filters,FilterChips}.ts(x)`, `api/{queryKeys,dashboard}.ts`, `shell/{DashboardShell,WorkspaceNav}.tsx`, the five fraud components and `TrendsPage.tsx`.
- Tests: `server/tests/test_segmentation.py` (including the first Hypothesis property on an analyst aggregate), `seed_fixture.py`'s Story 7.3 oracle, `SegmentationBar.test.tsx`, `e2e/fixtures/seed.ts`, `e2e/stories/7-3-segmentation-universal-drill-down.spec.ts`, and thirteen existing server test files re-pinned to document v7.

**Review findings.** Two adversarial passes (general + edge-case) produced 20 distinct findings after deduplication: **16 patched** (2 high, 8 medium, 6 low), **4 deferred**, 0 rejected, 0 intent gaps, 0 spec defects — no loopback. The two high findings were both surfaces telling the analyst something false: a failed values request stranded a filter the analyst could neither see nor clear while asserting the figures were unfiltered, and AC 4's per-surface zero-result state had been built only on the bar, leaving eight fraud surfaces and the trend cards describing an intersection in the language of the whole portfolio. Notably, both new test oracles had reproduced the implementation rather than checked it — the third appearance of the failure mode Stories 7.1 and 7.2 were each reviewed for — and one shipped contract guard had been weakened to pass rather than narrowed.

**Verification.** Every command below was run by the orchestrator after patching, not only by the implementing agent.

| Gate | Result |
| --- | --- |
| `ruff check` / `ruff format --check` | pass — 291 files |
| `mypy .` (strict) | pass — 277 source files |
| `pytest` (DB-backed, pg on 55432) | **3043 passed, 1 skipped** |
| web `lint` / `typecheck` / `test` | pass — 0 errors; **757 tests** |
| `e2e typecheck` | pass |
| Playwright `--grep "@story:7-3\|@smoke"` | **42 passed**, incl. all 8 Story 7.3 tests |

**Residual risks.**
- The seed's one analyst is `scope_all`, so no HTTP request demonstrates a *narrowed* analyst. AD-7 is proved at the service level against hand-built narrowed and empty-book contexts and strengthened here by a Hypothesis property that no `Segmentation` value can produce a row outside the unfiltered scoped set — but the composition through `api.deps.get_caller_context` remains inferred for analysts, as it was after 7.1 and 7.2.
- The epic text and the story's Task 1 specify multi-value, OR-composed dimensions; this spec and `DrillFilters` are single-valued. The single-valued reading was built and asserted structurally, and the divergence is recorded in `deferred-work.md` — adding OR later moves the wire form, the drill cursor's byte-compared filter payload, the one-chip-per-facet row and every oracle.
- `claimsInScope` on the fraud panel and the trends payload now count the segmented book; the unfiltered count moved to the values endpoint. That is a semantic change to two already-shipped wire fields.
- The patch pass changed a wire payload (`DrillClaimsResponse` now publishes the age edges), rewrote both oracles, and tightened which rule documents are loadable at all — which is why a follow-up review is recommended.
