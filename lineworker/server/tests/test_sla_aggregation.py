"""Story 1.5 AC 1/3/4 — the SLA aggregation, without a database.

This file covers the *pure* half of the story: given a scoped caseload,
what does the strip say? Everything here runs in the lint job, so the
invariants that matter most (a number is never invented, a status always
agrees with the number beside it) are checked on every push.

The named cases from AC 4 — empty, all-passing, all-warning — are unit
tests. The invariants that should hold for *any* caseload are Hypothesis
properties (NFR-7), because the interesting failures in an aggregation are
the caseloads nobody thought to write down: one claim, all-null columns,
settled claims with no settlement duration, a book with a single settled
claim that did return to work.

The oracle is deliberately restated here (`_mean`, the target numbers)
rather than imported from the code under test — a test that computes its
expectation with the implementation agrees with it no matter what either
of them does.
"""

import re
from collections.abc import Sequence
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from config import Settings
from services.worklist import sla

SERVER_ROOT = Path(__file__).resolve().parents[1]

# The targets, restated independently of `config.py` (see module docstring).
PICK_TARGET = 1.0
APPROVE_TARGET = 5.0
SETTLE_TARGET = 30.0
RTW_TARGET = 80.0

TARGETS = Settings(database_url="postgresql://x/y")

# The AD-2 guard's pattern (used by two tests below: one that greps the
# tree with it, one that proves it still matches what it is meant to).
# No `.` in the lookbehind — `Claim.sla_pick_days` is the idiom being
# guarded against, not an exception to it.
SLA_COLUMN_READ = re.compile(r"(?<!\w)(sla_pick_days|sla_approve_days|settlement_days)(?!\w)")


def sample(
    *,
    pick: int | None = None,
    approve: int | None = None,
    settlement: int | None = None,
    settled: bool = False,
    recovered: bool = False,
) -> sla.SlaSample:
    return sla.SlaSample(
        pick_days=pick,
        approve_days=approve,
        settlement_days=settlement,
        is_settled=settled,
        fully_recovered=recovered,
    )


def strip(samples: Sequence[sla.SlaSample]) -> dict[sla.SlaMetricKey, sla.SlaMetric]:
    return dict(sla.strip_of(samples, sla.targets_for(TARGETS)))


K = sla.SlaMetricKey
S = sla.SlaStatus


# --- AC 4: the three named cases ---------------------------------------


def test_an_empty_caseload_reports_no_data_and_never_a_number() -> None:
    """The prototype answered 7.4d / 42d / 87% here. That is the bug.

    A brand-new handler with an empty book must not be shown someone
    else's averages — and there is no honest number to show, so every tile
    says so.
    """
    result = strip([])

    assert set(result) == set(K)
    for key, metric in result.items():
        assert metric.value is None, f"{key} invented a value for an empty caseload"
        assert metric.status is S.no_data


def test_an_all_passing_caseload_reports_four_passes() -> None:
    caseload = [
        sample(pick=0, approve=2, settlement=10, settled=True, recovered=True),
        sample(pick=1, approve=4, settlement=20, settled=True, recovered=True),
    ]

    result = strip(caseload)

    assert result[K.pick].value == 0.5
    assert result[K.approve].value == 3.0
    assert result[K.settle].value == 15
    assert result[K.rtw_rate].value == 100
    assert [result[key].status for key in K] == [S.passing] * 4


def test_an_all_warning_caseload_reports_four_warnings() -> None:
    caseload = [
        sample(pick=3, approve=9, settlement=60, settled=True, recovered=False),
        sample(pick=5, approve=11, settlement=80, settled=True, recovered=False),
    ]

    result = strip(caseload)

    assert result[K.pick].value == 4.0
    assert result[K.approve].value == 10.0
    assert result[K.settle].value == 70
    assert result[K.rtw_rate].value == 0
    assert [result[key].status for key in K] == [S.warn] * 4


# --- segment-empty cases (Task 4) --------------------------------------


def test_a_caseload_with_nothing_settled_still_computes_pick_and_approve() -> None:
    """Partial data is the common case, and it is not an error.

    Every open claim has a pick and an approve duration; none has a
    settlement or a return-to-work outcome. Two tiles answer, two say they
    cannot — rather than the whole strip going dark, or the settled tiles
    borrowing a number.
    """
    result = strip([sample(pick=0, approve=2), sample(pick=1, approve=4)])

    assert result[K.pick].value == 0.5
    assert result[K.approve].value == 3.0
    assert result[K.settle].value is None
    assert result[K.settle].status is S.no_data
    assert result[K.rtw_rate].value is None
    assert result[K.rtw_rate].status is S.no_data


def test_settled_claims_without_a_duration_still_count_towards_the_rtw_rate() -> None:
    """The two settled metrics have different denominators, on purpose.

    Settle averages the settled claims that *have* a duration; RTW rate is
    over *all* settled claims, because a settlement with no recorded
    duration is still a settlement with a return-to-work outcome. Sharing
    one denominator would quietly drop those claims from the rate.
    """
    result = strip(
        [
            sample(settlement=10, settled=True, recovered=True),
            sample(settlement=None, settled=True, recovered=False),
        ]
    )

    assert result[K.settle].value == 10
    assert result[K.rtw_rate].value == 50


def test_a_metric_ignores_claims_whose_column_is_null_rather_than_defaulting_it() -> None:
    """`c.slaPickDays || 1` — the prototype's other silent fabrication.

    A missing duration is unknown, not one day. Averaging it as 1 drags
    every average towards the target and makes a book with no data at all
    look like a book that hits it.
    """
    result = strip([sample(pick=6), sample(pick=None), sample(pick=None)])

    assert result[K.pick].value == 6.0, "a null pick duration was counted as a value"


def test_a_caseload_with_no_pick_durations_at_all_reports_no_data() -> None:
    result = strip([sample(approve=2), sample(approve=4)])

    assert result[K.pick].value is None
    assert result[K.pick].status is S.no_data
    assert result[K.approve].value == 3.0


# --- rounding (Task 1: display precision at the service edge) ----------


@pytest.mark.parametrize(
    ("durations", "expected"),
    [
        ([1, 2], 1.5),
        ([1, 1, 2], 1.3),  # 1.333… → one decimal
        ([2, 2, 3], 2.3),  # 2.333…
        ([1, 2, 2], 1.7),  # 1.666… rounds up
        ([0, 1, 1], 0.7),  # 0.666…
    ],
)
def test_pick_and_approve_round_to_one_decimal(durations: list[int], expected: float) -> None:
    assert strip([sample(pick=d, approve=d) for d in durations])[K.pick].value == expected
    assert strip([sample(pick=d, approve=d) for d in durations])[K.approve].value == expected


@pytest.mark.parametrize(
    ("durations", "expected"),
    [([10, 11], 11), ([10, 10, 11], 10), ([29, 30], 30), ([1, 2], 2)],
)
def test_settle_rounds_to_whole_days_half_up(durations: list[int], expected: int) -> None:
    """Half-up, not Python's bankers' rounding.

    `round(10.5)` is 10 and `round(11.5)` is 12 — a display that rounds one
    way at 10.5 and the other at 11.5 is indefensible on a screen, and the
    inconsistency shows up as a one-day disagreement between this tile and
    any other view of the same average.
    """
    settle = strip([sample(settlement=d, settled=True) for d in durations])[K.settle]
    assert settle.value == expected


def test_the_rtw_rate_rounds_to_a_whole_percent() -> None:
    caseload = [sample(settled=True, recovered=i < 2) for i in range(3)]

    assert strip(caseload)[K.rtw_rate].value == 67  # 66.66…


def test_the_status_is_decided_on_the_value_that_is_reported() -> None:
    """29.6 days displays as `30d`, so it must not also say it passed.

    Status is computed after rounding for exactly this reason: a tile that
    reads `30d ✓ <30d` is a bug report waiting to happen, and the rounded
    number is the one the user can see.
    """
    caseload = [sample(settlement=29, settled=True)] * 2 + [sample(settlement=31, settled=True)] * 3
    result = strip(caseload)  # mean 30.2 → 30

    assert result[K.settle].value == 30
    assert result[K.settle].status is S.warn


# --- AC 2/3: pass/warn against configured targets -----------------------


def test_the_days_metrics_pass_strictly_below_their_target() -> None:
    at_target = strip([sample(pick=1, approve=5, settlement=30, settled=True)])

    assert at_target[K.pick].status is S.warn
    assert at_target[K.approve].status is S.warn
    assert at_target[K.settle].status is S.warn


def test_the_rtw_rate_passes_strictly_above_its_target() -> None:
    """80% exactly is a miss: the target reads `> 80%` (AC 2)."""
    caseload = [sample(settled=True, recovered=i < 8) for i in range(10)]

    assert strip(caseload)[K.rtw_rate].value == 80
    assert strip(caseload)[K.rtw_rate].status is S.warn


def test_every_metric_carries_its_target_and_the_direction_it_is_compared_in() -> None:
    result = strip([])

    assert result[K.pick].target == PICK_TARGET
    assert result[K.approve].target == APPROVE_TARGET
    assert result[K.settle].target == SETTLE_TARGET
    assert result[K.rtw_rate].target == RTW_TARGET

    assert result[K.rtw_rate].direction is sla.SlaDirection.above
    for key in (K.pick, K.approve, K.settle):
        assert result[key].direction is sla.SlaDirection.below


def test_the_targets_move_when_the_configuration_moves() -> None:
    """AC 3 made checkable: the numbers are parameters, not code.

    A book that misses a 1-day pick target hits a 10-day one. If this test
    ever fails, a literal has crept back into the aggregation.
    """
    caseload = [sample(pick=4, approve=4, settlement=40, settled=True, recovered=True)]

    strict = dict(sla.strip_of(caseload, sla.targets_for(TARGETS)))
    lenient = dict(
        sla.strip_of(
            caseload,
            sla.targets_for(
                TARGETS.model_copy(
                    update={
                        "sla_pick_target_days": 10.0,
                        "sla_approve_target_days": 10.0,
                        "sla_settle_target_days": 100.0,
                        "sla_rtw_target_pct": 99.0,
                    }
                )
            ),
        )
    )

    assert [strict[key].status for key in K] == [S.warn, S.passing, S.warn, S.passing]
    assert [lenient[key].status for key in K] == [S.passing, S.passing, S.passing, S.passing]


# --- Hypothesis properties (NFR-7) --------------------------------------

durations = st.integers(min_value=0, max_value=400)
caseloads = st.lists(
    st.builds(
        sla.SlaSample,
        pick_days=st.none() | durations,
        approve_days=st.none() | durations,
        settlement_days=st.none() | durations,
        is_settled=st.booleans(),
        fully_recovered=st.booleans(),
    ),
    max_size=60,
)


@given(caseloads)
@hyp_settings(max_examples=300)
def test_status_always_agrees_with_the_value_beside_it(
    caseload: list[sla.SlaSample],
) -> None:
    """The invariant a user reads off the screen: the tick matches the number."""
    for key, metric in strip(caseload).items():
        if metric.value is None:
            assert metric.status is S.no_data, f"{key} has no value but claims a verdict"
            continue
        expected = (
            metric.value < metric.target
            if metric.direction is sla.SlaDirection.below
            else metric.value > metric.target
        )
        assert (metric.status is S.passing) == expected, f"{key} disagrees with its own number"
        assert metric.status is not S.no_data


@given(caseloads)
@hyp_settings(max_examples=300)
def test_an_average_always_lies_between_the_smallest_and_largest_input(
    caseload: list[sla.SlaSample],
) -> None:
    result = strip(caseload)

    picks = [s.pick_days for s in caseload if s.pick_days is not None]
    approves = [s.approve_days for s in caseload if s.approve_days is not None]
    settles = [
        s.settlement_days for s in caseload if s.is_settled and s.settlement_days is not None
    ]

    for key, inputs in ((K.pick, picks), (K.approve, approves), (K.settle, settles)):
        value = result[key].value
        if not inputs:
            assert value is None
            continue
        assert value is not None
        assert min(inputs) <= value <= max(inputs), f"{key}={value} is outside {inputs}"


@given(caseloads)
@hyp_settings(max_examples=300)
def test_the_rtw_rate_is_a_percentage_of_the_settled_claims(
    caseload: list[sla.SlaSample],
) -> None:
    settled = [s for s in caseload if s.is_settled]
    rate = strip(caseload)[K.rtw_rate]

    if not settled:
        assert rate.value is None
        return
    assert rate.value is not None
    assert 0 <= rate.value <= 100
    if all(s.fully_recovered for s in settled):
        assert rate.value == 100
    if not any(s.fully_recovered for s in settled):
        assert rate.value == 0


@given(caseloads)
@hyp_settings(max_examples=300)
def test_no_metric_ever_invents_a_value_for_an_empty_segment(
    caseload: list[sla.SlaSample],
) -> None:
    """The story's central prohibition, as a property over every caseload."""
    result = strip(caseload)
    empty = {
        K.pick: not any(s.pick_days is not None for s in caseload),
        K.approve: not any(s.approve_days is not None for s in caseload),
        K.settle: not any(s.is_settled and s.settlement_days is not None for s in caseload),
        K.rtw_rate: not any(s.is_settled for s in caseload),
    }
    for key, is_empty in empty.items():
        assert (result[key].value is None) == is_empty, f"{key} disagrees about having data"


# --- AD-2: the aggregation exists exactly once --------------------------


def test_nothing_outside_the_worklist_aggregation_reads_the_sla_source_columns() -> None:
    """AD-2's binding rule, enforced the way 1.4 enforced the risk band.

    Story 5.3's dashboard tiles and the copilot's later narration must call
    `services.worklist.sla`, not average `sla_pick_days` again next to it.
    Grep is blunt on purpose: a second reader of these columns either calls
    the aggregation or argues for an allowlist entry in review.

    **What the rule actually forbids, sharpened by Story 2.2.** It is a
    second *aggregation*, not any mention of the column. The settled overview
    shows one claim's "days to settlement", which the prototype computes as
    `settlement_days || days_open` — a fallback rule over a single row,
    nothing an average could disagree with. The entry below is what that
    argument looks like when it is made once, in a registered derivation,
    rather than repeated at whichever card needs the number: `days_to_settlement`
    has exactly one computer for the same reason every other derived value
    does (AD-10), and the alternative was this guard collecting a service
    module per consuming screen.
    """
    allowed = {
        SERVER_ROOT / "services" / "worklist" / "sla.py",
        SERVER_ROOT / "data" / "models" / "core.py",  # the columns are declared here
        # Story 2.2: the registered `days_to_settlement` derivation — a
        # per-claim fallback, not an aggregate. See the docstring above.
        SERVER_ROOT / "services" / "derivations" / "open_duration.py",
        *(SERVER_ROOT / "tests").rglob("*.py"),
        *(SERVER_ROOT / "data" / "seed").rglob("*.py"),
        *(SERVER_ROOT / "data" / "versions").rglob("*.py"),  # frozen migrations
    }

    offenders = [
        path.relative_to(SERVER_ROOT)
        for path in SERVER_ROOT.rglob("*.py")
        if path not in allowed
        and "__pycache__" not in path.parts
        and ".venv" not in path.parts
        and SLA_COLUMN_READ.search(path.read_text())
    ]
    assert not offenders, (
        f"{offenders} read an SLA source column directly — call "
        "services.worklist.sla instead (AD-2), or justify an allowlist entry"
    )


@pytest.mark.parametrize(
    "violation",
    [
        "sa.func.avg(Claim.sla_pick_days)",
        "select(Claim.settlement_days)",
        "claim.sla_approve_days",
        "    sla_pick_days: Mapped[int | None]",
        'row["settlement_days"]',
    ],
)
def test_the_ad2_guard_matches_the_idioms_a_second_aggregation_would_use(violation: str) -> None:
    """The guard's own regression test — it was vacuous without one.

    The pattern started life as a copy of Story 1.4's threshold guard,
    whose lookbehind excludes a preceding `.` so that `1.65` is not read as
    the number 65. Carried over to attribute *names*, that exclusion made
    the guard blind to `Claim.sla_pick_days` — the one form a duplicate
    aggregation would actually be written in — so the test above passed
    while enforcing nothing. Nothing in a grep-style guard tells you it has
    stopped matching, so the matching is now asserted directly.
    """
    assert SLA_COLUMN_READ.search(violation), f"the AD-2 guard would not catch {violation!r}"


@pytest.mark.parametrize(
    "innocent",
    ["days_recovery", "sla_pick_days_total", "presettlement_days", "sla_settle_days"],
)
def test_the_ad2_guard_does_not_fire_on_unrelated_names(innocent: str) -> None:
    """A guard that matches too much gets an allowlist entry per story and
    stops meaning anything — so the boundaries are pinned too. (`sla_settle_days`
    is a real seeded column the strip deliberately does not use: the Settle
    metric averages `settlement_days`, as the prototype's `recalcSLA` does.)"""
    assert not SLA_COLUMN_READ.search(innocent)


def test_the_aggregation_names_no_target_of_its_own() -> None:
    """AC 3 structurally: no literal target survives in the formula module.

    Weaker than a grep for `80` (which would trip over any percentage) and
    stronger than reading the code: every target the strip reports must be
    traceable to a `Settings` field by name.
    """
    source = (SERVER_ROOT / "services" / "worklist" / "sla.py").read_text()
    fields = re.findall(r"sla_\w*target\w*", source)

    assert set(fields) == {
        "sla_pick_target_days",
        "sla_approve_target_days",
        "sla_settle_target_days",
        "sla_rtw_target_pct",
    }
    for field in set(fields):
        assert field in Settings.model_fields, f"{field} is not a configured parameter"
