"""foundation schema

Constraints are declared inline in ``create_table`` rather than added
afterwards with ``op.create_unique_constraint``. SQLite cannot ALTER a table to
add a constraint, so the standalone form makes the whole chain impossible to
smoke-test anywhere but PostgreSQL. Inline declarations run everywhere and
produce the same schema.

Revision ID: 20260824_0001
Revises:
Create Date: 2026-08-24
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "20260824_0001"
down_revision = None
branch_labels = None
depends_on = None

_UUID = sa.String(36)


def _pk(name: str = "id") -> sa.Column[str]:
    return sa.Column(name, _UUID, primary_key=True)


def _fk(
    table: str,
    name: str,
    target: str,
    *,
    nullable: bool = False,
    ondelete: str | None = None,
) -> sa.Column[str]:
    """A foreign key named the way the ORM's naming convention names it.

    Leaving these unnamed lets each backend invent its own, which then shows up
    as spurious drift the first time someone runs ``--autogenerate``.
    """
    referred = target.split(".")[0]
    return sa.Column(
        name,
        _UUID,
        sa.ForeignKey(target, name=f"fk_{table}_{name}_{referred}", ondelete=ondelete),
        nullable=nullable,
    )


def _timestamps() -> list[sa.Column[datetime]]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _index(name: str, table: str, columns: list[str], *, unique: bool = False) -> None:
    op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    op.create_table(
        "users",
        _pk(),
        sa.Column("status", sa.String(32), nullable=False),
        *_timestamps(),
    )
    _index("ix_users_status", "users", ["status"])

    op.create_table(
        "roles",
        _pk(),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text),
        *_timestamps(),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )

    op.create_table(
        "permissions",
        _pk(),
        sa.Column("namespace", sa.String(128), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "namespace", "action", name="uq_permissions_namespace_action"
        ),
    )

    op.create_table(
        "user_roles",
        _fk("user_roles", "user_id", "users.id", ondelete="CASCADE"),
        _fk("user_roles", "role_id", "roles.id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "role_id", name="pk_user_roles"),
    )

    op.create_table(
        "role_permissions",
        _fk("role_permissions", "role_id", "roles.id", ondelete="CASCADE"),
        _fk("role_permissions", "permission_id", "permissions.id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(
            "role_id", "permission_id", name="pk_role_permissions"
        ),
    )

    op.create_table(
        "telegram_identities",
        _pk(),
        _fk("telegram_identities", "user_id", "users.id"),
        sa.Column("telegram_user_id", sa.BigInteger, nullable=False),
        sa.Column("username", sa.String(255)),
        sa.Column("first_name", sa.String(255)),
        sa.Column("last_name", sa.String(255)),
        *_timestamps(),
        sa.UniqueConstraint("user_id", name="uq_telegram_identities_user_id"),
    )
    _index("ix_telegram_identities_user_id", "telegram_identities", ["user_id"])
    # The model declares unique=True alongside index=True, which SQLAlchemy
    # renders as one unique index rather than a constraint plus a plain index.
    _index(
        "ix_telegram_identities_telegram_user_id",
        "telegram_identities",
        ["telegram_user_id"],
        unique=True,
    )

    op.create_table(
        "conversations",
        _pk(),
        _fk("conversations", "owner_id", "users.id"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255)),
        sa.Column("meta", sa.JSON, nullable=False),
        *_timestamps(),
    )
    _index("ix_conversations_owner_id", "conversations", ["owner_id"])
    _index("ix_conversations_status", "conversations", ["status"])
    _index("ix_conversations_owner_status", "conversations", ["owner_id", "status"])

    op.create_table(
        "messages",
        _pk(),
        _fk("messages", "conversation_id", "conversations.id"),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
        *_timestamps(),
    )
    _index("ix_messages_conversation_id", "messages", ["conversation_id"])
    _index("ix_messages_role", "messages", ["role"])
    _index(
        "ix_messages_conversation_created", "messages", ["conversation_id", "created_at"]
    )

    op.create_table(
        "agent_runs",
        _pk(),
        _fk("agent_runs", "user_id", "users.id"),
        _fk("agent_runs", "conversation_id", "conversations.id"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("failure_reason", sa.Text),
        sa.Column("usage_metadata", sa.JSON, nullable=False),
        *_timestamps(),
    )
    _index("ix_agent_runs_user_id", "agent_runs", ["user_id"])
    _index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])
    _index("ix_agent_runs_status", "agent_runs", ["status"])

    op.create_table(
        "agent_steps",
        _pk(),
        _fk("agent_steps", "run_id", "agent_runs.id"),
        sa.Column("index", sa.Integer, nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("run_id", "index", name="uq_agent_steps_run_index"),
    )
    _index("ix_agent_steps_run_id", "agent_steps", ["run_id"])
    _index("ix_agent_steps_run_type", "agent_steps", ["run_id", "type"])

    op.create_table(
        "tools",
        _pk(),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("sensitivity", sa.String(32), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("name", "version", name="uq_tools_name_version"),
    )

    op.create_table(
        "tool_executions",
        _pk(),
        _fk("tool_executions", "run_id", "agent_runs.id"),
        _fk("tool_executions", "tool_id", "tools.id", nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input", sa.JSON, nullable=False),
        sa.Column("output", sa.JSON),
        sa.Column("failure_reason", sa.Text),
        *_timestamps(),
    )
    _index("ix_tool_executions_run_id", "tool_executions", ["run_id"])
    _index("ix_tool_executions_tool_id", "tool_executions", ["tool_id"])
    _index("ix_tool_executions_status", "tool_executions", ["status"])

    op.create_table(
        "memories",
        _pk(),
        _fk("memories", "owner_id", "users.id"),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        *_timestamps(),
    )
    _index("ix_memories_owner_id", "memories", ["owner_id"])
    _index("ix_memories_status", "memories", ["status"])
    _index("ix_memories_owner_type", "memories", ["owner_id", "type"])

    op.create_table(
        "memory_items",
        _pk(),
        _fk("memory_items", "memory_id", "memories.id"),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("importance", sa.Integer, nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
        *_timestamps(),
    )
    _index("ix_memory_items_memory_id", "memory_items", ["memory_id"])

    op.create_table(
        "files",
        _pk(),
        _fk("files", "owner_id", "users.id"),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("processing_status", sa.String(32), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("storage_key", name="uq_files_storage_key"),
    )
    _index("ix_files_owner_id", "files", ["owner_id"])
    _index("ix_files_processing_status", "files", ["processing_status"])
    _index("ix_files_owner_status", "files", ["owner_id", "processing_status"])

    op.create_table(
        "file_chunks",
        _pk(),
        _fk("file_chunks", "file_id", "files.id"),
        sa.Column("index", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("file_id", "index", name="uq_file_chunks_file_index"),
    )
    _index("ix_file_chunks_file_id", "file_chunks", ["file_id"])

    op.create_table(
        "usage_records",
        _pk(),
        _fk("usage_records", "user_id", "users.id"),
        sa.Column("dimension", sa.String(64), nullable=False),
        sa.Column("amount", sa.Integer, nullable=False),
        sa.Column("period", sa.String(32), nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
        *_timestamps(),
    )
    _index("ix_usage_records_user_id", "usage_records", ["user_id"])
    _index(
        "ix_usage_records_user_dimension_period",
        "usage_records",
        ["user_id", "dimension", "period"],
    )

    op.create_table(
        "subscriptions",
        _pk(),
        _fk("subscriptions", "user_id", "users.id"),
        sa.Column("plan", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    _index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    _index("ix_subscriptions_status", "subscriptions", ["status"])

    op.create_table(
        "tasks",
        _pk(),
        sa.Column("type", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("max_attempts", sa.Integer, nullable=False),
        sa.Column("idempotency_key", sa.String(255)),
        sa.Column("last_error", sa.Text),
        *_timestamps(),
        sa.UniqueConstraint(
            "type", "idempotency_key", name="uq_tasks_type_idempotency_key"
        ),
    )
    _index("ix_tasks_type", "tasks", ["type"])
    _index("ix_tasks_state", "tasks", ["state"])

    op.create_table(
        "task_attempts",
        _pk(),
        _fk("task_attempts", "task_id", "tasks.id"),
        sa.Column("attempt_number", sa.Integer, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text),
        *_timestamps(),
        sa.UniqueConstraint(
            "task_id", "attempt_number", name="uq_task_attempts_task_attempt"
        ),
    )
    _index("ix_task_attempts_task_id", "task_attempts", ["task_id"])

    op.create_table(
        "idempotency_records",
        _pk(),
        sa.Column("namespace", sa.String(128), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("result", sa.JSON, nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "namespace", "key", name="uq_idempotency_namespace_key"
        ),
    )
    _index("ix_idempotency_records_state", "idempotency_records", ["state"])

    op.create_table(
        "audit_logs",
        _pk(),
        _fk("audit_logs", "actor_user_id", "users.id", nullable=True),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("target_type", sa.String(128)),
        sa.Column("target_id", sa.String(128)),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
        *_timestamps(),
    )
    _index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    _index("ix_audit_logs_action", "audit_logs", ["action"])


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
