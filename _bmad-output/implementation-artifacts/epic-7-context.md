# Epic 7 Context: Analyst Workspace

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Epic 7 gives the analyst persona a workspace of their own instead of the supervisor dashboard they have been borrowing since Epic 5. It delivers a dedicated fraud view with the SIU pipeline, time-series and cohort analytics, nine-dimension segmentation with drill-down from every aggregate, financial decomposition of paid/reserve/incurred with cost-driver analysis, and audited server-side export of whatever the analyst is currently looking at. It matters because analysis is the one BRD capability the prototype never had — the analyst was rendered as a supervisor clone — and because it is where aggregates stop being a wall of numbers and become a route to the claims behind them. Everything is built on Epic 5's scope-aware aggregates: this epic adds dimensions and depth, not a second analytics stack. Fraud-score modeling and dashboard-scope copilot stay deferred.

## Stories

- Story 7.1: Fraud Analytics Workspace
- Story 7.2: Trend & Cohort Analytics
- Story 7.3: Segmentation & Universal Drill-Down
- Story 7.4: Financial Decomposition
- Story 7.5: Dataset & Chart Export

## Requirements & Constraints

- **Fraud workspace.** Fraud-score distribution, the flagged-claims list at the shared fraud threshold (the same value the queue, KPI cards and flags already use — never a second constant), and an SIU-review pipeline grouped by status and handler. Fraud rates by injury type, employer and handler render as sortable charts/tables from scoped aggregates. Cached AI fraud-indicator narratives aggregate into a ranked red-flag frequency view, labeled as AI-generated and stamped with generation time.
- **Trends and cohorts.** Time series over selectable periods for FNOL/DOI volume, average days open, settlement cycle time, RTW rate and cost. Cohorts (severity band, disability type, sector) render side by side with consistent colors and thresholds. Sparse or empty periods get a defined partial/empty state — never a zero line that reads as a real value.
- **Segmentation.** Filtering by employer, sector, state/region, injury type, ICD-10, severity band, age group, gender and disability type recomputes every KPI, chart and table in the workspace server-side under the combined filter. Filters AND together, display as clearable chips, and an impossible combination shows an explicit zero-result state rather than a broken layout.
- **Universal drill-down.** Every KPI, chart segment and table row anywhere in the workspace is a click target that reaches the constituent claims with the active segmentation preserved, opening the read-only claim view. Filter state lives in the URL: a shared or reloaded link restores the filters and re-resolves scope server-side, and a scoped persona must not reach claims outside their book by editing the URL — that is a test, not an assumption.
- **Financial decomposition.** Paid vs. reserve vs. incurred across the scoped portfolio, broken down by the segmentation dimensions. The Light/Adequate/Heavy reserve distribution consumes the existing per-claim reserve check rather than re-deriving it. Cost-driver comparisons quantify surgery vs. non-surgery and litigation vs. non-litigation, each drillable to claims.
- **Export.** Export of any filtered claim list or chart's aggregate data as CSV/XLSX, generated server-side under the caller's scope context and never from client-held data. Each export emits a content-free audit event (who, entity, filter set, row count), since export moves PHI out of the system. Large exports stream or paginate without timing out, with progress and a non-blocking completion notification.
- **Universal.** Loading, empty and error states on every surface; errors surface inline or as non-blocking notifications, never native dialogs. Aggregates carry unit tests over representative mixes, and each story ships one Playwright spec against the freshly reset stack as its done-gate.

## Technical Decisions

- **Same aggregate service, more dimensions.** These views extend the existing scoped aggregate layer behind scope-enforcing repositories; the browser never folds over raw claim rows. Where a new dimension is needed, add it to the existing endpoints rather than standing up a parallel analytics path.
- **Scope is a repository guarantee, not a role branch.** Every read carries caller context and applies one unconditional employer filter; full-portfolio personas make that predicate a tautology. Analyst dashboards are read-only — scope gates visibility, role gates capability. This binds export and every aggregate equally.
- **One computer per derived value.** Risk bands, days open, flags, totals, SLA aggregates, the priority score and the reserve verdict already exist as registered derivations; consume them. Any genuinely new derived value in this epic (for example a fraud band) gets exactly one registered computing function with its cut-offs in the versioned rules tier — never a threshold inlined in a chart or in the browser.
- **Two-tier rules.** Fraud and risk thresholds, cohort band boundaries, period definitions and export row caps are versioned rules-tier parameters; the arithmetic around them is typed Python.
- **AI content is cached, labeled and never authoritative.** Fraud-indicator narratives come from the AI-insight cache owned by the retrieval service, are displayed with their generation timestamp, and are never treated as claim data or as a source of figures.
- **Conventions that apply here.** Money is integer cents in DB and API, formatted only in the UI. List endpoints return `{items, nextCursor, total?}` with `filter[…]`/`sort` params through the generated OpenAPI client. Enums stay snake_case with UI-owned labels. Errors are problem+json. Export bytes go through the shared blob-store protocol rather than ad-hoc file handling.
- **Frontend discipline.** Server state exclusively through TanStack Query with keys in the shared `queryKeys` module; charts are Recharts, tables TanStack Table over vendored shadcn/ui. Nothing server-derived — including sorts, totals and export contents — is recomputed client-side.
- **Do not build ahead.** Dashboard-scope copilot remains deferred: no aggregate copilot tools, no dashboard threads, no copilot panel on these routes. Real fraud-score modeling is a separate initiative — the seeded score stays as-is.
- **Performance is a known open shape.** The Epic 5 aggregates fold in process over the caller's whole scope per request, which is free at the seeded volume and O(scope) in general. Cohorts, segmentation and export multiply that. This epic is the natural place to weigh a `GROUP BY` push-down or a materialized ranking — decide deliberately rather than inheriting the shape by default.

## UX & Interaction Patterns

- **There is no design contract for these screens.** The prototype rendered the analyst as a supervisor clone, so the fraud, trends, segmentation and export surfaces have no as-built reference. A UX pass over the analyst workspace before implementation was an explicit recommendation; treat missing visual direction as a gap to resolve, not to improvise past.
- The workspace is an analyst route inside the existing app shell — top bar, stat tiles and SLA strip are already present and must not be duplicated.
- Reuse the established dashboard vocabulary: dark, information-dense cards, shared ok/warn/error status semantics as Tailwind tokens over vendored shadcn/ui, Recharts for every chart, consistent colors and thresholds across cohorts.
- The segmentation control is global to the workspace; active filters appear as clearable chips, the URL is shareable and back-button safe, and every aggregate on screen respects the active filter.
- Feedback is non-blocking throughout, including export progress and completion.

## Cross-Story Dependencies

- **Epic 5 → all of Epic 7.** The scoped aggregate service, shared thresholds, chart set, priority scorer and the drill-through pattern (filtered claim list plus read-only claim view, URL-restorable) are the foundation. Extend them; do not fork them.
- **Epics 1–3 → all of Epic 7.** Scaffold and seeded portfolio, personas and employer assignments, the auth/scope context builder, risk/stage derivations, the SLA aggregation, and Epic 3's financial figures and reserve check that Story 7.4 must consume rather than re-derive.
- **Within the epic.** 7.3's segmentation is the cross-cutting filter every other story's aggregates must accept, so define its filter contract early; 7.1, 7.2 and 7.4 each supply click sources for its universal drill-down; 7.5 exports whatever filter state 7.3 established.
- **Epic 8 → afterwards.** Purge, audit hardening and the full CI gate apply to whatever this epic adds, including the export audit trail.
- **Product decisions that land on these screens.** Three carried-over questions want an owner rather than a dev-time fix: what the total-paid figure actually sums (it moves financial decomposition directly), whether a claim's severity should aggregate over multiple injuries (it moves every severity cohort and chart), and an inconsistent field name for the fraud score across existing payloads that is worth settling before new fraud surfaces inherit the ambiguity.
