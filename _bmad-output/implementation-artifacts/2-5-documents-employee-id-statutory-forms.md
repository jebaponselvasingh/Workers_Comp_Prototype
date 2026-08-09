# Story 2.5: Documents, Employee ID & Statutory Forms

Status: ready-for-dev

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

- [ ] Task 1: Migration — `path_required_form` reference data (AC: 1)
  - [ ] Alembic migration creating `path_required_form` (surrogate int PK, `path` enum `a | b | c`, `form_code`, `form_name`, `description`, `timing`, `download_url`, `sort_order`)
  - [ ] Seed from the prototype's `PATH_DOCS`: Path A — C-2F, FAR-1; Path B — C-3, RFA-1W, C-4.3, C-11; Path C — AFF-1, C-64, C-65 (with their descriptions, timing strings, and URLs) — DATA source only, never code [Source: docs/Workers_Comp_Prototype.html lines 1542–1558]
  - [ ] Path display metadata (label, icon, description, banner colors) is **UI-owned** per the enums convention — port `PATH_META` semantics into the web feature, not the DB [Source: docs/Workers_Comp_Prototype.html lines 1559–1563]
  - [ ] Note: the seeded URLs are NY WCB / FNSB **placeholders**; per-jurisdiction validation of statutory form references is NFR-4 / a Deferred decision — do not attempt real sourcing here, do record the caveat in code comments
- [ ] Task 2: Path-classification derivation + JDM parameters (AC: 1, 2)
  - [ ] `services/derivations.claim_path(claim)` → `a | b | c`, registered (AD-10) — this **closes the prototype's always-Path-B gap** (`c.path || "B"`, line 1566): classify **C** when the claim indicates fatality (per the seeded data model: derive from the claim's severity/disability/outcome fields — e.g. a fatality indicator if 1.2's schema carries one, else the JDM-parameterized severity/outcome rule); **A** when minor/first-aid-only (severity score below the JDM minor threshold AND no lost time / no surgery / temporary disability); else **B**
  - [ ] All classification parameters (minor severity threshold, qualifying conditions) live in a ZEN JDM document (AD-8); the branching lives in typed Python — one tier each
  - [ ] The dev seed must yield at least one claim classifying to each path for testability; if the 100-claim dataset contains no fatality, add a **test fixture** claim in tests (not the seed) to cover C, and document that choice in the Dev Agent Record
  - [ ] Unit tests covering all three paths + boundary values of the minor threshold (AC 2 is explicit: tests cover A, B, and C)
- [ ] Task 3: API — tab payload + document content endpoint (AC: 1, 3, 4)
  - [ ] Extend the claim-detail payload (2.2's endpoint) with the Documents & ID block: classified `path` + its `path_required_form` rows; employee-ID card data (employee ID, policy number, DOI, handler, plant, state · region, name, role); documents list (from 2.2's `document` table: name, type, filed date)
  - [ ] `GET /api/claims/{claimBusinessId}/documents/{id}/content` — resolves file bytes/URL through the `BlobStore` protocol (`put/get/delete/url`) in `services/`; seeded documents have no real binaries yet (binary backend is Deferred), so the viewer's structured sheet is generated server-side from claim + document fields, with `blob_key` resolution wired for when real files exist — the protocol boundary is what's binding, not the backing store
  - [ ] All routes behind the AD-7 scope context; regenerate the OpenAPI client
- [ ] Task 4: Documents & ID tab UI (AC: 1, 3, 5)
  - [ ] Replace 2.2's tab empty state with: **required-forms card** — path banner (icon + label + description, path-colored left border/background per UI-owned meta) and one row per form: form-code chip, name, description, ⏱ timing, ⬇ Download link (opens `download_url` in a new tab) [Source: docs/Workers_Comp_Prototype.html lines 1565–1584]
  - [ ] **Employee ID card**: branded card with initials avatar, name, role, Employee ID / Policy / DOI / Handler grid, plant + state·region footer [Source: docs/Workers_Comp_Prototype.html lines 1589–1593]
  - [ ] **Claim documents list**: header with file count, rows showing type chip (C-1/INC/MED/WAGE/RTW/LEG per doc type), name, "Filed {date}", "View →" affordance [Source: docs/Workers_Comp_Prototype.html lines 1594–1596]
  - [ ] Explicit empty state when the claim has zero documents (NFR-3); loading/error states for the tab
- [ ] Task 5: Read-only document viewer modal (AC: 4)
  - [ ] shadcn/ui Dialog (read-only — no edit affordances): FROI type renders the full injury detail sheet (claim ID, policy, employee, employer/plant, DOI, filed, injury type, body part, ICD-10, cause, severity, AWW, handler, supervisor, OSHA recordable) ; other types render the summary sheet (claim ID, policy, employee, plant, filed, status, handler); signature footer rows [Source: docs/Workers_Comp_Prototype.html openDoc lines 1605–1612]
  - [ ] Sheet content comes from the Task 3 content endpoint (server-assembled, AD-1) — the SPA does not compose FROI fields from the claim object client-side
  - [ ] Close via ✕ and backdrop; no native dialogs anywhere (NFR-3)
- [ ] Task 6: Tests + E2E spec (AC: all)
  - [ ] Unit: path derivation A/B/C + thresholds (Task 2); required-forms lookup returns the right rows per path in `sort_order`; content endpoint FROI vs summary shapes; BlobStore protocol conformance test (volume impl)
  - [ ] Vitest: forms card renders per-path banner + rows; ID card fields; documents list + empty state; viewer read-only rendering for FROI and non-FROI
  - [ ] `e2e/stories/2-5-documents-employee-id-statutory-forms.spec.ts` tagged `@story:2-5 @epic:2`; `@smoke` happy path: open Documents & ID on a seeded Path-B claim → forms card lists C-3/RFA-1W/C-4.3/C-11 with download links → ID card renders → click a FROI row → read-only modal shows full injury detail → close; additional tests: a Path-A seeded claim shows the A form set; empty state on a zero-document claim (seed or fixture)

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

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
