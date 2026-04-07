"""Add indexes for audit log queries.

Improves query performance for common filter patterns:
- audit_log: timestamp (sort), actor (filter)

Note: webhook_deliveries.webhook_id index already exists (migration 005).

Revision ID: 010
Revises: 009
Create Date: 2026-04-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add indexes for audit log."""
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_timestamp ON audit_log (timestamp)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_actor ON audit_log (actor)")


def downgrade() -> None:
    """Remove added indexes."""
    op.execute("DROP INDEX IF EXISTS ix_audit_log_actor")
    op.execute("DROP INDEX IF EXISTS ix_audit_log_timestamp")
