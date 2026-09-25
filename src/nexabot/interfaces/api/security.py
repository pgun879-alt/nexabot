"""The HTTP authentication boundary.

PRODUCTION BLOCKER: NexaBot has no HTTP authentication. No credential format,
session model, or token issuer has been chosen, and inventing one here would
produce something that *looks* like authentication without being trustworthy —
worse than none, because it invites routes to be written as if they were
protected.

What exists instead is the boundary itself. ``require_actor`` is a FastAPI
dependency that **denies by default**: with no authenticator wired it refuses
every request. A route that declares it is therefore unreachable until real
authentication is implemented, rather than silently open.

Domain permissions do not protect HTTP routes. ``User.has_permission`` answers
"may this user do this?" only once the *which user* question is settled, and
that is this module's job. The only endpoints currently served are
``/health/live`` and ``/health/ready``, neither of which reads user data; there
is no route today that should be calling ``require_actor`` and is not.

Wiring a real implementation means: implement ``Authenticator``, set it on the
app with ``set_authenticator``, and declare ``Depends(require_actor)`` on every
route that touches user-owned data. Ownership checks stay where they already
are — in the application layer, against the resolved actor — because the
authenticated caller and the resource owner are separate questions.
"""

from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import Depends, FastAPI, Request

from nexabot.domain.common.errors import AuthorizationError, ConfigurationError
from nexabot.domain.identity.entities import User

_AUTHENTICATOR_ATTRIBUTE = "nexabot_authenticator"


class Authenticator(Protocol):
    async def authenticate(self, request: Request) -> User | None:
        """Resolve the caller, or return ``None`` when the request is anonymous.

        Implementations must not fall back to a default or system user: an
        unauthenticated request has no actor, and saying otherwise hands every
        anonymous caller someone else's authority.
        """


def set_authenticator(app: FastAPI, authenticator: Authenticator) -> None:
    """Install the authenticator every protected route will use."""
    setattr(app.state, _AUTHENTICATOR_ATTRIBUTE, authenticator)


def get_authenticator(request: Request) -> Authenticator:
    authenticator: Authenticator | None = getattr(
        request.app.state, _AUTHENTICATOR_ATTRIBUTE, None
    )
    if authenticator is None:
        # Fail closed. A route asked for an authenticated actor and the process
        # cannot produce one; serving the request anyway would be the exact
        # silent-bypass this module exists to prevent.
        raise ConfigurationError(
            "Authentication is not configured for this deployment.",
            code="authentication_not_configured",
        )
    return authenticator


async def require_actor(
    request: Request,
    authenticator: Annotated[Authenticator, Depends(get_authenticator)],
) -> User:
    """Resolve the authenticated, active caller or refuse the request."""
    user = await authenticator.authenticate(request)
    if user is None:
        raise AuthorizationError(
            "Authentication is required for this endpoint.", code="authentication_required"
        )
    user.ensure_active()
    return user
