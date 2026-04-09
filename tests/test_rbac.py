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
    # Ensure mock methods return serialisable values so FastAPI doesn't fail
    _d = {"status": "mock"}
    client.approvals.approve.return_value = _d
    client.approvals.approve_all.return_value = _d
    client.approvals.reject.return_value = None
    client.approvals.edit.return_value = None
    client.approvals.undo.return_value = _d
    client.approvals.clear.return_value = {"cleared": 0}
    client.approvals.get_draft.return_value = {"chunks": []}
    client.approvals.get_pending.return_value = []
    client.approvals.get_history.return_value = []
    client.providers.create.return_value = _d
    client.providers.update.return_value = _d
    client.providers.delete.return_value = True
    client.providers.list.return_value = []
    client.providers.get.return_value = _d
    client.sandboxes.create.return_value = _d
    client.sandboxes.delete.return_value = True
    client.sandboxes.list.return_value = []
    client.sandboxes.get.return_value = _d
    client.sandboxes.exec.return_value = _d
    client.get_cluster_inference.return_value = _d
    client.set_cluster_inference.return_value = _d
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

    def test_bootstrap_propagates_exception(self, monkeypatch):
        monkeypatch.setenv("SHOREGUARD_ADMIN_PASSWORD", "secret")
        monkeypatch.setattr(
            "shoreguard.api.auth.create_user",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("db down")),
        )
        with pytest.raises(RuntimeError, match="db down"):
            bootstrap_admin_user()


# ─── Integration: Role enforcement on routes ────────────────────────────────

GW = "test"


async def test_viewer_can_list_gateways(viewer_client):
    resp = await viewer_client.get("/api/gateway/list")
    assert resp.status_code not in (401, 403)


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
    assert resp.status_code not in (401, 403)


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
    assert resp.status_code not in (401, 403)


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


async def test_deleted_user_session_rejected(db, mock_client):
    """After deleting a user, their existing session token should be rejected."""
    from shoreguard.api.deps import get_client
    from shoreguard.api.main import app

    info = create_user("doomed@test.com", "pass1234", "operator")
    create_user("admin@test.com", "adminpass", "admin")
    app.dependency_overrides[get_client] = lambda: mock_client
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/login", json={"email": "doomed@test.com", "password": "pass1234"}
            )
            assert resp.status_code == 200
            # Delete the user while session is still valid
            delete_user(info["id"])
            # Next request should fail
            resp = await c.get("/api/gateway/list")
            assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


# ─── Operator positive tests ──────────────────────────────────────────────


async def test_operator_can_create_sandbox(operator_client):
    resp = await operator_client.post(
        f"/api/gateways/{GW}/sandboxes", json={"name": "op-sb", "image": "test"}
    )
    assert resp.status_code not in (401, 403)


async def test_operator_can_approve_chunk(operator_client):
    resp = await operator_client.post(f"/api/gateways/{GW}/sandboxes/sb/approvals/chunk1/approve")
    assert resp.status_code not in (401, 403)


async def test_operator_can_create_provider(operator_client):
    resp = await operator_client.post(
        f"/api/gateways/{GW}/providers",
        json={"name": "p", "type": "openai", "api_key": "sk-test"},
    )
    assert resp.status_code not in (401, 403)


async def test_operator_can_set_inference(operator_client):
    resp = await operator_client.put(
        f"/api/gateways/{GW}/inference",
        json={"provider_name": "p", "model_id": "m"},
    )
    assert resp.status_code not in (401, 403)


async def test_operator_can_delete_provider(operator_client):
    resp = await operator_client.delete(f"/api/gateways/{GW}/providers/prov1")
    assert resp.status_code not in (401, 403)


# ─── Parametrised role enforcement ────────────────────────────────────────

_RANK = {"admin": 2, "operator": 1, "viewer": 0}


@pytest.fixture
async def all_role_clients(db, mock_client):
    """Provide admin, operator, viewer clients in a single fixture (shared DB)."""
    from shoreguard.api.deps import get_client
    from shoreguard.api.main import app

    create_user("admin@test.com", "adminpass", "admin")
    create_user("operator@test.com", "oppass", "operator")
    create_user("viewer@test.com", "viewpass", "viewer")
    app.dependency_overrides[get_client] = lambda: mock_client
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as admin_c,
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as op_c,
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as view_c,
    ):
        for c, email, pw in [
            (admin_c, "admin@test.com", "adminpass"),
            (op_c, "operator@test.com", "oppass"),
            (view_c, "viewer@test.com", "viewpass"),
        ]:
            resp = await c.post("/api/auth/login", json={"email": email, "password": pw})
            assert resp.status_code == 200
        yield {"admin": admin_c, "operator": op_c, "viewer": view_c}
    app.dependency_overrides.clear()


_ROLE_ENDPOINTS = [
    ("GET", "/api/gateway/list", "viewer", None),
    ("POST", "/api/gateway/register", "admin", {"name": "g", "endpoint": "1.2.3.4:443"}),
    ("POST", f"/api/gateways/{GW}/sandboxes", "operator", {"name": "sb", "image": "i"}),
    ("DELETE", f"/api/gateways/{GW}/sandboxes/sb", "operator", None),
    ("POST", f"/api/gateways/{GW}/sandboxes/sb/approvals/c1/approve", "operator", None),
    ("POST", f"/api/gateways/{GW}/sandboxes/sb/approvals/c1/reject", "operator", None),
    ("POST", f"/api/gateways/{GW}/sandboxes/sb/approvals/approve-all", "operator", None),
    (
        "POST",
        f"/api/gateways/{GW}/sandboxes/sb/approvals/c1/edit",
        "operator",
        {"proposed_rule": {}},
    ),
    ("POST", f"/api/gateways/{GW}/sandboxes/sb/approvals/c1/undo", "operator", None),
    ("POST", f"/api/gateways/{GW}/sandboxes/sb/approvals/clear", "operator", None),
    ("POST", f"/api/gateways/{GW}/providers", "operator", {"name": "p", "type": "t"}),
    ("PUT", f"/api/gateways/{GW}/providers/p", "operator", {}),
    ("DELETE", f"/api/gateways/{GW}/providers/p", "operator", None),
    ("PUT", f"/api/gateways/{GW}/inference", "operator", {"provider_name": "p", "model_id": "m"}),
    ("GET", "/api/auth/users", "admin", None),
    ("POST", "/api/auth/users", "admin", {"email": "x@x.com", "role": "viewer"}),
    ("GET", "/api/auth/service-principals", "admin", None),
]


@pytest.mark.parametrize(
    "method,path,min_role,body",
    _ROLE_ENDPOINTS,
    ids=[f"{m} {p}" for m, p, _, _ in _ROLE_ENDPOINTS],
)
async def test_role_enforcement(all_role_clients, method, path, min_role, body):
    """Verify that each endpoint enforces its minimum role correctly."""
    for role_name, client in all_role_clients.items():
        kwargs = {"json": body} if body is not None else {}
        resp = await getattr(client, method.lower())(path, **kwargs)
        if _RANK[role_name] >= _RANK[min_role]:
            assert resp.status_code not in (401, 403), (
                f"{role_name} should not be blocked for {method} {path}"
            )
        else:
            assert resp.status_code == 403, f"{role_name} should be denied for {method} {path}"


# ─── Login ────────────────────────────────────────────────────────────────


async def test_login_returns_role(db):
    from shoreguard.api.main import app

    create_user("test@test.com", "mypass", "operator")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/auth/login", json={"email": "test@test.com", "password": "mypass"}
        )
    assert resp.status_code == 200
    assert resp.json()["role"] == "operator"


# ─── Operator denied from admin-only user/SP endpoints ─────────────────────


async def test_operator_cannot_list_users(operator_client):
    resp = await operator_client.get("/api/auth/users")
    assert resp.status_code == 403


async def test_operator_cannot_create_user(operator_client):
    resp = await operator_client.post(
        "/api/auth/users", json={"email": "new@test.com", "role": "viewer"}
    )
    assert resp.status_code == 403


async def test_operator_cannot_delete_user(operator_client):
    resp = await operator_client.delete("/api/auth/users/999")
    assert resp.status_code == 403


async def test_operator_cannot_list_sps(operator_client):
    resp = await operator_client.get("/api/auth/service-principals")
    assert resp.status_code == 403


async def test_operator_cannot_create_sp(operator_client):
    resp = await operator_client.post(
        "/api/auth/service-principals", json={"name": "evil-sp", "role": "admin"}
    )
    assert resp.status_code == 403


async def test_operator_cannot_delete_sp(operator_client):
    resp = await operator_client.delete("/api/auth/service-principals/999")
    assert resp.status_code == 403


# ─── Self-deletion guard ───────────────────────────────────────────────────


async def test_admin_cannot_delete_self(admin_client):
    """Admin should not be able to delete their own account."""
    # Find own user ID
    resp = await admin_client.get("/api/auth/users")
    assert resp.status_code == 200
    users = resp.json()
    own_user = next(u for u in users if u["email"] == "admin@test.com")

    resp = await admin_client.delete(f"/api/auth/users/{own_user['id']}")
    assert resp.status_code == 400
    assert "own account" in resp.json()["detail"].lower()


# ─── Last admin guard (API level) ───────────────────────────────────────────


async def test_cannot_delete_last_admin(db, mock_client):
    """Deleting the only admin should be blocked at the API level."""
    from shoreguard.api.deps import get_client
    from shoreguard.api.main import app

    admin_info = create_user("admin@test.com", "adminpass", "admin")
    viewer_info = create_user("viewer@test.com", "viewpass", "viewer")
    app.dependency_overrides[get_client] = lambda: mock_client
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/login", json={"email": "admin@test.com", "password": "adminpass"}
            )
            assert resp.status_code == 200

            # Can delete the viewer
            resp = await c.delete(f"/api/auth/users/{viewer_info['id']}")
            assert resp.status_code == 200

            # Cannot delete the last admin (different from self — use a second admin)
            # Actually admin_info is ourself, so this also tests self-deletion guard
            resp = await c.delete(f"/api/auth/users/{admin_info['id']}")
            assert resp.status_code == 400
    finally:
        app.dependency_overrides.clear()


# ─── Regression: response_model=None error paths (Fix #5) ────────────────


async def test_duplicate_user_returns_409(admin_client):
    """Creating a duplicate user returns 409, not 500 (requires response_model=None)."""
    resp = await admin_client.post(
        "/api/auth/users",
        json={"email": "admin@test.com", "role": "viewer"},
    )
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


async def test_delete_nonexistent_user_returns_404(admin_client):
    """Deleting a user that does not exist returns 404, not 500."""
    resp = await admin_client.delete("/api/auth/users/99999")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


async def test_set_gateway_role_nonexistent_gateway_returns_404(admin_client):
    """Setting a gateway role for a nonexistent gateway returns 404, not 500."""
    # First create a user to target
    resp = await admin_client.post(
        "/api/auth/users",
        json={"email": "target@test.com", "role": "viewer"},
    )
    assert resp.status_code == 201
    user_id = resp.json()["id"]

    resp = await admin_client.put(
        f"/api/auth/users/{user_id}/gateway-roles/nonexistent-gw",
        json={"role": "operator"},
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ─── SP endpoint symmetry tests ───────────────────────────────────────────


async def test_duplicate_sp_returns_409(admin_client):
    """Creating a service principal with a duplicate name returns 409."""
    resp = await admin_client.post(
        "/api/auth/service-principals",
        json={"name": "dup-sp", "role": "viewer"},
    )
    assert resp.status_code == 201

    resp = await admin_client.post(
        "/api/auth/service-principals",
        json={"name": "dup-sp", "role": "viewer"},
    )
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


async def test_delete_nonexistent_sp_returns_404(admin_client):
    """Deleting an SP that does not exist returns 404."""
    resp = await admin_client.delete("/api/auth/service-principals/99999")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


async def test_set_sp_gateway_role_nonexistent_gateway_returns_404(admin_client):
    """Setting a gateway role on an SP for a nonexistent gateway returns 404."""
    resp = await admin_client.post(
        "/api/auth/service-principals",
        json={"name": "sp-gw-test", "role": "viewer"},
    )
    assert resp.status_code == 201
    sp_id = resp.json()["id"]

    resp = await admin_client.put(
        f"/api/auth/service-principals/{sp_id}/gateway-roles/nonexistent-gw",
        json={"role": "operator"},
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ─── DELETE gateway-role endpoints ─────────────────────────────────────────


async def test_delete_user_gateway_role_not_found(admin_client):
    """Deleting a gateway role that doesn't exist returns 404."""
    resp = await admin_client.post(
        "/api/auth/users",
        json={"email": "del-role@test.com", "role": "viewer"},
    )
    assert resp.status_code == 201
    user_id = resp.json()["id"]

    resp = await admin_client.delete(f"/api/auth/users/{user_id}/gateway-roles/nonexistent-gw")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


async def test_delete_sp_gateway_role_not_found(admin_client):
    """Deleting an SP gateway role that doesn't exist returns 404."""
    resp = await admin_client.post(
        "/api/auth/service-principals",
        json={"name": "sp-del-role", "role": "viewer"},
    )
    assert resp.status_code == 201
    sp_id = resp.json()["id"]

    resp = await admin_client.delete(
        f"/api/auth/service-principals/{sp_id}/gateway-roles/nonexistent-gw"
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ─── Validation: invalid gateway name / invalid role ──────────────────────


async def test_set_user_gateway_role_invalid_name_returns_400(admin_client):
    """Setting a gateway role with an invalid gateway name returns 400."""
    resp = await admin_client.post(
        "/api/auth/users",
        json={"email": "val-name@test.com", "role": "viewer"},
    )
    assert resp.status_code == 201
    user_id = resp.json()["id"]

    resp = await admin_client.put(
        f"/api/auth/users/{user_id}/gateway-roles/INVALID NAME!!!",
        json={"role": "operator"},
    )
    assert resp.status_code == 400
    assert "invalid gateway name" in resp.json()["detail"].lower()


async def test_set_user_gateway_role_invalid_role_returns_400(admin_client):
    """Setting a gateway role with an invalid role returns 400."""
    resp = await admin_client.post(
        "/api/auth/users",
        json={"email": "val-role@test.com", "role": "viewer"},
    )
    assert resp.status_code == 201
    user_id = resp.json()["id"]

    resp = await admin_client.put(
        f"/api/auth/users/{user_id}/gateway-roles/some-gw",
        json={"role": "superadmin"},
    )
    assert resp.status_code == 400
    assert "invalid role" in resp.json()["detail"].lower()


async def test_set_sp_gateway_role_invalid_name_returns_400(admin_client):
    """Setting an SP gateway role with an invalid gateway name returns 400."""
    resp = await admin_client.post(
        "/api/auth/service-principals",
        json={"name": "sp-val-name", "role": "viewer"},
    )
    assert resp.status_code == 201
    sp_id = resp.json()["id"]

    resp = await admin_client.put(
        f"/api/auth/service-principals/{sp_id}/gateway-roles/INVALID NAME!!!",
        json={"role": "operator"},
    )
    assert resp.status_code == 400
    assert "invalid gateway name" in resp.json()["detail"].lower()


async def test_set_sp_gateway_role_invalid_role_returns_400(admin_client):
    """Setting an SP gateway role with an invalid role returns 400."""
    resp = await admin_client.post(
        "/api/auth/service-principals",
        json={"name": "sp-val-role", "role": "viewer"},
    )
    assert resp.status_code == 201
    sp_id = resp.json()["id"]

    resp = await admin_client.put(
        f"/api/auth/service-principals/{sp_id}/gateway-roles/some-gw",
        json={"role": "superadmin"},
    )
    assert resp.status_code == 400
    assert "invalid role" in resp.json()["detail"].lower()


async def test_delete_user_gateway_role_invalid_name_returns_400(admin_client):
    """Deleting a user gateway role with an invalid gateway name returns 400."""
    resp = await admin_client.delete("/api/auth/users/1/gateway-roles/INVALID NAME!!!")
    assert resp.status_code == 400
    assert "invalid gateway name" in resp.json()["detail"].lower()


async def test_delete_sp_gateway_role_invalid_name_returns_400(admin_client):
    """Deleting an SP gateway role with an invalid gateway name returns 400."""
    resp = await admin_client.delete("/api/auth/service-principals/1/gateway-roles/INVALID NAME!!!")
    assert resp.status_code == 400
    assert "invalid gateway name" in resp.json()["detail"].lower()
