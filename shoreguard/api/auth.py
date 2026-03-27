"""API-key authentication for Shoreguard.

Single shared API key configured via ``--api-key`` CLI flag or the
``SHOREGUARD_API_KEY`` environment variable.

Three accepted credential transports:
1. ``Authorization: Bearer <key>`` header  — API / Terraform / curl
2. ``sg_session`` cookie (HMAC-signed)     — Web UI after login
3. ``?token=<key>`` query parameter        — WebSocket connections

When **no** API key is configured the auth dependency is a no-op so
Shoreguard stays zero-config for local development.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time

from fastapi import Cookie, HTTPException, Query, Request, WebSocket, status

logger = logging.getLogger("shoreguard")

# ─── Module state (set once at startup via configure()) ──────────────────────

_api_key: str | None = None
_hmac_secret: bytes = b""

# Cookie / session settings
COOKIE_NAME = "sg_session"
SESSION_MAX_AGE = 86400 * 7  # 7 days


def configure(api_key: str | None) -> None:
    """Store the API key and derive an HMAC secret for session cookies.

    Called once from the CLI entry-point before Uvicorn starts.
    """
    global _api_key, _hmac_secret  # noqa: PLW0603
    _api_key = api_key
    if api_key:
        _hmac_secret = hashlib.sha256(api_key.encode()).digest()
        logger.info("API-key authentication enabled")
    else:
        _hmac_secret = b""


def is_auth_enabled() -> bool:
    """Return True when an API key has been configured."""
    return _api_key is not None


# ─── Session cookie helpers ──────────────────────────────────────────────────


def create_session_token() -> str:
    """Create an HMAC-signed session token: ``<nonce>.<expiry>.<signature>``."""
    nonce = secrets.token_urlsafe(24)
    expiry = str(int(time.time()) + SESSION_MAX_AGE)
    payload = f"{nonce}.{expiry}"
    sig = hmac.new(_hmac_secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token: str) -> bool:
    """Verify the HMAC signature and expiry of a session token."""
    parts = token.split(".")
    if len(parts) != 3:
        return False
    nonce, expiry_str, sig = parts
    payload = f"{nonce}.{expiry_str}"
    expected = hmac.new(_hmac_secret, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        logger.debug("Session token verification failed: bad signature")
        return False
    try:
        if int(expiry_str) < int(time.time()):
            logger.debug("Session token verification failed: expired")
            return False
    except ValueError:
        return False
    return True


# ─── FastAPI dependencies ────────────────────────────────────────────────────


def require_auth(request: Request) -> None:
    """Reject the request when auth is enabled and no valid credential is found."""
    if check_request_auth(request):
        return

    client_ip = request.client.host if request.client else "unknown"
    logger.warning(
        "Auth rejected: missing or invalid credentials (path=%s, client=%s)",
        request.url.path,
        client_ip,
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_auth_ws(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    sg_session: str | None = Cookie(default=None),
) -> None:
    """FastAPI dependency for WebSocket auth.

    Accepts the API key via ``?token=`` query param or ``sg_session`` cookie.
    Raises an HTTP 403 *before* the connection is accepted.
    """
    if not _api_key:
        return

    # 1. Query-param token
    if token and hmac.compare_digest(token.encode(), _api_key.encode()):
        logger.debug("WebSocket auth via query-param token (path=%s)", websocket.url.path)
        return

    # 2. Session cookie
    if sg_session and verify_session_token(sg_session):
        logger.debug("WebSocket auth via session cookie (path=%s)", websocket.url.path)
        return

    client_ip = websocket.client.host if websocket.client else "unknown"
    logger.warning(
        "WebSocket auth rejected (path=%s, client=%s)",
        websocket.url.path,
        client_ip,
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="WebSocket authentication failed",
    )


# ─── Credential check (shared by dependencies and route handlers) ────────────


def check_request_auth(request: Request) -> bool:
    """Return True when the request carries a valid Bearer token or session cookie.

    Used by both ``require_auth`` and the ``/api/auth/check`` endpoint to
    avoid duplicating the credential-extraction logic.
    """
    if not _api_key:
        return True  # auth disabled → always valid

    # 1. Bearer token (scheme is case-insensitive per RFC 7235)
    auth_header = request.headers.get("authorization", "")
    if auth_header[:7].lower() == "bearer ":
        token = auth_header[7:]
        if hmac.compare_digest(token.encode(), _api_key.encode()):
            logger.debug("HTTP auth via Bearer token (path=%s)", request.url.path)
            return True

    # 2. Session cookie
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie and verify_session_token(cookie):
        logger.debug("HTTP auth via session cookie (path=%s)", request.url.path)
        return True

    return False


# ─── Login / logout helpers (called from route handlers) ─────────────────────


def check_api_key(key: str) -> bool:
    """Return True when *key* matches the configured API key."""
    if not _api_key:
        return False
    return hmac.compare_digest(key.encode(), _api_key.encode())
