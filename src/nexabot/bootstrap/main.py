"""Process entrypoints.

Bootstrap is the only layer allowed to construct concrete adapters and hand
them to an interface. Everything below this module receives its dependencies.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from nexabot.bootstrap.container import Container
from nexabot.domain.common.errors import ConfigurationError
from nexabot.interfaces.api.app import create_api_app
from nexabot.observability.logging import configure_logging

logger = logging.getLogger(__name__)


def build_api_app(container: Container | None = None) -> FastAPI:
    """Wire a fully configured API application.

    The readiness checker built by the container is passed in here; without
    this wiring ``/health/ready`` would silently report ``degraded`` forever
    because it would never see a checker.
    """
    resolved = Container.from_environment() if container is None else container

    configure_logging(
        level=resolved.settings.app.log_level,
        log_format=resolved.settings.app.log_format,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "api.startup",
            extra={"environment": resolved.settings.app.environment.value},
        )
        try:
            yield
        finally:
            # The engine owns a connection pool; leaking it wedges shutdown.
            await resolved.engine.dispose()
            logger.info("api.shutdown")

    app = create_api_app(
        resolved.settings,
        readiness_checker=resolved.readiness_checker,
        lifespan=lifespan,
    )
    app.state.container = resolved
    return app


def run_api() -> None:
    """Console-script entrypoint: ``nexabot-api``."""
    import uvicorn

    container = Container.from_environment()
    if not container.settings.features.enable_api:
        raise ConfigurationError(
            "The API interface is disabled.",
            code="api_disabled",
            flag="NEXABOT_ENABLE_API",
        )

    uvicorn.run(
        build_api_app(container),
        host=container.settings.api.host,
        port=container.settings.api.port,
        log_config=None,  # configure_logging already owns the root logger
    )


if __name__ == "__main__":  # pragma: no cover
    run_api()
