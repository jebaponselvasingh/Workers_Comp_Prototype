"""Repositories — the only layer that talks SQL (AD-1).

AD-7 binds every repository that reads *claim-scoped* data: those methods
take a caller context and apply one unconditional employer filter. Four
modules below are deliberate carve-outs, and each says so in its own
docstring: `identity` is the plumbing that *builds* that context (sessions
and app_user rows), so it necessarily runs before one exists; `glossary`,
`statutory_forms` and `state_rates` read reference data that has no employer,
no claim and no PHI, so there is nothing for a filter to narrow.

`claims` (Story 1.4) is the first scoped repository and the template for
the rest: read its module docstring before writing another one.

`embeddings` (Story 6.1) is the second scoped one, and it is a **partial**
carve-out rather than a whole one: its claim-touching functions apply
`employer_scope(ctx)` like any other, while its three knowledge-corpus
functions take a context they do not filter on. That is a deliberate
divergence from the three modules above, argued in its own docstring — the
short version is that a module doing both should not change signature shape
halfway down.

`insights` (Story 6.2) is the third scoped one, and it is a whole one: every
function reaches `claim` through `employer_scope(ctx)`, the write included. A
cached AI narrative is derived from a claim's clinical text, its reserve and
its fraud score, so serving one across an employer partition is the same leak
as serving the case file it was cut from — there is no carve-out to argue for.

`copilot` (Story 6.3) is the fourth scoped one, and it is a whole one for a
reason one indirection removed from `insights`': its rows carry no claim data
at all, but each is the *key* to a checkpointed transcript that quotes one. It
also carries a second predicate the other three do not — `user_id == ctx.user_id`
— because scope answers "may this caller see this claim?" and not "is this
conversation theirs?", and two handlers can share an employer. That is
ownership, not a widening, and its own docstring argues it.

**The LangGraph checkpoint tables have no repository here and must not get
one.** They are AD-3's registered exception: the saver owns them, migration 0043
vendors their DDL, and the only reader in the process is the saver itself. A
module in this package selecting from `checkpoints` would be the thing that
exception exists to forbid.
"""

from data.repositories import (
    claims,
    copilot,
    embeddings,
    glossary,
    identity,
    insights,
    state_rates,
    statutory_forms,
)

__all__ = [
    "claims",
    "copilot",
    "embeddings",
    "glossary",
    "identity",
    "insights",
    "state_rates",
    "statutory_forms",
]
