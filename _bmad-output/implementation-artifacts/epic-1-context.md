# Epic 1 Context: Secure Login & Scoped Console Foundation

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Epic 1 turns the browser-only claims prototype into a real, persistent, server-authoritative console and establishes the foundation every later epic builds on. It delivers the greenfield monorepo scaffold with a one-command dev environment and a CI gate, the durable PostgreSQL claim portfolio (schema, seed, audit table), role/persona login with real server sessions, and the persistent top bar — identity, live caseload stat tiles, and an SLA strip computed honestly for every role — plus the searchable domain glossary. It matters because it fixes the prototype's two structural sins in one pass: client-side scoping that any user could bypass, and derived numbers computed in the browser. After this epic, a persona logs in and sees exactly the book of business the server says they own, with every figure on screen produced by a service.

## Stories

- Story 1.1: Running Project Skeleton
- Story 1.2: Persisted Claim Portfolio (Schema & Seed)
- Story 1.3: Role & Persona Login
- Story 1.4: Scoped Top Bar & Caseload Stats
- Story 1.5: SLA Strip for Every Role
- Story 1.6: Domain Glossary

## Requirements & Constraints

- **Login flow.** Picking a role card repopulates the persona dropdown with only that role's personas. Entering the console establishes a server session, resolves role and scope server-side, and routes to the dashboard (supervisor/analyst) or the handler workspace shell. Logout ends the session server-side and returns to login. Any request without a valid session gets a 401 problem+json response.
- **Scoping is a server guarantee.** A user's visible caseload is limited to the claims of their assigned employers. Full-portfolio supervisor and analyst personas see all claims; every other persona sees only their assigned employer groups. No endpoint may accept caller-supplied scope, and no session token carries claim scope.
- **Top bar.** Role badge, label, and user chip reflect the logged-in persona. Caseload / Active Treatment / High Risk tiles are computed server-side over that persona's scope only.
- **SLA strip.** Average pick / approve / settle days plus RTW rate, shown for all three roles — not handlers only, which is the gap the prototype left. Targets (< 1d pick, < 5d approve, < 30d settle, > 80% RTW) drive pass/warn coloring and come from configuration or rules, never hardcoded. Aggregation needs unit and property tests covering empty, all-passing, and all-warning caseloads.
- **Glossary.** A slide-in panel over 24 seeded reference terms, filterable live by abbreviation, term, or definition text, with an explicit "no matches" state.
- **Persistence and audit.** Every mutation persists and emits an append-only audit event in the same transaction. Optimistic concurrency (version compare-and-swap, 409 on conflict) applies to every mutable entity.
- **No blocking native dialogs anywhere.** Errors surface as toasts or inline messages; every list, detail, and chart surface needs loading, empty, and error states.
- **PHI discipline from day one.** All claim-derived stores are PHI-class: encrypted at rest and in transit, and PHI values are banned from operational logs (IDs and event names only).
- **Quality gate.** CI runs lint, typecheck, tests, and a clean Alembic upgrade against a fresh database on every merge. Each story also ships one Playwright spec covering its acceptance criteria against the composed stack; a story cannot reach review or done without it.

## Technical Decisions

- **API-first layering.** The SPA renders and captures input only. Every calculation, permission filter, and mutation happens behind FastAPI. If a number appears on screen, a service computed it.
- **Scope enforced in the repository layer.** Every repository method requires a caller context of `(user_id, role, employer_ids | ALL)` and applies one unconditional employer filter — the full-portfolio case makes the predicate a tautology rather than skipping it. No repository, service, or tool may branch on role to widen or skip the filter. Scope gates visibility; role gates capability. The caller context is built in exactly one place, a FastAPI dependency, and re-resolved from the database on every request rather than materialized into a token.
- **Persona and scope data model.** One `app_user` table (`id, name, role, scope_all`) covers all three roles, with scope in one `user_employer_assignment` table for everyone. Supervisor scope is employer-based, not a handler hierarchy — a handler's book may straddle supervisor scopes. Nine demo personas are seed-migration data until an identity provider is chosen; the auth dependency is the designated swap point.
- **One computer per derived value.** Every derived field (`days_open`, `risk`, operational flags, stage counts, SLA aggregates) has exactly one registered computing function in the derivations service. Consumers call it; nothing re-derives, and no derived field is a user-writable column. SLA aggregation in particular exists exactly once, in the worklist service, and is reused verbatim by the dashboard later.
- **Two-tier rules.** Versioned, effective-dated JDM documents own parameters and thresholds (SLA targets among them); typed Python owns formulas and reads its tunables from JDM. A rule element lives in exactly one tier.
- **Audited writes.** Writes go through service-layer command functions that emit a fixed-shape audit event with JSONB before/after diffs in the same transaction. The application database role holds INSERT-only rights on the audit table. Routers never touch a session for writes.
- **One PostgreSQL owns everything** — relational data, embeddings, checkpoints, insight cache, audit log — migrated by Alembic only.
- **Schema conventions.** Snake_case column naming follows the Excel spec's canonical `Table.Column` mapping; surrogate integer PKs everywhere plus the exposed business ID `WC-nnnn` on claims; `version` on mutable entities; money as integer cents in DB, API, and rules, formatted only in the UI; snake_case enum values with UI-owned display labels. API JSON is camelCase via Pydantic aliases. List endpoints return `{items, nextCursor, total?}` with cursor pagination, consumed by one generated OpenAPI client.
- **Frontend state discipline.** All server state through TanStack Query with query keys declared in one shared module. Server-derived values are never computed client-side. Optimistic updates are permitted only for user-entered scalars; a 409 rolls back and renders the fresh server state inline, with no silent retry or client-side merge.
- **Config.** One pydantic-settings module, 12-factor env vars, no secrets in version control. Deployment is on-prem Docker Compose with nginx as sole ingress and the database port internal-only; every container exposes a health endpoint and compose healthchecks gate startup order.

## UX & Interaction Patterns

- **Login screen:** full-screen gradient card, dataset banner, three clickable role cards with supervisor pre-selected, a persona dropdown driven by the selected card, and an "Enter Console →" action.
- **Persistent top bar:** brand mark, role badge, Glossary button, Switch (logout), three live stat tiles, the four-KPI SLA strip with tooltips and pass/warn coloring, and a user chip with avatar initials.
- **Glossary panel:** slide-in with live search, dismissible via ✕ or backdrop click.
- **Visual identity:** the prototype's dark, information-dense console aesthetic and its ok/warn/error status color semantics are preserved, translated into Tailwind design tokens over vendored shadcn/ui components rather than reimplemented ad hoc.
- Native `alert()`-style dialogs are replaced throughout with in-app toasts and inline validation.

## Cross-Story Dependencies

- **1.1 → everything.** The scaffold, Compose stack, CI gate, and Tailwind/shadcn token layer are prerequisites for all other stories in this and every later epic.
- **1.2 → 1.3, 1.4, 1.5.** Login resolves personas from the seeded `app_user` / `user_employer_assignment` rows; the stat tiles and SLA strip aggregate over the seeded claims. The audit table and INSERT-only grant established here are what every later audited command depends on.
- **1.3 → 1.4, 1.5, 1.6.** The auth dependency built here is the sole caller-scope-context builder that the tiles, SLA strip, and all later scoped endpoints consume.
- **1.4 → 1.5.** Both render into the same top bar and both compute over the same scoped caseload; the SLA strip must not introduce a second aggregation path.
- **Forward:** Epic 5's dashboard SLA tiles must reuse Story 1.5's single aggregation and agree with the top-bar strip. Epic 2's queue and Epic 5's KPI cards must read the same registered derivations and JDM thresholds introduced here. The scoping guarantee established in 1.3/1.4 is the invariant Epic 5's drill-through URLs and Epic 6's vector retrieval are later tested against.
- **Known carry-over from 1.3 review (deferred, not defects):** single-session-vs-multi-device policy and session reaping are undecided, so login does not revoke pre-existing sessions; the persona list endpoint ships without the filter/sort/cursor machinery, which is to be built once with Epic 2's first genuinely pageable list; an idle SPA does not detect server-side session expiry.
