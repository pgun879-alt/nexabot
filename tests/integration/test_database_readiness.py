from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from nexabot.bootstrap.container import CompositeReadinessChecker
from nexabot.infrastructure.database.readiness import DatabaseReadinessChecker


def test_database_readiness_checker_reports_ok_when_reachable() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessionmaker = async_sessionmaker(engine)
        checker = DatabaseReadinessChecker(sessionmaker)

        health = await checker.check()

        assert health.status == "ok"

    asyncio.run(scenario())


def test_database_readiness_checker_reports_error_when_unreachable() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///nonexistent-dir/does-not-exist.db")
        sessionmaker = async_sessionmaker(engine)
        checker = DatabaseReadinessChecker(sessionmaker)

        health = await checker.check()

        assert health.status == "error"
        assert "error" in health.details

    asyncio.run(scenario())


def test_composite_readiness_checker_is_degraded_when_one_component_fails() -> None:
    async def scenario() -> None:
        healthy_engine = create_async_engine("sqlite+aiosqlite://")
        unhealthy_engine = create_async_engine(
            "sqlite+aiosqlite:///nonexistent-dir/does-not-exist.db"
        )
        checker = CompositeReadinessChecker(
            component_checkers={
                "database": DatabaseReadinessChecker(async_sessionmaker(healthy_engine)),
                "cache": DatabaseReadinessChecker(async_sessionmaker(unhealthy_engine)),
            }
        )

        report = await checker.check()

        assert report.status == "degraded"
        assert report.components["database"].status == "ok"
        assert report.components["cache"].status == "error"

    asyncio.run(scenario())
