"""Integration tests for pages.py auth API endpoints."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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
    yield factory
    auth.reset()
    engine.dispose()


@pytest.fixture
def _with_admin(db):
    create_user(ADMIN_EMAIL, ADMIN_PASS, "admin")


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
async def fresh_client(db):
    """Client against an empty DB (no users = setup not complete)."""
    from shoreguard.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


# ─── Setup endpoint ──────────────────────────────────────────────────────────


class TestSetup:
    async def test_setup_success(self, fresh_client):
        resp = await fresh_client.post(
            "/api/auth/setup",
            json={"email": "new@test.com", "password": "securepass"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_setup_already_complete(self, admin_client):
        resp = await admin_client.post(
            "/api/auth/setup",
            json={"email": "other@test.com", "password": "securepass"},
        )
        assert resp.status_code == 400
        assert "already complete" in resp.json()["detail"].lower()

    async def test_setup_invalid_email(self, fresh_client):
        resp = await fresh_client.post(
            "/api/auth/setup",
            json={"email": "not-an-email", "password": "securepass"},
        )
        assert resp.status_code == 400
        assert "email" in resp.json()["detail"].lower()

    async def test_setup_short_password(self, fresh_client):
        resp = await fresh_client.post(
            "/api/auth/setup",
            json={"email": "admin@test.com", "password": "short"},
        )
        assert resp.status_code == 400
        assert "8 characters" in resp.json()["detail"]

    async def test_setup_long_password(self, fresh_client):
        resp = await fresh_client.post(
            "/api/auth/setup",
            json={"email": "admin@test.com", "password": "x" * 200},
        )
        assert resp.status_code == 422

    async def test_setup_empty_fields(self, fresh_client):
        resp = await fresh_client.post(
            "/api/auth/setup",
            json={"email": "", "password": ""},
        )
        assert resp.status_code == 422


# ─── Login endpoint ──────────────────────────────────────────────────────────


class TestLogin:
    async def test_login_before_setup(self, fresh_client):
        resp = await fresh_client.post(
            "/api/auth/login",
            json={"email": "x@test.com", "password": "whatever"},
        )
        assert resp.status_code == 400
        assert "setup" in resp.json()["detail"].lower()

    async def test_login_invalid_credentials(self, db, _with_admin):
        from shoreguard.api.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/auth/login",
                json={"email": ADMIN_EMAIL, "password": "wrongpass"},
            )
            assert resp.status_code == 401

    async def test_login_long_password(self, db, _with_admin):
        from shoreguard.api.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/auth/login",
                json={"email": ADMIN_EMAIL, "password": "x" * 200},
            )
            assert resp.status_code == 422


# ─── User management (admin-only) ────────────────────────────────────────────


class TestUserManagement:
    async def test_create_user_invalid_role(self, admin_client):
        resp = await admin_client.post(
            "/api/auth/users",
            json={"email": "new@test.com", "role": "superadmin"},
        )
        assert resp.status_code == 400
        assert "role" in resp.json()["detail"].lower()

    async def test_create_user_invalid_email(self, admin_client):
        resp = await admin_client.post(
            "/api/auth/users",
            json={"email": "not-valid", "role": "viewer"},
        )
        assert resp.status_code == 400
        assert "email" in resp.json()["detail"].lower()

    async def test_create_user_empty_email(self, admin_client):
        resp = await admin_client.post(
            "/api/auth/users",
            json={"email": "", "role": "viewer"},
        )
        assert resp.status_code == 400

    async def test_create_user_duplicate_email(self, admin_client):
        resp1 = await admin_client.post(
            "/api/auth/users",
            json={"email": "dup@test.com", "role": "viewer"},
        )
        assert resp1.status_code == 201

        resp2 = await admin_client.post(
            "/api/auth/users",
            json={"email": "dup@test.com", "role": "viewer"},
        )
        assert resp2.status_code == 409

    async def test_delete_self_prevented(self, admin_client):
        # Get admin user ID
        users = (await admin_client.get("/api/auth/users")).json()
        admin_id = next(u["id"] for u in users if u["email"] == ADMIN_EMAIL)

        resp = await admin_client.delete(f"/api/auth/users/{admin_id}")
        assert resp.status_code == 400
        assert "own account" in resp.json()["detail"].lower()

    async def test_delete_last_admin_prevented(self, admin_client):
        # Create a second user (viewer), then try to delete the only admin
        await admin_client.post(
            "/api/auth/users",
            json={"email": "viewer@test.com", "role": "viewer"},
        )
        users = (await admin_client.get("/api/auth/users")).json()
        admin_id = next(u["id"] for u in users if u["email"] == ADMIN_EMAIL)

        # This should fail because it's the last admin AND it's self-deletion
        resp = await admin_client.delete(f"/api/auth/users/{admin_id}")
        assert resp.status_code == 400

    async def test_delete_nonexistent_user(self, admin_client):
        resp = await admin_client.delete("/api/auth/users/99999")
        assert resp.status_code == 404


# ─── Gateway role management ─────────────────────────────────────────────────


class TestGatewayRoles:
    async def test_set_gateway_role_invalid_gw_name(self, admin_client):
        users = (await admin_client.get("/api/auth/users")).json()
        uid = users[0]["id"]

        resp = await admin_client.put(
            f"/api/auth/users/{uid}/gateway-roles/--invalid!",
            json={"role": "viewer"},
        )
        assert resp.status_code == 400
        assert "gateway" in resp.json()["detail"].lower()

    async def test_set_gateway_role_invalid_role(self, admin_client):
        users = (await admin_client.get("/api/auth/users")).json()
        uid = users[0]["id"]

        resp = await admin_client.put(
            f"/api/auth/users/{uid}/gateway-roles/my-gw",
            json={"role": "superadmin"},
        )
        assert resp.status_code == 400
        assert "role" in resp.json()["detail"].lower()

    async def test_delete_gateway_role_invalid_gw_name(self, admin_client):
        users = (await admin_client.get("/api/auth/users")).json()
        uid = users[0]["id"]

        resp = await admin_client.delete(f"/api/auth/users/{uid}/gateway-roles/--invalid!")
        assert resp.status_code == 400

    async def test_delete_gateway_role_not_found(self, admin_client):
        users = (await admin_client.get("/api/auth/users")).json()
        uid = users[0]["id"]

        resp = await admin_client.delete(f"/api/auth/users/{uid}/gateway-roles/nonexistent-gw")
        assert resp.status_code == 404


# ─── Registration ─────────────────────────────────────────────────────────────


class TestRegistration:
    async def test_register_disabled(self, db, _with_admin):
        from shoreguard.api.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/auth/register",
                json={"email": "new@test.com", "password": "securepass"},
            )
            assert resp.status_code == 403
            assert "disabled" in resp.json()["detail"].lower()

    async def test_register_invalid_email(self, db, _with_admin, monkeypatch):
        monkeypatch.setenv("SHOREGUARD_ALLOW_REGISTRATION", "true")
        from shoreguard.api.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/auth/register",
                json={"email": "not-valid", "password": "securepass"},
            )
            assert resp.status_code == 400

    async def test_register_short_password(self, db, _with_admin, monkeypatch):
        monkeypatch.setenv("SHOREGUARD_ALLOW_REGISTRATION", "true")
        from shoreguard.api.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/auth/register",
                json={"email": "new@test.com", "password": "short"},
            )
            assert resp.status_code == 400

    async def test_register_duplicate_email(self, db, _with_admin, monkeypatch):
        monkeypatch.setenv("SHOREGUARD_ALLOW_REGISTRATION", "true")
        from shoreguard.api.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/auth/register",
                json={"email": ADMIN_EMAIL, "password": "securepass"},
            )
            assert resp.status_code == 409


# ─── Service principal management ─────────────────────────────────────────────


class TestServicePrincipalManagement:
    async def test_create_sp_invalid_role(self, admin_client):
        resp = await admin_client.post(
            "/api/auth/service-principals",
            json={"name": "test-sp", "role": "superadmin"},
        )
        assert resp.status_code == 400
        assert "role" in resp.json()["detail"].lower()

    async def test_create_sp_empty_name(self, admin_client):
        resp = await admin_client.post(
            "/api/auth/service-principals",
            json={"name": "", "role": "viewer"},
        )
        assert resp.status_code == 400

    async def test_create_sp_duplicate_name(self, admin_client):
        resp1 = await admin_client.post(
            "/api/auth/service-principals",
            json={"name": "dup-sp", "role": "viewer"},
        )
        assert resp1.status_code == 201

        resp2 = await admin_client.post(
            "/api/auth/service-principals",
            json={"name": "dup-sp", "role": "viewer"},
        )
        assert resp2.status_code == 409

    async def test_delete_sp_not_found(self, admin_client):
        resp = await admin_client.delete("/api/auth/service-principals/99999")
        assert resp.status_code == 404

    async def test_rotate_sp_not_found(self, admin_client):
        resp = await admin_client.post("/api/auth/service-principals/99999/rotate")
        assert resp.status_code == 404


# ─── Auth check endpoint ─────────────────────────────────────────────────────


class TestAuthCheck:
    async def test_auth_check_before_setup(self, fresh_client):
        resp = await fresh_client.get("/api/auth/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["needs_setup"] is True
        assert data["authenticated"] is False

    async def test_auth_check_authenticated(self, admin_client):
        resp = await admin_client.get("/api/auth/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["role"] == "admin"
