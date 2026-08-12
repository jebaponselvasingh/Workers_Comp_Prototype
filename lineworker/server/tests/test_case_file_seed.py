"""Story 2.2 AC 4 — the `timeline_event` and `document` tables in a database.

DB-backed, because everything AC 4 claims is a claim about a database: that
the two tables exist with the right shape, that 556 events and 563 documents
landed under the right claims **in the prototype's order**, that the app role
can reach them, and that the tags and types are the closed sets the code
branches on.

Expectations come from `seed_fixture`, i.e. from `data/seed/case_file_seed.json`
— the same file migration 0011 loads. No count and no description is typed
into this file. `test_case_file_seed_file.py` is what ties that file back to
the prototype; without it, everything here would agree with a wrong file.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import ProgrammingError

from data.models.enums import RUNTIME_ONLY_TIMELINE_TAGS, DocType, TimelineTag
from tests import seed_fixture
from tests.conftest import APP_PASSWORD, requires_db

pytestmark = requires_db


def _sync(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="module")
def owner_engine(seeded_db_url: str) -> sa.Engine:
    return sa.create_engine(_sync(seeded_db_url))


@pytest.fixture(scope="module")
def app_engine(seeded_db_url: str) -> sa.Engine:
    """Connection as the runtime app role, which is what the API uses.

    The owner connection would hide a missing grant entirely — the failure
    would then appear only in the deployed stack, as a 500 on every claim
    detail request.
    """
    url = sa.make_url(_sync(seeded_db_url)).set(username="lineworker_app", password=APP_PASSWORD)
    return sa.create_engine(url)


# Three claims chosen for what their case files actually contain — measured
# against the committed seed, after the first version of this comment got two
# of the three wrong (code review, 2026-08-12):
#
#   WC-20017  treatment, 5 timeline events, carries a `Post-surgery` document
#             (an undated `filed_date`) and is missing `incident`
#   WC-20051  settled, 6 events, carries the undated `Closed` settlement event
#             and is also missing `incident`
#   WC-20289  intake, 4 events, has all four required document types on file —
#             the complete-checklist case
#
# Note what is *not* covered here: no treatment claim in the portfolio exceeds
# the recent-6 window, so the truncating slice cannot be exercised against a
# database at all. `test_claim_detail_assembly.py` covers it over a synthetic
# twelve-event claim, and the story's Debug Log records why.
SAMPLE_CLAIMS = ["WC-20017", "WC-20051", "WC-20289"]


def test_every_seeded_event_and_document_landed(owner_engine: sa.Engine) -> None:
    with owner_engine.connect() as conn:
        events = conn.execute(sa.text("SELECT count(*) FROM timeline_event")).scalar_one()
        documents = conn.execute(sa.text("SELECT count(*) FROM document")).scalar_one()

    assert events == len(seed_fixture.all_timeline_events())
    assert documents == len(seed_fixture.all_documents())


@pytest.mark.parametrize("claim_id", SAMPLE_CLAIMS)
def test_a_claims_timeline_is_stored_in_the_prototypes_order(
    owner_engine: sa.Engine, claim_id: str
) -> None:
    """Order is the whole assertion, and it is `id` order.

    The log has no total order in its own columns — dates repeat within a
    claim and every settlement event has none — so display order is append
    order. The treatment overview renders the *last six* events, so an order
    that drifted would silently show a different six.
    """
    with owner_engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT e.event_date, e.description, e.tag FROM timeline_event e "
                "JOIN claim c ON c.id = e.claim_id WHERE c.claim_id = :cid ORDER BY e.id"
            ),
            {"cid": claim_id},
        ).all()

    expected = seed_fixture.timeline_events_for(claim_id)
    assert [
        {
            "event_date": row.event_date.isoformat() if row.event_date else None,
            "description": row.description,
            "tag": row.tag,
        }
        for row in rows
    ] == [
        {"event_date": e["event_date"], "description": e["description"], "tag": e["tag"]}
        for e in expected
    ]


@pytest.mark.parametrize("claim_id", SAMPLE_CLAIMS)
def test_a_claims_documents_are_stored_in_the_prototypes_order(
    owner_engine: sa.Engine, claim_id: str
) -> None:
    with owner_engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT d.name, d.doc_type, d.filed_date, d.blob_key FROM document d "
                "JOIN claim c ON c.id = d.claim_id WHERE c.claim_id = :cid ORDER BY d.id"
            ),
            {"cid": claim_id},
        ).all()

    expected = seed_fixture.documents_for(claim_id)
    assert [
        {
            "name": row.name,
            "doc_type": row.doc_type,
            "filed_date": row.filed_date.isoformat() if row.filed_date else None,
        }
        for row in rows
    ] == [
        {"name": d["name"], "doc_type": d["doc_type"], "filed_date": d["filed_date"]}
        for d in expected
    ]
    # Seeded rows have no bytes behind them; `blob_key` is the seam the
    # `BlobStore` protocol fills when real files arrive.
    assert all(row.blob_key is None for row in rows)


def test_the_seeded_tags_are_exactly_the_ones_the_code_names(owner_engine: sa.Engine) -> None:
    """`tag` is `Text`, so this test is the constraint the column does not have.

    `TimelineTag` exists because one rule branches on a tag (the settled
    overview finding the settlement event) and a bare string typo there is a
    rule that silently stops firing. The column stays `Text` so eight later
    epics can append their own event kinds without an `ALTER TYPE` each —
    which leaves the seeded set unguarded unless something asserts it.

    **Still an equality, with the runtime-only members subtracted** (Story
    2.3). `TimelineTag.edit` is emitted by the inline-edit command and no
    seeded row carries it, so the obvious repair — relaxing this to a subset
    check — would have quietly stopped catching a *seeded* tag nothing names.
    Naming the exception instead keeps both halves: every seeded tag is one
    the code knows, and every non-runtime member is one the seed produces.
    """
    with owner_engine.connect() as conn:
        tags = {row[0] for row in conn.execute(sa.text("SELECT DISTINCT tag FROM timeline_event"))}

    assert tags == {
        member.value for member in TimelineTag if member not in RUNTIME_ONLY_TIMELINE_TAGS
    }
    assert RUNTIME_ONLY_TIMELINE_TAGS, "the subtraction above must not be a no-op"


def test_the_seeded_doc_types_are_the_whole_enum(owner_engine: sa.Engine) -> None:
    """Every `DocType` member appears, and nothing else can.

    The database enforces the second half (a native enum); this asserts the
    first, so a member Story 2.5 has to map to a chip and a viewer cannot be
    one the seed never produces.
    """
    with owner_engine.connect() as conn:
        types = {row[0] for row in conn.execute(sa.text("SELECT DISTINCT doc_type FROM document"))}

    assert types == {member.value for member in DocType}


def test_undated_rows_are_null_rather_than_a_string(owner_engine: sa.Engine) -> None:
    """The `Closed` / `Post-surgery` ruling, in the database.

    The prototype prints these where a date belongs. A `DATE` column cannot,
    and should not: the honest reading is "not known", and the UI omits the
    clause rather than rendering a non-date.
    """
    with owner_engine.connect() as conn:
        undated_events = conn.execute(
            sa.text("SELECT count(*) FROM timeline_event WHERE event_date IS NULL")
        ).scalar_one()
        undated_documents = conn.execute(
            sa.text("SELECT count(*) FROM document WHERE filed_date IS NULL")
        ).scalar_one()

    assert undated_events == sum(
        1 for event in seed_fixture.all_timeline_events() if event["event_date"] is None
    )
    assert undated_documents == sum(
        1 for document in seed_fixture.all_documents() if document["filed_date"] is None
    )


def test_every_row_hangs_off_a_real_claim(owner_engine: sa.Engine) -> None:
    """The business-id → surrogate resolution the migration performs.

    A silent mis-resolution would attach one claim's history to another, and
    every count in this file would still be right.
    """
    with owner_engine.connect() as conn:
        misfiled = conn.execute(
            sa.text(
                "SELECT count(*) FROM timeline_event e LEFT JOIN claim c ON c.id = e.claim_id "
                "WHERE c.id IS NULL"
            )
        ).scalar_one()
        per_claim: dict[str, int] = {
            row[0]: row[1]
            for row in conn.execute(
                sa.text(
                    "SELECT c.claim_id, count(*) FROM timeline_event e "
                    "JOIN claim c ON c.id = e.claim_id GROUP BY c.claim_id"
                )
            )
        }

    assert misfiled == 0
    expected: dict[str, int] = {}
    for event in seed_fixture.all_timeline_events():
        expected[event["claim_id"]] = expected.get(event["claim_id"], 0) + 1
    assert per_claim == expected


def test_the_app_role_can_read_both_tables(app_engine: sa.Engine) -> None:
    """AD-4's grant surface, exercised as the role the API actually connects as."""
    with app_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM timeline_event")).scalar_one() > 0
        assert conn.execute(sa.text("SELECT count(*) FROM document")).scalar_one() > 0


def test_the_app_role_can_append_a_timeline_event(app_engine: sa.Engine) -> None:
    """Story 2.3's command has to be able to write here — unlike `audit_event`.

    Both tables are append-only in *design*; only `audit_event` is append-only
    by *grant*, because there append-only is a security property. Here it is
    AD-12 — rows are emitted by the owning service's command — enforced by
    where the code lives. Asserting the grant now means 2.3 does not discover
    a missing one at the end of its own implementation.
    """
    with app_engine.begin() as conn:
        claim_id = conn.execute(sa.text("SELECT id FROM claim LIMIT 1")).scalar_one()
        inserted = conn.execute(
            sa.text(
                "INSERT INTO timeline_event (claim_id, event_date, description, tag) "
                "VALUES (:cid, NULL, 'grant probe', 'edit') RETURNING id"
            ),
            {"cid": claim_id},
        ).scalar_one()
        # Rolled back by deleting it here rather than by leaving the
        # transaction open: the module-scoped engine is shared, and a probe
        # left behind would change every count above depending on test order.
        conn.execute(sa.text("DELETE FROM timeline_event WHERE id = :id"), {"id": inserted})


def test_the_app_role_still_cannot_create_tables(app_engine: sa.Engine) -> None:
    """AD-4's no-DDL guarantee, re-asserted after this story's grants.

    `GRANT USAGE, SELECT ON ALL SEQUENCES` is a blunt statement and this
    migration repeats it; the point of the guarantee is that it never widens
    into DDL.
    """
    with (
        app_engine.connect() as conn,
        pytest.raises(ProgrammingError, match="permission denied"),
    ):
        conn.execute(sa.text("CREATE TABLE grant_probe (id int)"))
