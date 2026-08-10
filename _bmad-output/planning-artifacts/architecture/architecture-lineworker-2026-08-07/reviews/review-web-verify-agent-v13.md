# Web Verification Review — ARCHITECTURE-SPINE v1.3 Agent Stack

- **Gate:** BMad architecture "Validate" — version/reality check
- **Reviewer role:** version/reality-check reviewer (live-web verification, Aug 2026)
- **File reviewed:** `_bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md`
- **Focus:** AD-6, AD-13, AD-14, and the Stack table (LangChain / LangGraph / assistant-ui decisions)
- **Method:** Verified against docs.langchain.com, reference.langchain.com, PyPI, npm registry, LangChain forum/GitHub — **not** training memory
- **Date:** 2026-08-10

## Verdict

**PASS.** All seven claims VERIFY against current (Aug 2026) documentation. No claim is WRONG or STALE. Two carry non-blocking caveats worth a note; one (claim 7) is verified with an important architectural nuance the spine already anticipates.

## Per-Claim Findings

| # | Claim (as stated in spine) | Status | Source |
| --- | --- | --- | --- |
| 1 | `langchain.agents.create_agent` is LangChain v1's current agent constructor, runs on LangGraph, is the recommended prebuilt for tool-calling loops; import `from langchain.agents import create_agent` | **VERIFIED** | reference.langchain.com/python/langchain/agents/factory/create_agent ; docs.langchain.com/oss/python/migrate/langchain-v1 |
| 2 | `langgraph.prebuilt.create_react_agent` is deprecated in v1 (removal in v2), migration to `create_agent` | **VERIFIED** | docs.langchain.com/oss/python/migrate/langgraph-v1 ; github.com/langchain-ai/langgraph/issues/6404 ; reference.langchain.com/python/langgraph.prebuilt/chat_agent_executor/create_react_agent |
| 3 | `HumanInTheLoopMiddleware` in `langchain.agents.middleware`; requires a checkpointer; uses `interrupt()`; per-tool `interrupt_on`; `allowed_decisions: approve/edit/reject` | **VERIFIED** (note: schema also supports a 4th decision, `respond`) | docs.langchain.com/oss/python/langchain/human-in-the-loop |
| 4 | `ModelCallLimitMiddleware` and `ToolCallLimitMiddleware` exist and bound runs; `ModelFallbackMiddleware` exists (spine deliberately leaves it unused) | **VERIFIED** | reference.langchain.com/python/langchain/agents/middleware ; .../model_call_limit/ModelCallLimitMiddleware ; .../tool_call_limit/ToolCallLimitMiddleware |
| 5 | `create_agent` supports `state_schema`, `context_schema`, `response_format`, `checkpointer`, `middleware` | **VERIFIED** (all five in the signature) | reference.langchain.com/python/langchain/agents/factory/create_agent |
| 6 | Stack pins: `langchain` 1.x; LangGraph (Python) 1.2.x; `langchain-ollama` 1.x; `AsyncPostgresSaver` from `langgraph-checkpoint-postgres`; `@assistant-ui/react-langgraph` 0.14.x | **VERIFIED** (all current lines; see caveat on the `langchain` line) | pypi.org/pypi/langchain/json (1.3.14) ; pypi.org/pypi/langgraph/json (1.2.10) ; pypi.org/pypi/langchain-ollama/json (1.1.0) ; reference.langchain.com/python/langgraph.checkpoint.postgres ; registry.npmjs.org/@assistant-ui/react-langgraph (0.14.23) |
| 7 | The assistant-ui LangGraph runtime surfaces the HITL middleware interrupt (approve/edit/reject) to the UI vs. only a raw `interrupt()`; mismatch between the middleware decision model and what the UI runtime renders | **VERIFIED w/ nuance** — surfaces the raw interrupt payload (which *carries* the structured decision schema); no native middleware-aware widget; mapping + resume-Command are developer-owned; documented friction | docs.langchain.com/oss/javascript/langchain/frontend/human-in-the-loop ; github.com/assistant-ui/assistant-ui/issues/1899 ; github.com/assistant-ui/assistant-ui/discussions/2647 ; deepwiki.com/assistant-ui/assistant-ui/6.2-langgraph-integration |

## Detail & Evidence

### Claim 1 — `create_agent` (VERIFIED)
The reference page confirms the exact import `from langchain.agents import create_agent` and that it returns a `CompiledStateGraph` ("an agent graph that calls tools in a loop") — i.e. it *is* a LangGraph graph, matching the spine's "compiles to a LangGraph graph." The LangChain v1 migration guide confirms it is the recommended successor to `create_react_agent`. A forum thread ("create_agent no longer exists in langchain.agents v1.1.0") initially suggested removal, but the thread's own resolution attributes it to a stale virtualenv, not an API change — `create_agent` remains present and current (verified against langchain 1.3.14). No issue.

### Claim 2 — `create_react_agent` deprecation (VERIFIED)
The LangGraph v1 migration guide and the deprecation message tracked in `langgraph#6404` both steer `langgraph.prebuilt.create_react_agent` users to `from langchain.agents import create_agent`. Deprecated in v1.0, slated for removal in v2.0 — exactly as the spine states.

### Claim 3 — `HumanInTheLoopMiddleware` (VERIFIED; one additive note)
The HITL docs confirm every specific: import `from langchain.agents.middleware import HumanInTheLoopMiddleware`; "Human-in-the-loop requires checkpointing to handle interrupts" (checkpointer mandatory — consistent with AD-6's AsyncPostgresSaver and AD-3); it "issues an interrupt that halts execution" (uses `interrupt()`); `interrupt_on` is "a mapping of tool names to approval configs." **Note:** the middleware actually supports **four** decisions — `approve`, `edit`, `reject`, **and `respond`** (return a human message as the tool result for "ask user" tools). The spine's `approve | edit | reject` is a deliberate and valid subset for a *write-tool* gate (a claim write is not an ask-user tool), so this is not an error — but it is worth recording that the schema is a superset, in case a future ask-user tool wants `respond`.

### Claim 4 — Limit / fallback middleware (VERIFIED)
`ModelCallLimitMiddleware` (thread- and run-level model-call caps with configurable exit) and `ToolCallLimitMiddleware` (tool-call caps) both exist in `langchain.agents.middleware` and bound runs, matching AD-14's per-run bounds. `ModelFallbackMiddleware` also exists (automatic fallback to alternative models on error) — the spine correctly declares it **deliberately unused** because AD-5 forbids any cloud fallback path. Consistent.

### Claim 5 — `create_agent` parameters (VERIFIED)
The published signature includes all five named params: `state_schema: type[AgentState[ResponseT]] | None`, `context_schema: type[ContextT] | None`, `response_format`, `checkpointer: Checkpointer | None`, and `middleware: Sequence[AgentMiddleware]`. AD-6's use of a shared closed `state_schema` (an `AgentState` subclass) and AD-7's caller context on `context_schema` are both supported as described.

### Claim 6 — Stack pins (VERIFIED; one caveat)
- **`langgraph` 1.2.x** — current PyPI latest is **1.2.10**. Exact-line match.
- **`langchain-ollama` 1.x** — current PyPI latest is **1.1.0** (released 2026-04-07), Production/Stable. Match. (Spine's "json_schema structured output default since 0.3.0" is a historical note, not a version claim.)
- **`AsyncPostgresSaver` from `langgraph-checkpoint-postgres`** — confirmed: import `from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver`; PyPI package is `langgraph-checkpoint-postgres`. Match, and the docs corroborate AD-3's concern about `.setup()` DDL (spine correctly vendors that DDL into an Alembic migration rather than calling `setup()` in prod).
- **`@assistant-ui/react-langgraph` 0.14.x** — npm `dist-tags.latest` is **0.14.23**. Current-line match; the spine's "0.13-era HITL interrupt bugs documented" note is consistent with the package's own history.
- **`langchain` 1.x** — *Caveat (non-blocking):* the pin reads "1.x", but the current stable line has already advanced to **1.3.14** (the ecosystem is publishing "LangChain 1.2/1.3" material). The loose "1.x" pin is technically accurate and still current, but it now spans three minor lines (1.0→1.3); the middleware and `create_agent` surfaces the spine depends on are stable across them. Recommend tightening to a concrete minor at project init (see recommendations).

### Claim 7 — assistant-ui runtime vs. the middleware decision model (VERIFIED w/ nuance)
This is the one claim with real architectural texture, and the spine's characterization holds up:

- The **HumanInTheLoopMiddleware interrupt payload is structured**: a `HITLRequest` with `actionRequests[]` (`name`, `args`, `description`) and `reviewConfigs` carrying `allowedDecisions` (`approve`, `reject`, `edit`, `respond`); resume is a typed `Command(resume=…)` with objects like `{type:"approve"}`, `{type:"edit", editedAction:{…}}`, `{type:"reject", message:…}` (per the LangChain **JS frontend HITL** docs).
- The **assistant-ui LangGraph runtime surfaces this as the raw interrupt value** — via `useLangGraphInterruptState()` / the runtime's interrupt state (thread extras). It does **not** ship a first-class, middleware-*aware* approve/edit/reject widget that auto-renders the `HITLRequest`/`allowedDecisions` schema. The developer maps the payload to a review card and constructs the resume `Command` themselves.
- Consequently there **is** a seam (not a blocker): the middleware's richer, four-value decision model surfaces to assistant-ui as an opaque interrupt payload the app must interpret. This friction is documented (assistant-ui issue #1899 "HITL interrupt flows not working with assistant-ui and LangGraph with Python"; discussion #2647 "Best way to render LangGraph interrupt data — interrupt state or tool args"). The LangChain JS HITL frontend guide itself demonstrates the flow with the LangGraph SDK `useStream`, and does **not** mention assistant-ui.
- **The spine already anticipates exactly this**: the Stack row flags "0.13-era HITL interrupt bugs documented — integration-test the approve/edit/reject round-trip on upgrade," and AD-15 mandates a graph/stream test of the `HumanInTheLoopMiddleware` approve/edit/reject round-trip against the model stub. That mitigation is the correct posture. No correction required; strengthen the wording (see recommendations).

## Recommendations (all non-blocking)

1. **Claim 7 — make the seam explicit in AD-6/AD-9.** State plainly that `@assistant-ui/react-langgraph` surfaces the *raw* HITL interrupt payload and that mapping the middleware's `actionRequests`/`allowedDecisions` to the review UI — and building the typed `Command(resume=…)` — is application code the team owns. AD-15's round-trip test is the right guard; keep it binding. This converts an implicit risk into an owned interface.
2. **Claim 3 — record the 4th decision.** Add a one-line note that the middleware also supports `respond`; the write-tool gate intentionally restricts to `approve | edit | reject`. Prevents a future maintainer from thinking `respond` is unavailable.
3. **Claim 6 — tighten the `langchain` pin.** The "1.x" line has moved to 1.3.x; pin a concrete minor (e.g. `langchain>=1.3,<1.4`) at project init alongside the already-concrete `langgraph 1.2.x`, so the resolved `create_agent`/middleware surface is reproducible. No API-surface risk today — this is reproducibility hygiene.
4. **No changes required** to claims 1, 2, 4, 5, or the LangGraph / langchain-ollama / AsyncPostgresSaver / assistant-ui-version facts — all current and correctly described.

## Sources

- create_agent reference (signature, import, params, CompiledStateGraph): https://reference.langchain.com/python/langchain/agents/factory/create_agent
- LangChain v1 migration guide (create_agent recommended): https://docs.langchain.com/oss/python/migrate/langchain-v1
- LangGraph v1 migration guide (create_react_agent deprecation): https://docs.langchain.com/oss/python/migrate/langgraph-v1
- create_react_agent deprecation message: https://github.com/langchain-ai/langgraph/issues/6404
- "create_agent no longer exists in 1.1.0" (resolved: stale env, not removed): https://forum.langchain.com/t/create-agent-no-longer-exists-in-langchain-agents-v1-1-0/2350
- Human-in-the-loop (Python) — checkpointer required, interrupt(), interrupt_on, allowed_decisions incl. respond: https://docs.langchain.com/oss/python/langchain/human-in-the-loop
- Middleware reference index (Model/Tool call-limit, ModelFallback): https://reference.langchain.com/python/langchain/agents/middleware
- ModelCallLimitMiddleware: https://reference.langchain.com/python/langchain/agents/middleware/model_call_limit/ModelCallLimitMiddleware
- ToolCallLimitMiddleware: https://reference.langchain.com/python/langchain/agents/middleware/tool_call_limit/ToolCallLimitMiddleware
- langchain-postgres / AsyncPostgresSaver import + package: https://reference.langchain.com/python/langgraph.checkpoint.postgres
- Human-in-the-loop (JS frontend) — HITLRequest / actionRequests / reviewConfigs / typed resume: https://docs.langchain.com/oss/javascript/langchain/frontend/human-in-the-loop
- assistant-ui LangGraph integration (interrupt state surfacing): https://deepwiki.com/assistant-ui/assistant-ui/6.2-langgraph-integration-(@assistant-uireact-langgraph)
- assistant-ui HITL friction (issue): https://github.com/assistant-ui/assistant-ui/issues/1899
- assistant-ui rendering interrupt data (discussion): https://github.com/assistant-ui/assistant-ui/discussions/2647
- PyPI langchain (1.3.14): https://pypi.org/pypi/langchain/json
- PyPI langgraph (1.2.10): https://pypi.org/pypi/langgraph/json
- PyPI langchain-ollama (1.1.0, 2026-04-07): https://pypi.org/pypi/langchain-ollama/json
- npm @assistant-ui/react-langgraph (0.14.23 latest): https://registry.npmjs.org/@assistant-ui/react-langgraph
