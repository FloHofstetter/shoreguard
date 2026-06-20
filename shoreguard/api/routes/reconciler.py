"""REST endpoints for the gateway restart reconciler.

Read-only views over the inventory snapshots and reap records the health
loop maintains: per-gateway reap history and latest inventory (gateway
scoped), plus a fleet-wide recent-reaps feed and the "at risk on restart"
gateway list. Surfacing/diagnosing only — no write endpoints.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from shoreguard.api.deps import get_gateway_name, get_services

router = APIRouter()
summary_router = APIRouter()


@router.get("/reaps")
async def gateway_reaps(
    request: Request, limit: int = Query(default=50, ge=1, le=500)
) -> dict[str, Any]:
    """Return recent restart reap records for this gateway.

    Args:
        request: Incoming HTTP request.
        limit: Maximum number of records.

    Returns:
        dict[str, Any]: ``{"items": [...]}`` newest first.
    """
    gateway = get_gateway_name(request)
    items = await get_services().gateway_inventory.list_recent_reaps(limit=limit, gateway=gateway)
    return {"items": items}


@router.get("/inventory")
async def gateway_inventory(request: Request) -> dict[str, Any]:
    """Return the latest inventory snapshot for this gateway.

    Args:
        request: Incoming HTTP request.

    Returns:
        dict[str, Any]: ``{"snapshot": {...} | None}``.
    """
    gateway = get_gateway_name(request)
    snapshot = await get_services().gateway_inventory.latest_inventory(gateway)
    return {"snapshot": snapshot}


@summary_router.get("/recent")
async def recent_reaps(limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
    """Return recent restart reap records across all gateways.

    Args:
        limit: Maximum number of records.

    Returns:
        dict[str, Any]: ``{"items": [...]}`` newest first, fleet-wide.
    """
    items = await get_services().gateway_inventory.list_recent_reaps(limit=limit)
    return {"items": items}


@summary_router.get("/at-risk")
async def at_risk_gateways() -> dict[str, Any]:
    """Return gateways below the configured restart-safe version floor.

    Returns:
        dict[str, Any]: ``{"restart_safe_min_version", "at_risk_gateways"}``.
    """
    status = get_services().update_check.status()
    return {
        "restart_safe_min_version": status.get("restart_safe_min_version"),
        "at_risk_gateways": status.get("at_risk_gateways", []),
    }
