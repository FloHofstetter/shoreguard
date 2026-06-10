"""Webhook subscription and delivery models."""

from __future__ import annotations

import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from shoreguard.db.models.base import Base


class Webhook(Base):
    """A webhook endpoint for event notifications.

    Attributes:
        id: Auto-incremented primary key.
        url: Target URL for POST requests (max 2048 chars).
        secret: HMAC-SHA256 signing secret.
        event_types: JSON-encoded list of subscribed event types.
        is_active: Whether the webhook is enabled.
        channel_type: Channel type (generic, slack, discord, email).
        extra_config: Optional JSON config for channel-specific settings.
        created_by: Email or service principal name of the creator.
        created_at: Timestamp when the webhook was created.
    """

    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret: Mapped[str] = mapped_column(String(128), nullable=False)
    event_types: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    channel_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="generic", server_default="generic"
    )
    extra_config: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(254), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WebhookDelivery(Base):
    """A delivery attempt for a webhook event.

    Attributes:
        id: Auto-incremented primary key.
        webhook_id: FK to the webhook that was triggered.
        event_type: The event type that triggered the delivery.
        payload_json: JSON-encoded event payload.
        status: Delivery status (``pending``, ``success``, ``failed``).
        response_code: HTTP response code from the target, if any.
        error_message: Error details on failure, if any.
        attempt: Current attempt number (1-based).
        created_at: Timestamp when the delivery was created.
        delivered_at: Timestamp when delivery succeeded, if any.
    """

    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_webhook_deliveries_webhook_id", "webhook_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    webhook_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    response_code: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
