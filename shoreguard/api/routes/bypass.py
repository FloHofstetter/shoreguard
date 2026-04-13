"""REST endpoints for the Bypass Detection dashboard.

Exposes read-only views over
:class:`~shoreguard.services.bypass.BypassService`'s in-memory
event ring buffer: a paginated event list with severity filter
and a per-severity summary with top offending sandboxes. Each
event carries a MITRE ATT&CK technique mapping for downstream
SIEM correlation.

The ring buffer is deliberately process-local: restarting
ShoreGuard clears the history, so long-term retention is a
webhook-to-SIEM problem, not a database problem for this route.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_gateway_name

if TYPE_CHECKING:
    from shoreguard.services.bypass import BypassService

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_bypass_service() -> BypassService:
    """Return the global BypassService singleton.

    Raises:
        HTTPException: If the service has not been initialised.

    Returns:
        BypassService: The active bypass service instance.
    """
    from shoreguard.services.bypass import bypass_service

    if bypass_service is None:
        raise HTTPException(503, "BypassService not initialised")
    return bypass_service


@router.get(
    "/{name}/bypass",
    dependencies=[Depends(require_role("viewer"))],
    response_model=None,
)
async def get_bypass_events(
    name: str,
    request: Request,
    gw: str = Depends(get_gateway_name),
    since_ms: int = Query(0, ge=0, description="Only events after this timestamp"),
    limit: int = Query(100, ge=1, le=1000, description="Max events to return"),
) -> dict[str, Any]:
    """Return bypass detection events for a sandbox.

    Args:
        name: Sandbox name.
        request: The incoming HTTP request.
        gw: Gateway name from the URL path.
        since_ms: Only return events after this timestamp (ms).
        limit: Maximum number of events to return.

    Returns:
        dict[str, Any]: ``{events: [...], count: int}``
    """
    svc = _get_bypass_service()
    events = svc.get_events(gw, name, since_ms=since_ms, limit=limit)
    return {"events": events, "count": len(events)}


@router.get(
    "/{name}/bypass/summary",
    dependencies=[Depends(require_role("viewer"))],
    response_model=None,
)
async def get_bypass_summary(
    name: str,
    request: Request,
    gw: str = Depends(get_gateway_name),
) -> dict[str, Any]:
    """Return aggregated bypass statistics for a sandbox.

    Args:
        name: Sandbox name.
        request: The incoming HTTP request.
        gw: Gateway name from the URL path.

    Returns:
        dict[str, Any]: Bypass summary with totals by technique and severity.
    """
    svc = _get_bypass_service()
    return dict(svc.get_summary(gw, name))
