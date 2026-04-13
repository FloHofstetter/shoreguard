"""Persistent state for long-running async operations.

Some REST endpoints — sandbox create, SSH session setup, gateway
restart — take long enough that returning 202 with an operation
id is better than holding the HTTP connection open. This service
backs that pattern: endpoints enqueue an operation, return the
id, and clients poll ``GET /operations/{id}`` until the status
flips from ``running`` to a terminal value.

Operations are stored in SQL so a poll survives server restarts
and fan out across replicas via the shared database. The worker
side of each operation lives in the calling service; this module
only owns lifecycle and state transitions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sqlalchemy import delete, func, select

from shoreguard.models import OperationRecord
from shoreguard.services.operations_types import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    ErrorCode,
    OpStatus,
)

logger = logging.getLogger(__name__)

# Module-level singleton — set during app lifespan (see shoreguard.api.main).
operation_service: AsyncOperationService | None = None


def _truncate_result(result: dict[str, Any], max_bytes: int | None = None) -> str:
    """Serialize *result* to JSON, truncating large string fields if needed.

    Always returns valid JSON.  Tries to shorten well-known large fields
    (stdout, stderr, output, logs) first, then falls back to a minimal
    placeholder if the payload is still too big.

    Args:
        result: Result payload to serialize.
        max_bytes: Maximum serialized size in bytes. Defaults to
            the configured ``ops.max_result_bytes`` setting when ``None``.

    Returns:
        str: JSON string representation of the (possibly truncated) result.
    """
    from shoreguard.settings import get_settings

    if max_bytes is None:
        max_bytes = get_settings().ops.max_result_bytes

    result_str = json.dumps(result, default=str)
    if len(result_str.encode()) <= max_bytes:
        return result_str

    trunc_chars = get_settings().ops.field_truncation_chars
    truncated = {**result, "truncated": True}
    for field in ("stdout", "stderr", "output", "logs"):
        if field in truncated and isinstance(truncated[field], str):
            truncated[field] = truncated[field][:trunc_chars]
            candidate = json.dumps(truncated, default=str)
            if len(candidate.encode()) <= max_bytes:
                return candidate

    return json.dumps({"truncated": True, "error": "Result too large to store"})


class AsyncOperationService:
    """DB-backed operation tracking using async SQLAlchemy.

    Provides create/start/complete/fail/cancel plus cleanup helpers, with
    an in-memory task registry that lets ``cancel()`` interrupt a running
    asyncio task.

    Args:
        session_factory: Async SQLAlchemy session factory.
        running_ttl: Seconds before a stuck running operation is timed out.
        retention_days: Days to keep completed operations before cleanup.
    """

    def __init__(  # noqa: D107
        self,
        session_factory: async_sessionmaker[AsyncSession],
        running_ttl: float = 600.0,
        retention_days: int = 30,
    ) -> None:
        self._session_factory = session_factory
        self._running_ttl = running_ttl
        self._retention_days = retention_days
        self._tasks: dict[str, asyncio.Task[None]] = {}

    # ── Create ────────────────────────────────────────────────────────────

    async def create(
        self,
        resource_type: str,
        resource_key: str,
        *,
        actor: str | None = None,
        gateway_name: str | None = None,
        idempotency_key: str | None = None,
    ) -> OperationRecord:
        """Create a new operation in *pending* state.

        Args:
            resource_type: Type of resource being operated on.
            resource_key: Name of the resource.
            actor: Identity of the user starting the operation.
            gateway_name: Gateway the operation targets.
            idempotency_key: Optional client-provided idempotency key.

        Returns:
            OperationRecord: The newly created operation record.
        """
        now = datetime.now(UTC)
        op = OperationRecord(
            id=str(uuid.uuid4()),
            status=OpStatus.pending,
            resource_type=resource_type,
            resource_key=resource_key,
            idempotency_key=idempotency_key,
            actor=actor,
            gateway_name=gateway_name,
            created_at=now,
            updated_at=now,
        )
        async with self._session_factory() as session:
            session.add(op)
            await session.commit()
            await session.refresh(op)
        return op

    async def create_if_not_running(
        self,
        resource_type: str,
        resource_key: str,
        *,
        actor: str | None = None,
        gateway_name: str | None = None,
        idempotency_key: str | None = None,
    ) -> OperationRecord | None:
        """Create a new operation if none is active for this resource.

        Args:
            resource_type: Type of resource being operated on.
            resource_key: Name of the resource.
            actor: Identity of the user starting the operation.
            gateway_name: Gateway the operation targets.
            idempotency_key: Optional client-provided idempotency key.

        Returns:
            OperationRecord | None: The new operation, or None if one is already active.
        """
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            result = await session.execute(
                select(OperationRecord).filter(
                    OperationRecord.status.in_(
                        [OpStatus.pending, OpStatus.running, OpStatus.cancelling]
                    ),
                    OperationRecord.resource_type == resource_type,
                    OperationRecord.resource_key == resource_key,
                )
            )
            if result.scalars().first() is not None:
                logger.info(
                    "Operation '%s/%s' already active, skipping",
                    resource_type,
                    resource_key,
                )
                return None
            op = OperationRecord(
                id=str(uuid.uuid4()),
                status=OpStatus.pending,
                resource_type=resource_type,
                resource_key=resource_key,
                idempotency_key=idempotency_key,
                actor=actor,
                gateway_name=gateway_name,
                created_at=now,
                updated_at=now,
            )
            session.add(op)
            await session.commit()
            await session.refresh(op)
        return op

    # ── State transitions ─────────────────────────────────────────────────

    async def start(self, op_id: str) -> None:
        """Transition pending → running.

        Args:
            op_id: The operation ID to start.
        """
        async with self._session_factory() as session:
            op = await session.get(OperationRecord, op_id)
            if op is None or op.status != OpStatus.pending:
                return
            op.status = OpStatus.running
            op.updated_at = datetime.now(UTC)
            await session.commit()

    async def complete(self, op_id: str, result: dict[str, Any]) -> None:
        """Mark an operation as succeeded.

        Args:
            op_id: The operation ID to complete.
            result: Result payload to store.
        """
        result_str = _truncate_result(result)
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            op = await session.get(OperationRecord, op_id)
            if op is None or op.status in TERMINAL_STATES:
                logger.debug("Ignoring complete() for non-active operation %s", op_id)
                return
            op.status = OpStatus.succeeded
            op.result_json = result_str
            op.progress_pct = 100
            op.completed_at = now
            op.updated_at = now
            await session.commit()
        logger.info("Operation %s succeeded", op_id)

    async def fail(self, op_id: str, error: str, error_code: str = ErrorCode.internal) -> None:
        """Mark an operation as failed.

        Args:
            op_id: The operation ID to mark as failed.
            error: Human-readable error message.
            error_code: Machine-readable error code.
        """
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            op = await session.get(OperationRecord, op_id)
            if op is None or op.status in TERMINAL_STATES:
                logger.debug("Ignoring fail() for non-active operation %s", op_id)
                return
            op.status = OpStatus.failed
            op.error_message = error
            op.error_code = error_code
            op.completed_at = now
            op.updated_at = now
            await session.commit()
        logger.warning("Operation %s failed (%s): %s", op_id, error_code, error)

    async def update_progress(self, op_id: str, pct: int, message: str | None = None) -> None:
        """Update progress for an active operation.

        Args:
            op_id: The operation ID to update.
            pct: Progress percentage (0-100).
            message: Optional progress message.
        """
        pct = max(0, min(100, pct))
        async with self._session_factory() as session:
            op = await session.get(OperationRecord, op_id)
            if op is None or op.status in TERMINAL_STATES:
                return
            op.progress_pct = pct
            op.progress_msg = message
            op.updated_at = datetime.now(UTC)
            await session.commit()

    # ── Queries ────────────────────────────────────────────────────────────

    async def get(self, op_id: str) -> OperationRecord | None:
        """Get an operation by ID.

        Args:
            op_id: The operation ID to look up.

        Returns:
            OperationRecord | None: The operation record, or None if not found.
        """
        async with self._session_factory() as session:
            op = await session.get(OperationRecord, op_id)
            if op is not None:
                await session.refresh(op)
            return op

    async def get_by_idempotency_key(self, key: str) -> OperationRecord | None:
        """Look up an operation by its idempotency key.

        Args:
            key: The idempotency key to search for.

        Returns:
            OperationRecord | None: The matching operation record, or None.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(OperationRecord).filter(OperationRecord.idempotency_key == key)
            )
            return result.scalars().first()

    async def list_ops(
        self,
        *,
        status: str | None = None,
        resource_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[OperationRecord], int]:
        """List operations with optional filtering.

        Args:
            status: Filter by status.
            resource_type: Filter by resource type.
            limit: Maximum number of results (max 200).
            offset: Number of results to skip.

        Returns:
            tuple[list[OperationRecord], int]: Tuple of (operations, total_count).
        """
        from shoreguard.settings import get_settings

        limit = min(limit, get_settings().ops.max_list_limit)
        async with self._session_factory() as session:
            query = select(OperationRecord)
            count_query = select(func.count(OperationRecord.id))
            if status:
                query = query.filter(OperationRecord.status == status)
                count_query = count_query.filter(OperationRecord.status == status)
            if resource_type:
                query = query.filter(OperationRecord.resource_type == resource_type)
                count_query = count_query.filter(OperationRecord.resource_type == resource_type)
            total = (await session.execute(count_query)).scalar() or 0
            result = await session.execute(
                query.order_by(OperationRecord.created_at.desc()).offset(offset).limit(limit)
            )
            ops = list(result.scalars().all())
            return ops, total

    async def is_running(self, resource_type: str, resource_key: str) -> bool:
        """Check if there is an active operation for the given resource.

        Args:
            resource_type: Type of resource to check.
            resource_key: Name of the resource to check.

        Returns:
            bool: True if an active (pending/running) operation exists.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(OperationRecord.id).filter(
                    OperationRecord.status.in_([OpStatus.pending, OpStatus.running]),
                    OperationRecord.resource_type == resource_type,
                    OperationRecord.resource_key == resource_key,
                )
            )
            return result.first() is not None

    async def status_counts(self) -> dict[str, int]:
        """Return counts of operations grouped by status.

        Returns:
            dict[str, int]: Mapping of status to count.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(OperationRecord.status, func.count(OperationRecord.id)).group_by(
                    OperationRecord.status
                )
            )
            return {status: count for status, count in result.all()}

    # ── Cancel ─────────────────────────────────────────────────────────────

    def register_task(self, op_id: str, task: asyncio.Task[None]) -> None:
        """Register an asyncio task for cancellation support.

        Args:
            op_id: The operation ID.
            task: The asyncio task running the operation.
        """
        self._tasks[op_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(op_id, None))

    async def cancel(self, op_id: str) -> OperationRecord | None:
        """Cancel an active operation.

        Args:
            op_id: The operation ID to cancel.

        Returns:
            OperationRecord | None: The updated operation record, or None if
            not found or not active.
        """
        async with self._session_factory() as session:
            op = await session.get(OperationRecord, op_id)
            if op is None or op.status not in (OpStatus.pending, OpStatus.running):
                return None
            op.status = OpStatus.cancelling
            op.updated_at = datetime.now(UTC)
            await session.commit()

        task = self._tasks.get(op_id)
        if task is not None and not task.done():
            task.cancel()
            return await self.get(op_id)

        await self.fail(op_id, "Operation was cancelled", error_code=ErrorCode.cancelled)
        return await self.get(op_id)

    # ── Cleanup ────────────────────────────────────────────────────────────

    async def recover_orphans(self) -> int:
        """Mark all active operations as failed (startup recovery).

        Returns:
            int: Number of orphaned operations recovered.
        """
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            result = await session.execute(
                select(OperationRecord).filter(OperationRecord.status.in_(list(ACTIVE_STATES)))
            )
            orphans = list(result.scalars().all())
            for op in orphans:
                op.status = OpStatus.failed
                op.error_message = "Server restarted while operation was in progress"
                op.error_code = ErrorCode.orphaned
                op.completed_at = now
                op.updated_at = now
            await session.commit()
            count = len(orphans)
        if count > 0:
            logger.warning("Recovered %d orphaned operations on startup", count)
        return count

    async def cleanup(self) -> int:
        """Expire stuck active operations and remove old completed ones.

        Returns:
            int: Number of operations cleaned up.
        """
        now = datetime.now(UTC)
        removed = 0
        async with self._session_factory() as session:
            cutoff = now - timedelta(seconds=self._running_ttl)
            result = await session.execute(
                select(OperationRecord).filter(
                    OperationRecord.status.in_(list(ACTIVE_STATES)),
                    OperationRecord.created_at < cutoff,
                )
            )
            for op in result.scalars().all():
                op.status = OpStatus.failed
                op.error_message = "Operation timed out"
                op.error_code = ErrorCode.timeout
                op.completed_at = now
                op.updated_at = now
                logger.warning("Operation %s timed out", op.id)

            retention_cutoff = now - timedelta(days=self._retention_days)
            del_result = await session.execute(
                delete(OperationRecord).filter(
                    OperationRecord.status.notin_(list(ACTIVE_STATES)),
                    OperationRecord.completed_at < retention_cutoff,
                )
            )
            removed = del_result.rowcount  # type: ignore[attr-defined]
            await session.commit()
        if removed > 0:
            logger.debug("Operation cleanup: removed %d expired operations", removed)
        return removed

    # ── Serialization ─────────────────────────────────────────────────────

    @staticmethod
    def to_dict(op: OperationRecord) -> dict[str, Any]:
        """Convert an operation record to a JSON-serializable dict.

        Args:
            op: The operation record to convert.

        Returns:
            dict[str, Any]: JSON-serializable representation for the API.
        """
        d: dict[str, Any] = {
            "id": op.id,
            "status": op.status,
            "resource_type": op.resource_type,
            "progress": op.progress_pct,
            "created_at": op.created_at.isoformat() if op.created_at else None,
            "updated_at": op.updated_at.isoformat() if op.updated_at else None,
        }
        if op.progress_msg:
            d["progress_message"] = op.progress_msg
        if op.result_json:
            try:
                d["result"] = json.loads(op.result_json)
            except json.JSONDecodeError:
                d["result"] = None
        if op.error_message:
            d["error"] = op.error_message
        if op.error_code:
            d["error_code"] = op.error_code
        if op.completed_at:
            d["completed_at"] = op.completed_at.isoformat()
        if op.gateway_name:
            d["gateway_name"] = op.gateway_name
        return d
