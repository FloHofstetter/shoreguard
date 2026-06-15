"""Add gateway inventory-snapshot and reap-record tables.

The restart reconciler snapshots each gateway's sandboxes + provider
attachments on every successful health probe, and on an
``unreachable → recovered`` transition diffs pre-down vs post-recovery to
record what a restart reaped. Both tables are append-only forensic history
(pruned by retention), not reversible state. Guarded with existence checks
like ``102_budgets`` because the ``v2_baseline`` revision creates the full
model schema on fresh databases. Rollback path: drop both tables (derived
data — re-captured next health pass).

Revision ID: 111_gateway_inventory
Revises: 110_tenants
Create Date: 2026-06-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "111_gateway_inventory"
down_revision: str | None = "110_tenants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the inventory-snapshot and reap-record tables."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("gateway_inventory_snapshots"):
        op.create_table(
            "gateway_inventory_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("gateway", sa.String(length=253), nullable=False),
            sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("sandboxes_json", sa.Text(), nullable=False),
            sa.Column("sandbox_count", sa.Integer(), nullable=False),
        )
        op.create_index(
            "ix_gateway_inventory_gw_time",
            "gateway_inventory_snapshots",
            ["gateway", "captured_at"],
        )
    if not inspector.has_table("gateway_reap_records"):
        op.create_table(
            "gateway_reap_records",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("gateway", sa.String(length=253), nullable=False),
            sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("recovered_from_status", sa.String(length=16), nullable=False),
            sa.Column("reaped_json", sa.Text(), nullable=False),
            sa.Column("reaped_count", sa.Integer(), nullable=False),
        )
        op.create_index(
            "ix_gateway_reap_gw_time",
            "gateway_reap_records",
            ["gateway", "detected_at"],
        )


def downgrade() -> None:
    """Drop the inventory-snapshot and reap-record tables."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in ("gateway_reap_records", "gateway_inventory_snapshots"):
        if inspector.has_table(table):
            op.drop_table(table)
