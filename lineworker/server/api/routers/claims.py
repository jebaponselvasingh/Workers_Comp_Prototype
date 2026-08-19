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

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Annotated, Any, Literal

import httpx
import structlog
from fastapi import APIRouter, Path, Query, Response, status
from pydantic import ConfigDict, Field

from api.deps import CallerContextDep, DbDep, SettingsDep
from api.errors import PROBLEM_CONTENT_TYPE, ProblemDocument, ProblemException
from api.routers.auth import UNAUTHENTICATED_RESPONSE
from api.schemas import ApiModel
from data.models.enums import (
    ActionCommand,
    ActionKey,
    ActionTarget,
    ActionUrgency,
    BillCategory,
    ClaimPath,
    ClaimStatus,
    CommStatus,
    Disability,
    DocType,
    ExpenseCategory,
    LineItemStatus,
    RecoveryWindow,
    ReturnStatus,
    ScheduleWeekStatus,
    Stage,
)
from services.claims.assessment import (
    approve_assessment,
    mark_osha_logged,
    set_document_review,
)
from services.claims.comp_rate import update_comp_rate_override
from services.claims.detail import (
    ClaimDetail,
    ClaimNotVisible,
    claim_detail,
    claim_financial_detail,
)
from services.claims.documents import DocumentNotVisible, document_content
from services.claims.edit import (
    EditNotPermitted,
    InvalidPatch,
    StaleClaim,
    update_claim_fields,
    update_claim_severity,
)
from services.claims.injuries import add_additional_injury, remove_additional_injury
from services.claims.reference import SEVERITY_MAX, SEVERITY_MIN
from services.derivations import CoordinationStatus, IndemnityType, RiskBand, TreatmentPhase
from services.financials import (
    COMP_RATE_MAX_BP,
    COMP_RATE_MIN_BP,
    ClaimFinancials,
    MissingStateRate,
    PaymentNotVisible,
    ReserveVerdict,
    StalePaymentRow,
)
from services.rag import MAX_K, embedding_client
from services.rag import similar_claims as rag_similar_claims
from services.rag.client import EmbeddingDimensionMismatch
from services.worklist.actions import claim_actions
from services.worklist.approvals import (
    ApprovalKind,
    ApprovalNotPermitted,
    ApprovalResult,
    approve_payment,
)
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

log = structlog.get_logger()

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


# --- Story 2.2: the case file -------------------------------------------


CLAIM_ID_PATTERN = r"^WC-\d{4,6}$"

#: The one route that needs a model server to answer a read declares this
#: (`GET /claims/{id}/similar`). Kept beside the 404 rather than in that route's
#: decorator so the two documented failure modes of this router read together.
MODEL_UNAVAILABLE_RESPONSE: dict[int | str, dict[str, object]] = {
    503: {
        "description": (
            "The local model server did not answer, so the query claim could "
            "not be embedded. Temporary and specific to AI-backed reads — "
            "claim data is unaffected (RFC 9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}

NOT_FOUND_RESPONSE: dict[int | str, dict[str, object]] = {
    404: {
        "description": (
            "No such claim in the caller's scope. Deliberately the same answer "
            "for a claim that does not exist and one that belongs to another "
            "employer — see the route docstring (RFC 9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}

#: Declared on every route that assembles a case file, which is all of them
#: except the document sheet: the benefit block is part of the payload, so a
#: jurisdiction with no statutory rate schedule takes the whole response down
#: rather than blanking one card. That is the intended behaviour (NFR-4 — no
#: silent default), and a contract that did not say so would leave a client
#: with an undeclared 500 in its generated types.
RATE_SCHEDULE_RESPONSE: dict[int | str, dict[str, object]] = {
    500: {
        "description": (
            "The claim's jurisdiction has no `state_rate_schedule` row, so its "
            "weekly benefit cannot be calculated and no default is substituted "
            "(RFC 9457 problem document). Unreachable against a correctly "
            "migrated database — 0023 refuses to complete otherwise."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}


class TimelineEntryResponse(ApiModel):
    """One line of the case timeline.

    `eventDate` is nullable because 62 seeded settlement events have none:
    the prototype writes the literal string `Closed` where a date belongs,
    and a non-date is not something a `DATE` column should be asked to hold.
    The UI leaves the date cell empty rather than inventing one.
    """

    event_date: date | None
    description: str
    tag: str


class ChecklistRowResponse(ApiModel):
    """One required intake document, and whether it is on file."""

    doc_type: DocType
    received: bool


class StepperStepResponse(ApiModel):
    """One of the four lifecycle steps, already marked.

    `done`/`current` are the server's, not the browser's: which steps are
    behind a claim is a statement about its lifecycle position, and deciding
    it client-side would be a second place that knows what follows
    investigation (AD-1).
    """

    stage: Stage
    done: bool
    current: bool


class CostSplitResponse(ApiModel):
    """The cost bar's three shares, as whole percentages summing to 100.

    Guaranteed to total exactly 100 — `services/derivations/claim_money.py`
    rounds the first two and gives the third the remainder — because three
    independently rounded shares can total 99 or 101, and a bar drawn as three
    widths then under- or overflows its track. The prototype has that bug.

    **One model for three surfaces** (Story 3.3): the investigation card's
    total-incurred bar, the settled payout breakdown and the Bills tab's
    paid-cost bar. They are drawn over different triples — the last one uses
    the effective breakdown rather than the `paid_*` columns — but the *shape*
    and the summing rule are one thing, and a second identical model here
    would have published two names for one contract to the generated client.
    """

    indemnity_pct: int
    medical_pct: int
    expense_pct: int


class CaseHeaderResponse(ApiModel):
    """Everything above the tab bar (UX-DR4).

    `risk` is the band the gauge is coloured by, and it is the *same* `risk`
    derivation the queue card's dot reads (AD-10) — which is what makes "the
    queue and the detail can never disagree about a claim" a property of the
    system rather than a promise. `severityScore` rides along because the
    header shows both ("High severity", 78/100), and the band is not
    recoverable from the score in the browser without re-implementing the
    thresholds.
    """

    claim_id: str
    worker_name: str
    worker_role: str
    employer_name: str
    state: str
    injury_type: str
    body_part: str
    # The diagram/select key behind the label. Not derivable from
    # `bodyPart` — the dataset's wording ("Wrist(s) & Hand(s)") and the
    # diagram's labels ("Right Hand") are different vocabularies.
    body_key: str
    cause: str
    icd: str
    severity_score: int
    risk: RiskBand
    stage: Stage
    #: The claim's assessment status (Story 3.5). New on this payload because
    #: 3.5 is the first story that *moves* it: the approval command writes
    #: `ch_approved`, and AC 4 asks the header to show the change without a
    #: manual refresh. Distinct from `stage` — a treatment-stage claim can be
    #: `initial` or `ch_approved` — and the UI owns the label.
    status: ClaimStatus
    fraud_flag: bool
    fraud_score: int
    litigation_flag: bool
    osha_recordable: bool
    #: Whether the OSHA 300 entry has been filed (Story 3.5). Read beside
    #: `oshaRecordable`, which is the only reason either is interesting.
    osha_logged: bool
    surgery_required: bool


class BodyPartOptionResponse(ApiModel):
    """One region of the body diagram: the key stored, the label shown."""

    key: str
    label: str


class EditOptionsResponse(ApiModel):
    """What the editable selects may offer (Story 2.3).

    Served rather than hardcoded in the SPA so the vocabulary has one source
    (AD-1) — the same eleven keys the edit command validates against and the
    same set Story 2.4's diagram addresses. A select built from this cannot
    offer a value the server would refuse with a 422.
    """

    body_parts: list[BodyPartOptionResponse]
    recovery_windows: list[RecoveryWindow]
    disabilities: list[Disability]


class IntakeOverviewResponse(ApiModel):
    """The intake variant: summary, reported injury, checklist, full timeline."""

    stage_variant: Literal["intake"]
    employee_business_id: str
    worker_name: str
    worker_role: str
    plant: str
    doi: date
    froi_date: date
    assign_date: date
    handler_name: str
    comm_status: CommStatus
    injury_type: str
    cause: str
    body_part: str
    severity_score: int
    risk: RiskBand
    aww_cents: int
    reserve_cents: int
    checklist: list[ChecklistRowResponse]
    timeline: list[TimelineEntryResponse]


class InvestigationOverviewResponse(ApiModel):
    """The investigation variant: injury card and financials/reserve card.

    The injury card's six fields became **editable in Story 2.3**, behind
    `PATCH /claims/{claimBusinessId}`. They are sent exactly as stored — the
    client edits what it was shown and sends the enclosing `version` back as
    `expectedVersion`. The financials card stays read-only: reserves and the
    severity score belong to Epic 3 and Story 2.4.
    """

    stage_variant: Literal["investigation"]
    injury_type: str
    cause: str
    body_part: str
    icd: str
    # Editable, and editable *only* with `icd`: the code and its description
    # are one fact, so the command refuses one without the other.
    icd_desc: str
    disability: Disability
    recovery: RecoveryWindow
    aww_cents: int
    total_paid_cents: int
    reserve_cents: int
    policy_num: str
    fraud_score: int
    severity_score: int
    risk: RiskBand
    paid_indemnity_cents: int
    paid_medical_cents: int
    cost_split: CostSplitResponse | None
    timeline: list[TimelineEntryResponse]


class TreatmentOverviewResponse(ApiModel):
    """The treatment variant: phase banner, paid-vs-reserve, coordination.

    `phase` and `coordinationStatus` are registered derivations (AC 5) and
    both notes are server-provided (AD-1). The short display labels are the
    UI's, like every other snake_case enum in this contract.

    The figures come from the claim's own paid/reserve columns. Story 3.3's
    bill and payment-schedule tables will be a better source for the same
    numbers — they agree by construction because both read the same columns
    through the same derivations.

    **The reserve-check verdict is not on this block**, although the card that
    renders it is this variant's. Story 3.2 publishes it as `reserveCheck` on
    the case file beside `benefit`, because Story 3.3's Bills summary renders
    the same judgement and one field under one query key is what makes the two
    surfaces identical structurally rather than by convention.
    """

    stage_variant: Literal["treatment"]
    phase: TreatmentPhase
    phase_note: str
    expected_days: int
    days_open: int
    recovery: RecoveryWindow
    paid_medical_cents: int
    paid_indemnity_cents: int
    reserve_cents: int
    coordination_status: CoordinationStatus
    coordination_note: str
    return_status: ReturnStatus
    comm_status: CommStatus
    handler_name: str
    timeline: list[TimelineEntryResponse]
    timeline_truncated: bool


class SettledOverviewResponse(ApiModel):
    """The settled variant: banner, payout breakdown, outcome, action summary.

    `settlementDate` is null for every seeded claim — the prototype's
    settlement events carry `Closed` where a date belongs — so the banner
    omits the clause rather than printing "settled on Closed".
    """

    stage_variant: Literal["settled"]
    settlement_date: date | None
    total_paid_cents: int
    paid_indemnity_cents: int
    paid_medical_cents: int
    paid_expense_cents: int
    reserve_cents: int
    cost_split: CostSplitResponse | None
    disability: Disability
    return_status: ReturnStatus
    days_to_settlement: int
    litigation_flag: bool
    handler_name: str
    timeline: list[TimelineEntryResponse]


StageOverviewResponse = Annotated[
    IntakeOverviewResponse
    | InvestigationOverviewResponse
    | TreatmentOverviewResponse
    | SettledOverviewResponse,
    Field(discriminator="stage_variant"),
]


class InjuryMarkerResponse(ApiModel):
    """One marker on the body diagram (Story 2.4, UX-DR6).

    `band` is the marker's severity band, computed by the same registered
    `risk` derivation the gauge and the queue dot read (AD-10). The SVG
    therefore picks a token from a key and decides nothing — the prototype's
    `injHTML` re-bands each marker at 70/40 inside the drawing function,
    which is a second banding rule and the thing AD-10 exists to prevent.

    `id` and `version` are null on the primary marker: it *is* the claim's
    own `bodyKey`/`severityScore`, so there is no `additional_injury` row to
    address, nothing to remove, and the claim's version governs it.
    """

    id: int | None
    version: int | None
    body_key: str
    body_part: str
    injury_type: str
    severity_score: int
    band: RiskBand
    primary: bool


class PrognosisResponse(ApiModel):
    """MMI estimate, RTW outlook, impairment and litigation risk.

    Persisted by migration 0015. Story 2.2 omitted the MMI row from the
    treatment card because nothing stored it and "a row that always reads
    '—' is furniture, not honesty"; it stores it now.
    """

    mmi: str
    rtw: str
    impairment: str
    litigation: str


class TreatmentPlanStepResponse(ApiModel):
    """One numbered step of the treatment plan."""

    step_no: int
    description: str


class InjuryDiagramResponse(ApiModel):
    """The Injury Diagram tab's whole payload (Story 2.4, AC 1).

    On the case file rather than on a stage variant: the tab is readable at
    every stage, and a claim does not stop having injuries when it settles.

    `defaultSeverityScore` is the score the add form starts at and comes from
    the `injury_capture` rule document (AD-8) — served rather than hardcoded
    in the SPA so that retuning it is a rule change and not a deploy.
    `captureVersion` names the document that answered, for
    `thresholdsVersion`'s reason.
    """

    markers: list[InjuryMarkerResponse]
    icd: str
    icd_desc: str
    prognosis: PrognosisResponse
    treatment_plan: list[TreatmentPlanStepResponse]
    contraindications: str
    default_severity_score: int
    capture_version: int
    # `severityScore`'s domain. Served for `editOptions`' reason: an input
    # bounded by what the server accepts cannot offer a value the command
    # refuses, and the browser keeps no copy of a numeric rule.
    severity_min: int
    severity_max: int


class RequiredFormResponse(ApiModel):
    """One statutory form the claim's classified path requires (Story 2.5).

    `downloadUrl` points at an **external blank**, not at `BlobStore` content —
    the two are different kinds of thing and the client opens them differently
    (a new tab versus the viewer). The seeded URLs are NY WCB / FNSB
    placeholders pending the NFR-4 per-jurisdiction validation that is an
    explicit Deferred decision; they are not labelled provisional in the UI,
    because that would be a product claim this story has no basis for.
    """

    form_code: str
    form_name: str
    description: str
    timing: str
    download_url: str


class EmployeeIdCardResponse(ApiModel):
    """The branded ID card (FR-DET-4).

    Published as a block of its own even though every field appears elsewhere
    on the case file: `employeeBusinessId` and `plant` live on the *intake*
    variant, and a client stitching this card out of the header plus a variant
    would render a different card depending on the claim's stage.
    """

    employee_business_id: str
    worker_name: str
    worker_role: str
    policy_num: str
    doi: date
    handler_name: str
    plant: str
    state: str
    region: str


class DocumentRowResponse(ApiModel):
    """One row of the claim documents list.

    `filedDate` is nullable for `TimelineEntryResponse`'s reason: 101 seeded
    documents carry a timing note (`Post-surgery`, `Closed`) where a filing
    date belongs. The list leaves the cell empty rather than inventing one.
    """

    id: int
    name: str
    doc_type: DocType
    filed_date: date | None


class DocumentsBlockResponse(ApiModel):
    """The Documents & ID tab's whole payload (Story 2.5, AC 1 and AC 3).

    Outside the stage-variant union, like `injury`: which filings a claim
    requires is a function of its **path**, not of its stage.

    `path` is the registered `claim_path` derivation's answer (AD-10) — the
    thing the prototype never actually computes. `pathVersion` names the rule
    document that classified, for `thresholdsVersion`'s reason: every document
    that decided something in this response is named in it.

    The path's label, icon and banner colour are **not** here. They are UI-owned
    per the Enums convention, exactly as the risk band's colours are — the
    prototype keeps them in a `PATH_META` object next to the form data, and
    porting that would have put two hex values in a database column.
    """

    path: ClaimPath
    required_forms: list[RequiredFormResponse]
    id_card: EmployeeIdCardResponse
    documents: list[DocumentRowResponse]
    path_version: int


class PhotoResponse(ApiModel):
    """One card of the incident-photo grid (Story 2.6, AC 1).

    **`id`, not an array index.** The prototype's `openPhoto(c, i)` addresses a
    photo by its position in the array — a handle whose meaning changes the
    moment anything is filed or removed. This is the row's own surrogate.

    **`hasBlob` and `blobUrl` are two facts, not one.** The first says a file
    exists; the second says this deployment can hand the browser a direct link
    to it. Both are false/null for every seeded photo because the prototype has
    no image files, and the tab renders its placeholder treatment — the designed
    state, not a degradation. They stay separate because a volume-backed store
    answers `url()` with `None` by design (see `services/blobstore`), and a
    client that read a null URL as "no photo" would show the placeholder for a
    whole grid of real photographs.

    The 📷 glyph is **not** here. It is the browser's rendering of "no bytes",
    UI-owned per the Enums convention, exactly as the risk band's colours and
    `PATH_META`'s labels are.
    """

    id: int
    caption: str
    source: str
    has_blob: bool
    blob_url: str | None


class PhotosBlockResponse(ApiModel):
    """The Photos tab's whole payload (Story 2.6, AC 1 and AC 3).

    Outside the stage-variant union, like `injury` and `documents`: a claim
    does not stop having evidence when it settles.

    **`count` is published rather than left to the client.** The prototype
    writes ``Photos (${c.photos.length})`` into the tab bar and maps the same
    array into the grid, which is consistent only because both read one global
    object. Here the tab strip and the grid are different components, so a label
    counting a list it does not hold would be a second answer to "how many
    photos does this claim have" — and the tab label is the half a handler reads
    first. It is `len(photos)` computed in exactly one place
    (`services/claims/photos.py`), which is what makes the two incapable of
    disagreeing rather than merely observed to agree.

    An empty `photos` list with `count: 0` is a real and rendered state (AC 3,
    NFR-3) — unreachable against the dev seed, where every claim carries two to
    four photos, and covered by fixtures for that reason.
    """

    photos: list[PhotoResponse]
    count: int


class BenefitResponse(ApiModel):
    """The statutory weekly indemnity benefit, decided server-side (Story 3.1).

    Outside the stage-variant union, like `injury`, `documents` and `photos`.
    The *card* renders on two variants (the two the prototype puts it on), but
    the figure is a fact about the claim at every stage — see `ClaimDetail`.

    **Every figure is integer cents; both rates are integer basis points.**
    `compRateBp: 6667` is 66.67%, and the unit is the column's, the rule
    document's and the PATCH body's — so a rate never becomes a float anywhere
    between the database and the input a handler types in. The SPA formats it
    through `lib/rate.ts` exactly as it formats cents through `lib/money.ts`.

    **`weeklyCents` is already clamped.** The bounds ride along so the card can
    state them (the prototype's "min/max" row), not so a client can apply them:
    the clamp happened in `services/financials`, which is the only place that
    knows the AWW.

    **`isOverridden` is published rather than inferred** from
    `compRateBp != defaultCompRateBp` — a handler who types the default back in
    has still overridden the claim, and the ↺ control has to appear for them.

    **`reserveRationale` is a finished paragraph, not a template.** Written by
    `services/financials/rationale.py` from the claim's own columns — a
    deterministic service output, not an LLM narrative and not an `ai_insight`
    row (AD-2). It is the one string in this contract that carries formatted
    money, because it is prose rather than a figure; the reasoning is in that
    module's docstring.

    **`scheduleEffectiveDate` is the provenance line.** The prototype closes
    the card with "Illustrative figures for prototype purposes — verify against
    the current WC board benefit schedule"; until per-jurisdiction statutory
    data is validated (NFR-4, Deferred), the equivalent honest note is the date
    the schedule on file took effect, which is a fact the table holds rather
    than a disclaimer hardcoded in a component.
    """

    weekly_cents: int
    comp_rate_bp: int
    default_comp_rate_bp: int
    is_overridden: bool
    indemnity_type: IndemnityType
    state_code: str
    state_name: str
    state_min_cents: int
    state_max_cents: int
    schedule_effective_date: date
    waiting_days: int
    reserve_rationale: str
    # The override's domain, served for the reason `injury.severityMin/Max`
    # are: a number input and a pre-flight refusal both need it, and two
    # literals in a React component is the shape `noDerivation.test.ts`
    # refuses. They are what `PATCH /claims/{id}/comp-rate` enforces.
    comp_rate_min_bp: int
    comp_rate_max_bp: int
    # The `benefit_params` document that answered, for `thresholdsVersion`'s
    # reason: every rule that decided something in this response is named in it.
    params_version: int


class ReserveCheckResponse(ApiModel):
    """The reserve adequacy verdict, decided server-side (Story 3.2).

    Outside the stage-variant union, like `benefit` — see `ClaimDetail`. The
    *chip* renders on the treatment variant; the judgement is a fact about the
    claim at every stage, and Story 3.3's Bills financial summary renders this
    same field rather than judging a ratio of its own.

    **`verdict` is the whole answer, and the SPA does no arithmetic to get it**
    (AD-1/AD-9). No client compares `projectedRemainingCents` with
    `reserveCents`: the comparison is `services/financials`', the strictness of
    its boundaries is the rule, and a browser repeating it would be the second
    computation AD-10 exists to prevent.

    **Every money figure is integer cents.** The three are published rather
    than only their total because a handler asking *why* a claim is light is
    answered by which half of the exposure is large — the indemnity still
    scheduled, or the bills not yet paid.

    **`ratioBp` is reported, not decided by.** 11 500 is 115% of the reserve.
    The verdict comes from an exact integer comparison in the service (see
    `services/financials/reserve.py`), not from this rounded figure.

    **Three fields are nullable and each `null` means one thing.**
    `remainingMedicalCents` is `null` when a claim's bills are not on file at
    all — different from a claim that has none, which is `0`. Story 3.3 creates
    the `bill` table, so today it is `null` on every claim, and the verdict says
    so rather than judging a reserve against half its exposure: an unknown term
    is non-negative, so `light` still holds on the partial figure while
    `adequate` and `heavy` are withheld as `indeterminate`.
    `projectedRemainingCents` is `null` whenever the medical term is, because a
    total with an unknown component is not a total — a lower bound published
    under that name would be the same mislabel `disbursedIndemnityCents` exists
    to undo. `ratioBp` is `null` whenever no complete comparison happened: a
    settled claim, or an incomplete exposure. So "there is a ratio" and "a band
    decided this" are one fact, which is what a client can rely on.

    **`scheduledIndemnityCents` and `disbursedIndemnityCents` are what
    `remainingIndemnityCents` is the difference of**, and they are on this block
    rather than the treatment variant so that the card's indemnity-paid row and
    this verdict come from one notion of "paid" (code review, 2026-08-14). They
    are *not* `overview.paidIndemnityCents`, which is `claim.paid_indemnity` —
    a snapshot that reads 0 on every open seeded claim while the schedule
    already shows disbursements, exactly as the prototype's `billsHTML`
    describes. `disbursed_` rather than `paid_` in the name for that reason.

    **`disbursedMedicalCents` is the same field one row up**, added by the
    second review of this story. The card's "Medical paid" row was still
    reading `overview.paidMedicalCents` — `claim.paid_medical`, 0 on all 38
    open seeded claims — directly above the corrected indemnity row and
    directly above a link to a tab that shows the same claim's paid bills as a
    real figure. It is the paid half of the bill list whose unpaid half is
    `remainingMedicalCents`, summed from one read.

    **`rationale` is a finished sentence**, the prototype's, written by the
    service from the claim's own figures — deterministic prose in the same
    category as `benefit.reserveRationale` and not an `ai_insight` row (AD-2).

    **`bandsVersion` names the `reserve_bands` document that answered**, for
    `paramsVersion`'s reason: every rule that decided something in this
    response is named in it.
    """

    verdict: ReserveVerdict
    ratio_bp: int | None
    projected_remaining_cents: int | None
    remaining_indemnity_cents: int
    remaining_medical_cents: int | None
    scheduled_indemnity_cents: int
    disbursed_indemnity_cents: int
    disbursed_medical_cents: int
    reserve_cents: int
    rationale: str
    bands_version: int


class ClaimDetailResponse(ApiModel):
    """The case file: header, stepper, and exactly one stage variant.

    **A discriminated union, not four optional blocks.** The alternative
    shape — `intake?`, `investigation?`, `treatment?`, `settled?` — can
    describe a claim as simultaneously in intake and settled, and leaves the
    client with four truthiness checks where the prototype's `ovHTML` has one
    dispatch. Here the payload carries the block for the claim's stage and
    `stageVariant` says which it is, so a `switch` is exhaustive by type.

    **Money is integer cents and the field names say so.** The `Cents` suffix
    is not decoration: the convention is cents end to end formatted only in
    the UI, and a bare `reserve` reads like dollars at every call site that
    touches it.

    `thresholdsVersion` rides along for the queue payload's reason — the risk
    band in the header and the treatment phase both come from a versioned
    rule document, and "which rules produced this?" should be answerable from
    the response rather than reconstructed.
    """

    claim_id: str
    version: int
    header: CaseHeaderResponse
    stepper: list[StepperStepResponse]
    overview: StageOverviewResponse
    # Story 2.4's injury diagram. Outside the discriminated union on purpose
    # — see `InjuryDiagramResponse`.
    injury: InjuryDiagramResponse
    # Story 2.5's Documents & ID tab, outside it for the same reason — see
    # `DocumentsBlockResponse`.
    documents: DocumentsBlockResponse
    # Story 2.6's Photos tab, outside it for the same reason — and the tab
    # bar reads its `count` at every stage. See `PhotosBlockResponse`.
    photos: PhotosBlockResponse
    # Story 3.1's benefit calculation, outside it for the same reason — see
    # `BenefitResponse`.
    benefit: BenefitResponse
    # Story 3.2's reserve adequacy verdict, outside it for the same reason —
    # and the field Story 3.3's Bills summary reads, which is what makes "the
    # same verdict in both places" a property of the payload. See
    # `ReserveCheckResponse`.
    reserve_check: ReserveCheckResponse
    # Story 2.3's editable vocabularies. On the case file rather than on the
    # investigation variant because the edit command is not stage-scoped —
    # 2.4 edits the body part from the diagram tab at any stage.
    edit_options: EditOptionsResponse
    thresholds_version: int
    # Null for every stage but intake, which is the only variant that reads
    # the `intake_required_documents` document. Sent for `thresholdsVersion`'s
    # reason: the checklist's rows are a rule document's answer, and the
    # queue payload sets the precedent of naming *every* document that
    # decided something in the response.
    requirements_version: int | None


@router.get(
    "/claims/{claim_business_id}",
    response_model=ClaimDetailResponse,
    summary="One claim's case file — header, stepper and stage-adaptive overview",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **NOT_FOUND_RESPONSE,
        **RATE_SCHEDULE_RESPONSE,
    },
)
async def detail(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    claim_business_id: Annotated[
        str,
        Path(
            pattern=CLAIM_ID_PATTERN,
            description="The claim's business id, `WC-nnnn`.",
            examples=["WC-20017"],
        ),
    ],
) -> ClaimDetailResponse:
    """The case file for one claim in the caller's book.

    **404 for out of scope, and that is the security answer rather than a
    convenience.** A 403 would confirm that the claim exists, which turns
    this route into an oracle a caller can walk `WC-20000`…`WC-20999`
    through to enumerate a portfolio they cannot read (AD-7). The repository
    returns nothing for both cases, so there is one branch here and no way to
    write the leak back in.

    The path is the only parameter, and it names a claim rather than a scope:
    "whose claims?" is answered by the session cookie, as it is on
    `/claims/queue` and `/stats/*`.
    """
    # Specific to one persona's book, so never served to another from a cache
    # upstream — the same reason `/me`, `/stats/*` and the queue say so.
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await claim_detail(db, ctx, claim_business_id)
    except ClaimNotVisible as exc:
        raise _not_found(claim_business_id) from exc
    except MissingStateRate as exc:
        raise _missing_state_rate(exc) from exc
    return ClaimDetailResponse.model_validate(result)


def _missing_state_rate(exc: MissingStateRate) -> ProblemException:
    """The 500 for a jurisdiction with no statutory rate schedule (AC 1).

    A problem document rather than a bare 500, because the story is explicit
    that a missing state is a *data error surfaced*, never a default: the
    prototype substitutes ``{max:1200, min:250}`` and shows the claim's real
    state name beside two numbers belonging to no jurisdiction (NFR-4).

    **500 rather than 4xx**, because the caller did nothing wrong and can do
    nothing about it: migration 0023 refuses to complete while any seeded
    claim's state is uncovered, so reaching this means reference data was
    loaded incompletely or a claim was inserted for a jurisdiction nobody has
    rates for. It is the same class of failure as a missing rule document.

    **The state code goes to the log, not to the body** (AD-11, and
    `api/errors.py`'s standing rule that `detail` carries no claim data). An
    operator needs the code and gets it from structlog; the caller needs to
    know the console cannot answer, and gets a sentence that says so.
    """
    log.error("benefit.state_rate_missing", state=exc.state_code)
    return ProblemException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        title="Internal Server Error",
        detail=(
            "This claim's jurisdiction has no statutory rate schedule on file, "
            "so its weekly benefit cannot be calculated."
        ),
        type_="/problems/missing-state-rate",
        headers={"Cache-Control": "no-store"},
    )


def _not_found(claim_business_id: str) -> ProblemException:
    """The 404 both claim routes answer — one wording, one `type`.

    Written once because the sameness *is* the security property: a caller
    comparing the GET's refusal with the PATCH's must not be able to learn
    from the difference that a claim exists but is not theirs to edit.
    """
    return ProblemException(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Not Found",
        detail=f"No claim {claim_business_id} in your caseload.",
        type_="/problems/claim-not-found",
        # Restated because raising abandons the injected `response`: the
        # handler builds a fresh `JSONResponse`. A 404 that depends on who is
        # asking must not be cached by an intermediary and replayed to
        # somebody whose book *does* contain the claim.
        headers={"Cache-Control": "no-store"},
    )


# --- Story 2.3: the audited inline edit ---------------------------------


class ClaimFieldPatch(ApiModel):
    """The PATCH body: a version to compare against, and the edited fields.

    **PATCH-shaped, so every field is optional and omission means "leave
    it"** (AD-4). `model_fields_set` is what separates "not sent" from "sent
    as null" — the second is a 422 rather than a way to blank a column,
    because none of these six fields has a meaningful empty value and a
    handler who clears an input meant to cancel, not to erase the ICD-10
    code.

    **`extra="forbid"` is the whitelist's outer wall.** A key that is not one
    of the seven fails validation here and never reaches the command, which
    is what turns "anything else in the patch → 422" into a property of the
    contract (and of the generated OpenAPI document) rather than a check
    somebody has to remember to write. The command re-checks anyway — it is
    also reachable from an agent tool, which does not come through Pydantic.

    Value-level rules (the ICD-10 shape, the eleven body keys, the five
    recovery windows, the length caps) deliberately stay in
    `services/claims/edit.py`. Declaring them twice would be two places to
    change when the vocabulary moves, and the server-side one is the one that
    is always enforced.
    """

    model_config = ApiModel.model_config | ConfigDict(extra="forbid")

    expected_version: int = Field(
        ge=1,
        description=(
            "The `version` the client read. The write is compare-and-swapped "
            "on it and answers 409 with the fresh entity on a mismatch."
        ),
    )
    injury_type: str | None = None
    cause: str | None = None
    body_key: str | None = None
    icd: str | None = None
    icd_desc: str | None = None
    disability: Disability | None = None
    recovery: RecoveryWindow | None = None

    def edited_fields(self) -> dict[str, object]:
        """The fields the caller actually sent, `null`s included.

        A `None` that arrived explicitly is in `model_fields_set`, so it is
        distinguishable from a field left out — and it is passed **through**
        to the command rather than dropped, because "PATCH ignored one of the
        fields you sent" is the kind of quiet the audit log exists to
        prevent.

        **The refusal itself moved into the command** (code review,
        2026-08-12). It used to happen here, which meant it happened while
        the router was still *building* the call — before the command's role
        check, so a supervisor sending `{"cause": null}` was told their patch
        was malformed rather than that their role cannot edit. That inverts
        the refusal ladder `services/claims/edit.py` documents, and it would
        have been inherited by every Epic 3 command reaching `_answer`.
        Refusing in `normalise` also covers the AD-13 agent tools, which
        never pass through this model at all.
        """
        sent = self.model_fields_set - {"expected_version"}
        return {name: getattr(self, name) for name in sent}


class ConflictProblemDocument(ProblemDocument):
    """The 409 body — a problem document **carrying the fresh entity**.

    The Write-concurrency convention's shape, declared as a model so it
    reaches the OpenAPI document and every Epic 3+ command inherits a
    contract rather than a habit. `claim` is the same `ClaimDetailResponse`
    the GET answers, so a client rendering a conflict runs the code it
    already has for rendering the case file (AD-9: roll back the optimistic
    value, show the returned state inline, no client-side merge).
    """

    claim: ClaimDetailResponse


def _conflict_schema() -> dict[str, Any]:
    """`ConflictProblemDocument`'s schema, pointed at the shared components.

    Pydantic's default `model_json_schema()` inlines every nested model under
    a local `$defs` and refers to them as `#/$defs/…`. That is correct JSON
    Schema and wrong here: the document this lands in is an OpenAPI one, the
    fragment is nested three levels inside a path item, and `#/$defs/…`
    resolves against the *document root* — where there is no `$defs`. The
    generated client's build broke on twenty-five dangling references before
    this function existed.

    So the refs are re-templated at the components section, which already
    holds every one of these models (they are the GET's response), and the
    now-redundant `$defs` block is dropped. `tests/test_problem_json.py`
    asserts the whole document has no unresolvable reference, so the next
    story that attaches an entity to an error cannot reintroduce this
    quietly.
    """
    schema = ConflictProblemDocument.model_json_schema(ref_template="#/components/schemas/{model}")
    schema.pop("$defs", None)
    return schema


CONFLICT_RESPONSE: dict[int | str, dict[str, object]] = {
    409: {
        "description": (
            "The claim has changed since the caller read it. The body is an "
            "RFC 9457 problem document carrying the fresh entity under "
            "`claim` — re-read and redo; nothing is merged server-side."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": _conflict_schema()}},
    }
}

FORBIDDEN_RESPONSE: dict[int | str, dict[str, object]] = {
    403: {
        "description": (
            "The caller's role does not carry the edit capability. Answered "
            "before the claim is looked up, so it says nothing about whether "
            "the claim exists (RFC 9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}


@router.patch(
    "/claims/{claim_business_id}",
    response_model=ClaimDetailResponse,
    summary="Edit a claim's clinical and classification fields (audited, versioned)",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **FORBIDDEN_RESPONSE,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
        **RATE_SCHEDULE_RESPONSE,
    },
)
async def edit_fields(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    patch: ClaimFieldPatch,
    claim_business_id: Annotated[
        str,
        Path(
            pattern=CLAIM_ID_PATTERN,
            description="The claim's business id, `WC-nnnn`.",
            examples=["WC-20017"],
        ),
    ],
) -> ClaimDetailResponse:
    """Apply a whitelisted patch to one claim, and answer with the case file.

    Thin in the way AD-1 and AD-4 both require: this function validates a
    body, calls one service command, and maps four exceptions onto four
    status codes. **It never touches a session for writing** — the command is
    the only write path, which is what makes "every mutation is audited" a
    structural fact rather than a convention (asserted in
    `tests/test_claim_edit_validation.py`).

    The success body is the whole entity, not an acknowledgement: it carries
    the new `version` the next edit will compare against, the timeline with
    this edit's event already in it, and every derived value recomputed
    through the registry (AD-10).
    """
    response.headers["Cache-Control"] = "no-store"
    return await _answer(
        lambda: update_claim_fields(
            db,
            ctx,
            claim_business_id,
            expected_version=patch.expected_version,
            patch=patch.edited_fields(),
        ),
        claim_business_id,
    )


async def _answer(
    call: Callable[[], Awaitable[ClaimDetail]],
    claim_business_id: str,
) -> ClaimDetailResponse:
    """Run one AD-4 command and map its four refusals onto four statuses.

    **Written once because the sameness is the contract.** Four routes now
    reach commands that raise the same four exceptions (Story 2.3's edit,
    2.4's severity, add and remove), and a caller comparing one route's
    refusal with another's must not be able to learn from the difference —
    which is only guaranteed if there is one function producing them. It
    also means Epic 3's commands inherit the mapping rather than copying it.

    The argument is a **callable**, not an awaited coroutine, so that nothing
    is evaluated before the `try` — a body-shaping helper that raised while
    the call was being built would escape this mapping entirely and surface
    as a 500. Nothing does today: the explicit-null refusal that used to live
    in `ClaimFieldPatch.edited_fields()` moved into the command during the
    2026-08-12 code review, precisely so the role check runs before it.
    """
    try:
        result = await call()
    except MissingStateRate as exc:
        # Caught **first**, and the ordering is not arbitrary: a command that
        # succeeded and then failed while re-reading the case file has already
        # committed, so the caller must not be told their patch was invalid.
        # `MissingStateRate` is not a `ValueError` and could not be caught by
        # `InvalidPatch` below in any case — this is here so the next reader
        # does not have to work that out.
        raise _missing_state_rate(exc) from exc
    except EditNotPermitted as exc:
        # Raised before the claim is read, so this answer is identical for a
        # claim in the caller's book, one in somebody else's, and one that
        # does not exist. A supervisor learns nothing from it.
        raise ProblemException(
            status_code=status.HTTP_403_FORBIDDEN,
            title="Forbidden",
            detail=str(exc),
            type_="/problems/edit-not-permitted",
            headers={"Cache-Control": "no-store"},
        ) from exc
    except ClaimNotVisible as exc:
        # **Also the answer for an injury id that is not on the claim**, and
        # the sameness is deliberate. A distinct "no such injury" would tell
        # a caller that the *claim* exists, which is exactly the enumeration
        # oracle `select_claim_detail`'s single-answer rule closes.
        raise _not_found(claim_business_id) from exc
    except InvalidPatch as exc:
        # Not a bare `ValueError`: catching that would turn an unrelated bug
        # deep in the service into a cheerful 422 that blames the caller.
        raise ProblemException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            title="Unprocessable Content",
            detail=str(exc),
            type_="/problems/invalid-patch",
            headers={"Cache-Control": "no-store"},
        ) from exc
    except StaleClaim as exc:
        raise ProblemException(
            status_code=status.HTTP_409_CONFLICT,
            title="Conflict",
            detail=(
                "This claim was changed by someone else while you were "
                "editing. The current values are attached."
            ),
            type_="/problems/stale-write",
            headers={"Cache-Control": "no-store"},
            extensions={
                # `mode="json"` because the extension is merged into a plain
                # dict and handed to `JSONResponse`, which encodes with
                # `json.dumps` and has never heard of `datetime.date` — the
                # response model's own serialiser is what the 200 path gets,
                # and this path has to ask for it explicitly.
                "claim": ClaimDetailResponse.model_validate(exc.fresh).model_dump(
                    by_alias=True, mode="json"
                )
            },
        ) from exc
    return ClaimDetailResponse.model_validate(result)


# --- Story 2.4: the injury diagram's three writes -----------------------


class SeverityPatch(ApiModel):
    """The severity-score PATCH body.

    A route of its own rather than an eighth key on `ClaimFieldPatch`, for
    the reason `services/claims/edit.py` gives at `update_claim_severity`:
    that whitelist and everything built on it are machinery for *text*.

    **The bounds are declared here as well as enforced in the command**, and
    that is a departure from 2.3's "value rules stay in the service". The
    reason is that these two are not a vocabulary that might move: `0..100`
    is `severity_score`'s domain, fixed by the column and by the CHECK
    constraint on `additional_injury`. Declaring it puts the bound in the
    OpenAPI document, so the generated client refuses `101` before it
    becomes a round trip — and the command still refuses it for the agent
    tools that never pass through Pydantic (AD-13).
    """

    model_config = ApiModel.model_config | ConfigDict(extra="forbid")

    expected_version: int = Field(
        ge=1,
        description=(
            "The `version` the client read. The write is compare-and-swapped "
            "on it and answers 409 with the fresh entity on a mismatch."
        ),
    )
    severity_score: int = Field(
        ge=SEVERITY_MIN,
        le=SEVERITY_MAX,
        description=(
            "0-100. Refused, never clamped — the prototype silently turns a "
            "typo of 780 into a maximum-severity claim."
        ),
    )


class NewInjury(ApiModel):
    """The add-injury body: a region, a type, and a score.

    `bodyKey` is a plain string here rather than an enum for the reason 2.3's
    `ClaimFieldPatch.bodyKey` is: the vocabulary is served on the case file
    (`editOptions.bodyParts`) and validated in the command, so a client builds
    its select from the response and cannot offer a value the server refuses.

    There is no `bodyPart` field. The label is the server's answer for the
    key (`BODY_PART_LABELS`), exactly as it is when a handler changes the
    claim's body part — a caller that could supply its own could file "Left
    Hand" against `head`.
    """

    model_config = ApiModel.model_config | ConfigDict(extra="forbid")

    expected_version: int = Field(
        ge=1,
        description=(
            "The **claim's** `version`. The insert is guarded on it, so an "
            "injury cannot be recorded against a case file that has moved on."
        ),
    )
    body_key: str = Field(description="One of `editOptions.bodyParts[].key`.")
    injury_type: str = Field(description="Free text, e.g. `Laceration`.")
    severity_score: int = Field(
        ge=SEVERITY_MIN,
        le=SEVERITY_MAX,
        description="0-100. The add form starts at `injury.defaultSeverityScore`.",
    )


@router.post(
    "/claims/{claim_business_id}/injuries",
    response_model=ClaimDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a secondary injury on a claim (audited, versioned)",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **FORBIDDEN_RESPONSE,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
        **RATE_SCHEDULE_RESPONSE,
    },
)
async def add_injury(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    injury: NewInjury,
    claim_business_id: Annotated[
        str,
        Path(
            pattern=CLAIM_ID_PATTERN,
            description="The claim's business id, `WC-nnnn`.",
            examples=["WC-20017"],
        ),
    ],
) -> ClaimDetailResponse:
    """Add one marker to the body diagram, and answer with the case file.

    **201 with no `Location` header.** A row is created, so 201 is the honest
    status; there is deliberately no `GET /claims/{id}/injuries/{id}` to
    point at, because the injuries are part of the case file and a second way
    to read one would be a second place for the diagram's data to come from.
    The body is the whole case file for `PATCH`'s reason: it carries the new
    marker, the new summary row with its own `id` and `version`, and every
    derived value recomputed through the registry.
    """
    response.headers["Cache-Control"] = "no-store"
    return await _answer(
        lambda: add_additional_injury(
            db,
            ctx,
            claim_business_id,
            expected_version=injury.expected_version,
            body_key=injury.body_key,
            injury_type=injury.injury_type,
            severity_score=injury.severity_score,
        ),
        claim_business_id,
    )


@router.delete(
    "/claims/{claim_business_id}/injuries/{injury_id}",
    response_model=ClaimDetailResponse,
    summary="Remove a secondary injury from a claim (audited, versioned)",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **FORBIDDEN_RESPONSE,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
        **RATE_SCHEDULE_RESPONSE,
    },
)
async def remove_injury(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    claim_business_id: Annotated[
        str,
        Path(
            pattern=CLAIM_ID_PATTERN,
            description="The claim's business id, `WC-nnnn`.",
            examples=["WC-20017"],
        ),
    ],
    injury_id: Annotated[
        int,
        Path(ge=1, description="The `id` of one of `injury.markers[]`.", examples=[42]),
    ],
    expected_version: Annotated[
        int,
        Query(
            # Aliased, because the camelCase boundary is the whole contract's
            # and a query parameter is no less on the wire than a body field.
            # The queue's four parameters are single words, so this is the
            # first place the question comes up.
            alias="expectedVersion",
            ge=1,
            description=(
                "The **injury row's** `version`, not the claim's — the delete "
                "compare-and-swaps on the row it destroys."
            ),
        ),
    ],
) -> ClaimDetailResponse:
    """Remove one secondary injury, and answer with the case file.

    **The version travels in the query string** because a DELETE body is
    permitted but widely dropped by proxies and generated clients, and a
    compare-and-swap whose guard can be silently discarded is not a guard.

    A 200 with the case file rather than a 204: the caller needs the diagram
    without the marker, and every other command on this router answers with
    the entity it changed.
    """
    response.headers["Cache-Control"] = "no-store"
    return await _answer(
        lambda: remove_additional_injury(
            db,
            ctx,
            claim_business_id,
            injury_id,
            expected_version=expected_version,
        ),
        claim_business_id,
    )


@router.patch(
    "/claims/{claim_business_id}/severity",
    response_model=ClaimDetailResponse,
    summary="Set a claim's severity score (audited, versioned)",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **FORBIDDEN_RESPONSE,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
        **RATE_SCHEDULE_RESPONSE,
    },
)
async def edit_severity(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    patch: SeverityPatch,
    claim_business_id: Annotated[
        str,
        Path(
            pattern=CLAIM_ID_PATTERN,
            description="The claim's business id, `WC-nnnn`.",
            examples=["WC-20017"],
        ),
    ],
) -> ClaimDetailResponse:
    """Set the severity score, and answer with the recomputed case file.

    The score is the input to the `risk` band (AD-10), so the response's
    header gauge, the marker colours in `injury`, the queue cards and the top
    bar's High Risk tile all move with it — none of them because this route
    told them to, all of them because they read the same derivation.
    """
    response.headers["Cache-Control"] = "no-store"
    return await _answer(
        lambda: update_claim_severity(
            db,
            ctx,
            claim_business_id,
            expected_version=patch.expected_version,
            severity_score=patch.severity_score,
        ),
        claim_business_id,
    )


# --- Story 3.1: the comp-rate override ----------------------------------


#: The override's unit, with its domain attached, so the bound reaches the
#: OpenAPI document and the generated client refuses `20000` before it becomes
#: a round trip. Declared as an alias rather than inline because `int | None`
#: cannot carry `ge`/`le` directly — the constraint has to sit on the `int`
#: half of the union, not on the nullable whole.
CompRateBasisPoints = Annotated[int, Field(ge=COMP_RATE_MIN_BP, le=COMP_RATE_MAX_BP)]


class CompRatePatch(ApiModel):
    """The comp-rate PATCH body: a version, and a rate or `null`.

    **`compRateBp` is required and nullable, which is the opposite of
    `ClaimFieldPatch`'s members** — and the difference is the story rather than
    an inconsistency. There, an explicit `null` is a caller error: none of the
    six clinical fields has a meaningful empty value, and a handler who cleared
    an input meant to cancel. Here `null` *is* the ↺ reset: it is the value
    that puts the claim back on the statutory default, and the command records
    it as a change like any other.

    A required field, then, rather than an optional one — there is exactly one
    thing this route does and omitting it is not a way to ask for it.

    **The bounds are declared here as well as enforced in the command**, on
    `SeverityPatch`'s argument: `0..15000` basis points is the comp rate's
    domain rather than a vocabulary that might move, so putting it in the
    contract lets the generated client refuse out-of-range input before a
    round trip — and the command still refuses it for the AD-13 agent tools
    that never pass through Pydantic.
    """

    model_config = ApiModel.model_config | ConfigDict(extra="forbid")

    expected_version: int = Field(
        ge=1,
        description=(
            "The `version` the client read. The write is compare-and-swapped "
            "on it and answers 409 with the fresh entity on a mismatch."
        ),
    )
    comp_rate_bp: CompRateBasisPoints | None = Field(
        description=(
            "The comp rate to apply, in **basis points** — 6667 is 66.67% of "
            "AWW. `null` clears the override and restores the statutory "
            "default (the ↺ control). Basis points rather than a percentage "
            "so the value is exact: 66.67 does not round-trip through a float."
        ),
    )


@router.patch(
    "/claims/{claim_business_id}/comp-rate",
    response_model=ClaimDetailResponse,
    summary="Set or clear a claim's comp-rate override (audited, versioned)",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **FORBIDDEN_RESPONSE,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
        **RATE_SCHEDULE_RESPONSE,
    },
)
async def edit_comp_rate(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    patch: CompRatePatch,
    claim_business_id: Annotated[
        str,
        Path(
            pattern=CLAIM_ID_PATTERN,
            description="The claim's business id, `WC-nnnn`.",
            examples=["WC-20017"],
        ),
    ],
) -> ClaimDetailResponse:
    """Override the comp rate — or reset it — and answer with the case file.

    **One route for both, because they are one column.** `compRateBp: null` is
    the reset; a second endpoint would duplicate the refusal ladder to express
    "write NULL" and give one column's history two action names in the audit
    log.

    The response's `benefit` block is recomputed **server-side** from the new
    column (AC 4): the weekly figure, the clamp against the state's bounds and
    the rationale's closing sentence all move because `services/financials` ran
    again, not because this route told the client anything. That is what makes
    the override a server-side recalculation rather than a number the browser
    displays back to itself.
    """
    response.headers["Cache-Control"] = "no-store"
    return await _answer(
        lambda: update_comp_rate_override(
            db,
            ctx,
            claim_business_id,
            expected_version=patch.expected_version,
            comp_rate_bp=patch.comp_rate_bp,
        ),
        claim_business_id,
    )


# --- Story 2.5: the read-only document viewer ---------------------------


class SheetRowResponse(ApiModel):
    """One labelled line of a document sheet.

    **Exactly one of `text` and `cents` is set.** The split keeps the money
    convention intact end to end — a FROI's average weekly wage crosses as
    integer cents and is formatted by the UI, like every other amount in this
    contract, rather than arriving pre-formatted as the one exception.

    Dates ride in `text` as ISO strings, which is what the SPA's `formatDate`
    already renders — including the em dash it renders for `null`, which is the
    right answer for the 101 seeded documents that carry a timing note where a
    filing date belongs.
    """

    label: str
    text: str | None = None
    cents: int | None = None


class DocumentSheetResponse(ApiModel):
    """A document viewer's whole content, assembled server-side (AC 4).

    `sheetVariant` discriminates the two layouts the prototype's `openDoc`
    branches between: a first report of injury renders the full injury detail,
    everything else a short summary. The dispatch is the server's (AD-1) and so
    are the rows, their order and their labels — what fields a statutory filing
    shows is not a layout choice.

    **Read-only, structurally.** There is no PATCH beside this route and no
    `version` on this model: the viewer displays a filing, and editing a claim
    goes through the four commands that already exist.

    `hasBlob` is false and `blobUrl` null for every seeded document, because
    none has bytes behind it (`blob_key` is null on all 563 rows — the
    prototype has no files). Both are on the contract now so that attaching a
    real PDF later changes the store and the ingest path, not this shape.
    """

    document_id: int
    name: str
    doc_type: DocType
    sheet_variant: Literal["froi", "summary"]
    rows: list[SheetRowResponse]
    signatures: list[str]
    has_blob: bool
    blob_url: str | None


@router.get(
    "/claims/{claim_business_id}/documents/{document_id}/content",
    response_model=DocumentSheetResponse,
    summary="One document's read-only viewer sheet",
    responses={**UNAUTHENTICATED_RESPONSE, **NOT_FOUND_RESPONSE},
)
async def document_sheet(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    claim_business_id: Annotated[
        str,
        Path(
            pattern=CLAIM_ID_PATTERN,
            description="The claim's business id, `WC-nnnn`.",
            examples=["WC-20017"],
        ),
    ],
    document_id: Annotated[
        int,
        Path(ge=1, description="The `id` of one of `documents.documents[]`.", examples=[42]),
    ],
) -> DocumentSheetResponse:
    """The sheet for one document of one claim in the caller's book.

    **404 covers three different situations on purpose**: no such document, a
    document belonging to a different claim, and a claim outside the caller's
    scope. Surrogate document ids are dense, so a route that distinguished them
    would let a caller walk `1…10000` and learn how many documents the
    portfolio holds and which ids are live — the enumeration AD-7 closes at the
    claim level, reopened one path segment down. `claim_repo.select_document`
    resolves all three to `None`, so there is one branch here.

    The claim id in the path is load-bearing rather than decorative: it is in
    the predicate, so a document id that *is* in the caller's scope does not
    resolve through a different claim's URL.
    """
    # Specific to one persona's book, so never served to another from a cache
    # upstream — the same reason every other route on this router says so.
    response.headers["Cache-Control"] = "no-store"
    try:
        sheet = await document_content(db, ctx, claim_business_id, document_id)
    except DocumentNotVisible as exc:
        # `_not_found`'s wording, deliberately: the sheet route's refusal must
        # be indistinguishable from the case file's, or the difference is
        # itself the answer to "does this claim exist?".
        raise _not_found(claim_business_id) from exc
    return DocumentSheetResponse.model_validate(sheet)


# --- Bills & Payments: the claim-financials read model (Story 3.3) --------


class ScheduleWeekResponse(ApiModel):
    """One week of the indemnity payment schedule (AC 2).

    **`status` is a snake_case enum and the label is the UI's.** "Due This
    Week" and "Pending Approval" are display strings the browser owns, per the
    Enums convention; what travels is the token a client can branch on.

    **No `id`, and a `version` that arrived with the story that reads it.** A
    week is identified by its claim and its number — the table's unique
    constraint — so a surrogate would be a second identity for one row. Story
    3.3 deferred `version` on the grounds that it would be "a field nothing can
    use"; Story 3.4 is the command that uses it, and the approval
    compare-and-swaps on exactly this number.

    **`approvable` is the server's answer to whether the ✓ button belongs on
    this row** (AD-1). Which statuses can be approved is the rule
    `services/financials/approval.py` refuses on, so a client deriving it from
    `status` would be a second copy of that rule — and the first divergence
    would be a button that 409s.

    `periodStart` and `periodEnd` are **inclusive**, so a week is seven days
    and the table renders "Apr 26 – May 2". An exclusive end would render as
    the next week's start date.
    """

    week_no: int
    period_start: date
    period_end: date
    amount_cents: int
    status: ScheduleWeekStatus
    version: int
    approvable: bool


class BillResponse(ApiModel):
    """One medical bill (AC 3).

    `id` is published — unlike on a schedule week — because a line item has no
    business identifier and no natural key: two rows on one claim can share a
    label, and the label is content a later story may reword. It is what a list
    key and Story 3.4's approval address the row by, exactly as `document` does.
    """

    id: int
    category: BillCategory
    label: str
    amount_cents: int
    status: LineItemStatus
    #: `ScheduleWeekResponse`'s two Story 3.4 fields, for the same reasons.
    #: Note that `approvable` is narrower here: a line item is approvable only
    #: from `under_review`, because `pending_submission` is the *provider's*
    #: state and there is nothing yet to approve.
    version: int
    approvable: bool


class ExpenseResponse(ApiModel):
    """One claim expense (AC 3) — `BillResponse`'s shape over its own vocabulary.

    A separate model rather than a generic one with a union category, because
    the two categories are genuinely different closed sets and a client
    switching on `category` should get exhaustiveness from the type. The
    *status* vocabulary is shared, which is why `LineItemStatus` is one enum.
    """

    id: int
    category: ExpenseCategory
    label: str
    amount_cents: int
    status: LineItemStatus
    version: int
    approvable: bool


class BillGroupResponse(ApiModel):
    """A claim's bills with the three figures their card heading states.

    The totals are the server's (AD-1): the heading reads "6 on file ($4,120 of
    $23,400 paid)", and a browser adding two of them itself is the arithmetic
    the architecture keeps out of components.
    """

    items: list[BillResponse]
    count: int
    total_cents: int
    paid_cents: int


class ExpenseGroupResponse(ApiModel):
    """A claim's expenses and their totals — `BillGroupResponse`'s shape."""

    items: list[ExpenseResponse]
    count: int
    total_cents: int
    paid_cents: int


class FinancialSummaryResponse(ApiModel):
    """The four paycards, the cost bar and the metrics row (AC 1).

    **Every figure here is a registered derivation's answer** (AD-10), and the
    treatment Overview's paid-vs-reserve card reads the same computers over the
    same rows — which is what makes AC 4's "identical figures" a property of the
    code rather than of two components being kept in step.

    **`paidToDateCents` is not the sum of the `paid_*` columns on an open
    claim.** Those are a snapshot and read zero on every open seeded claim
    while the schedule shows elapsed weeks and the bills show payments, so this
    figure falls back to the live sources — the prototype's own rule, which its
    `billsHTML` explains in a comment. `paidFromColumns` says which source
    answered. The fallback is decided once on the total rather than per
    component, so the three `paid*Cents` figures below always come from one
    source and always add to `paidToDateCents`.

    **`costSplit` is null when nothing has been disbursed**, which the card
    renders as "No payments disbursed yet — reserve of $X held against
    projected exposure" rather than as a bar of three zero-width segments.

    **`nextPaymentDue` skips weeks that are awaiting approval or already
    scheduled into a batch.** A claim whose schedule is unapproved has no
    payment *due*, and one already approved is waiting to be paid rather than
    to fall due — so `null` here is an answer, not a missing value.

    `weekCount` is the number of rows in `schedule`, which is not always the
    projection's week count: a shortened schedule keeps any week that was
    already approved or paid, so the table can show more weeks than the claim
    now projects.
    """

    paid_to_date_cents: int
    total_claim_projected_cents: int
    reserve_cents: int
    cost_split: CostSplitResponse | None
    paid_indemnity_cents: int
    paid_medical_cents: int
    paid_expense_cents: int
    paid_from_columns: bool

    weekly_indemnity_cents: int
    installments_paid: int
    week_count: int
    next_payment_due: date | None
    bills_on_file: int

    scheduled_indemnity_cents: int
    disbursed_indemnity_cents: int
    #: When the next payment batch runs (Story 3.4) — the date the summary's
    #: batch note and both approval sheets render. Server-computed from the
    #: configured cadence (`PAYMENT_BATCH_WEEKDAYS`) through a registered
    #: derivation, never "the next Tuesday" worked out in a browser.
    next_batch_date: date


class ClaimFinancialsResponse(ApiModel):
    """The whole Bills & Payments tab, in one payload (AC 1-4).

    **One resource rather than four**, and the reason is consistency rather
    than round trips: the summary's figures are sums over the three lists
    beside it, so a client holding a summary fetched before a schedule refresh
    and a schedule fetched after one would render a totals row that disagreed
    with the rows it totals. One payload under one query key makes that
    unrepresentable.

    **`reserveCheck` is here *and* on the case file, and it is the same
    value.** Both come from one assembler over one set of rows
    (`services/financials/summary.py`), so the treatment Overview card and this
    tab cannot show different verdicts or different figures whenever either was
    fetched — which is what AC 4 asks for. It is repeated rather than referenced
    because a client rendering the Bills tab should not have to have loaded the
    case file first.

    **The schedule is embedded rather than paginated.** The Lists convention
    specifies `{items, nextCursor}` for list endpoints; a claim's schedule is
    clamped to at most twenty weeks by the generator and its line items to a
    handful, so the whole of each is one page and always will be. Documented
    here because the convention is a default, not an exemption anybody should
    have to guess at.
    """

    summary: FinancialSummaryResponse
    schedule: list[ScheduleWeekResponse]
    bills: BillGroupResponse
    expenses: ExpenseGroupResponse
    reserve_check: ReserveCheckResponse


@router.get(
    "/claims/{claim_business_id}/financials",
    response_model=ClaimFinancialsResponse,
    summary="One claim's bills, expenses and week-by-week indemnity schedule",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **NOT_FOUND_RESPONSE,
        **RATE_SCHEDULE_RESPONSE,
    },
)
async def financials(
    ctx: CallerContextDep,
    db: DbDep,
    settings: SettingsDep,
    response: Response,
    claim_business_id: Annotated[
        str,
        Path(
            pattern=CLAIM_ID_PATTERN,
            description="The claim's business id, `WC-nnnn`.",
            examples=["WC-20017"],
        ),
    ],
) -> ClaimFinancialsResponse:
    """The Bills & Payments read model for one claim in the caller's book.

    **404 for out of scope, in the case file's exact wording**, and for its
    reason: a route that distinguished "no such claim" from "not yours" is an
    oracle for enumerating a portfolio the caller cannot read (AD-7). The
    repository answers `None` to both.

    **This GET can write.** Three of the five schedule statuses are the
    calendar's answer, so the read model refreshes the claim's
    `payment_schedule_week` rows before reading them — and so does the case
    file, because AC 4 requires the two surfaces to agree whichever was fetched
    first. The refresh is a no-op on every request that does not cross a week
    boundary: nothing is written, committed or audited when nothing has moved.
    """
    # Specific to one persona's book, so never served to another from a cache
    # upstream — the same reason every other route on this router says so.
    response.headers["Cache-Control"] = "no-store"
    try:
        result: ClaimFinancials = await claim_financial_detail(
            db,
            ctx,
            claim_business_id,
            batch_weekdays=settings.payment_batch_weekday_numbers,
        )
    except ClaimNotVisible as exc:
        raise _not_found(claim_business_id) from exc
    except MissingStateRate as exc:
        raise _missing_state_rate(exc) from exc
    return ClaimFinancialsResponse.model_validate(result)


# --- Story 3.4: approving a payment into the next batch -------------------


class FinancialsConflictProblemDocument(ProblemDocument):
    """The approval's 409 body — a problem document carrying the fresh payload.

    `ConflictProblemDocument`'s shape with a different entity attached, and the
    difference is the story's. A stale *edit* needs the case file back; a stale
    *approval* needs the Bills payload, because the row that moved is one line
    of a table whose totals moved with it. Attaching the whole read model means
    the SPA installs one object and re-renders the tab, which is the same thing
    the 200 path does — no second code path for a conflict (AD-9).

    `paymentStatus` is the extension worth having beside it: "somebody else
    approved this" and "the batch paid it while your sheet was open" are
    different sentences to show a handler, and only the row's fresh status
    tells them apart. It is redundant with the payload — the row is in there —
    but finding it means knowing which of three lists to look in and by what
    key, and a client that got that wrong would render the generic message.
    """

    financials: ClaimFinancialsResponse
    payment_status: str


def _financials_conflict_schema() -> dict[str, Any]:
    """`FinancialsConflictProblemDocument`'s schema, pointed at the components
    section — `_conflict_schema`'s fix, for the same reason and with the same
    consequence if it is skipped (twenty-five dangling `$defs` references and a
    generated client that will not build)."""
    schema = FinancialsConflictProblemDocument.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )
    schema.pop("$defs", None)
    return schema


FINANCIALS_CONFLICT_RESPONSE: dict[int | str, dict[str, object]] = {
    409: {
        "description": (
            "The payment row has moved since the caller read it — approved by "
            "somebody else, or already disbursed by the batch. The body is an "
            "RFC 9457 problem document carrying the fresh Bills & Payments "
            "payload under `financials` and the row's current status under "
            "`paymentStatus`. Re-read and redo; nothing is merged server-side."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": _financials_conflict_schema()}},
    }
}


class PaymentApproval(ApiModel):
    """The approval POST body: what to approve, and the version it was read at.

    **`targetId` means a week *number* for `kind: week` and a row *id*
    otherwise**, which is the two tables' own identities rather than an
    inconsistency: a schedule week is addressed by `(claim, weekNo)` — its
    unique constraint, and why `ScheduleWeekResponse` publishes no `id` — and a
    line item has no natural key at all. Both are published on the payload the
    caller is looking at, so neither has to be constructed.

    **One route for three entities, rather than three routes.** They are one
    decision ("this payment is approved for the next batch"), they take one
    body, they answer with one payload and they share every refusal. Three
    endpoints would be three chances for one of them to drop the status guard —
    and Story 3.5's checklist would then have to know which to call.
    """

    model_config = ApiModel.model_config | ConfigDict(extra="forbid")

    kind: ApprovalKind = Field(
        description=(
            "Which kind of payment row to approve: an indemnity schedule "
            "`week`, a medical `bill`, or a claim `expense`."
        ),
    )
    target_id: int = Field(
        ge=1,
        description=(
            "The week number (`kind: week`) or the line item's `id` "
            "(`kind: bill | expense`) — both published on the financials payload."
        ),
    )
    expected_version: int = Field(
        ge=1,
        description=(
            "The `version` the client read **off the row**, not off the claim. "
            "The write is compare-and-swapped on it and additionally guarded on "
            "the row's current status; either mismatch answers 409 with the "
            "fresh payload."
        ),
    )


@router.post(
    "/claims/{claim_business_id}/payments/approvals",
    response_model=ClaimFinancialsResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve one payment into the next batch (audited, versioned)",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **FORBIDDEN_RESPONSE,
        **NOT_FOUND_RESPONSE,
        **FINANCIALS_CONFLICT_RESPONSE,
        **RATE_SCHEDULE_RESPONSE,
    },
)
async def approve_payment_route(
    ctx: CallerContextDep,
    db: DbDep,
    settings: SettingsDep,
    response: Response,
    body: PaymentApproval,
    claim_business_id: Annotated[
        str,
        Path(
            pattern=CLAIM_ID_PATTERN,
            description="The claim's business id, `WC-nnnn`.",
            examples=["WC-20017"],
        ),
    ],
) -> ClaimFinancialsResponse:
    """Set one payment row to `payment_scheduled`, and answer with the tab.

    **POST rather than PATCH, and a resource rather than a field.** The client
    is not proposing a status — it is requesting a transition, and the server
    decides both the target status and whether the transition is available.
    A `PATCH {status: "payment_scheduled"}` would publish `paid` as something a
    client could ask for, which is exactly the invariant this story exists to
    protect: approval never marks paid.

    Thin in AD-1's sense: this validates a body, calls one worklist command and
    maps four exceptions onto four statuses. The command delegates the write to
    `services/financials`, which AD-12 names as the only writer of these three
    tables — this route reaches none of them.

    The success body is the **whole Bills & Payments payload**, not an
    acknowledgement. An approval moves the row's chip, the schedule's next-due
    date and the sheet's state, so returning the read model is what keeps the
    SPA from deciding which of its figures the approval invalidated (AD-9).
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        result: ApprovalResult = await approve_payment(
            db,
            ctx,
            claim_business_id,
            kind=body.kind,
            target_id=body.target_id,
            expected_version=body.expected_version,
            batch_weekdays=settings.payment_batch_weekday_numbers,
        )
    except MissingStateRate as exc:
        # First, for `_answer`'s reason: a command that succeeded and then
        # failed while re-reading has already committed, so the caller must not
        # be told their request was refused.
        raise _missing_state_rate(exc) from exc
    except ApprovalNotPermitted as exc:
        # Raised before the claim is read, so this answer is identical for a
        # claim in the caller's book, one in somebody else's, and one that does
        # not exist — the ordering `services/claims/edit.py` argues for.
        raise ProblemException(
            status_code=status.HTTP_403_FORBIDDEN,
            title="Forbidden",
            detail=str(exc),
            type_="/problems/approval-not-permitted",
            headers={"Cache-Control": "no-store"},
        ) from exc
    except (ClaimNotVisible, PaymentNotVisible) as exc:
        # **One answer for a claim outside the book and a week that is not on
        # it**, in the case file's exact wording. A distinct "no such week"
        # would confirm that the claim exists, which is the enumeration oracle
        # `select_claim_detail`'s single-answer rule closes.
        raise _not_found(claim_business_id) from exc
    except StalePaymentRow as exc:
        try:
            conflict = await _approval_conflict(db, ctx, settings, claim_business_id, exc)
        except MissingStateRate as rate_exc:
            # `_approval_conflict` re-reads the whole tab, so it can raise
            # everything `GET /financials` can — and it runs *inside* an
            # `except` block, where the handler eleven lines up cannot see it.
            # Left alone, a claim whose state has no rate row answers 500 on
            # the one path that exists to answer 409. Nothing committed on
            # either branch, so the caller loses nothing by hearing the reason
            # the read itself would have given them.
            raise _missing_state_rate(rate_exc) from rate_exc
        raise conflict from exc
    return ClaimFinancialsResponse.model_validate(result.financials)


async def _approval_conflict(
    db: DbDep,
    ctx: CallerContextDep,
    settings: SettingsDep,
    claim_business_id: str,
    exc: StalePaymentRow,
) -> ProblemException:
    """Build the 409, re-reading the payload the caller should render.

    The fresh read happens **here rather than inside the command**, and that is
    deliberate: the command's job ends when its write is refused, and having it
    assemble a whole read model on its failure path would make every caller —
    including Story 3.5's checklist and Epic 6's agent tools — pay for a
    response shape only HTTP needs. `StaleClaim` takes the opposite call
    because its entity is one the command already had in hand.

    Returns rather than raises, so the call site reads `raise await
    _approval_conflict(...)` and mypy keeps its flow analysis
    (`services/claims/edit.py::conflict`'s trick, from the other direction).
    """
    fresh = await claim_financial_detail(
        db,
        ctx,
        claim_business_id,
        batch_weekdays=settings.payment_batch_weekday_numbers,
    )
    return ProblemException(
        status_code=status.HTTP_409_CONFLICT,
        title="Conflict",
        detail=(
            "This payment has already moved on — it was approved or disbursed "
            "while you were looking at it. The current figures are attached."
        ),
        type_="/problems/stale-payment",
        headers={"Cache-Control": "no-store"},
        extensions={
            # `mode="json"` for `_answer`'s reason: the extension is merged
            # into a plain dict and encoded by `json.dumps`, which has never
            # heard of `datetime.date`.
            "financials": ClaimFinancialsResponse.model_validate(fresh).model_dump(
                by_alias=True, mode="json"
            ),
            # The enum's value, not the member — the wire speaks snake_case
            # tokens and the UI owns the label (Enums convention).
            "paymentStatus": str(exc.status.value),
        },
    )


# --- Story 3.5: the auto-generated action checklist and its three writes ---


class ActionResponse(ApiModel):
    """One row of the "⏰ Upcoming actions required" card.

    **Everything on this object was decided by the server**, which is the whole
    of AC 1: the urgency, the ordering, the cap and whether the row is even
    here are `services/worklist/actions.py`'s answers over a rule document's
    parameters. The SPA maps `urgency` to a chip colour and `target` to a
    button label — the enum convention's UI half — and computes nothing.

    `enabled` and `disabledReason` are the cross-epic seam (AC 3). A `false`
    here is not a failure: it says the surface this points at belongs to Epic
    4 or Epic 6, and the sentence beside it names which. Stories 4.2 and 6.2
    flip these by deleting a row from the generator's seam table, with no
    change to the client — which is only true because the client never decides
    what has shipped.

    `command`, `documentId` and `documentVersion` are the completion control
    (AC 5). The version travels for `ScheduleWeekResponse.version`'s reason: a
    control that compare-and-swaps has to be holding the number it will send,
    or a handler's first click 409s against a payload they never saw.
    """

    id: str = Field(
        description=(
            "The row's stable identity, `{key}:{target}` — unique within the "
            "list by construction. Keys a client-side list; not a database id."
        ),
    )
    key: ActionKey
    label: str
    urgency: ActionUrgency
    target: ActionTarget
    enabled: bool
    disabled_reason: str | None = Field(
        description=(
            "Why the go-to control is disabled, naming the epic that enables "
            "it. Null exactly when `enabled` is true."
        ),
    )
    command: ActionCommand | None = Field(
        description=(
            "The audited completion this row offers beside its go-to control, "
            "or null when it offers none. Never set on a disabled row."
        ),
    )
    document_id: int | None = Field(
        description="The document `command` acts on, when it acts on one.",
    )
    document_version: int | None = Field(
        description=(
            "The **document's** `version`, to be sent back as `expectedVersion` — not the claim's."
        ),
    )


class ClaimActionsResponse(ApiModel):
    """The claim's checklist, and the rule document that shaped it.

    `cap` and `paddingFloor` ride along for `thresholdsVersion`'s reason: the
    length of this list is a rule document's answer, and "why are there six of
    these?" should be answerable from the response rather than reconstructed.
    `rulesVersion` names the document that answered, exactly as the queue
    payload names both of its own.

    No `count`: `items` is the list and its length is already the answer —
    publishing a second one would be a number a client could find disagreeing
    with what it is rendering.
    """

    items: list[ActionResponse]
    cap: int
    padding_floor: int
    rules_version: int


@router.get(
    "/claims/{claim_business_id}/actions",
    response_model=ClaimActionsResponse,
    summary="The claim's auto-generated action checklist, ranked and capped",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **NOT_FOUND_RESPONSE,
    },
)
async def actions(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    claim_business_id: Annotated[
        str,
        Path(
            pattern=CLAIM_ID_PATTERN,
            description="The claim's business id, `WC-nnnn`.",
            examples=["WC-20017"],
        ),
    ],
) -> ClaimActionsResponse:
    """What this claim needs next, in the order the server ranked it.

    Thin by AD-1: one service call and a shape. Eleven trigger rules, their
    urgencies, the cap and the padding floor are all decided in
    `services/worklist` over the `worklist_actions` rule document — this route
    does not filter, sort, count or compare anything, and the client must not
    either (`web/src/features/queue/noDerivation.test.ts` holds that half).

    **No `RATE_SCHEDULE_RESPONSE`**, unlike every other case-file route: this
    payload carries no benefit block, so a jurisdiction with no statutory rate
    schedule does not take it down. The checklist is exactly the surface that
    should still answer when a money figure cannot be computed.

    404 for out of scope, in the case file's exact wording and for its reason
    (AD-7).
    """
    # Specific to one persona's book, so never served to another from a cache
    # upstream — the same reason every other route on this router says so.
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await claim_actions(db, ctx, claim_business_id)
    except ClaimNotVisible as exc:
        raise _not_found(claim_business_id) from exc
    return ClaimActionsResponse.model_validate(result)


class SimilarClaimResponse(ApiModel):
    """One neighbouring claim, with the freshness of the vector that matched.

    `embeddedAt` and `stale` are on every item rather than on the envelope
    because they are per-row facts: one neighbour may have been embedded an
    hour ago and another marked stale by an edit thirty seconds ago, and a
    single envelope-level timestamp would have to choose which lie to tell.
    AD-12 requires retrieval to carry freshness, and Story 6.4's similar-case
    action discloses staleness past a configured threshold — from this field.

    `distance` is cosine distance: smaller is nearer, 0 is identical. Published
    raw rather than converted to a "92% similar" score, because that conversion
    is a presentation decision and a percentage invented in a router is a
    figure no service computed (NFR-3).
    """

    claim_id: str
    employer_short_name: str
    injury_type: str
    severity_score: int
    distance: float
    embedded_at: datetime | None
    stale: bool


class SimilarClaimsResponse(ApiModel):
    """The neighbour list, and the bound that produced it.

    `k` is echoed for `ClaimActionsResponse`'s reason — "why are there five of
    these?" should be answerable from the response rather than reconstructed
    from a request nobody kept.

    **It is the requested bound, not the item count**, and the two differ
    whenever the caller's book is smaller than `k`: a handler scoped to one
    employer who asks for 25 gets `k: 25` beside seven items, and that pair is
    the whole point — it says "you asked for 25 and the partition held seven"
    rather than leaving the caller to wonder whether the search stopped early.
    `MAX_K` is enforced by the route as a 422 rather than silently clamped, so
    no value can arrive here that the caller did not choose.

    No `count`: `items` is the list and its length is already the answer.
    """

    items: list[SimilarClaimResponse]
    k: int


@router.get(
    "/claims/{claim_business_id}/similar",
    response_model=SimilarClaimsResponse,
    summary="Claims in the caller's book most similar to this one",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **NOT_FOUND_RESPONSE,
        **MODEL_UNAVAILABLE_RESPONSE,
    },
)
async def similar(
    ctx: CallerContextDep,
    db: DbDep,
    settings: SettingsDep,
    response: Response,
    claim_business_id: Annotated[
        str,
        Path(
            pattern=CLAIM_ID_PATTERN,
            description="The claim's business id, `WC-nnnn`.",
            examples=["WC-20017"],
        ),
    ],
    k: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_K,
            description=(
                "How many neighbours to return. Bounded rather than paged: the "
                "hundredth-nearest claim is not the next page of similar cases, "
                "it is a claim that is not similar."
            ),
        ),
    ] = 5,
) -> SimilarClaimsResponse:
    """Vector similarity over the caller's employer partition (AC 3).

    Thin by AD-1: the route validates one path parameter and one bound, calls
    one service, and maps its result onto the wire. The query text is composed
    and embedded in `services/rag`; the repository is invoked with a vector and
    applies `employer_scope(ctx)` exactly as `GET /claims/queue` does. There is
    no scope-shaped parameter here for `queue`'s reason — "whose claims?" is
    answered by the session cookie and by nothing a caller can send.

    **404 for out of scope and 404 for unknown**, the case file's exact
    wording and its reason: two different answers would make this route an
    oracle for enumerating a portfolio the caller cannot read (AD-7).

    **No `web/` component calls this yet.** AD-15 gates the story on a spec the
    browser can reach and AC 3 asks for the scope guarantee to be proven
    through the service rather than only at the repository, so this is the
    smallest honest surface that satisfies both. Story 6.4's similar-case quick
    action calls the same service function through a registered tool.
    """
    # Specific to one persona's book, so never served to another from a cache
    # upstream — the same reason every other route on this router says so.
    response.headers["Cache-Control"] = "no-store"
    try:
        items = await rag_similar_claims(
            db,
            ctx,
            claim_business_id=claim_business_id,
            k=k,
            client=embedding_client(settings),
        )
    except ClaimNotVisible as exc:
        raise _not_found(claim_business_id) from exc
    except (EmbeddingDimensionMismatch, httpx.HTTPError) as exc:
        # This route is the one place in the build where an interactive read
        # depends on the model server: the query claim is composed and embedded
        # per request (AC 2 puts the embedding in `services/rag`, before the
        # repository). So the outage has to have an answer here, and an
        # unlabelled 500 after a 60-second timeout is not one — it tells the
        # caller nothing and reads as a defect in the claim service rather than
        # as a model server that is down.
        #
        # 503 and not 502: the dependency is internal and the condition is
        # temporary, which is what a client needs to know to decide whether
        # retrying is sensible. Story 6.6 owns *honest degradation* — the
        # disabled inputs, the `ai_unavailable` stream code, the bounded
        # retry — and none of that is built here; this is the status line it
        # will be built on top of. The exception's own text is never echoed:
        # for a transport error it can carry a request body (AD-11).
        raise ProblemException(
            status_code=503,
            title="Model server unavailable",
            detail=(
                "Similar-case search needs the local model server to embed the "
                "query claim, and it did not answer. Claim data is unaffected."
            ),
        ) from exc
    return SimilarClaimsResponse(
        items=[SimilarClaimResponse.model_validate(item) for item in items],
        k=k,
    )


UNPROCESSABLE_RESPONSE: dict[int | str, dict[str, object]] = {
    422: {
        "description": (
            "The request names something this command cannot apply — a claim "
            "whose injury is not OSHA recordable, or a document review step "
            "that is not one of the two (RFC 9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}


class VersionedCommand(ApiModel):
    """The body every checklist completion sends: the version it was read at.

    One model for the assessment approval and the OSHA entry, because they are
    one body — a compare-and-swap and nothing else. The document review adds a
    `step` and so has a model of its own rather than an optional field here: an
    optional discriminator is a body that can be valid and mean nothing.

    `extra="forbid"` for `ClaimFieldPatch`'s reason — an unknown key is a 422
    from the contract rather than a value silently dropped on the way to a
    command.
    """

    model_config = ApiModel.model_config | ConfigDict(extra="forbid")

    expected_version: int = Field(
        ge=1,
        description=(
            "The `version` the client read off the **claim**. The write is "
            "compare-and-swapped on it *and* guarded on the claim's current "
            "state; either mismatch answers 409 with the fresh case file."
        ),
    )


class DocumentReviewCommand(ApiModel):
    """The document review body: which completion, and the document's version.

    **`step` is one of the two document members of `ActionCommand`**, which is
    the same value the checklist published on the row the handler clicked — so
    the control and the command name one thing rather than two that have to be
    kept in agreement. A caller sending `approve_assessment` here gets a 422
    from the enum before the command sees it, and the command refuses it again
    for the AD-13 agent tools that never pass through this model.
    """

    model_config = ApiModel.model_config | ConfigDict(extra="forbid")

    expected_version: int = Field(
        ge=1,
        description=(
            "The **document's** `version`, published on the checklist row as "
            "`documentVersion` — not the claim's."
        ),
    )
    step: ActionCommand = Field(
        description=(
            "`mark_document_reviewed` for a first read, `confirm_document` to "
            "accept one already reviewed. Confirming an unreviewed document "
            "is refused with 409, which is the prototype's own sequencing "
            "enforced server-side."
        ),
    )


@router.post(
    "/claims/{claim_business_id}/assessment/approval",
    response_model=ClaimDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve the claim's assessment (audited, versioned, status-guarded)",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **FORBIDDEN_RESPONSE,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
        **RATE_SCHEDULE_RESPONSE,
    },
)
async def approve_assessment_route(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    body: VersionedCommand,
    claim_business_id: Annotated[
        str,
        Path(
            pattern=CLAIM_ID_PATTERN,
            description="The claim's business id, `WC-nnnn`.",
            examples=["WC-20017"],
        ),
    ],
) -> ClaimDetailResponse:
    """Move a claim from assessment to `ch_approved`, and answer with the file.

    **POST to a resource rather than PATCH of a field**, exactly as the payment
    approval is: the client is not proposing a status, it is requesting a
    transition, and the server decides both the target status and whether the
    transition is available. `PATCH {status: "ch_approved"}` would publish the
    whole status vocabulary as something a caller may ask for — including
    `denied` and `settled_closed`, which no story has given anybody a command
    for.

    The success body is the whole case file, so the header's chip, the stepper
    and the timeline arrive together (AC 4). The SPA invalidates the queue and
    the top-bar counts beside it, because a claim leaving `ch_assessment_process`
    changes a number two panes away that nothing in the browser could compute.
    """
    response.headers["Cache-Control"] = "no-store"
    return await _answer(
        lambda: approve_assessment(
            db, ctx, claim_business_id, expected_version=body.expected_version
        ),
        claim_business_id,
    )


@router.post(
    "/claims/{claim_business_id}/documents/{document_id}/review",
    response_model=ClaimDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Mark a document reviewed, or confirm one already reviewed (audited)",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **FORBIDDEN_RESPONSE,
        **NOT_FOUND_RESPONSE,
        **UNPROCESSABLE_RESPONSE,
        **CONFLICT_RESPONSE,
        **RATE_SCHEDULE_RESPONSE,
    },
)
async def review_document(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    body: DocumentReviewCommand,
    claim_business_id: Annotated[
        str,
        Path(
            pattern=CLAIM_ID_PATTERN,
            description="The claim's business id, `WC-nnnn`.",
            examples=["WC-20017"],
        ),
    ],
    document_id: Annotated[
        int,
        Path(ge=1, description="The document's surrogate id, as the payload publishes it."),
    ],
) -> ClaimDetailResponse:
    """Record one step of a document's review, and answer with the case file.

    **404 for a document that is not on this claim**, in the claim's own
    wording: `select_document` conflates "no such document", "another claim's
    document" and "not your claim" for the reason the claim route conflates its
    own two, and a distinct "no such document" would confirm that the *claim*
    exists (AD-7).
    """
    response.headers["Cache-Control"] = "no-store"
    return await _answer(
        lambda: set_document_review(
            db,
            ctx,
            claim_business_id,
            document_id,
            step=body.step,
            expected_version=body.expected_version,
        ),
        claim_business_id,
    )


@router.post(
    "/claims/{claim_business_id}/osha-log",
    response_model=ClaimDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Record the injury on the OSHA 300 log (audited, versioned)",
    responses={
        **UNAUTHENTICATED_RESPONSE,
        **FORBIDDEN_RESPONSE,
        **NOT_FOUND_RESPONSE,
        **UNPROCESSABLE_RESPONSE,
        **CONFLICT_RESPONSE,
        **RATE_SCHEDULE_RESPONSE,
    },
)
async def mark_osha_logged_route(
    ctx: CallerContextDep,
    db: DbDep,
    response: Response,
    body: VersionedCommand,
    claim_business_id: Annotated[
        str,
        Path(
            pattern=CLAIM_ID_PATTERN,
            description="The claim's business id, `WC-nnnn`.",
            examples=["WC-20017"],
        ),
    ],
) -> ClaimDetailResponse:
    """Set `osha_logged`, and answer with the case file.

    A claim whose injury is not recordable is **422, not 409**: there is
    nothing to re-read and trying again will never work, because recordability
    is a property of the injury rather than a state a claim passes through.
    Every other refusal on this route is one of `_answer`'s four.
    """
    response.headers["Cache-Control"] = "no-store"
    return await _answer(
        lambda: mark_osha_logged(
            db, ctx, claim_business_id, expected_version=body.expected_version
        ),
        claim_business_id,
    )
