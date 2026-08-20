"""Thin wrappers over deterministic services — the registry's entries.

Five functions, one service call each, no business logic, every one returning
AD-13's `{ok, data, display}` envelope. They are what stands between a model
and a figure: `agents/insights.py` gathers through these and nowhere else, and
`agents/registry.py` registers four of them plus `claim_reader`, so "which
numbers is an answer entitled to?" has a five-item answer a reviewer can read
in one sitting.

## Why they are here rather than inside `services/rag`

Gathering happens in `agents/` on purpose, and Story 6.3 is why. This module
shipped in 6.2 saying that promoting these into the registry "should be a
change to how they are declared, not a move between packages" — and that is
exactly what happened: the functions did not move, did not change signature and
did not grow a parameter. `agents/registry.py` declares each one with a typed
argument schema, a `kind`, and injected scope and session; the file it lives in
is the file it lived in. Putting them inside the `services/rag` command would
have meant 6.3 relocating them or registering wrappers around wrappers.

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
from agents.tools.claim import claim_reader
from agents.tools.fraud import FraudSignals, fraud_signals
from agents.tools.reserve import reserve_check
from agents.tools.similar import NEIGHBOUR_COUNT, SimilarCases, similar_cases

__all__ = [
    "NEIGHBOUR_COUNT",
    "FraudSignals",
    "SimilarCases",
    "claim_reader",
    "fraud_signals",
    "next_actions",
    "reserve_check",
    "similar_cases",
]
