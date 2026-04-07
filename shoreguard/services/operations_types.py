"""Typed enums for the long-running operations system."""

from __future__ import annotations

from enum import StrEnum


class OpStatus(StrEnum):
    """Operation lifecycle states."""

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
    """Types of resources that can have long-running operations."""

    sandbox = "sandbox"
    exec = "exec"
    gateway = "gateway"


class ErrorCode(StrEnum):
    """Machine-readable error codes for failed operations."""

    internal = "internal"
    timeout = "timeout"
    cancelled = "cancelled"
    orphaned = "orphaned"
    grpc_unavailable = "grpc_unavailable"
