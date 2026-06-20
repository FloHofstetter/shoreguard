"""Add tenant grouping tables.

Tenants are a control-plane visibility boundary: a named grouping of
gateways and users so a non-admin user sees only their tenants'
gateways. Three tables — ``tenants`` plus the ``tenant_gateways`` and
``tenant_users`` joins — mirroring the group tables. Guarded with
existence checks (like ``102_budgets``) because the ``v2_baseline``
revision creates the full model schema on fresh databases. Rollback
path: drop the three tables (pure membership/config data — no gateway,
user, or audit row is touched).

Revision ID: 110_tenants
Revises: 109_budget_pricing
Create Date: 2026-06-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "110_tenants"
down_revision: str | None = "109_budget_pricing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the tenant, tenant-gateway, and tenant-user tables."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("tenants"):
        op.create_table(
            "tenants",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("name"),
        )
    if not inspector.has_table("tenant_gateways"):
        op.create_table(
            "tenant_gateways",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("gateway_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["gateway_id"], ["gateways.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("tenant_id", "gateway_id", name="uq_tenant_gateway"),
        )
        op.create_index("ix_tenant_gateways_tenant_id", "tenant_gateways", ["tenant_id"])
    if not inspector.has_table("tenant_users"):
        op.create_table(
            "tenant_users",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_user"),
        )
        op.create_index("ix_tenant_users_tenant_id", "tenant_users", ["tenant_id"])
        op.create_index("ix_tenant_users_user_id", "tenant_users", ["user_id"])


def downgrade() -> None:
    """Drop the tenant tables."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in ("tenant_users", "tenant_gateways", "tenants"):
        if inspector.has_table(table):
            op.drop_table(table)
