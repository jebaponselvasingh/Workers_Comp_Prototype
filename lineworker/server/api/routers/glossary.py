"""The WC domain glossary (FR-GLOS-1).

Thin by AD-1 and parameter-free like `/stats/*`, but for the opposite
reason: those endpoints take no parameters because the caller must not be
able to choose a scope, this one because there is nothing to choose. The
glossary is 25 rows of reference vocabulary, the same for every
authenticated persona of every role — which is what makes filtering it in
the browser (the SPA's live search) rendering rather than a permission
decision moved client-side.

There is no POST, PATCH or DELETE here and no service behind it: reference
data changes by migration, and the app role's only grant on the table is
SELECT.
"""

from fastapi import APIRouter

from api.deps import DbDep
from api.routers.auth import UNAUTHENTICATED_RESPONSE
from api.schemas import ApiModel
from data.repositories.glossary import list_terms

router = APIRouter(tags=["glossary"])


class GlossaryTermResponse(ApiModel):
    """One term. No `id` and no `sortOrder` on the wire.

    Order is carried by the array — the SPA renders the list as given — and
    a surrogate key nothing links to would be an invitation to link to it,
    which is exactly the `glossary_term`↔`claim` join this story is not
    building.
    """

    abbreviation: str
    term: str
    definition: str


class GlossaryList(ApiModel):
    """The `{items, nextCursor, total}` envelope (Lists convention).

    `nextCursor` is structurally always null: the glossary is one fixed
    page. The envelope is here so the generated client sees one list shape
    across the whole API, the same reasoning `PersonaList` records.
    """

    items: list[GlossaryTermResponse]
    next_cursor: str | None = None
    total: int


@router.get(
    "/glossary",
    response_model=GlossaryList,
    summary="Every WC glossary term, in display order",
    responses=UNAUTHENTICATED_RESPONSE,
)
async def glossary(db: DbDep) -> GlossaryList:
    """Deliberately without `Cache-Control: no-store`.

    `/me` and `/stats/*` set it because their payloads are specific to one
    persona's identity or scope, and a cache upstream serving one
    supervisor's caseload to another would be a scope leak. Nothing about
    this response is caller-specific: every authenticated session gets
    byte-identical bytes, there is no PHI in them, and the worst a shared
    cache could do is serve the industry's definition of "FNOL" to someone
    who had not logged in. Marking it `no-store` anyway would say the
    payload was sensitive, which is a claim the next reader would have to
    disprove.
    """
    items = [GlossaryTermResponse.model_validate(term) for term in await list_terms(db)]
    return GlossaryList(items=items, total=len(items))
