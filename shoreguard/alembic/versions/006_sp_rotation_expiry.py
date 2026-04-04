"""Add key_prefix and expires_at columns to service_principals table.

Revision ID: 006
Revises: 005
Create Date: 2026-04-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add key_prefix and expires_at to service_principals."""
    op.add_column(
        "service_principals",
        sa.Column("key_prefix", sa.String(12), nullable=True),
    )
    op.add_column(
        "service_principals",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Remove key_prefix and expires_at from service_principals."""
    op.drop_column("service_principals", "expires_at")
    op.drop_column("service_principals", "key_prefix")
