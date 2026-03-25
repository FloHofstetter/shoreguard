"""Integration tests for client connection and health check."""

from __future__ import annotations

import pytest

from shoreguard.client import ShoreGuardClient

pytestmark = pytest.mark.integration


def test_health_returns_healthy(sg_client):
    """health() returns a dict with status and version from a live gateway."""
    result = sg_client.health()

    assert result["status"] in ("healthy", "ok")
    assert isinstance(result["version"], str)
    assert len(result["version"]) > 0


def test_get_gateway_config(sg_client):
    """get_gateway_config() returns settings and revision from a live gateway."""
    result = sg_client.get_gateway_config()

    assert "settings" in result
    assert isinstance(result["settings"], dict)
    assert "settings_revision" in result
    assert isinstance(result["settings_revision"], int)


def test_client_context_manager(gateway_endpoint):
    """Client works as a context manager and closes cleanly."""
    if gateway_endpoint.startswith("__cluster__:"):
        cluster = gateway_endpoint.split(":", 1)[1]
        client = ShoreGuardClient.from_active_cluster(cluster=cluster)
    else:
        client = ShoreGuardClient(gateway_endpoint)

    with client as c:
        result = c.health()
        assert result["status"] in ("healthy", "ok")
