---
title: 'Story 4.1 — Meeting Scheduling & Management'
type: 'feature'
created: '2026-08-17'
status: 'done'
baseline_revision: '3d9fea69fba95d34863acdb776e9292caaeca35d'
final_revision: '8948cf00c87b57fd9e2636fb83d4070f1bdfd3c5'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/_bmad-output/implementation-artifacts/4-1-meeting-scheduling-management.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The handler has no persisted place to plan stakeholder touchpoints — the prototype kept meetings in an in-memory store a reload erased, the right pane is still a "copilot arrives in Epic 6" placeholder, and Story 3.5's `meetings` action deep-link ships disabled with nowhere to go.

**Approach:** Create the `meeting` table as the first slice of the diary aggregate owned by `services/claims`, with audited CAS-guarded create/complete/delete commands, a caller-scoped cursor-paginated list, and a registered server-side upcoming/done derivation; on the SPA, turn the existing right-pane `<aside>` into the copilot panel shell hosting a Diary tab whose Meetings sub-tab renders the scheduler modal and the date-sorted list, and enable the meetings deep-link seam end-to-end.

## Boundaries & Constraints

**Always:**
- Every write is a command in `services/claims/meetings.py` that emits an `audit_event` with JSONB before/after diffs in the **same transaction** (AD-4); complete and delete are CAS-guarded on `expected_version` and answer 409 with the fresh meeting.
- Every read and write takes `CallerContext` and applies scope in the repository only (AD-7). A meeting is visible only to its owner (`app_user_id == ctx.user_id`); a claim link is accepted only if the claim passes `employer_scope(ctx)`. Out-of-scope and absent are the same 404 answer.
- Only the `handler` role may create/complete/delete (mirror `EditNotPermitted` from `services/claims/edit.py`).
- Upcoming/done is a **registered derivation** (AD-10) computed server-side and sent on the wire; the SPA renders it and never recomputes it from dates.
- Follow the house command ladder verbatim: role → scope/existence → request validity → version → write → audit → `db.commit()` → `db.expire_all()`.
- Enum values are snake_case in DB and on the wire; labels are UI-owned. JSON is camelCase via `ApiModel`. Errors are RFC 9457 problem+json.
- Meeting notes, location and participants are PHI-class: never write their content to logs — ids and event names only.

**Block If:**
- A required meeting type or participant value falls outside the canonical 10 / 6 sets, or the seed cannot be expressed without free-text `meeting_type` (that is a schema change request, not dev discretion).
- Any handler persona in the 1.2 seed has fewer than two scoped claims, so the "two demo meetings per handler" clause cannot be satisfied deterministically.
- Delivering the story would require real egress (SMTP, ICS, calendar API, notification).

**Never:**
- No calendar/ICS/email egress of any kind, and no modelling of the row as a delivery attempt — a scheduled meeting is a log of intent (spine → Deferred).
- No Notes sub-tab behaviour (4.2), no email composer / `email_log` / `email_template` (4.3), no content under the ⚡ Actions tab (Epic 6) — all three render as explicit disabled/placeholder seams.
- No `timeline_event` rows for meetings (the claim timeline does not surface meetings).
- No toast provider, queue or library — see Design Notes; feedback follows the existing `role="status"` / `role="alert"` precedent. No native `alert()`/`confirm()`.
- No new `hooks/` directory, no react-hook-form, no zod, no `dark:` utilities, no dark palette.
- No client-side membership test for which action targets are navigable — the server owns `enabled`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Create happy path | handler, `POST /api/claims-diary/meetings` with type, date, optional time/location/notes/participants, in-scope `claimId` | 201 with the created meeting incl. server-derived `status`; one `audit_event` (`create_meeting`/`meeting`, `before=null`) in the same txn | No error expected |
| Create without date | body omits `meetingDate` | 422 problem+json, `type=/problems/validation-error`, detail names the field | Inline message at the date field; dialog stays open |
| Create against a claim outside scope | handler A posts `claimId` belonging to handler B's employer | 404 problem+json, `/problems/meeting-claim-not-found`, same wording as absent | Inline refusal; no row, no audit event written |
| Create by non-handler | supervisor or analyst session | 403 problem+json (`EditNotPermitted` mapping) | Control not rendered for those roles; server is the gate |
| List | handler with N own meetings | `{items, nextCursor, total}` sorted by `meeting_date` then `id` (total order), each item carrying `status` | Bad/expired cursor → 400 `/problems/bad-cursor`, never a silent page-one fallback |
| List isolation | handler A requests list | Only meetings whose `app_user_id == A`; claim-linked rows additionally filtered by employer scope | No leakage; absent rows are simply not returned |
| Complete | `PATCH …/meetings/{id}` with matching `expectedVersion` | 200 with fresh meeting, `isDone=true`, `version+1`, `status="done"`; audit with before/after diff | — |
| Complete/delete with stale version | `expectedVersion` ≠ current | 409 problem+json `/problems/stale-write` with extension member `meeting` carrying the fresh entity | Client installs the fresh entity, invalidates the list key, never retries |
| Complete/delete of another user's meeting | id owned by a different `app_user` | 404, identical to absent | Inline refusal |
| Delete | `DELETE …/meetings/{id}?expectedVersion=n` | 204; audit event records the removed row as `before`, `after=null` | — |
| Seeded state | freshly migrated DB | Exactly 2 meetings per handler persona, dated the migration-run date, types `rtw_conference` and `claim_review_supervisor` | Migration raises a sentence if a persona has <2 scoped claims |

</intent-contract>

## Code Map

Server (`lineworker/server/`):
- `data/models/enums.py` -- ADD `MeetingType` (10 snake_case values) and `MeetingParticipant` (6); both `StrEnum`, wired through the local `_enum()` helper.
- `data/models/core.py` + `data/models/__init__.py` -- NEW `Meeting` model; JSONB `participants` (no `ARRAY` column exists anywhere in this codebase), `version`/`created_at` per house convention.
- `data/versions/20260817_0032_meeting.py` -- NEW structure migration, `revision="0032_meeting"`, `down_revision="0031_worklist_action_rules"` (current head), `op.f()` names, explicit enum drops in `downgrade`, `GRANT` to `lineworker_app`.
- `data/versions/20260817_0033_seed_meetings.py` -- NEW seed migration (structure and seed are separate revisions here, per `0026`/`0027`).
- `data/repositories/claims.py` -- ADD `insert_meeting`, `select_meetings_page`, `select_meeting`, `complete_meeting_cas`, `delete_meeting_cas`; all take `ctx` and apply owner + `employer_scope(ctx)` predicates.
- `services/derivations/meeting_horizon.py` + `services/derivations/__init__.py` -- NEW registered derivation `meeting_status`.
- `services/claims/meetings.py` -- NEW; the aggregate's only writer.
- `api/routers/diary.py` + `api/app.py` -- NEW thin router at prefix `/claims-diary` (keeps the 2622-line `claims.py` from growing).
- `services/worklist/actions.py` -- MODIFY `SEAM_REASONS`: drop `ActionTarget.meetings` (now live), keep `diary` with a reason naming Story 4.2 rather than "Epic 4".

Web (`lineworker/web/`):
- `src/features/shell/WorkspaceShell.tsx` -- MODIFY the existing right-pane `<aside aria-label="Copilot" data-testid="copilot-pane">` **in place** (keep label, testid and `hidden … xl:block`); render `<CopilotPane />` inside it and provide the diary-nav context.
- `src/features/copilot/CopilotPane.tsx` -- NEW panel shell: pulse + title, claim-context sub-line from `useSelectedClaimId()`, disabled ⚡ Actions tab with tooltip, active 📓 Diary tab.
- `src/features/diary/DiaryTab.tsx`, `MeetingsSubTab.tsx`, `MeetingSchedulerDialog.tsx`, `MeetingCard.tsx`, `DiaryNav.tsx` -- NEW.
- `src/api/queryKeys.ts` -- ADD a top-level `meetings` group (the list is caller-scoped, not claim-scoped).
- `src/api/meetings.ts` -- NEW query/mutation hooks mirroring `src/api/claims.ts`.
- `src/api/schema.d.ts` -- REGENERATE (CI diffs this file).
- `src/components/ui/{checkbox,textarea,label}.tsx` -- NEW vendored shadcn primitives (currently MISSING), `dark:` utilities stripped.
- `src/features/claim-detail/ActionsCard.tsx`, `ClaimDetailPane.tsx`, `labels.ts` -- MODIFY so a `meetings` action navigates to the right pane.

Tests:
- `lineworker/server/tests/test_meetings.py`, `tests/test_meeting_seed.py` -- NEW.
- Colocated `*.test.tsx` beside each new web component; `src/test/api-mock.ts` -- ADD a `meetings` route **before** the generic `/api/claims/` match.
- `lineworker/e2e/stories/4-1-meeting-scheduling-management.spec.ts` -- NEW, `@story:4-1 @epic:4`, one `@smoke`; `lineworker/e2e/fixtures/seed.ts` -- ADD an independent oracle for the seeded meetings.

## Tasks & Acceptance

**Execution:**
- [x] `data/models/enums.py` -- add `MeetingType`/`MeetingParticipant` -- AC 1, 3; the 10 types and 6 participants are the wire contract.
- [x] `data/models/core.py`, `data/models/__init__.py` -- add the `Meeting` model (`app_user_id` FK owner, nullable `claim_id` FK, `meeting_type`, `meeting_date date NOT NULL`, nullable `meeting_time`, `location`, `notes`, JSONB `participants`, `is_done` default false, `version`, `created_at`) -- AC 3.
- [x] `data/versions/20260817_0032_meeting.py` -- structure migration + grants + enum drops on downgrade -- AC 3.
- [x] `data/versions/20260817_0033_seed_meetings.py` -- seed two demo meetings per handler persona against their two lowest-sorted scoped claim business ids, `meeting_date = CURRENT_DATE` at migration run, mapped to `rtw_conference` and `claim_review_supervisor` with the prototype's fuller labels carried in `notes`; raise a sentence if a persona has <2 claims -- AC 6.
- [x] `data/repositories/claims.py` -- add the five scoped meeting queries; child-table scope via `claim_id.in_(select(Claim.id).where(employer_scope(ctx)))`, CAS writes returning `None`/0 rows on version mismatch, delete uses `RETURNING` so the audit `before` is what was removed -- AC 3, 4.
- [x] `services/derivations/meeting_horizon.py` + `__init__.py` -- register `meeting_status` ("upcoming when the meeting date is on or after the given day and it is not done, else done") -- AC 3.
- [x] `services/claims/meetings.py` -- `create_meeting`, `complete_meeting`, `delete_meeting`, `list_meetings`; module constants for action/entity strings; audit via `services.audit.record` in the same transaction; no timeline emission -- AC 2, 3, 4.
- [x] `api/routers/diary.py`, `api/app.py` -- the four routes with `ApiModel` request/response schemas, camelCase aliases, `{items, nextCursor, total}` envelope, `expectedVersion` query param (`ge=1`) on delete, 409 carrying extension member `meeting`, and the documented `responses=` dicts -- AC 1–4.
- [x] `services/worklist/actions.py` -- enable the `meetings` seam target and re-word the remaining `diary` reason -- AC 5 sibling seam; the SPA must not decide this.
- [x] `lineworker/web` -- vendor `checkbox`, `textarea`, `label` via `npx shadcn@latest add`, then strip every `dark:` utility -- AC 1; these three primitives are missing today.
- [x] `src/api/queryKeys.ts`, `src/api/meetings.ts`, `src/api/schema.d.ts` -- keys, hooks and a regenerated client; mutations install the 409's fresh entity via `problemExtension`, invalidate the list key with `exact: true` and never retry -- AC 3, 4.
- [x] `src/features/copilot/CopilotPane.tsx` + `src/features/shell/WorkspaceShell.tsx` -- panel shell rendered inside the existing aside, disabled Actions tab with tooltip -- AC 1.
- [x] `src/features/diary/DiaryTab.tsx` + `MeetingsSubTab.tsx` -- sub-tab bar with Notes/Emails placeholders naming Stories 4.2/4.3, loading/empty/error states -- AC 1, 3.
- [x] `src/features/diary/MeetingSchedulerDialog.tsx` -- the modal per UX-DR10 with hand-rolled `useState` validation and `feedbackFromError` mapping -- AC 1, 2.
- [x] `src/features/diary/MeetingCard.tsx` -- card rendering + Done/Delete actions + disabled "✉ Email participants" with the 3.5 tooltip pattern -- AC 3, 4, 5.
- [x] `src/features/diary/DiaryNav.tsx`, `src/features/claim-detail/{ActionsCard,ClaimDetailPane,labels}.tsx` -- context so a `meetings` action opens Diary → Meetings → scheduler across panes -- AC 1.
- [x] `lineworker/server/tests/test_meetings.py`, `tests/test_meeting_seed.py` -- cover every I/O Matrix row (422, 403, 404 scope, 409 CAS round-trip, list isolation and total ordering, same-transaction audit for all three commands) plus the seed assertion -- AC all.
- [x] Colocated vitest files + `src/test/api-mock.ts` -- modal inline validation, disabled Actions tab, disabled email button tooltip, 409 rollback -- AC 1, 2, 5.
- [x] `lineworker/e2e/stories/4-1-meeting-scheduling-management.spec.ts` + `fixtures/seed.ts` -- the story gate -- AC all.
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- record the toast decision and any new seams -- housekeeping.

**Acceptance Criteria:**
- Given a handler on the workspace, when the right pane renders, then it shows the copilot shell with a disabled ⚡ Actions tab (tooltip naming Epic 6) and an active 📓 Diary tab whose Notes and Emails sub-tabs state which story delivers them.
- Given the Meetings sub-tab, when the handler opens the scheduler and saves a valid meeting, then the row persists, the list re-renders with it in date order, and success is announced non-blockingly without a native dialog.
- Given a meeting card, when Done or Delete is used, then the change persists with an audit event and the list re-renders; a concurrent change instead surfaces the fresh server state inline.
- Given any meeting card, when the handler reaches the "✉ Email participants" control, then it is rendered disabled and states that the composer arrives in Story 4.3, without becoming actionable in this story.
- Given a claim whose action checklist emits a meetings action, when the handler follows that link, then the Diary tab opens on Meetings with the scheduler ready — the same control that shipped disabled in Story 3.5.
- Given a freshly migrated database, when a handler logs in, then exactly the two seeded demo meetings for that persona are listed.
- Given the full gate, when ruff, mypy, pytest, eslint, tsc, vitest and the tagged Playwright spec run, then all pass against a freshly rebuilt e2e stack.

## Spec Change Log

## Review Triage Log

### 2026-08-17 — Review pass

- intent_gap: 0
- bad_spec: 0
- patch: 20: (high 0, medium 3, low 17)
- defer: 2: (high 0, medium 0, low 2)
- reject: 3: (high 0, medium 0, low 3)
- addressed_findings:
  - `[medium]` `[patch]` The diary truncated silently at fifty rows, oldest-first — `nextCursor` and `total` were published and unread, so a handler past fifty meetings lost the *newest* ones with no count, control or error. `useMeetings` is now a `useInfiniteQuery`; the sub-tab renders `total` and a "Show more" while `hasNextPage`.
  - `[medium]` `[patch]` Seed migration `0033`'s `downgrade()` ran an unconditional `DELETE FROM meeting`, destroying every handler-created row — PHI — and not only the twelve it seeded. Narrowed to the exact `(app_user_id, claim_id, meeting_type, notes)` tuples `upgrade()` builds, via a shared helper.
  - `[medium]` `[patch]` `normalise_participants` silently dropped unknown participants instead of refusing them, contradicting `MeetingParticipant`'s own docstring and leaving `_view` to raise on the next read. Unknown values now raise `InvalidPatch` at the boundary, with a test.
  - `[low]` `[patch]` The scheduler's default date came from `toISOString()` (UTC) in a feature whose card formatter deliberately parses a local wall clock; rebuilt from local calendar parts.
  - `[low]` `[patch]` The read-only claim field rendered a dangling em dash (`WC-20017 — `) while the case file was pending or errored; now three-state, mirroring `CopilotPane`.
  - `[low]` `[patch]` Cancel flipped the `open` prop from outside, so Radix never fired `onOpenChange` and the documented single reset path was skipped; Cancel now routes through it and is disabled while the POST is in flight.
  - `[low]` `[patch]` `test_meetings.py` hard-coded `2026-09-01` and asserted `status == "upcoming"` — a test that would fail on a calendar rather than a regression from 2026-09-02. Moved to a far-future date, matching the e2e and vitest fixtures.
  - `[low]` `[patch]` `test_meeting_seed.py` bounded a Postgres `CURRENT_DATE` with Python's local `date.today()`, reintroducing the UTC-midnight straddle its own docstring claimed to avoid; now compared against the database clock.
  - `[low]` `[patch]` The `noDerivation` guard gained the diary and copilot roots but no rule that could catch a client-side upcoming/done comparison; `status`, `isDone`, `meetingDate` and `meetingTime` added to `DERIVED_FIELDS` and the guard confirmed to fail when such a comparison is written.
  - `[low]` `[patch]` The POST route did not catch `MeetingNotVisible` from its post-commit re-read, so a scope change mid-request would answer 500 after a committed, audited write. (PATCH and DELETE already caught it — that half of the finding was not real.) All three routes are now consistent.
  - `[low]` `[patch]` `components/ui/label.tsx` was vendored, imported nowhere, and retained shadcn's meaningless `"use client"`; deleted.
  - `[low]` `[patch]` `MeetingCard.tsx` held the feature's only non-trivial logic — `formatWhen` — with no colocated test, and both list fixtures carried a non-null time so the all-day and malformed-date branches never ran. Added `MeetingCard.test.tsx` and `DiaryNav.test.tsx`.
  - `[low]` `[patch]` Both new tab strips had accessibility gaps: a focusable `<span tabIndex={0}>` as a direct child of `role="tablist"`, no `aria-controls`, and no `role="tabpanel"` anywhere in the right pane. Brought in line with `DetailTabs`.
  - `[low]` `[patch]` A 422 about `notes`, a 404 or a network failure all marked the *date* input invalid; refusals now carry the field they belong to.
  - `[low]` `[patch]` `replaceInList` was written as the single cache-installer and then duplicated inline with a divergent invalidate policy; reduced to one helper with the policy as a documented parameter.
  - `[low]` `[patch]` The polite live region kept "Meeting marked done." across unrelated paths because sibling mutations were reset asymmetrically; one `clearFeedback()` now resets both on all three paths.
  - `[low]` `[patch]` `MEETING_UNPROCESSABLE_RESPONSE` documented over-length free text as `/problems/invalid-patch`, but Pydantic's `max_length` refuses first with `/problems/validation-error`; docs corrected and both the pytest and the vitest stub re-pointed at the type each actually exercises.
  - `[low]` `[patch]` `_meeting_query()` was documented with Sphinx's `#:` attribute syntax on a `def`; converted to a docstring.
  - `[low]` `[patch]` The I/O Matrix names `/problems/bad-cursor` while the code answers `/problems/invalid-cursor` — the code is right, matching the queue's type and the shared `BAD_CURSOR_RESPONSE` dict since Story 2.1. The matrix is inside the read-only contract, so the correction is recorded in Design Notes instead.
  - `[low]` `[patch]` Two comments asserted that a `meetings` deep link below the `xl` breakpoint "does nothing visible". Verified false: the aside is `display:none` but still mounted and the dialog portals to `document.body`, so the scheduler does open over an invisible diary. Comments corrected and the real behaviour pinned by an e2e test at 1024px.

## Design Notes

**Correction: the bad-cursor problem type is `/problems/invalid-cursor`.** The I/O & Edge-Case Matrix above says a bad or expired cursor answers `/problems/bad-cursor`. It does not, and it should not: `GET /claims-diary/meetings` emits `/problems/invalid-cursor`, which is the type `/claims/queue` has used since Story 1.3 and the one the shared `BAD_CURSOR_RESPONSE` dict in `api/routers/claims.py` documents. **The code is right and the matrix guessed** — a second type for the same refusal would give one console two vocabularies for "that cursor does not describe a position in this list". The matrix is inside the read-only `<intent-contract>` and is therefore left as written; this paragraph is the correction of record, so that the artifact and the code stop disagreeing in the file a reviewer opens first. Everything else the matrix says about that row — 400, never a silent page-one fallback — holds exactly.

**Feedback: live region, not a toast — a documented divergence from Task 5.** The story text says "non-blocking success toast", but there is no toast provider, queue or live-region host anywhere in `web/`, and Story 3.4 deferred building one with the note that the first story owning *two* surfaces should build it. Story 4.1 owns one (meeting save); 4.3 adds email send. So this story follows the shipped precedent — an always-mounted `<p role="status" data-testid="meetings-status" className="sr-only">` carrying at most one sentence, plus inline `<p role="alert" className="text-error">` for refusals — and the toast primitive stays deferred to 4.3. NFR-3 asks for non-blocking feedback, which this satisfies; record the divergence in `deferred-work.md`.

**Why a separate `queryKeys.meetings` group.** `list_meetings` is scoped to the *caller*, not to a claim, so the list is not a child read-model of `claims.detail`. Consequently meeting mutations must **not** carry `queryKeys.claims.writes(claimId)` — that key drives `useClaimWriteInFlight`, which disables claim editing controls, and a meeting write does not bump `claim.version`. Give the group its own `writes` key and state this reasoning in the module docstring.

**Cross-pane deep link.** The meetings action lives in the centre pane (`ClaimDetailPane.navigate`) but its target is the right pane, so lift the intent into a small context in `features/diary/DiaryNav.tsx` provided by `WorkspaceShell` — React context for local UI state, matching the epic's state discipline. Two hazards: the aside is `hidden … xl:block`, so a deep link below 1280px lands on an invisible pane (assert the e2e viewport is ≥ `xl`, and set it explicitly on the spec if the Playwright default is narrower); and `navigate()` currently falls through silently for unknown targets, so add `meetings` to both `NAVIGABLE_FROM_OVERVIEW` and the `navigate` branch.

**Seam label churn.** `SEAM_REASONS` maps `diary` and `meetings` to the same "Available with Diary & Meetings — Epic 4" sentence. Once meetings is live that sentence is wrong for the survivor, so re-word the `diary` entry to name Story 4.2 and update the server test and the `ActionsCard.test.tsx` fixture that assert on it.

**Ordering must be total.** Sort by `(meeting_date, id)`, never `meeting_date` alone — same-day meetings would otherwise page non-deterministically (Story 3.5 learning).

**ORM hazards carried forward.** `expire_on_commit=False`, so read the integers you need into locals before a write; on the 409 path `db.rollback()` expires everything. Call `db.expire_all()` after `commit()` before re-reading. `expectedVersion` has `ge=1` on the wire, so test staleness with `version + 1`, not `version - 1`.

**Purge cascade.** `meeting` is PHI-class and belongs in the Epic 8 purge cascade, which does not exist yet. Do not invent a per-table deletion path here; Story 8.1 owns the cascade and will pick the table up.

## Verification

**Commands:**
- `cd lineworker/server && uv run ruff check . && uv run ruff format --check .` -- expected: clean.
- `cd lineworker/server && uv run mypy .` -- expected: clean under strict.
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker uv run pytest` -- expected: all pass, including the new DB-backed meeting tests (a bare `uv run pytest` silently skips them).
- `cd lineworker/web && npm run generate:api && git diff --stat src/api/schema.d.ts` -- expected: the regenerated client is committed; CI fails on a stale `schema.d.ts`.
- `cd lineworker/web && npm run lint && npm run typecheck && npm test` -- expected: clean, all vitest pass.
- `cd lineworker/e2e && docker compose -f ../deploy/compose.e2e.yaml up -d --build --wait` -- expected: stack healthy, **rebuilt from patched source** (Story 3.5 discarded a run made against stale images).
- `cd lineworker/e2e && npx playwright test --grep "@story:4-1"` then `npm test` -- expected: the new spec passes, then the full suite passes with no regressions.

**Manual checks (if no CLI):**
- At a viewport ≥1280px, the right pane shows the copilot shell; the ⚡ Actions tab is visibly disabled and explains itself on hover; Notes and Emails sub-tabs name their stories rather than rendering empty.

## Auto Run Result

Status: done

### What was implemented

The `meeting` table — the first slice of Epic 4's diary aggregate, owned by `services/claims` — with
audited, CAS-guarded `create` / `complete` / `delete` commands, a caller-scoped keyset-paginated
list, and `meeting_status` registered as a server-side derivation so upcoming/done is never
recomputed in the browser. On the SPA, the right-pane `<aside>` that has read "The AI copilot arrives
in Epic 6" since Story 2.1 became the copilot panel shell: a pulse header with a claim-context
sub-line, a disabled ⚡ Actions tab naming Epic 6, and an active 📓 Diary tab whose Meetings sub-tab
carries the scheduler modal, the date-sorted card list and Notes/Emails placeholders naming Stories
4.2 and 4.3. Story 3.5's `meetings` deep link was enabled end-to-end — one deletion from
`SEAM_REASONS`, exactly as `ActionTarget`'s docstring promised — and a seed migration gives each of
the six handler personas the two demo meetings that close FR-LOGIN-3's seeded-meetings clause.

Two decisions worth surfacing. Feedback is the existing polite live region, **not** a toast: the
story text asked for one, but no toast provider exists in `web/`, Story 3.4 deferred building it to
the first story with two surfaces needing it, and 4.1 has one — so the primitive stays deferred to
4.3 (recorded in Design Notes and `deferred-work.md`). And meetings emit **no** `timeline_event`,
because AD-12 scopes the timeline to claim-mutating commands — which contradicts Story 3.5's
prediction that this story would widen the tag set, so that prediction is corrected in
`deferred-work.md` rather than left to look like missed work.

### Files changed

**Server — new:** `services/claims/meetings.py` (the aggregate's only writer) ·
`services/derivations/meeting_horizon.py` (the upcoming/done rule) · `api/routers/diary.py` (four
thin routes at `/claims-diary`) · `data/versions/20260817_0032_meeting.py` (structure) ·
`20260817_0033_seed_meetings.py` (seed) · `tests/test_meetings.py` · `tests/test_meeting_seed.py`.

**Server — modified:** `data/models/{enums,core,__init__}.py` (the 10 types, 6 participants, the
`Meeting` model) · `data/repositories/claims.py` (five scoped queries) ·
`services/derivations/__init__.py` (registration) · `services/worklist/actions.py` (the seam goes
live) · `api/{app.py,routers/__init__.py}` · `tests/{seed_fixture,test_action_checklist}.py`.

**Web — new:** `api/meetings.ts` · `features/copilot/CopilotPane.tsx` ·
`features/diary/{DiaryNav,DiaryTab,MeetingsSubTab,MeetingSchedulerDialog,MeetingCard,labels}` ·
`components/ui/{checkbox,textarea}.tsx` · six colocated test files.

**Web — modified:** `api/{queryKeys,schema.d}.ts` · `features/shell/WorkspaceShell.tsx` (the aside
extended in place, label and testid kept) · `features/claim-detail/{ClaimDetailPane,actions/
ActionsCard}.tsx` (the deep link) · `features/queue/noDerivation.test.ts` · `test/api-mock.ts`.

**E2E:** new `stories/4-1-meeting-scheduling-management.spec.ts` (five tests, one `@smoke`) ·
modified `fixtures/seed.ts` (independent seed oracle) · `stories/2-2-…spec.ts` (a page-wide
`role="tab"` count re-scoped to the case file's own tablist, since the right pane adds five tabs).

### Review findings

Two independent adversarial reviews (Blind Hunter, Edge Case Hunter) over the full diff. No intent
gaps and no spec defects — the contract held, so no repair loopback ran (`review_loop_iteration`
stays 0). **20 findings patched** (3 medium, 17 low), **2 deferred**, **3 rejected**; the per-finding
detail is in the Review Triage Log above. The three medium ones all had real user consequence: a
diary that silently truncated at fifty rows oldest-first, a seed downgrade that would have deleted
every handler-created meeting, and a participant validator that dropped unknown values rather than
refusing them. One reviewer claim was verified false and left alone (PATCH and DELETE already caught
`MeetingNotVisible`); another was verified *true against the code's own comments* — the scheduler
does open below the `xl` breakpoint over an invisible diary — so the comments were corrected and the
behaviour pinned by a test rather than papered over.

### Verification

Every gate was run twice: once by the implementation agent, once independently after the review
patches. Final numbers, all above the Epic 3 baselines:

- `ruff check` + `ruff format --check`: clean, 182 files
- `mypy .` (strict): clean, 182 files
- `pytest` with a live database: **1702 passed** (baseline 1699)
- `eslint`: **0 errors** (10 pre-existing `react-refresh` warnings) · `tsc --noEmit`: clean
- `vitest`: **356 passed**, 26 files (baseline 339)
- `playwright`: **128/128** against a stack rebuilt `--build` from patched source (baseline 127)
- `npm run generate:api`: `schema.d.ts` regenerated and stable, so CI's schema diff will not fail

### Residual risks

- **The copilot pane is `xl`-only.** Below 1280px the diary is invisible while the scheduler still
  opens over it; a meeting saved there persists correctly but cannot be seen until the window widens.
  Now documented and tested rather than assumed away, but it is a real rough edge for a narrow
  window, and reopening the breakpoint decision belongs to whoever owns the layout.
- **The seed's `meeting_date` is the migration-run date**, so a long-lived demo database drifts into
  showing two past meetings. Intended (the story says so), but it looks like stale data after a week.
- **The two demo meeting titles are not among the ten canonical types.** They are seeded as
  `rtw_conference` and `claim_review_supervisor` with the prototype's fuller labels carried in
  `notes` — a documented mapping, not verbatim fidelity.
- **No `update_meeting`.** Correcting a date means delete-and-recreate, which loses the audit thread;
  deferred with the reasoning in `deferred-work.md`.
