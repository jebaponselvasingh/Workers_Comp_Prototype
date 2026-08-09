---
stepsCompleted: [1, 2, 3, 4, 5, 6]
inputDocuments:
  - docs/BRD-Workers-Comp-Console.md (git HEAD) — acting PRD
  - _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md — canonical architecture
  - docs/Architecture-LINEWORKER.md — architecture narrative (reference)
  - _bmad-output/planning-artifacts/epics.md — epics & stories (8 epics / 40 stories)
  - docs/Workers_Comp_Prototype.html — UX design contract
  - docs/WC_Feature_Element_Details.xlsx — field-level spec (reference)
---

# Implementation Readiness Assessment Report

**Date:** 2026-08-09
**Project:** LINEWORKER — Manufacturing Workers' Compensation Console

## Document Inventory

| Document type | File | Status |
|---|---|---|
| PRD | `docs/BRD-Workers-Comp-Console.md` (git HEAD) | ⚠️ Substitute — reverse-engineered BRD acting as PRD (deleted in working tree, read from git) |
| Architecture | `architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md` | ✅ Final (updated 2026-08-09), AD-1…AD-14 |
| Architecture (narrative) | `docs/Architecture-LINEWORKER.md` v1.2 | ✅ Reference projection of the spine |
| Epics & Stories | `epics.md` | ✅ Complete — 8 epics, 40 stories, stepsCompleted [1,2,3,4] |
| UX Design | `docs/Workers_Comp_Prototype.html` + `docs/WC_Feature_Element_Details.xlsx` | ⚠️ Substitute — prototype as design contract; 12 UX-DRs extracted into epics.md |

**Duplicates:** none.
**Warnings:** no formal PRD or UX spec — both substitutions are deliberate, user-confirmed decisions recorded in `epics.md` frontmatter.

## PRD Analysis

Source: `docs/BRD-Workers-Comp-Console.md` @ git HEAD (365 lines, read in full). The BRD is a reverse-engineered, screen-by-screen as-built spec of the prototype with "prototype limitation" callouts that §7.4 consolidates into production requirements.

### Functional Requirements

**Login / Roles / Scoping (5)**
- FR-LOGIN-1: Selecting a role card MUST repopulate the persona dropdown with only that role's personas.
- FR-LOGIN-2: "Enter Console" MUST set the active user/role, compute the caseload, populate the top-bar stats, and render either the dashboard (supervisor/analyst) or the handler workspace.
- FR-LOGIN-3: For handlers, entering the console MUST auto-select the first claim, seed demo meetings, and render the detail + copilot panes.
- BR-ROLE-1: The visible caseload for any user MUST be limited to the claims mapped to that persona (employer-based partition).
- BR-ROLE-2: Role label, badge, and top-bar caseload stats MUST reflect the logged-in persona.

**Global / Top Bar (4)**
- FR-TOP-1: "Switch" MUST log out via return to login.
- FR-TOP-2: Stat tiles MUST recompute from the logged-in persona's caseload.
- FR-SLA-1: SLA tiles MUST show pick/approve/settle averages and RTW rate with pass/warn coloring against targets (per §7.4-7: for every role).
- FR-GLOS-1: Glossary search MUST filter by abbreviation, term, or definition text and show a "no matches" state.

**Handler — Queue (4):** FR-Q-1 (8-filter re-filter + grouped priority-sorted render) · FR-Q-2 (cards surface risk + FRAUD/LITIG/PAY DUE/SIU) · FR-Q-3 (card selection drives detail + copilot) · FR-Q-4 (priority scoring elevates litigation, SIU, RTW-blocked, pending-approval, payment-due, surgical).

**Handler — Detail (5):** FR-DET-1 (stage-adaptive Overview) · FR-DET-2 (inline edits update record + dependent calcs) · FR-DET-3 (Reserve Check: projected remaining exposure vs reserve → Light/Adequate/Heavy) · FR-DET-4 (read-only document/photo viewers) · FR-ACT-1 (state-generated actions, deep links, Approve Assessment → CH Approved).

**Handler — consolidated (11):** FR-H-1 (prioritized filterable stage-grouped caseload) · FR-H-2 (stage-adaptive editable case file) · FR-H-3 (benefit calc, state min/max clamp, comp-rate override) · FR-H-4 (reserve adequacy verdict) · FR-H-5 (deep-linked action checklist + one-click approval) · FR-H-6 (injury visualization, multi-injury, severity edit) · FR-H-7 (bills + weekly indemnity schedule) · FR-H-8 (path-based statutory forms + real path classification) · FR-H-9 (claim-aware AI copilot + per-claim history) · FR-H-10 (diary/meetings/templated email, claim-linked and logged) · FR-H-11 (glossary + RTW-letter generation).

**Handler — Copilot & Diary (5):** FR-CP-1 (7 claim-contextual quick actions) · FR-CP-2 (per-claim chat history) · FR-DIARY-1 (dated claim-linked notes) · FR-DIARY-2 (schedule/complete/delete/convert meetings) · FR-DIARY-3 (compose/template/log emails).

**Supervisor (9):** FR-SUP-1 (10 KPI cards, stated thresholds) · FR-SUP-2 (handler ranking by composite cycle time) · FR-SUP-3 (flag slow handlers) · FR-SUP-4 (7 charts from persona caseload) · FR-SUP-5 (top-30 priority table) · FR-SUP-A (KPI oversight) · FR-SUP-B (productivity/complexity benchmarking) · FR-SUP-C (distribution analytics) · FR-SUP-D (priority worklist; §7.4-5 adds drill-through).

**Analyst target-state (6):** FR-AN-1 (fraud workspace) · FR-AN-2 (trends/cohorts) · FR-AN-3 (universal KPI drill-down) · FR-AN-4 (9-dimension segmentation) · FR-AN-5 (financial analytics) · FR-AN-6 (export).

**Total FRs: 49** (47 FR + 2 BR)

### Non-Functional Requirements

- NFR-1 (No persistence → persistence required): all edits/chats/notes/meetings/emails must persist to a backend and be auditable.
- NFR-2 (Offline AI → server-brokered AI): AI must be brokered server-side with credentials and guardrails; no credential-less client-side calls.
- NFR-3 (Native dialogs → in-app notifications): replace `alert()` with non-blocking in-app notifications.
- NFR-4 (Static content → validated statutory data): benefit figures validated against live state schedules; real form sources.

**Total NFRs: 4** (expanded to 8 in epics.md by folding in architecture-binding constraints — security/PHI, degradation, CI, operations)

### Additional Requirements (BRD §7)

- §7.1 SLA definitions: Pick < 1d, Approve < 5d, Settle < 30d, RTW > 80%.
- §7.2 Derived flags: SIU review (fraudFlag ∧ score ≥ 60), RTW blocked, payment due (hash-bucket demo definitions).
- §7.3 Compliance: OSHA recordability, ICD-10 on every claim, statutory min/max enforcement, path-based forms A/B/C.
- §7.4 Consolidated production requirements: (1) real authN/authZ + assignment source, (2) persistence + audit, (3) server-side AI with guardrails, (4) analyst differentiation, (5) dashboard drill-through, (6) claim-path classification, (7) SLA strip for all roles, (8) real integrations (email/calendar/DMS/export/live schedules), (9) UX polish (no alerts; empty/error/loading states).

### PRD Completeness Assessment

Strong: exhaustive screen-by-screen FR catalog with stable IDs, explicit thresholds/weights, an element-to-requirement index (§8), and honest prototype-limitation callouts that convert cleanly into production requirements. Weaknesses as a PRD: no prioritization (MoSCoW), no success metrics beyond SLA targets, business objectives are explicitly "inferred from the design," and target-state analyst requirements (FR-AN-*) are less specified than as-built sections. None of these block epic traceability; they are inherent to the BRD-as-PRD substitution.

## Epic Coverage Validation

Method: independent mechanical verification — each FR grepped (word-boundary) against the story sections of each epic in `epics.md`, then compared to the document's own FR Coverage Map. Not taken on trust from the map.

### Coverage Matrix

| FR | Epic coverage (verified in story ACs) | Status |
|---|---|---|
| FR-LOGIN-1, FR-LOGIN-2 | Epic 1 (Story 1.3) | ✓ Covered |
| FR-LOGIN-3 | Epics 1/2/4 — routing (1.3), auto-select (2.1), seeded meetings (4.1) | ✓ Covered (deliberate split, documented) |
| BR-ROLE-1, BR-ROLE-2 | Epic 1 (Stories 1.3, 1.4) | ✓ Covered |
| FR-TOP-1, FR-TOP-2 | Epic 1 (Stories 1.3, 1.4) | ✓ Covered |
| FR-SLA-1 | Epic 1 (Story 1.5; reaffirmed 4.2, 5.3) | ✓ Covered |
| FR-GLOS-1 | Epic 1 (Story 1.6) | ✓ Covered |
| FR-Q-1..4, FR-H-1 | Epic 2 (Story 2.1) | ✓ Covered |
| FR-DET-1, FR-H-2 | Epic 2 (Stories 2.2, 2.3) | ✓ Covered |
| FR-DET-2 | Epic 2 (Stories 2.3, 2.4) | ✓ Covered |
| FR-DET-4 | Epic 2 (Stories 2.5, 2.6) | ✓ Covered |
| FR-H-6 | Epic 2 (Story 2.4) | ✓ Covered |
| FR-H-8 | Epic 2 (Story 2.5) | ✓ Covered |
| FR-H-3 | Epic 3 (Story 3.1) | ✓ Covered |
| FR-H-4, FR-DET-3 | Epic 3 (Story 3.2) | ✓ Covered |
| FR-H-7 | Epic 3 (Stories 3.3, 3.4) | ✓ Covered |
| FR-H-5, FR-ACT-1 | Epic 3 (Story 3.5) | ✓ Covered |
| FR-DIARY-1..3, FR-H-10 | Epic 4 (Stories 4.1–4.3) | ✓ Covered |
| FR-SUP-1, FR-SUP-A | Epic 5 (Story 5.1) | ✓ Covered |
| FR-SUP-2..3, FR-SUP-B | Epic 5 (Story 5.2) | ✓ Covered |
| FR-SUP-4, FR-SUP-C | Epic 5 (Story 5.3) | ✓ Covered |
| FR-SUP-5, FR-SUP-D | Epic 5 (Stories 5.4, 5.5) | ✓ Covered |
| FR-CP-1 | Epic 6 (Story 6.4) | ✓ Covered |
| FR-CP-2 | Epic 6 (Story 6.3) | ✓ Covered |
| FR-H-9 | Epic 6 (Stories 6.2, 6.3) | ✓ Covered |
| FR-H-11 | Epic 6 (Story 6.5 RTW letter) + Epic 1 (Story 1.6 glossary) | ✓ Covered (split documented) |
| FR-AN-1..6 | Epic 7 (Stories 7.1–7.5) | ✓ Covered |

### Missing Requirements

None. Every PRD FR is cited in at least one story's acceptance criteria. No FRs appear in epics that are absent from the PRD (all IDs originate in the BRD; UX-DR1..12 and NFR-5..8 are derived from the confirmed UX contract and architecture, documented in the Requirements Inventory).

**Discrepancy found & fixed during validation:** the FR Coverage Map listed FR-LOGIN-3 under Epic 1 only, while its implementation is split across Stories 1.3 / 2.1 / 4.1. Map entry corrected in `epics.md`.

### Coverage Statistics

- Total PRD FRs: 49 (47 FR + 2 BR)
- FRs covered in epics: 49
- Coverage: **100%**

## UX Alignment Assessment

### UX Document Status

**Not found** (no `*ux*.md` in planning artifacts). UX is unambiguously implied — this is a user-facing three-workspace console. The gap is deliberately filled by a substitute contract: `docs/Workers_Comp_Prototype.html` (clickable as-built design) + `docs/WC_Feature_Element_Details.xlsx` (93-row element spec), formalized as UX-DR1..12 in `epics.md`.

### Alignment Analysis

**UX ↔ PRD: aligned by construction.** The BRD is a screen-by-screen reverse-engineering of the prototype, so every UX surface has a matching FR and vice versa (the BRD's §8 element-to-requirement index makes this explicit).

**UX ↔ Architecture: supported.** The stack names the UX-bearing choices directly (React 19, shadcn/ui + Tailwind 4, TanStack Query/Table, Recharts, assistant-ui for chat); AD-9 fixes frontend state discipline; AD-14 specifies the degradation UI (disable only affected inputs); the conventions replace `alert()` with problem+json → toast/inline (NFR-3/UX-DR11). SVG ports (risk gauge, body silhouette) are self-contained assets carried by stories 2.2/2.4.

**Documented, intentional divergences from the as-built UX** (not misalignments): the `offlineAnswer` canned-AI pattern is banned (AD-14 honest degradation replaces it); native dialogs removed; SLA strip computed for all roles; always-Path-B forms replaced by real classification. Each is traced to a story.

### Warnings

1. ⚠️ **Epic 7 (Analyst Workspace) has no UX reference.** The prototype renders the analyst as a supervisor clone, so fraud workspace, trends, segmentation, and export screens (Stories 7.1–7.5) will be designed during implementation without a design contract. **Recommendation:** run `bmad-ux` for the analyst surfaces before starting Epic 7 (not a blocker for Epics 1–6).
2. ⚠️ **Drill-through views (Story 5.5) are new UX** with no prototype precedent — lower risk than Epic 7 (they're filtered lists + read-only detail reusing existing patterns), but design attention is warranted at story prep time.
3. ℹ️ **UX-DR12 (visual-identity translation to Tailwind tokens)** rests on Story 1.1 without a formal token spec; drift risk is cosmetic, mitigated by the prototype being viewable next to the build.

## Epic Quality Review

Standards applied: user-value epics, epic independence, no forward dependencies, just-in-time schema, testable Given/When/Then ACs, single-session story sizing. Reviewer note: the epics were authored this session; this review was run adversarially against the artifacts, including a mechanical ERD-entity sweep.

### 🔴 Critical Violations

None found.

### 🟠 Major Issues

None found (two schema-assignment defects found by the ERD sweep were remediated during this review — see below).

### 🟡 Minor Concerns

1. **Story 1.2 is technically framed** ("Persisted Claim Portfolio", actor: claims organization). Accepted deviation: greenfield Epic 1 setup stories are sanctioned by the method, the seed data is a prerequisite for every user-visible story, and only core tables are created (not the full schema). No action.
2. **Epic 8 is a compliance/operations epic** with no FRs. Accepted deviation, decided with rationale in epic design: purge/backup/restore-drill are genuine standalone compliance deliverables for a PHI system; per-write audit and encryption were distributed into Epics 1–7. No action.
3. **Dense ACs**: several stories carry multi-clause ACs (e.g. 2.1, 6.3) that bundle architecture citations with behavior. Testable, but story-prep (`bmad-create-story`) should decompose them into task lists. No document change needed.
4. **FR-LOGIN-3 split across three epics** (1.3 routing, 2.1 auto-select, 4.1 seeded meetings). Documented in Epic 1's scope note and the corrected coverage map. Acceptable.

### Defects Found & Remediated During Review

1. **`expense` table had no creating story** (in architecture ERD; settled-stage payout breakdown and financials reference expense data). → Fixed: Story 3.3 now creates and seeds `expense` alongside `bill`.
2. **`treatment_plan_step` table had no creating story** (in architecture ERD; Story 2.4 renders the treatment plan card). → Fixed: Story 2.4 now creates and seeds `treatment_plan_step`.
3. **FR Coverage Map listed FR-LOGIN-3 under Epic 1 only** → corrected to the 1.3/2.1/4.1 split (step 3 of this assessment).

### Compliance Checklist Results

| Check | Result |
|---|---|
| Epics deliver user value | ✅ 7 of 8 user-value; Epic 8 accepted compliance deviation |
| Epic independence (N never needs N+1) | ✅ Verified — cross-epic seams use disabled-until-built links (3.5→4.2/6.2) and backward retro-wiring (6.1→E2/E3 commands) |
| No forward story dependencies within epics | ✅ Verified story-by-story; 4.1's "Email participants" explicitly disabled until 4.3 |
| Just-in-time schema | ✅ 25/25 ERD entities now have a creating story across 15+ story-scoped migrations; no big-bang schema |
| Starter template | N/A (none specified) — Story 1.1 is the greenfield scaffold with CI and dev environment, as required |
| Given/When/Then ACs, error/empty states | ✅ All 40 stories; error, empty, and degradation states present |
| FR traceability | ✅ 100% (step 3) |

### Story Sizing Assessment

All 40 stories judged single-dev-session completable. Largest-risk stories: 6.3 (graph + checkpoints + SSE + UI shell) and 2.2 (four stage variants) — both remain coherent single units; flag for extra care at story-prep time rather than splitting now.

## Summary and Recommendations

### Overall Readiness Status

**✅ READY** — proceed to Phase 4 (sprint planning and story implementation).

### Critical Issues Requiring Immediate Action

None. Zero critical and zero major issues remain open. Three defects found during this assessment (unassigned `expense` and `treatment_plan_step` tables; stale FR-LOGIN-3 map entry) were fixed in `epics.md` during the review.

### Standing Advisories (non-blocking)

1. **Epic 7 has no UX design contract** — the prototype offers nothing for the analyst-specific screens. Run `bmad-ux` for the analyst workspace before Epic 7 begins (there is a long runway: Epics 1–6 first).
2. **PRD substitution limits** — the BRD carries no prioritization or success metrics beyond SLA targets. If scope pressure arises mid-build, use `bmad-correct-course` rather than ad-hoc cuts, since no MoSCoW baseline exists.
3. **Deferred-decision triggers** — the architecture's Deferred list (IdP before first non-dev deployment; statutory coverage before per-state go-live; Ollama capacity before multi-user) should be tracked in sprint planning so triggers aren't missed.
4. **Dense ACs in stories 2.1, 2.2, 6.3** — decompose into task lists at `bmad-create-story` time; consider extra validation on 6.3.

### Recommended Next Steps

1. Run `bmad-sprint-planning` to generate the sprint plan from the 8 epics / 40 stories.
2. Begin the story cycle: `bmad-create-story` → validate → `bmad-dev-story` → `bmad-code-review`, starting with Story 1.1.
3. Before Epic 7: run `bmad-ux` for the analyst workspace surfaces.

### Final Note

This assessment examined 5 input documents, verified 49/49 FRs (100% coverage) plus 8 NFRs and 12 UX-DRs, validated 8 epics / 40 stories against best practices, and swept all 25 ERD entities for creating stories. It identified 3 defects (all fixed during review), 2 accepted deviations (documented with rationale), and 4 non-blocking advisories. The planning artifacts are aligned and implementation-ready.

**Assessor:** BMAD Implementation Readiness workflow (Claude) · **Date:** 2026-08-09
