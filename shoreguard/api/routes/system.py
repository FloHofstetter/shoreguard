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
