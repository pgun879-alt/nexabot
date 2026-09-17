"""Explicit domain and application error taxonomy.

Interfaces map these errors to Telegram messages, HTTP responses, or worker
failure states. Raw exception details are deliberately not exposed through
``safe_message``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
    VALIDATION = "validation"
    AUTHORIZATION = "authorization"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMIT = "rate_limit"
    EXTERNAL_SERVICE = "external_service"
    TASK_EXECUTION = "task_execution"
    AGENT_EXECUTION = "agent_execution"
    STORAGE = "storage"
    CONFIGURATION = "configuration"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class NexaBotError(Exception):
    """Base error with stable machine and user-facing fields."""

    code: str
    safe_message: str
    category: ErrorCategory
    retryable: bool = False
    http_status: int = 500
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Exception.__init__(self, self.safe_message)

    def to_safe_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.safe_message,
                "category": self.category.value,
                "retryable": self.retryable,
            }
        }
        if self.details:
            payload["error"]["details"] = dict(self.details)
        return payload


class ValidationError(NexaBotError):
    def __init__(
        self, safe_message: str, *, code: str = "validation_error", **details: Any
    ) -> None:
        super().__init__(
            code=code,
            safe_message=safe_message,
            category=ErrorCategory.VALIDATION,
            retryable=False,
            http_status=400,
            details=details,
        )


class AuthorizationError(NexaBotError):
    def __init__(
        self,
        safe_message: str = "You are not allowed to perform this action.",
        *,
        code: str = "forbidden",
        **details: Any,
    ) -> None:
        super().__init__(
            code=code,
            safe_message=safe_message,
            category=ErrorCategory.AUTHORIZATION,
            retryable=False,
            http_status=403,
            details=details,
        )


class NotFoundError(NexaBotError):
    def __init__(self, safe_message: str, *, code: str = "not_found", **details: Any) -> None:
        super().__init__(
            code=code,
            safe_message=safe_message,
            category=ErrorCategory.NOT_FOUND,
            retryable=False,
            http_status=404,
            details=details,
        )


class ConflictError(NexaBotError):
    def __init__(self, safe_message: str, *, code: str = "conflict", **details: Any) -> None:
        super().__init__(
            code=code,
            safe_message=safe_message,
            category=ErrorCategory.CONFLICT,
            retryable=False,
            http_status=409,
            details=details,
        )


class RateLimitError(NexaBotError):
    def __init__(self, safe_message: str, *, code: str = "rate_limited", **details: Any) -> None:
        super().__init__(
            code=code,
            safe_message=safe_message,
            category=ErrorCategory.RATE_LIMIT,
            retryable=True,
            http_status=429,
            details=details,
        )


class ExternalServiceError(NexaBotError):
    def __init__(
        self,
        safe_message: str,
        *,
        code: str = "external_service_error",
        retryable: bool = True,
        **details: Any,
    ) -> None:
        super().__init__(
            code=code,
            safe_message=safe_message,
            category=ErrorCategory.EXTERNAL_SERVICE,
            retryable=retryable,
            http_status=502,
            details=details,
        )


class TaskExecutionError(NexaBotError):
    def __init__(
        self,
        safe_message: str,
        *,
        code: str = "task_execution_error",
        retryable: bool = False,
        **details: Any,
    ) -> None:
        super().__init__(
            code=code,
            safe_message=safe_message,
            category=ErrorCategory.TASK_EXECUTION,
            retryable=retryable,
            http_status=500,
            details=details,
        )


class AgentExecutionError(NexaBotError):
    def __init__(
        self,
        safe_message: str,
        *,
        code: str = "agent_execution_error",
        retryable: bool = False,
        **details: Any,
    ) -> None:
        super().__init__(
            code=code,
            safe_message=safe_message,
            category=ErrorCategory.AGENT_EXECUTION,
            retryable=retryable,
            http_status=500,
            details=details,
        )


class StorageError(NexaBotError):
    def __init__(
        self,
        safe_message: str,
        *,
        code: str = "storage_error",
        retryable: bool = True,
        **details: Any,
    ) -> None:
        super().__init__(
            code=code,
            safe_message=safe_message,
            category=ErrorCategory.STORAGE,
            retryable=retryable,
            http_status=500,
            details=details,
        )


class ConfigurationError(NexaBotError):
    def __init__(
        self, safe_message: str, *, code: str = "configuration_error", **details: Any
    ) -> None:
        super().__init__(
            code=code,
            safe_message=safe_message,
            category=ErrorCategory.CONFIGURATION,
            retryable=False,
            http_status=500,
            details=details,
        )
