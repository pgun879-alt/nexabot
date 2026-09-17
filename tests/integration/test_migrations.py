"""The migration chain must run, reverse, and agree with the ORM metadata.

Migrations were previously unverifiable: they could only run against
PostgreSQL, and nothing checked that they still described the same schema the
models declare. Drift there is silent until a deploy fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from nexabot.infrastructure.database import models as _models  # noqa: F401
from nexabot.infrastructure.database.base import Base

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def migrated_database(tmp_path: Path) -> Path:
    """A SQLite database built by running the real migration chain."""
    database = tmp_path / "migrations.db"
    upgrade(_alembic_config(database), "head")
    return database


def _alembic_config(database: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    # Migrations run through an async engine, matching production.
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{database}"
    return config


def _inspect_url(database: Path) -> str:
    """Same file, sync driver: SQLAlchemy's inspector is synchronous."""
    return f"sqlite:///{database}"


def test_migration_chain_creates_every_table(migrated_database: Path) -> None:
    engine = create_engine(_inspect_url(migrated_database))
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    expected = set(Base.metadata.tables) | {"alembic_version"}
    assert expected - tables == set(), f"missing tables: {expected - tables}"


def test_migrations_produce_the_schema_the_models_declare(
    migrated_database: Path,
) -> None:
    """Regression: nothing used to catch migration/model drift."""
    engine = create_engine(_inspect_url(migrated_database))
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            differences = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert differences == [], f"schema drift: {differences}"


def test_migration_chain_is_fully_reversible(tmp_path: Path) -> None:
    database = tmp_path / "reversible.db"
    config = _alembic_config(database)

    upgrade(config, "head")
    downgrade(config, "base")

    engine = create_engine(_inspect_url(database))
    try:
        remaining = set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()

    assert remaining == set(), f"downgrade left tables behind: {remaining}"

    # A second upgrade proves downgrade left a genuinely clean slate.
    upgrade(config, "head")
