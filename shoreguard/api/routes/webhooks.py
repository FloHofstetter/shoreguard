"""REST endpoints for webhook management (admin only)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

import shoreguard.services.webhooks as webhook_mod
from shoreguard.api.schemas import (
    MessageResponse,
    PaginatedResponse,
    WebhookCreateResponse,
    WebhookDeliveryResponse,
    WebhookResponse,
)
from shoreguard.api.validation import (
    check_write_rate_limit,
    validate_smtp_host,
    validate_webhook_url,
)

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_CHANNEL_TYPES = ("generic", "slack", "discord", "email")


class WebhookCreateRequest(BaseModel):
    """Request body for creating a webhook.

    Attributes:
        url: Target URL for POST requests.
        event_types: List of event type strings to subscribe to.
        channel_type: Channel type (generic, slack, discord, email).
        extra_config: Optional channel-specific config (e.g. SMTP settings).
    """

    url: str = Field(max_length=2048)
    event_types: list[str] = Field(max_length=50)
    channel_type: str = "generic"
    extra_config: dict[str, Any] | None = None

    @field_validator("event_types")
    @classmethod
    def check_event_types(cls, v: list[str]) -> list[str]:
        """Enforce non-empty event types with max length."""
        for et in v:
            if not et.strip() or len(et) > 100:
                raise ValueError("each event_type must be non-empty and at most 100 chars")
        return v

    @field_validator("channel_type")
    @classmethod
    def check_channel_type(cls, v: str) -> str:
        """Restrict to known channel types."""
        if v not in VALID_CHANNEL_TYPES:
            raise ValueError(f"must be one of: {', '.join(VALID_CHANNEL_TYPES)}")
        return v


class WebhookUpdateRequest(BaseModel):
    """Request body for updating a webhook.

    Attributes:
        url: New target URL.
        event_types: New event type subscriptions.
        is_active: New active state.
        channel_type: New channel type.
        extra_config: New channel-specific config.
    """

    url: str | None = Field(default=None, max_length=2048)
    event_types: list[str] | None = Field(default=None, max_length=50)
    is_active: bool | None = None
    channel_type: str | None = None
    extra_config: dict[str, Any] | None = None

    @field_validator("event_types")
    @classmethod
    def check_event_types(cls, v: list[str] | None) -> list[str] | None:
        """Enforce non-empty event types with max length."""
        if v is None:
            return v
        for et in v:
            if not et.strip() or len(et) > 100:
                raise ValueError("each event_type must be non-empty and at most 100 chars")
        return v

    @field_validator("channel_type")
    @classmethod
    def check_channel_type(cls, v: str | None) -> str | None:
        """Restrict to known channel types."""
        if v is not None and v not in VALID_CHANNEL_TYPES:
            raise ValueError(f"must be one of: {', '.join(VALID_CHANNEL_TYPES)}")
        return v


def _get_svc() -> webhook_mod.WebhookService:
    if webhook_mod.webhook_service is None:
        raise HTTPException(503, "Webhook service not initialised")
    return webhook_mod.webhook_service


@router.get("", response_model=PaginatedResponse)
async def list_webhooks() -> dict[str, Any]:
    """List all registered webhooks.

    Returns:
        dict[str, Any]: Paginated webhook entries.
    """
    svc = _get_svc()
    items = await asyncio.to_thread(svc.list)
    return {"items": items, "total": len(items)}


@router.post("", status_code=201, response_model=WebhookCreateResponse)
async def create_webhook(body: WebhookCreateRequest, request: Request) -> dict[str, Any]:
    """Create a new webhook with an auto-generated signing secret.

    Args:
        body: Webhook creation parameters.
        request: The incoming HTTP request.

    Returns:
        dict[str, Any]: Created webhook including the secret.

    Raises:
        HTTPException: If channel type is invalid or email config is missing.
    """
    check_write_rate_limit(request)
    svc = _get_svc()
    validate_webhook_url(body.url)

    if body.channel_type == "email" and not body.extra_config:
        raise HTTPException(400, "Email channel requires extra_config with smtp_host and to_addrs")
    if body.channel_type == "email" and body.extra_config:
        if "smtp_host" not in body.extra_config or "to_addrs" not in body.extra_config:
            raise HTTPException(400, "Email extra_config must include smtp_host and to_addrs")
        validate_smtp_host(str(body.extra_config["smtp_host"]))

    extra_config_json = json.dumps(body.extra_config) if body.extra_config else None
    actor = getattr(request.state, "user_id", "unknown")
    result = await asyncio.to_thread(
        svc.create,
        url=body.url,
        event_types=body.event_types,
        created_by=str(actor),
        channel_type=body.channel_type,
        extra_config=extra_config_json,
    )
    logger.info(
        "Webhook created (id=%d, channel=%s, url=%s, actor=%s)",
        result["id"],
        body.channel_type,
        body.url,
        actor,
    )
    return result


@router.get("/{webhook_id}", response_model=WebhookResponse)
async def get_webhook(webhook_id: int) -> dict[str, Any]:
    """Get a webhook by ID.

    Args:
        webhook_id: Primary key of the webhook.

    Returns:
        dict[str, Any]: Webhook data.

    Raises:
        HTTPException: If webhook is not found.
    """
    svc = _get_svc()
    result = await asyncio.to_thread(svc.get, webhook_id)
    if result is None:
        raise HTTPException(404, "Webhook not found")
    return result


@router.put("/{webhook_id}", response_model=WebhookResponse)
async def update_webhook(
    webhook_id: int, body: WebhookUpdateRequest, request: Request
) -> dict[str, Any]:
    """Update an existing webhook.

    Args:
        webhook_id: Primary key of the webhook.
        body: Fields to update.
        request: The incoming HTTP request.

    Returns:
        dict[str, Any]: Updated webhook data.

    Raises:
        HTTPException: If webhook is not found or channel type is invalid.
    """
    check_write_rate_limit(request)
    svc = _get_svc()
    if body.url is not None:
        validate_webhook_url(body.url)
    if body.extra_config and "smtp_host" in body.extra_config:
        validate_smtp_host(str(body.extra_config["smtp_host"]))

    extra_config_json = json.dumps(body.extra_config) if body.extra_config else None
    result = await asyncio.to_thread(
        svc.update,
        webhook_id,
        url=body.url,
        event_types=body.event_types,
        is_active=body.is_active,
        channel_type=body.channel_type,
        extra_config=extra_config_json,
    )
    if result is None:
        raise HTTPException(404, "Webhook not found")
    return result


@router.delete("/{webhook_id}", status_code=204)
async def delete_webhook(webhook_id: int) -> None:
    """Delete a webhook by ID.

    Args:
        webhook_id: Primary key of the webhook.

    Raises:
        HTTPException: If webhook is not found.
    """
    svc = _get_svc()
    deleted = await asyncio.to_thread(svc.delete, webhook_id)
    if not deleted:
        raise HTTPException(404, "Webhook not found")


@router.post("/{webhook_id}/test", response_model=MessageResponse)
async def test_webhook(webhook_id: int) -> dict[str, str]:
    """Send a test event to a specific webhook.

    Args:
        webhook_id: Primary key of the webhook.

    Returns:
        dict[str, str]: Confirmation message.

    Raises:
        HTTPException: If webhook is not found.
    """
    svc = _get_svc()
    wh = await asyncio.to_thread(svc.get, webhook_id)
    if wh is None:
        raise HTTPException(404, "Webhook not found")
    await svc.fire(
        "webhook.test",
        {"webhook_id": wh["id"], "message": "Test event from Shoreguard"},
    )
    return {"status": "Test event sent"}


@router.get("/{webhook_id}/deliveries", response_model=list[WebhookDeliveryResponse])
async def list_deliveries(
    webhook_id: int, limit: int = Query(50, ge=1, le=500)
) -> list[dict[str, Any]]:
    """List recent delivery attempts for a webhook.

    Args:
        webhook_id: Primary key of the webhook.
        limit: Maximum number of records to return.

    Returns:
        list[dict[str, Any]]: Delivery records, newest first.

    Raises:
        HTTPException: If webhook is not found.
    """
    svc = _get_svc()
    wh = await asyncio.to_thread(svc.get, webhook_id)
    if wh is None:
        raise HTTPException(404, "Webhook not found")
    return await asyncio.to_thread(svc.list_deliveries, webhook_id, limit=limit)
