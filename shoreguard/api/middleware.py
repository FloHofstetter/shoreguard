"""HTTP middleware: rate limiting, body size limit, static-file caching."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

_RATE_LIMIT_SKIP_PATHS = frozenset({"/healthz", "/readyz", "/metrics", "/version"})


async def global_rate_limit_middleware(request: Request, call_next: Any) -> Any:
    """Coarse per-IP rate limit applied to every HTTP request.

    Health and metrics endpoints are exempt so that probes and scrapers
    can never be blocked.  Applied in addition to login/write limiters.

    Args:
        request: The incoming HTTP request.
        call_next: The next ASGI handler in the middleware chain.

    Returns:
        Any: A 429 response when rate-limited, otherwise the downstream response.
    """
    path = request.url.path
    if path in _RATE_LIMIT_SKIP_PATHS:
        return await call_next(request)

    from shoreguard.api.ratelimit import get_global_limiter

    client_ip = request.client.host if request.client else "unknown"
    limiter = get_global_limiter()
    blocked, retry_after = limiter.is_limited(client_ip)
    if blocked:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests"},
            headers={"Retry-After": str(retry_after)},
        )
    limiter.record(client_ip)
    return await call_next(request)


async def body_size_limit_middleware(request: Request, call_next: Any) -> Any:
    """Reject requests whose Content-Length exceeds the configured limit.

    Note: only honours the ``Content-Length`` header — chunked uploads
    without a length header are forwarded unchanged and bounded by the
    individual endpoint's Pydantic field limits.

    Args:
        request: The incoming HTTP request.
        call_next: The next ASGI handler in the middleware chain.

    Returns:
        Any: A 400/413 response when the body is invalid or too large, otherwise the
            downstream response.
    """
    from shoreguard.settings import get_settings

    max_bytes = get_settings().limits.max_request_body_bytes
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            length = int(cl)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"detail": "Invalid Content-Length header"},
            )
        if length > max_bytes:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Request body too large (limit {max_bytes} bytes)"},
                headers={"Connection": "close"},
            )
    return await call_next(request)


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles that asks browsers to revalidate on every request."""

    async def get_response(self, path: str, scope: Any) -> Any:
        """Serve the static asset with a no-cache directive.

        Args:
            path: Filesystem-relative path of the requested asset.
            scope: ASGI scope for the current request.

        Returns:
            Any: The underlying StaticFiles response with a ``Cache-Control``
            ``no-cache`` header applied.
        """
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response
