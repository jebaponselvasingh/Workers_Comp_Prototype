---
baseline_commit: c28cfc1b89f97a2eb3378cddd5aa70b86e1b4803
---

# Story 2.6: Incident Photos

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want incident and site photos with a viewer,
so that visual evidence is reviewable in the case file.

## Acceptance Criteria

1. **Given** the Photos tab, **when** it renders, **then** a grid of photo cards (caption + source) shows with the tab label carrying the count, backed by a `photo` table seeded from prototype data.
2. **Given** a photo card click, **when** the viewer opens, **then** it is a read-only photo viewer modal (FR-DET-4).
3. **And** an empty state renders when the claim has no photos (NFR-3).

## Tasks / Subtasks

- [x] Task 1: Migration — `photo` table, seeded (AC: 1)
  - [x] Alembic migration creating `photo` (surrogate int PK, `claim_id` FK, `caption`, `source`, `blob_key` nullable — image bytes ride the `BlobStore` protocol when real files exist; the prototype ships no binaries, placeholder rendering is expected)
  - [x] Seed from each prototype claim's `photos` array — objects `{t, d}` map to `caption` (t) and `source` (d); claims with empty arrays get no rows — DATA source only, never code [Source: docs/Workers_Comp_Prototype.html ALL_CLAIMS line 632, rendered at photosHTML line 1599]
  - [x] Snake_case naming per the canonical mapping; migration runs clean on a fresh DB (CI gate)
- [x] Task 2: API — photos in the claim payload (AC: 1, 2)
  - [x] Extend the claim-detail payload (2.2's endpoint) with the photos block: `photos: [{id, caption, source}]` and a count the tab label uses — count comes from the server payload, never a client-side length of a separately fetched list drifting from the tab (one source)
  - [x] Photo content resolution through the `BlobStore` protocol (`url`/`get`) for rows with a `blob_key`; seeded rows have none — the API signals "no binary" and the UI renders the placeholder treatment (binary backend is a Deferred decision; the protocol boundary is what's binding)
  - [x] Behind the AD-7 scope context; regenerate the OpenAPI client
- [x] Task 3: Photos tab UI (AC: 1, 3)
  - [x] Replace 2.2's Photos tab empty-state placeholder: tab label becomes `Photos (n)` from the server count [Source: docs/Workers_Comp_Prototype.html line 1185]
  - [x] Grid of photo cards: thumbnail region (📷 placeholder treatment when no binary; real image via BlobStore URL when present), caption, source line [Source: docs/Workers_Comp_Prototype.html lines 1599–1603]
  - [x] Explicit empty state when the claim has zero photos ("No photos on file.") — and the tab label reads `Photos (0)` (NFR-3)
  - [x] Loading and error states for the tab (NFR-3); TanStack Query via the shared `claimDetail` key (AD-9 — no separate fetch idiom for one tab)
- [x] Task 4: Read-only photo viewer modal (AC: 2)
  - [x] shadcn/ui Dialog, read-only — no edit/delete affordances: large photo region (placeholder or BlobStore image), then Caption / Source / Claim (business ID) key-value rows [Source: docs/Workers_Comp_Prototype.html openPhoto line 1613]
  - [x] Close via ✕ and backdrop click; no native dialogs (NFR-3)
- [x] Task 5: Tests + E2E spec (AC: all)
  - [x] Unit (server): photos block assembly (rows + count agree; scoped access; no-binary signaling)
  - [x] Vitest: grid renders a card per photo with caption + source; placeholder thumbnail; empty state; tab count; viewer read-only rendering
  - [x] `e2e/stories/2-6-incident-photos.spec.ts` tagged `@story:2-6 @epic:2`; `@smoke` happy path: select a seeded claim with photos → tab label shows the count → grid renders cards → click a card → read-only viewer shows caption/source/claim → close via backdrop; additional test: a seeded claim with zero photos shows `Photos (0)` and the empty state

## Dev Notes

### What this story is — and is not

The smallest story of the epic and the last Epic 2 seam-closer: it creates the `photo` table, fills the Photos tab (grid + count + viewer + empty state), and completes the 6-tab detail pane's Epic 2 surface — after this story only Bills & Payments (Epic 3) and AI Insights (Epic 6) still show cross-epic empty states. **Read-only**: there is no photo upload, capture, annotation, or deletion anywhere in the epics — do not add write commands, and therefore no AD-4 command machinery is needed here (the migration is the table's only writer this epic). The prototype's `photosHTML`/`openPhoto` JS is design/data reference only — never code.

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### Architecture compliance (binding ADs for this story)

- **AD-1:** photo rows and count come from the detail endpoint; the SPA renders payloads.
- **AD-7:** photos ride the scope-enforced claim-detail repository path; out-of-scope claims expose nothing.
- **AD-9:** photos live under the shared `claimDetail` query key — one server-state idiom, no bespoke fetch.
- **AD-11:** incident photos are claim-derived PHI-class binaries — when real files arrive they live on encrypted volumes behind BlobStore; nothing in logs beyond IDs/event names.
- **AD-12:** `photo` is owned by `services/claims` (case-file aggregate); this epic its only writer is the seed migration.
- **Conventions — Binary storage:** all bytes through the one `BlobStore` protocol; volume-vs-MinIO stays deferred.
- **AD-15:** story spec is the done-gate (see Testing requirements).

### Data notes

- Creates + seeds: `photo` from the prototype's per-claim `photos` arrays (`t`→`caption`, `d`→`source`). Field names `t`/`d` are prototype JSON abbreviations — the DB uses the canonical descriptive snake_case names.
- No image binaries exist in the prototype (thumbnails are the 📷 emoji) — seeded rows carry `blob_key = NULL` and the UI's placeholder treatment is the designed state, not a degradation. Real-file ingestion arrives with the deferred binary-backend decision.
- Uses: `claim` (2.2 payload), BlobStore protocol from 2.5 (reuse the same module — do not create a second).

### UX notes

- UX-DR5: tab label `Photos (n)` — the count in the label is part of the design contract.
- UX-DR10 (read-only photo viewer modal) and UX-DR11/NFR-3 (no native dialogs; empty/loading/error states) govern.
- UX-DR12 + Story 1.1 ruling: prototype palette is **LIGHT and canonical** (epics.md "dark console aesthetic" is a documented discrepancy); cards and modal ride the 1.1 tokens with the dense-card style.
- Grid follows the prototype's `.pgrid`/`.phcard` feel: compact cards, thumbnail on top, caption bold, source muted.

### Testing requirements

- Unit: payload assembly (count/rows agreement, scope, no-binary signal).
- Vitest: grid, placeholder, count, empty state, read-only viewer.
- E2E (AD-15): `e2e/stories/2-6-incident-photos.spec.ts` tagged `@story:2-6 @epic:2`, exactly one `@smoke` happy path, per-spec DB reset; covers grid + viewer + empty state. **The story cannot move to `review`/`done` until this spec passes.**

### Project Structure Notes

- Server: migration in `server/data/` versions; payload assembly in `server/services/claims/`; BlobStore from `server/services/` (shared with 2.5).
- Web: `web/src/features/claim-detail/photos/` — `PhotosTab.tsx`, `PhotoCard.tsx`, `PhotoViewerDialog.tsx`; the tab-label count comes from the same `claimDetail` payload the tab bar (2.2's `DetailTabs.tsx`) already consumes — update that component to append the count.
- Reuse the Dialog patterns established by 2.5's document viewer for visual and a11y consistency.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 2.6]
- FR-DET-4 (read-only viewers) + NFR-3: [Source: _bmad-output/planning-artifacts/epics.md#Functional Requirements / #NonFunctional Requirements]
- AD-7 / AD-9 / AD-11 / AD-12 / AD-15 + Binary-storage convention: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules / #Consistency Conventions]
- `photo` in the ERD (CLAIM evidences PHOTO): [Source: ARCHITECTURE-SPINE.md#Core entity ERD]
- Deferred binary backend: [Source: ARCHITECTURE-SPINE.md#Deferred — "Binary storage backend"]
- Prototype design/data contract: [Source: docs/Workers_Comp_Prototype.html — tab label 1185, photosHTML 1599–1603, openPhoto 1613, ALL_CLAIMS 632]
- Design-token light-palette ruling: [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]

## Dev Agent Record

### Agent Model Used

claude-opus-5[1m] (Claude Opus 5, 1M context), via the `bmad-dev-story` workflow.

### Debug Log References

- **The seeded book has no photoless claim, and the story assumed it might.** Task 1's subtask says "claims with empty arrays get no rows"; there are none — all 100 claims carry two to four photos, 293 in total. AC 3's empty state is therefore unreachable against the dev seed, exactly as Story 2.5's document empty state was, and it is covered by a vitest fixture plus an e2e test that deletes one claim's rows through `psqlQuery`. Two tests assert the premise itself (`test_photo_migration.py` over the seed file, `test_photos_tab.py` across the two seed files) so a portfolio change that makes the state reachable fails loudly rather than leaving two synthetic tests looking like overkill.
- **`WC-20017` is not in Kaya Johnson's book.** The first draft of `test_photos_tab.py` used the prototype's own first claim id as a fixture and got a 404 from every API test in the file. Replaced with `a_claim_in(persona)` reading `seed_fixture.claims_for`, which is Story 1.4's rule ("derive from the seed at test time") applied to a claim id rather than to a count — and the same rule the e2e oracles already follow.
- **`PATCH /claims/{id}` is a real verb, so the read-only assertion had to be narrower than 2.5's.** A first draft asserted 405 for POST/PUT/PATCH/DELETE on the case-file route and failed on PATCH with a 422: Story 2.3 owns that verb for the six inline fields. The parametrisation dropped to POST/PUT/DELETE and the docstring says *why* PATCH is absent — its `extra="forbid"` body is what keeps a photo field out of it, asserted where that whitelist lives.
- **`photo` shadowed a `sa.Table` in migration 0021** — the shape-check loop binds `photo` per row and the table object was bound to the same name below it. mypy caught it (`"dict[str, Any]" has no attribute "insert"`); renamed to `photo_table`. Worth recording because the migration would have run correctly and the error was a type-checker finding, not a test one.
- **Two vitest tests were wrong, not the components.** `userEvent.tab()` from the document root lands on the first tab button, not the first photo card, so the keyboard test asserted the wrong element — rewritten to focus the card and assert its tag is `BUTTON`. And a 500 raced the shared client's two 5xx retries; swapped for a 403, which is the lesson `ClaimDetailPane.test.tsx` already records from Story 1.4.

### Completion Notes List

- **AC 1 — the count is published, and there is a fixture whose job is to prove it.** The prototype writes ``Photos (${c.photos.length})`` into the tab bar while `photosHTML` maps the same array into cards; that is consistent only because both read one global object. Here the tab strip and the grid are different components, so `count` crosses the wire and `photos_block` is the only thing that computes it. The assertion that matters is not the agreement test — a component reading `photos.length` passes that against any faithful payload — but `PHOTOS_BLOCK_MISCOUNTED`, a payload that disagrees with itself, where only a component reading `count` answers 9. `count` and `hasBlob` were added to `noDerivation.test.ts`'s derived-field list for the same reason.
- **`hasBlob` and `blobUrl` are two fields because a volume-backed store needs them to be.** `BlobStore.url` may answer `None` *by design* — that is in the protocol Story 2.5 wrote, and the whole point of it being optional there. So a UI inferring "no photo" from a null URL would render the placeholder across a grid of real photographs the day a deployment picks a volume over MinIO. The card has three thumbnail states rather than two (`absent`, `unlinked`, the image), and the unit tests exercise both stores.
- **Nothing reads bytes to build a card.** `_PresigningStore` in the tests raises on `get`, which is as much of the assertion as the URL is: a grid of thumbnails must not pull image data through the API container.
- **No `sort_order` column, deliberately, and it is the one place this story departs from Story 2.5's table.** `path_required_form` needed an explicit number because three independent reference lists share one table; a claim's photos are one list, so `photo` follows `document` and `timeline_event` — insertion order preserved by the identity column, read back by `id`. There is no other total order available: 54 captions repeat across the book and two photos of one claim routinely share a source line.
- **No `version` column either, and the grant is what makes that true rather than hopeful.** AD-4's compare-and-swap arbitrates concurrent writers and there are none: nothing in Epics 1–8 uploads, annotates or deletes a photo. Migration 0020 grants `SELECT` and nothing else, on `glossary_term`'s and `path_required_form`'s precedent — so "the viewer is read-only" is the shape of the schema rather than a discipline the components keep. `PhotoViewerDialog` imports no mutation hook because there is none to import.
- **`source` keeps its date fragment inside the string.** "Plant safety, 03/22" is a provenance sentence: it carries no year, its day is not always the incident's, and the book holds 232 distinct source strings. Splitting it would have produced a `DATE` column that is wrong a good fraction of the time beside a `source` that had lost half its meaning — recorded as deferred work, where it belongs with the ingest path that would fix it properly.
- **A card is addressed by `photo.id`, not by an array index.** `openPhoto(c, i)` reads `c.photos[i]`; the dialog here takes the row's own surrogate and looks it up, so a payload that changed under an open viewer renders nothing rather than the wrong photograph under the right caption. The cards are `<button>`s for Story 2.5's reason — the prototype attaches `data-pi` to a `div` and delegates, which makes its photo viewer unopenable without a mouse.
- **The viewer fetches nothing, and that is the deliberate opposite of Story 2.5's call.** A document sheet is a dozen rows of the claim's data assembled per document, so 2.5 made it a query of its own rather than multiplying the console's most-fetched payload. A photo's viewer shows three fields the grid is already rendering behind the dialog, so a query for them would be a round trip for data the client holds. Asserted by counting `fetch` calls across opening the dialog.
- **The tab has no loading or error state of its own (NFR-3, AD-9).** The photos ride the shared `claimDetail` query, so there is one request, one skeleton and one error message for the whole case file. A skeleton here would imply a fetch that does not happen — which is a worse dishonesty than a missing one, because it would tell a handler the console was waiting on their evidence when it was waiting on nothing. Two tests pin it from the pane's side.
- **📷 does not cross the wire.** The prototype writes the emoji into `photosHTML` and again into `openPhoto`; the Enums convention keeps display metadata in the browser, which is the same argument that kept `PATH_META`'s labels and hex colours out of Story 2.5's seed. A test greps the whole payload for the glyph.
- **Story 2.2's Photos seam was re-pointed, not deleted**, in both the vitest suite and the 2.2 Playwright spec (1.5/1.6/2.1/2.3/2.4/2.5's precedent). `test("the Photos tab carries no count until Story 2.6 has a table to count")` became the stronger assertion it was standing in for, and the seam table's row was replaced by a test that the *panel* renders — a seam assertion goes on passing while a tab renders nothing at all. **Epic 2's tab surface is now closed:** the two remaining seams both name an epic rather than a story.
- **The scope guarantee is pinned at the repository, not at the endpoint.** `claim_detail` refuses an out-of-scope claim before `select_photos` is reached, so deleting `employer_scope` from that query would leave every API test green — precisely the state Story 2.5's code review found for `select_document`. The assertion lives in `test_scoped_repository.py`, where this module's "every query applies the filter" invariant lives.
- **Tests:** 781 server (26 new — the seeded rows against a second, independently written parse of the prototype's HTML, the count and per-claim range, the FK, the SELECT-only grant, the block's rows and order, the count from both ends, the block across all four stages, a supervisor's read, the AD-7 refusal against a computed cross-scope claim, both `BlobStore` implementations plus the no-store and null-key cases, the count invariant by construction, the 📷 grep, the three 405s, and the repository scope test). 227 vitest (16 new — the grid, ids over indices, the label from `count` including the miscounted payload and the unopened tab, all three thumbnail states, the empty state and its zero label, the viewer's three rows, its read-only structure, its ✕, its silence on the network, keyboard reachability, and both pane-level states). 86 Playwright (5 new, `@story:2-6 @epic:2`, one `@smoke`; the count includes the DB-reset setup project).
- **Verified live.** e2e stack (`compose.e2e.yaml`, :8081) rebuilt with the new API and SPA: full suite **86/86**, the 13-test `@smoke` set and the 47-test `@epic:2` close-out all green against a freshly reset stack. `alembic check` reports no drift and a `downgrade 0019` → `upgrade head` round trip completes clean. `npm run generate:api` leaves `src/api/schema.d.ts` byte-identical on a second run. ruff + format + mypy clean; eslint clean (8 pre-existing warnings, none new); tsc clean for both `web/` and `e2e/`. No new runtime or dev dependency.

### File List

**New — server**

- `lineworker/server/data/versions/20260812_0020_photo.py`
- `lineworker/server/data/versions/20260812_0021_seed_photos.py`
- `lineworker/server/data/seed/extract_prototype_photos.py`
- `lineworker/server/data/seed/photos.json`
- `lineworker/server/services/claims/photos.py`
- `lineworker/server/tests/test_photo_migration.py`
- `lineworker/server/tests/test_photos_tab.py`

**Modified — server**

- `lineworker/server/data/models/core.py` (`Photo`)
- `lineworker/server/data/models/__init__.py` (export)
- `lineworker/server/data/repositories/claims.py` (`select_photos`)
- `lineworker/server/services/claims/detail.py` (the `photos` block on the case file)
- `lineworker/server/api/routers/claims.py` (two response models, the block on `ClaimDetailResponse`)
- `lineworker/server/tests/test_scoped_repository.py` (the photo read's own scope test)

**New — web / e2e**

- `lineworker/web/src/features/claim-detail/photos/PhotosTab.tsx`
- `lineworker/web/src/features/claim-detail/photos/PhotoCard.tsx`
- `lineworker/web/src/features/claim-detail/photos/PhotoViewerDialog.tsx`
- `lineworker/web/src/features/claim-detail/photos/PhotosTab.test.tsx`
- `lineworker/e2e/stories/2-6-incident-photos.spec.ts`

**Modified — web / e2e**

- `lineworker/web/src/api/claims.ts` (the block's two types, and why there is no hook)
- `lineworker/web/src/api/schema.d.ts` (regenerated)
- `lineworker/web/src/features/claim-detail/DetailTabs.tsx` (the photos panel and the label's count; the seam entry removed)
- `lineworker/web/src/features/claim-detail/ClaimDetailPane.tsx` (renders the tab, passes the count)
- `lineworker/web/src/features/claim-detail/ClaimDetailPane.test.tsx` (2.2's Photos seam assertions re-pointed)
- `lineworker/web/src/features/queue/noDerivation.test.ts` (`count`/`hasBlob` added to the derived fields; the photos folder named in the scan assertion)
- `lineworker/web/src/test/api-mock.ts` (`PHOTOS_BLOCK` and its three variants; `photos` on the four case-file fixtures)
- `lineworker/e2e/fixtures/seed.ts` (the 2.6 oracles: `expectedPhotosFor`, `claimWithMostPhotos`)
- `lineworker/e2e/stories/2-2-case-header-stage-adaptive-overview.spec.ts` (the Photos seam assertion re-pointed)

**Modified — repo**

- `_bmad-output/implementation-artifacts/deferred-work.md` (three items)
- `_bmad-output/implementation-artifacts/2-6-incident-photos.md` (this file), `_bmad-output/implementation-artifacts/sprint-status.yaml`

### Change Log

- 2026-08-12: Ran a regression-scoped review of **Story 2.4** before Epic 2 sign-off, since this story and 2.5 between them modified 12 of 2.4's 39 files. No regressions: 2.4's four pinned fixes are intact and each still fails on revert, and the seam assertions this story re-pointed (`ClaimDetailPane.test.tsx`, the 2-2 spec, `noDerivation.test.ts`) got stronger rather than weaker. Four findings in 2.4's own surface were fixed and are recorded in that story's Change Log; two of them touched files this story also edits, so they land in the same commit.

- 2026-08-12: Addressed code review findings — **5 findings, 5 fixed, 0 deferred, 0 dismissed** (2 in this story, 2 in Story 2.5's files, 1 fixture consistency note; all rated low, no correctness defect in shipped behaviour). The one worth reading is the third: the e2e spec's "the viewer shows the photo it was opened by" negative assertion compared a *caption* row against a *source* string and therefore could not fail. The obvious repair — compare sources — would have been **worse than the bug**, because 29 of the 100 seeded claims have two photographs filed by the same person on the same day, so `photos[0].source === photos[1].source` verbatim and the assertion would fail against a correct viewer; `services/claims/photos.py` says exactly that about the data, and the first draft of the spec did not read it. The second repair (the same negative on the caption) is sound but *redundant* — captions are unique within every claim, so the positive assertion above it already excludes the wrong one — which was verified by breaking the viewer two different ways and watching the spec go red at the positive assertions both times, never at the negative. It was deleted rather than kept: a guard that cannot fail on its own is coverage in appearance only, which is the defect the original line actually had. Also fixed: a `CallerContext` parameter on `_overview` that nothing had read since Story 2.5's code review hoisted its last scoped query out — now pinned by a source-level guard over all of `services/`, verified to fail when the parameter is put back; a document viewer whose subtitle read "Loading…" above its own error message; a comment in 2.5's extractor promising order-independent key matching that its positional regex does not provide; and four case-file fixtures carrying `thresholdsVersion: 2` beside `pathVersion: 3`, a pair the server always makes equal, now one constant. Gate re-run green: ruff + format + mypy clean, **pytest 782**, **vitest 227**, **Playwright 86/86** plus the 13-test `@smoke` set and the 47-test `@epic:2` close-out against a rebuilt e2e stack.

- 2026-08-12: Story 2.6 implemented end to end — `photo` created and seeded with the prototype's 293 incident and site photographs across all 100 claims via a new extractor, with a `SELECT`-only grant that makes "read-only" a property of the schema rather than of the components. The case file gained a `photos` block carrying the cards and the **server's** count, with `hasBlob`/`blobUrl` as two separate facts behind the one `BlobStore` protocol so a presigning store and a mounted volume are already distinguishable. The Photos tab replaced Story 2.2's last Epic 2 seam with a ported grid, a `Photos (n)` label fed from the payload, three honest thumbnail states and a read-only shadcn Dialog that fetches nothing. Epic 2's tab surface is closed: the two seams still standing belong to Epic 3 and Epic 6. Full gate green: ruff + format + mypy clean, pytest 781, vitest 227, Playwright 86/86 plus the 13-test `@smoke` set and the 47-test `@epic:2` close-out against a rebuilt e2e stack. Status → review.
