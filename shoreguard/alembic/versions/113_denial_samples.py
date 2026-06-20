"""Add the persisted denial-sample table (Policy Simulator slice B).

Durable corpus of L7 denial samples (fed from ``SubmitPolicyAnalysis``)
so the policy simulator can replay them against a candidate policy after
a restart — the live denial cache is in-memory and volatile. Guarded with
existence checks like ``102_budgets`` because the ``v2_baseline`` revision
creates the full model schema on fresh databases. Rollback path: drop the
table (pure observational data — re-populates from future analyses).

Revision ID: 113_denial_samples
Revises: 112_rate_governor
Create Date: 2026-06-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "113_denial_samples"
down_revision: str | None = "112_rate_governor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the denial-sample table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("denial_samples"):
        op.create_table(
            "denial_samples",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("gateway", sa.String(length=253), nullable=False),
            sa.Column("sandbox", sa.String(length=253), nullable=False),
            sa.Column("binary", sa.String(length=512), nullable=False),
            sa.Column("host", sa.String(length=253), nullable=False),
            sa.Column("port", sa.Integer(), nullable=False),
            sa.Column("l7_samples_json", sa.Text(), nullable=False),
            sa.Column("deny_reason", sa.Text(), nullable=False),
            sa.Column("count", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "gateway", "sandbox", "binary", "host", "port", name="uq_denial_sample"
            ),
        )
        op.create_index("ix_denial_samples_sb", "denial_samples", ["gateway", "sandbox"])


def downgrade() -> None:
    """Drop the denial-sample table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("denial_samples"):
        op.drop_table("denial_samples")
