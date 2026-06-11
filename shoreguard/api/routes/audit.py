"""REST endpoints for querying and exporting the audit log.

Exposes two read-only routes: a filterable list endpoint
(``GET /api/audit`` with actor / resource / action filters and
time-window paging) and an export endpoint that streams the
filtered result set as CSV or JSON for external archival or SIEM
ingestion. Writes never happen through this module — rows are
appended from inside every mutating endpoint via
``audit_log(...)``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import Response

from shoreguard.api.deps import get_services
from shoreguard.api.schemas import AuditListResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=AuditListResponse)
async def list_audit_entries(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    actor: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    gateway: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """List audit log entries with optional filters (admin only).

    Args:
        limit: Maximum number of entries to return.
        offset: Number of entries to skip for pagination.
        actor: Filter by acting user identity.
        action: Filter by action type.
        resource_type: Filter by resource type.
        gateway: Filter by gateway name.
        since: ISO-8601 lower bound for the entry timestamp.
        until: ISO-8601 upper bound for the entry timestamp.

    Returns:
        dict[str, Any]: Paginated entries with total count.
    """
    entries, total = await get_services().audit.list_with_count(
        limit=limit,
        offset=offset,
        actor=actor,
        action=action,
        resource_type=resource_type,
        gateway=gateway,
        since=since,
        until=until,
    )
    return {"entries": entries, "total": total}


@router.get("/verify")
async def verify_audit_chain() -> dict[str, Any]:
    """Verify the tamper-evidence hash chain over the audit log.

    Returns:
        dict[str, Any]: ``ok``, ``checked``, ``legacy`` (pre-chain rows),
        and ``first_bad_id`` when a break was found.
    """
    return await get_services().audit.verify_chain()


@router.get("/export", response_model=None)
async def export_audit(
    fmt: str = Query("json", alias="format", pattern="^(json|csv)$"),
    actor: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    gateway: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> Response:
    """Export audit log as JSON or CSV (admin only).

    Args:
        fmt: Export format, either ``"json"`` or ``"csv"``.
        actor: Filter by acting user identity.
        action: Filter by action type.
        resource_type: Filter by resource type.
        gateway: Filter by gateway name.
        since: ISO-8601 lower bound for the entry timestamp.
        until: ISO-8601 upper bound for the entry timestamp.

    Returns:
        Response: The exported audit data as a downloadable response.
    """
    if fmt == "csv":
        csv_data = await get_services().audit.export_csv(
            actor=actor,
            action=action,
            resource_type=resource_type,
            gateway=gateway,
            since=since,
            until=until,
        )
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
        )

    entries = await get_services().audit.list(
        limit=10000,
        actor=actor,
        action=action,
        resource_type=resource_type,
        gateway=gateway,
        since=since,
        until=until,
    )
    return Response(
        content=json.dumps(entries, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=audit_log.json"},
    )
