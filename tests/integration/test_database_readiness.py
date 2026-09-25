from __future__ import annotations

import asyncio
import time
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexabot.bootstrap.container import CompositeReadinessChecker
from nexabot.infrastructure.database.readiness import DatabaseReadinessChecker
from tests.support import EngineRegistry, run_scenario

UNREACHABLE_DATABASE = "sqlite+aiosqlite:///nonexistent-dir/does-not-exist.db"


def test_database_readiness_checker_reports_ok_when_reachable() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        engine = await engines.engine()
        sessionmaker = async_sessionmaker(engine)
        checker = DatabaseReadinessChecker(sessionmaker)

        health = await checker.check()

        assert health.status == "ok"

    run_scenario(scenario)


def test_database_readiness_checker_reports_error_when_unreachable() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        engine = await engines.engine(UNREACHABLE_DATABASE)
        sessionmaker = async_sessionmaker(engine)
        checker = DatabaseReadinessChecker(sessionmaker)

        health = await checker.check()

        assert health.status == "error"
        assert health.details == {"error": "unavailable"}

    run_scenario(scenario)


def test_readiness_details_never_quote_the_driver_error() -> None:
    """Regression: str(exc) was returned to callers of /health/ready.

    Driver errors routinely quote the DSN, and the readiness endpoint is
    reachable by anything that can reach the process.
    """

    async def scenario(engines: EngineRegistry) -> None:
        engine = await engines.engine(
            "sqlite+aiosqlite:///nonexistent-dir/hunter2-secret-path.db"
        )
        health = await DatabaseReadinessChecker(async_sessionmaker(engine)).check()

        assert health.status == "error"
        assert "hunter2" not in str(health.details)
        assert "nonexistent-dir" not in str(health.details)

    run_scenario(scenario)


def test_readiness_gives_up_rather_than_hanging_on_a_wedged_database() -> None:
    """Regression: the check was unbounded, so the probe could block forever."""

    class _NeverAnswers:
        async def __aenter__(self) -> object:
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

        async def __aexit__(self, *_: object) -> None:  # pragma: no cover - never entered
            return None

    async def scenario(_: EngineRegistry) -> None:
        checker = DatabaseReadinessChecker(
            cast("async_sessionmaker[AsyncSession]", lambda: _NeverAnswers()),
            timeout_seconds=0.05,
        )

        started = time.monotonic()
        health = await checker.check()
        elapsed = time.monotonic() - started

        assert health.status == "error"
        assert health.details == {"error": "timeout"}
        assert elapsed < 1.0

    run_scenario(scenario)


def test_composite_readiness_checker_is_degraded_when_one_component_fails() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        healthy_engine = await engines.engine()
        unhealthy_engine = await engines.engine(UNREACHABLE_DATABASE)
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

    run_scenario(scenario)
