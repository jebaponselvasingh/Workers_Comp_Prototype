# Story 1.3: Role & Persona Login

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a WC console user,
I want to pick my role and persona and enter the console,
so that I get my role-appropriate workspace.

## Acceptance Criteria

1. **Given** the login screen (gradient card, dataset banner, 3 role cards with supervisor pre-selected — UX-DR1), **when** a role card is clicked, **then** the persona dropdown repopulates with only that role's personas (FR-LOGIN-1).
2. **Given** a selected persona, **when** "Enter Console →" is clicked, **then** a server session is established via the OIDC-ready auth dependency — role and scope resolved server-side from `app_user`, tokens never carrying claim scope — and the user routes to the dashboard (supervisor/analyst) or handler workspace shell (FR-LOGIN-2).
3. **Given** a logged-in session, **when** "↩ Switch" is clicked, **then** the session ends server-side and the login screen returns (FR-TOP-1).
4. **Given** any API request without a valid session, **when** it reaches any endpoint, **then** the response is 401 problem+json (RFC 9457).

## Tasks / Subtasks

- [ ] Task 1: Session auth backbone — the OIDC-ready dependency (AC: 2, 3, 4)
  - [ ] Server-side session store (a `session` table or signed-cookie-referenced store; **HttpOnly cookie carries only an opaque session id** — never role, never employer scope). Session row references `app_user.id`; expiry from config
  - [ ] One FastAPI auth dependency (`server/api/deps.py`): resolves cookie → session → `app_user` row on **every request**; this is the single OIDC swap point (IdP choice is Deferred — until then, "authentication" is persona selection against the 1.2 seed, and this dependency is where a real IdP later plugs in without touching any endpoint)
  - [ ] Context-builder dependency (AD-7 groundwork): from the resolved `app_user`, build the caller context `(user_id, role, employer_ids | ALL)` — `ALL` derived only from `app_user.scope_all`, resolved in exactly this one place. Story 1.4's repositories consume it; build and attach it now
  - [ ] `POST /api/auth/login` (persona id → mints session, sets cookie), `POST /api/auth/logout` (deletes the server-side session, clears cookie), `GET /api/me` (current persona: name, role, initials for the user chip)
  - [ ] `GET /api/personas` — role-grouped persona list driving the dropdown (id, name, display label). Pre-auth by necessity (it *is* the login picker); expose id/name/role/label only — no scope data. This endpoint rides the deferred-IdP decision and is replaced wholesale with real identity
- [ ] Task 2: 401 problem+json plumbing (AC: 4)
  - [ ] Global FastAPI exception handling: missing/invalid session → 401 with RFC 9457 body (`type`, `title`, `status`, `detail`), `content-type: application/problem+json`; register once so **every** future router inherits it
  - [ ] Wire the generated OpenAPI client + a TanStack Query error boundary: 401 → redirect to login (no toast storm); this starts the AD-9 discipline — `queryKeys` module begins here with `me`/`personas` keys
  - [ ] Pydantic alias generator (camelCase JSON) configured on the shared base model — first real API payloads, so the convention locks in now
- [ ] Task 3: Login screen — UX-DR1 (AC: 1, 2)
  - [ ] `web/src/features/login/`: full-screen gradient card with LINEWORKER brand mark, subtitle "Manufacturing Workers Compensation Console — United States", dataset banner ("📊 Dataset: WC_Manufacturing_Claims_2026.xlsx · 100 unique employees · 10 employers · 15 plants · 27 injury types")
  - [ ] 3 clickable role cards, supervisor pre-selected: 👔 Supervisor "Portfolio, SLA, handler performance" · 📋 Claims Handler "Caseload, case detail, copilot" · 📊 Data Analyst "Fraud, trends, KPI drill-down" — selection state per prototype
  - [ ] Role card click repopulates the persona `<select>` from the `/api/personas` data for that role only, with the prototype's display labels (e.g. "David Bline — WC Supervisor (All 100 claims)", "Jennifer Park — WC Supervisor (Toyota/GM/3M)", "Kaya Johnson — Handler (CAT · GE · Whirlpool · Deere)")
  - [ ] "Enter Console →" submits login; on success route by role: `supervisor`/`analyst` → dashboard shell, `handler` → workspace shell
  - [ ] Loading + error states on the login action (inline message on failure — no `alert()`, NFR-3)
- [ ] Task 4: Post-login shells & Switch (AC: 2, 3)
  - [ ] Minimal routed shells: dashboard shell (`/dashboard`) and handler workspace shell (`/workspace`) — layout frames only, each rendering the top-bar slot and an empty content area ("dashboard arrives Epic 5" / "queue arrives Epic 2" placeholder panels). Content is 1.4+/2.1/5.1 scope
  - [ ] "↩ Switch" button (top-bar slot) calls logout, clears client cache (TanStack `queryClient.clear()`), returns to login — **server-side session is dead** afterward (a replayed request 401s; under test)
  - [ ] Route guard: unauthenticated visit to `/dashboard` or `/workspace` redirects to login (driven by the 401/`me` check, not client-side role assumptions)
- [ ] Task 5: Tests (AC: all)
  - [ ] pytest: login mints a session and `GET /api/me` returns the persona with server-resolved role; logout invalidates (subsequent `me` → 401); any protected endpoint without a cookie → 401 problem+json with correct content-type; cookie/session payload contains **no employer ids** (assert the stored/serialized session carries only the user reference)
  - [ ] pytest: personas endpoint groups the 1.2 seed correctly (3 supervisors / 1 analyst / 6 handlers)
  - [ ] Vitest: role card click swaps dropdown contents; login error state renders inline
  - [ ] E2E fixture: `e2e/fixtures/` gains **login-as-persona** (drives the real login screen — the fixture every later story's spec uses, per AD-15)
  - [ ] `e2e/stories/1-3-role-persona-login.spec.ts` tagged `@story:1-3 @epic:1` (anchored grep — never matches `1-30`): role-card → dropdown repopulation; supervisor login lands on dashboard shell; handler login lands on workspace shell; Switch returns to login and back-button/direct-URL access 401-redirects; one `@smoke` happy path (login as David Bline → dashboard shell visible)

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story delivers **identity, session, and routing** — the login surface and the auth dependency every later endpoint hides behind. It does NOT deliver: the top bar's stats/badge/user-chip content (1.4 — the shells render a top-bar *slot* only), the SLA strip (1.5), glossary (1.6), the claim queue or auto-select-first-claim (2.1 — that clause of FR-LOGIN-3 is explicitly Epic 2 scope), seeded demo meetings (4.1), or any dashboard content (5.x). Also NOT here: a real IdP — OIDC vs local credentials is a Deferred architecture decision; the auth dependency is the swap point, and seed-persona login is the sanctioned interim (readiness report advisory 3 tracks the trigger: "before first non-dev deployment").

Do not copy the prototype's `pickRole`/`doLogin` JS — the login screen is a React feature; the prototype supplies visual contract and label text only.

### Architecture compliance (binding ADs for this story)

- **AD-1 layering:** role and scope resolution happen server-side; the SPA never decides what a role can see — it renders what `/api/me` says and routes on it.
- **AD-7 scoping:** the context-builder dependency built here is *the* single place `scope_all` → `ALL` resolution lives. Tokens/cookies never carry claim scope — scope is re-resolved from `app_user` + `user_employer_assignment` on every request, which is what makes the AD-7 "re-resolve on every run start and resume" rule cheap later.
- **AD-9 frontend state:** TanStack Query owns `me`/`personas` server state via the shared `queryKeys` module started here; no hand-rolled fetch state.
- **Conventions:** OIDC-ready session auth (the Auth conventions row verbatim); RFC 9457 problem+json for 401 (Errors row); camelCase JSON via Pydantic alias generator; one generated OpenAPI client.
- **AD-15:** the persona-login fixture ships here and becomes shared plumbing — later specs must use it rather than reimplementing login.

### Auth design notes

- Session mechanics: opaque id in an HttpOnly, SameSite cookie; server-side row holds `user_id`, `created_at`, `expires_at`. Logout deletes the row (server-side end is AC 3's point — a stolen cookie dies with the session).
- 401 vs 403: absent/invalid session → 401 problem+json. Role-capability denials (403) first arise with write endpoints (2.3+) — nothing to build now, but the exception-handler shape should cover both statuses.
- The seed-auth login accepts a persona **id** (surrogate `app_user.id` from `/api/personas`), not a free-text `name|role` string — the prototype's `"David Bline|supervisor"` value format does not survive.
- David Bline exists twice in `app_user` (supervisor row + analyst row, per Story 1.2's documented ruling); the personas endpoint naturally lists him under both roles — matching the prototype's dropdowns.

### UX notes (UX-DR1; prototype lines ~432–447 are the contract)

- Light palette per the Story 1.1 design-token ruling — the "dark console aesthetic" wording in epics.md is a documented discrepancy; the prototype's light `:root` tokens are canonical. The login gradient card styles with the 1.1 Tailwind tokens.
- Supervisor card pre-selected on first render, with its personas pre-populated (the prototype boots with David Bline selected).
- Dataset banner text verbatim from the prototype; role-card icons/titles/descriptions verbatim (Task 3).
- Persona labels: keep the prototype's parenthetical scope hints — they make scoped-vs-full-portfolio personas legible in demos and in e2e specs.

### Testing requirements (this story's definition of done)

- pytest: session lifecycle, 401 problem+json shape, no-scope-in-session assertion, persona grouping.
- Vitest: role-card → dropdown behavior, inline error state.
- E2E: `1-3-role-persona-login.spec.ts` green against the reset stack (now with real seed from 1.2), `@smoke` tagged; login fixture landed in `e2e/fixtures/`; story cannot reach `review`/`done` until the spec passes (AD-15).

### Project Structure Notes

- Server: `server/api/deps.py` (auth + context-builder dependencies), `server/api/routers/auth.py`; session model/migration in `server/data/` (Alembic revision — this story may add the `session` table; it is infrastructure, not a domain entity, and is not in the ERD's 25).
- Web: `web/src/features/login/`, route setup in the app shell, `web/src/api/` gains the generated client + `queryKeys.ts`.
- First real API consumer: generate the OpenAPI client in the web build (script + CI step) so the contract stays the single source of TS types.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 1.3]
- Auth convention (OIDC-ready, tokens never carry scope) + Errors convention (RFC 9457): [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Consistency Conventions]
- AD-7 (context-builder as sole scope resolver): [Source: ARCHITECTURE-SPINE.md#AD-7]
- AD-9 (TanStack Query, queryKeys): [Source: ARCHITECTURE-SPINE.md#AD-9]
- AD-15 (login fixture as shared plumbing, spec naming/tags): [Source: ARCHITECTURE-SPINE.md#AD-15]
- Deferred IdP decision: [Source: ARCHITECTURE-SPINE.md#Deferred; implementation-readiness-report-2026-08-09.md#Standing Advisories item 3]
- UX-DR1 + login markup/labels: [Source: epics.md#UX Design Requirements; docs/Workers_Comp_Prototype.html lines ~432–447, ~962–983]
- Design-token ruling (light palette canonical): [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
