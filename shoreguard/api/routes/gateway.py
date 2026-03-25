"""REST endpoints for multi-gateway management and diagnostics."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from shoreguard.services.gateway import gateway_service

router = APIRouter()


class CreateGatewayRequest(BaseModel):
    """Request body for creating a new gateway."""

    name: str = "openshell"
    port: int | None = None
    remote_host: str | None = None
    gpu: bool = False


@router.get("/list")
async def gateway_list() -> list[dict[str, Any]]:
    """List all configured gateways with metadata and status."""
    return await asyncio.to_thread(gateway_service.list_all)


@router.get("/info")
async def gateway_info() -> dict[str, Any]:
    """Return active gateway configuration and connection status."""
    return await asyncio.to_thread(gateway_service.get_info)


@router.get("/config")
async def gateway_config() -> dict[str, Any]:
    """Get the global gateway configuration (settings and revision)."""
    return await asyncio.to_thread(gateway_service.get_config)


@router.get("/diagnostics")
async def gateway_diagnostics() -> dict[str, Any]:
    """Check Docker availability, daemon status, and permissions."""
    return await asyncio.to_thread(gateway_service.diagnostics)


# ─── Actions on active gateway ──────────────────────────────────────────────


@router.post("/start")
async def gateway_start_active() -> dict[str, Any]:
    """Start the active gateway."""
    return await asyncio.to_thread(gateway_service.start)


@router.post("/stop")
async def gateway_stop_active() -> dict[str, Any]:
    """Stop the active gateway."""
    return await asyncio.to_thread(gateway_service.stop)


@router.post("/restart")
async def gateway_restart_active() -> dict[str, Any]:
    """Restart the active gateway."""
    return await asyncio.to_thread(gateway_service.restart)


# ─── Actions on named gateway ───────────────────────────────────────────────


@router.post("/{name}/select")
async def gateway_select(name: str) -> dict[str, Any]:
    """Set a gateway as active and reconnect."""
    return await asyncio.to_thread(gateway_service.select, name)


@router.post("/{name}/start")
async def gateway_start_named(name: str) -> dict[str, Any]:
    """Start a specific gateway by name."""
    return await asyncio.to_thread(gateway_service.start, name)


@router.post("/{name}/stop")
async def gateway_stop_named(name: str) -> dict[str, Any]:
    """Stop a specific gateway by name."""
    return await asyncio.to_thread(gateway_service.stop, name)


@router.post("/{name}/restart")
async def gateway_restart_named(name: str) -> dict[str, Any]:
    """Restart a specific gateway by name."""
    return await asyncio.to_thread(gateway_service.restart, name)


@router.post("/{name}/destroy")
async def gateway_destroy(name: str) -> dict[str, Any]:
    """Destroy a gateway and remove its configuration."""
    return await asyncio.to_thread(gateway_service.destroy, name)


@router.post("/create")
async def gateway_create(body: CreateGatewayRequest) -> dict[str, Any]:
    """Create a new gateway."""
    return await asyncio.to_thread(
        gateway_service.create,
        name=body.name,
        port=body.port,
        remote_host=body.remote_host,
        gpu=body.gpu,
    )
