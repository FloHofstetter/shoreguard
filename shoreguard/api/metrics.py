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

# ── M28 gRPC / sandbox hardening metrics ────────────────────────────────

sg_grpc_call_total = Counter(
    "sg_grpc_call_total",
    "Total gRPC calls to OpenShell gateways by logical op and final status code",
    ["op", "code"],
)

sg_grpc_call_duration_seconds = Histogram(
    "sg_grpc_call_duration_seconds",
    "gRPC call wall-clock duration including retries, in seconds",
    ["op"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

sg_grpc_retry_total = Counter(
    "sg_grpc_retry_total",
    "gRPC call retries by logical op and observed status code",
    ["op", "code"],
)

sg_sandbox_phase_transitions_total = Counter(
    "sg_sandbox_phase_transitions_total",
    "Observed sandbox phase transitions per gateway",
    ["gateway", "from", "to"],
)

sg_boot_hook_runs_total = Counter(
    "sg_boot_hook_runs_total",
    "Boot hook executions by gateway, phase, and final status",
    ["gateway", "phase", "status"],
)

sg_boot_hook_duration_seconds = Histogram(
    "sg_boot_hook_duration_seconds",
    "Boot hook execution duration in seconds",
    ["phase"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 15.0, 30.0, 60.0, 300.0),
)

sg_gateway_cert_expiry_seconds = Gauge(
    "sg_gateway_cert_expiry_seconds",
    "Seconds until the registered gateway client certificate expires",
    ["gateway"],
)

sg_gateway_cert_rotations_total = Counter(
    "sg_gateway_cert_rotations_total",
    "Outcomes of the background cert-rotation service per gateway",
    ["gateway", "outcome"],
)


def record_grpc_attempt(*, op_name: str, attempt: int, code: object, outcome: str) -> None:
    """Callback target for :func:`shoreguard.client._resilience.call_with_retry`.

    Increments the retry counter for every intermediate retry and records the
    final status code on success or give-up. Call duration is observed by the
    caller via :func:`record_grpc_duration` since the resilience helper does
    not know when the logical op started.

    Args:
        op_name: Logical-op label such as ``"sandboxes.create"``.
        attempt: 1-based attempt number.
        code: gRPC status code of the just-observed attempt or ``None`` on
            success.
        outcome: One of ``"ok"``, ``"retry"``, ``"giveup"``.
    """
    code_label = getattr(code, "name", "OK" if code is None else str(code))
    if outcome == "retry":
        sg_grpc_retry_total.labels(op=op_name, code=code_label).inc()
    else:
        sg_grpc_call_total.labels(op=op_name, code=code_label).inc()


def record_grpc_duration(op_name: str, duration_s: float) -> None:
    """Observe the wall-clock duration of a logical gRPC op.

    Args:
        op_name: Logical-op label such as ``"sandboxes.create"``.
        duration_s: Wall-clock duration in seconds including any retries.
    """
    sg_grpc_call_duration_seconds.labels(op=op_name).observe(duration_s)


def record_boot_hook_run(*, gateway: str, phase: str, status: str, duration_s: float) -> None:
    """Record a boot-hook execution outcome.

    Args:
        gateway: Gateway name the sandbox belongs to.
        phase: ``pre_create`` or ``post_create``.
        status: ``success`` or ``failure``.
        duration_s: Wall-clock duration of the hook in seconds.
    """
    sg_boot_hook_runs_total.labels(gateway=gateway, phase=phase, status=status).inc()
    sg_boot_hook_duration_seconds.labels(phase=phase).observe(duration_s)


def record_sandbox_phase_transition(*, gateway: str, from_phase: str, to_phase: str) -> None:
    """Increment the phase transition counter for a sandbox.

    Args:
        gateway: Gateway name the sandbox belongs to.
        from_phase: Previous phase string, or ``"none"`` for the first
            observation after creation.
        to_phase: New phase string.
    """
    sg_sandbox_phase_transitions_total.labels(
        gateway=gateway, **{"from": from_phase, "to": to_phase}
    ).inc()


def record_gateway_cert_rotation(gateway: str, outcome: str) -> None:
    """Increment the cert-rotation outcome counter.

    Args:
        gateway: Gateway name label.
        outcome: One of ``"success"``, ``"failure"``, ``"skipped_not_due"``,
            ``"skipped_no_cert"``. Unknown labels are accepted so callers
            can add new outcomes without a code change here; dashboards
            will surface them automatically.
    """
    sg_gateway_cert_rotations_total.labels(gateway=gateway, outcome=outcome).inc()


def record_gateway_cert_expiry(gateway: str, seconds_until_expiry: float | None) -> None:
    """Publish the seconds-until-expiry gauge for a gateway's client cert.

    Args:
        gateway: Gateway name.
        seconds_until_expiry: Remaining seconds until ``NotAfter`` of the
            registered client certificate. ``None`` clears the gauge (e.g.
            when the gateway was removed).
    """
    if seconds_until_expiry is None:
        sg_gateway_cert_expiry_seconds.labels(gateway=gateway).set(0)
        return
    sg_gateway_cert_expiry_seconds.labels(gateway=gateway).set(seconds_until_expiry)


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
