---
title: 'Story 4.2 — Claim-Linked Diary Notes'
type: 'feature'
created: '2026-08-17'
status: 'done'
baseline_revision: '4c0ecd107f8345f36c91c8bd3d9dfa3059c166df'
final_revision: 'a14109ad1ca4b19bc0d59e36df14164acd2e7782'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/_bmad-output/implementation-artifacts/4-2-claim-linked-diary-notes.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-4-1-meeting-scheduling-management.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The handler has no persisted diary. The prototype kept notes in an in-memory `diaryNotes` global a reload erased, the Notes sub-tab Story 4.1 built is still a placeholder reading "Dated diary notes arrive with the notes tab — Story 4.2", and Story 3.5's "Log Diary Entry" deep link ships disabled with nowhere to go — pointing at a weekly check-in action that, today, nothing can ever satisfy.

**Approach:** Add `diary_note` — an append-only, per-user, optionally claim-tagged table owned by `services/claims` — with one audited create command and a caller-scoped cursor-paginated newest-first list; replace the Notes placeholder with the greeting card, a server-derived today's-meetings summary reusing Story 4.1's meeting plumbing, the pinned add-note input and the notes list; and close the Story 3.5 seam end-to-end by enabling the `diary` target *and* making the check-in rule stop firing once a note exists, so the deep link is the completion path rather than a link to nowhere.

## Boundaries & Constraints

**Always:**
- `create_diary_note` lives in `services/claims/notes.py` and is the only writer of `diary_note`. It follows the house command ladder verbatim: role gate → validation → scoped write → `audit.record` in the **same transaction** → `db.commit()` → `db.expire_all()` → re-read.
- Every read and write takes `CallerContext`; scope is applied in the repository only (AD-7). A note is visible only to its author (`app_user_id == ctx.user_id`); a claim tag is accepted only if that claim passes `employer_scope(ctx)`. Out-of-scope and absent are the same 404.
- Only the `handler` role may create a note — raise `EditNotPermitted` imported from `services/claims/edit.py`, never redefined.
- **Upcoming/done and today-ness are server-derived** (AD-1, AD-10). `features/diary` and `features/copilot` are scanned by `web/src/features/queue/noDerivation.test.ts`; the browser may not sort, count, or compare meeting dates, statuses or counts. Only the *viewer's own clock* is client-side: the greeting bucket, the "Today is …" line, and the local calendar day sent to the server as `day`.
- SLA is untouched. Do **not** port `recalcSLA` — saving a note writes `diary_note` + `audit_event` and nothing else (AD-2, FR-SLA-1).
- Note text is PHI-class: never logged. `audit.record` already logs field names only; keep it that way.
- JSON is camelCase via `ApiModel`; errors are RFC 9457 problem+json; `noted_at` is UTC `timestamptz` formatted in the UI only.

**Block If:**
- Satisfying the today's-meetings summary or the upcoming count would require a **new meetings endpoint** or a second writer on `meeting` (the story forbids both) *and* the extension described in Design Notes proves impossible — that is an architecture question, not dev discretion.
- A generic "action state" / "completed actions" table appears necessary. Completions are entity-backed by design (`services/worklist/actions.py` docstring; spec-3-5 Block If). **HALT rather than add one.**
- Tightening `_diary_check_in` cannot be expressed as a read (`services/worklist` must never write — AD-12).

**Never:**
- For `diary_note`: no editing or deletion, no `version` column, no CAS and therefore no 409 path — it is create-only in v1, and append-only rows are exempt from compare-and-swap. (Story 4.1's `complete_meeting` keeps its own CAS and 409; the summary calls it unchanged.)
- No seed data. Notes start empty.
- No `timeline_event` for notes, and no second writer on `meeting` — the ✓ Done action calls Story 4.1's `complete_meeting`.
- No Emails sub-tab or composer (4.3), no Actions-tab behaviour (Epic 6). Both stay explicit seams.
- No toast provider, queue or library — feedback follows the shipped `role="status"` / `role="alert"` precedent. No native `alert()`/`confirm()`.
- No react-hook-form, no zod, no new `hooks/` directory, no `dark:` utilities, no dark palette.
- No fifth `ActionCommand` member and no completion button on the diary row — see Design Notes.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Create happy path | handler, `POST /api/claims-diary/notes` with `noteText` and an in-scope `claimId` | 201 with the created note (`id`, `noteText`, `notedAt`, `claimId`, `workerName`); exactly one `audit_event` (`create_diary_note`/`diary_note`, `before=null`) in the same transaction | No error expected |
| Create untagged | body omits `claimId` (no claim selected) | 201; row persists with `claim_id = NULL`; the list renders it with no claim tag | No error expected |
| Create with empty text | `noteText` is `""` or whitespace only | 422 problem+json `/problems/invalid-patch`, detail names `noteText` | Inline message at the input; input keeps its text; no row, no audit event |
| Create over length | `noteText` longer than 2000 characters | 422 problem+json `/problems/validation-error` (Pydantic `max_length` refuses before the command) | Inline message at the input |
| Create against a claim outside scope | handler A posts a `claimId` in handler B's employer | 404 problem+json `/problems/note-claim-not-found`, same wording as absent | Inline refusal; no row, no audit event |
| Create by non-handler | supervisor or analyst session | 403 problem+json `/problems/edit-not-permitted` | Add-note form not rendered for those roles; the server is the gate |
| List | handler with N own notes | `{items, nextCursor, total}` ordered `noted_at DESC, id DESC` (total order), newest first | — |
| List isolation | handler A requests the list | Only notes whose `app_user_id == A`; a note tagged to a claim now outside A's scope is still A's own note and is still returned | No leakage of another user's notes |
| List paging | more notes than the page limit | `nextCursor` issued; walking it visits every note exactly once | Bad or expired cursor → 400 `/problems/invalid-cursor`, never a silent page-one fallback |
| Today's meetings | `GET /api/claims-diary/meetings?day=<local ISO date>` | That day's meetings for the caller, ordered by time (all-day first), each carrying its server-derived `status`; envelope also carries `upcomingCount` for the whole book | Loading / empty / error states rendered; empty day → "📅 No meetings scheduled for today." |
| ✓ Done from the summary | handler clicks ✓ Done on a today card | Story 4.1's `complete_meeting` runs; both the day list and the Meetings sub-tab show it done | 409 → the fresh meeting is installed inline and both lists refetch; never retried |
| Diary deep link | claim in `treatment` with no recent note; handler clicks "Log Diary Entry →" | Right pane opens Diary → Notes with the add-note input focused | — |
| Check-in completion | a note tagged to that claim is saved | The `diary_check_in` row drops out of the regenerated action checklist for `DIARY_CHECK_IN_DAYS` | — |

</intent-contract>

## Code Map

Server (`lineworker/server/`):
- `data/models/core.py` + `data/models/__init__.py` -- NEW `DiaryNote` model: `id` Identity PK, `app_user_id` FK NOT NULL `index=True`, nullable `claim_id` FK `index=True`, `note_text Text`, `noted_at DateTime(timezone=True)` `index=True`. **No `version`** — paraphrase `TimelineEvent`'s append-only justification in the docstring. Register in `__init__.py` import **and** `__all__`, alphabetically (`DiaryNote` sits between `Document` and `Employee`).
- `data/versions/20260817_0034_diary_note.py` -- NEW structure migration, `revision="0034_diary_note"`, `down_revision="0033_seed_meetings"` (current head), `op.f()` constraint names, both `GRANT` lines, drop-indexes-then-table downgrade. No enum, no seed.
- `data/repositories/claims.py` -- ADD `diary_note_scope(ctx)` (owner AND nullable-claim-through-`employer_scope`, copying `meeting_scope` at line 951), `_diary_note_query()` (outer joins to `Claim`/`Employee` for the labelled `claim_business_id`/`worker_name`), `insert_diary_note` (two-branch INSERT…FROM SELECT, copying `insert_meeting`), `select_diary_notes_page`, `count_diary_notes`, under a `# --- Story 4.2: the diary aggregate's notes ---` banner. MODIFY the meeting queries per Design Notes ("One ordering rule").
- `services/claims/notes.py` -- NEW; `CREATE_ACTION="create_diary_note"`, `ENTITY="diary_note"`, `MAX_NOTE_LENGTH: Final[int] = 2000`, `NOTE_TEXT_FIELD="noteText"`, the page-limit and cursor-age constants copied from `meetings.py`, `create_diary_note`, `list_diary_notes`, `Cursor`/`encode_cursor`/`decode_cursor`, `_view`, `_diff`, `DiaryNoteClaimNotVisible`, `InvalidCursor`.
- `services/derivations/meeting_horizon.py` -- ADD the SQL twin of the existing `meeting_status` rule (`upcoming_predicate`) beside the Python classifier, plus `upcoming_count` support; one rule, two renderings.
- `api/routers/diary.py` -- ADD `POST /claims-diary/notes` and `GET /claims-diary/notes` to the existing router (no new router file); ADD the `day` query param and `upcomingCount` field to the meetings list per Design Notes.
- `services/worklist/actions.py` -- DELETE the `ActionTarget.diary` entry from `SEAM_REASONS`; tighten `_diary_check_in` to "in treatment AND no note on this claim within `DIARY_CHECK_IN_DAYS`"; update the dict's doc-comment.

Web (`lineworker/web/`):
- `src/lib/clock.ts` + `clock.test.ts` -- NEW: `greetingFor(now)`, `todayIso(now)`, `formatLongDate(now)`. **Outside** the `noDerivation` scan roots, which is why the greeting's `< 12` / `< 17` comparisons live here.
- `src/api/queryKeys.ts` -- ADD `diaryNotes: { list, writes }`; CHANGE `meetings.list` usage to allow a day-scoped sibling key (see Design Notes).
- `src/api/diaryNotes.ts` -- NEW `useDiaryNotes()` (`useInfiniteQuery`), `useAddDiaryNote()`, `useDiaryNoteWriteInFlight()`, mirroring `src/api/meetings.ts`.
- `src/api/meetings.ts` -- MODIFY: `useMeetings(options?: { day?: string })`, `upcomingCount` surfaced from `select`, and `replaceInList` invalidating by **prefix** so the day list and the full list both refresh.
- `src/api/schema.d.ts` -- REGENERATE (CI diffs this file).
- `src/features/diary/NotesSubTab.tsx` -- NEW: greeting card, today's-meetings summary, notes list, pinned add-note form, live region.
- `src/features/diary/MeetingCard.tsx` -- MODIFY: add a `compact` variant rendering ✓ Done + Open Claim only.
- `src/features/diary/DiaryTab.tsx` -- MODIFY: delete `SEAMS.notes`, narrow the record to `Record<"emails", …>`, mount `<NotesSubTab>`; update the file docstring.
- `src/features/diary/DiaryNav.tsx` -- MODIFY: add `noteFocusSession: number` and `requestNotes: () => void` to the interface, `NO_DIARY_PANE`, the provider and the `useMemo` deps.
- `src/features/claim-detail/ClaimDetailPane.tsx` -- MODIFY `navigate`: `if (target === "diary") { requestNotes(); }`.
- `src/features/claim-detail/actions/ActionsCard.tsx` -- MODIFY: add `"diary"` to `NAVIGABLE_FROM_OVERVIEW` (without it an enabled diary row renders no control at all).
- `src/features/queue/useSelectedClaim.ts` -- MODIFY: extract `useSelectClaim(): (claimId: string) => void` from the existing `select` callback and have `useSelectedClaim` reuse it. One source of truth; the URL `?claim=` param stays the store.
- `src/features/diary/MeetingSchedulerDialog.tsx` -- MODIFY: replace its private `today()` with `todayIso()` so the two definitions cannot drift.
- `src/features/queue/noDerivation.test.ts` -- MODIFY: add `upcomingCount` to `DERIVED_FIELDS` and assert the scan reaches `features/diary/NotesSubTab.tsx`.
- `src/index.css` tokens only — no new palette. `.dgreet` ports to `bg-steel-soft` + steel border; note cards to `bg-surface border-border`; the claim tag to `text-steel`.

Tests:
- `server/tests/test_diary_notes.py` -- NEW (`pytestmark = requires_db`).
- `server/tests/test_meetings.py`, `tests/test_action_checklist.py` -- MODIFY per the ordering change and the seam going live.
- Colocated `NotesSubTab.test.tsx`, `MeetingCard.test.tsx` (compact), `DiaryTab.test.tsx`, `DiaryNav.test.tsx`, `ActionsCard.test.tsx`; `src/test/api-mock.ts` -- ADD a `/api/claims-diary/notes` route **beside the meetings block, before `/api/claims/`**.
- `e2e/stories/4-2-claim-linked-diary-notes.spec.ts` -- NEW, `@story:4-2 @epic:4`, one `@smoke`, file-level `test.use({ viewport: { width: 1440, height: 900 } })`.

## Tasks & Acceptance

**Execution:**
- [x] `data/models/core.py`, `data/models/__init__.py` -- add the `DiaryNote` model with no `version` column and the append-only justification in its docstring -- the table AC 2 persists to.
- [x] `data/versions/20260817_0034_diary_note.py` -- structure migration on head `0033_seed_meetings`, both grants, drop-indexes-then-table downgrade -- AC 2; keep full CRUD grants because Story 8.1's purge needs DELETE.
- [x] `data/repositories/claims.py` -- add `diary_note_scope`, `_diary_note_query`, `insert_diary_note`, `select_diary_notes_page` (keyset `(noted_at, id)` **descending** — flip both the comparison and the ordering), `count_diary_notes` -- AC 2, 3; scope lives here and nowhere else.
- [x] `services/derivations/meeting_horizon.py` -- add the SQL twin of the upcoming rule beside the Python classifier and a docstring binding them -- one rule, two renderings; the greeting's count must not restate it.
- [x] `data/repositories/claims.py` + `services/claims/meetings.py` + `api/routers/diary.py` -- one ordering rule `(meeting_date, COALESCE(meeting_time,'00:00'), id)`, the three-member cursor, the optional `day` filter, and `upcomingCount` on the envelope -- AC 1; this is what lets the summary be server-derived without a new endpoint.
- [x] `services/claims/notes.py` -- `create_diary_note` (role → validate → scoped insert → audit → commit → `expire_all` → re-read) and `list_diary_notes` with the opaque cursor -- AC 2, 3; `_diff` carries the claim's **business** id so Story 8.1's purge can find it.
- [x] `api/routers/diary.py` -- `POST`/`GET /claims-diary/notes` with `ApiModel` schemas, `Field(description=…)`, `max_length=MAX_NOTE_LENGTH`, `pattern=CLAIM_ID_PATTERN`, `Cache-Control: no-store`, and `responses=` dicts reusing `UNAUTHENTICATED_RESPONSE`/`FORBIDDEN_RESPONSE`/`BAD_CURSOR_RESPONSE` -- AC 2, 3.
- [x] `services/worklist/actions.py` -- delete the `diary` seam entry and tighten `_diary_check_in` to "in treatment AND no note on this claim within `DIARY_CHECK_IN_DAYS` (7)" -- AC 4, 5; the module reads, never writes (AD-12).
- [x] `src/lib/clock.ts` + `clock.test.ts` -- `greetingFor`, `todayIso`, `formatLongDate`, all built from **local** calendar parts -- AC 1; `toISOString()` is UTC and wrong here.
- [x] `src/api/queryKeys.ts`, `src/api/diaryNotes.ts`, `src/api/meetings.ts`, `src/api/schema.d.ts` -- keys, hooks, the `day` option, prefix invalidation and a regenerated client -- AC 1, 2, 3.
- [x] `src/features/diary/MeetingCard.tsx` -- add the `compact` variant (✓ Done + Open Claim, no Delete, no email seam) reusing `formatWhen` -- AC 1; do not duplicate the markup or write a second time formatter.
- [x] `src/features/diary/NotesSubTab.tsx` -- greeting card, today's-meetings summary, notes list, pinned add-note form, `clearFeedback()` on every path, one `sr-only` live region -- AC 1, 2, 3.
- [x] `src/features/diary/DiaryTab.tsx` -- mount `NotesSubTab`, delete the `notes` seam, keep only-the-selected-sub-tab-mounted -- AC 1.
- [x] `src/features/diary/DiaryNav.tsx`, `src/features/claim-detail/{ClaimDetailPane,actions/ActionsCard}.tsx`, `src/features/queue/useSelectedClaim.ts` -- `requestNotes()` + `noteFocusSession`, the `diary` navigate branch, `"diary"` in `NAVIGABLE_FROM_OVERVIEW`, and the extracted `useSelectClaim` that Open Claim drives -- AC 1, 4.
- [x] `server/tests/test_diary_notes.py` -- cover every I/O Matrix row (422 empty and over-length, 403, 404 scope, list isolation, DESC total ordering, paging visits every note exactly once, invalid cursor, null-claim path, same-transaction audit, **no** timeline event) -- AC all.
- [x] `server/tests/{test_meetings,test_action_checklist}.py` -- update for the ordering/`day`/`upcomingCount` change; replace `test_the_diary_seam_names_the_story_that_delivers_it` with `test_the_diary_target_is_live_since_story_4_2` (modelled on the 4.1 template) and drop the diary tuple from the seam loop; add the check-in boundary test -- AC 4, 5.
- [x] Colocated vitest + `src/test/api-mock.ts` -- greeting boundaries, empty states, submit-clears-input, inline 422, compact card actions, Open Claim selection, `noDerivation` additions -- AC 1, 2, 3.
- [x] `e2e/stories/4-2-claim-linked-diary-notes.spec.ts` -- the story gate -- AC all.
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- record the completion-control decision, the meetings-list wire extension, and any new seam -- housekeeping; the 3.5 entry that asked for this decision must be answered, not left open.

**Acceptance Criteria:**
- Given a handler on the Notes sub-tab, when it renders, then it shows the greeting with their first name, today's long date, the active claim line (or "Select a case"), the upcoming-meetings count when greater than zero, the hint line, and the today's-meetings section — all counts and statuses taken from the server.
- Given a today's-meetings card, when ✓ Done is used, then Story 4.1's command persists it and both the summary and the Meetings sub-tab show it done; when Open Claim is used, then the queue selection and the case file move to that claim.
- Given the add-note input, when a valid note is submitted, then it persists, the input clears, and the note appears at the top of the list with its date, time and claim tag; when empty text is submitted, then an inline refusal appears at the input and nothing is written.
- Given a claim in treatment whose checklist emits the diary check-in, when the handler follows "Log Diary Entry →", then the right pane opens Diary → Notes with the add-note input focused — the control Story 3.5 shipped disabled.
- Given a note saved against that claim, when the checklist regenerates, then the diary check-in no longer fires, and it fires again once the note is older than `DIARY_CHECK_IN_DAYS`.
- Given a page reload, when the handler returns to the Notes sub-tab, then previously saved notes are still listed — the point of FR-DIARY-1.
- Given the full gate, when ruff, mypy, pytest, eslint, tsc, vitest and the tagged Playwright spec run, then all pass against a freshly rebuilt e2e stack.

## Spec Change Log

## Review Triage Log

### 2026-08-17 — Review pass (follow-up)

- intent_gap: 0
- bad_spec: 0
- patch: 19: (high 2, medium 7, low 10)
- defer: 1: (high 0, medium 0, low 1)
- reject: 0
- addressed_findings:
  - `[high]` `[patch]` The claim-tag capture the first pass added had a null corner that lied: a draft started with no claim selected pinned `null` forever, so selecting a claim afterwards — including via this pane's own Open Claim — still wrote the note permanently untagged, while the form rendered "No claim selected" with a case visibly open and `data-pinned` set to true. The pinned-null case now has its own sentence, and a test starts a draft with nothing selected and then moves the selection.
  - `[high]` `[patch]` The list-scope predicate contradicted this spec's own I/O matrix. The matrix says a note tagged to a claim now outside the caller's scope "is still A's own note and is still returned"; `diary_note_scope` filtered it out and a test asserted the disappearance, so a reassigned handler lost every note they had ever written about that employer, with no tombstone and no other read path. The predicate is author-only now — employer scope still gates *accepting* a tag on write — and the test was flipped. The privacy argument for the old behaviour is real and is recorded in `deferred-work.md` rather than silently kept.
  - `[medium]` `[patch]` The one-clock fix made `issued_on` equal the day being asked about, so on every day-filtered page the future-check and the seven-day `MAX_CURSOR_AGE` became `day > day` and `day - day > 7d` — both structurally unreachable, silently voiding the expiry the cursor's docstring promises. `issued_on` is a server stamp from `utc_today()` again, checked against the same clock.
  - `[medium]` `[patch]` `day` was unvalidated and, after the one-clock fix, also set the horizon for `status` and the whole-book `upcomingCount` — so `?day=1970-01-01` returned every open meeting as upcoming, and a pane left open past local midnight silently re-counted yesterday's horizon. Bounded where it acts as the clock, with the route description corrected to separate `upcomingCount`'s membership from its horizon.
  - `[medium]` `[patch]` Both sub-tabs tested `isError` before checking for cached data, so a failed *refetch* discarded a correct list that was already in hand — and because this story made ✓ Done always refetch on 200, a transient blip right after a successful completion blanked the summary the handler had just acted in. Both ladders now branch on "errored with data in hand".
  - `[medium]` `[patch]` `/problems/note-not-readable` — the type the first pass created precisely to stop a duplicate write — left every affordance pointing at that duplicate: the draft stayed, Save stayed enabled, and neither the notes list nor the checklist was invalidated, so the committed note never appeared to prove it existed. Handled in `onError`.
  - `[medium]` `[patch]` The first pass narrowed typing-clears-a-meeting-refusal but left the mirror: ✓ Done and Open Claim still called `add.reset()`, erasing a note refusal mid-read with the unsent draft still in the box. Split into a meeting-only reset.
  - `[medium]` `[patch]` The `notFound` member added to the shared `FieldFeedback` matched any bare 404, so a proxy or deploy-skew 404 rendered the synthesised "The server answered 404." as a permanent refusal under a severity-score input — and because `InlineEditField` derives its test id from the feedback kind, every inline-edit surface in the app silently changed its 404 test id while only the diary paths were re-tested. Now gated on known problem types with a fallback.
  - `[medium]` `[patch]` The `DiaryNote` docstring claimed "nothing about it reaches a log beyond ids and field names" while `_diff` writes the full note text into `audit_event.after`. The docstring now says what the code does; the retention consequence is deferred to Story 8.1.
  - `[low]` `[patch]` The cursor's day-mismatch check ran after the age check, so a cursor from another day was refused with a clock sentence rather than the filter sentence written for it.
  - `[low]` `[patch]` `noteFocusPending` was raised on intent but only ever lowered by `selectSubTab`, and `requestMeetings` bypassed it — unreachable today, but Epic 6 makes the copilot strip a real two-way switch and the caret theft returns. Lowered where it is consumed.
  - `[low]` `[patch]` The greeting's loading state was invisible: pending and a legitimate zero both rendered nothing, which is the same absence/zero conflation the error state was added to fix, and it is the state on every mount.
  - `[low]` `[patch]` The compact card's Open Claim was the only action without a busy gate, so clicking it mid-completion reset the in-flight mutation's local state and the label reverted from "Completing…" while the write was outstanding.
  - `[low]` `[patch]` `workerName` shipped on every note row and was never rendered — PHI on the wire for no consumer; dropped from the projection.
  - `[low]` `[patch]` Smaller: the note form's `onSubmit` did not check the in-flight flag though its hook's docstring says all three controls must agree; `maxLength` counted UTF-16 units while the server counts code points, cutting emoji off at half the displayed cap; and disabling the textarea on save blurred the caret out of the pinned input.
  - `[low]` `[patch]` The vitest mock ignored `day` entirely, so deleting the parameter from `NotesSubTab` left all 82 diary tests green; the stub now branches on it and both sub-tab suites assert the URL. Two inline fixtures that omitted `upcomingCount` — responses the server cannot produce — were corrected.
  - `[low]` `[patch]` No test sent `?day=` over HTTP, and the route silently ignores unknown query parameters, so dropping `day` from the signature passed every Python and e2e test. Four HTTP tests added, reading `upcomingCount` and `nextCursor` off the body and paging the cursor.
  - `[low]` `[patch]` The e2e "Open Claim drives the queue selection" test clicked the control on a card whose claim was already selected, and the selector early-returns on re-selection — so both assertions were true before the click. It now uses a different claim.
  - `[low]` `[patch]` The e2e greeting-count oracle fetched the unfiltered list (UTC-judged) and compared it to a number judged at the browser's local day, assuming away the exact skew the high-severity patch fixed. Both oracles now send the viewer's day.

### 2026-08-17 — Review pass

- intent_gap: 0
- bad_spec: 0
- patch: 20: (high 3, medium 6, low 11)
- defer: 2: (high 0, medium 0, low 2)
- reject: 0
- addressed_findings:
  - `[high]` `[patch]` `day` was the viewer's local calendar day but `status` and `upcomingCount` were judged against `utc_today()`, so for a Pacific handler after 17:00 every meeting under "Today's Meetings" rendered done, lost its ✓ Done control and was excluded from the greeting's count. `list_meetings` now resolves one clock — the day the caller asked about is the day the page is judged against — pinned by a test that builds the skew from `utc_today() - 1` rather than a frozen constant.
  - `[high]` `[patch]` Saving a note never invalidated `queryKeys.claims.actions`, so the check-in row the note satisfies stayed on screen and the handler could log the same note repeatedly. `useAddDiaryNote` now invalidates it from the response's claim; the e2e test that hid this by reloading first asserts the row disappears **before** any reload.
  - `[high]` `[patch]` The add-note form showed nothing about which claim it would tag, and "Open Claim" — a control this story added inside the same pane — moved the selection under a surviving draft, so a note could be written permanently against a claim it does not describe and silently satisfy that claim's check-in. The claim is now captured on the first keystroke, submitted from the capture rather than the live selection, and named on screen (including when it differs from the case now open).
  - `[medium]` `[patch]` Every refusal collapsed to "Could not save. Try again in a moment." — advice that can never succeed for a 422 or a 404. Now routed through `feedbackFromError`, which also gained a `notFound` branch, closing the item Story 4.1's review left open.
  - `[medium]` `[patch]` `normalise_note_text` refused tabs and carriage returns as "characters that cannot be stored", which is untrue — a note pasted from Excel or an email signature was rejected. `\r` and `\t` are now kept; NUL and the rest of C0/DEL are still refused, and the docstring says which constraint is real.
  - `[medium]` `[patch]` A half-typed note was destroyed by switching to the Meetings sub-tab, because `DiaryTab` unmounts the unselected tab and the draft was local state. Lifted into `DiaryNav`, effect-free.
  - `[medium]` `[patch]` `autoFocus` was gated on a counter that stays non-zero for the provider's life, so after one deep link every later manual selection of Notes yanked focus out of the tablist. Split into a monotonic `noteFocusSession` (the remount key) and a `noteFocusPending` intent that `selectSubTab` lowers; tested through real remounts.
  - `[medium]` `[patch]` `DiaryNoteNotVisible` was answered as `/problems/note-claim-not-found` with the literal text "No claim None in your caseload." — after the row was already committed and audited, inviting a duplicate write into an append-only table. It has its own type and wording now, naming the note and saying explicitly not to write it again.
  - `[medium]` `[patch]` The textarea declared no `maxLength`, so a pasted 2,400-character summary failed with no indication of the limit; the cap and a live character count are now on the control.
  - `[low]` `[patch]` A cursor issued by the day-filtered list could be replayed without `day` and silently paged the whole book from that position. `day` is now part of the cursor and a mismatch is refused, matching what `queue.py` does with its filter and what the route's own 400 description already promised.
  - `[low]` `[patch]` `decode_cursor` accepted a tz-aware `last_time` that would reach a naive column comparison; guarded, mirroring the notes cursor written in the same diff.
  - `[low]` `[patch]` `latest_note_at` was the one optional data input on `generate_actions`, where `None` means "never checked in" — a future caller omitting it would re-open the check-in portfolio-wide with no test failure. Now required.
  - `[low]` `[patch]` `queryKeys.ts` claimed the day key "rolls over at local midnight"; it rolls over on the next render. Comment corrected to describe the code (the timer itself is deferred).
  - `[low]` `[patch]` The caller-scoped note read was justified in four places by "a supervisor opening the same case file still sees this row" — but the workspace route is gated to handlers, so that can never happen. The decision stands on its privacy argument; the false reachability claim is removed.
  - `[low]` `[patch]` Typing a note cleared a meeting's 409 refusal the handler was still reading; note-form clearing is now scoped to note feedback.
  - `[low]` `[patch]` The e2e smoke test asserted absolute counts and a positional `shown[0]` although its own header forbids assuming counts, and migration 0033 can date a seeded meeting to today. Assertions are now state-relative and identity-based.
  - `[low]` `[patch]` The e2e upcoming-count oracle compared a whole-book count against one unpaged page; it now asserts the book fits one page first, as its pytest twin already did.
  - `[low]` `[patch]` Note length was measured pre-strip by Pydantic and post-strip by the command, so a 2,000-character note ending in a newline was refused by the wrong problem type. A `BeforeValidator` makes both bounds measure the same string.
  - `[low]` `[patch]` `upcomingCount` is a whole-book fact delivered on the day-filtered envelope, so a failed day request made the greeting line vanish — indistinguishable from a legitimate zero. It now renders an explicit unknown state.
  - `[low]` `[patch]` The compact card rendered "Open Claim" without checking the callback existed, shipping a dead click the same file hides the button to avoid elsewhere; the gate is tightened and the two tests that asserted the defect were corrected. The duplicated `employer_scope` in `select_latest_note_at` was removed alongside.

## Design Notes

**The diary check-in stays a link, and the note *is* the completion.** `deferred-work.md` asked Story 4.2 to decide whether the diary row gains a completion control. It does not. `ActionCommand` has four members, each naming a specific entity write, and `services/worklist/actions.py` states the rule in its own docstring: *"There is no 'completed actions' store, deliberately — a second place a completion is written down is the first place the two can disagree."* A fifth command whose only job is to record "I said I did it" is exactly that second place, and spec-3-5's Block If says HALT rather than add one. So the completion is entity-backed, as `_diary_check_in`'s docstring already promised: *"when it lands this condition becomes 'no note within seven days' without the row appearing or disappearing from the card in the meantime."* Saving a note makes the row stop firing on its own — the same shape as `osha_log`. One control does both jobs, which is precisely why `"diary"` must be added to `NAVIGABLE_FROM_OVERVIEW`: the render gate is `!enabled || NAVIGABLE_FROM_OVERVIEW.has(target)`, so an enabled row that is not in the set renders **no control at all**. Story 4.1 hit the same trap.

**`DIARY_CHECK_IN_DAYS` is a module constant, not a JDM tunable.** `worklist_actions.jdm.json` carries urgencies; the trigger *logic* has always been Python. Keep the seven days as `Final[int] = 7` in `actions.py` beside the rule it belongs to, and record in `deferred-work.md` that a check-in cadence is arguably an operator tunable (AD-8) if anyone ever wants to retune it. The predicate is a **read** of `diary_note` from `services/worklist` — reads across aggregates are fine; AD-12 governs writes, and this module never writes.

**Why the meetings list grows a `day` filter and an `upcomingCount` — and why that is not a new endpoint.** The story forbids new meeting endpoints and a second writer on `meeting`, and `noDerivation.test.ts` scans `features/diary`, so the browser cannot filter to today, sort by time, or count what is upcoming. Both facts have to arrive already computed. The smallest honest answer is to extend the one existing read: `GET /claims-diary/meetings` takes an optional `day` (ISO date, the *viewer's* local calendar day — the same class of client input as `as_of`), and its envelope grows `upcomingCount`, always whole-book and independent of `day`. The Notes sub-tab then makes **one** request that serves both the summary and the greeting's count. Filtering a page of the unfiltered list instead would be the fifty-row truncation bug the 4.1 review already caught once, in a new place — today's meetings sort *after* every past meeting.

**One ordering rule, one cursor.** Change the meetings order to `(meeting_date, COALESCE(meeting_time,'00:00'), id)` globally rather than giving the day-filtered read its own. Reasons: a day filter reduces it to time-then-id for free, so there is one cursor shape instead of two; same-day meetings in the Meetings sub-tab become time-ordered, which is what a date-sorted diary should have done anyway; and `COALESCE` (never `NULLS FIRST`) keeps the row-value keyset comparison NULL-free — a NULL member makes `tuple_(…) > tuple_(…)` evaluate to NULL and silently drop the row. The cursor gains a third member; old cursors are refused as `/problems/invalid-cursor`, which is already the tested behaviour. Update `test_paging_visits_every_meeting_exactly_once` and any test that constructs a cursor by hand.

**The upcoming rule must not be written twice.** The greeting's count needs the rule in SQL; `meeting_status` has it in Python. Put both in `services/derivations/meeting_horizon.py`, adjacent, with a pytest that asserts they agree across a boundary matrix (yesterday / today / tomorrow × done / not done). Two independent restatements of "upcoming" is how a console starts disagreeing with itself.

**Cache keys, and the one 4.1 policy that must relax.** Add `diaryNotes: { list, writes }` top-level — a note belongs to the handler, not to a claim, so it is not a child of `claims.detail`, and its `writes` key must never be `claims.writes` (that key drives `useClaimWriteInFlight` and would grey out every editable control on the case file for a write that bumps no claim version). Make the day list a sibling under the same prefix — `meetings.day(day) = ["meetings","list",day]` — so `replaceInList`'s invalidation can drop `exact: true` and cover both entries by prefix. Without that relaxation, ✓ Done from the summary updates the day list and leaves the Meetings sub-tab stale. The 409-refetches / 200-does-not policy is unchanged and still correct.

**Focus without an effect.** `features/diary` has no `useEffect` anywhere, because `react-hooks/set-state-in-effect` forbids the cascading shape. Model "focus the add-note input" as a session counter exactly like `schedulerSession`: `requestNotes()` calls `setSubTab("notes")` **first** (the input is unmounted while Meetings shows), then bumps `noteFocusSession`; `NotesSubTab` passes that counter as the React `key` on the input so a fresh mount with `autoFocus` *is* the focus. Do not add a boolean intent flag a consumer clears in an effect.

**Feedback is the live region again, not a toast.** Story 3.4 deferred the toast primitive to the first story owning two surfaces needing one; 4.1 had one (meeting save) and 4.2 has one (note save). 4.3 will have the second and owes the primitive. So mount one `<p role="status" data-testid="notes-status" className="sr-only">` carrying at most one sentence, with inline `role="alert"` refusals at the control, and `clearFeedback()` — resetting the mutation *and* the local refusal — called on every path. `isSuccess` is sticky; a nested ternary, not concatenation.

**`noted_at` is one `timestamptz`, and that is a deliberate divergence from the Excel.** `docs/WC_Feature_Element_Details.xlsx` row 91 names `DiaryNotes.handler, .text, .date, .time, .claim_id`. Story 4.1 made the same collapse for meetings, but kept date and time split *because the modal captures a calendar day*. A note has no user-entered time — `noted_at` is the server clock — so one column is the honest shape. Say so in the model docstring, next to the file header's "Naming is canonical per docs/WC_Feature_Element_Details.xlsx".

**The audit action is `create_diary_note`, not `create`.** The story file writes `(action: create, entity: diary_note)`; every service in this codebase uses verb_entity (`create_meeting`, `mark_osha_logged`). Follow the code.

**ORM hazards carried forward from 4.1.** `expire_on_commit=False`, so read the integers you need into locals before a write; call `db.expire_all()` after `commit()` before re-reading. There is no CAS and no 409 here, so the rollback-expires-everything hazard does not apply — but the post-commit re-read can still raise if scope changed mid-request, and that must be caught rather than answered as a 500 (the 4.1 review found exactly this on the POST route).

**Purge cascade.** `diary_note` is PHI-class and belongs in the Epic 8 cascade, which does not exist yet. Do not invent a per-table deletion path; Story 8.1 owns it and will pick the table up. Keep the DELETE grant so it can.

**Prototype fidelity.** Use the *second* `renderDiary` (`docs/Workers_Comp_Prototype.html` line 2045) — the first is dead code and its empty state differs. Exact strings: `"Good morning"` (`h < 12`) / `"Good afternoon"` (`h < 17`) / `"Good evening"`; `"{greeting}, {firstName} 👋"`; `"Today is <strong>{long date}</strong>."`; `"Active: {claimId} — {name} ({injuryType})"`; `"📅 N upcoming meeting(s) — see below / Meetings tab"`; `"Log notes below · Schedule meetings · Email stakeholders via tabs above."`; `"📅 Today's Meetings (N)"`; `"📅 No meetings scheduled for today."`; `"No notes yet."`; `"Add a diary note for today…"`; `"Save"`; the note header `"{date} · {time}"` and tag `"📎 {claimId}"`. Ignore line 2082's `recalcSLA()`.

**Implementation divergences of record (2026-08-17).** Four decisions the Design
Notes above did not settle, recorded here so the artifact and the code do not
disagree in the file a reviewer opens first. All four are also in
`deferred-work.md`.

1. **`useCompleteMeeting` refetches on 200.** The Cache-keys note says "the
   409-refetches / 200-does-not policy is unchanged and still correct". It is not
   correct once `upcomingCount` is on the envelope: that field is a server-derived
   aggregate over the whole book, a single meeting's response body cannot refresh
   it, and the browser is forbidden from decrementing it. Without the refetch,
   ticking a today meeting done leaves the greeting saying "📅 3 upcoming
   meetings" over a summary showing two — the console disagreeing with itself,
   reached through the cache rather than through a second `>=`. The `refetch`
   parameter is kept, because the reason the two paths differ has not gone away.

2. **`upcoming_predicate` is called from `services/claims/meetings.py`, not from
   the repository.** The Code Map's `count_upcoming_meetings` would have made
   `data/repositories/claims.py` import from `services/`, which no module under
   `data/` does. The repository instead exposes `count_meetings_matching(db, ctx,
   predicate)` and the *service* supplies the derivation's predicate — the
   arrangement `count_claims_matching` already documents ("predicates for what is
   being counted arrive from `services/`"). One rule, one owner, layering intact.

3. **The diary pane opens on Notes.** 4.1's `INITIAL_SUB_TAB` comment reads
   "Meetings is the sub-tab *this story* builds, so it is the one that opens";
   Notes is first in the strip, carries the greeting UX-DR9 names this story the
   bearer of, and is what the prototype shows on arrival. The cost is that a
   reload returns there — sub-tab selection is local UI state and not in the URL —
   which cost `4-1-…spec.ts` one added click after its reload.

4. **The check-in window is exclusive and caller-scoped.** "No note within
   `DIARY_CHECK_IN_DAYS`" is read as `days_since < 7`, so a Monday note holds the
   row closed for six days and it fires again the following Monday, which is what
   *weekly* means; `tests/test_action_checklist.py` parametrises 0/1/6/7/30 so the
   edge is a decision rather than a drift. And the note the rule looks for is the
   **reader's own**, because counting anybody's note would publish the existence
   of a handler's private working record to whoever else read the claim.
   *(Corrected at review, 2026-08-17: this note previously added "a supervisor
   opening the same case file still sees the row" as the illustration. That
   consequence has no path to a screen — `web/src/App.tsx` gates the workspace
   route to `allow={["handler"]}`, so supervisors and analysts never open a case
   file. The decision stands on the privacy argument; the illustration was
   false. `deferred-work.md` carries the same correction.)*

## Verification

**Commands:**
- `cd lineworker/server && uv run ruff check . && uv run ruff format --check .` -- expected: clean.
- `cd lineworker/server && uv run mypy .` -- expected: clean under strict.
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker uv run pytest` -- expected: all pass including the new DB-backed diary tests (a bare `uv run pytest` silently skips them); count at or above the 1702 baseline.
- `cd lineworker/web && npm run generate:api && git diff --stat src/api/schema.d.ts` -- expected: the regenerated client is committed; CI fails on a stale `schema.d.ts`.
- `cd lineworker/web && npm run lint && npm run typecheck && npm test` -- expected: clean; vitest at or above the 356 baseline; `noDerivation.test.ts` passes with `NotesSubTab.tsx` in the scanned set.
- `cd lineworker/e2e && docker compose -f ../deploy/compose.e2e.yaml up -d --build --wait` -- expected: stack healthy, **rebuilt from patched source** (Story 3.5 discarded a run made against stale images).
- `cd lineworker/e2e && npx playwright test --grep "@story:4-2"` then `npm test` -- expected: the new spec passes, then the full suite passes with no regressions (baseline 128).

**Manual checks (if no CLI):**
- At a viewport ≥1280px the Notes sub-tab shows the greeting, today's meetings and the notes list; below `xl` the pane is attached but hidden, so a diary deep link focuses an invisible input — pin that behaviour with a test rather than assuming it away, as Story 4.1 did for the scheduler.

## Auto Run Result

Status: done

### What was implemented

`diary_note` — the diary aggregate's second slice, owned by `services/claims` — as an append-only,
per-user, optionally claim-tagged table with one audited create command, a caller-scoped
keyset-paginated newest-first list, and no `version` column, because compare-and-swap arbitrates
concurrent writers of a mutable row and a note is written once. On the SPA, the Notes placeholder
Story 4.1 left behind became the real surface: the greeting card, a today's-meetings summary
rendering 4.1's `MeetingCard` in a new compact variant with ✓ Done and Open Claim, the
reverse-chronological note list, and the pinned add-note form.

Two pieces are structurally more than they look. Today's meetings and the upcoming count had to
arrive **already computed**, because `noDerivation.test.ts` scans `features/diary` and the story
forbids new meeting endpoints — so `GET /claims-diary/meetings` grew an optional `day` parameter and
an `upcomingCount` envelope field, and the Notes sub-tab makes one request that serves both the
summary and the greeting. That forced the meetings ordering to
`(meeting_date, COALESCE(meeting_time,'00:00'), id)` globally with a three-member cursor, and put
the upcoming rule in two renderings — Python and SQL — held equal by a boundary-matrix test rather
than by hope. And the check-in completion is **entity-backed**: `_diary_check_in` now reads "in
treatment AND no note on this claim within `DIARY_CHECK_IN_DAYS`", so the deep link Story 3.5
shipped disabled is itself the completion path, and no fifth `ActionCommand` was invented.

That last point was this story's open decision, handed forward by `deferred-work.md`. The row stays
a link. `services/worklist/actions.py` states in its own docstring that a second place a completion
is written down is the first place the two can disagree, and spec-3-5 makes adding an action-state
table a HALT condition — so the note itself closing the row is both the cheaper and the sanctioned
answer.

### Files changed

**Server — new:** `services/claims/notes.py` (the table's only writer) ·
`data/versions/20260817_0034_diary_note.py` (structure; no seed — notes start empty) ·
`tests/test_diary_notes.py`.

**Server — modified:** `data/models/{core,__init__}.py` (the `DiaryNote` model) ·
`data/repositories/claims.py` (the note queries; meetings ordering, `day`,
`count_meetings_matching`, `select_latest_note_at`) · `services/derivations/{meeting_horizon,
__init__}.py` (the SQL twin of the upcoming rule) · `services/claims/meetings.py` (three-member
cursor, `day`, one clock) · `api/routers/diary.py` (the two note routes, `day`, `upcomingCount`) ·
`services/worklist/actions.py` (the seam deleted, the check-in tightened) ·
`tests/{test_meetings,test_action_checklist}.py`.

**Web — new:** `lib/clock.{ts,test.ts}` (greeting and local-day helpers, deliberately outside the
`noDerivation` scan roots) · `api/diaryNotes.ts` · `features/diary/NotesSubTab.{tsx,test.tsx}`.

**Web — modified:** `api/{queryKeys,meetings,schema.d}.ts` · `features/diary/{DiaryNav,DiaryTab,
MeetingCard,MeetingSchedulerDialog}.tsx` + tests · `features/copilot/CopilotPane.tsx` + test ·
`features/claim-detail/{ClaimDetailPane,actions/ActionsCard,useInlineEdits,InlineEditField}.tsx` +
tests · `features/queue/{useSelectedClaim,noDerivation.test}.ts` · `test/api-mock.ts`.

**E2E:** new `stories/4-2-claim-linked-diary-notes.spec.ts` (8 tests, one `@smoke`) · modified
`stories/4-1-meeting-scheduling-management.spec.ts`.

### Review findings

Two independent adversarial reviews (Blind Hunter, Edge Case Hunter) over the full 6,688-line diff.
No intent gaps and no spec defects — the contract held, so no repair loopback ran
(`review_loop_iteration` stays 0). **20 findings patched** (3 high, 6 medium, 11 low), **2
deferred**, **0 rejected**; per-finding detail is in the Review Triage Log above. None of the twenty
was found to be unreal.

The three high ones each broke something a handler would actually hit. A timezone skew between the
client-resolved `day` and the server-resolved `as_of` rendered a Pacific handler's entire afternoon
of meetings as *done*, with no ✓ Done control and no upcoming count — the hazard of putting two
clocks in one envelope, which is exactly what this story did for the first time. Saving a note never
invalidated the action checklist, so the row the note satisfies stayed on screen and invited the
handler to log it again; the e2e test had hidden this by reloading the page before asserting.
And the add-note form named no claim while offering a control that changes the selection underneath
a surviving draft — a note could be committed permanently against a claim it does not describe, in
a table with no edit and no delete, silently satisfying the wrong claim's check-in.

One fix went past its finding on purpose: `feedbackFromError` gained a `notFound` branch rather than
just being imported, because the new `/problems/note-not-readable` is answered *after* the row is
committed, so telling the handler to "try again in a moment" would have manufactured the duplicate
write a sibling finding exists to prevent. That closes an item Story 4.1's review left open.

### Verification

Every gate was run twice — once by the implementation agent, once after the review patches. Final
numbers, all at or above the Story 4.1 baselines:

- `ruff check` + `ruff format --check`: clean, 185 files
- `mypy .` (strict): clean, 185 files
- `pytest` with a live database: **1772 passed** (baseline 1702)
- `eslint`: **0 errors** (10 pre-existing `react-refresh`/`exhaustive-deps` warnings) · `tsc
  --noEmit`: clean
- `vitest`: **415 passed**, 28 files (baseline 356)
- `playwright`: **136/136** against a stack rebuilt `--build` from patched source, migration
  `0034_diary_note` applied (baseline 128)
- `npm run generate:api`: `schema.d.ts` regenerated and stable on re-run, so CI's schema diff holds

### Residual risks

- **The unfiltered meetings list still judges against `utc_today()`**, because it has no `day` to
  judge against. So a Pacific handler's Notes summary and Meetings sub-tab can still disagree about
  a same-day meeting. The boundary is pinned by tests in both directions; closing it means a
  `day`/`asOf` parameter on the unfiltered read, which is a contract change this story did not own.
- **No midnight rollover.** A Notes sub-tab left open across local midnight keeps yesterday's key,
  greeting and summary until something re-renders it. Self-correcting on any click, deferred rather
  than fixed because the honest fix needs an effect in a feature that deliberately has none.
- **Notes cannot be edited, deleted, or re-tagged.** Sharper for a *record* than it was for a
  meeting: the mis-tag hazard is now mitigated (the tag is visible and pinned to the draft) but not
  eliminated, and correcting a note is a compliance question — amendment versus overwrite — rather
  than a CRUD one.
- **A note silently satisfies a claim's weekly check-in for seven days.** That is the design, but it
  means an incorrectly tagged note suppresses a real outstanding action with no signal.
- **`diary_note` is PHI and is in no purge cascade**, because none exists yet. The handles Story 8.1
  needs are in place and the DELETE grant is asserted by a test.
- **The old meetings cursor is refused across this deploy.** A client mid-scroll gets one
  `400 /problems/invalid-cursor` and reloads — tested and correct, but a real one-off event.
- **Same-day meeting ordering changed for every reader**, not just the new summary: the Meetings
  sub-tab now reads down the clock where it used to read by insertion order. Better, and a change
  nobody asked for.
- **The copilot pane is `xl`-only**, so a diary deep link below 1280px focuses an invisible input —
  worse than 4.1's scheduler, which at least portals into view. Pinned by a test; reopening the
  breakpoint belongs to whoever owns the layout.

### Follow-up review pass (2026-08-17)

A second, independent adversarial review was run because the first pass set
`followup_review_recommended: true`. Both reviewers were given the first pass's triage log, told not
to repeat it, and pointed specifically at the first pass's own patches — the least-reviewed code in
the tree, written quickly against a fix-list.

**19 findings patched (2 high, 7 medium, 10 low), 1 deferred, 0 rejected.** No intent gaps and no
spec defects, so `review_loop_iteration` stays 0.

Both high findings vindicate that aim. The claim-tag capture — pass one's answer to a high-severity
mis-attribution — had a null corner: a draft begun before any claim was selected pinned `null`
permanently, so the note was still written untagged while the form insisted "No claim selected" with
a case visibly open. The fix was correct and incomplete, which is harder to see than a plain bug.
The second is a contract violation that had gone unnoticed through planning, implementation and a
full review: this spec's own I/O matrix says a note stays visible to its author when the tagged
claim leaves their scope, and the code dropped it — with a test asserting the drop. The matrix has
exactly one reading, so the code was brought to it, and the privacy argument for the old behaviour
was recorded rather than silently kept.

Two more patches were defects the first pass *introduced*: the one-clock fix made the cursor's
`issued_on` equal the day being asked about, quietly voiding seven-day expiry on every day-filtered
page; and the `notFound` member added to the shared `FieldFeedback` matched any bare 404, changing
the rendered copy and the `data-testid` on every inline-edit surface in the app while only the diary
paths were re-tested.

**Gates after the follow-up:** ruff and mypy clean (185 files) · pytest **1805** (was 1772) ·
vitest **439** / 28 files (was 415) · eslint 0 errors · Playwright `@story:4-2` **9/9**, full suite
**136/136** against a stack rebuilt from patched source.
