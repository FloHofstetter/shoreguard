"""Unit tests for api/deps.py — ContextVar delegation to gateway_service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from shoreguard.api.deps import (
    _current_gateway,
    get_client,
    reset_backoff,
    resolve_gateway,
    set_client,
)


def test_get_client_delegates():
    """get_client() reads current gateway from ContextVar and delegates to gateway_service."""
    _current_gateway.set("my-gw")
    with patch("shoreguard.services.gateway.gateway_service") as mock_svc:
        mock_svc.get_client.return_value = MagicMock()
        get_client()
        mock_svc.get_client.assert_called_once_with(name="my-gw")


def test_set_client_delegates():
    """set_client() reads current gateway and delegates to gateway_service.set_client."""
    _current_gateway.set("my-gw")
    mock_client = MagicMock()
    with patch("shoreguard.services.gateway.gateway_service") as mock_svc:
        set_client(mock_client)
        mock_svc.set_client.assert_called_once_with(mock_client, name="my-gw")


def test_reset_backoff_delegates():
    """reset_backoff() reads current gateway and delegates to gateway_service.reset_backoff."""
    _current_gateway.set("my-gw")
    with patch("shoreguard.services.gateway.gateway_service") as mock_svc:
        reset_backoff()
        mock_svc.reset_backoff.assert_called_once_with(name="my-gw")


# ─── resolve_gateway ────────────────────────────────────────────────────────


def test_resolve_gateway_valid_name():
    """resolve_gateway sets the ContextVar for a valid name."""
    resolve_gateway("my-gw")
    assert _current_gateway.get() == "my-gw"


def test_resolve_gateway_invalid_name_raises():
    """resolve_gateway raises HTTPException 400 for invalid names."""
    with pytest.raises(HTTPException) as exc_info:
        resolve_gateway("--malicious")
    assert exc_info.value.status_code == 400


def test_resolve_gateway_rejects_empty():
    """resolve_gateway rejects empty string."""
    with pytest.raises(HTTPException) as exc_info:
        resolve_gateway("")
    assert exc_info.value.status_code == 400


def test_get_client_with_none_gateway():
    """get_client passes None when no gateway is set."""
    _current_gateway.set(None)
    with patch("shoreguard.services.gateway.gateway_service") as mock_svc:
        mock_svc.get_client.return_value = MagicMock()
        get_client()
        mock_svc.get_client.assert_called_once_with(name=None)


# ─── _get_gateway_service None check ──────────────────────────────────────


def test_get_gateway_service_raises_when_none():
    """_get_gateway_service raises RuntimeError if gateway_service is None."""
    from shoreguard.api.deps import _get_gateway_service

    with patch("shoreguard.services.gateway.gateway_service", None):
        with pytest.raises(RuntimeError, match="not initialised"):
            _get_gateway_service()


# ─── ShoreGuardClient.from_active_cluster error handling ──────────────────


def test_from_active_cluster_missing_metadata_file(tmp_path, monkeypatch):
    """from_active_cluster raises GatewayNotConnectedError for missing metadata."""
    from shoreguard.client import ShoreGuardClient
    from shoreguard.exceptions import GatewayNotConnectedError

    monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: tmp_path)
    gw_dir = tmp_path / "gateways" / "my-gw"
    gw_dir.mkdir(parents=True)
    # No metadata.json

    with pytest.raises(GatewayNotConnectedError, match="Failed to load metadata"):
        ShoreGuardClient.from_active_cluster(cluster="my-gw")


def test_from_active_cluster_corrupt_json(tmp_path, monkeypatch):
    """from_active_cluster raises GatewayNotConnectedError for corrupt JSON."""
    from shoreguard.client import ShoreGuardClient
    from shoreguard.exceptions import GatewayNotConnectedError

    monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: tmp_path)
    gw_dir = tmp_path / "gateways" / "my-gw"
    gw_dir.mkdir(parents=True)
    (gw_dir / "metadata.json").write_text("not valid json{{{")

    with pytest.raises(GatewayNotConnectedError, match="Failed to load metadata"):
        ShoreGuardClient.from_active_cluster(cluster="my-gw")


def test_from_active_cluster_missing_endpoint_key(tmp_path, monkeypatch):
    """from_active_cluster raises GatewayNotConnectedError when endpoint key is missing."""
    import json

    from shoreguard.client import ShoreGuardClient
    from shoreguard.exceptions import GatewayNotConnectedError

    monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: tmp_path)
    gw_dir = tmp_path / "gateways" / "my-gw"
    gw_dir.mkdir(parents=True)
    (gw_dir / "metadata.json").write_text(json.dumps({"some_other_key": "value"}))

    with pytest.raises(GatewayNotConnectedError, match="Missing 'gateway_endpoint'"):
        ShoreGuardClient.from_active_cluster(cluster="my-gw")
