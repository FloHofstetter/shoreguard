"""Tests for the Prometheus /metrics and health endpoints."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from shoreguard.api.main import app


@pytest.fixture
async def client():
    """Async HTTP client for testing unauthenticated endpoints."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


class TestMetricsEndpoint:
    async def test_returns_200(self, client):
        resp = await client.get("/metrics")
        assert resp.status_code == 200

    async def test_content_type(self, client):
        resp = await client.get("/metrics")
        assert "text/plain" in resp.headers["content-type"]

    async def test_contains_build_info(self, client):
        resp = await client.get("/metrics")
        assert "shoreguard_info" in resp.text

    async def test_contains_gateway_gauge(self, client):
        resp = await client.get("/metrics")
        assert "shoreguard_gateways_total" in resp.text

    async def test_http_counter_after_request(self, client):
        await client.get("/healthz")
        resp = await client.get("/metrics")
        assert "shoreguard_http_requests_total" in resp.text


@pytest.fixture
def _auth_db():
    """Set up auth with a real DB session factory (no_auth=False)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from shoreguard.api import auth
    from shoreguard.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    auth.init_auth_for_test(factory)
    yield factory
    auth.reset()
    auth._no_auth = True  # noqa: SLF001 — restore default test state
    engine.dispose()


class TestMetricsAuth:
    async def test_requires_auth_when_auth_enabled(self, client, _auth_db):
        """When auth is enabled and metrics_public=false, /metrics returns 401."""
        resp = await client.get("/metrics")
        assert resp.status_code == 401

    async def test_accessible_when_metrics_public(self, client, _auth_db, monkeypatch):
        """When metrics_public=true, /metrics is accessible without auth."""
        from shoreguard.settings import reset_settings

        monkeypatch.setenv("SHOREGUARD_METRICS_PUBLIC", "true")
        reset_settings()
        resp = await client.get("/metrics")
        assert resp.status_code == 200

    async def test_accessible_when_no_auth(self, client):
        """/metrics is accessible when no_auth=true (default test fixture)."""
        resp = await client.get("/metrics")
        assert resp.status_code == 200


class TestHealthEndpoints:
    async def test_readyz_logs_warning_on_db_failure(self, client, caplog):
        with (
            patch("shoreguard.db.get_engine", side_effect=RuntimeError("connection refused")),
            caplog.at_level(logging.WARNING, logger="shoreguard.api.main"),
        ):
            resp = await client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json()["status"] == "not ready"
        assert "database unreachable" in caplog.text
        assert "connection refused" in caplog.text
