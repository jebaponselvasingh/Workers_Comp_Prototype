---
baseline_commit: 78dc3454757be90bf159abfe4eb9022c2b6838a9
---

# Story 1.2: Persisted Claim Portfolio (Schema & Seed)

Status: done

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

- [x] Task 1: DB roles & extension groundwork (AC: 1, 3)
  - [x] Migration enabling `CREATE EXTENSION IF NOT EXISTS vector` (pgvector ≥ 0.8.2 — the image from Story 1.1 bundles it; this story activates it so AD-3's single-store commitment is real from the first schema)
  - [x] Introduce the two-role model: the **migration/owner role** (runs Alembic; used by the e2e reset fixture) and the **runtime app role** `lineworker_app` (what the api container connects as; no DDL rights). Config module gains `alembic_database_url` alongside `database_url`; the api entrypoint from 1.1 runs `alembic upgrade head` under the owner URL, then uvicorn connects as the app role
  - [x] Grants migration: app role gets CRUD on domain tables, but **INSERT-only** on `audit_event` (no UPDATE/DELETE/TRUNCATE)
  - [x] Create the `audit_redactor` DB role per the AD-4 registered exception: three grants on `audit_event` — SELECT, UPDATE on the `before`/`after` columns, and DELETE constrained by a row-level-security policy to rows older than the retention floor. SELECT (grant **and** RLS policy) is not extra reach, it is what makes the other two usable: Postgres requires SELECT on every column named in a `WHERE` clause and applies SELECT policies to the rows an UPDATE/DELETE reads, so without it a *targeted* redaction is impossible — the statement either errors or silently matches zero rows, leaving "rewrite every row" as the only executable form. No login user assumes it yet (the purge/retention jobs that use it are Epic 8); the role and grants exist now so AD-4's grant surface is complete and testable
- [x] Task 2: Core schema migration (AC: 1, 4)
  - [x] SQLAlchemy 2.0 models in `server/data/models/` + Alembic revision creating: `employer`, `employee`, `claim`, `app_user`, `user_employer_assignment`, `audit_event`
  - [x] Naming: snake_case per the Excel mapping (`docs/WC_Feature_Element_Details.xlsx` is canonical — check it before naming any column); surrogate identity int PK `id` on every table; `claim.claim_id` business ID `WC-nnnn` with a UNIQUE constraint
  - [x] `version int not null default 1` on mutable entities (`claim`, `employee`, `employer`, `app_user`); `audit_event` is append-only — no version
  - [x] All money columns integer cents (`*_cents` or per Excel naming): `paid_indemnity`, `paid_medical`, `paid_expense`, `reserve`, `aww` — the prototype seed carries dollars; multiply by 100 at seed time
  - [x] Enums snake_case lowercase: `stage` (`intake|investigation|treatment|settled`), `status` (map the dataset's distinct display strings mechanically — `Initial` → `initial`, `CH Assessment Process` → `ch_assessment_process`, `CH Approved` → `ch_approved`, `Settled & Closed` → `settled_closed`, etc.; Story 3.5 already depends on these exact tokens), plus disability/gender/return-status enums per the Excel. UI owns display labels — never store the display strings
  - [x] `audit_event` fixed schema exactly: `(id, at, actor_id, actor_role, action, entity, entity_id, before, after)` with `before`/`after` as JSONB (AD-4)
  - [x] Excluded as columns (AC 4 — each is a registered derivation later, per AD-10): `days_open`, `risk`, `severity` band, `siu_review`, `rtw_blocked`, `payment_due`, `next_payment_date`, `total_paid`, `total_incurred`, employee `initials`, `age_group` (derivable from `age`)
- [x] Task 3: Seed migration — data extraction (AC: 2)
  - [x] Extract `ALL_CLAIMS` (JSON array, `docs/Workers_Comp_Prototype.html` line ~632) into seed data — **DATA source only, never a code source**; a one-off extraction script may live in `server/data/seed/` with the resulting normalized seed committed as JSON/CSV the migration reads
  - [x] Normalize into: 10 `employer` rows (name, short display name, sector), 100 `employee` rows (business id `EMP-*`, name, gender, age, hire_date, role/dept, plant per Excel placement), 100 `claim` rows (scalar fields only — see Dev Notes field-disposition table)
  - [x] Convert dollars → cents; dates → ISO `date` columns; display strings → snake_case enum values
- [x] Task 4: Seed migration — personas & scope (AC: 2)
  - [x] `app_user` rows for the demo personas (see Dev Notes → Personas for the authoritative list): 6 handlers, 3 supervisors, 1 analyst — one row per `name|role` login option (David Bline appears twice: supervisor and analyst; both `scope_all = true`)
  - [x] `user_employer_assignment` rows enumerating each non-`scope_all` persona's employers exactly as the prototype's `HANDLER_MAP` (line ~937) partitions them; `scope_all` personas get **no** assignment rows — full portfolio comes only from the flag (AD-7)
  - [x] `claim.handler_id` FK → the assigned handler's `app_user` row (from the seed's `handler` field). Do **not** persist the seed's `supervisor` field — supervisor visibility is employer-scope-based (AD-7), not a claim column
- [x] Task 5: Verification tests (AC: all)
  - [x] pytest: `alembic upgrade head` against a fresh DB, then assert row counts (100 claims / 100 employees / 10 employers / 10 `app_user` rows / assignment rows match `HANDLER_MAP`), `WC-nnnn` uniqueness, and that Jennifer Park's assignments are exactly Toyota Motor Manufacturing + General Motors + 3M Company
  - [x] pytest: as the app role, INSERT on `audit_event` succeeds; UPDATE and DELETE are refused by the DB (assert the grant, not app-layer behavior)
  - [x] pytest: model metadata sweep asserting none of the banned derived columns exist on `claim`/`employee` (AC 4 as a regression guard)
  - [x] Downgrade→upgrade round-trip runs clean (CI already runs upgrade against fresh DB per 1.1)
- [x] Task 6: E2E story spec (AC: 1, 2)
  - [x] `e2e/stories/1-2-persisted-claim-portfolio-schema-seed.spec.ts` tagged `@story:1-2 @epic:1`; the DB-reset project dependency from 1.1 now exercises real migrations + seed (its seed step stops being a no-op)
  - [x] `@smoke` happy path: freshly reset stack reaches healthy (`/api/healthz` through nginx) after full migrate+seed; spec asserts seed presence via the e2e DB-owner connection (100 claims, 10 employers, personas) — there is no UI surface for this story, so assertions are stack-level by design

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

## Senior Developer Review (AI)

**Date:** 2026-08-10 · **Reviewer:** /code-review (multi-angle adversarial, fresh-context fork) · **Outcome:** Changes Requested → all resolved

Scope note: the committed range against `origin/development` was empty, so the review took the whole untracked `lineworker/` tree plus `.github/workflows/ci.yaml` — i.e. Story 1.1's surface as well as 1.2's. Every gate was executed, not just read (ruff/mypy/pytest, pytest against live pgvector:pg18, web lint/typecheck/vitest, the full `compose.e2e` stack + Playwright). Nothing invalidated a demonstrated AC; all 12 findings were latent defects the green suite did not cover.

### Action Items

- [x] [High] Rotating `APP_DB_PASSWORD` bricks the dev stack permanently — role created `IF NOT EXISTS`, so the password is written exactly once and `alembic upgrade head` is a no-op thereafter (`server/data/versions/20260810_0002_vector_and_roles.py:36`)
- [x] [Med] `audit_redactor` can only redact *every* row, never a targeted one — no SELECT grant, and Postgres requires SELECT on every column named in a `WHERE` (`server/data/versions/20260810_0003_core_schema.py:218`)
- [x] [Med] `--color-muted` repeats the exact `accent` collision the file's own header documents — shadcn spends `muted` on a background (`web/src/index.css:26`)
- [x] [Med] Health check discards the exception, hiding the failures it exists to report (`server/api/app.py:22`)
- [x] [Low] `config.py` URL rewrites are silent no-ops on any non-`postgresql://` scheme (`server/config.py:39`) *(also on 1.1's verified-but-cut list)*
- [x] [Low] Stock `dark:` variants ship in a deliberately light-only app (`web/src/components/ui/button.tsx:16`, `select.tsx:38`, `input.tsx:11`, `badge.tsx:8`)
- [x] [Low] AD-15 reset guard is module-global state that silently depends on `workers: 1` (`e2e/fixtures/test.ts:14`)
- [x] [Low] `e2e/tsconfig.json` never exercised — Playwright transpiles with esbuild and does not type-check (`.github/workflows/ci.yaml:92`) *(also on 1.1's verified-but-cut list)*
- [x] [Low] `employee_rows` built one-per-claim with no dedup against a UNIQUE business id (`server/data/seed/extract_prototype_seed.py:127`)
- [x] [Low] Date-normalization docstring states a false provenance — the prototype's JS `Date` does not roll `2026-02-29` over, it yields `Invalid Date` (`server/data/seed/extract_prototype_seed.py:97`)
- [x] [Low] No `file_template`, so the migration naming convention is unenforced for the next `alembic revision` (`server/alembic.ini:2`)
- [x] [Low] `location /api/` does not match a bare `/api`, which falls through to the SPA and answers 200 with `index.html` (`deploy/nginx/default.conf:10`)

### Two fixes the finding's own remedy would not have completed

- **Password rotation:** an unconditional `ALTER ROLE` inside migration 0002 does *not* fix it — the migration never runs again once the DB is at head. The role DDL moved to `server/data/roles.py`, which 0002 calls **and** the entrypoint re-runs on every boot.
- **`audit_redactor`:** the `GRANT SELECT` alone converts the permission error into a *silent* zero-row match, because Postgres applies SELECT **policies** to the rows an UPDATE/DELETE reads through its `WHERE`. A matching `audit_redactor_select` RLS policy (scoped to the update policy) was required as well.

## Dev Agent Record

### Agent Model Used

claude-fable-5 (Claude Fable 5)

### Debug Log References

- **Data quirk (documented):** the synthetic dataset contains the impossible date `2026-02-29` (WC-20969 froi_date; 2026 is not a leap year). The prototype simply rendered it as an invalid date (`new Date("2026-02-29")` is `Invalid Date` in JS — it does not roll over); the extraction script normalizes invalid calendar dates forward instead (`2026-02-29` → `2026-03-01`, logged at extraction) so the column can be a real `DATE`. Exactly one occurrence across all date fields.
- **Naming:** Excel canonical name is `severity_score` (story task text said `sev_score` — Excel wins per Dev Notes). `claim.employee_id` is the surrogate FK while `employee.employee_id` is the business string — both names are Excel-canonical; noted for 1.3+ readers.
- **Downgrade fixes found by the round-trip test:** (1) `DROP ROLE` blocked by surviving schema-privilege dependencies → `DROP OWNED BY` first in 0002 downgrade; (2) audit rows referencing seeded users block the seed downgrade → 0004 downgrade clears `audit_event` first (owner-level teardown; the append-only ban binds the app role).
- **pgvector is an untrusted extension** ("must be superuser to create"): after the e2e reset drops schema public (cascading the extension away), the re-migrate as `lineworker_e2e_owner` could not recreate it. Resolution: the e2e-profile-only owner role is now SUPERUSER (harness plumbing; init SQL documents why). The AD-4 guarantee — the runtime app role has no DDL — is enforced by the grants migration and asserted by pytest (`test_app_role_has_no_ddl`).
- Unique-constraint test needed `INSERT … OVERRIDING USER VALUE` so the duplicate row takes a fresh identity id and trips `uq_claim_claim_id` instead of the primary key.

### Completion Notes List

- **AC 1 (schema):** migrations 0002–0003 create `employer`, `employee`, `claim`, `app_user`, `user_employer_assignment`, `audit_event` with Excel-canonical snake_case naming, identity int PKs, `uq_claim_claim_id` on the WC-nnnn business id, `version` on all mutable entities, money as integer cents (BigInteger; Excel column names kept), and 7 native snake_case enums (status tokens exactly as Story 3.5 expects: `initial`, `ch_assessment_process`, `ch_approved`, `denied`, `settled`, `settled_closed`). 0003 was Alembic-autogenerated from the SQLAlchemy models, then hand-extended with grants + RLS.
- **AC 2 (seed):** migration 0004 loads `data/seed/seed_data.json` (committed; produced by `data/seed/extract_prototype_seed.py` from the prototype's `ALL_CLAIMS`): 10 employers (name/short_name/sector), 100 employees, **10 `app_user` rows covering the 9 personas** (David Bline seeded as both supervisor and analyst, both `scope_all=true`, per the documented discrepancy note), 16 `user_employer_assignment` rows exactly matching `HANDLER_MAP`, 100 claims with resolved FKs (`handler_id` from the seed's handler field; the `supervisor` field deliberately not persisted). Nested collections + `cp_*` narratives carried through under `"deferred"` for later stories' seed migrations.
- **AC 3 (grants):** two-role model live — entrypoint migrates via `ALEMBIC_DATABASE_URL` (owner), uvicorn connects as `lineworker_app` (created by 0002, password from `APP_DB_PASSWORD`). App role: CRUD on domain tables, INSERT+SELECT only on `audit_event` — UPDATE/DELETE refused by the DB (pytest-asserted), no DDL (asserted). `audit_redactor` (NOLOGIN) holds three grants: SELECT, UPDATE(before, after) and DELETE, the last RLS-constrained to rows older than the 7-year retention floor (`audit_retention_years` config); SELECT is required for the other two to be usable on a qualified statement at all, and pytest asserts a targeted redaction reaches one row while the retention floor still blocks deleting a recent one.
- **Role password rotation:** the migration that creates `lineworker_app` runs once, so it cannot be the only place the password is written — a rotated `APP_DB_PASSWORD` against the persisted `pgdata` volume would leave the role on its old password and the api permanently unable to authenticate. `data/roles.py` owns the role DDL and the entrypoint re-runs it (`python -m data.roles`) on every boot, unconditionally; migration 0002 calls the same helpers. Verified end to end on the dev stack: after rotating, the api is healthy and the old password is rejected over the network.
- **AC 4 (no derived columns):** `days_open`, `risk`, severity band, `siu_review`, `rtw_blocked`, `payment_due`, `next_payment_date`, `total_paid`, `total_incurred`, `initials`, `age_group`, `supervisor` are absent; `tests/test_no_derived_columns.py` is the standing regression guard. SLA `sla_*` values persist as source data per the story's documented data-quirk ruling.
- **Tests:** 22 server tests green (14 at first submission + 8 from the review round) — seed counts (100/100/10/10/16), Jennifer Park's book exactly Toyota+GM+3M, scope_all personas have zero assignment rows, WC-nnnn uniqueness, audit grants (INSERT ok / UPDATE+DELETE denied), no-DDL, banned-column sweep, downgrade→upgrade round trip, **plus** targeted redaction under `audit_redactor` (one row redacted; retention floor still blocks deleting a recent row), app-password rotation reaching the role, 5 `Settings` URL-normalization cases, and the healthz error-class log assertion. CI migrations job now also runs the DB-backed suite against its fresh pgvector service container.
- **Review round (2026-08-10) — all 12 findings resolved, every gate re-run green:**
  - ✅ [High] Role DDL extracted to `server/data/roles.py` (create-if-absent + unconditional `ALTER ROLE`); migration 0002 calls it and the entrypoint re-runs `python -m data.roles` on every boot. Verified live on the dev stack with the persisted `pgdata` volume: after rotating `APP_DB_PASSWORD`, api healthy and the old password rejected **over the network** (an in-container `psql` to `localhost` accepts anything — the postgres image trusts 127.0.0.1, so that check must not run locally).
  - ✅ [Med] `GRANT SELECT ON audit_event TO audit_redactor` **plus** an `audit_redactor_select` RLS policy scoped to the update policy; pytest asserts targeted UPDATE and DELETE reach exactly one row while a recent row stays undeletable.
  - ✅ [Med] `--color-muted` (a text color) renamed to `--color-muted-text`; `--color-muted: var(--muted)` added to `@theme inline`. Verified in built CSS: `.bg-muted{background-color:var(--muted)}` (the light surface) and `.text-muted-text{color:var(--color-muted-text)}`. `App.tsx` updated — it was the live consumer.
  - ✅ [Med] `check_db` logs `error=type(exc).__name__`; unit test asserts the field is present and carries no credentials (AD-11 unaffected — a class name is not PHI).
  - ✅ [Low] `config.py` parses with `make_url(...).set(drivername=...)` and rejects non-postgres backends outright; `postgres://` and pre-qualified `postgresql+asyncpg://` both normalize correctly.
  - ✅ [Low] All `dark:` utilities stripped from the four vendored shadcn components (button, badge, input, select) — the light-only commitment is now documented in `index.css`'s header as a standing rule for future `shadcn add`.
  - ✅ [Low] AD-15 reset fixture asserts `testInfo.config.workers === 1` instead of trusting it; verified it fires under `--workers=2`.
  - ✅ [Low] `e2e` gains a `typecheck` script, run in CI **before** the 15-minute stack build so a type error fails in seconds.
  - ✅ [Low] Seed extraction dedupes employees by business id (first claim wins); re-extraction produces byte-identical `seed_data.json`.
  - ✅ [Low] Date-quirk docstring and this story's Debug Log corrected — the prototype yields `Invalid Date`, it does not roll over; the forward roll is our deterministic house rule.
  - ✅ [Low] `alembic.ini` gains `file_template`; verified a fresh `alembic revision` emits `20260810_<slug>.py`.
  - ✅ [Low] nginx `location = /api` returns 308 to `/api/`, with `absolute_redirect off` so the published port survives the redirect (nginx listens on 80 internally and was rebuilding the Location without it).
- **AD-15 gate:** `1-2-persisted-claim-portfolio-schema-seed.spec.ts` (@story:1-2 @epic:1, one @smoke) passes against the freshly reset e2e stack — full suite 6/6 including 1.1's spec with the per-file auto-reset firing between spec files; the reset's migrate step now IS the seed step (0004 runs inside `alembic upgrade head` as the e2e owner). Dev stack verified live: healthy end-to-end with the app role, 100 claims, vector extension active.
- **For the repo admin (carried from 1.1):** confirm first CI run; the four jobs remain the required checks.

### File List

New files:

- `lineworker/server/data/models/base.py`, `lineworker/server/data/models/enums.py`, `lineworker/server/data/models/core.py` (SQLAlchemy 2.0 models)
- `lineworker/server/data/versions/20260810_0002_vector_and_roles.py`
- `lineworker/server/data/versions/20260810_0003_core_schema.py`
- `lineworker/server/data/versions/20260810_0004_seed_portfolio.py`
- `lineworker/server/data/seed/__init__.py`, `lineworker/server/data/seed/extract_prototype_seed.py`, `lineworker/server/data/seed/seed_data.json`
- `lineworker/server/tests/test_schema_seed.py`, `lineworker/server/tests/test_no_derived_columns.py`
- `lineworker/e2e/stories/1-2-persisted-claim-portfolio-schema-seed.spec.ts`
- `lineworker/server/data/roles.py` (review round — role DDL + boot-time password reconcile)
- `lineworker/server/tests/test_config.py` (review round — URL normalization)

Modified files:

- `lineworker/server/data/models/__init__.py` (exports)
- `lineworker/server/config.py` (`alembic_database_url`, `app_db_password`, `audit_retention_years`, `sync_alembic_database_url`; review round: `make_url`-based driver rewrite)
- `lineworker/server/data/env.py` (owner URL; `target_metadata = Base.metadata`)
- `lineworker/deploy/compose.yaml`, `lineworker/deploy/compose.e2e.yaml` (two-role env wiring)
- `lineworker/deploy/.env.example` (`APP_DB_PASSWORD`; review round: rotation semantics documented)
- `lineworker/deploy/e2e-init/01-e2e-owner.sql` (superuser harness role; rationale documented)
- `lineworker/e2e/fixtures/reset.ts` (migrate as e2e owner via `ALEMBIC_DATABASE_URL`; `psqlQuery` helper; recycle targets `lineworker_app`)
- `.github/workflows/ci.yaml` (DB-backed pytest in migrations job; review round: e2e `typecheck` step)
- `_bmad-output/implementation-artifacts/1-2-persisted-claim-portfolio-schema-seed.md`, `_bmad-output/implementation-artifacts/sprint-status.yaml`

Modified in the review round (Story 1.1 surface — see that story's Change Log):

- `lineworker/server/alembic.ini` (`file_template`), `lineworker/server/entrypoint.sh` (role sync step), `lineworker/server/api/app.py` (health-check error class), `lineworker/server/tests/test_healthz.py`
- `lineworker/deploy/nginx/default.conf` (bare `/api` → 308, `absolute_redirect off`)
- `lineworker/web/src/index.css`, `lineworker/web/src/App.tsx`, `lineworker/web/src/components/ui/{button,badge,input,select}.tsx`
- `lineworker/e2e/fixtures/test.ts` (workers guard), `lineworker/e2e/package.json` (`typecheck`)
- `_bmad-output/implementation-artifacts/8-1-phi-purge-cascade-retention.md`, `_bmad-output/implementation-artifacts/8-2-database-audit-log-hardening.md` (redactor grant surface corrected to three grants — those stories specify it)

### Change Log

- 2026-08-10: Story 1.2 implemented — schema (6 tables, 7 enums), two-role model + AD-4 grant surface + audit RLS, seed extraction + migration (100 claims / 100 employees / 10 employers / 10 users / 16 assignments), 14 verification tests, e2e story spec. Dev + e2e stacks verified live; full AD-15 suite 6/6. Status → review.
- 2026-08-10: Addressed code review findings — 12 items resolved (1 High, 3 Med, 8 Low), two of them needing a broader fix than the finding proposed (boot-time role reconcile; RLS SELECT policy alongside the grant). Full gate re-run green: ruff + mypy clean, pytest 22 against live pgvector:pg18 (12 passed / 9 skipped without a DB), web lint + typecheck + vitest 2, new e2e typecheck clean, Playwright 6/6 against the full compose stack. Password rotation, the `/api` redirect, the `workers` guard, the alembic template, and byte-identical seed re-extraction each verified live. Status → done.
