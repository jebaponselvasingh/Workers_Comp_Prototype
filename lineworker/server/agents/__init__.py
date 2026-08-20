"""`agents/` — the only package in the build that runs inference (Story 6.2).

Reserved empty by Story 1.1 so the dependency direction would already exist
when Epic 6 arrived, and filled here by the first thing that needs it: the
generation half of the AI insight cache.

The layering rule this package exists to make true is the spine's dependency
diagram — **chat inference originates only from `agents/`**, and `agents/` may
import `services/` while `services/` may never import `agents/`. So the call
graph runs one way: composition root → `agents/` → `services/` → `data/`.
`services/rag` still owns `ai_insight` (AD-12) and still writes every row of
it; what it receives from here is an `InsightGenerator`, injected exactly as
Story 6.1 injected an `EmbeddingClient`.

What Story 6.2 put here:

- `client.py` — the build's only **structured** chat client, and the second
  module to name `OLLAMA_BASE_URL`.
- `prompts/` — versioned instruction files loaded by key, the only instruction
  channel, with the shared AD-16 preamble.
- `schemas.py` — one Pydantic model per kind for what the model may write, and
  one for what is stored; the split between them is AD-2.
- `tools/` — four thin wrappers over deterministic services, returning AD-13's
  `{ok, data, display}` envelope. **Not a registry**; 6.3 makes them one.
- `envelope.py` — that envelope.
- `insights.py` — the orchestration both refresh paths enter.

What Story 6.3 added, which is the copilot spine itself:

- `state.py` — the one closed state schema every node shares (AD-6). New
  channels are added there by review, never node-locally.
- `context.py` — the per-run context carrying `CallerContext` and the session
  factory. The AD-7 split lives across these two files: a caller *reference* is
  checkpointed, a caller *scope* never is.
- `registry.py` — the AD-13 registry the four wrappers above were promoted
  into, plus the `WriteNotApproved` raise that ships before any write tool does.
- `chat_model.py` — the streaming model factory, and the build's fourth reader
  of `OLLAMA_BASE_URL`. `client.py` says why a fourth is correct rather than a
  violation.
- `graph.py` — the one compiled `StateGraph`: a deterministic entry router
  (dispatch map empty until 6.4) around a `create_agent` grounded-chat node.
- `threads.py` — thread-id minting, the advisory-lock single-flight guard, and
  the saver-derived interrupt probe. The checkpoint tables are read **through
  the saver** and never with SQL (AD-3's exception).
- `greeting.py` — the seeded case-summary line, which is deterministic service
  output rather than model prose (AD-2).

What Story 6.4 added, which is the deterministic half of the copilot:

- `qas.py` — the seven quick-action nodes and the composition they share. Each
  calls registered read tools for its facts and uses the model only to narrate
  them; one of the seven calls no model at all, which is the false path Story
  6.6 gates its degradation on.
- `graph.py::QUICK_ACTIONS` — the key→node map, filled. Consulted **before any
  model call**, so a quick action is answered identically every time because its
  key routed it (AD-14). Every entry declares `requires_llm` and, where it needs
  one, a prompt key.
- `tools/knowledge.py` and `tools/rtw.py` — two more thin wrappers, and
  `similar_cases` finally registered. Registering it needed the thing 6.3
  declined to invent: `registry.py::INJECTED`, a declaration of the keyword
  arguments a tool gets from `CopilotContext` rather than from the model, which
  is what keeps an embeddings client and a staleness window off every
  `args_schema`.
- `prompts/{laborlaw,similar,rtw,reserve,fraud,nextactions}.md` — six versioned
  files keyed 1:1 to the quick actions that need one, composed on
  `copilot_system.md` by `prompts.qas_system_message`.

**Story 6.5 closed the write path**, which is the paragraph this one used to be
the negative of:

- `approval.py` — the gate, in one module: the `interrupt_on` configuration
  `HumanInTheLoopMiddleware` is built from, the `pending_approval` lifecycle,
  the approval marker a write must carry to reach `registry.invoke`, the
  identity-and-version guard on an `edit`, the stale and scope fail-safes, the
  content-free audit call, and the model-free hand-off that turns the RTW
  letter's save into a tool call. There is exactly **one** `interrupt()` in the
  build and it is the vendor's, inside that middleware.
- `tools/documents.py` and `tools/claim_write.py` — the first two `kind: write`
  entries, wrapping `services/claims.create_document` and
  `services/claims.update_claim_fields`. Both are **bound to the model and
  gated, never hidden** (AD-6, §5.2), which is why AC 6's unrequested-write
  scenario is a thing that can be tested at all.

What is still deliberately absent: the `ai_unavailable`/`ai_limit` degradation
surface (6.6). The `requires_llm` flags are declared and nothing surfaces them
yet.

Re-exported here so a composition root writes `from agents import
refresh_claim_insights` — the shape `services/rag` and `services/derivations`
already have at their call sites.
"""

from agents.client import ChatClient, ChatError, ChatSchemaRejected, ChatUnavailable, chat_client
from agents.envelope import ToolResult, ToolUnavailable
from agents.insights import (
    InsightGenerationDeps,
    ToolBackedGenerator,
    refresh_claim_insights,
    refresh_pending_insights,
)

__all__ = [
    "ChatClient",
    "ChatError",
    "ChatSchemaRejected",
    "ChatUnavailable",
    "InsightGenerationDeps",
    "ToolBackedGenerator",
    "ToolResult",
    "ToolUnavailable",
    "chat_client",
    "refresh_claim_insights",
    "refresh_pending_insights",
]
