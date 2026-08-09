# Story 6.4: Deterministic Quick Actions (QAS)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want one-click quick actions that behave identically every time,
so that routine copilot queries are fast and trustworthy.

## Acceptance Criteria

1. **Given** the 7 quick-action buttons (§ Labor law & state rules · ↻ Similar case outcomes · 📄 Review RTW Policy · ✓ Reserve review · 🔍 Fraud risk check · ! Next best actions · 📊 Data alignment note), **when** any is clicked, **then** the key routes through the static key→node map before any LLM call — only free text reaches the LLM router (AD-14, FR-CP-1) — and the routing map is unit-tested key by key (NFR-7).
2. **Given** each QAS node, **when** it executes, **then** it declares `requires_llm`, invokes its registered read tools, and may use the LLM only to narrate tool output — quoting `display` values verbatim, never originating figures (AD-2, AD-13).
3. **Given** the similar-case action, **when** it answers, **then** it discloses staleness beyond the configured `embedded_at` threshold (AD-12), and the labor-law action grounds in scoped `knowledge_chunk` retrieval.
4. **Given** a tool failure, **when** it occurs, **then** the node streams a structured error message — never a raw stack trace, never silently swallowed (AD-13).

## Tasks / Subtasks

- [ ] Task 1: Static QAS routing map (AC: 1)
  - [ ] Populate the 6.3 entry-node dispatch hook with the static key→node map for all 7 keys: `laborlaw`, `similar`, `rtw`, `reserve`, `fraud`, `nextactions`, `data_alignment` (snake_case keys; UI owns the emoji/labels per the enums convention)
  - [ ] Dispatch happens **before any LLM call** — a QAS message carries its key; the entry node routes it straight to its named node; only free-text messages fall through to the LLM router (AD-14)
  - [ ] Each map entry declares `requires_llm: bool`; the map is one importable structure (single source for 6.6's degradation gating and the unit tests); `data_alignment` should be the natural `requires_llm: false` entry (deterministic service-derived dataset note) so a false path exists for 6.6 — decide per-key flags at implementation and record them in the Dev Agent Record
- [ ] Task 2: QAS nodes — deterministic tool calls + narrate-only LLM (AC: 2, 3)
  - [ ] One node per key in the compiled graph (extending 6.3's topology; state stays within `agents/state.py` — new channels only via that module):
    - `laborlaw` → registry read tool over `services/rag.search_knowledge` (scoped `knowledge_chunk` retrieval, 6.1); narrated briefing with the "informational only — not legal advice" disclaimer
    - `similar` → similar-case read tool (returns `embedded_at`); narration must disclose staleness beyond the config threshold `embedding_staleness_threshold` (add knob to `server/config.py`)
    - `reserve` → reserve-check tool (`services/financials`, Epic 3's single computation); narrate the Light/Adequate/Heavy verdict + figures via `display`
    - `fraud` → fraud score/flags tool (`services/derivations`); low-risk claims get the low-risk confirmation, mirroring 6.2's card logic
    - `nextactions` → action-worklist tool (`services/worklist`)
    - `data_alignment` → deterministic dataset-alignment note from service output (no LLM if flagged `requires_llm: false`)
    - `rtw` → **draft-only in this story**: tool-merge of claim fields + LLM surrounding prose streamed as a normal assistant message; the editable modal, `interrupt()` gate, and save-to-claim are Story 6.5 — this node proposes **no write** here
  - [ ] Register any missing read tools in the 6.3 registry (each wraps exactly one service call, `{ok, data, display}` envelope, injected caller context — AD-13); nodes call registry entries, never services directly
  - [ ] LLM usage in every node is narrate-only: never tool selection, never figures — prompts instruct quoting `display` verbatim (AD-2/AD-13)
- [ ] Task 3: Prompts (AC: 2)
  - [ ] One versioned prompt file per QAS key needing one in `agents/prompts/`, keyed 1:1 to QAS keys (spine Prompts convention); the prototype's `QAS` prompt texts are the content reference (carried "nearly verbatim" per the architecture narrative) — never inline string literals in node bodies
- [ ] Task 4: Structured tool-failure handling (AC: 4)
  - [ ] A failed tool (`ok: false` envelope) yields a streamed structured error message in the transcript (what failed, in plain terms) and a clean `done`/`error` terminal per the stream conventions — no raw stack traces into the transcript, no silent swallow; keep the run's one-terminal-event invariant
  - [ ] Log the failure content-free (IDs + event names, AD-11)
- [ ] Task 5: Quick-action buttons UI (AC: 1)
  - [ ] Add the 7 quick-action buttons to the copilot panel (`web/src/features/copilot/`, UX-DR8) with the prototype's icon+label treatment; clicking sends the QAS key (plus the claim context the panel already carries) through the existing assistant-ui/SSE path — no new transport
  - [ ] Buttons respect the panel's existing states (disabled while a run is in flight — single-flight 409 avoidance); per-key degradation disabling is 6.6
- [ ] Task 6: Tests (AC: all)
  - [ ] Unit — routing map **key by key** (NFR-7/AC 1): each of the 7 keys reaches exactly its node with zero LLM-router involvement (stub model asserts no router call); unknown key → structured rejection; free text → LLM router; every entry declares `requires_llm`
  - [ ] Unit/graph (stub model): per-node — correct registry tool(s) invoked, narration contains the tool's `display` strings verbatim (assert figure provenance), similar-case staleness disclosure fires past threshold and not before, fraud low-risk variant, `rtw` streams a draft and proposes no write (`pending_approval` stays None)
  - [ ] Tool-failure test: forced `ok: false` → structured message in stream + terminal event + nothing raw
  - [ ] E2E: `e2e/stories/6-4-deterministic-quick-actions-qas.spec.ts` tagged `@story:6-4 @epic:6`, one `@smoke` happy path — handler clicks a quick action against the model-stub and a structured answer streams to `done`; cover at least one `requires_llm: false` action and the stream-event sequence; assert structure, never prose (AD-15)

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story makes the copilot's routine work **deterministic**: the static 7-key routing map, the QAS nodes over registered read tools, per-key prompts, and the buttons. It is **not**: the graph/threads/SSE/panel shell (6.3 — must be done first); the RTW letter's modal, interrupt gate, or save (6.5 — this story's `rtw` node is deliberately draft-only, a scope seam ruled at story creation: the epics' 6.4 AC has all 7 nodes executing, while the modal + `interrupt()` + write belong to 6.5's ACs); degradation behavior when Ollama is down (6.6 — but the `requires_llm` flags this story declares are exactly what 6.6 gates on). The prototype's `QAS` array and `offlineAnswer` switch are the behavioral reference for *content*, and the banned pattern for *mechanism*, respectively — every answer here is live tool output + live narration, never canned text (AD-14).

### Architecture compliance (binding ADs for this story)

- **AD-14:** static key→node map before any LLM call; only free text reaches the LLM router; each entry declares `requires_llm`.
- **AD-2:** figures come from tools; the reserve node narrates `reserveCheck` output — no figure originates in a prompt or completion.
- **AD-13:** nodes call registry entries only; one service call per tool; `{ok, data, display}`; `display` quoted verbatim; structured errors; injected caller context (an agent can never narrate another handler's claim).
- **AD-12:** similar-case answers disclose staleness beyond the configured `embedded_at` threshold.
- **AD-7:** labor-law and similar-case retrieval ride the scoped repositories from 6.1.
- **AD-6:** all traffic stays inside the one compiled StateGraph; state changes only via `agents/state.py`.
- **AD-11:** failures and runs logged content-free.
- **AD-15:** story spec with `@smoke`; copilot specs assert structure, not prose.

### Data notes

No new tables. New config knob: `embedding_staleness_threshold`. Reads via registry tools over: `services/rag` (knowledge + similar-case), `services/financials` (reserve check), `services/worklist` (next actions), `services/derivations` (fraud score/flags), `services/claims` (claim reader for RTW merge fields). All writes: none (the `rtw` draft is transcript-only; `pending_approval` remains untouched until 6.5).

### UX notes

- UX-DR8: 7 quick-action buttons in the copilot panel; keep the prototype's icon + terse-label treatment (§ ↻ 📄 ✓ 🔍 ! 📊) and the disclaimer on labor-law output.
- UX-DR11/NFR-3: structured error messages render inline in the chat, never dialogs.
- UX-DR12: Story 1.1 light-palette tokens (canonical; "dark console aesthetic" is the documented discrepancy).
- FR-CP-1 is this story's FR: 7 claim-contextual quick actions, deterministic, honest.

### Testing requirements

- The key-by-key routing unit test is an explicit AC — one assertion per key, plus free-text fall-through and unknown-key rejection.
- Figure-provenance assertions (narration contains `display` verbatim) are the AD-2 enforcement tests.
- Stub chat model everywhere; `@live-ai` for anything needing a real model, excluded from the gate.
- Playwright: `e2e/stories/6-4-deterministic-quick-actions-qas.spec.ts` tagged `@story:6-4 @epic:6`, exactly one `@smoke` happy path against the model-stub. Story cannot move to `review`/`done` until it passes (AD-15 done-gate).

### Project Structure Notes

- All server work extends 6.3's modules: map + nodes in the graph module(s) under `agents/`, tools in `agents/tools/`, prompts in `agents/prompts/`; UI change is confined to `web/src/features/copilot/`.
- Do not restructure 6.3's entry node — populate its dispatch hook. Keys are the stable contract: 6.5 reuses `rtw`, 6.6 gates on the map's `requires_llm` flags, and prompt files are keyed 1:1 to them.
- Depends on 6.1 (retrieval + stub), 6.2 (fraud/low-risk shape parity), 6.3 (graph, registry, stream, panel).

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 6.4]
- AD-14 full text (static map, requires_llm); AD-2; AD-13; AD-12 staleness: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-14 / #AD-2 / #AD-13 / #AD-12]
- Router topology + named routes + "prompt templates carry over nearly verbatim": [Source: docs/Architecture-LINEWORKER.md#5.2]
- Prompts convention (QAS keys map 1:1 to prompt files); Testing convention (routing-map unit tests): [Source: ARCHITECTURE-SPINE.md#Consistency Conventions]
- FR-CP-1: [Source: epics.md#Requirements Inventory]
- Prototype QAS buttons + prompts (content reference; `offlineAnswer` banned): [Source: docs/Workers_Comp_Prototype.html (QAS array ~renderCP; offlineAnswer ~line 1533)]
- RTW seam to 6.5: [Source: epics.md#Story 6.5 (📄 Review RTW Policy AC)]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
