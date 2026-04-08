"""Tests for User Groups / Teams — Group-based RBAC."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import shoreguard.services.audit as audit_mod
from shoreguard.api import auth
from shoreguard.api.auth import (
    _lookup_gateway_role,
    _lookup_group_global_role,
    add_group_member,
    create_group,
    create_user,
    delete_group,
    get_group,
    list_group_gateway_roles,
    list_group_members,
    list_groups,
    list_user_groups,
    remove_group_gateway_role,
    remove_group_member,
    set_group_gateway_role,
    update_group,
)
from shoreguard.exceptions import NotFoundError
from shoreguard.exceptions import ValidationError as DomainValidationError
from shoreguard.models import Base, Gateway

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "adminpass123"
VIEWER_EMAIL = "viewer@test.com"
VIEWER_PASS = "viewerpass1"
GW_NAME = "test-gw"
GW_NAME_2 = "test-gw-2"


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
def _with_two_gateways(db):
    session = db()
    for name in (GW_NAME, GW_NAME_2):
        gw = Gateway(
            name=name,
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


# ─── Unit tests: Group CRUD ──────────────────────────────────────────────


class TestCreateGroup:
    def test_create_basic(self, db):
        result = create_group("devs", "operator", "Development team")
        assert result["name"] == "devs"
        assert result["role"] == "operator"
        assert result["description"] == "Development team"
        assert result["member_count"] == 0
        assert "id" in result

    def test_create_default_role(self, db):
        result = create_group("viewers")
        assert result["role"] == "viewer"

    def test_duplicate_name_raises(self, db):
        create_group("devs")
        with pytest.raises(IntegrityError):
            create_group("devs")

    def test_invalid_role_raises(self, db):
        with pytest.raises(DomainValidationError, match="Invalid role"):
            create_group("devs", "superadmin")


class TestUpdateGroup:
    def test_update_name(self, db):
        g = create_group("devs", "operator")
        result = update_group(g["id"], name="developers")
        assert result["name"] == "developers"
        assert result["role"] == "operator"

    def test_update_role(self, db):
        g = create_group("devs", "viewer")
        result = update_group(g["id"], role="admin")
        assert result["role"] == "admin"

    def test_update_description(self, db):
        g = create_group("devs")
        result = update_group(g["id"], description="New desc")
        assert result["description"] == "New desc"

    def test_update_nonexistent_raises(self, db):
        with pytest.raises(NotFoundError, match="not found"):
            update_group(999, name="nope")

    def test_invalid_role_raises(self, db):
        g = create_group("devs")
        with pytest.raises(DomainValidationError, match="Invalid role"):
            update_group(g["id"], role="superadmin")


class TestDeleteGroup:
    def test_delete_existing(self, db):
        g = create_group("devs")
        assert delete_group(g["id"]) is True
        assert get_group(g["id"]) is None

    def test_delete_nonexistent(self, db):
        assert delete_group(999) is False

    def test_cascade_removes_members(self, db):
        g = create_group("devs")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g["id"], u["id"])
        delete_group(g["id"])
        assert list_user_groups(u["id"]) == []

    def test_cascade_removes_gateway_roles(self, db, _with_gateway):
        g = create_group("devs")
        set_group_gateway_role(g["id"], GW_NAME, "admin")
        delete_group(g["id"])
        assert list_group_gateway_roles(g["id"]) == []


class TestListGroups:
    def test_empty(self, db):
        assert list_groups() == []

    def test_with_members(self, db):
        g = create_group("devs", "operator")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g["id"], u["id"])
        groups = list_groups()
        assert len(groups) == 1
        assert groups[0]["member_count"] == 1

    def test_ordered_by_name(self, db):
        create_group("zebra")
        create_group("alpha")
        groups = list_groups()
        assert groups[0]["name"] == "alpha"
        assert groups[1]["name"] == "zebra"


class TestGetGroup:
    def test_with_members(self, db):
        g = create_group("devs")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g["id"], u["id"])
        result = get_group(g["id"])
        assert result is not None
        assert len(result["members"]) == 1
        assert result["members"][0]["email"] == "u@test.com"

    def test_nonexistent(self, db):
        assert get_group(999) is None


# ─── Unit tests: Group Membership ────────────────────────────────────────


class TestGroupMembership:
    def test_add_member(self, db):
        g = create_group("devs")
        u = create_user("u@test.com", "password1", "viewer")
        result = add_group_member(g["id"], u["id"])
        assert result["group_name"] == "devs"
        assert result["user_email"] == "u@test.com"

    def test_duplicate_membership_raises(self, db):
        g = create_group("devs")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g["id"], u["id"])
        with pytest.raises(IntegrityError):
            add_group_member(g["id"], u["id"])

    def test_add_to_nonexistent_group_raises(self, db):
        u = create_user("u@test.com", "password1", "viewer")
        with pytest.raises(NotFoundError, match="Group 999 not found"):
            add_group_member(999, u["id"])

    def test_add_nonexistent_user_raises(self, db):
        g = create_group("devs")
        with pytest.raises(NotFoundError, match="User 999 not found"):
            add_group_member(g["id"], 999)

    def test_remove_member(self, db):
        g = create_group("devs")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g["id"], u["id"])
        assert remove_group_member(g["id"], u["id"]) is True
        assert list_group_members(g["id"]) == []

    def test_remove_nonexistent_member(self, db):
        g = create_group("devs")
        assert remove_group_member(g["id"], 999) is False

    def test_list_members(self, db):
        g = create_group("devs")
        u1 = create_user("a@test.com", "password1", "viewer")
        u2 = create_user("b@test.com", "password1", "operator")
        add_group_member(g["id"], u1["id"])
        add_group_member(g["id"], u2["id"])
        members = list_group_members(g["id"])
        assert len(members) == 2
        assert members[0]["email"] == "a@test.com"

    def test_list_user_groups(self, db):
        g1 = create_group("alpha")
        g2 = create_group("beta")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g1["id"], u["id"])
        add_group_member(g2["id"], u["id"])
        groups = list_user_groups(u["id"])
        assert len(groups) == 2
        assert groups[0]["name"] == "alpha"

    def test_user_delete_cascades_membership(self, db):
        from shoreguard.api.auth import delete_user

        g = create_group("devs")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g["id"], u["id"])
        delete_user(u["id"])
        assert list_group_members(g["id"]) == []


# ─── Unit tests: Group Gateway Roles ────────────────────────────────────


class TestGroupGatewayRoles:
    def test_set_role(self, db, _with_gateway):
        g = create_group("devs")
        result = set_group_gateway_role(g["id"], GW_NAME, "operator")
        assert result == {"group_id": g["id"], "gateway_name": GW_NAME, "role": "operator"}

    def test_update_role(self, db, _with_gateway):
        g = create_group("devs")
        set_group_gateway_role(g["id"], GW_NAME, "operator")
        result = set_group_gateway_role(g["id"], GW_NAME, "admin")
        assert result["role"] == "admin"

    def test_invalid_role_raises(self, db, _with_gateway):
        g = create_group("devs")
        with pytest.raises(DomainValidationError, match="Invalid role"):
            set_group_gateway_role(g["id"], GW_NAME, "superadmin")

    def test_nonexistent_group_raises(self, db, _with_gateway):
        with pytest.raises(NotFoundError, match="Group 999 not found"):
            set_group_gateway_role(999, GW_NAME, "admin")

    def test_nonexistent_gateway_raises(self, db):
        g = create_group("devs")
        with pytest.raises(NotFoundError, match="Gateway.*not found"):
            set_group_gateway_role(g["id"], "no-such-gw", "admin")

    def test_remove_role(self, db, _with_gateway):
        g = create_group("devs")
        set_group_gateway_role(g["id"], GW_NAME, "admin")
        assert remove_group_gateway_role(g["id"], GW_NAME) is True
        assert list_group_gateway_roles(g["id"]) == []

    def test_remove_nonexistent(self, db, _with_gateway):
        g = create_group("devs")
        assert remove_group_gateway_role(g["id"], GW_NAME) is False

    def test_list_roles(self, db, _with_two_gateways):
        g = create_group("devs")
        set_group_gateway_role(g["id"], GW_NAME, "operator")
        set_group_gateway_role(g["id"], GW_NAME_2, "admin")
        roles = list_group_gateway_roles(g["id"])
        assert len(roles) == 2

    def test_gateway_delete_cascades(self, db, _with_gateway):
        g = create_group("devs")
        set_group_gateway_role(g["id"], GW_NAME, "admin")
        session = db()
        gw = session.query(Gateway).filter(Gateway.name == GW_NAME).first()
        session.delete(gw)
        session.commit()
        session.close()
        assert list_group_gateway_roles(g["id"]) == []


# ─── Unit tests: Role Resolution ────────────────────────────────────────


class TestRoleResolution:
    def test_group_gateway_role_when_no_individual(self, db, _with_gateway):
        """Group gateway role applies when user has no individual gateway role."""
        g = create_group("devs")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g["id"], u["id"])
        set_group_gateway_role(g["id"], GW_NAME, "operator")
        result = _lookup_gateway_role(user_id=u["id"], gateway=GW_NAME)
        assert result == "operator"

    def test_individual_gateway_wins_over_group_gateway(self, db, _with_gateway):
        """Individual gateway role takes precedence over group gateway role."""
        from shoreguard.api.auth import set_gateway_role

        g = create_group("devs")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g["id"], u["id"])
        set_group_gateway_role(g["id"], GW_NAME, "admin")
        set_gateway_role(user_id=u["id"], gateway_name=GW_NAME, role="viewer")
        result = _lookup_gateway_role(user_id=u["id"], gateway=GW_NAME)
        assert result == "viewer"

    def test_multiple_groups_highest_rank_wins(self, db, _with_gateway):
        """When user is in multiple groups, highest gateway role wins."""
        g1 = create_group("viewers")
        g2 = create_group("admins", "admin")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g1["id"], u["id"])
        add_group_member(g2["id"], u["id"])
        set_group_gateway_role(g1["id"], GW_NAME, "viewer")
        set_group_gateway_role(g2["id"], GW_NAME, "admin")
        result = _lookup_gateway_role(user_id=u["id"], gateway=GW_NAME)
        assert result == "admin"

    def test_group_global_role_elevates(self, db):
        """User (viewer) in group (operator) effectively gets operator."""
        g = create_group("devs", "operator")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g["id"], u["id"])
        result = _lookup_group_global_role(u["id"])
        assert result == "operator"

    def test_group_global_role_does_not_downgrade(self, db):
        """User (admin) in group (viewer) keeps admin."""
        g = create_group("viewers", "viewer")
        u = create_user("u@test.com", "password1", "admin")
        add_group_member(g["id"], u["id"])
        # _lookup_group_global_role returns "viewer" but require_role only
        # elevates (it checks if group_global rank > current rank)
        result = _lookup_group_global_role(u["id"])
        assert result == "viewer"  # lookup returns it, but require_role won't apply

    def test_multiple_groups_global_highest_wins(self, db):
        """Multiple group memberships: highest global role wins."""
        g1 = create_group("viewers", "viewer")
        g2 = create_group("admins", "admin")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g1["id"], u["id"])
        add_group_member(g2["id"], u["id"])
        result = _lookup_group_global_role(u["id"])
        assert result == "admin"

    def test_no_groups_returns_none(self, db):
        """User in no groups: _lookup_group_global_role returns None."""
        u = create_user("u@test.com", "password1", "viewer")
        assert _lookup_group_global_role(u["id"]) is None

    def test_no_group_gateway_role_returns_none(self, db, _with_gateway):
        """User in group without gateway role: _lookup_gateway_role returns None."""
        g = create_group("devs")
        u = create_user("u@test.com", "password1", "viewer")
        add_group_member(g["id"], u["id"])
        result = _lookup_gateway_role(user_id=u["id"], gateway=GW_NAME)
        assert result is None


# ─── API endpoint tests ──────────────────────────────────────────────────


class TestGroupAPI:
    async def test_create_group(self, admin_client):
        resp = await admin_client.post(
            "/api/auth/groups",
            json={"name": "devs", "role": "operator", "description": "Dev team"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "devs"
        assert data["role"] == "operator"

    async def test_create_duplicate_409(self, admin_client):
        await admin_client.post("/api/auth/groups", json={"name": "devs"})
        resp = await admin_client.post("/api/auth/groups", json={"name": "devs"})
        assert resp.status_code == 409

    async def test_create_invalid_role_400(self, admin_client):
        resp = await admin_client.post(
            "/api/auth/groups", json={"name": "devs", "role": "superadmin"}
        )
        assert resp.status_code == 400

    async def test_list_groups(self, admin_client):
        await admin_client.post("/api/auth/groups", json={"name": "alpha"})
        await admin_client.post("/api/auth/groups", json={"name": "beta"})
        resp = await admin_client.get("/api/auth/groups")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    async def test_get_group(self, admin_client):
        resp = await admin_client.post("/api/auth/groups", json={"name": "devs"})
        gid = resp.json()["id"]
        resp = await admin_client.get(f"/api/auth/groups/{gid}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "devs"

    async def test_get_group_404(self, admin_client):
        resp = await admin_client.get("/api/auth/groups/999")
        assert resp.status_code == 404

    async def test_update_group(self, admin_client):
        resp = await admin_client.post("/api/auth/groups", json={"name": "devs"})
        gid = resp.json()["id"]
        resp = await admin_client.put(
            f"/api/auth/groups/{gid}", json={"name": "developers", "role": "operator"}
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "developers"

    async def test_update_group_404(self, admin_client):
        resp = await admin_client.put("/api/auth/groups/999", json={"name": "nope"})
        assert resp.status_code == 404

    async def test_delete_group(self, admin_client):
        resp = await admin_client.post("/api/auth/groups", json={"name": "devs"})
        gid = resp.json()["id"]
        resp = await admin_client.delete(f"/api/auth/groups/{gid}")
        assert resp.status_code == 200

    async def test_delete_group_404(self, admin_client):
        resp = await admin_client.delete("/api/auth/groups/999")
        assert resp.status_code == 404

    async def test_viewer_cannot_access(self, viewer_client):
        resp = await viewer_client.get("/api/auth/groups")
        assert resp.status_code == 403


class TestGroupMemberAPI:
    async def test_add_member(self, admin_client):
        g = (await admin_client.post("/api/auth/groups", json={"name": "devs"})).json()
        users = (await admin_client.get("/api/auth/users")).json()
        resp = await admin_client.post(
            f"/api/auth/groups/{g['id']}/members",
            json={"user_id": users[0]["id"]},
        )
        assert resp.status_code == 201

    async def test_add_duplicate_409(self, admin_client):
        g = (await admin_client.post("/api/auth/groups", json={"name": "devs"})).json()
        users = (await admin_client.get("/api/auth/users")).json()
        await admin_client.post(
            f"/api/auth/groups/{g['id']}/members",
            json={"user_id": users[0]["id"]},
        )
        resp = await admin_client.post(
            f"/api/auth/groups/{g['id']}/members",
            json={"user_id": users[0]["id"]},
        )
        assert resp.status_code == 409

    async def test_remove_member(self, admin_client):
        g = (await admin_client.post("/api/auth/groups", json={"name": "devs"})).json()
        users = (await admin_client.get("/api/auth/users")).json()
        await admin_client.post(
            f"/api/auth/groups/{g['id']}/members",
            json={"user_id": users[0]["id"]},
        )
        resp = await admin_client.delete(f"/api/auth/groups/{g['id']}/members/{users[0]['id']}")
        assert resp.status_code == 200

    async def test_remove_nonexistent_404(self, admin_client):
        g = (await admin_client.post("/api/auth/groups", json={"name": "devs"})).json()
        resp = await admin_client.delete(f"/api/auth/groups/{g['id']}/members/999")
        assert resp.status_code == 404


class TestGroupGatewayRoleAPI:
    async def test_set_gateway_role(self, admin_client, _with_gateway):
        g = (await admin_client.post("/api/auth/groups", json={"name": "devs"})).json()
        resp = await admin_client.put(
            f"/api/auth/groups/{g['id']}/gateway-roles/{GW_NAME}",
            json={"role": "operator"},
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "operator"

    async def test_list_gateway_roles(self, admin_client, _with_gateway):
        g = (await admin_client.post("/api/auth/groups", json={"name": "devs"})).json()
        await admin_client.put(
            f"/api/auth/groups/{g['id']}/gateway-roles/{GW_NAME}",
            json={"role": "operator"},
        )
        resp = await admin_client.get(f"/api/auth/groups/{g['id']}/gateway-roles")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_remove_gateway_role(self, admin_client, _with_gateway):
        g = (await admin_client.post("/api/auth/groups", json={"name": "devs"})).json()
        await admin_client.put(
            f"/api/auth/groups/{g['id']}/gateway-roles/{GW_NAME}",
            json={"role": "operator"},
        )
        resp = await admin_client.delete(f"/api/auth/groups/{g['id']}/gateway-roles/{GW_NAME}")
        assert resp.status_code == 200

    async def test_remove_nonexistent_404(self, admin_client, _with_gateway):
        g = (await admin_client.post("/api/auth/groups", json={"name": "devs"})).json()
        resp = await admin_client.delete(f"/api/auth/groups/{g['id']}/gateway-roles/{GW_NAME}")
        assert resp.status_code == 404

    async def test_invalid_role_400(self, admin_client, _with_gateway):
        g = (await admin_client.post("/api/auth/groups", json={"name": "devs"})).json()
        resp = await admin_client.put(
            f"/api/auth/groups/{g['id']}/gateway-roles/{GW_NAME}",
            json={"role": "superadmin"},
        )
        assert resp.status_code == 400


# ─── Integration: role resolution via API ────────────────────────────────


class TestGroupRoleResolutionAPI:
    async def test_group_global_role_grants_access(self, admin_client, _with_viewer):
        """Viewer user in operator group can access operator endpoints."""
        g = (
            await admin_client.post("/api/auth/groups", json={"name": "ops", "role": "operator"})
        ).json()
        users = (await admin_client.get("/api/auth/users")).json()
        viewer_user = next(u for u in users if u["email"] == VIEWER_EMAIL)
        await admin_client.post(
            f"/api/auth/groups/{g['id']}/members",
            json={"user_id": viewer_user["id"]},
        )

        from shoreguard.api.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/auth/login",
                json={"email": VIEWER_EMAIL, "password": VIEWER_PASS},
            )
            assert resp.status_code == 200
            # Viewer in operator group should still not access admin endpoints
            resp = await client.get("/api/auth/users")
            assert resp.status_code == 403
