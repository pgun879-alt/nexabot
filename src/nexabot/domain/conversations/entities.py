"""Conversation entities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from nexabot.domain.common.errors import ConflictError, ValidationError
from nexabot.domain.common.ids import ConversationId, MessageId, UserId, new_message_id
from nexabot.domain.common.time import utc_now


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class Message:
    id: MessageId
    conversation_id: ConversationId
    role: MessageRole
    content: str
    metadata: Mapping[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValidationError("Message content is required.")


@dataclass(slots=True)
class Conversation:
    id: ConversationId
    owner_id: UserId
    status: ConversationStatus = ConversationStatus.ACTIVE
    title: str | None = None
    messages: tuple[Message, ...] = field(default_factory=tuple)
    metadata: Mapping[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    history_window: int | None = None
    """How many trailing messages were loaded, or ``None`` for the full history.

    Loading every message of a long-running conversation to use the last
    handful is wasteful, but a partially loaded aggregate that cannot be
    distinguished from a complete one is worse. Recording the window lets
    ``recent_messages`` refuse to answer a question the loaded data cannot
    support instead of silently returning too little.
    """

    def append_message(
        self,
        *,
        role: MessageRole,
        content: str,
        metadata: Mapping[str, object] | None = None,
    ) -> Message:
        self.ensure_active()
        message = Message(
            id=new_message_id(),
            conversation_id=self.id,
            role=role,
            content=content,
            metadata=metadata or {},
        )
        self.messages = (*self.messages, message)
        if self.history_window is not None:
            # The trailing run of known-loaded messages just grew by one. Not
            # widening it would make the aggregate claim it knows less history
            # than it is actually holding.
            self.history_window += 1
        self.updated_at = utc_now()
        return message

    def recent_messages(self, limit: int) -> tuple[Message, ...]:
        if limit <= 0:
            raise ValidationError("Recent message limit must be positive.")
        if self.history_window is not None and limit > self.history_window:
            raise ValidationError(
                "Requested more history than was loaded for this conversation.",
                code="conversation_history_not_loaded",
                requested=limit,
                loaded=self.history_window,
            )
        return self.messages[-limit:]

    def archive(self) -> None:
        self.ensure_active()
        self.status = ConversationStatus.ARCHIVED
        self.updated_at = utc_now()

    def delete(self) -> None:
        if self.status == ConversationStatus.DELETED:
            raise ConflictError(
                "Conversation is already deleted.", code="conversation_already_deleted"
            )
        self.status = ConversationStatus.DELETED
        self.updated_at = utc_now()

    def ensure_active(self) -> None:
        if self.status != ConversationStatus.ACTIVE:
            raise ConflictError(
                "Conversation is not active.",
                code="conversation_not_active",
                status=self.status.value,
            )
