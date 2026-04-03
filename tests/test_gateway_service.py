"""Tests for GatewayService — connection management, registry-backed discovery."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import grpc
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import shoreguard.services.gateway as gw_module
from shoreguard.exceptions import GatewayNotConnectedError
from shoreguard.models import Base
from shoreguard.services.gateway import GatewayService, _derive_status
from shoreguard.services.registry import GatewayRegistry

GW = "test-gw"


@pytest.fixture(autouse=True)
def _reset_gateway_state():
    """Reset module-level gateway state before each test."""
    gw_module._reset_clients()
    yield
    gw_module._reset_clients()


@pytest.fixture
def registry():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    reg = GatewayRegistry(factory)
    yield reg
    engine.dispose()


@pytest.fixture
def svc(registry):
    return GatewayService(registry)


# ─── _derive_status ─────────────────────────────────────────────────────────


def test_derive_status_connected():
    assert _derive_status(True, None) == "connected"
    assert _derive_status(True, "healthy") == "connected"


def test_derive_status_unreachable():
    assert _derive_status(False, "healthy") == "unreachable"
    assert _derive_status(False, "degraded") == "unreachable"


def test_derive_status_offline():
    assert _derive_status(False, None) == "offline"
    assert _derive_status(False, "unknown") == "offline"
    assert _derive_status(False, "unhealthy") == "offline"


# ─── Connection management ──────────────────────────────────────────────────


def test_get_client_returns_existing(svc):
    """Existing healthy client is returned directly."""
    mock = MagicMock()
    mock.health.return_value = {"status": "healthy"}
    svc.set_client(mock, name=GW)

    result = svc.get_client(name=GW)

    assert result is mock
    mock.health.assert_called_once()


def test_get_client_reconnects_on_health_fail(svc):
    """Failed health check triggers reconnection."""
    old_client = MagicMock()
    old_client.health.side_effect = grpc.RpcError()
    svc.set_client(old_client, name=GW)

    new_client = MagicMock()
    new_client.health.return_value = {"status": "healthy"}

    with patch.object(svc, "_try_connect", return_value=new_client):
        result = svc.get_client(name=GW)

    assert result is new_client
    old_client.close.assert_called_once()


def test_get_client_reconnect_close_fails(svc):
    """Even if close() raises, reconnection proceeds."""
    old_client = MagicMock()
    old_client.health.side_effect = grpc.RpcError()
    old_client.close.side_effect = RuntimeError("close failed")
    svc.set_client(old_client, name=GW)

    new_client = MagicMock()
    new_client.health.return_value = {"status": "ok"}

    with patch.object(svc, "_try_connect", return_value=new_client):
        result = svc.get_client(name=GW)

    assert result is new_client


def test_get_client_backoff_escalation(svc):
    """Backoff doubles after each failure, up to max."""
    with patch.object(svc, "_try_connect", return_value=None):
        with pytest.raises(GatewayNotConnectedError):
            svc.get_client(name=GW)

    entry = gw_module._clients[GW]
    assert entry.backoff == gw_module._BACKOFF_MIN

    # Force past the backoff window
    entry.last_attempt = 0.0
    with patch.object(svc, "_try_connect", return_value=None):
        with pytest.raises(GatewayNotConnectedError):
            svc.get_client(name=GW)

    assert entry.backoff == gw_module._BACKOFF_MIN * gw_module._BACKOFF_FACTOR


def test_backoff_prevents_rapid_reconnect(svc):
    """After failed connection, subsequent calls within backoff raise."""
    with patch.object(svc, "_try_connect", return_value=None):
        with pytest.raises(GatewayNotConnectedError):
            svc.get_client(name=GW)

    with patch.object(svc, "_try_connect") as mock_connect:
        with pytest.raises(GatewayNotConnectedError):
            svc.get_client(name=GW)
        mock_connect.assert_not_called()


def test_set_client_none_pops(svc):
    """set_client(None) removes the gateway entry."""
    svc.set_client(MagicMock(), name=GW)
    assert GW in gw_module._clients
    svc.set_client(None, name=GW)
    assert GW not in gw_module._clients


def test_set_client_creates_entry(svc):
    """set_client for new gateway creates entry."""
    mock = MagicMock()
    svc.set_client(mock, name="new-gw")
    assert gw_module._clients["new-gw"].client is mock
    assert gw_module._clients["new-gw"].backoff == 0.0


def test_reset_backoff_existing(svc):
    """reset_backoff zeroes backoff and last_attempt."""
    entry = gw_module._ClientEntry()
    entry.backoff = 30.0
    entry.last_attempt = 12345.0
    gw_module._clients[GW] = entry

    svc.reset_backoff(name=GW)

    assert entry.backoff == 0.0
    assert entry.last_attempt == 0.0


def test_reset_backoff_nonexistent_is_noop(svc):
    """reset_backoff on unknown gateway does nothing."""
    svc.reset_backoff(name="nonexistent")


# ─── _try_connect ────────────────────────────────────────────────────────────


def test_try_connect_from_registry(svc, registry):
    """_try_connect uses from_credentials when gateway is in registry."""
    registry.register(GW, "10.0.0.1:8443", ca_cert=b"ca", client_cert=b"cert", client_key=b"key")

    mock_client = MagicMock()
    mock_client.health.return_value = {"status": "healthy"}
    with (
        patch("shoreguard.services.gateway.is_private_ip", return_value=False),
        patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_credentials",
            return_value=mock_client,
        ) as mock_factory,
    ):
        result = svc._try_connect(GW)

    assert result is mock_client
    mock_factory.assert_called_once_with(
        "10.0.0.1:8443", ca_cert=b"ca", client_cert=b"cert", client_key=b"key"
    )


def test_try_connect_fallback_to_config(svc, monkeypatch):
    """_try_connect falls back to from_active_cluster when not in registry."""
    mock_client = MagicMock()
    mock_client.health.return_value = {"status": "ok"}
    monkeypatch.setattr(
        "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
        lambda cluster: mock_client,
    )
    result = svc._try_connect(GW)
    assert result is mock_client


def test_try_connect_registry_failure(svc, registry):
    """_try_connect returns None when registry connection fails."""
    registry.register(GW, "10.0.0.1:8443")
    with (
        patch("shoreguard.services.gateway.is_private_ip", return_value=False),
        patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_credentials",
            side_effect=OSError("connection failed"),
        ),
    ):
        result = svc._try_connect(GW)
    assert result is None


def test_try_connect_registry_health_fail(svc, registry):
    """_try_connect returns None when health check fails after connect."""
    registry.register(GW, "10.0.0.1:8443")
    mock_client = MagicMock()
    mock_client.health.side_effect = grpc.RpcError()
    with (
        patch("shoreguard.services.gateway.is_private_ip", return_value=False),
        patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_credentials",
            return_value=mock_client,
        ),
    ):
        result = svc._try_connect(GW)
    assert result is None
    mock_client.close.assert_called_once()


# ─── Registration ────────────────────────────────────────────────────────────


def test_register_creates_gateway(svc):
    """Register adds gateway to registry and returns record."""
    result = svc.register(GW, "10.0.0.1:8443", auth_mode="insecure")
    assert result["name"] == GW
    assert result["endpoint"] == "10.0.0.1:8443"
    assert "connected" in result
    assert "status" in result


def test_register_duplicate_raises(svc):
    """Registering same name twice raises ValueError."""
    svc.register(GW, "10.0.0.1:8443", auth_mode="insecure")
    with pytest.raises(ValueError, match="already registered"):
        svc.register(GW, "10.0.0.2:8443", auth_mode="insecure")


def test_register_with_certs(svc):
    """Register stores certificates."""
    svc.register(
        GW,
        "10.0.0.1:8443",
        ca_cert=b"ca-data",
        client_cert=b"cert-data",
        client_key=b"key-data",
    )
    record = svc._registry.get(GW)
    assert record["has_ca_cert"] is True
    assert record["has_client_cert"] is True
    assert record["has_client_key"] is True
    # Verify raw bytes via get_credentials
    creds = svc._registry.get_credentials(GW)
    assert creds["ca_cert"] == b"ca-data"
    assert creds["client_cert"] == b"cert-data"
    assert creds["client_key"] == b"key-data"


def test_unregister_removes_gateway(svc):
    """Unregister removes gateway from registry."""
    svc.register(GW, "10.0.0.1:8443", auth_mode="insecure")
    assert svc.unregister(GW) is True
    assert svc._registry.get(GW) is None


def test_unregister_nonexistent(svc):
    """Unregister non-existent gateway returns False."""
    assert svc.unregister("nope") is False


def test_unregister_clears_client(svc):
    """Unregister closes any cached client."""
    svc.register(GW, "10.0.0.1:8443", auth_mode="insecure")
    mock_client = MagicMock()
    svc.set_client(mock_client, name=GW)
    svc.unregister(GW)
    assert GW not in gw_module._clients


# ─── test_connection ─────────────────────────────────────────────────────────


def test_test_connection_not_registered(svc):
    """test_connection raises NotFoundError for unknown gateway."""
    from shoreguard.exceptions import NotFoundError

    with pytest.raises(NotFoundError, match="not registered"):
        svc.test_connection("nope")


def test_test_connection_success(svc, registry):
    """test_connection returns health info when connected."""
    registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
    mock_client = MagicMock()
    mock_client.health.return_value = {"status": "healthy", "version": "1.0"}
    svc.set_client(mock_client, name=GW)

    result = svc.test_connection(GW)
    assert result["success"] is True
    assert result["connected"] is True
    assert result["version"] == "1.0"


def test_test_connection_failure(svc, registry):
    """test_connection returns error when connection fails."""
    registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
    with patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("fail")):
        result = svc.test_connection(GW)
    assert result["success"] is False
    assert result["connected"] is False


# ─── List & Info ─────────────────────────────────────────────────────────────


def test_list_all_empty(svc):
    """list_all returns empty list when no gateways registered."""
    assert svc.list_all() == []


def test_list_all_with_gateways(svc, registry):
    """list_all returns all registered gateways with enriched status."""
    registry.register("alpha", "10.0.0.1:8443", auth_mode="insecure")
    registry.register("beta", "10.0.0.2:8443", auth_mode="insecure")

    result = svc.list_all()
    names = [gw["name"] for gw in result]
    assert names == ["alpha", "beta"]
    for gw in result:
        assert "connected" in gw
        assert "status" in gw


def test_list_all_connected_gateway(svc, registry):
    """list_all reports connected status for gateways with cached client."""
    registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
    mock_client = MagicMock()
    svc.set_client(mock_client, name=GW)

    result = svc.list_all()
    assert len(result) == 1
    assert result[0]["connected"] is True
    assert result[0]["status"] == "connected"


def test_list_all_no_cached_client(svc, registry):
    """list_all reports disconnected when no client is cached."""
    registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")

    result = svc.list_all()
    assert result[0]["connected"] is False


def test_get_info_with_name(svc, registry):
    """get_info returns gateway record with live status."""
    registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
    result = svc.get_info(GW)
    assert result["configured"] is True
    assert result["name"] == GW
    assert result["endpoint"] == "10.0.0.1:8443"


def test_get_info_not_registered(svc):
    """get_info for unregistered gateway returns error."""
    result = svc.get_info("unknown")
    assert result["configured"] is False
    assert "not registered" in result["error"]


def test_get_info_connected(svc, registry):
    """get_info shows connected state when client is cached."""
    registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
    mock_client = MagicMock()
    mock_client.health.return_value = {"status": "healthy", "version": "2.0"}
    svc.set_client(mock_client, name=GW)

    result = svc.get_info(GW)
    assert result["connected"] is True
    assert result["version"] == "2.0"


# ─── Config ──────────────────────────────────────────────────────────────────


def test_get_config_delegates_to_client(svc):
    """get_config calls client.get_gateway_config()."""
    mock_client = MagicMock()
    mock_client.health.return_value = {"status": "healthy"}
    mock_client.get_gateway_config.return_value = {"settings": {}}
    svc.set_client(mock_client, name=GW)

    result = svc.get_config(GW)
    mock_client.get_gateway_config.assert_called_once()
    assert result == {"settings": {}}


# ─── Health monitor ──────────────────────────────────────────────────────────


def test_check_all_health_updates_registry(svc, registry):
    """check_all_health probes gateways and updates health in registry."""
    registry.register("gw1", "10.0.0.1:8443", auth_mode="insecure")
    registry.register("gw2", "10.0.0.2:8443", auth_mode="insecure")

    mock_client = MagicMock()
    mock_client.health.return_value = {"status": "healthy"}

    def fake_get_client(name):
        if name == "gw1":
            return mock_client
        raise GatewayNotConnectedError("fail")

    with patch.object(svc, "get_client", side_effect=fake_get_client):
        svc.check_all_health()

    gw1 = registry.get("gw1")
    gw2 = registry.get("gw2")
    assert gw1["last_status"] == "healthy"
    assert gw1["last_seen"] is not None
    assert gw2["last_status"] == "unreachable"


# ─── DNS rebinding protection ─────────────────────────────────────��──────────


def test_try_connect_from_registry_blocks_private_ip(svc, registry, monkeypatch):
    """Connection is blocked when endpoint resolves to private IP (non-local mode)."""
    monkeypatch.delenv("SHOREGUARD_LOCAL_MODE", raising=False)
    registry.register("private-gw", "10.0.0.1:8443", auth_mode="insecure")
    creds = registry.get_credentials("private-gw")
    with patch("shoreguard.services.gateway.is_private_ip", return_value=True):
        result = svc._try_connect_from_registry("private-gw", creds)
    assert result is None


def test_try_connect_from_registry_allows_private_ip_in_local_mode(svc, registry, monkeypatch):
    """Connection to private IP is allowed in local mode."""
    monkeypatch.setenv("SHOREGUARD_LOCAL_MODE", "1")
    registry.register("local-gw", "127.0.0.1:8080", auth_mode="insecure")
    creds = registry.get_credentials("local-gw")
    mock_client = MagicMock()
    mock_client.health.return_value = {"status": "healthy"}
    with patch(
        "shoreguard.services.gateway.ShoreGuardClient.from_credentials",
        return_value=mock_client,
    ):
        result = svc._try_connect_from_registry("local-gw", creds)
    assert result is mock_client


def test_try_connect_from_registry_allows_public_ip(svc, registry):
    """Connection is allowed when endpoint resolves to public IP."""
    registry.register("public-gw", "203.0.113.1:8443", auth_mode="insecure")
    creds = registry.get_credentials("public-gw")
    mock_client = MagicMock()
    mock_client.health.return_value = {"status": "healthy"}
    with (
        patch("shoreguard.services.gateway.is_private_ip", return_value=False),
        patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_credentials",
            return_value=mock_client,
        ),
    ):
        result = svc._try_connect_from_registry("public-gw", creds)
    assert result is mock_client


def test_check_all_health_empty_registry(svc, registry):
    """check_all_health returns early when no gateways are registered."""
    svc.check_all_health()  # Should not raise


def test_check_all_health_consecutive_failures(svc, registry):
    """check_all_health handles multiple failing gateways."""
    registry.register("gw1", "10.0.0.1:8443", auth_mode="insecure")
    registry.register("gw2", "10.0.0.2:8443", auth_mode="insecure")

    with patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("fail")):
        svc.check_all_health()

    gw1 = registry.get("gw1")
    gw2 = registry.get("gw2")
    assert gw1["last_status"] == "unreachable"
    assert gw2["last_status"] == "unreachable"


def test_check_all_health_db_error_does_not_stop_loop(svc, registry):
    """update_health DB failure for one gateway does not skip the rest."""
    from sqlalchemy.exc import OperationalError

    registry.register("gw1", "10.0.0.1:8443", auth_mode="insecure")
    registry.register("gw2", "10.0.0.2:8443", auth_mode="insecure")

    call_count = 0
    original_update_health = registry.update_health

    def failing_update(name, status, ts):
        nonlocal call_count
        call_count += 1
        if name == "gw1":
            raise OperationalError("db locked", None, None)
        original_update_health(name, status, ts)

    with (
        patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("fail")),
        patch.object(registry, "update_health", side_effect=failing_update),
    ):
        svc.check_all_health()

    # Both gateways were attempted despite gw1's DB error
    assert call_count == 2


def test_get_info_disconnects_stale_client(svc, registry):
    """get_info clears client when health check fails."""
    registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
    mock_client = MagicMock()
    mock_client.health.side_effect = grpc.RpcError()
    svc.set_client(mock_client, name=GW)

    result = svc.get_info(GW)
    assert result["connected"] is False
    assert svc.get_cached_client(GW) is None
