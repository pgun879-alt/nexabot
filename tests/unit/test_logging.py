from __future__ import annotations

import logging
import sys

from nexabot.observability.context import TraceContext, bind_trace_context, get_trace_context
from nexabot.observability.logging import JsonFormatter, redact


def test_redact_masks_secret_like_fields() -> None:
    payload = {
        "token": "abc",
        "nested": {"api_key": "secret", "safe": "value"},
        "items": [{"password": "hidden"}],
    }

    assert redact(payload) == {
        "token": "********",
        "nested": {"api_key": "********", "safe": "value"},
        "items": [{"password": "********"}],
    }


def test_redact_strips_credentials_from_urls_inside_message_text() -> None:
    """Regression: key matching never sees a DSN quoted inside an exception.

    Driver errors embed the whole connection URL in their message, so a
    database outage would have written the database password to the logs.
    """
    message = (
        "could not connect to postgresql+asyncpg://nexabot:hunter2@db.internal:5432/nexabot"
    )

    redacted = redact(message)

    assert "hunter2" not in redacted
    assert "nexabot:" not in redacted
    assert "db.internal:5432/nexabot" in redacted


def test_redact_leaves_ordinary_prose_alone() -> None:
    assert redact("agent run failed after 3 attempts") == "agent run failed after 3 attempts"
    assert redact("see https://docs.example/guide") == "see https://docs.example/guide"


def test_json_formatter_redacts_a_credential_in_an_exception() -> None:
    try:
        raise RuntimeError("connect failed: redis://user:s3cr3t@cache.internal:6379/0")
    except RuntimeError:
        record = logging.LogRecord(
            name="nexabot.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="database unavailable",
            args=(),
            exc_info=sys.exc_info(),
        )

    payload = JsonFormatter().format(record)

    assert "s3cr3t" not in payload
    assert "cache.internal:6379" in payload


def test_trace_context_is_bound_and_restored() -> None:
    assert get_trace_context().request_id is None

    with bind_trace_context(TraceContext(request_id="req-1", user_id="user-1")):
        assert get_trace_context().as_log_fields() == {"request_id": "req-1", "user_id": "user-1"}

    assert get_trace_context().request_id is None
