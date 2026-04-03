"""Integration tests for GatewayService with a real gateway."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_gateway_list_all(gateway_service):
    """list_all() returns a list with registered gateways."""
    result = gateway_service.list_all()

    assert isinstance(result, list)
    if result:
        gw = result[0]
        assert "name" in gw
        assert "status" in gw


def test_local_gateway_diagnostics(gateway_service):
    """LocalGatewayManager.diagnostics() returns Docker and openshell status."""
    from shoreguard.services.local_gateway import LocalGatewayManager

    mgr = LocalGatewayManager(gateway_service)
    result = mgr.diagnostics()

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
    result = gateway_service.get_config("integration-test")

    assert "settings" in result
    assert isinstance(result["settings"], dict)
    assert "settings_revision" in result
