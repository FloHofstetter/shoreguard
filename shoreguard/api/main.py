"""FastAPI application entry point."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

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
    bypass,
    gateway,
    operations,
    policies,
    prover,
    providers,
    sandboxes,
    templates,
    webhooks,
)
from .schemas import HealthResponse, InferenceConfigResponse, VersionResponse
from .security_headers import security_headers_middleware
from .websocket import router as ws_router

logger = logging.getLogger(__name__)

# Supervision state for long-running background tasks. Keys match the
# ``_cleanup_operations`` / ``_health_monitor`` task names.  Read by
# ``/readyz`` to surface dead or stalled workers.
_task_health: dict[str, dict[str, Any]] = {
    "cleanup": {"last_success": None, "consecutive_failures": 0, "alive": False},
    "health_monitor": {"last_success": None, "consecutive_failures": 0, "alive": False},
}


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

    import shoreguard.services.gateway as gw_mod
    from shoreguard.db import init_db
    from shoreguard.services.registry import GatewayRegistry

    try:
        engine = init_db()
    except Exception:
        logger.exception("Failed to initialise database")
        raise
    session_factory = sa_sessionmaker(bind=engine)
    registry = GatewayRegistry(session_factory)
    gw_mod.gateway_service = gw_mod.GatewayService(registry)
    logger.info("Gateway service initialised")

    if settings.server.local_mode:
        import shoreguard.services.local_gateway as local_mod

        local_mod.local_gateway_manager = local_mod.LocalGatewayManager(gw_mod.gateway_service)
        logger.info("Local gateway mode enabled")

        # Auto-import filesystem gateways so locally managed gateways
        # appear in the DB without a manual import-gateways step.
        imported, skipped = _import_filesystem_gateways(registry)
        if imported:
            logger.info("Auto-imported %d gateway(s) from filesystem", imported)

    # ── Sandbox metadata ───────────────────────────────────────────────
    import shoreguard.services.sandbox_meta as sandbox_meta_mod

    sandbox_meta_mod.sandbox_meta_store = sandbox_meta_mod.SandboxMetaStore(session_factory)
    logger.info("Sandbox metadata store initialised")

    # ── Operations ──────────────────────────────────────────────────────
    import shoreguard.services.operations as ops_mod
    from shoreguard.db import get_async_session_factory, init_async_db

    init_async_db(str(engine.url))
    async_sf = get_async_session_factory()
    ops_mod.operation_service = ops_mod.AsyncOperationService(
        async_sf,
        running_ttl=settings.ops.running_ttl,
        retention_days=settings.ops.retention_days,
    )
    orphaned = await ops_mod.operation_service.recover_orphans()
    if orphaned:
        logger.info("Recovered %d orphaned operations from previous run", orphaned)
    logger.info("Operation service initialised (async)")

    # ── Audit ────────────────────────────────────────────────────────────
    import shoreguard.services.audit as audit_mod

    audit_mod.audit_service = audit_mod.AuditService(session_factory)
    logger.info("Audit service initialised")

    # ── Webhooks ────────────────────────────────────────────────────────
    import shoreguard.services.webhooks as webhook_mod

    webhook_mod.webhook_service = webhook_mod.WebhookService(session_factory)
    logger.info("Webhook service initialised")

    # ── Bypass detection ────────────────────────────────────────────────
    import shoreguard.services.bypass as bypass_mod

    bypass_mod.bypass_service = bypass_mod.BypassService()
    logger.info("Bypass detection service initialised")

    # ── Policy pin service (M18) ─────────────────────────────────────
    import shoreguard.services.policy_pin as pin_mod

    pin_mod.policy_pin_service = pin_mod.PolicyPinService(session_factory)
    logger.info("Policy pin service initialised")

    # ── Approval workflow service (M19) ───────────────────────────────
    import shoreguard.services.approval_workflow as wf_mod

    wf_mod.approval_workflow_service = wf_mod.ApprovalWorkflowService(session_factory)
    logger.info("Approval workflow service initialised")

    # ── Denial context cache (M16) ─────────────────────────────────────
    import shoreguard.services.denial_context as dc_mod

    dc_mod.denial_context_service = dc_mod.DenialContextService()
    logger.info("Denial context cache initialised")

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
    async def _cleanup_operations() -> None:
        """Periodically purge expired operations and audit entries."""
        base_interval = settings.background.cleanup_interval
        max_interval = settings.background.cleanup_max_interval
        backoff_threshold = settings.background.cleanup_backoff_threshold
        consecutive_failures = 0
        interval = base_interval
        _task_health["cleanup"]["alive"] = True
        _task_health["cleanup"]["last_success"] = time.time()
        while True:
            await asyncio.sleep(interval)
            try:
                op_svc = ops_mod.operation_service
                if op_svc is not None:
                    await op_svc.cleanup()
                if audit_mod.audit_service:
                    await asyncio.to_thread(audit_mod.audit_service.cleanup)
                if webhook_mod.webhook_service:
                    await asyncio.to_thread(webhook_mod.webhook_service.cleanup_old_deliveries)
                consecutive_failures = 0
                interval = base_interval
                _task_health["cleanup"]["last_success"] = time.time()
                _task_health["cleanup"]["consecutive_failures"] = 0
            except Exception:
                consecutive_failures += 1
                _task_health["cleanup"]["consecutive_failures"] = consecutive_failures
                logger.exception(
                    "Operation cleanup failed (consecutive failures: %d)",
                    consecutive_failures,
                )
                if consecutive_failures >= backoff_threshold:
                    interval = min(interval * 2, max_interval)
                    logger.error(
                        "Operation cleanup has failed %d consecutive times, "
                        "backing off to %ds interval",
                        consecutive_failures,
                        interval,
                    )

    async def _health_monitor() -> None:
        """Periodically check health of all registered gateways."""
        base_interval = settings.background.health_interval
        max_interval = settings.background.health_max_interval
        backoff_threshold = settings.background.health_backoff_threshold
        consecutive_failures = 0
        interval = base_interval
        _task_health["health_monitor"]["alive"] = True
        _task_health["health_monitor"]["last_success"] = time.time()
        while True:
            await asyncio.sleep(interval)
            try:
                gw_svc = gw_mod.gateway_service
                if gw_svc is None:
                    continue
                await asyncio.to_thread(gw_svc.check_all_health)
                consecutive_failures = 0
                interval = base_interval
                _task_health["health_monitor"]["last_success"] = time.time()
                _task_health["health_monitor"]["consecutive_failures"] = 0
            except Exception:
                consecutive_failures += 1
                _task_health["health_monitor"]["consecutive_failures"] = consecutive_failures
                logger.exception(
                    "Health monitor error (consecutive failures: %d)",
                    consecutive_failures,
                )
                if consecutive_failures >= backoff_threshold:
                    interval = min(interval * 2, max_interval)
                    logger.error(
                        "Health monitor has failed %d consecutive times, "
                        "backing off to %ds interval",
                        consecutive_failures,
                        interval,
                    )

    def _make_done_cb(name: str) -> Callable[[asyncio.Task[Any]], None]:
        def _cb(t: asyncio.Task[Any]) -> None:
            _task_health[name]["alive"] = False
            if t.cancelled():
                logger.info("Background task %s cancelled", name)
                return
            exc = t.exception()
            if exc is not None:
                logger.error(
                    "Background task %s exited with exception: %s",
                    name,
                    exc,
                    exc_info=exc,
                )
            else:
                logger.warning("Background task %s exited unexpectedly", name)

        return _cb

    cleanup_task = asyncio.create_task(_cleanup_operations())
    cleanup_task.add_done_callback(_make_done_cb("cleanup"))
    health_task = asyncio.create_task(_health_monitor())
    health_task.add_done_callback(_make_done_cb("health_monitor"))
    yield

    # ── Graceful shutdown ──────────────────────────────────────────
    logger.info("Shutdown started")

    # 1. Stop background polling (fast)
    cleanup_task.cancel()
    health_task.cancel()

    # 2. Cancel LRO tasks (CancelledError handler marks ops as failed)
    from shoreguard.api.lro import shutdown_lros

    lro_count = await shutdown_lros(timeout=10.0)
    if lro_count:
        logger.info("Cancelled %d LRO task(s)", lro_count)

    # 3. Cancel in-flight webhook deliveries
    if webhook_mod.webhook_service:
        wh_count = await webhook_mod.webhook_service.shutdown(timeout=3.0)
        if wh_count:
            logger.info("Cancelled %d webhook delivery task(s)", wh_count)

    # 4. Await background task cancellation with a hard deadline so a
    #    task that swallows CancelledError cannot block shutdown forever.
    bg_tasks = (cleanup_task, health_task)
    shutdown_timeout = float(settings.server.graceful_shutdown_timeout)
    _, pending = await asyncio.wait(bg_tasks, timeout=shutdown_timeout)
    if pending:
        logger.warning(
            "Background tasks did not exit within %.1fs: %d still pending",
            shutdown_timeout,
            len(pending),
        )

    # 5. Dispose DB engines
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
async def readyz(verbose: bool = False) -> JSONResponse:
    """Readiness probe — checks database connectivity and gateway health.

    Args:
        verbose: If True, include per-gateway breakdown.

    Returns:
        JSONResponse: 200 with check details when ready, 503 otherwise.
    """
    import shoreguard.services.gateway as gw_mod
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
    if gw_mod.gateway_service is not None:
        checks["gateway_service"] = "ok"
        try:
            gateways = await asyncio.wait_for(
                asyncio.to_thread(gw_mod.gateway_service._registry.list_all),
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
    now = time.time()
    for name, state in _task_health.items():
        if not state["alive"]:
            checks[f"background_{name}"] = "dead"
            healthy = False
            continue
        last = state["last_success"]
        checks[f"background_{name}"] = "ok"
        if last is not None:
            age = now - last
            checks[f"background_{name}_age_s"] = round(age, 1)
            stall_threshold: float
            if name == "cleanup":
                stall_threshold = 2.0 * float(get_settings().background.cleanup_max_interval)
            else:
                stall_threshold = 2.0 * float(get_settings().background.health_max_interval)
            if age > stall_threshold:
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
gw_api.include_router(providers.router, prefix="/providers", tags=["providers"])


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
