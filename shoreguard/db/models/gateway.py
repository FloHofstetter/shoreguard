"""Gateway registry model."""

from __future__ import annotations

import datetime

from sqlalchemy import (
    DateTime,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from shoreguard.db.models.base import Base


class Gateway(Base):
    """A registered OpenShell gateway.

    Attributes:
        id: Auto-incremented primary key.
        name: Unique gateway name (max 253 chars).
        endpoint: Host:port of the gateway gRPC endpoint.
        scheme: Connection scheme (``https`` by default).
        auth_mode: Optional authentication mode identifier.
        ca_cert: Optional CA certificate bytes for mTLS.
        client_cert: Optional client certificate bytes for mTLS.
        client_key: Optional client private key bytes for mTLS.
        metadata_json: Optional JSON-encoded metadata blob.
        description: Optional free-text description of the gateway's purpose.
        labels_json: Optional JSON-encoded key-value labels for filtering.
        registered_at: Timestamp when the gateway was registered.
        last_seen: Timestamp of the most recent health check.
        last_status: Last known health status string.
    """

    __tablename__ = "gateways"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(253), unique=True, nullable=False)
    endpoint: Mapped[str] = mapped_column(String(260), nullable=False)
    scheme: Mapped[str] = mapped_column(String, nullable=False, default="https")
    auth_mode: Mapped[str | None] = mapped_column(String)
    ca_cert: Mapped[bytes | None] = mapped_column(LargeBinary)
    client_cert: Mapped[bytes | None] = mapped_column(LargeBinary)
    client_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    metadata_json: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    labels_json: Mapped[str | None] = mapped_column(Text)
    registered_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_seen: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str] = mapped_column(String, default="unknown")
