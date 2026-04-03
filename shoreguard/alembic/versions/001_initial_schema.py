"""Initial schema — all tables created from scratch.

Revision ID: 001
Revises: None
Create Date: 2026-04-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create all tables for Shoreguard."""
    op.create_table(
        "gateways",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(253), unique=True, nullable=False),
        sa.Column("endpoint", sa.String(260), nullable=False),
        sa.Column("scheme", sa.String(), nullable=False, server_default="https"),
        sa.Column("auth_mode", sa.String(), nullable=True),
        sa.Column("ca_cert", sa.LargeBinary(), nullable=True),
        sa.Column("client_cert", sa.LargeBinary(), nullable=True),
        sa.Column("client_key", sa.LargeBinary(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(), nullable=False, server_default="unknown"),
    )
    op.create_index("ix_gateways_endpoint", "gateways", ["endpoint"])
    op.create_index("ix_gateways_registered_at", "gateways", ["registered_at"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(254), unique=True, nullable=False),
        sa.Column("hashed_password", sa.String(128), nullable=True),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("invite_token_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "service_principals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "user_gateway_roles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "gateway_id",
            sa.Integer(),
            sa.ForeignKey("gateways.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.UniqueConstraint("user_id", "gateway_id"),
    )

    op.create_table(
        "sp_gateway_roles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "sp_id",
            sa.Integer(),
            sa.ForeignKey("service_principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "gateway_id",
            sa.Integer(),
            sa.ForeignKey("gateways.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.UniqueConstraint("sp_id", "gateway_id"),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(254), nullable=False),
        sa.Column("actor_role", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=False),
        sa.Column("resource_id", sa.String(253), nullable=False, server_default=""),
        sa.Column("gateway_name", sa.String(253), nullable=True),
        sa.Column(
            "gateway_id",
            sa.Integer(),
            sa.ForeignKey("gateways.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("client_ip", sa.String(45), nullable=True),
    )
    op.create_index("ix_audit_log_timestamp", "audit_log", ["timestamp"])
    op.create_index("ix_audit_log_actor", "audit_log", ["actor"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_gateway_name", "audit_log", ["gateway_name"])


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table("audit_log")
    op.drop_table("sp_gateway_roles")
    op.drop_table("user_gateway_roles")
    op.drop_table("service_principals")
    op.drop_table("users")
    op.drop_table("gateways")
