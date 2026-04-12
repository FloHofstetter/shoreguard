"""Integration tests for the discovery API routes (M22)."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _init_discovery():
    """Wire DiscoveryService into the global module so the route can find it."""
    import shoreguard.services.discovery as discovery_mod
    import shoreguard.services.gateway as gw_mod
    from shoreguard.services.discovery import DiscoveryService
    from shoreguard.settings import DiscoverySettings

    settings = DiscoverySettings(
        enabled=True,
        domains=["openshell.internal"],
        interval_seconds=60,
        auto_register=True,
    )
    assert gw_mod.gateway_service is not None
    discovery_mod.discovery_service = DiscoveryService(
        gw_mod.gateway_service._registry,
        gw_mod.gateway_service,
        settings,
    )
    yield
    discovery_mod.discovery_service = None


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
