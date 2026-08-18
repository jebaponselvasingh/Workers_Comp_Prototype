"""The supervisor/analyst dashboard's read models (FR-SUP-1/A).

Thin by AD-1 and by AD-7, `stats.py`'s shape exactly: the route takes **no
parameters at all**. There is no employer, no user, no scope and no "as" — the
only input is the session cookie, which the app-level auth dependency turns
into a caller context. That is what "no endpoint accepts caller-supplied scope"
looks like in practice: not validation that rejects a smuggled scope, but a
signature with nowhere to put one.

Read-only, and structurally so. Epic 5's dashboards gate visibility by scope
and capability by role, and no dashboard surface writes anything — so there is
no command here, no audit event and no toast on the other end. A query is not a
mutation.

**Handlers are served too, deliberately.** The route carries no role gate, and
`test_the_cards_cover_exactly_the_personas_book` parametrizes a handler and
asserts `200` — so this is a tested contract rather than an oversight. It is
not a leak: the *same* `employer_scope` predicate applies, so a handler gets
counts over the claims she is already entitled to read one by one, and the SPA
routes her to `WorkspaceShell` with no path to this surface. Adding a role
check would therefore buy nothing and cost the property the endpoint is built
on — that the only thing separating two callers' responses is the scope
predicate, not a branch.

That reasoning is about *this* answer and does not transfer. **Story 5.2's
handler-benchmarking endpoint must decide access on its own terms**, because
what it returns is a different kind of thing: a handler's own book aggregated
is her own data in another shape, while a table of peers' performance rows is
information about colleagues. Inheriting "no gate, scope is enough" there would
be reading a precedent set for a count of your own claims into a question it
was never asked.
"""

from fastapi import APIRouter, Response

from api.deps import CallerContextDep, DbDep
from api.routers.auth import UNAUTHENTICATED_RESPONSE
from api.schemas import ApiModel
from rules.parameters import thresholds_for
from services.worklist import portfolio_summary

router = APIRouter(tags=["dashboard"])


class PortfolioSummaryResponse(ApiModel):
    """The ten KPI figures, the dataset chip's counts, and the rules behind them.

    Flat rather than nested per card row, for the reason `TopBarStatsResponse`
    is flat: this is one aggregate over one scoped set, not a list, and grouping
    the fields by which row of the design they happen to render in would encode
    a layout decision in the contract. The UI owns the order.

    **`highRiskSeverityMin` and `fraudScoreMin` are on the wire deliberately.**
    Two card captions quote them ("Severity ≥ N/100", "Score ≥ N — review
    needed"), and a client holding either number would be a second copy of a
    rule it cannot see change: superseding the rule document would move the
    count and leave the caption claiming the old cut-off. They arrive from the
    derivations that did the counting, so the published number is provably the
    one the figures were produced at.

    `rulesVersion` names the document those two came from. It is what makes the
    pair auditable after the fact — "13 flagged" is only interpretable next to
    which version of the rule said so.
    """

    total_claims: int
    under_treatment: int
    settled_closed: int
    high_risk: int
    total_paid_cents: int
    total_reserve_cents: int
    fraud_flagged: int
    osha_recordable: int
    litigation: int
    surgery_required: int
    employer_count: int
    plant_count: int
    high_risk_severity_min: int
    fraud_score_min: int
    rules_version: int


@router.get(
    "/dashboard/summary",
    response_model=PortfolioSummaryResponse,
    summary="Portfolio KPI cards for the session's persona",
    responses=UNAUTHENTICATED_RESPONSE,
)
async def summary(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
) -> PortfolioSummaryResponse:
    """The ten cards, for whoever holds the session cookie — and no one else.

    No role branch of any kind. The analyst reads this same dashboard until
    Epic 7 gives them their own workspace, and the only difference between two
    personas' responses is the scope predicate the repository applied — which
    is a property this endpoint has by having nothing else in it.
    """
    # `/stats/topbar`'s reasoning: this response is specific to one persona's
    # scope, so it must never be served to another from a cache upstream.
    response.headers["Cache-Control"] = "no-store"
    # Loaded here rather than inside the aggregate, so the aggregate stays a
    # pure composition of scope and parameters — see `portfolio_summary`.
    thresholds = await thresholds_for(db)
    figures = await portfolio_summary(db, ctx, thresholds)
    return PortfolioSummaryResponse(
        total_claims=figures.total_claims,
        under_treatment=figures.under_treatment,
        settled_closed=figures.settled_closed,
        high_risk=figures.high_risk,
        total_paid_cents=figures.total_paid_cents,
        total_reserve_cents=figures.total_reserve_cents,
        fraud_flagged=figures.fraud_flagged,
        osha_recordable=figures.osha_recordable,
        litigation=figures.litigation,
        surgery_required=figures.surgery_required,
        employer_count=figures.employer_count,
        plant_count=figures.plant_count,
        high_risk_severity_min=figures.high_risk_severity_min,
        fraud_score_min=figures.fraud_score_min,
        rules_version=thresholds.version,
    )
