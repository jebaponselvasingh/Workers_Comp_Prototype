"""Glossary reference reads — the second sanctioned unscoped repository.

**Why `list_terms` takes no `CallerContext`.** AD-7 binds every repository
that reads claim-scoped data: a required caller context and one
unconditional employer filter. `glossary_term` has no employer column, no
claim relationship and no PHI — it is 25 rows of industry vocabulary,
identical for a handler, a supervisor and an analyst. There is nothing here
to scope, so a context parameter would be decoration: accepted, ignored,
and read by the next author as evidence that scoping had been considered
when in fact nothing had been enforced. `identity.py` carves itself out for
a different reason (it is what *builds* the context); this module carves
itself out because the data it reads has no owner.

Authentication still applies — the endpoint sits behind the app-level
dependency — so "unscoped" here means "the same answer for every
authenticated caller", never "public".

**Paged for real since Story 9.8.** The ordering was already fixed
explicitly, on the unique `sort_order`, "so a paged version cannot silently
repeat or drop rows"; this is that paged version. `list_terms` now takes a
`limit` and an optional keyset position and the router mints a cursor from
it, `count_terms` answers `total` from a `COUNT(*)` rather than from
`len(items)`, and `filter[abbreviation]` / `sort` are the two Lists-convention
parameters this table can honestly offer. Before, the envelope claimed a
pagination that did not exist: `nextCursor` was structurally null, the read
had no `LIMIT`, and `total` was computed from the very list it was returned
beside — so a truncation could never have been detected.
"""

import base64
import binascii
import enum
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from data.models import GlossaryTerm

#: The statement `_filtered` narrows, whichever of the two it is.
#:
#: A type variable rather than two overloads, because the page and the count
#: have to apply the facet identically — see `_filtered` — and one function is
#: the only way to say that in a way a reader cannot un-say by editing one of
#: two bodies.
type _S = sa.Select[Any]


class GlossarySort(enum.StrEnum):
    """The orderings `?sort=` may name. Lowercase snake_case, per conventions.

    Three, not "any column": every option here has to be paired with a total
    order and tested as one, and a `sort` parameter that accepted an arbitrary
    column name would be both an unbounded contract and a place for a caller to
    name something that is not a column.

    `sort_order` is the default and is the prototype's display sequence — FNOL
    first, then the acronym cluster, then the manufacturing hazards. The two
    alphabetical options exist because a glossary is a reference list and "find
    me the A's" is what a reader does with one; they are not what the panel
    renders.
    """

    sort_order = "sort_order"
    abbreviation = "abbreviation"
    term = "term"


#: Which column each ordering sorts on, before the tie-break.
#:
#: A mapping rather than a `match`, `drill_through._PREDICATES`' reason: the
#: check below runs at import, so a fourth member added to `GlossarySort`
#: without a column fails the process rather than sorting by whatever the
#: `else` branch happened to be.
_SORT_COLUMNS: Mapping[GlossarySort, InstrumentedAttribute[Any]] = {
    GlossarySort.sort_order: GlossaryTerm.sort_order,
    GlossarySort.abbreviation: GlossaryTerm.abbreviation,
    GlossarySort.term: GlossaryTerm.term,
}
# An explicit raise rather than `assert`: this guard is the load-bearing half
# of the comment above, and `python -O` deletes assertions — which would leave
# the drift it exists to catch shipping in exactly the build that runs in
# production.
if set(_SORT_COLUMNS) != set(GlossarySort):
    raise RuntimeError("every glossary sort option needs a column")


def _filtered(statement: _S, abbreviation: str | None) -> _S:
    """`filter[abbreviation]`, applied identically to the page and to the count.

    One function rather than two spellings, because the whole value of a
    `COUNT(*)` is that it describes the list being paged: a count that applied
    the filter differently from the page would publish a `total` about a
    different question, which is the defect this endpoint's `len(items)` had in
    a louder form.

    Matched as the exact stored string — no trimming, case-folding or prefix
    match — which is `drill_through`'s rule for its two free-text facets. The
    SPA's live search stays a client-side rendering of a list it already holds
    (`GlossaryPanel.tsx`); this facet is for a caller that wants one row.
    """
    if abbreviation is None:
        return statement
    return statement.where(GlossaryTerm.abbreviation == abbreviation)


async def list_terms(
    db: AsyncSession,
    *,
    limit: int,
    after: tuple[str | int, int] | None = None,
    abbreviation: str | None = None,
    sort: GlossarySort = GlossarySort.sort_order,
) -> Sequence[GlossaryTerm]:
    """One page of glossary terms, in the requested order.

    **Keyset, not offset**, `select_meetings_page`'s idiom and its reason: an
    offset shifts under an insert, and although reference data changes only by
    migration, a paging strategy that is only correct because the table is
    quiet is a strategy nobody can reason about. `after` is the previous page's
    last row as `(sort value, sort_order)` and the predicate is the row-value
    comparison against the same two expressions the sort uses.

    **The tie-break is `sort_order` in every ordering, including its own.**
    `abbreviation` is unique today and `term` is not — "Apportionment" repeats
    its term verbatim (see the model) — so an ordering on either alone is
    partial, and a keyset page that ended inside a tie would repeat one row and
    drop another. Pairing every option with the one column the table guarantees
    unique makes all three total by construction rather than by coincidence.
    """
    column = _SORT_COLUMNS[sort]
    statement = _filtered(sa.select(GlossaryTerm), abbreviation)
    if after is not None:
        last_value, last_sort_order = after
        statement = statement.where(
            sa.tuple_(column, GlossaryTerm.sort_order)
            > sa.tuple_(
                # Typed literals rather than bare Python values: a row-value
                # comparison hands both sides to the driver as parameters, and
                # an untyped one reaches asyncpg with nothing to encode it as
                # (`select_meetings_page`'s lesson).
                sa.literal(last_value, column.type),
                sa.literal(last_sort_order, GlossaryTerm.sort_order.type),
            )
        )
    return (
        await db.scalars(statement.order_by(column, GlossaryTerm.sort_order).limit(limit))
    ).all()


async def count_terms(db: AsyncSession, *, abbreviation: str | None = None) -> int:
    """How many terms are in the list being paged — the envelope's `total`.

    A `COUNT(*)` over the same filter the page applies, so `total` means "rows
    that exist" and never "rows on this page". The distinction is the whole
    reason this function exists: `total = len(items)` is a number that agrees
    with itself whatever happens to the table, so a truncation — the exact
    failure a `LIMIT` introduces — could not have been detected from the
    payload.

    Counted on **every** page rather than the first only, unlike
    `list_email_logs`. That endpoint pages a handler's whole sent log; this one
    pages 25 rows of reference vocabulary behind an index-only scan, and a
    `total` that appeared and vanished would be a shape every client has to
    branch on for no saving worth measuring.
    """
    count = await db.scalar(
        _filtered(sa.select(sa.func.count()).select_from(GlossaryTerm), abbreviation)
    )
    return int(count or 0)


class InvalidCursor(ValueError):
    """A cursor that does not describe a position in the requested list.

    Its own class rather than the worklist's, and the duplication is the rule
    rather than an oversight: `services/worklist/queue.py`,
    `services/claims/meetings.py`, `services/claims/emails.py` and
    `services/claims/notes.py` each declare one, each router maps it to the same
    400 `/problems/invalid-cursor`, and nothing in this project shares a
    pagination module. Importing the worklist's would also point `data/` at
    `services/`, which is the one direction AD-1's dependency rule forbids.
    """


@dataclass(frozen=True)
class Cursor:
    """Where a page of the glossary ended, and what ordering decided that.

    **The codec lives in the repository, not in a service.** This endpoint has
    no service — reference data changes by migration, there is no command and no
    policy — so the two candidates were this module and the router. It is here
    because a cursor is a *position in an ordering*, and the ordering is argued,
    fixed and tie-broken thirty lines up. Putting the token beside the `ORDER BY`
    it names keeps them one change apart; putting it in the router would leave a
    thin route holding the only statement of what "after" means.

    **Compared, all three.** There is no rule document behind this list and
    therefore no version to record — the ordering is stored columns, not Python
    arithmetic over a JDM block — so what remains is the shape of the request:

    - `sort` — a cursor is a position in an ordering, and a position in a
      different ordering is a different place. Replaying an `abbreviation`
      cursor under the default sort would resume the display-order list after a
      key cut from the alphabetical one, skipping rows with a 200 and nothing on
      screen to say so. `queue.Cursor` compares `queue_filter` and `stage` for
      exactly this reason.
    - `abbreviation` — the same argument for the one facet. `None` is a value
      like any other here: it means "the whole glossary", and a whole-glossary
      cursor replayed *with* a facet is refused just as loudly.
    - `limit` — *reused* when a request omits one, `queue.Cursor`'s division, and
      compared when it supplies a different one. Page one at `limit=10` ends
      after ten terms; a follow-up that cut fifty from that key would hand back a
      window the caller never asked for.

    There is no `as_of` and no expiry. Every other cursor in this console carries
    one because its list is aged, scored or judged against a day; a glossary term
    has no clock, so a token found in a bookmark a year later still names exactly
    the row it named — and refusing it would be theatre.
    """

    last_value: str | int
    last_sort_order: int
    limit: int
    sort: GlossarySort
    abbreviation: str | None


def encode_cursor(cursor: Cursor) -> str:
    """Base64url of a compact JSON object, unpadded — `queue.encode_cursor`'s form.

    Opaque by intent rather than by encryption: the glossary is the one list in
    this console that is byte-identical for every authenticated caller, so there
    is nothing here to protect and obscurity is doing no security work. What the
    encoding buys is that clients treat it as a token to hand back rather than a
    key to increment.
    """
    payload = json.dumps(
        {
            "k": [cursor.last_value, cursor.last_sort_order],
            "l": cursor.limit,
            "s": cursor.sort.value,
            "a": cursor.abbreviation,
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


#: Postgres' `integer` range, restated here because a cursor is caller-supplied
#: input and this decoder is the only thing standing between it and the driver.
#:
#: ``sort_order`` is an `integer` column, so a forged cursor naming 2**63 reaches
#: asyncpg as an out-of-range parameter and raises `DataError` — which is not in
#: the except tuple below and escaped as a **500** rather than the 400 this
#: function exists to produce. Bounded here, beside every other bound on the
#: token, rather than by widening the except tuple to a driver exception this
#: module would otherwise never name.
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1


def _int32(value: int, what: str) -> int:
    """One of the cursor's integer members, held to what an `integer` column holds."""
    if not _INT32_MIN <= value <= _INT32_MAX:
        raise ValueError(f"the cursor's {what} is outside the range of an integer column")
    return value


def decode_cursor(raw: str, *, min_limit: int, max_limit: int) -> Cursor:
    """Parse a cursor, or refuse it. Never a silent fallback to page one.

    `queue.decode_cursor`'s body, narrowed to what this list has. Answering page
    one for an undecodable cursor would turn a client bug into an infinite "Show
    more" that re-appends the same terms for ever.

    A cursor is unsigned base64 JSON, so every field is bounded rather than
    merely parsed:

    - `sort` goes through `GlossarySort(...)`, so a forged ordering is a refusal
      here rather than a `KeyError` inside the query builder.
    - `limit` is held to the range the route declares. The cursor reuses it when
      the request omits one, so an unbounded field would be a way past the
      route's own ceiling without ever passing FastAPI's validator.
    - `k` is the resumption key, and its first member must be the type the named
      ordering sorts on: an `int` for `sort_order`, a `str` for the two
      alphabetical options. A mismatch would reach the driver as a row-value
      comparison between a text column and an integer parameter, which is a
      database error rather than the 400 this function exists to produce.
    - `ArithmeticError` sits in the except tuple beside `ValueError` for
      `queue.decode_cursor`'s reason: `json` accepts the literal `Infinity` and
      `int(float("inf"))` raises `OverflowError`, which is not a `ValueError`.
    - both integer members are held to the **int32** range, because
      `sort_order` is an `integer` column: a forged 2**63 is bounded here rather
      than reaching asyncpg as a `DataError`, which is not a `ValueError` either
      and escaped this decoder as a 500.
    """
    try:
        padded = raw + "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        key = data["k"]
        if not isinstance(key, list) or len(key) != 2:
            raise TypeError("the cursor's resumption key is not a two-element array")
        sort = GlossarySort(data["s"])
        last_value = _sort_value(key[0], sort)
        abbreviation = data["a"]
        if abbreviation is not None and not isinstance(abbreviation, str):
            raise TypeError("the cursor's abbreviation facet is not a string")
        cursor = Cursor(
            last_value=last_value,
            last_sort_order=_int32(int(key[1]), "sort position"),
            limit=int(data["l"]),
            sort=sort,
            abbreviation=abbreviation,
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
    if not min_limit <= cursor.limit <= max_limit:
        raise InvalidCursor(
            f"The pagination cursor names a page size of {cursor.limit}; "
            f"it must be between {min_limit} and {max_limit}."
        )
    return cursor


def _sort_value(raw: object, sort: GlossarySort) -> str | int:
    """A cursor's sort value, coerced through the type its ordering sorts on.

    `bool` is an `int` in Python and is refused explicitly, `drill_through.
    _as_int`'s rule: `{"k": [true, 3]}` would otherwise decode cleanly and
    compare a boolean against an integer column for ever.
    """
    if sort is GlossarySort.sort_order:
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise TypeError(f"expected an integer sort position, got {type(raw).__name__}")
        return _int32(raw, "sort value")
    if not isinstance(raw, str):
        raise TypeError(f"expected a string sort value, got {type(raw).__name__}")
    return raw


def sort_value_of(term: GlossaryTerm, sort: GlossarySort) -> str | int:
    """The value the named ordering sorted `term` by — the cursor's first member.

    Here rather than in the router for `Cursor`'s reason: the value the cursor
    records has to be the value the `ORDER BY` used, and one mapping means the
    two cannot come to disagree the way a second `if` in a route would.
    """
    if sort is GlossarySort.abbreviation:
        return term.abbreviation
    if sort is GlossarySort.term:
        return term.term
    return term.sort_order
