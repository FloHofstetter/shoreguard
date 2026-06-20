"""Add rate-limit and rate-pause tables (Spend Governor stage 2).

Per-sandbox inference request-rate ceilings (``sandbox_rate_limits``) and
the reversible soft-pauses the governor engages when one is exceeded
(``rate_pause_entries``). The pause table is deliberately separate from
``kill_switch_entries`` so the governor never collides with the kill
switch / budget detach. Guarded with existence checks like ``102_budgets``
because the ``v2_baseline`` revision creates the full model schema on fresh
databases. Rollback path: drop both tables (rate limits are operator
config; pause entries are reversible runtime state — resume re-attaches
first).

Revision ID: 112_rate_governor
Revises: 111_gateway_inventory
Create Date: 2026-06-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "112_rate_governor"
down_revision: str | None = "111_gateway_inventory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the rate-limit and rate-pause tables."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("sandbox_rate_limits"):
        op.create_table(
            "sandbox_rate_limits",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("gateway", sa.String(length=253), nullable=False),
            sa.Column("sandbox", sa.String(length=253), nullable=False),
            sa.Column("max_requests", sa.Integer(), nullable=False),
            sa.Column("window_seconds", sa.Integer(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("window_count_start", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("gateway", "sandbox", name="uq_sandbox_rate_limit"),
        )
    if not inspector.has_table("rate_pause_entries"):
        op.create_table(
            "rate_pause_entries",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("gateway", sa.String(length=253), nullable=False),
            sa.Column("sandbox", sa.String(length=253), nullable=False),
            sa.Column("providers_json", sa.Text(), nullable=False),
            sa.Column("paused_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resume_after", sa.DateTime(timezone=True), nullable=False),
            sa.Column("reason", sa.String(length=32), nullable=False),
            sa.UniqueConstraint("gateway", "sandbox", name="uq_rate_pause"),
        )
        op.create_index("ix_rate_pause_entries_gateway", "rate_pause_entries", ["gateway"])


def downgrade() -> None:
    """Drop the rate-limit and rate-pause tables."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in ("rate_pause_entries", "sandbox_rate_limits"):
        if inspector.has_table(table):
            op.drop_table(table)
