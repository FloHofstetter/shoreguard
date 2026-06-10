"""SBOM snapshot and component models."""

from __future__ import annotations

import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shoreguard.db.models.base import Base


class SBOMSnapshot(Base):
    """A CycloneDX SBOM uploaded for a sandbox.

    One snapshot per ``(gateway, sandbox)`` pair — a new upload
    replaces the previous snapshot rather than appending. Historical
    snapshots are intentionally out of scope; if you need them,
    archive the raw CycloneDX in object storage from CI before
    uploading, because the ``raw_json`` column reflects only the
    latest upload.

    Attributes:
        id: Auto-incremented primary key.
        gateway_name: Gateway the sandbox belongs to.
        sandbox_name: Sandbox the SBOM describes.
        bom_format: CycloneDX-only for now ("CycloneDX").
        spec_version: CycloneDX spec version (e.g. "1.5").
        serial_number: Optional CycloneDX serialNumber URN.
        uploaded_by: Identity of the user who uploaded the snapshot.
        uploaded_at: When the snapshot was uploaded.
        component_count: Number of components in the SBOM.
        vulnerability_count: Number of vulnerabilities declared in the SBOM.
        max_severity: Highest severity across all vulnerabilities, or None.
        raw_json: The original CycloneDX JSON document, retained for download.
        components: Cascade-delete relationship to ``SBOMComponent`` rows.
    """

    __tablename__ = "sbom_snapshots"
    __table_args__ = (UniqueConstraint("gateway_name", "sandbox_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway_name: Mapped[str] = mapped_column(String(253), nullable=False, index=True)
    sandbox_name: Mapped[str] = mapped_column(String(253), nullable=False, index=True)
    bom_format: Mapped[str] = mapped_column(String(32), nullable=False)
    spec_version: Mapped[str] = mapped_column(String(16), nullable=False)
    serial_number: Mapped[str | None] = mapped_column(String(128))
    uploaded_by: Mapped[str] = mapped_column(String(254), nullable=False)
    uploaded_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    component_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vulnerability_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_severity: Mapped[str | None] = mapped_column(String(16))
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)

    components: Mapped[list[SBOMComponent]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class SBOMComponent(Base):
    """A single component row denormalised from a CycloneDX SBOM.

    Components are stored as flat rows so the components search
    endpoint can paginate and filter via SQL without re-parsing the
    raw CycloneDX JSON on each request. The ``vuln_count`` and
    ``max_severity`` columns are maintained at ingest time by
    joining through ``bom_ref`` against the document's
    ``vulnerabilities`` array, so the search endpoint never has to
    open the raw document.

    Attributes:
        id: Auto-incremented primary key.
        snapshot_id: Foreign key to the parent SBOM snapshot.
        bom_ref: CycloneDX bom-ref of the component (used to join vulns).
        name: Component name (e.g. "requests").
        version: Component version (e.g. "2.31.0").
        purl: Package URL (e.g. "pkg:pypi/requests@2.31.0").
        type: CycloneDX type (library, framework, container, ...).
        licenses: Comma-joined license identifiers.
        vuln_count: Number of vulnerabilities affecting this component.
        max_severity: Highest severity across the component's vulnerabilities.
        snapshot: Backref to the parent ``SBOMSnapshot`` row.
    """

    __tablename__ = "sbom_components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("sbom_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bom_ref: Mapped[str | None] = mapped_column(String(512), index=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    version: Mapped[str | None] = mapped_column(String(128))
    purl: Mapped[str | None] = mapped_column(String(1024), index=True)
    type: Mapped[str | None] = mapped_column(String(32))
    licenses: Mapped[str | None] = mapped_column(Text)
    vuln_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_severity: Mapped[str | None] = mapped_column(String(16))

    snapshot: Mapped[SBOMSnapshot] = relationship(back_populates="components")
