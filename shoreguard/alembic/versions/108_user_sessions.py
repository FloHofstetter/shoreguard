"""Add the user_sessions table for session listing and revocation.

One row per signed-in session, cascade-deleted with the owning user.
Guarded with existence checks because the ``v2_baseline`` revision
creates the full model schema on fresh databases. Rollback path: drop
the table (sessions fall back to stateless, non-revocable cookies).

Revision ID: 108_user_sessions
Revises: 107_device_link_codes
Create Date: 2026-06-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "108_user_sessions"
down_revision: str | None = "107_device_link_codes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create user_sessions (skipped if the baseline made it)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("user_sessions"):
        op.create_table(
            "user_sessions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("session_id", sa.String(length=64), nullable=False, unique=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("kind", sa.String(length=20), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ip", sa.String(length=64), nullable=True),
            sa.Column("user_agent", sa.String(length=512), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])


def downgrade() -> None:
    """Drop the user_sessions table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("user_sessions"):
        op.drop_table("user_sessions")
