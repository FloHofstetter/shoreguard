"""Tests for gateway management API routes (v0.3 — registration model)."""

from __future__ import annotations

import base64
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def mock_gw_svc():
    """Mock the gateway_service used by routes."""
    with patch("shoreguard.services.gateway.gateway_service") as mock:
        yield mock


@pytest.fixture
async def gw_client():
    """Async HTTP client hitting the real FastAPI app."""
    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


# ─── Queries ─────────────────────────────────────────────────────────────────


async def test_gateway_list(gw_client, mock_gw_svc):
    mock_gw_svc.list_all.return_value = [{"name": "gw1", "status": "connected"}]
    resp = await gw_client.get("/api/gateway/list")
    assert resp.status_code == 200
    assert resp.json()[0]["name"] == "gw1"
    mock_gw_svc.list_all.assert_called_once()


async def test_gateway_info(gw_client, mock_gw_svc):
    mock_gw_svc.get_info.return_value = {"name": "gw1", "connected": True}
    resp = await gw_client.get("/api/gateway/gw1/info")
    assert resp.status_code == 200
    assert resp.json()["connected"] is True
    mock_gw_svc.get_info.assert_called_once_with("gw1")


async def test_gateway_config(gw_client, mock_gw_svc):
    mock_gw_svc.get_config.return_value = {"settings": {"log_level": "info"}}
    resp = await gw_client.get("/api/gateway/gw1/config")
    assert resp.status_code == 200
    assert resp.json()["settings"]["log_level"] == "info"
    mock_gw_svc.get_config.assert_called_once_with("gw1")


# ─── Registration ────────────────────────────────────────────────────────────


async def test_gateway_register(gw_client, mock_gw_svc):
    mock_gw_svc.register.return_value = {
        "name": "new-gw",
        "endpoint": "8.8.8.8:8443",
        "connected": False,
        "status": "unreachable",
    }
    resp = await gw_client.post(
        "/api/gateway/register",
        json={"name": "new-gw", "endpoint": "8.8.8.8:8443", "auth_mode": "insecure"},
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "new-gw"
    mock_gw_svc.register.assert_called_once()


async def test_gateway_register_with_certs(gw_client, mock_gw_svc):
    mock_gw_svc.register.return_value = {"name": "tls-gw", "connected": False}
    ca = base64.b64encode(b"ca-data").decode()
    cert = base64.b64encode(b"cert-data").decode()
    key = base64.b64encode(b"key-data").decode()
    resp = await gw_client.post(
        "/api/gateway/register",
        json={
            "name": "tls-gw",
            "endpoint": "8.8.8.8:8443",
            "ca_cert": ca,
            "client_cert": cert,
            "client_key": key,
        },
    )
    assert resp.status_code == 201
    call_kwargs = mock_gw_svc.register.call_args
    assert call_kwargs.kwargs["ca_cert"] == b"ca-data"
    assert call_kwargs.kwargs["client_cert"] == b"cert-data"
    assert call_kwargs.kwargs["client_key"] == b"key-data"


async def test_gateway_register_invalid_name(gw_client, mock_gw_svc):
    resp = await gw_client.post(
        "/api/gateway/register",
        json={"name": "--malicious", "endpoint": "8.8.8.8:8443"},
    )
    assert resp.status_code == 400
    assert "Invalid gateway name" in resp.json()["detail"]


async def test_gateway_register_duplicate_returns_409(gw_client, mock_gw_svc):
    mock_gw_svc.register.side_effect = ValueError("Gateway 'dup' is already registered")
    resp = await gw_client.post(
        "/api/gateway/register",
        json={"name": "dup", "endpoint": "8.8.8.8:8443"},
    )
    assert resp.status_code == 409


async def test_gateway_register_invalid_base64(gw_client, mock_gw_svc):
    resp = await gw_client.post(
        "/api/gateway/register",
        json={"name": "gw1", "endpoint": "8.8.8.8:8443", "ca_cert": "not-valid-b64!!!"},
    )
    assert resp.status_code == 400
    assert "base64" in resp.json()["detail"].lower()


async def test_gateway_register_invalid_scheme(gw_client, mock_gw_svc):
    resp = await gw_client.post(
        "/api/gateway/register",
        json={"name": "gw1", "endpoint": "8.8.8.8:8443", "scheme": "ftp"},
    )
    assert resp.status_code == 422


async def test_gateway_register_invalid_auth_mode(gw_client, mock_gw_svc):
    resp = await gw_client.post(
        "/api/gateway/register",
        json={"name": "gw1", "endpoint": "8.8.8.8:8443", "auth_mode": "magic"},
    )
    assert resp.status_code == 422


async def test_gateway_register_empty_endpoint(gw_client, mock_gw_svc):
    resp = await gw_client.post(
        "/api/gateway/register",
        json={"name": "gw1", "endpoint": "   "},
    )
    assert resp.status_code == 422


async def test_gateway_register_name_too_long(gw_client, mock_gw_svc):
    resp = await gw_client.post(
        "/api/gateway/register",
        json={"name": "a" * 300, "endpoint": "8.8.8.8:8443"},
    )
    assert resp.status_code == 400
    assert "Invalid gateway name" in resp.json()["detail"]


async def test_gateway_register_cert_too_large(gw_client, mock_gw_svc):
    import base64

    huge = base64.b64encode(b"x" * 70_000).decode()
    resp = await gw_client.post(
        "/api/gateway/register",
        json={"name": "gw1", "endpoint": "8.8.8.8:8443", "ca_cert": huge},
    )
    assert resp.status_code == 400
    assert "exceeds maximum size" in resp.json()["detail"]


async def test_gateway_register_metadata_too_large(gw_client, mock_gw_svc):
    big_meta = {"key": "x" * 20_000}
    resp = await gw_client.post(
        "/api/gateway/register",
        json={"name": "gw1", "endpoint": "8.8.8.8:8443", "metadata": big_meta},
    )
    assert resp.status_code == 400
    assert "metadata exceeds" in resp.json()["detail"]


# ─── Unregister ──────────────────────────────────────────────────────────────


async def test_gateway_unregister(gw_client, mock_gw_svc):
    mock_gw_svc.unregister.return_value = True
    resp = await gw_client.delete("/api/gateway/my-gw")
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    mock_gw_svc.unregister.assert_called_once_with("my-gw")


async def test_gateway_unregister_not_found(gw_client, mock_gw_svc):
    mock_gw_svc.unregister.return_value = False
    resp = await gw_client.delete("/api/gateway/unknown")
    assert resp.status_code == 404


# ─── Test connection ─────────────────────────────────────────────────────────


async def test_gateway_test_connection(gw_client, mock_gw_svc):
    mock_gw_svc.test_connection.return_value = {
        "success": True,
        "connected": True,
        "version": "1.0",
    }
    resp = await gw_client.post("/api/gateway/my-gw/test-connection")
    assert resp.status_code == 200
    assert resp.json()["connected"] is True
    mock_gw_svc.test_connection.assert_called_once_with("my-gw")


# ─── Local mode routes (without SHOREGUARD_LOCAL_MODE) ───────────────────────


async def test_local_routes_return_404_without_local_mode(gw_client):
    """Local lifecycle routes return 404 when not in local mode."""
    for path in [
        "/api/gateway/diagnostics",
        "/api/gateway/my-gw/start",
        "/api/gateway/my-gw/stop",
        "/api/gateway/my-gw/restart",
        "/api/gateway/my-gw/destroy",
    ]:
        method = "post" if path != "/api/gateway/diagnostics" else "get"
        resp = await getattr(gw_client, method)(path)
        assert resp.status_code == 404, f"Expected 404 for {path}, got {resp.status_code}"


async def test_create_returns_404_without_local_mode(gw_client):
    resp = await gw_client.post("/api/gateway/create", json={"name": "new-gw"})
    assert resp.status_code == 404


# ─── Path parameter validation ──────────────────────────────────────────────


async def test_invalid_name_on_delete(gw_client, mock_gw_svc):
    resp = await gw_client.delete("/api/gateway/--bad")
    assert resp.status_code == 400
    assert "Invalid gateway name" in resp.json()["detail"]


async def test_invalid_name_on_test_connection(gw_client, mock_gw_svc):
    resp = await gw_client.post("/api/gateway/--bad/test-connection")
    assert resp.status_code == 400


# ─── SSRF endpoint validation ────────────────────────────────────────────────


async def test_gateway_register_private_ip_rejected(gw_client, mock_gw_svc):
    resp = await gw_client.post(
        "/api/gateway/register",
        json={"name": "evil", "endpoint": "127.0.0.1:8443"},
    )
    assert resp.status_code == 422
    assert "private" in resp.text.lower() or "loopback" in resp.text.lower()


async def test_gateway_register_localhost_rejected(gw_client, mock_gw_svc):
    resp = await gw_client.post(
        "/api/gateway/register",
        json={"name": "evil", "endpoint": "localhost:8443"},
    )
    assert resp.status_code == 422


async def test_gateway_register_rfc1918_rejected(gw_client, mock_gw_svc):
    for ip in ["10.0.0.1:8443", "192.168.1.1:8443", "172.16.0.1:8443"]:
        resp = await gw_client.post(
            "/api/gateway/register",
            json={"name": "evil", "endpoint": ip},
        )
        assert resp.status_code == 422, f"Expected 422 for {ip}, got {resp.status_code}"


async def test_gateway_register_bad_format_rejected(gw_client, mock_gw_svc):
    for ep in ["just-a-host", "http://host:443", ":8443", "host:99999"]:
        resp = await gw_client.post(
            "/api/gateway/register",
            json={"name": "gw1", "endpoint": ep},
        )
        assert resp.status_code == 422, f"Expected 422 for '{ep}', got {resp.status_code}"


# ─── Local mode routes (with mock manager) ──────────────────────────────────


@pytest.fixture
def mock_local_mgr():
    """Patch _get_local_manager to return a mock LocalGatewayManager."""
    from unittest.mock import MagicMock

    mgr = MagicMock()
    with patch("shoreguard.api.routes.gateway._get_local_manager", return_value=mgr):
        yield mgr


async def test_gateway_diagnostics_local_mode(gw_client, mock_local_mgr):
    mock_local_mgr.diagnostics.return_value = {
        "docker_installed": True,
        "docker_daemon_running": True,
    }
    resp = await gw_client.get("/api/gateway/diagnostics")
    assert resp.status_code == 200
    assert resp.json()["docker_installed"] is True
    mock_local_mgr.diagnostics.assert_called_once()


async def test_gateway_start_named_local_mode(gw_client, mock_local_mgr):
    mock_local_mgr.start.return_value = {"success": True, "output": "Started my-gw"}
    resp = await gw_client.post("/api/gateway/my-gw/start")
    assert resp.status_code == 200
    mock_local_mgr.start.assert_called_once_with("my-gw")


async def test_gateway_stop_named_local_mode(gw_client, mock_local_mgr):
    mock_local_mgr.stop.return_value = {"success": True, "output": "Stopped"}
    resp = await gw_client.post("/api/gateway/my-gw/stop")
    assert resp.status_code == 200
    mock_local_mgr.stop.assert_called_once_with("my-gw")


async def test_gateway_restart_named_local_mode(gw_client, mock_local_mgr):
    mock_local_mgr.restart.return_value = {"success": True, "output": "Restarted"}
    resp = await gw_client.post("/api/gateway/my-gw/restart")
    assert resp.status_code == 200
    mock_local_mgr.restart.assert_called_once_with("my-gw")


async def test_gateway_destroy_local_mode(gw_client, mock_local_mgr):
    mock_local_mgr.destroy.return_value = {"success": True, "output": "Destroyed"}
    resp = await gw_client.post("/api/gateway/my-gw/destroy")
    assert resp.status_code == 200
    mock_local_mgr.destroy.assert_called_once_with("my-gw", force=False)


async def test_gateway_destroy_force_local_mode(gw_client, mock_local_mgr):
    mock_local_mgr.destroy.return_value = {"success": True, "output": "Force destroyed"}
    resp = await gw_client.post("/api/gateway/my-gw/destroy?force=true")
    assert resp.status_code == 200
    mock_local_mgr.destroy.assert_called_once_with("my-gw", force=True)


async def test_gateway_destroy_invalid_name_local_mode(gw_client, mock_local_mgr):
    resp = await gw_client.post("/api/gateway/--bad/destroy")
    assert resp.status_code == 400


async def test_gateway_create_local_mode(gw_client, mock_local_mgr):
    mock_local_mgr.create.return_value = {"success": True, "name": "new-gw"}
    resp = await gw_client.post("/api/gateway/create", json={"name": "new-gw"})
    assert resp.status_code == 202
    assert "operation_id" in resp.json()


# ─── NotFoundError → 404 mapping ──────────────────────────────────────────────


async def test_gateway_test_connection_not_found(gw_client, mock_gw_svc):
    """test-connection returns 404 when gateway is not registered."""
    from shoreguard.exceptions import NotFoundError

    mock_gw_svc.test_connection.side_effect = NotFoundError("Gateway 'nope' not registered")
    resp = await gw_client.post("/api/gateway/nope/test-connection")
    assert resp.status_code == 404
    assert "not registered" in resp.json()["detail"]


# ─── remote_host validation ──────────────────────────────────────────────────


async def test_create_gateway_invalid_remote_host(gw_client, mock_local_mgr):
    """Create rejects invalid remote_host."""
    resp = await gw_client.post(
        "/api/gateway/create",
        json={"name": "new-gw", "remote_host": "host; rm -rf /"},
    )
    assert resp.status_code == 422


async def test_create_gateway_valid_remote_host(gw_client, mock_local_mgr):
    """Create accepts valid remote_host."""
    mock_local_mgr.create.return_value = {"success": True, "name": "new-gw"}
    resp = await gw_client.post(
        "/api/gateway/create",
        json={"name": "new-gw", "remote_host": "192.168.1.100"},
    )
    assert resp.status_code == 202
