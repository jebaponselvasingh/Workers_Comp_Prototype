# Epic 6 Context: AI Adjuster Copilot & RAG

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Epic 6 replaces the prototype's fake copilot — a credential-less browser call to a cloud API that always failed into pre-written canned text — with a real one that runs entirely inside the network. It lays the RAG foundation (local embeddings, scope-enforced vector search over claims and a seeded labor-law corpus), a per-claim AI-insight cache surfaced as the Insights tab, one supervisor-router agent graph with seven deterministic quick actions, persistent per-claim chat threads that survive navigation and sessions, human-gated writes including the RTW letter, and honest degradation when the model is down. It matters because this is where PHI would most plausibly leak and where an LLM could most plausibly invent a dollar figure or mutate a claim — so the epic's real product is the set of guarantees around the model, not the model's prose. Everything here is claim-scoped; a dashboard-scope copilot is deferred by decision.

## Stories

- Story 6.1: Local Model Serving & Embedding Foundation
- Story 6.2: AI Insight Cache & Insights Tab
- Story 6.3: Copilot Chat with Persistent Threads
- Story 6.4: Deterministic Quick Actions (QAS)
- Story 6.5: Human-Gated Writes & the RTW Letter
- Story 6.6: Honest Degradation

## Requirements & Constraints

- **Local-only inference.** All chat and embedding traffic goes to an internal-network Ollama whose port is never published. No cloud code path exists — not as a fallback, not behind a flag. Model names are deployment config, never literals in code; GPU in prod, small CPU models acceptable in dev.
- **Copilot surface.** The claim-scoped panel carries a pulse header, claim-context line, Actions/Diary tabs, seven quick-action buttons, a seeded case-summary greeting, streaming chat, a disclaimer, and a per-claim thread switcher.
- **The seven quick actions.** Labor law & state rules · similar case outcomes · review RTW policy · reserve review · fraud risk check · next best actions · data alignment note. Each behaves identically every time because its key routes deterministically; only free text reaches an LLM router.
- **AI Insights tab.** Four cached narratives per claim — similar-case outcomes, reserve adequacy review, next best actions, fraud risk indicators — each displayed with its generation timestamp, none user-editable, with an explicit not-yet-generated state. A low-fraud-risk claim shows a low-risk confirmation, not an empty list.
- **Never originates figures.** Any money or date in a copilot answer or a generated letter comes from deterministic service output quoted verbatim; the model supplies only surrounding prose.
- **Human-gated writes.** Every AI-proposed write to a claim-owned entity pauses for explicit approval before execution, records the entity versions it was drafted against, and fails safe if the claim moved underneath it. Approve, edit, and reject all record a content-free audit event; reject persists nothing else.
- **Untrusted content.** Claim narratives, diary notes, documents, retrieved chunks, and tool payloads are data to analyze, never instructions to follow. The safety case rests on the approval gate, repository scoping, and the absence of any egress path — not on detecting injection.
- **Degradation is honest.** When the model is unreachable, LLM-free actions still run and stream, only the affected inputs disable, no pre-authored text is presented as model output, and every non-AI claim screen keeps working with no hard dependency on the agent runtime. Retry is bounded.
- **Universal.** Loading, empty, and error states throughout; errors surface inline or as non-blocking notifications. Each story ships one Playwright spec against the freshly reset stack as its done-gate, with copilot specs asserting structure — event sequences, interrupt shapes — never model prose.

## Technical Decisions

- **One graph, not per-feature loops.** All copilot traffic enters a single compiled StateGraph: a deterministic supervisor router whose free-text branch is a `create_agent` harness, checkpointed to Postgres. All nodes share one closed typed schema declared in a single state module; new channels are added there by review, never node-locally.
- **Threads are server-minted.** Keyed `(scope, user_id, conversation_seq)` with scope being a claim business ID in v1; clients never supply a thread id. New conversation increments the sequence and prior threads become read-only. A thread is single-flight — a second message while a run is active or an interrupt pends is rejected. Every run terminates with exactly one terminal stream event.
- **Caller scope is context, not state.** It rides the per-run context schema, re-resolved from the user's employer assignments on every run start and every resume, never checkpointed. Repositories — vector search included — apply one unconditional employer filter; nothing branches on role to widen it.
- **Tools are thin and registered.** Each wraps exactly one service call, carries a typed argument schema and a declared read/write/refresh kind, and returns an `{ok, data, display}` envelope whose `display` strings the model quotes verbatim. No tool imports the data layer or holds a session. Scope is injected by the registry, never a model-populated parameter. Failures return structured errors, never stack traces into the transcript.
- **One gated write step.** Write tools are registered but middleware-gated; a claim write executes in exactly one place, after approval, through the same audited command path everything else uses. Quick-action write nodes hold no write tools — the RTW letter drafts a payload and hands it to that same step, so free-chat and QAS writes produce one identical interrupt shape. The registry's marker-less raise is defence in depth, not a second approval. At most one write pends at a time.
- **Two owners to respect.** The RAG service is the sole embeddings client and sole writer of embeddings and the insight cache, with one refresh command for both scheduled and on-demand paths. Query text is embedded before the repository is called — repositories receive vectors, never text. Commands elsewhere that mutate embedded source fields mark rows stale in the same transaction; retrieval returns embedding freshness and similar-case answers disclose staleness past a configured threshold.
- **Prompts and logs.** Prompts are versioned files loaded by key, the only instruction channel; QAS keys map 1:1 to prompt files where a prompt is needed. Operational logs carry IDs and event names only — never prompt bodies, model output, or PHI values.
- **Frontend.** Chat runs on the assistant-ui LangGraph runtime over SSE — no hand-rolled streaming. Assistant output renders as sanitized markdown only, never raw HTML, and URLs in model output are never auto-fetched by server or client.
- **Do not build ahead.** No dashboard-scope threads, aggregate tools, or dashboard QAS keys. Knowledge-corpus ingestion cadence, answer-quality eval harness, real fraud scoring, and concurrent-load Ollama queueing are all deferred — a minimal seeded labor-law corpus is enough to make the RAG path real.
- **Mechanism left to the build.** The approval-marker channel's exact name and lifecycle, the QAS-to-write handoff, the frontend's decision reverse-mapping, and the scope-lost resume response code are intentionally unfixed; the invariants above are binding, and the interrupt round-trip test is what settles the rest.

## UX & Interaction Patterns

- The copilot is the right pane of the handler three-pane workspace and appears only in claim context. Selecting a claim drives it.
- The RTW letter presents in a wide editable modal with edit, print, and copy affordances; saving it back to the claim passes the approval gate like any other write.
- The approval dialog renders the server-supplied pending tool call — tool name and typed arguments — so the user approves the exact payload that will execute, never the model's paraphrase of it.
- Degradation disables exactly the affected inputs with visible disabled-state treatment, never the whole pane.
- Preserve the prototype's dark, information-dense console aesthetic and shared ok/warn/error status semantics, expressed as Tailwind tokens over vendored shadcn/ui.

## Cross-Story Dependencies

- **Epics 1–3 → all of Epic 6.** The scaffold and seeded portfolio, the personas and their employer assignments, the auth/scope context builder, the audited command pattern with version CAS, and the deterministic financial and derivation services the copilot's read tools wrap. Story 6.1 retro-wires mark-stale calls into commands built in Epics 2 and 3.
- **Within the epic.** 6.1 creates the embedding and knowledge tables everything else retrieves from. 6.2 creates the insight cache and lights up the Insights tab. 6.3 stands up the graph, threads, streaming, and the panel shell that 6.4–6.6 extend. 6.4's quick actions and 6.5's gated writes both sit on 6.3's graph; 6.5's RTW letter is one of 6.4's seven keys. 6.6 exercises the degradation paths of everything above it.
- **Epic 3 → 6.2.** The action checklist's "View Fraud Indicators" deep-link ships disabled in Epic 3 and enables here, landing on the Insights tab.
- **Forward.** Epic 7 reopens dashboard-scope copilot capabilities; the thread key shape already reserves that scope, so adding them changes no keys. Epic 8's purge cascade covers checkpoints, embeddings, and the insight cache as PHI-class stores, and its CI gate covers the routing, registry, round-trip, and injection-fixture tests this epic adds.
