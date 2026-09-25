"""Conversation repository ports."""

from __future__ import annotations

from typing import Protocol

from nexabot.domain.common.errors import ValidationError
from nexabot.domain.common.ids import ConversationId, UserId
from nexabot.domain.conversations.entities import Conversation, Message

DEFAULT_MESSAGE_WINDOW = 50
"""How many recent messages a loaded conversation carries by default.

Loading a conversation must cost the same whether it holds ten messages or ten
thousand. Callers that need a specific window pass ``message_limit``; callers
that need to page through history use ``recent_messages``.
"""

MAX_MESSAGE_WINDOW = 500
"""Hard ceiling on how many messages one call may load.

A window is ultimately caller-controlled input. Without a ceiling, a single
request can ask for an unbounded number of rows and turn a read into a memory
exhaustion vector; the limit is enforced here rather than in each adapter so
every implementation of the port inherits it.
"""


def validate_message_window(limit: int) -> int:
    """Reject windows that are not a usable, bounded page size."""
    if limit <= 0:
        raise ValidationError(
            "Message window must be positive.",
            code="message_window_invalid",
            requested=limit,
        )
    if limit > MAX_MESSAGE_WINDOW:
        raise ValidationError(
            "Message window exceeds the maximum page size.",
            code="message_window_too_large",
            requested=limit,
            maximum=MAX_MESSAGE_WINDOW,
        )
    return limit


class ConversationRepository(Protocol):
    async def get(
        self,
        conversation_id: ConversationId,
        *,
        message_limit: int = DEFAULT_MESSAGE_WINDOW,
    ) -> Conversation | None:
        """Return a conversation carrying at most ``message_limit`` recent messages."""

    async def get_active_for_user(
        self,
        user_id: UserId,
        *,
        message_limit: int = DEFAULT_MESSAGE_WINDOW,
    ) -> Conversation | None:
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
