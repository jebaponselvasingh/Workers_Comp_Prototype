# Web Verification Review — Agent-Harness Sections (AD-5, AD-6, AD-13, AD-14 + Stack rows)

- **Reviewer:** web-verification pass (Claude Code)
- **Date:** 2026-08-07
- **Scope:** ARCHITECTURE-SPINE.md — AD-5, AD-6, AD-13, AD-14; Stack rows for LangGraph, langchain-ollama, Ollama, assistant-ui
- **Method:** Web searches and vendor-doc/registry fetches performed 2026-08-07

## Verdict

**PASS WITH FINDINGS.** Every load-bearing technical mechanism the spine relies on is real and current in the referenced libraries. Two version pins are stale (assistant-ui 0.13.x → 0.14.x current; langchain-ollama is now on a 1.x line), and AD-6's streaming sentence glosses over the fact that the assistant-ui LangGraph runtime speaks the LangGraph SDK event protocol (its documented token mode is `messages-tuple`), which constrains how the custom FastAPI SSE endpoint must be built. Nothing invalidates an architectural decision; the fixes are wording/pin updates plus one explicit protocol note.

---

## Per-claim verification

### 1. LangGraph Python 1.2.x — HITL, checkpointer, stream modes

| Claim (spine) | Status | Evidence |
|---|---|---|
| `interrupt()` pauses the graph; `Command(resume=…)` resumes it, carrying the human's value back to the `interrupt()` call (AD-6) | **VERIFIED** | Official LangChain reference: `interrupt` raises `GraphInterrupt`, surfaces the payload to the client; client resumes via `Command(resume=value)`; the node re-executes from its start. Requires a checkpointer — which AD-6 provides (AsyncPostgresSaver). Sources: [interrupt reference](https://reference.langchain.com/python/langgraph/types/interrupt), [LangChain HITL blog](https://www.langchain.com/blog/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt) |
| `AsyncPostgresSaver` from `langgraph-checkpoint-postgres` is the async Postgres checkpointer (AD-6) | **VERIFIED** | Documented in the current LangChain reference and shipped in `langgraph-checkpoint-postgres` (`langgraph.checkpoint.postgres.aio.AsyncPostgresSaver`); non-blocking, `.setup()` creates tables, `autocommit=True` + `row_factory=dict_row` required on manually created connections. Sources: [AsyncPostgresSaver reference](https://reference.langchain.com/python/langgraph.checkpoint.postgres/aio/AsyncPostgresSaver), [PyPI langgraph-checkpoint-postgres](https://pypi.org/project/langgraph-checkpoint-postgres), [GitHub libs/checkpoint-postgres](https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres) |
| `messages` and `updates` are valid stream modes for streaming to a client (AD-6) | **VERIFIED** | Current docs list both: `messages` = token-by-token LLM output + metadata; `updates` = per-node state deltas; a list (`stream_mode=["updates","messages"]`) interleaves both in one labeled stream. Sources: [Pregel.stream reference](https://reference.langchain.com/python/langgraph/pregel/main/Pregel/stream), [LangGraph streaming docs](https://docs.langchain.com/oss/python/langgraph/streaming) |
| Version pin 1.2.x is current | **VERIFIED** | PyPI latest is `langgraph 1.2.10` (released 2026-07-28); 1.2.x is the current minor line. Source: [PyPI langgraph](https://pypi.org/project/langgraph/) |

### 2. assistant-ui `@assistant-ui/react-langgraph` 0.13.x

| Claim (spine) | Status | Evidence |
|---|---|---|
| Consumes LangGraph streams (AD-6, AD-9) | **VERIFIED with constraint** | The runtime integrates directly with `@langchain/langgraph-sdk`; graph state (`state.values.messages`) is the source of truth. Its documented default path is a LangGraph API server; a custom FastAPI endpoint is feasible via `useLangGraphRuntime`'s `stream` callback but must emit LangGraph-SDK-shaped events (reference implementation: assistant-ui + assistant-stream + FastAPI). Sources: [LangGraph runtime overview](https://www.assistant-ui.com/docs/runtimes/langgraph/overview), [Yonom/assistant-ui-langgraph-fastapi](https://github.com/Yonom/assistant-ui-langgraph-fastapi) |
| Surfaces interrupts (HITL approval) to the UI (AD-6) | **VERIFIED** | Pending interrupt payload is exposed on `stream.interrupt` / via `useLangGraphInterruptState()`; the UI resumes with `stream.submit(null, { command: { resume: … } })` — exactly the approve/reject round-trip AD-6 specifies. Caveat: multiple GitHub issues/discussions report HITL interrupt-flow bugs in some versions (#1280, #1899, #1975) — pin and integration-test this path. Sources: [assistant-ui LangGraph docs](https://www.assistant-ui.com/docs/runtimes/langgraph), [issue #1280](https://github.com/assistant-ui/assistant-ui/issues/1280), [issue #1899](https://github.com/assistant-ui/assistant-ui/issues/1899) |
| Stream-mode constraint | **PARTIALLY VERIFIED — wording gap** | assistant-ui's streaming doc shows `streamMode: "messages-tuple"` as the token-streaming mode (per-chunk metadata: `langgraph_node`, `langgraph_step`), with `custom` for UI messages; `updates` carries state. The spine's phrase "streams LangGraph `messages` + `updates` events" names the server-side Python modes, not the SDK wire modes assistant-ui documents. Source: [assistant-ui streaming doc](https://www.assistant-ui.com/docs/runtimes/langgraph/streaming) |
| Version pin 0.13.x is current | **STALE** | npm latest is `@assistant-ui/react-langgraph 0.14.20`, actively maintained, no deprecation. 0.13.x is superseded. Source: [npm registry, `@assistant-ui/react-langgraph/latest`](https://registry.npmjs.org/@assistant-ui/react-langgraph/latest) |

### 3. langchain-ollama ≥0.3 with qwen3

| Claim (spine) | Status | Evidence |
|---|---|---|
| Tool calling supported (AD-13 tools, qwen3 chat model) | **VERIFIED** | `ChatOllama` supports the Ollama tool-calling API; qwen3 is consistently benchmarked among the most reliable local tool-calling model families on Ollama (lowest dropped-call rate, schema-valid arguments), and the Ollama qwen3 library entry carries the `tools` capability. Sources: [Ollama qwen3](https://ollama.com/library/qwen3), [Ollama streaming + tool calling blog](https://ollama.com/blog/streaming-tool) |
| Structured output via `json_schema` (Stack row: "≥0.3 (json_schema default)") | **VERIFIED** | `ChatOllama.with_structured_output` supports `'json_schema'` (Ollama structured-output API), `'function_calling'`, and `'json_mode'`; the reference explicitly notes the default `method` became `'json_schema'` in langchain-ollama 0.3.0 — matching the spine's parenthetical exactly. Source: [with_structured_output reference](https://reference.langchain.com/python/langchain-ollama/chat_models/ChatOllama/with_structured_output) |
| Pin "≥0.3" current | **VERIFIED but loose** | PyPI latest is `langchain-ollama 1.1.0` (2026-04-07). "≥0.3" is technically satisfied and the json_schema-default rationale holds, but the floor reads as if 0.3.x were the current era; a 1.x floor would better reflect reality. Source: [PyPI langchain-ollama](https://pypi.org/project/langchain-ollama/) |

### 4. Ollama / supersession sweep

| Item | Status | Notes |
|---|---|---|
| Ollama "current stable server, digest pinned at deploy" | **VERIFIED** | No supersession; qwen3 and bge-m3 both remain in the Ollama library; digest-pin-at-deploy phrasing keeps the row evergreen. |
| LangGraph renamed/superseded? | **NO** | 1.2.x is the live line; `interrupt`/`Command` and checkpointer packages unchanged. |
| assistant-ui LangGraph runtime superseded? | **NO, but minor bumped** | Package name unchanged; 0.13.x → 0.14.x since pinning. |
| langchain-ollama superseded? | **NO** | Same package; major version is now 1.x. |

---

## Findings

| # | Severity | Finding | Recommendation |
|---|---|---|---|
| F1 | **High** | AD-6/AD-9 assume the assistant-ui LangGraph runtime consumes a custom FastAPI SSE endpoint, but the runtime is built on `@langchain/langgraph-sdk` and its documented default is a LangGraph API server. Feasible self-hosted path exists (`useLangGraphRuntime` `stream` callback; assistant-stream + FastAPI reference impl), but the endpoint must emit LangGraph-SDK-protocol events — this is an implementation contract the spine doesn't state. | Add one sentence to AD-6 (or a stack-row note): "the copilot endpoint emits the LangGraph SDK stream-event protocol (assistant-stream/FastAPI pattern), consumed via `useLangGraphRuntime`'s custom `stream` callback." |
| F2 | **Medium** | Stack pins `@assistant-ui/react-langgraph` at 0.13.x; npm latest is 0.14.20. Given documented HITL interrupt bugs in past versions (issues #1280/#1899), pinning a superseded minor risks shipping a known-buggy interrupt path. | Bump the stack row to 0.14.x and add an integration test for the interrupt approve/reject round-trip. |
| F3 | **Medium** | AD-6 says the SSE stream carries "`messages` + `updates` events"; assistant-ui's streaming docs use `messages-tuple` as the token mode (SDK wire naming). The Python-side and SDK-side mode names differ, and the spine mixes them. | Reword AD-6 to name the wire modes the UI actually consumes (`messages-tuple` for tokens, `updates` for state; add `custom` if UI messages/progress are wanted). |
| F4 | **Low** | langchain-ollama floor "≥0.3" is satisfied but stale-reading — the package is on 1.1.0. | Restate as "1.x (json_schema default since 0.3.0)". |
| F5 | **Low** | qwen3 tool calling is well-supported, but reliability commentary is strongest for larger variants; `qwen3:14b` on the 24 GB tier is the floor config for tool-call fidelity with the AD-13 thin-tool registry. | No spine change; flag for evaluation during agent-harness spike (AD-14 already gives honest degradation if calls fail). |

## Sources (primary)

- https://reference.langchain.com/python/langgraph/types/interrupt
- https://reference.langchain.com/python/langgraph.checkpoint.postgres/aio/AsyncPostgresSaver
- https://reference.langchain.com/python/langgraph/pregel/main/Pregel/stream
- https://docs.langchain.com/oss/python/langgraph/streaming
- https://pypi.org/project/langgraph/ (1.2.10, 2026-07-28)
- https://registry.npmjs.org/@assistant-ui/react-langgraph/latest (0.14.20)
- https://www.assistant-ui.com/docs/runtimes/langgraph/overview
- https://www.assistant-ui.com/docs/runtimes/langgraph/streaming
- https://github.com/assistant-ui/assistant-ui/issues/1280 · /issues/1899 · /discussions/1975
- https://github.com/Yonom/assistant-ui-langgraph-fastapi
- https://reference.langchain.com/python/langchain-ollama/chat_models/ChatOllama/with_structured_output
- https://pypi.org/project/langchain-ollama/ (1.1.0, 2026-04-07)
- https://ollama.com/library/qwen3 · https://ollama.com/blog/streaming-tool
- https://www.langchain.com/blog/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt
