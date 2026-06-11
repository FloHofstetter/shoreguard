"""Add the tamper-evident hash chain columns to the audit log.

Every new audit row hashes its own fields together with the previous
row's hash, making silent edits detectable (``shoreguard audit verify``
or ``GET /api/audit/verify``). Existing rows stay ``NULL`` — the chain
starts with the first row written after this upgrade. Guarded with
existence checks because the ``v2_baseline`` revision creates the full
model schema on fresh databases. Rollback path: drop the two columns
(verification simply reports nothing to check).

Revision ID: 105_audit_hash_chain
Revises: 104_webauthn_credentials
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "105_audit_hash_chain"
down_revision: str | None = "104_webauthn_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add prev_hash / entry_hash (skipped if the baseline made them)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("audit_log")}
    if "prev_hash" not in columns:
        op.add_column("audit_log", sa.Column("prev_hash", sa.String(length=64), nullable=True))
    if "entry_hash" not in columns:
        op.add_column("audit_log", sa.Column("entry_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Drop the hash chain columns."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("audit_log")}
    if "entry_hash" in columns:
        op.drop_column("audit_log", "entry_hash")
    if "prev_hash" in columns:
        op.drop_column("audit_log", "prev_hash")
