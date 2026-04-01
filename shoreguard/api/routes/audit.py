"""REST endpoints for the audit log."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import Response

import shoreguard.services.audit as audit_mod

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def list_audit_entries(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    actor: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    """List audit log entries with optional filters (admin only).

    Args:
        limit: Maximum number of entries to return.
        offset: Number of entries to skip for pagination.
        actor: Filter by acting user identity.
        action: Filter by action type.
        resource_type: Filter by resource type.
        since: ISO-8601 lower bound for the entry timestamp.
        until: ISO-8601 upper bound for the entry timestamp.

    Returns:
        list[dict[str, Any]]: A list of audit log entry dicts.
    """
    if audit_mod.audit_service is None:
        return []
    return await asyncio.to_thread(
        audit_mod.audit_service.list,
        limit=limit,
        offset=offset,
        actor=actor,
        action=action,
        resource_type=resource_type,
        since=since,
        until=until,
    )


@router.get("/export")
async def export_audit(
    fmt: str = Query("json", alias="format", pattern="^(json|csv)$"),
    actor: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> Response:
    """Export audit log as JSON or CSV (admin only).

    Args:
        fmt: Export format, either ``"json"`` or ``"csv"``.
        actor: Filter by acting user identity.
        action: Filter by action type.
        resource_type: Filter by resource type.
        since: ISO-8601 lower bound for the entry timestamp.
        until: ISO-8601 upper bound for the entry timestamp.

    Returns:
        Response: The exported audit data as a downloadable response.
    """
    if audit_mod.audit_service is None:
        if fmt == "csv":
            return Response(content="", media_type="text/csv")
        return Response(content="[]", media_type="application/json")

    if fmt == "csv":
        csv_data = await asyncio.to_thread(
            audit_mod.audit_service.export_csv,
            actor=actor,
            action=action,
            resource_type=resource_type,
            since=since,
            until=until,
        )
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
        )

    entries = await asyncio.to_thread(
        audit_mod.audit_service.list,
        limit=10000,
        actor=actor,
        action=action,
        resource_type=resource_type,
        since=since,
        until=until,
    )
    return Response(
        content=json.dumps(entries, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=audit_log.json"},
    )
