"""Webhook subscription and delivery schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

# ─── Webhooks ─────────────────────────────────────────────────────────────────


class WebhookResponse(BaseModel):
    """Webhook subscription record (without secret).

    Attributes:
        model_config (ConfigDict): Pydantic config.
        id (int): Webhook ID.
        url (str): Target URL invoked on delivery.
        event_types (list[str] | Any): Event type filter for this subscription.
        is_active (bool): Whether the webhook is currently active.
        channel_type (str): Delivery channel type (e.g. ``generic``, ``slack``).
        created_at (str | None): ISO timestamp when the webhook was created.
        created_by (str | None): Identifier of the user who created the webhook.
        extra_config (dict[str, Any] | None): Channel-specific extra configuration.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    url: str
    event_types: list[str] | Any = None
    is_active: bool = True
    channel_type: str = "generic"
    created_at: str | None = None
    created_by: str | None = None
    extra_config: dict[str, Any] | None = None


class WebhookCreateResponse(WebhookResponse):
    """Webhook creation response — includes the HMAC secret (shown only once).

    Attributes:
        secret (str): HMAC signing secret — returned only at creation time.
    """

    secret: str


class WebhookDeliveryResponse(BaseModel):
    """Webhook delivery attempt record.

    Attributes:
        model_config (ConfigDict): Pydantic config.
        id (int | None): Delivery attempt ID.
        webhook_id (int | None): ID of the webhook that was delivered.
        event_type (str | None): Event type of the delivered payload.
        status (str | None): Delivery status (e.g. ``success``, ``failed``).
        response_code (int | None): HTTP response code from the target.
        error_message (str | None): Error message if delivery failed.
        attempt (int | None): Attempt number (starting at 1).
        created_at (str | None): ISO timestamp when the attempt was created.
        delivered_at (str | None): ISO timestamp when the delivery completed.
    """

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    webhook_id: int | None = None
    event_type: str | None = None
    status: str | None = None
    response_code: int | None = None
    error_message: str | None = None
    attempt: int | None = None
    created_at: str | None = None
    delivered_at: str | None = None
