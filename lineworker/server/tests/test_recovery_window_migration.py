"""Migration 0013 — the display-string → token conversion, and its coverage.

The conversion is the only thing connecting `weeks_6_8` to the option a
handler picked in the prototype. Three ways it could go wrong and nothing
else would notice: the map could miss a value the seed contains (0013's
`upgrade()` refuses, but only at migration time on a database that has that
value), it could produce a token `RecoveryWindow` does not have, or a member
could be added to the enum with no way to reach it from the source data.

`test_claim_edit_validation.py` asserts the map against the *prototype's*
option list; this file asserts it against the *seeded data* and the enum.
"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

from data.models.enums import RecoveryWindow
from tests import seed_fixture
from tests.conftest import requires_db

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "versions"
    / "20260812_0013_recovery_window_enum.py"
)
_spec = importlib.util.spec_from_file_location("_m0013", _MIGRATION)
assert _spec and _spec.loader
_m0013 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m0013)

TEXT_TO_TOKEN: dict[str, str] = _m0013.TEXT_TO_TOKEN


def test_the_map_covers_every_value_the_seed_contains() -> None:
    """A seeded value the map does not know would abort `upgrade()`.

    Better to fail here, in the suite, than during a migration on somebody's
    database — the refusal in `upgrade()` is the safety net, not the test.
    """
    seeded = {claim["recovery"] for claim in seed_fixture.seed()["claims"]}
    assert seeded, "the seed has no claims"
    assert seeded <= set(TEXT_TO_TOKEN)


def test_the_map_produces_exactly_the_enum() -> None:
    """Every token is a member, and every member is reachable from the data.

    The second half is the one worth having: a member with no source string
    is a value the column can hold that no migration can produce, which is
    how an enum quietly grows a state nothing tests.
    """
    assert set(TEXT_TO_TOKEN.values()) == {member.value for member in RecoveryWindow}


def test_the_map_is_injective() -> None:
    """Two display strings collapsing to one token would silently merge two
    populations of claims — and `downgrade()` could not tell them apart."""
    assert len(set(TEXT_TO_TOKEN.values())) == len(TEXT_TO_TOKEN)


@pytest.mark.parametrize("unknown", ["3-5 Weeks", "", "weeks_4_6"])
def test_an_unmapped_value_is_not_silently_defaulted(unknown: str) -> None:
    """`upgrade()` refuses rather than guessing — asserted on the map because
    the refusal is `not in TEXT_TO_TOKEN`. Note the third case: a value that
    is *already* a token is unmapped too, which is what makes re-running the
    conversion against converted data an error rather than a no-op."""
    assert unknown not in TEXT_TO_TOKEN


@requires_db
def test_the_column_holds_the_enum_after_migration(seeded_db_url: str) -> None:
    """The end state, against a real database: a native enum type, and every
    seeded row converted to a member."""
    engine = sa.create_engine(seeded_db_url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.connect() as conn:
            data_type, udt = conn.execute(
                sa.text(
                    "SELECT data_type, udt_name FROM information_schema.columns "
                    "WHERE table_name = 'claim' AND column_name = 'recovery'"
                )
            ).one()
            stored = {
                row[0] for row in conn.execute(sa.text("SELECT DISTINCT recovery FROM claim"))
            }
    finally:
        engine.dispose()

    assert (data_type, udt) == ("USER-DEFINED", "recovery_window")
    assert stored == {member.value for member in RecoveryWindow}
