# Story 1.6: Domain Glossary

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a console user,
I want a searchable WC glossary,
so that domain terms are one click away everywhere.

## Acceptance Criteria

1. **Given** the 📖 Glossary button, **when** clicked, **then** a slide-in panel lists the seeded terms from `glossary_term` reference data (the prototype's full `GLOSS` list — 25 terms; the epics' "24" is a documented miscount, see Dev Notes), closing via ✕ or backdrop click.
2. **Given** a search query, **when** typed, **then** terms filter live by abbreviation, term, or definition text, and a "no matches" state shows when nothing matches (FR-GLOS-1).

## Tasks / Subtasks

- [ ] Task 1: `glossary_term` reference data (AC: 1)
  - [ ] Alembic revision creating `glossary_term` (surrogate int PK; columns per the Excel mapping — abbreviation, term, definition; plus a stable sort order if the Excel calls for one). Reference data, read-only to the app: no `version` column, no audit wiring, no write commands or endpoints
  - [ ] Seed migration loading the prototype's `GLOSS` array (`docs/Workers_Comp_Prototype.html` lines ~1772–1798) — **all 25 entries, verbatim text** (FNOL, TTD, PPD, PTD, MMI, IME, AWW, RTW, CTS, HAVS, NIHL, OSHA 300, FROI, ICD-10, Reserve, Subro, PPE, EMG, ORIF, Arc Flash, SLA, LTD, WPI, Apportionment, VR); data source only, never code
- [ ] Task 2: Glossary endpoint (AC: 1)
  - [ ] `GET /api/glossary` (auth-required): full term list in the `{items, nextCursor, total?}` list envelope per convention (25 rows — a single page; cursor mechanics may be trivial but the envelope shape holds), camelCase fields, prototype display order
  - [ ] pytest: endpoint returns all seeded terms; unauthenticated request → 401 problem+json (inherited from 1.3's handler — assert it holds here)
- [ ] Task 3: Slide-in panel UI — UX-DR10 (AC: 1, 2)
  - [ ] `web/src/features/glossary/` (or beside the top-bar chrome): right-hand slide-in panel over a backdrop (shadcn/ui Sheet fits) — header "WC Glossary" + ✕ close, search input, scrollable term list
  - [ ] Term row per prototype: term name with abbreviation chip, definition below — dense card styling with the 1.1 tokens (light palette per the 1.1 ruling)
  - [ ] Open via the top-bar 📖 Glossary button — **enable the disabled seam Story 1.4 left**, removing its tooltip; close via ✕ AND backdrop click (both under test); focus moves into the panel on open, returns to the button on close (Sheet gives this — verify)
  - [ ] Data via TanStack Query (`queryKeys.glossary`), fetched on first open and cached; loading + error states (NFR-3)
- [ ] Task 4: Live search + no-match state (AC: 2)
  - [ ] Search filters as the user types, case-insensitive substring across **abbreviation OR term OR definition** (the prototype's `renderGloss` predicate exactly)
  - [ ] Filtering runs client-side over the cached full list — sanctioned scope-free reference-data exception, see Dev Notes; no per-keystroke server round-trips
  - [ ] No-match state per prototype: muted `No matches for "query".` echoing the query — never an empty void (NFR-3)
  - [ ] Clearing the query restores the full list; panel reopen resets to unfiltered (prototype calls `renderGloss("")` on open)
  - [ ] Vitest: predicate matches on each of the three fields; no-match state renders with the query echoed; clear restores
- [ ] Task 5: E2E story spec (AC: 1, 2)
  - [ ] `e2e/stories/1-6-domain-glossary.spec.ts` tagged `@story:1-6 @epic:1`, using the login fixture (any persona — the glossary is role-independent chrome; assert it opens for a handler AND a supervisor to prove "everywhere")
  - [ ] Open panel → terms listed; type an abbreviation (e.g. "HAVS") → filtered hit; type a definition-only word → hit (proves definition-text search); type gibberish → no-match state; close via ✕; reopen and close via backdrop click
  - [ ] One `@smoke` happy path: login → open glossary → search "MMI" → "Maximum Medical Improvement" visible

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

The last Epic 1 story: a read-only reference-data feature that completes the top bar. NOT here: any glossary authoring/admin (none exists anywhere in scope), claim-linked glossary behavior (the ERD's dotted `GLOSSARY_TERM }o--o{ CLAIM` association has no story — do not invent a join table), or the RTW-letter half of FR-H-11 (Epic 6, Story 6.5). The glossary is the second half of FR-H-11 and all of FR-GLOS-1; epics.md documents this split.

⚠️ **Documented discrepancy — term count:** FR-GLOS-1 and the epics AC say "24 domain terms", but the prototype's `GLOSS` array — the design contract — contains **25 entries** (verified by count of lines ~1773–1797). Same ruling as the 1.1 design-token discrepancy: **the prototype file is the contract; seed all 25.** The AC above is adjusted accordingly (precedent: Story 1.1 adjusted UX-DR12's "dark" wording the same way). If the count matters to anyone, that's a change request, not dev discretion.

### Architecture compliance (binding ADs for this story)

- **Capability map:** Glossary = "`glossary_term` reference data + `web/`, governed by conventions" — deliberately the lightest-governance row in the whole map. No AD-4 (no writes), no AD-12 owner beyond the seed migration, no derivations.
- **AD-7 note — the sanctioned client-filter exception:** glossary terms are global reference data with **no employer scope and no PHI**; there is nothing to scope-enforce beyond the auth requirement. Client-side live filtering of the fetched list therefore does not violate AD-1's "every filter-by-permission happens behind FastAPI" — this is text search over data the caller already fully holds, i.e. rendering. Do not cargo-cult a server search endpoint; equally, do not reuse this pattern for claim data (where AD-7 always applies).
- **AD-3:** the table lives in the one PostgreSQL, created by Alembic like everything else.
- **AD-9:** TanStack Query for the list; the search box is local UI state (React state — exactly what AD-9 assigns to it).
- **Conventions:** list envelope `{items, …}`; snake_case DB / camelCase JSON; seed text verbatim (it is content, not code).

### Data notes

- Creates: `glossary_term` (+ seed). Uses: nothing else. Write-owner: none at runtime — reference data mutated only by future migrations.
- Prototype field mapping: `a` → abbreviation, `t` → term, `d` → definition.
- Note two quirks to preserve verbatim: "OSHA 300" and "Arc Flash" have multi-word abbreviations; "Apportionment" has identical abbreviation and term — the row renders fine, don't dedupe it away.

### UX notes (UX-DR10; prototype `#glov` overlay + `renderGloss`, lines ~1772–1799, glossary open wiring line ~985)

- Slide-in from the right over a click-to-close backdrop (prototype: `#glov` gets `.open`; backdrop click closes when the click target is the overlay itself). ✕ button in the panel header.
- Row anatomy: term name prominent, abbreviation as a small chip beside it, definition in muted text below — information-dense per the house style, hairline dividers between rows.
- Search input at the top of the panel, autofocused on open; filtering instant per keystroke ("live" is the AC word).
- No-match copy: `No matches for "<query>".` in faint/muted style — keep the query echo, it confirms what was searched.
- The 📖 Glossary button lives in the top bar for **every role** (it precedes the stat tiles) — the "one click away everywhere" clause; both shells already share the top bar, so enabling the button covers all roles.

### Testing requirements (this story's definition of done)

- pytest: seed count (25) + endpoint list + 401 without session.
- Vitest: three-field search predicate, no-match state, clear-restores, open/close behavior.
- E2E: `1-6-domain-glossary.spec.ts` green (`@story:1-6 @epic:1`, one `@smoke`); covers open/✕/backdrop-close, three-field search, no-match, and two roles; story cannot reach `review`/`done` until it passes (AD-15).
- Epic 1 close-out note: with 1.6 done, all Epic 1 specs (`@epic:1`) should pass together against one reset stack — worth a local full-epic run before calling the epic done.

### Project Structure Notes

- Server: migration + seed in `server/data/` (seed JSON beside 1.2's extracted data in `server/data/seed/`), router `server/api/routers/glossary.py` (thin — repository can be a trivial unscoped reference-data reader; document why it takes no caller context, citing the AD-7 note above).
- Web: panel component + `queryKeys.glossary`; the top-bar button seam from 1.4 flips from disabled to wired here (delete the seam comment).

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 1.6]
- FR-GLOS-1 + FR-H-11 split (glossary half in Epic 1): [Source: epics.md#Requirements Inventory / #FR Coverage Map]
- Capability map row (glossary governance): [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Capability → Architecture Map]
- List-envelope + naming conventions: [Source: ARCHITECTURE-SPINE.md#Consistency Conventions]
- UX-DR10 (glossary slide-in with live search): [Source: epics.md#UX Design Requirements]
- Prototype GLOSS data + renderGloss predicate + no-match copy + open/close wiring: [Source: docs/Workers_Comp_Prototype.html lines ~1772–1799, ~985–986]
- Term-count discrepancy ruling precedent (contract file wins): [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]

## Dev Agent Record

### Agent Model Used

claude-opus-5[1m] (Claude Opus 5, 1M context)

### Debug Log References

- **The `@smoke` test the story dictates asserts something false.** "search `MMI` → `Maximum Medical Improvement` visible" reads like a one-row result; it is three. `TTD`'s definition ends "before MMI" and `IME`'s says "verify diagnosis, MMI, or treatment appropriateness", so the three-field predicate correctly returns all three. Caught by a strict-mode violation on `getByTestId("glossary-term-name")` resolving to three elements. The spec now expects `glossaryMatching("MMI")` from the oracle and asserts the named row is *among* them — which tests the predicate rather than a coincidence about the data.
- **A JS object literal is not JSON, and the obvious fix is the wrong one.** `GLOSS` uses unquoted keys (`{a:"…",t:"…",d:"…"}`), so `json.loads` cannot read it. The tempting repair — rewrite `, t:"` to `, "t":"` across the array — would also fire inside a definition that happened to contain that sequence. The extractor matches whole entries structurally instead and asserts `len(entries) == array.count("{")`, so an entry it failed to parse aborts the extraction rather than silently vanishing from the seed.
- **`data-testid` does not typecheck in an object literal.** JSX special-cases hyphenated attributes; a value typed as `React.ComponentProps<typeof SheetOverlay>` does not accept them (TS2353). The vendored `SheetContent`'s `overlayProps` therefore names `"data-testid"` explicitly in its type — the alternative, a `Record<\`data-${string}\`, string>` intersection, buys generality nothing needs.
- **`type="search"` puts a second ✕ in the panel.** Blink and WebKit draw a native clear button inside a search input, so the rendered panel had a clear-the-query ✕ about two inches from the dismiss-the-panel ✕. Found by screenshotting the running e2e stack, not by a test — no assertion would have noticed. Changed to `type="text"`, which is also what the prototype's input is.
- **Focus return is structural, not incidental.** The top bar owns `glossaryOpen`, but the button is passed *into* the panel as `SheetTrigger asChild`; Radix then restores focus to the trigger on close. With the button merely calling `setOpen(true)` from outside, focus return would depend on the trigger happening to be `document.activeElement` at open time — true for a mouse click, and not something a Task 3 requirement should rest on. Asserted in both vitest (`toHaveFocus`) and Playwright (`toBeFocused`).
- **Radix's default open-autofocus lands on the search box for free**, because the vendored `SheetContent` renders its close button *after* `children` — so the input is the first tabbable node. That is a DOM-order dependency, so it is pinned by its own test rather than assumed.
- Local DB-backed runs used a disposable `pgvector/pgvector:pg18` on :55432 per `tests/conftest.py`; the container was removed afterwards. `alembic downgrade base` → `upgrade head` was run explicitly, and it completes without error with `alembic check` clean on both sides. Note the limit of that claim: it proves the *row content* returns intact (the full DB-backed suite passes after the round trip), not that the database is byte-identical. `0007 downgrade()` deletes the rows without resetting the identity sequence, so `glossary_term.id` values shift on the way back up, and nothing asserts them — deliberately, since `id` is neither published on the wire nor referenced by anything.

### Completion Notes List

- **AC 1 — 25 terms, seeded from a file nobody typed.** `data/seed/extract_prototype_glossary.py` reads the prototype's `GLOSS` and writes `data/seed/glossary_terms.json`; migration 0007 loads that file; `tests/seed_fixture.glossary_terms()` and `e2e/fixtures/seed.ts` read the same file for their expectations. No count and no definition text appears in any test, any component, or any Python module. A separate extractor from Story 1.2's rather than a mode of it, so an unrelated 100-claim `seed_data.json` is not rewritten by a glossary story.
- **AC 1 — the documented term-count deviation stands.** FR-GLOS-1 and the epics say 24; the prototype has 25 and the prototype is the contract, the same ruling Story 1.1 applied to the design tokens. Recorded in migration 0007's docstring so the next reader finds the decision where the data is, not only in this file.
- **AC 1 — read-only, and the database enforces it.** The only grant is `GRANT SELECT ON glossary_term TO lineworker_app` — no INSERT, no sequence grant, no `audit_redactor` grant, no RLS. `test_app_role_cannot_write_the_glossary` proves the absence with an INSERT, an UPDATE and a DELETE that must each raise "permission denied"; a grant that was never made is otherwise invisible until something needs it. No `version` column and no audit wiring either, with the reason written into the model's docstring rather than left as an omission.
- **AC 1/2 — the endpoint answers the same thing to everyone, and that is the assertion.** `GET /api/glossary` takes no parameters (checked against the published OpenAPI document), and `test_the_answer_is_identical_for_every_role` compares five personas' responses to each other. Every other list in this console differs per persona by design; this one differing would mean somebody had put a scope filter where there is nothing to scope.
- **A design ruling the spec asked for: no `Cache-Control` header.** `/me` and `/stats/*` set `no-store` because their payloads are persona-specific and a shared cache could leak one supervisor's caseload to another. This payload is byte-identical for every session and contains no PHI, so `no-store` would assert a sensitivity the data does not have. Written out in the route's docstring and pinned by `test_no_cache_control_header_is_set`, because "we decided not to" and "we forgot" look the same in a diff a year later.
- **AC 1 — the panel is the prototype's, with the prototype's heading.** `#glov` becomes a shadcn Sheet: right-hand 390px slide-in over a click-to-close backdrop, header, ✕, autofocused rounded search box, scrollable rows of term + abbreviation chip + muted definition with hairline dividers. Design tokens only — the one non-token colour is the backdrop's `bg-black/35`, which is the vendored `dialog.tsx` idiom at the prototype's opacity. **Documented deviation:** the story's Task 3 text says the header reads "WC Glossary"; the prototype's `#glov` header reads **"WC Glossary — Manufacturing"**, and the prototype is this story's declared UX contract, so the subtitle is kept (same contract-file-wins ruling as the term count).
- **`sheet.tsx` vendored with no new dependency**, importing `{ Dialog as SheetPrimitive } from "radix-ui"` exactly as `dialog.tsx` does, `dark:` variants stripped, plain function components with `data-slot`. One house addition: `SheetContent` accepts `overlayProps`, in the same spirit as `dialog.tsx`'s `showCloseButton` — the overlay is rendered inside `SheetContent`, and the backdrop has no accessible role, so a spec asserting backdrop dismissal has no other way to address it. The alternative (a coordinate click) silently starts hitting the panel the day the panel gets wider.
- **AC 2 — the search is the prototype's predicate, and it runs client-side deliberately.** Case-insensitive substring across abbreviation OR term OR definition; empty query matches everything; the no-match line echoes the query untrimmed. The AD-7 argument for filtering in the browser is written into the component's docstring rather than left implicit: glossary terms carry no employer scope and no PHI, the caller already holds all 25 rows, so narrowing text they already have is rendering, not authorization — and it explicitly does not generalise to claim data.
- **AC 2 — the definition-only case is the one that matters.** `audiogram` appears in no abbreviation and no term name, only in NIHL's definition. It is tested in vitest and again in Playwright, because an implementation that searched abbreviation and term would pass every other search assertion in the suite.
- **Task 3 — closing returns focus to the button**, via `SheetTrigger asChild` (see Debug Log). This story has exactly two acceptance criteria; focus return is a Task 3 sub-bullet, not an AC, and calling it "AC 4" invented a criterion the story does not have. Reopening resets the query to empty, matching the prototype's `renderGloss("")`; asserted in both suites.
- **Loading and error are distinct rendered branches (NFR-3).** Skeleton rows while in flight, an inline `role="status"` error otherwise — never an empty list, because a glossary that renders empty on failure teaches a new handler that the term they looked up does not exist. The error test uses a non-retryable 404, not a 500, so it asserts the branch instead of racing the retry policy (Story 1.4's lesson).
- **Fetched on first open, then held.** `useGlossary(enabled)` is gated on the panel being open — the component is mounted all session because it wraps the bar's button, and most sessions never open it — with `staleTime` and `gcTime` both `Infinity`. That is not tuning: rows that change only by migration cannot produce a different answer within a session, and `useLogout` clears the cache wholesale so nothing outlives a persona.
- **Story 1.4's seam assertions were re-pointed, not deleted** — the precedent 1.5 set. The vitest test now asserts the button is enabled and opens the panel; the 1-4 Playwright test is renamed "visible and **filled**" and asserts both seams (glossary enabled, SLA strip visible). What 1.4 guaranteed was a slot in the *shared* bar; something occupying it is that guarantee being kept.
- **Tests:** 175 server (15 new — seed-vs-file equality, dense sort order, both prototype quirks, SELECT-yes/INSERT-UPDATE-DELETE-no, five personas' payloads, cross-role identity, parameter-free contract, camelCase envelope, 401 problem+json, and the absent cache header). 56 vitest (14 new: open, fetch-not-before-open, autofocus, three search fields, no-match echo, clear-restores, reopen-resets, ✕ + focus return, backdrop close, skeleton, 404 error, and the two data quirks) — the TopBar seam test was re-pointed rather than added. 40 Playwright (10 new, `@story:1-6 @epic:1`, one `@smoke`), plus the 7-test `@smoke` set and a full `@epic:1` close-out run, all green against a freshly reset stack.
- **Epic 1 close-out.** `npx playwright test --grep @epic:1` is 40/40 — every Epic 1 spec passing together against one reset stack, which the story asks for before calling the epic done.
- **Verified live.** e2e stack (`compose.e2e.yaml`, :8081): `/api/glossary` 401s with `application/problem+json` unauthenticated and returns `{items: 25, nextCursor: null, total: 25}` with camelCase keys and FNOL first / VR last once a session cookie is presented, and sends no `Cache-Control`. The rendered panel was screenshotted open and in its no-match state and compared against the prototype's `#glov` overlay.
- **For the repo admin:** the migrations CI job now also runs `tests/test_glossary.py` (DB-backed; it would otherwise skip in the server job). No new runtime or dev dependency was added by this story — the Sheet primitive rides the `radix-ui` package already present.

### Code Review Follow-ups (2026-08-10)

Twelve findings from a two-reviewer adversarial pass, all triaged and all applied. Two themes run through them: states that *look* handled but collapse two different facts into one message, and comments that describe an earlier version of the code they sit above.

- [x] **[Med] A successful but empty payload was reported as "no matches for your query".** The results ternary tested `shown.length === 0` and then branched on the query, so `{items: [], total: 0}` — 0006 applied without 0007, a `downgrade 0006`, a table someone emptied — told a handler who had typed `mmi` that `No matches for "mmi".` That is the exact lie the NFR-3 comment in the error branch is written to prevent, arriving through a 200 where no error branch can catch it. `terms.length === 0` is now tested **first**, and the two states carry distinct ids (`glossary-unavailable` vs `glossary-empty`) — they previously shared one, so no test could tell them apart. Both are now under vitest, including the "typed a query into an empty glossary" case that is the whole point of the ordering.
- [x] **[Med] `key={term.abbreviation}` rested on a uniqueness nothing enforced.** `abbreviation` was plain `Text` with `sort_order` as the only unique column, and `GlossaryTermResponse` deliberately publishes neither `id` nor `sortOrder` — so the client keyed its rows on a column the database allowed to repeat. Fixed at the source rather than the call site: `unique=True` on the model and `uq_glossary_term_abbreviation` in migration 0006 (unshipped, so amending it is correct — a new revision to constrain a table two revisions old would be noise). The model, the migration and the call site each say the same thing: the constraint is what makes the wire payload keyable *without* leaking a surrogate id into a public contract. `test_the_two_prototype_quirks_survived_the_pipeline` also stopped building a dict keyed on abbreviation — safe now, but the row list states what the test actually checks.
- [x] **[Med] Nothing tied the seeded data back to the prototype, so AC 1's "verbatim" had no automated coverage.** Migration, pytest, `seed_fixture` and the e2e oracle all read `glossary_terms.json`, so a truncated or hand-edited regeneration passed all 39 tests by making every one of them agree with the same wrong file. New `tests/test_glossary_seed_file.py` parses `const GLOSS=[…]` out of the prototype and asserts the committed JSON matches it entry for entry, field for field, in order — deliberately with a hand-written scanner rather than the extractor's own regex, since importing that would only prove the extractor agrees with itself. Not DB-backed, so it runs in the plain server job; skips with the resolved path in the reason if the prototype is absent, so a partial checkout cannot turn it into a CI failure. Relatedly, `test_sort_order_is_dense_and_starts_at_zero` was renamed and its docstring corrected: `sort_order` comes from `enumerate()`, so a dropped entry yields a dense `0..23` and the test compared `range(24)` to `range(24)` — it never could detect what it claimed.
- [x] **[Med] Live filtering was silent to assistive technology.** A screen-reader user typed `havs`, the list re-rendered, and nothing was announced — not a count, not the no-match line, not loading. A polite `sr-only` live region now announces the result count as the filter changes; the scrolling list is deliberately *not* the live region, which would re-announce all 25 rows per keystroke and make the panel unusable rather than merely silent. `aria-busy` marks the list while pending, because the skeletons are `aria-hidden` and the region was otherwise indistinguishable from an empty glossary. The error paragraph moved from `role="status"` to `role="alert"`: it replaces everything the panel was opened for, and a polite region waits for a lull that someone typing never gives it. Announcement, busy state and error role are all under vitest.
- [x] **[Low] The predicate was documented as "the prototype's, unchanged" while adding a `trim()`.** The prototype's `renderGloss` does not trim, so `"   "` and `"havs "` behave differently from the contract file. The trim is **kept** — terms arrive pasted out of claim notes and adjuster email with a space attached, and answering "no matches" to that is a lie about the data — but it is now a *named* deviation in both the file docstring and `matches()`, with the reason, and the no-match line still echoes the query untrimmed. `glossaryMatching` in the e2e oracle reproduced the trim while presenting itself as an independent restatement of the *prototype's* rule; it now says it restates the **implemented** rule, knowingly. Pinned by a vitest case (whitespace-only shows the full list; a trailing space still matches), so it is a decision with a test rather than an accident somebody later "fixes".
- [x] **[Low] The extractor's documented command and its default argument both pointed at a path that does not exist.** `../docs/Workers_Comp_Prototype.html` "run from server/" resolves to `lineworker/docs/…`; `docs/` is at the repository root, so it is `../../docs/`. Verified by running it — the documented invocation failed. Both fixed, plus an existence check that exits with the resolved path in the message, matching the friendly `SystemExit("GLOSS not found")` its sibling branch already had. Re-ran the extractor: `glossary_terms.json` regenerates byte-identically. (`extract_prototype_seed.py` has the same bug in its docstring; pre-existing and deferred, so a glossary story does not touch the 100-claim extractor.)
- [x] **[Low] The integrity guard was a strippable `assert` counting the wrong thing.** `assert len(entries) == array.count("{")` counted every `{` in the array, including any inside a definition, and vanished entirely under `python -O` — at which point a regex miss silently drops a term, the exact outcome the comment above it claimed to prevent. Now a `raise SystemExit` on a count of entry *boundaries* (`{a:`). The three `json.loads` calls decoding the captured JS literals are also wrapped: JS accepts escapes JSON rejects (`\'`, `\x41`, `\0`), and a future prototype edit using one produced a bare `JSONDecodeError` pointing at a column offset inside an invisible slice. The failure now names the entry index, the field and the literal.
- [x] **[Low] Migration 0007 inserted the JSON payload with no shape check.** A drifted extractor reached the database as a NOT NULL violation or a SQLAlchemy `CompileError` about a bind parameter, halting the upgrade at 0006 — table created, empty, and served by the API as a successful empty glossary (which is what makes the first finding above more than theoretical). A non-empty check and an expected-key-set check on the first row now fail with a sentence naming the seed file and the fix.
- [x] **[Low] The "no `Cache-Control`" test locked in an invariant it did not mean.** The judgement being recorded is "this payload is identical for every caller and PHI-free", not "this endpoint must never be `no-store`". Renamed to `test_the_payload_is_declared_shared_cacheable_not_persona_specific`, with the docstring stating the claim it defends and stating outright that a future story adding any caller-specific field to this payload has changed the premise and must change this test deliberately rather than read its failure as a regression.
- [x] **[Low] Both new oracles handed out their internal mutable collection.** `expectedGlossary()` returned the module-level array (and `glossaryMatching("")` returned it unfiltered), and `seed_fixture.glossary_terms()` returned the `lru_cache`'d list itself. One in-place `.sort()` or `.reverse()` — a reasonable thing to write in a test *about ordering* — silently rewrites the expectation for every later test in the same worker or session, and the failure surfaces in an unrelated file with no visible cause. Both return copies now.
- [x] **[Low] The implementation cited an "AC 4" this story does not have.** Story 1.6 defines exactly AC 1 and AC 2; focus return is a Task 3 sub-bullet. Corrected in `GlossaryPanel.tsx`, `TopBar.tsx`, the vitest test name, and this record's Debug Log and Completion Notes. The Debug Log's round-trip claim was overstated in the same spirit and is now softened to what was actually verified: `downgrade base` → `upgrade head` completes with `alembic check` clean and the full DB-backed suite green afterwards, but `id` values shift because `0007 downgrade()` does not reset the identity sequence, and nothing asserts them.
- [x] **[Low] The glossary button lost its tooltip when the seam was filled.** `title` and `disabled:opacity-60` were removed alongside the `disabled` attribute, and `TopBar.test.tsx`'s matching `toHaveAttribute("title", …)` assertion was deleted rather than re-pointed — the one thing Story 1.5's precedent says not to do with a seam assertion. The button now carries a title describing what it does (a button labelled only with an emoji and the word "Glossary" does not say the definitions are searchable), `disabled:opacity-60` stays so any future disabled state renders distinguishably like the ✕ Switch button beside it, and the assertion follows the seam instead of disappearing with it.

**Deliberately not changed** (triaged as out of scope, recorded so the next reader does not re-raise them): `total = len(items)` with an always-null `nextCursor` and no `LIMIT` — the list-pagination machinery gets built once, with Epic 2's first genuinely pageable list, and `/personas` already sets this precedent; `return data!` in `api/glossary.ts` — the house idiom, identical in `stats.ts` and `auth.ts`; no retry affordance in the error branch — closing and reopening the panel re-fires the query; `0007 downgrade()` deleting rows without resetting the identity sequence — the code stands, only the overstated claim about it was corrected; the unchecked `as SeedGlossaryTerm[]` cast in the e2e oracle — the prototype-tied test above closes the real gap behind it; and `extract_prototype_seed.py`'s identical path bug, which predates this story.

**Gate re-run after the fixes:** ruff and `ruff format --check` clean (68 files), mypy strict clean (68 files), pytest 89 passed / 88 skipped without a database and **177 passed / 0 skipped** against a disposable `pgvector/pgvector:pg18`, `alembic check` reporting no drift both before and after an explicit `downgrade base` → `upgrade head`, eslint clean (2 pre-existing `react-refresh` warnings in `components/ui/`), `tsc --noEmit` clean for web and e2e, **61 vitest**, `npm run generate:api` leaving `src/api/schema.d.ts` byte-identical (the new unique constraint is a database fact, not a wire one), and Playwright **40/40** plus the 7-test `@smoke` set against a freshly built e2e stack, torn down with `down -v` afterwards.

### File List

New files (paths relative to repo root):

- `lineworker/server/data/seed/extract_prototype_glossary.py` (AD-12 extractor), `lineworker/server/data/seed/glossary_terms.json` (generated, committed)
- `lineworker/server/data/versions/20260810_0006_glossary_term.py` (table + the single SELECT grant), `lineworker/server/data/versions/20260810_0007_seed_glossary.py` (the 25 rows)
- `lineworker/server/data/repositories/glossary.py` (the second sanctioned unscoped repository)
- `lineworker/server/api/routers/glossary.py` (`GET /glossary`)
- `lineworker/server/tests/test_glossary.py` (DB-backed: seed, grants, endpoint, 401)
- `lineworker/web/src/components/ui/sheet.tsx` (vendored shadcn Sheet on the `radix-ui` package)
- `lineworker/web/src/api/glossary.ts` (`useGlossary`)
- `lineworker/web/src/features/glossary/GlossaryPanel.tsx`, `lineworker/web/src/features/glossary/GlossaryPanel.test.tsx`
- `lineworker/e2e/stories/1-6-domain-glossary.spec.ts`

Modified files:

- `lineworker/server/data/models/core.py` (`GlossaryTerm`), `lineworker/server/data/models/__init__.py` (export — `alembic check` reads metadata through it)
- `lineworker/server/data/repositories/__init__.py` (registers `glossary`, records the second carve-out)
- `lineworker/server/api/routers/__init__.py`, `lineworker/server/api/app.py` (router registration; `PUBLIC_PATHS` untouched)
- `lineworker/server/tests/seed_fixture.py` (`glossary_terms()` oracle)
- `lineworker/web/src/api/queryKeys.ts` (`glossary`), `lineworker/web/src/api/schema.d.ts` (regenerated)
- `lineworker/web/src/features/shell/TopBar.tsx` (button enabled, owns the open state, renders the panel; seam paragraph in the docstring replaced), `lineworker/web/src/features/shell/TopBar.test.tsx` (seam assertion re-pointed)
- `lineworker/web/src/test/api-mock.ts` (`glossary` route + `GLOSSARY_TERMS` fixture)
- `lineworker/e2e/fixtures/seed.ts` (`expectedGlossary` / `glossaryMatching` oracles), `lineworker/e2e/stories/1-4-scoped-top-bar-caseload-stats.spec.ts` (seam assertion follows the filled seam)
- `.github/workflows/ci.yaml` (the new DB-backed test file in the migrations job)
- `_bmad-output/implementation-artifacts/1-6-domain-glossary.md` (this file), `_bmad-output/implementation-artifacts/sprint-status.yaml`

Added or modified in the review round:

- `lineworker/server/tests/test_glossary_seed_file.py` (**new** — the seed file against the prototype's `GLOSS`, not DB-backed)
- `lineworker/server/data/models/core.py` (`abbreviation` unique), `lineworker/server/data/versions/20260810_0006_glossary_term.py` (`uq_glossary_term_abbreviation`)
- `lineworker/server/data/versions/20260810_0007_seed_glossary.py` (seed shape guard)
- `lineworker/server/data/seed/extract_prototype_glossary.py` (resolved default path + existence check, non-strippable boundary check, named JSON-decode failures)
- `lineworker/server/tests/test_glossary.py` (dense-order test renamed and its claim corrected, quirk test asserts rows not a dict, cache-header test renamed to the judgement it defends)
- `lineworker/server/tests/seed_fixture.py` (`glossary_terms()` returns a copy)
- `lineworker/web/src/features/glossary/GlossaryPanel.tsx` (empty-glossary branch first with its own test id, live region, `aria-busy`, `role="alert"`, trim documented as a named deviation)
- `lineworker/web/src/features/glossary/GlossaryPanel.test.tsx` (5 new tests: empty glossary, empty-vs-no-match, announcement, busy state, trim; error role asserted)
- `lineworker/web/src/test/api-mock.ts` (`GLOSSARY_EMPTY` fixture)
- `lineworker/web/src/features/shell/TopBar.tsx` (button `title` + `disabled:opacity-60` restored), `lineworker/web/src/features/shell/TopBar.test.tsx` (title assertion re-pointed)
- `lineworker/e2e/fixtures/seed.ts` (both oracles return copies; `glossaryMatching` documented as restating the implemented rule)

### Change Log

- 2026-08-10: Story 1.6 implemented — `glossary_term` reference data created by migration 0006 with `SELECT` as its only grant and seeded by 0007 from `glossary_terms.json`, extracted verbatim from the prototype's 25-entry `GLOSS` (the epics' "24" is a documented miscount); an unscoped repository whose docstring argues its own AD-7 exemption; the parameter-free `GET /api/glossary` in the standard envelope, auth-required and deliberately without `Cache-Control`; and the UX-DR10 slide-in panel — vendored shadcn Sheet, prototype row anatomy, autofocused live search across abbreviation, term and definition, query-echoing no-match state, skeleton and error branches — opened by the 📖 Glossary button Story 1.4 shipped disabled. 15 new server tests (175 total), 14 vitest (56), 10 e2e (40) plus `@smoke`; Story 1.4's two seam assertions re-pointed rather than deleted, and the full `@epic:1` suite run green together as Epic 1's close-out. Status → review.
- 2026-08-10: Code review — 12 findings, all 12 applied. The one that mattered was a state collapse: a successful empty payload rendered as `No matches for "<query>".`, telling a handler a term does not exist when in fact the table is empty — the NFR-3 failure the error branch exists to prevent, arriving through a 200. The empty-glossary check now runs first with its own test id, and both states are under test. Alongside it: `abbreviation` gained the unique constraint the React key was already assuming (fixed in the model and the unshipped 0006, not by exposing a surrogate id); a new non-DB test parses the prototype's `GLOSS` and pins the seed file to it, closing the "verbatim" half of AC 1 that every existing test could only assert against itself; the panel gained a polite live region, `aria-busy` and an `alert`-role error, since live filtering was announcing nothing at all; the extractor's documented path was corrected (it did not resolve) and its `assert`-based integrity guard replaced with a non-strippable check that counts entry boundaries rather than braces; 0007 gained a shape guard so a drifted seed fails with a sentence; both oracles return copies; the trim in the search predicate is now a documented, tested deviation instead of a comment claiming the predicate was unchanged; and the "AC 4" citations were corrected to Task 3, the story having only two ACs. Full gate re-run green: 177 pytest DB-backed (89 + 88 skipped without), 61 vitest, 40 Playwright + 7 `@smoke`, `alembic check` clean across a `downgrade base` → `upgrade head` round trip, `schema.d.ts` byte-identical.
