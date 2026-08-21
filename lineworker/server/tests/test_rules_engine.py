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

from data.models.enums import ActionKey, ActionUrgency, ClaimStatus, DocType, RecoveryWindow
from rules.engine import LoadedDocument, RuleDocumentMissing, evaluate, load
from rules.parameters import (
    BENEFIT_PARAMS_KEY,
    DERIVATION_THRESHOLDS_KEY,
    HANDLER_PERFORMANCE_KEY,
    INJURY_CAPTURE_KEY,
    INTAKE_REQUIRED_DOCUMENTS_KEY,
    PRIORITY_WEIGHTS_KEY,
    RESERVE_BANDS_KEY,
    WORKLIST_ACTIONS_KEY,
    BenefitParams,
    DerivationThresholds,
    HandlerPerformance,
    IntakeRequirements,
    PriorityWeights,
    ReserveBands,
    WorklistActions,
    benefit_params_for,
    handler_performance_for,
    intake_requirements_for,
    reserve_bands_for,
    thresholds_for,
    urgency_parameter_name,
    weights_for,
    worklist_actions_for,
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
    (DERIVATION_THRESHOLDS_KEY, 6, "derivation_thresholds.v6.jdm.json"),
    (PRIORITY_WEIGHTS_KEY, 1, "priority_weights.jdm.json"),
    (INTAKE_REQUIRED_DOCUMENTS_KEY, 1, "intake_required_documents.jdm.json"),
    # Story 2.4's, missing from this tuple until Story 2.6's review pass found
    # it — see `test_every_committed_rule_document_is_covered_by_this_file`,
    # which is what stops the next one being missed.
    (INJURY_CAPTURE_KEY, 1, "injury_capture.jdm.json"),
    # Story 3.1's, and the first document owned by `services/financials`.
    (BENEFIT_PARAMS_KEY, 1, "benefit_params.jdm.json"),
    # Story 3.2's, the second — the reserve adequacy bands.
    (RESERVE_BANDS_KEY, 1, "reserve_bands.jdm.json"),
    # Story 3.5's, and the first owned by `services/worklist`. Story 5.4
    # supersedes it with a v2 carrying the supervisor worklist's cap and page
    # size — see `SEEDED_DOCUMENTS` for v1, which is still committed and still
    # seeded.
    (WORKLIST_ACTIONS_KEY, 2, "worklist_actions.v2.jdm.json"),
    # Story 5.2's, the second owned by `services/worklist` — and the first whose
    # parameters reach a *registered derivation* from outside
    # `derivation_thresholds`, through `.of()` rather than through `build`.
    (HANDLER_PERFORMANCE_KEY, 1, "handler_performance.jdm.json"),
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
    (DERIVATION_THRESHOLDS_KEY, 2, "derivation_thresholds.v2.jdm.json"),
    (DERIVATION_THRESHOLDS_KEY, 3, "derivation_thresholds.v3.jdm.json"),
    (DERIVATION_THRESHOLDS_KEY, 4, "derivation_thresholds.v4.jdm.json"),
    (DERIVATION_THRESHOLDS_KEY, 5, "derivation_thresholds.v5.jdm.json"),
    # Story 3.5's v1, superseded by Story 5.4's v2 above and still committed:
    # 0031 reads the unversioned filename at migration time, so this row exists
    # on every fresh database and is still a file that can silently disagree
    # with it.
    (WORKLIST_ACTIONS_KEY, 1, "worklist_actions.jdm.json"),
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
    # Story 2.5's three, added in version 3.
    "pathMinorSeverityMax": 35,
    "pathMinorRecoveryWindows": ["weeks_0_2"],
    "pathFatalitySeverityMin": 100,
    # Story 3.1's one, added in version 4. It parameterises `indemnity_type`,
    # which is a registered derivation — the rate it selects lives in
    # `benefit_params` instead, which is the AD-8 split this pair illustrates.
    "ptdSeverityThreshold": 85,
    # Story 5.1's one, added in version 5. It parameterises `fraud_flagged`,
    # the dashboard's fraud REVIEW rule — deliberately a different number from
    # `siuFraudScoreMin` above, which is the queue's *referral* rule over the
    # same column pair. Two rules, two thresholds, and this document is where
    # the difference is visible at a glance.
    "fraudFlagScoreMin": 55,
    # Story 7.1's two, added in version 6. They parameterise `fraud_band`, which
    # bands `fraud_score` **alone** — no `fraud_flag` conjunct — and is therefore
    # a third rule over this column pair rather than a re-spelling of either
    # above. `fraudBandHighMin` carries the same integer as `fraudFlagScoreMin`,
    # and the document is where that coincidence is visible: two rules that agree
    # on today's numbers are still two rules, and only one of them moves when the
    # review population is retuned.
    "fraudBandHighMin": 55,
    "fraudBandMedMin": 35,
}

#: The parameters *added after* v3 and after v2, so the supersession assertion
#: below reads as "this version is the one before it plus what its story added"
#: rather than as a growing tuple of exclusions repeated three times. Written out
#: here because each name is a story's addition and the set is the record of
#: which: `ptdSeverityThreshold` is 3.1's, `fraudFlagScoreMin` 5.1's, and the two
#: band edges 7.1's.
_AFTER_V3: frozenset[str] = frozenset({"fraudFlagScoreMin", "fraudBandHighMin", "fraudBandMedMin"})
_AFTER_V2: frozenset[str] = _AFTER_V3 | {"ptdSeverityThreshold"}

# Story 3.1's document, restated. Rates are BASIS POINTS: 6667 is 66.67%.
EXPECTED_BENEFIT_PARAMS: dict[str, Any] = {
    "defaultCompRateBp": 6667,
    "ptdCompRateBp": 10_000,
    "waitingPeriodDays": 7,
}

# Story 5.2's document, restated. The two deviation bands are whole
# percentages and one of them is negative — faster than the desk is *less* cycle
# time — and the three blend weights are BASIS POINTS over inputs expressed on a
# 0-100 scale: 10000 is one times the average severity, 1000 is 0.10 times the
# surgery percentage, 1500 is 0.15 times the litigation percentage.
#
# `complexityHighMin` is 65 and so is `riskHighMin` above. Restated separately
# rather than shared, because they are different rules over different subjects
# (one claim's severity score against one handler's blended mix) and the
# coincidence is exactly what a later reader would collapse.
EXPECTED_HANDLER_PERFORMANCE: dict[str, Any] = {
    "onTrackDeviationPctMax": -8,
    "attentionDeviationPctMin": 8,
    "severityWeightBp": 10_000,
    "surgeryRateWeightBp": 1_000,
    "litigationRateWeightBp": 1_500,
    "complexityScoreMax": 100,
    "complexityHighMin": 65,
    "complexityMedMin": 40,
}

# Story 3.2's document, restated. Ratios are BASIS POINTS: 11500 is 115%.
EXPECTED_RESERVE_BANDS: dict[str, Any] = {
    "lightRatioBp": 11_500,
    "heavyRatioBp": 6_000,
}

# Story 3.5's document, restated. Thirteen parameters: the cap, the padding
# floor, and one urgency per trigger rule — keyed here by the *document's*
# camelCase names, so a rename on either side of `urgency_parameter_name` shows
# up as a failure rather than as a rule that quietly loses its tuning.
EXPECTED_WORKLIST_ACTIONS: dict[str, Any] = {
    "cap": 6,
    "paddingFloor": 3,
    "urgencyAssessmentApproval": "high",
    "urgencySiuEscalation": "high",
    "urgencyOverdueRtw": "high",
    "urgencySurgicalPreAuth": "high",
    "urgencyBillReview": "medium",
    "urgencyPaymentConfirmation": "medium",
    "urgencyOshaLog": "medium",
    "urgencyDefenseCounsel": "medium",
    "urgencyModifiedDuty": "medium",
    "urgencyDiaryCheckIn": "low",
    "urgencyRoutineReview": "low",
    # Story 5.4's two, added in version 2. They bound the supervisor's priority
    # worklist and nothing on the action checklist reads either — they are in
    # *this* document rather than beside `priority_weights.pageLimit` because
    # that document's version is recorded in every outstanding queue cursor, and
    # superseding it to add a number the queue never reads would invalidate all
    # of them. The page limit is below the cap deliberately, so the seeded book
    # exercises the cursor.
    "supervisorWorklistCap": 30,
    "supervisorWorklistPageLimit": 10,
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


async def test_every_superseded_version_is_still_exactly_what_it_was(
    db: AsyncSession,
) -> None:
    """Supersession is a new row, never an edit — asserted, not just documented.

    A queue cursor records the thresholds version that ranked it, and the
    story-2.1 ordering is only explainable while v1 still says what it said.
    So v1 must keep its five parameters and *not* have grown v2's four or
    v3's three.

    **Both superseded versions, not just v1** (Story 2.5). The single-version
    form of this test was written when there was one superseded document, and
    it would have gone on passing while a v3 quietly rewrote v2 — which is the
    same failure one story later, against a version the treatment-phase banner
    is still explained by. Parametrising over the stack is what keeps the
    assertion about *the rule* rather than about the one row that happened to
    exist when it was written.

    **Addressed by version, not by date** (code review, 2026-08-12). Every
    version shares v1's effective date, because a version that adds *required*
    parameters cannot be safely future-dated — between the migration and the
    effective date the loader resolves the older one, which the typed block
    then refuses for the keys it does not have, 500ing the queue, `/stats/*`
    and the case file alike. The consequence is that **no date selects a
    superseded version any more**, which is the intended state and the reason
    this test reads the rows directly.
    """
    superseded = {
        1: {
            "riskHighMin": 65,
            "riskMedMin": 35,
            "siuFraudScoreMin": 60,
            "rtwBlockedHashModulus": 5,
            "paymentDueHashModulus": 3,
        },
        # v2 is v1 plus the treatment-phase four, and *without* Story 2.5's
        # three: a v3 that had been written as an edit would show up here.
        2: {
            key: value
            for key, value in EXPECTED_THRESHOLDS.items()
            if not key.startswith("path") and key not in _AFTER_V2
        },
        # v3 is v2 plus the path three, and *without* Story 3.1's one — the
        # same assertion one story later, against the version the forms card
        # is still explained by.
        3: {key: value for key, value in EXPECTED_THRESHOLDS.items() if key not in _AFTER_V2},
        # v4 is v3 plus Story 3.1's one, and *without* Story 5.1's — the same
        # assertion two epics later, against the version the seeded benefit
        # figures are still explained by.
        4: {key: value for key, value in EXPECTED_THRESHOLDS.items() if key not in _AFTER_V3},
        # v5 is v4 plus Story 5.1's review threshold, and *without* Story 7.1's
        # band pair — the same assertion an epic later, against the version the
        # supervisor's Fraud Flags card is still explained by.
        5: {
            key: value
            for key, value in EXPECTED_THRESHOLDS.items()
            if key not in ("fraudBandHighMin", "fraudBandMedMin")
        },
    }

    for version, expected in superseded.items():
        content = (
            await db.execute(
                sa.text("SELECT content FROM rule_document WHERE key = :key AND version = :v"),
                {"key": DERIVATION_THRESHOLDS_KEY, "v": version},
            )
        ).scalar_one()
        document = LoadedDocument(key=DERIVATION_THRESHOLDS_KEY, version=version, content=content)
        assert evaluate(document) == expected, f"v{version} was edited rather than superseded"

    # …and the loader really does prefer the newest on the shared date, which
    # is what closes the window the fix was about.
    effective = max(
        version for _key, version, _file in EFFECTIVE_DOCUMENTS if _key == DERIVATION_THRESHOLDS_KEY
    )
    assert (await load(db, DERIVATION_THRESHOLDS_KEY, date(2026, 8, 11))).version == effective


async def test_a_missing_key_raises_rather_than_returning_an_empty_block(
    db: AsyncSession,
) -> None:
    """The whole reason `load` raises.

    A silently-absent rule document would score every claim zero: the queue
    would render in claim-id order, look completely normal, and be wrong
    with nothing anywhere to say so.

    **The key is deliberately one no story will ever seed** (Story 3.2). This
    test used to ask for `reserve_bands` — a document Epic 3 was going to add —
    and it started failing the day 3.2 added it, having quietly stopped
    asserting anything about missing keys some time before that. A placeholder
    that a later story turns real is a test that expires without saying so.
    """
    with pytest.raises(RuleDocumentMissing, match="no_such_rule"):
        await load(db, "no_such_rule")


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


async def test_the_benefit_params_document_evaluates_to_the_story_values(
    db: AsyncSession,
) -> None:
    assert evaluate(await load(db, BENEFIT_PARAMS_KEY)) == EXPECTED_BENEFIT_PARAMS


async def test_the_reserve_bands_document_evaluates_to_the_story_values(
    db: AsyncSession,
) -> None:
    assert evaluate(await load(db, RESERVE_BANDS_KEY)) == EXPECTED_RESERVE_BANDS


async def test_the_worklist_actions_document_evaluates_to_the_story_values(
    db: AsyncSession,
) -> None:
    assert evaluate(await load(db, WORKLIST_ACTIONS_KEY)) == EXPECTED_WORKLIST_ACTIONS


async def test_the_handler_performance_document_evaluates_to_the_story_values(
    db: AsyncSession,
) -> None:
    assert evaluate(await load(db, HANDLER_PERFORMANCE_KEY)) == EXPECTED_HANDLER_PERFORMANCE


async def test_every_trigger_rule_has_an_urgency_in_the_document(db: AsyncSession) -> None:
    """The eleven rules and the eleven parameters are the same eleven.

    Asserted against `ActionKey` rather than against a list written here,
    because the failure this catches is a twelfth rule added in Python with no
    key in the document — which `WorklistActions.of` refuses, but only once
    something loads it. A test that named the eleven itself would have to be
    edited by the same person who forgot the document.
    """
    result = evaluate(await load(db, WORKLIST_ACTIONS_KEY))

    for key in ActionKey:
        name = urgency_parameter_name(key)
        assert name in result, f"{name} is missing from worklist_actions"
        assert ActionUrgency(result[name])


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
        version=6,
        risk_high_min=EXPECTED_THRESHOLDS["riskHighMin"],
        risk_med_min=EXPECTED_THRESHOLDS["riskMedMin"],
        siu_fraud_score_min=EXPECTED_THRESHOLDS["siuFraudScoreMin"],
        rtw_blocked_hash_modulus=EXPECTED_THRESHOLDS["rtwBlockedHashModulus"],
        payment_due_hash_modulus=EXPECTED_THRESHOLDS["paymentDueHashModulus"],
        treatment_early_max_ratio=EXPECTED_THRESHOLDS["treatmentEarlyMaxRatio"],
        treatment_active_max_ratio=EXPECTED_THRESHOLDS["treatmentActiveMaxRatio"],
        recovery_year_expected_days=EXPECTED_THRESHOLDS["recoveryYearExpectedDays"],
        recovery_default_expected_days=EXPECTED_THRESHOLDS["recoveryDefaultExpectedDays"],
        path_minor_severity_max=EXPECTED_THRESHOLDS["pathMinorSeverityMax"],
        path_minor_recovery_windows=frozenset(
            RecoveryWindow(value) for value in EXPECTED_THRESHOLDS["pathMinorRecoveryWindows"]
        ),
        path_fatality_severity_min=EXPECTED_THRESHOLDS["pathFatalitySeverityMin"],
        ptd_severity_threshold=EXPECTED_THRESHOLDS["ptdSeverityThreshold"],
        fraud_flag_score_min=EXPECTED_THRESHOLDS["fraudFlagScoreMin"],
        fraud_band_high_min=EXPECTED_THRESHOLDS["fraudBandHighMin"],
        fraud_band_med_min=EXPECTED_THRESHOLDS["fraudBandMedMin"],
    )
    assert await benefit_params_for(db) == BenefitParams(
        version=1,
        default_comp_rate_bp=EXPECTED_BENEFIT_PARAMS["defaultCompRateBp"],
        ptd_comp_rate_bp=EXPECTED_BENEFIT_PARAMS["ptdCompRateBp"],
        waiting_period_days=EXPECTED_BENEFIT_PARAMS["waitingPeriodDays"],
    )
    assert await reserve_bands_for(db) == ReserveBands(
        version=1,
        light_ratio_bp=EXPECTED_RESERVE_BANDS["lightRatioBp"],
        heavy_ratio_bp=EXPECTED_RESERVE_BANDS["heavyRatioBp"],
    )
    assert await handler_performance_for(db) == HandlerPerformance(
        version=1,
        on_track_deviation_pct_max=EXPECTED_HANDLER_PERFORMANCE["onTrackDeviationPctMax"],
        attention_deviation_pct_min=EXPECTED_HANDLER_PERFORMANCE["attentionDeviationPctMin"],
        severity_weight_bp=EXPECTED_HANDLER_PERFORMANCE["severityWeightBp"],
        surgery_rate_weight_bp=EXPECTED_HANDLER_PERFORMANCE["surgeryRateWeightBp"],
        litigation_rate_weight_bp=EXPECTED_HANDLER_PERFORMANCE["litigationRateWeightBp"],
        complexity_score_max=EXPECTED_HANDLER_PERFORMANCE["complexityScoreMax"],
        complexity_high_min=EXPECTED_HANDLER_PERFORMANCE["complexityHighMin"],
        complexity_med_min=EXPECTED_HANDLER_PERFORMANCE["complexityMedMin"],
    )
    assert await worklist_actions_for(db) == WorklistActions(
        version=2,
        cap=EXPECTED_WORKLIST_ACTIONS["cap"],
        padding_floor=EXPECTED_WORKLIST_ACTIONS["paddingFloor"],
        urgencies={
            key: ActionUrgency(EXPECTED_WORKLIST_ACTIONS[urgency_parameter_name(key)])
            for key in ActionKey
        },
        supervisor_worklist_cap=EXPECTED_WORKLIST_ACTIONS["supervisorWorklistCap"],
        supervisor_worklist_page_limit=EXPECTED_WORKLIST_ACTIONS["supervisorWorklistPageLimit"],
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


async def test_no_rule_document_is_seeded_with_a_future_effective_date(
    db: AsyncSession,
) -> None:
    """Every seeded document is live the moment its migration has run.

    Story 2.6's review pass found `injury_capture` v1 seeded with an effective
    date one day *after* the three documents around it. Migration 0019 already
    states the rule for a superseded version ("a version that adds required
    parameters cannot be safely future-dated — between the migration and the
    effective date the loader resolves the older one"); for a document with no
    predecessor the consequence is worse, because there is nothing older to
    resolve. `injury_capture_for` raises `RuleDocumentMissing`, and it is
    called on the case-file path, so the whole case file 500s — the queue's
    409 bodies included.

    Asserted against the *seeded rows* rather than against the migration
    constants, because the row is what the loader reads. A document dated in
    the future is one nobody can evaluate until a clock catches up, which is
    not a state any migration should be able to leave a fresh database in.
    """
    rows = (
        await db.execute(
            sa.text("SELECT key, version, effective_from FROM rule_document ORDER BY key, version")
        )
    ).all()
    assert rows, "no rule documents are seeded — did the migration chain run?"

    # `date.today()` rather than a frozen date: the assertion is about the
    # database a developer or CI actually migrated, and a document dated after
    # *now* is one that machine cannot evaluate however the calendar moves.
    future = [
        (key, version, effective) for key, version, effective in rows if effective > date.today()
    ]

    assert future == [], (
        "these seeded rule documents are not effective yet, so a fresh database "
        f"cannot evaluate them: {future}"
    )


# --- the file and the row are the same thing ----------------------------


def test_every_committed_rule_document_is_covered_by_this_file() -> None:
    """The tuples above are hand-maintained, so something has to count them.

    Found by Story 2.6's review pass: `injury_capture.jdm.json` had been in
    `rules/documents/` since Story 2.4 and in neither tuple, so the one test
    that makes a committed file and its seeded row the same thing —
    `test_the_seeded_row_is_byte_for_byte_the_committed_document` — did not
    cover it. Retuning `newInjuryDefaultSeverity` on a database where 0016 had
    already run would have left the file and the row disagreeing, the
    add-injury form serving the old default, and the entire gate green.

    A directory listing rather than another hand-written entry, because the
    hand-written entry is what failed. Every `.jdm.json` under
    `rules/documents` is authored by a story, seeded by a migration and
    therefore drift-capable; a file that is deliberately *not* seeded does not
    belong in that directory.

    Filesystem-only, so it runs without a database — the tuple can be wrong
    long before anybody with Postgres notices.
    """
    on_disk = {path.name for path in DOCUMENTS_DIR.glob("*.jdm.json")}
    covered = {filename for _, _, filename in SEEDED_DOCUMENTS}

    assert on_disk - covered == set(), (
        "these rule documents are committed but compared against no seeded row, "
        f"so an edit to one would drift silently: {sorted(on_disk - covered)}"
    )
    assert covered - on_disk == set(), (
        "these documents are expected by the tests and are missing from disk: "
        f"{sorted(covered - on_disk)}"
    )


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
