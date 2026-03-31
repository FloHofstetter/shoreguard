"""Create audit_log table.

Revision ID: 005
Revises: 004
Create Date: 2026-03-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the audit_log table."""
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.String(), nullable=False),
        sa.Column("actor", sa.String(254), nullable=False),
        sa.Column("actor_role", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=False),
        sa.Column("resource_id", sa.String(253), nullable=False, server_default=""),
        sa.Column("gateway", sa.String(253), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("client_ip", sa.String(45), nullable=True),
    )
    op.create_index("ix_audit_log_timestamp", "audit_log", ["timestamp"])
    op.create_index("ix_audit_log_actor", "audit_log", ["actor"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_resource_type", "audit_log", ["resource_type"])
    op.create_index("ix_audit_log_gateway", "audit_log", ["gateway"])


def downgrade() -> None:
    """Drop the audit_log table."""
    op.drop_index("ix_audit_log_gateway", table_name="audit_log")
    op.drop_index("ix_audit_log_resource_type", table_name="audit_log")
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_index("ix_audit_log_actor", table_name="audit_log")
    op.drop_index("ix_audit_log_timestamp", table_name="audit_log")
    op.drop_table("audit_log")
