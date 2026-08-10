---
baseline_commit: 86f55cc68a873cc2a638eaefa175752028f4e3e8
---

# Story 1.4: Scoped Top Bar & Caseload Stats

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a logged-in user,
I want the top bar to show my identity and live caseload stats,
so that I see the size and risk of my book at a glance.

## Acceptance Criteria

1. **Given** any persona, **when** the console loads, **then** Caseload / Active Tx / High Risk tiles are computed server-side over only that persona's employer scope (FR-TOP-2, BR-ROLE-1), enforced in the repository layer via the caller context with `scope_all` as a tautology predicate — never a skipped filter (AD-7).
2. **And** no endpoint accepts caller-supplied scope.
3. **Given** the top bar, **when** it renders, **then** the role badge, label, and user chip (avatar initials + name + role) reflect the logged-in persona (BR-ROLE-2, UX-DR2).
4. **Given** scoped supervisor Jennifer Park, **when** her stats compute, **then** they cover only Toyota/GM/3M claims, while David Bline's cover all 100 (both under test).
5. **Given** derived values used by the tiles (`risk`, stage counts), **when** computed, **then** each comes from exactly one registered function in `services/derivations` (AD-10).

## Tasks / Subtasks

- [x] Task 1: Scope-enforcing repository layer — first real AD-7 enforcement (AC: 1, 2, 4)
  - [x] `server/data/repositories/claims.py`: every repository method takes the caller context `(user_id, role, employer_ids | ALL)` built by the 1.3 context-builder dependency — **no default, no optional**; a method cannot be called without one
  - [x] The employer filter is applied unconditionally: `ALL` (from `app_user.scope_all` only) makes the predicate a tautology (e.g. `WHERE TRUE` / no-op clause built by the same code path) — **never** an `if scope_all: skip filter` branch, and never a role check widening the filter (scope gates visibility; role gates capability)
  - [x] Repository unit tests: a scoped context returns only in-scope claims; `ALL` returns everything; constructing/calling without a context is impossible by signature
- [x] Task 2: Derivations registry — first entries (AC: 5)
  - [x] `server/services/derivations/`: the one registry of derived-value computers. Register `risk` (band of `sev_score`: `high` ≥ 65, `med` 35–64, `low` ≤ 34 — thresholds from config now, marked for JDM migration when ZEN lands per AD-8; values verified against all 100 seed records in 1.2) — and `severity` band if surfaced (same function, same thresholds)
  - [x] Stat aggregates (caseload count, active-treatment count, high-risk count) computed in `services/worklist` **through** the registry's `risk` function and the repository scope context — no SQL that re-encodes the ≥ 65 rule outside the registered function
  - [x] Unit tests: band boundaries (64/65, 34/35); a grep-style regression test or code-review note that no other module hardcodes 65
- [x] Task 3: Top-bar stats endpoint (AC: 1, 2, 4)
  - [x] `GET /api/stats/topbar` (auth-required): returns `{caseload, activeTx, highRisk}` for the **session's** persona — the route takes no scope, employer, or user parameters (AC 2); camelCase per convention
  - [x] Computed in `services/worklist` over the scoped repository; `activeTx` = claims with `stage = treatment`; `highRisk` = claims whose registered `risk` derivation returns `high`
  - [x] pytest (AC 4, both named personas under test): login as Jennifer Park → counts cover exactly the Toyota Motor Manufacturing/General Motors/3M Company claims (assert against seed-derived expected numbers); David Bline (supervisor) → caseload 100; also assert a handler persona (e.g. Sarah Williams → 3M-only) so all three roles hit the same path
  - [x] pytest: request with a smuggled scope attempt (`?employerId=…`, extra body fields) does not alter results — unknown params ignored/rejected, response identical
- [x] Task 4: Top bar UI — UX-DR2 (AC: 3)
  - [x] Persistent top-bar component rendered by both shells (dashboard + workspace) from 1.3's slot: brand mark ("L" + LINEWORKER + "Manufacturing WC" sub), role badge (👔 Supervisor / 📋 Handler / 📊 Analyst), spacer, Glossary + Switch buttons, three stat tiles, SLA-strip slot, user chip
  - [x] Stat tiles: Caseload (neutral) · Active Tx (warn accent) · High Risk (error accent) — values from the stats endpoint via TanStack Query (`queryKeys.stats.topbar`); loading skeleton + error state (NFR-3), never a client-side computation
  - [x] User chip: avatar initials (first letters of first/last name, client-derived for display only), name, role label ("WC Supervisor" / "Claims Handler" / "Data Analyst") from `/api/me`
  - [x] Seams for sibling stories: 📖 Glossary button renders **disabled with tooltip** until 1.6 wires the panel; the SLA-strip slot renders empty (or skeleton) until 1.5 fills it — document both in code comments
  - [x] Vitest: badge/label/chip render from `me` data for all three roles; tiles render from query data; disabled-glossary seam present
- [x] Task 5: E2E story spec (AC: 1, 3, 4)
  - [x] `e2e/stories/1-4-scoped-top-bar-caseload-stats.spec.ts` tagged `@story:1-4 @epic:1`, using the 1.3 login fixture
  - [x] Login as Jennifer Park → top bar shows her name/initials/supervisor badge and the Toyota/GM/3M-scoped counts; login as David Bline → Caseload tile reads 100; login as a handler → handler badge + that book's counts
  - [x] One `@smoke` happy path: David Bline logs in, top bar renders with Caseload 100

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story ships the persistent top bar (identity + three stat tiles) and — more importantly — the **AD-7 repository scope layer and the AD-10 derivations registry**, the two load-bearing mechanisms every later read path reuses. It does NOT ship: the SLA strip (1.5 — leave the slot), the glossary panel (1.6 — button disabled), any claim list/queue (2.1), or dashboard KPI cards (5.1 — those reuse this story's aggregates pattern, not the other way around). The prototype's `getMine()`/`HANDLER_MAP` client-side filtering and `setStats()` DOM pokes (lines ~989–990) are the exact anti-pattern this story replaces — visual contract only.

### Architecture compliance (binding ADs for this story)

- **AD-1:** tile numbers appear on screen ⇒ a service computed them. The SPA renders `{caseload, activeTx, highRisk}` verbatim.
- **AD-7 (the heart of this story):** scope enforced in the repository layer via a mandatory caller context; `scope_all` → tautology predicate, never a skipped filter, never a role-conditional bypass; context built in exactly one place (1.3's dependency); no endpoint accepts caller-supplied scope. The Jennifer Park vs David Bline test pair is the named, epics-mandated proof.
- **AD-10:** `risk` gets its single registered computer here; stat aggregates consume it. Queue cards (2.1), the risk gauge (2.2), and dashboard KPIs (5.1) will all call the same function — that future consistency is why the registry, not an inline expression, is required now.
- **AD-8 (forward note):** band thresholds (65/35) are parameters; ZEN/JDM is not installed until Epic 2 — read them from the config module now with an explicit `TODO(JDM)` marker, consistent with the conventions row ("SLA targets from config/DB — never hardcoded") and Story 1.1's "ZEN arrives with its first consuming story".
- **AD-9:** tiles via TanStack Query under shared `queryKeys`; no derived value computed client-side (initials are presentation, not a domain derivation).

### Data notes

- Tables used: `claim`, `app_user`, `user_employer_assignment` (all from 1.2). **No new tables, no writes** — this story is read-only, so no AD-4 commands and no AD-12 ownership changes; `services/worklist` here gains read aggregates only (its write-owner role starts in Epic 3).
- `risk` is not a column (1.2 banned it); it derives from `claim.sev_score`. Aggregate SQL may push the band predicate into the query **only** if the predicate is generated from the registered derivation's thresholds (one source of truth), e.g. the derivation module exposes both the row-level function and the SQLAlchemy filter expression.
- Expected test numbers: derive from the seed at test time (count the seed rows per employer) rather than hardcoding magic numbers — the seed is the fixture.

### UX notes (UX-DR2; prototype lines ~449–481 are the contract)

- Layout order per prototype: brand · role badge · spacer · Glossary · Switch · tstat×3 · SLA strip · user chip.
- Tile styling: the prototype marks Active Tx with the warn class and High Risk with the error class — reuse the 1.1 status tokens (`--color-wn`/`--color-er` equivalents); light palette per the 1.1 ruling.
- High Risk tile semantics: "Severity ≥ 65/100" (the supervisor KPI card states it; same threshold, same source).
- Role badge text/icons verbatim: 👔 Supervisor · 📋 Handler · 📊 Analyst; chip role labels: "WC Supervisor" / "Data Analyst" / "Claims Handler".

### Testing requirements (this story's definition of done)

- pytest: repository scope tests (scoped/ALL/signature), derivation band boundaries, stats endpoint per-persona numbers (Park / Bline / one handler), scope-smuggling rejection.
- Vitest: top-bar rendering for all three roles, loading/error states, seams.
- E2E: `1-4-scoped-top-bar-caseload-stats.spec.ts` green (`@story:1-4 @epic:1`, one `@smoke`); story cannot reach `review`/`done` until it passes (AD-15).

### Project Structure Notes

- New: `server/data/repositories/` (scope-enforcing; pattern set here is the template for every future repository), `server/services/derivations/` (registry), `server/services/worklist/` (read aggregates), `server/api/routers/stats.py`.
- Web: `web/src/components/` top-bar component (shared across shells — it is chrome, not a feature folder), `queryKeys` gains `stats`.
- The caller-context type lives in one shared module (e.g. `server/data/context.py`) imported by deps and repositories — not redefined per layer.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 1.4]
- AD-7 full rule (tautology predicate, one context-builder, no caller-supplied scope, Kaya/Deere normative case): [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-7]
- AD-10 (one computer per derived value; risk in its binds list): [Source: ARCHITECTURE-SPINE.md#AD-10]
- AD-8 + Config convention (thresholds as parameters; config/DB never hardcoded): [Source: ARCHITECTURE-SPINE.md#AD-8 / #Consistency Conventions]
- Scoping capability row: [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map (Roles & scoping)]
- UX-DR2 + top-bar markup: [Source: epics.md#UX Design Requirements; docs/Workers_Comp_Prototype.html lines ~449–481]
- Prototype anti-pattern being replaced (getMine/setStats): [Source: docs/Workers_Comp_Prototype.html lines ~989–990]
- Risk band boundaries verified against seed data (high ≥ 65, med 35–64, low ≤ 34): [Source: docs/Workers_Comp_Prototype.html ALL_CLAIMS dataset analysis; BRD §7.2 via implementation-readiness-report-2026-08-09.md#Additional Requirements]

## Dev Agent Record

### Agent Model Used

claude-opus-5[1m] (Claude Opus 5, 1M context)

### Debug Log References

- **The AD-7 caller-context types had to move before a repository could require one.** Story 1.3 declared `CallerContext`/`AllEmployers` inside `api/deps.py`; a repository importing them from there would have inverted the layering (`api` → `services` → `data`). They now live in `data/context.py`; the *builder* is untouched and still the only place `scope_all` becomes `ALL`.
- **The grep-style "nobody else hardcodes the band" test caught its own author.** Its first red was `services/worklist/stats.py` — a docstring saying "this module does not know that the boundary is 65". The prose was the fix, and the episode is the argument for keeping the test blunt.
- **`services/` was an implicit namespace package.** Story 1.1 created `services/<name>/__init__.py` for six future services but never `services/__init__.py`. Added one, so `services` is a regular package with a docstring saying what belongs in it.
- **A vitest stub that resolves immediately makes loading states unobservable.** `stubApi` gained a `"pending"` route (a promise that never settles) — without it the skeleton assertion was a race, and "shows a skeleton" and "shows a zero" are the two states this story most needs to keep apart.
- **`toContainText` on a stat tile is a false-pass generator**: `"15"` contains `"5"`. Every tile's value carries its own `data-testid` so both vitest and Playwright can match it exactly.
- **The stats error test uses a 404, not a 500.** The shared QueryClient retries 5xx twice with backoff (Story 1.3's policy, deliberately kept), so a 500 would have made the assertion race the retry timer rather than test the render branch. A proxy 404 is a real, non-retryable case `client.ts` already documents.

### Completion Notes List

- **AC 1/2 — the AD-7 repository layer (the load-bearing half of this story).** `data/repositories/claims.py` is the first repository to read claim data and the template for every later one. Three properties are asserted *structurally*, not by example, because examples do not stop the next repository from getting it wrong: a caller context is required by signature (a reflective test walks every public function and rejects a default), the employer predicate is always a predicate (asserted on compiled SQL — `ALL` produces `WHERE true`, and the difference between that and an omitted filter is invisible in a result set but decisive the first time somebody adds an `OR`), and the module contains no role check at all (scope gates visibility, role gates capability).
- **AC 5 — one computer for `risk`.** `services/derivations` registers it under its canonical name and refuses a second registration for the same name. The derivation exposes two forms — `of(score)` in Python and `sql_is(band)` for aggregates — and they are one source, not two implementations: `sql_is` compares against the CASE expression built from the same two attributes `of()` reads. A DB test bands all 100 seeded claims both ways and asserts the sets are identical, which is what makes the Dev Notes' permission to push the band into SQL actually checkable.
- **AD-8 forward note honoured.** The thresholds are `Settings` fields with an explicit `TODO(JDM)`, plus a `model_validator` that refuses `med_min > high_min` at startup — an inverted pair would silently reclassify a portfolio rather than fail. A test proves the bands move when the config moves, so the ZEN migration is a change of *source*, not a hunt for literals.
- **AC 1/2/4 — the endpoint.** `GET /api/stats/topbar` takes **no parameters at all**; AC 2 is asserted against the published OpenAPI document (`parameters: []`, no request body) as well as behaviourally, with seven smuggling attempts (`employerId`, `employerIds`, `scopeAll`, `userId`, `role`, snake and camel) all returning byte-identical results. Expected numbers come from `data/seed/seed_data.json` counted independently in `tests/seed_fixture.py` — no magic numbers, and the expectation cannot agree with the code by construction. All four personas covered: Park 27/5/10, Bline 100/28/32, Sarah Williams 8/1/4, analyst 100/28/32.
- **AC 3 — the bar.** Rendered by both shells in the prototype's order (brand · badge · spacer · Glossary · Switch · tiles · SLA slot · chip), with the prototype's own tokens for the warn/error tile accents. Three states per tile: value, skeleton while in flight, and **em dash on failure — never a zero**. That distinction is the whole of NFR-3 here: "no high-risk claims" and "we could not count your high-risk claims" are different facts, and one of them sends a handler home early. `Record<UserRole, …>` maps for the badge and chip labels make a new `user_role` member a compile error rather than an `undefined` beside somebody's name.
- **Seams left explicitly inert:** the 📖 Glossary button renders disabled with a tooltip naming Story 1.6, and the SLA-strip slot renders empty in position for Story 1.5. Both are asserted by tests so a sibling story cannot quietly delete the frame.
- **Two documented deviations.**
  1. **Component location.** The story's Project Structure Notes put the top bar in `web/src/components/`; it stays in `web/src/features/shell/` where Story 1.3 created it. That folder *is* the chrome folder (it holds both shells, the route guard and the route table) and `components/` currently holds only vendored shadcn primitives — splitting chrome across two directories to satisfy a path would cost cohesion and buy nothing. The story's actual requirement ("shared across shells, not a feature folder") holds either way.
  2. **Avatar initials.** The story says client-derived; the chip renders `me.initials`, which `/api/me` has returned since Story 1.3. AD-10 does not bind initials (they are presentation), but the rule already had exactly one implementation and one is the right number. No new client-side derivation was introduced.
- **Tests:** 101 server (35 new: repository contract + scoped reads, band boundaries and config-sourced thresholds, SQL/Python agreement across the seed, registry duplicate/unknown handling, the hardcoded-threshold grep, per-persona endpoint numbers, scope smuggling, parameter-free contract, 401, and persona re-resolution). 24 vitest (9 new: three roles' badge/label/chip, tiles from query data, skeleton, honest-degradation em dash, both seams, and the bar rendering before `/me` answers). 23 Playwright (8 new, `@story:1-4 @epic:1`, one `@smoke`), including an in-page `fetch` with smuggled scope parameters that still returns the caller's own book.
- **Verified live on both stacks:** dev (`compose.yaml`, :8080) — `/api/stats/topbar` 401s unauthenticated, then returns 27/5/10 · 100/28/32 · 8/1/4 · 100/28/32 for Park, Bline, Williams and the analyst, unchanged by `?employerId=1&scopeAll=true`; the rendered bar screenshotted and eyeballed against the prototype. e2e (`compose.e2e.yaml`, :8081) — full suite 23/23 plus the 5-test `@smoke` set against a freshly reset stack. `alembic check`: no drift (this story adds no schema).
- **For the repo admin:** the migrations CI job now also runs `test_scoped_repository.py`, `test_derivations.py` and `test_topbar_stats.py` — they are DB-backed and would otherwise skip in the lint job. No new dependencies were added by this story.

### File List

New files (paths relative to repo root):

- `lineworker/server/data/context.py` (AD-7 caller-context vocabulary, moved out of `api/deps.py`)
- `lineworker/server/data/repositories/claims.py` (the scope-enforcing repository template)
- `lineworker/server/services/__init__.py`
- `lineworker/server/services/derivations/registry.py`, `lineworker/server/services/derivations/risk_band.py`
- `lineworker/server/services/worklist/stats.py`
- `lineworker/server/api/routers/stats.py`
- `lineworker/server/tests/seed_fixture.py` (seed-derived expectations)
- `lineworker/server/tests/test_scoped_repository.py`, `test_derivations.py`, `test_topbar_stats.py`
- `lineworker/web/src/api/stats.ts`
- `lineworker/web/src/features/shell/TopBar.test.tsx`
- `lineworker/e2e/fixtures/seed.ts` (independent oracle for the expected counts)
- `lineworker/e2e/stories/1-4-scoped-top-bar-caseload-stats.spec.ts`

Modified files:

- `lineworker/server/api/deps.py` (imports the context types; builder unchanged)
- `lineworker/server/api/app.py` (includes `stats_router`), `lineworker/server/api/routers/__init__.py`
- `lineworker/server/config.py` (`risk_high_min` / `risk_med_min` + the non-overlap validator)
- `lineworker/server/data/repositories/__init__.py` (exports `claims`)
- `lineworker/server/services/derivations/__init__.py`, `lineworker/server/services/worklist/__init__.py` (were empty Story 1.1 placeholders)
- `lineworker/server/tests/test_auth.py` (`AllEmployers` import follows the type)
- `lineworker/web/src/features/shell/TopBar.tsx` (the story's UI)
- `lineworker/web/src/api/queryKeys.ts` (`stats.topbar`), `lineworker/web/src/api/schema.d.ts` (regenerated)
- `lineworker/web/src/test/api-mock.ts` (`stats` route, `"pending"` routes, `me` fixtures)
- `lineworker/README.md` (source tree: `features/shell/`, `features/login/`)
- `.github/workflows/ci.yaml` (three new DB-backed test files in the migrations job)
- `_bmad-output/implementation-artifacts/1-4-scoped-top-bar-caseload-stats.md` (this file), `_bmad-output/implementation-artifacts/sprint-status.yaml`

Added or modified in the review round (2026-08-10):

- `lineworker/server/data/repositories/claims.py` (`select_from(Claim)`; `count_statement` exposed for a DB-free assertion)
- `lineworker/server/services/derivations/risk.py` → `risk_band.py` (module/export name collision), `lineworker/server/services/derivations/__init__.py` (the naming rule)
- `lineworker/server/tests/test_scoped_repository.py` (2 new tests), `lineworker/server/tests/test_derivations.py` (1 new test)
- `lineworker/web/src/api/auth.ts` (clear the cache on login, not only on logout)
- `lineworker/web/src/features/shell/RequireSession.tsx` (a known user outlives a failed refetch)
- `lineworker/web/src/features/login/LoginScreen.tsx` (`isSuccess` breaks the redirect loop; the unroutable alert resets)
- `lineworker/web/src/App.test.tsx` (3 new tests), `lineworker/web/src/features/login/LoginScreen.test.tsx` (2 new tests)
- `_bmad-output/implementation-artifacts/deferred-work.md` (finding 6 folded into the existing session-policy deferral)

### Code Review Follow-ups (2026-08-10)

Six findings, all reproduced before acting. Five fixed with a regression test each (every test verified to fail against the pre-fix code); one was already a recorded deferral. Writing the regression test for finding 2 surfaced a seventh defect the review did not report.

- [x] **[High, in effect] `count_claims_matching` inferred its FROM clause from the caller's predicates.** A SELECT of only aggregates has no table of its own, so with an unbounded scope *and* an all-tautology bucket set — `{"caseload": sa.true()}` for a full-portfolio supervisor — it compiled to `SELECT count(*) FILTER (WHERE true) WHERE true`: a valid query over no rows that answers **1** for everybody, with the AD-7 scope predicate silently gone. Today's three-bucket caller escaped only because two of its predicates happen to name a column, while the module docstring explicitly blesses `sa.true()` as a bucket — so the next single-count caller (a "my caseload" badge) would have hit it. Fixed with `.select_from(Claim)`; the statement builder is now exported as `count_statement` so the compiled SQL is asserted **without** a database, in the CI job that has none.
- [x] **[Med] A second persona could inherit the first one's cached numbers.** `useLogin.onSuccess` seeded `me` but never cleared the cache, and `queryKeys.ts` claimed the clear-on-switch guarantee that only `useLogout` provided. Logout is not the only way a session ends — TTL expiry, a database reset, an admin revocation, another tab — and after any of those the guard redirects to the login screen with the previous persona's still-*fresh* (`staleTime: 30_000`) `stats.topbar` entry intact, so the next persona's top bar would render someone else's caseload under their own name. That is this story's central promise failing on the client after the server got it right. Fixed by clearing before seeding, symmetrically with `useLogout`.
- [x] **[High, found while fixing the above] An expired session put the SPA in an infinite redirect loop.** TanStack keeps the last good `data` when a *refetch* fails, so on a 401 revalidation `RequireSession` redirected to `/`, `LoginScreen` saw stale `me` data, concluded "already signed in", and redirected back to the shell — "Maximum update depth exceeded" and a blank page, the exact failure `routes.ts` warns about in its own comment. Reproduced in the test that now covers it. Fixed by reading `isSuccess`: being signed in means the last `/me` *succeeded*, not that one once did.
- [x] **[Low] A failed background refetch tore down a working shell.** `if (error || !me)` fired even with data present, so one unlucky non-401 on a `/me` revalidation replaced the screen a user was mid-task on with a full-screen "could not confirm your session" notice — the opposite of that module's stated intent. Now only a genuinely unknown user gets the notice.
- [x] **[Low] `services.derivations.risk` was shadowed by its own export.** `from services.derivations.risk import risk` rebound the package attribute from the submodule to the `Derivation`, so `services.derivations.risk.RiskBand` raised `AttributeError`. This package is the template later derivations copy, so the module is renamed `risk_band.py` and the package now states the rule — *name the module after the rule, the export after the derived value* — with a test that walks every derivation module and enforces it, because the trap only bites whoever adds `days_open.py`.
- [x] **[Low] The unroutable-role alert was sticky.** `unroutableRole` was set but never cleared, so it stayed pinned to the login form through later attempts — including ones that failed for a different reason, whose own message the alert's ternary then never reached. Cleared on submit and on role change.
- [ ] **[Low] `login` does not revoke the session in its own request cookie.** Not fixed: this is item 1 of `deferred-work.md`, and revoking the presented cookie *is* the single-session-vs-multi-device policy decision — a one-line change that would settle a product question silently. The finding's sharper framing has been added to that entry. The only persona-switch path in the UI ("↩ Switch") does revoke.

**Gate re-run after the fixes:** ruff + `ruff format --check` clean, mypy clean (58 files), pytest 104 (3 new), vitest 29 (5 new), Playwright 23/23 + `@smoke` against a rebuilt e2e stack, `alembic check` no drift, generated client byte-identical.

### Change Log

- 2026-08-10: Story 1.4 implemented — the AD-7 scope-enforcing claim repository (mandatory caller context, tautology predicate for `ALL`, no role branch), the AD-10 derivations registry with `risk` as its first entry (Python + SQL forms from one source, thresholds as AD-8 config parameters with a `TODO(JDM)`), `services/worklist` read aggregates, the parameter-free `GET /api/stats/topbar`, and the UX-DR2 top bar with three server-computed tiles, loading skeletons, honest-degradation em dashes and inert seams for Stories 1.5/1.6. 35 new server tests (101 total), 9 vitest (24), 8 e2e (23) plus `@smoke`; dev and e2e stacks verified live. Status → review.
- 2026-08-10: Code review — 6 findings, 5 fixed with regression tests, 1 confirmed as an existing deferral. The two that mattered: the aggregate count statement inferred its FROM clause from the caller's predicates and so could drop the AD-7 scope filter entirely, and a session that ended without a logout let the next persona inherit the previous one's cached tiles. Writing the second regression test exposed an unreported infinite redirect loop between the route guard and the login screen on session expiry, fixed in the same pass. Full gate re-run green (104 pytest, 29 vitest, 23 Playwright + `@smoke`).
