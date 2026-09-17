"""Background task queue port."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from nexabot.domain.common.ids import TaskId


class TaskState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class TaskEnvelope:
    id: TaskId
    type: str
    payload: Mapping[str, Any]
    idempotency_key: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class TaskQueue(Protocol):
    async def enqueue(self, task: TaskEnvelope) -> None:
        """Submit a task for async processing."""

    async def dequeue(self, timeout_seconds: int = 5) -> TaskEnvelope | None:
        """Claim the next task, if available."""

    async def acknowledge(self, task_id: TaskId) -> None:
        """Mark a queued task as handled."""
