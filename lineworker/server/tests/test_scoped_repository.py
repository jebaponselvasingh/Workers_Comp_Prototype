"""Story 1.4 AC 1/2/4 — the AD-7 repository scope layer.

This is the first repository that reads claim data, so it is also the
template every later repository copies. The tests below therefore assert
the *shape* of the contract (a caller context is required by signature, the
employer filter is unconditional) as hard as they assert the results,
because the shape is what stops Story 6.x from quietly adding an
unscoped query.
"""

import inspect
import re
import typing
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from data import repositories
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, Claim
from data.models.enums import Stage, UserRole
from data.repositories import claims as claim_repo
from data.repositories.identity import employer_ids_for
from tests import seed_fixture
from tests.conftest import requires_db

pytestmark = requires_db


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


# --- the contract itself ------------------------------------------------


def test_every_repository_read_requires_a_caller_context() -> None:
    """ "Impossible by signature" — no default, no optional, no keyword escape.

    Reflective rather than a list of hand-written assertions per function:
    a repository method added by a later story is covered the moment it is
    written, which is the only version of this test that keeps working.
    """
    public = [
        (name, obj)
        for name, obj in vars(claim_repo).items()
        if inspect.isfunction(obj)
        and not name.startswith("_")
        and obj.__module__ == claim_repo.__name__
    ]
    assert public, "no public repository functions found — did the module move?"

    for name, func in public:
        hints = typing.get_type_hints(func)
        params = list(inspect.signature(func).parameters.values())
        ctx_params = [p for p in params if hints.get(p.name) is CallerContext]
        assert len(ctx_params) == 1, f"{name}() must take exactly one CallerContext"
        ctx = ctx_params[0]
        assert ctx.default is inspect.Parameter.empty, (
            f"{name}() gives its caller context a default — that makes an unscoped call a typo away"
        )


def test_the_scope_predicate_is_a_tautology_for_all_never_a_skipped_filter() -> None:
    """AD-7: `ALL` widens the predicate; it never removes it.

    Asserted on the compiled SQL because the distinction is invisible in the
    result set — an unfiltered query and a `WHERE true` query return the
    same rows, and only one of them survives someone adding an OR later.
    """
    scoped = claim_repo.employer_scope(CallerContext(1, UserRole.handler, frozenset({2, 3})))
    unscoped = claim_repo.employer_scope(CallerContext(1, UserRole.supervisor, ALL_EMPLOYERS))

    assert "employer_id IN" in str(scoped.compile(compile_kwargs={"literal_binds": True}))
    assert str(unscoped.compile(compile_kwargs={"literal_binds": True})).lower() == "true"


# A read of the *caller's* role, in the three names a caller context is
# bound to in this codebase, plus any mention of the enum itself.
#
# Narrowed from a bare `".role" not in source` in Story 2.2. That version
# matched `Employee.role`, which is the injured worker's **job title** — a
# different column, a different meaning, and one the case-file header has to
# select. Left as it was, the next person would have reached for an
# allowlist or renamed a database column to satisfy a test, which is the
# guard training the code rather than the other way round. It still catches
# every form the failure it exists for would actually take: a repository
# reading `ctx.role` to widen its filter.
# Any `.role` read, minus the two forms that are demonstrably the injured
# worker's job title. Blunt again by default — the previous version bound
# the check to three identifier names (`ctx`/`context`/`caller`), which a
# single `scope = ctx` rename walked straight past (code review,
# 2026-08-12). Excluding the innocent forms keeps the coverage the original
# `".role" not in body` had while still letting the case header select the
# column it needs.
CALLER_ROLE_READ = re.compile(r"(?<!Employee)(?<!employee)\.\s*role\b|\bUserRole\b")


def test_no_repository_branches_on_role() -> None:
    """Scope gates visibility; role gates capability — never the reverse (AD-7).

    A role check inside a repository is how "supervisors see everything"
    gets re-implemented next to the scope filter and then diverges from it.
    """
    source = inspect.getsource(claim_repo)
    body = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))

    assert not CALLER_ROLE_READ.search(body), (
        "repository reads the caller's role — scope must not branch on role"
    )


@pytest.mark.parametrize(
    "smell",
    [
        "if ctx.role is UserRole.supervisor:",
        "if context.role == 'supervisor': return sa.true()",
        "widened = caller.role in ADMIN_ROLES",
        "from data.models.enums import UserRole",
        # The rename that walked past the name-bound version of this guard.
        "scope = ctx\nif scope.role == 'supervisor': return sa.true()",
        "if self._ctx.role is not None: ...",
        "principal.role == 'analyst'",
    ],
)
def test_the_role_guard_would_notice_a_role_branch(smell: str) -> None:
    """A guard that only ever reads a clean file cannot tell "nothing is
    wrong" from "nothing is checked"."""
    assert CALLER_ROLE_READ.search(smell)


@pytest.mark.parametrize(
    "innocent",
    [
        'Employee.role.label("worker_role")',  # the injured worker's job title
        "employee.role",
        "handler.name.label('handler_name')",
        "rows = await db.scalars(sa.select(Claim))",
    ],
)
def test_the_role_guard_leaves_ordinary_columns_alone(innocent: str) -> None:
    """The other half: `employee.role` is a job title, not a capability.

    The case-file header selects it, and a guard that could not tell the two
    apart would have made "do not branch on the caller's role" mean "do not
    say the word role".
    """
    assert not CALLER_ROLE_READ.search(innocent)


# --- scoped reads -------------------------------------------------------


async def test_a_scoped_context_returns_only_in_scope_claims(db: AsyncSession) -> None:
    ctx = await context_for(db, "Jennifer Park", "supervisor")
    rows = await claim_repo.list_claims(db, ctx)

    assert {row.claim_id for row in rows} == seed_fixture.expected_claim_ids(
        "Jennifer Park", "supervisor"
    )
    assert rows, "the scoped supervisor should see a non-empty book"


async def test_all_employers_returns_everything(db: AsyncSession) -> None:
    ctx = await context_for(db, "David Bline", "supervisor")
    rows = await claim_repo.list_claims(db, ctx)

    total = (await db.scalar(sa.select(sa.func.count()).select_from(Claim))) or 0
    assert len(rows) == total == len(seed_fixture.seed()["claims"])


async def test_a_handler_sees_strictly_less_than_the_full_portfolio(db: AsyncSession) -> None:
    handler = await claim_repo.list_claims(db, await context_for(db, "Sarah Williams", "handler"))
    everything = await claim_repo.list_claims(
        db, await context_for(db, "David Bline", "supervisor")
    )

    handler_ids = {row.claim_id for row in handler}
    assert handler_ids == seed_fixture.expected_claim_ids("Sarah Williams", "handler")
    assert handler_ids < {row.claim_id for row in everything}


async def test_a_context_with_an_empty_book_sees_nothing(db: AsyncSession) -> None:
    """The other end of the sentinel: empty scope must not mean "no filter".

    `None`/`{}` standing for "everything" is the classic form of this bug,
    which is why `ALL_EMPLOYERS` is its own type.
    """
    ctx = CallerContext(user_id=1, role=UserRole.handler, employer_ids=frozenset())
    assert await claim_repo.list_claims(db, ctx) == []
    assert await claim_repo.count_claims_matching(db, ctx, {"n": sa.true()}) == {"n": 0}


async def test_counts_are_scoped_the_same_way_as_reads(db: AsyncSession) -> None:
    ctx = await context_for(db, "Jennifer Park", "supervisor")
    counts = await claim_repo.count_claims_matching(
        db,
        ctx,
        {"all": sa.true(), "treatment": Claim.stage == Stage.treatment},
    )
    expected = seed_fixture.expected_topbar_stats("Jennifer Park", "supervisor")

    assert counts["all"] == expected["caseload"]
    assert counts["treatment"] == expected["activeTx"]


async def test_counting_with_only_tautology_predicates_still_counts_claims(
    db: AsyncSession,
) -> None:
    """Regression (code review, 2026-08-10): the FROM clause must not be
    inferred from the caller's predicates.

    A SELECT of nothing but aggregates has no table of its own. With an
    unbounded scope *and* an all-tautology bucket set, nothing in the
    statement mentioned `claim`, so it compiled to
    `SELECT count(*) FILTER (WHERE true) WHERE true` — one row, count 1,
    no scope filter, no error. The three-bucket top-bar call escaped only
    because two of its predicates happen to name a column.
    """
    everything = await claim_repo.count_claims_matching(
        db, await context_for(db, "David Bline", "supervisor"), {"caseload": sa.true()}
    )
    assert everything["caseload"] == len(seed_fixture.seed()["claims"])

    scoped = await claim_repo.count_claims_matching(
        db, await context_for(db, "Sarah Williams", "handler"), {"caseload": sa.true()}
    )
    assert scoped["caseload"] == len(seed_fixture.claims_for("Sarah Williams", "handler"))


def test_the_counting_statement_always_names_its_table() -> None:
    """The same defect on the compiled SQL, so it is caught without a database.

    The worst case is the one that used to break: unbounded scope, and a
    bucket set whose every predicate is a tautology.
    """
    stmt = claim_repo.count_statement(
        CallerContext(1, UserRole.supervisor, ALL_EMPLOYERS),
        {"caseload": sa.true()},
        ["caseload"],
    )
    assert "FROM claim" in str(stmt)


async def test_a_bucket_predicate_cannot_widen_the_scope(db: AsyncSession) -> None:
    """A caller-supplied predicate is ANDed with the scope, never ORed.

    Buckets exist so `services/worklist` can count "high risk" without the
    repository knowing what risk is. This asserts that the seam cannot be
    used to see somebody else's claims.
    """
    ctx = await context_for(db, "Sarah Williams", "handler")
    counts = await claim_repo.count_claims_matching(db, ctx, {"n": sa.true()})

    assert counts["n"] == len(seed_fixture.claims_for("Sarah Williams", "handler"))
    assert counts["n"] < len(seed_fixture.seed()["claims"])


def test_repositories_package_exports_the_claim_repository() -> None:
    assert repositories.claims is claim_repo
