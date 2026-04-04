"""Add description and labels_json columns to gateways table.

Revision ID: 004
Revises: 003
Create Date: 2026-04-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add description and labels columns to gateways table."""
    op.add_column(
        "gateways",
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.add_column(
        "gateways",
        sa.Column("labels_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Remove description and labels columns from gateways table."""
    op.drop_column("gateways", "labels_json")
    op.drop_column("gateways", "description")
