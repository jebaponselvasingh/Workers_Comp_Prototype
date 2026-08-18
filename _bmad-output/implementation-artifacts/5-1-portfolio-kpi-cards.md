# Story 5.1: Portfolio KPI Cards

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a WC supervisor,
I want portfolio KPIs at the top of my dashboard,
so that volume, financial exposure, and risk concentration are visible at a glance.

## Acceptance Criteria

1. **Given** a supervisor or analyst, **when** the dashboard loads, **then** the header shows "Manufacturing WC — Portfolio Overview" with the dataset chip, and row 1 renders Total Claims · Under Treatment · Settled & Closed · High Risk (severity ≥ 65) · Total Paid ($) · Total Reserve ($) over the persona's scoped caseload (FR-SUP-1, FR-SUP-A).
2. **And** row 2 renders Fraud Flags (score ≥ 55) · OSHA Recordable · Litigation (attorney-represented) · Surgery Required from the same scoped aggregates.
3. **Given** the thresholds (65, 55), **when** resolved, **then** they come from JDM parameters — the same values every other surface uses (AD-8, AD-10), verified by a test that scoped supervisor Jennifer Park's counts cover only Toyota/GM/3M.
4. **Given** any aggregate endpoint, **when** called, **then** it computes in `services/worklist` behind the repository scope context (AD-7), with loading and error states in the UI (NFR-3).

## Tasks / Subtasks

- [x] Task 1: Portfolio summary aggregate in `services/worklist` (AC: 1, 2, 3, 4)
  - [x] One aggregate function (e.g. `portfolio_summary(ctx)`) computing all 10 KPI values in a single scoped pass: total claims, under treatment (stage = `treatment`), settled & closed (stage = `settled`), high risk (severity ≥ high-risk threshold), total paid (cents), total reserve (cents), fraud flags (fraud score ≥ fraud threshold), OSHA recordable, litigation (attorney-represented), surgery required
  - [x] Every input flag/derived value (stage, severity band, `siu_review`-style flags, paid/reserve totals) obtained via the registered `services/derivations` functions — never re-derived inside the aggregate (AD-10)
  - [x] Repository access exclusively through the caller scope context; no role branch anywhere in the aggregate (scope gates visibility — AD-7)
- [x] Task 2: Thresholds from JDM (AC: 3)
  - [x] Resolve high-risk severity (seeded 65) and fraud-flag score (seeded 55) from the existing ZEN JDM parameter documents introduced by Epics 2/3 (queue risk band, fraud flag) — reuse the same document keys, do NOT author a second dashboard copy of either value (AD-8, AD-10)
  - [x] If a needed parameter does not yet exist as JDM, add it to the appropriate shared document (DB-versioned, effective-dated) so queue, detail, and dashboard all read one value
- [x] Task 3: Read-only dashboard API (AC: 1, 2, 4)
  - [x] `GET /api/dashboard/summary` (thin router → `services/worklist`), camelCase JSON via Pydantic alias, money as integer cents, no caller-supplied scope parameter of any kind (AD-7)
  - [x] Endpoint requires a valid session (401 problem+json otherwise, per Epic 1 auth deps); it is a query — no mutation, no audit event
- [x] Task 4: Dashboard page + KPI card rows (AC: 1, 2, 4)
  - [x] Build the real dashboard page in `web/features/dashboard/` (Story 1.3 routes supervisor AND analyst here — the analyst sees this same dashboard until Epic 7)
  - [x] Header: "Manufacturing WC — Portfolio Overview" + dataset chip (`WC_Manufacturing_Claims_2026.xlsx` label per the prototype); role badge/top bar already exist from Epic 1
  - [x] Row 1 (6 cards) and row 2 (4 cards) styled with the Story 1.1 design tokens (information-dense card style, ok/warn/error hues); dollar values formatted from cents in the UI only
  - [x] TanStack Query via the shared `queryKeys` module (add a `dashboard` key family); loading skeleton and problem+json → inline error state (NFR-3); no client-side computation over raw claims (AD-1)
  - [x] Cards are static this story — click-to-drill-through interaction arrives in Story 5.5 (leave the card component API ready to accept an `onClick`/href without redesign)
- [x] Task 5: Tests (AC: all)
  - [x] Unit tests: `portfolio_summary` for full-scope David Bline (all 100 claims) vs scoped Jennifer Park (Toyota/GM/3M only — counts strictly smaller, every counted claim's employer in her assignment set); threshold values asserted to come from the JDM resolution path, not literals
  - [x] Unit test: empty scope returns all-zero KPIs (feeds NFR-3 zero states)
  - [x] Playwright `e2e/stories/5-1-portfolio-kpi-cards.spec.ts` tagged `@story:5-1 @epic:5`, one `@smoke` happy path: login as supervisor → dashboard renders header + 10 KPI cards with values; plus a scoped-persona assertion (Jennifer Park's Total Claims < David Bline's)

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story delivers the dashboard **page shell + the 10 KPI cards** and the first `services/worklist` portfolio aggregate. It is the foundation the rest of Epic 5 stacks onto. It is NOT: handler benchmarking (5.2), charts/Recharts (5.3 — do not install Recharts here), the top-30 worklist (5.4), or any drill-through interaction (5.5 — cards render values only this story). The dashboard is **read-only by role capability**: scope gates visibility, role gates capability (AD-7) — no mutating endpoint exists on any dashboard surface in this epic. The analyst logs into this exact dashboard until Epic 7 differentiates their workspace; do not build any analyst-specific variation.

### Architecture compliance (binding ADs for this story)

- **AD-1:** every KPI number is computed by `services/worklist`; the SPA renders values it received — never sums claims client-side.
- **AD-7:** aggregates run behind the repository scope context `(user_id, role, employer_ids | ALL)`; `scope_all` is a tautology predicate, never a skipped filter; no endpoint accepts caller-supplied scope; no role-conditional filter bypass.
- **AD-8:** the 65/55 thresholds are JDM parameters (DB-versioned, effective-dated); Python owns only the aggregation arithmetic.
- **AD-10:** thresholds and derived inputs (stage, flags, totals) resolve through the single registered computer per value — the dashboard, queue, and claim detail can never disagree on "high risk" or "fraud flagged".
- **AD-9:** server state via TanStack Query under the shared `queryKeys` module; no optimistic anything (read-only surface).
- **Conventions:** camelCase JSON, integer cents, RFC 9457 problem+json → inline error/toast (NFR-3).

### Data notes

- **Tables created: none.** Epic 5 creates no tables — all stories aggregate over existing data (claim, employer, employee, app_user, user_employer_assignment, bill/expense/payment_schedule_week for paid figures).
- **Writes: none.** No AD-12 ownership question arises; `services/worklist` is the computing (not writing) owner of these aggregates. No audit events (queries are not mutations).
- JDM parameter documents are rules content (DB-versioned via the Epic 2/3 rules seeding mechanism), not schema.

### UX notes

- UX-DR7 governs the dashboard: KPI card rows exactly as the prototype's supervisor view (`renderSV()` in `docs/Workers_Comp_Prototype.html`) lays them out — two rows, terse labels, dense cards. Clickability ships in 5.5.
- UX-DR2's top bar (stats + SLA strip) already exists from Epic 1 and must visually agree with the dashboard (same tokens, same values where they overlap).
- **Design-token ruling:** the prototype palette is LIGHT and canonical; epics.md UX-DR12's "dark console aesthetic" wording is a documented discrepancy (see Story 1.1 Dev Notes). Use the Story 1.1 Tailwind tokens; do not invent a dark theme.
- NFR-3 / UX-DR11: loading skeletons, inline error state, and zero-value rendering (a scoped persona with an empty book sees zeros, not blanks or crashes).

### Testing requirements

- Unit: `portfolio_summary` scoping (Jennifer Park = Toyota/GM/3M only; David Bline = all), threshold-from-JDM assertion, empty-scope zeros.
- Web: Vitest smoke on the KPI card component (loading → data → error states).
- E2E (AD-15): `e2e/stories/5-1-portfolio-kpi-cards.spec.ts` tagged `@story:5-1 @epic:5`, exactly one `@smoke` happy path, run against the freshly reset e2e compose stack. **The story cannot move to `review`/`done` until this spec passes.** Selector policy: accessible role first, `data-testid` second, never CSS classes.

### Project Structure Notes

- Server: aggregate in `server/services/worklist/`, router in `server/api/` (thin). Derivation lookups via `server/services/derivations/` registry.
- Web: `web/src/features/dashboard/` (page, KPI card components); query hooks + `dashboard` query keys in `web/src/api/`.
- The generated OpenAPI client picks up the new endpoint — regenerate, don't hand-write the TS types.
- Epic order: Epics 1–4 precede this story in sprint order; the auth deps (1.3), scope context builder (1.4), derivations registry (1.4/2.x), and JDM mechanism (2.1/3.x) all exist. If a prerequisite registered derivation is missing, add it to `services/derivations` — never inline it.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 5.1]
- Epic 5 preamble (read-only by role capability; analyst same dashboard): [Source: _bmad-output/planning-artifacts/epics.md#Epic 5]
- AD-7 scope enforcement, AD-8 two-tier rules, AD-10 one computer per derived value: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- Capability map row "Supervisor KPIs, charts, drill-through" → `services/worklist` + `web/features/dashboard`: [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map]
- Scoped-persona test fixture (Jennifer Park Toyota/GM/3M, David Bline all 100): [Source: _bmad-output/planning-artifacts/epics.md#Story 1.4]
- FR-SUP-1/A: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]
- UX-DR7, UX-DR11: [Source: _bmad-output/planning-artifacts/epics.md#UX Design Requirements]
- Design-token discrepancy ruling: [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]
- AD-15 E2E gate: [Source: ARCHITECTURE-SPINE.md#AD-15]

## Dev Agent Record

### Agent Model Used

claude-opus-5[1m] (Claude Opus 5, 1M context), via the `bmad-dev-auto` workflow.

### Debug Log References

- **A required rule parameter added at the end of the chain kills a *fresh* `alembic upgrade head`, and migration 0028 is where it lands.** `DerivationThresholds` is a frozen dataclass with no optional fields, and 0028 is the one migration that instantiates the *live* typed block — it needs `compute_benefit` to materialize the payment schedules. On a fresh database it runs eleven revisions before 0037 inserts v5, so it read v4, `DerivationThresholds.of` refused a missing `fraudFlagScoreMin`, and every DB-backed test errored at fixture setup (653 errors, all `seeded_db_url`). Existing databases were unaffected, which is exactly why this would have shipped and only broken CI. Fixed in 0028 with `_thresholds`: the effective document's values are overlaid on the newest *committed* document's, so a fresh database materializes under the parameters it will be running under one migration later, and `USED_THRESHOLDS` refuses to complete any parameter the benefit calculation actually reads. The hazard is now stated in that migration's own docstring, because the next required parameter will meet it too.
- **Four test modules pin `thresholds_version` and one pins the block's `version`, so superseding the document is never a one-file change.** `test_claim_detail`, `test_claims_queue` (twice) and `test_rule_parameters` assert `== 4`; five more construct a `DerivationThresholds` literal that no longer type-checks without the new field. All of them are deliberately pinned rather than read from the loader (`seed_fixture`'s oracle discipline), so the churn is the mechanism working — but it is worth knowing in advance that a v6 costs ten files, not two.
- **`test_no_module_outside_the_registry_hardcodes_the_band` greps docstrings, so the aggregate cannot *describe* the High Risk band.** `services/worklist/summary.py` documents that card as "`risk.of(...)` is `high`" and adds "the band's boundary is not known here and must not become known here" — the prose had to be written around a number it would have been natural to quote. Same for `api/routers/dashboard.py`, whose response-model docstring writes the captions as "Severity ≥ N/100".
- **`PortfolioClaim(*row)` type-checks and is one reordered projection away from counting OSHA-recordable injuries as litigation.** Thirteen columns, seven of them booleans or ints. Built by name off the `sa.Row` instead (`queue.py`'s pattern), with the reason in a comment — a swap would have produced two plausible wrong numbers and no failure anywhere except the seed oracle.
- **`CallerContext.employer_ids` is `ALL_EMPLOYERS | frozenset[int]`, so a test that filters SQL on it does not type-check.** `test_every_claim_behind_a_scoped_personas_cards_is_inside_her_book` reads the bare frozenset from `employer_ids_for` and builds the context from that, which is also the more honest statement: a scoped persona is precisely the case where the union has one inhabitant.
- **A component test that awaits the *heading* before reading the chip asserts nothing.** "Manufacturing WC — Portfolio Overview" is static text and is on screen before the request settles, so `findByRole("heading")` returned instantly and `getByTestId("dataset-chip")` failed. Awaited on the chip itself. Related: the failure-state test used a 500, which `createQueryClient` retries twice — a 404 (`TopBar.test.tsx`'s precedent) asserts the rendered branch instead of racing the backoff.
- **`web/openapi.json` is gitignored** (`lineworker/.gitignore:18`), so "regenerate and commit both" is really "regenerate; only `src/api/schema.d.ts` is tracked". A second `npm run generate:api` produced no further diff.

### Completion Notes List

- **AC 1 — the header, the chip and row 1, in the prototype's order, from one scoped read.** `renderSV` folds the global claim array with ten browser-side passes; `services/worklist/summary.py` does one `select_claim_columns` read behind `employer_scope` and one pure fold. The card order is a declared `CardSpec` list rather than JSX layout, so a reviewer can check it line by line against `renderSV` (line 1060) — and `DashboardPage.test.tsx` asserts the rendered `data-testid` sequence, not ten independent lookups, because a page that put every card in the wrong row would satisfy those.
- **AC 1 — the dataset chip's three counts are computed, and that makes a prototype error visible on purpose.** `renderSV` writes "10 employers · 15 US plants" as literals; its own array holds **29** distinct plants, and both numbers are scope-blind — a chip telling Jennifer Park she is looking at ten employers is a lie the prototype could afford and this console cannot. All three come off the response, so the full-portfolio chip now reads 29 plants. Recorded as a discrepancy below.
- **AC 2 — row 2's Fraud Flags card counts a *new registered derivation*, not `siu_review`.** The queue chips SIU at `fraudFlag && fraudScore >= 60` (9 seeded claims); the dashboard card is captioned "Score ≥ 55 — review needed" and counts 13. Two rules, one column pair, two thresholds. `FraudFlaggedDerivation` sits beside `SiuReviewDerivation` in `queue_flags.py` precisely so the pair is read together, and `test_fraud_flagged_and_siu_review_disagree_on_the_seeded_portfolio` asserts the referral set is a *strict subset* of the review set — the guard against a later reader collapsing them.
- **AC 3 — both thresholds resolve through `thresholds_for(db)`, and the response carries them so the captions follow the document.** `highRiskSeverityMin` and `fraudScoreMin` are read off the *derivations that did the counting*, not off the parameter block in parallel, so the published number is provably the one the figures were produced at. `test_a_superseded_rule_document_moves_the_count_and_the_caption` inserts a v6 effective today and watches both move with nothing deployed; `DashboardPage.test.tsx` renders the retuned payload and watches the *caption* follow, which is the half a server test cannot see.
- **AC 3 — Jennifer Park's proof is taken twice, at two levels.** The endpoint test compares all twelve of her figures against `seed_fixture.expected_portfolio_summary` and asserts every one is strictly below David Bline's; a separate test builds her caller context the way `api/deps.py` does, calls the aggregate directly, and reads the employers behind the folded rows back out of the database — so a widened predicate surfaces as an employer she is not assigned to rather than as a number that happens to match.
- **AC 4 — the endpoint has nowhere to put a scope, and that is asserted against the published contract.** `GET /dashboard/summary` takes only `ctx`, `db`, `response`; the OpenAPI operation declares zero parameters and no request body, five smuggled query parameters leave the answer byte-identical, `Cache-Control: no-store` is set, and no audit row is written (counted before and after). No role branch exists anywhere on the path — supervisor, analyst and handler take the identical route, which is what makes "the only difference is the scope predicate" structural rather than a claim.
- **AC 4 — NFR-3's three states, and never a zero for an unknown figure.** Pending draws ten `animate-pulse` skeletons with `aria-busy` on the container and *no* chip (its counts are as absent as the cards'); failure renders one inline `role="alert"` in place of both rows, with no cards and no skeletons, because ten zeroed KPIs are a much quieter lie than an error message. A *known* zero is still a zero — Park's book genuinely holds no litigated claim — and `KpiCard` takes a plain `string`, with no em-dash fallback: unlike `SlaStrip`, where the server really does send `null` for a segment with no data, every field on this endpoint is a non-nullable integer, so an unknown figure is a skeleton or the banner rather than a card with a dash in it.
- **The SPA computes nothing, and `features/dashboard` now joins the roots that enforce it.** `noDerivation.test.ts` gained the directory plus twelve field names; `employerCount` and `plantCount` are on that list because they read like chip decoration rather than like figures, and `highRiskSeverityMin`/`fraudScoreMin` because a comparison against one would be the browser re-deciding a band the server already banded. The page formats cents through `lib/money.ts` and picks a colour token; nothing else.
- **The dashboard does not repeat the top bar.** Caseload, Active Tx, High Risk and the SLA strip already render for every role in the shared bar (UX-DR2), so the page adds no second copy — asserted in the e2e (`top-bar` has count 1 while the page's own High Risk card renders its own figure and caption).
- **Nothing was built ahead.** No handler table (5.2), no Recharts and no chart (5.3 — the dependency was not added), no top-30 worklist (5.4), no click, href or drill-through (5.5). `KpiCard` takes content only, so 5.5 adds an `onClick` to that file and to no caller. No analyst variant, no mutating endpoint, no toast, no new table, no dark theme.
- **Tests:** **1971 server** (+27 — nineteen in the new `test_portfolio_summary.py` covering every I/O-matrix row: four personas against the seed oracle, Bline's literal 100, the plant-count and stage-vs-status departures, the strict-narrowing pair, the scoped-set proof at the service, the empty-`frozenset` all-zero case, thresholds and `rulesVersion` asserted against `thresholds_for(db)`, the review-vs-referral count, the v6 supersession, the parameter-free contract, five smuggled parameters, the camelCase key set, `no-store`, the 401 and the absent audit row; plus four in `test_derivations.py` for the new derivation, its independent parameter and its disagreement with `siu_review`, and four in `test_rule_parameters.py` for the new cut-off's range and the two fraud parameters' independence). **504 vitest** (+6 — the ten cards in the server's order, cents formatted and a known zero kept, the chip's scoped counts, the two captions under a seeded *and* a superseded response, the pending skeletons with no figures and no chip, and the inline alert with no zeros). **150 Playwright** (+7, `@story:5-1 @epic:5`, one `@smoke` walking the header, chip and ten card values; the two rule captions; the scoped supervisor with her own chip; the analyst on the same dashboard; a persona switch replacing the portfolio; a smuggled-parameter request from the page's own session; and the top bar not duplicated).
- **Verified live.** e2e stack (`compose.e2e.yaml`, :8081) rebuilt with the new API and SPA: full suite **150/150** and the 22-test `@smoke` set green against a freshly reset stack. `alembic check` reports no drift, and a `downgrade 0036_seed_email_templates` → `upgrade head` round trip completes clean with `check` still clean afterwards. `npm run generate:api` leaves `src/api/schema.d.ts` byte-identical on a second run. ruff + `ruff format --check` + mypy clean across 193 files; eslint 0 errors (11 pre-existing warnings, none new); tsc and `vite build` clean for `web/`, tsc clean for `e2e/`. No new runtime or dev dependency.

### Recorded discrepancies

- **Settled & Closed counts `stage`, and that is 62 where the prototype's 54.** `renderSV` filters `status === "Settled & Closed"`; this counts `stage = settled`. They differ on eight claims whose stage is `settled` and whose status is the earlier `settled`. `stage` is chosen because it is what every other surface already groups by — the queue's four stage groups, the SLA strip's settled segment, 5.3's settlement donut — and a KPI disagreeing with the donut beneath it is the failure AD-10 exists to prevent. Asserted explicitly (`test_settled_and_closed_counts_the_stage_not_the_status`) so the delta cannot be rediscovered as a defect.
- **The dataset chip reads 29 plants where the prototype hardcodes 15.** The literal is wrong for the portfolio it was written for and scope-blind for every other persona; counting distinct `claim.plant` over the scoped set is the fix, and the full-portfolio chip therefore reads "10 employers · 29 plants". Keeping the literal would have meant shipping a number the SPA cannot know and that is wrong for everyone.
- **`fraud_flagged` (55, 13 claims) is a distinct rule from `siu_review` (60, 9 claims).** Both read `fraud_flag` and `fraud_score`; the SIU rule is the *referral* threshold that earns a queue card its priority weight and its chip, the new one is the wider *review* population the dashboard card counts. They are two registered derivations over two adjacent JDM parameters, not one rule with two readers, and the seeded numbers are what force the distinction.
- **`litigation_flag` and `attorney_rep` agree on all 100 seeded claims**, so the Litigation card's spec-level Block If did not trigger; the card counts `litigation_flag`, which is what `renderSV` counts, under the prototype's "Attorney representation" caption.
- **The Total Paid card's caption names two of the three columns its figure sums.** "Indemnity + medical" is the prototype's wording and is ported unchanged, while `TotalPaidDerivation` adds `paid_indemnity + paid_medical + paid_expense` — and the expense column is **$65,761** of the full portfolio's **$1,670,497**, so the caption undercounts its own number by about 4%. The same shape as the "Total incurred" note already recorded against `claim_money.py`: the figure is the design contract and is ported exactly, the label is the UI's, and rewording it is a product decision rather than a port. Stated on the card spec in `DashboardPage.tsx` so the next reader corrects neither half by accident.
- **Total Paid sums the static `paid_*` columns and therefore excludes $335,985 of `status = paid` bills and expenses the Bills tab shows.** 38 of the 100 seeded claims (all 28 in treatment, 6 intake, 4 investigation) carry zero paid columns while holding real paid line items — about 20% of the card's headline figure — so a supervisor can read "Total Paid $1,670,497", drill into any treatment claim, and find a five-figure Paid to Date on a claim this card counted as nothing. Not an AD-10 violation (the two quantities have one computer each: `total_paid` over the columns, `claim_financials.paid_to_date` over the ledger) and not a deviation from the design contract (the prototype has no bills table and computes exactly the ported figure). **Which quantity this card should sum is the recorded product decision carried into Epic 5** — see `deferred-work.md` — and it wants an owner rather than a dev-time correction, so the prototype's figure ships until it has one. Resolving it is one call site in `services/worklist/summary.py` plus both test oracles, and it moves Epic 7's financial decomposition with it. Stated in `summary_of`'s Total Paid bullet.

### File List

**New — server**

- `lineworker/server/rules/documents/derivation_thresholds.v5.jdm.json`
- `lineworker/server/data/versions/20260818_0037_fraud_flag_threshold.py`
- `lineworker/server/services/worklist/summary.py`
- `lineworker/server/api/routers/dashboard.py`
- `lineworker/server/tests/test_portfolio_summary.py`

**Modified — server**

- `lineworker/server/rules/parameters.py` (`fraud_flag_score_min` on `DerivationThresholds`, its range check, its mapping in `.of()`)
- `lineworker/server/services/derivations/queue_flags.py` (`FraudFlaggedDerivation`, registered as `fraud_flagged`)
- `lineworker/server/services/derivations/__init__.py` (the new exports and the narration)
- `lineworker/server/services/worklist/__init__.py` (the `summary` module and its exports)
- `lineworker/server/api/routers/__init__.py`, `lineworker/server/api/app.py` (`dashboard_router`)
- `lineworker/server/data/versions/20260814_0028_materialize_schedules.py` (`_thresholds`/`_newest_committed`/`USED_THRESHOLDS` — the later-required-parameter hazard)
- `lineworker/server/tests/seed_fixture.py` (`expected_portfolio_summary`, `FRAUD_FLAG_SCORE_MIN`)
- `lineworker/server/tests/test_derivations.py` (the registry inventory, the new derivation's boundaries and its disagreement with `siu_review`)
- `lineworker/server/tests/test_rule_parameters.py` (the new cut-off's range and the two fraud parameters' independence)
- `lineworker/server/tests/test_rules_engine.py` (v5 effective, v4 seeded-and-superseded, the new expected value)
- `lineworker/server/tests/{test_claim_detail,test_claims_queue,test_benefit_calculation,test_case_file_derivations,test_claim_financials,test_payment_projection}.py` (the pinned document version and the block literals)

**New — web / e2e**

- `lineworker/web/src/api/dashboard.ts`
- `lineworker/web/src/features/dashboard/KpiCard.tsx`
- `lineworker/web/src/features/dashboard/DashboardPage.tsx`
- `lineworker/web/src/features/dashboard/DashboardPage.test.tsx`
- `lineworker/e2e/stories/5-1-portfolio-kpi-cards.spec.ts`

**Modified — web / e2e**

- `lineworker/web/src/api/queryKeys.ts` (the `dashboard` family)
- `lineworker/web/src/api/schema.d.ts` (regenerated; `openapi.json` is gitignored)
- `lineworker/web/src/features/shell/DashboardShell.tsx` (the placeholder replaced, `aria-label` kept)
- `lineworker/web/src/test/api-mock.ts` (the `dashboardSummary` route, `DASHBOARD_SUMMARY`, `DASHBOARD_SUMMARY_RETUNED`)
- `lineworker/web/src/features/queue/noDerivation.test.ts` (`features/dashboard` and this story's twelve fields)
- `lineworker/e2e/fixtures/seed.ts` (`expectedPortfolioSummaryFor`, `EXPECTED_KPI_CAPTIONS`, `plant` on `SeedClaim`)
