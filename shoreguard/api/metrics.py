"""Prometheus metrics endpoint, HTTP request middleware, and request-ID tracking."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, Info, generate_latest

# ── Request-ID ContextVar ───────────────────────────────────────────────

request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

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

http_request_duration_seconds = Histogram(
    "shoreguard_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path_template"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# ── Router ───────────────────────────────────────────────────────────────

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    """Prometheus metrics endpoint.

    Requires authentication unless ``SHOREGUARD_METRICS_PUBLIC=true`` or
    auth is disabled entirely (``SHOREGUARD_NO_AUTH=true``).

    Args:
        request: Incoming HTTP request.

    Returns:
        Response: Prometheus text format metrics.

    Raises:
        HTTPException: 401 when authentication is required but missing.
    """
    from shoreguard.settings import get_settings

    settings = get_settings()
    if not settings.auth.no_auth and not settings.auth.metrics_public:
        from .auth import check_request_auth

        identity = check_request_auth(request)
        if identity is None:
            raise HTTPException(status_code=401, detail="Authentication required")
    await _collect_gauges()
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── Log filter ───────────────────────────────────────────────────────────


class RequestIdFilter(logging.Filter):
    """Inject ``request_id`` into every log record from the ContextVar."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003, D102
        record.request_id = request_id_ctx.get() or "-"  # type: ignore[attr-defined]
        return True


# ── Middleware ────────────────────────────────────────────────────────────


async def metrics_middleware(request: Request, call_next: object) -> Response:
    """Track request-ID, latency, and count for every HTTP request.

    Args:
        request: The incoming HTTP request.
        call_next: ASGI middleware chain.

    Returns:
        Response: The response from the next middleware/handler.
    """
    # ── Request-ID: honour inbound header or generate ──
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    request.state.request_id = rid
    request_id_ctx.set(rid)

    start = time.monotonic()
    response: Response = await call_next(request)  # type: ignore[operator]
    duration = time.monotonic() - start

    # ── Response header ──
    response.headers["X-Request-ID"] = rid

    # ── Metrics (skip /metrics itself) ──
    if not request.url.path.startswith("/metrics"):
        http_requests_total.labels(
            method=request.method,
            status=str(response.status_code),
        ).inc()

        route = request.scope.get("route")
        path_template = route.path if route else request.url.path
        http_request_duration_seconds.labels(
            method=request.method,
            path_template=path_template,
        ).observe(duration)

    return response


# ── Gauge collection ─────────────────────────────────────────────────────


async def _collect_gauges() -> None:
    """Update gauge values from current service state."""
    import shoreguard.services.gateway as gw_mod
    import shoreguard.services.operations as ops_mod

    # Gateway status counts
    if gw_mod.gateway_service is not None:
        all_gw = await asyncio.to_thread(gw_mod.gateway_service.list_all)
        counts: dict[str, int] = {}
        for g in all_gw:
            status = g.get("status", "unknown")
            counts[status] = counts.get(status, 0) + 1
        gateways_total._metrics.clear()
        for status, count in counts.items():
            gateways_total.labels(status=status).set(count)

    # Operation status counts
    operations_total._metrics.clear()
    op_svc = ops_mod.operation_service
    if op_svc is not None:
        op_counts = await op_svc.status_counts()
        for status, count in op_counts.items():
            operations_total.labels(status=status).set(count)
