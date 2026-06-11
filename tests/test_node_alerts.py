"""Tests for host threshold alerts (node_alerts service and route)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from shoreguard.services.node_alerts import NodeAlertService
from shoreguard.settings import NodeAlertSettings


class _StubNodeStats:
    def __init__(self, stats: dict[str, Any]) -> None:
        self.stats = stats

    async def collect(self) -> dict[str, Any]:
        return self.stats


def _stats(gpu_temp: float | None = 60.0, mem_pct: float = 40.0, disk_pct: float = 50.0):
    return {
        "scope": "shoreguard-host",
        "cpu": {"count": 20, "load_1m": 1.0, "load_5m": 1.0, "load_15m": 1.0},
        "memory": {"total_mb": 128000, "available_mb": 64000, "used_pct": mem_pct},
        "disk": {"total_gb": 4000.0, "free_gb": 2000.0, "used_pct": disk_pct},
        "gpus": (
            [{"name": "NVIDIA GB10", "temperature_c": gpu_temp, "power_w": 30.0}]
            if gpu_temp is not None
            else []
        ),
    }


@pytest.fixture
def fire(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr("shoreguard.services.webhooks.fire_webhook", mock)
    return mock


async def test_breach_fires_event_once(fire) -> None:
    stub = _StubNodeStats(_stats(gpu_temp=92.0))
    svc = NodeAlertService(stub, NodeAlertSettings())  # type: ignore[arg-type]

    states = await svc.run_once()
    await svc.run_once()  # second pass: still breached, no second event

    assert fire.await_count == 1
    event, payload = fire.await_args.args
    assert event == "node.threshold_breached"
    assert payload["metric"] == "gpu_temp_c"
    assert payload["value"] == 92.0
    assert any(s["metric"] == "gpu_temp_c" and s["breached"] for s in states)


async def test_recovery_fires_recovered_event(fire) -> None:
    stub = _StubNodeStats(_stats(disk_pct=95.0))
    svc = NodeAlertService(stub, NodeAlertSettings())  # type: ignore[arg-type]

    await svc.run_once()
    stub.stats = _stats(disk_pct=70.0)
    await svc.run_once()

    assert fire.await_count == 2
    event, payload = fire.await_args.args
    assert event == "node.recovered"
    assert payload["metric"] == "disk_used_pct"


async def test_missing_metrics_fire_nothing(fire) -> None:
    stub = _StubNodeStats({"scope": "shoreguard-host", "memory": None, "disk": None, "gpus": []})
    svc = NodeAlertService(stub, NodeAlertSettings())  # type: ignore[arg-type]

    states = await svc.run_once()

    fire.assert_not_awaited()
    assert all(not s["breached"] for s in states)


async def test_custom_thresholds_respected(fire) -> None:
    settings = NodeAlertSettings(gpu_temp_c=70.0)
    stub = _StubNodeStats(_stats(gpu_temp=75.0))
    svc = NodeAlertService(stub, settings)  # type: ignore[arg-type]

    await svc.run_once()

    assert fire.await_count == 1
    assert fire.await_args.args[1]["threshold"] == 70.0


async def test_status_reflects_breaches(fire) -> None:
    stub = _StubNodeStats(_stats(mem_pct=99.0))
    svc = NodeAlertService(stub, NodeAlertSettings())  # type: ignore[arg-type]

    await svc.run_once()
    status = svc.status()

    assert status["enabled"] is True
    assert status["breached"] == ["mem_used_pct"]
    assert status["thresholds"]["mem_used_pct"] == 95.0


@pytest.fixture
def db():
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


async def test_node_alerts_route(db) -> None:
    from httpx import ASGITransport, AsyncClient

    from shoreguard.api.auth import create_user

    await create_user("viewer@test.com", "viewerpass1", "viewer")
    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/login",
            json={"email": "viewer@test.com", "password": "viewerpass1"},
        )
        assert resp.status_code == 200
        resp = await client.get("/api/system/node-alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert "gpu_temp_c" in data["thresholds"]


async def test_node_alerts_route_requires_auth(db) -> None:
    from httpx import ASGITransport, AsyncClient

    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/system/node-alerts")
        assert resp.status_code == 401
