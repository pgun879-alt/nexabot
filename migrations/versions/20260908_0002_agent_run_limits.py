"""agent run limits

Revision ID: 20260908_0002
Revises: 20260824_0001
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0002"
down_revision = "20260824_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs", sa.Column("max_steps", sa.Integer, nullable=False, server_default="12")
    )
    op.add_column(
        "agent_runs", sa.Column("max_tool_calls", sa.Integer, nullable=False, server_default="6")
    )
    op.add_column(
        "agent_runs", sa.Column("max_retries", sa.Integer, nullable=False, server_default="2")
    )
    op.add_column(
        "agent_runs",
        sa.Column("max_execution_seconds", sa.Integer, nullable=False, server_default="60"),
    )
    op.add_column(
        "agent_runs",
        sa.Column("max_context_tokens", sa.Integer, nullable=False, server_default="12000"),
    )
    op.add_column("agent_runs", sa.Column("final_response", sa.Text, nullable=True))

    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.alter_column("max_steps", server_default=None)
        batch_op.alter_column("max_tool_calls", server_default=None)
        batch_op.alter_column("max_retries", server_default=None)
        batch_op.alter_column("max_execution_seconds", server_default=None)
        batch_op.alter_column("max_context_tokens", server_default=None)


def downgrade() -> None:
    op.drop_column("agent_runs", "final_response")
    op.drop_column("agent_runs", "max_context_tokens")
    op.drop_column("agent_runs", "max_execution_seconds")
    op.drop_column("agent_runs", "max_retries")
    op.drop_column("agent_runs", "max_tool_calls")
    op.drop_column("agent_runs", "max_steps")
