"""Add the gateway_curfews table (quiet hours / agent curfew).

One optional curfew per gateway: inside the configured window the
background task engages the reversible kill switch, outside it the
curfew-engaged switch is released. Guarded with existence checks
because the ``v2_baseline`` revision creates the full model schema on
fresh databases. Rollback path: drop the table (operator config that
can be re-entered).

Revision ID: 106_gateway_curfews
Revises: 105_audit_hash_chain
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "106_gateway_curfews"
down_revision: str | None = "105_audit_hash_chain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create gateway_curfews (skipped if the baseline made it)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("gateway_curfews"):
        op.create_table(
            "gateway_curfews",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("gateway", sa.String(length=253), nullable=False, unique=True),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("start_minute", sa.Integer(), nullable=False),
            sa.Column("end_minute", sa.Integer(), nullable=False),
            sa.Column("timezone", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    """Drop the gateway_curfews table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("gateway_curfews"):
        op.drop_table("gateway_curfews")
