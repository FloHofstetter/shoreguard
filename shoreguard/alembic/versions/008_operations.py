"""Create operations table for long-running operation tracking.

Revision ID: 008
Revises: 007
Create Date: 2026-04-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create operations table."""
    op.create_table(
        "operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("resource_type", sa.String(50), nullable=False),
        sa.Column("resource_key", sa.String(253), nullable=False),
        sa.Column("idempotency_key", sa.String(253), nullable=True, unique=True),
        sa.Column("progress_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_msg", sa.String(500), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("actor", sa.String(254), nullable=True),
        sa.Column("gateway_name", sa.String(253), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_operations_status", "operations", ["status"])
    op.create_index("ix_operations_resource", "operations", ["resource_type", "resource_key"])


def downgrade() -> None:
    """Drop operations table."""
    op.drop_index("ix_operations_resource", table_name="operations")
    op.drop_index("ix_operations_status", table_name="operations")
    op.drop_table("operations")
