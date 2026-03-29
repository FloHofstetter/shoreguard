"""FastAPI application entry point."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
from .pages import FRONTEND_DIR
from .pages import router as pages_router
from .routes import approvals, gateway, operations, policies, providers, sandboxes
from .websocket import router as ws_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — initialise DB, services, and background tasks."""
    import os

    from sqlalchemy.orm import sessionmaker as sa_sessionmaker

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

    if os.environ.get("SHOREGUARD_LOCAL_MODE"):
        import shoreguard.services.local_gateway as local_mod

        local_mod.local_gateway_manager = local_mod.LocalGatewayManager(gw_mod.gateway_service)
        logger.info("Local gateway mode enabled")

        # Auto-import filesystem gateways so locally managed gateways
        # appear in the DB without a manual import-gateways step.
        imported, skipped = _import_filesystem_gateways(registry)
        if imported:
            logger.info("Auto-imported %d gateway(s) from filesystem", imported)

    # ── Auth ─────────────────────────────────────────────────────────────
    init_auth(session_factory)
    bootstrap_admin_user()

    # Hide OpenAPI docs when authentication is enabled to avoid leaking
    # the full API schema to unauthenticated users.
    if is_setup_complete():
        app.openapi_url = None
        app.docs_url = None
        app.redoc_url = None

    # ── Background tasks ─────────────────────────────────────────────────
    async def _cleanup_operations() -> None:
        from shoreguard.services.operations import operation_store

        consecutive_failures = 0
        while True:
            await asyncio.sleep(600)
            try:
                operation_store.cleanup()
                consecutive_failures = 0
            except Exception:
                consecutive_failures += 1
                logger.exception(
                    "Operation cleanup failed (consecutive failures: %d)",
                    consecutive_failures,
                )

    async def _health_monitor() -> None:
        consecutive_failures = 0
        while True:
            await asyncio.sleep(30)
            try:
                await asyncio.to_thread(gw_mod.gateway_service.check_all_health)  # type: ignore[union-attr]
                consecutive_failures = 0
            except Exception:
                consecutive_failures += 1
                logger.exception(
                    "Health monitor error (consecutive failures: %d)",
                    consecutive_failures,
                )

    cleanup_task = asyncio.create_task(_cleanup_operations())
    health_task = asyncio.create_task(_health_monitor())
    yield
    cleanup_task.cancel()
    health_task.cancel()
    for task in (cleanup_task, health_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    engine.dispose()
    logger.debug("Database engine disposed")


app = FastAPI(
    title="Shoreguard",
    description="Open source control plane for NVIDIA OpenShell",
    version="0.4.0",
    lifespan=lifespan,
)

register_error_handlers(app)


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


@gw_api.put("/inference", dependencies=[Depends(require_role("operator"))])
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


# ─── WebSocket, pages, and static files ─────────────────────────────────────

app.include_router(ws_router)
app.include_router(pages_router)

# Serve static files (CSS, JS, images)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


if __name__ == "__main__":
    cli()
