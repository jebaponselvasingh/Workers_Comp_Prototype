# Story 7.5: Dataset & Chart Export

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a data analyst,
I want to export what I'm looking at,
so that offline analysis and audit requests are self-service.

## Acceptance Criteria

1. **Given** any filtered claim list or chart in the analyst workspace, **when** Export is clicked, **then** the server generates a CSV/XLSX of exactly the current scoped, filtered dataset or the chart's aggregate data (FR-AN-6) — generated server-side under the caller's scope context, never from client-held data (AD-7).
2. **Given** an export, **when** it completes, **then** an audit event records who exported what (entity, filter set, row count — content-free) per AD-4, since exports move PHI out of the system (NFR-5).
3. **Given** a large export, **when** it generates, **then** it streams or paginates without timing out, and the UI shows progress with a non-blocking completion notification (NFR-3).

## Tasks / Subtasks

- [ ] Task 1: Export service in `services/worklist` (AC: 1)
  - [ ] One export command taking `(caller context, export target, format, segmentation filter)` where target is either a claim-dataset export or a named chart-aggregate export (each analyst chart's aggregate function is reusable from the service layer per 7.4's note — the export calls the same function the chart endpoint calls, so exported figures can never diverge from displayed figures, AD-10 spirit)
  - [ ] Dataset export re-runs the scoped, filtered query server-side under the caller's AD-7 context — the request carries only the filter spec (7.3 schema) + target, **never rows, IDs-to-include, or client-held data**; the response is exactly what the caller could see on screen
  - [ ] Formats: CSV (streamed via generator) and XLSX (openpyxl/xlsxwriter — pick one, document in this file's Dev Agent Record; not architecture-pinned); money rendered from integer cents with explicit column headers/units; dates ISO-8601; enum values as DB snake_case with a header note (export is a data product, not a UI)
  - [ ] Export files are generated per-request and streamed to the caller — **not** persisted server-side, not written to the BlobStore, no temp file left on disk (PHI posture, AD-11); role-gated to `analyst`
- [ ] Task 2: Export audit (AC: 2)
  - [ ] The export command emits an AD-4 audit event through `services/audit` in the same unit of work as the export query: fixed schema with `action: export`, `entity` = export target, and a content-free payload of `{filter set, format, row count}` — **no claim values, no worker names, no file content** in the event (AD-11 log ban applies to audit payloads here by design of the AC)
  - [ ] Emission happens only after the dataset is materialized/streamed successfully (row count is real, not estimated); a failed export emits nothing but returns problem+json
  - [ ] structlog gets IDs and event names only — never filenames-with-PHI, never row content (AD-11)
- [ ] Task 3: Large-export behavior (AC: 3)
  - [ ] CSV streams row batches via the async generator (server-side keyset/cursor iteration per list conventions — no `OFFSET` scans, no loading the full dataset into memory); XLSX builds in streaming/write-only mode; a config knob (pydantic-settings) caps max export rows with a clear problem+json over-limit response
  - [ ] UI: Export buttons on the workspace claim lists and chart cards; in-progress state (button-level spinner/disabled — only the affected control, never the workspace), browser-native download of the stream, non-blocking toast on completion or failure (UX-DR11, NFR-3)
- [ ] Task 4: Wire export affordances across the workspace (AC: 1)
  - [ ] Add Export (CSV/XLSX choice) to: the 7.1 flagged-claims list + fraud breakdown charts, 7.2 trend charts, 7.3 drill-through claim lists, 7.4 financial charts — each passing its current target + active segmentation filter; nothing exports from Epic 5 supervisor screens (analyst capability only, this story)
- [ ] Task 5: Tests (AC: all)
  - [ ] pytest: exported rows exactly match the scoped+filtered query for the same context (equality test against the list endpoint); scope enforcement (scoped analyst persona export never contains out-of-scope employers — under test); audit event emitted with correct content-free payload and real row count; over-limit and failure paths; CSV/XLSX format correctness (cents→decimal rendering, ISO dates)
  - [ ] Vitest: export control states (idle/in-progress/done/failed), non-blocking notifications
  - [ ] Playwright `e2e/stories/7-5-dataset-chart-export.spec.ts` tagged `@story:7-5 @epic:7`, one `@smoke` happy path: analyst login → apply a segmentation filter → export the filtered claim list as CSV → downloaded file parses and row count matches the on-screen total → (via API assertion) an export audit event exists; plus a chart-aggregate export test

## Dev Notes

> **Prerequisite advisory: run `bmad-ux` for the analyst workspace surfaces before starting this epic (readiness report, standing advisory #1). If a UX spec exists by dev time, it supersedes the layout suggestions here.** There is no prototype precedent for export affordances; where layout is open below, reuse Epic 5's established dashboard patterns and shadcn/ui primitives rather than inventing new idioms.

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

Server-side CSV/XLSX export of exactly-what-you-see: filtered claim datasets and chart aggregate data, audited as PHI egress. It is **not**: scheduled/emailed reports, export to external systems (DMS, BI tools, SMTP delivery — **real integrations are the deferred BRD §7.4-8 decision with its own compliance review**; this story's egress is a user-initiated browser download only), a saved-report library or export history UI (the audit trail is the record), PDF rendering, or a generic reporting engine. **Never a client-side dump**: the AC is explicit that export is generated server-side under the caller's scope context — exporting the TanStack Query cache or DOM table is prohibited (AD-1/AD-7). No new analytics — export rides the aggregate functions 7.1–7.4 already built.

### Architecture compliance (binding ADs for this story)

- **AD-1 / AD-7:** the export re-executes the scoped, filtered query server-side; the request carries a filter spec, never data; the caller's repository scope context bounds every row — a tampered filter can only narrow, never widen (7.3 invariant).
- **AD-4:** the export audit event goes through a `services/audit`-emitting command with the fixed audit schema; `audit_event` stays INSERT-only for the app role. Export mutates no entity, so no version CAS applies — but the audit emission is non-optional and content-free.
- **AD-11:** export output is a PHI-bearing file leaving the system — that is the point of the audit AC. Server side, follow the AD-11 posture: no persisted export artifacts, no PHI in structlog, generation happens in-process and streams out over the TLS ingress. What happens on the analyst's machine after download is governed by organizational policy, not this system; note the deferred real-integrations decision (BRD §7.4-8) covers any future managed egress channel.
- **AD-12:** no ownership changes — export reads through `services/worklist` aggregates/queries; the audit event is the only write, through its owning service.
- **AD-9:** export state is local UI state (button-level); no server-state caching of export payloads.
- **AD-15:** story spec + done-gate (see Testing requirements).

### Data notes

Creates **no tables** (uses the existing `audit_event` from Epic 1 — append-only, fixed schema). Reads: everything the 7.1–7.4 aggregates and the 7.3-filtered claim list read, through the same service functions. New config: `export_max_rows` (and optionally a wall-clock cap) in the single pydantic-settings module — never a scattered env read. Dependency note: e2e download assertions need Playwright's download handling against the streamed response; keep the `@smoke` export small (seeded data is ~100 claims, well under any limit).

### UX notes

No UX-DR covers export (the Epic 7 gap). Borrow: UX-DR11 (non-blocking toasts for completion/failure; only the affected control shows busy state), Epic 5 card idioms (an Export affordance in the card header/toolbar of each chart and list). Format choice (CSV vs XLSX) as a small menu on the affordance — don't build a modal wizard. ⚠️ Design-token ruling: the epics' UX-DR12 "dark console aesthetic" is a **documented discrepancy** — the prototype palette is LIGHT and canonical; use Story 1.1's Tailwind tokens.

### Testing requirements

- pytest: export-equals-view equality, scope containment (property-test worthy: for any filter, export rows ⊆ caller's scoped claims), audit payload shape + content-free assertion (no PHI field values in `before`/`after`/payload), row-count accuracy, over-limit problem+json, streaming behavior (memory-bounded generator), format fidelity.
- Vitest: control states + notifications.
- Playwright `e2e/stories/7-5-dataset-chart-export.spec.ts` tagged `@story:7-5 @epic:7` (anchored grep), exactly one `@smoke` happy path with a real download parse + audit assertion, against the freshly reset e2e stack (seed is synthetic — downloaded artifacts in CI are non-PHI per AD-15's seed rule). **The story cannot move to review/done until this spec passes (AD-15 done-gate).**

### Project Structure Notes

- Export command + format writers in `server/services/worklist/` (a small `export/` module is fine); audit emission via `server/services/audit/`; thin streaming route in `server/api/`; UI affordances in `web/src/features/dashboard/` (analyst subfolder).
- Depends on 7.1–7.4 (targets + filter schema + reusable aggregate functions). The XLSX library choice and `export_max_rows` default are dev decisions — record them in the Dev Agent Record so later stories don't churn.
- Epic 8's purge cascade does not touch exports (nothing persisted) — by design; keep it that way.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 7.5]
- FR-AN-6, NFR-3, NFR-5: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]
- Epic 7 UX gap + advisory: [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Standing Advisories item 1]
- Audit schema + INSERT-only role: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-4]
- PHI-class stores, log ban, purge posture: [Source: ARCHITECTURE-SPINE.md#AD-11]
- Scope enforcement on every data path: [Source: ARCHITECTURE-SPINE.md#AD-7]
- Deferred real integrations incl. export/email/DMS (BRD §7.4-8) and data-egress row: [Source: docs/Architecture-LINEWORKER.md#7. Security & compliance / #10. Deliberately deferred]; [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Additional Requirements (BRD §7)]
- 12-factor config module: [Source: ARCHITECTURE-SPINE.md#Consistency Conventions]
- E2E gate + non-PHI CI artifacts rule: [Source: ARCHITECTURE-SPINE.md#AD-15]
- Design-token ruling (light palette canonical): [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
