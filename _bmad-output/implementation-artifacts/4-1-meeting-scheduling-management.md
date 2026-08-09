# Story 4.1: Meeting Scheduling & Management

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want to schedule and manage claim-linked meetings,
So that stakeholder touchpoints are planned and tracked.

## Acceptance Criteria

1. **Given** the Meetings sub-tab, **when** "＋ Schedule Meeting" opens the modal, **then** it offers the 10 meeting types (3-Point Contact — Initial, RTW Conference, NCM Care Coordination, IME Preparation, Settlement Discussion, Physician Consultation, Employer Accommodation Review, Litigation Prep, Claim Review — Supervisor, Other), read-only linked claim, date (required), time, participant checkboxes (Employee / Employer HR / NCM / Treating Physician / My Supervisor / Attorney), notes/agenda, and location/link (UX-DR10).
2. **And** a missing date blocks save with inline validation, not a native dialog (NFR-3).
3. **Given** a saved meeting, **when** it persists, **then** a `meeting` row (table created here) is written via an audited command (AD-4), and the list renders sorted by date with Upcoming/Done styling, showing type, date/time, location, linked claim, notes, and participant tags (FR-DIARY-2, UX-DR9).
4. **Given** a meeting card, **when** "✓ Done" or "Delete" is clicked, **then** the status change or removal persists with audit and the list re-renders (FR-DIARY-2).
5. **And** the "✉ Email participants" button renders disabled with a tooltip until the composer ships in Story 4.3.
6. **Given** the seed migration for this story, **when** it runs, **then** each handler persona gets the two demo meetings, completing FR-LOGIN-3's seeded-meetings clause.

## Tasks / Subtasks

- [ ] Task 1: `meeting` table migration + demo seed (AC: 3, 6)
  - [ ] Alembic migration creating `meeting`: surrogate `id` int PK, `app_user_id` FK (owner — the handler who holds the meeting), `claim_id` FK nullable (ERD: `CLAIM |o--o{ MEETING`), `meeting_type` snake_case enum of the 10 canonical types, `meeting_date date NOT NULL`, `meeting_time time` nullable, `location text`, `notes text`, `participants` (array/JSONB of snake_case participant enum values: `employee | employer_hr | ncm | treating_physician | supervisor | attorney`), `is_done bool default false`, `version int` (mutable entity — CAS per conventions), `created_at timestamptz`
  - [ ] Seed step: for **each handler persona** (from the 1.2-seeded `app_user` rows, role `handler`), insert the two demo meetings from the prototype's `seedMeetingsIfEmpty` — (a) "RTW Check-In Call" 10:30, location Phone, participants Employee + Employer HR, linked to the handler's first scoped claim; (b) "Case Review — Reserve & Treatment Plan" 15:00, location Video Call, participants NCM + Supervisor, linked to their second scoped claim; `meeting_date` = seed-run current date so the demo shows today's meetings (see Dev Notes → seed-type mapping for the enum mapping of these two labels)
- [ ] Task 2: `services/claims` meeting commands & query (AC: 2, 3, 4)
  - [ ] `create_meeting` command: validates `meeting_date` present (422 problem+json on missing — the server-side half of AC 2), claim link must be inside the caller's scope (AD-7), inserts row + audit event `(action: create, entity: meeting)` with after-diff in the same transaction (AD-4)
  - [ ] `complete_meeting` command: PATCH-shaped, CAS on `expected_version`, sets `is_done = true`, audit event with before/after diff; 409 problem+json with fresh entity on version mismatch
  - [ ] `delete_meeting` command: CAS-guarded delete + audit event recording the removed row as the before-diff
  - [ ] `list_meetings` repository query: caller-context scoped (AD-7 — a user sees only their own meetings; claim joins filtered by employer scope), sorted by `meeting_date`, cursor-paginated `{items, nextCursor}` per list conventions
  - [ ] Upcoming/Done display status is server-derived (date ≥ today ∧ not done → upcoming) by a registered derivation (AD-10) — the SPA never re-derives it
- [ ] Task 3: API routes (AC: 1–4)
  - [ ] `POST /api/claims-diary/meetings`, `GET …/meetings` (cursor list), `PATCH …/meetings/{id}` (done, with `expectedVersion`), `DELETE …/meetings/{id}` — thin routers over the Task 2 commands, camelCase JSON via Pydantic alias, RFC 9457 problem+json errors, auth dependency builds the scope context (no caller-supplied scope)
  - [ ] Regenerate the OpenAPI client; add `meetings` keys to the shared `queryKeys` module (AD-9)
- [ ] Task 4: Copilot-panel shell + Diary tab scaffolding (AC: 1, 3) — *first Epic 4 story, so the right-pane shell lands here*
  - [ ] `web/src/features/copilot/`: panel shell per UX-DR8 — pulse indicator + "AI Adjuster Copilot" title, claim-context sub-line, two tabs: **⚡ Actions (disabled, tooltip "Copilot arrives with the AI epic")** — stays disabled until Epic 6 — and **📓 Diary** (active)
  - [ ] `web/src/features/diary/`: Diary tab body with sub-tab bar 📓 Notes · 📅 Meetings · ✉ Emails (UX-DR9); this story implements Meetings; Notes and Emails sub-tabs render explicit "arrives in Story 4.2 / 4.3" empty placeholders (NFR-3 empty states)
  - [ ] If a prior story already stubbed the right pane, extend it in place — check earlier Dev Agent Records; do not create a duplicate panel
- [ ] Task 5: Meeting scheduler modal (AC: 1, 2)
  - [ ] shadcn Dialog per UX-DR10 mirroring the prototype's modal: type select (10 options), read-only linked-claim field (steel-blue `--st`/`--sld` treatment, value `WC-nnnn — Worker Name`), date input defaulting to today, time defaulting 10:00, 6 participant checkboxes (Employee pre-checked), notes/agenda textarea, location/link input, Cancel + "📅 Schedule Meeting" actions
  - [ ] Missing date → inline field-level validation message; never a native `alert()` (NFR-3, UX-DR11); server 422 also maps to the inline message
  - [ ] On save: TanStack mutation → invalidate meetings keys → non-blocking success toast
- [ ] Task 6: Meetings list rendering (AC: 3, 4, 5)
  - [ ] Cards sorted by date: `✓/📅` + type, 🕐 date/time (+ ` · location` when set), 📋 linked claim (`WC-nnnn — Name`), italic notes, participant tags (NCM/Attorney get their accent classes per the prototype), Upcoming/Done card styling from the server-derived status (UX-DR9)
  - [ ] Actions row: "✓ Done" (hidden once done) → `complete_meeting` mutation; "Delete" → `delete_meeting` mutation; 409 on either rolls back and re-renders fresh state (AD-9)
  - [ ] "✉ Email participants" button rendered **disabled** with tooltip "Email composer arrives in Story 4.3" (AC 5 — cross-story seam)
  - [ ] Empty state: "No meetings scheduled…" message + the ＋ Schedule Meeting button (NFR-3)
  - [ ] Enable Story 3.5's meeting-kind action deep-links (the prototype's `goToMeetings`): "go to" opens the Diary tab → Meetings sub-tab → scheduler modal; diary-note-kind links stay disabled until 4.2
- [ ] Task 7: Tests + E2E gate (AC: all)
  - [ ] pytest: create validation (missing date 422), scope enforcement (handler A cannot create/list against a claim outside their book), CAS 409 round-trip on complete/delete, audit event emitted in-transaction for all three commands, seed migration produces exactly 2 meetings per handler persona
  - [ ] Vitest: modal inline validation, disabled email-button tooltip, disabled Actions tab
  - [ ] `e2e/stories/4-1-meeting-scheduling-management.spec.ts` tagged `@story:4-1 @epic:4`: login as a handler → Diary → Meetings shows the 2 seeded meetings → schedule a new meeting (happy path, tagged `@smoke`) → missing-date inline validation → mark done → delete → email button disabled

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story ships the **meetings** third of the diary aggregate plus the right-pane shell it lives in. **Meeting "scheduling" is a persisted log** — no calendar invite, no ICS, no notification is generated; real calendar egress is a deferred epic by architecture decision (spine → Deferred → "Email/calendar egress"). Do NOT build any egress path. Do NOT build: the Notes sub-tab (4.2), the email composer or `email_log`/`email_template` (4.3 — the "✉ Email participants" button exists but is disabled), or anything under the ⚡ Actions tab (Epic 6 — the tab renders disabled). The prototype's in-memory `meetingsStore` and login-time `seedMeetingsIfEmpty` are behavior references only — persistence replaces both, and the seeded-meetings clause of FR-LOGIN-3 is satisfied by this story's **migration**, not by login-time code.

### Architecture compliance (binding ADs for this story)

- **AD-1:** the meeting list, its sort, and Upcoming/Done status all come from the server; the SPA renders and captures input only.
- **AD-4:** all three mutations are audited commands — create/complete/delete each emit an `audit_event` with JSONB diffs in the same transaction; complete/delete are CAS-guarded on `expected_version` (meeting is a mutable entity).
- **AD-7:** `list_meetings` and every command take the repository caller context; a claim link outside the caller's employer scope is rejected. No endpoint accepts caller-supplied scope.
- **AD-10:** upcoming/done display status is one registered derivation; queue-side "today's meetings" (Story 4.2) will reuse it — never re-derive client-side.
- **AD-12:** the diary aggregate (`meeting`, and later `diary_note`, `email_log`, `email_template`) is owned by **`services/claims`** per the Capability → Architecture Map ("Diary, meetings, emails, templates … Lives in `services/claims` (diary aggregate)"). All writes go through its commands; no other service or router touches the table. Do not emit `timeline_event` rows for meetings — the prototype doesn't surface meetings on the claim timeline, and timeline emission belongs to claim-mutating commands.
- **AD-15:** story spec `e2e/stories/4-1-meeting-scheduling-management.spec.ts`, tags `@story:4-1 @epic:4`, one `@smoke` happy path; the story cannot move to `review`/`done` until it passes against the freshly reset e2e stack.
- **Conventions:** enums snake_case in DB/API with UI-owned labels (`meeting_type`, participant values); cursor-paginated list envelope; problem+json errors → toast/inline; dates ISO (`meeting_date` is a calendar `date`, `created_at` is `timestamptz`).

### Data notes

- **Creates:** `meeting` (write-owner: `services/claims`). No other table this story.
- **Uses:** `app_user` (owner FK + handler personas for seed), `claim` (optional link + scope check), `audit_event` (INSERT-only app role, from 1.2).
- **Participants:** a fixed 6-value set — store as an array/JSONB of snake_case enum values, not a join table (no participant-side queries exist anywhere in the requirements; keep it simple). UI labels ("Nurse Case Manager", "My Supervisor") are UI-owned.
- **Seed-type mapping (documented discrepancy):** the prototype's two demo meetings use type labels ("RTW Check-In Call", "Case Review — Reserve & Treatment Plan") that are **not** among the scheduler's 10 canonical types. Since `meeting_type` is an enum, seed them as `rtw_conference` and `claim_review_supervisor` respectively, carrying the prototype's fuller label in `notes` if fidelity matters. Flagged at story creation; if verbatim seed titles are wanted, that's a schema change request (free-text type), not dev discretion.
- **Seed dates:** the prototype seeds "today at login"; a migration runs once, so use the migration-run current date — the demo then shows today's meetings on day one and past meetings later, which is acceptable demo behavior (note it in the seed migration docstring).
- The Excel's `Table.Column` mapping is canonical for DB naming — check `docs/WC_Feature_Element_Details.xlsx` for any meeting-table naming rows before inventing column names.

### UX notes

- **UX-DR10 (modal):** mirror the prototype exactly — 10-type select, read-only claim field with the steel-blue token treatment, date required/defaulted-today, time defaulted 10:00, participant checkbox grid with Employee pre-checked, notes/agenda placeholder "Meeting agenda, topics to cover, documents needed…", location placeholder "e.g. Teams call, plant office, adjuster office…".
- **UX-DR9 (list):** date-sorted cards; Upcoming cards get the accent treatment, Done cards muted with ✓; participant tags with `ncm`/`atty` accent classes; actions ✓ Done / ✉ Email participants (disabled) / Delete.
- **UX-DR8 (panel shell):** pulse header + claim-context sub-line ship now so the pane looks like the prototype; only the Diary tab is interactive.
- **NFR-3 / UX-DR11:** the prototype's `alert('Please select a date.')` must NOT survive — inline validation at the date field; success/error via toasts.
- **Design tokens:** the prototype palette is **light** and canonical (Story 1.1 Dev Notes ruling); epics.md's "dark console aesthetic" wording is a documented discrepancy. Reuse the 1.1 Tailwind tokens (`--st`/`--sld` for the claim field, status colors for Upcoming/Done).

### Testing requirements (this story's definition of done)

- pytest: command unit tests (validation 422, scope rejection, CAS 409 with fresh-entity body, same-transaction audit emission for create/complete/delete), seed assertion (2 meetings × each handler persona, types mapped per the seed-type mapping).
- Vitest: modal validation, disabled states (Actions tab, email button + tooltips).
- Playwright `4-1-meeting-scheduling-management.spec.ts` (`@story:4-1 @epic:4`, one `@smoke`): seeded meetings visible, schedule/complete/delete round-trip, inline date validation, disabled email button. Selector policy: accessible role first, `data-testid` second, never CSS classes.
- ruff + mypy + eslint + tsc clean; Alembic upgrade clean on a fresh DB.

### Project Structure Notes

- Server: commands in `server/services/claims/` (diary aggregate module, e.g. `meetings.py`), repository in `server/data/`, router in `server/api/`; derivation registered in `server/services/derivations`.
- Web: `web/src/features/copilot/` (panel shell), `web/src/features/diary/` (sub-tabs + meetings UI + modal); query keys in the shared `queryKeys` module; shadcn Dialog/Tooltip/Checkbox/Select from `src/components/ui/`.
- Cross-story seams: 4.2 consumes `list_meetings` for the today's-meetings summary and the ✓ Done action; 4.3 enables the email button and reads meeting participants for pre-fill — keep the meeting card component's action row easy to extend.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 4.1]
- Epic 4 scope note (sends/schedules are logs; Actions tab disabled until Epic 6): [Source: _bmad-output/planning-artifacts/epics.md#Epic 4]
- Diary-aggregate ownership: [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map — "Diary, meetings, emails, templates"]
- AD-4 audited CAS commands / AD-7 scoping / AD-10 derivations / AD-12 ownership / AD-15 E2E gate: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- Deferred calendar egress: [Source: ARCHITECTURE-SPINE.md#Deferred — "Email/calendar egress"; docs/Architecture-LINEWORKER.md#10]
- Meeting modal markup + types + participants: [Source: docs/Workers_Comp_Prototype.html lines 536–579]
- `saveMeeting` / `renderMeetings` / `seedMeetingsIfEmpty` behavior: [Source: docs/Workers_Comp_Prototype.html lines 1840–1925]
- FR-LOGIN-3 seeded-meetings split (1.3/2.1/4.1): [Source: implementation-readiness-report-2026-08-09.md#Coverage Matrix]
- UX-DR8/9/10/11: [Source: _bmad-output/planning-artifacts/epics.md#UX Design Requirements]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
