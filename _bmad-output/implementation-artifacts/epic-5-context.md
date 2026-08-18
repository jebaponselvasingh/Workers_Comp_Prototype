# Epic 5 Context: Supervisor Portfolio Dashboard

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Epic 5 builds the oversight half of the console: the dashboard a supervisor lands on instead of a case file. It delivers ten portfolio KPI cards, a ranked handler-performance table with workload, speed and complexity context, seven distribution charts, the top-30 priority claims worklist, and drill-through from every KPI, chart segment and table row into the claims behind the number. It matters because the prototype computed all of this in the browser over the full claim array with client-side scoping — here every figure is a scoped server aggregate, every threshold is the same one the queue and top bar use, and the dashboard becomes an entry point to action rather than a wall of numbers. Dashboards are read-only: scope gates what a persona can see, role gates what they can do. The analyst persona logs into this same dashboard until Epic 7 gives them their own workspace.

## Stories

- Story 5.1: Portfolio KPI Cards
- Story 5.2: Handler Performance Benchmarking
- Story 5.3: Portfolio Analytics Charts
- Story 5.4: Priority Claims Worklist (Top 30)
- Story 5.5: Dashboard Drill-Through

## Requirements & Constraints

- **KPI cards.** A portfolio header with the dataset chip, then two rows of cards over the persona's scoped caseload: total claims, under treatment, settled & closed, high risk (severity at or above the risk threshold), total paid and total reserve; then fraud flags (fraud score at or above its threshold), OSHA recordable, litigation/attorney-represented and surgery-required. Both thresholds are the same shared values every other surface uses.
- **Handler benchmarking.** Handlers ranked by composite cycle time (pick + approve + settle), with rank, handler, case count, a cycle-speed bar relative to peers, average days, RTW percentage, a Low/Med/High complexity grade blending severity, surgery rate and litigation rate, pending approvals, and an On Track / Watch / Attention status derived from cycle-time deviation against the portfolio. A leader/laggard callout accompanies the table. Only handlers with claims inside the viewer's scope appear, and the filtering happens server-side.
- **Charts.** Seven: settlement-status donut, severity donut (High/Medium/Low on the same risk threshold), SLA pass/fail tiles, recovery-status bars, injury-type top-8 bars, total-paid-by-employer bars, claims-by-state top-10 bars. Thresholds and colors must match the KPI cards and the handler queue exactly; the SLA tiles must agree with the top bar because they read the same aggregation. Every chart needs a defined empty/zero state that does not break layout.
- **Priority worklist.** Population is active-treatment ∪ fraud-flagged ∪ litigation-flagged, capped at 30 (the cap is a tunable parameter, not a constant). Columns: claim ID, worker, employer, injury type, severity, fraud score, handler, days open, priority next best action, status — litigation rows carry a LITIG chip. The next-best-action value comes from the deterministic action generator, never from a model. Rows are server-sorted by the same priority scorer the handler queue uses, and the endpoint is cursor-paginated like every other list.
- **Drill-through.** Clicking a KPI card or chart segment opens a scoped, filtered claim list showing the constituent claims with the applied filter visible and clearable. Clicking a claim row anywhere opens a read-only claim view — header, overview, financials, documents, with no edit affordances. Clicking a handler row lists that handler's caseload within the viewer's scope. Drill-through state lives in the URL: loading one directly restores the filters and re-resolves scope server-side, and a scoped supervisor must not be able to reach claims outside their book by editing the URL. That last property is a test, not an assumption.
- **Universal.** Loading, empty and error states on every surface; errors surface inline or as non-blocking notifications, never native dialogs. Aggregate computations carry unit tests over representative mixes, and each story ships one Playwright spec against the freshly reset stack as its done-gate.

## Technical Decisions

- **Aggregates live in the worklist service.** Every number on this dashboard is computed by `services/worklist` behind scope-enforcing repositories and delivered ready to render. No chart or card may compute over raw claim rows in the browser.
- **Scope is a repository guarantee, not a role branch.** Every read carries the caller context and applies one unconditional employer filter; full-portfolio personas make that predicate a tautology rather than skipping it. Supervisor scope is employer-based, not a handler hierarchy — a handler's book may straddle supervisor scopes, so handler rows and their metrics must be computed *within* the viewer's scope rather than by looking up who reports to whom.
- **One computer per derived value.** Risk bands, days open, totals, SLA aggregates and the priority score already exist as registered derivations; the dashboard consumes them. Reusing them is what makes the dashboard agree with the queue and top bar — a second implementation is the failure mode this epic is most exposed to. The complexity score and the cycle-time status are new derived values and each gets exactly one registered function.
- **Two-tier rules.** The risk and fraud thresholds, the cycle-time deviation bands that produce On Track / Watch / Attention, the worklist cap and the complexity weights are versioned rules-tier parameters; the arithmetic around them is typed Python.
- **Conventions that apply here.** Money is integer cents in DB and API, formatted only in the UI. List endpoints return `{items, nextCursor, total?}` with `filter[…]`/`sort` params, consumed through the generated OpenAPI client. Enum values stay snake_case with UI-owned display labels. Errors are problem+json.
- **Frontend discipline.** Server state exclusively through TanStack Query with keys in the shared `queryKeys` module; charts are Recharts, tables TanStack Table over vendored shadcn/ui. Nothing server-derived is recomputed client-side, including sorts and totals.
- **Read-only means absent, not disabled.** The claim view opened by drill-through reuses the detail components but must not render edit affordances; the write commands behind them accept only the handler role regardless.
- **Do not build ahead.** Dashboard-scope copilot is deferred by decision — no aggregate copilot tools, no dashboard threads, no panel on this route. Analyst depth (deep segmentation, export) is Epic 7; build the aggregates so that epic can reuse them, but not its features.

## UX & Interaction Patterns

- The dashboard is the supervisor/analyst landing route inside the existing app shell, so the top bar, stat tiles and SLA strip are already present and must not be duplicated by the page.
- Layout runs KPI rows → handler performance table → charts → priority table, in the prototype's dark, information-dense aesthetic with the shared ok/warn/error status semantics, expressed as Tailwind tokens over vendored shadcn/ui components.
- Every KPI card, chart segment and table row is a click target; the resulting filtered list shows its filter as a visible, clearable chip and its URL is shareable and back-button safe.
- The handler table carries per-row cycle-speed bars scaled against peers, a status chip, and a leader/laggard callout; the priority table carries LITIG chips.
- Feedback is non-blocking. Whether a toast primitive exists depends on what Epic 4 shipped — check before assuming one, and otherwise use the established in-place update plus polite live region pattern.

## Cross-Story Dependencies

- **Epics 1–3 → all of Epic 5.** The scaffold and seeded portfolio, the nine personas and their employer assignments (including the scoped supervisor whose counts are the scoping test), the auth/scope context builder, Epic 1's single SLA aggregation, Epic 2's queue priority scorer and risk/stage derivations, and Epic 3's financial aggregates (paid, reserve) plus its action generator, which supplies the priority worklist's next-best-action column.
- **Within the epic.** 5.1 establishes the scoped aggregate service and the shared thresholds that 5.2 and 5.3 read. 5.4 needs Epic 3's action generator and Epic 2's scorer. 5.5 depends on 5.1–5.4 existing as click sources and on Epic 2's detail components for the read-only claim view.
- **Epic 4 → 5.x only incidentally.** Nothing on this dashboard reads diary, meeting or email data; do not couple to it.
- **Forward.** Epic 7 builds its fraud workspace, cohorts, segmentation and export on these same aggregates — design the endpoints so extra dimensions are additions, not rewrites. Epic 8's purge, audit and CI gates apply to whatever this epic adds.
- **Open questions this epic will surface.** Two carried-over product decisions land directly on these screens: what the "total paid / total claim amount" figure actually sums (it moves the financial KPI cards if resolved), and whether a claim's severity should aggregate over multiple injuries (it moves the severity donut). Both want an owner, not a dev-time correction. Two earlier deferrals were explicitly parked pending 5.4's worklist: the stage-group list response shape, whose paging cost was left unresolved with this top-30 list in view, and whether an action row may approve a payment directly. Weigh in on both while building 5.4.
