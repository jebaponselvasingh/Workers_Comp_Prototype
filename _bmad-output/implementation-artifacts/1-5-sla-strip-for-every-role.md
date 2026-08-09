# Story 1.5: SLA Strip for Every Role

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user of any role,
I want the top-bar SLA strip,
so that performance against SLA targets is honestly reported for my caseload.

## Acceptance Criteria

1. **Given** a persona's caseload, **when** the top bar renders, **then** the strip shows avg Pick / Approve / Settle and RTW rate computed exactly once in `services/worklist` (AD-2), for all three roles — closing the prototype's handler-only recalc gap (FR-SLA-1).
2. **Given** the SLA targets (< 1d / < 5d / < 30d / > 80%), **when** each tile renders, **then** it colors pass/warn against its target and carries an explanatory tooltip.
3. **And** targets come from configuration/rules, never hardcoded.
4. **Given** the aggregation functions, **when** tests run, **then** unit/property tests cover empty caseloads, all-passing, and all-warning cases.

## Tasks / Subtasks

- [ ] Task 1: SLA aggregation in `services/worklist` — the single computer (AC: 1, 3)
  - [ ] One aggregation function (module: `services/worklist`) over the caller's scoped caseload (AD-7 repository context from 1.4), returning the four metrics with their targets and pass/warn status per tile — the server decides pass/warn, the UI only styles it (AD-1)
  - [ ] Metric definitions (from the prototype's `recalcSLA`, line ~1802, and BRD §7.1): **Pick** = avg `sla_pick_days` over the caseload; **Approve** = avg `sla_approve_days` over claims that have one; **Settle** = avg `settlement_days` over settled claims that have one; **RTW rate** = settled claims whose `return_status` is the fully-recovered value ÷ all settled claims, as a percentage
  - [ ] **No fabricated fallbacks:** the prototype substitutes hardcoded 7.4d / 42d / 87% when a segment is empty — that is banned (honest reporting is this story's purpose). A metric with no qualifying claims returns `null` + a `no_data` status; the empty-caseload result is all-null, never invented numbers
  - [ ] Targets (`pick < 1d`, `approve < 5d`, `settle < 30d`, `rtw > 80%`) live in the config module (`server/config.py`, 12-factor) with an explicit `TODO(JDM)` marker: AD-8 assigns SLA targets to the JDM tier — migrate them into a ZEN document when the ZEN wrapper lands (Epic 2/3); the aggregation reads targets by key either way, so the migration touches config only
  - [ ] Rounding at the service edge per prototype display precision: pick/approve one decimal, settle whole days, RTW whole percent
- [ ] Task 2: API exposure (AC: 1)
  - [ ] Extend the top-bar stats response or add `GET /api/stats/sla` (auth-required, session-scoped — no caller-supplied scope, per 1.4's rule): `{pick: {value, target, status}, approve: {…}, settle: {…}, rtwRate: {…}}`, `status ∈ pass|warn|no_data`; camelCase, days/percent as numbers formatted only in the UI
  - [ ] Computed for **every role** — the endpoint has no role branch; supervisor/analyst/handler all get their scoped caseload's strip (the point of FR-SLA-1)
- [ ] Task 3: SLA strip UI — fills the 1.4 slot (AC: 1, 2)
  - [ ] Strip in the shared top bar: "SLA" label + four tiles (Pick · Approve · Settle · RTW Rate), each showing value (`0.8d` / `42d` / `87%`), metric label, and target annotation (`✓ <1d` pass / `⚠ >5d` warn per prototype)
  - [ ] Pass/warn coloring from the server's `status` using the 1.1 ok/warn/error tokens; prototype detail: a failing RTW tile styles with the **error** class (not warn) — follow the contract; `no_data` renders an em-dash tile in muted style, never a fake value (NFR-3 empty state)
  - [ ] Tooltips verbatim from the prototype titles: Pick "Avg days from FROI to Handler Assignment — target <1 day" · Approve "Avg days from FROI to Claim Approval — target <5 days" · Settle "Avg days from FROI to Settlement — target <30 days" · RTW "Percentage of settled claims with successful RTW" (shadcn/ui tooltip — accessible, not bare `title`)
  - [ ] Data via TanStack Query (`queryKeys.stats.sla`), loading skeleton + error state; renders on both shells (dashboard + workspace) automatically because it lives in the shared top bar
- [ ] Task 4: Unit & property tests (AC: 4)
  - [ ] Unit (pytest): empty caseload → all-null/`no_data`; all-passing caseload → four `pass`; all-warning caseload → four `warn`; segment-empty cases (no settled claims → Settle and RTW `no_data` while Pick/Approve compute); rounding rules
  - [ ] Property tests (Hypothesis, per NFR-7): for generated caseloads, pass/warn status always agrees with value-vs-target comparison; averages lie within min/max of inputs; RTW rate ∈ [0, 100]; metrics never invent values for empty segments
  - [ ] pytest: same persona-scoping proof as 1.4 — Jennifer Park's strip computes over Toyota/GM/3M claims only (reuse the seed-derived expectations)
- [ ] Task 5: E2E story spec (AC: 1, 2)
  - [ ] `e2e/stories/1-5-sla-strip-for-every-role.spec.ts` tagged `@story:1-5 @epic:1`, using the login fixture
  - [ ] Strip visible with four tiles for **all three roles** (supervisor, analyst, handler logins — the "every role" clause under test); tiles carry pass/warn styling consistent with seeded data; tooltip text reachable
  - [ ] One `@smoke` happy path: David Bline logs in → SLA strip renders four computed tiles

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story fills the SLA-strip slot 1.4 left in the top bar, backed by the **single** SLA aggregation in `services/worklist`. It is a read-only aggregate story: no new tables, no writes, no audit events. NOT here: the dashboard's SLA tiles chart (5.3 — it explicitly *reuses this aggregation*, so get the function signature/service placement right), diary-note-triggered recalc (obsolete — 4.2 notes the prototype's note-triggered SLA refresh dies because the strip is always server-computed), per-handler SLA benchmarking (5.2), and any JDM/ZEN installation (Epic 2/3 — see the targets ruling below).

The gap being closed (BRD §7.4-7): the prototype recomputes the strip only on handler flows (`recalcSLA` reads `getMine()` and is invoked from handler-side code paths), so supervisors/analysts see stale header defaults. Here the strip is one scoped endpoint consumed by the shared top bar — every role, every load.

### Architecture compliance (binding ADs for this story)

- **AD-2 (the binding rule):** SLA aggregation exists **exactly once**, in `services/worklist`. Story 5.3's dashboard tiles and any future copilot narration reach this same function (agents via a LangGraph tool later) — never a re-implementation.
- **AD-1:** value, target, and pass/warn status all come from the server; the SPA styles and formats only.
- **AD-7:** aggregation runs over the scoped repository with the caller context; `scope_all` personas aggregate the full portfolio through the same tautology predicate.
- **AD-8 + Config convention:** targets are parameters, not code. The conventions row sanctions "SLA targets from config/DB — never hardcoded"; AD-8 assigns them to the JDM tier once ZEN exists. Ruling for this story: config module now, keyed reads, `TODO(JDM)` marker — hardcoding numbers at call sites is the violation either way.
- **AD-10:** SLA aggregates are in AD-10's binds list — this aggregation function is their registered computer; per-claim inputs (`sla_pick_days` etc.) are seeded source columns per Story 1.2's documented SLA-data ruling (they do not reconcile with the synthetic dataset's date fields, so they seed as data; date-difference derivations arrive with real claim flow).
- **AD-9:** TanStack Query, shared `queryKeys`, no client math.

### Data notes

- Tables read: `claim` (via the scoped repository), `app_user`/`user_employer_assignment` (context). No writes; `services/worklist` remains read-only until Epic 3.
- RTW definition detail: the prototype counts settled claims with `returnStatus.includes('Fully')` ("Fully Recovered"); with 1.2's snake_case enums this is the fully-recovered enum value — match the seed's mapped value, don't substring-match.
- The prototype's `recalcSLA` pick metric defaults missing `slaPickDays` to 1 (`c.slaPickDays||1`) — another silent-fabrication pattern; production averages over present values only and reports `no_data` when none qualify.

### UX notes (UX-DR2; prototype lines ~458–480 are the contract)

- Strip anatomy per prototype: `SLA` label, then four compact tiles each with big value (`sk-n`), small metric label (`sk-l`), and target status line (`sk-s`: `✓ <1d` / `⚠ >5d`).
- Coloring: pass → ok token, warn → warn token; failing RTW → error token (prototype line ~1820 uses `er` for RTW misses). Light palette per the 1.1 ruling.
- Tooltips are part of the AC — every tile gets one (text above); ensure keyboard/focus accessibility (shadcn/ui Tooltip).
- States: loading skeleton, error state, and `no_data` em-dash tiles (NFR-3/UX-DR11) — an empty book of business is a legitimate render.

### Testing requirements (this story's definition of done)

- pytest unit: empty / all-pass / all-warn / segment-empty / rounding (AC 4 names the first three explicitly).
- Hypothesis property tests on the aggregation invariants (NFR-7 requires property-based tests on derivations/financial formulas — this is the epic's first).
- pytest: scoped-persona strip (Jennifer Park) proof.
- Vitest: tile rendering per status incl. `no_data`, tooltip presence.
- E2E: `1-5-sla-strip-for-every-role.spec.ts` green (`@story:1-5 @epic:1`, one `@smoke`, all-three-roles coverage); story cannot reach `review`/`done` until it passes (AD-15).

### Project Structure Notes

- Server: aggregation in `server/services/worklist/` (beside 1.4's stat aggregates — same service, per AD-2's ownership sentence); endpoint in `server/api/routers/stats.py`; targets in `server/config.py`.
- Web: strip component beside the 1.4 top-bar component; `queryKeys.stats` extended.
- Signature note for the future: 5.3 will call this exact function for the dashboard SLA tiles — keep it a pure scoped-caseload → metrics function with no HTTP concerns, so the dashboard route composes it without duplication.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 1.5]
- AD-2 (SLA aggregation exactly once in services/worklist): [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-2]
- AD-8 (SLA targets in the JDM parameter tier) + Config convention (targets from config/DB, never hardcoded): [Source: ARCHITECTURE-SPINE.md#AD-8 / #Consistency Conventions]
- Testing convention (Hypothesis property tests; NFR-7): [Source: ARCHITECTURE-SPINE.md#Consistency Conventions (Testing & CI); epics.md#NonFunctional Requirements NFR-7]
- SLA definitions & targets: [Source: implementation-readiness-report-2026-08-09.md#Additional Requirements (BRD §7.1); epics.md FR-SLA-1]
- Handler-only recalc gap (§7.4-7): [Source: implementation-readiness-report-2026-08-09.md#Additional Requirements (§7.4-7); epics.md FR-SLA-1]
- Prototype strip markup + tooltips + recalc semantics: [Source: docs/Workers_Comp_Prototype.html lines ~458–480, ~1802–1821]
- Story 1.2 SLA-fields-as-data ruling: [Source: _bmad-output/implementation-artifacts/1-2-persisted-claim-portfolio-schema-seed.md#Claim field disposition]
- Dashboard reuse contract: [Source: epics.md#Story 5.3 ("reuse Epic 1's single SLA aggregation")]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
