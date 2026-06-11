"""Tests for Web Push: VAPID keys, subscriptions, delivery, and routes."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base
from shoreguard.services.push import PushService
from shoreguard.settings import PushSettings


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / "shoreguard"


@pytest.fixture
async def push_svc(config_dir):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = PushService(factory, PushSettings())
    yield svc
    await engine.dispose()


class _FakeWebPushException(Exception):
    def __init__(self, msg: str, response=None) -> None:
        super().__init__(msg)
        self.response = response


def _patch_pywebpush(webpush_mock: MagicMock):
    mod = MagicMock()
    mod.webpush = webpush_mock
    mod.WebPushException = _FakeWebPushException
    return patch.dict("sys.modules", {"pywebpush": mod})


SUB = {"endpoint": "https://push.example.com/sub/1", "p256dh": "pk", "auth": "as"}


async def _subscribe(svc: PushService, endpoint: str = SUB["endpoint"], email: str = "a@b.com"):
    return await svc.subscribe(
        user_email=email, endpoint=endpoint, p256dh="pk", auth="as", user_agent="UA"
    )


# ─── VAPID keys ──────────────────────────────────────────────────────────────


def test_vapid_key_generated_and_persisted(config_dir, push_svc) -> None:
    key1 = push_svc.public_key()
    key_file = config_dir / ".vapid_private"
    assert key_file.is_file()
    assert oct(key_file.stat().st_mode & 0o777) == "0o600"
    # A second service instance loads the same key.
    svc2 = PushService(push_svc._session_factory, PushSettings())
    assert svc2.public_key() == key1
    # Uncompressed P-256 point is 65 bytes → 87 base64url chars.
    assert len(key1) == 87


# ─── Subscription CRUD ───────────────────────────────────────────────────────


async def test_subscribe_upserts_by_endpoint(push_svc) -> None:
    first = await _subscribe(push_svc)
    second = await _subscribe(push_svc, email="other@b.com")
    assert first["id"] == second["id"]
    subs = await push_svc.list_for_user("other@b.com")
    assert len(subs) == 1
    assert await push_svc.list_for_user("a@b.com") == []


async def test_unsubscribe(push_svc) -> None:
    await _subscribe(push_svc)
    assert await push_svc.unsubscribe(SUB["endpoint"]) is True
    assert await push_svc.unsubscribe(SUB["endpoint"]) is False
    assert await push_svc.list_for_user("a@b.com") == []


async def test_endpoint_truncated_in_listing(push_svc) -> None:
    long_endpoint = "https://push.example.com/" + "x" * 100
    await _subscribe(push_svc, endpoint=long_endpoint)
    subs = await push_svc.list_for_user("a@b.com")
    assert len(subs[0]["endpoint"]) <= 61
    assert subs[0]["endpoint"].endswith("…")


# ─── Sending ─────────────────────────────────────────────────────────────────


async def test_send_payload_delivers_to_all(push_svc) -> None:
    await _subscribe(push_svc, endpoint="https://push.example.com/1")
    await _subscribe(push_svc, endpoint="https://push.example.com/2", email="b@b.com")

    webpush = MagicMock()
    with _patch_pywebpush(webpush):
        result = await push_svc.send_payload(json.dumps({"title": "t", "body": "b", "url": "/"}))

    assert result == {"sent": 2, "failed": 0, "pruned": 0}
    assert webpush.call_count == 2
    kwargs = webpush.call_args.kwargs
    assert kwargs["subscription_info"]["keys"] == {"p256dh": "pk", "auth": "as"}
    assert kwargs["vapid_claims"]["sub"] == "mailto:admin@localhost"


async def test_send_payload_only_email_filter(push_svc) -> None:
    await _subscribe(push_svc, endpoint="https://push.example.com/1", email="a@b.com")
    await _subscribe(push_svc, endpoint="https://push.example.com/2", email="b@b.com")

    webpush = MagicMock()
    with _patch_pywebpush(webpush):
        result = await push_svc.send_payload("{}", only_email="a@b.com")

    assert result["sent"] == 1
    assert webpush.call_count == 1


async def test_send_payload_prunes_gone_subscriptions(push_svc) -> None:
    await _subscribe(push_svc)

    response = MagicMock(status_code=410)
    webpush = MagicMock(side_effect=_FakeWebPushException("gone", response=response))
    with _patch_pywebpush(webpush):
        result = await push_svc.send_payload("{}")

    assert result == {"sent": 0, "failed": 0, "pruned": 1}
    assert await push_svc.list_for_user("a@b.com") == []


async def test_send_payload_counts_other_failures(push_svc) -> None:
    await _subscribe(push_svc)

    webpush = MagicMock(side_effect=OSError("network down"))
    with _patch_pywebpush(webpush):
        result = await push_svc.send_payload("{}")

    assert result == {"sent": 0, "failed": 1, "pruned": 0}
    assert len(await push_svc.list_for_user("a@b.com")) == 1


# ─── Routes ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db(config_dir):
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


@pytest.fixture
async def client(db):
    from httpx import ASGITransport, AsyncClient

    from shoreguard.api.auth import create_user
    from shoreguard.api.main import app

    await create_user("op@test.com", "operatorpass1", "operator")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/auth/login", json={"email": "op@test.com", "password": "operatorpass1"}
        )
        assert resp.status_code == 200
        yield c


async def test_public_key_route(client) -> None:
    resp = await client.get("/api/push/public-key")
    assert resp.status_code == 200
    assert len(resp.json()["public_key"]) == 87


async def test_subscribe_list_delete_flow(client) -> None:
    resp = await client.post(
        "/api/push/subscriptions",
        json={"endpoint": SUB["endpoint"], "keys": {"p256dh": "pk", "auth": "as"}},
    )
    assert resp.status_code == 201
    assert resp.json()["user_email"] == "op@test.com"

    resp = await client.get("/api/push/subscriptions")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = await client.delete(
        "/api/push/subscriptions", params={"endpoint": SUB["endpoint"]}
    )
    assert resp.status_code == 204

    resp = await client.get("/api/push/subscriptions")
    assert resp.json() == []


async def test_push_test_without_devices_404(client) -> None:
    resp = await client.post("/api/push/test")
    assert resp.status_code == 404


async def test_push_requires_auth(db) -> None:
    from httpx import ASGITransport, AsyncClient

    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/push/public-key")
        assert resp.status_code == 401


async def test_service_worker_served() -> None:
    from httpx import ASGITransport, AsyncClient

    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/sw.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]
        assert "showNotification" in resp.text
