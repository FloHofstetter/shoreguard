"""End-to-end tests for auth flows: invite, registration, single-user mode, no-auth."""

from __future__ import annotations

import datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.api import auth
from shoreguard.api.auth import create_user
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
    from unittest.mock import MagicMock

    from shoreguard.client import ShoreGuardClient

    client = MagicMock(spec=ShoreGuardClient)
    client.sandboxes = MagicMock()
    client.policies = MagicMock()
    client.providers = MagicMock()
    client.approvals = MagicMock()
    return client


# ─── Invite Flow ────────────────────────────────────────────────────────────


class TestInviteFlow:
    async def test_full_invite_flow(self, db, mock_client):
        """Admin creates invite -> user accepts -> user can login."""
        from shoreguard.api.deps import get_client
        from shoreguard.api.main import app

        create_user("admin@test.com", "adminpass", "admin")
        app.dependency_overrides[get_client] = lambda: mock_client
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                # 1. Login as admin
                resp = await c.post(
                    "/api/auth/login",
                    json={"email": "admin@test.com", "password": "adminpass"},
                )
                assert resp.status_code == 200

                # 2. Create invite
                resp = await c.post(
                    "/api/auth/users",
                    json={"email": "invited@test.com", "role": "operator"},
                )
                assert resp.status_code == 201
                invite_token = resp.json()["invite_token"]
                assert invite_token

            # 3. Accept invite (new client, no cookies)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    "/api/auth/accept-invite",
                    json={"token": invite_token, "password": "newpass12"},
                )
                assert resp.status_code == 200
                assert resp.json()["role"] == "operator"
                assert "sg_session" in resp.cookies

            # 4. Login with new credentials
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    "/api/auth/login",
                    json={"email": "invited@test.com", "password": "newpass12"},
                )
                assert resp.status_code == 200
                assert resp.json()["role"] == "operator"
        finally:
            app.dependency_overrides.clear()

    async def test_invite_token_single_use(self, db):
        """After accepting an invite, the same token cannot be reused."""
        from shoreguard.api.main import app

        create_user("admin@test.com", "adminpass", "admin")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/login",
                json={"email": "admin@test.com", "password": "adminpass"},
            )
            assert resp.status_code == 200

            resp = await c.post(
                "/api/auth/users",
                json={"email": "once@test.com", "role": "viewer"},
            )
            token = resp.json()["invite_token"]

        # Accept
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/accept-invite",
                json={"token": token, "password": "password123"},
            )
            assert resp.status_code == 200

            # Reuse same token
            resp = await c.post(
                "/api/auth/accept-invite",
                json={"token": token, "password": "another1"},
            )
            assert resp.status_code == 400

    async def test_invite_invalid_token(self, db):
        """Bogus invite token should be rejected."""
        from shoreguard.api.main import app

        create_user("admin@test.com", "adminpass", "admin")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/accept-invite",
                json={"token": "bogus-token-12345678", "password": "password123"},
            )
            assert resp.status_code == 400

    async def test_invite_short_password(self, db):
        """Password under 8 chars should be rejected on invite accept."""
        from shoreguard.api.main import app

        create_user("admin@test.com", "adminpass", "admin")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/accept-invite",
                json={"token": "any-token", "password": "short"},
            )
            assert resp.status_code == 400
            assert "8 characters" in resp.json()["detail"]


# ─── Single-User Mode ──────────────────────────────────────────────────────


class TestSingleUserMode:
    async def test_no_users_blocks_non_setup_endpoints(self, db, mock_client):
        """Empty DB should block API access except setup-related paths."""
        from shoreguard.api.deps import get_client
        from shoreguard.api.main import app

        app.dependency_overrides[get_client] = lambda: mock_client
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                # Non-setup endpoints should be blocked
                resp = await c.get("/api/gateway/list")
                assert resp.status_code == 401

                # Setup-related endpoints should still work
                resp = await c.get("/api/auth/check")
                assert resp.status_code == 200
        finally:
            app.dependency_overrides.clear()

    async def test_setup_creates_first_admin(self, db):
        """Setup wizard should create admin and return session cookie."""
        from shoreguard.api.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/setup",
                json={"email": "first@admin.com", "password": "secret12"},
            )
            assert resp.status_code == 200
            assert resp.json()["role"] == "admin"
            assert "sg_session" in resp.cookies

    async def test_setup_blocked_after_first_user(self, db):
        """Setup should fail when users already exist."""
        from shoreguard.api.main import app

        create_user("existing@test.com", "password123", "admin")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/setup",
                json={"email": "new@admin.com", "password": "password123"},
            )
            assert resp.status_code == 400
            assert "already complete" in resp.json()["detail"]

    async def test_setup_rejects_short_password(self, db):
        """Setup should reject passwords under 8 characters."""
        from shoreguard.api.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/setup",
                json={"email": "admin@test.com", "password": "short"},
            )
            assert resp.status_code == 400
            assert "8 characters" in resp.json()["detail"]

    async def test_no_auth_mode(self, db, mock_client, monkeypatch):
        """SHOREGUARD_NO_AUTH=1 should grant admin access without login."""
        from shoreguard.api import auth as auth_mod
        from shoreguard.api.deps import get_client
        from shoreguard.api.main import app

        # Create a user so setup is "complete" — normally would require login
        create_user("admin@test.com", "adminpass", "admin")

        # Enable no-auth mode
        monkeypatch.setattr(auth_mod, "_no_auth", True)

        app.dependency_overrides[get_client] = lambda: mock_client
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                # Should succeed without any credentials
                resp = await c.get("/api/gateway/list")
                assert resp.status_code != 401
                assert resp.status_code != 403
        finally:
            app.dependency_overrides.clear()


# ─── Self-Registration ──────────────────────────────────────────────────────


class TestRegistration:
    async def test_register_when_enabled(self, db, monkeypatch):
        """Self-registration should create a viewer account when enabled."""
        from shoreguard.api.main import app

        create_user("admin@test.com", "adminpass", "admin")
        monkeypatch.setenv("SHOREGUARD_ALLOW_REGISTRATION", "1")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/register",
                json={"email": "new@user.com", "password": "password123"},
            )
            assert resp.status_code == 201
            assert resp.json()["role"] == "viewer"
            assert "sg_session" in resp.cookies

    async def test_register_when_disabled(self, db):
        """Registration should return 403 when disabled."""
        from shoreguard.api.main import app

        create_user("admin@test.com", "adminpass", "admin")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/register",
                json={"email": "new@user.com", "password": "password123"},
            )
            assert resp.status_code == 403

    async def test_register_duplicate_email(self, db, monkeypatch):
        """Duplicate email should return 409."""
        from shoreguard.api.main import app

        create_user("admin@test.com", "adminpass", "admin")
        monkeypatch.setenv("SHOREGUARD_ALLOW_REGISTRATION", "1")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/register",
                json={"email": "admin@test.com", "password": "password123"},
            )
            assert resp.status_code == 409

    async def test_register_short_password(self, db, monkeypatch):
        """Short password should be rejected."""
        from shoreguard.api.main import app

        create_user("admin@test.com", "adminpass", "admin")
        monkeypatch.setenv("SHOREGUARD_ALLOW_REGISTRATION", "1")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/register",
                json={"email": "new@user.com", "password": "short"},
            )
            assert resp.status_code == 400
            assert "8 characters" in resp.json()["detail"]


# ─── Password boundary tests ─────────────────────────────────────────────────


class TestPasswordMaxLength:
    """Password over 128 characters should be rejected on all endpoints."""

    async def test_setup_rejects_long_password(self, db):
        from shoreguard.api.main import app

        long_pw = "a" * 129
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/setup", json={"email": "admin@test.com", "password": long_pw}
            )
            assert resp.status_code == 400
            assert "128" in resp.json()["detail"]

    async def test_login_rejects_long_password(self, db):
        from shoreguard.api.main import app

        create_user("admin@test.com", "adminpass", "admin")
        long_pw = "a" * 129
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/login", json={"email": "admin@test.com", "password": long_pw}
            )
            assert resp.status_code == 400
            assert "128" in resp.json()["detail"]

    async def test_accept_invite_rejects_long_password(self, db):
        from shoreguard.api.main import app

        create_user("admin@test.com", "adminpass", "admin")
        long_pw = "a" * 129
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/accept-invite", json={"token": "any-token", "password": long_pw}
            )
            assert resp.status_code == 400
            assert "128" in resp.json()["detail"]

    async def test_register_rejects_long_password(self, db, monkeypatch):
        from shoreguard.api.main import app

        create_user("admin@test.com", "adminpass", "admin")
        monkeypatch.setenv("SHOREGUARD_ALLOW_REGISTRATION", "1")
        long_pw = "a" * 129
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/register", json={"email": "new@user.com", "password": long_pw}
            )
            assert resp.status_code == 400
            assert "128" in resp.json()["detail"]


# ─── Invite token expiration ────────────────────────────────────────────────


class TestInviteTokenExpiry:
    async def test_expired_invite_token_rejected(self, db):
        """Invite tokens older than 7 days should be rejected."""
        from shoreguard.api.main import app

        create_user("admin@test.com", "adminpass", "admin")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Login as admin
            resp = await c.post(
                "/api/auth/login",
                json={"email": "admin@test.com", "password": "adminpass"},
            )
            assert resp.status_code == 200

            # Create invite
            resp = await c.post(
                "/api/auth/users", json={"email": "invited@test.com", "role": "viewer"}
            )
            assert resp.status_code == 201
            invite_token = resp.json()["invite_token"]

        # Manipulate the created_at timestamp to be 8 days ago
        from shoreguard.models import User

        session = db()
        try:
            user = session.query(User).filter(User.email == "invited@test.com").first()
            old_time = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=8)
            user.created_at = old_time.isoformat()
            session.commit()
        finally:
            session.close()

        # Try to accept — should be rejected as expired
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/accept-invite",
                json={"token": invite_token, "password": "password123"},
            )
            assert resp.status_code == 400
            detail = resp.json()["detail"].lower()
            assert "expired" in detail or "invalid" in detail


# ─── Invalid role parameter ─────────────────────────────────────────────────


class TestInvalidRole:
    async def test_create_user_with_invalid_role(self, db, mock_client):
        from shoreguard.api.deps import get_client
        from shoreguard.api.main import app

        create_user("admin@test.com", "adminpass", "admin")
        app.dependency_overrides[get_client] = lambda: mock_client
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    "/api/auth/login",
                    json={"email": "admin@test.com", "password": "adminpass"},
                )
                assert resp.status_code == 200

                resp = await c.post(
                    "/api/auth/users",
                    json={"email": "bad@test.com", "role": "superadmin"},
                )
                assert resp.status_code == 400
                assert "Invalid role" in resp.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    async def test_create_sp_with_invalid_role(self, db, mock_client):
        from shoreguard.api.deps import get_client
        from shoreguard.api.main import app

        create_user("admin@test.com", "adminpass", "admin")
        app.dependency_overrides[get_client] = lambda: mock_client
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    "/api/auth/login",
                    json={"email": "admin@test.com", "password": "adminpass"},
                )
                assert resp.status_code == 200

                resp = await c.post(
                    "/api/auth/service-principals",
                    json={"name": "bad-sp", "role": "superadmin"},
                )
                assert resp.status_code == 400
                assert "Invalid role" in resp.json()["detail"]
        finally:
            app.dependency_overrides.clear()


# ─── Inactive user behaviour ────────────────────────────────────────────────


class TestInactiveUser:
    async def test_inactive_user_cannot_login(self, db):
        """Deactivated users should not be able to authenticate."""
        from shoreguard.api.auth import authenticate_user
        from shoreguard.models import User

        create_user("active@test.com", "password123", "viewer")

        # Deactivate the user directly in DB
        session = db()
        try:
            user = session.query(User).filter(User.email == "active@test.com").first()
            user.is_active = False
            session.commit()
        finally:
            session.close()

        assert authenticate_user("active@test.com", "password123") is None

    async def test_inactive_user_session_rejected(self, db, mock_client):
        """Existing session of a deactivated user should be rejected."""
        from shoreguard.api.deps import get_client
        from shoreguard.api.main import app
        from shoreguard.models import User

        create_user("admin@test.com", "adminpass", "admin")
        create_user("target@test.com", "password123", "viewer")
        app.dependency_overrides[get_client] = lambda: mock_client
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    "/api/auth/login",
                    json={"email": "target@test.com", "password": "password123"},
                )
                assert resp.status_code == 200

                # Deactivate user directly
                session = db()
                try:
                    user = session.query(User).filter(User.email == "target@test.com").first()
                    user.is_active = False
                    session.commit()
                finally:
                    session.close()

                # Session should now be rejected
                resp = await c.get("/api/gateway/list")
                assert resp.status_code == 401
        finally:
            app.dependency_overrides.clear()


# ─── Duplicate email on admin invite ─────────────────────────────────────────


class TestDuplicateInvite:
    async def test_duplicate_email_invite_returns_409(self, db, mock_client):
        from shoreguard.api.deps import get_client
        from shoreguard.api.main import app

        create_user("admin@test.com", "adminpass", "admin")
        app.dependency_overrides[get_client] = lambda: mock_client
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    "/api/auth/login",
                    json={"email": "admin@test.com", "password": "adminpass"},
                )
                assert resp.status_code == 200

                # First invite
                resp = await c.post(
                    "/api/auth/users", json={"email": "dup@test.com", "role": "viewer"}
                )
                assert resp.status_code == 201

                # Duplicate invite
                resp = await c.post(
                    "/api/auth/users", json={"email": "dup@test.com", "role": "operator"}
                )
                assert resp.status_code == 409
                assert "already exists" in resp.json()["detail"]
        finally:
            app.dependency_overrides.clear()
