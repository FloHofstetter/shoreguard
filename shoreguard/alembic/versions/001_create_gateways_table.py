"""Create gateways table.

Revision ID: 001
Revises: None
Create Date: 2026-03-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the gateways table."""
    op.create_table(
        "gateways",
        sa.Column("name", sa.String(253), primary_key=True),
        sa.Column("endpoint", sa.String(260), nullable=False),
        sa.Column("scheme", sa.String(), nullable=False, server_default="https"),
        sa.Column("auth_mode", sa.String(), nullable=True),
        sa.Column("ca_cert", sa.LargeBinary(), nullable=True),
        sa.Column("client_cert", sa.LargeBinary(), nullable=True),
        sa.Column("client_key", sa.LargeBinary(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("registered_at", sa.String(), nullable=False),
        sa.Column("last_seen", sa.String(), nullable=True),
        sa.Column("last_status", sa.String(), nullable=False, server_default="unknown"),
    )
    op.create_index("ix_gateways_endpoint", "gateways", ["endpoint"])
    op.create_index("ix_gateways_registered_at", "gateways", ["registered_at"])


def downgrade() -> None:
    """Drop the gateways table."""
    op.drop_index("ix_gateways_registered_at", table_name="gateways")
    op.drop_index("ix_gateways_endpoint", table_name="gateways")
    op.drop_table("gateways")
