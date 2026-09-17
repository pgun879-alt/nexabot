"""Alembic environment.

NexaBot's ``DATABASE_URL`` uses an async driver (``postgresql+asyncpg``), so
migrations must run through an async engine and hand a sync-style connection to
Alembic via ``run_sync``. Building a synchronous engine over an async driver
fails at the first I/O with ``MissingGreenlet``.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from nexabot.config.settings import Settings
from nexabot.infrastructure.database import models as _models  # noqa: F401
from nexabot.infrastructure.database.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    """Resolve the target database.

    An explicitly supplied URL wins so tests and one-off tooling can point the
    chain at a scratch database without mutating the environment; otherwise the
    normal configuration applies.
    """
    override = config.attributes.get("database_url")
    if override:
        return str(override)
    return Settings.load().database.url


def run_migrations_offline() -> None:
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = get_database_url()

    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
