"""Tests for the gateway restart reconciler (inventory store + reap-diff)."""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base
from shoreguard.services.gateway import GatewayService
from shoreguard.services.gateway_inventory import GatewayInventoryStore
from shoreguard.services.registry import GatewayRegistry


class _FakeSandboxes:
    def __init__(self, inventory: dict[str, list[str]]) -> None:
        self._inv = inventory

    async def list(self, *, limit: int = 1000, offset: int = 0) -> list[dict[str, Any]]:
        return [{"name": n} for n in self._inv]

    async def list_providers(self, name: str) -> list[dict[str, Any]]:
        return [{"name": p} for p in self._inv.get(name, [])]


class _FakeClient:
    def __init__(self, inventory: dict[str, list[str]], *, status: str = "healthy") -> None:
        self.sandboxes = _FakeSandboxes(inventory)
        self._status = status

    async def health(self) -> dict[str, Any]:
        return {"status": self._status}


@pytest.fixture
async def factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_capture_and_latest(factory) -> None:
    store = GatewayInventoryStore(factory)
    inv = await store.capture("gw", _FakeClient({"a": ["openai"], "b": []}))
    assert inv == {"a": ["openai"], "b": []}
    assert await store.latest("gw") == {"a": ["openai"], "b": []}
    assert await store.latest("other") is None
    snap = await store.latest_inventory("gw")
    assert snap is not None and snap["sandbox_count"] == 2


async def test_diff_and_record(factory) -> None:
    store = GatewayInventoryStore(factory)
    pre = {"a": ["openai"], "b": ["x"]}
    post = {"a": []}  # b reaped entirely; a lost its provider
    reaped = await store.diff_and_record("gw", pre, post, "unreachable")
    by_sb = {r["sandbox"]: r["lost_providers"] for r in reaped}
    assert by_sb["b"] == ["x"]
    assert by_sb["a"] == ["openai"]
    recent = await store.list_recent_reaps()
    assert recent[0]["reaped_count"] == 2
    assert recent[0]["gateway"] == "gw"
    # Nothing lost → no record, empty result.
    assert await store.diff_and_record("gw", {"a": []}, {"a": []}, "unreachable") == []
    since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    assert await store.count_reaps_since(since) == 2


async def test_reconcile_on_recovery_fires_reaped_event(factory, monkeypatch) -> None:
    registry = GatewayRegistry(factory)
    store = GatewayInventoryStore(factory)
    svc = GatewayService(registry, store)
    await registry.register("gw", "10.0.0.1:8443", auth_mode="insecure")
    # Pre-down snapshot: two sandboxes attached.
    await store.capture("gw", _FakeClient({"a": ["openai"], "b": ["x"]}))
    # Mark it down so the next probe is an unreachable→recovered transition.
    await registry.update_health("gw", "unreachable", datetime.datetime.now(datetime.UTC))

    fired: list[tuple[str, dict]] = []

    async def _fake_fire(event: str, payload: dict) -> None:
        fired.append((event, payload))

    monkeypatch.setattr("shoreguard.services.webhooks.fire_webhook", _fake_fire)
    # On recovery only "a" survives; "b" was reaped by the restart.
    post_client = _FakeClient({"a": ["openai"]}, status="healthy")
    with patch.object(svc, "get_client", new=AsyncMock(return_value=post_client)):
        await svc.check_all_health()

    events = [e for e, _ in fired]
    assert "gateway.recovered" in events
    reaped = [p for e, p in fired if e == "gateway.sandboxes_reaped"]
    assert len(reaped) == 1
    assert reaped[0]["reaped_count"] == 1
    assert reaped[0]["reaped"][0]["sandbox"] == "b"
    # The reap was persisted.
    assert (await store.list_recent_reaps())[0]["reaped_count"] == 1


async def test_at_risk_gateways(monkeypatch) -> None:
    from shoreguard.services.update_check import UpdateCheckService
    from shoreguard.settings import UpdateSettings, reset_settings

    monkeypatch.setenv("SHOREGUARD_RECONCILER_RESTART_SAFE_MIN_VERSION", "0.0.55")
    reset_settings()
    svc = GatewayService(MagicMock())
    svc._versions = {"old-gw": "0.0.50", "new-gw": "0.0.60"}  # noqa: SLF001
    status = UpdateCheckService(svc, UpdateSettings()).status()
    assert status["restart_safe_min_version"] == "0.0.55"
    assert status["at_risk_gateways"] == ["old-gw"]
