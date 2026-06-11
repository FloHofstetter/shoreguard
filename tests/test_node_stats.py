"""Tests for the host node-stats service and route."""

from __future__ import annotations

import pytest

from shoreguard.services.node_stats import NodeStatsService, parse_nvidia_smi_csv


def test_parse_nvidia_smi_csv_single_gpu() -> None:
    output = "NVIDIA GB10, 42, 12288, 122880, 61, 38.5\n"
    gpus = parse_nvidia_smi_csv(output)
    assert gpus == [
        {
            "name": "NVIDIA GB10",
            "utilization_pct": 42.0,
            "memory_used_mb": 12288.0,
            "memory_total_mb": 122880.0,
            "temperature_c": 61.0,
            "power_w": 38.5,
        }
    ]


def test_parse_nvidia_smi_csv_handles_na_and_garbage() -> None:
    output = "NVIDIA GB10, [N/A], 1024, 122880, N/A, [N/A]\nshort,line\n"
    gpus = parse_nvidia_smi_csv(output)
    assert len(gpus) == 1
    assert gpus[0]["utilization_pct"] is None
    assert gpus[0]["temperature_c"] is None
    assert gpus[0]["memory_used_mb"] == 1024.0
    assert gpus[0]["power_w"] is None


def test_parse_nvidia_smi_csv_five_field_fallback() -> None:
    gpus = parse_nvidia_smi_csv("NVIDIA GB10, 42, 1024, 122880, 61\n")
    assert len(gpus) == 1
    assert gpus[0]["power_w"] is None


def test_parse_nvidia_smi_csv_empty() -> None:
    assert parse_nvidia_smi_csv("") == []


async def test_collect_returns_host_sample_and_caches(monkeypatch) -> None:
    svc = NodeStatsService(cache_ttl=60.0)
    stats = await svc.collect()
    assert stats["scope"] == "shoreguard-host"
    # On Linux CI these are present; the shape contract is what matters.
    assert "cpu" in stats and "memory" in stats and "gpus" in stats and "disk" in stats

    # Second call within the TTL returns the cached object — no re-sample.
    calls = {"n": 0}

    def _boom() -> dict:
        calls["n"] += 1
        raise AssertionError("must not re-collect within TTL")

    monkeypatch.setattr("shoreguard.services.node_stats._collect_sync", _boom)
    again = await svc.collect()
    assert again is stats
    assert calls["n"] == 0


@pytest.fixture
def db():
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


async def test_node_stats_route(db) -> None:
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
        resp = await client.get("/api/system/node-stats")
        assert resp.status_code == 200
        assert resp.json()["scope"] == "shoreguard-host"


async def test_node_stats_route_requires_auth(db) -> None:
    from httpx import ASGITransport, AsyncClient

    from shoreguard.api.auth import create_user

    await create_user("viewer@test.com", "viewerpass1", "viewer")
    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/system/node-stats")
        assert resp.status_code == 401
