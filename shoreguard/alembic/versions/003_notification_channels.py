"""Add channel_type and extra_config columns to webhooks table.

Revision ID: 003
Revises: 002
Create Date: 2026-04-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add notification channel columns to webhooks table."""
    op.add_column(
        "webhooks",
        sa.Column("channel_type", sa.String(20), nullable=False, server_default="generic"),
    )
    op.add_column(
        "webhooks",
        sa.Column("extra_config", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Remove notification channel columns from webhooks table."""
    op.drop_column("webhooks", "extra_config")
    op.drop_column("webhooks", "channel_type")
