"""Hash invite tokens instead of storing them in plaintext.

Revision ID: 004
Revises: 003
Create Date: 2026-03-29
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add invite_token_hash column, hash existing tokens, drop plaintext column."""
    op.add_column("users", sa.Column("invite_token_hash", sa.String(64), nullable=True))

    # Hash existing plaintext invite tokens
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, invite_token FROM users WHERE invite_token IS NOT NULL")
    )
    for row in rows:
        token_hash = hashlib.sha256(row.invite_token.encode()).hexdigest()
        conn.execute(
            sa.text("UPDATE users SET invite_token_hash = :h WHERE id = :id"),
            {"h": token_hash, "id": row.id},
        )

    op.drop_column("users", "invite_token")
    op.create_index("ix_users_invite_token_hash", "users", ["invite_token_hash"])


def downgrade() -> None:
    """This migration is non-reversible: SHA-256 hashes cannot be reversed.

    Downgrading restores the column structure but all pending invite tokens
    will be lost (set to NULL).  Affected users must be re-invited.
    """
    op.drop_index("ix_users_invite_token_hash", table_name="users")
    op.add_column("users", sa.Column("invite_token", sa.String(64), nullable=True))
    # SHA-256 hashes cannot be reversed — pending invites are invalidated.
    # Affected users must be re-invited after downgrade.
    op.drop_column("users", "invite_token_hash")
