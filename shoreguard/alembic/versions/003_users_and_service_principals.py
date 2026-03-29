"""Replace api_keys with users and service_principals tables.

Revision ID: 003
Revises: 002
Create Date: 2026-03-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create users and service_principals, migrate api_keys data, drop api_keys."""
    # Create users table
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(128), nullable=True),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("invite_token", sa.String(64), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
    )

    # Create service_principals table
    op.create_table(
        "service_principals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("last_used", sa.String(), nullable=True),
    )
    op.create_index("ix_service_principals_key_hash", "service_principals", ["key_hash"])

    # Migrate existing api_keys → service_principals
    op.execute(
        "INSERT INTO service_principals (name, key_hash, role, created_at, last_used) "
        "SELECT name, key_hash, role, created_at, last_used FROM api_keys"
    )

    # Drop old table
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_table("api_keys")


def downgrade() -> None:
    """Recreate api_keys, migrate service_principals back, drop users and service_principals."""
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("last_used", sa.String(), nullable=True),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"])

    op.execute(
        "INSERT INTO api_keys (name, key_hash, role, created_at, last_used) "
        "SELECT name, key_hash, role, created_at, last_used FROM service_principals"
    )

    op.drop_index("ix_service_principals_key_hash", table_name="service_principals")
    op.drop_table("service_principals")
    op.drop_table("users")
