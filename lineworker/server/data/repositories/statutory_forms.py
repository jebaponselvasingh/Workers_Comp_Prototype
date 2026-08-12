"""Statutory form reference reads — the third sanctioned unscoped repository.

**Why `forms_for_path` takes no `CallerContext`.** AD-7 binds every repository
that reads claim-scoped data: a required caller context and one unconditional
employer filter. `path_required_form` has no employer column, no claim
relationship and no PHI — it is nine rows saying which filings each handling
path requires, identical for every claim and every persona. There is nothing
here to scope, and a context parameter would be decoration: accepted, ignored,
and read by the next author as evidence that scoping had been enforced when
nothing had. `glossary.py` carves itself out on exactly this basis;
`identity.py` on a different one (it is what *builds* the context).

The claim-scoped half of the question is asked elsewhere and first: the caller
resolves the claim through `claim_repo.select_claim_detail` — which is scoped —
and derives its path from the row it got back. What arrives here is a path,
not a claim, and a path belongs to nobody.

Authentication still applies (the endpoint sits behind the app-level
dependency), so "unscoped" means "the same answer for every authenticated
caller", never "public".

Ordering is fixed explicitly on `sort_order`, which is unique *within a path* —
so the ordering is total for any single-path query, and a paged version could
not silently repeat or drop a row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from data.models import PathRequiredForm
from data.models.enums import ClaimPath


async def forms_for_path(db: AsyncSession, path: ClaimPath) -> Sequence[PathRequiredForm]:
    """The forms one handling path requires, in the prototype's display order.

    Filtered in SQL rather than by loading all nine and partitioning in Python.
    Nine rows would make either honest, but the query says what it means, and a
    "load everything and filter in the service" habit established on reference
    data is the habit that later gets applied to a claim table.
    """
    return (
        await db.scalars(
            sa.select(PathRequiredForm)
            .where(PathRequiredForm.path == path)
            .order_by(PathRequiredForm.sort_order)
        )
    ).all()
