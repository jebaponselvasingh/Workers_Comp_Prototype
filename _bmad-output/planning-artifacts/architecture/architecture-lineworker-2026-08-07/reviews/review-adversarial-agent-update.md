# Adversarial Review — Agent Harness (post AD-6 amendment, AD-13, AD-14)

- **Target:** ARCHITECTURE-SPINE.md, agent harness only (AD-6 as amended, AD-13, AD-14, plus AD-2/3/4/5/7/10/11/12 where they touch `agents/`)
- **Method:** Two-unit divergence attack. Each finding constructs builds that obey every AD **to the letter** yet integrate incompatibly. Judged against what the spine *says*, not what a sensible engineer would assume.
- **Date:** 2026-08-07
- **Verdict:** **FAIL for parallel build.** The amended AD-6/AD-13/AD-14 set fixes the graph-state and routing surface but leaves the write-tool gate, the resume/reject wire protocol, the thread-identity function, and the embedding write path underspecified — three findings are critical, each sufficient to make the two units' first integration fail.

## The two units

- **Unit A — Copilot backend team.** Owns `server/agents/` (StateGraph, supervisor router, `tools/` registry, prompts, checkpointer wiring) and the copilot SSE endpoint in `server/api/`.
- **Unit B — Copilot frontend team.** Owns `web/features/copilot` on the assistant-ui LangGraph runtime (per AD-9), consuming the SSE stream and the resume path.
- Where a finding is intra-backend, the two parties are **Engineer A1** (write tools + interrupt flow) and **Engineer A2** (read/RAG nodes), both inside Unit A.

---

## Findings

### F1 — CRITICAL — The interrupt gate is on "nodes"; write *tools* are ungated

AD-6 gates "any **node** that would persist a change to a claim-data entity" behind `interrupt()`. AD-13 says every tool "wraps exactly one service **command** or query" — so write-command tools are explicitly contemplated — and says nothing about interrupts, two-phase execution, or read/write classification.

- **Engineer A1** (letter-of-the-law): registers `save_rtw_letter_to_documents` as an AD-13 tool wrapping the `services/claims` document command. The free-chat path is a standard LLM tool-calling node; the model selects the tool; the shared ToolNode executes it. The ToolNode is a generic executor — it is not "a node that would persist a change," the *tool* is — and no AD says a tool may not execute a write when called. The write goes through an AD-4 command, is audited, is scope-checked per AD-7. Every AD satisfied; **the LLM has just mutated a claim-data entity with no human approval.**
- **Engineer A2** builds write actions as dedicated nodes that `interrupt()` before calling the same tools.

Both are AD-compliant; the graph now has two inconsistent write disciplines, and the human-gate invariant AD-6 exists to enforce is void on the free-chat path.

**Fix (tighten AD-13 + AD-6):** "Every tool is registered as `kind: read | write`. Write tools are never exposed to any LLM tool-selection node. A write tool may be invoked only by the approval-execution step that runs after an `interrupt()` has returned an approve; the registry enforces this (write tools raise if invoked without an approval token in graph state). Free-chat may *propose* writes only by emitting a `pending_approval` payload."

### F2 — CRITICAL — Embedding write path is architecturally impossible as drawn

AD-12/AD-10: embeddings and `ai_insight` are "owned and refreshed **solely** by `services/rag`." AD-5: "all model inference **and embedding**" goes to Ollama. The Design Paradigm diagram draws exactly one arrow to Ollama — `AGENT --> OLLAMA` — and the dependency rule says "arrows only point downward/rightward **as drawn**." There is no `SVC --> OLLAMA` arrow.

- **Unit A** reads the diagram literally: only `agents/` may call Ollama. So the embedding-refresh job (Structural Seed: "runs as scheduled jobs inside the `api` process") calls an agent graph, which computes vectors and passes them to a `services/rag` command. But now `agents/` initiates refresh, while AD-10 says `ai_insight` is "refreshed **solely** by `services/rag`" — and AD-6 separately says "**writes to the AI-insight cache** [by the copilot graph] are exempt from the interrupt gate," which presupposes agents write it at all. Contradiction inside the spine.
- **A second team** (or `services/rag`'s author) reads AD-12 ownership as primary and has `services/rag` call `langchain-ollama` directly — violating the drawn dependency arrows, and putting model inference in a layer AD-5's config story never mentions.

Neither embedding-at-ingest, query-time query-embedding for pgvector search (AD-7 puts vector search in the **repository** layer — which is `data/`, two layers below the only component allowed to talk to Ollama), nor `ai_insight` refresh has a legal path. Every implementation must break at least one literal rule; two teams will break *different* ones.

**Fix (amend Design Paradigm + AD-12):** add arrow `SVC["services/rag"] --> OLLAMA` and rule text: "`services/rag` is the sole non-agent Ollama client, embeddings-endpoint only (`bge-m3`); chat inference remains exclusive to `agents/`. Query-time embedding of the user's search text is performed by `services/rag` before invoking the repository; repositories receive vectors, never text. `ai_insight` refresh is initiated by `services/rag` (scheduled) or by an agent **through a `services/rag` command** (on-demand); both paths are the same command."

### F3 — CRITICAL — Resume/reject wire protocol and mid-stream errors are unspecified

AD-6 fixes the graph-internal mechanics (`interrupt()`, `Command(resume=…)`) and says interrupts "surface to the UI as interrupt events on that same stream." It says nothing about: how the client *sends* the resume; what the graph/stream does on **reject**; or how errors are delivered mid-stream. The Conventions row says all errors are RFC 9457 problem+json "from FastAPI exception handlers" — impossible after SSE headers are sent.

- **Unit A** builds: `POST /copilot/threads/{id}/resume` accepting `{approved: bool}`, returning a *new* SSE stream; on reject the node returns early, writes nothing, emits no terminal event beyond stream close; Ollama mid-stream timeout closes the SSE connection (AD-14's "AI unavailable state" delivered as HTTP 503 — only possible pre-stream).
- **Unit B** builds on the assistant-ui LangGraph runtime, which sends the resume as a command payload on the *same* send endpoint and expects the interrupt's resolution, a rendered rejection message, and errors as **in-stream events** — otherwise the UI hangs on a silently closed connection with a spinner.

Both teams satisfy every written word of AD-6/AD-14. The first rejected RTW letter and the first mid-generation Ollama timeout both produce a hung UI.

**Fix (new AD or AD-6 rule extension):** "The copilot stream contract is: `messages`, `updates`, `interrupt`, `error`, `done` — an `error` event carries the problem+json body inline (the Conventions envelope applies to payload shape, not transport); every run terminates with exactly one of `interrupt | error | done`. Resume is `POST` to the same runs endpoint with a `command: {resume: {approved, payload?}}` body (the shape the assistant-ui LangGraph runtime emits) and returns a new stream for the same thread. On **reject**, the graph resumes, discards the proposed write, appends an assistant message stating the action was cancelled (streamed as `messages`), and clears `pending_approval`; nothing is persisted to claim entities and no AD-4 audit event is emitted (no mutation occurred), but a `copilot.approval_rejected` timeline/log event MAY NOT be silently substituted for either."

### F4 — HIGH — Thread identity fails for dashboards, claim-less chat, and "new chat"

AD-6: "checkpointed per `(claim_id, user_id)`" and the state schema requires `claim_id`. But FR-CP-* copilot also serves flows with no claim: a supervisor on `svView` asking "which handlers exceed cap?", a handler asking a glossary/labor-law question before selecting a claim (labor-law RAG is in scope per the Capability Map and needs no claim). Also, keying by the *pair* means one immortal thread per (claim, user): "start a new conversation" is unrepresentable without violating the stated key.

- **Unit A** makes `claim_id` non-nullable (it's in the required schema) and derives `thread_id = f"{claim_id}:{user_id}"`; claim-less requests are rejected 422.
- **Unit B** ships a dashboard copilot pane and a "New chat" button (standard assistant-ui affordances), sending `claim_id: null` and a fresh client-generated `thread_id`. Neither team violated a word of AD-6.

**Fix (amend AD-6):** "`claim_id` is `Optional`; thread key is `(scope, user_id, conversation_seq)` where `scope` is a claim business ID or the literal `dashboard`. A user may open a new conversation (incrementing `conversation_seq`); prior threads become read-only history. Server mints `thread_id`; clients never supply one."

### F5 — HIGH — One `pending_approval`, but nothing forbids a second run / second approval on the thread

The schema has a **singular** `pending_approval`, yet the spine never says a thread is single-flighted. LangGraph will happily accept a new `invoke` on a paused thread (clobbering the pending interrupt) and a multi-step run can hit two write nodes sequentially.

- **Unit A** allows a new user message while an approval is pending (new run resumes/overwrites the checkpoint — the pending write silently evaporates, or worse, `Command(resume)` later applies to the wrong pending action).
- **Unit B** renders one modal approval bound to the last interrupt event and disables composer input until resolved. State desync on the first impatient user.

**Fix (AD-6 rule):** "A thread is single-flight: while a run is active or an interrupt is pending, the runs endpoint rejects new messages for that thread with 409 (problem+json). At most one `pending_approval` may exist per thread; a graph path requiring a second claim-write within one run must sequence it as a second interrupt after the first resolves."

### F6 — HIGH — Checkpoint tables: unowned entity, Alembic-only contradiction, purge with no owner

AD-3: everything in the one PostgreSQL is "migrated by **Alembic only**." AsyncPostgresSaver (named in AD-6) creates and writes its own tables via its own `setup()`/psycopg — not Alembic, not SQLAlchemy. AD-12 requires "exactly one write-owner per entity" registered in the Capability Map — checkpoint tables appear nowhere in it. AD-11 says purge is "a single purge cascade owned by `services/audit`" — so `services/audit` deletes rows of tables no service owns, that agents write through a third-party saver, in a schema Alembic never saw.

- **Data-layer engineer** (obeying AD-3/AD-4 literally) refuses the saver's DDL and DML path; **Unit A** cannot run LangGraph persistence without it. Deadlock resolved differently per team → dev DBs and prod migrations diverge.

**Fix (AD-3 + Capability Map row):** "Exception, explicitly registered: LangGraph checkpoint tables (`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`) are created by a dedicated Alembic migration that vendors the saver's DDL (pinned to LangGraph 1.2.x; upgrading LangGraph requires a migration) and are written **only** by AsyncPostgresSaver within `agents/`. Add Capability Map row: 'Copilot threads & checkpoints — lives in `agents/` (via AsyncPostgresSaver) — governed by AD-3(exception), AD-6, AD-11'. `services/audit`'s purge cascade deletes checkpoints by `thread_id` prefix per claim/user; retention for copilot checkpoints is its own config knob, default 90 days, distinct from the 7-year audit floor."

### F7 — HIGH — Checkpointed `caller` scope goes stale; resume actor unverified

AD-7: "agents receive [the caller context] in graph state and pass it to every tool." Graph state is checkpointed (AD-6). So the scope context is *persisted* and *replayed*: reassign a handler's employers today, resume yesterday's paused approval tomorrow, and the resumed run's tools filter by yesterday's book of business — reading (or completing a write to) a claim the user no longer holds. Separately, nothing says the resume endpoint must verify the resuming user *is* the thread's user — "explicit user approval" names no actor check.

- **Unit A** trusts checkpointed `caller` (that is literally what AD-7 prescribes); **the API engineer** assumed repository-layer enforcement makes staleness harmless — but the repository filters by whatever context it is handed.

**Fix (AD-7 rule):** "The `caller` channel in graph state is a reference (user id + role), never a materialized scope. On every run start **and every resume**, the API dependency re-resolves scope from `handler_employer_assignment` and overwrites the channel; tools receive only the fresh context. The resume endpoint rejects any actor other than the thread's `user_id` (403)."

### F8 — HIGH — QAS nodes and the LLM: streaming shape and degradation scope diverge

AD-14: QAS keys "route through a static key→node map **before any LLM call**; only free-text messages go to the LLM router," and "if Ollama is unavailable … the copilot returns an explicit 'AI unavailable' state."

Two literal readings coexist: (a) only *routing* is pre-LLM — the target node may then call the LLM to narrate tool output (AD-2 expressly allows narration); (b) quick actions are deterministic end-to-end — the node returns tool output with no inference.

- **Unit A** (reading b, reinforced by AD-14's anti-`offlineAnswer` intent) makes `benefit_calc` and `payment_schedule` QAS nodes return structured tool results with zero LLM tokens — and keeps them **working during an Ollama outage** (no inference needed, and AD-14 only mandates degradation "if Ollama is unavailable" for things that need it… or does it mandate it for the whole copilot? The sentence doesn't scope itself).
- **Unit B** renders everything through the assistant-ui message stream: a QAS press that emits no `messages` tokens renders an empty bubble; and its outage handling disables the entire copilot pane on the first "AI unavailable," including the QAS buttons Unit A deliberately kept alive.
- The RTW letter deepens it: it *must* call the LLM (AD-2: "the LLM drafts only surrounding prose") and *must* interrupt (AD-6). Is it a QAS key or free chat? The spine lists it beside quick actions in AD-6's binds but never assigns it. Reading (b) makes it impossible as a QAS node.

**Fix (AD-14 rule):** "Every QAS node MAY invoke the LLM to narrate its tool results (never to choose tools or originate figures, per AD-2); RTW letter IS a QAS key whose node runs tool-merge → LLM prose → interrupt. Each QAS node declares `requires_llm: bool` in the key→node map. During Ollama unavailability, `requires_llm: false` actions execute and stream normally; `requires_llm: true` actions and free chat return the `error` event with code `ai_unavailable`. The UI disables exactly the affected inputs, never the pane."

### F9 — MEDIUM — RAG is a node, so AD-13 doesn't bind it

AD-6 says "tool/**RAG nodes**"; AD-13 binds "every LangGraph **tool**." A RAG *node* that calls `services/rag` directly (legal per the dependency rule: `agents/` → `services/`) escapes the registry, the Pydantic schema, the injected caller context ("agents … pass it to **every tool**" — a node is not a tool), and the structured-error requirement. Engineer A2 builds retrieval as a bare node with hand-rolled scope passing (or forgets it — nothing *written* forces the context into a non-tool service call); Engineer A1 builds retrieval as a registered tool. Two retrieval paths, one audited/scoped by construction, one by discipline.

**Fix (AD-13 rule):** "AD-13 binds every service invocation from `agents/`, whether made by a ToolNode or directly inside any node: all such calls go through the registry (nodes call registry entries, not services). 'RAG node' is a graph-topology term; its retrieval call is a registered read tool."

### F10 — MEDIUM — Tool result format into the LLM context is unspecified; cents will be misnarrated

Conventions: money is integer cents "in DB, API, and across the ZEN boundary … format in the UI only." Tools wrap service queries, so tool output is cents; AD-2 says the copilot "may quote tool output but never originate a financial figure." Quoting literally yields "your weekly benefit is 84500"; converting to "$845.00" is the LLM *transforming* a figure — the exact drift AD-2 exists to prevent, done in the least reliable component. Unit A's two engineers already disagree: A1 formats inside the tool (arguably violating "format in the UI only"), A2 passes raw cents.

**Fix (AD-13 rule):** "Tool results enter the model context in a fixed envelope `{ok, data, display}` where `display` contains service-side human-formatted strings for every money/date field (this is presentation for the model-as-consumer, carved out from the UI-only formatting rule). Prompts instruct the model to quote `display` values verbatim; `data` stays cents for the UI's structured rendering."

### F11 — MEDIUM — Rejection leaves no trace anywhere, by rule

AD-4 audits mutations; a rejection mutates nothing → no audit event, and AD-6 forbids side-channel writes while paused. So "supervisor asks: has the AI ever proposed payments this handler declined?" is unanswerable — and the two units will invent different answers (Unit A logs to structlog — forbidden if the proposed content contains PHI per AD-11; Unit B keeps it client-side only — lost on reload). The draft itself *does* survive in the checkpoint (PHI-class, fine per AD-11), which one team will treat as the record and the other as ephemeral.

**Fix (AD-6 rule):** "Approval outcomes (approved/rejected, action kind, claim, actor, at — no content) are recorded via a `services/audit` command `record_copilot_approval` in both branches; the approve branch's subsequent AD-4 write references it. This is the only persistence permitted on the reject branch."

### F12 — LOW — Shared-schema wording permits divergent extra channels

AD-6: "no node defines private state channels **for these**" — i.e., for the four named keys. Extra channels are unconstrained: A1 adds `draft_letter`, A2 adds `rtw_draft` and `retrieved_chunks`; the supervisor router and the SSE `updates` projection see a different state shape depending on which nodes ran. Not integration-fatal, but it erodes the "one typed schema" claim.

**Fix:** "The graph-state schema is closed: any new channel is added to the single shared TypedDict in one module (`agents/state.py`) via review, never node-locally."

### F13 — LOW — No token/length bound on generation

AD-14 bounds retries but nothing bounds a single generation; an unbounded `qwen3:32b` ramble is a PHI-heavy, checkpoint-bloating, SSE-hogging run. Teams will pick different `num_predict`/timeout values or none.

**Fix (convention row):** "Copilot inference carries deployment-config `num_predict` and per-run wall-clock timeout; exceeding either terminates the run with the `error` stream event (code `ai_limit`)."

---

## Summary table

| # | Severity | One-line |
| --- | --- | --- |
| F1 | Critical | Interrupt gate binds nodes, not tools — LLM tool-calling can execute claim writes unapproved |
| F2 | Critical | No legal Ollama path for `services/rag` embeddings; AD-6 vs AD-10/12 disagree on who writes `ai_insight` |
| F3 | Critical | Resume/reject wire protocol and mid-stream error events unspecified; problem+json convention can't work mid-SSE |
| F4 | High | `(claim_id, user_id)` threading has no answer for dashboard/claim-less chat or "new conversation" |
| F5 | High | Nothing forbids concurrent runs or a second approval on a paused thread |
| F6 | High | Checkpoint tables violate Alembic-only AD-3, have no AD-12 owner, and no retention distinct from audit's 7 years |
| F7 | High | Checkpointed caller scope replays stale RBAC; resume actor never verified |
| F8 | High | Ambiguous whether QAS nodes may call the LLM; outage degradation scope (whole copilot vs LLM-needing paths) diverges; RTW letter unassigned |
| F9 | Medium | RAG-as-node escapes AD-13's registry, schema, context-injection, and error rules |
| F10 | Medium | Tool results reach the LLM as integer cents; quoting vs converting both violate something |
| F11 | Medium | Rejected approvals are unpersistable by rule — no trace anywhere |
| F12 | Low | "For these" wording lets nodes add divergent ad-hoc state channels |
| F13 | Low | No token/length/time bound on a single generation |
