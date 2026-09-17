"""Small dependency container for process bootstrap.

This is intentionally minimal. Concrete adapters are added as implementation
phases introduce PostgreSQL sessions, Redis queues, LLM providers, and Telegram.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nexabot.config.settings import Settings
from nexabot.infrastructure.database.readiness import DatabaseReadinessChecker
from nexabot.infrastructure.database.session import create_engine, create_sessionmaker
from nexabot.interfaces.api.health import ComponentHealth, ReadinessReport


def _aggregate_status(components: dict[str, ComponentHealth]) -> str:
    return (
        "ok" if all(component.status == "ok" for component in components.values()) else "degraded"
    )


@dataclass(slots=True)
class StaticReadinessChecker:
    component_statuses: dict[str, ComponentHealth] = field(
        default_factory=lambda: {"application": ComponentHealth(status="ok")}
    )

    async def check(self) -> ReadinessReport:
        return ReadinessReport(
            status=_aggregate_status(self.component_statuses), components=self.component_statuses
        )


class ComponentChecker(Protocol):
    async def check(self) -> ComponentHealth:
        """Check one dependency and report its health."""


@dataclass(slots=True)
class CompositeReadinessChecker:
    component_checkers: dict[str, ComponentChecker]

    async def check(self) -> ReadinessReport:
        components = {
            name: await checker.check() for name, checker in self.component_checkers.items()
        }
        return ReadinessReport(status=_aggregate_status(components), components=components)


class _AlwaysHealthy:
    async def check(self) -> ComponentHealth:
        return ComponentHealth(status="ok")


@dataclass(slots=True)
class Container:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    readiness_checker: CompositeReadinessChecker

    @classmethod
    def from_environment(cls) -> Container:
        settings = Settings.load()
        engine = create_engine(
            settings.database.url,
            pool_size=settings.database.pool_size,
            max_overflow=settings.database.max_overflow,
        )
        sessionmaker = create_sessionmaker(engine)
        readiness_checker = CompositeReadinessChecker(
            component_checkers={
                "application": _AlwaysHealthy(),
                "database": DatabaseReadinessChecker(sessionmaker),
            }
        )
        return cls(
            settings=settings,
            engine=engine,
            sessionmaker=sessionmaker,
            readiness_checker=readiness_checker,
        )
