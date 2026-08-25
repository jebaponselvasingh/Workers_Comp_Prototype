"""Story 1.6 — the seeded glossary and `GET /glossary` (AC 1, 2).

DB-backed, because everything this story claims is a claim about a database:
that 25 rows landed with the prototype's text and order, that the app role
can read them and cannot write them, and that the endpoint hands the same
list to every authenticated persona and nothing at all to an anonymous one.

Expectations come from `seed_fixture.glossary_terms()`, i.e. from
`data/seed/glossary_terms.json` — the same file migration 0007 loads. No
count and no definition is typed into this file.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import ProgrammingError

from api import create_app
from api.routers.glossary import DEFAULT_PAGE_LIMIT
from config import Settings
from tests import seed_fixture
from tests.conftest import APP_PASSWORD, requires_db

pytestmark = requires_db

GLOSSARY = "/glossary"

# Every seeded persona shape: both scoped and full-portfolio, and all three
# roles. "One click away everywhere" is a claim about the whole cast.
PERSONAS = [
    ("Kaya Johnson", "handler"),
    ("Sarah Williams", "handler"),
    ("Jennifer Park", "supervisor"),
    ("David Bline", "supervisor"),
    ("David Bline", "analyst"),
]


def _sync(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="module")
def owner_engine(seeded_db_url: str) -> sa.Engine:
    """Schema-owner connection to the freshly migrated database."""
    return sa.create_engine(_sync(seeded_db_url))


@pytest.fixture(scope="module")
def app_engine(seeded_db_url: str) -> sa.Engine:
    """Connection as the runtime app role (created by migration 0002)."""
    url = sa.make_url(_sync(seeded_db_url)).set(username="lineworker_app", password=APP_PASSWORD)
    return sa.create_engine(url)


@asynccontextmanager
async def make_client(db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(database_url=db_url, env="e2e"))  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def login_as(client: httpx.AsyncClient, name: str, role: str) -> None:
    personas = (await client.get("/personas")).json()["items"]
    match = [p for p in personas if p["name"] == name and p["role"] == role]
    assert len(match) == 1, f"expected exactly one {name}/{role} persona"
    assert (await client.post("/auth/login", json={"personaId": match[0]["id"]})).status_code == 200


async def glossary_for(db_url: str, name: str, role: str) -> dict[str, Any]:
    async with make_client(db_url) as client:
        await login_as(client, name, role)
        resp = await client.get(GLOSSARY)
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body


# --- AC 1: the rows are the file's, verbatim and in order ----------------


def test_the_table_holds_exactly_the_seed_file(owner_engine: sa.Engine) -> None:
    with owner_engine.connect() as conn:
        rows = (
            conn.execute(
                sa.text(
                    "SELECT abbreviation, term, definition, sort_order "
                    "FROM glossary_term ORDER BY sort_order"
                )
            )
            .mappings()
            .all()
        )

    expected = seed_fixture.glossary_terms()
    assert len(rows) == len(expected)
    # Compared field by field rather than as a count: "25 rows arrived" and
    # "the prototype's 25 rows arrived" are different assertions, and only
    # the second one notices a truncated definition or a swapped pair.
    assert [dict(row) for row in rows] == expected


def test_sort_order_in_the_table_matches_the_file(owner_engine: sa.Engine) -> None:
    """Every `sort_order` in the file reached the table, none added, none lost.

    Note what this does *not* prove, because the docstring used to claim it:
    it cannot detect a term the extractor dropped. `sort_order` comes from
    `enumerate(entries)`, so a regex miss produces a file that is still
    dense from zero — this test would compare `range(24)` to `range(24)` and
    pass. The extractor's own boundary check is what catches a dropped
    entry, and `test_the_seed_file_is_the_prototypes_gloss_verbatim` is what
    catches a file that stopped matching the prototype. What this test
    covers is the migration: a partial insert, a re-run that duplicated
    rows, or a hand-edited file with a gap in it.
    """
    with owner_engine.connect() as conn:
        orders = conn.execute(sa.text("SELECT sort_order FROM glossary_term ORDER BY 1")).scalars()
        assert list(orders) == [term["sort_order"] for term in seed_fixture.glossary_terms()]


def test_the_two_prototype_quirks_survived_the_pipeline(owner_engine: sa.Engine) -> None:
    """Multi-word abbreviations and the term that repeats itself.

    Normalizing either would be an edit to the contract, and both are the
    kind of thing a well-meaning cleanup removes silently.
    """
    with owner_engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT abbreviation, term FROM glossary_term ORDER BY sort_order")
        ).all()

    # Asserted against the row list, not a dict keyed on abbreviation. The
    # dict was safe — `uq_glossary_term_abbreviation` makes collapsing rows
    # impossible — but "these pairs are among the rows" is what the test
    # means, and a lookup that *could not* collide either way says it more
    # plainly than one that relies on a constraint two files away.
    pairs = [(row[0], row[1]) for row in rows]

    assert ("OSHA 300", "OSHA Recordkeeping Log") in pairs
    assert ("Arc Flash", "Electrical Arc Flash") in pairs
    assert ("Apportionment", "Apportionment") in pairs


# --- AC 1: the AD-4 grant, asserted rather than assumed ------------------


def test_app_role_can_read_the_glossary(app_engine: sa.Engine) -> None:
    with app_engine.connect() as conn:
        count = conn.execute(sa.text("SELECT count(*) FROM glossary_term")).scalar_one()
    assert count == len(seed_fixture.glossary_terms())


def test_app_role_cannot_write_the_glossary(app_engine: sa.Engine) -> None:
    """Reference data is read-only *to the application*, enforced by the DB.

    Modelled on `test_schema_seed.py::test_app_role_audit_grants`: a grant
    that was never made is invisible until something tries to use it, so the
    absence is asserted with three statements that must each be refused.
    """
    statements = [
        "INSERT INTO glossary_term (abbreviation, term, definition, sort_order) "
        "VALUES ('XX', 'Made Up', 'Not from the prototype.', 9999)",
        "UPDATE glossary_term SET definition = 'x' WHERE abbreviation = 'FNOL'",
        "DELETE FROM glossary_term WHERE abbreviation = 'FNOL'",
    ]
    for statement in statements:
        with (
            app_engine.connect() as conn,
            pytest.raises(ProgrammingError, match="permission denied"),
        ):
            conn.execute(sa.text(statement))


# --- AC 2: the endpoint --------------------------------------------------


@pytest.mark.parametrize(("name", "role"), PERSONAS)
async def test_every_persona_gets_every_term_in_order(
    seeded_db_url: str, name: str, role: str
) -> None:
    body = await glossary_for(seeded_db_url, name, role)
    expected = seed_fixture.glossary_terms()

    assert body["items"] == [
        {"abbreviation": t["abbreviation"], "term": t["term"], "definition": t["definition"]}
        for t in expected
    ]
    assert body["total"] == len(expected)
    assert body["nextCursor"] is None


async def test_the_answer_is_identical_for_every_role(seeded_db_url: str) -> None:
    """The clause under test is "everywhere", so sameness is the assertion.

    Every other list endpoint in this app answers differently per persona by
    design (AD-7). This one must not — a glossary that varied by role would
    mean somebody had put a scope filter where there is nothing to scope.
    """
    answers = [await glossary_for(seeded_db_url, name, role) for name, role in PERSONAS]
    for answer in answers[1:]:
        assert answer == answers[0]


async def test_the_endpoint_takes_exactly_the_lists_conventions_parameters(
    seeded_db_url: str,
) -> None:
    """**Amended by Story 9.8**, and the rename is the contract change.

    Its predecessor was `test_the_endpoint_takes_no_parameters`, and it passed
    because the endpoint published a `{items, nextCursor, total}` envelope it
    could not honour: no `LIMIT`, a structurally-null cursor, and `total` taken
    from the very list it was returned beside. The Lists convention specifies
    `filter[…]`/`sort` and cursor pagination for *all* list endpoints, and the
    1.3 register entry deferred building that machinery to "Epic 2's first
    genuinely pageable list endpoint" — five epics ago.

    Renamed rather than deleted, and the new name is an **allowlist** rather
    than an assertion of emptiness, exactly as `/dashboard/priority-claims` did
    when it grew its one cursor: a test called "takes no parameters" that passes
    on a route with four is a sentence a reader would have to disbelieve.

    The property it was written to defend survives intact and is asserted below:
    **no parameter here can name a scope.** The four are a page size, an opaque
    cursor, one exact-match facet over a column with no owner, and an ordering.
    None of them selects whose glossary this is, because there is only one.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, "Kaya Johnson", "handler")
        schema = (await client.get("/openapi.json")).json()
        operation = schema["paths"]["/glossary"]["get"]

    assert {p["name"] for p in operation.get("parameters", [])} == {
        "cursor",
        "limit",
        "filter[abbreviation]",
        "sort",
    }
    assert all(p["in"] == "query" for p in operation.get("parameters", []))
    assert "requestBody" not in operation


async def test_camel_case_envelope_is_published(seeded_db_url: str) -> None:
    """`nextCursor`, not `next_cursor` — the naming convention on the wire.

    **Amended by Story 9.8** only in what the envelope now *means*: the three
    members are the same three and the camelCase claim is unchanged, but
    `nextCursor` is a real token and `total` is a `COUNT(*)`. The member set is
    asserted exactly, so a future story that drops `total` (which the convention
    permits — `total?`) has to say so here.
    """
    async with make_client(seeded_db_url) as client:
        schema = (await client.get("/openapi.json")).json()

    envelope = schema["components"]["schemas"]["GlossaryList"]["properties"]
    assert set(envelope) == {"items", "nextCursor", "total"}
    term = schema["components"]["schemas"]["GlossaryTermResponse"]["properties"]
    assert set(term) == {"abbreviation", "term", "definition"}


# --- AC 4: the pagination is real ----------------------------------------


async def test_the_seeded_glossary_fits_inside_the_default_page(seeded_db_url: str) -> None:
    """The guard that keeps the SPA's single unparameterised read complete.

    `GlossaryPanel` makes one request and filters the result in the browser,
    which is rendering rather than a scoping decision. `DEFAULT_PAGE_LIMIT` is
    deliberately four times the seeded vocabulary so that read still returns
    everything — and this is what makes that a property rather than a hope: a
    migration that grows the glossary past the default fails here rather than
    quietly shortening the panel.
    """
    body = await glossary_for(seeded_db_url, "Kaya Johnson", "handler")

    assert len(seed_fixture.glossary_terms()) < DEFAULT_PAGE_LIMIT
    assert body["nextCursor"] is None
    assert len(body["items"]) == body["total"]


async def test_a_limit_cuts_the_page_and_the_total_still_counts_the_table(
    seeded_db_url: str,
) -> None:
    """The distinction `total = len(items)` could not express.

    This is the whole of the 1.6 register entry: with no `LIMIT`, `total` agreed
    with itself whatever happened to the table, so a truncation could never have
    been detected from the payload. Cut the page and the two numbers separate.
    """
    expected = seed_fixture.glossary_terms()
    async with make_client(seeded_db_url) as client:
        await login_as(client, "Kaya Johnson", "handler")
        body = (await client.get(GLOSSARY, params={"limit": 5})).json()

    assert len(body["items"]) == 5
    assert body["total"] == len(expected)
    assert body["total"] > len(body["items"])
    assert body["nextCursor"] is not None


async def test_a_cursor_walk_visits_every_term_exactly_once_in_order(
    seeded_db_url: str,
) -> None:
    """The I/O matrix's second glossary row, walked to exhaustion.

    Every term visited once, in `sort_order`, and `nextCursor` null exactly
    once — on the last page. A page size that does not divide the vocabulary,
    deliberately: an exact divisor is the case where an off-by-one in the
    "is there more?" test produces a phantom empty final page and nothing
    notices.
    """
    expected = [t["abbreviation"] for t in seed_fixture.glossary_terms()]
    walked: list[str] = []
    nulls = 0
    async with make_client(seeded_db_url) as client:
        await login_as(client, "Kaya Johnson", "handler")
        cursor: str | None = None
        for _ in range(len(expected) + 2):
            params: dict[str, Any] = {"limit": 7}
            if cursor is not None:
                params["cursor"] = cursor
            body = (await client.get(GLOSSARY, params=params)).json()
            walked.extend(item["abbreviation"] for item in body["items"])
            cursor = body["nextCursor"]
            if cursor is None:
                nulls += 1
                break

    assert walked == expected
    assert len(walked) == len(set(walked))
    assert nulls == 1


async def test_an_alphabetical_sort_reorders_the_page_and_pages_correctly(
    seeded_db_url: str,
) -> None:
    """`sort` is a real ordering, and its cursor walks *that* list.

    Two claims in one test because they are one property: a `sort` parameter
    that changed the first page but resumed on the default ordering would page
    two different lists and skip rows in both.

    The expectation is the **whole list under the same sort**, read in one page,
    rather than a Python `sorted()`. Not laziness: `sorted()` orders by code
    point and Postgres orders by the database's collation, and this vocabulary
    contains multi-word abbreviations ("OSHA 300", "Arc Flash") where the two
    disagree about spaces. Restating the collation here would pin the test to
    one server's locale and would be asserting something this story does not
    claim; what it does claim is that *the walk equals the list*.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, "Kaya Johnson", "handler")
        whole = (await client.get(GLOSSARY, params={"sort": "abbreviation"})).json()
        assert whole["nextCursor"] is None, "the expectation must be one page"
        expected = [item["abbreviation"] for item in whole["items"]]

        walked: list[str] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 6, "sort": "abbreviation"}
            if cursor is not None:
                params["cursor"] = cursor
            body = (await client.get(GLOSSARY, params=params)).json()
            walked.extend(item["abbreviation"] for item in body["items"])
            cursor = body["nextCursor"]
            if cursor is None:
                break

        default_order = (await client.get(GLOSSARY)).json()

    assert walked == expected
    assert len(walked) == len(set(walked))
    # …and `sort` actually sorted: the alphabetical list is not the display one.
    assert expected != [item["abbreviation"] for item in default_order["items"]]


async def test_a_cursor_replayed_under_a_different_sort_is_refused(
    seeded_db_url: str,
) -> None:
    """A position in one ordering is not a position in another.

    Served rather than refused, this would resume the display-order list after a
    key cut from the alphabetical one — skipping terms with a 200 and nothing on
    screen to say so. `queue.py` compares its filter and stage for exactly this
    reason.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, "Kaya Johnson", "handler")
        cursor = (await client.get(GLOSSARY, params={"limit": 5, "sort": "abbreviation"})).json()[
            "nextCursor"
        ]
        resp = await client.get(GLOSSARY, params={"limit": 5, "cursor": cursor})

    assert resp.status_code == 400
    assert resp.json()["type"] == "/problems/invalid-cursor"


async def test_a_facet_narrows_the_page_and_the_count_together(seeded_db_url: str) -> None:
    """`total` describes the list being paged, never the table behind it.

    Counted through the same predicate the page applies — one function, two
    callers — because a count that applied the facet differently would publish a
    number about a different question.
    """
    first = seed_fixture.glossary_terms()[0]["abbreviation"]
    async with make_client(seeded_db_url) as client:
        await login_as(client, "Kaya Johnson", "handler")
        body = (await client.get(GLOSSARY, params={"filter[abbreviation]": first})).json()

    assert [item["abbreviation"] for item in body["items"]] == [first]
    assert body["total"] == 1
    assert body["nextCursor"] is None


@pytest.mark.parametrize(
    "cursor",
    [
        "not-base64-at-all!!",
        "e30",
        # A forged page size past the route's ceiling — the cursor reuses it
        # when the request omits one, so an unbounded field here would be a way
        # round the route's own validator.
        "eyJrIjpbMSwxXSwibCI6MTAwMDAwLCJzIjoic29ydF9vcmRlciIsImEiOm51bGx9",
        # A sort nobody declared.
        "eyJrIjpbMSwxXSwibCI6NSwicyI6ImJhbmFuYSIsImEiOm51bGx9",
        # A string sort value under the integer ordering.
        "eyJrIjpbIngiLDFdLCJsIjo1LCJzIjoic29ydF9vcmRlciIsImEiOm51bGx9",
        # An integer past what an `integer` column holds, in both members of the
        # key: `{"k":[2**63, 2**63],"l":5,"s":"sort_order","a":null}`. Right
        # type, right shape, decodes cleanly — and then reaches asyncpg as a
        # row-value comparison parameter out of range for `sort_order`, which
        # raises `DataError`. `DataError` is not a `ValueError` and is not in
        # this decoder's except tuple, so before the int32 bound it escaped as a
        # **500** rather than the 400 every other malformed cursor gets.
        "eyJrIjpbOTIyMzM3MjAzNjg1NDc3NTgwOCw5MjIzMzcyMDM2ODU0Nzc1ODA4XSwibCI6NSwicyI6InNvcnRfb3JkZXIiLCJhIjpudWxsfQ",
    ],
)
async def test_a_forged_cursor_is_a_400_rather_than_a_500_or_a_page_one(
    seeded_db_url: str, cursor: str
) -> None:
    """Never a silent page one — that turns a client bug into an infinite walk.

    Driven through the app rather than the decoder, because what is being
    asserted is *which status code the caller sees*: a `TypeError` escaping into
    the row-value comparison would be a 500, and a silent fallback would be a
    200 that re-appends the same terms for ever.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, "Kaya Johnson", "handler")
        resp = await client.get(GLOSSARY, params={"cursor": cursor})

    assert resp.status_code == 400, resp.text
    assert resp.json()["type"] == "/problems/invalid-cursor"


async def test_unauthenticated_is_401_problem_json(seeded_db_url: str) -> None:
    """Inherited from Story 1.3's app-level dependency — asserted, not rebuilt.

    `/glossary` is deliberately *not* in `PUBLIC_PATHS`: making it public is
    a security decision, and the fact that its contents are harmless is not
    the same as the endpoint being safe to expose unauthenticated.
    """
    async with make_client(seeded_db_url) as client:
        resp = await client.get(GLOSSARY)

    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["status"] == 401


async def test_the_payload_is_declared_shared_cacheable_not_persona_specific(
    seeded_db_url: str,
) -> None:
    """The claim under test is about the *data*, not about a header.

    `/me` and `/stats/*` set `Cache-Control: no-store` because their bodies
    differ per persona and a shared cache could hand one supervisor's
    caseload to another. This body is byte-identical for every caller and
    contains no PHI (`test_the_answer_is_identical_for_every_role` is the
    same claim from the other side), so `no-store` would assert a
    sensitivity the data does not have. The absent header is how that
    judgement is recorded, and it is pinned because "we chose not to" and
    "we forgot" look identical in a diff a year later.

    What this test is *not*: a rule that `/glossary` must never send
    `Cache-Control`. A future story that adds any caller-specific field to
    this payload — a "recently viewed" flag, a per-role note, anything — has
    changed the premise, and must change this test on purpose (to assert
    `no-store`) rather than read its failure as a regression to be papered
    over.
    """
    async with make_client(seeded_db_url) as client:
        await login_as(client, "Kaya Johnson", "handler")
        resp = await client.get(GLOSSARY)

    assert "cache-control" not in resp.headers
