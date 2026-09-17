"""Conversation repository ports."""

from __future__ import annotations

from typing import Protocol

from nexabot.domain.common.ids import ConversationId, UserId
from nexabot.domain.conversations.entities import Conversation, Message


class ConversationRepository(Protocol):
    async def get(self, conversation_id: ConversationId) -> Conversation | None:
        """Return a conversation by id."""

    async def get_active_for_user(self, user_id: UserId) -> Conversation | None:
        """Return the user's current active conversation, if any."""

    async def add(self, conversation: Conversation) -> None:
        """Persist a new conversation."""

    async def save(self, conversation: Conversation) -> None:
        """Persist a changed conversation aggregate."""

    async def append_message(self, message: Message) -> None:
        """Persist one message."""

    async def recent_messages(
        self, conversation_id: ConversationId, limit: int
    ) -> tuple[Message, ...]:
        """Load a bounded recent history window."""
