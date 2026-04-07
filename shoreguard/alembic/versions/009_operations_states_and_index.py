"""Add partial unique index for active operations on (resource_type, resource_key).

Prevents concurrent operations on the same resource at the DB level.
Also supports the new pending/cancelling status values (no column change
needed — status is String(20) which already accommodates them).

Revision ID: 009
Revises: 008
Create Date: 2026-04-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add partial unique index for active operations."""
    op.execute(
        "CREATE UNIQUE INDEX ix_operations_active_resource "
        "ON operations (resource_type, resource_key) "
        "WHERE status IN ('pending', 'running', 'cancelling')"
    )


def downgrade() -> None:
    """Remove partial unique index."""
    op.drop_index("ix_operations_active_resource", table_name="operations")
