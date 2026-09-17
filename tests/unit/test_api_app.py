"""Tests for the wired API application and the HTTP error boundary."""

from __future__ import annotations

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine

from nexabot.bootstrap.container import (
    CompositeReadinessChecker,
    Container,
    StaticReadinessChecker,
)
from nexabot.bootstrap.main import build_api_app
from nexabot.config.settings import Settings
from nexabot.domain.common.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from nexabot.infrastructure.database.readiness import DatabaseReadinessChecker
from nexabot.infrastructure.database.session import create_sessionmaker
from nexabot.interfaces.api.app import create_api_app


def _sqlite_container() -> tuple[Container, list[str]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessionmaker = create_sessionmaker(engine)
    events: list[str] = []

    @event.listens_for(engine.sync_engine, "engine_disposed")
    def _record_dispose(_: object) -> None:
        events.append("disposed")

    container = Container(
        settings=Settings.load({}),
        engine=engine,
        sessionmaker=sessionmaker,
        readiness_checker=CompositeReadinessChecker(
            component_checkers={"database": DatabaseReadinessChecker(sessionmaker)}
        ),
    )
    return container, events


def test_build_api_app_wires_the_readiness_checker() -> None:
    """Regression: the container's checker used to never reach the app."""
    container, _ = _sqlite_container()

    with TestClient(build_api_app(container)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["components"]["database"]["status"] == "ok"


def test_lifespan_disposes_the_engine_on_shutdown() -> None:
    container, events = _sqlite_container()

    with TestClient(build_api_app(container)) as client:
        client.get("/health/live")
        assert events == []

    assert events == ["disposed"]


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (ValidationError("bad input"), 400, "validation_error"),
        (AuthorizationError(), 403, "forbidden"),
        (NotFoundError("missing"), 404, "not_found"),
        (ConflictError("already there"), 409, "conflict"),
        (RateLimitError("slow down"), 429, "rate_limited"),
    ],
)
def test_domain_errors_map_to_their_http_status(
    error: Exception, expected_status: int, expected_code: str
) -> None:
    """Regression: every NexaBotError used to surface as an opaque 500."""
    router = APIRouter()

    @router.get("/boom")
    async def boom() -> None:
        raise error

    app = create_api_app(Settings.load({}), readiness_checker=StaticReadinessChecker())
    app.include_router(router)

    response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code


def test_error_response_carries_only_safe_fields() -> None:
    router = APIRouter()

    @router.get("/boom")
    async def boom() -> None:
        raise RateLimitError("Too many requests.", retry_after_seconds=30)

    app = create_api_app(Settings.load({}), readiness_checker=StaticReadinessChecker())
    app.include_router(router)

    payload = TestClient(app, raise_server_exceptions=False).get("/boom").json()

    assert payload == {
        "error": {
            "code": "rate_limited",
            "message": "Too many requests.",
            "category": "rate_limit",
            "retryable": True,
            "details": {"retry_after_seconds": 30},
        }
    }
    assert "Traceback" not in str(payload)


def test_unexpected_errors_are_not_dressed_up_as_domain_errors() -> None:
    router = APIRouter()

    @router.get("/boom")
    async def boom() -> None:
        raise RuntimeError("internal detail that must not leak")

    app = create_api_app(Settings.load({}), readiness_checker=StaticReadinessChecker())
    app.include_router(router)

    response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500
    assert "internal detail" not in response.text
