"""REST endpoints for sandbox inference budgets and usage.

Gateway-scoped budget CRUD plus usage queries (mounted under
``/api/gateways/{gw}/sandboxes``), and a global top-consumers summary
for the dashboard. Metering itself runs in the ``usage_metering``
background task — see :mod:`shoreguard.services.budgets`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_gateway_name, get_services
from shoreguard.services.audit import audit_log

logger = logging.getLogger(__name__)

router = APIRouter()
summary_router = APIRouter()


class BudgetRequest(BaseModel):
    """Budget upsert payload.

    Attributes:
        limit_requests: Inference request ceiling for the window.
        window: ``daily`` / ``weekly`` / ``monthly`` / ``total``.
        action: ``notify`` or ``detach``.
    """

    limit_requests: int = Field(ge=1)
    window: str = "daily"
    action: str = "notify"


@router.get("/{name}/budget")
async def get_budget(request: Request, name: str) -> dict[str, Any]:
    """Return the budget configured for a sandbox (or null).

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.

    Returns:
        dict[str, Any]: ``{"budget": {...} | None, "metering_enabled"}``.
    """
    from shoreguard.settings import get_settings

    gateway = get_gateway_name(request)
    budget = await get_services().budget.get_budget(gateway, name)
    return {"budget": budget, "metering_enabled": get_settings().budget.metering_enabled}


@router.put("/{name}/budget", dependencies=[Depends(require_role("admin"))])
async def put_budget(request: Request, name: str, body: BudgetRequest) -> dict[str, Any]:
    """Create or update a sandbox budget.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.
        body: Budget parameters.

    Returns:
        dict[str, Any]: The saved budget record.

    Raises:
        HTTPException: 400 on invalid window/action/limit.
    """
    gateway = get_gateway_name(request)
    try:
        budget = await get_services().budget.set_budget(
            gateway,
            name,
            limit_requests=body.limit_requests,
            window=body.window,
            action=body.action,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_log(
        request,
        "budget.set",
        "sandbox",
        name,
        gateway=gateway,
        detail={"limit": body.limit_requests, "window": body.window, "action": body.action},
    )
    return budget


@router.delete("/{name}/budget", dependencies=[Depends(require_role("admin"))])
async def delete_budget(request: Request, name: str) -> dict[str, Any]:
    """Remove a sandbox budget.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.

    Returns:
        dict[str, Any]: ``{"deleted": bool}``.
    """
    gateway = get_gateway_name(request)
    deleted = await get_services().budget.delete_budget(gateway, name)
    if deleted:
        await audit_log(request, "budget.delete", "sandbox", name, gateway=gateway)
    return {"deleted": deleted}


@router.get("/{name}/usage")
async def get_usage(
    request: Request, name: str, days: int = Query(default=7, ge=1, le=90)
) -> dict[str, Any]:
    """Return per-day inference usage for a sandbox.

    Args:
        request: Incoming HTTP request.
        name: Sandbox name.
        days: Trailing days to include.

    Returns:
        dict[str, Any]: Usage rows, today's count, budget, window usage.
    """
    gateway = get_gateway_name(request)
    return await get_services().budget.usage(gateway, name, days=days)


@summary_router.get("/summary")
async def usage_summary(days: int = Query(default=7, ge=1, le=90)) -> dict[str, Any]:
    """Return the top inference consumers across all gateways.

    Args:
        days: Trailing window for the totals.

    Returns:
        dict[str, Any]: ``{"since", "top": [...]}``.
    """
    return await get_services().budget.summary(days=days)
