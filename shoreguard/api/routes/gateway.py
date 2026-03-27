"""REST endpoints for multi-gateway management and diagnostics."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import grpc
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from shoreguard.exceptions import friendly_grpc_error
from shoreguard.services.gateway import gateway_service
from shoreguard.services.operations import operation_store

logger = logging.getLogger("shoreguard")

_VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")

router = APIRouter()

_background_tasks: set[asyncio.Task] = set()


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
async def gateway_destroy(name: str, force: bool = False) -> dict[str, Any]:
    """Destroy a gateway and remove its configuration."""
    return await asyncio.to_thread(gateway_service.destroy, name, force=force)


@router.post("/create", status_code=202)
async def gateway_create(body: CreateGatewayRequest) -> dict[str, Any]:
    """Create a new gateway. Returns 202 with an operation ID for polling."""
    if not _VALID_NAME_RE.match(body.name):
        raise HTTPException(400, "Invalid gateway name: must match [a-zA-Z0-9][a-zA-Z0-9._-]*")
    op = operation_store.create_if_not_running("gateway", body.name)
    if op is None:
        raise HTTPException(409, f"Gateway '{body.name}' creation already in progress")

    async def _run() -> None:
        logger.info("Starting gateway creation: '%s' (op=%s)", body.name, op.id)
        try:
            result = await asyncio.to_thread(
                gateway_service.create,
                name=body.name,
                port=body.port,
                remote_host=body.remote_host,
                gpu=body.gpu,
            )
            if result.get("success") is False:
                operation_store.fail(op.id, result.get("error", "Gateway creation failed"))
            else:
                logger.info("Gateway creation completed: '%s' (op=%s)", body.name, op.id)
                operation_store.complete(op.id, result)
        except asyncio.CancelledError:
            logger.warning("Gateway creation cancelled for '%s'", body.name)
            operation_store.fail(op.id, "Operation was cancelled")
        except (grpc.RpcError, OSError, TimeoutError, RuntimeError) as e:
            logger.error("Gateway creation failed for '%s': %s", body.name, e, exc_info=True)
            msg = (
                friendly_grpc_error(e)
                if isinstance(e, grpc.RpcError)
                else "Gateway creation failed unexpectedly"
            )
            try:
                operation_store.fail(op.id, msg)
            except Exception:
                logger.exception("Failed to record operation failure for %s", op.id)
        except Exception:
            logger.exception("Gateway creation failed unexpectedly for '%s'", body.name)
            try:
                operation_store.fail(op.id, "Unexpected internal error")
            except Exception:
                logger.exception("Failed to record operation failure for %s", op.id)

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"operation_id": op.id, "status": "running", "resource_type": "gateway"}
