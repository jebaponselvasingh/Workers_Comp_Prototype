"""Top-bar statistics (FR-TOP-2, BR-ROLE-1).

Thin by AD-1 and by AD-7: the route takes **no parameters at all**. There is
no employer, no user, no scope, and no "as" — the only input is the session
cookie, which the app-level auth dependency turns into a caller context.
That is what "no endpoint accepts caller-supplied scope" looks like in
practice: not validation that rejects a smuggled scope, but a signature
with nowhere to put one.
"""

from fastapi import APIRouter, Response

from api.deps import CallerContextDep, DbDep, SettingsDep
from api.routers.auth import UNAUTHENTICATED_RESPONSE
from api.schemas import ApiModel
from services.worklist import topbar_stats
from services.worklist.sla import SlaDirection, SlaMetricKey, SlaStatus, sla_strip

router = APIRouter(tags=["stats"])


class TopBarStatsResponse(ApiModel):
    """`{caseload, activeTx, highRisk}` — camelCase via `ApiModel`."""

    caseload: int
    active_tx: int
    high_risk: int


@router.get(
    "/stats/topbar",
    response_model=TopBarStatsResponse,
    summary="Caseload tiles for the session's persona",
    responses=UNAUTHENTICATED_RESPONSE,
)
async def topbar(
    ctx: CallerContextDep,
    db: DbDep,
    settings: SettingsDep,
    response: Response,
) -> TopBarStatsResponse:
    # Same reasoning as `/me`: this response is specific to one persona's
    # scope, so it must never be served to another from a cache upstream.
    response.headers["Cache-Control"] = "no-store"
    stats = await topbar_stats(db, ctx, settings)
    return TopBarStatsResponse(
        caseload=stats.caseload,
        active_tx=stats.active_tx,
        high_risk=stats.high_risk,
    )


class SlaMetricResponse(ApiModel):
    """One SLA tile, decided entirely server-side (AD-1).

    `value` is `null` exactly when `status` is `no_data` — a segment with
    no qualifying claims. The SPA renders that as an em dash; what it must
    never do is substitute a number, which is the prototype behaviour this
    story exists to remove.

    `target`, `direction` and `decimals` travel with the value so the UI
    can write the comparison out (`✓ <1d`, `⚠ >5d`) and format the figure
    without holding an operator, a threshold or a precision of its own.
    """

    value: float | None
    target: float
    direction: SlaDirection
    decimals: int
    status: SlaStatus


class SlaStripResponse(ApiModel):
    """`{pick, approve, settle, rtwRate}` — camelCase via `ApiModel`."""

    pick: SlaMetricResponse
    approve: SlaMetricResponse
    settle: SlaMetricResponse
    rtw_rate: SlaMetricResponse


@router.get(
    "/stats/sla",
    response_model=SlaStripResponse,
    summary="SLA strip for the session's persona",
    responses=UNAUTHENTICATED_RESPONSE,
)
async def sla(
    ctx: CallerContextDep,
    db: DbDep,
    settings: SettingsDep,
    response: Response,
) -> SlaStripResponse:
    """The strip, for whoever holds the session cookie — and no one else.

    Parameter-free for the same reason `/stats/topbar` is, and with no role
    branch of any kind: FR-SLA-1 is the statement that a supervisor,
    analyst and handler all get *their* caseload's strip from one
    computation, so a branch here would be the defect rather than a
    feature.
    """
    response.headers["Cache-Control"] = "no-store"
    strip = await sla_strip(db, ctx, settings)
    return SlaStripResponse(
        pick=SlaMetricResponse.model_validate(strip[SlaMetricKey.pick]),
        approve=SlaMetricResponse.model_validate(strip[SlaMetricKey.approve]),
        settle=SlaMetricResponse.model_validate(strip[SlaMetricKey.settle]),
        rtw_rate=SlaMetricResponse.model_validate(strip[SlaMetricKey.rtw_rate]),
    )
