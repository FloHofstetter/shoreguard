"""In-memory store for tracking long-running operations."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Operation:
    """A tracked long-running operation."""

    id: str
    status: str  # "running" | "succeeded" | "failed"
    resource_type: str  # "gateway" | "sandbox"
    resource_key: str  # name of the resource (for duplicate detection)
    created_at: float = field(default_factory=time.monotonic)
    completed_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class OperationStore:
    """Thread-safe in-memory store for async operations.

    Operations are stored with a TTL and automatically cleaned up.
    """

    def __init__(self, ttl: float = 3600.0, running_ttl: float = 600.0) -> None:  # noqa: D107
        self._ops: dict[str, Operation] = {}
        self._lock = threading.Lock()
        self._ttl = ttl
        self._running_ttl = running_ttl

    def create(self, resource_type: str, resource_key: str) -> Operation:
        """Create a new running operation."""
        op = Operation(
            id=str(uuid.uuid4()),
            status="running",
            resource_type=resource_type,
            resource_key=resource_key,
        )
        with self._lock:
            self._ops[op.id] = op
        return op

    def create_if_not_running(self, resource_type: str, resource_key: str) -> Operation | None:
        """Atomically check for a running operation and create one if none exists.

        Returns the new Operation, or None if one is already running.
        """
        op = Operation(
            id=str(uuid.uuid4()),
            status="running",
            resource_type=resource_type,
            resource_key=resource_key,
        )
        with self._lock:
            if any(
                o.status == "running"
                and o.resource_type == resource_type
                and o.resource_key == resource_key
                for o in self._ops.values()
            ):
                return None
            self._ops[op.id] = op
        return op

    def complete(self, op_id: str, result: dict[str, Any]) -> None:
        """Mark an operation as succeeded with its result."""
        with self._lock:
            op = self._ops.get(op_id)
            if op:
                op.status = "succeeded"
                op.result = result
                op.completed_at = time.monotonic()
                logger.info(
                    "Operation %s (%s/%s) succeeded",
                    op_id,
                    op.resource_type,
                    op.resource_key,
                )

    def fail(self, op_id: str, error: str) -> None:
        """Mark an operation as failed with an error message."""
        with self._lock:
            op = self._ops.get(op_id)
            if op:
                op.status = "failed"
                op.error = error
                op.completed_at = time.monotonic()
                logger.warning(
                    "Operation %s (%s/%s) failed: %s",
                    op_id,
                    op.resource_type,
                    op.resource_key,
                    error,
                )

    def get(self, op_id: str) -> Operation | None:
        """Get an operation by ID, or None if not found/expired."""
        with self._lock:
            return self._ops.get(op_id)

    def is_running(self, resource_type: str, resource_key: str) -> bool:
        """Check if there is already a running operation for the given resource."""
        with self._lock:
            return any(
                op.status == "running"
                and op.resource_type == resource_type
                and op.resource_key == resource_key
                for op in self._ops.values()
            )

    def cleanup(self) -> int:
        """Remove completed operations older than TTL and expire stuck running operations."""
        now = time.monotonic()
        removed = 0
        with self._lock:
            # Expire running operations that exceeded running_ttl
            for op in self._ops.values():
                if op.status == "running" and (now - op.created_at) > self._running_ttl:
                    op.status = "failed"
                    op.error = "Operation timed out"
                    op.completed_at = now
                    logger.warning(
                        "Operation %s (%s/%s) timed out after %.0fs",
                        op.id,
                        op.resource_type,
                        op.resource_key,
                        now - op.created_at,
                    )
            # Remove completed operations older than TTL
            expired = [
                op_id
                for op_id, op in self._ops.items()
                if op.completed_at is not None and (now - op.completed_at) > self._ttl
            ]
            for op_id in expired:
                del self._ops[op_id]
                removed += 1
        if removed > 0:
            logger.debug("Operation cleanup: removed %d expired operations", removed)
        return removed

    def to_dict(self, op: Operation) -> dict[str, Any]:
        """Convert an operation to a JSON-serializable dict."""
        with self._lock:
            d = asdict(op)
        # Remove internal fields
        d.pop("created_at", None)
        d.pop("completed_at", None)
        d.pop("resource_key", None)
        # Remove None fields
        return {k: v for k, v in d.items() if v is not None}

    def _reset(self) -> None:
        """Clear all operations. For testing only."""
        with self._lock:
            self._ops.clear()


operation_store = OperationStore()
