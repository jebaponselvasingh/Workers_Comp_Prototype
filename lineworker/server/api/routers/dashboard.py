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

**Story 5.3's `/dashboard/charts` follows `/summary` and not the benchmarks**,
and the discriminator that decides between the two precedents is written out at
that route rather than inherited by proximity: it is whether the response
reveals a *named other person's* performance. It does not — see `charts` below.

**Story 5.4's `/dashboard/priority-claims` follows them too**, and it is the
harder call of the two because that payload *does* carry a handler's name in
every row. It carries it as the **owner** of a claim the caller can already
read, never as a metric about them — see `priority_claims` below, where the
discriminator is restated rather than inherited.

It is also the first route in this file with a query parameter. Exactly one, and
it is a `cursor`: an opaque token this service minted, not a scope and not a page
size. The preamble above still holds — there is nowhere in any signature here to
put an employer, a user or an "as".

**Story 7.1's three `/dashboard/fraud*` routes are the first in this file that
answer one role and refuse the other two**, and that is a deliberate inversion of
the property Epic 5 shipped rather than a new precedent for the discriminator
above. That discriminator decides whether a *payload* needs a gate; these routes
are gated because of what they **are** — the analyst's own workspace, the first
surface in this console that a supervisor does not have. So
`test_the_analyst_reads_byte_identically_to_the_supervisor` stays true of every
endpoint it was written about, and is false here by construction. The gate is
`services.worklist.fraud.require_fraud_analytics_access`, a new allowlist over
`{analyst}` beside `benchmarks.PERMITTED_ROLES` and never a widening of it: that
list is the oversight capability two roles share, and reaching across both with
one constant would have opened colleagues' performance figures to whatever the
wider list grew to hold.

`GET /dashboard/fraud/rates` is also the first route here with a `sort`. Exactly
three, one per table, each a closed enum — and the reason a sort is safe here
where `/dashboard/claims` refuses one is the shape of the payload rather than a
change of mind: those tables carry no cursor and no `total`, so there is no offset
whose meaning a re-ordering could invalidate. `services/worklist/fraud.py` carries
the argument.
"""

from collections.abc import Mapping
from datetime import date, datetime
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Query, Response, status

from agents.schemas import read_fraud_clauses
from api.deps import CallerContextDep, DbDep, SettingsDep
from api.errors import PROBLEM_CONTENT_TYPE, ProblemDocument, ProblemException
from api.routers.auth import UNAUTHENTICATED_RESPONSE
from api.routers.stats import SlaMetricResponse, SlaStripResponse
from api.schemas import ApiModel
from data.models.enums import Disability, Gender, ReturnStatus, Stage
from rules.parameters import (
    handler_performance_for,
    reserve_bands_for,
    thresholds_for,
    trend_periods_for,
    weights_for,
    worklist_actions_for,
)
from services.derivations import AgeBand, ComplexityBand, CycleStatus, FraudBand, RiskBand
from services.financials import ReserveVerdict
from services.worklist import (
    BenchmarksNotPermitted,
    BreakdownDimension,
    DrillFilters,
    EmployerRate,
    FraudAnalyticsNotPermitted,
    FraudRateSort,
    FraudRateSorts,
    HandlerRate,
    InjuryTypeRate,
    InvalidCursor,
    PortfolioTrends,
    RateBreakdown,
    Segmentation,
    TrendAnchor,
    TrendCohort,
    TrendGrain,
    TrendMetric,
    TrendRangeInvalid,
    TrendRangeTooWide,
    drill_through_claims,
    financial_decomposition,
    fraud_panel,
    fraud_rates,
    fraud_red_flags,
    handler_benchmarks,
    portfolio_charts,
    portfolio_summary,
    portfolio_trends,
    priority_claims,
    require_benchmarks_access,
    require_fraud_analytics_access,
    reserve_adequacy,
    segmentation_values,
)
from services.worklist.charts import CategoryCount, Distribution, EmployerPaid, LabelCount
from services.worklist.decomposition import (
    DEFAULT_BREAKDOWN,
    CostDriverPair,
    FinancialBreakdown,
    MoneyTotals,
)
from services.worklist.fraud import HandlerCount
from services.worklist.segmentation import DimensionValues
from services.worklist.sla import SlaMetric, SlaMetricKey

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


#: `claims.BAD_CURSOR_RESPONSE`'s shape, with this surface's reason.
#:
#: Declared here rather than imported from `api/routers/claims.py`, for the
#: reason `FORBIDDEN_RESPONSE` above is declared here rather than imported: that
#: copy describes a cursor that "belongs to a different filter, group or rules
#: version", and the worklist has neither a filter nor a group. Same structure,
#: same problem type, one accurate description each — a shared constant would
#: have to describe both lists and would end up describing neither.
BAD_CURSOR_RESPONSE: dict[int | str, dict[str, object]] = {
    400: {
        "description": (
            "The pagination cursor is unreadable, names a position past the end "
            "of the worklist, or was cut under a rules version that has since "
            "been superseded (RFC 9457 problem document)."
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

    # Published so Story 5.5's drill-through can filter on something that cannot
    # collide. `benchmarks.py` has grouped on this id since 5.2 — two handlers
    # sharing a display name are two rows, not one merged one — and until now
    # only the name crossed the wire, which would have made a row click open
    # *both* their caseloads. `EmployerPaidResponse.employerId` carries the same
    # field for the same reason on the same dashboard, and its comment already
    # names this story. An additive publish: nothing that reads this response
    # today asserts its field set is closed.
    handler_id: int
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
                handler_id=row.handler_id,
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


class CategoryCountResponse(ApiModel):
    """One slice of an enum-keyed distribution.

    `key` is the enum's wire value — `settled`, `high`, `returned_and_fully_recovered`
    — and never a display label, per the Enums convention: the UI owns "Settled
    & Closed" and "Fully Recovered", and a server that shipped those strings
    would be deciding copy over a contract.

    A category the caller's scope does not contain is **absent from `items`**
    rather than present with a zero. See `PortfolioChartsResponse`.
    """

    key: str
    count: int


class LabelCountResponse(ApiModel):
    """One bar of a free-text distribution — an injury type or a US state.

    `label` rather than `key` because there is no enum behind it and nothing to
    look up: both columns are free text with no reference table, so the stored
    value *is* the label. It is grouped exactly as stored, with no trimming,
    case-folding or merging of near-duplicates.
    """

    label: str
    count: int


class EmployerPaidResponse(ApiModel):
    """One bar of the total-paid-by-employer chart, in integer cents.

    Money crosses the wire as integer cents on a `*Cents`-suffixed field and is
    formatted only by `web/src/lib/money.ts`, like every other amount in this
    console.

    `employerId` rides along because the label is a `short_name` and a name is
    not an identity. Nothing renders it today; Story 5.5's drill-through needs
    something that cannot collide, and adding it later would be a contract
    change on a surface that already has consumers.
    """

    employer_id: int
    label: str
    paid_cents: int


class DistributionResponse[ItemT](ApiModel):
    """A finished series: the items to draw, and everything a caption needs.

    Generic over its item type so the four scalar fields are declared once and
    each series still publishes its own concrete item shape in the contract. The
    three item types are not interchangeable — a donut slice, a labelled bar and
    an employer's spend answer different questions — but "how many of how many,
    and was it cut" is the same question for all of them.

    `items` arrive **in the order they are drawn**, decided by
    `services.worklist.charts`: enum-keyed series in their enum's declaration
    order, ranked series count descending then label ascending. A client that
    re-sorted would be re-deciding an ordering computed over a scope it does not
    hold (AD-1), which is why the order is a property of the response rather
    than a suggestion.

    **`limit`, `totalCategories` and `truncated` exist so no truncation is
    silent.** A top-8 chart drawn from a scope with twenty injury types is a
    statement about twenty things showing eight of them, and the caption has to
    be able to say so — from these fields, never from a constant the client
    holds. `truncated` is computed here rather than left to a client comparing
    the other two, for the same reason every other comparison on this dashboard
    is: it is arithmetic over a scoped set, and the browser does none.

    `total` is the total of the quantity the series distributes — a claim count
    for the five counted series and **cents** for the employer series, which
    distributes money. That is the one field on this model whose unit varies,
    so it is stated rather than left to be inferred.

    `limit` is `null` for a series that has no cap (the three enum-keyed ones and
    the employer chart). `null` rather than a large sentinel: "never cut" is a
    different fact from "cut at a thousand", and a caption quoting a sentinel
    would be quoting a number nobody chose.
    """

    items: list[ItemT]
    total: int
    total_categories: int
    truncated: bool
    limit: int | None


class PortfolioChartsResponse(ApiModel):
    """The seven analytics surfaces for one scoped book (FR-SUP-4/C, UX-DR7).

    One payload rather than seven endpoints, which is the choice the story text
    left open and this is the argument for it: the seven surfaces are folded
    from **one** scoped read in one pass, so serving them separately would mean
    seven scans of the same rows and seven chances for two of them to describe
    slightly different sets if a claim changed in between. They also appear and
    disappear together on the page — a partial dashboard of three charts is not
    a state the design has — so one request, one cache entry, one failure mode.

    **`sla` is `SlaStripResponse`, imported from `api.routers.stats` rather
    than redeclared.** That is the whole of AC 3 expressed in the type system:
    the dashboard tiles and the top-bar strip are two renderings of one server
    value, and a second model here — however faithfully copied — would be a
    second contract that could drift a field at a time. It is populated from
    `sla.strip_of` over the same scoped rows, so the two surfaces cannot
    disagree without disagreeing inside `services/worklist/sla.py` first.

    **`highRiskSeverityMin` and `medRiskSeverityMin` are on the wire
    deliberately**, for the reason `PortfolioSummaryResponse` publishes its two:
    the severity donut's legend quotes the band boundaries, and a client holding
    either number would be a second copy of a rule it cannot see change —
    superseding the document would move the slice and leave the caption claiming
    the old cut-off. They arrive from the derivation that did the banding, so
    the published numbers are provably the ones the counts were produced at.

    `rulesVersion` names the document those two came from. As on the two
    endpoints above it rides along unrendered: it is what makes a *stored or
    forwarded* response self-describing, and the only thing a client wanting to
    invalidate on a rules change could key on. What would be wrong is claiming
    the screen states it.

    **A category the scope does not contain is absent, not zero.** Jennifer
    Park's book has no `intake` stage, so her `byStage` has three items and not
    four. Emitting a zero row would put an invisible slice in a donut and a
    zero-length bar in a chart; omitting it means each series lists exactly what
    the scope contains. A consumer therefore cannot assume a fixed row count —
    the UI's label maps are lookups over whatever arrived.
    """

    by_stage: DistributionResponse[CategoryCountResponse]
    by_severity: DistributionResponse[CategoryCountResponse]
    by_recovery_status: DistributionResponse[CategoryCountResponse]
    by_injury_type: DistributionResponse[LabelCountResponse]
    by_employer: DistributionResponse[EmployerPaidResponse]
    by_state: DistributionResponse[LabelCountResponse]
    sla: SlaStripResponse
    high_risk_severity_min: int
    med_risk_severity_min: int
    rules_version: int


#: The four SLA tiles in the order `SlaStripResponse` declares them, so the
#: mapping the service returns becomes that model without a field being spelled
#: twice. `SlaMetricKey`'s own members rather than a literal tuple: a fifth
#: metric would fail here loudly instead of being dropped from the dashboard
#: while `/stats/sla` grew it.
_SLA_FIELDS: Final[tuple[SlaMetricKey, ...]] = tuple(SlaMetricKey)

# Every metric key is a field on the model, and every field is a metric key.
#
# `_sla_strip` builds the response from `**{key.value: ...}`, and Pydantic
# ignores a keyword that is not a field: add a fifth `SlaMetricKey` member and
# the new tile would vanish from this dashboard *quietly*, while `/stats/sla` —
# which names its four fields in source — kept publishing four. An import-time
# equality is the cheapest place to make that loud, and it costs nothing at
# request time. Asserted against `model_fields` rather than a written-out tuple
# so neither side can be updated alone.
assert {key.value for key in _SLA_FIELDS} == set(SlaStripResponse.model_fields), (
    "SlaMetricKey and SlaStripResponse have diverged: "
    f"{ {key.value for key in _SLA_FIELDS} ^ set(SlaStripResponse.model_fields) }"
)


def _sla_strip(strip: Mapping[SlaMetricKey, SlaMetric]) -> SlaStripResponse:
    """`sla.strip_of`'s mapping as the model `/stats/sla` already publishes.

    `model_validate` per metric, which is that route's own line — the two
    endpoints serialise one service value through one model, so a change to
    what a tile carries reaches both or neither.
    """
    return SlaStripResponse(
        **{key.value: SlaMetricResponse.model_validate(strip[key]) for key in _SLA_FIELDS}
    )


def _categories(
    series: Distribution[CategoryCount],
) -> DistributionResponse[CategoryCountResponse]:
    """An enum-keyed series, field by field.

    Field by field rather than `model_validate` on the dataclass, for the reason
    every other route in this codebase constructs explicitly: the mapping from a
    service value to a wire model is the place a renamed field should fail to
    compile, and a structural coercion is the place it silently would not.
    """
    return DistributionResponse[CategoryCountResponse](
        items=[CategoryCountResponse(key=item.key, count=item.count) for item in series.items],
        total=series.total,
        total_categories=series.total_categories,
        truncated=series.truncated,
        limit=series.limit,
    )


def _labels(
    series: Distribution[LabelCount],
) -> DistributionResponse[LabelCountResponse]:
    """A free-text series, field by field — `_categories`' reasoning."""
    return DistributionResponse[LabelCountResponse](
        items=[LabelCountResponse(label=item.label, count=item.count) for item in series.items],
        total=series.total,
        total_categories=series.total_categories,
        truncated=series.truncated,
        limit=series.limit,
    )


def _employers(
    series: Distribution[EmployerPaid],
) -> DistributionResponse[EmployerPaidResponse]:
    """The employer spend series, field by field — `_categories`' reasoning."""
    return DistributionResponse[EmployerPaidResponse](
        items=[
            EmployerPaidResponse(
                employer_id=item.employer_id, label=item.label, paid_cents=item.paid_cents
            )
            for item in series.items
        ],
        total=series.total,
        total_categories=series.total_categories,
        truncated=series.truncated,
        limit=series.limit,
    )


@router.get(
    "/dashboard/charts",
    response_model=PortfolioChartsResponse,
    summary="Portfolio distribution charts for the session's persona",
    responses=UNAUTHENTICATED_RESPONSE,
)
async def charts(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    settings: SettingsDep,
) -> PortfolioChartsResponse:
    """The seven chart surfaces, for whoever holds the session cookie.

    `benchmarks`' signature — `ctx`, `db`, `response`, `settings` — and nothing
    else. The extra parameter is `Settings` rather than anything a caller could
    send: the SLA targets the four tiles are judged against are deployment
    configuration, and `sla.targets_for` is the one place they become targets.
    There is still nowhere in this signature to put a scope (AD-7), which is
    what makes `test_query_parameters_cannot_widen_or_change_the_scope` a
    property of the shape rather than of a validator.

    ## This endpoint is ungated, and the decision is deliberate

    The two routes above disagree, so a third one cannot pick a precedent by
    proximity. `/dashboard/summary` serves any authenticated caller;
    `/dashboard/handler-benchmarks` refuses everyone outside a
    supervisor/analyst allowlist. **The discriminator is whether the aggregate
    reveals a named other person's performance.**

    The benchmark table ranks colleagues by name and speed and says whose desk
    needs a check-in. That is oversight — a capability a handler does not carry
    — and scope cannot narrow it to the caller, because a handler shares
    employers with the peers she would be reading about.

    These seven series describe the caller's **scope** by stage, severity band,
    recovery status, injury type, employer and state. "Scope", not "own book",
    and the distinction is worth the words: `employer_scope` is employer-based
    for every role, so a handler's charts aggregate every claim at her
    employers, including her colleagues' — the same rows her queue filters
    differently, not a narrower set. They name **nobody**: there is no handler
    dimension on this payload, deliberately, and every figure is a count or a
    sum over claims the caller is already entitled to read one by one. Scope
    gates visibility, role gates capability (AD-7), and there is
    no capability here to gate — so a role check would buy nothing and cost the
    property this endpoint is built on, that the only thing separating two
    callers' responses is the scope predicate.

    `test_a_handler_may_read_the_charts_for_her_employers` pins that as a
    contract
    rather than leaving it as an omission a later reader would have to guess
    about. If a dimension that names people is ever added here — a per-handler
    series for Epic 7 — this argument stops applying and the gate has to be
    reconsidered on that day, which is why it is stated as a rule and not as an
    answer.
    """
    # `/stats/topbar`'s reasoning: this response is specific to one persona's
    # scope, so it must never be served to another from a cache upstream. First
    # statement in the body, so no early return can skip it.
    response.headers["Cache-Control"] = "no-store"
    # Loaded here rather than inside the aggregate, so the aggregate stays a
    # pure composition of scope and parameters — `portfolio_summary`'s rule.
    # One document, not three: the severity donut is the only surface here that
    # reaches a rule, and it reaches the same `DerivationThresholds` the High
    # Risk card above it was counted from.
    thresholds = await thresholds_for(db)
    series = await portfolio_charts(db, ctx, thresholds, settings)

    return PortfolioChartsResponse(
        by_stage=_categories(series.by_stage),
        by_severity=_categories(series.by_severity),
        by_recovery_status=_categories(series.by_recovery_status),
        by_injury_type=_labels(series.by_injury_type),
        by_employer=_employers(series.by_employer),
        by_state=_labels(series.by_state),
        sla=_sla_strip(series.sla),
        high_risk_severity_min=series.high_risk_severity_min,
        med_risk_severity_min=series.med_risk_severity_min,
        rules_version=thresholds.version,
    )


class PriorityClaimRowResponse(ApiModel):
    """One row of the priority worklist, in the columns' own order (UX-DR7).

    The prototype's ten headers, verbatim: Claim ID · Worker · Employer · Injury
    Type · Severity · Fraud Score · Handler · Days Open · Priority Next Best
    Action · Status. Every field is rendered as it arrives — the SPA formats,
    truncates visually and picks a colour token, and computes nothing (AD-1).

    **`severityBand` is a band, not a score**, for the reason the queue card
    publishes one: the boundaries are a rule document's and a client holding them
    would be a second copy of a rule it cannot see change. The raw
    `severityScore` is deliberately absent — the column shows a chip, and sending
    the number beside the band would put every ingredient of a re-banding on one
    object.

    **`fraudScore` and `fraudFlagged` travel together, and that is not
    redundancy.** The column shows the score and tints it by whether the claim
    clears the dashboard's fraud *review* threshold. Sending only the score would
    push that comparison into the browser; sending only the flag would lose the
    figure the column exists to show. The flag is the registered `fraud_flagged`
    derivation's answer — the same one the Fraud Flags card above this table was
    counted with, and deliberately **not** the queue's SIU referral rule.

    **`stage` and `litigationFlag` are both here because the Status cell is a
    choice between them.** The prototype draws a LITIG chip on a litigated claim
    and the stage pill otherwise; *which* to draw is a presentation decision the
    UI owns, and the two facts behind it are the server's. A single pre-resolved
    "status chip" string would be the server deciding copy over a contract, which
    is what the enum convention exists to prevent.

    `nextBestAction` is a **label** and carries no command, no target and no
    document id — unlike `ActionResponse`, whose rows a handler can act on. This
    table is read-only by role capability: a supervisor directs effort by talking
    to a handler, so there is nothing here to press. Story 5.5 makes the row
    itself clickable; that is a navigation, not a mutation.

    `injuryType` and `nextBestAction` carry their **full** strings. The prototype
    cuts them at 22 and 48 characters in the render function; truncation is a
    property of the column a value is drawn in, not of the claim, so the SPA
    truncates with CSS and keeps the whole text available to a screen reader.
    """

    claim_id: str
    worker: str
    employer_short_name: str
    injury_type: str
    severity_band: RiskBand
    fraud_score: int
    fraud_flagged: bool
    handler_name: str
    days_open: int
    next_best_action: str
    stage: Stage
    litigation_flag: bool


class PriorityClaimsResponse(ApiModel):
    """One page of the worklist, the size of the book behind it, and the rules.

    `{items, nextCursor, total}` — the list convention, with `total` the
    **population before the cap** rather than the length of `items` or of the
    capped list. The caption reads "showing top 30 of 38", and both numbers are
    on the wire: `cap` is thirty and `total` is thirty-eight. Publishing the
    post-cap count instead would make the caption say "top 30 of 30", which is
    true, circular, and tells a supervisor nothing about how much of her book
    qualified. `total` is stable across every page of a walk, for
    `StageGroupResponse.total`'s reason — a count that shrank as the page moved
    would misdescribe the portfolio.

    **`cap` is on the wire because the caption quotes it and because it is a rule
    document's answer.** Superseding `worklist_actions` has to move the row count
    *and* the sentence explaining it, together, with nothing deployed — the same
    contract the KPI cards' two thresholds have.

    **The three thresholds ride along** for that reason exactly: the severity
    chips were banded at two of them and the fraud tint decided at the third, so
    a client holding any would be a second copy of a rule it cannot see change.
    They arrive from the derivations that did the deciding, so the published
    numbers are provably the ones the rows were produced at.

    **`rulesVersion` is `worklist_actions`', not the thresholds'**, which is
    worth stating because the two other dashboard payloads publish a different
    document's version under the same field name. It names the document that
    decided the *cap* — the number this response publishes and this endpoint's
    cursor is validated against. As on its siblings it rides along unrendered:
    it is what makes a stored or forwarded response self-describing, and the only
    thing a client wanting to invalidate on a rules change could key on.

    **`truncated` is the caption's other half.** "Showing top 30 of 38" and
    "8 claims" are two different sentences, and which one is true is a
    comparison of two rule-decided numbers — so it is decided on this side of
    the wire, like every other comparison on this dashboard. A browser working
    it out from `total` and `cap` would be evaluating a rule it cannot see
    change, which AD-1 forbids and `noDerivation.test.ts` catches. Without it, a
    scoped supervisor with eight qualifying claims is told "showing top 30 of 8".

    `nextCursor` is null exactly when the worklist is finished — never "null
    because this page came back short", which would strand a tail the caption has
    already told the reader is there.
    """

    items: list[PriorityClaimRowResponse]
    next_cursor: str | None
    total: int
    cap: int
    truncated: bool
    high_risk_severity_min: int
    med_risk_severity_min: int
    fraud_flag_score_min: int
    rules_version: int


@router.get(
    "/dashboard/priority-claims",
    response_model=PriorityClaimsResponse,
    summary="The session persona's highest-priority claims, ranked and paged",
    responses={**UNAUTHENTICATED_RESPONSE, **BAD_CURSOR_RESPONSE},
)
async def worklist(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    cursor: Annotated[
        str | None,
        Query(description="An opaque `nextCursor` from a previous response."),
    ] = None,
) -> PriorityClaimsResponse:
    """The top-N priority claims for whoever holds the session cookie.

    ## One query parameter, and it is not a page size

    Every other route in this file declares none, and `cursor` is the smallest
    possible departure: an opaque token this service minted, handed back
    unchanged. **There is deliberately no `limit`.** The page size is a published
    rule (`worklist_actions.supervisorWorklistPageLimit`), not a caller's
    choice — so `?limit=20` is an unknown parameter that FastAPI ignores and
    `test_query_parameters_cannot_widen_or_change_the_scope` keeps inert, exactly
    as `?employerId=3` is. The three sibling routes' contract test —
    `test_the_route_declares_no_parameters_at_all` — becomes
    `test_the_route_declares_exactly_one_parameter_and_it_is_the_cursor` here:
    an allowlist of exactly `{"cursor"}` rather than an assertion of emptiness,
    renamed rather than relaxed under its old name, because a test called "no
    parameters at all" that passes on a route with one is a sentence a reader
    would have to disbelieve.

    ## This endpoint is ungated, and it is the harder of the two calls

    Story 5.3 settled this file's discriminator: role-gate when the payload puts
    a **named other person's performance** on the wire.
    `/dashboard/handler-benchmarks` ranks colleagues by speed and says whose desk
    needs a check-in, which is oversight — a capability a handler does not carry.
    `/dashboard/summary` and `/dashboard/charts` name nobody.

    This payload names somebody in every row, so the rule has to be applied
    rather than pattern-matched. `handlerName` is the **owner** of the claim on
    that row: it is the answer to "whose desk is this on", carries no figure
    about that person, and is attached to a claim the caller could already open
    one by one — `employer_scope` is the same predicate here as in the queue, so
    a handler reading this table sees nothing her own caseload does not already
    contain. A name beside a claim is a fact about the claim; a *metric* beside a
    name is a fact about the person, and that is the line.

    The counter-argument is real and is worth stating rather than hiding: this
    table exists precisely so that effort can be directed at named people, and a
    future column carrying a per-handler figure — a count of their rows, an
    average age of their book — would move it across the line and the gate would
    have to be reconsidered on that day. That is a reason to keep the
    discriminator written down here, not a reason to gate a claim list today.
    `test_a_handler_may_read_her_own_books_priority_claims` pins it as a
    contract rather than leaving it an omission a later reader has to guess at.

    ## Three documents, loaded here

    All three blocks are loaded in the route and handed down, so the aggregate
    stays a composition of scope and parameters — `portfolio_summary`'s rule.
    Three because the table reaches three rules, and each is a different half of
    the answer: `derivation_thresholds` decides the flags and the fraud arm of
    the population, `priority_weights` decides the ordering, and
    `worklist_actions` decides the cap, the page size *and* the eleven urgencies
    the action column is ranked by. All three are also what the cursor is
    validated against, which is why they are resolved at today's date and never
    at the cursor's — a comparison against the versions effective on the
    cursor's own date could only ever succeed.
    """
    # `/stats/topbar`'s reasoning: this response is specific to one persona's
    # scope, so it must never be served to another from a cache upstream. First
    # statement in the body, so neither the refusal below nor any early return
    # can skip it.
    response.headers["Cache-Control"] = "no-store"
    thresholds = await thresholds_for(db)
    weights = await weights_for(db)
    params = await worklist_actions_for(db)
    try:
        page = await priority_claims(db, ctx, thresholds, weights, params, cursor=cursor)
    except InvalidCursor as exc:
        # 400 rather than 422, `claims.queue`'s ruling: the cursor is
        # syntactically a string and passed validation. What failed is that it
        # does not describe a position in *this* list — a fact only the service
        # knows.
        #
        # The header is re-stated because raising abandons `response`: the
        # exception handler builds a fresh `JSONResponse` and the injected one is
        # never sent. A 400 that names a caller's worklist length is as
        # persona-specific as the 200 above it, and it is the response most
        # likely to be retried.
        raise ProblemException(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Bad Request",
            detail=str(exc),
            type_="/problems/invalid-cursor",
            headers={"Cache-Control": "no-store"},
        ) from exc

    return PriorityClaimsResponse(
        items=[
            PriorityClaimRowResponse(
                claim_id=row.claim_id,
                worker=row.worker,
                employer_short_name=row.employer_short_name,
                injury_type=row.injury_type,
                severity_band=row.severity_band,
                fraud_score=row.fraud_score,
                fraud_flagged=row.fraud_flagged,
                handler_name=row.handler_name,
                days_open=row.days_open,
                next_best_action=row.next_best_action,
                stage=row.stage,
                litigation_flag=row.litigation_flag,
            )
            for row in page.items
        ],
        next_cursor=page.next_cursor,
        total=page.total,
        cap=page.cap,
        truncated=page.truncated,
        high_risk_severity_min=page.high_risk_severity_min,
        med_risk_severity_min=page.med_risk_severity_min,
        fraud_flag_score_min=page.fraud_flag_score_min,
        rules_version=page.rules_version,
    )


#: `BAD_CURSOR_RESPONSE`'s shape, with the drill-through's own reason.
#:
#: A second copy in one file, and the argument is `FORBIDDEN_RESPONSE`'s: the
#: constant above describes a cursor into a list that "has neither a filter nor
#: a group", which is true of the worklist and exactly wrong here — this list is
#: *defined* by its filter set, and a cursor minted under a different one is the
#: most likely way to reach this refusal. Same structure, same problem type, one
#: accurate description each. A shared constant would have to describe both
#: lists and would end up describing neither, which is the failure that kept
#: `claims.BAD_CURSOR_RESPONSE` out of this file in the first place.
DRILL_BAD_CURSOR_RESPONSE: dict[int | str, dict[str, object]] = {
    400: {
        "description": (
            "The pagination cursor is unreadable, was minted under a different "
            "filter set, names a position past the end of the filtered list, or "
            "was cut under a rules version that has since been superseded "
            "(RFC 9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}


class DrillClaimRowResponse(ApiModel):
    """One claim in a drill-through list — **`ClaimCardResponse`, field for field**.

    Not "the queue card's shape": its field set, in its spelling, so the SPA
    renders a drill result and a queue group with the *same* component and the
    two cannot come to disagree about what a claim looks like.
    `test_the_drill_row_is_the_queue_card_field_for_field` asserts the equality
    between the two models rather than between two fixtures, which is where a
    divergence would actually appear.

    That equality is a real constraint and it is worth naming what it costs:
    this payload publishes no employer id, no handler name and no state — three
    of the things the caller may have filtered on. A row is a claim as the
    console draws it, not a record of the query that found it, and what was
    filtered is on `appliedFilters`, once, where a chip row reads it.

    Nothing here is a hint the client finishes. `risk` is a band rather than a
    score to compare, `priorityMarker` is a decision rather than a rank to
    threshold, and `priorityScore` is published for `ClaimCardResponse`'s
    recorded reason — it makes the ordering explainable to a supervisor asking
    why a claim is third — and never as an invitation to re-sort a list ranked
    under two rule versions the browser does not hold.
    """

    claim_id: str
    days_open: int
    risk: RiskBand
    worker_name: str
    injury_type: str
    stage: Stage
    employer_short_name: str
    fraud_flag: bool
    litigation_flag: bool
    payment_due: bool
    siu_review: bool
    rtw_blocked: bool
    priority_score: float
    priority_marker: bool


class AppliedFilterResponse(ApiModel):
    """One narrowing the server applied, as a clearable chip draws it.

    `key` is the facet (`stage`, `severityBand`, `handlerId`, …), `value` is the
    wire form the caller sent, and `display` is a human label **or null**.

    **`display` is resolved for exactly two of the twelve facets.** Ten of them
    carry a value the UI already owns copy for — the three enums are snake_case
    wire values whose labels belong to the client per the Enums convention, the
    four booleans are the KPI cards' own names, and `injuryType`/`state` are
    free text where the stored value *is* the label. Shipping those strings
    would be the server deciding copy over a contract.

    The other two are ids, and an id is not a label: nothing in the browser can
    turn `handlerId=4` into a name on a cold URL load, because the dashboard
    that published the id may never have been rendered. So those two are
    resolved here, off rows the aggregate had already read, and the client's
    rule is `display ?? UI_LABEL[key][value] ?? value`.

    `display` is also null for an id the caller's **scope** does not contain — a
    smuggled `filter[employerId]`. That is deliberate rather than incidental: a
    resolved name would make the chip an oracle for the existence of an employer
    the caller cannot see, which is the leak `select_claim_detail` answers
    `None` twice over to prevent (AD-7).

    This list is what makes "the chips, the request and the result agree" a
    property rather than a hope: it is the server's reading of the URL, so an
    unknown parameter name produces no chip because it narrowed nothing.
    """

    key: str
    value: str
    display: str | None


class DrillClaimsResponse(ApiModel):
    """One page of the claims behind a dashboard figure (FR-SUP-D, AC 1).

    `{items, nextCursor, total}` — the list convention — plus the filters that
    produced it and the two rule documents that ranked it.

    **`total` is the whole filtered population and every one of it is reachable
    by paging.** There is no cap on this list, which is the difference from
    `PriorityClaimsResponse` and is the point rather than an omission: the whole
    promise of a drill-through is that its count equals the number that opened
    it, and a capped list would report ninety-two while showing thirty. `total`
    is stable across every page of a walk, for `StageGroupResponse.total`'s
    reason — a count that shrank as the page moved would misdescribe the book.

    **`appliedFilters` is the chip row, in the server's order**, and it exists so
    the chips are a rendering of the server's reading of the URL rather than a
    second parse of it in the browser. `filter[banana]=1` produces no chip
    because it narrowed nothing; `filter[stage]=settled` produces exactly one
    because it narrowed exactly once.

    **Two rule versions, and each names a document that decided something
    visible.** `rulesVersion` is `priority_weights` — the ordering and the 🔺
    marker. `thresholdsVersion` is `derivation_thresholds` — the band on every
    row, and the populations behind the severity, fraud and priority facets.
    Both are what the cursor is validated against, which is why they are
    resolved at today's date and never at the cursor's: a comparison against the
    versions effective on the cursor's own date could only ever succeed. As on
    the four sibling payloads they ride along unrendered; what would be wrong is
    claiming the screen states them.

    **No thresholds a figure was produced at**, unlike its four siblings, and the
    absence is deliberate: nothing here quotes one. The severity band arrives
    banded per row, the fraud flag arrives decided, and a caption saying
    "Severity ≥ N" belongs to the card that was clicked rather than to the list it
    opened. Publishing them anyway would be putting every ingredient of a
    re-banding on an object whose rows are already banded.

    **The three age edges are the one exception, and they are on this payload for
    a chip rather than for a figure (Story 7.3).** `AgeBand`'s members are ordinal
    words carrying no numbers at all — deliberately, so that moving an edge in
    `derivation_thresholds` cannot leave a member name asserting the old one — so
    the *range of years* a chip shows can only ever be composed in the browser
    from the document's own cut-offs. That chip is drawn from `appliedFilters`
    *here*, on a list an analyst reaches by clicking a chart with
    `filter[ageGroup]` active, and without the edges it read "Age group: older"
    one click after reading that range on the workspace's own bar — one value
    under two names, which is precisely what Story 7.3's "one vocabulary" claim
    denies.

    The alternative was a resolved `display` string decided server-side, and it
    is refused for `DimensionValueResponse`'s recorded reason: a label composed
    in Python would be a *second* copy of a value derived from a rule document,
    free to disagree with the one the workspace's own bar composes. Three
    integers from the document this route already loads, composed once in
    `segmentation/ageBands.ts`, is one rule with one renderer. They are
    unconditional rather than sent only when `filter[ageGroup]` is set, because a
    field that appears and disappears is a shape a client has to branch on for a
    caption.

    `nextCursor` is null exactly when the list is finished — never "null because
    this page came back short", which would strand a tail `total` has already
    told the reader is there.
    """

    items: list[DrillClaimRowResponse]
    next_cursor: str | None
    total: int
    applied_filters: list[AppliedFilterResponse]
    rules_version: int
    thresholds_version: int
    age_younger_min: int
    age_older_min: int
    age_oldest_min: int


@router.get(
    "/dashboard/claims",
    response_model=DrillClaimsResponse,
    summary="The claims behind a dashboard figure, filtered, ranked and paged",
    responses={**UNAUTHENTICATED_RESPONSE, **FORBIDDEN_RESPONSE, **DRILL_BAD_CURSOR_RESPONSE},
)
async def drill_claims(  # noqa: PLR0913 - one parameter per published facet; see below
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    stage: Annotated[
        Stage | None,
        Query(alias="filter[stage]", description="The claim's lifecycle stage."),
    ] = None,
    severity_band: Annotated[
        RiskBand | None,
        Query(
            alias="filter[severityBand]",
            description="The registered `risk` band the High Risk card counts with.",
        ),
    ] = None,
    fraud_flagged: Annotated[
        bool | None,
        Query(
            alias="filter[fraudFlagged]",
            description=(
                "The Fraud Flags card's *review* rule — deliberately not the "
                "queue's higher SIU referral cut."
            ),
        ),
    ] = None,
    litigation: Annotated[
        bool | None,
        Query(alias="filter[litigation]", description="The Litigation card's flag."),
    ] = None,
    surgery: Annotated[
        bool | None,
        Query(alias="filter[surgery]", description="The Surgery Required card's flag."),
    ] = None,
    osha_recordable: Annotated[
        bool | None,
        Query(alias="filter[oshaRecordable]", description="The OSHA Recordable card's flag."),
    ] = None,
    recovery_status: Annotated[
        ReturnStatus | None,
        Query(
            alias="filter[recoveryStatus]",
            description="The recovery-status chart's fold key.",
        ),
    ] = None,
    injury_type: Annotated[
        str | None,
        Query(
            alias="filter[injuryType]",
            description=(
                "The injury-type chart's bar label, matched as the exact stored "
                "string — no trimming, case-folding or merging."
            ),
        ),
    ] = None,
    state: Annotated[
        str | None,
        Query(
            alias="filter[state]",
            description="The claims-by-state chart's bar label, matched exactly.",
        ),
    ] = None,
    employer_id: Annotated[
        int | None,
        Query(
            alias="filter[employerId]",
            description=(
                "An employer's id, as published by the employer spend chart. "
                "Intersects the caller's scope and can never widen it."
            ),
        ),
    ] = None,
    handler_id: Annotated[
        int | None,
        Query(
            alias="filter[handlerId]",
            description=(
                "A handler's id, as published by the handler benchmark table. "
                "Intersects the caller's scope and can never widen it."
            ),
        ),
    ] = None,
    priority: Annotated[
        bool | None,
        Query(
            alias="filter[priority]",
            description="The priority worklist's population, before its cap.",
        ),
    ] = None,
    fraud_band: Annotated[
        FraudBand | None,
        Query(
            alias="filter[fraudBand]",
            description=(
                "The registered `fraud_band` banding of `fraud_score` **alone** — "
                "no `fraud_flag` conjunct, so this is neither the review rule "
                "above nor the referral rule below."
            ),
        ),
    ] = None,
    siu_review: Annotated[
        bool | None,
        Query(
            alias="filter[siuReview]",
            description=(
                "The queue's SIU *referral* rule — deliberately narrower than "
                "`filter[fraudFlagged]`'s review cut."
            ),
        ),
    ] = None,
    fnol_from: Annotated[
        date | None,
        Query(
            alias="filter[fnolFrom]",
            description=(
                "Claims whose FNOL date is on or after this day. **Inclusive**, "
                "which is the reading a trend bucket's own boundary publishes."
            ),
        ),
    ] = None,
    fnol_to: Annotated[
        date | None,
        Query(
            alias="filter[fnolTo]",
            description="Claims whose FNOL date is on or before this day. Inclusive.",
        ),
    ] = None,
    doi_from: Annotated[
        date | None,
        Query(
            alias="filter[doiFrom]",
            description=(
                "Claims whose date of injury is on or after this day. Inclusive, "
                "and a different column from `filter[fnolFrom]` on purpose."
            ),
        ),
    ] = None,
    doi_to: Annotated[
        date | None,
        Query(
            alias="filter[doiTo]",
            description="Claims whose date of injury is on or before this day. Inclusive.",
        ),
    ] = None,
    disability: Annotated[
        Disability | None,
        Query(
            alias="filter[disability]",
            description="The claim's disability type, as the trend cohort splits on it.",
        ),
    ] = None,
    sector: Annotated[
        str | None,
        Query(
            alias="filter[sector]",
            description=(
                "The employer's sector, matched as the exact stored string — no "
                "trimming, case-folding or merging. An employer attribute, not "
                "an industry rollup."
            ),
        ),
    ] = None,
    region: Annotated[
        str | None,
        Query(
            alias="filter[region]",
            description=(
                "The operating region the claim's plant sits in, matched as the "
                "exact stored string — a different column from `filter[state]`, "
                "which is the jurisdiction a benefit is calculated under."
            ),
        ),
    ] = None,
    icd10: Annotated[
        str | None,
        Query(
            alias="filter[icd10]",
            description=(
                "The claim's ICD-10 code, matched as the exact stored string. The "
                "column is `claim.icd`; the facet names the coding system."
            ),
        ),
    ] = None,
    age_group: Annotated[
        AgeBand | None,
        Query(
            alias="filter[ageGroup]",
            description=(
                "The registered `age_band` of the injured worker's age. Ordinal "
                "words rather than ranges: the edges live in `derivation_thresholds` "
                "and are published on `/dashboard/segmentation/values`."
            ),
        ),
    ] = None,
    gender: Annotated[
        Gender | None,
        Query(alias="filter[gender]", description="The injured worker's gender."),
    ] = None,
    reserve_verdict: Annotated[
        ReserveVerdict | None,
        Query(
            alias="filter[reserveVerdict]",
            description=(
                "Epic 3's reserve adequacy verdict for the claim, as the analyst "
                "workspace's adequacy distribution counted it. **This is the one "
                "facet that costs extra reads**: the verdict is not a column, so "
                "setting it loads each claim's payment schedule and bills and the "
                "`reserve_bands` document. Every other facet leaves the route at "
                "one scoped read."
            ),
        ),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(description="An opaque `nextCursor` from a previous response."),
    ] = None,
) -> DrillClaimsResponse:
    """The claims behind a KPI card, a chart segment, a handler row or a worklist.

    ## Twenty-six parameters, and not one of them is a scope

    Twenty-five facets and a cursor. Every facet is a *narrowing* applied after
    `employer_scope(ctx)` has already decided which rows exist, so
    `filter[employerId]` and `filter[handlerId]` intersect the caller's book and
    can never widen it: a scoped supervisor naming an employer outside hers gets
    an empty page, never a 403 and never a row. There is still nowhere in this
    signature to put a scope (AD-7), which is what keeps
    `test_query_parameters_cannot_widen_or_change_the_scope` a property of the
    shape rather than of a validator — and `?scopeAll=true` remains an unknown
    parameter FastAPI ignores, exactly as it is on the four routes above.

    **There is deliberately no `limit` and no `sort`.** The page size is
    `priority_weights.pageLimit`, a published rule; the order is
    `priority.order_key`, the queue's own. A `sort` parameter would make every
    outstanding cursor ambiguous, because an offset into a ranking means nothing
    against a different one.

    ## Why `filter[…]` here and a scalar `filter` on `/claims/queue`

    The two spellings mean different things, and the next reader's first
    question will be why they differ.

    `/claims/queue` takes `filter=high_risk`: one of **eight operational
    modes**. `high_risk` and `payment_due` are not independent dimensions a
    handler intersects — they are alternative ways to look at one queue, and
    choosing two of them at once is not a question that surface asks.

    This route intersects **independent facets**: a supervisor drills into High
    Risk, then narrows to one employer, then to litigated claims, and each is a
    separate dimension of the same set. That is what the architecture's list
    convention spells with brackets, and it is why the twenty-five arrive as
    twenty-five parameters rather than as one enum.

    ## Story 7.4 adds one facet and changes none

    `filter[reserveVerdict]`, **appended** for the reason 7.1's two, 7.2's six
    and 7.3's four were: `appliedFilters` is the chip row's order, it is read off
    `DrillFilters`' field order, and inserting a facet anywhere but the end
    silently renumbers the chips on every drill-through URL anybody has shared.

    It is the analyst workspace's Financial section's own click target — a
    segment of the reserve-adequacy distribution — and it is matched through the
    registered computation that segment was *counted* with
    (`services/financials/reserve.py`), which is this route's founding rule
    applied to a value that is not a derivation and not a column. It is also the
    only facet here whose value cannot be reached from the claim row alone, which
    is why it is the only one that changes what this route costs; see the
    parameter's own description and `drill_through_claims`.

    The route stays **ungated** with the addition: a reserve verdict is a
    judgement about a claim the session can already open one at a time, and the
    payload still names nobody.

    ## Story 7.3 adds four facets and changes none

    `filter[region]`, `filter[icd10]`, `filter[ageGroup]` and `filter[gender]`,
    **appended** for the reason 7.1's two and 7.2's six were: `appliedFilters` is
    the chip row's order and it is read off `DrillFilters`' field order, so
    inserting `filter[gender]` beside `filter[state]` — where it reads more
    naturally — would silently re-order the chips on every drill-through URL
    anybody has already shared.

    They are not this surface's own click targets. They are the tail of the
    *segmentation* vocabulary the analyst workspace narrows by, and the whole
    point of them arriving here is that the workspace and this list say the same
    words: an analyst filtering by sector and gender clicks a chart segment and
    lands on this route with those two parameters intact plus the slice's own, as
    three independently clearable chips. `services/worklist/segmentation.py`
    asserts the subset relationship at import so the two vocabularies cannot come
    apart.

    The route stays **ungated** with the four additions, and that is worth
    checking rather than assuming: none of them publishes a figure about a named
    person, and the `filter[handlerId]` gate below is unchanged. `filter[gender]`
    is the one to look twice at — it narrows on an attribute of the *injured
    worker* — and it is not a figure about a colleague: the rows it returns are
    claims this session can already open one at a time, and the payload names
    nobody. It is a segmentation dimension the story's AC lists by name, over a
    column the caller's own case files already show.

    ## Story 7.2 adds six facets and changes none

    Four date bounds and two column equalities — `filter[fnolFrom]`,
    `filter[fnolTo]`, `filter[doiFrom]`, `filter[doiTo]`, `filter[disability]`
    and `filter[sector]` — which together are what makes a point on a trend chart
    a click target. Both date bounds of a pair are **inclusive**, which is the
    reading `/dashboard/trends` publishes its bucket boundaries under, so a
    bucket's drill returns exactly the claims that point was folded from rather
    than a list that is one day off at each end.

    They are **appended**, never inserted, for the reason Story 7.1's two were:
    `appliedFilters` is the chip row's order and it is read off `DrillFilters`'
    field order, so inserting a date facet beside `stage` would silently
    re-order the chips on every drill-through URL anybody has already shared.

    The two anchors are two separate pairs over two separate columns because a
    trend point knows which anchor produced it: a DOI point narrowed on the FNOL
    column would open a plausible list of the wrong claims, which is this route's
    founding failure mode with a date in it.

    ## Story 7.1 adds two facets and changes none

    `filter[fraudBand]` and `filter[siuReview]` are the analyst workspace's own
    click targets — a band segment and a pipeline segment — and each is matched
    through the registered derivation the segment was *counted* with. Every
    existing facet answers exactly what it answered before, which is the contract
    Story 5.5 owns and this story does not: `fraudFlagged` is still the review
    rule and is deliberately not either of the new two, and a URL written before
    this story still produces the same list and the same chips in the same order.

    The route stays **ungated** with the two additions, and that is worth
    checking rather than assuming: neither publishes a figure about a named
    person, and the `handlerId` gate below is unchanged. The three
    `/dashboard/fraud*` routes that *do* gate are gated because they are the
    analyst's workspace, not because a fraud facet is sensitive.

    ## This endpoint is ungated, and the argument is re-applied rather than
    inherited

    This file's discriminator, settled by Story 5.3 and re-applied by 5.4:
    role-gate when the payload puts a **named other person's performance** on
    the wire. `/dashboard/handler-benchmarks` ranks colleagues by speed and says
    whose desk needs a check-in, which is oversight — a capability a handler
    does not carry.

    This payload is a list of claims the caller can already open one at a time:
    `employer_scope` is the same predicate here as in the queue and in
    `GET /claims/{id}`, so every row is a claim the session could have read
    singly. The rows name **nobody** — the row is the queue card's field set,
    which carries no handler at all. So the route is ungated by default.

    **One facet is gated, and the reason is that it crosses the line the
    paragraph above draws.** `filter[handlerId]` was first written as ungated on
    the argument that narrowing a list to one person's claims attributes no
    *metric* to that person. That argument was wrong, and the way it was wrong
    is worth keeping: the response publishes `total`, and `total` under a sole
    `handlerId` facet **is** a count about that person — the same figure
    `/dashboard/handler-benchmarks` publishes as `caseCount` and 403s a handler
    for reading. `AppliedFilter.display` supplies the name beside it, and
    handler ids are small integers, so a handler could walk the range and
    rebuild the gated column one colleague at a time. A figure beside a name is
    a fact about the person; that is the line, and `total` was already over it.

    So a caller asking about **somebody else's** book must carry the oversight
    capability, checked with `handler_benchmarks`' own gate rather than a second
    copy of the role list. Asking about **your own** book is not oversight and
    stays open: a handler filtering her own queue learns nothing she cannot
    already count. The check reads nothing and runs before the two rule
    documents, so the refusal precedes every read on this route — the property
    `require_benchmarks_access`' docstring exists to make true.

    ## Two documents, loaded here

    Both blocks are loaded in the route and handed down, so the aggregate stays
    a composition of scope and parameters — `portfolio_summary`'s rule.
    `derivation_thresholds` decides the band on every row and the populations
    behind five of the twenty facets; `priority_weights` decides the ordering,
    the marker and the page size. Both are what the cursor is validated against,
    which is why they are resolved at today's date and never at the cursor's.
    """
    # `/stats/topbar`'s reasoning: this response is specific to one persona's
    # scope, so it must never be served to another from a cache upstream. First
    # statement in the body, so neither the refusal below nor any early return
    # can skip it.
    response.headers["Cache-Control"] = "no-store"
    # Before the two rule-document reads, so "the refusal happens before any
    # read" is true of this route the way it is true of `/handler-benchmarks`.
    # `is not None` rather than a truthiness test: handler ids are integers and
    # a falsy one would silently skip the gate.
    if handler_id is not None and handler_id != ctx.user_id:
        try:
            require_benchmarks_access(ctx)
        except BenchmarksNotPermitted as exc:
            raise _forbidden(exc) from exc
    thresholds = await thresholds_for(db)
    weights = await weights_for(db)
    # Built field by field, like every response model in this file and for the
    # same reason: the mapping from the route's twenty-five parameters to the
    # service's twenty-five fields is the place a renamed facet should fail to
    # compile, and a `**locals()`-shaped shortcut is the place it silently
    # would not.
    filters = DrillFilters(
        stage=stage,
        severity_band=severity_band,
        fraud_flagged=fraud_flagged,
        litigation=litigation,
        surgery=surgery,
        osha_recordable=osha_recordable,
        recovery_status=recovery_status,
        injury_type=injury_type,
        state=state,
        employer_id=employer_id,
        handler_id=handler_id,
        priority=priority,
        fraud_band=fraud_band,
        siu_review=siu_review,
        fnol_from=fnol_from,
        fnol_to=fnol_to,
        doi_from=doi_from,
        doi_to=doi_to,
        disability=disability,
        sector=sector,
        region=region,
        icd10=icd10,
        age_group=age_group,
        gender=gender,
        reserve_verdict=reserve_verdict,
    )
    try:
        page = await drill_through_claims(db, ctx, thresholds, weights, filters, cursor=cursor)
    except InvalidCursor as exc:
        # 400 rather than 422, `claims.queue`'s ruling: the cursor is
        # syntactically a string and passed validation. What failed is that it
        # does not describe a position in *this* list — a fact only the service
        # knows. 400 stays reserved for this one refusal here: an unknown enum
        # *value* is the global handler's 422 `/problems/validation-error`, and
        # an unknown parameter *name* is ignored, as it is everywhere else.
        #
        # The header is re-stated because raising abandons `response`: the
        # exception handler builds a fresh `JSONResponse` and the injected one is
        # never sent. A 400 that names a caller's list length is as
        # persona-specific as the 200 above it, and it is the response most
        # likely to be retried.
        raise ProblemException(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Bad Request",
            detail=str(exc),
            type_="/problems/invalid-cursor",
            headers={"Cache-Control": "no-store"},
        ) from exc

    return DrillClaimsResponse(
        items=[
            DrillClaimRowResponse(
                claim_id=row.claim_id,
                days_open=row.days_open,
                risk=row.risk,
                worker_name=row.worker_name,
                injury_type=row.injury_type,
                stage=row.stage,
                employer_short_name=row.employer_short_name,
                fraud_flag=row.fraud_flag,
                litigation_flag=row.litigation_flag,
                payment_due=row.payment_due,
                siu_review=row.siu_review,
                rtw_blocked=row.rtw_blocked,
                priority_score=row.priority_score,
                priority_marker=row.priority_marker,
            )
            for row in page.items
        ],
        next_cursor=page.next_cursor,
        total=page.total,
        applied_filters=[
            AppliedFilterResponse(key=item.key, value=item.value, display=item.display)
            for item in page.applied_filters
        ],
        rules_version=page.rules_version,
        thresholds_version=page.thresholds_version,
        # Read off the block this route already loaded, and off nothing else —
        # `segmentation_values` makes the same read for the same reason and
        # records it: these three are not a rule any figure here was produced at,
        # they are the material an `ageGroup` chip's range label is composed
        # from. See `DrillClaimsResponse`.
        age_younger_min=thresholds.age_younger_min,
        age_older_min=thresholds.age_older_min,
        age_oldest_min=thresholds.age_oldest_min,
    )


# --- Story 7.1: the analyst workspace's Fraud section --------------------


#: `FORBIDDEN_RESPONSE`'s shape, with this section's reason.
#:
#: A third copy of one structure in one file, and the argument is the one
#: `DRILL_BAD_CURSOR_RESPONSE` makes: the constant above describes "the oversight
#: capability", which is accurate for the benchmark table and exactly wrong here.
#: These routes refuse a *supervisor*, who carries oversight in full — what they
#: gate is the analyst workspace, a surface that persona does not have. A shared
#: constant would have to describe both refusals and would end up describing
#: neither, and the description is what a client reading the OpenAPI document has
#: to reason about. Deliberately not imported from anywhere.
FRAUD_ANALYTICS_FORBIDDEN_RESPONSE: dict[int | str, dict[str, object]] = {
    403: {
        "description": (
            "The caller's role does not carry the analyst-workspace capability. "
            "Answered before any claim is read and before any rule document is "
            "loaded, so it says nothing about what is in the caller's scope "
            "(RFC 9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}


def _fraud_forbidden(exc: FraudAnalyticsNotPermitted) -> ProblemException:
    """`FraudAnalyticsNotPermitted` as this section's 403 problem document.

    `_forbidden`'s twin with its own problem *type*, and the separate type is the
    point rather than tidiness: `/problems/benchmarks-not-permitted` tells a
    client "you may not read colleagues' figures", which is untrue of a supervisor
    refused here. One translator per refusal, each raised from both the gate at
    the top of a route and the service's own check behind it, because two copies
    of a problem type string is how the second one drifts.

    `Cache-Control: no-store` is restated in the exception because raising
    abandons the injected `Response`: the handler builds a fresh `JSONResponse`
    and the header set in the route body is never sent.
    """
    return ProblemException(
        status_code=status.HTTP_403_FORBIDDEN,
        title="Forbidden",
        detail=str(exc),
        type_="/problems/fraud-analytics-not-permitted",
        headers={"Cache-Control": "no-store"},
    )


# --- Story 7.3: the analyst workspace's segmentation ---------------------
#
# **Declared here, above the sections that take it, and that is the only reason
# it is not at the bottom of the file with the rest of Story 7.3.** Every other
# story's block sits after the ones before it, so a reader scrolls through the
# router in the order the console was built. A FastAPI dependency is evaluated
# when the decorated function is *defined*, so this one has to precede
# `/dashboard/fraud`, which is Story 7.1's. The values endpoint it also serves is
# at the end of the file, where 7.3's own section belongs.


async def _segmentation(  # noqa: PLR0913 - one parameter per published dimension; see below
    severity_band: Annotated[
        RiskBand | None,
        Query(
            alias="filter[severityBand]",
            description="The registered `risk` band — the same one the High Risk card counts.",
        ),
    ] = None,
    injury_type: Annotated[
        str | None,
        Query(
            alias="filter[injuryType]",
            description=(
                "The claim's injury type, matched as the exact stored string — no "
                "trimming, case-folding or merging."
            ),
        ),
    ] = None,
    state: Annotated[
        str | None,
        Query(
            alias="filter[state]",
            description="The claim's jurisdiction, matched exactly. Not the employer's region.",
        ),
    ] = None,
    employer_id: Annotated[
        int | None,
        Query(
            alias="filter[employerId]",
            description=(
                "An employer's id. Intersects the caller's scope and can never widen it — "
                "naming one outside the book empties every aggregate."
            ),
        ),
    ] = None,
    disability: Annotated[
        Disability | None,
        Query(alias="filter[disability]", description="The claim's disability type."),
    ] = None,
    sector: Annotated[
        str | None,
        Query(
            alias="filter[sector]",
            description=(
                "The employer's sector, matched as the exact stored string. An employer "
                "attribute, not an industry rollup."
            ),
        ),
    ] = None,
    region: Annotated[
        str | None,
        Query(
            alias="filter[region]",
            description=(
                "The operating region the claim's plant sits in, matched exactly — a "
                "different column from `filter[state]`, which is the jurisdiction."
            ),
        ),
    ] = None,
    icd10: Annotated[
        str | None,
        Query(
            alias="filter[icd10]",
            description="The claim's ICD-10 code, matched as the exact stored string.",
        ),
    ] = None,
    age_group: Annotated[
        AgeBand | None,
        Query(
            alias="filter[ageGroup]",
            description=(
                "The registered `age_band` of the injured worker's age. Ordinal words "
                "rather than ranges: the edges are published on "
                "`/dashboard/segmentation/values` and the label is composed from them."
            ),
        ),
    ] = None,
    gender: Annotated[
        Gender | None,
        Query(alias="filter[gender]", description="The injured worker's gender."),
    ] = None,
) -> Segmentation:
    """The ten `filter[…]` dimensions, declared **once** for four routes.

    A FastAPI dependency rather than ten parameters written out on each of
    `/dashboard/fraud`, `/dashboard/fraud/rates`, `/dashboard/fraud/red-flags`,
    `/dashboard/trends` and `/dashboard/segmentation/values`: ten parameters
    spelled five times is five places to drift, and the drift would be silent in
    the direction that matters — a section that spelled one alias differently
    would ignore the workspace's filter and quietly describe the whole book under
    a chip row saying otherwise.

    **The aliases are `DrillFilters`' own**, character for character, and that is
    the story's central claim rather than a convenience:
    `services/worklist/segmentation.py` asserts its keys and wire spellings are a
    subset of the drill list's at import, so the same query string narrows a
    workspace section and the list a click into it opens. `filter[employerId]`
    here and `filter[employerId]` on `/dashboard/claims` are one parameter with
    two readers.

    **Not one of the ten is a scope.** `filter[employerId]` is a *narrowing*
    applied after `employer_scope(ctx)` has already decided which rows exist, so
    a caller naming an employer outside their book gets empty aggregates, never a
    row and never a 403 — `drill_claims`' ruling, on four more routes. There is
    still nowhere in any of these signatures to put a user or an "as", and
    `?scopeAll=true` remains an unknown parameter FastAPI ignores.

    The **type of each parameter is the refusal**: `filter[gender]=nonsense` is a
    422 from FastAPI's own coercion before any service runs, which is why
    `Segmentation` holds no vocabulary check and must not grow one.

    Built field by field, like every response model in this file and for the same
    reason: the mapping from these ten parameters to the service's ten fields is
    the place a renamed dimension should fail to compile, and a
    `**locals()`-shaped shortcut is the place it silently would not.

    **`async def`, although nothing here awaits, and that is the point.** Starlette
    runs a *sync* dependency in a threadpool, so a plain `def` would put a thread
    hop in front of all five analyst routes on every request — to build a frozen
    dataclass out of ten arguments FastAPI has already coerced. Declaring it
    `async` keeps it on the event loop, where the work it does actually belongs.
    The rule this follows is the one the codebase already applies to its reads: a
    dependency that touches nothing blocking is a coroutine, and a dependency that
    blocks is what the threadpool is for.
    """
    return Segmentation(
        severity_band=severity_band,
        injury_type=injury_type,
        state=state,
        employer_id=employer_id,
        disability=disability,
        sector=sector,
        region=region,
        icd10=icd10,
        age_group=age_group,
        gender=gender,
    )


#: The ten dimensions as one injected argument.
#:
#: Named rather than spelled `Annotated[Segmentation, Depends(_segmentation)]` at
#: five call sites, `CallerContextDep`'s idiom in `api/deps.py`: a dependency
#: written out per route is a dependency one route can be given a different
#: version of.
SegmentationDep = Annotated[Segmentation, Depends(_segmentation)]


class HandlerCountResponse(ApiModel):
    """One handler's share of the SIU pipeline — the id, the name, the count.

    `handlerId` is the identity and `handlerName` the label,
    `HandlerBenchmarkResponse`'s split and its reason: the segment opens
    `filter[handlerId]`, and a drill keyed on a display name would merge two desks
    that share one.

    A handler with no referred claim is **absent** rather than a zero row: this is
    a list of the desks carrying SIU work, and the denominator a reader wants —
    how many claims that handler holds — is the rate breakdown's `claims` column
    one route over, published there precisely so it is on screen rather than
    inferred from an empty segment.
    """

    handler_id: int
    handler_name: str
    count: int


def _handler_counts(
    series: Distribution[HandlerCount],
) -> DistributionResponse[HandlerCountResponse]:
    """The per-handler series, field by field — `_categories`' reasoning."""
    return DistributionResponse[HandlerCountResponse](
        items=[
            HandlerCountResponse(
                handler_id=item.handler_id, handler_name=item.handler_name, count=item.count
            )
            for item in series.items
        ],
        total=series.total,
        total_categories=series.total_categories,
        truncated=series.truncated,
        limit=series.limit,
    )


class FraudPanelResponse(ApiModel):
    """The fraud-score distribution, the SIU pipeline, and the rules behind them.

    **`byBand` is zero-filled and its two siblings are not**, which is the one
    thing about this payload a consumer has to know. Every other distribution on
    this dashboard omits a category the scope does not contain
    (`PortfolioChartsResponse` says so at length), because those vocabularies are
    columns'. `FraudBand` is a *rule's* answer over a score every claim carries,
    so all three members are always present — and "no claim in this book scored
    into the high band" is the single most valuable thing this chart can say. A
    two-segment distribution a reader could not distinguish from a build that
    forgot to draw the third would read as reassurance.

    `siuByStage` and `siuByHandler` follow the omission rule: a stage with no
    referred claim is a stage the pipeline does not reach.

    **The four thresholds ride along** for the reason the KPI cards' two do: the
    band legend quotes its edges and the pipeline caption names the referral
    cut-off, so a client holding either would be a second copy of a rule it cannot
    see change. All four arrive from the derivations that did the deciding, so the
    published numbers are provably the ones the segments were produced at — and
    all four are separate parameters even where two of them read the same integer
    today, which is `derivation_thresholds.v6`'s whole argument.

    **`flaggedClaims` is on this payload and is not a segment of `byBand`.** It is
    the *review* population — `fraud_flagged`, the rule `/dashboard/summary`
    counts as `fraudFlagged` and `/dashboard/claims?filter[fraudFlagged]=true`
    reports as `total` — and the section's flagged-claims panel is that existing
    list rather than a second one. Publishing the count here is what lets the
    entry point state the size of the list before it is opened.

    `rulesVersion` names the document all four thresholds came from. As on every
    sibling payload it rides along unrendered: it is what makes a stored or
    forwarded response self-describing, and the only thing a client wanting to
    invalidate on a rules change could key on.
    """

    by_band: DistributionResponse[CategoryCountResponse]
    siu_by_stage: DistributionResponse[CategoryCountResponse]
    siu_by_handler: DistributionResponse[HandlerCountResponse]
    claims_in_scope: int
    flagged_claims: int
    siu_claims: int
    fraud_band_high_min: int
    fraud_band_med_min: int
    fraud_flag_score_min: int
    siu_fraud_score_min: int
    rules_version: int


@router.get(
    "/dashboard/fraud",
    response_model=FraudPanelResponse,
    summary="Fraud-score distribution and SIU pipeline for the session's analyst",
    responses={**UNAUTHENTICATED_RESPONSE, **FRAUD_ANALYTICS_FORBIDDEN_RESPONSE},
)
async def fraud(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    segmentation: SegmentationDep,
) -> FraudPanelResponse:
    """The Fraud section's headline surfaces, for whoever holds the session cookie.

    **Ten parameters since Story 7.3, and not one of them is a scope.** They
    arrive as one injected `Segmentation` — see `_segmentation`, which declares
    them once for this route and its four siblings — and every one is a
    *narrowing* applied inside the service over rows `employer_scope(ctx)` had
    already decided existed. The role gate below decides whether this endpoint
    answers; the scope predicate inside the repository decides what it answers;
    the filter decides how much of that it describes. No code on this path
    branches on role to widen anything.

    An impossible combination is a **200 with empty aggregates**, never an error:
    the band distribution is zero-filled (a rule's vocabulary is always complete),
    the pipeline is empty, and the three counters are zero. `DrillFilters`'
    ruling, on a surface where a nine-dimension AND makes emptiness ordinary.

    ## This endpoint is gated, and the argument is not the file's usual one

    The discriminator Stories 5.3, 5.4 and 5.5 settled asks whether a payload puts
    a **named other person's performance** on the wire. It would answer "yes" here
    — `siuByHandler` names handlers and counts their referred claims — and that is
    enough on its own. But it is not the reason: this route would be gated if it
    named nobody, because what it gates is the analyst *workspace*. Epic 5 shipped
    an analyst who was a supervisor clone; this section is the first thing that
    persona has and the other two do not, and role is what separates a persona's
    surface from a view anyone may read.

    So a supervisor gets 403 here while continuing to read every Epic 5 dashboard
    route byte-identically to what she read before this story, which is what
    `tests/test_fraud_analytics.py` asserts from both directions.

    ## One document, loaded here

    `derivation_thresholds`, handed down, so the aggregate stays a composition of
    scope and parameters — `portfolio_summary`'s rule. One rather than three
    because every rule this surface reaches is a registered derivation, and
    `DerivationThresholds` is the single block all three are built from.
    """
    # `/stats/topbar`'s reasoning: this response is specific to one persona's
    # scope, so it must never be served to another from a cache upstream. First
    # statement in the body, so neither the refusal below nor any early return can
    # skip it.
    response.headers["Cache-Control"] = "no-store"
    # **Before the document load, not after it.** The service raises the same
    # refusal from the same allowlist and is still the real gate — capability
    # lives with the module that owns the rows — but it is reached one
    # `rule_document` read later, which would make "answered before any read" true
    # of the claim read and false of everything above it. Deliberately the
    # *service's* function rather than a copy of the condition: two spellings of
    # one allowlist is how a future `UserRole` gets admitted by one of them.
    try:
        require_fraud_analytics_access(ctx)
    except FraudAnalyticsNotPermitted as exc:
        raise _fraud_forbidden(exc) from exc
    thresholds = await thresholds_for(db)
    # Belt and braces, and cheap: the service checks the same allowlist itself, so
    # a refusal cannot escape as a 500 if this route is ever reordered or a second
    # caller appears. Unreachable today — the call above already refused — which
    # is why it delegates to the same translator rather than restating it.
    try:
        panel = await fraud_panel(db, ctx, thresholds, segmentation)
    except FraudAnalyticsNotPermitted as exc:  # pragma: no cover - the gate answered first
        raise _fraud_forbidden(exc) from exc

    return FraudPanelResponse(
        by_band=_categories(panel.by_band),
        siu_by_stage=_categories(panel.siu_by_stage),
        siu_by_handler=_handler_counts(panel.siu_by_handler),
        claims_in_scope=panel.claims_in_scope,
        flagged_claims=panel.flagged_claims,
        siu_claims=panel.siu_claims,
        fraud_band_high_min=panel.fraud_band_high_min,
        fraud_band_med_min=panel.fraud_band_med_min,
        fraud_flag_score_min=panel.fraud_flag_score_min,
        siu_fraud_score_min=panel.siu_fraud_score_min,
        rules_version=thresholds.version,
    )


class InjuryTypeRateResponse(ApiModel):
    """One injury type's flagged share, with its denominator on the wire.

    `flagged`, `claims` **and** `rateBp`, all three. A rate alone is
    uninterpretable over a thin bucket — one flagged claim of one is 100%, and so
    is fifty of fifty — and the honest answer is not a suppression rule the server
    would have to invent but the denominator beside the figure, so a reader can
    see what the percentage is a percentage *of*.

    `rateBp` is **basis points**, the unit the comp rate and the reserve ratio
    already use: an integer over a fixed scale, decided server-side with its
    rounding stated once, so the browser formats and divides nothing (AD-1).

    `injuryType` is the exact stored string and is what `filter[injuryType]`
    matches — no trimming, case-folding or merging, `LabelCountResponse`'s ruling
    over the same column.
    """

    injury_type: str
    flagged: int
    claims: int
    rate_bp: int


class EmployerRateResponse(ApiModel):
    """One employer's flagged share. `InjuryTypeRateResponse`'s figures, keyed by id.

    `employerId` rather than the label, `EmployerPaidResponse`'s rule: a
    `short_name` is a label and not an identity, and the drill-through has to
    filter on something that cannot collide.
    """

    employer_id: int
    label: str
    flagged: int
    claims: int
    rate_bp: int


class HandlerRateResponse(ApiModel):
    """One handler's flagged share. `EmployerRateResponse`'s shape over the other id.

    This is the row that puts a figure beside a named colleague — the very shape
    this file's discriminator gates `/dashboard/handler-benchmarks` for — and it
    is one of the reasons the whole section carries a role gate rather than
    relying on scope.
    """

    handler_id: int
    handler_name: str
    flagged: int
    claims: int
    rate_bp: int


class RateBreakdownResponse[RowT](ApiModel):
    """One finished breakdown: the rows in their order, and how they were cut.

    Generic over its row type so the four scalar fields are declared once and each
    table still publishes its own concrete row shape in the contract —
    `DistributionResponse`'s arrangement and its reason.

    **No `total` and no `nextCursor`, deliberately.** `items` is the whole list,
    which is `HandlerBenchmarksResponse`' shape, and it is what makes the `sort`
    parameter safe: `/dashboard/claims` refuses a sort because an offset into one
    ranking means nothing against another, and a list with no offset has nothing
    to be ambiguous about. Twenty, ten and six rows on the full portfolio.

    `sort` echoes the order that was applied, so a stored response is
    self-describing and a control renders the server's answer rather than its own
    last click — `appliedFilters`' reason on a different kind of input, and read
    the same way: `FraudRateTables.tsx` takes the `<select>`'s value from this
    field once an answer is on screen, so a request that 422s or times out cannot
    leave a control claiming an order the rows beside it are not in.

    `limit`, `totalCategories` and `truncated` are the truncation contract:
    `truncated` is decided here rather than left to a client comparing the other
    two, and `limit` is `null` for the two breakdowns that are never cut.
    """

    items: list[RowT]
    total_categories: int
    truncated: bool
    limit: int | None
    sort: FraudRateSort


class FraudRatesResponse(ApiModel):
    """The three flagged-rate breakdowns over one scoped book.

    **`flagged` is the *review* rule everywhere on this payload** — the same
    `fraud_flagged` derivation the Fraud Flags card was counted with and
    `filter[fraudFlagged]=true` opens — and deliberately not the SIU referral one,
    which is the narrower population and would put a rate under a heading
    promising the wider set. `fraudFlagScoreMin` rides along because the tables'
    footnote quotes it.

    `claimsInScope` and `flaggedClaims` are the portfolio-level pair every row is
    a partition of, so the table is checkable from the screen: the `claims` column
    sums to the first and the `flagged` column to the second, on the two uncapped
    breakdowns.

    `rulesVersion` names the document `fraudFlagScoreMin` came from, unrendered,
    for its siblings' reason.
    """

    by_injury_type: RateBreakdownResponse[InjuryTypeRateResponse]
    by_employer: RateBreakdownResponse[EmployerRateResponse]
    by_handler: RateBreakdownResponse[HandlerRateResponse]
    claims_in_scope: int
    flagged_claims: int
    fraud_flag_score_min: int
    rules_version: int


def _injury_rates(
    breakdown: RateBreakdown[InjuryTypeRate],
) -> RateBreakdownResponse[InjuryTypeRateResponse]:
    """The injury-type breakdown, field by field — `_categories`' reasoning."""
    return RateBreakdownResponse[InjuryTypeRateResponse](
        items=[
            InjuryTypeRateResponse(
                injury_type=row.injury_type,
                flagged=row.flagged,
                claims=row.claims,
                rate_bp=row.rate_bp,
            )
            for row in breakdown.items
        ],
        total_categories=breakdown.total_categories,
        truncated=breakdown.truncated,
        limit=breakdown.limit,
        sort=breakdown.sort,
    )


def _employer_rates(
    breakdown: RateBreakdown[EmployerRate],
) -> RateBreakdownResponse[EmployerRateResponse]:
    """The employer breakdown, field by field — `_categories`' reasoning."""
    return RateBreakdownResponse[EmployerRateResponse](
        items=[
            EmployerRateResponse(
                employer_id=row.employer_id,
                label=row.label,
                flagged=row.flagged,
                claims=row.claims,
                rate_bp=row.rate_bp,
            )
            for row in breakdown.items
        ],
        total_categories=breakdown.total_categories,
        truncated=breakdown.truncated,
        limit=breakdown.limit,
        sort=breakdown.sort,
    )


def _handler_rates(
    breakdown: RateBreakdown[HandlerRate],
) -> RateBreakdownResponse[HandlerRateResponse]:
    """The handler breakdown, field by field — `_categories`' reasoning."""
    return RateBreakdownResponse[HandlerRateResponse](
        items=[
            HandlerRateResponse(
                handler_id=row.handler_id,
                handler_name=row.handler_name,
                flagged=row.flagged,
                claims=row.claims,
                rate_bp=row.rate_bp,
            )
            for row in breakdown.items
        ],
        total_categories=breakdown.total_categories,
        truncated=breakdown.truncated,
        limit=breakdown.limit,
        sort=breakdown.sort,
    )


@router.get(
    "/dashboard/fraud/rates",
    response_model=FraudRatesResponse,
    summary="Flagged-claim rates by injury type, employer and handler",
    responses={**UNAUTHENTICATED_RESPONSE, **FRAUD_ANALYTICS_FORBIDDEN_RESPONSE},
)
async def fraud_rate_breakdowns(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    segmentation: SegmentationDep,
    injury_type_sort: Annotated[
        FraudRateSort,
        Query(
            alias="sort[injuryType]",
            description="The order the injury-type breakdown is served in.",
        ),
    ] = FraudRateSort.rate_desc,
    employer_sort: Annotated[
        FraudRateSort,
        Query(alias="sort[employer]", description="The order the employer breakdown is served in."),
    ] = FraudRateSort.rate_desc,
    handler_sort: Annotated[
        FraudRateSort,
        Query(alias="sort[handler]", description="The order the handler breakdown is served in."),
    ] = FraudRateSort.rate_desc,
) -> FraudRatesResponse:
    """Flagged-over-total by three dimensions, each independently ordered.

    ## Thirteen parameters, and not one of them is a scope

    Three sorts and the workspace's ten segmentation dimensions, the latter as
    one injected `Segmentation` — see `_segmentation`. The two kinds are
    different in what they may change: a sort re-orders rows the fold already
    produced, and a filter decides which rows are folded at all. Both are applied
    after the one scoped read, so neither costs a query and neither can widen the
    caller's book.

    ## The three sorts

    One `sort` per table, aliased in `filter[…]`'s style so the wire name and the
    parameter name are one string — the property `drill/filters.ts` rests on. Each
    is a closed `FraudRateSort`, so `sort[handler]=severity` is a 422 from
    FastAPI's own coercion before this function runs: the **type is the check**,
    and a vocabulary restated in the body would be the enum spelled twice.

    They are three rather than one because the three tables are three
    independent controls: sorting the injury-type breakdown must leave the other
    two at their declared defaults, which a single shared parameter could not
    express.

    There is still nowhere in this signature to put an employer, a user or an
    "as" (AD-7), and `?scopeAll=true` remains an unknown parameter FastAPI
    ignores.

    ## Why a `sort` here when `/dashboard/claims` refuses one

    That list is cursored, and an offset into one ranking means nothing against a
    different one, so a `sort` there would make every outstanding cursor
    ambiguous. These breakdowns carry no cursor and no `total` —
    `HandlerBenchmarksResponse`' shape — so a sort is a total order applied after
    a read that did not change, with nothing paged for it to invalidate.

    ## One document, loaded here

    `derivation_thresholds`, for `fraud`'s reason: the `flagged` column is a
    registered derivation's answer and that block is what it is built from.
    """
    # `/stats/topbar`'s reasoning, and first in the body so no refusal skips it.
    response.headers["Cache-Control"] = "no-store"
    # Before the document load — `fraud`'s note.
    try:
        require_fraud_analytics_access(ctx)
    except FraudAnalyticsNotPermitted as exc:
        raise _fraud_forbidden(exc) from exc
    thresholds = await thresholds_for(db)
    # Built field by field, like every response model in this file and for the
    # same reason: the mapping from the route's three parameters to the service's
    # three fields is the place a renamed table should fail to compile.
    sorts = FraudRateSorts(
        injury_type=injury_type_sort,
        employer=employer_sort,
        handler=handler_sort,
    )
    try:
        rates = await fraud_rates(db, ctx, thresholds, sorts, segmentation)
    except FraudAnalyticsNotPermitted as exc:  # pragma: no cover - the gate answered first
        raise _fraud_forbidden(exc) from exc

    return FraudRatesResponse(
        by_injury_type=_injury_rates(rates.by_injury_type),
        by_employer=_employer_rates(rates.by_employer),
        by_handler=_handler_rates(rates.by_handler),
        claims_in_scope=rates.claims_in_scope,
        flagged_claims=rates.flagged_claims,
        fraud_flag_score_min=rates.fraud_flag_score_min,
        rules_version=thresholds.version,
    )


class RedFlagClauseResponse(ApiModel):
    """One cached red-flag clause and how many claims named it.

    `claims` counts **distinct claims**, never mentions: a narrative listing one
    indicator twice is one claim worrying about one thing, and counting it twice
    would make one model's repetition look like a pattern across a book.

    `clause` is the first-seen original spelling. Two claims whose clauses differ
    only in case or in a trailing full stop are one row, and the row reads the way
    the first of them wrote it — a casefolded sentence on screen would make the
    view look generated by the fold rather than by the model.

    **Not a click target, and that is a decision.** Every other segment on this
    dashboard opens the claims behind it; a clause cannot, because "which claims
    does this phrase name" is a question only the fold's own normalisation can
    answer and turning that into a claim population would be the browser — or this
    endpoint — asserting a classification nobody versioned (AD-2). The coverage
    figures beside the ranking are what a reader navigates by instead.
    """

    clause: str
    claims: int


class FraudRedFlagsResponse(ApiModel):
    """The ranked clauses across one scoped book, and the cache behind them (AD-10).

    **Every figure here describes the cache, not the claims.** How many claims in
    scope carry a fraud narrative at all, when the oldest and newest were
    generated, how many rows this build could not read. AD-10's rule is that model
    output is rendered with its generation time and never presented as claim data,
    and on a portfolio-wide view the practical form of that rule is that the
    coverage is published beside the ranking: five clauses over a hundred claims
    and four narratives says something very different from five over a hundred and
    a hundred.

    **This is exact-text grouping over model-authored prose**, so a count of one is
    the ordinary case and the ranking is a reading aid rather than a taxonomy. The
    alternative — clustering or keyword-bucketing — would be the server
    originating a classification nobody can version or review;
    `services/worklist/fraud.py` carries the argument in full and the card's
    caption says so on screen.

    `generatedFrom` and `generatedTo` are both `null` on a cold cache, which is
    the seeded state and the ordinary one. Two nulls rather than an epoch or a
    "now", because a card cannot draw a range that does not exist and must not
    invent one.

    `unreadable` counts rows whose stored `content` failed re-validation — an
    older prompt version whose schema has since moved. They are excluded from the
    ranking, the coverage and the range, and counted here:
    `ClaimInsightsResponse`'s tolerance applied to a fold, so one bad row cannot
    take a portfolio view down and cannot be invisible either.

    `models` is every distinct model that wrote a readable row, sorted. A portfolio
    view spans generations, so there is no single model to label it with, and the
    honest answer over a set is the set. Empty when nothing was readable — which
    is when the card has no provenance line to draw and renders its empty state.

    **`rulesVersion` names the document that decided the *population*, not one
    that decided a figure**, and until Story 7.3 this payload deliberately had no
    such field — this view reaches no threshold, so publishing one would have been
    claiming a provenance the figures did not have. That is still true of every
    number on it. What changed is which claims those numbers are over: the
    workspace's segmentation narrows this view like every other, and two of its
    ten dimensions are registered derivations over `derivation_thresholds`. So the
    version is here on that footing and the distinction is worth keeping in mind
    when reading it beside `FraudPanelResponse`'s, which does name the document
    four published edges came from.
    """

    items: list[RedFlagClauseResponse]
    total_clauses: int
    truncated: bool
    limit: int
    claims_with_insight: int
    claims_in_scope: int
    unreadable: int
    generated_from: datetime | None
    generated_to: datetime | None
    models: list[str]
    rules_version: int


@router.get(
    "/dashboard/fraud/red-flags",
    response_model=FraudRedFlagsResponse,
    summary="Ranked red-flag clauses from the cached fraud narratives",
    responses={**UNAUTHENTICATED_RESPONSE, **FRAUD_ANALYTICS_FORBIDDEN_RESPONSE},
)
async def fraud_red_flag_frequency(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    segmentation: SegmentationDep,
) -> FraudRedFlagsResponse:
    """The red-flag frequency view, for whoever holds the session cookie.

    `fraud`'s signature: the ten segmentation dimensions and nothing else, so
    there is nowhere to put a scope (AD-7). Gated for that route's reason — this
    is the analyst workspace, and the gate runs before any read.

    **The one thing this route hands the aggregate is a reader**, not a parameter
    block: `agents.schemas.read_fraud_clauses`, which knows what a stored fraud
    card looks like. `services/` may never import `agents/` (AD-5), so the shape's
    owner supplies the reader and the fold receives it — `services/rag/insights.py`
    takes an injected `InsightGenerator` for exactly this reason. `api/` sits
    above both, which is why the injection happens here.

    **A rule document *is* loaded here since Story 7.3, and the sentence that used
    to sit in this paragraph was that it was not.** This view still groups prose
    and counts claims — no figure on it is produced at a threshold — but *which*
    claims it describes is now the segmentation's answer, and two of the ten
    dimensions are registered derivations over `derivation_thresholds`. So the
    block is loaded for the population rather than for a figure, and
    `rulesVersion` is published on that footing: it names the document that
    decided which claims were counted, not one that decided a number.

    **Read-only over `services/rag`'s table (AD-12).** Nothing on this path writes,
    and nothing on it triggers a refresh: a cold cache answers 200 with an empty
    ranking and a null range, which the card renders as a first-class empty state
    rather than as a spinner over work that is not happening. Regenerating an
    insight is the claim surface's Refresh, owned by the service that owns the
    rows.
    """
    # `/stats/topbar`'s reasoning, and first in the body so no refusal skips it.
    response.headers["Cache-Control"] = "no-store"
    try:
        require_fraud_analytics_access(ctx)
    except FraudAnalyticsNotPermitted as exc:
        raise _fraud_forbidden(exc) from exc
    # **A rule document is read on this route, and until Story 7.3 none was.**
    # Not for a figure — nothing this view publishes was produced at a threshold —
    # but for the *population*: two of the ten segmentation dimensions are
    # registered derivations over `derivation_thresholds`, so which claims the
    # ranking describes is now this document's answer. Loaded here and handed
    # down, `portfolio_summary`'s rule, and after the gate so no read precedes a
    # refusal.
    thresholds = await thresholds_for(db)
    # Belt and braces — `fraud`'s note. Unreachable today.
    try:
        ranked = await fraud_red_flags(db, ctx, read_fraud_clauses, thresholds, segmentation)
    except FraudAnalyticsNotPermitted as exc:  # pragma: no cover - the gate answered first
        raise _fraud_forbidden(exc) from exc

    return FraudRedFlagsResponse(
        items=[RedFlagClauseResponse(clause=row.clause, claims=row.claims) for row in ranked.items],
        total_clauses=ranked.total_clauses,
        truncated=ranked.truncated,
        limit=ranked.limit,
        claims_with_insight=ranked.claims_with_insight,
        claims_in_scope=ranked.claims_in_scope,
        unreadable=ranked.unreadable,
        generated_from=ranked.generated_from,
        generated_to=ranked.generated_to,
        models=list(ranked.models),
        rules_version=thresholds.version,
    )


# --- Story 7.2: the analyst workspace's Trends section -------------------


#: The two window refusals, as problem *types* rather than one shared 422.
#:
#: `FRAUD_ANALYTICS_FORBIDDEN_RESPONSE`'s argument applied to a validation
#: failure: a client can act differently on each, so collapsing them into one
#: description would describe neither. An inverted range is a bug in whatever
#: built the URL and the fix is to swap two values; a too-wide one is a
#: well-formed question this deployment declines to answer, and the fix is to
#: narrow the window or widen the grain. The cap is named in the detail *and*
#: published on every successful payload as `maxBuckets`, so a period control can
#: refuse locally rather than learning the edge from a 422.
TREND_RANGE_RESPONSE: dict[int | str, dict[str, object]] = {
    422: {
        "description": (
            "The requested window cannot be served: `/problems/trend-range-invalid` "
            "for a range that runs backwards, `/problems/trend-range-too-wide` for "
            "one covering more buckets than `maxBuckets` allows (RFC 9457 problem "
            "document). Decided from the parameters and the rules document alone, "
            "so no claim is read to produce it."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}


def _trend_range_invalid(exc: TrendRangeInvalid) -> ProblemException:
    """`TrendRangeInvalid` as this section's 422 problem document.

    `Cache-Control: no-store` is restated in the exception for `_fraud_forbidden`'s
    reason: raising abandons the injected `Response`, so the header set in the
    route body is never sent. A 422 that quotes a caller's own dates is as
    persona-agnostic as it gets, but the rule on this router is that *every*
    answer from a scoped surface is uncacheable, and an exception is exactly where
    that rule gets quietly dropped.
    """
    return ProblemException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        title="Unprocessable Content",
        detail=str(exc),
        type_="/problems/trend-range-invalid",
        headers={"Cache-Control": "no-store"},
    )


def _trend_range_too_wide(exc: TrendRangeTooWide) -> ProblemException:
    """`TrendRangeTooWide` as this section's 422 problem document.

    A separate translator from the one above for the reason there are two problem
    types at all — see `TREND_RANGE_RESPONSE`. One translator per refusal, each
    raised from exactly one place, because two copies of a problem type string is
    how the second one drifts.
    """
    return ProblemException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        title="Unprocessable Content",
        detail=str(exc),
        type_="/problems/trend-range-too-wide",
        headers={"Cache-Control": "no-store"},
    )


class TrendPointResponse(ApiModel):
    """One bucket of one series: what it was, what it says, how much is behind it.

    **`value` is `null`, never `0`, when there was nothing to average or rate**,
    and that is the prohibition this whole section exists for (AC 3). Counts and
    sums zero-fill because an empty period genuinely saw zero claims and paid zero
    cents; a mean and a rate over an empty denominator are not zero, they are
    absent, and a line drawn through zero would read as a real and excellent
    result. `SlaMetricResponse` draws the same distinction with `null` + `no_data`
    one route over, and this is that vocabulary inherited rather than a second
    one.

    `claimCount` travels beside every value including the null ones: a settlement
    mean over two claims and one over forty are the same kind of number and not
    the same kind of evidence. **It is the population of *this metric*, not of
    the bucket** — `avgSettlementDays` is a mean over settled claims carrying a
    duration and `rtwRateBp` a rate over settled claims, so a busy month in which
    three claims settled publishes `claimCount: 3` on those two points and the
    month's whole count on the other three. The bucket's own count is the
    `volume` series' *value* on the same payload and is deliberately not repeated
    here under a second name.

    `lowConfidence` is the server's verdict, not a threshold for the browser to
    apply — the ceiling is `trend_periods.lowConfidenceClaimMax` and a comparison
    made in the SPA would be the one rule on this payload nobody could see change
    (AD-8). It is `0 < claimCount <= ceiling` over that same per-metric count: a
    bucket with **no** claims behind a figure is not low confidence, it is no
    confidence, and its value is already `null`.

    `partial` says the bucket had not finished when the window was cut — true of
    the newest bucket of every ordinary window, since one ends in the period
    `asOf` falls in. The point is a part period drawn at a whole period's width
    and every metric on it is affected differently (a count and a sum are short,
    an average age is dragged toward zero by claims days old), so the fact is
    published and the card marks it rather than any figure being adjusted.

    `bucketFrom` and `bucketTo` are inclusive and are what the drill-through's
    date facets are filled from — a copy of the boundary the fold used, never a
    boundary the browser re-derived from `bucketKey`.
    """

    bucket_key: str
    bucket_label: str
    bucket_from: date
    bucket_to: date
    value: int | None
    claim_count: int
    low_confidence: bool
    partial: bool


class TrendBucketResponse(ApiModel):
    """One period on the x-axis: how to key it, how to name it, what it covers.

    **The window's vocabulary, published independently of the series**, and it
    exists because the series could not carry it reliably: an empty window under a
    cohort split publishes *no series at all*, so a client reading the periods off
    a series' points found none, and the drill period `<select>` and its "View
    claims" button both went dead — while the same empty window under
    `cohort=none` left them live, because zero-filled points still exist. The
    availability of the keyboard's only drill path depended on an unrelated
    selector, which was tolerable while an empty result was rare and is not now
    that a nine-dimension AND makes one ordinary.

    The four fields are `TrendPointResponse`'s first four, and they are the same
    four values — a point still carries them, because a drill is *per point* and
    looking a boundary up in a parallel array is one index slip away from opening
    the claims behind the month next door. This list is what a **control** reads;
    a point is what a **click** reads.

    `bucketFrom` and `bucketTo` are **inclusive** bounds and are what
    `filter[fnolFrom]`/`filter[fnolTo]` are filled from, so a bucket's drill
    returns exactly the claims its point was folded from.
    """

    bucket_key: str
    bucket_label: str
    bucket_from: date
    bucket_to: date


class TrendSeriesResponse(ApiModel):
    """One line on one chart: which metric, which cohort, and what a caption needs.

    **`points` is always the full window**, one entry per bucket in the window's
    order, gaps included as `null` values. A series that skipped its empty buckets
    would make two lines on one chart disagree about where March is.

    **`seriesTotal` is what emptiness is decided on, never `points.length`.** A
    zero-filled series has as many points as any other, so a length test can never
    fire — the bug Story 7.1 shipped once and `DistributionDonut` now avoids by
    testing its total. It is a *claim* count rather than a value total for all
    five metrics, deliberately: "was there anything here to describe" is the same
    question for a mean as for a sum, and a summed mean is not a number. It is the
    sum of the points' `claimCount`s and is therefore **per metric** — a window
    full of claims none of which settled empties the two SLA cards and no others,
    which is the honest answer and not the same answer as an empty book.

    `valueTotal` is the window total *of the metric* and is `null` for the three
    that cannot be summed — `null` rather than zero for the same reason a point
    is.

    `noDataBuckets` is the card's footnote ("3 of 12 periods have no data"),
    published rather than left to a client counting nulls, which is the arithmetic
    AD-1 removes from the browser.

    `metric` is a closed enum on the wire rather than a bare string, so the
    generated client gets a union and an unknown metric fails to compile —
    `RateBreakdownResponse.sort`'s arrangement, for its reason.

    `cohortKey`, `cohortLabel` and `paletteSlot` are all `null` on an unsplit
    series, so "is this a cohort?" is a field rather than an inference from the
    request. **`paletteSlot` is an ordinal, not a colour**: the browser maps it
    through the theme's categorical palette, and the slot is assigned by sorting
    cohort values on their *wire key* so a cohort keeps its colour across all five
    charts and across a refetch however its ranking moves. `cohortLabel` is `null`
    for all three dimensions this story ships — the two enums' labels are the
    SPA's (the Enums convention) and sector is free text where the stored value is
    the label — and the client's rule is `label ?? key`, which is
    `AppliedFilterResponse`'s split exactly.
    """

    metric: TrendMetric
    cohort_key: str | None
    cohort_label: str | None
    palette_slot: int | None
    points: list[TrendPointResponse]
    series_total: int
    value_total: int | None
    no_data_buckets: int


class PortfolioTrendsResponse(ApiModel):
    """Five metrics over one window of one scoped book, and what the axis means.

    **`asOf` is published**, which is new on this dashboard and is the point
    rather than a detail: `avgDaysOpen` counts up to a day and never freezes, so a
    chart of claim ages that did not say which day they were measured on would be
    unreadable the moment it was stored or forwarded. It is also the clock every
    bucket boundary was decided against, and `deferred-work.md` has wanted this
    field on a payload since Story 2.1.

    **`grain`, `anchor` and `cohort` are echoed as their enums**, so a stored
    payload is self-describing and a control renders the server's answer rather
    than its own last click — `RateBreakdownResponse.sort`'s reason on three
    inputs instead of one: a request that 422s or times out must not leave a
    selector claiming a grain the chart beside it is not drawn at.

    **The window is described three ways and none is redundant.** `grain` and
    `anchor` say what a bucket is and what puts a claim in one; `windowFrom` /
    `windowTo` are the outer bounds a caption quotes; `bucketCount` is what a
    client checks its point count against without counting an array.
    `defaultBuckets` and `maxBuckets` ride along so a period control offers
    exactly the range this deployment permits and refuses a wider one before a
    request is made.

    **`claimsInScope` and `claimsInWindow` are two different facts**, and the
    difference belongs on screen: the first is the population these figures
    describe, the second is how much of it the chosen window covers. A window
    describing eleven of a hundred claims is not wrong, but a reader who thinks
    it describes a hundred is.

    **Since Story 7.3 `claimsInScope` is the *segmented* book**, not the caller's
    whole one: with a filter applied every figure here describes the
    intersection, so a denominator quoting the unfiltered portfolio would put "11
    of 100" under charts folded from twenty. `FraudPanelResponse.claimsInScope`
    means the same thing for the same reason, and the unfiltered count is
    `claimsInScope` on `GET /dashboard/segmentation/values`, published there
    beside `claimsMatching` so the workspace can state both.

    **`buckets` is the window's vocabulary, and it is on this payload rather
    than only inside the series.** See `TrendBucketResponse`: a client that read
    the periods off a series' points found none at all when an empty window met a
    cohort split, and the drill period control went dead — a control whose
    availability depended on an unrelated selector. The list is always the full
    window, whatever the fold found in it.

    **Two rule documents, named separately.** `rulesVersion` is
    `derivation_thresholds` — where the severity cohort's band edges come from —
    exactly as it means on the fraud payloads, and the window parameters travel as
    `periodsVersion`. `deferred-work.md` records that `rulesVersion` already names
    different documents on different routes; two explicitly named fields is the
    one move that reduces that ambiguity rather than adding to it.

    **The two targets and the two band edges quote rules, so they travel with the
    figures** — `PortfolioChartsResponse`' reason. `settleTargetDays` and
    `rtwTargetBp` come from the `SlaTarget`s the strip decided its verdicts
    against, and the rate target is on the series' own basis-point scale because a
    target on a different scale is a reference line nobody can draw. The two
    severity edges come from the derivation that did the banding.
    """

    as_of: date
    grain: TrendGrain
    anchor: TrendAnchor
    cohort: TrendCohort
    window_from: date
    window_to: date
    bucket_count: int
    claims_in_scope: int
    claims_in_window: int
    buckets: list[TrendBucketResponse]
    series: list[TrendSeriesResponse]
    default_buckets: int
    max_buckets: int
    low_confidence_claim_max: int
    settle_target_days: int
    rtw_target_bp: int
    high_risk_severity_min: int
    med_risk_severity_min: int
    rules_version: int
    periods_version: int


def _trend_series(trends: PortfolioTrends) -> list[TrendSeriesResponse]:
    """The series, field by field — `_categories`' reasoning, two levels deep.

    Field by field rather than `model_validate` over the dataclass, for the reason
    every response model in this file is built explicitly: the mapping from a
    service value to a wire model is the place a renamed field should fail to
    compile, and a structural coercion is the place it silently would not. It
    matters more here than on a flat payload — a point carries two adjacent dates
    and two adjacent integers, and a `**asdict()` shortcut would carry a
    transposition through both nestings without a complaint.
    """
    return [
        TrendSeriesResponse(
            metric=series.metric,
            cohort_key=series.cohort_key,
            cohort_label=series.cohort_label,
            palette_slot=series.palette_slot,
            points=[
                TrendPointResponse(
                    bucket_key=point.bucket_key,
                    bucket_label=point.bucket_label,
                    bucket_from=point.bucket_from,
                    bucket_to=point.bucket_to,
                    value=point.value,
                    claim_count=point.claim_count,
                    low_confidence=point.low_confidence,
                    partial=point.partial,
                )
                for point in series.points
            ],
            series_total=series.series_total,
            value_total=series.value_total,
            no_data_buckets=series.no_data_buckets,
        )
        for series in trends.series
    ]


@router.get(
    "/dashboard/trends",
    response_model=PortfolioTrendsResponse,
    summary="Five time series over a bucketed window for the session's analyst",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **FRAUD_ANALYTICS_FORBIDDEN_RESPONSE,
        **TREND_RANGE_RESPONSE,
    },
)
async def trends(  # noqa: PLR0913 - a window, a split and the workspace's filter
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    settings: SettingsDep,
    segmentation: SegmentationDep,
    grain: Annotated[
        TrendGrain,
        Query(description="How wide one bucket is."),
    ] = TrendGrain.month,
    anchor: Annotated[
        TrendAnchor,
        Query(
            description=(
                "Which date a claim is bucketed by — the claim's FNOL date or the "
                "date of injury. Never both, and never a third column."
            )
        ),
    ] = TrendAnchor.fnol,
    cohort: Annotated[
        TrendCohort,
        Query(
            description=(
                "The single dimension each metric is split by, or `none`. Not a "
                "filter: a cohort split partitions the population rather than "
                "narrowing it."
            )
        ),
    ] = TrendCohort.none,
    from_date: Annotated[
        date | None,
        Query(
            alias="from",
            description=(
                "The first day the window covers; its whole bucket is included. "
                "Omitted, the window is `defaultBuckets` ending in `to`'s bucket."
            ),
        ),
    ] = None,
    to_date: Annotated[
        date | None,
        Query(
            alias="to",
            description=(
                "The last day the window covers; its whole bucket is included. "
                "Omitted, the window ends in the bucket `asOf` falls in."
            ),
        ),
    ] = None,
) -> PortfolioTrendsResponse:
    """Five server-computed series over the caller's book, for whoever holds the cookie.

    ## Fifteen parameters, and not one of them is a scope

    A grain, an anchor, a cohort dimension, two dates — and the workspace's ten
    segmentation dimensions as one injected `Segmentation` (see `_segmentation`).
    The five decide what is *drawn*: which periods exist, which date puts a claim
    in one, and how the lines are split. The ten decide which claims are folded
    into them at all, and every one is a narrowing applied after
    `employer_scope(ctx)` — `filter[employerId]` intersects the caller's book and
    can never widen it.

    Three of the five are
    closed enums, so `grain=fortnight` is a 422 from FastAPI's own coercion before
    this function runs — the **type is the check**, `FraudRateSort`'s arrangement,
    and a vocabulary restated in the body would be the enum spelled twice. The two
    dates are a *window*, not a filter and not a page: they narrow which periods
    are drawn, never which employers' claims are in them.

    There is nowhere in this signature to put an employer, a user or an "as"
    (AD-7), and `?scopeAll=true` remains an unknown parameter FastAPI ignores,
    exactly as it is on every route above.

    `from` and `to` are aliased because `from` is a Python keyword — the wire name
    and the parameter name are one string everywhere else in this file and this is
    the one place the language will not allow it.

    ## This endpoint is gated, and the gate is Story 7.1's

    `/dashboard/fraud`'s argument, unchanged and deliberately not re-derived: this
    is the analyst *workspace*, the surface Epic 5's analyst did not have, and
    role is what separates a persona's workspace from a view anyone may read. The
    allowlist is `fraud.FRAUD_ANALYTICS_ROLES` — reused rather than re-declared,
    because a second section of one workspace carrying a second spelling of one
    allowlist is how a future `UserRole` gets admitted by one of them.

    So a supervisor gets 403 here while continuing to read every Epic 5 dashboard
    route byte-identically to what she read before Epic 7 began.

    ## Two documents, loaded here

    `derivation_thresholds` decides the severity cohort's band edges, through the
    same registered `risk` derivation the High Risk card and 5.3's donut read.
    `trend_periods` decides the default window, its cap and the low-confidence
    ceiling. Both are loaded here and handed down so the aggregate stays a
    composition of scope and parameters — `portfolio_summary`'s rule — and both
    versions are published, named separately, because they are two documents and
    one field could only name one of them.

    ## The window is refused before the read, not after it

    Both 422s are decided from the caller's parameters and `trend_periods` alone,
    inside the service and above its one `await`. A window nobody can be served
    should not cost a query, and a refusal that arrived after a scoped read would
    have answered differently for an analyst with employers and one between
    assignments.
    """
    # `/stats/topbar`'s reasoning: this response is specific to one persona's
    # scope, so it must never be served to another from a cache upstream. First
    # statement in the body, so neither the refusal below nor any early return can
    # skip it.
    response.headers["Cache-Control"] = "no-store"
    # **Before the document loads, not after them** — `fraud`'s note. Deliberately
    # the *service's* function rather than a copy of the condition: two spellings
    # of one allowlist is how a future `UserRole` gets admitted by one of them.
    try:
        require_fraud_analytics_access(ctx)
    except FraudAnalyticsNotPermitted as exc:
        raise _fraud_forbidden(exc) from exc
    thresholds = await thresholds_for(db)
    periods = await trend_periods_for(db)
    try:
        # Every argument keyword, and `from_date`/`to_date` are why: two adjacent
        # optional dates would transpose silently and invert a window, which is
        # the same class of defect a positional projection build carries.
        computed = await portfolio_trends(
            db,
            ctx,
            thresholds,
            periods,
            settings,
            segmentation,
            grain=grain,
            anchor=anchor,
            cohort=cohort,
            from_date=from_date,
            to_date=to_date,
        )
    except TrendRangeInvalid as exc:
        raise _trend_range_invalid(exc) from exc
    except TrendRangeTooWide as exc:
        raise _trend_range_too_wide(exc) from exc
    except FraudAnalyticsNotPermitted as exc:  # pragma: no cover - the gate answered first
        # Belt and braces, and cheap: the service checks the same allowlist
        # itself, so a refusal cannot escape as a 500 if this route is ever
        # reordered or a second caller appears. Unreachable today.
        raise _fraud_forbidden(exc) from exc

    return PortfolioTrendsResponse(
        as_of=computed.as_of,
        grain=computed.grain,
        anchor=computed.anchor,
        cohort=computed.cohort,
        window_from=computed.window_from,
        window_to=computed.window_to,
        bucket_count=computed.bucket_count,
        claims_in_scope=computed.claims_in_scope,
        claims_in_window=computed.claims_in_window,
        buckets=[
            TrendBucketResponse(
                bucket_key=bucket.key,
                bucket_label=bucket.label,
                bucket_from=bucket.start,
                bucket_to=bucket.end,
            )
            for bucket in computed.buckets
        ],
        series=_trend_series(computed),
        default_buckets=computed.default_buckets,
        max_buckets=computed.max_buckets,
        low_confidence_claim_max=computed.low_confidence_claim_max,
        settle_target_days=computed.settle_target_days,
        rtw_target_bp=computed.rtw_target_bp,
        high_risk_severity_min=computed.high_risk_severity_min,
        med_risk_severity_min=computed.med_risk_severity_min,
        rules_version=thresholds.version,
        periods_version=periods.version,
    )


# --- Story 7.3: what the workspace can be segmented by -------------------


class DimensionValueResponse(ApiModel):
    """One value a picker may offer, and what it reads as.

    `value` is the wire form the `filter[…]` parameter carries (`high`, `female`,
    `Aerospace`, `3`) and `label` is a human name **or null** —
    `AppliedFilterResponse.display`'s split, restated on a control so the client's
    rule is one rule in both places: `label ?? UI_LABEL[key][value] ?? value`.

    **Exactly one of the ten dimensions carries a label**, and it is
    `employerId`, for that model's recorded reason: an id is not a name and
    nothing in the browser can turn `3` into "Boeing Everett" on a cold URL load.
    The two enums and the two bands are snake_case tokens whose copy the SPA owns
    (the Enums convention), and the five free-text dimensions are columns where
    the stored value *is* the label — shipping any of those from here would be
    the server deciding copy over a contract.

    `ageGroup` is the one that had to be argued rather than sorted, and it lands
    with the nine: its members are ordinal words a reader wants to see as a
    range of years, but that string is composed from the three age edges
    published beside these dimensions, so a `label` here would be a second copy
    of a value derived from a rule document — free to disagree with the chip one
    component over the first time an edge moved.
    """

    value: str
    label: str | None


class DimensionValuesResponse(ApiModel):
    """Every value one dimension carries **inside the caller's own book**.

    `key` is the facet name as `filter[…]` spells it, so a picker's `<select>`
    and the parameter it writes are one string and no translation table stands
    between them.

    **The values are folded from the caller's scoped rows**, which is the whole
    reason this endpoint exists rather than a client-side scan (AD-1) or a
    hardcoded vocabulary: a control cannot offer a sector, a region or an
    employer with no claims behind it, cannot enumerate anything outside the
    book, and therefore cannot lead an analyst into a zero-result page that was
    unreachable from the data. It is also what keeps `AppliedFilter.display`'s
    two null cases (`deferred-work.md`) out of reach through the control.

    The order is the server's and no client sorts it (AD-1): the two bands and
    the two enums come in their **declaration** order, because a vocabulary has
    one and "High, Low, Medium" is a severity picker nobody can scan; the other
    six sort ascending by what is on screen, which is the only order a reader can
    verify from the control itself.
    """

    key: str
    values: list[DimensionValueResponse]


class SegmentationValuesResponse(ApiModel):
    """What the workspace may be segmented by, what it currently is, and the age edges.

    One payload for the whole control, because it is one screen's worth of state
    folded from one scoped read.

    **`dimensions` is over the unfiltered book and `claimsMatching` is over the
    filtered one**, and that asymmetry is the design rather than an oversight: a
    picker whose options had been cut by the active filter could not be used to
    *widen* one, which would make every narrowing a one-way door. So the options
    describe what the caller could ask, and the two counts describe what they
    have asked.

    **`appliedFilters` is the server's reading of the URL**, in the same chip
    order the drill list publishes, and it is what the workspace's chip row draws
    — never a second parse of the query string in the browser. A URL carrying an
    unknown parameter name produces no chip, because the server narrowed nothing
    by it; a URL carrying `filter[sector]=Aerospace` produces exactly one. That
    is what makes "the chips, the request and the result agree" a property rather
    than a hope, and it is why the chips on this bar and the chips on the list a
    click into it opens are drawn from the same model with the same rule.

    **`claimsInScope` and `claimsMatching` are the pair every zero-result state
    is decided on.** Emptiness is a *total*, never a row count
    (`DistributionDonut`'s rule), and a nine-dimension AND makes it a normal
    outcome of a normal gesture rather than an edge case — so the figure that
    says "nothing matches" is published rather than inferred from an empty array.
    `claimsInScope` is also the unfiltered denominator the sections' own
    `claimsInScope` stopped being when they started describing the intersection.

    **The three age edges are here and nowhere else.** `AgeBand`'s members carry
    no numbers at all — deliberately, so that moving an edge in
    `derivation_thresholds` cannot leave a member name asserting the old one — and
    the range of years an analyst reads is composed in the browser from these
    three integers. One source, quoted once.

    `rulesVersion` names the document all three edges and both bands came from.
    """

    dimensions: list[DimensionValuesResponse]
    applied_filters: list[AppliedFilterResponse]
    claims_in_scope: int
    claims_matching: int
    age_younger_min: int
    age_older_min: int
    age_oldest_min: int
    rules_version: int


def _dimension_values(dimension: DimensionValues) -> DimensionValuesResponse:
    """One dimension's options, field by field — `_categories`' reasoning."""
    return DimensionValuesResponse(
        key=dimension.key,
        values=[
            DimensionValueResponse(value=option.value, label=option.label)
            for option in dimension.values
        ],
    )


@router.get(
    "/dashboard/segmentation/values",
    response_model=SegmentationValuesResponse,
    summary="The dimension values the session's analyst may segment by",
    responses={**UNAUTHENTICATED_RESPONSE, **FRAUD_ANALYTICS_FORBIDDEN_RESPONSE},
)
async def segmentation_dimension_values(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    segmentation: SegmentationDep,
) -> SegmentationValuesResponse:
    """What this caller's book can be sliced by — the segmentation control's read.

    ## Ten parameters, and not one of them is a scope

    The same ten `filter[…]` dimensions the four aggregate routes take, from the
    same `_segmentation` dependency, so the control and the sections it filters
    read one query string. They are used here for two things and neither is the
    options list: `claimsMatching` counts what survives, and `appliedFilters` is
    the server's reading of the URL that the chip row draws. The options
    themselves are folded over the **unfiltered** book — see
    `SegmentationValuesResponse` on why a picker narrowed by its own filter would
    be a one-way door.

    ## The path is `/dashboard/segmentation/values` and not `/dashboard/values`

    It is a *sub-resource of the segmentation control*, and naming it that way
    leaves room for the thing 7.5's export will want — a description of the
    current filter, at `/dashboard/segmentation/…` — without either endpoint
    having to be renamed. `/dashboard/values` would be a name that says nothing
    about which values.

    ## This endpoint is gated, and the argument is `/dashboard/fraud`'s

    It is the analyst workspace, and the gate is
    `fraud.FRAUD_ANALYTICS_ROLES` — reused rather than re-declared, because a
    third section of one workspace carrying a third spelling of one allowlist is
    how a future `UserRole` gets admitted by one of them. It buys slightly more
    here than on the sections it serves: this payload *enumerates* the employers,
    sectors, regions and ICD-10 codes a caller's book carries, which is the most
    directly enumerable thing the workspace publishes — and it enumerates them
    from claims in scope, so a caller learns nothing about a book that is not
    theirs.

    ## One document, loaded here

    `derivation_thresholds`, handed down, so the aggregate stays a composition of
    scope and parameters — `portfolio_summary`'s rule. One rather than two
    because everything this surface reaches is a registered derivation and its
    three published edges: the severity band, the age band, and the three
    cut-offs the second is built from.
    """
    # `/stats/topbar`'s reasoning: this response is specific to one persona's
    # scope, so it must never be served to another from a cache upstream. First
    # statement in the body, so neither the refusal below nor any early return can
    # skip it.
    response.headers["Cache-Control"] = "no-store"
    # Before the document load — `fraud`'s note.
    try:
        require_fraud_analytics_access(ctx)
    except FraudAnalyticsNotPermitted as exc:
        raise _fraud_forbidden(exc) from exc
    thresholds = await thresholds_for(db)
    # Belt and braces — `fraud`'s note. Unreachable today.
    try:
        values = await segmentation_values(db, ctx, thresholds, segmentation)
    except FraudAnalyticsNotPermitted as exc:  # pragma: no cover - the gate answered first
        raise _fraud_forbidden(exc) from exc

    return SegmentationValuesResponse(
        dimensions=[_dimension_values(dimension) for dimension in values.dimensions],
        applied_filters=[
            AppliedFilterResponse(key=item.key, value=item.value, display=item.display)
            for item in values.applied_filters
        ],
        claims_in_scope=values.claims_in_scope,
        claims_matching=values.claims_matching,
        age_younger_min=values.age_younger_min,
        age_older_min=values.age_older_min,
        age_oldest_min=values.age_oldest_min,
        rules_version=thresholds.version,
    )


# --- Story 7.4: the analyst workspace's Financial section -----------------


class MoneyTotalsResponse(ApiModel):
    """The three money figures, for a book or for any part of one.

    **Nested rather than flattened onto every row that carries them**, and the
    nesting is the contract rather than tidiness: these three describe *the same
    claims*, and a shape that spread them across a group's other fields would let
    a later reader add a fourth figure to one level and not the other. It is the
    same argument `SlaStripResponse` makes for keeping four tiles in one object.

    Every field is integer cents and says so in its name (the money convention).
    `web/src/lib/money.ts` is the only place a hundred is divided by, and no
    client adds any two of these together — `projectedCents` **is**
    `paidCents + reserveCents` on today's data and is published rather than left
    to be added, which is AD-1 at its most literal.

    **What each one sums is not obvious and is stated on the card, not here.**
    `paidCents` is the registered `total_paid` derivation over the static
    `paid_*` columns — the same figure `/dashboard/summary` publishes as
    `totalPaidCents` and 5.3's employer chart bars — which excludes bills and
    expenses paid on open claims; `deferred-work.md` carries that finding, its
    magnitude and its owner. `projectedCents` is `total_claim_projected` and is
    deliberately **not** called "incurred": the case file already uses "Total
    incurred" for the paid-only figure, and `services/derivations/claim_money.py`
    records the discrepancy that this payload refuses to spread.
    """

    paid_cents: int
    reserve_cents: int
    projected_cents: int


class BreakdownGroupResponse(ApiModel):
    """One row of the breakdown: what it is, how many claims, and what they cost.

    `key` is the **wire value the grouped dimension's own facet takes**, so a
    row's drill target is `filter[<groupBy>]=<key>` with nothing composed in
    between — the same string `appliedFilters` publishes and the same string the
    client's label maps are keyed on. `label` is a human name **or null**,
    `DimensionValueResponse`'s split restated on a row: only `employerId` carries
    one, because an id is not a name and nothing in the browser can turn `3` into
    "Boeing Everett" on a cold URL load.

    `claimCount` rides beside the money for `InjuryTypeRateResponse`'s reason: a
    spend figure is uninterpretable without the population behind it — one
    settled claim can outspend twenty open ones — and the honest answer is to
    publish the denominator rather than to invent a suppression rule.
    """

    key: str
    label: str | None
    claim_count: int
    totals: MoneyTotalsResponse


class FinancialBreakdownResponse(ApiModel):
    """One dimension's groups, ranked by projected cost and cut.

    `DistributionResponse`'s truncation contract over a series that distributes
    three quantities: `groupCount` is how many groups the segment holds *before*
    the cut, `truncated` is decided server-side rather than left to a client
    comparing two fields, and `limit` is the cap that was applied — so the card's
    caption reads "top 12 of 34" without any client restating the number.

    **There is no `total` here, and the portfolio totals one level up are not
    it.** Those are whole-segment figures that do not move when the tail is cut,
    which is what lets a caption quote a cut beside a total that is still the
    answer to "what does this segment cost". A `total` on this object would be a
    second sum a reader could not tell from that one.

    `dimension` echoes what was grouped by, `RateBreakdownResponse.sort`'s
    reason: the `<select>` renders the server's answer rather than its own last
    click, so a request that 422s or times out cannot leave a control claiming a
    grouping the rows beside it are not in.
    """

    dimension: BreakdownDimension
    items: list[BreakdownGroupResponse]
    group_count: int
    truncated: bool
    limit: int


class CostDriverCohortResponse(ApiModel):
    """One side of a cost-driver comparison.

    `key` is `"true"` or `"false"` — the wire form of the boolean facet this
    cohort drills on, so a click opens `filter[surgery]=true` with no mapping and
    the client's existing boolean label map answers "Yes"/"No" unchanged.

    **`averageProjectedCents` is null, never 0, for an empty cohort** —
    `TrendPointResponse.value`'s sentinel and its argument: a mean over an empty
    set is not zero, and a cohort reporting `$0` would read as a cohort that costs
    nothing rather than as one with no claims in it. It is floor division over
    cents, computed once server-side, and no client divides `projectedCents` by
    `claimCount` to check it (AD-1).

    All three totals travel beside the average, and the seeded book is why: all
    three litigated claims are open, so their `paidCents` is exactly zero and a
    paid-only comparison would report that litigation costs nothing at all.
    """

    key: str
    claim_count: int
    totals: MoneyTotalsResponse
    average_projected_cents: int | None


class CostDriverPairResponse(ApiModel):
    """A driver and its complement — the two cohorts that partition the segment.

    A pair rather than two independent rows, and the pairing is the assertion:
    the two `claimCount`s sum to `claimsInScope`, so neither cohort is a sample
    of the other and a card cannot end up comparing a surgery cohort against a
    portfolio total that contains it.

    `facet` is the `filter[…]` name each side drills on, published rather than
    inferred from the pair's position in the payload — `DimensionValuesResponse.key`'s
    rule: a control's value and the parameter it writes are one string, and
    nothing in the browser composes a parameter name.
    """

    facet: str
    with_driver: CostDriverCohortResponse
    without_driver: CostDriverCohortResponse


class FinancialDecompositionResponse(ApiModel):
    """Portfolio totals, one breakdown, and both cost-driver pairs.

    Three answers on one payload because they are folded from **one traversal of
    one scoped read** — `FraudPanelResponse`'s argument: separate requests would
    let a breakdown describe a different book from the total above it, and the
    identity a reader checks on this screen is precisely that the groups sum to
    the heading.

    `claimsInScope` counts the **segmented** book, which is what every analyst
    payload's field of that name has counted since Story 7.3; the unfiltered
    denominator is `claimsInScope` on `/dashboard/segmentation/values`.

    `rulesVersion` names `derivation_thresholds`, the document whose two band
    edges decided which claims a `severityBand` or `ageGroup` grouping put where.
    As on every sibling payload it rides along unrendered: it is what makes a
    stored or forwarded response self-describing. The *reserve* bands are a
    different document and are published on the adequacy payload as
    `bandsVersion`, because one field could only name one of them.
    """

    totals: MoneyTotalsResponse
    claims_in_scope: int
    breakdown: FinancialBreakdownResponse
    surgery: CostDriverPairResponse
    litigation: CostDriverPairResponse
    rules_version: int


class VerdictCountResponse(ApiModel):
    """One bucket of the reserve-adequacy distribution.

    `verdict` is the snake_case wire value of a `ReserveVerdict` member (the enum
    convention — the UI owns "Reserve Light", and the case file's own
    `RESERVE_VERDICT_LABEL` is the map that supplies it), and it is also the
    value `filter[reserveVerdict]` takes, so a segment's drill target is the
    segment's own key.
    """

    verdict: ReserveVerdict
    count: int


class ReserveAdequacyResponse(ApiModel):
    """The portfolio's reserve verdicts, counted, with the bands behind them.

    **Five items, always, in the enum's declaration order**, which is
    `FraudPanelResponse.byBand`'s zero-fill rule on a second vocabulary and for
    the same reason: a verdict is a *rule's* answer over a claim every book
    contains, so all five members exist for every portfolio, and "no claim in
    this segment is under-reserved" is the most valuable thing this chart can
    say. A distribution missing an empty bucket cannot be told from a build that
    forgot to draw it.

    **Five and not three**, although the story's AC names Light/Adequate/Heavy.
    `closed_final` and `indeterminate` are the two verdicts that are not band
    answers — a settled claim has no exposure left to judge, and a claim whose
    bills are not on file has not had a check rather than failed one — and on the
    seeded portfolio `closed_final` alone holds 62 of 100 claims. Publishing
    three buckets would have drawn a donut whose total was a third of
    `claimsInScope` under a heading reading "portfolio".

    `total` equals `claimsInScope` by construction: every claim in the segment
    lands in exactly one bucket. Both are published rather than one implied,
    because `DistributionDonut` decides emptiness on a *total* and never on a row
    count — which the zero-fill makes necessary — and because a card that
    reported one and implied the other would be asking a reader to trust an
    identity rather than see it.

    **The two band edges and `bandsVersion` travel with the figures**, for the
    reason `FraudPanelResponse`'s four thresholds do: the card's footnote quotes
    the ratios the buckets were produced at, so a client holding either number
    would be a second copy of a rule it cannot see change. They are read off the
    `ReserveBands` block the verdicts were computed with, and `bandsVersion`
    names `reserve_bands` rather than `derivation_thresholds` — a different
    document, so a different field.

    **And `rulesVersion` beside it, naming `derivation_thresholds`**, because
    this route reads that document too and the figures depend on it: the
    segmentation is applied through the registered `risk` and `age_band`
    computers, so `filter[severityBand]=high` narrows this distribution through
    edges the payload would otherwise never name. A retune of those edges moves
    every bucket here with nothing on the response to say which rules produced
    it — the state every sibling analyst payload publishes `rulesVersion` to
    prevent. Two documents decide these counts, so both are named; the earlier
    reading that one field "could only name one of them" was an argument for a
    second field, not for an omission.
    """

    items: list[VerdictCountResponse]
    total: int
    claims_in_scope: int
    light_ratio_bp: int
    heavy_ratio_bp: int
    bands_version: int
    rules_version: int


def _money(totals: MoneyTotals) -> MoneyTotalsResponse:
    """The three figures, field by field — `_categories`' reasoning."""
    return MoneyTotalsResponse(
        paid_cents=totals.paid_cents,
        reserve_cents=totals.reserve_cents,
        projected_cents=totals.projected_cents,
    )


def _breakdown(breakdown: FinancialBreakdown) -> FinancialBreakdownResponse:
    """One breakdown, field by field — `_categories`' reasoning."""
    return FinancialBreakdownResponse(
        dimension=breakdown.dimension,
        items=[
            BreakdownGroupResponse(
                key=group.key,
                label=group.label,
                claim_count=group.claim_count,
                totals=_money(group.totals),
            )
            for group in breakdown.items
        ],
        group_count=breakdown.group_count,
        truncated=breakdown.truncated,
        limit=breakdown.limit,
    )


def _cost_driver(pair: CostDriverPair) -> CostDriverPairResponse:
    """One driver's two cohorts, field by field — `_categories`' reasoning."""
    return CostDriverPairResponse(
        facet=pair.facet,
        with_driver=CostDriverCohortResponse(
            key=pair.with_driver.key,
            claim_count=pair.with_driver.claim_count,
            totals=_money(pair.with_driver.totals),
            average_projected_cents=pair.with_driver.average_projected_cents,
        ),
        without_driver=CostDriverCohortResponse(
            key=pair.without_driver.key,
            claim_count=pair.without_driver.claim_count,
            totals=_money(pair.without_driver.totals),
            average_projected_cents=pair.without_driver.average_projected_cents,
        ),
    )


@router.get(
    "/dashboard/financials",
    response_model=FinancialDecompositionResponse,
    summary="Paid, reserve and projected totals with a breakdown, for the session's analyst",
    responses={**UNAUTHENTICATED_RESPONSE, **FRAUD_ANALYTICS_FORBIDDEN_RESPONSE},
)
async def financials(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    segmentation: SegmentationDep,
    group_by: Annotated[
        BreakdownDimension,
        Query(
            alias="groupBy",
            description=(
                "Which of the ten segmentation dimensions the money is broken "
                "down by. The members are the `filter[…]` facet names themselves, "
                "so a group's key is what its own drill-through filters on."
            ),
        ),
    ] = DEFAULT_BREAKDOWN,
) -> FinancialDecompositionResponse:
    """The Financial section's totals, breakdown and cost drivers.

    ## Eleven parameters, and not one of them is a scope

    The workspace's ten `filter[…]` dimensions as one injected `Segmentation`
    (see `_segmentation`) and one control. The ten decide **which claims** are
    folded and every one is a narrowing applied after `employer_scope(ctx)`, so
    `filter[employerId]` intersects the caller's book and can never widen it; the
    control decides **how the money is grouped** and changes no read at all.
    There is nowhere in this signature to put an employer, a user or an "as"
    (AD-7), and `?scopeAll=true` remains an unknown parameter FastAPI ignores.

    `groupBy`'s **type is the check**: `groupBy=nonsense` is a 422 from FastAPI's
    own coercion before this function runs, `FraudRateSort`'s arrangement, which
    is why `BreakdownDimension` holds no vocabulary check and must not grow one.

    ## Two routes rather than one, and this is the cheap half

    The adequacy distribution is `/dashboard/financials/reserve-adequacy` and not
    a field here, for two reasons that point the same way. It costs **three**
    scoped reads and a second rule document where this costs one and one, so
    folding it in would make every totals render pay for a chart the analyst may
    not be looking at; and the two would then fail together, where
    `FraudPage`'s four independent queries let one card's outage leave the rest
    of a section standing (NFR-3).

    ## This endpoint is gated, and the argument is `/dashboard/fraud`'s

    It is the analyst *workspace* — the fourth section of the surface Epic 5's
    analyst did not have — and the allowlist is `fraud.FRAUD_ANALYTICS_ROLES`,
    reused rather than re-declared, because a fourth section carrying a fourth
    spelling of one allowlist is how a future `UserRole` gets admitted by one of
    them. A supervisor gets 403 here while continuing to read every Epic 5
    dashboard route byte-identically.

    ## One document, loaded here

    `derivation_thresholds`, handed down, so the aggregate stays a composition of
    scope and parameters — `portfolio_summary`'s rule. One rather than two
    because everything this surface reaches is a registered derivation: the two
    money computers, and the `risk` and `age_band` bands two of the ten groupings
    go through. `reserve_bands` is the *other* route's document, and it is loaded
    there.
    """
    # `/stats/topbar`'s reasoning: this response is specific to one persona's
    # scope, so it must never be served to another from a cache upstream. First
    # statement in the body, so neither the refusal below nor any early return can
    # skip it.
    response.headers["Cache-Control"] = "no-store"
    # **Before the document load, not after it** — `fraud`'s note. Deliberately
    # the *service's* function rather than a copy of the condition: two spellings
    # of one allowlist is how a future `UserRole` gets admitted by one of them.
    try:
        require_fraud_analytics_access(ctx)
    except FraudAnalyticsNotPermitted as exc:
        raise _fraud_forbidden(exc) from exc
    thresholds = await thresholds_for(db)
    # Belt and braces — `fraud`'s note. Unreachable today: the call above already
    # refused.
    try:
        computed = await financial_decomposition(
            db, ctx, thresholds, segmentation, dimension=group_by
        )
    except FraudAnalyticsNotPermitted as exc:  # pragma: no cover - the gate answered first
        raise _fraud_forbidden(exc) from exc

    return FinancialDecompositionResponse(
        totals=_money(computed.totals),
        claims_in_scope=computed.claims_in_scope,
        breakdown=_breakdown(computed.breakdown),
        surgery=_cost_driver(computed.surgery),
        litigation=_cost_driver(computed.litigation),
        rules_version=thresholds.version,
    )


@router.get(
    "/dashboard/financials/reserve-adequacy",
    response_model=ReserveAdequacyResponse,
    summary="The reserve adequacy verdict distribution for the session's analyst",
    responses={**UNAUTHENTICATED_RESPONSE, **FRAUD_ANALYTICS_FORBIDDEN_RESPONSE},
)
async def financial_reserve_adequacy(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    segmentation: SegmentationDep,
) -> ReserveAdequacyResponse:
    """How the caller's book is reserved — Epic 3's verdict, counted (AC 2).

    ## Ten parameters, and not one of them is a scope

    The same ten `filter[…]` dimensions the other analyst routes take, from the
    same `_segmentation` dependency, so the whole workspace reads one query
    string. There is no control here: a distribution over a closed five-member
    vocabulary has nothing to group by and nothing to sort.

    ## The verdict is Epic 3's, reached by Epic 3's function

    `services/worklist/decomposition.adequacy_of` **counts** verdicts and
    computes none: they arrive from
    `services/financials/reserve.reserve_checks_for_claims`, which folds each
    claim's stored payment-schedule weeks and bills through
    `reserve_check_from_rows` — the same function the case file's chip and the
    Bills tab's summary reach the verdict through (AD-2, AD-10). A bucket on this
    chart and a chip on a claim are therefore one computation with two callers,
    which is what `filter[reserveVerdict]` then makes navigable.

    **It does not materialize**, and that is the one behavioural difference from
    the claim-level path, stated rather than hidden: `reserve_check_for_claim`
    refreshes the schedule before reading it and a refresh is a write, which an
    analyst route may not make. The residual gap is a claim whose week boundary
    has passed since anyone last opened it; the card footnotes it.

    ## Three scoped reads, and the number is published in the tests

    The claims, then the weeks and the bills in bulk. `fraud/red-flags` records
    the same shape for its two, and the reason the count is guarded is the
    obvious wrong implementation on this path: a loop over
    `reserve_check_for_claim` would be three reads *and* a schedule refresh per
    claim on a `GET`.

    ## This endpoint is gated, and the argument is `/dashboard/financials`'

    Same workspace, same allowlist, reused rather than re-declared.

    ## Two documents, loaded here

    `derivation_thresholds` builds the segmentation's two band computers — the
    filter can narrow on `severityBand` and `ageGroup`, which are registered
    derivations — and `reserve_bands` decides the verdict. Both are loaded here
    and handed down so the aggregate stays a composition of scope and parameters,
    and both versions reach the client: `bandsVersion` on this payload names the
    second, because it is the document that decided every bucket.
    """
    # `/stats/topbar`'s reasoning — `financials`' note. First statement in the
    # body, so no early return can skip it.
    response.headers["Cache-Control"] = "no-store"
    # Before both document loads — `fraud`'s note.
    try:
        require_fraud_analytics_access(ctx)
    except FraudAnalyticsNotPermitted as exc:
        raise _fraud_forbidden(exc) from exc
    thresholds = await thresholds_for(db)
    bands = await reserve_bands_for(db)
    # Belt and braces — `fraud`'s note. Unreachable today.
    try:
        adequacy = await reserve_adequacy(db, ctx, thresholds, bands, segmentation)
    except FraudAnalyticsNotPermitted as exc:  # pragma: no cover - the gate answered first
        raise _fraud_forbidden(exc) from exc

    return ReserveAdequacyResponse(
        items=[
            VerdictCountResponse(verdict=item.verdict, count=item.count) for item in adequacy.items
        ],
        total=adequacy.total,
        claims_in_scope=adequacy.claims_in_scope,
        light_ratio_bp=adequacy.light_ratio_bp,
        heavy_ratio_bp=adequacy.heavy_ratio_bp,
        bands_version=adequacy.bands_version,
        rules_version=thresholds.version,
    )
