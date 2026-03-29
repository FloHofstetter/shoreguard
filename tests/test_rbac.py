"""Tests for RBAC — role-based access control with users and service principals."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.api import auth
from shoreguard.api.auth import (
    _ROLE_RANK,
    ROLES,
    bootstrap_admin_user,
    create_service_principal,
    create_user,
    delete_service_principal,
    delete_user,
    list_service_principals,
    list_users,
)
from shoreguard.models import Base

# ─── Fixtures ───────────────────────────────────────────────────────────────


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
def mock_client():
    from shoreguard.client import ShoreGuardClient

    client = MagicMock(spec=ShoreGuardClient)
    client.sandboxes = MagicMock()
    client.policies = MagicMock()
    client.providers = MagicMock()
    client.approvals = MagicMock()
    return client


def _login_cookie(client_resp) -> dict:
    """Extract session cookie from login response."""
    return {"sg_session": client_resp.cookies.get("sg_session")}


@pytest.fixture
async def admin_client(db, mock_client):
    from shoreguard.api.deps import get_client
    from shoreguard.api.main import app

    create_user("admin@test.com", "adminpass", "admin")
    app.dependency_overrides[get_client] = lambda: mock_client
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/auth/login", json={"email": "admin@test.com", "password": "adminpass"}
        )
        assert resp.status_code == 200
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def operator_client(db, mock_client):
    from shoreguard.api.deps import get_client
    from shoreguard.api.main import app

    create_user("admin@test.com", "adminpass", "admin")
    create_user("operator@test.com", "oppass", "operator")
    app.dependency_overrides[get_client] = lambda: mock_client
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/auth/login", json={"email": "operator@test.com", "password": "oppass"}
        )
        assert resp.status_code == 200
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def viewer_client(db, mock_client):
    from shoreguard.api.deps import get_client
    from shoreguard.api.main import app

    create_user("admin@test.com", "adminpass", "admin")
    create_user("viewer@test.com", "viewpass", "viewer")
    app.dependency_overrides[get_client] = lambda: mock_client
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/auth/login", json={"email": "viewer@test.com", "password": "viewpass"}
        )
        assert resp.status_code == 200
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def sp_client(db, mock_client):
    """Client using a service principal Bearer token."""
    from shoreguard.api.deps import get_client
    from shoreguard.api.main import app

    create_user("admin@test.com", "adminpass", "admin")
    key, _ = create_service_principal("test-sp", "viewer")
    app.dependency_overrides[get_client] = lambda: mock_client
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    ) as c:
        yield c
    app.dependency_overrides.clear()


# ─── Unit: Role hierarchy ───────────────────────────────────────────────────


class TestRoleHierarchy:
    def test_roles_tuple(self):
        assert ROLES == ("admin", "operator", "viewer")

    def test_admin_outranks_all(self):
        assert _ROLE_RANK["admin"] > _ROLE_RANK["operator"]
        assert _ROLE_RANK["admin"] > _ROLE_RANK["viewer"]

    def test_operator_outranks_viewer(self):
        assert _ROLE_RANK["operator"] > _ROLE_RANK["viewer"]


# ─── Unit: CRUD ─────────────────────────────────────────────────────────────


class TestCRUD:
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        pass

    def test_user_crud(self):
        info = create_user("u@test.com", "pass", "operator")
        assert info["email"] == "u@test.com"
        users = list_users()
        assert len(users) == 1
        assert delete_user(info["id"])
        assert list_users() == []

    def test_sp_crud(self):
        key, info = create_service_principal("sp1", "viewer")
        assert info["name"] == "sp1"
        sps = list_service_principals()
        assert len(sps) == 1
        assert delete_service_principal(info["id"])
        assert list_service_principals() == []

    def test_duplicate_user_raises(self):
        create_user("dup@test.com", "pass", "viewer")
        with pytest.raises(Exception):
            create_user("dup@test.com", "pass", "admin")

    def test_duplicate_sp_raises(self):
        create_service_principal("dup", "viewer")
        with pytest.raises(Exception):
            create_service_principal("dup", "admin")


# ─── Unit: Bootstrap ───────────────────────────────────────────────────────


class TestBootstrap:
    @pytest.fixture(autouse=True)
    def _setup(self, db, monkeypatch):
        self._db = db
        self._monkeypatch = monkeypatch

    def test_bootstrap_creates_admin(self):
        self._monkeypatch.setenv("SHOREGUARD_ADMIN_PASSWORD", "secret")
        bootstrap_admin_user()
        users = list_users()
        assert len(users) == 1
        assert users[0]["email"] == "admin@localhost"
        assert users[0]["role"] == "admin"

    def test_bootstrap_noop_with_existing_users(self):
        create_user("existing@test.com", "pass", "viewer")
        self._monkeypatch.setenv("SHOREGUARD_ADMIN_PASSWORD", "secret")
        bootstrap_admin_user()
        users = list_users()
        assert len(users) == 1
        assert users[0]["email"] == "existing@test.com"

    def test_bootstrap_noop_without_env(self):
        self._monkeypatch.delenv("SHOREGUARD_ADMIN_PASSWORD", raising=False)
        bootstrap_admin_user()
        assert list_users() == []


# ─── Integration: Role enforcement on routes ────────────────────────────────

GW = "test"


async def test_viewer_can_list_gateways(viewer_client):
    resp = await viewer_client.get("/api/gateway/list")
    assert resp.status_code != 403


async def test_viewer_cannot_register_gateway(viewer_client):
    resp = await viewer_client.post(
        "/api/gateway/register", json={"name": "evil", "endpoint": "1.2.3.4:443"}
    )
    assert resp.status_code == 403


async def test_operator_cannot_register_gateway(operator_client):
    resp = await operator_client.post(
        "/api/gateway/register", json={"name": "evil", "endpoint": "1.2.3.4:443"}
    )
    assert resp.status_code == 403


async def test_admin_can_register_gateway(admin_client):
    resp = await admin_client.post(
        "/api/gateway/register", json={"name": "test-gw", "endpoint": "1.2.3.4:443"}
    )
    assert resp.status_code != 403


async def test_viewer_cannot_create_sandbox(viewer_client):
    resp = await viewer_client.post(
        f"/api/gateways/{GW}/sandboxes", json={"name": "evil-sb", "image": "test"}
    )
    assert resp.status_code == 403


async def test_viewer_cannot_approve_chunk(viewer_client):
    resp = await viewer_client.post(f"/api/gateways/{GW}/sandboxes/sb/approvals/chunk1/approve")
    assert resp.status_code == 403


async def test_viewer_cannot_delete_provider(viewer_client):
    resp = await viewer_client.delete(f"/api/gateways/{GW}/providers/prov1")
    assert resp.status_code == 403


async def test_viewer_cannot_set_inference(viewer_client):
    resp = await viewer_client.put(
        f"/api/gateways/{GW}/inference",
        json={"provider_name": "p", "model_id": "m"},
    )
    assert resp.status_code == 403


async def test_sp_bearer_can_read(sp_client):
    resp = await sp_client.get("/api/gateway/list")
    assert resp.status_code != 403


async def test_sp_viewer_cannot_register(sp_client):
    resp = await sp_client.post(
        "/api/gateway/register", json={"name": "evil", "endpoint": "1.2.3.4:443"}
    )
    assert resp.status_code == 403


# ─── Integration: User management endpoints ─────────────────────────────────


async def test_admin_can_create_user(admin_client):
    resp = await admin_client.post(
        "/api/auth/users",
        json={"email": "new@test.com", "password": "newpass123", "role": "operator"},
    )
    assert resp.status_code == 201
    assert resp.json()["email"] == "new@test.com"


async def test_admin_can_list_users(admin_client):
    resp = await admin_client.get("/api/auth/users")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_viewer_cannot_manage_users(viewer_client):
    resp = await viewer_client.get("/api/auth/users")
    assert resp.status_code == 403


# ─── Integration: Service principal management ──────────────────────────────


async def test_admin_can_create_sp(admin_client):
    resp = await admin_client.post(
        "/api/auth/service-principals",
        json={"name": "new-sp", "role": "operator"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "key" in data
    assert data["name"] == "new-sp"


async def test_admin_can_list_sps(admin_client):
    resp = await admin_client.get("/api/auth/service-principals")
    assert resp.status_code == 200


async def test_viewer_cannot_manage_sps(viewer_client):
    resp = await viewer_client.get("/api/auth/service-principals")
    assert resp.status_code == 403


# ─── Integration: Setup wizard ──────────────────────────────────────────────


async def test_setup_creates_admin(db):
    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/auth/setup",
            json={"email": "first@admin.com", "password": "secret123"},
        )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"
    assert "sg_session" in resp.cookies


async def test_setup_rejects_when_users_exist(db):
    from shoreguard.api.main import app

    create_user("existing@test.com", "pass", "admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/auth/setup",
            json={"email": "new@admin.com", "password": "pass"},
        )
    assert resp.status_code == 400
    assert "already complete" in resp.json()["detail"]


async def test_login_returns_role(db):
    from shoreguard.api.main import app

    create_user("test@test.com", "mypass", "operator")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/auth/login", json={"email": "test@test.com", "password": "mypass"}
        )
    assert resp.status_code == 200
    assert resp.json()["role"] == "operator"
