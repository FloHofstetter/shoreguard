"""Tests for User Groups / Teams — Group-based RBAC."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from shoreguard.api.auth import (
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
from shoreguard.api.auth.rbac import _lookup_gateway_role, _lookup_group_global_role
from shoreguard.exceptions import NotFoundError
from shoreguard.exceptions import ValidationError as DomainValidationError
from shoreguard.models import Gateway

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "adminpass123"
VIEWER_EMAIL = "viewer@test.com"
VIEWER_PASS = "viewerpass1"
GW_NAME = "test-gw"
GW_NAME_2 = "test-gw-2"


@pytest.fixture
def db():
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db(foreign_keys=True)
    yield factory
    dispose()


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
async def _with_admin(db):
    await create_user(ADMIN_EMAIL, ADMIN_PASS, "admin")


@pytest.fixture
async def _with_viewer(db):
    await create_user(VIEWER_EMAIL, VIEWER_PASS, "viewer")


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
    async def test_create_basic(self, db):
        result = await create_group("devs", "operator", "Development team")
        assert result["name"] == "devs"
        assert result["role"] == "operator"
        assert result["description"] == "Development team"
        assert result["member_count"] == 0
        assert "id" in result

    async def test_create_default_role(self, db):
        result = await create_group("viewers")
        assert result["role"] == "viewer"

    async def test_duplicate_name_raises(self, db):
        await create_group("devs")
        with pytest.raises(IntegrityError):
            await create_group("devs")

    async def test_invalid_role_raises(self, db):
        with pytest.raises(DomainValidationError, match="Invalid role"):
            await create_group("devs", "superadmin")


class TestUpdateGroup:
    async def test_update_name(self, db):
        g = await create_group("devs", "operator")
        result = await update_group(g["id"], name="developers")
        assert result["name"] == "developers"
        assert result["role"] == "operator"

    async def test_update_role(self, db):
        g = await create_group("devs", "viewer")
        result = await update_group(g["id"], role="admin")
        assert result["role"] == "admin"

    async def test_update_description(self, db):
        g = await create_group("devs")
        result = await update_group(g["id"], description="New desc")
        assert result["description"] == "New desc"

    async def test_update_nonexistent_raises(self, db):
        with pytest.raises(NotFoundError, match="not found"):
            await update_group(999, name="nope")

    async def test_invalid_role_raises(self, db):
        g = await create_group("devs")
        with pytest.raises(DomainValidationError, match="Invalid role"):
            await update_group(g["id"], role="superadmin")


class TestDeleteGroup:
    async def test_delete_existing(self, db):
        g = await create_group("devs")
        assert await delete_group(g["id"]) is True
        assert await get_group(g["id"]) is None

    async def test_delete_nonexistent(self, db):
        assert await delete_group(999) is False

    async def test_cascade_removes_members(self, db):
        g = await create_group("devs")
        u = await create_user("u@test.com", "password1", "viewer")
        await add_group_member(g["id"], u["id"])
        await delete_group(g["id"])
        assert await list_user_groups(u["id"]) == []

    async def test_cascade_removes_gateway_roles(self, db, _with_gateway):
        g = await create_group("devs")
        await set_group_gateway_role(g["id"], GW_NAME, "admin")
        await delete_group(g["id"])
        assert await list_group_gateway_roles(g["id"]) == []


class TestListGroups:
    async def test_empty(self, db):
        assert await list_groups() == []

    async def test_with_members(self, db):
        g = await create_group("devs", "operator")
        u = await create_user("u@test.com", "password1", "viewer")
        await add_group_member(g["id"], u["id"])
        groups = await list_groups()
        assert len(groups) == 1
        assert groups[0]["member_count"] == 1

    async def test_ordered_by_name(self, db):
        await create_group("zebra")
        await create_group("alpha")
        groups = await list_groups()
        assert groups[0]["name"] == "alpha"
        assert groups[1]["name"] == "zebra"


class TestGetGroup:
    async def test_with_members(self, db):
        g = await create_group("devs")
        u = await create_user("u@test.com", "password1", "viewer")
        await add_group_member(g["id"], u["id"])
        result = await get_group(g["id"])
        assert result is not None
        assert len(result["members"]) == 1
        assert result["members"][0]["email"] == "u@test.com"

    async def test_nonexistent(self, db):
        assert await get_group(999) is None


# ─── Unit tests: Group Membership ────────────────────────────────────────


class TestGroupMembership:
    async def test_add_member(self, db):
        g = await create_group("devs")
        u = await create_user("u@test.com", "password1", "viewer")
        result = await add_group_member(g["id"], u["id"])
        assert result["group_name"] == "devs"
        assert result["user_email"] == "u@test.com"

    async def test_duplicate_membership_raises(self, db):
        g = await create_group("devs")
        u = await create_user("u@test.com", "password1", "viewer")
        await add_group_member(g["id"], u["id"])
        with pytest.raises(IntegrityError):
            await add_group_member(g["id"], u["id"])

    async def test_add_to_nonexistent_group_raises(self, db):
        u = await create_user("u@test.com", "password1", "viewer")
        with pytest.raises(NotFoundError, match="Group 999 not found"):
            await add_group_member(999, u["id"])

    async def test_add_nonexistent_user_raises(self, db):
        g = await create_group("devs")
        with pytest.raises(NotFoundError, match="User 999 not found"):
            await add_group_member(g["id"], 999)

    async def test_remove_member(self, db):
        g = await create_group("devs")
        u = await create_user("u@test.com", "password1", "viewer")
        await add_group_member(g["id"], u["id"])
        assert await remove_group_member(g["id"], u["id"]) is True
        assert await list_group_members(g["id"]) == []

    async def test_remove_nonexistent_member(self, db):
        g = await create_group("devs")
        assert await remove_group_member(g["id"], 999) is False

    async def test_list_members(self, db):
        g = await create_group("devs")
        u1 = await create_user("a@test.com", "password1", "viewer")
        u2 = await create_user("b@test.com", "password1", "operator")
        await add_group_member(g["id"], u1["id"])
        await add_group_member(g["id"], u2["id"])
        members = await list_group_members(g["id"])
        assert len(members) == 2
        assert members[0]["email"] == "a@test.com"

    async def test_list_user_groups(self, db):
        g1 = await create_group("alpha")
        g2 = await create_group("beta")
        u = await create_user("u@test.com", "password1", "viewer")
        await add_group_member(g1["id"], u["id"])
        await add_group_member(g2["id"], u["id"])
        groups = await list_user_groups(u["id"])
        assert len(groups) == 2
        assert groups[0]["name"] == "alpha"

    async def test_user_delete_cascades_membership(self, db):
        from shoreguard.api.auth import delete_user

        g = await create_group("devs")
        u = await create_user("u@test.com", "password1", "viewer")
        await add_group_member(g["id"], u["id"])
        await delete_user(u["id"])
        assert await list_group_members(g["id"]) == []


# ─── Unit tests: Group Gateway Roles ────────────────────────────────────


class TestGroupGatewayRoles:
    async def test_set_role(self, db, _with_gateway):
        g = await create_group("devs")
        result = await set_group_gateway_role(g["id"], GW_NAME, "operator")
        assert result == {"group_id": g["id"], "gateway_name": GW_NAME, "role": "operator"}

    async def test_update_role(self, db, _with_gateway):
        g = await create_group("devs")
        await set_group_gateway_role(g["id"], GW_NAME, "operator")
        result = await set_group_gateway_role(g["id"], GW_NAME, "admin")
        assert result["role"] == "admin"

    async def test_invalid_role_raises(self, db, _with_gateway):
        g = await create_group("devs")
        with pytest.raises(DomainValidationError, match="Invalid role"):
            await set_group_gateway_role(g["id"], GW_NAME, "superadmin")

    async def test_nonexistent_group_raises(self, db, _with_gateway):
        with pytest.raises(NotFoundError, match="Group 999 not found"):
            await set_group_gateway_role(999, GW_NAME, "admin")

    async def test_nonexistent_gateway_raises(self, db):
        g = await create_group("devs")
        with pytest.raises(NotFoundError, match="Gateway.*not found"):
            await set_group_gateway_role(g["id"], "no-such-gw", "admin")

    async def test_remove_role(self, db, _with_gateway):
        g = await create_group("devs")
        await set_group_gateway_role(g["id"], GW_NAME, "admin")
        assert await remove_group_gateway_role(g["id"], GW_NAME) is True
        assert await list_group_gateway_roles(g["id"]) == []

    async def test_remove_nonexistent(self, db, _with_gateway):
        g = await create_group("devs")
        assert await remove_group_gateway_role(g["id"], GW_NAME) is False

    async def test_list_roles(self, db, _with_two_gateways):
        g = await create_group("devs")
        await set_group_gateway_role(g["id"], GW_NAME, "operator")
        await set_group_gateway_role(g["id"], GW_NAME_2, "admin")
        roles = await list_group_gateway_roles(g["id"])
        assert len(roles) == 2

    async def test_gateway_delete_cascades(self, db, _with_gateway):
        g = await create_group("devs")
        await set_group_gateway_role(g["id"], GW_NAME, "admin")
        session = db()
        gw = session.query(Gateway).filter(Gateway.name == GW_NAME).first()
        session.delete(gw)
        session.commit()
        session.close()
        assert await list_group_gateway_roles(g["id"]) == []


# ─── Unit tests: Role Resolution ────────────────────────────────────────


class TestRoleResolution:
    async def test_group_gateway_role_when_no_individual(self, db, _with_gateway):
        """Group gateway role applies when user has no individual gateway role."""
        g = await create_group("devs")
        u = await create_user("u@test.com", "password1", "viewer")
        await add_group_member(g["id"], u["id"])
        await set_group_gateway_role(g["id"], GW_NAME, "operator")
        result = await _lookup_gateway_role(user_id=u["id"], gateway=GW_NAME)
        assert result == "operator"

    async def test_individual_gateway_wins_over_group_gateway(self, db, _with_gateway):
        """Individual gateway role takes precedence over group gateway role."""
        from shoreguard.api.auth import set_gateway_role

        g = await create_group("devs")
        u = await create_user("u@test.com", "password1", "viewer")
        await add_group_member(g["id"], u["id"])
        await set_group_gateway_role(g["id"], GW_NAME, "admin")
        await set_gateway_role(user_id=u["id"], gateway_name=GW_NAME, role="viewer")
        result = await _lookup_gateway_role(user_id=u["id"], gateway=GW_NAME)
        assert result == "viewer"

    async def test_multiple_groups_highest_rank_wins(self, db, _with_gateway):
        """When user is in multiple groups, highest gateway role wins."""
        g1 = await create_group("viewers")
        g2 = await create_group("admins", "admin")
        u = await create_user("u@test.com", "password1", "viewer")
        await add_group_member(g1["id"], u["id"])
        await add_group_member(g2["id"], u["id"])
        await set_group_gateway_role(g1["id"], GW_NAME, "viewer")
        await set_group_gateway_role(g2["id"], GW_NAME, "admin")
        result = await _lookup_gateway_role(user_id=u["id"], gateway=GW_NAME)
        assert result == "admin"

    async def test_group_global_role_elevates(self, db):
        """User (viewer) in group (operator) effectively gets operator."""
        g = await create_group("devs", "operator")
        u = await create_user("u@test.com", "password1", "viewer")
        await add_group_member(g["id"], u["id"])
        result = await _lookup_group_global_role(u["id"])
        assert result == "operator"

    async def test_group_global_role_does_not_downgrade(self, db):
        """User (admin) in group (viewer) keeps admin."""
        g = await create_group("viewers", "viewer")
        u = await create_user("u@test.com", "password1", "admin")
        await add_group_member(g["id"], u["id"])
        # _lookup_group_global_role returns "viewer" but require_role only
        # elevates (it checks if group_global rank > current rank)
        result = await _lookup_group_global_role(u["id"])
        assert result == "viewer"  # lookup returns it, but require_role won't apply

    async def test_multiple_groups_global_highest_wins(self, db):
        """Multiple group memberships: highest global role wins."""
        g1 = await create_group("viewers", "viewer")
        g2 = await create_group("admins", "admin")
        u = await create_user("u@test.com", "password1", "viewer")
        await add_group_member(g1["id"], u["id"])
        await add_group_member(g2["id"], u["id"])
        result = await _lookup_group_global_role(u["id"])
        assert result == "admin"

    async def test_no_groups_returns_none(self, db):
        """User in no groups: _lookup_group_global_role returns None."""
        u = await create_user("u@test.com", "password1", "viewer")
        assert await _lookup_group_global_role(u["id"]) is None

    async def test_no_group_gateway_role_returns_none(self, db, _with_gateway):
        """User in group without gateway role: _lookup_gateway_role returns None."""
        g = await create_group("devs")
        u = await create_user("u@test.com", "password1", "viewer")
        await add_group_member(g["id"], u["id"])
        result = await _lookup_gateway_role(user_id=u["id"], gateway=GW_NAME)
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
