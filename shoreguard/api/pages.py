"""HTML page routes and auth API endpoints for the Shoreguard frontend."""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

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
    verify_session_token,
)

logger = logging.getLogger(__name__)


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
    user = authenticate_user(body.email, body.password)
    if not user:
        client_ip = request.client.host if request.client else "unknown"
        logger.warning("Login failed: invalid credentials (client=%s)", client_ip)
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid email or password"},
        )
    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        "Login successful (client=%s, email=%s, role=%s)", client_ip, user["email"], user["role"]
    )
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
async def logout():
    """Clear the session cookie."""
    logger.debug("Logout")
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
    try:
        info = create_user(body.email.strip(), body.password, "admin")
    except Exception as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

    logger.info("Setup complete: admin user created (%s)", info["email"])
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


def _require_admin(request: Request) -> None:
    role = check_request_auth(request)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/api/auth/users")
async def get_users(request: Request):
    """List all users (admin only)."""
    _require_admin(request)
    return list_users()


@router.post("/api/auth/users", status_code=201)
async def create_user_endpoint(request: Request, body: CreateUserRequest):
    """Invite a new user (admin only). Returns an invite token."""
    _require_admin(request)
    if body.role not in ROLES:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Invalid role: {body.role!r} (must be one of {ROLES})"},
        )
    if not body.email.strip():
        return JSONResponse(status_code=400, content={"detail": "Email is required"})
    try:
        info = create_user(body.email.strip(), None, body.role)
    except Exception as e:
        detail = str(e)
        if "UNIQUE" in detail or "unique" in detail.lower():
            detail = f"A user with email '{body.email.strip()}' already exists"
        return JSONResponse(status_code=409, content={"detail": detail})
    return info


@router.delete("/api/auth/users/{user_id}")
async def delete_user_endpoint(request: Request, user_id: int):
    """Delete a user (admin only)."""
    _require_admin(request)
    if delete_user(user_id):
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
    user = accept_invite(body.token, body.password)
    if not user:
        return JSONResponse(status_code=400, content={"detail": "Invalid or expired invite token"})

    logger.info("Invite accepted (email=%s, role=%s)", user["email"], user["role"])
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
    if len(body.password) < 8:
        return JSONResponse(
            status_code=400, content={"detail": "Password must be at least 8 characters"}
        )
    try:
        info = create_user(body.email.strip(), body.password, "viewer")
    except Exception as e:
        detail = str(e)
        if "UNIQUE" in detail or "unique" in detail.lower():
            detail = f"An account with email '{body.email.strip()}' already exists"
        return JSONResponse(status_code=409, content={"detail": detail})

    logger.info("Self-registration (email=%s)", info["email"])
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


@router.get("/api/auth/service-principals")
async def get_sps(request: Request):
    """List all service principals (admin only)."""
    _require_admin(request)
    return list_service_principals()


@router.post("/api/auth/service-principals", status_code=201)
async def create_sp_endpoint(request: Request, body: CreateSPRequest):
    """Create a new service principal (admin only)."""
    _require_admin(request)
    if body.role not in ROLES:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Invalid role: {body.role!r} (must be one of {ROLES})"},
        )
    if not body.name.strip():
        return JSONResponse(status_code=400, content={"detail": "Name is required"})
    try:
        plaintext, info = create_service_principal(body.name.strip(), body.role)
    except Exception as e:
        detail = str(e)
        if "UNIQUE" in detail or "unique" in detail.lower():
            detail = f"A service principal named '{body.name.strip()}' already exists"
        return JSONResponse(status_code=409, content={"detail": detail})
    return {"key": plaintext, **info}


@router.delete("/api/auth/service-principals/{sp_id}")
async def delete_sp_endpoint(request: Request, sp_id: int):
    """Delete a service principal (admin only)."""
    _require_admin(request)
    if delete_service_principal(sp_id):
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
