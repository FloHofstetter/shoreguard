"""Security headers middleware."""

from __future__ import annotations

import secrets

from fastapi import Request, Response


async def security_headers_middleware(request: Request, call_next: object) -> Response:
    """Add standard security headers to every HTTP response.

    Generates a per-request CSP nonce on ``request.state.csp_nonce`` before
    dispatching to the route so templates can render nonce-gated inline
    scripts.  When ``auth.csp_strict`` is enabled, the Content-Security-Policy
    header is built from ``auth.csp_policy_strict`` with the nonce
    interpolated; otherwise the static ``auth.csp_policy`` value is used.

    Args:
        request: The incoming request.
        call_next: ASGI call-next callable.

    Returns:
        Response: The response with security headers injected.
    """
    nonce = secrets.token_urlsafe(16)
    request.state.csp_nonce = nonce

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

    if settings.auth.csp_strict:
        response.headers["Content-Security-Policy"] = settings.auth.csp_policy_strict.format(
            nonce=nonce
        )
    elif settings.auth.csp_policy:
        response.headers["Content-Security-Policy"] = settings.auth.csp_policy

    return response
