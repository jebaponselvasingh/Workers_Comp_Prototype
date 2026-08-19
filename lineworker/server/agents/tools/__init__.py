"""Thin wrappers over deterministic services — **not** a tool registry.

Four functions, one service call each, no business logic, every one returning
AD-13's `{ok, data, display}` envelope. They are what stands between a model
and a figure: `agents/insights.py` gathers through these and nowhere else, so
"which numbers is a narrative entitled to?" has a four-item answer a reviewer
can read in one sitting.

## Why they are here rather than inside `services/rag`

Gathering happens in `agents/` on purpose, and the purpose is one story away.
Story 6.3 promotes these same four functions into the *registered* tool
registry — typed argument schemas, a declared read/write/refresh kind, scope
injected by the registry, structured errors into the transcript — and doing
that should be a change to how they are declared, not a move between packages.
Putting them inside the `services/rag` command would have meant 6.3 either
relocating them or registering wrappers around wrappers.

**This story deliberately does not build the registry**, the `kind`
declarations, or context injection. It builds the four functions and the
envelope, keeps them thin enough to absorb, and stops. A registry with one
consumer is a registry designed against one use.

## The rule these four keep

One service call each. If a wrapper needs a threshold, it asks the rules tier
and hands the answer to a *registered derivation* rather than comparing
anything itself (`fraud.py` is the one where that distinction bites, and it
says so). If a wrapper needs a formatted figure, it calls the service layer's
own formatter (`reserve.py`). No wrapper filters, sorts, re-ranks, re-cuts or
re-bands what a service returned — those are the derivations AD-10 gives
exactly one computer each, and a tool is not it.
"""

from agents.tools.actions import next_actions
from agents.tools.fraud import FraudSignals, fraud_signals
from agents.tools.reserve import reserve_check
from agents.tools.similar import NEIGHBOUR_COUNT, SimilarCases, similar_cases

__all__ = [
    "NEIGHBOUR_COUNT",
    "FraudSignals",
    "SimilarCases",
    "fraud_signals",
    "next_actions",
    "reserve_check",
    "similar_cases",
]
