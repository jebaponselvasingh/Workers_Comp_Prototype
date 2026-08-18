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

**And it decided differently: `/dashboard/handler-benchmarks` answers 403 to a
handler**, before any read. The two routes sitting in one file with opposite
answers is the point rather than an inconsistency — what separates them is what
the rows are *about*, not which router they live in. See
`services.worklist.benchmarks.BenchmarksNotPermitted` for the argument in full.
"""

from fastapi import APIRouter, Response, status

from api.deps import CallerContextDep, DbDep, SettingsDep
from api.errors import PROBLEM_CONTENT_TYPE, ProblemDocument, ProblemException
from api.routers.auth import UNAUTHENTICATED_RESPONSE
from api.schemas import ApiModel
from rules.parameters import handler_performance_for, thresholds_for, weights_for
from services.derivations import ComplexityBand, CycleStatus
from services.worklist import (
    BenchmarksNotPermitted,
    handler_benchmarks,
    portfolio_summary,
    require_benchmarks_access,
)

router = APIRouter(tags=["dashboard"])

#: `claims.FORBIDDEN_RESPONSE`'s shape, with this surface's reason.
#:
#: Declared here rather than imported from `api/routers/claims.py`, whose copy
#: describes "the edit capability" — accurate there and misleading in a
#: read-only router's published contract, where the refusal is about *whose*
#: numbers these are rather than about writing anything. The structure is
#: identical on purpose: one problem+json schema, one 403, answered before any
#: lookup so the body says nothing about the caller's book.
FORBIDDEN_RESPONSE: dict[int | str, dict[str, object]] = {
    403: {
        "description": (
            "The caller's role does not carry the oversight capability. Answered "
            "before any claim is read, so it says nothing about what is in the "
            "caller's scope (RFC 9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}


def _forbidden(exc: BenchmarksNotPermitted) -> ProblemException:
    """`BenchmarksNotPermitted` as this router's 403 problem document.

    One translator rather than two, because the refusal is now raised from two
    places in the same route: the gate at the top, which runs before any read at
    all, and the service's own check behind it. Two copies of a problem type
    string is how the second one drifts.
    """
    return ProblemException(
        status_code=status.HTTP_403_FORBIDDEN,
        title="Forbidden",
        detail=str(exc),
        type_="/problems/benchmarks-not-permitted",
        headers={"Cache-Control": "no-store"},
    )


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


class HandlerBenchmarkResponse(ApiModel):
    """One row of the handler performance table, in the columns' own order.

    Every field is rendered verbatim: the SPA formats and colours, and computes
    nothing (AD-1). `rank` in particular is the server's, because the ordering is
    a function of a rule document's thresholds and a scope predicate the browser
    holds neither of.

    **Five nullable fields, two different reasons.** `rtwPct` is null when the
    handler has settled nothing — ordinary, and drawn as an em dash exactly as
    the SLA strip draws a `no_data` tile. `rank`, `compositeDays`,
    `cycleSpeedPct`, `deviationPct` and `cycleStatus` are null *together*, and
    only when the caller's whole scope has no data for one of the three
    cycle-time segments; see `services.worklist.benchmarks` on why a partial sum
    is refused instead of published.

    `rank` is on that list rather than always present because it is the
    composite's ordinal: numbering rows that could not be ranked — and that are
    therefore in name order — would publish a ranking claim the response's own
    `compositeDays: null` contradicts one field away.

    `compositeDays` is published to one decimal and **ranked at full
    precision**. The Avg Days column therefore shows the figure to a finer
    precision than any of the three segments it sums, which is deliberate: the
    order is decided at that precision, and a column showing less of the number
    than the sort used would leave a reader unable to tell a real gap from a
    rounding artefact.

    The three segment averages behind `compositeDays` are deliberately **not** on
    the wire. They are already published, for the same scope, by `/stats/sla` —
    the strip this table's arithmetic comes from — and a second copy per row
    would be three more numbers a client could find disagreeing with the tiles
    above it.
    """

    rank: int | None
    handler_name: str
    case_count: int
    cycle_speed_pct: int | None
    composite_days: float | None
    rtw_pct: float | None
    complexity_score: int
    complexity_band: ComplexityBand
    pending_approvals: int
    deviation_pct: int | None
    cycle_status: CycleStatus | None


class HandlerBenchmarksResponse(ApiModel):
    """The ranked rows, the book they were ranked against, and the live bands.

    **No `total` and no `nextCursor`, deliberately.** `items` is the whole list
    and its length is already the answer — the number of handlers with claims
    inside the caller's scope, which is bounded by the size of a desk rather than
    by the size of the portfolio (six on the full seeded book, two for a scoped
    supervisor). `ClaimActionsResponse` is the precedent and gives the reason a
    second count would be worse than none: it is a number a client could find
    disagreeing with what it is rendering. A cursor would be worse still — the
    ranking is a total order over the whole set, so a page of it would have to
    re-rank on every request to stay meaningful.

    **The four thresholds ride along** for the reason the KPI cards' two do: the
    table's footnote quotes the deviation bands and the two complexity
    cut-points, so a client holding any of those numbers would be a second copy
    of a rule it cannot see change, and superseding `handler_performance` has to
    move the chips *and* the sentence under them together.

    **`rulesVersion` rides along and is currently rendered nowhere**, which is
    said plainly here rather than dressed up: the footnote quotes the four
    thresholds and not the version they came from. It is on the wire because it
    is the only thing that makes a *stored or forwarded* response
    self-describing — two captures of this table taken either side of a
    supersession are otherwise indistinguishable — and because a client that
    wanted to invalidate a cache on a rules change has nothing else to key on.
    Story 5.1's `/dashboard/summary` publishes its `rulesVersion` unrendered for
    the same reason. What would be wrong is claiming the screen states it.

    `portfolioCompositeDays` is here because every `deviationPct` is a percentage
    *of* it. Without it the column is uninterpretable and unrecoverable — the SPA
    cannot add three scoped averages of its own.
    """

    items: list[HandlerBenchmarkResponse]
    portfolio_composite_days: float | None
    leader: str | None
    laggard: str | None
    on_track_deviation_pct_max: int
    attention_deviation_pct_min: int
    complexity_high_min: int
    complexity_med_min: int
    rules_version: int


@router.get(
    "/dashboard/handler-benchmarks",
    response_model=HandlerBenchmarksResponse,
    summary="Handlers in the session's scope, ranked by composite cycle time",
    responses={**UNAUTHENTICATED_RESPONSE, **FORBIDDEN_RESPONSE},
)
async def benchmarks(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    settings: SettingsDep,
) -> HandlerBenchmarksResponse:
    """The handler performance table, for whoever holds the session cookie.

    `summary`'s signature with one parameter more, and the extra one is
    `Settings` rather than anything a caller could send: the SLA targets the
    cycle-time segments are measured against are deployment configuration, and
    `sla.targets_for` is the one place they become targets. There is still
    nowhere in this signature to put a scope (AD-7).

    No role branch appears in any *figure*. The 403 below is a gate on the whole
    surface, not a variation within it, so the only difference between a
    supervisor's response and an analyst's is the scope predicate the repository
    applied — which is the property this endpoint keeps by having nothing else
    in it.
    """
    # `/stats/topbar`'s reasoning: this response is specific to one persona's
    # scope, so it must never be served to another from a cache upstream. Set
    # before the service call so a 403 carries it too.
    response.headers["Cache-Control"] = "no-store"
    # **The gate runs before the three document loads, not after them.** The
    # service raises the same refusal from the same allowlist and is still the
    # real gate — capability lives with the module that owns the rows — but it is
    # reached three `rules_document` reads later, which made "answered before any
    # read" true of the claim read and false of everything above it. One call to
    # the service's own check, at the top, makes the sentence true of every read
    # this route performs. It is deliberately the *service's* function rather
    # than a copy of the condition: two spellings of one allowlist is how a
    # future `UserRole` gets admitted by one of them.
    try:
        require_benchmarks_access(ctx)
    except BenchmarksNotPermitted as exc:
        raise _forbidden(exc) from exc
    # All three blocks loaded here rather than inside the aggregate, so the
    # aggregate stays a composition of scope and parameters —
    # `portfolio_summary`'s rule. Three documents because the table reaches three
    # rules: its own bands and blend, the queue scorer's definition of "pending
    # approval" (which this table counts rather than restates), and the
    # `DerivationThresholds` every registered derivation is *built* from. The
    # third is loaded even though both of this story's derivations ignore it: a
    # block half injected and half fetched is the arrangement nobody can read off
    # a signature, and `Derivation.build` takes that type or nothing.
    params = await handler_performance_for(db)
    weights = await weights_for(db)
    thresholds = await thresholds_for(db)
    # Belt and braces, and cheap: the service checks the same allowlist itself,
    # so a refusal cannot escape as a 500 if this route is ever reordered or a
    # second caller appears. Unreachable today — the call above already refused —
    # which is why it delegates to the same translator rather than restating it.
    try:
        table = await handler_benchmarks(db, ctx, params, weights, thresholds, settings)
    except BenchmarksNotPermitted as exc:  # pragma: no cover - the gate above answered first
        raise _forbidden(exc) from exc

    return HandlerBenchmarksResponse(
        items=[
            HandlerBenchmarkResponse(
                rank=row.rank,
                handler_name=row.handler_name,
                case_count=row.case_count,
                cycle_speed_pct=row.cycle_speed_pct,
                composite_days=row.composite_days,
                rtw_pct=row.rtw_pct,
                complexity_score=row.complexity_score,
                complexity_band=row.complexity_band,
                pending_approvals=row.pending_approvals,
                deviation_pct=row.deviation_pct,
                cycle_status=row.cycle_status,
            )
            for row in table.items
        ],
        portfolio_composite_days=table.portfolio_composite_days,
        leader=table.leader,
        laggard=table.laggard,
        on_track_deviation_pct_max=params.on_track_deviation_pct_max,
        attention_deviation_pct_min=params.attention_deviation_pct_min,
        complexity_high_min=params.complexity_high_min,
        complexity_med_min=params.complexity_med_min,
        rules_version=params.version,
    )
