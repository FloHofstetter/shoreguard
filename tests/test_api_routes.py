"""Integration tests for FastAPI API routes."""

from __future__ import annotations

import asyncio
import time

from shoreguard.exceptions import GatewayNotConnectedError, NotFoundError

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
    """POST /api/gateways/{gw}/sandboxes with valid body succeeds (202 for async LRO)."""
    mock_client.sandboxes.create.return_value = {"name": "new-sb", "phase": "provisioning"}

    resp = await api_client.post(
        f"/api/gateways/{GW}/sandboxes",
        json={"name": "new-sb", "image": "base"},
    )

    assert resp.status_code == 202


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


async def test_get_inference(api_client, mock_client):
    """GET /api/gateways/{gw}/inference returns cluster inference config."""
    mock_client.get_cluster_inference.return_value = {
        "provider_name": "anthropic",
        "model_id": "claude-3",
        "version": 1,
        "route_name": "default",
        "timeout_secs": 30,
    }
    resp = await api_client.get(f"/api/gateways/{GW}/inference")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider_name"] == "anthropic"
    assert data["model_id"] == "claude-3"


async def test_set_inference_validation(api_client, mock_client):
    """PUT /api/gateways/{gw}/inference with valid body passes through."""
    mock_client.set_cluster_inference.return_value = {
        "provider_name": "anthropic",
        "model_id": "claude-3",
        "version": 1,
        "route_name": "",
        "timeout_secs": 90,
    }

    resp = await api_client.put(
        f"/api/gateways/{GW}/inference",
        json={"provider_name": "anthropic", "model_id": "claude-3", "timeout_secs": 90},
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


async def test_get_nonexistent_sandbox(api_client, mock_client):
    """GET /api/gateways/{gw}/sandboxes/{name} returns 404 for unknown sandbox."""
    mock_client.sandboxes.get.side_effect = NotFoundError("Sandbox not found")

    resp = await api_client.get(f"/api/gateways/{GW}/sandboxes/nonexistent")

    assert resp.status_code == 404


async def test_delete_nonexistent_sandbox(api_client, mock_client):
    """DELETE /api/gateways/{gw}/sandboxes/{name} returns 404 for unknown sandbox."""
    mock_client.sandboxes.delete.side_effect = NotFoundError("Sandbox not found")

    resp = await api_client.delete(f"/api/gateways/{GW}/sandboxes/nonexistent")

    assert resp.status_code == 404


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

    assert resp.status_code == 202
    assert "operation_id" in resp.json()


async def test_get_sandbox_logs(api_client, mock_client):
    """GET /api/gateways/{gw}/sandboxes/{name}/logs returns log entries."""
    mock_client.sandboxes.get.return_value = {"id": "abc-123", "name": "sb1"}
    mock_client.sandboxes.get_logs.return_value = [{"message": "started", "level": "info"}]

    resp = await api_client.get(f"/api/gateways/{GW}/sandboxes/sb1/logs?lines=50")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["message"] == "started"


async def test_create_sandbox_duplicate_returns_409(api_client, mock_client):
    """Second sandbox creation with the same name returns 409 while first is running."""

    def _slow_create(**kwargs):
        time.sleep(10)
        return {"name": "dup-sb", "id": "abc"}

    mock_client.sandboxes.create.side_effect = _slow_create
    mock_client.sandboxes.wait_ready.side_effect = lambda *a, **kw: None

    resp1 = await api_client.post(
        f"/api/gateways/{GW}/sandboxes",
        json={"name": "dup-sb", "image": "base"},
    )
    assert resp1.status_code == 202

    # Give the event loop a tick so the task starts
    await asyncio.sleep(0.05)

    resp2 = await api_client.post(
        f"/api/gateways/{GW}/sandboxes",
        json={"name": "dup-sb", "image": "base"},
    )
    assert resp2.status_code == 409


async def test_exec_shlex_unterminated_quote(api_client, mock_client):
    """POST exec with unterminated quote returns 400 (ValidationError)."""
    mock_client.sandboxes.get.return_value = {"id": "abc-123", "name": "sb1"}
    resp = await api_client.post(
        f"/api/gateways/{GW}/sandboxes/sb1/exec",
        json={"command": "echo 'hello"},
    )
    assert resp.status_code == 400
    assert "Invalid command syntax" in resp.json()["detail"]


async def test_create_sandbox_invalid_name(api_client, mock_client):
    """POST sandbox create with invalid name returns 400."""
    resp = await api_client.post(
        f"/api/gateways/{GW}/sandboxes",
        json={"name": "--malicious", "image": "base"},
    )
    assert resp.status_code == 400
    assert "Invalid sandbox name" in resp.json()["detail"]


# ─── Operations endpoint ─────────────────────────────────────────────────────


async def test_get_operation_found(api_client):
    """GET /api/operations/{id} returns 200 for an existing operation."""
    from shoreguard.services.operations import operation_store

    op = operation_store.create("sandbox", "test-sb")
    operation_store.complete(op.id, {"name": "test-sb"})
    resp = await api_client.get(f"/api/operations/{op.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "succeeded"
    assert data["id"] == op.id


async def test_get_operation_not_found(api_client):
    """GET /api/operations/{nonexistent} returns 404."""
    resp = await api_client.get("/api/operations/nonexistent-id")
    assert resp.status_code == 404


# ─── LRO completion tests ────────────────────────────────────────────────────


async def test_sandbox_create_lro_success(api_client, mock_client):
    """Sandbox LRO completes and operation transitions to succeeded."""
    mock_client.sandboxes.create.return_value = {"name": "lro-sb", "id": "abc"}
    mock_client.sandboxes.wait_ready.return_value = None
    mock_client.sandboxes.get.return_value = {"name": "lro-sb", "phase": "ready"}

    resp = await api_client.post(
        f"/api/gateways/{GW}/sandboxes",
        json={"name": "lro-sb", "image": "base"},
    )
    assert resp.status_code == 202
    op_id = resp.json()["operation_id"]

    await asyncio.sleep(0.2)

    resp2 = await api_client.get(f"/api/operations/{op_id}")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "succeeded"


async def test_sandbox_create_lro_failure(api_client, mock_client):
    """Sandbox LRO that raises marks the operation as failed."""
    mock_client.sandboxes.create.side_effect = RuntimeError("boom")

    resp = await api_client.post(
        f"/api/gateways/{GW}/sandboxes",
        json={"name": "fail-sb", "image": "base"},
    )
    assert resp.status_code == 202
    op_id = resp.json()["operation_id"]

    await asyncio.sleep(0.2)

    resp2 = await api_client.get(f"/api/operations/{op_id}")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "failed"


async def test_create_sandbox_empty_name(api_client, mock_client):
    """POST with empty name defaults to 'unnamed'."""
    mock_client.sandboxes.create.return_value = {"name": "unnamed", "id": "xyz"}
    mock_client.sandboxes.wait_ready.return_value = None
    mock_client.sandboxes.get.return_value = {"name": "unnamed", "phase": "ready"}

    resp = await api_client.post(
        f"/api/gateways/{GW}/sandboxes",
        json={"name": "", "image": "base"},
    )
    assert resp.status_code == 202
    op_id = resp.json()["operation_id"]
    from shoreguard.services.operations import operation_store

    op = operation_store.get(op_id)
    assert op.resource_key == "unnamed"


async def test_create_sandbox_wait_ready_timeout(api_client, mock_client):
    """Sandbox created but wait_ready times out — operation still succeeds with warning."""
    mock_client.sandboxes.create.return_value = {"name": "slow-sb", "id": "abc"}
    mock_client.sandboxes.wait_ready.side_effect = TimeoutError("timed out")

    resp = await api_client.post(
        f"/api/gateways/{GW}/sandboxes",
        json={"name": "slow-sb", "image": "base"},
    )
    assert resp.status_code == 202
    op_id = resp.json()["operation_id"]

    await asyncio.sleep(0.2)

    resp2 = await api_client.get(f"/api/operations/{op_id}")
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["status"] == "succeeded"
    assert "warning" in data.get("result", {})


async def test_sandbox_create_lro_cancelled(api_client, mock_client):
    """CancelledError during sandbox LRO marks the operation as failed."""

    async def _cancel_task():
        await asyncio.sleep(0.05)
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task() and "_run" in repr(task):
                task.cancel()

    mock_client.sandboxes.create.side_effect = lambda **kw: time.sleep(10)

    resp = await api_client.post(
        f"/api/gateways/{GW}/sandboxes",
        json={"name": "cancel-sb", "image": "base"},
    )
    assert resp.status_code == 202
    op_id = resp.json()["operation_id"]

    await _cancel_task()
    await asyncio.sleep(0.2)

    resp2 = await api_client.get(f"/api/operations/{op_id}")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "failed"


async def test_get_sandbox_logs_default_params(api_client, mock_client):
    """GET /api/gateways/{gw}/sandboxes/{name}/logs returns log entries."""
    mock_client.sandboxes.get.return_value = {"id": "sb-123", "name": "sb1"}
    mock_client.sandboxes.get_logs.return_value = [
        {"timestamp": 1000, "message": "hello", "level": "INFO"},
    ]

    resp = await api_client.get(f"/api/gateways/{GW}/sandboxes/sb1/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["message"] == "hello"


async def test_get_sandbox_logs_with_params(api_client, mock_client):
    """GET /api/gateways/{gw}/sandboxes/{name}/logs passes query params."""
    mock_client.sandboxes.get.return_value = {"id": "sb-123", "name": "sb1"}
    mock_client.sandboxes.get_logs.return_value = []

    resp = await api_client.get(
        f"/api/gateways/{GW}/sandboxes/sb1/logs",
        params={"lines": 50, "since_ms": 500, "min_level": "ERROR", "sources": "app,system"},
    )
    assert resp.status_code == 200
    mock_client.sandboxes.get_logs.assert_called_once_with(
        "sb-123",
        lines=50,
        since_ms=500,
        sources=["app", "system"],
        min_level="ERROR",
    )
