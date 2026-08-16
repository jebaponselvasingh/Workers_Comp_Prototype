"""Test-profile operations. Served **only** under `ENV=e2e` (Story 3.4).

AD-15 requires the payment-batch e2e to have "a deterministic way to trigger
the batch … never wait on wall-clock cadence". This router is that hook, and
its whole design is about the two ways such a hook goes wrong.

**It does not exist outside the e2e profile.** `create_app` includes this
router only when `settings.env is Env.e2e`, so in dev and prod the path is a
404 from the router itself — not a 403 from a guard somebody could get wrong,
and not a route that "checks the environment" at request time. A test hook
whose safety depends on a runtime `if` is a test hook that ships.

**It triggers the real command, not a fixture.** `run_payment_batch` is the
function the scheduler calls; there is no second code path here. That is what
makes the e2e a test of the batch rather than of a stub — and it is why the
route body is three lines.

**The caller's own scope, not the system actor's.** The batch takes a
`CallerContext` (see `services/financials/batch.py` on why), and the obvious
alternative here would be to resolve the system actor so a test run pays the
whole portfolio. Using the requesting handler's context instead is both more
deterministic — one book, not a hundred claims — and a stronger assertion: it
proves the batch's AD-7 predicate is real, because a run under Kaya's context
that paid Sarah's claims would show up as a failing count.

The dev/prod trigger is the scheduler and nothing else. An operator-facing
"run the batch now" control is a real thing to want and is deliberately not
here: it needs a role, an audit story of its own and a rate limit, none of
which belong in a story that is building the batch.
"""

from typing import Annotated

from fastapi import APIRouter, Response
from pydantic import Field

from api.deps import CallerContextDep, DbDep
from api.routers.auth import UNAUTHENTICATED_RESPONSE
from api.schemas import ApiModel
from services.financials.batch import PaymentBatchRun, run_payment_batch

router = APIRouter(tags=["admin"])


class PaymentBatchRunResponse(ApiModel):
    """What one batch run did — counts and claim ids, never amounts.

    The command's own summary, published as-is. `rowsPaidByEntity` is keyed by
    table name so a spec can assert "three weeks and one bill" rather than a
    single total that two different outcomes could produce.
    """

    rows_paid: Annotated[int, Field(description="Total rows moved to `paid`.")]
    rows_paid_by_entity: dict[str, int]
    claims_touched: list[str]


@router.post(
    "/admin/payment-batch",
    response_model=PaymentBatchRunResponse,
    summary="Run the payment batch now (e2e profile only)",
    responses=UNAUTHENTICATED_RESPONSE,
)
async def trigger_payment_batch(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
) -> PaymentBatchRunResponse:
    """Disburse every approved payment in the caller's scope, now.

    Idempotent by construction — a second call moves nothing, because the
    first call's rows are no longer `payment_scheduled`. The e2e spec asserts
    exactly that, which is the acceptance criterion rather than a property of
    this route.
    """
    response.headers["Cache-Control"] = "no-store"
    run: PaymentBatchRun = await run_payment_batch(db, ctx)
    return PaymentBatchRunResponse(
        rows_paid=run.rows_paid,
        rows_paid_by_entity=run.rows_paid_by_entity,
        claims_touched=list(run.claims_touched),
    )
