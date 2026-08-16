"""Settings URL derivation — the single config surface (Story 1.1 AC 4)."""

import pytest
from pydantic import ValidationError

from config import RETIRED_SETTINGS, Env, Settings


def test_bare_postgresql_url_gets_each_driver() -> None:
    settings = Settings(database_url="postgresql://u:p@h:5432/db")
    assert settings.async_database_url == "postgresql+asyncpg://u:p@h:5432/db"
    assert settings.sync_database_url == "postgresql+psycopg://u:p@h:5432/db"


def test_postgres_alias_is_normalized() -> None:
    """`postgres://` is a common form; it must not reach the engine as-is."""
    settings = Settings(database_url="postgres://u:p@h:5432/db")
    assert settings.async_database_url == "postgresql+asyncpg://u:p@h:5432/db"


def test_already_qualified_driver_is_replaced_not_kept() -> None:
    """An asyncpg URL must still yield psycopg on the sync/Alembic paths."""
    settings = Settings(database_url="postgresql+asyncpg://u:p@h:5432/db")
    assert settings.sync_database_url == "postgresql+psycopg://u:p@h:5432/db"
    assert settings.async_database_url == "postgresql+asyncpg://u:p@h:5432/db"


def test_alembic_url_overrides_database_url() -> None:
    settings = Settings(
        database_url="postgresql://app:p@h:5432/db",
        alembic_database_url="postgresql://owner:o@h:5432/db",
    )
    assert settings.sync_alembic_database_url == "postgresql+psycopg://owner:o@h:5432/db"


def test_non_postgres_backend_is_rejected() -> None:
    settings = Settings(database_url="mysql://u:p@h:3306/db")
    with pytest.raises(ValueError, match="requires postgresql"):
        _ = settings.async_database_url


def test_a_retired_setting_is_refused_rather_than_ignored() -> None:
    """Story 2.1 moved the risk bands into a rule document.

    `extra="ignore"` is the right policy for a stray variable nobody meant,
    and exactly the wrong one for an override an operator set on purpose:
    the value would be dropped in silence and the app would run on the
    default they believed they had replaced. The regression this guards is
    an upgrade that quietly reverts a tuned band to 65/35 — the same silent
    failure `deploy/compose.yaml`'s env_file comment describes.

    Built through `model_validate` rather than as keyword arguments because
    a retired name is no longer a field: passing it as a kwarg is a type
    error, while a dict is exactly the shape the settings sources hand in.
    """
    for name in RETIRED_SETTINGS:
        with pytest.raises(ValidationError, match="retired setting"):
            Settings.model_validate({name: 80})


def test_the_refusal_names_where_the_setting_went() -> None:
    """A refusal that only says "no" makes the operator go looking."""
    with pytest.raises(ValidationError, match="derivation_thresholds rule document"):
        Settings.model_validate({"risk_high_min": 80})


def test_a_retired_name_is_refused_whatever_its_case() -> None:
    """Environment variables arrive upper-cased; the check is on the name."""
    with pytest.raises(ValidationError, match="retired setting"):
        Settings.model_validate({"RISK_MED_MIN": 35})


def test_an_unrelated_unknown_variable_is_still_ignored() -> None:
    """Only *retired* names are refused.

    The environment of a real deployment is full of variables that are none
    of this app's business; refusing those would make `Settings` unusable
    anywhere but a clean room.
    """
    assert Settings.model_validate({"some_other_teams_variable": "whatever"}).env is Env.dev


# --- Story 3.4: the payment batch's cadence -------------------------------


def test_the_default_cadence_is_the_prototypes_tuesdays_and_fridays() -> None:
    """`date.weekday()` is Monday-first, so Tuesday is 1 and Friday is 4.

    Worth an assertion because the prototype counts from a *different* origin:
    JavaScript's `getDay()` is Sunday-first, and its `getDay()===2||===5` names
    the same two days from 2 and 5. Transliterating those integers would have
    shifted the whole schedule by a day.
    """
    assert Settings().payment_batch_weekday_numbers == frozenset({1, 4})


@pytest.mark.parametrize(
    "configured, expected",
    [
        ("mon", {0}),
        ("Mon, Thu", {0, 3}),
        ("monday,thursday", {0, 3}),
        ("sun", {6}),
        ("tue, tue", {1}),
    ],
)
def test_the_cadence_is_parsed_from_day_names(configured: str, expected: set[int]) -> None:
    """Day names rather than integers, because this is a value an operator
    reads and edits: `[1,4]` is a value nobody can check at a glance. Full
    names and abbreviations both work, and a repeat is a set."""
    assert Settings(payment_batch_weekdays=configured).payment_batch_weekday_numbers == frozenset(
        expected
    )


def test_an_unparseable_day_stops_the_process_naming_what_was_typed() -> None:
    """A `list[int]` field would take `[9]` happily and the batch would then
    never run, silently, for ever. This fails at read time with the token."""
    settings = Settings(payment_batch_weekdays="mon,Funday")
    with pytest.raises(ValueError, match="Funday"):
        _ = settings.payment_batch_weekday_numbers


def test_an_empty_cadence_is_refused_rather_than_meaning_never() -> None:
    settings = Settings(payment_batch_weekdays=" , ")
    with pytest.raises(ValueError, match="never run"):
        _ = settings.payment_batch_weekday_numbers


def test_the_scheduler_is_off_under_e2e_and_on_elsewhere() -> None:
    """A determinism property, not a deployment preference: a suite whose stack
    pays invoices on a wall clock cannot assert what a claim's figures are. The
    e2e profile triggers the batch explicitly instead."""
    assert Settings(env=Env.e2e).scheduler_runs is False
    assert Settings(env=Env.dev).scheduler_runs is True
    # An explicit setting still wins in both directions, so a developer can
    # watch the real thing run.
    assert Settings(env=Env.e2e, scheduler_enabled=True).scheduler_runs is True
    assert Settings(env=Env.dev, scheduler_enabled=False).scheduler_runs is False
