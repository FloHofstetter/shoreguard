"""FastAPI application entry point."""

import asyncio
import logging
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
    GatewayNotConnectedError,
    NotFoundError,
    PolicyError,
    SandboxError,
    ShoreGuardError,
    friendly_grpc_error,
)

from .deps import get_client, resolve_gateway
from .routes import approvals, gateway, policies, providers, sandboxes

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
    """Application lifespan — no eager connection needed with multi-gateway."""
    yield


app = FastAPI(
    title="Shoreguard",
    description="Open source control plane for NVIDIA OpenShell",
    version="0.1.0",
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
}


_DOMAIN_STATUS_MAP: dict[type, int] = {
    GatewayNotConnectedError: 503,
    NotFoundError: 404,
    PolicyError: 500,
    SandboxError: 500,
}


@app.exception_handler(ShoreGuardError)
async def shoreguard_error_handler(request: Request, exc: ShoreGuardError):
    """Return the appropriate HTTP status for domain errors."""
    status = _DOMAIN_STATUS_MAP.get(type(exc), 500)
    return JSONResponse(status_code=status, content={"detail": str(exc)})


@app.exception_handler(TimeoutError)
async def timeout_error_handler(request: Request, exc: TimeoutError):
    """Return 504 for timeout errors."""
    return JSONResponse(status_code=504, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Catch gRPC errors and return proper HTTP responses."""
    if isinstance(exc, grpc.RpcError):
        code = exc.code() if hasattr(exc, "code") else None
        detail = friendly_grpc_error(exc)
        http_status = _GRPC_STATUS_MAP.get(code, 500) if code is not None else 500
        return JSONResponse(status_code=http_status, content={"detail": detail})
    raise exc


# ─── Gateway-scoped API routes ──────────────────────────────────────────────
# All sandbox/policy/provider operations are scoped to a specific gateway.
# The resolve_gateway dependency sets a ContextVar so get_client() returns
# the correct client — route handlers need zero changes.

gw_api = APIRouter(
    prefix="/api/gateways/{gw}",
    dependencies=[Depends(resolve_gateway)],
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

app.include_router(gateway.router, prefix="/api/gateway", tags=["gateway"])

# Presets are local YAML files, not gateway-scoped — mount globally too
app.include_router(policies.router, prefix="/api", tags=["policies-global"])


# ─── WebSocket (gateway-scoped) ─────────────────────────────────────────────


@app.websocket("/ws/{gw}/{sandbox_name}")
async def sandbox_events(websocket: WebSocket, gw: str, sandbox_name: str):
    """Stream live sandbox events over WebSocket."""
    await websocket.accept()
    from .deps import _current_gateway

    _current_gateway.set(gw)
    try:
        client = get_client()
    except GatewayNotConnectedError:
        await websocket.send_json(
            {"type": "error", "data": {"message": f"Gateway '{gw}' not connected"}}
        )
        return

    try:
        sandbox = await asyncio.to_thread(client.sandboxes.get, sandbox_name)
        sandbox_id = sandbox["id"]

        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        cancel_event = asyncio.Event()

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
                        queue.put_nowait(event)
                except grpc.RpcError as exc:
                    if cancel_event.is_set():
                        return
                    detail = exc.details() if hasattr(exc, "details") else str(exc)
                    logger.warning("WatchSandbox stream error for %s: %s", sandbox_name, detail)
                    queue.put_nowait(
                        {"type": "error", "data": {"message": f"Stream error: {detail}"}}
                    )
                finally:
                    queue.put_nowait(None)

            await asyncio.to_thread(_iter_watch)

        producer_task = asyncio.create_task(_producer())

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                await websocket.send_json(event)
        finally:
            cancel_event.set()
            producer_task.cancel()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "data": {"message": str(e)}})
        except (WebSocketDisconnect, RuntimeError):
            pass


# ─── Page routes ─────────────────────────────────────────────────────────────


def _openshell_meta():
    """Lazy import to avoid circular deps at module level."""
    from shoreguard.services._openshell_meta import get_openshell_meta

    return get_openshell_meta()


def _gw_ctx(gw: str, **extra: object) -> dict:
    """Common template context for gateway-scoped pages."""
    return {"active_page": "sandboxes", "gateway_name": gw, **extra}


# ── Global pages ─────────────────────────────────────────────────────────────


@app.get("/")
async def dashboard_redirect():
    """Redirect root to gateways list."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/gateways", status_code=302)


@app.get("/gateways")
async def gateways_page(request: Request):
    """Gateway list page."""
    return templates.TemplateResponse(
        request,
        "pages/gateways.html",
        {"active_page": "gateways"},
    )


@app.get("/gateways/{name:path}")
async def gateway_detail_or_sub(request: Request, name: str):
    """Gateway detail page or gateway-scoped sub-pages.

    Matches:
      /gateways/mygateway                          → gateway detail
      /gateways/mygateway/sandboxes                → sandbox list
      /gateways/mygateway/sandboxes/foo             → sandbox detail
      /gateways/mygateway/sandboxes/foo/policy      → sandbox policy
      etc.
    """
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
    return templates.TemplateResponse(
        request,
        "pages/policies.html",
        {"active_page": "policies"},
    )


@app.get("/policies/{name}")
async def preset_detail_page(request: Request, name: str):
    """Preset detail page (global)."""
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
