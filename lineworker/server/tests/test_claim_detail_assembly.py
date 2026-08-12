"""Story 2.2 — the case file's assembly rules, without a database.

`test_claim_detail.py` drives the endpoint against the seeded portfolio,
which is the right way to assert the contract and the wrong way to assert
the *rules*: the seed contains no treatment claim longer than the recent
window and no claim missing every required document, so the branches that
matter most are the ones real data never reaches.

These are pure functions over small inputs, so they run in the plain
`uv run pytest` job — and they can build the twelve-event claim the
portfolio does not have.
"""

from datetime import date

import pytest

from data.models.enums import DocType, Stage, TimelineTag
from services.claims.detail import (
    RECENT_TIMELINE_COUNT,
    ChecklistRow,
    TimelineEntry,
    _checklist,
    _settlement_date,
    _stepper,
    _timeline,
)


def event(index: int, tag: str = TimelineTag.medical, day: int | None = None) -> TimelineEntry:
    return TimelineEntry(
        event_date=date(2026, 3, day) if day else None,
        description=f"event {index}",
        tag=tag,
    )


# --- the stepper --------------------------------------------------------


@pytest.mark.parametrize(
    ("stage", "done_count"),
    [(Stage.intake, 0), (Stage.investigation, 1), (Stage.treatment, 2), (Stage.settled, 3)],
)
def test_the_stepper_is_always_four_steps_with_one_current(stage: Stage, done_count: int) -> None:
    steps = _stepper(stage)

    assert [step.stage for step in steps] == [
        Stage.intake,
        Stage.investigation,
        Stage.treatment,
        Stage.settled,
    ]
    assert sum(step.done for step in steps) == done_count
    assert [step.stage for step in steps if step.current] == [stage]
    # A step is never both. The prototype's markup makes them exclusive by
    # class; here they are two booleans, so it is worth stating.
    assert not any(step.done and step.current for step in steps)


def test_every_stage_has_a_step() -> None:
    """A fifth `Stage` member would otherwise raise `ValueError` from inside
    a request rather than failing here."""
    for stage in Stage:
        assert any(step.stage is stage for step in _stepper(stage))


# --- the recent-timeline slice ------------------------------------------


def test_the_full_timeline_is_returned_untouched() -> None:
    events = [event(index) for index in range(12)]

    assert _timeline(events, recent_only=False) == tuple(events)


def test_the_recent_slice_is_the_last_six_in_append_order() -> None:
    """The *last* six, not the first — the prototype's `.slice(-6)`.

    Getting this backwards shows a handler the intake paperwork and hides
    the surgery, on a card headed "recent", and no seeded claim is long
    enough for the difference to show up in an integration test.
    """
    events = [event(index) for index in range(12)]

    sliced = _timeline(events, recent_only=True)

    assert len(sliced) == RECENT_TIMELINE_COUNT
    assert sliced == tuple(events[6:])
    assert sliced[-1].description == "event 11"


@pytest.mark.parametrize("count", [0, 1, 5, 6])
def test_a_timeline_at_or_under_the_window_is_returned_whole(count: int) -> None:
    events = [event(index) for index in range(count)]

    assert _timeline(events, recent_only=True) == tuple(events)


# --- the intake checklist -----------------------------------------------


REQUIRED = (DocType.froi, DocType.incident, DocType.medauth, DocType.wage)


def test_the_checklist_keeps_the_documents_order_not_the_claims() -> None:
    """The rows are the *requirement* list, so their order is the rule
    document's. A claim that filed its wage statement first must not
    reorder somebody's checklist."""
    on_file = frozenset({DocType.wage, DocType.froi})

    assert _checklist(REQUIRED, on_file) == (
        ChecklistRow(doc_type=DocType.froi, received=True),
        ChecklistRow(doc_type=DocType.incident, received=False),
        ChecklistRow(doc_type=DocType.medauth, received=False),
        ChecklistRow(doc_type=DocType.wage, received=True),
    )


def test_a_claim_with_nothing_on_file_is_all_missing() -> None:
    assert all(not row.received for row in _checklist(REQUIRED, frozenset()))


def test_a_claim_with_everything_on_file_is_all_received() -> None:
    assert all(row.received for row in _checklist(REQUIRED, frozenset(REQUIRED)))


def test_documents_nobody_required_do_not_appear() -> None:
    """The checklist answers "what is still missing", not "what is on file".

    A legal filing and a return-to-work letter are real documents that are
    not intake requirements; listing them would turn a four-row checklist
    into an inventory.
    """
    rows = _checklist(REQUIRED, frozenset({DocType.legal, DocType.rtw, DocType.froi}))

    assert [row.doc_type for row in rows] == list(REQUIRED)


def test_an_empty_requirement_list_is_an_empty_checklist() -> None:
    """ "Nothing is required at intake" is a position the rule document may
    take; the UI's empty state is what renders it."""
    assert _checklist((), frozenset({DocType.froi})) == ()


# --- the settlement date ------------------------------------------------


def test_the_settlement_date_comes_from_the_settlement_tagged_event() -> None:
    events = [
        event(0, TimelineTag.intake, day=1),
        event(1, TimelineTag.rtw, day=5),
        event(2, TimelineTag.settlement, day=9),
    ]

    assert _settlement_date(events) == date(2026, 3, 9)


def test_the_last_settlement_event_wins() -> None:
    """A reopened-and-resettled claim has two. The prototype searches
    backwards; so does this, for the same reason — the later one is the
    settlement the banner is describing."""
    events = [
        event(0, TimelineTag.settlement, day=2),
        event(1, TimelineTag.settlement, day=20),
    ]

    assert _settlement_date(events) == date(2026, 3, 20)


def test_a_settlement_event_without_a_date_yields_none() -> None:
    """Every seeded settlement event is exactly this case: the prototype
    writes `Closed` where the date belongs, so there is nothing to report
    and the banner omits the clause rather than printing a non-date."""
    assert _settlement_date([event(0, TimelineTag.settlement)]) is None


def test_a_timeline_with_no_settlement_event_yields_none() -> None:
    assert _settlement_date([event(0, TimelineTag.intake, day=1)]) is None


def test_a_description_mentioning_closure_is_not_a_settlement() -> None:
    """The prototype matches `/settl|clos/i` against the tag *or the
    description*, so "closure planning underway" reads as a settlement and
    dates the banner from an event about planning. Matching the tag token
    exactly is what stops a wording change from moving a settlement date.
    """
    events = [event(0, TimelineTag.medical, day=4)]
    events[0] = TimelineEntry(
        event_date=date(2026, 3, 4),
        description="Nearing MMI — closure planning underway",
        tag=TimelineTag.medical,
    )

    assert _settlement_date(events) is None
