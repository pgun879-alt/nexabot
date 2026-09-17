"""Unit of Work port.

RunAgentTurn must persist state before and after an external LLM call without
holding a database transaction open across that call. The repository ports
alone do not express transaction boundaries, so this port provides the
smallest coordinated-transaction abstraction needed: a bundle of repositories
bound to one persistence transaction, committed together and rolled back
together.
"""

from __future__ import annotations

from types import TracebackType
from typing import Protocol

from nexabot.ports.repositories.agent import AgentRunRepository
from nexabot.ports.repositories.conversations import ConversationRepository
from nexabot.ports.repositories.idempotency import IdempotencyRepository
from nexabot.ports.repositories.identity import UserRepository


class UnitOfWork(Protocol):
    users: UserRepository
    conversations: ConversationRepository
    agent_runs: AgentRunRepository
    idempotency: IdempotencyRepository

    async def __aenter__(self) -> UnitOfWork:
        """Open a transaction and expose session-bound repositories."""

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Commit on clean exit; roll back and release resources otherwise."""


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork:
        """Create a new, not-yet-opened unit of work."""
