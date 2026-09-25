"""SQLAlchemy implementation of the UnitOfWork port."""

from __future__ import annotations

import logging
from types import TracebackType

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexabot.infrastructure.database.errors import translate_database_errors
from nexabot.infrastructure.database.repositories.agent import SqlAlchemyAgentRunRepository
from nexabot.infrastructure.database.repositories.conversations import (
    SqlAlchemyConversationRepository,
)
from nexabot.infrastructure.database.repositories.idempotency import (
    SqlAlchemyIdempotencyRepository,
)
from nexabot.infrastructure.database.repositories.identity import SqlAlchemyUserRepository
from nexabot.ports.repositories.agent import AgentRunRepository
from nexabot.ports.repositories.conversations import ConversationRepository
from nexabot.ports.repositories.idempotency import IdempotencyRepository
from nexabot.ports.repositories.identity import UserRepository

logger = logging.getLogger(__name__)


class SqlAlchemyUnitOfWork:
    """One database transaction, exposing repositories bound to it.

    Entering opens a fresh session; a clean exit commits it, an exception
    rolls it back. Each unit of work is short-lived by design so callers can
    interleave transactions with non-database work (an LLM call) instead of
    holding one transaction open across it.
    """

    users: UserRepository
    conversations: ConversationRepository
    agent_runs: AgentRunRepository
    idempotency: IdempotencyRepository

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        session = self._sessionmaker()
        self._session = session
        self.users = SqlAlchemyUserRepository(session)
        self.conversations = SqlAlchemyConversationRepository(session)
        self.agent_runs = SqlAlchemyAgentRunRepository(session)
        self.idempotency = SqlAlchemyIdempotencyRepository(session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        session = self._session
        if session is None:
            return
        try:
            if exc_type is None:
                # Commit is where deferred constraint and connection failures
                # surface; translate them before they reach application code.
                with translate_database_errors(operation="commit"):
                    await session.commit()
            else:
                await self._rollback_quietly(session)
        finally:
            await session.close()
            self._session = None

        if isinstance(exc, SQLAlchemyError):
            # Repositories flush eagerly, so most constraint violations are
            # raised inside the block rather than at commit. Translating here
            # covers every repository at once: re-raising inside the
            # translator converts the error and keeps the original as __cause__.
            with translate_database_errors(operation="transaction"):
                raise exc

    @staticmethod
    async def _rollback_quietly(session: AsyncSession) -> None:
        """Roll back, but never let the rollback replace the original error.

        A transaction is usually unwinding because something already went
        wrong; if the connection is what went wrong, the rollback fails too.
        Letting that propagate would hide the failure the caller actually needs
        to see behind a generic connection error. The session is closed either
        way, so a failed rollback leaks nothing.
        """
        try:
            await session.rollback()
        except SQLAlchemyError:
            logger.warning("unit_of_work.rollback_failed", exc_info=True)


class SqlAlchemyUnitOfWorkFactory:
    """Creates a new SqlAlchemyUnitOfWork per call, bound to one sessionmaker."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    def __call__(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self._sessionmaker)
