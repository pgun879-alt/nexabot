from __future__ import annotations

from nexabot.domain.common.errors import (
    AgentExecutionError,
    AuthorizationError,
    ErrorCategory,
    RateLimitError,
)


def test_application_errors_have_stable_safe_shape() -> None:
    error = AuthorizationError(action="admin.broadcast")

    assert error.category == ErrorCategory.AUTHORIZATION
    assert error.retryable is False
    assert error.http_status == 403
    assert error.to_safe_dict() == {
        "error": {
            "code": "forbidden",
            "message": "You are not allowed to perform this action.",
            "category": "authorization",
            "retryable": False,
            "details": {"action": "admin.broadcast"},
        }
    }


def test_rate_limit_errors_are_retryable() -> None:
    error = RateLimitError("Too many requests.", retry_after_seconds=30)

    assert error.retryable is True
    assert error.http_status == 429
    assert error.details["retry_after_seconds"] == 30


def test_agent_execution_error_can_be_non_retryable() -> None:
    error = AgentExecutionError("Agent step limit exceeded.", code="agent_step_limit_exceeded")

    assert error.retryable is False
    assert error.code == "agent_step_limit_exceeded"
