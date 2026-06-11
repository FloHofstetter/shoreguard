"""Add the device_link_codes table for QR sign-in handoff.

One row per minted device-link code, cascade-deleted with the issuing
user. Guarded with existence checks because the ``v2_baseline``
revision creates the full model schema on fresh databases. Rollback
path: drop the table (outstanding codes are invalidated; operators
fall back to password/passkey login).

Revision ID: 107_device_link_codes
Revises: 106_gateway_curfews
Create Date: 2026-06-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "107_device_link_codes"
down_revision: str | None = "106_gateway_curfews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create device_link_codes (skipped if the baseline made it)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("device_link_codes"):
        op.create_table(
            "device_link_codes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("code_hash", sa.String(length=64), nullable=False, unique=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("redeemer_ip", sa.String(length=64), nullable=True),
            sa.Column("redeemer_user_agent", sa.String(length=512), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("denied_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_device_link_codes_user_id", "device_link_codes", ["user_id"])


def downgrade() -> None:
    """Drop the device_link_codes table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("device_link_codes"):
        op.drop_table("device_link_codes")
