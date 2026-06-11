"""Add kill_switch_entries table.

Stores which providers were detached from which sandbox while the
gateway kill switch is engaged, so ``resume`` can re-attach them.

The ``v2_baseline`` revision creates the schema with
``Base.metadata.create_all`` — on a fresh database this table therefore
already exists when this migration runs, so both directions are guarded
with an existence check. Rollback path: drop the table (kill-switch
state is reconstructible by re-engaging; no irreplaceable data).

Revision ID: 101_kill_switch
Revises: v2_baseline
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "101_kill_switch"
down_revision: str | None = "v2_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "kill_switch_entries"


def upgrade() -> None:
    """Create the kill_switch_entries table (skipped if baseline made it)."""
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("gateway", sa.String(length=253), nullable=False),
        sa.Column("sandbox", sa.String(length=253), nullable=False),
        sa.Column("providers_json", sa.Text(), nullable=False),
        sa.Column("engaged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("engaged_by", sa.String(), nullable=False),
    )
    op.create_index("ix_kill_switch_entries_gateway", _TABLE, ["gateway"])


def downgrade() -> None:
    """Drop the kill_switch_entries table."""
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE):
        op.drop_table(_TABLE)
