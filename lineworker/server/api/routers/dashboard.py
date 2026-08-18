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
"""

from collections.abc import Mapping
from typing import Annotated, Final

from fastapi import APIRouter, Query, Response, status

from api.deps import CallerContextDep, DbDep, SettingsDep
from api.errors import PROBLEM_CONTENT_TYPE, ProblemDocument, ProblemException
from api.routers.auth import UNAUTHENTICATED_RESPONSE
from api.routers.stats import SlaMetricResponse, SlaStripResponse
from api.schemas import ApiModel
from data.models.enums import Stage
from rules.parameters import (
    handler_performance_for,
    thresholds_for,
    weights_for,
    worklist_actions_for,
)
from services.derivations import ComplexityBand, CycleStatus, RiskBand
from services.worklist import (
    BenchmarksNotPermitted,
    InvalidCursor,
    handler_benchmarks,
    portfolio_charts,
    portfolio_summary,
    priority_claims,
    require_benchmarks_access,
)
from services.worklist.charts import CategoryCount, Distribution, EmployerPaid, LabelCount
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
