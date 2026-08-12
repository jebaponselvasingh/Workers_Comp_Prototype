"""Story 2.1 — the ZEN JDM tier, against a migrated database (AD-8).

Four things have to hold before any weight is worth trusting:

1. The loader picks the *effective* version of a key — highest version whose
   date has arrived — and refuses rather than defaults when there is none.
2. Both seeded documents evaluate to their typed parameter blocks, with the
   values the story specifies.
3. The committed `rules/documents/*.jdm.json` files and the seeded rows are
   the same bytes, so a reviewer reading the diff is reading what runs.
4. Nothing but a migration can write here — the app role holds SELECT only.

The expected numbers are written out below rather than read from the file
under test. That is the same oracle discipline `seed_fixture` uses: a test
that loaded the document to decide what the document should say would pass
against any document at all.
"""

import json
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from data.models.enums import ClaimStatus, DocType
from rules.engine import LoadedDocument, RuleDocumentMissing, evaluate, load
from rules.parameters import (
    DERIVATION_THRESHOLDS_KEY,
    INTAKE_REQUIRED_DOCUMENTS_KEY,
    PRIORITY_WEIGHTS_KEY,
    DerivationThresholds,
    IntakeRequirements,
    PriorityWeights,
    intake_requirements_for,
    thresholds_for,
    weights_for,
)
from tests.conftest import requires_db

pytestmark = requires_db

DOCUMENTS_DIR = Path(__file__).resolve().parents[1] / "rules" / "documents"

# Every key, the version currently effective, and the file that authored it.
#
# The version column is the part worth stating: Story 2.2 supersedes the
# thresholds with a v2 carrying the treatment-phase parameters, so "the
# seeded documents are all version 1" stopped being true the moment a rule
# was retuned — which is the whole mechanism AD-8 exists for. The filename
# column records the convention that came with it: `<key>.jdm.json` is
# version 1 and every later version is `<key>.v<N>.jdm.json`, because 0009
# reads the unversioned name at migration time and editing it would rewrite
# v1's content on a fresh database.
EFFECTIVE_DOCUMENTS: tuple[tuple[str, int, str], ...] = (
    (DERIVATION_THRESHOLDS_KEY, 2, "derivation_thresholds.v2.jdm.json"),
    (PRIORITY_WEIGHTS_KEY, 1, "priority_weights.jdm.json"),
    (INTAKE_REQUIRED_DOCUMENTS_KEY, 1, "intake_required_documents.jdm.json"),
)

# **Every** seeded (key, version, file), not only the effective ones.
#
# Parametrising the file-equals-row test over `EFFECTIVE_DOCUMENTS` alone
# left `derivation_thresholds.jdm.json` — the v1 document migration 0009
# still reads at migration time — compared against nothing, in the very
# change that introduced the versioned-filename convention to prevent
# exactly that drift (code review, 2026-08-12). A superseded version is
# still authored by a committed file and still seeded on every fresh
# database, so it is still a file that can silently disagree with a row.
SEEDED_DOCUMENTS: tuple[tuple[str, int, str], ...] = (
    (DERIVATION_THRESHOLDS_KEY, 1, "derivation_thresholds.jdm.json"),
    *EFFECTIVE_DOCUMENTS,
)

# The story's seeded values, restated. See the module docstring.
EXPECTED_THRESHOLDS: dict[str, Any] = {
    "riskHighMin": 65,
    "riskMedMin": 35,
    "siuFraudScoreMin": 60,
    "rtwBlockedHashModulus": 5,
    "paymentDueHashModulus": 3,
    # Story 2.2's four, added in version 2.
    "treatmentEarlyMaxRatio": 0.3,
    "treatmentActiveMaxRatio": 0.7,
    "recoveryYearExpectedDays": 180,
    "recoveryDefaultExpectedDays": 42,
}

EXPECTED_INTAKE_REQUIREMENTS: dict[str, Any] = {
    "requiredDocTypes": ["froi", "incident", "medauth", "wage"],
}

EXPECTED_WEIGHTS: dict[str, Any] = {
    "litigation": 40,
    "siuReview": 35,
    "rtwBlocked": 30,
    "pendingApproval": 25,
    # The other half of the same rule element: the weight is what a pending
    # claim is worth, this is which statuses are pending. Both in the
    # document, per AD-8 — a rule element lives in exactly one tier.
    "pendingApprovalStatuses": ["initial", "ch_assessment_process"],
    "paymentDue": 20,
    "surgery": 15,
    "severityFactor": 0.3,
    "daysOpenFactor": 0.2,
    "daysOpenCap": 60,
    "settledPenalty": -100,
    "markerThreshold": 30,
    "markerCount": 3,
    "pageLimit": 50,
}


@pytest.fixture
async def db(seeded_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


# --- loading ------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "version"), [(key, version) for key, version, _file in EFFECTIVE_DOCUMENTS]
)
async def test_the_loader_picks_the_effective_version_of_each_key(
    db: AsyncSession, key: str, version: int
) -> None:
    """ "Highest version whose date has arrived", against the real seed.

    This was "all documents load as version 1" until Story 2.2 superseded
    the thresholds. Pinning the expected version per key is what makes the
    supersession visible here rather than only in the migration: a v3 that
    lands without this table being updated fails loudly instead of quietly
    re-ranking every queue.
    """
    document = await load(db, key)

    assert document.key == key
    assert document.version == version
    assert document.content["nodes"], "the document arrived without its graph"


async def test_version_one_of_a_superseded_document_is_still_exactly_what_it_was(
    db: AsyncSession,
) -> None:
    """Supersession is a new row, never an edit — asserted, not just documented.

    A queue cursor records the thresholds version that ranked it, and the
    story-2.1 ordering is only explainable while v1 still says what it said.
    So v1 must keep its five parameters and *not* have grown v2's four.

    **Addressed by version, not by date** (code review, 2026-08-12). v2 now
    shares v1's effective date, because a version that adds *required*
    parameters cannot be safely future-dated — between the migration and the
    effective date the loader resolves v1, which the typed block then refuses
    for the keys it does not have, 500ing the queue, `/stats/*` and the case
    file alike. The consequence is that **no date selects v1 any more**, which
    is the intended state and the reason this test reads the row directly.
    """
    content = (
        await db.execute(
            sa.text("SELECT content FROM rule_document WHERE key = :key AND version = 1"),
            {"key": DERIVATION_THRESHOLDS_KEY},
        )
    ).scalar_one()
    v1 = LoadedDocument(key=DERIVATION_THRESHOLDS_KEY, version=1, content=content)

    assert evaluate(v1) == {
        "riskHighMin": 65,
        "riskMedMin": 35,
        "siuFraudScoreMin": 60,
        "rtwBlockedHashModulus": 5,
        "paymentDueHashModulus": 3,
    }
    # …and the loader really does prefer v2 on the shared date, which is what
    # closes the window the fix was about.
    assert (await load(db, DERIVATION_THRESHOLDS_KEY, date(2026, 8, 11))).version == 2


async def test_a_missing_key_raises_rather_than_returning_an_empty_block(
    db: AsyncSession,
) -> None:
    """The whole reason `load` raises.

    A silently-absent rule document would score every claim zero: the queue
    would render in claim-id order, look completely normal, and be wrong
    with nothing anywhere to say so.
    """
    with pytest.raises(RuleDocumentMissing, match="reserve_bands"):
        await load(db, "reserve_bands")


async def test_a_version_whose_effective_date_has_not_arrived_is_not_used(
    db: AsyncSession,
) -> None:
    """Effective dating, exercised end to end.

    Version 2 is inserted with a date in the future and must not win until
    that date; asking as of the day it starts must return it. Inserted
    directly rather than by migration because the point is the *loader's*
    date comparison, not another seeding path.
    """
    starts = date(2099, 1, 1)
    await db.execute(
        sa.text(
            "INSERT INTO rule_document (key, version, effective_from, content, created_at) "
            "VALUES (:key, 2, :starts, CAST(:content AS jsonb), now())"
        ),
        {
            "key": PRIORITY_WEIGHTS_KEY,
            "starts": starts,
            "content": json.dumps((await load(db, PRIORITY_WEIGHTS_KEY)).content),
        },
    )

    assert (await load(db, PRIORITY_WEIGHTS_KEY, date(2098, 12, 31))).version == 1
    assert (await load(db, PRIORITY_WEIGHTS_KEY, starts)).version == 2

    await db.rollback()


# --- evaluation into typed blocks ---------------------------------------


async def test_the_thresholds_document_evaluates_to_the_story_values(db: AsyncSession) -> None:
    assert evaluate(await load(db, DERIVATION_THRESHOLDS_KEY)) == EXPECTED_THRESHOLDS


async def test_the_weights_document_evaluates_to_the_story_values(db: AsyncSession) -> None:
    assert evaluate(await load(db, PRIORITY_WEIGHTS_KEY)) == EXPECTED_WEIGHTS


async def test_the_intake_requirements_document_evaluates_to_the_story_values(
    db: AsyncSession,
) -> None:
    assert evaluate(await load(db, INTAKE_REQUIRED_DOCUMENTS_KEY)) == EXPECTED_INTAKE_REQUIREMENTS


async def test_the_typed_blocks_carry_the_evaluated_values(db: AsyncSession) -> None:
    """The JSON→Python boundary, in the direction consumers use it.

    `thresholds_for` / `weights_for` are what every caller reaches for, so
    the snake_case field names and the camelCase document keys are asserted
    against each other here — the one place a rename could go unnoticed.
    """
    thresholds = await thresholds_for(db)
    weights = await weights_for(db)
    requirements = await intake_requirements_for(db)

    assert thresholds == DerivationThresholds(
        version=2,
        risk_high_min=EXPECTED_THRESHOLDS["riskHighMin"],
        risk_med_min=EXPECTED_THRESHOLDS["riskMedMin"],
        siu_fraud_score_min=EXPECTED_THRESHOLDS["siuFraudScoreMin"],
        rtw_blocked_hash_modulus=EXPECTED_THRESHOLDS["rtwBlockedHashModulus"],
        payment_due_hash_modulus=EXPECTED_THRESHOLDS["paymentDueHashModulus"],
        treatment_early_max_ratio=EXPECTED_THRESHOLDS["treatmentEarlyMaxRatio"],
        treatment_active_max_ratio=EXPECTED_THRESHOLDS["treatmentActiveMaxRatio"],
        recovery_year_expected_days=EXPECTED_THRESHOLDS["recoveryYearExpectedDays"],
        recovery_default_expected_days=EXPECTED_THRESHOLDS["recoveryDefaultExpectedDays"],
    )
    assert requirements == IntakeRequirements(
        version=1,
        required_doc_types=tuple(
            DocType(value) for value in EXPECTED_INTAKE_REQUIREMENTS["requiredDocTypes"]
        ),
    )
    assert weights == PriorityWeights(
        version=1,
        litigation=EXPECTED_WEIGHTS["litigation"],
        siu_review=EXPECTED_WEIGHTS["siuReview"],
        rtw_blocked=EXPECTED_WEIGHTS["rtwBlocked"],
        pending_approval=EXPECTED_WEIGHTS["pendingApproval"],
        pending_approval_statuses=frozenset(
            ClaimStatus(value) for value in EXPECTED_WEIGHTS["pendingApprovalStatuses"]
        ),
        payment_due=EXPECTED_WEIGHTS["paymentDue"],
        surgery=EXPECTED_WEIGHTS["surgery"],
        severity_factor=EXPECTED_WEIGHTS["severityFactor"],
        days_open_factor=EXPECTED_WEIGHTS["daysOpenFactor"],
        days_open_cap=EXPECTED_WEIGHTS["daysOpenCap"],
        settled_penalty=EXPECTED_WEIGHTS["settledPenalty"],
        marker_threshold=EXPECTED_WEIGHTS["markerThreshold"],
        marker_count=EXPECTED_WEIGHTS["markerCount"],
        page_limit=EXPECTED_WEIGHTS["pageLimit"],
    )


# --- the file and the row are the same thing ----------------------------


@pytest.mark.parametrize(("key", "version", "filename"), SEEDED_DOCUMENTS)
async def test_the_seeded_row_is_byte_for_byte_the_committed_document(
    db: AsyncSession, key: str, version: int, filename: str
) -> None:
    """What makes reviewing the diff equivalent to reviewing the rule.

    The migration reads these files, so this looks circular — but only until
    someone edits a document *after* the migration has run against a live
    database, which is the exact drift a committed-file-plus-seeded-row
    arrangement invites.

    The filename is a parameter now rather than `f"{key}.jdm.json"`: with
    versions in play, a key no longer names one file, and guessing the name
    would have quietly compared v2's row against v1's file.

    Parametrised over every *seeded* version rather than only the effective
    one — a superseded document is still committed, still seeded, and still
    capable of drifting from its row.
    """
    on_disk: dict[str, Any] = json.loads((DOCUMENTS_DIR / filename).read_text())
    row = (
        await db.execute(
            sa.text("SELECT content FROM rule_document WHERE key = :key AND version = :version"),
            {"key": key, "version": version},
        )
    ).scalar_one()

    assert row == on_disk


# --- AD-4 grants --------------------------------------------------------


async def test_the_app_role_may_read_rule_documents_and_nothing_more(db: AsyncSession) -> None:
    """Rules are authored by migration, never by the application.

    An app role that could rewrite `priority_weights` would be able to
    re-rank every handler's caseload from inside a request — a capability no
    endpoint offers and nobody asked for.
    """
    granted = set(
        (
            await db.scalars(
                sa.text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = 'rule_document' AND grantee = 'lineworker_app'"
                )
            )
        ).all()
    )
    assert granted == {"SELECT"}
