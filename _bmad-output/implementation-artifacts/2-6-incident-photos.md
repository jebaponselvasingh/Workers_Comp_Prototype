# Story 2.6: Incident Photos

Status: ready-for-dev

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

- [ ] Task 1: Migration — `photo` table, seeded (AC: 1)
  - [ ] Alembic migration creating `photo` (surrogate int PK, `claim_id` FK, `caption`, `source`, `blob_key` nullable — image bytes ride the `BlobStore` protocol when real files exist; the prototype ships no binaries, placeholder rendering is expected)
  - [ ] Seed from each prototype claim's `photos` array — objects `{t, d}` map to `caption` (t) and `source` (d); claims with empty arrays get no rows — DATA source only, never code [Source: docs/Workers_Comp_Prototype.html ALL_CLAIMS line 632, rendered at photosHTML line 1599]
  - [ ] Snake_case naming per the canonical mapping; migration runs clean on a fresh DB (CI gate)
- [ ] Task 2: API — photos in the claim payload (AC: 1, 2)
  - [ ] Extend the claim-detail payload (2.2's endpoint) with the photos block: `photos: [{id, caption, source}]` and a count the tab label uses — count comes from the server payload, never a client-side length of a separately fetched list drifting from the tab (one source)
  - [ ] Photo content resolution through the `BlobStore` protocol (`url`/`get`) for rows with a `blob_key`; seeded rows have none — the API signals "no binary" and the UI renders the placeholder treatment (binary backend is a Deferred decision; the protocol boundary is what's binding)
  - [ ] Behind the AD-7 scope context; regenerate the OpenAPI client
- [ ] Task 3: Photos tab UI (AC: 1, 3)
  - [ ] Replace 2.2's Photos tab empty-state placeholder: tab label becomes `Photos (n)` from the server count [Source: docs/Workers_Comp_Prototype.html line 1185]
  - [ ] Grid of photo cards: thumbnail region (📷 placeholder treatment when no binary; real image via BlobStore URL when present), caption, source line [Source: docs/Workers_Comp_Prototype.html lines 1599–1603]
  - [ ] Explicit empty state when the claim has zero photos ("No photos on file.") — and the tab label reads `Photos (0)` (NFR-3)
  - [ ] Loading and error states for the tab (NFR-3); TanStack Query via the shared `claimDetail` key (AD-9 — no separate fetch idiom for one tab)
- [ ] Task 4: Read-only photo viewer modal (AC: 2)
  - [ ] shadcn/ui Dialog, read-only — no edit/delete affordances: large photo region (placeholder or BlobStore image), then Caption / Source / Claim (business ID) key-value rows [Source: docs/Workers_Comp_Prototype.html openPhoto line 1613]
  - [ ] Close via ✕ and backdrop click; no native dialogs (NFR-3)
- [ ] Task 5: Tests + E2E spec (AC: all)
  - [ ] Unit (server): photos block assembly (rows + count agree; scoped access; no-binary signaling)
  - [ ] Vitest: grid renders a card per photo with caption + source; placeholder thumbnail; empty state; tab count; viewer read-only rendering
  - [ ] `e2e/stories/2-6-incident-photos.spec.ts` tagged `@story:2-6 @epic:2`; `@smoke` happy path: select a seeded claim with photos → tab label shows the count → grid renders cards → click a card → read-only viewer shows caption/source/claim → close via backdrop; additional test: a seeded claim with zero photos shows `Photos (0)` and the empty state

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

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
