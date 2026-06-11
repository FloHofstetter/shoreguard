"""Liveness and readiness probes (unauthenticated)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from shoreguard import __build_time__, __git_sha__, __version__
from shoreguard.api.schemas import HealthResponse, VersionResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> dict[str, str]:
    """Liveness probe — returns 200 if the process is running.

    Returns:
        dict[str, str]: Status object with ``{"status": "ok"}``.
    """
    return {"status": "ok"}


@router.get("/version", response_model=VersionResponse)
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


@router.get("/readyz")
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
                container.registry.list_all(),
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
