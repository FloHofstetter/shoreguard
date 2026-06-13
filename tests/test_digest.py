"""Tests for the daily activity digest service and route."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from shoreguard.models import AuditEntry, Base, KillSwitchEntry, Webhook, WebhookDelivery
from shoreguard.services.digest import DIGEST_SENT_ACTION, DigestService


class _FakeRegistry:
    def __init__(self, gateways: list[dict] | None = None):
        self._gateways = gateways or []

    async def list_all(self) -> list[dict]:
        return self._gateways


class _FakeAudit:
    def __init__(self) -> None:
        self.logged: list[dict] = []

    async def log(self, **kwargs) -> None:
        self.logged.append(kwargs)


class _FakeBudget:
    def __init__(self, top: list[dict] | None = None) -> None:
        self._top = top or []

    async def summary(self, *, days: int = 7) -> dict:
        return {"since": "2026-06-13", "top": self._top}


@pytest.fixture
async def db_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _audit_row(action: str, *, hours_ago: float = 1.0) -> AuditEntry:
    return AuditEntry(
        timestamp=datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=hours_ago),
        actor="t@x",
        actor_role="admin",
        action=action,
        resource_type="sandbox",
        resource_id="sb1",
    )


async def test_build_aggregates_window(db_factory) -> None:
    async with db_factory() as session:
        session.add_all(
            [
                _audit_row("sandbox.create"),
                _audit_row("sandbox.create"),
                _audit_row("approval.approve"),
                _audit_row("auth.forbidden"),
                _audit_row("sandbox.create", hours_ago=48),  # outside window
            ]
        )
        wh = Webhook(
            url="https://x",
            secret="s",
            event_types="[]",
            channel_type="generic",
            is_active=True,
            created_by="t@x",
            created_at=datetime.datetime.now(datetime.UTC),
        )
        session.add(wh)
        await session.flush()
        session.add(
            WebhookDelivery(
                webhook_id=wh.id,
                event_type="t",
                payload_json="{}",
                status="failed",
                created_at=datetime.datetime.now(datetime.UTC),
            )
        )
        session.add(
            KillSwitchEntry(
                gateway="gw1",
                sandbox="sb1",
                providers_json="[]",
                engaged_at=datetime.datetime.now(datetime.UTC),
                engaged_by="a",
            )
        )
        await session.commit()

    svc = DigestService(
        db_factory,
        _FakeRegistry([{"name": "gw1", "last_status": "unreachable"}]),  # type: ignore[arg-type]
    )
    digest = await svc.build(hours=24)
    assert digest["audit"]["total"] == 4
    assert digest["sandboxes"]["created"] == 2
    assert digest["approvals"]["approved"] == 1
    assert digest["audit"]["forbidden"] == 1
    assert digest["webhook_failures"] == 1
    assert digest["kill_switch_engaged"] == ["gw1"]
    assert digest["gateways"]["unreachable"] == ["gw1"]
    assert "4 actions" in digest["message"]
    assert "kill switch on: gw1" in digest["message"]


async def test_build_includes_todays_spend(db_factory) -> None:
    budget = _FakeBudget(
        [
            {"gateway": "gw1", "sandbox": "claude", "requests": 100},
            {"gateway": "gw1", "sandbox": "nightly", "requests": 42},
        ]
    )
    svc = DigestService(db_factory, _FakeRegistry(), budget)  # type: ignore[arg-type]
    digest = await svc.build(hours=24)
    assert digest["spending"]["today_total"] == 142
    assert digest["spending"]["top"][0]["sandbox"] == "claude"
    assert "142 inference requests" in digest["message"]


async def test_build_without_budget_omits_spend_phrase(db_factory) -> None:
    svc = DigestService(db_factory, _FakeRegistry())  # type: ignore[arg-type]
    digest = await svc.build(hours=24)
    assert digest["spending"] == {"today_total": 0, "top": []}
    assert "inference requests" not in digest["message"]


async def test_dispatch_if_due_once_per_day(db_factory, monkeypatch) -> None:
    fired: list[tuple[str, dict]] = []

    async def _fake_fire(event: str, payload: dict) -> None:
        fired.append((event, payload))

    monkeypatch.setattr("shoreguard.services.webhooks.fire_webhook", _fake_fire)

    svc = DigestService(db_factory, _FakeRegistry())  # type: ignore[arg-type]
    audit = _FakeAudit()
    current_hour = datetime.datetime.now().astimezone().hour

    # Not yet due (configured hour is in the future)
    if current_hour < 23:
        assert await svc.dispatch_if_due(hour=23, audit=audit) is False
        assert fired == []

    # Due now
    assert await svc.dispatch_if_due(hour=0, audit=audit) is True
    assert fired and fired[0][0] == "digest.daily"
    assert audit.logged and audit.logged[0]["action"] == DIGEST_SENT_ACTION

    # Marker persisted → second call same day is a no-op
    async with db_factory() as session:
        session.add(_make_sent_marker())
        await session.commit()
    assert await svc.dispatch_if_due(hour=0, audit=audit) is False
    assert len(fired) == 1


def _make_sent_marker() -> AuditEntry:
    return AuditEntry(
        timestamp=datetime.datetime.now(datetime.UTC),
        actor="system",
        actor_role="system",
        action=DIGEST_SENT_ACTION,
        resource_type="digest",
        resource_id="",
    )


@pytest.fixture
def db():
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


async def test_route_returns_digest(db) -> None:
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
        resp = await client.get("/api/digest")
        assert resp.status_code == 200
        body = resp.json()
        assert "audit" in body and "message" in body
