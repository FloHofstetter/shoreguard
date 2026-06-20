"""Tests for the per-sandbox rate governor and reversible soft-pause."""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base, KillSwitchEntry, RatePauseEntry, SandboxUsage
from shoreguard.services.rate_governor import RateGovernorService
from shoreguard.settings import RateGovernorSettings


class _FakeSandboxes:
    def __init__(self) -> None:
        self.providers: dict[str, list[dict[str, Any]]] = {"agent-a": [{"name": "anthropic"}]}
        self.detached: list[tuple[str, str]] = []
        self.attached: list[tuple[str, str]] = []

    async def list(self, *, limit: int = 1000, offset: int = 0) -> list[dict[str, Any]]:
        return [{"name": n} for n in self.providers]

    async def list_providers(self, name: str) -> list[dict[str, Any]]:
        return list(self.providers.get(name, []))

    async def detach_provider(self, name: str, provider: str) -> dict[str, Any]:
        self.detached.append((name, provider))
        return {}

    async def attach_provider(self, name: str, provider: str) -> dict[str, Any]:
        self.attached.append((name, provider))
        return {}


class _FakeClient:
    def __init__(self) -> None:
        self.sandboxes = _FakeSandboxes()


class _FakeGatewayService:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client

    async def get_client(self, name: str) -> _FakeClient:
        return self._client


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
    svc = RateGovernorService(
        factory,
        _FakeGatewayService(client),  # type: ignore[arg-type]
        MagicMock(),
        RateGovernorSettings(cooldown_seconds=1),
    )
    yield svc, client, factory
    await engine.dispose()


async def _set_usage(factory, requests: int) -> None:
    async with factory() as session:
        row = (
            (
                await session.execute(
                    select(SandboxUsage).where(
                        SandboxUsage.gateway == "gw1", SandboxUsage.sandbox == "agent-a"
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            session.add(
                SandboxUsage(gateway="gw1", sandbox="agent-a", day="2026-06-13", requests=requests)
            )
        else:
            row.requests = requests
        await session.commit()


async def test_rate_limit_crud_and_validation(setup) -> None:
    svc, _client, _factory = setup
    assert await svc.get_rate_limit("gw1", "agent-a") is None
    rl = await svc.set_rate_limit("gw1", "agent-a", max_requests=10, window_seconds=60)
    assert rl["max_requests"] == 10 and rl["window_seconds"] == 60
    with pytest.raises(ValueError):
        await svc.set_rate_limit("gw1", "agent-a", max_requests=0, window_seconds=60)
    with pytest.raises(ValueError):
        await svc.set_rate_limit("gw1", "agent-a", max_requests=5, window_seconds=0)
    assert await svc.delete_rate_limit("gw1", "agent-a") is True
    assert await svc.delete_rate_limit("gw1", "agent-a") is False


async def test_evaluate_trips_soft_pause_not_kill_switch(setup, monkeypatch) -> None:
    svc, client, factory = setup
    fired: list[tuple[str, dict]] = []

    async def _fake_fire(event: str, payload: dict) -> None:
        fired.append((event, payload))

    monkeypatch.setattr("shoreguard.services.webhooks.fire_webhook", _fake_fire)
    await svc.set_rate_limit("gw1", "agent-a", max_requests=2, window_seconds=3600)
    await _set_usage(factory, 5)
    # First tick opens the window (baseline 5), nothing trips.
    assert (await svc.run_once())["paused"] == 0
    # Three more requests within the window → over the ceiling of 2.
    await _set_usage(factory, 8)
    assert (await svc.run_once())["paused"] == 1
    assert ("agent-a", "anthropic") in client.sandboxes.detached
    async with factory() as session:
        pause = (await session.execute(select(RatePauseEntry))).scalars().first()
        assert pause is not None and pause.reason == "rate_governor"
        # The governor never writes a kill-switch entry.
        assert (await session.execute(select(KillSwitchEntry))).scalars().first() is None
    assert [e for e, _ in fired] == ["rate.paused"]
    # Idempotent: re-evaluating does not double-pause.
    assert (await svc.run_once())["paused"] == 0


async def test_skips_sandbox_already_kill_switched(setup, monkeypatch) -> None:
    svc, client, factory = setup

    async def _fake_fire(event: str, payload: dict) -> None:
        return None

    monkeypatch.setattr("shoreguard.services.webhooks.fire_webhook", _fake_fire)
    async with factory() as session:
        session.add(
            KillSwitchEntry(
                gateway="gw1",
                sandbox="agent-a",
                providers_json="[]",
                engaged_at=datetime.datetime.now(datetime.UTC),
                engaged_by="human",
            )
        )
        await session.commit()
    await svc.set_rate_limit("gw1", "agent-a", max_requests=1, window_seconds=3600)
    await _set_usage(factory, 5)
    await svc.run_once()  # opens window
    await _set_usage(factory, 50)
    assert (await svc.run_once())["paused"] == 0  # skipped — already kill-switched
    assert client.sandboxes.detached == []
    async with factory() as session:
        assert (await session.execute(select(RatePauseEntry))).scalars().first() is None


async def test_auto_resume_reattaches_after_cooldown(setup, monkeypatch) -> None:
    svc, client, factory = setup
    fired: list[tuple[str, dict]] = []

    async def _fake_fire(event: str, payload: dict) -> None:
        fired.append((event, payload))

    monkeypatch.setattr("shoreguard.services.webhooks.fire_webhook", _fake_fire)
    await svc.set_rate_limit("gw1", "agent-a", max_requests=2, window_seconds=3600)
    await _set_usage(factory, 5)
    await svc.run_once()
    await _set_usage(factory, 8)
    await svc.run_once()  # paused
    # Make the cooldown elapse.
    async with factory() as session:
        entry = (await session.execute(select(RatePauseEntry))).scalars().first()
        entry.resume_after = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
        await session.commit()
    assert (await svc.run_once())["resumed"] == 1
    assert ("agent-a", "anthropic") in client.sandboxes.attached
    async with factory() as session:
        assert (await session.execute(select(RatePauseEntry))).scalars().first() is None
    assert "rate.resumed" in [e for e, _ in fired]
