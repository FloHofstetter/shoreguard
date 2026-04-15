"""REST endpoint for polling long-running operation status.

Some mutating endpoints return HTTP 202 plus an operation id
instead of holding the request open while a slow action (sandbox
create, SSH session setup, gateway restart) runs to completion.
This module provides the client-facing poll path clients hit to
discover when those operations have finished, what they
produced, and whether they succeeded.

State is read directly from
:class:`~shoreguard.services.operations.OperationService`; writes
happen inside whichever service owns the operation worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.responses import JSONResponse, StreamingResponse

from shoreguard.api.auth import require_role
from shoreguard.api.schemas import OperationListResponse, OperationResponse
from shoreguard.services import operations as _ops_mod
from shoreguard.services.operations import AsyncOperationService
from shoreguard.services.operations_types import ACTIVE_STATES, TERMINAL_STATES

logger = logging.getLogger(__name__)

router = APIRouter()


def _format_sse_event(event: str | None, payload: dict[str, Any]) -> str:
    """Serialise an SSE event with control-character-safe data.

    json.dumps already escapes embedded newline/CR inside strings, but
    the SSE wire format treats raw newline, CR, or NUL bytes anywhere in
    the data: line as a framing delimiter or hard abort. Mirrors upstream
    OpenShell #842 (format_sse_error) by stripping any stray control bytes
    before emission so a caller that hands us a pre-escaped string can't
    smuggle in a premature terminator.

    Args:
        event: Optional SSE event name. ``None`` omits the ``event:`` line.
        payload: JSON-serialisable dict for the ``data:`` line.

    Returns:
        str: Fully framed SSE record (``event`` line optional,
            ``data`` line, terminating blank line).
    """
    data = json.dumps(payload)
    data = data.replace("\r", "\\r").replace("\n", "\\n")
    data = "".join(ch for ch in data if ch >= " " or ch == "\t")
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {data}\n\n"


def _get_svc() -> AsyncOperationService:
    if _ops_mod.operation_service is None:
        raise HTTPException(503, "Operation service not initialised")
    return _ops_mod.operation_service


@router.get("", response_model=OperationListResponse)
async def list_operations(
    status: str | None = Query(None, description="Filter by status (running, succeeded, failed)"),
    resource_type: str | None = Query(None, description="Filter by resource type"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List operations with optional filtering.

    Args:
        status: Filter by operation status.
        resource_type: Filter by resource type.
        limit: Maximum number of results.
        offset: Number of results to skip.

    Returns:
        dict[str, Any]: List of operations and total count.
    """
    svc = _get_svc()
    ops, total = await svc.list_ops(
        status=status,
        resource_type=resource_type,
        limit=limit,
        offset=offset,
    )
    return {
        "operations": [svc.to_dict(op) for op in ops],
        "total": total,
    }


@router.get("/{operation_id}")
async def get_operation(
    operation_id: str,
    wait: int | None = Query(None, ge=1, le=60, description="Long-poll: wait up to N seconds"),
) -> JSONResponse:
    """Get the current status of a long-running operation.

    Returns a ``Retry-After`` header when the operation is still active.
    Supports long-polling via the ``wait`` query parameter.

    Args:
        operation_id: The unique identifier of the operation.
        wait: Optional long-poll timeout in seconds (1-60).

    Raises:
        HTTPException: If the operation is not found.

    Returns:
        JSONResponse: The serialised operation state.
    """
    svc = _get_svc()
    op = await svc.get(operation_id)
    if op is None:
        logger.debug("Operation not found: %s", operation_id)
        raise HTTPException(status_code=404, detail="Operation not found")

    if wait and op.status in ACTIVE_STATES:
        deadline = asyncio.get_event_loop().time() + wait
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1)
            op = await svc.get(operation_id)
            if op is None or op.status in TERMINAL_STATES:
                break

    if op is None:
        raise HTTPException(status_code=404, detail="Operation not found")

    headers = {}
    if op.status in ACTIVE_STATES:
        headers["Retry-After"] = "2"
    return JSONResponse(content=svc.to_dict(op), headers=headers)


@router.get("/{operation_id}/stream")
async def stream_operation(operation_id: str) -> StreamingResponse:
    """Stream operation progress via Server-Sent Events.

    Sends an SSE event every second until the operation reaches a terminal
    state (succeeded or failed).

    Args:
        operation_id: The unique identifier of the operation.

    Raises:
        HTTPException: If the operation is not found.

    Returns:
        StreamingResponse: SSE event stream.
    """
    svc = _get_svc()
    op = await svc.get(operation_id)
    if op is None:
        raise HTTPException(status_code=404, detail="Operation not found")

    async def event_generator():
        while True:
            op = await svc.get(operation_id)
            if op is None:
                yield _format_sse_event("error", {"error": "not_found"})
                return
            yield _format_sse_event(None, svc.to_dict(op))
            if op.status in TERMINAL_STATES:
                return
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/{operation_id}/cancel",
    response_model=OperationResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def cancel_operation(operation_id: str) -> dict[str, Any]:
    """Cancel a running operation.

    Args:
        operation_id: The operation ID to cancel.

    Raises:
        HTTPException: If the operation is not found or not active.

    Returns:
        dict[str, Any]: The updated operation state.
    """
    svc = _get_svc()
    op = await svc.cancel(operation_id)
    if op is None:
        raise HTTPException(status_code=400, detail="Operation not found or not running")
    return svc.to_dict(op)
