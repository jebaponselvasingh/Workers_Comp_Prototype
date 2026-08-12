"""Story 1.4 AC 5 / Story 2.1 AC 5 — `services/derivations` is the one
computer for every derived value.

AD-10's promise is not "the band is computed correctly" but "the band is
computed in one place". Correctness is a handful of tests here; the rest are
about the *only*: that the SQL form and the Python form cannot disagree,
that the thresholds come from the rules tier rather than from the code, and
that no other module has quietly grown its own copy of `>= 65`.

Story 2.1 adds `days_open`, `siu_review`, `rtw_blocked` and `payment_due`,
and moves the parameter source from `Settings` to a versioned JDM document.
The band tests below are unchanged in substance — only the tier that owns
the two numbers moved, which is exactly what Story 1.4's `TODO(JDM)`
predicted.
"""

import importlib
import re
from dataclasses import fields, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from data.models import Claim
from data.models.enums import RecoveryWindow, ReturnStatus, Stage
from rules.parameters import DerivationThresholds, RuleParameterError
from services import derivations
from services.derivations import RiskBand
from services.derivations.registry import Derivation, register
from tests import seed_fixture
from tests.conftest import requires_db

SERVER_ROOT = Path(__file__).resolve().parents[1]

# The seeded `derivation_thresholds` document, restated as an independent
# oracle. Same reasoning as `seed_fixture.HIGH_RISK_MIN`: importing the
# numbers from the code under test would make every assertion below agree
# with that code however wrong both were. `tests/test_rules_engine.py` is
# what ties these values back to the committed document.
SEEDED_THRESHOLDS = DerivationThresholds(
    version=3,
    risk_high_min=65,
    risk_med_min=35,
    siu_fraud_score_min=60,
    rtw_blocked_hash_modulus=5,
    payment_due_hash_modulus=3,
    # Story 2.2's four, which arrived with version 2 of the document.
    treatment_early_max_ratio=0.3,
    treatment_active_max_ratio=0.7,
    recovery_year_expected_days=180,
    recovery_default_expected_days=42,
    # Story 2.5's three, which arrived with version 3.
    path_minor_severity_max=35,
    path_minor_recovery_windows=frozenset({RecoveryWindow.weeks_0_2}),
    path_fatality_severity_min=100,
)


def thresholds(**overrides: Any) -> DerivationThresholds:
    """The seeded block, with named parameters replaced.

    `replace` rather than a hand-written argument per field: the per-field
    form had to be extended every time the block grew, and a field somebody
    forgot to thread through would silently ignore an override the test was
    written to exercise.
    """
    unknown = set(overrides) - {field.name for field in fields(DerivationThresholds)}
    assert not unknown, f"no such threshold: {sorted(unknown)}"
    return replace(SEEDED_THRESHOLDS, **overrides)


def risk_for(**overrides: int) -> derivations.RiskDerivation:
    return derivations.risk.for_thresholds(thresholds(**overrides))


# --- the band itself ----------------------------------------------------


@pytest.mark.parametrize(
    ("score", "band"),
    [
        (0, RiskBand.low),
        (34, RiskBand.low),  # boundary: last low
        (35, RiskBand.med),  # boundary: first med
        (64, RiskBand.med),  # boundary: last med
        (65, RiskBand.high),  # boundary: first high
        (100, RiskBand.high),
    ],
)
def test_band_boundaries(score: int, band: RiskBand) -> None:
    assert risk_for().of(score) is band


def test_thresholds_come_from_the_rules_tier_not_from_the_code() -> None:
    """AD-8: bands are parameters. Moving them must not need a code change.

    This is what made the `TODO(JDM)` migration a change of *source* — the
    same two numbers arriving from a versioned JDM document instead of env
    — rather than a hunt through modules for literals. The assertion did
    not change when the source did, which is the point.
    """
    strict = risk_for(risk_high_min=90, risk_med_min=80)

    assert strict.of(89) is RiskBand.med
    assert strict.of(90) is RiskBand.high
    assert strict.of(79) is RiskBand.low


def test_an_inverted_band_configuration_is_refused_when_the_document_is_read() -> None:
    """`med_min > high_min` would leave scores in two bands at once.

    Rejected by the parameter block rather than resolved by the derivation,
    so a bad document fails when it is loaded instead of silently
    reclassifying a portfolio. Story 1.4 made this check at process start,
    against `Settings`; it now happens at the moment the numbers actually
    arrive, which is strictly earlier in their life.
    """
    with pytest.raises(RuleParameterError, match="riskMedMin"):
        thresholds(risk_high_min=40, risk_med_min=60)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("risk_high_min", 1000),
        ("risk_high_min", -1),
        ("risk_med_min", 101),
        ("risk_med_min", -5),
    ],
)
def test_a_band_cut_off_outside_the_severity_range_is_refused(field: str, value: int) -> None:
    """Story 1.4's `Field(ge=0, le=100)`, asserted against its new home.

    `severity_score` is a 0–100 column. A cut-off outside it does not raise
    anything at read time — it bands the entire portfolio into one band and
    keeps working: `riskHighMin: 1000` makes every claim `low`, the top bar
    counts zero high-risk claims, the `high_risk` filter returns nothing,
    and every one of those looks like a quiet week.
    """
    with pytest.raises(RuleParameterError, match="between 0 and 100"):
        thresholds(**{field: value})


# --- one computer, two forms -------------------------------------------


def test_the_sql_form_is_built_from_the_same_thresholds() -> None:
    compiled = str(
        risk_for(risk_high_min=77, risk_med_min=44)
        .sql_is(RiskBand.high)
        .compile(compile_kwargs={"literal_binds": True})
    )
    assert "77" in compiled and "44" in compiled
    assert "65" not in compiled, "the SQL form ignored the configured thresholds"


@requires_db
async def test_the_sql_form_and_the_python_form_agree_on_every_seeded_claim(
    seeded_db_url: str,
) -> None:
    """The reason a derivation may expose SQL at all (Dev Notes).

    Pushing the band into an aggregate query is allowed *only* because the
    predicate is generated from the registered function's thresholds. This
    test is what makes that claim checkable: 100 real severity scores,
    banded both ways, compared row by row.
    """
    engine = create_async_engine(seeded_db_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    risk = risk_for()
    try:
        async with async_sessionmaker(engine)() as db:
            for band in RiskBand:
                by_sql = set(
                    (await db.scalars(sa.select(Claim.claim_id).where(risk.sql_is(band)))).all()
                )
                by_python = {
                    claim["claim_id"]
                    for claim in seed_fixture.seed()["claims"]
                    if risk.of(claim["severity_score"]) is band
                }
                assert by_sql == by_python, f"SQL and Python disagree on the {band} band"
    finally:
        await engine.dispose()


# --- days_open (Story 2.1) ----------------------------------------------


def test_days_open_counts_whole_days_from_the_froi_date() -> None:
    opened = date(2026, 3, 24)
    duration = derivations.days_open.for_thresholds(thresholds())

    assert duration.of(opened, opened) == 0
    assert duration.of(opened, date(2026, 3, 25)) == 1
    assert duration.of(opened, date(2026, 4, 24)) == 31


def test_a_claim_reported_in_the_future_is_zero_days_old_not_negative() -> None:
    """The seeded portfolio runs past today, so this is a real row, not a
    hypothetical. A negative age would subtract from the priority score
    through the days-open term and rank a not-yet-open claim *below* an
    identical open one."""
    duration = derivations.days_open.for_thresholds(thresholds())
    assert duration.of(date(2026, 12, 31), date(2026, 8, 11)) == 0


@given(
    froi=st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 1, 1)),
    offset=st.integers(min_value=-500, max_value=5_000),
    extra=st.integers(min_value=0, max_value=5_000),
)
def test_days_open_is_never_negative_and_never_shrinks_as_time_passes(
    froi: date, offset: int, extra: int
) -> None:
    """Two properties the score depends on (NFR-7).

    Non-negativity keeps the days-open term additive. Monotonicity in
    `as_of` is what makes a claim's rank drift upward as it ages instead of
    oscillating — and it is the property the zero floor could plausibly
    have broken, since a floor is a clamp.
    """
    duration = derivations.days_open.for_thresholds(thresholds())
    earlier = froi + timedelta(days=offset)
    later = earlier + timedelta(days=extra)

    assert duration.of(froi, earlier) >= 0
    assert duration.of(froi, later) >= duration.of(froi, earlier)


# --- the queue's operational flags (Story 2.1) --------------------------


def test_siu_review_needs_both_the_flag_and_the_score() -> None:
    siu = derivations.siu_review.for_thresholds(thresholds())

    assert siu.of(fraud_flag=True, fraud_score=60) is True  # boundary: at the threshold
    assert siu.of(fraud_flag=True, fraud_score=59) is False
    assert siu.of(fraud_flag=False, fraud_score=99) is False


def test_the_siu_threshold_is_a_parameter_not_a_literal() -> None:
    strict = derivations.siu_review.for_thresholds(thresholds(siu_fraud_score_min=90))
    assert strict.of(fraud_flag=True, fraud_score=89) is False
    assert strict.of(fraud_flag=True, fraud_score=90) is True


@pytest.mark.parametrize(
    ("business_id", "bucket"),
    [
        ("WC-20017", 1969813783),
        ("WC-10001", 1968890225),
        ("", 0),
        ("A", 65),
    ],
)
def test_the_hash_bucket_matches_the_prototypes_hash_str(business_id: str, bucket: int) -> None:
    """Values produced by the prototype's `hashStr` in a JavaScript engine.

    Written out rather than recomputed, because the whole point of the port
    is that the two *implementations* agree — generating the expectation
    with the function under test would assert nothing at all. `WC-20017`
    exercises the 32-bit wrap; the empty string and `"A"` pin the two
    degenerate cases the loop could get wrong.
    """
    assert derivations.hash_bucket(business_id) == bucket


def test_rtw_blocked_needs_treatment_and_an_untreated_return_status() -> None:
    blocked = derivations.rtw_blocked.for_thresholds(thresholds())
    high = RiskBand.high

    assert (
        blocked.of(
            claim_id="WC-20017",
            stage=Stage.settled,
            return_status=ReturnStatus.under_treatment,
            risk=high,
        )
        is False
    )
    assert (
        blocked.of(
            claim_id="WC-20017",
            stage=Stage.treatment,
            return_status=ReturnStatus.returned_and_under_therapy,
            risk=high,
        )
        is False
    )
    # In treatment, under treatment, high risk — blocked regardless of the
    # demo hash bucket.
    assert (
        blocked.of(
            claim_id="WC-20017",
            stage=Stage.treatment,
            return_status=ReturnStatus.under_treatment,
            risk=high,
        )
        is True
    )


def test_payment_due_is_the_treatment_stage_outside_the_zero_bucket() -> None:
    due = derivations.payment_due.for_thresholds(thresholds())
    modulus = SEEDED_THRESHOLDS.payment_due_hash_modulus

    in_bucket = next(
        claim["claim_id"]
        for claim in seed_fixture.seed()["claims"]
        if derivations.hash_bucket(claim["claim_id"]) % modulus == 0
    )
    outside = next(
        claim["claim_id"]
        for claim in seed_fixture.seed()["claims"]
        if derivations.hash_bucket(claim["claim_id"]) % modulus != 0
    )

    assert due.of(claim_id=in_bucket, stage=Stage.treatment) is False
    assert due.of(claim_id=outside, stage=Stage.treatment) is True
    assert due.of(claim_id=outside, stage=Stage.intake) is False


def test_every_seeded_claim_agrees_with_the_prototypes_flag_definitions() -> None:
    """The three flags over the whole seeded portfolio, against the rules
    restated from `docs/Workers_Comp_Prototype.html` lines 951-956.

    An oracle rather than an echo: the expressions below are written out
    from the prototype, not imported from the derivations, so a change to
    either side shows up as a disagreement.
    """
    block = thresholds()
    risk = derivations.risk.for_thresholds(block)
    siu = derivations.siu_review.for_thresholds(block)
    blocked = derivations.rtw_blocked.for_thresholds(block)
    due = derivations.payment_due.for_thresholds(block)

    for claim in seed_fixture.seed()["claims"]:
        claim_id = claim["claim_id"]
        bucket = derivations.hash_bucket(claim_id)
        band = risk.of(claim["severity_score"])
        stage = Stage(claim["stage"])
        return_status = ReturnStatus(claim["return_status"])

        assert siu.of(fraud_flag=claim["fraud_flag"], fraud_score=claim["fraud_score"]) == (
            claim["fraud_flag"] and claim["fraud_score"] >= 60
        ), claim_id
        assert blocked.of(
            claim_id=claim_id, stage=stage, return_status=return_status, risk=band
        ) == (
            stage is Stage.treatment
            and return_status is ReturnStatus.under_treatment
            and (bucket % 5 == 0 or band is RiskBand.high)
        ), claim_id
        assert due.of(claim_id=claim_id, stage=stage) == (
            stage is Stage.treatment and bucket % 3 != 0
        ), claim_id


# --- the registry -------------------------------------------------------


def test_risk_is_registered_under_its_canonical_name() -> None:
    assert derivations.get("risk") is derivations.risk
    assert "risk" in derivations.registered_names()


@pytest.mark.parametrize("name", ["days_open", "siu_review", "rtw_blocked", "payment_due"])
def test_the_queue_derivations_are_registered_under_their_canonical_names(name: str) -> None:
    """AC 5: the queue's flags come only from registered functions.

    Registration under the *wire* name is what makes that checkable — the
    detail pane, the dashboard and the copilot's tool registry all ask for
    `payment_due` by string, and one of them getting `None` and computing
    its own is the drift AD-10 exists to prevent.
    """
    assert name in derivations.registered_names()
    assert derivations.get(name) is getattr(derivations, name)


def test_registering_a_second_computer_for_a_name_is_refused() -> None:
    """The registry's entire job (AD-10): a derived value gets one computer.

    A duplicate registration is exactly the drift AD-10 exists to prevent —
    two functions, both plausible, disagreeing in different screens.
    """
    with pytest.raises(ValueError, match="risk"):
        register(Derivation("risk", "a second opinion", lambda _thresholds: None))


def test_asking_for_an_unregistered_derivation_is_an_error_not_a_none() -> None:
    """`total_incurred` is bound by AD-10 and lands with Epic 3's financials.

    Repointed here from `days_open`, which this story registered. The
    assertion is about the registry's refusal to answer `None`, so it needs
    a name AD-10 names and no story has claimed yet.
    """
    with pytest.raises(KeyError, match="total_incurred"):
        derivations.get("total_incurred")


def test_no_derivation_module_is_named_after_the_value_it_exports() -> None:
    """Regression (code review, 2026-08-10): the package's naming rule.

    `from services.derivations.risk import risk` rebound the package
    attribute `risk` from the submodule to the `Derivation`, so importing
    the module got you the instance and `.RiskBand` raised `AttributeError`.
    Asserted mechanically because the trap only bites the *next* derivation
    — which is why Story 2.1's modules are `open_duration` and `queue_flags`
    rather than `days_open` and `payment_due`.
    """
    package = Path(derivations.__file__).parent
    modules = {path.stem for path in package.glob("*.py")} - {"__init__", "registry"}

    for module_name in modules:
        submodule = importlib.import_module(f"services.derivations.{module_name}")
        assert getattr(derivations, module_name, submodule) is submodule, (
            f"services.derivations.{module_name} is shadowed by an export of the same "
            "name — name the module after the rule, not after the derived value"
        )


def test_no_module_outside_the_registry_hardcodes_the_band() -> None:
    """The grep-style regression test Task 2 asks for.

    Deliberately blunt: any bare `65` or `35` outside the allowlist fails,
    and the fix is either to call the derivation or to add a justified entry
    below. A future story that needs 65 for an unrelated reason sees this
    message and makes that choice on purpose.

    Story 2.1 changes what the allowlist says rather than how it works.
    `config.py` is gone from it — the bands are no longer settings — and
    `rules/documents/` takes its place, which is the whole of AD-8's move in
    one line. The scan grew to cover `*.jdm.json` at the same time, so
    "the numbers live in a rule document" is enforced rather than merely
    intended: a JDM file authored anywhere else fails here too.
    """
    allowed = {
        *(SERVER_ROOT / "rules" / "documents").rglob("*.jdm.json"),  # where they live now
        *(SERVER_ROOT / "services" / "derivations").rglob("*.py"),
        *(SERVER_ROOT / "tests").rglob("*.py"),  # tests are the independent oracle
        *(SERVER_ROOT / "data" / "seed").rglob("*.py"),  # the dataset itself
        *(SERVER_ROOT / "data" / "versions").rglob("*.py"),  # frozen migrations
    }
    threshold = re.compile(r"(?<![\w.])(65|35)(?![\w.])")

    offenders = [
        path.relative_to(SERVER_ROOT)
        for pattern in ("*.py", "*.jdm.json")
        for path in SERVER_ROOT.rglob(pattern)
        if path not in allowed
        and "__pycache__" not in path.parts
        and ".venv" not in path.parts
        and threshold.search(path.read_text())
    ]
    assert not offenders, (
        f"{offenders} name a risk-band threshold directly — call "
        "services.derivations.risk instead, or justify an allowlist entry"
    )
