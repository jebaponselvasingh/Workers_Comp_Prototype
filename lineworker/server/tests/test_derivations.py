"""Story 1.4 AC 5 — `services/derivations` is the one computer for `risk`.

AD-10's promise is not "the band is computed correctly" but "the band is
computed in one place". Correctness is one test here; the other three are
about the *only*: that the SQL form and the Python form cannot disagree,
that the thresholds come from configuration rather than from the code, and
that no other module has quietly grown its own copy of `>= 65`.
"""

import importlib
import re
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import Settings
from data.models import Claim
from services import derivations
from services.derivations import RiskBand
from services.derivations.registry import Derivation, register
from tests import seed_fixture
from tests.conftest import requires_db

SERVER_ROOT = Path(__file__).resolve().parents[1]


def risk_for(**overrides: int) -> derivations.RiskDerivation:
    return derivations.risk.for_settings(Settings(**overrides))  # type: ignore[arg-type]


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


def test_thresholds_come_from_configuration_not_from_the_code() -> None:
    """AD-8: bands are parameters. Moving them must not need a code change.

    This is what makes the `TODO(JDM)` migration a change of *source* — the
    same two numbers arriving from a ZEN decision table instead of env —
    rather than a hunt through modules for literals.
    """
    strict = risk_for(risk_high_min=90, risk_med_min=80)

    assert strict.of(89) is RiskBand.med
    assert strict.of(90) is RiskBand.high
    assert strict.of(79) is RiskBand.low


def test_an_inverted_band_configuration_is_refused_at_startup() -> None:
    """`med_min > high_min` would leave scores in two bands at once.

    Rejected by the settings model rather than resolved by the derivation,
    so a bad value fails when the process boots instead of silently
    reclassifying a portfolio.
    """
    with pytest.raises(ValueError, match="risk_med_min"):
        Settings(risk_high_min=40, risk_med_min=60)


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


# --- the registry -------------------------------------------------------


def test_risk_is_registered_under_its_canonical_name() -> None:
    assert derivations.get("risk") is derivations.risk
    assert "risk" in derivations.registered_names()


def test_registering_a_second_computer_for_a_name_is_refused() -> None:
    """The registry's entire job (AD-10): a derived value gets one computer.

    A duplicate registration is exactly the drift AD-10 exists to prevent —
    two functions, both plausible, disagreeing in different screens.
    """
    with pytest.raises(ValueError, match="risk"):
        register(Derivation("risk", "a second opinion", lambda _settings: None))


def test_asking_for_an_unregistered_derivation_is_an_error_not_a_none() -> None:
    with pytest.raises(KeyError, match="days_open"):
        derivations.get("days_open")


def test_no_derivation_module_is_named_after_the_value_it_exports() -> None:
    """Regression (code review, 2026-08-10): the package's naming rule.

    `from services.derivations.risk import risk` rebound the package
    attribute `risk` from the submodule to the `Derivation`, so importing
    the module got you the instance and `.RiskBand` raised `AttributeError`.
    Asserted mechanically because the trap only bites the *next* derivation
    — whoever adds `days_open.py` exporting `days_open`.
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

    Deliberately blunt: any bare `65` or `35` in server code outside the
    allowlist fails, and the fix is either to call the derivation or to add
    a justified entry below. A future story that needs 65 for an unrelated
    reason sees this message and makes that choice on purpose.
    """
    allowed = {
        SERVER_ROOT / "config.py",  # where the parameters are declared
        *(SERVER_ROOT / "services" / "derivations").rglob("*.py"),
        *(SERVER_ROOT / "tests").rglob("*.py"),  # tests are the independent oracle
        *(SERVER_ROOT / "data" / "seed").rglob("*.py"),  # the dataset itself
        *(SERVER_ROOT / "data" / "versions").rglob("*.py"),  # frozen migrations
    }
    threshold = re.compile(r"(?<![\w.])(65|35)(?![\w.])")

    offenders = [
        path.relative_to(SERVER_ROOT)
        for path in SERVER_ROOT.rglob("*.py")
        if path not in allowed
        and "__pycache__" not in path.parts
        and ".venv" not in path.parts
        and threshold.search(path.read_text())
    ]
    assert not offenders, (
        f"{offenders} name a risk-band threshold directly — call "
        "services.derivations.risk instead, or justify an allowlist entry"
    )
