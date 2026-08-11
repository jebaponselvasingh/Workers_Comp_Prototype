"""Claim reads. Story 2.1 opens the router with the handler's queue.

Thin by AD-1: the route validates four query parameters, calls one service,
and maps its result onto the wire. Every number and every flag in the
payload was decided in `services/worklist`.

Thin by AD-7 in the same way `/stats/*` is — **there is no scope-shaped
parameter here**. Not a rejected one: an absent one. The four parameters
are `filter`, `stage`, `cursor` and `limit`, none of which can name an
employer, a user or a role, so "whose claims?" has exactly one answer and it
comes from the session cookie. A `employerId` a caller invents is ignored
like any other unknown query string (`tests/test_claims_queue.py` proves the
answer is byte-identical), because a 422 would tell them which names exist.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from api.deps import CallerContextDep, DbDep
from api.errors import PROBLEM_CONTENT_TYPE, ProblemDocument, ProblemException
from api.routers.auth import UNAUTHENTICATED_RESPONSE
from api.schemas import ApiModel
from data.models.enums import Stage
from services.derivations import RiskBand
from services.worklist.priority import QueueFilter
from services.worklist.queue import (
    MAX_PAGE_LIMIT,
    MIN_PAGE_LIMIT,
    ClaimQueue,
    InvalidCursor,
    QueueCard,
    StageGroup,
    claim_queue,
)

router = APIRouter(tags=["claims"])

BAD_CURSOR_RESPONSE: dict[int | str, dict[str, object]] = {
    400: {
        "description": (
            "The pagination cursor is unreadable, or belongs to a different "
            "filter, group or rules version (RFC 9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}


class ClaimCardResponse(ApiModel):
    """One queue card — every field of it computed server-side (AD-1).

    The four rows the prototype draws, in payload form: identity and age;
    risk dot, worker and flag badges; injury type; stage pill and employer.
    Nothing here is a hint the client finishes computing — `risk` is a band,
    not a score, and `priorityMarker` is a decision, not a rank the SPA
    thresholds itself.

    `injuryType` carries the **full** string. The prototype clamps it to 30
    characters, but the longest in the seeded portfolio is 29, so the clamp
    never fires — and truncation is a property of the column it is drawn in,
    not of the claim. The card truncates visually and keeps the whole text
    available to a screen reader.

    `priorityScore` is published deliberately, though the items already
    arrive in order. It is what makes the ordering explainable — to a
    handler asking why a claim is third, to a spec asserting the rule, and
    to Epic 5 reusing the same figure on the dashboard. It is not an
    invitation to re-sort: the score depends on a rules version the client
    does not have.
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


class StageGroupResponse(ApiModel):
    """One stage's page, in the `{items, nextCursor, total}` envelope.

    Unlike `/personas` and `/glossary`, whose cursors are structurally null,
    this one is real: `total` is the size of the whole filtered group (the
    number in the chip beside the stage header) and `nextCursor` is non-null
    exactly while claims remain beyond `items`.
    """

    items: list[ClaimCardResponse]
    next_cursor: str | None = None
    total: int


class StageGroupsResponse(ApiModel):
    """The four groups, always all four.

    Named fields rather than a map keyed by stage: the four stages are the
    contract (the SPA renders four sections in this order whatever the data
    says), and a map would let a response omit one — which the client would
    have to render as either "empty" or "unknown", two very different
    things.
    """

    intake: StageGroupResponse
    investigation: StageGroupResponse
    treatment: StageGroupResponse
    settled: StageGroupResponse


class ClaimQueueResponse(ApiModel):
    """The queue, the rules that ranked it, and the two totals behind it.

    **Both rule-document versions, not one.** `rulesVersion` names the
    `priority_weights` version and `thresholdsVersion` names the
    `derivation_thresholds` one. Reporting only the first was reporting half
    the answer: the thresholds decide `risk`, `siuReview` and `rtwBlocked`,
    which are *inputs* to every score in the payload, and the cursor already
    records both for exactly that reason. Any conversation about "why is
    this claim first" starts by establishing which documents answered.

    **Both totals, and the SPA computes neither.** `unfilteredTotal` is how
    many claims the caller has in scope *before* the filter; `filteredTotal`
    is how many survived it. The second is the sum of the four group totals,
    and it is sent anyway: a client that adds them up has re-implemented
    "how big is this queue" in the browser, which is the derivation AD-1
    keeps server-side and `noDerivation.test.ts` fails a build over. The pair
    is what lets the pane tell "no claims in your caseload" from "no claims
    match this filter" (NFR-3) without holding an unfiltered copy of the
    caseload to compare against.
    """

    groups: StageGroupsResponse
    rules_version: int
    thresholds_version: int
    unfiltered_total: int
    filtered_total: int


def _card(card: QueueCard) -> ClaimCardResponse:
    return ClaimCardResponse(
        claim_id=card.claim.claim_id,
        days_open=card.claim.days_open,
        risk=card.flags.risk,
        worker_name=card.claim.worker_name,
        injury_type=card.claim.injury_type,
        stage=card.claim.stage,
        employer_short_name=card.claim.employer_short_name,
        fraud_flag=card.claim.fraud_flag,
        litigation_flag=card.claim.litigation_flag,
        payment_due=card.flags.payment_due,
        siu_review=card.flags.siu_review,
        rtw_blocked=card.flags.rtw_blocked,
        priority_score=card.priority_score,
        priority_marker=card.priority_marker,
    )


def _group(group: StageGroup) -> StageGroupResponse:
    return StageGroupResponse(
        items=[_card(card) for card in group.items],
        next_cursor=group.next_cursor,
        total=group.total,
    )


def _queue(result: ClaimQueue) -> ClaimQueueResponse:
    return ClaimQueueResponse(
        groups=StageGroupsResponse(
            intake=_group(result.groups[Stage.intake]),
            investigation=_group(result.groups[Stage.investigation]),
            treatment=_group(result.groups[Stage.treatment]),
            settled=_group(result.groups[Stage.settled]),
        ),
        rules_version=result.rules_version,
        thresholds_version=result.thresholds_version,
        unfiltered_total=result.unfiltered_total,
        filtered_total=result.filtered_total,
    )


@router.get(
    "/claims/queue",
    response_model=ClaimQueueResponse,
    summary="The session persona's claim queue, grouped by stage and ranked",
    responses={**UNAUTHENTICATED_RESPONSE, **BAD_CURSOR_RESPONSE},
)
async def queue(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    queue_filter: Annotated[
        QueueFilter,
        Query(
            alias="filter",
            description="One of the eight operational filters; unknown values are refused.",
        ),
    ] = QueueFilter.all,
    stage: Annotated[
        Stage | None,
        Query(description="Which group `cursor` addresses. Never narrows the response."),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(description="An opaque `nextCursor` from a previous response."),
    ] = None,
    limit: Annotated[
        int | None,
        Query(
            ge=MIN_PAGE_LIMIT,
            le=MAX_PAGE_LIMIT,
            description="Page size per group; defaults to the rules document's.",
        ),
    ] = None,
) -> ClaimQueueResponse:
    """The caller's queue. Filter and page it; you cannot re-scope it."""
    # Specific to one persona's book, so it must never be served to another
    # from a cache upstream — the same reason `/me` and `/stats/*` say so.
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await claim_queue(
            db,
            ctx,
            queue_filter=queue_filter,
            stage=stage,
            cursor=cursor,
            limit=limit,
        )
    except InvalidCursor as exc:
        # 400 rather than 422: the cursor is syntactically a string and
        # passed validation. What failed is that it does not describe a
        # position in *this* list — a fact only the service knows.
        #
        # The header is re-stated here because raising abandons `response`:
        # the exception handler builds a fresh `JSONResponse` and the
        # injected one is never sent. A 400 that names a caller's filter and
        # stage is as persona-specific as the 200 above it, and it is the
        # response most likely to be retried — leaving it cacheable would
        # let an intermediary answer the *next* handler's "Show more" with
        # this one's refusal.
        raise ProblemException(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Bad Request",
            detail=str(exc),
            type_="/problems/invalid-cursor",
            headers={"Cache-Control": "no-store"},
        ) from exc
    return _queue(result)
