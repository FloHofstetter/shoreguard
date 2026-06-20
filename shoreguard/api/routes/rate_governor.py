"""REST endpoints for per-sandbox inference rate ceilings.

Gateway-scoped rate-limit CRUD plus pause status (mounted under
``/api/gateways/{gw}/sandboxes``), and a global fleet-wide list of active
soft-pauses. The governor itself runs in the ``rate_governor`` background
task — see :mod:`shoreguard.services.rate_governor`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_gateway_name, get_services
from shoreguard.services.audit import audit_log

logger = logging.getLogger(__name__)

router = APIRouter()
summary_router = APIRouter()


class RateLimitRequest(BaseModel):
    """Rate-limit upsert payload.

    Attributes:
        max_requests: Inference request ceiling within the window.
        window_seconds: Tumbling window length in seconds.
        enabled: Whether the governor evaluates this limit.
    """

    max_requests: int = Field(ge=1)
    window_seconds: int = Field(default=60, ge=1)
    enabled: bool = True


@router.get("/{name}/rate-limit")
async def get_rate_limit(request: Request, name: str) -> dict[str, Any]:
    """Return the rate limit configured for a sandbox (or null).

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.

    Returns:
        dict[str, Any]: ``{"rate_limit": {...} | None, "enabled": bool}``.
    """
    from shoreguard.settings import get_settings

    gateway = get_gateway_name(request)
    rate_limit = await get_services().rate_governor.get_rate_limit(gateway, name)
    return {"rate_limit": rate_limit, "enabled": get_settings().rate_governor.enabled}


@router.put("/{name}/rate-limit", dependencies=[Depends(require_role("admin"))])
async def put_rate_limit(request: Request, name: str, body: RateLimitRequest) -> dict[str, Any]:
    """Create or update a sandbox rate limit.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.
        body: Rate-limit parameters.

    Returns:
        dict[str, Any]: The saved rate-limit record.

    Raises:
        HTTPException: 400 on invalid parameters.
    """
    gateway = get_gateway_name(request)
    try:
        rate_limit = await get_services().rate_governor.set_rate_limit(
            gateway,
            name,
            max_requests=body.max_requests,
            window_seconds=body.window_seconds,
            enabled=body.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_log(
        request,
        "rate.set",
        "sandbox",
        name,
        gateway=gateway,
        detail={"max_requests": body.max_requests, "window_seconds": body.window_seconds},
    )
    return rate_limit


@router.delete("/{name}/rate-limit", dependencies=[Depends(require_role("admin"))])
async def delete_rate_limit(request: Request, name: str) -> dict[str, Any]:
    """Remove a sandbox rate limit.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.

    Returns:
        dict[str, Any]: ``{"deleted": bool}``.
    """
    gateway = get_gateway_name(request)
    deleted = await get_services().rate_governor.delete_rate_limit(gateway, name)
    if deleted:
        await audit_log(request, "rate.delete", "sandbox", name, gateway=gateway)
    return {"deleted": deleted}


@router.get("/{name}/rate-status")
async def get_rate_status(request: Request, name: str) -> dict[str, Any]:
    """Return the rate limit plus current soft-pause state for a sandbox.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.

    Returns:
        dict[str, Any]: ``{"rate_limit", "paused", "resume_after"}``.
    """
    gateway = get_gateway_name(request)
    return await get_services().rate_governor.status(gateway, name)


@summary_router.get("/paused")
async def list_paused() -> dict[str, Any]:
    """Return all active rate-governor soft-pauses across the fleet.

    Returns:
        dict[str, Any]: ``{"items": [...]}``.
    """
    return {"items": await get_services().rate_governor.list_paused()}
