"""Integration tests for FastAPI API routes."""

from __future__ import annotations

from shoreguard.exceptions import GatewayNotConnectedError

GW = "test"  # gateway name used in all gateway-scoped URLs


async def test_list_sandboxes(api_client, mock_client):
    """GET /api/gateways/{gw}/sandboxes returns mocked sandbox list."""
    mock_client.sandboxes.list.return_value = [
        {"name": "sb1", "phase": "ready"},
        {"name": "sb2", "phase": "provisioning"},
    ]

    resp = await api_client.get(f"/api/gateways/{GW}/sandboxes")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["name"] == "sb1"


async def test_create_sandbox_validation(api_client, mock_client):
    """POST /api/gateways/{gw}/sandboxes with valid body succeeds."""
    mock_client.sandboxes.create.return_value = {"name": "new-sb", "phase": "provisioning"}

    resp = await api_client.post(
        f"/api/gateways/{GW}/sandboxes",
        json={"name": "new-sb", "image": "base"},
    )

    assert resp.status_code == 201


async def test_health_disconnected(api_client, mock_client):
    """GET /api/gateways/{gw}/health returns 503 when gateway raises GatewayNotConnectedError."""
    mock_client.health.side_effect = GatewayNotConnectedError("not connected")

    resp = await api_client.get(f"/api/gateways/{GW}/health")

    assert resp.status_code == 503


async def test_create_ssh_session(api_client, mock_client):
    """POST /api/gateways/{gw}/sandboxes/{name}/ssh creates an SSH session."""
    mock_client.sandboxes.get.return_value = {"id": "abc-123", "name": "sb1"}
    mock_client.sandboxes.create_ssh_session.return_value = {
        "sandbox_id": "abc-123",
        "token": "tok-xyz",
        "gateway_host": "127.0.0.1",
        "gateway_port": 8080,
        "gateway_scheme": "https",
        "connect_path": "/connect",
        "host_key_fingerprint": "",
        "expires_at_ms": 9999999,
    }

    resp = await api_client.post(f"/api/gateways/{GW}/sandboxes/sb1/ssh")

    assert resp.status_code == 201
    data = resp.json()
    assert data["token"] == "tok-xyz"


async def test_revoke_ssh_session(api_client, mock_client):
    """DELETE /api/gateways/{gw}/sandboxes/{name}/ssh revokes an SSH session."""
    mock_client.sandboxes.revoke_ssh_session.return_value = True

    resp = await api_client.request(
        "DELETE",
        f"/api/gateways/{GW}/sandboxes/sb1/ssh",
        json={"token": "tok-xyz"},
    )

    assert resp.status_code == 200
    assert resp.json()["revoked"] is True


async def test_set_inference_validation(api_client, mock_client):
    """PUT /api/gateways/{gw}/inference with valid body passes through."""
    mock_client.set_cluster_inference.return_value = {
        "provider_name": "anthropic",
        "model_id": "claude-3",
        "version": 1,
        "route_name": "",
    }

    resp = await api_client.put(
        f"/api/gateways/{GW}/inference",
        json={"provider_name": "anthropic", "model_id": "claude-3"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["provider_name"] == "anthropic"


async def test_get_sandbox(api_client, mock_client):
    """GET /api/gateways/{gw}/sandboxes/{name} returns sandbox data."""
    mock_client.sandboxes.get.return_value = {"name": "sb1", "phase": "ready"}

    resp = await api_client.get(f"/api/gateways/{GW}/sandboxes/sb1")

    assert resp.status_code == 200
    assert resp.json()["name"] == "sb1"


async def test_delete_sandbox(api_client, mock_client):
    """DELETE /api/gateways/{gw}/sandboxes/{name} deletes a sandbox."""
    mock_client.sandboxes.delete.return_value = True

    resp = await api_client.delete(f"/api/gateways/{GW}/sandboxes/sb1")

    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


async def test_list_presets(api_client):
    """GET /api/policies/presets returns local YAML presets (no gateway needed)."""
    resp = await api_client.get("/api/policies/presets")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert any(p["name"] == "pypi" for p in data)


async def test_get_preset_not_found(api_client):
    """GET /api/policies/presets/{name} returns 404 for unknown preset."""
    resp = await api_client.get("/api/policies/presets/nonexistent-preset-xyz")

    assert resp.status_code == 404


async def test_filesystem_access_validation(api_client, mock_client):
    """POST filesystem path with invalid access value returns 422."""
    resp = await api_client.post(
        f"/api/gateways/{GW}/sandboxes/sb1/policy/filesystem",
        json={"path": "/tmp", "access": "invalid"},
    )

    assert resp.status_code == 422


async def test_exec_sandbox(api_client, mock_client):
    """POST /api/gateways/{gw}/sandboxes/{name}/exec executes a command."""
    mock_client.sandboxes.get.return_value = {"id": "abc-123", "name": "sb1"}
    mock_client.sandboxes.exec.return_value = {"exit_code": 0, "stdout": "hello"}

    resp = await api_client.post(
        f"/api/gateways/{GW}/sandboxes/sb1/exec",
        json={"command": "echo hello"},
    )

    assert resp.status_code == 200
    assert resp.json()["exit_code"] == 0


async def test_get_sandbox_logs(api_client, mock_client):
    """GET /api/gateways/{gw}/sandboxes/{name}/logs returns log entries."""
    mock_client.sandboxes.get.return_value = {"id": "abc-123", "name": "sb1"}
    mock_client.sandboxes.get_logs.return_value = [{"message": "started", "level": "info"}]

    resp = await api_client.get(f"/api/gateways/{GW}/sandboxes/sb1/logs?lines=50")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["message"] == "started"
