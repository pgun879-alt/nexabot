"""SQLAlchemy implementation of the conversation repository port."""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from nexabot.domain.common.errors import NotFoundError
from nexabot.domain.common.ids import ConversationId, MessageId, UserId
from nexabot.domain.conversations.entities import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
)
from nexabot.infrastructure.database.models import ConversationModel, MessageModel


class SqlAlchemyConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, conversation_id: ConversationId) -> Conversation | None:
        row = await self._session.get(ConversationModel, str(conversation_id))
        if row is None:
            return None
        return await self._to_domain(row)

    async def get_active_for_user(self, user_id: UserId) -> Conversation | None:
        row = (
            await self._session.execute(
                sa.select(ConversationModel)
                .where(
                    ConversationModel.owner_id == str(user_id),
                    ConversationModel.status == ConversationStatus.ACTIVE.value,
                )
                .order_by(ConversationModel.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return await self._to_domain(row)

    async def add(self, conversation: Conversation) -> None:
        self._session.add(
            ConversationModel(
                id=str(conversation.id),
                owner_id=str(conversation.owner_id),
                status=conversation.status.value,
                title=conversation.title,
                meta=dict(conversation.metadata),
            )
        )
        await self._session.flush()
        for message in conversation.messages:
            await self.append_message(message)

    async def save(self, conversation: Conversation) -> None:
        row = await self._session.get(ConversationModel, str(conversation.id))
        if row is None:
            raise NotFoundError("Conversation not found.", code="conversation_not_found")
        row.status = conversation.status.value
        row.title = conversation.title
        row.meta = dict(conversation.metadata)

    async def append_message(self, message: Message) -> None:
        self._session.add(
            MessageModel(
                id=str(message.id),
                conversation_id=str(message.conversation_id),
                role=message.role.value,
                content=message.content,
                meta=dict(message.metadata),
            )
        )
        await self._session.flush()

    async def recent_messages(
        self, conversation_id: ConversationId, limit: int
    ) -> tuple[Message, ...]:
        return await self._load_messages(str(conversation_id), limit=limit)

    async def _load_messages(
        self, conversation_id: str, *, limit: int | None = None
    ) -> tuple[Message, ...]:
        stmt = sa.select(MessageModel).where(MessageModel.conversation_id == conversation_id)
        if limit is None:
            stmt = stmt.order_by(MessageModel.created_at.asc())
            rows = (await self._session.execute(stmt)).scalars().all()
        else:
            stmt = stmt.order_by(MessageModel.created_at.desc()).limit(limit)
            rows = list(reversed((await self._session.execute(stmt)).scalars().all()))
        return tuple(_message_to_domain(row) for row in rows)

    async def _to_domain(self, row: ConversationModel) -> Conversation:
        messages = await self._load_messages(row.id)
        return Conversation(
            id=ConversationId(UUID(row.id)),
            owner_id=UserId(UUID(row.owner_id)),
            status=ConversationStatus(row.status),
            title=row.title,
            messages=messages,
            metadata=row.meta,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


def _message_to_domain(row: MessageModel) -> Message:
    return Message(
        id=MessageId(UUID(row.id)),
        conversation_id=ConversationId(UUID(row.conversation_id)),
        role=MessageRole(row.role),
        content=row.content,
        metadata=row.meta,
        created_at=row.created_at,
    )
