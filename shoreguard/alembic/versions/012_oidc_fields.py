"""Add OIDC provider and subject fields to users.

Allows users to authenticate via OpenID Connect providers.

Revision ID: 012
Revises: 011
Create Date: 2026-04-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "012"
down_revision: str | None = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add oidc_provider and oidc_sub columns to users table."""
    op.add_column("users", sa.Column("oidc_provider", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("oidc_sub", sa.String(255), nullable=True))
    op.create_index(
        "uq_user_oidc",
        "users",
        ["oidc_provider", "oidc_sub"],
        unique=True,
    )


def downgrade() -> None:
    """Remove OIDC columns from users table."""
    op.drop_index("uq_user_oidc", table_name="users")
    op.drop_column("users", "oidc_sub")
    op.drop_column("users", "oidc_provider")
