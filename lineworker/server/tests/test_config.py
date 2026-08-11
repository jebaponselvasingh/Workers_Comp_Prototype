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
