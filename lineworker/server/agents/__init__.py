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

- `client.py` — the build's only chat client, and the second of the two modules
  that name `OLLAMA_BASE_URL`.
- `prompts/` — versioned instruction files loaded by key, the only instruction
  channel, with the shared AD-16 preamble.
- `schemas.py` — one Pydantic model per kind for what the model may write, and
  one for what is stored; the split between them is AD-2.
- `tools/` — four thin wrappers over deterministic services, returning AD-13's
  `{ok, data, display}` envelope. **Not a registry**; 6.3 makes them one.
- `envelope.py` — that envelope.
- `insights.py` — the orchestration both refresh paths enter.

What is deliberately absent: the StateGraph, the supervisor router, threads,
checkpoints, SSE, the tool *registry*, the quick-action keys, interrupts and
the approval middleware. Stories 6.3 through 6.6 own those, and every one of
them lands in this package.

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
