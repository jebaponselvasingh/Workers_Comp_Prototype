# Epic 3 Context: Financial Engine & Action Worklist

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Epic 3 puts the money behind the case file. It delivers the statutory weekly indemnity calculation — comp-rate percentage of average weekly wage, clamped to the state's maintained statutory minimum and maximum, typed TTD/TPD/PPD/PTD, with an audited handler override and reset — the reserve adequacy verdict that judges projected remaining exposure against the reserve, the Bills & Payments tab with its medical bills, expenses and week-by-week indemnity schedule, the approval-plus-batch path that actually disburses, and the auto-generated action checklist that tells a handler what to do next. It matters because it is the epic where the prototype's most dangerous shortcut is undone: numbers a browser invented become numbers a service computed once and can defend. Every figure on these surfaces exists in exactly one place server-side, and the two surfaces that show the same figure show the same server value rather than two agreeing computations.

## Stories

- Story 3.1: Statutory Benefit Calculation
- Story 3.2: Reserve Adequacy Check
- Story 3.3: Bills & Indemnity Payment Schedule
- Story 3.4: Payment Approval & Batch
- Story 3.5: Auto-Generated Action Checklist & Approval

## Requirements & Constraints

- **Benefit calculation.** Weekly indemnity is the comp-rate percentage of AWW (default 66.67%; 100% PTD when disability is permanent and severity is at or above the threshold), clamped to the state statutory min/max held in a maintained rate-schedule table. The card shows indemnity type, the state bounds, the seven-day waiting-period note, and a generated reserve rationale. The comp-rate override is an audited command that recomputes server-side, with a reset control restoring the default.
- **Reserve check.** Projected remaining exposure (unpaid indemnity plus unpaid medical) compared to the current reserve, classified Light / Adequate / Heavy with a rationale. Band boundaries are rules-tier parameters, not code. The verdict renders identically in the Bills financial summary and the treatment-stage Overview card, from the same server value.
- **Bills & Payments.** Financial summary (Total Claim Amount projected, Paid To Date, Reserve Remaining, reserve verdict) plus a metrics row (Weekly Indemnity, Installments Paid, Next Payment Due, Bills On File). The indemnity schedule renders week by week with statuses `paid` / `due_this_week` / `upcoming` / `pending_approval`. Medical bills list by category (initial treatment, surgery/facility, imaging, PT, follow-up, pharmacy) with Paid / Under Review / Pending Submission; expense totals feed the settled-stage payout breakdown.
- **Approval and batch.** Approving a pending week sets `payment_scheduled` via the owning financials command under a status guard with an audit event. The payment batch is the only writer of `paid` for line items and moves only `payment_scheduled` rows, status-guarded so re-runs are idempotent. After a batch, Paid To Date, Installments Paid and Next Payment Due recompute from the derivations rather than being written down.
- **Action checklist.** Claim-state-driven actions from eleven trigger rules (bill review, weekly diary check-in, surgical pre-auth, overdue RTW, modified duty, defense counsel, SIU escalation, OSHA log, payment confirmation, assessment approval, plus padding to at least three), capped at six, each with an urgency tag; trigger parameters live in a rules-tier decision table. "Go to" buttons deep-link to surfaces that exist; links to not-yet-built surfaces render disabled with a tooltip. "Approve Assessment" is a status-guarded audited transition to CH Approved that refreshes queue, detail, and pending-approval counts. Status-toggle completions persist and re-render the list.
- **Universal.** Money is integer cents in database, API and across the rules boundary, formatted only in the UI. Every mutation persists with an audit event in the same transaction under optimistic-concurrency compare-and-swap. Explicit loading, empty and error states; no blocking native dialogs. Financial formulas carry property-based tests across statutory ranges; each story ships one Playwright spec as its done-gate.

## Technical Decisions

- **Every figure computed once, server-side.** Benefit calculation, reserve check, payment-schedule generation and priority/worklist aggregation each exist in exactly one place — `services/financials` or `services/worklist`. Agents reach them only as tools; a copilot answer may quote a tool result but never originates a financial figure.
- **One write-owner per entity.** `payment_schedule_week`, `bill` and `expense` are owned by `services/financials`; the worklist's approval command calls the financials command rather than writing the row itself. Actions and approvals are generated and orchestrated by `services/worklist`, which routes every write through the owning service. Timeline events are emitted only as a side effect of the owning command.
- **Two-tier rules.** Versioned, effective-dated JDM documents own parameters — default comp rate, PTD threshold, reserve bands, action-trigger tables. Typed Python owns the formulas (clamp arithmetic, exposure projection, schedule construction) and reads its tunables from the rules tier. A rule element lives in exactly one tier.
- **Status-guarded lifecycle transitions.** Payment and assessment transitions guard on expected current status in addition to the version CAS. The `payment_scheduled → paid` contract is batch-only and fixed. Scheduled jobs (the payment batch) run in-process for now; the scheduler mechanism itself is deferred.
- **Statutory data is real data.** State min/max comes from a seeded, maintained rate-schedule table, not constants. A jurisdiction with no schedule is a data error surfaced as problem+json, never a silent default.
- **Frontend state discipline.** All server state through TanStack Query with keys in the shared module. Optimistic updates only for user-entered scalars (the comp-rate override); server-derived values — indemnity, verdict, totals, next payment date — wait for the server. A 409 rolls back and renders the fresh entity inline.
- **Scope stays a server guarantee.** Financial reads and the action list go through repository methods applying the caller's employer filter unconditionally. Write commands accept only the handler role.

## UX & Interaction Patterns

- **Bills & Payments** is the third detail tab: financial summary card, metrics row, week-by-week schedule table with status chips, and the categorized bills/expenses lists.
- **Two surfaces, one number.** The treatment-stage Overview's paid-vs-reserve card links through to the Bills tab, and both must show identical figures; the reserve verdict likewise appears in both places from one server value.
- **Upcoming Actions** is a compact block of at most six items, each with an urgency tag and a "go to" affordance; unreachable targets are visibly disabled with an explanatory tooltip rather than hidden.
- **Feedback is non-blocking and local** — controls update in place with a polite live region; refusals appear inline at the control. There is no toast infrastructure in the SPA yet, so do not assume one exists.
- **Visual identity:** the prototype's dark, information-dense console aesthetic and ok/warn/error status semantics, via Tailwind tokens over vendored shadcn/ui components.

## Cross-Story Dependencies

- **Epics 1–2 → all of Epic 3.** Scaffold, seeded portfolio, audit table, auth/scope dependency, the claim detail tab shell and the stage-adaptive Overview cards this epic fills.
- **3.1 → 3.2 → 3.3 → 3.4.** The benefit feeds the exposure projection; the projection feeds the schedule; the schedule is what approval and the batch act on. The reserve verdict is incomplete until bills are seeded, so the medical exposure term and anything depending on a complete verdict must not be assumed present before 3.3.
- **3.5 depends on 3.3/3.4 and on Epic 2.** Bill-review and payment-confirmation triggers read financial state; the deep links target the Bills and Documents tabs built earlier. Links to Diary/Meetings (Epic 4) and Fraud Indicators (Epic 6) ship disabled by design — the seam is intentional, not a gap.
- **Approve Assessment touches Epic 1 and 2 surfaces.** The status change must refresh the queue card, the case header and the pending-approval counts through the same registered derivations — no second computation path.
- **Forward:** Epic 5's dashboard and top-30 worklist reuse this epic's financial aggregates and worklist scoring verbatim; Epic 6's copilot reaches the benefit and reserve computations as tools, and every AD-4 command written here must gain a mark-stale call when embeddings arrive — leave the seam.
- **Known carry-over from Stories 3.1–3.4 (deferred, not defects):** the rate-schedule table holds one row per state, so effective-dating is provenance rather than selection, and its seeded figures are illustrative pending sourced statutory data; schedule week-count tunables remain Python constants rather than rules parameters; the reserve check is deliberately outside the derivations registry, so consumers reach `services/financials` directly; the case-file GET path refreshes the schedule and can therefore write and audit under a reading caller (a system actor now exists but is unused here); the calendar projection still marks elapsed weeks `paid` independently of the batch; and an approved or paid week orphaned by a shortened schedule is persisted but unsurfaced — 3.3 deferred the flag-and-chip to 3.4, and 3.4 re-recorded it.
