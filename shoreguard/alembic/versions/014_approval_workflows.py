"""Add approval_workflows and approval_decisions tables (M19 quorum).

Multi-stage approvals: a workflow row configures required_approvals per
sandbox; decision rows accumulate votes and are cleared once quorum fires
the upstream gateway approve.

Revision ID: 014
Revises: 013
Create Date: 2026-04-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: str | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create approval_workflows and approval_decisions tables."""
    op.create_table(
        "approval_workflows",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("gateway_name", sa.String(253), nullable=False),
        sa.Column("sandbox_name", sa.String(253), nullable=False),
        sa.Column("required_approvals", sa.Integer, nullable=False, server_default="2"),
        sa.Column("required_roles_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column(
            "distinct_actors",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("escalation_timeout_minutes", sa.Integer, nullable=True),
        sa.Column("created_by", sa.String(254), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("gateway_name", "sandbox_name"),
    )
    op.create_table(
        "approval_decisions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "workflow_id",
            sa.Integer,
            sa.ForeignKey("approval_workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gateway_name", sa.String(253), nullable=False),
        sa.Column("sandbox_name", sa.String(253), nullable=False),
        sa.Column("chunk_id", sa.String(128), nullable=False),
        sa.Column("actor", sa.String(254), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_approval_decisions_chunk",
        "approval_decisions",
        ["gateway_name", "sandbox_name", "chunk_id"],
    )


def downgrade() -> None:
    """Drop approval_decisions and approval_workflows tables."""
    op.drop_index("ix_approval_decisions_chunk", table_name="approval_decisions")
    op.drop_table("approval_decisions")
    op.drop_table("approval_workflows")
