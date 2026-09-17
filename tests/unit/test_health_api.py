from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from nexabot.bootstrap.container import StaticReadinessChecker
from nexabot.config.settings import Settings
from nexabot.interfaces.api.app import create_api_app
from nexabot.interfaces.api.health import live, ready


def test_health_endpoints() -> None:
    app = create_api_app(Settings.load({}))
    app.state.readiness_checker = StaticReadinessChecker()
    request = SimpleNamespace(app=app)

    assert asyncio.run(live()) == {"status": "ok"}

    report = asyncio.run(ready(request))  # type: ignore[arg-type]
    assert report.status == "ok"


def test_readiness_without_checker_is_degraded() -> None:
    app = create_api_app(Settings.load({}))
    request = SimpleNamespace(app=app)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(ready(request))  # type: ignore[arg-type]

    assert raised.value.status_code == 503
    assert raised.value.detail["status"] == "degraded"
