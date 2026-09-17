"""Identifier helpers used at domain boundaries."""

from __future__ import annotations

from typing import NewType
from uuid import UUID, uuid4

UserId = NewType("UserId", UUID)
ConversationId = NewType("ConversationId", UUID)
MessageId = NewType("MessageId", UUID)
AgentRunId = NewType("AgentRunId", UUID)
AgentStepId = NewType("AgentStepId", UUID)
TaskId = NewType("TaskId", UUID)
FileId = NewType("FileId", UUID)


def new_user_id() -> UserId:
    return UserId(uuid4())


def new_conversation_id() -> ConversationId:
    return ConversationId(uuid4())


def new_message_id() -> MessageId:
    return MessageId(uuid4())


def new_agent_run_id() -> AgentRunId:
    return AgentRunId(uuid4())


def new_agent_step_id() -> AgentStepId:
    return AgentStepId(uuid4())


def new_task_id() -> TaskId:
    return TaskId(uuid4())


def new_file_id() -> FileId:
    return FileId(uuid4())
