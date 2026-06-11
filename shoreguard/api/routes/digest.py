"""REST endpoint for the on-demand activity digest.

One read-only route that builds the "what happened in the last N
hours?" report live — the dashboard's overnight-report card renders it.
The scheduled daily push is the ``daily_digest`` background task.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from shoreguard.api.deps import get_services

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def get_digest(hours: int = Query(default=24, ge=1, le=168)) -> dict[str, Any]:
    """Return the activity digest for the trailing window.

    Args:
        hours: Window size in hours (1-168, default 24).

    Returns:
        dict[str, Any]: Digest payload (audit summary, sandbox churn,
            approvals, gateway health, webhook failures, kill switch).
    """
    return await get_services().digest.build(hours=hours)
