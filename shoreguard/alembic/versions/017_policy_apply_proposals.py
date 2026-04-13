"""Add policy_apply_proposals table.

Stores pending YAML policy apply requests for sandboxes that have an
active quorum approval workflow. Rows are keyed by a synthetic
``chunk_id`` derived from a sha256 prefix of the YAML body so
subsequent vote-only calls (potentially from different runners) can
reference the same proposal without having to resubmit the payload.

Revision ID: 017
Revises: 016
Create Date: 2026-04-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "017"
down_revision: str | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the policy_apply_proposals table."""
    op.create_table(
        "policy_apply_proposals",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("gateway_name", sa.String(253), nullable=False),
        sa.Column("sandbox_name", sa.String(253), nullable=False),
        sa.Column("chunk_id", sa.String(80), nullable=False),
        sa.Column("yaml_text", sa.Text, nullable=False),
        sa.Column("expected_hash", sa.String(80), nullable=True),
        sa.Column("proposed_by", sa.String(254), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "gateway_name",
            "sandbox_name",
            "chunk_id",
            name="uq_policy_apply_proposals_chunk",
        ),
    )


def downgrade() -> None:
    """Drop the policy_apply_proposals table."""
    op.drop_table("policy_apply_proposals")
