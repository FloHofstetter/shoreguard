"""Tests for the reversible gateway kill switch and health-transition events."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base
from shoreguard.services.kill_switch import KillSwitchService


class _FakeSandboxes:
    """In-memory stand-in for the client's sandbox submanager."""

    def __init__(self) -> None:
        self.attachments: dict[str, list[str]] = {
            "agent-a": ["anthropic", "github"],
            "agent-b": ["openai"],
            "agent-c": [],
        }
        self.fail_detach: set[str] = set()
        self.fail_attach: set[str] = set()

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return [{"name": n} for n in self.attachments]

    async def list_providers(self, sandbox_name: str) -> list[dict[str, Any]]:
        return [{"name": p} for p in self.attachments[sandbox_name]]

    async def detach_provider(self, sandbox_name: str, provider_name: str) -> dict[str, Any]:
        if provider_name in self.fail_detach:
            raise RuntimeError("detach boom")
        self.attachments[sandbox_name].remove(provider_name)
        return {}

    async def attach_provider(self, sandbox_name: str, provider_name: str) -> dict[str, Any]:
        if provider_name in self.fail_attach:
            raise RuntimeError("attach boom")
        self.attachments[sandbox_name].append(provider_name)
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
    svc = KillSwitchService(factory, _FakeGatewayService(client))  # type: ignore[arg-type]
    yield svc, client
    await engine.dispose()


async def test_engage_detaches_and_records(setup) -> None:
    svc, client = setup
    report = await svc.engage("gw1", actor="admin@x")
    assert {s["name"] for s in report["sandboxes"]} == {"agent-a", "agent-b", "agent-c"}
    assert report["errors"] == []
    # Everything detached on the gateway side
    assert client.sandboxes.attachments == {"agent-a": [], "agent-b": [], "agent-c": []}
    status = await svc.status("gw1")
    assert status["engaged"] is True
    assert status["sandboxes"] == 3
    assert status["engaged_by"] == "admin@x"


async def test_engage_twice_refused(setup) -> None:
    svc, _client = setup
    await svc.engage("gw1", actor="a")
    with pytest.raises(RuntimeError, match="already engaged"):
        await svc.engage("gw1", actor="a")


async def test_resume_reattaches_and_clears(setup) -> None:
    svc, client = setup
    await svc.engage("gw1", actor="a")
    report = await svc.resume("gw1", actor="a")
    assert report["errors"] == []
    assert sorted(client.sandboxes.attachments["agent-a"]) == ["anthropic", "github"]
    assert client.sandboxes.attachments["agent-b"] == ["openai"]
    assert (await svc.status("gw1"))["engaged"] is False


async def test_resume_keeps_failed_entries_for_retry(setup) -> None:
    svc, client = setup
    await svc.engage("gw1", actor="a")
    client.sandboxes.fail_attach = {"anthropic"}
    report = await svc.resume("gw1", actor="a")
    assert any("anthropic" in e for e in report["errors"])
    # agent-a's entry survives for a retry; agent-b/c entries are gone
    status = await svc.status("gw1")
    assert status["engaged"] is True
    assert status["sandboxes"] == 1
    # Retry after the failure clears
    client.sandboxes.fail_attach = set()
    report = await svc.resume("gw1", actor="a")
    assert report["errors"] == []
    assert (await svc.status("gw1"))["engaged"] is False
    assert sorted(client.sandboxes.attachments["agent-a"]) == ["anthropic", "github"]


async def test_engage_partial_detach_failure_recorded(setup) -> None:
    svc, client = setup
    client.sandboxes.fail_detach = {"github"}
    report = await svc.engage("gw1", actor="a")
    assert any("github" in e for e in report["errors"])
    # github stays attached; the stored entry only lists what was detached
    assert client.sandboxes.attachments["agent-a"] == ["github"]
    await svc.resume("gw1", actor="a")
    assert sorted(client.sandboxes.attachments["agent-a"]) == ["anthropic", "github"]


# ─── health-transition webhook events ──────────────────────────────────────


async def test_health_transition_fires_events(monkeypatch) -> None:
    from shoreguard.services.gateway import GatewayService

    fired: list[tuple[str, dict]] = []

    async def _fake_fire(event: str, payload: dict) -> None:
        fired.append((event, payload))

    class _Registry:
        def __init__(self) -> None:
            self.rows = [
                {"name": "up-goes-down", "last_status": "connected"},
                {"name": "down-comes-up", "last_status": "unreachable"},
                {"name": "never-seen", "last_status": "unknown"},
                {"name": "stays-up", "last_status": "connected"},
            ]

        async def list_all(self) -> list[dict]:
            return self.rows

        async def update_health(self, name: str, status: str, ts) -> None:
            pass

    class _Client:
        def __init__(self, status: str) -> None:
            self._status = status

        async def health(self) -> dict:
            return {"status": self._status}

    statuses = {
        "up-goes-down": "unreachable",
        "down-comes-up": "connected",
        "never-seen": "unreachable",
        "stays-up": "connected",
    }

    svc = GatewayService.__new__(GatewayService)
    svc._registry = _Registry()  # type: ignore[attr-defined]

    async def _get_client(*, name: str) -> _Client:
        return _Client(statuses[name])

    monkeypatch.setattr(svc, "get_client", _get_client)
    monkeypatch.setattr("shoreguard.services.webhooks.fire_webhook", _fake_fire)

    await svc.check_all_health()

    events = {(e, p["gateway"]) for e, p in fired}
    assert ("gateway.unreachable", "up-goes-down") in events
    assert ("gateway.recovered", "down-comes-up") in events
    # never-seen (unknown → unreachable) and stays-up fire nothing
    assert len(events) == 2
