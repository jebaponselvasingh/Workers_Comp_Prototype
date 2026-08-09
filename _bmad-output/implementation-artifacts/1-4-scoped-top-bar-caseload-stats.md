# Story 1.4: Scoped Top Bar & Caseload Stats

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a logged-in user,
I want the top bar to show my identity and live caseload stats,
so that I see the size and risk of my book at a glance.

## Acceptance Criteria

1. **Given** any persona, **when** the console loads, **then** Caseload / Active Tx / High Risk tiles are computed server-side over only that persona's employer scope (FR-TOP-2, BR-ROLE-1), enforced in the repository layer via the caller context with `scope_all` as a tautology predicate — never a skipped filter (AD-7).
2. **And** no endpoint accepts caller-supplied scope.
3. **Given** the top bar, **when** it renders, **then** the role badge, label, and user chip (avatar initials + name + role) reflect the logged-in persona (BR-ROLE-2, UX-DR2).
4. **Given** scoped supervisor Jennifer Park, **when** her stats compute, **then** they cover only Toyota/GM/3M claims, while David Bline's cover all 100 (both under test).
5. **Given** derived values used by the tiles (`risk`, stage counts), **when** computed, **then** each comes from exactly one registered function in `services/derivations` (AD-10).

## Tasks / Subtasks

- [ ] Task 1: Scope-enforcing repository layer — first real AD-7 enforcement (AC: 1, 2, 4)
  - [ ] `server/data/repositories/claims.py`: every repository method takes the caller context `(user_id, role, employer_ids | ALL)` built by the 1.3 context-builder dependency — **no default, no optional**; a method cannot be called without one
  - [ ] The employer filter is applied unconditionally: `ALL` (from `app_user.scope_all` only) makes the predicate a tautology (e.g. `WHERE TRUE` / no-op clause built by the same code path) — **never** an `if scope_all: skip filter` branch, and never a role check widening the filter (scope gates visibility; role gates capability)
  - [ ] Repository unit tests: a scoped context returns only in-scope claims; `ALL` returns everything; constructing/calling without a context is impossible by signature
- [ ] Task 2: Derivations registry — first entries (AC: 5)
  - [ ] `server/services/derivations/`: the one registry of derived-value computers. Register `risk` (band of `sev_score`: `high` ≥ 65, `med` 35–64, `low` ≤ 34 — thresholds from config now, marked for JDM migration when ZEN lands per AD-8; values verified against all 100 seed records in 1.2) — and `severity` band if surfaced (same function, same thresholds)
  - [ ] Stat aggregates (caseload count, active-treatment count, high-risk count) computed in `services/worklist` **through** the registry's `risk` function and the repository scope context — no SQL that re-encodes the ≥ 65 rule outside the registered function
  - [ ] Unit tests: band boundaries (64/65, 34/35); a grep-style regression test or code-review note that no other module hardcodes 65
- [ ] Task 3: Top-bar stats endpoint (AC: 1, 2, 4)
  - [ ] `GET /api/stats/topbar` (auth-required): returns `{caseload, activeTx, highRisk}` for the **session's** persona — the route takes no scope, employer, or user parameters (AC 2); camelCase per convention
  - [ ] Computed in `services/worklist` over the scoped repository; `activeTx` = claims with `stage = treatment`; `highRisk` = claims whose registered `risk` derivation returns `high`
  - [ ] pytest (AC 4, both named personas under test): login as Jennifer Park → counts cover exactly the Toyota Motor Manufacturing/General Motors/3M Company claims (assert against seed-derived expected numbers); David Bline (supervisor) → caseload 100; also assert a handler persona (e.g. Sarah Williams → 3M-only) so all three roles hit the same path
  - [ ] pytest: request with a smuggled scope attempt (`?employerId=…`, extra body fields) does not alter results — unknown params ignored/rejected, response identical
- [ ] Task 4: Top bar UI — UX-DR2 (AC: 3)
  - [ ] Persistent top-bar component rendered by both shells (dashboard + workspace) from 1.3's slot: brand mark ("L" + LINEWORKER + "Manufacturing WC" sub), role badge (👔 Supervisor / 📋 Handler / 📊 Analyst), spacer, Glossary + Switch buttons, three stat tiles, SLA-strip slot, user chip
  - [ ] Stat tiles: Caseload (neutral) · Active Tx (warn accent) · High Risk (error accent) — values from the stats endpoint via TanStack Query (`queryKeys.stats.topbar`); loading skeleton + error state (NFR-3), never a client-side computation
  - [ ] User chip: avatar initials (first letters of first/last name, client-derived for display only), name, role label ("WC Supervisor" / "Claims Handler" / "Data Analyst") from `/api/me`
  - [ ] Seams for sibling stories: 📖 Glossary button renders **disabled with tooltip** until 1.6 wires the panel; the SLA-strip slot renders empty (or skeleton) until 1.5 fills it — document both in code comments
  - [ ] Vitest: badge/label/chip render from `me` data for all three roles; tiles render from query data; disabled-glossary seam present
- [ ] Task 5: E2E story spec (AC: 1, 3, 4)
  - [ ] `e2e/stories/1-4-scoped-top-bar-caseload-stats.spec.ts` tagged `@story:1-4 @epic:1`, using the 1.3 login fixture
  - [ ] Login as Jennifer Park → top bar shows her name/initials/supervisor badge and the Toyota/GM/3M-scoped counts; login as David Bline → Caseload tile reads 100; login as a handler → handler badge + that book's counts
  - [ ] One `@smoke` happy path: David Bline logs in, top bar renders with Caseload 100

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story ships the persistent top bar (identity + three stat tiles) and — more importantly — the **AD-7 repository scope layer and the AD-10 derivations registry**, the two load-bearing mechanisms every later read path reuses. It does NOT ship: the SLA strip (1.5 — leave the slot), the glossary panel (1.6 — button disabled), any claim list/queue (2.1), or dashboard KPI cards (5.1 — those reuse this story's aggregates pattern, not the other way around). The prototype's `getMine()`/`HANDLER_MAP` client-side filtering and `setStats()` DOM pokes (lines ~989–990) are the exact anti-pattern this story replaces — visual contract only.

### Architecture compliance (binding ADs for this story)

- **AD-1:** tile numbers appear on screen ⇒ a service computed them. The SPA renders `{caseload, activeTx, highRisk}` verbatim.
- **AD-7 (the heart of this story):** scope enforced in the repository layer via a mandatory caller context; `scope_all` → tautology predicate, never a skipped filter, never a role-conditional bypass; context built in exactly one place (1.3's dependency); no endpoint accepts caller-supplied scope. The Jennifer Park vs David Bline test pair is the named, epics-mandated proof.
- **AD-10:** `risk` gets its single registered computer here; stat aggregates consume it. Queue cards (2.1), the risk gauge (2.2), and dashboard KPIs (5.1) will all call the same function — that future consistency is why the registry, not an inline expression, is required now.
- **AD-8 (forward note):** band thresholds (65/35) are parameters; ZEN/JDM is not installed until Epic 2 — read them from the config module now with an explicit `TODO(JDM)` marker, consistent with the conventions row ("SLA targets from config/DB — never hardcoded") and Story 1.1's "ZEN arrives with its first consuming story".
- **AD-9:** tiles via TanStack Query under shared `queryKeys`; no derived value computed client-side (initials are presentation, not a domain derivation).

### Data notes

- Tables used: `claim`, `app_user`, `user_employer_assignment` (all from 1.2). **No new tables, no writes** — this story is read-only, so no AD-4 commands and no AD-12 ownership changes; `services/worklist` here gains read aggregates only (its write-owner role starts in Epic 3).
- `risk` is not a column (1.2 banned it); it derives from `claim.sev_score`. Aggregate SQL may push the band predicate into the query **only** if the predicate is generated from the registered derivation's thresholds (one source of truth), e.g. the derivation module exposes both the row-level function and the SQLAlchemy filter expression.
- Expected test numbers: derive from the seed at test time (count the seed rows per employer) rather than hardcoding magic numbers — the seed is the fixture.

### UX notes (UX-DR2; prototype lines ~449–481 are the contract)

- Layout order per prototype: brand · role badge · spacer · Glossary · Switch · tstat×3 · SLA strip · user chip.
- Tile styling: the prototype marks Active Tx with the warn class and High Risk with the error class — reuse the 1.1 status tokens (`--color-wn`/`--color-er` equivalents); light palette per the 1.1 ruling.
- High Risk tile semantics: "Severity ≥ 65/100" (the supervisor KPI card states it; same threshold, same source).
- Role badge text/icons verbatim: 👔 Supervisor · 📋 Handler · 📊 Analyst; chip role labels: "WC Supervisor" / "Data Analyst" / "Claims Handler".

### Testing requirements (this story's definition of done)

- pytest: repository scope tests (scoped/ALL/signature), derivation band boundaries, stats endpoint per-persona numbers (Park / Bline / one handler), scope-smuggling rejection.
- Vitest: top-bar rendering for all three roles, loading/error states, seams.
- E2E: `1-4-scoped-top-bar-caseload-stats.spec.ts` green (`@story:1-4 @epic:1`, one `@smoke`); story cannot reach `review`/`done` until it passes (AD-15).

### Project Structure Notes

- New: `server/data/repositories/` (scope-enforcing; pattern set here is the template for every future repository), `server/services/derivations/` (registry), `server/services/worklist/` (read aggregates), `server/api/routers/stats.py`.
- Web: `web/src/components/` top-bar component (shared across shells — it is chrome, not a feature folder), `queryKeys` gains `stats`.
- The caller-context type lives in one shared module (e.g. `server/data/context.py`) imported by deps and repositories — not redefined per layer.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 1.4]
- AD-7 full rule (tautology predicate, one context-builder, no caller-supplied scope, Kaya/Deere normative case): [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-7]
- AD-10 (one computer per derived value; risk in its binds list): [Source: ARCHITECTURE-SPINE.md#AD-10]
- AD-8 + Config convention (thresholds as parameters; config/DB never hardcoded): [Source: ARCHITECTURE-SPINE.md#AD-8 / #Consistency Conventions]
- Scoping capability row: [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map (Roles & scoping)]
- UX-DR2 + top-bar markup: [Source: epics.md#UX Design Requirements; docs/Workers_Comp_Prototype.html lines ~449–481]
- Prototype anti-pattern being replaced (getMine/setStats): [Source: docs/Workers_Comp_Prototype.html lines ~989–990]
- Risk band boundaries verified against seed data (high ≥ 65, med 35–64, low ≤ 34): [Source: docs/Workers_Comp_Prototype.html ALL_CLAIMS dataset analysis; BRD §7.2 via implementation-readiness-report-2026-08-09.md#Additional Requirements]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
