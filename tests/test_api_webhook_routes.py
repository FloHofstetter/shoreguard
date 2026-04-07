"""Tests for the webhook management API endpoints (admin only)."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import shoreguard.services.webhooks as webhook_mod
from shoreguard.api import auth
from shoreguard.api.auth import create_user
from shoreguard.models import Base

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "adminpass123"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    auth.init_auth_for_test(factory)
    webhook_mod.webhook_service = webhook_mod.WebhookService(factory)
    yield factory
    auth.reset()
    webhook_mod.webhook_service = None
    engine.dispose()


@pytest.fixture
def _with_admin(db):
    create_user(ADMIN_EMAIL, ADMIN_PASS, "admin")


@pytest.fixture
def _with_viewer(db):
    create_user("viewer@test.com", "viewerpass1", "viewer")


@pytest.fixture
async def admin_client(db, _with_admin):
    from shoreguard.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
        )
        assert resp.status_code == 200
        yield client


@pytest.fixture
async def viewer_client(db, _with_viewer):
    from shoreguard.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/auth/login",
            json={"email": "viewer@test.com", "password": "viewerpass1"},
        )
        assert resp.status_code == 200
        yield client


async def _create_webhook(client: AsyncClient, **overrides) -> dict:
    body = {
        "url": "https://example.com/hook",
        "event_types": ["sandbox.created"],
        "channel_type": "generic",
    }
    body.update(overrides)
    resp = await client.post("/api/webhooks", json=body)
    assert resp.status_code == 201
    return resp.json()


class TestListWebhooks:
    async def test_list_empty(self, admin_client):
        resp = await admin_client.get("/api/webhooks")
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "total": 0}

    async def test_list_returns_created(self, admin_client):
        await _create_webhook(admin_client)
        resp = await admin_client.get("/api/webhooks")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["url"] == "https://example.com/hook"
        assert data["total"] == 1


class TestCreateWebhook:
    async def test_create_generic(self, admin_client):
        data = await _create_webhook(admin_client)
        assert data["url"] == "https://example.com/hook"
        assert data["channel_type"] == "generic"
        assert data["is_active"] is True
        assert "secret" in data
        assert "id" in data

    async def test_create_email_with_config(self, admin_client):
        data = await _create_webhook(
            admin_client,
            channel_type="email",
            extra_config={"smtp_host": "smtp.example.com", "to_addrs": ["a@b.com"]},
        )
        assert data["channel_type"] == "email"
        assert data["extra_config"]["smtp_host"] == "smtp.example.com"

    async def test_create_invalid_channel_type(self, admin_client):
        resp = await admin_client.post(
            "/api/webhooks",
            json={"url": "https://x.com", "event_types": ["*"], "channel_type": "invalid"},
        )
        assert resp.status_code == 400
        assert "channel_type" in resp.json()["detail"].lower()

    async def test_create_email_missing_config(self, admin_client):
        resp = await admin_client.post(
            "/api/webhooks",
            json={"url": "https://x.com", "event_types": ["*"], "channel_type": "email"},
        )
        assert resp.status_code == 400

    async def test_create_email_incomplete_config(self, admin_client):
        resp = await admin_client.post(
            "/api/webhooks",
            json={
                "url": "https://x.com",
                "event_types": ["*"],
                "channel_type": "email",
                "extra_config": {"smtp_host": "smtp.example.com"},
            },
        )
        assert resp.status_code == 400

    async def test_create_missing_required_fields(self, admin_client):
        resp = await admin_client.post("/api/webhooks", json={})
        assert resp.status_code == 422


class TestGetWebhook:
    async def test_get_existing(self, admin_client):
        created = await _create_webhook(admin_client)
        resp = await admin_client.get(f"/api/webhooks/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    async def test_get_nonexistent(self, admin_client):
        resp = await admin_client.get("/api/webhooks/99999")
        assert resp.status_code == 404


class TestUpdateWebhook:
    async def test_update_url(self, admin_client):
        created = await _create_webhook(admin_client)
        resp = await admin_client.put(
            f"/api/webhooks/{created['id']}",
            json={"url": "https://new.example.com/hook"},
        )
        assert resp.status_code == 200
        assert resp.json()["url"] == "https://new.example.com/hook"

    async def test_update_deactivate(self, admin_client):
        created = await _create_webhook(admin_client)
        resp = await admin_client.put(
            f"/api/webhooks/{created['id']}",
            json={"is_active": False},
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    async def test_update_invalid_channel_type(self, admin_client):
        created = await _create_webhook(admin_client)
        resp = await admin_client.put(
            f"/api/webhooks/{created['id']}",
            json={"channel_type": "invalid"},
        )
        assert resp.status_code == 400

    async def test_update_nonexistent(self, admin_client):
        resp = await admin_client.put(
            "/api/webhooks/99999",
            json={"url": "https://x.com"},
        )
        assert resp.status_code == 404


class TestDeleteWebhook:
    async def test_delete_existing(self, admin_client):
        created = await _create_webhook(admin_client)
        resp = await admin_client.delete(f"/api/webhooks/{created['id']}")
        assert resp.status_code == 204

        resp = await admin_client.get(f"/api/webhooks/{created['id']}")
        assert resp.status_code == 404

    async def test_delete_nonexistent(self, admin_client):
        resp = await admin_client.delete("/api/webhooks/99999")
        assert resp.status_code == 404


class TestTestWebhook:
    async def test_test_nonexistent(self, admin_client):
        resp = await admin_client.post("/api/webhooks/99999/test")
        assert resp.status_code == 404

    async def test_test_existing(self, admin_client):
        created = await _create_webhook(admin_client)
        resp = await admin_client.post(f"/api/webhooks/{created['id']}/test")
        assert resp.status_code == 200
        assert resp.json()["status"] == "Test event sent"


class TestListDeliveries:
    async def test_deliveries_nonexistent_webhook(self, admin_client):
        resp = await admin_client.get("/api/webhooks/99999/deliveries")
        assert resp.status_code == 404

    async def test_deliveries_empty(self, admin_client):
        created = await _create_webhook(admin_client)
        resp = await admin_client.get(f"/api/webhooks/{created['id']}/deliveries")
        assert resp.status_code == 200
        assert resp.json() == []


class TestRoleEnforcement:
    async def test_viewer_cannot_list_webhooks(self, viewer_client):
        resp = await viewer_client.get("/api/webhooks")
        assert resp.status_code == 403

    async def test_viewer_cannot_create_webhook(self, viewer_client):
        resp = await viewer_client.post(
            "/api/webhooks",
            json={"url": "https://x.com", "event_types": ["*"]},
        )
        assert resp.status_code == 403

    async def test_unauthenticated_gets_401(self, db, _with_admin):
        from shoreguard.api.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/webhooks")
            assert resp.status_code == 401


class TestServiceNotInitialised:
    async def test_503_when_service_missing(self, db, _with_admin):
        webhook_mod.webhook_service = None
        from shoreguard.api.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/auth/login",
                json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
            )
            assert resp.status_code == 200
            resp = await client.get("/api/webhooks")
            assert resp.status_code == 503
