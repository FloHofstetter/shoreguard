"""Domain exceptions for Shoreguard."""

from __future__ import annotations

import grpc

_GRPC_FRIENDLY: dict[grpc.StatusCode, str] = {
    grpc.StatusCode.UNIMPLEMENTED: "Not supported by the current gateway version.",
    grpc.StatusCode.UNAVAILABLE: "Gateway is not reachable. Check if it is running.",
    grpc.StatusCode.PERMISSION_DENIED: "Permission denied by the gateway.",
    grpc.StatusCode.UNAUTHENTICATED: "Authentication required. Check gateway credentials.",
}


def friendly_grpc_error(exc: Exception) -> str:
    """Return a user-friendly message for gRPC errors, or str(exc) for others."""
    if isinstance(exc, grpc.RpcError):
        code = exc.code() if hasattr(exc, "code") else None
        if code is not None:
            friendly = _GRPC_FRIENDLY.get(code)
            if friendly:
                return friendly
        detail = exc.details() if hasattr(exc, "details") else ""
        if detail:
            return detail
        if code is not None:
            return f"gRPC error: {code.name}"
    return str(exc)


class ShoreGuardError(Exception):
    """Base for all Shoreguard domain errors."""


class GatewayNotConnectedError(ShoreGuardError):
    """The OpenShell gateway is not reachable or not configured."""


class NotFoundError(ShoreGuardError):
    """A requested resource (preset, sandbox, etc.) was not found."""


class PolicyError(ShoreGuardError):
    """A policy read or modification failed."""


class SandboxError(ShoreGuardError):
    """A sandbox entered an unexpected state."""


class ValidationError(ShoreGuardError):
    """Input validation failed (e.g. malformed command syntax)."""


class FeatureNotAvailableError(ShoreGuardError):
    """A feature is not available in the current OpenShell gateway version."""
