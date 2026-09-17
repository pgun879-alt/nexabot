"""Correlation context for structured logs and traces."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TraceContext:
    request_id: str | None = None
    user_id: str | None = None
    conversation_id: str | None = None
    agent_run_id: str | None = None
    task_id: str | None = None

    def as_log_fields(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "request_id": self.request_id,
                "user_id": self.user_id,
                "conversation_id": self.conversation_id,
                "agent_run_id": self.agent_run_id,
                "task_id": self.task_id,
            }.items()
            if value is not None
        }


_trace_context: ContextVar[TraceContext | None] = ContextVar("nexabot_trace_context", default=None)


def get_trace_context() -> TraceContext:
    return _trace_context.get() or TraceContext()


@contextmanager
def bind_trace_context(context: TraceContext) -> Iterator[None]:
    token = _trace_context.set(context)
    try:
        yield
    finally:
        _trace_context.reset(token)
