"""The worklist's approval surface (Story 3.4, AC 1) — AD-12's delegation.

`services/worklist` has been read-only since Story 1.4; this is its first
command, and it writes nothing. AD-12 names this exact arrangement:

> `payment_schedule_week` is owned by `services/financials` (worklist approval
> calls financials' command)

So the shape here is fixed by the architecture rather than chosen: this module
gates capability, resolves the claim, delegates the write, and re-reads the
result. It contains no SQL against `payment_schedule_week`, `bill` or `expense`
and never will — `tests/test_payment_ownership.py` asserts that structurally
rather than trusting this paragraph.

## Why the approval command lives here rather than on the financials package

Because the *caller* is the worklist. Story 3.5's action checklist is a
worklist surface, and its "Review bill → Approve payment" action lands on this
function; the Bills tab's ✓ button reaches the same one through the same route.
Putting the capability gate and the claim resolve here means both callers get
one refusal ladder and one response shape, and `services/financials` stays what
it is everywhere else in Epic 3: a package that computes and writes money from
identifiers it is handed, and never resolves a claim.

## The response is the whole financials payload, and that is AD-9

An approval moves more than a status. `paid_to_date` does not change (nothing
is disbursed yet), but `next_payment_due` skips the newly-scheduled week,
`installments_paid` does not move but the schedule's chips do, and the reserve
verdict is computed from the same rows. Returning an acknowledgement would
leave the SPA deciding which of its cached figures the approval invalidated —
which is precisely the reasoning `update_claim_fields` gives for returning the
whole case file, and precisely what AD-9 forbids the browser from doing.

So: one read model in, one read model out, and the 409 carries it too. The
client installs what it is given and computes nothing.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models.enums import UserRole
from data.repositories import claims as claim_repo
from services.claims.detail import ClaimNotVisible, claim_financial_detail
from services.financials.approval import (
    Approval,
    approve_bill_payment,
    approve_expense_payment,
    approve_schedule_week,
)
from services.financials.summary import ClaimFinancials

log = structlog.get_logger()

#: What a caller can ask to approve. A closed union rather than a free string,
#: so an unknown kind is a type error here and a 422 from the generated client
#: rather than a branch that falls through to "nothing happened".
ApprovalKind = Literal["week", "bill", "expense"]

APPROVAL_KINDS: Final[tuple[ApprovalKind, ...]] = ("week", "bill", "expense")


class ApprovalNotPermitted(PermissionError):
    """The caller's role does not carry the approve capability (AD-7).

    Handlers approve payments; supervisors and analysts read (BR-ROLE). Raised
    **before the claim is looked up**, which is `services/claims/edit.py`'s
    ordering rule and a security property rather than a style choice: if scope
    were checked first, a supervisor would get 404 for a claim outside their
    book and 403 for one inside it, and the difference would answer "does this
    claim exist?".
    """


@dataclass(frozen=True)
class ApprovalResult:
    """The fresh read model, and what the approval actually did.

    Two fields because the route needs both: the payload is the response body,
    and `approval` is what the structured log line and (later) Story 3.5's
    action-checklist acknowledgement describe. Keeping them together means the
    caller cannot report an approval that a different read produced.
    """

    financials: ClaimFinancials
    approval: Approval


async def approve_payment(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    *,
    kind: ApprovalKind,
    target_id: int,
    expected_version: int,
    batch_weekdays: frozenset[int],
    as_of: date | None = None,
    now: datetime | None = None,
) -> ApprovalResult:
    """Approve one payment on one claim, and answer with the fresh figures.

    `target_id` is a **week number** when `kind` is `"week"` and a surrogate
    row id otherwise, which is the two tables' own identities rather than an
    inconsistency: a schedule week is addressed by `(claim, week_no)` — its
    unique constraint — and a line item has no natural key at all. The wire
    contract says so in the same words (`ScheduleWeekResponse` publishes no
    `id`; `BillResponse` does).

    `batch_weekdays` is threaded through to the read model for the reason
    `claim_financial_detail` gives: the disbursement cadence is deployment
    config, it comes from `Settings` at the route, and no service here reads it
    for itself. It is required rather than defaulted so that a caller cannot
    silently render a "next batch" date computed from a cadence nobody
    deployed.

    Raises `ApprovalNotPermitted` (403), `ClaimNotVisible` (404),
    `PaymentNotVisible` (404) or `StalePaymentRow` (409). The last two come
    from the owning command and travel through untouched — this function
    re-raises nothing and translates nothing, because a second wording of one
    refusal is a second refusal a caller can tell apart.

    **This function does not own a transaction**, and the distinction matters:
    the financials command commits its own write (as every AD-4 command in this
    build does), and the re-read below happens afterwards, in a fresh
    transaction. A composite that needed several writes atomically would have
    to call the pieces rather than this function —
    `update_claim_fields`'s docstring makes the same warning about itself.
    """
    if ctx.role is not UserRole.handler:
        raise ApprovalNotPermitted("Only a claims handler can approve a payment.")

    row = await claim_repo.select_claim_detail(db, ctx, claim_business_id)
    if row is None:
        raise ClaimNotVisible(claim_business_id)
    claim = row.Claim

    approval = await _delegate(
        db,
        ctx,
        kind=kind,
        claim_pk=claim.id,
        claim_ref=claim.claim_id,
        target_id=target_id,
        expected_version=expected_version,
        now=now,
    )

    # Committed objects stay readable (`expire_on_commit=False`), so the
    # identity map still holds rows from before the write. Expire, or the
    # payload could carry the pre-approval status — `update_claim_fields` has
    # the same line for the same reason.
    db.expire_all()
    financials = await claim_financial_detail(
        db,
        ctx,
        claim_business_id,
        batch_weekdays=batch_weekdays,
        as_of=as_of,
    )

    # Ids and an action, never an amount or a claimant (AD-11).
    log.info(
        "payment.approved",
        claim_id=claim.claim_id,
        entity=approval.entity,
        actor_id=ctx.user_id,
    )
    return ApprovalResult(financials=financials, approval=approval)


async def _delegate(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    kind: ApprovalKind,
    claim_pk: int,
    claim_ref: str,
    target_id: int,
    expected_version: int,
    now: datetime | None,
) -> Approval:
    """Route one kind to its owning financials command.

    A three-way branch rather than a dict of callables, because the three
    commands take differently-named keyword arguments (`week_no`, `bill_id`,
    `expense_id`) — and those names are the point: the alternative is one
    generic `row_id` parameter, which is exactly how a bill id ends up
    approving a week. Keeping the branch here means the mismatch is a type
    error rather than a wrong write.
    """
    if kind == "week":
        return await approve_schedule_week(
            db,
            ctx,
            claim_pk=claim_pk,
            claim_ref=claim_ref,
            week_no=target_id,
            expected_version=expected_version,
            now=now,
        )
    if kind == "bill":
        return await approve_bill_payment(
            db,
            ctx,
            claim_pk=claim_pk,
            claim_ref=claim_ref,
            bill_id=target_id,
            expected_version=expected_version,
            now=now,
        )
    return await approve_expense_payment(
        db,
        ctx,
        claim_pk=claim_pk,
        claim_ref=claim_ref,
        expense_id=target_id,
        expected_version=expected_version,
        now=now,
    )


__all__ = [
    "APPROVAL_KINDS",
    "ApprovalKind",
    "ApprovalNotPermitted",
    "ApprovalResult",
    "approve_payment",
]
