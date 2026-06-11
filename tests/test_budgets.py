"""Tests for inference usage metering and sandbox budgets."""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base, KillSwitchEntry, SandboxUsage, UsageCursor
from shoreguard.services.budgets import BudgetService
from shoreguard.settings import BudgetSettings


def _now_ms() -> int:
    return int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)


class _FakeSandboxes:
    def __init__(self) -> None:
        self.names = ["agent-a"]
        self.logs: list[dict[str, Any]] = []
        self.providers: dict[str, list[str]] = {"agent-a": ["anthropic"]}
        self.detached: list[tuple[str, str]] = []

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return [{"name": n} for n in self.names]

    async def get_logs(self, sandbox_id: str, *, lines: int = 200, since_ms: int = 0, **kw):
        return [line for line in self.logs if line["timestamp_ms"] > since_ms][:lines]

    async def list_providers(self, sandbox_name: str) -> list[dict[str, Any]]:
        return [{"name": p} for p in self.providers.get(sandbox_name, [])]

    async def detach_provider(self, sandbox_name: str, provider_name: str) -> dict[str, Any]:
        self.detached.append((sandbox_name, provider_name))
        return {}


class _FakeClient:
    def __init__(self) -> None:
        self.sandboxes = _FakeSandboxes()


class _FakeGatewayService:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client

    async def get_client(self, name: str) -> _FakeClient:
        return self._client


class _FakeRegistry:
    async def list_all(self) -> list[dict[str, Any]]:
        return [{"name": "gw1", "last_status": "connected"}]


@pytest.fixture
async def setup():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    client = _FakeClient()
    svc = BudgetService(
        factory,
        _FakeGatewayService(client),  # type: ignore[arg-type]
        _FakeRegistry(),  # type: ignore[arg-type]
        BudgetSettings(),
    )
    yield svc, client, factory
    await engine.dispose()


def _log(ts: int, source: str = "inference", target: str = "") -> dict[str, Any]:
    return {"timestamp_ms": ts, "source": source, "target": target, "message": "x"}


async def test_first_poll_sets_cursor_without_billing_history(setup) -> None:
    svc, client, factory = setup
    client.sandboxes.logs = [_log(_now_ms() - 1000)]
    result = await svc.poll_once()
    assert result["counted"] == 0
    async with factory() as session:
        cursor = (await session.execute(select(UsageCursor))).scalars().first()
        assert cursor is not None and cursor.last_ms > 0
        assert (await session.execute(select(SandboxUsage))).scalars().first() is None


async def test_poll_counts_inference_lines_and_advances_cursor(setup) -> None:
    svc, client, _factory = setup
    await svc.poll_once()  # establish cursor
    base = _now_ms()
    client.sandboxes.logs = [
        _log(base + 1000),
        _log(base + 2000, source="proxy"),
        _log(base + 3000, source="supervisor"),  # not inference
        _log(base + 4000, source="gateway", target="inference.local"),
    ]
    result = await svc.poll_once()
    assert result["counted"] == 3
    usage = await svc.usage("gw1", "agent-a")
    assert usage["today"] == 3
    # Re-poll with no new logs: cursor advanced past everything
    result = await svc.poll_once()
    assert result["counted"] == 0
    assert (await svc.usage("gw1", "agent-a"))["today"] == 3


async def test_budget_crud_and_validation(setup) -> None:
    svc, _client, _factory = setup
    assert await svc.get_budget("gw1", "agent-a") is None
    budget = await svc.set_budget(
        "gw1", "agent-a", limit_requests=10, window="daily", action="notify"
    )
    assert budget["limit_requests"] == 10
    budget = await svc.set_budget(
        "gw1", "agent-a", limit_requests=5, window="weekly", action="detach"
    )
    assert budget["window"] == "weekly"
    with pytest.raises(ValueError):
        await svc.set_budget("gw1", "agent-a", limit_requests=5, window="hourly", action="notify")
    with pytest.raises(ValueError):
        await svc.set_budget("gw1", "agent-a", limit_requests=5, window="daily", action="explode")
    assert await svc.delete_budget("gw1", "agent-a") is True
    assert await svc.delete_budget("gw1", "agent-a") is False


async def test_budget_notify_fires_once_per_window(setup, monkeypatch) -> None:
    svc, client, _factory = setup
    fired: list[tuple[str, dict]] = []

    async def _fake_fire(event: str, payload: dict) -> None:
        fired.append((event, payload))

    monkeypatch.setattr("shoreguard.services.webhooks.fire_webhook", _fake_fire)
    await svc.poll_once()  # cursor
    await svc.set_budget("gw1", "agent-a", limit_requests=2, window="daily", action="notify")
    base = _now_ms()
    client.sandboxes.logs = [_log(base + 1000), _log(base + 2000), _log(base + 3000)]
    await svc.poll_once()
    assert [e for e, _ in fired] == ["budget.exceeded"]
    assert fired[0][1]["used"] >= 2
    # Second evaluation in the same window must not re-fire
    client.sandboxes.logs = [_log(base + 4000)]
    await svc.poll_once()
    assert len(fired) == 1
    # No providers were detached for action=notify
    assert client.sandboxes.detached == []


async def test_budget_detach_cuts_providers_and_records_entry(setup, monkeypatch) -> None:
    svc, client, factory = setup

    async def _fake_fire(event: str, payload: dict) -> None:
        pass

    monkeypatch.setattr("shoreguard.services.webhooks.fire_webhook", _fake_fire)
    await svc.poll_once()
    await svc.set_budget("gw1", "agent-a", limit_requests=1, window="daily", action="detach")
    base = _now_ms()
    client.sandboxes.logs = [_log(base + 1000), _log(base + 2000)]
    await svc.poll_once()
    assert ("agent-a", "anthropic") in client.sandboxes.detached
    async with factory() as session:
        entry = (await session.execute(select(KillSwitchEntry))).scalars().first()
        assert entry is not None
        assert entry.engaged_by == "budget"
        assert entry.sandbox == "agent-a"


async def test_summary_lists_top_consumers(setup) -> None:
    svc, client, _factory = setup
    await svc.poll_once()
    base = _now_ms()
    client.sandboxes.logs = [_log(base + i * 100) for i in range(1, 6)]
    await svc.poll_once()
    summary = await svc.summary()
    assert summary["top"] == [{"gateway": "gw1", "sandbox": "agent-a", "requests": 5}]
