# Story 5.5: Dashboard Drill-Through

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a WC supervisor,
I want to click any KPI, chart segment, or table row to see the claims behind it,
so that oversight leads to action instead of dead ends.

## Acceptance Criteria

1. **Given** any KPI card or chart segment, **when** clicked, **then** a scoped, filtered claim-list view opens showing the constituent claims, with the applied filter visible and clearable (FR-SUP-D).
2. **Given** a claim row in any dashboard table or drill-through list, **when** clicked, **then** a read-only claim view opens (header, overview, financials, documents — no edit affordances), because role gates capability (AD-7).
3. **Given** a handler row in the performance table, **when** clicked, **then** a drill-through lists that handler's caseload within the viewer's scope (FR-SUP-B).
4. **Given** any drill-through URL, **when** loaded directly, **then** filter state restores from the URL and scope re-resolves server-side — a scoped supervisor can never reach claims outside their book by editing the URL (AD-7, under test).

## Tasks / Subtasks

- [ ] Task 1: Filtered claim-list endpoint (AC: 1, 3, 4)
  - [ ] `GET /api/dashboard/claims` in `services/worklist` behind the repository scope context, accepting only whitelisted `filter[…]` params per list conventions (e.g. `filter[stage]`, `filter[severityBand]`, `filter[fraudFlagged]`, `filter[litigation]`, `filter[recoveryStatus]`, `filter[injuryType]`, `filter[employerId]`, `filter[state]`, `filter[handlerId]`, `filter[priority]` for the top-30 population) — one filter vocabulary covering every 5.1 KPI, 5.3 chart segment, and 5.2/5.4 row
  - [ ] Filter semantics resolve through the SAME registered derivations and JDM thresholds as the surface that was clicked (AD-10): clicking High Risk (≥ 65) must return exactly the claims that card counted — under test
  - [ ] Cursor-paginated `{items, nextCursor, total?}`; server-sorted (priority order by default); unknown/invalid filter values → 400 problem+json, never silently ignored
  - [ ] Scope is NEVER a filter param: employer scope comes only from the session's repository context; `filter[employerId]` intersects with (never widens) the caller's scope (AD-7)
- [ ] Task 2: Drill-through claim-list view (AC: 1, 3, 4)
  - [ ] New routed view in `web/features/dashboard/` (e.g. `/dashboard/claims?filter…`): claim cards/rows reusing the Epic 2 queue-card visual pattern (risk dot, flag badges, stage pill, employer short name) in a flat filtered list
  - [ ] Applied-filter chip(s) visible at the top with a clear (✕) control — clearing updates the URL and refetches; back button returns to the dashboard with its state intact
  - [ ] Filter state lives in the URL query string (restorable, shareable within the same scope); direct load reconstructs the view from the URL alone (AC 4); TanStack Query keys include the filter set (AD-9)
  - [ ] Loading/empty/error states (NFR-3) — an empty result (e.g. zero fraud flags in scope) is a defined state, not a blank page
- [ ] Task 3: Wire every dashboard surface (AC: 1, 3)
  - [ ] 5.1 KPI cards: each of the 10 navigates to the list with its filter (Total Claims → unfiltered scoped list; High Risk → severity-band filter; Total Paid/Reserve → the claim list that backs the sum, filter documented per card)
  - [ ] 5.3 charts: every donut slice / bar navigates with the segment's filter (settlement status, severity band, recovery status, injury type, employer, state); SLA tiles navigate to the tile's underlying cohort where meaningful (document any tile ruled non-navigable and why)
  - [ ] 5.2 handler rows → `filter[handlerId]` list (AC 3 — within viewer scope by construction); 5.4 rows → read-only claim view (Task 4)
  - [ ] All click targets keyboard-accessible with visible focus (real links/buttons, not div onClick)
- [ ] Task 4: Read-only claim view (AC: 2)
  - [ ] Routed view (e.g. `/dashboard/claims/WC-nnnn`) reusing Epic 2/3's claim-detail presentation components — case header + risk gauge (UX-DR4), overview, financials summary, documents list — with ALL edit affordances absent (no inline editors, no action buttons, no copilot pane; the copilot is claim-scoped handler UX and dashboard-scope copilot is architecture-deferred)
  - [ ] Read-only is enforced server-side, not just visually: supervisor/analyst roles are not authorized for claim mutation commands (role gates capability — AD-7); the view simply never renders edit controls
  - [ ] Out-of-scope claim ID in the URL → 404 problem+json rendered as an in-app not-found state (scope gates visibility: an out-of-scope claim is invisible, not forbidden — no existence leak)
- [ ] Task 5: Tests (AC: all)
  - [ ] Unit: filter → predicate mapping per filter key; High Risk drill-through result set count == 5.1's High Risk KPI on the same scope (same JDM threshold); handler filter returns only that handler's in-scope claims; invalid filter → 400
  - [ ] Scope-escape test (server): Jennifer Park requesting `filter[employerId]=<Boeing>` gets an empty/scoped-out result, and `GET` on a Boeing claim ID returns 404 — proven at the API level
  - [ ] Web: Vitest on filter-chip render/clear and read-only view (asserts no edit controls render)
  - [ ] Playwright `e2e/stories/5-5-dashboard-drill-through.spec.ts` tagged `@story:5-5 @epic:5`, one `@smoke` happy path: supervisor clicks a KPI card → filtered list with visible clearable chip → clicks a claim → read-only claim view; plus the AC-4 URL test: login as Jennifer Park, load a drill-through URL hand-edited to Boeing scope/claim → scoped-out result / not-found state (the "under test" clause of AC 4)

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

The interaction layer that makes Epic 5 an oversight tool: one filtered claim-list view, one read-only claim view, and click-wiring across every 5.1–5.4 surface. It creates NO new aggregates beyond the filtered list endpoint and NO new visual language — readiness advisory #2 flags drill-through as **new UX with no prototype precedent**, deliberately mitigated by composing existing patterns (Epic 2 queue-card styling for lists, Epic 2/3 detail components for the read-only view). Design attention is warranted: this is the one Epic 5 story where the prototype offers no screen to copy — keep it visually boring and consistent rather than inventing. It is NOT: an editable detail (handler workspace owns editing), a copilot surface (dashboard-scope copilot is architecture-deferred), or the analyst's universal drill-down/export (Epic 7 builds deeper on this same endpoint pattern).

### Architecture compliance (binding ADs for this story)

- **AD-7 (the load-bearing AD here):** scope re-resolves server-side from the session on every request — the URL carries filters, never scope; `scope_all` remains a tautology predicate; out-of-scope access is invisible (404), not role-branched. Role gates capability: supervisors/analysts read, never write. AC 4's URL-escape test is this AD's proof.
- **AD-10:** each drill-through filter reuses the exact registered derivation/JDM threshold of the surface clicked — the list a KPI opens must reconcile to the number on the card.
- **AD-1:** filtering, sorting, counting all server-side; the SPA passes filter params and renders items.
- **AD-9:** filter state in the URL + TanStack Query keys keyed by filter set (add to the shared `queryKeys` module); no client-side claim filtering.
- **Conventions:** `filter[…]`/`sort` query params, cursor pagination, business IDs `WC-nnnn` in routes, problem+json → inline states.

### Data notes

- **Tables created: none** (Epic 5 creates no tables). Reads: claim + display joins, document list (read-only reuse of Epic 2's endpoints where they fit), financial summaries via Epic 3's services.
- **Writes: none**; no AD-12 ownership impact. Prefer reusing existing scoped read endpoints (claim detail, documents) over minting parallel dashboard copies — the read-only claim view should hit the same GETs the handler view uses, scoped identically; only the filtered-list endpoint is new.

### UX notes

- UX-DR7's closing clause is this story: "every KPI/segment/row clickable for drill-through."
- Readiness advisory #2: drill-through views are new UX with no prototype precedent — filtered lists + read-only claim detail reusing existing patterns; design attention warranted at implementation time. Reuse Story 1.1 tokens and Epic 2 card/detail patterns; the filter chip is the only genuinely new element (style it like the existing badge/chip family).
- **Design-token ruling:** prototype palette is LIGHT and canonical; epics.md's "dark console aesthetic" is a documented discrepancy (Story 1.1 Dev Notes).
- NFR-3 / UX-DR11: loading, empty (with the active filter named in the empty message), error, and not-found states.
- Accessibility: drill targets are links/buttons with focus states; chart-segment clicks need discernible text (aria-label with the segment name/value).

### Testing requirements

- Unit: filter-predicate mapping, KPI↔list reconciliation, invalid-filter 400, scope-escape at the API level (Jennifer Park vs Boeing).
- Web: Vitest — filter chip lifecycle, read-only view renders zero edit affordances.
- E2E (AD-15): `e2e/stories/5-5-dashboard-drill-through.spec.ts` tagged `@story:5-5 @epic:5`, exactly one `@smoke` happy path (KPI → list → read-only claim), plus the URL scope-escape scenario AC 4 places explicitly under test. **Story cannot reach `review`/`done` until the spec passes** against the freshly reset e2e stack. Selectors: role first, `data-testid` second.

### Project Structure Notes

- Server: filtered-list query in `server/services/worklist/`; thin router in `server/api/`; no new services.
- Web: routes + views in `web/src/features/dashboard/`; reuse presentation components from `web/src/features/claim-detail/` for the read-only view (extract shared presentational pieces if they're currently entangled with edit logic — presentation/edit separation is the refactor this story is allowed to make); query hooks/keys in `web/src/api/`; regenerate the OpenAPI client.
- Depends on all of 5.1–5.4 (the surfaces being wired) and Epics 2/3 (components + read endpoints). Epic 7's universal drill-down (FR-AN-3) extends this pattern — keep the filter vocabulary and list view role-agnostic (scope-driven only).

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 5.5]
- FR-SUP-D (drill-through per BRD §7.4-5), FR-SUP-B: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]
- Readiness advisory #2 (new UX, design attention warranted): [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#UX Alignment Assessment — Warnings]
- AD-7 (scope re-resolution, no caller-supplied scope, role gates capability), AD-9, AD-10: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- List/filter/pagination + ID conventions: [Source: ARCHITECTURE-SPINE.md#Consistency Conventions]
- Dashboard-scope copilot deferred (no copilot pane on read-only view): [Source: ARCHITECTURE-SPINE.md#Deferred]
- UX-DR4/DR7/DR11: [Source: _bmad-output/planning-artifacts/epics.md#UX Design Requirements]
- Design-token discrepancy ruling: [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]
- AD-15 E2E gate: [Source: ARCHITECTURE-SPINE.md#AD-15]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
