"""Application factory — builds the ShoreGuard FastAPI app.

:func:`create_app` assembles middleware, routers, and static files;
:func:`lifespan` owns process startup/shutdown (database, the
:class:`~shoreguard.container.ServiceContainer`, auth, background
tasks). ``shoreguard.api.main`` keeps a module-level ``app`` built from
this factory for ``uvicorn shoreguard.api.main:app`` compatibility.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from shoreguard import __build_time__, __git_sha__, __version__

if TYPE_CHECKING:
    from shoreguard.services.local_gateway import LocalGatewayManager
    from shoreguard.settings import Settings

logger = logging.getLogger(__name__)

OPENAPI_TAGS = [
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
]


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
    from shoreguard.settings import get_settings

    settings = get_settings()
    settings.enforce_production_safety()

    from shoreguard.api.metrics import RequestIdFilter, shoreguard_info

    # Install request-ID log filter so %(request_id)s is available in all loggers.
    logging.getLogger().addFilter(RequestIdFilter())

    # ── Tracing (OTel) ──────────────────────────────────────────────────
    # Must happen before any gRPC channel is constructed so the client
    # instrumentor can patch channel creation.
    from shoreguard.api.tracing import init_tracing, instrument_fastapi

    if init_tracing():
        instrument_fastapi(app)

    from shoreguard.container import build_container, install, uninstall
    from shoreguard.db import get_async_session_factory, init_async_db, init_db
    from shoreguard.services.audit_export import AuditExporter
    from shoreguard.services.gateway_import import import_filesystem_gateways

    try:
        engine = init_db()
    except Exception:
        logger.exception("Failed to initialise database")
        raise
    init_async_db(str(engine.url))

    # The exporter needs the running loop to schedule webhook dispatch
    # from the sync ``AuditService.log`` path, so it is built here rather
    # than inside ``build_container``.
    audit_exporter = AuditExporter(settings.audit, loop=asyncio.get_running_loop())
    container = build_container(
        settings,
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
        imported, _skipped = await import_filesystem_gateways(container.registry)
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
    from shoreguard.api.auth import bootstrap_admin_user, init_auth, is_setup_complete
    from shoreguard.api.oidc import init_oidc

    init_auth(get_async_session_factory())
    await bootstrap_admin_user()
    init_oidc()

    # Hide OpenAPI docs when authentication is enabled to avoid leaking
    # the full API schema to unauthenticated users.
    if await is_setup_complete():
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


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ShoreGuard FastAPI application.

    Args:
        settings: Settings snapshot for app-construction-time decisions
            (currently CORS). Falls back to ``get_settings()``. Runtime
            settings are still read lazily per request.

    Returns:
        FastAPI: The fully assembled application.
    """
    from shoreguard.api.auth import require_auth, require_role
    from shoreguard.api.deps import resolve_gateway
    from shoreguard.api.errors import register_error_handlers
    from shoreguard.api.metrics import metrics_middleware
    from shoreguard.api.metrics import router as metrics_router
    from shoreguard.api.middleware import (
        NoCacheStaticFiles,
        body_size_limit_middleware,
        global_rate_limit_middleware,
    )
    from shoreguard.api.pages import FRONTEND_DIR
    from shoreguard.api.pages import router as pages_router
    from shoreguard.api.routes import (
        approvals,
        audit,
        boot_hooks,
        budgets,
        bypass,
        digest,
        gateway,
        health,
        inference,
        one_tap,
        operations,
        policies,
        prover,
        provider_profiles,
        providers,
        sandboxes,
        sbom,
        security,
        services,
        system,
        templates,
        tokens,
        webhooks,
    )
    from shoreguard.api.routes import (
        auth as auth_routes,
    )
    from shoreguard.api.routes import (
        users as user_routes,
    )
    from shoreguard.api.security_headers import security_headers_middleware
    from shoreguard.api.websocket import router as ws_router
    from shoreguard.settings import get_settings

    settings = settings or get_settings()

    app = FastAPI(
        title="Shoreguard",
        description="Open source control plane for NVIDIA OpenShell",
        version=__version__,
        lifespan=lifespan,
        openapi_tags=OPENAPI_TAGS,
    )

    register_error_handlers(app)

    # ── Middleware (outermost last-added) ───────────────────────────────
    app.middleware("http")(metrics_middleware)
    app.middleware("http")(security_headers_middleware)
    app.middleware("http")(global_rate_limit_middleware)
    app.middleware("http")(body_size_limit_middleware)
    # GZip compression for responses >= 1 KB (SSE/WebSockets unaffected).
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    if settings.cors.allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors.allow_origins,
            allow_credentials=settings.cors.allow_credentials,
            allow_methods=settings.cors.allow_methods,
            allow_headers=settings.cors.allow_headers,
            max_age=settings.cors.max_age,
        )

    # ── Health probes (unauthenticated) ─────────────────────────────────
    app.include_router(health.router)
    app.include_router(metrics_router)

    # ── Gateway-scoped API routes ────────────────────────────────────────
    # All sandbox/policy/provider operations are scoped to a specific
    # gateway; resolve_gateway stores the {gw} path segment on
    # request.state so get_client() returns the right client.
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
    gw_api.include_router(budgets.router, prefix="/sandboxes", tags=["budgets"])
    gw_api.include_router(providers.router, prefix="/providers", tags=["providers"])
    gw_api.include_router(
        provider_profiles.router, prefix="/provider-profiles", tags=["provider-profiles"]
    )
    gw_api.include_router(services.router, prefix="/services", tags=["services"])
    gw_api.include_router(tokens.router, prefix="/tokens", tags=["tokens"])
    gw_api.include_router(inference.router)
    app.include_router(gw_api)

    # ── Global API routes (not gateway-scoped) ───────────────────────────
    app.include_router(
        gateway.router,
        prefix="/api/gateway",
        tags=["gateway"],
        dependencies=[Depends(require_auth)],
    )
    # Presets are local YAML files, not gateway-scoped — mount only preset
    # routes globally. The sandbox-scoped policy routes are already
    # mounted under gw_api and must NOT be duplicated at the global level.
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
        security.router,
        prefix="/api/security",
        tags=["security"],
        dependencies=[Depends(require_auth), Depends(require_role("admin"))],
    )
    app.include_router(
        digest.router,
        prefix="/api/digest",
        tags=["digest"],
        dependencies=[Depends(require_auth)],
    )
    app.include_router(
        budgets.summary_router,
        prefix="/api/usage",
        tags=["budgets"],
        dependencies=[Depends(require_auth)],
    )
    app.include_router(
        system.router,
        prefix="/api/system",
        tags=["system"],
        dependencies=[Depends(require_auth)],
    )
    app.include_router(
        templates.router,
        prefix="/api/sandbox-templates",
        tags=["templates"],
        dependencies=[Depends(require_auth)],
    )

    # ── Auth + user management APIs (self-guarded routes) ────────────────
    app.include_router(auth_routes.router)
    app.include_router(user_routes.router)
    # One-tap approval votes: the signed token IS the credential, so the
    # route mounts without the session-auth dependency (and 404s unless
    # the feature is explicitly enabled).
    app.include_router(one_tap.router, tags=["approvals"])

    # ── WebSocket, pages, and static files ───────────────────────────────
    app.include_router(ws_router)
    app.include_router(pages_router)
    app.mount("/static", NoCacheStaticFiles(directory=str(FRONTEND_DIR)), name="static")

    return app
