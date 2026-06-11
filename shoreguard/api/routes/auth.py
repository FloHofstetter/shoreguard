"""Authentication API: login, logout, OIDC, setup, invites, registration."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    pass

from shoreguard.api.auth import (
    COOKIE_NAME,
    accept_invite,
    authenticate_user,
    check_request_auth,
    clear_lockout,
    create_user,
    find_or_create_oidc_user,
    is_account_locked,
    is_registration_enabled,
    is_setup_complete,
    record_failed_login,
    require_auth,
    user_sessions,
    verify_session_token,
)
from shoreguard.api.deps import get_services
from shoreguard.api.password import check_password
from shoreguard.api.ratelimit import get_login_limiter
from shoreguard.api.schemas import (
    AuthCheckResponse,
    OidcProviderInfo,
)
from shoreguard.api.validation import client_ip, valid_email
from shoreguard.services.audit import audit_log

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


def _check_rate_limit(request: Request) -> None:
    """Raise 429 if the client IP is rate-limited.

    Args:
        request: Incoming HTTP request.

    Raises:
        HTTPException: 429 with ``Retry-After`` header when rate-limited.
    """
    limiter = get_login_limiter()
    ip = client_ip(request)
    blocked, retry_after = limiter.is_limited(ip)
    if blocked:
        raise HTTPException(
            429,
            "Too many requests. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    limiter.record(ip)


router = APIRouter()


# ─── Auth endpoints ──────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    """Request body for the login endpoint.

    Attributes:
        email: User email address.
        password: User password.
    """

    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)


@router.post("/api/auth/login")
async def login(request: Request, body: LoginRequest) -> JSONResponse:
    """Validate credentials and set a session cookie.

    Args:
        request: Incoming HTTP request.
        body: Login credentials.

    Returns:
        JSONResponse: Session cookie on success, or error details.

    Raises:
        HTTPException: If setup is not complete, the account is locked, or
            the credentials are invalid.
    """
    _check_rate_limit(request)
    if not await is_setup_complete():
        raise HTTPException(400, "Setup not complete — create an admin user first")
    locked, lockout_retry = is_account_locked(body.email)
    if locked:
        raise HTTPException(
            429,
            "Too many requests. Try again later.",
            headers={"Retry-After": str(lockout_retry)},
        )
    user = await authenticate_user(body.email, body.password)
    if not user:
        record_failed_login(body.email)
        logger.warning("Login failed: invalid credentials (client=%s)", client_ip(request))
        request.state.user_id = body.email
        request.state.role = "unknown"
        await audit_log(request, "user.login_failed", "user", body.email)
        raise HTTPException(401, "Invalid email or password")
    clear_lockout(body.email)
    ip = client_ip(request)
    logger.info("Login successful (client=%s, email=%s, role=%s)", ip, user["email"], user["role"])
    request.state.user_id = user["email"]
    request.state.role = user["role"]
    await audit_log(request, "user.login", "user", user["email"])
    token = await user_sessions.create_tracked_session(
        request, user["id"], user["role"], kind="password"
    )
    response = JSONResponse(content={"ok": True, "role": user["role"], "email": user["email"]})
    secure = request.url.scheme == "https"
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=86400 * 7,
        path="/",
    )
    return response


@router.post("/api/auth/logout")
async def logout(request: Request) -> JSONResponse:
    """Clear the session cookie.

    Args:
        request: Incoming HTTP request.

    Returns:
        JSONResponse: Confirmation response with cookie deleted.
    """
    cookie = request.cookies.get(COOKIE_NAME)
    user_info = "unknown"
    if cookie:
        result = verify_session_token(cookie)
        if result:
            user_id = result[0]
            # Resolve email for consistent audit logging
            from shoreguard.api.auth.core import _lookup_user, session_nonce

            u = await _lookup_user(user_id)
            user_info = u["email"] if u else f"user_id={user_id}"
            # Revoke the ledger row so this session disappears from the
            # device list and can never be reused.
            nonce = session_nonce(cookie)
            if nonce:
                await user_sessions.revoke_by_nonce(nonce)
    logger.info("Logout (actor=%s, client=%s)", user_info, client_ip(request))
    await audit_log(request, "user.logout", "user", user_info)
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/api/auth/check", response_model=AuthCheckResponse)
async def auth_check(request: Request) -> dict[str, Any]:
    """Return auth status, role, and whether setup is needed.

    Args:
        request: Incoming HTTP request.

    Returns:
        dict[str, Any]: Authentication state including role and setup status.
    """
    needs_setup = not await is_setup_complete()
    if needs_setup:
        return {
            "authenticated": False,
            "auth_enabled": False,
            "role": None,
            "needs_setup": True,
            "registration_enabled": False,
        }

    role = await check_request_auth(request)
    email = None
    # Extract email from session cookie if present
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie and role:
        result = verify_session_token(cookie)
        if result:
            from sqlalchemy import select

            from shoreguard.api.auth import state
            from shoreguard.models import User

            if state.session_factory:
                async with state.session_factory() as session:
                    user = (
                        (await session.execute(select(User).where(User.id == result[0])))
                        .scalars()
                        .first()
                    )
                    if user:
                        email = user.email
    from shoreguard.api.oidc import get_providers
    from shoreguard.settings import get_settings

    return {
        "authenticated": role is not None,
        "auth_enabled": True,
        "role": role,
        "email": email,
        "needs_setup": False,
        "registration_enabled": is_registration_enabled(),
        "local_mode": get_services().local_gateway is not None,
        "oidc_providers": [
            {"name": p.name, "display_name": p.display_name} for p in get_providers()
        ],
        "device_link_enabled": get_settings().auth.device_link_enabled,
        "session_tracking": get_settings().auth.session_tracking,
    }


# ─── Active sessions (self-service revocation) ──────────────────────────────


def _session_user_id(request: Request) -> int:
    """Return the database id of the session-authenticated user.

    Args:
        request: The incoming HTTP request.

    Returns:
        int: The user's database id.

    Raises:
        HTTPException: 400 when the caller has no real user session
            (service principals, ``--no-auth`` dev bypass).
    """
    user_db_id = getattr(request.state, "user_db_id", None)
    if not isinstance(user_db_id, int):
        raise HTTPException(400, "Session management needs a real user session")
    return user_db_id


@router.get("/api/auth/sessions", dependencies=[Depends(require_auth)])
async def list_sessions(request: Request) -> dict[str, Any]:
    """List the current user's active sessions (devices).

    Args:
        request: The incoming HTTP request.

    Returns:
        dict[str, Any]: ``{"sessions": [...]}`` newest first, with the
        requesting session flagged ``current``.
    """
    user_id = _session_user_id(request)
    current = getattr(request.state, "session_nonce", None)
    return {"sessions": await user_sessions.list_for_user(user_id, current)}


@router.delete("/api/auth/sessions/{session_pk}", dependencies=[Depends(require_auth)])
async def revoke_session(session_pk: int, request: Request) -> dict[str, str]:
    """Revoke one of the current user's sessions by id.

    Args:
        session_pk: Primary key of the session to revoke.
        request: The incoming HTTP request.

    Returns:
        dict[str, str]: ``{"status": "revoked"}``.

    Raises:
        HTTPException: 404 when no matching active session exists.
    """
    user_id = _session_user_id(request)
    if not await user_sessions.revoke(user_id, session_pk):
        raise HTTPException(404, "No such active session")
    await audit_log(
        request,
        "user.session.revoke",
        "user",
        str(getattr(request.state, "user_id", "")),
        detail={"session_id": session_pk},
    )
    return {"status": "revoked"}


@router.post("/api/auth/sessions/revoke-others", dependencies=[Depends(require_auth)])
async def revoke_other_sessions(request: Request) -> dict[str, int]:
    """Revoke all of the current user's sessions except this one.

    Args:
        request: The incoming HTTP request.

    Returns:
        dict[str, int]: ``{"revoked": <count>}``.
    """
    user_id = _session_user_id(request)
    current = getattr(request.state, "session_nonce", None)
    revoked = await user_sessions.revoke_others(user_id, current)
    await audit_log(
        request,
        "user.session.revoke_others",
        "user",
        str(getattr(request.state, "user_id", "")),
        detail={"revoked": revoked},
    )
    return {"revoked": revoked}


# ─── OIDC / OpenID Connect ──────────────────────────────────────────────────


@router.get("/api/auth/oidc/providers", response_model=list[OidcProviderInfo])
async def oidc_providers_list() -> list[dict[str, str]]:
    """Return configured OIDC providers (public info only).

    Returns:
        list[dict[str, str]]: Provider name and display_name for each configured provider.
    """
    from shoreguard.api.oidc import get_providers

    return [{"name": p.name, "display_name": p.display_name} for p in get_providers()]


@router.get("/api/auth/oidc/login/{provider_name}")
async def oidc_login(request: Request, provider_name: str) -> RedirectResponse:
    """Initiate an OIDC authorization flow.

    Generates PKCE verifier, state, nonce, and sets a signed state cookie
    before redirecting to the provider's authorization endpoint.

    Args:
        request: Incoming HTTP request.
        provider_name: Name of the configured OIDC provider.

    Returns:
        RedirectResponse: Redirect to the provider's authorization endpoint.

    Raises:
        HTTPException: If the provider name is unknown.
    """
    import secrets as _secrets

    from shoreguard.api.oidc import (
        OIDC_STATE_COOKIE,
        build_authorize_url,
        build_state_cookie,
        generate_pkce,
        get_provider,
    )

    provider = get_provider(provider_name)
    if not provider:
        raise HTTPException(404, "Unknown OIDC provider")

    next_url = request.query_params.get("next", "/")
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"

    state = _secrets.token_urlsafe(32)
    nonce = _secrets.token_urlsafe(32)
    code_verifier, code_challenge = generate_pkce()

    callback_url = str(request.url_for("oidc_callback"))
    authorize_url = await build_authorize_url(provider, callback_url, state, nonce, code_challenge)

    cookie_value = build_state_cookie(provider_name, state, nonce, code_verifier, next_url)

    response = RedirectResponse(url=authorize_url, status_code=307)
    response.set_cookie(
        OIDC_STATE_COOKIE,
        cookie_value,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=300,
        path="/api/auth/oidc",
    )
    return response


@router.get("/api/auth/oidc/callback")
async def oidc_callback(request: Request) -> RedirectResponse:
    """Handle the OIDC provider callback.

    Verifies the state cookie, exchanges the authorization code for tokens,
    validates the ID token, and creates or links the user account.

    Args:
        request: Incoming HTTP request with ``code`` and ``state`` params.

    Returns:
        RedirectResponse: Redirect to the original ``next`` URL with
        a session cookie set.
    """
    from shoreguard.api.oidc import (
        OIDC_STATE_COOKIE,
        exchange_code,
        extract_email,
        get_provider,
        map_role,
        verify_id_token,
        verify_state_cookie,
    )

    # Provider error (user denied consent, etc.)
    error = request.query_params.get("error")
    if error:
        logger.warning("OIDC provider returned error: %s", error)
        return RedirectResponse(url="/login?error=oidc_denied", status_code=302)

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        return RedirectResponse(url="/login?error=oidc_failed", status_code=302)

    # Verify state cookie
    cookie_value = request.cookies.get(OIDC_STATE_COOKIE)
    if not cookie_value:
        logger.warning("OIDC callback: missing state cookie")
        return RedirectResponse(url="/login?error=oidc_failed", status_code=302)

    state_data = verify_state_cookie(cookie_value)
    if not state_data:
        logger.warning("OIDC callback: invalid or expired state cookie")
        return RedirectResponse(url="/login?error=oidc_failed", status_code=302)

    if state_data["s"] != state:
        logger.warning("OIDC callback: state mismatch")
        return RedirectResponse(url="/login?error=oidc_failed", status_code=302)

    provider_name = state_data["p"]
    nonce = state_data["n"]
    code_verifier = state_data["v"]
    next_url = state_data.get("x", "/")
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"

    provider = get_provider(provider_name)
    if not provider:
        return RedirectResponse(url="/login?error=oidc_failed", status_code=302)

    # Exchange code for tokens
    try:
        callback_url = str(request.url_for("oidc_callback"))
        token_response = await exchange_code(provider, code, callback_url, code_verifier)
    except Exception:
        logger.exception("OIDC token exchange failed (provider=%s)", provider_name)
        return RedirectResponse(url="/login?error=oidc_failed", status_code=302)

    id_token = token_response.get("id_token")
    if not id_token:
        logger.error("OIDC token response missing id_token (provider=%s)", provider_name)
        return RedirectResponse(url="/login?error=oidc_failed", status_code=302)

    # Verify ID token
    try:
        claims = await verify_id_token(provider, id_token, nonce)
    except Exception:
        logger.exception("OIDC ID token verification failed (provider=%s)", provider_name)
        return RedirectResponse(url="/login?error=oidc_failed", status_code=302)

    email = extract_email(claims)
    if not email:
        logger.error("OIDC claims missing email (provider=%s)", provider_name)
        return RedirectResponse(url="/login?error=oidc_failed", status_code=302)

    sub = claims.get("sub", "")
    role = map_role(provider, claims)

    # Find or create user
    try:
        result = await find_or_create_oidc_user(email, provider_name, sub, role)
    except Exception:
        logger.exception("OIDC user lookup/creation failed (email=%s)", email)
        return RedirectResponse(url="/login?error=oidc_failed", status_code=302)

    user = result["user"]
    action = result["action"]

    # Audit
    request.state.user_id = user["email"]
    request.state.role = user["role"]
    detail = {"provider": provider_name}
    await audit_log(request, "oidc.login", "user", user["email"], detail=detail)
    if action == "link":
        await audit_log(request, "oidc.link", "user", user["email"], detail=detail)
    elif action == "create":
        detail["role"] = role
        await audit_log(request, "oidc.create", "user", user["email"], detail=detail)

    # Create session
    token = await user_sessions.create_tracked_session(
        request, user["id"], user["role"], kind="oidc"
    )
    response = RedirectResponse(url=next_url, status_code=302)
    secure = request.url.scheme == "https"
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=86400 * 7,
        path="/",
    )
    # Clear state cookie
    response.delete_cookie(OIDC_STATE_COOKIE, path="/api/auth/oidc")
    logger.info(
        "OIDC login successful (email=%s, provider=%s, action=%s)",
        user["email"],
        provider_name,
        action,
    )
    return response


# ─── Setup wizard ───────────────────────────────────────────────────────────


class SetupRequest(BaseModel):
    """Request body for the initial admin setup.

    Attributes:
        email: Admin email address.
        password: Admin password.
    """

    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)


@router.post("/api/auth/setup")
async def setup(request: Request, body: SetupRequest) -> JSONResponse:
    """Create the first admin user. Only works when no users exist.

    Args:
        request: Incoming HTTP request.
        body: Admin credentials for initial setup.

    Returns:
        JSONResponse: Session cookie on success, or error details.

    Raises:
        HTTPException: If setup is already complete, inputs are invalid, or
            the user creation fails.
    """
    _check_rate_limit(request)
    if await is_setup_complete():
        raise HTTPException(400, "Setup already complete")
    if not body.email.strip() or not body.password:
        raise HTTPException(400, "Email and password are required")
    if not valid_email(body.email):
        raise HTTPException(400, "Invalid email format")
    pwd_err = check_password(body.password)
    if pwd_err:
        raise HTTPException(400, pwd_err)
    try:
        info = await create_user(body.email.strip(), body.password, "admin")
    except IntegrityError:
        logger.warning("Setup failed: duplicate admin email (email=%s)", body.email.strip())
        raise HTTPException(409, f"A user with email '{body.email.strip()}' already exists")
    except Exception:
        logger.exception("Setup failed")
        raise HTTPException(500, "Setup failed")

    logger.info(
        "Setup complete: admin user created (email=%s, client=%s)",
        info["email"],
        client_ip(request),
    )
    request.state.user_id = info["email"]
    request.state.role = "admin"
    await audit_log(request, "user.setup", "user", info["email"])
    token = await user_sessions.create_tracked_session(request, info["id"], "admin", kind="setup")
    response = JSONResponse(content={"ok": True, "role": "admin", "email": info["email"]})
    secure = request.url.scheme == "https"
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=86400 * 7,
        path="/",
    )
    return response


# ─── Invite acceptance (public) ─────────────────────────────────────────────


class AcceptInviteRequest(BaseModel):
    """Request body for accepting an invite.

    Attributes:
        token: Invite token from the invitation link.
        password: Chosen password for the new account.
    """

    token: str = Field(min_length=1, max_length=512)
    password: str = Field(min_length=1, max_length=128)


@router.post("/api/auth/accept-invite")
async def accept_invite_endpoint(request: Request, body: AcceptInviteRequest) -> JSONResponse:
    """Accept an invite and set password. Returns session cookie.

    Args:
        request: Incoming HTTP request.
        body: Invite token and chosen password.

    Returns:
        JSONResponse: Session cookie on success, or error details.

    Raises:
        HTTPException: If the password is missing/invalid or the invite
            token is invalid or expired.
    """
    if not body.password:
        raise HTTPException(400, "Password is required")
    pwd_err = check_password(body.password)
    if pwd_err:
        raise HTTPException(400, pwd_err)
    user = await accept_invite(body.token, body.password)
    if not user:
        raise HTTPException(400, "Invalid or expired invite token")

    logger.info(
        "Invite accepted (email=%s, role=%s, client=%s)",
        user["email"],
        user["role"],
        client_ip(request),
    )
    request.state.user_id = user["email"]
    request.state.role = user["role"]
    await audit_log(request, "user.invite.accept", "user", user["email"])
    token = await user_sessions.create_tracked_session(
        request, user["id"], user["role"], kind="invite"
    )
    response = JSONResponse(content={"ok": True, "role": user["role"], "email": user["email"]})
    secure = request.url.scheme == "https"
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=86400 * 7,
        path="/",
    )
    return response


# ─── Self-registration (opt-in) ─────────────────────────────────────────────


class RegisterRequest(BaseModel):
    """Request body for self-registration.

    Attributes:
        email: Email address for the new account.
        password: Chosen password.
    """

    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)


@router.post("/api/auth/register")
async def register_endpoint(request: Request, body: RegisterRequest) -> JSONResponse:
    """Self-register a new viewer account. Requires SHOREGUARD_ALLOW_REGISTRATION.

    Args:
        request: Incoming HTTP request.
        body: Registration email and password.

    Returns:
        JSONResponse: Session cookie on success, or error details.

    Raises:
        HTTPException: If registration is disabled, setup is incomplete,
            inputs are invalid, the email already exists, or creation fails.
    """
    _check_rate_limit(request)
    if not is_registration_enabled():
        raise HTTPException(403, "Registration is disabled")
    if not await is_setup_complete():
        raise HTTPException(400, "Setup not complete — use /setup first")
    if not body.email.strip() or not body.password:
        raise HTTPException(400, "Email and password are required")
    if not valid_email(body.email):
        raise HTTPException(400, "Invalid email format")
    pwd_err = check_password(body.password)
    if pwd_err:
        raise HTTPException(400, pwd_err)
    try:
        info = await create_user(body.email.strip(), body.password, "viewer")
    except IntegrityError:
        logger.warning(
            "Duplicate registration attempt (email=%s, client=%s)",
            body.email.strip(),
            client_ip(request),
        )
        raise HTTPException(409, f"An account with email '{body.email.strip()}' already exists")
    except Exception:
        logger.exception("Registration failed")
        raise HTTPException(500, "Registration failed")

    logger.info("Self-registration (email=%s, client=%s)", info["email"], client_ip(request))
    request.state.user_id = info["email"]
    request.state.role = "viewer"
    await audit_log(request, "user.register", "user", info["email"])
    token = await user_sessions.create_tracked_session(
        request, info["id"], "viewer", kind="register"
    )
    response = JSONResponse(
        content={"ok": True, "role": "viewer", "email": info["email"]}, status_code=201
    )
    secure = request.url.scheme == "https"
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=86400 * 7,
        path="/",
    )
    return response
