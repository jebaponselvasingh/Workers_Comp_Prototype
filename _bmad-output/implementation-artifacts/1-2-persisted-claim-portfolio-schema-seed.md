# Story 1.2: Persisted Claim Portfolio (Schema & Seed)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims organization,
I want the claim portfolio persisted in PostgreSQL,
so that all consoles operate on durable, auditable data instead of a browser array.

## Acceptance Criteria

1. **Given** Alembic migrations on a fresh DB, **when** `alembic upgrade head` runs, **then** `employer`, `employee`, `claim`, `app_user`, `user_employer_assignment`, and `audit_event` exist with the Excel's canonical snake_case `Table.Column` naming, surrogate int PKs, unique business ID `WC-nnnn` on claim, `version` on mutable entities, money as integer cents, and snake_case enums.
2. **Given** the seed migration, **when** it runs, **then** the prototype's 100 claims, 10 employers, and 9 personas load — `scope_all = true` for the full-portfolio supervisor and analyst personas, enumerated employer assignments for everyone else.
3. **Given** the `audit_event` table, **when** DB grants are inspected, **then** the application DB role holds INSERT-only rights on it (no UPDATE/DELETE).
4. **And** no derived value (days_open, risk, operational flags) exists as a user-writable column.

## Tasks / Subtasks

- [ ] Task 1: DB roles & extension groundwork (AC: 1, 3)
  - [ ] Migration enabling `CREATE EXTENSION IF NOT EXISTS vector` (pgvector ≥ 0.8.2 — the image from Story 1.1 bundles it; this story activates it so AD-3's single-store commitment is real from the first schema)
  - [ ] Introduce the two-role model: the **migration/owner role** (runs Alembic; used by the e2e reset fixture) and the **runtime app role** `lineworker_app` (what the api container connects as; no DDL rights). Config module gains `alembic_database_url` alongside `database_url`; the api entrypoint from 1.1 runs `alembic upgrade head` under the owner URL, then uvicorn connects as the app role
  - [ ] Grants migration: app role gets CRUD on domain tables, but **INSERT-only** on `audit_event` (no UPDATE/DELETE/TRUNCATE)
  - [ ] Create the `audit_redactor` DB role per the AD-4 registered exception: exactly two grants on `audit_event` — UPDATE on the `before`/`after` columns, and DELETE constrained by a row-level-security policy to rows older than the retention floor. No login user assumes it yet (the purge/retention jobs that use it are Epic 8); the role and grants exist now so AD-4's grant surface is complete and testable
- [ ] Task 2: Core schema migration (AC: 1, 4)
  - [ ] SQLAlchemy 2.0 models in `server/data/models/` + Alembic revision creating: `employer`, `employee`, `claim`, `app_user`, `user_employer_assignment`, `audit_event`
  - [ ] Naming: snake_case per the Excel mapping (`docs/WC_Feature_Element_Details.xlsx` is canonical — check it before naming any column); surrogate identity int PK `id` on every table; `claim.claim_id` business ID `WC-nnnn` with a UNIQUE constraint
  - [ ] `version int not null default 1` on mutable entities (`claim`, `employee`, `employer`, `app_user`); `audit_event` is append-only — no version
  - [ ] All money columns integer cents (`*_cents` or per Excel naming): `paid_indemnity`, `paid_medical`, `paid_expense`, `reserve`, `aww` — the prototype seed carries dollars; multiply by 100 at seed time
  - [ ] Enums snake_case lowercase: `stage` (`intake|investigation|treatment|settled`), `status` (map the dataset's distinct display strings mechanically — `Initial` → `initial`, `CH Assessment Process` → `ch_assessment_process`, `CH Approved` → `ch_approved`, `Settled & Closed` → `settled_closed`, etc.; Story 3.5 already depends on these exact tokens), plus disability/gender/return-status enums per the Excel. UI owns display labels — never store the display strings
  - [ ] `audit_event` fixed schema exactly: `(id, at, actor_id, actor_role, action, entity, entity_id, before, after)` with `before`/`after` as JSONB (AD-4)
  - [ ] Excluded as columns (AC 4 — each is a registered derivation later, per AD-10): `days_open`, `risk`, `severity` band, `siu_review`, `rtw_blocked`, `payment_due`, `next_payment_date`, `total_paid`, `total_incurred`, employee `initials`, `age_group` (derivable from `age`)
- [ ] Task 3: Seed migration — data extraction (AC: 2)
  - [ ] Extract `ALL_CLAIMS` (JSON array, `docs/Workers_Comp_Prototype.html` line ~632) into seed data — **DATA source only, never a code source**; a one-off extraction script may live in `server/data/seed/` with the resulting normalized seed committed as JSON/CSV the migration reads
  - [ ] Normalize into: 10 `employer` rows (name, short display name, sector), 100 `employee` rows (business id `EMP-*`, name, gender, age, hire_date, role/dept, plant per Excel placement), 100 `claim` rows (scalar fields only — see Dev Notes field-disposition table)
  - [ ] Convert dollars → cents; dates → ISO `date` columns; display strings → snake_case enum values
- [ ] Task 4: Seed migration — personas & scope (AC: 2)
  - [ ] `app_user` rows for the demo personas (see Dev Notes → Personas for the authoritative list): 6 handlers, 3 supervisors, 1 analyst — one row per `name|role` login option (David Bline appears twice: supervisor and analyst; both `scope_all = true`)
  - [ ] `user_employer_assignment` rows enumerating each non-`scope_all` persona's employers exactly as the prototype's `HANDLER_MAP` (line ~937) partitions them; `scope_all` personas get **no** assignment rows — full portfolio comes only from the flag (AD-7)
  - [ ] `claim.handler_id` FK → the assigned handler's `app_user` row (from the seed's `handler` field). Do **not** persist the seed's `supervisor` field — supervisor visibility is employer-scope-based (AD-7), not a claim column
- [ ] Task 5: Verification tests (AC: all)
  - [ ] pytest: `alembic upgrade head` against a fresh DB, then assert row counts (100 claims / 100 employees / 10 employers / 10 `app_user` rows / assignment rows match `HANDLER_MAP`), `WC-nnnn` uniqueness, and that Jennifer Park's assignments are exactly Toyota Motor Manufacturing + General Motors + 3M Company
  - [ ] pytest: as the app role, INSERT on `audit_event` succeeds; UPDATE and DELETE are refused by the DB (assert the grant, not app-layer behavior)
  - [ ] pytest: model metadata sweep asserting none of the banned derived columns exist on `claim`/`employee` (AC 4 as a regression guard)
  - [ ] Downgrade→upgrade round-trip runs clean (CI already runs upgrade against fresh DB per 1.1)
- [ ] Task 6: E2E story spec (AC: 1, 2)
  - [ ] `e2e/stories/1-2-persisted-claim-portfolio-schema-seed.spec.ts` tagged `@story:1-2 @epic:1`; the DB-reset project dependency from 1.1 now exercises real migrations + seed (its seed step stops being a no-op)
  - [ ] `@smoke` happy path: freshly reset stack reaches healthy (`/api/healthz` through nginx) after full migrate+seed; spec asserts seed presence via the e2e DB-owner connection (100 claims, 10 employers, personas) — there is no UI surface for this story, so assertions are stack-level by design

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story turns the browser array into a database: **schema + seed + grants, nothing else**. Do NOT build: auth/session or any login endpoint (1.3), the repository scope-context layer and stats endpoints (1.4 — repositories arrive with their first consumer), SLA aggregation (1.5), `glossary_term` (1.6), `timeline_event`/`document` (2.2), `treatment_plan_step`/`additional_injury` (2.4), `path_required_form` (2.5), `photo` (2.6), `state_rate_schedule` (3.1), `bill`/`expense`/`payment_schedule_week` (3.3), `meeting`/`diary_note`/`email_log`/`email_template` (4.x), embeddings/`knowledge_chunk`/`ai_insight` (6.x). Just-in-time schema is a readiness-report-verified property of this epic breakdown — creating later tables here is a defect, not a favor.

The prototype (`docs/Workers_Comp_Prototype.html`) is a **data source only**: `ALL_CLAIMS` seeds the migration; none of its JS survives. The Excel (`docs/WC_Feature_Element_Details.xlsx`) is the canonical `Table.Column` naming authority — read it before writing the models.

### Architecture compliance (binding ADs for this story)

- **AD-3 one PostgreSQL:** everything lands in the single instance from 1.1; Alembic-only migrations — never `create_all()`. `CREATE EXTENSION vector` here makes the vector store part of the same instance from day one.
- **AD-4 audited command writes:** this story ships the `audit_event` table, its fixed schema, the INSERT-only app-role grant, and the `audit_redactor` exception role. The command functions that *emit* audit events arrive with the first mutations (2.3); the grant surface must be right now.
- **AD-7 scoping model:** `app_user (id, name, role, scope_all)` + `user_employer_assignment (user_id, employer_id)` is the one scope model for every role. `scope_all` resolved from the flag only — never from role.
- **AD-10 one computer per derived value:** enforced negatively here — no derived value is a column (AC 4). The registered computing functions arrive in 1.4+.
- **AD-12 write ownership:** no writers exist yet; the seed migration is the sanctioned initial loader.
- **Conventions:** snake_case DB naming per Excel; surrogate int PKs + `WC-nnnn` business ID; integer cents; ISO dates (`date` for calendar fields, `timestamptz` UTC for `audit_event.at`); snake_case enums with UI-owned labels; `version` CAS column on mutable entities.

### Claim field disposition (from the prototype's scalar field inventory)

Persist on `claim` (names per Excel): `claim_id` (WC-nnnn), `policy_num`, FKs `employer_id`/`employee_id`/`handler_id`, `plant`, `state`, `region` (worksite placement per Excel — may live on employer/plant rows if the Excel says so), `doi`, `froi_date`, `assign_date`, `approval_date`, `rtw_rec`, `actual_rtw`, `injury_type`, `cause`, `body_part`, `body_key`, `icd`, `icd_desc`, `sev_score`, `disability`, `status`, `comm_status`, `return_status`, `stage`, `recovery` (window text), `surgery_required`, `osha_recordable`, `litigation_flag`, `attorney_rep`, `fraud_score`, `fraud_flag`, `paid_indemnity`, `paid_medical`, `paid_expense`, `reserve`, `aww` (cents), `days_recovery`, `settlement_days`, `sla_pick_days`, `sla_approve_days`, `sla_settle_days`, `contraindications`.

Persist on `employee`: business id, name, gender, age, hire_date, role (job title), dept.

**Do not persist** (derived — AD-10/AC 4): `days_open`, `risk`, `severity` (band = pure function of `sev_score`: High ≥ 65, Medium 35–64, Low ≤ 34 — verified against all 100 records), `siuReview`/`rtwBlocked`/`paymentDue`/`nextPaymentDate` (the prototype computes these in a `forEach` after the array — they are never seed columns), `totalPaid` (= sum of the three paid components), `totalIncurred` (= total_paid + reserve), `initials`, `ageGroup`, `supervisor`.

**Deferred to later stories' tables** (present in the seed records — carry them through the extraction untouched, they seed later migrations): `timeline` (2.2), `documents` (2.2/2.5), `photos` (2.6), `treatmentPlan`/`prognosis`/`contraindications` details (2.4), `cpNextActions`/`cpSimilarCase`/`cpReserveNote`/`cpFraudIndicators` (Epic 6 `ai_insight` — pre-authored AI narratives are **never** claim columns, per AD-10).

⚠️ **Documented data quirk — SLA fields seed as data:** the prototype's `sla_pick_days`/`sla_approve_days` values do **not** reconcile with its own date fields (e.g. WC-20017: froi 03-24 → assign 03-25 is 1 day, but `slaPickDays` is 2). The synthetic dataset's SLA figures and its dates are independently generated. Ruling: persist the `sla_*` values as source data columns (they are not user-editable and not among AC 4's banned derivations); date-difference derivations become the real computation when real claims flow (rides the Deferred statutory/data-process decisions). Story 1.5 aggregates over these columns.

### Personas (authoritative, from `HANDLER_MAP` line ~937 + `pickRole` line ~962)

| Persona | Role | Scope |
|---|---|---|
| Kaya Johnson | handler | Caterpillar Inc. · General Electric (GE) · Whirlpool Corporation · John Deere |
| Dante Reyes | handler | Boeing · Honeywell International Inc. |
| Marcus Chen | handler | Toyota Motor Manufacturing · General Motors |
| Sarah Williams | handler | 3M Company |
| Liam O'Sullivan | handler | John Deere |
| Fatima Al-Mansoori | handler | Lockheed Martin |
| David Bline | supervisor | `scope_all = true` (all 100) |
| Jennifer Park | supervisor | Toyota Motor Manufacturing · General Motors · 3M Company |
| Ken Stoker | supervisor | John Deere · Lockheed Martin |
| David Bline | analyst | `scope_all = true` |

⚠️ **Documented discrepancy — "9 personas" vs 10 rows:** the epics AC counts the prototype's 9 named personas (`HANDLER_MAP` keys). But `app_user.role` is single-valued (AD-7 schema) and David Bline logs in as both supervisor **and** analyst, so the seed needs one row per `name|role` login option: **10 `app_user` rows covering the 9 named personas**. Both Bline rows carry `scope_all = true` — matching the spine's Deferred note ("`scope_all = true` for the full-portfolio supervisor and analyst personas", plural). Note the Kaya Johnson/John Deere vs Ken Stoker case: a handler's book legitimately straddles supervisor scopes — the spine calls this normative; do not "fix" it.

### Testing requirements (this story's definition of done)

- pytest suite from Task 5 green (seed counts, scope rows, grants, banned-column sweep, WC-nnnn uniqueness).
- Alembic upgrade (and downgrade round-trip) clean against a fresh DB in CI.
- E2E: `1-2-persisted-claim-portfolio-schema-seed.spec.ts` passes against the freshly reset e2e stack (reset now = drop schema → migrate → **real seed**), `@smoke` tagged; story cannot move to `review`/`done` until it passes (AD-15).

### Project Structure Notes

- Models: `server/data/models/` (one module per aggregate is fine); Alembic revisions in `server/data/` versions dir established in 1.1; normalized seed data + extraction script under `server/data/seed/`.
- Config module (`server/config.py`) gains `alembic_database_url`; compose env templates updated accordingly — still no secrets in VCS.
- The e2e DB-owner role (1.1) is the only role that can drop schema; verify the runtime app role cannot (AD-15 note: "the runtime app role must not be able to — AD-4 grants" becomes true in this story).
- Repositories (`server/data/repositories/`) are **not** built here — they arrive with their first consumers (1.3 auth reads, 1.4 scoped claim reads) so the AD-7 scope-context signature is designed against a real caller.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 1.2]
- AD-3 / AD-4 (audit schema, grants, audit_redactor) / AD-7 (scope model) / AD-10 (derived-value ban): [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- Conventions (naming, IDs, money, enums, version CAS): [Source: ARCHITECTURE-SPINE.md#Consistency Conventions]
- Seed mandate + persona/scope semantics: [Source: ARCHITECTURE-SPINE.md#Deferred (Identity provider); epics.md#Additional Requirements (Data seed)]
- ERD (which entities exist now vs later): [Source: ARCHITECTURE-SPINE.md#Core entity ERD; docs/Architecture-LINEWORKER.md#4.1]
- Canonical column naming: [Source: docs/WC_Feature_Element_Details.xlsx]
- Seed data + HANDLER_MAP + derived-flag forEach: [Source: docs/Workers_Comp_Prototype.html lines ~632, ~937–957]
- Just-in-time schema verification (25/25 entities have creating stories): [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Compliance Checklist Results]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
