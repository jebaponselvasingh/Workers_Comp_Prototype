"""Statutory rate-schedule reads — the fourth sanctioned unscoped repository.

**Why `rate_for_state` takes no `CallerContext`.** AD-7 binds every repository
that reads claim-scoped data: a required caller context and one unconditional
employer filter. `state_rate_schedule` has no employer column, no claim
relationship and no PHI — it is seventeen rows saying what a jurisdiction's
weekly indemnity bounds are, identical for every claim and every persona. There
is nothing here to scope, and a context parameter would be decoration:
accepted, ignored, and read by the next author as evidence that scoping had
been enforced when nothing had. `glossary.py` and `statutory_forms.py` carve
themselves out on exactly this basis; `identity.py` on a different one (it is
what *builds* the context).

The claim-scoped half of the question is asked elsewhere and first: the caller
resolves the claim through `claim_repo.select_claim_detail` — which is scoped —
and reads its `state` off the row it got back. What arrives here is a
two-letter state code, and a state belongs to nobody.

Authentication still applies (the endpoint sits behind the app-level
dependency), so "unscoped" means "the same answer for every authenticated
caller", never "public".

**`None` is a data error, not an empty result.** Every state a claim is filed
in has a schedule — migration 0023 refuses to complete otherwise — so a miss
here means a claim was inserted for a jurisdiction nobody has loaded rates for.
The caller raises rather than defaulting; the prototype's silent
``|| {max:1200,min:250}`` is precisely what NFR-4 rules out, and it is why this
function returns an option rather than a row-or-fallback.
"""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from data.models import StateRateSchedule


async def rate_for_state(db: AsyncSession, state_code: str) -> StateRateSchedule | None:
    """One jurisdiction's weekly bounds, or `None` if none is loaded.

    `state_code` is unique, so this is `one_or_none()` rather than a
    latest-effective-date pick. When a refresh process adds a second year's
    figures the constraint moves to `(state_code, effective_date)` and this
    query grows the `effective_date <= as_of` predicate and an
    `ORDER BY effective_date DESC LIMIT 1` — the shape `rules/engine.py`'s
    `load` already uses for rule documents, which is why the column is here
    before anything needs it.
    """
    return (
        await db.scalars(
            sa.select(StateRateSchedule).where(StateRateSchedule.state_code == state_code)
        )
    ).one_or_none()
