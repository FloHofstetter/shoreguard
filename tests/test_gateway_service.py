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
    from shoreguard.settings import get_settings

    gw_cfg = get_settings().gateway
    assert entry.backoff == gw_cfg.backoff_min

    # Force past the backoff window
    entry.last_attempt = 0.0
    with patch.object(svc, "_try_connect", return_value=None):
        with pytest.raises(GatewayNotConnectedError):
            svc.get_client(name=GW)

    assert entry.backoff == gw_cfg.backoff_min * gw_cfg.backoff_factor


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
    """Registering same name twice raises ConflictError."""
    from shoreguard.exceptions import ConflictError

    svc.register(GW, "10.0.0.1:8443", auth_mode="insecure")
    with pytest.raises(ConflictError, match="already registered"):
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


# ═══════════════════════════════════════════════════════════════════════════════
# Additional mutation-killing tests
# ═══════════════════════════════════════════════════════════════════════════════


# ─── _derive_status: exhaustive combinations ─────────────────────────────────


class TestDeriveStatusExhaustive:
    """Cover all branches and boundary values of _derive_status."""

    def test_connected_overrides_any_last_status(self):
        for status in (None, "healthy", "degraded", "unknown", "unreachable", "offline", ""):
            assert _derive_status(True, status) == "connected"

    def test_disconnected_healthy_is_unreachable(self):
        assert _derive_status(False, "healthy") == "unreachable"

    def test_disconnected_degraded_is_unreachable(self):
        assert _derive_status(False, "degraded") == "unreachable"

    def test_disconnected_none_is_offline(self):
        assert _derive_status(False, None) == "offline"

    def test_disconnected_unknown_is_offline(self):
        assert _derive_status(False, "unknown") == "offline"

    def test_disconnected_empty_string_is_offline(self):
        assert _derive_status(False, "") == "offline"

    def test_disconnected_unreachable_is_offline(self):
        """'unreachable' as last_status is NOT in the healthy/degraded set."""
        assert _derive_status(False, "unreachable") == "offline"

    def test_disconnected_random_string_is_offline(self):
        assert _derive_status(False, "something_else") == "offline"

    def test_return_type_is_str(self):
        assert isinstance(_derive_status(True, None), str)
        assert isinstance(_derive_status(False, None), str)
        assert isinstance(_derive_status(False, "healthy"), str)


# ─── _try_connect_from_config: detailed mutation killing ─────────────────────


class TestTryConnectFromConfig:
    """Kill mutations in _try_connect_from_config."""

    def test_success_returns_client(self, svc):
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "healthy"}
        with patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
            return_value=mock_client,
        ):
            result = svc._try_connect_from_config(GW)
        assert result is mock_client
        mock_client.health.assert_called_once()

    def test_from_active_cluster_grpc_error_returns_none(self, svc):
        with patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
            side_effect=grpc.RpcError(),
        ):
            assert svc._try_connect_from_config(GW) is None

    def test_from_active_cluster_oserror_returns_none(self, svc):
        with patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
            side_effect=OSError("fail"),
        ):
            assert svc._try_connect_from_config(GW) is None

    def test_from_active_cluster_connection_error_returns_none(self, svc):
        with patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
            side_effect=ConnectionError("fail"),
        ):
            assert svc._try_connect_from_config(GW) is None

    def test_from_active_cluster_timeout_error_returns_none(self, svc):
        with patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
            side_effect=TimeoutError("fail"),
        ):
            assert svc._try_connect_from_config(GW) is None

    def test_from_active_cluster_key_error_returns_none(self, svc):
        with patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
            side_effect=KeyError("missing"),
        ):
            assert svc._try_connect_from_config(GW) is None

    def test_from_active_cluster_value_error_returns_none(self, svc):
        with patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
            side_effect=ValueError("bad"),
        ):
            assert svc._try_connect_from_config(GW) is None

    def test_from_active_cluster_json_decode_error_returns_none(self, svc):
        import json

        with patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
            side_effect=json.JSONDecodeError("bad", "", 0),
        ):
            assert svc._try_connect_from_config(GW) is None

    def test_from_active_cluster_gateway_not_connected_error_returns_none(self, svc):
        with patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
            side_effect=GatewayNotConnectedError("fail"),
        ):
            assert svc._try_connect_from_config(GW) is None

    def test_health_fail_returns_none_and_closes(self, svc):
        mock_client = MagicMock()
        mock_client.health.side_effect = grpc.RpcError()
        with patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
            return_value=mock_client,
        ):
            result = svc._try_connect_from_config(GW)
        assert result is None
        mock_client.close.assert_called_once()

    def test_health_fail_oserror_returns_none(self, svc):
        mock_client = MagicMock()
        mock_client.health.side_effect = OSError("fail")
        with patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
            return_value=mock_client,
        ):
            result = svc._try_connect_from_config(GW)
        assert result is None
        mock_client.close.assert_called_once()

    def test_health_fail_connection_error_returns_none(self, svc):
        mock_client = MagicMock()
        mock_client.health.side_effect = ConnectionError("fail")
        with patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
            return_value=mock_client,
        ):
            result = svc._try_connect_from_config(GW)
        assert result is None

    def test_health_fail_timeout_error_returns_none(self, svc):
        mock_client = MagicMock()
        mock_client.health.side_effect = TimeoutError("fail")
        with patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
            return_value=mock_client,
        ):
            result = svc._try_connect_from_config(GW)
        assert result is None

    def test_health_fail_close_raises_is_ok(self, svc):
        """Even if close() raises, we still return None."""
        mock_client = MagicMock()
        mock_client.health.side_effect = grpc.RpcError()
        mock_client.close.side_effect = OSError("close fail")
        with patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
            return_value=mock_client,
        ):
            result = svc._try_connect_from_config(GW)
        assert result is None
        mock_client.close.assert_called_once()

    def test_health_return_value_not_used_directly(self, svc):
        """health() is called but its return value only matters for success path."""
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "degraded", "version": "0.1"}
        with patch(
            "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
            return_value=mock_client,
        ):
            result = svc._try_connect_from_config(GW)
        assert result is mock_client


# ─── _try_connect_from_registry: detailed ────────────────────────────────────


class TestTryConnectFromRegistry:
    """Kill mutations in _try_connect_from_registry."""

    def test_endpoint_parsing_with_port(self, svc, registry):
        """Host is extracted by splitting on last colon."""
        registry.register("gw", "203.0.113.5:9443", auth_mode="insecure")
        creds = registry.get_credentials("gw")
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "healthy"}
        with (
            patch("shoreguard.services.gateway.is_private_ip", return_value=False) as ip_check,
            patch(
                "shoreguard.services.gateway.ShoreGuardClient.from_credentials",
                return_value=mock_client,
            ),
        ):
            svc._try_connect_from_registry("gw", creds)
        # is_private_ip should be called with just the host, not host:port
        ip_check.assert_called_once_with("203.0.113.5")

    def test_endpoint_without_port(self, svc, registry):
        """Endpoint without colon: full string passed to is_private_ip."""
        creds = {"endpoint": "myhost", "ca_cert": None, "client_cert": None, "client_key": None}
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "ok"}
        with (
            patch("shoreguard.services.gateway.is_private_ip", return_value=False) as ip_check,
            patch(
                "shoreguard.services.gateway.ShoreGuardClient.from_credentials",
                return_value=mock_client,
            ),
        ):
            svc._try_connect_from_registry("gw", creds)
        ip_check.assert_called_once_with("myhost")

    def test_from_credentials_called_with_bytes_only(self, svc):
        """Non-bytes creds are passed as None to from_credentials."""
        creds = {
            "endpoint": "host:443",
            "ca_cert": "not-bytes",
            "client_cert": "not-bytes",
            "client_key": "not-bytes",
        }
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "ok"}
        with (
            patch("shoreguard.services.gateway.is_private_ip", return_value=False),
            patch(
                "shoreguard.services.gateway.ShoreGuardClient.from_credentials",
                return_value=mock_client,
            ) as mock_fc,
        ):
            svc._try_connect_from_registry("gw", creds)
        mock_fc.assert_called_once_with("host:443", ca_cert=None, client_cert=None, client_key=None)

    def test_from_credentials_called_with_bytes(self, svc):
        """Bytes creds are passed through to from_credentials."""
        creds = {
            "endpoint": "host:443",
            "ca_cert": b"ca",
            "client_cert": b"cert",
            "client_key": b"key",
        }
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "ok"}
        with (
            patch("shoreguard.services.gateway.is_private_ip", return_value=False),
            patch(
                "shoreguard.services.gateway.ShoreGuardClient.from_credentials",
                return_value=mock_client,
            ) as mock_fc,
        ):
            svc._try_connect_from_registry("gw", creds)
        mock_fc.assert_called_once_with(
            "host:443", ca_cert=b"ca", client_cert=b"cert", client_key=b"key"
        )

    def test_from_credentials_missing_optional_creds(self, svc):
        """Missing optional cred keys default to None."""
        creds = {"endpoint": "host:443"}
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "ok"}
        with (
            patch("shoreguard.services.gateway.is_private_ip", return_value=False),
            patch(
                "shoreguard.services.gateway.ShoreGuardClient.from_credentials",
                return_value=mock_client,
            ) as mock_fc,
        ):
            svc._try_connect_from_registry("gw", creds)
        mock_fc.assert_called_once_with("host:443", ca_cert=None, client_cert=None, client_key=None)

    def test_connection_error_returns_none(self, svc):
        creds = {"endpoint": "host:443"}
        with (
            patch("shoreguard.services.gateway.is_private_ip", return_value=False),
            patch(
                "shoreguard.services.gateway.ShoreGuardClient.from_credentials",
                side_effect=ConnectionError("fail"),
            ),
        ):
            assert svc._try_connect_from_registry("gw", creds) is None

    def test_timeout_error_returns_none(self, svc):
        creds = {"endpoint": "host:443"}
        with (
            patch("shoreguard.services.gateway.is_private_ip", return_value=False),
            patch(
                "shoreguard.services.gateway.ShoreGuardClient.from_credentials",
                side_effect=TimeoutError("fail"),
            ),
        ):
            assert svc._try_connect_from_registry("gw", creds) is None

    def test_health_fail_oserror(self, svc):
        creds = {"endpoint": "host:443"}
        mock_client = MagicMock()
        mock_client.health.side_effect = OSError("fail")
        with (
            patch("shoreguard.services.gateway.is_private_ip", return_value=False),
            patch(
                "shoreguard.services.gateway.ShoreGuardClient.from_credentials",
                return_value=mock_client,
            ),
        ):
            assert svc._try_connect_from_registry("gw", creds) is None
        mock_client.close.assert_called_once()

    def test_health_fail_connection_error(self, svc):
        creds = {"endpoint": "host:443"}
        mock_client = MagicMock()
        mock_client.health.side_effect = ConnectionError("fail")
        with (
            patch("shoreguard.services.gateway.is_private_ip", return_value=False),
            patch(
                "shoreguard.services.gateway.ShoreGuardClient.from_credentials",
                return_value=mock_client,
            ),
        ):
            assert svc._try_connect_from_registry("gw", creds) is None

    def test_health_fail_timeout_error(self, svc):
        creds = {"endpoint": "host:443"}
        mock_client = MagicMock()
        mock_client.health.side_effect = TimeoutError("fail")
        with (
            patch("shoreguard.services.gateway.is_private_ip", return_value=False),
            patch(
                "shoreguard.services.gateway.ShoreGuardClient.from_credentials",
                return_value=mock_client,
            ),
        ):
            assert svc._try_connect_from_registry("gw", creds) is None

    def test_health_fail_close_raises_grpc(self, svc):
        creds = {"endpoint": "host:443"}
        mock_client = MagicMock()
        mock_client.health.side_effect = grpc.RpcError()
        mock_client.close.side_effect = grpc.RpcError()
        with (
            patch("shoreguard.services.gateway.is_private_ip", return_value=False),
            patch(
                "shoreguard.services.gateway.ShoreGuardClient.from_credentials",
                return_value=mock_client,
            ),
        ):
            assert svc._try_connect_from_registry("gw", creds) is None

    def test_health_fail_close_raises_oserror(self, svc):
        creds = {"endpoint": "host:443"}
        mock_client = MagicMock()
        mock_client.health.side_effect = grpc.RpcError()
        mock_client.close.side_effect = OSError("close fail")
        with (
            patch("shoreguard.services.gateway.is_private_ip", return_value=False),
            patch(
                "shoreguard.services.gateway.ShoreGuardClient.from_credentials",
                return_value=mock_client,
            ),
        ):
            assert svc._try_connect_from_registry("gw", creds) is None


# ─── check_all_health: detailed mutation killing ────────────────────────────


class TestCheckAllHealth:
    """Kill mutations in check_all_health."""

    def test_empty_registry_returns_early(self, svc, registry):
        """No gateways: should not call get_client at all."""
        with patch.object(svc, "get_client") as mock_gc:
            svc.check_all_health()
        mock_gc.assert_not_called()

    def test_healthy_status_extracted_from_health_dict(self, svc, registry):
        """The status key from health() dict is used for update_health."""
        registry.register("gw1", "10.0.0.1:8443", auth_mode="insecure")
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "degraded"}

        with (
            patch.object(svc, "get_client", return_value=mock_client),
            patch.object(registry, "update_health") as mock_uh,
        ):
            svc.check_all_health()

        assert mock_uh.call_count == 1
        call_args = mock_uh.call_args
        assert call_args[0][0] == "gw1"
        assert call_args[0][1] == "degraded"

    def test_missing_status_key_defaults_to_unknown(self, svc, registry):
        """If health() returns dict without 'status', default to 'unknown'."""
        registry.register("gw1", "10.0.0.1:8443", auth_mode="insecure")
        mock_client = MagicMock()
        mock_client.health.return_value = {}

        with (
            patch.object(svc, "get_client", return_value=mock_client),
            patch.object(registry, "update_health") as mock_uh,
        ):
            svc.check_all_health()

        assert mock_uh.call_args[0][1] == "unknown"

    def test_grpc_error_sets_unreachable(self, svc, registry):
        registry.register("gw1", "10.0.0.1:8443", auth_mode="insecure")

        with (
            patch.object(svc, "get_client", side_effect=grpc.RpcError()),
            patch.object(registry, "update_health") as mock_uh,
        ):
            svc.check_all_health()

        assert mock_uh.call_args[0][1] == "unreachable"

    def test_gateway_not_connected_sets_unreachable(self, svc, registry):
        registry.register("gw1", "10.0.0.1:8443", auth_mode="insecure")

        with (
            patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("fail")),
            patch.object(registry, "update_health") as mock_uh,
        ):
            svc.check_all_health()

        assert mock_uh.call_args[0][1] == "unreachable"

    def test_update_health_called_with_datetime(self, svc, registry):
        from datetime import datetime

        registry.register("gw1", "10.0.0.1:8443", auth_mode="insecure")

        with (
            patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("fail")),
            patch.object(registry, "update_health") as mock_uh,
        ):
            svc.check_all_health()

        ts = mock_uh.call_args[0][2]
        assert isinstance(ts, datetime)
        assert ts.tzinfo is not None

    def test_all_gateways_are_probed(self, svc, registry):
        """Every registered gateway is probed, not just the first."""
        registry.register("gw1", "10.0.0.1:8443", auth_mode="insecure")
        registry.register("gw2", "10.0.0.2:8443", auth_mode="insecure")
        registry.register("gw3", "10.0.0.3:8443", auth_mode="insecure")

        probed = []

        def track_get_client(name):
            probed.append(name)
            raise GatewayNotConnectedError("fail")

        with patch.object(svc, "get_client", side_effect=track_get_client):
            svc.check_all_health()

        assert probed == ["gw1", "gw2", "gw3"]

    def test_name_extracted_from_gateway_dict(self, svc, registry):
        """The 'name' key from each gateway dict is used for get_client."""
        registry.register("my-gw", "10.0.0.1:8443", auth_mode="insecure")

        with (
            patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("f")) as mock_gc,
            patch.object(registry, "update_health"),
        ):
            svc.check_all_health()

        mock_gc.assert_called_once_with(name="my-gw")


# ─── set_client: state mutation assertions ───────────────────────────────────


class TestSetClient:
    """Kill mutations in set_client."""

    def test_set_client_stores_exact_client(self, svc):
        mock = MagicMock()
        svc.set_client(mock, name=GW)
        assert gw_module._clients[GW].client is mock

    def test_set_client_resets_backoff_to_zero(self, svc):
        """Setting a client must reset backoff to exactly 0.0."""
        entry = gw_module._ClientEntry()
        entry.backoff = 99.0
        entry.client = None
        gw_module._clients[GW] = entry

        mock = MagicMock()
        svc.set_client(mock, name=GW)
        assert gw_module._clients[GW].backoff == 0.0

    def test_set_client_none_removes_entry_completely(self, svc):
        """set_client(None) must pop the entry, not just set client=None."""
        svc.set_client(MagicMock(), name=GW)
        assert GW in gw_module._clients
        svc.set_client(None, name=GW)
        assert GW not in gw_module._clients

    def test_set_client_none_on_missing_is_noop(self, svc):
        """set_client(None) on nonexistent gateway doesn't raise."""
        svc.set_client(None, name="nonexistent")
        assert "nonexistent" not in gw_module._clients

    def test_set_client_overwrites_existing(self, svc):
        """Setting a new client replaces the old one."""
        mock1 = MagicMock()
        mock2 = MagicMock()
        svc.set_client(mock1, name=GW)
        svc.set_client(mock2, name=GW)
        assert gw_module._clients[GW].client is mock2

    def test_set_client_creates_new_entry_if_needed(self, svc):
        """Setting client for unknown gateway creates new _ClientEntry."""
        assert "brand-new" not in gw_module._clients
        mock = MagicMock()
        svc.set_client(mock, name="brand-new")
        entry = gw_module._clients["brand-new"]
        assert entry.client is mock
        assert entry.backoff == 0.0

    def test_set_client_preserves_other_gateways(self, svc):
        mock1 = MagicMock()
        mock2 = MagicMock()
        svc.set_client(mock1, name="gw-a")
        svc.set_client(mock2, name="gw-b")
        svc.set_client(None, name="gw-a")
        assert "gw-a" not in gw_module._clients
        assert gw_module._clients["gw-b"].client is mock2


# ─── reset_backoff: detailed ─────────────────────────────────────────────────


class TestResetBackoff:
    """Kill mutations in reset_backoff."""

    def test_resets_backoff_to_zero(self, svc):
        entry = gw_module._ClientEntry()
        entry.backoff = 60.0
        gw_module._clients[GW] = entry
        svc.reset_backoff(name=GW)
        assert entry.backoff == 0.0

    def test_resets_last_attempt_to_zero(self, svc):
        entry = gw_module._ClientEntry()
        entry.last_attempt = 999.0
        gw_module._clients[GW] = entry
        svc.reset_backoff(name=GW)
        assert entry.last_attempt == 0.0

    def test_noop_for_missing_gateway(self, svc):
        """No error when gateway not in _clients."""
        svc.reset_backoff(name="missing")

    def test_noop_for_empty_string(self, svc):
        """Empty name should be a no-op (guard: `if gw_name and ...`)."""
        svc.reset_backoff(name="")

    def test_both_fields_reset_together(self, svc):
        entry = gw_module._ClientEntry()
        entry.backoff = 30.0
        entry.last_attempt = 500.0
        gw_module._clients[GW] = entry
        svc.reset_backoff(name=GW)
        assert entry.backoff == 0.0
        assert entry.last_attempt == 0.0

    def test_does_not_remove_entry(self, svc):
        """reset_backoff should NOT remove the entry from _clients."""
        entry = gw_module._ClientEntry()
        entry.backoff = 10.0
        entry.client = MagicMock()
        gw_module._clients[GW] = entry
        svc.reset_backoff(name=GW)
        assert GW in gw_module._clients
        assert gw_module._clients[GW].client is not None


# ─── test_connection: detailed ───────────────────────────────────────────────


class TestTestConnection:
    """Kill mutations in test_connection."""

    def test_not_found_raises(self, svc):
        from shoreguard.exceptions import NotFoundError

        with pytest.raises(NotFoundError, match="not registered"):
            svc.test_connection("unknown")

    def test_success_returns_exact_keys(self, svc, registry):
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "healthy", "version": "1.2.3"}
        svc.set_client(mock_client, name=GW)

        result = svc.test_connection(GW)
        assert result == {
            "success": True,
            "connected": True,
            "version": "1.2.3",
            "health_status": "healthy",
        }

    def test_success_with_missing_version(self, svc, registry):
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "ok"}
        svc.set_client(mock_client, name=GW)

        result = svc.test_connection(GW)
        assert result["success"] is True
        assert result["version"] is None
        assert result["health_status"] == "ok"

    def test_success_with_missing_status(self, svc, registry):
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        mock_client = MagicMock()
        mock_client.health.return_value = {"version": "2.0"}
        svc.set_client(mock_client, name=GW)

        result = svc.test_connection(GW)
        assert result["success"] is True
        assert result["health_status"] is None

    def test_failure_returns_exact_keys(self, svc, registry):
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        with patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("test error")):
            result = svc.test_connection(GW)
        assert result == {
            "success": False,
            "connected": False,
            "error": "test error",
        }

    def test_failure_grpc_error(self, svc, registry):
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        with patch.object(svc, "get_client", side_effect=grpc.RpcError()):
            result = svc.test_connection(GW)
        assert result["success"] is False
        assert result["connected"] is False
        assert "error" in result

    def test_calls_reset_backoff(self, svc, registry):
        """test_connection resets backoff before trying to connect."""
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        with (
            patch.object(svc, "reset_backoff") as mock_rb,
            patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("f")),
        ):
            svc.test_connection(GW)
        mock_rb.assert_called_once_with(GW)


# ─── get_info: detailed ─────────────────────────────────────────────────────


class TestGetInfo:
    """Kill mutations in get_info."""

    def test_not_registered_returns_exact_dict(self, svc):
        result = svc.get_info("nope")
        assert result == {"configured": False, "error": "Gateway 'nope' not registered"}

    def test_registered_no_client_returns_configured(self, svc, registry):
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        result = svc.get_info(GW)
        assert result["configured"] is True
        assert result["connected"] is False
        assert "version" not in result

    def test_registered_with_healthy_client(self, svc, registry):
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "healthy", "version": "3.0"}
        svc.set_client(mock_client, name=GW)

        result = svc.get_info(GW)
        assert result["configured"] is True
        assert result["connected"] is True
        assert result["version"] == "3.0"
        assert result["status"] == "connected"

    def test_version_not_set_when_none(self, svc, registry):
        """If version is falsy (None), it should NOT be in the result."""
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "ok"}
        svc.set_client(mock_client, name=GW)

        result = svc.get_info(GW)
        assert result["connected"] is True
        assert "version" not in result

    def test_version_not_set_when_empty_string(self, svc, registry):
        """Empty string version is falsy, should NOT appear."""
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "ok", "version": ""}
        svc.set_client(mock_client, name=GW)

        result = svc.get_info(GW)
        assert "version" not in result

    def test_stale_client_clears_and_returns_disconnected(self, svc, registry):
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        mock_client = MagicMock()
        mock_client.health.side_effect = grpc.RpcError()
        svc.set_client(mock_client, name=GW)

        result = svc.get_info(GW)
        assert result["connected"] is False
        assert GW not in gw_module._clients

    def test_status_uses_derive_status(self, svc, registry):
        """Status is derived from connected + last_status."""
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        # Update health to "healthy" then check disconnected -> unreachable
        from datetime import UTC, datetime

        registry.update_health(GW, "healthy", datetime.now(UTC))

        result = svc.get_info(GW)
        assert result["connected"] is False
        assert result["status"] == "unreachable"

    def test_status_offline_when_no_last_status(self, svc, registry):
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        result = svc.get_info(GW)
        assert result["status"] == "offline"


# ─── list_all: detailed ─────────────────────────────────────────────────────


class TestListAll:
    """Kill mutations in list_all."""

    def test_empty_returns_empty_list(self, svc):
        result = svc.list_all()
        assert result == []
        assert isinstance(result, list)

    def test_connected_gateway_status(self, svc, registry):
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        svc.set_client(MagicMock(), name=GW)
        result = svc.list_all()
        assert result[0]["connected"] is True
        assert result[0]["status"] == "connected"

    def test_disconnected_gateway_no_last_status(self, svc, registry):
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        result = svc.list_all()
        assert result[0]["connected"] is False
        assert result[0]["status"] == "offline"

    def test_disconnected_gateway_with_healthy_last_status(self, svc, registry):
        from datetime import UTC, datetime

        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        registry.update_health(GW, "healthy", datetime.now(UTC))
        result = svc.list_all()
        assert result[0]["connected"] is False
        assert result[0]["status"] == "unreachable"

    def test_labels_filter_passed_through(self, svc, registry):
        registry.register("gw1", "10.0.0.1:8443", auth_mode="insecure", labels={"env": "prod"})
        registry.register("gw2", "10.0.0.2:8443", auth_mode="insecure", labels={"env": "dev"})
        result = svc.list_all(labels_filter={"env": "prod"})
        assert len(result) == 1
        assert result[0]["name"] == "gw1"

    def test_each_gateway_gets_connected_and_status(self, svc, registry):
        registry.register("a", "10.0.0.1:8443", auth_mode="insecure")
        registry.register("b", "10.0.0.2:8443", auth_mode="insecure")
        svc.set_client(MagicMock(), name="a")

        result = svc.list_all()
        by_name = {gw["name"]: gw for gw in result}
        assert by_name["a"]["connected"] is True
        assert by_name["a"]["status"] == "connected"
        assert by_name["b"]["connected"] is False


# ─── get_cached_client: detailed ─────────────────────────────────────────────


class TestGetCachedClient:
    """Kill mutations in get_cached_client."""

    def test_returns_none_when_no_entry(self, svc):
        assert svc.get_cached_client("missing") is None

    def test_returns_none_when_client_is_none(self, svc):
        entry = gw_module._ClientEntry()
        entry.client = None
        gw_module._clients[GW] = entry
        assert svc.get_cached_client(GW) is None

    def test_returns_exact_client(self, svc):
        mock = MagicMock()
        entry = gw_module._ClientEntry()
        entry.client = mock
        gw_module._clients[GW] = entry
        assert svc.get_cached_client(GW) is mock

    def test_does_not_modify_state(self, svc):
        mock = MagicMock()
        entry = gw_module._ClientEntry()
        entry.client = mock
        entry.backoff = 42.0
        gw_module._clients[GW] = entry
        svc.get_cached_client(GW)
        assert entry.backoff == 42.0
        assert entry.client is mock


# ─── get_client: backoff detailed ────────────────────────────────────────────


class TestGetClientBackoff:
    """Detailed backoff behavior tests to kill mutations."""

    def test_initial_backoff_is_backoff_min(self, svc):
        from shoreguard.settings import get_settings

        cfg = get_settings().gateway
        with patch.object(svc, "_try_connect", return_value=None):
            with pytest.raises(GatewayNotConnectedError):
                svc.get_client(name=GW)
        assert gw_module._clients[GW].backoff == cfg.backoff_min

    def test_second_failure_doubles_backoff(self, svc):
        from shoreguard.settings import get_settings

        cfg = get_settings().gateway
        with patch.object(svc, "_try_connect", return_value=None):
            with pytest.raises(GatewayNotConnectedError):
                svc.get_client(name=GW)
        # Force past backoff
        gw_module._clients[GW].last_attempt = 0.0
        with patch.object(svc, "_try_connect", return_value=None):
            with pytest.raises(GatewayNotConnectedError):
                svc.get_client(name=GW)
        assert gw_module._clients[GW].backoff == cfg.backoff_min * cfg.backoff_factor

    def test_backoff_caps_at_max(self, svc):
        from shoreguard.settings import get_settings

        cfg = get_settings().gateway
        with patch.object(svc, "_try_connect", return_value=None):
            with pytest.raises(GatewayNotConnectedError):
                svc.get_client(name=GW)

        # Set backoff close to max
        gw_module._clients[GW].backoff = cfg.backoff_max
        gw_module._clients[GW].last_attempt = 0.0

        with patch.object(svc, "_try_connect", return_value=None):
            with pytest.raises(GatewayNotConnectedError):
                svc.get_client(name=GW)

        assert gw_module._clients[GW].backoff == cfg.backoff_max

    def test_successful_reconnect_clears_backoff(self, svc):
        """After backoff, successful reconnect resets backoff to 0."""
        with patch.object(svc, "_try_connect", return_value=None):
            with pytest.raises(GatewayNotConnectedError):
                svc.get_client(name=GW)

        assert gw_module._clients[GW].backoff > 0

        # Force past backoff
        gw_module._clients[GW].last_attempt = 0.0
        new_client = MagicMock()
        new_client.health.return_value = {"status": "ok"}
        with patch.object(svc, "_try_connect", return_value=new_client):
            result = svc.get_client(name=GW)

        assert result is new_client
        assert gw_module._clients[GW].backoff == 0.0
        assert gw_module._clients[GW].client is new_client

    def test_within_backoff_window_skips_connect(self, svc):
        """Within backoff window, _try_connect is never called."""
        with patch.object(svc, "_try_connect", return_value=None):
            with pytest.raises(GatewayNotConnectedError):
                svc.get_client(name=GW)

        # Don't reset last_attempt -> within backoff window
        with patch.object(svc, "_try_connect") as mock_tc:
            with pytest.raises(GatewayNotConnectedError):
                svc.get_client(name=GW)
            mock_tc.assert_not_called()

    def test_last_attempt_updated_on_each_try(self, svc):
        import time

        before = time.monotonic()
        with patch.object(svc, "_try_connect", return_value=None):
            with pytest.raises(GatewayNotConnectedError):
                svc.get_client(name=GW)
        after = time.monotonic()
        assert before <= gw_module._clients[GW].last_attempt <= after


# ─── register: connection attempt ────────────────────────────────────────────


class TestRegister:
    """Kill mutations in register."""

    def test_register_connected_true(self, svc):
        mock_client = MagicMock()
        mock_client.health.return_value = {"status": "ok"}

        with patch.object(svc, "_try_connect", return_value=mock_client):
            result = svc.register("rg", "host:443", auth_mode="insecure")

        assert result["connected"] is True
        assert result["status"] == "connected"

    def test_register_connected_false(self, svc):
        result = svc.register("rg", "host:443", auth_mode="insecure")
        assert result["connected"] is False
        assert result["status"] == "unreachable"

    def test_register_returns_name_and_endpoint(self, svc):
        result = svc.register("myname", "myhost:443", auth_mode="insecure")
        assert result["name"] == "myname"
        assert result["endpoint"] == "myhost:443"


# ─── unregister: detailed ───────────────────────────────────────────────────


class TestUnregister:
    """Kill mutations in unregister."""

    def test_unregister_clears_client_first(self, svc, registry):
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        mock = MagicMock()
        svc.set_client(mock, name=GW)
        assert GW in gw_module._clients

        result = svc.unregister(GW)
        assert result is True
        assert GW not in gw_module._clients
        assert registry.get(GW) is None

    def test_unregister_returns_false_for_missing(self, svc):
        assert svc.unregister("nope") is False

    def test_unregister_returns_true_for_existing(self, svc, registry):
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        assert svc.unregister(GW) is True

    def test_unregister_delegates_to_registry(self, svc, registry):
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        with patch.object(registry, "unregister", return_value=True) as mock_unreg:
            result = svc.unregister(GW)
        assert result is True
        mock_unreg.assert_called_once_with(GW)


# ─── update_gateway_metadata ─────────────────────────────────────────────────


class TestUpdateGatewayMetadata:
    """Kill mutations in update_gateway_metadata."""

    def test_not_found_raises(self, svc):
        from shoreguard.exceptions import NotFoundError

        with pytest.raises(NotFoundError, match="not found"):
            svc.update_gateway_metadata("nope", description="x")

    def test_update_description(self, svc, registry):
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        result = svc.update_gateway_metadata(GW, description="new desc")
        assert result["description"] == "new desc"

    def test_update_labels(self, svc, registry):
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure")
        result = svc.update_gateway_metadata(GW, labels={"env": "prod"})
        assert result["labels"] == {"env": "prod"}

    def test_unset_skips_fields(self, svc, registry):
        """When using default _UNSET, fields are not passed to registry."""
        registry.register(GW, "10.0.0.1:8443", auth_mode="insecure", description="orig")
        result = svc.update_gateway_metadata(GW)
        assert result["description"] == "orig"


# ─── _ClientEntry defaults ───────────────────────────────────────────────────


class TestClientEntry:
    """Verify _ClientEntry initialization."""

    def test_defaults(self):
        entry = gw_module._ClientEntry()
        assert entry.client is None
        assert entry.last_attempt == 0.0
        assert entry.backoff == 0.0

    def test_slots(self):
        entry = gw_module._ClientEntry()
        with pytest.raises(AttributeError):
            entry.nonexistent = "x"


# ─── _reset_clients ─────────────────────────────────────────────────────────


class TestResetClients:
    def test_clears_all(self):
        gw_module._clients["a"] = gw_module._ClientEntry()
        gw_module._clients["b"] = gw_module._ClientEntry()
        gw_module._reset_clients()
        assert len(gw_module._clients) == 0


# ─── registry property ──────────────────────────────────────────────────────


class TestRegistryProperty:
    def test_returns_registry(self, svc, registry):
        assert svc.registry is registry
