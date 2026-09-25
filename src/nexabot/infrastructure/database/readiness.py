"""Database-backed readiness check."""

from __future__ import annotations

import asyncio
import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexabot.interfaces.api.health import ComponentHealth

logger = logging.getLogger(__name__)

DEFAULT_READINESS_TIMEOUT_SECONDS = 2.0
"""How long a readiness probe waits for the database before giving up.

A readiness probe that blocks is worse than one that fails: the orchestrator
learns nothing, the probe's own timeout decides the outcome, and the request
holds a connection the whole time. Short is correct here — a database that
cannot answer ``SELECT 1`` in two seconds is not ready to serve traffic.
"""


class DatabaseReadinessChecker:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        timeout_seconds: float = DEFAULT_READINESS_TIMEOUT_SECONDS,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._timeout_seconds = timeout_seconds

    async def check(self) -> ComponentHealth:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._sessionmaker() as session:
                    await session.execute(sa.text("SELECT 1"))
        except TimeoutError:
            logger.warning("readiness.database_timeout timeout_seconds=%s", self._timeout_seconds)
            return ComponentHealth(status="error", details={"error": "timeout"})
        except Exception:
            # A DB outage must degrade the readiness report, not crash the
            # health endpoint. The exception text stays in the logs: driver
            # errors routinely quote the DSN, and /health/ready is reachable by
            # anything that can reach the process.
            logger.exception("readiness.database_unavailable")
            return ComponentHealth(status="error", details={"error": "unavailable"})
        return ComponentHealth(status="ok")
