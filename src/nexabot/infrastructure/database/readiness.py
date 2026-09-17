"""Database-backed readiness check."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexabot.interfaces.api.health import ComponentHealth


class DatabaseReadinessChecker:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def check(self) -> ComponentHealth:
        try:
            async with self._sessionmaker() as session:
                await session.execute(sa.text("SELECT 1"))
        except Exception as exc:
            # A DB outage must degrade the readiness report, not crash the health endpoint.
            return ComponentHealth(status="error", details={"error": str(exc)})
        return ComponentHealth(status="ok")
