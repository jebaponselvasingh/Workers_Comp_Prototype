"""Story 6.1 AC 4 — the retro-wiring, and the decision not to wire the fifth.

`deferred-work.md` recorded the AD-12 mark-stale obligation three times across
Stories 2.3, 2.4 and 3.1, against five AD-4 commands, and explicitly handed the
resolution to this story. Four are now wired and one is not, so this module has
a behavioural half and a structural half — and the structural half is the one
that matters most.

**Why a source-level assertion at all.** The behavioural tests below prove that
the four commands mark their claim stale today. They cannot prove anything about
the *sixth* command, written next epic, that mutates a composer input and calls
nothing — which is exactly the failure that produced three identical
`deferred-work.md` entries in the first place. An omission is invisible: the
command commits, the audit row lands, the timeline reads correctly, and the only
symptom is a similar-case answer built from a claim's pre-edit summary, months
later, with a fresh timestamp beside it saying otherwise.

So the AST check pins the current answer in both directions: these four call it,
and `comp_rate.py` does not. A story that wires a fifth command changes a list
here on purpose; a story that unwires one has to explain itself to a failing
test.
"""

import ast
import io
import tokenize
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, Claim
from data.models.enums import UserRole
from data.repositories.identity import employer_ids_for
from services.claims.edit import StaleClaim, update_claim_fields, update_claim_severity
from services.claims.injuries import add_additional_injury, remove_additional_injury
from services.rag import refresh_stale_embeddings
from tests import seed_fixture
from tests.conftest import requires_db
from tests.embedding_fixture import FakeEmbeddingClient

SERVER_ROOT = Path(__file__).resolve().parents[1]
KAYA = ("Kaya Johnson", "handler")

#: The AD-4 commands that mutate a claim-text composer input, and the module
#: each lives in. Written as `(module, function)` rather than as a set of
#: module paths because the obligation attaches to a *command*, not to a file:
#: `services/claims/edit.py` holds two commands and both are wired, while a
#: third added to the same file would need its own decision.
WIRED_COMMANDS = [
    ("services/claims/edit.py", "update_claim_fields"),
    ("services/claims/edit.py", "update_claim_severity"),
    ("services/claims/injuries.py", "add_additional_injury"),
    ("services/claims/injuries.py", "remove_additional_injury"),
]

#: The fifth command, deliberately not wired. `services/claims/comp_rate.py`'s
#: module docstring carries the argument; this is the assertion that the
#: decision cannot be quietly reversed in either direction.
UNWIRED_COMMANDS = [("services/claims/comp_rate.py", "update_comp_rate_override")]


def _calls_mark_claim_stale(module: str, function: str) -> bool:
    """Does this function's body contain a `rag.mark_claim_stale(...)` call?

    AST rather than a substring search, so a mention in a docstring or a comment
    does not count as a call — which matters here specifically, because
    `comp_rate.py` discusses `mark_claim_stale` at length in prose and must
    still read as unwired.
    """
    tree = ast.parse((SERVER_ROOT / module).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) or node.name != function:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                target = inner.func
                name = (
                    target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                )
                if name == "mark_claim_stale":
                    return True
    return False


@pytest.mark.parametrize(("module", "function"), WIRED_COMMANDS)
def test_every_wired_command_calls_mark_claim_stale(module: str, function: str) -> None:
    """The four, asserted structurally so a fifth cannot land unwired unnoticed."""
    assert _calls_mark_claim_stale(module, function), (
        f"{module}::{function} mutates a claim-text composer input and does not ask "
        "services/rag to mark the claim's embedding stale (AD-12)"
    )


@pytest.mark.parametrize(("module", "function"), UNWIRED_COMMANDS)
def test_the_comp_rate_command_is_deliberately_not_wired(module: str, function: str) -> None:
    """A decision, not a miss — and the docstring has to say so.

    Both halves are asserted: no call, *and* a module docstring that explains
    the omission. An undocumented absence is indistinguishable from a command
    somebody forgot, which is precisely how the other four came to be
    outstanding for four stories.
    """
    assert not _calls_mark_claim_stale(module, function)

    source = (SERVER_ROOT / module).read_text(encoding="utf-8")
    docstring = ast.get_docstring(ast.parse(source)) or ""
    assert "mark_claim_stale" in docstring, (
        f"{module} skips the AD-12 mark-stale call and its module docstring does not "
        "record why — see services/claims/comp_rate.py's own argument"
    )


@pytest.mark.parametrize(("module", "function"), [*WIRED_COMMANDS, *UNWIRED_COMMANDS])
def test_no_command_writes_the_embedding_tables_itself(module: str, function: str) -> None:
    """AD-12's other half: the mutating service asks, it never writes.

    `services/rag` performs every write to the three tables. A command that
    reached for `ClaimEmbedding` directly would be a second writer, and the
    ownership statement would stop being checkable by reading one package.

    **Comments and docstrings are stripped before the scan**, the way
    `test_no_repository_branches_on_role` already strips comments. Without it
    this guard and its neighbour pull in opposite directions: that one *requires*
    `comp_rate.py`'s docstring to name `mark_claim_stale` and explain which
    table it is deliberately not marking, while this one would fail the build
    the moment that explanation mentioned the table by name. A rule that makes
    the correct comment unwritable trains the code instead of guarding it.
    """
    source = _code_only((SERVER_ROOT / module).read_text(encoding="utf-8"))
    for forbidden in ("ClaimEmbedding", "claim_embedding", "KnowledgeEmbedding"):
        assert forbidden not in source, f"{module} names {forbidden} — AD-12 gives it no business"


def _code_only(source: str) -> str:
    """`source` with its comments and docstrings removed.

    Tokenize rather than regex: a `#` inside a string literal is not a comment,
    and this guard is asserting on the presence of *identifiers*, so a
    hand-rolled stripper that got that wrong would either hide a real second
    writer or fail on prose that happened to contain a hash.

    A docstring is a string expression standing alone as a statement, which at
    token level is a STRING whose preceding significant token is a NEWLINE,
    INDENT, DEDENT or nothing — that is the whole rule, and it is why the
    `prev` bookkeeping below ignores NL and COMMENT.
    """
    out: list[str] = []
    prev = tokenize.INDENT
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and prev in (
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
        ):
            continue
        if token.type not in (tokenize.NL, tokenize.COMMENT):
            prev = token.type
        out.append(token.string)
    return "\n".join(out)


# --- the behavioural half ------------------------------------------------
#
# Marked `@requires_db` individually rather than with a module-level
# `pytestmark`, because the four structural checks above must run on a laptop
# with no database — they are the half that catches a sixth unwired command,
# and a guard that only runs in CI is a guard nobody sees fail.


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def context_for(db: AsyncSession, name: str, role: str) -> CallerContext:
    user = (
        await db.scalars(sa.select(AppUser).where(AppUser.name == name, AppUser.role == role))
    ).one()
    return CallerContext(
        user_id=user.id,
        role=user.role,
        employer_ids=ALL_EMPLOYERS if user.scope_all else await employer_ids_for(db, user.id),
    )


async def system_ctx(db: AsyncSession) -> CallerContext:
    user = (await db.scalars(sa.select(AppUser).where(AppUser.role == UserRole.system))).one()
    return CallerContext(user_id=user.id, role=user.role, employer_ids=ALL_EMPLOYERS)


async def embed_everything(db: AsyncSession) -> None:
    """Bring the portfolio to `stale = false` so a later flag means something.

    Run at the top of each behavioural test rather than once per module:
    `seeded_db_url` is module-scoped and these commands commit, so a test that
    depended on the previous one's state would pass or fail on file order.
    """
    await db.execute(
        sa.text(
            "UPDATE claim_embedding SET embedding = NULL, stale = true, embedded_at = NULL, "
            "source_text_hash = NULL, model = NULL"
        )
    )
    await db.commit()
    await refresh_stale_embeddings(
        db, await system_ctx(db), client=FakeEmbeddingClient(), limit=None
    )


async def state_of(
    db: AsyncSession, claim_business_id: str
) -> tuple[bool, datetime | None, int, int]:
    """`(stale, embedded_at, audit rows, timeline rows)` for one claim.

    All four in one helper because AC 4's claim is about them *together*: the
    flag and the two log rows arrive from one commit or from none.
    """
    row = (
        await db.execute(
            sa.text(
                "SELECT e.stale, e.embedded_at, "
                "(SELECT count(*) FROM audit_event a WHERE a.entity_id = c.claim_id), "
                "(SELECT count(*) FROM timeline_event t WHERE t.claim_id = c.id) "
                "FROM claim c JOIN claim_embedding e ON e.claim_id = c.id "
                "WHERE c.claim_id = :cid"
            ),
            {"cid": claim_business_id},
        )
    ).one()
    return bool(row[0]), row[1], int(row[2]), int(row[3])


async def claim_version(db: AsyncSession, claim_business_id: str) -> int:
    version = await db.scalar(sa.select(Claim.version).where(Claim.claim_id == claim_business_id))
    return int(version or 0)


@requires_db
async def test_an_inline_edit_marks_the_claim_stale_in_the_same_transaction(
    db: AsyncSession,
) -> None:
    """`update_claim_fields` — the command `deferred-work.md` first recorded.

    Three side effects from one commit: the audit row, the timeline row and the
    stale flag. Asserted together, because "in the same transaction" is the
    acceptance criterion and each of them alone would be satisfied by a version
    that committed separately.
    """
    await embed_everything(db)
    ctx = await context_for(db, *KAYA)
    claim_id = sorted(seed_fixture.expected_claim_ids(*KAYA))[0]
    before = await state_of(db, claim_id)
    assert before[0] is False, "the fixture should leave the claim freshly embedded"

    await update_claim_fields(
        db,
        ctx,
        claim_id,
        expected_version=await claim_version(db, claim_id),
        patch={"injury_type": "Rotator cuff tear"},
    )

    stale, embedded_at, audits, events = await state_of(db, claim_id)
    assert stale is True
    assert embedded_at is not None, "the previous vector is still there, just out of date"
    assert audits > before[2]
    assert events > before[3]


@requires_db
async def test_a_severity_edit_marks_the_claim_stale(db: AsyncSession) -> None:
    """`update_claim_severity` — the raw score is a composer input in its own right.

    The composer deliberately embeds the score rather than the risk band
    (`services/rag/claim_text.py` argues why), so 4 → 7 is a genuinely different
    summary rather than a value that may or may not cross a threshold.
    """
    await embed_everything(db)
    ctx = await context_for(db, *KAYA)
    claim_id = sorted(seed_fixture.expected_claim_ids(*KAYA))[1]
    current = await db.scalar(sa.select(Claim.severity_score).where(Claim.claim_id == claim_id))

    await update_claim_severity(
        db,
        ctx,
        claim_id,
        expected_version=await claim_version(db, claim_id),
        severity_score=(int(current or 0) + 1) % 101,
    )

    stale, _, _, _ = await state_of(db, claim_id)
    assert stale is True


@requires_db
async def test_adding_and_removing_a_secondary_injury_both_mark_the_claim_stale(
    db: AsyncSession,
) -> None:
    """The two commands that write no `claim` column and bump no `claim.version`.

    By every measure `services/claims/injuries.py` uses, the claim did not
    change — which is exactly why `mark_claim_stale` takes a `claim_pk`: a
    command mutating a *child* row still has to be able to mark the parent's
    embedding stale.
    """
    await embed_everything(db)
    ctx = await context_for(db, *KAYA)
    claim_id = sorted(seed_fixture.expected_claim_ids(*KAYA))[2]
    version = await claim_version(db, claim_id)

    detail = await add_additional_injury(
        db,
        ctx,
        claim_id,
        expected_version=version,
        body_key="lumbar",
        injury_type="Strain",
        severity_score=30,
    )
    stale, _, _, _ = await state_of(db, claim_id)
    assert stale is True
    assert await claim_version(db, claim_id) == version, "adding must not bump the claim's version"

    # Re-embed, then remove the row and assert the flag comes back — the mirror
    # image, and the case a claim whose secondary injury was recorded in error
    # would otherwise be stuck in.
    await embed_everything(db)
    # The secondary marker on the diagram — `id` and `version` are `None` on
    # the primary, which is the claim's own body key rather than a row (see
    # `InjuryMarker`), so the added row is the one with an id.
    marker = next(m for m in detail.injury.markers if m.id is not None)
    assert marker.id is not None and marker.version is not None
    await remove_additional_injury(
        db,
        ctx,
        claim_id,
        marker.id,
        expected_version=marker.version,
    )

    stale_again, _, _, _ = await state_of(db, claim_id)
    assert stale_again is True


@requires_db
async def test_a_lost_compare_and_swap_marks_nothing(db: AsyncSession) -> None:
    """Rollback rolls the flag back with everything else (AC 4's second half).

    A stale version writes no column, no audit row, no timeline row — and must
    write no stale flag either. The opposite would mark a claim stale for an
    edit that never happened, and the refresh would then re-embed it and reset
    the flag, so the defect would be invisible from the row and visible only as
    a model round trip nobody asked for.
    """
    await embed_everything(db)
    ctx = await context_for(db, *KAYA)
    claim_id = sorted(seed_fixture.expected_claim_ids(*KAYA))[3]
    before = await state_of(db, claim_id)

    with pytest.raises(StaleClaim):
        await update_claim_fields(
            db,
            ctx,
            claim_id,
            expected_version=await claim_version(db, claim_id) + 99,
            patch={"injury_type": "Never written"},
        )

    assert await state_of(db, claim_id) == before


@requires_db
async def test_a_no_op_patch_marks_nothing(db: AsyncSession) -> None:
    """The log is a record of changes, and so is the stale flag.

    Story 2.3's rule: a patch whose values all equal the stored ones writes
    nothing and emits nothing. Re-embedding on it would cost a model round trip
    per tab-through of the injury card.
    """
    await embed_everything(db)
    ctx = await context_for(db, *KAYA)
    claim_id = sorted(seed_fixture.expected_claim_ids(*KAYA))[4]
    current = await db.scalar(sa.select(Claim.injury_type).where(Claim.claim_id == claim_id))
    before = await state_of(db, claim_id)

    await update_claim_fields(
        db,
        ctx,
        claim_id,
        expected_version=await claim_version(db, claim_id),
        patch={"injury_type": current},
    )

    assert await state_of(db, claim_id) == before


@requires_db
async def test_the_refresh_clears_the_flag_and_stamps_a_fresh_embedded_at(
    db: AsyncSession,
) -> None:
    """The round trip AC 4 describes end to end: edit ⇒ stale ⇒ refresh ⇒ fresh.

    `embedded_at` must actually move, not merely be non-null: a refresh that
    cleared the flag without restamping the timestamp would leave Story 6.4's
    staleness disclosure reading an hour-old time on a second-old vector.
    """
    await embed_everything(db)
    ctx = await context_for(db, *KAYA)
    claim_id = sorted(seed_fixture.expected_claim_ids(*KAYA))[5]
    _, first_embedded_at, _, _ = await state_of(db, claim_id)
    assert first_embedded_at is not None, "the fixture leaves the claim embedded"

    await update_claim_fields(
        db,
        ctx,
        claim_id,
        expected_version=await claim_version(db, claim_id),
        patch={"cause": "Overhead lifting, repeated"},
    )
    assert (await state_of(db, claim_id))[0] is True

    await refresh_stale_embeddings(
        db, await system_ctx(db), client=FakeEmbeddingClient(), limit=None
    )

    stale, embedded_at, _, _ = await state_of(db, claim_id)
    assert stale is False
    assert embedded_at is not None and embedded_at > first_embedded_at


@requires_db
async def test_the_edit_changes_the_stored_source_hash(db: AsyncSession) -> None:
    """The flag and the hash tell the same story from two directions.

    `stale` is what the refresh *selects* on; `source_text_hash` is what proves
    the summary really changed. A composer that had missed the edited field
    would produce a cleared flag and an identical hash — green on every other
    assertion in this module.
    """
    await embed_everything(db)
    ctx = await context_for(db, *KAYA)
    claim_id = sorted(seed_fixture.expected_claim_ids(*KAYA))[6]
    before = await db.scalar(
        sa.text(
            "SELECT e.source_text_hash FROM claim_embedding e "
            "JOIN claim c ON c.id = e.claim_id WHERE c.claim_id = :cid"
        ),
        {"cid": claim_id},
    )

    await update_claim_fields(
        db,
        ctx,
        claim_id,
        expected_version=await claim_version(db, claim_id),
        patch={"cause": "Struck by falling stock"},
    )
    await refresh_stale_embeddings(
        db, await system_ctx(db), client=FakeEmbeddingClient(), limit=None
    )

    after = await db.scalar(
        sa.text(
            "SELECT e.source_text_hash FROM claim_embedding e "
            "JOIN claim c ON c.id = e.claim_id WHERE c.claim_id = :cid"
        ),
        {"cid": claim_id},
    )
    assert before and after and before != after
