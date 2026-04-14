"""OpenTelemetry trace-context propagation for ShoreGuard.

M28 Observability — Säule 2. Wires up auto-instrumentation for the two
boundary layers the routed-inference path crosses:

* **FastAPI** — incoming HTTP requests become root spans and honour any
  inbound ``traceparent`` header (W3C Trace Context).
* **gRPC client** — outgoing calls to OpenShell gateways are wrapped so
  that the active trace context rides along as gRPC metadata.

Nothing in this module is called unless ``settings.tracing.enabled`` is
true; the imports themselves are cheap.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_instrumented = False


def init_tracing() -> bool:
    """Initialise the global TracerProvider and auto-instrument FastAPI + gRPC.

    Idempotent: a second call is a no-op so tests can re-run the setup in
    the same interpreter without colliding with the already-instrumented
    gRPC library state.

    Returns:
        bool: True when tracing was (or already is) active, False when
        disabled via settings.
    """
    global _instrumented

    from shoreguard.settings import get_settings

    settings = get_settings()
    if not settings.tracing.enabled:
        return False

    if _instrumented:
        return True

    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

    resource = Resource.create({"service.name": settings.tracing.service_name})
    provider = TracerProvider(
        resource=resource,
        sampler=TraceIdRatioBased(settings.tracing.sample_ratio),
    )

    if settings.tracing.otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter: object = OTLPSpanExporter(endpoint=settings.tracing.otlp_endpoint)
        logger.info("Tracing: OTLP exporter -> %s", settings.tracing.otlp_endpoint)
    else:
        exporter = ConsoleSpanExporter()
        logger.info("Tracing: console exporter (no otlp_endpoint configured)")

    provider.add_span_processor(BatchSpanProcessor(exporter))  # type: ignore[arg-type]
    trace.set_tracer_provider(provider)

    from opentelemetry.instrumentation.grpc import GrpcInstrumentorClient

    GrpcInstrumentorClient().instrument()
    logger.info("Tracing: gRPC client instrumented")

    _instrumented = True
    return True


def instrument_fastapi(app: object) -> None:
    """Wrap a FastAPI app with the OTel ASGI middleware.

    Must be called after :func:`init_tracing`. Safe to call when tracing
    is disabled — the instrumentor itself checks the global TracerProvider
    and becomes a no-op.

    Args:
        app: The FastAPI application instance.
    """
    from shoreguard.settings import get_settings

    if not get_settings().tracing.enabled:
        return

    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]
    logger.info("Tracing: FastAPI instrumented")


def reset_for_tests() -> None:
    """Release the global `_instrumented` flag so tests can re-init.

    Does not un-patch gRPC; tests that care must use their own mocks.
    """
    global _instrumented
    _instrumented = False
