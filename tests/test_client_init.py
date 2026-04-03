"""Unit tests for ShoreGuardClient — constructor, methods, lifecycle."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from shoreguard.client import ShoreGuardClient, _resolve_active_cluster
from shoreguard.exceptions import GatewayNotConnectedError

# ─── FakeStubs for gRPC methods ──────────────────────────────────────────────


class _FakeOpenShellStub:
    def __init__(self):
        self.request = None

    def Health(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(status=1, version="1.2.3")

    def GetGatewayConfig(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(
            settings={
                "log_level": SimpleNamespace(
                    WhichOneof=lambda _: "string_value", string_value="info"
                ),
                "debug": SimpleNamespace(WhichOneof=lambda _: "bool_value", bool_value=True),
                "max_retries": SimpleNamespace(WhichOneof=lambda _: "int_value", int_value=5),
                "cert_data": SimpleNamespace(
                    WhichOneof=lambda _: "bytes_value", bytes_value=b"cert"
                ),
            },
            settings_revision=7,
        )


class _FakeInferenceStub:
    def __init__(self):
        self.request = None

    def GetClusterInference(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(
            provider_name="anthropic",
            model_id="claude-sonnet-4-20250514",
            version=2,
            route_name="default",
            timeout_secs=30,
        )

    def SetClusterInference(self, req, timeout=None):
        self.request = req
        resp = SimpleNamespace(
            provider_name=req.provider_name,
            model_id=req.model_id,
            version=3,
            route_name="default",
            timeout_secs=req.timeout_secs if hasattr(req, "timeout_secs") else 0,
        )
        resp.validation_performed = True
        resp.validated_endpoints = [
            SimpleNamespace(host="api.anthropic.com", port=443, reachable=True, error=""),
        ]
        return resp


@pytest.fixture
def client():
    """Create a client with fake stubs (no real gRPC channel)."""
    c = object.__new__(ShoreGuardClient)
    c._endpoint = "localhost:8080"
    c._timeout = 30.0
    c._channel = MagicMock()
    c._stub = _FakeOpenShellStub()
    c._inference_stub = _FakeInferenceStub()
    c.sandboxes = MagicMock()
    c.policies = MagicMock()
    c.approvals = MagicMock()
    c.providers = MagicMock()
    return c


# ─── Constructor ──────────────────────────────────────────────────────────────


def test_init_insecure(monkeypatch):
    """Constructor without certs creates insecure channel."""
    mock_channel = MagicMock()
    monkeypatch.setattr("grpc.insecure_channel", lambda ep: mock_channel)

    c = ShoreGuardClient("localhost:8080")

    assert c._endpoint == "localhost:8080"
    assert c._channel is mock_channel


def test_init_mtls(tmp_path, monkeypatch):
    """Constructor with certs creates secure channel."""
    ca = tmp_path / "ca.crt"
    cert = tmp_path / "tls.crt"
    key = tmp_path / "tls.key"
    ca.write_bytes(b"ca-data")
    cert.write_bytes(b"cert-data")
    key.write_bytes(b"key-data")

    mock_channel = MagicMock()
    monkeypatch.setattr("grpc.ssl_channel_credentials", lambda **kw: MagicMock())
    monkeypatch.setattr("grpc.secure_channel", lambda ep, creds: mock_channel)

    c = ShoreGuardClient("localhost:443", ca_path=ca, cert_path=cert, key_path=key)

    assert c._channel is mock_channel


# ─── from_active_cluster ─────────────────────────────────────────────────────


def test_from_active_cluster_http(tmp_path, monkeypatch):
    """from_active_cluster parses http endpoint and creates insecure client."""
    config_dir = tmp_path / "openshell"
    gw_dir = config_dir / "gateways" / "my-gw"
    gw_dir.mkdir(parents=True)
    (gw_dir / "metadata.json").write_text(
        json.dumps(
            {
                "gateway_endpoint": "http://127.0.0.1:8080",
            }
        )
    )
    monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: config_dir)
    monkeypatch.setattr("grpc.insecure_channel", lambda ep: MagicMock())

    c = ShoreGuardClient.from_active_cluster(cluster="my-gw")
    assert c._endpoint == "127.0.0.1:8080"


def test_from_active_cluster_https(tmp_path, monkeypatch):
    """from_active_cluster with https loads mTLS certs."""
    config_dir = tmp_path / "openshell"
    gw_dir = config_dir / "gateways" / "my-gw"
    mtls_dir = gw_dir / "mtls"
    mtls_dir.mkdir(parents=True)
    (gw_dir / "metadata.json").write_text(
        json.dumps(
            {
                "gateway_endpoint": "https://myhost:443",
            }
        )
    )
    (mtls_dir / "ca.crt").write_bytes(b"ca")
    (mtls_dir / "tls.crt").write_bytes(b"cert")
    (mtls_dir / "tls.key").write_bytes(b"key")

    monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: config_dir)
    monkeypatch.setattr("grpc.ssl_channel_credentials", lambda **kw: MagicMock())
    monkeypatch.setattr("grpc.secure_channel", lambda ep, creds: MagicMock())

    c = ShoreGuardClient.from_active_cluster(cluster="my-gw")
    assert c._endpoint == "myhost:443"


def test_resolve_active_cluster_env_var(monkeypatch):
    """OPENSHELL_GATEWAY env var overrides config file."""
    monkeypatch.setenv("OPENSHELL_GATEWAY", "env-gw")
    assert _resolve_active_cluster() == "env-gw"


def test_resolve_active_cluster_file(tmp_path, monkeypatch):
    """Reads gateway name from active_gateway file."""
    config_dir = tmp_path / "openshell"
    config_dir.mkdir()
    (config_dir / "active_gateway").write_text("file-gw\n")
    monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: config_dir)
    monkeypatch.delenv("OPENSHELL_GATEWAY", raising=False)

    assert _resolve_active_cluster() == "file-gw"


def test_resolve_active_cluster_empty(tmp_path, monkeypatch):
    """Empty active_gateway file raises GatewayNotConnectedError."""
    config_dir = tmp_path / "openshell"
    config_dir.mkdir()
    (config_dir / "active_gateway").write_text("")
    monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: config_dir)
    monkeypatch.delenv("OPENSHELL_GATEWAY", raising=False)

    with pytest.raises(GatewayNotConnectedError):
        _resolve_active_cluster()


# ─── gRPC methods ─────────────────────────────────────────────────────────────


def test_health(client):
    """health() maps status enum to string and returns version."""
    result = client.health()
    assert result["status"] == "healthy"
    assert result["version"] == "1.2.3"


def test_get_cluster_inference(client):
    """get_cluster_inference() returns provider/model dict."""
    result = client.get_cluster_inference()
    assert result["provider_name"] == "anthropic"
    assert result["model_id"] == "claude-sonnet-4-20250514"
    assert result["version"] == 2
    assert result["route_name"] == "default"
    assert result["timeout_secs"] == 30


def test_get_gateway_config(client):
    """get_gateway_config() unpacks SettingValue oneofs."""
    result = client.get_gateway_config()
    assert result["settings"]["log_level"] == "info"
    assert result["settings"]["debug"] is True
    assert result["settings"]["max_retries"] == 5
    assert result["settings"]["cert_data"] == b"cert"
    assert result["settings_revision"] == 7


def test_set_cluster_inference(client):
    """set_cluster_inference() returns result with validation fields."""
    result = client.set_cluster_inference(
        provider_name="anthropic", model_id="claude-sonnet-4-20250514", verify=True, timeout_secs=45
    )
    assert result["provider_name"] == "anthropic"
    assert result["version"] == 3
    assert result["timeout_secs"] == 45
    assert result["validation_performed"] is True
    assert len(result["validated_endpoints"]) == 1
    assert result["validated_endpoints"][0]["host"] == "api.anthropic.com"
    assert result["validated_endpoints"][0]["reachable"] is True


def test_set_cluster_inference_without_validation(client):
    """set_cluster_inference works when validation fields are absent."""

    # Override with a stub that doesn't have validation fields
    class _NoValidationStub:
        def SetClusterInference(self, req, timeout=None):
            return SimpleNamespace(
                provider_name="openai",
                model_id="gpt-4o",
                version=1,
                route_name="",
                timeout_secs=0,
            )

    client._inference_stub = _NoValidationStub()
    result = client.set_cluster_inference(provider_name="openai", model_id="gpt-4o")
    assert result["provider_name"] == "openai"
    assert "validation_performed" not in result
    assert "validated_endpoints" not in result


# ─── Lifecycle ────────────────────────────────────────────────────────────────


def test_close(client):
    """close() closes the channel."""
    client.close()
    client._channel.close.assert_called_once()


def test_context_manager(client):
    """Context manager calls close on exit."""
    with client as c:
        assert c is client
    client._channel.close.assert_called_once()


# ─── from_credentials ─────────────────────────────────────────────────────────


def test_from_credentials_insecure(monkeypatch):
    """from_credentials without certs creates insecure channel."""
    mock_channel = MagicMock()
    monkeypatch.setattr("grpc.insecure_channel", lambda ep: mock_channel)

    c = ShoreGuardClient.from_credentials("host:8443")

    assert c._endpoint == "host:8443"
    assert c._channel is mock_channel


def test_from_credentials_secure(monkeypatch):
    """from_credentials with all certs creates secure channel."""
    mock_creds = MagicMock()
    mock_channel = MagicMock()
    monkeypatch.setattr("grpc.ssl_channel_credentials", lambda **kw: mock_creds)
    monkeypatch.setattr("grpc.secure_channel", lambda ep, creds: mock_channel)

    c = ShoreGuardClient.from_credentials(
        "host:8443",
        ca_cert=b"ca-data",
        client_cert=b"cert-data",
        client_key=b"key-data",
    )

    assert c._channel is mock_channel


def test_from_credentials_partial_certs_uses_insecure(monkeypatch):
    """from_credentials with only some certs falls back to insecure channel."""
    mock_channel = MagicMock()
    monkeypatch.setattr("grpc.insecure_channel", lambda ep: mock_channel)

    c = ShoreGuardClient.from_credentials("host:8443", ca_cert=b"ca-data")

    assert c._channel is mock_channel
