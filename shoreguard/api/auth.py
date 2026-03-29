"""User-based authentication with service principals for Shoreguard.

Two identity types:
- **Users**: email + password → session cookie (Web UI)
- **Service Principals**: API key → Bearer token (Terraform, CI/CD)

Both carry a role: admin, operator, viewer (hierarchical).

Three credential transports:
1. ``Authorization: Bearer <sp-key>`` header — API / Terraform / curl
2. ``sg_session`` cookie (HMAC-signed)      — Web UI after login
3. ``?token=<sp-key>`` query parameter      — WebSocket connections
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import TYPE_CHECKING

from fastapi import Cookie, HTTPException, Query, Request, WebSocket, status
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker as SessionMaker

logger = logging.getLogger(__name__)

# ─── Roles ──────────────────────────────────────────────────────────────────

ROLES = ("admin", "operator", "viewer")
_ROLE_RANK: dict[str, int] = {"admin": 2, "operator": 1, "viewer": 0}

# ─── Password hashing ──────────────────────────────────────────────────────

_hasher = PasswordHash((BcryptHasher(),))


def hash_password(password: str) -> str:
    """Hash a plaintext password."""
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify a plaintext password against a hash."""
    return _hasher.verify(password, hashed)


# ─── Module state ──────────────────────────────────────────────────────────

_session_factory: SessionMaker | None = None
_hmac_secret: bytes = b""

COOKIE_NAME = "sg_session"
SESSION_MAX_AGE = 86400 * 7  # 7 days


def _load_or_create_secret_key() -> bytes:
    """Load or generate the HMAC secret key for session cookies."""
    env_key = os.environ.get("SHOREGUARD_SECRET_KEY")
    if env_key:
        return hashlib.sha256(env_key.encode()).digest()

    from shoreguard.config import shoreguard_config_dir

    key_file = shoreguard_config_dir() / ".secret_key"
    if key_file.exists():
        return key_file.read_bytes()

    key_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret = secrets.token_bytes(32)
    key_file.write_bytes(secret)
    os.chmod(key_file, 0o600)
    logger.info("Generated new secret key at %s", key_file)
    return secret


def init_auth(session_factory: SessionMaker) -> None:
    """Initialise the auth module with a DB session factory.

    Called once from the application lifespan.
    """
    global _session_factory, _hmac_secret  # noqa: PLW0603
    _session_factory = session_factory
    _hmac_secret = _load_or_create_secret_key()


def reset() -> None:
    """Reset all auth state. For test teardown only."""
    global _session_factory, _hmac_secret  # noqa: PLW0603
    _session_factory = None
    _hmac_secret = b""


def init_auth_for_test(session_factory: SessionMaker) -> None:
    """Initialise auth with a test DB and a fixed HMAC secret."""
    global _session_factory, _hmac_secret  # noqa: PLW0603
    _session_factory = session_factory
    _hmac_secret = b"test-secret-key-for-unit-tests!!"


def is_setup_complete() -> bool:
    """Return True when at least one user exists in the database."""
    if _session_factory is None:
        return False
    from shoreguard.models import User

    session = _session_factory()
    try:
        return session.query(User).count() > 0
    finally:
        session.close()


# ─── Key hashing (for service principals) ───────────────────────────────────


def _hash_key(key: str) -> str:
    """Return the SHA-256 hex digest of a service principal key."""
    return hashlib.sha256(key.encode()).hexdigest()


# ─── Session cookie helpers ─────────────────────────────────────────────────


def create_session_token(user_id: int, role: str) -> str:
    """Create an HMAC-signed session token.

    Format: ``<nonce>.<expiry>.<user_id>.<role>.<signature>``
    """
    nonce = secrets.token_urlsafe(24)
    expiry = str(int(time.time()) + SESSION_MAX_AGE)
    payload = f"{nonce}.{expiry}.{user_id}.{role}"
    sig = hmac.new(_hmac_secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token: str) -> tuple[int, str] | None:
    """Verify a session token and return ``(user_id, role)`` or None."""
    parts = token.split(".")
    if len(parts) != 5:
        return None
    nonce, expiry_str, user_id_str, role, sig = parts
    if role not in ROLES:
        return None
    payload = f"{nonce}.{expiry_str}.{user_id_str}.{role}"
    expected = hmac.new(_hmac_secret, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        if int(expiry_str) < int(time.time()):
            return None
        user_id = int(user_id_str)
    except ValueError:
        return None
    return user_id, role


# ─── DB lookups ─────────────────────────────────────────────────────────────


def _lookup_sp(key: str) -> str | None:
    """Look up a service principal by Bearer token. Returns role or None."""
    if _session_factory is None:
        return None
    from shoreguard.models import ServicePrincipal

    key_hash = _hash_key(key)
    session = _session_factory()
    try:
        row = session.query(ServicePrincipal).filter(ServicePrincipal.key_hash == key_hash).first()
        if row is None:
            return None
        row.last_used = datetime.datetime.now(datetime.UTC).isoformat()
        session.commit()
        return row.role
    except Exception:
        session.rollback()
        logger.exception("SP key lookup failed")
        return None
    finally:
        session.close()


def authenticate_user(email: str, password: str) -> dict | None:
    """Verify user credentials. Returns user info dict or None."""
    if _session_factory is None:
        return None
    from shoreguard.models import User

    session = _session_factory()
    try:
        user = session.query(User).filter(User.email == email).first()
        if user is None or not user.is_active or user.invite_token is not None:
            return None
        if not user.hashed_password or not verify_password(password, user.hashed_password):
            return None
        return {"id": user.id, "email": user.email, "role": user.role}
    finally:
        session.close()


# ─── Credential resolution ──────────────────────────────────────────────────


def check_request_auth(request: Request) -> str | None:
    """Return the role for the request, or None if unauthenticated.

    Sets ``request.state.role`` on success.
    """
    if not is_setup_complete():
        return "admin"  # no users yet → unrestricted access (setup wizard handles redirect)

    # 1. Bearer token → service principal
    auth_header = request.headers.get("authorization", "")
    if auth_header[:7].lower() == "bearer ":
        token = auth_header[7:]
        role = _lookup_sp(token)
        if role:
            logger.debug("Auth via SP Bearer token (path=%s, role=%s)", request.url.path, role)
            return role

    # 2. Session cookie → user
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        result = verify_session_token(cookie)
        if result:
            _, role = result
            logger.debug("Auth via session cookie (path=%s, role=%s)", request.url.path, role)
            return role

    return None


# ─── FastAPI dependencies ──────────────────────────────────────────────────


def require_auth(request: Request) -> None:
    """Reject unauthenticated requests (401)."""
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
        detail="Invalid or missing credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_role(minimum: str):
    """Return a FastAPI dependency that enforces a minimum role level."""

    def _dependency(request: Request) -> None:
        role = getattr(request.state, "role", None)
        if role is None:
            role = check_request_auth(request)
            if role is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or missing credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            request.state.role = role
        if _ROLE_RANK.get(role, -1) < _ROLE_RANK[minimum]:
            logger.warning("Role check failed: %s < %s (path=%s)", role, minimum, request.url.path)
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

    Accepts SP key via ``?token=`` or session cookie.
    """
    if not is_setup_complete():
        return

    # 1. Query-param token → service principal
    if token:
        role = _lookup_sp(token)
        if role:
            logger.debug("WebSocket auth via SP token (path=%s, role=%s)", websocket.url.path, role)
            return

    # 2. Session cookie → user
    if sg_session:
        result = verify_session_token(sg_session)
        if result:
            logger.debug("WebSocket auth via session cookie (path=%s)", websocket.url.path)
            return

    client_ip = websocket.client.host if websocket.client else "unknown"
    logger.warning("WebSocket auth rejected (path=%s, client=%s)", websocket.url.path, client_ip)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="WebSocket authentication failed",
    )


# ─── User CRUD ─────────────────────────────────────────────────────────────


def create_user(email: str, password: str | None, role: str) -> dict:
    """Create a new user account.

    If *password* is None, an invite token is generated instead.
    The user must accept the invite to set their password.
    Returns user info dict (includes ``invite_token`` when applicable).
    """
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role!r}")
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import User

    now = datetime.datetime.now(datetime.UTC).isoformat()
    invite_token = None
    hashed_pw = None
    if password:
        hashed_pw = hash_password(password)
    else:
        invite_token = secrets.token_urlsafe(32)

    session = _session_factory()
    try:
        user = User(
            email=email,
            hashed_password=hashed_pw,
            role=role,
            invite_token=invite_token,
            created_at=now,
        )
        session.add(user)
        session.commit()
        result: dict = {"id": user.id, "email": user.email, "role": user.role, "created_at": now}
        if invite_token:
            result["invite_token"] = invite_token
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def accept_invite(token: str, password: str) -> dict | None:
    """Accept an invite by setting the user's password. Returns user info or None."""
    if _session_factory is None:
        return None
    from shoreguard.models import User

    session = _session_factory()
    try:
        user = session.query(User).filter(User.invite_token == token).first()
        if user is None:
            return None
        user.hashed_password = hash_password(password)
        user.invite_token = None
        session.commit()
        return {"id": user.id, "email": user.email, "role": user.role}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def list_users() -> list[dict]:
    """Return all users (without password hashes)."""
    if _session_factory is None:
        return []
    from shoreguard.models import User

    session = _session_factory()
    try:
        rows = session.query(User).order_by(User.created_at).all()
        return [
            {
                "id": r.id,
                "email": r.email,
                "role": r.role,
                "is_active": r.is_active,
                "pending_invite": r.invite_token is not None,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    finally:
        session.close()


def delete_user(user_id: int) -> bool:
    """Delete a user by ID. Returns True if found."""
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import User

    session = _session_factory()
    try:
        row = session.query(User).filter(User.id == user_id).first()
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


# ─── Service Principal CRUD ────────────────────────────────────────────────


def create_service_principal(
    name: str, role: str, created_by: int | None = None
) -> tuple[str, dict]:
    """Create a new service principal. Returns ``(plaintext_key, info_dict)``."""
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role!r}")
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import ServicePrincipal

    plaintext = secrets.token_urlsafe(32)
    key_hash = _hash_key(plaintext)
    now = datetime.datetime.now(datetime.UTC).isoformat()

    session = _session_factory()
    try:
        sp = ServicePrincipal(
            name=name, key_hash=key_hash, role=role, created_by=created_by, created_at=now
        )
        session.add(sp)
        session.commit()
        return plaintext, {"id": sp.id, "name": name, "role": role, "created_at": now}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def list_service_principals() -> list[dict]:
    """Return all service principals (without key hashes)."""
    if _session_factory is None:
        return []
    from shoreguard.models import ServicePrincipal

    session = _session_factory()
    try:
        rows = session.query(ServicePrincipal).order_by(ServicePrincipal.created_at).all()
        return [
            {
                "id": r.id,
                "name": r.name,
                "role": r.role,
                "created_by": r.created_by,
                "created_at": r.created_at,
                "last_used": r.last_used,
            }
            for r in rows
        ]
    finally:
        session.close()


def delete_service_principal(sp_id: int) -> bool:
    """Delete a service principal by ID. Returns True if found."""
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import ServicePrincipal

    session = _session_factory()
    try:
        row = session.query(ServicePrincipal).filter(ServicePrincipal.id == sp_id).first()
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


# ─── Bootstrap ─────────────────────────────────────────────────────────────


def bootstrap_admin_user() -> None:
    """Seed the first admin user from env var if the users table is empty."""
    password = os.environ.get("SHOREGUARD_ADMIN_PASSWORD")
    if not password or _session_factory is None:
        return
    if is_setup_complete():
        return
    try:
        create_user("admin@localhost", password, "admin")
        logger.info("Bootstrap admin user created (admin@localhost)")
    except Exception:
        logger.exception("Failed to bootstrap admin user")
