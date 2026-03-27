"""Integration tests for gateway management API routes."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def mock_gw_svc():
    """Mock the module-level gateway_service singleton used by gateway routes."""
    with patch("shoreguard.api.routes.gateway.gateway_service") as mock:
        yield mock


@pytest.fixture
async def gw_client():
    """Async HTTP client hitting the real FastAPI app (no dependency overrides needed)."""
    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_gateway_list(gw_client, mock_gw_svc):
    """GET /api/gateway/list delegates to gateway_service.list_all()."""
    mock_gw_svc.list_all.return_value = [{"name": "gw1", "status": "connected"}]

    resp = await gw_client.get("/api/gateway/list")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "gw1"
    mock_gw_svc.list_all.assert_called_once()


async def test_gateway_info(gw_client, mock_gw_svc):
    """GET /api/gateway/info delegates to gateway_service.get_info()."""
    mock_gw_svc.get_info.return_value = {"name": "gw1", "connected": True}

    resp = await gw_client.get("/api/gateway/info")

    assert resp.status_code == 200
    assert resp.json()["connected"] is True


async def test_gateway_config(gw_client, mock_gw_svc):
    """GET /api/gateway/config delegates to gateway_service.get_config()."""
    mock_gw_svc.get_config.return_value = {
        "settings": {"log_level": "info"},
        "settings_revision": 1,
    }

    resp = await gw_client.get("/api/gateway/config")

    assert resp.status_code == 200
    assert resp.json()["settings"]["log_level"] == "info"


async def test_gateway_diagnostics(gw_client, mock_gw_svc):
    """GET /api/gateway/diagnostics delegates to gateway_service.diagnostics()."""
    mock_gw_svc.diagnostics.return_value = {"docker_installed": True}

    resp = await gw_client.get("/api/gateway/diagnostics")

    assert resp.status_code == 200
    assert resp.json()["docker_installed"] is True


async def test_gateway_select(gw_client, mock_gw_svc):
    """POST /api/gateway/{name}/select delegates to gateway_service.select(name)."""
    mock_gw_svc.select.return_value = {"name": "my-gw", "active": True}

    resp = await gw_client.post("/api/gateway/my-gw/select")

    assert resp.status_code == 200
    mock_gw_svc.select.assert_called_once_with("my-gw")


# ─── Lifecycle actions (active gateway) ──────────────────────────────────────


async def test_gateway_start_active(gw_client, mock_gw_svc):
    mock_gw_svc.start.return_value = {"success": True, "output": "started"}
    resp = await gw_client.post("/api/gateway/start")
    assert resp.status_code == 200
    mock_gw_svc.start.assert_called_once()


async def test_gateway_stop_active(gw_client, mock_gw_svc):
    mock_gw_svc.stop.return_value = {"success": True, "output": "stopped"}
    resp = await gw_client.post("/api/gateway/stop")
    assert resp.status_code == 200
    mock_gw_svc.stop.assert_called_once()


async def test_gateway_restart_active(gw_client, mock_gw_svc):
    mock_gw_svc.restart.return_value = {"success": True}
    resp = await gw_client.post("/api/gateway/restart")
    assert resp.status_code == 200
    mock_gw_svc.restart.assert_called_once()


# ─── Lifecycle actions (named gateway) ───────────────────────────────────────


async def test_gateway_start_named(gw_client, mock_gw_svc):
    mock_gw_svc.start.return_value = {"success": True}
    resp = await gw_client.post("/api/gateway/my-gw/start")
    assert resp.status_code == 200
    mock_gw_svc.start.assert_called_once_with("my-gw")


async def test_gateway_stop_named(gw_client, mock_gw_svc):
    mock_gw_svc.stop.return_value = {"success": True}
    resp = await gw_client.post("/api/gateway/my-gw/stop")
    assert resp.status_code == 200
    mock_gw_svc.stop.assert_called_once_with("my-gw")


async def test_gateway_restart_named(gw_client, mock_gw_svc):
    mock_gw_svc.restart.return_value = {"success": True}
    resp = await gw_client.post("/api/gateway/my-gw/restart")
    assert resp.status_code == 200
    mock_gw_svc.restart.assert_called_once_with("my-gw")


async def test_gateway_destroy(gw_client, mock_gw_svc):
    mock_gw_svc.destroy.return_value = {"success": True}
    resp = await gw_client.post("/api/gateway/my-gw/destroy")
    assert resp.status_code == 200
    mock_gw_svc.destroy.assert_called_once_with("my-gw", force=False)


async def test_gateway_destroy_with_force(gw_client, mock_gw_svc):
    mock_gw_svc.destroy.return_value = {"success": True}
    resp = await gw_client.post("/api/gateway/my-gw/destroy?force=true")
    assert resp.status_code == 200
    mock_gw_svc.destroy.assert_called_once_with("my-gw", force=True)


async def test_gateway_destroy_blocked(gw_client, mock_gw_svc):
    mock_gw_svc.destroy.return_value = {
        "success": False,
        "error": "Gateway 'my-gw' still has 2 sandbox(es). Use force=true.",
        "sandboxes": ["sb1", "sb2"],
        "providers": [],
    }
    resp = await gw_client.post("/api/gateway/my-gw/destroy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "sandbox(es)" in data["error"]


async def test_gateway_create(gw_client, mock_gw_svc):
    resp = await gw_client.post(
        "/api/gateway/create",
        json={"name": "new-gw", "port": 9090, "gpu": True},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "operation_id" in data
    assert data["status"] == "running"
    assert data["resource_type"] == "gateway"


async def test_gateway_create_duplicate_returns_409(gw_client, mock_gw_svc):
    import asyncio
    import time

    # Make the mock block so the operation stays "running"
    def _slow_create(**kwargs):
        time.sleep(10)
        return {"success": True}

    mock_gw_svc.create.side_effect = _slow_create

    # First create starts an operation
    resp1 = await gw_client.post(
        "/api/gateway/create",
        json={"name": "dup-gw", "port": 9090},
    )
    assert resp1.status_code == 202

    # Give the event loop a tick so the task starts
    await asyncio.sleep(0.05)

    # Second create with same name returns 409
    resp2 = await gw_client.post(
        "/api/gateway/create",
        json={"name": "dup-gw", "port": 9091},
    )
    assert resp2.status_code == 409


async def test_gateway_create_lro_success(gw_client, mock_gw_svc):
    """Gateway LRO completes and operation transitions to succeeded."""
    import asyncio

    mock_gw_svc.create.return_value = {"name": "new-gw", "success": True}

    resp = await gw_client.post(
        "/api/gateway/create",
        json={"name": "new-gw", "port": 9090},
    )
    assert resp.status_code == 202
    op_id = resp.json()["operation_id"]

    await asyncio.sleep(0.2)

    resp2 = await gw_client.get(f"/api/operations/{op_id}")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "succeeded"


async def test_gateway_create_lro_failure(gw_client, mock_gw_svc):
    """Gateway LRO that raises marks the operation as failed."""
    import asyncio

    mock_gw_svc.create.side_effect = RuntimeError("docker not found")

    resp = await gw_client.post(
        "/api/gateway/create",
        json={"name": "fail-gw"},
    )
    assert resp.status_code == 202
    op_id = resp.json()["operation_id"]

    await asyncio.sleep(0.2)

    resp2 = await gw_client.get(f"/api/operations/{op_id}")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "failed"


async def test_gateway_create_success_false(gw_client, mock_gw_svc):
    """Gateway create returning success=False marks operation as failed."""
    import asyncio

    mock_gw_svc.create.return_value = {"success": False, "error": "openshell CLI not found"}

    resp = await gw_client.post(
        "/api/gateway/create",
        json={"name": "bad-gw"},
    )
    assert resp.status_code == 202
    op_id = resp.json()["operation_id"]

    await asyncio.sleep(0.2)

    resp2 = await gw_client.get(f"/api/operations/{op_id}")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "failed"


async def test_gateway_create_invalid_name(gw_client, mock_gw_svc):
    """Gateway create with invalid name returns 400."""
    resp = await gw_client.post(
        "/api/gateway/create",
        json={"name": "--malicious"},
    )
    assert resp.status_code == 400
    assert "Invalid gateway name" in resp.json()["detail"]


async def test_gateway_create_lro_catch_all(gw_client, mock_gw_svc):
    """Unexpected exception type in background task still marks operation as failed."""
    import asyncio

    mock_gw_svc.create.side_effect = ValueError("totally unexpected")

    resp = await gw_client.post(
        "/api/gateway/create",
        json={"name": "catchall-gw"},
    )
    assert resp.status_code == 202
    op_id = resp.json()["operation_id"]

    await asyncio.sleep(0.3)

    resp2 = await gw_client.get(f"/api/operations/{op_id}")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "failed"


async def test_gateway_create_lro_cancelled(gw_client, mock_gw_svc):
    """CancelledError during gateway LRO marks the operation as failed."""
    import asyncio
    import time

    async def _cancel_task():
        await asyncio.sleep(0.05)
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task() and "_run" in repr(task):
                task.cancel()

    mock_gw_svc.create.side_effect = lambda **kw: time.sleep(10)

    resp = await gw_client.post(
        "/api/gateway/create",
        json={"name": "cancel-gw"},
    )
    assert resp.status_code == 202
    op_id = resp.json()["operation_id"]

    await _cancel_task()
    await asyncio.sleep(0.2)

    resp2 = await gw_client.get(f"/api/operations/{op_id}")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "failed"
