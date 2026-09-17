from __future__ import annotations

from nexabot.infrastructure.database import models as _models  # noqa: F401
from nexabot.infrastructure.database.base import Base


def test_foundation_database_tables_are_registered() -> None:
    expected_tables = {
        "users",
        "roles",
        "permissions",
        "user_roles",
        "role_permissions",
        "telegram_identities",
        "conversations",
        "messages",
        "agent_runs",
        "agent_steps",
        "tools",
        "tool_executions",
        "memories",
        "memory_items",
        "files",
        "file_chunks",
        "usage_records",
        "subscriptions",
        "tasks",
        "task_attempts",
        "idempotency_records",
        "audit_logs",
    }

    assert expected_tables.issubset(set(Base.metadata.tables))


def test_database_model_has_required_uniqueness_constraints() -> None:
    constraints = {
        constraint.name
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if constraint.name
    }

    assert "uq_permissions_namespace_action" in constraints
    assert "uq_agent_steps_run_index" in constraints
    assert "uq_idempotency_namespace_key" in constraints
    assert "uq_tools_name_version" in constraints
