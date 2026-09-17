"""Repository adapters are verified against a real SQLAlchemy engine.

Production uses PostgreSQL over asyncpg (see infrastructure.database.session).
These tests substitute an in-memory SQLite engine so the mapping and query
logic can be verified without a running PostgreSQL server; the repositories
themselves are written against standard SQLAlchemy Core/ORM and contain no
PostgreSQL-specific behavior.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nexabot.domain.agent.entities import AgentLimits, AgentRun, AgentRunStatus, AgentStepType
from nexabot.domain.common.ids import new_agent_run_id, new_conversation_id, new_user_id
from nexabot.domain.conversations.entities import Conversation, MessageRole
from nexabot.domain.identity.entities import Permission, Role, TelegramIdentity, User
from nexabot.infrastructure.database import models as _models  # noqa: F401
from nexabot.infrastructure.database.base import Base
from nexabot.infrastructure.database.repositories.agent import SqlAlchemyAgentRunRepository
from nexabot.infrastructure.database.repositories.conversations import (
    SqlAlchemyConversationRepository,
)
from nexabot.infrastructure.database.repositories.idempotency import SqlAlchemyIdempotencyRepository
from nexabot.infrastructure.database.repositories.identity import SqlAlchemyUserRepository
from nexabot.ports.repositories.idempotency import IdempotencyState


async def _make_sessionmaker() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def test_user_repository_round_trips_roles_and_telegram_identity() -> None:
    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        permission = Permission(namespace="tool", action="calculator")
        role = Role(name="member", permissions=frozenset({permission}))
        user = User(id=new_user_id(), roles=frozenset({role}))
        user.attach_telegram_identity(TelegramIdentity(telegram_user_id=555, username="ilya"))

        async with sessionmaker() as session:
            await SqlAlchemyUserRepository(session).add(user)
            await session.commit()

        async with sessionmaker() as session:
            repo = SqlAlchemyUserRepository(session)
            loaded = await repo.get(user.id)
            assert loaded is not None
            assert loaded.telegram_identity == TelegramIdentity(
                telegram_user_id=555, username="ilya"
            )
            assert loaded.has_permission(permission)

            by_telegram = await repo.get_by_telegram_identity(555)
            assert by_telegram is not None
            assert by_telegram.id == user.id

    asyncio.run(scenario())


def test_user_repository_save_updates_status() -> None:
    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        user = User(id=new_user_id())

        async with sessionmaker() as session:
            await SqlAlchemyUserRepository(session).add(user)
            await session.commit()

        user.ban()
        async with sessionmaker() as session:
            await SqlAlchemyUserRepository(session).save(user)
            await session.commit()

        async with sessionmaker() as session:
            loaded = await SqlAlchemyUserRepository(session).get(user.id)
            assert loaded is not None
            assert loaded.status.value == "banned"

    asyncio.run(scenario())


def test_conversation_repository_persists_messages_and_bounds_recent_window() -> None:
    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        owner = User(id=new_user_id())
        conversation = Conversation(id=new_conversation_id(), owner_id=owner.id)

        async with sessionmaker() as session:
            await SqlAlchemyUserRepository(session).add(owner)
            await SqlAlchemyConversationRepository(session).add(conversation)
            await session.commit()

        async with sessionmaker() as session:
            conv_repo = SqlAlchemyConversationRepository(session)
            loaded = await conv_repo.get(conversation.id)
            assert loaded is not None
            first = loaded.append_message(role=MessageRole.USER, content="hello")
            await conv_repo.append_message(first)
            second = loaded.append_message(role=MessageRole.ASSISTANT, content="hi there")
            await conv_repo.append_message(second)
            await session.commit()

        async with sessionmaker() as session:
            conv_repo = SqlAlchemyConversationRepository(session)
            recent = await conv_repo.recent_messages(conversation.id, limit=1)
            assert [message.content for message in recent] == ["hi there"]

            active = await conv_repo.get_active_for_user(owner.id)
            assert active is not None
            assert len(active.messages) == 2

    asyncio.run(scenario())


def test_agent_run_repository_round_trips_limits_and_steps() -> None:
    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        owner = User(id=new_user_id())
        conversation = Conversation(id=new_conversation_id(), owner_id=owner.id)
        run = AgentRun(
            id=new_agent_run_id(),
            user_id=owner.id,
            conversation_id=conversation.id,
            provider="stub",
            model="test-model",
            limits=AgentLimits(
                max_steps=3,
                max_tool_calls=1,
                max_retries=1,
                max_execution_seconds=5,
                max_context_tokens=500,
            ),
        )
        run.start()
        run.record_step(AgentStepType.PLANNING)

        async with sessionmaker() as session:
            await SqlAlchemyUserRepository(session).add(owner)
            await SqlAlchemyConversationRepository(session).add(conversation)
            await SqlAlchemyAgentRunRepository(session).add(run)
            await session.commit()

        run.record_step(AgentStepType.MODEL_CALL)
        run.finish("done", usage_metadata={"total_tokens": 10})

        async with sessionmaker() as session:
            await SqlAlchemyAgentRunRepository(session).save(run)
            await session.commit()

        async with sessionmaker() as session:
            loaded = await SqlAlchemyAgentRunRepository(session).get(run.id)
            assert loaded is not None
            assert loaded.status == AgentRunStatus.SUCCEEDED
            assert loaded.limits.max_steps == 3
            assert loaded.final_response == "done"
            assert [step.type for step in loaded.steps] == [
                AgentStepType.PLANNING,
                AgentStepType.MODEL_CALL,
                AgentStepType.FINAL_RESPONSE,
            ]

    asyncio.run(scenario())


def test_idempotency_repository_start_is_idempotent_and_completes() -> None:
    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        async with sessionmaker() as session:
            repo = SqlAlchemyIdempotencyRepository(session)
            first = await repo.start("agent-run", "key-1")
            second = await repo.start("agent-run", "key-1")
            assert first.state == IdempotencyState.STARTED
            assert second.state == IdempotencyState.STARTED

            # Only the first caller may do the work.
            assert first.owned is True
            assert second.owned is False

            await repo.complete("agent-run", "key-1", {"ok": True})
            await session.commit()

        async with sessionmaker() as session:
            record = await SqlAlchemyIdempotencyRepository(session).start("agent-run", "key-1")
            assert record.state == IdempotencyState.COMPLETED
            assert record.result == {"ok": True}

    asyncio.run(scenario())


def test_expired_idempotency_claim_can_be_reclaimed_exactly_once() -> None:
    """A crashed attempt must not poison its key forever, nor free it twice."""

    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        async with sessionmaker() as session:
            repo = SqlAlchemyIdempotencyRepository(session)
            original = await repo.start("agent-run", "abandoned")
            assert original.owned is True
            await session.commit()

        async with sessionmaker() as session:
            repo = SqlAlchemyIdempotencyRepository(session)

            # A claim still within its TTL stays with its owner.
            assert (await repo.start("agent-run", "abandoned")).owned is False

            # Once expired, exactly one caller takes it over.
            takeover = await repo.start("agent-run", "abandoned", claim_ttl_seconds=0)
            assert takeover.owned is True
            assert takeover.state == IdempotencyState.STARTED

            # The new claim is fresh, so it is not immediately re-stealable.
            assert (await repo.start("agent-run", "abandoned")).owned is False

    asyncio.run(scenario())
