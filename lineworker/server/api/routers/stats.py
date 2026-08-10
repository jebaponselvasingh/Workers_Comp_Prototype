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
