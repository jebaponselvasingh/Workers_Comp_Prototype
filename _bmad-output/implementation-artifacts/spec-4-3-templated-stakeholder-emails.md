---
title: 'Story 4.3 — Templated Stakeholder Emails'
type: 'feature'
created: '2026-08-18'
status: 'done'
baseline_revision: '1c87448ca3c71a96e686188d6485d6469ac28336'
final_revision: 'e5b1ebf7b90e9405da70b733a012f57a30ac9bb8'
review_loop_iteration: 0
followup_review_recommended: true # 32 patches across schema, migration indexes, seed data, API refusals, cache policy and three test suites — one of them a duplicate-write hazard; breadth and volume warrant an independent look
context:
  - '{project-root}/_bmad-output/implementation-artifacts/4-3-templated-stakeholder-emails.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-4-1-meeting-scheduling-management.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-4-2-claim-linked-diary-notes.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The diary's third sub-tab is still a placeholder reading "The stakeholder email composer arrives with templated emails." The prototype kept sends in an `emailsStore` global a reload erased, announced them with `alert()`, and rebuilt six claim-aware letters from JS template literals in the browser; Story 4.1's "✉ Email participants" button ships disabled with nowhere to go. Nothing a handler writes to a stakeholder survives the page.

**Approach:** Add the diary aggregate's last two tables — `email_template` (reference data, six rows seeded from the prototype's `loadEmailTemplate` map) and `email_log` (append-only) — owned by `services/claims`; merge template text against the claim **server-side** from one merge service that reads `days_open` from its registered derivation; serve templates, merges, an audited send-as-log command and a caller-scoped cursor-paginated list from the existing `/claims-diary` router; and on the SPA build the composer modal, the Emails sub-tab, the toast primitive this epic has deferred three times, and flip 4.1's disabled email seam into the convert-to-email path.

## Boundaries & Constraints

**Always:**
- Send is a **log row and nothing else** (spine → Deferred, "Email/calendar egress"). No SMTP client, no outbox, no delivery status, no network egress of any kind; the button says "✉ Send Email (logged)" and means it.
- Template merging happens on the server (AD-1). The SPA renders what the merge endpoint returns and captures edits; it never interpolates a claim field into subject or body.
- `days_open` comes from `services/derivations.days_open` (AD-10), never recomputed in the merge code or the browser.
- Every write is an audited command: `email_log` insert + `audit_event` in one transaction (AD-4), action `send_email`, entity `email_log`.
- Reads and writes resolve the claim under the caller's context and the sent-log list is the caller's own (AD-7). No endpoint accepts caller-supplied scope. Write commands accept `handler` only.
- `email_template` and `email_log` are written only by `services/claims` (AD-12). This story writes nothing to `meeting`, `claim` or `timeline_event`.
- Both tables join the diary aggregate as PHI-class: full CRUD grants to `lineworker_app` so Story 8.1's purge can reach them, values never in structlog lines (AD-11).
- Enums snake_case in DB and on the wire, labels owned by the UI; cursor pagination; problem+json; `sent_at` is `timestamptz` UTC formatted in the browser.
- The story spec `e2e/stories/4-3-templated-stakeholder-emails.spec.ts` (`@story:4-3 @epic:4`, one `@smoke`) must pass against a freshly rebuilt e2e stack before this reaches review (AD-15).

**Block If:**
- The composed stack cannot be rebuilt or the e2e suite cannot run — the AD-15 gate cannot be waived unattended.
- Any requirement here would need an outbound mail/calendar client, a real recipient address, or a delivery-status column. That is the deferred-egress decision and it is not this story's to reopen: HALT rather than model a send as a delivery attempt.
- The `email_log` audit diff question widens beyond this story's narrow answer (see Design Notes) into changing how `notes.py`/`meetings.py` write theirs — that is Story 8.1's call.

**Never:**
- No template administration, editing or deletion surface — templates are seed data written only by migration.
- No email threading, replies, drafts, per-recipient addresses, attachments, or an openable email card (the prototype's `.email-card` has `cursor:pointer` and no handler; do not invent one).
- No edit or delete path for `email_log`; no `version` column and no CAS (append-only, exempt by the write-concurrency convention).
- No `timeline_event` emission, no `ActionTarget.emails` deep link, nothing under the ⚡ Actions tab (Epic 6).
- No auto-filled financial figures: the Settlement template's `$[AMOUNT]` and `[RATING]%` stay literal handler-fill text (AD-2).
- No native `alert()`/`confirm()` anywhere in the flow (NFR-3, UX-DR11).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| List templates | `GET /claims-diary/email-templates`, any authenticated caller | 200, the six rows in seeded order: `templateKey`, `label`, `defaultRecipients` | No error expected |
| Merge a template | `GET /claims-diary/email-templates/{key}/merged?claimId=WC-nnnn`, claim in the caller's book | 200 `{subject, body, recipients, claimId}` — every `{{…}}` resolved, `days_open` from the derivation, `stage`/`status` as prose labels | No error expected |
| Merge, unknown key | `key` not in `email_template` | 404 `/problems/email-template-not-found` | Same 404 for an absent key and a malformed one — no enumeration oracle |
| Merge, claim outside scope | `claimId` real but another handler's employer | 404 `/problems/email-claim-not-found` | Same answer as a claim that does not exist |
| Merge, no claim | `claimId` omitted | 422 `/problems/validation-error` | Templates are claim-aware by definition; the SPA disables the buttons with a reason rather than sending this |
| Meeting draft | `GET /claims-diary/meetings/{id}/email-draft`, meeting in the caller's diary | 200 `{subject, body, recipients, claimId}` — the confirmation letter, recipients = that meeting's participants | No error expected |
| Meeting draft, not mine | meeting id belonging to another handler | 404 `/problems/meeting-not-found` | Identical to "does not exist" |
| Send, happy path | `POST /claims-diary/emails` `{subject, body, priority, recipients[], claimId?, templateKey?}` as handler | 201 with the created log; one `audit_event` `send_email` in the same transaction; `Cache-Control: no-store` | No error expected |
| Send, empty subject | `subject` blank or whitespace | 422 `/problems/invalid-patch`, detail "Subject cannot be empty" | Nothing written, no audit row; SPA renders it inline at the subject field |
| Send, no recipients | `recipients: []` | 422 `/problems/invalid-patch`, detail "Select at least one recipient" | Nothing written, no audit row; inline at the recipient fieldset |
| Send, unknown recipient token | `recipients: ["ceo"]` | 422 `/problems/invalid-patch` | The refusal never echoes the submitted value (AD-11) |
| Send, over-length | subject > 200 or body > 10000 chars | 422 naming the field and the limit, never the value | Wire `max_length` and the command's cap measure the same trimmed string |
| Send, claim outside scope | `claimId` not in the caller's book | 404 `/problems/email-claim-not-found` | INSERT…FROM SELECT matches no row; transaction rolled back before the audit write |
| Send as supervisor | role ≠ handler | 403 `/problems/edit-not-permitted` | Raised before any lookup, so it leaks nothing about the claim |
| Send, unknown template key | `templateKey` not seeded | 404 `/problems/email-template-not-found` | `template_id` is a real FK; a bad key is refused rather than nulled |
| List sends | `GET /claims-diary/emails` | 200 `{items, nextCursor, total}`, `sent_at DESC, id DESC`; `total` on the first page only, `null` on cursor pages | No error expected |
| List, another handler's sends | Sarah's session after Kaya has sent | 200 with none of Kaya's rows | Author scope, not employer scope |
| List, bad cursor | tampered / expired / foreign-shaped cursor | 400 `/problems/invalid-cursor` | Never a silent page one |
| Compose with no claim selected | workspace has no `?claim=` | Template buttons disabled with a stated reason; free compose still sends with `claimId` null | The read-only claim field reads "No claim selected" |

</intent-contract>

## Code Map

Server (`lineworker/server/`):
- `data/models/enums.py` -- ADD `EmailPriority(StrEnum)` = `normal | high | urgent`, prototype rendering order, with the `[Source: docs/Workers_Comp_Prototype.html line 612]` citation. **Do not add a recipient enum** — `MeetingParticipant` (line 361) *is* the shared six-value stakeholder vocabulary this story reuses (see Design Notes).
- `data/models/core.py` + `data/models/__init__.py` -- NEW `EmailLog` and `EmailTemplate`. `EmailTemplate`: `id` Identity PK, `template_key Text` unique, `label Text`, `subject_template Text`, `body_template Text`, `default_recipients JSONB`, no `version` (reference data, migration-written, same shape as `GlossaryTerm`). `EmailLog`: `id` Identity PK, `app_user_id` FK NOT NULL `index=True`, nullable `claim_id` FK `index=True`, nullable `template_id` FK, `subject Text`, `body Text` nullable, `priority` native enum, `recipients JSONB`, `sent_at DateTime(timezone=True)` `index=True` `server_default=func.now()`, **no `version`** — paraphrase `DiaryNote`'s append-only justification. Register both in the import list **and** `__all__`, alphabetically — `Document, EmailLog, EmailTemplate, Employee, Employer`.
- `data/versions/20260818_0035_email_tables.py` -- NEW structure migration, `revision="0035_email_tables"`, `down_revision="0034_diary_note"` (current head). Creates the `email_priority` native enum with its member tuple written out literally, both tables, `op.f()` constraint names, the `uq_email_template_template_key` unique, indexes on `email_log.app_user_id`/`claim_id`/`sent_at`, both `GRANT` lines for each table, and a downgrade that drops indexes → tables → `sa.Enum(name="email_priority").drop(op.get_bind(), checkfirst=True)`.
- `data/versions/20260818_0036_seed_email_templates.py` -- NEW seed migration, `down_revision="0035_email_tables"`. `EMAIL_TEMPLATES: tuple[dict[str, Any], ...]` frozen at module level (0033's constant-in-the-migration pattern; six rows is far too small for a `data/seed/*.json` extractor), `autoload_with=bind` insert, downgrade deleting only `template_key IN (…)`.
- `data/repositories/claims.py` -- ADD under a `# --- Story 4.3: the diary aggregate's emails ---` banner: `email_log_scope(ctx)` (author only — `EmailLog.app_user_id == ctx.user_id`), `_email_log_query()` (outer joins to `Claim`/`Employee` for labelled `claim_business_id`/`worker_name`, plus `EmailTemplate.template_key`), `insert_email_log` (two-branch INSERT…FROM SELECT copying `insert_diary_note`), `select_email_logs_page` (keyset `(sent_at, id)` **descending**), `count_email_logs`, `select_email_templates`, `select_email_template(key)`, and `select_merge_source(db, ctx, claim_business_id)` returning the labelled claim/employee/handler row the merge needs.
- `services/claims/emails.py` -- NEW, beside `meetings.py`/`notes.py`. `SEND_ACTION="send_email"`, `ENTITY="email_log"`, `MAX_SUBJECT_LENGTH: Final[int] = 200`, `MAX_BODY_LENGTH: Final[int] = 10_000`, `SUBJECT_FIELD="subject"`/`BODY_FIELD="body"`/`RECIPIENTS_FIELD="recipients"`, the page-limit and cursor-age constants copied from `notes.py`; `list_email_templates`, `merged_template`, `meeting_email_draft`, `send_email`, `list_email_logs`, `render_template` (the pure merge function), `MERGE_FIELDS`, `STAGE_PROSE`/`STATUS_PROSE`, `Cursor`/`encode_cursor`/`decode_cursor`, `_view`, `_diff`, `EmailTemplateNotFound`, `EmailClaimNotVisible`, `EmailLogNotVisible`, `InvalidCursor`; role refusal and 422 imported from `services.claims.edit`.
- `api/routers/diary.py` -- ADD five routes to the existing router (no new router file): `GET /email-templates`, `GET /email-templates/{templateKey}/merged`, `GET /meetings/{meetingId}/email-draft`, `POST /emails`, `GET /emails`; `ApiModel` schemas, `Field(description=…)`, `max_length` mirroring the command caps, `Cache-Control: no-store` on every route and every problem, `responses=` reusing `UNAUTHENTICATED_RESPONSE`/`FORBIDDEN_RESPONSE`/`BAD_CURSOR_RESPONSE` plus new `EMAIL_NOT_FOUND_RESPONSE`/`EMAIL_UNPROCESSABLE_RESPONSE`. Update the module docstring — it already reserves `email_log` for this story.

Web (`lineworker/web/`):
- `src/components/ui/toast.tsx` -- NEW, the primitive Stories 3.4/4.1/4.2 deferred here. `ToastProvider`, `useToast()`, `ToastHost` — no dependency, **no `useEffect`**: `push()` schedules its own `setTimeout` dismissal from the event handler that called it. `TOAST_DURATION_MS` lives here, outside the `noDerivation` scan roots.
- `src/App.tsx` -- MODIFY: wrap the authenticated shell in `<ToastProvider>` and mount one `<ToastHost>`.
- `src/api/queryKeys.ts` -- ADD top-level `emails: { templates, list, draft(kind, id), writes }`. Never under `claims`, never reusing `claims.writes`.
- `src/api/fieldLimits.ts` -- ADD `EMAIL_SUBJECT_MAX`, `EMAIL_BODY_MAX` mirroring the server caps.
- `src/api/emails.ts` -- NEW: `useEmailTemplates()`, `useEmailLogs()` (`useInfiniteQuery`), `useMergedTemplate()`/`useMeetingEmailDraft()` (enabled-gated queries), `useSendEmail()`, `useEmailWriteInFlight()`, mirroring `src/api/diaryNotes.ts`.
- `src/api/schema.d.ts` -- REGENERATE (`npm run generate:api`; CI diffs this file).
- `src/features/diary/labels.ts` -- ADD `RECIPIENT_LABEL` (the composer's own wording: Employee / Employer HR / Nurse Case Manager / Treating Physician / Supervisor / Attorney, each with the prototype's emoji) and `EMAIL_PRIORITY_LABEL` (Normal / High / Urgent) + `EMAIL_PRIORITY_ORDER`/`EMAIL_PRIORITY_TONE`.
- `src/features/diary/EmailComposerDialog.tsx` -- NEW, modelled line-for-line on `MeetingSchedulerDialog.tsx`: wide Dialog, recipients-first, six template buttons, required subject, ~180px body, read-only steel claim reference, priority select, Cancel + "✉ Send Email (logged)".
- `src/features/diary/EmailsSubTab.tsx` -- NEW: the four-branch list idiom (`isPending` / `isError && data===undefined` / empty / populated + stale strip + count + "Show more"), reverse-chronological cards, and the pinned "＋ Compose Email to Stakeholders" button.
- `src/features/diary/DiaryTab.tsx` -- MODIFY: mount `<EmailsSubTab>`, delete the now-empty `SEAMS` record and its `Exclude<>` type and else-branch, update the docstring.
- `src/features/diary/DiaryNav.tsx` -- MODIFY: add `composer: {open, session, prefill}`, `openComposer(prefill?)`, `closeComposer()`, `requestEmails()`; extend `NO_DIARY_PANE`, the provider and the `useMemo` deps.
- `src/features/diary/MeetingCard.tsx` -- MODIFY: delete `EMAIL_SEAM_REASON` and the whole Tooltip/`aria-describedby`/sr-only seam wrapper; the button becomes `onClick={() => onEmail(meeting)}` `disabled={busy}`, full variant only. ADD the inline "Delete? / Cancel" two-step deferred-work assigns to whoever builds the feedback primitive.
- `src/features/diary/MeetingsSubTab.tsx` -- MODIFY: pass `onEmail` through to the card (calls `openComposer({kind:"meeting", meetingId})`).
- `src/features/queue/noDerivation.test.ts` -- MODIFY: add `api/emails.ts` to `ROOT_FILES`, add `sentAt`/`priority` to `DERIVED_FIELDS`, and assert the scan reaches `features/diary/EmailsSubTab.tsx` and `features/diary/EmailComposerDialog.tsx`.
- `src/index.css` tokens only — no new palette. `.email-card` ports to `bg-steel-soft` + steel border with `text-steel` title; `.sent-badge` to the ok pair; High/Urgent priority to the warn/error pairs.

Tests:
- `server/tests/test_emails.py` -- NEW (pure merge tests unmarked at the top, `@requires_db` DB tests below).
- Colocated `EmailComposerDialog.test.tsx`, `EmailsSubTab.test.tsx`, `toast.test.tsx`; `MeetingCard.test.tsx`, `MeetingsSubTab.test.tsx`, `DiaryTab.test.tsx` -- MODIFY (three assertions currently pin the disabled seam); `NotesSubTab.test.tsx` -- keep the compact-variant "no `meeting-email`" assertion, which is now a real guard rather than a seam echo.
- `src/test/api-mock.ts` -- ADD the `/api/claims-diary/email-templates` and `/api/claims-diary/emails` routes **inside the diary block, before `/api/claims/`**, dispatching `POST` before `GET`.
- `e2e/fixtures/seed.ts` -- ADD `EMAIL_TEMPLATE_KEYS` / `expectedTemplateRecipients` beside Story 4.1's meeting block, as the independent oracle.
- `e2e/stories/4-3-templated-stakeholder-emails.spec.ts` -- NEW, `@story:4-3 @epic:4`, one `@smoke`, file-level `test.use({ viewport: { width: 1440, height: 900 } })`.

## Tasks & Acceptance

**Execution:**
- [x] `data/models/enums.py` -- add `EmailPriority` with the prototype citation; add a docstring note on `MeetingParticipant` recording that it is now the shared stakeholder vocabulary for emails too -- AC 1, 4; one vocabulary, so convert-to-email maps 1:1.
- [x] `data/models/core.py`, `data/models/__init__.py` -- add `EmailTemplate` (reference data, no `version`) and `EmailLog` (append-only, no `version`, append-only justification in the docstring) -- AC 3, 4; the tables the story persists to.
- [x] `data/versions/20260818_0035_email_tables.py` -- structure migration on head `0034_diary_note`, literal enum members, both grants per table, drop-indexes-then-tables-then-enum downgrade -- AC 3, 4; keep full CRUD grants because Story 8.1's purge needs DELETE.
- [x] `data/versions/20260818_0036_seed_email_templates.py` -- seed the six templates: keys `three_point_contact | rtw_offer | ncm_referral | status_update | ime_request | settlement_notice`, labels `3-Point Contact · RTW Offer · NCM Referral · Status Update · IME Request · Settlement Notice`, subject/body ported **verbatim** from `docs/Workers_Comp_Prototype.html` lines 1955–1999 with each `${c.field}`/`${handler}` replaced by its `{{placeholder}}` and every bracketed handler-fill string left literal, and the default recipient sets from the same map -- AC 3; templates are data, not code.
- [x] `data/repositories/claims.py` -- add `email_log_scope`, `_email_log_query`, `insert_email_log`, `select_email_logs_page` (keyset `(sent_at, id)` descending — flip both the comparison and the ordering), `count_email_logs`, `select_email_templates`, `select_email_template`, `select_merge_source` -- AC 3, 4; scope lives here and nowhere else.
- [x] `services/claims/emails.py` -- `render_template` (pure), `merged_template`, `meeting_email_draft`, `send_email` (role → validate → scoped insert → audit → commit → `expire_all` → re-read), `list_email_logs`, `list_email_templates` -- AC 1–5; `_diff` carries the claim's **business** id so Story 8.1's purge can find it.
- [x] `api/routers/diary.py` -- the five routes with `ApiModel` schemas, `Field(description=…)`, `max_length`, `pattern=CLAIM_ID_PATTERN`, `Cache-Control: no-store`, and `responses=` dicts -- AC 1–5.
- [x] `src/components/ui/toast.tsx` + `src/App.tsx` -- the toast primitive and its single host, dismissal scheduled at push time so the feature keeps its no-effect discipline -- AC 4; the debt Stories 3.4, 4.1 and 4.2 each recorded and deferred.
- [x] `src/api/queryKeys.ts`, `src/api/fieldLimits.ts`, `src/api/emails.ts`, `src/api/schema.d.ts` -- keys, caps, hooks and a regenerated client -- AC 1–5.
- [x] `src/features/diary/labels.ts` -- `RECIPIENT_LABEL`, `EMAIL_PRIORITY_LABEL`/`_ORDER`/`_TONE`, each quoting the prototype wording -- AC 1; the UI owns labels, the server owns values.
- [x] `src/features/diary/EmailComposerDialog.tsx` -- the wide modal, template buttons fetching the server merge, inline validation at the offending control, three-state read-only claim field -- AC 1, 2, 3.
- [x] `src/features/diary/EmailsSubTab.tsx` -- the reverse-chronological card list with sent badge, recipients line, snippet, priority accent, empty/loading/error/stale states and the compose button -- AC 4.
- [x] `src/features/diary/DiaryTab.tsx`, `DiaryNav.tsx` -- mount `EmailsSubTab`, delete the seam record entirely, add the composer session state and `requestEmails()` -- AC 4, 5.
- [x] `src/features/diary/MeetingCard.tsx`, `MeetingsSubTab.tsx` -- flip the ✉ seam live with an `onEmail` prop, delete the tooltip wrapper and `EMAIL_SEAM_REASON`, and give Delete the inline two-step -- AC 5.
- [x] `src/features/queue/noDerivation.test.ts` -- add `api/emails.ts` to `ROOT_FILES`, `sentAt`/`priority` to `DERIVED_FIELDS`, and assert the scan reaches both new diary files -- AC all; the policy must cover the code this story adds.
- [x] `server/tests/test_emails.py` -- cover every I/O Matrix row (six-row seed shape, per-template merge with no `{{…}}` surviving, derivation-sourced `days_open`, 422s for subject/recipients/length/unknown token, 403, 404 for scope/template/meeting, list author-isolation, DESC ordering, paging visits every row exactly once, `total` only on page one, invalid cursor, null-claim path, same-transaction audit, **no** timeline event, no-egress grep, DELETE grant for 8.1) -- AC all.
- [x] Colocated vitest + `src/test/api-mock.ts` -- composer inline validation, template pre-fill replacing the recipient set, send → toast → sub-tab switch, empty state, compact card still has no email control, toast auto-dismiss and manual dismiss, meeting delete two-step -- AC 1–5.
- [x] `e2e/fixtures/seed.ts` + `e2e/stories/4-3-templated-stakeholder-emails.spec.ts` -- the story gate -- AC all.
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- record the audit-diff divergence, the `MeetingParticipant` naming debt, the `list_meetings` envelope item this story answers only for its own table, and the two shipped surfaces not migrated to the toast -- housekeeping; three prior entries name 4.3 as the owner and must be answered rather than left open.

**Acceptance Criteria:**
- Given a handler on the Emails sub-tab with no sends yet, when it renders, then it shows the prototype's empty state and the "＋ Compose Email to Stakeholders" button, and opening the composer offers the six recipient checkboxes (Employee pre-checked), the six template buttons, a required subject, a body, the read-only claim reference and a Normal/High/Urgent priority.
- Given the composer with a claim selected, when a template button is used, then the subject and body arrive merged from the server with no `{{…}}` left in them and the recipient checkboxes are set to exactly that template's default set; when no claim is selected, then the template buttons are disabled and say why, and free composition still works.
- Given the composer, when Send is pressed with an empty subject or with no recipient checked, then an inline refusal appears at that control, no native dialog is shown, and nothing is written.
- Given a valid composition, when Send is pressed, then an `email_log` row and its `send_email` audit event are written in one transaction, the modal closes, a non-blocking toast confirms it, and the Diary pane switches to Emails where the send is listed first with its subject, sent badge, recipients line and body snippet.
- Given a meeting card in the Meetings sub-tab, when "✉ Email participants" is used, then it is enabled, and the composer opens pre-filled with the server-merged meeting-confirmation subject and body and that meeting's participants checked; the compact card in today's-meetings summary still carries no email control.
- Given two handlers whose employer scopes overlap, when each lists their sends, then neither sees the other's rows, and a supervisor's send is refused with 403 while their list is empty rather than their handlers'.
- Given a page reload, when the handler returns to the Emails sub-tab, then previously logged emails are still listed — the point of FR-DIARY-3.
- Given the full gate, when ruff, mypy, pytest, eslint, tsc, vitest and the tagged Playwright spec run, then all pass against a freshly rebuilt e2e stack.

## Spec Change Log

## Review Triage Log

### 2026-08-18 — Review pass

- intent_gap: 0
- bad_spec: 0
- patch: 32: (high 1, medium 8, low 23)
- defer: 2: (high 0, medium 0, low 2)
- reject: 3: (high 0, medium 0, low 3)
- addressed_findings:
  - `[high]` `[patch]` `READABLE_NOT_FOUND` never gained this story's three 404 problem types, so the composer rendered "Could not save. Try again in a moment." over a server refusal that said the row **was** written and audited and must not be re-sent — a handler following the message they were shown would duplicate a row in an append-only table with no edit and no delete. All three types registered; the composer now treats `/problems/email-not-readable` as a completed send (toast, close, switch to ✉ Emails), which is what the row actually is. Two composer 404 tests and an `EMAIL_WRITTEN_NOT_READABLE` fixture added — the modal previously had no 404 coverage at all.
  - `[medium]` `[patch]` Send was disabled by `busy` but not by `merging`, so pressing it inside a merge round trip logged one template's `template_key` against another's body and `claimId`; opening from a meeting card and typing before the draft landed logged the letter against the workspace's claim rather than the meeting's. Send is now gated on both, on the button and in `submit`.
  - `[medium]` `[patch]` `useMergedTemplate` cached merges with `staleTime: Infinity` although the merge reads six columns Story 2.3's inline editor writes plus `days_open`, so an edited claim re-merged from cache and stale text was sent and logged. Staleness set to `0`; the same argument applied to `useMeetingEmailDraft`; the `queryKeys` comment claiming a merge is "a pure function of rows that this story never writes" corrected.
  - `[medium]` `[patch]` A claim-less meeting (the ERD permits one and 4.1's scheduler creates one) drafted a letter with a dangling em dash and an empty `📋 Claim:` line. An untagged variant now drops the clause; both variants share one body builder so they cannot drift.
  - `[medium]` `[patch]` `_meeting_date_time` formatted a stored, audited document with `%A`/`%B`/`%p`, which read `LC_TIME` from the container — verified to produce `"Montag, August 24 at 10:30 "` under `de_DE.UTF-8`, with an empty AM/PM. Replaced with explicit English names and an explicit meridiem, pinned by a locale-sweeping test.
  - `[medium]` `[patch]` Migration 0035's three single-column indexes served none of the only query that exists, and its docstring's justification was false. Replaced with the composite `(app_user_id, sent_at DESC, id DESC)` the keyset read needs, keeping the FK-side `claim_id` index for Story 8.1's purge; `downgrade()` and the ORM's `__table_args__` updated; up/down/up verified clean.
  - `[medium]` `[patch]` `GET /claims-diary/meetings/{id}/email-draft` had zero HTTP coverage — both I/O-matrix rows were exercised only by calling the service directly, leaving the URL, the camelCase body, `Cache-Control: no-store` and the 404 mapping untested. Route-level tests added for the draft (happy path and "not mine"), and for the merge route's `/problems/email-template-not-found` on absent and malformed keys.
  - `[medium]` `[patch]` Four list tests depended on rows written by earlier tests in the same module; two of them (`…supervisor…sees_none_of_it`, `…answers_for_whoever_holds_the_cookie`) passed **vacuously** in isolation because both sides of the comparison were empty. Each now writes its own rows and asserts the list is non-empty before asserting what is absent from it.
  - `[medium]` `[patch]` `_diff`'s docstring and the matching `deferred-work.md` entry claimed the body omission kept the worker's identifying detail out of `audit_event`; five of six seeded subjects interpolate `{{worker_name}}`, so the name and claim id land there anyway. Both corrected to state what the omission actually buys — proportion, not exemption — without changing behaviour.
  - `[low]` `[patch]` `normalise_recipients` reached `MeetingParticipant(value)` without a shape check, so an unhashable member or a non-iterable payload raised `TypeError` and escaped as a 500 rather than the intended 422; `send_email`'s `template_key` had the same gap. Both guarded, matching what `normalise_priority` already did one field over.
  - `[low]` `[patch]` `_email_template_not_found` reflected up to 64 characters of caller-supplied text into the problem detail, contradicting the no-echo rule this story's own `normalise_recipients` states explicitly. Detail is now a fixed sentence; no `pattern` added to the path param, so an absent key and a malformed one still share one answer.
  - `[low]` `[patch]` Seed migration 0036 violated its own documented invariant — `ncm_referral` and `ime_request` were written in the prototype's emphasis order, not the vocabulary's. Both reordered; the seed test now asserts the ordering it always claimed instead of only checking for duplicates.
  - `[low]` `[patch]` The "no `{{…}}` survives a merge" guarantee was narrower than every docstring asserting it: the seed test reused the merge's own lowercase-only pattern, so `{{Claim_Id}}` or `{{ claim id }}` was invisible to both and would ship with braces intact. A brace-pair guard now runs over all eight letters and over rendered output.
  - `[low]` `[patch]` The unknown-recipient and unknown-template refusal tests asserted the status and problem type but never that the submitted token was absent from the response, though their pure-function twins did. Both now assert it.
  - `[low]` `[patch]` `merged_template` put `row.handler_name` into a `dict[str, str]` with no `None` guard while `meeting_email_draft` narrowed the same value; a NULL would have made `re.sub` raise. Made consistent.
  - `[low]` `[patch]` The read-only claim field rendered the live `claimId` prop rather than the value captured at mount, so a navigation behind an open composer degraded the label while the payload was unchanged — contradicting the module's own docstring. Captured id and worker name are now used.
  - `[low]` `[patch]` `aria-invalid` was set on a `<fieldset>`, where it is inert, so the "addressed to nobody" refusal reached assistive technology only through the alert paragraph. Moved onto the six checkboxes, with the attributes now asserted the way the subject's twin asserts them.
  - `[low]` `[patch]` `useEmailTemplates()` fired on every workspace load because the composer is mounted outside the sub-tab switch — and the hook's own comment justified its cache policy on the opposite claim. Gated on the composer being open; comment corrected.
  - `[low]` `[patch]` The template button row had no pending or empty state, leaving the "Quick templates" heading over an empty row against the epic's explicit requirement that every list surface carry loading, empty and error states. Four branches now.
  - `[low]` `[patch]` `MERGE_FAILED_MESSAGE` reported a failed *meeting draft* as a template failure the handler never asked for. A separate draft message added.
  - `[low]` `[patch]` The toast stack was unbounded, so confirmations arriving inside one dismissal window could grow a fixed-position stack past the viewport. Capped at four, oldest dropped — this is a shared primitive later stories will push to.
  - `[low]` `[patch]` `MeetingCard`'s confirm button carried an unreachable "Deleting…" branch (its own handler unmounts the pair in the same commit), and Cancel was disabled by the list-wide `busy` flag, so a command on another card could strand an armed destructive control. Dead branch removed; Cancel always disarms.
  - `[low]` `[patch]` `RECIPIENT_ORDER` duplicated `PARTICIPANT_ORDER` with nothing enforcing they stay equal, though the shared-order argument on both servers' normalisers depends on it. Derived from one source; the labels still legitimately differ in one entry.
  - `[low]` `[patch]` Six test assertions compared the DOM against the exact constant the component maps over — including one titled "the subject and the body declare **the server's** caps" that would pass with either cap set to any value, and a Sent-badge assertion computed by the formatter under test. Replaced with written-out literals and an independent `Intl` restatement; three overclaiming comments corrected.
  - `[low]` `[patch]` The structural writer-scan rglobbed the whole server root including `.venv`, unlike its stated model, and the no-egress scan was a bare substring match that a comment could trip and `import_module("smtplib")` could evade. Both now share an excluded source walker and the egress check is import-shaped, with a test of the scanner itself.
  - `[low]` `[patch]` The days-open/signature test used a claim whose assigned handler *was* the caller, so an implementation signing with the wrong name passed identically; the DESC-ordering test never pinned that its rows shared a `sent_at`, so the `id` tiebreak was never exercised. Both fixed.
  - `[low]` `[patch]` The spec contradicted itself: Design Notes said `clearFeedback()` resets the delete confirmation "on every other path" while divergence 13 and the code say it is deliberately outside that list-wide reset. Design Notes corrected to match the code.
  - `[low]` `[patch]` The e2e seed oracle's comment described the pre-fix seed ordering. Corrected; the oracle already sorted, so nothing behavioural changed.

## Design Notes

**The recipient vocabulary is `MeetingParticipant`, and it is not renamed.** The story asks for "one shared 6-value set with 4.1's participant enum", and `data/models/enums.py` already has exactly that: `employee | employer_hr | ncm | treating_physician | supervisor | attorney`. Reuse it directly in `emails.py` rather than declaring a second enum with the same members — two vocabularies is how convert-to-email starts needing a translation table. The name is wrong for an email and a rename to `Stakeholder` would be the honest fix, but it would touch the enum, `meetings.py`, the router, the generated `schema.d.ts` type name and every test that spells it, inside a story that already ships two tables, five endpoints, a modal, a sub-tab and a new UI primitive. One vocabulary with an imperfect name beats two with good ones; record the rename in `deferred-work.md`. Recipients are stored the way participants are — a plain `JSONB` list of enum *values*, validated in Python (de-duplicate, re-order into enum order, unknown token → `InvalidPatch`), re-decoded in `_view`. There is no `ARRAY` column anywhere in this schema and no CHECK constraint on the JSONB, which would be a second copy of the enum free to drift.

**Merge placeholders are named after the column they read.** `{{claim_id}}` `{{worker_name}}` `{{doi}}` `{{injury_type}}` `{{cause}}` `{{body_part}}` `{{icd}}` `{{days_open}}` `{{stage}}` `{{status}}` `{{handler_name}}` — eleven, matching the eleven interpolations in the prototype's six bodies. `worker_name` is `employee.name` (the prototype's `c.name`), `handler_name` is the caller's `app_user.name`, `days_open` is `derivations.days_open.for_thresholds(…).of(claim.froi_date, as_of)`. `render_template` is a pure function over a `Mapping[str, str]`, so it is testable without a database, and it **fails loudly**: an unknown `{{token}}` in a template raises rather than rendering empty, which is what makes "no `{{…}}` survives a merge" a real assertion instead of a coincidence. The bracketed prompts are not merge fields and must survive byte-for-byte: `[Transitional Duty — to be completed by supervisor]`, `[Please add current activity summary]`, `[Please add next steps]`, `[IME Physician / IME Coordinator]`, `$[AMOUNT]`, `[Compromise & Release / Stipulation]`, `[RATING]%`. AD-2 is explicit that the settlement figures stay handler-filled.

**`stage` and `status` merge as prose, and that is not the UI owning labels.** The convention "UI owns display labels" is about what a *component* renders. A merged email body is not a component — it is a stored document composed on the server by AD-1, and it must not read "Current Stage: treatment". So `emails.py` carries `STAGE_PROSE` and `STATUS_PROSE`, whose strings are copied from `web/src/features/claim-detail/labels.ts` verbatim (`Intake/Investigation/Treatment/Settled`; `Initial/CH Assessment Process/CH Approved/Denied/Settled/Settled — Closed`) with a comment naming that file as the source. A pytest asserts the two maps cover every member of each enum, so a new status cannot silently merge as a raw token.

**Templates are claim-aware, so a template without a claim is refused rather than degraded.** The prototype rendered `${c?c.claimId:''}` and produced letters full of holes. The merge endpoint requires `claimId`; the SPA disables the six buttons with a stated reason when the workspace has no selection, which is the house's disabled-with-tooltip pattern rather than a new one. Free composition — subject and body typed by hand — still sends, and logs with `claim_id` null, which is what the ERD's `CLAIM |o--o{ EMAIL_LOG` optional edge is for.

**The audit diff carries who and what, not the body.** `notes.py` and `meetings.py` copy their free text into `audit_event.after`, and the 4.1/4.2 follow-up review recorded the consequence: purging the row leaves the PHI behind in an append-only table whose disposition is redact-in-place. Story 8.1 owns that decision and this story does not pre-empt it — but it also does not enlarge it. `_diff` writes `claim_id` (business id), `subject`, `recipients`, `priority` and `template_key`, and **omits `body`**. The reason is not squeamishness: `email_log` is append-only and has no delete path, so the after-diff is never needed to reconstruct a row somebody removed, and the body is the largest PHI blob the console stores. What the audit event is for here is the record that a communication went out and to whom. Say so in the `_diff` docstring, next to the divergence from `notes.py`, and record it in `deferred-work.md` so 8.1 harmonises all three rather than discovering the inconsistency.

**`total` is computed on the first page only — the envelope question, answered for this table.** `deferred-work.md` records that `list_meetings` recounts the whole book on every "Show more" page while the SPA reads `total` from `pages[0]` alone, and predicts "Story 4.3's email log will have the same envelope question". It does, and the answer is cheap here because nothing is shipped yet to change: `list_email_logs` issues the `COUNT(*)` only when no cursor was supplied and returns `null` otherwise. The Lists convention already writes `total` as optional (`{items, nextCursor, total?}`), so this is the convention rather than a divergence from it, and `select` reading `pages[0].total` is unaffected. `list_meetings` is deliberately **not** touched — that is a change to shipped, tested behaviour and belongs with whoever next has a reason to open it.

**The toast, at last — and it has no effects.** Three specs have now recorded that the primitive belongs to "the first story with two surfaces needing one", and 4.3 is the last story in the epic. It is built here, in `src/components/ui/toast.tsx`, and it is deliberately small: a provider holding `toasts: Toast[]`, a `push({tone, message})` that appends and schedules `setTimeout(() => dismiss(id), TOAST_DURATION_MS)` **from inside the event handler that called it**, a manual ✕, and one `<ToastHost>` mounted in `App.tsx` rendering `role="status" aria-live="polite"` in a fixed corner stack. No `useEffect` anywhere — `react-hooks/set-state-in-effect` forbids the cascading shape and `features/diary` has stayed effect-free through two stories; scheduling the dismissal at push time keeps that. No dependency is added: `radix-ui`'s Toast would bring focus-management and swipe semantics this does not need, and the house already hand-rolls its live regions. **The two shipped surfaces are not migrated.** `MeetingsSubTab` and `NotesSubTab` keep their `sr-only` `role="status"` regions and inline refusals exactly as they are; rewiring them would churn a dozen passing tests for no behaviour change, and the toast host announces politely on the same channel. Record the migration as follow-up.

**Refusals stay inline; only success toasts.** UX-DR11 asks for inline validation *and* non-blocking confirmations, and they are different channels. An empty subject renders `role="alert"` at the subject field with `aria-invalid` and `aria-describedby`, exactly as `MeetingSchedulerDialog` does for a missing date; a 422 from the server maps through `feedbackFromError` to the same alert. Only the completed send raises a toast — `✓ Email logged to: {recipients}` — which is the prototype's second `alert()` in its proper form. A refusal that disappears after four seconds is a worse control than one that stays at the field the handler must fix.

**Convert-to-email is server-merged too, and it reads `meeting` without writing it.** `GET /claims-diary/meetings/{id}/email-draft` returns the same `{subject, body, recipients, claimId}` shape as a template merge, built from the prototype's `quickEmailMeeting` letter (type, long-form date/time, location or `TBD`, claim, agenda or `See attached claim file.`, confirm-attendance ask, handler sign-off). It goes through the same `render_template` with a meeting-sourced mapping rather than a second string builder in the router. The prototype pre-checked recipients by substring-matching participant *labels* (`toList.some(t => t.toLowerCase().includes(k))`, which is why `Employer HR` matched `employer`); here the meeting's participants **are** the recipient enum members, so the mapping is identity and the fragile match disappears. AD-12: this reads `meeting` through 4.1's scoped query and writes nothing to it.

**The seam flip is a deletion, and three tests are its guard.** `MeetingCard.tsx` currently wraps the button in `TooltipProvider/Tooltip/TooltipTrigger` with a `tabIndex={0}` span, `title`, `aria-describedby` and an sr-only reason, all keyed off `EMAIL_SEAM_REASON`. All of it goes; the button gains `onClick` and `disabled={busy}`. `MeetingsSubTab.test.tsx:102-112` and `MeetingCard.test.tsx:142-143,163` assert the disabled state and must be rewritten to assert the enabled one — and `NotesSubTab.test.tsx:169,173` and `MeetingCard.test.tsx`'s compact case, which assert the compact variant renders **no** `meeting-email`, must be kept: they were written as seam echoes and become real guards that the summary card stays a two-action card.

**Delete gets its two-step here, because this is the story that built the primitive.** `deferred-work.md`: "`MeetingCard`'s Delete fires `useDeleteMeeting` immediately — no confirmation, no undo — and because there is no `update_meeting`, delete-and-recreate is the sanctioned correction path… Whoever builds that primitive (4.3 owes one) should give this control an inline 'Delete? / Cancel' two-step rather than a dialog." It is ten lines in a file this story already opens, on an irreversible PHI row sitting beside the control this story is enabling. Local `useState` on the card (`confirming: boolean`), first click swaps the button for "Delete? / Cancel", and every other control **on that card** lowers it in its own handler. It is deliberately *not* wired into `MeetingsSubTab`'s `clearFeedback()` — see divergence 13 below: that reset is list-wide, and a confirmation it could clear would be one another card's click could silently disarm. No `confirm()`, no dialog, no new primitive.

**ORM and command hazards carried forward.** `expire_on_commit=False`, so read the integers you need into locals before a write and call `db.expire_all()` after `commit()` before re-reading; the post-commit re-read can still fail if scope changed mid-request and must be answered as `/problems/email-not-readable` with a "do not send it again" sentence rather than a 500 — the 4.1 review found exactly this on the POST route, and here the consequence is sharper because there is no delete path for a duplicate. Free text is typed `object` in the command signature so untrusted input is re-validated (AD-16). Validation trims first, then measures, so the wire's `max_length` and the command's cap measure the same string. Refusals name the field and the limit and **never echo the value**.

**Prototype fidelity.** Exact strings: modal title `✉ Email to Stakeholders`; section headings `Select recipients` / `Quick templates`; field labels `Subject` / `Body` / `Claim reference` / `Priority`; placeholders `Subject line…` / `Email body…`; the claim field's `No claim selected` fallback and `WC-nnnn — Worker Name` value; buttons `Cancel` and `✉ Send Email (logged)`; the compose button `＋ Compose Email to Stakeholders`; the empty state `No emails sent yet. Use the button below to compose.`; the card's `✉ {subject}`, `Sent {date}` badge, `To: {recipients}` with ` · {claimName}` when claim-linked, and the 100-character snippet with a trailing `…`. Two prototype behaviours are deliberately **not** ported: `sendEmail` allowed zero recipients (the story requires at least one) and `.email-card` carries `cursor:pointer` with no handler (there is nothing to open).

**Implementation divergences of record (2026-08-18).** Fourteen decisions the Design Notes above did not settle, recorded here so the artifact and the code do not disagree in the file a reviewer opens first. The first, second and thirteenth are also in `deferred-work.md`.

1. **`select_email_templates`/`select_email_template` take a `CallerContext` they immediately `del`.** `tests/test_scoped_repository.py` requires every public function in the repository to take one; template rows are ownerless reference data with nothing to scope. The house's answer elsewhere is a carve-out module taking no context (`glossary.py`, `statutory_forms.py`), which looked worse for two functions consumed by one scoped command. The discard is documented at the call site so it is neither copied nor "fixed" without a decision.
2. **Path parameters are snake_case** — `/email-templates/{template_key}/merged`, `/meetings/{meeting_id}/email-draft` — because the existing router already spells `/meetings/{meeting_id}` and the new draft route sits beside it. The spec wrote the merge path two ways (`{templateKey}` in the Code Map, `{key}` in the matrix); neither is on the wire, but both name the generated client's argument, so the router's own convention won.
3. **`MEETING_TYPE_PROSE` was needed and is not in the Code Map.** The convert-to-email letter's "📅 Type:" line interpolates what the prototype kept as free text; here `meeting_type` is an enum, so the same argument that gives `stage` and `status` prose maps applies — a stored document must not read "Type: rtw_conference". Copied verbatim from `web/src/features/diary/labels.ts::MEETING_TYPE_LABEL`, with a totality test.
4. **The meeting-confirmation letter lives in `emails.py`, not in `email_template`.** `GET /email-templates` must publish exactly the six the button row offers; a seventh seeded row would be a button that does not exist. It still goes through `render_template`, so there is one merge engine and one "no `{{…}}` survives" guarantee, and `MEETING_MERGE_FIELDS` is kept separate with a test asserting no seeded template may use the meeting-only placeholders.
5. **An all-day meeting's draft renders the date with no clock.** The prototype builds `new Date(date + 'T' + null)` and would print the literal string "Invalid Date" into the letter. The card omits the time rather than printing midnight; the draft now matches, pinned by a test.
6. **`handler_name` on the meeting draft comes from the identity read, not from `select_merge_source`.** A meeting may have no claim, and the spec-mandated merge-source read requires one. `merged_template` still takes it from `select_merge_source`'s labelled column as specified.
7. **`UnknownMergeField` is a fifth exception class**, beyond the four the Code Map enumerates. It is deliberately not a refusal and never maps to a 4xx — it fires only when seeded template text names an unknown placeholder, which is a reference-data defect, and raising is what makes "no leftover `{{…}}`" a real assertion rather than a coincidence.
8. **`normalise_priority` gates on `isinstance(raw, str)` before constructing the enum.** `EmailPriority(["high"])` raises `TypeError`, not `ValueError`, and would have escaped the command's refusal path as a 500.
9. **The composer is mounted by `DiaryTab`, not by `EmailsSubTab`.** `DiaryTab` mounts only the selected sub-tab, so a dialog owned by the Emails tab could be opened from a meeting card only by force-switching the pane first. Hanging it off the panel keeps the prototype's page-level modal and its ordering — the jump to ✉ Emails happens *after* a send, not before the compose — which is what makes `requestEmails()` load-bearing rather than a no-op. `openComposer()` therefore does not move the sub-tab, and the toast is pushed in `DiaryTab` for the same reason: the confirmation and the switch are one consequence of one event.
10. **The composer derives its fields rather than seeding a draft.** `MeetingSchedulerDialog` seeds `useState` at mount because everything it needs is a prop; here subject, body and recipients arrive from the network *after* mount, and copying them into state on arrival is exactly the `setState`-in-effect shape this feature is forbidden. So the server merge is the base, the handler's typing is a sparse `edits` record over it, and every field is computed during render. A template click is one handler that installs the new fill and drops the edits — which *is* "a template replaces the recipient set, never unions with it", expressed as data rather than as a sequence of writes.
11. **`formatSentAt` went into `src/lib/clock.ts`**, not into `EmailsSubTab.tsx`. `clock.ts` documents itself as the single home for formatting the reader's local time and is deliberately outside the `noDerivation` scan roots, which is where a date formatter has to live.
12. **The card's `To:` line and the toast use the compact participant labels, not the composer's emoji ones.** `RECIPIENT_LABEL` (👤 Employee, 🏭 Employer HR, …) is the checkbox wording from the prototype's modal and is used only there; the sent-card recipients line and the toast reuse `PARTICIPANT_TAG_LABEL` (Employee · Employer HR · NCM · Physician · Supervisor · Attorney), because the prototype's own `alert()` used bare capitalised tokens and six emoji in a line repeated down a 300px list reads as noise.
13. **The Delete confirmation is card-local and deliberately outside `clearFeedback()`.** Every other control on the card lowers it in its own handler, but `MeetingsSubTab`'s feedback reset cannot reach it: a confirmation that outlived its card, or followed the handler to the next row, would be worse than none.
14. **"No claim selected" is not reachable in the running console by navigation alone**, so the e2e spec reaches it deterministically. `useSelectedClaim` auto-fills `?claim=` the moment the queue answers and no seeded handler has an empty book, so a bare `/workspace` holds no selection for a few milliseconds. The spec's `withNoSelection` helper answers `GET /api/claims/queue` with a 503 problem document — precisely the case that hook's own docstring names ("a slow request cannot leave the URL pointing at nothing and an empty caseload never navigates at all"). Nothing else in the diary pane reads the queue, so the composer under test is the real one; the helper says all of this at length so the next reader does not mistake it for a mock.

## Verification

**Commands:**
- `cd lineworker/server && uv run ruff check . && uv run ruff format --check .` -- expected: clean.
- `cd lineworker/server && uv run mypy .` -- expected: clean under strict.
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker uv run pytest` -- expected: all pass including the new DB-backed email tests (a bare `uv run pytest` silently skips them); count at or above the 1805 baseline.
- `cd lineworker/web && npm run generate:api && git diff --stat src/api/schema.d.ts` -- expected: the regenerated client is committed; CI fails on a stale `schema.d.ts`.
- `cd lineworker/web && npm run lint && npm run typecheck && npm test` -- expected: clean; vitest at or above the 439 baseline; `noDerivation.test.ts` passes with the two new diary files and `api/emails.ts` in the scanned set.
- `cd lineworker/e2e && docker compose -f ../deploy/compose.e2e.yaml up -d --build --wait` -- expected: stack healthy, **rebuilt from patched source** (Story 3.5 discarded a run made against stale images).
- `cd lineworker/e2e && npx playwright test --grep "@story:4-3\b|@smoke\b"` then `npm test` -- expected: the new spec passes, then the full suite passes with no regressions (baseline 135–136; confirm with `npx playwright test --list`).

**Manual checks (if no CLI):**
- At a viewport ≥1280px the Emails sub-tab shows the list and the compose button, and the composer opens over the workspace; below `xl` the pane is attached but hidden — the dialog portals to `document.body`, so unlike 4.2's add-note input the composer *is* visible there. Pin that difference with a test rather than assuming it either way.

## Auto Run Result

Status: done

### What was implemented

Story 4.3 — the diary aggregate's last two tables and the surfaces over them, which closes
Epic 4. `email_template` is reference data seeded with the prototype's six claim-aware letters,
their subject and body text ported verbatim with each JS interpolation converted to a named
`{{placeholder}}` and every bracketed handler-fill prompt left literal. `email_log` is
append-only, written by one audited `send_email` command that is a **log and nothing else** —
no SMTP, no outbox, no delivery status, asserted structurally by an import-shaped scan over the
whole server tree. Merging happens on the server (AD-1) through one `render_template` that
raises rather than rendering a hole, resolving `days_open` from its registered derivation
(AD-10) and rendering `stage`, `status` and `meeting_type` as prose because a stored document
must not read "Current Stage: treatment". Five routes joined the existing `/claims-diary`
router; the SPA got the composer modal, the Emails sub-tab, and the toast primitive Stories
3.4, 4.1 and 4.2 each deferred to whoever owned two feedback surfaces. Story 4.1's disabled
"✉ Email participants" seam is live, server-merged from the meeting, and the Delete control
beside it gained the inline two-step `deferred-work.md` assigned to whoever built the toast.

### Files changed

**Server**
- `data/models/enums.py` — `EmailPriority`; `MeetingParticipant` documented as the shared stakeholder vocabulary emails reuse.
- `data/models/core.py`, `data/models/__init__.py` — `EmailTemplate` and `EmailLog`, both without `version`, registered alphabetically.
- `data/versions/20260818_0035_email_tables.py` — both tables, the `email_priority` enum, the composite `(app_user_id, sent_at DESC, id DESC)` index, full CRUD grants for Story 8.1's purge.
- `data/versions/20260818_0036_seed_email_templates.py` — the six templates, in the vocabulary's own recipient order.
- `data/repositories/claims.py` — the Story 4.3 banner: scope, labelled query, INSERT…FROM SELECT, descending keyset page, counts, template and merge-source reads.
- `services/claims/emails.py` — the merge engine, the send-as-log command, the caller-scoped list, and the pure validators.
- `api/routers/diary.py` — five routes, their schemas, and three problem factories.
- `tests/test_emails.py` — the story's server gate.

**Web**
- `components/ui/toast.tsx` (+ test), `App.tsx` — the primitive and its single host, effect-free and capped.
- `api/emails.ts`, `api/queryKeys.ts`, `api/fieldLimits.ts`, `api/schema.d.ts` — hooks, keys, caps, regenerated client.
- `features/diary/EmailComposerDialog.tsx`, `EmailsSubTab.tsx` (+ tests) — the modal and the sent log.
- `features/diary/DiaryTab.tsx`, `DiaryNav.tsx`, `MeetingCard.tsx`, `MeetingsSubTab.tsx`, `NotesSubTab.tsx`, `labels.ts` — the seam flip, the composer's state, the delete two-step.
- `features/claim-detail/useInlineEdits.ts` — the three email 404s registered as readable refusals.
- `lib/clock.ts`, `test/api-mock.ts`, `features/queue/noDerivation.test.ts` — formatting, stubs, and the derivation policy extended over the new files.

**E2E**
- `stories/4-3-templated-stakeholder-emails.spec.ts` — the AD-15 gate, seven tests, one `@smoke`.
- `stories/4-1-meeting-scheduling-management.spec.ts` — three assertions amended, none deleted: the emails placeholder, the disabled ✉, and the single-click delete.
- `fixtures/seed.ts` — the template oracle.

### Review findings

One review pass, two adversarial reviewers, findings deduplicated and triaged: **0 intent gaps,
0 spec-level defects, 32 patches applied, 2 items deferred, 3 rejected.** The one high-severity
finding was a duplicate-write hazard: the composer discarded the server's "the row was written,
do not send it again" refusal and invited a retry into a table with no edit and no delete. Eight
medium findings covered a send/merge race that could log one template's key against another's
body, cached merges going stale over an edited claim, a malformed letter for a claim-less
meeting, locale-dependent text inside a stored document, an index set that served no query, a
route with no HTTP coverage, two tests that passed vacuously, and a docstring claim about PHI
that was not true. The remainder were refusal hygiene, missing states, dead branches and test
assertions that could not fail. Deferred: the "one transaction" test technique, which cannot
actually distinguish one transaction from two and is shared by three modules; and the identical
`TypeError`-escapes-as-500 shape in Story 4.1's `normalise_participants`.

### Verification

- `ruff check` + `ruff format --check` — clean. `mypy` — clean, 189 source files.
- `pytest` with `MIGRATION_TEST_DATABASE_URL` — **1943 passed** (1805 baseline + 138).
- Migration `upgrade head → downgrade 0034_diary_note → upgrade head` — clean on a fresh schema.
- `npm run generate:api` — the committed client matches the server; `git diff` on the regenerated file is empty.
- `npm run lint` — 0 errors (11 pre-existing `react-refresh` warnings). `npm run typecheck` — clean.
- `npm test` (vitest) — **498 passed**, 32 files (439 baseline + 59).
- E2E stack **rebuilt from patched source** (`up -d --build --wait`, all services healthy, migrations through `0036`); `npx playwright test --grep "@story:4-3"` — 8 passed; full suite `npm test` — **143 passed** (136 baseline + 7), 21 spec files, `tsc --noEmit` clean.

### Residual risks

- **`email_log` has no correction path.** No edit, no delete, and the send is one click from a
  filled composer. The 4.2 review recorded the same gap for `diary_note` and it is sharper here:
  a letter with the wrong claim tag or the wrong recipient set is a permanent record. The
  `/problems/email-not-readable` path is now handled so the one server-side route to a duplicate
  is closed, but a handler who simply presses Send twice still logs twice.
- **The audit event carries PHI it cannot shed.** Omitting `body` keeps the letter itself in the
  one table the purge cascade can reach, but the subject carries the worker's name for five of
  the six templates. Story 8.1 owns the redaction decision for all three diary tables.
- **Sub-tab selection is not in the URL**, so a reload returns to Notes and the Emails list a
  handler was reading is one click away rather than where they left it — 4.2's documented cost,
  unchanged.
- **`sprint-status.yaml` is not updated by this workflow.** It still shows 4-1, 4-2 and 4-3 at
  `ready-for-dev`/`review` although all three specs are `done`; the same drift 4.1 and 4.2 left.
