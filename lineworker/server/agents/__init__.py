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

What is still deliberately absent: the quick-action keys and their node map
(6.4), the `interrupt()` producers, the approval-marker lifecycle and the RTW
letter (6.5), and the `ai_unavailable`/`ai_limit` degradation surface (6.6).
The schema declares `pending_approval`, the stream speaks `interrupt`, the run
bounds are wired — and nothing raises, fills or surfaces any of them yet.

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
