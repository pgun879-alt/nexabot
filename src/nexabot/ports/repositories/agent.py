"""Agent run repository ports."""

from __future__ import annotations

from typing import Protocol

from nexabot.domain.agent.entities import AgentRun, AgentStep
from nexabot.domain.common.ids import AgentRunId


class AgentRunRepository(Protocol):
    async def get(self, run_id: AgentRunId) -> AgentRun | None:
        """Return an agent run by id."""

    async def add(self, run: AgentRun) -> None:
        """Persist a new run."""

    async def save(self, run: AgentRun) -> None:
        """Persist run state."""

    async def append_step(self, step: AgentStep) -> None:
        """Persist one agent step."""
