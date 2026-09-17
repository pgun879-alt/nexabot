"""foundation schema

Revision ID: 20260824_0001
Revises:
Create Date: 2026-08-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260824_0001"
down_revision = None
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def uuid_column(name: str = "id") -> sa.Column:
    return sa.Column(name, sa.String(36), primary_key=True)


def upgrade() -> None:
    op.create_table("users", uuid_column(), sa.Column("status", sa.String(32), nullable=False), *timestamps())
    op.create_index("ix_users_status", "users", ["status"])

    op.create_table("roles", uuid_column(), sa.Column("name", sa.String(128), nullable=False), sa.Column("description", sa.Text), *timestamps())
    op.create_unique_constraint("uq_roles_name", "roles", ["name"])

    op.create_table(
        "permissions",
        uuid_column(),
        sa.Column("namespace", sa.String(128), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        *timestamps(),
    )
    op.create_unique_constraint("uq_permissions_namespace_action", "permissions", ["namespace", "action"])

    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", sa.String(36), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.String(36), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("permission_id", sa.String(36), sa.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
    )

    op.create_table(
        "telegram_identities",
        uuid_column(),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger, nullable=False),
        sa.Column("username", sa.String(255)),
        sa.Column("first_name", sa.String(255)),
        sa.Column("last_name", sa.String(255)),
        *timestamps(),
    )
    op.create_unique_constraint("uq_telegram_identities_user_id", "telegram_identities", ["user_id"])
    op.create_unique_constraint("uq_telegram_identities_telegram_user_id", "telegram_identities", ["telegram_user_id"])
    op.create_index("ix_telegram_identities_telegram_user_id", "telegram_identities", ["telegram_user_id"])

    op.create_table(
        "conversations",
        uuid_column(),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255)),
        sa.Column("meta", sa.JSON, nullable=False),
        *timestamps(),
    )
    op.create_index("ix_conversations_owner_id", "conversations", ["owner_id"])
    op.create_index("ix_conversations_status", "conversations", ["status"])
    op.create_index("ix_conversations_owner_status", "conversations", ["owner_id", "status"])

    op.create_table(
        "messages",
        uuid_column(),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
        *timestamps(),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_role", "messages", ["role"])
    op.create_index("ix_messages_conversation_created", "messages", ["conversation_id", "created_at"])

    op.create_table(
        "agent_runs",
        uuid_column(),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("failure_reason", sa.Text),
        sa.Column("usage_metadata", sa.JSON, nullable=False),
        *timestamps(),
    )
    op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"])
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])

    op.create_table(
        "agent_steps",
        uuid_column(),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("index", sa.Integer, nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
        *timestamps(),
    )
    op.create_unique_constraint("uq_agent_steps_run_index", "agent_steps", ["run_id", "index"])
    op.create_index("ix_agent_steps_run_id", "agent_steps", ["run_id"])
    op.create_index("ix_agent_steps_run_type", "agent_steps", ["run_id", "type"])

    op.create_table(
        "tools",
        uuid_column(),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("sensitivity", sa.String(32), nullable=False),
        *timestamps(),
    )
    op.create_unique_constraint("uq_tools_name_version", "tools", ["name", "version"])

    op.create_table(
        "tool_executions",
        uuid_column(),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("tool_id", sa.String(36), sa.ForeignKey("tools.id")),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input", sa.JSON, nullable=False),
        sa.Column("output", sa.JSON),
        sa.Column("failure_reason", sa.Text),
        *timestamps(),
    )
    op.create_index("ix_tool_executions_run_id", "tool_executions", ["run_id"])
    op.create_index("ix_tool_executions_tool_id", "tool_executions", ["tool_id"])
    op.create_index("ix_tool_executions_status", "tool_executions", ["status"])

    op.create_table(
        "memories",
        uuid_column(),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_memories_owner_id", "memories", ["owner_id"])
    op.create_index("ix_memories_status", "memories", ["status"])
    op.create_index("ix_memories_owner_type", "memories", ["owner_id", "type"])

    op.create_table(
        "memory_items",
        uuid_column(),
        sa.Column("memory_id", sa.String(36), sa.ForeignKey("memories.id"), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("importance", sa.Integer, nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
        *timestamps(),
    )
    op.create_index("ix_memory_items_memory_id", "memory_items", ["memory_id"])

    op.create_table(
        "files",
        uuid_column(),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("processing_status", sa.String(32), nullable=False),
        *timestamps(),
    )
    op.create_unique_constraint("uq_files_storage_key", "files", ["storage_key"])
    op.create_index("ix_files_owner_id", "files", ["owner_id"])
    op.create_index("ix_files_processing_status", "files", ["processing_status"])
    op.create_index("ix_files_owner_status", "files", ["owner_id", "processing_status"])

    op.create_table(
        "file_chunks",
        uuid_column(),
        sa.Column("file_id", sa.String(36), sa.ForeignKey("files.id"), nullable=False),
        sa.Column("index", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
        *timestamps(),
    )
    op.create_unique_constraint("uq_file_chunks_file_index", "file_chunks", ["file_id", "index"])
    op.create_index("ix_file_chunks_file_id", "file_chunks", ["file_id"])

    op.create_table(
        "usage_records",
        uuid_column(),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("dimension", sa.String(64), nullable=False),
        sa.Column("amount", sa.Integer, nullable=False),
        sa.Column("period", sa.String(32), nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
        *timestamps(),
    )
    op.create_index("ix_usage_records_user_id", "usage_records", ["user_id"])
    op.create_index("ix_usage_records_user_dimension_period", "usage_records", ["user_id", "dimension", "period"])

    op.create_table(
        "subscriptions",
        uuid_column(),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("plan", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    op.create_index("ix_subscriptions_status", "subscriptions", ["status"])

    op.create_table(
        "tasks",
        uuid_column(),
        sa.Column("type", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("max_attempts", sa.Integer, nullable=False),
        sa.Column("idempotency_key", sa.String(255)),
        sa.Column("last_error", sa.Text),
        *timestamps(),
    )
    op.create_unique_constraint("uq_tasks_type_idempotency_key", "tasks", ["type", "idempotency_key"])
    op.create_index("ix_tasks_type", "tasks", ["type"])
    op.create_index("ix_tasks_state", "tasks", ["state"])

    op.create_table(
        "task_attempts",
        uuid_column(),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("attempt_number", sa.Integer, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text),
        *timestamps(),
    )
    op.create_unique_constraint("uq_task_attempts_task_attempt", "task_attempts", ["task_id", "attempt_number"])
    op.create_index("ix_task_attempts_task_id", "task_attempts", ["task_id"])

    op.create_table(
        "idempotency_records",
        uuid_column(),
        sa.Column("namespace", sa.String(128), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("result", sa.JSON, nullable=False),
        *timestamps(),
    )
    op.create_unique_constraint("uq_idempotency_namespace_key", "idempotency_records", ["namespace", "key"])
    op.create_index("ix_idempotency_records_state", "idempotency_records", ["state"])

    op.create_table(
        "audit_logs",
        uuid_column(),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("target_type", sa.String(128)),
        sa.Column("target_id", sa.String(128)),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
        *timestamps(),
    )
    op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])


def downgrade() -> None:
    for table in [
        "audit_logs",
        "idempotency_records",
        "task_attempts",
        "tasks",
        "subscriptions",
        "usage_records",
        "file_chunks",
        "files",
        "memory_items",
        "memories",
        "tool_executions",
        "tools",
        "agent_steps",
        "agent_runs",
        "messages",
        "conversations",
        "telegram_identities",
        "role_permissions",
        "user_roles",
        "permissions",
        "roles",
        "users",
    ]:
        op.drop_table(table)
