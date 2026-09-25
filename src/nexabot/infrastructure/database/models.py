"""Relational persistence model.

These ORM classes mirror the initial architecture concepts without leaking
SQLAlchemy into domain entities.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from nexabot.domain.common.time import utc_now
from nexabot.infrastructure.database.base import Base, TimestampMixin


def uuid_pk() -> Mapped[str]:
    return mapped_column(sa.String(36), primary_key=True)


def uuid_fk(target: str, *, nullable: bool = False, index: bool = True) -> Mapped[str]:
    return mapped_column(sa.String(36), sa.ForeignKey(target), nullable=nullable, index=index)


user_roles = sa.Table(
    "user_roles",
    Base.metadata,
    sa.Column(
        "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    ),
    sa.Column(
        "role_id", sa.String(36), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    ),
)

role_permissions = sa.Table(
    "role_permissions",
    Base.metadata,
    sa.Column(
        "role_id", sa.String(36), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    ),
    sa.Column(
        "permission_id",
        sa.String(36),
        sa.ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class UserModel(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = uuid_pk()
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('active', 'banned', 'disabled')", name="user_status_valid"
        ),
    )


class TelegramIdentityModel(TimestampMixin, Base):
    __tablename__ = "telegram_identities"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = uuid_fk("users.id")
    telegram_user_id: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, unique=True, index=True
    )
    username: Mapped[str | None] = mapped_column(sa.String(255))
    first_name: Mapped[str | None] = mapped_column(sa.String(255))
    last_name: Mapped[str | None] = mapped_column(sa.String(255))

    __table_args__ = (sa.UniqueConstraint("user_id", name="uq_telegram_identities_user_id"),)


class RoleModel(TimestampMixin, Base):
    __tablename__ = "roles"

    id: Mapped[str] = uuid_pk()
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(sa.Text)


class PermissionModel(TimestampMixin, Base):
    __tablename__ = "permissions"

    id: Mapped[str] = uuid_pk()
    namespace: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    action: Mapped[str] = mapped_column(sa.String(128), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("namespace", "action", name="uq_permissions_namespace_action"),
    )


class ConversationModel(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str] = uuid_fk("users.id")
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(sa.String(255))
    meta: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)

    __table_args__ = (
        sa.Index("ix_conversations_owner_status", "owner_id", "status"),
        sa.CheckConstraint(
            "status IN ('active', 'archived', 'deleted')", name="conversation_status_valid"
        ),
    )


class MessageModel(TimestampMixin, Base):
    __tablename__ = "messages"

    id: Mapped[str] = uuid_pk()
    conversation_id: Mapped[str] = uuid_fk("conversations.id")
    role: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)

    __table_args__ = (
        sa.Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        sa.CheckConstraint(
            "role IN ('system', 'user', 'assistant', 'tool')", name="message_role_valid"
        ),
        # Message.__post_init__ rejects blank content; an empty row would load
        # back as an entity the domain considers impossible to construct.
        sa.CheckConstraint("content <> ''", name="message_content_not_empty"),
    )


class AgentRunModel(TimestampMixin, Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = uuid_fk("users.id")
    conversation_id: Mapped[str] = uuid_fk("conversations.id")
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    model: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(sa.Text)
    final_response: Mapped[str | None] = mapped_column(sa.Text)
    usage_metadata: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)
    max_steps: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=12)
    max_tool_calls: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=6)
    max_retries: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=2)
    max_execution_seconds: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=60)
    max_context_tokens: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=12_000)

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="agent_run_status_valid",
        ),
        # Mirrors AgentLimits.__post_init__, which refuses to build a run whose
        # budgets are not positive.
        sa.CheckConstraint(
            "max_steps > 0 AND max_tool_calls > 0 AND max_retries > 0 "
            "AND max_execution_seconds > 0 AND max_context_tokens > 0",
            name="agent_run_limits_positive",
        ),
        # A run that reached a terminal state recorded how it ended. Without
        # this, a half-written settlement leaves a row that looks finished but
        # carries neither an answer nor a reason.
        sa.CheckConstraint(
            "(status NOT IN ('succeeded', 'failed', 'cancelled') OR finished_at IS NOT NULL) "
            "AND (status <> 'succeeded' OR final_response IS NOT NULL) "
            "AND (status <> 'failed' OR failure_reason IS NOT NULL)",
            name="agent_run_terminal_state_recorded",
        ),
    )


class AgentStepModel(TimestampMixin, Base):
    __tablename__ = "agent_steps"

    id: Mapped[str] = uuid_pk()
    run_id: Mapped[str] = uuid_fk("agent_runs.id")
    index: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("run_id", "index", name="uq_agent_steps_run_index"),
        sa.Index("ix_agent_steps_run_type", "run_id", "type"),
        # AgentRun numbers steps from 1; index 0 or negative would break the
        # "persisted steps are a prefix of in-memory steps" assumption the
        # agent-run repository relies on when appending new steps.
        # "index" is quoted because it is a reserved word on SQLite.
        sa.CheckConstraint('"index" >= 1', name="agent_step_index_positive"),
    )


class ToolModel(TimestampMixin, Base):
    __tablename__ = "tools"

    id: Mapped[str] = uuid_pk()
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    version: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    sensitivity: Mapped[str] = mapped_column(sa.String(32), nullable=False)

    __table_args__ = (sa.UniqueConstraint("name", "version", name="uq_tools_name_version"),)


class ToolExecutionModel(TimestampMixin, Base):
    __tablename__ = "tool_executions"

    id: Mapped[str] = uuid_pk()
    run_id: Mapped[str] = uuid_fk("agent_runs.id")
    tool_id: Mapped[str | None] = uuid_fk("tools.id", nullable=True)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    input: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    failure_reason: Mapped[str | None] = mapped_column(sa.Text)


class MemoryModel(TimestampMixin, Base):
    __tablename__ = "memories"

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str] = uuid_fk("users.id")
    type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)

    __table_args__ = (sa.Index("ix_memories_owner_type", "owner_id", "type"),)


class MemoryItemModel(TimestampMixin, Base):
    __tablename__ = "memory_items"

    id: Mapped[str] = uuid_pk()
    memory_id: Mapped[str] = uuid_fk("memories.id")
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    importance: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)


class FileModel(TimestampMixin, Base):
    __tablename__ = "files"

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str] = uuid_fk("users.id")
    filename: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(sa.String(1024), nullable=False, unique=True)
    processing_status: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)

    __table_args__ = (sa.Index("ix_files_owner_status", "owner_id", "processing_status"),)


class FileChunkModel(TimestampMixin, Base):
    __tablename__ = "file_chunks"

    id: Mapped[str] = uuid_pk()
    file_id: Mapped[str] = uuid_fk("files.id")
    index: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)

    __table_args__ = (sa.UniqueConstraint("file_id", "index", name="uq_file_chunks_file_index"),)


class UsageRecordModel(TimestampMixin, Base):
    __tablename__ = "usage_records"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = uuid_fk("users.id")
    dimension: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    amount: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    period: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)

    __table_args__ = (
        sa.Index("ix_usage_records_user_dimension_period", "user_id", "dimension", "period"),
    )


class SubscriptionModel(TimestampMixin, Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = uuid_fk("users.id")
    plan: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    valid_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class TaskModel(TimestampMixin, Base):
    __tablename__ = "tasks"

    id: Mapped[str] = uuid_pk()
    type: Mapped[str] = mapped_column(sa.String(128), nullable=False, index=True)
    state: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)
    attempts: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(sa.Integer, default=3, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(255))
    last_error: Mapped[str | None] = mapped_column(sa.Text)

    __table_args__ = (
        sa.UniqueConstraint("type", "idempotency_key", name="uq_tasks_type_idempotency_key"),
    )


class TaskAttemptModel(TimestampMixin, Base):
    __tablename__ = "task_attempts"

    id: Mapped[str] = uuid_pk()
    task_id: Mapped[str] = uuid_fk("tasks.id")
    attempt_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    error: Mapped[str | None] = mapped_column(sa.Text)

    __table_args__ = (
        sa.UniqueConstraint("task_id", "attempt_number", name="uq_task_attempts_task_attempt"),
    )


class IdempotencyRecordModel(TimestampMixin, Base):
    __tablename__ = "idempotency_records"

    id: Mapped[str] = uuid_pk()
    namespace: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    key: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    state: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    result: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)
    # When the current STARTED claim was taken, so a crashed attempt's claim
    # can expire instead of blocking the key forever.
    claimed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utc_now, nullable=False
    )
    # Identifies the *current* claim. Settlement is conditioned on it, so a
    # worker whose claim expired and was taken over cannot overwrite the
    # outcome recorded by the attempt that replaced it.
    claim_token: Mapped[str] = mapped_column(sa.String(36), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("namespace", "key", name="uq_idempotency_namespace_key"),
        sa.Index("ix_idempotency_records_state_claimed_at", "state", "claimed_at"),
        sa.CheckConstraint(
            "state IN ('started', 'completed', 'failed')",
            name="idempotency_state_valid",
        ),
    )


class AuditLogModel(TimestampMixin, Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = uuid_pk()
    actor_user_id: Mapped[str | None] = uuid_fk("users.id", nullable=True)
    action: Mapped[str] = mapped_column(sa.String(128), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(sa.String(128))
    target_id: Mapped[str | None] = mapped_column(sa.String(128))
    outcome: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)
