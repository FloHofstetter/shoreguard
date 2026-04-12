"""Add policy_pins table for immutable policy locking.

Operators can pin a sandbox's policy at a specific version to prevent
accidental modifications during production freezes.

Revision ID: 013
Revises: 012
Create Date: 2026-04-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: str | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create policy_pins table."""
    op.create_table(
        "policy_pins",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("gateway_name", sa.String(253), nullable=False),
        sa.Column("sandbox_name", sa.String(253), nullable=False),
        sa.Column("pinned_version", sa.Integer, nullable=False),
        sa.Column("pinned_by", sa.String(254), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("gateway_name", "sandbox_name"),
    )


def downgrade() -> None:
    """Drop policy_pins table."""
    op.drop_table("policy_pins")
