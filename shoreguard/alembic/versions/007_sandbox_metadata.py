"""Create sandbox_meta table for ShoreGuard-side sandbox metadata.

Revision ID: 007
Revises: 006
Create Date: 2026-04-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create sandbox_meta table."""
    op.create_table(
        "sandbox_meta",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("gateway_name", sa.String(253), nullable=False),
        sa.Column("sandbox_name", sa.String(253), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("labels_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("gateway_name", "sandbox_name"),
    )


def downgrade() -> None:
    """Drop sandbox_meta table."""
    op.drop_table("sandbox_meta")
