"""Integration tests for authentication routes — user-based auth."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.api import auth
from shoreguard.api.auth import create_service_principal, create_user
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
    """Create an admin user for tests that need auth."""
    create_user(ADMIN_EMAIL, ADMIN_PASS, "admin")


@pytest.fixture
async def client(db, _with_admin):
    from shoreguard.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture
async def authed_client(client):
    """Client with an active session cookie."""
    resp = await client.post(
        "/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
    )
    assert resp.status_code == 200
    yield client


# ─── Login endpoint ─────────────────────────────────────────────────────────


async def test_login_success(client):
    resp = await client.post(
        "/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["role"] == "admin"
    assert data["email"] == ADMIN_EMAIL
    assert "sg_session" in resp.cookies


async def test_login_wrong_password(client):
    resp = await client.post(
        "/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": "wrong"},
    )
    assert resp.status_code == 401
    assert "sg_session" not in resp.cookies


async def test_login_nonexistent_user(client):
    resp = await client.post(
        "/api/auth/login",
        json={"email": "nobody@test.com", "password": "pass"},
    )
    assert resp.status_code == 401


# ─── Logout ─────────────────────────────────────────────────────────────────


async def test_logout(authed_client):
    resp = await authed_client.post("/api/auth/logout")
    assert resp.status_code == 200


# ─── Auth check ─────────────────────────────────────────────────────────────


async def test_auth_check_authenticated(authed_client):
    resp = await authed_client.get("/api/auth/check")
    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is True
    assert data["role"] == "admin"
    assert data["needs_setup"] is False


async def test_auth_check_unauthenticated(client):
    resp = await client.get("/api/auth/check")
    data = resp.json()
    assert data["authenticated"] is False


async def test_auth_check_needs_setup(db):
    """Fresh DB with no users -> needs_setup is True."""
    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/auth/check")
    data = resp.json()
    assert data["needs_setup"] is True
    assert data["authenticated"] is False


# ─── Bearer token (service principal) ───────────────────────────────────────


async def test_bearer_auth_with_sp(db, _with_admin):
    from shoreguard.api.main import app

    key, _ = create_service_principal("test-sp", "viewer")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    ) as c:
        resp = await c.get("/api/auth/check")
    assert resp.status_code == 200
    assert resp.json()["role"] == "viewer"


async def test_bearer_invalid_key(db, _with_admin):
    from shoreguard.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer invalid-key"},
    ) as c:
        resp = await c.get("/api/auth/check")
    assert resp.json()["authenticated"] is False
