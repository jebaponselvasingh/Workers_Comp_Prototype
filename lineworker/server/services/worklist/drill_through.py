"""The dashboard's drill-through — the claims behind a number (FR-SUP-D, AC 1).

Epic 5 ends at the figures. Ten KPI cards, six chart families, a ranked handler
table and a top-N worklist all render numbers, and until this module there was
no way to ask any of them *which claims*. `GET /claims/queue` cannot answer it:
it takes one of eight operational modes, covers four of the ten cards and none
of the chart facets, and is the handler's list rather than the portfolio's.

So: one more scoped aggregate. It reads the caller's book once, narrows it by a
whitelist of twelve facets, ranks what survives with the queue's own scorer, and
pages it with a cursor. Every row it emits is the **queue card's field set,
field for field**, so the list a supervisor opens from a donut slice looks like
the list a handler works from — and `test_the_drill_row_is_the_queue_card_field_
for_field` is what keeps the two from drifting.

## Twelve facets, a whitelist, and each one is the clicked surface's own rule

The filter set is closed and typed rather than free-form (`?where=severity>60`),
and that is the whole architectural move. A free-form filter language would put
a second query planner in this codebase and would let a caller ask a question no
dashboard surface asks — while the *only* requirement this endpoint actually has
is that the list it opens **reconciles with the number that opened it**.

That requirement is not satisfied by writing twelve plausible predicates. It is
satisfied by each predicate being the same symbol the counting surface used, and
this project has already recorded three near-misses where a plausible predicate
would have opened a plausible list containing the wrong claims:

- **Fraud is `fraud_flagged`, never `siu_review`.** Two rules over one column
  pair: the dashboard's *review* threshold and the queue's *referral* one. The
  Fraud Flags card counts the wider set; a drill-through pointed at the narrower
  one would open a shorter list under a card that had promised a longer one, and
  every step of it would look defensible. `FraudFlaggedDerivation`'s docstring
  exists for this.
- **Settled is a *stage*, never `status == settled_closed`.** Story 5.1's
  ruling, restated by 5.3's donut and by the worklist's treatment arm. The two
  readings differ by eight claims on the seeded book, and the donut counts the
  stage.
- **Injury type and state are the exact stored string.** `charts.py:132-142`
  refuses to trim, case-fold or merge near-duplicates on the way to drawing a
  bar, because canonicalizing free text is a data-quality decision with an
  owner. A filter that *did* canonicalize would return a set the bar never
  counted — a longer list than the number that opened it.

`filter[priority]` is the sharpest case and gets the sharpest answer: it is
`priority_claims.qualifies_for_worklist`, **imported**, not a restatement of the
union. A restated `stage == treatment or fraud or litigation` would agree on the
seeded book and would disagree the first time either arm moved — which is the
two bullets above, again, in one expression.

The predicates are one table (`_PREDICATES`) rather than twelve branches, in
`priority._PREDICATES`' shape, so "each facet reads its own owner" is a list a
reviewer checks in one screen instead of a property spread over a function.

## The ordering is imported, and there is no `sort` parameter

`priority.order_key` over `priority.priority_score` — the symbol Story 5.4
extracted out of `queue._ranked_group` so that "the same ordering" is a shared
function rather than a sentence. There is no second sort anywhere in this
module and no way for a caller to ask for one: an offset cursor into a ranked
list is only meaningful against the ranking that produced it, and a `sort`
parameter would make every outstanding cursor ambiguous.

The 🔺 marker rides along for the same reason it does on a queue card, and it is
computed over the **filtered** list before the page is cut — `priority_markers`'
own ruling: the marker describes the top of the list it is drawn on, and it is
deliberately insensitive to paging.

## Scope is not a filter, and there is nowhere to put one

`employer_scope(ctx)` is on the WHERE clause of the one read, as it is
everywhere else (AD-7). `filter[employerId]` and `filter[handlerId]` are
narrowings applied *after* it, over rows the caller was already entitled to
read — so a scoped supervisor naming an employer outside her book gets an empty
page, never a row and never a 403. There is no signature here, at any layer,
with room for a scope: not the route's thirteen parameters, not `DrillFilters`,
not this module's entry point. That is what makes
`test_query_parameters_cannot_widen_or_change_the_scope` a property of the shape
rather than of a validator.

## One scoped read

`select_drill_rows`, once, over the whole book — the ranking is a total order
over the filtered population, so there is no page of it to read — and then
filtering, ranking, counting and paging in Python over derived rows. That split
is not a preference: `select_queue_rows`' docstring already ruled that the
repository decides *which rows* (scope, and nothing else), and half of these
facets read values no column carries. `test_the_aggregate_takes_exactly_one_
scoped_read` counts the statements.

## Why this module is not `priority_claims.py`

They answer different questions over the same book. That one shows the top N of
a fixed population, capped by a rule document, with a generated action per row.
This one shows **all** of an arbitrary narrowing, uncapped, as queue cards. The
cap is the difference that matters: a drill-through that quietly showed thirty
of ninety claims would be a list disagreeing with the card that opened it, which
is the one thing this module exists not to do. So `total` here is the filtered
population and every one of it is reachable by paging.
"""

import base64
import binascii
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import date
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from data.context import CallerContext
from data.models.enums import ClaimStatus, ReturnStatus, Stage
from data.repositories import claims as claim_repo
from rules.parameters import DerivationThresholds, PriorityWeights
from services import derivations
from services.derivations import (
    FraudFlaggedDerivation,
    PaymentDueDerivation,
    RiskBand,
    RiskDerivation,
    RtwBlockedDerivation,
    SiuReviewDerivation,
    utc_today,
)
from services.worklist.priority import (
    QueueClaim,
    QueueFlags,
    order_key,
    priority_markers,
    priority_score,
)
from services.worklist.priority_claims import qualifies_for_worklist
from services.worklist.queue import (
    MAX_CURSOR_AGE,
    MAX_PAGE_LIMIT,
    MIN_PAGE_LIMIT,
    InvalidCursor,
)


@dataclass(frozen=True)
class DrillClaim:
    """One claim: the queue card's facts, plus the four columns the filters read.

    A projection rather than the ORM entity, for `QueueClaim`'s and
    `PriorityClaim`'s reasons — it keeps `select` pure and generatable, and it
    puts every input to the twelve predicates and the score next to each other
    instead of spread across a row object with forty other columns.

    Three groups of fields, and the grouping is the contract:

    - The **queue card's** thirteen, so `DrillRow` can be built without a second
      read and `ClaimCard` can render a drill list and a queue group with one
      component.
    - The **four filter columns** — `employer_id`, `handler_id`, `state`,
      `osha_recordable` — which no queue card shows and four of the twelve
      facets narrow on. The two ids rather than the two names: a name is a
      label, and `benchmarks.py` has grouped on `handler_id` since Story 5.2
      because two handlers may share a display name.
    - `handler_name`, which is neither. It exists so the applied-filter chip for
      `filter[handlerId]` can say who, resolved off a row this aggregate already
      read rather than from a second query for a caption. See `AppliedFilter`.

    `days_open` is here rather than `froi_date` for `QueueClaim`'s reason: it is
    *derived* (`services/derivations/open_duration`), and both the scorer and the
    card's age must consume the registry's answer rather than age a date.
    """

    claim_id: str
    stage: Stage
    status: ClaimStatus
    return_status: ReturnStatus
    severity_score: int
    days_open: int
    injury_type: str
    worker_name: str
    employer_short_name: str
    surgery_required: bool
    litigation_flag: bool
    fraud_flag: bool
    fraud_score: int
    employer_id: int
    handler_id: int
    handler_name: str
    state: str
    osha_recordable: bool


@dataclass(frozen=True)
class DrillFlags:
    """The AD-10 derived values one row needs, as the registry computed them.

    `QueueFlags`' four — the band and the three the scorer weights — widened by
    `fraud_flagged`, which the scorer has no use for and this surface has two:
    it is the `filter[fraudFlagged]` facet *and* the fraud arm of
    `filter[priority]`.

    **Declared here rather than imported from `priority_claims.PriorityFlags`,
    whose field set this is exactly.** What the two modules share is the
    *registry*, not the bundle. Importing that class would make this module's
    predicate table depend on a dataclass owned by a surface that ranks, caps
    and generates actions — so a sixth member added there for the action
    generator's benefit would silently become an input to the drill's filter
    vocabulary. `PriorityFlags` split from `QueueFlags` one level down for the
    same reason, and its docstring says so.
    """

    risk: RiskBand
    siu_review: bool
    rtw_blocked: bool
    payment_due: bool
    fraud_flagged: bool


@dataclass(frozen=True)
class DrillFilters:
    """The twelve facets, each optional, each `None` when the caller omitted it.

    One field per facet rather than a `Mapping[str, str]`, and the **type of
    each field is the refusal**: `filter[stage]=banana` cannot reach this class,
    because FastAPI coerces the query parameter into `Stage` and answers 422
    before the service is called. That is the whole of the validation story —
    there is no vocabulary check in this module, and there must not be one, or
    the enum would be spelled twice.

    `__post_init__` refuses **nothing**, deliberately. `stage=settled` beside
    `priority=true` is unsatisfiable on today's population, `severityBand=low`
    beside `fraudFlagged=true` may be unsatisfiable tomorrow, and an
    unsatisfiable combination is a legitimate empty page rather than an error:
    the caller asked a well-formed question whose answer is "none". Refusing it
    would mean this class knowing which combinations the *data* can satisfy,
    which is a fact about a seed rather than about a contract.

    The two booleans that read like their column and the four that do not are
    named for the surface rather than the column on purpose: `litigation` is the
    Litigation card, `surgery` is Surgery Required, `osha_recordable` is OSHA
    Recordable, and `priority` is the worklist's population — a caller reading
    the URL should recognise the thing they clicked.
    """

    stage: Stage | None = None
    severity_band: RiskBand | None = None
    fraud_flagged: bool | None = None
    litigation: bool | None = None
    surgery: bool | None = None
    osha_recordable: bool | None = None
    recovery_status: ReturnStatus | None = None
    injury_type: str | None = None
    state: str | None = None
    employer_id: int | None = None
    handler_id: int | None = None
    priority: bool | None = None


#: The facet names, in the order a chip row draws them, declared once.
#:
#: Read off `DrillFilters`' own fields rather than written out, so a thirteenth
#: facet cannot be added to that dataclass and forgotten here — which would
#: publish a filter that narrowed the list and never appeared in
#: `appliedFilters`, i.e. a chip the caller cannot see and therefore cannot
#: clear. `_PREDICATES` is asserted total against this tuple at import time for
#: the other half of the same failure.
FILTER_KEYS: Final[tuple[str, ...]] = tuple(field.name for field in fields(DrillFilters))


#: Each facet's field name → the spelling the wire uses for it.
#:
#: The values are exactly the strings inside the route's `filter[…]` aliases,
#: and that is the whole reason this table exists: a chip publishes its `key` so
#: that clicking its ✕ can remove *that* parameter from the URL, which only
#: works if the key the server published and the parameter name the server
#: accepts are the same string. Publishing the Python field name instead would
#: give the browser `severity_band` for a parameter called
#: `filter[severityBand]`, and the client would need a second translation table
#: — one that could disagree.
#:
#: camelCase rather than snake_case because that is the API's convention for
#: everything but enum *values*, and a facet name is not an enum value.
WIRE_KEYS: Final[Mapping[str, str]] = {
    "stage": "stage",
    "severity_band": "severityBand",
    "fraud_flagged": "fraudFlagged",
    "litigation": "litigation",
    "surgery": "surgery",
    "osha_recordable": "oshaRecordable",
    "recovery_status": "recoveryStatus",
    "injury_type": "injuryType",
    "state": "state",
    "employer_id": "employerId",
    "handler_id": "handlerId",
    "priority": "priority",
}

assert set(WIRE_KEYS) == set(FILTER_KEYS), (
    f"DrillFilters and WIRE_KEYS have diverged: {set(WIRE_KEYS) ^ set(FILTER_KEYS)}"
)


@dataclass(frozen=True)
class AppliedFilter:
    """One narrowing that was applied, as a chip can draw it.

    `key` is the facet's name, `value` is the wire form the caller sent
    (`settled`, `high`, `true`, `Boeing Everett`, `3`), and `display` is a
    human label **or `None`**.

    **`display` is server-resolved for exactly two of the twelve, and the split
    is the point.** Ten of the facets carry a value the UI already has copy for:
    `stage`, `severityBand` and `recoveryStatus` are enums whose labels the SPA
    owns (the Enums convention — a server that shipped "Settled & Closed" would
    be deciding copy over a contract), the four booleans are the cards' own
    names, and `injuryType` and `state` are free text where the stored value
    *is* the label. Resolving those here would be this module writing the UI's
    words.

    The other two are ids, and an id is not a label. Nothing in the browser can
    turn `handlerId=4` into "Marcus Chen" on a cold URL load — the dashboard
    that published the id may never have been rendered — so the chip would read
    "Handler: 4" or would need a second request. Both are worse than a string
    resolved off a row this aggregate already read.

    So: two resolved, ten `None`, and the client's rule is
    `display ?? UI_LABEL[key][value] ?? value`.
    """

    key: str
    value: str
    display: str | None


@dataclass(frozen=True)
class DrillRow:
    """One row of the drill list — **the queue card's field set, exactly**.

    Not "a similar set": the same fourteen fields, in the same spelling, so that
    `ClaimCard` renders a drill result and a queue group without a variant and
    the two cannot drift apart. `test_the_drill_row_is_the_queue_card_field_for_
    field` asserts the two *response* models' field sets are equal, which is
    where a divergence would actually show up.

    That is a real constraint rather than a tidy coincidence. It means this
    surface publishes no employer id, no handler name and no state — the three
    things it filters on and the card does not show — because a row is a claim
    as the console draws it, not a record of the query that found it. What was
    filtered is on `DrillClaims.applied_filters`, once, where a chip row can
    read it.

    Nothing here is a hint the client finishes: `risk` is a band rather than a
    score to compare, `priority_marker` is a decision rather than a rank to
    threshold, and `priority_score` is published for `ClaimCardResponse`'s
    recorded reason — it makes the ordering explainable — and never as an
    invitation to re-sort.
    """

    claim_id: str
    days_open: int
    risk: RiskBand
    worker_name: str
    injury_type: str
    stage: Stage
    employer_short_name: str
    fraud_flag: bool
    litigation_flag: bool
    payment_due: bool
    siu_review: bool
    rtw_blocked: bool
    priority_score: float
    priority_marker: bool


@dataclass(frozen=True)
class DrillClaims:
    """One page of a drill-through, and what the caller asked for to get it.

    **`total` is the filtered population and every one of it is reachable.**
    There is no cap on this list, which is the difference from
    `PriorityClaims` and is load-bearing rather than incidental: the whole
    promise of a drill-through is that its count equals the number that opened
    it, and a capped list would show thirty of a card's ninety-two while
    reporting ninety-two. `total` is stable across every page of a walk, for
    `StageGroupResponse.total`'s reason — a count that shrank as the page moved
    would misdescribe the book.

    `applied_filters` is what the chips draw, in `FILTER_KEYS` order, and it
    exists so the chip row is a rendering of the *server's* reading of the URL
    rather than a second parse of it. A URL carrying an unknown parameter name
    produces no chip, because the server ignored it; a URL carrying `filter[
    stage]=settled` produces exactly one, because the server applied exactly
    one. That is what makes "the chips, the request and the result agree" a
    property rather than a hope.

    Two rule versions rather than one, and each names a document that decided
    something visible: `rules_version` is `priority_weights`, which decided the
    **ordering** and the marker; `thresholds_version` is
    `derivation_thresholds`, which decided the band on every row and the fraud
    and priority facets' populations. Both are also what the cursor is validated
    against. They ride along unrendered, as the sibling dashboard payloads' do:
    it is what makes a stored or forwarded response self-describing, and the
    only thing a client wanting to invalidate on a rules change could key on.
    """

    items: tuple[DrillRow, ...]
    next_cursor: str | None
    total: int
    applied_filters: tuple[AppliedFilter, ...]
    rules_version: int
    thresholds_version: int


# --- the predicate table ------------------------------------------------


def _matches_priority(claim: DrillClaim, flags: DrillFlags, value: object) -> bool:
    """`filter[priority]` — the worklist's population predicate, imported.

    A named function rather than a lambda because it is the one entry in the
    table below whose right-hand side is a *call* rather than a field, and it is
    the entry a future reader is most likely to "simplify" back into three
    `or`-ed conditions. `qualifies_for_worklist` is the same symbol
    `priority_claims.rank` selects with, so "the drill-through opens the
    worklist's population" is one function with two callers rather than two
    unions that agree today.
    """
    return qualifies_for_worklist(claim, flags) == value


#: One predicate per facet, in one mapping — total by construction against
#: `FILTER_KEYS`, and checkable as such (the assertion below runs at import).
#:
#: `priority._PREDICATES`' shape and its reason: a `match` statement would read
#: the same and pass mypy, and nothing would notice a thirteenth facet added to
#: `DrillFilters` without a branch. Here it fails at import.
#:
#: Each entry names the owner of the rule the clicked surface counted with —
#: `severity_band` through the registered `risk` derivation, `fraud_flagged`
#: through the registered fraud rule and *not* `siu_review`, `stage` through the
#: stage column and not `status`, the two free-text facets against the exact
#: stored string. See the module docstring for what each of those would cost.
_PREDICATES: Final[Mapping[str, Callable[[DrillClaim, DrillFlags, Any], bool]]] = {
    "stage": lambda claim, _flags, value: claim.stage == value,
    "severity_band": lambda _claim, flags, value: flags.risk == value,
    "fraud_flagged": lambda _claim, flags, value: flags.fraud_flagged == value,
    "litigation": lambda claim, _flags, value: claim.litigation_flag == value,
    "surgery": lambda claim, _flags, value: claim.surgery_required == value,
    "osha_recordable": lambda claim, _flags, value: claim.osha_recordable == value,
    "recovery_status": lambda claim, _flags, value: claim.return_status == value,
    "injury_type": lambda claim, _flags, value: claim.injury_type == value,
    "state": lambda claim, _flags, value: claim.state == value,
    "employer_id": lambda claim, _flags, value: claim.employer_id == value,
    "handler_id": lambda claim, _flags, value: claim.handler_id == value,
    "priority": _matches_priority,
}

# Every facet has a predicate, and every predicate names a facet. A filter
# declared on `DrillFilters` with no entry here would be accepted by the route,
# published as a chip, and narrow nothing — a list that says it is filtered and
# is not. An import-time equality is the cheapest place to make that loud, and
# it costs nothing at request time.
assert set(_PREDICATES) == set(FILTER_KEYS), (
    f"DrillFilters and _PREDICATES have diverged: {set(_PREDICATES) ^ set(FILTER_KEYS)}"
)


def matches(claim: DrillClaim, flags: DrillFlags, filters: DrillFilters) -> bool:
    """Does this claim survive every facet the caller set?

    An `and` over the *set* facets only: an omitted facet is not a wildcard
    predicate that happens to return `True`, it is a question nobody asked. The
    distinction matters for `filter[litigation]=false`, which is a real
    narrowing (claims with no attorney) and not the same thing as omitting the
    facet — so the check is `is not None` on the field rather than a truthiness
    test on the value.
    """
    for key in FILTER_KEYS:
        value = getattr(filters, key)
        if value is None:
            continue
        if not _PREDICATES[key](claim, flags, value):
            return False
    return True


# --- the cursor ---------------------------------------------------------


def _wire_value(value: object) -> str | int | bool:
    """One filter value as JSON puts it in a cursor and a chip shows it.

    Enums become their wire value (snake_case, per the Enums convention),
    everything else travels as itself. Deliberately *not* `str()` across the
    board: `str(True)` is `"True"`, which is neither the query string's `true`
    nor round-trippable through `bool(...)`, and an id is an integer on the
    wire everywhere else in this console.
    """
    if isinstance(value, Stage | RiskBand | ReturnStatus):
        return value.value
    if isinstance(value, bool | int | str):
        return value
    raise TypeError(f"unencodable filter value {value!r}")  # pragma: no cover


def _filter_payload(filters: DrillFilters) -> dict[str, str | int | bool]:
    """The set facets as a plain dict, in `FILTER_KEYS` order.

    Ordered rather than merely equal, because this dict is what the cursor
    carries and what a decoded cursor is compared against: two dicts compare by
    content, but a *serialised* one compares by bytes, and an unordered build
    would mint two different cursors for one filter set depending on which
    parameter FastAPI happened to bind first.
    """
    payload: dict[str, str | int | bool] = {}
    for key in FILTER_KEYS:
        value = getattr(filters, key)
        if value is not None:
            payload[key] = _wire_value(value)
    return payload


@dataclass(frozen=True)
class Cursor:
    """Where a page ended, and everything that decided where that was.

    `priority_claims.Cursor`'s shape, widened by the one thing this list has and
    that one does not: **the filter set**.

    **Compared.**

    - `filters` — a cursor is an *offset* into a ranking, and an offset into a
      different ranking is a different place. Replaying page one's cursor under
      `filter[stage]=settled` would page into the settled list at the offset the
      unfiltered list ended at, silently skipping or repeating claims with
      nothing on screen to say so. `test_claims_queue.py` already refuses this
      for the queue's single filter; this is that ruling on a set.
    - `weights_version` (`priority_weights`) decides the ordering and the marker.
    - `thresholds_version` (`derivation_thresholds`) decides the band on every
      row *and* the populations behind `filter[severityBand]`,
      `filter[fraudFlagged]` and `filter[priority]` — so it can change which
      claims are in the list as well as where they sit in it.

    Both versions are compared against what is effective **today**, which is why
    `drill_through_claims` resolves nothing at the cursor's own date: a
    comparison against the versions effective on the date the cursor recorded
    could only ever succeed, which is how the queue's arrangement was wrong on
    its first outing.

    **Reused.** `as_of` and `limit` describe the *window* rather than the rules,
    and re-deriving either mid-list is what loses or repeats a claim — a "Show
    more" issued at 23:59:59 UTC and answered at 00:00:01 would re-age every
    claim and re-rank the list around it. Both are still validated, because a
    cursor is caller-supplied input like any other; and `limit` is checked
    against the effective rule as well as the transport range, because this
    route declares no page-size parameter and the cursor is the only place one
    could be smuggled in — the defect Story 5.4 shipped and corrected.
    """

    offset: int
    limit: int
    filters: DrillFilters
    weights_version: int
    thresholds_version: int
    as_of: date


def encode_cursor(cursor: Cursor) -> str:
    """Base64url of a compact JSON object, unpadded — `queue.encode_cursor`'s form.

    Opaque by intent rather than by encryption, for that function's reason: it
    carries no claim data and nothing a caller could use to widen their scope
    (scope is never in a request — AD-7), so obscurity is doing no security
    work. What the encoding buys is that clients treat it as a token to hand
    back rather than an offset to increment.
    """
    payload = json.dumps(
        {
            "o": cursor.offset,
            "l": cursor.limit,
            "f": _filter_payload(cursor.filters),
            "v": cursor.weights_version,
            "t": cursor.thresholds_version,
            "d": cursor.as_of.isoformat(),
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(raw: str, as_of: date | None = None) -> Cursor:
    """Parse a cursor, or refuse it. Never a silent fallback to page one.

    `priority_claims.decode_cursor`'s body and every one of its arguments, each
    of which is a defect that reached one of these functions first:

    - Answering page one for an undecodable cursor would turn a client bug into
      an infinite "Show more" that re-appends the same claims for ever.
    - `ArithmeticError` sits in the except tuple beside `ValueError` because
      `json` accepts the literal `Infinity` and `int(float("inf"))` raises
      `OverflowError`, which is **not** a `ValueError` — a forged cursor
      carrying it escaped the queue as a 500 rather than the 400 this function
      exists to produce.
    - `limit` is held to `MIN_PAGE_LIMIT`–`MAX_PAGE_LIMIT` here and to the
      *effective rule* in `drill_through_claims`. The route declares no `limit`
      parameter, so the cursor is the only place a page size could be smuggled
      in.
    - `as_of` is bounded by `MAX_CURSOR_AGE`. It cannot select a rule document,
      so a hostile date never reaches the loader — but a claim aged against the
      year 3000 ranks by arithmetic nobody asked for, and "reload from the first
      page" is the truthful answer.

    The filter set is rebuilt through `DrillFilters(**…)`, so a forged cursor
    naming a facet that does not exist, or a value the field's type cannot hold,
    is a `TypeError`/`ValueError` here rather than a filter nothing applies.
    The two version fields are parsed and bounded here and *compared* in
    `drill_through_claims`, which is the only place that knows what is effective
    today.
    """
    try:
        padded = raw + "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        cursor = Cursor(
            offset=int(data["o"]),
            limit=int(data["l"]),
            filters=_filters_of(data["f"]),
            weights_version=int(data["v"]),
            thresholds_version=int(data["t"]),
            as_of=date.fromisoformat(data["d"]),
        )
    except (
        AttributeError,
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
            "reload the list from the first page."
        )
    if today - cursor.as_of > MAX_CURSOR_AGE:
        raise InvalidCursor(
            f"The pagination cursor is dated {cursor.as_of} and the claims it ranked have "
            f"aged {(today - cursor.as_of).days} days since; "
            "reload the list from the first page."
        )
    return cursor


def _filters_of(payload: object) -> DrillFilters:
    """A decoded cursor's filter dict, back into the typed dataclass.

    Every value is coerced through the field's own type rather than trusted as
    JSON left it, because JSON has three of the six types this dataclass uses
    and a forged cursor can spell any of them: `{"stage": "banana"}` must be a
    refusal and not a facet that matches nothing, and `{"employer_id": "3"}`
    must not become a filter that compares a string against an integer column
    for ever. `Stage("banana")` raises `ValueError`, which the caller above
    turns into the same 400 every other unreadable cursor gets.
    """
    if not isinstance(payload, dict):
        raise TypeError(f"the cursor's filter set is {type(payload).__name__}, not an object")
    unknown = set(payload) - set(FILTER_KEYS)
    if unknown:
        raise ValueError(f"the cursor names unknown filters: {sorted(unknown)}")
    coerced: dict[str, object] = {}
    for key, raw in payload.items():
        coerced[key] = _COERCE[key](raw)
    return DrillFilters(**coerced)  # type: ignore[arg-type]


def _as_bool(raw: object) -> bool:
    """A cursor's boolean, refused unless it really is one.

    `bool(raw)` would accept `"false"` as `True`, which is the shape a forged
    cursor takes when somebody hand-edits the JSON — and it would flip a facet's
    meaning while decoding cleanly.
    """
    if not isinstance(raw, bool):
        raise TypeError(f"expected a boolean, got {type(raw).__name__}")
    return raw


def _as_int(raw: object) -> int:
    """A cursor's id. `bool` is an `int` in Python and is refused explicitly."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TypeError(f"expected an integer, got {type(raw).__name__}")
    return raw


def _as_str(raw: object) -> str:
    if not isinstance(raw, str):
        raise TypeError(f"expected a string, got {type(raw).__name__}")
    return raw


#: How each facet's cursor-borne value becomes the field's own type.
#:
#: A table beside `_PREDICATES` rather than a chain of `isinstance` checks
#: inside `_filters_of`, for that table's reason: a thirteenth facet needs an
#: entry here, and the assertion below is where a reader finds that out.
_COERCE: Final[Mapping[str, Callable[[Any], object]]] = {
    "stage": lambda raw: Stage(_as_str(raw)),
    "severity_band": lambda raw: RiskBand(_as_str(raw)),
    "fraud_flagged": _as_bool,
    "litigation": _as_bool,
    "surgery": _as_bool,
    "osha_recordable": _as_bool,
    "recovery_status": lambda raw: ReturnStatus(_as_str(raw)),
    "injury_type": _as_str,
    "state": _as_str,
    "employer_id": _as_int,
    "handler_id": _as_int,
    "priority": _as_bool,
}

assert set(_COERCE) == set(FILTER_KEYS), (
    f"DrillFilters and _COERCE have diverged: {set(_COERCE) ^ set(FILTER_KEYS)}"
)


# --- the pure selection -------------------------------------------------


@dataclass(frozen=True)
class _Computers:
    """The five registered computers this module folds with, built once.

    A bundle rather than five parameters, for `priority_claims._Computers`'
    reason: `_flags_of` is called once per claim in `select`'s loop, and a
    five-argument call there would put the build order and the call order in two
    places.
    """

    risk: RiskDerivation
    siu_review: SiuReviewDerivation
    rtw_blocked: RtwBlockedDerivation
    payment_due: PaymentDueDerivation
    fraud_flagged: FraudFlaggedDerivation

    @classmethod
    def of(cls, thresholds: DerivationThresholds) -> "_Computers":
        """Build all five from one parameter block — the registry's whole point."""
        return cls(
            risk=derivations.risk.for_thresholds(thresholds),
            siu_review=derivations.siu_review.for_thresholds(thresholds),
            rtw_blocked=derivations.rtw_blocked.for_thresholds(thresholds),
            payment_due=derivations.payment_due.for_thresholds(thresholds),
            fraud_flagged=derivations.fraud_flagged.for_thresholds(thresholds),
        )


def _flags_of(claim: DrillClaim, computers: _Computers) -> DrillFlags:
    """One claim's five derived values, every one of them asked of the registry.

    Takes the built computers rather than the threshold block, which is the
    difference between one build and one per claim — `queue._rows_to_cards`'
    shape, for its reason. `select` builds them once, above its loop.
    """
    band = computers.risk.of(claim.severity_score)
    return DrillFlags(
        risk=band,
        siu_review=computers.siu_review.of(
            fraud_flag=claim.fraud_flag, fraud_score=claim.fraud_score
        ),
        rtw_blocked=computers.rtw_blocked.of(
            claim_id=claim.claim_id,
            stage=claim.stage,
            return_status=claim.return_status,
            risk=band,
        ),
        payment_due=computers.payment_due.of(claim_id=claim.claim_id, stage=claim.stage),
        fraud_flagged=computers.fraud_flagged.of(
            fraud_flag=claim.fraud_flag, fraud_score=claim.fraud_score
        ),
    )


def _queue_claim(claim: DrillClaim) -> QueueClaim:
    """This projection, narrowed to the eleven facts the scorer reads.

    Restated field by field rather than shared by inheritance, and named rather
    than positional, for `priority_claims._queue_claim`'s two reasons: keeping
    the two projections separate keeps `priority_score`'s signature a statement
    about what the *formula* needs, and a positional build over four adjacent
    free-text columns is one reordering away from scoring an injury type as a
    worker's name.
    """
    return QueueClaim(
        claim_id=claim.claim_id,
        stage=claim.stage,
        status=claim.status,
        severity_score=claim.severity_score,
        days_open=claim.days_open,
        injury_type=claim.injury_type,
        worker_name=claim.worker_name,
        employer_short_name=claim.employer_short_name,
        surgery_required=claim.surgery_required,
        litigation_flag=claim.litigation_flag,
        fraud_flag=claim.fraud_flag,
    )


@dataclass(frozen=True)
class RankedClaim:
    """One survivor of the filter set, with what the ordering and the row need.

    Three values rather than a bare `DrillClaim`, because the score is computed
    inside `select`'s single pass and re-deriving it to build a row would be a
    second evaluation of the scorer over five registered derivations — and
    because the value the *ordering* used is then the value a test can assert
    against. `priority_claims.rank` returns pairs for the same reason; this
    carries a third member because a drill row publishes `priorityScore` where
    a worklist row does not.
    """

    claim: DrillClaim
    flags: DrillFlags
    priority_score: float


def select(
    caseload: Sequence[DrillClaim],
    filters: DrillFilters,
    thresholds: DerivationThresholds,
    weights: PriorityWeights,
) -> tuple[list[RankedClaim], int]:
    """The filtered population, ranked. Pure — no session, no clock, no rule load.

    Returns the whole ranked list **and its length**, and the length is not
    redundant with `len(...)` at the call site so much as it is the *contract*:
    this list has no cap, so `total` is the population and every one of it is
    reachable by paging. Returning them together is what makes "the count and
    the pages describe one set" structural rather than a comment —
    `charts_of`'s and `rank`'s argument.

    Three steps, in this order, and the order is the rule:

    1. Derive every claim's flags through the registry (AD-10). **Before** the
       filter, because three of the twelve facets — `severityBand`,
       `fraudFlagged`, `priority` — *are* derived values.
    2. Apply every set facet (`matches`). An unset facet narrows nothing.
    3. Sort with `priority.order_key` over `priority.priority_score` — the
       queue's ordering and the queue's scorer, imported. Not "the same
       arithmetic": the same two functions.

    The score is computed **once per claim**, into the sort key, rather than
    inside the comparator: `sorted(key=…)` calls its key once per element, so a
    key that scored on the fly would still be correct, but a re-entrant one
    would score O(n log n) times over five registered derivations.

    Nothing here re-weights and nothing here cuts. The settled penalty applies
    as written even on `filter[stage]=settled`, where every member pays it and
    it therefore cancels — the scorer is one function and this module does not
    get to hold a variant of it.
    """
    # Built once, above the loop — see `_Computers`.
    computers = _Computers.of(thresholds)
    scored: list[tuple[DrillClaim, DrillFlags, QueueClaim, float]] = []
    for claim in caseload:
        flags = _flags_of(claim, computers)
        if not matches(claim, flags, filters):
            continue
        queue_claim = _queue_claim(claim)
        scored.append(
            (
                claim,
                flags,
                queue_claim,
                priority_score(
                    queue_claim,
                    QueueFlags(
                        risk=flags.risk,
                        siu_review=flags.siu_review,
                        rtw_blocked=flags.rtw_blocked,
                        payment_due=flags.payment_due,
                    ),
                    weights,
                ),
            )
        )
    scored.sort(key=lambda entry: order_key((entry[2], entry[3])))
    ranked = [
        RankedClaim(claim=claim, flags=flags, priority_score=score)
        for claim, flags, _queue, score in scored
    ]
    return ranked, len(ranked)


def _row(entry: RankedClaim, marker: bool) -> DrillRow:
    """One ranked claim as the queue card draws it.

    Named rather than positional for `priority_claims._row`'s reason, over a
    fourteen-field row where five adjacent booleans and three adjacent strings
    would each survive a swap, type-check, run, and put the wrong badge on the
    wrong claim.
    """
    return DrillRow(
        claim_id=entry.claim.claim_id,
        days_open=entry.claim.days_open,
        risk=entry.flags.risk,
        worker_name=entry.claim.worker_name,
        injury_type=entry.claim.injury_type,
        stage=entry.claim.stage,
        employer_short_name=entry.claim.employer_short_name,
        fraud_flag=entry.claim.fraud_flag,
        litigation_flag=entry.claim.litigation_flag,
        payment_due=entry.flags.payment_due,
        siu_review=entry.flags.siu_review,
        rtw_blocked=entry.flags.rtw_blocked,
        priority_score=entry.priority_score,
        priority_marker=marker,
    )


def _applied(filters: DrillFilters, caseload: Sequence[DrillClaim]) -> tuple[AppliedFilter, ...]:
    """The chips, in `FILTER_KEYS` order, with the two id displays resolved.

    Resolved from `caseload` — the rows this aggregate already read — and never
    from a second query, which is what keeps the endpoint at one scoped read. It
    also keeps the resolution *inside the scope*: a scoped supervisor naming an
    employer outside her book gets `display: null` rather than that employer's
    name, so the chip cannot become an oracle for the existence of an employer
    she cannot see (AD-7, the same reason `select_claim_detail` answers `None`
    twice over).

    A linear scan rather than a pre-built index: at most two ids are ever
    resolved, over a caseload bounded by one persona's book, and an index built
    per request would be a dict of every employer and handler in scope to answer
    two lookups.
    """
    applied: list[AppliedFilter] = []
    for key in FILTER_KEYS:
        value = getattr(filters, key)
        if value is None:
            continue
        display: str | None = None
        if key == "employer_id":
            display = next(
                (claim.employer_short_name for claim in caseload if claim.employer_id == value),
                None,
            )
        elif key == "handler_id":
            display = next(
                (claim.handler_name for claim in caseload if claim.handler_id == value),
                None,
            )
        applied.append(AppliedFilter(key=WIRE_KEYS[key], value=_chip_value(value), display=display))
    return tuple(applied)


def _chip_value(value: object) -> str:
    """A facet's value as the URL spells it — the string a chip's label keys on.

    `true`/`false` rather than `True`/`False`, because the client's label map is
    keyed on what the query string carries and a Python repr is not that.
    """
    wire = _wire_value(value)
    if isinstance(wire, bool):
        return "true" if wire else "false"
    return str(wire)


async def drill_through_claims(
    db: AsyncSession,
    ctx: CallerContext,
    thresholds: DerivationThresholds,
    weights: PriorityWeights,
    filters: DrillFilters,
    *,
    cursor: str | None = None,
    as_of: date | None = None,
) -> DrillClaims:
    """One page of the claims behind a dashboard number — the endpoint's one call.

    Takes its two parameter blocks rather than fetching either, which is
    `portfolio_charts`' and `priority_claims`' signature and their reason: the
    route loads them once and hands them down, so this stays a composition of
    scope and parameters instead of dragging the rules engine into an aggregate
    that would then have to decide which date to resolve at.

    **Two dates, and they are not interchangeable** — `claim_queue`'s division,
    which this cursor inherits. `today` is when the request is served and is what
    the recorded versions are compared against, so a page cut under a superseded
    document is refused rather than served from a retired ranking. `aged_on` is
    the day the claims are aged against; a cursor's recorded day wins there, and
    only there, so page two is cut from the list page one was.

    **One awaited read, regardless of page size or filter set.** The ranking is
    a total order over the filtered population, so there is no page of it to
    read, and the two id chips are resolved from the rows that read returned.
    `test_the_aggregate_takes_exactly_one_scoped_read` counts the statements.

    No role appears anywhere in this path. Supervisor, analyst and handler take
    the identical scoped route through the repository, which is the whole of
    AD-7 on this surface — see the route's docstring for why this endpoint has
    no gate.
    """
    decoded = decode_cursor(cursor, as_of) if cursor is not None else None
    today = as_of or utc_today()
    aged_on = decoded.as_of if decoded is not None else today
    page_size = weights.page_limit

    if decoded is not None:
        if decoded.filters != filters:
            raise InvalidCursor(
                "That page was cut from a differently filtered list; "
                "reload the list from the first page."
            )
        if decoded.weights_version != weights.version:
            raise InvalidCursor(
                f"That page was ranked by priority_weights v{decoded.weights_version}, "
                f"and v{weights.version} is now effective; "
                "reload the list from the first page."
            )
        if decoded.thresholds_version != thresholds.version:
            raise InvalidCursor(
                f"That page was derived from derivation_thresholds "
                f"v{decoded.thresholds_version}, and v{thresholds.version} is now effective; "
                "reload the list from the first page."
            )
        # Checked against the *effective rule* and not merely against the
        # transport range, which `decode_cursor` cannot do — the rule is not in
        # scope there. A cursor is unsigned base64 JSON, so `decoded.limit` is
        # caller-supplied in every sense that matters, and taking it on trust
        # would hand back through the cursor exactly the parameter this route
        # refuses to declare. Story 5.4 shipped that defect and corrected it;
        # this is the correction, written first.
        if decoded.limit != page_size:
            raise InvalidCursor(
                f"That page was cut at {decoded.limit} rows, and the list now "
                f"pages at {page_size}; reload the list from the first page."
            )

    days_open = derivations.days_open.for_thresholds(thresholds)
    rows = await claim_repo.select_drill_rows(db, ctx)
    # Named rather than positional (`DrillClaim(*row)`), which would work and
    # would be one reordered projection away from filtering claims by their
    # worker's name — `charts.portfolio_charts`' argument, on a projection
    # carrying five adjacent free-text columns and four adjacent booleans.
    caseload = [
        DrillClaim(
            claim_id=row.claim_id,
            stage=row.stage,
            status=row.status,
            return_status=row.return_status,
            severity_score=row.severity_score,
            days_open=days_open.of(row.froi_date, aged_on),
            injury_type=row.injury_type,
            worker_name=row.worker_name,
            employer_short_name=row.employer_short_name,
            surgery_required=row.surgery_required,
            litigation_flag=row.litigation_flag,
            fraud_flag=row.fraud_flag,
            fraud_score=row.fraud_score,
            employer_id=row.employer_id,
            handler_id=row.handler_id,
            handler_name=row.handler_name,
            state=row.state,
            osha_recordable=row.osha_recordable,
        )
        for row in rows
    ]

    ranked, total = select(caseload, filters, thresholds, weights)
    # Over the whole filtered list, before the page is cut — `priority_markers`'
    # own ruling, which this surface inherits unchanged: the marker describes the
    # top of the list it is drawn on, and asking for page 2 must never make a
    # fourth claim sprout one.
    markers = priority_markers([entry.priority_score for entry in ranked], weights)

    offset = decoded.offset if decoded is not None else 0
    # A cursor is only ever issued for an offset that has rows behind it, so an
    # offset at or past the end describes a list this one is not. The
    # alternative — an empty page beside a non-zero `total` and a null
    # `nextCursor` — reads as a list that is simultaneously populated and
    # finished, which a client can only render as a lie. `queue._page`'s rule.
    if offset > 0 and offset >= total:
        raise InvalidCursor(
            f"That page starts at {offset} but the list now holds {total} claims; "
            "reload the list from the first page."
        )
    window = range(offset, min(offset + page_size, total))

    return DrillClaims(
        items=tuple(_row(ranked[index], markers[index]) for index in window),
        # Null only when the list is finished — never "null because this page
        # came back short", which would strand a tail the count has already told
        # the reader is there.
        next_cursor=(
            None
            if offset + page_size >= total
            else encode_cursor(
                Cursor(
                    offset=offset + page_size,
                    limit=page_size,
                    filters=filters,
                    weights_version=weights.version,
                    thresholds_version=thresholds.version,
                    as_of=aged_on,
                )
            )
        ),
        total=total,
        applied_filters=_applied(filters, caseload),
        rules_version=weights.version,
        thresholds_version=thresholds.version,
    )


__all__ = [
    "FILTER_KEYS",
    "WIRE_KEYS",
    "AppliedFilter",
    "Cursor",
    "DrillClaim",
    "DrillClaims",
    "DrillFilters",
    "DrillFlags",
    "DrillRow",
    "InvalidCursor",
    "RankedClaim",
    "decode_cursor",
    "drill_through_claims",
    "encode_cursor",
    "matches",
    "select",
]
