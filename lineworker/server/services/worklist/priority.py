"""The priority score — computed **exactly once**, here (AD-2, FR-Q-4).

Epic 5's top-30 worklist and the supervisor dashboard will import
`priority_score` unchanged; the copilot will quote it through a read tool.
That is the whole reason it is a pure function over two small projections
rather than a method on an ORM entity or a step inside the queue assembly:
anything that can describe a claim can be ranked, without a session, a
request, or a database.

**Where each half of the rule lives (AD-8).** Every parameter in the formula
— each weight, the severity and days-open factors, the days-open cap, the
settled penalty, and the set of statuses that counts as awaiting a decision
— arrives in `PriorityWeights`, read from the versioned `priority_weights`
JDM document. This module holds the *arithmetic* and not one constant. Grep
it: there is no literal, and no hardcoded status, in the scoring function.
That is what makes "re-rank the portfolio without a deploy" a data change,
and what lets `tests/test_rules_engine.py` prove the ordering shifts when the
document does — against a real second document version, not a mutated
dataclass.

The status set was the last thing here that was not data. It lived as a
Python `frozenset` beside its own weight in the document, so retuning "which
statuses are pending approval" was a deploy while retuning what they are
worth was a migration: one rule element in two tiers, which is exactly what
AD-8 forbids. `rules/parameters.py` now reads it from the document and
resolves every member against `ClaimStatus` before a claim is scored.

**Why the claim and its flags are separate arguments.** They have different
provenance and the signature says so. `QueueClaim` is a row the repository
read under the caller's scope (AD-7); `QueueFlags` is what the derivations
registry computed from it (AD-10). Bundling them would let a future caller
hand-fill a flag beside a real column and nothing would notice — which is
precisely the queue-and-detail disagreement AD-10 exists to prevent.

**Why `settled` subtracts instead of being excluded.** A settled claim is
still in the caseload, still openable, and still countable; it simply must
not compete with live work. Subtracting a constant does two useful things at
once: inside the settled group every member pays it, so it cancels and the
group still ranks by how much attention each claim *would* have needed;
across groups it biases settled work downwards. It is a bias and not a
floor — the seeded categorical weights total 165 against a −100 penalty, so
a settled claim carrying every flag still outscores a quiet live one. That
is harmless while the queue is grouped by stage and the two never share a
list; Epic 5's ungrouped top-30 inherits this scorer and will have to decide
whether it is harmless there (`test_priority_score.py` says so out loud).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from data.models.enums import ClaimStatus, Stage
from rules.parameters import PriorityWeights
from services.derivations import RiskBand


class QueueFilter(StrEnum):
    """The eight operational filters (FR-Q-1), snake_case per the enum
    convention — "High risk" and "Payment due" are the UI's labels, not the
    wire's.

    `active` rather than `treatment`: the prototype's option reads "Active /
    In treatment", and the filter's meaning is "work in progress". It
    happens to be the treatment stage today; naming it after the stage would
    freeze that coincidence into the contract.
    """

    all = "all"
    active = "active"
    high_risk = "high_risk"
    fraud = "fraud"
    litigation = "litigation"
    payment_due = "payment_due"
    surgery = "surgery"
    siu = "siu"


@dataclass(frozen=True)
class QueueClaim:
    """One claim reduced to the facts a queue card shows and the score reads.

    A projection rather than the ORM entity, for `SlaSample`'s reasons: it
    keeps the scorer pure and generatable, and it puts the inputs to the
    formula next to each other instead of spread across a row object with
    forty other columns.

    `days_open` is here rather than `froi_date` because it is *derived*
    (`services/derivations/open_duration`), and the scorer must consume the
    registry's answer rather than recompute an age from a date — the same
    rule that keeps `risk` in `QueueFlags`.
    """

    claim_id: str
    stage: Stage
    status: ClaimStatus
    severity_score: int
    days_open: int
    injury_type: str
    worker_name: str
    employer_short_name: str
    surgery_required: bool
    litigation_flag: bool
    fraud_flag: bool


@dataclass(frozen=True)
class QueueFlags:
    """The AD-10 derived values for one claim, as the registry computed them.

    Every field here has exactly one computing function in
    `services/derivations`. Nothing in this module or the one above it may
    produce one of these another way.
    """

    risk: RiskBand
    siu_review: bool
    rtw_blocked: bool
    payment_due: bool


def priority_score(claim: QueueClaim, flags: QueueFlags, weights: PriorityWeights) -> float:
    """Rank one claim. Pure, total, and free of every parameter it uses.

    "Free of every parameter" is literal and checkable: there is no number
    and no status name below, only `weights.*` and the claim's own columns.
    `test_no_weight_is_hardcoded_in_the_scorer` zeroes the whole block and
    asserts the answer is zero, which no stray `+ 40` would survive.
    """
    score = 0.0
    if claim.litigation_flag:
        score += weights.litigation
    if flags.siu_review:
        score += weights.siu_review
    if flags.rtw_blocked:
        score += weights.rtw_blocked
    # "Waiting on us to decide" — the document's list, not this module's.
    # A claim nobody has approved or denied is a claim whose clock is
    # running with no treatment authorized, which is why the seeded weight
    # puts it above a payment that is merely due.
    if claim.status in weights.pending_approval_statuses:
        score += weights.pending_approval
    if flags.payment_due:
        score += weights.payment_due
    if claim.surgery_required:
        score += weights.surgery
    score += claim.severity_score * weights.severity_factor
    # Capped: an eighteen-month-old claim is not nine times more urgent than
    # a two-month-old one, and without the cap the age term would eventually
    # dominate every categorical signal in the formula.
    score += min(claim.days_open, weights.days_open_cap) * weights.days_open_factor
    if claim.stage is Stage.settled:
        # `+=`, not `-=`: the penalty is negative *in the document*, so the
        # sign is a parameter like everything else. Spelling it as a
        # subtraction here would mean a document that set it positive did
        # the opposite of what it said.
        score += weights.settled_penalty
    return score


def priority_markers(scores: Sequence[float], weights: PriorityWeights) -> list[bool]:
    """Which of an already-ranked list carry the 🔺 — one bool per score.

    Here rather than in `queue.py` for the reason `priority_score` is here:
    the marker is the second half of the same worklist rule, and Epic 5's
    top-30 will want it too. Left buried in the queue's grouping loop, the
    next consumer would re-derive "the top few, if they are high enough"
    from the prose and the two would drift — the AD-2/AD-10 failure mode,
    one rule with two implementations.

    The rule is `markerCount` items with `score > markerThreshold`, in rank
    order. Strictly greater, matching the prototype's `priorityScore(c)>30`.

    **Descending order is a precondition, and it is enforced.** The caller
    owns the sort (and its tie-break), because the marker cannot be decided
    independently of the order it is decided in. The prototype expressed the
    rule positionally — `i<3 && priorityScore(c)>30` over its rendered list
    — and this expresses it as a budget spent on marked claims. Over a
    descending sequence the two are the same function; over an unsorted one
    they are not, and the prototype's would hand a marker to whatever
    happened to land in the first three slots. Rather than pick a winner for
    a case that means nothing either way, the precondition is checked: an
    unsorted `scores` is a caller bug (the queue sorts in `_ranked_group`,
    Epic 5's ungrouped top-30 will have to sort too), and a caller bug that
    silently produces plausible markers is the one this function is most
    likely to be handed. `ValueError`, not a quiet re-sort — re-sorting here
    would give the caller back markers that do not line up with the list it
    is about to render them against.

    **The marker describes the top of the list it is computed over.** The
    queue computes it after filtering, so the same claim can carry the
    marker under `all` and not under `high_risk` — it is genuinely a
    different list, and marking the top of the *unfiltered* group would put
    the 🔺 on nothing a filtered handler can see. What it is deliberately
    *not* sensitive to is paging: it is decided over the whole filtered
    group, before any slice, so asking for page 2 can never make a fourth
    claim sprout one.
    """
    for position, (higher, lower) in enumerate(zip(scores, scores[1:], strict=False)):
        if lower > higher:
            raise ValueError(
                f"priority_markers needs a descending sequence; position {position} scores "
                f"{higher} and position {position + 1} scores {lower}. Sort before marking — "
                "the marker is a statement about the top of a ranked list."
            )

    marked = 0
    markers: list[bool] = []
    for score in scores:
        marker = marked < weights.marker_count and score > weights.marker_threshold
        if marker:
            marked += 1
        markers.append(marker)
    return markers


# One predicate per filter, in one mapping — total by construction, and
# checkable as such (`test_priority_score.py` asserts every member has an
# entry). A `match` statement would read the same and pass mypy, but nothing
# would notice a ninth filter added to the enum without a branch.
_PREDICATES: Mapping[QueueFilter, Callable[[QueueClaim, QueueFlags], bool]] = {
    QueueFilter.all: lambda _claim, _flags: True,
    QueueFilter.active: lambda claim, _flags: claim.stage is Stage.treatment,
    QueueFilter.high_risk: lambda _claim, flags: flags.risk is RiskBand.high,
    QueueFilter.fraud: lambda claim, _flags: claim.fraud_flag,
    QueueFilter.litigation: lambda claim, _flags: claim.litigation_flag,
    QueueFilter.payment_due: lambda _claim, flags: flags.payment_due,
    QueueFilter.surgery: lambda claim, _flags: claim.surgery_required,
    QueueFilter.siu: lambda _claim, flags: flags.siu_review,
}


def matches(queue_filter: QueueFilter, claim: QueueClaim, flags: QueueFlags) -> bool:
    """Does this claim belong in the filtered queue?

    Decided here, over the *derived* row, rather than as a SQL predicate in
    the repository — see `select_queue_rows`. Three of the eight filters read
    values no column carries, and a filter set split between the two tiers
    would be two encodings of one rule.
    """
    return _PREDICATES[queue_filter](claim, flags)
