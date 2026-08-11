---
title: 'Story 2.1 — Prioritized, Filterable Claim Queue'
type: 'feature'
created: '2026-08-11'
status: 'done'
baseline_revision: 'e889089'
final_revision: 'b4a5494'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/_bmad-output/implementation-artifacts/2-1-prioritized-filterable-claim-queue.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** A logged-in handler reaches `/workspace` and sees the stub text "Claim queue arrives in Epic 2." There is no way to see a caseload, no ordering that puts the riskiest claim first, and no selected claim for Stories 2.2–2.6 to render against.

**Approach:** Build the left pane and everything behind it: register the four missing queue derivations (`days_open`, `siu_review`, `rtw_blocked`, `payment_due`) in `services/derivations`; land the ZEN rules tier (first JDM consumer) so weights, bands and thresholds are versioned data; compute `priority_score` exactly once in `services/worklist`; serve grouped, filtered, cursor-paginated results from `GET /api/claims/queue`; and render collapsible stage groups of claim cards with URL-held selection.

## Boundaries & Constraints

**Always:**
- Every number on a card is computed server-side (AD-1). The SPA never derives a flag, a score, a band, or a day count — it renders the payload.
- Each derived value has exactly one registered computer in `services/derivations` (AD-10); queue and future detail read the same function.
- Weights, caps, bands and thresholds live in a versioned JDM document; the score arithmetic is typed Python reading its tunables from it (AD-8). Neither tier holds the other's rule element.
- Scope is a repository-layer predicate built from the auth dependency's `CallerContext` (AD-7). The endpoint has nowhere to put a caller-supplied scope.
- `priority_score` exists in exactly one place — Epic 5's top-30 worklist will import it unchanged (AD-2).
- Money in integer cents, dates ISO-8601, enum wire values snake_case with UI-owned labels, camelCase JSON via `ApiModel`, claims addressed by business ID `WC-nnnn`, errors as RFC 9457 problem+json.
- Every list response is `{items, nextCursor, total}` with a real cursor and a real count (Lists convention).
- Loading, error and empty states on the pane and on every stage group; "no claims in scope", "no claims match this filter" and "no claims in this stage" are three distinct messages (NFR-3).
- The palette is the light one in `web/src/index.css` (Story 1.1 ruling). `epic-2-context.md`'s "dark console aesthetic" line restates epics.md's known discrepancy and is overridden.

**Block If:**
- `zen-engine` cannot be installed, or its Python API cannot evaluate a committed JDM document to a parameter block — abandoning ZEN is an architecture decision (AD-8), not an implementation choice.
- Migrating the risk bands out of `Settings` would require changing an Epic 1 acceptance assertion — that is, an assertion about *what a persona sees* rather than about where the number is read from. Rewriting `test_derivations.py`'s three `Settings(...)` constructions, the `.env.example` block and the compose comment is expected work, not a blocker.

**Never:**
- No detail pane, no tabs, no case header, no inline editing, no injury diagram, no copilot — those are 2.2–2.6 and Epic 6. The centre pane is a placeholder naming the selected claim.
- No client-side re-filtering of a cached superset; changing the filter refetches (AD-1).
- No writes. Nothing in this story mutates domain data; no `timeline_event`, no audit rows beyond what login already emits.
- No derived value becomes a `claim` column (`tests/test_no_derived_columns.py` guards this).
- No re-implementation of the score in SQL or TypeScript.
- Do not retrofit `/personas` or `/glossary` onto the new pagination machinery; their `total` is already truthful because they are unpaged.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Handler loads the queue | Session cookie for Kaya Johnson; `GET /claims/queue` | 4 stage groups (`intake` 3, `investigation` 1, `treatment` 15, `settled` 26), items priority-sorted desc within each, `Cache-Control: no-store` | No error expected |
| Scoped read | Session for Sarah Williams (3M only) | Exactly her 8 claims; no Boeing/Toyota claim ID appears in any group | No error expected |
| Filter applied | `?filter=litigation` | Only `litigation_flag` claims, still grouped and sorted; empty groups present with `total: 0` | Unknown `filter` value → 422 problem+json from FastAPI enum validation |
| Priority marker | A stage group whose top claims score above the marker threshold | The first 3 items of that group with `score > threshold` carry `priorityMarker: true`; a 4th equally-high claim does not | No error expected |
| Settled sinks | A settled claim with litigation and high severity | Its score is negative; it sorts last within `settled` and never carries the marker | No error expected |
| Group paging | A group with more claims than the page limit | `nextCursor` non-null, `total` = full group count, `items` = one page; `?stage=settled&cursor=…` returns the next page of that group only | Cursor whose embedded filter ≠ request filter → 400 problem+json; undecodable cursor → 400 |
| Empty stage | Sarah Williams, `intake` | `items: []`, `total: 0`, `nextCursor: null`; UI shows "No claims in this stage." | No error expected |
| Filter matches nothing | `?filter=fraud` for a handler with no fraud claims | All four groups empty; UI shows the filter-empty message, not the scope-empty one | No error expected |
| Auto-select | `/workspace` with no `claim` search param | First item of the first non-empty group is selected; URL replaced with `?claim=WC-nnnn` | Caseload empty → no selection, scope-empty message, no navigation loop |
| Stale selection | `?claim=WC-9999` not in the payload | No card highlighted; the placeholder says the claim is not in this caseload | No redirect, no refetch loop |
| Queue request fails | API returns 500 | Pane renders `role="alert"` error state; the rest of the shell keeps working | 401 is handled globally by the existing query cache handler |

</intent-contract>

## Code Map

**Server — read before writing**
- `lineworker/server/services/derivations/registry.py` -- `Derivation(name, describes, build)` + `register`/`get`; duplicate names raise. `build` currently takes `Settings` — this story widens it (see Design Notes).
- `lineworker/server/services/derivations/risk_band.py` -- the template: frozen dataclass with `.of()` (Python) and `.sql_is()` (SQL) from one source; `register(...)` at module bottom.
- `lineworker/server/services/derivations/__init__.py` -- registration list; a derivation not imported here does not exist. **Module naming rule: name the module after the rule, never after the exported value.**
- `lineworker/server/services/worklist/{stats.py,sla.py}` -- service shape: pure function over projected rows + thin async wrapper; `sla.py` is the Hypothesis-tested pure-core precedent.
- `lineworker/server/data/repositories/claims.py` -- `employer_scope(ctx)`, `list_claims`, `select_claim_columns`; docstring already says "Story 2.1 adds the filters, ordering and paging". Every public function takes `ctx` positionally, no default.
- `lineworker/server/data/models/core.py` -- `Claim` columns; worker name is `employee.name`, employer label is `employer.short_name`. No `days_open` column, by design.
- `lineworker/server/api/routers/stats.py` -- router idiom: `ApiModel` response, `UNAUTHENTICATED_RESPONSE`, `Cache-Control: no-store`, no scope params.
- `lineworker/server/api/routers/glossary.py` + `auth.py` -- the `{items, nextCursor, total}` envelope as shipped in Epic 1.
- `lineworker/server/api/{app.py,routers/__init__.py,deps.py}` -- router registration; auth is app-level, so a new router is protected by default. Paths omit `/api` (`root_path`).
- `lineworker/server/config.py` -- lines ~68–77 are the `TODO(JDM)` risk-band block this story resolves; the SLA block below it does **not** move.
- `lineworker/server/data/versions/20260810_000{3,4,6,7}_*.py` -- migration idiom: string revision ids, `op.f(...)` constraint names, per-object GRANT block, JSON-file seed migrations. Current head is `0007_seed_glossary`.
- `lineworker/server/tests/test_derivations.py` -- lines ~136–138 assert `days_open` is **unregistered**; `test_no_module_outside_the_registry_hardcodes_the_band` bans a bare `65`/`35` in server `.py` outside its allowlist.
- `lineworker/server/tests/{conftest.py,seed_fixture.py,test_topbar_stats.py}` -- `seeded_db_url`, `claims_for(name, role)` oracle, and the `login_as` ASGI client pattern to copy.

**Web — read before writing**
- `lineworker/web/src/features/shell/WorkspaceShell.tsx` -- the stub to replace; `App.test.tsx` asserts a region named "Claim workspace".
- `lineworker/web/src/features/shell/{TopBar.tsx,SlaStrip.tsx}` -- component conventions: own hook, skeleton/error states, `data-testid` stems, `TONE_CLASS` maps.
- `lineworker/web/src/features/glossary/GlossaryPanel.tsx` -- empty-vs-no-match ordering and the `sr-only role="status"` result announcement to mirror for filter changes.
- `lineworker/web/src/api/{client.ts,queryKeys.ts,stats.ts,errors.ts}` -- `api.GET(path, { params: { query } })`, key registry (`as const`, entity-keyed, **never scope-keyed**), `ApiError`.
- `lineworker/web/src/components/ui/select.tsx` -- vendored Radix Select, unused so far; the filter dropdown is its first consumer (`test/setup.ts` already stubs `ResizeObserver`).
- `lineworker/web/src/index.css` -- tokens. `brand` (not `accent`), `muted-text` (not `muted`), `error/warn/steel/ok` + `-soft` pairs, `font-mono` for IDs and day counts.
- `lineworker/web/src/test/api-mock.ts` -- `StubRoutes` + fixtures; extend, do not replace.

**E2E / CI**
- `lineworker/e2e/fixtures/{test.ts,login.ts,seed.ts,selectors.ts}` -- custom `test` export, `PERSONAS`, seed oracles that restate rules rather than importing them.
- `lineworker/e2e/stories/1-4-scoped-top-bar-caseload-stats.spec.ts` -- house spec shape: tags in the `describe` title, one `@smoke` prefix, oracle-derived expectations.
- `.github/workflows/ci.yaml` -- the `migrations` job enumerates DB-backed test files **by hand**; a new one that is not listed never runs in CI.

**Reference (design + data only, never a code source)**
- `docs/Workers_Comp_Prototype.html` -- `hashStr` line 646; derived-flag pass lines 949–957; `priorityScore` 1120–1132; `STAGE_GROUPS` 1133–1138; `buildQCard` 1139–1151; `renderQ` + filter mapping 1152–1181; filter `<option>`s 489–498.

## Tasks & Acceptance

**Execution:**

- [x] `lineworker/server/pyproject.toml` -- add `zen-engine` (pinned, resolves to 0.53.0) to `dependencies`; add a `[[tool.mypy.overrides]]` entry with `ignore_missing_imports` for `zen.*` (mypy is strict and already covers `rules/`); `uv sync` to refresh `uv.lock` (CI runs `--frozen`) -- AD-8's engine is a project dependency, not a vendored blob.
- [x] `lineworker/server/rules/documents/derivation_thresholds.jdm.json` -- new. JDM document owning the parameters the AD-10 derivations read: `riskHighMin` 65, `riskMedMin` 35, `siuFraudScoreMin` 60, `rtwBlockedHashModulus` 5, `paymentDueHashModulus` 3 -- the prototype's demo tunables become data so a real definition later changes parameters, not call sites.
- [x] `lineworker/server/rules/documents/priority_weights.jdm.json` -- new. JDM document owning the scorer's parameters: `litigation` 40, `siuReview` 35, `rtwBlocked` 30, `pendingApproval` 25, `paymentDue` 20, `surgery` 15, `severityFactor` 0.3, `daysOpenFactor` 0.2, `daysOpenCap` 60, `settledPenalty` -100, `markerThreshold` 30, `markerCount` 3, `pageLimit` 50 -- every literal in the prototype's `priorityScore` becomes a versioned parameter (AD-8).
- [x] `lineworker/server/data/models/core.py` + `data/models/__init__.py` -- add `RuleDocument` (`id`, `key` Text, `version` Integer, `effective_from` Date, `content` JSONB, `created_at` timestamptz; unique `(key, version)`). Export it so `alembic check` sees it -- AD-8's "versioned in the DB with effective dates" needs a table, and this is its first consumer.
- [x] `lineworker/server/data/versions/20260811_0008_rule_document.py` -- new migration (down_revision `0007_seed_glossary`): create the table with `op.f(...)` constraint names and `GRANT SELECT` only to the app role -- rules are read-only at runtime; authoring is a migration.
- [x] `lineworker/server/data/versions/20260811_0009_seed_rule_documents.py` -- new migration: insert version 1 of both JDM documents, read from the committed `rules/documents/*.jdm.json` files, `effective_from` = the story's date -- same seed-from-committed-JSON pattern as `0004`/`0007`, so the file and the row cannot drift.
- [x] `lineworker/server/rules/engine.py` -- new. `load(db, key, as_of) -> LoadedDocument(version, content)` picking the highest `version` with `effective_from <= as_of`; a process-level LRU compiling `ZenEngine().create_decision(content)` keyed `(key, version)`; `evaluate(...)` returning the decision's `result`. Raise (never default) when a key is missing -- a silently-absent rule document would score every claim zero.
- [x] `lineworker/server/rules/parameters.py` -- new. Frozen typed dataclasses `DerivationThresholds` and `PriorityWeights` plus `thresholds_for(...)` / `weights_for(...)` that evaluate the documents and validate the result into them -- the JSON→Python boundary is typed once so no consumer indexes a raw dict.
- [x] `lineworker/server/services/derivations/registry.py` -- widen `Derivation.build` from `Callable[[Settings], T]` to `Callable[[DerivationThresholds], T]` and rename `for_settings` to `for_thresholds` -- parameters now come from the rules tier; the registry docstring already anticipates this move.
- [x] `lineworker/server/services/derivations/risk_band.py` -- build `RiskDerivation` from `DerivationThresholds` instead of `Settings`; behaviour unchanged -- completes the `TODO(JDM)` in `config.py` for the two band numbers.
- [x] `lineworker/server/config.py` -- delete `risk_high_min` / `risk_med_min` and the `_bands_must_not_overlap` validator (the ordering check moves into `DerivationThresholds`); keep the SLA block and its `TODO(JDM)` untouched -- one tier per rule element (AD-8).
- [x] `lineworker/deploy/.env.example` -- replace the "Derived-value parameters (Story 1.4)" block (lines ~28–32) with a note that the severity band is now a versioned rule document, not an env knob; leave the SLA block -- an `.env` line for a setting that no longer exists is worse than no line.
- [x] `lineworker/deploy/compose.yaml` -- update the `env_file` comment (line ~36) that names `RISK_HIGH_MIN` as an example override to name a setting that still exists -- the comment is the operator's map.
- [x] `lineworker/server/tests/test_derivations.py` -- rewrite the three cases that construct `Settings(risk_high_min=…, risk_med_min=…)` (lines ~58, ~72–73, ~81) to build `DerivationThresholds` directly, keeping the inverted-band refusal assertion against its new home -- the behaviour is unchanged; only the tier that owns the numbers moves.
- [x] `lineworker/server/services/derivations/open_duration.py` -- new, registers `days_open`: `(as_of - froi_date).days`, floored at 0, with `as_of` an explicit parameter -- named after the rule, not the value (test-enforced); an injected clock keeps unit tests and the e2e oracle deterministic.
- [x] `lineworker/server/services/derivations/queue_flags.py` -- new, registers `siu_review`, `rtw_blocked`, `payment_due` as the prototype's demo definitions, each documented as demo-grade per the architecture's Deferred list, with a faithful `hash_bucket` port of the prototype's `hashStr` -- AD-10 fixes where they are computed; their business definitions are explicitly deferred.
- [x] `lineworker/server/services/derivations/__init__.py` -- import the two new modules and add every new name to `__all__` -- the import list *is* the registration list; an unimported derivation does not exist to `get()`.
- [x] `lineworker/server/data/repositories/claims.py` -- add `select_queue_rows(db, ctx, predicate)` joining `employee` and `employer` for the card columns, applying `employer_scope(ctx)` unconditionally and ANDing the service's predicate, ordered by `claim_id` -- the repository decides *which rows*, the service decides *what counts*.
- [x] `lineworker/server/services/worklist/priority.py` -- new. `priority_score(claim, flags, weights) -> float` as pure typed arithmetic over a projected row, plus `QueueFilter` (the 8 snake_case values) → predicate mapping -- the exactly-once scorer (AD-2) that Epic 5's top-30 will import unchanged.
- [x] `lineworker/server/services/worklist/queue.py` -- new. Loads both rule documents once per request, reads the scoped rows, derives flags, scores, sorts `(-score, claim_id)`, groups by stage, marks the top `markerCount` above `markerThreshold` per group, and slices by cursor -- assembly server-side (AD-1); marker computed over the whole group so paging cannot change it. Re-export the public names from `services/worklist/__init__.py` alongside `topbar_stats`, per that package's explicit-export convention.
- [x] `lineworker/server/api/routers/claims.py` -- new. `GET /claims/queue` with `filter` (enum, default `all`), optional `stage`, optional `cursor`, optional `limit`; returns `{groups: {intake, investigation, treatment, settled}}` each `{items, nextCursor, total}`; `Cache-Control: no-store`; `UNAUTHENTICATED_RESPONSE`; no scope-shaped parameter exists -- AD-1/AD-7 in the signature, not in validation.
- [x] `lineworker/server/api/routers/__init__.py` + `api/app.py` -- export and `include_router` the new router -- registration is the only wiring; auth is app-level.
- [x] `lineworker/server/tests/test_rules_engine.py` -- new (DB-backed). Document load-by-key picks the highest effective version; a missing key raises; both seeded documents evaluate to their typed parameter blocks; the committed JSON files match the seeded rows.
- [x] `lineworker/server/tests/test_derivations.py` -- repoint the "unregistered derivation" assertion off `days_open` (now registered) onto a still-unregistered name; extend the band-literal allowlist to cover `rules/documents/*.jdm.json`; add per-derivation cases over seeded claims and a Hypothesis property that `days_open` is non-negative and monotone in `as_of`.
- [x] `lineworker/server/tests/test_priority_score.py` -- new. Each weight term toggles the score by exactly its seeded value; settled claims sink; the marker threshold is honoured; a fixture document with changed weights shifts scores with no code change; Hypothesis property — adding any positive-weight condition never lowers a non-settled claim's score, and the days-open term saturates at the cap.
- [x] `lineworker/server/tests/test_claims_queue.py` -- new (DB-backed). Grouping and per-group ordering against the seed oracle; all 8 filters; scoped handler sees only in-scope IDs; scope-shaped query params change nothing; cursor continuity (page 1 + page 2 = the group, no repeats, no gaps) and `total` = full group count; a cursor from a different filter is rejected.
- [x] `.github/workflows/ci.yaml` -- add `tests/test_rules_engine.py` and `tests/test_claims_queue.py` to the `migrations` job's enumerated file list and its comment block -- an unlisted DB-backed test never runs in CI.
- [x] `lineworker/web/src/api/schema.d.ts` -- regenerate via `npm run generate:api` and commit -- CI fails on a stale client.
- [x] `lineworker/web/src/api/queryKeys.ts` -- add `claims.queue(filter)` as a function member returning an `as const` tuple; keep scope out of the key -- a filter change must be a distinct cache entry (AD-9).
- [x] `lineworker/web/src/api/claims.ts` -- new. `useClaimQueue(filter)` over the generated client; `useStageGroupPage` for a group's `nextCursor`; re-export the card and filter types from `components["schemas"][…]` -- the feature never hand-rolls a fetch.
- [x] `lineworker/web/src/features/queue/{QueuePane,StageGroup,ClaimCard,FilterSelect}.tsx` -- new. Pane owns the query, the filter and the announcement; `StageGroup` is a collapsible `aria-expanded` header with icon, label and count chip plus its own empty state and "Show more" when `nextCursor` is present; `ClaimCard` renders the four rows from the payload only; `FilterSelect` is the vendored Radix Select with UI-owned labels -- server payload in, pixels out (AD-1/AD-10).
- [x] `lineworker/web/src/features/queue/useSelectedClaim.ts` -- new. Reads/writes the `claim` search param on `/workspace` and auto-selects the first item of the first non-empty group (replacing, not pushing, the entry) -- FR-LOGIN-3's auto-select slice; selection is local/URL state, never server state (AD-9).
- [x] `lineworker/web/src/features/shell/WorkspaceShell.tsx` -- replace the stub with the 3-pane row (queue · placeholder detail · placeholder copilot) under `TopBar`, keeping the `Claim workspace` region name -- 2.2 fills the centre against this selection.
- [x] `lineworker/web/src/test/api-mock.ts` -- add a `claimsQueue` route keyed on the request URL so a filter change is observable, plus `QUEUE_GROUPS` fixtures using real seed numbers -- filter-refetch cannot be asserted against a URL-blind stub.
- [x] `lineworker/web/src/features/queue/*.test.tsx` -- new. Card renders every field and badge; stage-empty vs filter-empty vs scope-empty; loading and error states; auto-select on mount; a filter change issues a new request rather than filtering in memory; clicking a card moves the highlight and the URL.
- [x] `lineworker/e2e/fixtures/seed.ts` -- add `expectedQueueFor(name, role)` restating the grouping, flag, scoring and marker rules from the seed JSON (never importing server code), computing `days_open` from `froi_date` against the UTC date -- the oracle must be able to disagree with the implementation.
- [x] `lineworker/e2e/fixtures/login.ts` -- add a second handler persona (Sarah Williams, 3M only) -- the scope and empty-stage assertions need a caseload that is not Kaya's 45.
- [x] `lineworker/e2e/stories/2-1-prioritized-filterable-claim-queue.spec.ts` -- new, `test.describe("@story:2-1 @epic:2 …")`, exactly one `@smoke`: log in as a handler → 4 stage groups with oracle counts → first claim auto-selected → pick a filter → list re-renders filtered → click a card → highlight and URL move. Plus: an empty-stage state, the 🔺 marker on the oracle's top-scoring claim, and a scoped handler never seeing an out-of-scope claim ID.

**Acceptance Criteria:**
- Given a handler with claims in only some stages, when the workspace loads, then all four stage groups render with icon, label and count, the empty ones showing "No claims in this stage.", and the first claim of the first non-empty group is selected with the URL carrying its business ID.
- Given the queue payload, when any card is inspected, then every flag, day count, risk band and marker on it came from the response, and no derivation exists in TypeScript — asserted by the absence of any scoring or flag logic under `web/src/features/queue/`.
- Given the seeded portfolio, when `priority_score` is computed, then it equals the pure-Python arithmetic over the seeded JDM weights for every claim, and Epic 5 could import that function unchanged.
- Given a rule document whose weights differ, when the same claims are scored, then the ordering changes with no code modification — proving the weights live in the rules tier.
- Given any persona, when they call the queue endpoint, then the result contains only claims their `user_employer_assignment` rows permit, and adding `employerId`, `userId`, `role` or `scopeAll` query parameters changes nothing.
- Given `uv run pytest`, `npm test`, `npm run typecheck`, `npm run lint` and the Playwright story spec, when all are run against a freshly reset e2e stack, then all pass — the AD-15 done-gate.

## Spec Change Log

## Review Triage Log

### 2026-08-11 — Review pass

- intent_gap: 0
- bad_spec: 0
- patch: 19: (high 1, medium 11, low 7)
- defer: 6: (high 0, medium 3, low 3)
- reject: 5
- addressed_findings:
  - `[high]` `[patch]` The cursor recorded only the priority-weights version, but the derivation thresholds, the resolved `as_of` date and the page size each also shape the ordering — a page-2 request cut under any changed input was accepted verbatim, silently skipping or repeating claims. `Cursor` now carries all four; `as_of` and `limit` are reused rather than re-resolved, and a mismatch is a 400 naming what disagreed.
  - `[medium]` `[patch]` Moving the risk bands to JDM dropped `config.py`'s `Field(ge=0, le=100)`, so a document with `riskHighMin: 1000` banded every claim `low` in silence. Range check restored in `DerivationThresholds.__post_init__`, with a test covering all four corners.
  - `[medium]` `[patch]` An out-of-range cursor offset returned empty `items` with `total > 0` and a null `nextCursor` — a group reading as non-empty and finished at once. Now `InvalidCursor`.
  - `[medium]` `[patch]` `rules/engine.py` cached decision compilation but not evaluation, so `/stats/topbar` — the shell's most frequently hit endpoint — ran a full `json.dumps` and a ZEN evaluation per request. Evaluation now cached per `(key, version, content, context)`; `_decision`'s docstring corrected, since it claimed the content was not part of the cache identity when `lru_cache` keys on every argument.
  - `[medium]` `[patch]` The priority-marker rule lived inside `_ranked_group` in `queue.py` while `priority_score` sat in `priority.py` precisely so Epic 5 could import it — the next consumer would have re-implemented it (the AD-2/AD-10 failure mode). Moved to `priority.py` as `priority_markers`, with its own tests, and its docstring now confronts the fact that the marker is filter-dependent.
  - `[medium]` `[patch]` `test_a_filter_that_matches_nothing_still_returns_four_empty_groups` used a persona who *has* a fraud claim, so its guard never fired and the real assertion never ran. Switched to a filter genuinely empty for her, with the premise as a hard `assert` so a seed change fails loudly instead of silently un-covering the case.
  - `[medium]` `[patch]` The settled-marker test asserted the −100 penalty puts the whole group below the threshold "however severe it was", which is arithmetically false (categorical weights total 165) and contradicted a test added in the same change. Renamed and reworded as a property of the seeded portfolio, naming the contradicting test.
  - `[medium]` `[patch]` NFR-3's three empty messages were two and a guess: with an empty book *and* a filter applied, the pane blamed the filter. The payload now carries `unfilteredTotal`, so scope-empty and filter-empty are decided from different facts. Also fixed "1 claims" and the announcement claiming a filtered count was the whole caseload.
  - `[medium]` `[patch]` `StageGroup` carried three paging defects: expansion survived a filter change and auto-fetched page 2 of the new filter; `hasMore` flipped false during the first fetch, unmounting the button and making its `disabled` and `Loading…` states unreachable; and accumulated pages could duplicate React keys and render a negative remainder. Expansion is now keyed to the filter it was opened under, `hasMore` waits for success, items dedupe by claim id, and the remainder is clamped.
  - `[medium]` `[patch]` The centre placeholder decided claim membership from the current filter's first page, so an in-scope claim was reported "not in this caseload" after a filter change (on the smoke path), after a "Show more", and — worst — when the queue request had simply failed. Replaced with an explicit present / unconfirmed / absent decision; absence is now claimed only from an answered, unfiltered, fully-loaded queue.
  - `[medium]` `[patch]` AC 2 required the absence of TypeScript derivation logic to be *asserted*; the server had its equivalent guard and the web side had only prose. Added `noDerivation.test.ts`, including a test proving the guard would catch a derivation and one proving it does not false-positive.
  - `[medium]` `[patch]` The cursor machinery had no browser-level coverage — the largest seeded group is 26 against a page limit of 50, so "Show more" could never appear in the e2e stack and every path AD-15 was meant to gate ran only under pytest. The story spec now drives a `limit=5` page round trip and a rejected cross-filter cursor through the logged-in browser session.
  - `[low]` `[patch]` `RuleDocument.created_at` had no default in model or migration, so the first ORM insert would fail on NOT NULL — a trap the "read-only at runtime" framing made easy to miss. Server default added.
  - `[low]` `[patch]` `select_queue_rows` took a `predicate` parameter with one caller passing `sa.true()` — speculative generality; removed.
  - `[low]` `[patch]` `oracle` was rebound inside the loop whose bounds came from it; renamed.
  - `[low]` `[patch]` `zen-engine` was specified as a range where the spec and the stack table both say pinned; pinned to `==0.53.0` and the lock refreshed.
  - `[low]` `[patch]` `config.py` kept twelve lines describing deleted fields under an empty section header; removed, along with an adjacent `TODO(JDM)` that still described the migration as pending.
  - `[low]` `[patch]` `compose.yaml`'s comment illustrated "variables an operator got silence from" with `LOG_LEVEL`, which is in the `environment:` block six lines below and therefore always worked; replaced with a variable that is genuinely `env_file`-only.
  - `[low]` `[patch]` `?claim=` with a blank value read as a selection rather than as absent, skipping auto-select and reporting an empty id as missing; and re-selecting the already-selected card pushed a history entry, making Back appear to do nothing. Both fixed.

## Design Notes

**Why the JDM document returns a parameter block rather than a score.** AD-8 splits the tiers: JDM owns parameters, Python owns formulas. So each document is evaluated **once per request** (not once per claim) and returns its constants; `priority_score` is then pure Python over 100 rows. This keeps the scorer Hypothesis-testable, keeps ZEN off the hot path, and makes "which version produced this ordering?" answerable — the response's ordering is a function of `(key, version)` the loader resolved.

**Why the risk bands move now.** The story's Task 1 names "risk band cut-offs" among the thresholds that must read from JDM, and `rtw_blocked` consumes the `risk` derivation directly. `config.py`'s `TODO(JDM)` already scripts the move: "changing where `risk_band.py` reads these two numbers from; no consumer of the derivation changes." The SLA targets stay in `Settings` — they belong to Story 1.5's aggregation and no acceptance criterion here touches them. Widening `Derivation.build` to take `DerivationThresholds` is the one ripple; `topbar_stats` and any other consumer pass the loaded thresholds instead of `Settings`. The alternative designs are all worse: a `build` that takes both tiers drags the rules loader into the top-bar path, and threading thresholds through every `.of()` call abandons the "built once, called many times" shape the registry exists for. The cost is that `RISK_HIGH_MIN` stops being an env override — replaced by a rule-document version, which is the operability model AD-8 chose; `.env.example` and the compose comment must say so rather than advertising a dead knob.

**Why `days_open` is derived from `froi_date`.** The prototype ships a static `daysOpen` per claim that correlates with no date in the record (checked against `doi`, `froi_date`, `actual_rtw`, `rtw_rec`, `settlement_days` — no consistent anchor), and Story 1.2 deliberately did not seed it. `froi_date` is when the claim was opened, so claim age counts from it. `as_of` is an explicit parameter defaulting to the UTC date, so a test can pin it and the e2e oracle can agree with a container in a different timezone.

**Cursor shape.** The score is Python-side, so keyset pagination in SQL is impossible without re-implementing the formula in the query — forbidden. The cursor is therefore an opaque base64 of `{filter, stage, offset, rulesVersion}` over the fully-scored, `(-score, claim_id)`-sorted group; `total` is the real group size and `nextCursor` is null only when the group is exhausted. This is the "build the pagination machinery once" item the deferred-work file parked for Epic 2's first pageable list. A cursor whose `filter` disagrees with the request is a 400, not a silent re-page.

**The priority marker is a property of the group, not the page.** `markerCount` items with `score > markerThreshold`, computed before slicing — so requesting page 2 cannot make a 4th claim sprout a marker. Strictly greater, matching the prototype's `priorityScore(c)>30`.

**Truncation stays in the UI.** The longest `injury_type` in the seed is 29 characters, so the prototype's 30-character clamp never fires. The wire carries the full string and the card uses Tailwind `truncate` with a `title` — one fewer presentation rule on the server, and no lost text for a screen reader.

**Demo derivations, faithfully ported.** `hash_bucket` is the prototype's `hashStr` (`h = (h*31 + ord(ch)) & 0xFFFFFFFF`) over the claim business ID. `rtw_blocked` and `payment_due` are hash-bucket demos by explicit architecture decision; the modulus of each is a JDM parameter so a real definition later changes data, not call sites. Comment them as demo-grade where a reader would otherwise assume business meaning.

## Verification

**Commands:**
- `cd lineworker/server && uv sync && uv run ruff check . && uv run ruff format --check . && uv run mypy .` -- expected: clean, including the new `rules/` package under strict mypy
- `cd lineworker/server && uv run pytest` -- expected: all pass; no new skips beyond the DB-gated ones
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:pw@127.0.0.1:55432/lineworker APP_DB_PASSWORD=lineworker_app_dev uv run pytest tests/test_rules_engine.py tests/test_claims_queue.py tests/test_derivations.py` -- against a disposable `pgvector/pgvector:pg18` on :55432 -- expected: pass, including the grant assertions
- `cd lineworker/server && uv run alembic upgrade head && uv run alembic check` -- expected: clean run against a fresh DB, no model/migration drift
- `cd lineworker/web && npm run generate:api && git diff --exit-code -- src/api/schema.d.ts` -- expected: no diff once the regenerated client is committed
- `cd lineworker/web && npm run lint && npm run typecheck && npm test` -- expected: clean; new queue tests pass; `App.test.tsx` still finds the "Claim workspace" region
- `cd lineworker/e2e && npm run typecheck` -- expected: clean
- `docker compose -f lineworker/deploy/compose.e2e.yaml up -d --build --wait && cd lineworker/e2e && npx playwright test --grep "@story:2-1\b|@smoke\b"` -- expected: the new spec and the whole smoke set green against the freshly reset stack
- `docker compose -f lineworker/deploy/compose.e2e.yaml down -v` -- expected: teardown after the run

**Manual checks (if no CLI):**
- Bring up the dev stack (`docker compose -f lineworker/deploy/compose.yaml up -d --build --wait`), log in as Kaya Johnson, and compare the left pane against the prototype's queue at `docs/Workers_Comp_Prototype.html`: four collapsible groups with 📥 🔍 🩺 ✅ and count chips, mono claim IDs with 🔺 on the top few, risk dot, FRAUD/LITIG/PAY DUE/SIU badges on the token colours, stage pill and employer short name — in the light palette, not the prototype's dark one.

## Auto Run Result

Status: `done`. One review pass, no spec loopback, no blocking condition.

### What was implemented

Story 2.1 turns `/workspace` from a stub into the handler's working surface, and lands the rules tier the whole rest of the project was waiting on.

`server/rules/` was an empty package and `zen-engine` was not a dependency. This story built the tier: a `rule_document` table (key + version + `effective_from` + JSONB content, `SELECT`-only to the app role), a load-by-key wrapper that picks the highest version effective on a date and caches both compilation and evaluation, and the first two committed JDM documents — `derivation_thresholds` and `priority_weights`. Each document is evaluated **once per request** and returns a typed parameter block; the arithmetic stays in Python. That is AD-8's split made concrete, and it keeps the scorer property-testable.

On top of it: four new registered derivations (`days_open`, `siu_review`, `rtw_blocked`, `payment_due`), the exactly-once `priority_score` and `priority_markers` in `services/worklist/priority.py`, and `GET /api/claims/queue` returning four stage groups — each a real `{items, nextCursor, total}` page — filtered, ranked, and marked server-side. The SPA gained a 3-pane workspace with a collapsible, filterable queue whose selection lives in the URL.

Three rulings worth naming. **The risk bands moved out of `Settings`** into the JDM document, completing the `TODO(JDM)` Story 1.4 left; `RISK_HIGH_MIN` is no longer an env knob, and changing a band is now a document version. The SLA targets deliberately stayed put. **`days_open` is derived from `froi_date`**, because the prototype's static `daysOpen` matches no date in the record — checked against `doi`, `froi_date`, `actual_rtw`, `rtw_rec` and `settlement_days`. And **paging cannot be keyset**: the sort key is Python arithmetic over a JDM parameter block, so the cursor records every input that shaped the ordering (filter, stage, offset, `asOf`, limit, and both document versions) and refuses anything that disagrees, rather than serving a page cut from a different list.

### Files changed

**New — server:** the two JDM documents under `rules/documents/`; `rules/engine.py` (load-by-key, effective-dating, caching) and `rules/parameters.py` (the single typed JSON→Python boundary); migrations `0008_rule_document` and `0009_seed_rule_documents`; `services/derivations/open_duration.py` and `queue_flags.py`; `services/worklist/priority.py` and `queue.py`; `api/routers/claims.py`; tests `test_rules_engine.py`, `test_priority_score.py`, `test_claims_queue.py`.

**New — web / e2e:** `api/claims.ts`; `features/queue/{QueuePane,StageGroup,ClaimCard,FilterSelect}.tsx` and `useSelectedClaim.ts`; tests `ClaimCard.test.tsx`, `QueuePane.test.tsx`, `WorkspaceShell.test.tsx`, and `noDerivation.test.ts` (the AC-2 guard); `e2e/stories/2-1-prioritized-filterable-claim-queue.spec.ts`.

**Changed:** `config.py` (bands removed), `services/derivations/{registry,risk_band,__init__}.py` (built from thresholds, not settings), `services/worklist/{stats,__init__}.py`, `data/models/core.py` (+`RuleDocument`), `data/repositories/claims.py` (+`select_queue_rows`), `api/{app,routers/__init__,routers/stats}.py`, `pyproject.toml`/`uv.lock` (pinned `zen-engine==0.53.0`), `tests/{seed_fixture,test_derivations}.py`, `deploy/{.env.example,compose.yaml}`, `.github/workflows/ci.yaml`, `web/src/api/{queryKeys,schema.d.ts}`, `web/src/features/shell/WorkspaceShell.tsx`, `web/src/test/{api-mock,setup}.ts`, `e2e/fixtures/{login,seed}.ts`.

### Review findings

19 patches applied, 6 deferred, 5 rejected, 0 intent gaps, 0 spec loopbacks. The full breakdown is in the Review Triage Log above; the deferred items are in [deferred-work.md](deferred-work.md).

The one high-severity finding: the cursor recorded only the priority-weights version, while the derivation thresholds, the resolved date and the page size each also shape the ordering — so a page-2 request cut under any changed input was accepted verbatim and could silently skip or repeat claims. Silent omission is the wrong failure mode for a claims console, so the cursor now records all four and refuses a mismatch.

The most instructive finding was a test that asserted nothing: `test_a_filter_that_matches_nothing_still_returns_four_empty_groups` used a persona who *has* a fraud claim, so its guard never fired. Its premise is now a hard assertion.

### Verification performed

Every gate below was re-run by the orchestrator after the patches, not only by the implementing agent:

- `ruff check` / `ruff format --check` / `mypy` — clean across 80 files
- `uv run pytest` without a database — 145 passed, 134 skipped
- `uv run pytest` against a disposable `pgvector/pgvector:pg18` — **279 passed**
- `alembic upgrade head` + `alembic check` — clean chain, no model/migration drift
- `npm run generate:api` — regeneration is idempotent (byte-identical `schema.d.ts`), so CI's stale-client diff will pass
- `npm run lint` (0 errors), `npm run typecheck`, `npm test` — **103 passed across 9 files**
- `cd e2e && npm run typecheck` — clean
- Playwright `--grep "@story:2-1\b|@smoke\b"` against a freshly built and reset e2e stack — **16 passed**, including the 9 tests of the new story spec. This is the AD-15 done-gate.

### Residual risks

- **Paging is exercised, but not through the SPA.** The largest seeded handler group is 26 against a page limit of 50, so "Show more" cannot appear in the e2e stack. The cursor round trip and its 400s are now covered by driving the API through the logged-in browser session with `limit=5`, and by pytest — but the React accumulation path itself is only under Vitest.
- **The queue scores the caller's whole scoped portfolio on every request**, page-2 requests included, because the sort key is not expressible in SQL. Correct and fast at 100 claims; deferred as the first thing to revisit if a portfolio grows.
- **A missing or invalid rule document is a 500**, and it takes `/stats/topbar` down with it. Deferred deliberately: the documents are seeded by migration, so their absence means a broken deployment, and choosing the right degraded shape spans every future JDM consumer.
- **DB-backed tests run as the migration owner**, so the `SELECT`-only grant on `rule_document` is asserted by reading `information_schema` rather than by serving a request as `lineworker_app`. Only the e2e stack exercises the app role. Pre-existing pattern, deferred.
- **Both queue oracles resolve "today" independently of the server**, so a run straddling midnight UTC would disagree by a day on every score and ordering expectation. A few seconds of exposure per day.
