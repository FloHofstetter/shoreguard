"""v2 baseline — squashed schema (replaces revisions 001–017).

The original 17-step chain accumulated during v0.x development was
squashed into this single baseline for the v0.38 architecture
redesign. The schema is created directly from the SQLAlchemy models,
so the baseline can never drift from ``shoreguard.models``.

Upgrade path for existing databases: ``init_db()`` detects a database
sitting at the final pre-squash revision (``017``, the v0.37 head) and
stamps it to this baseline — the schema is identical, no DDL runs.
Databases on older revisions must upgrade through ShoreGuard v0.37
first.

Revision ID: v2_baseline
Revises:
Create Date: 2026-06-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "v2_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the full current schema from the SQLAlchemy models."""
    from shoreguard.models import Base

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    """Drop every model-managed table (full teardown to an empty database)."""
    from shoreguard.models import Base

    Base.metadata.drop_all(bind=op.get_bind())
