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
    _order_key,
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
    last_score=42.5,
    last_claim_id="WC-0005",
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
        # `k`, not `o`: Story 9.8 replaced the offset with a resumption key,
        # and the default payload here is what a *real* cursor now looks like.
        "k": [42.5, "WC-0005"],
        "v": 1,
        "t": 1,
        "d": TODAY.isoformat(),
        "l": 5,
    }
    payload.update(fields)
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def forge_payload() -> dict[str, object]:
    """`forge`'s default payload, for the one test that has to *remove* a key."""
    return {
        "f": "all",
        "s": "settled",
        "k": [42.5, "WC-0005"],
        "v": 1,
        "t": 1,
        "d": TODAY.isoformat(),
        "l": 5,
    }


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
    last_score=st.floats(allow_nan=False, allow_infinity=False, width=64),
    last_claim_id=st.text(min_size=1, max_size=12),
    rules_version=st.integers(min_value=1, max_value=10_000),
    thresholds_version=st.integers(min_value=1, max_value=10_000),
    age=st.integers(min_value=0, max_value=MAX_CURSOR_AGE.days),
    limit=st.integers(min_value=MIN_PAGE_LIMIT, max_value=MAX_PAGE_LIMIT),
)
def test_every_cursor_this_service_can_issue_decodes_back_to_itself(
    queue_filter: QueueFilter,
    stage: Stage,
    last_score: float,
    last_claim_id: str,
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

    **Amended by Story 9.8**: the position is a `(score, claim_id)` key
    rather than an offset, so the generated field is a float beside a string
    instead of an integer. The property is unchanged and is now stronger —
    a float that did not survive the JSON round trip *exactly* would resume
    a walk one row out, which is the failure the keyset exists to remove.
    Infinities and `NaN` are excluded because `encode_cursor` cannot be
    handed one from a real page; `test_a_cursor_carrying_a_non_finite_score_is_refused`
    covers the forged case.
    """
    cursor = Cursor(
        queue_filter=queue_filter,
        stage=stage,
        last_score=last_score,
        last_claim_id=last_claim_id,
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
    400 this refusal exists to produce.

    **Amended by Story 9.8**: the field carrying the infinity moved from the
    offset (`o`) to the page size (`l`), because the offset no longer exists.
    The property — a non-`ValueError` arithmetic failure inside the decoder is
    a 400 and not a 500 — is the same one, on a field that still goes through
    `int(...)`."""
    with pytest.raises(InvalidCursor):
        decode_cursor(forge(l=float("inf")), TODAY)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), float("-inf")])
def test_a_cursor_carrying_a_non_finite_score_is_refused(score: float) -> None:
    """Story 9.8's sharpest forged input, and `NaN` is why the check exists.

    A `NaN` score decodes cleanly — `json` spells it and `float` accepts it —
    and then every comparison against it is false, so `bisect_right` would
    return a position decided by which elements it happened to probe rather
    than by the ordering. That is a *silently wrong page*, which is the one
    outcome this whole story is about removing; the infinities are refused
    beside it because a key outside the range of any real score names a
    position no page was ever cut at.
    """
    with pytest.raises(InvalidCursor):
        decode_cursor(forge(k=[score, "WC-0005"]), TODAY)


@pytest.mark.parametrize(
    "key",
    [
        5,  # not an array at all
        [42.5],  # one member
        [42.5, "WC-0005", 3],  # three
        ["42.5", "WC-0005"],  # a score that is a string
        [True, "WC-0005"],  # `bool` is an `int` in Python
        [42.5, 5],  # a claim id that is not a string
    ],
)
def test_a_malformed_resumption_key_is_refused(key: object) -> None:
    """Every part of `k` is checked rather than coerced.

    A string score or an integer id would reach the tuple comparison inside
    `bisect_right` and raise `TypeError` from inside a read — a 500 on the
    handler's primary screen — rather than the 400 a forged cursor deserves.
    `bool` is refused explicitly because it is an `int` in Python and would
    otherwise compare as `1`.
    """
    with pytest.raises(InvalidCursor):
        decode_cursor(forge(k=key), TODAY)


def test_a_cursor_predating_the_keyset_change_is_refused() -> None:
    """AC 1: an offset-shaped cursor is refused, never reinterpreted.

    A cursor minted before Story 9.8 carries `o` and no `k`. Reading the
    offset as a key, or falling back to page one, would resume a walk at the
    wrong row without saying so — which is precisely the silent skip the
    keyset replaces.
    """
    stale = dict(forge_payload(), o=5)
    stale.pop("k")
    encoded = base64.urlsafe_b64encode(json.dumps(stale).encode()).decode().rstrip("=")

    with pytest.raises(InvalidCursor):
        decode_cursor(encoded, TODAY)


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


@pytest.mark.parametrize("field", ["f", "s", "k", "v", "t", "d", "l"])
def test_a_cursor_missing_any_field_is_refused(field: str) -> None:
    """Every recorded field is load-bearing, so a payload without one
    describes no list — never a default filled in silently.

    **Amended by Story 9.8**: `o` became `k`. The property is unchanged and
    the substitution is what makes a pre-9.8 cursor — which has `o` and no
    `k` — refused rather than reinterpreted."""
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


def group_of(count: int) -> list[QueueCard]:
    """`count` treatment claims, already ranked — `WC-0000` first, descending.

    `severity=100 - n` with `severity_factor=1` makes the score strictly
    decreasing in `n`, so card `n` is at position `n` and a test can name a
    position by naming a claim.
    """
    return _ranked_group(
        scored(*(claim(f"WC-{n:04d}", severity=100 - n) for n in range(count))),
        Stage.treatment,
        QueueFilter.all,
        WEIGHTS,
    )


def page_of(count: int, start: int, limit: int) -> StageGroup:
    """One page of a `count`-card group, beginning at position `start`.

    **Amended by Story 9.8**: `start` used to be the cursor's offset and is
    now the position a keyset cursor resumes *at* — expressed as the
    `order_key` of the card before it, which is exactly what a real cursor
    carries. The helper takes a position rather than a key so that every
    test below still reads as a statement about where a page begins;
    what changed is how the service is told, not what is being asserted.
    """
    cards = group_of(count)
    resume_after = None if start == 0 else _order_key(cards[start - 1])
    return _page(cards, Stage.treatment, QueueFilter.all, resume_after, limit, 1, 1, TODAY)


def test_a_group_shorter_than_a_page_reports_no_successor() -> None:
    group = page_of(3, start=0, limit=5)

    assert len(group.items) == 3
    assert group.next_cursor is None
    assert group.total == 3


def test_a_full_page_with_claims_behind_it_reports_a_successor() -> None:
    """**Amended by Story 9.8**: the successor names a row, not a count.

    It used to assert `cursor.offset == 5`. The cursor no longer holds an
    offset, so the same property — "the next page starts where this one
    stopped" — is now asserted as the key of the *last card served*, which is
    the whole of what keyset resumption means.
    """
    cards = group_of(12)
    group = page_of(12, start=0, limit=5)
    assert group.next_cursor is not None
    cursor = decode_cursor(group.next_cursor, TODAY)

    assert len(group.items) == 5
    assert cursor.last_claim_id == cards[4].claim.claim_id
    assert cursor.last_score == cards[4].priority_score
    assert cursor.limit == 5
    assert cursor.stage is Stage.treatment
    assert cursor.as_of == TODAY


def test_a_group_that_divides_exactly_into_pages_still_ends() -> None:
    """The boundary that produces a phantom empty page if `>=` is written
    as `>`: ten claims at five a page must finish after the second, not
    offer a third that has nothing in it."""
    assert page_of(10, start=5, limit=5).next_cursor is None


def test_the_total_is_the_whole_group_not_the_page() -> None:
    """It is the number in the chip beside the stage header; a count that
    shrank when the page did would misdescribe the caseload."""
    assert page_of(12, start=5, limit=5).total == 12


def test_the_last_page_may_be_short() -> None:
    group = page_of(12, start=10, limit=5)

    assert len(group.items) == 2
    assert group.next_cursor is None


def test_a_key_past_the_end_is_an_ended_walk_rather_than_a_refusal() -> None:
    """**Amended by Story 9.8**, and the amendment *is* the fix.

    Its predecessor — `test_an_offset_at_or_past_the_end_is_refused` —
    asserted a 400, on the argument that "a cursor is only ever issued for an
    offset with claims behind it, so one past the end describes a list this is
    not", and that empty `items` beside a non-zero `total` and a null
    `nextCursor` reads as a group that is populated and finished at once.

    Under a keyset that argument no longer holds, because the state it
    described is no longer contradictory. A key past the end means every claim
    below the caller's position has left the group — settled, re-staged, edited
    out from under the filter — which is a walk that has genuinely ended, and
    the three fields say so exactly: nothing after your position, twelve in the
    group, no next page. The old refusal would now force a reload for a walk
    that finished correctly.

    The property being defended is unchanged: **a client must never be able to
    read a page as both populated and finished.** It is asserted here on the
    reading the keyset makes true.
    """
    group = page_of(10, start=10, limit=5)

    assert group.items == ()
    assert group.total == 10
    assert group.next_cursor is None


def test_an_empty_group_is_a_page_rather_than_a_refusal() -> None:
    """The start of nothing is the ordinary "this stage is empty" case,
    not a bad cursor — it is what the pane renders "No claims in this
    stage." against."""
    group = page_of(0, start=0, limit=5)

    assert group.items == ()
    assert group.total == 0
    assert group.next_cursor is None


def test_a_row_leaving_the_group_mid_walk_skips_nobody() -> None:
    """AC 1, and the defect this story exists to remove.

    Page one is walked at three a page. The claim ranked *above* the page
    boundary — position 1 of ten — then leaves the group, as a settlement or a
    stage edit would remove it. Page two is requested with the cursor page one
    handed back.

    Under the offset this cursor used to carry, page two would have started at
    index 3 of a nine-card list — which is the *fourth* surviving claim, so
    `WC-0003` would have been served on page one, skipped on page two, and
    never appeared again. Under a key it resumes after `WC-0002` whatever
    happened above it.

    The assertion is the whole-walk property rather than a single page: every
    surviving claim appears exactly once, in rank order.
    """
    full = group_of(10)
    first = _page(full, Stage.treatment, QueueFilter.all, None, 3, 1, 1, TODAY)
    assert first.next_cursor is not None
    cursor = decode_cursor(first.next_cursor, TODAY)

    # One claim above the boundary leaves the population between the requests.
    survivors = [card for card in full if card.claim.claim_id != "WC-0001"]
    walked = [card.claim.claim_id for card in first.items]
    resume: tuple[float, str] | None = cursor.resume_after
    while resume is not None:
        page = _page(survivors, Stage.treatment, QueueFilter.all, resume, 3, 1, 1, TODAY)
        walked.extend(card.claim.claim_id for card in page.items)
        resume = (
            None
            if page.next_cursor is None
            else decode_cursor(page.next_cursor, TODAY).resume_after
        )

    # `WC-0001` was served on page one before it left, and appears once.
    assert walked == [card.claim.claim_id for card in full]
    assert len(walked) == len(set(walked))
