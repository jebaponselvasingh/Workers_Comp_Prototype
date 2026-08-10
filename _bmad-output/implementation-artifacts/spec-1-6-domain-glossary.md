---
title: 'Story 1.6: Domain Glossary'
type: 'feature'
created: '2026-08-10'
baseline_revision: '9291f8364dcbe51372348241b5e01758742359d1'
final_revision: 'd3dba378d00841daebef9dfae6ccb4c011fee5df'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: true # 12 patches across schema, API semantics, UI empty-state, and a11y — breadth and volume warrant an independent look
context:
  - '{project-root}/_bmad-output/implementation-artifacts/1-6-domain-glossary.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
warnings: [oversized] # cross-layer story (migration + API + UI + three test suites) kept in one file per the template's cohesion rule
---

<intent-contract>

## Intent

**Problem:** The top bar's 📖 Glossary button is an inert seam Story 1.4 shipped disabled, so the 25 WC domain terms that make this console readable to a new handler exist only inside the prototype HTML. FR-GLOS-1 needs them seeded, served, and searchable from every role's shell.

**Approach:** Add `glossary_term` as read-only reference data (Alembic-created, migration-seeded from the prototype's `GLOSS` array verbatim), serve it from an auth-required `GET /api/glossary` in the standard list envelope, and render a right-hand slide-in panel with live client-side search that the existing Glossary button opens.

## Boundaries & Constraints

**Always:**
- The prototype file `docs/Workers_Comp_Prototype.html` is the data contract: seed **all 25** `GLOSS` entries with **verbatim** text (`a`→abbreviation, `t`→term, `d`→definition), preserving prototype display order. FR-GLOS-1's "24 terms" is a documented miscount — same ruling precedent as Story 1.1's design tokens.
- Reference data is read-only to the application: no `version` column, no audit wiring, no write commands or endpoints, and the only DB grant to `lineworker_app` is `SELECT`.
- Seed text lives in a JSON data file extracted by a script, never hand-typed into Python and never inline in a component. Test and e2e expectations derive counts and text from that same file (seed-is-the-fixture doctrine), never hardcoded.
- The endpoint returns `{items, nextCursor, total}` with camelCase fields via `ApiModel`, is protected by the app-level auth dependency (not added to `PUBLIC_PATHS`), and takes no parameters.
- Search filters **client-side** over the cached full list — the sanctioned AD-7 exception, valid only because glossary terms are global reference data with no employer scope and no PHI. Do not add a server search parameter; do not reuse this pattern for claim data.
- The Glossary button lives in the shared top bar, so enabling it must serve every role identically. Story 1.4's seam assertions (vitest + the 1-4 e2e spec) are **re-pointed, not deleted** — the precedent Story 1.5 set.
- Loading and error states are explicit (NFR-3): never an empty void, never invented content.

**Block If:**
- The Excel element mapping (`docs/WC_Feature_Element_Details.xlsx`) demands column names or a term set that contradicts the prototype's 25 entries.
- Anything argues for making `/glossary` pre-auth (a `PUBLIC_PATHS` entry is a security decision, not a dev call).

**Never:** glossary authoring/admin UI; a `glossary_term`↔`claim` join table (the ERD's dotted association has no story — do not invent it); the RTW-letter half of FR-H-11 (Story 6.5); server-side search/pagination machinery; any change to the SLA strip or stat tiles.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Panel opens | Authenticated user clicks 📖 Glossary | Panel slides in over a backdrop; all 25 terms listed in prototype order; search box empty and focused | No error expected |
| Live search — abbreviation | Query `havs` | Only Hand-Arm Vibration Syndrome listed (case-insensitive) | No error expected |
| Live search — term | Query `maximum medical` | Only MMI listed | No error expected |
| Live search — definition only | Query text appearing solely in a definition (e.g. `audiogram`) | Only NIHL listed — proves definition-text search | No error expected |
| No matches | Query `zzzz` | Muted `No matches for "zzzz".` echoing the query verbatim | No error expected |
| Clear query | Query cleared to `""` | Full 25-term list restored | No error expected |
| Reopen after searching | Panel closed with a query active, then reopened | List unfiltered and search box empty (prototype calls `renderGloss("")` on open) | No error expected |
| Close | ✕ clicked, or backdrop clicked | Panel dismissed; focus returns to the Glossary button | No error expected |
| Terms in flight | `GET /api/glossary` pending | Skeleton rows inside the open panel | No error expected |
| Fetch fails | `GET /api/glossary` returns 404/500 | Inline `role="status"` error message inside the panel; no term rows, no fabricated content | Rendered branch, not a thrown boundary |
| Unauthenticated | No/expired session cookie | `401` `application/problem+json` from the app-level auth dependency | Inherited from Story 1.3 — asserted here, not reimplemented |

</intent-contract>

## Code Map

- `docs/Workers_Comp_Prototype.html` -- lines ~1772–1798 `GLOSS` (25 entries, the data contract); ~1799 `renderGloss` (the search predicate + no-match copy); ~262–274 panel CSS; ~623–628 `#glov` markup; ~985–986 open/backdrop wiring
- `lineworker/server/data/seed/extract_prototype_seed.py` -- Story 1.2's sanctioned extractor; the pattern the glossary extractor mirrors (AD-12)
- `lineworker/server/data/models/core.py` -- declarative style (`Mapped[...]`/`mapped_column`, `Identity()` PK, `Text`); `AuditEvent`/`Session` are the "no version column" precedent
- `lineworker/server/data/models/__init__.py` -- must export the new model; `data/env.py` builds `target_metadata` through it, so `alembic check` depends on it
- `lineworker/server/data/versions/20260810_0003_core_schema.py` -- `op.create_table` + explicit `op.f(...)` constraint names + per-object GRANT block (AD-4 minimal verbs)
- `lineworker/server/data/versions/20260810_0004_seed_portfolio.py` -- seed-migration pattern: read JSON, `sa.Table(..., autoload_with=bind)`, insert; head is `0005_session`
- `lineworker/server/data/repositories/identity.py` -- the sanctioned **unscoped** repository carve-out and the docstring that justifies taking no `CallerContext`
- `lineworker/server/api/routers/auth.py` -- `PersonaList` = the `{items, nextCursor, total}` envelope shape; `UNAUTHENTICATED_RESPONSE`
- `lineworker/server/api/routers/stats.py` -- thin-router pattern; note it sets `Cache-Control: no-store` because its payload is persona-specific — the glossary is not
- `lineworker/server/api/app.py`, `api/routers/__init__.py`, `api/deps.py` -- router registration; auth is app-level so a new router is protected by default (`PUBLIC_PATHS` untouched)
- `lineworker/server/tests/test_schema_seed.py` -- `app_engine` fixture asserting grants as `lineworker_app`; the model for the SELECT-yes/INSERT-no test
- `lineworker/web/src/components/ui/dialog.tsx` -- house shadcn style: `import { Dialog as DialogPrimitive } from "radix-ui"`, `data-slot`, no `dark:` variants, function components
- `lineworker/web/src/features/shell/SlaStrip.tsx` -- component conventions: own query hook, skeleton/error/em-dash states, `data-testid` stems
- `lineworker/web/src/features/shell/TopBar.tsx` -- lines ~14–20 (seam docstring) and ~138–148 (the disabled button) are what this story flips
- `lineworker/web/src/api/{client,queryKeys,stats}.ts` -- `api.GET("/glossary")` idiom, key registry, `useQuery` shape
- `lineworker/web/src/test/api-mock.ts` -- `StubRoutes` needs a `glossary` field and a `/api/glossary` branch
- `lineworker/e2e/fixtures/{seed,login,selectors,test}.ts` -- oracle doctrine, `PERSONAS`, role-first selector policy, the custom `test` export
- `lineworker/e2e/stories/1-4-scoped-top-bar-caseload-stats.spec.ts` -- line ~117 asserts the button is disabled; re-point it
- `.github/workflows/ci.yaml` -- the `migrations` job lists DB-backed test files explicitly; the `web` job diffs the regenerated `schema.d.ts`

## Tasks & Acceptance

**Execution:**
- [x] `lineworker/server/data/seed/extract_prototype_glossary.py` -- new sibling extractor parsing `const GLOSS=[…]` out of the prototype and writing `glossary_terms.json` (`abbreviation`, `term`, `definition`, `sort_order` = array index) -- a separate script rather than a mode of 1.2's extractor so `seed_data.json` is not rewritten by an unrelated story
- [x] `lineworker/server/data/seed/glossary_terms.json` -- generated, committed; the single source both the seed migration and the test/e2e oracles read
- [x] `lineworker/server/data/models/core.py` + `data/models/__init__.py` -- add `GlossaryTerm` (`id` Identity PK, `abbreviation`/`term`/`definition` `Text`, `sort_order` `Integer` unique); class docstring records why there is no `version` column and no audit wiring
- [x] `lineworker/server/data/versions/20260810_0006_glossary_term.py` -- create the table (`revision = "0006_glossary_term"`, revises `0005_session`); grant exactly `GRANT SELECT ON glossary_term TO lineworker_app` -- AD-4 grants the verbs the app uses and no more
- [x] `lineworker/server/data/versions/20260810_0007_seed_glossary.py` -- load `glossary_terms.json` (`revision = "0007_seed_glossary"`), mirroring 0004's read-JSON-and-insert shape; keeps schema and data in separate revisions as 0003/0004 do
- [x] `lineworker/server/data/repositories/glossary.py` + `repositories/__init__.py` -- `list_terms(db)` ordered by `sort_order`; module docstring states why it takes no `CallerContext` (no employer scope, no PHI) so the omission reads as a decision, not the defect AD-7 exists to catch
- [x] `lineworker/server/api/routers/glossary.py` + `api/routers/__init__.py` + `api/app.py` -- `GET /glossary` returning the envelope; no parameters; `responses=UNAUTHENTICATED_RESPONSE`; comment why it does **not** set `Cache-Control: no-store` (identical for every caller, unlike `/stats/*`)
- [x] `lineworker/server/tests/test_glossary.py` -- DB-backed: seeded row count and text equal the JSON file, endpoint returns every term in prototype order with camelCase keys, unauthenticated request is 401 problem+json, and `lineworker_app` can SELECT but not INSERT (grant asserted, not assumed)
- [x] `.github/workflows/ci.yaml` -- add `tests/test_glossary.py` to the `migrations` job's explicit list and update its per-story comment, or it silently skips
- [x] `lineworker/web/src/components/ui/sheet.tsx` -- vendor shadcn Sheet, rewritten to `import { Dialog as SheetPrimitive } from "radix-ui"` per house style; no new dependency
- [x] `lineworker/web/src/api/glossary.ts` + `api/queryKeys.ts` -- `useGlossary()` on `queryKeys.glossary`, fetched on first open and cached (reference data: long/infinite `staleTime`)
- [x] `lineworker/web/src/features/glossary/GlossaryPanel.tsx` -- the panel: header, ✕ close, autofocused search input, scrollable rows (term + abbreviation chip + muted definition, hairline dividers), skeleton/error/no-match states; existing design tokens only, no hardcoded hex
- [x] `lineworker/web/src/features/shell/TopBar.tsx` -- enable the button, drop the Story 1.6 tooltip and the seam paragraph in the file docstring, own the open/close state, render the panel
- [x] `lineworker/web/src/features/glossary/GlossaryPanel.test.tsx` -- the search predicate across each of the three fields, no-match state with the query echoed, clear-restores, reopen-resets, ✕ and backdrop close, focus in/out, loading and error branches (use a non-retryable 404 so the assertion does not race the retry policy)
- [x] `lineworker/web/src/features/shell/TopBar.test.tsx` -- re-point the seam test: the button is enabled and opens the panel
- [x] `lineworker/web/src/test/api-mock.ts` -- `glossary` route + fixtures derived from the seed JSON's shape
- [x] `lineworker/web/src/api/schema.d.ts` -- regenerate via `npm run generate:api` and commit, or the `web` CI job fails on the diff
- [x] `lineworker/e2e/fixtures/seed.ts` -- export a glossary oracle reading `glossary_terms.json`, so the spec asserts a derived count rather than a literal 25
- [x] `lineworker/e2e/stories/1-6-domain-glossary.spec.ts` -- `test.describe("@story:1-6 @epic:1 domain glossary", …)`, one `@smoke` test; open → terms listed; abbreviation search; definition-only search; gibberish → no-match; close via ✕; reopen and close via backdrop; asserted for a handler **and** a supervisor
- [x] `lineworker/e2e/stories/1-4-scoped-top-bar-caseload-stats.spec.ts` -- re-point line ~117 from `toBeDisabled()` to the filled seam, keeping the comment's point about the shared bar
- [x] `_bmad-output/implementation-artifacts/1-6-domain-glossary.md` + `sprint-status.yaml` -- fill the Dev Agent Record (model, debug log, completion notes, file list, change log) and move the story to `review`

**Acceptance Criteria:**
- Given a fresh `alembic upgrade head`, when the database is inspected, then `glossary_term` holds exactly the entries in `glossary_terms.json` with text byte-identical to the prototype's `GLOSS`, and `lineworker_app` holds `SELECT` on it and no other verb.
- Given any authenticated persona of any role, when `GET /api/glossary` is called, then every seeded term is returned in prototype display order inside `{items, nextCursor, total}` with camelCase fields; given no session, then the response is `401` `application/problem+json`.
- Given a logged-in handler and, separately, a logged-in supervisor, when the 📖 Glossary button is clicked, then the slide-in panel opens over a backdrop and lists every seeded term — the button is enabled for every role, from the shared top bar.
- Given the open panel, when the panel is dismissed by ✕ or by clicking the backdrop, then the panel closes and keyboard focus returns to the Glossary button.
- Given the full test gate, when it runs, then `ruff`/`ruff format --check`/`mypy` are clean, pytest and vitest pass with the new tests, `alembic check` reports no model/migration drift, the regenerated `schema.d.ts` is byte-identical to the committed one, and the Playwright suite — including `@story:1-6` and the `@smoke` set — is green against a freshly reset e2e stack.

## Spec Change Log

### 2026-08-10 — Documentation correction (no loopback, no code reverted)

- **Triggering finding:** Blind Hunter reported that the implementation trims the search query while both the component docstring and this spec's Design Notes asserted the predicate was the prototype's "exactly" — and that the e2e oracle, introduced as an *independent* restatement of the rule, reproduced the trim rather than the prototype's behaviour, so it agreed with the code under test in precisely the case the doctrine exists to catch.
- **What was amended:** the Design Notes sentence now states the trim as a named deviation and says the no-match echo stays untrimmed.
- **Known-bad state avoided:** a spec that documents a rule the code does not implement, which the next author would either "fix" back to the prototype (breaking pasted queries) or copy forward as true.
- **Why this is not a `bad_spec` loopback:** the root cause was a false claim in prose, not an under-specified requirement. The code already implements the corrected statement, no code was reverted, and `review_loop_iteration` is unchanged. The finding was triaged and fixed as a patch (P5); this entry records the accompanying prose correction so the amendment is auditable rather than silent.
- **KEEP:** the trim behaviour itself, the untrimmed echo, and the vitest cases pinning both (whitespace-only query → full list; `"havs "` → one match).

## Review Triage Log

### 2026-08-10 — Review pass

- intent_gap: 0
- bad_spec: 0
- patch: 12: (high 0, medium 4, low 8)
- defer: 2: (high 0, medium 1, low 1)
- reject: 5: (high 0, medium 0, low 5)
- addressed_findings:
  - `[medium]` `[patch]` An empty successful payload rendered as `No matches for "<query>".`, teaching a handler that a term does not exist when in fact the glossary was empty (0006 applied without 0007, a wiped table) — the exact failure NFR-3 and the error branch exist to prevent. Now branches on `terms.length === 0` before the query, with distinct test ids for the two states so a test can tell them apart; both covered by vitest.
  - `[medium]` `[patch]` `key={term.abbreviation}` rested on a uniqueness nothing enforced — `sort_order` was the only unique column and the wire payload deliberately withholds `id` and `sortOrder`, leaving the client no stable key. Fixed at the source: `unique=True` on the model plus `uq_glossary_term_abbreviation` in the unshipped `0006` (no new revision), and the quirk test now asserts a row list instead of a dict that could silently collapse duplicates.
  - `[medium]` `[patch]` Nothing tied the seeded rows back to the prototype: migration, pytest, `seed_fixture` and the e2e oracle all read the same JSON, so a truncated regeneration passed all 39 tests while AC 1's "verbatim from `GLOSS`" was asserted only in prose. Added `tests/test_glossary_seed_file.py`, which scans `const GLOSS=[…]` out of the prototype with its own parser (not the extractor's regex) and asserts the JSON matches entry-for-entry, field-for-field, in order.
  - `[medium]` `[patch]` Live filtering was silent to assistive technology — no live region, no `aria-busy`, and the error used polite `role="status"` for a message that replaces all content. Added a polite `sr-only` result-count announcement, `aria-busy` while pending, and `role="alert"` on the error.
  - `[low]` `[patch]` The predicate was documented as the prototype's "unchanged" while adding a `trim()`, and the e2e oracle reproduced the deviation under a comment claiming independence. Trim kept as the better behaviour, now named as a deviation in the code, the oracle comment, this spec, and pinned by vitest.
  - `[low]` `[patch]` The new extractor's documented invocation and default argument both pointed at `../docs/…`, which from `server/` resolves to a path that does not exist. Corrected to `../../docs/…` and given an existence check that exits with the resolved path.
  - `[low]` `[patch]` The extractor's integrity guard was a `python -O`-strippable `assert` counting every `{` in the array rather than entry boundaries — so it could both false-positive on a definition containing a brace and vanish exactly when a regex miss would silently drop a term. Replaced with a non-strippable `SystemExit` on a real entry count, and the JS-literal decoding now names the offending entry instead of raising a bare column offset.
  - `[low]` `[patch]` Migration `0007` inserted the seed payload unchecked, so a drifted extractor would abort the upgrade with a NOT NULL violation and leave the table created-but-empty at `0006`. Added a non-empty and key-set guard that fails with the seed file named.
  - `[low]` `[patch]` The "no `Cache-Control`" assertion locked in the wrong invariant — it read as "this endpoint must never be `no-store`" rather than "this payload is identical for every caller and PHI-free". Renamed and documented so a future story adding a caller-specific field changes it deliberately instead of reading the failure as a regression.
  - `[low]` `[patch]` Both new oracles returned their internal mutable collection, so one in-place sort by a future caller would corrupt expectations for every later test in the same worker or module, surfacing in an unrelated file. Both now return copies.
  - `[low]` `[patch]` The implementation cited an "AC 4" the story does not have (focus return is a Task 3 sub-bullet) in five places, and the story record claimed a migration round trip more strongly than what was verified. Both corrected.
  - `[low]` `[patch]` Filling the seam removed the button's `title` and its `disabled:opacity-60` along with the `disabled` attribute, and the matching assertion was deleted rather than re-pointed — leaving the only affordance in the bar with no tooltip and no distinguishable disabled state. Both restored, assertion re-pointed.

## Design Notes

**Why `sort_order` rather than ordering by `term`.** The AC says prototype display order, and that order is deliberate (FNOL first, the acronym cluster, then the manufacturing-specific hazards). An `ORDER BY term` would scatter it. The column also gives the repository a deterministic sort, which `identity.py` already states as a rule so a future paged version cannot silently repeat rows.

**Why two migrations.** 0003 creates the core schema and 0004 seeds it; splitting the glossary the same way keeps `downgrade()` honest — dropping the table takes its rows with it, and re-seeding is a revision of its own rather than an edit to a structural migration.

**The search predicate is the prototype's, with one named deviation.** Case-insensitive substring across abbreviation OR term OR definition, and an empty query matches everything. The one difference from `renderGloss`: the query is trimmed before matching, so a trailing space pasted in with a term still finds it and a whitespace-only query shows the full list rather than the no-match state. The echo in the no-match line stays untrimmed — it must show what was actually typed.

```ts
const q = query.trim().toLowerCase();
const shown = q
  ? terms.filter((t) =>
      t.abbreviation.toLowerCase().includes(q) ||
      t.term.toLowerCase().includes(q) ||
      t.definition.toLowerCase().includes(q))
  : terms;
```

**Two data quirks to preserve, not clean up.** "OSHA 300" and "Arc Flash" have multi-word abbreviations, and "Apportionment" has an abbreviation identical to its term. The row renders fine; deduping or normalising either would edit the contract.

## Verification

**Commands:**
- `cd lineworker/server && uv run ruff check . && uv run ruff format --check . && uv run mypy .` -- expected: clean
- `cd lineworker/server && uv run pytest` -- expected: all pass, no new skips beyond the DB-gated ones
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:pw@127.0.0.1:55432/lineworker APP_DB_PASSWORD=lineworker_app_dev uv run pytest tests/test_glossary.py` -- against a disposable `pgvector/pgvector:pg18` on :55432 (the pattern Story 1.5 used) -- expected: pass, including the grant assertions
- `cd lineworker/server && uv run alembic check` -- expected: no model/migration drift
- `cd lineworker/web && npm run lint && npm run typecheck && npm test` -- expected: clean; new glossary tests pass
- `cd lineworker/web && npm run generate:api && git diff --exit-code -- src/api/schema.d.ts` -- expected: no diff after committing the regenerated client
- `docker compose -f lineworker/deploy/compose.e2e.yaml up -d --build --wait && cd lineworker/e2e && npm test && npm run smoke` -- expected: full suite green including `@story:1-6`; then `npx playwright test --grep @epic:1` for the Epic 1 close-out run
- `docker compose -f lineworker/deploy/compose.yaml up -d --build --wait` then `curl -i localhost:8080/api/glossary` -- expected: `401` problem+json unauthenticated; the full 25 terms once a session cookie is presented

**Manual checks (if no CLI):**
- Open the dev stack at `localhost:8080`, log in as a handler and again as a supervisor, and compare the rendered panel against the prototype's `#glov` overlay: right-hand slide-in, term name with abbreviation chip, muted definition, hairline dividers, and the no-match copy echoing the query.

## Auto Run Result

Status: `done`. One review pass, no loopbacks.

### What was implemented

Story 1.6 fills the last seam in the Epic 1 top bar. `glossary_term` arrives as read-only reference data — created by migration `0006`, seeded by `0007` from a committed JSON file that a new extractor produces from the prototype's `GLOSS` array — and is served by an auth-required, parameter-free `GET /api/glossary` in the standard list envelope. The 📖 Glossary button Story 1.4 shipped disabled now opens a right-hand slide-in panel with live search across abbreviation, term and definition text, closing by ✕ or backdrop with focus returning to the button.

The two rulings worth naming: **all 25** prototype entries are seeded, not the 24 FR-GLOS-1 claims — the prototype file is the contract, the same precedent Story 1.1 set for design tokens. And the search runs **client-side**, which is a sanctioned exception to AD-7 rather than a shortcut: glossary terms carry no employer scope and no PHI, every authenticated caller gets byte-identical rows, so narrowing text the caller already holds is rendering, not a permission decision moved into the browser. It does not generalise to claim data, and the code says so where someone would be tempted to copy it.

### Files changed

**New — server**
- `lineworker/server/data/seed/extract_prototype_glossary.py` — sibling to Story 1.2's extractor; parses `GLOSS` and writes the seed JSON. A separate script rather than a mode of the existing one, so `seed_data.json` is not rewritten by an unrelated story.
- `lineworker/server/data/seed/glossary_terms.json` — the single source the migration, pytest, `seed_fixture` and the e2e oracle all read.
- `lineworker/server/data/versions/20260810_0006_glossary_term.py` — the table; `GRANT SELECT` and nothing else.
- `lineworker/server/data/versions/20260810_0007_seed_glossary.py` — the rows, with a shape guard.
- `lineworker/server/data/repositories/glossary.py` — the second sanctioned unscoped repository, with the docstring that says why it takes no caller context.
- `lineworker/server/api/routers/glossary.py` — the endpoint, including the documented decision not to send `Cache-Control`.
- `lineworker/server/tests/test_glossary.py` — DB-backed: seed fidelity, per-persona endpoint reads, 401, and the SELECT-yes/INSERT-no grant.
- `lineworker/server/tests/test_glossary_seed_file.py` — ties the committed JSON back to the prototype; the only test that can catch a bad regeneration.

**New — web / e2e**
- `lineworker/web/src/components/ui/sheet.tsx` — vendored shadcn Sheet, rewritten onto the unified `radix-ui` import; no new dependency.
- `lineworker/web/src/api/glossary.ts` — `useGlossary(enabled)`, gated so the fetch happens on first open.
- `lineworker/web/src/features/glossary/GlossaryPanel.tsx` + `.test.tsx` — the panel and its 14 cases.
- `lineworker/e2e/stories/1-6-domain-glossary.spec.ts` — 10 specs, `@story:1-6 @epic:1`, one `@smoke`.

**Modified** — `data/models/core.py` and `models/__init__.py` (the `GlossaryTerm` model); `repositories/__init__.py`, `api/routers/__init__.py`, `api/app.py` (registration); `tests/seed_fixture.py` (glossary oracle); `.github/workflows/ci.yaml` (both new DB-backed files in the migrations job); `web/src/api/queryKeys.ts`, `schema.d.ts` (regenerated), `test/api-mock.ts`; `web/src/features/shell/TopBar.tsx` + `TopBar.test.tsx` and `e2e/stories/1-4-…spec.ts` (Story 1.4's seam assertions re-pointed, not deleted); `e2e/fixtures/seed.ts` (oracle).

### Review findings

12 patches applied (4 medium, 8 low), 2 deferred, 5 rejected, 0 intent gaps, 0 spec defects. The four that mattered: an empty glossary reported itself to the user as "no matches for your query"; the React list key rested on a column the database allowed to repeat; nothing tied the seeded rows back to the prototype, so a truncated regeneration would have passed all 39 tests; and live filtering was silent to assistive technology. Full breakdown in the Review Triage Log above; the two deferrals are in `deferred-work.md`.

### Verification

Every command below was run by the orchestrator after the patches, not only by the implementing agent.

- `ruff check` + `ruff format --check` clean (68 files); `mypy` strict clean (68 files).
- pytest **177 passed** against a disposable `pgvector/pgvector:pg18`; **89 passed / 88 skipped** without one, which is the CI server job's shape.
- `alembic check` — no model/migration drift, including after the new unique constraint.
- `npm run generate:api` — `schema.d.ts` regenerates byte-identically.
- eslint 0 errors (2 pre-existing warnings in vendored `badge.tsx`/`button.tsx`); `tsc --noEmit` clean in `web` and `e2e`; vitest **61 passed**.
- Playwright **40/40** and `@smoke` **7/7** against a freshly built and reset e2e stack — run twice, before and after the review patches. Since every spec in the suite is `@epic:1`, that 40/40 is also the Epic 1 close-out run the story asks for.
- The seed file was independently proven reproducible: re-running the extractor against the prototype produced a byte-identical `glossary_terms.json`.

### Residual risks

- **The dev stack curl was not run.** All live verification went through the e2e stack on :8081. Both stacks serve the same image through the same nginx config, so the gap is procedural rather than substantive, but the spec listed a dev-stack check and it was not separately executed.
- **CI itself has not run.** The four jobs were reproduced locally command-for-command; nothing has been pushed.
- **The empty-glossary branch and the screen-reader announcement are vitest-only.** Both are reachable in a browser only by wiping the table or by using assistive technology, so the e2e suite does not cover them — consistent with how the loading and error branches are covered, but worth knowing.
- **Epic 1 is not closed.** Stories 1.4 and 1.5 are still at `review`; 1.6 joins them. The epic's own close-out (all six stories `done`, retrospective) is a separate decision.
</content>
</invoke>
