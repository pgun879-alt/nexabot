"""The HTTP authentication boundary must fail closed.

NexaBot has no HTTP authentication yet (see interfaces/api/security.py). What
these tests protect is the property that makes that safe: a route declaring
``require_actor`` is unreachable until authentication is really wired, rather
than silently open.
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient

from nexabot.config.settings import Settings
from nexabot.domain.common.ids import new_user_id
from nexabot.domain.identity.entities import User, UserStatus
from nexabot.interfaces.api.app import create_api_app
from nexabot.interfaces.api.security import require_actor, set_authenticator


class _FixedAuthenticator:
    def __init__(self, user: User | None) -> None:
        self._user = user

    async def authenticate(self, request: Request) -> User | None:
        return self._user


def _app_with_protected_route(user: User | None, *, wire_authenticator: bool) -> FastAPI:
    router = APIRouter()

    @router.get("/protected")
    async def protected(actor: User = Depends(require_actor)) -> dict[str, str]:  # noqa: B008
        return {"actor": str(actor.id)}

    app = create_api_app(Settings.load({}))
    app.include_router(router)
    if wire_authenticator:
        set_authenticator(app, _FixedAuthenticator(user))
    return app


def test_a_protected_route_is_closed_when_no_authenticator_is_wired() -> None:
    """The current state of the system: protected routes are unreachable."""
    app = _app_with_protected_route(None, wire_authenticator=False)

    response = TestClient(app, raise_server_exceptions=False).get("/protected")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "authentication_not_configured"


def test_an_anonymous_request_is_refused_rather_than_given_a_default_actor() -> None:
    app = _app_with_protected_route(None, wire_authenticator=True)

    response = TestClient(app, raise_server_exceptions=False).get("/protected")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "authentication_required"


def test_an_authenticated_active_user_reaches_the_route() -> None:
    user = User(id=new_user_id())
    app = _app_with_protected_route(user, wire_authenticator=True)

    response = TestClient(app).get("/protected")

    assert response.status_code == 200
    assert response.json() == {"actor": str(user.id)}


@pytest.mark.parametrize("status", [UserStatus.BANNED, UserStatus.DISABLED])
def test_an_inactive_user_is_refused_even_when_authenticated(status: UserStatus) -> None:
    """Authentication answers 'who', not 'may they'."""
    app = _app_with_protected_route(User(id=new_user_id(), status=status), wire_authenticator=True)

    response = TestClient(app, raise_server_exceptions=False).get("/protected")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "user_not_active"


def test_health_endpoints_stay_open() -> None:
    """Liveness must not depend on anything, including authentication."""
    app = _app_with_protected_route(None, wire_authenticator=False)

    assert TestClient(app).get("/health/live").status_code == 200
