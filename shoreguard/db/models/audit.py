"""Append-only audit log model."""

from __future__ import annotations

import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from shoreguard.db.models.base import Base


class AuditEntry(Base):
    """A persistent audit log entry for state-changing operations.

    Attributes:
        id: Auto-incremented primary key.
        timestamp: When the action occurred.
        actor: Email or service principal name of the acting identity.
        actor_role: Effective role at time of action.
        action: Machine-readable action identifier.
        resource_type: Type of resource affected (e.g. ``sandbox``).
        resource_id: Identifier of the affected resource.
        gateway_name: Human-readable gateway name, if applicable.
        gateway_id: FK to the gateway, or ``None`` if deleted.
        detail: Optional free-text detail or JSON payload.
        client_ip: IP address of the requesting client, if available.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_timestamp", "timestamp"),
        Index("ix_audit_log_actor", "actor"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(254), nullable=False)
    actor_role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown", server_default="unknown"
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str] = mapped_column(
        String(253), nullable=False, default="", server_default=""
    )
    gateway_name: Mapped[str | None] = mapped_column(String(253))
    gateway_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("gateways.id", ondelete="SET NULL")
    )
    detail: Mapped[str | None] = mapped_column(Text)
    client_ip: Mapped[str | None] = mapped_column(String(45))
