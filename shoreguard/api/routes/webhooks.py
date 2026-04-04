"""REST endpoints for webhook management (admin only)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import shoreguard.services.webhooks as webhook_mod

logger = logging.getLogger(__name__)

router = APIRouter()


class WebhookCreateRequest(BaseModel):
    """Request body for creating a webhook."""

    url: str
    event_types: list[str]


class WebhookUpdateRequest(BaseModel):
    """Request body for updating a webhook."""

    url: str | None = None
    event_types: list[str] | None = None
    is_active: bool | None = None


def _get_svc() -> webhook_mod.WebhookService:
    if webhook_mod.webhook_service is None:
        raise HTTPException(503, "Webhook service not initialised")
    return webhook_mod.webhook_service


@router.get("")
async def list_webhooks() -> list[dict[str, Any]]:
    """List all registered webhooks.

    Returns:
        list[dict[str, Any]]: All webhook entries.
    """
    svc = _get_svc()
    return await asyncio.to_thread(svc.list)


@router.post("", status_code=201)
async def create_webhook(body: WebhookCreateRequest, request: Request) -> dict[str, Any]:
    """Create a new webhook with an auto-generated signing secret.

    Args:
        body: Webhook creation parameters.
        request: The incoming HTTP request.

    Returns:
        dict[str, Any]: Created webhook including the secret.
    """
    svc = _get_svc()
    actor = getattr(request.state, "user_id", "unknown")
    result = await asyncio.to_thread(
        svc.create,
        url=body.url,
        event_types=body.event_types,
        created_by=str(actor),
    )
    logger.info("Webhook created (id=%d, url=%s, actor=%s)", result["id"], body.url, actor)
    return result


@router.get("/{webhook_id}")
async def get_webhook(webhook_id: int) -> dict[str, Any]:
    """Get a webhook by ID.

    Args:
        webhook_id: Primary key of the webhook.

    Returns:
        dict[str, Any]: Webhook data.
    """
    svc = _get_svc()
    result = await asyncio.to_thread(svc.get, webhook_id)
    if result is None:
        raise HTTPException(404, "Webhook not found")
    return result


@router.put("/{webhook_id}")
async def update_webhook(webhook_id: int, body: WebhookUpdateRequest) -> dict[str, Any]:
    """Update an existing webhook.

    Args:
        webhook_id: Primary key of the webhook.
        body: Fields to update.

    Returns:
        dict[str, Any]: Updated webhook data.
    """
    svc = _get_svc()
    result = await asyncio.to_thread(
        svc.update,
        webhook_id,
        url=body.url,
        event_types=body.event_types,
        is_active=body.is_active,
    )
    if result is None:
        raise HTTPException(404, "Webhook not found")
    return result


@router.delete("/{webhook_id}", status_code=204)
async def delete_webhook(webhook_id: int) -> None:
    """Delete a webhook by ID.

    Args:
        webhook_id: Primary key of the webhook.
    """
    svc = _get_svc()
    deleted = await asyncio.to_thread(svc.delete, webhook_id)
    if not deleted:
        raise HTTPException(404, "Webhook not found")


@router.post("/{webhook_id}/test")
async def test_webhook(webhook_id: int) -> dict[str, str]:
    """Send a test event to a specific webhook.

    Args:
        webhook_id: Primary key of the webhook.

    Returns:
        dict[str, str]: Confirmation message.
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
