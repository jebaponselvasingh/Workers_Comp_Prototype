"""`reserve_check` — the reserve adequacy verdict, as a tool envelope.

One service call, no business logic, no second opinion. Everything the reserve
insight is allowed to say a number about comes out of here, and it comes out of
here because `services/financials.reserve_check_for_claim` computed it (AD-2).

**It reaches that function through `services/claims/detail.claim_detail`**, and
that is worth one paragraph because the indirection looks like a shortcut and
is the opposite. `reserve_check_for_claim` needs a loaded `Claim`, a computed
`Benefit` and the claim's surrogate key before it can be called; assembling
those here would mean this wrapper doing three reads and a benefit calculation
of its own — which is precisely the "business logic in a tool" the thin-wrapper
rule forbids, and which would give the insight a *second* path to a figure the
case file already has. `claim_detail.reserve_check` **is**
`reserve_check_for_claim`'s return value, unmodified, so calling it here is the
same computation reached by the same route the Overview card and the Bills tab
use. The AD-2 equality test asserts exactly that, by calling `claim_detail`
itself and comparing.

**`display` is the service layer's own formatter.** `format_dollars` is what
the reserve *rationale* is already written with (`services/financials/
rationale.py`), so a narrative that quotes these strings quotes the same money
the card beside it renders — down to the rounding, which is documented as
agreeing with the browser's `money.ts` and would otherwise be a second one.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from agents.envelope import ToolResult
from data.context import CallerContext
from services.claims.detail import ClaimNotVisible, claim_detail
from services.financials import ReserveCheck, format_comp_rate, format_dollars


async def reserve_check(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    claim_business_id: str,
) -> ToolResult[ReserveCheck]:
    """The claim's reserve verdict and its exposure terms, scoped.

    `ok=False` for a claim the caller cannot see, rather than a raised
    `ClaimNotVisible`: the generator has already resolved the claim through
    `services/rag`, so reaching this branch means the claim moved out from
    under the run, and a kind that quietly does not generate is the right
    outcome for that.

    **A `None` money figure is passed through as absent, never as zero.**
    `remaining_medical_cents is None` means the bills are not on file, which
    forces `verdict` to `indeterminate` and `ratio_bp` to `None`; a wrapper that
    formatted it as `"$0"` would hand the model a figure meaning "nothing is
    outstanding" for a state that means "nobody knows". The reserve content
    schema refuses a quoted ratio in that state for the same reason, and this
    is where the honesty starts.
    """
    try:
        detail = await claim_detail(db, ctx, claim_business_id)
    except ClaimNotVisible:
        return ToolResult.failed("the claim is not in this caller's book")

    check = detail.reserve_check
    display = {
        "reserveCents": format_dollars(check.reserve_cents),
        "remainingIndemnityCents": format_dollars(check.remaining_indemnity_cents),
        "disbursedIndemnityCents": format_dollars(check.disbursed_indemnity_cents),
        "disbursedMedicalCents": format_dollars(check.disbursed_medical_cents),
    }
    if check.remaining_medical_cents is not None:
        display["remainingMedicalCents"] = format_dollars(check.remaining_medical_cents)
    if check.projected_remaining_cents is not None:
        display["projectedRemainingCents"] = format_dollars(check.projected_remaining_cents)
    if check.ratio_bp is not None:
        # The exposure ratio, formatted by the service that owns basis points
        # (`services/financials.format_comp_rate` — the same function the
        # comp-rate row is rendered through). It was an f-string in
        # `agents/insights.py` until the Story 6.2 review pointed out that the
        # package forbidden to originate a figure was formatting one, and that
        # the string was reaching the prompt without passing through `display`
        # at all (AD-2/AD-13).
        #
        # Present exactly when `ratio_bp` is — which is exactly when the bills
        # are on file — so the absence of this key is itself the "there is no
        # ratio to quote" state rather than a second condition to remember.
        display["ratioBp"] = f"{format_comp_rate(check.ratio_bp)}%"
    return ToolResult.succeeded(check, display=display)
