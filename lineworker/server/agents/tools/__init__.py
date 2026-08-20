"""Thin wrappers over deterministic services — the registry's entries.

Seven functions, one service call each, no business logic, every one returning
AD-13's `{ok, data, display}` envelope. They are what stands between a model
and a figure: `agents/insights.py` gathers through these and nowhere else, and
`agents/registry.py` registers every one of them, so "which numbers is an
answer entitled to?" has a seven-item answer a reviewer can read in one
sitting.

## Why they are here rather than inside `services/rag`

Gathering happens in `agents/` on purpose, and Story 6.3 is why. This module
shipped in 6.2 saying that promoting these into the registry "should be a
change to how they are declared, not a move between packages" — and that is
exactly what happened: the functions did not move, did not change signature and
did not grow a parameter. `agents/registry.py` declares each one with a typed
argument schema, a `kind`, and injected scope and session; the file it lives in
is the file it lived in. Putting them inside the `services/rag` command would
have meant 6.3 relocating them or registering wrappers around wrappers.

**What 6.4 added here is two**, and both are retrieval-shaped rather than
figure-shaped. `knowledge.py` wraps `services/rag.search_knowledge` for the
labour-law quick action, and `rtw.py` projects `claim_detail` down to the three
return-to-work facts a draft letter may state. Both fence what a person wrote
before it leaves the wrapper, for `claim.py`'s reason and with one addition
that only applies now that the tools are registered: an entry the grounded-chat
agent can call directly has no composing node to fence on its behalf, so a
wrapper that returned raw text would be protected on the quick-action path and
unprotected on the free-text one.

6.4 also *registers* `similar_cases`, which shipped here in 6.2 and stayed out
of the registry through 6.3 because it needs an `EmbeddingClient` and a
staleness window. Neither may be a field on an argument schema (AD-16), so
`agents/registry.py` grew a declaration for context-injected keyword arguments
instead — and the function itself is unchanged, which is this package's whole
pattern: promoting a wrapper is a change to how it is declared.

**What 6.3 added here is one function**, `claim_reader` — the case-file header,
which is the first thing a grounded conversation needs and which the insight
pipeline never wanted because its four kinds each start from a figure. What it
did *not* add is a `kind` argument, a context parameter or a registry import:
the entries stay ignorant of being registered, which is what keeps them
testable as plain functions and what let 6.2's four be absorbed without a diff.

## The rule these five keep

One service call each. If a wrapper needs a threshold, it asks the rules tier
and hands the answer to a *registered derivation* rather than comparing
anything itself (`fraud.py` is the one where that distinction bites, and it
says so). If a wrapper needs a formatted figure, it calls the service layer's
own formatter (`reserve.py`). No wrapper filters, sorts, re-ranks, re-cuts or
re-bands what a service returned — those are the derivations AD-10 gives
exactly one computer each, and a tool is not it.
"""

from agents.tools.actions import next_actions
from agents.tools.claim import ClaimContext, claim_reader
from agents.tools.fraud import FraudSignals, fraud_signals
from agents.tools.knowledge import (
    KNOWLEDGE_CHUNKS,
    KnowledgeHits,
    KnowledgePassage,
    labor_law_search,
)
from agents.tools.reserve import reserve_check
from agents.tools.rtw import RtwContext, rtw_reader
from agents.tools.similar import NEIGHBOUR_COUNT, SimilarCases, similar_cases

__all__ = [
    "KNOWLEDGE_CHUNKS",
    "NEIGHBOUR_COUNT",
    "ClaimContext",
    "FraudSignals",
    "KnowledgeHits",
    "KnowledgePassage",
    "RtwContext",
    "SimilarCases",
    "claim_reader",
    "fraud_signals",
    "labor_law_search",
    "next_actions",
    "reserve_check",
    "rtw_reader",
    "similar_cases",
]
