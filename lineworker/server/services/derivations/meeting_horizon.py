"""Whether a scheduled meeting is still ahead — one computer (Story 4.1, AD-10).

The prototype decides this in the browser, twice over: `renderMeetings` (line
1898) computes `m.date >= today && !m.done` to pick a card class, and Story
4.2's today's-meetings summary will ask the same question of the same rows in
a different component. Two comparisons against two notions of "today" — the
browser's local midnight in one place and whatever a later surface reaches for
in the other — is exactly the drift AD-10 exists to stop, and it is the quiet
kind: both answers look right for most of the day.

So the answer is the server's, computed here, and sent on the wire as
`status`. The SPA renders it and never re-derives it from `meetingDate`.

**Registered although it reads no threshold and is one comparison.** That is
`next_batch_date`'s argument and `installments_paid`'s before it: registration
is not about the size of the function, it is about there being exactly one of
it. Story 4.2's queue-side summary consumes this entry rather than writing a
second `>=`.

**`as_of` is a parameter, not `utc_today()` called inside.** The horizon moves
at midnight, so a test that could not name the day would have to either freeze
the clock or accept a assertion that fails once a day; every other derivation
that depends on the calendar (`days_open`, `next_batch_date`,
`next_payment_due`) takes the same argument for the same reason, and the
request-level caller resolves it once so that every meeting in one list is
judged against one day.

**A past meeting nobody ticked reads as done, not as overdue.** The prototype
has the same two states and this keeps them: a third value ("missed") would be
a product claim — that a touchpoint which has not been marked complete did not
happen — that nothing in the data supports, since the ✓ is optional and a
handler who held the call may simply not have come back to the card.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol

from services.derivations.registry import Derivation, register


class MeetingStatus(StrEnum):
    """The two states a meeting card renders in — Upcoming and Done.

    Here rather than in `data/models/enums.py` because **no column holds
    one**: `is_done` and `meeting_date` are the stored facts and this is the
    answer computed from them, which is `RiskBand`'s and `TreatmentPhase`'s
    position exactly. A member of this enum in a database enum type would be a
    derived column, which Story 1.2 banned.

    Snake_case tokens on the wire; the labels ("Upcoming", "✓ Done") and the
    card's accent treatment are the browser's, per the Enums convention.
    """

    upcoming = "upcoming"
    done = "done"


class ScheduledMeeting(Protocol):
    """The two columns the rule reads — the whole of what it depends on.

    Structural rather than the ORM class for `ActionClaim`'s reason: the rule
    stays pure and a test can hand it a two-field stand-in, so both sides of
    the boundary are exercised without a database.
    """

    @property
    def meeting_date(self) -> date: ...
    @property
    def is_done(self) -> bool: ...


@dataclass(frozen=True)
class MeetingStatusDerivation:
    def of(self, meeting: ScheduledMeeting, as_of: date) -> MeetingStatus:
        """`upcoming` while the day has not passed and nobody has ticked it.

        **`>=`, not `>`**: a meeting scheduled for this afternoon is upcoming
        all day. The column is a calendar date with an optional wall-clock time
        beside it, and judging "has it happened yet" from the time would need a
        timezone this console does not collect — so the honest granularity is
        the day, and the day it falls on counts as ahead.
        """
        if meeting.is_done:
            return MeetingStatus.done
        return MeetingStatus.upcoming if meeting.meeting_date >= as_of else MeetingStatus.done


meeting_status = register(
    Derivation(
        name="meeting_status",
        describes=(
            "whether a scheduled meeting is still ahead — upcoming when its date "
            "is on or after the given day and nobody has marked it complete, done "
            "otherwise"
        ),
        build=lambda _thresholds: MeetingStatusDerivation(),
    )
)
