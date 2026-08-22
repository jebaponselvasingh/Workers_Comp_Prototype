"""Story 8.1 — the purge cascade, its retention jobs, and the grants under both.

AC 6 asks for "an integration test sweeping relational rows, vectors, cache,
checkpoints, and blobs", and that sweep is the first test in this file. The rest
of the file exists because the sweep alone would pass against several cascades
that are wrong in ways nobody would notice for a year: one that is not
re-runnable, one that redacts by deleting, one that takes an injured worker's
identity out from under their *other* claim, one that runs only because the test
happened to connect as a superuser.

## The cascade runs as the **application role**, deliberately

Every other DB-backed test in this suite connects on `MIGRATION_TEST_DATABASE_
URL`, which is the owner — a superuser in the test container. That is right for
a test *about* the schema and wrong for this one, because the thing being
checked is a command that ships: `purge_claim` runs in the api process and in
`scripts/purge_claim.py`, both on `lineworker_app`, whose grants are exactly
what migration 0003 and migration 0049 say they are. Running the sweep as the
owner would have passed happily against the schema as it stood before this
story, in which the app role cannot delete a `photo` at all.

So `app_db` is an app-role session and it is what every purge below is handed.
The owner engine is used for two things only, both of which the app role
legitimately cannot do: seeding rows the shipped code has no command for
(`photo.blob_key`, an `ai_insight`), and reading `audit_event` back after a
redaction the app role is not permitted to have made.

## These tests share one database and each takes its own claim

The module-scoped `seeded_db_url` rebuilds the schema once for this file, and
every test in it destroys a claim — so nothing may assume the seeded portfolio
is intact, and no two tests may purge the same subject. `_claims()` hands out
distinct business ids from the seed in a fixed order, so a collision is a
`KeyError` at collection time rather than an assertion failure three tests
later. `tests/test_documents_tab.py` and `test_photos_tab.py` both record the
version of this lesson where a destructive test emptied a claim two other tests
were reading.

## The oracle is a scan of every diff, not the predicate

`services/audit/purge.py::_claim_audit_predicate` finds a claim's audit rows by
three disjoint routes and says in its docstring that a test which shared that
predicate would agree with it however wrong it was. So the redaction assertions
here do not restate the predicate: they seed audit rows through the *shipped*
recording paths, then read every remaining diff in the table and fail if either
the seeded worker's **name** or the purged claim's **business id** is anywhere in
it. A route the cascade does not know about fails this file rather than passing
it.

The business id is the second needle because the name alone was not enough, and
the gap it left is instructive: four commands (`services/financials/batch.py`,
`approval.py`, `materialize.py`, `services/claims/assessment.py`) record against
a child entity while naming the claim's business id in `entity_id`, and their
diffs hold statuses, amounts and review flags but never a person's name. A
name-only scan was therefore blind to exactly the rows the first version of the
predicate was blind to — two independent-looking checks with one shared
blind spot, which is the failure mode an oracle exists to not have.
"""

import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agents.threads import discard_thread, mint_thread_id
from config import Env, Settings
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser
from data.models.enums import UserRole
from data.repositories.identity import employer_ids_for
from services import audit
from services.audit._redactor import REDACTOR_ROLE, RedactorUnavailable, redactor_connection
from services.audit.purge import (
    CHECKPOINT_STORE,
    PURGE_ACTION,
    REDACT_ACTION,
    REDACTED_ENTITY,
    REDACTION_MARKER,
    BlobStoreRequired,
    ClaimNotFound,
    SystemActorRefused,
    UserNotFound,
    claim_store_names,
    purge_claim,
    purge_user,
)
from services.audit.retention import (
    AUDIT_RETENTION_ACTION,
    CHECKPOINT_RETENTION_ACTION,
    RetentionFloorMismatch,
    purge_expired_audit_events,
    purge_expired_checkpoints,
)
from services.blobstore import BlobStoreError, VolumeBlobStore
from services.claims.notes import create_diary_note
from services.financials.batch import system_context
from tests import seed_fixture
from tests.conftest import APP_PASSWORD, requires_db

#: The handler whose book every claim below is taken from, and who writes the
#: diary notes the redaction assertions read back. A handler rather than the
#: system actor because `create_diary_note` refuses any other role — which is
#: the point of using the shipped command rather than an `INSERT`.
HANDLER = ("Kaya Johnson", "handler")

#: One claim per test, handed out in a fixed order so that two tests cannot
#: destroy the same subject. Sorted rather than seed-file order so the mapping
#: is stable against a re-ordered seed, and named rather than indexed so a
#: collision is a duplicate key in a literal dict — visible in review.
_CLAIM_FOR: dict[str, int] = {
    "sweep": 0,
    "idempotence": 1,
    "shared_employee_a": 2,
    "shared_employee_b": 3,
    "blob_refusal": 4,
    "blob_missing": 5,
    "skeleton": 6,
    "content_free": 7,
    "checkpoints": 8,
    "redactor_missing": 9,
    "app_role_grants": 10,
    "no_blobs": 11,
    "financial_audit": 12,
    # The retention sweeps need claims to hang threads off too, and they used
    # to reach for `claims[20]` and `claims[21]` by bare index — which is the
    # exact unreserved indexing this map exists to prevent, and which
    # `claim_for`'s length assertion could not cover because the indices never
    # passed through it. Reserved here instead.
    "checkpoint_old": 13,
    "checkpoint_edge": 14,
    "checkpoint_failure": 15,
    "blob_failure": 16,
}


def claim_for(purpose: str) -> str:
    """The business id reserved for one test. See `_CLAIM_FOR`."""
    claims = sorted(seed_fixture.expected_claim_ids(*HANDLER))
    assert len(claims) > max(_CLAIM_FOR.values()), "the seeded book is too small for this file"
    return claims[_CLAIM_FOR[purpose]]


def worker_of(business_id: str) -> str:
    """The injured worker's name, read from the seed file rather than the database.

    An independent oracle in `seed_fixture.py`'s sense: the assertion "this
    person's name is nowhere in the audit log" must not be built from a query
    the purge could have emptied.
    """
    claims = {claim["claim_id"]: claim for claim in seed_fixture.seed()["claims"]}
    employees = {row["employee_id"]: row for row in seed_fixture.seed()["employees"]}
    name: str = employees[claims[business_id]["employee_id"]]["name"]
    return name


# --- plumbing ------------------------------------------------------------


def as_role(url: str, driver: str = "") -> str:
    """`url` rewritten for the application role, password intact.

    `render_as_string(hide_password=False)` rather than `str(URL)`, and the
    difference is not cosmetic: SQLAlchemy's `__str__` masks the password, so a
    URL built the obvious way authenticates as `lineworker_app` with the literal
    string `***` and every test in this file fails on a connection error three
    frames from anything it is about.
    """
    if driver:
        url = url.replace("postgresql://", f"postgresql+{driver}://", 1)
    return (
        sa.make_url(url)
        .set(username="lineworker_app", password=APP_PASSWORD)
        .render_as_string(hide_password=False)
    )


@pytest.fixture
def settings(seeded_db_url: str) -> Settings:
    """App-role runtime URL, owner-role redactor URL — the shipped arrangement.

    `alembic_database_url` is what `services/audit/_redactor.py` reaches for,
    and the two being different roles is the whole of AD-4's split: the
    application may insert an audit row and may never rewrite one.
    """
    return Settings(
        database_url=as_role(seeded_db_url),
        alembic_database_url=seeded_db_url,
        env=Env.e2e,
    )


@pytest.fixture
async def app_db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    """A session on the **application** role — see the module docstring."""
    engine = create_async_engine(as_role(seeded_db_url, "asyncpg"))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
def owner(seeded_db_url: str) -> Iterator[sa.Engine]:
    """A synchronous owner connection, for seeding and for reading audit rows back."""
    engine = sa.create_engine(
        seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1),
        isolation_level="AUTOCOMMIT",
    )
    try:
        yield engine
    finally:
        engine.dispose()


async def system_ctx(db: AsyncSession) -> CallerContext:
    """The one machine identity (`services/financials/batch.py`), reused."""
    return await system_context(db)


async def handler_ctx(db: AsyncSession) -> CallerContext:
    """`HANDLER`'s context, resolved the way `api/deps` resolves one."""
    user = (
        await db.scalars(
            sa.select(AppUser).where(AppUser.name == HANDLER[0], AppUser.role == HANDLER[1])
        )
    ).one()
    return CallerContext(
        user_id=user.id,
        role=UserRole.handler,
        employer_ids=ALL_EMPLOYERS if user.scope_all else await employer_ids_for(db, user.id),
    )


class RecordingBlobStore:
    """A `BlobStore` that remembers what it was asked to delete.

    A double rather than `VolumeBlobStore` for the sweep, because the assertion
    is about *which keys the cascade collected* — and a volume store would
    answer that question only indirectly, by the absence of files whose absence
    could equally mean they were never written. `test_a_blob_the_store_no_longer_
    holds_is_still_counted` uses the real one, where the point is the
    idempotence of `delete` rather than the bookkeeping.
    """

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def put(self, key: str, data: bytes, *, content_type: str) -> None:  # pragma: no cover
        raise AssertionError("the cascade must never write a blob")

    def get(self, key: str) -> bytes:  # pragma: no cover
        raise AssertionError("the cascade must never read a blob")

    def delete(self, key: str) -> None:
        self.deleted.append(key)

    def url(self, key: str) -> str | None:  # pragma: no cover
        return None


class RecordingThreadDeleter:
    """A `ThreadDeleter` that remembers the ids it was handed.

    Enough for every test except `test_the_cascade_discards_the_savers_own_
    checkpoints`, which uses a real `AsyncPostgresSaver` because "the injected
    callable was called" and "the transcripts are gone" are two different
    claims and only the second is AC 1's.
    """

    def __init__(self) -> None:
        self.discarded: list[str] = []

    async def __call__(self, thread_id: str) -> None:
        self.discarded.append(thread_id)


def counts_for(owner: sa.Engine, claim_pk: int) -> dict[str, int]:
    """Every claim-keyed store's row count for one claim, read as the owner.

    The store list comes from `claim_store_names()` rather than being typed out
    here, which is `services/financials/batch.py::entity_names`' arrangement and
    its reason: a table added to the cascade and forgotten by this test would be
    a gap in the one test that exists to find gaps. The two entries that are not
    claim-keyed tables are handled by their callers.
    """
    keyed = {
        name: "claim_id"
        for name in claim_store_names()
        if name not in {CHECKPOINT_STORE, "claim", "employee"}
    }
    with owner.connect() as conn:
        counted = {
            table: conn.execute(
                sa.text(f"SELECT count(*) FROM {table} WHERE {column} = :pk"),  # noqa: S608
                {"pk": claim_pk},
            ).scalar_one()
            for table, column in keyed.items()
        }
        counted["claim"] = conn.execute(
            sa.text("SELECT count(*) FROM claim WHERE id = :pk"), {"pk": claim_pk}
        ).scalar_one()
    return counted


def claim_pk_of(owner: sa.Engine, business_id: str) -> tuple[int, int]:
    with owner.connect() as conn:
        row = conn.execute(
            sa.text("SELECT id, employee_id FROM claim WHERE claim_id = :b"), {"b": business_id}
        ).one()
    return int(row.id), int(row.employee_id)


def audit_rows(owner: sa.Engine) -> list[dict[str, Any]]:
    """Every audit row, as the owner. Nothing in the product reads this table."""
    with owner.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                sa.text(
                    "SELECT id, at, actor_id, actor_role::text AS actor_role, action, "
                    'entity, entity_id, "before", "after" FROM audit_event ORDER BY id'
                )
            ).mappings()
        ]


def seed_derived_stores(owner: sa.Engine, claim_pk: int, user_id: int) -> None:
    """Every store the dev seed leaves empty for a claim, filled so the sweep can bite.

    Five of the sixteen claim-keyed tables carry no seeded rows and would
    therefore make the sweep vacuous for themselves: `ai_insight` and
    `ai_insight_attempt` (nothing has been generated against the model stub),
    `additional_injury` (a secondary injury is captured by a handler, and no
    seeded claim has one), and `diary_note`/`email_log` (both deliberately
    unseeded — a seeded note would be words nobody wrote attributed to a named
    handler, which `tests/test_diary_notes.py` records).

    Written by `INSERT` rather than through the owning commands because the
    subject here is deletion rather than creation: `create_diary_note` is
    exercised as a command in `test_a_user_purge_takes_their_null_claim_rows_
    too`, and the rows this function makes exist only to be counted and then
    counted again at zero.

    `claim_embedding` is **not** in the list: migration 0041 pre-creates one row
    per claim, so the sweep's assertion about the vector store is about a row a
    real deployment has rather than one this test invented.
    """
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO ai_insight (claim_id, kind, content, model, generated_at) "
                "VALUES (:pk, 'reserve_adequacy_review', :content, 'model-stub', now())"
            ),
            {"pk": claim_pk, "content": json.dumps({"narrative": "seeded for the purge test"})},
        )
        conn.execute(
            sa.text("INSERT INTO ai_insight_attempt (claim_id, attempted_at) VALUES (:pk, now())"),
            {"pk": claim_pk},
        )
        conn.execute(
            sa.text(
                "INSERT INTO additional_injury "
                "(claim_id, body_key, body_part, injury_type, severity_score) "
                "VALUES (:pk, 'torso', 'Lower Back', 'Strain', 40)"
            ),
            {"pk": claim_pk},
        )
        conn.execute(
            sa.text(
                "INSERT INTO diary_note (app_user_id, claim_id, note_text, noted_at) "
                "VALUES (:u, :pk, 'Called the plant about light duty.', now())"
            ),
            {"u": user_id, "pk": claim_pk},
        )
        conn.execute(
            sa.text(
                "INSERT INTO email_log "
                "(app_user_id, claim_id, subject, priority, recipients, sent_at) "
                "VALUES (:u, :pk, 'Treatment authorisation', 'normal', :to, now())"
            ),
            {"u": user_id, "pk": claim_pk, "to": json.dumps(["adjuster@example.test"])},
        )


def seed_blob_keys(owner: sa.Engine, claim_pk: int) -> list[str]:
    """Give one document and one photograph a `blob_key`, and return both keys.

    Every one of the 563 seeded documents and 293 seeded photographs carries a
    null key — the prototype has no files behind them — so the binary half of
    AC 1 is unreachable against the dev seed and has to be produced. Two rows in
    two tables rather than one, because the cascade collects from both and a
    single key would not notice a `BLOB_COLUMNS` entry that had been dropped.
    """
    keys = [f"doc-{claim_pk}.pdf", f"photo-{claim_pk}.jpg"]
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE document SET blob_key = :k WHERE id = "
                "(SELECT id FROM document WHERE claim_id = :pk ORDER BY id LIMIT 1)"
            ),
            {"k": keys[0], "pk": claim_pk},
        )
        conn.execute(
            sa.text(
                "UPDATE photo SET blob_key = :k WHERE id = "
                "(SELECT id FROM photo WHERE claim_id = :pk ORDER BY id LIMIT 1)"
            ),
            {"k": keys[1], "pk": claim_pk},
        )
    return keys


def seed_thread(owner: sa.Engine, claim_pk: int, user_id: int, seq: int = 1) -> str:
    """One `copilot_thread` row, with no checkpoints behind it.

    Enough for every test whose subject is the cascade's bookkeeping.
    `test_the_cascade_discards_the_savers_own_checkpoints` writes real ones
    through the saver, which is the only way to check that the transcripts
    themselves are gone.
    """
    with owner.connect() as conn:
        business_id = conn.execute(
            sa.text("SELECT claim_id FROM claim WHERE id = :pk"), {"pk": claim_pk}
        ).scalar_one()
    thread_id = mint_thread_id(str(business_id), user_id, seq)
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO copilot_thread "
                "(thread_id, claim_id, user_id, conversation_seq, created_at) "
                "VALUES (:t, :pk, :u, :s, now())"
            ),
            {"t": thread_id, "pk": claim_pk, "u": user_id, "s": seq},
        )
    return thread_id


async def seed_phi_audit_rows(db: AsyncSession, business_id: str, worker: str) -> None:
    """Audit rows carrying the worker's name, one per route the predicate walks.

    Written through `services/audit.record` — the helper every AD-4 command
    writes through — rather than by `INSERT`, so the rows are shaped exactly as
    the shipped commands shape them. Three routes, because
    `_claim_audit_predicate` needs all three and a test that seeded only one
    would pass against a predicate missing the other two:

    1. `entity = 'claim'` — a field edit, the way `services/claims/edit.py`
       records one.
    2. `before`/`after` carrying `claim_id` — a child entity, the way
       `notes.py::_diff`, `meetings.py::_diff` and `emails.py::_diff` do.
    3. `entity = 'ai_insight'` with `entity_id = '<claim>:<kind>'` — the way
       `services/rag/insights.py` does.
    """
    ctx = await handler_ctx(db)
    await audit.record(
        db,
        ctx,
        action="update_claim_fields",
        entity="claim",
        entity_id=business_id,
        before={"contraindications": f"{worker} may not climb"},
        after={"contraindications": f"{worker} is cleared for light duty"},
    )
    await audit.record(
        db,
        ctx,
        action="send_email",
        entity="email_log",
        entity_id="4242",
        before=None,
        after={"claim_id": business_id, "subject": f"Return to work — {worker}"},
    )
    await audit.record(
        db,
        ctx,
        action="ai_insight.generated",
        entity="ai_insight",
        entity_id=f"{business_id}:fraud_risk",
        before=None,
        after={"summary": f"{worker} filed within 48 hours"},
    )
    await db.commit()


# --- AC 6: the sweep -----------------------------------------------------


@requires_db
async def test_purging_a_claim_empties_every_phi_store_it_touches(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """AC 1 and AC 6 in one gesture — every store class, asserted table by table.

    The five store families the epic names are all here: relational rows across
    the claim aggregate, the `claim_embedding` vector, the `ai_insight` cache
    and its attempt cursor, the conversation the checkpoints hang off, and the
    document and photo binaries collected through `BlobStore`.

    **The premise is asserted before the conclusion.** Every count is checked to
    be non-zero first, because "no rows remain" is trivially satisfied by a
    claim that had none — and three of these stores (`ai_insight`,
    `ai_insight_attempt`, the two `blob_key`s) are unreachable against the dev
    seed and are produced above. If a future seed change makes any of them
    reachable, or unreachable, this fails here with a readable reason rather
    than by making the sweep vacuous.

    **The store list is read from the cascade** (`claim_store_names`), so a
    table added to `CLAIM_CHILDREN` and forgotten here is impossible: the count
    dict grows with it, and the assertion below covers whatever is in it.

    The `employee` row goes too, because this claim is the only one naming that
    worker — the shared case is
    `test_a_worker_with_a_second_claim_outlives_the_purge_of_the_first`.
    """
    business_id = claim_for("sweep")
    claim_pk, employee_pk = claim_pk_of(owner, business_id)
    ctx = await system_ctx(app_db)
    seed_derived_stores(owner, claim_pk, (await handler_ctx(app_db)).user_id)
    keys = seed_blob_keys(owner, claim_pk)
    thread_id = seed_thread(owner, claim_pk, ctx.user_id)

    before = counts_for(owner, claim_pk)
    empty = sorted(table for table, count in before.items() if count == 0)
    assert empty == [], f"the sweep would be vacuous for {empty}"

    blobs = RecordingBlobStore()
    threads = RecordingThreadDeleter()
    run = await purge_claim(
        app_db,
        ctx,
        business_id=business_id,
        settings=settings,
        blobs=blobs,
        delete_thread=threads,
    )

    assert counts_for(owner, claim_pk) == dict.fromkeys(before, 0)
    assert blobs.deleted == keys
    assert threads.discarded == [thread_id]
    assert run.blobs_deleted == len(keys)
    assert run.rows_deleted_by_entity[CHECKPOINT_STORE] == 1
    with owner.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM employee WHERE id = :pk"), {"pk": employee_pk}
            ).scalar_one()
            == 0
        )


@requires_db
async def test_a_purge_writes_one_content_free_event_per_store_it_emptied(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """AC 1's audit half: one `purge` row per touched store, and no content in any.

    Per store rather than per run, because the question an auditor asks is "was
    this claim's document set destroyed, and when". Content-free because the
    only honest payload for "rows were removed" is none — a count in `after`
    would be a fact this very cascade could later erase from itself.

    The claim is the one the sweep above already purged, deliberately: this is a
    second reading of that run's audit rows rather than a second purge, so the
    two tests cannot disagree about which run they are describing.
    """
    business_id = claim_for("sweep")
    events = [
        row
        for row in audit_rows(owner)
        if row["action"] == PURGE_ACTION and row["entity_id"] == business_id
    ]
    assert events, "the sweep wrote no purge events"
    assert {row["entity"] for row in events} <= set(claim_store_names())
    assert all(row["before"] is None and row["after"] is None for row in events)
    # …and one per store, not one per row: `document` lost several.
    assert len({row["entity"] for row in events}) == len(events)


@requires_db
async def test_a_second_purge_of_the_same_claim_deletes_nothing_and_records_nothing(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """The re-runnability property, which is what makes a partial failure survivable.

    A purge that cannot be re-run is a purge that cannot be trusted: the blob
    step and the checkpoint step are both outside the relational transaction, so
    "run it again" has to be the operator's whole recovery procedure.

    Asserted on the audit log as well as on the return value, because an empty
    `PurgeRun` beside a fresh crop of `purge` events would mean the summary and
    the record disagreed — and the record is the one somebody reads in seven
    years.

    **The second call must not raise `ClaimNotFound`, and that is the whole
    point of the fixup this test was rewritten for.** The relational phase
    commits before the blob and redaction steps run, so a failure in either
    leaves a subject with no `claim` row and possibly unredacted diffs — and a
    command that answered "no such claim" to the operator trying to recover from
    exactly that would make its own re-runnability promise false at the one
    moment it is needed. `purge_claim` therefore tells a purged claim from an
    unknown one by the `purge` event the first run committed, and re-runs the
    redaction over a subject whose row is gone.
    `test_an_unknown_claim_is_refused_before_anything_is_touched` is the other
    half: an id with no such event is still refused.
    """
    business_id = claim_for("idempotence")
    claim_pk, _ = claim_pk_of(owner, business_id)
    ctx = await system_ctx(app_db)
    seed_derived_stores(owner, claim_pk, (await handler_ctx(app_db)).user_id)

    first = await purge_claim(
        app_db,
        ctx,
        business_id=business_id,
        settings=settings,
        blobs=None,
        delete_thread=RecordingThreadDeleter(),
    )
    assert not first.is_empty
    events_after_first = len(audit_rows(owner))

    second = await purge_claim(
        app_db,
        ctx,
        business_id=business_id,
        settings=settings,
        blobs=None,
        delete_thread=RecordingThreadDeleter(),
    )

    assert second.is_empty
    assert second.rows_deleted == 0
    assert second.rows_redacted == 0
    assert len(audit_rows(owner)) == events_after_first


@requires_db
async def test_a_second_user_purge_deletes_nothing_redacts_nothing_and_stays_silent(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """Idempotence where the subject survives its own purge — the real re-run case.

    `purge_user` never deletes the `app_user` row, so the subject is still there
    on the second call and the command actually runs a second time. That is what
    makes this the test the module docstring's re-runnability claim rests on:
    every store is already empty, every diff already carries the marker, and the
    run must therefore be empty in all three counters, write no audit row and
    log nothing.
    """
    ctx = await system_ctx(app_db)
    handler = await handler_ctx(app_db)
    # Something of the handler's to lose, minted here rather than assumed: the
    # two demo meetings migration 0033 seeds for this persona hang off the two
    # lowest-sorted claims in her book, which the tests above have already
    # destroyed. A test whose first run was empty would prove nothing about the
    # second.
    await create_diary_note(app_db, handler, note_text="Left a voicemail for the plant nurse.")
    user_id = handler.user_id

    first = await purge_user(
        app_db,
        ctx,
        user_id=user_id,
        settings=settings,
        blobs=None,
        delete_thread=RecordingThreadDeleter(),
    )
    assert not first.is_empty, "the seeded handler has meetings and audit diffs to lose"
    events_before = len(audit_rows(owner))

    second = await purge_user(
        app_db,
        ctx,
        user_id=user_id,
        settings=settings,
        blobs=None,
        delete_thread=RecordingThreadDeleter(),
    )
    assert second.is_empty
    assert second.rows_deleted == 0
    assert second.rows_redacted == 0
    assert len(audit_rows(owner)) == events_before

    # …and the account itself was never a candidate (the story's Block If).
    with owner.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM app_user WHERE id = :u"), {"u": user_id}
            ).scalar_one()
            == 1
        )


@requires_db
async def test_an_unknown_claim_is_refused_before_anything_is_touched(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """A typo must not be a partial cascade.

    `ClaimNotFound` is raised before the redactor connection is opened and
    before any statement that could delete a row. The audit log is the evidence:
    a run that had got as far as *any* store would have written a `purge` event
    for it.

    This is the half `test_a_second_purge_of_the_same_claim_deletes_nothing_and_
    records_nothing` does not cover, and the two together are the whole rule.
    An absent `claim` row is now two states rather than one — a claim this
    cascade already destroyed, and an id that never named anything — and only
    the first of them is re-runnable. `WC-99999` has no `purge` event behind it,
    so it stays a refusal; a purged claim has one, so it resumes. A fixup that
    made every absent row resumable would have turned a typo at three in the
    morning into a silent success.
    """
    events_before = len(audit_rows(owner))
    with pytest.raises(ClaimNotFound):
        await purge_claim(
            app_db,
            await system_ctx(app_db),
            business_id="WC-99999",
            settings=settings,
            blobs=None,
            delete_thread=RecordingThreadDeleter(),
        )
    assert len(audit_rows(owner)) == events_before


@requires_db
async def test_an_unknown_user_is_refused_the_same_way(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """`ClaimNotFound`'s twin, one subject over — `app_user` ids are surrogates
    and a mistyped one must not purge whoever happens to hold it."""
    events_before = len(audit_rows(owner))
    with pytest.raises(UserNotFound):
        await purge_user(
            app_db,
            await system_ctx(app_db),
            user_id=999_999,
            settings=settings,
            blobs=None,
            delete_thread=RecordingThreadDeleter(),
        )
    assert len(audit_rows(owner)) == events_before


@requires_db
async def test_purging_the_system_actor_is_refused_rather_than_redacting_the_whole_log(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """The one `app_user` row `purge_user` must never accept.

    `purge_user` redacts **every diff its subject wrote** — the predicate is
    `actor_id = :user_id` and the command's docstring argues that breadth as a
    feature. Pointed at `services/financials/batch.py::system_context`'s row it
    stops being a feature: that actor writes every payment-batch transition,
    every embedding refresh and every retention summary in the build, so one
    mistyped id would erase the machine-written half of the audit log in a
    single irreversible command.

    There is also nothing there to purge. The system actor holds no diary notes,
    no meetings, no email log and no conversations, and `tests/test_personas.py`
    pins that it can never hold a session either — so the command would delete
    nothing and redact everything, which is the exact inverse of what it is for.

    The assertion is on the log as well as on the exception: a refusal that
    raised *after* redacting would be a worse failure than no refusal at all,
    because the message would say it had not happened.
    """
    ctx = await system_ctx(app_db)
    before = audit_rows(owner)

    with pytest.raises(SystemActorRefused) as refused:
        await purge_user(
            app_db,
            ctx,
            user_id=ctx.user_id,
            settings=settings,
            blobs=None,
            delete_thread=RecordingThreadDeleter(),
        )

    assert str(ctx.user_id) in str(refused.value)
    after = audit_rows(owner)
    assert len(after) == len(before)
    assert [row["after"] for row in after] == [row["after"] for row in before], (
        "the refusal redacted something on its way out"
    )


@requires_db
async def test_a_worker_with_a_second_claim_outlives_the_purge_of_the_first(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """The `employee` row is deleted only when nothing else names it.

    One person can hold more than one claim. The seeded portfolio happens to be
    one-to-one, which is exactly why this state is *produced* rather than found:
    a cascade that deleted the worker unconditionally would pass every other
    test in this file and would break the foreign key of the claim it left
    behind, in production, on the second purge anybody ran.
    """
    doomed, survivor = claim_for("shared_employee_a"), claim_for("shared_employee_b")
    doomed_pk, employee_pk = claim_pk_of(owner, doomed)
    survivor_pk, _ = claim_pk_of(owner, survivor)
    with owner.begin() as conn:
        conn.execute(
            sa.text("UPDATE claim SET employee_id = :e WHERE id = :pk"),
            {"e": employee_pk, "pk": survivor_pk},
        )

    run = await purge_claim(
        app_db,
        await system_ctx(app_db),
        business_id=doomed,
        settings=settings,
        blobs=None,
        delete_thread=RecordingThreadDeleter(),
    )

    assert run.rows_deleted_by_entity["employee"] == 0
    with owner.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM employee WHERE id = :e"), {"e": employee_pk}
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM claim WHERE id = :pk"), {"pk": survivor_pk}
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM claim WHERE id = :pk"), {"pk": doomed_pk}
            ).scalar_one()
            == 0
        )


# --- binaries ------------------------------------------------------------


@requires_db
async def test_a_claim_with_binaries_and_no_store_is_refused_before_the_first_delete(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """`BlobStoreRequired`, and the rows are all still there afterwards.

    The keys live on the rows. A cascade that deleted the `document` rows and
    *then* found it had nowhere to delete their bytes would have destroyed the
    only handles those objects had — unreachable, unpurgeable and, from that
    moment, invisible to every future run of this same command.
    """
    business_id = claim_for("blob_refusal")
    claim_pk, _ = claim_pk_of(owner, business_id)
    seed_blob_keys(owner, claim_pk)
    before = counts_for(owner, claim_pk)

    with pytest.raises(BlobStoreRequired):
        await purge_claim(
            app_db,
            await system_ctx(app_db),
            business_id=business_id,
            settings=settings,
            blobs=None,
            delete_thread=RecordingThreadDeleter(),
        )

    assert counts_for(owner, claim_pk) == before


@requires_db
async def test_a_claim_with_no_binaries_needs_no_store_at_all(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """…and this is the state the seeded portfolio is actually in.

    All 563 documents and all 293 photographs carry a null `blob_key`, because
    the prototype has no files behind them — so the e2e purge and the management
    command run with no store today. If the refusal above were unconditional,
    that would be a cascade nobody could run against the shipped seed, which is
    the failure this test exists to stop the fix for the previous one causing.

    The premise is asserted before the conclusion: a claim that already had keys
    would make this pass for the wrong reason.
    """
    business_id = claim_for("no_blobs")
    claim_pk, _ = claim_pk_of(owner, business_id)
    with owner.connect() as conn:
        keyed = conn.execute(
            sa.text("SELECT count(*) FROM document WHERE claim_id = :pk AND blob_key IS NOT NULL"),
            {"pk": claim_pk},
        ).scalar_one()
    assert keyed == 0, "the seed has grown blob keys; this test needs a claim without one"

    run = await purge_claim(
        app_db,
        await system_ctx(app_db),
        business_id=business_id,
        settings=settings,
        blobs=None,
        delete_thread=RecordingThreadDeleter(),
    )

    assert run.blobs_deleted == 0
    assert counts_for(owner, claim_pk) == dict.fromkeys(counts_for(owner, claim_pk), 0)


@requires_db
async def test_a_blob_the_store_no_longer_holds_is_still_counted_and_its_row_deleted(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings, tmp_path: Path
) -> None:
    """`BlobStore.delete` is documented idempotent for this cascade — checked.

    A half-completed purge re-run finds objects that are already gone, and a
    store that raised `BlobNotFound` on them would make the second run fail at
    exactly the point the first one succeeded. `VolumeBlobStore` over an empty
    directory is the sharpest available version of that: every key is absent.
    """
    business_id = claim_for("blob_missing")
    claim_pk, _ = claim_pk_of(owner, business_id)
    keys = seed_blob_keys(owner, claim_pk)

    run = await purge_claim(
        app_db,
        await system_ctx(app_db),
        business_id=business_id,
        settings=settings,
        blobs=VolumeBlobStore(tmp_path),
        delete_thread=RecordingThreadDeleter(),
    )

    assert run.blobs_deleted == len(keys)
    assert run.blobs_failed == 0
    assert counts_for(owner, claim_pk)["document"] == 0


@requires_db
async def test_a_store_error_on_one_blob_orphans_it_without_skipping_the_redaction(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """A failing `delete` must not take the audit half of the cascade with it.

    `_delete_blobs` runs after the relational commit and immediately before the
    redaction, so an unguarded raise there is not "one object was left behind" —
    it is one object left behind **and** every diff describing the claim left
    unredacted, with the rows that named the object already gone. That trades a
    recoverable failure (an orphan a re-run or a store sweep can find) for an
    unrecoverable one (PHI in an append-only table whose subject no longer
    exists anywhere else).

    So the store raises on one of the two keys and the assertions are that the
    other key still went, that the failure is *reported* rather than swallowed
    (`blobs_failed`), and that the redaction happened anyway.

    The premise is asserted first: a claim with no keys at all would make the
    whole test vacuous, and the seeded portfolio is exactly such a claim until
    `seed_blob_keys` runs.
    """
    business_id = claim_for("blob_failure")
    claim_pk, _ = claim_pk_of(owner, business_id)
    keys = seed_blob_keys(owner, claim_pk)
    assert len(keys) == 2
    await seed_phi_audit_rows(app_db, business_id, worker_of(business_id))

    class HalfBrokenStore(RecordingBlobStore):
        def delete(self, key: str) -> None:
            super().delete(key)
            if key == keys[0]:
                raise BlobStoreError("the volume went away")

    blobs = HalfBrokenStore()
    run = await purge_claim(
        app_db,
        await system_ctx(app_db),
        business_id=business_id,
        settings=settings,
        blobs=blobs,
        delete_thread=RecordingThreadDeleter(),
    )

    # Every key was attempted — the loop did not stop at the first failure.
    assert blobs.deleted == keys
    assert run.blobs_failed == 1
    assert run.blobs_deleted == 1
    # …and the half that cannot be recovered by re-running still happened: the
    # three rows `seed_phi_audit_rows` wrote through the shipped recording paths
    # all carry the marker rather than the worker's name.
    assert run.rows_redacted >= 3
    marked = [
        row
        for row in audit_rows(owner)
        if (row["before"] or row["after"] or {}).get(REDACTION_MARKER) is True
    ]
    assert len(marked) >= 3


# --- AC 3 and AC 4: redaction -------------------------------------------


@requires_db
async def test_the_redaction_overwrites_the_diffs_and_leaves_the_skeleton_untouched(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """AC 3 — byte-for-byte on six columns, and the marker on the other two.

    This is the assertion that distinguishes redaction from deletion, which is
    the whole reason `audit_event` is the one store the cascade does not empty:
    the row has to keep saying that an actor did a thing to a claim at a time,
    so that "who touched this file" stays answerable seven years later.

    The skeleton is captured *before* the purge and compared afterwards rather
    than being spot-checked, because "unchanged" is a claim about all six
    columns and an implementation that quietly rewrote `at` would satisfy any
    smaller assertion.
    """
    business_id = claim_for("skeleton")
    worker = worker_of(business_id)
    await seed_phi_audit_rows(app_db, business_id, worker)

    subjects = {
        row["id"]: row
        for row in audit_rows(owner)
        if row["entity_id"] in {business_id, f"{business_id}:fraud_risk"}
        or (row["after"] or {}).get("claim_id") == business_id
    }
    assert len(subjects) == 3, "the three predicate routes must each have a row"

    run = await purge_claim(
        app_db,
        await system_ctx(app_db),
        business_id=business_id,
        settings=settings,
        blobs=None,
        delete_thread=RecordingThreadDeleter(),
    )
    assert run.rows_redacted >= 3

    after = {row["id"]: row for row in audit_rows(owner)}
    for row_id, original in subjects.items():
        redacted = after[row_id]
        for column in ("at", "actor_id", "actor_role", "action", "entity", "entity_id"):
            assert redacted[column] == original[column], f"{column} moved on row {row_id}"
        for column in ("before", "after"):
            if original[column] is None:
                assert redacted[column] is None, f"{column} was NULL and must stay NULL"
            else:
                assert redacted[column] == {
                    REDACTION_MARKER: True,
                    "redacted_at": redacted[column]["redacted_at"],
                }


@requires_db
async def test_no_audit_diff_anywhere_still_carries_the_purged_workers_name_or_claim_id(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """The **oracle** for AC 3 — a scan, not a restatement of the predicate.

    `_claim_audit_predicate` walks three routes and its docstring says why a
    test that shared it would agree with it however wrong it was. So this reads
    every remaining diff in the whole table and looks for two strings. A fourth
    route nobody thought of fails here.

    **Two needles rather than one, and the second is why this test was rewritten.**
    Scanning for the worker's *name* alone made the oracle blind to exactly the
    rows the predicate was blind to. Four commands —
    `services/financials/batch.py`, `approval.py`, `materialize.py` and
    `services/claims/assessment.py` — record against a child entity while
    putting the **claim's business id** in `entity_id`, and their diffs carry
    statuses, amounts, week numbers and document review flags. Not one of them
    ever writes a person's name into a diff, so a name-only scan agreed with the
    narrow `entity = 'claim' AND entity_id = :wc` predicate however many of
    those rows it left behind. The business id is the string those rows do
    carry, so it is the string the oracle has to look for.

    `entity_id` is skeleton and must keep the business id (AC 3 is explicit that
    it survives byte-identical), so the scan reads `before` and `after` only.

    The premise is asserted first, on a *different* claim and a *different*
    worker: the same scan must still find a name and an id that were never
    purged, or the assertions below would pass against a bug that emptied the
    diff columns entirely.
    """
    purged = claim_for("skeleton")  # redacted by the test above
    untouched = claim_for("content_free")
    await seed_phi_audit_rows(app_db, untouched, worker_of(untouched))

    # The premise behind the premise: two seeded workers who happened to share a
    # name would make the control and the assertion the same string, and the
    # test would pass or fail for a reason that has nothing to do with the code.
    assert worker_of(purged) != worker_of(untouched)
    assert purged != untouched

    serialised = json.dumps(
        [{"before": row["before"], "after": row["after"]} for row in audit_rows(owner)]
    )
    assert worker_of(untouched) in serialised, "the scan cannot see a name it should find"
    assert untouched in serialised, "the scan cannot see a business id it should find"
    assert worker_of(purged) not in serialised
    assert purged not in serialised


@requires_db
async def test_a_financial_audit_row_naming_the_claim_in_entity_id_is_redacted_too(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """The shape the first predicate could not see, seeded and then swept.

    `services/financials/approval.py`, `batch.py`, `materialize.py` and
    `services/claims/assessment.py` all record an audit row whose `entity` is a
    child table — `bill`, `expense`, `payment_schedule_week`, `document` — and
    whose `entity_id` is the **claim's** business id, each with a comment saying
    it does that deliberately so the log answers "what happened to WC-20017"
    without a join. None of their diffs carries a `claim_id` key.

    Against `entity = 'claim' AND entity_id = :wc` those rows matched nothing:
    not route 1 (the entity is wrong), not route 2 (no `claim_id` in the diff),
    not route 3 (not an insight). A claim's whole payment history — plan,
    statuses, amounts — and its document review state therefore survived its own
    purge. This seeds one of them through `audit.record`, the helper every one of
    those four commands writes through, and asserts the diffs carry the marker
    afterwards.

    The row id is captured and re-read rather than the table being scanned,
    because "this row was redacted" and "nothing anywhere mentions the claim"
    are two different claims and the sweep above already makes the second one.
    """
    business_id = claim_for("financial_audit")
    ctx = await handler_ctx(app_db)
    approved = await audit.record(
        app_db,
        ctx,
        action="approve_bill",
        entity="bill",
        # The claim's business id under a *child* entity, and a diff with no
        # `claim_id` key — `approval.py`'s exact shape.
        entity_id=business_id,
        before={"status": "pending", "item": "4242"},
        after={"status": "scheduled", "item": "4242"},
    )
    await app_db.commit()
    row_id = approved.id

    # The premise: the diff really does hold content and really does not name
    # the claim anywhere a `->> 'claim_id'` could find it.
    original = {row["id"]: row for row in audit_rows(owner)}[row_id]
    assert original["after"] == {"status": "scheduled", "item": "4242"}
    assert "claim_id" not in (original["before"] or {})
    assert "claim_id" not in (original["after"] or {})

    run = await purge_claim(
        app_db,
        await system_ctx(app_db),
        business_id=business_id,
        settings=settings,
        blobs=None,
        delete_thread=RecordingThreadDeleter(),
    )
    assert run.rows_redacted >= 1

    redacted = {row["id"]: row for row in audit_rows(owner)}[row_id]
    for column in ("before", "after"):
        assert redacted[column][REDACTION_MARKER] is True, f"{column} kept its content"
    # …and the skeleton is untouched, including the `entity_id` that is the only
    # thing tying this row to the claim it describes.
    for column in ("actor_id", "actor_role", "action", "entity", "entity_id", "at"):
        assert redacted[column] == original[column], f"{column} moved"


@requires_db
async def test_a_content_free_row_is_left_exactly_as_it_is_and_emits_no_redact_event(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """A row with no diff was never redacted, so it does not say it was.

    `copilot_approval.*` rows carry `before = after = NULL` by construction, and
    writing a marker into a column that never held content would assert a
    redaction that did not happen — a lie the seven-year floor would then
    preserve. AC 4 asks for one `redact` event *per redaction*, and there was
    none here.
    """
    business_id = claim_for("content_free")
    ctx = await handler_ctx(app_db)
    approval = await audit.record_copilot_approval(
        app_db, ctx, outcome="approved", claim_business_id=business_id
    )
    approval_id = approval.id

    run = await purge_claim(
        app_db,
        await system_ctx(app_db),
        business_id=business_id,
        settings=settings,
        blobs=None,
        delete_thread=RecordingThreadDeleter(),
    )

    rows = {row["id"]: row for row in audit_rows(owner)}
    assert rows[approval_id]["before"] is None
    assert rows[approval_id]["after"] is None
    redacted_ids = {
        int(row["entity_id"])
        for row in rows.values()
        if row["action"] == REDACT_ACTION and row["entity"] == REDACTED_ENTITY
    }
    assert approval_id not in redacted_ids
    # AC 4: exactly one event per redacted row, and the count agrees with the
    # run summary rather than with a second reading of the predicate.
    assert (
        len([row for row in rows.values() if row["action"] == REDACT_ACTION]) >= run.rows_redacted
    )


@requires_db
async def test_one_redact_event_names_each_row_it_redacted(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """AC 4, read off the log the previous test's purge wrote.

    `entity_id` carries the rewritten row's own id — the one place in this build
    where an `entity_id` is a surrogate rather than a business key, and the only
    identifier an audit row has. Every `redact` event must point at a row that
    (a) still exists and (b) actually carries the marker: an event naming a row
    that was never rewritten would be the audit trail describing something that
    did not happen.
    """
    rows = {row["id"]: row for row in audit_rows(owner)}
    events = [row for row in rows.values() if row["action"] == REDACT_ACTION]
    assert events, "no redaction has happened in this module yet"
    assert all(row["before"] is None and row["after"] is None for row in events)

    named = [int(row["entity_id"]) for row in events]
    assert len(named) == len(set(named)), "a row was redacted twice"
    for row_id in named:
        target = rows[row_id]
        assert (target["before"] or target["after"] or {}).get(REDACTION_MARKER) is True


# --- checkpoints ---------------------------------------------------------


@requires_db
async def test_the_cascade_discards_the_savers_own_checkpoints(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings, seeded_db_url: str
) -> None:
    """AC 1's checkpoint clause, through the real saver rather than through a double.

    Every other test here injects a `RecordingThreadDeleter`, which proves the
    cascade *calls* the seam. That is not the same claim as "the transcripts are
    gone", and the transcripts are the PHI — a checkpoint quotes a diagnosis, a
    wage and a fraud indicator back verbatim. So this one builds an
    `AsyncPostgresSaver` over the vendored tables, writes a real checkpoint,
    binds the seam to `agents.threads.discard_thread` exactly as
    `scripts/purge_claim.py` does, and reads the tables back.

    The read is raw SQL **from a test**, which is the only place it is allowed:
    AD-3's exception forbids *application* code from touching these tables and
    `tests/test_layering.py` enforces that, while a test is the independent
    oracle (`test_copilot_persistence.py` says the same thing about the same
    tables).

    `saver.setup()` is never called — migration 0043 vendored the DDL, and the
    tables are already there.
    """
    business_id = claim_for("checkpoints")
    claim_pk, _ = claim_pk_of(owner, business_id)
    ctx = await system_ctx(app_db)
    thread_id = seed_thread(owner, claim_pk, ctx.user_id)

    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=1,
        max_size=1,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    try:
        saver = AsyncPostgresSaver(pool)
        await saver.aput(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
            empty_checkpoint(),
            # The vendor's `CheckpointMetadata` fixes the vocabulary of
            # `source`; `"input"` is the member a first turn carries.
            {"source": "input", "step": 0},
            {},
        )
        with owner.connect() as conn:
            written = conn.execute(
                sa.text("SELECT count(*) FROM checkpoints WHERE thread_id = :t"),
                {"t": thread_id},
            ).scalar_one()
        assert written > 0, "the saver wrote nothing, so the sweep would be vacuous"

        async def delete_thread(value: str) -> None:
            await discard_thread(saver, thread_id=value)

        await purge_claim(
            app_db,
            ctx,
            business_id=business_id,
            settings=settings,
            blobs=None,
            delete_thread=delete_thread,
        )
    finally:
        await pool.close()

    with owner.connect() as conn:
        remaining = conn.execute(
            sa.text("SELECT count(*) FROM checkpoints WHERE thread_id = :t"), {"t": thread_id}
        ).scalar_one()
        threads = conn.execute(
            sa.text("SELECT count(*) FROM copilot_thread WHERE thread_id = :t"), {"t": thread_id}
        ).scalar_one()
    assert remaining == 0
    assert threads == 0


# --- the grant and RLS boundary -----------------------------------------


@requires_db
def test_the_app_role_cannot_rewrite_an_audit_diff(owner: sa.Engine, seeded_db_url: str) -> None:
    """AD-4's floor: the application is INSERT-only on `audit_event`, still.

    Story 8.1 adds a role that *can* rewrite a diff, and the whole design rests
    on that role not being this one. Asserted here as well as in
    `tests/test_schema_seed.py` because this is the file about the code that
    redacts: the day somebody "fixes" a permission error by granting UPDATE to
    `lineworker_app`, the failure should be in the purge story's own suite.
    """
    engine = sa.create_engine(as_role(seeded_db_url, "psycopg"))
    try:
        with engine.connect() as conn, pytest.raises(ProgrammingError, match="permission denied"):
            conn.execute(sa.text('UPDATE audit_event SET "after" = NULL'))
    finally:
        engine.dispose()


@requires_db
def test_the_redactor_cannot_insert_an_audit_event(owner: sa.Engine) -> None:
    """…which is why the `purge` and `redact` events are written by the app role.

    A redactor that could insert would be a role able to *forge* audit history
    as well as erase it, and the two grants AD-4 names would have become three.
    The asymmetry is what forces the cascade's two-connection shape, so it is
    worth pinning: if this ever passes, `services/audit/purge.py` could be
    simplified in a way that quietly widens the exception.
    """
    with owner.connect() as conn:
        conn.execute(sa.text(f"SET ROLE {REDACTOR_ROLE}"))
        try:
            with pytest.raises(ProgrammingError, match="permission denied"):
                conn.execute(
                    sa.text(
                        "INSERT INTO audit_event "
                        "(at, actor_id, actor_role, action, entity, entity_id) "
                        "VALUES (now(), 1, 'handler', 'forged', 'claim', 'WC-00000')"
                    )
                )
        finally:
            conn.execute(sa.text("RESET ROLE"))


@requires_db
def test_the_redactor_cannot_delete_a_row_younger_than_the_retention_floor(
    owner: sa.Engine,
) -> None:
    """The RLS policy is the safety net, and a net is only a net if it holds.

    `audit_redactor_delete` is `USING (at < now() - interval '7 years')`. A bug
    in `retention.py`'s arithmetic — or an operator who set
    `AUDIT_RETENTION_YEARS=0` — must not be able to delete yesterday's audit
    trail, and the database is what makes that true rather than the code.

    Note the shape of the failure: **`rowcount == 0`, not an error**. RLS filters
    rather than refuses, which is exactly why `purge_expired_audit_events` counts
    before it deletes: without that comparison an under-delete is
    indistinguishable from a quiet day.
    """
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO audit_event "
                "(at, actor_id, actor_role, action, entity, entity_id) VALUES "
                "(now() - interval '1 day', (SELECT id FROM app_user LIMIT 1), 'handler', "
                "'test', 'claim', 'WC-RLS-YOUNG')"
            )
        )
    with owner.connect() as conn:
        conn.execute(sa.text(f"SET ROLE {REDACTOR_ROLE}"))
        try:
            deleted = conn.execute(
                sa.text("DELETE FROM audit_event WHERE entity_id = 'WC-RLS-YOUNG'")
            ).rowcount
        finally:
            conn.execute(sa.text("RESET ROLE"))
    assert deleted == 0
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM audit_event WHERE entity_id = 'WC-RLS-YOUNG'"))


@requires_db
def test_the_app_role_can_delete_a_photo_so_story_8_1_can_purge_one(
    owner: sa.Engine, seeded_db_url: str
) -> None:
    """Migration 0049's first grant, asserted through behaviour rather than catalog.

    `tests/test_photo_migration.py` checks the grant list; this checks that the
    statement the cascade actually issues succeeds under the role the cascade
    actually runs as. Before this story it did not: `photo` was `SELECT` only,
    and the purge would have failed on the ninth table of sixteen with the claim
    already half destroyed.

    Rolled back rather than committed — this file destroys enough claims as it
    is, and the assertion is that the statement was *permitted*, which `rowcount`
    answers without keeping the result.
    """
    business_id = claim_for("app_role_grants")
    engine = sa.create_engine(as_role(seeded_db_url, "psycopg"))
    try:
        with engine.connect() as conn:
            deleted = conn.execute(
                sa.text(
                    "DELETE FROM photo WHERE claim_id = (SELECT id FROM claim WHERE claim_id = :b)"
                ),
                {"b": business_id},
            ).rowcount
            conn.rollback()
    finally:
        engine.dispose()
    assert deleted > 0


@requires_db
async def test_a_purge_without_an_owner_url_is_refused_before_anything_is_deleted(
    app_db: AsyncSession, owner: sa.Engine, seeded_db_url: str
) -> None:
    """`RedactorUnavailable`, and the message names the role and never the DSN.

    A purge with no redaction path is the worst of both outcomes: the rows are
    destroyed and their contents survive in the audit log, with no way to re-run
    the half that failed because the half that succeeded is not repeatable. So
    the connection is opened *before* the first delete, and a deployment that
    cleared `ALEMBIC_DATABASE_URL` gets a refusal rather than a half-purge.

    The DSN carries the owner's password and an exception message reaches logs,
    stderr and bug reports — hence the second assertion.
    """
    business_id = claim_for("redactor_missing")
    claim_pk, _ = claim_pk_of(owner, business_id)
    before = counts_for(owner, claim_pk)
    app_only = Settings(database_url=as_role(seeded_db_url), alembic_database_url=None, env=Env.e2e)

    with pytest.raises(RedactorUnavailable) as refusal:
        await purge_claim(
            app_db,
            await system_ctx(app_db),
            business_id=business_id,
            settings=app_only,
            blobs=None,
            delete_thread=RecordingThreadDeleter(),
        )

    assert REDACTOR_ROLE in str(refusal.value) or "ALEMBIC_DATABASE_URL" in str(refusal.value)
    assert APP_PASSWORD not in str(refusal.value)
    assert counts_for(owner, claim_pk) == before


@requires_db
async def test_the_redactor_connection_really_is_the_redactor(settings: Settings) -> None:
    """`SET ROLE` is verified rather than assumed — `_assume_role`'s second statement.

    A switch that silently did not happen would leave every redaction running as
    the *owner*, who bypasses row-level security: the redaction would succeed,
    the retention floor would stop applying, and nothing anywhere would say so.
    That is a failure with no symptom, which is why the module reads
    `current_user` back and why this test reads it a second time.
    """
    async with redactor_connection(settings) as conn:
        assert await conn.scalar(sa.text("SELECT current_user")) == REDACTOR_ROLE


# --- retention -----------------------------------------------------------


def _insert_audit_row(owner: sa.Engine, *, at_sql: str, entity_id: str) -> int:
    """One audit row stamped by a SQL expression the caller writes.

    The instant is an expression rather than an interval string because the
    interesting case is *just inside* the floor, and `interval \'7 years - 1
    second\'` is not a thing PostgreSQL parses. Interpolated rather than bound
    because a bind parameter cannot be an expression; the values are literals in
    this file and never reach here from anywhere else.
    """
    with owner.begin() as conn:
        return int(
            conn.execute(
                sa.text(
                    "INSERT INTO audit_event "
                    "(at, actor_id, actor_role, action, entity, entity_id) VALUES "
                    f"({at_sql}, (SELECT id FROM app_user LIMIT 1), "  # noqa: S608
                    "'handler', 'test', 'claim', :e) RETURNING id"
                ),
                {"e": entity_id},
            ).scalar_one()
        )


@requires_db
async def test_the_audit_sweep_deletes_past_the_floor_and_spares_the_boundary(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """AC 5's housekeeping, and the boundary in the same test — deliberately.

    The two belong together because the interesting failure is a comparison
    written `<=`, which deletes the row at exactly the floor and passes every
    test that only checks that old rows go. Three ages: ten years (past),
    one second inside seven years (the boundary, which must survive), and one
    day (plainly young).

    The sweep's summary event is content-free and carries the cutoff as
    `entity_id`, which is what makes a count checkable after the event: "deleted
    N rows" says nothing without "everything older than this".
    """
    old = _insert_audit_row(owner, at_sql="now() - interval '10 years'", entity_id="WC-RET-OLD")
    # One minute *inside* the floor rather than one second: the sweep's `now()`
    # is later than this insert's by however long the test takes, and a margin
    # of seconds would make the boundary assertion a race rather than a rule.
    boundary = _insert_audit_row(
        owner,
        at_sql="now() - interval '7 years' + interval '1 minute'",
        entity_id="WC-RET-EDGE",
    )
    young = _insert_audit_row(owner, at_sql="now() - interval '1 day'", entity_id="WC-RET-YOUNG")

    run = await purge_expired_audit_events(app_db, await system_ctx(app_db), settings=settings)

    surviving = {row["id"] for row in audit_rows(owner)}
    assert old not in surviving
    assert boundary in surviving, "a row at exactly the floor must survive (`<`, never `<=`)"
    assert young in surviving
    assert run.rows_deleted >= 1

    summary = [row for row in audit_rows(owner) if row["action"] == AUDIT_RETENTION_ACTION]
    assert len(summary) == 1
    assert summary[0]["before"] is None and summary[0]["after"] is None
    assert summary[0]["entity_id"] == run.cutoff.isoformat()


@requires_db
async def test_an_audit_sweep_with_nothing_due_writes_no_event_and_deletes_nothing(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """A daily job that logged "swept nothing" would bury the runs that did something.

    Run immediately after the sweep above, when everything past the floor has
    already gone — so this is the state the job is in on 364 days out of 365,
    and the property is that those 364 runs are invisible.
    """
    before = len(audit_rows(owner))
    run = await purge_expired_audit_events(app_db, await system_ctx(app_db), settings=settings)
    assert run.is_empty
    assert len(audit_rows(owner)) == before


@requires_db
async def test_a_floor_shorter_than_the_policys_raises_rather_than_under_deleting(
    app_db: AsyncSession, owner: sa.Engine, seeded_db_url: str
) -> None:
    """The divergence migration 0049's docstring refuses to close by weakening the policy.

    Configuration says one year; the RLS policy migration 0003 baked in still
    says seven. The rows between are selected by the job's own cutoff and
    refused by the policy, and **nothing raises on its own** — RLS filters
    rather than errors, so the job would report "nothing was due" for ever.

    That is why the count-then-delete comparison exists, and this is the test
    that makes it load-bearing. Without it, lowering `AUDIT_RETENTION_YEARS`
    without a migration is a silent no-op that looks exactly like a healthy job.
    """
    _insert_audit_row(owner, at_sql="now() - interval '3 years'", entity_id="WC-RET-MISMATCH")
    shortened = Settings(
        database_url=as_role(seeded_db_url),
        alembic_database_url=seeded_db_url,
        audit_retention_years=1,
        env=Env.e2e,
    )

    with pytest.raises(RetentionFloorMismatch) as mismatch:
        await purge_expired_audit_events(app_db, await system_ctx(app_db), settings=shortened)
    assert "AUDIT_RETENTION_YEARS=1" in str(mismatch.value)

    # …and the row is still there, which is the outcome the policy exists for.
    assert any(row["entity_id"] == "WC-RET-MISMATCH" for row in audit_rows(owner))
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM audit_event WHERE entity_id = 'WC-RET-MISMATCH'"))


@requires_db
async def test_the_checkpoint_sweep_discards_conversations_older_than_the_cutoff(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """AC 5's other floor, keyed on `copilot_thread.created_at` (Design Note 6).

    Three threads: one comfortably past the ninety-day cutoff, one one second
    inside it, and one minted now. The middle one is the boundary and must
    survive — `<`, never `<=`, for `purge_expired_audit_events`' reason.

    The knob bounds a conversation's **age** rather than its idleness, because
    the checkpoint tables carry no timestamp this codebase may read. A thread
    created ninety-one days ago is swept even if it was used yesterday; that
    limitation is recorded in `deferred-work.md` rather than worked around.
    """
    ctx = await system_ctx(app_db)
    # Five minutes inside the cutoff rather than one second, for the audit
    # sweep's reason: the job computes `now()` after this fixture ran.
    #
    # Both claims come through `claim_for` rather than off a bare index into
    # the sorted book. An earlier version reached `claims[20]` and `claims[21]`
    # directly, which put two subjects outside `_CLAIM_FOR` and therefore
    # outside the collision check the map exists to be — a test added later and
    # given index 20 would have destroyed this one's thread with no failure
    # anywhere near the cause.
    ages = {
        "old": (claim_for("checkpoint_old"), timedelta(days=200)),
        "edge": (claim_for("checkpoint_edge"), timedelta(days=90) - timedelta(minutes=5)),
    }
    threads: dict[str, str] = {}
    for label, (business_id, age) in ages.items():
        claim_pk, _ = claim_pk_of(owner, business_id)
        threads[label] = seed_thread(owner, claim_pk, ctx.user_id)
        with owner.begin() as conn:
            conn.execute(
                sa.text("UPDATE copilot_thread SET created_at = :at WHERE thread_id = :t"),
                {"at": datetime.now(UTC) - age, "t": threads[label]},
            )

    deleter = RecordingThreadDeleter()
    run = await purge_expired_checkpoints(app_db, ctx, settings=settings, delete_thread=deleter)

    assert threads["old"] in deleter.discarded
    assert threads["edge"] not in deleter.discarded
    assert run.threads_deleted == len(deleter.discarded)
    with owner.connect() as conn:
        remaining = set(
            conn.execute(sa.text("SELECT thread_id FROM copilot_thread")).scalars().all()
        )
    assert threads["old"] not in remaining
    assert threads["edge"] in remaining

    summary = [row for row in audit_rows(owner) if row["action"] == CHECKPOINT_RETENTION_ACTION]
    assert len(summary) == 1
    assert summary[0]["before"] is None and summary[0]["after"] is None


@requires_db
async def test_a_checkpoint_sweep_with_nothing_due_writes_no_event(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """The silent-empty-run property, one floor over — see the audit sweep's twin."""
    before = len(audit_rows(owner))
    run = await purge_expired_checkpoints(
        app_db,
        await system_ctx(app_db),
        settings=settings,
        delete_thread=RecordingThreadDeleter(),
    )
    assert run.is_empty
    assert len(audit_rows(owner)) == before


@requires_db
async def test_one_thread_the_saver_cannot_discard_does_not_stall_the_whole_sweep(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """A daily job whose loop is unguarded stops deleting anything, for ever.

    This is the failure the guard exists for and it is not a dramatic one: one
    checkpoint row corrupted by a half-written write, or one thread id the
    vendored tables no longer recognise, and `adelete_thread` raises. Because
    the sweep's input is "every thread past the cutoff", that same thread is
    re-selected tomorrow and every day after — so an unguarded loop means not
    one conversation is ever deleted again, and the only symptom is a daily
    exception nobody connects to a retention floor.

    Two threads on one claim, both well past the cutoff, and a deleter that
    raises for exactly one of them. The assertions are that the *other* one went
    all the way (transcript discarded **and** `copilot_thread` row deleted), and
    that the failing one kept its row — because a row kept beside a transcript
    that is still there is the honest state and is what makes the next run retry
    that thread rather than orphan it.

    The failing thread is deleted at the end. It is older than the cutoff by
    two hundred days and would otherwise be swept up by every retention test
    added to this file afterwards, which is the kind of shared-state coupling
    the module docstring's one-claim-per-test rule exists to avoid.
    """
    ctx = await system_ctx(app_db)
    claim_pk, _ = claim_pk_of(owner, claim_for("checkpoint_failure"))
    doomed = seed_thread(owner, claim_pk, ctx.user_id, seq=1)
    stubborn = seed_thread(owner, claim_pk, ctx.user_id, seq=2)
    with owner.begin() as conn:
        conn.execute(
            sa.text("UPDATE copilot_thread SET created_at = :at WHERE thread_id = ANY(:ids)"),
            {"at": datetime.now(UTC) - timedelta(days=200), "ids": [doomed, stubborn]},
        )

    attempted: list[str] = []

    async def flaky(thread_id: str) -> None:
        attempted.append(thread_id)
        if thread_id == stubborn:
            raise RuntimeError("the saver could not discard this transcript")

    run = await purge_expired_checkpoints(app_db, ctx, settings=settings, delete_thread=flaky)

    # The premise: the sweep really did reach both, so "the other one survived"
    # is a statement about the guard rather than about a loop that stopped.
    assert set(attempted) == {doomed, stubborn}
    assert run.threads_deleted == 1
    with owner.connect() as conn:
        remaining = set(
            conn.execute(sa.text("SELECT thread_id FROM copilot_thread")).scalars().all()
        )
    assert doomed not in remaining
    assert stubborn in remaining, "a thread whose transcript survived must keep its index row"

    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM copilot_thread WHERE thread_id = :t"), {"t": stubborn})


# --- the user cascade ----------------------------------------------------


@requires_db
async def test_a_user_purge_takes_their_null_claim_rows_too(
    app_db: AsyncSession, owner: sa.Engine, settings: Settings
) -> None:
    """A note tagged to no claim is still that handler's note.

    `meeting.claim_id`, `diary_note.claim_id` and `email_log.claim_id` are all
    nullable, and a purge keyed on the claim would leave exactly the rows
    nothing else can reach — invisible to a claim cascade for ever, because
    there is no claim to cascade from.

    The note is written through the shipped command rather than by `INSERT`, so
    it carries the audit row a real one carries; the assertion below is that
    both the row and its diff are gone.
    """
    ctx = await handler_ctx(app_db)
    note = await create_diary_note(app_db, ctx, note_text="No claim on this one.")

    with owner.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM diary_note WHERE id = :i AND claim_id IS NULL"),
                {"i": note.id},
            ).scalar_one()
            == 1
        )

    run = await purge_user(
        app_db,
        await system_ctx(app_db),
        user_id=ctx.user_id,
        settings=settings,
        blobs=None,
        delete_thread=RecordingThreadDeleter(),
    )

    assert run.rows_deleted_by_entity["diary_note"] >= 1
    with owner.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM diary_note WHERE id = :i"), {"i": note.id}
            ).scalar_one()
            == 0
        )
    diffs = json.dumps(
        [{"before": row["before"], "after": row["after"]} for row in audit_rows(owner)]
    )
    assert "No claim on this one." not in diffs


# --- the run summary -----------------------------------------------------


@requires_db
def test_the_run_summary_is_safe_to_print() -> None:
    """`PurgeRun.as_json` is what the management command puts on stdout.

    Counts and store names, and that is the whole of it: the object is printed
    on an operator's terminal, into a shell's scrollback and quite possibly into
    a ticket, so anything claim-shaped in it would be PHI leaving the system
    through the one surface nobody thinks of as an egress.

    Checked as a property of the *shape* rather than by scanning a run's output,
    because the keys are fixed and a value that was not a count would be a code
    change rather than a data one.
    """
    from services.audit.purge import PurgeRun

    payload = PurgeRun(
        rows_deleted_by_entity={"document": 4, "photo": 0},
        blobs_deleted=1,
        rows_redacted=2,
        blobs_failed=1,
    ).as_json()

    assert set(payload) == {
        "rowsDeleted",
        "rowsDeletedByEntity",
        "blobsDeleted",
        "blobsFailed",
        "rowsRedacted",
        "isEmpty",
    }
    assert payload["rowsDeleted"] == 4
    assert payload["blobsFailed"] == 1
    assert payload["isEmpty"] is False
    assert all(isinstance(value, int) for value in payload["rowsDeletedByEntity"].values())
    assert set(payload["rowsDeletedByEntity"]) <= set(claim_store_names())
