"""Request-scoped trace context.

Regression: TraceContext and the JSON formatter's request_id field existed,
but nothing ever bound a context, so every HTTP log line was emitted with the
correlation fields empty.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.testclient import TestClient

from nexabot.config.settings import Settings
from nexabot.interfaces.api.app import create_api_app
from nexabot.interfaces.api.middleware import (
    MAX_INBOUND_REQUEST_ID_LENGTH,
    REQUEST_ID_HEADER,
)
from nexabot.observability.context import get_trace_context
from nexabot.observability.logging import TraceContextFilter


def _client() -> TestClient:
    router = APIRouter()

    @router.get("/echo-trace")
    async def echo_trace() -> dict[str, str | None]:
        return {"request_id": get_trace_context().request_id}

    app = create_api_app(Settings.load({}))
    app.include_router(router)
    return TestClient(app)


def test_every_request_gets_an_id_that_the_endpoint_can_see() -> None:
    response = _client().get("/echo-trace")

    assert response.status_code == 200
    bound = response.json()["request_id"]
    assert bound
    assert response.headers[REQUEST_ID_HEADER] == bound


def test_request_ids_differ_between_requests() -> None:
    client = _client()

    first = client.get("/echo-trace").json()["request_id"]
    second = client.get("/echo-trace").json()["request_id"]

    assert first != second


def test_a_caller_supplied_request_id_is_propagated() -> None:
    response = _client().get("/echo-trace", headers={REQUEST_ID_HEADER: "upstream-trace-42"})

    assert response.json()["request_id"] == "upstream-trace-42"
    assert response.headers[REQUEST_ID_HEADER] == "upstream-trace-42"


def test_an_abusive_request_id_header_is_replaced_not_echoed() -> None:
    """The header is attacker-controlled and lands in every log line."""
    client = _client()

    overlong = client.get(
        "/echo-trace", headers={REQUEST_ID_HEADER: "x" * (MAX_INBOUND_REQUEST_ID_LENGTH + 1)}
    ).json()["request_id"]
    assert len(overlong) < MAX_INBOUND_REQUEST_ID_LENGTH

    # A newline would let a caller forge extra log entries.
    forged = client.get(
        "/echo-trace", headers={REQUEST_ID_HEADER: "ok\nINFO fake log line"}
    ).json()["request_id"]
    assert "fake log line" not in forged


def test_trace_context_does_not_leak_between_requests() -> None:
    client = _client()
    client.get("/echo-trace")

    assert get_trace_context().request_id is None


def test_log_records_emitted_during_a_request_carry_its_id() -> None:
    """The whole point: correlating a log line back to the request."""
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    handler.addFilter(TraceContextFilter())

    router = APIRouter()

    @router.get("/logs-something")
    async def logs_something() -> dict[str, bool]:
        logging.getLogger("nexabot.test.during_request").info("handling")
        return {"ok": True}

    app = create_api_app(Settings.load({}))
    app.include_router(router)

    logger = logging.getLogger("nexabot.test.during_request")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        TestClient(app).get("/logs-something", headers={REQUEST_ID_HEADER: "traced-1"})
    finally:
        logger.removeHandler(handler)

    assert [record.request_id for record in records] == ["traced-1"]  # type: ignore[attr-defined]
