"""Create gateway-scoped role tables.

Revision ID: 006
Revises: 005
Create Date: 2026-03-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create user_gateway_roles and sp_gateway_roles tables."""
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
            "gateway_name",
            sa.String(253),
            sa.ForeignKey("gateways.name", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.UniqueConstraint("user_id", "gateway_name"),
    )
    op.create_index("ix_user_gateway_roles_user_id", "user_gateway_roles", ["user_id"])
    op.create_index("ix_user_gateway_roles_gateway_name", "user_gateway_roles", ["gateway_name"])

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
            "gateway_name",
            sa.String(253),
            sa.ForeignKey("gateways.name", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.UniqueConstraint("sp_id", "gateway_name"),
    )
    op.create_index("ix_sp_gateway_roles_sp_id", "sp_gateway_roles", ["sp_id"])
    op.create_index("ix_sp_gateway_roles_gateway_name", "sp_gateway_roles", ["gateway_name"])


def downgrade() -> None:
    """Drop gateway-scoped role tables."""
    op.drop_index("ix_sp_gateway_roles_gateway_name", table_name="sp_gateway_roles")
    op.drop_index("ix_sp_gateway_roles_sp_id", table_name="sp_gateway_roles")
    op.drop_table("sp_gateway_roles")
    op.drop_index("ix_user_gateway_roles_gateway_name", table_name="user_gateway_roles")
    op.drop_index("ix_user_gateway_roles_user_id", table_name="user_gateway_roles")
    op.drop_table("user_gateway_roles")
