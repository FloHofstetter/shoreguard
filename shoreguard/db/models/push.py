"""Web Push subscription model."""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from shoreguard.db.models.base import Base


class PushSubscription(Base):
    """One browser push subscription registered from the PWA.

    A device (phone, laptop) registers its push endpoint after the user
    grants notification permission; the ``webpush`` webhook channel fans
    out to every stored subscription. Subscriptions are keyed by their
    endpoint URL — re-registering the same browser upserts.

    Attributes:
        id: Auto-incremented primary key.
        user_email: Email of the user who registered the device
            (``no-auth`` in dev-bypass mode).
        endpoint: Push-service endpoint URL (unique per browser).
        p256dh: Client public key for payload encryption (base64url).
        auth: Client auth secret for payload encryption (base64url).
        user_agent: Browser user-agent at registration, for display.
        created_at: When the device registered.
    """

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_email: Mapped[str] = mapped_column(String(254), nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    auth: Mapped[str] = mapped_column(String(255), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
