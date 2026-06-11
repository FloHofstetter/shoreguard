"""Add the push_subscriptions table for Web Push (PWA notifications).

One row per registered browser/device; the ``webpush`` webhook channel
fans out to every row. Guarded with existence checks because the
``v2_baseline`` revision creates the full model schema on fresh
databases. Rollback path: drop the table (devices simply re-register).

Revision ID: 103_push_subscriptions
Revises: 102_budgets
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "103_push_subscriptions"
down_revision: str | None = "102_budgets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create push_subscriptions (skipped if the baseline made it)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("push_subscriptions"):
        op.create_table(
            "push_subscriptions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_email", sa.String(length=254), nullable=False),
            sa.Column("endpoint", sa.Text(), nullable=False, unique=True),
            sa.Column("p256dh", sa.String(length=255), nullable=False),
            sa.Column("auth", sa.String(length=255), nullable=False),
            sa.Column("user_agent", sa.String(length=512), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_push_subscriptions_user_email", "push_subscriptions", ["user_email"])


def downgrade() -> None:
    """Drop the push_subscriptions table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("push_subscriptions"):
        op.drop_table("push_subscriptions")
