"""`next_actions` — the auto-generated checklist, as a tool envelope.

One service call. `services/worklist.claim_actions` evaluates eleven trigger
rules over a rule document's parameters, ranks them, caps them and pads a short
list; this wrapper hands the result on unchanged. The insight's job is to
explain *why* those rows are what a handler should do next, in the order the
server already put them in — not to re-rank, re-cap or re-label them (AD-2).

**`display` is empty, and that is a statement rather than an oversight.** The
checklist carries labels, urgencies and targets: no money, no dates, nothing
whose rendering could differ between the sentence and the card. An envelope that
manufactured a display string here would be inventing a formatting decision to
fill a field, which is how the field stops meaning "the service's own
rendering".
"""

from sqlalchemy.ext.asyncio import AsyncSession

from agents.envelope import ToolResult
from data.context import CallerContext
from services.claims.detail import ClaimNotVisible
from services.worklist.actions import ClaimActions, claim_actions


async def next_actions(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
) -> ToolResult[ClaimActions]:
    """The claim's ranked action checklist and the rule document that shaped it.

    `ok=False` for a claim the caller cannot see, `reserve_check`'s rule and its
    reason.

    The whole `ClaimActions` rather than only its `items`, because `cap`,
    `padding_floor` and `rules_version` are what make the list's *length*
    explicable — and an insight that said "these are the six most urgent" when
    the cap was four would be asserting a rule nobody applied.
    """
    try:
        actions = await claim_actions(db, ctx, claim_business_id)
    except ClaimNotVisible:
        return ToolResult.failed("the claim is not in this caller's book")
    return ToolResult.succeeded(actions)
