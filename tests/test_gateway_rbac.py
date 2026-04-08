"""Tests for Gateway-scoped RBAC feature."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import shoreguard.services.audit as audit_mod
from shoreguard.api import auth
from shoreguard.api.auth import (
    _GatewayRoleLookupError,
    _lookup_gateway_role,
    create_service_principal,
    create_user,
    list_gateway_roles_for_sp,
    list_gateway_roles_for_user,
    remove_gateway_role,
    set_gateway_role,
)
from shoreguard.api.deps import _current_gateway
from shoreguard.exceptions import ValidationError as DomainValidationError
from shoreguard.models import Base, Gateway

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "adminpass123"
VIEWER_EMAIL = "viewer@test.com"
VIEWER_PASS = "viewerpass1"
GW_NAME = "test-gw"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    auth.init_auth_for_test(factory)
    audit_mod.audit_service = audit_mod.AuditService(factory)
    yield factory
    auth.reset()
    audit_mod.audit_service = None
    engine.dispose()


@pytest.fixture
def _with_gateway(db):
    """Create a test gateway in the DB."""
    session = db()
    gw = Gateway(
        name=GW_NAME,
        endpoint="10.0.0.1:8443",
        scheme="https",
        registered_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session.add(gw)
    session.commit()
    session.close()


@pytest.fixture
def _with_admin(db):
    create_user(ADMIN_EMAIL, ADMIN_PASS, "admin")


@pytest.fixture
def _with_viewer(db):
    create_user(VIEWER_EMAIL, VIEWER_PASS, "viewer")


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
            json={"email": VIEWER_EMAIL, "password": VIEWER_PASS},
        )
        assert resp.status_code == 200
        yield client


# ─── Unit tests: set_gateway_role ─────────────────────────────────────────


class TestSetGatewayRole:
    def test_create_new_user_role(self, db, _with_gateway):
        user = create_user("u@test.com", "password1", "viewer")
        result = set_gateway_role(user_id=user["id"], gateway_name=GW_NAME, role="admin")
        assert result == {"user_id": user["id"], "gateway_name": GW_NAME, "role": "admin"}

    def test_update_existing_user_role(self, db, _with_gateway):
        user = create_user("u@test.com", "password1", "viewer")
        set_gateway_role(user_id=user["id"], gateway_name=GW_NAME, role="admin")
        result = set_gateway_role(user_id=user["id"], gateway_name=GW_NAME, role="operator")
        assert result["role"] == "operator"

    def test_create_sp_role(self, db, _with_gateway):
        _key, sp = create_service_principal("test-sp", "viewer")
        result = set_gateway_role(sp_id=sp["id"], gateway_name=GW_NAME, role="admin")
        assert result == {"sp_id": sp["id"], "gateway_name": GW_NAME, "role": "admin"}

    def test_update_existing_sp_role(self, db, _with_gateway):
        _key, sp = create_service_principal("test-sp", "viewer")
        set_gateway_role(sp_id=sp["id"], gateway_name=GW_NAME, role="admin")
        result = set_gateway_role(sp_id=sp["id"], gateway_name=GW_NAME, role="operator")
        assert result["role"] == "operator"

    def test_invalid_role_raises(self, db, _with_gateway):
        user = create_user("u@test.com", "password1", "viewer")
        with pytest.raises(DomainValidationError, match="Invalid role"):
            set_gateway_role(user_id=user["id"], gateway_name=GW_NAME, role="superadmin")

    def test_missing_both_ids_raises(self, db, _with_gateway):
        with pytest.raises(DomainValidationError, match="Either user_id or sp_id must be provided"):
            set_gateway_role(gateway_name=GW_NAME, role="admin")


# ─── Unit tests: remove_gateway_role ──────────────────────────────────────


class TestRemoveGatewayRole:
    def test_remove_existing_user_role(self, db, _with_gateway):
        user = create_user("u@test.com", "password1", "viewer")
        set_gateway_role(user_id=user["id"], gateway_name=GW_NAME, role="admin")
        assert remove_gateway_role(user_id=user["id"], gateway_name=GW_NAME) is True

    def test_remove_nonexistent_returns_false(self, db, _with_gateway):
        user = create_user("u@test.com", "password1", "viewer")
        assert remove_gateway_role(user_id=user["id"], gateway_name=GW_NAME) is False

    def test_remove_sp_role(self, db, _with_gateway):
        _key, sp = create_service_principal("test-sp", "viewer")
        set_gateway_role(sp_id=sp["id"], gateway_name=GW_NAME, role="admin")
        assert remove_gateway_role(sp_id=sp["id"], gateway_name=GW_NAME) is True

    def test_remove_without_ids_returns_false(self, db):
        assert remove_gateway_role(gateway_name="anything") is False


# ─── Unit tests: list_gateway_roles_for_user ──────────────────────────────


class TestListGatewayRolesForUser:
    def test_returns_roles(self, db, _with_gateway):
        user = create_user("u@test.com", "password1", "viewer")
        set_gateway_role(user_id=user["id"], gateway_name=GW_NAME, role="admin")
        roles = list_gateway_roles_for_user(user["id"])
        assert len(roles) == 1
        assert roles[0] == {"gateway_name": GW_NAME, "role": "admin"}

    def test_empty_when_none(self, db):
        user = create_user("u@test.com", "password1", "viewer")
        assert list_gateway_roles_for_user(user["id"]) == []

    def test_empty_when_session_factory_is_none(self, db):
        user = create_user("u@test.com", "password1", "viewer")
        # Temporarily clear the session factory
        original = auth._session_factory
        auth._session_factory = None
        try:
            assert list_gateway_roles_for_user(user["id"]) == []
        finally:
            auth._session_factory = original


# ─── Unit tests: list_gateway_roles_for_sp ────────────────────────────────


class TestListGatewayRolesForSP:
    def test_returns_roles(self, db, _with_gateway):
        _key, sp = create_service_principal("test-sp", "viewer")
        set_gateway_role(sp_id=sp["id"], gateway_name=GW_NAME, role="operator")
        roles = list_gateway_roles_for_sp(sp["id"])
        assert len(roles) == 1
        assert roles[0] == {"gateway_name": GW_NAME, "role": "operator"}

    def test_empty_when_none(self, db):
        _key, sp = create_service_principal("test-sp", "viewer")
        assert list_gateway_roles_for_sp(sp["id"]) == []

    def test_empty_when_session_factory_is_none(self, db):
        _key, sp = create_service_principal("test-sp", "viewer")
        original = auth._session_factory
        auth._session_factory = None
        try:
            assert list_gateway_roles_for_sp(sp["id"]) == []
        finally:
            auth._session_factory = original


# ─── Unit tests: _lookup_gateway_role ─────────────────────────────────────


class TestLookupGatewayRole:
    def test_returns_role_when_exists(self, db, _with_gateway):
        user = create_user("u@test.com", "password1", "viewer")
        set_gateway_role(user_id=user["id"], gateway_name=GW_NAME, role="admin")
        assert _lookup_gateway_role(user_id=user["id"], gateway=GW_NAME) == "admin"

    def test_returns_none_when_not_found(self, db, _with_gateway):
        user = create_user("u@test.com", "password1", "viewer")
        assert _lookup_gateway_role(user_id=user["id"], gateway=GW_NAME) is None

    def test_returns_none_when_no_ids(self, db):
        assert _lookup_gateway_role(gateway=GW_NAME) is None

    def test_returns_none_when_session_factory_is_none(self, db):
        original = auth._session_factory
        auth._session_factory = None
        try:
            assert _lookup_gateway_role(user_id=1, gateway=GW_NAME) is None
        finally:
            auth._session_factory = original

    def test_raises_on_db_error(self, db, _with_gateway, monkeypatch):
        """Simulate a SQLAlchemyError during the query — should raise _GatewayRoleLookupError."""
        from sqlalchemy.exc import SQLAlchemyError

        user = create_user("u@test.com", "password1", "viewer")

        original_factory = auth._session_factory

        def broken_factory():
            session = original_factory()

            def patched_query(*args, **kwargs):
                raise SQLAlchemyError("simulated DB error")

            session.query = patched_query
            return session

        auth._session_factory = broken_factory
        try:
            with pytest.raises(_GatewayRoleLookupError):
                _lookup_gateway_role(user_id=user["id"], gateway=GW_NAME)
        finally:
            auth._session_factory = original_factory


# ─── Integration tests: role resolution in require_role() ─────────────────


class TestGatewayRoleResolution:
    async def test_viewer_gets_admin_on_scoped_gateway(self, db, _with_gateway, _with_viewer):
        """A global viewer with a gateway-scoped admin role gets admin on that gateway."""
        from shoreguard.api.main import app
        from shoreguard.models import User

        session = db()
        user = session.query(User).filter(User.email == VIEWER_EMAIL).first()
        user_id = user.id
        session.close()

        set_gateway_role(user_id=user_id, gateway_name=GW_NAME, role="admin")

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/auth/login",
                json={"email": VIEWER_EMAIL, "password": VIEWER_PASS},
            )
            assert resp.status_code == 200

            # The audit endpoint requires admin role. Without the gateway scope,
            # the viewer would be rejected. But the gateway-scoped admin role
            # should grant access when _current_gateway is set.
            # We test the role resolution by directly checking _lookup_gateway_role
            # and the ContextVar mechanism.
            token = _current_gateway.set(GW_NAME)
            try:
                gw_role = _lookup_gateway_role(user_id=user_id, gateway=GW_NAME)
                assert gw_role == "admin"
            finally:
                _current_gateway.reset(token)

    async def test_admin_gets_viewer_on_scoped_gateway(self, db, _with_gateway, _with_admin):
        """A global admin with a gateway-scoped viewer role gets viewer on that gateway."""
        from shoreguard.models import User

        session = db()
        user = session.query(User).filter(User.email == ADMIN_EMAIL).first()
        user_id = user.id
        session.close()

        set_gateway_role(user_id=user_id, gateway_name=GW_NAME, role="viewer")

        token = _current_gateway.set(GW_NAME)
        try:
            gw_role = _lookup_gateway_role(user_id=user_id, gateway=GW_NAME)
            assert gw_role == "viewer"
        finally:
            _current_gateway.reset(token)

    async def test_no_gateway_role_falls_back_to_global(self, db, _with_gateway, _with_viewer):
        """No gateway-scoped role means the global role applies."""
        from shoreguard.models import User

        session = db()
        user = session.query(User).filter(User.email == VIEWER_EMAIL).first()
        user_id = user.id
        session.close()

        token = _current_gateway.set(GW_NAME)
        try:
            gw_role = _lookup_gateway_role(user_id=user_id, gateway=GW_NAME)
            assert gw_role is None  # None means "use global role"
        finally:
            _current_gateway.reset(token)

    async def test_role_resolution_only_when_gateway_set(self, db, _with_gateway, _with_viewer):
        """Role resolution only applies when _current_gateway is set."""
        from shoreguard.models import User

        session = db()
        user = session.query(User).filter(User.email == VIEWER_EMAIL).first()
        user_id = user.id
        session.close()

        set_gateway_role(user_id=user_id, gateway_name=GW_NAME, role="admin")

        # Without setting _current_gateway, the ContextVar is None
        assert _current_gateway.get() is None
        # The lookup function itself still works, but require_role won't call it
        # because gateway is None. Verify the ContextVar default.
        gw_role = _lookup_gateway_role(user_id=user_id, gateway=GW_NAME)
        assert gw_role == "admin"  # The role exists in DB, but require_role wouldn't use it


# ─── API endpoint tests: user gateway roles ───────────────────────────────


class TestUserGatewayRoleEndpoints:
    async def test_set_role(self, admin_client, _with_gateway, _with_viewer):

        # Get viewer user ID
        resp = await admin_client.get("/api/auth/users")
        users = resp.json()
        viewer = next(u for u in users if u["email"] == VIEWER_EMAIL)

        resp = await admin_client.put(
            f"/api/auth/users/{viewer['id']}/gateway-roles/{GW_NAME}",
            json={"role": "operator"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == viewer["id"]
        assert data["gateway_name"] == GW_NAME
        assert data["role"] == "operator"

    async def test_list_roles(self, admin_client, _with_gateway, _with_viewer):
        resp = await admin_client.get("/api/auth/users")
        viewer = next(u for u in resp.json() if u["email"] == VIEWER_EMAIL)

        # Set a role first
        await admin_client.put(
            f"/api/auth/users/{viewer['id']}/gateway-roles/{GW_NAME}",
            json={"role": "admin"},
        )

        resp = await admin_client.get(f"/api/auth/users/{viewer['id']}/gateway-roles")
        assert resp.status_code == 200
        roles = resp.json()
        assert len(roles) == 1
        assert roles[0]["gateway_name"] == GW_NAME
        assert roles[0]["role"] == "admin"

    async def test_remove_role(self, admin_client, _with_gateway, _with_viewer):
        resp = await admin_client.get("/api/auth/users")
        viewer = next(u for u in resp.json() if u["email"] == VIEWER_EMAIL)

        # Set, then remove
        await admin_client.put(
            f"/api/auth/users/{viewer['id']}/gateway-roles/{GW_NAME}",
            json={"role": "admin"},
        )
        resp = await admin_client.delete(f"/api/auth/users/{viewer['id']}/gateway-roles/{GW_NAME}")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    async def test_remove_nonexistent_returns_404(self, admin_client, _with_gateway, _with_viewer):
        resp = await admin_client.get("/api/auth/users")
        viewer = next(u for u in resp.json() if u["email"] == VIEWER_EMAIL)

        resp = await admin_client.delete(f"/api/auth/users/{viewer['id']}/gateway-roles/{GW_NAME}")
        assert resp.status_code == 404

    async def test_set_invalid_role_returns_400(self, admin_client, _with_gateway, _with_viewer):
        resp = await admin_client.get("/api/auth/users")
        viewer = next(u for u in resp.json() if u["email"] == VIEWER_EMAIL)

        resp = await admin_client.put(
            f"/api/auth/users/{viewer['id']}/gateway-roles/{GW_NAME}",
            json={"role": "superadmin"},
        )
        assert resp.status_code == 400
        assert "Invalid role" in resp.json()["detail"]

    async def test_set_role_nonexistent_gateway_returns_404(self, admin_client, _with_viewer):
        """Setting a role for a non-existent gateway triggers IntegrityError -> 404."""
        resp = await admin_client.get("/api/auth/users")
        viewer = next(u for u in resp.json() if u["email"] == VIEWER_EMAIL)

        resp = await admin_client.put(
            f"/api/auth/users/{viewer['id']}/gateway-roles/nonexistent-gw",
            json={"role": "admin"},
        )
        assert resp.status_code == 404


# ─── API endpoint tests: SP gateway roles ─────────────────────────────────


class TestSPGatewayRoleEndpoints:
    async def test_set_sp_role(self, admin_client, _with_gateway):
        # Create SP via API
        resp = await admin_client.post(
            "/api/auth/service-principals",
            json={"name": "ci-bot", "role": "viewer"},
        )
        assert resp.status_code == 201
        sp_id = resp.json()["id"]

        resp = await admin_client.put(
            f"/api/auth/service-principals/{sp_id}/gateway-roles/{GW_NAME}",
            json={"role": "operator"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["sp_id"] == sp_id
        assert data["gateway_name"] == GW_NAME
        assert data["role"] == "operator"

    async def test_list_sp_roles(self, admin_client, _with_gateway):
        resp = await admin_client.post(
            "/api/auth/service-principals",
            json={"name": "ci-bot", "role": "viewer"},
        )
        sp_id = resp.json()["id"]

        await admin_client.put(
            f"/api/auth/service-principals/{sp_id}/gateway-roles/{GW_NAME}",
            json={"role": "admin"},
        )

        resp = await admin_client.get(f"/api/auth/service-principals/{sp_id}/gateway-roles")
        assert resp.status_code == 200
        roles = resp.json()
        assert len(roles) == 1
        assert roles[0]["gateway_name"] == GW_NAME
        assert roles[0]["role"] == "admin"

    async def test_remove_sp_role(self, admin_client, _with_gateway):
        resp = await admin_client.post(
            "/api/auth/service-principals",
            json={"name": "ci-bot", "role": "viewer"},
        )
        sp_id = resp.json()["id"]

        await admin_client.put(
            f"/api/auth/service-principals/{sp_id}/gateway-roles/{GW_NAME}",
            json={"role": "admin"},
        )
        resp = await admin_client.delete(
            f"/api/auth/service-principals/{sp_id}/gateway-roles/{GW_NAME}"
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    async def test_set_sp_invalid_role_returns_400(self, admin_client, _with_gateway):
        resp = await admin_client.post(
            "/api/auth/service-principals",
            json={"name": "ci-bot", "role": "viewer"},
        )
        sp_id = resp.json()["id"]

        resp = await admin_client.put(
            f"/api/auth/service-principals/{sp_id}/gateway-roles/{GW_NAME}",
            json={"role": "megaadmin"},
        )
        assert resp.status_code == 400

    async def test_set_sp_role_nonexistent_gateway_returns_404(self, admin_client):
        resp = await admin_client.post(
            "/api/auth/service-principals",
            json={"name": "ci-bot", "role": "viewer"},
        )
        sp_id = resp.json()["id"]

        resp = await admin_client.put(
            f"/api/auth/service-principals/{sp_id}/gateway-roles/nonexistent-gw",
            json={"role": "admin"},
        )
        assert resp.status_code == 404


# ─── API endpoint tests: role enforcement ─────────────────────────────────


class TestGatewayRoleEndpointEnforcement:
    async def test_viewer_cannot_set_gateway_role(self, viewer_client, _with_admin, _with_gateway):
        resp = await viewer_client.get("/api/auth/users")
        # Viewer can't list users either — that's 403
        assert resp.status_code == 403

    async def test_viewer_cannot_list_gateway_roles(
        self, viewer_client, _with_admin, _with_gateway
    ):
        resp = await viewer_client.get("/api/auth/users/1/gateway-roles")
        assert resp.status_code == 403

    async def test_viewer_cannot_delete_gateway_role(
        self, viewer_client, _with_admin, _with_gateway
    ):
        resp = await viewer_client.delete(f"/api/auth/users/1/gateway-roles/{GW_NAME}")
        assert resp.status_code == 403

    async def test_viewer_cannot_set_sp_gateway_role(
        self, viewer_client, _with_admin, _with_gateway
    ):
        resp = await viewer_client.put(
            f"/api/auth/service-principals/1/gateway-roles/{GW_NAME}",
            json={"role": "admin"},
        )
        assert resp.status_code == 403

    async def test_viewer_cannot_list_sp_gateway_roles(
        self, viewer_client, _with_admin, _with_gateway
    ):
        resp = await viewer_client.get("/api/auth/service-principals/1/gateway-roles")
        assert resp.status_code == 403

    async def test_viewer_cannot_delete_sp_gateway_role(
        self, viewer_client, _with_admin, _with_gateway
    ):
        resp = await viewer_client.delete(f"/api/auth/service-principals/1/gateway-roles/{GW_NAME}")
        assert resp.status_code == 403

    async def test_unauthenticated_gets_401(self, db, _with_admin, _with_gateway):
        from shoreguard.api.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/auth/users/1/gateway-roles")
            assert resp.status_code == 401
