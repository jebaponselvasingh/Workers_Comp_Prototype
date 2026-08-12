---
baseline_commit: c28cfc1b89f97a2eb3378cddd5aa70b86e1b4803
---

# Story 2.5: Documents, Employee ID & Statutory Forms

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want the claim's documents and its path-based statutory forms in one place,
so that filings are complete and on time.

## Acceptance Criteria

1. **Given** the Documents & ID tab, **when** the required-forms card renders, **then** it lists forms from `path_required_form` reference data for the claim's classified path (A minor / B follow-up / C fatality) with form IDs, descriptions, timing, and ⬇ download links (FR-H-8).
2. **And** path classification is a registered derivation with its rule parameters in JDM — closing the prototype's always-Path-B gap — with tests covering all three paths.
3. **Given** the tab, **when** it renders, **then** the employee ID card displays and the claim documents list shows name/type/date rows (FR-DET-4).
4. **Given** a document row click, **when** the viewer opens, **then** it is a read-only modal — FROI renders full injury detail, others a summary sheet (FR-DET-4) — with files resolved through the `BlobStore` protocol.
5. **Given** a claim with no documents, **when** the tab renders, **then** an explicit empty state shows (NFR-3).

## Tasks / Subtasks

- [x] Task 1: Migration — `path_required_form` reference data (AC: 1)
  - [x] Alembic migration creating `path_required_form` (surrogate int PK, `path` enum `a | b | c`, `form_code`, `form_name`, `description`, `timing`, `download_url`, `sort_order`)
  - [x] Seed from the prototype's `PATH_DOCS`: Path A — C-2F, FAR-1; Path B — C-3, RFA-1W, C-4.3, C-11; Path C — AFF-1, C-64, C-65 (with their descriptions, timing strings, and URLs) — DATA source only, never code [Source: docs/Workers_Comp_Prototype.html lines 1542–1558]
  - [x] Path display metadata (label, icon, description, banner colors) is **UI-owned** per the enums convention — port `PATH_META` semantics into the web feature, not the DB [Source: docs/Workers_Comp_Prototype.html lines 1559–1563]
  - [x] Note: the seeded URLs are NY WCB / FNSB **placeholders**; per-jurisdiction validation of statutory form references is NFR-4 / a Deferred decision — do not attempt real sourcing here, do record the caveat in code comments
- [x] Task 2: Path-classification derivation + JDM parameters (AC: 1, 2)
  - [x] `services/derivations.claim_path(claim)` → `a | b | c`, registered (AD-10) — this **closes the prototype's always-Path-B gap** (`c.path || "B"`, line 1566): classify **C** when the claim indicates fatality (per the seeded data model: derive from the claim's severity/disability/outcome fields — e.g. a fatality indicator if 1.2's schema carries one, else the JDM-parameterized severity/outcome rule); **A** when minor/first-aid-only (severity score below the JDM minor threshold AND no lost time / no surgery / temporary disability); else **B**
  - [x] All classification parameters (minor severity threshold, qualifying conditions) live in a ZEN JDM document (AD-8); the branching lives in typed Python — one tier each
  - [x] The dev seed must yield at least one claim classifying to each path for testability; if the 100-claim dataset contains no fatality, add a **test fixture** claim in tests (not the seed) to cover C, and document that choice in the Dev Agent Record
  - [x] Unit tests covering all three paths + boundary values of the minor threshold (AC 2 is explicit: tests cover A, B, and C)
- [x] Task 3: API — tab payload + document content endpoint (AC: 1, 3, 4)
  - [x] Extend the claim-detail payload (2.2's endpoint) with the Documents & ID block: classified `path` + its `path_required_form` rows; employee-ID card data (employee ID, policy number, DOI, handler, plant, state · region, name, role); documents list (from 2.2's `document` table: name, type, filed date)
  - [x] `GET /api/claims/{claimBusinessId}/documents/{id}/content` — resolves file bytes/URL through the `BlobStore` protocol (`put/get/delete/url`) in `services/`; seeded documents have no real binaries yet (binary backend is Deferred), so the viewer's structured sheet is generated server-side from claim + document fields, with `blob_key` resolution wired for when real files exist — the protocol boundary is what's binding, not the backing store
  - [x] All routes behind the AD-7 scope context; regenerate the OpenAPI client
- [x] Task 4: Documents & ID tab UI (AC: 1, 3, 5)
  - [x] Replace 2.2's tab empty state with: **required-forms card** — path banner (icon + label + description, path-colored left border/background per UI-owned meta) and one row per form: form-code chip, name, description, ⏱ timing, ⬇ Download link (opens `download_url` in a new tab) [Source: docs/Workers_Comp_Prototype.html lines 1565–1584]
  - [x] **Employee ID card**: branded card with initials avatar, name, role, Employee ID / Policy / DOI / Handler grid, plant + state·region footer [Source: docs/Workers_Comp_Prototype.html lines 1589–1593]
  - [x] **Claim documents list**: header with file count, rows showing type chip (C-1/INC/MED/WAGE/RTW/LEG per doc type), name, "Filed {date}", "View →" affordance [Source: docs/Workers_Comp_Prototype.html lines 1594–1596]
  - [x] Explicit empty state when the claim has zero documents (NFR-3); loading/error states for the tab
- [x] Task 5: Read-only document viewer modal (AC: 4)
  - [x] shadcn/ui Dialog (read-only — no edit affordances): FROI type renders the full injury detail sheet (claim ID, policy, employee, employer/plant, DOI, filed, injury type, body part, ICD-10, cause, severity, AWW, handler, supervisor, OSHA recordable) ; other types render the summary sheet (claim ID, policy, employee, plant, filed, status, handler); signature footer rows [Source: docs/Workers_Comp_Prototype.html openDoc lines 1605–1612]
  - [x] Sheet content comes from the Task 3 content endpoint (server-assembled, AD-1) — the SPA does not compose FROI fields from the claim object client-side
  - [x] Close via ✕ and backdrop; no native dialogs anywhere (NFR-3)
- [x] Task 6: Tests + E2E spec (AC: all)
  - [x] Unit: path derivation A/B/C + thresholds (Task 2); required-forms lookup returns the right rows per path in `sort_order`; content endpoint FROI vs summary shapes; BlobStore protocol conformance test (volume impl)
  - [x] Vitest: forms card renders per-path banner + rows; ID card fields; documents list + empty state; viewer read-only rendering for FROI and non-FROI
  - [x] `e2e/stories/2-5-documents-employee-id-statutory-forms.spec.ts` tagged `@story:2-5 @epic:2`; `@smoke` happy path: open Documents & ID on a seeded Path-B claim → forms card lists C-3/RFA-1W/C-4.3/C-11 with download links → ID card renders → click a FROI row → read-only modal shows full injury detail → close; additional tests: a Path-A seeded claim shows the A form set; empty state on a zero-document claim (seed or fixture)

## Dev Notes

### What this story is — and is not

This story fills the Documents & ID tab: path classification (a real derivation at last), the `path_required_form` reference table, the employee ID card, the documents list, and read-only viewers. The `document` **table already exists** (created + seeded in 2.2) — this story reads it and adds the content/viewer path; do not re-create it. Not in scope: document upload/ingest (no story yet — deferred with the binary backend), document review→confirm write-backs (Epic 3's action worklist), photos (2.6), OSHA/compliance workflows beyond displaying recordability on the FROI sheet. The MinIO-vs-volume decision stays deferred — code only against the `BlobStore` protocol.

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### Architecture compliance (binding ADs for this story)

- **AD-1:** viewer sheets and the classified path are server-assembled; the SPA renders payloads.
- **AD-7:** documents and content endpoints scope-enforced in the repository; an out-of-scope document ID must not resolve.
- **AD-8:** path-classification parameters in a ZEN JDM document; classification branching in typed Python — one rule element, one tier.
- **AD-10:** `claim_path` is a registered derivation — the forms card, and any later surface that needs the path (e.g. Epic 3 actions), call the same function.
- **Conventions — Binary storage:** all file bytes ride the one `BlobStore` protocol in `services/`; backing impl swappable without touching callers.
- **Conventions — Enums:** `path` stored/wired snake_case (`a|b|c`); labels, icons, and colors are UI-owned.
- **AD-15:** story spec is the done-gate (see Testing requirements).

### Data notes

- Creates: `path_required_form` (reference data, seeded — 9 rows across 3 paths). Reference data is effectively read-only; no runtime writer, so no `version` column needed.
- Uses: `document` (2.2), `claim`, `employee`/`employer` fields for the ID card.
- Write-owner (AD-12): `path_required_form` is reference data under `services/claims`' capability row ("Clinical & regulatory fields — statutory forms by path"); its only writer is the migration.
- **NFR-4 / Deferred:** statutory form references must be validated per jurisdiction before go-live; the seeded NY WCB / FNSB URLs are placeholders by explicit architecture decision ("State statutory coverage… and sourcing of real statutory form PDFs" — Deferred). Surface the caveat in a code comment, not in the UI.
- Fatality path (C) note: the 2026 dataset is a live-claims manufacturing book — verify at dev time whether any seeded claim classifies C; if not, C is exercised by test fixtures (Task 2) and the derivation remains fully covered. Record the finding in the Dev Agent Record.

### UX notes

- UX-DR5 (tab placement), UX-DR10 (read-only document viewer modals), UX-DR11/NFR-3 (no native dialogs; explicit empty state) govern.
- UX-DR12 + Story 1.1 ruling: prototype palette is **LIGHT and canonical** (epics.md "dark console aesthetic" is a documented discrepancy). Path banner colors map to tokens: A → ok/ok-soft, B → steel/steel-soft, C → er/er-soft (the prototype's hexes are exactly those tokens).
- The employee ID card is a stylized branded card ("LINEWORKER MFG CO." / WC-VERIFIED) — port its look with tokens + the mono font for IDs; initials avatar from the worker name.
- Download links open in a new tab (`rel="noopener"`); they are external placeholder PDFs, not BlobStore content.

### Testing requirements

- Unit tests the ACs demand: **path classification across all three paths** (AC 2 names this explicitly) + threshold boundary values; per-path form lookup; FROI vs summary content shapes; BlobStore protocol conformance.
- Vitest: forms card per path, ID card, list + empty state, read-only viewers.
- E2E (AD-15): `e2e/stories/2-5-documents-employee-id-statutory-forms.spec.ts` tagged `@story:2-5 @epic:2`, exactly one `@smoke` happy path, per-spec DB reset; covers Path-B forms render, FROI viewer, a second path's form set, and the empty state. **The story cannot move to `review`/`done` until this spec passes.**

### Project Structure Notes

- Server: migration in `server/data/` versions; `claim_path` in `server/services/derivations/`; JDM document in `server/rules/`; BlobStore protocol + volume impl in `server/services/` (blobstore module per the 1.1 tree); tab/content assembly in `server/services/claims/`; routes in `server/api/`.
- Web: `web/src/features/claim-detail/documents/` — `DocumentsTab.tsx`, `RequiredFormsCard.tsx`, `EmployeeIdCard.tsx`, `DocumentList.tsx`, `DocumentViewerDialog.tsx`; path display meta as a UI constant; keys in `web/src/api/queryKeys`.
- The doc-type → chip mapping (FROI→C-1, INCIDENT→INC, MEDAUTH→MED, WAGE→WAGE, RTW→RTW, LEGAL→LEG) is UI-owned labeling over the snake_case `doc_type` enum from 2.2.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 2.5]
- FR-H-8 incl. "real path classification per claim (§7.4-6)": [Source: _bmad-output/planning-artifacts/epics.md#Functional Requirements]
- AD-7 / AD-8 / AD-10 / AD-15 + Binary-storage & Enums conventions: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules / #Consistency Conventions]
- Capability map row "Clinical & regulatory fields — statutory forms by path" + `path_required_form` in the ERD: [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map / #Core entity ERD]
- Deferred statutory sourcing + NFR-4: [Source: ARCHITECTURE-SPINE.md#Deferred; _bmad-output/planning-artifacts/epics.md#NFR-4]
- Prototype seed data + design contract: [Source: docs/Workers_Comp_Prototype.html — PATH_DOCS 1542–1558, PATH_META 1559–1563, always-Path-B gap 1566, pathDocsHTML 1565–1584, docsHTML/ID card 1586–1597, openDoc viewer 1605–1612]
- Design-token light-palette ruling: [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]

## Dev Agent Record

### Agent Model Used

claude-opus-5[1m] (Claude Opus 5, 1M context), via the `bmad-dev-story` workflow.

### Debug Log References

- **The derivation could not be called `claim_path.py`.** `test_no_derivation_module_is_named_after_the_value_it_exports` fired immediately: `from services.derivations.claim_path import claim_path` rebinds the package attribute from the submodule to the `Derivation` instance, after which `services.derivations.claim_path.ClaimPathDerivation` raises `AttributeError` with no obvious cause. The package's own convention answers it — a module is named after the *rule*, not the derived value — so the file is `path_classification.py`. Worth recording because the trap only bites the story that adds a derivation, and this is the first one since the convention was written down.
- **`# … the AFF-1/C-64/C-65 filing set` failed the threshold grep.** `test_no_module_outside_the_registry_hardcodes_the_band` matches a bare `65` not preceded by a word character, and a hyphen is not one — so a *form code* in a comment read as a risk-band threshold. Reworded to "the death-benefit filing set" rather than allowlisted, on Story 2.4's precedent (the guard is deliberately blunt, and a comment naming a threshold is one edit away from being code that uses it). The collision is worth knowing about: this story's whole vocabulary is form codes of the shape `C-64`, `C-65`, `C-4.3`.
- **The empty-state e2e test deleted the documents of the claim two other tests read.** `firstClaimInStage(KAYA, "settled")` and `firstClaimOnPath(KAYA, "b")` are the same row — Kaya's lowest claim id is settled and classifies B — so a destructive test surfaced as `Cannot read properties of undefined` in the *AD-7* test three tests later, pointing at the wrong code. Fixed with a `lastClaimInStage` oracle plus two explicit `expect(...).not.toBe(...)` guards in the spec, so the next destructive test fails on its own premise rather than on somebody else's assertion.
- **`openapi-fetch` hands `fetch` a `Request` object as its only argument**, so `String(input)` is `[object Request]` and the "nothing is requested until a row is opened" assertion counted zero requests in both directions. Reading `input.url` is the fix — the same trap Story 2.4's `InjuryWrites.test.tsx` recorded, hit again in the first test of this story that inspected a call.
- **`test_version_one_of_a_superseded_document_is_still_exactly_what_it_was` was asserting less than it read.** It pinned v1's content and then checked that the loader preferred **v2**; with v3 seeded, the second assertion simply failed. But the first was the weaker problem: written for one superseded version, it would have gone on passing while a v3 quietly rewrote v2. Generalised to walk every superseded version and to compute the effective one from the table it already declares.
- **Four version pins moved from 2 to 3** (`test_claim_detail.py`, `test_claims_queue.py` ×2, `test_rules_engine.py`). That is the supersession mechanism working exactly as `test_the_loader_picks_the_effective_version_of_each_key` says it should — a v3 that landed without those tables being updated fails loudly instead of quietly re-ranking every queue.

### Completion Notes List

- **AC 2 — the prototype does not classify anything, and this is the story that says so out loud.** `pathDocsHTML` reads `c.path || "B"` against a dataset in which **no claim carries a `path` field at all** (verified: zero occurrences of `"path":` across the 100-claim array). Every one of its claims — a 20-severity hand laceration and a 98-severity amputation alike — renders the same four Path B forms, under a banner that states the classification with complete confidence. `claim_path` is a registered derivation (AD-10) whose three parameters live in `derivation_thresholds` v3 (AD-8), and the assertion that catches the prototype's behaviour is not the per-claim one but `test_two_claims_on_different_paths_get_different_form_sets` — a single-payload test passes against the bug.
- **The fatality cut-off sits at the top of the domain, and that is the most consequential decision in the story.** The seeded schema carries **no fatality indicator of any kind**: no flag, no outcome, no status; `ReturnStatus`'s three members all describe living workers. The story permitted a JDM-parameterised severity/outcome rule, so `claim_path` requires maximum severity **and** permanent disability **and** no return to work — and `pathFatalitySeverityMin` is 100 because of what the data actually contains. All seven `permanent` claims in the book are amputations scoring 75–98, and **several returned to work fully recovered**. Any threshold low enough to make Path C fire against this portfolio would file a living amputation survivor as a death-benefit claim and show their handler the AFF-1 (dependants' claim), the C-64 (proof of death) and the C-65 (burial expenses). That is not a cosmetic misclassification. So no seeded claim classifies C — the story anticipated this — and C is exercised by the `FATAL` fixture in `test_path_classification.py`, with three separate tests for the ways a *survivor* must not reach it. `test_no_seeded_claim_is_classified_a_fatality` runs over all 100 claims and its failure message says explicitly that the fix is a column, not a lower threshold.
- **The path parameters went into `derivation_thresholds` v3 rather than a document of their own**, which cuts the opposite way from Story 2.4's `injury_capture` and 2.2's `intake_required_documents`. The rule the codebase already states: `DerivationThresholds` is the single argument every *registered derivation* is built from, and the other two documents parameterise `services/claims` **commands**. `claim_path` is a derivation, so it belongs in the block — and splitting it out would have added a second loader call to the case-file path to answer one classification. v3 takes v1's effective date under the rule 0012 wrote down (a version that adds *required* parameters cannot be future-dated, or the console 500s until the date arrives).
- **`claim.path` is not a column, and the classification is not stored.** Story 1.2 banned derived columns and the ban is load-bearing here: the path is a function of `severity_score`, `disability`, `surgery_required`, `recovery` and `return_status`, and Stories 2.3 and 2.4 made all five editable. A stored path would be stale the moment a handler corrected a severity score, with nothing to say so — and the surface it would be stale on is the one listing a claim's statutory obligations.
- **`PATH_META` did not cross the wire, and a test enforces it.** The prototype keeps the label, the icon and two hex colours per path in an object beside `PATH_DOCS`; the Enums convention puts all four in the browser (`pathMeta.ts`), exactly as the risk band's labels are. The port is faithful rather than re-designed: the six hexes `PATH_META` writes are precisely `--ok`/`--okd`, `--st`/`--sld` and `--er`/`--erd` in the prototype's own `:root`, so the tokens reach the same colours by name. `test_no_path_display_metadata_reached_the_seed_file` greps the seed for a hex value and for the three labels, and `test_no_path_label_icon_or_colour_crosses_the_wire` does the same to the payload.
- **`PATH_META`'s `|| PATH_META.B` fallback is why the prototype's bug is invisible**, and it is not ported. `pathMeta.ts` is a `Record<ClaimPath, PathMeta>`, so a fourth path fails the TypeScript build beside the other three rather than rendering an unstyled banner.
- **AC 1 — the forms are reference rows, and the extractor is under test.** `extract_prototype_path_forms.py` reads `PATH_DOCS` into `path_required_forms.json` (nine rows: 2/4/3), 0017 creates the table and 0018 seeds it with a per-path count check. `test_path_forms_migration.py` asserts the seeded rows against **the prototype's HTML**, through a second, independently written parse — a comparison against the extractor's own output would pass however wrong it was. `sort_order` is unique per `(path, sort_order)` rather than globally, because each path's list is numbered from zero and a global constraint would make adding a Path A form renumber Path C; a separate test asserts the numbering has no gaps, which is the failure a dropped form would otherwise look like.
- **`services/blobstore` was an empty file and is now the boundary.** Four operations, `put/get/delete/url`, and deliberately no `list`/`exists`/`stat` — `exists` in particular invites the check-then-read race `get` raising `BlobNotFound` closes. `url` may answer `None`, in the *protocol*, so a caller written against a presigning store does not break the day a deployment picks a volume. `VolumeBlobStore` exists so the protocol has conformance tests with real bytes behind it, and it refuses any key that is not a flat name: a `blob_key` will one day come from an ingest path or an AD-13 agent tool, and a traversal there would read arbitrary files out of the API container. The test suite is parametrised over `STORES`, so MinIO inherits it by being added to one dict.
- **The viewer renders no bytes, and that is stated rather than hidden.** All 563 seeded documents have `blob_key` null — the prototype has no files. The sheet is assembled server-side from claim and document columns (which is what `openDoc` renders anyway), `hasBlob`/`blobUrl` are on the contract, and `document_content` takes an optional `store` so the resolution is wired without a viewer taking a storage decision.
- **AC 4 — a sheet row carries either `text` or `cents`, never both.** The alternative was a server-formatted `"$1,432.00"`, which would have been the single place in the system where money was not integer cents formatted only in the UI. `SheetRow.of_text` / `of_date` / `of_cents` are the only constructors, so the invariant holds by construction, and two tests pin it: one asserts the AWW row is the only money row and equals the seeded cents, the other that no `$` appears anywhere in the payload. Dates ride in `text` as ISO strings, which is exactly what the SPA's `formatDate` already renders — including the em dash for the 101 documents that carry a timing note where a date belongs.
- **Two deliberate departures from `openDoc`.** It prints a **Supervisor** row, and nothing persists a supervisor — Story 1.2 did not seed one, so the prototype's own viewer renders `undefined` there. Story 2.2's ruling applies ("a row that always reads '—' is furniture, not honesty") and a statutory filing is the last place to print a blank where a name goes. It also prints the dataset's `severity` **display string** ("High"/"Medium"/"Low"), which is a *third* banding of `severity_score` beside the gauge's and the queue dot's; the sheet carries the score, because a first report records what was assessed and AD-10 says a band has one computer.
- **The 404 covers three situations and a test compares the problem `type`.** No such document, a document of a different claim, and a claim outside the caller's scope all resolve to `None` in `select_document`. Surrogate document ids are dense, so a route that told them apart would let a caller walk `1…10000` and learn how many documents the portfolio holds — the enumeration `select_claim_detail` closes at the claim level, reopened one path segment down. The claim id in the path is a *predicate*, not decoration: a test proves a document of the caller's **own** second claim does not resolve through the first claim's URL.
- **A supervisor can open a document sheet.** Worth an explicit test rather than an assumption: the four write commands refuse a non-handler, and a viewer that had copied that role check would have hidden the case file's documents from the person reviewing the file. Viewing is a read; reads are gated by scope.
- **AC 4 — read-only is asserted structurally, twice.** The dialog imports no mutation hook and contains no input, select or submit; a vitest test counts the buttons inside it (one, the ✕) and asserts no `textbox`/`combobox` exists, and a server test asserts `POST`/`PATCH`/`PUT`/`DELETE` on the content route all answer 405. A viewer that a later story quietly gave a PATCH would be a second write path for columns the four AD-4 commands own.
- **The sheet is a query of its own, not a block on the case file.** The one departure from Story 2.2's "everything the tab shows arrives with the tab": a claim carries three to eight documents and each sheet is a dozen rows of the same claim's data, so folding them in would multiply the payload of the most-fetched endpoint to serve a modal most handlers never open. It is `enabled` on the dialog's open state — a test counts the requests and asserts zero until a row is clicked — and keyed **under** the claim (`["claims","detail",id,"document",n]`) so an edit that changes the case file can reach every sheet cut from it.
- **Document rows are `<button>`s.** The prototype attaches `data-di` to a `div` and delegates, which makes its viewer unopenable by keyboard and invisible to a screen reader — a document viewer nobody can reach without a mouse. Asserted with a keyboard-only test rather than a comment.
- **AC 5 — the empty state is unreachable against the seed, which is why it is tested twice.** All 100 claims carry three to eight documents (`test_every_seeded_claim_has_documents_so_the_empty_state_needs_a_fixture` asserts the premise, so a seed change that made it reachable shows up as a failing test rather than a state nobody re-checked). It is covered by a vitest fixture and by an e2e test that empties one claim's file through `psqlQuery`. A second test asserts the empty state does **not** empty the forms card: which filings a path requires does not depend on what has been filed, and a state that blanked both would tell a handler their statutory obligations had gone away because nobody had met them yet.
- **`data/repositories/statutory_forms.py` is the third sanctioned unscoped repository**, on `glossary.py`'s exact argument: `path_required_form` has no employer, no claim and no PHI, so a `CallerContext` parameter would be decoration — accepted, ignored, and read by the next author as evidence that scoping had been enforced. The claim-scoped half is asked first and elsewhere: the caller resolves the claim through the scoped `select_claim_detail` and derives a path from it, and a path belongs to nobody.
- **The `documents` block sits outside the stage-variant union**, like 2.4's `injury`, and the ID card is published as a block rather than stitched client-side — `employeeBusinessId` and `plant` live on the *intake* variant, so a client assembling the card itself would render a different card once a claim moved to treatment. Asserted across three stages.
- **Story 2.2's Documents seam was re-pointed, not deleted**, in both the vitest suite and the Playwright spec (1.5/1.6/2.1/2.3/2.4's precedent). Each now asserts the stronger version of what it guaranteed: the tab-reset test checks that the *built* panel renders (a seam assertion would have gone on passing while the tab rendered nothing), and the seam table checks that a tab is either built or honest about not being — with the two built ones asserted explicitly beside the three that are not.
- **`noDerivation.test.ts` gained `path` to its derived-field list** and the documents folder to its scan assertion. `path` is the single most consequential derived value in the console — it decides which death-benefit forms a handler is shown — so a comparison against it in the browser is a component deciding a regulatory question.
- **Tests:** 753 server (58 new — the classification across all three paths with both boundaries from both sides, three ways a survivor must not reach Path C, the parameter block's new refusals, four Hypothesis properties, the migration literal against the enum, the seeded rows against the prototype's own HTML, `sort_order` gaps, the grant surface, both sheet variants, the money invariant, the three-way 404 including its problem `type`, the cross-claim document refusal, the 405s, and the BlobStore conformance suite including six traversal keys). 210 vitest (20 new — the banner and rows per path, the disjoint form sets, the ID card and its initials, the list's chips and dates, the empty state and the forms card surviving it, both viewer variants, cents formatted in the browser, the em dash, the read-only assertion, the deferred fetch, and the unmounted panel). 83 Playwright (6 new, `@story:2-5 @epic:2`, one `@smoke`; the count includes the DB-reset setup project).
- **Verified live.** e2e stack (`compose.e2e.yaml`, :8081) rebuilt from clean: full suite **82/82**, the 12-test `@smoke` set and the 43-test `@epic:2` close-out all green against a freshly reset stack. `alembic check` reports no drift and a `downgrade 0016` → `upgrade head` round trip completes clean. `npm run generate:api` leaves `src/api/schema.d.ts` byte-identical on a second run. ruff + format + mypy clean; eslint clean (8 pre-existing warnings, none new). No new runtime or dev dependency.

### File List

**New — server**

- `lineworker/server/data/versions/20260812_0017_path_required_form.py`
- `lineworker/server/data/versions/20260812_0018_seed_path_forms.py`
- `lineworker/server/data/versions/20260812_0019_claim_path_rules.py`
- `lineworker/server/data/seed/extract_prototype_path_forms.py`
- `lineworker/server/data/seed/path_required_forms.json`
- `lineworker/server/data/repositories/statutory_forms.py`
- `lineworker/server/rules/documents/derivation_thresholds.v3.jdm.json`
- `lineworker/server/services/derivations/path_classification.py`
- `lineworker/server/services/claims/documents.py`
- `lineworker/server/tests/test_path_classification.py`
- `lineworker/server/tests/test_path_forms_migration.py`
- `lineworker/server/tests/test_documents_tab.py`
- `lineworker/server/tests/test_blobstore.py`

**Modified — server**

- `lineworker/server/services/blobstore/__init__.py` (was an empty module; now the `BlobStore` protocol, its two exceptions and `VolumeBlobStore`)
- `lineworker/server/data/models/enums.py` (`ClaimPath`)
- `lineworker/server/data/models/core.py` (`PathRequiredForm`)
- `lineworker/server/data/models/__init__.py` (export)
- `lineworker/server/data/repositories/__init__.py` (the third carve-out, documented)
- `lineworker/server/data/repositories/claims.py` (`select_document`)
- `lineworker/server/rules/parameters.py` (three path parameters, `_recovery_window_set`, two new refusals)
- `lineworker/server/services/derivations/__init__.py` (registers `claim_path`)
- `lineworker/server/services/claims/detail.py` (the `documents` block on the case file)
- `lineworker/server/api/routers/claims.py` (four response models, the block on `ClaimDetailResponse`, and the content route)
- `lineworker/server/tests/test_derivations.py`, `test_case_file_derivations.py`, `test_rule_parameters.py` (the v3 threshold oracles)
- `lineworker/server/tests/test_rules_engine.py` (v3 in both document tables; the superseded-version test generalised to walk every version)
- `lineworker/server/tests/test_claim_detail.py`, `test_claims_queue.py` (thresholds version pins 2 → 3)
- `lineworker/server/tests/test_scoped_repository.py` (code review: the child-read scope test `select_document` had no cover for)

**New — web / e2e**

- `lineworker/web/src/features/claim-detail/documents/DocumentsTab.tsx`
- `lineworker/web/src/features/claim-detail/documents/RequiredFormsCard.tsx`
- `lineworker/web/src/features/claim-detail/documents/EmployeeIdCard.tsx`
- `lineworker/web/src/features/claim-detail/documents/DocumentList.tsx`
- `lineworker/web/src/features/claim-detail/documents/DocumentViewerDialog.tsx`
- `lineworker/web/src/features/claim-detail/documents/pathMeta.ts`
- `lineworker/web/src/features/claim-detail/documents/DocumentsTab.test.tsx`
- `lineworker/e2e/stories/2-5-documents-employee-id-statutory-forms.spec.ts`

**Modified — web / e2e**

- `lineworker/web/src/api/claims.ts` (`useDocumentSheet` and the block's types)
- `lineworker/web/src/api/queryKeys.ts` (`claims.documentSheet`)
- `lineworker/web/src/api/schema.d.ts` (regenerated)
- `lineworker/web/src/features/claim-detail/DetailTabs.tsx` (the documents panel; the seam entry removed)
- `lineworker/web/src/features/claim-detail/ClaimDetailPane.tsx` (renders the tab)
- `lineworker/web/src/features/claim-detail/ClaimDetailPane.test.tsx` (2.2's seam assertions re-pointed)
- `lineworker/web/src/features/queue/noDerivation.test.ts` (`path` added to the derived fields; the documents folder named in the scan assertion)
- `lineworker/web/src/test/api-mock.ts` (`DOCUMENTS_BLOCK` and its two variants, both sheets, the `documentSheet` stub route, `documents` on the four case-file fixtures)
- `lineworker/e2e/fixtures/seed.ts` (the 2.5 oracles: the classification rule restated, the per-path form sets, the ID card's fields, `lastClaimInStage`)
- `lineworker/e2e/stories/2-2-case-header-stage-adaptive-overview.spec.ts` (the Documents seam assertion re-pointed)

**Modified — repo**

- `_bmad-output/implementation-artifacts/deferred-work.md` (four items)
- `_bmad-output/implementation-artifacts/2-5-documents-employee-id-statutory-forms.md` (this file), `_bmad-output/implementation-artifacts/sprint-status.yaml`

### Change Log

- 2026-08-12: Two findings from **Story 2.6's** code review landed in this story's files and were fixed there (both low, neither a behaviour change to what 2.5 shipped). `DocumentViewerDialog` rendered its header outside the pending/error branch, so a failed sheet showed the subtitle "Loading…" directly above "⚠ This document could not be loaded" — two contradictory statements, of which the misleading one tells a handler to wait for a request that has already failed; it now has a third label and a test asserting the subtitle agrees with the body. And `extract_prototype_path_forms.py` carried a comment claiming its five keys "are matched by name rather than by position so that a reordered literal is still read correctly", which is the opposite of what its positional regex does — a reordered `PATH_DOCS` matches zero entries and aborts the extraction. The abort is the *right* failure and was kept; the comment now describes it, because as written it invited a maintainer to reorder the prototype's literal and expect it to work. Extractor output verified byte-identical.

- 2026-08-12: Addressed code review findings — **4 findings, 4 fixed, 0 deferred, 0 dismissed**. The one that mattered was an AD-7 test that asserted nothing: it paired a *foreign claim id* with a *local document id*, which the claim/document mismatch predicate refuses on its own, so `employer_scope` in the new `select_document` had no test behind it. The endpoint-level test could not be repaired into one either — `document_content` re-reads through the already-scoped `select_claim_detail`, so the filter can be deleted with every API test still green — and the guarantee moved to `test_scoped_repository.py`, where this module's "every query applies the filter" invariant lives. Also: every case-file read of an *intake* claim ran the same scoped document query twice (the checklist and the new block each fetched their own), on the most-fetched payload in the console; and the viewer rendered every non-money row through `formatDate`, which works only while that helper is the identity function and would print `Invalid Date` across a whole FROI sheet the day it isn't. Each fix is pinned by a test verified to fail when the fix is reverted. One e2e defect surfaced while fixing the first: a second `loginAs` on an authenticated page never reaches the role picker, so the spec switches personas first. Gate re-run green: ruff + format + mypy clean, **pytest 755**, **vitest 211**, **Playwright 82/82** plus the 12-test `@smoke` set and the 43-test `@epic:2` close-out against a rebuilt e2e stack.

- 2026-08-12: Story 2.5 implemented end to end — `path_required_form` created and seeded with the prototype's nine statutory forms (2/4/3 across three paths) via a new extractor, and `derivation_thresholds` superseded with a v3 carrying the three claim-path parameters. `claim_path` joined the AD-10 registry as `services/derivations/path_classification.py`, closing the prototype's always-Path-B gap: the seeded book now classifies five claims Path A and ninety-five Path B, with Path C recognised only by a fixture because the schema carries no fatality indicator (recorded as deferred work, with a test over all 100 claims to keep it that way). The case file gained a `documents` block — the classified path, its forms, the employee ID card and the document list — and a new `GET /claims/{id}/documents/{id}/content` route answers a server-assembled read-only sheet in two variants behind the one `BlobStore` protocol, which `services/blobstore` now actually defines. The Documents & ID tab replaced Story 2.2's seam with the ported forms card, ID badge, keyboard-reachable document list and a read-only shadcn Dialog. Full gate green: ruff + format + mypy clean, pytest 753, vitest 210, Playwright 82/82 plus the 12-test `@smoke` set and the 43-test `@epic:2` close-out against a rebuilt e2e stack. Status → review.

### Code Review Pass — 2026-08-12

Four findings, **all four real and all four fixed**. Each is now pinned by a
test verified to fail when the fix is reverted (checked by reverting each one
and watching the new test go red).

**1. The AD-7 test asserted nothing (medium).** `test_an_out_of_scope_claims_document_is_a_404`
logged in as Sarah and asked for *Sarah's own* claim id paired with one of
*Kaya's* document ids. The claim was therefore in the caller's scope, and the
404 came from the claim/document mismatch predicate — so `employer_scope` in
the new `select_document` could have been deleted with every test still green.

The repair is not the obvious one. Rewriting the endpoint test to use a
genuinely foreign pair still does not pin the filter, because
`document_content` resolves the document and *then* re-reads the claim through
the already-scoped `select_claim_detail`: the route refuses a step later, which
makes the endpoint safe today and the repository invariant untested. That is
exactly the state `data/repositories/claims.py`'s docstring says must not
exist — "every query in this module applies the filter", enforced structurally
rather than by whichever caller happens to check afterwards. So the guarantee
moved to `test_scoped_repository.py`, which owns that invariant, and asserts it
at the repository: a document that resolves for its owner and not for the other
handler can only have been filtered by scope. Deleting the predicate now fails
that one test and nothing else, which is the right blast radius.

The endpoint and e2e tests were repaired anyway, because their *comments*
claimed a cross-scope refusal they did not make. Both now read a document id
while logged in as its owner and ask for it as somebody else, and both compare
all three refusals — out of scope, wrong claim, absent id — as answers the
*same* caller can see. A refusal only leaks something if the caller can compare
it with another refusal they can also obtain, and the old version compared two
different callers' answers, which is not a comparison anybody can make.

**2. The same defect at the e2e layer (low).** Fixed alongside, and it surfaced
a second one: `loginAs` on an already-authenticated page redirects to the
workspace and never sees the role picker, so the second login timed out. The
spec now calls `switchPersona` first — which is how a handler actually leaves
the console, and what the login fixture exposes for it.

**3. One scoped query run twice per intake case file (low).** `documents_block`
and the intake variant's checklist are built from the same rows and each
fetched its own. The read is hoisted into `claim_detail` and handed to both;
`documents_block` lost its `CallerContext` parameter as a result, which is the
right shape — everything it still reads for itself is unscoped reference data,
and a context it did not use would be exactly the decoration
`data/repositories/statutory_forms.py` argues against. Pinned by a test that
counts the statements the engine executes, because reading `detail.py` and
believing it is what let the duplicate in. Worth fixing despite being a
performance finding: the case file is re-read after each of the four AD-4
commands and embedded in every 409 body.

**4. `formatDate` applied to rows that are not dates (low).** Every non-money
sheet row was rendered as `formatDate(row.text ?? null)`, including `Claim ID`,
`Employee`, `Injury Type`, `Severity` and `Status`. It works only because that
helper is currently `iso ?? "—"` — the identity function. The day it does what
its name and signature promise, an entire FROI sheet renders `Invalid Date`,
and the developer making that change would be looking at the timeline and the
header, not here. The viewer now renders `row.text ?? EMPTY` with its own
constant; the em dash is what the two shared, so that is all that is shared.
Pinned by a test asserting four non-date rows *by value*, which fails when
`formatDate` is made to format.
