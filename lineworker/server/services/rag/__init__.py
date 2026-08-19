"""`services/rag` — the AI substrate's owner (Story 6.1).

Empty since Story 1.1, which reserved the package so that the dependency
direction would already exist when Epic 6 arrived. This is what fills it, and
the package holds three responsibilities that are deliberately not separable:

1. **The sole embeddings client** (`client.py`). One module in the whole build
   issues an HTTP request to Ollama, and nothing here touches the chat
   endpoint. AD-5's "no PHI leaves the network" is checkable because of this.
2. **Sole write ownership of three tables** (`embeddings.py`) — `claim_embedding`,
   `knowledge_chunk`, `knowledge_embedding`. Every other service that needs a
   row to change asks, through `mark_claim_stale`, in the caller's own
   transaction (AD-12).
3. **The query path** (`retrieval.py`), where text becomes a vector before any
   repository is called.

`claim_text.py` sits under all three: one deterministic composer, so that
`source_text_hash` detects real change rather than dict iteration order.

Re-exported here so callers write `from services import rag` and then
`rag.mark_claim_stale(...)` — the same shape `services/audit` and
`services/claims/timeline` already have at their call sites, which is what
makes the four wired commands in Epics 2 and 3 read as one convention rather
than as four imports.

**Not here, and not by accident:** no LangGraph, no chat client, no prompts, no
tool registry, no insight cache. 6.2 through 6.6 build those, and the
`agents/` package (empty, reserved by Story 1.1) is where the graph lands.
"""

from services.rag.claim_text import compose_and_hash, compose_claim_text, text_hash
from services.rag.client import (
    EMBEDDING_DIMENSIONS,
    EmbeddingClient,
    EmbeddingDimensionMismatch,
    OllamaEmbeddingClient,
    embedding_client,
)
from services.rag.embeddings import (
    RefreshRun,
    embed_claims,
    mark_claim_stale,
    refresh_stale_embeddings,
    seed_knowledge_corpus,
)
from services.rag.retrieval import (
    MAX_K,
    KnowledgeHit,
    SimilarClaim,
    search_knowledge,
    similar_claims,
)

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "MAX_K",
    "EmbeddingClient",
    "EmbeddingDimensionMismatch",
    "KnowledgeHit",
    "OllamaEmbeddingClient",
    "RefreshRun",
    "SimilarClaim",
    "compose_and_hash",
    "compose_claim_text",
    "embed_claims",
    "embedding_client",
    "mark_claim_stale",
    "refresh_stale_embeddings",
    "search_knowledge",
    "seed_knowledge_corpus",
    "similar_claims",
    "text_hash",
]
