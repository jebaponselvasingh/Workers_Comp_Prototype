"""The case file, assembled server-side (Story 2.2, FR-DET-1).

Everything the detail pane shows is decided here (AD-1). The SPA receives a
finished header, a finished stepper and exactly one finished stage-variant
block; it holds no phase rule, no coordination rule, no checklist logic, no
percentage arithmetic and no "which six events" slice. That is the prototype
behaviour this story replaces — `renderDet` computed all of it in the browser
from a global claim array.

**One block, not four.** `ovHTML` dispatches on `c.stage` and builds one of
four bodies; so does this. The response carries the block for the claim's
stage and nothing else, so a payload cannot describe a claim as
simultaneously in intake and settled, and the client's render has one branch
rather than four truthiness checks.

**Money is integer cents, and every field says so.** Wire names carry a
`_cents` suffix. The convention is "integer cents end to end, formatted only
in the UI", and a suffix is what makes the unit un-missable at a call site —
the alternative is a `reserve` that reads like dollars in every component
that touches it.

**Two labels this story deliberately does not send.** The prototype's
treatment card shows a supervisor and an MMI estimate; neither is persisted
(Story 1.2 deliberately did not seed `supervisor`, and `prognosis` is Story
2.4's). A row that always reads "—" is not honesty, it is furniture, so both
are omitted rather than rendered empty — recorded in the story's Dev Agent
Record so the omission is a decision rather than an oversight.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models.core import Claim, Document
from data.models.enums import (
    CommStatus,
    Disability,
    DocType,
    RecoveryWindow,
    ReturnStatus,
    Stage,
    TimelineTag,
)
from data.repositories import claims as claim_repo
from rules.parameters import (
    DerivationThresholds,
    injury_capture_for,
    intake_requirements_for,
    thresholds_for,
)
from services import derivations
from services.claims.documents import DocumentsBlock, documents_block
from services.claims.photos import PhotosBlock, photos_block
from services.claims.reference import (
    BODY_PART_OPTIONS,
    RECOVERY_WINDOWS,
    SEVERITY_MAX,
    SEVERITY_MIN,
    BodyPartOption,
)
from services.derivations import (
    CoordinationStatus,
    CostSplit,
    RiskBand,
    TreatmentPhase,
    utc_today,
)
from services.financials import (
    Benefit,
    ClaimFinancials,
    ReserveCheck,
    benefit_for_claim,
    claim_financials,
    reserve_check_for_claim,
)

# The lifecycle, left to right — the prototype's `stageStepperHTML` order and
# the queue's `STAGE_ORDER`. One tuple, because a stepper that disagreed with
# the queue's grouping about what comes after what would be two lifecycles.
STAGE_STEPS: tuple[Stage, ...] = (Stage.intake, Stage.investigation, Stage.treatment, Stage.settled)

# The prototype's treatment overview renders `timeline.slice(-6)`. Marked
# here rather than in the SPA (AD-1): "which six" is a decision about the
# data, and a client that sliced would be free to slice differently.
RECENT_TIMELINE_COUNT = 6


class ClaimNotVisible(LookupError):
    """No claim with that business id is in the caller's scope.

    Deliberately one exception for "does not exist" and "belongs to someone
    else" — see `claim_repo.select_claim_detail` for why telling them apart
    would be an enumeration oracle.
    """


@dataclass(frozen=True)
class TimelineEntry:
    """One line of the case timeline.

    `event_date` is optional because 62 seeded settlement events have none —
    the prototype writes the literal `Closed` where a date belongs. The UI
    renders an empty date cell; it does not invent one.
    """

    event_date: date | None
    description: str
    tag: str


@dataclass(frozen=True)
class ChecklistRow:
    """One required intake document, and whether it is on file."""

    doc_type: DocType
    received: bool


@dataclass(frozen=True)
class StepperStep:
    stage: Stage
    done: bool
    current: bool


@dataclass(frozen=True)
class EditOptions:
    """The vocabularies an editable field may be set to (Story 2.3).

    Sent rather than hardcoded in the SPA, and that is AD-1 rather than
    convenience: the eleven body keys are the same set Story 2.4's diagram
    addresses its regions by and the same set the edit command validates
    against, so a copy in TypeScript is a third place for them to disagree.
    Serving them also makes a select structurally incapable of offering a
    value the server would refuse.

    Published on the case file rather than on the investigation variant
    because the command is not stage-scoped — 2.4 edits the body part from
    the diagram tab, whatever stage the claim is in.
    """

    body_parts: tuple[BodyPartOption, ...]
    recovery_windows: tuple[RecoveryWindow, ...]
    disabilities: tuple[Disability, ...]


# A constant, not a query: these are code-level vocabularies (see
# `reference.py` on why they are not JDM parameters), so the case file
# carries them without a round trip and without a per-request rebuild.
EDIT_OPTIONS = EditOptions(
    body_parts=BODY_PART_OPTIONS,
    recovery_windows=RECOVERY_WINDOWS,
    disabilities=tuple(Disability),
)


@dataclass(frozen=True)
class InjuryMarker:
    """One marker on the body diagram (Story 2.4, UX-DR6).

    **The band is on the marker, and the SVG is therefore data-only.** The
    prototype re-derives a colour per marker from the raw score
    (`m.sevScore>=70?er:...`), which is a second banding rule living in a
    drawing function; here every marker arrives already banded by the one
    registered `risk` derivation (AD-10), so the component chooses a token
    from a key and decides nothing.

    `id` and `version` are `None` on the primary marker and set on every
    secondary, and the asymmetry is the fact rather than a shape compromise:
    the primary injury *is* the claim's own `body_key`/`severity_score`, so
    it has no `additional_injury` row to address, cannot be removed, and is
    versioned by the claim. `primary` is published beside them rather than
    left to be inferred from `id === null`, because "which marker pulses" is
    a statement about the injury and not about the payload's shape.
    """

    id: int | None
    version: int | None
    body_key: str
    body_part: str
    injury_type: str
    severity_score: int
    band: RiskBand
    primary: bool


@dataclass(frozen=True)
class Prognosis:
    """The four clinical outlooks the prognosis card shows.

    Story 2.2 omitted the MMI estimate from the treatment card because
    nothing persisted it — "a row that always reads '—' is furniture, not
    honesty". Migration 0015 persists all four, so the row can be rendered.
    """

    mmi: str
    rtw: str
    impairment: str
    litigation: str


@dataclass(frozen=True)
class TreatmentPlanEntry:
    """One numbered step of the treatment plan."""

    step_no: int
    description: str


@dataclass(frozen=True)
class InjuryDiagram:
    """Everything the Injury Diagram tab draws (Story 2.4, AC 1).

    A block of its own rather than fields spread across the header and the
    stage variant, for the reason the variants are a discriminated union:
    the tab is one surface with one payload, and it is available at *every*
    stage — a settled claim's diagram is as readable as an intake claim's.
    Putting it on a variant would have meant four copies or a tab that
    disappeared when a claim settled.

    `default_severity_score` and `capture_version` come from the
    `injury_capture` rule document (AD-8): the add form's starting score is
    tuning, and `capture_version` names the document that answered, for
    `thresholds_version`'s reason — every rule that decided something in this
    response is named in it.
    """

    markers: tuple[InjuryMarker, ...]
    icd: str
    icd_desc: str
    prognosis: Prognosis
    treatment_plan: tuple[TreatmentPlanEntry, ...]
    contraindications: str
    default_severity_score: int
    capture_version: int
    # `severity_score`'s domain, served rather than restated in the SPA — a
    # number input and a pre-flight refusal both need it, and two literals
    # in a React component is the shape `noDerivation.test.ts` refuses (and
    # did refuse, on the first draft of the severity card).
    severity_min: int
    severity_max: int


@dataclass(frozen=True)
class CaseHeader:
    """Everything above the tab bar (UX-DR4).

    `risk` is the band, not the score — the same `risk` derivation the queue
    dot reads (AD-10), which is what makes "the gauge and the card can never
    disagree" a property of the system rather than a hope.

    `body_key` rides beside `body_part` because the two are different things
    (see `services/claims/reference.py`): the label is what a human reads,
    the key is what the body diagram and the edit select address. Neither is
    recoverable from the other — the seeded vocabularies do not overlap.
    """

    claim_id: str
    worker_name: str
    worker_role: str
    employer_name: str
    state: str
    injury_type: str
    body_part: str
    body_key: str
    cause: str
    icd: str
    severity_score: int
    risk: RiskBand
    stage: Stage
    fraud_flag: bool
    fraud_score: int
    litigation_flag: bool
    surgery_required: bool
    osha_recordable: bool


@dataclass(frozen=True)
class IntakeOverview:
    """Intake summary, reported injury, the document checklist, the timeline."""

    stage_variant: str
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
    checklist: tuple[ChecklistRow, ...]
    timeline: tuple[TimelineEntry, ...]


@dataclass(frozen=True)
class InvestigationOverview:
    """The injury card — editable since Story 2.3 — and the financials card.

    Seven of these fields are what a handler edits inline; the rest of the
    block is the read-only financial summary beside it. The editable ones are
    sent exactly as stored, because the PATCH that changes them compares
    against the `version` on the enclosing `ClaimDetail` and the client has
    to be able to send back what it was shown.
    """

    stage_variant: str
    injury_type: str
    cause: str
    body_part: str
    icd: str
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
    # `None` when nothing has been paid: the prototype draws the bar only
    # then, and a zero-width three-segment bar is a different, worse way of
    # saying "no payments yet" than the sentence beside it.
    cost_split: CostSplit | None
    timeline: tuple[TimelineEntry, ...]


@dataclass(frozen=True)
class TreatmentOverview:
    """Phase banner, paid-vs-reserve, care & RTW coordination, recent timeline."""

    stage_variant: str
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
    timeline: tuple[TimelineEntry, ...]
    # True when the timeline above is the recent slice rather than the whole
    # log. The count is the server's decision; the flag is what lets the card
    # say "recent" honestly instead of implying it is the full history.
    timeline_truncated: bool


@dataclass(frozen=True)
class SettledOverview:
    """Settled banner, final payout breakdown, outcome, full action summary."""

    stage_variant: str
    # `None` for every seeded claim: the prototype's settlement events carry
    # the literal string `Closed` where a date belongs, so there is no date to
    # report and the banner omits the clause rather than printing a non-date.
    settlement_date: date | None
    total_paid_cents: int
    paid_indemnity_cents: int
    paid_medical_cents: int
    paid_expense_cents: int
    reserve_cents: int
    cost_split: CostSplit | None
    disability: Disability
    return_status: ReturnStatus
    days_to_settlement: int
    litigation_flag: bool
    handler_name: str
    timeline: tuple[TimelineEntry, ...]


StageOverview = IntakeOverview | InvestigationOverview | TreatmentOverview | SettledOverview


@dataclass(frozen=True)
class ClaimDetail:
    """The whole case file: header, stepper, and one stage variant.

    `version` is the claim row's compare-and-swap column. Nothing in this
    story writes, so nothing uses it yet — it is published now because Story
    2.3's inline edits send it back as `expected_version`, and an endpoint
    that made a client fetch the entity twice to edit it once would be the
    thing AD-4 is trying to avoid.
    """

    claim_id: str
    version: int
    header: CaseHeader
    stepper: tuple[StepperStep, ...]
    overview: StageOverview
    # Story 2.4's tab. Not on a stage variant: the diagram is readable at
    # every stage, and a claim does not stop having injuries when it settles.
    injury: InjuryDiagram
    # Story 2.5's tab, outside the union for the same reason — the statutory
    # forms a claim requires are a function of its *path*, not of its stage,
    # and an intake claim's ID card is as readable as a settled one's.
    documents: DocumentsBlock
    # Story 2.6's tab, outside the union for `injury`'s and `documents`'
    # reason: a claim does not stop having evidence when it settles, and the
    # tab bar reads this block's `count` at every stage.
    photos: PhotosBlock
    # Story 3.1's benefit calculation, outside the union for `injury`'s and
    # `documents`' reason — with one extra. The *card* renders on two of the
    # four stage variants (the two the prototype puts it on), but the figure is
    # meaningful at every stage: a settled claim was paid a weekly indemnity,
    # and an intake claim already has an AWW, a state and a disability. Putting
    # the block on the variants would mean two copies of one field list and a
    # payload whose shape changed under a claim as it progressed — and Story
    # 3.3's payment schedule, which is a Bills-tab surface, needs the same
    # figure at whatever stage the claim is in.
    benefit: Benefit
    # Story 3.2's reserve adequacy verdict, outside the union for `benefit`'s
    # reason and with the same extra: the *chip* renders on the treatment
    # variant (the one the prototype puts it on), but the judgement is a fact
    # about the claim at every stage — a settled claim's verdict is
    # `closed_final`, which is an answer rather than an absence.
    #
    # **On the case file rather than on the treatment block, and that is what
    # makes AC 3 structural.** Story 3.3's Bills financial summary renders the
    # same verdict; sharing one field under one query key
    # (`queryKeys.claims.detail`) is what makes "identical in both surfaces" a
    # property of the payload rather than a convention two components are
    # trusted to keep. A copy on the treatment variant would let 3.3 read the
    # other one and nothing anywhere would say they had drifted.
    reserve_check: ReserveCheck
    edit_options: EditOptions
    thresholds_version: int
    # `None` for every stage but intake, because no other variant reads the
    # requirements document. Published for `thresholds_version`'s reason:
    # the checklist's rows are decided by a versioned rule document, and
    # "which rules produced this?" is only answerable if the response names
    # every document that answered — the queue publishes both of its own.
    requirements_version: int | None


def _stepper(stage: Stage) -> tuple[StepperStep, ...]:
    """Done for every step before the claim's, current for its own.

    Sent rather than computed in the SPA for AD-1's reason, and it is not
    pedantry: "which steps are done" is a statement about the claim's
    lifecycle position, and the browser deciding it would be a second place
    that knows what follows investigation.
    """
    position = STAGE_STEPS.index(stage)
    return tuple(
        StepperStep(stage=step, done=index < position, current=index == position)
        for index, step in enumerate(STAGE_STEPS)
    )


def _timeline(rows: list[TimelineEntry], recent_only: bool) -> tuple[TimelineEntry, ...]:
    return tuple(rows[-RECENT_TIMELINE_COUNT:] if recent_only else rows)


def _checklist(
    required: tuple[DocType, ...], on_file: frozenset[DocType]
) -> tuple[ChecklistRow, ...]:
    """Received/Missing per required type, in the rule document's order.

    The comparison is by *type*, not by name: two wage statements are one
    requirement met, and a document nobody required does not appear at all.
    """
    return tuple(
        ChecklistRow(doc_type=doc_type, received=doc_type in on_file) for doc_type in required
    )


def _settlement_date(events: list[TimelineEntry]) -> date | None:
    """The latest *dated* settlement-tagged event, or `None`.

    The prototype searches backwards for a tag *or description* matching
    `/settl|clos/i` — a substring scan over free text, which also matches
    "claim closed", "closure planning" and anything else containing "clos".
    Here the tag is a token, so the match is an equality against
    `TimelineTag.settlement` and cannot drift with an edit to a description.

    **Latest by date, not by append position** (code review, 2026-08-12).
    Two separate problems with reading the last settlement row and taking
    whatever date it holds:

    - Append order is not chronological. Eight seeded claims already carry
      dated events that run backwards in append order, so "the last one
      appended" is not "the most recent" on a claim that was reopened and
      resettled.
    - An undated later row would discard a known earlier date, leaving the
      banner without a clause it could have filled.

    Both are masked today because all 62 seeded settlement events are
    undated, which is exactly why they were worth fixing before a real
    settlement date lands in the data and makes the answer visible.
    """
    dated = [
        entry.event_date
        for entry in events
        if entry.tag == TimelineTag.settlement and entry.event_date is not None
    ]
    return max(dated) if dated else None


async def claim_detail(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    *,
    as_of: date | None = None,
) -> ClaimDetail:
    """The case file for one claim, or `ClaimNotVisible`.

    One rule-document load, one claim read, two child reads. The derivations
    are built once from the loaded thresholds and called — the shape the
    registry exists for.
    """
    today = as_of or utc_today()
    row = await claim_repo.select_claim_detail(db, ctx, claim_business_id)
    if row is None:
        raise ClaimNotVisible(claim_business_id)

    thresholds = await thresholds_for(db, today)
    events = [
        TimelineEntry(event_date=event.event_date, description=event.description, tag=event.tag)
        for event in await claim_repo.select_timeline_events(db, ctx, claim_business_id)
    ]

    claim = row.Claim
    risk = derivations.risk.for_thresholds(thresholds).of(claim.severity_score)
    header = CaseHeader(
        claim_id=claim.claim_id,
        worker_name=row.worker_name,
        worker_role=row.worker_role,
        employer_name=row.employer_name,
        state=claim.state,
        injury_type=claim.injury_type,
        body_part=claim.body_part,
        body_key=claim.body_key,
        cause=claim.cause,
        icd=claim.icd,
        severity_score=claim.severity_score,
        risk=risk,
        stage=claim.stage,
        fraud_flag=claim.fraud_flag,
        fraud_score=claim.fraud_score,
        litigation_flag=claim.litigation_flag,
        surgery_required=claim.surgery_required,
        osha_recordable=claim.osha_recordable,
    )

    # **Read once, used twice** (code review, 2026-08-12). Story 2.5's block
    # needs every document; the *intake* variant's checklist needs the set of
    # types on file. Left to fetch their own, an intake claim ran the identical
    # scoped query twice on every case-file read — and the case file is
    # re-read after each of the four commands and embedded in every 409 body,
    # so it is the most-fetched payload in the console. Hoisted here rather
    # than cached inside either consumer, because a read that two blocks share
    # belongs to the function that assembles both.
    documents = await claim_repo.select_documents(db, ctx, claim.claim_id)

    overview, requirements_version = await _overview(
        db, row, risk, thresholds, events, documents, today
    )

    # Computed once and handed to the reserve check rather than fetched twice.
    # `benefit_for_claim` costs a `state_rate_schedule` lookup and a
    # `benefit_params` load, and the reserve check needs the identical weekly
    # figure to project the schedule the exposure is measured from — asking
    # again would be two round trips to arrive at one number, and AD-2's "one
    # computation" is easier to keep true when there is one call site.
    benefit = await benefit_for_claim(db, claim, thresholds, today)

    return ClaimDetail(
        claim_id=claim.claim_id,
        version=claim.version,
        header=header,
        stepper=_stepper(claim.stage),
        overview=overview,
        injury=await _injury(db, ctx, claim, risk, thresholds, today),
        documents=await documents_block(db, row, thresholds, documents),
        # No store is passed, and that is the deployment's state rather than an
        # omission: the binary backend is a Deferred decision, so nothing is
        # configured and every seeded row's `blob_key` is null anyway. The
        # parameter exists on `photos_block` so that wiring one in later is a
        # change to this line and to nothing else.
        photos=photos_block(await claim_repo.select_photos(db, ctx, claim.claim_id)),
        # The claim row is handed over rather than re-read: `services/financials`
        # computes from columns and never queries a claim, which is what keeps
        # `claim` a table only `services/claims` reads on the case-file path
        # (AD-12). It does read `state_rate_schedule`, which belongs to nobody.
        benefit=benefit,
        # The claim row and the benefit are handed over for the same reason:
        # `services/financials` computes from columns and never queries a
        # claim, which is what keeps `claim` a table only `services/claims`
        # reads on the case-file path (AD-12). It does read `rule_document`,
        # which belongs to nobody, and — from Story 3.3 — `payment_schedule_week`
        # and `bill`, which are financial tables rather than claim ones.
        #
        # **This refreshes the schedule before judging it** (Story 3.3), which
        # is why a GET now writes on the ~1-in-n requests that cross a week
        # boundary. Three of the five week statuses move with the calendar, and
        # AC 4 requires that this card and the Bills tab cannot show different
        # figures — which is only true if the refresh happens ahead of *both*
        # reads rather than ahead of one. `materialize_schedule` writes,
        # commits and audits nothing when nothing has moved.
        reserve_check=await reserve_check_for_claim(
            db,
            ctx,
            claim,
            benefit,
            today,
            claim_pk=claim.id,
            claim_ref=claim.claim_id,
        ),
        edit_options=EDIT_OPTIONS,
        thresholds_version=thresholds.version,
        requirements_version=requirements_version,
    )


async def claim_financial_detail(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    *,
    batch_weekdays: frozenset[int],
    as_of: date | None = None,
) -> ClaimFinancials:
    """The Bills & Payments read model for one claim, or `ClaimNotVisible`.

    **Here rather than in `services/financials` because of who owns `claim`.**
    AD-12 makes `services/claims` the only reader of the claim row on this
    path, and `services/financials` computes from columns it is handed and
    never queries one — the rule Story 3.2 recorded and Story 3.1 before it.
    So this function does the scoped resolve and the two rule-tier loads, and
    `claim_financials` does the money. The split is the same one
    `claim_detail` already keeps three lines further up.

    Raises `ClaimNotVisible` for a claim outside the caller's book *and* for
    one that does not exist, which is `select_claim_detail`'s deliberate
    conflation: two different answers would make this route an oracle for
    enumerating the portfolio (AD-7).

    `batch_weekdays` is required rather than defaulted (Story 3.4). It is the
    disbursement cadence the summary's "next batch" date is computed from, and
    it comes from `Settings` at the route — the SLA strip's arrangement. No
    default, because a default here would be a second place the cadence is
    written down, and the one that answered when somebody forgot to thread the
    real one.
    """
    today = as_of or utc_today()
    row = await claim_repo.select_claim_detail(db, ctx, claim_business_id)
    if row is None:
        raise ClaimNotVisible(claim_business_id)

    claim = row.Claim
    thresholds = await thresholds_for(db, today)
    return await claim_financials(
        db,
        ctx,
        claim,
        claim_pk=claim.id,
        claim_ref=claim.claim_id,
        benefit=await benefit_for_claim(db, claim, thresholds, today),
        thresholds=thresholds,
        as_of=today,
        batch_weekdays=batch_weekdays,
    )


async def _injury(
    db: AsyncSession,
    ctx: CallerContext,
    claim: Claim,
    risk: RiskBand,
    thresholds: DerivationThresholds,
    today: date,
) -> InjuryDiagram:
    """The Injury Diagram tab's payload — markers already banded (AC 1).

    The primary marker's band is the `risk` the header was built from — the
    *same value*, passed in rather than recomputed, so the gauge and the
    pulsing marker cannot disagree about one claim. Each secondary is banded
    by the same derivation over its own score, which is what makes a
    severity-52 secondary render amber next to a severity-78 primary in red
    without either colour being decided in the browser.

    **A deliberate deviation from the prototype.** `injHTML` bands its marker
    colours against a cut-off pair written into the drawing function, which
    is *not* the pair `risk` uses — the deployed `derivation_thresholds`
    document says something lower — so some scores draw one colour here and
    another there. Two banding rules for one score is exactly what AD-10
    exists to prevent, and the prototype's second pair appears in no rule
    document, so the registered derivation wins. Recorded in the story's Dev
    Agent Record rather than reproduced.

    (The numbers are deliberately not written out above:
    `tests/test_derivations.py::test_no_module_outside_the_registry_hardcodes_the_band`
    greps for them, and a comment naming a threshold is one edit away from
    being code that uses it.)
    """
    band = derivations.risk.for_thresholds(thresholds)
    capture = await injury_capture_for(db, today)

    primary = InjuryMarker(
        id=None,
        version=None,
        body_key=claim.body_key,
        body_part=claim.body_part,
        injury_type=claim.injury_type,
        severity_score=claim.severity_score,
        band=risk,
        primary=True,
    )
    secondaries = [
        InjuryMarker(
            id=injury.id,
            version=injury.version,
            body_key=str(injury.body_key),
            body_part=injury.body_part,
            injury_type=injury.injury_type,
            severity_score=injury.severity_score,
            band=band.of(injury.severity_score),
            primary=False,
        )
        for injury in await claim_repo.select_additional_injuries(db, ctx, claim.claim_id)
    ]

    return InjuryDiagram(
        # The primary first, as the prototype's `allMarkers` concatenates
        # them: the summary list reads "the injury, then what was added to
        # it", and the SVG draws in the same order so a secondary sharing a
        # region overlays the primary rather than hiding under it.
        markers=(primary, *secondaries),
        icd=claim.icd,
        icd_desc=claim.icd_desc,
        prognosis=Prognosis(
            mmi=claim.prognosis_mmi,
            rtw=claim.prognosis_rtw,
            impairment=claim.prognosis_impairment,
            litigation=claim.prognosis_litigation,
        ),
        treatment_plan=tuple(
            TreatmentPlanEntry(step_no=step.step_no, description=step.description)
            for step in await claim_repo.select_treatment_plan_steps(db, ctx, claim.claim_id)
        ),
        contraindications=claim.contraindications,
        default_severity_score=capture.new_injury_default_severity,
        capture_version=capture.version,
        severity_min=SEVERITY_MIN,
        severity_max=SEVERITY_MAX,
    )


async def _overview(
    db: AsyncSession,
    row: sa.Row[Any],
    risk: RiskBand,
    thresholds: DerivationThresholds,
    events: list[TimelineEntry],
    documents: Sequence[Document],
    today: date,
) -> tuple[StageOverview, int | None]:
    """`ovHTML`'s dispatch, server-side, with the rules version it consulted.

    `documents` is handed in rather than read here: `claim_detail` needs the
    same rows for Story 2.5's block, and only the intake variant reads them at
    all — so fetching them in this function meant one scoped query per intake
    claim that the caller had already run.

    **No `CallerContext`, and its absence is the point** (code review,
    2026-08-12). Hoisting that read took away this function's last scoped
    query, leaving a `ctx` parameter that was accepted and never used — which
    is exactly what `data/repositories/statutory_forms.py` argues against in
    the story that created it: a context nobody reads is decoration, and the
    next author to add a branch here would see it in the signature and take
    the scoping as already done. Everything this function still reads is
    either handed to it or unscoped rule data.
    """
    claim = row.Claim
    paid = derivations.total_paid.for_thresholds(thresholds).of(claim)
    split = derivations.cost_split.for_thresholds(thresholds).of(claim)

    if claim.stage is Stage.intake:
        requirements = await intake_requirements_for(db, today)
        on_file = frozenset(document.doc_type for document in documents)
        return IntakeOverview(
            stage_variant=Stage.intake.value,
            employee_business_id=row.employee_business_id,
            worker_name=row.worker_name,
            worker_role=row.worker_role,
            plant=claim.plant,
            doi=claim.doi,
            froi_date=claim.froi_date,
            assign_date=claim.assign_date,
            handler_name=row.handler_name,
            comm_status=claim.comm_status,
            injury_type=claim.injury_type,
            cause=claim.cause,
            body_part=claim.body_part,
            severity_score=claim.severity_score,
            risk=risk,
            aww_cents=claim.aww,
            reserve_cents=claim.reserve,
            checklist=_checklist(requirements.required_doc_types, on_file),
            timeline=_timeline(events, recent_only=False),
        ), requirements.version

    if claim.stage is Stage.treatment:
        days_open = derivations.days_open.for_thresholds(thresholds).of(claim.froi_date, today)
        phase = derivations.treatment_phase.for_thresholds(thresholds).of(
            days_open=days_open, recovery=claim.recovery
        )
        blocked = derivations.rtw_blocked.for_thresholds(thresholds).of(
            claim_id=claim.claim_id,
            stage=claim.stage,
            return_status=claim.return_status,
            risk=risk,
        )
        coordination = derivations.coordination_status.for_thresholds(thresholds).of(
            rtw_blocked=blocked,
            comm_status=claim.comm_status,
            litigation_flag=claim.litigation_flag,
        )
        return TreatmentOverview(
            stage_variant=Stage.treatment.value,
            phase=phase.phase,
            phase_note=phase.note,
            expected_days=phase.expected_days,
            days_open=days_open,
            recovery=claim.recovery,
            paid_medical_cents=claim.paid_medical,
            paid_indemnity_cents=claim.paid_indemnity,
            reserve_cents=claim.reserve,
            coordination_status=coordination.status,
            coordination_note=coordination.note,
            return_status=claim.return_status,
            comm_status=claim.comm_status,
            handler_name=row.handler_name,
            timeline=_timeline(events, recent_only=True),
            timeline_truncated=len(events) > RECENT_TIMELINE_COUNT,
        ), None

    if claim.stage is Stage.settled:
        return SettledOverview(
            stage_variant=Stage.settled.value,
            settlement_date=_settlement_date(events),
            total_paid_cents=paid,
            paid_indemnity_cents=claim.paid_indemnity,
            paid_medical_cents=claim.paid_medical,
            paid_expense_cents=claim.paid_expense,
            reserve_cents=claim.reserve,
            cost_split=split,
            disability=claim.disability,
            return_status=claim.return_status,
            # A registered derivation rather than the fallback inline: the
            # column it falls back from is one AD-2 reserves to the SLA
            # aggregation, and Epic 5 wants the same per-claim figure.
            days_to_settlement=derivations.days_to_settlement.for_thresholds(thresholds).of(
                claim, today
            ),
            litigation_flag=claim.litigation_flag,
            handler_name=row.handler_name,
            timeline=_timeline(events, recent_only=False),
        ), None

    if claim.stage is not Stage.investigation:
        # Explicit rather than an unconditional trailing `return`. A fifth
        # `Stage` member would otherwise be handed an investigation body and
        # then 500 several lines later inside `_stepper`'s `index()`, which
        # names neither the stage nor this function. Adding a member is a
        # migration, so this is unreachable today — the point is that when
        # it stops being unreachable it fails here, saying what happened.
        raise NotImplementedError(f"no overview variant for stage {claim.stage!r}")

    return InvestigationOverview(
        stage_variant=Stage.investigation.value,
        injury_type=claim.injury_type,
        cause=claim.cause,
        body_part=claim.body_part,
        icd=claim.icd,
        icd_desc=claim.icd_desc,
        disability=claim.disability,
        recovery=claim.recovery,
        aww_cents=claim.aww,
        total_paid_cents=paid,
        reserve_cents=claim.reserve,
        policy_num=claim.policy_num,
        fraud_score=claim.fraud_score,
        severity_score=claim.severity_score,
        risk=risk,
        paid_indemnity_cents=claim.paid_indemnity,
        paid_medical_cents=claim.paid_medical,
        cost_split=split,
        timeline=_timeline(events, recent_only=False),
    ), None
