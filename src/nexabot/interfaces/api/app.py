"""FastAPI application factory.

This module stays free of concrete adapters: callers pass in whatever the
application needs. ``nexabot.bootstrap.main`` is what actually constructs those
dependencies.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from nexabot.config.settings import Settings
from nexabot.domain.common.errors import ErrorCategory, NexaBotError
from nexabot.interfaces.api.health import ReadinessChecker
from nexabot.interfaces.api.health import router as health_router
from nexabot.interfaces.api.middleware import TraceContextMiddleware

logger = logging.getLogger(__name__)

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]

_SERVER_SIDE_CATEGORIES = frozenset(
    {
        ErrorCategory.EXTERNAL_SERVICE,
        ErrorCategory.TASK_EXECUTION,
        ErrorCategory.AGENT_EXECUTION,
        ErrorCategory.STORAGE,
        ErrorCategory.CONFIGURATION,
        ErrorCategory.INTERNAL,
    }
)


async def nexabot_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """Map the domain error taxonomy onto HTTP without leaking internals.

    ``to_safe_dict`` is the only payload ever returned: stack traces and raw
    exception text stay in the logs. Starlette types handlers as taking a bare
    ``Exception``, hence the narrowing guard.

    Server-side categories are logged with a traceback because they describe
    something wrong with the system; client-side ones (validation, not found,
    forbidden) are logged at info without one, since a stack trace per bad
    request is noise that buries the failures that matter.
    """
    if not isinstance(exc, NexaBotError):
        raise exc

    if exc.category in _SERVER_SIDE_CATEGORIES:
        logger.exception("api.request_failed code=%s category=%s", exc.code, exc.category.value)
    else:
        logger.info("api.request_rejected code=%s category=%s", exc.code, exc.category.value)

    return JSONResponse(status_code=exc.http_status, content=jsonable_encoder(exc.to_safe_dict()))


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
    # No CORS middleware is installed on purpose: the default (no CORS headers)
    # is what keeps browsers from letting other origins call this API with the
    # caller's credentials. Add it only alongside an explicit allow-list.
    app.add_middleware(TraceContextMiddleware)
    app.add_exception_handler(NexaBotError, nexabot_error_handler)
    app.include_router(health_router)
    return app
