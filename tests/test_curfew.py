"""Tests for the agent curfew (quiet hours) service and routes."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base
from shoreguard.services.curfew import CURFEW_ACTOR, CurfewService, minute_in_window

# ─── Window logic ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("minute", "start", "end", "expected"),
    [
        (600, 540, 1020, True),  # 10:00 in 09:00–17:00
        (520, 540, 1020, False),  # 08:40 outside
        (1020, 540, 1020, False),  # end is exclusive
        (1380, 1320, 420, True),  # 23:00 in 22:00–07:00 (overnight)
        (180, 1320, 420, True),  # 03:00 in overnight window
        (600, 1320, 420, False),  # 10:00 outside overnight window
        (540, 540, 540, False),  # empty window
    ],
)
def test_minute_in_window(minute: int, start: int, end: int, expected: bool) -> None:
    assert minute_in_window(minute, start, end) is expected


# ─── Service ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def setup():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    kill_switch = AsyncMock()
    kill_switch.status.return_value = {"engaged": False, "engaged_by": None}
    kill_switch.engage.return_value = {"gateway": "gw1", "sandboxes": [{}], "errors": []}
    kill_switch.resume.return_value = {"gateway": "gw1", "sandboxes": [{}], "errors": []}
    svc = CurfewService(factory, kill_switch)
    yield svc, kill_switch
    await engine.dispose()


@pytest.fixture
def fire(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr("shoreguard.services.webhooks.fire_webhook", mock)
    return mock


async def test_set_get_delete(setup) -> None:
    svc, _ = setup
    assert await svc.get("gw1") is None
    stored = await svc.set("gw1", enabled=True, start_minute=1320, end_minute=420, timezone="UTC")
    assert stored["start_minute"] == 1320
    fetched = await svc.get("gw1")
    assert fetched is not None and fetched["timezone"] == "UTC"
    # Upsert updates in place.
    await svc.set("gw1", enabled=False, start_minute=0, end_minute=60, timezone="Europe/Berlin")
    updated = await svc.get("gw1")
    assert updated is not None
    assert updated["enabled"] is False
    assert updated["timezone"] == "Europe/Berlin"
    assert await svc.delete("gw1") is True
    assert await svc.delete("gw1") is False


async def test_set_rejects_bad_timezone(setup) -> None:
    svc, _ = setup
    with pytest.raises(ValueError, match="timezone"):
        await svc.set("gw1", enabled=True, start_minute=0, end_minute=60, timezone="Mars/Olympus")


async def test_run_once_engages_inside_window(setup, fire) -> None:
    svc, kill_switch = setup
    # 00:00–23:59 window: always inside.
    await svc.set("gw1", enabled=True, start_minute=0, end_minute=1439, timezone="UTC")

    actions = await svc.run_once()

    kill_switch.engage.assert_awaited_once_with("gw1", actor=CURFEW_ACTOR)
    assert actions == [{"gateway": "gw1", "action": "engaged"}]
    event, payload = fire.await_args.args
    assert event == "kill_switch.engaged"
    assert payload["actor"] == CURFEW_ACTOR


async def test_run_once_idempotent_when_already_engaged(setup, fire) -> None:
    svc, kill_switch = setup
    kill_switch.status.return_value = {"engaged": True, "engaged_by": CURFEW_ACTOR}
    await svc.set("gw1", enabled=True, start_minute=0, end_minute=1439, timezone="UTC")

    actions = await svc.run_once()

    kill_switch.engage.assert_not_awaited()
    kill_switch.resume.assert_not_awaited()
    assert actions == []


async def test_run_once_releases_outside_window(setup, fire) -> None:
    svc, kill_switch = setup
    kill_switch.status.return_value = {"engaged": True, "engaged_by": CURFEW_ACTOR}
    # Empty window: always outside.
    await svc.set("gw1", enabled=True, start_minute=0, end_minute=0, timezone="UTC")

    actions = await svc.run_once()

    kill_switch.resume.assert_awaited_once_with("gw1", actor=CURFEW_ACTOR)
    assert actions == [{"gateway": "gw1", "action": "released"}]
    assert fire.await_args.args[0] == "kill_switch.released"


async def test_run_once_never_releases_foreign_engagement(setup, fire) -> None:
    svc, kill_switch = setup
    kill_switch.status.return_value = {"engaged": True, "engaged_by": "admin@test.com"}
    await svc.set("gw1", enabled=True, start_minute=0, end_minute=0, timezone="UTC")

    actions = await svc.run_once()

    kill_switch.resume.assert_not_awaited()
    assert actions == []


async def test_run_once_skips_disabled(setup, fire) -> None:
    svc, kill_switch = setup
    await svc.set("gw1", enabled=False, start_minute=0, end_minute=1439, timezone="UTC")

    actions = await svc.run_once()

    kill_switch.status.assert_not_awaited()
    assert actions == []


async def test_run_once_gateway_error_does_not_block_others(setup, fire) -> None:
    svc, kill_switch = setup
    await svc.set("gw1", enabled=True, start_minute=0, end_minute=1439, timezone="UTC")
    await svc.set("gw2", enabled=True, start_minute=0, end_minute=1439, timezone="UTC")
    kill_switch.engage.side_effect = [RuntimeError("boom"), {"sandboxes": [{}], "errors": []}]

    actions = await svc.run_once()

    assert actions == [{"gateway": "gw2", "action": "engaged"}]


# ─── Routes ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


@pytest.fixture
async def admin_client(db):
    from httpx import ASGITransport, AsyncClient

    from shoreguard.api.auth import create_user
    from shoreguard.api.main import app

    await create_user("admin@test.com", "adminpassword1", "admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/login", json={"email": "admin@test.com", "password": "adminpassword1"}
        )
        assert resp.status_code == 200
        yield client


async def test_curfew_route_crud(admin_client) -> None:
    resp = await admin_client.get("/api/gateway/gw1/curfew")
    assert resp.status_code == 200
    assert resp.json() == {"configured": False}

    resp = await admin_client.put(
        "/api/gateway/gw1/curfew",
        json={"enabled": True, "start_minute": 1320, "end_minute": 420, "timezone": "UTC"},
    )
    assert resp.status_code == 200
    assert resp.json()["start_minute"] == 1320

    resp = await admin_client.get("/api/gateway/gw1/curfew")
    assert resp.json()["end_minute"] == 420

    resp = await admin_client.delete("/api/gateway/gw1/curfew")
    assert resp.status_code == 204
    resp = await admin_client.delete("/api/gateway/gw1/curfew")
    assert resp.status_code == 404


async def test_curfew_route_rejects_bad_timezone(admin_client) -> None:
    resp = await admin_client.put(
        "/api/gateway/gw1/curfew",
        json={"enabled": True, "start_minute": 0, "end_minute": 60, "timezone": "Nope/Nope"},
    )
    assert resp.status_code == 400
