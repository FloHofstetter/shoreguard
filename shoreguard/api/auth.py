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
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from fastapi import Cookie, HTTPException, Query, Request, WebSocket, status
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from shoreguard.exceptions import NotFoundError
from shoreguard.exceptions import ValidationError as DomainValidationError

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker as SessionMaker

logger = logging.getLogger(__name__)

# ─── Roles ──────────────────────────────────────────────────────────────────

ROLES = ("admin", "operator", "viewer")
_ROLE_RANK: dict[str, int] = {"admin": 2, "operator": 1, "viewer": 0}

_SENTINEL = object()  # default marker for optional kwargs

# ─── Password hashing ──────────────────────────────────────────────────────

_hasher = PasswordHash((BcryptHasher(),))


def hash_password(password: str) -> str:
    """Hash a plaintext password.

    Args:
        password: The plaintext password to hash.

    Returns:
        str: Bcrypt-hashed password string.
    """
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify a plaintext password against a hash.

    Args:
        password: The plaintext password to verify.
        hashed: The bcrypt hash to verify against.

    Returns:
        bool: ``True`` if the password matches.
    """
    try:
        return _hasher.verify(password, hashed)
    except ValueError, TypeError:
        # Corrupt or unrecognised hash format — treat as non-match.
        logger.warning("Password verification error (corrupt hash?)", exc_info=True)
        return False


# ─── Module state ──────────────────────────────────────────────────────────

_session_factory: SessionMaker | None = None
_hmac_secret: bytes = b""
_no_auth: bool = False


def _get_auth_settings():  # noqa: ANN202
    """Return auth settings from the central Settings singleton."""  # noqa: DOC201
    from shoreguard.settings import get_settings

    return get_settings().auth


# Module-level aliases so existing ``from .auth import COOKIE_NAME`` works.
# The values are read once at import time; if they ever need to vary at
# runtime, call ``_get_auth_settings()`` directly instead.
COOKIE_NAME = "sg_session"
SESSION_MAX_AGE = 86400 * 7  # 7 days


def _load_or_create_secret_key() -> bytes:
    """Load or generate the HMAC secret key for session cookies.

    Returns:
        bytes: 32-byte HMAC signing key.
    """
    auth_cfg = _get_auth_settings()
    if auth_cfg.secret_key:
        return hashlib.sha256(auth_cfg.secret_key.encode()).digest()

    from shoreguard.config import shoreguard_config_dir

    key_file = shoreguard_config_dir() / ".secret_key"
    if key_file.exists():
        return key_file.read_bytes()

    key_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret = secrets.token_bytes(32)
    fd = os.open(str(key_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, secret)
    finally:
        os.close(fd)
    logger.info("Generated new secret key at %s", key_file)
    return secret


def init_auth(session_factory: SessionMaker) -> None:
    """Initialise the auth module with a DB session factory.

    Called once from the application lifespan.

    Args:
        session_factory: SQLAlchemy session factory bound to the engine.
    """
    global _session_factory, _hmac_secret, _no_auth  # noqa: PLW0603
    _session_factory = session_factory
    _hmac_secret = _load_or_create_secret_key()
    _no_auth = _get_auth_settings().no_auth


def reset() -> None:
    """Reset all auth state. For test teardown only."""
    global _session_factory, _hmac_secret, _no_auth  # noqa: PLW0603
    _session_factory = None
    _hmac_secret = b""
    _no_auth = False
    _account_failures.clear()


def init_auth_for_test(session_factory: SessionMaker) -> None:
    """Initialise auth with a test DB and a fixed HMAC secret.

    Args:
        session_factory: SQLAlchemy session factory for the test database.
    """
    global _session_factory, _hmac_secret, _no_auth  # noqa: PLW0603
    _session_factory = session_factory
    _hmac_secret = b"test-secret-key-for-unit-tests!!"
    _no_auth = False


def is_registration_enabled() -> bool:
    """Return True when self-registration is allowed.

    Returns:
        bool: ``True`` if ``SHOREGUARD_ALLOW_REGISTRATION`` is set.
    """
    return _get_auth_settings().allow_registration


def is_setup_complete() -> bool:
    """Return True when at least one user exists in the database.

    Returns:
        bool: ``True`` if at least one user row exists.
    """
    if _session_factory is None:
        return False
    from shoreguard.models import User

    with _session_factory() as session:
        try:
            return session.query(User).count() > 0
        except SQLAlchemyError:
            logger.exception("Failed to check setup status")
            return False


# ─── Key hashing (for service principals) ───────────────────────────────────


def _hash_key(key: str) -> str:
    """Return the SHA-256 hex digest of a service principal key.

    Args:
        key: Plaintext API key.

    Returns:
        str: Hex-encoded SHA-256 digest.
    """
    return hashlib.sha256(key.encode()).hexdigest()


# ─── Session cookie helpers ─────────────────────────────────────────────────


def create_session_token(user_id: int, role: str) -> str:
    """Create an HMAC-signed session token.

    Format: ``<nonce>.<expiry>.<user_id>.<role>.<signature>``

    Args:
        user_id: Database ID of the authenticated user.
        role: The user's current role.

    Returns:
        str: Signed session token string.
    """
    nonce = secrets.token_urlsafe(24)
    expiry = str(int(time.time()) + _get_auth_settings().session_max_age)
    payload = f"{nonce}.{expiry}.{user_id}.{role}"
    sig = hmac.new(_hmac_secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token: str) -> tuple[int, str] | None:
    """Verify a session token and return ``(user_id, role)`` or None.

    Args:
        token: The session token string to verify.

    Returns:
        tuple[int, str] | None: ``(user_id, role)`` if valid, else ``None``.
    """
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
    """Look up a service principal by Bearer token. Returns role or None.

    .. deprecated:: Use :func:`_lookup_sp_identity` for new code.

    Args:
        key: Plaintext API key from the Bearer header.

    Returns:
        str | None: Role string or ``None`` if not found.
    """
    result = _lookup_sp_identity(key)
    return result["role"] if result else None


def authenticate_user(email: str, password: str) -> dict | None:
    """Verify user credentials. Returns user info dict or None.

    Uses constant-time comparison to prevent timing-based email enumeration:
    a dummy bcrypt hash is verified when the user does not exist so that the
    response time is indistinguishable from a wrong-password attempt.

    Args:
        email: User email address.
        password: Plaintext password to verify.

    Returns:
        dict | None: ``{id, email, role}`` on success, else ``None``.
    """
    if _session_factory is None:
        return None
    from shoreguard.models import User

    email = email.strip().lower()
    with _session_factory() as session:
        user = session.query(User).filter(User.email == email).first()

        # Always run bcrypt to prevent timing-based user enumeration.
        # The dummy hash is a valid bcrypt hash that will never match.
        _DUMMY_HASH = "$2b$12$LJ3m4ys3Lg2VBe50VdnCJOIBbGMkGLWMFwxL8MKGqUVAyGYQz/mPa"
        valid_user = (
            user is not None
            and user.is_active
            and user.invite_token_hash is None
            and user.hashed_password is not None
        )
        password_ok = verify_password(password, user.hashed_password if valid_user else _DUMMY_HASH)

        if not valid_user or not password_ok:
            logger.warning("Auth failed: invalid credentials (email=%s)", email)
            return None
        return {"id": user.id, "email": user.email, "role": user.role}


# ── Account lockout ──────────────────────────────────────────────────────────
# In-memory tracking of failed login attempts per (normalized) email.
# Complements IP-based rate limiting: rate limiting blocks one IP attacking
# many accounts; account lockout blocks many IPs attacking one account.

_account_failures: dict[str, tuple[int, float]] = {}


def record_failed_login(email: str) -> None:
    """Increment the failure counter for *email*.

    Args:
        email: The email that failed authentication (will be lowered).
    """
    import time

    key = email.strip().lower()
    count, _ = _account_failures.get(key, (0, 0.0))
    _account_failures[key] = (count + 1, time.monotonic())


def is_account_locked(email: str) -> tuple[bool, int]:
    """Check whether *email* is temporarily locked due to repeated failures.

    Args:
        email: The email to check.

    Returns:
        tuple[bool, int]: ``(locked, retry_after_seconds)``. When *locked* is
        ``True``, *retry_after* indicates how long the caller should wait.
    """
    import time

    from shoreguard.settings import get_settings

    key = email.strip().lower()
    entry = _account_failures.get(key)
    if entry is None:
        return False, 0

    count, last_failure = entry
    settings = get_settings().auth
    if count < settings.account_lockout_attempts:
        return False, 0

    elapsed = time.monotonic() - last_failure
    remaining = settings.account_lockout_duration - elapsed
    if remaining <= 0:
        # Lockout expired — clear
        _account_failures.pop(key, None)
        return False, 0

    return True, int(remaining) + 1


def clear_lockout(email: str) -> None:
    """Clear the failure counter on successful login.

    Args:
        email: The email to clear.
    """
    _account_failures.pop(email.strip().lower(), None)


def reset_lockouts() -> None:
    """Clear all lockout state (for tests)."""
    _account_failures.clear()


def _lookup_user(user_id: int) -> dict | None:
    """Return ``{id, email, role}`` if the user exists and is active, else None.

    Args:
        user_id: Database ID of the user.

    Returns:
        dict | None: User info dict or ``None``.
    """
    if _session_factory is None:
        return None
    from shoreguard.models import User

    with _session_factory() as session:
        user = session.query(User).filter(User.id == user_id).first()
        if user is None or not user.is_active:
            return None
        return {"id": user.id, "email": user.email, "role": user.role}


def _lookup_sp_identity(key: str) -> dict | None:
    """Look up a service principal by Bearer token. Returns ``{name, role}`` or None.

    Args:
        key: Plaintext API key from the Bearer header.

    Returns:
        dict | None: ``{id, name, role}`` or ``None`` if not found.
    """
    if _session_factory is None:
        return None
    from shoreguard.models import ServicePrincipal

    key_hash = _hash_key(key)
    with _session_factory() as session:
        try:
            row = (
                session.query(ServicePrincipal)
                .filter(ServicePrincipal.key_hash == key_hash)
                .first()
            )
            if row is None:
                return None
            if row.expires_at is not None and row.expires_at.replace(
                tzinfo=row.expires_at.tzinfo or datetime.UTC,
            ) <= datetime.datetime.now(datetime.UTC):
                logger.info("Service principal '%s' has expired", row.name)
                return None
            row.last_used = datetime.datetime.now(datetime.UTC)
            session.commit()
            return {"id": row.id, "name": row.name, "role": row.role}
        except SQLAlchemyError:
            session.rollback()
            logger.exception("SP key lookup failed")
            return None


# ─── Credential resolution ──────────────────────────────────────────────────


def check_request_auth(request: Request) -> str | None:
    """Return the role for the request, or None if unauthenticated.

    Sets ``request.state.role`` and ``request.state.user_id`` on success.
    The role is always read from the **database** (not the session token)
    so that demotions / deactivations take effect immediately.

    Args:
        request: The incoming HTTP request.

    Returns:
        str | None: Role string or ``None`` if unauthenticated.

    Raises:
        HTTPException: 503 if the database session factory is not initialised.
    """
    if _no_auth:
        request.state.user_id = "no-auth"
        return "admin"
    if _session_factory is None:
        logger.error("Auth check with no DB session factory — denying request")
        raise HTTPException(status_code=503, detail="Service not ready")
    if not is_setup_complete():
        request.state.user_id = "setup-pending"
        # Only allow setup-related paths before first user is created
        path = request.url.path
        if path in ("/api/auth/setup", "/api/auth/check", "/setup") or path.startswith(
            ("/static/", "/favicon")
        ):
            return "admin"
        return None  # block all other API access until setup is complete

    # 1. Bearer token → service principal
    auth_header = request.headers.get("authorization", "")
    if auth_header[:7].lower() == "bearer ":
        token = auth_header[7:]
        sp = _lookup_sp_identity(token)
        if sp:
            request.state.user_id = f"sp:{sp['name']}"
            request.state.sp_db_id = sp["id"]
            logger.debug(
                "Auth via SP Bearer token (path=%s, role=%s)", request.url.path, sp["role"]
            )
            return sp["role"]

    # 2. Session cookie → user
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        result = verify_session_token(cookie)
        if result:
            user_id, _token_role = result
            user_info = _lookup_user(user_id)
            if user_info:
                request.state.user_id = user_info["email"]
                request.state.user_db_id = user_info["id"]
                logger.debug(
                    "Auth via session cookie (path=%s, role=%s, user=%s)",
                    request.url.path,
                    user_info["role"],
                    user_info["email"],
                )
                return user_info["role"]
            logger.warning("Session for inactive/deleted user_id=%d", user_id)

    return None


# ─── Gateway-scoped role lookup ───────────────────────────────────────────


class _GatewayRoleLookupError(Exception):
    """Raised when the gateway role DB lookup fails — triggers a 503."""


def _lookup_gateway_role(
    *, user_id: int | None = None, sp_id: int | None = None, gateway: str
) -> str | None:
    """Return the gateway-scoped role override, or None if no override exists.

    Raises ``_GatewayRoleLookupError`` on DB failure so the caller does NOT
    silently fall back to the (possibly higher) global role (fail-closed).

    Args:
        user_id: Database ID of the user, or ``None``.
        sp_id: Database ID of the service principal, or ``None``.
        gateway: Gateway name to look up the scoped role for.

    Returns:
        str | None: Scoped role string or ``None`` if no override.

    Raises:
        _GatewayRoleLookupError: If the DB query fails.
    """
    if _session_factory is None:
        return None
    from shoreguard.models import (
        Gateway,
        GroupGatewayRole,
        GroupMember,
        SPGatewayRole,
        UserGatewayRole,
    )

    with _session_factory() as session:
        try:
            if user_id is not None:
                # Priority 1: individual gateway role
                row = (
                    session.query(UserGatewayRole)
                    .join(Gateway, UserGatewayRole.gateway_id == Gateway.id)
                    .filter(
                        UserGatewayRole.user_id == user_id,
                        Gateway.name == gateway,
                    )
                    .first()
                )
                if row:
                    return row.role
                # Priority 2: group gateway role (highest rank wins)
                group_rows = (
                    session.query(GroupGatewayRole.role)
                    .join(GroupMember, GroupMember.group_id == GroupGatewayRole.group_id)
                    .join(Gateway, Gateway.id == GroupGatewayRole.gateway_id)
                    .filter(GroupMember.user_id == user_id, Gateway.name == gateway)
                    .all()
                )
                if group_rows:
                    return max((r[0] for r in group_rows), key=lambda r: _ROLE_RANK.get(r, -1))
                return None
            elif sp_id is not None:
                row = (
                    session.query(SPGatewayRole)
                    .join(Gateway, SPGatewayRole.gateway_id == Gateway.id)
                    .filter(SPGatewayRole.sp_id == sp_id, Gateway.name == gateway)
                    .first()
                )
                return row.role if row else None
            else:
                return None
        except SQLAlchemyError:
            logger.exception("Gateway role lookup failed (gateway=%s)", gateway)
            raise _GatewayRoleLookupError(f"Gateway role lookup failed for gateway={gateway}")


def _lookup_group_global_role(user_id: int) -> str | None:
    """Return the highest global role from all groups a user belongs to.

    Args:
        user_id: Database ID of the user.

    Returns:
        str | None: Highest group global role, or ``None`` if not in any group.

    Raises:
        _GatewayRoleLookupError: If the DB query fails.
    """
    if _session_factory is None:
        return None
    from shoreguard.models import Group, GroupMember

    with _session_factory() as session:
        try:
            rows = (
                session.query(Group.role)
                .join(GroupMember, GroupMember.group_id == Group.id)
                .filter(GroupMember.user_id == user_id)
                .all()
            )
            if not rows:
                return None
            return max((r[0] for r in rows), key=lambda r: _ROLE_RANK.get(r, -1))
        except SQLAlchemyError:
            logger.exception("Group global role lookup failed (user_id=%d)", user_id)
            raise _GatewayRoleLookupError(f"Group global role lookup failed for user_id={user_id}")


# ─── FastAPI dependencies ──────────────────────────────────────────────────


def require_auth(request: Request) -> None:
    """Reject unauthenticated requests (401).

    Args:
        request: The incoming HTTP request.

    Raises:
        HTTPException: 401 if credentials are missing or invalid.
    """
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


def require_role(minimum: str) -> Callable[..., Coroutine[Any, Any, None]]:
    """Return a FastAPI dependency that enforces a minimum role level.

    When inside a gateway-scoped route (gateway name on ``request.state``),
    a per-gateway role override takes precedence over the global role.

    Args:
        minimum: The minimum required role (``admin``, ``operator``, ``viewer``).

    Returns:
        Callable[..., Coroutine[Any, Any, None]]: An async FastAPI dependency
            callable.
    """
    from shoreguard.api.deps import get_gateway_name

    async def _dependency(request: Request) -> None:
        """Check that the caller has at least the required role.

        Args:
            request: The incoming HTTP request.

        Raises:
            HTTPException: 401 if unauthenticated, 403 if insufficient role.
        """
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

        # Check for a gateway-scoped role override
        gateway = get_gateway_name(request)
        if gateway:
            user_db_id = getattr(request.state, "user_db_id", None)
            sp_db_id = getattr(request.state, "sp_db_id", None)
            try:
                gw_role = _lookup_gateway_role(user_id=user_db_id, sp_id=sp_db_id, gateway=gateway)
            except _GatewayRoleLookupError:
                raise HTTPException(
                    status_code=503,
                    detail="Gateway role lookup failed — try again later",
                )
            if gw_role:
                role = gw_role
                request.state.role = role

        # Group global role fallback (only for users, elevates if higher)
        user_db_id = getattr(request.state, "user_db_id", None)
        if user_db_id is not None:
            try:
                group_global = _lookup_group_global_role(user_db_id)
            except _GatewayRoleLookupError:
                raise HTTPException(
                    status_code=503,
                    detail="Group role lookup failed — try again later",
                )
            if group_global and _ROLE_RANK.get(group_global, -1) > _ROLE_RANK.get(role, -1):
                role = group_global
                request.state.role = role

        if _ROLE_RANK.get(role, -1) < _ROLE_RANK[minimum]:
            actor = getattr(request.state, "user_id", "unknown")
            logger.warning(
                "Role check failed: %s < %s (path=%s, method=%s, actor=%s)",
                role,
                minimum,
                request.url.path,
                request.method,
                actor,
            )
            from shoreguard.services.audit import audit_log

            await audit_log(
                request,
                "auth.forbidden",
                "role",
                minimum,
                detail={"actor_role": role, "required_role": minimum},
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

    Accepts SP key via ``?token=`` or session cookie.

    Args:
        websocket: The WebSocket connection.
        token: Optional SP key from ``?token=`` query parameter.
        sg_session: Optional session cookie value.

    Raises:
        HTTPException: 403 if authentication fails.
    """
    if _no_auth:
        return
    if not is_setup_complete():
        return

    # 1. Query-param token → service principal
    if token:
        sp = _lookup_sp_identity(token)
        if sp:
            logger.debug(
                "WebSocket auth via SP token (path=%s, role=%s)", websocket.url.path, sp["role"]
            )
            return

    # 2. Session cookie → user
    if sg_session:
        result = verify_session_token(sg_session)
        if result:
            user_id, _ = result
            if _lookup_user(user_id) is not None:
                logger.debug("WebSocket auth via session cookie (path=%s)", websocket.url.path)
                return
            logger.warning("WebSocket session for inactive/deleted user_id=%d", user_id)

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

    Args:
        email: User email address.
        password: Plaintext password, or ``None`` for invite-based creation.
        role: One of ``admin``, ``operator``, ``viewer``.

    Returns:
        dict: User info dict (includes ``invite_token`` when applicable).

    Raises:
        DomainValidationError: If the role is invalid.
        RuntimeError: If the database is not available.
        IntegrityError: If the email already exists.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if role not in ROLES:
        raise DomainValidationError(f"Invalid role: {role!r}")
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import User

    email = email.strip().lower()
    now = datetime.datetime.now(datetime.UTC)
    invite_token = None
    invite_token_hash = None
    hashed_pw = None
    if password:
        hashed_pw = hash_password(password)
    else:
        invite_token = secrets.token_urlsafe(32)
        invite_token_hash = _hash_key(invite_token)

    with _session_factory() as session:
        try:
            user = User(
                email=email,
                hashed_password=hashed_pw,
                role=role,
                invite_token_hash=invite_token_hash,
                created_at=now,
            )
            session.add(user)
            session.commit()
            result: dict = {
                "id": user.id,
                "email": user.email,
                "role": user.role,
                "created_at": now.isoformat(),
            }
            if invite_token:
                result["invite_token"] = invite_token
            logger.info(
                "User created (id=%d, email=%s, role=%s, has_invite=%s)",
                user.id,
                email,
                role,
                invite_token is not None,
            )
            return result
        except IntegrityError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            logger.exception("Failed to create user (email=%s)", email)
            raise


INVITE_MAX_AGE = 86400 * 7  # 7 days — module-level alias for backwards compat


def accept_invite(token: str, password: str) -> dict | None:
    """Accept an invite by setting the user's password.

    Rejects tokens older than the configured invite max age.

    Args:
        token: The invite token from the invite link.
        password: The new plaintext password to set.

    Returns:
        dict | None: ``{id, email, role}`` on success, else ``None``.

    Raises:
        IntegrityError: On constraint violation during update.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if _session_factory is None:
        return None
    from shoreguard.models import User

    token_hash = _hash_key(token)
    with _session_factory() as session:
        try:
            user = (
                session.query(User)
                .filter(User.invite_token_hash == token_hash)
                .with_for_update()
                .first()
            )
            if user is None:
                return None
            # Check token age
            if user.created_at:
                created_at = user.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=datetime.UTC)
                age = (datetime.datetime.now(datetime.UTC) - created_at).total_seconds()
                if age > _get_auth_settings().invite_max_age:
                    logger.warning(
                        "Invite token expired (email=%s, age_hours=%.1f)", user.email, age / 3600
                    )
                    return None
            user.hashed_password = hash_password(password)
            user.invite_token_hash = None
            session.commit()
            logger.info(
                "Invite accepted (user_id=%d, email=%s, role=%s)",
                user.id,
                user.email,
                user.role,
            )
            return {"id": user.id, "email": user.email, "role": user.role}
        except IntegrityError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            logger.exception("Failed to accept invite")
            raise


def list_users() -> list[dict]:
    """Return all users (without password hashes).

    Returns:
        list[dict]: User info dicts ordered by creation time.
    """
    if _session_factory is None:
        return []
    from shoreguard.models import User

    with _session_factory() as session:
        try:
            rows = session.query(User).order_by(User.created_at).all()
            return [
                {
                    "id": r.id,
                    "email": r.email,
                    "role": r.role,
                    "is_active": r.is_active,
                    "pending_invite": r.invite_token_hash is not None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "oidc_provider": r.oidc_provider,
                }
                for r in rows
            ]
        except SQLAlchemyError:
            logger.exception("Failed to list users")
            return []


def find_or_create_oidc_user(email: str, oidc_provider: str, oidc_sub: str, role: str) -> dict:
    """Find an existing user or create one for an OIDC login.

    Lookup order:
    1. By ``(oidc_provider, oidc_sub)`` — returning OIDC user.
    2. By ``email`` — existing local user, link OIDC identity.
    3. No match — create a new user with OIDC identity, no password.

    Args:
        email: Email from the OIDC claims.
        oidc_provider: Provider name (e.g. ``"google"``).
        oidc_sub: The ``sub`` claim from the ID token.
        role: Role for new users (from role mapping).

    Returns:
        dict: Mapping with ``"user"`` (user info dict) and ``"action"``
        (``"login"``, ``"link"``, or ``"create"``).

    Raises:
        RuntimeError: If the database is not available.
    """
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import User

    email = email.strip().lower()

    with _session_factory() as session:
        # 1. Lookup by OIDC identity
        user = (
            session.query(User)
            .filter(User.oidc_provider == oidc_provider, User.oidc_sub == oidc_sub)
            .first()
        )
        if user:
            info = {"id": user.id, "email": user.email, "role": user.role}
            return {"user": info, "action": "login"}

        # 2. Lookup by email — link OIDC identity
        user = session.query(User).filter(User.email == email).first()
        if user:
            user.oidc_provider = oidc_provider
            user.oidc_sub = oidc_sub
            session.commit()
            logger.info("Linked OIDC identity (user=%s, provider=%s)", email, oidc_provider)
            info = {"id": user.id, "email": user.email, "role": user.role}
            return {"user": info, "action": "link"}

        # 3. Create new user
        now = datetime.datetime.now(datetime.UTC)
        if role not in ROLES:
            role = "viewer"
        user = User(
            email=email,
            hashed_password=None,
            role=role,
            created_at=now,
            oidc_provider=oidc_provider,
            oidc_sub=oidc_sub,
        )
        session.add(user)
        session.commit()
        logger.info(
            "Created OIDC user (id=%d, email=%s, provider=%s, role=%s)",
            user.id,
            email,
            oidc_provider,
            role,
        )
        info = {"id": user.id, "email": user.email, "role": user.role}
        return {"user": info, "action": "create"}


def delete_user(user_id: int) -> bool:
    """Delete a user by ID.

    Uses a single transaction with locked read to prevent TOCTOU races.

    Args:
        user_id: Database ID of the user to delete.

    Returns:
        bool: ``True`` if the user was found and deleted.

    Raises:
        DomainValidationError: If the user is the last active admin.
        ValueError: Re-raised after rollback on generic value errors.
        RuntimeError: If the database is not available.
        IntegrityError: On constraint violation.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import User

    with _session_factory() as session:
        try:
            row = session.query(User).filter(User.id == user_id).with_for_update().first()
            if row is None:
                return False
            if row.role == "admin" and row.is_active:
                admin_count = (
                    session.query(func.count(User.id))
                    .filter(
                        User.role == "admin",
                        User.is_active == True,  # noqa: E712
                        User.id != user_id,
                    )
                    .scalar()
                )
                if admin_count == 0:
                    raise DomainValidationError("Cannot delete the last active admin user")
            email, role = row.email, row.role
            session.delete(row)
            session.commit()
            logger.info(
                "User deleted from DB (user_id=%d, email=%s, role=%s)",
                user_id,
                email,
                role,
            )
            return True
        except IntegrityError:
            session.rollback()
            raise
        except ValueError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            logger.exception("Failed to delete user (user_id=%d)", user_id)
            raise


# ─── Service Principal CRUD ────────────────────────────────────────────────


def create_service_principal(
    name: str,
    role: str,
    created_by: int | None = None,
    expires_at: datetime.datetime | None = None,
) -> tuple[str, dict]:
    """Create a new service principal.

    Args:
        name: Human-readable name for the principal.
        role: One of ``admin``, ``operator``, ``viewer``.
        created_by: Database ID of the creating user, or ``None``.
        expires_at: Optional expiry timestamp; ``None`` means never expires.

    Returns:
        tuple[str, dict]: ``(plaintext_key, info_dict)``.

    Raises:
        DomainValidationError: If the role is invalid.
        RuntimeError: If the database is not available.
        IntegrityError: If the name already exists.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if role not in ROLES:
        raise DomainValidationError(f"Invalid role: {role!r}")
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import ServicePrincipal

    plaintext = "sg_" + secrets.token_urlsafe(32)
    key_hash = _hash_key(plaintext)
    key_prefix = plaintext[:12]
    now = datetime.datetime.now(datetime.UTC)

    with _session_factory() as session:
        try:
            sp = ServicePrincipal(
                name=name,
                key_hash=key_hash,
                key_prefix=key_prefix,
                role=role,
                created_by=created_by,
                created_at=now,
                expires_at=expires_at,
            )
            session.add(sp)
            session.commit()
            logger.info(
                "Service principal created (id=%d, name=%s, role=%s, created_by=%s)",
                sp.id,
                name,
                role,
                created_by,
            )
            return plaintext, {
                "id": sp.id,
                "name": name,
                "role": role,
                "key_prefix": key_prefix,
                "created_at": now.isoformat(),
                "expires_at": expires_at.isoformat() if expires_at else None,
            }
        except IntegrityError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            logger.exception("Failed to create service principal (name=%s)", name)
            raise


def list_service_principals() -> list[dict]:
    """Return all service principals (without key hashes).

    Returns:
        list[dict]: SP info dicts ordered by creation time.
    """
    if _session_factory is None:
        return []
    from shoreguard.models import ServicePrincipal

    with _session_factory() as session:
        try:
            rows = session.query(ServicePrincipal).order_by(ServicePrincipal.created_at).all()
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "role": r.role,
                    "key_prefix": r.key_prefix,
                    "created_by": r.created_by,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "last_used": r.last_used.isoformat() if r.last_used else None,
                    "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                }
                for r in rows
            ]
        except SQLAlchemyError:
            logger.exception("Failed to list service principals")
            return []


def delete_service_principal(sp_id: int) -> bool:
    """Delete a service principal by ID.

    Args:
        sp_id: Database ID of the service principal to delete.

    Returns:
        bool: ``True`` if the principal was found and deleted.

    Raises:
        RuntimeError: If the database is not available.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import ServicePrincipal

    with _session_factory() as session:
        try:
            row = session.query(ServicePrincipal).filter(ServicePrincipal.id == sp_id).first()
            if row is None:
                return False
            name, role = row.name, row.role
            session.delete(row)
            session.commit()
            logger.info("Service principal deleted (sp_id=%d, name=%s, role=%s)", sp_id, name, role)
            return True
        except Exception:
            session.rollback()
            logger.exception("Failed to delete service principal (sp_id=%d)", sp_id)
            raise


def rotate_service_principal(sp_id: int) -> tuple[str, dict] | None:
    """Rotate the API key for a service principal.

    Generates a new key and immediately invalidates the old one.

    Args:
        sp_id: Database ID of the service principal.

    Returns:
        tuple[str, dict] | None: ``(new_plaintext_key, info_dict)`` or ``None``
            if the principal was not found.

    Raises:
        RuntimeError: If the database is not available.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import ServicePrincipal

    new_plaintext = "sg_" + secrets.token_urlsafe(32)
    new_hash = _hash_key(new_plaintext)
    new_prefix = new_plaintext[:12]

    with _session_factory() as session:
        try:
            row = session.query(ServicePrincipal).filter(ServicePrincipal.id == sp_id).first()
            if row is None:
                return None
            row.key_hash = new_hash
            row.key_prefix = new_prefix
            session.commit()
            logger.info("Service principal key rotated (sp_id=%d, name=%s)", sp_id, row.name)
            return new_plaintext, {
                "id": row.id,
                "name": row.name,
                "role": row.role,
                "key_prefix": new_prefix,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            }
        except Exception:
            session.rollback()
            logger.exception("Failed to rotate service principal key (sp_id=%d)", sp_id)
            raise


# ─── Gateway-scoped role CRUD ─────────────────────────────────────────────


def set_gateway_role(
    *, user_id: int | None = None, sp_id: int | None = None, gateway_name: str, role: str
) -> dict:
    """Create or update a per-gateway role override.

    Args:
        user_id: Database ID of the user, or ``None``.
        sp_id: Database ID of the service principal, or ``None``.
        gateway_name: Name of the gateway to scope the role to.
        role: One of ``admin``, ``operator``, ``viewer``.

    Returns:
        dict: The saved role record.

    Raises:
        DomainValidationError: If the role is invalid.
        NotFoundError: If the gateway is not found.
        RuntimeError: If the database is not available.
        IntegrityError: On constraint violation.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if role not in ROLES:
        raise DomainValidationError(f"Invalid role: {role!r}")
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import Gateway, SPGatewayRole, UserGatewayRole

    with _session_factory() as session:
        try:
            gw = session.query(Gateway).filter(Gateway.name == gateway_name).first()
            if gw is None:
                raise NotFoundError(f"Gateway '{gateway_name}' not found")
            if user_id is not None:
                row = (
                    session.query(UserGatewayRole)
                    .filter(
                        UserGatewayRole.user_id == user_id,
                        UserGatewayRole.gateway_id == gw.id,
                    )
                    .first()
                )
                if row:
                    row.role = role
                else:
                    row = UserGatewayRole(user_id=user_id, gateway_id=gw.id, role=role)
                    session.add(row)
                session.commit()
                return {"user_id": user_id, "gateway_name": gateway_name, "role": role}
            elif sp_id is not None:
                row = (
                    session.query(SPGatewayRole)
                    .filter(
                        SPGatewayRole.sp_id == sp_id,
                        SPGatewayRole.gateway_id == gw.id,
                    )
                    .first()
                )
                if row:
                    row.role = role
                else:
                    row = SPGatewayRole(sp_id=sp_id, gateway_id=gw.id, role=role)
                    session.add(row)
                session.commit()
                return {"sp_id": sp_id, "gateway_name": gateway_name, "role": role}
            else:
                raise DomainValidationError("Either user_id or sp_id must be provided")
        except IntegrityError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            raise


def remove_gateway_role(
    *, user_id: int | None = None, sp_id: int | None = None, gateway_name: str
) -> bool:
    """Remove a per-gateway role override.

    Args:
        user_id: Database ID of the user, or ``None``.
        sp_id: Database ID of the service principal, or ``None``.
        gateway_name: Name of the gateway to remove the override for.

    Returns:
        bool: ``True`` if the override was found and removed.

    Raises:
        RuntimeError: If the database is not available.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import Gateway, SPGatewayRole, UserGatewayRole

    with _session_factory() as session:
        try:
            gw = session.query(Gateway).filter(Gateway.name == gateway_name).first()
            if gw is None:
                return False
            if user_id is not None:
                row = (
                    session.query(UserGatewayRole)
                    .filter(
                        UserGatewayRole.user_id == user_id,
                        UserGatewayRole.gateway_id == gw.id,
                    )
                    .first()
                )
            elif sp_id is not None:
                row = (
                    session.query(SPGatewayRole)
                    .filter(
                        SPGatewayRole.sp_id == sp_id,
                        SPGatewayRole.gateway_id == gw.id,
                    )
                    .first()
                )
            else:
                return False
            if row is None:
                return False
            session.delete(row)
            session.commit()
            logger.info(
                "Gateway role removed (user_id=%s, sp_id=%s, gateway=%s)",
                user_id,
                sp_id,
                gateway_name,
            )
            return True
        except Exception:
            session.rollback()
            logger.exception(
                "Failed to remove gateway role (user_id=%s, sp_id=%s, gateway=%s)",
                user_id,
                sp_id,
                gateway_name,
            )
            raise


def list_gateway_roles_for_user(user_id: int) -> list[dict]:
    """Return all gateway-scoped role overrides for a user.

    Args:
        user_id: Database ID of the user.

    Returns:
        list[dict]: Dicts with ``gateway_name`` and ``role`` keys.
    """
    if _session_factory is None:
        return []
    from shoreguard.models import Gateway, UserGatewayRole

    with _session_factory() as session:
        try:
            rows = (
                session.query(UserGatewayRole, Gateway.name)
                .join(Gateway, UserGatewayRole.gateway_id == Gateway.id)
                .filter(UserGatewayRole.user_id == user_id)
                .order_by(Gateway.name)
                .all()
            )
            return [{"gateway_name": gw_name, "role": r.role} for r, gw_name in rows]
        except SQLAlchemyError:
            logger.exception("Failed to list gateway roles for user %d", user_id)
            return []


def list_gateway_roles_for_sp(sp_id: int) -> list[dict]:
    """Return all gateway-scoped role overrides for a service principal.

    Args:
        sp_id: Database ID of the service principal.

    Returns:
        list[dict]: Dicts with ``gateway_name`` and ``role`` keys.
    """
    if _session_factory is None:
        return []
    from shoreguard.models import Gateway, SPGatewayRole

    with _session_factory() as session:
        try:
            rows = (
                session.query(SPGatewayRole, Gateway.name)
                .join(Gateway, SPGatewayRole.gateway_id == Gateway.id)
                .filter(SPGatewayRole.sp_id == sp_id)
                .order_by(Gateway.name)
                .all()
            )
            return [{"gateway_name": gw_name, "role": r.role} for r, gw_name in rows]
        except SQLAlchemyError:
            logger.exception("Failed to list gateway roles for SP %d", sp_id)
            return []


# ─── Group CRUD ────────────────────────────────────────────────────────────


def create_group(name: str, role: str = "viewer", description: str | None = None) -> dict:
    """Create a new user group.

    Args:
        name: Unique group name.
        role: Global group role (``admin``, ``operator``, ``viewer``).
        description: Optional human-readable description.

    Returns:
        dict: The created group info.

    Raises:
        DomainValidationError: If the role is invalid.
        RuntimeError: If the database is not available.
        IntegrityError: If the name already exists.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if role not in ROLES:
        raise DomainValidationError(f"Invalid role: {role!r}")
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import Group

    now = datetime.datetime.now(datetime.UTC)
    with _session_factory() as session:
        try:
            group = Group(name=name.strip(), description=description, role=role, created_at=now)
            session.add(group)
            session.commit()
            logger.info("Group created (id=%d, name=%s, role=%s)", group.id, name, role)
            return {
                "id": group.id,
                "name": group.name,
                "description": group.description,
                "role": group.role,
                "created_at": now.isoformat(),
                "member_count": 0,
            }
        except IntegrityError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            logger.exception("Failed to create group (name=%s)", name)
            raise


def update_group(
    group_id: int,
    *,
    name: str | None = None,
    role: str | None = None,
    description: str | None | object = _SENTINEL,
) -> dict:
    """Update a group's name, role, or description.

    Args:
        group_id: Database ID of the group.
        name: New name, or ``None`` to keep unchanged.
        role: New role, or ``None`` to keep unchanged.
        description: New description, or sentinel to keep unchanged.

    Returns:
        dict: The updated group info.

    Raises:
        DomainValidationError: If the role is invalid.
        NotFoundError: If the group is not found.
        RuntimeError: If the database is not available.
        IntegrityError: On constraint violation.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if role is not None and role not in ROLES:
        raise DomainValidationError(f"Invalid role: {role!r}")
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import Group

    with _session_factory() as session:
        try:
            group = session.query(Group).filter(Group.id == group_id).first()
            if group is None:
                raise NotFoundError(f"Group {group_id} not found")
            if name is not None:
                group.name = name.strip()
            if role is not None:
                group.role = role
            if description is not _SENTINEL:
                group.description = description
            session.commit()
            return {
                "id": group.id,
                "name": group.name,
                "description": group.description,
                "role": group.role,
                "created_at": group.created_at.isoformat(),
            }
        except IntegrityError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            raise


def delete_group(group_id: int) -> bool:
    """Delete a group (CASCADE removes memberships and gateway roles).

    Args:
        group_id: Database ID of the group.

    Returns:
        bool: ``True`` if deleted, ``False`` if not found.

    Raises:
        RuntimeError: If the database is not available.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import Group

    with _session_factory() as session:
        try:
            group = session.query(Group).filter(Group.id == group_id).first()
            if group is None:
                return False
            session.delete(group)
            session.commit()
            logger.info("Group deleted (id=%d)", group_id)
            return True
        except Exception:
            session.rollback()
            logger.exception("Failed to delete group (id=%d)", group_id)
            raise


def list_groups() -> list[dict]:
    """Return all groups with member counts.

    Returns:
        list[dict]: Group info dicts ordered by name.
    """
    if _session_factory is None:
        return []
    from shoreguard.models import Group, GroupMember

    with _session_factory() as session:
        try:
            from sqlalchemy import func

            rows = (
                session.query(
                    Group,
                    func.count(GroupMember.id).label("member_count"),
                )
                .outerjoin(GroupMember, GroupMember.group_id == Group.id)
                .group_by(Group.id)
                .order_by(Group.name)
                .all()
            )
            return [
                {
                    "id": g.id,
                    "name": g.name,
                    "description": g.description,
                    "role": g.role,
                    "created_at": g.created_at.isoformat(),
                    "member_count": cnt,
                }
                for g, cnt in rows
            ]
        except SQLAlchemyError:
            logger.exception("Failed to list groups")
            return []


def get_group(group_id: int) -> dict | None:
    """Return a group with its member list.

    Args:
        group_id: Database ID of the group.

    Returns:
        dict | None: Group info with ``members`` list, or ``None``.
    """
    if _session_factory is None:
        return None
    from shoreguard.models import Group, GroupMember, User

    with _session_factory() as session:
        try:
            group = session.query(Group).filter(Group.id == group_id).first()
            if group is None:
                return None
            members = (
                session.query(User.id, User.email, User.role)
                .join(GroupMember, GroupMember.user_id == User.id)
                .filter(GroupMember.group_id == group_id)
                .order_by(User.email)
                .all()
            )
            return {
                "id": group.id,
                "name": group.name,
                "description": group.description,
                "role": group.role,
                "created_at": group.created_at.isoformat(),
                "members": [
                    {"id": uid, "email": email, "role": role} for uid, email, role in members
                ],
            }
        except SQLAlchemyError:
            logger.exception("Failed to get group %d", group_id)
            return None


# ─── Group membership ──────────────────────────────────────────────────────


def add_group_member(group_id: int, user_id: int) -> dict:
    """Add a user to a group.

    Args:
        group_id: Database ID of the group.
        user_id: Database ID of the user.

    Returns:
        dict: Membership info.

    Raises:
        NotFoundError: If the group or user is not found.
        RuntimeError: If the database is not available.
        IntegrityError: If the membership already exists.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import Group, GroupMember, User

    with _session_factory() as session:
        try:
            group = session.query(Group).filter(Group.id == group_id).first()
            if group is None:
                raise NotFoundError(f"Group {group_id} not found")
            user = session.query(User).filter(User.id == user_id).first()
            if user is None:
                raise NotFoundError(f"User {user_id} not found")
            membership = GroupMember(group_id=group_id, user_id=user_id)
            session.add(membership)
            session.commit()
            logger.info("Added user %d to group %d", user_id, group_id)
            return {
                "group_id": group_id,
                "group_name": group.name,
                "user_id": user_id,
                "user_email": user.email,
            }
        except IntegrityError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            raise


def remove_group_member(group_id: int, user_id: int) -> bool:
    """Remove a user from a group.

    Args:
        group_id: Database ID of the group.
        user_id: Database ID of the user.

    Returns:
        bool: ``True`` if removed, ``False`` if membership not found.

    Raises:
        RuntimeError: If the database is not available.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import GroupMember

    with _session_factory() as session:
        try:
            row = (
                session.query(GroupMember)
                .filter(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
                .first()
            )
            if row is None:
                return False
            session.delete(row)
            session.commit()
            logger.info("Removed user %d from group %d", user_id, group_id)
            return True
        except Exception:
            session.rollback()
            raise


def list_group_members(group_id: int) -> list[dict]:
    """Return all members of a group.

    Args:
        group_id: Database ID of the group.

    Returns:
        list[dict]: Member dicts with ``id``, ``email``, ``role``.
    """
    if _session_factory is None:
        return []
    from shoreguard.models import GroupMember, User

    with _session_factory() as session:
        try:
            rows = (
                session.query(User.id, User.email, User.role)
                .join(GroupMember, GroupMember.user_id == User.id)
                .filter(GroupMember.group_id == group_id)
                .order_by(User.email)
                .all()
            )
            return [{"id": uid, "email": email, "role": role} for uid, email, role in rows]
        except SQLAlchemyError:
            logger.exception("Failed to list members for group %d", group_id)
            return []


def list_user_groups(user_id: int) -> list[dict]:
    """Return all groups a user belongs to.

    Args:
        user_id: Database ID of the user.

    Returns:
        list[dict]: Group dicts with ``id``, ``name``, ``role``.
    """
    if _session_factory is None:
        return []
    from shoreguard.models import Group, GroupMember

    with _session_factory() as session:
        try:
            rows = (
                session.query(Group.id, Group.name, Group.role)
                .join(GroupMember, GroupMember.group_id == Group.id)
                .filter(GroupMember.user_id == user_id)
                .order_by(Group.name)
                .all()
            )
            return [{"id": gid, "name": name, "role": role} for gid, name, role in rows]
        except SQLAlchemyError:
            logger.exception("Failed to list groups for user %d", user_id)
            return []


# ─── Group gateway-scoped roles ───────────────────────────────────────────


def set_group_gateway_role(group_id: int, gateway_name: str, role: str) -> dict:
    """Create or update a per-gateway role override for a group.

    Args:
        group_id: Database ID of the group.
        gateway_name: Name of the gateway.
        role: One of ``admin``, ``operator``, ``viewer``.

    Returns:
        dict: The saved role record.

    Raises:
        DomainValidationError: If the role is invalid.
        NotFoundError: If the group or gateway is not found.
        RuntimeError: If the database is not available.
        IntegrityError: On constraint violation.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if role not in ROLES:
        raise DomainValidationError(f"Invalid role: {role!r}")
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import Gateway, Group, GroupGatewayRole

    with _session_factory() as session:
        try:
            group = session.query(Group).filter(Group.id == group_id).first()
            if group is None:
                raise NotFoundError(f"Group {group_id} not found")
            gw = session.query(Gateway).filter(Gateway.name == gateway_name).first()
            if gw is None:
                raise NotFoundError(f"Gateway '{gateway_name}' not found")
            row = (
                session.query(GroupGatewayRole)
                .filter(
                    GroupGatewayRole.group_id == group_id,
                    GroupGatewayRole.gateway_id == gw.id,
                )
                .first()
            )
            if row:
                row.role = role
            else:
                row = GroupGatewayRole(group_id=group_id, gateway_id=gw.id, role=role)
                session.add(row)
            session.commit()
            return {"group_id": group_id, "gateway_name": gateway_name, "role": role}
        except IntegrityError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            raise


def remove_group_gateway_role(group_id: int, gateway_name: str) -> bool:
    """Remove a per-gateway role override for a group.

    Args:
        group_id: Database ID of the group.
        gateway_name: Name of the gateway.

    Returns:
        bool: ``True`` if removed, ``False`` if not found.

    Raises:
        RuntimeError: If the database is not available.
        Exception: On unexpected DB errors (re-raised after rollback).
    """
    if _session_factory is None:
        raise RuntimeError("Database not available")
    from shoreguard.models import Gateway, GroupGatewayRole

    with _session_factory() as session:
        try:
            gw = session.query(Gateway).filter(Gateway.name == gateway_name).first()
            if gw is None:
                return False
            row = (
                session.query(GroupGatewayRole)
                .filter(
                    GroupGatewayRole.group_id == group_id,
                    GroupGatewayRole.gateway_id == gw.id,
                )
                .first()
            )
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise


def list_group_gateway_roles(group_id: int) -> list[dict]:
    """Return all gateway-scoped role overrides for a group.

    Args:
        group_id: Database ID of the group.

    Returns:
        list[dict]: Dicts with ``gateway_name`` and ``role`` keys.
    """
    if _session_factory is None:
        return []
    from shoreguard.models import Gateway, GroupGatewayRole

    with _session_factory() as session:
        try:
            rows = (
                session.query(GroupGatewayRole, Gateway.name)
                .join(Gateway, GroupGatewayRole.gateway_id == Gateway.id)
                .filter(GroupGatewayRole.group_id == group_id)
                .order_by(Gateway.name)
                .all()
            )
            return [{"gateway_name": gw_name, "role": r.role} for r, gw_name in rows]
        except SQLAlchemyError:
            logger.exception("Failed to list gateway roles for group %d", group_id)
            return []


# ─── Bootstrap ─────────────────────────────────────────────────────────────


def bootstrap_admin_user() -> None:
    """Seed the first admin user from env var if the users table is empty.

    Raises:
        Exception: If user creation fails (re-raised after logging).
    """
    password = _get_auth_settings().admin_password
    if not password or _session_factory is None:
        return
    if is_setup_complete():
        return
    try:
        create_user("admin@localhost", password, "admin")
        logger.info("Bootstrap admin user created (admin@localhost)")
    except Exception:
        logger.exception("Failed to bootstrap admin user")
        raise
