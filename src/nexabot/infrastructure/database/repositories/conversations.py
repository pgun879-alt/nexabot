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
from nexabot.infrastructure.database.base import ensure_utc
from nexabot.infrastructure.database.models import ConversationModel, MessageModel
from nexabot.ports.repositories.conversations import (
    DEFAULT_MESSAGE_WINDOW,
    validate_message_window,
)


class SqlAlchemyConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self,
        conversation_id: ConversationId,
        *,
        message_limit: int = DEFAULT_MESSAGE_WINDOW,
    ) -> Conversation | None:
        row = await self._session.get(ConversationModel, str(conversation_id))
        if row is None:
            return None
        return await self._to_domain(row, message_limit=message_limit)

    async def get_active_for_user(
        self,
        user_id: UserId,
        *,
        message_limit: int = DEFAULT_MESSAGE_WINDOW,
    ) -> Conversation | None:
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
        return await self._to_domain(row, message_limit=message_limit)

    async def add(self, conversation: Conversation) -> None:
        self._session.add(
            ConversationModel(
                id=str(conversation.id),
                owner_id=str(conversation.owner_id),
                status=conversation.status.value,
                title=conversation.title,
                meta=dict(conversation.metadata),
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
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
        row.updated_at = conversation.updated_at

    async def append_message(self, message: Message) -> None:
        self._session.add(
            MessageModel(
                id=str(message.id),
                conversation_id=str(message.conversation_id),
                role=message.role.value,
                content=message.content,
                meta=dict(message.metadata),
                # The domain stamps the message when it is appended. Letting the
                # column default fire instead would store a different instant
                # than the entity the caller was handed, and ordering is derived
                # from this column.
                created_at=message.created_at,
                updated_at=message.created_at,
            )
        )
        await self._session.flush()

    async def recent_messages(
        self, conversation_id: ConversationId, limit: int
    ) -> tuple[Message, ...]:
        return await self._load_messages(
            str(conversation_id), limit=validate_message_window(limit)
        )

    async def _load_messages(self, conversation_id: str, *, limit: int) -> tuple[Message, ...]:
        """Load the newest ``limit`` messages, oldest first.

        Ordering is ``(created_at, id)``. ``created_at`` is the append instant
        assigned by the domain; ``id`` only breaks exact ties so the window is a
        stable total order rather than whatever order the backend happens to
        return. Known limitation: because ``id`` is a random UUID, two messages
        appended to the same conversation within the same microsecond order
        deterministically but not necessarily in append order. Today a turn is
        the only writer of a conversation's messages and appends them
        sequentially, so this is unreachable in practice; a monotonic
        per-conversation sequence column is the fix if concurrent writers are
        ever introduced. See docs/architecture/architecture.md.
        """
        rows = (
            (
                await self._session.execute(
                    sa.select(MessageModel)
                    .where(MessageModel.conversation_id == conversation_id)
                    .order_by(MessageModel.created_at.desc(), MessageModel.id.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return tuple(_message_to_domain(row) for row in reversed(rows))

    async def _to_domain(self, row: ConversationModel, *, message_limit: int) -> Conversation:
        limit = validate_message_window(message_limit)
        messages = await self._load_messages(row.id, limit=limit)
        return Conversation(
            id=ConversationId(UUID(row.id)),
            owner_id=UserId(UUID(row.owner_id)),
            status=ConversationStatus(row.status),
            title=row.title,
            messages=messages,
            metadata=row.meta,
            created_at=ensure_utc(row.created_at),
            updated_at=ensure_utc(row.updated_at),
            # Fewer rows than the window means the window was never reached and
            # the whole history is present; only a full page is evidence that
            # older messages may have been left behind.
            history_window=limit if len(messages) == limit else None,
        )


def _message_to_domain(row: MessageModel) -> Message:
    return Message(
        id=MessageId(UUID(row.id)),
        conversation_id=ConversationId(UUID(row.conversation_id)),
        role=MessageRole(row.role),
        content=row.content,
        metadata=row.meta,
        created_at=ensure_utc(row.created_at),
    )
