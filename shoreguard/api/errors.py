"""Exception handlers for the FastAPI application."""

import logging

import grpc
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from shoreguard.exceptions import (
    ConflictError,
    FeatureNotAvailableError,
    GatewayNotConnectedError,
    NotFoundError,
    PolicyError,
    SandboxError,
    ShoreGuardError,
    ValidationError,
    friendly_grpc_error,
)

from .error_codes import (
    AUTHENTICATION_REQUIRED,
    CONFLICT,
    FEATURE_NOT_AVAILABLE,
    GATEWAY_NOT_CONNECTED,
    GATEWAY_UPGRADE_REQUIRED,
    INTERNAL_ERROR,
    NOT_FOUND,
    PERMISSION_DENIED,
    POLICY_ERROR,
    SANDBOX_CONFLICT,
    TIMEOUT,
    VALIDATION_ERROR,
    code_for_status,
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
    ConflictError: 409,
    ValidationError: 400,
    FeatureNotAvailableError: 501,
}

_DOMAIN_CODE_MAP: dict[type, str] = {
    GatewayNotConnectedError: GATEWAY_NOT_CONNECTED,
    NotFoundError: NOT_FOUND,
    PolicyError: POLICY_ERROR,
    SandboxError: SANDBOX_CONFLICT,
    ConflictError: CONFLICT,
    ValidationError: VALIDATION_ERROR,
    FeatureNotAvailableError: FEATURE_NOT_AVAILABLE,
}

_GRPC_CODE_MAP: dict[grpc.StatusCode, str] = {
    grpc.StatusCode.INVALID_ARGUMENT: VALIDATION_ERROR,
    grpc.StatusCode.NOT_FOUND: NOT_FOUND,
    grpc.StatusCode.ALREADY_EXISTS: CONFLICT,
    grpc.StatusCode.PERMISSION_DENIED: PERMISSION_DENIED,
    grpc.StatusCode.UNAUTHENTICATED: AUTHENTICATION_REQUIRED,
    grpc.StatusCode.UNAVAILABLE: GATEWAY_NOT_CONNECTED,
    grpc.StatusCode.UNIMPLEMENTED: FEATURE_NOT_AVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED: TIMEOUT,
}


def _detect_feature_from_path(path: str) -> str:
    """Extract a human-readable feature name from the request URL path.

    Args:
        path: The URL path of the incoming request.

    Returns:
        str: A human-readable feature name.
    """
    if "/policy" in path:
        return "Sandbox policy management"
    if "/approvals" in path:
        return "Policy approval workflow"
    if "/inference" in path:
        return "Inference routing"
    return "This operation"


def _get_request_id(request: Request) -> str | None:
    """Extract the request ID set by metrics middleware."""
    return getattr(request.state, "request_id", None)


def _error_body(detail: str, request: Request, *, code: str | None = None, **extra: object) -> dict:
    """Build a consistent error response body with optional request_id."""
    body: dict = {"detail": detail}
    if code:
        body["code"] = code
    rid = _get_request_id(request)
    if rid:
        body["request_id"] = rid
    body.update(extra)
    return body


def register_error_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI app.

    Args:
        app: The FastAPI application instance.
    """

    @app.exception_handler(ShoreGuardError)
    async def shoreguard_error_handler(request: Request, exc: ShoreGuardError) -> JSONResponse:
        """Return the appropriate HTTP status for domain errors.

        Args:
            request: The incoming HTTP request.
            exc: The domain-level exception that was raised.

        Returns:
            JSONResponse: Error response with status code and detail.
        """
        status = _DOMAIN_STATUS_MAP.get(type(exc), 500)
        error_code = _DOMAIN_CODE_MAP.get(type(exc), INTERNAL_ERROR)
        if isinstance(exc, GatewayNotConnectedError):
            logger.debug("Gateway not connected: %s", exc)
        elif status >= 500:
            logger.error("Unhandled domain error: %s (status=%d)", exc, status, exc_info=True)
        else:
            logger.warning("Domain error: %s (status=%d)", exc, status)
        return JSONResponse(
            status_code=status, content=_error_body(str(exc), request, code=error_code)
        )

    @app.exception_handler(TimeoutError)
    async def timeout_error_handler(request: Request, exc: TimeoutError) -> JSONResponse:
        """Return 504 for timeout errors.

        Args:
            request: The incoming HTTP request.
            exc: The timeout exception that was raised.

        Returns:
            JSONResponse: 504 error response.
        """
        logger.warning("Timeout on %s: %s", request.url.path, exc)
        return JSONResponse(status_code=504, content=_error_body(str(exc), request, code=TIMEOUT))

    @app.exception_handler(grpc.RpcError)
    async def grpc_exception_handler(request: Request, exc: grpc.RpcError) -> JSONResponse:
        """Catch gRPC errors and return proper HTTP responses.

        Args:
            request: The incoming HTTP request.
            exc: The gRPC error that was raised.

        Returns:
            JSONResponse: Mapped HTTP error response.
        """
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
                content=_error_body(
                    detail,
                    request,
                    code=GATEWAY_UPGRADE_REQUIRED,
                    feature=feature,
                    upgrade_required=True,
                ),
            )
        detail = friendly_grpc_error(exc)
        http_status = _GRPC_STATUS_MAP.get(code, 500) if code is not None else 500
        error_code = (
            _GRPC_CODE_MAP.get(code, INTERNAL_ERROR) if code is not None else INTERNAL_ERROR
        )
        return JSONResponse(
            status_code=http_status, content=_error_body(detail, request, code=error_code)
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Return structured 422 for Pydantic / query-param validation errors.

        Args:
            request: The incoming HTTP request.
            exc: The validation exception raised by FastAPI/Pydantic.

        Returns:
            JSONResponse: 422 error response with detailed validation errors.
        """
        errors = []
        for err in exc.errors():
            clean = {k: v for k, v in err.items() if k != "ctx"}
            errors.append(clean)
        return JSONResponse(
            status_code=422,
            content=_error_body("Validation error", request, code=VALIDATION_ERROR, errors=errors),
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Catch-all for unhandled exceptions — return 500 without leaking internals.

        Args:
            request: The incoming HTTP request.
            exc: The unhandled exception.

        Returns:
            JSONResponse: Generic 500 error response.
        """
        logger.error("Unhandled exception on %s: %s", request.url.path, exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content=_error_body("Internal server error", request, code=INTERNAL_ERROR),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        """Return structured error responses for HTTPException.

        Args:
            request: The incoming HTTP request.
            exc: The HTTPException that was raised.

        Returns:
            JSONResponse: Error response with status code, detail, and error code.
        """
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(detail, request, code=code_for_status(exc.status_code)),
            headers=getattr(exc, "headers", None),
        )
