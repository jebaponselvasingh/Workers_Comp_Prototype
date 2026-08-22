"""AD-11 on the two flows nothing else captures — asserted against real stderr.

`tests/test_copilot_logging.py` and `tests/test_ai_insights.py` do this for the
copilot and for insight generation. `scripts/lint_log_phi.py` does the static
half over the whole tree. What was left unwatched is the oldest write path in
the build and the newest: an **audited inline claim edit**, which is what a
handler does all day and which passes a claim's fields through the audit
recorder on every keystroke that commits — and the **retention sweeps**, whose
whole job is to touch every expired row in two PHI-bearing stores.

## Every search here is paired with a positive control

`tests/test_copilot_logging.py`'s rule, restated because it is the rule that
decides whether a file like this means anything: **a search for absent strings
passes identically against a process that logged nothing at all.** A test that
runs a flow, captures stderr and asserts four `not in`s is green if logging was
never configured, if the flow raised before it wrote, or if somebody deleted the
event. So each block names an event that *must* be there — `audit.recorded` for
the edit, `retention.audit_swept` and `retention.checkpoints_swept` for the
sweeps — and asserts it first.

## The needles are seeded values, read from the seed file

An assertion that a worker's name is absent is only worth having if the name is
real, distinctive and actually in the row the flow touched. `tests/seed_fixture
.py` is the independent oracle for that — it reads the same JSON the migration
seeds from — so the needles come from there rather than from a query the flow
could have changed. The one needle that is *not* seeded is the diary note's
text: notes start empty by design (migration 0034 says why), so this file writes
one, and it is the strongest needle of the set because it is free text a handler
typed about an injured worker.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agents.threads import mint_thread_id
from config import Env, Settings
from data.context import ALL_EMPLOYERS, CallerContext
from data.models import AppUser, Claim
from data.repositories.identity import employer_ids_for
from logging_config import configure_logging
from services.audit.retention import purge_expired_audit_events, purge_expired_checkpoints
from services.claims.edit import update_claim_fields
from services.claims.notes import create_diary_note
from services.financials.batch import system_context
from tests import seed_fixture
from tests.conftest import APP_PASSWORD, requires_db

pytestmark = requires_db

HANDLER = ("Kaya Johnson", "handler")

#: The value typed into the edited field, and it is a needle rather than data.
#:
#: Long, specific and unlike anything else this process emits — the same
#: technique and the same reason as `test_copilot_logging.py::QUESTION`. A
#: realistic value ("Fall from Height (corrected)") would make the absence
#: assertion weak: it shares words with the seed, with the log's event names and
#: with half the module docstrings in `services/claims`.
EDITED_INJURY_TYPE = "ZZ distinctive injury description fixture 8-2 no log may carry"

#: The value the *second* audited edit in this file writes, and it has to be a
#: different string rather than a tidy reuse of the one above.
#:
#: `seeded_db_url` is module-scoped, so by the time the backstop test runs the
#: claim's `injury_type` is already `EDITED_INJURY_TYPE` — and
#: `services/claims/edit.py` treats a patch that changes nothing as a no-op:
#: no UPDATE, no audit row, no log line at all. Reusing the constant would
#: leave that test asserting the absence of a marker in a flow that never ran,
#: which is precisely the vacuity every other control in this file exists to
#: rule out.
RE_EDITED_INJURY_TYPE = "ZZ distinctive injury description fixture 8-2 second edit no log may carry"

#: The diary note's body — free text about an injured worker, which is the
#: single richest PHI surface in the non-AI half of this build.
NOTE_TEXT = (
    "ZZ distinctive diary note fixture 8-2: claimant reports persistent pain and "
    "declines the modified duty offer."
)


def a_claim_of(persona: tuple[str, str]) -> str:
    """The first claim in the persona's book, by business id."""
    return sorted(seed_fixture.expected_claim_ids(*persona))[0]


def phi_needles(business_id: str) -> dict[str, str]:
    """The seeded values that must not appear in any log line, by what they are.

    Keyed by description so a failure names the *kind* of leak rather than a
    string. Story 8.2 names four kinds — worker name, ICD-10 code, wage figure,
    note text — and the ICD-10 code gets two entries because the code and its
    description leak independently: a log line that carried `W17.89XA` and one
    that carried "Other fall from one level to another" are the same failure
    arriving through different fields.

    `aww` is rendered as its raw integer (cents) because that is how the value
    exists in the row and in every service that touches it; a formatted figure
    would be testing this file's formatter rather than the log.
    """
    seed = seed_fixture.seed()
    claim = {row["claim_id"]: row for row in seed["claims"]}[business_id]
    employee = {row["employee_id"]: row for row in seed["employees"]}[claim["employee_id"]]
    return {
        "the injured worker's name": str(employee["name"]),
        "the ICD-10 code": str(claim["icd"]),
        "the ICD-10 description": str(claim["icd_desc"]),
        "the average weekly wage": str(claim["aww"]),
        "the contraindications prose": str(claim["contraindications"]),
    }


def assert_no_phi(emitted: str, business_id: str, *extra: str) -> None:
    """Every needle, each with its own message. Shared by both flows."""
    for description, needle in phi_needles(business_id).items():
        assert needle not in emitted, f"{description} reached the log (AD-11): {needle!r}"
    for needle in extra:
        assert needle not in emitted, f"a value written by this test reached the log: {needle!r}"


def as_app_role(url: str, driver: str = "") -> str:
    """`url` rewritten for the application role — `test_purge_cascade.py::as_role`.

    Restated rather than imported for the reason that file gives about its own
    helpers: `render_as_string(hide_password=False)` is load-bearing, because
    SQLAlchemy's `__str__` masks the password and a URL built the obvious way
    authenticates as `lineworker_app` with the literal string `***`.
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
    """App-role runtime URL, owner-role redactor URL — the shipped arrangement."""
    return Settings(
        database_url=as_app_role(seeded_db_url),
        alembic_database_url=seeded_db_url,
        env=Env.e2e,
    )


@pytest.fixture
async def app_db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    """A session on the **application** role, which is what a request runs as."""
    engine = create_async_engine(as_app_role(seeded_db_url, "asyncpg"))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
def owner(seeded_db_url: str) -> Iterator[sa.Engine]:
    """A synchronous owner connection, for seeding rows no shipped command writes."""
    engine = sa.create_engine(
        seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1),
        isolation_level="AUTOCOMMIT",
    )
    try:
        yield engine
    finally:
        engine.dispose()


async def handler_ctx(db: AsyncSession) -> CallerContext:
    """`HANDLER`'s context, resolved the way `api/deps` resolves one."""
    user = (
        await db.scalars(
            sa.select(AppUser).where(AppUser.name == HANDLER[0], AppUser.role == HANDLER[1])
        )
    ).one()
    return CallerContext(
        user_id=user.id,
        role=user.role,
        employer_ids=ALL_EMPLOYERS if user.scope_all else await employer_ids_for(db, user.id),
    )


class NoopThreadDeleter:
    """A `ThreadDeleter` that succeeds and remembers nothing.

    The sweep's *logging* is what this file is about, so the injected deleter
    only has to let the run reach its summary line. `test_purge_cascade.py`
    owns the question of whether the transcripts are really gone.
    """

    async def __call__(self, thread_id: str) -> None:
        return None


async def test_an_audited_claim_edit_logs_field_names_and_no_values(
    app_db: AsyncSession, capfd: pytest.CaptureFixture[str]
) -> None:
    """The write path a handler uses all day, and what its log line may contain.

    `services/audit.record` logs `action`, `entity`, `entity_id`, `actor_id` and
    `fields` — the *names* of the columns that moved. The values are in the
    `before`/`after` diff on the audit row, which is where AD-4 wants them and
    where the Story 8.1 cascade can redact them. That division is the whole
    point, and it is invisible unless somebody checks the bytes: `fields=
    sorted(after)` and `fields=after` are one character apart in the source and
    the second one puts every edited value in the log.

    Both an edit and a diary note, because they are two shapes of the same
    write. The edit moves a claim's own clinical fields; the note is free text
    a handler typed about a person. Nothing in the build is more obviously PHI
    than the second, and nothing is more tempting to log for support purposes.
    """
    configure_logging("INFO")
    business_id = a_claim_of(HANDLER)
    ctx = await handler_ctx(app_db)

    version = (
        await app_db.scalars(sa.select(Claim.version).where(Claim.claim_id == business_id))
    ).one()
    await update_claim_fields(
        app_db,
        ctx,
        business_id,
        expected_version=version,
        patch={"injury_type": EDITED_INJURY_TYPE},
    )
    await create_diary_note(app_db, ctx, note_text=NOTE_TEXT, claim_business_id=business_id)

    emitted = capfd.readouterr().err

    # The positive control, first: without it every assertion below is a
    # statement about a process that may simply not have logged.
    assert "audit.recorded" in emitted, (
        "no audit log line was emitted at all — this test cannot tell a clean log from a silent one"
    )
    # …and what AD-11 *permits*, asserted positively, because "carries ids only"
    # is a claim about what is present as much as about what is not. A story
    # that stripped these to be safe would have made the log useless rather than
    # compliant.
    assert business_id in emitted, "the claim's business id is an identifier and belongs in the log"
    assert "injury_type" in emitted, "the edited column's NAME belongs in the log"

    assert_no_phi(emitted, business_id, EDITED_INJURY_TYPE, NOTE_TEXT)


def _insert_expired_audit_row(owner: sa.Engine, entity_id: str) -> None:
    """One `audit_event` well past the seven-year floor, so the sweep has work.

    Inserted as the owner rather than through `services/audit.record`, because
    the recorder stamps `at` with the clock and this row has to be a decade old.
    Its content is deliberately trivial: what this file needs from the sweep is
    its *summary line*, and a summary line's job is to say how many rows went
    without saying what any of them held.
    """
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO audit_event "
                "(at, actor_id, actor_role, action, entity, entity_id) VALUES "
                "(now() - interval '10 years', (SELECT id FROM app_user LIMIT 1), "
                "'handler', 'test', 'claim', :e)"
            ),
            {"e": entity_id},
        )


def _seed_expired_thread(owner: sa.Engine, business_id: str, user_id: int) -> str:
    """One `copilot_thread` older than the checkpoint floor, with no checkpoints."""
    with owner.connect() as conn:
        claim_pk = conn.execute(
            sa.text("SELECT id FROM claim WHERE claim_id = :b"), {"b": business_id}
        ).scalar_one()
    thread_id = mint_thread_id(business_id, user_id, 1)
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO copilot_thread "
                "(thread_id, claim_id, user_id, conversation_seq, created_at) "
                "VALUES (:t, :pk, :u, 1, :at)"
            ),
            {
                "t": thread_id,
                "pk": claim_pk,
                "u": user_id,
                "at": datetime.now(UTC) - timedelta(days=200),
            },
        )
    return thread_id


async def test_the_retention_sweeps_log_counts_and_no_content(
    app_db: AsyncSession,
    owner: sa.Engine,
    settings: Settings,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The two jobs whose whole purpose is to touch every expired PHI row.

    A retention sweep reads rows it is about to destroy, which makes it the one
    place in the build where "log what we deleted, so we can prove it" is a
    reasonable-sounding instruction with a catastrophic reading. The compliant
    line says how many rows and past which cutoff; the tempting one says which
    rows, and would reconstruct in an operator's log aggregator exactly the
    history the sweep was executing a retention policy to destroy.

    Both sweeps run in one test because they emit two halves of one obligation
    and both must be non-empty for their summaries to exist at all — an empty
    run logs nothing by design (`purge_expired_audit_events`' docstring), so a
    version of this test that seeded no expired rows would assert the absence of
    PHI in a stream containing no retention lines whatsoever.
    """
    business_id = a_claim_of(HANDLER)
    ctx = await handler_ctx(app_db)
    _insert_expired_audit_row(owner, business_id)
    _seed_expired_thread(owner, business_id, ctx.user_id)

    configure_logging("INFO")
    system = await system_context(app_db)
    checkpoints = await purge_expired_checkpoints(
        app_db, system, settings=settings, delete_thread=NoopThreadDeleter()
    )
    audits = await purge_expired_audit_events(app_db, system, settings=settings)

    emitted = capfd.readouterr().err

    assert checkpoints.threads_deleted >= 1, "the checkpoint sweep had nothing to do"
    assert audits.rows_deleted >= 1, "the audit sweep had nothing to do"
    # The positive controls. Both, because one sweep logging correctly says
    # nothing about the other.
    assert "retention.checkpoints_swept" in emitted
    assert "retention.audit_swept" in emitted
    # What the lines legitimately carry: counts and the configured floors.
    assert "threads_deleted" in emitted
    assert "rows_deleted" in emitted

    assert_no_phi(emitted, business_id, EDITED_INJURY_TYPE, NOTE_TEXT)


def test_the_needles_are_real(capfd: pytest.CaptureFixture[str]) -> None:
    """The oracle's own control: a needle that is empty makes every search vacuous.

    `"" not in emitted` is false, so an empty needle would fail loudly — but a
    needle that is one character, or a number that appears in every timestamp,
    would pass every assertion in this file for ever while proving nothing. So
    the seed's values are checked for the property the searches depend on: long
    enough to be distinctive, and actually present in the seed rather than
    `None` rendered as a string.
    """
    needles = phi_needles(a_claim_of(HANDLER))
    assert len(needles) == 5
    for description, needle in needles.items():
        assert needle and needle != "None", f"{description} is empty in the seed"
        assert len(needle) >= 5, f"{description} is {needle!r}, too short to be a real needle"


def test_the_capture_would_notice_a_leaking_line(capfd: pytest.CaptureFixture[str]) -> None:
    """And the capture itself works — the control the two flow tests cannot be.

    `capfd` reads file descriptors, `configure_logging` installs a handler bound
    to `sys.stderr` at construction time, and the two have to agree or every
    "not in emitted" above is a statement about an empty string. Asserted with a
    line that is deliberately not a leak: it logs the needle under an
    allowlisted key so the processor leaves it intact, which is what makes the
    assertion about the *capture* rather than about the block.
    """
    from services.audit import log as audit_log

    configure_logging("INFO")
    audit_log.info("audit.recorded", entity_id=NOTE_TEXT)
    emitted = capfd.readouterr().err
    assert NOTE_TEXT in emitted, (
        "stderr capture is not seeing the log pipeline's output, so every absence "
        "assertion in this file is vacuous"
    )


async def test_no_log_call_in_the_edit_path_uses_a_non_allowlisted_key(
    app_db: AsyncSession, capfd: pytest.CaptureFixture[str]
) -> None:
    """The processor did not have to fire, which is a different claim from "no PHI".

    `_block_unallowlisted` replaces a non-allowlisted value with a marker, so a
    flow whose log line carried `body=<the note>` would emit
    `body="<blocked:not-on-log-allowlist>"` — and every absence assertion above
    would pass. That is the backstop working, and it is exactly the situation
    the lint exists to have prevented before the code ran.

    So the marker's *presence* is asserted against too. A hit here means a log
    call in this flow is passing a keyword the allowlist does not name and the
    static lint has not caught it — a computed keyword name, or one arriving
    through a `**` unpack — and the fix is the same as the lint's: log an id.

    **Both writes, because the test is named for the edit path.** It ran only
    `create_diary_note` at first, which is a real audited write and is not the
    one the name promises: `update_claim_fields` is the flow a handler is in all
    day, it is the flow whose `fields=sorted(after)` is one character from
    `fields=after`, and it is the flow the test above pairs with the note for
    exactly that reason. A backstop assertion that never entered the path it is
    named for is the kind of coverage that reads as more than it is.
    """
    from logging_config import BLOCKED_VALUE

    configure_logging("INFO")
    business_id = a_claim_of(HANDLER)
    ctx = await handler_ctx(app_db)

    version = (
        await app_db.scalars(sa.select(Claim.version).where(Claim.claim_id == business_id))
    ).one()
    await update_claim_fields(
        app_db,
        ctx,
        business_id,
        expected_version=version,
        patch={"injury_type": RE_EDITED_INJURY_TYPE},
    )
    await create_diary_note(app_db, ctx, note_text=NOTE_TEXT, claim_business_id=business_id)

    emitted = capfd.readouterr().err
    assert "audit.recorded" in emitted
    # …and the *edit* is one of the writes that produced it. Without this the
    # test would be green on a no-op patch, which is what a reused value would
    # have made it, and the assertion below would be about the note alone.
    assert "injury_type" in emitted, (
        "the edit path did not audit anything — the patch was a no-op, so this test is "
        "not exercising the path it is named for"
    )
    assert BLOCKED_VALUE not in emitted, (
        "the runtime backstop fired during an audited write, which means a log call "
        "reached the pipeline with a keyword LOG_KEY_ALLOWLIST does not name."
    )


def test_the_settings_fixture_is_the_shipped_two_role_arrangement(settings: Settings) -> None:
    """A guard on this file's own plumbing, not on the code under test.

    The retention sweep's redaction half runs `SET ROLE audit_redactor` on the
    *owner* connection, and the application role cannot do it — its `audit_
    event` grants are INSERT and SELECT by design. If this fixture handed both
    URLs the same role the sweep would refuse before deleting anything, both
    positive controls would be absent, and the failure would read as "the
    retention job logged nothing" rather than as "the test is wired wrong".
    """
    assert settings.database_url != settings.alembic_database_url
    assert "lineworker_app" in settings.database_url


def test_nothing_here_reads_a_value_the_flows_could_have_changed() -> None:
    """`seed_fixture`'s rule, restated where it matters most.

    The needles come from the seed *file*, never from the database, because the
    flows under test write to the database — and an oracle assembled from a
    query the flow just ran can be made to agree with any outcome. The edit
    below changes `injury_type` on the same claim the needles are read for,
    which is precisely the value a database-sourced oracle would have picked up.
    """
    needles = phi_needles(a_claim_of(HANDLER))
    assert EDITED_INJURY_TYPE not in needles.values()
    assert RE_EDITED_INJURY_TYPE not in needles.values()
    assert NOTE_TEXT not in needles.values()


def test_the_seed_fixture_and_the_needles_agree_about_the_claim() -> None:
    """One more vacuity guard: the claim the needles describe is a real seeded one."""
    business_id = a_claim_of(HANDLER)
    assert business_id.startswith("WC-")
    claims: dict[str, Any] = {row["claim_id"]: row for row in seed_fixture.seed()["claims"]}
    assert business_id in claims
