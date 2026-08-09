# Story 4.2: Claim-Linked Diary Notes

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want a dated diary tied to my claims,
So that my working notes survive and stay attached to the case.

## Acceptance Criteria

1. **Given** the Notes sub-tab, **when** it renders, **then** it shows the time-of-day greeting, today's date, the active claim, and a today's-meetings summary with "✓ Done" / "Open Claim" actions wired to the meeting store and queue selection (UX-DR9).
2. **Given** the add-note input, **when** a note is submitted, **then** a `diary_note` row (table created here) persists via an audited command, tagged to the current claim and dated (FR-DIARY-1), and appears at the top of the reverse-chronological list.
3. **Given** the notes list, **when** it renders, **then** notes show newest-first with date and claim tag, with an empty state when there are none (NFR-3); the prototype's note-triggered SLA recalc is obsolete because the strip is always server-computed (FR-SLA-1).
4. **Given** Epic 3's "Log Diary Entry" action deep-link, **when** clicked, **then** it opens this sub-tab focused on the add-note input, enabling the link Story 3.5 left disabled.

## Tasks / Subtasks

- [ ] Task 1: `diary_note` table migration (AC: 2)
  - [ ] Alembic migration creating `diary_note`: surrogate `id` int PK, `app_user_id` FK NOT NULL (author — ERD: `APP_USER ||--o{ DIARY_NOTE : writes`), `claim_id` FK nullable (ERD: `CLAIM |o--o{ DIARY_NOTE : tagged_to` — a note saved with no claim selected is legal), `note_text text NOT NULL`, `noted_at timestamptz NOT NULL` (server clock, UTC)
  - [ ] No `version` column — notes are create-only in v1 (no edit/delete UI exists in the design contract); append-only rows are exempt from CAS per the write-concurrency convention. No seed data — notes start empty.
- [ ] Task 2: `services/claims` note command & query (AC: 2, 3)
  - [ ] `create_diary_note` command: validates non-empty `note_text` (422 problem+json otherwise — the prototype silently ignores empty input; here it's inline-messaged), claim tag must be inside the caller's employer scope when present (AD-7), inserts row + audit event `(action: create, entity: diary_note)` with after-diff in the same transaction (AD-4)
  - [ ] `list_diary_notes` repository query: caller-context scoped (a user reads only their own notes — the prototype keys `diaryNotes` by user, across all their claims, and the list is NOT filtered by the selected claim), ordered `noted_at DESC`, cursor-paginated `{items, nextCursor}`
  - [ ] Do NOT port `recalcSLA`: the prototype recomputes the SLA strip when a note is saved; that trigger is obsolete — the strip is always server-computed in `services/worklist` per Story 1.5 (FR-SLA-1, AD-2). Saving a note touches nothing but `diary_note` + `audit_event`.
- [ ] Task 3: API routes (AC: 2, 3)
  - [ ] `POST /api/claims-diary/notes`, `GET …/notes` (cursor list) — thin routers over Task 2, camelCase JSON, problem+json errors, auth dependency builds the scope context
  - [ ] Regenerate the OpenAPI client; add `diaryNotes` keys to the shared `queryKeys` module (AD-9)
- [ ] Task 4: Notes sub-tab UI (AC: 1, 2, 3)
  - [ ] Greeting card (`dgreet` in the prototype): time-of-day greeting from the client clock ("Good morning" < 12:00, "Good afternoon" < 17:00, else "Good evening") + first name + 👋; "Today is **{long date}**"; active-claim line `Active: WC-nnnn — Name (injury type)` when a claim is selected; upcoming-meetings count line when > 0 ("📅 N upcoming meetings — see below / Meetings tab"); hint line "Log notes below · Schedule meetings · Email stakeholders via tabs above."
  - [ ] Today's-meetings summary: "📅 Today's Meetings (N)" section listing today's meetings time-sorted (reusing Story 4.1's `list_meetings` query + server-derived done/upcoming status — AD-10), each card with 🕐 time — type, 📍 location, 📋 claim link, and actions: "✓ Done" → 4.1's `complete_meeting` mutation (invalidates the shared meetings keys so the Meetings sub-tab agrees), "Open Claim" → drives the queue selection + detail pane to that claim (the 4.2 half of "wired to the meeting store and queue selection"); "📅 No meetings scheduled for today." when empty
  - [ ] Add-note input pinned at the bottom (placeholder "Add a diary note for today…") + Save; empty text → inline validation; success → clear input, invalidate notes keys, note appears at top
  - [ ] Notes list newest-first: date · time header, note text, `📎 WC-nnnn` claim tag when tagged; "No notes yet." empty state (NFR-3)
- [ ] Task 5: Enable Epic 3's "Log Diary Entry" deep-link (AC: 4)
  - [ ] Story 3.5 rendered diary-kind action "go to" buttons disabled with a tooltip; flip them live: clicking opens the copilot pane → Diary tab → Notes sub-tab and focuses the add-note input (the prototype's `goToDiaryNotes`)
  - [ ] Meeting-kind deep-links were enabled by 4.1 — verify both kinds against Story 3.5's action list and remove any leftover disabled/tooltip states for diary kinds only (Fraud Indicators stays disabled until Epic 6)
- [ ] Task 6: Tests + E2E gate (AC: all)
  - [ ] pytest: empty-text 422, out-of-scope claim tag rejected, audit event emitted in-transaction, list ordering + pagination, null-claim note allowed
  - [ ] Vitest: greeting bucket boundaries (11:59/12:00/16:59/17:00), empty states, add-note flow
  - [ ] `e2e/stories/4-2-claim-linked-diary-notes.spec.ts` tagged `@story:4-2 @epic:4`: login as handler → Diary → Notes shows greeting + today's seeded meetings → add a note and see it top-of-list with claim tag (happy path, tagged `@smoke`) → mark a today's meeting done from the summary → Open Claim drives queue selection → 3.5 deep-link opens and focuses the input → note survives reload (persistence proof)

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story ships the **Notes** sub-tab: a persisted, per-user, claim-tagged diary plus the greeting/today's-meetings dashboard at the top of it. It builds ON Story 4.1's meetings plumbing (the summary reuses `list_meetings` and `complete_meeting` — no new meeting endpoints, no second writer on `meeting`). Do NOT build: the email composer or Emails sub-tab (4.3), any Actions-tab behavior (Epic 6), note editing/deletion (not in the design contract — create-only), or any SLA computation (Task 2 explicitly buries the prototype's `recalcSLA`-on-note-save; the strip has been server-computed for every role since Story 1.5). The prototype's in-memory `diaryNotes` global is a behavior reference only.

### Architecture compliance (binding ADs for this story)

- **AD-1:** note rows, their order, and the today's-meetings data come from the server; the SPA renders and captures input. (The greeting's time-of-day bucket and "today is" line are viewer-clock presentation, not business data — client-side is correct there.)
- **AD-2 / FR-SLA-1:** SLA aggregation exists exactly once in `services/worklist`; this story must not add a second trigger or computation path — the note-save → SLA-recalc coupling from the prototype is the pattern AD-2 exists to kill.
- **AD-4:** `create_diary_note` is an audited command — audit event in the same transaction. As an append-only entity, `diary_note` carries no `version` and is exempt from CAS (write-concurrency convention: "Append-only stores … are exempt — never read-modify-written").
- **AD-7:** list/create take the repository caller context; a note can only be tagged to a claim inside the caller's book; no endpoint accepts caller-supplied scope.
- **AD-10:** today's-meetings done/upcoming status comes from 4.1's registered derivation — this surface and the Meetings sub-tab must agree because they call the same function.
- **AD-12:** `diary_note` is part of the diary aggregate owned by `services/claims`; the ✓ Done action requests the change through 4.1's owning command — this story writes nothing to `meeting`. No `timeline_event` emission for notes (not a claim mutation; the prototype doesn't surface notes on the claim timeline).
- **AD-15:** story spec `e2e/stories/4-2-claim-linked-diary-notes.spec.ts`, tags `@story:4-2 @epic:4`, one `@smoke`; story cannot reach `review`/`done` until it passes.
- **Conventions:** cursor-paginated list envelope; problem+json → inline/toast; `noted_at` as UTC `timestamptz`, formatted in the UI only.

### Data notes

- **Creates:** `diary_note` (write-owner: `services/claims`, create-only in v1). No seed.
- **Uses:** `meeting` (read via 4.1's query; ✓ Done through 4.1's command), `app_user`, `claim` (tag + scope check), `audit_event`.
- The Excel's `Table.Column` mapping is canonical for DB naming — check `docs/WC_Feature_Element_Details.xlsx` for diary-note rows before inventing column names.
- Per-user, not per-claim: the notes list shows all the user's notes (each carrying its claim tag); it is not filtered by the selected claim. This matches the prototype and FR-DIARY-1's "tied to a claim" via the tag, not via list scoping.

### UX notes

- **UX-DR9:** time-of-day greeting, today's date, reverse-chronological list, Upcoming/Done meeting styling with ✓ actions — this story is the primary bearer of UX-DR9's greeting clause; match the prototype's `renderDiary` layout (greeting card → today's meetings → notes).
- **UX-DR11 / NFR-3:** no native dialogs anywhere; inline validation on the empty input; loading/empty states on both the summary and the list.
- The prototype greeting also shows caseload color (high-risk/fraud counts feed its text in earlier revisions) — the shipped contract text is the greeting + date + active claim + meetings count + hint line; anything needing counts must come from existing scoped aggregates, not client math (AD-1). Keep to the contract text.
- **Design tokens:** the prototype palette is **light** and canonical (Story 1.1 ruling); epics.md's "dark console aesthetic" is a documented discrepancy. Note cards use muted/faint text tokens (`--mt`/`--ft`), claim tags the steel-blue pair.

### Testing requirements (this story's definition of done)

- pytest: command validation (422), scope rejection, same-transaction audit emission, DESC ordering + cursor pagination, nullable-claim path.
- Vitest: greeting boundaries, empty states, submit-clears-input.
- Playwright `4-2-claim-linked-diary-notes.spec.ts` (`@story:4-2 @epic:4`, one `@smoke`): add-note persistence round-trip (survives reload — the point of FR-DIARY-1), today's-meetings ✓ Done + Open Claim wiring, 3.5 deep-link focus. Selector policy: role first, `data-testid` second, never CSS classes.
- ruff + mypy + eslint + tsc clean; Alembic upgrade clean on a fresh DB.

### Project Structure Notes

- Server: `server/services/claims/` diary module (e.g. `notes.py` beside 4.1's `meetings.py`), repository in `server/data/`, router in `server/api/`.
- Web: `web/src/features/diary/` — Notes sub-tab component beside 4.1's Meetings component; today's-meetings summary should reuse 4.1's meeting-card component (compact variant) rather than duplicating markup; queryKeys shared with 4.1 so ✓ Done invalidates both surfaces.
- Cross-story seams: 4.1 built the sub-tab bar with a Notes placeholder — replace the placeholder, don't re-scaffold; 4.3 will add the Emails sub-tab next to this one; Story 3.5's action-list component owns the deep-link buttons — the change there is flipping diary-kind entries from disabled to the navigation callback.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 4.2]
- Epic 4 scope note: [Source: _bmad-output/planning-artifacts/epics.md#Epic 4]
- Diary-aggregate ownership: [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map — "Diary, meetings, emails, templates"]
- AD-2 (single SLA computation) / AD-4 (append-only exemption) / AD-7 / AD-10 / AD-12 / AD-15: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules / #Consistency Conventions]
- SLA strip server-computed for every role: [Source: _bmad-output/planning-artifacts/epics.md#Story 1.5]
- 3.5 disabled deep-links seam: [Source: _bmad-output/planning-artifacts/epics.md#Story 3.5]
- `renderDiary` (greeting, today's meetings, notes list) + `saveDiary` + `goToDiaryNotes`: [Source: docs/Workers_Comp_Prototype.html lines 919, 1754–1769, 2046–2080]
- UX-DR9/11: [Source: _bmad-output/planning-artifacts/epics.md#UX Design Requirements]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
