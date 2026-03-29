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
    check_api_key,
    check_request_auth,
    create_api_key,
    create_session_token,
    delete_api_key,
    is_auth_enabled,
    list_api_keys,
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

    key: str


@router.post("/api/auth/login")
async def login(request: Request, body: LoginRequest):
    """Validate the API key and set a session cookie."""
    if not is_auth_enabled():
        return JSONResponse(
            status_code=400,
            content={"detail": "Authentication is not enabled"},
        )
    role = check_api_key(body.key)
    if not role:
        client_ip = request.client.host if request.client else "unknown"
        logger.warning("Login failed: invalid API key (client=%s)", client_ip)
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid API key"},
        )
    client_ip = request.client.host if request.client else "unknown"
    logger.info("Login successful (client=%s, role=%s)", client_ip, role)
    token = create_session_token(role=role)
    response = JSONResponse(content={"ok": True, "role": role})
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
    """Return whether the current request is authenticated.

    Used by the frontend to decide whether to show the login page.
    """
    if not is_auth_enabled():
        return {"authenticated": True, "auth_enabled": False, "role": "admin"}

    role = check_request_auth(request)
    return {"authenticated": role is not None, "auth_enabled": True, "role": role}


# ─── API key management (admin-only) ─────────────────────────────────────────


class CreateKeyRequest(BaseModel):
    """Request body for creating an API key."""

    name: str
    role: str = "viewer"


def _require_admin(request: Request) -> None:
    role = check_request_auth(request)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/api/auth/keys")
async def list_keys(request: Request):
    """List all API keys (admin only)."""
    _require_admin(request)
    return list_api_keys()


@router.post("/api/auth/keys", status_code=201)
async def create_key(request: Request, body: CreateKeyRequest):
    """Create a new API key (admin only)."""
    _require_admin(request)
    if body.role not in ROLES:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Invalid role: {body.role!r} (must be one of {ROLES})"},
        )
    if not body.name.strip():
        return JSONResponse(status_code=400, content={"detail": "Name is required"})
    try:
        plaintext, info = create_api_key(body.name.strip(), body.role)
    except Exception as e:
        detail = str(e)
        if "UNIQUE" in detail or "unique" in detail.lower():
            detail = f"A key named '{body.name.strip()}' already exists"
        return JSONResponse(status_code=409, content={"detail": detail})
    return {"key": plaintext, **info}


@router.delete("/api/auth/keys/{name}")
async def remove_key(request: Request, name: str):
    """Delete an API key (admin only)."""
    _require_admin(request)
    if delete_api_key(name):
        return {"ok": True}
    return JSONResponse(status_code=404, content={"detail": f"Key '{name}' not found"})


# ─── Page helpers ────────────────────────────────────────────────────────────


def _openshell_meta():
    """Lazy import to avoid circular deps at module level."""
    from shoreguard.services._openshell_meta import get_openshell_meta

    return get_openshell_meta()


def _gw_ctx(gw: str, **extra: object) -> dict:
    """Common template context for gateway-scoped pages."""
    return {"active_page": "sandboxes", "gateway_name": gw, **extra}


def _require_page_auth(request: Request):
    """Redirect to /login if auth is enabled and the request has no valid session."""
    if not is_auth_enabled():
        return None
    if check_request_auth(request):
        return None
    from urllib.parse import quote

    return RedirectResponse(url=f"/login?next={quote(request.url.path)}", status_code=302)


# ─── Global pages ────────────────────────────────────────────────────────────


@router.get("/login")
async def login_page(request: Request):
    """Serve the login page."""
    return templates.TemplateResponse(request, "pages/login.html", {})


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

    return JSONResponse(status_code=404, content={"detail": "Page not found"})


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


@router.get("/keys")
async def keys_page(request: Request):
    """API key management page (admin only)."""
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    role = check_request_auth(request)
    if role != "admin":
        return JSONResponse(status_code=403, content={"detail": "Admin access required"})
    return templates.TemplateResponse(
        request,
        "pages/keys.html",
        {"active_page": "keys"},
    )
