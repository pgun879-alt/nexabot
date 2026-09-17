"""FastAPI application factory.

This module stays free of concrete adapters: callers pass in whatever the
application needs. ``nexabot.bootstrap.main`` is what actually constructs those
dependencies.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from nexabot.config.settings import Settings
from nexabot.domain.common.errors import NexaBotError
from nexabot.interfaces.api.health import ReadinessChecker
from nexabot.interfaces.api.health import router as health_router

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


async def nexabot_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """Map the domain error taxonomy onto HTTP without leaking internals.

    ``to_safe_dict`` is the only payload ever returned: stack traces and raw
    exception text stay in the logs. Starlette types handlers as taking a bare
    ``Exception``, hence the narrowing guard.
    """
    if not isinstance(exc, NexaBotError):
        raise exc
    return JSONResponse(
        status_code=exc.http_status, content=jsonable_encoder(exc.to_safe_dict())
    )


def create_api_app(
    settings: Settings,
    *,
    readiness_checker: ReadinessChecker | None = None,
    lifespan: Lifespan | None = None,
) -> FastAPI:
    kwargs: dict[str, Any] = {"title": settings.app.name}
    if lifespan is not None:
        kwargs["lifespan"] = lifespan

    app = FastAPI(**kwargs)
    app.state.settings = settings
    if readiness_checker is not None:
        app.state.readiness_checker = readiness_checker
    app.add_exception_handler(NexaBotError, nexabot_error_handler)
    app.include_router(health_router)
    return app
