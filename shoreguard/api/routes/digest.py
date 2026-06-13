"""REST endpoint for the on-demand activity digest.

One read-only route that builds the "what happened in the last N
hours?" report live — the dashboard's overnight-report card renders it.
The scheduled daily push is the ``daily_digest`` background task.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request

from shoreguard.api.auth import scoped_gateway_names
from shoreguard.api.deps import get_services

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def get_digest(
    request: Request, hours: int = Query(default=24, ge=1, le=168)
) -> dict[str, Any]:
    """Return the activity digest for the trailing window.

    Scoped to the caller's tenants' gateways for a non-admin tenant user
    (cross-cutting unattributed audit events stay visible); the full fleet
    otherwise. The pushed daily digest is always fleet-wide.

    Args:
        request: The incoming HTTP request (for tenant scoping).
        hours: Window size in hours (1-168, default 24).

    Returns:
        dict[str, Any]: Digest payload (audit summary, sandbox churn,
            approvals, gateway health, webhook failures, kill switch).
    """
    scope = await scoped_gateway_names(request)
    return await get_services().digest.build(hours=hours, scope=scope)
