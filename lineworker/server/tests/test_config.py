"""Settings URL derivation — the single config surface (Story 1.1 AC 4)."""

import pytest

from config import Settings


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
