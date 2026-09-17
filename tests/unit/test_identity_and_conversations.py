from __future__ import annotations

import pytest

from nexabot.domain.common.errors import ConflictError
from nexabot.domain.common.ids import new_conversation_id, new_user_id
from nexabot.domain.conversations.entities import Conversation, ConversationStatus, MessageRole
from nexabot.domain.identity.entities import Permission, Role, TelegramIdentity, User, UserStatus


def test_user_permissions_are_derived_from_roles() -> None:
    permission = Permission(namespace="tool", action="calculator")
    user = User(
        id=new_user_id(),
        roles=frozenset({Role(name="member", permissions=frozenset({permission}))}),
    )

    assert user.has_permission(permission)
    assert not user.has_permission(Permission(namespace="admin", action="broadcast"))


def test_user_ban_unban_state_transitions() -> None:
    user = User(id=new_user_id(), telegram_identity=TelegramIdentity(telegram_user_id=123))

    user.ban()
    assert user.status == UserStatus.BANNED

    user.unban()
    assert user.status == UserStatus.ACTIVE


def test_user_cannot_be_banned_twice() -> None:
    user = User(id=new_user_id())
    user.ban()

    with pytest.raises(ConflictError):
        user.ban()


def test_conversation_appends_and_bounds_recent_messages() -> None:
    conversation = Conversation(id=new_conversation_id(), owner_id=new_user_id())

    first = conversation.append_message(role=MessageRole.USER, content="first")
    second = conversation.append_message(role=MessageRole.ASSISTANT, content="second")

    assert conversation.recent_messages(1) == (second,)
    assert conversation.recent_messages(2) == (first, second)


def test_archived_conversation_rejects_new_messages() -> None:
    conversation = Conversation(id=new_conversation_id(), owner_id=new_user_id())

    conversation.archive()

    assert conversation.status == ConversationStatus.ARCHIVED
    with pytest.raises(ConflictError):
        conversation.append_message(role=MessageRole.USER, content="blocked")
