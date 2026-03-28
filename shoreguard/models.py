"""SQLAlchemy ORM models for Shoreguard."""

from __future__ import annotations

from sqlalchemy import LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all Shoreguard models."""


class Gateway(Base):
    """A registered OpenShell gateway."""

    __tablename__ = "gateways"

    name: Mapped[str] = mapped_column(String(253), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(260), nullable=False)
    scheme: Mapped[str] = mapped_column(String, nullable=False, default="https")
    auth_mode: Mapped[str | None] = mapped_column(String)
    ca_cert: Mapped[bytes | None] = mapped_column(LargeBinary)
    client_cert: Mapped[bytes | None] = mapped_column(LargeBinary)
    client_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    metadata_json: Mapped[str | None] = mapped_column(Text)
    registered_at: Mapped[str] = mapped_column(String, nullable=False)
    last_seen: Mapped[str | None] = mapped_column(String)
    last_status: Mapped[str] = mapped_column(String, default="unknown")
