"""Add sbom_snapshots and sbom_components tables.

One snapshot per ``(gateway, sandbox)`` — uploads replace the
prior snapshot rather than appending. Components are denormalised
into a separate table so the component search endpoint can
paginate and filter via SQL without re-parsing the raw CycloneDX
JSON on each request.

Revision ID: 015
Revises: 014
Create Date: 2026-04-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create sbom_snapshots and sbom_components tables."""
    op.create_table(
        "sbom_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("gateway_name", sa.String(253), nullable=False),
        sa.Column("sandbox_name", sa.String(253), nullable=False),
        sa.Column("bom_format", sa.String(32), nullable=False),
        sa.Column("spec_version", sa.String(16), nullable=False),
        sa.Column("serial_number", sa.String(128), nullable=True),
        sa.Column("uploaded_by", sa.String(254), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("component_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("vulnerability_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_severity", sa.String(16), nullable=True),
        sa.Column("raw_json", sa.Text, nullable=False),
        sa.UniqueConstraint("gateway_name", "sandbox_name"),
    )
    op.create_index(
        "ix_sbom_snapshots_gateway",
        "sbom_snapshots",
        ["gateway_name"],
    )
    op.create_index(
        "ix_sbom_snapshots_sandbox",
        "sbom_snapshots",
        ["sandbox_name"],
    )
    op.create_table(
        "sbom_components",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "snapshot_id",
            sa.Integer,
            sa.ForeignKey("sbom_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bom_ref", sa.String(512), nullable=True),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("version", sa.String(128), nullable=True),
        sa.Column("purl", sa.String(1024), nullable=True),
        sa.Column("type", sa.String(32), nullable=True),
        sa.Column("licenses", sa.Text, nullable=True),
        sa.Column("vuln_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_severity", sa.String(16), nullable=True),
    )
    op.create_index("ix_sbom_components_snapshot", "sbom_components", ["snapshot_id"])
    op.create_index("ix_sbom_components_name", "sbom_components", ["name"])
    op.create_index("ix_sbom_components_purl", "sbom_components", ["purl"])
    op.create_index("ix_sbom_components_bom_ref", "sbom_components", ["bom_ref"])


def downgrade() -> None:
    """Drop sbom_components and sbom_snapshots tables."""
    op.drop_index("ix_sbom_components_bom_ref", table_name="sbom_components")
    op.drop_index("ix_sbom_components_purl", table_name="sbom_components")
    op.drop_index("ix_sbom_components_name", table_name="sbom_components")
    op.drop_index("ix_sbom_components_snapshot", table_name="sbom_components")
    op.drop_table("sbom_components")
    op.drop_index("ix_sbom_snapshots_sandbox", table_name="sbom_snapshots")
    op.drop_index("ix_sbom_snapshots_gateway", table_name="sbom_snapshots")
    op.drop_table("sbom_snapshots")
