"""Long-running operation record model."""

from __future__ import annotations

import datetime

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from shoreguard.db.models.base import Base


class OperationRecord(Base):
    """A tracked long-running operation with DB persistence.

    Attributes:
        id: UUID primary key.
        status: Lifecycle state (pending → running → succeeded/failed, or cancelling → failed).
        resource_type: Type of resource (sandbox, exec, gateway).
        resource_key: Resource identifier for duplicate detection.
        idempotency_key: Optional client-provided key for idempotent requests.
        progress_pct: Progress percentage (0-100).
        progress_msg: Human-readable progress message.
        result_json: JSON-encoded result payload on success.
        error_message: Error description on failure.
        error_code: Machine-readable error code (timeout, cancelled, etc.).
        actor: Identity of the user who started the operation.
        gateway_name: Gateway the operation targets.
        created_at: When the operation was created.
        updated_at: When the operation was last updated.
        completed_at: When the operation finished.
    """

    __tablename__ = "operations"
    __table_args__ = (
        Index("ix_operations_status", "status"),
        Index("ix_operations_resource", "resource_type", "resource_key"),
        Index(
            "ix_operations_active_resource",
            "resource_type",
            "resource_key",
            unique=True,
            sqlite_where=text("status IN ('pending', 'running', 'cancelling')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(253), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(253), unique=True)
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_msg: Mapped[str | None] = mapped_column(String(500))
    result_json: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(50))
    actor: Mapped[str | None] = mapped_column(String(254))
    gateway_name: Mapped[str | None] = mapped_column(String(253))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
