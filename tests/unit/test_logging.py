from __future__ import annotations

from nexabot.observability.context import TraceContext, bind_trace_context, get_trace_context
from nexabot.observability.logging import redact


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


def test_trace_context_is_bound_and_restored() -> None:
    assert get_trace_context().request_id is None

    with bind_trace_context(TraceContext(request_id="req-1", user_id="user-1")):
        assert get_trace_context().as_log_fields() == {"request_id": "req-1", "user_id": "user-1"}

    assert get_trace_context().request_id is None
