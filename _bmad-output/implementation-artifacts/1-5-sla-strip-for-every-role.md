---
baseline_commit: c7ffe4f8ff8b3adb0b0a80b4c604d738bd250cc6
---

# Story 1.5: SLA Strip for Every Role

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user of any role,
I want the top-bar SLA strip,
so that performance against SLA targets is honestly reported for my caseload.

## Acceptance Criteria

1. **Given** a persona's caseload, **when** the top bar renders, **then** the strip shows avg Pick / Approve / Settle and RTW rate computed exactly once in `services/worklist` (AD-2), for all three roles — closing the prototype's handler-only recalc gap (FR-SLA-1).
2. **Given** the SLA targets (< 1d / < 5d / < 30d / > 80%), **when** each tile renders, **then** it colors pass/warn against its target and carries an explanatory tooltip.
3. **And** targets come from configuration/rules, never hardcoded.
4. **Given** the aggregation functions, **when** tests run, **then** unit/property tests cover empty caseloads, all-passing, and all-warning cases.

## Tasks / Subtasks

- [x] Task 1: SLA aggregation in `services/worklist` — the single computer (AC: 1, 3)
  - [x] One aggregation function (module: `services/worklist`) over the caller's scoped caseload (AD-7 repository context from 1.4), returning the four metrics with their targets and pass/warn status per tile — the server decides pass/warn, the UI only styles it (AD-1)
  - [x] Metric definitions (from the prototype's `recalcSLA`, line ~1802, and BRD §7.1): **Pick** = avg `sla_pick_days` over the caseload; **Approve** = avg `sla_approve_days` over claims that have one; **Settle** = avg `settlement_days` over settled claims that have one; **RTW rate** = settled claims whose `return_status` is the fully-recovered value ÷ all settled claims, as a percentage
  - [x] **No fabricated fallbacks:** the prototype substitutes hardcoded 7.4d / 42d / 87% when a segment is empty — that is banned (honest reporting is this story's purpose). A metric with no qualifying claims returns `null` + a `no_data` status; the empty-caseload result is all-null, never invented numbers
  - [x] Targets (`pick < 1d`, `approve < 5d`, `settle < 30d`, `rtw > 80%`) live in the config module (`server/config.py`, 12-factor) with an explicit `TODO(JDM)` marker: AD-8 assigns SLA targets to the JDM tier — migrate them into a ZEN document when the ZEN wrapper lands (Epic 2/3); the aggregation reads targets by key either way, so the migration touches config only
  - [x] Rounding at the service edge per prototype display precision: pick/approve one decimal, settle whole days, RTW whole percent
- [x] Task 2: API exposure (AC: 1)
  - [x] Extend the top-bar stats response or add `GET /api/stats/sla` (auth-required, session-scoped — no caller-supplied scope, per 1.4's rule): `{pick: {value, target, status}, approve: {…}, settle: {…}, rtwRate: {…}}`, `status ∈ pass|warn|no_data`; camelCase, days/percent as numbers formatted only in the UI
  - [x] Computed for **every role** — the endpoint has no role branch; supervisor/analyst/handler all get their scoped caseload's strip (the point of FR-SLA-1)
- [x] Task 3: SLA strip UI — fills the 1.4 slot (AC: 1, 2)
  - [x] Strip in the shared top bar: "SLA" label + four tiles (Pick · Approve · Settle · RTW Rate), each showing value (`0.8d` / `42d` / `87%`), metric label, and target annotation (`✓ <1d` pass / `⚠ >5d` warn per prototype)
  - [x] Pass/warn coloring from the server's `status` using the 1.1 ok/warn/error tokens; prototype detail: a failing RTW tile styles with the **error** class (not warn) — follow the contract; `no_data` renders an em-dash tile in muted style, never a fake value (NFR-3 empty state)
  - [x] Tooltips verbatim from the prototype titles: Pick "Avg days from FROI to Handler Assignment — target <1 day" · Approve "Avg days from FROI to Claim Approval — target <5 days" · Settle "Avg days from FROI to Settlement — target <30 days" · RTW "Percentage of settled claims with successful RTW" (shadcn/ui tooltip — accessible, not bare `title`)
  - [x] Data via TanStack Query (`queryKeys.stats.sla`), loading skeleton + error state; renders on both shells (dashboard + workspace) automatically because it lives in the shared top bar
- [x] Task 4: Unit & property tests (AC: 4)
  - [x] Unit (pytest): empty caseload → all-null/`no_data`; all-passing caseload → four `pass`; all-warning caseload → four `warn`; segment-empty cases (no settled claims → Settle and RTW `no_data` while Pick/Approve compute); rounding rules
  - [x] Property tests (Hypothesis, per NFR-7): for generated caseloads, pass/warn status always agrees with value-vs-target comparison; averages lie within min/max of inputs; RTW rate ∈ [0, 100]; metrics never invent values for empty segments
  - [x] pytest: same persona-scoping proof as 1.4 — Jennifer Park's strip computes over Toyota/GM/3M claims only (reuse the seed-derived expectations)
- [x] Task 5: E2E story spec (AC: 1, 2)
  - [x] `e2e/stories/1-5-sla-strip-for-every-role.spec.ts` tagged `@story:1-5 @epic:1`, using the login fixture
  - [x] Strip visible with four tiles for **all three roles** (supervisor, analyst, handler logins — the "every role" clause under test); tiles carry pass/warn styling consistent with seeded data; tooltip text reachable
  - [x] One `@smoke` happy path: David Bline logs in → SLA strip renders four computed tiles

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story fills the SLA-strip slot 1.4 left in the top bar, backed by the **single** SLA aggregation in `services/worklist`. It is a read-only aggregate story: no new tables, no writes, no audit events. NOT here: the dashboard's SLA tiles chart (5.3 — it explicitly *reuses this aggregation*, so get the function signature/service placement right), diary-note-triggered recalc (obsolete — 4.2 notes the prototype's note-triggered SLA refresh dies because the strip is always server-computed), per-handler SLA benchmarking (5.2), and any JDM/ZEN installation (Epic 2/3 — see the targets ruling below).

The gap being closed (BRD §7.4-7): the prototype recomputes the strip only on handler flows (`recalcSLA` reads `getMine()` and is invoked from handler-side code paths), so supervisors/analysts see stale header defaults. Here the strip is one scoped endpoint consumed by the shared top bar — every role, every load.

### Architecture compliance (binding ADs for this story)

- **AD-2 (the binding rule):** SLA aggregation exists **exactly once**, in `services/worklist`. Story 5.3's dashboard tiles and any future copilot narration reach this same function (agents via a LangGraph tool later) — never a re-implementation.
- **AD-1:** value, target, and pass/warn status all come from the server; the SPA styles and formats only.
- **AD-7:** aggregation runs over the scoped repository with the caller context; `scope_all` personas aggregate the full portfolio through the same tautology predicate.
- **AD-8 + Config convention:** targets are parameters, not code. The conventions row sanctions "SLA targets from config/DB — never hardcoded"; AD-8 assigns them to the JDM tier once ZEN exists. Ruling for this story: config module now, keyed reads, `TODO(JDM)` marker — hardcoding numbers at call sites is the violation either way.
- **AD-10:** SLA aggregates are in AD-10's binds list — this aggregation function is their registered computer; per-claim inputs (`sla_pick_days` etc.) are seeded source columns per Story 1.2's documented SLA-data ruling (they do not reconcile with the synthetic dataset's date fields, so they seed as data; date-difference derivations arrive with real claim flow).
- **AD-9:** TanStack Query, shared `queryKeys`, no client math.

### Data notes

- Tables read: `claim` (via the scoped repository), `app_user`/`user_employer_assignment` (context). No writes; `services/worklist` remains read-only until Epic 3.
- RTW definition detail: the prototype counts settled claims with `returnStatus.includes('Fully')` ("Fully Recovered"); with 1.2's snake_case enums this is the fully-recovered enum value — match the seed's mapped value, don't substring-match.
- The prototype's `recalcSLA` pick metric defaults missing `slaPickDays` to 1 (`c.slaPickDays||1`) — another silent-fabrication pattern; production averages over present values only and reports `no_data` when none qualify.

### UX notes (UX-DR2; prototype lines ~458–480 are the contract)

- Strip anatomy per prototype: `SLA` label, then four compact tiles each with big value (`sk-n`), small metric label (`sk-l`), and target status line (`sk-s`: `✓ <1d` / `⚠ >5d`).
- Coloring: pass → ok token, warn → warn token; failing RTW → error token (prototype line ~1820 uses `er` for RTW misses). Light palette per the 1.1 ruling.
- Tooltips are part of the AC — every tile gets one (text above); ensure keyboard/focus accessibility (shadcn/ui Tooltip).
- States: loading skeleton, error state, and `no_data` em-dash tiles (NFR-3/UX-DR11) — an empty book of business is a legitimate render.

### Testing requirements (this story's definition of done)

- pytest unit: empty / all-pass / all-warn / segment-empty / rounding (AC 4 names the first three explicitly).
- Hypothesis property tests on the aggregation invariants (NFR-7 requires property-based tests on derivations/financial formulas — this is the epic's first).
- pytest: scoped-persona strip (Jennifer Park) proof.
- Vitest: tile rendering per status incl. `no_data`, tooltip presence.
- E2E: `1-5-sla-strip-for-every-role.spec.ts` green (`@story:1-5 @epic:1`, one `@smoke`, all-three-roles coverage); story cannot reach `review`/`done` until it passes (AD-15).

### Project Structure Notes

- Server: aggregation in `server/services/worklist/` (beside 1.4's stat aggregates — same service, per AD-2's ownership sentence); endpoint in `server/api/routers/stats.py`; targets in `server/config.py`.
- Web: strip component beside the 1.4 top-bar component; `queryKeys.stats` extended.
- Signature note for the future: 5.3 will call this exact function for the dashboard SLA tiles — keep it a pure scoped-caseload → metrics function with no HTTP concerns, so the dashboard route composes it without duplication.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 1.5]
- AD-2 (SLA aggregation exactly once in services/worklist): [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-2]
- AD-8 (SLA targets in the JDM parameter tier) + Config convention (targets from config/DB, never hardcoded): [Source: ARCHITECTURE-SPINE.md#AD-8 / #Consistency Conventions]
- Testing convention (Hypothesis property tests; NFR-7): [Source: ARCHITECTURE-SPINE.md#Consistency Conventions (Testing & CI); epics.md#NonFunctional Requirements NFR-7]
- SLA definitions & targets: [Source: implementation-readiness-report-2026-08-09.md#Additional Requirements (BRD §7.1); epics.md FR-SLA-1]
- Handler-only recalc gap (§7.4-7): [Source: implementation-readiness-report-2026-08-09.md#Additional Requirements (§7.4-7); epics.md FR-SLA-1]
- Prototype strip markup + tooltips + recalc semantics: [Source: docs/Workers_Comp_Prototype.html lines ~458–480, ~1802–1821]
- Story 1.2 SLA-fields-as-data ruling: [Source: _bmad-output/implementation-artifacts/1-2-persisted-claim-portfolio-schema-seed.md#Claim field disposition]
- Dashboard reuse contract: [Source: epics.md#Story 5.3 ("reuse Epic 1's single SLA aggregation")]

## Dev Agent Record

### Agent Model Used

claude-opus-5[1m] (Claude Opus 5, 1M context)

### Debug Log References

- **Rounding a fraction before scaling it collapses every rate to 0% or 100%.** The RTW metric first read `_rounded(recovered, settled, decimals) * 100` — with `decimals=0` that quantizes 7/11 to `1` and reports 100%. Caught by the unit test for 2-of-3 (expected 67), which is why that case is written out rather than left to the property tests.
- **Float division moves tiles across their targets.** `sum(values)/len(values)` renders a true 29.5 as 29.499999999999996, which rounds *down* through a `<30d` target and shows `29d ✓` for a book that averages exactly 30. The averages are computed in `Decimal` and quantized `ROUND_HALF_UP`; Python's default bankers' rounding would also have made 10.5 → 10 and 11.5 → 12 on the same tile.
- **`sa.select(*columns)` does not accept `Sequence[ColumnElement[Any]]`.** SQLAlchemy's `select()` overloads take a much wider union than `ColumnElement`, and mypy strict rejects the splat. Typing the repository parameter as `Sequence[InstrumentedAttribute[Any]]` both type-checks and says the true thing: callers pass mapped attributes of `Claim`, not arbitrary expressions.
- **jsdom has no `ResizeObserver`, and Radix's floating layer needs one.** Every tooltip test threw on mount — a jsdom gap rather than an app one, so a no-op observer is registered in `src/test/setup.ts` beside the existing `Request` patch. The tests it unblocks assert what a tooltip *says* and what it is attached to, never where it was positioned; positioning is the e2e suite's business.
- **`getByRole("tooltip")` is ambiguous the moment a second one opens.** In Playwright, hovering one tile and then focusing another left both mounted (the first does not always close first), and `toHaveCount(0)` after `mouse.move(0, 0)` did not settle either. The spec now resolves each description through the trigger's `aria-describedby`, which is unambiguous *and* asserts the accessibility linkage the AC actually asks for.
- Local DB-backed runs used a disposable `pgvector/pgvector:pg18` on :55432 per `tests/conftest.py`; the container was removed afterwards.

### Completion Notes List

- **AC 1 — one aggregation, in the place AD-2 names.** `services/worklist/sla.py` holds the whole rule. Its core (`strip_of`) is a pure caseload → metrics function with no session, HTTP or clock, exactly as the story's Project Structure Notes ask for on Story 5.3's behalf: the dashboard composes it without going through the endpoint. `sla_strip` is the thin async wrapper that reads the caller's book through the AD-7 repository and calls it.
- **AC 1 — "exactly once" is enforced, not asserted.** A grep-style test fails if any module outside `services/worklist/sla.py` reads `sla_pick_days`, `sla_approve_days` or `settlement_days`, modelled on Story 1.4's band test. That is what actually stops 5.2/5.3 from averaging these columns a second time next to the first, which no amount of documentation does.
- **AC 1 — every role, structurally.** `GET /api/stats/sla` takes no parameters at all (asserted against the published OpenAPI document, plus five smuggling attempts that must return byte-identical answers) and contains no role branch. The endpoint test runs the full seeded cast — two scoped supervisors, the portfolio supervisor, two handlers and the analyst — against expectations `tests/seed_fixture.py` computes from the seed file independently.
- **AC 1 — the honesty rule, which is the point of the story.** There is no code path that produces a number the caseload did not contain: an empty segment returns `null` + `no_data` and the UI draws a muted em dash. The prototype's three fabrications are each covered by a named test — the 7.4d/42d/87% substitutions, the `c.slaPickDays || 1` default, and the handler-only recalc.
- **A definition the story left implicit, decided and documented.** Settle and RTW rate have *different denominators*: Settle averages settled claims that have a duration, RTW rate is over **all** settled claims, because a settlement with no recorded duration still has a return-to-work outcome. Sharing one denominator would have quietly dropped those claims from the rate. Written down in `strip_of`'s docstring and pinned by its own test.
- **AC 2 — the verdict is the server's, and the tile only styles it.** `SlaStrip.tsx` contains no threshold and no comparison operator; it receives `value`, `target`, `direction` and `status`. `direction` is a small, deliberate addition to the response shape the story sketched: the annotation writes the comparison out (`✓ <1d` / `⚠ >5d`), and deriving that operator client-side would have put half of each rule in the browser where nothing checks it. The prototype's one asymmetry is kept — a missed RTW rate draws in the **error** token, every other miss in warn.
- **AC 2 — tooltips are real tooltips.** shadcn/ui `Tooltip` with the prototype's four titles verbatim, on focusable triggers, verified by keyboard in both vitest and Playwright and asserted through `aria-describedby` rather than by proximity.
- **AC 3 — targets are parameters, checkably.** Four `Settings` fields with the `TODO(JDM)` marker AD-8 asks for; `targets_for()` is the only reader and the whole of the eventual ZEN migration. Two tests keep it honest: a structural one asserting every `sla_*target*` name in the aggregation is a real `Settings` field, and a behavioural one starting a second app with looser targets and proving the verdicts flip while the measured values do not — which is precisely what separates a target from a formula.
- **AC 4 — unit and property tests.** 28 in `test_sla_aggregation.py`, all DB-free so they run in the lint job: the three named cases (empty / all-pass / all-warn), segment-empty and null-column cases, the rounding table, boundary behaviour at the target, and four Hypothesis properties over generated caseloads (status agrees with its own number; an average lies between the smallest and largest input; the RTW rate is a percentage of the settled claims; no metric ever invents a value for an empty segment).
- **Two boundary rulings worth review.** (1) The RTW target is compared **strictly** (`> 80%`), as AC 2 and the tile annotation state — the prototype used `>= 80`, so a book at exactly 80.0% now warns where the prototype passed it. (2) Pass/warn is decided on the **rounded** value, the one on screen: a mean of 29.6 days displays `30d`, and `30d ✓ <30d` would be indefensible whatever the unrounded arithmetic said. Both are stated in code comments and pinned by tests.
- **One documented deviation from the story text.** The story allowed either extending `/stats/topbar` or adding `/stats/sla`; the separate endpoint and query key were chosen because the two aggregates have different costs and, from Epic 3, different invalidation triggers (a payment approval moves a tile, only a settlement moves the strip). A shared key would recompute both whenever either changed.
- **Story 1.4's seam assertions were followed, not deleted.** Both the vitest and Playwright tests that asserted the SLA slot was empty now assert the strip occupies it. What 1.4 guaranteed was a slot in the *shared* bar; the strip filling it is that guarantee being kept, and a deleted test would have left "every role gets one" resting on nothing structural.
- **Tests:** 151 server (47 new: 28 aggregation + 19 endpoint), 42 vitest (18 new: tile rendering per status, formatting precision, target annotations both ways, `no_data` em dashes, mixed strips, skeletons, honest degradation, and four tooltip/keyboard tests), 30 Playwright (7 new, `@story:1-5 @epic:1`, one `@smoke`). `ruff` + `ruff format --check` clean, mypy clean (61 files), eslint clean (2 pre-existing warnings in vendored shadcn files), `tsc --noEmit` clean in web and e2e, generated OpenAPI client regenerates byte-identically, `alembic check` reports no drift (this story adds no schema).
- **Verified live on both stacks.** e2e (`compose.e2e.yaml`, :8081): full suite 30/30 plus the 6-test `@smoke` set against a freshly reset stack. dev (`compose.yaml`, :8080): `/api/stats/sla` 401s unauthenticated, returns Ken Stoker's 2.8d / 8.2d / 57d / 86% unchanged by `?employerId=1&scopeAll=true`, and the rendered bar was screenshotted and compared against the prototype's strip.
- **For the repo admin:** the migrations CI job now also runs `tests/test_sla_endpoint.py` (DB-backed; the pure aggregation tests run in the server job). Two new dev-only dependencies, both named by the story: `hypothesis` (NFR-7 property tests) and `@testing-library/user-event` (keyboard-reachable tooltips). No runtime dependency was added.

### File List

New files (paths relative to repo root):

- `lineworker/server/services/worklist/sla.py` (the single SLA aggregation — AD-2)
- `lineworker/server/tests/test_sla_aggregation.py` (unit + Hypothesis properties + the AD-2/AC-3 structural tests)
- `lineworker/server/tests/test_sla_endpoint.py` (DB-backed, per-persona, all three roles)
- `lineworker/web/src/features/shell/SlaStrip.tsx`, `lineworker/web/src/features/shell/SlaStrip.test.tsx`
- `lineworker/e2e/stories/1-5-sla-strip-for-every-role.spec.ts`

Modified files:

- `lineworker/server/config.py` (four SLA targets with the `TODO(JDM)` marker and range validation)
- `lineworker/server/data/repositories/claims.py` (`select_claim_columns` — scoped projection)
- `lineworker/server/services/worklist/__init__.py` (exports `sla`), `lineworker/server/api/routers/stats.py` (`GET /stats/sla`)
- `lineworker/server/tests/seed_fixture.py` (`expected_sla_strip` — independent oracle)
- `lineworker/server/pyproject.toml`, `lineworker/server/uv.lock` (hypothesis, dev group)
- `lineworker/web/src/api/stats.ts` (`useSlaStrip`), `lineworker/web/src/api/queryKeys.ts` (`stats.sla`), `lineworker/web/src/api/schema.d.ts` (regenerated)
- `lineworker/web/src/features/shell/TopBar.tsx` (renders the strip in 1.4's slot), `lineworker/web/src/features/shell/TopBar.test.tsx` (the seam assertion follows it)
- `lineworker/web/src/test/api-mock.ts` (`sla` route + fixtures), `lineworker/web/src/test/setup.ts` (`ResizeObserver` stub)
- `lineworker/web/package.json`, `lineworker/web/package-lock.json` (@testing-library/user-event, dev)
- `lineworker/e2e/fixtures/seed.ts` (`expectedSlaFor` oracle), `lineworker/e2e/fixtures/login.ts` (Ken Stoker persona), `lineworker/e2e/stories/1-4-scoped-top-bar-caseload-stats.spec.ts` (seam assertion follows the strip)
- `.github/workflows/ci.yaml` (the new DB-backed test file in the migrations job)
- `_bmad-output/implementation-artifacts/1-5-sla-strip-for-every-role.md` (this file), `_bmad-output/implementation-artifacts/sprint-status.yaml`

Added or modified in the review round (2026-08-10):

- `lineworker/server/tests/test_sla_aggregation.py` (the AD-2 guard pattern extracted to `SLA_COLUMN_READ`, plus 9 new tests proving it matches what it must and not what it must not)
- `lineworker/server/services/worklist/sla.py` (`SlaMetric.decimals`; a single `_no_data` constructor so an empty segment cannot be spelled two ways), `lineworker/server/api/routers/stats.py` (`decimals` on the response), `lineworker/server/tests/test_sla_endpoint.py` (asserts it)
- `lineworker/web/src/features/shell/SlaStrip.tsx` (formats from the server's precision), `lineworker/web/src/features/shell/SlaStrip.test.tsx`, `lineworker/web/src/test/api-mock.ts`, `lineworker/web/src/api/schema.d.ts` (regenerated)
- `lineworker/e2e/fixtures/seed.ts` (`no_data` branch + exact-numerator rounding), `lineworker/e2e/stories/1-5-sla-strip-for-every-role.spec.ts` (`TONE` gains `no_data`)
- `lineworker/deploy/compose.yaml` (`env_file` on the api service — the fix that makes AC 3 true in the shipped stack), `lineworker/deploy/.env.example` (documents the SLA and risk knobs)

### Code Review Follow-ups (2026-08-10)

Five findings, each reproduced before acting. All five fixed; two of them were defects in this story's *own* safeguards, which is the more useful half of the review.

- [x] **[Med] The AD-2 guard never matched the idiom it exists to catch.** `test_nothing_outside_the_worklist_aggregation_reads_the_sla_source_columns` used `(?<![\w.])` — copied from Story 1.4's threshold guard, where excluding a preceding `.` correctly stops `1.65` reading as the number 65. Carried over to attribute *names*, that exclusion made the pattern blind to `Claim.sla_pick_days`, the one form a duplicate aggregation would actually be written in. The test passed while enforcing nothing, under a module docstring claiming it "enforces that literally". Confirmed by planting `services/worklist/_fake_dupe.py` containing `sa.func.avg(Claim.sla_pick_days)`: green before the fix, red after. The pattern now lives in a module constant with two parametrized tests of its own — one proving it matches five violating idioms, one proving it stays off `sla_settle_days`, `days_recovery` and other near-neighbours, because a guard that over-matches collects allowlist entries until it means nothing.
- [x] **[Low] Display precision was a second copy of a server rule.** `SlaTarget.decimals` decided what the value was rounded to *and* what the verdict was decided on, but was not sent — the client held its own `decimals` per tile. Today they agree; after the `TODO(JDM)` migration gives Settle one decimal, the server would warn on 61.5 and the tile would read `62d`, a number the server never computed. `decimals` is now on the wire beside `target` and `direction`, and `formatValue` takes it from the metric. The component holds no threshold, no operator and now no rounding rule.
- [x] **[Low] The e2e oracle could predict `"NaNd"` for an empty segment.** `mean()` divided by `values.length` unguarded and `expectedSlaFor` had no `no_data` branch, so a seed change emptying any persona's segment would have asserted `"NaNd"` with `bg-warn` against a correctly-rendered muted em dash — a failure pointing at the component instead of the fixture. The Python twin already guarded this; the TypeScript one now does too, and `TONE` gained the `no_data` row so the spec can expect what the UI actually does.
- [x] **[Low] The same oracle rounded in the arithmetic the server avoids.** `Math.round((sum / len) * factor)` is exactly the binary-float path `_rounded`'s docstring cites as the reason for `Decimal`: a true 2.65 evaluates as 26.499999999999996 and rounds to 2.6 where the server reports 2.7. No seeded persona hits a `.x5` boundary today, so this was latent rather than live. Scaling the (integer) sum before dividing keeps the numerator exact.
- [x] **[Low, pre-existing — fixed anyway] `deploy/.env` was a dead letter, so AC 3 was only half true.** Compose auto-loads `.env` for `${…}` interpolation but does not pass it into containers, and the image carries no `.env`, so any key the compose file did not name reached nothing. Verified live: `SLA_SETTLE_TARGET_DAYS=100` in `deploy/.env` left the endpoint reporting `target: 30.0`. That made this story's "targets come from configuration, never hardcoded" true in code and false in the shipped stack — and it had silently applied to Story 1.3's `SESSION_TTL_HOURS` (documented in `.env.example`) and Story 1.4's `RISK_*` since they landed. Fixed with `env_file: [{path: .env, required: false}]` on the api service — `required: false` because a clean checkout has no `.env` and must still `docker compose up`. Re-verified: the same key now yields `target: 100.0` and flips Settle to `pass`, and removing the file restores 30.0. `.env.example` documents the SLA and risk knobs and states why `compose.e2e.yaml` deliberately does **not** read the file (its specs assert against default thresholds; a local override leaking in would fail the suite with no defect behind it).

**Gate re-run after the fixes:** ruff + `ruff format --check` clean, mypy clean (61 files), pytest 160 (9 new), vitest 42, Playwright 30/30 + the 6-test `@smoke` set against a rebuilt e2e stack, generated OpenAPI client regenerates byte-identically, `alembic check` no drift.

**One process note for the record:** an `npx prettier --write` run during the fixes reformatted three web files to 80 columns. Prettier is not this project's tooling (no config, not a dependency), and the house width is ~100 — the reformatting was reverted rather than committed as incidental churn.

### Change Log

- 2026-08-10: Story 1.5 implemented — the single AD-2 SLA aggregation in `services/worklist/sla.py` (pure `strip_of` core, exact-decimal half-up rounding, `no_data` instead of the prototype's fabricated 7.4d/42d/87%), four AD-8 configured targets with a `TODO(JDM)`, a scoped repository projection, the parameter-free `GET /api/stats/sla` for every role, and the UX-DR2 SLA strip filling Story 1.4's top-bar seam with server-decided pass/warn styling, accessible tooltips, skeletons and honest-degradation em dashes. 47 new server tests (151 total, incl. 4 Hypothesis properties per NFR-7), 18 vitest (42), 7 e2e (30) plus `@smoke`; dev and e2e stacks verified live. Status → review.
- 2026-08-10: Code review — 5 findings, all 5 fixed with a regression test or a live verification each. The two that mattered were defects in this story's own safeguards: the AD-2 "exactly one aggregation" guard could not match `Claim.sla_pick_days` and so enforced nothing, and `deploy/.env` never reached the container, which made AC 3's "targets come from configuration" true in code and false in the shipped stack (a pre-existing gap that had also silenced Story 1.3's and 1.4's documented knobs). Display precision moved onto the wire so the client holds no rounding rule, and the e2e oracle stopped being able to predict `NaN` or round in float. Full gate re-run green (160 pytest, 42 vitest, 30 Playwright + @smoke).
