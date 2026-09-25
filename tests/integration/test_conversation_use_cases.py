from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexabot.application.conversations.use_cases import (
    AppendMessage,
    ArchiveConversation,
    CreateConversation,
    DeleteConversation,
    LoadRecentContext,
)
from nexabot.domain.common.errors import ConflictError, NotFoundError
from nexabot.domain.common.ids import UserId, new_conversation_id, new_user_id
from nexabot.domain.conversations.entities import MessageRole
from nexabot.domain.identity.entities import User
from nexabot.infrastructure.database.repositories.conversations import (
    SqlAlchemyConversationRepository,
)
from nexabot.infrastructure.database.repositories.identity import SqlAlchemyUserRepository
from tests.support import EngineRegistry, run_scenario


async def _make_owner(sessionmaker: async_sessionmaker[AsyncSession]) -> UserId:
    owner_id = new_user_id()
    async with sessionmaker() as session:
        await SqlAlchemyUserRepository(session).add(User(id=owner_id))
        await session.commit()
    return owner_id


def test_create_conversation_and_append_messages() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        owner_id = await _make_owner(sessionmaker)

        async with sessionmaker() as session:
            conversation = await CreateConversation(
                conversations=SqlAlchemyConversationRepository(session)
            ).execute(owner_id=owner_id, title="support")
            await session.commit()

        async with sessionmaker() as session:
            append = AppendMessage(conversations=SqlAlchemyConversationRepository(session))
            await append.execute(
                conversation_id=conversation.id, role=MessageRole.USER, content="hello"
            )
            await append.execute(
                conversation_id=conversation.id, role=MessageRole.ASSISTANT, content="hi"
            )
            await session.commit()

        async with sessionmaker() as session:
            recent = await LoadRecentContext(
                conversations=SqlAlchemyConversationRepository(session)
            ).execute(conversation.id, limit=1)
            assert [message.content for message in recent] == ["hi"]

    run_scenario(scenario)


def test_append_message_to_unknown_conversation_raises_not_found() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        async with sessionmaker() as session:
            append = AppendMessage(conversations=SqlAlchemyConversationRepository(session))
            with pytest.raises(NotFoundError):
                await append.execute(
                    conversation_id=new_conversation_id(),
                    role=MessageRole.USER,
                    content="hello",
                )

    run_scenario(scenario)


def test_archive_then_delete_conversation() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        owner_id = await _make_owner(sessionmaker)

        async with sessionmaker() as session:
            conversation = await CreateConversation(
                conversations=SqlAlchemyConversationRepository(session)
            ).execute(owner_id=owner_id)
            await session.commit()

        async with sessionmaker() as session:
            repo = SqlAlchemyConversationRepository(session)
            archived = await ArchiveConversation(conversations=repo).execute(conversation.id)
            await session.commit()
            assert archived.status.value == "archived"

        async with sessionmaker() as session:
            append = AppendMessage(conversations=SqlAlchemyConversationRepository(session))
            with pytest.raises(ConflictError):
                await append.execute(
                    conversation_id=conversation.id, role=MessageRole.USER, content="blocked"
                )

        async with sessionmaker() as session:
            repo = SqlAlchemyConversationRepository(session)
            deleted = await DeleteConversation(conversations=repo).execute(conversation.id)
            await session.commit()
            assert deleted.status.value == "deleted"

    run_scenario(scenario)
