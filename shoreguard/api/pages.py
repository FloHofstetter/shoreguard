"""HTML page routes and auth API endpoints for the Shoreguard frontend."""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from starlette.responses import HTMLResponse
from starlette.templating import _TemplateResponse as TemplateResponse

if TYPE_CHECKING:
    from shoreguard.services._openshell_meta import OpenShellMeta

from shoreguard.config import VALID_GATEWAY_NAME_RE
from shoreguard.services.audit import audit_log

from .auth import (
    COOKIE_NAME,
    ROLES,
    accept_invite,
    add_group_member,
    authenticate_user,
    check_request_auth,
    clear_lockout,
    create_group,
    create_service_principal,
    create_session_token,
    create_user,
    delete_group,
    delete_service_principal,
    delete_user,
    find_or_create_oidc_user,
    get_group,
    is_account_locked,
    is_registration_enabled,
    is_setup_complete,
    list_gateway_roles_for_sp,
    list_gateway_roles_for_user,
    list_group_gateway_roles,
    list_groups,
    list_service_principals,
    list_users,
    record_failed_login,
    remove_gateway_role,
    remove_group_gateway_role,
    remove_group_member,
    require_role,
    rotate_service_principal,
    set_gateway_role,
    set_group_gateway_role,
    update_group,
    verify_session_token,
)
from .password import check_password
from .ratelimit import get_login_limiter
from .schemas import (
    AuthCheckResponse,
    GatewayRoleResponse,
    GroupDetailResponse,
    GroupMemberResponse,
    GroupResponse,
    OidcProviderInfo,
    OkResponse,
    ServicePrincipalCreateResponse,
    ServicePrincipalResponse,
    UserCreateResponse,
    UserResponse,
)

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


def _valid_email(email: str) -> bool:
    """Basic email format check.

    Args:
        email: Email address to validate.

    Returns:
        bool: True if the email matches a basic pattern.
    """
    return bool(_EMAIL_RE.match(email.strip()))


def _client_ip(request: Request) -> str:
    """Extract client IP from request.

    Args:
        request: Incoming HTTP request.

    Returns:
        str: Client IP address or ``"unknown"``.
    """
    return request.client.host if request.client else "unknown"


def _check_rate_limit(request: Request) -> None:
    """Raise 429 if the client IP is rate-limited.

    Args:
        request: Incoming HTTP request.

    Raises:
        HTTPException: 429 with ``Retry-After`` header when rate-limited.
    """
    limiter = get_login_limiter()
    ip = _client_ip(request)
    blocked, retry_after = limiter.is_limited(ip)
    if blocked:
        raise HTTPException(
            429,
            "Too many requests. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    limiter.record(ip)


def _get_actor(request: Request) -> str:
    """Extract acting user identity from request state.

    Args:
        request: Incoming HTTP request.

    Returns:
        str: User identifier or ``"unknown"``.
    """
    user_id = getattr(request.state, "user_id", None)
    return str(user_id) if user_id else "unknown"


def _resolve_frontend_dir() -> Path:
    """Resolve the frontend directory for both installed and dev-checkout modes.

    Returns:
        Path: Resolved path to the frontend assets directory.

    Raises:
        FileNotFoundError: If neither the installed nor dev-checkout frontend directory exists.
    """
    pkg_dir = Path(__file__).resolve().parent.parent / "_frontend"
    if pkg_dir.is_dir():
        return pkg_dir
    dev_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
    if dev_dir.is_dir():
        return dev_dir
    raise FileNotFoundError(
        "Frontend directory not found. Reinstall shoreguard or run from the repository root."
    )


FRONTEND_DIR = _resolve_frontend_dir()

templates = Jinja2Templates(directory=str(FRONTEND_DIR / "templates"))


def _csp_nonce_for(request: Request) -> str:
    """Return the per-request CSP nonce set by ``security_headers_middleware``.

    Exposed as a Jinja global so templates can render ``nonce="{{ csp_nonce(request) }}"``
    on inline ``<script>``/``<style>`` tags without every TemplateResponse call
    site having to pass it explicitly.

    Args:
        request: The incoming HTTP request whose state carries the nonce.

    Returns:
        str: The nonce string, or ``""`` if none has been set (e.g. during
        isolated template rendering in tests).
    """
    return getattr(request.state, "csp_nonce", "")


def _csp_strict_enabled() -> bool:
    """Return whether strict CSP mode is currently enabled.

    Exposed as a Jinja global so templates can switch between the standard
    Alpine.js build and the CSP-safe build based on runtime configuration.

    Returns:
        bool: ``True`` when ``auth.csp_strict`` is enabled.
    """
    from shoreguard.settings import get_settings

    return get_settings().auth.csp_strict


templates.env.globals["csp_nonce"] = _csp_nonce_for
templates.env.globals["csp_strict_enabled"] = _csp_strict_enabled

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
    if not is_setup_complete():
        raise HTTPException(400, "Setup not complete — create an admin user first")
    locked, lockout_retry = is_account_locked(body.email)
    if locked:
        raise HTTPException(
            429,
            "Too many requests. Try again later.",
            headers={"Retry-After": str(lockout_retry)},
        )
    user = authenticate_user(body.email, body.password)
    if not user:
        record_failed_login(body.email)
        logger.warning("Login failed: invalid credentials (client=%s)", _client_ip(request))
        request.state.user_id = body.email
        request.state.role = "unknown"
        await audit_log(request, "user.login_failed", "user", body.email)
        raise HTTPException(401, "Invalid email or password")
    clear_lockout(body.email)
    client_ip = _client_ip(request)
    logger.info(
        "Login successful (client=%s, email=%s, role=%s)", client_ip, user["email"], user["role"]
    )
    request.state.user_id = user["email"]
    request.state.role = user["role"]
    await audit_log(request, "user.login", "user", user["email"])
    token = create_session_token(user_id=user["id"], role=user["role"])
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
            from shoreguard.api.auth import _lookup_user

            u = _lookup_user(user_id)
            user_info = u["email"] if u else f"user_id={user_id}"
    logger.info("Logout (actor=%s, client=%s)", user_info, _client_ip(request))
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
    needs_setup = not is_setup_complete()
    if needs_setup:
        return {
            "authenticated": False,
            "auth_enabled": False,
            "role": None,
            "needs_setup": True,
            "registration_enabled": False,
        }

    role = check_request_auth(request)
    email = None
    # Extract email from session cookie if present
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie and role:
        result = verify_session_token(cookie)
        if result:
            from shoreguard.api.auth import _session_factory
            from shoreguard.models import User

            if _session_factory:
                session = _session_factory()
                try:
                    user = session.query(User).filter(User.id == result[0]).first()
                    if user:
                        email = user.email
                finally:
                    session.close()
    import shoreguard.services.local_gateway as local_mod
    from shoreguard.api.oidc import get_providers

    return {
        "authenticated": role is not None,
        "auth_enabled": True,
        "role": role,
        "email": email,
        "needs_setup": False,
        "registration_enabled": is_registration_enabled(),
        "local_mode": local_mod.local_gateway_manager is not None,
        "oidc_providers": [
            {"name": p.name, "display_name": p.display_name} for p in get_providers()
        ],
    }


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
        result = find_or_create_oidc_user(email, provider_name, sub, role)
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
    token = create_session_token(user_id=user["id"], role=user["role"])
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
    if is_setup_complete():
        raise HTTPException(400, "Setup already complete")
    if not body.email.strip() or not body.password:
        raise HTTPException(400, "Email and password are required")
    if not _valid_email(body.email):
        raise HTTPException(400, "Invalid email format")
    pwd_err = check_password(body.password)
    if pwd_err:
        raise HTTPException(400, pwd_err)
    try:
        info = create_user(body.email.strip(), body.password, "admin")
    except IntegrityError:
        logger.warning("Setup failed: duplicate admin email (email=%s)", body.email.strip())
        raise HTTPException(409, f"A user with email '{body.email.strip()}' already exists")
    except Exception:
        logger.exception("Setup failed")
        raise HTTPException(500, "Setup failed")

    logger.info(
        "Setup complete: admin user created (email=%s, client=%s)",
        info["email"],
        _client_ip(request),
    )
    request.state.user_id = info["email"]
    request.state.role = "admin"
    await audit_log(request, "user.setup", "user", info["email"])
    token = create_session_token(user_id=info["id"], role="admin")
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


# ─── User management (admin-only) ───────────────────────────────────────────


class CreateUserRequest(BaseModel):
    """Request body for inviting a user.

    Attributes:
        email: Email address of the user to invite.
        role: Role to assign (default ``"viewer"``).
    """

    email: str
    role: str = "viewer"


@router.get(
    "/api/auth/users",
    dependencies=[Depends(require_role("admin"))],
    response_model=list[UserResponse],
)
async def get_users(request: Request) -> list[dict[str, Any]]:
    """List all users (admin only).

    Args:
        request: Incoming HTTP request.

    Returns:
        list[dict[str, Any]]: All registered users.
    """
    return list_users()


@router.post(
    "/api/auth/users",
    status_code=201,
    dependencies=[Depends(require_role("admin"))],
    response_model=UserCreateResponse,
)
async def create_user_endpoint(
    request: Request, body: CreateUserRequest
) -> dict[str, Any] | JSONResponse:
    """Invite a new user (admin only). Returns an invite token.

    Args:
        request: Incoming HTTP request.
        body: User email and role.

    Returns:
        dict[str, Any] | JSONResponse: Created user info including invite token.

    Raises:
        HTTPException: If the role or email is invalid, the email already
            exists, or user creation fails.
    """
    if body.role not in ROLES:
        raise HTTPException(400, f"Invalid role: {body.role!r} (must be one of {ROLES})")
    if not body.email.strip():
        raise HTTPException(400, "Email is required")
    if not _valid_email(body.email):
        raise HTTPException(400, "Invalid email format")
    try:
        info = create_user(body.email.strip(), None, body.role)
    except IntegrityError:
        logger.warning(
            "Duplicate user creation attempt (email=%s, actor=%s)",
            body.email.strip(),
            _get_actor(request),
        )
        raise HTTPException(409, f"A user with email '{body.email.strip()}' already exists")
    except Exception:
        logger.exception("Failed to create user")
        raise HTTPException(500, "Failed to create user")
    logger.info(
        "User invited (email=%s, role=%s, actor=%s)", info["email"], body.role, _get_actor(request)
    )
    await audit_log(request, "user.invite", "user", info["email"], detail={"role": body.role})
    return info


@router.delete(
    "/api/auth/users/{user_id}",
    dependencies=[Depends(require_role("admin"))],
    response_model=OkResponse,
)
async def delete_user_endpoint(request: Request, user_id: int) -> dict[str, Any] | JSONResponse:
    """Delete a user (admin only).

    Args:
        request: Incoming HTTP request.
        user_id: Database ID of the user to delete.

    Returns:
        dict[str, Any] | JSONResponse: Confirmation or error response.

    Raises:
        HTTPException: If attempting self-deletion, deleting the last admin,
            or the user does not exist.
    """
    # Prevent self-deletion
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        result = verify_session_token(cookie)
        if result and result[0] == user_id:
            raise HTTPException(400, "Cannot delete your own account")
    # Prevent deleting the last admin
    users = list_users()
    active_admins = [u for u in users if u.get("role") == "admin" and u.get("is_active")]
    target_is_admin = any(u["id"] == user_id and u.get("role") == "admin" for u in users)
    if target_is_admin and len(active_admins) <= 1:
        raise HTTPException(400, "Cannot delete the last admin user")
    if delete_user(user_id):
        logger.info("User deleted (user_id=%s, actor=%s)", user_id, _get_actor(request))
        await audit_log(request, "user.delete", "user", str(user_id))
        return {"ok": True}
    raise HTTPException(404, "User not found")


# ─── Gateway-scoped role management (admin-only) ──────────────────────────


class SetGatewayRoleRequest(BaseModel):
    """Request body for setting a per-gateway role override.

    Attributes:
        role: Role to assign for the gateway scope.
    """

    role: str


@router.get(
    "/api/auth/users/{user_id}/gateway-roles",
    dependencies=[Depends(require_role("admin"))],
    response_model=list[GatewayRoleResponse],
)
async def get_user_gateway_roles(user_id: int) -> list[dict[str, Any]]:
    """List all gateway-scoped role overrides for a user.

    Args:
        user_id: Database ID of the user.

    Returns:
        list[dict[str, Any]]: Gateway role overrides for the user.
    """
    return await asyncio.to_thread(list_gateway_roles_for_user, user_id)


@router.put(
    "/api/auth/users/{user_id}/gateway-roles/{gw}",
    dependencies=[Depends(require_role("admin"))],
    response_model=GatewayRoleResponse,
)
async def set_user_gateway_role(
    request: Request, user_id: int, gw: str, body: SetGatewayRoleRequest
) -> dict[str, Any] | JSONResponse:
    """Set or update a per-gateway role for a user.

    Args:
        request: Incoming HTTP request.
        user_id: Database ID of the user.
        gw: Gateway name.
        body: Role to assign.

    Returns:
        dict[str, Any] | JSONResponse: Updated gateway role mapping.

    Raises:
        HTTPException: If the gateway name or role is invalid, or a gateway
            role conflict occurs.
    """
    if not VALID_GATEWAY_NAME_RE.match(gw):
        logger.warning(
            "Invalid gateway name rejected (gateway=%s, actor=%s)", gw, _get_actor(request)
        )
        raise HTTPException(400, "Invalid gateway name")
    if body.role not in ROLES:
        logger.warning("Invalid role rejected (role=%s, actor=%s)", body.role, _get_actor(request))
        raise HTTPException(400, f"Invalid role: {body.role!r} (must be one of {ROLES})")
    try:
        result = await asyncio.to_thread(
            set_gateway_role, user_id=user_id, gateway_name=gw, role=body.role
        )
    except IntegrityError:
        logger.warning(
            "Gateway role conflict (user_id=%s, gateway=%s, role=%s, actor=%s)",
            user_id,
            gw,
            body.role,
            _get_actor(request),
        )
        raise HTTPException(409, "Gateway role conflict")
    logger.info(
        "User gateway role set (user_id=%s, gateway=%s, role=%s, actor=%s)",
        user_id,
        gw,
        body.role,
        _get_actor(request),
    )
    await audit_log(
        request,
        "user.gateway_role.set",
        "user",
        str(user_id),
        detail={"gateway": gw, "role": body.role},
    )
    return result


@router.delete(
    "/api/auth/users/{user_id}/gateway-roles/{gw}",
    dependencies=[Depends(require_role("admin"))],
    response_model=OkResponse,
)
async def delete_user_gateway_role(
    request: Request, user_id: int, gw: str
) -> dict[str, Any] | JSONResponse:
    """Remove a per-gateway role override for a user (falls back to global role).

    Args:
        request: Incoming HTTP request.
        user_id: Database ID of the user.
        gw: Gateway name.

    Returns:
        dict[str, Any] | JSONResponse: Confirmation or error response.

    Raises:
        HTTPException: If the gateway name is invalid or the override does
            not exist.
    """
    if not VALID_GATEWAY_NAME_RE.match(gw):
        logger.warning(
            "Invalid gateway name rejected (gateway=%s, actor=%s)", gw, _get_actor(request)
        )
        raise HTTPException(400, "Invalid gateway name")
    if await asyncio.to_thread(remove_gateway_role, user_id=user_id, gateway_name=gw):
        logger.info(
            "User gateway role removed (user_id=%s, gateway=%s, actor=%s)",
            user_id,
            gw,
            _get_actor(request),
        )
        await audit_log(
            request,
            "user.gateway_role.remove",
            "user",
            str(user_id),
            detail={"gateway": gw},
        )
        return {"ok": True}
    logger.warning(
        "Gateway role not found for deletion (user_id=%s, gateway=%s, actor=%s)",
        user_id,
        gw,
        _get_actor(request),
    )
    raise HTTPException(404, "Gateway role not found")


@router.get(
    "/api/auth/service-principals/{sp_id}/gateway-roles",
    dependencies=[Depends(require_role("admin"))],
    response_model=list[GatewayRoleResponse],
)
async def get_sp_gateway_roles(sp_id: int) -> list[dict[str, Any]]:
    """List all gateway-scoped role overrides for a service principal.

    Args:
        sp_id: Database ID of the service principal.

    Returns:
        list[dict[str, Any]]: Gateway role overrides for the service principal.
    """
    return await asyncio.to_thread(list_gateway_roles_for_sp, sp_id)


@router.put(
    "/api/auth/service-principals/{sp_id}/gateway-roles/{gw}",
    dependencies=[Depends(require_role("admin"))],
    response_model=GatewayRoleResponse,
)
async def set_sp_gateway_role_endpoint(
    request: Request, sp_id: int, gw: str, body: SetGatewayRoleRequest
) -> dict[str, Any] | JSONResponse:
    """Set or update a per-gateway role for a service principal.

    Args:
        request: Incoming HTTP request.
        sp_id: Database ID of the service principal.
        gw: Gateway name.
        body: Role to assign.

    Returns:
        dict[str, Any] | JSONResponse: Updated gateway role mapping.

    Raises:
        HTTPException: If the gateway name or role is invalid, or a gateway
            role conflict occurs.
    """
    if not VALID_GATEWAY_NAME_RE.match(gw):
        logger.warning(
            "Invalid gateway name rejected (gateway=%s, actor=%s)", gw, _get_actor(request)
        )
        raise HTTPException(400, "Invalid gateway name")
    if body.role not in ROLES:
        logger.warning("Invalid role rejected (role=%s, actor=%s)", body.role, _get_actor(request))
        raise HTTPException(400, f"Invalid role: {body.role!r} (must be one of {ROLES})")
    try:
        result = await asyncio.to_thread(
            set_gateway_role, sp_id=sp_id, gateway_name=gw, role=body.role
        )
    except IntegrityError:
        logger.warning(
            "Gateway role conflict (sp_id=%s, gateway=%s, role=%s, actor=%s)",
            sp_id,
            gw,
            body.role,
            _get_actor(request),
        )
        raise HTTPException(409, "Gateway role conflict")
    logger.info(
        "SP gateway role set (sp_id=%s, gateway=%s, role=%s, actor=%s)",
        sp_id,
        gw,
        body.role,
        _get_actor(request),
    )
    await audit_log(
        request,
        "sp.gateway_role.set",
        "service_principal",
        str(sp_id),
        detail={"gateway": gw, "role": body.role},
    )
    return result


@router.delete(
    "/api/auth/service-principals/{sp_id}/gateway-roles/{gw}",
    dependencies=[Depends(require_role("admin"))],
    response_model=OkResponse,
)
async def delete_sp_gateway_role(
    request: Request, sp_id: int, gw: str
) -> dict[str, Any] | JSONResponse:
    """Remove a per-gateway role override for a service principal.

    Args:
        request: Incoming HTTP request.
        sp_id: Database ID of the service principal.
        gw: Gateway name.

    Returns:
        dict[str, Any] | JSONResponse: Confirmation or error response.

    Raises:
        HTTPException: If the gateway name is invalid or the override does
            not exist.
    """
    if not VALID_GATEWAY_NAME_RE.match(gw):
        logger.warning(
            "Invalid gateway name rejected (gateway=%s, actor=%s)", gw, _get_actor(request)
        )
        raise HTTPException(400, "Invalid gateway name")
    if await asyncio.to_thread(remove_gateway_role, sp_id=sp_id, gateway_name=gw):
        logger.info(
            "SP gateway role removed (sp_id=%s, gateway=%s, actor=%s)",
            sp_id,
            gw,
            _get_actor(request),
        )
        await audit_log(
            request,
            "sp.gateway_role.remove",
            "service_principal",
            str(sp_id),
            detail={"gateway": gw},
        )
        return {"ok": True}
    logger.warning(
        "Gateway role not found for deletion (sp_id=%s, gateway=%s, actor=%s)",
        sp_id,
        gw,
        _get_actor(request),
    )
    raise HTTPException(404, "Gateway role not found")


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
    user = accept_invite(body.token, body.password)
    if not user:
        raise HTTPException(400, "Invalid or expired invite token")

    logger.info(
        "Invite accepted (email=%s, role=%s, client=%s)",
        user["email"],
        user["role"],
        _client_ip(request),
    )
    request.state.user_id = user["email"]
    request.state.role = user["role"]
    await audit_log(request, "user.invite.accept", "user", user["email"])
    token = create_session_token(user_id=user["id"], role=user["role"])
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
    if not is_setup_complete():
        raise HTTPException(400, "Setup not complete — use /setup first")
    if not body.email.strip() or not body.password:
        raise HTTPException(400, "Email and password are required")
    if not _valid_email(body.email):
        raise HTTPException(400, "Invalid email format")
    pwd_err = check_password(body.password)
    if pwd_err:
        raise HTTPException(400, pwd_err)
    try:
        info = create_user(body.email.strip(), body.password, "viewer")
    except IntegrityError:
        logger.warning(
            "Duplicate registration attempt (email=%s, client=%s)",
            body.email.strip(),
            _client_ip(request),
        )
        raise HTTPException(409, f"An account with email '{body.email.strip()}' already exists")
    except Exception:
        logger.exception("Registration failed")
        raise HTTPException(500, "Registration failed")

    logger.info("Self-registration (email=%s, client=%s)", info["email"], _client_ip(request))
    request.state.user_id = info["email"]
    request.state.role = "viewer"
    await audit_log(request, "user.register", "user", info["email"])
    token = create_session_token(user_id=info["id"], role="viewer")
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


# ─── Service principal management (admin-only) ─────────────────────────────


class CreateSPRequest(BaseModel):
    """Request body for creating a service principal.

    Attributes:
        name: Display name for the service principal.
        role: Role to assign (default ``"viewer"``).
        expires_at: Optional expiry timestamp (ISO-8601).
    """

    name: str
    role: str = "viewer"
    expires_at: datetime.datetime | None = None


@router.get(
    "/api/auth/service-principals",
    dependencies=[Depends(require_role("admin"))],
    response_model=list[ServicePrincipalResponse],
)
async def get_sps(request: Request) -> list[dict[str, Any]]:
    """List all service principals (admin only).

    Args:
        request: Incoming HTTP request.

    Returns:
        list[dict[str, Any]]: All registered service principals.
    """
    return list_service_principals()


@router.post(
    "/api/auth/service-principals",
    status_code=201,
    dependencies=[Depends(require_role("admin"))],
    response_model=ServicePrincipalCreateResponse,
)
async def create_sp_endpoint(
    request: Request, body: CreateSPRequest
) -> dict[str, Any] | JSONResponse:
    """Create a new service principal (admin only).

    Args:
        request: Incoming HTTP request.
        body: Service principal name and role.

    Returns:
        dict[str, Any] | JSONResponse: Created service principal info including API key.

    Raises:
        HTTPException: If the role or name is invalid, the name already
            exists, or creation fails.
    """
    if body.role not in ROLES:
        raise HTTPException(400, f"Invalid role: {body.role!r} (must be one of {ROLES})")
    if not body.name.strip():
        raise HTTPException(400, "Name is required")
    try:
        plaintext, info = create_service_principal(
            body.name.strip(), body.role, expires_at=body.expires_at
        )
    except IntegrityError:
        logger.warning(
            "Duplicate service principal creation attempt (name=%s, actor=%s)",
            body.name.strip(),
            _get_actor(request),
        )
        raise HTTPException(409, f"A service principal named '{body.name.strip()}' already exists")
    except Exception:
        logger.exception("Failed to create service principal")
        raise HTTPException(500, "Failed to create service principal")
    logger.info(
        "Service principal created (name=%s, role=%s, actor=%s)",
        body.name.strip(),
        body.role,
        _get_actor(request),
    )
    await audit_log(
        request,
        "sp.create",
        "service_principal",
        body.name.strip(),
        detail={"role": body.role},
    )
    return {"key": plaintext, **info}


@router.delete(
    "/api/auth/service-principals/{sp_id}",
    dependencies=[Depends(require_role("admin"))],
    response_model=OkResponse,
)
async def delete_sp_endpoint(request: Request, sp_id: int) -> dict[str, Any] | JSONResponse:
    """Delete a service principal (admin only).

    Args:
        request: Incoming HTTP request.
        sp_id: Database ID of the service principal to delete.

    Returns:
        dict[str, Any] | JSONResponse: Confirmation or error response.

    Raises:
        HTTPException: If the service principal does not exist.
    """
    if delete_service_principal(sp_id):
        logger.info("Service principal deleted (sp_id=%s, actor=%s)", sp_id, _get_actor(request))
        await audit_log(request, "sp.delete", "service_principal", str(sp_id))
        return {"ok": True}
    raise HTTPException(404, "Service principal not found")


@router.post(
    "/api/auth/service-principals/{sp_id}/rotate",
    dependencies=[Depends(require_role("admin"))],
    response_model=ServicePrincipalCreateResponse,
)
async def rotate_sp_endpoint(request: Request, sp_id: int) -> dict[str, Any] | JSONResponse:
    """Rotate the API key for a service principal (admin only).

    Generates a new key and immediately invalidates the old one.

    Args:
        request: Incoming HTTP request.
        sp_id: Database ID of the service principal.

    Returns:
        dict[str, Any] | JSONResponse: New key info or error response.

    Raises:
        HTTPException: If the service principal does not exist.
    """
    result = rotate_service_principal(sp_id)
    if result is None:
        raise HTTPException(404, "Service principal not found")
    plaintext, info = result
    logger.info("Service principal key rotated (sp_id=%s, actor=%s)", sp_id, _get_actor(request))
    await audit_log(request, "sp.rotate", "service_principal", str(sp_id))
    return {"key": plaintext, **info}


# ─── Group management (admin-only) ──────────────────────────────────────────


class CreateGroupRequest(BaseModel):
    """Request body for creating a group.

    Attributes:
        name: Group name.
        role: Default role for members of the group.
        description: Optional free-form description.
    """

    name: str
    role: str = "viewer"
    description: str | None = None


class UpdateGroupRequest(BaseModel):
    """Request body for updating a group.

    Attributes:
        name: New group name, if changing.
        role: New default role, if changing.
        description: New description, if changing.
    """

    name: str | None = None
    role: str | None = None
    description: str | None = None


class AddGroupMemberRequest(BaseModel):
    """Request body for adding a member to a group.

    Attributes:
        user_id: Database ID of the user to add.
    """

    user_id: int


@router.get(
    "/api/auth/groups",
    dependencies=[Depends(require_role("admin"))],
    response_model=list[GroupResponse],
)
async def get_groups(request: Request) -> list[dict[str, Any]]:
    """List all groups with member counts.

    Args:
        request: The incoming HTTP request.

    Returns:
        list[dict[str, Any]]: Group info dicts.
    """
    return await asyncio.to_thread(list_groups)


@router.post(
    "/api/auth/groups",
    dependencies=[Depends(require_role("admin"))],
    status_code=201,
    response_model=GroupResponse,
)
async def create_group_endpoint(
    request: Request, body: CreateGroupRequest
) -> dict[str, Any] | JSONResponse:
    """Create a new group.

    Args:
        request: The incoming HTTP request.
        body: Group creation payload.

    Returns:
        dict[str, Any] | JSONResponse: Created group or error response.

    Raises:
        HTTPException: If the role is invalid or the group name conflicts.
    """
    if body.role not in ROLES:
        raise HTTPException(400, f"Invalid role: {body.role!r}")
    try:
        result = await asyncio.to_thread(create_group, body.name, body.role, body.description)
    except IntegrityError:
        raise HTTPException(409, "Group name already exists")
    await audit_log(request, "group.create", "group", body.name, detail={"role": body.role})
    return result


@router.get(
    "/api/auth/groups/{group_id}",
    dependencies=[Depends(require_role("admin"))],
    response_model=GroupDetailResponse,
)
async def get_group_endpoint(request: Request, group_id: int) -> dict[str, Any] | JSONResponse:
    """Get a group with its member list.

    Args:
        request: The incoming HTTP request.
        group_id: Database ID of the group.

    Returns:
        dict[str, Any] | JSONResponse: Group info or 404.

    Raises:
        HTTPException: If the group does not exist.
    """
    result = await asyncio.to_thread(get_group, group_id)
    if result is None:
        raise HTTPException(404, "Group not found")
    return result


@router.put(
    "/api/auth/groups/{group_id}",
    dependencies=[Depends(require_role("admin"))],
    response_model=GroupResponse,
)
async def update_group_endpoint(
    request: Request, group_id: int, body: UpdateGroupRequest
) -> dict[str, Any] | JSONResponse:
    """Update a group.

    Args:
        request: The incoming HTTP request.
        group_id: Database ID of the group.
        body: Update payload.

    Returns:
        dict[str, Any] | JSONResponse: Updated group or error.

    Raises:
        HTTPException: If the role is invalid or the new name conflicts.
    """
    if body.role is not None and body.role not in ROLES:
        raise HTTPException(400, f"Invalid role: {body.role!r}")
    try:
        result = await asyncio.to_thread(
            update_group, group_id, name=body.name, role=body.role, description=body.description
        )
    except IntegrityError:
        raise HTTPException(409, "Group name already exists")
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    await audit_log(request, "group.update", "group", result["name"], detail=changes)
    return result


@router.delete(
    "/api/auth/groups/{group_id}",
    dependencies=[Depends(require_role("admin"))],
    response_model=OkResponse,
)
async def delete_group_endpoint(request: Request, group_id: int) -> dict[str, Any] | JSONResponse:
    """Delete a group.

    Args:
        request: The incoming HTTP request.
        group_id: Database ID of the group.

    Returns:
        dict[str, Any] | JSONResponse: Success or 404.

    Raises:
        HTTPException: If the group does not exist.
    """
    # Fetch group name for audit before deleting
    info = await asyncio.to_thread(get_group, group_id)
    if info is None:
        raise HTTPException(404, "Group not found")
    await asyncio.to_thread(delete_group, group_id)
    await audit_log(request, "group.delete", "group", info["name"])
    return {"ok": True}


# ─── Group membership (admin-only) ──────────────────────────────────────────


@router.post(
    "/api/auth/groups/{group_id}/members",
    dependencies=[Depends(require_role("admin"))],
    status_code=201,
    response_model=GroupMemberResponse,
)
async def add_group_member_endpoint(
    request: Request, group_id: int, body: AddGroupMemberRequest
) -> dict[str, Any] | JSONResponse:
    """Add a user to a group.

    Args:
        request: The incoming HTTP request.
        group_id: Database ID of the group.
        body: Member payload with user_id.

    Returns:
        dict[str, Any] | JSONResponse: Membership info or error.

    Raises:
        HTTPException: If the user is already a member of the group.
    """
    try:
        result = await asyncio.to_thread(add_group_member, group_id, body.user_id)
    except IntegrityError:
        raise HTTPException(409, "User is already a member")
    await audit_log(
        request,
        "group.member.add",
        "group",
        result["group_name"],
        detail={"user_id": body.user_id, "user_email": result["user_email"]},
    )
    return result


@router.delete(
    "/api/auth/groups/{group_id}/members/{user_id}",
    dependencies=[Depends(require_role("admin"))],
    response_model=OkResponse,
)
async def remove_group_member_endpoint(
    request: Request, group_id: int, user_id: int
) -> dict[str, Any] | JSONResponse:
    """Remove a user from a group.

    Args:
        request: The incoming HTTP request.
        group_id: Database ID of the group.
        user_id: Database ID of the user to remove.

    Returns:
        dict[str, Any] | JSONResponse: Success or 404.

    Raises:
        HTTPException: If the group or membership does not exist.
    """
    # Fetch group name for audit
    info = await asyncio.to_thread(get_group, group_id)
    if info is None:
        raise HTTPException(404, "Group not found")
    removed = await asyncio.to_thread(remove_group_member, group_id, user_id)
    if not removed:
        raise HTTPException(404, "Membership not found")
    await audit_log(
        request,
        "group.member.remove",
        "group",
        info["name"],
        detail={"user_id": user_id},
    )
    return {"ok": True}


# ─── Group gateway roles (admin-only) ───────────────────────────────────────


@router.get(
    "/api/auth/groups/{group_id}/gateway-roles",
    dependencies=[Depends(require_role("admin"))],
    response_model=list[GatewayRoleResponse],
)
async def get_group_gateway_roles_endpoint(request: Request, group_id: int) -> list[dict[str, Any]]:
    """List gateway-scoped roles for a group.

    Args:
        request: The incoming HTTP request.
        group_id: Database ID of the group.

    Returns:
        list[dict[str, Any]]: Gateway role dicts.
    """
    return await asyncio.to_thread(list_group_gateway_roles, group_id)


@router.put(
    "/api/auth/groups/{group_id}/gateway-roles/{gw}",
    dependencies=[Depends(require_role("admin"))],
    response_model=GatewayRoleResponse,
)
async def set_group_gateway_role_endpoint(
    request: Request, group_id: int, gw: str, body: SetGatewayRoleRequest
) -> dict[str, Any] | JSONResponse:
    """Set a per-gateway role for a group.

    Args:
        request: The incoming HTTP request.
        group_id: Database ID of the group.
        gw: Gateway name.
        body: Role payload.

    Returns:
        dict[str, Any] | JSONResponse: Saved role or error.

    Raises:
        HTTPException: If the role is invalid.
    """
    if body.role not in ROLES:
        raise HTTPException(400, f"Invalid role: {body.role!r}")
    result = await asyncio.to_thread(set_group_gateway_role, group_id, gw, body.role)
    await audit_log(
        request,
        "group.gateway_role.set",
        "group",
        str(group_id),
        detail={"gateway": gw, "role": body.role},
    )
    return result


@router.delete(
    "/api/auth/groups/{group_id}/gateway-roles/{gw}",
    dependencies=[Depends(require_role("admin"))],
    response_model=OkResponse,
)
async def delete_group_gateway_role_endpoint(
    request: Request, group_id: int, gw: str
) -> dict[str, Any] | JSONResponse:
    """Remove a per-gateway role for a group.

    Args:
        request: The incoming HTTP request.
        group_id: Database ID of the group.
        gw: Gateway name.

    Returns:
        dict[str, Any] | JSONResponse: Success or 404.

    Raises:
        HTTPException: If the gateway role does not exist.
    """
    removed = await asyncio.to_thread(remove_group_gateway_role, group_id, gw)
    if not removed:
        raise HTTPException(404, "Gateway role not found")
    await audit_log(
        request,
        "group.gateway_role.remove",
        "group",
        str(group_id),
        detail={"gateway": gw},
    )
    return {"ok": True}


# ─── Page helpers ────────────────────────────────────────────────────────────


def _openshell_meta() -> OpenShellMeta:
    """Lazy import to avoid circular deps at module level.

    Returns:
        OpenShellMeta: Cached metadata about provider types and community sandboxes.
    """
    from shoreguard.services._openshell_meta import get_openshell_meta

    return get_openshell_meta()


def _gw_ctx(gw: str, **extra: object) -> dict[str, Any]:
    """Common template context for gateway-scoped pages.

    Args:
        gw: Gateway name.
        **extra: Additional context variables.

    Returns:
        dict[str, Any]: Template context dict with gateway info.
    """
    return {"active_page": "sandboxes", "gateway_name": gw, **extra}


def _render_error(
    request: Request, status_code: int, title: str, message: str, icon: str = "exclamation-triangle"
) -> HTMLResponse:
    """Render a styled error page.

    Args:
        request: Incoming HTTP request.
        status_code: HTTP status code for the response.
        title: Error title displayed to the user.
        message: Error description displayed to the user.
        icon: Bootstrap icon name for the error page.

    Returns:
        HTMLResponse: Rendered error page with the given status code.
    """
    resp = templates.TemplateResponse(
        request,
        "pages/error.html",
        {"error_title": title, "error_message": message, "error_icon": icon},
    )
    return HTMLResponse(content=resp.body, status_code=status_code, headers=dict(resp.headers))


def _require_page_auth(request: Request) -> RedirectResponse | None:
    """Redirect to /login or /setup based on auth state.

    Args:
        request: Incoming HTTP request.

    Returns:
        RedirectResponse | None: Redirect if unauthenticated, or None if authorized.
    """
    from shoreguard.api.auth import _session_factory

    # If a DB is configured but no users exist yet → setup wizard
    if _session_factory is not None and not is_setup_complete():
        from urllib.parse import quote

        return RedirectResponse(url=f"/setup?next={quote(request.url.path)}", status_code=302)

    role = check_request_auth(request)
    if role is None:
        from urllib.parse import quote

        return RedirectResponse(url=f"/login?next={quote(request.url.path)}", status_code=302)
    request.state.role = role
    return None


# ─── Global pages ────────────────────────────────────────────────────────────


@router.get("/login", response_model=None)
async def login_page(request: Request) -> TemplateResponse:
    """Serve the login page.

    Args:
        request: Incoming HTTP request.

    Returns:
        TemplateResponse: Rendered login page.
    """
    return templates.TemplateResponse(request, "pages/login.html", {})


@router.get("/register", response_model=None)
async def register_page(request: Request) -> TemplateResponse | HTMLResponse:
    """Serve the self-registration page.

    Args:
        request: Incoming HTTP request.

    Returns:
        TemplateResponse | HTMLResponse: Rendered registration page, or error if disabled.
    """
    if not is_registration_enabled():
        return _render_error(
            request,
            403,
            "Registration Disabled",
            "Self-registration is not enabled on this instance. "
            "Ask an administrator for an invite.",
            icon="person-x",
        )
    return templates.TemplateResponse(request, "pages/register.html", {})


@router.get("/invite", response_model=None)
async def invite_page(request: Request) -> TemplateResponse:
    """Serve the invite acceptance page.

    Args:
        request: Incoming HTTP request.

    Returns:
        TemplateResponse: Rendered invite acceptance page.
    """
    return templates.TemplateResponse(request, "pages/invite.html", {})


@router.get("/setup", response_model=None)
async def setup_page(request: Request) -> TemplateResponse | RedirectResponse:
    """Serve the setup wizard (only when no users exist).

    Args:
        request: Incoming HTTP request.

    Returns:
        TemplateResponse | RedirectResponse: Rendered setup page, or redirect if already set up.
    """
    if is_setup_complete():
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(request, "pages/setup.html", {})


@router.get("/", response_model=None)
async def dashboard_page(request: Request) -> TemplateResponse | RedirectResponse:
    """Dashboard overview page.

    Args:
        request: Incoming HTTP request.

    Returns:
        TemplateResponse | RedirectResponse: Rendered dashboard page.
    """
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "pages/dashboard.html",
        {"active_page": "dashboard"},
    )


@router.get("/gateways", response_model=None)
async def gateways_page(request: Request) -> TemplateResponse | RedirectResponse:
    """Gateway list page.

    Args:
        request: Incoming HTTP request.

    Returns:
        TemplateResponse | RedirectResponse: Rendered gateways list page.
    """
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "pages/gateways.html",
        {"active_page": "gateways"},
    )


@router.get("/gateways/{name:path}", response_model=None)
async def gateway_detail_or_sub(
    request: Request, name: str
) -> TemplateResponse | RedirectResponse | HTMLResponse:
    """Gateway detail page or gateway-scoped sub-pages.

    Args:
        request: Incoming HTTP request.
        name: Gateway name, optionally followed by a sub-path.

    Returns:
        TemplateResponse | RedirectResponse | HTMLResponse: Rendered gateway page or 404 error page.
    """
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    parts = name.split("/", 1)
    gw = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    # Register new gateway page
    if gw == "new" and not rest:
        return templates.TemplateResponse(
            request,
            "pages/gateway_register.html",
            {"active_page": "gateways"},
        )

    # Gateway detail (no sub-path)
    if not rest:
        return templates.TemplateResponse(
            request,
            "pages/gateway_detail.html",
            {"active_page": "gateways", "gateway_name": gw},
        )

    # ── Gateway-scoped pages ────────────────────────────────────────
    ctx = _gw_ctx(gw)

    # Sandboxes
    if rest == "sandboxes":
        return templates.TemplateResponse(request, "pages/sandboxes.html", ctx)

    if rest.startswith("sandboxes/"):
        sb_path = rest[len("sandboxes/") :]
        sb_parts = sb_path.split("/", 1)
        sb_name = sb_parts[0]
        sb_rest = sb_parts[1] if len(sb_parts) > 1 else ""
        ctx["sandbox_name"] = sb_name

        if not sb_rest:
            ctx["active_tab"] = "overview"
            return templates.TemplateResponse(request, "pages/sandbox_detail.html", ctx)
        if sb_rest == "policy":
            ctx["active_tab"] = "policy"
            return templates.TemplateResponse(request, "pages/sandbox_policy.html", ctx)
        if sb_rest == "approvals":
            ctx["active_tab"] = "approvals"
            return templates.TemplateResponse(request, "pages/sandbox_approvals.html", ctx)
        if sb_rest == "logs":
            ctx["active_tab"] = "logs"
            return templates.TemplateResponse(request, "pages/sandbox_logs.html", ctx)
        if sb_rest == "terminal":
            ctx["active_tab"] = "terminal"
            return templates.TemplateResponse(request, "pages/sandbox_terminal.html", ctx)
        if sb_rest == "bypass":
            ctx["active_tab"] = "bypass"
            return templates.TemplateResponse(request, "pages/sandbox_bypass.html", ctx)
        if sb_rest == "verify":
            ctx["active_tab"] = "prover"
            return templates.TemplateResponse(request, "pages/sandbox_prover.html", ctx)
        if sb_rest == "sbom":
            ctx["active_tab"] = "sbom"
            return templates.TemplateResponse(request, "pages/sandbox_sbom.html", ctx)
        if sb_rest == "hooks":
            ctx["active_tab"] = "hooks"
            return templates.TemplateResponse(request, "pages/sandbox_hooks.html", ctx)
        if sb_rest == "network-policies":
            return templates.TemplateResponse(
                request,
                "pages/policy_section.html",
                {
                    **ctx,
                    "section": "network",
                    "section_title": "Network Policies",
                    "section_icon": "globe",
                },
            )
        if sb_rest == "filesystem-policy":
            return templates.TemplateResponse(
                request,
                "pages/policy_section.html",
                {
                    **ctx,
                    "section": "filesystem",
                    "section_title": "Filesystem Policy",
                    "section_icon": "folder",
                },
            )
        if sb_rest == "process-policy":
            return templates.TemplateResponse(
                request,
                "pages/policy_section.html",
                {
                    **ctx,
                    "section": "process",
                    "section_title": "Process & Landlock",
                    "section_icon": "cpu",
                },
            )
        if sb_rest == "apply-preset":
            return templates.TemplateResponse(
                request,
                "pages/policy_section.html",
                {
                    **ctx,
                    "section": "presets",
                    "section_title": "Apply Preset",
                    "section_icon": "shield-plus",
                },
            )
        if sb_rest.startswith("rules/"):
            rule_key = sb_rest[len("rules/") :]
            ctx["rule_key"] = rule_key
            return templates.TemplateResponse(request, "pages/rule_detail.html", ctx)

    # Providers
    if rest == "providers":
        meta = _openshell_meta()
        ctx["active_page"] = "providers"
        ctx["provider_types"] = [{"type": k, **v} for k, v in meta.provider_types.items()]
        return templates.TemplateResponse(request, "pages/providers.html", ctx)

    if rest == "providers/new":
        meta = _openshell_meta()
        ctx["active_page"] = "providers"
        ctx["provider_types"] = [{"type": k, **v} for k, v in meta.provider_types.items()]
        ctx["mode"] = "create"
        ctx["provider_name"] = ""
        return templates.TemplateResponse(request, "pages/provider_form.html", ctx)

    if rest.startswith("providers/") and rest.endswith("/edit"):
        provider_name = rest[len("providers/") : -len("/edit")]
        if provider_name:
            meta = _openshell_meta()
            ctx["active_page"] = "providers"
            ctx["provider_types"] = [{"type": k, **v} for k, v in meta.provider_types.items()]
            ctx["mode"] = "edit"
            ctx["provider_name"] = provider_name
            return templates.TemplateResponse(request, "pages/provider_form.html", ctx)

    # Wizard
    if rest == "wizard":
        meta = _openshell_meta()
        ctx["active_page"] = "wizard"
        ctx["community_sandboxes"] = meta.community_sandboxes
        return templates.TemplateResponse(request, "pages/wizard.html", ctx)

    return _render_error(
        request,
        404,
        "Page Not Found",
        "The page you are looking for does not exist.",
        icon="question-circle",
    )


@router.get("/policies", response_model=None)
async def policies_page(request: Request) -> TemplateResponse | RedirectResponse:
    """Policy presets list page (global, not gateway-scoped).

    Args:
        request: Incoming HTTP request.

    Returns:
        TemplateResponse | RedirectResponse: Rendered policies list page.
    """
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "pages/policies.html",
        {"active_page": "policies"},
    )


@router.get("/policies/{name}", response_model=None)
async def preset_detail_page(request: Request, name: str) -> TemplateResponse | RedirectResponse:
    """Preset detail page (global).

    Args:
        request: Incoming HTTP request.
        name: Preset name.

    Returns:
        TemplateResponse | RedirectResponse: Rendered preset detail page.
    """
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "pages/preset_detail.html",
        {"active_page": "policies", "preset_name": name},
    )


@router.get("/audit", response_model=None)
async def audit_page(request: Request) -> TemplateResponse | RedirectResponse | HTMLResponse:
    """Audit log page (admin only).

    Args:
        request: Incoming HTTP request.

    Returns:
        TemplateResponse | RedirectResponse | HTMLResponse: Rendered audit log
            page or access denied error.
    """
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    if getattr(request.state, "role", None) != "admin":
        return _render_error(
            request,
            403,
            "Access Denied",
            "You need admin privileges to view the audit log.",
            icon="shield-lock",
        )
    return templates.TemplateResponse(
        request,
        "pages/audit.html",
        {"active_page": "audit"},
    )


@router.get("/groups", response_model=None)
async def groups_page(request: Request) -> TemplateResponse | RedirectResponse | HTMLResponse:
    """Group management page (admin only).

    Args:
        request: Incoming HTTP request.

    Returns:
        TemplateResponse | RedirectResponse | HTMLResponse: Rendered groups page
            or access denied error.
    """
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    if getattr(request.state, "role", None) != "admin":
        return _render_error(
            request,
            403,
            "Access Denied",
            "You need admin privileges to manage groups.",
            icon="shield-lock",
        )
    return templates.TemplateResponse(
        request,
        "pages/groups.html",
        {"active_page": "groups"},
    )


@router.get("/users", response_model=None)
async def users_page(request: Request) -> TemplateResponse | RedirectResponse | HTMLResponse:
    """User and service principal management page (admin only).

    Args:
        request: Incoming HTTP request.

    Returns:
        TemplateResponse | RedirectResponse | HTMLResponse: Rendered users
            management page or access denied error.
    """
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    if getattr(request.state, "role", None) != "admin":
        return _render_error(
            request,
            403,
            "Access Denied",
            "You need admin privileges to manage users and service principals.",
            icon="shield-lock",
        )
    return templates.TemplateResponse(
        request,
        "pages/users.html",
        {"active_page": "users"},
    )


@router.get("/webhooks", response_model=None)
async def webhooks_page(request: Request) -> TemplateResponse | RedirectResponse | HTMLResponse:
    """Webhook subscription management page (admin only).

    Args:
        request: Incoming HTTP request.

    Returns:
        TemplateResponse | RedirectResponse | HTMLResponse: Rendered webhooks
            management page or access denied error.
    """
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    if getattr(request.state, "role", None) != "admin":
        return _render_error(
            request,
            403,
            "Access Denied",
            "You need admin privileges to manage webhooks.",
            icon="shield-lock",
        )
    return templates.TemplateResponse(
        request,
        "pages/webhooks.html",
        {"active_page": "webhooks"},
    )


@router.get("/users/new", response_model=None)
async def user_new_page(request: Request) -> TemplateResponse | RedirectResponse | HTMLResponse:
    """Invite user form page (admin only).

    Args:
        request: Incoming HTTP request.

    Returns:
        TemplateResponse | RedirectResponse | HTMLResponse: Rendered invite
            user form or access denied error.
    """
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    if getattr(request.state, "role", None) != "admin":
        return _render_error(
            request,
            403,
            "Access Denied",
            "You need admin privileges to invite users.",
            icon="shield-lock",
        )
    return templates.TemplateResponse(request, "pages/user_new.html", {"active_page": "users"})


@router.get("/users/new-service-principal", response_model=None)
async def sp_new_page(request: Request) -> TemplateResponse | RedirectResponse | HTMLResponse:
    """Create service principal form page (admin only).

    Args:
        request: Incoming HTTP request.

    Returns:
        TemplateResponse | RedirectResponse | HTMLResponse: Rendered service
            principal form or access denied error.
    """
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    if getattr(request.state, "role", None) != "admin":
        return _render_error(
            request,
            403,
            "Access Denied",
            "You need admin privileges to create service principals.",
            icon="shield-lock",
        )
    return templates.TemplateResponse(request, "pages/sp_new.html", {"active_page": "users"})
