"""`next_batch_date` — the disbursement calendar (Story 3.4, AD-10).

Pure, so this file needs no database and can range over a whole year in
milliseconds. What it is checking is one rule with one boundary, and the
boundary is the interesting half: **the next batch is never today.**

The prototype's loop starts at `i = 1`, which reads like an off-by-one and is
not. The sentence this date appears in sits beside an approval the handler is
about to make, and today's batch may already have run — so promising same-day
disbursement for money approved an hour after the file went out is the one
direction this must not be wrong in.
"""

from datetime import date, timedelta

import pytest

from services import derivations
from services.derivations.batch_calendar import NextBatchDateDerivation

#: The default cadence, restated as an oracle rather than imported from
#: `Settings`: a test that read the configuration under test would agree with
#: any cadence at all.
TUE_FRI = frozenset({1, 4})

#: 2026-08-10 is a Monday. Every date below is expressed as an offset from it,
#: so the weekday of each is readable rather than something to look up.
MONDAY = date(2026, 8, 10)


def day(offset: int) -> date:
    return MONDAY + timedelta(days=offset)


def next_batch(as_of: date, weekdays: frozenset[int] = TUE_FRI) -> date:
    return NextBatchDateDerivation().of(as_of, weekdays)


# --- the rule ------------------------------------------------------------


@pytest.mark.parametrize(
    "offset, expected_offset",
    [
        (0, 1),  # Monday   -> Tuesday
        (1, 4),  # Tuesday  -> Friday      (never today)
        (2, 4),  # Wednesday-> Friday
        (3, 4),  # Thursday -> Friday
        (4, 8),  # Friday   -> next Tuesday (never today)
        (5, 8),  # Saturday -> Tuesday
        (6, 8),  # Sunday   -> Tuesday
    ],
)
def test_the_next_batch_is_the_next_configured_day(offset: int, expected_offset: int) -> None:
    assert next_batch(day(offset)) == day(expected_offset)


@pytest.mark.parametrize("offset", range(370))
def test_the_answer_is_always_strictly_in_the_future(offset: int) -> None:
    """Over a year and change, so no month, quarter or year boundary is
    special. The two properties together are the whole contract: it is a batch
    day, and it is not today."""
    as_of = day(offset)
    answer = next_batch(as_of)
    assert answer > as_of
    assert answer.weekday() in TUE_FRI
    # And it is the *first* one: nothing between today and the answer is a
    # batch day.
    between = [as_of + timedelta(days=n) for n in range(1, (answer - as_of).days)]
    assert not any(candidate.weekday() in TUE_FRI for candidate in between)


def test_a_daily_cadence_answers_tomorrow() -> None:
    """The degenerate case, and the one that pins "strictly after": with every
    day a batch day the answer is tomorrow, not today."""
    every_day = frozenset(range(7))
    assert next_batch(MONDAY, every_day) == day(1)


def test_a_single_day_cadence_wraps_a_whole_week() -> None:
    """A Monday-only cadence, asked on a Monday, answers next Monday — the
    furthest the search ever has to look, and the case that would fall off the
    end of a loop bounded at six."""
    assert next_batch(MONDAY, frozenset({0})) == day(7)


def test_an_empty_cadence_raises_rather_than_looping() -> None:
    """Unreachable through `Settings`, which refuses an empty cadence at read
    time — this is the second line of defence, and it is a sentence rather
    than an `UnboundLocalError`."""
    with pytest.raises(ValueError, match="never run"):
        next_batch(MONDAY, frozenset())


def test_the_cadence_is_configuration_rather_than_a_rule_parameter() -> None:
    """Two deployments, two answers, one function.

    Which weekdays a carrier's disbursement file goes out is a deployment
    fact, not an AD-8 business threshold — so it is an argument here rather
    than something read from `DerivationThresholds`.
    """
    assert next_batch(MONDAY, frozenset({0, 3})) == day(3)  # Mon/Thu -> Thursday
    assert next_batch(MONDAY, TUE_FRI) == day(1)


# --- the registry --------------------------------------------------------


def test_next_batch_date_is_registered_under_its_canonical_name() -> None:
    """AD-10: three surfaces render this date — the Bills summary's note and
    both approval sheets — and each asks the registry rather than adding days
    to today until it hits a Tuesday."""
    assert "next_batch_date" in derivations.registered_names()
    assert derivations.get("next_batch_date") is derivations.next_batch_date
