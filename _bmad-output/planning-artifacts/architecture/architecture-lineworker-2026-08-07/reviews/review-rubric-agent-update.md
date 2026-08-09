# Review — ARCHITECTURE-SPINE.md agent-side update (AD-6 amended; AD-13, AD-14 added)

**Reviewer:** rubric reviewer (agent-harness weighting)
**Date:** 2026-08-07
**Artifact:** `_bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md`
**Scope:** the updated spine as a whole, judged against the six-item checklist with the agent-harness dimension weighted.

**Verdict: CONDITIONAL PASS — the agent-harness dimension is now substantially decided (topology, state schema, checkpointing, HITL, streaming, tool contract, routing, degradation are all fixed and enforceable, and AD-13/14 close the two worst seams between `agents/`, `services/rag`, and `web/features/copilot`), but two agent-side gaps remain that would let independently built units diverge: the model-call boundary for `services/rag`'s AI-insight/embedding generation contradicts the paradigm diagram, and agent testing/eval is completely silent.**

---

## Checklist walk

### 1. Fixes the real divergence points for the level below — MOSTLY YES; one real agent-side miss

What the update gets right, and these are exactly the seams three independent teams would tear:

- **Tool contract** (AD-13) — "exactly one service command or query per tool, one registry, typed Pydantic schemas, context injected by the registry" is the single most important agent-side seam. Without it the `agents/` team re-implements service logic in tool bodies and drifts from `services/`. Fixed, and fixed at the right altitude.
- **Routing determinism** (AD-14) — the static QAS key→node map *before any LLM call* prevents the `web/features/copilot` team from wiring quick actions as free-text prompts (the lazy port of the prototype's `QAS`). Fixed.
- **Degradation** (AD-14) — explicitly kills the prototype's `offlineAnswer` pattern (canned text as fake AI) and decouples claim screens from agent-runtime availability. Both are real, named failure modes of this specific rebuild.
- **UI↔agent stream contract** (AD-6 amendment) — pinning LangGraph `messages` + `updates` SSE events consumed by the assistant-ui LangGraph runtime, with interrupts on the same stream, fixes the wire contract between `agents/` and `web/features/copilot`. Combined with AD-9's "no hand-rolled fetch streaming", the seam is held from both sides. Good.
- **HITL resume path** (AD-6) — "resumes only via `Command(resume=…)`, no side-channel writes while paused, resulting write still goes through AD-4" closes the approval-bypass hole.

**Miss (agent-side):** the **model-call boundary for `services/rag`** is not fixed and the paradigm diagram actively misleads. `services/rag` "owns and refreshes solely" the `ai_insight` narratives (AD-10) and owns embeddings (AD-12); both require inference (AD-5: "all model inference *and embedding*" goes to Ollama). But the Design Paradigm diagram has exactly one edge to Ollama — `AGENT → OLLAMA` — and the dependency rule says "arrows only point downward/rightward *as drawn*". As written, `services/rag` cannot legally reach the model it needs to do the job the spine assigns it. Two teams will resolve this differently: one routes narrative generation through the copilot graph (agents generate, then call rag's write command per AD-13), the other has `services/rag` call `langchain-ollama` directly from its scheduled refresh job (which the "embedding refresh runs as scheduled jobs inside the `api` process" line already implies). See finding F1.

### 2. Every AD's Rule enforceable and divergence-preventing — AD-13/14 clean; AD-6 has one soft edge

- **AD-13 — PASS.** Every clause is a code-review predicate: "wraps exactly one service command or query" (diffable), "no composition of multiple writes inside a tool" (diffable), "all tools live in `agents/tools/`" (grep), "typed Pydantic argument schema" (grep), "context injected by the registry from graph state — never a tool parameter the model can populate" (schema inspection), "no tool imports `data/` or holds a DB session" (import lint). Each clause maps directly to a stated divergence. This is the best-written AD in the document.
- **AD-14 — PASS with one soft term.** "Static key→node map before any LLM call" — checkable. "Explicit 'AI unavailable' state", "no cloud fallback", "no pre-authored answers presented as model output", "claim screens have no hard dependency on the agent runtime" — all checkable (the last by import/route inspection). One clause is not: **"bounded retry"** names no bound and no home for the bound. Per the Config convention, the fix is one phrase: retry count/timeout live in the pydantic-settings module. See F6.
- **AD-6 (amended) — PASS with one definitional gap.** The state-schema clause ("no node defines private state channels for these"), the checkpoint key `(claim_id, user_id)`, AsyncPostgresSaver, the `interrupt()`/`Command(resume=…)` discipline, and the SSE event vocabulary are all checkable. The soft edge: the interrupt gate binds writes to a **"claim-data entity"**, defined only by one example ("e.g. save an RTW letter to documents"). Is a copilot-drafted diary note a claim-data write? A meeting? An email log? The ERD ties all three to CLAIM optionally. Two node authors will answer differently, and the answer decides whether a human gate exists. The AD-12 ownership registry is sitting right there to anchor the definition. See F3.

### 3. Could anything under Deferred let two agent-side units diverge — NO for what's listed; YES for what's absent

The listed deferrals are agent-safe: **Model pinning** is held by AD-5 (names are config, no code path depends on a tag); **Derived-flag definitions** are held by AD-10 (only the *where* is binding); the scheduler mechanism deferral is held by AD-12 ownership. None lets two agent-side units diverge.

The risk is what is *neither decided nor deferred* (rubric item 5 overlap — filed there): agent eval/testing (F2), prompt management (F4), knowledge-corpus ingestion (F5), thread lifecycle/context-window policy (F8). Silence is not a deferral; a team hitting these will invent an answer without knowing it owned a decision.

### 4. Do the new ADs contradict or weaken AD-1..12 or the Conventions — NO contradictions; two wording harmonizations

- AD-13 **strengthens** AD-2 ("agents access them only as LangGraph tools") and AD-4 (tools can't compose writes, so no un-audited multi-write). AD-14 **strengthens** AD-5 (restates no-cloud-fallback under failure) and NFR-resilience posture. The AD-6 insight-cache exemption is consistent with AD-10/AD-12: the exemption lifts the *interrupt*, not ownership — the write still goes through `services/rag`'s command via an AD-13 tool, and still audits per AD-4. Coherent.
- **Harmonize (low):** AD-7 says agents "pass it to every tool"; AD-13 says the context "is never a tool parameter" and is injected by the registry. Same intent, opposite surface reading — AD-13 is the refinement and AD-7 should point to it. See F7.
- **Latent tension sharpened, not created:** the AGENT→OLLAMA-only diagram (F1) predates this update, but AD-13's "every tool wraps exactly one service command" plus AD-10's "refreshed solely by `services/rag`" now make the narrative-generation path formally unresolvable without a decision.

### 5. Agent-harness completeness — 9 of 11 decided; 2 silent

| Dimension | Status |
| --- | --- |
| Graph topology | Decided — one compiled StateGraph, supervisor router → tool/RAG nodes (AD-6) |
| State schema | Decided — `messages`, `caller`, `claim_id`, `pending_approval`; no private channels (AD-6) |
| Checkpointing | Decided — AsyncPostgresSaver, `(claim_id, user_id)` key, PHI-class + purge cascade (AD-3, AD-6, AD-11) |
| Human-in-the-loop | Decided — `interrupt()`/`Command(resume=…)` on claim-data writes (AD-6); *entity boundary soft (F3), resume payload shape unfixed (F9)* |
| Streaming | Decided — SSE, `messages`+`updates` events, assistant-ui runtime both ends (AD-6, AD-9) |
| Tool contract | Decided — AD-13 |
| Routing | Decided — static QAS map first, LLM router for free text only (AD-14) |
| Failure/degradation | Decided — explicit unavailable state, bounded retry, no coupling to claim ops (AD-14) |
| RAG boundary | Mostly decided — ownership (AD-10/12), scoped retrieval (AD-7), tool access (AD-13); **generation path to Ollama unresolved (F1); corpus ingestion silent (F5)** |
| Prompt management | **Silent as a rule** — `agents/ … prompts (QAS keys)` appears only in the source tree; nothing prevents inline prompts in nodes or prompt forks per feature (F4) |
| Agent testing/eval | **Silent** — the Testing & CI convention covers financial property tests and web smoke tests; nothing covers routing-map tests, graph tests with a stubbed model, interrupt-path tests, or any answer-quality eval (F2) |

Also silent, lower stakes: thread lifecycle and context-window policy (F8); whether supervisor/analyst personas get the copilot at all, and what a claim-less thread keys on given `(claim_id, user_id)` (F10).

### 6. Terse build-substrate style — PASS

AD-13 and AD-14 match the house voice exactly: Binds/Prevents/Rule, prototype-artifact callouts (`offlineAnswer`, credential-less fetch) used as prevention anchors, no filler. AD-14's "honest degradation" title is in-register. The amended AD-6 is the one heavy paragraph — its Rule is a single ~110-word block carrying five distinct decisions (state schema, checkpoint key, interrupt gate, resume discipline, stream contract); density is on-style, but sentence structure is at the limit of skimmability. Cosmetic only.

---

## Findings

| # | Severity | Finding | Proposed fix |
| --- | --- | --- | --- |
| F1 | **High** | `services/rag` owns AI-insight narrative refresh and embedding generation, but the paradigm diagram's only model edge is `AGENT → OLLAMA` and the dependency rule forbids undrawn arrows — the generation path is formally impossible as written, and the two obvious resolutions (graph-generates-then-rag-writes vs. rag-calls-Ollama-directly) will be picked differently by the `agents/` and `services/rag` teams. | Decide one: add a `SVC(rag) → OLLAMA` edge for embeddings + scheduled narrative refresh (recommended — keeps the copilot graph out of batch paths), or state in AD-10/AD-12 that narratives are generated only by graph nodes and persisted via rag's command. Update the diagram and one sentence in AD-10 either way. |
| F2 | **High** | Agent testing/eval is entirely silent — no convention for routing-map tests, graph tests against a stubbed/recorded model, interrupt/resume path tests, or any eval of copilot answer quality. Three teams will ship three definitions of "the copilot works", and AD-14's determinism claims are unverifiable without them. | Add one Testing & CI convention line, e.g.: "`agents/`: pytest graph tests run with a stub model client (no Ollama in CI); the QAS key→node map, interrupt/resume paths, and tool-registry schemas are covered by unit tests; model-in-the-loop eval is a deferred epic with its own golden-transcript set." Decide the CI part; defer the eval part explicitly. |
| F3 | Medium | AD-6's interrupt gate hinges on "claim-data entity", defined only by example. Whether copilot-drafted diary notes, meetings, or emails require human approval is left to each node author. | Anchor the term to the AD-12 registry: e.g. "claim-data entity = any entity whose write-owner is `services/claims` or `services/financials`; `ai_insight`/embeddings (owned by `services/rag`) are the only exemption." One sentence in AD-6. |
| F4 | Medium | Prompt management exists only as a source-tree label (`prompts (QAS keys)`). No rule keeps prompts out of node bodies, ties QAS keys to prompt files, or versions them — the exact "pre-authored answer" drift AD-14 fights can re-enter through prompt sprawl. | Add one clause (AD-13 or a conventions row): "All prompt templates live in `agents/prompts/`, keyed by QAS key or node name, versioned in VCS; no literal prompt strings in node/tool code." |
| F5 | Medium | `KNOWLEDGE_CHUNK`/`KNOWLEDGE_EMBEDDING` appear in the ERD and labor-law RAG in the Capability Map, but corpus sourcing, chunking, and refresh ownership are neither decided nor deferred. | Add a Deferred entry: "Knowledge-corpus ingestion — labor-law source selection, chunking, and refresh cadence; `services/rag` owns the pipeline (AD-12) and only that ownership is binding now." |
| F6 | Low | AD-14's "bounded retry" names no bound and no home for it — not checkable in review. | Append: "retry count and timeout are pydantic-settings config (Config convention), not literals." |
| F7 | Low | AD-7 "agents … pass it to every tool" reads as a tool parameter; AD-13 forbids exactly that surface. | Reword AD-7: "…agents carry it in graph state; the tool registry injects it (AD-13)." |
| F8 | Low | Thread lifecycle and context-window policy (message trimming/summarization for long checkpointed threads on 14B/32B local models) are silent; two node authors will manage `messages` growth differently. | Add to Deferred: "Thread window management — trimming/summarization strategy for long copilot threads; until decided, nodes must not mutate `messages` history except via LangGraph reducers." |
| F9 | Low | `Command(resume=…)` "carrying the approve/reject" fixes the mechanism but not the payload shape — and the RTW-letter case invites an *edited* artifact in the resume, which web and agents teams could shape differently. | Fix a minimal typed resume payload in AD-6 (e.g. `{decision: approve\|reject, content?}` validated against the pending action) or add it as a named open question. |
| F10 | Low | Copilot availability per persona is undecided: FR-CP binds the handler view, the checkpoint key requires a `claim_id`, and supervisor/analyst dashboards have no stated copilot story or claim-less thread key. | One sentence deciding "copilot is handler-view, claim-scoped only for now" (matching the key), or a Deferred entry for dashboard copilot. |

## Bottom line

AD-13 and AD-14 are exactly the ADs this update needed and are written to the document's best standard; the amended AD-6 correctly pins state, checkpointing, HITL, and the stream contract. No new AD contradicts the existing twelve. Resolve F1 and F2 (plus the one-sentence F3) before restoring `status: final`; F4–F10 are single-line amendments that can ride the same edit.
