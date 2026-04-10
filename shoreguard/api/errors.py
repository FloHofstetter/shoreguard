"""Exception handlers for the FastAPI application.

Responses follow RFC 9457 (Problem Details for HTTP APIs): every error
body carries ``type``, ``title``, ``status``, ``detail``, and the
``application/problem+json`` content type. Our custom ``code`` and
``request_id`` fields live as RFC 9457 extension members alongside the
standard ones.
"""

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

PROBLEM_JSON = "application/problem+json"

# Combined (status, code) maps — one entry per exception/gRPC code.
_DOMAIN_MAP: dict[type, tuple[int, str]] = {
    GatewayNotConnectedError: (503, GATEWAY_NOT_CONNECTED),
    NotFoundError: (404, NOT_FOUND),
    PolicyError: (400, POLICY_ERROR),
    SandboxError: (409, SANDBOX_CONFLICT),
    ConflictError: (409, CONFLICT),
    ValidationError: (400, VALIDATION_ERROR),
    FeatureNotAvailableError: (501, FEATURE_NOT_AVAILABLE),
}

_GRPC_MAP: dict[grpc.StatusCode, tuple[int, str]] = {
    grpc.StatusCode.INVALID_ARGUMENT: (400, VALIDATION_ERROR),
    grpc.StatusCode.NOT_FOUND: (404, NOT_FOUND),
    grpc.StatusCode.ALREADY_EXISTS: (409, CONFLICT),
    grpc.StatusCode.PERMISSION_DENIED: (403, PERMISSION_DENIED),
    grpc.StatusCode.UNAUTHENTICATED: (401, AUTHENTICATION_REQUIRED),
    grpc.StatusCode.UNAVAILABLE: (503, GATEWAY_NOT_CONNECTED),
    grpc.StatusCode.UNIMPLEMENTED: (501, FEATURE_NOT_AVAILABLE),
    grpc.StatusCode.DEADLINE_EXCEEDED: (504, TIMEOUT),
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
    """Extract the request ID set by metrics middleware.

    Args:
        request: The incoming HTTP request.

    Returns:
        str | None: The request ID if set, otherwise None.
    """
    return getattr(request.state, "request_id", None)


def _title_for_code(code: str) -> str:
    """Humanize an error code constant into an RFC 9457 ``title``.

    Args:
        code: The machine-readable error code (e.g. ``NOT_FOUND``).

    Returns:
        str: A short human-readable summary (e.g. ``Not Found``).
    """
    return code.replace("_", " ").title()


def _problem(
    detail: str,
    request: Request,
    *,
    status: int,
    code: str,
    **extra: object,
) -> JSONResponse:
    """Build an RFC 9457 Problem Details response.

    Args:
        detail: Human-readable explanation specific to this occurrence.
        request: The incoming HTTP request.
        status: The HTTP status code.
        code: Machine-readable error code constant.
        **extra: Additional RFC 9457 extension members.

    Returns:
        JSONResponse: A ``application/problem+json`` response body.
    """
    body: dict = {
        "type": "about:blank",
        "title": _title_for_code(code),
        "status": status,
        "detail": detail,
        "code": code,
    }
    rid = _get_request_id(request)
    if rid:
        body["request_id"] = rid
    body.update(extra)
    return JSONResponse(status_code=status, content=body, media_type=PROBLEM_JSON)


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
            JSONResponse: RFC 9457 error response.
        """
        status, error_code = _DOMAIN_MAP.get(type(exc), (500, INTERNAL_ERROR))
        if isinstance(exc, GatewayNotConnectedError):
            logger.debug("Gateway not connected: %s", exc)
        elif status >= 500:
            logger.error("Unhandled domain error: %s (status=%d)", exc, status, exc_info=True)
        else:
            logger.warning("Domain error: %s (status=%d)", exc, status)
        return _problem(str(exc), request, status=status, code=error_code)

    @app.exception_handler(TimeoutError)
    async def timeout_error_handler(request: Request, exc: TimeoutError) -> JSONResponse:
        """Return 504 for timeout errors.

        Args:
            request: The incoming HTTP request.
            exc: The timeout exception that was raised.

        Returns:
            JSONResponse: 504 RFC 9457 error response.
        """
        logger.warning("Timeout on %s: %s", request.url.path, exc)
        return _problem(str(exc), request, status=504, code=TIMEOUT)

    @app.exception_handler(grpc.RpcError)
    async def grpc_exception_handler(request: Request, exc: grpc.RpcError) -> JSONResponse:
        """Catch gRPC errors and return proper HTTP responses.

        Args:
            request: The incoming HTTP request.
            exc: The gRPC error that was raised.

        Returns:
            JSONResponse: Mapped RFC 9457 error response.
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
            return _problem(
                detail,
                request,
                status=501,
                code=GATEWAY_UPGRADE_REQUIRED,
                feature=feature,
                upgrade_required=True,
            )
        http_status, error_code = (
            _GRPC_MAP.get(code, (500, INTERNAL_ERROR))
            if code is not None
            else (500, INTERNAL_ERROR)
        )
        return _problem(friendly_grpc_error(exc), request, status=http_status, code=error_code)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Return structured 422 for Pydantic / query-param validation errors.

        Args:
            request: The incoming HTTP request.
            exc: The validation exception raised by FastAPI/Pydantic.

        Returns:
            JSONResponse: 422 RFC 9457 error response with validation errors.
        """
        errors = [{k: v for k, v in err.items() if k != "ctx"} for err in exc.errors()]
        return _problem(
            "Validation error",
            request,
            status=422,
            code=VALIDATION_ERROR,
            errors=errors,
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Catch-all for unhandled exceptions — return 500 without leaking internals.

        Args:
            request: The incoming HTTP request.
            exc: The unhandled exception.

        Returns:
            JSONResponse: Generic 500 RFC 9457 error response.
        """
        logger.error("Unhandled exception on %s: %s", request.url.path, exc, exc_info=True)
        return _problem("Internal server error", request, status=500, code=INTERNAL_ERROR)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        """Return structured error responses for HTTPException.

        Args:
            request: The incoming HTTP request.
            exc: The HTTPException that was raised.

        Returns:
            JSONResponse: RFC 9457 error response preserving the exception headers.
        """
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        response = _problem(
            detail,
            request,
            status=exc.status_code,
            code=code_for_status(exc.status_code),
        )
        headers = getattr(exc, "headers", None)
        if headers:
            response.headers.update(headers)
        return response
