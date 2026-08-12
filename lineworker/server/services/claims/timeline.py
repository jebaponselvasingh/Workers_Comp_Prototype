"""The `timeline_event` writer — AD-12 says there is exactly one, and it is
inside `services/claims`.

Story 2.2 created and seeded the table and read it into every stage variant;
this module is its **first runtime emitter**. The signature is deliberately
awkward to misuse: an event is appended by a command that has already
performed the mutation it describes, in that command's transaction, so there
is no commit here and no way to append an event for a change that did not
happen.

**Why a module of its own rather than two lines inside `edit.py`.** Epic 3's
payment approvals, Epic 4's meetings and diary notes and Epic 6's generated
letters all append here too. AD-12's "sole emitter" is a claim about the
codebase, and it is only checkable if there is one function to point at —
`tests/test_claim_edit_validation.py` asserts that **exactly one** module in
the tree constructs a `TimelineEvent` — this one.

**`claim_id` is the surrogate primary key**, not the `WC-nnnn` business id —
the house quirk `TimelineEvent`'s own docstring documents. Taking it as an
`int` named `claim_pk` makes the mistake a type error rather than a foreign
key violation at commit time.
"""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from data.models import TimelineEvent
from data.models.enums import TimelineTag


async def append(
    db: AsyncSession,
    *,
    claim_pk: int,
    description: str,
    tag: TimelineTag,
    event_date: date | None,
) -> TimelineEvent:
    """Add one timeline row to the caller's transaction. Does not commit.

    No `version`, no update path, no delete: the table is append-only
    (`TimelineEvent`'s docstring argues why), so this is the whole write
    surface it will ever have.
    """
    event = TimelineEvent(
        claim_id=claim_pk,
        event_date=event_date,
        description=description,
        tag=tag.value,
    )
    db.add(event)
    return event
