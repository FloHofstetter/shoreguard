"""Auth core: passwords, sessions, lockout, credential resolution, state."""

from __future__ import annotations

import datetime
import hashlib
import hmac
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request
from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from shoreguard.settings import AuthSettings

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
    except ValueError, TypeError, PwdlibError:
        # Corrupt or unrecognised hash format — treat as non-match.
        # PwdlibError covers UnknownHashError (garbage hash strings) and
        # HasherNotAvailable (hash format from a disabled hasher).
        logger.warning("Password verification error (corrupt hash?)", exc_info=True)
        return False


# ─── Module state ──────────────────────────────────────────────────────────


@dataclass
class _AuthState:
    """Mutable auth runtime state, shared by all auth submodules.

    Attributes:
        session_factory: DB session factory, set by :func:`init_auth`.
        hmac_secret: Secret for session-token HMAC signatures.
        no_auth: When True, every request is treated as an admin
            (``--no-auth`` development mode).
    """

    session_factory: async_sessionmaker[AsyncSession] | None = None
    hmac_secret: bytes = b""
    no_auth: bool = False


state = _AuthState()


def set_no_auth(value: bool) -> None:
    """Enable or disable the global auth bypass (dev mode / tests).

    Args:
        value: True to treat every request as an admin.
    """
    state.no_auth = value


def _get_auth_settings() -> AuthSettings:
    """Return auth settings from the central Settings singleton.

    Returns:
        AuthSettings: The auth subsection of the central Settings singleton.
    """
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


def init_auth(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Initialise the auth module with a DB session factory.

    Called once from the application lifespan.

    Args:
        session_factory: Async SQLAlchemy session factory bound to the engine.
    """
    state.session_factory = session_factory
    state.hmac_secret = _load_or_create_secret_key()
    state.no_auth = _get_auth_settings().no_auth


def reset() -> None:
    """Reset all auth state. For test teardown only."""
    state.session_factory = None
    state.hmac_secret = b""
    state.no_auth = False
    _account_failures.clear()


def init_auth_for_test(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Initialise auth with a test DB and a fixed HMAC secret.

    Args:
        session_factory: Async SQLAlchemy session factory for the test database.
    """
    state.session_factory = session_factory
    state.hmac_secret = b"test-secret-key-for-unit-tests!!"
    state.no_auth = False


def is_registration_enabled() -> bool:
    """Return True when self-registration is allowed.

    Returns:
        bool: ``True`` if ``SHOREGUARD_ALLOW_REGISTRATION`` is set.
    """
    return _get_auth_settings().allow_registration


async def is_setup_complete() -> bool:
    """Return True when at least one user exists in the database.

    Returns:
        bool: ``True`` if at least one user row exists.
    """
    if state.session_factory is None:
        return False
    from sqlalchemy import func, select

    from shoreguard.models import User

    async with state.session_factory() as session:
        try:
            count = (await session.execute(select(func.count()).select_from(User))).scalar()
            return (count or 0) > 0
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


def _sign_token(nonce: str, expiry: int, user_id: int, role: str) -> str:
    """Assemble and HMAC-sign a session token from its parts.

    Args:
        nonce: Random per-session nonce (also the session id material).
        expiry: Absolute expiry as a unix timestamp.
        user_id: Database ID of the authenticated user.
        role: The user's role.

    Returns:
        str: Signed token ``<nonce>.<expiry>.<user_id>.<role>.<signature>``.
    """
    payload = f"{nonce}.{expiry}.{user_id}.{role}"
    sig = hmac.new(state.hmac_secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def mint_session(user_id: int, role: str, max_age: int | None = None) -> tuple[str, str, int]:
    """Mint a session token and expose its nonce and expiry.

    Used by session tracking, which needs the nonce to record the
    session in the revocation ledger.

    Args:
        user_id: Database ID of the authenticated user.
        role: The user's current role.
        max_age: Token lifetime in seconds (defaults to ``session_max_age``).

    Returns:
        tuple[str, str, int]: ``(token, nonce, expiry_epoch)``.
    """
    nonce = secrets.token_urlsafe(24)
    ttl = max_age if max_age is not None else _get_auth_settings().session_max_age
    expiry = int(time.time()) + ttl
    return _sign_token(nonce, expiry, user_id, role), nonce, expiry


def create_session_token(user_id: int, role: str, max_age: int | None = None) -> str:
    """Create an HMAC-signed session token.

    Format: ``<nonce>.<expiry>.<user_id>.<role>.<signature>``

    Args:
        user_id: Database ID of the authenticated user.
        role: The user's current role.
        max_age: Token lifetime in seconds. Defaults to the configured
            ``session_max_age``; pass a shorter value for a scoped
            session (e.g. a device-link handoff).

    Returns:
        str: Signed session token string.
    """
    return mint_session(user_id, role, max_age)[0]


def session_nonce(token: str) -> str | None:
    """Return the nonce of a session token without verifying it.

    Args:
        token: A session cookie value.

    Returns:
        str | None: The leading nonce, or ``None`` if the token is malformed.
    """
    parts = token.split(".")
    return parts[0] if len(parts) == 5 and parts[0] else None


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
    expected = hmac.new(state.hmac_secret, payload.encode(), hashlib.sha256).hexdigest()
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


async def _lookup_sp(key: str) -> str | None:
    """Look up a service principal by Bearer token. Returns role or None.

    .. deprecated:: Use :func:`_lookup_sp_identity` for new code.

    Args:
        key: Plaintext API key from the Bearer header.

    Returns:
        str | None: Role string or ``None`` if not found.
    """
    result = await _lookup_sp_identity(key)
    return result["role"] if result else None


async def authenticate_user(email: str, password: str) -> dict | None:
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
    if state.session_factory is None:
        return None
    from sqlalchemy import select

    from shoreguard.models import User

    email = email.strip().lower()
    async with state.session_factory() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalars().first()

        # Always run bcrypt to prevent timing-based user enumeration.
        # The dummy hash is a valid bcrypt hash that will never match.
        _DUMMY_HASH = "$2b$12$LJ3m4ys3Lg2VBe50VdnCJOIBbGMkGLWMFwxL8MKGqUVAyGYQz/mPa"
        valid_user = (
            user is not None
            and user.is_active
            and user.invite_token_hash is None
            and user.hashed_password is not None
        )
        hashed = user.hashed_password if user is not None and valid_user else None
        password_ok = verify_password(password, hashed or _DUMMY_HASH)

        if user is None or not valid_user or not password_ok:
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


async def _lookup_user(user_id: int) -> dict | None:
    """Return ``{id, email, role}`` if the user exists and is active, else None.

    Args:
        user_id: Database ID of the user.

    Returns:
        dict | None: User info dict or ``None``.
    """
    if state.session_factory is None:
        return None
    from sqlalchemy import select

    from shoreguard.models import User

    async with state.session_factory() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalars().first()
        if user is None or not user.is_active:
            return None
        return {"id": user.id, "email": user.email, "role": user.role}


async def _lookup_user_by_email(email: str) -> dict | None:
    """Return ``{id, email, role}`` for an active user by email, else None.

    Args:
        email: User email address (matched case-insensitively).

    Returns:
        dict | None: User info dict or ``None``.
    """
    if state.session_factory is None:
        return None
    from sqlalchemy import select

    from shoreguard.models import User

    async with state.session_factory() as session:
        user = (
            (await session.execute(select(User).where(User.email == email.strip().lower())))
            .scalars()
            .first()
        )
        if user is None or not user.is_active:
            return None
        return {"id": user.id, "email": user.email, "role": user.role}


async def _lookup_sp_identity(key: str) -> dict | None:
    """Look up a service principal by Bearer token. Returns ``{name, role}`` or None.

    Args:
        key: Plaintext API key from the Bearer header.

    Returns:
        dict | None: ``{id, name, role}`` or ``None`` if not found.
    """
    if state.session_factory is None:
        return None
    from sqlalchemy import select

    from shoreguard.models import ServicePrincipal

    key_hash = _hash_key(key)
    async with state.session_factory() as session:
        try:
            row = (
                (
                    await session.execute(
                        select(ServicePrincipal).where(ServicePrincipal.key_hash == key_hash)
                    )
                )
                .scalars()
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
            await session.commit()
            return {"id": row.id, "name": row.name, "role": row.role}
        except SQLAlchemyError:
            await session.rollback()
            logger.exception("SP key lookup failed")
            return None


# ─── Credential resolution ──────────────────────────────────────────────────


async def check_request_auth(request: Request) -> str | None:
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
    if state.no_auth:
        request.state.user_id = "no-auth"
        return "admin"
    if state.session_factory is None:
        logger.error("Auth check with no DB session factory — denying request")
        raise HTTPException(status_code=503, detail="Service not ready")
    if not await is_setup_complete():
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
        sp = await _lookup_sp_identity(token)
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
            user_info = await _lookup_user(user_id)
            if user_info:
                # Session tracking: reject a cookie whose session has been
                # revoked (logged out from another device). Unrecorded
                # sessions pass — the ledger is a revocation list, not an
                # allowlist, so pre-tracking cookies keep working.
                nonce = None
                if _get_auth_settings().session_tracking:
                    from shoreguard.api.auth import user_sessions

                    nonce = session_nonce(cookie)
                    if nonce and await user_sessions.is_revoked(nonce):
                        logger.info("Rejected revoked session for user=%s", user_info["email"])
                        return None
                    if nonce:
                        await user_sessions.touch(nonce)
                request.state.user_id = user_info["email"]
                request.state.user_db_id = user_info["id"]
                request.state.session_nonce = nonce
                logger.debug(
                    "Auth via session cookie (path=%s, role=%s, user=%s)",
                    request.url.path,
                    user_info["role"],
                    user_info["email"],
                )
                return user_info["role"]
            logger.warning("Session for inactive/deleted user_id=%d", user_id)

    # 3. Tailscale Serve identity header → user (opt-in). Only honoured for
    # loopback connections: `tailscale serve` proxies via the local
    # tailscaled, so a legitimate header can only arrive from 127.0.0.1 —
    # from anywhere else it could be forged by a network peer.
    if _get_auth_settings().tailscale_identity:
        login = request.headers.get("tailscale-user-login")
        client_host = request.client.host if request.client else ""
        if login and client_host in ("127.0.0.1", "::1"):
            user_info = await _lookup_user_by_email(login)
            if user_info:
                request.state.user_id = user_info["email"]
                request.state.user_db_id = user_info["id"]
                logger.debug(
                    "Auth via Tailscale identity (path=%s, role=%s, user=%s)",
                    request.url.path,
                    user_info["role"],
                    user_info["email"],
                )
                return user_info["role"]
            logger.debug("Tailscale identity %r has no matching active user", login)

    return None
