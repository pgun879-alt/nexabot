"""Shared test scaffolding for database-backed scenarios.

Production runs PostgreSQL over asyncpg. These helpers substitute an in-memory
SQLite engine so mapping, query, and transaction behaviour can be verified
without a running PostgreSQL server; the code under test is plain SQLAlchemy
Core/ORM with no PostgreSQL-specific behaviour.

``run_scenario`` exists because every engine an async test creates owns a
connection pool and, under aiosqlite, a worker thread. Letting ``asyncio.run``
close the loop without disposing them leaves those threads calling into a dead
loop, which surfaces as "RuntimeError: Event loop is closed" noise that buries
real failures. Disposing before the loop closes is the fix.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nexabot.infrastructure.database import models as _models  # noqa: F401
from nexabot.infrastructure.database.base import Base


class EngineRegistry:
    """Tracks the engines a scenario creates so they can all be disposed."""

    def __init__(self) -> None:
        self._engines: list[AsyncEngine] = []

    def track(self, engine: AsyncEngine) -> AsyncEngine:
        self._engines.append(engine)
        return engine

    async def engine(self, url: str = "sqlite+aiosqlite://") -> AsyncEngine:
        return self.track(create_async_engine(url))

    async def sessionmaker(
        self, url: str = "sqlite+aiosqlite://"
    ) -> async_sessionmaker[AsyncSession]:
        """A sessionmaker over a fresh engine with the schema already created."""
        engine = await self.engine(url)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        return async_sessionmaker(engine, expire_on_commit=False)

    async def dispose_all(self) -> None:
        for engine in self._engines:
            await engine.dispose()
        self._engines.clear()


def run_scenario(scenario: Callable[[EngineRegistry], Awaitable[None]]) -> None:
    """Run an async scenario, disposing every engine before the loop closes."""
    registry = EngineRegistry()

    async def main() -> None:
        try:
            await scenario(registry)
        finally:
            await registry.dispose_all()

    asyncio.run(main())
