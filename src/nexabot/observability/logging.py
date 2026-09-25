"""Structured logging setup."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from nexabot.config.settings import LogFormat
from nexabot.observability.context import get_trace_context

SECRET_FIELD_PATTERN = re.compile(
    r"(token|secret|password|api[_-]?key|authorization)", re.IGNORECASE
)

URL_CREDENTIALS_PATTERN = re.compile(r"(?P<scheme>[a-z][a-z0-9+.\-]*://)(?P<userinfo>[^/\s@]+)@")
"""Credentials embedded in a connection URL inside a log *message*.

Key-based redaction only reaches values the caller passed as structured
fields. Driver exceptions quote the whole DSN inside their message text, which
key matching never sees — so a database outage would write the database
password into the logs. The pattern is deliberately narrow (userinfo before an
``@`` in a URL) to keep it from mangling ordinary prose.
"""


class TraceContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in get_trace_context().as_log_fields().items():
            setattr(record, key, value)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for field in ("request_id", "user_id", "conversation_id", "agent_run_id", "task_id"):
            value = getattr(record, field, None)
            if value:
                payload[field] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(redact(payload), ensure_ascii=False, default=str)


def redact(value: Any) -> Any:
    """Mask secret-looking fields and URL credentials anywhere in a payload."""
    if isinstance(value, dict):
        return {
            key: ("********" if SECRET_FIELD_PATTERN.search(str(key)) else redact(child))
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str):
        return URL_CREDENTIALS_PATTERN.sub(r"\g<scheme>********@", value)
    return value


def configure_logging(*, level: str = "INFO", log_format: LogFormat = LogFormat.CONSOLE) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    handler = logging.StreamHandler()
    handler.addFilter(TraceContextFilter())
    if log_format == LogFormat.JSON:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))

    root.addHandler(handler)
