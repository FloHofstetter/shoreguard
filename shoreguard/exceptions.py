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
    """Return a user-friendly message for gRPC errors, or str(exc) for others.

    Args:
        exc: The exception to translate.

    Returns:
        str: A human-readable error string suitable for UI display.
    """
    if isinstance(exc, grpc.RpcError):
        code = exc.code() if hasattr(exc, "code") else None
        if code is not None:
            friendly = _GRPC_FRIENDLY.get(code)
            if friendly:
                return friendly
            return f"gRPC error: {code.name}"
        return "An unexpected gateway communication error occurred."
    return "An unexpected gateway communication error occurred."


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


class ConflictError(ShoreGuardError):
    """A write operation conflicts with existing state."""


class PolicyLockedError(ShoreGuardError):
    """A policy modification was rejected because the policy is pinned."""


class ValidationError(ShoreGuardError):
    """Input validation failed (e.g. malformed command syntax)."""


class InvalidSBOMError(ShoreGuardError):
    """An uploaded SBOM document is malformed or unsupported (M21)."""


class BootHookError(ShoreGuardError):
    """A boot hook failed (M22).

    Carries the failed hook's name + captured output so callers can surface
    it to the user. Pre-create failures abort CreateSandbox; post-create
    failures abort the sandbox warm-up only when ``continue_on_failure`` is
    false.

    Args:
        message: Human-readable failure description.
        hook_name: Name of the failing hook.
        phase: ``pre_create`` or ``post_create``.
        output: Captured stdout+stderr (already truncated by caller).
    """

    def __init__(  # noqa: D107
        self,
        message: str,
        *,
        hook_name: str = "",
        phase: str = "",
        output: str = "",
    ) -> None:
        super().__init__(message)
        self.hook_name = hook_name
        self.phase = phase
        self.output = output
