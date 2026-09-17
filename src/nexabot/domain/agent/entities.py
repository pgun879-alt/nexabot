"""Traceable agent run state machine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from nexabot.domain.common.errors import AgentExecutionError, ConflictError, ValidationError
from nexabot.domain.common.ids import (
    AgentRunId,
    AgentStepId,
    ConversationId,
    UserId,
    new_agent_step_id,
)
from nexabot.domain.common.time import utc_now


class AgentRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentStepType(StrEnum):
    PLANNING = "planning"
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    VALIDATION = "validation"
    FINAL_RESPONSE = "final_response"
    ERROR = "error"


TERMINAL_STEP_TYPES = frozenset({AgentStepType.FINAL_RESPONSE, AgentStepType.ERROR})
"""Steps that record how a run ended rather than work it chose to do.

These are exempt from the step budget. Budgets exist to stop a run from
consuming more work than it was granted; refusing the step that closes the run
would strand it in ``RUNNING`` forever and discard an answer the model was
already paid for.
"""


@dataclass(frozen=True, slots=True)
class AgentLimits:
    max_steps: int = 12
    max_tool_calls: int = 6
    max_retries: int = 2
    max_execution_seconds: int = 60
    max_context_tokens: int = 12_000

    def __post_init__(self) -> None:
        values = {
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "max_retries": self.max_retries,
            "max_execution_seconds": self.max_execution_seconds,
            "max_context_tokens": self.max_context_tokens,
        }
        invalid = [name for name, value in values.items() if value <= 0]
        if invalid:
            raise ValidationError("Agent limits must be positive.", invalid_fields=invalid)


@dataclass(frozen=True, slots=True)
class AgentStep:
    id: AgentStepId
    run_id: AgentRunId
    index: int
    type: AgentStepType
    metadata: Mapping[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class AgentRun:
    id: AgentRunId
    user_id: UserId
    conversation_id: ConversationId
    provider: str
    model: str
    limits: AgentLimits = field(default_factory=AgentLimits)
    status: AgentRunStatus = AgentRunStatus.PENDING
    steps: tuple[AgentStep, ...] = field(default_factory=tuple)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_reason: str | None = None
    final_response: str | None = None
    usage_metadata: Mapping[str, object] = field(default_factory=dict)

    def start(self) -> None:
        if self.status != AgentRunStatus.PENDING:
            raise ConflictError(
                "Only pending agent runs can be started.", code="agent_run_not_pending"
            )
        self.status = AgentRunStatus.RUNNING
        self.started_at = utc_now()

    def record_step(
        self,
        step_type: AgentStepType,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> AgentStep:
        self._ensure_running()
        self._ensure_step_budget(step_type)
        step = AgentStep(
            id=new_agent_step_id(),
            run_id=self.id,
            index=len(self.steps) + 1,
            type=step_type,
            metadata=metadata or {},
        )
        self.steps = (*self.steps, step)
        return step

    def finish(
        self, final_response: str, *, usage_metadata: Mapping[str, object] | None = None
    ) -> None:
        self._ensure_running()
        if not final_response.strip():
            raise ValidationError("Final response is required.")
        self.record_step(AgentStepType.FINAL_RESPONSE)
        self.status = AgentRunStatus.SUCCEEDED
        self.final_response = final_response
        self.usage_metadata = usage_metadata or {}
        self.finished_at = utc_now()

    def fail(self, reason: str) -> None:
        if self.status in {
            AgentRunStatus.SUCCEEDED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }:
            raise ConflictError(
                "Terminal agent runs cannot transition again.", code="agent_run_terminal"
            )
        if self.status == AgentRunStatus.RUNNING:
            self.record_step(AgentStepType.ERROR, metadata={"reason": reason})
        self.status = AgentRunStatus.FAILED
        self.failure_reason = reason
        self.finished_at = utc_now()

    @property
    def tool_call_count(self) -> int:
        return sum(1 for step in self.steps if step.type == AgentStepType.TOOL_CALL)

    def _ensure_running(self) -> None:
        if self.status != AgentRunStatus.RUNNING:
            raise ConflictError(
                "Agent run is not running.", code="agent_run_not_running", status=self.status.value
            )

    def _ensure_step_budget(self, step_type: AgentStepType) -> None:
        if step_type in TERMINAL_STEP_TYPES:
            return
        if len(self.steps) >= self.limits.max_steps:
            raise AgentExecutionError(
                "Agent step limit exceeded.", code="agent_step_limit_exceeded"
            )
        if (
            step_type == AgentStepType.TOOL_CALL
            and self.tool_call_count >= self.limits.max_tool_calls
        ):
            raise AgentExecutionError(
                "Agent tool call limit exceeded.", code="agent_tool_limit_exceeded"
            )
