"""Security headers middleware."""

from __future__ import annotations

from fastapi import Request, Response


async def security_headers_middleware(request: Request, call_next: object) -> Response:
    """Add standard security headers to every HTTP response.

    Args:
        request: The incoming request.
        call_next: ASGI call-next callable.

    Returns:
        The response with security headers injected.
    """
    response: Response = await call_next(request)  # type: ignore[operator]

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

    from shoreguard.settings import get_settings

    settings = get_settings()
    if settings.auth.hsts_enabled:
        response.headers["Strict-Transport-Security"] = (
            f"max-age={settings.auth.hsts_max_age}; includeSubDomains"
        )
    if settings.auth.csp_policy:
        response.headers["Content-Security-Policy"] = settings.auth.csp_policy

    return response
