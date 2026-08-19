"""Story 6.1 AC 3 — vector similarity search is scoped like any other query.

The acceptance criterion names this test, and the reason it is named rather
than left to the general repository guard is that a similarity search is the
query in this codebase most likely to acquire a quiet exception. It does not
look like a `WHERE` clause anybody can eyeball: the ordering is a float from an
operator nobody reads, the index is opaque, and the plausible-sounding
optimisation — "search the whole corpus, filter the results afterwards" — is
both faster and wrong, because it returns `k` minus however many neighbours
belonged to somebody else.

So the assertions are at **both** levels the story asks for. At the repository,
because that is where the guarantee lives and where a refactor would remove it.
Through the service, because AC 3 says "under test with a scoped persona" and
`similar_claims` is what Story 6.4's quick action will actually call — a
repository-only test would leave the layer that composes and embeds the query
free to widen the context on the way down.

Three personas, chosen from the seeded nine for what they prove:

- **Kaya Johnson**, a handler scoped to four employers, with 45 claims — big
  enough that a scope leak has somewhere to leak from.
- **Sarah Williams**, a handler scoped to 3M alone, whose book is disjoint from
  Kaya's — so "Kaya never sees a 3M claim" is a statement with content.
- **David Bline**, `scope_all` — the other half of AD-7's rule that `ALL`
  widens the predicate rather than removing it. A test that only proved
  narrowing would pass against a repository that returned nothing at all.

And a fourth context with no employer assignments, which is the sentinel's
other end: empty scope must mean "sees nothing", never "no filter".
"""

from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, Claim
from data.models.enums import UserRole
from data.repositories import embeddings as embedding_repo
from data.repositories.identity import employer_ids_for
from services.rag import refresh_stale_embeddings, similar_claims
from tests import seed_fixture
from tests.conftest import requires_db
from tests.embedding_fixture import FakeEmbeddingClient, deterministic_vector

pytestmark = requires_db

KAYA = ("Kaya Johnson", "handler")
SARAH = ("Sarah Williams", "handler")
BLINE = ("David Bline", "supervisor")


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def context_for(db: AsyncSession, name: str, role: str) -> CallerContext:
    """Build a caller context the way the API dependency does (AD-7)."""
    user = (
        await db.scalars(sa.select(AppUser).where(AppUser.name == name, AppUser.role == role))
    ).one()
    return CallerContext(
        user_id=user.id,
        role=user.role,
        employer_ids=ALL_EMPLOYERS if user.scope_all else await employer_ids_for(db, user.id),
    )


@pytest.fixture
async def embedded(db: AsyncSession) -> CallerContext:
    """A fully embedded portfolio, and the unbounded context that produced it.

    The refresh runs under the seeded system actor exactly as the scheduler
    does — which also means every assertion below is made against rows written
    by a `scope_all` context, so a scope leak in a *read* cannot be masked by
    the writes having been narrow.
    """
    system = (await db.scalars(sa.select(AppUser).where(AppUser.role == UserRole.system))).one()
    ctx = CallerContext(user_id=system.id, role=system.role, employer_ids=ALL_EMPLOYERS)
    await refresh_stale_embeddings(db, ctx, client=FakeEmbeddingClient(), limit=None)
    return ctx


async def test_the_repository_never_returns_an_out_of_scope_claim(
    db: AsyncSession, embedded: CallerContext
) -> None:
    """AC 3 at the repository, with `k` larger than the caller's whole book.

    `k=200` against a 100-claim portfolio is the point: it asks for more
    neighbours than exist, so an unscoped query would return every claim in the
    database and the assertion below could not pass by accident. What comes back
    is bounded by the caller's book, not by `k`.
    """
    kaya = await context_for(db, *KAYA)
    hers = seed_fixture.expected_claim_ids(*KAYA)

    rows = await embedding_repo.select_similar_claims(
        db,
        kaya,
        embedding=deterministic_vector("a lifting injury to the shoulder"),
        k=200,
        exclude_claim_pk=None,
    )

    returned = {row.claim_id for row in rows}
    assert returned, "an embedded book should have neighbours"
    assert returned <= hers
    assert len(returned) == len(hers), "k exceeded the book, so the book is the bound"


async def test_the_service_never_returns_an_out_of_scope_claim(
    db: AsyncSession, embedded: CallerContext
) -> None:
    """AC 3 through `similar_claims`, which is what Story 6.4's tool calls.

    The service composes and embeds the query itself, so this also proves that
    the context it hands the repository is the one it was given rather than a
    widened copy.
    """
    kaya = await context_for(db, *KAYA)
    hers = seed_fixture.expected_claim_ids(*KAYA)
    target = sorted(hers)[0]

    items = await similar_claims(
        db, kaya, claim_business_id=target, k=25, client=FakeEmbeddingClient()
    )

    assert items
    assert {item.claim_id for item in items} <= hers
    assert target not in {item.claim_id for item in items}


async def test_two_handlers_books_do_not_leak_into_each_other(
    db: AsyncSession, embedded: CallerContext
) -> None:
    """Sarah's book and Kaya's are disjoint in the seed, so this has content.

    Stated as a disjointness rather than as "Kaya sees 45 claims", because a
    count can be right while the wrong 45 rows are in it.
    """
    kaya = await context_for(db, *KAYA)
    sarah = await context_for(db, *SARAH)
    hers, theirs = seed_fixture.expected_claim_ids(*KAYA), seed_fixture.expected_claim_ids(*SARAH)
    assert hers.isdisjoint(theirs), "the seed's two handler books must be disjoint"

    query = deterministic_vector("repetitive strain")
    kaya_rows = await embedding_repo.select_similar_claims(
        db, kaya, embedding=query, k=200, exclude_claim_pk=None
    )
    sarah_rows = await embedding_repo.select_similar_claims(
        db, sarah, embedding=query, k=200, exclude_claim_pk=None
    )

    assert {row.claim_id for row in kaya_rows}.isdisjoint({row.claim_id for row in sarah_rows})


async def test_a_scope_all_persona_may_cross_the_partition(
    db: AsyncSession, embedded: CallerContext
) -> None:
    """The other half of AD-7: `ALL` widens the predicate, never removes it.

    Without this, a repository that returned nothing for everybody would pass
    the narrowing tests above.
    """
    bline = await context_for(db, *BLINE)
    total = len(seed_fixture.seed()["claims"])

    rows = await embedding_repo.select_similar_claims(
        db,
        bline,
        embedding=deterministic_vector("a lifting injury to the shoulder"),
        k=200,
        exclude_claim_pk=None,
    )

    returned = {row.claim_id for row in rows}
    assert len(returned) == total
    assert not returned <= seed_fixture.expected_claim_ids(*KAYA)


async def test_an_empty_book_retrieves_nothing(db: AsyncSession, embedded: CallerContext) -> None:
    """Empty scope means "sees nothing", never "no filter" (HTTP 200, no items).

    The classic form of this bug is `None`/`{}` standing for "everything",
    which is exactly why `ALL_EMPLOYERS` is its own type.
    """
    nobody = CallerContext(user_id=1, role=UserRole.handler, employer_ids=frozenset())

    rows = await embedding_repo.select_similar_claims(
        db, nobody, embedding=deterministic_vector("anything"), k=10, exclude_claim_pk=None
    )
    assert rows == []


async def test_an_out_of_scope_target_is_the_same_answer_as_a_missing_one(
    db: AsyncSession, embedded: CallerContext
) -> None:
    """One 404, two causes — `select_claim_detail`'s single-answer rule.

    Two different answers would make `GET /claims/{id}/similar` an oracle a
    caller can walk `WC-20000`…`WC-20999` through to enumerate a portfolio they
    cannot read. The service raises the same exception for both, so the route
    has one branch and no way to write the leak back in.
    """
    from services.claims.detail import ClaimNotVisible

    kaya = await context_for(db, *KAYA)
    theirs = sorted(seed_fixture.expected_claim_ids(*SARAH))[0]
    client = FakeEmbeddingClient()

    with pytest.raises(ClaimNotVisible):
        await similar_claims(db, kaya, claim_business_id=theirs, k=5, client=client)
    with pytest.raises(ClaimNotVisible):
        await similar_claims(db, kaya, claim_business_id="WC-99999", k=5, client=client)


async def test_the_knowledge_corpus_reads_the_same_for_every_persona(
    db: AsyncSession, embedded: CallerContext
) -> None:
    """The documented carve-out, asserted rather than assumed.

    `knowledge_chunk` has no employer, no claim and no PHI, so a scoped handler
    and a `scope_all` supervisor must see identical rows. This is the test that
    would fail if somebody "helpfully" added an employer filter to the corpus
    search — and the test that would fail if the claim search's filter were ever
    removed and this one's absence were the model that was copied.
    """
    kaya = await context_for(db, *KAYA)
    bline = await context_for(db, *BLINE)
    query = deterministic_vector("Ohio waiting period")

    hers = await embedding_repo.select_similar_chunks(db, kaya, embedding=query, k=3)
    theirs = await embedding_repo.select_similar_chunks(db, bline, embedding=query, k=3)

    assert len(hers) == 3
    assert [row.source for row in hers] == [row.source for row in theirs]
    assert all(row.source.startswith("synthetic-demo:") for row in hers)


async def test_the_stale_write_is_scoped_too(db: AsyncSession, embedded: CallerContext) -> None:
    """A handler cannot mark another handler's claim stale by primary key.

    The writes are the paths nobody would think to scope, which is why they are
    scoped and why this is asserted. Silent rather than raising, for
    `select_claim_detail`'s reason.
    """
    kaya = await context_for(db, *KAYA)
    theirs = sorted(seed_fixture.expected_claim_ids(*SARAH))[0]
    claim_pk = await db.scalar(sa.select(Claim.id).where(Claim.claim_id == theirs))
    assert claim_pk is not None

    affected = await embedding_repo.upsert_claim_embedding_stale(db, kaya, claim_pk=claim_pk)
    await db.commit()

    assert affected == 0
    stale = await db.scalar(
        sa.text("SELECT stale FROM claim_embedding WHERE claim_id = :pk"), {"pk": claim_pk}
    )
    assert stale is False
