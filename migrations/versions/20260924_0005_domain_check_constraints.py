"""domain check constraints

Each constraint here mirrors an invariant the domain entities already enforce
in Python. Application-only invariants hold exactly as long as every writer
goes through the application; these make the database refuse the same rows, so
a bad backfill, a manual fix, or a future writer cannot create state the domain
considers impossible to construct.

Enum-valued columns are constrained where a repository converts the stored
string back into a ``StrEnum`` on load: an unexpected value there raises a bare
``ValueError`` from deep inside a repository rather than a domain error.

Revision ID: 20260924_0005
Revises: 20260924_0004
Create Date: 2026-09-24
"""

from __future__ import annotations

from alembic import op

revision = "20260924_0005"
down_revision = "20260924_0004"
branch_labels = None
depends_on = None

# (table, constraint name, condition)
_CHECKS = [
    ("users", "user_status_valid", "status IN ('active', 'banned', 'disabled')"),
    (
        "conversations",
        "conversation_status_valid",
        "status IN ('active', 'archived', 'deleted')",
    ),
    ("messages", "message_role_valid", "role IN ('system', 'user', 'assistant', 'tool')"),
    ("messages", "message_content_not_empty", "content <> ''"),
    (
        "agent_runs",
        "agent_run_status_valid",
        "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
    ),
    (
        "agent_runs",
        "agent_run_limits_positive",
        "max_steps > 0 AND max_tool_calls > 0 AND max_retries > 0 "
        "AND max_execution_seconds > 0 AND max_context_tokens > 0",
    ),
    (
        "agent_runs",
        "agent_run_terminal_state_recorded",
        "(status NOT IN ('succeeded', 'failed', 'cancelled') OR finished_at IS NOT NULL) "
        "AND (status <> 'succeeded' OR final_response IS NOT NULL) "
        "AND (status <> 'failed' OR failure_reason IS NOT NULL)",
    ),
    # "index" is quoted because it is a reserved word on SQLite.
    ("agent_steps", "agent_step_index_positive", '"index" >= 1'),
]


def upgrade() -> None:
    for table, name, condition in _CHECKS:
        # Batch mode so SQLite (which cannot ALTER a table to add a constraint)
        # can run the same chain the production backend runs.
        with op.batch_alter_table(table) as batch_op:
            batch_op.create_check_constraint(name, condition)


def downgrade() -> None:
    # The bare name is passed here, not the rendered one: the metadata naming
    # convention ("ck_%(table_name)s_%(constraint_name)s") is applied by the
    # operation itself, and pre-expanding it produces a doubled prefix.
    for table, name, _ in reversed(_CHECKS):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(name, type_="check")
