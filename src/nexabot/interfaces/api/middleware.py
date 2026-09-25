"""HTTP middleware that binds request-scoped trace context.

``observability.context`` has always defined a ``TraceContext``, but nothing
ever bound one: every log line from an HTTP request was emitted with the
context empty, so the request id fields in the JSON formatter were dead weight.
This is the missing half.
"""

from __future__ import annotations

from uuid import uuid4

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nexabot.observability.context import TraceContext, bind_trace_context

REQUEST_ID_HEADER = "X-Request-ID"

MAX_INBOUND_REQUEST_ID_LENGTH = 128
"""Longest inbound request id that is echoed rather than replaced.

The header is attacker-controlled and ends up in every log line for the
request. Bounding it stops a caller from using it to inflate log volume, and
the value is constrained to printable ASCII for the same reason: a newline in
a request id forges log entries.
"""


def _accept_inbound_request_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    candidate = raw.strip()
    if not candidate or len(candidate) > MAX_INBOUND_REQUEST_ID_LENGTH:
        return None
    if not candidate.isascii() or not candidate.isprintable():
        return None
    return candidate


class TraceContextMiddleware:
    """Assign a request id, bind it for the request, and echo it back.

    Written as raw ASGI rather than ``BaseHTTPMiddleware`` because the latter
    runs the endpoint in a separate task, and a ``ContextVar`` set in the
    middleware would not be visible inside the endpoint.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope)
        request_id = (
            _accept_inbound_request_id(request.headers.get(REQUEST_ID_HEADER)) or uuid4().hex
        )
        scope["state"] = {**scope.get("state", {}), "request_id": request_id}

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.lower().encode(), request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        with bind_trace_context(TraceContext(request_id=request_id)):
            await self._app(scope, receive, send_with_request_id)


def request_id_of(request: Request) -> str | None:
    """Read the current request's id, for handlers that need to report it."""
    return getattr(request.state, "request_id", None)
