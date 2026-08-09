# Story 6.5: Human-Gated Writes & the RTW Letter

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want the copilot to draft actions and letters that only execute with my explicit approval,
so that AI never mutates a claim on its own.

## Acceptance Criteria

1. **Given** any graph-proposed write to an owned entity (claim fields, diary, meetings, emails, documents), **when** proposed, **then** the run pauses via `interrupt()` with a `pending_approval` recording the `version` of every entity drafted against, and write tools are unreachable by LLM tool selection — the registry raises without the approval token (AD-6, AD-13).
2. **Given** an approval, **when** resumed via `Command(resume=…)` by the thread's own user (others 403), **then** the write executes through AD-4 commands CAS-guarded on the recorded versions — a stale approval 409s, the graph discards the proposal, tells the user the claim changed, and may re-propose from fresh state; it never force-writes.
3. **Given** a rejection, **when** it resolves, **then** the proposal is discarded, `pending_approval` clears, an assistant message confirms cancellation, and both branches record a content-free `record_copilot_approval` audit event — the only persistence on the reject branch.
4. **Given** the 📄 Review RTW Policy quick action, **when** clicked, **then** its QAS node merges claim fields from tool output, the LLM drafts surrounding prose only (AD-2), the letter presents in the wide editable modal with ✏ Edit / 🖨 Print / 📋 Copy (UX-DR10), and saving it to the claim passes the interrupt gate (FR-H-11).
5. **Given** the graph tests, **when** CI runs, **then** a stub chat model covers the approve/reject round-trip and one integration test drives the real SSE + assistant-ui protocol end to end (NFR-7).

## Tasks / Subtasks

- [ ] Task 1: `pending_approval` + `interrupt()` flow (AC: 1)
  - [ ] Type the `pending_approval` channel in `agents/state.py` (extend, via that module only): action kind, target entity/entities with ids, drafted payload, and the **`version` of every entity drafted against** (fetched via read tools at draft time); at most one `pending_approval` per thread — a second write in one run sequences as a second interrupt after the first resolves (AD-6)
  - [ ] Approval node: any graph-proposed write to an entity in the AD-12 ownership registry (claim fields, diary notes, meetings, emails, documents) sets `pending_approval` and pauses via `interrupt()`; the SSE run terminates with the `interrupt` event carrying the proposal payload for assistant-ui to render (6.3's protocol slot, first real use)
  - [ ] The model only ever **proposes** (emits the payload); write tools stay out of every LLM tool-selection set — assert none is bound to any model call
- [ ] Task 2: Write tools + approval-token gate (AC: 1, 2)
  - [ ] Register the needed `kind: write` tools in `agents/tools/`, each wrapping exactly one existing AD-4 owning-service command (claim field update → `services/claims`; diary/meeting/email → Epic 4 commands; document save → `services/claims`/BlobStore path); no new business logic, no composed writes in a tool (AD-13)
  - [ ] 6.3's registry gate becomes real: define the approval token (set only by the approval-execution step after an `interrupt()` resolves approve); a write tool invoked without it **raises** — keep 6.3's unit test green and extend it (token present ⇒ executes; forged/absent ⇒ raises)
  - [ ] Write commands receive `expected_version` from the recorded `pending_approval` versions — CAS per the Write-concurrency convention; caller context injected by the registry as ever (AD-7)
- [ ] Task 3: Resume — approve / reject / stale (AC: 2, 3)
  - [ ] Resume path: POST to the runs endpoint with `command: {resume: …}` (6.3's shape) resumes the paused graph; **only the thread's own `user_id` may resume — others 403** (enforced server-side at the API dependency, re-resolving caller per AD-7 on resume)
  - [ ] Approve branch: execute through the write tool → AD-4 command CAS on recorded versions → on success stream confirmation and `done`; on 409 (stale): discard the proposal, clear `pending_approval`, stream an assistant message that the claim changed since drafting, optionally re-propose from **fresh** state (new versions, new interrupt) — never force-write, never merge
  - [ ] Reject branch: discard proposal, clear `pending_approval`, stream cancellation confirmation; **no entity write occurs**
  - [ ] Both branches call `services/audit.record_copilot_approval` (new content-free command: action kind, claim, actor, timestamp, outcome — no drafted content); this is the **only** persistence permitted on the reject branch (checkpointer aside) — audit-test it
  - [ ] Single-flight interaction: interrupt-pending thread still 409s new messages (6.3); resume is the only way forward; after resolution the thread returns to accepting
- [ ] Task 4: RTW letter node completion (AC: 4)
  - [ ] Extend 6.4's draft-only `rtw` node: tool-merge claim fields (worker, employer, dates, restrictions — from the claim-reader tool's `display` values), LLM drafts surrounding prose only (AD-2); letter returned as a structured draft payload
  - [ ] Wide editable modal in `web/src/features/copilot/` per UX-DR10: contenteditable letter body with ✏ Edit / 🖨 Print / 📋 Copy actions (print via browser print of the letter surface; copy to clipboard); handler edits are theirs — the edited text is what goes into the save proposal
  - [ ] "Save to claim" from the modal submits the write proposal → `interrupt()` gate → assistant-ui approval UI → approve persists via the owning service's AD-4 command as a claim-attached document/letter artifact (reuse the Epic 2 documents path; **no new table** — if the existing `document` shape can't carry a generated letter, stop and raise rather than inventing schema)
  - [ ] Print/Copy without saving stays gate-free (no entity write — nothing to approve)
- [ ] Task 5: Approval UI (AC: 1–4)
  - [ ] assistant-ui interrupt rendering: proposal card in the thread (what will change, against which claim) with Approve / Reject; disable composer while pending (single-flight); 403 for foreign users is server-enforced — the UI simply never offers resume on others' threads
  - [ ] Stale-approval outcome renders the "claim changed since drafting" message inline with the fresh re-proposal if issued (NFR-3 states; no dialogs)
  - [ ] **0.14.x caution:** `@assistant-ui/react-langgraph` 0.13-era HITL interrupt bugs are documented — do not downgrade; if the runtime misbehaves on interrupt/resume, capture it in the Dev Agent Record and adapt server payload shape to the runtime's expectation (AD-9: the runtime's protocol wins)
- [ ] Task 6: Tests (AC: 5, all)
  - [ ] Graph tests (stub chat model scripting a write proposal): approve round-trip (interrupt → resume approve → CAS write executed → audit event → done); reject round-trip (no write, `pending_approval` cleared, cancellation message, audit event); stale round-trip (bump entity version between draft and approve → 409 → discard + message + optional re-propose; assert no write occurred); forged-token raise; foreign-user resume → 403
  - [ ] **The** SSE + assistant-ui protocol integration test (NFR-7, explicit AC): drives the real HTTP SSE endpoint through the interrupt → `command: {resume}` → new stream → terminal event cycle end to end against the stub — this is the round-trip test the stack table demands for the 0.14.x runtime
  - [ ] Audit assertions: both branches produce exactly one content-free `record_copilot_approval` row; reject branch produces nothing else
  - [ ] E2E: `e2e/stories/6-5-human-gated-writes-the-rtw-letter.spec.ts` tagged `@story:6-5 @epic:6`, one `@smoke` happy path — RTW quick action → letter modal (Edit/Print/Copy present) → save → interrupt approval card → approve → confirmation; plus the reject path; driven by the model-stub's scripted write-proposal (AD-15 names this exact scenario); assert structure, never prose

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story closes the copilot's **write path**: `pending_approval` + `interrupt()`, version-pinned approvals executing CAS-guarded AD-4 commands, the registry's approval-token gate going live, the `record_copilot_approval` audit command, and the RTW letter end to end (merge → prose → modal → gated save). It is **not**: new write capabilities or business commands (every write reuses an existing Epics 2–4 owning-service command — if one is missing, that is a finding, not a build item); the QAS map or other nodes (6.4); degradation (6.6). The AI-insight refresh path (6.2) remains gate-exempt but audited — do not route it through interrupts. Core invariant, stated once more because it is the story's soul: **write tools are NEVER LLM-selectable; writes happen only via `pending_approval` + `interrupt()` + version-pinned approval; stale approvals fail safe; nothing ever force-writes.**

### Architecture compliance (binding ADs for this story)

- **AD-6:** interrupt gate on every graph-proposed write to owned entities; one `pending_approval` per thread; resume only by the thread's own user via `Command(resume=…)`; version-pinned approvals; both outcomes audited content-free via `record_copilot_approval` — the only reject-branch persistence.
- **AD-13:** write tools registered `kind: write`, one command each, unreachable without the approval token (registry raises); context injected, never model-suppliable.
- **AD-4 / Write-concurrency convention:** approval executes PATCH-shaped commands CAS-ing on the recorded `expected_version`; 409 problem+json with fresh entity on mismatch; conflict resolution is re-read-and-redo (re-propose), never server-side merge.
- **AD-2:** RTW letter merges claim fields from tool output; the LLM drafts surrounding prose only.
- **AD-12:** every write lands through the entity's owning service (claims/diary aggregate/documents); no new writers.
- **AD-7:** caller re-resolved on resume; 403 for non-owners.
- **AD-11:** proposals/letters never logged; audit rows content-free.
- **AD-9:** assistant-ui renders the interrupt natively — no custom approval transport.
- **AD-15:** the interrupt approve/reject round-trip against the stub's scripted write-proposal is a named gate scenario.

### Data notes

No new tables. New audit command: `services/audit.record_copilot_approval` (content-free row in the existing `audit_event` fixed schema). Writes flow exclusively through existing AD-12 owners: `services/claims` (claim fields, documents, diary aggregate incl. meetings/emails per the Capability Map). `pending_approval` lives in graph state/checkpoints only (PHI-class via AD-11, already covered by 6.3's retention knob).

### UX notes

- UX-DR10: the RTW letter modal is a named modal contract — wide, contenteditable, ✏ Edit / 🖨 Print / 📋 Copy; match the prototype's `rtw` modal behavior (reference only, no code reuse).
- UX-DR8/UX-DR11: approval card inline in the thread; composer disabled while pending; stale-conflict and cancellation as inline messages — no dialogs (NFR-3).
- UX-DR12: Story 1.1 light-palette tokens (canonical; the "dark console aesthetic" wording is the documented discrepancy).
- FR-H-11 (letter half): pre-populated from claim data, editable, print/copy; glossary half shipped in Epic 1.

### Testing requirements

- The five graph round-trips in Task 6 (approve / reject / stale / forged-token / foreign-user) are the minimum; the stale case must assert **no write occurred**.
- The real SSE + assistant-ui integration test is an explicit AC (NFR-7) — not optional, and it is the designated guard for the 0.14.x runtime's HITL behavior on any future upgrade.
- Stub model scripts the write proposal (deterministic); anything live-model is `@live-ai`, excluded from the gate.
- Playwright: `e2e/stories/6-5-human-gated-writes-the-rtw-letter.spec.ts` tagged `@story:6-5 @epic:6`, exactly one `@smoke` happy path. Story cannot move to `review`/`done` until it passes (AD-15 done-gate).

### Project Structure Notes

- Server work extends 6.3/6.4 modules: state channel, approval node + resume handling in the graph, write tools in `agents/tools/`, `record_copilot_approval` in `services/audit`; UI in `web/src/features/copilot/` (modal + interrupt rendering).
- Depends on 6.3 (graph/stream/registry/schema) and 6.4 (`rtw` node + map); 6.6 relies on this story leaving the thread-state machine clean (interrupt-pending → resolved → accepting).
- If the checkpoint of an interrupt-pending thread outlives a deploy, resume must still work — test a restart between interrupt and resume in the integration test if cheap; note the result either way.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 6.5]
- AD-6 full text (approval gate, versions, stale-safe, `record_copilot_approval`): [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-6]
- AD-13 write-tool gate; AD-4 + Write-concurrency convention; Copilot stream resume shape: [Source: ARCHITECTURE-SPINE.md#AD-13 / #AD-4 / #Consistency Conventions]
- "Writes are human-gated and stale-safe" narrative: [Source: docs/Architecture-LINEWORKER.md#5.2]
- assistant-ui 0.14.x pin + 0.13 HITL bug note: [Source: ARCHITECTURE-SPINE.md#Stack]
- AD-15 interrupt round-trip as gate scenario: [Source: ARCHITECTURE-SPINE.md#AD-15]
- UX-DR10 (RTW modal), FR-H-11: [Source: epics.md#UX Design Requirements / #Requirements Inventory]
- Prototype RTW letter modal (behavior reference): [Source: docs/Workers_Comp_Prototype.html (RTW letter modal + Edit/Print/Copy)]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
