"""Story 1.4 AC 1/2/4 — the AD-7 repository scope layer.

This is the first repository that reads claim data, so it is also the
template every later repository copies. The tests below therefore assert
the *shape* of the contract (a caller context is required by signature, the
employer filter is unconditional) as hard as they assert the results,
because the shape is what stops Story 6.x from quietly adding an
unscoped query.
"""

import ast
import inspect
import re
import typing
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from data import repositories
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, Claim
from data.models.enums import Stage, UserRole
from data.repositories import claims as claim_repo
from data.repositories import embeddings as embedding_repo
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


#: Every repository module bound by AD-7, and therefore by the two structural
#: guards below.
#:
#: **A list rather than one module, since Story 6.1.** Both guards were written
#: against `claims` alone and this module's own docstring says they exist to
#: stop "Story 6.x quietly adding an unscoped query" — so a guard still bound to
#: one module when Story 6.x actually arrived would have let 6.1 do exactly
#: that, in a file whose queries are the hardest in the codebase to eyeball. The
#: guards are parameterized rather than duplicated so that the *assertions* stay
#: written once: two copies of a structural check are two things to update, and
#: the one nobody updates is the one that stops failing.
#:
#: The three carve-out repositories (`glossary`, `state_rates`,
#: `statutory_forms`) are deliberately absent: they take no caller context at
#: all, and each argues in its own docstring why. `identity` is absent because
#: it is what *builds* a context and necessarily runs before one exists.
SCOPED_REPOSITORY_MODULES = [claim_repo, embedding_repo]

SCOPED_REPOSITORY_IDS = ["claims", "embeddings"]


@pytest.mark.parametrize("module", SCOPED_REPOSITORY_MODULES, ids=SCOPED_REPOSITORY_IDS)
def test_every_repository_read_requires_a_caller_context(module: object) -> None:
    """ "Impossible by signature" — no default, no optional, no keyword escape.

    Reflective rather than a list of hand-written assertions per function:
    a repository method added by a later story is covered the moment it is
    written, which is the only version of this test that keeps working.
    """
    public = [
        (name, obj)
        for name, obj in vars(module).items()
        if inspect.isfunction(obj)
        and not name.startswith("_")
        and obj.__module__ == module.__name__  # type: ignore[attr-defined]
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


def test_no_service_function_accepts_a_caller_context_it_never_reads() -> None:
    """A `ctx` nobody uses is worse than no `ctx` at all (code review, 2026-08-12).

    `data/repositories/statutory_forms.py` makes this argument for the module
    it carves out of AD-7, and the same hazard reached `services/claims`: when
    Story 2.5's code review hoisted `select_documents` up into `claim_detail`,
    it took away `_overview`'s last scoped query and left the parameter behind.
    Nothing failed. Nothing could — an unused argument is invisible to ruff's
    default rules, to mypy, and to every test that calls the function
    correctly.

    What makes it worth a guard rather than a one-line fix is who pays: the
    next author adding a stage variant sees `ctx: CallerContext` in the
    signature and reasonably concludes the scoping is handled here. It is not.
    The signature is documentation, and this is the test that stops it lying.

    Source-level rather than reflective, because the question is "does the
    body mention it", which no signature exposes. `del ctx` counts as a read
    and should: it is a deliberate statement that the argument is unused.
    """
    services = Path(__file__).resolve().parents[1] / "services"
    offenders: list[str] = []

    for module in sorted(services.rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            args = node.args
            takes_ctx = any(
                arg.annotation is not None and ast.unparse(arg.annotation) == "CallerContext"
                for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)
            )
            if not takes_ctx:
                continue
            # The parameter itself is in `node.args`, not in the body, so any
            # `ctx` name found by walking the body is a genuine use.
            used = any(
                isinstance(inner, ast.Name) and inner.id == "ctx"
                for statement in node.body
                for inner in ast.walk(statement)
            )
            if not used:
                offenders.append(f"{module.relative_to(services.parent)}::{node.name}")

    assert offenders == [], (
        "these functions accept a CallerContext and never read it, which reads to the "
        f"next author as proof that scoping was applied: {offenders}"
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


@pytest.mark.parametrize("module", SCOPED_REPOSITORY_MODULES, ids=SCOPED_REPOSITORY_IDS)
def test_no_repository_branches_on_role(module: object) -> None:
    """Scope gates visibility; role gates capability — never the reverse (AD-7).

    A role check inside a repository is how "supervisors see everything"
    gets re-implemented next to the scope filter and then diverges from it.
    """
    source = inspect.getsource(module)  # type: ignore[arg-type]
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


async def test_a_child_read_applies_the_filter_itself_rather_than_trusting_its_caller(
    db: AsyncSession,
) -> None:
    """`select_document`, the first child read whose scope nothing else proves.

    **This test exists because the endpoint-level one could not do the job**
    (code review, 2026-08-12). `services/claims/documents.py` resolves the
    document and *then* re-reads the claim through the already-scoped
    `select_claim_detail`, so deleting `employer_scope` from `select_document`
    leaves every API test green — the route is refused a step later. That makes
    the endpoint safe today and the repository invariant untested, which is
    precisely the state this module's docstring says must not exist: "every
    query in this module applies the filter", enforced structurally rather than
    by whichever caller happens to check afterwards.

    Asserted at the repository, where the guarantee lives. Sarah's book and
    Kaya's are disjoint, so a document that resolves for its owner and not for
    the other handler can only have been filtered by scope.
    """
    owner = await context_for(db, "Sarah Williams", "handler")
    stranger = await context_for(db, "Kaya Johnson", "handler")

    claim_id = sorted(seed_fixture.expected_claim_ids("Sarah Williams", "handler"))[0]
    documents = await claim_repo.select_documents(db, owner, claim_id)
    assert documents, f"{claim_id} has no documents to ask for"
    document_id = documents[0].id

    assert await claim_repo.select_document(db, owner, claim_id, document_id) is not None
    assert await claim_repo.select_document(db, stranger, claim_id, document_id) is None
    # …and the same for the list read the tab is built from, which has the same
    # shape and the same reason to be scoped on its own.
    assert await claim_repo.select_documents(db, stranger, claim_id) == []


async def test_the_photo_read_applies_the_filter_itself(db: AsyncSession) -> None:
    """`select_photos` (Story 2.6), for `select_document`'s reason above.

    Weaker to leave untested than the document read, not stronger: photos are
    claim-derived PHI-class evidence (AD-11) reached from a payload whose other
    blocks are all scoped by the claim read that precedes them. A `select_photos`
    that trusted its caller would be one refactor away from serving another
    handler's incident scene — and no API test would notice, because
    `claim_detail` refuses the claim first.

    Sarah's book and Kaya's are disjoint, so a claim whose photos resolve for
    its owner and not for the other handler can only have been filtered by
    scope.
    """
    owner = await context_for(db, "Sarah Williams", "handler")
    stranger = await context_for(db, "Kaya Johnson", "handler")

    claim_id = sorted(seed_fixture.expected_claim_ids("Sarah Williams", "handler"))[0]

    assert await claim_repo.select_photos(db, owner, claim_id), f"{claim_id} has no photos"
    assert await claim_repo.select_photos(db, stranger, claim_id) == []


def test_repositories_package_exports_the_scoped_repositories() -> None:
    """Both, and by identity rather than by name.

    `repositories.claims` has been asserted since Story 1.4; `embeddings` joins
    it because the package docstring now describes it as the second scoped
    module, and a docstring that named a module the package did not export
    would be the kind of wrong that nothing else notices.
    """
    assert repositories.claims is claim_repo
    assert repositories.embeddings is embedding_repo
    assert set(repositories.__all__) >= {"claims", "embeddings"}
