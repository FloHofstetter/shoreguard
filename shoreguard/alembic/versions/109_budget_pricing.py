"""Add the estimated-dollar budget ceiling column.

Stage 1 of the Spend Governor: the optional ``limit_usd`` ceiling on
``sandbox_budgets`` lets a budget be expressed in estimated dollars
(from the pricing overlay) instead of a raw request count. Guarded with
a column-existence check — like ``102_budgets`` — because the
``v2_baseline`` revision creates the full model schema (including this
column) on fresh databases. Rollback path: drop the column (operator
config; the request-count budget keeps working).

Revision ID: 109_budget_pricing
Revises: 108_user_sessions
Create Date: 2026-06-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "109_budget_pricing"
down_revision: str | None = "108_user_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``sandbox_budgets.limit_usd`` (skipped if the baseline made it)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("sandbox_budgets")}
    if "limit_usd" not in columns:
        op.add_column("sandbox_budgets", sa.Column("limit_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    """Drop the estimated-dollar budget ceiling column."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("sandbox_budgets")}
    if "limit_usd" in columns:
        op.drop_column("sandbox_budgets", "limit_usd")
