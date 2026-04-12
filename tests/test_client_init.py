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

    def GetInferenceBundle(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(
            revision="rev-42",
            generated_at_ms=1700000000000,
            routes=[
                SimpleNamespace(
                    name="default",
                    base_url="https://api.anthropic.com",
                    protocols=["https"],
                    api_key="sk-ant-secret",  # pragma: allowlist secret
                    model_id="claude-sonnet-4",
                    provider_type="anthropic",
                    timeout_secs=60,
                ),
                SimpleNamespace(
                    name="cheap",
                    base_url="https://api.openai.com",
                    protocols=["https"],
                    api_key="",
                    model_id="gpt-4o-mini",
                    provider_type="openai",
                    timeout_secs=30,
                ),
            ],
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
    c._stub = _FakeOpenShellStub()  # type: ignore[assignment]
    c._inference_stub = _FakeInferenceStub()  # type: ignore[assignment]
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


def test_get_inference_bundle(client):
    """get_inference_bundle() returns redacted routes + revision."""
    result = client.get_inference_bundle()
    assert result["revision"] == "rev-42"
    assert result["generated_at_ms"] == 1700000000000
    assert len(result["routes"]) == 2

    secret_route = result["routes"][0]
    assert secret_route["name"] == "default"
    assert secret_route["base_url"] == "https://api.anthropic.com"
    assert secret_route["protocols"] == ["https"]
    assert secret_route["model_id"] == "claude-sonnet-4"
    assert secret_route["provider_type"] == "anthropic"
    assert secret_route["timeout_secs"] == 60
    assert secret_route["has_api_key"] is True
    # Critical: raw secret never crosses the wrapper boundary.
    assert "api_key" not in secret_route

    empty_route = result["routes"][1]
    assert empty_route["has_api_key"] is False
    assert "api_key" not in empty_route


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


# ─── Mutation-killing tests ─────────────────────────────────────────────────


class TestHealthStatusMapping:
    """Kill mutations in the status_names dict lookup in health()."""

    @pytest.mark.parametrize(
        "code,expected",
        [
            (0, "unspecified"),
            (1, "healthy"),
            (2, "degraded"),
            (3, "unhealthy"),
            (99, "unknown"),
        ],
    )
    def test_health_status_codes(self, code, expected, monkeypatch):
        c = object.__new__(ShoreGuardClient)
        c._endpoint = "localhost:8080"
        c._timeout = 30.0
        c._channel = MagicMock()
        c._stub = MagicMock()
        c._stub.Health.return_value = SimpleNamespace(status=code, version="v")
        result = c.health()
        assert result["status"] == expected
        assert result["version"] == "v"

    def test_health_uses_timeout(self, monkeypatch):
        c = object.__new__(ShoreGuardClient)
        c._endpoint = "localhost:8080"
        c._timeout = 42.0
        c._channel = MagicMock()
        c._stub = MagicMock()
        c._stub.Health.return_value = SimpleNamespace(status=1, version="v")
        c.health()
        c._stub.Health.assert_called_once()
        _, kwargs = c._stub.Health.call_args
        assert kwargs["timeout"] == 42.0


class TestGetClusterInferenceMutations:
    """Kill mutations in get_cluster_inference return dict keys/values."""

    def test_route_name_forwarded(self, monkeypatch):
        c = object.__new__(ShoreGuardClient)
        c._timeout = 5.0
        c._inference_stub = MagicMock()
        c._inference_stub.GetClusterInference.return_value = SimpleNamespace(
            provider_name="p", model_id="m", version=1, route_name="r", timeout_secs=10
        )
        result = c.get_cluster_inference(route_name="custom")
        req = c._inference_stub.GetClusterInference.call_args[0][0]
        assert req.route_name == "custom"
        assert result == {
            "provider_name": "p",
            "model_id": "m",
            "version": 1,
            "route_name": "r",
            "timeout_secs": 10,
        }

    def test_default_route_name_empty(self):
        c = object.__new__(ShoreGuardClient)
        c._timeout = 5.0
        c._inference_stub = MagicMock()
        c._inference_stub.GetClusterInference.return_value = SimpleNamespace(
            provider_name="p", model_id="m", version=1, route_name="", timeout_secs=0
        )
        c.get_cluster_inference()
        req = c._inference_stub.GetClusterInference.call_args[0][0]
        assert req.route_name == ""


class TestSetClusterInferenceMutations:
    """Kill mutations in set_cluster_inference request building and response dict."""

    def test_verify_true_sets_no_verify_false(self):
        c = object.__new__(ShoreGuardClient)
        c._timeout = 5.0
        c._inference_stub = MagicMock()
        resp = SimpleNamespace(
            provider_name="p", model_id="m", version=1, route_name="", timeout_secs=0
        )
        # no validation fields
        c._inference_stub.SetClusterInference.return_value = resp
        c.set_cluster_inference(provider_name="p", model_id="m", verify=True)
        req = c._inference_stub.SetClusterInference.call_args[0][0]
        assert req.verify is True
        assert req.no_verify is False

    def test_verify_false_sets_no_verify_true(self):
        c = object.__new__(ShoreGuardClient)
        c._timeout = 5.0
        c._inference_stub = MagicMock()
        resp = SimpleNamespace(
            provider_name="p", model_id="m", version=1, route_name="", timeout_secs=0
        )
        c._inference_stub.SetClusterInference.return_value = resp
        c.set_cluster_inference(provider_name="p", model_id="m", verify=False)
        req = c._inference_stub.SetClusterInference.call_args[0][0]
        assert req.verify is False
        assert req.no_verify is True

    def test_route_name_and_timeout_forwarded(self):
        c = object.__new__(ShoreGuardClient)
        c._timeout = 5.0
        c._inference_stub = MagicMock()
        resp = SimpleNamespace(
            provider_name="p", model_id="m", version=1, route_name="r", timeout_secs=120
        )
        c._inference_stub.SetClusterInference.return_value = resp
        result = c.set_cluster_inference(
            provider_name="p", model_id="m", route_name="r", timeout_secs=120
        )
        req = c._inference_stub.SetClusterInference.call_args[0][0]
        assert req.route_name == "r"
        assert req.timeout_secs == 120
        assert result["route_name"] == "r"
        assert result["timeout_secs"] == 120

    def test_validated_endpoints_fields(self):
        c = object.__new__(ShoreGuardClient)
        c._timeout = 5.0
        c._inference_stub = MagicMock()
        resp = SimpleNamespace(
            provider_name="p",
            model_id="m",
            version=1,
            route_name="",
            timeout_secs=0,
            validation_performed=True,
            validated_endpoints=[
                SimpleNamespace(host="h1", port=443, reachable=True, error=""),
                SimpleNamespace(host="h2", port=8080, reachable=False, error="timeout"),
            ],
        )
        c._inference_stub.SetClusterInference.return_value = resp
        result = c.set_cluster_inference(provider_name="p", model_id="m")
        assert result["validation_performed"] is True
        assert len(result["validated_endpoints"]) == 2
        assert result["validated_endpoints"][0] == {
            "host": "h1",
            "port": 443,
            "reachable": True,
            "error": "",
        }
        assert result["validated_endpoints"][1] == {
            "host": "h2",
            "port": 8080,
            "reachable": False,
            "error": "timeout",
        }


class TestGetGatewayConfigMutations:
    """Kill mutations in get_gateway_config oneof handling."""

    def test_unknown_oneof_field_ignored(self):
        """A SettingValue with an unknown oneof type is not included."""
        c = object.__new__(ShoreGuardClient)
        c._timeout = 5.0
        c._stub = MagicMock()
        c._stub.GetGatewayConfig.return_value = SimpleNamespace(
            settings={
                "unknown": SimpleNamespace(WhichOneof=lambda _: "future_value"),
            },
            settings_revision=1,
        )
        result = c.get_gateway_config()
        assert result["settings"] == {}
        assert result["settings_revision"] == 1

    def test_each_value_type_maps_correctly(self):
        c = object.__new__(ShoreGuardClient)
        c._timeout = 5.0
        c._stub = MagicMock()
        c._stub.GetGatewayConfig.return_value = SimpleNamespace(
            settings={
                "s": SimpleNamespace(WhichOneof=lambda _: "string_value", string_value="hello"),
                "b": SimpleNamespace(WhichOneof=lambda _: "bool_value", bool_value=False),
                "i": SimpleNamespace(WhichOneof=lambda _: "int_value", int_value=0),
                "by": SimpleNamespace(WhichOneof=lambda _: "bytes_value", bytes_value=b""),
            },
            settings_revision=99,
        )
        result = c.get_gateway_config()
        assert result["settings"]["s"] == "hello"
        assert result["settings"]["b"] is False
        assert result["settings"]["i"] == 0
        assert result["settings"]["by"] == b""
        assert result["settings_revision"] == 99


class TestFromActiveClusterMutations:
    """Kill mutations in URL parsing, port defaults, error handling."""

    def test_missing_metadata_raises(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "openshell"
        gw_dir = config_dir / "gateways" / "my-gw"
        gw_dir.mkdir(parents=True)
        # No metadata.json
        monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: config_dir)
        with pytest.raises(GatewayNotConnectedError, match="Failed to load metadata"):
            ShoreGuardClient.from_active_cluster(cluster="my-gw")

    def test_invalid_json_raises(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "openshell"
        gw_dir = config_dir / "gateways" / "my-gw"
        gw_dir.mkdir(parents=True)
        (gw_dir / "metadata.json").write_text("not json")
        monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: config_dir)
        with pytest.raises(GatewayNotConnectedError, match="Failed to load metadata"):
            ShoreGuardClient.from_active_cluster(cluster="my-gw")

    def test_empty_gateway_endpoint_raises(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "openshell"
        gw_dir = config_dir / "gateways" / "my-gw"
        gw_dir.mkdir(parents=True)
        (gw_dir / "metadata.json").write_text(json.dumps({"gateway_endpoint": ""}))
        monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: config_dir)
        with pytest.raises(GatewayNotConnectedError, match="Missing 'gateway_endpoint'"):
            ShoreGuardClient.from_active_cluster(cluster="my-gw")

    def test_missing_gateway_endpoint_key_raises(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "openshell"
        gw_dir = config_dir / "gateways" / "my-gw"
        gw_dir.mkdir(parents=True)
        (gw_dir / "metadata.json").write_text(json.dumps({"other_key": "val"}))
        monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: config_dir)
        with pytest.raises(GatewayNotConnectedError, match="Missing 'gateway_endpoint'"):
            ShoreGuardClient.from_active_cluster(cluster="my-gw")

    def test_http_default_port_80(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "openshell"
        gw_dir = config_dir / "gateways" / "my-gw"
        gw_dir.mkdir(parents=True)
        (gw_dir / "metadata.json").write_text(json.dumps({"gateway_endpoint": "http://myhost"}))
        monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: config_dir)
        monkeypatch.setattr("grpc.insecure_channel", lambda ep: MagicMock())
        c = ShoreGuardClient.from_active_cluster(cluster="my-gw")
        assert c._endpoint == "myhost:80"

    def test_https_default_port_443(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "openshell"
        gw_dir = config_dir / "gateways" / "my-gw"
        mtls_dir = gw_dir / "mtls"
        mtls_dir.mkdir(parents=True)
        (gw_dir / "metadata.json").write_text(json.dumps({"gateway_endpoint": "https://myhost"}))
        (mtls_dir / "ca.crt").write_bytes(b"ca")
        (mtls_dir / "tls.crt").write_bytes(b"cert")
        (mtls_dir / "tls.key").write_bytes(b"key")
        monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: config_dir)
        monkeypatch.setattr("grpc.ssl_channel_credentials", lambda **kw: MagicMock())
        monkeypatch.setattr("grpc.secure_channel", lambda ep, creds: MagicMock())
        c = ShoreGuardClient.from_active_cluster(cluster="my-gw")
        assert c._endpoint == "myhost:443"

    def test_no_hostname_defaults_to_localhost(self, tmp_path, monkeypatch):
        """When urlparse yields no hostname, default to 127.0.0.1."""
        config_dir = tmp_path / "openshell"
        gw_dir = config_dir / "gateways" / "my-gw"
        gw_dir.mkdir(parents=True)
        # A URL that parses with no hostname
        (gw_dir / "metadata.json").write_text(json.dumps({"gateway_endpoint": "http://:9090"}))
        monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: config_dir)
        monkeypatch.setattr("grpc.insecure_channel", lambda ep: MagicMock())
        c = ShoreGuardClient.from_active_cluster(cluster="my-gw")
        assert c._endpoint == "127.0.0.1:9090"

    def test_uses_active_cluster_when_no_cluster_arg(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENSHELL_GATEWAY", "env-cluster")
        config_dir = tmp_path / "openshell"
        gw_dir = config_dir / "gateways" / "env-cluster"
        gw_dir.mkdir(parents=True)
        (gw_dir / "metadata.json").write_text(
            json.dumps({"gateway_endpoint": "http://localhost:8080"})
        )
        monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: config_dir)
        monkeypatch.setattr("grpc.insecure_channel", lambda ep: MagicMock())
        c = ShoreGuardClient.from_active_cluster()
        assert c._endpoint == "localhost:8080"

    def test_custom_timeout_forwarded(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "openshell"
        gw_dir = config_dir / "gateways" / "my-gw"
        gw_dir.mkdir(parents=True)
        (gw_dir / "metadata.json").write_text(
            json.dumps({"gateway_endpoint": "http://localhost:8080"})
        )
        monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: config_dir)
        monkeypatch.setattr("grpc.insecure_channel", lambda ep: MagicMock())
        c = ShoreGuardClient.from_active_cluster(cluster="my-gw", timeout=99.0)
        assert c._timeout == 99.0


class TestFromCredentialsMutations:
    """Kill mutations in from_credentials."""

    def test_timeout_stored(self, monkeypatch):
        monkeypatch.setattr("grpc.insecure_channel", lambda ep: MagicMock())
        c = ShoreGuardClient.from_credentials("h:80", timeout=77.0)
        assert c._timeout == 77.0

    def test_endpoint_stored(self, monkeypatch):
        monkeypatch.setattr("grpc.insecure_channel", lambda ep: MagicMock())
        c = ShoreGuardClient.from_credentials("myhost:9090")
        assert c._endpoint == "myhost:9090"

    def test_managers_initialized(self, monkeypatch):
        monkeypatch.setattr("grpc.insecure_channel", lambda ep: MagicMock())
        c = ShoreGuardClient.from_credentials("h:80")
        assert hasattr(c, "sandboxes")
        assert hasattr(c, "policies")
        assert hasattr(c, "approvals")
        assert hasattr(c, "providers")

    def test_secure_passes_correct_bytes(self, monkeypatch):
        captured = {}

        def mock_ssl(**kw):
            captured.update(kw)
            return MagicMock()

        monkeypatch.setattr("grpc.ssl_channel_credentials", mock_ssl)
        monkeypatch.setattr("grpc.secure_channel", lambda ep, creds: MagicMock())
        ShoreGuardClient.from_credentials(
            "h:443", ca_cert=b"CA", client_cert=b"CERT", client_key=b"KEY"
        )
        assert captured["root_certificates"] == b"CA"
        assert captured["private_key"] == b"KEY"
        assert captured["certificate_chain"] == b"CERT"

    def test_partial_certs_only_ca_and_key(self, monkeypatch):
        """Missing client_cert falls back to insecure."""
        monkeypatch.setattr("grpc.insecure_channel", lambda ep: MagicMock())
        c = ShoreGuardClient.from_credentials("h:80", ca_cert=b"ca", client_key=b"key")
        assert c._channel is not None

    def test_partial_certs_only_ca_and_cert(self, monkeypatch):
        """Missing client_key falls back to insecure."""
        monkeypatch.setattr("grpc.insecure_channel", lambda ep: MagicMock())
        c = ShoreGuardClient.from_credentials("h:80", ca_cert=b"ca", client_cert=b"cert")
        assert c._channel is not None


class TestInitMutations:
    """Kill mutations in __init__ constructor."""

    def test_timeout_default(self, monkeypatch):
        monkeypatch.setattr("grpc.insecure_channel", lambda ep: MagicMock())
        c = ShoreGuardClient("h:80")
        assert c._timeout == 30.0

    def test_timeout_custom(self, monkeypatch):
        monkeypatch.setattr("grpc.insecure_channel", lambda ep: MagicMock())
        c = ShoreGuardClient("h:80", timeout=99.0)
        assert c._timeout == 99.0

    def test_managers_created(self, monkeypatch):
        monkeypatch.setattr("grpc.insecure_channel", lambda ep: MagicMock())
        c = ShoreGuardClient("h:80")
        from shoreguard.client.approvals import ApprovalManager
        from shoreguard.client.policies import PolicyManager
        from shoreguard.client.providers import ProviderManager
        from shoreguard.client.sandboxes import SandboxManager

        assert isinstance(c.sandboxes, SandboxManager)
        assert isinstance(c.policies, PolicyManager)
        assert isinstance(c.approvals, ApprovalManager)
        assert isinstance(c.providers, ProviderManager)

    def test_mtls_passes_correct_bytes(self, tmp_path, monkeypatch):
        ca = tmp_path / "ca.crt"
        cert = tmp_path / "tls.crt"
        key = tmp_path / "tls.key"
        ca.write_bytes(b"CA-DATA")
        cert.write_bytes(b"CERT-DATA")
        key.write_bytes(b"KEY-DATA")
        captured = {}

        def mock_ssl(**kw):
            captured.update(kw)
            return MagicMock()

        monkeypatch.setattr("grpc.ssl_channel_credentials", mock_ssl)
        monkeypatch.setattr("grpc.secure_channel", lambda ep, creds: MagicMock())
        ShoreGuardClient("h:443", ca_path=ca, cert_path=cert, key_path=key)
        assert captured["root_certificates"] == b"CA-DATA"
        assert captured["private_key"] == b"KEY-DATA"
        assert captured["certificate_chain"] == b"CERT-DATA"

    def test_partial_paths_uses_insecure(self, monkeypatch):
        """Only ca_path without cert/key uses insecure."""
        import pathlib

        mock_ch = MagicMock()
        monkeypatch.setattr("grpc.insecure_channel", lambda ep: mock_ch)
        c = ShoreGuardClient("h:80", ca_path=pathlib.Path("/some/ca"))
        assert c._channel is mock_ch


class TestResolveActiveClusterMutations:
    """Kill mutations in _resolve_active_cluster."""

    def test_env_var_takes_precedence_over_file(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "openshell"
        config_dir.mkdir()
        (config_dir / "active_gateway").write_text("file-gw")
        monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: config_dir)
        monkeypatch.setenv("OPENSHELL_GATEWAY", "env-gw")
        assert _resolve_active_cluster() == "env-gw"

    def test_file_with_whitespace_stripped(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "openshell"
        config_dir.mkdir()
        (config_dir / "active_gateway").write_text("  my-gw  \n")
        monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: config_dir)
        monkeypatch.delenv("OPENSHELL_GATEWAY", raising=False)
        assert _resolve_active_cluster() == "my-gw"

    def test_file_not_found_raises(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "openshell"
        config_dir.mkdir()
        # No active_gateway file
        monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: config_dir)
        monkeypatch.delenv("OPENSHELL_GATEWAY", raising=False)
        with pytest.raises(FileNotFoundError):
            _resolve_active_cluster()

    def test_whitespace_only_raises(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "openshell"
        config_dir.mkdir()
        (config_dir / "active_gateway").write_text("   \n  ")
        monkeypatch.setattr("shoreguard.client.openshell_config_dir", lambda: config_dir)
        monkeypatch.delenv("OPENSHELL_GATEWAY", raising=False)
        with pytest.raises(GatewayNotConnectedError):
            _resolve_active_cluster()


class TestContextManagerMutations:
    """Kill mutations in __enter__ / __exit__."""

    def test_enter_returns_self(self):
        c = object.__new__(ShoreGuardClient)
        c._channel = MagicMock()
        assert c.__enter__() is c

    def test_exit_calls_close(self):
        c = object.__new__(ShoreGuardClient)
        c._channel = MagicMock()
        c.__exit__(None, None, None)
        c._channel.close.assert_called_once()
