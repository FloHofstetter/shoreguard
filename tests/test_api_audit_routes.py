"""Tests for the audit log API endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import shoreguard.services.audit as audit_mod
from shoreguard.api import auth
from shoreguard.api.auth import create_user
from shoreguard.models import Base

GW = "test"
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
    audit_mod.audit_service = audit_mod.AuditService(factory)
    yield factory
    auth.reset()
    audit_mod.audit_service = None
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


def _seed_audit(count=3):
    assert audit_mod.audit_service is not None
    for i in range(count):
        audit_mod.audit_service.log(
            actor=f"user{i}@test.com",
            actor_role="admin",
            action=f"sandbox.action{i}",
            resource_type="sandbox",
            resource_id=f"sb-{i}",
        )


class TestListAudit:
    async def test_list_returns_entries(self, admin_client):
        _seed_audit(3)
        resp = await admin_client.get("/api/audit")
        assert resp.status_code == 200
        data = resp.json()
        # 3 seeded + 1 login audit entry from admin_client fixture
        assert len(data) >= 3

    async def test_list_without_seed(self, admin_client):
        resp = await admin_client.get("/api/audit")
        assert resp.status_code == 200
        # At least the login audit entry exists
        assert len(resp.json()) >= 1

    async def test_list_with_filters(self, admin_client):
        _seed_audit(5)
        resp = await admin_client.get("/api/audit?actor=user0@test.com")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["actor"] == "user0@test.com"

    async def test_list_pagination(self, admin_client):
        _seed_audit(10)
        resp = await admin_client.get("/api/audit?limit=3&offset=0")
        assert resp.status_code == 200
        assert len(resp.json()) == 3


class TestExportAudit:
    async def test_export_json(self, admin_client):
        _seed_audit(2)
        resp = await admin_client.get("/api/audit/export?format=json")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/json"
        assert "attachment" in resp.headers.get("content-disposition", "")

    async def test_export_csv(self, admin_client):
        _seed_audit(2)
        resp = await admin_client.get("/api/audit/export?format=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        lines = resp.text.strip().split("\n")
        assert len(lines) >= 3  # header + 2 seeded rows + login entry

    async def test_export_csv_without_seed(self, admin_client):
        resp = await admin_client.get("/api/audit/export?format=csv")
        assert resp.status_code == 200
        lines = resp.text.strip().split("\n")
        assert len(lines) >= 1  # at least header (+ login entry)


class TestRoleEnforcement:
    async def test_viewer_cannot_list_audit(self, viewer_client):
        resp = await viewer_client.get("/api/audit")
        assert resp.status_code == 403

    async def test_viewer_cannot_export_audit(self, viewer_client):
        resp = await viewer_client.get("/api/audit/export?format=json")
        assert resp.status_code == 403

    async def test_unauthenticated_gets_401(self, db, _with_admin):
        from shoreguard.api.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/audit")
            assert resp.status_code == 401
