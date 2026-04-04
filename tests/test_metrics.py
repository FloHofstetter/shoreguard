"""Tests for the Prometheus /metrics endpoint."""

from __future__ import annotations

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
