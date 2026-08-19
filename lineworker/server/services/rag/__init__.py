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

Story 6.2 adds a fourth table and one more responsibility of the same kind:

4. **Sole write ownership of `ai_insight`** (`insights.py`) — the per-claim AI
   narrative cache. The generation itself lives in `agents/`, because inference
   may only originate there (AD-5), and it reaches this package through an
   injected `InsightGenerator` protocol exactly as the refresh reaches Ollama
   through `EmbeddingClient`. One function writes the table, and the scheduled
   job, the handler's Refresh button and the e2e trigger all call it (AD-12).

`claim_text.py` sits under all three: one deterministic composer, so that
`source_text_hash` detects real change rather than dict iteration order.

Re-exported here so callers write `from services import rag` and then
`rag.mark_claim_stale(...)` — the same shape `services/audit` and
`services/claims/timeline` already have at their call sites, which is what
makes the four wired commands in Epics 2 and 3 read as one convention rather
than as four imports.

**Not here, and not by accident:** no LangGraph, no chat client, no prompts and
no tool registry. Story 6.2 filled `agents/` with the first chat client, the
first prompt files and four thin service wrappers; 6.3 stands the graph up
there. What stays out of this package is *inference* — `client.py` still speaks
to one endpoint, `/api/embed`, and `insights.py` receives narratives it did not
generate.
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
from services.rag.insights import (
    ALL_KINDS,
    INSIGHT_GENERATED_ACTION,
    CachedInsight,
    InsightGenerationError,
    InsightGenerator,
    InsightRun,
    claim_insights,
    claims_needing_insights,
    store_insights,
)
from services.rag.retrieval import (
    DISTANCE_PLACES,
    MAX_K,
    KnowledgeHit,
    SimilarClaim,
    format_distance,
    search_knowledge,
    similar_claims,
    subject_scoped_context,
)

__all__ = [
    "ALL_KINDS",
    "DISTANCE_PLACES",
    "EMBEDDING_DIMENSIONS",
    "INSIGHT_GENERATED_ACTION",
    "MAX_K",
    "CachedInsight",
    "EmbeddingClient",
    "EmbeddingDimensionMismatch",
    "InsightGenerationError",
    "InsightGenerator",
    "InsightRun",
    "KnowledgeHit",
    "OllamaEmbeddingClient",
    "RefreshRun",
    "SimilarClaim",
    "claim_insights",
    "claims_needing_insights",
    "compose_and_hash",
    "compose_claim_text",
    "embed_claims",
    "embedding_client",
    "format_distance",
    "mark_claim_stale",
    "refresh_stale_embeddings",
    "search_knowledge",
    "seed_knowledge_corpus",
    "similar_claims",
    "store_insights",
    "subject_scoped_context",
    "text_hash",
]
