"""HTML page routes and auth API endpoints for the Shoreguard frontend."""

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from shoreguard.services.audit import audit_log

from .auth import (
    COOKIE_NAME,
    ROLES,
    accept_invite,
    authenticate_user,
    check_request_auth,
    create_service_principal,
    create_session_token,
    create_user,
    delete_service_principal,
    delete_user,
    is_registration_enabled,
    is_setup_complete,
    list_service_principals,
    list_users,
    require_role,
    verify_session_token,
)

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


def _valid_email(email: str) -> bool:
    """Basic email format check."""
    return bool(_EMAIL_RE.match(email.strip()))


def _client_ip(request: Request) -> str:
    """Extract client IP from request."""
    return request.client.host if request.client else "unknown"


def _get_actor(request: Request) -> str:
    """Extract acting user identity from request state."""
    user_id = getattr(request.state, "user_id", None)
    return str(user_id) if user_id else "unknown"


def _resolve_frontend_dir() -> Path:
    """Resolve the frontend directory for both installed and dev-checkout modes."""
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

router = APIRouter()


# ─── Auth endpoints ──────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    """Request body for the login endpoint."""

    email: str
    password: str


@router.post("/api/auth/login")
async def login(request: Request, body: LoginRequest):
    """Validate credentials and set a session cookie."""
    if not is_setup_complete():
        return JSONResponse(
            status_code=400,
            content={"detail": "Setup not complete — create an admin user first"},
        )
    if len(body.password) > 128:
        return JSONResponse(
            status_code=400, content={"detail": "Password must be at most 128 characters"}
        )
    user = authenticate_user(body.email, body.password)
    if not user:
        logger.warning("Login failed: invalid credentials (client=%s)", _client_ip(request))
        request.state.user_id = body.email
        request.state.role = "unknown"
        await audit_log(request, "user.login_failed", "user", body.email)
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid email or password"},
        )
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
async def logout(request: Request):
    """Clear the session cookie."""
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


@router.get("/api/auth/check")
async def auth_check(request: Request):
    """Return auth status, role, and whether setup is needed."""
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
    return {
        "authenticated": role is not None,
        "auth_enabled": True,
        "role": role,
        "email": email,
        "needs_setup": False,
        "registration_enabled": is_registration_enabled(),
    }


# ─── Setup wizard ───────────────────────────────────────────────────────────


class SetupRequest(BaseModel):
    """Request body for the initial admin setup."""

    email: str
    password: str


@router.post("/api/auth/setup")
async def setup(request: Request, body: SetupRequest):
    """Create the first admin user. Only works when no users exist."""
    if is_setup_complete():
        return JSONResponse(status_code=400, content={"detail": "Setup already complete"})
    if not body.email.strip() or not body.password:
        return JSONResponse(status_code=400, content={"detail": "Email and password are required"})
    if not _valid_email(body.email):
        return JSONResponse(status_code=400, content={"detail": "Invalid email format"})
    if len(body.password) < 8:
        return JSONResponse(
            status_code=400, content={"detail": "Password must be at least 8 characters"}
        )
    if len(body.password) > 128:
        return JSONResponse(
            status_code=400, content={"detail": "Password must be at most 128 characters"}
        )
    try:
        info = create_user(body.email.strip(), body.password, "admin")
    except IntegrityError:
        logger.warning("Setup failed: duplicate admin email (email=%s)", body.email.strip())
        return JSONResponse(
            status_code=409,
            content={"detail": f"A user with email '{body.email.strip()}' already exists"},
        )
    except Exception:
        logger.exception("Setup failed")
        return JSONResponse(status_code=500, content={"detail": "Setup failed"})

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
    """Request body for inviting a user."""

    email: str
    role: str = "viewer"


@router.get("/api/auth/users", dependencies=[Depends(require_role("admin"))])
async def get_users(request: Request):
    """List all users (admin only)."""
    return list_users()


@router.post("/api/auth/users", status_code=201, dependencies=[Depends(require_role("admin"))])
async def create_user_endpoint(request: Request, body: CreateUserRequest):
    """Invite a new user (admin only). Returns an invite token."""
    if body.role not in ROLES:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Invalid role: {body.role!r} (must be one of {ROLES})"},
        )
    if not body.email.strip():
        return JSONResponse(status_code=400, content={"detail": "Email is required"})
    if not _valid_email(body.email):
        return JSONResponse(status_code=400, content={"detail": "Invalid email format"})
    try:
        info = create_user(body.email.strip(), None, body.role)
    except IntegrityError:
        logger.warning(
            "Duplicate user creation attempt (email=%s, actor=%s)",
            body.email.strip(),
            _get_actor(request),
        )
        return JSONResponse(
            status_code=409,
            content={"detail": f"A user with email '{body.email.strip()}' already exists"},
        )
    except Exception:
        logger.exception("Failed to create user")
        return JSONResponse(status_code=500, content={"detail": "Failed to create user"})
    logger.info(
        "User invited (email=%s, role=%s, actor=%s)", info["email"], body.role, _get_actor(request)
    )
    await audit_log(request, "user.invite", "user", info["email"], detail={"role": body.role})
    return info


@router.delete("/api/auth/users/{user_id}", dependencies=[Depends(require_role("admin"))])
async def delete_user_endpoint(request: Request, user_id: int):
    """Delete a user (admin only)."""
    # Prevent self-deletion
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        result = verify_session_token(cookie)
        if result and result[0] == user_id:
            return JSONResponse(
                status_code=400, content={"detail": "Cannot delete your own account"}
            )
    # Prevent deleting the last admin
    users = list_users()
    active_admins = [u for u in users if u.get("role") == "admin" and u.get("is_active")]
    target_is_admin = any(u["id"] == user_id and u.get("role") == "admin" for u in users)
    if target_is_admin and len(active_admins) <= 1:
        return JSONResponse(
            status_code=400, content={"detail": "Cannot delete the last admin user"}
        )
    if delete_user(user_id):
        logger.info("User deleted (user_id=%s, actor=%s)", user_id, _get_actor(request))
        await audit_log(request, "user.delete", "user", str(user_id))
        return {"ok": True}
    return JSONResponse(status_code=404, content={"detail": "User not found"})


# ─── Invite acceptance (public) ─────────────────────────────────────────────


class AcceptInviteRequest(BaseModel):
    """Request body for accepting an invite."""

    token: str
    password: str


@router.post("/api/auth/accept-invite")
async def accept_invite_endpoint(request: Request, body: AcceptInviteRequest):
    """Accept an invite and set password. Returns session cookie."""
    if not body.password or len(body.password) < 8:
        return JSONResponse(
            status_code=400, content={"detail": "Password must be at least 8 characters"}
        )
    if len(body.password) > 128:
        return JSONResponse(
            status_code=400, content={"detail": "Password must be at most 128 characters"}
        )
    user = accept_invite(body.token, body.password)
    if not user:
        return JSONResponse(status_code=400, content={"detail": "Invalid or expired invite token"})

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
    """Request body for self-registration."""

    email: str
    password: str


@router.post("/api/auth/register")
async def register_endpoint(request: Request, body: RegisterRequest):
    """Self-register a new viewer account. Requires SHOREGUARD_ALLOW_REGISTRATION."""
    if not is_registration_enabled():
        return JSONResponse(status_code=403, content={"detail": "Registration is disabled"})
    if not is_setup_complete():
        return JSONResponse(
            status_code=400, content={"detail": "Setup not complete — use /setup first"}
        )
    if not body.email.strip() or not body.password:
        return JSONResponse(status_code=400, content={"detail": "Email and password are required"})
    if not _valid_email(body.email):
        return JSONResponse(status_code=400, content={"detail": "Invalid email format"})
    if len(body.password) < 8:
        return JSONResponse(
            status_code=400, content={"detail": "Password must be at least 8 characters"}
        )
    if len(body.password) > 128:
        return JSONResponse(
            status_code=400, content={"detail": "Password must be at most 128 characters"}
        )
    try:
        info = create_user(body.email.strip(), body.password, "viewer")
    except IntegrityError:
        logger.warning(
            "Duplicate registration attempt (email=%s, client=%s)",
            body.email.strip(),
            _client_ip(request),
        )
        return JSONResponse(
            status_code=409,
            content={"detail": f"An account with email '{body.email.strip()}' already exists"},
        )
    except Exception:
        logger.exception("Registration failed")
        return JSONResponse(status_code=500, content={"detail": "Registration failed"})

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
    """Request body for creating a service principal."""

    name: str
    role: str = "viewer"


@router.get("/api/auth/service-principals", dependencies=[Depends(require_role("admin"))])
async def get_sps(request: Request):
    """List all service principals (admin only)."""
    return list_service_principals()


@router.post(
    "/api/auth/service-principals", status_code=201, dependencies=[Depends(require_role("admin"))]
)
async def create_sp_endpoint(request: Request, body: CreateSPRequest):
    """Create a new service principal (admin only)."""
    if body.role not in ROLES:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Invalid role: {body.role!r} (must be one of {ROLES})"},
        )
    if not body.name.strip():
        return JSONResponse(status_code=400, content={"detail": "Name is required"})
    try:
        plaintext, info = create_service_principal(body.name.strip(), body.role)
    except IntegrityError:
        logger.warning(
            "Duplicate service principal creation attempt (name=%s, actor=%s)",
            body.name.strip(),
            _get_actor(request),
        )
        return JSONResponse(
            status_code=409,
            content={"detail": f"A service principal named '{body.name.strip()}' already exists"},
        )
    except Exception:
        logger.exception("Failed to create service principal")
        return JSONResponse(
            status_code=500, content={"detail": "Failed to create service principal"}
        )
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
    "/api/auth/service-principals/{sp_id}", dependencies=[Depends(require_role("admin"))]
)
async def delete_sp_endpoint(request: Request, sp_id: int):
    """Delete a service principal (admin only)."""
    if delete_service_principal(sp_id):
        logger.info("Service principal deleted (sp_id=%s, actor=%s)", sp_id, _get_actor(request))
        await audit_log(request, "sp.delete", "service_principal", str(sp_id))
        return {"ok": True}
    return JSONResponse(status_code=404, content={"detail": "Service principal not found"})


# ─── Page helpers ────────────────────────────────────────────────────────────


def _openshell_meta():
    """Lazy import to avoid circular deps at module level."""
    from shoreguard.services._openshell_meta import get_openshell_meta

    return get_openshell_meta()


def _gw_ctx(gw: str, **extra: object) -> dict:
    """Common template context for gateway-scoped pages."""
    return {"active_page": "sandboxes", "gateway_name": gw, **extra}


def _render_error(
    request: Request, status_code: int, title: str, message: str, icon: str = "exclamation-triangle"
):
    """Render a styled error page."""
    from starlette.responses import HTMLResponse

    resp = templates.TemplateResponse(
        request,
        "pages/error.html",
        {"error_title": title, "error_message": message, "error_icon": icon},
    )
    return HTMLResponse(content=resp.body, status_code=status_code, headers=dict(resp.headers))


def _require_page_auth(request: Request):
    """Redirect to /login or /setup based on auth state."""
    from shoreguard.api.auth import _session_factory

    # If a DB is configured but no users exist yet → setup wizard
    if _session_factory is not None and not is_setup_complete():
        from urllib.parse import quote

        return RedirectResponse(url=f"/setup?next={quote(request.url.path)}", status_code=302)

    role = check_request_auth(request)
    if role is None:
        from urllib.parse import quote

        return RedirectResponse(url=f"/login?next={quote(request.url.path)}", status_code=302)
    return None


# ─── Global pages ────────────────────────────────────────────────────────────


@router.get("/login")
async def login_page(request: Request):
    """Serve the login page."""
    return templates.TemplateResponse(request, "pages/login.html", {})


@router.get("/register")
async def register_page(request: Request):
    """Serve the self-registration page."""
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


@router.get("/invite")
async def invite_page(request: Request):
    """Serve the invite acceptance page."""
    return templates.TemplateResponse(request, "pages/invite.html", {})


@router.get("/setup")
async def setup_page(request: Request):
    """Serve the setup wizard (only when no users exist)."""
    if is_setup_complete():
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(request, "pages/setup.html", {})


@router.get("/")
async def dashboard_redirect(request: Request):
    """Redirect root to gateways list."""
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    return RedirectResponse(url="/gateways", status_code=302)


@router.get("/gateways")
async def gateways_page(request: Request):
    """Gateway list page."""
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "pages/gateways.html",
        {"active_page": "gateways"},
    )


@router.get("/gateways/{name:path}")
async def gateway_detail_or_sub(request: Request, name: str):
    """Gateway detail page or gateway-scoped sub-pages."""
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    parts = name.split("/", 1)
    gw = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

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


@router.get("/policies")
async def policies_page(request: Request):
    """Policy presets list page (global, not gateway-scoped)."""
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "pages/policies.html",
        {"active_page": "policies"},
    )


@router.get("/policies/{name}")
async def preset_detail_page(request: Request, name: str):
    """Preset detail page (global)."""
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "pages/preset_detail.html",
        {"active_page": "policies", "preset_name": name},
    )


@router.get("/audit")
async def audit_page(request: Request):
    """Audit log page (admin only)."""
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    role = check_request_auth(request)
    if role != "admin":
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


@router.get("/users")
async def users_page(request: Request):
    """User and service principal management page (admin only)."""
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    role = check_request_auth(request)
    if role != "admin":
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


@router.get("/users/new")
async def user_new_page(request: Request):
    """Invite user form page (admin only)."""
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    role = check_request_auth(request)
    if role != "admin":
        return _render_error(
            request,
            403,
            "Access Denied",
            "You need admin privileges to invite users.",
            icon="shield-lock",
        )
    return templates.TemplateResponse(request, "pages/user_new.html", {"active_page": "users"})


@router.get("/users/new-service-principal")
async def sp_new_page(request: Request):
    """Create service principal form page (admin only)."""
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    role = check_request_auth(request)
    if role != "admin":
        return _render_error(
            request,
            403,
            "Access Denied",
            "You need admin privileges to create service principals.",
            icon="shield-lock",
        )
    return templates.TemplateResponse(request, "pages/sp_new.html", {"active_page": "users"})
