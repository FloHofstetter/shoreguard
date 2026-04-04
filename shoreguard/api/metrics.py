"""Prometheus metrics endpoint and HTTP request middleware."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Info, generate_latest

# ── Metrics definitions ──────────────────────────────────────────────────

shoreguard_info = Info("shoreguard", "ShoreGuard build information")

gateways_total = Gauge(
    "shoreguard_gateways_total",
    "Number of registered gateways by status",
    ["status"],
)

operations_total = Gauge(
    "shoreguard_operations_total",
    "Number of tracked operations by status",
    ["status"],
)

webhook_deliveries_total = Counter(
    "shoreguard_webhook_deliveries_total",
    "Total webhook delivery attempts by result",
    ["status"],
)

http_requests_total = Counter(
    "shoreguard_http_requests_total",
    "Total HTTP requests by method and status code",
    ["method", "status"],
)

# ── Router ───────────────────────────────────────────────────────────────

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint (unauthenticated).

    Returns:
        Response: Prometheus text format metrics.
    """
    await asyncio.to_thread(_collect_gauges)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── Middleware ────────────────────────────────────────────────────────────


async def metrics_middleware(request: Request, call_next: object) -> Response:
    """Count HTTP requests by method and status code.

    Args:
        request: The incoming HTTP request.
        call_next: ASGI middleware chain.

    Returns:
        Response: The response from the next middleware/handler.
    """
    response = await call_next(request)  # type: ignore[operator]
    if not request.url.path.startswith("/metrics"):
        http_requests_total.labels(
            method=request.method,
            status=str(response.status_code),
        ).inc()
    return response  # type: ignore[return-value]


# ── Gauge collection ─────────────────────────────────────────────────────


def _collect_gauges() -> None:
    """Update gauge values from current service state."""
    import shoreguard.services.gateway as gw_mod
    from shoreguard.services.operations import operation_store

    # Gateway status counts
    if gw_mod.gateway_service is not None:
        all_gw = gw_mod.gateway_service.list_all()
        counts: dict[str, int] = {}
        for g in all_gw:
            status = g.get("status", "unknown")
            counts[status] = counts.get(status, 0) + 1
        gateways_total._metrics.clear()
        for status, count in counts.items():
            gateways_total.labels(status=status).set(count)

    # Operation status counts
    op_counts = operation_store.status_counts()
    operations_total._metrics.clear()
    for status, count in op_counts.items():
        operations_total.labels(status=status).set(count)
