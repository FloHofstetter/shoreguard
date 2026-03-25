"""Unit tests for api/deps.py — ContextVar delegation to gateway_service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from shoreguard.api.deps import _current_gateway, get_client, reset_backoff, set_client


def test_get_client_delegates():
    """get_client() reads current gateway from ContextVar and delegates to gateway_service."""
    _current_gateway.set("my-gw")
    with patch("shoreguard.api.deps.gateway_service") as mock_svc:
        mock_svc.get_client.return_value = MagicMock()
        get_client()
        mock_svc.get_client.assert_called_once_with(name="my-gw")


def test_set_client_delegates():
    """set_client() reads current gateway and delegates to gateway_service.set_client."""
    _current_gateway.set("my-gw")
    mock_client = MagicMock()
    with patch("shoreguard.api.deps.gateway_service") as mock_svc:
        set_client(mock_client)
        mock_svc.set_client.assert_called_once_with(mock_client, name="my-gw")


def test_reset_backoff_delegates():
    """reset_backoff() reads current gateway and delegates to gateway_service.reset_backoff."""
    _current_gateway.set("my-gw")
    with patch("shoreguard.api.deps.gateway_service") as mock_svc:
        reset_backoff()
        mock_svc.reset_backoff.assert_called_once_with(name="my-gw")
