---
title: 'Story 6.4 — Deterministic Quick Actions (QAS)'
type: 'feature'
created: '2026-08-20'
baseline_revision: '37f56be723d5291b7c8a379c40adcf7fc9390084'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: true # the fix pass reopened an AD-16 containment surface (untrusted knowledge_chunk metadata reaching model context unfenced, in the section the prompt grants quoting authority) and rewrote a financial-narration branch that had been asserting a verdict the service never returned — 3 high and 8 medium fixes across security, correctness and frontend concurrency, two of them in code whose own test was passing against the defect. The routing map, the registry injection and the prompt composition are settled; the metadata normalisation, the reserve verdict branches and the quick-action key handoff are what a second pass should read
context:
  - '{project-root}/_bmad-output/implementation-artifacts/6-4-deterministic-quick-actions-qas.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-6-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-6-3-copilot-chat-with-persistent-threads.md'
  - '{project-root}/docs/Architecture-LINEWORKER.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The copilot answers free text and nothing else. `agents/graph.py::QUICK_ACTIONS` is an empty map, the ⚡ Actions tab has no buttons, and the seven questions a handler asks every day would therefore reach an LLM router that may answer them differently each time. The prototype answered them from canned `offlineAnswer` text, which AD-14 bans by name.

**Approach:** Fill the dispatch hook Story 6.3 shipped. Seven snake_case keys, each mapping to one node that calls registered read tools for its facts and uses the chat model only to narrate them; seven buttons in the Actions tab that ride the existing SSE run path with a `quickAction` key on the request body. Routing is a dict lookup that happens before any model call, so a quick action is identical every time because its key routed it — not because a router model agreed with itself.

## Boundaries & Constraints

**Always:**
- The key→node map is one importable structure, consulted **before any LLM call**. Every entry declares `requires_llm: bool`. Only free text reaches the `create_agent` router (AD-14).
- Nodes call registry entries through `agents/registry.invoke`, never services directly (AD-13). Every tool wraps exactly one service call.
- The claim a node asks about comes from `CopilotContext.claim_business_id`, never from the model and never from message text (AD-16).
- Figures come from tools. Prompts instruct the model to quote `display` strings verbatim; no node prompt states a money amount, a date or a count (AD-2).
- Retrieved chunks and claim narrative enter model context per-item fenced and source-tagged via `agents/fencing.fence`, under `MATERIAL_HEADING`; computed figures go unfenced under `FIGURES_HEADING` (the `agents/insights.py::_narrate` shape).
- Fenced strings are model-context only. Nothing carrying `ITEM_OPEN` may reach the transcript.
- Prompts are versioned files in `agents/prompts/`, loaded by key, keyed 1:1 to QAS keys. No inline prompt literals in node bodies.
- A node holds no DB session across a model call — each registry call opens and closes its own.
- Every run still ends with exactly one terminal stream event.
- Logs carry ids, keys and event names only (AD-11).

**Block If:**
- Registering `similar_cases` or `labor_law_search` cannot be done without putting a caller-, scope- or threshold-naming field on a model-facing argument schema.
- The pinned `create_agent` / LangGraph version cannot stream `messages` frames from a plain (non-agent) node, so a `requires_llm: true` QAS node cannot stream its narration.

**Never:**
- No `interrupt()`, no `pending_approval`, no write tool, no `kind: write` registration. The `rtw` node drafts into the transcript and proposes nothing (Story 6.5 owns the modal, the gate and the save).
- No dashboard-scope keys, no aggregate tools, no per-key degradation disabling (6.6), no answer-quality evaluation.
- No second staleness threshold, and no re-derivation of the disclosure sentence outside `agents/tools/similar.py`.
- No canned text presented as model output. A `requires_llm: false` route is a declared deterministic note, never an answer attributed to the model.
- No restructuring of 6.3's entry node, stream path, thread minting or single-flight guard.
- The model never picks a verdict, a variant, a ranking or a route.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Any of the 7 keys clicked | `quickAction: "<key>"` + the button's label as `message` | Entry router dispatches straight to that key's node; `updates` frames name `entry_router` then that node; run ends `done` | No error expected |
| Free text | `message` only, `quickAction` absent | `quick_action` cleared to `None`, route is `grounded_chat` | No error expected |
| Unknown key | `quickAction: "laborlow"` | 422 problem+json before a thread is touched — the structured rejection | Nothing checkpointed, no model call, no terminal stream |
| `reserve` | claim with bills on file | Narration quotes every `display` string from `reserve_check` verbatim; verdict is the service's | Tool `ok:false` → structured message + `done` |
| `reserve` | claim with no bills (`indeterminate`; `ratio_bp`/`remaining_medical_cents` are `None`, keys absent from `display`) | Narration says the medical exposure is not yet on file | Never renders `$0` for an absent key |
| `fraud` | `siu_review` and `fraud_flagged` both false | Low-risk confirmation variant, chosen in Python from the envelope | — |
| `fraud` | either derivation true | Red-flag variant | — |
| `similar` | some neighbours stale past `insight_staleness_disclosure_days` | `disclosure` from the tool is included verbatim; framing says "this claim's employer", never "your caseload" | — |
| `similar` | nothing stale (`disclosure is None`) | No staleness sentence at all | — |
| `similar` | employer partition has no neighbours | Answers that there are no comparables; `ok:true` with an empty list is success | — |
| `laborlaw` | seeded `knowledge_chunk` corpus | Briefing grounded in retrieved chunks, each fenced and tagged `knowledge:{source}`, closing with "informational only — not legal advice" | Empty retrieval → says so; never invents statute |
| `data_alignment` | any claim | Deterministic provenance note from `claim_reader`'s bare scalars; **no model call** | Tool `ok:false` → structured message |
| `rtw` | any claim | Draft letter streamed as an ordinary assistant message; quotes restriction/prognosis text from tool output; names no return date | `pending_approval` stays `None`; no write proposed |
| Any node, tool returns `ok:false` | e.g. claim not in caller's book | The node streams a plain-terms message naming what failed, then `done` | Never a stack trace, never silently swallowed |
| Poisoned claim/chunk text | injection seeded in `cause` or `chunk_text` | Route unchanged, tool selection unchanged, scope unchanged; payload appears only inside a fence | Containment, not detection |
| Button clicked while a run is in flight | `busy || running` | Buttons disabled; no second POST | Avoids the single-flight 409 |

</intent-contract>

## Code Map

**Server — the map and the nodes**
- `lineworker/server/agents/graph.py` -- `QUICK_ACTIONS` becomes `Mapping[str, QuickAction]` where `QuickAction` is a frozen dataclass carrying `node`, `requires_llm` and the optional `prompt_key`. `route_entry` returns `QUICK_ACTIONS[key].node`; the conditional-edge path map reads `.node`. `Route` (line ~318) widens from `Literal["grounded_chat"]` to include the seven node names. `build_graph` already takes `model` — the QAS nodes get it by closure; that is the seam, and no node constructs a model.
- `lineworker/server/agents/qas.py` -- **new**; the seven nodes and the shared narrate helper. One module so the map, the nodes and their prompt keys are read together. The helper builds the two-section user message (`FIGURES_HEADING` unfenced, `MATERIAL_HEADING` fenced) and streams the model; `insights.py::_narrate` is the shape, minus the structured-output half — QAS streams prose.
- `lineworker/server/agents/state.py` -- **no change expected.** `quick_action` and `route` are already declared. If a node genuinely needs a channel, it is added here, in both `CopilotState` and `CopilotAgentState`, by review.

**Server — tools**
- `lineworker/server/agents/registry.py` -- register `similar_cases` (the entry `registry.py:174-187` says belongs to this story) and a new `labor_law_search`. Both need dependencies that are **not** model-suppliable, so `RegisteredTool` gains a declarative way to name context-injected keyword arguments and `invoke` supplies them from `CopilotContext`. Nothing naming a caller, employer, role or threshold may appear on any `args_schema` — `tests/test_copilot_graph.py:462` enforces the field-name ban and must keep passing.
- `lineworker/server/agents/tools/knowledge.py` -- **new**; wraps exactly one call, `services.rag.search_knowledge`, returning `ToolResult[KnowledgeHits]`. `KnowledgeHit` carries no `embedded_at`, so there is no staleness path for the corpus and none is invented. Its argument schema **subclasses `ClaimArgs`** and adds `query_text` — `tests/test_copilot_graph.py:447-459` asserts every registered schema is a `ClaimArgs` subclass, and inheriting keeps that green while putting the search under the same claim-confinement check every other tool gets. `k` is a module constant, not an argument.
- `lineworker/server/agents/tools/similar.py` -- **unchanged.** Registered as-is, `subject_scoped_context` narrowing included.
- `lineworker/server/agents/tools/rtw.py` -- **new**; wraps the same single `services.claims.detail.claim_detail` call `claim_reader` uses and projects the RTW-relevant fields: `injury.contraindications`, `injury.prognosis.rtw`, and `overview.return_status` where the stage variant carries it. Human-written strings are fenced exactly as `claim.py::claim_context` fences its five.
- `lineworker/server/agents/context.py` -- `CopilotContext` gains `embedding_client: EmbeddingClient` and `embedding_staleness_days: int`, built in `lifespan` and passed per run. Still never checkpointed.

**Server — prompts**
- `lineworker/server/agents/prompts/{laborlaw,similar,reserve,fraud,nextactions,rtw}.md` -- **new**, six files, stem == QAS key, `<!-- prompt: <key> v1 -->` header. Content reference is the prototype's `QAS[].pr` templates, carried over as *instructions to narrate tool output* rather than as questions containing claim data. `data_alignment` gets none — it makes no model call.
- `lineworker/server/agents/prompts/__init__.py` -- a `qas_system_message(key)` composing `copilot_system.md` with the QAS prompt, parallel to `system_message`. It must compose on `copilot_system.md` and **not** `system.md`, which ends "Answer only with the JSON object matching the schema you were given".

**Server — API**
- `lineworker/server/api/routers/copilot.py` -- `RunRequest` (`:486`, `extra="forbid"`) gains `quick_action`. Validate against the map's keys and 422 on an unknown one — that is the structured rejection AC 1 asks for; `route_entry`'s fall-through to chat stays as the in-graph net. `quick_action` with `command` is refused like the existing both-keys case. The key is passed to `run_inputs`, which already accepts it.
- `lineworker/server/api/app.py` -- `lifespan` builds the `EmbeddingClient` (`services.rag.embedding_client`) once and puts it plus `settings.insight_staleness_disclosure_days` on the per-run `CopilotContext`.
- `lineworker/server/config.py` -- **no new knob.** See Design Notes.

**Web**
- `lineworker/web/src/features/copilot/quickActions.ts` -- **new**; the UI-owned `Record<QuickActionKey, {icon, label, sublabel}>` with the seven glyphs `§ ↻ 📄 ✓ 🔍 ! 📊`. A `Record` over the key union so an added key is a build error (`features/claim-detail/documents/pathMeta.ts` is the exemplar). The emoji never crosses the wire — the Enums convention.
- `lineworker/web/src/features/copilot/QuickActions.tsx` -- **new**; the button strip, rendered between the greeting `<p>` and the scroller `<div>` in `ActionsTab`, matching the prototype's placement. Raw `<button>` with token classes per `features/diary/EmailComposerDialog.tsx:539-557`, not the shadcn `Button`. Do not copy `ActionsTab`'s `text-er`/`text-wn` — those tokens do not exist.
- `lineworker/web/src/features/copilot/ActionsTab.tsx` -- mount the strip; hide it when `readOnly`, disable on `busy || running`. `send()` gains the key so the run body becomes `{message, quickAction}`. The key must reach `streamRun`'s body at line ~237, not just the runtime config, which the `stream` callback discards today.
- `lineworker/web/src/api/schema.d.ts` -- regenerate and commit; CI fails on a diff, and the client cannot send `quickAction` until the server model lands.
- `lineworker/web/src/test/api-mock.ts` -- let the `copilotRun` stub capture the POST body so a test can assert the key that was sent.

**Tests**
- `lineworker/server/tests/test_copilot_qas.py` -- **new**: the key-by-key routing table (one assertion per key, asserting the scripted model was never called), unknown key, free-text fall-through, `requires_llm` declared on every entry; per-node tool invocation and `display`-verbatim provenance; the fraud low-risk and red-flag variants; staleness disclosure firing past the threshold and not before; `rtw` streams a draft and leaves `pending_approval` `None`; the forced `ok:false` tool failure yielding a structured message and one terminal.
- `lineworker/server/tests/test_copilot_graph.py` -- **amend** `test_the_quick_action_map_is_empty_in_this_story` into its opposite (AD-15: amended, never deleted); the `Route` literal and path-map assertions follow.
- `lineworker/server/tests/test_ai_insights.py` -- **amend** the prompt-file set equality at `:184` to admit the six new stems.
- `lineworker/server/tests/test_prompt_injection_fixtures.py` -- **amend** `test_the_injection_changes_no_route_and_reaches_no_write_tool`: a poisoned message must not select a quick action, and a poisoned `knowledge_chunk` retrieved by `laborlaw` must not change route, tool selection or scope. The `all(entry.kind is ToolKind.read)` assertion binds the two new tools.
- `lineworker/server/tests/test_copilot_logging.py` -- **amend**: a QAS run's prompt body, narration and retrieved chunk text reach no log line; positive control on a QAS event name.
- `lineworker/web/src/features/copilot/QuickActions.test.tsx` + `ActionsTab.test.tsx` -- the seven buttons render with their glyphs, are disabled while a run is in flight, absent on a read-only thread, and a click POSTs the right `quickAction`.
- `lineworker/e2e/stories/6-4-deterministic-quick-actions-qas.spec.ts` -- **new**, `@story:6-4 @epic:6`, exactly one `@smoke`. Filename must match `stories/6-4-*.spec.ts` and the branch name must contain `6-4`, or CI's grep derivation silently runs the whole suite. Cover one `requires_llm: true` action streaming to `done` and the `data_alignment` action, asserting the event sequence and structure — never prose.

## Tasks & Acceptance

**Execution:**
- [x] `agents/graph.py` -- turn `QUICK_ACTIONS` into a typed map of entries declaring `node`, `requires_llm` and `prompt_key`; widen `Route`; wire the seven nodes into the path map -- one importable structure is what 6.6 gates on and what the routing test enumerates; two structures would let the map and the graph disagree.
- [x] `agents/context.py` + `api/app.py` -- add the embedding client and the staleness threshold to `CopilotContext`, built once in `lifespan` -- they are dependencies, not arguments; an args-schema field here would be a model-suppliable knob and AD-16 forbids it.
- [x] `agents/registry.py` + `agents/tools/knowledge.py` + `agents/tools/rtw.py` -- register `similar_cases`, `labor_law_search` and the RTW reader with context-injected dependencies -- `registry.py:174-187` names this story as the one that registers `similar_cases`; the field-name ban test must stay green.
- [x] `agents/prompts/*.md` + `prompts/__init__.py` -- six versioned QAS prompt files and the composition helper -- compose on `copilot_system.md`; `system.md` demands JSON-only output and would silently break streamed prose.
- [x] `agents/qas.py` -- the seven nodes and the shared narrate helper -- figures unfenced under `FIGURES_HEADING`, untrusted text fenced under `MATERIAL_HEADING`, verdict and variant chosen in Python before the model sees anything.
- [x] `agents/qas.py` (`laborlaw`) -- compose the retrieval query from `claim_reader`'s bare scalars only, never from its fenced narrative -- the fenced fields carry delimiter markup and are unusable as a query; see Design Notes for why the thin query loses nothing.
- [x] `agents/qas.py` (`data_alignment`) -- the deterministic note from `claim_reader`'s **bare scalars only** -- the `narrative` tuple is fenced model-context text; rendering it would put `<<<LINEWORKER-ITEM` in the handler's transcript.
- [x] `api/routers/copilot.py` -- `quick_action` on `RunRequest`, validated against the map, 422 on unknown, refused alongside `command` -- `extra="forbid"` means the client 422s until this lands, so it is the first half of the vertical slice.
- [x] `web/src/api/schema.d.ts` -- regenerate and commit -- CI fails on a diff.
- [x] `web/src/features/copilot/quickActions.ts` + `QuickActions.tsx` + `ActionsTab.tsx` -- the UI key→label map, the button strip, and the key on the run body -- the glyphs stay client-side per the Enums convention; the key must reach `streamRun`'s body, not only the runtime config the `stream` callback discards.
- [x] `server/tests/test_copilot_qas.py` -- the routing table, the per-node provenance and variant tests, and the tool-failure test -- the key-by-key assertion is an explicit AC, and "no LLM router was involved" is the half that a passing route test can otherwise assert vacuously.
- [x] `test_copilot_graph.py`, `test_ai_insights.py`, `test_prompt_injection_fixtures.py`, `test_copilot_logging.py`, `web/.../ActionsTab.test.tsx`, `test/api-mock.ts` -- amend every assertion this story falsifies -- AD-15: amended into its opposite, never deleted.
- [x] `e2e/stories/6-4-deterministic-quick-actions-qas.spec.ts` -- the story done-gate -- one `@smoke`, structure only, an idempotent per-test helper rather than test ordering.

**Acceptance Criteria:**
- Given the seven quick-action keys, when each is routed, then it reaches exactly its own node, the scripted chat model records no router call, and every map entry declares `requires_llm` — asserted once per key.
- Given a free-text message, when it runs, then `quick_action` is `None` and the route is `grounded_chat`; given an unknown key, the run is refused 422 before any thread state changes.
- Given any `requires_llm: true` node, when it answers, then every money amount, date and count in its narration is a `display` string its tool returned, and the node's prompt file contains no figure.
- Given the `similar` action against neighbours older than the configured threshold, when it answers, then the tool's own `disclosure` sentence appears verbatim and the answer names this claim's employer rather than the caller's caseload; given nothing stale, no staleness sentence appears.
- Given the `laborlaw` action, when it answers, then it is grounded in retrieved `knowledge_chunk` content, each chunk individually fenced and tagged with its source id, and the answer carries the not-legal-advice disclaimer.
- Given a knowledge chunk or claim field seeded with injection text, when a quick action puts it in model context, then the route, the tool selection and the scope are unchanged and no write tool is reachable.
- Given a tool that returns `ok: false`, when a node handles it, then a plain-terms message naming the failure is streamed, exactly one terminal event follows, and no stack trace or raw exception text appears in the transcript or the log.
- Given the `data_alignment` action, when it runs, then no model call is made, the note contains no fence markup, and the run still terminates `done`.
- Given the `rtw` action, when it runs, then a draft streams as an ordinary assistant message, `pending_approval` is `None`, no write tool is invoked, and no return date the tools did not supply appears.
- Given a run in flight, when the handler clicks a quick action, then the buttons are disabled and no second run is posted.
- Given the full CI gate, when it runs, then ruff, mypy strict, pytest (including DB-backed), `alembic check`, vitest, the `schema.d.ts` diff check, the compose port check and the Playwright suite are all green.

## Spec Change Log

## Review Triage Log

### 2026-08-20 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 16: (high 3, medium 8, low 5)
- defer: 1: (high 0, medium 0, low 1)
- reject: 0
- addressed_findings:
  - `[high]` `[patch]` Untrusted corpus metadata reached the model unfenced, inside the half the prompt grants quoting authority. `knowledge_chunk.source` and `state_code` are unbounded `Text` columns an ingestion path outside this console fills; `knowledge.py` fenced a passage's `text` but published both raw, and the labour-law node interpolated them under `FIGURES_HEADING`. `fence()` scrubs the tag it derives from `source` for exactly this reason. The repo already held the exploit — `POISONED_SOURCE`, written to `chunk.source` by an existing fixture. Both fields are now normalised at publication, so the published value and the delimiter tag are one string by construction and the grounded-chat `ToolMessage` path is covered too. Review-of-6.2 finding M1, reintroduced one layer up.
  - `[high]` `[patch]` The new containment test asserted on the one field that was already safe — `item["text"]` and the fence tag, never `item["source"]` — while its own docstring said the fixture poisoned both. It passed against the defect above. Now asserts over `source` and `state_code`, with a non-vacuity check that the poisoned passage was actually retrieved; verified to fail against the unfixed code.
  - `[high]` `[patch]` The reserve node stated a verdict it never read: its `else` branch asserted "the verdict says the reserve cannot be judged" whenever `remainingMedicalCents` was absent, but `reserve.py:397` returns `light` in that state when the known lower bound already clears the band, and `closed_final` when settled. An intake claim with no bills and indemnity above the light band got `verdict: light` and "cannot be judged" in the same authoritative block. Verdict clause now conditioned on `indeterminate`; regression test added.
  - `[medium]` `[patch]` A blank narration was checkpointed as a turn — an empty completion, or one yielding non-`str` content blocks, left an empty `AIMessage` and terminated `error` after the node had succeeded. This is the class Story 6.3's review fixed on the free-chat path. Now a plain note, with a log line and a test driving a silent model.
  - `[medium]` `[patch]` Fifteen tests errored instead of skipping without a database: six functions took the `engine` fixture with no `@requires_db`, breaking the suite's established contract for anyone without Postgres. Confirmed by running with the variable unset; now 0 errors.
  - `[medium]` `[patch]` `Route` was dead and its docstring claimed a test guarded it — it appeared only at its definition and in `__all__`, and `route_entry` was annotated `-> str`. The claim is now true: `route_entry` returns `Route`, and a test asserts the literal's members equal the graph's destinations.
  - `[medium]` `[patch]` Two quick actions contradicted each other on the same claim: `rtw` stated as fact "no return date anywhere on this claim record and no tool can supply one", while `services/worklist/actions.py::_overdue_rtw` reads `rtw_rec`/`actual_rtw` and can tell the handler the date is overdue. Narrowed to what is true — no reader available to this action projects one — in the node, the tool, the prompt and the deferred-work entry.
  - `[medium]` `[patch]` Every prompt instructed the model to use a `MATERIAL TO ANALYSE` section that can be absent, with nothing saying so. Worst case was `rtw`, told to quote medical restrictions for a letter addressed to an injured worker when there might be no such section — the hallucination this story exists to prevent. The figures now state explicitly when no material section is present, and a declined `claim_reader` is reported rather than silently swallowed.
  - `[medium]` `[patch]` The legal disclaimer was a request with no enforcement — required by Task 2, asked for in the prompt, asserted nowhere, and unassertable through the hash-derived model stub. Now appended in Python for the `laborlaw` node, onto both the stream and the checkpointed message, guarded against duplication and covered by a unit test.
  - `[medium]` `[patch]` The quick-action key handoff could carry the wrong key. `stream` is an async generator function, so calling it constructs the generator without running a line; the singleton ref was read an arbitrary time later, and `disabled={busy || running}` only takes effect after React commits, so two clicks in one tick both reached `send`. Run A could carry run B's key — a determinism break, which is this story's whole subject. Fixed with a synchronous guard and a FIFO queue; the new test fails without the guard.
  - `[medium]` `[patch]` AC cross-references were fiction: tests cited AC 7, 8 and 9 against a five-AC story, and mislabelled three others, so an acceptance audit would have reported coverage that did not exist. Corrected; all five ACs are now named by at least one test.
  - `[low]` `[patch]` `resume_inputs` did not clear `quick_action` though `run_inputs` clears it explicitly for the last-write-wins reason, and `state.py` documented the invariant as if it held everywhere. Story 6.5 raises the first interrupt against exactly this path.
  - `[low]` `[patch]` `assert "$" not in emitted` was an assertion about the whole of stderr rather than about the prompt; one unrelated future log line containing `$` would have failed it claiming a money figure leaked. Scoped to the money strings the run's own reserve check produced.
  - `[low]` `[patch]` `ToolDependencyMissing` did not catch the failure its own docstring named: only `KeyError` was caught, so renaming a `CopilotContext` field raised a bare `AttributeError` and surfaced as a generic `error` frame.
  - `[low]` `[patch]` The strip and the composer disagreed about "busy" — during a copilot write in flight the buttons greyed out and the text box did not.
  - `[low]` `[patch]` Two docstrings said things that parsed to nothing: `RunRequest`'s "seventh key that is not absent", and `_validate_run_body`'s "Six refusals" counting a branch reachable only as the residue of another.

## Design Notes

**No new staleness knob, and this is a deliberate deviation from the story.** The story's Data notes ask for `embedding_staleness_threshold`. `config.py:333` already has `insight_staleness_disclosure_days`, whose own comment states it *is* AD-12's configured threshold for the similar-case answer. The disclosure sentence interpolates the number — "last indexed more than N days ago" — so two knobs would let the Insights card and the copilot chat tell the same handler two different numbers about the same neighbour in the same session. AD-12 says "a configured threshold", singular. The existing knob is reused as-is; renaming it to something kind-neutral is a larger blast radius (three call sites, the env file, its tests) for no behavioural gain, and is recorded rather than done.

**Why `data_alignment` is not the banned pattern.** AD-14 bans `offlineAnswer`: canned text *presented as model output* while a live model was claimed. A `requires_llm: false` route is the opposite — it declares up front that no model is involved, and its content is assembled in committed code from a registered tool's output about this claim. It is also the false path 6.6 needs to gate on, which is why the story asks for one.

**Two sections, one shape, borrowed from 6.2.** `agents/insights.py::_narrate` puts computed figures unfenced under `FIGURES_HEADING` and human-written text fenced under `MATERIAL_HEADING`, in the user message, with the system message coming only from prompt files. QAS nodes reuse that composition exactly; what they do not reuse is the structured-output half, because a quick action streams prose. The 6.2 prompt files are unusable here for the same reason: they compose on `system.md`, which ends by demanding a JSON object.

**The verdict is never the model's.** Two precedents bind. `fraud` computes `elevated = signals.siu_review or signals.fraud_flagged` in Python and lets it pick the prompt variant, exactly as `insights.py:441` does. `reserve` narrates whichever of the five `ReserveVerdict` members the service returned — `light | adequate | heavy | closed_final | indeterminate`, not the three the story names. `indeterminate` means no bills are on file, and the `display` map then omits `remainingMedicalCents`, `projectedRemainingCents` and `ratioBp` precisely so there is nothing to quote; the prompt must read an absent key as "not on file" and never as `$0`.

**The RTW draft names no date.** `claim_reader` carries no RTW fields, and `Claim.rtw_rec` / `Claim.actual_rtw` are on the ORM model but on no `ClaimDetail` field, so no tool can supply a return date without a services-layer change this story has no standing to make. `claim_detail` does already carry `injury.contraindications`, `injury.prognosis.rtw` and `overview.return_status`, so the draft quotes those. AD-2 forbids the model originating the date, and a letter that omits it is honest where one that invents it is not; the gap is recorded as deferred work for 6.5.

**Where the labor-law query text comes from, and why it is thin.** `claim_reader` returns `injury_type`, `cause` and `icd + body_part` only as `fence()` strings — delimiter markup included — because they are human-written. They are therefore unusable as a search query, and unwrapping a fence to get the raw value back would defeat the fence. So the `laborlaw` node composes its query from `claim_reader`'s **bare scalars** (`state`, `stage`, `risk`, `severity_score`), which is the half that actually selects the right chunks: the corpus is chunked by jurisdiction and `KnowledgeHit` carries `state_code`. Injury specificity is not lost — the fenced claim material travels in the same user message under `MATERIAL_HEADING`, so the model narrates the retrieved statutes against the injury without any claim text having steered retrieval. This keeps the AD-16 property exact: untrusted content shaped no query, selected no tool and named no scope.

**Fenced text is model-context only.** `claim_reader`'s `narrative` tuple is five `fence()` strings carrying `<<<LINEWORKER-ITEM source="...">>>` delimiters. They exist to keep untrusted claim text separable inside a prompt. A node that rendered one into the transcript would show a handler the delimiter markup and would defeat the fence's only purpose. `data_alignment` is the node most at risk, because it is the one that formats tool output directly for the reader.

## Verification

**Commands:**
- `cd lineworker/server && uv run ruff check . && uv run ruff format --check .` -- expected: clean.
- `cd lineworker/server && uv run mypy .` -- expected: clean under strict.
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker uv run pytest` -- expected: all pass, DB-backed modules not skipped.
- `cd lineworker/server && uv run alembic upgrade head && uv run alembic check` -- expected: "No new upgrade operations detected"; this story adds no migration, so a proposed operation means an accidental model change.
- `cd lineworker/web && npm run generate:api && git diff --exit-code -- src/api/schema.d.ts` -- expected: no diff after committing.
- `cd lineworker/web && npm run lint && npm run typecheck && npm test` -- expected: clean.
- `cd lineworker && docker compose -f deploy/compose.e2e.yaml up -d --build --wait` -- expected: all services healthy including `model-stub`.
- `cd lineworker/e2e && npm run typecheck && npx playwright test --grep "@story:6-4\b|@story:6-3\b"` -- expected: pass; the 6.3 spec must pass alongside the new one.
- `cd lineworker && docker compose -f deploy/compose.e2e.yaml config --format json > /tmp/e2e.json && python3 ../.github/scripts/check_model_ports.py /tmp/e2e.json` -- expected: pass; no new service published.
- `cd lineworker/server && grep -rn "ITEM_OPEN\|LINEWORKER-ITEM" agents/qas.py` -- expected: no occurrence outside a fence construction; nothing formats a fenced string for the reader.

**Manual checks (if no CLI):**
- Seven buttons appear in the ⚡ Actions tab above the transcript, each with its glyph; all seven grey out while an answer is streaming and none is present on a prior read-only thread.
- Clicking ✓ Reserve review streams an answer whose dollar figures match the Reserve card on the same claim, character for character.
- Clicking 📊 Data alignment note answers with no model running (stop the model container and it still answers).

## Auto Run Result

Status: done

### What was implemented

The seven deterministic quick actions, filling the dispatch hook Story 6.3 shipped empty. A key
arrives on the `quick_action` channel, a static dict lookup picks its node **before any model call**,
and that node calls registered read tools through `agents/registry.invoke` for its facts. Six keys
narrate — figures unfenced under `FIGURES_HEADING`, untrusted text fenced per item under
`MATERIAL_HEADING`, system message from a versioned prompt file and nothing else — and stream token
by token. The seventh, `data_alignment`, calls no model at all and is the `requires_llm: false` path
Story 6.6 gates on. Seven buttons in the ⚡ Actions tab ride the existing SSE run path with a
`quickAction` key on the body; no new transport.

Every verdict, variant and ranking is chosen in Python before the model sees anything: the reserve
node narrates whichever of the five `ReserveVerdict` members the service returned, and the fraud
node's `elevated = siu_review or fraud_flagged` picks the prompt variant, so the model is
structurally unable to decide either. All seven registry entries are `kind: read` — no write tool,
no `interrupt()`, `pending_approval` untouched — so the `rtw` node drafts into the transcript and
proposes nothing, which is the seam Story 6.5 takes up.

Three things the plan did not anticipate. A model-free node had no way to reach the wire, because
`messages` stream mode carries only model tokens and the terminal decision requires assistant
content; `custom` was added to the stream modes and a note payload is translated into an ordinary
`messages` frame, which also became the channel every node's tool-failure sentence uses. The
labour-law retrieval query could not be built from `claim_reader`'s narrative, because those fields
are `fence()` strings carrying delimiter markup — so the query is composed from bare scalars and the
injury text travels as fenced material instead. And `remainingMedicalCents` and `ratioBp` do not
travel together: a `closed_final` claim has its bills on file and no ratio, which raised a `KeyError`
inside the node on the first settled claim it met.

### Files changed

35 files, ~4,200 lines added across the tracked diff and the new modules. The shape of it:
- **Agents** — `qas.py` (the seven nodes, the shared narrate helper, the note and failure paths),
  `tools/knowledge.py`, `tools/rtw.py`, and six versioned prompt files keyed 1:1 to the QAS keys.
- **Graph and registry** — `QuickAction` and the filled `QUICK_ACTIONS`, `Route` widened and now
  actually enforced, `INJECTED` context-injected tool dependencies with `ToolDependencyMissing`, and
  three newly registered read tools including `similar_cases`, which 6.3 left unregistered by name
  for this story.
- **API** — `quick_action` on `RunRequest`, validated against the map and refused 422 before a
  thread is touched; the embedding client and staleness threshold onto `CopilotContext` in
  `lifespan`.
- **Web** — `quickActionMeta.ts`, `QuickActions.tsx`, and `ActionsTab` carrying the key to the wire
  through a FIFO queue behind a synchronous send guard.
- **Tests** — `test_copilot_qas.py` (the key-by-key routing table, provenance, both fraud variants,
  the reserve edge states, staleness firing and not firing, per-node tool failure), one e2e spec,
  two web suites, plus AD-15 amendments to `test_copilot_graph.py`, `test_ai_insights.py`,
  `test_prompt_injection_fixtures.py`, `test_copilot_logging.py`, `ActionsTab.test.tsx` and
  `api-mock.ts`.

### Review findings

Two independent adversarial reviewers over the full diff. **16 patched** (3 high, 8 medium, 5 low),
**1 deferred**, **0 rejected**, no intent gaps and no spec-level defects — the spec had stated every
violated invariant correctly, so there was nothing to amend and no case for re-derivation.

The three high-severity fixes: untrusted `knowledge_chunk` metadata reached the model unfenced
inside the section the prompt grants quoting authority, with the exploit payload already sitting in
an existing fixture; the new containment test asserted on the one field that was already safe and
would have passed against that defect; and the reserve node asserted "the verdict says the reserve
cannot be judged" in two states where the service had in fact returned `light` or `closed_final`.

Worth recording, because it is the same lesson Story 6.3 wrote down: the containment test named the
right property, ran against the right fixture, and read the wrong field. It was green throughout
while the surface it existed to guard was open. The two reviewers also disagreed about the frontend
key handoff — one cleared it after checking that no key survives to the next turn, the other found
that two clicks inside one render tick interleave — and the pessimist was right, because `stream` is
an async generator whose body does not run when it is constructed.

### Verification performed

Every gate re-run independently after the fix pass, not taken on report:

| Gate | Result |
|---|---|
| ruff check + format | clean, 272 files |
| mypy strict | no issues, 259 files |
| pytest (DB-backed) | 2674 passed, 1 skipped (2617 at baseline; +57 tests) |
| pytest **without** a database | 16 passed, 39 skipped, **0 errors** (was 15 errors before the fix pass) |
| alembic upgrade → check | "No new upgrade operations detected"; this story adds no migration |
| `uv sync --frozen` | clean, 74 packages |
| web typecheck / lint / vitest | clean · 0 errors, 11 pre-existing warnings · 640 passed (630 at baseline) |
| `generate:api` idempotence | regenerating twice yields an identical hash |
| Playwright, full suite on a rebuilt stack | 202 passed (197 at baseline), including all five 6-4 tests |
| compose port check | no profile publishes a model-server port |
| fence markers in `qas.py` | only in docstring prose; nothing constructs or renders a delimiter |

The `schema.d.ts` check earned its place this pass: the P16 docstring rewrite changed the OpenAPI
description, so the committed client was stale after patching and CI's `git diff --exit-code` would
have failed on merge. Regenerated and re-verified idempotent.

### Residual risks

- **The AD-16 containment fix is newer than the surface it guards.** Normalising `source` and
  `state_code` at publication is correct and now covered by a fixture that fails without it, but the
  ingestion path that fills those columns is still deferred, so no real writer has ever exercised
  them. The scrubber and the fence are settled; the metadata normalisation is one pass old.
- **The disclaimer is appended in Python, which is honest but blunt.** A compliant completion that
  phrases the sentence differently now gets the canonical sentence appended anyway, so a briefing
  can carry a near-duplicate. Guarded only by an exact-substring check.
- **`data_alignment` is the one answer with no service behind it.** Its provenance text is committed
  prose assembled from `claim_reader`'s bare scalars, correct today and untested against a future
  claim-shape change that would leave the note describing fields that moved.
- **The frontend key handoff is fixed at two levels but tested at one.** The FIFO queue and the
  synchronous guard both address it; the new test fails without the guard, and would still pass if
  the queue regressed to a single slot while the guard held.
