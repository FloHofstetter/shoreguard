"""Exception handlers for the FastAPI application."""

import logging

import grpc
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from shoreguard.exceptions import (
    FeatureNotAvailableError,
    GatewayNotConnectedError,
    NotFoundError,
    PolicyError,
    SandboxError,
    ShoreGuardError,
    ValidationError,
    friendly_grpc_error,
)

logger = logging.getLogger(__name__)

_GRPC_STATUS_MAP = {
    grpc.StatusCode.INVALID_ARGUMENT: 400,
    grpc.StatusCode.NOT_FOUND: 404,
    grpc.StatusCode.ALREADY_EXISTS: 409,
    grpc.StatusCode.PERMISSION_DENIED: 403,
    grpc.StatusCode.UNAUTHENTICATED: 401,
    grpc.StatusCode.UNAVAILABLE: 503,
    grpc.StatusCode.UNIMPLEMENTED: 501,
    grpc.StatusCode.DEADLINE_EXCEEDED: 504,
}

_DOMAIN_STATUS_MAP: dict[type, int] = {
    GatewayNotConnectedError: 503,
    NotFoundError: 404,
    PolicyError: 400,
    SandboxError: 409,
    ValidationError: 400,
    FeatureNotAvailableError: 501,
}


def _detect_feature_from_path(path: str) -> str:
    """Extract a human-readable feature name from the request URL path."""
    if "/policy" in path:
        return "Sandbox policy management"
    if "/approvals" in path:
        return "Policy approval workflow"
    if "/inference" in path:
        return "Inference routing"
    return "This operation"


def register_error_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI app."""

    @app.exception_handler(ShoreGuardError)
    async def shoreguard_error_handler(request: Request, exc: ShoreGuardError):
        """Return the appropriate HTTP status for domain errors."""
        status = _DOMAIN_STATUS_MAP.get(type(exc), 500)
        if status >= 500:
            logger.error("Unhandled domain error: %s (status=%d)", exc, status, exc_info=True)
        else:
            logger.warning("Domain error: %s (status=%d)", exc, status)
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    @app.exception_handler(TimeoutError)
    async def timeout_error_handler(request: Request, exc: TimeoutError):
        """Return 504 for timeout errors."""
        logger.warning("Timeout on %s: %s", request.url.path, exc)
        return JSONResponse(status_code=504, content={"detail": str(exc)})

    @app.exception_handler(grpc.RpcError)
    async def grpc_exception_handler(request: Request, exc: grpc.RpcError):
        """Catch gRPC errors and return proper HTTP responses."""
        code = exc.code() if hasattr(exc, "code") else None
        logger.warning(
            "gRPC error on %s (code=%s): %s",
            request.url.path,
            code,
            friendly_grpc_error(exc),
        )
        if code == grpc.StatusCode.UNIMPLEMENTED:
            feature = _detect_feature_from_path(request.url.path)
            detail = (
                f"{feature} is not supported by the current OpenShell gateway version. "
                f"This feature requires a newer gateway."
            )
            return JSONResponse(
                status_code=501,
                content={"detail": detail, "feature": feature, "upgrade_required": True},
            )
        detail = friendly_grpc_error(exc)
        http_status = _GRPC_STATUS_MAP.get(code, 500) if code is not None else 500
        return JSONResponse(status_code=http_status, content={"detail": detail})

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        """Catch-all for unhandled exceptions — return 500 without leaking internals."""
        logger.error("Unhandled exception on %s: %s", request.url.path, exc, exc_info=True)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
