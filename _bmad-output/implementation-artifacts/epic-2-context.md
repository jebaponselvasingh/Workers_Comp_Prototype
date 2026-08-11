# Epic 2 Context: Handler Caseload & Case File

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Epic 2 builds the handler's daily working surface on top of Epic 1's scoped, persistent foundation: a prioritized, filterable, stage-grouped claim queue on the left and a complete case file in the center. It delivers server-computed priority scoring with JDM-owned weights, a case header with risk gauge, a stage-adaptive Overview, audited inline editing of clinical and classification fields, the interactive body-map injury diagram with multi-injury capture, path-classified statutory forms with document viewers, and incident photos. It matters because it is the first epic where a handler can actually *work* — and because it establishes the patterns every later claim mutation follows: PATCH-shaped commands with optimistic concurrency, single-source derivations that keep queue and detail agreeing, and timeline events emitted by the owning service. The Bills & Payments and AI Insights tabs exist in the tab bar but show explicit empty states until Epics 3 and 6 fill them.

## Stories

- Story 2.1: Prioritized, Filterable Claim Queue
- Story 2.2: Case Header & Stage-Adaptive Overview
- Story 2.3: Audited Inline Field Editing
- Story 2.4: Interactive Injury Diagram
- Story 2.5: Documents, Employee ID & Statutory Forms
- Story 2.6: Incident Photos

## Requirements & Constraints

- **Queue.** Only the persona's scoped claims, grouped into four collapsible stage sections (intake / investigation / treatment / settled) with counts and per-stage empty states; the first claim auto-selects on load. Eight operational filters (All / Active / High risk / Fraud / Litigation / Payment due / Surgery / SIU) re-filter server-side and return grouped, priority-sorted results. Cards show claim ID, days open, risk dot, worker name, FRAUD/LITIG/PAY DUE/SIU badges, truncated injury type, stage pill, and employer short name. Selecting a card drives both the detail and copilot panes; queue and detail must never disagree on a flag.
- **Priority scoring.** Computed once server-side, ordering the queue so litigation, SIU, RTW-blocked, pending-approval, payment-due, and surgical claims rise; severity and days-open contribute, settled claims sink. Weights are tunable data, not code constants. The top three cards above the threshold carry a priority marker.
- **Case detail.** Header carries worker name, meta line, injury summary, a badge row (stage pill plus conditional fraud / litigation / surgery / OSHA), and a risk gauge colored by band. Overview always opens with a four-step stage stepper and then adapts its body to the claim's stage — intake shows a document checklist of received vs. missing required forms, investigation shows injury plus financial/reserve cards, treatment shows a phase banner with paid-vs-reserve and care/RTW coordination status, settled shows the payout breakdown and outcome summary. Treatment phase and coordination status are server-derived, never computed in the SPA.
- **Inline editing.** Injury type, cause, body part, ICD-10, disability, and recovery window are editable inline. Every edit is a versioned, audited command; dependent values (risk, severity band) recompute server-side and the affected cached queries invalidate so queue, header, and stat tiles move together. A version conflict returns a 409 carrying the fresh entity; the client rolls back and renders the fresh value inline at the edited field, with no silent retry or client-side merge.
- **Injury diagram.** Ported body-silhouette SVG with severity-colored markers and a pulsing primary injury, plus ICD-10 diagnosis, prognosis, a numbered treatment plan, and restrictions. Secondary injuries are addable via popover (body part + injury type + severity 0–100) and removable, both audited. Severity and body-part changes recompute severity band and risk server-side.
- **Documents & statutory forms.** Required forms come from reference data for the claim's classified path (A minor / B follow-up / C fatality) with IDs, descriptions, timing, and download links. Path classification must be a real derivation with its parameters in rules data — closing the prototype's always-Path-B gap — with tests covering all three paths. Documents and photos open in read-only viewer modals; FROI renders full injury detail, others a summary sheet.
- **Universal.** Explicit loading, empty, and error states on every list, tab, and viewer; no blocking native dialogs — validation is inline, errors are toasts. Every mutation persists with an audit event in the same transaction. Each story ships one Playwright spec covering its acceptance criteria against the composed stack; a story cannot reach review or done without it.

## Technical Decisions

- **Nothing derived is computed in the browser.** Priority score, operational flags, risk, severity band, days open, treatment phase, coordination status, and path classification each have exactly one registered computing function server-side; consumers call it. Priority scoring and any worklist aggregation live in the worklist service; claim-detail and injury writes are owned by the claims service.
- **Two-tier rules.** Priority weights, thresholds, bands, and path-classification parameters live in versioned, effective-dated JDM documents; typed Python owns the score arithmetic and classification formula and reads its tunables from JDM. A rule element lives in exactly one tier — never both.
- **Audited command writes.** All mutations go through service-layer PATCH-shaped commands carrying `expected_version`, compare-and-swapped on the row, emitting a fixed-shape audit event with before/after JSONB diffs in the same transaction. Routers stay thin. Append-only stores are exempt from versioning.
- **One write-owner per entity.** `timeline_event` rows are emitted only as a side effect of the owning service's command — never written directly by a router or a second service. The new tables this epic introduces (`timeline_event`, `document`, `treatment_plan_step`, `additional_injury`, `photo`) are created by story migrations and seeded from prototype data; Alembic is the only migration path.
- **Pagination machinery lands here.** Epic 2's queue is the first genuinely pageable list, so the list convention — `{items, nextCursor, total?}` with real cursor pagination plus `filter[…]`/`sort` params — should be built properly once in this epic rather than improvised, and the placeholder envelopes shipped in Epic 1 reconciled against it (`total` from a real count, or dropped).
- **Frontend state discipline.** All server state through TanStack Query with keys in the shared keys module. Optimistic updates only for user-entered scalars; server-derived values wait for the server. Selection state is local UI state.
- **Conventions to honor.** Money as integer cents end to end, formatted only in the UI; ISO dates; snake_case enum values with UI-owned display labels; camelCase JSON via Pydantic aliases consumed by the one generated OpenAPI client; claims addressed by business ID `WC-nnnn`; RFC 9457 problem+json errors mapped to toasts or inline messages.
- **Binaries.** All document and photo bytes flow through the single `BlobStore` protocol (`put/get/delete/url`). The backing implementation is a volume for now and must remain swappable — no caller may reach past the protocol.
- **Scope is still a server guarantee.** Every queue, detail, document, and photo read goes through repository methods that apply the caller's employer filter unconditionally. No endpoint accepts caller-supplied scope.

## UX & Interaction Patterns

- **Three-pane handler layout:** Queue (left) · Case Detail (center) · Copilot (right). The copilot pane is scaffolded and claim-context-aware in this epic; its behavior arrives in Epic 6.
- **Queue:** collapsible stage groups with emoji headers and counts, priority markers on the top-3 over threshold, risk dot plus flag badges on each card, and a clear selection highlight.
- **Detail:** six-tab bar (Overview · Injury Diagram · Bills & Payments · Documents & ID · Photos (n) · AI Insights), stage stepper marking done/current, and a semicircular risk-gauge SVG.
- **Injury diagram:** port the prototype's silhouette SVG assets and marker animation rather than redrawing; add-injury is a popover, secondaries removable with an ✕.
- **Modals:** document and photo viewers are strictly read-only.
- **Visual identity:** preserve the prototype's dark, information-dense console aesthetic and ok/warn/error status semantics via Tailwind design tokens over vendored shadcn/ui components.

## Cross-Story Dependencies

- **Epic 1 → all of Epic 2.** The scaffold, Compose stack, CI gate, seeded claim portfolio, audit table, and the single auth/scope-context dependency are prerequisites. Story 2.1 completes the login flow's handler path (auto-select first claim) begun in 1.3.
- **2.1 → 2.2–2.6.** Card selection is the mechanism that drives every detail tab; all later stories assume a selected claim.
- **2.2 → 2.3, 2.5.** The `timeline_event` and `document` tables created here are what inline edits write into and what the Documents tab reads.
- **2.3, 2.4 → 2.1 and the top bar.** Edits that change risk or severity must refresh the queue card, the risk gauge, and Epic 1's caseload stat tiles through the same derivations — no second computation path.
- **Forward:** Epic 3 fills the Bills & Payments tab, the reserve-check card referenced by the investigation and treatment Overviews, and the action checklist; Epic 5's dashboard and top-30 worklist reuse this epic's priority scoring and derivations verbatim; Epic 6 fills the AI Insights tab and the copilot pane. When Epic 6 introduces embeddings, this epic's claim-mutating commands must call the mark-stale command in the same transaction — leave the seam.
- **Known deferrals that constrain scope:** the real business definitions for the demo-derived flags (`siu_review`, `payment_due`) are undecided — only *where* they are computed is fixed; statutory form PDFs and per-jurisdiction coverage are placeholders pending validation; the blob-storage backend choice is open behind the protocol.
