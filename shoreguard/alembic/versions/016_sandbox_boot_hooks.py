"""Add sandbox_boot_hooks table (M22 boot hooks).

Stores ShoreGuard-side pre/post-create hooks for a sandbox. Pre-create
hooks act as validation gates evaluated locally before CreateSandbox;
post-create hooks execute commands inside the sandbox via ExecSandbox.

Revision ID: 016
Revises: 015
Create Date: 2026-04-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the sandbox_boot_hooks table."""
    op.create_table(
        "sandbox_boot_hooks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("gateway_name", sa.String(253), nullable=False),
        sa.Column("sandbox_name", sa.String(253), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("command", sa.Text, nullable=False),
        sa.Column("workdir", sa.String(512), nullable=False, server_default=""),
        sa.Column("env_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("timeout_seconds", sa.Integer, nullable=False, server_default="30"),
        sa.Column("order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("1")),
        sa.Column(
            "continue_on_failure",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("created_by", sa.String(254), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(16), nullable=True),
        sa.Column("last_output", sa.Text, nullable=True),
        sa.UniqueConstraint(
            "gateway_name",
            "sandbox_name",
            "phase",
            "name",
            name="uq_sandbox_boot_hooks_name",
        ),
    )
    op.create_index(
        "ix_sandbox_boot_hooks_lookup",
        "sandbox_boot_hooks",
        ["gateway_name", "sandbox_name", "phase", "order"],
    )


def downgrade() -> None:
    """Drop the sandbox_boot_hooks table."""
    op.drop_index("ix_sandbox_boot_hooks_lookup", table_name="sandbox_boot_hooks")
    op.drop_table("sandbox_boot_hooks")
