"""SQLAlchemy ORM models for Shoreguard."""

from __future__ import annotations

from sqlalchemy import Integer, LargeBinary, String, Text
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


class ApiKey(Base):
    """An API key with an assigned role."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    last_used: Mapped[str | None] = mapped_column(String)
