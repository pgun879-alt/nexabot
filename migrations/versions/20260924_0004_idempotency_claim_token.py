"""idempotency claim token

A ``STARTED`` claim can expire and be taken over by another attempt, but until
now nothing recorded *which* claim a settling caller held. The former owner of
an expired claim could therefore call complete()/fail() and overwrite the
outcome produced by the attempt that replaced it. This column is what
settlement is conditioned on.

Revision ID: 20260924_0004
Revises: 20260917_0003
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260924_0004"
down_revision = "20260917_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "idempotency_records",
        sa.Column("claim_token", sa.String(36), nullable=True),
    )

    # Existing rows predate claim tokens. Backfilling from the primary key gives
    # every row a distinct, non-null value; any in-flight claim from before the
    # deploy simply cannot be settled by its original caller, which is the
    # conservative outcome rather than the dangerous one.
    records = sa.table(
        "idempotency_records",
        sa.column("id", sa.String(36)),
        sa.column("claim_token", sa.String(36)),
    )
    op.execute(records.update().values(claim_token=records.c.id))

    with op.batch_alter_table("idempotency_records") as batch_op:
        batch_op.alter_column(
            "claim_token",
            existing_type=sa.String(36),
            nullable=False,
        )
        batch_op.create_check_constraint(
            "idempotency_state_valid",
            "state IN ('started', 'completed', 'failed')",
        )


def downgrade() -> None:
    with op.batch_alter_table("idempotency_records") as batch_op:
        # Bare name: the metadata naming convention renders the "ck_<table>_"
        # prefix, so pre-expanding it here would double the prefix.
        batch_op.drop_constraint("idempotency_state_valid", type_="check")
        batch_op.drop_column("claim_token")
