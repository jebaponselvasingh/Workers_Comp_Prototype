"""`labor_law_search` — Story 6.1's scoped corpus retrieval, as a tool envelope.

One service call. `services/rag.search_knowledge` embeds the question, asks a
repository that applies the caller's context for the nearest `knowledge_chunk`
rows, and hands back `KnowledgeHit`s. Nothing here re-ranks, re-cuts or
re-scores what came back, and `k` is a module constant rather than an argument
for a reason worth stating up front: a model that could ask for fifty passages
could fill its own context with retrieved text and push the instructions out of
it, which is a prompt-injection amplifier wearing the costume of a tuning knob.

## The passages arrive **fenced**, and that is not the caller's choice

`chunk_text` and `title` are the most obviously untrusted strings in the build.
They come from an ingestion path Epic 6 defers — which is to say from outside
this console — and the whole point of retrieval is to put them in front of a
model. So this wrapper does what `agents/tools/claim.py` does with a claim's
`cause`: every passage is scrubbed and wrapped in a source-tagged fence before
it leaves this function, tagged `knowledge:{source}` exactly as
`agents/insights.py::_knowledge` tags the same corpus.

Fencing **here** rather than in the node that composes the prompt is the
difference between a control and a convention. This entry is registered, so the
grounded-chat agent can call it directly on a free-text turn — and on that path
there is no composing node at all: the envelope goes into a `ToolMessage`
verbatim. A wrapper that returned raw chunk text and trusted its callers to
fence would be correct on the quick-action path and silently unprotected on the
other one.

`distance` is left bare, because it is a float this system computed.
`source` and `state_code` are **not**: they are unbounded `Text` columns filled
by the same ingestion path, and `_identifier` below normalises both before they
are published — see `KnowledgePassage`. `KnowledgeHit` carries **no
`embedded_at`**, so there is no freshness to disclose about the corpus and none
is invented here: AD-12's staleness obligation is about *claim* vectors, whose
`embedded_at` the similar-case wrapper reads, and manufacturing a corpus
equivalent would be a figure no service produced.

## Why the argument schema still names a claim

`search_knowledge` does not take one. `agents/registry.py::KnowledgeArgs`
subclasses `ClaimArgs` anyway — declared there, beside the schema it extends,
because that module's docstring is where "nothing on any argument schema may
name a caller, an employer or a role" is stated and a second file of
model-facing schemas would be a second place to check it — so that this tool is
confined by exactly the same check every other registered tool is:
`agents/registry.invoke` refuses any
`claim_business_id` but the thread's, and a tool exempt from that check would be
the one tool whose arguments injected text could still shape freely. The claim
is not passed to the service — there is no parameter for it — so what the field
buys is the confinement, not a filter.

The retrieval's scope comes from `ctx`, which the registry injects and no
schema can name.
"""

from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from agents.envelope import ToolResult
from agents.fencing import fence, scrub
from data.context import CallerContext
from services.rag import EmbeddingClient, search_knowledge

#: How many passages one quick action is given. Three, which is
#: `agents/insights.py::KNOWLEDGE_CHUNKS`' number and for its reason: the seeded
#: corpus holds fifteen clearly-labelled synthetic chunks, and a briefing handed
#: half of them is a briefing whose relevant passage is buried.
#:
#: A **constant, not an argument** — see the module docstring. `MAX_K` in
#: `services/rag/retrieval.py` is the service's own ceiling and still applies;
#: this is the copilot's choice within it.
KNOWLEDGE_CHUNKS = 3


def _identifier(value: str) -> str:
    """One untrusted corpus identifier, reduced to a single line of plain words.

    `scrub` plus the whitespace collapse `agents/fencing.fence` applies to its
    own tag, and it is applied **here, at publication**, rather than at each
    place a caller happens to interpolate one.

    The reason is that `knowledge_chunk.source` and `knowledge_chunk.state_code`
    are unbounded `Text` columns written by an ingestion path Epic 6 defers —
    which is to say by somebody outside this console — and
    `agents/qas.py::_build_labor_law` writes both into a `figures` line, under
    the one heading `agents/prompts.qas_system_message` tells the model was
    computed by this system and may be quoted verbatim. `fence()` already
    scrubs the tag it derives from `source` for exactly that reason (review of
    Story 6.2, M1); publishing the raw value beside the scrubbed tag handed the
    same string back one layer up, where nothing was looking at it. A source
    that spells a newline, a forged `DETERMINISTIC FIGURES …` heading and a
    settlement amount underneath it is the whole of the exploit, and it is
    already a fixture (`tests/test_prompt_injection_fixtures.py::POISONED_SOURCE`).

    Applied at publication rather than in the composing node so the
    grounded-chat path is covered too: that path has no composing node at all —
    this envelope goes into a `ToolMessage` verbatim — which is the same
    argument the module docstring makes about fencing `text`.
    """
    return " ".join(scrub(value).split())


@dataclass(frozen=True)
class KnowledgePassage:
    """One retrieved chunk, ready to put in front of a model.

    `text` is the fenced, source-tagged item — `<<<LINEWORKER-ITEM source="…">>>`
    and all — so a caller composing a prompt inserts it as-is and a caller
    rendering it to a human would be showing delimiter markup, which is the
    signal that it was never meant for a transcript. `source` and `state_code`
    are published beside it so a briefing can attribute a passage without
    parsing the fence back apart — **scrubbed and collapsed by `_identifier`**,
    because they are as untrusted as the chunk body and land in the authoritative
    half of the prompt.

    `state_code`'s `None` survives that treatment distinctly: "the ingestion
    recorded no jurisdiction" and "the ingestion recorded an empty one" are
    different facts about the corpus, and the node renders the first as "not
    stated".
    """

    source: str
    state_code: str | None
    distance: float
    text: str


@dataclass(frozen=True)
class KnowledgeHits:
    """What the corpus answered, and nothing about how fresh it is.

    A wrapper type rather than a bare tuple so that "there were none" is a
    payload with a shape instead of an empty list a caller has to remember to
    check — the empty-retrieval case is a real state (a corpus that has not been
    seeded, a jurisdiction with no passage) and the briefing has to say so
    rather than invent a statute.
    """

    passages: tuple[KnowledgePassage, ...]
    query_k: int


async def labor_law_search(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
    query_text: str,
    client: EmbeddingClient,
) -> ToolResult[KnowledgeHits]:
    """The nearest labour-law passages to a question, fenced and attributed.

    `claim_business_id` is accepted and deliberately unused — see the module
    docstring: it exists so `agents/registry.invoke` can confine this tool to
    the thread's own claim like every other, and there is no service parameter
    for it to be passed to.

    **An empty result is a success.** A corpus with nothing near the question is
    a real answer — the briefing says there is no passage on file rather than
    writing one — and `ok=False` there would have made "no guidance in the
    corpus" look like a broken tool. `agents/tools/similar.py` makes the same
    call about an empty neighbour list.

    **`unavailable=True` is claimed only for a transport failure**, which is
    `similar_cases`' rule and its reason (review of Story 6.2, M2). Embedding
    the query is a live call, so this is the second wrapper whose failure can be
    an outage rather than a scope refusal — but `EmbeddingDimensionMismatch` is
    a permanent configuration error that names a model and a column width, and
    reporting it as an outage tells an operator to restart a container that was
    answering perfectly well. It propagates, as does a database error and
    anything unanticipated; only "the server did not answer" is absorbed.

    `display` is empty, and `next_actions` records why that is a statement: a
    distance is already rendered by `services/rag.format_distance` for the
    callers that show one, and there is no money and no date in a passage.
    """
    try:
        hits = await search_knowledge(
            db, ctx, query_text=query_text, k=KNOWLEDGE_CHUNKS, client=client
        )
    except (httpx.HTTPError, TimeoutError, OSError):
        # Content-free by construction — nothing from the exception reaches the
        # message (AD-11) — and the node turns it into a plain-terms sentence
        # rather than a stack trace in a transcript.
        return ToolResult.failed("the local model server did not answer", unavailable=True)

    return ToolResult.succeeded(
        KnowledgeHits(
            passages=tuple(
                KnowledgePassage(
                    source=_identifier(hit.source),
                    # `None` is preserved; only a real string is normalised.
                    state_code=(None if hit.state_code is None else _identifier(hit.state_code)),
                    distance=hit.distance,
                    # The tag `agents/insights.py::_knowledge` uses, so the same
                    # corpus is attributed the same way on both surfaces and a
                    # reader who learned to recognise `knowledge:synthetic-demo:…`
                    # on a card recognises it in the chat. `fence` scrubs the tag
                    # itself, so passing the raw column here and the normalised
                    # one to `source` above would still agree — it is passed
                    # normalised so that the published field and the delimiter
                    # line are the same string by construction rather than by
                    # two functions happening to reduce it the same way.
                    text=fence(
                        f"knowledge:{_identifier(hit.source)}",
                        f"{hit.title}: {hit.chunk_text}",
                    ),
                )
                for hit in hits
            ),
            query_k=KNOWLEDGE_CHUNKS,
        )
    )
