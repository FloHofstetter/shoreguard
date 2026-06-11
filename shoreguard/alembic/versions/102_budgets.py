"""Add sandbox budget / usage / cursor tables.

Phase-1 spend guardrails: per-sandbox inference-request counters
(metered from gateway logs), per-sandbox budgets, and the log-poll
cursors. Like ``101_kill_switch``, both directions are guarded with
existence checks because the ``v2_baseline`` revision creates the full
model schema on fresh databases. Rollback path: drop the three tables
(usage counters are derived data; budgets are operator config that can
be re-entered).

Revision ID: 102_budgets
Revises: 101_kill_switch
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "102_budgets"
down_revision: str | None = "101_kill_switch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create budget, usage, and cursor tables (skipped if baseline made them)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("sandbox_budgets"):
        op.create_table(
            "sandbox_budgets",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("gateway", sa.String(length=253), nullable=False),
            sa.Column("sandbox", sa.String(length=253), nullable=False),
            sa.Column("limit_requests", sa.Integer(), nullable=False),
            sa.Column("window", sa.String(length=16), nullable=False),
            sa.Column("action", sa.String(length=16), nullable=False),
            sa.Column("notified_key", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("gateway", "sandbox", name="uq_sandbox_budget"),
        )
    if not inspector.has_table("sandbox_usage"):
        op.create_table(
            "sandbox_usage",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("gateway", sa.String(length=253), nullable=False),
            sa.Column("sandbox", sa.String(length=253), nullable=False),
            sa.Column("day", sa.String(length=10), nullable=False),
            sa.Column("requests", sa.Integer(), nullable=False),
            sa.UniqueConstraint("gateway", "sandbox", "day", name="uq_sandbox_usage_day"),
        )
        op.create_index("ix_sandbox_usage_day", "sandbox_usage", ["day"])
    if not inspector.has_table("usage_cursors"):
        op.create_table(
            "usage_cursors",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("gateway", sa.String(length=253), nullable=False),
            sa.Column("sandbox", sa.String(length=253), nullable=False),
            sa.Column("last_ms", sa.BigInteger(), nullable=False),
            sa.UniqueConstraint("gateway", "sandbox", name="uq_usage_cursor"),
        )


def downgrade() -> None:
    """Drop the budget, usage, and cursor tables."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in ("usage_cursors", "sandbox_usage", "sandbox_budgets"):
        if inspector.has_table(table):
            op.drop_table(table)
