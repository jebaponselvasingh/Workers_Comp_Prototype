"""Story 3.5 AC 1 — the eleven trigger rules, the cap, the padding, the order.

The generator is a pure function over structural protocols, so almost
everything worth asserting about it can be asserted without a database, a rules
engine or an HTTP client: each rule fires and does not fire against a claim that
differs in exactly one field, the cap keeps the *most urgent* six rather than
the first six, the padding fills a short list without displacing a trigger, and
the order is total rather than merely sorted.

That last one is the reason this file exists at all rather than being folded
into an endpoint test. Story 5.4's supervisor worklist reads element 0 of this
tuple as a claim's "Priority Next Best Action", so an order that depended on
dict iteration or on which sort Python ships would give a claim a different next
action between releases with nothing anywhere saying so — invisible on the card
(six rows in a slightly different order still look right) and wrong in a column
a supervisor makes decisions from.

The last section drives the endpoint against a real database, because three
things are only true of the wired-up version: that the derived flags come from
the registry rather than from a comparison written here, that the payload names
the rule document that shaped it, and that a claim outside the caller's book is
a 404 in the case file's exact wording.
"""

import json
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import create_app
from config import Settings
from data.models import Claim
from data.models.enums import (
    ActionCommand,
    ActionKey,
    ActionTarget,
    ActionUrgency,
    ClaimStatus,
    DocType,
    LineItemStatus,
    ReturnStatus,
    ScheduleWeekStatus,
    Stage,
)
from rules.parameters import WorklistActions, urgency_parameter_name
from services.worklist.actions import (
    SEAM_REASONS,
    Action,
    ClaimFlags,
    generate_actions,
)
from tests import seed_fixture
from tests.conftest import requires_db

TODAY = date(2026, 8, 17)


# --- stand-ins ----------------------------------------------------------


@dataclass(frozen=True)
class FakeClaim:
    """A claim as the eleven rules see it — nothing derived, nothing financial.

    Defaults are a claim with **nothing outstanding**: approved, not
    recordable, no surgery, no litigation, fully returned, settled. Every test
    below changes exactly one field, so the delta *is* the rule under test and
    a rule that fired for the wrong reason shows up as a second row appearing.
    """

    stage: Stage = Stage.settled
    status: ClaimStatus = ClaimStatus.settled_closed
    return_status: ReturnStatus = ReturnStatus.returned_and_fully_recovered
    surgery_required: bool = False
    osha_recordable: bool = False
    osha_logged: bool = False
    litigation_flag: bool = False
    attorney_rep: bool = False
    rtw_rec: date | None = None
    actual_rtw: date | None = None


@dataclass(frozen=True)
class FakeDocument:
    id: int = 1
    doc_type: DocType = DocType.medauth
    reviewed: bool = False
    confirmed: bool = False
    version: int = 1


@dataclass(frozen=True)
class FakeBill:
    status: LineItemStatus = LineItemStatus.paid


@dataclass(frozen=True)
class FakeWeek:
    status: ScheduleWeekStatus = ScheduleWeekStatus.paid


QUIET = ClaimFlags(siu_review=False, rtw_blocked=False, payment_due=False)

#: The committed document's parameters, restated. Written out rather than loaded
#: so that a test asserting "the cap is six" cannot be satisfied by whatever the
#: document happens to say — the oracle discipline `seed_fixture` and
#: `tests/test_rules_engine.py` both keep.
URGENCIES = {
    ActionKey.assessment_approval: ActionUrgency.high,
    ActionKey.siu_escalation: ActionUrgency.high,
    ActionKey.overdue_rtw: ActionUrgency.high,
    ActionKey.surgical_pre_auth: ActionUrgency.high,
    ActionKey.bill_review: ActionUrgency.medium,
    ActionKey.payment_confirmation: ActionUrgency.medium,
    ActionKey.osha_log: ActionUrgency.medium,
    ActionKey.defense_counsel: ActionUrgency.medium,
    ActionKey.modified_duty: ActionUrgency.medium,
    ActionKey.diary_check_in: ActionUrgency.low,
    ActionKey.routine_review: ActionUrgency.low,
}

PARAMS = WorklistActions(version=1, cap=6, padding_floor=3, urgencies=URGENCIES)


def run(
    claim: FakeClaim,
    *,
    documents: Sequence[FakeDocument] = (),
    bills: Sequence[FakeBill] = (),
    weeks: Sequence[FakeWeek] = (),
    flags: ClaimFlags = QUIET,
    params: WorklistActions = PARAMS,
    as_of: date = TODAY,
) -> tuple[Action, ...]:
    return generate_actions(
        claim,
        documents=documents,
        bills=bills,
        weeks=weeks,
        flags=flags,
        params=params,
        as_of=as_of,
    )


def keys(actions: Sequence[Action]) -> list[ActionKey]:
    return [action.key for action in actions]


# --- the quiet baseline -------------------------------------------------


def test_a_settled_claim_with_nothing_on_file_produces_no_actions_at_all() -> None:
    """The empty state has to be reachable, or it is decoration.

    A settled claim with no documents and no schedule offers no trigger *and*
    no padding item, because all three padding rows are conditional. Without
    that, the card would invent three things to do for a closed claim and the
    prototype's "No outstanding actions — claim is on track" would be a branch
    nothing could reach.
    """
    assert run(FakeClaim()) == ()


def test_the_baseline_claim_fires_nothing_so_every_case_below_is_its_own_delta() -> None:
    """Every test in the next section changes one field of `FakeClaim()`. If the
    baseline fired anything, each of those would be asserting about two rules."""
    assert keys(run(FakeClaim())) == []


# --- the eleven rules, firing and not firing ----------------------------


@pytest.mark.parametrize("status", sorted(ClaimStatus))
def test_the_assessment_approval_fires_from_exactly_two_statuses(status: ClaimStatus) -> None:
    """The set the command guards on, over the whole vocabulary.

    Parametrised across every status rather than checking the two that fire,
    because the failure worth catching is the *other* direction: a trigger that
    offered Approve on a `denied` claim would send a handler to a command that
    refuses it, for a control that should not have been on screen.
    """
    fired = ActionKey.assessment_approval in keys(run(FakeClaim(status=status)))
    assert fired == (status in {ClaimStatus.initial, ClaimStatus.ch_assessment_process})


def test_siu_escalation_reads_the_derivation_and_not_a_fraud_score() -> None:
    """`siu_review` is the registered derivation, and nothing here recomputes it.

    The threshold it encodes (`siuFraudScoreMin`) never reaches this module,
    which is what keeps the checklist and the queue's SIU filter from answering
    differently about one claim (AD-10).
    """
    assert ActionKey.siu_escalation not in keys(run(FakeClaim()))
    assert ActionKey.siu_escalation in keys(run(FakeClaim(), flags=replace(QUIET, siu_review=True)))


def test_overdue_rtw_fires_on_the_derivation_or_on_a_passed_recommendation() -> None:
    """Two conditions, and each catches claims the other misses.

    `rtw_blocked` is a demo hash bucket over treatment claims; the date
    comparison is a fact about this claim's calendar that no derivation covers.
    Asserting them separately is what stops one being deleted as redundant.
    """
    away = FakeClaim(return_status=ReturnStatus.under_treatment)
    assert ActionKey.overdue_rtw not in keys(run(away))
    assert ActionKey.overdue_rtw in keys(run(away, flags=replace(QUIET, rtw_blocked=True)))
    assert ActionKey.overdue_rtw in keys(
        run(
            FakeClaim(return_status=ReturnStatus.under_treatment, rtw_rec=TODAY - timedelta(days=1))
        )
    )


def test_a_worker_already_back_is_never_overdue_however_the_dates_read() -> None:
    """The gate that made this rule agree with `modified_duty` (code review,
    2026-08-17).

    Two seeded claims — WC-21122 and WC-21683 — are `returned_and_under_therapy`
    with a recommended date in the past and no `actual_rtw` captured. That is a
    records gap, not a worker who never went back, and reading the null alone
    put a `high` "chase the return to work" row on the same card as
    `modified_duty`'s "the worker is back on restricted capacity". A checklist
    that contradicts itself in two adjacent lines is worse than one that misses
    a row, so `return_status` decides and the calendar only sharpens it.

    Asserted for both returned statuses, and with the derivation forced on as
    well as off: `rtw_blocked` already implies `under_treatment`, so a flag
    saying otherwise is a bug in the derivation and must not resurrect the row
    here.
    """
    stale_records = {"rtw_rec": TODAY - timedelta(days=30), "actual_rtw": None}
    for returned in (
        ReturnStatus.returned_and_under_therapy,
        ReturnStatus.returned_and_fully_recovered,
    ):
        claim = FakeClaim(return_status=returned, **stale_records)  # type: ignore[arg-type]
        assert ActionKey.overdue_rtw not in keys(run(claim))
        assert ActionKey.overdue_rtw not in keys(run(claim, flags=replace(QUIET, rtw_blocked=True)))


def test_a_recommended_return_date_that_has_been_met_is_not_overdue() -> None:
    """The `actual_rtw` half of the rule. Without it, every claim whose worker
    came back on time would carry an overdue-return action for ever."""
    past = TODAY - timedelta(days=30)
    assert ActionKey.overdue_rtw not in keys(run(FakeClaim(rtw_rec=past, actual_rtw=past)))


def test_a_recommended_return_date_still_in_the_future_is_not_overdue() -> None:
    assert ActionKey.overdue_rtw not in keys(run(FakeClaim(rtw_rec=TODAY + timedelta(days=1))))


def test_the_recommended_return_date_boundary_is_today_exclusive() -> None:
    """A date of today has not passed. Stated because it is the one comparison
    in the rule table whose off-by-one would be invisible: a claim due back this
    morning is not yet late, and a card that said so would be crying wolf on
    every claim on its return date."""
    assert ActionKey.overdue_rtw not in keys(run(FakeClaim(rtw_rec=TODAY)))


def test_surgical_pre_auth_fires_while_the_authorization_is_unconfirmed() -> None:
    """ "Not yet authorized" is `confirmed`, not `reviewed` — reading an
    authorization is not granting it, which is the whole reason the two columns
    are separate."""
    surgical = FakeClaim(surgery_required=True)

    assert ActionKey.surgical_pre_auth in keys(run(surgical, documents=[FakeDocument()]))
    assert ActionKey.surgical_pre_auth in keys(
        run(surgical, documents=[FakeDocument(reviewed=True)])
    )
    assert ActionKey.surgical_pre_auth not in keys(
        run(surgical, documents=[FakeDocument(reviewed=True, confirmed=True)])
    )


def test_surgical_pre_auth_does_not_fire_without_surgery() -> None:
    assert ActionKey.surgical_pre_auth not in keys(run(FakeClaim(), documents=[FakeDocument()]))


def test_surgical_pre_auth_fires_when_no_authorization_is_on_file_at_all() -> None:
    """A missing authorization is a stronger reason to look, not a weaker one.

    The row has no completion control in that case — there is no document to
    mark — so it deep-links to the Documents tab instead. A generator that
    required a document to exist would go quiet on exactly the claims where the
    pre-auth has not been started.
    """
    only_a_froi = [FakeDocument(doc_type=DocType.froi)]
    actions = {
        action.key: action
        for action in run(FakeClaim(surgery_required=True), documents=only_a_froi)
    }

    assert ActionKey.surgical_pre_auth in actions
    assert actions[ActionKey.surgical_pre_auth].command is None
    assert actions[ActionKey.surgical_pre_auth].document_id is None


def test_bill_review_fires_only_for_a_bill_awaiting_a_decision() -> None:
    """`under_review` is the status `services/financials`' approval accepts, so
    the row's deep link lands on a bill that actually has a ✓ on it."""
    assert ActionKey.bill_review not in keys(run(FakeClaim(), bills=[FakeBill()]))
    assert ActionKey.bill_review not in keys(
        run(FakeClaim(), bills=[FakeBill(status=LineItemStatus.pending_submission)])
    )
    assert ActionKey.bill_review in keys(
        run(FakeClaim(), bills=[FakeBill(status=LineItemStatus.under_review)])
    )


@pytest.mark.parametrize("status", sorted(ScheduleWeekStatus))
def test_payment_confirmation_fires_for_a_week_that_is_due_or_awaiting_approval(
    status: ScheduleWeekStatus,
) -> None:
    fired = ActionKey.payment_confirmation in keys(run(FakeClaim(), weeks=[FakeWeek(status)]))
    assert fired == (
        status in {ScheduleWeekStatus.due_this_week, ScheduleWeekStatus.pending_approval}
    )


def test_payment_confirmation_also_fires_on_the_registered_flag() -> None:
    """The union with `payment_due`, which is a demo hash bucket over treatment
    claims (`services/derivations/queue_flags.py`). Consuming it as-is is the
    story's instruction; the consequence — the row appears on some claims that
    owe nothing — is a known property of the seed rather than a defect here."""
    assert ActionKey.payment_confirmation in keys(
        run(FakeClaim(), flags=replace(QUIET, payment_due=True))
    )


def test_the_osha_log_fires_on_the_gap_between_recordable_and_logged() -> None:
    """The reason `osha_logged` is stored rather than derived: nothing else on
    the claim says whether anybody made the entry."""
    assert ActionKey.osha_log not in keys(run(FakeClaim()))
    assert ActionKey.osha_log in keys(run(FakeClaim(osha_recordable=True)))
    assert ActionKey.osha_log not in keys(run(FakeClaim(osha_recordable=True, osha_logged=True)))


def test_defense_counsel_fires_on_either_litigation_or_representation() -> None:
    """Either, not both — a filed suit and a retained attorney are different
    facts and each is a reason for counsel to be in the loop."""
    assert ActionKey.defense_counsel not in keys(run(FakeClaim()))
    assert ActionKey.defense_counsel in keys(run(FakeClaim(litigation_flag=True)))
    assert ActionKey.defense_counsel in keys(run(FakeClaim(attorney_rep=True)))


@pytest.mark.parametrize("return_status", sorted(ReturnStatus))
def test_modified_duty_fires_only_for_a_worker_back_on_restricted_capacity(
    return_status: ReturnStatus,
) -> None:
    fired = ActionKey.modified_duty in keys(run(FakeClaim(return_status=return_status)))
    assert fired == (return_status is ReturnStatus.returned_and_under_therapy)


@pytest.mark.parametrize("stage", sorted(Stage))
def test_the_diary_check_in_fires_for_a_claim_in_treatment(stage: Stage) -> None:
    fired = ActionKey.diary_check_in in keys(run(FakeClaim(stage=stage)))
    assert fired == (stage is Stage.treatment)


# --- the seam: disabled targets and their sentences ---------------------


def test_every_seam_target_renders_disabled_with_the_epic_that_enables_it() -> None:
    """AC 3, and the property Stories 4.2 and 6.2 will flip.

    The sentence is asserted to be the *server's* — the same string the
    generator's table holds — because the whole point of publishing it is that
    the browser never decides which epics have shipped.
    """
    loud = FakeClaim(stage=Stage.treatment, return_status=ReturnStatus.returned_and_under_therapy)
    actions = {
        action.key: action
        for action in run(
            loud, flags=ClaimFlags(siu_review=True, rtw_blocked=True, payment_due=True)
        )
    }

    for key, target in (
        (ActionKey.siu_escalation, ActionTarget.fraud),
        (ActionKey.overdue_rtw, ActionTarget.rtw_letter),
        (ActionKey.modified_duty, ActionTarget.meetings),
        (ActionKey.diary_check_in, ActionTarget.diary),
    ):
        action = actions.get(key)
        # Not every one of the four survives the cap on this claim; the ones
        # that do must be disabled, and the assertion is about them.
        if action is None:
            continue
        assert action.enabled is False
        assert action.disabled_reason == SEAM_REASONS[target]


def test_a_disabled_row_never_carries_a_completion_command() -> None:
    """There is nothing to complete until the surface exists, and a button that
    recorded a completion for work nobody could do would be a false record."""
    for action in run(
        FakeClaim(stage=Stage.treatment, osha_recordable=True),
        flags=ClaimFlags(siu_review=True, rtw_blocked=True, payment_due=False),
    ):
        if not action.enabled:
            assert action.command is None


def test_an_enabled_row_carries_no_disabled_reason() -> None:
    """`enabled` and `disabledReason` are one fact in two fields, and the
    payload's contract says the second is null exactly when the first is true."""
    for action in run(FakeClaim(osha_recordable=True, litigation_flag=True)):
        if action.enabled:
            assert action.disabled_reason is None


# --- completions the row offers -----------------------------------------


def test_the_assessment_row_offers_the_approval_command() -> None:
    action = next(
        action
        for action in run(FakeClaim(status=ClaimStatus.ch_assessment_process))
        if action.key is ActionKey.assessment_approval
    )
    assert action.command is ActionCommand.approve_assessment
    assert action.target is ActionTarget.approve


def test_the_osha_row_offers_the_mark_logged_command() -> None:
    action = next(
        action
        for action in run(FakeClaim(osha_recordable=True))
        if action.key is ActionKey.osha_log
    )
    assert action.command is ActionCommand.mark_osha_logged


def test_the_pre_auth_row_offers_review_then_confirm_and_carries_the_version() -> None:
    """The offered control is the server's answer, and it advances with the row.

    The version travels with it for `ScheduleWeekView.version`'s reason: a
    control that compare-and-swaps has to hold the number it will send, or the
    handler's first click 409s against a payload they never saw.
    """
    surgical = FakeClaim(surgery_required=True)

    unread = next(
        action
        for action in run(surgical, documents=[FakeDocument(id=7, version=3)])
        if action.key is ActionKey.surgical_pre_auth
    )
    assert unread.command is ActionCommand.mark_document_reviewed
    assert (unread.document_id, unread.document_version) == (7, 3)

    read = next(
        action
        for action in run(surgical, documents=[FakeDocument(id=7, version=4, reviewed=True)])
        if action.key is ActionKey.surgical_pre_auth
    )
    assert read.command is ActionCommand.confirm_document
    assert (read.document_id, read.document_version) == (7, 4)


def test_the_pre_auth_row_names_the_first_unconfirmed_authorization() -> None:
    """Documents arrive in filing order, so "first" is the oldest outstanding
    one — which is the one a handler should be looking at."""
    action = next(
        action
        for action in run(
            FakeClaim(surgery_required=True),
            documents=[
                FakeDocument(id=1, confirmed=True, reviewed=True),
                FakeDocument(id=2),
                FakeDocument(id=3),
            ],
        )
        if action.key is ActionKey.surgical_pre_auth
    )
    assert action.document_id == 2


# --- the cap, the padding and the order ---------------------------------


def everything_fires(
    return_status: ReturnStatus = ReturnStatus.under_treatment,
) -> tuple[FakeClaim, dict[str, Any]]:
    """The loudest claim the rule table admits — nine of the ten triggers.

    **Ten cannot fire at once, and that is a property of the domain rather than
    a gap in this fixture** (code review, 2026-08-17). `overdue_rtw` asks
    whether the worker is still away and `modified_duty` asks whether they are
    back on restricted capacity; one `return_status` cannot answer yes to both,
    and a card that claimed otherwise would contradict itself in two adjacent
    rows. The parameter names which of the two worlds the caller wants; the
    default is the one where the worker has not returned, because that is the
    world the cap test's expected ordering was written against.

    Everything else here is independent claim state, except
    `payment_confirmation`, which rides a flag.
    """
    claim = FakeClaim(
        stage=Stage.treatment,
        status=ClaimStatus.ch_assessment_process,
        return_status=return_status,
        surgery_required=True,
        osha_recordable=True,
        litigation_flag=True,
        rtw_rec=TODAY - timedelta(days=5),
    )
    inputs: dict[str, Any] = {
        "documents": [FakeDocument()],
        "bills": [FakeBill(status=LineItemStatus.under_review)],
        "weeks": [FakeWeek(status=ScheduleWeekStatus.due_this_week)],
        "flags": ClaimFlags(siu_review=True, rtw_blocked=True, payment_due=True),
    }
    return claim, inputs


def test_every_trigger_rule_can_fire_across_the_two_return_status_worlds() -> None:
    """The premise of the cap tests below. If this drifted — a rule that could
    no longer fire alongside the others — the cap would still be six and the
    test asserting it would be exercising nothing.

    Two claims rather than one, because `overdue_rtw` and `modified_duty` are
    mutually exclusive by construction (see `everything_fires`). Asserting the
    *union* is what keeps that exclusion honest: if a later edit made both
    fire together, or made either stop firing at all, one of the three
    assertions below breaks.
    """
    uncapped = replace(PARAMS, cap=99, padding_floor=0)

    away_claim, away_inputs = everything_fires()
    back_claim, back_inputs = everything_fires(ReturnStatus.returned_and_under_therapy)
    away = set(keys(run(away_claim, params=uncapped, **away_inputs)))
    back = set(keys(run(back_claim, params=uncapped, **back_inputs)))

    assert away == set(ActionKey) - {ActionKey.routine_review, ActionKey.modified_duty}
    assert back == set(ActionKey) - {ActionKey.routine_review, ActionKey.overdue_rtw}
    assert away | back == set(ActionKey) - {ActionKey.routine_review}


def test_the_cap_keeps_the_most_urgent_rather_than_the_first_to_fire() -> None:
    """Ranking happens before cutting, which is what makes the cap mean "the six
    most urgent". Cutting first would make it "whichever six the rule table
    happens to list earliest", and the two differ on exactly the claims a
    handler most needs the card to be right about."""
    claim, inputs = everything_fires()

    capped = run(claim, **inputs)

    assert len(capped) == PARAMS.cap
    assert keys(capped) == [
        ActionKey.assessment_approval,
        ActionKey.siu_escalation,
        ActionKey.overdue_rtw,
        ActionKey.surgical_pre_auth,
        ActionKey.bill_review,
        ActionKey.payment_confirmation,
    ]
    assert [action.urgency for action in capped[:4]] == [ActionUrgency.high] * 4


def test_a_different_cap_in_the_rule_document_changes_the_list_length() -> None:
    """AC 2, at the generator's boundary: the cap is a parameter, so nothing in
    Python decides how long the card is.

    `tests/test_action_endpoint_parameters` below does the same thing through a
    superseded rule document and a real database, which is the end-to-end half
    of the claim.
    """
    claim, inputs = everything_fires()

    assert len(run(claim, params=replace(PARAMS, cap=2, padding_floor=0), **inputs)) == 2
    assert len(run(claim, params=replace(PARAMS, cap=9, padding_floor=0), **inputs)) == 9


def test_the_order_is_stable_when_the_inputs_are_shuffled() -> None:
    """Story 5.4 reads element 0, so this is the test that protects it.

    Ties on urgency are broken by the rules' declaration order, not by the order
    documents or bills happen to arrive in — so re-ordering every input list
    must not move a single row. Without the explicit tiebreak the list would
    still *sort*, and would still look right, and element 0 could differ.
    """
    claim, inputs = everything_fires()
    inputs["documents"] = [FakeDocument(id=2), FakeDocument(id=1)]
    inputs["bills"] = [
        FakeBill(status=LineItemStatus.paid),
        FakeBill(status=LineItemStatus.under_review),
    ]

    first = run(claim, **inputs)
    reversed_inputs = {
        "documents": list(reversed(inputs["documents"])),
        "bills": list(reversed(inputs["bills"])),
        "weeks": list(reversed(inputs["weeks"])),
        "flags": inputs["flags"],
    }

    assert keys(run(claim, **reversed_inputs)) == keys(first)
    assert run(claim, **inputs) == first


def test_rows_of_equal_urgency_keep_the_rule_tables_order() -> None:
    """The tiebreak, isolated. All five `medium` rules fire and no `high` one
    does, so the whole ordering is the tiebreak and nothing else."""
    claim = FakeClaim(
        stage=Stage.investigation,
        return_status=ReturnStatus.returned_and_under_therapy,
        osha_recordable=True,
        litigation_flag=True,
    )

    ordered = run(
        claim,
        bills=[FakeBill(status=LineItemStatus.under_review)],
        weeks=[FakeWeek(status=ScheduleWeekStatus.due_this_week)],
    )

    assert keys(ordered) == [
        ActionKey.bill_review,
        ActionKey.payment_confirmation,
        ActionKey.osha_log,
        ActionKey.defense_counsel,
        ActionKey.modified_duty,
    ]


def test_a_quiet_open_claim_is_padded_to_the_floor() -> None:
    """One rule fires; the list comes back at the padding floor."""
    padded = run(
        FakeClaim(stage=Stage.treatment, status=ClaimStatus.ch_approved),
        documents=[FakeDocument()],
        weeks=[FakeWeek()],
    )

    assert len(padded) == PARAMS.padding_floor
    assert keys(padded) == [
        ActionKey.diary_check_in,
        ActionKey.routine_review,
        ActionKey.routine_review,
    ]


def test_padding_never_displaces_a_trigger() -> None:
    """Padding is considered only after the cut, so a full list gains none."""
    claim, inputs = everything_fires()

    assert ActionKey.routine_review not in keys(run(claim, **inputs))


def test_padding_stops_at_the_floor_rather_than_filling_to_the_cap() -> None:
    """The floor is a minimum, not a target length. A card padded to six on a
    claim with one real action would bury the one thing that mattered."""
    padded = run(
        FakeClaim(stage=Stage.treatment),
        documents=[FakeDocument()],
        weeks=[FakeWeek()],
    )

    assert len(padded) == 3


def test_padding_offers_only_the_rows_that_apply() -> None:
    """Each padding row has a condition, which is what keeps the empty state
    reachable and what stops the card offering "review the schedule" on a claim
    with no schedule."""
    padded = run(FakeClaim(stage=Stage.treatment))

    # Treatment stage, no documents, no schedule: the case-file row applies and
    # the other two do not, so the diary check-in plus one padding row is all
    # this claim can offer — short of the floor, honestly.
    assert keys(padded) == [ActionKey.diary_check_in, ActionKey.routine_review]
    assert padded[1].target is ActionTarget.overview


def test_a_padding_floor_of_zero_leaves_a_short_list_short() -> None:
    unpadded = run(
        FakeClaim(stage=Stage.treatment),
        documents=[FakeDocument()],
        params=replace(PARAMS, padding_floor=0),
    )
    assert keys(unpadded) == [ActionKey.diary_check_in]


def test_every_row_has_a_unique_identity() -> None:
    """`id` keys a client-side list, so a duplicate is a React list that drops
    a row — and the three padding rows are the case that makes it possible."""
    claim, inputs = everything_fires()
    padded = run(FakeClaim(stage=Stage.treatment), documents=[FakeDocument()], weeks=[FakeWeek()])

    for actions in (run(claim, **inputs), padded):
        ids = [action.id for action in actions]
        assert len(set(ids)) == len(ids)


def test_the_urgency_of_every_row_comes_from_the_parameter_block() -> None:
    """AD-8, structurally: retune a rule in the block and the row moves with it.

    Asserted by making every rule `low` and checking that nothing renders `high`
    — a hardcoded urgency anywhere in the rule table would survive that.
    """
    claim, inputs = everything_fires()
    flattened = replace(PARAMS, urgencies={key: ActionUrgency.low for key in ActionKey})

    assert {action.urgency for action in run(claim, params=flattened, **inputs)} == {
        ActionUrgency.low
    }


# --- wired up, against a real database ----------------------------------


KAYA = ("Kaya Johnson", "handler")
JENNIFER = ("Jennifer Park", "supervisor")


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


@asynccontextmanager
async def make_client(db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(database_url=db_url, env="e2e"))  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def login_as(client: httpx.AsyncClient, name: str, role: str) -> None:
    personas = (await client.get("/personas")).json()["items"]
    match = [p for p in personas if p["name"] == name and p["role"] == role]
    assert len(match) == 1, f"expected exactly one {name}/{role} persona"
    assert (await client.post("/auth/login", json={"personaId": match[0]["id"]})).status_code == 200


def claims_for(persona: tuple[str, str]) -> list[str]:
    return sorted(str(claim["claim_id"]) for claim in seed_fixture.claims_for(*persona))


@requires_db
async def test_the_endpoint_answers_a_ranked_capped_list_for_a_seeded_claim(
    seeded_db_url: str,
) -> None:
    """The whole wiring: scope, derivations, rule document, generator, payload.

    Asserted over the *whole* caseload rather than one claim, because the two
    things a single claim cannot show are that no claim ever exceeds the cap and
    that the order the server sent is already ranked — a client that re-sorted
    would be indistinguishable from one that did not on a list of three.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)

        for claim_id in claims_for(KAYA)[:12]:
            resp = await client.get(f"/claims/{claim_id}/actions")
            assert resp.status_code == 200, resp.text
            payload = resp.json()

            assert payload["cap"] == 6
            assert payload["paddingFloor"] == 3
            assert payload["rulesVersion"] == 1
            assert len(payload["items"]) <= payload["cap"]

            ranks = ["high", "medium", "low"]
            positions = [ranks.index(item["urgency"]) for item in payload["items"]]
            assert positions == sorted(positions), f"{claim_id} came back unranked"


@requires_db
async def test_a_claim_outside_the_callers_book_is_the_case_files_own_404(
    seeded_db_url: str,
) -> None:
    """AD-7: one answer for "no such claim" and "not yours", in the same words
    the case-file route uses — a different one would make this route an oracle
    for enumerating a portfolio the caller cannot read."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        mine = set(claims_for(KAYA))
        theirs = next(
            claim for claim in claims_for(("Dante Reyes", "handler")) if claim not in mine
        )

        outside = await client.get(f"/claims/{theirs}/actions")
        missing = await client.get("/claims/WC-99999/actions")

        assert outside.status_code == 404
        assert missing.status_code == 404
        assert outside.json()["detail"] == f"No claim {theirs} in your caseload."
        assert outside.json()["type"] == missing.json()["type"]


@requires_db
async def test_a_supervisor_may_read_a_checklist_they_cannot_act_on(
    seeded_db_url: str,
) -> None:
    """Reading what is outstanding is a read; capability gating belongs on the
    writes. A supervisor who could not see the list would be unable to ask a
    handler about it, and the three commands refuse them anyway."""
    async with make_client(seeded_db_url) as client:
        await login_as(client, *JENNIFER)
        claim_id = claims_for(JENNIFER)[0]

        assert (await client.get(f"/claims/{claim_id}/actions")).status_code == 200


@requires_db
async def test_the_osha_trigger_reads_the_claims_own_columns(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """The generator against real rows rather than stand-ins, on the one rule
    whose backing column this story adds.

    Flipping `osha_logged` directly (rather than through the command, which
    `tests/test_assessment_commands.py` covers) isolates the *trigger*: the row
    drops out because the column moved, which is what "completions are
    entity-backed" means.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)

        claim_id = await _first_claim_where(
            client, lambda payload: ActionKey.osha_log.value in _keys_of(payload)
        )

        await db.execute(
            sa.update(Claim).where(Claim.claim_id == claim_id).values(osha_logged=True)
        )
        await db.commit()

        after = (await client.get(f"/claims/{claim_id}/actions")).json()["items"]
        assert ActionKey.osha_log.value not in {item["key"] for item in after}


@requires_db
async def test_a_superseded_rule_document_changes_the_list_with_no_code_change(
    db: AsyncSession, seeded_db_url: str
) -> None:
    """AC 2, end to end — the only demonstration of AD-8 that is worth anything.

    A v2 of `worklist_actions` with `cap: 1` is inserted, effective today, and
    the same claim's list comes back one row long. Nothing is deployed, nothing
    is restarted and no Python changes; `rules/engine.py` picks the highest
    version whose date has arrived on the next request.

    Rolled back at the end so the rest of the module sees the seeded document —
    and read back *before* the rollback, because the assertion is about what a
    request answered while the version was live.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, *KAYA)
        claim_id = await _first_claim_where(client, lambda payload: len(payload["items"]) > 1)

        original = (await client.get(f"/claims/{claim_id}/actions")).json()
        assert original["cap"] == 6

        document = (
            await db.execute(
                sa.text(
                    "SELECT content FROM rule_document "
                    "WHERE key = 'worklist_actions' ORDER BY version DESC LIMIT 1"
                )
            )
        ).scalar_one()
        narrowed = _retuned(document, cap=1, padding_floor=1)
        await db.execute(
            sa.text(
                "INSERT INTO rule_document (key, version, effective_from, content, created_at) "
                "VALUES ('worklist_actions', 2, :today, CAST(:content AS jsonb), now())"
            ),
            {"today": date.today(), "content": _dumps(narrowed)},
        )
        await db.commit()

        try:
            retuned = (await client.get(f"/claims/{claim_id}/actions")).json()
            assert retuned["cap"] == 1
            assert retuned["rulesVersion"] == 2
            assert len(retuned["items"]) == 1
            # The one survivor is the first of the original list — the ranking
            # is unchanged, only its length.
            assert retuned["items"][0]["key"] == original["items"][0]["key"]
        finally:
            await db.execute(
                sa.text("DELETE FROM rule_document WHERE key = 'worklist_actions' AND version = 2")
            )
            await db.commit()


def _keys_of(payload: dict[str, Any]) -> set[str]:
    return {str(item["key"]) for item in payload["items"]}


async def _first_claim_where(
    client: httpx.AsyncClient, matches: Callable[[dict[str, Any]], bool]
) -> str:
    """The caller's first claim whose checklist satisfies `matches`.

    A loop rather than a generator expression, and the reason is worth
    recording: `next(... for c in ... if await ...)` is an *async generator*
    rather than an iterator, so `next` raises `TypeError` — a failure that reads
    like a bad predicate rather than like the wrong comprehension.

    Searched rather than hardcoded for `tests/test_payment_approval.py`'s
    reason: which seeded claim carries a given trigger is a property of the
    dataset, and a test that pinned one would fail on a reseed for a reason that
    has nothing to do with what it checks.
    """
    for claim_id in claims_for(KAYA):
        payload: dict[str, Any] = (await client.get(f"/claims/{claim_id}/actions")).json()
        if matches(payload):
            return claim_id
    pytest.fail("no seeded claim in this caseload matches the predicate")


def _retuned(content: dict[str, Any], *, cap: int, padding_floor: int) -> dict[str, Any]:
    """The committed document with two expressions rewritten.

    Edited rather than written out, so the variant differs from the real
    document in exactly the parameters under test — the same discipline every
    `**changes` fixture in `tests/test_rule_parameters.py` keeps.

    The padding floor moves with the cap because `WorklistActions` refuses a
    floor above it, and rightly: "pad to three, then cut to one" is a
    contradiction rather than a tuning. That refusal is exercised directly in
    `tests/test_rule_parameters.py`; here it just has to be respected.
    """
    variant = _dumps(content)
    variant = variant.replace('"key": "cap", "value": "6"', f'"key": "cap", "value": "{cap}"')
    variant = variant.replace(
        '"key": "paddingFloor", "value": "3"',
        f'"key": "paddingFloor", "value": "{padding_floor}"',
    )
    retuned: dict[str, Any] = _loads(variant)
    return retuned


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(", ", ": "))


def _loads(value: str) -> Any:
    result: Any = json.loads(value)
    return result


def test_the_urgency_parameter_names_cover_every_rule() -> None:
    """A last-line guard on the two vocabularies: a rule with no parameter name
    is a rule whose urgency the document cannot carry."""
    assert {urgency_parameter_name(key) for key in ActionKey} == {
        urgency_parameter_name(key) for key in URGENCIES
    }
