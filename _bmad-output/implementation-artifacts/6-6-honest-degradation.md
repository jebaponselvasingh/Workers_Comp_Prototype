# Story 6.6: Honest Degradation

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want the console to stay fully usable when the model is down,
so that AI unavailability never blocks claim operations.

## Acceptance Criteria

1. **Given** Ollama is unreachable, **when** a `requires_llm: true` action or free chat runs, **then** the stream returns `error` code `ai_unavailable` with bounded retry — no retry storms, no cloud fallback, no pre-authored text presented as model output (AD-14, NFR-2).
2. **Given** the same outage, **when** a `requires_llm: false` action runs, **then** it executes and streams normally, and the UI disables exactly the affected inputs — never the whole copilot pane (NFR-6).
3. **Given** any non-AI claim screen, **when** Ollama is down, **then** queue, detail, financials, and diary all function with no hard dependency on the agent runtime (under Playwright test).
4. **Given** a run exceeding `num_predict` or the wall-clock timeout, **when** it terminates, **then** it ends with `error` code `ai_limit` and the thread returns to an accepting state.

## Tasks / Subtasks

- [ ] Task 1: `ai_unavailable` server path (AC: 1)
  - [ ] Detect Ollama unreachability at the chat-client boundary in `agents/` (connect/read failure, health probe); a `requires_llm: true` QAS action or free-chat run terminates with the `error` stream event, problem+json body inline, code `ai_unavailable` — exactly one terminal event, thread returns to accepting state (single-flight cleared)
  - [ ] Bounded retry server-side: small fixed attempt count with backoff on the failed call — never a retry storm, never queue-and-wait; there is **no cloud fallback** (AD-5 — no such code path exists to fall into) and **no canned text** (`offlineAnswer` is the explicitly banned prototype pattern — an outage message is a typed error, never prose presented as model output)
  - [ ] Embeddings outage note: 6.1's refresh jobs already just skip-and-retry-next-cycle (staleness stays flagged); confirm no crash-loop, nothing more to build
- [ ] Task 2: `requires_llm: false` actions keep working (AC: 2)
  - [ ] With the chat model unreachable, dispatching a `requires_llm: false` key from 6.4's map must never touch the chat client: execute tools, stream results, terminate `done` — add a guard/test that the false-path makes zero model calls (that is what makes the flag honest)
- [ ] Task 3: AI-availability signal + partial UI disabling (AC: 1, 2)
  - [ ] Expose availability without new machinery where possible: a lightweight AI-health flag (e.g. on an existing status/health endpoint or a tiny `GET` probe in `api/`) polled via TanStack Query, **plus** reactive marking on receipt of an `ai_unavailable` stream error; recovery flips the flag back on a successful probe — bounded polling interval (config knob), no storm
  - [ ] Copilot panel (`web/src/features/copilot/`): when unavailable, disable **exactly** the affected inputs — free-text composer and `requires_llm: true` quick-action buttons (from the 6.4 map's flags, which the UI must receive or mirror from one source) — with a quiet inline notice; `requires_llm: false` buttons, the Diary tab, thread history reading, and the whole rest of the pane stay live (UX-DR8 disabled-state treatment; never grey the pane)
  - [ ] Client retry is bounded too: a manual "try again" affordance and the poll — no automatic client re-fire loops (NFR-6)
- [ ] Task 4: No hard dependency from claim screens (AC: 3)
  - [ ] Audit `web/` for any agent-runtime dependency outside `features/copilot/` (imports, queries, render-blocking calls): queue, claim detail (all tabs incl. AI Insights — cards render cached rows + empty states without the runtime), financials, dashboard, diary must load and function with Ollama down; fix anything found
  - [ ] Server-side: no non-copilot endpoint may touch the chat client; AI-health probe failure must not degrade `/healthz` container health (the api container is healthy without its model — deliberate: compose must not restart the api over an Ollama outage; document in the probe's docstring)
- [ ] Task 5: `ai_limit` bounds (AC: 4)
  - [ ] Enforce 6.3's wired bounds: `num_predict` passed to the model call; wall-clock timeout wrapping the run; either overrun terminates the stream with `error` code `ai_limit` (problem+json inline), partial output stays visible in the transcript as whatever streamed, and the thread returns to accepting (single-flight cleared, no stuck `pending_approval` — an interrupt never got raised)
  - [ ] Both bounds are config knobs (already in `server/config.py` from 6.3); no hardcoding
- [ ] Task 6: Tests (AC: all)
  - [ ] Unit/graph: chat-client failure → `ai_unavailable` terminal error + accepting thread; retry attempts bounded (assert call count); `requires_llm: false` path makes zero model calls; timeout/num_predict overrun → `ai_limit` + accepting thread
  - [ ] Integration: stop/point-away the stub → free chat run over real SSE yields the `error` event with `ai_unavailable` problem+json; restart → recovery
  - [ ] E2E: `e2e/stories/6-6-honest-degradation.spec.ts` tagged `@story:6-6 @epic:6` — AD-15 names this pattern: **stop the model-stub**, then assert: (a) `@smoke` happy path — queue, claim detail, financials, diary all fully function with the stub stopped (AC 3's Playwright clause); (b) copilot pane shows exactly the affected inputs disabled, a `requires_llm: false` action still streams to `done`, a free-chat attempt surfaces the `ai_unavailable` error state; assert structure/event codes, never prose
  - [ ] Verify no canned text: assert the outage UI/transcript contains a typed error state and no assistant-styled prose message

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

The epic's closing story: it makes AD-14's degradation promises **observable and tested** — `ai_unavailable` and `ai_limit` error paths, bounded retry, the false-path guarantee, precise partial disabling, and the proof that claim operations never depend on the model. It is **not**: new copilot features, nodes, tools, or map entries (6.3/6.4/6.5 — all prerequisites); monitoring/alerting stack (Deferred — the health probe here is a UI signal, not observability); Ollama capacity/queueing under concurrent load (Deferred by decision — single-user assumption; only the per-run bounds are binding). The prototype's `offlineAnswer()` — a `switch` of pre-written claim-specific text served when the API call fails — is this story's named enemy: the whole point is that failure looks like failure, honestly typed, while everything deterministic keeps working.

### Architecture compliance (binding ADs for this story)

- **AD-14:** the binding text — `requires_llm: false` executes and streams normally during outage; `requires_llm: true` + free chat return `error` code `ai_unavailable` with bounded retry; no retry storms; no cloud fallback; no pre-authored answers; UI disables exactly the affected inputs, never the pane; claim screens have no hard dependency on the agent runtime.
- **AD-5:** there is no fallback path because no cloud code path exists — do not add one under any guise.
- **Copilot stream convention:** `error` carries problem+json inline; exactly one terminal event; `num_predict` + wall-clock overruns end with code `ai_limit`.
- **AD-6:** after any terminal error the thread's single-flight lock clears — the thread is accepting again (AC 4 says so explicitly for `ai_limit`; apply equally to `ai_unavailable`).
- **AD-9:** availability state via TanStack Query; no hand-rolled polling loops outside it.
- **AD-11:** outage/limit logs are IDs + event names; no payloads.
- **AD-15:** degradation specs run by stopping the model-stub in the e2e profile — the named technique; `@live-ai` never applies here (an outage needs no live model).
- **NFR-2 / NFR-6:** server-brokered AI with guardrails; availability degradation never blocks non-AI claim operations.

### Data notes

No new tables, no schema changes. New config knobs only: AI-health poll interval and retry bounds (extend `server/config.py`); `num_predict` / wall-clock knobs already exist from 6.3. No writes anywhere — this story only adds failure handling, a status signal, and tests.

### UX notes

- UX-DR8: the disabled-state treatment for `ai_unavailable` is written into the panel's design requirement — only affected inputs disabled, quiet inline notice, pane alive (Diary tab, history, false-flag actions untouched).
- UX-DR11 / NFR-3: outage and limit surfaces are inline states/toasts, never dialogs; include the manual retry affordance.
- UX-DR12: state styling from the Story 1.1 tokens (warn/error semantics; light palette is canonical — the "dark console aesthetic" wording is the documented discrepancy).

### Testing requirements

- The zero-model-calls assertion on the `requires_llm: false` path and the bounded-retry call-count assertion are the two tests that keep the flags honest — treat both as mandatory.
- Both error codes (`ai_unavailable`, `ai_limit`) asserted at stream level with problem+json bodies; thread-returns-to-accepting asserted after each.
- Playwright: `e2e/stories/6-6-honest-degradation.spec.ts` tagged `@story:6-6 @epic:6` with exactly one `@smoke` happy path (the claim-screens-function-with-stub-stopped scenario), against the freshly reset e2e stack. Story cannot move to `review`/`done` until it passes (AD-15 done-gate).
- Spec mechanics note: stopping/starting the `model-stub` container mid-spec needs a fixture helper (compose control from the test runner) — build it in `e2e/fixtures/` so Epic 8's gate reuses it, and leave the stub running for subsequent spec files (reset discipline).

### Project Structure Notes

- Changes concentrate in: the chat-client boundary + run wrapper in `agents/`, a small probe in `api/`, panel state in `web/src/features/copilot/`, and `e2e/fixtures/` + the story spec. No new packages.
- Last story of Epic 6: leave the epic in the state Epic 8's full gate expects — routing tests, interrupt round-trips, SSE integration test, and this story's degradation specs are all named components of Story 8.4's complete CI gate.
- Depends on 6.3 (stream/bounds/thread machine), 6.4 (`requires_llm` map), 6.5 (clean thread-state machine around interrupts).

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 6.6]
- AD-14 full text (the story's spec); AD-5 no-fallback: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-14 / #AD-5]
- Copilot stream convention (`ai_limit`, one terminal event, problem+json): [Source: ARCHITECTURE-SPINE.md#Consistency Conventions]
- "Degradation is honest" narrative + `offlineAnswer` ban: [Source: docs/Architecture-LINEWORKER.md#5.2]
- AD-15 stop-the-stub degradation technique: [Source: ARCHITECTURE-SPINE.md#AD-15]
- Deferred: Ollama capacity/queueing, observability stack: [Source: ARCHITECTURE-SPINE.md#Deferred]
- NFR-2 / NFR-6; UX-DR8 disabled-state treatment: [Source: epics.md#Requirements Inventory / #UX Design Requirements]
- Prototype `offlineAnswer` (the banned pattern, ~line 1533) and `sendAI` (~line 1586): [Source: docs/Workers_Comp_Prototype.html]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
