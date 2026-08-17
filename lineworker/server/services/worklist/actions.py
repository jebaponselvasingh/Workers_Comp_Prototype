"""The auto-generated action checklist (Story 3.5) — one generator, AD-2.

A handler opening a case file gets figures and history; this is the part that
answers "what do I do next". Eleven trigger rules are evaluated against the
claim, its documents and its money, ranked, capped and padded, and the result
is what the Overview's "⏰ Upcoming actions required" card renders and what
Story 5.4's supervisor worklist reads element 0 of.

## Four properties, and each one is load-bearing

**Deterministic, and never LLM-originated (AD-2).** The same claim state
produces the same list in the same order. Epic 6's copilot may *quote* this
list; it may not produce one, and nothing here asks a model anything.

**Totally ordered, not merely sorted.** Rows rank on `(urgency, rule index)`
where the index is `ActionKey`'s declaration order. Urgency alone leaves ties —
five rules share `medium` — and a tie broken by dict iteration is a list that
reorders between releases. That would be invisible on this card and *visible*
in Story 5.4, whose "Priority Next Best Action" column is element 0 of this
tuple: a claim's next best action would change without its state changing.
`tests/test_action_checklist.py` asserts the order is stable across shuffled
inputs.

**Every tunable is a rule document's answer (AD-8).** The cap, the padding
floor and all eleven urgencies come from `worklist_actions` through
`rules/parameters.py`. What stays here is the trigger *logic* — what makes a
bill reviewable, what makes a return-to-work date overdue — because that is a
condition over typed claim state rather than a number an operator retunes.
Supersede the document with a different `cap` and the rendered list changes
length with no deploy; `tests/test_rule_parameters.py` demonstrates exactly
that end to end, because a claim about a parameter tier is only worth as much
as its demonstration.

**Derived flags come from the registry and nowhere else (AD-10).**
`siu_review`, `rtw_blocked` and `payment_due` arrive on `ClaimFlags`, computed
by `services/derivations` in `claim_actions` below. Two of the three are
hash-bucket demo definitions over the claim id — `queue_flags.py` says so at
length — and this story consumes them as they are. The consequence is worth
stating plainly rather than discovering: on some claims the checklist will name
a payment nobody owes, because `payment_due` is a property of the claim id's
digits. Replacing those definitions is a change to one module and to no caller,
which is the whole point of them being registered.

## Why the rules are a table of small functions

Each rule is a function from one context to `Action | None`, and `_TRIGGERS`
lists them in `ActionKey` order. Written as a table rather than as one long
`if` chain because the *order is the contract*: a chain hides the ranking
tiebreak in its line numbers, and appending a rule at the bottom of a chain is
a silent re-ranking of everything above it. Here the order is
`data/models/enums.py`'s and a twelfth rule is a member added at a chosen
position.

`routine_review` is the eleventh member and is deliberately not in `_TRIGGERS`:
it produces *several* rows rather than one, and it runs only after the triggers
have been counted. It is still a rule and can still decline to fire — a settled
claim with no documents and no schedule produces nothing at all, which is what
the card's empty state is for.

## Completions are entity-backed, which is why there is no state table here

An action stops firing because the entity it reads has moved: `osha_log` while
`claim.osha_logged` is false, `surgical_pre_auth` while the medical
authorization is unconfirmed, `bill_review` while a bill is `under_review`.
There is no "completed actions" store, deliberately — the narrative's rule is
that "the worklist and the ledgers stay in sync because they read the same
status row", and a second place a completion is written down is the first place
the two can disagree. `services/claims/assessment.py` holds the three commands
that move those rows; this module never writes (AD-12).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
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
from data.repositories import claims as claim_repo
from rules.parameters import WorklistActions, thresholds_for, worklist_actions_for
from services import derivations

# `services/claims` imports nothing from `services/worklist`, so this direction
# is one-way — the same direction AD-12 already runs in when
# `services/worklist/approvals.py` calls into `services/financials`.
from services.claims.assessment import APPROVABLE_ASSESSMENT_STATUSES, document_step_of
from services.claims.detail import ClaimNotVisible
from services.financials import APPROVABLE_LINE_ITEM_STATUSES

#: What a `document` row must be for the surgical pre-authorization to count as
#: on file. `medauth` is the prototype's own document class for a treatment
#: authorization (`DocType`), and "authorized" is `confirmed` rather than
#: `reviewed`: reading the authorization is not granting it.
PRE_AUTH_DOC_TYPE: Final[DocType] = DocType.medauth

#: Line-item and week statuses the two financial triggers read.
#:
#: `BILL_REVIEW_STATUSES` is **imported from the command that enforces it**,
#: for the reason `APPROVABLE_ASSESSMENT_STATUSES` is imported above: a trigger
#: that reads its own copy of "what can be approved" stops firing for bills the
#: Bills tab is happily offering an ✓ on, the first time somebody widens the
#: financials set. It was a restated literal until the review pass caught it
#: (2026-08-17); the week set has no such owner, because no command approves a
#: week for being *due* — that is a fact about the calendar, not a capability.
BILL_REVIEW_STATUSES: Final[frozenset[LineItemStatus]] = APPROVABLE_LINE_ITEM_STATUSES
PAYMENT_DUE_WEEK_STATUSES: Final[frozenset[ScheduleWeekStatus]] = frozenset(
    {ScheduleWeekStatus.due_this_week, ScheduleWeekStatus.pending_approval}
)

#: The targets no surface exists for yet, and the sentence each one shows
#: instead of a dead click (NFR-3, AC 3).
#:
#: **The sentence is the server's, not the browser's**, which is what makes the
#: seam one field: Story 4.1 enabled the meetings link and Story 6.2 will enable
#: the fraud one by deleting an entry here, and the SPA changes not at all. A
#: client-side map of "which epics have shipped" would be a second copy of this
#: table, stale in exactly the release where one of them landed.
#:
#: **`meetings` was here and is not any more** — Story 4.1 built the scheduler,
#: so the target is live and the row carries a real "Schedule Meeting →". That
#: is the whole of what enabling a seam costs, and it is the demonstration
#: `ActionTarget`'s docstring promised: one deletion here, nothing in the SPA.
#:
#: **`diary` was here too, and Story 4.2 deleted it.** The Notes sub-tab exists,
#: so "Log Diary Entry →" opens it with the add-note input focused. Two
#: deletions in two stories, and the SPA changed in neither — except for one
#: line that is *not* about the seam and is easy to mistake for it:
#: `ActionsCard.NAVIGABLE_FROM_OVERVIEW` has to gain the target as well, because
#: that card's render gate is `!enabled || NAVIGABLE_FROM_OVERVIEW.has(target)`
#: and an enabled row outside the set renders no control at all. Story 4.1 hit
#: the same trap with `meetings`.
SEAM_REASONS: Final[Mapping[ActionTarget, str]] = {
    ActionTarget.fraud: "Available with AI Insights — Epic 6",
    ActionTarget.rtw_letter: "Available with the RTW letter — Epic 6",
}

#: How long one diary note holds the weekly check-in closed.
#:
#: **A module constant, not a JDM tunable**, and the line is the one AD-8 draws:
#: `worklist_actions` carries the cap, the padding floor and eleven *urgencies*
#: — numbers an operator retunes to change how loud a list is. The trigger
#: *logic* has always been Python, because it is a condition over typed state
#: rather than a knob. Seven days is what "weekly" means, and it sits beside the
#: rule that reads it.
#:
#: The counter-argument is real and is recorded in `deferred-work.md`: a
#: check-in *cadence* is arguably operator tuning, and promoting it is one key
#: in the parameter block plus a line in `WorklistActions` if anybody ever wants
#: to retune it without a deploy.
DIARY_CHECK_IN_DAYS: Final[int] = 7


class ActionClaim(Protocol):
    """The claim columns the eleven rules read.

    Structural rather than the ORM class for `ScheduleRow`'s reason: the
    generator stays pure and a test can hand it a small stand-in, so the whole
    rule table is exercised without a database. Every member is a stored column
    — nothing derived appears here, because derived values arrive on
    `ClaimFlags` from the registry (AD-10).
    """

    @property
    def stage(self) -> Stage: ...
    @property
    def status(self) -> ClaimStatus: ...
    @property
    def return_status(self) -> ReturnStatus: ...
    @property
    def surgery_required(self) -> bool: ...
    @property
    def osha_recordable(self) -> bool: ...
    @property
    def osha_logged(self) -> bool: ...
    @property
    def litigation_flag(self) -> bool: ...
    @property
    def attorney_rep(self) -> bool: ...
    @property
    def rtw_rec(self) -> date | None: ...
    @property
    def actual_rtw(self) -> date | None: ...


class ActionDocument(Protocol):
    """One `document` row, as the pre-authorization rule reads it."""

    @property
    def id(self) -> int: ...
    @property
    def doc_type(self) -> DocType: ...
    @property
    def reviewed(self) -> bool: ...
    @property
    def confirmed(self) -> bool: ...
    @property
    def version(self) -> int: ...


class ActionLineItem(Protocol):
    """One `bill`, as the bill-review rule reads it."""

    @property
    def status(self) -> LineItemStatus: ...


class ActionWeek(Protocol):
    """One `payment_schedule_week`, as the payment-confirmation rule reads it."""

    @property
    def status(self) -> ScheduleWeekStatus: ...


@dataclass(frozen=True)
class ClaimFlags:
    """The three registered derivations the rules consume (AD-10).

    Handed in rather than computed here, and the distinction is the architecture
    rather than a preference: these three have exactly one computer each, in
    `services/derivations`, so that the queue card, the case-file header and
    this checklist cannot disagree about whether a claim is blocked. A generator
    that re-derived them would be the second computer AD-10 exists to prevent —
    and it would be the *quiet* kind, because both answers would look right most
    of the time.
    """

    siu_review: bool
    rtw_blocked: bool
    payment_due: bool


@dataclass(frozen=True)
class Action:
    """One row of the checklist.

    `id` is the row's stable identity — `"{key}:{target}"` — and it is computed
    here rather than in the browser for the reason every other identity on the
    wire is: it keys a React list and an e2e selector, and a client that built
    it would be a second place the format is written down. It is unique by
    construction (no two rules share a target with the same key, and the three
    padding rows differ in theirs), which is why it does not need a surrogate.

    `enabled` and `disabled_reason` travel together and are the cross-epic seam:
    a `False` here is not an error state, it is "the surface this points at is
    Epic 4's". The sentence is the server's — see `SEAM_REASONS`.

    `command` is the completion this row offers, if any, and `document_id` /
    `document_version` are what that command needs when it acts on a document.
    The version rides along for `ScheduleWeekView.version`'s reason: a control
    that compare-and-swaps must be holding the number it will send, or the
    handler's first click 409s against a payload they never saw.
    """

    id: str
    key: ActionKey
    label: str
    urgency: ActionUrgency
    target: ActionTarget
    enabled: bool
    disabled_reason: str | None
    command: ActionCommand | None
    document_id: int | None
    document_version: int | None


@dataclass(frozen=True)
class ClaimActions:
    """The checklist for one claim, and the parameters that shaped it.

    `cap` and `padding_floor` are published beside the rows for
    `thresholds_version`'s reason: the card's length is a rule document's
    answer, and "why are there six of these?" should be answerable from the
    response rather than reconstructed. `rules_version` names the document that
    answered, exactly as the queue names both of its own.

    Nothing on this payload is a count the client has to compute — `items` is
    the list, and its length is already the answer.
    """

    items: tuple[Action, ...]
    cap: int
    padding_floor: int
    rules_version: int


@dataclass(frozen=True)
class _Context:
    """Everything the rule table sees. One object so a rule's signature is one
    parameter and adding an input does not touch eleven functions."""

    claim: ActionClaim
    documents: Sequence[ActionDocument]
    bills: Sequence[ActionLineItem]
    weeks: Sequence[ActionWeek]
    #: When the **caller** last wrote a diary note about this claim, or `None`
    #: (Story 4.2). A read of another aggregate rather than a fourth row
    #: sequence, because the rule needs one instant and not a list — and a read
    #: is all it is: AD-12 governs writes, and this module never writes.
    latest_note_at: datetime | None
    flags: ClaimFlags
    params: WorklistActions
    as_of: date


def _action(
    context: _Context,
    key: ActionKey,
    *,
    label: str,
    target: ActionTarget,
    command: ActionCommand | None = None,
    document: ActionDocument | None = None,
) -> Action:
    """Build one row, taking its urgency from the rule document and its
    `enabled` from the seam table.

    Every rule goes through here, so no rule can hardcode an urgency (AD-8) or
    forget that its target is one of the four that has no surface yet — which
    is the failure this helper exists to prevent, because a disabled control
    that renders as enabled is precisely the dead click NFR-3 forbids.
    """
    reason = SEAM_REASONS.get(target)
    return Action(
        id=f"{key.value}:{target.value}",
        key=key,
        label=label,
        urgency=context.params.urgency_of(key),
        target=target,
        enabled=reason is None,
        disabled_reason=reason,
        # A seam target's row never offers a completion: there is nothing to
        # complete until the surface exists, and a button that wrote a
        # completion for work nobody could do would be a false record.
        command=None if reason is not None else command,
        document_id=None if document is None else document.id,
        document_version=None if document is None else document.version,
    )


# --- the eleven rules, in ranking-tiebreak order ------------------------


def _assessment_approval(context: _Context) -> Action | None:
    """The claim's own assessment is still awaiting a decision.

    The prototype's `approveClaim` (lines 925-930) reads the same two statuses,
    and the set is imported from the command that enforces it rather than
    restated: a trigger that offered the button from a status the command
    refuses is a 409 a handler cannot act on.
    """
    if context.claim.status not in APPROVABLE_ASSESSMENT_STATUSES:
        return None
    return _action(
        context,
        ActionKey.assessment_approval,
        label="Approve the claim assessment",
        target=ActionTarget.approve,
        command=ActionCommand.approve_assessment,
    )


def _siu_escalation(context: _Context) -> Action | None:
    """The fraud indicators clear the SIU referral threshold.

    The registered `siu_review` derivation and nothing else — the threshold it
    encodes (`siuFraudScoreMin`) is a rules parameter this module never sees,
    which is what stops the checklist and the queue's SIU filter answering
    differently about one claim.
    """
    if not context.flags.siu_review:
        return None
    return _action(
        context,
        ActionKey.siu_escalation,
        label="Escalate to SIU — fraud indicators on file",
        target=ActionTarget.fraud,
    )


def _overdue_rtw(context: _Context) -> Action | None:
    """The worker has not returned, and either the derivation says blocked or
    the recommended date has passed.

    Two conditions rather than one, and they are different statements.
    `rtw_blocked` is the registered derivation (a demo hash bucket over
    treatment claims, `queue_flags.py`); the date comparison is a fact about
    *this* claim's calendar that no derivation covers — a recommended return
    date in the past with no actual return recorded. Either alone would miss
    claims the other catches, and the union is what the story's rule names
    ("`rtw_blocked` / RTW date passed without actual RTW").

    **"Has not returned" is `return_status`, not a null `actual_rtw`**, and the
    difference is two real claims. WC-21122 and WC-21683 are seeded
    `returned_and_under_therapy` with a recommended date in the past and no
    actual return date captured — a records gap, not a worker who never went
    back. Reading the null alone put a `high` "chase the return to work" row on
    the same card as `modified_duty`'s "the worker is back on restricted
    capacity", which is the checklist contradicting itself in two adjacent
    lines. `rtw_blocked` already implies `under_treatment` (`queue_flags.py`),
    so this gate changes nothing for that branch and closes the date branch's
    hole — and it is the condition the first line of this docstring has claimed
    from the start (code review, 2026-08-17).
    """
    claim = context.claim
    if claim.return_status is not ReturnStatus.under_treatment:
        return None
    date_passed = (
        claim.rtw_rec is not None and claim.rtw_rec < context.as_of and claim.actual_rtw is None
    )
    if not (context.flags.rtw_blocked or date_passed):
        return None
    return _action(
        context,
        ActionKey.overdue_rtw,
        label="Review Return-to-Work policy & offer letter",
        target=ActionTarget.rtw_letter,
    )


def _surgical_pre_auth(context: _Context) -> Action | None:
    """Surgery is required and the medical authorization is not yet confirmed.

    "Not yet authorized" is `confirmed`, not `reviewed`: reading an
    authorization is not granting it, which is the whole reason the two columns
    are separate. The action carries the *first* unconfirmed authorization on
    file so its completion control has something to act on; a claim with no
    authorization at all still fires — the document is missing entirely, which
    is a stronger reason to look, not a weaker one — and its control is a deep
    link to the Documents tab rather than a write.
    """
    if not context.claim.surgery_required:
        return None
    pending = next(
        (
            document
            for document in context.documents
            if document.doc_type is PRE_AUTH_DOC_TYPE and not document.confirmed
        ),
        None,
    )
    if pending is None and any(
        document.doc_type is PRE_AUTH_DOC_TYPE for document in context.documents
    ):
        # Every authorization on file is confirmed — the pre-auth is done.
        return None
    # Which control the row offers is `services/claims`' answer, asked rather
    # than restated — the offered completion and the accepted transition are one
    # rule, and two copies of it is a button the command refuses.
    command = (
        None
        if pending is None
        else document_step_of(reviewed=pending.reviewed, confirmed=pending.confirmed)
    )
    return _action(
        context,
        ActionKey.surgical_pre_auth,
        label="Authorize the surgical pre-approval",
        target=ActionTarget.documents,
        command=command,
        document=pending,
    )


def _bill_review(context: _Context) -> Action | None:
    """At least one medical bill is sitting `under_review`.

    The status the *approval* command accepts (`APPROVABLE_LINE_ITEM_STATUSES`
    in `services/financials`), which is not a coincidence: this row's deep link
    lands on the Bills tab so the handler can press the ✓ Story 3.4 put there,
    and an action that fired for a bill nobody can approve would send them to a
    row with no control on it.
    """
    if not any(bill.status in BILL_REVIEW_STATUSES for bill in context.bills):
        return None
    return _action(
        context,
        ActionKey.bill_review,
        label="Review medical bills awaiting approval",
        target=ActionTarget.bills,
    )


def _payment_confirmation(context: _Context) -> Action | None:
    """A week of the indemnity schedule is due or awaiting approval.

    The union of the registered `payment_due` flag and the stored week statuses,
    for `_overdue_rtw`'s reason: the flag is a demo hash bucket over treatment
    claims and the statuses are this claim's actual schedule. Consuming both
    means the row appears for the claims the queue already marks payment-due
    *and* for every claim with a week a handler can actually act on.

    **This reads the stored statuses without refreshing the schedule**, and that
    is deliberate. Three of the five week statuses move with the calendar and
    `services/financials/materialize.py` is what advances them — but it writes,
    and Story 3.3 already recorded a GET that writes as debt rather than a
    pattern to spread. The row's deep link lands on the Bills tab, which does
    refresh, so the worst case is that a handler is sent to look at a schedule
    one week-boundary staler than the tab will show them.
    """
    due = context.flags.payment_due or any(
        week.status in PAYMENT_DUE_WEEK_STATUSES for week in context.weeks
    )
    if not due:
        return None
    return _action(
        context,
        ActionKey.payment_confirmation,
        label="Confirm the indemnity payment for this week",
        target=ActionTarget.bills,
    )


def _osha_log(context: _Context) -> Action | None:
    """A recordable injury whose OSHA 300 entry has not been filed.

    The gap between the two columns, and the reason `osha_logged` is stored
    rather than derived: `osha_recordable` says the entry is *required*, and
    nothing else on the claim says whether anybody made it.
    """
    if not (context.claim.osha_recordable and not context.claim.osha_logged):
        return None
    return _action(
        context,
        ActionKey.osha_log,
        label="Record the injury on the OSHA 300 log",
        target=ActionTarget.overview,
        command=ActionCommand.mark_osha_logged,
    )


def _defense_counsel(context: _Context) -> Action | None:
    """The claim is in litigation, or the worker is represented.

    Either, not both. They are different facts — a filed suit and a retained
    attorney — and each on its own is a reason for the carrier's counsel to be
    in the loop. The queue's priority score weights only `litigation_flag`;
    that is a *ranking* decision and this is a *what to do* one, so they are
    allowed to read different columns.
    """
    if not (context.claim.litigation_flag or context.claim.attorney_rep):
        return None
    return _action(
        context,
        ActionKey.defense_counsel,
        label="Coordinate the case file with defense counsel",
        target=ActionTarget.documents,
    )


def _modified_duty(context: _Context) -> Action | None:
    """The worker is back on restricted capacity and the restrictions need
    confirming with the plant.

    `returned_and_under_therapy` is the one of the three return statuses that
    describes somebody working within limits — `under_treatment` is not back at
    all and `returned_and_fully_recovered` has no restrictions left to confirm.
    """
    if context.claim.return_status is not ReturnStatus.returned_and_under_therapy:
        return None
    return _action(
        context,
        ActionKey.modified_duty,
        label="Schedule the modified-duty review with the plant",
        target=ActionTarget.meetings,
    )


def _diary_check_in(context: _Context) -> Action | None:
    """A claim in treatment with no recent note from the reader's own diary.

    **The completion this rule reads is the note itself** (Story 4.2), which is
    what the previous version of this docstring promised would happen: "when it
    lands this condition becomes 'no note within seven days' without the row
    appearing or disappearing from the card in the meantime." It has, and the
    row does not move — the *stage* gate is unchanged and the note condition
    only ever removes rows that had nothing to satisfy them.

    **There is no completion button, and that is a decision rather than an
    omission.** `ActionCommand` has four members, each naming a specific entity
    write, and this module's docstring states the rule: there is no "completed
    actions" store, because a second place a completion is written down is the
    first place the two can disagree. A fifth command whose only job is to
    record "I said I did it" is exactly that second place. So the deep link is
    the completion path: following "Log Diary Entry →" opens the Notes sub-tab
    focused on the input, and saving a note makes this row stop firing on its
    own — the same shape `osha_log` has, one column over.

    **`latest_note_at` is the *caller's* most recent note on this claim**, not
    anybody's. A note belongs to its author (`diary_note_scope`), and counting
    *anybody's* note would publish the existence of a handler's private working
    record to whoever else read the claim — a diary is not a management report.
    So "has this been checked in on?" is answered from the reader's own diary,
    and a reader with no diary of their own sees the row.

    **That is not a statement about supervisors reading case files, and an
    earlier version of this docstring wrongly said it was.** `GET
    /claims/{id}/actions` has no role gate, but the only surface that reads it
    is the workspace, and `web/src/App.tsx` gates that route to `handler` — no
    supervisor or analyst ever reaches a case file, so the "supervisor sees an
    extra row" consequence has no path to a screen. The scope decision stands on
    the privacy argument alone; the reachability claim was decoration, and
    decoration that a reader could have checked and found false.

    **The window is exclusive at `DIARY_CHECK_IN_DAYS`.** A note written today
    holds the row closed for six more days and it fires again on the seventh,
    which is what "weekly" means: a Monday note is due again the following
    Monday, not the Tuesday after. `noted_at` is compared as a UTC calendar day
    against the same `as_of` every other rule here uses.
    """
    if context.claim.stage is not Stage.treatment:
        return None
    latest = context.latest_note_at
    if latest is not None and (context.as_of - latest.date()).days < DIARY_CHECK_IN_DAYS:
        return None
    return _action(
        context,
        ActionKey.diary_check_in,
        label="Log the weekly diary check-in",
        target=ActionTarget.diary,
    )


#: The ten single-row rules, in `ActionKey` order — which is the ranking
#: tiebreak. See the module docstring on why this is a table.
_TRIGGERS: Final[tuple[tuple[ActionKey, Callable[[_Context], Action | None]], ...]] = (
    (ActionKey.assessment_approval, _assessment_approval),
    (ActionKey.siu_escalation, _siu_escalation),
    (ActionKey.overdue_rtw, _overdue_rtw),
    (ActionKey.surgical_pre_auth, _surgical_pre_auth),
    (ActionKey.bill_review, _bill_review),
    (ActionKey.payment_confirmation, _payment_confirmation),
    (ActionKey.osha_log, _osha_log),
    (ActionKey.defense_counsel, _defense_counsel),
    (ActionKey.modified_duty, _modified_duty),
    (ActionKey.diary_check_in, _diary_check_in),
)

#: Where each rule sits when two share an urgency. Built from `_TRIGGERS` rather
#: than from `ActionKey` so that the tiebreak is the *evaluation* order a reader
#: sees above, and so a rule moved in the table moves in the ranking with it.
_RULE_INDEX: Final[Mapping[ActionKey, int]] = {
    key: index for index, (key, _rule) in enumerate(_TRIGGERS)
}

#: Most urgent first. A tuple rather than an `IntEnum` on `ActionUrgency`,
#: because rank is a property of this ordering rather than of the vocabulary —
#: the wire carries `"high"`, and a numeric member would be a second thing the
#: token could be confused with.
_URGENCY_RANK: Final[Mapping[ActionUrgency, int]] = {
    urgency: rank for rank, urgency in enumerate(ActionUrgency)
}

#: The padding rows, in the order they are offered, each with the condition
#: under which it applies. All three carry `ActionKey.routine_review`, so they
#: share its urgency and rank below every trigger of a louder one.
#:
#: **Conditional rather than filler**, which is what keeps the empty state
#: reachable: a settled claim with no documents and no schedule offers none of
#: these, and the card says "No outstanding actions — claim is on track"
#: instead of inventing three things to do.
_PADDING: Final[tuple[tuple[ActionTarget, str, Callable[[_Context], bool]], ...]] = (
    (
        ActionTarget.overview,
        "Review the case file and confirm the reserve position",
        lambda context: context.claim.stage is not Stage.settled,
    ),
    (
        ActionTarget.documents,
        "Review the documents on file",
        lambda context: len(context.documents) > 0,
    ),
    (
        ActionTarget.bills,
        "Review the indemnity payment schedule",
        lambda context: len(context.weeks) > 0,
    ),
)


def _rank(action: Action) -> tuple[int, int]:
    """`(urgency, rule index)` — the total order. See the module docstring.

    Padding rows are not in `_RULE_INDEX`; they sort after every trigger of the
    same urgency, which is the honest answer for a row that fired because the
    list was short rather than because anything happened.
    """
    return (_URGENCY_RANK[action.urgency], _RULE_INDEX.get(action.key, len(_TRIGGERS)))


def generate_actions(
    claim: ActionClaim,
    *,
    documents: Sequence[ActionDocument],
    bills: Sequence[ActionLineItem],
    weeks: Sequence[ActionWeek],
    # **Required, like every other data input**, and it was the one keyword here
    # with a default. `None` is not "not supplied", it is the *answer* "this
    # claim has never been checked in on" — the value that makes
    # `_diary_check_in` fire. A caller who forgot the `select_latest_note_at`
    # read would therefore re-open the check-in on every treatment claim in the
    # book and no test would notice, because the checklist would still be
    # perfectly well-formed. Making it required moves that from a silent wrong
    # answer to a `TypeError` at the call site.
    latest_note_at: datetime | None,
    flags: ClaimFlags,
    params: WorklistActions,
    as_of: date,
) -> tuple[Action, ...]:
    """The checklist for one claim — pure, deterministic, totally ordered.

    Three steps, in this order, and the order is the rule:

    1. Every trigger is evaluated and the survivors are ranked and cut to
       `cap`. Ranking before cutting is what makes the cap mean "the six most
       urgent" rather than "the first six that happened to fire".
    2. If fewer rows than `padding_floor` survived, routine review items are
       appended until the floor is met or the applicable ones run out. Padding
       fills a *short* list; it never displaces a trigger, because it is
       considered only after the cut.
    3. The result is truncated to `cap` again — a no-op while `padding_floor <=
       cap`, which `WorklistActions.__post_init__` refuses to let a document
       break, and here anyway so that the cap is a property of this function
       rather than of an invariant two modules away.

    Takes no session and touches no registry: the flags and the parameters
    arrive already resolved, which is what lets `tests/test_action_checklist.py`
    exercise all eleven rules against hand-written stand-ins with no database
    and no rules engine.
    """
    context = _Context(
        claim=claim,
        documents=documents,
        bills=bills,
        weeks=weeks,
        latest_note_at=latest_note_at,
        flags=flags,
        params=params,
        as_of=as_of,
    )

    fired = [action for _key, rule in _TRIGGERS if (action := rule(context)) is not None]
    # `sorted` is stable, so equal ranks would keep evaluation order anyway —
    # the explicit rule index is here so that the order is a *statement* rather
    # than a consequence of which sort Python ships.
    chosen = sorted(fired, key=_rank)[: context.params.cap]

    if len(chosen) < context.params.padding_floor:
        for target, label, applies in _PADDING:
            if len(chosen) >= context.params.padding_floor:
                break
            if applies(context):
                chosen.append(
                    _action(
                        context,
                        ActionKey.routine_review,
                        label=label,
                        target=target,
                    )
                )

    # **Ranked again after padding, and the second sort is not redundant.**
    # Padding is appended, so the list is only in rank order while
    # `urgencyRoutineReview` is the lowest urgency in play — which is true of
    # the shipped document and is not something the parameter block enforces
    # (nor should it: "show the handler their routine review first" is a
    # policy an operator may want). Retune it upward and, without this line,
    # rows that outrank every trigger render beneath them and Story 5.4 reads
    # the wrong element 0. `_rank` already sorts padding after triggers of
    # equal urgency, so this is a no-op on today's parameters (code review,
    # 2026-08-17).
    return tuple(sorted(chosen, key=_rank)[: context.params.cap])


async def claim_actions(
    db: AsyncSession,
    ctx: CallerContext,
    claim_business_id: str,
    *,
    as_of: date | None = None,
) -> ClaimActions:
    """Resolve one claim in the caller's scope and generate its checklist.

    Raises `ClaimNotVisible` for a claim outside the caller's book **and** for
    one that does not exist — `select_claim_detail`'s deliberate conflation, so
    that this route cannot be walked to enumerate a portfolio (AD-7).

    **No role gate**, unlike the three commands. Reading what is outstanding on
    a claim is a read, and a supervisor looking at their own book's case file
    should see the same list the handler does; capability gating belongs on the
    writes, which is where `services/claims/assessment.py` puts it.

    Six reads and two rule-document loads. The claim resolve is scoped; the four
    child reads are scoped again in the repository, which is that module's
    standing rule rather than redundancy.

    The sixth is Story 4.2's: the caller's most recent diary note on this claim,
    read so the check-in rule can stop firing once one exists. A read across
    aggregates, which is fine — AD-12 governs *writes*, and this module never
    writes.
    """
    today = as_of or derivations.utc_today()
    row = await claim_repo.select_claim_detail(db, ctx, claim_business_id)
    if row is None:
        raise ClaimNotVisible(claim_business_id)
    claim = row.Claim

    thresholds = await thresholds_for(db, today)
    params = await worklist_actions_for(db, today)

    risk = derivations.risk.for_thresholds(thresholds).of(claim.severity_score)
    flags = ClaimFlags(
        siu_review=derivations.siu_review.for_thresholds(thresholds).of(
            fraud_flag=claim.fraud_flag, fraud_score=claim.fraud_score
        ),
        rtw_blocked=derivations.rtw_blocked.for_thresholds(thresholds).of(
            claim_id=claim.claim_id,
            stage=claim.stage,
            return_status=claim.return_status,
            risk=risk,
        ),
        payment_due=derivations.payment_due.for_thresholds(thresholds).of(
            claim_id=claim.claim_id, stage=claim.stage
        ),
    )

    items = generate_actions(
        claim,
        documents=await claim_repo.select_documents(db, ctx, claim_business_id),
        bills=await claim_repo.select_bills(db, ctx, claim_business_id),
        weeks=await claim_repo.select_payment_schedule(db, ctx, claim_business_id),
        latest_note_at=await claim_repo.select_latest_note_at(db, ctx, claim_business_id),
        flags=flags,
        params=params,
        as_of=today,
    )
    return ClaimActions(
        items=items,
        cap=params.cap,
        padding_floor=params.padding_floor,
        rules_version=params.version,
    )


__all__ = [
    "BILL_REVIEW_STATUSES",
    "DIARY_CHECK_IN_DAYS",
    "PAYMENT_DUE_WEEK_STATUSES",
    "PRE_AUTH_DOC_TYPE",
    "SEAM_REASONS",
    "Action",
    "ActionClaim",
    "ActionDocument",
    "ActionLineItem",
    "ActionWeek",
    "ClaimActions",
    "ClaimFlags",
    "claim_actions",
    "generate_actions",
]
