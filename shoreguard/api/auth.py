"""Role-based API-key authentication for Shoreguard.

Multiple API keys with roles (admin / operator / viewer) stored in the
database.  A single legacy key via ``--api-key`` / ``SHOREGUARD_API_KEY``
is supported for backward compatibility and bootstrap.

Three accepted credential transports:
1. ``Authorization: Bearer <key>`` header  — API / Terraform / curl
2. ``sg_session`` cookie (HMAC-signed)     — Web UI after login
3. ``?token=<key>`` query parameter        — WebSocket connections

When **no** API key is configured the auth dependency is a no-op so
Shoreguard stays zero-config for local development.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import logging
import secrets
import time
from typing import TYPE_CHECKING

from fastapi import Cookie, HTTPException, Query, Request, WebSocket, status

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker as SessionMaker

logger = logging.getLogger(__name__)

# ─── Roles ──────────────────────────────────────────────────────────────────

ROLES = ("admin", "operator", "viewer")
_ROLE_RANK: dict[str, int] = {"admin": 2, "operator": 1, "viewer": 0}

# ─── Module state (set once at startup via configure()) ─────────────────────

_api_key: str | None = None
_hmac_secret: bytes = b""
_session_factory: SessionMaker | None = None

# Cookie / session settings
COOKIE_NAME = "sg_session"
SESSION_MAX_AGE = 86400 * 7  # 7 days


def configure(api_key: str | None, session_factory: SessionMaker | None = None) -> None:
    """Store the API key and derive an HMAC secret for session cookies.

    Called once from the CLI entry-point before Uvicorn starts, and again
    from the lifespan once the database is available.
    """
    global _api_key, _hmac_secret, _session_factory  # noqa: PLW0603
    if session_factory is not None:
        _session_factory = session_factory
    if api_key is not None:
        _api_key = api_key
        _hmac_secret = hashlib.sha256(api_key.encode()).digest()
        logger.info("API-key authentication enabled")


def reset() -> None:
    """Reset all auth state. For test teardown only."""
    global _api_key, _hmac_secret, _session_factory  # noqa: PLW0603
    _api_key = None
    _hmac_secret = b""
    _session_factory = None


def is_auth_enabled() -> bool:
    """Return True when an API key has been configured."""
    return _api_key is not None


# ─── Key hashing ────────────────────────────────────────────────────────────


def _hash_key(key: str) -> str:
    """Return the SHA-256 hex digest of a key."""
    return hashlib.sha256(key.encode()).hexdigest()


# ─── DB key lookup ──────────────────────────────────────────────────────────


def _lookup_db_key(key: str) -> str | None:
    """Look up a Bearer token in the api_keys table.

    Returns the role if found, None otherwise.  Updates ``last_used``.
    """
    if _session_factory is None:
        return None
    from shoreguard.models import ApiKey

    key_hash = _hash_key(key)
    session = _session_factory()
    try:
        row = session.query(ApiKey).filter(ApiKey.key_hash == key_hash).first()
        if row is None:
            return None
        row.last_used = datetime.datetime.now(datetime.UTC).isoformat()
        session.commit()
        return row.role
    except Exception:
        session.rollback()
        logger.exception("DB key lookup failed")
        return None
    finally:
        session.close()


# ─── Session cookie helpers ─────────────────────────────────────────────────


def create_session_token(role: str = "admin") -> str:
    """Create an HMAC-signed session token: ``<nonce>.<expiry>.<role>.<signature>``."""
    nonce = secrets.token_urlsafe(24)
    expiry = str(int(time.time()) + SESSION_MAX_AGE)
    payload = f"{nonce}.{expiry}.{role}"
    sig = hmac.new(_hmac_secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token: str) -> str | None:
    """Verify the HMAC signature and expiry of a session token.

    Returns the role if valid, None otherwise.
    """
    parts = token.split(".")
    if len(parts) != 4:
        return None
    nonce, expiry_str, role, sig = parts
    if role not in ROLES:
        return None
    payload = f"{nonce}.{expiry_str}.{role}"
    expected = hmac.new(_hmac_secret, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        logger.debug("Session token verification failed: bad signature")
        return None
    try:
        if int(expiry_str) < int(time.time()):
            logger.debug("Session token verification failed: expired")
            return None
    except ValueError:
        return None
    return role


# ─── Credential resolution ──────────────────────────────────────────────────


def _resolve_bearer_role(token: str) -> str | None:
    """Resolve a Bearer token to a role.

    Checks the legacy single key first, then the database.
    """
    # Legacy single key → admin
    if _api_key and hmac.compare_digest(token.encode(), _api_key.encode()):
        return "admin"
    # DB lookup
    return _lookup_db_key(token)


# ─── FastAPI dependencies ──────────────────────────────────────────────────


def check_request_auth(request: Request) -> str | None:
    """Return the role for the request, or None if unauthenticated.

    Used by both ``require_auth`` and the ``/api/auth/check`` endpoint.
    """
    if not _api_key:
        return "admin"  # auth disabled → full access

    # 1. Bearer token (scheme is case-insensitive per RFC 7235)
    auth_header = request.headers.get("authorization", "")
    if auth_header[:7].lower() == "bearer ":
        token = auth_header[7:]
        role = _resolve_bearer_role(token)
        if role:
            logger.debug("HTTP auth via Bearer token (path=%s, role=%s)", request.url.path, role)
            return role

    # 2. Session cookie
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        role = verify_session_token(cookie)
        if role:
            logger.debug("HTTP auth via session cookie (path=%s, role=%s)", request.url.path, role)
            return role

    return None


def require_auth(request: Request) -> None:
    """Reject the request when auth is enabled and no valid credential is found."""
    role = check_request_auth(request)
    if role is not None:
        request.state.role = role
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


def require_role(minimum: str):
    """Return a FastAPI dependency that enforces a minimum role level."""

    def _dependency(request: Request) -> None:
        role = getattr(request.state, "role", None)
        # If require_auth hasn't run yet (e.g. route not under require_auth),
        # resolve the role now.
        if role is None:
            role = check_request_auth(request)
            if role is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or missing API key",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            request.state.role = role
        if _ROLE_RANK.get(role, -1) < _ROLE_RANK[minimum]:
            logger.warning(
                "Role check failed: %s < %s (path=%s)",
                role,
                minimum,
                request.url.path,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {minimum} role",
            )

    return _dependency


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
    if token:
        role = _resolve_bearer_role(token)
        if role:
            logger.debug(
                "WebSocket auth via query-param token (path=%s, role=%s)", websocket.url.path, role
            )
            return

    # 2. Session cookie
    if sg_session:
        role = verify_session_token(sg_session)
        if role:
            logger.debug(
                "WebSocket auth via session cookie (path=%s, role=%s)", websocket.url.path, role
            )
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


# ─── Login / logout helpers ────────────────────────────────────────────────


def check_api_key(key: str) -> str | None:
    """Return the role for *key*, or None if invalid."""
    if not _api_key:
        return None
    # Legacy single key → admin
    if hmac.compare_digest(key.encode(), _api_key.encode()):
        return "admin"
    # DB lookup
    return _lookup_db_key(key)


# ─── API key CRUD ──────────────────────────────────────────────────────────


def create_api_key(name: str, role: str) -> tuple[str, dict]:
    """Create a new API key and store it in the database.

    Returns ``(plaintext_key, info_dict)``.
    """
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role!r} (must be one of {ROLES})")
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import ApiKey

    plaintext = secrets.token_urlsafe(32)
    key_hash = _hash_key(plaintext)
    now = datetime.datetime.now(datetime.UTC).isoformat()

    session = _session_factory()
    try:
        row = ApiKey(name=name, key_hash=key_hash, role=role, created_at=now)
        session.add(row)
        session.commit()
        info = {"name": name, "role": role, "created_at": now}
        return plaintext, info
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def list_api_keys() -> list[dict]:
    """Return all API keys (without hashes)."""
    if _session_factory is None:
        return []
    from shoreguard.models import ApiKey

    session = _session_factory()
    try:
        rows = session.query(ApiKey).order_by(ApiKey.created_at).all()
        return [
            {
                "name": r.name,
                "role": r.role,
                "created_at": r.created_at,
                "last_used": r.last_used,
            }
            for r in rows
        ]
    finally:
        session.close()


def delete_api_key(name: str) -> bool:
    """Delete an API key by name. Returns True if found."""
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import ApiKey

    session = _session_factory()
    try:
        row = session.query(ApiKey).filter(ApiKey.name == name).first()
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def bootstrap_admin_key(api_key: str | None) -> None:
    """Seed the api_keys table with the bootstrap admin key if empty."""
    if not api_key or _session_factory is None:
        return
    from shoreguard.models import ApiKey

    session = _session_factory()
    try:
        count = session.query(ApiKey).count()
        if count > 0:
            return
        key_hash = _hash_key(api_key)
        now = datetime.datetime.now(datetime.UTC).isoformat()
        row = ApiKey(name="bootstrap", key_hash=key_hash, role="admin", created_at=now)
        session.add(row)
        session.commit()
        logger.info("Bootstrap admin key seeded into database")
    except Exception:
        session.rollback()
        logger.exception("Failed to bootstrap admin key")
    finally:
        session.close()
