"""`similar_cases` — Story 6.1's scoped vector search, as a tool envelope.

One service call. `services/rag.similar_claims` composes the target claim's
clinical summary, embeds it, and asks a repository that applies
`employer_scope(ctx)` for the nearest neighbours — so the scope guarantee on
this path is the one AD-7 already gives every claim read, reached through the
one function that owns it. Nothing here filters, re-ranks or re-cuts the list.

**The gather runs under the subject claim's partition, not the actor's.** This
is the one wrapper of the four that reads *other* claims, and what it reads is
written into `ai_insight` and then served to everybody who can see the subject.
The scheduled refresh runs as the system actor, whose `scope_all` makes
`employer_scope` the AD-7 tautology — so without the narrowing below the
persisted card names neighbouring claim ids and employer short names from the
whole portfolio, and hands them to a handler scoped to one plant (review of
Story 6.2, H1). `services/rag.subject_scoped_context` is where that rule is
argued; it is applied here rather than inside `similar_claims` because the
interactive `/claims/{id}/similar` route genuinely does want the reader's whole
book.

**`display` carries one entry per neighbour — its distance, keyed by claim id.**
The string comes from `services/rag.format_distance`, the service that owns the
figure, so the composer looks a distance up instead of formatting one; AD-13's
rule is that a display string is produced once, by the service layer, and
nothing downstream re-formats it.

**The freshness disclosure is computed here.** AD-12 requires
retrieval to return `embedded_at` and answers to disclose staleness past a
configured threshold, and this is where that threshold is applied: a neighbour
whose vector is flagged `stale`, or whose `embedded_at` is older than
`INSIGHT_STALENESS_DISCLOSURE_DAYS`, makes the wrapper produce a sentence the
insight is required to carry. The *count* and the sentence are both computed
here rather than by the model, because "how many of these are out of date" is a
figure (AD-2) and the disclosure is the thing the reader is entitled to.

**Distances are not converted to a similarity percentage**, and no wrapper
should ever add one. `SimilarClaim.distance` is cosine distance published raw
precisely because a percentage is a presentation decision, and a "92% similar"
invented in a tool envelope would be a figure no service computed — with a
model about to write a sentence around it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agents.envelope import ToolResult
from data.context import CallerContext
from services.claims.detail import ClaimNotVisible
from services.rag import (
    EmbeddingClient,
    EmbeddingDimensionMismatch,
    SimilarClaim,
    format_distance,
    similar_claims,
    subject_scoped_context,
)

#: How many neighbours an insight narrates. Five, which is the `/similar`
#: route's own default and is a card's worth: the sixth-nearest claim is not
#: "the next page of similar cases", it is a claim that is not similar
#: (`services/rag/retrieval.py::MAX_K` argues the whole case).
NEIGHBOUR_COUNT = 5


@dataclass(frozen=True)
class SimilarCases:
    """The neighbour list and what the reader must be told about its freshness.

    `stale_count` and `disclosure` are computed from the items rather than left
    to the card, because the disclosure is an AD-12 obligation on the *answer*
    and a card that decided when to show it would be a second place the
    threshold lives.

    `disclosure` is `None` when nothing is stale, which is a different fact from
    an empty string and is what lets the content schema require the sentence
    exactly when there is one to require.
    """

    items: tuple[SimilarClaim, ...]
    stale_count: int
    disclosure: str | None


async def similar_cases(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
    client: EmbeddingClient,
    staleness_days: int,
    k: int = NEIGHBOUR_COUNT,
    as_of: datetime | None = None,
) -> ToolResult[SimilarCases]:
    """The nearest claims at the subject claim's employer, with freshness disclosed.

    `ok=False` for a claim the caller cannot see, `reserve_check`'s rule and its
    reason. `ok=False` for an unreachable model server too — embedding the query
    claim is a live call, so this is the one wrapper of the four whose failure
    can be an outage rather than a scope refusal, and the generator turns it
    into the same "this kind did not generate" outcome either way.

    **An empty neighbour list is a success, not a failure.** A handler between
    assignments has an empty book, and an employer partition holding one claim
    has no neighbours for it; both are real states and the insight says so
    explicitly rather than rendering an empty card (the I/O matrix's "empty
    book" row). `ok=False` there would have made "no similar cases" look like a
    broken tool.

    **The set is the subject claim's employer partition, not the caller's book**
    — see `subject_scoped_context` above and the narrowing argued in the module
    docstring. Everything that describes this list to a reader or to a model has
    to say so, and for a while did not (follow-up review of Story 6.2, B9): for
    a handler covering two employers, "your caseload" names a strictly larger
    set than the one that was searched.

    `as_of` is injectable for the reason every other clock in this codebase is:
    a staleness disclosure computed from `datetime.now()` is a test that has to
    write rows with fabricated timestamps to say anything at all.

    **`unavailable=True` is claimed only for a transport failure**, and the
    narrowness is the point (review of Story 6.2, M2). It becomes a 503 reading
    "Model server unavailable" on the interactive route, so a bare `except
    Exception` here reported a configuration error, a database error and a
    plain bug as a model outage — telling an operator to restart a container
    that was answering perfectly well. `EmbeddingDimensionMismatch` is the worst
    of those: `services/rag/client.py` raises it *deliberately* as a permanent
    configuration error naming the model, the width it returned and the width
    the column holds, and `services/rag/embeddings.py` re-raises it rather than
    counting it for exactly that reason. It is re-raised here too, as is
    `SQLAlchemyError` and anything this function did not anticipate; the
    generator's caller counts the kind as failed and logs the exception class.
    """
    subject = await subject_scoped_context(db, ctx, claim_business_id=claim_business_id)
    if subject is None:
        return ToolResult.failed("the claim is not in this caller's book")

    try:
        items = await similar_claims(
            db, subject, claim_business_id=claim_business_id, k=k, client=client
        )
    except ClaimNotVisible:
        return ToolResult.failed("the claim is not in this caller's book")
    except (EmbeddingDimensionMismatch, SQLAlchemyError):
        # A model of the wrong dimensionality, or a database that refused the
        # statement. Neither is an outage and neither gets better on the next
        # tick, so neither may wear the 503 that tells an operator to wait.
        raise
    except (httpx.HTTPError, TimeoutError, OSError):
        # The model server did not answer: unreachable, refused, timed out.
        # Content-free by construction — nothing from the exception reaches the
        # message (AD-11) — and the generator's caller reports the kind as
        # failed rather than writing a neighbour list from nowhere.
        return ToolResult.failed("the local model server did not answer", unavailable=True)

    now = as_of or datetime.now(UTC)
    cutoff = now - timedelta(days=staleness_days)
    stale = _stale_items(items, cutoff)
    return ToolResult.succeeded(
        SimilarCases(
            items=tuple(items),
            stale_count=len(stale),
            disclosure=_disclosure(len(stale), len(items), staleness_days),
        ),
        # Keyed by claim id so the composer can look a neighbour's distance up
        # rather than format one: `services/rag.format_distance` produces the
        # string, once, and nothing downstream re-formats it (AD-13, and the
        # rule `agents/envelope.py` states).
        display={item.claim_id: format_distance(item.distance) for item in items},
    )


def _stale_items(items: Sequence[SimilarClaim], cutoff: datetime) -> list[SimilarClaim]:
    """Neighbours whose vector is flagged stale or predates the cutoff.

    Both conditions, because they are different facts. `stale` means an edit has
    landed since the vector was computed and the next refresh tick will redo it;
    an old `embedded_at` means no tick has touched the row in longer than this
    deployment's disclosure window, which usually means the refresh job itself
    has been failing. A reader is owed the same warning either way.

    A neighbour with no `embedded_at` cannot appear here — `select_similar_claims`
    excludes rows with a NULL vector — but the guard is written anyway, because
    the type admits `None` and a `None` treated as "fresh" is the wrong default
    for a disclosure.
    """
    return [
        item
        for item in items
        if item.stale or item.embedded_at is None or item.embedded_at < cutoff
    ]


def _disclosure(stale_count: int, total: int, staleness_days: int) -> str | None:
    """The sentence the insight must carry, or `None` when nothing is stale.

    Written here rather than by the model because it states two counts and a
    configured window — three figures, and AD-2 says the model originates none
    of them. What the model does with it is quote it.
    """
    if stale_count == 0:
        return None
    return (
        f"{stale_count} of {total} comparable claims were last indexed more than "
        f"{staleness_days} days ago or have changed since; treat their outcomes as indicative."
    )
