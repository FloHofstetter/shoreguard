"""Typed enums for the long-running operations system."""

from __future__ import annotations

from enum import StrEnum


class OpStatus(StrEnum):
    """Operation lifecycle states.

    Attributes:
        pending: Operation accepted but not yet started.
        running: Operation currently executing.
        cancelling: Operation is being cancelled.
        succeeded: Operation completed successfully.
        failed: Operation failed with an error.
    """

    pending = "pending"
    running = "running"
    cancelling = "cancelling"
    succeeded = "succeeded"
    failed = "failed"


# Terminal states — no further transitions allowed.
TERMINAL_STATES = frozenset({OpStatus.succeeded, OpStatus.failed})

# Active states — operation is in progress or queued.
ACTIVE_STATES = frozenset({OpStatus.pending, OpStatus.running, OpStatus.cancelling})


class ResourceType(StrEnum):
    """Types of resources that can have long-running operations.

    Attributes:
        sandbox: Sandbox resource operations.
        exec: Exec (command execution) resource operations.
        gateway: Gateway resource operations.
    """

    sandbox = "sandbox"
    exec = "exec"
    gateway = "gateway"


class ErrorCode(StrEnum):
    """Machine-readable error codes for failed operations.

    Attributes:
        internal: Unexpected internal error.
        timeout: Operation exceeded its allotted time.
        cancelled: Operation was cancelled by the user or system.
        orphaned: Operation was orphaned (e.g. worker died) and reconciled.
        grpc_unavailable: Underlying gRPC service was unavailable.
    """

    internal = "internal"
    timeout = "timeout"
    cancelled = "cancelled"
    orphaned = "orphaned"
    grpc_unavailable = "grpc_unavailable"
