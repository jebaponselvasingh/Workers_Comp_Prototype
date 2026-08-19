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

**Story 6.1 adds a second trigger of exactly the same shape**, and the three
paragraphs above transfer verbatim: `POST /admin/embedding-refresh` exists only
under `ENV=e2e`, calls the same `refresh_stale_embeddings` the scheduler calls,
and runs under the requesting persona's context. The scheduler is off in this
profile by design, so without it a spec would have to wait fifteen minutes for
a tick — AD-15's "never wait on wall-clock cadence" again, for a different job.

The one difference worth naming is what the caller's scope buys here. For the
payment batch it makes the run smaller and more deterministic. For the refresh
it is a live assertion: a run under a scoped handler embeds that handler's book
and nothing else, so the AD-7 predicate on the two *write* paths — the ones
nobody would think to scope — is exercised by every e2e run rather than only by
a unit test.
"""

from typing import Annotated

from fastapi import APIRouter, Response
from pydantic import Field

from api.deps import CallerContextDep, DbDep, SettingsDep
from api.routers.auth import UNAUTHENTICATED_RESPONSE
from api.schemas import ApiModel
from services.financials.batch import PaymentBatchRun, run_payment_batch
from services.rag import RefreshRun, embedding_client, refresh_stale_embeddings

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


class EmbeddingRefreshResponse(ApiModel):
    """What one refresh run did — counts, never a claim id and never a vector.

    Two counts rather than one total because they are two tables and two
    different outcomes: a run that embedded fifteen chunks and no claims and
    one that embedded fifteen claims and no chunks are not the same event, and
    a spec asserting a single number could not tell them apart.

    **`rowsFailed` and `chunksFailed` complete the picture, and there are two of
    them for the same reason there are two embedded counts.** A run in which the
    model server was unreachable returns 200 with zeroes and non-zero failure
    counts, which is the honest report of "the job ran and could not do the
    work" (AD-11: a count, not the ids). The two halves fail independently — a
    deployment whose corpus was seeded long ago has nothing pending in the
    corpus half, so a tick that fails to reach the model reports
    `rowsFailed > 0` and `chunksFailed == 0`, and one on a fresh deployment
    reports both. A single `failed` total would have made those look alike.
    """

    rows_embedded: int
    chunks_embedded: int
    rows_failed: int
    chunks_failed: int


@router.post(
    "/admin/embedding-refresh",
    response_model=EmbeddingRefreshResponse,
    summary="Embed every pending row now (e2e profile only)",
    responses=UNAUTHENTICATED_RESPONSE,
)
async def trigger_embedding_refresh(
    ctx: CallerContextDep,
    db: DbDep,
    settings: SettingsDep,
    response: Response,
) -> EmbeddingRefreshResponse:
    """Run the embedding refresh over the caller's scope, now.

    `limit=None` — everything pending — rather than the configured batch size,
    so a spec does not have to know what `EMBEDDING_REFRESH_BATCH_SIZE` is or
    call this route four times to drain a hundred claims. The batch bound is a
    property of the *scheduled* path and is asserted where it lives, in
    `tests/test_rag_embeddings.py`.

    Idempotent by construction, like the batch: the pending query is "stale,
    never embedded, or embedded by a different model", so a second call under
    the same model selects nothing and returns zeroes.

    **It answers 200 even when the model server is down.** Both halves of the
    refresh degrade and count rather than raise (`services/rag/embeddings.py`
    argues why), so this route reports a failed run rather than turning it into
    a 500 — which is what lets a degradation spec assert on the counts.
    """
    response.headers["Cache-Control"] = "no-store"
    run: RefreshRun = await refresh_stale_embeddings(
        db, ctx, client=embedding_client(settings), limit=None
    )
    return EmbeddingRefreshResponse(
        rows_embedded=run.rows_embedded,
        chunks_embedded=run.chunks_embedded,
        rows_failed=run.rows_failed,
        chunks_failed=run.chunks_failed,
    )
