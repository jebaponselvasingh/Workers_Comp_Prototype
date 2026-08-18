---
title: 'Story 5.1 — Portfolio KPI Cards'
type: 'feature'
created: '2026-08-18'
baseline_revision: 'e5468680bd162dd5430150ca215f3f68c01ca2ce'
final_revision: 'e8126b4a720c29daad8404c049e5d357c105e4ee'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: true # 23 patches across a shipped migration, the client-side derivation guard, the dashboard's error/announcement paths and four reformatted shared fixtures — one of them a real hole in the guard AC 4 leans on; breadth and the migration's blast radius warrant an independent look
context:
  - '{project-root}/_bmad-output/implementation-artifacts/5-1-portfolio-kpi-cards.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-5-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Supervisors and analysts land on `DashboardShell`, whose entire body reads "Portfolio dashboard arrives in Epic 5." The prototype's ten KPI cards were computed in the browser by `renderSV()` folding the whole 100-claim array with client-side scoping, so nothing about volume, exposure or risk concentration exists server-side and no persona can see their own book's figures.

**Approach:** Add `portfolio_summary` to `services/worklist` — one scoped projection read folded by a pure function that asks the registered derivations for every band and total — expose it as a parameter-free `GET /dashboard/summary`, and replace the `DashboardShell` placeholder with the portfolio header, dataset chip and two rows of KPI cards. The dashboard's "fraud flagged" rule (score ≥ 55) does not yet exist as a parameter — the JDM document carries only the *SIU referral* threshold (60) — so this story adds it as a registered derivation over a new versioned parameter rather than letting the card compare a score itself.

## Boundaries & Constraints

**Always:**
- Every figure is computed in `services/worklist` behind `employer_scope` (AD-7). The endpoint takes **no parameters at all** — no employer, no user, no "as" — exactly like `/stats/topbar`.
- Bands, flags and totals come from the registered derivations in `services/derivations` (AD-10): `risk` for High Risk, `fraud_flagged` for Fraud Flags, `total_paid` for Total Paid. Nothing re-derives them, in Python or in SQL.
- Both thresholds resolve through `thresholds_for(db)` → `DerivationThresholds` (AD-8). The response carries them so the card captions read the live values.
- Money crosses the wire as integer cents on `*Cents`-suffixed fields and is formatted only by `web/src/lib/money.ts::formatCents`.
- The SPA computes nothing: no totals, no percentages, no comparisons. `features/dashboard` joins `noDerivation.test.ts`'s roots in this story.
- No file under `server/` outside the derivations/rules/tests/seed/versions allowlist may contain a bare `65` or `35` — *including in comments and docstrings* (`test_no_module_outside_the_registry_hardcodes_the_band` greps for it).
- `aria-label="Portfolio dashboard"` survives on whatever replaces the placeholder section (`web/src/App.test.tsx` asserts it three times).

**Block If:**
- The seeded portfolio's `litigation_flag` and `attorney_rep` columns disagree on any claim (they agree on all 100 today) — the Litigation card would then be counting one of two different things and needs an owner, not a dev-time pick.
- Adding `fraudFlagScoreMin` to `DerivationThresholds` would require re-dating any *existing* rule document, or `alembic check` reports drift the new migration did not cause.

**Never:**
- No handler benchmarking (5.2), no Recharts and no chart of any kind (5.3 — do not add the dependency), no top-30 worklist (5.4), no click, href or drill-through behaviour (5.5). `KpiCard` accepts its content only; it grows an `onClick` in 5.5 without redesign.
- No analyst-specific variation — the analyst reads this same dashboard until Epic 7.
- No mutating endpoint, no audit event, no toast: this surface is read-only, and queries are not mutations.
- No new table. Epic 5 creates none.
- No dark theme. The Story 1.1 light tokens are canonical; epics.md's "dark console aesthetic" is a recorded discrepancy.
- Do not reuse `siu_review` for the Fraud Flags card — it is the SIU *referral* rule at a different threshold and counts 9 claims where the card counts 13.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Full-portfolio persona | Session for David Bline (`scope_all`) | 100 claims · 28 under treatment · 62 settled · 32 high risk · 167049700 paid cents · 47050100 reserve cents · 13 fraud flags · 64 OSHA · 3 litigation · 39 surgery; chip counts 10 employers, 29 plants | No error expected |
| Scoped persona | Session for Jennifer Park (Toyota/GM/3M) | 27 · 5 · 20 · 10 · 63293700 · 8973300 · 3 · 18 · 0 · 11; chip counts 3 employers, 9 plants. Every one strictly below David Bline's, every counted claim's employer inside her assignment set | No error expected |
| Empty book | `CallerContext` with `employer_ids=frozenset()` | All ten figures `0`; thresholds and `rulesVersion` still populated | No error expected |
| Thresholds superseded | A `derivation_thresholds` v6 raising `fraudFlagScoreMin` effective today | Fraud Flags count drops and the card's caption follows it, with no code change | No error expected |
| No session | Request without a session cookie | `401` problem+json from the app-level auth dependency | Router declares `UNAUTHENTICATED_RESPONSE` |
| Fetch in flight | Query pending | Ten card skeletons, container `aria-busy` | Never render `0` for an unknown figure |
| Fetch failed | `ApiError` from the endpoint | Inline `role="alert"` message in place of the rows | No native dialog, no retry loop, no zeros |

</intent-contract>

## Code Map

- `server/services/worklist/stats.py` -- `topbar_stats`: the aggregate to imitate for shape (frozen dataclass out, thresholds in, no role branch).
- `server/services/worklist/sla.py` -- `sla_strip` + pure `strip_of`: the precedent for *this* story's mechanism — one `select_claim_columns` read folded in Python because the values are derived, not columns.
- `server/data/repositories/claims.py` -- `employer_scope` (AD-7 predicate), `select_claim_columns` (the scoped projection read).
- `server/services/derivations/risk_band.py` -- `risk`: High Risk's computer, and the template a new derivation copies.
- `server/services/derivations/queue_flags.py` -- `siu_review` at `siuFraudScoreMin` (60); the new `fraud_flagged` (55) belongs beside it so the two thresholds are legible together.
- `server/services/derivations/claim_money.py` -- `total_paid` and its `PaidColumns` protocol, which exists so a service can pass a row projection.
- `server/rules/parameters.py` -- `DerivationThresholds`, its `__post_init__` validation, `.of()` camelCase mapping, `thresholds_for(db)`.
- `server/rules/documents/derivation_thresholds.v4.jdm.json` -- current parameters; v5 supersedes it, never edits it.
- `server/data/versions/20260814_0024_benefit_rules.py` -- the rule-document migration pattern (spelled-out `ROWS`, `EFFECTIVE_FROM`, reflect-and-insert, delete on downgrade). Head is `0036_seed_email_templates`.
- `server/api/routers/stats.py` -- the parameter-free read-only router, `no-store`, `UNAUTHENTICATED_RESPONSE`, thresholds loaded in the route.
- `server/api/app.py` -- `create_app`'s `include_router` block; `root_path="/api"` supplies the prefix, routers carry none.
- `server/tests/test_topbar_stats.py` -- `context_for`, `make_client`, `login_as`; copy all three.
- `server/tests/seed_fixture.py` -- the independent oracle: expectations re-derived from `seed_data.json`, never from the service.
- `server/tests/test_derivations.py` -- `test_no_module_outside_the_registry_hardcodes_the_band` and the registry inventory assertions.
- `web/src/features/shell/DashboardShell.tsx` -- the placeholder to replace; keeps `TopBar` and the `aria-label`.
- `web/src/features/claim-detail/Cards.tsx` -- `CaseCard`: the dense card anatomy KPI cards copy (shadcn `Card` is deliberately unused).
- `web/src/features/shell/TopBar.tsx` -- `StatTile`: the value/label/tone anatomy, and the three figures the dashboard must **not** repeat.
- `web/src/api/stats.ts`, `web/src/api/queryKeys.ts`, `web/src/lib/money.ts`, `web/src/test/api-mock.ts` -- the hook, key, formatter and fetch-stub conventions.
- `web/src/features/queue/noDerivation.test.ts` -- `ROOTS`, which this story extends.
- `e2e/fixtures/{test,login,selectors,seed}.ts` -- the spec harness: `_freshDbPerFile`, `PERSONAS`, `byRole`/`byTestId`, seed-derived expectations.
- `docs/Workers_Comp_Prototype.html` -- `renderSV()` (line 1001) and the `.kpi`/`.krow` styles (lines 47–60): the design contract for card order, labels, captions and tones.

## Tasks & Acceptance

**Execution:**

- [x] `server/rules/documents/derivation_thresholds.v5.jdm.json` -- copy v4 and add `fraudFlagScoreMin: "55"` -- the dashboard's fraud rule is a parameter, not a literal in an aggregate (AD-8); v4 is never edited.
- [x] `server/rules/parameters.py` -- add `fraud_flag_score_min: int` to `DerivationThresholds`, map it in `.of()`, and validate it in `__post_init__` against the same 0–100 score range as `siu_fraud_score_min` -- a required field, so the parameter and its reader ship together.
- [x] `server/data/versions/20260818_0037_fraud_flag_threshold.py` -- new revision `0037_fraud_flag_threshold`, `down_revision = "0036_seed_email_templates"`, inserting `("derivation_thresholds", 5, "derivation_thresholds.v5.jdm.json")` with `EFFECTIVE_FROM = date(2026, 8, 11)`; `downgrade` deletes `(key, version)` -- v4's own date, because the new field is *required*: a later date would leave a gap in which `DerivationThresholds` cannot be built and every console surface 500s.
- [x] `server/services/derivations/queue_flags.py` -- add `FraudFlaggedDerivation` (`fraud_flag and fraud_score >= fraud_flag_score_min`) and register it as `fraud_flagged`, with a docstring stating plainly why it is not `siu_review` -- one computer for the dashboard card, 5.4's worklist population and Epic 7's fraud workspace.
- [x] `server/services/derivations/__init__.py` -- export `FraudFlaggedDerivation` and `fraud_flagged`, extend the narration -- a derivation not imported here does not exist to `get()`.
- [x] `server/services/worklist/summary.py` -- new module: a frozen `PortfolioSummary`, a **pure** `summary_of(caseload, risk, fraud_flagged, total_paid)`, and `portfolio_summary(db, ctx, thresholds)` doing one `select_claim_columns` read and folding it -- `sla_strip`'s split, so the arithmetic is database-free and Hypothesis-testable and the scoped read happens once.
- [x] `server/services/worklist/__init__.py` -- re-export `summary`, `PortfolioSummary`, `portfolio_summary` in the sorted `__all__` -- the package's export convention.
- [x] `server/api/routers/dashboard.py` -- `router = APIRouter(tags=["dashboard"])`, `GET /dashboard/summary` taking only `ctx`, `db`, `response`; sets `Cache-Control: no-store`, loads `await thresholds_for(db)`, declares `UNAUTHENTICATED_RESPONSE`, returns a flat `ApiModel` -- a signature with nowhere to put a scope is what AD-7 looks like in practice.
- [x] `server/api/routers/__init__.py` + `server/api/app.py` -- export `dashboard_router` and register it -- app-level auth covers it automatically.
- [x] `server/tests/seed_fixture.py` -- add `expected_portfolio_summary(name, role)` re-deriving all ten figures plus employer/plant counts from `seed_data.json` -- expectations must be an independent oracle, not the service under test.
- [x] `server/tests/test_portfolio_summary.py` -- new suite covering every I/O Matrix row: David Bline's ten figures against the oracle, Jennifer Park strictly smaller with every counted claim in her employer set, the empty-`frozenset` all-zero case, thresholds and `rulesVersion` arriving from the JDM resolution path (assert against `thresholds_for(db)`, never a literal), a v6 document inserted effective today changing the fraud count and `rulesVersion` with no code change, and the endpoint's 401.
- [x] `server/tests/test_derivations.py` -- extend the registry inventory to include `fraud_flagged`, and assert `fraud_flagged` and `siu_review` disagree on the seeded portfolio -- the guard against a later reader collapsing the two.
- [x] `web/src/api/schema.d.ts` + `web/openapi.json` -- regenerate via `npm run generate:api`; never hand-edit -- CI fails on a diff.
- [x] `web/src/api/queryKeys.ts` -- add a `dashboard: { summary: ["dashboard","summary"] as const }` family after `glossary`, with the house doc comment -- no persona or scope segment: the server answers for the cookie holder.
- [x] `web/src/api/dashboard.ts` -- `useDashboardSummary()` over `api.GET("/dashboard/summary")`, `staleTime: 30_000`, exporting the `components["schemas"][…]` response type -- the aggregate convention `/stats/*` already uses.
- [x] `web/src/features/dashboard/KpiCard.tsx` -- presentational card: value, label, caption, tone (`steel|warn|ok|error|brand|plain`), `data-testid="kpi-<slug>"` with the figure on `kpi-<slug>-value` -- `CaseCard`/`StatTile` anatomy; a separate `-value` id so an exact-match assertion cannot pass on a substring.
- [x] `web/src/features/dashboard/DashboardPage.tsx` -- header "Manufacturing WC — Portfolio Overview" + dataset chip, the 6-card and 4-card rows in the prototype's order, labels, captions and tones; skeleton while pending with `aria-busy`, inline `role="alert"` on failure, `sr-only` polite announcement -- AC 1, 2, 4.
- [x] `web/src/features/shell/DashboardShell.tsx` -- render `<DashboardPage />` inside a section that keeps `aria-label="Portfolio dashboard"` -- `App.test.tsx` asserts that name.
- [x] `web/src/test/api-mock.ts` -- add a `dashboardSummary` route keyed on `/api/dashboard/summary` and a `DASHBOARD_SUMMARY` fixture -- there is no MSW; every component test routes through `stubApi`.
- [x] `web/src/features/dashboard/DashboardPage.test.tsx` -- pending → skeletons, data → ten cards with the server's figures in the server's order, error → inline alert and no zeros, captions interpolating the server's thresholds rather than a local constant.
- [x] `web/src/features/queue/noDerivation.test.ts` -- add `"features/dashboard"` to `ROOTS` with a comment naming what it prevents -- ten server-computed figures are exactly what a component would be tempted to total.
- [x] `e2e/fixtures/seed.ts` -- add `expectedPortfolioSummaryFor(persona)` re-derived from the seed -- the spec's expectations follow the seed, not the API.
- [x] `e2e/stories/5-1-portfolio-kpi-cards.spec.ts` -- `test.describe("@story:5-1 @epic:5 …")`, one `@smoke` happy path (login as `fullPortfolioSupervisor` → header, chip and ten card values via `byTestId(…"-value")` + `toHaveText`), plus a scoped assertion that `scopedSupervisor`'s Total Claims is lower, and an analyst reaching the same dashboard.

**Acceptance Criteria:**

- Given a supervisor or analyst session, when the dashboard loads, then the header reads "Manufacturing WC — Portfolio Overview" beside the `WC_Manufacturing_Claims_2026.xlsx` chip and ten cards render in the prototype's order — Total Claims · Under Treatment · Settled & Closed · High Risk · Total Paid · Total Reserve, then Fraud Flags · OSHA Recordable · Litigation · Surgery Required.
- Given the High Risk and Fraud Flags cards, when their captions render, then the numbers in them are the values the response carried from the JDM documents, so superseding the rule document changes the caption with no code change.
- Given `GET /api/dashboard/summary`, when it is called, then it accepts no parameter of any kind, emits no audit row, sets `Cache-Control: no-store`, and answers `401` problem+json without a session.
- Given any two personas, when both load the dashboard, then neither figure could have been produced by a role branch: the only difference between their responses is the scope predicate the repository applied.
- Given the verification commands below, when they run, then server tests, ruff, mypy, vitest, eslint, tsc, the OpenAPI regeneration diff, an `alembic check` plus `downgrade 0036` → `upgrade head` round trip, and the full Playwright suite against a freshly reset stack all pass.

## Spec Change Log

## Review Triage Log

### 2026-08-18 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 23: (high 0, medium 10, low 13)
- defer: 3: (high 1, medium 1, low 1)
- reject: 0
- addressed_findings:
  - `[medium]` `[patch]` Migration 0028's new `_newest_committed` globbed `<key>.v*.jdm.json`, contradicting the "never glob, spell rows out" invariant migration 0037 restates twelve lines away in the same diff — glob now filtered to `^\d+$` version segments and the docstring reconciles the two rules explicitly: the globbed document never becomes a live rule because `USED_THRESHOLDS` guarantees every parameter actually read comes from the database.
  - `[medium]` `[patch]` `int(...)` on the globbed filename raised an uncaught `ValueError` on any non-conforming file (an editor backup), killing `alembic upgrade head` with a traceback naming neither the file nor the convention — non-conforming names are now skipped.
  - `[medium]` `[patch]` `_thresholds`' docstring promised a fresh database materializes under "the parameters it will be running under one migration later", true only for *added* parameters — a later version that *retunes* a threshold leaves the seeded schedules on the older value. Docstring corrected to state what the code does.
  - `[medium]` `[patch]` `highRisk` was absent from `noDerivation.test.ts`'s `DERIVED_FIELDS` while the comment claimed twelve fields and listed eleven — the most rule-laden count on the page was unguarded, so `summary.highRisk * 100` in a component would have passed the build guard AC 4 leans on.
  - `[medium]` `[patch]` `features/dashboard` was added to `ROOTS` without the matching assertion in "the scan reaches the files it claims to", which every prior story that added a root also added — nothing verified `DashboardPage.tsx` was actually scanned.
  - `[medium]` `[patch]` `useDashboardSummary`'s `staleTime` comment justified 30s against "a top bar refreshed on the same schedule", but nothing in the app polls and `refetchOnWindowFocus` is off — the described behaviour did not exist. Comment rewritten to describe what actually happens; no polling added (5.3's concern).
  - `[medium]` `[patch]` The Total Paid caption "Indemnity + medical" names two of the three columns its figure sums (expense is $65,761 of $1,670,497). The prototype's wording is kept — the figure is the design contract — with the mismatch recorded on the card spec in `claim_money.py`'s register and in the story's discrepancies.
  - `[medium]` `[patch]` ~500 lines of unrelated 80-column Prettier reflow across `e2e/fixtures/seed.ts`, `web/src/test/api-mock.ts`, `queryKeys.ts` and `noDerivation.test.ts` — this repo has no Prettier config or format gate and 101 other files fail `prettier --check`, so the churn buried ~90 real lines and misattributed `git blame` on shared fixtures. All four rebuilt from baseline plus only the Story 5.1 additions: 978 changed lines down to 214.
  - `[medium]` `[patch]` The error path announced the same sentence through both a polite `role="status"` region and an assertive `role="alert"`, so a screen reader heard the failure twice — `QueuePane` had already solved this with an empty announcement in the error branch and a comment saying why; the pattern is now followed.
  - `[medium]` `[patch]` `isError` and `data === undefined` were branched on independently, leaving the fourth combination unhandled: a failed refetch after `staleTime` expiry rendered the dataset chip with the *previous* portfolio's counts directly above "figures could not be loaded", with all ten cards gone. The chip is now hidden on error.
  - `[low]` `[patch]` `summary_of`'s `total_paid` bullet claimed the figure "agrees with the investigation card and the settled payout breakdown by construction" without stating what it disagrees with; it now records the $335,985 divergence from `claim_financials.paid_to_date` and points at the deferred product decision. `PortfolioClaim`'s docstring said "twelve facts" for thirteen fields.
  - `[low]` `[patch]` `plants` was keyed on the plant *name* while `employers` was keyed on `employer_id` — two employers operating a same-named site would have counted once. Now keyed on `(employer_id, plant)`; no behaviour change on today's seed.
  - `[low]` `[patch]` `PortfolioSummary`'s docstring did not record that `employer_count` counts employers *with claims in scope* rather than employers assigned, a divergence neither oracle catches because both count the same way.
  - `[low]` `[patch]` `KpiCard`'s `value: string | null` em-dash path carried a nine-line NFR-3 rationale but had no caller and no test — every field on this endpoint is a non-nullable integer. Removed, with the contrast against `SlaStrip`'s genuinely-nullable tiles written down.
  - `[low]` `[patch]` `CardSpec` paired `field: keyof PortfolioSummary` with an independent `money?: boolean`, so `{ field: "totalClaims", money: true }` type-checked and would render a hundred-claim portfolio as "$1". Now a discriminated union over the `*Cents` fields — 5.2 and 5.3 extend this table.
  - `[low]` `[patch]` `DashboardShell`'s `min-h-screen` parent with a `flex-1 overflow-y-auto` child and no `min-h-0` meant the scroll container never engaged; matched to `WorkspaceShell`'s working shape so the two shells scroll identically once 5.3's charts arrive.
  - `[low]` `[patch]` The router docstring accounted only for supervisor and analyst while a test makes handler access a 200 contract; handlers are now documented as intentionally served, with Story 5.2 flagged to decide on its own terms.
  - `[low]` `[patch]` `test_a_scoped_supervisor_sees_strictly_less_than_the_portfolio` asserted strict `<` across all twelve keys where `litigation` is `0 < 3` — one seed edit from a red test about data rather than scoping. Now `<=` per key plus strict `<` on `totalClaims` and `employerCount`.
  - `[low]` `[patch]` The superseded-document test inserted `effective_from = date.today()` (local) while the rules loader filters against a UTC-derived today, so it would fail east of UTC near midnight as if the loader were broken. Uses `utc_today()`.
  - `[low]` `[patch]` The no-versioned-files fallback in 0028 read a path without the `is_file()` check every other rules migration performs.
  - `[low]` `[patch]` A comment at 0028's `DerivationThresholds.of` now records that the block's `version` can under-describe its merged contents, warning the next author not to stamp it as provenance on a materialized row.
  - `[low]` `[patch]` `test_case_file_derivations.py` carried `# Version 3 of derivation_thresholds, restated.` above `version=5` — stale since 4, and this diff touched the line beneath it.
  - `[low]` `[patch]` Every task and subtask in the story file was unticked while its Dev Agent Record claimed a full green run, so a reviewer opening it saw an unstarted story.

## Design Notes

**1. Why a Python fold and not `COUNT(*) FILTER`.** `topbar_stats` counts in SQL because all three of its buckets are expressible there — `risk` publishes `sql_is`, and "active treatment" is a column comparison. Four of this story's ten are not: `fraud_flagged` and `total_paid` have Python computers only, and giving them SQL twins would be a second encoding of each rule, which is what `risk_band`'s own docstring warns against. Mixing the two would also mean two statements over one scoped set, which can disagree if a claim changes between them. So this aggregate follows `sla_strip`: one `select_claim_columns` read, one pure fold. A hundred rows, and the note in `risk_band.sql_is` already states the trade — correctness of the single source outranks a plan shape at this size.

**2. `fraud_flagged` is not `siu_review`, and the numbers say so.** The prototype's queue chips SIU at `fraudFlag && fraudScore >= 60` (9 claims); its dashboard card counts `fraudFlag` under the caption "Score ≥ 55 — review needed" (13 claims). Two rules, two thresholds, one column pair. Reusing `siu_review` would silently show 9 where the reviewed design shows 13; comparing a score inside the aggregate would put half a rule in `services/worklist`. Hence a registered derivation over a new parameter. The formulation is `fraud_flag AND fraud_score >= min`, matching `siu_review`'s "both conditions, not either" — and on the seeded portfolio `fraud_flag` and `fraud_score >= 55` coincide exactly, so the card reproduces the prototype's 13.

**3. Settled & Closed counts `stage`, and that is 62 rather than the prototype's 54.** `renderSV` filters `status === "Settled & Closed"`; the story's task list says `stage = settled`. They differ on eight claims whose stage is `settled` and whose status is the earlier `Settled`. `stage` is chosen because it is what every other surface in the console already groups by (the queue's four stage groups, the SLA strip's settled segment, 5.3's settlement donut), and a KPI that disagreed with the donut beneath it would be the exact failure AD-10 exists to prevent. Record the delta in the Dev Agent Record: it is a visible change from the prototype, not a defect.

**4. Litigation counts `litigation_flag`.** The card's caption is the prototype's "Attorney representation" and the AC says "attorney-represented", but `renderSV` counts `litigationFlag`. Both columns exist and agree on all 100 seeded claims, so the port is unambiguous today; the Block If above covers the day they diverge.

**5. The response carries its thresholds, and the chip carries its scope.** `highRiskSeverityMin`, `fraudScoreMin` and `rulesVersion` travel with the figures so no caption holds a rule value the server owns — and so the superseded-document test can watch the *UI* follow the document. The dataset chip's employer and plant counts are likewise counted over the scoped set rather than hardcoded: a chip that told Jennifer Park she was looking at ten employers would be a lie the prototype could afford and this console cannot. **This makes a prototype error visible and that is intended** — `renderSV` writes the literal "15 US plants" while its own array holds 29 distinct plants, so the full-portfolio chip now reads 29. Record it in the Dev Agent Record; keeping the literal would mean shipping a number the SPA cannot know and that is wrong for every persona including the one it was written for.

**6. `65` and `35` cannot appear in the new server files.** `test_no_module_outside_the_registry_hardcodes_the_band` greps every `.py` and `.jdm.json` outside a five-entry allowlist for those two integers, comments and docstrings included. `services/worklist/summary.py` and `api/routers/dashboard.py` are both outside it — so the High Risk band is obtained from `derivations.risk` and never described numerically in prose there. The new v5 JDM document and the new tests are inside the allowlist.

## Verification

**Commands:**
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker uv run pytest` -- expected: all green, including the new suite; no previously passing test regressed.
- `cd lineworker/server && uv run ruff check . && uv run ruff format --check . && uv run mypy .` -- expected: clean across all files.
- `cd lineworker/server && uv run alembic check` -- expected: no drift; then `uv run alembic downgrade 0036_seed_email_templates && uv run alembic upgrade head` completes clean.
- `cd lineworker/web && npm run generate:api` -- expected: `src/api/schema.d.ts` and `openapi.json` change once and are byte-identical on a second run.
- `cd lineworker/web && npm test && npm run lint && npm run typecheck && npm run build` -- expected: vitest green (including `noDerivation.test.ts` over the new root), eslint no new warnings, tsc and build clean.
- `cd lineworker && docker compose -f deploy/compose.e2e.yaml up -d --build --wait` then `cd e2e && npm run typecheck && npm test` -- expected: the full suite green against a freshly reset stack, with the new `@story:5-1` spec and the `@smoke` set included.

## Auto Run Result

Status: done

**Implemented.** Ten portfolio KPI cards over one scoped caseload, the first `services/worklist` portfolio aggregate behind them, and the rules-tier parameter the fraud card had no way to read. `portfolio_summary` takes one `select_claim_columns` read and folds it in a pure function that asks the registry for every band and total; `GET /dashboard/summary` is parameter-free, `no-store`, audit-free, and carries both thresholds and the rules version so the two card captions quote the number the server counted at.

**Files changed.** New — `rules/documents/derivation_thresholds.v5.jdm.json` (adds `fraudFlagScoreMin`), `data/versions/20260818_0037_fraud_flag_threshold.py` (inserts v5, effective-dated to v4's own date because the field is required), `services/worklist/summary.py` (`PortfolioClaim`, `PortfolioSummary`, the pure `summary_of`, `portfolio_summary`), `api/routers/dashboard.py`, `tests/test_portfolio_summary.py`, `web/src/api/dashboard.ts`, `web/src/features/dashboard/{KpiCard,DashboardPage,DashboardPage.test}.tsx`, `e2e/stories/5-1-portfolio-kpi-cards.spec.ts`. Modified — `services/derivations/queue_flags.py` (`fraud_flagged` beside `siu_review`) and its package exports, `rules/parameters.py` (the new required field), `data/versions/20260814_0028_materialize_schedules.py` (the overlay that keeps a fresh `upgrade head` alive when a later migration adds a required parameter), `api/{app,routers/__init__}.py`, `services/worklist/__init__.py`, `tests/seed_fixture.py` plus nine test modules for the version bump, `web/src/api/{queryKeys,schema.d}.ts`, `web/src/features/shell/DashboardShell.tsx`, `web/src/features/queue/noDerivation.test.ts`, `web/src/test/api-mock.ts`, `e2e/fixtures/seed.ts`.

**Review findings.** 23 patches applied (10 medium, 13 low), 3 deferred, 0 rejected, 0 intent gaps, 0 spec defects. The medium ones: migration 0028's new document glob contradicting the "never glob" invariant restated twelve lines away, and its unguarded `int()` parse; a docstring promising more than the merge delivers; `highRisk` missing from the client-side derivation guard while its comment claimed otherwise; a missing scan-reaches assertion; a `staleTime` comment describing polling the app does not do; the Total Paid caption naming two of three summed columns; ~500 lines of unrelated Prettier reflow across four shared fixtures, restored to 214; a failure announced twice to screen readers; and a failed refetch rendering the dataset chip with the previous portfolio's counts above the error banner.

**Deferred.** The Total Paid card excludes $335,985 of paid bills and expenses that the Bills tab shows on 38 claims with zero paid columns — the carried-over "what does the paid figure actually sum" decision, recorded with the magnitude so its owner decides against a number. `USED_THRESHOLDS` in migration 0028 is a hand-transcribed allowlist with nothing tying it to what `compute_benefit` reads, and it fails silently when it rots. And the endpoint's absent role gate, harmless for a count of your own book, is a precedent Story 5.2's peer-performance table must decide on its own terms.

**Verification.** Re-run independently after the patch pass, not only reported: 1971 server tests, ruff clean, `ruff format --check` clean over 193 files, mypy clean over 193 files, `alembic check` clean plus a `downgrade 0036` → `upgrade head` round trip, `npm run generate:api` byte-identical, 504 vitest, eslint 0 errors (11 pre-existing warnings), tsc and build clean, and 150/150 Playwright against a freshly rebuilt e2e stack with all seven `@story:5-1` specs green.

**Residual risks.** The three deferred items above. Two are documentation-and-decision rather than defects; the `USED_THRESHOLDS` one is the sharpest, because its failure mode is silently seeding financial data under a threshold the database does not hold, and the mechanical guard that would close it belongs beside the payment-projection tests rather than inside a migration.
