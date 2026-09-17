"""idempotency claim expiry

Adds the timestamp that lets an unsettled STARTED claim expire, so a process
that dies mid-operation does not block its idempotency key forever.

Revision ID: 20260917_0003
Revises: 20260908_0002
Create Date: 2026-09-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260917_0003"
down_revision = "20260908_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "idempotency_records",
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Existing rows needed a default to backfill; new rows get their value
    # from the application so the claim time is the real claim time.
    with op.batch_alter_table("idempotency_records") as batch_op:
        batch_op.alter_column(
            "claimed_at",
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=None,
        )

    op.create_index(
        "ix_idempotency_records_state_claimed_at",
        "idempotency_records",
        ["state", "claimed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_idempotency_records_state_claimed_at", "idempotency_records")
    op.drop_column("idempotency_records", "claimed_at")
