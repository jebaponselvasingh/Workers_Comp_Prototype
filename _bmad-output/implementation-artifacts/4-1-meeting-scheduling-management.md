# Story 4.1: Meeting Scheduling & Management

Status: review

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

claude-opus-5[1m] (Claude Opus 5, 1M context), via the `bmad-dev-auto` workflow.

### Debug Log References

- **`web/src/features/copilot/` and `web/src/features/diary/` already existed — and contained nothing.** Task 4's instruction to "extend the right pane in place if a prior story stubbed it" resolves in two directions at once: the *directories* were created empty in Stories 1.1/1.2 (`.gitkeep`, 0 bytes), so the feature code is genuinely new, but a real right-pane stub **is** rendered in `features/shell/WorkspaceShell.tsx` and was extended in place, keeping its `aria-label="Copilot"` and `data-testid="copilot-pane"` because `WorkspaceShell.test.tsx`, `App.test.tsx` and existing e2e specs key off both.
- **The deep link crosses panes, and an event-shaped context would not survive lint.** `ClaimDetailPane` (centre) raises the intent; the copilot `<aside>` (right) consumes it. The obvious shape — a "somebody asked for the scheduler" event each consumer reacts to in a `useEffect` — is what `react-hooks/set-state-in-effect` refuses, and it produced three errors. `DiaryNav` therefore owns the state itself (`subTab`, `schedulerOpen`, `schedulerSession`), so the deep link is two setters in one handler and the whole feature contains no `useEffect`.
- **The scheduler opens below the `xl` breakpoint, over a diary nobody can see.** Two code comments confidently asserted the opposite ("does nothing visible — the honest behaviour"). Code review checked: the aside is `hidden … xl:flex`, which is `display:none` but still **mounted**, `MeetingsSubTab` is the default sub-tab, and the Radix dialog portals to `document.body`. So the modal does render, the meeting saves correctly, and the list behind it is invisible until the window widens. The comments were wrong rather than the code; both were corrected and an e2e test at 1024px now pins the real behaviour.
- **`meeting_type` is an enum and the prototype's two demo titles are not among its ten members.** Seeded as `rtw_conference` and `claim_review_supervisor` with the prototype's fuller labels carried in `notes`, exactly as the story's seed-type mapping directs. Flagged rather than quietly resolved: verbatim seed titles would be a schema change (free-text type), not dev discretion.
- **`meeting_date` comes from the database clock, and one test compared it to the local one.** Migration 0033 stamps `sa.func.current_date()` specifically because a Python `date.today()` and a Postgres `CURRENT_DATE` can disagree across a UTC midnight — then `test_meeting_seed.py` bounded the result with `date.today()` and reintroduced the straddle for any developer west of UTC running after local afternoon. Caught in review; the bound is now the database's own date.
- **A test that would have failed on a calendar rather than a regression.** `a_meeting_body()` defaulted `meetingDate` to `2026-09-01` and asserted `status == "upcoming"`; the route derives status from `utc_today()`, so the assertion inverts on 2026-09-02. The e2e spec and the vitest fixtures had already been given far-future dates; this file had not.
- **The list sorts ascending, which decides what silent truncation costs.** `(meeting_date, id)` ascending against a fifty-row default page means the rows that survive the cut are the *completed past* ones and newly scheduled meetings fall off the end. `useMeetings` shipped as a plain `useQuery` ignoring `nextCursor`; review reclassified that from a deferral to a defect for exactly this reason, and it is now a `useInfiniteQuery` with `total` and a "Show more".
- **`normalise_participants` dropped unknown values instead of refusing them,** while `_view` does `MeetingParticipant(value)` on read — so a token written by any non-Pydantic caller is accepted silently and then 500s on the next list. `MeetingParticipant`'s own docstring promises refusal at the boundary. It now raises `InvalidPatch` without echoing the value.
- **A seed migration's `downgrade()` is not automatically safe when the table is user-writable.** `0033`'s downgrade was written as the `DELETE FROM …` its 0011/0021/0027 precedents use — but those tables were seed-only, and `meeting` is written by three handler-facing commands from the day it ships. It now deletes only the tuples `upgrade()` builds.
- **The `noDerivation` guard was pointed at the new directories but could not catch the rule it was added for.** Its comment cites `m.date >= today && !m.done`; `DERIVED_FIELDS` held no `status`, `isDone`, `meetingDate` or `meetingTime`, and the only comparison rule needs a numeric literal on the right. Adding the field names armed it, verified by writing the offending comparison into `MeetingCard.tsx` and watching the guard fail.

### Completion Notes List

- All six ACs are implemented and covered end to end. Server: `meeting` table (structure `0032`, seed `0033`), audited CAS `create_meeting` / `complete_meeting` / `delete_meeting` plus `list_meetings` in `services/claims/meetings.py`, `meeting_status` registered as a derivation, four thin routes at `/claims-diary`. Web: the copilot panel shell in the existing aside, the Diary tab with Meetings implemented and Notes/Emails naming Stories 4.2/4.3, the UX-DR10 scheduler modal, the UX-DR9 card list, and the cross-pane deep link.
- **Story 3.5's `meetings` seam is live**, and enabling it was one deletion from `SEAM_REASONS` in `services/worklist/actions.py` plus a branch in the SPA's `navigate()` — the property `ActionTarget`'s docstring promised, now asserted directly. `diary` stays disabled and its shared sentence was re-worded to name Story 4.2, which required updating `tests/test_action_checklist.py`, `web/src/test/api-mock.ts` and `ActionsCard.test.tsx` rather than working around them.
- **Two deliberate divergences from the story text.** Feedback is the existing polite live region, not a toast: no toast provider exists in `web/`, Story 3.4 deferred building one to the first story with *two* surfaces needing it, and this story has one — so the primitive is deferred to 4.3 (Story 4.3's email send is the second surface). And no `timeline_event` is emitted for meetings, per AD-12 — which contradicts Story 3.5's prediction that this story would widen the tag set, so that prediction is corrected in `deferred-work.md` rather than left looking like missed work.
- **`/problems/invalid-cursor`, not the spec's `/problems/bad-cursor`.** The queue has answered `invalid-cursor` since Story 2.1 and the diary router imports its shared `BAD_CURSOR_RESPONSE` dict; two type strings for one failure across two list endpoints would make the SPA's error handling depend on which list it was reading.
- **Code review found 20 issues, all fixed** (3 medium, 17 low) — see the spec's Review Triage Log. The medium three: the silent fifty-row truncation, the seed downgrade deleting handler-created rows, and the participant validator dropping unknown values. Two findings were deferred (`feedbackFromError` has no 404/403 branch; `delete_meeting`'s audit diff takes `claim_business_id` from a pre-read row) and three rejected.
- **Gate, run twice — once at implementation, once after the review patches:** ruff + format clean (182 files), mypy strict clean, **pytest 1702 passed** (was 1646 at Epic 3 close), eslint 0 errors, tsc clean, **vitest 356 passed** (was 314), **Playwright 128/128** (was 123) against a stack rebuilt `--build` from patched source. `schema.d.ts` regenerated and stable.
- **Known rough edge:** below 1280px the copilot pane is hidden while the scheduler still opens over it. The pane has been `xl`-only since Story 2.1; the behaviour is now documented and tested rather than assumed, but reopening the breakpoint belongs to whoever owns the layout.

### File List

**New — server:** `services/claims/meetings.py` · `services/derivations/meeting_horizon.py` · `api/routers/diary.py` · `data/versions/20260817_0032_meeting.py` · `data/versions/20260817_0033_seed_meetings.py` · `tests/test_meetings.py` · `tests/test_meeting_seed.py`

**Modified — server:** `data/models/enums.py` (`MeetingType`, `MeetingParticipant`) · `data/models/core.py` (`Meeting`) · `data/models/__init__.py` (export) · `data/repositories/claims.py` (five scoped meeting queries) · `services/derivations/__init__.py` (registration) · `services/worklist/actions.py` (`meetings` seam live, `diary` re-worded) · `api/app.py` + `api/routers/__init__.py` (router mounted) · `tests/seed_fixture.py` (meeting oracles) · `tests/test_action_checklist.py` (seam wording)

**New — web:** `src/api/meetings.ts` · `src/features/copilot/CopilotPane.tsx` + `.test.tsx` · `src/features/diary/{DiaryNav,DiaryTab,MeetingsSubTab,MeetingSchedulerDialog,MeetingCard,labels}.tsx` · `src/features/diary/{DiaryNav,DiaryTab,MeetingsSubTab,MeetingSchedulerDialog,MeetingCard}.test.tsx` · `src/components/ui/checkbox.tsx` · `src/components/ui/textarea.tsx`

**Modified — web:** `src/api/queryKeys.ts` (meetings group) · `src/api/schema.d.ts` (regenerated) · `src/features/shell/WorkspaceShell.tsx` + `.test.tsx` (aside extended in place) · `src/features/claim-detail/ClaimDetailPane.tsx` (deep link) · `src/features/claim-detail/actions/ActionsCard.tsx` + `.test.tsx` (`meetings` navigable) · `src/features/queue/noDerivation.test.ts` (roots + fields) · `src/test/api-mock.ts` (meetings route, ordered before the case-file match)

**New — e2e:** `stories/4-1-meeting-scheduling-management.spec.ts` (`@story:4-1 @epic:4`, five tests, one `@smoke`)

**Modified — e2e:** `fixtures/seed.ts` (independent seed oracle) · `stories/2-2-case-header-stage-adaptive-overview.spec.ts` (page-wide `role="tab"` count re-scoped to the case file's own tablist)

**Deleted:** `src/components/ui/label.tsx` (vendored, imported nowhere)

**Artifacts:** `_bmad-output/implementation-artifacts/spec-4-1-meeting-scheduling-management.md` · `epic-4-context.md` (new) · `deferred-work.md` (9 items) · `sprint-status.yaml`
