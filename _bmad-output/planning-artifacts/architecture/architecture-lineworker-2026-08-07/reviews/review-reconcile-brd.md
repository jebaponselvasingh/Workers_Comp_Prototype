# Reconciliation Review — BRD vs ARCHITECTURE-SPINE

- **Spine:** `_bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md` (draft, 2026-08-07)
- **Source:** `docs/BRD-Workers-Comp-Console.md` @ git HEAD (365 lines)
- **Question:** What does the BRD require that did not land in the spine — and for each miss, is it (a) a spine change, (b) a Deferred entry, or (c) level-below detail the spine intentionally leaves open?

**Overall verdict:** The spine covers the BRD's load-bearing requirements well — every prototype limitation in §7.4 items 1–3 (auth, persistence, AI brokering) is answered by a named AD (AD-7, AD-3/AD-4, AD-5/AD-6). The misses are quiet ones: one production requirement silently dropped (supervisor drill-through), a binds header narrower than the spine's own body, one mis-homed capability-map row, and a cluster of level-below details that are fine but worth one-line anchors. No new AD is required; the fixes are edits to `binds`, the Capability Map, and Deferred.

---

## Finding-by-finding

### 1. Supervisor drill-through (§5.6 limitation, §7.4 item 5) — DROPPED, needs explicit placement — **HIGH**

BRD §5.6 and §7.4-5 make click-through from KPIs/charts/table rows into claims a **production requirement for the Supervisor**, not just the Analyst: "A production supervisor view should allow click-through to the underlying claim(s) and to a specific handler's caseload" and "make supervisor/analyst KPIs, charts and rows navigable into claims."

The spine defers only "**Analyst workspace depth (FR-AN-1..6)**" and maps FR-SUP-A..D to `services/worklist` aggregates with no navigation contract. Supervisor drill-through is neither built, deferred, nor mentioned — it falls between the FR-SUP rows (in scope) and the FR-AN Deferred bullet (out of scope). The Deferred bullet's "aggregates are built scope-aware from day one so it bolts on" covers *scoping*, not *navigability* — drill-through additionally requires every aggregate endpoint to expose (or link to) its constituent claim list.

**Disposition: spine change (small).** Either add supervisor drill-through to the FR-SUP capability-map row (e.g. "aggregate endpoints return constituent-claim filters; dashboard rows link to filtered queue") or name it explicitly in Deferred so the drop is a decision, not an accident. Recommend the former: it is a cheap API-shape convention now and expensive to retrofit (this is exactly the "bolts on" property the Deferred bullet claims to preserve).

### 2. `binds` front matter is narrower than the spine's own body — **MEDIUM**

Front matter: `binds: [FR-H-1..11, FR-SUP-A..D, FR-AN-1..6, BR-ROLE-1..2, NFR-1..4]`.

But the ADs and Capability Map themselves govern requirement IDs outside that list: AD-2 cites FR-DET-3; AD-6 cites FR-CP-1..2; AD-8 cites FR-Q-4 and FR-DET-3; the Capability Map cites FR-Q-*, FR-DET-*, FR-DIARY-*, FR-SUP-1..5-adjacent content. Entirely unbound anywhere: FR-LOGIN-1..3, FR-TOP-1..2, FR-SLA-1, FR-GLOS-1, FR-ACT-1, FR-SUP-1..5 (the numbered ones, vs the lettered consolidation), FR-DIARY-1..3.

Most of these are transitively covered by the FR-H consolidations (FR-H-5 ⊇ FR-ACT-1, FR-H-10 ⊇ FR-DIARY-1..3, FR-H-11 ⊇ glossary/RTW, FR-SUP-A..D ⊇ FR-SUP-1..5), so nothing is functionally lost — but a traceability pass against the BRD will report false gaps, and FR-SLA-1/FR-TOP-2 have no consolidated parent at all.

**Disposition: spine change (mechanical).** Extend `binds` to the full ID roster (or state "lettered/consolidated IDs subsume their numbered constituents; FR-LOGIN/FR-TOP/FR-SLA/FR-GLOS ride on AD-1/AD-7/AD-10"). No AD content changes.

### 3. §7.3 compliance is mis-homed in the Capability Map; OSHA recordability has no owner — **MEDIUM**

The Capability Map row "Audit & compliance (NFR-1, §7.3) → `services/audit` + pgaudit" conflates two different things. NFR-1/audit is correct. But BRD §7.3 is **domain compliance correctness** — OSHA recordability tracked and surfaced, ICD-10 on every claim, statutory min/max clamping, path-based forms — none of which lives in `services/audit`. Statutory clamping is AD-8/`services/financials`; path forms have their own row; but **OSHA recordability** (source flag, KPI card, the "Verify OSHA 300/301 log entry" action rule, the case-header badge) appears nowhere in the spine — not in the ERD notes, not in any AD's binds, not in the map.

**Disposition: level-below detail, but fix the map row.** OSHA is a claim column plus worklist/action logic — no invariant needed (AD-10 already governs it if any derivation appears). But the map row should be split: "Audit trail (NFR-1) → services/audit" and "Compliance correctness (§7.3: OSHA, ICD-10) → services/claims + services/worklist actions," so §7.3 doesn't silently read as "the audit log handles it."

### 4. SLA strip parity for every role (§3.2 limitation, §7.4 item 7) — structurally prevented, but unnamed — **MEDIUM**

The BRD flags that the prototype recomputes the top-bar SLA strip only for Handlers (static 0.8d/7.4d/42d/87% for Supervisor/Analyst — it even recomputes as a *side effect of adding a diary note*) and requires per-role recompute in production. The spine never mentions this, and FR-SLA-1/FR-TOP-2 are unbound (finding 2).

However, the failure mode cannot recur under the spine as written: AD-1 forbids client-side computation ("If a number appears on screen, a service computed it") and AD-10 makes SLA aggregates read-time derivations. Any role's top bar necessarily calls the same aggregate endpoint.

**Disposition: fine as level-below, contingent on finding 2.** Add FR-SLA-1/FR-TOP-2 to AD-1 and AD-10 binds so the guarantee is traceable; no new AD or convention needed. (Also confirms the diary-note→SLA-recalc coupling in the prototype is an artifact to *not* port.)

### 5. Handler performance benchmarking (FR-SUP-2/3) — covered, but its tunables are outside AD-8's binds — **LOW**

FR-SUP-B (benchmarking) is in `binds` and the map. But the benchmark's business knobs — the Complexity score blend (severity + surgery% + litigation%), the On Track/Watch/Attention deviation thresholds, and the leader/laggard callout logic — are exactly the "business-tunable rules" AD-8 routes to ZEN/JDM, yet AD-8's binds list ("priority weights, reserve bands, SLA targets, worklist caps") omits them. Risk: they get hardcoded in `services/worklist` because no rule claimed them.

**Disposition: tighten AD-8 binds** (add "handler-benchmark blends & deviation thresholds"). One-line edit; the AD's rule already covers the semantics.

### 6. Analyst target-state (§6, FR-AN-1..6) — correctly Deferred — **OK (no action beyond finding 1)**

The BRD is explicit that FR-AN-1..6 are *target state*, undifferentiated today. The spine's handling is exemplary: bound in front matter, mapped to the same scope-aware aggregates, explicitly Deferred as its own epic with the bolt-on property stated. FR-AN-6 (export) rides the same deferral. The only leak is drill-through's dual citizenship with the Supervisor (finding 1).

### 7. Glossary (§3.4, FR-GLOS-1, half of FR-H-11) — no home in the map — **LOW**

FR-H-11 is "Glossary **and** RTW-letter generation"; the map's FR-H-11 row covers only the RTW letter (copilot row). The glossary — 24 static terms with live search — appears nowhere: no map row, no ERD entity, no `web/` feature folder. It is trivially level-below (static reference data; a frontend constant or a tiny reference table both satisfy the spine), but with zero anchor a builder must guess, and "reference data in a JSON constant inside the SPA" would quietly violate nothing while contradicting the spirit of AD-3.

**Disposition: level-below; add a one-line map row** ("Glossary (FR-GLOS-1, FR-H-11) → static reference data in `web/` or `services/claims` reference endpoint — team's choice"). No AD.

### 8. RTW letter flow (§4.4.3) — covered, with one AD-2 tension worth a sentence — **LOW**

AD-6 and the map both name RTW letter generation, including the `interrupt()` gate before save. Good. Two quiet mismatches with the BRD:

1. The prototype's RTW letter is **deterministic templating** (contenteditable letter pre-populated from claim fields; Edit/Print/Copy — it is opened by a quick action but never touches the copilot). Routing it through `agents/` is a *capability upgrade*, but AD-2 then applies with force: every figure and restriction in the letter (benefit amounts, physician restrictions, graduation schedule) must come from tool output, i.e. the letter body should be template-assembled in a service and at most *polished* by the LLM.
2. The prototype has no "save" — only print/copy. AD-6's human-gated save is a new requirement the spine adds, which is fine, but print/copy (§7.4-8 "print/export") is otherwise unmentioned.

**Disposition: level-below.** AD-2 + AD-6 already constrain it correctly; the epic that builds it should cite both. No spine edit required (optionally add "RTW letter body is service-templated; LLM may rephrase, never originate figures" as an AD-2 example).

### 9. Meeting→email conversion & the diary cluster (FR-DIARY-1..3, §4.3.2, §4.4.1–2) — covered — **OK**

"✉ Email participants" (meeting→email conversion), 10 meeting types, 6 email templates, seeded demo meetings, per-claim note linkage: all are feature-level flows under the map's "Diary, meetings, emails → `services/claims` (diary aggregate)" row, with AD-4 auditing every write and the Deferred "Email/calendar egress" bullet correctly keeping prototype log-only send semantics until the integration epic. Meeting types / email templates are reference data — level-below. No action.

### 10. Per-role dashboards & per-persona scoping (§2, §5, BR-ROLE-1..2) — covered — **OK**

AD-7 (server-authoritative scoping via `handler_employer_assignment`, no caller-supplied scope) directly kills the prototype's client-side `HANDLER_MAP`, and FR-SUP-4's "charts recompute from the persona's caseload" follows from the same repository-level scope injection. Role badge/top-bar identity (BR-ROLE-2) is level-below UI. The Supervisor/Analyst *rendering* divergence is the Deferred analyst epic (finding 6). No action.

### 11. The 8 queue filters (§4.1.1, FR-Q-1) — covered; flag definitions need a real-rule note — **LOW**

Filters map to `services/worklist` under AD-2/AD-8/AD-10, and every filter predicate is either a source column or an AD-10-governed derived flag (`siu_review`, `rtw_blocked`, `payment_due`). The quiet issue: BRD §7.2 defines `rtwBlocked` and `paymentDue` partly by **deterministic hash bucket** — prototype fakery, not a business rule. The spine's Deferred covers fraud-score modeling but not the fact that these two flags have *no real definition yet*. In the target schema they are honestly derivable (`payment_due` from `PAYMENT_SCHEDULE_WEEK`, `rtw_blocked` from RTW dates/status), which AD-10 permits, but someone must decide the formulas.

**Disposition: level-below with a Deferred whisper.** Optionally add to Deferred: "Real definitions for `rtw_blocked` / `payment_due` (the prototype's hash-bucket stand-ins) — JDM rules per AD-8, derived per AD-10." Not spine-blocking.

### 12. Non-blocking notifications (NFR-3) — covered by convention — **OK**

NFR-3 is in `binds`, and the Errors convention ("SPA maps status → toast/inline per TanStack Query error boundaries") plus AD-9's state discipline eliminate `alert()` structurally for error paths. Success confirmations ("email sent") aren't literally named, but any convention-following implementation lands on toasts. Level-below; no action.

### 13. External statutory form links & live state schedules (NFR-4, §4.2 Tab 4) — mostly covered; link sourcing undecided — **LOW**

NFR-4 has two halves. **Benefit-figure validity:** covered — `STATE_RATE_SCHEDULE` entity, AD-8's typed statutory Python, and the Deferred "State statutory coverage & update cadence" bullet (which even names the refresh-ownership question). **Form download links:** the prototype's ⬇ Download links are hardcoded external NY WCB / FNSB PDF URLs. `path_required_form` reference data fixes the metadata, but whether downloads are external URLs (browser egress to state sites — an availability/consistency question, though not a PHI one) or locally mirrored PDFs (then colliding with the "Binary storage" Deferred bullet, currently scoped to *claim* documents/photos) is decided nowhere.

**Disposition: Deferred.** Widen the "Binary storage" bullet to "documents/photos **and statutory-form PDFs (mirror vs external link)**" — one clause. Also note the prototype's Path-B-always bug: FR-H-8's "target: real path classification" is implicitly handled (a `path` classification is derivable/ZEN territory under AD-8/AD-10) but never named; the FR-H-8 map row could add "incl. claim path A/B/C classification."

---

## Summary disposition table

| # | BRD item | Spine status | Disposition | Severity |
|---|---|---|---|---|
| 1 | Supervisor drill-through (§5.6, §7.4-5) | Dropped (only analyst deferred) | Spine change: map row or explicit Deferred | High |
| 2 | Unbound FR IDs (FR-SLA, FR-TOP, FR-LOGIN, FR-GLOS, FR-ACT, FR-Q/DET/CP/DIARY, FR-SUP-1..5) | binds narrower than body | Spine change: extend `binds` (mechanical) | Medium |
| 3 | §7.3 compliance / OSHA recordability | Mis-homed to services/audit; OSHA unowned | Level-below; fix map row | Medium |
| 4 | SLA recompute for every role (§3.2, §7.4-7) | Unnamed; structurally prevented by AD-1/AD-10 | Level-below; bind FR-SLA-1 (with #2) | Medium |
| 5 | Handler-benchmark tunables (FR-SUP-2/3) | Rule covered; tunables outside AD-8 binds | Tighten AD-8 binds | Low |
| 6 | Analyst target state (§6) | Deferred, correctly | None | OK |
| 7 | Glossary (FR-GLOS-1) | Absent from map | Level-below; add map row | Low |
| 8 | RTW letter flow (§4.4.3) | Covered; deterministic-templating tension with AD-6 routing | Level-below; optional AD-2 example | Low |
| 9 | Meeting→email conversion, templates, meeting types | Covered (diary aggregate + AD-4 + Deferred egress) | None | OK |
| 10 | Per-role/per-persona dashboards | Covered (AD-7) | None | OK |
| 11 | 8 queue filters; hash-bucket flag definitions (§7.2) | Filters covered; real flag formulas undecided | Level-below; optional Deferred bullet | Low |
| 12 | Non-blocking notifications (NFR-3) | Covered by Errors convention + AD-9 | None | OK |
| 13 | External form links / live schedules (NFR-4); path classification (FR-H-8) | Rates covered; PDF sourcing + path classification unnamed | Widen Deferred bullet; annotate FR-H-8 row | Low |

No missing AD was found: every BRD invariant-grade requirement maps to AD-1..10 or a convention. All fixes are edits to `binds`, three Capability Map rows, one AD-8 binds line, and two Deferred bullets.
