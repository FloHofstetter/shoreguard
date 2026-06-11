"""HTML page routes for the ShoreGuard frontend."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse
from starlette.templating import _TemplateResponse as TemplateResponse

if TYPE_CHECKING:
    from shoreguard.services._openshell_meta import OpenShellMeta


from .auth import (
    check_request_auth,
    is_registration_enabled,
    is_setup_complete,
)

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


def _client_ip(request: Request) -> str:
    """Extract client IP from request.

    Args:
        request: Incoming HTTP request.

    Returns:
        str: Client IP address or ``"unknown"``.
    """
    return request.client.host if request.client else "unknown"


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


async def _require_page_auth(request: Request) -> RedirectResponse | None:
    """Redirect to /login or /setup based on auth state.

    Args:
        request: Incoming HTTP request.

    Returns:
        RedirectResponse | None: Redirect if unauthenticated, or None if authorized.
    """
    from shoreguard.api.auth import state

    # If a DB is configured but no users exist yet → setup wizard
    if state.session_factory is not None and not await is_setup_complete():
        from urllib.parse import quote

        return RedirectResponse(url=f"/setup?next={quote(request.url.path)}", status_code=302)

    role = await check_request_auth(request)
    if role is None:
        from urllib.parse import quote

        return RedirectResponse(url=f"/login?next={quote(request.url.path)}", status_code=302)
    request.state.role = role
    return None


# ─── Global pages ────────────────────────────────────────────────────────────


@router.get("/sw.js", include_in_schema=False)
async def service_worker() -> FileResponse:
    """Serve the Web Push service worker from the app root.

    Served at ``/sw.js`` (not ``/static/``) so its scope covers the
    whole app — a service worker can only control pages under its own
    path.

    Returns:
        FileResponse: The service worker script.
    """
    return FileResponse(FRONTEND_DIR / "sw.js", media_type="text/javascript")


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
    if await is_setup_complete():
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
    redirect = await _require_page_auth(request)
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
    redirect = await _require_page_auth(request)
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
    redirect = await _require_page_auth(request)
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
        if sb_rest == "forward":
            ctx["active_tab"] = "forward"
            return templates.TemplateResponse(request, "pages/sandbox_forward.html", ctx)
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

    # Provider profiles (M37 / OpenShell PR #1170 — typed provider-type registry)
    if rest == "provider-profiles":
        ctx["active_page"] = "provider-profiles"
        return templates.TemplateResponse(request, "pages/provider_profiles.html", ctx)

    # Service routing (M38 / OpenShell PR #1101 — local-domain service endpoints)
    if rest == "services":
        ctx["active_page"] = "services"
        return templates.TemplateResponse(request, "pages/services.html", ctx)

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
    redirect = await _require_page_auth(request)
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
    redirect = await _require_page_auth(request)
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
    redirect = await _require_page_auth(request)
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


@router.get("/approvals/one-tap", response_model=None)
async def one_tap_page(request: Request, token: str = "") -> TemplateResponse | HTMLResponse:
    """Mobile confirmation page for a one-tap approval link.

    The signed token in the query string is the credential — no session is
    required. The page only *shows* the decision; casting it is a POST to
    ``/api/approvals/one-tap`` from the island.

    Args:
        request: Incoming HTTP request.
        token: Signed one-tap token from the notification link.

    Returns:
        TemplateResponse | HTMLResponse: Confirmation page, or an error page
            when the feature is disabled or the token is invalid.
    """
    from shoreguard.services.approval_links import verify_one_tap_token
    from shoreguard.settings import get_settings

    if not get_settings().webhooks.one_tap_approvals:
        return _render_error(
            request, 404, "Not Found", "One-tap approvals are disabled.", icon="link-45deg"
        )
    data = verify_one_tap_token(token)
    if data is None:
        return _render_error(
            request,
            400,
            "Invalid Link",
            "This approval link is invalid or has expired. Open ShoreGuard to vote.",
            icon="link-45deg",
        )
    return templates.TemplateResponse(
        request,
        "pages/one_tap.html",
        {"props": {**data, "token": token}},
    )


@router.get("/profile", response_model=None)
async def profile_page(request: Request) -> TemplateResponse | RedirectResponse:
    """Personal settings: passkeys and push devices.

    Args:
        request: Incoming HTTP request.

    Returns:
        TemplateResponse | RedirectResponse: Rendered profile page, or a
            redirect to login.
    """
    redirect = await _require_page_auth(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "pages/profile.html",
        {"active_page": "profile"},
    )


@router.get("/security", response_model=None)
async def security_page(request: Request) -> TemplateResponse | RedirectResponse | HTMLResponse:
    """Security posture self-check page (admin only).

    Args:
        request: Incoming HTTP request.

    Returns:
        TemplateResponse | RedirectResponse | HTMLResponse: Rendered security
            posture page or access denied error.
    """
    redirect = await _require_page_auth(request)
    if redirect:
        return redirect
    if getattr(request.state, "role", None) != "admin":
        return _render_error(
            request,
            403,
            "Access Denied",
            "You need admin privileges to view the security check.",
            icon="shield-lock",
        )
    return templates.TemplateResponse(
        request,
        "pages/security.html",
        {"active_page": "security"},
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
    redirect = await _require_page_auth(request)
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
    redirect = await _require_page_auth(request)
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
    redirect = await _require_page_auth(request)
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
    redirect = await _require_page_auth(request)
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
    redirect = await _require_page_auth(request)
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
