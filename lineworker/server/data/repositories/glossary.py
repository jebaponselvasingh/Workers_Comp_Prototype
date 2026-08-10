"""Glossary reference reads — the second sanctioned unscoped repository.

**Why `list_terms` takes no `CallerContext`.** AD-7 binds every repository
that reads claim-scoped data: a required caller context and one
unconditional employer filter. `glossary_term` has no employer column, no
claim relationship and no PHI — it is 25 rows of industry vocabulary,
identical for a handler, a supervisor and an analyst. There is nothing here
to scope, so a context parameter would be decoration: accepted, ignored,
and read by the next author as evidence that scoping had been considered
when in fact nothing had been enforced. `identity.py` carves itself out for
a different reason (it is what *builds* the context); this module carves
itself out because the data it reads has no owner.

Authentication still applies — the endpoint sits behind the app-level
dependency — so "unscoped" here means "the same answer for every
authenticated caller", never "public".

The ordering is fixed explicitly, on the unique `sort_order`, so a paged
version cannot silently repeat or drop rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from data.models import GlossaryTerm


async def list_terms(db: AsyncSession) -> Sequence[GlossaryTerm]:
    """Every glossary term, in the prototype's display order."""
    return (await db.scalars(sa.select(GlossaryTerm).order_by(GlossaryTerm.sort_order))).all()
