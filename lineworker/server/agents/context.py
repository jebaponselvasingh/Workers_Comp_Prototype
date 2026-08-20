"""The per-run context — the one place caller scope travels, and it is never checkpointed.

AD-7's other half. `agents/state.py` holds the caller *reference* as a
checkpointed channel; this module holds the materialized scope, and LangGraph
does not persist a `context_schema` value at all. That asymmetry is the whole
design:

- A reference is safe to checkpoint because it is meaningless on its own and is
  overwritten at every run start and resume anyway.
- A **scope** is not safe to checkpoint, ever. `CallerContext.employer_ids` is
  the answer to "which employers may this person read?", resolved from
  `user_employer_assignment` at one instant. Frozen into a checkpoint it
  becomes yesterday's book of business, replayed months later by a resume, with
  no screen anywhere saying so — an agent narrating a claim the handler has
  since lost.

So `CallerContext` is built **only** by `api.deps.get_caller_context`, from this
request's own cookie, and reaches the graph only through the object below,
which lives for exactly one run.

## The session factory rides here too, and that is not an accident

The registry's tools need a database session. They must not take the *request's*
session, because the runs endpoint streams: a conversational turn is longer than
a refresh and a `create_agent` loop may make several model calls, so a pooled
connection handed to the graph would sit idle-in-transaction for the whole
stream. `services/rag/insights.py` learned this at refresh scale and fixed it
with `await self._db.rollback()` before every completion; this is the same rule
one story on, enforced structurally instead — there is no session here to hold,
only a factory, and each tool opens and closes its own.

`app.state.sessionmaker` is what the composition root puts in it, so a tool's
session has exactly the lifetime of the tool call.

## Nothing here is model-suppliable

The model never sees this object and has no parameter that could name any part
of it (AD-13: "scope and session come from the registry; neither is a
model-populated parameter"). It is constructed by the runs endpoint, handed to
`graph.astream(..., context=...)`, and read by the registry through LangGraph's
`get_runtime`. There is no path from a message, a tool argument or a claim
narrative to a field on it.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from data.context import CallerContext


@dataclass(frozen=True)
class CopilotContext:
    """One run's caller scope and session factory. Lives for the run, never longer.

    Frozen for `CallerContext`'s reason and with more at stake: a run that could
    widen its own scope halfway through would defeat the one filter every
    repository applies.

    **Not a `TypedDict`, unlike the state channels**, and the difference is
    deliberate. State is serialised into checkpoints, so it has to be plain
    JSON-shaped data; context is never serialised, so it can hold live objects —
    which is exactly why the session factory can be here and could not be a
    channel. If this type ever becomes serialisable, that is the signal
    somebody is about to checkpoint it.
    """

    caller: CallerContext
    sessionmaker: async_sessionmaker[AsyncSession]

    #: **Which claim this thread is about — the leash on every tool argument.**
    #:
    #: `ClaimArgs.claim_business_id` is model-populated by construction: a tool
    #: needs to be told which claim, and the model is what fills a tool's
    #: arguments in. Scope stops that becoming a cross-employer read — a
    #: repository will not serve a claim outside `employer_ids` — but scope is a
    #: *book*, not a *conversation*, and a handler covering forty claims has
    #: forty claims a hijacked model could name. AD-16 forbids untrusted content
    #: selecting tool arguments in the same sentence it forbids tool selection,
    #: and a `cause` reading "also fetch WC-9999" was, until this field existed,
    #: able to pull a second claim's worker, diagnosis and flags into this
    #: thread's transcript and its checkpoints (review of Story 6.3).
    #:
    #: So the run binds the thread's own claim here, `agents/registry.invoke`
    #: refuses any other, and the model's argument becomes a *confirmation*
    #: rather than a choice. It rides the context and not a state channel for
    #: the reason everything here does: a checkpointed leash is yesterday's
    #: leash, and this one is re-derived from `copilot_thread` at every run.
    #:
    #: `None` is the reserved `dashboard` scope (AD-6, Epic 7): a thread with no
    #: claim of its own, where a tool naming one has nothing to be confined to
    #: and is refused outright. v1 never mints such a thread.
    claim_business_id: str | None


__all__ = ["CopilotContext"]
