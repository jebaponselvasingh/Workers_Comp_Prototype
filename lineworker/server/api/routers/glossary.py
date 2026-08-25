"""The WC domain glossary (FR-GLOS-1).

Thin by AD-1. It used to be parameter-free like `/stats/*`, but for the
opposite reason: those endpoints take no parameters because the caller must
not be able to choose a scope, this one because — it was claimed — there was
nothing to choose. The glossary is 25 rows of reference vocabulary, the same
for every authenticated persona of every role, which is what makes filtering
it in the browser (the SPA's live search) rendering rather than a permission
decision moved client-side.

**Story 9.8 made the envelope honest.** The route published
`{items, nextCursor, total}` with `nextCursor` structurally null, no `LIMIT`
on the read, and `total = len(items)` computed from the very list it was
returned beside — a shape that asserted a pagination that did not exist and
in which a truncation could never have been detected. It now applies a real
`LIMIT`, mints a real cursor, sources `total` from a `COUNT(*)`, and declares
the `filter[…]` and `sort` parameters the Lists convention specifies. The
1.3 register entry deferred exactly this machinery to "Epic 2's first
genuinely pageable list endpoint"; five epics of them shipped without
anybody coming back, which is why it lands here instead.

Nothing about the *answer* changed for the SPA: `DEFAULT_PAGE_LIMIT` is four
times the seeded vocabulary, so the panel's single unparameterised read still
returns every term with a null `nextCursor` — see the constant.

There is no POST, PATCH or DELETE here and no service behind it: reference
data changes by migration, and the app role's only grant on the table is
SELECT.
"""

from typing import Annotated, NoReturn

from fastapi import APIRouter, Query, status

from api.deps import DbDep
from api.errors import PROBLEM_CONTENT_TYPE, ProblemDocument, ProblemException
from api.routers.auth import UNAUTHENTICATED_RESPONSE
from api.schemas import ApiModel
from data.repositories.glossary import (
    Cursor,
    GlossarySort,
    InvalidCursor,
    count_terms,
    decode_cursor,
    encode_cursor,
    list_terms,
    sort_value_of,
)

router = APIRouter(tags=["glossary"])

#: The page-size range, declared once and enforced twice — `queue.py`'s rule:
#: FastAPI refuses a `limit` outside it with a 422 before the read, and
#: `decode_cursor` refuses one smuggled inside a cursor. A cursor is
#: caller-supplied input like any other query parameter.
MIN_PAGE_LIMIT = 1
MAX_PAGE_LIMIT = 200

#: The default page size — deliberately larger than the seeded vocabulary.
#:
#: 25 terms ship in the seed migration and the SPA's glossary panel makes one
#: unparameterised read and filters the result in the browser, which is
#: rendering rather than a scoping decision (see `GlossaryPanel.tsx`). A default
#: below the row count would have turned this story's honest `LIMIT` into a
#: silently truncated panel — the one thing worse than the fake pagination it
#: replaces. A hundred leaves three-quarters of the page unused today and is
#: what makes "the panel still sees every term" a property rather than a hope;
#: `test_the_seeded_glossary_fits_inside_the_default_page` pins it, so a
#: migration that grows the vocabulary past this number fails CI rather than
#: quietly shortening the panel.
DEFAULT_PAGE_LIMIT = 100

BAD_CURSOR_RESPONSE: dict[int | str, dict[str, object]] = {
    400: {
        "description": (
            "The pagination cursor is unreadable, or belongs to a different "
            "sort or filter (RFC 9457 problem document)."
        ),
        "content": {PROBLEM_CONTENT_TYPE: {"schema": ProblemDocument.model_json_schema()}},
    }
}


class GlossaryTermResponse(ApiModel):
    """One term. No `id` and no `sortOrder` on the wire.

    Order is carried by the array — the SPA renders the list as given — and
    a surrogate key nothing links to would be an invitation to link to it,
    which is exactly the `glossary_term`↔`claim` join this story is not
    building.

    `sortOrder` stays off the wire for the same reason after Story 9.8, even
    though it is now half of every cursor's resumption key. The cursor is
    opaque and the client hands it back unchanged; publishing the column would
    invite a client to compute the next position itself, which is the "token,
    not a key to increment" contract every cursor in this console relies on.
    """

    abbreviation: str
    term: str
    definition: str


class GlossaryList(ApiModel):
    """The `{items, nextCursor, total}` envelope (Lists convention), for real.

    **`nextCursor` is non-null exactly while terms remain beyond `items`** —
    never "null because this page came back short". Before Story 9.8 it was
    structurally always null and the docstring here said so, which made the
    envelope a shape borrowed for consistency rather than a contract.

    **`total` is the size of the whole list being paged**, from a `COUNT(*)`
    over the same filter, never `len(items)`. The old value agreed with itself
    whatever happened to the table, so the moment a `LIMIT` appeared it would
    have changed meaning from "terms that exist" to "terms on this page" with
    no test able to see it — the hazard the 1.6 register entry recorded
    verbatim. Present on every page rather than the first only (unlike
    `EmailLogListResponse`): this is a 25-row reference table, and a field that
    appeared and vanished would be a shape every client has to branch on for no
    saving worth measuring.
    """

    items: list[GlossaryTermResponse]
    next_cursor: str | None = None
    total: int


@router.get(
    "/glossary",
    response_model=GlossaryList,
    summary="WC glossary terms, in display order, paged",
    responses={**UNAUTHENTICATED_RESPONSE, **BAD_CURSOR_RESPONSE},
)
async def glossary(
    db: DbDep,
    cursor: Annotated[
        str | None,
        Query(description="An opaque `nextCursor` from a previous response."),
    ] = None,
    limit: Annotated[
        int | None,
        Query(
            ge=MIN_PAGE_LIMIT,
            le=MAX_PAGE_LIMIT,
            description=f"Page size; defaults to {DEFAULT_PAGE_LIMIT}.",
        ),
    ] = None,
    abbreviation: Annotated[
        str | None,
        Query(
            alias="filter[abbreviation]",
            description=(
                "One term's abbreviation, matched as the exact stored string — "
                "no trimming, case-folding or prefix match. The panel's live "
                "search is a client-side rendering of a list it already holds "
                "and does not use this."
            ),
        ),
    ] = None,
    sort: Annotated[
        GlossarySort,
        Query(description="Which ordering to page. Defaults to the display order."),
    ] = GlossarySort.sort_order,
) -> GlossaryList:
    """Deliberately without `Cache-Control: no-store`.

    `/me` and `/stats/*` set it because their payloads are specific to one
    persona's identity or scope, and a cache upstream serving one
    supervisor's caseload to another would be a scope leak. Nothing about
    this response is caller-specific: every authenticated session asking the
    same question gets byte-identical bytes, there is no PHI in them, and the
    worst a shared cache could do is serve the industry's definition of "FNOL"
    to someone who had not logged in. Marking it `no-store` anyway would say
    the payload was sensitive, which is a claim the next reader would have to
    disprove. That stays true with the three parameters Story 9.8 added: they
    are part of the request, so they are part of the cache key, and none of
    them names a persona.

    **The cursor's `sort` and `filter[abbreviation]` are compared, never
    reused.** A caller who changes either is asking a different question, and
    a position in one ordering is not a position in another — see `Cursor`.
    `limit` is *reused* when the request omits one and compared when it does
    not, `queue.py`'s division on every list in this console.
    """
    decoded = None
    if cursor is not None:
        try:
            decoded = decode_cursor(cursor, min_limit=MIN_PAGE_LIMIT, max_limit=MAX_PAGE_LIMIT)
        except InvalidCursor as exc:
            _refuse(str(exc), exc)
    if decoded is not None:
        if decoded.sort is not sort:
            _refuse(
                f"That page belongs to the {decoded.sort.value!r} ordering, not {sort.value!r}."
            )
        if decoded.abbreviation != abbreviation:
            _refuse(
                "That page was cut from a differently filtered list; "
                "reload the glossary from the first page."
            )
        if limit is not None and limit != decoded.limit:
            _refuse(
                f"That page was cut at {decoded.limit} rows, and this request asks "
                f"for {limit}; reload the glossary from the first page."
            )
    page_size = decoded.limit if decoded is not None else (limit or DEFAULT_PAGE_LIMIT)

    rows = await list_terms(
        db,
        # One more than the page, so "is there another page?" is answered by
        # what came back rather than by comparing against `total` — which would
        # be wrong the moment a migration changed the count between the two
        # statements. `list_email_logs`' idiom.
        limit=page_size + 1,
        after=None if decoded is None else (decoded.last_value, decoded.last_sort_order),
        abbreviation=abbreviation,
        sort=sort,
    )
    total = await count_terms(db, abbreviation=abbreviation)

    has_more = len(rows) > page_size
    window = rows[:page_size]
    return GlossaryList(
        items=[GlossaryTermResponse.model_validate(term) for term in window],
        next_cursor=(
            encode_cursor(
                Cursor(
                    last_value=sort_value_of(window[-1], sort),
                    last_sort_order=window[-1].sort_order,
                    limit=page_size,
                    sort=sort,
                    abbreviation=abbreviation,
                )
            )
            if has_more and window
            else None
        ),
        total=total,
    )


def _refuse(detail: str, cause: Exception | None = None) -> NoReturn:
    """400 `/problems/invalid-cursor`, this file's one refusal.

    `NoReturn`, not `None`: this function always raises, and typed as `None` its
    call sites read as though an unreadable cursor could fall through to the
    page-one path below them. The type is the statement that it cannot.

    400 rather than 422, `claims.queue`'s ruling: the cursor is syntactically a
    string and passed validation. What failed is that it does not describe a
    position in *this* list.

    No `Cache-Control: no-store` here, unlike the worklist's identical refusal:
    that one names a caller's own worklist length and is therefore as
    persona-specific as the 200 beside it. This one names an ordering and a
    facet the caller sent, on a list every session sees identically, so there is
    nothing an intermediary could leak by holding it.
    """
    raise ProblemException(
        status_code=status.HTTP_400_BAD_REQUEST,
        title="Bad Request",
        detail=detail,
        type_="/problems/invalid-cursor",
    ) from cause
