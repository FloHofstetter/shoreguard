"""Integration tests for GatewayService with a real gateway."""

from __future__ import annotations

import pytest

from shoreguard.services.gateway import GatewayService

pytestmark = pytest.mark.integration


def test_gateway_health(gateway_service):
    """health() returns connected=True with a live gateway."""
    result = gateway_service.health()

    assert result["connected"] is True
    assert "version" in result
    assert "health_status" in result


def test_gateway_list_all(sg_client):
    """list_all() returns a list that includes at least one gateway."""
    svc = GatewayService()
    result = svc.list_all()

    assert isinstance(result, list)
    # There should be at least the active/test gateway
    if result:
        gw = result[0]
        assert "name" in gw
        assert "status" in gw
        assert "container_status" in gw


def test_gateway_diagnostics(gateway_service):
    """diagnostics() returns Docker and openshell status."""
    result = gateway_service.diagnostics()

    assert "docker_installed" in result
    assert "docker_daemon_running" in result
    assert "docker_accessible" in result
    assert "user" in result
    assert isinstance(result["user"], str)
    # Docker must be running for the gateway to work
    assert result["docker_installed"] is True
    assert result["docker_daemon_running"] is True


def test_gateway_get_config(gateway_service):
    """get_config() returns settings from the live gateway."""
    result = gateway_service.get_config()

    assert "settings" in result
    assert isinstance(result["settings"], dict)
    assert "settings_revision" in result
