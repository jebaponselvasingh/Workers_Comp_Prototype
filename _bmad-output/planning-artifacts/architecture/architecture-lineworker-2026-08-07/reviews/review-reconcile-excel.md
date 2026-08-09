# Reconciliation Review — WC_Feature_Element_Details.xlsx (WC_Overview) vs ARCHITECTURE-SPINE.md

- **Spine:** `_bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md` (draft, 2026-08-07)
- **Source:** `docs/WC_Feature_Element_Details.xlsx`, sheet `WC_Overview` (full dump reviewed, 96 spec rows)
- **Question:** What does the Excel specify that did not land in the spine?

## Verdict

The spine absorbs the Excel well — write-back semantics, the 7-item cap, RBAC scoping, SLA tunables, and derived-field discipline all have a governing AD — but it has one genuine architectural gap (cached `cp_*` AI narratives collide with AD-10/AD-6), three ERD name omissions (Glossary, EmailTemplates, Employees), and one un-homed process implied throughout the Excel (the payment batch).

---

## 1. ERD coverage — every table the Excel names

| Excel table | In spine ERD? | Disposition |
| --- | --- | --- |
| Claims | Yes (`CLAIM`) | Covered |
| Documents | Yes (`DOCUMENT`) | Covered |
| Bills | Yes (`BILL`) | Covered |
| Expenses | Yes (`EXPENSE`) | Covered |
| PaymentSchedule | Yes (`PAYMENT_SCHEDULE_WEEK`) | Covered |
| Timeline | Yes (`TIMELINE_EVENT`) | Covered |
| TreatmentPlan | Yes (`TREATMENT_PLAN_STEP`) | Covered |
| AdditionalInjuries | Yes (`ADDITIONAL_INJURY`) | Covered |
| Photos | Yes (`PHOTO`); binaries handled by Deferred bullet | Covered |
| DiaryNotes | Yes (`DIARY_NOTE`) | Covered (see 1c) |
| Meetings | Yes (`MEETING`) | Covered (see 1c) |
| Emails | Yes (`EMAIL_LOG`) | Covered (see 1c) |
| StateRateSchedule | Yes (`STATE_RATE_SCHEDULE`) | Covered |
| PathRequiredForms | Yes (`PATH_REQUIRED_FORM`) | Covered |
| HandlerEmployerAssignment | Yes (AD-7 + ERD) | Covered |
| Handlers | Yes (`HANDLER`) | Covered |
| CopilotChatLog | Explicitly superseded (AD-3: LangGraph checkpointer tables, keyed per AD-6 `(claim_id, user_id)`) | Covered — deliberate substitution, traceable |
| **Glossary** | **No** | **Miss — spine change needed** |
| **EmailTemplates** | **No** | **Miss — spine change needed** |
| **Employees** | **No** | **Miss — decision needed** |

### 1a. Glossary (Excel row: Glossary button)
The Excel names a `Glossary` reference table (`term, abbreviation, definition, category`) backing an always-visible header feature. The ERD's stated contract is "names + relationships; detail owned by migrations" — column detail may live below, but the *name* is exactly what the ERD owns, and it is absent. It is also absent from the Capability → Architecture map (the header/glossary feature maps to no row). **Spine change needed:** add `GLOSSARY_TERM` as reference data in the ERD (no relationships needed) — one line.

### 1b. EmailTemplates (Excel row: Diary — Emails tab)
`EmailTemplates(template_key, subject, body, default_recipients)` is static reference data driving the templated composer. The Deferred bullet "Email/calendar egress" covers *sending*, but the composer + templates + `EMAIL_LOG` write are in-scope day one (FR-H-10 area, `services/claims` diary aggregate). Templates have no home: not in ERD, and the Config convention ("rule-document versions, SLA targets read from config/DB") doesn't name them. **Spine change needed:** add `EMAIL_TEMPLATE` reference entity (or explicitly assign templates to config/seed data — either is fine, but pick one).

### 1c. Employees (Excel row: Employee ID card)
Row 69 is the only row that cites a distinct `Employees` table (`Employees.name, Employees.employee_id`); every other row reads worker identity off `Claims.*` (`Claims.name`, `Claims.employee_id`). The spine ERD embeds the worker in `CLAIM` and has no claimant entity. This is a normalization decision the spine currently makes silently. Given one-claimant-per-claim in the dataset and no cross-claim claimant features in scope, embedding is defensible — but the Excel names the table, so the spine should say so out loud. **Spine change needed (small):** one line in Consistency Conventions or under the ERD stating "worker identity is denormalized onto `CLAIM`; no separate `EMPLOYEE` entity (Excel row 69's `Employees.*` maps to `CLAIM` columns)" — or add the entity if a claimant registry is wanted later.

Also noted: the ERD links `DIARY_NOTE`/`MEETING`/`EMAIL_LOG` only to `HANDLER`, while the Excel gives each a `claim_id` link. **Fine as level-below detail** (migrations own it), but the `CLAIM ||--o{ …` edges are cheap to add and would make the diary aggregate's shape honest.

---

## 2. Write-back semantics — governed by an AD?

| Excel semantic | Governing AD | Disposition |
| --- | --- | --- |
| `Documents.reviewed_flag` / `confirmed_flag` updates (row 29) | AD-4 (cites rows 29, 64–66 by name); Capability map "Action worklist + approvals" | Covered |
| Confirm enabled only after Review (ordering rule) | AD-4 audits it; ordering itself is service-command logic | Fine as level-below detail |
| Bills/Expenses status transitions on Approve Payment | AD-4 (audited commands) + enum convention lists the statuses | Covered, with two caveats below |
| PaymentSchedule week approval → status write-back, reflected in financial summary | AD-4 + AD-2 (schedule generation in `services/financials`) | Covered |
| 7-item action-list cap, category priority order | AD-8 explicitly binds "worklist caps" as ZEN tunables | Covered — correctly placed as tunable, not code constant |
| Shared status between Upcoming-actions and Bills/Expenses ledgers | AD-1 (server computes both views from the same columns) + AD-10 (no duplicated derived state) | Covered implicitly — fine as level-below detail |
| Action list itself recomputed per render, paid/confirmed items drop off | AD-10 (derivation at read time) | Covered |

**Caveat A — the payment batch is un-homed (spine change or Deferred entry needed).** Rows 29, 64, 65, 66 all say approval means "the payment will be scheduled for the next batch." That implies an asynchronous batch payment run — a scheduler/job that moves `Payment Scheduled → Paid` outside any request. The spine has no background-job element anywhere: no worker, no scheduler, no Deferred bullet. Either add a structural element (even "in-process APScheduler job inside `api`, writes via AD-4 commands") or a Deferred bullet ("Payment batch execution — approval sets status only; the disbursement batch is a later epic"). Right now a builder cannot tell whether "batch" is in scope.

**Caveat B — the Excel contradicts itself on the target status, and the spine doesn't arbitrate.** Row 29 says Approve Payment sets `Bills.status / Expenses.status = 'Paid'`; rows 64–66's prose says approval sets status = `'Payment Scheduled'` (with the batch later making it Paid), while row 64's data-mapping column simultaneously says "UPDATE target: → 'Paid'". The spine's enum convention lists the statuses but not the canonical transition chain (`Under Review → Payment Scheduled → Paid`, terminal states, who may move what). One sentence in the enum convention row naming the canonical transition (approval → `Payment Scheduled`; only the batch/settlement sets `Paid`) resolves the source conflict. **Fine as convention-level fix; flagged because the source is genuinely ambiguous.**

---

## 3. AI-generated `cp_*` fields — architectural home

The Excel (rows 77–80) specifies four AI-generated artifacts with explicit persistence: "Output cached as `Claims.cp_similar_case`", `Claims.cp_reserve_note`, `Claims.cp_next_actions[]`, `Claims.cp_fraud_indicators[]`.

The spine gives these no home, and its ADs actively squeeze them from both sides:

- **AD-10** says derived values are never stored as editable columns and may be cached *only* if refresh is owned by the computing service — but AD-10's binds list (`days_open`, `risk`, `siu_review`, …) omits all `cp_*` fields, and `cp_*` are LLM narratives, not rule derivations, so it's unclear AD-10 even claims them.
- **AD-6** says any agent node that persists a change must pass `interrupt()` with explicit user approval — read literally, an agent caching its own insight output would require human approval per refresh, which is nonsense for a cache but is what the rule says.
- **AD-3** says everything lives in the one PostgreSQL, so *where* is answerable — but not *what table*, *who refreshes*, or *how staleness is handled* (the Excel prompts embed volatile inputs: `days_open`, `stage`, `total_paid`, latest Timeline row — cached output goes stale by construction).
- The Capability map row "Similar-case + labor-law RAG (AI Insights tab)" routes to `services/rag` + `agents/` but says nothing about caching, and the enum/ERD contain no `cp_*` or insight-cache entity.

**Spine change needed (this is the most substantive miss):** add an explicit rule — e.g. an `AI_INSIGHT` cache table (`claim_id, kind, content, model, input_fingerprint/generated_at`) owned by `services/rag`, **not** columns on `CLAIM`; declare it a cache in AD-10's sense (service-owned refresh keyed on input fingerprint, never user-editable); and carve the exemption in AD-6 that persisting *insight cache entries* is not a claim-data write and does not require `interrupt()` (it still emits an AD-4 audit event as an agent-initiated write). `cp_fraud_indicators` should additionally cross-reference the existing "Fraud-score modeling" Deferred bullet: the numeric `fraud_score` stays a stored/deferred field, the indicator *narrative* joins the insight cache. One new short AD (or an amendment block to AD-10) covers all four fields.

---

## 4. Everything else checked — landed or acceptably below the spine

- **SLA strip (Pick/Approve/Settle/RTW targets)** — AD-8 names SLA targets as ZEN tunables; source date fields (`froi_date`, `assign_date`, `approval_date`, `settlement_date`) are migrations detail. Covered.
- **Priority score weights, priority flag (top-3 > 30)** — AD-2/AD-8/AD-10 via `services/worklist`. Covered.
- **Benefit calc (comp-rate default incl. PTD 100% rule, state min/max clamp, 7-day wait)** — AD-2 + AD-8 statutory-Python rule, `STATE_RATE_SCHEDULE` in ERD. Covered.
- **Editable fields (injury narrative, comp-rate %, severity score, body key)** — AD-4 audited commands + AD-9 optimistic mutations; recompute of `risk_level` on severity change is AD-10 read-time derivation. Covered.
- **Deterministic-hash synthetic bill/expense amounts (rows 65–66)** — prototype data-synthesis logic, not production behavior; belongs to seed/migration scripts. Fine as level-below detail (worth one line in the data-seeding story, not the spine).
- **Reserve check bands (115%/60%), treatment-phase banding, financial-summary fallback derivation (row 61)** — AD-2/AD-8/AD-10 service computations. Covered; the row-61 fallback ("if paid_* snapshot is 0, derive from ledgers") is exactly the stale-snapshot problem AD-10 exists to kill — in production the snapshot fields should simply *be* the read-time derivation. Fine.
- **RTW letter flow (template pre-fill, not an LLM call per row 86)** — AD-6 human-gated write covers the save; note the Excel says the chip is template-fill, so it need not enter the LangGraph graph at all. Level-below detail.
- **Chat history persistence** — CopilotChatLog consciously superseded (AD-3/AD-6). Covered. (If compliance later needs queryable chat transcripts rather than checkpoints, that's a new requirement, not a spine miss today.)
- **Glossary/disclaimer/static text, tab switches, role badge, logout** — session/static concerns; conventions cover auth/session. Fine.
- **Photos binaries, email egress, analyst depth, fraud model, state coverage** — all present in Deferred. Covered.

---

## 5. Findings summary

| # | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| F1 | High | Cached AI narratives (`cp_similar_case`, `cp_reserve_note`, `cp_next_actions`, `cp_fraud_indicators`) have no home; AD-10 excludes them from its binds and AD-6's interrupt rule would absurdly gate cache writes | Spine change: `AI_INSIGHT` cache entity + AD-10 amendment + AD-6 exemption |
| F2 | Medium | ERD omits three Excel-named tables: `Glossary`, `EmailTemplates`, `Employees` (worker identity silently denormalized onto CLAIM) | Spine change: add two reference entities + one explicit denormalization note |
| F3 | Medium | "Scheduled for the next batch" (rows 29/64/65/66) implies a payment-batch job; spine has no background-process element and no Deferred entry for it | Spine change or Deferred entry — currently undecidable by a builder |
| F4 | Low | Excel self-contradicts on approve-payment target status (`'Paid'` row 29 vs `'Payment Scheduled'` rows 64–66); spine enum convention lists statuses but not the canonical transition chain | Convention-level fix: name the transition chain once |
| F5 | Low | ERD lacks `CLAIM` edges for `DIARY_NOTE`/`MEETING`/`EMAIL_LOG` despite Excel `claim_id` on each | Fine as level-below detail; cheap ERD addition |

Write-back semantics overall: **well covered** — AD-4 cites the exact Excel rows, AD-8 owns the 7-item cap, and the Capability map routes the worklist to audited commands. The misses are the AI-cache contradiction (F1) and the two scope-edge omissions (F2, F3).
