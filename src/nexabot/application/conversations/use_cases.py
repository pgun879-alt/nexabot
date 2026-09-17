"""Conversation application use cases.

These orchestrate domain entities and repository ports only. No
infrastructure, Telegram, or FastAPI details may appear here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from nexabot.domain.common.errors import NotFoundError
from nexabot.domain.common.ids import ConversationId, UserId, new_conversation_id
from nexabot.domain.conversations.entities import Conversation, Message, MessageRole
from nexabot.ports.repositories.conversations import ConversationRepository


@dataclass(slots=True)
class CreateConversation:
    conversations: ConversationRepository

    async def execute(self, *, owner_id: UserId, title: str | None = None) -> Conversation:
        conversation = Conversation(id=new_conversation_id(), owner_id=owner_id, title=title)
        await self.conversations.add(conversation)
        return conversation


@dataclass(slots=True)
class AppendMessage:
    conversations: ConversationRepository

    async def execute(
        self,
        *,
        conversation_id: ConversationId,
        role: MessageRole,
        content: str,
        metadata: Mapping[str, object] | None = None,
    ) -> Message:
        conversation = await self._get_or_raise(conversation_id)
        message = conversation.append_message(role=role, content=content, metadata=metadata)
        await self.conversations.append_message(message)
        return message

    async def _get_or_raise(self, conversation_id: ConversationId) -> Conversation:
        conversation = await self.conversations.get(conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found.", code="conversation_not_found")
        return conversation


@dataclass(slots=True)
class LoadRecentContext:
    conversations: ConversationRepository

    async def execute(self, conversation_id: ConversationId, *, limit: int) -> tuple[Message, ...]:
        return await self.conversations.recent_messages(conversation_id, limit)


@dataclass(slots=True)
class ArchiveConversation:
    conversations: ConversationRepository

    async def execute(self, conversation_id: ConversationId) -> Conversation:
        conversation = await self.conversations.get(conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found.", code="conversation_not_found")
        conversation.archive()
        await self.conversations.save(conversation)
        return conversation


@dataclass(slots=True)
class DeleteConversation:
    conversations: ConversationRepository

    async def execute(self, conversation_id: ConversationId) -> Conversation:
        conversation = await self.conversations.get(conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found.", code="conversation_not_found")
        conversation.delete()
        await self.conversations.save(conversation)
        return conversation
