"""Helpers for creating long-running operation (LRO) endpoints.

``run_lro`` eliminates the boilerplate that every LRO route otherwise
duplicates: operation creation, background-task setup, error handling,
task registration, and the 202 response.

Usage::

    @router.post("/create", dependencies=[Depends(require_role("operator"))])
    async def create_something(body: CreateBody, request: Request) -> JSONResponse:
        # … pre-validation …

        async def work(op):
            result = await do_the_thing(body)
            await op_svc.update_progress(op.id, 50, "Half done")
            return await finish(result)

        return await run_lro(
            resource_type="thing",
            resource_key=body.name,
            work=work,
            unique=True,
            actor=get_actor(request),
            gateway_name=get_gateway_name(request),
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import grpc
from fastapi import HTTPException
from starlette.responses import JSONResponse

from shoreguard.exceptions import friendly_grpc_error
from shoreguard.models import OperationRecord
from shoreguard.services import operations as _ops_mod
from shoreguard.services.operations import AsyncOperationService
from shoreguard.services.operations_types import ErrorCode, OpStatus

logger = logging.getLogger(__name__)

# Prevent background tasks from being garbage-collected.
_background_tasks: set[asyncio.Task[None]] = set()


async def shutdown_lros(timeout: float = 10.0) -> int:
    """Cancel all running LRO tasks and wait for completion.

    Each task's CancelledError handler marks its operation as failed.

    Args:
        timeout: Maximum seconds to wait for tasks to finish.

    Returns:
        int: Number of tasks that were cancelled.
    """
    tasks = list(_background_tasks)
    if not tasks:
        return 0
    for t in tasks:
        t.cancel()
    await asyncio.wait(tasks, timeout=timeout)
    return len(tasks)


async def run_lro(
    *,
    resource_type: str,
    resource_key: str,
    work: Callable[[OperationRecord], Awaitable[dict[str, Any]]],
    unique: bool = False,
    actor: str | None = None,
    gateway_name: str | None = None,
    idempotency_key: str | None = None,
) -> JSONResponse:
    """Create an operation and run *work* in a background task.

    Args:
        resource_type: The kind of resource (e.g. "sandbox", "gateway").
        resource_key: Resource identifier for deduplication.
        work: Async callable that receives the :class:`OperationRecord`
            and returns a result dict on success.  Any exception is
            automatically caught and recorded as a failure.
        unique: If ``True``, uses :meth:`create_if_not_running` and
            returns 409 if an active operation already exists.
        actor: Identity of the user who initiated the operation.
        gateway_name: Gateway the operation targets.
        idempotency_key: Client-provided idempotency key.

    Returns:
        JSONResponse: A 202 response with ``operation_id``, ``status``, and
            ``resource_type``, plus ``Location`` and ``Retry-After`` headers.

    Raises:
        HTTPException: 503 if the operation service is not initialised,
            409 if *unique* is set and a duplicate is detected.
    """
    svc: AsyncOperationService = _ops_mod.operation_service  # type: ignore[assignment]
    if svc is None:
        raise HTTPException(503, "Operation service not initialised")

    # ── Idempotency check ─────────────────────────────────────────────
    if idempotency_key:
        existing = await svc.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            headers = {
                "Location": f"/api/operations/{existing.id}",
                "Retry-After": "2",
            }
            return JSONResponse(
                status_code=202,
                content={
                    "operation_id": existing.id,
                    "status": existing.status,
                    "resource_type": existing.resource_type,
                },
                headers=headers,
            )

    # ── Create operation ──────────────────────────────────────────────
    if unique:
        op = await svc.create_if_not_running(
            resource_type,
            resource_key,
            actor=actor,
            gateway_name=gateway_name,
            idempotency_key=idempotency_key,
        )
        if op is None:
            raise HTTPException(
                409, f"{resource_type} '{resource_key}' operation already in progress"
            )
    else:
        op = await svc.create(
            resource_type,
            resource_key,
            actor=actor,
            gateway_name=gateway_name,
            idempotency_key=idempotency_key,
        )

    # ── Background task ───────────────────────────────────────────────
    async def _run() -> None:
        await svc.start(op.id)
        try:
            result = await work(op)
            await svc.complete(op.id, result if result is not None else {})
        except asyncio.CancelledError:
            logger.warning("LRO %s/%s cancelled (op=%s)", resource_type, resource_key, op.id)
            await svc.fail(op.id, "Operation was cancelled", error_code=ErrorCode.cancelled)
        except Exception as exc:
            logger.exception("LRO %s/%s failed (op=%s)", resource_type, resource_key, op.id)
            message = friendly_grpc_error(exc) if isinstance(exc, grpc.RpcError) else str(exc)
            await svc.fail(op.id, message[:500])

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    svc.register_task(op.id, task)

    # ── 202 Response ──────────────────────────────────────────────────
    headers = {"Location": f"/api/operations/{op.id}", "Retry-After": "2"}
    return JSONResponse(
        status_code=202,
        content={
            "operation_id": op.id,
            "status": OpStatus.pending,
            "resource_type": resource_type,
        },
        headers=headers,
    )
