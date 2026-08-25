"""The handler's caseload queue, assembled server-side (FR-H-1, FR-Q-1/2/4).

Everything a queue card shows and everything about the order it shows it in
is decided here (AD-1). The SPA receives four stage groups of finished cards
and renders them; it holds no scoring, no grouping, no filter predicate and
no marker rule. That is not stylistic — the prototype computed all of it in
the browser from a global claim array, which is the same code path that made
its scoping client-side.

**The shape of one request.** Load the two rule documents once (AD-8). Read
the caller's claims once, under the repository's scope filter (AD-7). Derive
each claim's flags through the registry (AD-10). Filter, score, sort, group,
mark, slice. Nothing in that sequence knows the caller's role, and nothing
re-reads the database per claim.

**Why the marker is computed before the slice.** `priority_markers` decides
which cards carry the 🔺 over the whole filtered group, before any page is
cut. Marking after slicing would let a fourth claim sprout a marker simply
because someone asked for page 2. The rule itself lives next to the score
in `priority.py`, where Epic 5 will find it.

**Why the cursor is opaque and keyset-based.** It used to be an offset, on
the argument that "keyset pagination needs the sort key in the query, and
this sort key is Python arithmetic over a JDM parameter block". That argument
is true of a *SQL* keyset and false of this one (Story 9.8): the group is
already fully scored and fully sorted, in Python, by `priority.order_key`, so
resuming after a key is a `bisect_right` into a list that exists — no
`ORDER BY` over the score, no materialized rank, the score still computed
exactly once (AD-2). What the change buys is the defect the offset had: a
claim that leaves a group between two requests slides every row below it up
one, so an offset of ten starts at what was row eleven and exactly one card
is skipped, silently, with a 200.

So the cursor records the **key** the page ended on, **together with
everything that shaped that ordering**: the filter, the stage, both
rule-document versions, the day the claims were aged against, and the page
size. Every one of them can change the list underneath a caller who is
halfway down it — a filter narrows it, a rule version re-ranks it, a request
that crosses UTC midnight re-ages it, and a different `limit` cuts a
different window. A cursor that does not describe the list being asked for is
a 400, not a best-effort re-page. A cursor minted before Story 9.8 carries an
offset and no key, and is refused for that reason rather than reinterpreted.

**Checked or reused — and which is which matters.** The two rule-document
versions are *checked*, because a superseded document must not go on
ranking a caller's queue indefinitely just because they kept clicking "Show
more"; the documents are therefore always resolved at **today's** date and
the cursor's versions are compared against what is effective now. The day
and the page size are *reused*, because they describe the window rather than
the rules and re-deriving them mid-list is what skips a claim. Resolving the
documents at the cursor's own date instead would collapse the check into a
tautology — the version it recorded is exactly the version effective on the
date it recorded — which is how this arrangement was wrong on its first
outing.

**Why every group is always the truth, and where `groups` is the exception.**
`stage` narrows nothing; it names which group the cursor addresses. A response
where three groups were *silently* empty because the caller asked about the
fourth would be indistinguishable from a caseload that really had nothing in
those stages — and "no claims in this stage" is a message this console owes
the user honestly (NFR-3).

`groups` (Story 9.8) is how a caller asks for less **and says so**. The
"Show more" path reads one group and discards three, so at `pageLimit: 50` an
incremental page ranks, marks and serialises up to two hundred fully-derived
cards to deliver fifty. Naming the groups makes the narrowing explicit in the
request and in the response — an omitted group is *absent*, not empty, so no
client can mistake "you did not ask" for "there is nothing". Omitting the
parameter is the initial load and returns all four, unchanged. The two totals
are unaffected either way: they are counted over the whole scored book, not
summed from whichever groups came back.
"""

import base64
import binascii
import json
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models.enums import Stage
from data.repositories import claims as claim_repo
from rules.parameters import DerivationThresholds, PriorityWeights, thresholds_for, weights_for
from services import derivations
from services.derivations import utc_today
from services.worklist.priority import (
    QueueClaim,
    QueueFilter,
    QueueFlags,
    matches,
    order_key,
    priority_markers,
    priority_score,
)

# The prototype's `STAGE_GROUPS` order (line 1133): the lifecycle, left to
# right. Icons and labels are the UI's — the wire carries the enum.
STAGE_ORDER: tuple[Stage, ...] = (Stage.intake, Stage.investigation, Stage.treatment, Stage.settled)

# The page-size range, declared once and enforced twice: FastAPI rejects a
# `limit` outside it with a 422 before the service is reached, and
# `decode_cursor` refuses one smuggled inside a cursor. Two enforcement
# points, one pair of numbers — a cursor is caller-supplied input like any
# other query parameter, and a forged one asking for a 10-million-row page
# must not be the way past the route's ceiling.
MIN_PAGE_LIMIT = 1
MAX_PAGE_LIMIT = 200

# How stale a cursor's recorded day may be before it stops describing any
# list worth continuing. A cursor is a pagination token held for the length
# of a scroll, not a bookmark: a week is generous for the honest case and
# still refuses a date that can only have been forged or unearthed. A day in
# the *future* is refused outright — no cursor this service issued can name
# one, since it stamps `utc_today()`.
MAX_CURSOR_AGE = timedelta(days=7)


class InvalidCursor(ValueError):
    """A cursor that does not describe a position in the requested list."""


@dataclass(frozen=True)
class Cursor:
    """Where a page ended, and everything that decided where that was.

    None of the context fields is decoration. Each names an input that,
    changed between two requests, silently produces a different list at the
    same offset — but they divide into two kinds, and the division is the
    whole design:

    **Compared.** These say which *rules* ranked the list, and a page cut
    under superseded rules is not a page of the list being asked for.

    **The position is a key, not an offset** (Story 9.8). `last_score` and
    `last_claim_id` are the two halves of `priority.order_key` for the last card
    of the page just served, and the next page resumes strictly after that key.
    A claim that leaves the group between two requests — settled, re-staged,
    edited out from under the filter — no longer slides the window and skips its
    neighbour. See the module docstring for why this is not the AD-2 breach the
    old offset's comment said it would be.

    **What that guarantee covers, exactly.** A claim that *leaves* the group is
    handled: the rows below it no longer slide up, so nothing is skipped. A
    claim whose **order key changes** mid-walk is not, and no field here can fix
    it — an edit that raises a severity score moves a claim from below the
    caller's key to above it, and the next page resumes strictly after that key,
    so the claim is never served: the walk ends one card short of its own
    `total`, with a 200. That is a narrower defect than the offset's (it takes
    an edit to the ranking inputs rather than any departure at all), it is the
    residual limit of resuming on a key over a mutable ranking, and it is
    recorded in `deferred-work.md` rather than implied away here.

    - `queue_filter` and `stage` — a different list outright.
    - `rules_version` / `thresholds_version` — the same claims, re-scored.
      The weights are the obvious half; the thresholds are the easy half to
      forget, because they decide `rtw_blocked`, `siu_review` and the risk
      band, which are *inputs* to the score. Both are compared against the
      versions effective **today**, which is why `claim_queue` resolves its
      documents at today's date and never at the cursor's: a comparison
      against the versions effective on the cursor's own date could only
      ever succeed.

    **Reused.** These say which *window* was cut, and re-deriving them
    mid-list is what loses or repeats a claim.

    - `as_of` — the day the claims are aged against. A "Show more" issued at
      23:59:59 UTC and answered at 00:00:01 re-ages every claim by a day and
      re-ranks the group around it, which is a real (if rare) way to skip a
      claim entirely. The honest answer is not "your page expired at
      midnight" but "here is the next page of the list you were reading".
      It is still *validated* — a date in the future, or older than
      `MAX_CURSOR_AGE`, describes no list this service ever cut — but it is
      never used to choose a rule document.
    - `limit` — the same reuse argument, for a much more common case. Page 1
      at `limit=10` ends after ten cards; a follow-up that forgot to repeat the
      limit would cut fifty from that key and hand the caller a window they
      never asked for, on a list whose "Show more" they will click again.
    """

    queue_filter: QueueFilter
    stage: Stage
    last_score: float
    last_claim_id: str
    rules_version: int
    thresholds_version: int
    as_of: date
    limit: int

    @property
    def resume_after(self) -> tuple[float, str]:
        """The `order_key` value the next page resumes strictly after.

        The score is stored un-negated so a reader of the decoded JSON sees the
        number the payload published, and negated here because that is the sort
        form `order_key` returns.
        """
        return (-self.last_score, self.last_claim_id)


def encode_cursor(cursor: Cursor) -> str:
    """Base64url of a compact JSON object, unpadded.

    Opaque by intent rather than by encryption: it carries nothing a caller
    could use to widen their scope (scope is never in the request — AD-7), so
    obscurity is not doing security work here. What the encoding bought is
    that clients treat it as a token to hand back rather than a position to
    increment — which is exactly what let Story 9.8 swap offset for keyset
    without a single client change.

    **Since that story it does carry one claim's business id**, as half the
    resumption key. Not a widening: it is a claim the caller was handed a
    moment earlier under the repository's scope filter, already on their
    screen in the `claimId` of the card beside it. Substituting somebody
    else's id yields a position in the caller's *own* ranked group and no card
    they could not already read.
    """
    payload = json.dumps(
        {
            "f": cursor.queue_filter.value,
            "s": cursor.stage.value,
            # `k`, not `o`. The rename is the compatibility break, deliberately:
            # a pre-9.8 cursor carries `o` and no `k`, so it fails the `KeyError`
            # branch in `decode_cursor` rather than being read as a position it
            # does not name.
            "k": [cursor.last_score, cursor.last_claim_id],
            "v": cursor.rules_version,
            "t": cursor.thresholds_version,
            "d": cursor.as_of.isoformat(),
            "l": cursor.limit,
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _resume_key(raw: object) -> tuple[float, str]:
    """A cursor's `k` member back into `(score, claim_id)`, or a refusal.

    Restated in each of the three ranked services rather than shared, which is
    this codebase's rule for cursor codecs: `encode_cursor`, `decode_cursor` and
    their bounds already live once per service, and a shared parser would be the
    first thread of a `pagination` module nobody has decided to write.

    Every part is checked rather than coerced, because a cursor is unsigned
    base64 JSON and forging one is trivial: the member must be a two-element
    array; the score must be a finite real number — `bool` is an `int` in Python
    and is refused explicitly, and `NaN` is refused because every comparison
    against it is false; the claim id must be a string, because a mixed-type
    tuple comparison raises rather than orders.

    `TypeError`/`ValueError` rather than a bespoke exception, so the caller's
    existing `except` tuple turns every unreadable cursor into the one
    `InvalidCursor` this module publishes.
    """
    if not isinstance(raw, list | tuple) or len(raw) != 2:
        raise TypeError("the cursor's resumption key is not a two-element array")
    score, claim_id = raw
    if isinstance(score, bool) or not isinstance(score, int | float):
        raise TypeError(f"the cursor's score is {type(score).__name__}, not a number")
    if not isfinite(score):
        raise ValueError("the cursor's score is not a finite number")
    if not isinstance(claim_id, str):
        raise TypeError(f"the cursor's claim id is {type(claim_id).__name__}, not a string")
    return (float(score), claim_id)


def decode_cursor(raw: str, as_of: date | None = None) -> Cursor:
    """Parse a cursor, or refuse it. Never a silent fallback to page one.

    Answering page one for an undecodable cursor would turn a client bug
    into an infinite "Show more" that re-appends the same claims for ever.

    **A cursor is caller-supplied input, and forging one is trivial** — it
    is base64 of JSON, opaque rather than signed (`encode_cursor` says why).
    So every field is bounded here, not merely parsed:

    - `ArithmeticError` is in the except tuple beside `ValueError`. `json`
      accepts the literal `Infinity`, and `int(float("inf"))` raises
      `OverflowError`, which is *not* a `ValueError` — a forged cursor
      carrying it escaped as a 500 rather than the 400 this function exists
      to produce.
    - `limit` is held to the same `MIN_PAGE_LIMIT`–`MAX_PAGE_LIMIT` range
      the route declares. Otherwise the cursor is a way around the route's
      ceiling: `limit` inside it is *reused* when the request omits one, so
      a forged page size would be honoured without ever passing FastAPI's
      validator.
    - `as_of` is bounded by `MAX_CURSOR_AGE`. It cannot select a rule
      document any more (see `claim_queue`), so a hostile date no longer
      reaches the loader — but a claim aged against the year 1900 or 3000
      ranks by arithmetic nobody asked for, and "reload from page one" is
      the truthful answer.
    - `k` is the resumption key, and `_resume_key` refuses anything that is
      not a finite number beside a string. `NaN` is the sharp case: `json`
      spells it, every comparison against it is false, and `bisect_right`
      would then resume wherever the probe sequence landed rather than where
      the ordering says. Since Story 9.8 this key replaces the offset, so a
      cursor issued before that change carries no `k` and is refused here.
    """
    try:
        padded = raw + "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        last_score, last_claim_id = _resume_key(data["k"])
        cursor = Cursor(
            queue_filter=QueueFilter(data["f"]),
            stage=Stage(data["s"]),
            last_score=last_score,
            last_claim_id=last_claim_id,
            rules_version=int(data["v"]),
            thresholds_version=int(data["t"]),
            as_of=date.fromisoformat(data["d"]),
            limit=int(data["l"]),
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        ArithmeticError,
        binascii.Error,
        UnicodeDecodeError,
    ) as exc:
        raise InvalidCursor("The pagination cursor is not readable.") from exc
    if not MIN_PAGE_LIMIT <= cursor.limit <= MAX_PAGE_LIMIT:
        raise InvalidCursor(
            f"The pagination cursor names a page size of {cursor.limit}; "
            f"it must be between {MIN_PAGE_LIMIT} and {MAX_PAGE_LIMIT}."
        )
    today = as_of or utc_today()
    if cursor.as_of > today:
        raise InvalidCursor(
            f"The pagination cursor is dated {cursor.as_of}, which is in the future; "
            "reload the queue from the first page."
        )
    if today - cursor.as_of > MAX_CURSOR_AGE:
        raise InvalidCursor(
            f"The pagination cursor is dated {cursor.as_of} and the claims it ranked have "
            f"aged {(today - cursor.as_of).days} days since; "
            "reload the queue from the first page."
        )
    return cursor


@dataclass(frozen=True)
class QueueCard:
    """One finished card: the row, its derived flags, its rank, its marker."""

    claim: QueueClaim
    flags: QueueFlags
    priority_score: float
    priority_marker: bool


@dataclass(frozen=True)
class StageGroup:
    """One stage's page, in the `{items, nextCursor, total}` shape.

    `total` is the size of the whole filtered group, not of `items`: it is
    what the count chip beside the stage header shows, and a count that
    shrank when the page did would misdescribe the caseload.
    """

    items: tuple[QueueCard, ...]
    next_cursor: str | None
    total: int


@dataclass(frozen=True)
class ClaimQueue:
    """All four groups, both rule-document versions, and both totals.

    `unfiltered_total` is the size of the caller's scoped book *before* the
    filter predicate; `filtered_total` is what the predicate left. Together
    they are the two facts NFR-3's three messages are decided from — "your
    caseload is empty", "nothing matches this filter", and (per group)
    "nothing in this stage".

    **Both are published even though `filtered_total` is the sum of the four
    group totals.** That sum is arithmetic, and arithmetic over a payload is
    the thing AD-1 keeps out of the browser: the SPA that added the four
    numbers up to decide which sentence to show had re-implemented "how big
    is this queue" client-side, one refactor away from disagreeing with the
    server about it. Sending the total costs four bytes and leaves the SPA
    reading two numbers and computing neither.

    `thresholds_version` rides beside `rules_version` for the reason the
    cursor carries both: the weights decide what a signal is worth, the
    thresholds decide whether the claim has it, and "which rules produced
    this ordering?" is only answered by naming both.

    **`groups` holds only the stages the request asked for** (Story 9.8).
    With no `groups` parameter that is all four, which is every call the
    console made before this story. A narrowed request omits the rest rather
    than emptying them — see the module docstring — and `filtered_total` is
    still the whole filtered book, because it is counted from the scored rows
    and not summed from whatever came back.

    **`as_of` is the day these cards were aged against**, `/dashboard/trends`'
    field verbatim. Page one resolves it to today; every later page reuses the
    day the cursor pinned, so a queue walked across UTC midnight publishes
    `daysOpen` values and an ordering computed against a date up to
    `MAX_CURSOR_AGE` in the past. That was already true and simply unstated.
    """

    groups: Mapping[Stage, StageGroup]
    rules_version: int
    thresholds_version: int
    unfiltered_total: int
    filtered_total: int
    as_of: date


def _rows_to_cards(
    rows: Sequence[sa.Row[Any]],
    thresholds: DerivationThresholds,
    weights: PriorityWeights,
    as_of: date,
) -> list[tuple[QueueClaim, QueueFlags, float]]:
    """Derive, project and score every scoped row — once each.

    The four computers are built once from the loaded thresholds and then
    called per row, which is the shape the registry exists for: a hundred
    claims cost one parameter load, not a hundred.
    """
    risk = derivations.risk.for_thresholds(thresholds)
    days_open = derivations.days_open.for_thresholds(thresholds)
    siu_review = derivations.siu_review.for_thresholds(thresholds)
    rtw_blocked = derivations.rtw_blocked.for_thresholds(thresholds)
    payment_due = derivations.payment_due.for_thresholds(thresholds)

    scored: list[tuple[QueueClaim, QueueFlags, float]] = []
    for row in rows:
        band = risk.of(row.severity_score)
        claim = QueueClaim(
            claim_id=row.claim_id,
            stage=row.stage,
            status=row.status,
            severity_score=row.severity_score,
            days_open=days_open.of(row.froi_date, as_of),
            injury_type=row.injury_type,
            worker_name=row.worker_name,
            employer_short_name=row.employer_short_name,
            surgery_required=row.surgery_required,
            litigation_flag=row.litigation_flag,
            fraud_flag=row.fraud_flag,
        )
        flags = QueueFlags(
            risk=band,
            siu_review=siu_review.of(fraud_flag=row.fraud_flag, fraud_score=row.fraud_score),
            rtw_blocked=rtw_blocked.of(
                claim_id=row.claim_id,
                stage=row.stage,
                return_status=row.return_status,
                risk=band,
            ),
            payment_due=payment_due.of(claim_id=row.claim_id, stage=row.stage),
        )
        scored.append((claim, flags, priority_score(claim, flags, weights)))
    return scored


def _ranked_group(
    scored: Sequence[tuple[QueueClaim, QueueFlags, float]],
    stage: Stage,
    queue_filter: QueueFilter,
    weights: PriorityWeights,
) -> list[QueueCard]:
    """One stage's cards, highest priority first, markers already decided.

    Sorted on `priority.order_key` — `(-score, claim_id)`, the definition this
    function used to hold inline and now shares with Story 5.4's ungrouped
    worklist. The tie-break is not cosmetic: ties are common (a settled group
    where every claim carries the same penalty and a similar severity), and a
    sort with a non-total key leaves equal elements in whatever order the input
    arrived — which would let a cursor into the group repeat one claim and drop
    another between two requests. See `order_key` for why the two callers share
    a symbol rather than a sentence.

    The lambda adapts this function's three-tuple to the key's `(claim, score)`
    pair. Adapting here rather than widening the key keeps the ordering a
    statement about a claim and its score, which is all it is about; the flags
    ride along because the card needs them, not because the order does.
    """
    members = [
        (claim, flags, score)
        for claim, flags, score in scored
        if claim.stage is stage and matches(queue_filter, claim, flags)
    ]
    members.sort(key=lambda entry: order_key((entry[0], entry[2])))

    markers = priority_markers([score for _claim, _flags, score in members], weights)
    return [
        QueueCard(claim=claim, flags=flags, priority_score=score, priority_marker=marker)
        for (claim, flags, score), marker in zip(members, markers, strict=True)
    ]


def _order_key(card: QueueCard) -> tuple[float, str]:
    """One finished card, through `priority.order_key`. The symbol, not the shape.

    `bisect_right` needs exactly the key `_ranked_group` sorted by, and writing
    `(-score, claim_id)` here would be a second spelling of the ordering — the
    failure `order_key`'s own docstring exists to prevent, since two spellings
    agree on every score and can disagree on every tie with nothing to say so.

    Called O(log n) times per group, only on the cards the search probes.
    """
    return order_key((card.claim, card.priority_score))


def _page(
    cards: Sequence[QueueCard],
    stage: Stage,
    queue_filter: QueueFilter,
    resume_after: tuple[float, str] | None,
    limit: int,
    rules_version: int,
    thresholds_version: int,
    as_of: date,
) -> StageGroup:
    # **Keyset, after the fold** — Story 9.8. `cards` is already sorted by
    # `priority.order_key`, so resuming is a binary search for the first card
    # strictly after the key the last page ended on. `bisect_right` rather than
    # `bisect_left`, because the cursor names a card that was *served*.
    #
    # No refusal for a position past the end any more, and its absence is the
    # fix rather than an omission. Under offsets, an offset at or past the end
    # described a list this one was not, and serving it would have meant an empty
    # page beside a non-zero `total` — a group simultaneously populated and
    # finished, which a client can only render as a lie. Under a key it means
    # something true and different: every claim below the caller's position has
    # left the group, so the walk has ended. An empty final page with a null
    # `nextCursor` beside a truthful `total` says exactly that.
    start = 0 if resume_after is None else bisect_right(cards, resume_after, key=_order_key)
    window = cards[start : start + limit]
    exhausted = start + limit >= len(cards)
    return StageGroup(
        items=tuple(window),
        # Null only when the group is finished — never "null because this
        # page happened to come back short", which would strand the tail of
        # a group the user can see counted in the chip beside it.
        next_cursor=(
            None
            if exhausted
            else encode_cursor(
                Cursor(
                    queue_filter=queue_filter,
                    stage=stage,
                    last_score=window[-1].priority_score,
                    last_claim_id=window[-1].claim.claim_id,
                    rules_version=rules_version,
                    thresholds_version=thresholds_version,
                    as_of=as_of,
                    limit=limit,
                )
            )
        ),
        total=len(cards),
    )


async def claim_queue(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    queue_filter: QueueFilter = QueueFilter.all,
    stage: Stage | None = None,
    groups: Sequence[Stage] | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    as_of: date | None = None,
) -> ClaimQueue:
    """The caller's queue: four groups, filtered, ranked, marked and paged.

    **Two dates, and they are not interchangeable.**

    `today` is when the request is being served. The rule documents are
    always resolved against it, so the version comparisons below mean what
    they say: a cursor issued under a document that has since been
    superseded is refused, and the caller reloads onto the ranking that is
    now in force. Resolving them against the cursor's own date instead would
    make every comparison a tautology and let one stale token pin a handler
    to a retired ranking for as long as they kept paging.

    `aged_on` is the day the claims are aged against — `days_open`, which
    feeds the score. A cursor's recorded day wins here, and only here: page 2
    must be cut from the list page 1 was, and a request that lands a second
    after UTC midnight would otherwise re-age the whole portfolio between the
    two pages. With no cursor the two dates are the same day.

    An explicit `as_of` argument sets `today` — it is how a test pins the
    clock, including for the document lookup.

    **`groups` narrows what is ranked, and never what is true** (Story 9.8).
    `None` means all four, which is the initial load and every call this console
    made before this story. A "Show more" names the one group it is walking, and
    the other three are then not sorted, not marked and not built into cards —
    the fold above them still runs, because the scores it produces are what
    `unfiltered_total`, `filtered_total` and the requested group's own ranking
    are computed from, and reducing *that* is Story 9.10's payload rather than
    this one's.

    A stage named in `groups` but absent from `STAGE_ORDER` cannot happen — the
    parameter is typed on the enum — and a duplicate is idempotent, so no
    validation is spent on either. An **empty** `groups` is refused rather than
    answered: `None` already means "all four", so an empty sequence can only be
    a caller that built the list and put nothing in it, and answering it would
    publish four null groups beside a non-zero `filteredTotal` — a payload
    indistinguishable from an error and readable as "your whole queue is
    missing". The route cannot produce one (an absent query parameter binds to
    `None`, and there is no wire spelling for a zero-length list), so this is a
    programming error and a `ValueError` rather than an `InvalidCursor`.
    """
    if groups is not None and len(groups) == 0:
        raise ValueError(
            "`groups` names the stage groups to return and cannot be empty; "
            "omit it to ask for all four."
        )
    decoded = decode_cursor(cursor, as_of) if cursor is not None else None
    today = as_of or utc_today()
    thresholds = await thresholds_for(db, today)
    weights = await weights_for(db, today)
    aged_on = decoded.as_of if decoded is not None else today

    if decoded is not None:
        if decoded.queue_filter is not queue_filter:
            raise InvalidCursor(
                f"That page belongs to the {decoded.queue_filter.value!r} filter, "
                f"not {queue_filter.value!r}."
            )
        if stage is not None and decoded.stage is not stage:
            raise InvalidCursor(
                f"That page belongs to the {decoded.stage.value!r} group, not {stage.value!r}."
            )
        # And the same ruling against `groups`, which is the parameter that
        # decides what comes back. Without this check a cursor into the settled
        # group replayed beside `groups=intake` passes every comparison above —
        # the filter, both versions, the date, the page size all describe the
        # list it was cut from — and then the loop below never reaches the group
        # it names, so `resume_after` is never applied: the caller gets intake
        # *page one* with a fresh cursor, and the group they were walking is
        # absent from the payload entirely. A silently restarted walk with a
        # 200, which is the one answer this cursor is not allowed to give.
        if groups is not None and decoded.stage not in groups:
            asked = ", ".join(s.value for s in STAGE_ORDER if s in groups)
            raise InvalidCursor(
                f"That page belongs to the {decoded.stage.value!r} group, and this "
                f"request asked for {asked}; ask for the group the cursor names, "
                "or reload the queue from the first page."
            )
        if decoded.rules_version != weights.version:
            raise InvalidCursor(
                f"That page was ranked by priority_weights v{decoded.rules_version}, "
                f"and v{weights.version} is now effective; "
                "reload the queue from the first page."
            )
        if decoded.thresholds_version != thresholds.version:
            raise InvalidCursor(
                f"That page was derived from derivation_thresholds "
                f"v{decoded.thresholds_version}, and v{thresholds.version} is now effective; "
                "reload the queue from the first page."
            )

    rows = await claim_repo.select_queue_rows(db, ctx)
    scored = _rows_to_cards(rows, thresholds, weights, aged_on)
    # The cursor's page size wins over the default for the same reason its
    # date does: the caller is reading one list and offsets into it are only
    # meaningful at the size they were cut at. An explicit `limit` on the
    # request is still honoured — a caller who changes it is asking for a
    # different window, not continuing the old one — but a caller who simply
    # omits it gets the page they asked for rather than a wider overlapping
    # one.
    page_size = limit or (decoded.limit if decoded is not None else weights.page_limit)

    wanted = STAGE_ORDER if groups is None else tuple(s for s in STAGE_ORDER if s in groups)
    pages: dict[Stage, StageGroup] = {}
    for group_stage in wanted:
        cards = _ranked_group(scored, group_stage, queue_filter, weights)
        resume_after = (
            decoded.resume_after if decoded is not None and decoded.stage is group_stage else None
        )
        pages[group_stage] = _page(
            cards,
            group_stage,
            queue_filter,
            resume_after,
            page_size,
            weights.version,
            thresholds.version,
            aged_on,
        )

    return ClaimQueue(
        groups=pages,
        rules_version=weights.version,
        thresholds_version=thresholds.version,
        # `scored` is every scoped row, before `matches` narrowed anything;
        # the second is what the predicate left. Both are counted here so the
        # SPA does neither.
        #
        # `filtered_total` is counted directly rather than summed from the four
        # group totals, and since Story 9.8 it has to be: a narrowed request
        # holds one group, and a sum over it would report the treatment group's
        # size as the size of the whole filtered queue. The two are equal when
        # all four are present — every claim has exactly one stage in
        # `STAGE_ORDER` — so this is the same number, computed from the fact
        # rather than from the payload. It is not free on the narrowed path: a
        # request naming one group folds `matches` over the whole scored book
        # here as well as over that group, so the pass `groups` saves on ranking,
        # marking and card-building is paid back once as a predicate sweep —
        # cheaper than the three group builds it replaces, and the price of a
        # `filteredTotal` that is still about the queue rather than about the
        # page.
        unfiltered_total=len(scored),
        filtered_total=sum(
            1 for claim, flags, _score in scored if matches(queue_filter, claim, flags)
        ),
        as_of=aged_on,
    )
