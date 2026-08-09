# Story 6.3: Copilot Chat with Persistent Threads

Status: ready-for-dev

<!-- Note: This is one of the two LARGEST-RISK stories in the plan (graph + checkpoints + SSE + UI shell — readiness advisory #4). Run validate-create-story on this file before dev-story, and budget extra care per task. -->

## Story

As a claims handler,
I want a streaming claim-scoped chat that remembers our conversation,
so that copilot context survives navigation and sessions.

## Acceptance Criteria

1. **Given** the copilot Actions tab (enabled from this story), **when** a claim is selected, **then** the panel shows the pulse header, claim context, seeded case-summary greeting, free-text input, and disclaimer (UX-DR8), speaking to one compiled supervisor-router StateGraph via SSE in the assistant-ui LangGraph runtime — no hand-rolled streaming (AD-6, AD-9).
2. **Given** threads, **when** conversations start or resume, **then** the server mints `thread_id` keyed `(scope, user_id, conversation_seq)` — claim-scope only in v1 — checkpointed by AsyncPostgresSaver into tables created by the vendored Alembic migration (AD-3 exception), and "new conversation" increments the seq with prior threads read-only (FR-CP-2).
3. **Given** a running or interrupt-pending thread, **when** a second message arrives, **then** it is rejected 409 (single-flight), and every run terminates with exactly one of `interrupt | error | done` per the stream conventions.
4. **Given** graph state, **when** any node runs, **then** all nodes share the closed typed schema in `agents/state.py`; the `caller` channel re-resolves from `app_user` on every run start and resume (AD-7); all service access goes through registered read tools with the `{ok, data, display}` envelope and injected caller context (AD-13).
5. **Given** prompts and logs, **when** inspected, **then** prompts load from versioned files in `agents/prompts/` and operational logs carry IDs only — no prompt bodies or model output (AD-11).

## Tasks / Subtasks

- [ ] Task 1: Install the agent stack (AC: 1)
  - [ ] Server: LangGraph (Python) 1.2.x + langchain-ollama 1.x pinned in `server/pyproject.toml`
  - [ ] Web: `@assistant-ui/react-langgraph` 0.14.x pinned (0.13-era HITL interrupt bugs are documented — never downgrade; the 6.5 integration test guards the round-trip on any upgrade)
  - [ ] Chat model client built from config (`ollama_base_url`, `chat_model` from 6.1) with deployment-config `num_predict` and a per-run wall-clock timeout knob added to `server/config.py` (enforcement/`ai_limit` surfacing is asserted in 6.6; wire the bounds now so runs are never unbounded)
- [ ] Task 2: Vendored checkpoint migration — AD-3 exception (AC: 2)
  - [ ] New Alembic revision in `server/data/` vendoring AsyncPostgresSaver's DDL **for the pinned LangGraph 1.2.x version** (generate from the saver's setup against a scratch DB, then freeze as migration SQL); header comment: pinned version + "upgrading LangGraph requires a new migration" + "never call saver.setup() in prod"
  - [ ] Checkpoint tables are written **only** by the saver inside `agents/` — no app model classes, no repository over them; they are PHI-class (AD-11) with their own retention knob `copilot_checkpoint_retention_days` default 90 in `server/config.py` (purge job itself is Epic 8 — add the knob and a docstring pointer, nothing more)
  - [ ] CI's Alembic fresh-DB job must stay green with the vendored revision
- [ ] Task 3: Closed typed state schema — `agents/state.py` (AC: 4)
  - [ ] One module declaring the full graph state (TypedDict/Pydantic per LangGraph 1.2 idiom): `messages` (add-messages channel), `caller` (user id + role reference — never a materialized scope), `claim_id` (optional — `dashboard` scope is a reserved key shape, unused in v1), `pending_approval` (typed, None here; 6.5 populates it), plus routing scratch channels as needed
  - [ ] Module docstring: new channels are added **here via review, never node-locally** (AD-6); this is the schema 6.4/6.5/6.6 extend
- [ ] Task 4: Tool registry foundation — AD-13 (AC: 4)
  - [ ] `agents/tools/` registry: each entry wraps **exactly one** service call, typed Pydantic argument schema, declared `kind: read | write`; registry injects the AD-7 caller context from graph state — context is never a model-suppliable parameter; no tool imports `data/` or holds a session
  - [ ] Fixed result envelope `{ok, data, display}`: `data` keeps API conventions (integer cents, ISO dates); `display` carries service-formatted strings for every money/date field; failures return structured error results — never raw stack traces into the transcript, never silently swallowed
  - [ ] Write-tool gate scaffold: registry raises if a `kind: write` entry is invoked without an approval token in state (the token flow lands in 6.5 — the raise-by-default gate ships **now** so no window ever exists); unit-test the raise
  - [ ] Register the initial read tools this story needs (claim reader; absorb 6.2's thin wrappers into the registry per that story's seam note)
- [ ] Task 5: Supervisor-router StateGraph (AC: 1, 4)
  - [ ] One compiled graph in `agents/` (built once at startup, checkpointer attached): entry router node → free-text path → LLM router → grounded-chat node (claim-context read tools + narrate); QAS static-map dispatch is 6.4 — leave the pre-LLM dispatch hook in the entry node with the map empty/feature-flagged
  - [ ] `caller` channel re-resolution: the API dependency re-resolves context from `app_user` + `user_employer_assignment` and **overwrites the channel on every run start and every resume** — a resumed thread never replays yesterday's book of business (AD-7); implement as the single place run-inputs are assembled
  - [ ] Grounded-chat prompting: system + case-summary-greeting prompts as versioned files in `agents/prompts/` loaded by key (AC 5); model may quote tool `display` values verbatim, never originate figures (AD-2)
- [ ] Task 6: Thread service + API (AC: 2, 3)
  - [ ] Thread minting: server derives `thread_id` from `(scope=claim business ID, user_id, conversation_seq)`; clients never supply one; endpoints: list threads for a claim (caller's own), get/create current, "new conversation" (increments seq; prior threads become read-only history — enforce server-side: posting to a non-latest seq is rejected)
  - [ ] Single-flight: while a run is active or an interrupt is pending, a new message for that thread → 409 problem+json (RFC 9457 envelope); track run-state server-side (checkpoint/interrupt inspection or a run bookkeeping row — keep it minimal and crash-safe, e.g. re-derive from the saver rather than trusting an in-memory flag)
  - [ ] History read: prior-thread transcript endpoint reading from checkpoints (read-only), scoped to the thread's own user
- [ ] Task 7: SSE runs endpoint (AC: 1, 3)
  - [ ] `POST` runs endpoint in `api/` streaming SSE in the wire format the assistant-ui LangGraph runtime consumes (`@langchain/langgraph-sdk` format; LangGraph `messages`/`updates` stream modes feed it); event set `messages`, `updates`, `interrupt`, `error`, `done`; **exactly one terminal event per run** (`interrupt | error | done`) — make the terminal-emit path single and exception-safe
  - [ ] `error` events carry the problem+json body inline; resume is a POST to the same endpoint with `command: {resume: …}` (shape lands now; first real interrupt is 6.5)
  - [ ] Runs inherit the Task-1 bounds (`num_predict`, wall-clock); nginx already has `proxy_buffering off` on `/api` from Story 1.1 — verify, don't rebuild
- [ ] Task 8: Copilot panel UI — enable the Actions tab (AC: 1, 2, 3)
  - [ ] `web/src/features/copilot/`: flip the Actions tab from its Epic-4 disabled state; panel per UX-DR8 — pulse indicator, claim-context header, seeded case-summary greeting, streaming free-text chat, disclaimer line, per-claim thread switcher + "new conversation" (read-only rendering for prior threads)
  - [ ] assistant-ui LangGraph runtime over the SSE endpoint — no hand-rolled fetch streaming (AD-9); thread list/selection state via TanStack Query on the shared `queryKeys` module; claim selection (FR-Q-3) swaps the panel to that claim's current thread
  - [ ] 409 single-flight surfaced as a non-blocking inline notice (NFR-3/UX-DR11 — no alert()); quick-action buttons are **not** in this story (6.4); degradation disabled-states are 6.6 (leave input enabled-by-default plumbing clean so 6.6 can target exactly the affected inputs)
  - [ ] Tokens: Story 1.1 light palette (canonical; "dark console aesthetic" is the documented discrepancy)
- [ ] Task 9: Logging discipline (AC: 5)
  - [ ] structlog events for run lifecycle: thread_id, run id, event names, durations — **never** message content, prompt bodies, or model output (AD-11); add a test/lint asserting copilot log calls carry no content fields
- [ ] Task 10: Tests (AC: all)
  - [ ] Unit: thread-key minting + seq increment + read-only enforcement; single-flight 409 (active run and interrupt-pending); state schema is the single source (no node-local channels — assert graph channels ⊆ `agents/state.py`); registry context injection; write-tool gate raises; envelope shape on success and failure
  - [ ] Graph test with a stub chat model: free-text run streams `messages` then exactly one `done`; caller re-resolution overwrites a poisoned `caller` channel on resume
  - [ ] Integration: run → checkpoint rows persisted → API restart → history endpoint returns transcript (persistence across sessions is the story's point)
  - [ ] E2E: `e2e/stories/6-3-copilot-chat-with-persistent-threads.spec.ts` tagged `@story:6-3 @epic:6`, one `@smoke` happy path — handler selects claim, Actions tab live, sends free text against the model-stub, asserts **structure not prose**: stream renders, terminal `done`, message persists across reload, new-conversation freezes prior thread, second-message-while-running 409 path
  - [ ] Amend Epic 4's spec (Actions tab was asserted disabled) in this PR — AD-15: specs amended, never deleted

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story stands up the **copilot spine**: the one compiled supervisor-router StateGraph, vendored checkpoint tables, server-minted single-flight threads, the SSE protocol, the tool-registry foundation, and the live Actions tab with free-text grounded chat. It is **not**: the 7 QAS quick actions or the static key→node map contents (6.4 — but the pre-LLM dispatch hook and `requires_llm` concept land in the entry node's design now so 6.4 slots in without re-architecture); write proposals, `interrupt()` flows, or the RTW letter (6.5 — but `pending_approval` exists in the schema, the write-tool gate raises, and the stream/resume protocol already speaks `interrupt`); degradation UX and `ai_unavailable`/`ai_limit` assertions (6.6 — but run bounds are wired). Dashboard-scope copilot is **Deferred** — the panel appears only in claim context and no `dashboard` thread is ever created; the key shape merely reserves it. The prototype's `sendAI`/`offlineAnswer` pair is the banned anti-pattern: no canned answers, ever (AD-14).

**Risk framing:** four subsystems meet here (graph, checkpoints, SSE, UI shell). Land them in task order — schema/migration and registry first, graph next, transport, then UI — keeping each independently tested before integrating. If mid-story the SSE wire format and assistant-ui disagree, the runtime's expected format wins (AD-9: no hand-rolled streaming); adapt the server.

### Architecture compliance (binding ADs for this story)

- **AD-6:** one compiled StateGraph; `(scope, user_id, conversation_seq)` server-minted threads; single-flight 409; closed schema in `agents/state.py`; at most one `pending_approval` per thread (scaffolded).
- **AD-3 (exception):** checkpoint DDL vendored in a dedicated Alembic migration, pinned to LangGraph 1.2.x, written only by the saver — never `setup()` in prod.
- **AD-7:** `caller` is a reference re-resolved from `app_user` on every start **and resume**; tools get context injected by the registry.
- **AD-13:** tools thin (one service call), registered, typed, `{ok, data, display}`; write tools unreachable without the approval token (raise ships now).
- **AD-9:** assistant-ui LangGraph runtime over SSE; TanStack Query + shared `queryKeys` for thread state.
- **AD-2:** grounded chat quotes tool `display` values; the LLM never originates financial figures.
- **AD-11:** checkpoints are PHI with their own 90-day retention knob; logs carry IDs only.
- **AD-14:** honest-degradation posture starts here — no canned answers, no cloud path (full assertions in 6.6).
- **AD-5:** chat inference only via internal Ollama from `agents/`.
- **Copilot stream convention row:** event set, one-terminal-event rule, problem+json in `error`, resume shape, `num_predict` + wall-clock bounds.
- **AD-15:** story spec + amended Epic-4 spec gate done.

### Data notes

Tables created here: the LangGraph checkpoint tables (vendored migration; owner: the saver in `agents/` — the registered AD-3/AD-12 exception; purge via `services/audit` in Epic 8 by `thread_id`). No other new tables — threads are derivable from checkpoints plus the minting rule; if a small thread bookkeeping table proves necessary for single-flight/seq tracking, it is owned by the thread service in `agents/`'s server-side module and noted in the Dev Agent Record. Reads: `app_user` + `user_employer_assignment` (context re-resolution), claim data via registered read tools only.

### UX notes

- UX-DR8 is the panel contract: pulse indicator, claim-context header, Actions/Diary tabs, seeded case-summary greeting, streaming chat with disclaimer, per-claim thread switcher. Diary tab already ships (Epic 4) inside this same panel shell — do not disturb it.
- UX-DR11 / NFR-3: 409 and stream errors as inline/toast, never blocking dialogs; loading/empty states for thread list and history.
- UX-DR12: light palette per the Story 1.1 canonical ruling.
- FR-CP-2: per-claim history must survive navigation and re-login — that is the acceptance bar for Task 10's integration test.

### Testing requirements

- Unit/graph/integration tests per Task 10; the one-terminal-event property and single-flight 409 are the two invariants most likely to regress — test them directly.
- Stub chat model for all graph tests; anything needing a live model is `@live-ai` and excluded from the gate (AD-15).
- Playwright: `e2e/stories/6-3-copilot-chat-with-persistent-threads.spec.ts` tagged `@story:6-3 @epic:6`, exactly one `@smoke` happy path, asserting stream-event structure against the model-stub. Story cannot move to `review`/`done` until it passes (AD-15 done-gate).
- The full SSE + assistant-ui protocol integration test (NFR-7) is formally 6.5's AC (with interrupts); this story's spec covers the non-interrupt stream path it will extend.

### Project Structure Notes

- `agents/` gains: `state.py`, graph module, `tools/` registry, `prompts/` files, thread service; API surface in `server/api/` (runs + threads routers, SSE); UI in `web/src/features/copilot/`.
- Depends on 6.1 (Ollama + stub) and benefits from 6.2's wrapper seam; 6.4/6.5/6.6 all build directly on this story's graph, registry, schema, and stream — deviations here are architecture changes, not dev discretion.
- Recommendation repeated from the readiness report: run `validate-create-story` on this file before `dev-story`.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 6.3]
- AD-6 full text (threads, single-flight, state, approval scaffolding): [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-6]
- AD-3 exception (vendored checkpoint DDL): [Source: ARCHITECTURE-SPINE.md#AD-3]
- Copilot stream + Prompts + Testing convention rows: [Source: ARCHITECTURE-SPINE.md#Consistency Conventions]
- AD-7 caller re-resolution; AD-13 registry contract; AD-9 frontend discipline: [Source: ARCHITECTURE-SPINE.md#AD-7 / #AD-13 / #AD-9]
- Graph topology + five node invariants; checkpoint-PHI narrative: [Source: docs/Architecture-LINEWORKER.md#5.2 / #5.4]
- Stack pins (LangGraph 1.2.x, langchain-ollama 1.x, assistant-ui 0.14.x + 0.13 HITL bug note): [Source: ARCHITECTURE-SPINE.md#Stack]
- Largest-risk advisory + dense-AC decomposition note: [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Story Sizing Assessment / #Standing Advisories]
- UX-DR8; Epic 4 Actions-tab-disabled seam: [Source: epics.md#UX Design Requirements / #Epic 4 (line ~636)]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
