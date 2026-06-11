"""REST endpoint for host resource stats (CPU / memory / GPU / disk).

One read-only route serving the cached sample from
:mod:`shoreguard.services.node_stats`. Scoped to the ShoreGuard host —
on a single-box deployment that is the gateway node.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_services

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/node-stats")
async def get_node_stats() -> dict[str, Any]:
    """Return CPU, memory, disk, and GPU stats for the ShoreGuard host.

    Returns:
        dict[str, Any]: Host stats sample (``scope: shoreguard-host``).
    """
    return await get_services().node_stats.collect()


@router.get("/node-alerts")
async def get_node_alerts() -> dict[str, Any]:
    """Return host threshold-alert configuration and current breaches.

    Returns:
        dict[str, Any]: Enabled flag, thresholds, and breached metrics.
    """
    return get_services().node_alerts.status()


class ProbeInferenceRequest(BaseModel):
    """Request body for probing a local inference endpoint.

    Attributes:
        base_url: OpenAI-compatible base URL on a private/LAN address.
    """

    base_url: str = Field(max_length=2048)


@router.post("/probe-inference", dependencies=[Depends(require_role("operator"))])
async def probe_inference(body: ProbeInferenceRequest) -> dict[str, Any]:
    """Probe an OpenAI-compatible endpoint on the LAN for served models.

    Lets the operator test ``base_url`` before creating a provider —
    e.g. Ollama on another homelab box. Restricted to private/LAN
    addresses; one read-only GET, nothing is stored.

    Args:
        body: The endpoint to probe.

    Returns:
        dict[str, Any]: ``{"ok", "models", "error"}``.
    """
    import asyncio

    from shoreguard.services.local_inference import probe_endpoint

    return await asyncio.to_thread(probe_endpoint, body.base_url)


@router.get("/access-urls")
async def get_access_urls() -> dict[str, Any]:
    """Return URLs under which other devices can reach this server.

    Backs the "Open on phone" QR dialog: when the operator browses via
    ``localhost``, the UI swaps in a LAN address from ``lan_urls`` — or
    warns that the server is bound to loopback only.

    Returns:
        dict[str, Any]: ``{"bind_host", "port", "loopback_only", "lan_urls"}``.
    """
    import asyncio

    from shoreguard.services.access_urls import access_urls

    return await asyncio.to_thread(access_urls)


@router.get("/updates")
async def get_update_status() -> dict[str, Any]:
    """Return update availability and gateway version skew.

    Returns:
        dict[str, Any]: Current/latest ShoreGuard version, check state,
        per-gateway OpenShell versions, and a skew flag.
    """
    return get_services().update_check.status()


@router.get("/backup", dependencies=[Depends(require_role("admin"))], response_model=None)
async def download_backup(request: Request) -> FileResponse:
    """Create a backup archive and stream it as a download (admin only).

    The archive contains the SQLite snapshot **and the secret-key
    material** — treat it like a credential.

    Args:
        request: The incoming HTTP request.

    Returns:
        FileResponse: The tar.gz backup archive.

    Raises:
        HTTPException: 400 when the deployment is not SQLite-backed
            (use ``pg_dump``).
    """
    import asyncio

    from shoreguard.services.audit import audit_log
    from shoreguard.services.backup import create_backup

    try:
        path = await asyncio.to_thread(create_backup)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_log(request, "backup.download", "system", path.name)
    return FileResponse(
        path,
        media_type="application/gzip",
        filename=path.name,
    )
