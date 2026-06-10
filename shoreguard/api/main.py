"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shoreguard.services.local_gateway import LocalGatewayManager

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text

from shoreguard import __build_time__, __git_sha__, __version__
from shoreguard.client import ShoreGuardClient
from shoreguard.exceptions import GatewayNotConnectedError

from .auth import (
    bootstrap_admin_user,
    init_auth,
    is_setup_complete,
    require_auth,
    require_role,
)
from .cli import _import_filesystem_gateways, cli  # noqa: F401 — cli re-exported for entry point
from .deps import get_client, resolve_gateway
from .errors import register_error_handlers
from .metrics import RequestIdFilter, metrics_middleware, shoreguard_info
from .metrics import router as metrics_router
from .pages import FRONTEND_DIR
from .pages import router as pages_router
from .routes import (
    approvals,
    audit,
    boot_hooks,
    bypass,
    gateway,
    operations,
    policies,
    prover,
    provider_profiles,
    providers,
    sandboxes,
    sbom,
    services,
    templates,
    tokens,
    webhooks,
)
from .schemas import (
    HealthResponse,
    InferenceBundleResponse,
    InferenceConfigResponse,
    VersionResponse,
)
from .security_headers import security_headers_middleware
from .websocket import router as ws_router

logger = logging.getLogger(__name__)


def _warn_if_docker_unusable(manager: LocalGatewayManager) -> None:
    """Log a boot-time warning when local mode is on but Docker is unusable.

    Runs ``manager.diagnostics()`` and, if Docker is not accessible, emits a
    single actionable warning so the failure surfaces at startup instead of as
    an opaque gRPC timeout the first time a solo dev creates a sandbox. Never
    raises — ShoreGuard still boots so the operator can read the message.

    Args:
        manager: The local gateway manager whose Docker diagnostics to check.
    """
    diag = manager.diagnostics()
    if not diag["docker_accessible"]:
        logger.warning(
            "Local mode is on but Docker is not usable (%s). Sandbox/gateway "
            "lifecycle will fail until Docker is running and accessible. "
            "See GET /api/gateways/diagnostics for details.",
            diag["docker_error"] or "Docker not installed",
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — initialise DB, services, and background tasks.

    Args:
        app: The FastAPI application instance.

    Yields:
        None: Control to the application while it is running.

    Raises:
        Exception: If database initialisation fails.
    """
    from sqlalchemy.orm import sessionmaker as sa_sessionmaker

    from shoreguard.settings import get_settings

    settings = get_settings()
    settings.enforce_production_safety()

    # Install request-ID log filter so %(request_id)s is available in all loggers.
    logging.getLogger().addFilter(RequestIdFilter())

    # ── Tracing (OTel) ──────────────────────────────────────────────────
    # M28 Observability Säule 2. Must happen before any gRPC channel is
    # constructed so the client instrumentor can patch channel creation.
    from .tracing import init_tracing, instrument_fastapi

    if init_tracing():
        instrument_fastapi(app)

    from shoreguard.container import build_container, install, uninstall
    from shoreguard.db import get_async_session_factory, init_async_db, init_db
    from shoreguard.services.audit_export import AuditExporter

    try:
        engine = init_db()
    except Exception:
        logger.exception("Failed to initialise database")
        raise
    session_factory = sa_sessionmaker(bind=engine)
    init_async_db(str(engine.url))

    # The exporter needs the running loop to schedule webhook dispatch
    # from the sync ``AuditService.log`` path, so it is built here rather
    # than inside ``build_container``.
    audit_exporter = AuditExporter(settings.audit, loop=asyncio.get_running_loop())
    container = build_container(
        settings,
        session_factory,
        get_async_session_factory(),
        audit_exporter=audit_exporter,
    )
    install(container)
    app.state.container = container
    logger.info(
        "Service container initialised (local_mode=%s, discovery=%s, drift=%s, cert_rotation=%s)",
        settings.server.local_mode,
        settings.discovery.enabled,
        settings.drift_detection.enabled,
        settings.cert_rotation.enabled,
    )

    if container.local_gateway is not None:
        # Surface Docker problems at boot instead of as an opaque gRPC timeout
        # later, when a solo dev first tries to create a sandbox. ``diagnostics``
        # shells out to ``docker``, so run it off the event loop.
        await asyncio.to_thread(_warn_if_docker_unusable, container.local_gateway)

        # Auto-import filesystem gateways so locally managed gateways
        # appear in the DB without a manual import-gateways step.
        imported, skipped = _import_filesystem_gateways(container.registry)
        if imported:
            logger.info("Auto-imported %d gateway(s) from filesystem", imported)

    orphaned = await container.operations.recover_orphans()
    if orphaned:
        logger.info("Recovered %d orphaned operations from previous run", orphaned)

    # ── Metrics ─────────────────────────────────────────────────────────
    shoreguard_info.info(
        {"version": __version__, "git_sha": __git_sha__, "build_time": __build_time__}
    )

    # ── Auth ─────────────────────────────────────────────────────────────
    init_auth(session_factory)
    bootstrap_admin_user()
    from shoreguard.api.oidc import init_oidc

    init_oidc()

    # Hide OpenAPI docs when authentication is enabled to avoid leaking
    # the full API schema to unauthenticated users.
    if is_setup_complete():
        app.openapi_url = None
        app.docs_url = None
        app.redoc_url = None

    # ── Background tasks ─────────────────────────────────────────────────
    from shoreguard.tasks import TaskSupervisor
    from shoreguard.tasks.definitions import build_tasks

    supervisor = TaskSupervisor()
    supervisor.start(build_tasks(container, settings))
    app.state.supervisor = supervisor

    yield

    # ── Graceful shutdown ──────────────────────────────────────────
    logger.info("Shutdown started")

    # 1. Cancel LRO tasks (CancelledError handler marks ops as failed)
    from shoreguard.api.lro import shutdown_lros

    lro_count = await shutdown_lros(timeout=10.0)
    if lro_count:
        logger.info("Cancelled %d LRO task(s)", lro_count)

    # 2. Cancel in-flight webhook deliveries
    wh_count = await container.webhooks.shutdown(timeout=3.0)
    if wh_count:
        logger.info("Cancelled %d webhook delivery task(s)", wh_count)

    # 3. Stop background polling with a hard deadline so a task that
    #    swallows CancelledError cannot block shutdown forever.
    await supervisor.shutdown(timeout=float(settings.server.graceful_shutdown_timeout))
    app.state.supervisor = None

    # 4. Dispose DB engines and drop the container
    uninstall()
    engine.dispose()
    from shoreguard.db import dispose_async_engine

    await dispose_async_engine()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Shoreguard",
    description="Open source control plane for NVIDIA OpenShell",
    version=__version__,
    lifespan=lifespan,
    openapi_tags=[
        {"name": "health", "description": "Liveness and readiness probes"},
        {"name": "sandboxes", "description": "Manage sandboxes within a gateway"},
        {
            "name": "policies",
            "description": "Gateway-scoped policy management (network rules, filesystem, presets)",
        },
        {"name": "policies-global", "description": "Global policy presets (not gateway-scoped)"},
        {"name": "approvals", "description": "Draft policy approval workflow"},
        {"name": "providers", "description": "Inference provider CRUD"},
        {
            "name": "provider-profiles",
            "description": "Reusable provider-type profile registry (M37 / OpenShell PR #1170)",
        },
        {"name": "gateway", "description": "Gateway registration, lifecycle, and diagnostics"},
        {"name": "operations", "description": "Long-running operation tracking and polling"},
        {"name": "audit", "description": "Audit log queries and export (admin only)"},
        {"name": "webhooks", "description": "Webhook subscription management (admin only)"},
        {"name": "templates", "description": "Sandbox template listing"},
    ],
)

register_error_handlers(app)


# ─── Health probes (unauthenticated) ────────────────────────────────────────

health_router = APIRouter(tags=["health"])


@health_router.get("/healthz", response_model=HealthResponse)
async def healthz() -> dict[str, str]:
    """Liveness probe — returns 200 if the process is running.

    Returns:
        dict[str, str]: Status object with ``{"status": "ok"}``.
    """
    return {"status": "ok"}


@health_router.get("/version", response_model=VersionResponse)
async def version_info() -> dict[str, str]:
    """Report version, git SHA, and build time of the running binary.

    Used after deploys to verify which artifact is actually serving
    traffic. Git SHA and build time are populated by Dockerfile ARGs
    at CI build time; local runs return ``"unknown"`` for both.

    Returns:
        dict[str, str]: ``{"version": ..., "git_sha": ..., "build_time": ...}``.
    """
    return {
        "version": __version__,
        "git_sha": __git_sha__,
        "build_time": __build_time__,
    }


@health_router.get("/readyz")
async def readyz(request: Request, verbose: bool = False) -> JSONResponse:
    """Readiness probe — checks database connectivity and gateway health.

    Args:
        request: Incoming HTTP request (for app state access).
        verbose: If True, include per-gateway breakdown.

    Returns:
        JSONResponse: 200 with check details when ready, 503 otherwise.
    """
    from shoreguard.container import try_get_container
    from shoreguard.db import get_engine
    from shoreguard.settings import get_settings

    readyz_timeout = get_settings().server.readyz_timeout
    checks: dict[str, Any] = {}
    healthy = True

    # ── Database ──────────────────────────────────────────────────
    try:
        engine = get_engine()
        t0 = time.monotonic()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_latency_ms = round((time.monotonic() - t0) * 1000, 1)
        checks["database"] = "ok"
        checks["database_latency_ms"] = db_latency_ms
    except Exception as exc:
        logger.warning("Health check: database unreachable: %s", exc)
        checks["database"] = str(exc)
        healthy = False

    # ── Gateway service ───────────────────────────────────────────
    container = try_get_container()
    if container is not None:
        checks["gateway_service"] = "ok"
        try:
            gateways = await asyncio.wait_for(
                asyncio.to_thread(container.registry.list_all),
                timeout=readyz_timeout,
            )
            total = len(gateways)
            connected = sum(1 for g in gateways if g.get("connected"))
            checks["gateways_total"] = total
            checks["gateways_connected"] = connected
            if total > 0 and connected < total:
                checks["gateways_degraded"] = True
            if verbose:
                checks["gateways"] = [
                    {
                        "name": g["name"],
                        "status": g.get("last_status", "unknown"),
                        "last_seen": g.get("last_seen"),
                        "connected": g.get("connected", False),
                    }
                    for g in gateways
                ]
        except TimeoutError:
            logger.warning("Health check: gateway registry timed out after %.1fs", readyz_timeout)
            checks["gateway_registry"] = f"timeout after {readyz_timeout}s"
            healthy = False
        except Exception:
            logger.debug("Health check: failed to query gateway list", exc_info=True)
    else:
        checks["gateway_service"] = "not initialised"
        healthy = False

    # ── Background task supervision ───────────────────────────────
    supervisor = getattr(request.app.state, "supervisor", None)
    if supervisor is not None:
        for name, state in supervisor.health_snapshot().items():
            if not state["alive"]:
                checks[f"background_{name}"] = "dead"
                healthy = False
                continue
            checks[f"background_{name}"] = "ok"
            if state["age_s"] is not None:
                checks[f"background_{name}_age_s"] = state["age_s"]
            if state["stalled"]:
                checks[f"background_{name}"] = "stalled"
                checks[f"background_{name}_stalled"] = True

    status_code = 200 if healthy else 503
    payload = {"status": "ready" if healthy else "not ready", "checks": checks}
    return JSONResponse(content=payload, status_code=status_code)


app.include_router(health_router)
app.include_router(metrics_router)
app.middleware("http")(metrics_middleware)
app.middleware("http")(security_headers_middleware)


# ─── Global rate limit middleware ───────────────────────────────────────────
_RATE_LIMIT_SKIP_PATHS = frozenset({"/healthz", "/readyz", "/metrics", "/version"})


@app.middleware("http")
async def global_rate_limit_middleware(request: Request, call_next: Any) -> Any:
    """Coarse per-IP rate limit applied to every HTTP request.

    Health and metrics endpoints are exempt so that probes and scrapers
    can never be blocked.  Applied in addition to login/write limiters.

    Args:
        request: The incoming HTTP request.
        call_next: The next ASGI handler in the middleware chain.

    Returns:
        Any: A 429 response when rate-limited, otherwise the downstream response.
    """
    path = request.url.path
    if path in _RATE_LIMIT_SKIP_PATHS:
        return await call_next(request)

    from shoreguard.api.ratelimit import get_global_limiter

    client_ip = request.client.host if request.client else "unknown"
    limiter = get_global_limiter()
    blocked, retry_after = limiter.is_limited(client_ip)
    if blocked:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests"},
            headers={"Retry-After": str(retry_after)},
        )
    limiter.record(client_ip)
    return await call_next(request)


# ─── Request body size limit middleware ─────────────────────────────────────
@app.middleware("http")
async def body_size_limit_middleware(request: Request, call_next: Any) -> Any:
    """Reject requests whose Content-Length exceeds the configured limit.

    Note: only honours the ``Content-Length`` header — chunked uploads
    without a length header are forwarded unchanged and bounded by the
    individual endpoint's Pydantic field limits.

    Args:
        request: The incoming HTTP request.
        call_next: The next ASGI handler in the middleware chain.

    Returns:
        Any: A 400/413 response when the body is invalid or too large, otherwise the
            downstream response.
    """
    from shoreguard.settings import get_settings as _gs

    max_bytes = _gs().limits.max_request_body_bytes
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            length = int(cl)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"detail": "Invalid Content-Length header"},
            )
        if length > max_bytes:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Request body too large (limit {max_bytes} bytes)"},
                headers={"Connection": "close"},
            )
    return await call_next(request)


# GZip compression for responses >= 1 KB (SSE streams and WebSockets unaffected).
from starlette.middleware.gzip import GZipMiddleware  # noqa: E402

app.add_middleware(GZipMiddleware, minimum_size=1000)

# CORS — off by default. Enable by setting SHOREGUARD_CORS_ALLOW_ORIGINS.
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from shoreguard.settings import get_settings as _get_settings_for_cors  # noqa: E402

_cors_cfg = _get_settings_for_cors().cors
if _cors_cfg.allow_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_cfg.allow_origins,
        allow_credentials=_cors_cfg.allow_credentials,
        allow_methods=_cors_cfg.allow_methods,
        allow_headers=_cors_cfg.allow_headers,
        max_age=_cors_cfg.max_age,
    )


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
gw_api.include_router(bypass.router, prefix="/sandboxes", tags=["bypass"])
gw_api.include_router(prover.router, prefix="/sandboxes", tags=["prover"])
gw_api.include_router(sbom.router, prefix="/sandboxes", tags=["sbom"])
gw_api.include_router(boot_hooks.router, prefix="/sandboxes", tags=["boot_hooks"])
gw_api.include_router(providers.router, prefix="/providers", tags=["providers"])
gw_api.include_router(
    provider_profiles.router, prefix="/provider-profiles", tags=["provider-profiles"]
)
gw_api.include_router(services.router, prefix="/services", tags=["services"])
gw_api.include_router(tokens.router, prefix="/tokens", tags=["tokens"])


@gw_api.get("/health", response_model=None)
async def gw_health(gw: str) -> dict[str, Any] | JSONResponse:
    """Return gateway health status.

    Args:
        gw: The gateway name.

    Returns:
        dict[str, Any] | JSONResponse: Health info or 503 if disconnected.
    """
    from .deps import _get_gateway_service

    try:
        client = _get_gateway_service().get_client(name=gw)
        return await asyncio.to_thread(client.health)
    except GatewayNotConnectedError:
        return JSONResponse(
            status_code=503,
            content={"status": "disconnected", "detail": f"Gateway '{gw}' not connected"},
        )


class SetInferenceRequest(BaseModel):
    """Request body for setting cluster inference configuration.

    Attributes:
        provider_name: Name of the inference provider.
        model_id: Identifier of the model to use.
        verify: Whether to verify the configuration before applying.
        timeout_secs: Per-route request timeout in seconds (0 = default 60s).
        route_name: Named inference route (empty for default cluster route).
    """

    provider_name: str = Field(min_length=1, max_length=253)
    model_id: str = Field(min_length=1, max_length=253)
    verify: bool = True
    timeout_secs: int = Field(default=0, ge=0, le=3600)
    route_name: str = Field(default="", max_length=253)


@gw_api.get("/inference", response_model=InferenceConfigResponse)
async def get_inference(
    gw: str,
    route_name: str = "",
    client: ShoreGuardClient = Depends(get_client),
) -> dict[str, Any]:
    """Return current cluster inference configuration.

    Args:
        gw: The gateway name.
        route_name: Named inference route to query. Empty string returns
            the default cluster route. ``sandbox-system`` returns the
            route used for sandbox system-level model calls (OpenShell
            v0.0.25+).
        client: The ShoreGuardClient for this gateway.

    Returns:
        dict[str, Any]: Current inference provider and model settings.
    """
    return await asyncio.to_thread(client.get_cluster_inference, route_name=route_name)


@gw_api.get("/inference/bundle", response_model=InferenceBundleResponse)
async def get_inference_bundle(
    gw: str,
    request: Request,
    client: ShoreGuardClient = Depends(get_client),
) -> dict[str, Any]:
    """Return the resolved inference bundle for this gateway.

    The bundle exposes the routes the gateway is currently serving
    after policy overlay — the cluster default plus every named
    route. API keys are redacted at the client-wrapper boundary:
    each route carries ``has_api_key`` (bool) instead of the secret
    value, so this endpoint can be read by non-admin operators
    without exposing credentials.

    Args:
        gw: The gateway name.
        request: The incoming HTTP request (for audit logging).
        client: The ShoreGuardClient for this gateway.

    Returns:
        dict[str, Any]: Bundle with revision, timestamp, and routes.
    """
    result = await asyncio.to_thread(client.get_inference_bundle)
    from shoreguard.services.audit import audit_log

    await audit_log(
        request,
        "gateway.inference_bundle.viewed",
        "inference_bundle",
        gw,
        gateway=gw,
    )
    return result


@gw_api.put(
    "/inference",
    response_model=InferenceConfigResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def set_inference(
    gw: str,
    body: SetInferenceRequest,
    request: Request,
    client: ShoreGuardClient = Depends(get_client),
) -> dict[str, Any]:
    """Update cluster inference configuration.

    Args:
        gw: The gateway name.
        body: The inference configuration to apply.
        request: The incoming HTTP request (for audit logging).
        client: The ShoreGuardClient for this gateway.

    Returns:
        dict[str, Any]: Updated inference configuration.
    """
    actor = getattr(request.state, "user_id", "unknown")
    logger.info(
        "Inference config updated (gateway=%s, provider=%s, model=%s, actor=%s)",
        gw,
        body.provider_name,
        body.model_id,
        actor,
    )
    result = await asyncio.to_thread(
        client.set_cluster_inference,
        provider_name=body.provider_name,
        model_id=body.model_id,
        verify=body.verify,
        timeout_secs=body.timeout_secs,
        route_name=body.route_name,
    )
    from shoreguard.services.audit import audit_log
    from shoreguard.services.webhooks import fire_webhook

    await audit_log(
        request,
        "inference.update",
        "inference",
        gw,
        gateway=gw,
        detail={"provider": body.provider_name, "model": body.model_id},
    )
    await fire_webhook(
        "inference.updated",
        {
            "gateway": gw,
            "provider": body.provider_name,
            "model": body.model_id,
            "actor": actor,
        },
    )
    return result


app.include_router(gw_api)


# ─── Global API routes (not gateway-scoped) ─────────────────────────────────

app.include_router(
    gateway.router,
    prefix="/api/gateway",
    tags=["gateway"],
    dependencies=[Depends(require_auth)],
)

# Presets are local YAML files, not gateway-scoped — mount only preset
# routes globally.  The sandbox-scoped policy routes (/sandboxes/{name}/policy/*)
# are already mounted under gw_api and must NOT be duplicated at the global level.
app.include_router(
    policies.preset_router,
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

app.include_router(
    audit.router,
    prefix="/api/audit",
    tags=["audit"],
    dependencies=[Depends(require_auth), Depends(require_role("admin"))],
)

app.include_router(
    webhooks.router,
    prefix="/api/webhooks",
    tags=["webhooks"],
    dependencies=[Depends(require_auth), Depends(require_role("admin"))],
)

app.include_router(
    templates.router,
    prefix="/api/sandbox-templates",
    tags=["templates"],
    dependencies=[Depends(require_auth)],
)


# ─── WebSocket, pages, and static files ─────────────────────────────────────

app.include_router(ws_router)
app.include_router(pages_router)


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles that asks browsers to revalidate on every request."""

    async def get_response(self, path: str, scope: Any) -> Any:
        """Serve the static asset with a no-cache directive.

        Args:
            path: Filesystem-relative path of the requested asset.
            scope: ASGI scope for the current request.

        Returns:
            Any: The underlying StaticFiles response with a ``Cache-Control``
            ``no-cache`` header applied.
        """
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Serve static files (CSS, JS, images)
app.mount("/static", NoCacheStaticFiles(directory=str(FRONTEND_DIR)), name="static")


if __name__ == "__main__":
    cli()
