---
title: 'Story 6.5 — Human-Gated Writes & the RTW Letter'
type: 'feature'
created: '2026-08-20'
baseline_revision: 'bea8ab3131397a2e93f55c0f2eaa02895c9f3712'
status: 'in-review'
review_loop_iteration: 0
followup_review_recommended: true # nineteen findings were patched into the diff, five of them high and all five on the approval path itself — an edit that could rename the pending tool, an edit that could introduce a version pin the handler never saw, a second save that reported success while discarding the letter, two write calls that wedged the thread, and an approval card that vanished on unmount. Four of those five were found independently by both reviewers, which is the signal that the area is dense rather than that the reviewers were thorough. The gate's core is settled; what a second pass should read is the edit-decision guard, the proposal-id handoff and the frontend's interrupt lifecycle across remount and refusal
context:
  - '{project-root}/_bmad-output/implementation-artifacts/6-5-human-gated-writes-the-rtw-letter.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-6-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-6-4-deterministic-quick-actions-qas.md'
  - '{project-root}/docs/Architecture-LINEWORKER.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The copilot can read a claim but cannot change one, and the machinery that would let it do so safely is a set of live-but-inert seams: `PendingApproval` is declared and never populated, `registry.invoke`'s `WriteNotApproved` raise guards a registry with no `kind: write` entry, `Command(resume=…)` is wired to an endpoint nothing interrupts, and the `interrupt` SSE frame carries only a `threadId`. Until they close, AI-proposed writes do not exist — and the RTW letter Story 6.4 drafts into the transcript has nowhere to go.

**Approach:** Register the first `kind: write` tools, install `HumanInTheLoopMiddleware` on the `create_agent` core as the single `interrupt()` producer, and make the resume path execute the drafted call through the entity's existing AD-4 command, compare-and-swapped on the versions recorded at draft time. Both write origins — a model-selected free-chat write and the RTW letter's deterministic save — reach that one gate and produce one identical interrupt shape. Approve, edit and reject each record a content-free audit row; a stale approval discards and re-proposes rather than force-writing.

## Boundaries & Constraints

**Always:**
- Exactly **one** `interrupt()` producer exists — `HumanInTheLoopMiddleware` on the `create_agent` core. Both write origins produce the identical `HITLRequest` payload (`action_requests[{name, args, description}]`, `review_configs[{action_name, allowed_decisions}]`) and resume through the identical `HITLResponse` (`decisions[{type: approve|edit|reject, …}]`). One wire contract, not two (AD-6).
- Write tools are **registered on the model and gated, never hidden** (AD-6, AD-13): the model may *select* one; only the middleware's resolved approval lets it *execute*. `registry.invoke`'s marker-less raise stays defence-in-depth, never a second approval an approved write must separately satisfy.
- Every write executes through an **existing** AD-12 owning-service command. A tool wraps exactly one command, adds no business logic, composes no writes, and holds no session (AD-13).
- Approvals are **version-pinned**: the drafted arguments carry the `expected_version` read at draft time; execution CAS-es on it. A `StaleClaim`/`StaleMeeting` discards the proposal, tells the user the claim changed, and may re-propose from fresh state. Nothing force-writes and nothing merges (AD-4, Write-concurrency convention).
- An `edit` decision may revise only **non-identity** arguments: it may not change the target entity, add or remove targets, or touch any `expected_version`. Enforced server-side against the drafted arguments, not trusted from the client (AD-6).
- Caller scope is re-resolved on every resume; if the re-resolved scope no longer covers the drafted target, the approval fails safe exactly like a stale one (AD-7).
- All three decisions record one content-free `record_copilot_approval` audit row. Reject persists nothing else (checkpointer aside). No proposal body, letter text or drafted field value reaches an audit row or a log line (AD-11).
- At most one write pends per thread. An interrupt-pending thread keeps 409-ing new messages; resume is the only way forward.
- The approval UI renders the **server-supplied pending tool call** — tool name and typed arguments — never the model's prose paraphrase (AD-16).
- Tests are amended into their opposite, never deleted (AD-15).

**Block If:**
- Any write other than the RTW letter's would need a new business command, a new table, or a new column. The letter's is the **one authorized exception** — see *Amendment: the letter gets a column* in Design Notes — and it is bounded to one nullable column, one create command, one repository CAS insert and one viewer variant. Anything beyond that bound is a stop.
- Wiring `BlobStore` — the volume-vs-MinIO backend is an open architecture decision, not this story's to take. The letter is stored as text in its own column precisely so this story does not have to take it.
- A second `DocType` member, or any change to the closed six — `enums.py:570-583` says a seventh member is a change to Story 2.5's surface. `DocType.rtw` already exists; use it.

**Never:**
- New write capabilities, new tables, or any new command or column beyond the authorized `document.body_text` / `create_document` pair.
- A QAS node executing a write directly, or holding a write tool (AD-6). `agents/qas.py::_call` keeps passing `approved_tool_call_ids=frozenset()`.
- A second `interrupt()` call site, a hand-rolled approval transport, or a side-channel write while a thread is paused.
- Routing the AI-insight refresh through the gate — it is `refresh`-class, gate-exempt and audit-bound (AD-13).
- A model call on the RTW **save** path. The handler's edited text is the payload; nothing regenerates it.
- Any figure or date the model originated in the letter (AD-2).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Free-chat write proposed | Model selects a `kind: write` tool | Run pauses; terminal `interrupt` frame carries tool name + typed args + the drafted `expected_version`; `pending_approval` set | No error expected |
| Approve | `{"command":{"resume":{"decisions":[{"type":"approve"}]}}}` from the thread's own user | Registry executes with the marker; AD-4 command CAS-es; confirmation streams; terminal `done`; one audit row | No error expected |
| Reject | `…{"type":"reject"}` | Proposal discarded, `pending_approval` cleared, cancellation message streams, terminal `done`; one audit row; **no entity write** | No error expected |
| Edit | `…{"type":"edit","edited_action":{…}}` revising a non-identity arg | Revised args execute under the same gate; one audit row | Identity or `expected_version` altered ⇒ refuse the decision, discard, audit as rejected |
| Stale approval | Entity version bumped between draft and approve | `StaleClaim`/`StaleMeeting` ⇒ discard, clear `pending_approval`, stream "the claim changed since drafting", optionally re-propose from fresh state; terminal `done`; **no write occurred** | Never force-write, never merge |
| Scope lost on resume | Caller's re-resolved scope no longer covers the target | Same fail-safe as stale; no write | Discarded, audited |
| Foreign user resumes | Another handler in the same employer scope POSTs the resume | 403 problem+json | Out-of-scope or unknown thread stays 404, byte-identical (see Design Notes) |
| Second message while pending | Any message POST on an interrupt-pending thread | 409 `/problems/thread-busy` (already shipped) | Unchanged |
| Injection-shaped unrequested write | Stub scripts a write tool call the user never asked for | Pauses at the same gate; reject leaves the claim untouched; no route, tool-selection or scope change | Marker-less path raises `WriteNotApproved` |
| Marker-less call | A hand-added node invokes a write entry directly | `WriteNotApproved` | Defence in depth |
| RTW draft (unchanged) | `quickAction: "rtw"` | Draft streams as an ordinary assistant message; `pending_approval` stays `None` | Unchanged from 6.4 |
| RTW save proposed | Handler's edited body + the claim `version` read at draft time | No model call; the write tool call is synthesized deterministically and pauses at the same gate, with the same payload shape a free-chat write produces | No error expected |
| RTW save approved | Approve on that proposal | `create_document` inserts one `document` row (`doc_type: rtw`, `body_text` = the handler's text) CAS-ed on the claim's version, appends a `TimelineTag.rtw` timeline event, audits, and the letter appears in the Documents tab | Claim moved ⇒ the stale branch; nothing inserted |
| Saved letter viewed | Open it from Documents | Viewer renders the `letter` sheet variant: the shared header rows plus the body | A document with no `body_text` keeps its existing variant |
| Print / Copy | Modal actions without saving | No run, no proposal, no gate | Nothing to approve |

</intent-contract>

## Code Map

**Server — state and the gate**
- `agents/state.py` -- `PendingApproval` (`:87-113`) is declared and unpopulated; extend it in this module only, per AD-6. It already carries `tool_name`, `tool_call_id`, `arguments`. The drafted `expected_version`s ride **inside `arguments`** (they are tool arguments), so AC 1's "records the version of every entity drafted against" is satisfied by recording the arguments verbatim; add whatever identity/version projection the edit-guard needs beside them, not instead of them. `CopilotState` and `CopilotAgentState` restate channels by name — a channel added to one and forgotten in the other is a mypy error at the writing node, and that is the intended sync mechanism.
- `agents/registry.py` -- `ToolKind.write` exists (`:108-121`); the gate is the first statement of `invoke` (`:526-531`), before argument validation, and that ordering is load-bearing. `build_tools(approved_tool_call_ids=frozenset())` (`:584-652`) binds **every** registry entry to the model, so a write entry becomes LLM-selectable the moment it is registered — which is AD-6's intent. The `StructuredTool` body passes `tool_call_id=None` today; the middleware is what supplies the real id, exactly as `:596-602` predicts. No `args_schema` may name a caller, employer, role or threshold — `tests/test_copilot_graph.py:462` enforces it and a write schema is not exempt. Context-injected dependencies go through `INJECTED` (`:218-221`).
- `agents/approval.py` -- **new**; the thin wrapper AD-6's Deferred entry leaves to this story. Owns: the write-tool `interrupt_on` config, the `pending_approval` lifecycle (set when a write call pends, cleared on every resolution), the approval marker handed to `registry.invoke`, the identity/version guard on an `edit`, the stale/scope fail-safe, and the `record_copilot_approval` call. One module so the gate reads as one thing.
- `agents/graph.py` -- add `HumanInTheLoopMiddleware(interrupt_on={…write tool names…: {"allowed_decisions": ["approve","edit","reject"]}})` plus the approval middleware to `build_graph`'s middleware list (`:302-392`); the module docstring at `:41-46` names its absence as this story's seam. `ToolCallLimitMiddleware` is already there — scope a write limit of one pending write per AD-6. `resume_inputs` (`:277-299`) is inert today and this story raises the first interrupt against it. If an approval destination outside `QUICK_ACTIONS` is needed, `Route` (`:103-112`) widens and the path map gains an explicit entry — both halves are built from `QUICK_ACTIONS` today.
- `agents/tools/claim_write.py` -- **new**; `update_claim_field`, `kind: write`, wrapping exactly one command: `services/claims/edit.py:331 update_claim_fields`. It is the version-carrying update-CAS exemplar and the vehicle for the free-chat write path and AC 6's unrequested-write turn. `update_claim_fields` already calls `mark_claim_stale` (`edit.py:441`); the tool inherits that and adds nothing. Only two write tools ship — see Design Notes for why these two and why diary, meetings and emails are not among them.

**Server — API**
- `api/routers/copilot.py` -- `_terminal_for` (`:1219-1289`) returns `("interrupt", {"threadId": …})`; grow that payload to carry the middleware's `HITLRequest` so AC 5 has something to render. `_run_frames` detects `__interrupt__` in the `updates` branch (`:1388-1391`) and raises `_Interrupted` — the payload must be captured there. `RunRequest` (`:500-573`, `extra="forbid"`) and `_validate_run_body` (`:983-1037`) already accept `command: {resume: …}` and already 422 a `command` without a `resume`. The interrupt-pending 409 already exempts a resume (`:911-912`). The resume path already re-resolves the caller and passes it via `Command(resume=…, update=…)` (`:1341-1343`) — the `update=` is AD-7 and must not be simplified away.
- `api/routers/copilot.py` + `agents/threads.py` + `data/repositories/copilot.py` -- the foreign-resume 403. `select_thread` (`data/repositories/copilot.py:151-158`) puts `user_id` in the SQL `WHERE`, so it cannot tell "foreign" from "absent" and everything is a 404 today. Add a **resume-only** lookup that keeps `employer_scope(ctx)` but drops the `user_id` predicate, and map "row exists, another owner" to 403. See Design Notes for why this does not reopen the enumeration oracle.

**Server — audit**
- `services/audit/__init__.py` -- one function, `record(db, ctx, *, action, entity, entity_id, before, after, at)` (`:38`). It does not commit and takes the actor from `ctx`. Add `record_copilot_approval` as a thin wrapper. `action`/`entity` are `Text` and `before`/`after` nullable JSONB, so **no migration**. Encode the outcome in `action` (`copilot_approval.approved` / `.edited` / `.rejected`, the `ai_insight.generated` style) rather than in `after`: the AD-11 `audit_redactor` role may overwrite `before`/`after`, so an outcome stored there is erasable by the purge cascade. `entity_id` is the claim's business id, per the house convention.

**Server — the letter's persistence (the amendment)**
- `data/versions/20260820_0044_document_body_text.py` -- **new**; one nullable `Text` column, `document.body_text`. Nullable is what keeps the 563 seeded rows valid without a backfill. No enum change: `DocType.rtw` already exists as a native member (`data/models/enums.py:583`) and `TimelineTag` is a `Text` column whose docstring already names "Epic 6's generated letters" as a writer (`enums.py:619-646`), so `TimelineTag.rtw` needs no migration either.
- `data/models/core.py` -- add `body_text: Mapped[str | None]` to `Document` (`:303-313`) and record in the docstring that it is PHI-class (AD-11), the way `DiaryNote.note_text` and `EmailLog.body` do. `blob_key` keeps its meaning — a handle to bytes that still do not exist; `body_text` is a generated letter's text and the two are not alternatives.
- `data/repositories/claims.py` -- `insert_document_cas`, modelled line for line on `insert_additional_injury_cas` (`:1034-1080`): an `INSERT … SELECT` whose source selects the claim under `employer_scope(ctx)` **and** `Claim.version == expected_version`, so scope and version are predicates inside the one statement rather than a read-modify-write with a window in it. Literals typed from the target columns, per that function's second argument (`doc_type` is a native enum).
- `services/claims/documents.py` -- `create_document`, the AD-4 command, shaped exactly like `injuries.py:120 add_additional_injury`: role gate → scoped re-read → version pre-check → CAS insert → `audit.record` + `timeline.append(tag=TimelineTag.rtw)` → commit → `expire_all` → return the re-assembled `ClaimDetail`. **No `mark_claim_stale`** — see Design Notes; the omission is a decision and must be documented in the module the way `comp_rate.py:38-52` documents its own.
- `services/claims/documents.py` -- the `letter` sheet variant. `DocumentSheet` (`:169-190`) gains `body_text: str | None`; `document_content` (`:265-350`) gains a branch beside `FROI_VARIANT`/`SUMMARY_VARIANT` (`:67-68`). **Dispatch on `document.body_text is not None`, not on `doc_type is DocType.rtw`** — a document is rendered as a letter because it carries a letter, and that keeps any future `rtw`-typed row without a body on the summary variant it belongs on.
- `web/src/features/claim-detail/documents/DocumentViewerDialog.tsx` -- render the `letter` variant: the header rows as today, then the body as pre-wrapped text. Plain text, never `dangerouslySetInnerHTML` — the body is handler-authored prose that began as model output, and `Transcript.tsx`'s markdown discipline is the standard it has to meet (AD-16).

**Server — the RTW letter node**
- `agents/qas.py` -- `_build_rtw` (`:753-810`) is draft-only and its docstring says so. Two changes: drop the figures sentence asserting the draft "is not saved to the claim and has not been sent to anybody" once a save exists, and surface the narrated letter body so the modal receives it (`_narrate` at `:285-292` currently consumes the text and returns only a `messages` update). `_call` (`:252-282`) must keep passing an empty marker set — a QAS node still holds no write tool.
- `agents/tools/rtw.py` -- `RtwContext` (`:83-104`) projects `claim_id`, `return_status` and fenced `restrictions`. **No return date exists on any `ClaimDetail` field** (`Claim.rtw_rec`/`actual_rtw` are ORM-only), which is 6.4's recorded deferral; the letter still names no date and the handler types one into the modal. `RtwContext` must also carry the claim's `version`, read at draft time — that is the number the save pins.
- `agents/tools/documents.py` -- **new**; the `kind: write` tool wrapping exactly one command, `services/claims/documents.create_document`. Its `args_schema` subclasses `ClaimArgs` (the registry test asserts every schema does) and adds `name`, `body_text` and `expected_version`. No caller, employer, role or threshold field — `tests/test_copilot_graph.py:462` bans them by name and a write schema is not exempt. `doc_type` is fixed to `DocType.rtw` in the impl, not an argument: the model must not be able to file a letter as a FROI.

**Web**
- `web/src/api/copilot.ts` -- `translate()` (`:355-402`) already maps the `interrupt` frame, but the vendored runtime has **no `interrupt` case**: `@assistant-ui/react-langgraph` 0.14.24 sets its interrupt state only from an `updates` frame carrying `__interrupt__` (`dist/useLangGraphMessages.js:146-150`). Either translate the server frame into `{event:"updates", data:{__interrupt__:[payload]}}` and get `interrupt`/`setInterrupt` free, or register `onCustomEvent`. AD-9: the runtime's protocol wins. `streamRun` (`:260`) is the single argued hand-rolled fetch.
- `web/src/features/copilot/ActionsTab.tsx` -- `useLangGraphMessages` is destructured for `{messages, sendMessage, setMessages}` only (`:350`); `interrupt`/`setInterrupt` are available and unused. Add the interrupt-pending flag to the same disable expression the 6.4 FIFO guard feeds (`:266-271`, `:294`, `:425-441`); a **resume run must not push a quick-action queue entry** or it steals the next run's key. Fold in the three dead token classes while here: `text-er` (`:474`) and `text-wn` (`:551`, `:561`) do not exist — the real tokens are `text-error` / `text-warn`.
- `web/src/features/copilot/ApprovalCard.tsx` -- **new**; the inline proposal card (tool name, typed arguments, Approve / Reject) rendered as a `<li>`-shaped turn. `Transcript.tsx` has no non-text turn slot today. No `@assistant-ui/react` primitive is imported anywhere in `src/` — stay hand-rolled Tailwind, as `ActionsTab.tsx:18-26` argues.
- `web/src/features/copilot/RtwLetterDialog.tsx` -- **new**; the UX-DR10 wide modal. shadcn `Dialog` with `className="max-h-[85vh] overflow-y-auto sm:max-w-3xl"` (the email composer at `features/diary/EmailComposerDialog.tsx:454-459` is the wide precedent), a `DialogTitle` + `DialogDescription`, and ✏ Edit / 🖨 Print / 📋 Copy. Clipboard, print and contenteditable are **greenfield — zero occurrences in `src/`**; prefer a `Textarea` edit toggle over `contentEditable`, matching `EmailComposerDialog.tsx:619-637` and dodging the sanitisation question `Transcript.tsx` is careful about.
- `web/src/features/claim-detail/ClaimDetailPane.tsx` + `features/claim-detail/actions/ActionsCard.tsx` -- the `rtw_letter` `ActionTarget` already exists and is seam-disabled (`labels.ts:309`, `schema.d.ts:1523`). Enabling it is a documented three-part move — the server `SEAM_REASONS` entry, `NAVIGABLE_FROM_OVERVIEW` (`ActionsCard.tsx:110-123`) **and** a `navigate()` branch (`ClaimDetailPane.tsx:139-166`); Stories 4.1 and 4.2 both broke by forgetting the third. The modal then has two openers and needs a shared owner, the `features/diary/DiaryNav.tsx` context being the precedent.
- `web/src/api/schema.d.ts` -- regenerate and commit; CI diffs it.

**Tests**
- `server/tests/test_copilot_graph.py` -- amend `test_pending_approval_is_declared_and_never_populated` (`:251`) and `test_nothing_in_this_story_raises_an_interrupt` (`:955`) into their opposites. The write-gate pair at `:598`/`:629` stays green and gains the real marker producer; its docstring already names the shape this story must produce. `_ToolCallingChatModel` (`:729`) + `_tool_call` (`:806`) is the only stub that can script a tool call — `GenericFakeChatModel` has no `bind_tools`. Per house convention the scripted model is declared local to the module that needs it.
- `server/tests/test_copilot_approval.py` -- **new**: the five round-trips (approve / reject / stale / forged-token / foreign-user) plus `edit`, the identity-and-version guard on `edit`, the scope-lost resume, and the audit assertions.
- `server/tests/test_copilot_threads.py` -- `_interrupting_graph` (`:806`) is a test-local scaffold whose docstring says "Story 6.5 owns the first shipped `interrupt()` producer, and this is not it". Replace it with the real producer and amend the tests at `:854`, `:891`, `:920` rather than duplicating them. The two ownership tests (`:386`, `:411`) stay green — both use a message POST and an out-of-scope thread, neither of which the 403 touches.
- `server/tests/test_prompt_injection_fixtures.py` (`:727-728`, `:856`) and `test_copilot_qas.py` (`:1089`) -- `assert all(entry.kind is ToolKind.read …)` and `len(REGISTRY) == 7` both break by design. Amend into "every write entry is gated and none is reachable without a marker", and extend the injection fixture with the unrequested-write turn (AC 6).
- `server/tests/test_copilot_logging.py` -- extend: no proposal body, letter text or drafted field value in any log line, with a positive control on an approval event name.
- `server/tests/test_model_stub.py` -- cover the stub's new tool-call branch (it loads `deploy/model-stub/app.py` by path — the established precedent).
- `deploy/model-stub/app.py` -- **cannot emit a tool call today**: `_envelope`/`_partial` hard-code `{"role":"assistant","content":…}` and nothing reads the request's `tools`. AD-15 requires the e2e round-trip to cover *both* a free-chat write and the RTW save. Add a request-shape-driven `tools`-aware branch (no control endpoint, no state — the stub's determinism argument). `check_model_ports.py`'s `EXPECTED` table needs no change as long as no service is added or renamed.
- `e2e/stories/6-5-human-gated-writes-the-rtw-letter.spec.ts` -- **new**, `test.describe("@story:6-5 @epic:6 …")`, exactly one `@smoke`. The filename must match `stories/6-5-*.spec.ts` **and** the branch name must contain `6-5`, or CI's grep derivation silently runs the whole suite. Structure follows `6-4-*.spec.ts`; `6-3-*.spec.ts` supplies the two patterns needed here — `page.evaluate` + raw `fetch` for genuine concurrency (`page.request` buffers the whole response), and the forged-id ownership assertion. Assert structure, never prose. Note `@live-ai` does not exist anywhere in this repo; do not invent it.

## Tasks & Acceptance

**Execution:** (dependency order — schema, then services, then tools, then the gate, then the API, then the web, then tests)

- [x] `data/versions/20260820_0044_document_body_text.py` + `data/models/core.py` -- add one nullable `Text` column `document.body_text`, marked PHI-class in the model docstring -- the authorized amendment; nullable so the 563 seeded rows need no backfill, and no enum migration because `DocType.rtw` (`enums.py:583`) and `TimelineTag.rtw` (`enums.py:645`, a `Text` column) both already exist.
- [x] `data/repositories/claims.py` -- `insert_document_cas` as an `INSERT … SELECT` whose source selects the claim under `employer_scope(ctx)` and `Claim.version == expected_version` -- an INSERT has no WHERE, so selecting the claim as the insert's source is what makes the guard and the write one operation rather than a read-modify-write with a window in it; copy `insert_additional_injury_cas` (`:1034-1080`) including its typed literals.
- [x] `services/claims/documents.py` -- `create_document`: role gate → scoped re-read → version pre-check → CAS insert → `audit.record` + `timeline.append(tag=TimelineTag.rtw)` → commit → `expire_all` → return `ClaimDetail` -- the `add_additional_injury` shape (`injuries.py:120-215`) exactly; document the deliberate absence of `mark_claim_stale` in the module the way `comp_rate.py:38-52` does.
- [x] `services/claims/documents.py` -- the `letter` sheet variant: `DocumentSheet` gains `body_text`, `document_content` gains a branch dispatching on `document.body_text is not None` -- dispatching on the body rather than the doc type keeps a body-less `rtw` row on the summary variant it belongs on.
- [x] `services/audit/__init__.py` -- `record_copilot_approval`, outcome encoded in `action` (`copilot_approval.approved` / `.edited` / `.rejected`) -- no migration; `before`/`after` are redactable by the AD-11 `audit_redactor`, so the one fact the row exists to hold must not live there.
- [x] `agents/tools/rtw.py` -- carry the claim's `version` on `RtwContext` -- read at draft time, it is the number the save pins.
- [x] `agents/tools/documents.py` + `agents/tools/claim_write.py` + `agents/registry.py` -- register `save_rtw_letter` (`kind: write`, wrapping `create_document`) and `update_claim_field` (`kind: write`, wrapping `update_claim_fields`) -- exactly two, and why those two is in Design Notes; `doc_type` is fixed in the impl so the model cannot file a letter as a FROI, and no `args_schema` may name a caller, employer, role or threshold (`test_copilot_graph.py:462`).
- [x] `agents/state.py` -- extend `PendingApproval` with whatever the edit-guard and the interrupt payload need beside the drafted `arguments`, in both state classes -- one closed schema in one module is AD-6; the restated channels are the sync mechanism, not duplication.
- [x] `agents/approval.py` -- **new**: the `interrupt_on` config, the `pending_approval` lifecycle, the marker handed to `registry.invoke`, the `edit` identity/version guard, the stale and scope-lost fail-safes, and the audit call -- one module so the gate reads as one thing; AD-6's Deferred entry leaves this mechanism to this story and the round-trip test is what settles it.
- [x] `agents/graph.py` -- install `HumanInTheLoopMiddleware` (decisions `approve|edit|reject`) plus the approval middleware, and scope a one-pending-write limit -- the middleware is the single `interrupt()` producer, which is what makes both write origins one wire contract.
- [x] `agents/approval.py` + `agents/graph.py` -- the RTW save's deterministic handoff: short-circuit the model call when state carries a drafted proposal and emit the write tool call directly -- the handler's edited text is the payload and regenerating it would discard their edit; no eighth quick-action key, because `graph.py:172-174` directs this story to reuse `rtw`.
- [x] `agents/qas.py` -- surface the narrated letter body to the modal and drop the "not saved to the claim" figures sentence -- the modal needs the text; the sentence stops being true once a save exists.
- [x] `api/routers/copilot.py` -- carry the middleware's `HITLRequest` in the terminal `interrupt` frame, and accept the RTW save's proposal on `RunRequest` -- AC 5 needs the server-supplied tool name and typed arguments; the frame carries only `threadId` today and `extra="forbid"` means the client 422s until the field lands.
- [x] `api/routers/copilot.py` + `agents/threads.py` + `data/repositories/copilot.py` -- an employer-scoped, owner-agnostic resume lookup and a 403 for another owner -- AD-6 and AC 2 require 403 where the shipped contract is 404; keeping the employer predicate is what reconciles them.
- [x] `web/src/api/schema.d.ts` -- regenerate and commit -- CI fails on a diff, and the client cannot send the new fields until the server models land.
- [x] `web/src/api/copilot.ts` -- deliver the interrupt to the vendored runtime in the shape it actually reads -- 0.14.24 sets interrupt state only from `__interrupt__` on an `updates` frame; AD-9 says the runtime's protocol wins.
- [x] `web/src/features/copilot/ApprovalCard.tsx` + `Transcript.tsx` + `ActionsTab.tsx` -- the inline proposal card, the interrupt-pending disable, and a resume that bypasses the quick-action queue -- a resume that queues a key steals the next run's key, which is the determinism break 6.4's review fixed; fix the three dead `text-er`/`text-wn` classes while in the file.
- [x] `web/src/features/copilot/RtwLetterDialog.tsx` + the `rtw_letter` `ActionTarget` enablement -- the UX-DR10 wide modal with Edit / Print / Copy and "Save to claim" -- Print and Copy stay gate-free; enabling the action target is the documented three-part move and Stories 4.1 and 4.2 both broke by forgetting the third part.
- [x] `web/src/features/claim-detail/documents/DocumentViewerDialog.tsx` -- render the `letter` variant body as pre-wrapped plain text -- never `dangerouslySetInnerHTML`; the body began as model output (AD-16).
- [x] `deploy/model-stub/app.py` + `server/tests/test_model_stub.py` -- a request-shape-driven `tools`-aware branch -- without it the free-chat write has no e2e coverage, and AD-15 names both write origins.
- [x] `server/tests/test_copilot_approval.py` -- **new**: the round-trips (approve / reject / stale / forged-token / foreign-user / edit), the `edit` identity-and-version guard, the scope-lost resume, and the audit assertions -- the stale case must assert **no write occurred**, which a passing approve test can otherwise satisfy vacuously.
- [x] the module owning `services/claims/documents.py`'s tests -- `create_document`'s own service tests: CAS loss inserts nothing, the audit row is content-free, the timeline event is tagged `rtw`, and the letter variant renders the body -- the command is new business code and needs coverage independent of the graph.
- [x] `test_copilot_graph.py`, `test_copilot_threads.py`, `test_prompt_injection_fixtures.py`, `test_copilot_qas.py`, `test_copilot_logging.py`, `web/.../ActionsTab.test.tsx`, `web/src/test/api-mock.ts` -- amend every assertion this story falsifies -- AD-15: amended into its opposite, never deleted.
- [x] `e2e/stories/6-5-human-gated-writes-the-rtw-letter.spec.ts` -- the story done-gate -- one `@smoke`, structure only; the filename must match `stories/6-5-*.spec.ts` and the branch name must contain `6-5`.

**Acceptance Criteria:**
- Given a write proposed from free chat and the same write proposed from the RTW save, when each pauses, then the two interrupt payloads are the same shape and the same resume body resolves both — asserted by comparing the two frames, not by inspecting one.
- Given an approved write, when it resumes, then the AD-4 command executes CAS-ed on the drafted `expected_version`, the confirmation streams, exactly one terminal event follows, and exactly one content-free approval row exists whose whole document contains no drafted value.
- Given a rejected write, when it resolves, then no entity row changed, `pending_approval` is cleared, a cancellation message streams, and the approval row is the **only** thing persisted.
- Given an entity version bumped between draft and approve, when the approval resumes, then no write occurred, the user is told the claim changed, and any re-proposal carries fresh versions.
- Given an `edit` decision that alters the target entity or an `expected_version`, when it resumes, then it is refused and nothing is written.
- Given a resume from another handler in the same employer scope, when it posts, then 403; given an out-of-scope or unknown thread, then the 404 stays byte-identical to today's.
- Given a stub scripted to call a write tool the user never asked for, when the run executes, then it pauses at the same gate, the route and scope are unchanged, and rejecting it leaves every row untouched.
- Given a write entry invoked without a marker by any path around the middleware, when it runs, then `WriteNotApproved` is raised before argument validation.
- Given the RTW modal, when the handler prints or copies without saving, then no run is posted and nothing is proposed.
- Given the handler edits the letter and saves it, when the proposal is approved, then exactly one `document` row exists carrying **their** edited text — not the model's draft — with `doc_type: rtw`, the claim's Documents list shows it, the timeline carries one `rtw` event, and opening it renders the `letter` variant with the body visible.
- Given the same save, when the claim's version has moved since the draft, then no `document` row is inserted and the stale branch runs — asserted by row count, not by the absence of an error.
- Given a saved letter, when its audit row is read, then the row names the action and the claim and contains no sentence of the letter.
- Given the full CI gate, when it runs, then ruff, mypy strict, pytest (including DB-backed), `alembic check` (no migration in this story, so a proposed operation means an accidental model change), vitest, the `schema.d.ts` diff check, the compose port check and the Playwright suite are all green.

## Spec Change Log

### 2026-08-20 — Amendment: RTW letter persistence

- **Finding that triggered it:** planning halted at step-02 with `intent gaps`. Story 6.5 Task 4 instructs "reuse the Epic 2 documents path; no new table — if the existing `document` shape can't carry a generated letter, stop and raise rather than inventing schema." Verified it cannot, on four counts (no body column, no create command anywhere in `services/`, `BlobStore` unwired with a Deferred backend, no viewer variant). AC 4's save half was unbuildable; every other AC was not.
- **What was amended:** the product owner authorized option (a) — extend `document` with one nullable `body_text` column, add one `create_document` command in the AD-12 owning service, and add one `letter` sheet variant. The spec's `Never` boundary now carves out exactly that pair and nothing wider; `Block If` still stops on any further command, table or column, on wiring `BlobStore`, and on touching the closed `DocType` set.
- **Known-bad state avoided:** the three alternatives that would have shipped without a schema change. Persisting the letter as a diary note or an `email_log` row (options c/d) was buildable today but would have put a claim document in Diary or conflated "drafted a letter" with "communicated with a stakeholder", and both targets are append-only with no version to pin — so AC 1's version-pinned approval would have had nothing to pin on the story's only real write. Wiring `BlobStore` (option b) would have taken the deferred volume-vs-MinIO decision inside a copilot story.
- **KEEP:** the `add_additional_injury` shape for `create_document` — insert a child row under the **parent claim's** `expected_version`, CAS inside an `INSERT … SELECT` so scope and version are predicates in one statement. That is what gives the RTW save a real stale-safe path and it must survive any re-derivation. Also keep the two "already anticipated" findings so nobody re-litigates them: `DocType.rtw` and `TimelineTag.rtw` both exist and need no migration. Also keep the deliberate **absence** of `mark_claim_stale` and its stated reason.

## Review Triage Log

### 2026-08-21 — Review pass

- intent_gap: 0
- bad_spec: 0
- patch: 19: (high 5, medium 10, low 4)
- defer: 3: (high 0, medium 0, low 3)
- reject: 0
- addressed_findings:
  - `[high]` `[patch]` **An `edit` decision could rename the tool, so a different write executed than the one approved.** `_identity_refusal` compared only arguments; the revised call's `name` was never checked against `pending["tool_name"]`, while `awrap_tool_call` resolved the registry entry from the *revised* name. A handler approving a claim-field edit could have filed a letter instead — the exact substitution AD-16's approval-honesty rule exists to prevent, and the one an `edit` makes cheapest. Both reviewers found it independently. The name is now the first comparison, before the identity keys.
  - `[high]` `[patch]` **An `edit` could introduce an identity argument the draft never carried.** `_identity_of` projects only keys present at draft time and the guard skipped keys absent from that projection, so an edit supplying `expected_version` pinned a version no handler saw on the card they approved — the force-write the stale-safe design forbids by name. Identity **key sets** are now compared before any value; adding a key and removing one are the same refusal.
  - `[high]` `[patch]` **A second RTW save on one thread silently no-opped and reported success.** The "already drafted?" check scanned the whole checkpointed history for any `AIMessage` tool call with a matching name, unkeyed to the current proposal and not comparing arguments — so draft → save → approve → draft → save answered "The letter has been filed on this claim" with no proposal, no gate and no row, discarding the handler's second letter. `ProposedWrite` now carries a `proposal_id` minted once per request, and the drafted call is resolved by it. Both reviewers found it independently.
  - `[high]` `[patch]` **Two write calls in one model turn batched into one interrupt and wedged the thread.** One `ToolCallLimitMiddleware` per write tool meant each counted only its own, so a turn calling both passed both; `HumanInTheLoopMiddleware` then emitted two `action_requests` while only the first was recorded, the card refused to render, and the thread 409-ed every message with no reachable resume. The code comment claimed precisely this was prevented. The proposal middleware now walks every write call, promotes the first unanswered valid one and answers the rest with error `ToolMessage`s; the comment was corrected to say the per-tool limiters are only half the cap. Both reviewers found it independently.
  - `[high]` `[patch]` **The approval card was lost whenever `ActionsTab` unmounted** — switching to the Diary tab or reloading discarded the interrupt state, leaving a thread that 409-ed forever with no way to answer. The pause is now published on the transcript response as `interruptPending` / `pendingApproval` and re-seeded on mount.
  - `[medium]` `[patch]` A failed resume (409, 503, dropped stream) cleared the interrupt optimistically and never restored it, making the pending write unanswerable. The interrupt is now held and put back on refusal.
  - `[medium]` `[patch]` Switching conversation or pressing **+ New** while an approval pended could resume the wrong thread or supersede the paused one into a permanent 409. `awaitingApproval` now feeds the switcher's disable expression — and the picker had no `disabled` at all, so it gained one.
  - `[medium]` `[patch]` An interrupt whose payload was absent or unparseable shipped `value: null`, rendering nothing while the thread stayed pending — a dead conversation. The run now ends with an `error` frame the handler can act on.
  - `[medium]` `[patch]` The card rendered arguments that had never been schema-validated, because validation happened after the gate; a handler could approve a payload the system already knew it would refuse. Drafted arguments are now validated before the pause and an invalid proposal never reaches the card.
  - `[medium]` `[patch]` `EMPTY_NARRATION_NOTE` could become the letter body: the `RTW_DRAFT` pin was published before narration ran, so a model returning nothing opened the modal with "the copilot returned nothing" as Save-able text. The pin moved onto the success path.
  - `[medium]` `[patch]` A malformed `resume` value raised inside the graph and surfaced as a 503. It is now validated against a typed model — non-empty `decisions`, a known `type`, `edited_action` present for `edit` — and refused 422 with the pause left intact.
  - `[medium]` `[patch]` A refused save closed the letter modal and lost the handler's edited text. The modal now closes only once the run is accepted.
  - `[medium]` `[patch]` An interrupt could be raised on a call the tool-budget limiter had already answered, and approving it audited as rejected. `interrupt_config()` gained an unanswered-call predicate and the proposal loop skips answered calls.
  - `[medium]` `[patch]` The "Draft RTW Letter →" deep link dead-clicked when the panel was showing Diary. Fixed in two halves — the seen-session ref moved into the provider, and the effect now waits for a thread before claiming, without which the dead click merely moved one layer in.
  - `[medium]` `[patch]` The registry's defence-in-depth id match was tautological: the marker was compared against a set built from the same marker, degenerating to "is the ContextVar set". The tool body now receives the executing call's own id via `InjectedToolCallId`, so the match is real as the docstring claimed.
  - `[low]` `[patch]` `write_outcome` declared seven values where four had producers. Trimmed to the real vocabulary and given a `Literal` that mypy enforces at all four write sites.
  - `[low]` `[patch]` `agents/tools/documents.py` was the only tool importing `data/`, against AD-13. The doc-type constant is now published from the owning service.
  - `[low]` `[patch]` `VARIANT_LABEL` had been widened to `Record<string, string>` with a fallback, losing exhaustiveness; restored so a new sheet variant fails typecheck.
  - `[low]` `[patch]` `deploy/model-stub/app.py` — which carries the branch making the free-chat write path e2e-testable — sat outside every lint, typecheck and CI scope. `deploy/` is now in the ruff scope in CI and the file is formatted.

## Design Notes

**Amendment: the letter gets a column.** Story 6.5 as written says "no new table — if the existing `document` shape can't carry a generated letter, stop and raise". It cannot: `Document` (`data/models/core.py:303-313`) is `id, claim_id, name, doc_type, filed_date, blob_key, reviewed, confirmed, version` with no body column; **no create-document command exists anywhere** (`services/claims/documents.py` is entirely read-only, the only writer is `set_document_review` flipping two booleans, and all 563 rows come from seed migration `20260812_0011`); `BlobStore` is an unwired protocol whose backend is an explicitly Deferred decision; and `DocumentSheet` has two hardcoded variants, so a letter's body would be invisible to the viewer. That raise was made and answered: **the product owner authorized extending `document` — one nullable column, one create command, one viewer variant — as an amendment to the story.** The alternatives and why they lost are recorded in the Spec Change Log above.

The amendment is small because the schema was built expecting it. `DocType.rtw` is already a member of the closed six (`enums.py:583`). `TimelineTag` is deliberately a `Text` column rather than a native enum, and its docstring names the reason in advance — "Epic 6's generated letters" is one of the writers it was widened for (`enums.py:619-646`) — so `TimelineTag.rtw` costs no migration. What is genuinely new is `document.body_text`, nullable, and the command that fills it.

**The letter save has a version to pin after all, and it is the claim's.** `add_additional_injury` (`injuries.py:120-215`) is the exact precedent: a command that inserts a *child* row takes the **parent claim's** `expected_version`, pre-checks it, and CAS-es on it inside an `INSERT … SELECT`. So the RTW save reads the claim's `version` at draft time, carries it in the proposal's arguments, and the approval executes against it — a real stale-safe path for AC 1 and AC 2, not a carve-out. This is why the version-pinning gap below does not touch either write this story actually ships.

**`create_document` must not call `mark_claim_stale`, and the omission is a decision.** `services/rag/claim_text.py:99-150` composes the embeddable claim summary from clinical fields only — injury type, cause, body part, ICD-10, severity, disability, recovery, surgery, sector, stage, status, the two return-to-work dates, and secondary injuries. Filing a letter writes none of them, so the claim is not less similar to its neighbours than it was a moment ago and re-embedding for it would be noise. `services/claims/comp_rate.py:38-52` is the precedent for *deliberately* not wiring the call and for saying so in the module rather than leaving a reader to wonder whether it was forgotten; `create_document` follows it. `update_claim_field`, by contrast, wraps `update_claim_fields`, which already calls it (`edit.py:441`) — the tool inherits that and adds nothing.

**Write tools are bound to the model, and the story's Task 1 says otherwise.** Task 1 asks to "assert none is bound to any model call". AD-6 says the opposite in terms: "the guarantee is enforced by the per-tool middleware, **not** by hiding write tools from the model", and §5.2 repeats it as "write tools are gated, not hidden". The story's own AC 6 settles it — a stub "scripted to attempt a write tool call" is only meaningful if the tool is bound. `build_tools` already binds every registry entry, so the code agrees with the architecture. AC 1's "unreachable by LLM tool selection" is therefore read as *not executable by tool selection alone*, which is exactly what the middleware plus the marker-less raise deliver. Recorded as a deliberate deviation from the story's phrasing, not from its intent.

**Two write tools, and why those two.** AC 1 names five entity classes (claim fields, diary, meetings, emails, documents), but the gate is node-agnostic by construction — it fires on any `kind: write` registry entry, so what the AC governs is *what happens when one is proposed*, not how many exist. This story registers exactly two: `save_rtw_letter` over the new `create_document`, because it is the story's whole subject; and `update_claim_field` over `update_claim_fields` (`edit.py:331`), because it is the version-carrying update-CAS exemplar and the natural vehicle for the free-chat write path and the injection-shaped unrequested write in AC 6. Between them the two exercise both CAS shapes — an UPDATE guarded on the row's own version and an INSERT guarded on its parent's. Diary, meetings and emails are deliberately not registered: they are append-only with no version to pin (see below), so adding them would widen the surface without exercising anything the two above do not. Registering them later changes no wire shape, which is the property that makes deferring them safe.

**Three decisions, not two.** The story's UI text says Approve / Reject. AD-6, the Copilot-stream convention and the Testing convention all say `approve | edit | reject`, and the vendored `HumanInTheLoopMiddleware` refuses an empty `allowed_decisions`. The middleware is configured with all three and `edit` is guarded and graph-tested; the v1 card ships two buttons, because the handler's edit affordance for the write this story centres on is the modal's text area, which revises the payload *before* it is proposed. `respond` exists in the vendor enum and stays unused (AD-6 names it unused in v1).

**Version pinning has nothing to pin on three of the five entity kinds.** AC 1 says the proposal records "the `version` of every entity drafted against". `diary_note` and `email_log` carry no `version` column and `create_meeting` takes no `expected_version` — all three by explicit design, argued in their own modules (`notes.py:15-18`, `emails.py:40-43`, `meetings.py:29-31`): they are append-only, so there is no read-modify-write to lose. Pinning them would mean either a new column or a re-read-and-compare inside a tool, and AD-13 forbids business logic in a tool. The honest reading: CAS binds the entities that carry a version, and for an append-only insert the interrupt gate itself is the guarantee — nothing can be clobbered because nothing is being overwritten. Both tools this story registers are version-carrying, so the point is recorded rather than exercised.

**403 without reopening the enumeration oracle.** Today every foreign thread is a 404 byte-identical to an unknown one, deliberately: thread ids are readable by design, so a distinguishable refusal would let a caller enumerate other handlers' conversations (`copilot.py:274-283`, `threads.py:420-425`, and two tests). AD-6 and AC 2 nonetheless require 403 on resume. The reconciliation is to keep `employer_scope(ctx)` in the resume-only lookup and drop only the `user_id` predicate: a 403 then means "inside your own book of business, another handler's conversation" and reveals nothing across employers, while an out-of-scope or non-existent thread keeps returning the same 404. Both existing ownership tests use a message POST against an out-of-scope thread and stay green; the 6-3 e2e forged-id test uses `.u99999.`, a user that does not exist, and stays green too.

**The approval audit is a second transaction, and that is correct.** `update_claim_fields` owns its transaction and its docstring forbids being wrapped by a composite that would inherit the authority to discard the caller's pending writes (`edit.py:359-368`); `create_document` will own its own for the same reason. So `record_copilot_approval` cannot share the write's transaction. It does not need to: the write already emits its own AD-4 audit event in-transaction; the approval row records a different fact — that a human decided — and is the only row the reject branch writes at all.

**The RTW save must not call the model.** The handler's edited text is the payload; regenerating it would discard their edit and would put an LLM on a write path AD-14 keeps deterministic. The seam is a middleware that short-circuits the model call when state carries a drafted proposal, returning the write tool call directly so `HumanInTheLoopMiddleware.after_model` interrupts on it natively — one interrupt shape, no model call, no eighth quick-action key (`graph.py:172-174` directs this story to reuse `rtw`). If the installed middleware cannot be made to gate a proposal that did not originate in a model call without making one, that is a Block If.

## Verification

**Commands:**
- `cd lineworker/server && uv run ruff check . && uv run ruff format --check .` -- expected: clean.
- `cd lineworker/server && uv run mypy .` -- expected: clean under strict.
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker uv run pytest` -- expected: all pass; every function taking `engine` also carries `@requires_db`, so a run without the variable skips rather than errors.
- `cd lineworker/server && uv run alembic upgrade head && uv run alembic check` -- expected: "No new upgrade operations detected" **after** migration 0044 is written; a proposed operation before that is the `body_text` column and is the signal to write it, not to edit the model back.
- `cd lineworker/server && uv run alembic downgrade -1 && uv run alembic upgrade head` -- expected: clean both ways; the column is nullable, so the down-revision drops it without touching the 563 seeded rows.
- `cd lineworker/web && npm run generate:api && git diff --exit-code -- src/api/schema.d.ts` -- expected: no diff after committing.
- `cd lineworker/web && npm run lint && npm run typecheck && npm test` -- expected: clean.
- `cd lineworker && docker compose -f deploy/compose.e2e.yaml up -d --build --wait` -- expected: all services healthy including `model-stub`.
- `cd lineworker/e2e && npm run typecheck && npx playwright test --grep "@story:6-5\b|@story:6-4\b|@story:6-3\b|@story:2-5\b"` -- expected: pass; 2-5 owns the Documents tab the letter now lands in, and 6-3/6-4 must pass alongside the new spec.
- `cd lineworker && docker compose -f deploy/compose.e2e.yaml config --format json > /tmp/e2e.json && python3 ../.github/scripts/check_model_ports.py /tmp/e2e.json` -- expected: pass; no new service published.
- `cd lineworker/server && grep -rn "interrupt(" agents/` -- expected: exactly one call site, in `agents/approval.py` or the middleware it configures.
- `cd lineworker/server && grep -rn "mark_claim_stale" services/claims/documents.py` -- expected: no call, and a docstring paragraph saying why.

**Manual checks (if no CLI):**
- The 📄 Review RTW Policy action streams a draft, then the wide modal opens with Edit / Print / Copy; Print and Copy issue no network request.
- Editing the letter and clicking Save to claim renders a proposal card naming the tool and its typed arguments; approving it makes the letter appear in the Documents tab, and opening it shows the edited body — not the model's draft.
- Rejecting leaves the claim's Overview values, its version and its document count unchanged.

## Auto Run Result

Status: in-progress

The 2026-08-20 `blocked` exit is preserved in the Spec Change Log above, which records the alternatives weighed and the decision taken. Options (b) `BlobStore`, (c) diary note and (d) `email_log` were rejected in favour of (a) extending `document`; option (e), descoping the save to its own story, was not needed once (a) was authorized.

### Implementation record (2026-08-21)

Implemented at `bea8ab3`; 44 files changed (9 new). Verified independently of the implementation
agent's own report: ruff + `ruff format --check` clean, mypy strict clean over 264 files,
**2700 passed / 1 skipped** against a live Postgres, `alembic check` reports no new operations and
the 0044 down/up round trip is clean, `schema.d.ts` regeneration is idempotent (identical sha over
two runs), web typecheck clean with **646 vitest tests passing**, e2e typecheck clean,
**7 Playwright tests green for `@story:6-5`** including the `@smoke` done-gate, and the three specs
this story amends (`@story:6-3`, `@story:6-4`, `@story:2-5`) still pass — 17 green.

Invariants spot-checked directly rather than taken on report: no `interrupt()` call site exists
anywhere in `agents/` (every hit is prose; the vendored middleware is the sole producer), exactly
two `ToolKind.write` registry entries, and `services/claims/documents.py` names `mark_claim_stale`
three times in prose and calls it zero times.

**Deviations from this spec, all defensible, all for review:**

1. **A real single-flight defect was found and fixed.** `_run_frames` raised `_Interrupted` the
   moment it saw `__interrupt__`, abandoning the stream mid-iteration. The interrupt happens
   *inside* the `create_agent` subgraph, so the outer graph had not yet written the task carrying
   the pause — `thread_state` reported `interrupt_pending: False` and **the 409 did not fire on the
   one condition it exists for**. The payload is now collected and raised after the loop drains.
   This is the highest-value finding of the build and the review should read it first.
2. **The approval card is rendered by `ActionsTab`, not by `Transcript`.** The spec's task named
   both files; the Code Map allowed either. `Transcript.tsx` is untouched.
3. **The deterministic confirmation is returned, not written to the custom stream** — doing both put
   it on the wire twice, because the model node's returned `AIMessage` is already emitted on
   `messages` (unlike an outer QAS node's).
4. **`create_document` uses two function-local imports** to break a cycle (`detail.py` imports
   `documents_block` from this module). Documented in the module docstring.
5. **`normalise_new_document` is its own validator** rather than `edit.require_text`, which bans the
   whole C0 range — a letter has newlines.
6. **The e2e 403 test uses the full-portfolio supervisor**, not a peer handler: `PERSONAS` has no
   handler whose employers overlap Kaya's. The employer-bounded half — 403 inside scope,
   byte-identical 404 outside — is asserted in `test_copilot_threads.py`, where the seeded
   assignments are readable.
7. **Print prints the dialog**, not a composed letterhead; a print-only surface needs a shell-wide
   `@media print` rule that a copilot story should not introduce.
8. **`deploy/model-stub/app.py` keeps one pre-existing `ruff format` difference** — `deploy/` is
   outside the server ruff scope and CI, and was left untouched.

### Review pass (2026-08-21)

Two adversarial reviewers ran in parallel over the 9,260-line diff without prior context. Nineteen
findings were patched into the diff, three deferred, none rejected, and neither an intent gap nor a
spec defect was found — the spec had stated the right invariants ("an `edit` may not change which
entities are written", "at most one write pends per thread") and the implementation had fallen short
of them, which is a patch pass rather than a re-derivation.

Four of the five high-severity findings were reported independently by **both** reviewers. All four
were confirmed in the source before any fix was written: the missing tool-name comparison in
`_identity_refusal`, the identity-key projection that let an `edit` add `expected_version`, the
history-scanning drafted-call lookup that made a second save a silent no-op, and the per-tool
`ToolCallLimitMiddleware` instances that could not cap two *different* write tools in one turn.

**Verification after the fix pass**, re-run independently of the fix agent's own report:

| Check | Before | After |
|---|---|---|
| `ruff check` + `format --check` (now including `deploy/`) | clean, 277 files | clean, 278 files |
| `mypy` strict | clean, 264 files | clean, 264 files |
| `pytest` (live Postgres) | 2700 passed, 1 skipped | **2722 passed, 1 skipped** |
| `vitest` | 646 passed | **653 passed** |
| Playwright, full suite | 202 passed | **209 passed** |
| `alembic check` / 0044 down-up | clean | clean |
| `schema.d.ts` regeneration | idempotent | idempotent |

Invariants re-checked directly after the fixes: no `interrupt()` call site anywhere in `agents/`
(every hit is prose), exactly two `ToolKind.write` registry entries, and `mark_claim_stale` still
called zero times in `services/claims/documents.py`.

**Residual risks.** The three deferred findings are recorded in `deferred-work.md`: a scope-lost
resume wedges its thread rather than discarding and auditing (nothing is written either way, and the
spine's Deferred list already reserves "the precise fail-safe response code for a scope-lost
resume"); `create_document` does not bump `claim.version`, so the letter save has no idempotency
should a replay path ever be added; and `lineworker/web` has no formatter or CI format check, which
is why roughly a third of this story's web diff is unrelated reflow.
