# Business Requirements Document (BRD)
## LINEWORKER — Manufacturing Workers' Compensation Console (Agentic AI Prototype)

| | |
|---|---|
| **Product** | LINEWORKER — Manufacturing WC Console (United States) |
| **Artifact analyzed** | `Workers_Comp_Prototype.html` (single-file interactive prototype) |
| **Prototype type** | Clickable UX design with an embedded AI "Adjuster Copilot" |
| **Dataset** | `WC_Manufacturing_Claims_2026.xlsx` — 100 unique employees · 10 employers · 15 US plants (login banner cites 27 injury types; live data contains 20) |
| **Roles** | Claims Handler · WC Supervisor · Data Analyst |
| **Document purpose** | Reverse-engineered, screen-by-screen business & functional requirements for each role, covering every menu, click, graph, form and modal in the prototype |
| **Status** | As-built specification of the prototype behavior (not a target-state spec). "Prototype limitation" callouts flag where real requirements must extend the prototype. |

---

## 1. Introduction

### 1.1 Product summary
LINEWORKER is a workers'-compensation claims console purpose-built for the **US manufacturing sector**. It presents an insurance-adjuster workflow enriched with an **agentic AI copilot** that provides claim-specific guidance (labor law, reserve adequacy, fraud checks, next-best-actions, similar-case outcomes) and generates stakeholder communications (emails, RTW letters). The system is organized around three roles, each with its own workspace.

### 1.2 Business objectives (inferred from the design)
1. **Accelerate the claim lifecycle** against explicit SLAs — pick a claim in < 1 day, approve in < 5 days, settle in < 30 days, and achieve > 80% successful return-to-work (RTW).
2. **Prioritize adjuster attention** on the highest-risk claims (litigation, fraud/SIU, RTW-blocked, surgery, pending approvals) via a computed priority score.
3. **Embed compliance & correctness** — OSHA recordability, ICD-10 coding, state statutory benefit min/max, reserve adequacy checks.
4. **Give supervisors portfolio oversight** — SLA performance, handler productivity/complexity, financial exposure, fraud/litigation concentration.
5. **Give analysts drill-down** into fraud, trends and KPIs.
6. **Reduce administrative friction** — one-click stakeholder emails (templated), meeting scheduling, RTW-letter generation, and a domain glossary.

### 1.3 Scope of this BRD
Covers all three roles and every interactive element in the prototype: the login/role selector, global top bar, glossary, the handler workspace (queue + case detail + copilot + diary/meetings/emails + modals), and the supervisor/analyst portfolio dashboard.

---

## 2. Roles, Personas & Data Scoping

The console has **three roles**, chosen on a login screen. Each role has one or more named **personas**; a persona determines which subset of the 100 claims is visible via a fixed `HANDLER_MAP`.

| Role | Icon | Tagline (as shown) | Workspace rendered | Personas & data scope |
|---|---|---|---|---|
| **WC Supervisor** *(default)* | 👔 | "Portfolio, SLA, handler performance" | Portfolio dashboard (`svView`) | **David Bline** — all 100 claims · **Jennifer Park** — Toyota / GM / 3M · **Ken Stoker** — John Deere / Lockheed Martin |
| **Claims Handler** | 📋 | "Caseload, case detail, copilot" | 3-pane handler workspace (`hvView`) | **Kaya Johnson** — Caterpillar · GE · Whirlpool · Deere · **Dante Reyes** — Boeing · Honeywell · **Marcus Chen** — Toyota · GM · **Sarah Williams** — 3M · **Liam O'Sullivan** — John Deere · **Fatima Al-Mansoori** — Lockheed Martin |
| **Data Analyst** | 📊 | "Fraud, trends, KPI drill-down" | Portfolio dashboard (`svView`) — same rendering as Supervisor | **David Bline (Analyst view)** — all 100 claims |

**Business rules on scoping**
- **BR-ROLE-1:** The visible caseload for any user MUST be limited to the claims mapped to that persona (employer-based partition). Supervisor "David Bline" and Analyst both see the full portfolio; other supervisors see only their assigned employer groups.
- **BR-ROLE-2:** Role label, badge (`👔 Supervisor` / `📊 Analyst` / `📋 Handler`), and the top-bar caseload stats MUST reflect the logged-in persona.
- **Prototype limitation:** Role and persona are **self-selected with no authentication**. A production system requires real identity, authorization, and an authoritative claim-assignment source rather than the static `HANDLER_MAP`.
- **Prototype limitation:** The **Data Analyst workspace is currently identical to the Supervisor dashboard** (same `renderSV`). Section 6 specifies the analyst-specific requirements this should become.

---

## 3. Global / Shared UX Components (all roles)

### 3.1 Login & role selector (`#loginScreen`)
- Full-screen gradient modal card titled **LINEWORKER — Manufacturing Workers Compensation Console — United States**.
- **Dataset banner:** "📊 Dataset: WC_Manufacturing_Claims_2026.xlsx · 100 unique employees · 10 employers · 15 plants · 27 injury types".
- **Role cards (3, clickable):** Supervisor (pre-selected), Claims Handler, Data Analyst. Clicking a card (`pickRole`) highlights it and repopulates the **"Log in as"** dropdown with that role's personas.
- **"Log in as" dropdown (`#usel`):** persona list scoped to the selected role.
- **"Enter Console →" button (`doLogin`):** hides the login screen, reveals the top bar, and renders the role-appropriate workspace.

**Functional requirements**
- **FR-LOGIN-1:** Selecting a role card MUST repopulate the persona dropdown with only that role's personas.
- **FR-LOGIN-2:** "Enter Console" MUST set the active user/role, compute the caseload, populate the top-bar stats, and render either the dashboard (supervisor/analyst) or the handler workspace.
- **FR-LOGIN-3:** For handlers, entering the console MUST auto-select the first claim, seed demo meetings, and render the detail + copilot panes.

### 3.2 Top bar (`.topbar`) — persistent across the session
Left→right: brand mark **LINEWORKER — Manufacturing WC**; role badge; spacer; **📖 Glossary** button; **↩ Switch** (logout) button; three live stat tiles; SLA strip; user chip (avatar initials + name + role).

| Element | Behavior / requirement |
|---|---|
| **📖 Glossary** | Opens the glossary overlay (§3.4). |
| **↩ Switch** | Logs out via full page reload (returns to login). **FR-TOP-1** |
| **Stat tile — Caseload** (`ts1`) | Count of the user's claims. |
| **Stat tile — Active Tx** (`ts2`) | Count of claims in `treatment` stage (warning color). |
| **Stat tile — High Risk** (`ts3`) | Count of claims with `risk = high` (error color). |
| **SLA strip** (4 KPIs) | Pick (< 1d target), Approve (< 5d), Settle (< 30d), RTW Rate (> 80%). Each tile carries an explanatory tooltip and a pass/warn color. |

- **FR-TOP-2:** Stat tiles MUST recompute from the logged-in persona's caseload.
- **FR-SLA-1:** SLA tiles MUST show pick/approve/settle averages and RTW rate with pass/warn coloring against targets.
- **Prototype limitation:** The top-bar SLA strip is **recalculated only for the Handler role** (`recalcSLA` runs inside the handler diary render). For Supervisor/Analyst it displays **static default values** (0.8d / 7.4d / 42d / 87%). A production build MUST recompute the strip for every role.

### 3.3 Data model (shared foundation)
Every claim record carries a rich schema (63 fields). Key groups the UI depends on:

- **Identity/context:** `employeeId, claimId, policyNum, name, gender, age, ageGroup, employer, sector, plant, state, region, role, dept, handler, supervisor, hireDate`.
- **Injury/clinical:** `injuryType, cause, bodyPart, bodyKey, icd, icdDesc, severity, sevScore, disability, recovery, surgeryRequired, treatmentPlan[], prognosis{mmi,rtw,impairment,litigation}, contraindications`.
- **Lifecycle/status:** `stage (intake|investigation|treatment|settled), status, commStatus, returnStatus, doi, froiDate, assignDate, approvalDate, rtwRec, actualRtw, daysOpen, daysRecovery, settlementDays`.
- **Risk/compliance flags:** `risk (low|med|high), oshaRecordable, litigationFlag, attorneyRep, fraudScore, fraudFlag`, plus derived `siuReview, rtwBlocked, paymentDue, nextPaymentDate`.
- **Financials:** `aww, reserve, paidIndemnity, paidMedical, paidExpense, totalPaid, totalIncurred`, plus SLA fields `slaPickDays, slaApproveDays, slaSettleDays`.
- **AI/enrichment:** `cpNextActions[], cpSimilarCase, cpReserveNote, cpFraudIndicators[]`.
- **Evidence:** `timeline[]{date,desc,tag}, documents[]{name,type,date}, photos[]{t,d}`.

**Data profile of the seed set (100 claims):** stages = intake/investigation/treatment/settled; statuses = Initial, CH Assessment Process, CH Approved, Denied, Settled, Settled & Closed; return statuses = Under Treatment / Returned and under Therapy / Returned and Fully Recovered; disability = Temporary/Permanent; fraud flags = 13; litigation = 3; OSHA recordable = 64; surgery required = 39; 6 handlers; 3 supervisors; 10 employers; 17 states; 20 distinct injury types.

### 3.4 Glossary overlay (`.glov`)
Slide-in panel titled **WC Glossary — Manufacturing** with a live **search box** filtering 24 domain terms (FNOL, TTD, PPD, PTD, MMI, IME, AWW, RTW, CTS, HAVS, NIHL, OSHA 300, FROI, ICD-10, Reserve, Subro, PPE, EMG, ORIF, Arc Flash, SLA, LTD, WPI, Apportionment, VR). Closes via ✕ or backdrop click.
- **FR-GLOS-1:** Search MUST filter by abbreviation, term, or definition text and show a "no matches" state.

### 3.5 Global non-functional / prototype limitations (apply to all roles)
- **NFR-1 (No persistence):** All edits, chats, diary notes, meetings and emails are held in memory only; a reload (including "Switch/logout") discards everything. Production requires a persistent backend.
- **NFR-2 (Offline AI):** The copilot attempts a browser-side call to the Anthropic Messages API **with no credentials**, so in practice it **always falls back to deterministic canned answers** (`offlineAnswer`) sourced from each claim's `cp*` fields. Production requires a secured, server-brokered AI integration.
- **NFR-3 (Native dialogs):** Several confirmations use `alert()` (e.g. email sent, meeting validation). Production should use in-app non-blocking notifications.
- **NFR-4 (Static content):** External form links point to NY WCB / FNSB PDFs; benefit figures are illustrative and must be validated against live state schedules.

---

## 4. ROLE 1 — CLAIMS HANDLER (detailed BRD)

The Handler is the **primary operational role** and the richest workspace. Layout = **3 columns**: **Queue** (left) · **Case Detail** (center) · **AI Adjuster Copilot** (right).

### 4.1 Queue panel (`.queue`)

**4.1.1 Header & caseload filter (`#qfSelect`)** — a dropdown with **8 filters**:

| Filter value | Shows |
|---|---|
| All claims | Entire caseload |
| Active / In treatment | `stage = treatment` |
| High risk | `risk = high` |
| Fraud alert | `fraudFlag = true` |
| Litigation | `litigationFlag = true` |
| Payment due | `paymentDue = true` |
| Surgery / complex medical | `surgeryRequired = true` |
| SIU review | `siuReview = true` |

**4.1.2 Stage grouping** — filtered claims are grouped into 4 collapsible sections with counts: **📥 Intake · 🔍 Investigation · 🩺 Treatment · ✅ Settled**. Empty stages show "No claims in this stage."

**4.1.3 Priority ordering** — within each stage, cards sort by a computed **priority score** (`priorityScore`): litigation +40, SIU +35, RTW-blocked +30, pending approval (`Initial`/`CH Assessment Process`) +25, payment due +20, surgery +15, plus 0.3×severity and 0.2×days-open (capped), minus 100 if settled. The top-3 highest-scoring cards (score > 30) get a **🔺 priority marker**.

**4.1.4 Claim card (`.qc`)** shows: priority marker, `claimId`, days-open, a **risk dot** (red/amber/green), worker name, contextual badges — **FRAUD / LITIG / PAY DUE / SIU**, truncated injury type, stage pill, and employer short name.

**Clicks:** clicking a card selects the claim, resets the detail tab to Overview, and re-renders the queue (to move selection highlight), the detail pane, and the copilot.

**Functional requirements**
- **FR-Q-1:** The filter dropdown MUST re-filter the caseload and re-render grouped, priority-sorted results.
- **FR-Q-2:** Cards MUST surface risk and the four operational flags at a glance.
- **FR-Q-3:** Selecting a card MUST drive the center and right panes to that claim.
- **FR-Q-4:** Priority scoring MUST elevate litigation, SIU, RTW-blocked, pending-approval, payment-due, and surgical claims.

### 4.2 Case Detail panel (`.det`)

**4.2.1 Case header** — worker name (H1); meta line `claimId · role · employer · state`; injury summary line `injuryType — bodyPart, cause · ICD-10 · severity`; a **badge row** (stage pill + conditional **⚠ Fraud Score**, **⚖ Litigation**, **🔪 Surgery**, **OSHA Rec.**); and a **risk gauge** SVG (semicircle colored by risk, labeled Low/Medium/High).

**4.2.2 Tab bar (6 tabs)** — **Overview · Injury Diagram · Bills & Payments · Documents & ID · Photos (n) · AI Insights**. Clicking a tab re-renders the tab content.

#### Tab 1 — Overview (stage-adaptive; `ovHTML`)
Always begins with a **4-step stage stepper** (Intake → Investigation → Treatment → Settled) marking done/current. The body varies by stage:

- **Intake** (`intakeOverviewHTML`): Intake summary card + Reported-injury card + **📋 Intake document checklist** (required = FROI, MEDAUTH, WAGE, INCIDENT, each "Received/Missing") + Upcoming Actions block + Case timeline.
- **Investigation** (`investigationOverviewHTML`): **Editable injury card** (injury type, cause, body part, ICD-10, disability, recovery window are inline-editable) + Financials/reserve card (with indemnity/medical cost bar) + **Benefit calculation card** + Actions block + full timeline.
- **Treatment** (`treatmentOverviewHTML`): **🩺 Current treatment phase** banner (Early / Active / Approaching-MMI, derived from days-open vs recovery window) + **💵 Bills-paid-vs-reserve** card with "View full Bills & Payments →" jump + **🤝 Care & RTW coordination** card (status = On Track / Coordination Gap / Awaiting Information / Legal Coordination) + Benefit card + Actions block + recent timeline.
- **Settled** (`settledOverviewHTML`): **✅ Claim settled & closed** banner + Final payout breakdown (indemnity/medical/expense bar) + Claim outcome card + **📝 Summary of actions taken** (full timeline).

#### Tab 1a — Benefit calculation & reserve (embedded card, `benefitCardHTML`)
- **Editable comp-rate input** (`% of AWW`, default 66.67%, or 100% for PTD when permanent + severity ≥ 85); an override recalculates weekly indemnity and shows a **↺ reset** control.
- Displays **weekly indemnity benefit** (clamped to the state statutory min/max via `STATE_WC_RATES`), **indemnity type** (TTD / TPD / PPD / PTD, derived from disability + return status), state min/max, payment schedule note (weekly after a 7-day waiting period), and an auto-generated **reserve rationale** paragraph.

#### Tab 1b — Upcoming actions required (embedded, `actionsBlockHTML` / `buildActions`)
Auto-generated, claim-specific task list (max 6), each with an urgency tag, an optional **"go to" button**, and a **status-toggle** button. Action rules:

| Trigger | Action | Go-to button | Status toggle |
|---|---|---|---|
| `treatment` | Review latest medical bill for coding accuracy | View Bill (Bills tab) | Mark Reviewed |
| `treatment` | Complete weekly diary check-in | Log Diary Entry (Diary) | Mark Logged |
| surgery & not settled | Confirm surgical pre-authorization on file | View Documents | Mark Confirmed |
| RTW-blocked | Follow up on overdue RTW | Open RTW Policy | Mark Followed Up |
| treatment + temporary | Check light/modified-duty availability | Open RTW Policy | Mark Followed Up |
| litigation | Coordinate with defense counsel | Schedule Meeting | Mark Coordinated |
| SIU review | Escalate to SIU | View Fraud Indicators (AI Insights) | Mark Escalated |
| OSHA & not settled | Verify OSHA 300/301 log entry | View Documents | Mark Verified |
| payment due | Confirm indemnity disburses on time | View Bill | Mark Confirmed |
| status Initial / CH Assessment | Complete assessment & **issue approval** | **Approve Assessment** | — |
| (padding to ≥3) | Reassess reserve adequacy; verify claimant contact | — | Mark Reviewed / Verified |

- **FR-ACT-1:** Actions MUST be generated from claim state; each "go to" MUST deep-link to the relevant tab/modal; **Approve Assessment** MUST set `status = CH Approved` and re-render queue + detail.

#### Tab 2 — Injury Diagram (`injHTML`)
- Interactive **body silhouette SVG** with animated markers at injured body regions, colored by severity; the **primary** injury pulses.
- **Editable body-part selector**, an **"+ Add another injury"** popover (body part + injury type + severity 0–100 → adds a secondary marker; secondary markers are removable via ✕).
- **Editable severity score** input (0–100) that recomputes severity band + risk.
- Cards: ICD-10 diagnosis, **Prognosis** (MMI / RTW outlook / impairment / litigation risk), **Treatment plan** (numbered), and **⚠ Restrictions** (contraindications).

#### Tab 3 — Bills & Payments (`billsHTML`)
- **Financial summary** (Total Claim Amount projected, Paid To Date, Reserve Remaining, **Reserve Check** = Light / Adequate / Heavy with rationale).
- Metrics row: Weekly Indemnity, Installments Paid, Next Payment Due, Bills On File.
- **Indemnity payment schedule** table (week-by-week with status Paid / Due This Week / Upcoming / Pending Approval).
- **Medical bills** list (built from claim characteristics: initial treatment, surgery/facility, imaging, PT, follow-up, pharmacy; each Paid / Under Review / Pending Submission).

#### Tab 4 — Documents & ID (`docsHTML`)
- **Path-based required forms** card (`pathDocsHTML`) — Path A minor / **Path B follow-up** / Path C fatality, listing the statutory forms (C-2F, C-3, RFA-1W, C-4.3, AFF-1, etc.) with descriptions, timing, and **⬇ Download** links.
  - **Prototype limitation:** the `path` field is **not present in the data**, so this always renders **Path B**. A real system must classify each claim's path.
- **Employee ID card** (visual).
- **Claim documents** list — clicking a row opens a **document viewer modal** (FROI renders full injury detail; others a summary sheet).

#### Tab 5 — Photos (`photosHTML`)
- Grid of incident/site photo cards (caption + source). Clicking a card opens a **photo viewer modal**. Empty state supported.

#### Tab 6 — AI Insights (`insightsHTML`)
- **🤖 Similar case outcomes**, **💰 Reserve adequacy review**, **⚡ Next best actions** (numbered), and **🔍 Fraud risk indicators** (score + red-flag list, or a low-risk confirmation). All sourced from the claim's `cp*` fields.

**Detail-panel functional requirements**
- **FR-DET-1:** The Overview MUST adapt content to the claim's lifecycle stage.
- **FR-DET-2:** Inline edits (injury fields, body part, severity, comp rate, added injuries) MUST update the record and re-render dependent calculations (benefit, reserve, risk).
- **FR-DET-3:** Reserve Check MUST compare projected remaining exposure (unpaid indemnity + unpaid medical) to the current reserve and classify Light/Adequate/Heavy.
- **FR-DET-4:** Documents and photos MUST open read-only viewer modals.

### 4.3 AI Adjuster Copilot panel (`.copilot`)
Header shows a live "pulse" indicator, "AI Adjuster Copilot," and the active claim context. Two tabs: **⚡ Actions** and **📓 Diary**.

**4.3.1 Actions tab**
- **Quick-action buttons (`QAS`, 7):** **§ Labor law & state rules · ↻ Similar case outcomes · 📄 Review RTW Policy · ✓ Reserve review · 🔍 Fraud risk check · ! Next best actions · 📊 Data alignment note.** Each button (except RTW) submits a claim-specific prompt to the copilot; **📄 Review RTW Policy** opens the RTW template modal instead.
- **Chat log** with a seeded case-summary greeting; a **free-text input** ("Ask about this case…") + send button; a disclaimer ("Informational only — not legal advice").
- **FR-CP-1:** Quick actions MUST send claim-contextual prompts; the assistant MUST answer per claim (live model when available, otherwise the deterministic offline briefing).
- **FR-CP-2:** Chat history MUST be maintained per claim within the session.

**4.3.2 Diary tab** — three sub-tabs: **📓 Notes · 📅 Meetings · ✉ Emails**.

- **Notes:** time-of-day greeting, today's date, active claim, **today's meetings** summary (with "✓ Done" / "Open Claim" actions), and a reverse-chronological note list. Add-note input (attaches the current claim id). *Adding a note also triggers a top-bar SLA recalc.*
- **Meetings:** list sorted by date with Upcoming/Done styling; each card shows type, date/time, location, linked claim, notes, participant tags, and actions **✓ Done / ✉ Email participants / Delete**. Two demo meetings are seeded on first login. **"＋ Schedule Meeting"** opens the meeting modal.
- **Emails:** reverse-chronological list of sent emails (subject, recipients, snippet, sent-badge). **"＋ Compose Email to Stakeholders"** opens the email composer.

**FR-DIARY-1:** Handlers MUST be able to log dated notes tied to a claim; **FR-DIARY-2:** schedule/complete/delete/convert-to-email meetings; **FR-DIARY-3:** compose, template, and log stakeholder emails.

### 4.4 Handler modals

**4.4.1 Meeting Scheduler (`#meetingModal`)** — fields: **Meeting Type** (10 options: 3-Point Contact Initial, RTW Conference, NCM Care Coordination, IME Preparation, Settlement Discussion, Physician Consultation, Employer Accommodation Review, Litigation Prep, Claim Review — Supervisor, Other), read-only linked claim, date, time, **participants** (Employee / Employer HR / Nurse Case Manager / Treating Physician / My Supervisor / Attorney), notes/agenda, location/link. Saves into the meeting store; date is required.

**4.4.2 Email Composer (`#emailModal`)** — **recipient checkboxes** (Employee / Employer HR / NCM / Physician / Supervisor / Attorney); **6 quick templates** — **3-Point Contact · RTW Offer · NCM Referral · Status Update · IME Request · Settlement Notice** (each pre-fills subject, body and recipient set with claim data); subject; body; read-only claim reference; **priority** (Normal / High / Urgent). "Send Email (logged)" records it and shows a confirmation. Subject is required.

**4.4.3 RTW Policy Template (`openRTW`)** — a wide modal with an **editable (contenteditable)** Return-to-Work offer letter pre-populated from claim data, plus **✏ Edit / 🖨 Print / 📋 Copy** controls. Sections: purpose, modified/light-duty assignment, physician restrictions honored, graduation schedule, response-required clause.

**4.4.4 Document viewer / Photo viewer / Generic modal (`showModal`)** — read-only detail sheets (§4.2 tabs 4–5).

### 4.5 Handler — consolidated functional requirements
- **FR-H-1** Prioritized, filterable, stage-grouped caseload.
- **FR-H-2** Stage-adaptive case file with editable clinical + financial fields.
- **FR-H-3** Automated benefit/indemnity calculation with state min/max clamping and manual comp-rate override.
- **FR-H-4** Reserve adequacy evaluation with actionable Light/Adequate/Heavy verdict.
- **FR-H-5** Auto-generated, deep-linked action checklist including one-click claim approval.
- **FR-H-6** Injury visualization with multi-injury capture and severity editing.
- **FR-H-7** Bills & week-by-week indemnity schedule tracking.
- **FR-H-8** Path-based statutory forms with downloads (target: real path classification).
- **FR-H-9** Claim-aware AI copilot (guidance + generation) with per-claim chat history.
- **FR-H-10** Diary, meeting scheduling, and templated stakeholder email — all claim-linked and logged.
- **FR-H-11** Glossary and RTW-letter generation.

---

## 5. ROLE 2 — WC SUPERVISOR (detailed BRD)

The Supervisor workspace is a single-scroll **Portfolio Overview dashboard** (`svView` / `renderSV`) scoped to the persona's caseload (David Bline = all 100; Jennifer Park = Toyota/GM/3M; Ken Stoker = Deere/Lockheed). No case-editing; it is analytical/oversight-oriented.

### 5.1 Dashboard header
Title **"Manufacturing WC — Portfolio Overview 2026"** with a dataset chip (claim count · 10 employers · 15 US plants).

### 5.2 KPI cards
**Row 1 (6):** Total Claims · Under Treatment · Settled & Closed · **High Risk** (severity ≥ 65) · **Total Paid** ($) · **Total Reserve** ($).
**Row 2 (4):** **Fraud Flags** (score ≥ 55) · **OSHA Recordable** · **Litigation** (attorney represented) · **Surgery Required**.
- **FR-SUP-1:** KPIs MUST aggregate over the persona's caseload with the stated thresholds.

### 5.3 Claim-handler performance table
A ranked table with a leader/laggard callout ("🏆 X leads the desk … ⚠️ Y is running slowest"). Columns: **# · Handler · Cases · Cycle Speed** (bar vs. peers) **· Avg Days · RTW % · Complexity** (Low/Med/High score blending severity, surgery %, litigation %) **· Pending Approvals · Status** (On Track / Watch / Attention, from cycle-time deviation vs the portfolio).
- **FR-SUP-2:** Rank handlers by composite cycle time (pick + approve + settle) and surface RTW %, complexity, and pending approvals per handler.
- **FR-SUP-3:** Flag handlers running materially slower than the portfolio for a workload check-in.

### 5.4 Analytical charts

| Chart | Type | Content |
|---|---|---|
| **Settlement status** | Donut | Settled / Under Treatment / Intake+Investigation |
| **Severity distribution** | Donut | High (≥65) / Medium / Low |
| **SLA performance** | 3 tiles | Avg Pick (<1d) · Avg Approve (<5d) · Avg Settle (<30d), pass/fail colored |
| **Recovery status** | 3 bars | Fully Recovered / Under Treatment / Under Therapy |
| **Injury type distribution (top 8)** | Horizontal bars | Manufacturing-specific injury frequency |
| **Total paid by employer** | Horizontal bars | $ paid per employer (color-coded) |
| **Claims by US state (top 10)** | Horizontal bars | Geographic distribution |

- **FR-SUP-4:** Charts MUST recompute from the persona's caseload and use consistent thresholds/coloring.

### 5.5 Priority claims table (top 30)
Columns: **Claim ID · Worker · Employer · Injury Type · Severity · Fraud Score · Handler · Days Open · Priority Next Best Action · Status** (litigation-flagged rows show a LITIG chip). Population = active-treatment ∪ fraud-flagged ∪ litigation-flagged, capped at 30.
- **FR-SUP-5:** Surface the highest-attention claims across the portfolio with the AI-suggested next action and handler ownership.

### 5.6 Supervisor — consolidated functional requirements
- **FR-SUP-A** Portfolio KPI oversight (volume, financial exposure, risk/compliance concentration).
- **FR-SUP-B** Handler productivity & complexity benchmarking with SLA-deviation status.
- **FR-SUP-C** Distribution analytics (severity, settlement, recovery, injury, employer spend, geography).
- **FR-SUP-D** Portfolio-wide priority worklist with ownership and recommended actions.
- **Prototype limitation:** the dashboard is **read-only** and offers **no drill-through** from a KPI/chart/row into a claim. A production supervisor view should allow click-through to the underlying claim(s) and to a specific handler's caseload.

---

## 6. ROLE 3 — DATA ANALYST (detailed BRD)

**As-built:** The Analyst logs in as "David Bline (Analyst view)" over the full 100-claim portfolio and is shown the **same dashboard as the Supervisor** (`renderSV`). Only the role badge (📊 Analyst) and label differ. Everything in §5 (KPIs, handler table, all charts, priority table) is therefore available to the Analyst today.

**Intended purpose (per the role tagline "Fraud, trends, KPI drill-down"):** the Analyst is meant to go **deeper** than the Supervisor's oversight — into fraud analytics, temporal/segment trends, and KPI decomposition.

### 6.1 Analyst functional requirements (target state)
- **FR-AN-1 (Fraud analytics):** Dedicated fraud workspace — fraud-score distribution, the 13 flagged claims (score ≥ 55), `cpFraudIndicators` aggregation, SIU-review pipeline, and fraud rate by injury type / employer / handler.
- **FR-AN-2 (Trends):** Time-series on FNOL/DOI volume, average days-open, settlement cycle time, RTW rate, and cost trends; cohort comparisons (severity band, disability type, sector).
- **FR-AN-3 (KPI drill-down):** Every KPI, chart segment and table row MUST be **clickable to drill into the constituent claims** (the capability the current read-only dashboard lacks).
- **FR-AN-4 (Segmentation):** Slice/filter by employer, sector, state/region, injury type, ICD-10, severity band, age group, gender, disability type.
- **FR-AN-5 (Financial analytics):** Paid vs. reserve vs. incurred; reserve-adequacy distribution (Light/Adequate/Heavy) across the portfolio; cost-driver analysis (surgery, litigation).
- **FR-AN-6 (Export):** Export filtered datasets and chart data for offline analysis (the dashboard references the source Excel workbook and sheet names, implying an audit/export expectation).
- **Prototype limitation / gap:** None of FR-AN-1…6 are differentiated from the Supervisor view today. Closing this gap is the core Analyst requirement.

---

## 7. Cross-Cutting Requirements

### 7.1 SLA definitions (governing KPIs across roles)
| SLA | Definition | Target |
|---|---|---|
| **Pick** | FROI → handler assignment | < 1 day |
| **Approve** | FROI → claim approval | < 5 days |
| **Settle** | FROI → settlement | < 30 days |
| **RTW Rate** | % of settled claims with successful RTW | > 80% |

### 7.2 Derived operational flags (drive prioritization & worklists)
- **SIU review:** `fraudFlag AND fraudScore ≥ 60`.
- **RTW blocked:** in treatment, under treatment, and (deterministic hash bucket OR high risk).
- **Payment due:** in treatment and (deterministic hash bucket).

### 7.3 Compliance & correctness
- OSHA recordability tracked and surfaced; ICD-10 coding on every claim; state statutory benefit min/max enforced in indemnity computation; path-based statutory forms (A/B/C) with timing guidance.

### 7.4 Consolidated prototype limitations → production requirements
1. **Authentication & authorization** — replace self-select roles/personas and the static `HANDLER_MAP` with real identity and an authoritative assignment source.
2. **Persistence** — all case edits, notes, meetings, emails, chats must persist to a backend and be auditable.
3. **AI integration** — broker AI calls server-side with credentials, guardrails, and citation of legal sources; the current client-side call is credential-less and always falls back offline.
4. **Analyst differentiation** — build the fraud/trends/drill-down analyst workspace (§6).
5. **Drill-through** — make supervisor/analyst KPIs, charts and rows navigable into claims.
6. **Claim-path classification** — populate the `path` field so Documents shows the correct A/B/C statutory forms.
7. **SLA strip parity** — recompute the top-bar SLA strip for all roles, not just handlers.
8. **Real integrations** — email send, meeting scheduling (calendar), document management, print/export, and live state benefit schedules.
9. **UX polish** — replace `alert()`/native dialogs with in-app notifications; add empty/error/loading states throughout.

---

## 8. Appendix — Element-to-requirement index (quick reference)

| UX element | Role(s) | Requirement(s) |
|---|---|---|
| Role cards + persona dropdown + Enter | All | FR-LOGIN-1..3, BR-ROLE-1..2 |
| Top bar stats / SLA strip / Glossary / Switch | All | FR-TOP-1..2, FR-SLA-1, FR-GLOS-1 |
| Caseload filter (8) + stage groups + priority cards | Handler | FR-Q-1..4 |
| Case header + risk gauge + 6 tabs | Handler | FR-DET-1..4 |
| Overview (4 stage variants) + stepper | Handler | FR-DET-1, FR-H-2 |
| Benefit/comp-rate + reserve check | Handler | FR-H-3..4, FR-DET-3 |
| Upcoming actions (11 rules) + approve | Handler | FR-ACT-1, FR-H-5 |
| Injury diagram + multi-injury + severity edit | Handler | FR-H-6 |
| Bills & payment schedule | Handler | FR-H-7 |
| Documents/ID + path forms + viewer | Handler | FR-H-8, FR-DET-4 |
| Photos + viewer | Handler | FR-DET-4 |
| AI Insights | Handler | FR-H-9 |
| Copilot quick actions (7) + chat | Handler | FR-CP-1..2 |
| Diary / Meetings / Emails (+3 modals, 6 email templates, 10 meeting types, RTW letter) | Handler | FR-DIARY-1..3, FR-H-10..11 |
| KPI cards (10) | Supervisor / Analyst | FR-SUP-1 |
| Handler performance table | Supervisor / Analyst | FR-SUP-2..3 |
| Donuts / SLA tiles / recovery / bar charts (7) | Supervisor / Analyst | FR-SUP-4 |
| Priority claims table (top 30) | Supervisor / Analyst | FR-SUP-5 |
| Fraud / trends / drill-down / segmentation / export | Analyst (target) | FR-AN-1..6 |

*End of document.*
