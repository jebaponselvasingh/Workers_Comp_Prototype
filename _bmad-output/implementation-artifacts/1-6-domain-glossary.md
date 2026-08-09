# Story 1.6: Domain Glossary

Status: ready-for-dev

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

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
