"""Unit tests for the M28 OpenTelemetry tracing init path."""

from __future__ import annotations

import pytest

from shoreguard.api import tracing as tracing_mod


@pytest.fixture(autouse=True)
def _reset_tracing_state():
    tracing_mod.reset_for_tests()
    yield
    tracing_mod.reset_for_tests()


def test_init_tracing_disabled_returns_false(monkeypatch):
    from shoreguard.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings.tracing, "enabled", False)

    assert tracing_mod.init_tracing() is False


def test_init_tracing_enabled_console_exporter(monkeypatch):
    from shoreguard.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings.tracing, "enabled", True)
    monkeypatch.setattr(settings.tracing, "otlp_endpoint", None)
    monkeypatch.setattr(settings.tracing, "sample_ratio", 1.0)

    assert tracing_mod.init_tracing() is True
    # Idempotent
    assert tracing_mod.init_tracing() is True


def test_init_tracing_installs_tracer_provider(monkeypatch):
    from opentelemetry import trace

    from shoreguard.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings.tracing, "enabled", True)
    monkeypatch.setattr(settings.tracing, "otlp_endpoint", None)

    tracing_mod.init_tracing()

    provider = trace.get_tracer_provider()
    tracer = provider.get_tracer("shoreguard-test")
    with tracer.start_as_current_span("unit-test-span") as span:
        assert span is not None
        ctx = span.get_span_context()
        assert ctx.trace_id != 0


def test_instrument_fastapi_noop_when_disabled(monkeypatch):
    from fastapi import FastAPI

    from shoreguard.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings.tracing, "enabled", False)

    app = FastAPI()
    tracing_mod.instrument_fastapi(app)


def test_w3c_traceparent_propagation_over_fastapi(monkeypatch):
    """End-to-end: inbound W3C traceparent becomes active trace context."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from opentelemetry import trace

    from shoreguard.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings.tracing, "enabled", True)
    monkeypatch.setattr(settings.tracing, "otlp_endpoint", None)

    tracing_mod.init_tracing()

    app = FastAPI()
    seen_trace_id: dict[str, int] = {}

    @app.get("/ping")
    def _ping() -> dict[str, str]:
        span = trace.get_current_span()
        seen_trace_id["id"] = span.get_span_context().trace_id
        return {"ok": "ok"}

    tracing_mod.instrument_fastapi(app)

    client = TestClient(app)
    # W3C traceparent: 00-<trace_id>-<span_id>-01
    traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    resp = client.get("/ping", headers={"traceparent": traceparent})

    assert resp.status_code == 200
    assert seen_trace_id.get("id") == 0x0AF7651916CD43DD8448EB211C80319C
