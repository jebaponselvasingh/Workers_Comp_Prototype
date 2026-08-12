"""`coordination_status` — whether care and return-to-work are on track.

The prototype's `coordinationCheck` (line 1319), ported as a registered
derivation (AC 5). Four outcomes, decided in a fixed order, and **the order is
the rule**: a claim can satisfy more than one condition at once, and the first
match wins because the list is ordered by what a handler has to act on first.

    rtw_blocked        → a return date has passed with nothing confirmed
    awaiting info      → the carrier is waiting on documents
    litigation         → everything routes through counsel
    otherwise          → on track

A blocked return outranks a paperwork gap; a paperwork gap outranks the
standing instruction that a represented claim is coordinated through counsel.
A set of independent booleans would let the card show "Legal Coordination" on
a claim whose return date lapsed a month ago, which is the one of the four a
handler must not miss.

**Parameterless, and registered anyway** — `days_open`'s argument. The
registry answers "who computes this?", not "what is configurable?", and the
alternative to registering it is a helper that a second surface quietly
reimplements. Its one *input* that is itself a rule, `rtw_blocked`, is passed
in rather than recomputed here: that derivation has exactly one computer
(`queue_flags`), which is what stops the queue card's PAY DUE / blocked state
and this card from disagreeing about the same claim.

**Which comm statuses count as "awaiting information".** The prototype tests
the *display* string with `/Need for Additional Information|Incomplete
Information/i`. Here the column is a `comm_status` enum, so the two members
are named directly — a regex over a snake_case token would be a string match
pretending to be a rule, and it would silently stop matching the day a label
changed. Deliberately **not** a JDM parameter, unlike the queue's
`pendingApprovalStatuses`: that one is a weight's eligibility list, an
operator's tuning knob. This is the definition of what "awaiting information"
*means*, and there is no version of this rule where a claim whose status is
literally "Need for Additional Information" is not waiting on information.

**Notes are static per branch.** The prototype interpolated the communication
status into two of its four sentences; here the note is a statement about the
coordination rule and the claim's communication status is a field of the same
card, rendered for every branch rather than only two. Two reasons: the
sentence stays a description of the rule (so it lives with the rule), and the
enum keeps its UI-owned display label instead of the server rendering one.
"""

from dataclasses import dataclass
from enum import StrEnum

from data.models.enums import CommStatus
from services.derivations.registry import Derivation, register

AWAITING_INFORMATION_STATUSES = frozenset(
    {CommStatus.need_for_additional_information, CommStatus.incomplete_information}
)


class CoordinationStatus(StrEnum):
    """Snake_case values per the enum convention — the UI owns labels."""

    coordination_gap = "coordination_gap"
    awaiting_information = "awaiting_information"
    legal_coordination = "legal_coordination"
    on_track = "on_track"


# The prototype's four sentences, with the interpolated communication status
# lifted out (see the module docstring).
STATUS_NOTES: dict[CoordinationStatus, str] = {
    CoordinationStatus.coordination_gap: (
        "RTW follow-up is overdue — recommended return date has passed with no "
        "confirmed update from employer or claimant."
    ),
    CoordinationStatus.awaiting_information: (
        "Outstanding information is holding up coordination."
    ),
    CoordinationStatus.legal_coordination: (
        "Attorney represented — coordinate all RTW/treatment communication through counsel."
    ),
    CoordinationStatus.on_track: (
        "Employer, claimant, and provider are aligned — no gaps identified."
    ),
}


@dataclass(frozen=True)
class CoordinationResult:
    status: CoordinationStatus
    note: str


@dataclass(frozen=True)
class CoordinationDerivation:
    """First match wins, in the order declared in the module docstring."""

    def of(
        self,
        *,
        rtw_blocked: bool,
        comm_status: CommStatus,
        litigation_flag: bool,
    ) -> CoordinationResult:
        if rtw_blocked:
            return self._result(CoordinationStatus.coordination_gap)
        if comm_status in AWAITING_INFORMATION_STATUSES:
            return self._result(CoordinationStatus.awaiting_information)
        if litigation_flag:
            return self._result(CoordinationStatus.legal_coordination)
        return self._result(CoordinationStatus.on_track)

    @staticmethod
    def _result(status: CoordinationStatus) -> CoordinationResult:
        return CoordinationResult(status=status, note=STATUS_NOTES[status])


coordination_status = register(
    Derivation(
        name="coordination_status",
        describes="care/RTW coordination state: blocked return, awaiting info, legal, or on track",
        build=lambda _thresholds: CoordinationDerivation(),
    )
)
