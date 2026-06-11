"""REST endpoint for host resource stats (CPU / memory / GPU / disk).

One read-only route serving the cached sample from
:mod:`shoreguard.services.node_stats`. Scoped to the ShoreGuard host —
on a single-box deployment that is the gateway node.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

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
