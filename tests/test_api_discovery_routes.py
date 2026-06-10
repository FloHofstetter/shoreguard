"""Integration tests for the DNS-SRV gateway discovery API routes."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _init_discovery(container):
    """Install a DiscoveryService with test settings in the container."""
    from shoreguard.services.discovery import DiscoveryService
    from shoreguard.settings import DiscoverySettings

    settings = DiscoverySettings(
        enabled=True,
        domains=["openshell.internal"],
        interval_seconds=60,
        auto_register=True,
    )
    container.discovery = DiscoveryService(
        container.registry,
        container.gateway,
        settings,
    )


class TestDiscoverEndpoint:
    async def test_status_initial(self, api_client):
        resp = await api_client.get("/api/gateway/discovery/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["domains"] == ["openshell.internal"]
        assert data["last_run_at"] is None

    async def test_discover_runs(self, api_client):
        with patch(
            "shoreguard.services.discovery.DiscoveryService.discover_domain",
            return_value=[],
        ):
            resp = await api_client.post("/api/gateway/discover", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["registered"] == []
        assert data["discovered"] == []

    async def test_discover_with_domain_override(self, api_client):
        with patch(
            "shoreguard.services.discovery.DiscoveryService.discover_domain",
            return_value=[],
        ) as mock:
            resp = await api_client.post(
                "/api/gateway/discover",
                json={"domains": ["custom.example.com"]},
            )
        assert resp.status_code == 200
        mock.assert_called_with("custom.example.com")
