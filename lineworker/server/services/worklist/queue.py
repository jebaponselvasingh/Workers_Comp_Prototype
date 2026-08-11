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

**Why the cursor is opaque and offset-based.** Keyset pagination needs the
sort key in the query, and this sort key is Python arithmetic over a JDM
parameter block — re-implementing it in SQL is exactly what AD-2 forbids. So
the cursor records where in the *fully scored, fully sorted* group a page
ended, **together with everything that shaped that ordering**: the filter,
the stage, both rule-document versions, the day the claims were aged
against, and the page size. Every one of them can change the list underneath
a caller who is halfway down it — a filter narrows it, a rule version
re-ranks it, a request that crosses UTC midnight re-ages it, and a different
`limit` turns the same offset into a different window. A cursor that does
not describe the list being asked for is a 400, not a best-effort re-page.

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

**Why every group is always the truth.** `stage` narrows nothing; it names
which group the cursor addresses. A response where three groups were empty
because the caller asked about the fourth would be indistinguishable from a
caseload that really had nothing in those stages — and "no claims in this
stage" is a message this story owes the user honestly (NFR-3).
"""

import base64
import binascii
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
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
      at `limit=10` ends at offset 10; a follow-up that forgot to repeat the
      limit would read `[10:60]` and hand the caller the ten rows they
      already had.
    """

    queue_filter: QueueFilter
    stage: Stage
    offset: int
    rules_version: int
    thresholds_version: int
    as_of: date
    limit: int


def encode_cursor(cursor: Cursor) -> str:
    """Base64url of a compact JSON object, unpadded.

    Opaque by intent rather than by encryption: it carries no claim data and
    nothing a caller could use to widen their scope (scope is never in the
    request — AD-7), so obscurity is not doing security work here. What the
    encoding buys is that clients treat it as a token to hand back rather
    than an offset to increment, which is what will let a later story swap
    the strategy without breaking them.
    """
    payload = json.dumps(
        {
            "f": cursor.queue_filter.value,
            "s": cursor.stage.value,
            "o": cursor.offset,
            "v": cursor.rules_version,
            "t": cursor.thresholds_version,
            "d": cursor.as_of.isoformat(),
            "l": cursor.limit,
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


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
    """
    try:
        padded = raw + "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        cursor = Cursor(
            queue_filter=QueueFilter(data["f"]),
            stage=Stage(data["s"]),
            offset=int(data["o"]),
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
    if cursor.offset < 0:
        raise InvalidCursor("The pagination cursor names a negative position.")
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
    """

    groups: Mapping[Stage, StageGroup]
    rules_version: int
    thresholds_version: int
    unfiltered_total: int
    filtered_total: int


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

    Sorted on `(-score, claim_id)`. The tie-break is not cosmetic: ties are
    common (a settled group where every claim carries the same penalty and a
    similar severity), and a sort with a non-total key leaves equal elements
    in whatever order the input arrived — which would let a cursor into the
    group repeat one claim and drop another between two requests.
    """
    members = [
        (claim, flags, score)
        for claim, flags, score in scored
        if claim.stage is stage and matches(queue_filter, claim, flags)
    ]
    members.sort(key=lambda entry: (-entry[2], entry[0].claim_id))

    markers = priority_markers([score for _claim, _flags, score in members], weights)
    return [
        QueueCard(claim=claim, flags=flags, priority_score=score, priority_marker=marker)
        for (claim, flags, score), marker in zip(members, markers, strict=True)
    ]


def _page(
    cards: Sequence[QueueCard],
    stage: Stage,
    queue_filter: QueueFilter,
    offset: int,
    limit: int,
    rules_version: int,
    thresholds_version: int,
    as_of: date,
) -> StageGroup:
    # A cursor is only ever issued for an offset that has claims behind it,
    # so an offset at or past the end describes a list this one is not. The
    # alternative — an empty page beside a non-zero `total` and a null
    # `nextCursor` — reads as a group that is simultaneously populated and
    # finished, which a client can only render as a lie.
    if offset > 0 and offset >= len(cards):
        raise InvalidCursor(
            f"That page starts at {offset} but the {stage.value!r} group now holds "
            f"{len(cards)} claims; reload the queue from the first page."
        )
    window = cards[offset : offset + limit]
    exhausted = offset + limit >= len(cards)
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
                    offset=offset + limit,
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
    """
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

    groups: dict[Stage, StageGroup] = {}
    for group_stage in STAGE_ORDER:
        cards = _ranked_group(scored, group_stage, queue_filter, weights)
        offset = decoded.offset if decoded is not None and decoded.stage is group_stage else 0
        groups[group_stage] = _page(
            cards,
            group_stage,
            queue_filter,
            offset,
            page_size,
            weights.version,
            thresholds.version,
            aged_on,
        )

    return ClaimQueue(
        groups=groups,
        rules_version=weights.version,
        thresholds_version=thresholds.version,
        # `scored` is every scoped row, before `matches` narrowed anything;
        # the group totals are what survived it. Both are counted here so
        # the SPA does neither.
        unfiltered_total=len(scored),
        filtered_total=sum(group.total for group in groups.values()),
    )
