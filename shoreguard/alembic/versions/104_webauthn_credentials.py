"""Add the webauthn_credentials table for passkey login.

One row per registered passkey, cascade-deleted with the owning user.
Guarded with existence checks because the ``v2_baseline`` revision
creates the full model schema on fresh databases. Rollback path: drop
the table (users fall back to password/OIDC login and re-register).

Revision ID: 104_webauthn_credentials
Revises: 103_push_subscriptions
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "104_webauthn_credentials"
down_revision: str | None = "103_push_subscriptions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create webauthn_credentials (skipped if the baseline made it)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("webauthn_credentials"):
        op.create_table(
            "webauthn_credentials",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("credential_id", sa.String(length=512), nullable=False, unique=True),
            sa.Column("public_key", sa.Text(), nullable=False),
            sa.Column("sign_count", sa.Integer(), nullable=False),
            sa.Column("transports", sa.String(length=255), nullable=True),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_used", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_webauthn_credentials_user_id", "webauthn_credentials", ["user_id"])


def downgrade() -> None:
    """Drop the webauthn_credentials table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("webauthn_credentials"):
        op.drop_table("webauthn_credentials")
