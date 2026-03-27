"""FastAPI application entry point."""

import asyncio
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import grpc
import typer
from fastapi import APIRouter, Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from shoreguard.client import ShoreGuardClient
from shoreguard.exceptions import (
    FeatureNotAvailableError,
    GatewayNotConnectedError,
    NotFoundError,
    PolicyError,
    SandboxError,
    ShoreGuardError,
    ValidationError,
    friendly_grpc_error,
)

from .auth import (
    COOKIE_NAME,
    check_api_key,
    check_request_auth,
    create_session_token,
    is_auth_enabled,
    require_auth,
    require_auth_ws,
)
from .auth import (
    configure as configure_auth,
)
from .deps import get_client, resolve_gateway
from .routes import approvals, gateway, operations, policies, providers, sandboxes

logger = logging.getLogger("shoreguard")


def _resolve_frontend_dir() -> Path:
    """Resolve the frontend directory for both installed and dev-checkout modes."""
    # Installed via pip: shoreguard/_frontend/ (sibling to shoreguard/api/)
    pkg_dir = Path(__file__).resolve().parent.parent / "_frontend"
    if pkg_dir.is_dir():
        return pkg_dir
    # Dev checkout: repo_root/frontend/
    dev_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
    if dev_dir.is_dir():
        return dev_dir
    raise FileNotFoundError(
        "Frontend directory not found. Reinstall shoreguard or run from the repository root."
    )


_FRONTEND_DIR = _resolve_frontend_dir()
_TEMPLATES_DIR = _FRONTEND_DIR / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — configure auth from env if not already set via CLI."""
    import os

    if not is_auth_enabled():
        env_key = os.environ.get("SHOREGUARD_API_KEY")
        if env_key:
            configure_auth(env_key)
            logger.info("API-key authentication enabled (from env)")

    # Hide OpenAPI docs when authentication is enabled to avoid leaking
    # the full API schema to unauthenticated users.
    if is_auth_enabled():
        app.openapi_url = None
        app.docs_url = None
        app.redoc_url = None

    async def _cleanup_operations() -> None:
        from shoreguard.services.operations import operation_store

        while True:
            await asyncio.sleep(600)
            try:
                operation_store.cleanup()
            except Exception:
                logger.exception("Operation cleanup failed")

    cleanup_task = asyncio.create_task(_cleanup_operations())
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Shoreguard",
    description="Open source control plane for NVIDIA OpenShell",
    version="0.2.0",
    lifespan=lifespan,
)

_GRPC_STATUS_MAP = {
    grpc.StatusCode.INVALID_ARGUMENT: 400,
    grpc.StatusCode.NOT_FOUND: 404,
    grpc.StatusCode.ALREADY_EXISTS: 409,
    grpc.StatusCode.PERMISSION_DENIED: 403,
    grpc.StatusCode.UNAUTHENTICATED: 401,
    grpc.StatusCode.UNAVAILABLE: 503,
    grpc.StatusCode.UNIMPLEMENTED: 501,
    grpc.StatusCode.DEADLINE_EXCEEDED: 504,
}


_DOMAIN_STATUS_MAP: dict[type, int] = {
    GatewayNotConnectedError: 503,
    NotFoundError: 404,
    PolicyError: 400,
    SandboxError: 409,
    ValidationError: 400,
    FeatureNotAvailableError: 501,
}


@app.exception_handler(ShoreGuardError)
async def shoreguard_error_handler(request: Request, exc: ShoreGuardError):
    """Return the appropriate HTTP status for domain errors."""
    status = _DOMAIN_STATUS_MAP.get(type(exc), 500)
    if status >= 500:
        logger.error("Unhandled domain error: %s (status=%d)", exc, status, exc_info=True)
    else:
        logger.warning("Domain error: %s (status=%d)", exc, status)
    return JSONResponse(status_code=status, content={"detail": str(exc)})


@app.exception_handler(TimeoutError)
async def timeout_error_handler(request: Request, exc: TimeoutError):
    """Return 504 for timeout errors."""
    logger.warning("Timeout on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=504, content={"detail": str(exc)})


def _detect_feature_from_path(path: str) -> str:
    """Extract a human-readable feature name from the request URL path."""
    if "/policy" in path:
        return "Sandbox policy management"
    if "/approvals" in path:
        return "Policy approval workflow"
    if "/inference" in path:
        return "Inference routing"
    return "This operation"


@app.exception_handler(grpc.RpcError)
async def grpc_exception_handler(request: Request, exc: grpc.RpcError):
    """Catch gRPC errors and return proper HTTP responses."""
    code = exc.code() if hasattr(exc, "code") else None
    logger.warning(
        "gRPC error on %s (code=%s): %s",
        request.url.path,
        code,
        friendly_grpc_error(exc),
    )
    if code == grpc.StatusCode.UNIMPLEMENTED:
        feature = _detect_feature_from_path(request.url.path)
        detail = (
            f"{feature} is not supported by the current OpenShell gateway version. "
            f"This feature requires a newer gateway."
        )
        return JSONResponse(
            status_code=501,
            content={"detail": detail, "feature": feature, "upgrade_required": True},
        )
    detail = friendly_grpc_error(exc)
    http_status = _GRPC_STATUS_MAP.get(code, 500) if code is not None else 500
    return JSONResponse(status_code=http_status, content={"detail": detail})


# ─── Gateway-scoped API routes ──────────────────────────────────────────────
# All sandbox/policy/provider operations are scoped to a specific gateway.
# The resolve_gateway dependency sets a ContextVar so get_client() returns
# the correct client — route handlers need zero changes.

gw_api = APIRouter(
    prefix="/api/gateways/{gw}",
    dependencies=[Depends(resolve_gateway), Depends(require_auth)],
)
gw_api.include_router(sandboxes.router, prefix="/sandboxes", tags=["sandboxes"])
gw_api.include_router(policies.router, tags=["policies"])
gw_api.include_router(approvals.router, prefix="/sandboxes", tags=["approvals"])
gw_api.include_router(providers.router, prefix="/providers", tags=["providers"])


@gw_api.get("/health")
async def gw_health(gw: str, client: ShoreGuardClient = Depends(get_client)):
    """Return gateway health status."""
    try:
        return await asyncio.to_thread(client.health)
    except GatewayNotConnectedError:
        return JSONResponse(
            status_code=503,
            content={"status": "disconnected", "detail": f"Gateway '{gw}' not connected"},
        )


class SetInferenceRequest(BaseModel):
    """Request body for setting cluster inference configuration."""

    provider_name: str
    model_id: str
    verify: bool = True


@gw_api.get("/inference")
async def get_inference(gw: str, client: ShoreGuardClient = Depends(get_client)):
    """Return current cluster inference configuration."""
    return await asyncio.to_thread(client.get_cluster_inference)


@gw_api.put("/inference")
async def set_inference(
    gw: str,
    body: SetInferenceRequest,
    client: ShoreGuardClient = Depends(get_client),
):
    """Update cluster inference configuration."""
    return await asyncio.to_thread(
        client.set_cluster_inference,
        provider_name=body.provider_name,
        model_id=body.model_id,
        verify=body.verify,
    )


app.include_router(gw_api)


# ─── Global API routes (not gateway-scoped) ─────────────────────────────────

app.include_router(
    gateway.router,
    prefix="/api/gateway",
    tags=["gateway"],
    dependencies=[Depends(require_auth)],
)

# Presets are local YAML files, not gateway-scoped — mount globally too
app.include_router(
    policies.router,
    prefix="/api",
    tags=["policies-global"],
    dependencies=[Depends(require_auth)],
)

app.include_router(
    operations.router,
    prefix="/api/operations",
    tags=["operations"],
    dependencies=[Depends(require_auth)],
)


# ─── WebSocket (gateway-scoped) ─────────────────────────────────────────────


@app.websocket("/ws/{gw}/{sandbox_name}")
async def sandbox_events(
    websocket: WebSocket,
    gw: str,
    sandbox_name: str,
    _auth: None = Depends(require_auth_ws),
):
    """Stream live sandbox events over WebSocket."""
    try:
        await websocket.accept()
    except RuntimeError:
        logger.debug("WebSocket closed before accept: %s/%s", gw, sandbox_name)
        return
    from .deps import _current_gateway

    _current_gateway.set(gw)
    try:
        client = await asyncio.to_thread(get_client)
    except GatewayNotConnectedError:
        await websocket.send_json(
            {"type": "error", "data": {"message": f"Gateway '{gw}' not connected"}}
        )
        return

    try:
        sandbox = await asyncio.to_thread(client.sandboxes.get, sandbox_name)
        sandbox_id = sandbox["id"]

        queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=1000)
        cancel_event = threading.Event()

        async def _producer():
            def _iter_watch():
                try:
                    for event in client.sandboxes.watch(
                        sandbox_id,
                        follow_status=True,
                        follow_logs=True,
                        follow_events=True,
                    ):
                        if cancel_event.is_set():
                            break
                        try:
                            queue.put_nowait(event)
                        except asyncio.QueueFull:
                            logger.warning(
                                "WebSocket event queue full for %s, dropping event",
                                sandbox_name,
                            )
                except grpc.RpcError as exc:
                    if cancel_event.is_set():
                        return
                    detail = exc.details() if hasattr(exc, "details") else str(exc)
                    logger.warning("WatchSandbox stream error for %s: %s", sandbox_name, detail)
                    try:
                        queue.put_nowait(
                            {"type": "error", "data": {"message": f"Stream error: {detail}"}}
                        )
                    except asyncio.QueueFull:
                        pass
                finally:
                    try:
                        queue.put_nowait(None)
                    except asyncio.QueueFull:
                        logger.warning(
                            "Could not send sentinel for %s, setting cancel event",
                            sandbox_name,
                        )
                        cancel_event.set()

            await asyncio.to_thread(_iter_watch)

        producer_task = asyncio.create_task(_producer())

        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    if cancel_event.is_set():
                        break
                    continue
                if event is None:
                    break
                await websocket.send_json(event)
        finally:
            cancel_event.set()
            producer_task.cancel()
            try:
                await producer_task
            except asyncio.CancelledError:
                pass

    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected: %s/%s", gw, sandbox_name)
    except grpc.RpcError as e:
        code = e.code() if hasattr(e, "code") else None
        if code == grpc.StatusCode.NOT_FOUND:
            msg = f"Sandbox '{sandbox_name}' not found"
        else:
            msg = friendly_grpc_error(e)
        logger.error("WebSocket gRPC error for %s/%s: %s", gw, sandbox_name, msg, exc_info=True)
        try:
            await websocket.send_json({"type": "error", "data": {"message": msg}})
        except (WebSocketDisconnect, RuntimeError):
            pass
    except Exception as e:
        logger.error("WebSocket error for %s/%s: %s", gw, sandbox_name, e, exc_info=True)
        try:
            await websocket.send_json({"type": "error", "data": {"message": "Internal error"}})
        except (WebSocketDisconnect, RuntimeError):
            pass


# ─── Auth endpoints ──────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    """Request body for the login endpoint."""

    key: str


@app.post("/api/auth/login")
async def login(request: Request, body: LoginRequest):
    """Validate the API key and set a session cookie."""
    if not is_auth_enabled():
        return JSONResponse(
            status_code=400,
            content={"detail": "Authentication is not enabled"},
        )
    if not check_api_key(body.key):
        client_ip = request.client.host if request.client else "unknown"
        logger.warning("Login failed: invalid API key (client=%s)", client_ip)
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid API key"},
        )
    logger.info("Login successful")
    token = create_session_token()
    response = JSONResponse(content={"ok": True})
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


@app.post("/api/auth/logout")
async def logout():
    """Clear the session cookie."""
    logger.debug("Logout")
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.get("/api/auth/check")
async def auth_check(request: Request):
    """Return whether the current request is authenticated.

    Used by the frontend to decide whether to show the login page.
    """
    if not is_auth_enabled():
        return {"authenticated": True, "auth_enabled": False}

    authenticated = check_request_auth(request)
    return {"authenticated": authenticated, "auth_enabled": True}


# ─── Page routes ─────────────────────────────────────────────────────────────


def _openshell_meta():
    """Lazy import to avoid circular deps at module level."""
    from shoreguard.services._openshell_meta import get_openshell_meta

    return get_openshell_meta()


def _gw_ctx(gw: str, **extra: object) -> dict:
    """Common template context for gateway-scoped pages."""
    return {"active_page": "sandboxes", "gateway_name": gw, **extra}


# ── Global pages ─────────────────────────────────────────────────────────────


@app.get("/login")
async def login_page(request: Request):
    """Serve the login page."""
    return templates.TemplateResponse(request, "pages/login.html", {})


def _require_page_auth(request: Request):
    """Redirect to /login if auth is enabled and the request has no valid session."""
    if not is_auth_enabled():
        return None
    if check_request_auth(request):
        return None
    from urllib.parse import quote

    from fastapi.responses import RedirectResponse

    return RedirectResponse(url=f"/login?next={quote(request.url.path)}", status_code=302)


@app.get("/")
async def dashboard_redirect(request: Request):
    """Redirect root to gateways list."""
    redirect = _require_page_auth(request)
    if redirect:
        return redirect
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/gateways", status_code=302)


@app.get("/gateways")
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


@app.get("/gateways/{name:path}")
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


@app.get("/policies")
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


@app.get("/policies/{name}")
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


# Serve static files (CSS, JS, images)
app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIR)), name="static")


cli = typer.Typer(
    name="shoreguard",
    help=(
        "Web control plane for NVIDIA OpenShell.\n\n"
        "Launch the Shoreguard dashboard to manage sandboxes, security policies, "
        "and approval flows through your browser.\n\n"
        "Connects to your active OpenShell gateway automatically "
        "via ~/.config/openshell/active_gateway."
    ),
    no_args_is_help=False,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    """Print version and exit when --version is passed."""
    if value:
        from shoreguard import __version__

        typer.echo(f"shoreguard {__version__}")
        raise typer.Exit


@cli.callback(invoke_without_command=True)
def main(
    host: Annotated[
        str,
        typer.Option(
            envvar="SHOREGUARD_HOST",
            help="Network interface to listen on. Use 127.0.0.1 for localhost only.",
            rich_help_panel="Server",
        ),
    ] = "0.0.0.0",
    port: Annotated[
        int,
        typer.Option(
            envvar="SHOREGUARD_PORT",
            help="HTTP port for the dashboard and REST API (/docs for Swagger UI).",
            rich_help_panel="Server",
        ),
    ] = 8888,
    log_level: Annotated[
        str,
        typer.Option(
            "--log-level",
            envvar="SHOREGUARD_LOG_LEVEL",
            help="Verbosity for Shoreguard and Uvicorn. Use 'debug' to troubleshoot.",
            rich_help_panel="Server",
        ),
    ] = "info",
    api_key: Annotated[
        str | None,
        typer.Option(
            "--api-key",
            envvar="SHOREGUARD_API_KEY",
            help="Shared API key for authentication. All API and UI access requires this key.",
            rich_help_panel="Security",
        ),
    ] = None,
    reload: Annotated[
        bool,
        typer.Option(
            "--reload/--no-reload",
            envvar="SHOREGUARD_RELOAD",
            help="Auto-reload on code changes. Disable with --no-reload for production.",
            rich_help_panel="Development",
        ),
    ] = True,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = None,
) -> None:
    """Start the Shoreguard server."""
    import uvicorn

    logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO))

    configure_auth(api_key)
    if api_key:
        logger.info("API-key authentication enabled")
    else:
        logger.info("No API key set — authentication disabled")

    uvicorn.run(
        "shoreguard.api.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    cli()
