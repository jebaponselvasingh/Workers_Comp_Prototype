"""Story 2.1 — the queue's cursor, sort, marker and paging, without a database.

`test_claims_queue.py` drives the whole endpoint against a migrated Postgres
and is the better test of *what the queue says*. It is also skipped entirely
on a laptop with no `MIGRATION_TEST_DATABASE_URL`, which for a while meant
the sorting, grouping, paging and cursor code in `services/worklist/queue.py`
had no coverage at all in a default `pytest` run. Everything below is pure:
dataclasses in, dataclasses out, no session, no seed.

Two things it covers that the DB-backed suite structurally cannot:

- **The tie-break.** Three docstrings call `(-score, claim_id)` load-bearing
  — it is what stops a cursor into a group repeating one claim and dropping
  another — and the seeded portfolio contains no tie, so the endpoint tests
  never exercise it. Here a tie is constructed on purpose.
- **A forged cursor.** `decode_cursor` is reached through the wire with
  strings a client could send; the interesting inputs are the ones a client
  could *build*, which means encoding a payload directly rather than
  round-tripping a real one.

The weights below are an arbitrary, readable block — **not** the seeded
document. Nothing here asserts what a weight is worth (that is
`test_rules_engine.py`'s job against the committed JSON); these tests assert
that the assembly reads whatever block it is handed.
"""

import base64
import json
from dataclasses import replace
from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from data.models.enums import ClaimStatus, Stage
from rules.parameters import PriorityWeights
from services.derivations import RiskBand
from services.worklist.priority import QueueClaim, QueueFilter, QueueFlags, priority_score
from services.worklist.queue import (
    MAX_CURSOR_AGE,
    MAX_PAGE_LIMIT,
    MIN_PAGE_LIMIT,
    Cursor,
    InvalidCursor,
    QueueCard,
    StageGroup,
    _page,
    _ranked_group,
    decode_cursor,
    encode_cursor,
)

TODAY = date(2026, 8, 11)

WEIGHTS = PriorityWeights(
    version=3,
    litigation=100,
    siu_review=0,
    rtw_blocked=0,
    pending_approval=0,
    pending_approval_statuses=frozenset(),
    payment_due=0,
    surgery=10,
    severity_factor=1,
    days_open_factor=0,
    days_open_cap=60,
    settled_penalty=-1000,
    marker_threshold=30,
    marker_count=3,
    page_limit=50,
)

FLAGS = QueueFlags(risk=RiskBand.low, siu_review=False, rtw_blocked=False, payment_due=False)


def claim(claim_id: str, *, severity: int = 0, stage: Stage = Stage.treatment) -> QueueClaim:
    return QueueClaim(
        claim_id=claim_id,
        stage=stage,
        status=ClaimStatus.ch_approved,
        severity_score=severity,
        days_open=0,
        injury_type="Laceration",
        worker_name="Dana Reyes",
        employer_short_name="3M",
        surgery_required=False,
        litigation_flag=False,
        fraud_flag=False,
    )


CURSOR = Cursor(
    queue_filter=QueueFilter.all,
    stage=Stage.settled,
    offset=5,
    rules_version=1,
    thresholds_version=1,
    as_of=TODAY,
    limit=5,
)


def forge(**fields: object) -> str:
    """A cursor built from a payload rather than from a `Cursor`.

    `encode_cursor` cannot express any of the inputs below — that is the
    point. A cursor is base64 of JSON and deliberately unsigned
    (`encode_cursor` argues why), so "what does the service do when the
    payload is not one it wrote?" is a question only a hand-built payload
    can ask.
    """
    payload: dict[str, object] = {
        "f": "all",
        "s": "settled",
        "o": 5,
        "v": 1,
        "t": 1,
        "d": TODAY.isoformat(),
        "l": 5,
    }
    payload.update(fields)
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


# --- the cursor round trip ----------------------------------------------


def test_a_cursor_survives_encoding_and_decoding_unchanged() -> None:
    assert decode_cursor(encode_cursor(CURSOR), TODAY) == CURSOR


def test_the_encoding_is_url_safe_and_unpadded() -> None:
    """It travels in a query string, so a `+`, a `/` or an `=` in it would
    have to survive whatever escaping the client applies. Padding is
    stripped on the way out and restored on the way in."""
    encoded = encode_cursor(CURSOR)

    assert "=" not in encoded
    assert "+" not in encoded
    assert "/" not in encoded


@given(
    queue_filter=st.sampled_from(list(QueueFilter)),
    stage=st.sampled_from(list(Stage)),
    offset=st.integers(min_value=0, max_value=1_000_000),
    rules_version=st.integers(min_value=1, max_value=10_000),
    thresholds_version=st.integers(min_value=1, max_value=10_000),
    age=st.integers(min_value=0, max_value=MAX_CURSOR_AGE.days),
    limit=st.integers(min_value=MIN_PAGE_LIMIT, max_value=MAX_PAGE_LIMIT),
)
def test_every_cursor_this_service_can_issue_decodes_back_to_itself(
    queue_filter: QueueFilter,
    stage: Stage,
    offset: int,
    rules_version: int,
    thresholds_version: int,
    age: int,
    limit: int,
) -> None:
    """The property the whole paging contract rests on.

    Every field is carried, none is truncated, and the date survives the
    ISO round trip. A single dropped field would show up as a cursor that
    silently changed filter or page size between two requests — which is
    exactly the class of bug the recorded context exists to prevent.
    """
    cursor = Cursor(
        queue_filter=queue_filter,
        stage=stage,
        offset=offset,
        rules_version=rules_version,
        thresholds_version=thresholds_version,
        as_of=TODAY - timedelta(days=age),
        limit=limit,
    )

    assert decode_cursor(encode_cursor(cursor), TODAY) == cursor


# --- a cursor the service did not issue ---------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not base64 at all",
        "eyJmb28iOiJiYXIifQ",  # valid base64, valid JSON, wrong shape
        "!!!!",
    ],
)
def test_an_unreadable_cursor_is_refused(raw: str) -> None:
    with pytest.raises(InvalidCursor):
        decode_cursor(raw, TODAY)


def test_a_cursor_carrying_infinity_is_refused_rather_than_crashing() -> None:
    """`json.loads` accepts the bare literal `Infinity`, and `int(inf)`
    raises `OverflowError` — which is not a `ValueError`, so it used to
    escape the except clause and leave the route with a 500 instead of the
    400 this refusal exists to produce."""
    with pytest.raises(InvalidCursor):
        decode_cursor(forge(o=float("inf")), TODAY)


def test_a_cursor_carrying_nan_is_refused() -> None:
    with pytest.raises(InvalidCursor):
        decode_cursor(forge(l=float("nan")), TODAY)


def test_a_negative_offset_is_refused() -> None:
    with pytest.raises(InvalidCursor, match="negative"):
        decode_cursor(forge(o=-1), TODAY)


@pytest.mark.parametrize("limit", [0, -5, MAX_PAGE_LIMIT + 1, 10_000_000])
def test_a_page_size_outside_the_routes_range_is_refused(limit: int) -> None:
    """The route caps `limit` at 200, and the cursor's own limit is *reused*
    when a request omits one — so an unbounded cursor limit is a way round
    the cap, not a cosmetic gap."""
    with pytest.raises(InvalidCursor, match="page size"):
        decode_cursor(forge(l=limit), TODAY)


def test_a_cursor_dated_in_the_future_is_refused() -> None:
    """No cursor this service issued can name one: `claim_queue` stamps
    `utc_today()`."""
    with pytest.raises(InvalidCursor, match="future"):
        decode_cursor(forge(d=(TODAY + timedelta(days=1)).isoformat()), TODAY)


def test_a_cursor_older_than_the_maximum_age_is_refused() -> None:
    """It cannot select a rule document any more — the documents are
    resolved at today's date — but a claim aged against a date years gone
    ranks by arithmetic nobody asked for."""
    stale = (TODAY - MAX_CURSOR_AGE - timedelta(days=1)).isoformat()

    with pytest.raises(InvalidCursor, match="aged"):
        decode_cursor(forge(d=stale), TODAY)


def test_a_cursor_at_exactly_the_maximum_age_is_still_readable() -> None:
    """The boundary, in the direction that matters: the refusal above must
    be a bound, not an off-by-one that expires same-day cursors."""
    at_limit = TODAY - MAX_CURSOR_AGE

    assert decode_cursor(forge(d=at_limit.isoformat()), TODAY).as_of == at_limit


@pytest.mark.parametrize("field", ["f", "s", "o", "v", "t", "d", "l"])
def test_a_cursor_missing_any_field_is_refused(field: str) -> None:
    """Every recorded field is load-bearing, so a payload without one
    describes no list — never a default filled in silently."""
    payload = json.loads(base64.urlsafe_b64decode(forge() + "=="))
    del payload[field]
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

    with pytest.raises(InvalidCursor):
        decode_cursor(raw, TODAY)


# --- ranking within a group ---------------------------------------------


def scored(
    *claims: QueueClaim,
) -> list[tuple[QueueClaim, QueueFlags, float]]:
    """Claims paired with the baseline flags and a score from `WEIGHTS`.

    Severity is the only term in play (factor 1, every categorical weight
    zero except litigation and surgery, which the claims below leave off),
    so a claim's score *is* its severity — which makes a deliberate tie one
    line of setup rather than a puzzle.
    """
    return [(c, FLAGS, priority_score(c, FLAGS, WEIGHTS)) for c in claims]


def ids(cards: list[QueueCard]) -> list[str]:
    return [card.claim.claim_id for card in cards]


def test_a_group_is_ranked_by_score_descending() -> None:
    cards = _ranked_group(
        scored(claim("WC-0001", severity=10), claim("WC-0002", severity=90)),
        Stage.treatment,
        QueueFilter.all,
        WEIGHTS,
    )

    assert ids(cards) == ["WC-0002", "WC-0001"]


def test_claims_with_equal_scores_are_ordered_by_claim_id() -> None:
    """The tie-break, exercised — which the seeded portfolio cannot do,
    because it happens to contain no tie.

    Without it `list.sort` leaves equal elements in input order, and the
    input order is whatever the repository's `ORDER BY claim_id` produced
    *this* time. A cursor into a group whose ties reshuffled between two
    requests repeats one claim and drops another, silently.
    """
    tied = scored(
        claim("WC-0009", severity=50),
        claim("WC-0003", severity=50),
        claim("WC-0007", severity=50),
    )
    forwards = _ranked_group(tied, Stage.treatment, QueueFilter.all, WEIGHTS)
    backwards = _ranked_group(list(reversed(tied)), Stage.treatment, QueueFilter.all, WEIGHTS)

    assert ids(forwards) == ["WC-0003", "WC-0007", "WC-0009"]
    assert ids(forwards) == ids(backwards)


def test_a_group_holds_only_its_own_stage() -> None:
    cards = _ranked_group(
        scored(
            claim("WC-0001", severity=90),
            claim("WC-0002", severity=80, stage=Stage.intake),
        ),
        Stage.treatment,
        QueueFilter.all,
        WEIGHTS,
    )

    assert ids(cards) == ["WC-0001"]


def test_the_filter_narrows_the_group_before_it_is_ranked() -> None:
    surgical = replace(claim("WC-0002", severity=10), surgery_required=True)
    cards = _ranked_group(
        scored(claim("WC-0001", severity=90), surgical),
        Stage.treatment,
        QueueFilter.surgery,
        WEIGHTS,
    )

    assert ids(cards) == ["WC-0002"]


def test_the_marker_goes_to_the_top_few_above_the_threshold() -> None:
    """Assigned over the whole ranked group, before any page is cut — the
    reason `_ranked_group` marks and `_page` only slices."""
    cards = _ranked_group(
        scored(*(claim(f"WC-{n:04d}", severity=100 - n) for n in range(6))),
        Stage.treatment,
        QueueFilter.all,
        WEIGHTS,
    )

    assert [card.priority_marker for card in cards] == [True, True, True, False, False, False]


def test_a_group_entirely_below_the_threshold_carries_no_marker() -> None:
    cards = _ranked_group(
        scored(claim("WC-0001", severity=20), claim("WC-0002", severity=10)),
        Stage.treatment,
        QueueFilter.all,
        WEIGHTS,
    )

    assert [card.priority_marker for card in cards] == [False, False]


# --- paging -------------------------------------------------------------


def page_of(count: int, offset: int, limit: int) -> StageGroup:
    cards = _ranked_group(
        scored(*(claim(f"WC-{n:04d}", severity=100 - n) for n in range(count))),
        Stage.treatment,
        QueueFilter.all,
        WEIGHTS,
    )
    return _page(cards, Stage.treatment, QueueFilter.all, offset, limit, 1, 1, TODAY)


def test_a_group_shorter_than_a_page_reports_no_successor() -> None:
    group = page_of(3, offset=0, limit=5)

    assert len(group.items) == 3
    assert group.next_cursor is None
    assert group.total == 3


def test_a_full_page_with_claims_behind_it_reports_a_successor() -> None:
    group = page_of(12, offset=0, limit=5)
    assert group.next_cursor is not None
    cursor = decode_cursor(group.next_cursor, TODAY)

    assert len(group.items) == 5
    assert cursor.offset == 5
    assert cursor.limit == 5
    assert cursor.stage is Stage.treatment
    assert cursor.as_of == TODAY


def test_a_group_that_divides_exactly_into_pages_still_ends() -> None:
    """The boundary that produces a phantom empty page if `>=` is written
    as `>`: ten claims at five a page must finish after the second, not
    offer a third that has nothing in it."""
    assert page_of(10, offset=5, limit=5).next_cursor is None


def test_the_total_is_the_whole_group_not_the_page() -> None:
    """It is the number in the chip beside the stage header; a count that
    shrank when the page did would misdescribe the caseload."""
    assert page_of(12, offset=5, limit=5).total == 12


def test_the_last_page_may_be_short() -> None:
    group = page_of(12, offset=10, limit=5)

    assert len(group.items) == 2
    assert group.next_cursor is None


def test_an_offset_at_or_past_the_end_is_refused() -> None:
    """A cursor is only ever issued for an offset with claims behind it, so
    one past the end describes a list this is not. The alternative — empty
    `items`, a non-zero `total`, a null `nextCursor` — reads as a group that
    is populated and finished at once."""
    with pytest.raises(InvalidCursor, match="reload the queue"):
        page_of(10, offset=10, limit=5)


def test_an_empty_group_is_a_page_rather_than_a_refusal() -> None:
    """Offset zero into nothing is the ordinary "this stage is empty" case,
    not a bad cursor — it is what the pane renders "No claims in this
    stage." against."""
    group = page_of(0, offset=0, limit=5)

    assert group.items == ()
    assert group.total == 0
    assert group.next_cursor is None
