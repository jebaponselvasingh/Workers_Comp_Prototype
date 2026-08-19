"""The query path — where text becomes a vector, and the only place it does.

AC 2's literal rule: **repositories receive vectors, never text.** Both
functions below embed here and then call the repository with `list[float]`.
That is a one-line arrangement with a real consequence — the decision of which
model embedded a query lives in the same package as the decision of which model
embedded the corpus, so "the same model on both sides of a similarity
comparison" is a property of one file rather than a coincidence between two.
A repository that took a string would have to reach for a client, and there
would then be two modules in the build issuing embeddings requests.

## `search_knowledge` takes a context it does not filter on

The labour-law corpus has no employer, no claim and no PHI: fifteen rows of
clearly-labelled synthetic text that read identically for every persona. There
is nothing to narrow.

It still takes a `CallerContext`, for the reason
`data/repositories/embeddings.py` argues at length and one more that belongs
here: these two functions are the copilot's two retrieval calls, reached from
the same run, and Story 6.4 will wrap both as registered tools whose scope the
registry injects. A pair in which one takes a context and the other does not
would make the registry's wiring asymmetric for a distinction the caller cannot
see — and the direction that asymmetry fails in is the dangerous one, because
the tool that *should* be scoped is the one whose parameter would look
optional.

## Why there is a public `/claims/{id}/similar` route behind this

This story ships no UI. AD-15 gates it on a spec the browser can reach, and AC
3 asks for the scope test to run through the *service* rather than only through
the repository — so there has to be one thin, scoped, browser-reachable read.
Story 6.4's similar-case quick action calls `similar_claims` through a
registered tool, not through that route; the route exists to make this story's
guarantee demonstrable rather than to be built on.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.repositories import embeddings as embedding_repo
from services.claims.detail import ClaimNotVisible
from services.rag.claim_text import compose_claim_text
from services.rag.client import EmbeddingClient

#: The most neighbours any caller may ask for. A bound rather than a page,
#: because this is a nearest-neighbour list rather than a collection: the
#: hundredth-nearest claim is not "the next page of similar cases", it is a
#: claim that is not similar. The API clamps to it and the router publishes it,
#: so a caller asking for 500 gets an answer rather than a 422 about a number
#: they had no way to know.
MAX_K = 25


@dataclass(frozen=True)
class SimilarClaim:
    """One neighbour, with the freshness of the vector that matched it.

    `embedded_at` and `stale` ride on every item from day one because AD-12
    requires retrieval to carry them and Story 6.4 discloses staleness past a
    configured threshold. A shape that grew them later would mean every
    consumer written before then had silently shown answers with no provenance
    — and there is no way to tell, after the fact, which of those answers were
    computed against a claim's pre-edit summary.

    `distance` is cosine distance: smaller is more similar, 0 is identical.
    Published rather than converted to a similarity percentage, because a
    percentage is a presentation decision and inventing one here would put a
    number on screen that no service computed (NFR-3's standing rule about
    figures the server did not produce).
    """

    claim_id: str
    employer_short_name: str
    injury_type: str
    severity_score: int
    distance: float
    embedded_at: datetime | None
    stale: bool


@dataclass(frozen=True)
class KnowledgeHit:
    """One retrieved labour-law chunk.

    `source` and `state_code` travel with the text because a retrieved passage
    without its provenance is exactly the thing a model will paraphrase as
    settled law. Every seeded `source` begins `synthetic-demo:` and every
    `chunk_text` opens by saying it is demonstration text, so a consumer that
    quotes the chunk quotes the disclaimer and a consumer that shows the source
    shows the label.
    """

    source: str
    state_code: str | None
    title: str
    chunk_text: str
    distance: float


async def similar_claims(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str | None = None,
    query_text: str | None = None,
    k: int,
    client: EmbeddingClient,
) -> Sequence[SimilarClaim]:
    """Claims in the caller's book nearest to a claim, or to a free-text query.

    Exactly one of `claim_business_id` and `query_text` must be given; both or
    neither is a programming error and raises `ValueError` rather than picking
    one. (Not `InvalidPatch`/422: no route lets a client send both, so reaching
    this is a bug in a caller rather than a bad request.)

    **The target claim is re-composed and re-embedded rather than read out of
    `claim_embedding`.** That costs one model round trip and buys two things.
    The stored vector may be stale — it is exactly the case a handler hits after
    editing the claim they are looking at — so using it would answer the
    question about the claim as it was. And the claim may never have been
    embedded at all, on a fresh deployment before the first refresh tick, where
    reading the stored vector would return nothing with no way to distinguish
    "no similar claims" from "not indexed yet".

    Raises `ClaimNotVisible` for a claim outside the caller's book **and** for
    one that does not exist — `select_claim_detail`'s single-answer rule, which
    the route turns into one 404 so that walking `WC-20000`…`WC-20999` teaches a
    caller nothing.

    An empty book returns an empty list, not an error: a context with zero
    employer assignments is a real state (a handler between assignments), and
    `employer_scope` renders it as a predicate that matches nothing.
    """
    if (claim_business_id is None) == (query_text is None):
        raise ValueError("similar_claims takes exactly one of claim_business_id and query_text")

    exclude_claim_pk: int | None = None
    if claim_business_id is not None:
        row = await embedding_repo.select_claim_embedding_source(
            db, ctx, claim_business_id=claim_business_id
        )
        if row is None:
            raise ClaimNotVisible(claim_business_id)
        injuries = await embedding_repo.select_additional_injuries_for_claims(
            db, ctx, claim_pks=[row.Claim.id]
        )
        text = compose_claim_text(row.Claim, row.Employer, injuries)
        exclude_claim_pk = row.Claim.id
    else:
        assert query_text is not None  # narrowed by the exclusivity check above
        text = query_text

    vectors = await client.embed([text])
    rows = await embedding_repo.select_similar_claims(
        db,
        ctx,
        embedding=vectors[0],
        k=min(k, MAX_K),
        exclude_claim_pk=exclude_claim_pk,
    )
    return [
        SimilarClaim(
            claim_id=row.claim_id,
            employer_short_name=row.employer_short_name,
            injury_type=row.injury_type,
            severity_score=row.severity_score,
            distance=float(row.distance),
            embedded_at=row.embedded_at,
            stale=bool(row.stale),
        )
        for row in rows
    ]


async def search_knowledge(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    query_text: str,
    k: int,
    client: EmbeddingClient,
) -> Sequence[KnowledgeHit]:
    """The `k` labour-law chunks nearest to a question, closest first.

    The caller context is required and is not a filter — see the module
    docstring. Story 6.4's labour-law quick action is the first consumer; this
    story ships the retrieval and no surface for it, because the story's job is
    the substrate.
    """
    vectors = await client.embed([query_text])
    rows = await embedding_repo.select_similar_chunks(
        db, ctx, embedding=vectors[0], k=min(k, MAX_K)
    )
    return [
        KnowledgeHit(
            source=row.source,
            state_code=row.state_code,
            title=row.title,
            chunk_text=row.chunk_text,
            distance=float(row.distance),
        )
        for row in rows
    ]


__all__ = ["MAX_K", "KnowledgeHit", "SimilarClaim", "search_knowledge", "similar_claims"]
