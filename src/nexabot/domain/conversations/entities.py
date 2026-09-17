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
        self.updated_at = utc_now()
        return message

    def recent_messages(self, limit: int) -> tuple[Message, ...]:
        if limit <= 0:
            raise ValidationError("Recent message limit must be positive.")
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
