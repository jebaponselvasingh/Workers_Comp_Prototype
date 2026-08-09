# Story 4.3: Templated Stakeholder Emails

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want to compose stakeholder emails from claim-aware templates,
So that routine communications take one click and are always logged.

## Acceptance Criteria

1. **Given** the email composer modal, **when** opened, **then** it offers recipient checkboxes (Employee / Employer HR / NCM / Physician / Supervisor / Attorney), subject (required), body, read-only claim reference, and priority (Normal / High / Urgent) (UX-DR10).
2. **And** a missing subject blocks send with inline validation (NFR-3).
3. **Given** the 6 quick templates (3-Point Contact · RTW Offer · NCM Referral · Status Update · IME Request · Settlement Notice) stored in an `email_template` table, **when** one is selected, **then** subject, body, and recipient set pre-fill with merged claim data (FR-DIARY-3).
4. **Given** "Send Email (logged)", **when** clicked, **then** an `email_log` row persists via an audited command (send-as-log per the deferred egress decision), a non-blocking confirmation toast shows (NFR-3, UX-DR11), and the Emails sub-tab lists it reverse-chronologically with subject, recipients, snippet, and sent-badge (FR-DIARY-3, FR-H-10, UX-DR9).
5. **Given** a meeting's "✉ Email participants" button, **when** clicked, **then** the composer opens pre-filled with that meeting's participants and claim context (FR-DIARY-2 convert-to-email), and the button enables from this story on.

## Tasks / Subtasks

- [ ] Task 1: `email_template` + `email_log` migrations & template seed (AC: 3, 4)
  - [ ] Alembic migration creating `email_template`: surrogate `id` int PK, `template_key` unique snake_case (`three_point_contact | rtw_offer | ncm_referral | status_update | ime_request | settlement_notice`), `label` (button text), `subject_template`, `body_template`, `default_recipients` (array of the snake_case recipient enum values)
  - [ ] Seed the 6 templates verbatim from the prototype's `loadEmailTemplate` map — subject/body text with merge placeholders (e.g. `{{claim_id}}`, `{{worker_name}}`, `{{doi}}`, `{{injury_type}}`, `{{body_part}}`, `{{icd}}`, `{{cause}}`, `{{days_open}}`, `{{stage}}`, `{{status}}`, `{{handler_name}}`) replacing the JS interpolations, and each template's default recipient set (3-Point → employee/employer_hr/physician; RTW Offer → employee/employer_hr/ncm; NCM Referral → ncm/physician/employer_hr; Status Update → employer_hr/supervisor; IME Request → physician/ncm; Settlement → employee/attorney)
  - [ ] Alembic migration creating `email_log`: surrogate `id` int PK, `app_user_id` FK NOT NULL (sender — ERD: `APP_USER ||--o{ EMAIL_LOG : sends`), `claim_id` FK nullable (ERD: `CLAIM |o--o{ EMAIL_LOG : about`), `template_id` FK nullable (ERD: `EMAIL_TEMPLATE ||--o{ EMAIL_LOG : seeds` — null for free-composed), `subject NOT NULL`, `body`, `priority` snake_case enum (`normal | high | urgent`), `recipients` (array of recipient enum values), `sent_at timestamptz NOT NULL`. Append-only — no `version` column, no update/delete path.
- [ ] Task 2: `services/claims` template merge + send commands (AC: 2, 3, 4)
  - [ ] `get_merged_template` query: loads the template row, merges claim fields server-side (AD-1 — pre-filled claim data on screen is service-computed) with `days_open` from its registered derivation (AD-10) and stage/status rendered as display labels, returns `{subject, body, recipients}`; claim resolved under the caller's scope context (AD-7)
  - [ ] `send_email` command (send-as-log): validates non-empty subject (422 problem+json — server half of AC 2) and ≥ 1 recipient, claim reference inside caller scope, inserts `email_log` + audit event `(action: create, entity: email_log)` with after-diff in the same transaction (AD-4). **No SMTP, no egress of any kind** — the deferred-egress decision is binding
  - [ ] `list_email_logs` repository query: caller-context scoped (a user sees their own sent log), ordered `sent_at DESC`, cursor-paginated `{items, nextCursor}`
- [ ] Task 3: API routes (AC: 1–4)
  - [ ] `GET /api/claims-diary/email-templates` (the 6, for the button row), `GET …/email-templates/{key}/merged?claimId=…` (merged pre-fill), `POST …/emails` (send-as-log), `GET …/emails` (cursor list) — thin routers, camelCase JSON, problem+json errors, auth dependency builds scope context
  - [ ] Regenerate the OpenAPI client; add `emailTemplates` / `emailLogs` keys to the shared `queryKeys` module (AD-9)
- [ ] Task 4: Email composer modal (AC: 1, 2, 3)
  - [ ] shadcn Dialog (wide variant) per UX-DR10 mirroring the prototype: "Select recipients" checkbox grid (Employee pre-checked for a blank compose), "Quick templates" row of 6 template buttons, subject input (required), body textarea (min-height ~180px), read-only claim-reference field (steel-blue treatment, `WC-nnnn — Worker Name`), priority select (Normal / High / Urgent), Cancel + "✉ Send Email (logged)" actions
  - [ ] Template button click → `get_merged_template` for the active claim → fill subject/body and set exactly the template's recipient checkboxes (server-supplied — the SPA does no merging)
  - [ ] Missing subject → inline field-level validation, never a native `alert()` (NFR-3, UX-DR11); server 422 maps to the same inline message
- [ ] Task 5: Send flow + Emails sub-tab (AC: 4)
  - [ ] Send → mutation → on success: close modal, non-blocking toast ("✓ Email logged to: …" — replaces the prototype's `alert()`), invalidate email keys, switch the Diary pane to the Emails sub-tab (prototype behavior)
  - [ ] Emails sub-tab (replaces 4.1's placeholder): reverse-chronological cards — ✉ subject + "Sent {date/time}" badge, "To: {recipients}" (+ ` · claimName` when claim-linked), ~100-char body snippet (UX-DR9); "No emails sent yet…" empty state (NFR-3); priority surfaced on the card (High/Urgent get warn/error accent)
- [ ] Task 6: Enable "✉ Email participants" convert-to-email (AC: 5)
  - [ ] Flip Story 4.1's disabled button live: clicking opens the composer pre-filled with a meeting-confirmation subject (`Meeting Confirmation: {type} — {WC-nnnn}`) and body (the prototype's `quickEmailMeeting` letter: type, date/time, location, claim, agenda/notes, confirm-attendance ask), recipients pre-checked to the meeting's participants; pre-fill content is server-merged like templates (same merge service, meeting-sourced fields)
  - [ ] Remove the 4.1 tooltip/disabled state; the button works from both the Meetings sub-tab and 4.2's today's-meetings summary if rendered there
- [ ] Task 7: Tests + E2E gate (AC: all)
  - [ ] pytest: seed assertion (6 templates, keys + default recipients), merge correctness per template (placeholders resolved from a known seeded claim; `days_open` from the derivation; no unresolved `{{…}}` left), subject/recipient validation 422s, scope rejection, audit event in-transaction, list ordering + pagination; grep-level assertion that no SMTP/mail library import exists in `services/claims`
  - [ ] Vitest: inline subject validation, template pre-fill rendering, recipient checkbox set/reset
  - [ ] `e2e/stories/4-3-templated-stakeholder-emails.spec.ts` tagged `@story:4-3 @epic:4`: login as handler → open composer → select 3-Point Contact template → merged subject/body/recipients appear → send → toast + Emails sub-tab lists it with sent-badge (happy path, tagged `@smoke`) → missing-subject inline validation → meeting card "✉ Email participants" opens pre-filled composer → log survives reload

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story completes the diary aggregate: the email composer, the 6 seeded claim-aware templates, the persisted send-log, the Emails sub-tab, and the 4.1 convert-to-email seam. **"Send" is a persisted log and nothing more** — real SMTP/calendar egress is a deferred epic by architecture decision with its own compliance review; do NOT add a mail client, an outbox, a delivery status, or any network egress (the "Sent" badge reflects the log row, honestly labeled "Send Email (logged)" in the UI). Do NOT build: template administration/editing (templates are seed data), email threading or replies, per-recipient addresses (recipients are role tags, matching the prototype), or anything under the Actions tab (Epic 6). The prototype's `emailsStore` global and its `alert()` confirmation are behavior references only — persistence and toasts replace them.

### Architecture compliance (binding ADs for this story)

- **AD-1:** template merging happens server-side — the pre-filled subject/body carrying claim data (DOI, ICD-10, days open, stage) is service-computed; the SPA only renders what `get_merged_template` returns and captures edits.
- **AD-2:** templates contain no computed financial figures (the Settlement template's `$[AMOUNT]` is deliberately a handler-filled placeholder — keep it; do not auto-fill from financials in this story).
- **AD-4:** `send_email` is an audited command — `email_log` insert + audit event in one transaction. `email_log` is append-only (no version, no CAS; write-concurrency convention exemption); note the email body is PHI-class content in the DB like every claim-derived store (AD-11 — never in operational logs).
- **AD-7:** merge and send resolve the claim under the caller context; the sent-log list is the caller's own; no endpoint accepts caller-supplied scope.
- **AD-10:** `days_open` in the Status Update / NCM Referral merges comes from the single registered derivation — never recomputed in the merge code or the SPA.
- **AD-12:** `email_template` and `email_log` join the diary aggregate owned by `services/claims`; convert-to-email reads the meeting via 4.1's query — no writes to `meeting` from this story, and no `timeline_event` emission.
- **AD-15:** story spec `e2e/stories/4-3-templated-stakeholder-emails.spec.ts`, tags `@story:4-3 @epic:4`, one `@smoke`; story cannot reach `review`/`done` until it passes.
- **Conventions:** snake_case enums (`priority`, recipient values, `template_key`) with UI-owned labels; cursor-paginated lists; problem+json → inline/toast; `sent_at` UTC `timestamptz` formatted in the UI.

### Data notes

- **Creates:** `email_template` (reference data, seeded 6 rows) and `email_log` (append-only) — both write-owned by `services/claims`. This completes all four diary-aggregate tables (`meeting` 4.1, `diary_note` 4.2, both email tables here) — every Epic 4 ERD entity now has its creating story.
- **Uses:** `claim` + `employee`/`employer` fields and `services/derivations` for merge values; `meeting` (read-only) for convert-to-email; `app_user`; `audit_event`.
- **Merge placeholders:** convert the prototype's JS template literals to a stable placeholder syntax (`{{snake_case}}`) resolved server-side; unresolved bracketed prompts meant for the handler (`[Please add current activity summary]`, `[Transitional Duty — to be completed by supervisor]`, `$[AMOUNT]`, `[RATING]%`, `[IME Physician / IME Coordinator]`) are template text, not merge fields — leave them literal.
- **Recipient enum:** one shared 6-value set with 4.1's participant enum (`employee | employer_hr | ncm | treating_physician | supervisor | attorney`) — reuse the same Python/DB enum so convert-to-email maps 1:1. Minor label discrepancy in the prototype: the meeting modal says "My Supervisor"/"Nurse Case Manager", the email modal "Supervisor"/"Nurse Case Manager" — labels are UI-owned; keep each modal's prototype wording.
- The Excel's `Table.Column` mapping is canonical for DB naming — check `docs/WC_Feature_Element_Details.xlsx` for email rows before inventing column names.

### UX notes

- **UX-DR10 (composer):** wide modal, recipients-first layout, 6 template buttons in a row, required subject, ~180px body, steel-blue read-only claim reference, priority select — mirror the prototype markup order exactly.
- **UX-DR9 (Emails sub-tab):** reverse-chronological email cards with sent-badge, recipients line, faint snippet.
- **UX-DR11 / NFR-3:** the prototype's `alert('Please enter a subject.')` and the post-send `alert('✓ Email logged…')` must NOT survive — inline validation and a success toast respectively; keep the prototype's auto-switch to the Emails sub-tab after send so the logged mail is immediately visible.
- **Design tokens:** the prototype palette is **light** and canonical (Story 1.1 ruling); epics.md's "dark console aesthetic" is a documented discrepancy. Sent-badge uses the ok pair; High/Urgent priority the warn/error pairs.

### Testing requirements (this story's definition of done)

- pytest: template seed shape, per-template merge correctness (including derivation-sourced `days_open` and no leftover `{{…}}`), validation 422s (subject, empty recipients), scope rejection, same-transaction audit, DESC ordering + pagination, no-egress assertion.
- Vitest: inline validation, template pre-fill, recipient set/reset per template.
- Playwright `4-3-templated-stakeholder-emails.spec.ts` (`@story:4-3 @epic:4`, one `@smoke`): template → merged compose → send → toast + Emails list round-trip with reload persistence; meeting convert-to-email pre-fill; inline subject validation. Selector policy: role first, `data-testid` second, never CSS classes.
- ruff + mypy + eslint + tsc clean; Alembic upgrade clean on a fresh DB.

### Project Structure Notes

- Server: `server/services/claims/` diary module (e.g. `emails.py` beside `meetings.py`/`notes.py`), templates seeded from the migration (keep template text in the migration or a data file under `server/data/` — not hardcoded in service code), router in `server/api/`.
- Web: `web/src/features/diary/` — composer modal + Emails sub-tab beside the 4.1/4.2 components; the modal must be openable from three places: the Emails sub-tab compose button, a meeting card's "✉ Email participants" (4.1 component — remove its disabled state here), and 4.2's today's-meetings summary if it renders the button.
- This story closes Epic 4: after it, FR-DIARY-1..3 and FR-H-10 are fully shipped; the only remaining disabled affordance in the pane is the ⚡ Actions tab (Epic 6).

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 4.3]
- Epic 4 scope note (sends are logs): [Source: _bmad-output/planning-artifacts/epics.md#Epic 4]
- Deferred email egress + compliance framing: [Source: ARCHITECTURE-SPINE.md#Deferred — "Email/calendar egress"; docs/Architecture-LINEWORKER.md#7 Security table "Data egress"]
- Diary-aggregate ownership: [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map — "Diary, meetings, emails, templates"]
- AD-1 / AD-4 / AD-7 / AD-10 / AD-12 / AD-15: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules / #Consistency Conventions]
- ERD (`EMAIL_TEMPLATE ||--o{ EMAIL_LOG : seeds`, optional claim links): [Source: ARCHITECTURE-SPINE.md#Core entity ERD]
- Composer modal markup: [Source: docs/Workers_Comp_Prototype.html lines 581–626]
- Template texts + default recipients (`loadEmailTemplate`), `sendEmail`, `renderEmails`, `quickEmailMeeting`: [Source: docs/Workers_Comp_Prototype.html lines 1927–2045]
- 4.1 disabled email-button seam: [Source: _bmad-output/implementation-artifacts/4-1-meeting-scheduling-management.md#Tasks — Task 6]
- UX-DR9/10/11: [Source: _bmad-output/planning-artifacts/epics.md#UX Design Requirements]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
