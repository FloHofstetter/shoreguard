"""Tests for RBAC — role-based access control with multi-key support."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.api import auth
from shoreguard.api.auth import (
    ROLES,
    _hash_key,
    _lookup_db_key,
    bootstrap_admin_key,
    check_api_key,
    configure,
    create_api_key,
    create_session_token,
    delete_api_key,
    list_api_keys,
    reset,
    verify_session_token,
)
from shoreguard.models import Base

TEST_KEY = "test-admin-key-123"


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def db_session_factory():
    """In-memory SQLite with api_keys table."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


@pytest.fixture(autouse=True)
def _auth_setup(db_session_factory):
    """Configure auth with a test key and DB."""
    configure(TEST_KEY, session_factory=db_session_factory)
    yield
    reset()


# ─── Unit: Role hierarchy ───────────────────────────────────────────────────


class TestRoleHierarchy:
    def test_roles_tuple(self):
        assert ROLES == ("admin", "operator", "viewer")

    def test_admin_outranks_all(self):
        from shoreguard.api.auth import _ROLE_RANK

        assert _ROLE_RANK["admin"] > _ROLE_RANK["operator"]
        assert _ROLE_RANK["admin"] > _ROLE_RANK["viewer"]

    def test_operator_outranks_viewer(self):
        from shoreguard.api.auth import _ROLE_RANK

        assert _ROLE_RANK["operator"] > _ROLE_RANK["viewer"]


# ─── Unit: Key hashing ─────────────────────────────────────────────────────


class TestHashKey:
    def test_deterministic(self):
        assert _hash_key("test") == _hash_key("test")

    def test_different_inputs(self):
        assert _hash_key("a") != _hash_key("b")

    def test_hex_length(self):
        assert len(_hash_key("any")) == 64


# ─── Unit: Session token with roles ────────────────────────────────────────


class TestSessionTokenRoles:
    def test_default_role_is_admin(self):
        token = create_session_token()
        assert verify_session_token(token) == "admin"

    def test_each_role_roundtrips(self):
        for role in ROLES:
            token = create_session_token(role=role)
            assert verify_session_token(token) == role

    def test_tampered_role_rejected(self):
        token = create_session_token(role="viewer")
        parts = token.split(".")
        parts[2] = "admin"  # tamper the role
        tampered = ".".join(parts)
        assert verify_session_token(tampered) is None

    def test_old_3part_token_rejected(self):
        """Old-format tokens (pre-RBAC) must not validate."""
        assert verify_session_token("nonce.12345678.abcdef1234") is None


# ─── Unit: DB key lookup ───────────────────────────────────────────────────


class TestDBKeyLookup:
    def test_legacy_key_returns_admin(self):
        assert check_api_key(TEST_KEY) == "admin"

    def test_db_key_returns_role(self, db_session_factory):
        key, info = create_api_key("test-op", "operator")
        assert check_api_key(key) == "operator"

    def test_unknown_key_returns_none(self):
        assert check_api_key("nonexistent-key") is None

    def test_lookup_without_db(self):
        reset()
        configure(TEST_KEY)  # no session_factory
        assert _lookup_db_key("anything") is None


# ─── Unit: Bootstrap ───────────────────────────────────────────────────────


class TestBootstrap:
    def test_creates_admin_key(self, db_session_factory):
        bootstrap_admin_key(TEST_KEY)
        keys = list_api_keys()
        assert len(keys) == 1
        assert keys[0]["name"] == "bootstrap"
        assert keys[0]["role"] == "admin"

    def test_noop_when_keys_exist(self, db_session_factory):
        create_api_key("existing", "viewer")
        bootstrap_admin_key(TEST_KEY)
        keys = list_api_keys()
        assert len(keys) == 1
        assert keys[0]["name"] == "existing"


# ─── Unit: Key CRUD ────────────────────────────────────────────────────────


class TestKeyCRUD:
    def test_create_and_list(self):
        key, info = create_api_key("my-key", "operator")
        assert len(key) > 20  # urlsafe token
        assert info["name"] == "my-key"
        assert info["role"] == "operator"

        keys = list_api_keys()
        assert len(keys) == 1
        assert keys[0]["name"] == "my-key"

    def test_delete(self):
        create_api_key("to-delete", "viewer")
        assert delete_api_key("to-delete") is True
        assert delete_api_key("to-delete") is False
        assert list_api_keys() == []

    def test_invalid_role_raises(self):
        with pytest.raises(ValueError, match="Invalid role"):
            create_api_key("bad", "superadmin")

    def test_duplicate_name_raises(self):
        create_api_key("dup", "viewer")
        with pytest.raises(Exception):
            create_api_key("dup", "admin")


# ─── Integration: require_role dependency ───────────────────────────────────


@pytest.fixture
def mock_client():
    from shoreguard.client import ShoreGuardClient

    client = MagicMock(spec=ShoreGuardClient)
    client.sandboxes = MagicMock()
    client.policies = MagicMock()
    client.providers = MagicMock()
    client.approvals = MagicMock()
    return client


@pytest.fixture
async def admin_client(mock_client):
    from shoreguard.api.deps import get_client
    from shoreguard.api.main import app

    app.dependency_overrides[get_client] = lambda: mock_client
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TEST_KEY}"},
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def operator_client(mock_client, db_session_factory):
    from shoreguard.api.deps import get_client
    from shoreguard.api.main import app

    key, _ = create_api_key("test-operator", "operator")
    app.dependency_overrides[get_client] = lambda: mock_client
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    ) as c:
        # Re-attach our DB after lifespan may have overwritten it
        auth._session_factory = db_session_factory
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def viewer_client(mock_client, db_session_factory):
    from shoreguard.api.deps import get_client
    from shoreguard.api.main import app

    key, _ = create_api_key("test-viewer", "viewer")
    app.dependency_overrides[get_client] = lambda: mock_client
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {key}"},
    ) as c:
        # Re-attach our DB after lifespan may have overwritten it
        auth._session_factory = db_session_factory
        yield c
    app.dependency_overrides.clear()


# ─── Integration: Role enforcement on routes ────────────────────────────────

GW = "test"


async def test_viewer_can_list_gateways(viewer_client):
    resp = await viewer_client.get("/api/gateway/list")
    # May fail with 503 (no real gateway) but NOT 403
    assert resp.status_code != 403


async def test_viewer_cannot_register_gateway(viewer_client):
    resp = await viewer_client.post(
        "/api/gateway/register",
        json={"name": "evil", "endpoint": "1.2.3.4:443"},
    )
    assert resp.status_code == 403


async def test_operator_cannot_register_gateway(operator_client):
    resp = await operator_client.post(
        "/api/gateway/register",
        json={"name": "evil", "endpoint": "1.2.3.4:443"},
    )
    assert resp.status_code == 403


async def test_admin_can_register_gateway(admin_client):
    resp = await admin_client.post(
        "/api/gateway/register",
        json={"name": "test-gw", "endpoint": "1.2.3.4:443"},
    )
    # Should not be 403 — may be 409 or 201 depending on state
    assert resp.status_code != 403


async def test_viewer_cannot_create_sandbox(viewer_client):
    resp = await viewer_client.post(
        f"/api/gateways/{GW}/sandboxes",
        json={"name": "evil-sb", "image": "test"},
    )
    assert resp.status_code == 403


async def test_operator_can_list_sandboxes(operator_client):
    from shoreguard.api.deps import _current_gateway

    _current_gateway.set(GW)
    resp = await operator_client.get(f"/api/gateways/{GW}/sandboxes")
    assert resp.status_code != 403


async def test_viewer_cannot_approve_chunk(viewer_client):
    resp = await viewer_client.post(
        f"/api/gateways/{GW}/sandboxes/sb/approvals/chunk1/approve",
    )
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


# ─── Integration: Key management endpoints ──────────────────────────────────


async def test_admin_can_create_key(admin_client):
    resp = await admin_client.post(
        "/api/auth/keys",
        json={"name": "new-key", "role": "operator"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "key" in data
    assert data["role"] == "operator"
    assert data["name"] == "new-key"


async def test_admin_can_list_keys(admin_client):
    resp = await admin_client.get("/api/auth/keys")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_admin_can_delete_key(admin_client):
    # Create then delete
    await admin_client.post("/api/auth/keys", json={"name": "temp", "role": "viewer"})
    resp = await admin_client.delete("/api/auth/keys/temp")
    assert resp.status_code == 200


async def test_viewer_cannot_manage_keys(viewer_client):
    resp = await viewer_client.get("/api/auth/keys")
    assert resp.status_code == 403

    resp = await viewer_client.post(
        "/api/auth/keys",
        json={"name": "hack", "role": "admin"},
    )
    assert resp.status_code == 403


async def test_operator_cannot_manage_keys(operator_client):
    resp = await operator_client.get("/api/auth/keys")
    assert resp.status_code == 403


# ─── Integration: Login returns role ────────────────────────────────────────


async def test_login_returns_role():
    from shoreguard.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post("/api/auth/login", json={"key": TEST_KEY})
        assert resp.status_code == 200
        assert resp.json()["role"] == "admin"


async def test_auth_check_returns_role():
    from shoreguard.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TEST_KEY}"},
    ) as client:
        resp = await client.get("/api/auth/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["role"] == "admin"


async def test_created_key_works_as_bearer(admin_client):
    """Create a key via API, then use it as Bearer token."""
    resp = await admin_client.post(
        "/api/auth/keys",
        json={"name": "bearer-test", "role": "viewer"},
    )
    new_key = resp.json()["key"]

    from shoreguard.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {new_key}"},
    ) as client:
        resp = await client.get("/api/auth/check")
        assert resp.status_code == 200
        assert resp.json()["role"] == "viewer"
